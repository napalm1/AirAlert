"""Phone map bridge, exposed to QML as ``phonemap``: runs airalert.phoneweb.PhoneServer when enabled in Settings
and hands it a JSON snapshot of the live traffic once a second (from the interface thread)."""
import json
import logging

from PySide6.QtCore import QCoreApplication, QObject, Qt, Property, Signal, Slot

from .. import __version__
from ..core.models import UNIT_KM
from ..core.receivers import resource_root
from ..phoneweb import PhoneServer, local_address

log = logging.getLogger(__name__)
TRAIL_POINTS = 24


class PhoneMap(QObject):
    changed = Signal()
    _paired = Signal(object)   # from a request thread: the new list of session hashes

    def __init__(self, controller):
        super().__init__(controller)
        self.controller = controller
        try:
            page = (resource_root() / 'airalert' / 'web' / 'phone.html').read_bytes()
        except OSError:
            log.exception('The phone map page is missing')
            page = b'<h1>AirAlert</h1><p>The phone map page is missing. Reinstall AirAlert.</p>'
        self.server = PhoneServer(page, controller.config['phone_map'].get('sessions', []),
                                  on_paired=self._paired.emit)
        self._error = ''
        self.host = '0.0.0.0'   # every network interface; tests use 127.0.0.1
        self._paired.connect(self._save_sessions, Qt.ConnectionType.QueuedConnection)
        controller.refresh_hooks.append(self._publish)
        controller.settingsChanged.connect(self.apply)
        app = QCoreApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self.shutdown)
        self.apply()

    # -------------------------------------------------------------- properties
    def _url(self):
        if not self.server.running:
            return ''
        return f'http://{local_address() or "this-computer"}:{self.server.port}'

    running = Property(bool, lambda self: self.server.running, notify=changed)
    url = Property(str, _url, notify=changed)
    code = Property(str, lambda self: self.server.code if self.server.running else '', notify=changed)
    devices = Property(int, lambda self: len(self.server.sessions), notify=changed)
    error = Property(str, lambda self: self._error, notify=changed)

    # ------------------------------------------------------------------- slots
    @Slot()
    def apply(self):
        """Start, stop or move the server to match the saved settings."""
        settings = self.controller.config['phone_map']
        wanted, port = bool(settings.get('enabled')), int(settings.get('port', 8765))
        self._error = ''
        if not wanted:
            self.server.stop()
        elif not self.server.running or self.server.port != port:
            try:
                self.server.start(port, self.host)
                self._publish(0)
            except OSError as e:
                log.warning('Phone map could not start on port %s: %s', port, e)
                self._error = f'Port {port} could not be opened (another program may be using it). Choose another port.'
        self.changed.emit()

    @Slot()
    def forgetDevices(self):
        self.server.forget_devices()
        self._save_sessions([])

    @Slot()
    def shutdown(self):
        self.server.stop()

    # --------------------------------------------------------------- internals
    def _save_sessions(self, sessions):
        config = self.controller.config
        config.values['phone_map'] = dict(config['phone_map'], sessions=list(sessions))
        try:
            config.save()
        except (OSError, ValueError):
            log.exception('Could not save the paired devices')
        self.changed.emit()

    def snapshot(self, now=None):
        controller = self.controller
        station, config = controller.station, controller.config
        units = config['units']
        ids = station.watched_ids()
        targets = []
        for t in station.store.targets.values():
            row = station.target_row(t, now, ids)
            position = t.position
            trail = list(t.trail)[-TRAIL_POINTS * 4::4] if position else []
            targets.append(dict(k=row['key'], l=row['label'], kind=row['kind'], sub=row['detail'],
                                lat=round(position[0], 5) if position else None,
                                lon=round(position[1], 5) if position else None,
                                alt=row['altitude'], spd=row['speed'], hdg=row['heading'], dist=row['distance'],
                                brg=row['bearing'], stale=row['stale'], emg=row['emergency'], sq=row['squawk'],
                                ph=row['phase'], sim=row['simulated'],
                                trail=[[round(lat, 5), round(lon, 5)] for _, lat, lon in trail]))
        aircraft = sum(t['kind'] == 'aircraft' for t in targets)
        return dict(version=__version__, running=station.running,
                    source='Simulation' if station.sim else 'Live', rate=station.rate, units=units,
                    home=list(config['home']) if config['home'] else None, tiles=bool(config['tiles']),
                    rings=[dict(km=r * UNIT_KM[units], label=f'{r:g} {units}') for r in config['rings']],
                    zones=[dict(name=z['name'], points=z['points']) for z in config['geofences']],
                    counts=dict(aircraft=aircraft, vessels=len(targets) - aircraft), targets=targets,
                    alerts=[dict(text=r['text'], time=r['timeText'], day=r['dayText'], key=r['target'])
                            for r in controller.alertFeed.rows[:20]])

    def _publish(self, now):
        if self.server.running:
            self.server.set_state(json.dumps(self.snapshot(), separators=(',', ':')).encode('utf-8'))


def create(controller):
    return PhoneMap(controller)
