"""The speed optimizations must not change any answer: faster queries are checked against brute force."""
import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

from airalert.core.config import Config
from airalert.core.database import Database
from airalert.core.models import UNIT_KM
from airalert.engine import Station

ROOT = Path(__file__).resolve().parent.parent
HOME = [32.0, -118.0]


def sighting(db, kind, ident, simulated=False, registration=None, first=1000.0, last=2000.0, encounters=1, **data):
    key = ('sim/' if simulated else '') + f'{kind}:{ident}'
    if registration:
        data['registration'] = registration
    db.conn.execute('INSERT INTO sightings VALUES (?,?,?,?,?,?,?,?)',
                    (key, kind, ident, first, last, json.dumps(data), int(simulated), encounters))
    return key


def position(db, key, distance, simulated=False, t=1500.0):
    db.conn.execute('INSERT INTO positions(key,time,lat,lon,distance,simulated) VALUES (?,?,?,?,?,?)',
                    (key, t, 32.0, -118.0, distance, int(simulated)))


@pytest.fixture
def db(tmp_path):
    database = Database(tmp_path / 'history.sqlite')
    yield database
    database.close()


@pytest.fixture
def station(tmp_path):
    config = Config(tmp_path)
    config.values.update(home=HOME, mode='Simulation', setup_done=True)
    config.save()
    s = Station(config)
    yield s
    s.shutdown()


# ---------------------------------------------------------------- watchlist lookup
def test_sightings_of_matches_identifier_or_registration_across_real_and_simulated(db):
    sighting(db, 'aircraft', 'ABC123', registration='N123AB', last=3000, encounters=2)
    sighting(db, 'aircraft', 'ABC123', simulated=True, registration='N123AB', last=5000, encounters=4)
    sighting(db, 'aircraft', 'DEF456', registration='n123ab', last=4000, encounters=1)   # same registration
    sighting(db, 'aircraft', 'FFF999', registration='N999ZZ', last=9000, encounters=7)   # someone else
    sighting(db, 'vessel', 'ABC123', last=8000, encounters=3)                             # same id, other kind
    db.conn.commit()
    assert db.sightings_of('aircraft', 'abc123') == (5000, 6)          # by ICAO, either case
    assert db.sightings_of('aircraft', 'N123AB') == (5000, 7)          # by registration, either case
    assert db.sightings_of('aircraft', ' n123ab ') == (5000, 7)
    assert db.sightings_of('vessel', 'ABC123') == (8000, 3)
    assert db.sightings_of('aircraft', 'NOPE') == (0, 0)
    assert db.sightings_of('vessel', 'N123AB') == (0, 0)


def test_sightings_of_uses_indexes_not_a_table_scan(db):
    plan = ' '.join(str(tuple(r)) for r in db.conn.execute(
        '''EXPLAIN QUERY PLAN SELECT MAX(last), SUM(encounters) FROM sightings WHERE kind=? AND (key IN (?, ?)
           OR json_extract(data, '$.registration') = ? COLLATE NOCASE)''', ('aircraft', 'a', 'b', 'c')))
    assert 'sightings_registration' in plan and 'SCAN' not in plan.replace('SCAN sightings USING', ''), plan


# ------------------------------------------------------------------ history queries
def test_history_and_event_filters_with_and_without_text(db):
    sighting(db, 'aircraft', 'ABC123', registration='N123AB', last=3000, callsign='UAL1')
    sighting(db, 'aircraft', 'DEF456', last=2000, callsign='DAL2')
    sighting(db, 'vessel', '366999712', last=1000, name='FERRY')
    for i, (category, text) in enumerate((('alert', 'Rule fired · N123AB'), ('detection', 'DEF456 first detected'),
                                           ('alert', 'Other alert'), ('receiver', 'Receiver up'))):
        db.event(category, 'aircraft:ABC123', text, now=100 + i)
    db.conn.commit()
    assert [r['identifier'] for r in db.history()] == ['ABC123', 'DEF456', '366999712']   # newest first
    assert [r['identifier'] for r in db.history('ual')] == ['ABC123']                      # text in the JSON
    assert [r['identifier'] for r in db.history('def')] == ['DEF456']                      # text in the id
    assert [r['identifier'] for r in db.history(kind='vessel')] == ['366999712']
    assert [r['identifier'] for r in db.history(start=2500)] == ['ABC123']
    assert len(db.events()) == 4
    assert [e['message'] for e in db.events(category='alert')] == ['Other alert', 'Rule fired · N123AB']
    assert [e['message'] for e in db.events('n123ab', 'alert')] == ['Rule fired · N123AB']
    assert [e['message'] for e in db.events('first detected')] == ['DEF456 first detected']
    assert db.events(start=200) == []


