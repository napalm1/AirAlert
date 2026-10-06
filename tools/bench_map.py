"""Paint-time benchmark for the live map.

100 targets with 15-minute trails (three squawking emergencies) in map and scope
styles with every layer on: airports, heading lines, coverage, labels, rings.
Also measures what the airport layer alone adds at busy views.

    .venv\\Scripts\\python.exe tools\\bench_map.py
"""
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

MAP_BUDGET_MS = 35.0
AIRPORT_BUDGET_MS = 3.0


def main():
    from airalert.app import _qt_dll_search_path
    _qt_dll_search_path()
    from PySide6.QtGui import QGuiApplication, QImage, QPainter
    app = QGuiApplication(sys.argv)
    from airalert import airports
    from airalert.core.models import Target, destination
    from airalert.ui.mapcanvas import MapCanvas
    started = time.perf_counter()
    if airports.load() is None:
        raise SystemExit('airport data missing: run python -m airalert.airports')
    print(f'airport data loaded in {(time.perf_counter() - started) * 1000:.0f} ms (background thread in the app)')
    home = (34.05, -118.25)
    now = time.time()
    targets = []
    for i in range(100):
        t = Target('aircraft' if i < 90 else 'vessel', f'A{i:05X}', now - 900, now)
        for k in range(450):
            lat, lon = destination(home, 10 + (i % 40) * 1.5, (i * 37 + k * 0.4) % 360)
            t.trail.append((now - 900 + k * 2, lat, lon))
        t.data.update(lat=lat, lon=lon, altitude=1000 + i * 350, heading=(i * 37 + 180) % 360, speed=120 + i * 4,
                      callsign=f'BENCH{i}', squawk='7700' if i in (5, 40, 77) else '1200')
        if t.kind == 'vessel':
            t.data['course'] = t.data['heading']
        t.position_time = now
        targets.append(t)
    coverage = dict(home=home, sectors=[80 + (i * 17) % 90 if i % 7 else None for i in range(36)])
    canvas = MapCanvas()
    canvas.setWidth(1600)
    canvas.setHeight(900)
    image = QImage(1600, 900, QImage.Format.Format_ARGB32_Premultiplied)

    def frame():
        painter = QPainter(image)
        start = time.perf_counter()
        canvas.paint(painter)
        elapsed = time.perf_counter() - start
        painter.end()
        return elapsed * 1000

    def scene(style, heading_minutes=5, layers=None, items=targets):
        return dict(targets=items, selected=items[3].key if items else '', watched={'a00002'}, home=home,
                    rings=[(8, '5 mi'), (16, '10 mi'), (40, '25 mi')], units='mi', zones=[], history=[], draft=[],
                    trail_minutes=15, heading_minutes=heading_minutes, coverage=coverage, now=None,
                    layers=layers or dict(trails=True, rings=True, labels=True, airports=True, headings=True,
                                          coverage=True),
                    tiles=False, theme='Dark', map_theme=style)

    worst = 0.0
    for label, minutes in (('all layers', 5), ('all layers, 30 min headings', 30)):
        canvas.set_scene(scene('Follow app', minutes))
        canvas.centerOnZoom(home[0], home[1], 9)
        first = frame()
        pans = []
        for _ in range(12):
            canvas._lon += 0.002
            pans.append(frame())
        refresh = []
        for _ in range(3):  # a new scene object: the once-a-second refresh
            canvas.set_scene(scene('Follow app', minutes))
            refresh.append(frame())
        worst = max(worst, max(pans), max(refresh))
        print(f'Map, {label:28s} first {first:6.1f} ms   pan avg {sum(pans) / len(pans):5.1f} / max {max(pans):5.1f} ms'
              f'   refresh avg {sum(refresh) / len(refresh):5.1f} ms')

    # The airport layer alone at busy views (no targets): with minus without.
    for name, lat, lon, zoom in (('NE US majors', 40.2, -76.0, 6.2), ('Los Angeles', 34.0, -118.2, 9),
                                 ('LA heliports', 34.05, -118.3, 11.5)):
        costs, drawn = {}, 0
        for on in (True, False):
            canvas.set_scene(scene('Follow app', layers=dict(rings=False, airports=on), items=[]))
            canvas.centerOnZoom(lat, lon, zoom)
            frame()
            samples = []
            for _ in range(10):
                canvas._lon += 0.001
                samples.append(frame())
            costs[on] = sum(samples) / len(samples)
            drawn = drawn or len(canvas._airport_hits)
        print(f'Airports, {name:13s} zoom {zoom:<4} {drawn:3d} drawn, adds {costs[True] - costs[False]:4.1f} ms '
              f'(budget {AIRPORT_BUDGET_MS:.0f} ms)')

    canvas.set_scene(scene('Scope only'))
    canvas.centerOnZoom(home[0], home[1], 9)
    first = frame()
    pans = []
    for _ in range(8):
        canvas._lon += 0.002
        canvas._scope_cache = None
        pans.append(frame())
    refresh = []
    for _ in range(4):  # the once-a-second rebuild with the scope center unchanged
        canvas.set_scene(scene('Scope only'))
        refresh.append(frame())
    print(f'Scope, all layers{"":18s} first {first:6.1f} ms   pan/rebuild avg {sum(pans) / len(pans):6.1f} ms'
          f'   refresh avg {sum(refresh) / len(refresh):5.1f} ms')
    sweeps = [frame() for _ in range(10)]
    print(f'{"":35s} sweep-only frame avg {sum(sweeps) / len(sweeps):6.1f} ms')
    print(f'Worst map frame {worst:.1f} ms | budget {MAP_BUDGET_MS:.0f} ms | '
          f'{"OK" if worst <= MAP_BUDGET_MS else "OVER BUDGET"}')
    del app


if __name__ == '__main__':
    main()
