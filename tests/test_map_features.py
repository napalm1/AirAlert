"""Live-map features: airports, heading lines, coverage, emergencies, layer controls and history playback."""
import gzip
import json
import logging
import math
import os
import time

import pytest

from airalert import airports
from airalert.core.database import Database
from airalert.core.models import Target, destination, distance_bearing
from airalert.playback import MAX_GAP, PlaybackData, auto_speed

HOME = [34.05, -118.25]


class LogCapture(logging.Handler):
    def __init__(self):
        super().__init__(logging.WARNING)
        self.records = []

    def emit(self, record):
        self.records.append(record.getMessage())


def pump(ms):
    from PySide6.QtCore import QEventLoop, QTimer
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def advance(controller, seconds=20, start=None):
    station = controller.station
    base = (time.time() - seconds * 2) if start is None else start
    for i in range(seconds):
        station.sim_time -= 2
        station.tick(now=base + i * 2)
    controller.refresh()
    return base


def scene_point(item, x, y):
    from PySide6.QtCore import QPoint, QPointF
    p = item.mapToScene(QPointF(x, y))
    return QPoint(round(p.x()), round(p.y()))


def objects(window):
    """Every QObject under the window plus every visual item (Repeater delegates have no QObject parent)."""
    from PySide6.QtCore import QObject
    seen, out = set(), []
    stack = [window.contentItem()] + list(window.findChildren(QObject))
    while stack:
        o = stack.pop()
        if id(o) in seen:
            continue
        seen.add(id(o))
        out.append(o)
        if hasattr(o, 'childItems'):
            stack.extend(o.childItems())
    return out


def find(window, predicate):
    return next((o for o in objects(window) if predicate(o)), None)


def center_of(item):
    return scene_point(item, item.width() / 2, item.height() / 2)


@pytest.fixture(scope='module')
def ui(tmp_path_factory):
    folder = tmp_path_factory.mktemp('airalert-map')
    (folder / 'settings.json').write_text(json.dumps(dict(home=HOME, mode='Simulation', setup_done=True,
                                                          sample_seconds=1, theme='Dark')), 'utf-8')
    os.environ['AIRALERT_DATA'] = str(folder)
    capture = LogCapture()
    logging.getLogger('airalert').addHandler(capture)
    from airalert.app import build
    app, engine, controller, window = build(['test'], tray=False, show=True, dialogs=False)
    assert window is not None, capture.records
    window.resize(1400, 880)
    assert airports.load() is not None
    pump(300)
    yield app, controller, window, capture
    controller.bridges['playback'].close()
    controller.shutdown()
    logging.getLogger('airalert').removeHandler(capture)


def mine(capture):
    return [r for r in capture.records if 'QML' in r or 'Map paint failed' in r or 'playback' in r.lower()]


# ------------------------------------------------------------------ airports
def test_airport_index_tiers_codes_and_frequencies():
    index = airports.load()
    assert index is not None and len(index) > 50000
    klax = index.info(index.find('KLAX'))
    assert klax['tier'] == airports.MAJOR and klax['iata'] == 'LAX' and klax['city'] == 'Los Angeles'
    assert [f['kind'] for f in klax['freqs']][:2] == ['TWR', 'ATIS'] and len(klax['freqs']) <= 4
    assert klax['freqs'][0]['mhz'] == '119.800'
    heliports = [i for i in range(len(index)) if index.tiers[i] == airports.HELIPORT]
    assert heliports and all('heli' in index.names[i].lower() for i in heliports[:200])
    assert all(index.iatas[i] for i in range(len(index)) if index.tiers[i] == airports.MAJOR)
    near = index.query(airports.MAJOR, 33.5, 34.5, -118.9, -117.9)
    assert index.find('KLAX') in near and near == sorted(near)
    # Date line: a box from 179E to 179W still finds Fiji-area fields on both sides.
    assert index.query(airports.OTHER, -20, -15, 179, -179) == sorted(index.query(airports.OTHER, -20, -15, 179, -179))
    assert airports.display_code('US-1234', 'ABC') == 'ABC' and airports.display_code('kLax', 'LAX') == 'KLAX'