# ---------------------------------------------------------------- maintenance
def test_vacuum_runs_only_when_it_gives_space_back(tmp_path):
    path = tmp_path / 'history.sqlite'
    db = Database(path)
    assert not db.worth_vacuuming()
    with db.conn:
        db.conn.executemany('INSERT INTO events(time,category,key,message,simulated) VALUES (?,?,?,?,?)',
                            [(float(i), 'detection', 'aircraft:A', 'x' * 400, 0) for i in range(60000)])
    db.conn.execute('PRAGMA wal_checkpoint(TRUNCATE)')
    big = path.stat().st_size
    assert big > 20 * 1024 ** 2
    db.maintenance(1e9, vacuum=False)                # everything deleted, file not rewritten
    assert path.stat().st_size == big and db.worth_vacuuming()
    db.maintenance(1e9)                              # 'auto': now worth it
    assert path.stat().st_size < big / 10 and not db.worth_vacuuming()
    db.close()


def test_startup_maintenance_skips_vacuum_but_delete_older_than_forces_it(station, monkeypatch):
    calls = []
    real = station.db.maintenance
    monkeypatch.setattr(station.db, 'maintenance', lambda cutoff, vacuum='auto': (calls.append(vacuum),
                                                                                  real(cutoff, vacuum))[1])
    station.delete_older_than(30)
    assert calls == [True]


def test_connection_uses_wal_with_normal_sync(db):
    assert db.conn.execute('PRAGMA journal_mode').fetchone()[0] == 'wal'
    assert db.conn.execute('PRAGMA synchronous').fetchone()[0] == 1     # NORMAL


# ---------------------------------------------------------------- statistics
def test_statistics_match_a_brute_force_count(station):
    db = station.db
    real_a = sighting(db, 'aircraft', 'A00001', last=5000)
    real_a2 = sighting(db, 'aircraft', 'A00002', last=6000)
    real_v = sighting(db, 'vessel', '366999712', last=7000)
    sim_a = sighting(db, 'aircraft', 'A00003', simulated=True, last=8000)
    for key, distance, simulated in ((real_a, 40.0, False), (real_a2, 90.0, False), (real_a2, 60.0, False),
                                     (real_v, 12.5, False), (sim_a, 200.0, True)):
        position(db, key, distance, simulated)
    now = time.time()
    for kind, key, simulated, n in (('aircraft', real_a, 0, 2), ('vessel', real_v, 0, 1), ('aircraft', sim_a, 1, 3)):
        for i in range(n):
            db.conn.execute('INSERT INTO events(time,category,key,message,simulated) VALUES (?,?,?,?,?)',
                            (now - i, 'alert', f'{kind}:X{i}', 'alert', simulated))
    db.conn.execute('INSERT INTO events(time,category,key,message,simulated) VALUES (?,?,?,?,?)',
                    (now, 'alert', '', 'no target', 0))                 # counted in neither kind
    db.conn.execute('INSERT INTO events(time,category,key,message,simulated) VALUES (?,?,?,?,?)',
                    (now, 'detection', 'aircraft:A00001', 'first', 0))   # not an alert
    db.conn.commit()
    units = station.config['units']
    stats = station.statistics()
    rows = {(r['source'], r['kind']): r for r in stats['rows']}
    assert rows['Local RF', 'aircraft']['unique'] == 2 and rows['Local RF', 'aircraft']['alerts'] == 2
    assert rows['Local RF', 'aircraft']['maxDistance'] == f'{90.0 / UNIT_KM[units]:.1f} {units}'
    assert rows['Local RF', 'vessel']['unique'] == 1 and rows['Local RF', 'vessel']['alerts'] == 1
    assert rows['Local RF', 'vessel']['maxDistance'] == f'{12.5 / UNIT_KM[units]:.1f} {units}'
    assert rows['Simulation', 'aircraft']['unique'] == 1 and rows['Simulation', 'aircraft']['alerts'] == 3
    assert rows['Simulation', 'aircraft']['maxDistance'] == f'{200.0 / UNIT_KM[units]:.1f} {units}'
    assert rows['Simulation', 'vessel'] == dict(source='Simulation', kind='vessel', unique=0, maxDistance='Unknown',
                                                last='Never', alerts=0)
    assert rows['Local RF', 'aircraft']['last'] != 'Never'


