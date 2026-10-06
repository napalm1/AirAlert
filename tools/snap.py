"""Developer screenshot harness: render AirAlert with simulated traffic to PNG files.

python tools/snap.py OUTDIR [--offscreen] [--steps N] then a list of shots, e.g.
    live:Dark:Follow_app  live-sel:Light:Light  scope:Dark:Scope_only  alerts  history  watch  stats  settings
"""
import json
import os
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main():
    out = Path(sys.argv[1]).resolve()
    args = sys.argv[2:]
    if '--offscreen' in args:
        os.environ['QT_QPA_PLATFORM'] = 'offscreen'
        args.remove('--offscreen')
    steps = 90
    if '--steps' in args:
        i = args.index('--steps')
        steps = int(args[i + 1])
        del args[i:i + 2]
    data = out / 'data'
    if data.exists():
        shutil.rmtree(data)
    data.mkdir(parents=True)
    os.environ['AIRALERT_DATA'] = str(data)
    os.environ['AIRALERT_UPDATE_URL'] = 'off'
    settings = dict(home=[38.4, -122.8], units='mi', mode='Simulation', setup_done=True, sample_seconds=1,
                    theme='Dark', map_theme='Follow app', tiles=False,
                    rules=[dict(id='r1', name='Aircraft proximity', enabled=True, kind='aircraft', field='registration',
                                value='', condition='enter', threshold=40.2336, zone='', cooldown=60, sound=False,
                                desktop=False),
                           dict(id='r2', name='Harbor zone', enabled=True, kind='vessel', field='identifier', value='',
                                condition='zone_enter', threshold=0, zone='Harbor', cooldown=120, sound=False,
                                desktop=False)],
                    watchlist=[dict(kind='aircraft', name='Demo Cessna', identifier='N123AB', notes='Club aircraft')],
                    geofences=[dict(name='Harbor', points=[[38.43, -122.87], [38.45, -122.78], [38.39, -122.76],
                                                           [38.37, -122.84]])])
    (data / 'settings.json').write_text(json.dumps(settings), 'utf-8')
    import logging
    logging.basicConfig(level=logging.INFO, stream=sys.stdout, format='%(levelname)s %(name)s: %(message)s')
    from airalert.app import build
    app, engine, controller, window = build([sys.argv[0]], tray=False, show=True, dialogs=False)
    if window is None:
        raise SystemExit('interface failed to load')
    window.setWidth(1480)
    window.setHeight(920)
    from PySide6.QtCore import QEventLoop, QTimer

    def pump(ms):
        loop = QEventLoop()
        QTimer.singleShot(ms, loop.quit)
        loop.exec()

    pump(400)
    controller.start()
    station = controller.station
    base = time.time() - steps * 2
    controller.pump.stop()
    for i in range(steps):
        station.sim_time -= 2
        notices, _ = station.tick(now=base + i * 2)
        for n in notices:
            controller._raise_alert(dict(n, sound=False, desktop=False))
    controller.pump.start()
    controller.refresh()
    pump(600)
    from PySide6.QtCore import QMetaObject, QObject
    popups = {'settings': 'settingsDialog', 'setup': 'settingsDialog', 'rule': 'ruleEditor', 'newrule': 'ruleEditor',
              'watchedit': 'watchEditor', 'maint': 'maintenanceDialog'}
    for shot in args:
        parts = shot.split(':')
        name = parts[0]
        theme = parts[1] if len(parts) > 1 else 'Dark'
        map_theme = parts[2].replace('_', ' ') if len(parts) > 2 else 'Follow app'
        section = parts[3] if len(parts) > 3 else 'general'
        controller.setTheme(theme)
        controller.setMapTheme(map_theme)
        page = {'live': 0, 'live-sel': 0, 'scope': 0, 'zone': 0, 'track': 0, 'alerts': 1, 'history': 2, 'watch': 3,
                'stats': 4, 'maint': 2}.get(name, 0)
        window.setProperty('page', page)
        if name == 'live-sel':
            controller.selectTarget('aircraft:A00001')
        elif name in ('live', 'scope'):
            controller.clearSelection()
        if name == 'history':
            controller.searchHistory({'category': 'sightings'})
        if name == 'track':
            controller.searchHistory({'category': 'sightings'})
            controller.openTrack(0)
        if name == 'zone':
            controller.beginZone()
            for lat, lon in ((38.55, -122.95), (38.6, -122.7), (38.45, -122.62)):
                controller.map.vertices.append((lat, lon))
            controller.map.drawingChanged.emit()
            controller.map.update()
        if name == 'settings':
            controller.openSettings.emit(False, section)
        if name == 'setup':
            controller.openSettings.emit(True, section)
        if name == 'rule':
            controller.editRule(0)
        if name == 'newrule':
            controller.newRule()
        if name == 'watchedit':
            controller.editWatch(0)
        if name == 'maint':
            QMetaObject.invokeMethod(window.findChild(QObject, 'maintenanceDialog'), 'openDialog')
        controller.refresh()
        pump(1200)
        image = window.grabWindow()
        path = out / f'{shot.replace(":", "_")}.png'
        image.save(str(path))
        print('saved', path)
        if name in popups:
            window.findChild(QObject, popups[name]).close()
            pump(300)
        if name == 'zone':
            controller.cancelZone()
    controller.shutdown()


if __name__ == '__main__':
    main()
