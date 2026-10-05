"""Aircraft database, statistics rollups and the Statistics / Watchlist interface (insights).

Screenshots with demo data: python tests/test_insights.py OUTDIR [stats:Dark stats:Light watch:Dark ...]
"""
import csv
import http.server
import io
import json
import logging
import os
import random
import sqlite3
import sys
import threading
import time
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from airalert import aircraftdb, insights  # noqa: E402
from airalert.core.database import Database  # noqa: E402
from airalert.core.models import Target, destination, distance_bearing  # noqa: E402

HOME = (38.4, -122.8)
OPENSKY_HEADER = ['icao24', 'registration', 'manufacturericao', 'manufacturername', 'model', 'typecode',
                  'serialnumber', 'linenumber', 'icaoaircrafttype', 'operator', 'operatorcallsign', 'operatoricao',
                  'operatoriata', 'owner', 'testreg', 'registered', 'reguntil', 'status', 'built', 'firstflightdate',
                  'seatconfiguration', 'engines', 'modes', 'adsb', 'acars', 'notes', 'categoryDescription']


# --------------------------------------------------------------------------- helpers
def make_opensky_zip(path, rows, extra=0):
    """A zip laid out like OpenSky's aircraftDatabase.zip. rows: dicts of OpenSky columns."""
    text = io.StringIO()
    writer = csv.DictWriter(text, fieldnames=OPENSKY_HEADER, quoting=csv.QUOTE_ALL, extrasaction='ignore')
    writer.writeheader()
    writer.writerow({})  # OpenSky files start with an empty row
    for row in rows:
        writer.writerow(row)
    for i in range(extra):
        writer.writerow(dict(icao24=f'{0x100000 + i:06x}', registration=f'X-{i:05d}', typecode='C172',
                             icaoaircrafttype='L1P'))
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr('media/data/samples/metadata/aircraftDatabase.csv', text.getvalue())
    return path


def add_position(db, key, t, lat, lon, simulated=False, kind='aircraft', data=None):
    ident = key.split(':', 1)[1]
    target = Target(kind, ident, t, t, dict(data or {}, lat=lat, lon=lon), position_time=t, simulated=simulated)
    db.samples.pop(Database.dbkey(target), None)  # record() thins samples per target; tests add them freely
    db.record(target, distance_bearing(HOME, (lat, lon))[0], sample_seconds=0, new=False)


AIRLINE_MIX = [('B738', 'SWA', 12), ('A320', 'UAL', 7), ('B739', 'ASA', 6), ('E75L', 'SKW', 6), ('A321', 'AAL', 5),
               ('A319', 'DAL', 4), ('B77W', 'UAL', 3), ('B789', 'UAL', 3), ('CRJ9', 'DAL', 3), ('B763', 'FDX', 2),
               ('B752', 'UPS', 2), ('C56X', 'EJA', 2), ('C172', None, 5), ('SR22', None, 3), ('PC12', None, 2),
               ('BE20', None, 1), ('P28A', None, 3), ('R44', None, 1)]


