"""QML bridge `insights`: aircraft database card, statistics charts and receiver coverage.

Registered by ui/bridges.py. Pure logic lives in airalert/insights.py and airalert/aircraftdb.py.
"""
import logging
import sqlite3
import threading
import time
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QObject, QTimer, QUrl, Property, Signal, Slot

from .. import __version__, aircraftdb, insights
from ..core.models import UNIT_KM

log = logging.getLogger(__name__)

RANGE_LABELS = {'24h': 'Last 24 hours', '7d': 'Last 7 days', '30d': 'Last 30 days'}
MAX_DOWNLOAD = 400 * 1024 ** 2


def _fmt_date(text):
    try:
        return datetime.fromisoformat(text).strftime('%b %d, %Y').replace(' 0', ' ')
    except (TypeError, ValueError):
        return text or ''


class Insights(QObject):
    statsChanged = Signal()
    liveChanged = Signal()
    aircraftDbChanged = Signal()
    updateChanged = Signal()
    _buildProgress = Signal(float)
    _buildDone = Signal(str, int)

    def __init__(self, controller):
        super().__init__(controller)
        self.controller = controller
        self.folder = Path(controller.config.folder)
        self.url = aircraftdb.OPENSKY_URL
        self.tracker = insights.RateTracker()
        self._range = '24h'
        self._include_sim = False
        self._active = False
        self._stats = self._empty_stats()
        self._live = dict(points=[], start=0, end=1, bin=5, current=0, peak=None, average=None, running=False,
                          simulated=False)
        self._db = {}
        self._last_catch_up = 0.0
        self._last_coverage = 0.0
        self._last_health = 0.0
        self._health = dict(status='unknown', current=None, typical=None, text='')
        self._records = {}
        self._backfilling = False
        # OpenSky update
        self._nam = None
        self._reply = None
        self._file = None
        self._worker = None
        self._cancel = threading.Event()
        self._state, self._progress, self._message = 'idle', 0.0, ''
        self._buildProgress.connect(self._on_build_progress)
        self._buildDone.connect(self._on_build_done)
        self._backfill_timer = QTimer(self)
        self._backfill_timer.setSingleShot(True)
        self._backfill_timer.setInterval(15)
        self._backfill_timer.timeout.connect(self._backfill_step)
        aircraftdb.ensure_local(self.folder)
        self._refresh_db_info()
        controller.refresh_hooks.append(self._on_refresh)
        app = QCoreApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self.shutdown)
        QTimer.singleShot(0, self, self._start_catch_up)

    # ------------------------------------------------------------------ helpers
    @property
    def station(self):
        return self.controller.station

    @property
    def conn(self):
        return self.station.db.conn

    def _units(self):
        units = self.controller.config['units']
        return units, UNIT_KM[units]

    def _home(self):
        home = self.controller.config['home']
        return (float(home[0]), float(home[1])) if home else None

    # --------------------------------------------------------------- properties
    def _p(ptype, getter, notify):
        return Property(ptype, getter, notify=notify)

    stats = _p('QVariantMap', lambda self: self._stats, statsChanged)
    timeRange = _p(str, lambda self: self._range, statsChanged)
    includeSimulation = _p(bool, lambda self: self._include_sim, statsChanged)
    live = _p('QVariantMap', lambda self: self._live, liveChanged)
    aircraftDb = _p('QVariantMap', lambda self: self._db, aircraftDbChanged)
    health = _p('QVariantMap', lambda self: self._health, liveChanged)
    records = _p('QVariantList', lambda self: self._records_rows(), statsChanged)
    updateState = _p(str, lambda self: self._state, updateChanged)
    updateProgress = _p(float, lambda self: self._progress, updateChanged)
    updateText = _p(str, lambda self: self._message, updateChanged)
    updateBusy = _p(bool, lambda self: self._state in ('downloading', 'building'), updateChanged)
    del _p

    # ------------------------------------------------------------ refresh hook
    def _on_refresh(self, now):
        st = self.station
        targets = list(st.store.targets.values())
        aircraft = sum(t.kind == 'aircraft' for t in targets)
        row = self.tracker.add(now, st.rate, st.running, bool(st.sim), aircraft, len(targets) - aircraft)
        if row:
            insights.store_minute(self.conn, row)
        if self._active:
            self._update_live(now)
        if now - self._last_catch_up >= 10 and not self._backfilling:
            self._last_catch_up = now
            self._start_catch_up()
        if now - self._last_coverage >= 60:
            self.publish_coverage(now)
        if now - self._last_health >= 60:
            self._last_health = now
            self.check_health(now)

    # ------------------------------------------------------------------ health
    def check_health(self, now=None):
        """Compare the recent message rate with what is normal for this hour; warn once when it drops."""
        now = time.time() if now is None else now
        st = self.station
        result = dict(status='unknown', current=None, typical=None)
        if st.running and not st.sim:
            try:
                result = insights.reception_health(self.conn, now)
            except sqlite3.Error:
                log.exception('Reception health check failed')
        text = ''
        if result['status'] == 'low':
            text = (f"Reception is low: {result['current']:g} messages a second in the last 15 minutes, against a "
                    f"usual {result['typical']:g} at this hour. Check the antenna, its cable and the USB connection.")
        was = self._health['status']
        self._health = dict(result, text=text)
        if result['status'] != was:
            self.liveChanged.emit()
        if result['status'] == 'low' and was != 'low' and self.controller.config['health_warning']:
            log.warning(text)
            self.station.record_event('receiver', '', text)
            self.controller.toast.emit(text, 'warning')
            notifier = self.controller.notifier
            if notifier.tray is not None and not notifier.quiet_now():
                from PySide6.QtWidgets import QSystemTrayIcon
                notifier.tray.showMessage('AirAlert: reception is low', text, QSystemTrayIcon.MessageIcon.Warning, 12000)
        return result

    # ----------------------------------------------------------------- records
    def _records_rows(self):
        """Station records as display rows: [dict(key, label, value, detail, icon)]."""
        units, factor = self._units()
        found = self._records

        def when(ts, fmt='%b %d, %Y'):
            return datetime.fromtimestamp(ts).strftime(fmt).replace(' 0', ' ')
        rows = []
        for key, label, icon, text in (
                ('farthest', 'Farthest aircraft', 'radar', lambda r: f"{r['value'] / factor:,.1f} {units}"),
                ('farthest_vessel', 'Farthest vessel', 'ship', lambda r: f"{r['value'] / factor:,.1f} {units}"),
                ('highest', 'Highest aircraft', 'arrowUp', lambda r: f"{r['value']:,.0f} ft"),
                ('fastest', 'Fastest aircraft', 'pulse', lambda r: f"{r['value']:,.0f} kn")):
            r = found.get(key)
            if r:
                rows.append(dict(key=key, label=label, icon=icon, value=text(r),
                                 detail=f"{r.get('label', '')} · {when(r['time'])}"))
        r = found.get('busiest_hour')
        if r:
            start = datetime.fromtimestamp(r['time'])
            rows.append(dict(key='busiest_hour', label='Busiest hour', icon='clock', value=f"{r['value']:,} aircraft",
                             detail=f"{when(r['time'])} · {start.strftime('%H:00')}–{(start.hour + 1) % 24:02d}:00"))
        r = found.get('busiest_day')
        if r:
            rows.append(dict(key='busiest_day', label='Busiest day', icon='chart', value=f"{r['value']:,} aircraft",
                             detail=when(r['time'] + 43200)))
        r = found.get('peak_rate')
        if r:
            rows.append(dict(key='peak_rate', label='Busiest minute', icon='signal', value=f"{r['value']:,.0f} msg/s",
                             detail=when(r['time'], '%b %d, %Y %H:%M')))
        return rows

    def _update_live(self, now):
        points = self.tracker.sparkline(now)
        values = [v for v in points if v is not None]
        st = self.station
        end = int(now) + 1
        self._live = dict(points=points, start=end - self.tracker.window, end=end, bin=5,
                          current=st.rate if st.running else 0, running=st.running,
                          simulated=bool(st.sim), peak=max(values) if values else None,
                          average=round(sum(values) / len(values), 1) if values else None)
        self.liveChanged.emit()

    # ----------------------------------------------------------------- rollups
    def _start_catch_up(self):
        try:
            remaining = insights.catch_up(self.conn, self._home())
        except sqlite3.Error:
            log.exception('Statistics rollup failed')
            return
        if remaining and not self._backfilling:
            self._backfilling = True
            log.info('Rolling up %s stored positions for statistics', remaining)
            self._backfill_timer.start()

    def _backfill_step(self):
        """Roll up a large archive in small slices so the interface stays responsive."""
        try:
            remaining = insights.catch_up(self.conn, self._home())
        except sqlite3.Error:
            log.exception('Statistics rollup failed')
            remaining = 0
        if remaining:
            self._backfill_timer.start()
            return
        self._backfilling = False
        if self._active:
            self.reload()
        else:
            self.publish_coverage()

    # ------------------------------------------------------------------- stats
    def _empty_stats(self):
        units = self.controller.config['units']
        return dict(range=self._range, rangeLabel=RANGE_LABELS[self._range], units=units, ready=False, start=0, end=1,
                    rate=dict(points=[], hasData=False, bucketMinutes=insights.RATE_BUCKET[self._range]),
                    hours=dict(values=[0] * 24, hasData=False, peakHour=-1), daily=dict(rows=[], hasData=False),
                    types=dict(rows=[], hasData=False), operators=dict(rows=[], hasData=False),
                    coverage=dict(hasData=False, homeSet=self._home() is not None, aircraft=[], vessel=[]))

    @Slot()
    def reload(self):
        """Recompute every chart (page open, range change and every ~45 s while the page is shown)."""
        now = time.time()
        if not self._backfilling:
            try:
                insights.catch_up(self.conn, self._home())
            except sqlite3.Error:
                log.exception('Statistics rollup failed')
        try:
            data = insights.snapshot(self.conn, self._range, self._include_sim, now)
        except sqlite3.Error:
            log.exception('Statistics query failed')
            return
        try:
            self._records = insights.records(self.conn, self._include_sim, now)
        except sqlite3.Error:
            log.exception('Station records query failed')
        self._stats = self._present(data, now)
        self.statsChanged.emit()
        self.publish_coverage(now, data['coverage'])

    def _present(self, data, now):
        units, factor = self._units()
        rate = data['rate']
        points = [dict(t=p['t'], v=p['value']) for p in rate['points']]
        hours = data['hours']
        daily = data['daily']
        cov = data['coverage']

        def convert(values):
            return [round(v / factor, 1) if v is not None else None for v in values]
        aircraft_cov, vessel_cov = convert(cov['aircraft']), convert(cov['vessel'])
        reach = [v for v in aircraft_cov + vessel_cov if v is not None]
        peak_hour = max(range(24), key=lambda h: hours[h]) if any(hours) else -1
        busiest = max(daily, key=lambda d: d['aircraft'] + d['vessels']) if daily else None
        with_type = sum(r['value'] for r in data['types'])
        return dict(
            range=self._range, rangeLabel=RANGE_LABELS[self._range], includeSim=self._include_sim, units=units,
            ready=True, updated=datetime.fromtimestamp(now).strftime('%H:%M'), elapsedMs=data['elapsedMs'],
            start=data['start'], end=data['end'],
            rate=dict(points=points, hasData=rate['minutes'] > 0, average=rate['average'], peak=rate['peak'],
                      messages=rate['messages'], hours=round(rate['minutes'] / 60, 1),
                      bucketMinutes=insights.RATE_BUCKET[self._range]),
            hours=dict(values=hours, hasData=any(hours), peakHour=peak_hour, total=sum(hours)),
            daily=dict(rows=daily, hasData=any(d['aircraft'] or d['vessels'] for d in daily),
                       busiest=busiest['short'] if busiest and (busiest['aircraft'] or busiest['vessels']) else ''),
            types=dict(rows=data['types'], hasData=bool(data['types']), aircraft=data['aircraft'], withType=with_type),
            operators=dict(rows=data['operators'], hasData=bool(data['operators']), aircraft=data['aircraft']),
            coverage=dict(aircraft=aircraft_cov, vessel=vessel_cov, hasData=bool(reach),
                          hasAircraft=any(v is not None for v in aircraft_cov),
                          hasVessel=any(v is not None for v in vessel_cov),
                          max=max(reach) if reach else 0, homeSet=self._home() is not None,
                          maxAircraft=max((v for v in aircraft_cov if v is not None), default=None),
                          maxVessel=max((v for v in vessel_cov if v is not None), default=None),
                          sectors=sum(1 for a, b in zip(aircraft_cov, vessel_cov) if a is not None or b is not None)))

    @Slot(str)
    def setTimeRange(self, value):
        if value in insights.RANGES and value != self._range:
            self._range = value
            self.reload()

    @Slot(bool)
    def setIncludeSimulation(self, value):
        if bool(value) != self._include_sim:
            self._include_sim = bool(value)
            self.reload()

    @Slot(bool)
    def setActive(self, active):
        """The Statistics page tells the bridge when it is shown (live sparkline and reloads)."""
        self._active = bool(active)
        if self._active:
            self._update_live(time.time())
            self.reload()

    # ---------------------------------------------------------------- coverage
    def publish_coverage(self, now=None, cov=None):
        """Share maximum range per 10° sector with the map: scene_extras['coverage'] (km)."""
        now = time.time() if now is None else now
        self._last_coverage = now
        home = self._home()
        extras = self.controller.scene_extras
        if home is None:
            if extras.pop('coverage', None) is not None:
                self.controller.push_scene()
            return
        if cov is None:
            try:
                cov = insights.coverage(self.conn, now - insights.RANGES[self._range], now, self._include_sim)
            except sqlite3.Error:
                log.exception('Coverage query failed')
                return
        extras['coverage'] = dict(home=home, sectors=list(cov['all']))
        self.controller.push_scene()

    # ------------------------------------------------------- aircraft database
    def _refresh_db_info(self):
        meta = aircraftdb.info(aircraftdb.local_path(self.folder))
        try:
            user = self.conn.execute('SELECT COUNT(*) FROM aircraft').fetchone()[0]
        except sqlite3.Error:
            user = 0
        bundled = 'bundled' in meta['source'].lower()
        self._db = dict(ok=meta['ok'], count=meta['count'], userCount=user, sizeMb=meta['sizeMb'],
                        source=('Bundled snapshot · OpenSky Network and dump1090-fa' if bundled
                                else meta['source'] or 'Not installed'),
                        sourceShort='Bundled snapshot' if bundled else ('OpenSky Network' if meta['ok'] else ''),
                        built=_fmt_date(meta['built'][:10]) if meta['built'] else '',
                        dataDate=_fmt_date(meta['dataDate']))
        self.aircraftDbChanged.emit()

    @Slot()
    def refreshAircraftDb(self):
        self._refresh_db_info()

    def _set_update(self, state, progress=None, message=None):
        self._state = state
        if progress is not None:
            self._progress = max(0.0, min(1.0, progress))
        if message is not None:
            self._message = message
        self.updateChanged.emit()

    @Slot()
    def updateAircraftDb(self):
        """Download OpenSky's public aircraft database and rebuild aircraft.sqlite (asynchronous)."""
        if self._state in ('downloading', 'building'):
            return
        from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest  # noqa: F401
        self._cleanup_files()
        self._cancel.clear()
        try:
            self._file = open(self._zip_path().with_suffix('.part'), 'wb')
        except OSError as e:
            self._set_update('error', 0, f'Could not write to the data folder: {e}')
            return
        if self._nam is None:
            self._nam = QNetworkAccessManager(self)
        request = QNetworkRequest(QUrl(self.url))
        request.setAttribute(QNetworkRequest.Attribute.RedirectPolicyAttribute,
                             QNetworkRequest.RedirectPolicy.NoLessSafeRedirectPolicy)
        request.setHeader(QNetworkRequest.KnownHeaders.UserAgentHeader, f'AirAlert/{__version__}')
        request.setTransferTimeout(60000)
        reply = self._nam.get(request)
        self._reply = reply
        reply.readyRead.connect(self._on_ready_read)
        reply.downloadProgress.connect(self._on_download_progress)
        reply.finished.connect(self._on_download_finished)
        self._set_update('downloading', 0.0, 'Connecting to OpenSky…')

    def _zip_path(self):
        return self.folder / 'aircraftDatabase.zip'

    def _tmp_path(self):
        return self.folder / (aircraftdb.AIRCRAFT_FILE + '.tmp')

    def _on_ready_read(self):
        reply = self._reply
        if reply is None or self._file is None:
            return
        data = bytes(reply.readAll())
        try:
            self._file.write(data)
        except OSError:
            log.exception('Writing the download failed')
            self._message = 'Could not write the download to the data folder.'
            reply.abort()
            return
        if self._file.tell() > MAX_DOWNLOAD:
            self._message = 'The download is unexpectedly large and was stopped.'
            reply.abort()

    def _on_download_progress(self, received, total):
        mb = received / 1024 ** 2
        if total > 0:
            self._set_update('downloading', received / total, f'Downloading {mb:.1f} of {total / 1024 ** 2:.1f} MB')
        else:
            self._set_update('downloading', 0.0, f'Downloading {mb:.1f} MB')

    def _on_download_finished(self):
        from PySide6.QtNetwork import QNetworkReply, QNetworkRequest
        reply, self._reply = self._reply, None
        if reply is None:
            return
        self._on_ready_read_final(reply)
        status = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
        error = reply.error()
        error_text = reply.errorString()
        reply.deleteLater()
        if self._file is not None:
            self._file.close()
            self._file = None
        part = self._zip_path().with_suffix('.part')
        if self._cancel.is_set() or error == QNetworkReply.NetworkError.OperationCanceledError:
            part.unlink(missing_ok=True)
            if self._cancel.is_set():
                self._set_update('idle', 0.0, 'Update cancelled. The current database was kept.')
            else:
                self._set_update('error', 0.0, self._message or 'The download was stopped.')
            return
        if error != QNetworkReply.NetworkError.NoError or (status is not None and int(status) != 200):
            part.unlink(missing_ok=True)
            reason = f'the server replied HTTP {int(status)}' if status is not None and int(status) != 200 else error_text
            self._set_update('error', 0.0, f'Download failed: {reason}. The current database was kept.')
            return
        try:
            part.replace(self._zip_path())
        except OSError as e:
            self._set_update('error', 0.0, f'Could not save the download: {e}')
            return
        self._set_update('building', 0.0, 'Building the aircraft database…')
        self._worker = threading.Thread(target=self._build, name='aircraft-db-build', daemon=True)
        self._worker.start()

    def _on_ready_read_final(self, reply):
        if self._file is not None and not self._cancel.is_set():
            try:
                self._file.write(bytes(reply.readAll()))
            except OSError:
                log.exception('Writing the download failed')

    def _build(self):
        """Worker thread: zip -> aircraft.sqlite.tmp. Reports back through queued signals."""
        try:
            count = aircraftdb.build_from_opensky(self._zip_path(), self._tmp_path(),
                                                  progress=self._buildProgress.emit, cancel=self._cancel.is_set)
            self._buildDone.emit('', count)
        except aircraftdb.Cancelled:
            self._buildDone.emit('cancelled', 0)
        except (OSError, ValueError, sqlite3.Error, KeyError, UnicodeError) as e:
            log.exception('Building the aircraft database failed')
            self._buildDone.emit(str(e) or type(e).__name__, 0)
        except Exception as e:  # zipfile.BadZipFile and friends must not kill the worker silently
            log.exception('Building the aircraft database failed')
            self._buildDone.emit(str(e) or type(e).__name__, 0)

    def _on_build_progress(self, fraction):
        if self._state == 'building':
            self._set_update('building', fraction, f'Building the aircraft database… {fraction * 100:.0f}%')

    def _on_build_done(self, error, count):
        self._worker = None
        self._zip_path().unlink(missing_ok=True)
        if error:
            self._tmp_path().unlink(missing_ok=True)
            if error == 'cancelled':
                self._set_update('idle', 0.0, 'Update cancelled. The current database was kept.')
            else:
                self._set_update('error', 0.0, f'The update could not be built ({error}). The current database was kept.')
            return
        try:
            self.station.db.close_aircraft()  # release the file so it can be replaced
            aircraftdb.install(self._tmp_path(), aircraftdb.local_path(self.folder))
        except OSError as e:
            log.exception('Installing the aircraft database failed')
            self._tmp_path().unlink(missing_ok=True)
            self._set_update('error', 0.0, f'The new database could not be installed ({e}).')
            return
        self._refresh_db_info()
        self._set_update('done', 1.0, f'Updated · {count:,} aircraft')
        self.controller.toast.emit(f'Aircraft database updated · {count:,} aircraft', 'success')

    @Slot()
    def cancelAircraftUpdate(self):
        if self._state == 'downloading' and self._reply is not None:
            self._cancel.set()
            self._reply.abort()
        elif self._state == 'building':
            self._cancel.set()
            self._set_update('building', self._progress, 'Cancelling…')

    def _cleanup_files(self):
        for path in (self._zip_path(), self._zip_path().with_suffix('.part'), self._tmp_path()):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass

    # ---------------------------------------------------------------- shutdown
    @Slot()
    def shutdown(self):
        self._backfill_timer.stop()
        self._cancel.set()
        if self._reply is not None:
            self._reply.abort()
        worker = self._worker
        if worker is not None:
            worker.join(timeout=10)
        if self._file is not None:
            self._file.close()
            self._file = None
        row = self.tracker.flush()
        if row:
            try:
                insights.store_minute(self.conn, row)
                self.tracker.acc = None
            except (sqlite3.Error, AttributeError):
                pass
        self._cleanup_files()


def create(controller):
    return Insights(controller)
