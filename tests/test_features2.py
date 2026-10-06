"""Snooze, rarity / military / circling alerts, flight phase, per-rule history, daily summary, station records,
reception health, backups, KML export, update checks, online lookups and the phone map."""
import http.client
import json
import logging
import os
import queue
import socket
import sqlite3
import threading
import time
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from airalert import backup, insights, phase, summary, traits, updates
from airalert.core.config import Config
from airalert.core.database import Database, export_kml
from airalert.core.models import Target, destination
from airalert.engine import Station, rule_sentence
from airalert.phoneweb import PhoneServer, is_private, token_hash

HOME = [32.0, -118.0]


class FakeManager:
    def __init__(self):
        self.messages = queue.Queue()
        self.status = queue.Queue()

    def stop(self):
        pass

    def join(self, timeout=None):
        pass

    def is_alive(self):
        return False


@pytest.fixture
def station(tmp_path):
    config = Config(tmp_path)
    config.values.update(home=HOME, mode='Simulation', setup_done=True, sample_seconds=1)
    config.save()
    s = Station(config)
    yield s
    s.shutdown()


def feed(station, messages, now):
    station.manager = station.manager if isinstance(station.manager, FakeManager) else FakeManager()
    station.running = True
    station.sim = None
    for m in messages:
        station.manager.messages.put(dict(m))
    return station.tick(now=now)[0]


def plane(ident, km=20, bearing=90, **extra):
    lat, lon = destination(HOME, km, bearing)
    return dict(kind='aircraft', identifier=ident, lat=lat, lon=lon, **extra)


def rule(station, condition, **extra):
    form = dict(station.rule_form(), name=condition, kind='aircraft', value='', condition=condition, cooldown=0,
                sound=False, desktop=False, **extra)
    assert station.save_rule(-1, form) == ''
    return station.config['rules'][-1]['id']


# ------------------------------------------------------------------------- snooze
def test_snoozed_alerts_are_recorded_but_flagged(station):
    rid = rule(station, 'first')
    assert station.snooze('rule', rid, time.time() + 3600) == ''
    notices = feed(station, [plane('A00001')], 1000)
    assert len(notices) == 1 and notices[0]['snoozed'] is True and notices[0]['rule_id'] == rid
    assert len(station.db.events(category='alert')) == 1                      # still in History
    assert station.rule_rows()[0]['snoozedUntil'] != ''
    assert station.snooze('rule', rid, 0) == ''                                # wake
    assert feed(station, [plane('A00002')], 1001)[0]['snoozed'] is False
    assert station.snooze('target', 'aircraft:A00003', time.time() + 60) == ''
    assert feed(station, [plane('A00003')], 1002)[0]['snoozed'] is True
    assert feed(station, [plane('A00004')], 1003)[0]['snoozed'] is False
    assert station.snooze('all', '', time.time() + 60) == ''
    assert feed(station, [plane('A00005')], 1004)[0]['snoozed'] is True
    info = station.snooze_summary()
    assert info['active'] and info['all'] and info['targets'][0]['label'] == 'A00003'
    assert Config(station.config.folder)['snooze']['targets']                 # survives a restart
    assert station.snooze('rule', '', time.time() + 60) != ''


def test_expired_snoozes_are_pruned(station):
    station.config.values['snooze'] = dict(all=time.time() - 5, rules={'x': time.time() - 5, 'y': time.time() + 500},
                                           targets={'aircraft:A': time.time() - 1})
    assert not station.is_snoozed('x', 'aircraft:A')
    assert station.is_snoozed('y', 'aircraft:B')
    assert station.prune_snoozes() is True
    assert station.config['snooze'] == dict(all=0, rules={'y': station.config['snooze']['rules']['y']}, targets={})
    assert station.prune_snoozes() is False


# ------------------------------------------------------------- rarity and military
def test_first_and_rare_type_alerts(station):
    first = rule(station, 'first_type')
    rare = rule(station, 'rare_type', threshold=3)

    def fired(ident, kind, now):
        return {n['rule_id']: n['text'] for n in feed(station, [plane(ident, type=kind)], now)}
    a = fired('A00001', 'B748', 1000)
    assert first in a and 'first B748 seen here' in a[first]
    assert rare in a and 'seen 0 times in 30 days' in a[rare]
    b = fired('A00002', 'B748', 1010)
    assert first not in b and rare in b and 'seen 1 time in 30 days' in b[rare]
    assert set(fired('A00003', 'B748', 1020)) == {rare}        # two earlier sightings: still under three
    assert fired('A00004', 'B748', 1030) == {}                 # three earlier sightings: no longer rare
    assert first in fired('A00005', 'A388', 1040)
    assert 'never seen at this station' in rule_sentence(dict(kind='aircraft', condition='first_type'), 'mi')
    assert 'fewer than 3 times' in station.rule_rows()[1]['sentence']


def test_first_type_remembers_earlier_sessions(tmp_path):
    config = Config(tmp_path)
    config.values.update(home=HOME, setup_done=True, sample_seconds=1)
    config.save()
    s = Station(config)
    rule(s, 'first_type')
    assert feed(s, [plane('A00001', type='C172')], time.time() - 50)
    s.shutdown()
    s = Station(Config(tmp_path))
    assert feed(s, [plane('A00009', type='C172')], time.time()) == []      # seen in the earlier session
    assert len(feed(s, [plane('A00010', type='PC12')], time.time())) == 1
    s.shutdown()