def test_airport_build_from_ourairports_csv(tmp_path):
    (tmp_path / 'airport-codes.csv').write_text(
        '#ICAO,IATA,Full_name,Continent,Location,Longitude,Latitude\n'
        '"KAAA","AAA","Alpha International Airport","NA","Alpha","-100.5","40.25"\n'
        '"1XX","","Bravo Field","NA","Bravo","-100.4","40.2"\n'
        '"2XX","","Charlie Hospital Heliport","NA","Charlie","-100.3","40.1"\n'
        '"3XX","","Old Field (Closed)","NA","","-100.2","40.1"\n'
        '"4XX","","No Coordinates","NA","","",""\n', 'utf-8')
    (tmp_path / 'airport-frequencies.csv').write_text(
        '"id","airport_ref","airport_ident","type","description","frequency_mhz"\n'
        '1,1,"KAAA","UNIC","UNICOM",122.95\n2,1,"KAAA","TWR","TOWER",118.3\n3,1,"KAAA","MISC","OPS",130.0\n'
        '4,2,"1XX","CTAF","CTAF",122.8\n', 'utf-8')
    out = tmp_path / 'airports.tsv.gz'
    assert airports.build(tmp_path, out) == 3
    with gzip.open(out, 'rt', encoding='utf-8') as f:
        assert f.readline().strip() == airports.FORMAT
    index = airports.AirportIndex(out)
    assert [index.idents[i] for i in range(len(index))] == ['KAAA', '1XX', '2XX']  # rank order = tier order
    assert index.tiers == [airports.MAJOR, airports.OTHER, airports.HELIPORT]
    assert [f['kind'] for f in index.info(0)['freqs']] == ['TWR', 'UNICOM']  # MISC dropped, towers first


# -------------------------------------------------------------- map geometry
def make_target(ident='A00001', lat=34.1, lon=-118.25, now=None, **data):
    now = time.time() if now is None else now
    t = Target('aircraft', ident, now - 60, now, dict(lat=lat, lon=lon, **data))
    t.position_time = now
    return t


def test_emergency_codes_are_normalized():
    from airalert.ui.mapcanvas import emergency_code, emergency_text
    assert emergency_code(make_target(squawk=7700)) == '7700'
    assert emergency_code(make_target(squawk='7600')) == '7600'
    assert emergency_code(make_target(squawk=7500.0)) == '7500'
    assert emergency_code(make_target(squawk='1200')) == '' and emergency_code(make_target()) == ''
    assert emergency_code(make_target(squawk=770)) == ''  # zero-filled to 0770
    vessel = Target('vessel', '123456789', 0, 0, dict(squawk='7700'))
    assert emergency_code(vessel) == ''
    assert emergency_text('7700') == 'SQUAWK 7700 · EMERGENCY' and 'HIJACK' in emergency_text('7500')
    assert 'RADIO FAILURE' in emergency_text('7600')


def test_heading_lines_are_geodesic_and_skip_stale_or_still_targets(ui):
    from airalert.ui.mapcanvas import MapCanvas
    canvas = MapCanvas()
    now = 1_000_000.0
    canvas.set_scene(dict(targets=[], layers={}, heading_minutes=5, now=now))
    t = make_target(now=now, heading=90.0, speed=240.0)
    entry = canvas._heading_state(t, now, 5)
    assert len(entry['geo']) == 6 and entry['geo'][0] == t.position
    d, b = distance_bearing(t.position, entry['geo'][5])
    assert d == pytest.approx(240 * 1.852 / 60 * 5, rel=1e-6) and b == pytest.approx(90, abs=0.2)
    assert entry['geo'][2] == pytest.approx(destination(t.position, 240 * 1.852 / 60 * 2, 90.0))
    assert canvas._heading_state(t, now, 5) is entry  # cached while the target is unchanged
    vessel = Target('vessel', '1', now, now, dict(lat=34.05, lon=-118.25, heading=10.0, course=200.0, speed=10.0))
    vessel.position_time = now
    assert distance_bearing(vessel.position, canvas._heading_state(vessel, now, 2)['geo'][-1])[1] == \
        pytest.approx(200, abs=0.2)  # vessels follow course over ground
    assert canvas._heading_state(make_target(now=now - 120, heading=90.0, speed=200.0), now, 5) is None  # stale
    assert canvas._heading_state(make_target(now=now, heading=90.0), now, 5) is None  # no speed
    assert canvas._heading_state(make_target(now=now, heading=90.0, speed=0.0), now, 5) is None
    # Minute ticks thin out when they would be closer than ~7 px.
    points = [(i * 10.0, 0.0) for i in range(11)]
    assert len(MapCanvas._tick_segments(points, 1.0)) == 10
    assert len(MapCanvas._tick_segments(points, 0.2)) == 2
    assert MapCanvas._tick_segments(points, 0.05) == []


