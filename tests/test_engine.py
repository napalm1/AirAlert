"""Station engine workflows shared by the UI and the packaged self-test."""
import json
import queue

import pytest

from airalert.core.config import Config
from airalert.core.models import UNIT_KM, destination
from airalert.engine import Station, rule_sentence

HOME = [32.0, -118.0]


class FakeManager:
    """Stands in for ReceiverManager so decoded messages can be injected."""
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
    station.manager = FakeManager()
    station.running = True
    station.sim = None
    for m in messages:
        station.manager.messages.put(m)
    return station.tick(now=now)


def test_simulation_requires_home_and_produces_traffic(tmp_path):
    config = Config(tmp_path)
    s = Station(config)
    assert 'home location' in s.start('Simulation')
    s.config.values['home'] = HOME
    assert s.start('Simulation') == ''
    s.sim_time -= 2
    s.tick()
    assert len(s.store.targets) == 9
    assert sum(t.kind == 'aircraft' for t in s.store.targets.values()) == 5
    assert all(t.simulated for t in s.store.targets.values())
    s.stop()
    assert not s.running
    s.shutdown()


def test_entry_stay_exit_reentry_and_notices(station):
    station.save_rule(-1, dict(name='N123AB near', kind='aircraft', field='registration', value='n123ab',
                               condition='enter', threshold=25, cooldown=0, sound=True, desktop=False))
    assert station.config['rules'][0]['threshold'] == pytest.approx(25 * UNIT_KM['mi'])
    fired = []
    for i, km in enumerate((50, 30, 30, 50, 30)):
        lat, lon = destination(HOME, km, 90)
        notices, _ = feed(station, [dict(kind='aircraft', identifier='A00001', registration='N123AB', lat=lat, lon=lon)],
                          now=1000 + i * 5)
        fired += notices
    assert len(fired) == 2
    assert fired[0]['sound'] is True and fired[0]['label'] == 'N123AB'
    assert len(station.db.events(category='alert')) == 2


def test_rule_form_round_trip_and_sentence(station):
    form = station.rule_form()
    assert form['threshold'] == pytest.approx(25)
    form.update(name='Low flyer', condition='altitude_below', threshold=1500, value='')
    assert station.save_rule(-1, form) == ''
    rows = station.rule_rows()
    assert rows[0]['threshold'] == '1500 ft'
    assert 'flies below 1500 ft' in rows[0]['sentence']
    assert station.save_rule(-1, dict(form, name='')) != ''
    assert station.save_rule(-1, dict(form, condition='zone_enter', zone='')) != ''
    assert station.set_rule_enabled(0, False) == ''
    assert station.config['rules'][0]['enabled'] is False
    assert station.delete_rule(0) == '' and station.config['rules'] == []
    assert 'within 25 mi' in rule_sentence(dict(kind='any', condition='enter', threshold=25 * UNIT_KM['mi']), 'mi')


def test_geofence_lifecycle_updates_rules(station):
    square = [[31.9, -118.1], [31.9, -117.9], [32.1, -117.9], [32.1, -118.1]]
    assert station.create_zone('Harbor', square) == ''
    assert station.create_zone('harbor', square) != ''  # names are unique, case-insensitive
    assert station.create_zone('Tiny', square[:2]) != ''
    station.save_rule(-1, dict(name='Zone', kind='any', field='identifier', value='', condition='zone_enter',
                               zone='Harbor', threshold=0, cooldown=0))
    notices, _ = feed(station, [dict(kind='vessel', identifier='123456789', lat=32.0, lon=-118.0)], now=5000)
    assert len(notices) == 1
    assert station.rename_zone('Harbor', 'Marina') == ''
    assert station.config['rules'][0]['zone'] == 'Marina'
    assert station.linked_rules('Marina') == 1
    assert station.remove_zone('Marina') == ''
    assert station.zone('Marina') is None
    assert station.rule_rows()[0]['inactive'] is True
    assert not any(k[0] == station.config['rules'][0]['id'] for k in station.alerts.states)


def test_watchlist_and_details(station):
    assert station.save_watch(-1, dict(kind='aircraft', name='Club', identifier='N123AB')) == ''
    assert station.save_watch(-1, dict(kind='aircraft', identifier=' ')) != ''
    lat, lon = destination(HOME, 10, 45)
    feed(station, [dict(kind='aircraft', identifier='A00001', registration='N123AB', lat=lat, lon=lon, altitude=4500,
                        speed=120, heading=45)], now=9000)
    t = station.store.targets['aircraft:A00001']
    assert station.is_watched(t)
    row = station.target_row(t, now=9000)
    assert row['watched'] and row['distance'] == pytest.approx(10 / UNIT_KM['mi'], abs=0.1) and row['bearing'] == 45
    details = dict(station.details(t))
    assert details['Registration'] == 'N123AB' and details['Altitude'] == '4500 ft'
    watch = station.watch_rows()[0]
    assert watch['live'] and watch['sightings'] == 1
    rule = station.rule_for_watch(0)
    assert rule['field'] == 'registration' and rule['value'] == 'N123AB'
    assert station.delete_watch(0) == '' and station.config['watchlist'] == []


def test_history_search_export_and_maintenance(station, tmp_path):
    for i, km in enumerate((10, 20, 40)):
        lat, lon = destination(HOME, km, 0)
        feed(station, [dict(kind='aircraft', identifier=f'A0000{i}', lat=lat, lon=lon)], now=10000 + i)
    rows = station.search_history('sightings')
    assert len(rows) == 3
    near = station.search_history('sightings', max_distance=10)  # miles: 10 km = 6.2 mi, 20 km = 12.4 mi
    assert [r['identifier'] for r in near] == ['A00000']
    assert station.search_history('detection', start=0, end=2e10)
    with pytest.raises(ValueError):
        station.search_history('sightings', start=10, end=5)
    station.export(tmp_path / 'out.json', rows)
    station.export(tmp_path / 'out.csv', rows)
    assert len(json.loads((tmp_path / 'out.json').read_text())) == 3
    info = station.maintenance_info()
    assert info['sightings'] == 3 and info['positions'] >= 3
    stats = station.statistics()
    assert {r['source'] for r in stats['rows']} == {'Local RF', 'Simulation'}


def test_pause_survives_database_failure(station):
    from airalert.core.database import Database
    assert station.start('Simulation') == ''
    station.db.conn.close()  # storage fails underneath the engine
    station.pause('Monitoring paused')
    assert not station.running and station.paused_reason == 'Monitoring paused' and station.session is None
    station.db = Database(station.config.folder / 'history.sqlite')


def test_settings_validation(station):
    assert station.save_settings(dict(rings=[0])) != ''
    assert station.save_settings(dict(gain='99')) != ''
    assert 'two different' in station.save_settings(dict(mode='Dual receivers', aircraft_device='A', marine_device='A'))
    assert station.save_settings(dict(units='km', rings=[5, 10], gain='20')) == ''
    assert Config(station.config.folder)['units'] == 'km'