def test_first_aircraft_operator_and_military(station):
    new_aircraft = rule(station, 'first_aircraft')
    operator = rule(station, 'first_operator')
    military = rule(station, 'military')
    got = {n['rule_id']: n['text'] for n in feed(station, [plane('A00001', callsign='UAL12')], 1000)}
    assert 'first time seen here' in got[new_aircraft] and 'first United Airlines flight seen here' in got[operator]
    assert military not in got
    again = {n['rule_id'] for n in feed(station, [plane('A00002', callsign='UAL99')], 1010)}
    assert again == {new_aircraft}                              # the airline is known now
    got = {n['rule_id']: n['text'] for n in feed(station, [plane('AE1234', callsign='RCH401')], 1020)}
    assert military in got and got[military].endswith('military')
    # The same aircraft coming back later is not "first" any more.
    station.store.expire(10 ** 9)
    station.alerts.states.clear()
    assert new_aircraft not in {n['rule_id'] for n in feed(station, [plane('A00001')], 5000)}


def test_military_recognition():
    assert traits.is_military('AE1234') and traits.is_military('adf7c8') and traits.is_military('43C001')
    assert not traits.is_military('A00001') and not traits.is_military('nonsense')
    assert traits.is_military('A12345', 'United States Air Force') and not traits.is_military('A12345', 'United Airlines')
    assert traits.airline_prefix('ual123 ') == 'UAL' and traits.airline_prefix('N123AB') == ''


def test_trait_rules_cost_nothing_when_unused(station, monkeypatch):
    calls = []
    monkeypatch.setattr(station.db, 'type_history', lambda *a: calls.append(a) or (0, 0))
    monkeypatch.setattr(station.db, 'operator_seen', lambda *a: calls.append(a) or False)
    rule(station, 'enter', threshold=50)
    feed(station, [plane('A00001', type='B738', callsign='UAL1')], 1000)
    assert calls == []


# --------------------------------------------------------------- circling and phase
def circle(ident, turns=1.5, radius_km=2.0, start=1000.0, step_s=8, step_deg=10, **extra):
    center = destination(HOME, 15, 45)
    out = []
    for i in range(int(turns * 360 / step_deg) + 1):
        lat, lon = destination(center, radius_km, (i * step_deg) % 360)
        out.append((start + i * step_s, dict(kind='aircraft', identifier=ident, lat=lat, lon=lon,
                                             heading=(i * step_deg + 90) % 360, **extra)))
    return out


def test_circling_alert_fires_once_for_an_orbit_and_not_for_a_straight_line(station):
    rid = rule(station, 'circling')
    fired = []
    for when, message in circle('A00001', altitude=1500, speed=90, vertical_speed=0):
        fired += feed(station, [message], when)
    assert [n['rule_id'] for n in fired] == [rid] and fired[0]['text'].endswith('circling')
    target = station.store.targets['aircraft:A00001']
    assert phase.flight_phase(target, target.last_seen)['code'] == 'circling'
    straight = []
    for i in range(60):
        lat, lon = destination(HOME, 5 + i * 0.8, 90)
        straight += feed(station, [dict(kind='aircraft', identifier='A00002', lat=lat, lon=lon, heading=90)], 3000 + i * 8)
    assert straight == []


class OneAirport:
    """An index with a single airport, like airalert.airports.AirportIndex."""
    def __init__(self, lat, lon, code='KTST'):
        self.lats, self.lons, self.codes = [lat], [lon], [code]

    def nearest(self, lat, lon, radius_km=30.0, tiers=None):
        from airalert.core.models import distance_bearing
        km = distance_bearing((lat, lon), (self.lats[0], self.lons[0]))[0]
        return (0, km) if km <= radius_km else None


def aircraft_at(km, bearing, now=1000.0, **data):
    lat, lon = destination(HOME, km, bearing)
    t = Target('aircraft', 'A00001', now - 60, now, dict(lat=lat, lon=lon, **data), position_time=now)
    return t


def test_flight_phase_tags():
    index, now = OneAirport(*HOME), 1000.0
    assert phase.flight_phase(aircraft_at(50, 0, altitude=20000, vertical_speed=1500, heading=0), now, index)['code'] == 'climb'
    assert phase.flight_phase(aircraft_at(50, 0, altitude=20000, vertical_speed=-1200, heading=0), now, index)['code'] == 'descent'
    assert phase.flight_phase(aircraft_at(50, 0, altitude=35000, vertical_speed=0, heading=0), now, index)['text'] == 'Level'
    # 10 km north of the airport, heading south towards it, descending and low: an approach.
    approach = phase.flight_phase(aircraft_at(10, 0, altitude=2500, vertical_speed=-700, heading=180), now, index, 'mi')
    assert approach['code'] == 'approach' and approach['text'] == 'On approach to KTST · 6 mi out'
    # Same place but flying away and climbing: a departure. Flying past sideways: just descending.
    assert phase.flight_phase(aircraft_at(6, 0, altitude=2000, vertical_speed=900, heading=5), now, index)['text'] == 'Departing KTST'
    assert phase.flight_phase(aircraft_at(10, 0, altitude=2500, vertical_speed=-700, heading=90), now, index)['code'] == 'descent'
    # No airport data, no vertical rate, a stale position or a vessel: nothing is claimed.
    assert phase.flight_phase(aircraft_at(10, 0, altitude=2500, vertical_speed=-700, heading=180), now, None)['code'] == 'descent'
    assert phase.flight_phase(aircraft_at(10, 0, altitude=2500), now, index)['code'] == ''
    assert phase.flight_phase(aircraft_at(10, 0, altitude=2500, vertical_speed=-700, heading=180), now + 500, index)['code'] == ''
    assert phase.flight_phase(Target('vessel', '1', 0, now, dict(lat=1, lon=1), position_time=now), now, index)['code'] == ''
    assert phase.describe_airport(destination(HOME, 8.04672, 45), index, 'mi') == 'KTST · 5.0 mi NE'
    assert phase.describe_airport(destination(HOME, 80, 45), index, 'mi') == ''