def test_coverage_rose_geometry(ui):
    from airalert.ui.mapcanvas import MapCanvas
    canvas = MapCanvas()
    sectors = [50.0] * 36
    sectors[3] = None
    canvas.set_scene(dict(targets=[], layers={}, coverage=dict(home=HOME, sectors=sectors)))
    geo = canvas._coverage_geo()
    assert geo and tuple(HOME) in geo['geo']  # the empty sector folds back to the station
    ranges = [distance_bearing(HOME, p) for p in geo['geo'] if p != tuple(HOME)]
    assert all(r == pytest.approx(50, rel=1e-6) for r, _ in ranges)
    assert not any(30.001 < b < 39.999 for _, b in ranges)   # the sector edges themselves sit at 30 and 40
    canvas.set_scene(dict(targets=[], layers={}, coverage=dict(home=HOME, sectors=[None] * 36)))
    assert canvas._coverage_geo() is None


def test_incremental_trail_cache_matches_a_fresh_build(ui):
    from airalert.ui.mapcanvas import MapCanvas
    now = 2_000_000.0
    t = make_target(now=now)
    for k in range(300):
        lat, lon = destination(HOME, 20, k * 0.5)
        t.trail.append((now - 900 + k * 2, lat, lon))
    live = MapCanvas()
    live.centerOnZoom(HOME[0], HOME[1], 9)

    def polys(canvas, when):
        canvas.set_scene(dict(targets=[t], layers={}, trail_minutes=10, now=when))
        canvas._trail_deadline = time.perf_counter() + 1
        result = canvas._trail_polys(t, when)
        return [(band, [(p.x(), p.y()) for p in poly]) for band, poly in result[0]], result[1]

    polys(live, now - 300)
    for k in range(300, 420):  # new samples arrive, old ones age out of the window
        lat, lon = destination(HOME, 20, k * 0.5)
        t.trail.append((now - 900 + k * 2, lat, lon))
    fresh = MapCanvas()
    fresh.centerOnZoom(HOME[0], HOME[1], 9)
    assert polys(live, now - 60) == polys(fresh, now - 60)
    bands = polys(fresh, now - 60)[0]
    assert [b for b, _ in bands] == sorted(b for b, _ in bands) and bands[-1][0] == 5
    for (_, a), (_, b) in zip(bands, bands[1:]):
        assert a[-1] == b[0]  # age bands join without gaps


def test_scope_trails_are_exact_azimuthal_projections(ui):
    from airalert.ui.mapcanvas import MapCanvas
    now = 3_000_000.0
    t = make_target(now=now)
    for k in range(200):
        t.trail.append((now - 400 + k * 2, *destination(HOME, 30 + k * 0.2, 40 + k * 0.7)))
    canvas = MapCanvas()
    canvas.setWidth(900)
    canvas.setHeight(700)
    canvas.set_scene(dict(targets=[t], layers={}, trail_minutes=15, map_theme='Scope only', now=now))
    canvas.centerOnZoom(34.0, -118.2, 8)
    polys = canvas._scope_trail_polys(t, now)
    assert [band for band, _ in polys] == [3, 4, 5]  # 400 s of trail fills the newest half of 15 min
    samples = {ts: (lat, lon) for ts, lat, lon in t.trail}
    simp = canvas._trail_poly_cache[t.key]
    for ts, q in zip(simp['ts'], [q for _, poly in polys[:1] for q in poly]):
        d, b = distance_bearing((34.0, -118.2), samples[ts])
        assert q.x() == pytest.approx(d * math.sin(math.radians(b)), abs=1e-6)
        assert q.y() == pytest.approx(-d * math.cos(math.radians(b)), abs=1e-6)