def test_farthest_range_is_cached_and_refreshed(station):
    db = station.db
    key = sighting(db, 'aircraft', 'A00001', last=5000)
    position(db, key, 10.0)
    db.conn.commit()
    assert station.statistics()['rows'][0]['maxDistance'].startswith(f'{10.0 / UNIT_KM["mi"]:.1f}')
    position(db, key, 50.0)
    db.conn.commit()
    assert station.statistics()['rows'][0]['maxDistance'].startswith(f'{10.0 / UNIT_KM["mi"]:.1f}')   # cached
    station._distance_cache = None                     # e.g. after deleting old history, or 60 s later
    assert station.statistics()['rows'][0]['maxDistance'].startswith(f'{50.0 / UNIT_KM["mi"]:.1f}')
    sighting(db, 'vessel', '366999712', last=6000)     # a new kind appearing refreshes immediately
    db.conn.commit()
    assert station.statistics()['rows'][1]['unique'] == 1


# ------------------------------------------------------- endless animations
# Qt redraws the whole window at 60 frames a second while ANY animation runs: one decorative endless pulse
# cost ~15% of a CPU core on an idle app. Endless animations must be deliberate, so each is listed here.
ENDLESS_ANIMATIONS = {
    'TopBar.qml': 1,          # the status pill's heartbeat: a 0.6 s ping every 4 s (has a PauseAnimation)
    'LivePage.qml': 1,        # rings around aircraft squawking 7500/7600/7700: worth the redraws
    'ReceptionDialog.qml': 2,  # while a reception check runs (modal, temporary)
    'PlaybackBar.qml': 1,     # while history loads (temporary)
}


def test_endless_animations_are_deliberate():
    qml = ROOT / 'airalert' / 'qml'
    found = {p.name: p.read_text('utf-8').count('Animation.Infinite') for p in qml.glob('*.qml')}
    found = {name: n for name, n in found.items() if n}
    assert found == ENDLESS_ANIMATIONS, (
        'An endless QML animation was added or removed. Each one keeps Qt redrawing the window at 60 fps; '
        'prefer a finite loop or a heartbeat with a PauseAnimation, then update ENDLESS_ANIMATIONS.')
    heartbeat = (qml / 'TopBar.qml').read_text('utf-8')
    assert 'PauseAnimation { duration: 3400 }' in heartbeat
    assert 'Animation.Infinite' not in (qml / 'SelectionPulse.qml').read_text('utf-8')


# ------------------------------------------------------------------ lazy imports
def test_decoder_libraries_load_only_when_a_parser_is_created():
    code = ('import sys; sys.path.insert(0, %r);'
            'import airalert.engine, airalert.reception, airalert.core.receivers, airalert.core.parsers;'
            "print(any(m.split('.')[0] in ('pyais', 'pyModeS') for m in sys.modules));"
            'from airalert.core.parsers import AISParser, ADSBParser; AISParser(); ADSBParser();'
            "print(all(m in sys.modules for m in ('pyais', 'pyModeS')))") % str(ROOT)
    out = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, timeout=60)
    assert out.stdout.split() == ['False', 'True'], out.stdout + out.stderr
