"""QML-facing controller: exposes the Station engine to the Qt Quick interface."""
import json
import logging
import math
import os
import re
import secrets
import sqlite3
import threading
import time
from datetime import datetime

from PySide6.QtCore import QObject, QProcess, Qt, QTimer, QUrl, Property, Signal, Slot
from PySide6.QtGui import QAction, QDesktopServices, QIcon, QWindow
from PySide6.QtNetwork import QNetworkReply, QNetworkRequest
from PySide6.QtWidgets import QApplication, QFileDialog, QMenu, QSystemTrayIcon

from .. import UPDATE_URL, __version__, backup, startup, summary, updates
from ..core.database import export_kml
from ..core.models import UNIT_KM
from ..notify import Notifier, in_quiet_hours
from ..core.receivers import resource_root
from ..engine import (CONDITIONS, DISTANCE_CONDITIONS, DISTANCE_EXTRAS, FIELDS, HISTORY_CATEGORIES, KINDS, MODES,
                      RECEIVER_KEYS, Station, nav_status_name, rule_sentence, ship_type_name, stamp)
from .mapcanvas import ALTITUDE_STOPS, PALETTES, UNKNOWN_ALTITUDE
from .models import RowModel, SortProxy
from .tiles import TileManager

try:
    import winsound
except ImportError:  # pragma: no cover - non-Windows development
    winsound = None

log = logging.getLogger(__name__)

TRAFFIC_ROLES = ['key', 'label', 'kind', 'identifier', 'detail', 'secondary', 'altitude', 'speed', 'distance', 'bearing',
                 'heading', 'lastSeen', 'lastSeenText', 'stale', 'hasPosition', 'watched', 'simulated', 'squawk',
                 'emergency', 'phase', 'phaseCode']
RULE_ROLES = ['key', 'row', 'name', 'enabled', 'kind', 'match', 'condition', 'threshold', 'cooldown', 'sound',
              'desktop', 'speak', 'phone', 'overrideQuiet', 'extras', 'sentence', 'inactive', 'firedTotal',
              'firedDay', 'firedWeek', 'daily', 'lastFired', 'snoozedUntil']
WATCH_ROLES = ['key', 'row', 'name', 'identifier', 'kind', 'notes', 'lastSeen', 'lastSeenText', 'sightings', 'live']
HISTORY_ROLES = ['key', 'row', 'title', 'kind', 'first', 'firstText', 'last', 'lastText', 'source', 'isSighting',
                 'category']
FEED_ROLES = ['key', 'text', 'time', 'timeText', 'dayText', 'target', 'simulated']
FIELD_ROLES = ['key', 'label', 'value']
CONDITION_LABELS = {'enter': 'Comes within range of home', 'leave': 'Moves out of range of home',
                    'first': 'Is first detected', 'signal': 'Is heard (first valid message)',
                    'altitude_below': 'Flies below an altitude', 'speed_above': 'Moves faster than a speed',
                    'zone_enter': 'Enters a geofence', 'zone_leave': 'Leaves a geofence',
                    'approach': 'Will pass near home (predicted)',
                    'squawk_emergency': 'Squawks an emergency code (7500/7600/7700)',
                    'first_aircraft': 'Is an aircraft never seen here before',
                    'first_type': 'Is an aircraft type never seen here before',
                    'first_operator': 'Flies for an airline never seen here before',
                    'rare_type': 'Is a rare aircraft type here',
                    'military': 'Is a military aircraft', 'circling': 'Is circling'}
EXTRA_LABELS = {'within': ('Is within distance of home', 'distance'), 'beyond': ('Is beyond distance of home', 'distance'),
                'altitude_below': ('Is below altitude', 'ft'), 'altitude_above': ('Is above altitude', 'ft'),
                'speed_above': ('Is faster than', 'kn'), 'speed_below': ('Is slower than', 'kn'),
                'in_zone': ('Is inside geofence', 'zone'), 'out_zone': ('Is outside geofence', 'zone')}
FIELD_LABELS = {'registration': 'Registration (tail number)', 'icao': 'ICAO address', 'callsign': 'Callsign',
                'type': 'Aircraft / ship type', 'squawk': 'Squawk code', 'mmsi': 'MMSI', 'name': 'Vessel name',
                'imo': 'IMO number',
                'identifier': 'Identifier (ICAO or MMSI)'}
# The altitude filter slider's top position means "and above" (a few aircraft cruise over 50,000 ft).
ALTITUDE_BAND_TOP = 50000
LOCATION_SCRIPT = ("Add-Type -AssemblyName System.Device; $g=New-Object System.Device.Location.GeoCoordinateWatcher; "
                   "$g.Start(); for($i=0;$i -lt 24 -and $g.Position.Location.IsUnknown;$i++){Start-Sleep -Milliseconds 500}; "
                   "if(!$g.Position.Location.IsUnknown){$g.Position.Location | Select-Object Latitude,Longitude | ConvertTo-Json}; $g.Stop()")


def _parse_time(text, default):
    text = (text or '').strip()
    if not text:
        return default
    for fmt in ('%Y-%m-%d %H:%M', '%Y-%m-%d %H:%M:%S', '%Y-%m-%d'):
        try:
            return datetime.strptime(text, fmt).timestamp()
        except ValueError:
            continue
    raise ValueError(f'Use YYYY-MM-DD HH:MM for dates (got “{text}”).')


def format_coordinate(lat, lon):
    """34.0522°N 118.2437°W"""
    return (f'{abs(lat):.4f}°{"N" if lat >= 0 else "S"} '
            f'{abs(lon):.4f}°{"E" if lon >= 0 else "W"}')