def test_paint_every_layer_in_map_and_scope(ui):
    from PySide6.QtGui import QImage, QPainter
    from airalert.ui.mapcanvas import MapCanvas, PALETTES
    app, controller, window, capture = ui
    before = len(mine(capture))
    now = time.time()
    targets = []
    for i in range(12):
        t = make_target(f'B{i:05X}', *destination(HOME, 5 + i * 3, i * 30), now=now, heading=i * 30.0,
                        speed=150.0 + i, altitude=2000.0 + i * 1500, squawk='7700' if i == 4 else '2000')
        for k in range(30):
            t.trail.append((now - 60 + k * 2, *destination(HOME, 5 + i * 3, i * 30 - 3 + k * 0.1)))
        targets.append(t)
    canvas = MapCanvas()
    canvas.setWidth(1000)
    canvas.setHeight(700)
    layers = dict(trails=True, rings=True, labels=True, airports=True, headings=True, coverage=True)
    for style, theme in (('Follow app', 'Dark'), ('Light', 'Light'), ('Scope only', 'Dark')):
        canvas.set_scene(dict(targets=targets, selected=targets[1].key, watched=set(), home=tuple(HOME),
                              rings=[(8, '5 mi')], units='mi', zones=[], history=[], draft=[], trail_minutes=15,
                              layers=layers, heading_minutes=3, tiles=False, theme=theme, map_theme=style,
                              coverage=dict(home=HOME, sectors=[40.0] * 36), now=None))
        canvas.centerOnZoom(HOME[0], HOME[1], 9)
        image = QImage(1000, 700, QImage.Format.Format_ARGB32_Premultiplied)
        painter = QPainter(image)
        canvas.paint(painter)
        painter.end()
        assert canvas._airport_hits, style  # KLAX and neighbors are drawn
        assert len(canvas._hits) == 12
    assert len(canvas.property('emergencyPoints')) == 1
    assert len(mine(capture)) == before, capture.records
    # The display clock is the scene's 'now' (history playback), not wall time.
    old = make_target(now=now - 10_000)
    canvas.set_scene(dict(targets=[old], layers={}, theme='Dark', map_theme='Dark', now=now - 10_000 + 5))
    assert canvas._target_color(old, canvas._now(), 'Dark').name() != PALETTES['Dark']['stale']
    canvas.set_scene(dict(targets=[old], layers={}, theme='Dark', map_theme='Dark', now=None))
    assert canvas._target_color(old, canvas._now(), 'Dark').name() == PALETTES['Dark']['stale']


# ------------------------------------------------------------------ live UI
def test_interface_loads_without_warnings(ui):
    app, controller, window, capture = ui
    assert controller.bridges.get('playback') is not None
    assert not mine(capture), capture.records