def populate_demo(db, home=HOME, now=None, days=14, seed=7):
    """Realistic received-RF history: daytime-heavy aircraft passes, a terrain shadow to the west,
    vessels on the bay to the south, and per-minute message rates with an outage."""
    now = time.time() if now is None else now
    rnd = random.Random(seed)
    conn = db.conn
    reach = [230 + 70 * abs(((i * 37) % 11) - 5) / 5 for i in range(36)]
    for i in range(25, 32):
        reach[i] = 95 + 12 * (i % 3)  # hills to the west
    pool = [f'{0xA00000 + rnd.randrange(0x3FFFF):06X}' for _ in range(1500)]
    weights = [w for _, _, w in AIRLINE_MIX]
    sightings = {}
    start = now - days * 86400
    t = start
    while t < now:
        hour = time.localtime(t).tm_hour
        busy = 0.15 if hour < 6 else 1.0 if 8 <= hour <= 21 else 0.5
        t += rnd.expovariate(busy * 14 / 3600)
        if t >= now:
            break
        icao = rnd.choice(pool)
        typ, op, _ = rnd.choices(AIRLINE_MIX, weights)[0]
        callsign = f'{op}{rnd.randrange(10, 2999)}' if op else f'N{rnd.randrange(100, 999)}{rnd.choice("ABCDEFGHJK")}'
        bearing = rnd.random() * 360
        far = reach[int(bearing // 10)] * rnd.uniform(0.25, 1.0)
        key = f'aircraft:{icao}'
        for step in range(10):
            lat, lon = destination(home, far * (1 - step * 0.07), bearing + step * 0.8)
            add_position(db, key, t + step * 60, lat, lon, data=dict(type=typ, callsign=callsign))
        first = sightings.get(key, (t, t))[0]
        sightings[key] = (first, t + 540, dict(type=typ, callsign=callsign))
    for v in range(6):
        key = f'vessel:36699{v:04d}'
        for hour in range(0, days * 24, 3):
            bearing, dist = 165 + v * 7 + rnd.uniform(-4, 4), 48 + v * 3 + rnd.uniform(-6, 6)
            lat, lon = destination(home, dist, bearing)
            add_position(db, key, start + hour * 3600 + v * 300, lat, lon, kind='vessel', data=dict(name=f'BAY {v}'))
    for key, (first, last, data) in sightings.items():
        conn.execute('UPDATE sightings SET first=?, last=?, data=? WHERE key=?', (first, last, json.dumps(data), key))
    rows = []
    outage = (now - 3 * 86400 - 6 * 3600, now - 3 * 86400)
    for minute in range(int(start // 60), int(now // 60)):
        ts = minute * 60
        if outage[0] <= ts < outage[1]:
            continue
        hour = time.localtime(ts).tm_hour
        base = 60 if hour < 6 else 420 if 8 <= hour <= 21 else 220
        rate = max(0, base * rnd.uniform(0.7, 1.25))
        rows.append((minute, int(rate * 60), int(base / 25), 3, 0))
    conn.executemany('INSERT OR REPLACE INTO minute_stats VALUES (?,?,?,?,?)', rows)
    conn.commit()


# ------------------------------------------------------------------ aircraft database
def test_descriptions_and_cleaning():
    assert aircraftdb.class_description('L2J') == 'Twin-engine jet airplane'
    assert aircraftdb.class_description('H1T') == 'Single-engine turbine helicopter'
    assert aircraftdb.class_description('L1P') == 'Single-engine piston airplane'
    assert aircraftdb.class_description('') == '' and aircraftdb.class_description('X9Z') == ''
    assert aircraftdb.clean_registration('-UNKNOWN-') == '' and aircraftdb.clean_registration('TC-') == ''
    assert aircraftdb.clean_registration('n1') == 'N1' and aircraftdb.clean_registration('99') == ''
    assert aircraftdb.clean_type('zzzz') == '' and aircraftdb.clean_type(' b738 ') == 'B738'
    assert aircraftdb.describe('B738') == ('737-800', 'Boeing')
    assert aircraftdb.describe('B738', '737-8H4', 'Boeing') == ('737-8H4', 'Boeing')
    assert aircraftdb.describe('XXXX', '', 'Acme', 'L1P') == ('Single-engine piston airplane', 'Acme')
    assert aircraftdb.valid_icao('abc123') == 'ABC123' and aircraftdb.valid_icao('xyz') == ''


def test_lookup_prefers_csv_imports_and_is_cached(tmp_path):
    aircraftdb.write_database(tmp_path / 'aircraft.sqlite', [
        ('ABCDEF', 'N555DB', 'B738', '737-800', 'Boeing', 'Southwest Airlines'),
        ('A00002', None, None, 'Single-engine piston airplane', 'Cessna', None)], dict(source='test'))
    db = Database(tmp_path / 'history.sqlite')
    try:
        assert db.lookup('abcdef') == dict(registration='N555DB', type='B738', model='737-800', manufacturer='Boeing',
                                           operator='Southwest Airlines')
        assert db.lookup('A00002') == dict(model='Single-engine piston airplane', manufacturer='Cessna')
        assert db.lookup('FFFFFF') == {}
        csv_file = tmp_path / 'mine.csv'
        csv_file.write_text('icao24,registration,typecode\nabcdef,N123AB,C172\n')
        assert db.import_aircraft(csv_file) == 1
        assert db.lookup('ABCDEF') == {'registration': 'N123AB', 'type': 'C172'}  # import wins, cache cleared
        started = time.perf_counter()
        for _ in range(2000):
            db.lookup('A00002')
            db.lookup('FFFFFF')
        assert time.perf_counter() - started < 0.2
        aircraftdb.write_database(tmp_path / 'new.tmp', [('A00002', 'N2', None, None, None, None)], {})
        db.close_aircraft()  # release the file so it can be replaced
        aircraftdb.install(tmp_path / 'new.tmp', tmp_path / 'aircraft.sqlite')
        assert db.lookup('A00002') == {'registration': 'N2'}
    finally:
        db.close()


def test_missing_database_is_picked_up_later(tmp_path):
    db = Database(tmp_path / 'history.sqlite')
    try:
        assert db.lookup('ABCDEF') == {}
        aircraftdb.write_database(tmp_path / 'aircraft.sqlite', [('ABCDEF', 'N9', None, None, None, None)], {})
        db._aircraft_checked = None  # the 30 s re-check has elapsed
        assert db.lookup('ABCDEF') == {'registration': 'N9'}
    finally:
        db.close()


def test_build_from_opensky_zip(tmp_path):
    zip_path = make_opensky_zip(tmp_path / 'aircraftDatabase.zip', [
        dict(icao24='aa3487', registration='N757F', manufacturername='Raytheon Aircraft Company', model='A36',
             typecode='BE36', icaoaircrafttype='L1P', owner='Private Person'),
        dict(icao24='3c6444', registration='D-AIBD', manufacturername='Airbus', model='', typecode='A319',
             icaoaircrafttype='L2J', operator='Lufthansa', operatorcallsign='LUFTHANSA'),
        dict(icao24='503c21', registration='LY-KNA', manufacturername='Impulse Aircraft', model='Impulse 100',
             typecode='ZZZZ'),
        dict(icao24='abcdefg', registration='BAD'),
        dict(icao24='a00005', registration='-UNKNOWN-', icaoaircrafttype='H2T')], extra=1200)
    fractions = []
    count = aircraftdb.build_from_opensky(zip_path, tmp_path / 'out.sqlite', progress=fractions.append)
    assert count == 1204 and fractions[-1] == 1.0 and fractions == sorted(fractions)
    conn = sqlite3.connect(tmp_path / 'out.sqlite')
    rows = {r[0]: r[1:] for r in conn.execute('SELECT * FROM aircraft WHERE icao NOT LIKE "1%"')}
    meta = dict(conn.execute('SELECT * FROM meta'))
    conn.close()
    assert rows['AA3487'] == ('N757F', 'BE36', 'A36', 'Raytheon Aircraft Company', None)
    assert rows['3C6444'] == ('D-AIBD', 'A319', 'A319', 'Airbus', 'Lufthansa')  # model from the type table
    assert rows['503C21'] == ('LY-KNA', None, 'Impulse 100', 'Impulse Aircraft', None)
    assert rows['A00005'] == (None, None, 'Twin-engine turbine helicopter', None, None)
    assert meta['count'] == '1204' and meta['source'].startswith('OpenSky') and meta['data_date']
    info = aircraftdb.info(tmp_path / 'out.sqlite')
    assert info['ok'] and info['count'] == 1204
    with pytest.raises(aircraftdb.Cancelled):
        aircraftdb.build_from_opensky(zip_path, tmp_path / 'cancelled.sqlite', cancel=lambda: True)
    too_small = make_opensky_zip(tmp_path / 'small.zip', [dict(icao24='aa3487', registration='N757F')])
    with pytest.raises(ValueError):
        aircraftdb.build_from_opensky(too_small, tmp_path / 'small.sqlite')


def test_bundled_database_is_complete_and_fast():
    path = aircraftdb.bundled_path()
    assert path.is_file(), 'run tools/build_aircraft_db.py'
    info = aircraftdb.info(path)
    assert info['ok'] and info['count'] > 400000 and 'bundled' in info['source'].lower()
    conn = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)
    try:
        plan = ' '.join(r[-1] for r in conn.execute('EXPLAIN QUERY PLAN SELECT * FROM aircraft WHERE icao=?', ('A0B1C2',)))
        assert 'PRIMARY KEY' in plan or 'USING' in plan
        assert conn.execute('SELECT registration, type FROM aircraft WHERE icao=?', ('3C6444',)).fetchone() == ('D-AIBD', 'A319')
        assert conn.execute('SELECT operator FROM aircraft WHERE icao=?', ('3C6444',)).fetchone() == ('Lufthansa',)
        assert conn.execute("SELECT COUNT(*) FROM aircraft WHERE type='ZZZZ' OR registration LIKE '%UNKNOWN%'").fetchone() == (0,)
    finally:
        conn.close()


def test_build_tool_follows_children_and_resolves_conflicts(tmp_path):
    sys.path.insert(0, str(ROOT / 'tools'))
    import build_aircraft_db as tool
    fa = tmp_path / 'db'
    (fa / 'aircraft_types').mkdir(parents=True)
    (fa / 'A.json').write_text(json.dumps({'children': ['A0'], 'BCDEF': {'r': 'N1FA', 't': 'B738'}}))
    (fa / 'A0.json').write_text(json.dumps({'0001': {'t': 'GLF4'}, '0002': {'r': 'N2', 't': 'TBM7'}}))
    (fa / 'aircraft_types' / 'icao_aircraft_types.json').write_text(json.dumps(
        {'B738': {'desc': 'L2J'}, 'GLF4': {'desc': 'L2J'}, 'TBM7': {'desc': 'L1T'}}))
    flightaware = tool.read_flightaware(fa)
    assert set(flightaware) == {'ABCDEF', 'A00001', 'A00002'}
    opensky = {'A00001': ('N1', 'Cessna', 'L2J', ''), 'A00002': ('N2X', 'Socata', 'L2J', 'SPEEDBIRD'),
               'A00003': ('TC-', '', 'L1P', 'AIR CHIEF')}
    records, stats = tool.merge(opensky, flightaware, tool.read_types(fa))
    by_icao = {r[0]: r for r in records}
    assert by_icao['A00001'] == ('A00001', 'N1', 'GLF4', 'G-IV', 'Gulfstream', '')
    # Re-registered address whose class contradicts the stale type code: type dropped, class kept.
    assert by_icao['A00002'] == ('A00002', 'N2X', '', 'Twin-engine jet airplane', 'Socata', 'British Airways')
    assert by_icao['A00003'][1:] == ('', '', 'Single-engine piston airplane', '', '')
    assert by_icao['ABCDEF'][1:4] == ('N1FA', 'B738', '737-800') and stats['type_conflicts'] == 1


# ----------------------------------------------------------------------- statistics
def test_rate_tracker_minutes_and_sparkline():
    tracker = insights.RateTracker(window=600)
    base = 1_800_000_000 - 1_800_000_000 % 60
    rows = []
    for i in range(150):
        running = not 70 <= i < 100
        row = tracker.add(base + i, 10 if running else 0, running, i > 120, 3, 1)
        if row:
            rows.append(row)
    assert rows == [(base // 60, 600, 3, 1, 0), (base // 60 + 1, 300, 3, 1, 0)]
    assert tracker.flush() == (base // 60 + 2, 300, 3, 1, 1)
    points = tracker.sparkline(base + 149)  # 120 five-second bins; the first sample lands in bin 90
    assert len(points) == 120 and points[90] == 10 and points[-1] == 10
    assert all(v is None for v in points[:90]) and all(v is None for v in points[104:110])  # pause = gap


def test_minute_stats_merge_and_retention(tmp_path):
    db = Database(tmp_path / 'history.sqlite')
    try:
        insights.store_minute(db.conn, (100, 50, 2, 1, 0))
        insights.store_minute(db.conn, (100, 25, 4, 0, 1))
        assert tuple(db.conn.execute('SELECT * FROM minute_stats').fetchone()) == (100, 75, 4, 1, 1)
        insights.store_minute(db.conn, (10_000_000, 1, 1, 1, 0))
        db.conn.execute("INSERT INTO hourly_targets VALUES (1, 'aircraft:A'), (200000000, 'aircraft:B')")
        db.conn.execute("INSERT INTO hourly_coverage VALUES (1, 'aircraft', 0, 3, 5.0)")
        db.conn.commit()
        db.maintenance(3600 * 1000)
        assert db.conn.execute('SELECT minute FROM minute_stats').fetchall()[0][0] == 10_000_000
        assert db.conn.execute('SELECT COUNT(*) FROM hourly_targets').fetchone()[0] == 1
        assert db.conn.execute('SELECT COUNT(*) FROM hourly_coverage').fetchone()[0] == 0
    finally:
        db.close()


def test_rollups_match_positions(tmp_path):
    db = Database(tmp_path / 'history.sqlite')
    now = time.time()
    try:
        expected = [None] * 36
        rnd = random.Random(3)
        for i in range(400):
            bearing, km = rnd.random() * 360, rnd.uniform(1, 250)
            lat, lon = destination(HOME, km, bearing)
            add_position(db, f'aircraft:{0xA10000 + i % 40:06X}', now - 3600 * (i % 30), lat, lon)
            d, b = distance_bearing(HOME, (lat, lon))
            sector = int(b // 10) % 36
            expected[sector] = max(expected[sector] or 0, d)
        lat, lon = destination(HOME, 400, 95)   # mid-sector 9 (exactly 90 sits on the 8/9 boundary)
        add_position(db, 'aircraft:A1FFFF', now - 60, lat, lon, simulated=True)
        db.conn.commit()
        assert insights.catch_up(db.conn, HOME, limit=150) > 0  # bounded slices
        insights.catch_up_all(db.conn, HOME)
        assert insights.catch_up(db.conn, HOME) == 0
        cov = insights.coverage(db.conn, now - 40 * 3600, now, False)
        for got, want in zip(cov['aircraft'], expected):
            assert (got is None and want is None) or got == pytest.approx(want, abs=0.02)
        assert cov['vessel'] == [None] * 36
        assert insights.coverage(db.conn, now - 40 * 3600, now, True)['aircraft'][9] == pytest.approx(400, abs=0.1)
        hours = insights.hour_of_day(db.conn, now - 86400, now, False)
        assert sum(hours) <= 40 * 24 and max(hours) >= 1
        # positions cleared (ids restart): rollups start over instead of stalling
        db.conn.execute('DELETE FROM positions')
        db.conn.commit()
        add_position(db, 'aircraft:A20000', now, *destination(HOME, 10, 10))
        db.conn.commit()
        insights.catch_up_all(db.conn, HOME)
        assert db.conn.execute("SELECT COUNT(*) FROM hourly_targets WHERE key='aircraft:A20000'").fetchone()[0] == 1
    finally:
        db.close()


def test_moving_home_rebuilds_coverage(tmp_path):
    db = Database(tmp_path / 'history.sqlite')
    now = time.time()
    try:
        add_position(db, 'aircraft:A00001', now, *destination(HOME, 100, 0))
        db.conn.commit()
        insights.catch_up_all(db.conn, HOME)
        assert insights.coverage(db.conn, now - 3600, now, False)['aircraft'][0] == pytest.approx(100, abs=0.05)
        moved = destination(HOME, 50, 0)
        insights.catch_up_all(db.conn, moved)
        assert insights.coverage(db.conn, now - 3600, now, False)['aircraft'][0] == pytest.approx(50, abs=0.05)
        insights.catch_up_all(db.conn, None)  # no home: coverage cleared, target rollups continue
        assert insights.coverage(db.conn, now - 3600, now, False)['aircraft'] == [None] * 36
    finally:
        db.close()


def test_snapshot_series_and_simulation_filter(tmp_path):
    db = Database(tmp_path / 'history.sqlite')
    now = time.time()
    try:
        populate_demo(db, now=now, days=14)
        for i in range(5):
            add_position(db, f'aircraft:A0000{i}', now - 100, *destination(HOME, 20, i * 50), simulated=True,
                         data=dict(type='ZZZ1', callsign=f'DEMO{i}'))
        insights.store_minute(db.conn, (int(now // 60), 99999, 5, 4, 1))
        db.conn.commit()
        insights.catch_up_all(db.conn, HOME)
        started = time.perf_counter()
        real = insights.snapshot(db.conn, '7d', False, now)
        assert time.perf_counter() - started < 0.3
        assert len(real['hours']) == 24 and sum(real['hours']) > 0
        assert real['hours'][3] < real['hours'][14]  # nights are quiet
        assert len(real['daily']) == 14 and real['daily'][-1]['today'] and all(d['vessels'] for d in real['daily'][:-1])
        assert real['types'][0]['key'] == 'B738' and real['types'][0]['detail'] == 'Boeing 737-800'
        assert real['operators'][0]['key'] in ('SWA', 'UAL') and real['operators'][0]['detail']
        assert all(len(r['key']) == 3 and r['key'].isalpha() for r in real['operators'])
        assert 'ZZZ1' not in {r['key'] for r in real['types']}
        west, east = real['coverage']['aircraft'][27], real['coverage']['aircraft'][9]
        assert west < east  # the terrain shadow
        assert max(v or 0 for v in real['coverage']['vessel']) < 80
        points = real['rate']['points']
        assert len(points) == 169 and any(p['value'] is None for p in points)  # outage gap
        assert real['rate']['peak'] < 99999 / 60
        real_day = insights.snapshot(db.conn, '24h', False, now)
        with_sim = insights.snapshot(db.conn, '24h', True, now)
        assert with_sim['aircraft'] == real_day['aircraft'] + 5
        assert with_sim['rate']['peak'] == pytest.approx(99999 / 60, abs=0.1)
        assert insights.snapshot(db.conn, '30d', False, now)['rate']['points'][0]['value'] is None
    finally:
        db.close()


# ------------------------------------------------------------------------------ UI
class QmlLog(logging.Handler):
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


def wait_for(condition, timeout=20.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if condition():
            return True
        pump(40)
    return condition()


def my_warnings(capture):
    mine = ('StatsPage', 'WatchlistPage', 'AircraftDbCard', 'Chart')
    return [r for r in capture.records if 'QML' in r and any(m in r for m in mine)]


def prepare_folder(folder, theme='Dark', demo=True):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / 'settings.json').write_text(json.dumps(dict(home=list(HOME), mode='Simulation', setup_done=True,
                                                          sample_seconds=1, theme=theme)), 'utf-8')
    if demo:
        db = Database(folder / 'history.sqlite')
        populate_demo(db)
        db.close()


_KEEP = []


@pytest.fixture(scope='module')
def ui(tmp_path_factory):
    folder = tmp_path_factory.mktemp('airalert-insights')
    prepare_folder(folder)
    os.environ['AIRALERT_DATA'] = str(folder)
    capture = QmlLog()
    logging.getLogger('airalert').addHandler(capture)
    from airalert.app import build
    app, engine, controller, window = build(['test'], tray=False, show=True, dialogs=False)
    assert window is not None, capture.records
    window.resize(1480, 920)
    pump(300)
    yield app, controller, window, capture, folder
    controller.bridges['insights'].shutdown()
    controller.shutdown()
    logging.getLogger('airalert').removeHandler(capture)
    _KEEP.append((engine, controller, window))  # never destroyed mid-run (teardown bindings would log warnings)


def test_bundled_database_installed_and_used_for_lookups(ui):
    app, controller, window, capture, folder = ui
    insights_bridge = controller.bridges['insights']
    assert (folder / 'aircraft.sqlite').is_file()
    db = insights_bridge.property('aircraftDb')
    assert db['ok'] and db['count'] > 400000 and db['sourceShort'] == 'Bundled snapshot'
    assert controller.station.db.lookup('3c6444')['type'] == 'A319'


def test_stats_page_charts_render_in_both_themes(ui):
    from PySide6.QtCore import QObject
    app, controller, window, capture, folder = ui
    window.setProperty('page', 4)
    pump(400)
    bridge = controller.bridges['insights']
    stats = bridge.property('stats')
    assert stats['ready'] and stats['rate']['hasData'] and stats['hours']['hasData']
    assert stats['types']['rows'][0]['label'] == 'B738' and stats['coverage']['hasData']
    assert stats['coverage']['aircraft'][9] > stats['coverage']['aircraft'][27]
    for name in ('rateCard', 'hoursCard', 'dailyCard', 'coverageCard', 'typesCard', 'operatorsCard'):
        card = window.findChild(QObject, name)
        assert card is not None and not card.property('empty'), name
    for theme in ('Light', 'Dark'):
        controller.setTheme(theme)
        pump(150)
        assert not window.grabWindow().isNull()
    assert not my_warnings(capture), capture.records


def test_filters_reload_and_publish_coverage(ui):
    app, controller, window, capture, folder = ui
    bridge = controller.bridges['insights']
    window.setProperty('page', 4)
    pushed = []
    original = controller.push_scene
    controller.push_scene = lambda: (pushed.append(dict(controller.scene_extras['coverage'])), original())
    try:
        bridge.setTimeRange('30d')
        assert bridge.property('timeRange') == '30d' and bridge.property('stats')['rangeLabel'] == 'Last 30 days'
        assert len(bridge.property('stats')['rate']['points']) == 181
        assert pushed and len(pushed[-1]['sectors']) == 36 and pushed[-1]['home'] == HOME
        real_max = max(s or 0 for s in pushed[-1]['sectors'])
        controller.start()
        for i in range(10):
            controller.station.sim_time -= 2
            controller.station.tick(now=time.time() - 20 + i * 2)
        bridge.setIncludeSimulation(True)
        assert bridge.property('includeSimulation')
        assert bridge.property('stats')['types']['rows']  # simulated aircraft types now count
        assert max(s or 0 for s in pushed[-1]['sectors']) >= real_max
        bridge.setIncludeSimulation(False)
        bridge.setTimeRange('24h')
    finally:
        controller.push_scene = original
    assert not my_warnings(capture), capture.records


def test_refresh_hook_records_minutes_and_live_rate(ui):
    app, controller, window, capture, folder = ui
    bridge = controller.bridges['insights']
    station = controller.station
    if not station.running:
        controller.start()
    window.setProperty('page', 4)
    base = (int(time.time()) // 60 + 1) * 60
    for i in range(70):
        station.rate = 40
        bridge._on_refresh(base + i)
    row = station.db.conn.execute('SELECT * FROM minute_stats WHERE minute=?', (base // 60,)).fetchone()
    assert row['messages'] == 60 * 40 and row['simulated'] == 1 and row['aircraft'] == 5
    live = bridge.property('live')
    assert live['running'] and live['simulated'] and live['points'][-1] == 40 and len(live['points']) == 120
    assert not my_warnings(capture), capture.records


class _Server:
    """Local HTTP server standing in for OpenSky (no internet access in tests)."""

    def __init__(self, folder, slow=False):
        root = str(folder)

        class Handler(http.server.SimpleHTTPRequestHandler):
            def __init__(self, *a, **k):
                super().__init__(*a, directory=root, **k)

            def log_message(self, *args):
                pass

            def do_GET(self):
                if slow and self.path.endswith('slow.zip'):
                    self.send_response(200)
                    self.send_header('Content-Length', str(50 * 1024 * 1024))
                    self.end_headers()
                    try:
                        for _ in range(500):
                            self.wfile.write(b'\0' * 65536)
                            time.sleep(0.02)
                    except OSError:
                        pass
                    return
                super().do_GET()

        self.httpd = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.url = f'http://127.0.0.1:{self.httpd.server_address[1]}/'
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


def test_update_from_opensky_downloads_builds_and_swaps(ui, tmp_path):
    app, controller, window, capture, folder = ui
    bridge = controller.bridges['insights']
    window.setProperty('page', 3)
    pump(200)
    make_opensky_zip(tmp_path / 'aircraftDatabase.zip', [
        dict(icao24='c0ffee', registration='C-FFEE', manufacturername='Airbus', model='A220-300', typecode='BCS3',
             operator='Air Canada')], extra=1500)
    server = _Server(tmp_path, slow=True)
    try:
        original_count = bridge.property('aircraftDb')['count']
        assert controller.station.db.lookup('C0FFEE') == {}
        # 1) an HTTP error keeps the current database
        bridge.url = server.url + 'missing.zip'
        bridge.updateAircraftDb()
        assert wait_for(lambda: bridge.property('updateState') == 'error')
        assert '404' in bridge.property('updateText') and bridge.property('aircraftDb')['count'] == original_count
        # 2) cancelling a download leaves nothing behind
        bridge.url = server.url + 'slow.zip'
        bridge.updateAircraftDb()
        assert wait_for(lambda: bridge.property('updateProgress') > 0.01)
        assert bridge.property('updateBusy')
        bridge.cancelAircraftUpdate()
        assert wait_for(lambda: bridge.property('updateState') == 'idle')
        assert 'cancelled' in bridge.property('updateText')
        assert not list(folder.glob('aircraftDatabase.zip*'))
        # 3) a full update swaps the database in place
        bridge.url = server.url + 'aircraftDatabase.zip'
        bridge.updateAircraftDb()
        assert wait_for(lambda: bridge.property('updateState') in ('done', 'error'), 60)
        assert bridge.property('updateState') == 'done', bridge.property('updateText')
        db = bridge.property('aircraftDb')
        assert db['count'] == 1501 and db['sourceShort'] == 'OpenSky Network' and db['dataDate']
        assert controller.station.db.lookup('C0FFEE') == dict(registration='C-FFEE', type='BCS3', model='A220-300',
                                                               manufacturer='Airbus', operator='Air Canada')
        assert not list(folder.glob('aircraftDatabase.zip*')) and not (folder / 'aircraft.sqlite.tmp').exists()
        assert not window.grabWindow().isNull()
    finally:
        server.close()
    assert not my_warnings(capture), capture.records


@pytest.mark.skipif(not os.environ.get('AIRALERT_OPENSKY_ZIP'), reason='set AIRALERT_OPENSKY_ZIP to a real download')
def test_real_opensky_file_builds(tmp_path):
    count = aircraftdb.build_from_opensky(os.environ['AIRALERT_OPENSKY_ZIP'], tmp_path / 'real.sqlite')
    assert count > 400000
    conn = sqlite3.connect(tmp_path / 'real.sqlite')
    assert conn.execute("SELECT type FROM aircraft WHERE icao='A0B1C2'").fetchone() == ('R44',)
    conn.close()


# ----------------------------------------------------------------- screenshot helper
def snapshots(out, shots):
    """Render the Statistics and Watchlist pages with demo data (developer aid, not a test)."""
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    os.environ.setdefault('QT_QUICK_BACKEND', 'software')
    os.environ.setdefault('QT_QPA_FONTDIR', r'C:\Windows\Fonts')
    import shutil
    out = Path(out).resolve()
    data = out / 'data'
    if data.exists():
        shutil.rmtree(data)
    prepare_folder(data, demo=not any('empty' in s for s in shots))
    os.environ['AIRALERT_DATA'] = str(data)
    logging.basicConfig(level=logging.WARNING, stream=sys.stdout, format='%(levelname)s %(name)s: %(message)s')
    from PySide6.QtCore import QObject, QPoint, QPointF
    from PySide6.QtTest import QTest
    from airalert.app import build
    app, engine, controller, window = build(['snap'], tray=False, show=True, dialogs=False)
    window.setWidth(1480)
    window.setHeight(920)
    pump(300)
    controller.start()
    bridge = controller.bridges['insights']
    now = time.time()
    rnd = random.Random(5)
    for i in range(600):
        bridge.tracker.add(now - 600 + i, int(260 + 90 * __import__('math').sin(i / 40) + rnd.uniform(-40, 40)),
                           True, True, 5, 4)
    for i in range(20):
        controller.station.sim_time -= 2
        controller.station.tick(now=now - 40 + i * 2)
    controller.refresh()
    for shot in shots:
        parts = shot.split(':')
        name, theme = parts[0], parts[1] if len(parts) > 1 else 'Dark'
        scroll = parts[2] if len(parts) > 2 else ''
        controller.setTheme(theme)
        window.setProperty('page', {'stats': 4, 'watch': 3}.get(name, 4))
        pump(500)
        bridge.setIncludeSimulation('sim' in scroll)
        bridge.setTimeRange('7d' if '7d' in scroll else '30d' if '30d' in scroll else '24h')
        flick = window.findChild(QObject, 'statsFlick')
        if flick is not None:
            content = flick.property('contentHeight') - flick.property('height')
            flick.setProperty('contentY', 0 if 'top' in scroll or not scroll else
                              content if 'bottom' in scroll else content * 0.45)
        if name == 'watch' and 'busy' in scroll:
            bridge._set_update('downloading', 0.42, 'Downloading 10.4 of 24.7 MB')
        pump(600)
        for obj in ('typesCard', 'coverageCard'):
            card = window.findChild(QObject, obj)
            if card is not None:
                card.setProperty('showTable', 'table' in scroll)
        if 'hover' in scroll:
            for obj, fx, fy in (('coverageCard', 0.42, 0.32), ('typesCard', 0.55, 0.3)):
                card = window.findChild(QObject, obj)
                p = card.mapToScene(QPointF(card.property('width') * fx, card.property('height') * fy))
                QTest.mouseMove(window, QPoint(int(p.x()), int(p.y())))
                pump(150)
        image = window.grabWindow()
        path = out / f'{shot.replace(":", "_")}.png'
        image.save(str(path))
        print('saved', path)
        if name == 'watch' and 'busy' in scroll:
            bridge._set_update('idle', 0, '')
    bridge.shutdown()
    controller.shutdown()


if __name__ == '__main__':
    snapshots(sys.argv[1], sys.argv[2:] or ['stats:Dark', 'stats:Light', 'watch:Dark'])
