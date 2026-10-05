"""Self-test for the packaged executable: AirAlert.exe --self-test OUTPUT_FOLDER.

Runs the real interface with simulated traffic and writes results.json.
Exit code 0 means every check passed.
"""
import json
import logging
import queue
import time
import traceback
from pathlib import Path


class _Capture(logging.Handler):
    def __init__(self):
        super().__init__(logging.WARNING)
        self.records = []

    def emit(self, record):
        self.records.append(record.getMessage())


class _FakeManager:
    def __init__(self):
        self.messages = queue.Queue()
        self.status = queue.Queue()

    def stop(self):
        pass

    def join(self, timeout=None):
        pass

    def is_alive(self):
        return False


def run(folder):
    folder = Path(folder).resolve()
    folder.mkdir(parents=True, exist_ok=True)
    home = [32.0, -118.0]
    (folder / 'settings.json').write_text(json.dumps(dict(
        home=home, mode='Simulation', setup_done=True, sample_seconds=1,
        rules=[dict(id='acceptance', name='N123AB near', kind='aircraft', field='registration', value='N123AB',
                    condition='enter', threshold=40.2336, cooldown=0, sound=False, desktop=False)])), 'utf-8')
    capture = _Capture()
    logging.getLogger('airalert').addHandler(capture)
    from .app import build
    from .core.models import destination
    from .core.receivers import resource_root
    _app, engine, controller, window = build(['AirAlert', '--self-test'], tray=False, show=True, dialogs=False)
    results = {}
    if window is None:
        results['interface'] = 'FAIL: QML did not load: ' + ' | '.join(capture.records[-5:])
        (folder / 'results.json').write_text(json.dumps(results, indent=2), 'utf-8')
        return 1

    from PySide6.QtCore import QEventLoop, QPoint, Qt, QTimer
    from PySide6.QtTest import QTest

    def pump(ms):
        loop = QEventLoop()
        QTimer.singleShot(ms, loop.quit)
        loop.exec()

    def point(lat, lon):
        scene = controller.map.mapToScene(controller.map.screen(lat, lon))
        return QPoint(round(scene.x()), round(scene.y()))

    def check(name, fn):
        try:
            fn()
            results[name] = 'pass'
        except Exception as e:  # noqa: BLE001 - every failure is reported
            results[name] = f'FAIL: {e!r}'
            logging.getLogger('airalert').error('Self-test %s failed\n%s', name, traceback.format_exc())

    window.resize(1400, 880)
    pump(500)

    def interface():
        assert window.property('title') == 'AirAlert'
        assert not [r for r in capture.records if 'QML' in r], capture.records

    def simulation():
        controller.setMode('Simulation')
        controller.start()
        station = controller.station
        base = time.time() - 40
        for i in range(20):
            station.sim_time -= 2
            station.tick(now=base + i * 2)
        controller.refresh()
        assert len(station.store.targets) == 9
        assert controller.trafficModel.rowCount() == 9
        controller.setSearch('N123AB')
        assert controller.trafficModel.rowCount() == 1
        controller.setSearch('')

    def click_select():
        target = controller.station.store.targets['aircraft:A00001']
        controller.map.centerOn(*target.position)
        window.grabWindow()
        QTest.mouseClick(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, point(*target.position))
        pump(80)
        assert controller.property('selectedKey') == 'aircraft:A00001'
        assert controller.property('details')['title'] == 'N123AB'

    def alert_pipeline():
        station = controller.station
        station.sim = None
        station.manager = _FakeManager()
        before = len(station.db.events(category='alert'))
        for km in (80, 30, 30, 80, 30):
            lat, lon = destination(home, km, 90)
            station.manager.messages.put(dict(kind='aircraft', identifier='B00001', registration='N123AB', lat=lat,
                                              lon=lon, simulated=True))
            controller._tick()
        assert len(station.db.events(category='alert')) - before == 2
        station.manager.status.put(('aircraft', 'Receiver disconnected. Retrying.'))
        controller._tick()
        controller.refresh()
        assert 'disconnected' in controller.property('healthText')
        assert controller.alertFeed.rowCount() >= 2

    def history_export():
        controller.searchHistory({'category': 'sightings'})
        assert controller.historyModel.rowCount() >= 9
        rows = controller.station.search_history('sightings')
        controller.station.export(folder / 'export.json', rows)
        controller.station.export(folder / 'export.csv', rows)
        assert len(json.loads((folder / 'export.json').read_text('utf-8'))) == len(rows)
        controller.openTrack(0)
        assert controller.property('details')['mode'] == 'history'
        controller.clearSelection()

    def geofence():
        controller.map.centerOnZoom(home[0], home[1], 10)
        controller.beginZone()
        window.grabWindow()
        for bearing in (0, 120, 240):
            lat, lon = destination(home, 6, bearing)
            QTest.mouseClick(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, point(lat, lon))
        pump(50)
        controller.finishZone()
        controller.commitZone('Self-test zone')
        assert controller.station.zone('Self-test zone') is not None
        controller.editZone('Self-test zone', 'remove')
        controller.commitZone('')
        assert controller.station.zone('Self-test zone') is None

    def views():
        for style in ('Scope only', 'Light', 'Follow app'):
            controller.setMapTheme(style)
            pump(120)
            image = window.grabWindow()
            assert not image.isNull()
            image.save(str(folder / f'view-{style.replace(" ", "-").lower()}.png'))
        controller.setTheme('Light')
        pump(120)
        window.grabWindow().save(str(folder / 'view-light-app.png'))
        controller.setTheme('Dark')

    def smart_alerts():
        station = controller.station
        station.sim = None
        station.running = True
        station.manager = _FakeManager()
        for form in (station.emergency_rule_form(),
                     dict(station.rule_form(), name='Pass overhead', condition='approach', threshold=3, lookahead=10,
                          value='', field='registration', sound=False, desktop=False)):
            assert station.save_rule(-1, dict(form, sound=False, desktop=False, speak=False, phone=False)) == ''
        before = len(station.db.events(category='alert'))
        lat, lon = destination(home, 30, 90)
        station.manager.messages.put(dict(kind='aircraft', identifier='C00001', callsign='TEST77', lat=lat, lon=lon,
                                          altitude=4000, speed=300, heading=270, squawk='7700', simulated=True))
        controller._tick()
        texts = [e['message'] for e in station.db.events(category='alert')][:len(station.db.events(category='alert')) - before]
        assert any('squawk 7700' in t for t in texts), texts
        assert any('passes' in t for t in texts), texts
        controller.refresh()
        row = next(controller.trafficModel.get(i) for i in range(controller.trafficModel.rowCount())
                   if controller.trafficModel.get(i)['key'] == 'aircraft:C00001')
        assert row['emergency'] == 'emergency'

    def notifications():
        from datetime import datetime
        controller.config.values['quiet_hours'] = dict(enabled=True, start='22:00', end='07:00')
        notice = dict(text='t', speech='t', sound=True, desktop=True, speak=True, phone=False)
        assert controller.notifier.deliver(notice, datetime(2026, 1, 1, 3, 0)) == []
        controller.config.values['quiet_hours'] = dict(enabled=False, start='22:00', end='07:00')

    def startup_shortcut():
        import os
        from . import startup
        os.environ['AIRALERT_STARTUP_DIR'] = str(folder / 'Startup')
        assert startup.set_enabled(True) == '' and startup.is_enabled()
        assert startup.set_enabled(False) == '' and not startup.is_enabled()

    def feature_bridges():
        import sqlite3
        from . import airports
        from .aircraftdb import bundled_path
        assert {'insights', 'playback', 'reception', 'phonemap', 'lookup'} <= set(controller.bridges), controller.bridges
        assert not controller.bridges['phonemap'].property('running')   # off until switched on
        assert controller.bridges['lookup'].property('info') == {}       # nothing is looked up online by default
        # Bundled aircraft database answers lookups through the history database.
        snapshot = sqlite3.connect(f'file:{bundled_path()}?mode=ro', uri=True)
        icao, registration = snapshot.execute(
            "SELECT icao, registration FROM aircraft WHERE registration <> '' LIMIT 1").fetchone()
        snapshot.close()
        assert controller.station.db.lookup(icao).get('registration') == registration
        index = airports.load(timeout=60)
        assert index is not None and len(index) > 50000
        # Replay the simulated traffic recorded above, then return to live.
        playback = controller.bridges['playback']
        playback.open(time.time() - 3600, time.time() + 60, True)
        for _ in range(100):
            if not playback.property('loading'):
                break
            pump(100)
        assert playback.property('active'), 'playback did not open'
        playback.seek(time.time() - 5)  # the simulated traffic above was recorded in the last minute
        pump(100)
        assert playback.property('targetCount') > 0, 'no targets at the replay time'
        assert controller.target_provider is not None
        playback.close()
        assert controller.target_provider is None and not playback.property('active')

    def packaged_files():
        root = resource_root()
        for relative in ('vendor/adsb/dump1090.exe', 'vendor/ais/AIS-catcher.exe', 'vendor/ais/rtlsdr.dll',
                         'assets/airalert.ico', 'airalert/qml/Main.qml', 'airalert/web/phone.html',
                         'vendor/aircraft/aircraft.sqlite',
                         'vendor/airports/airports.tsv.gz'):
            assert (root / relative).exists(), relative

    for name, fn in (('interface', interface), ('simulation', simulation), ('click_select', click_select),
                     ('alert_pipeline', alert_pipeline), ('history_export', history_export), ('geofence', geofence),
                     ('views', views), ('smart_alerts', smart_alerts), ('notifications', notifications),
                     ('startup_shortcut', startup_shortcut), ('feature_bridges', feature_bridges),
                     ('packaged_files', packaged_files)):
        check(name, fn)
    controller.station.stop()
    results['qml_warnings'] = [r for r in capture.records if 'QML' in r]
    (folder / 'results.json').write_text(json.dumps(results, indent=2), 'utf-8')
    controller.shutdown()
    window.close()
    window = None
    del engine  # interface first, then the bridges it binds to
    ok = all(v == 'pass' for k, v in results.items() if k != 'qml_warnings') and not results['qml_warnings']
    return 0 if ok else 1