def test_airport_hover_card_and_click_keeps_selection(ui):
    from PySide6.QtCore import QObject, QPoint, Qt
    from PySide6.QtTest import QTest
    app, controller, window, capture = ui
    controller.start()
    advance(controller, 10)
    controller.setLayer('airports', True)
    target = next(iter(controller.station.store.targets.values()))
    controller.selectTarget(target.key)
    item = controller.map
    item.centerOnZoom(33.9425, -118.408, 11)  # KLAX
    window.grabWindow()
    index = airports.get()
    klax = index.find('KLAX')
    hit = next(h for h in item._airport_hits if h[2] == klax)
    assert not item._hit(QPoint(round(hit[0]), round(hit[1])))  # no target on top of the airport
    QTest.mouseMove(window, scene_point(item, hit[0], hit[1]))
    pump(50)
    info = item.property('airportInfo')
    assert info['code'] == 'KLAX' and info['name'].startswith('Los Angeles International')
    card = find(window, lambda o: o.metaObject().className().startswith('AirportCard'))
    assert card is not None and card.property('visible')
    QTest.mouseClick(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                     scene_point(item, hit[0], hit[1]))
    pump(50)
    assert controller.property('selectedKey') == target.key  # an airport click is not a background click
    QTest.mouseMove(window, QPoint(700, 860))
    pump(30)
    assert item.property('airportInfo') == {}
    controller.setLayer('airports', False)
    window.grabWindow()
    assert item._airport_hits == []
    controller.setLayer('airports', True)
    controller.clearSelection()


def test_emergency_targets_pulse(ui):
    app, controller, window, capture = ui
    advance(controller, 3)
    target = controller.station.store.targets['aircraft:A00002']
    controller.map.centerOnZoom(*target.position, 9)
    target.data['squawk'] = '7700'
    controller.push_scene()
    points = controller.map.property('emergencyPoints')
    assert [p['key'] for p in points] == ['aircraft:A00002']
    pulses = [o for o in objects(window) if o.objectName() == 'emergencyPulse']
    assert len(pulses) == 4 and [p.property('visible') for p in pulses].count(True) == 1
    target.data['squawk'] = '1200'
    controller.push_scene()
    assert controller.map.property('emergencyPoints') == []
    assert not any(p.property('visible') for p in pulses)


def open_layers(window):
    from PySide6.QtCore import QMetaObject
    button = find(window, lambda o: o.property('tip') == 'Map layers')
    QMetaObject.invokeMethod(button, 'click')
    pump(250)
    return button


def test_layer_switches_heading_length_and_altitude_band(ui):
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest
    app, controller, window, capture = ui
    window.setProperty('page', 0)
    window.resize(1400, 1040)  # tall enough that the layers popover does not need to scroll
    pump(100)
    button = open_layers(window)
    for key, label in (('airports', 'Airports'), ('headings', 'Heading lines'), ('coverage', 'Coverage')):
        switch = find(window, lambda o: o.property('text') == label and o.property('subtitle') is not None)
        assert switch is not None and switch.property('visible'), label
        before = controller.config['map_layers'][key]
        QTest.mouseClick(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, center_of(switch))
        pump(50)
        assert controller.config['map_layers'][key] is (not before), key
        assert controller.map.scene['layers'][key] is (not before)
    assert controller.config['map_layers']['headings'] is True

    # Heading length: saved when the handle is released.
    slider = find(window, lambda o: o.inherits('QQuickSlider') and o.property('to') == 30)
    handle = slider.property('handle')
    start = center_of(handle)
    QTest.mousePress(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start)
    for step in range(1, 6):
        QTest.mouseMove(window, start + QPoint(step * 16, 0), 5)
    assert controller.config['heading_minutes'] == 2  # not saved while dragging
    QTest.mouseRelease(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start + QPoint(80, 0))
    pump(50)
    minutes = controller.config['heading_minutes']
    assert 2 < minutes <= 30 and controller.property('headingMinutes') == minutes
    assert controller.map.scene['heading_minutes'] == minutes

    # Altitude band: enable, then drag the upper handle; saved on release only.
    band = find(window, lambda o: o.property('text') == 'Only aircraft in a band')
    QTest.mouseClick(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, center_of(band))
    pump(50)
    assert controller.config['altitude_filter'] == dict(enabled=True, min=0, max=50000, include_unknown=True)
    upper = find(window, lambda o: o.objectName() == 'bandUpperHandle')
    start = center_of(upper)
    QTest.mousePress(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start)
    for step in range(1, 8):
        QTest.mouseMove(window, start - QPoint(step * 15, 0), 5)
    assert controller.config['altitude_filter']['max'] == 50000
    QTest.mouseRelease(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start - QPoint(105, 0))
    pump(50)
    band_max = controller.config['altitude_filter']['max']
    assert 10000 < band_max < 50000 and band_max % 500 == 0
    visible = controller.current_targets()
    assert all(t.data.get('altitude') is None or t.data['altitude'] <= band_max
               for t in visible if t.kind == 'aircraft')
    check = find(window, lambda o: o.property('text') == 'Include aircraft without altitude')
    QTest.mouseClick(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, center_of(check))
    pump(50)
    assert controller.config['altitude_filter']['include_unknown'] is False
    controller.setAltitudeFilter(False, 0, 50000, True)
    controller.setHeadingMinutes(2)
    controller.setLayer('headings', False)
    from PySide6.QtCore import QMetaObject
    QMetaObject.invokeMethod(button, 'click')
    window.resize(1400, 880)
    pump(200)
    assert not mine(capture), capture.records