def test_real_airport_index_finds_the_nearest_airport():
    from airalert import airports
    index = airports.load()
    found = index.nearest(33.9425, -118.408)           # on top of Los Angeles International (KLAX)
    assert found is not None and index.codes[found[0]] == 'KLAX' and found[1] < 1.5
    assert index.nearest(30.0, -140.0, 30) is None     # mid-Pacific


def test_target_row_and_details_carry_the_phase(station):
    feed(station, [plane('A00001', km=60, altitude=30000, vertical_speed=1800, heading=90, speed=400)], time.time())
    t = station.store.targets['aircraft:A00001']
    assert station.target_row(t)['phase'] == 'Climbing' and station.target_row(t)['phaseCode'] == 'climb'
    assert dict(station.details(t))['Flight phase'] == 'Climbing'
    assert 'Nearest airport' in dict(station.details(t))


# ------------------------------------------------------------------ rule history
def test_rule_activity_counts_and_daily_bars(station):
    rid = rule(station, 'first')
    other = rule(station, 'military')
    now = time.time()
    for age in (10, 3600, 3 * 86400, 20 * 86400):
        station.db.event('alert', 'aircraft:A', 'x', False, now - age, rule=rid)
    station.db.event('alert', 'aircraft:A', 'old style alert without a rule', False, now - 5)
    station.db.conn.commit()
    activity = station.db.rule_activity(now)
    assert activity[rid]['total'] == 4 and activity[rid]['day'] == 2 and activity[rid]['week'] == 3
    assert sum(activity[rid]['daily']) == 3 and len(activity[rid]['daily']) == 14 and activity[rid]['daily'][-1] >= 1
    rows = {r['key']: r for r in station.rule_rows(now)}
    assert rows[rid]['firedTotal'] == 4 and rows[rid]['firedWeek'] == 3 and rows[rid]['lastFired'] != ''
    assert rows[other]['firedTotal'] == 0 and rows[other]['lastFired'] == '' and rows[other]['daily'] == [0] * 14


def test_events_table_is_upgraded_in_place(tmp_path):
    path = tmp_path / 'history.sqlite'
    conn = sqlite3.connect(path)
    conn.execute('CREATE TABLE events (id INTEGER PRIMARY KEY, time REAL, category TEXT, key TEXT, message TEXT, simulated INTEGER)')
    conn.execute("INSERT INTO events(time,category,key,message,simulated) VALUES (1,'alert','aircraft:A','old',0)")
    conn.commit()
    conn.close()
    db = Database(path)
    db.event('alert', 'aircraft:B', 'new', rule='r1')
    assert [(e['message'], e['rule']) for e in db.events(category='alert')][::-1] == [('old', None), ('new', 'r1')]
    db.close()


