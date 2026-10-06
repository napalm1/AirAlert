"""Developer screenshots of the reception check dialog (no receiver needed).

python tools/reception_snap.py [OUTDIR]     (default test-output/snaps-reception)

Runs real checks against recordings (tests/fixtures/modes1.bin and synthetic IQ files)
plus a few simulated states, in Dark and Light, and saves PNGs. Uses a throwaway data
folder inside OUTDIR, never the real one.
"""
import json
import os
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def make_adsb_iq(path, frames):
    """UC8 IQ at 2 MS/s with `frames` ADS-B frames of one aircraft (alternating CPR, so positions decode)."""
    iq = bytearray()
    for i in range(frames):
        frame = '8D40621D58C382D690C8AC2863A7' if i % 2 == 0 else '8D40621D58C386435CC412692AD6'
        pulse = [1 if j in (0, 2, 7, 9) else 0 for j in range(16)]
        for bit in ''.join(f'{int(c, 16):04b}' for c in frame):
            pulse.extend([1, 0] if bit == '1' else [0, 1])
        iq.extend(bytes([127, 127]) * 4096)
        for v in pulse:
            iq.extend(bytes([227 if v else 127, 127]))
    path.write_bytes(bytes(iq))
    return path


def pump(ms):
    from PySide6.QtCore import QEventLoop, QTimer
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def wait_idle(bridge, timeout=90):
    deadline = time.time() + timeout
    while bridge.property('running') and time.time() < deadline:
        pump(50)


def demo_rows(phase_of, numbers):
    from airalert import reception as rc
    rows = []
    for gain, (frames, valid, targets, positions) in numbers.items():
        row = rc.blank_result(gain, '40')
        row.update(frames=frames, valid=valid, targets=targets, positions=positions, phase=phase_of(gain),
                   score=rc.score(valid, targets, positions))
        rows.append(row)
    top = max(r['valid'] for r in rows) or 1
    for r in rows:
        r['share'] = r['valid'] / top
    return rows


DEMO = {'auto': (1480, 1102, 14, 176), '20.7': (690, 655, 8, 90), '31.2': (1395, 1271, 13, 204),
        '38.2': (1760, 1544, 17, 251), '40': (1802, 1530, 16, 247), '45.3': (1905, 1380, 15, 219),
        '48.8': (2010, 1096, 12, 160)}


def main():
    out = Path(sys.argv[1] if len(sys.argv) > 1 else ROOT / 'test-output' / 'snaps-reception').resolve()
    data = out / 'data'
    shutil.rmtree(data, ignore_errors=True)
    data.mkdir(parents=True)
    os.environ['AIRALERT_DATA'] = str(data)
    os.environ['AIRALERT_UPDATE_URL'] = 'off'
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    os.environ.setdefault('QT_QUICK_BACKEND', 'software')
    os.environ.setdefault('QT_QPA_FONTDIR', r'C:\Windows\Fonts')
    (data / 'settings.json').write_text(json.dumps(dict(home=[38.4, -122.8], mode='Aircraft', setup_done=True,
                                                        theme='Dark', gain='40')), 'utf-8')
    import logging
    logging.basicConfig(level=logging.WARNING, stream=sys.stdout, format='%(levelname)s %(name)s: %(message)s')
    from PySide6.QtCore import QObject, QUrl
    from PySide6.QtQml import QQmlComponent
    from airalert import reception as rc
    from airalert.app import build, qml_folder
    app, engine, controller, window = build(['reception-snap'], tray=False, show=True, dialogs=False)
    if window is None:
        raise SystemExit('interface failed to load')
    window.resize(1480, 920)
    pump(300)
    bridge = controller.bridges['reception']
    dialog = window.findChild(QObject, 'receptionDialog')
    if dialog is None:  # not yet part of Main.qml: create it here
        component = QQmlComponent(engine, QUrl.fromLocalFile(str(qml_folder() / 'ReceptionDialog.qml')))
        dialog = component.create()
        if dialog is None:
            raise SystemExit('\n'.join(e.toString() for e in component.errors()))
        dialog.setParent(window.contentItem())
        dialog.setProperty('parent', window.contentItem())
    recordings = out / 'recordings'
    recordings.mkdir(exist_ok=True)
    files = {g: make_adsb_iq(recordings / f'adsb-{n}.cu8', n)
             for g, n in (('auto', 60), ('20.7', 24), ('31.2', 90), ('38.2', 130), ('45.3', 110), ('48.8', 40))}
    files['40'] = (ROOT / 'tests/fixtures/modes1.bin').resolve()
    (recordings / 'quiet.cu8').write_bytes(bytes([127]) * 3_000_000)

    def shot(name, theme):
        controller.setTheme(theme)
        pump(700)
        path = out / f'{name}-{theme.lower()}.png'
        window.grabWindow().save(str(path))
        print('saved', path)

    bridge.open()
    pump(400)
    for theme in ('Dark', 'Light'):
        shot('1-idle', theme)

    # Simulated mid-run state (the real recordings decode in well under a second).
    bridge._running = True
    bridge._run += 1
    bridge._on_event((bridge._run, dict(
        type='progress', progress=0.49, status='Listening at 38.2 dB · gain 4 of 7',
        step='9 s left · 1,210 frames · 1,061 valid · 15 aircraft',
        results=demo_rows(lambda g: 'done' if g in ('auto', '20.7', '31.2') else 'active' if g == '38.2' else 'pending',
                          dict(DEMO, **{'38.2': (1210, 1061, 15, 170), '40': (0, 0, 0, 0), '45.3': (0, 0, 0, 0),
                                        '48.8': (0, 0, 0, 0)})))))
    for theme in ('Dark', 'Light'):
        shot('2-running', theme)
    bridge._running = False

    # Simulated busy-evening result with a recommendation.
    bridge._run += 1
    bridge._running = True
    rows = demo_rows(lambda g: 'done', DEMO)
    best = rc.recommend(rows)
    best['best'] = True
    runner = rc.ReceptionCheck('adsb', dict(controller.config.values), data, gains=list(DEMO))
    runner.results = rows
    bridge._on_event((bridge._run, dict(type='done', results=rows, recommendation=runner._recommendation(best),
                                        error='', message='', cancelled=False, elapsed=161)))
    for theme in ('Dark', 'Light'):
        shot('3-recommendation', theme)

    # Real check against recordings (different recording per gain).
    bridge.infile = files
    bridge.start('adsb', 10)
    wait_idle(bridge)
    for theme in ('Dark', 'Light'):
        shot('4-recordings', theme)

    # Nothing heard at any gain.
    bridge.infile = recordings / 'quiet.cu8'
    bridge.start('adsb', 10)
    wait_idle(bridge)
    for theme in ('Dark', 'Light'):
        shot('5-no-signal', theme)

    # Receiver error (simulated) in marine mode.
    bridge.setMode('ais')
    bridge._run += 1
    bridge._running = True
    rows = [dict(r, phase='skipped', note='skipped') for r in bridge.property('results')]
    bridge._on_event((bridge._run, dict(type='done', results=rows, recommendation=None, error=rc.BUSY, message='',
                                        cancelled=False, elapsed=2)))
    for theme in ('Dark', 'Light'):
        shot('6-error-marine', theme)

    # Monitoring is running.
    bridge.setMode('adsb')
    controller.setMode('Simulation')
    controller.start()
    bridge.start('adsb', 20)
    pump(300)
    shot('7-monitoring', 'Dark')
    controller.stop()
    dialog.close()
    pump(200)
    controller.shutdown()


if __name__ == '__main__':
    main()