# ------------------------------------------------------------------ playback
def history_db(path, now=10_000.0):
    db = Database(path)
    rows = []
    # Aircraft flying north at ~216 kn with 10 s samples, then a 10-minute gap.
    for i in range(30):
        rows.append(('aircraft:ABC123', now + i * 10, 34.0 + i * 0.01, -118.0, 5000 + i * 10, None, None, 0))
    rows.append(('aircraft:ABC123', now + 900, 34.5, -118.0, 7000, 200, None, 0))
    for i in range(20):
        rows.append(('sim/vessel:990000001', now + 100 + i * 30, 33.5, -118.5 + i * 0.001, None, 8.0, None, 1))
    db.conn.executemany('INSERT INTO positions(key,time,lat,lon,altitude,speed,distance,simulated) '
                        'VALUES(?,?,?,?,?,?,?,?)', rows)
    db.conn.executemany('INSERT INTO sightings VALUES(?,?,?,?,?,?,?,1)', [
        ('aircraft:ABC123', 'aircraft', 'ABC123', now, now + 900,
         json.dumps(dict(callsign='TEST1', heading=270.0, altitude=9999, squawk='7700')), 0),
        ('sim/vessel:990000001', 'vessel', '990000001', now + 100, now + 700,
         json.dumps(dict(name='DEMO', course=5.0, heading=5.0)), 1)])
    db.conn.commit()
    return db


def test_playback_data_positions_trails_and_derived_motion(tmp_path):
    now = 10_000.0
    db = history_db(tmp_path / 'history.sqlite', now)
    data = PlaybackData.load(db.conn, now - 100, now + 2000)
    assert len(data) == 2 and data.rows == 51 and not data.truncated
    assert data.start == now and data.end == now + 900
    assert data.targets_at(now - 1) == []
    shown = data.targets_at(now + 95, trail_minutes=1)
    aircraft = next(t for t in shown if t.kind == 'aircraft')
    assert aircraft.key == 'aircraft:ABC123' and aircraft.label == 'TEST1'
    assert aircraft.position == pytest.approx((34.09, -118.0))  # last sample at or before t
    assert aircraft.position_time == aircraft.last_seen == now + 90 and aircraft.first_seen == now
    assert aircraft.data['altitude'] == 5090 and aircraft.data['squawk'] == '7700'
    assert aircraft.data['heading'] == pytest.approx(0, abs=0.5)  # derived: flying north, not the stored 270
    assert aircraft.data['speed'] == pytest.approx(1.112 / 10 * 3600 / 1.852, rel=0.01)  # derived knots
    assert [p[0] for p in aircraft.trail] == [now + 40 + i * 10 for i in range(6)]  # [t - 60 s, t]
    assert data.targets_at(now + 95, 1)[0] is aircraft  # the same object is updated in place
    vessel = next(t for t in data.targets_at(now + 400) if t.kind == 'vessel')
    assert vessel.simulated and vessel.data['course'] == pytest.approx(90, abs=1) and 'altitude' not in vessel.data
    # Hidden after MAX_GAP without a newer sample, shown again at the next sample.
    assert not [t for t in data.targets_at(now + 290 + MAX_GAP + 1) if t.kind == 'aircraft']
    assert [t for t in data.targets_at(now + 900) if t.kind == 'aircraft']
    whole = next(t for t in data.targets_at(now + 290, trail_minutes=0) if t.kind == 'aircraft')
    assert len(whole.trail) == 30
    assert data.next_sample_after(now + 700) == now + 900
    real_only = PlaybackData.load(db.conn, now - 100, now + 2000, include_simulated=False)
    assert [tr.kind for tr in real_only.tracks] == ['aircraft']
    capped = PlaybackData.load(db.conn, now - 100, now + 2000, max_rows=12)
    assert capped.truncated and capped.rows == 12 and capped.end < now + 900
    assert auto_speed(3600) == 10 and auto_speed(600) == 1 and auto_speed(7 * 86400) == 1200
    db.close()