# ----------------------------------------------------------------- daily summary
def test_daily_summary_text_and_schedule(station):
    conn = station.db.conn
    now = datetime.now().replace(hour=20, minute=0, second=0, microsecond=0).timestamp()
    text = summary.day_summary(conn, 'mi', now)
    assert text['text'] == 'Today: AirAlert was not monitoring.' and not text['monitored']
    conn.execute('INSERT INTO sightings VALUES (?,?,?,?,?,?,?,?)',
                 ('aircraft:A00001', 'aircraft', 'A00001', now - 600, now - 60, json.dumps(dict(registration='N123AB')), 0, 1))
    conn.execute('INSERT INTO sightings VALUES (?,?,?,?,?,?,?,?)',
                 ('aircraft:A00002', 'aircraft', 'A00002', now - 600, now - 60, '{}', 0, 1))
    conn.execute('INSERT INTO sightings VALUES (?,?,?,?,?,?,?,?)',
                 ('sim/aircraft:A00009', 'aircraft', 'A00009', now - 600, now - 60, '{}', 1, 1))
    conn.execute('INSERT INTO sightings VALUES (?,?,?,?,?,?,?,?)',
                 ('vessel:366', 'vessel', '366', now - 600, now - 60, '{}', 0, 1))
    conn.execute('INSERT INTO positions(key,time,lat,lon,distance,simulated) VALUES (?,?,?,?,?,?)',
                 ('aircraft:A00001', now - 100, 32, -118, 160.9344, 0))
    conn.execute('INSERT INTO positions(key,time,lat,lon,distance,simulated) VALUES (?,?,?,?,?,?)',
                 ('sim/aircraft:A00009', now - 100, 32, -118, 900, 1))
    station.db.event('alert', 'aircraft:A00001', 'x', False, now - 50)
    for m in range(90):
        conn.execute('INSERT OR REPLACE INTO minute_stats VALUES (?,?,?,?,?)', (int(now // 60) - m, 600, 2, 1, 0))
    conn.commit()
    info = summary.day_summary(conn, 'mi', now)
    assert info['text'] == 'Today: 2 aircraft and 1 vessel · farthest N123AB at 100 mi · 1 alert · monitored 1.5 h'
    settings = dict(enabled=True, time='19:30', phone=True)
    assert summary.due(conn, settings, now)
    assert not summary.due(conn, dict(settings, time='20:01'), now)
    assert not summary.due(conn, dict(settings, enabled=False), now)
    summary.mark_sent(conn, now)
    assert not summary.due(conn, settings, now + 600)
    assert summary.due(conn, settings, now + 86400)            # tomorrow it is due again


# ----------------------------------------------------- records and reception health
def test_station_records_follow_the_rollups(station):
    conn = station.db.conn
    now = time.time()
    conn.execute('INSERT INTO sightings VALUES (?,?,?,?,?,?,?,?)',
                 ('aircraft:A00001', 'aircraft', 'A00001', now - 600, now, json.dumps(dict(registration='N1FAR')), 0, 1))
    rows = [('aircraft:A00001', now - 300, 100.0, 38000, 450, 0), ('aircraft:A00001', now - 200, 321.5, 41000, 512, 0),
            ('aircraft:A00002', now - 100, 50.0, 99999, 9999, 0),       # garbage altitude and speed are ignored
            ('vessel:366', now - 100, 44.0, None, 12, 0), ('sim/aircraft:S1', now - 100, 900.0, 50000, 600, 1)]
    conn.executemany('INSERT INTO positions(key,time,lat,lon,distance,altitude,speed,simulated) VALUES (?,?,32,-118,?,?,?,?)', rows)
    conn.execute('INSERT OR REPLACE INTO minute_stats VALUES (?,?,?,?,?)', (int(now // 60), 6000, 3, 1, 0))
    conn.commit()
    insights.catch_up_all(conn, HOME)
    found = insights.records(conn, False, now)
    assert found['farthest']['value'] == 321.5 and found['farthest']['label'] == 'N1FAR'
    assert found['highest']['value'] == 41000 and found['fastest']['value'] == 512
    assert found['farthest_vessel']['value'] == 44.0 and found['peak_rate']['value'] == 100.0
    assert found['busiest_hour']['value'] >= 1 and found['busiest_day']['value'] == 2
    assert insights.records(conn, True, now)['farthest']['value'] == 900.0       # with simulation included
    # A later, shorter contact does not replace the record; a longer one does. Deleting history keeps records.
    conn.execute('INSERT INTO positions(key,time,lat,lon,distance,simulated) VALUES (?,?,32,-118,?,0)', ('aircraft:A00001', now, 10.0))
    conn.execute('INSERT INTO positions(key,time,lat,lon,distance,simulated) VALUES (?,?,32,-118,?,0)', ('aircraft:A00003', now, 400.0))
    conn.commit()
    insights.catch_up_all(conn, HOME)
    assert insights.records(conn, False, now)['farthest']['value'] == 400.0
    conn.execute('DELETE FROM positions')
    conn.commit()
    insights.catch_up_all(conn, HOME)
    assert insights.records(conn, False, now)['farthest']['value'] == 400.0


def test_reception_health_low_ok_and_unknown(station):
    conn = station.db.conn
    now = datetime.now().replace(minute=30, second=0, microsecond=0).timestamp()
    minute = int(now // 60)
    assert insights.reception_health(conn, now)['status'] == 'unknown'          # no history at all

    def history(per_minute):
        conn.execute('DELETE FROM minute_stats')
        for day in range(1, 6):                                                  # the same hour on five earlier days
            for m in range(-30, 30):
                conn.execute('INSERT OR REPLACE INTO minute_stats VALUES (?,?,?,?,?)',
                             (minute - day * 1440 + m, per_minute, 5, 0, 0))

    def recent(per_minute, count=15):
        for m in range(1, count + 1):
            conn.execute('INSERT OR REPLACE INTO minute_stats VALUES (?,?,?,?,?)', (minute - m, per_minute, 5, 0, 0))
    history(6000)                      # normally 100 messages a second at this hour
    recent(600)                        # now 10
    low = insights.reception_health(conn, now)
    assert low == dict(status='low', current=10.0, typical=100.0)
    recent(5400)
    assert insights.reception_health(conn, now)['status'] == 'ok'
    history(60)                        # a normally very quiet hour (1 msg/s) cannot be judged
    recent(0)
    assert insights.reception_health(conn, now)['status'] == 'unknown'
    history(6000)
    conn.execute('DELETE FROM minute_stats WHERE minute >= ?', (minute - 15,))
    recent(0, count=5)                 # only five minutes of monitoring so far
    assert insights.reception_health(conn, now)['status'] == 'unknown'


# ---------------------------------------------------------------------- backups
def test_backup_writes_a_restorable_zip_and_prunes_old_ones(station, tmp_path):
    feed(station, [plane('A00001')], time.time())
    station.db.conn.commit()
    dest = tmp_path / 'backups'
    assert backup.latest(dest) is None and backup.due(dest, 7)
    made = [backup.make_backup(station.config.folder, dest, keep=2, now=time.time() + i) for i in range(4)]
    kept = backup.backups(dest)
    assert len(kept) == 2 and made[-1] in kept and not list(dest.glob('*.tmp')) and not list(dest.glob('*.partial'))
    assert not backup.due(dest, 7) and backup.due(dest, 7, now=time.time() + 8 * 86400)
    with zipfile.ZipFile(made[-1]) as zf:
        assert sorted(zf.namelist()) == ['history.sqlite', 'settings.json']
        assert json.loads(zf.read('settings.json'))['home'] == HOME
        zf.extract('history.sqlite', tmp_path / 'restore')
    restored = sqlite3.connect(tmp_path / 'restore' / 'history.sqlite')
    assert restored.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
    assert restored.execute("SELECT COUNT(*) FROM sightings WHERE identifier='A00001'").fetchone()[0] == 1
    restored.close()
    with pytest.raises(ValueError):
        backup.make_backup(station.config.folder, '')


# -------------------------------------------------------------------------- KML
def test_kml_export_is_valid_and_uses_metres(tmp_path):
    rows = [dict(time=1790000000 + i * 10, lat=32 + i * 0.01, lon=-118 + i * 0.01, altitude=10000) for i in range(3)]
    path = tmp_path / 'track.kml'
    export_kml(path, rows, 'N123AB & <friends>')
    root = ET.parse(path).getroot()
    ns = {'k': 'http://www.opengis.net/kml/2.2'}
    assert root.find('.//k:Document/k:name', ns).text == 'N123AB & <friends>'
    coords = root.find('.//k:LineString/k:coordinates', ns).text.split()
    assert coords[0] == '-118.000000,32.000000,3048' and len(coords) == 3
    assert root.find('.//k:LineString/k:altitudeMode', ns).text == 'absolute'
    assert len(root.findall('.//k:Point', ns)) == 2
    export_kml(path, [dict(time=1, lat=1.0, lon=2.0, altitude=None), dict(time=2, lat=1.1, lon=2.1, altitude=None)], 'ship')
    assert ET.parse(path).getroot().find('.//k:LineString/k:altitudeMode', ns).text == 'clampToGround'
    with pytest.raises(ValueError):
        export_kml(path, [], 'nothing')


# ---------------------------------------------------------------------- updates
def test_update_feed_parsing():
    assert updates.is_newer('1.1.0', '1.0.9') and updates.is_newer('1.10', '1.9.5') and updates.is_newer('2', '1.9')
    assert not updates.is_newer('1.0.0', '1.0.0') and not updates.is_newer('1.0', '1.0.0') and not updates.is_newer('0.9', '1.0')
    feed_bytes = json.dumps(dict(version='1.2.0', url='https://example.com/setup.exe', sha256='AB', notes='n')).encode()
    found = updates.parse_feed(feed_bytes, '1.1.0')
    assert found == dict(version='1.2.0', url='https://example.com/setup.exe', sha256='ab', notes='n', available=True)
    assert updates.parse_feed(feed_bytes, '1.2.0')['available'] is False
    assert updates.parse_feed(json.dumps(dict(version='9', url='http://insecure/x.exe')).encode(), '1')['url'] == ''
    for bad in (b'not json', b'[]', b'{"version": 5}', b'{"url": "https://x"}'):
        with pytest.raises(ValueError):
            updates.parse_feed(bad, '1.0.0')


# -------------------------------------------------------------------- phone map
def request(port, method, path, body=None, cookie=None):
    conn = http.client.HTTPConnection('127.0.0.1', port, timeout=5)
    headers = {'Content-Type': 'application/json'}
    if cookie:
        headers['Cookie'] = cookie
    conn.request(method, path, body=json.dumps(body) if body is not None else None, headers=headers)
    response = conn.getresponse()
    data = response.read()
    conn.close()
    return response.status, data, response.getheader('Set-Cookie') or ''


def test_phone_server_pairing_and_access_control():
    saved = []
    server = PhoneServer(b'<html>page</html>', on_paired=saved.append)
    port = server.start(0, '127.0.0.1')
    try:
        server.set_state(b'{"targets":[]}')
        status, body, _ = request(port, 'GET', '/')
        assert status == 200 and body == b'<html>page</html>'
        assert request(port, 'GET', '/api/state')[0] == 401                       # not paired
        assert request(port, 'GET', '/api/state', cookie='airalert=guess')[0] == 401
        assert request(port, 'GET', '/secret')[0] == 404
        assert request(port, 'POST', '/api/pair', dict(code='000000' if server.code != '000000' else '111111'))[0] == 403
        code = server.code
        status, _, cookie = request(port, 'POST', '/api/pair', dict(code=code))
        assert status == 200 and 'HttpOnly' in cookie and 'SameSite=Strict' in cookie
        token = cookie.split(';')[0]
        assert request(port, 'GET', '/api/state', cookie=token) == (200, b'{"targets":[]}', '')
        assert server.code != code                                                # a code pairs one device
        assert saved and saved[-1] == [token_hash(token.split('=', 1)[1])]        # only a hash is kept
        assert request(port, 'POST', '/api/pair', dict(code=code))[0] == 403      # the old code is dead
        server.forget_devices()
        assert request(port, 'GET', '/api/state', cookie=token)[0] == 401
    finally:
        server.stop()
    assert not server.running


def test_phone_server_locks_out_guessing():
    server = PhoneServer(b'x')
    port = server.start(0, '127.0.0.1')
    try:
        first_code = server.code
        wrong = '000000' if first_code != '000000' else '111111'
        statuses = [request(port, 'POST', '/api/pair', dict(code=wrong))[0] for _ in range(5)]
        assert statuses == [403] * 5
        status, body, _ = request(port, 'POST', '/api/pair', dict(code=server.code))
        assert status == 429 and b'wait' in body                                  # even the right code is refused now
        assert server.code != first_code and not server.sessions
    finally:
        server.stop()


def test_only_home_network_addresses_are_private():
    for address in ('127.0.0.1', '192.168.1.20', '10.0.0.5', '172.16.4.4', '169.254.3.3', '::1', 'fe80::1%eth0',
                    '::ffff:192.168.1.9'):
        assert is_private(address), address
    for address in ('8.8.8.8', '172.32.0.1', '1.2.3.4', '2001:4860:4860::8888', 'not-an-ip', '::ffff:8.8.8.8'):
        assert not is_private(address), address


def test_restored_sessions_still_work_after_a_restart():
    server = PhoneServer(b'x')
    port = server.start(0, '127.0.0.1')
    _, _, cookie = request(port, 'POST', '/api/pair', dict(code=server.code))
    sessions = list(server.sessions)
    server.stop()
    again = PhoneServer(b'x', sessions)
    port = again.start(0, '127.0.0.1')
    try:
        assert request(port, 'GET', '/api/state', cookie=cookie.split(';')[0])[0] == 200
    finally:
        again.stop()


# ------------------------------------------------------------------ whole app (UI)
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


def wait_until(condition, timeout=6.0):
    deadline = time.monotonic() + timeout
    while not condition() and time.monotonic() < deadline:
        pump(25)
    return condition()


def free_port():
    probe = socket.socket()
    probe.bind(('127.0.0.1', 0))
    port = probe.getsockname()[1]
    probe.close()
    return port


class Api(BaseHTTPRequestHandler):
    """Stands in for adsbdb.com, the photo host and the update feed."""
    photo = b''
    hits = []

    def log_message(self, *args):
        pass

    def do_GET(self):
        Api.hits.append(self.path)
        port = self.server.server_address[1]
        if self.path == '/aircraft/A0F1BB':
            body, kind = json.dumps(dict(response=dict(aircraft=dict(
                mode_s='A0F1BB', url_photo=f'http://127.0.0.1:{port}/photo.jpg')))).encode(), 'application/json'
        elif self.path == '/callsign/UAL1':
            body, kind = json.dumps(dict(response=dict(flightroute=dict(
                origin=dict(iata_code='SFO', municipality='San Francisco'),
                destination=dict(iata_code='SIN', municipality='Singapore'))))).encode(), 'application/json'
        elif self.path == '/photo.jpg':
            body, kind = Api.photo, 'image/jpeg'
        elif self.path == '/latest.json':
            body, kind = json.dumps(dict(version='99.0.0', url='https://example.com/AirAlert-Setup-99.exe')).encode(), 'application/json'
        else:
            self.send_response(404)
            self.send_header('Content-Length', '0')
            self.end_headers()
            return
        self.send_response(200)
        self.send_header('Content-Type', kind)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture(scope='module')
def ui(tmp_path_factory):
    folder = tmp_path_factory.mktemp('airalert-features')
    (folder / 'settings.json').write_text(json.dumps(dict(home=HOME, mode='Simulation', setup_done=True,
                                                          sample_seconds=1, theme='Dark')), 'utf-8')
    os.environ['AIRALERT_DATA'] = str(folder)
    capture = QmlLog()
    logging.getLogger('airalert').addHandler(capture)
    from airalert.app import build
    app, engine, controller, window = build(['test'], tray=False, show=True, dialogs=False)
    assert window is not None, capture.records
    window.resize(1400, 880)
    controller.bridges['phonemap'].host = '127.0.0.1'      # never open the firewall prompt from a test
    from PySide6.QtCore import QBuffer, QIODevice
    from PySide6.QtGui import QColor, QImage
    image = QImage(64, 48, QImage.Format.Format_RGB32)
    image.fill(QColor('#3366cc'))
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, 'JPG')
    Api.photo = bytes(buffer.data())
    server = ThreadingHTTPServer(('127.0.0.1', 0), Api)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    pump(300)
    # Real (not simulated) traffic injected like a decoder would.
    station = controller.station
    station.manager, station.running, station.sim = FakeManager(), True, None
    now = time.time()
    station.manager.messages.put(dict(plane('A0F1BB', km=40, callsign='UAL1', altitude=31000, vertical_speed=1600,
                                            heading=90, speed=430), _received=now))
    station.manager.messages.put(dict(plane('AE1234', km=25, bearing=200, callsign='RCH401', altitude=9000,
                                            vertical_speed=0, heading=10, speed=300), _received=now))
    controller._tick()
    controller.refresh()
    yield dict(app=app, controller=controller, window=window, capture=capture, folder=folder,
               api=f'http://127.0.0.1:{server.server_address[1]}')
    server.shutdown()
    controller.bridges['phonemap'].shutdown()
    controller.shutdown()
    logging.getLogger('airalert').removeHandler(capture)


def warnings(ui):
    return [r for r in ui['capture'].records if 'QML' in r]


def texts(window):
    from PySide6.QtCore import QObject
    seen, out, stack = set(), [], [window.contentItem()] + list(window.findChildren(QObject))
    alive = []   # keep every wrapper referenced: a freed wrapper's id() can be reused and hide a whole subtree
    while stack:
        o = stack.pop()
        if id(o) in seen:
            continue
        seen.add(id(o))
        alive.append(o)
        if o.metaObject().className().startswith('QQuickText') and o.property('visible'):
            out.append(str(o.property('text')))
        if hasattr(o, 'childItems'):
            stack.extend(o.childItems())
    return out


def test_all_bridges_and_new_conditions_are_present(ui):
    controller = ui['controller']
    assert {'insights', 'playback', 'reception', 'phonemap', 'lookup'} <= set(controller.bridges)
    conditions = {c['value'] for c in controller.ruleOptions()['conditions']}
    assert {'first_aircraft', 'first_type', 'first_operator', 'rare_type', 'military', 'circling'} <= conditions
    assert not warnings(ui), ui['capture'].records


def test_inspector_shows_phase_and_military(ui):
    controller, window = ui['controller'], ui['window']
    controller.selectTarget('aircraft:A0F1BB')
    pump(150)
    details = controller.property('details')
    assert details['phase'] == 'Climbing' and details['military'] is False
    fields = {r['label']: r['value'] for r in controller.detailFields.rows}
    assert fields['Flight phase'] == 'Climbing' and 'Nearest airport' in fields
    assert 'Climbing' in texts(window)
    controller.selectTarget('aircraft:AE1234')
    pump(150)
    assert controller.property('details')['military'] is True and 'Military' in texts(window)
    row = next(r for r in controller.trafficSource.rows if r['key'] == 'aircraft:A0F1BB')
    assert row['phase'] == 'Climbing'
    assert not warnings(ui), ui['capture'].records


def test_snooze_through_the_controller(ui):
    controller, window = ui['controller'], ui['window']
    station = controller.station
    rid = rule(station, 'first')
    controller._refresh_rules()
    raised = []
    controller.alertRaised.connect(lambda *a: raised.append(a))
    controller.snooze('rule', rid, 60)
    assert controller.property('snoozeInfo')['active'] is True
    row = next(r for r in controller.rulesModel.rows if r['key'] == rid)
    assert row['snoozedUntil'] != ''
    unread = controller.property('unreadAlerts')
    station.manager.messages.put(dict(plane('A00777'), _received=time.time()))
    controller._tick()
    assert raised == [] and controller.property('unreadAlerts') == unread        # silent...
    assert any('A00777' in r['text'] for r in controller.alertFeed.rows)         # ...but recorded
    row = next(r for r in controller.rulesModel.rows if r['key'] == rid)
    assert row['firedTotal'] == 1 and row['firedDay'] == 1
    window.setProperty('page', 1)
    pump(250)
    shown = ' '.join(texts(window))
    assert 'Snoozed until' in shown and 'Fired 1× in 24 h' in shown and 'Resume alerts' in shown
    controller.snooze('rule', rid, 0)
    assert controller.property('snoozeInfo')['active'] is False
    station.manager.messages.put(dict(plane('A00778'), _received=time.time()))
    controller._tick()
    assert len(raised) == 1
    controller.snooze('all', '', -1)                                             # rest of today
    assert controller.property('snoozeInfo')['all'] == '23:59'
    controller.wakeAll()
    assert controller.property('snoozeInfo')['active'] is False
    window.setProperty('page', 0)
    assert not warnings(ui), ui['capture'].records


def test_settings_round_trip_for_the_new_options(ui):
    controller = ui['controller']
    was_running = controller.station.running
    controller.station.running = False          # receiver fields are irrelevant here; avoid the running lock
    try:
        form = controller.settingsData()
        assert form['summaryEnabled'] is False and form['phoneMapEnabled'] is False and form['lookupPhotos'] is False
        assert 'HH:MM' in controller.saveSettings(dict(form, summaryEnabled=True, summaryTime='25:00'))
        assert 'backup folder' in controller.saveSettings(dict(form, backupEnabled=True, backupFolder=''))
        assert 'port' in controller.saveSettings(dict(form, phoneMapPort='80'))
        assert controller.saveSettings(dict(form, summaryEnabled=True, summaryTime='7:15', summaryPhone=False,
                                            health_warning=False, backupDays='3', backupKeep='9')) == ''
        assert controller.config['daily_summary'] == dict(enabled=True, time='07:15', phone=False)
        assert controller.config['health_warning'] is False
        assert controller.config['backup']['every_days'] == 3 and controller.config['backup']['keep'] == 9
        assert controller.saveSettings(dict(controller.settingsData(), summaryEnabled=False, health_warning=True)) == ''
    finally:
        controller.station.running = was_running


def test_backup_now_and_daily_summary(ui, tmp_path):
    controller = ui['controller']
    toasts = []
    controller.toast.connect(lambda text, kind: toasts.append(text))
    dest = tmp_path / 'my backups'
    assert controller.backupNow('') != ''
    assert controller.backupNow(str(dest)) == ''
    assert wait_until(lambda: any('Backup saved' in t for t in toasts), 15), toasts
    assert len(backup.backups(dest)) == 1 and controller.lastBackupText(str(dest)).startswith('Last backup 20')
    assert controller.lastBackupText('') == 'No backup yet'
    controller.sendSummaryNow()
    assert any(t.startswith('Today: ') for t in toasts)
    assert any(e['message'].startswith('Today: ') for e in controller.station.db.events(category='application'))


def test_update_check_against_a_feed(ui):
    controller = ui['controller']
    controller.update_url = ''
    controller.checkUpdates(True)
    assert controller.property('updateInfo')['configured'] is False
    controller.update_url = ui['api'] + '/latest.json'
    controller.checkUpdates(True)
    assert wait_until(lambda: controller.property('updateInfo').get('available') is True)
    info = controller.property('updateInfo')
    assert info['version'] == '99.0.0' and info['url'].startswith('https://') and '99.0.0' in info['status']
    controller.update_url = ui['api'] + '/missing.json'
    controller.checkUpdates(False)
    assert wait_until(lambda: 'Could not check' in controller.property('updateInfo')['status'])
    assert controller.property('updateInfo')['available'] is False
    controller.update_url = ''


def test_photo_and_route_lookup_only_when_enabled(ui):
    controller = ui['controller']
    look = controller.bridges['lookup']
    look.base = ui['api']
    Api.hits.clear()
    controller.selectTarget('aircraft:A0F1BB')
    pump(300)
    assert look.property('info') == {} and Api.hits == []                        # off by default: nothing is sent
    controller.config.values['online_lookup'] = dict(photos=True, routes=True)
    controller.settingsChanged.emit()
    assert wait_until(lambda: look.property('info').get('route') and look.property('info').get('photo'))
    info = look.property('info')
    assert info['route'] == 'SFO → SIN' and info['routeDetail'] == 'San Francisco → Singapore'
    assert info['photo'].startswith('file:') and (ui['folder'] / 'photos' / 'A0F1BB.jpg').is_file()
    assert info['key'] == 'aircraft:A0F1BB' and info['photoCredit']
    hits = list(Api.hits)
    controller.refresh()
    controller.refresh()
    pump(200)
    assert Api.hits == hits                                                      # asked once, then remembered
    assert 'SFO → SIN · San Francisco → Singapore' in texts(ui['window'])
    controller.selectTarget('aircraft:AE1234')                                   # unknown to the service: no photo
    assert wait_until(lambda: look.property('info').get('photoPending') is False and
                      look.property('info').get('routePending') is False)
    assert look.property('info')['photo'] == '' and look.property('info')['route'] == ''
    controller.config.values['online_lookup'] = dict(photos=False, routes=False)
    controller.settingsChanged.emit()
    assert look.property('info') == {}
    assert not warnings(ui), ui['capture'].records


def test_phone_map_end_to_end(ui):
    controller = ui['controller']
    bridge = controller.bridges['phonemap']
    assert not bridge.property('running') and bridge.property('url') == ''
    port = free_port()
    controller.config.values['phone_map'] = dict(enabled=True, port=port, sessions=[])
    controller.settingsChanged.emit()
    assert bridge.property('running') and str(port) in bridge.property('url') and len(bridge.property('code')) == 6
    status, page, _ = request(port, 'GET', '/')
    assert status == 200 and b'Pair this device' in page and b'/api/state' in page
    assert request(port, 'GET', '/api/state')[0] == 401
    status, _, cookie = request(port, 'POST', '/api/pair', dict(code=bridge.property('code')))
    assert status == 200
    assert wait_until(lambda: len(controller.config['phone_map']['sessions']) == 1)   # saved (hashed), via the GUI thread
    assert bridge.property('devices') == 1                  # saved (hashed) for the next start
    controller._second()                                                         # publishes a fresh snapshot
    status, body, _ = request(port, 'GET', '/api/state', cookie=cookie.split(';')[0])
    state = json.loads(body)
    assert status == 200 and state['home'] == HOME and state['units'] == 'mi' and state['running'] is True
    target = next(t for t in state['targets'] if t['k'] == 'aircraft:A0F1BB')
    assert target['l'] and target['ph'] == 'Climbing' and target['alt'] == 31000 and target['lat'] is not None
    assert state['counts']['aircraft'] == len([t for t in state['targets'] if t['kind'] == 'aircraft'])
    assert 'settings' not in body.decode() and 'ntfy' not in body.decode()       # no settings or keys leak out
    bridge.forgetDevices()
    assert request(port, 'GET', '/api/state', cookie=cookie.split(';')[0])[0] == 401
    assert controller.config['phone_map']['sessions'] == []
    # A port that is already taken is reported, not fatal.
    blocker = socket.socket()
    blocker.bind(('127.0.0.1', 0))
    blocker.listen(1)
    controller.config.values['phone_map'] = dict(enabled=True, port=blocker.getsockname()[1], sessions=[])
    controller.settingsChanged.emit()
    assert 'could not be opened' in bridge.property('error')
    blocker.close()
    controller.config.values['phone_map'] = dict(enabled=False, port=port, sessions=[])
    controller.settingsChanged.emit()
    assert not bridge.property('running')
    with pytest.raises(OSError):
        request(port, 'GET', '/')


def test_reception_warning_is_raised_once(ui):
    controller = ui['controller']
    bridge = controller.bridges['insights']
    conn = controller.station.db.conn
    toasts = []
    controller.toast.connect(lambda text, kind: toasts.append(text))
    now = datetime.now().replace(minute=30, second=0, microsecond=0).timestamp()
    minute = int(now // 60)
    conn.execute('DELETE FROM minute_stats')
    for day in range(1, 6):
        for m in range(-30, 30):
            conn.execute('INSERT OR REPLACE INTO minute_stats VALUES (?,?,?,?,?)', (minute - day * 1440 + m, 6000, 5, 0, 0))
    for m in range(1, 16):
        conn.execute('INSERT OR REPLACE INTO minute_stats VALUES (?,?,?,?,?)', (minute - m, 300, 5, 0, 0))
    conn.commit()
    assert bridge.check_health(now)['status'] == 'low'
    assert bridge.property('health')['status'] == 'low' and 'Reception is low' in bridge.property('health')['text']
    assert sum('Reception is low' in t for t in toasts) == 1
    bridge.check_health(now)
    assert sum('Reception is low' in t for t in toasts) == 1                     # not repeated while it stays low
    assert 'Reception low' in texts(ui['window'])
    for m in range(1, 16):
        conn.execute('INSERT OR REPLACE INTO minute_stats VALUES (?,?,?,?,?)', (minute - m, 5800, 5, 0, 0))
    conn.commit()
    assert bridge.check_health(now)['status'] == 'ok' and bridge.property('health')['text'] == ''
    conn.execute('DELETE FROM minute_stats')
    conn.commit()
    assert not warnings(ui), ui['capture'].records


def test_statistics_page_shows_station_records(ui):
    controller, window = ui['controller'], ui['window']
    insights.catch_up_all(controller.station.db.conn, HOME)
    window.setProperty('page', 4)
    pump(500)
    rows = controller.bridges['insights'].property('records')
    labels = {r['label'] for r in rows}
    assert {'Farthest aircraft', 'Highest aircraft', 'Fastest aircraft'} <= labels, rows
    farthest = next(r for r in rows if r['key'] == 'farthest')
    assert farthest['value'].endswith(' mi') and '·' in farthest['detail']
    assert 'FARTHEST AIRCRAFT' in texts(window)
    window.setProperty('page', 0)
    pump(100)
    assert not warnings(ui), ui['capture'].records


def test_settings_dialog_shows_the_new_sections(ui):
    from PySide6.QtCore import Q_ARG, QMetaObject, QObject
    controller, window = ui['controller'], ui['window']
    dialog = window.findChild(QObject, 'settingsDialog')
    controller.update_url = ''
    controller.checkUpdates(False)       # forget the status left by the update-check test
    for section in ('connections', 'notifications', 'database'):
        QMetaObject.invokeMethod(dialog, 'openWith', Q_ARG('QVariant', False), Q_ARG('QVariant', section))
        assert wait_until(lambda: dialog.property('opened'))
        pump(150)
        shown = ' '.join(texts(window))
        expected = dict(connections='Live map on your phone or tablet', notifications='Daily summary',
                        database='Back up automatically')[section]
        assert expected in shown, section
        if section == 'connections':
            assert 'Update checks are not set up' in shown
        assert not window.grabWindow().isNull()
        dialog.close()
        assert wait_until(lambda: not dialog.property('visible'))
    assert not warnings(ui), ui['capture'].records
