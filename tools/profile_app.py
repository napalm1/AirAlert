"""Speed profile of the app around the map: startup, the engine's per-message work and the once-a-second refresh.

    .venv\\Scripts\\python.exe tools\\profile_app.py            # timings
    .venv\\Scripts\\python.exe tools\\profile_app.py --profile  # plus the hottest functions of each phase

Everything runs against a throw-away data folder, with synthetic traffic (no receiver needed).
"""
import cProfile
import io
import json
import os
import pstats
import random
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('QT_QUICK_BACKEND', 'software')
PROFILE = '--profile' in sys.argv
HOME = [34.05, -118.25]
TARGETS = 300


def timed(label, fn, repeat=1):
    started = time.perf_counter()
    result = None
    for _ in range(repeat):
        result = fn()
    ms = (time.perf_counter() - started) * 1000 / repeat
    print(f'{label:52s} {ms:9.2f} ms')
    return result


def hot(label, fn, repeat=1, top=12):
    """Run fn under cProfile and print the top functions by own time."""
    if not PROFILE:
        return timed(label, fn, repeat)
    profiler = cProfile.Profile()
    profiler.enable()
    result = timed(label, fn, repeat)
    profiler.disable()
    out = io.StringIO()
    pstats.Stats(profiler, stream=out).sort_stats('tottime').print_stats(top)
    print('\n'.join(line for line in out.getvalue().splitlines() if line.strip())[:2600] + '\n')
    return result


def main():
    folder = Path(tempfile.mkdtemp(prefix='airalert-profile-'))
    (folder / 'settings.json').write_text(json.dumps(dict(home=HOME, mode='Simulation', setup_done=True,
                                                          sample_seconds=1, theme='Dark')), 'utf-8')
    os.environ['AIRALERT_DATA'] = str(folder)
    os.environ['AIRALERT_UPDATE_URL'] = 'off'
    t0 = time.perf_counter()
    from airalert.app import build
    print(f'{"import airalert.app":52s} {(time.perf_counter() - t0) * 1000:9.2f} ms')
    app, engine, controller, window = timed('build(): Station + Controller + QML + first show',
                                            lambda: build(['profile'], tray=False, show=True, dialogs=False))
    assert window is not None
    window.resize(1400, 880)
    from PySide6.QtCore import QEventLoop, QTimer

    def pump(ms):
        loop = QEventLoop()
        QTimer.singleShot(ms, loop.quit)
        loop.exec()
    timed('first frames (pump 300 ms)', lambda: pump(300))
    controller.start()
    station = controller.station
    from airalert.core.models import destination
    rnd = random.Random(7)
    station.sim = None

    class Feed:
        def __init__(self):
            import queue
            self.messages, self.status = queue.Queue(), queue.Queue()

        def stop(self):
            pass

        def join(self, timeout=None):
            pass

        def is_alive(self):
            return False
    station.manager = Feed()
    station.running = True
    base = time.time() - 600
    state = {}
    for i in range(TARGETS):
        state[i] = dict(bearing=rnd.uniform(0, 360), dist=rnd.uniform(5, 180), track=rnd.uniform(0, 360),
                        alt=rnd.uniform(500, 40000))

    def batch(step, per_target=3):
        now = base + step * 2
        for _ in range(per_target):
            for i, s in state.items():
                s['dist'] += rnd.uniform(-0.3, 0.3)
                lat, lon = destination(HOME, max(1, s['dist']), s['bearing'])
                station.manager.messages.put(dict(kind='aircraft', identifier=f'A{i:05X}', lat=lat, lon=lon,
                                                  altitude=s['alt'], speed=300, heading=s['track'],
                                                  callsign=f'PRF{i}', squawk='1200', _received=now))
        return station.tick(now=now)

    print(f'\n--- {TARGETS} aircraft, {TARGETS * 3} messages per tick (times are per tick / per call)')
    step = iter(range(10 ** 6))

    def one_tick():
        return batch(next(step))
    hot('tick() warm-up: 60 ticks incl. feeding the queue', lambda: [one_tick() for _ in range(60)], 1)
    timed('tick() steady state incl. feeding the queue', one_tick, 20)
    controller.refresh()
    hot('controller.refresh() (rows + counts + scene)', controller.refresh, 20)
    hot('controller._second() (rate + refresh + hooks)', controller._second, 20)
    keys = list(station.store.targets)[:5]
    controller.selectTarget(keys[0])
    hot('refresh() with a selected target', controller.refresh, 20)
    hot('station.target_row x300', lambda: [station.target_row(t) for t in station.store.targets.values()], 5)
    hot('window.grabWindow() (full render)', window.grabWindow, 5)
    print('\n--- pages')
    for name, page in (('Alerts', 1), ('History', 2), ('Watchlist', 3), ('Statistics', 4), ('Live map', 0)):
        timed(f'show page {name} + settle', lambda page=page: (window.setProperty('page', page), pump(250)), 1)
    controller.stop()
    controller.shutdown()
    window = None
    del engine


if __name__ == '__main__':
    main()