def test_playback_end_to_end_on_the_live_map(ui):
    from PySide6.QtCore import QObject, Qt
    from PySide6.QtTest import QTest
    app, controller, window, capture = ui
    playback = controller.bridges['playback']
    controller.setMapTheme('Follow app')
    base = advance(controller, 30)
    live_targets = len(controller.station.store.targets)
    ribbon = find(window, lambda o: o.metaObject().className().startswith('AlertRibbon'))
    bar = find(window, lambda o: o.metaObject().className().startswith('PlaybackBar'))
    assert bar is not None and not bar.property('visible')

    playback.openRange('not a date', '', True)
    assert not playback.property('active') and not playback.property('loading')

    window.setProperty('page', 2)
    pump(100)
    replay = find(window, lambda o: o.property('text') == 'Replay period on map')
    assert replay is not None
    history = find(window, lambda o: o.metaObject().className().startswith('HistoryPage'))
    assert history is not None
    QTest.mouseClick(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, center_of(replay))
    playback.wait_loaded()
    pump(100)
    assert playback.property('active') and playback.property('playing')
    assert window.property('page') == 0  # jumps to the live map
    assert controller.target_provider is not None
    assert base - 120 <= playback.property('start') < playback.property('end') <= time.time()
    assert bar.property('visible') and (ribbon is None or not ribbon.property('visible'))
    assert controller.map.scene['now'] == pytest.approx(playback.property('time'))

    # Pause with Space, then seek / step / change speed.
    QTest.keyClick(window, Qt.Key.Key_Space)
    pump(50)
    assert not playback.property('playing')
    middle = (playback.property('start') + playback.property('end')) / 2
    playback.seek(middle)
    assert playback.property('time') == pytest.approx(middle)
    assert controller.now() == pytest.approx(middle) and controller.map.scene['now'] == pytest.approx(middle)
    shown = controller.current_targets()
    assert shown and playback.property('targetCount') == len(controller.target_provider())
    assert all(t.position_time <= middle for t in shown)
    assert all(t.trail[-1][0] <= middle for t in shown if t.trail)
    playback.step(-10)
    assert playback.property('time') == pytest.approx(middle - 10)
    playback.seek(-1)
    assert playback.property('time') == playback.property('start')
    playback.setSpeed(300)
    assert playback.property('speed') == 300
    playback.setSpeed(7)
    assert playback.property('speed') == 300
    assert playback.property('timeText') and playback.property('rangeText').endswith('targets')
    window.grabWindow()
    controller.refresh()
    assert controller.trafficModel.rowCount() == len(controller.current_targets())

    # Playing runs to the end and stops there.
    playback.setSpeed(1200)
    playback.seek(playback.property('end') - 2)
    playback.togglePlay()
    pump(400)
    assert not playback.property('playing') and playback.property('time') == playback.property('end')

    playback.close()
    assert not playback.property('active') and controller.target_provider is None
    assert 'now' not in controller.scene_extras and controller.map.scene['now'] is None
    assert len(controller.current_targets()) == live_targets
    assert not bar.property('visible')
    assert not mine(capture), capture.records