def _comparable(value):
    """A stored setting in comparable form: numbers by value (60 == 60.0 == '60'), text trimmed and case-folded."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return str(value).strip().casefold()


class Controller(QObject):
    stateChanged = Signal()
    appearanceChanged = Signal()
    countsChanged = Signal()
    selectionChanged = Signal()
    rulesChanged = Signal()
    zonesChanged = Signal()
    historyChanged = Signal()
    zoneStateChanged = Signal()
    alertsChanged = Signal()
    settingsChanged = Signal()
    toast = Signal(str, str)
    alertRaised = Signal(str, str, str)
    openRuleEditor = Signal(int, 'QVariantMap')
    openWatchEditor = Signal(int, 'QVariantMap')
    openSettings = Signal(bool, str)
    receiversDetected = Signal(str, 'QVariantList')
    locationResolved = Signal(bool, float, float, str)
    requestPage = Signal(int)
    notice = Signal(str, str)
    updateChanged = Signal()
    backupFinished = Signal(bool, str, bool)   # ok, file path or error, announce

    def __init__(self, station=None, tray=True, parent=None):
        super().__init__(parent)
        self.station = station or Station()
        self.config = self.station.config
        self.tiles = TileManager(self.config.folder, self)
        self.tiles.set_enabled(self.config['tiles'])
        self.tiles.tileReady.connect(self._tile_ready)
        self.tiles.statusChanged.connect(lambda _: self.stateChanged.emit())
        self.map = None
        self.window = None
        self._mode_choice = self.config['mode']
        self._search = ''
        self._selected = ''
        self._history_target = None
        self._history_track = []
        self._history_rows = []
        self._history_filters = {}
        self._history_info = 'Choose filters and search your local archive.'
        self._details = self._overview_details()
        self._counts = (0, 0, 0)
        self._page = 0
        self._unread = 0
        self._latest_alert = {}
        self._zone = dict(mode='', title='', hint='', name='', original='')
        self._pending_zone = []
        self._detect_process = None
        self._locator = None
        # Extension points used by the feature bridges (see ui/bridges.py):
        self.refresh_hooks = []      # callables(now) run once per second after the refresh
        self.scene_extras = {}       # extra map-scene keys, e.g. 'coverage', 'now' (playback clock)
        self.target_provider = None  # callable() -> [Target]; replaces live targets (history playback)
        self.bridges = {}
        self.trafficSource = RowModel(TRAFFIC_ROLES, parent=self)
        self._traffic = SortProxy(self.trafficSource, 'distance', parent=self)
        self._rules = RowModel(RULE_ROLES, parent=self)
        self.watchSource = RowModel(WATCH_ROLES, parent=self)
        self._watch = SortProxy(self.watchSource, 'name', parent=self)
        self.historySource = RowModel(HISTORY_ROLES, parent=self)
        self._history = SortProxy(self.historySource, 'last', ascending=False, parent=self)
        self._feed = RowModel(FEED_ROLES, parent=self)
        # The inspector's field list: values update in place every second, so it keeps its scroll position.
        self._detail_fields = RowModel(FIELD_ROLES, parent=self)
        self._fields_owner = None
        self.selectionChanged.connect(self._sync_detail_fields)
        self._load_feed()
        self._refresh_rules()
        self._refresh_watch()
        self.tray = None
        self._quitting = False
        self._tray_hint_shown = False
        if tray and QSystemTrayIcon.isSystemTrayAvailable():
            self._build_tray()
        self.notifier = Notifier(self.config, self.tray, self)
        self.notifier.phoneResult.connect(lambda service, ok, message:
                                          self.toast.emit(message, 'success' if ok else 'error'))
        self._housekeeping_at = 0.0
        self._backup_thread = None
        self._backup_tried = 0.0
        self.backupFinished.connect(self._backup_done, Qt.ConnectionType.QueuedConnection)
        # AIRALERT_UPDATE_URL overrides the feed address; any value that is not an https address (e.g. 'off')
        # disables update checks, which tests, developer tools and the packaged self-test rely on.
        self.update_url = os.environ.get('AIRALERT_UPDATE_URL', UPDATE_URL)
        if not self.update_url.lower().startswith('https://'):
            self.update_url = ''
        self._update = dict(configured=bool(self.update_url), available=False, version='', url='', notes='', status='')
        self._update_checked = 0.0
        self._update_announced = ''
        self.pump = QTimer(self)
        self.pump.setInterval(250)
        self.pump.timeout.connect(self._tick)
        self.pump.start()
        self.render = QTimer(self)
        self.render.setInterval(1000)
        self.render.timeout.connect(self._second)
        self.render.start()

    # -------------------------------------------------------------- properties
    def _p(ptype, getter, notify):
        return Property(ptype, getter, notify=notify)

    version = Property(str, lambda self: __version__, constant=True)
    modes = _p('QVariantList', lambda self: self.modesFor(self.config['show_simulation']), settingsChanged)
    simulationEnabled = _p(bool, lambda self: bool(self.config['show_simulation']), settingsChanged)
    running = _p(bool, lambda self: self.station.running, stateChanged)
    mode = _p(str, lambda self: self.station.mode if self.station.running else self._mode_choice, stateChanged)
    isSimulation = _p(bool, lambda self: bool(self.station.sim), stateChanged)
    homeSet = _p(bool, lambda self: bool(self.config['home']), settingsChanged)
    dataFolder = Property(str, lambda self: str(self.config.folder), constant=True)
    theme = _p(str, lambda self: self.config['theme'], appearanceChanged)
    mapTheme = _p(str, lambda self: self.config['map_theme'], appearanceChanged)
    units = _p(str, lambda self: self.config['units'], settingsChanged)
    tilesEnabled = _p(bool, lambda self: bool(self.config['tiles']), settingsChanged)
    layers = _p('QVariantMap', lambda self: dict(self.config['map_layers']), appearanceChanged)
    altitudeFilter = _p('QVariantMap', lambda self: dict(self.config['altitude_filter']), appearanceChanged)
    headingMinutes = _p(int, lambda self: int(self.config['heading_minutes']), appearanceChanged)
    gain = _p(str, lambda self: str(self.config['gain']), settingsChanged)
    closeToTray = _p(bool, lambda self: bool(self.config['close_to_tray']) and self.tray is not None, settingsChanged)
    quietActive = _p(bool, lambda self: in_quiet_hours(self.config['quiet_hours']), stateChanged)
    hasEmergencyRule = _p(bool, lambda self: self.station.has_emergency_rule(), rulesChanged)
    snoozeInfo = _p('QVariantMap', lambda self: self.station.snooze_summary(), alertsChanged)
    updateInfo = _p('QVariantMap', lambda self: dict(self._update, current=__version__), updateChanged)
    trafficModel = Property(QObject, lambda self: self._traffic, constant=True)
    rulesModel = Property(QObject, lambda self: self._rules, constant=True)
    watchModel = Property(QObject, lambda self: self._watch, constant=True)
    historyModel = Property(QObject, lambda self: self._history, constant=True)
    alertFeed = Property(QObject, lambda self: self._feed, constant=True)
    detailFields = Property(QObject, lambda self: self._detail_fields, constant=True)
    aircraftCount = _p(int, lambda self: self._counts[0], countsChanged)
    vesselCount = _p(int, lambda self: self._counts[1], countsChanged)
    visibleCount = _p(int, lambda self: self._counts[2], countsChanged)
    rate = _p(int, lambda self: self.station.rate, countsChanged)
    unreadAlerts = _p(int, lambda self: self._unread, alertsChanged)
    latestAlert = _p('QVariantMap', lambda self: self._latest_alert, alertsChanged)
    selectedKey = _p(str, lambda self: self._selected, selectionChanged)
    details = _p('QVariantMap', lambda self: self._details, selectionChanged)
    zoneMode = _p(str, lambda self: self._zone['mode'], zoneStateChanged)
    zoneTitle = _p(str, lambda self: self._zone['title'], zoneStateChanged)
    zoneHint = _p(str, lambda self: self._zone['hint'], zoneStateChanged)
    zoneName = _p(str, lambda self: self._zone['name'], zoneStateChanged)
    historyInfo = _p(str, lambda self: self._history_info, historyChanged)

    def _map_style(self):
        """The map palette in use ('Dark' or 'Light'; the radar scope has its own and shows no legend)."""
        style = self.config['map_theme']
        if style not in ('Dark', 'Light'):
            style = 'Dark' if style == 'Scope only' else self.config['theme']
        return style

    def _legend(self):
        return [dict(altitude=a, color=c) for a, c in ALTITUDE_STOPS[self._map_style()]]

    # Map legend: the colors the map actually draws with.
    altitudeLegend = _p('QVariantList', _legend, appearanceChanged)
    mapKey = _p('QVariantMap', lambda self: dict(unknown=UNKNOWN_ALTITUDE[self._map_style()],
                                                 **{k: PALETTES[self._map_style()][k]
                                                    for k in ('vessel', 'stale', 'star', 'outline')}),
                appearanceChanged)
    # Traffic list: the same ramp tuned for the app appearance, so text stays readable on its panels.
    panelAltitudeStops = _p('QVariantList', lambda self: [dict(altitude=a, color=c) for a, c in
                                                          ALTITUDE_STOPS[self.config['theme']]], appearanceChanged)

    def _zones(self):
        return [dict(name=z['name'], rules=self.station.linked_rules(z['name']), points=len(z['points']))
                for z in self.config['geofences']]

    zones = _p('QVariantList', _zones, zonesChanged)

    def _source_text(self):
        if self.station.running:
            return 'Simulation' if self.station.sim else 'Live RF'
        return 'Paused' if self.station.paused_reason else 'Idle'

    sourceText = _p(str, _source_text, stateChanged)

    def _health(self):
        if self.station.paused_reason and not self.station.running:
            return self.station.paused_reason
        if self.station.receiver_health:
            return ' · '.join(f'{k}: {v}' for k, v in self.station.receiver_health.items())[:160]
        if self.station.sim and self.station.running:
            return 'Synthetic traffic · not received RF'
        return 'Receiver idle'

    healthText = _p(str, _health, stateChanged)

    def _map_status(self):
        if self.config['map_theme'] == 'Scope only':
            return 'Radar scope · cosmetic sweep'
        return self.tiles.status if self.config['tiles'] else 'Offline grid'

    mapStatus = _p(str, _map_status, stateChanged)
    locationText = _p(str, lambda self: 'Station location set' if self.config['home'] else 'Location not set',
                      settingsChanged)
    del _p

    # --------------------------------------------------------------- map link
    @Slot(QObject)
    def attachMap(self, item):
        self.map = item
        item.tiles = self.tiles
        item.targetClicked.connect(self.selectTarget)
        item.backgroundClicked.connect(self._background_clicked)
        item.zoneFinished.connect(self._zone_drawn)
        item.drawingChanged.connect(self._drawing_changed)
        home = self.config['home']
        if home:
            item.centerOnZoom(home[0], home[1], 9)
        else:
            item.centerOnZoom(25, -40, 2.6)
        self._push_scene()

    def attach_window(self, window):
        self.window = window
        if not self.config['setup_done']:
            QTimer.singleShot(350, lambda: self.openSettings.emit(True, 'general'))
        if self.config.warning:
            QTimer.singleShot(600, lambda: self.notice.emit('Settings recovered', self.config.warning))
        if self.config['auto_start'] and self.config['setup_done']:
            QTimer.singleShot(1500, self._auto_start)
        if self.config['check_updates'] and self.update_url:
            QTimer.singleShot(15000, lambda: self.checkUpdates(False))

    def _auto_start(self):
        if not self.station.running:
            log.info('Starting monitoring automatically (%s)', self._mode_choice)
            self.start()

    def _tile_ready(self):
        if self.map is not None:
            self.map.update()

    def now(self):
        """Current display time: the playback clock when replaying history, else wall time."""
        return self.scene_extras.get('now') or time.time()

    def _passes_filters(self, t):
        layers = self.config['map_layers']
        if (t.kind == 'aircraft' and not layers.get('aircraft', True)) or \
                (t.kind == 'vessel' and not layers.get('vessels', True)):
            return False
        band = self.config['altitude_filter']
        if band.get('enabled') and t.kind == 'aircraft':
            altitude = t.data.get('altitude')
            if altitude is None:
                if not band.get('include_unknown', True):
                    return False
            else:
                high = band.get('max', ALTITUDE_BAND_TOP)
                if not band.get('min', 0) <= altitude <= (high if high < ALTITUDE_BAND_TOP else math.inf):
                    return False
        if self._search:
            haystack = (t.identifier + ' ' + ' '.join(map(str, t.data.values()))).casefold()
            if self._search.casefold() not in haystack:
                return False
        return True

    def current_targets(self):
        """Targets shown in the list and on the map (live, or history playback), filtered."""
        source = self.target_provider() if self.target_provider else self.station.store.targets.values()
        return [t for t in source if self._passes_filters(t)]

    def lookup_target(self, key):
        if self.target_provider:
            found = next((t for t in self.target_provider() if t.key == key), None)
            if found is not None:
                return found
        return self.station.store.targets.get(key)

    def _scene(self):
        units = self.config['units']
        targets = self.current_targets()
        scene = dict(targets=targets, selected=self._selected, watched=self.station.watched_ids(),
                    home=tuple(self.config['home']) if self.config['home'] else None,
                    rings=[(r * UNIT_KM[units], f'{r:g} {units}') for r in self.config['rings']], units=units,
                    zones=self.config['geofences'], history=[(r['lat'], r['lon']) for r in self._history_track],
                    draft=self._pending_zone, draft_name=self._zone['name'] if self._zone['mode'] != 'create' else '',
                    trail_minutes=self.config['trail_minutes'], layers=dict(self.config['map_layers']),
                    tiles=bool(self.config['tiles']), theme=self.config['theme'], map_theme=self.config['map_theme'],
                    heading_minutes=self.config['heading_minutes'], now=None)
        scene.update(self.scene_extras)
        return scene

    def _push_scene(self):
        if self.map is not None:
            self.map.set_scene(self._scene())

    def push_scene(self):
        """Re-render the map now (bridges call this after changing scene_extras/target_provider).
        Skipped while the window is hidden; showing it refreshes everything."""
        if self._ui_visible():
            self._push_scene()

    # ---------------------------------------------------------------- engine
    def _tick(self):
        try:
            notices, expired = self.station.tick()
        except (sqlite3.Error, OSError, ValueError):
            already_paused = not self.station.running and bool(self.station.paused_reason)
            if not already_paused:
                log.exception('Monitoring error')
            self.station.pause('Monitoring paused · storage or data error; see log')
            if not already_paused:
                self.toast.emit('Monitoring paused because of a storage or data error. Details are in the log.',
                                'error')
            self.stateChanged.emit()
            return
        for n in notices:
            self._raise_alert(n)
        if self._selected and self._selected in expired:
            self._selected = ''
            self._details = self._overview_details()
            self.selectionChanged.emit()
            self.push_scene()  # drop the selection ring now, not at the next refresh

    def _raise_alert(self, n):
        when = datetime.fromtimestamp(n['time'])
        snoozed = bool(n.get('snoozed'))   # recorded in the feed and History, but nothing flashes or sounds
        if not snoozed:
            self._latest_alert = dict(text=n['text'], key=n['key'], time=when.strftime('%H:%M:%S'), rule=n['rule'],
                                      ruleId=n.get('rule_id', ''), label=n['label'], simulated=n['simulated'],
                                      stamp=n['time'])
            self._unread += 1
        self._load_feed()
        self._rules.sync_rows(self.station.rule_rows())   # "fired N times" on the rule cards
        self.alertsChanged.emit()
        if not snoozed:
            self.alertRaised.emit(n['text'], n['key'], when.strftime('%H:%M:%S'))
            self.notifier.deliver(n)

    def _load_feed(self):
        rows = self.station.db.conn.execute(
            "SELECT id,time,key,message,simulated FROM events WHERE category='alert' ORDER BY time DESC LIMIT 60").fetchall()
        today = datetime.now().date()
        feed = []
        for r in rows:
            when = datetime.fromtimestamp(r['time'])
            feed.append(dict(key=str(r['id']), text=r['message'], time=r['time'], timeText=when.strftime('%H:%M:%S'),
                             dayText='Today' if when.date() == today else when.strftime('%b %d'),
                             target=r['key'], simulated=bool(r['simulated'])))
        self._feed.sync_rows(feed)  # new alerts slide in at the top without scrolling the list
        if feed and not self._latest_alert:
            first = feed[0]
            self._latest_alert = dict(text=first['text'], key=first['target'], time=first['timeText'],
                                      stamp=first['time'], label='', rule='', ruleId='',
                                      simulated=first['simulated'])

    def _ui_visible(self):
        """False while the window is hidden in the tray or minimized: nothing is drawn, so nothing needs updating."""
        window = self.window
        if window is None:
            return True
        try:
            return bool(window.isVisible()) and window.visibility() != QWindow.Visibility.Minimized
        except Exception:
            return True

    def _second(self):
        """Once a second: sample the message rate, refresh the views (only while they are on screen), then run
        the extension hooks.

        Only this timer samples the rate, so refreshes caused by searching or filtering never
        reset the messages-per-second count."""
        self.station.sample_rate()
        if self._ui_visible():
            self.refresh()
        wall = time.time()
        if self._page == 3 and int(wall) % 5 == 0:
            self._refresh_watch()
        if wall - self._housekeeping_at >= 30:
            self._housekeeping_at = wall
            try:
                self._housekeeping(wall)
            except (sqlite3.Error, OSError, ValueError):
                log.exception('Housekeeping failed')
        for hook in list(self.refresh_hooks):
            try:
                hook(wall)
            except Exception:  # a broken extension must not stop monitoring
                log.exception('Refresh hook failed')

    def _housekeeping(self, wall):
        """Every 30 s: expired snoozes, the daily summary, automatic backups and the daily update check."""
        if self.station.prune_snoozes(wall):
            self._refresh_rules()
            self.alertsChanged.emit()
        if summary.due(self.station.db.conn, self.config['daily_summary'], wall):
            self._send_summary(wall)
        settings = self.config['backup']
        if settings.get('enabled') and settings.get('folder') and wall - self._backup_tried > 3600 and \
                backup.due(settings['folder'], settings.get('every_days', 7), wall):
            self._start_backup(announce=False)
        if self.config['check_updates'] and self.update_url and wall - self._update_checked > 86400:
            self.checkUpdates(False)

    # ---------------------------------------------------------------- snooze
    @Slot(str, str, int)
    def snooze(self, scope, key, minutes):
        """Silence 'all' alerts, a 'rule' (key = rule id) or a 'target' (key) for some minutes.
        minutes < 0 = for the rest of today, 0 = wake it now."""
        now = time.time()
        if minutes < 0:
            until = datetime.now().replace(hour=23, minute=59, second=59, microsecond=0).timestamp()
        else:
            until = now + minutes * 60 if minutes else 0
        error = self.station.snooze(scope, key, until)
        if error:
            self.toast.emit(error, 'warning')
            return
        self._refresh_rules()
        self.alertsChanged.emit()
        what = {'all': 'All alerts', 'rule': 'This alert', 'target': key.split(':', 1)[-1]}.get(scope, 'Alerts')
        if until:
            self.toast.emit(f"{what} snoozed until {datetime.fromtimestamp(until).strftime('%H:%M')}. "
                            'Alerts are still recorded.', 'info')
        else:
            self.toast.emit(f'{what}: snooze ended', 'info')

    @Slot()
    def wakeAll(self):
        """End every snooze."""
        self.station._save_values(snooze=dict(all=0, rules={}, targets={}))
        self._refresh_rules()
        self.alertsChanged.emit()
        self.toast.emit('Alerts resumed', 'info')

    # --------------------------------------------------------- daily summary
    def _send_summary(self, wall, manual=False):
        info = summary.day_summary(self.station.db.conn, self.config['units'], wall)
        if not manual:
            summary.mark_sent(self.station.db.conn, wall)
            if not info['monitored'] and not info['aircraft'] and not info['vessels']:
                return   # nothing happened today: no point in a message
        self.station.record_event('application', '', info['text'])
        if self.tray is not None:
            self.tray.showMessage('AirAlert daily summary', info['text'], QSystemTrayIcon.MessageIcon.Information, 12000)
        if self.config['daily_summary'].get('phone', True):
            self.notifier.send_phone('AirAlert daily summary', info['text'])
        self.toast.emit(info['text'], 'info')

    @Slot()
    def sendSummaryNow(self):
        self._send_summary(time.time(), manual=True)

    # ---------------------------------------------------------------- backups
    def _start_backup(self, announce):
        if self._backup_thread is not None and self._backup_thread.is_alive():
            return False
        settings = dict(self.config['backup'])
        self._backup_tried = time.time()
        self.station.db.conn.commit()
        data_folder = self.config.folder

        def work():
            try:
                path = backup.make_backup(data_folder, settings.get('folder', ''), settings.get('keep', 5))
                self.backupFinished.emit(True, str(path), announce)
            except Exception as e:  # reported to the user; never crash the worker
                log.exception('Backup failed')
                self.backupFinished.emit(False, str(e), announce)
        self._backup_thread = threading.Thread(target=work, name='backup', daemon=True)
        self._backup_thread.start()
        return True

    def _backup_done(self, ok, text, announce):
        if ok:
            log.info('Backup written: %s', text)
            if announce:
                self.toast.emit(f'Backup saved: {text}', 'success')
        else:
            self.toast.emit(f'The backup could not be made: {text}', 'error')
        self.settingsChanged.emit()

    @Slot(str, result=str)
    def backupNow(self, folder):
        """Back up to folder now (the Settings dialog passes what is typed, saved or not). Returns '' or an error."""
        folder = str(folder or '').strip()
        if not folder:
            return 'Choose a backup folder first.'
        settings = dict(self.config['backup'], folder=folder)
        self.config.values['backup'] = settings
        try:
            self.config.save()
        except (OSError, ValueError) as e:
            return f'Could not save the backup folder: {e}'
        return '' if self._start_backup(announce=True) else 'A backup is already running.'

    @Slot(str, result=str)
    def chooseFolder(self, current):
        return QFileDialog.getExistingDirectory(None, 'Choose a folder', str(current or ''))

    @Slot(str, result=str)
    def lastBackupText(self, folder):
        newest = backup.latest(folder) if str(folder or '').strip() else None
        return f'Last backup {stamp(newest[1])[:16]}' if newest else 'No backup yet'

    # ---------------------------------------------------------------- updates
    @Slot(bool)
    def checkUpdates(self, manual=True):
        """Ask the update address whether a newer version exists. Nothing is downloaded or installed."""
        self._update_checked = time.time()
        if not self.update_url:
            self._update.update(configured=False, status='Update checks are not set up for this copy of AirAlert.')
            self.updateChanged.emit()
            return
        request = QNetworkRequest(QUrl(self.update_url))
        request.setHeader(QNetworkRequest.KnownHeaders.UserAgentHeader, f'AirAlert/{__version__}')
        request.setTransferTimeout(15000)
        reply = self.notifier.network.get(request)
        self._update.update(configured=True, status='Checking…')
        self.updateChanged.emit()

        def finished():
            try:
                if reply.error() != QNetworkReply.NetworkError.NoError:
                    raise ValueError(f'Could not check for updates: {reply.errorString()}')
                found = updates.parse_feed(bytes(reply.readAll()), __version__)
                self._update.update(found, status=f"Version {found['version']} is available." if found['available']
                                    else f'You have the latest version ({__version__}).')
                if found['available'] and (manual or self._update_announced != found['version']):
                    self._update_announced = found['version']
                    self.toast.emit(f"AirAlert {found['version']} is available. Open Settings → Connections to "
                                    'download it.', 'info')
            except ValueError as e:
                self._update.update(available=False, status=str(e))
                if manual:
                    self.toast.emit(str(e), 'warning')
            finally:
                reply.deleteLater()
                self.updateChanged.emit()
        reply.finished.connect(finished)

    @Slot()
    def openUpdate(self):
        url = str(self._update.get('url') or '')
        if url.lower().startswith('https://'):
            QDesktopServices.openUrl(QUrl(url))

    @Slot()
    def refresh(self):
        now = self.now()
        ids = self.station.watched_ids()
        targets = self.current_targets()
        self.trafficSource.update_rows([self.station.target_row(t, now, ids) for t in targets])
        everything = list(self.target_provider()) if self.target_provider else list(self.station.store.targets.values())
        aircraft = sum(t.kind == 'aircraft' for t in everything)
        self._counts = (aircraft, len(everything) - aircraft, len(targets))
        self.countsChanged.emit()
        self._push_scene()
        if self._selected:
            selected = self.lookup_target(self._selected)
            if selected is not None:
                self._details = self._target_details(selected)
                self.selectionChanged.emit()
        self.stateChanged.emit()

    # ---------------------------------------------------------------- control
    @Slot(bool, result='QVariantList')
    def modesFor(self, show_simulation):
        """The monitoring modes offered to the user: Simulation only once it has been switched on."""
        return [m for m in MODES if m != 'Simulation' or show_simulation]

    @Slot(str)
    def setMode(self, mode):
        if mode in self.modesFor(self.config['show_simulation']) and not self.station.running:
            self._mode_choice = mode
            self.stateChanged.emit()

    @Slot()
    def start(self):
        error = self.station.start(self._mode_choice)
        if error:
            self.toast.emit(error, 'warning')
            if 'home location' in error:
                self.openSettings.emit(False, 'location')
            return
        self._selected = ''
        self._history_target = None
        self._history_track = []
        self._details = self._overview_details()
        self.selectionChanged.emit()
        self.stateChanged.emit()
        self.toast.emit(f'Monitoring started · {self._mode_choice}', 'success')
        self.refresh()

    @Slot()
    def stop(self):
        self.station.stop()
        self.stateChanged.emit()
        self.toast.emit('Monitoring stopped', 'info')

    @Slot()
    def toggleMonitoring(self):
        self.stop() if self.station.running else self.start()

    @Slot(str)
    def setSearch(self, text):
        self._search = text
        self.refresh()

    @Slot(str)
    def setTheme(self, name):
        if name in ('Dark', 'Light') and name != self.config['theme']:
            self.config.values['theme'] = name
            self.config.save()
            self.appearanceChanged.emit()
            current = self._current_target()
            if current is not None:
                # Keep the history flag: a replayed track must stay a "historical track" after a theme change.
                self._details = self._target_details(current, history=self._history_target is not None)
                if self._history_target is not None:
                    self._details['trackPoints'] = len(self._history_track)
            else:
                self._details = self._overview_details()
            self.selectionChanged.emit()
            self._push_scene()

    @Slot()
    def toggleTheme(self):
        self.setTheme('Light' if self.config['theme'] == 'Dark' else 'Dark')

    @Slot(str)
    def setMapTheme(self, name):
        if name in ('Follow app', 'Dark', 'Light', 'Scope only'):
            entering_scope = name == 'Scope only' and self.config['map_theme'] != 'Scope only'
            self.config.values['map_theme'] = name
            self.config.save()
            self.appearanceChanged.emit()
            self.stateChanged.emit()
            self._push_scene()
            home = self.config['home']
            if entering_scope and home and self.map is not None:
                # The radar is most useful centered on the station; drag to move it.
                self.map.centerOn(home[0], home[1])

    @Slot(str, bool)
    def setLayer(self, name, value):
        if name in self.config['map_layers']:
            self.config['map_layers'][name] = bool(value)
            self.config.save()
            self.appearanceChanged.emit()
            self.refresh()

    @Slot(bool, float, float, bool)
    def setAltitudeFilter(self, enabled, minimum, maximum, include_unknown):
        low, high = sorted((max(0.0, minimum), min(100000.0, maximum)))
        self.config.values['altitude_filter'] = dict(enabled=bool(enabled), min=int(low), max=int(high),
                                                     include_unknown=bool(include_unknown))
        self.config.save()
        self.appearanceChanged.emit()
        self.refresh()

    @Slot(int)
    def setHeadingMinutes(self, minutes):
        self.config.values['heading_minutes'] = max(1, min(30, int(minutes)))
        self.config.save()
        self.appearanceChanged.emit()
        self._push_scene()

    @Slot(str, result=str)
    def applyGain(self, gain):
        """Save a receiver gain ('auto' or dB), e.g. from the reception check. Returns '' or an error."""
        if self.station.running:
            return 'Stop monitoring before changing the receiver gain.'
        error = self.station.save_settings(dict(gain=str(gain).strip().lower() or 'auto'))
        if not error:
            self.settingsChanged.emit()
            self.toast.emit(f'Receiver gain set to {gain}' + ('' if gain == 'auto' else ' dB'), 'success')
        return error

    @Slot()
    def centerHome(self):
        home = self.config['home']
        if home and self.map is not None:
            self.map.centerOn(home[0], home[1])
        elif not home:
            self.toast.emit('Set your station location in Settings to use Center.', 'info')
            self.requestSettings('location')

    @Slot(str)
    def requestSettings(self, section='general'):
        self.openSettings.emit(False, section or 'general')

    # -------------------------------------------------------------- selection
    def _current_target(self):
        if self._selected:
            return self.lookup_target(self._selected)
        return self._history_target

    @Slot(str)
    def selectTarget(self, key):
        t = self.lookup_target(key)
        if not t:
            return
        self._selected = key
        self._history_target = None
        if self._history_track:
            self._history_track = []
        self._details = self._target_details(t)
        self.selectionChanged.emit()
        self._push_scene()

    @Slot(str)
    def focusTarget(self, key):
        """Select and center a live target (used by list rows and alert links)."""
        t = self.lookup_target(key)
        if t is None:
            # e.g. an older alert whose target has since left reception range
            self.toast.emit('That target is no longer being tracked. Its sightings are in History.', 'info')
            return
        self.selectTarget(key)
        if t.position and self.map is not None:
            self.map.centerOn(*t.position)

    def _background_clicked(self):
        if self._selected:
            self.clearSelection()

    @Slot()
    def clearSelection(self):
        self._selected = ''
        self._history_target = None
        self._history_track = []
        self._details = self._overview_details()
        self.selectionChanged.emit()
        self._push_scene()

    def _sync_detail_fields(self):
        """Mirror details['fields'] into the inspector's list model (runs on every selectionChanged)."""
        d = self._details
        rows = [dict(key=f['label'], label=f['label'], value=str(f['value'])) for f in d.get('fields', [])]
        owner = (d.get('mode'), d.get('key'))
        if owner != self._fields_owner:
            self._fields_owner = owner
            self._detail_fields.set_rows(rows)  # another target: its fields start at the top
        else:
            self._detail_fields.sync_rows(rows)

    def _overview_details(self):
        return dict(mode='overview', title='Station overview', kind='', source='', metrics=[], fields=[],
                    watched=False, history=False)

    def _target_details(self, t, history=False):
        if t is None:
            return self._overview_details()
        fields = self.station.details(t, self.now())
        lookup = {k: v for k, v in fields}
        row = self.station.target_row(t, self.now())
        units = self.config['units']

        def metric(label, value, unit):
            return dict(label=label, value=value if value is not None else '—', unit=unit if value is not None else '')
        if t.kind == 'aircraft':
            vs = t.data.get('vertical_speed')
            metrics = [metric('Altitude', f"{row['altitude']:,}" if row['altitude'] is not None else None, 'ft'),
                       metric('Ground speed', f"{row['speed']:.0f}" if row['speed'] is not None else None, 'kn'),
                       metric('Distance', f"{row['distance']:.1f}" if row['distance'] is not None else None, units),
                       metric('Bearing', f"{row['bearing']:03d}" if row['bearing'] is not None else None, '°'),
                       metric('Vertical rate', f'{vs:+,.0f}' if vs is not None else None, 'ft/min'),
                       metric('Heading', f"{t.data['heading']:.0f}" if t.data.get('heading') is not None else None, '°')]
        else:
            metrics = [metric('Speed', f"{row['speed']:.1f}" if row['speed'] is not None else None, 'kn'),
                       metric('Course', f"{t.data['course']:.0f}" if t.data.get('course') is not None else None, '°'),
                       metric('Distance', f"{row['distance']:.1f}" if row['distance'] is not None else None, units),
                       metric('Bearing', f"{row['bearing']:03d}" if row['bearing'] is not None else None, '°'),
                       metric('Heading', f"{t.data['heading']:.0f}" if t.data.get('heading') is not None else None, '°'),
                       metric('Status', nav_status_name(t.data.get('navigation_status')) or None, '')]
        subtitle = ' · '.join(str(v) for v in (lookup.get('Callsign') if lookup.get('Callsign') != t.label else None,
                                                    lookup.get('Type') if t.kind == 'aircraft'
                                                    else ship_type_name(t.data.get('type')),
                                                    t.identifier) if v)
        return dict(mode='history' if history else 'target', key=t.key, title=t.label, kind=t.kind,
                    subtitle=subtitle, source='Simulation' if t.simulated else 'Local RF', metrics=metrics,
                    fields=[dict(label=k, value=v if v is not None else '—') for k, v in fields],
                    watched=self.station.is_watched(t), history=history, stale=row['stale'],
                    phase='' if history else row.get('phase', ''), military=bool(t.traits.get('military')),
                    lastSeen=row['lastSeenText'], squawk=row['squawk'], emergency=row['emergency'])

    @Slot(str, result='QVariantMap')
    def hoverInfo(self, key):
        t = self.lookup_target(key)
        if not t:
            return {}
        row = self.station.target_row(t, self.now())
        parts = []
        if t.kind == 'aircraft' and row['altitude'] is not None:
            parts.append(f"{row['altitude']:,} ft")
        if row['speed'] is not None:
            parts.append(f"{row['speed']:.0f} kn")
        if row['distance'] is not None:
            parts.append(f"{row['distance']:.1f} {self.config['units']}")
        return dict(label=t.label, kind=t.kind, line=' · '.join(parts) or 'No position data',
                    secondary=row['secondary'], stale=row['stale'], watched=row['watched'])

    # ------------------------------------------------------------------ rules
    def _refresh_rules(self):
        self._rules.sync_rows(self.station.rule_rows())  # e.g. pausing a rule keeps the list where it is
        self.rulesChanged.emit()
        self.zonesChanged.emit()  # geofence entries show how many alerts use them

    @Slot(result='QVariantMap')
    def ruleOptions(self):
        return dict(kinds=KINDS, fields=[dict(value=f, label=FIELD_LABELS[f]) for f in FIELDS],
                    conditions=[dict(value=c, label=CONDITION_LABELS[c]) for c in CONDITIONS],
                    extras=[dict(value=k, label=label, unit=unit) for k, (label, unit) in EXTRA_LABELS.items()],
                    zones=[z['name'] for z in self.config['geofences']], units=self.config['units'],
                    phoneConfigured=self.notifier.phone_configured(),
                    quietEnabled=bool(self.config['quiet_hours'].get('enabled')))

    @Slot()
    def newEmergencyRule(self):
        self.openRuleEditor.emit(-1, self.station.emergency_rule_form())

    @Slot()
    def newRule(self):
        self.openRuleEditor.emit(-1, self.station.rule_form())

    @Slot(int)
    def editRule(self, index):
        if 0 <= index < len(self.config['rules']):
            self.openRuleEditor.emit(index, self.station.rule_form(self.config['rules'][index]))

    @Slot(int, 'QVariantMap', result=str)
    def saveRule(self, index, form):
        error = self.station.save_rule(index, dict(form))
        if not error:
            self._refresh_rules()
            self.toast.emit('Alert saved' if index < 0 else 'Alert updated', 'success')
        return error

    @Slot(int)
    def deleteRule(self, index):
        if 0 <= index < len(self.config['rules']):
            name = self.config['rules'][index]['name']
            error = self.station.delete_rule(index)
            self._refresh_rules()
            self.toast.emit(error or f'Deleted alert “{name}”', 'error' if error else 'info')

    @Slot(int, bool)
    def setRuleEnabled(self, index, enabled):
        error = self.station.set_rule_enabled(index, enabled)
        self._refresh_rules()
        if error:
            self.toast.emit(error, 'error')

    @Slot('QVariantMap', result=str)
    def ruleSentence(self, form):
        form = dict(form)
        try:
            threshold = float(form.get('threshold') or 0)
        except (TypeError, ValueError):
            threshold = 0
        if form.get('condition') in DISTANCE_CONDITIONS:
            threshold *= UNIT_KM[self.config['units']]
        form['threshold'] = threshold
        extras = []
        for e in form.get('extra') or []:
            e = dict(e)
            try:
                e['threshold'] = float(e.get('threshold') or 0) * (UNIT_KM[self.config['units']]
                                                                    if e.get('condition') in DISTANCE_EXTRAS else 1)
            except (TypeError, ValueError):
                e['threshold'] = 0
            extras.append(e)
        form['extra'] = extras
        try:
            form['lookahead'] = float(form.get('lookahead') or 5)
        except (TypeError, ValueError):
            form['lookahead'] = 5.0
        return rule_sentence(form, self.config['units'])

    @Slot()
    def alertForSelected(self):
        t = self._current_target()
        if t:
            self.openRuleEditor.emit(-1, self.station.rule_for_target(t))

    @Slot()
    def markAlertsSeen(self):
        if self._unread:
            self._unread = 0
            self.alertsChanged.emit()

    # -------------------------------------------------------------- watchlist
    def _refresh_watch(self):
        self.watchSource.update_rows(self.station.watch_rows())

    @Slot()
    def newWatch(self):
        self.openWatchEditor.emit(-1, dict(kind='aircraft', name='', identifier='', notes=''))

    @Slot(int)
    def editWatch(self, index):
        if 0 <= index < len(self.config['watchlist']):
            self.openWatchEditor.emit(index, dict(self.config['watchlist'][index]))

    @Slot()
    def watchSelected(self):
        t = self._current_target()
        if t:
            self.openWatchEditor.emit(-1, dict(kind=t.kind, name=t.label, identifier=t.identifier, notes=''))

    @Slot(int, 'QVariantMap', result=str)
    def saveWatch(self, index, entry):
        error = self.station.save_watch(index, dict(entry))
        if not error:
            self._refresh_watch()
            self._after_watch_change()
            self.toast.emit('Watchlist updated', 'success')
        return error

    @Slot(int)
    def deleteWatch(self, index):
        error = self.station.delete_watch(index)
        self.watchSource.update_rows(self.station.watch_rows())
        self._after_watch_change()
        if error:
            self.toast.emit(error, 'error')

    def _after_watch_change(self):
        t = self._current_target()
        if t:
            self._details = self._target_details(t, history=self._history_target is not None)
            self.selectionChanged.emit()
        self.refresh()

    @Slot(int)
    def alertForWatch(self, index):
        if 0 <= index < len(self.config['watchlist']):
            self.openRuleEditor.emit(-1, self.station.rule_for_watch(index))

    @Slot()
    def importAircraftCsv(self):
        path, _ = QFileDialog.getOpenFileName(None, 'Import aircraft lookup CSV', '', 'CSV files (*.csv)')
        if not path:
            return
        try:
            n = self.station.import_aircraft(path)
        except (OSError, ValueError, sqlite3.Error, UnicodeDecodeError) as e:
            self.toast.emit(f'Import failed: {e}', 'error')
            return
        self.toast.emit(f'Imported {n:,} ICAO → registration mappings', 'success')

    # -------------------------------------------------------------- geofences
    def _set_zone(self, **values):
        self._zone.update(values)
        self.zoneStateChanged.emit()

    @Slot()
    def beginZone(self):
        self.cancelZone()
        if self.map is None:
            return
        self.requestPage.emit(0)
        self._set_zone(mode='draw', title='Draw geofence', name='', original='',
                       hint='Click the map to place at least three points. Double-click, press Enter or choose '
                            'Finish when done. Right-click removes the last point. Esc cancels.')
        self.map.beginZone()

    def _drawing_changed(self):
        if self._zone['mode'] == 'draw' and self.map is not None and self.map.drawing:
            n = len(self.map.vertices)
            self._set_zone(hint=f'{n} point{"s" if n != 1 else ""} placed · '
                                + ('Finish when ready.' if n >= 3 else f'Place {3 - n} more to make a shape.')
                                + ' Right-click undoes. Esc cancels.')

    @Slot()
    def finishZone(self):
        if self.map is not None and self._zone['mode'] == 'draw':
            if len(self.map.vertices) < 3:
                self._set_zone(hint='Place at least three points first.')
                return
            self.map.finishZone()

    def _zone_drawn(self, points):
        self._pending_zone = [list(p) for p in points]
        self._set_zone(mode='create', title='Name geofence', name='',
                       hint='Give this zone a unique name, then use it in Enters/Leaves geofence alerts.')
        self._push_scene()

    @Slot(str, str)
    def editZone(self, name, mode):
        zone = self.station.zone(name)
        if not zone or mode not in ('rename', 'remove'):
            return
        self.cancelZone()
        self.requestPage.emit(0)
        self._pending_zone = zone['points']
        count = self.station.linked_rules(name)
        if mode == 'rename':
            hint = 'Alerts that use this geofence will follow the new name.'
        else:
            hint = (f'Remove “{name}”? {count} linked alert rule{"s" if count != 1 else ""} will become inactive.'
                    if count else f'Remove “{name}”? Recorded traffic is kept.')
        self._set_zone(mode=mode, title='Rename geofence' if mode == 'rename' else 'Remove geofence', name=name,
                       original=name, hint=hint)
        self.showZone(name)
        self._push_scene()

    @Slot(str)
    def showZone(self, name):
        zone = self.station.zone(name)
        if zone and self.map is not None:
            self.requestPage.emit(0)
            self.map.centerOn(*self.station.zone_center(zone))

    @Slot(str)
    def commitZone(self, name):
        mode, original = self._zone['mode'], self._zone['original']
        if mode == 'create':
            error = self.station.create_zone(name, self._pending_zone)
            message = f'Geofence saved · {name.strip()}'
        elif mode == 'rename':
            error = self.station.rename_zone(original, name)
            message = f'Geofence renamed · {name.strip()}'
        elif mode == 'remove':
            error = self.station.remove_zone(original)
            message = f'Geofence removed · {original}'
        else:
            return
        if error:
            self._set_zone(hint=error)
            return
        self.cancelZone()
        self.zonesChanged.emit()
        self._refresh_rules()
        self.toast.emit(message, 'success')

    @Slot()
    def cancelZone(self):
        if self.map is not None:
            self.map.cancelZone()
        self._pending_zone = []
        self._set_zone(mode='', title='', hint='', name='', original='')
        self._push_scene()

    @Slot(result=bool)
    def escape(self):
        """Esc: cancel geofence work first. Returns True when something was cancelled."""
        if self._zone['mode'] or (self.map is not None and self.map.drawing):
            self.cancelZone()
            return True
        return False

    # ---------------------------------------------------------------- history
    @Slot('QVariantMap')
    def searchHistory(self, filters):
        filters = dict(filters)
        self._history_filters = filters
        category = filters.get('category', 'sightings')
        if category not in HISTORY_CATEGORIES:
            category = 'sightings'
        try:
            start = _parse_time(filters.get('start'), 0)
            end = _parse_time(filters.get('end'), 1e20)
            distance_text = str(filters.get('maxDistance') or '').strip()
            max_distance = None
            if distance_text and category == 'sightings':
                try:
                    max_distance = float(distance_text)
                except ValueError:
                    raise ValueError('Enter a number for maximum distance.')
            rows = self.station.search_history(category, str(filters.get('query', '')), filters.get('kind', 'any'),
                                               start, end, max_distance)
        except ValueError as e:
            self.toast.emit(str(e), 'warning')
            return
        self._history_rows = rows
        self._history_range = (start, end)
        if category == 'sightings':
            mapped = []
            for i, r in enumerate(rows):
                data = json.loads(r['data'])  # once per row (up to 5,000 rows)
                title = data.get('registration') or data.get('name') or data.get('callsign') or r['identifier']
                mapped.append(dict(key=str(i), row=i, title=title if title == r['identifier']
                                   else f"{title} · {r['identifier']}",
                                   kind=r['kind'], first=r['first'], firstText=stamp(r['first']), last=r['last'],
                                   lastText=stamp(r['last']), source='Simulation' if r['simulated'] else 'Local RF',
                                   isSighting=True, category='sighting'))
        else:
            mapped = [dict(key=str(i), row=i, title=r['message'], kind=r['key'].split(':')[0] if r['key'] else '',
                           first=r['time'], firstText=stamp(r['time']), last=r['time'], lastText='',
                           source='Simulation' if r['simulated'] else 'Local', isSighting=False,
                           category=r['category']) for i, r in enumerate(rows)]
        self.historySource.set_rows(mapped)
        self._history_info = (f'{len(rows):,} result{"s" if len(rows) != 1 else ""} (limit 5,000) · times are local'
                              + (' · distances use the home location at recording time' if category == 'sightings' else ''))
        self.historyChanged.emit()

    def _history_row(self, index):
        return self._history_rows[index] if 0 <= index < len(self._history_rows) else None

    @Slot(int)
    def openTrack(self, index):
        r = self._history_row(index)
        if not r or 'data' not in r:
            self.toast.emit('Choose a sighting (not an event) to open its track.', 'info')
            return
        start, end = getattr(self, '_history_range', (0, 1e20))
        track = self.station.track(r['key'], start, end)
        t = self.station.history_target(r)
        self._selected = ''
        self._history_target = t
        self._history_track = track
        details = self._target_details(t, history=True)
        details['title'] = t.label
        details['trackPoints'] = len(track)
        self._details = details
        self.selectionChanged.emit()
        self._push_scene()
        self.requestPage.emit(0)
        if track and self.map is not None:
            self.map.centerOn(track[-1]['lat'], track[-1]['lon'])
        elif not track:
            self.toast.emit('No recorded positions for this sighting in the chosen period.', 'info')

    def _save_rows(self, rows, name, track_name=''):
        if not rows:
            self.toast.emit('Nothing to export yet.', 'info')
            return
        kinds = 'CSV (*.csv);;JSON (*.json)' + (';;Google Earth KML (*.kml)' if track_name else '')
        path, _ = QFileDialog.getSaveFileName(None, 'Export local data', name, kinds)
        if not path:
            return
        try:
            if track_name and path.lower().endswith('.kml'):
                export_kml(path, rows, track_name)
            else:
                self.station.export(path, rows)
        except (OSError, ValueError) as e:
            self.toast.emit(f'Export failed: {e}', 'error')
            return
        self.toast.emit(f'Exported {len(rows):,} rows', 'success')

    @Slot()
    def exportResults(self):
        self._save_rows(self._history_rows, 'AirAlert-history.csv')

    @Slot(int)
    def exportTrack(self, index):
        r = self._history_row(index)
        if not r or 'data' not in r:
            self.toast.emit('Choose a sighting to export its track.', 'info')
            return
        start, end = getattr(self, '_history_range', (0, 1e20))
        label = self.station.history_target(r).label
        self._save_rows(self.station.track(r['key'], start, end), f'AirAlert-track-{r["identifier"]}.csv',
                        track_name=f'{label} · {stamp(r["first"])[:16]}')

    @Slot(result='QVariantMap')
    def maintenanceInfo(self):
        return self.station.maintenance_info()

    @Slot(int)
    def deleteOlderThan(self, days):
        if days < 1:
            return
        self.station.delete_older_than(days)
        self.toast.emit(f'Deleted history older than {days} day{"s" if days != 1 else ""}', 'success')
        if self._history_filters:
            self.searchHistory(self._history_filters)
        self._load_feed()
        self.alertsChanged.emit()

    # ------------------------------------------------------------- statistics
    @Slot(result='QVariantMap')
    def statistics(self):
        stats = self.station.statistics()
        stats['live'] = dict(aircraft=self._counts[0], vessels=self._counts[1], rate=self.station.rate,
                             running=self.station.running, mode=self.station.mode)
        stats['rules'] = sum(r.get('enabled', True) for r in self.config['rules'])
        stats['watch'] = len(self.config['watchlist'])
        stats['zones'] = len(self.config['geofences'])
        return stats

    @Slot(int)
    def setPage(self, index):
        self._page = index
        if index == 1:
            self.markAlertsSeen()
        if index == 3:
            self._refresh_watch()

    # --------------------------------------------------------------- settings
    @Slot(result='QVariantMap')
    def settingsData(self):
        c = self.config
        home = c['home'] or [0, 0]
        return dict(mode=c['mode'], units=c['units'], theme=c['theme'], homeEnabled=bool(c['home']),
                    lat=home[0], lon=home[1], aircraft_device=c['aircraft_device'], marine_device=c['marine_device'],
                    aircraft_seconds=c['aircraft_seconds'], marine_seconds=c['marine_seconds'], gain=str(c['gain']),
                    ppm=c['ppm'], aircraft_ttl=c['aircraft_ttl'], vessel_ttl=c['vessel_ttl'], map_theme=c['map_theme'],
                    tiles=c['tiles'], rings=', '.join(f'{r:g}' for r in c['rings']), trail_minutes=c['trail_minutes'],
                    sample_seconds=c['sample_seconds'], retention_days=c['retention_days'], folder=str(c.folder),
                    setupDone=c['setup_done'], running=self.station.running,
                    show_simulation=c['show_simulation'],
                    close_to_tray=c['close_to_tray'], start_with_windows=startup.is_enabled(),
                    auto_start=c['auto_start'], trayAvailable=self.tray is not None,
                    quietEnabled=c['quiet_hours']['enabled'], quietStart=c['quiet_hours']['start'],
                    quietEnd=c['quiet_hours']['end'],
                    summaryEnabled=c['daily_summary']['enabled'], summaryTime=c['daily_summary']['time'],
                    summaryPhone=c['daily_summary']['phone'], health_warning=c['health_warning'],
                    backupEnabled=c['backup']['enabled'], backupFolder=c['backup']['folder'],
                    backupDays=c['backup']['every_days'], backupKeep=c['backup']['keep'],
                    lookupPhotos=c['online_lookup']['photos'], lookupRoutes=c['online_lookup']['routes'],
                    check_updates=c['check_updates'], phoneMapEnabled=c['phone_map']['enabled'],
                    phoneMapPort=c['phone_map']['port'],
                    **{k: c['phone'].get(k, '') for k in (
                        'ntfy_enabled', 'ntfy_server', 'ntfy_topic', 'ntfy_token', 'pushover_enabled',
                        'pushover_user', 'pushover_token')})

    @Slot('QVariantMap', result=str)
    def saveSettings(self, form):
        form = dict(form)
        try:
            rings = [float(v.strip()) for v in str(form.get('rings', '')).split(',') if v.strip()]
            values = dict(mode=form['mode'], units=form['units'], theme=form['theme'],
                          aircraft_device=str(form['aircraft_device']).strip(),
                          marine_device=str(form['marine_device']).strip(),
                          aircraft_seconds=int(float(form['aircraft_seconds'])),
                          marine_seconds=int(float(form['marine_seconds'])),
                          gain=str(form['gain']).strip().lower() or 'auto', ppm=int(float(form['ppm'])),
                          aircraft_ttl=int(float(form['aircraft_ttl'])), vessel_ttl=int(float(form['vessel_ttl'])),
                          map_theme=form['map_theme'], tiles=bool(form['tiles']), rings=rings,
                          trail_minutes=int(float(form['trail_minutes'])),
                          sample_seconds=int(float(form['sample_seconds'])),
                          retention_days=int(float(form['retention_days'])))
            if values['gain'] != 'auto':
                try:
                    values['gain'] = f"{float(values['gain'].removesuffix('db').strip()):g}"
                except ValueError:
                    raise ValueError('Gain must be "auto" or a number of dB between 0 and 50.')
            values['close_to_tray'] = bool(form.get('close_to_tray', self.config['close_to_tray']))
            values['show_simulation'] = bool(form.get('show_simulation', self.config['show_simulation']))
            if values['mode'] == 'Simulation' and not values['show_simulation']:
                values['mode'] = 'Aircraft'
            values['auto_start'] = bool(form.get('auto_start', self.config['auto_start']))
            quiet = dict(enabled=bool(form.get('quietEnabled', False)),
                         start=str(form.get('quietStart', '22:00')).strip(), end=str(form.get('quietEnd', '07:00')).strip())
            for clock in (quiet['start'], quiet['end']):
                parts = clock.split(':')
                if len(parts) != 2 or not all(x.isdigit() for x in parts) or len(parts[1]) != 2 or \
                        not (0 <= int(parts[0]) <= 23 and 0 <= int(parts[1]) <= 59):
                    raise ValueError('Quiet hours use 24-hour HH:MM times, e.g. 22:00 and 07:00.')
            quiet['start'] = f"{int(quiet['start'].split(':')[0]):02d}:{quiet['start'].split(':')[1]}"
            quiet['end'] = f"{int(quiet['end'].split(':')[0]):02d}:{quiet['end'].split(':')[1]}"
            values['quiet_hours'] = quiet
            phone = dict(self.config['phone'])
            for key in ('ntfy_enabled', 'pushover_enabled'):
                if key in form:
                    phone[key] = bool(form[key])
            for key in ('ntfy_server', 'ntfy_topic', 'ntfy_token', 'pushover_user', 'pushover_token'):
                if key in form:
                    phone[key] = str(form[key] or '').strip()
            if phone['ntfy_enabled'] and not phone['ntfy_topic']:
                raise ValueError('Enter an ntfy topic (or turn ntfy off).')
            if phone['ntfy_enabled'] and not phone['ntfy_server'].startswith(('https://', 'http://')):
                raise ValueError('The ntfy server must start with https:// (e.g. https://ntfy.sh).')
            if phone['pushover_enabled'] and not (phone['pushover_user'] and phone['pushover_token']):
                raise ValueError('Enter both the Pushover user key and API token (or turn Pushover off).')
            values['phone'] = phone
            c = self.config
            report = dict(enabled=bool(form.get('summaryEnabled', c['daily_summary']['enabled'])),
                          time=str(form.get('summaryTime', c['daily_summary']['time'])).strip(),
                          phone=bool(form.get('summaryPhone', c['daily_summary']['phone'])))
            parts = report['time'].split(':')
            if len(parts) != 2 or not all(x.isdigit() for x in parts) or len(parts[1]) != 2 or \
                    not (0 <= int(parts[0]) <= 23 and 0 <= int(parts[1]) <= 59):
                raise ValueError('The daily summary time uses 24-hour HH:MM, e.g. 21:00.')
            report['time'] = f'{int(parts[0]):02d}:{parts[1]}'
            values['daily_summary'] = report
            values['health_warning'] = bool(form.get('health_warning', c['health_warning']))
            saved = dict(enabled=bool(form.get('backupEnabled', c['backup']['enabled'])),
                         folder=str(form.get('backupFolder', c['backup']['folder']) or '').strip(),
                         every_days=int(float(form.get('backupDays', c['backup']['every_days']))),
                         keep=int(float(form.get('backupKeep', c['backup']['keep']))))
            if saved['enabled'] and not saved['folder']:
                raise ValueError('Choose a backup folder (or turn automatic backups off).')
            if not 1 <= saved['every_days'] <= 365 or not 1 <= saved['keep'] <= 50:
                raise ValueError('Back up every 1 to 365 days and keep 1 to 50 backups.')
            values['backup'] = saved
            values['online_lookup'] = dict(photos=bool(form.get('lookupPhotos', c['online_lookup']['photos'])),
                                           routes=bool(form.get('lookupRoutes', c['online_lookup']['routes'])))
            values['check_updates'] = bool(form.get('check_updates', c['check_updates']))
            port = int(float(form.get('phoneMapPort', c['phone_map']['port'])))
            if not 1024 <= port <= 65535:
                raise ValueError('The phone map port must be between 1024 and 65535.')
            values['phone_map'] = dict(c['phone_map'], enabled=bool(form.get('phoneMapEnabled', c['phone_map']['enabled'])),
                                       port=port)
            if form.get('homeEnabled'):
                lat, lon = float(form['lat']), float(form['lon'])
                if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                    raise ValueError('Latitude must be -90…90 and longitude -180…180.')
                values['home'] = [lat, lon]
            else:
                values['home'] = None
            for key, low, high in (('aircraft_seconds', 5, 3600), ('marine_seconds', 5, 3600),
                                   ('aircraft_ttl', 10, 86400), ('vessel_ttl', 10, 86400),
                                   ('sample_seconds', 1, 36500), ('retention_days', 1, 36500),
                                   ('trail_minutes', 0, 1440), ('ppm', -150, 150)):
                if not low <= values[key] <= high:
                    raise ValueError(f'{key.replace("_", " ").capitalize()} must be between {low} and {high}.')
        except (KeyError, TypeError, ValueError) as e:
            return str(e) if isinstance(e, ValueError) else f'Missing or invalid value: {e}'
        if self.station.sim and not values['show_simulation']:
            self.stop()   # simulation mode is being hidden: do not leave it running
        if self.station.running:
            changed = [k for k in RECEIVER_KEYS if _comparable(values.get(k)) != _comparable(self.config[k])]
            if changed:
                return 'Stop monitoring to change the mode or receiver settings (the other settings can change now).'
        old_home = self.config['home']
        error = self.station.save_settings(values)
        if error:
            return error
        wants_startup = bool(form.get('start_with_windows', startup.is_enabled()))
        if wants_startup != startup.is_enabled():
            startup_error = startup.set_enabled(wants_startup)
            if startup_error:
                self.toast.emit(startup_error, 'error')
        self.config.values['start_with_windows'] = startup.is_enabled()
        self.config.save()
        self._settings_applied(old_home)
        return ''

    def _settings_applied(self, old_home):
        self._mode_choice = self.config['mode']
        self.tiles.set_enabled(self.config['tiles'])
        self.settingsChanged.emit()
        self.appearanceChanged.emit()
        self.stateChanged.emit()
        self._refresh_rules()
        if self.config['home'] and self.config['home'] != old_home and self.map is not None:
            self.map.centerOnZoom(self.config['home'][0], self.config['home'][1], 9)
        self.refresh()

    @Slot(float, float, result=str)
    def setHome(self, lat, lon):
        """Set the station (home) location, e.g. from a right-click on the map. Returns '' or an error."""
        try:
            lat, lon = float(lat), float(lon)
        except (TypeError, ValueError):
            return 'That point has no usable coordinates.'
        if not (-90 <= lat <= 90 and -180 <= lon <= 180) or lat != lat or lon != lon:
            return 'That point is outside the map.'
        old_home = self.config['home']
        error = self.station.save_settings(dict(home=[round(lat, 6), round(lon, 6)]))
        if error:
            self.toast.emit(error, 'error')
            return error
        self._settings_applied(old_home)
        self.toast.emit(f'Station location set to {format_coordinate(lat, lon)}', 'success')
        return ''

    @Slot()
    def detectReceivers(self):
        if self._detect_process and self._detect_process.state() != QProcess.ProcessState.NotRunning:
            return
        process = QProcess(self)
        process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self._detect_process = process

        def finished(*_):
            output = bytes(process.readAll()).decode(errors='replace')
            devices = re.findall(r'(?m)^(\d+):\s*(.+?),\s*SN:\s*(\S+)', output)
            if devices:
                text = f'{len(devices)} receiver{"s" if len(devices) != 1 else ""} found'
            elif 'access denied' in output.lower():
                text = ('A USB receiver was found, but Windows denied access. Close other SDR apps (such as '
                        'SDR#), check the WinUSB driver and reconnect the receiver.')
            else:
                text = 'No accessible RTL-SDR receivers detected. Check the USB connection and WinUSB driver.'
                if self.config['show_simulation']:
                    text += ' Simulation works without hardware.'
            self.receiversDetected.emit(text, [dict(index=i, name=n.strip(), serial=s) for i, n, s in devices])

        process.finished.connect(finished)
        process.errorOccurred.connect(
            lambda _: self.receiversDetected.emit('The receiver tool is missing or could not start.', []))
        process.start(str(resource_root() / 'vendor/ais/AIS-catcher.exe'), ['-l'])
        QTimer.singleShot(10000, self, lambda: process.kill() if process.state() != QProcess.ProcessState.NotRunning else None)

    @Slot()
    def locateComputer(self):
        process = QProcess(self)
        self._locator = process

        def done(*_):
            try:
                data = json.loads(bytes(process.readAllStandardOutput()).decode())
                self.locationResolved.emit(True, float(data['Latitude']), float(data['Longitude']),
                                           'Computer location found. Review the coordinates before saving.')
            except (ValueError, KeyError, TypeError):
                self.locationResolved.emit(False, 0.0, 0.0, 'Windows location is unavailable or not permitted. '
                                                            'Enter latitude and longitude manually.')
        process.finished.connect(done)
        process.errorOccurred.connect(
            lambda error: self.locationResolved.emit(False, 0.0, 0.0, 'Windows location could not be queried. '
                                                                     'Enter latitude and longitude manually.')
            if error == QProcess.ProcessError.FailedToStart else None)
        # Reads the Windows location provider only; it does not change permissions or call web services.
        process.start('powershell.exe', ['-NoProfile', '-NonInteractive', '-Command', LOCATION_SCRIPT])

    @Slot()
    def openDataFolder(self):
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.config.folder)))

    # ------------------------------------------------------------------ tray
    def _build_tray(self):
        icon = QIcon(str(resource_root() / 'assets/airalert.ico'))
        self.tray = QSystemTrayIcon(icon, self)
        self.tray.setToolTip('AirAlert')
        menu = QMenu()
        show = QAction('Show AirAlert', menu)
        show.triggered.connect(self.showWindow)
        toggle = QAction('Start monitoring', menu)
        toggle.triggered.connect(self.toggleMonitoring)
        menu.aboutToShow.connect(lambda: toggle.setText('Stop monitoring' if self.station.running
                                                        else 'Start monitoring'))
        quiet = QAction('Snooze all alerts for 1 hour', menu)
        quiet.triggered.connect(lambda: self.wakeAll() if self.station.snooze_summary()['active']
                                else self.snooze('all', '', 60))
        menu.aboutToShow.connect(lambda: quiet.setText('Resume alerts' if self.station.snooze_summary()['active']
                                                       else 'Snooze all alerts for 1 hour'))
        quit_action = QAction('Quit AirAlert', menu)
        quit_action.triggered.connect(self.quitApp)
        for action in (show, toggle, quiet):
            menu.addAction(action)
        menu.addSeparator()
        menu.addAction(quit_action)
        self._tray_menu = menu
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(lambda reason: self.showWindow()
                                    if reason == QSystemTrayIcon.ActivationReason.Trigger else None)
        self.tray.messageClicked.connect(self.showWindow)
        self.tray.show()

    @Slot()
    def showWindow(self):
        if self.window is None:
            return
        try:
            if self.window.visibility() == QWindow.Visibility.Minimized:
                self.window.showNormal()
            else:
                self.window.show()
            self.window.raise_()
            self.window.requestActivate()
        except Exception:
            log.exception('Could not show the window')
        self.refresh()  # the views were not updated while hidden

    @Slot(result=bool)
    def allowClose(self):
        """Window close: hide to the tray (monitoring continues) unless quitting."""
        if self._quitting or not self.config['close_to_tray'] or self.tray is None:
            return True
        if not self._tray_hint_shown:
            self._tray_hint_shown = True
            self.tray.showMessage('AirAlert is still running',
                                  'Alerts keep working in the background. Right-click the tray icon to quit.',
                                  QSystemTrayIcon.MessageIcon.Information, 5000)
        return False

    @Slot()
    def quitApp(self):
        self._quitting = True
        if self.window is not None:
            self.window.close()
        QApplication.quit()

    @Slot(str)
    def testPhone(self, service):
        if not self.notifier.send_phone('AirAlert test', 'Test notification from AirAlert. Phone alerts are working.',
                                        only=service or None):
            self.toast.emit('Turn the service on and fill in its details first (then Save).', 'warning')
        else:
            self.toast.emit(f'Sending a test notification via {service or "your phone services"}\u2026', 'info')

    @Slot()
    def testVoice(self):
        self.notifier.speak('This is AirAlert. Voice alerts are working.')

    @Slot(result=str)
    def generateTopic(self):
        return 'airalert-' + secrets.token_hex(6)

    def shutdown(self):
        self.pump.stop()
        self.render.stop()
        self.station.shutdown()
        if self.tray is not None:
            self.tray.hide()
