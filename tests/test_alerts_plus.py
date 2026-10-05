"""Emergency / predicted-pass / AND alerts, quiet hours, voice, phone pushes, startup and single instance."""
import json
import os
import queue
import sys
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from airalert.core.alerts import AlertEngine, closest_approach, emergency_kind
from airalert.core.config import Config
from airalert.core.models import UNIT_KM, Target, TargetStore, destination
from airalert.engine import Station

HOME = (32.0, -118.0)
_KEEP = []  # Qt objects with network/process work stay alive until the session ends


def aircraft(store, now, **data):
    t, _ = store.update(dict(kind='aircraft', identifier=data.pop('identifier', 'A00001'), **data), now)
    return t


def rule(**values):
    base = dict(id='r', name='Rule', kind='aircraft', field='registration', value='', cooldown=0, enabled=True)
    base.update(values)
    return base


# ------------------------------------------------------------------ alert engine
def test_emergency_squawk_fires_once_and_rearms():
    store, engine = TargetStore(), AlertEngine()
    r = [rule(condition='squawk_emergency')]
    fired = []
    for i, code in enumerate(('1200', '7700', '7700', '1200', '7600')):
        t = aircraft(store, 100 + i, squawk=code)
        fired += engine.evaluate(t, r, HOME, [], 100 + i)
    assert [e['detail']['squawk'] for e in fired] == ['7700', '7600']
    assert fired[1]['detail']['emergency'] == 'radio failure'
    assert emergency_kind(aircraft(store, 200, squawk=7500)) == 'hijack'  # numeric squawk normalised


def test_predicted_pass_fires_before_arrival_and_not_when_leaving():
    store, engine = TargetStore(), AlertEngine()
    r = [rule(condition='approach', threshold=5.0, lookahead=10)]
    lat, lon = destination(HOME, 40, 90)  # 40 km east
    t = aircraft(store, 1000, lat=lat, lon=lon, heading=270, speed=300)  # flying west toward home
    cpa, eta = closest_approach(HOME, t)
    assert cpa < 0.5 and 250 < eta < 280  # 40 km at 555.6 km/h ~ 259 s
    events = engine.evaluate(t, r, HOME, [], 1000)
    assert len(events) == 1 and events[0]['detail']['eta_s'] > 200
    assert not engine.evaluate(t, r, HOME, [], 1001)  # still approaching: no repeat
    t2 = aircraft(store, 1000, identifier='A00002', lat=lat, lon=lon, heading=90, speed=300)  # flying away
    assert not engine.evaluate(t2, r, HOME, [], 1000)
    far_lat, far_lon = destination(HOME, 200, 90)
    t3 = aircraft(store, 1000, identifier='A00003', lat=far_lat, lon=far_lon, heading=270, speed=300)
    assert not engine.evaluate(t3, r, HOME, [], 1000)  # 22 minutes away, beyond a 10 minute look-ahead


def test_and_conditions_require_all_at_once():
    store, engine = TargetStore(), AlertEngine()
    r = [rule(condition='enter', threshold=40, extra=[dict(condition='altitude_below', threshold=3000)])]
    lat, lon = destination(HOME, 10, 0)
    fired = []
    for i, altitude in enumerate((5000, 4000, 2500, 2000, 5000, 2500)):
        t = aircraft(store, 10 + i, lat=lat, lon=lon, altitude=altitude)
        fired += engine.evaluate(t, r, HOME, [], 10 + i)
    assert len(fired) == 2  # descends below 3,000 ft inside the radius; later re-arms and fires again


def test_leave_rule_with_filter_only_fires_when_filter_holds():
    store, engine = TargetStore(), AlertEngine()
    r = [rule(condition='leave', threshold=20, extra=[dict(condition='speed_above', threshold=100)])]
    near, far = destination(HOME, 5, 0), destination(HOME, 30, 0)
    t = aircraft(store, 1, lat=near[0], lon=near[1], speed=80)
    engine.evaluate(t, r, HOME, [], 1)
    t = aircraft(store, 2, lat=far[0], lon=far[1], speed=80)
    assert not engine.evaluate(t, r, HOME, [], 2)  # left, but slower than 100 kn
    t = aircraft(store, 3, lat=near[0], lon=near[1], speed=150)
    engine.evaluate(t, r, HOME, [], 3)
    t = aircraft(store, 4, lat=far[0], lon=far[1], speed=150)
    assert len(engine.evaluate(t, r, HOME, [], 4)) == 1


def test_zone_extra_and_unknown_data_never_satisfy():
    store, engine = TargetStore(), AlertEngine()
    zone = dict(name='Box', points=[[31.9, -118.1], [31.9, -117.9], [32.1, -117.9], [32.1, -118.1]])
    r = [rule(condition='first', extra=[dict(condition='in_zone', zone='Box'),
                                        dict(condition='altitude_above', threshold=1000)])]
    t = aircraft(store, 1, lat=32.0, lon=-118.0)  # inside, altitude unknown
    assert not engine.evaluate(t, r, HOME, [zone], 1)
    t = aircraft(store, 2, lat=32.0, lon=-118.0, altitude=1500)
    assert len(engine.evaluate(t, r, HOME, [zone], 2)) == 1


# ---------------------------------------------------------------- config/engine
def test_config_validates_and_merges_new_settings(tmp_path):
    with pytest.raises(ValueError):
        Config.validate(dict(quiet_hours=dict(enabled=True, start='25:00', end='07:00')))
    with pytest.raises(ValueError):
        Config.validate(dict(rules=[rule(condition='enter', extra=[dict(condition='nonsense')])]))
    with pytest.raises(ValueError):
        Config.validate(dict(rules=[rule(condition='approach', lookahead=0)]))
    # Settings from an older version (no airports layer, no phone block) gain the new defaults.
    (tmp_path / 'settings.json').write_text(json.dumps(dict(
        map_layers=dict(aircraft=True, vessels=False, trails=True, rings=True, labels=True), units='nm')), 'utf-8')
    c = Config(tmp_path)
    assert c['map_layers']['vessels'] is False and c['map_layers']['airports'] is True
    assert c['phone']['ntfy_server'] == 'https://ntfy.sh' and c['close_to_tray'] is True


@pytest.fixture
def station(tmp_path):
    config = Config(tmp_path)
    config.values.update(home=list(HOME), units='mi', setup_done=True)
    config.save()
    s = Station(config)
    yield s
    s.shutdown()


def test_rule_form_round_trip_with_extras_and_lookahead(station):
    form = station.rule_form()
    form.update(name='Low pass', condition='approach', threshold=3, lookahead=8, speak=True, phone=True,
                override_quiet=True, extra=[dict(condition='within', threshold=10), dict(condition='altitude_below',
                                                                                        threshold=4000)])
    assert station.save_rule(-1, form) == ''
    saved = station.config['rules'][0]
    assert saved['threshold'] == pytest.approx(3 * UNIT_KM['mi']) and saved['lookahead'] == 8
    assert saved['extra'][0]['threshold'] == pytest.approx(10 * UNIT_KM['mi'])
    assert saved['speak'] and saved['phone'] and saved['override_quiet']
    again = station.rule_form(saved)
    assert again['extra'][0]['threshold'] == pytest.approx(10) and again['lookahead'] == 8
    row = station.rule_rows()[0]
    assert row['extras'] == 2 and '8 min' in row['threshold']
    assert 'predicted to pass within 3 mi' in row['sentence'] and 'below 4000 ft' in row['sentence']
    assert station.save_rule(-1, dict(form, extra=[dict(condition='in_zone', zone='')])) != ''
    assert station.save_rule(-1, dict(form, lookahead=90)) != ''
    assert Config(station.config.folder)['rules'][0]['extra'][1]['condition'] == 'altitude_below'


def test_emergency_preset_and_alert_texts(station):
    assert not station.has_emergency_rule()
    assert station.save_rule(-1, station.emergency_rule_form()) == ''
    assert station.has_emergency_rule()
    station.running, station.sim = True, None

    class Manager:
        messages, status = queue.Queue(), queue.Queue()

        @staticmethod
        def is_alive():
            return False

        @staticmethod
        def stop():
            pass

        @staticmethod
        def join(timeout=None):
            pass
    station.manager = Manager()
    lat, lon = destination(HOME, 8, 45)
    station.manager.messages.put(dict(kind='aircraft', identifier='ABC123', callsign='UAL12', lat=lat, lon=lon,
                                      altitude=12000, squawk='7700'))
    notices, _ = station.tick(now=5000)
    assert len(notices) == 1
    n = notices[0]
    assert n['urgent'] and n['override_quiet'] and n['phone'] and n['speak']
    assert 'squawk 7700 (emergency)' in n['text']
    assert n['speech'].startswith('Emergency alert. UAL12 is squawking 7 7 0 0')
    row = station.target_row(station.store.targets['aircraft:ABC123'], 5000)
    assert row['emergency'] == 'emergency' and row['squawk'] == '7700'
    rule_ = dict(name='x', condition='enter')
    speech = station.speech_text(rule_, station.store.targets['aircraft:ABC123'], {})
    assert speech == 'Alert. UAL12, 5 miles northeast, 12,000 feet.'


# ------------------------------------------------------------------ notify
def test_quiet_hours_windows():
    from airalert.notify import in_quiet_hours
    overnight = dict(enabled=True, start='22:00', end='07:00')
    assert in_quiet_hours(overnight, datetime(2026, 1, 1, 23, 30))
    assert in_quiet_hours(overnight, datetime(2026, 1, 1, 6, 59))
    assert not in_quiet_hours(overnight, datetime(2026, 1, 1, 7, 0))
    assert not in_quiet_hours(overnight, datetime(2026, 1, 1, 12, 0))
    daytime = dict(enabled=True, start='09:00', end='17:00')
    assert in_quiet_hours(daytime, datetime(2026, 1, 1, 12, 0)) and not in_quiet_hours(daytime, datetime(2026, 1, 1, 18, 0))
    assert not in_quiet_hours(dict(overnight, enabled=False), datetime(2026, 1, 1, 23, 30))


class _Capture(BaseHTTPRequestHandler):
    received = []

    def do_POST(self):
        body = self.rfile.read(int(self.headers['Content-Length']))
        _Capture.received.append((self.path, dict(self.headers), body.decode('utf-8')))
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'{}')

    def log_message(self, *args):
        pass


@pytest.fixture
def http_server():
    _Capture.received = []
    server = HTTPServer(('127.0.0.1', 0), _Capture)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f'http://127.0.0.1:{server.server_address[1]}'
    server.shutdown()


def _app():
    from airalert.app import _qt_dll_search_path
    _qt_dll_search_path()
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication(['test'])


def _wait(app, predicate, ms=5000):
    from PySide6.QtCore import QElapsedTimer
    timer = QElapsedTimer()
    timer.start()
    while not predicate() and timer.elapsed() < ms:
        app.processEvents()
    return predicate()


def test_phone_pushes_reach_ntfy_and_pushover(tmp_path, http_server, monkeypatch):
    app = _app()
    import airalert.notify as notify
    monkeypatch.setattr(notify, 'PUSHOVER_URL', http_server + '/1/messages.json')
    config = Config(tmp_path)
    config.values['phone'] = dict(ntfy_enabled=True, ntfy_server=http_server, ntfy_topic='airalert-test',
                                  ntfy_token='secret', pushover_enabled=True, pushover_user='user1',
                                  pushover_token='tok1')
    notifier = notify.Notifier(config)
    _KEEP.append(notifier)
    results = []
    notifier.phoneResult.connect(lambda service, ok, message: results.append((service, ok)))
    notice = dict(text='N123AB \u00b7 5 mi', phone=True, urgent=True, sound=False, desktop=False, speak=False)
    assert notifier.deliver(notice) == ['phone']
    assert _wait(app, lambda: len(results) == 2)
    assert sorted(results) == [('ntfy', True), ('pushover', True)]
    by_path = {path: (headers, body) for path, headers, body in _Capture.received}
    headers, body = by_path['/']
    payload = json.loads(body)
    assert payload['topic'] == 'airalert-test' and payload['message'] == 'N123AB \u00b7 5 mi' and payload['priority'] == 5
    assert headers['Authorization'] == 'Bearer secret'
    form = by_path['/1/messages.json'][1]
    assert 'token=tok1' in form and 'user=user1' in form and 'priority=1' in form


def test_quiet_hours_hold_back_everything_unless_overridden(tmp_path):
    _app()
    from airalert.notify import Notifier
    config = Config(tmp_path)
    config.values['quiet_hours'] = dict(enabled=True, start='22:00', end='07:00')
    notifier = Notifier(config)
    _KEEP.append(notifier)
    spoken = []
    notifier.speak = spoken.append
    notifier.beep = lambda urgent=False: None
    night = datetime(2026, 1, 1, 2, 0)
    notice = dict(text='x', speech='x', sound=True, desktop=True, speak=True, phone=False)
    assert notifier.deliver(notice, night) == []
    assert notifier.deliver(dict(notice, override_quiet=True), night) == ['sound', 'voice']
    assert spoken == ['x']


def test_speech_text_travels_outside_the_command_line(tmp_path):
    app = _app()
    from airalert.notify import Notifier
    notifier = Notifier(Config(tmp_path))
    _KEEP.append(notifier)
    out = tmp_path / 'said.txt'
    notifier.speech_program = sys.executable
    notifier.speech_args = ['-c', f"import os; open(r'{out}', 'w', encoding='utf-8').write(os.environ['AIRALERT_SAY'])"]
    hostile = 'N1"; Remove-Item C:\\x; $(evil) `whoami`'
    done = []
    notifier.spoken.connect(done.append)
    notifier.speak(hostile)
    assert _wait(app, lambda: done)
    assert out.read_text(encoding='utf-8') == hostile


# ------------------------------------------------------------- startup / instance
def test_start_with_windows_shortcut(tmp_path, monkeypatch):
    from airalert import startup
    monkeypatch.setenv('AIRALERT_STARTUP_DIR', str(tmp_path / 'Startup'))
    assert not startup.is_enabled()
    assert startup.set_enabled(True) == ''
    assert startup.is_enabled() and startup.shortcut_path().stat().st_size > 0
    target, arguments, folder = startup.launch_command()
    assert '--minimized' in arguments
    assert startup.set_enabled(False) == '' and not startup.is_enabled()


def test_single_instance_guard(monkeypatch):
    _app()
    from airalert.app import single_instance_server
    monkeypatch.setenv('AIRALERT_INSTANCE', f'AirAlert-test-{os.getpid()}')
    first = single_instance_server()
    assert first is not None and first.isListening()
    assert single_instance_server() is None  # a second launch defers to the first
    first.close()
    again = single_instance_server()
    assert again is not None
    again.close()


def test_close_to_tray_policy(tmp_path, monkeypatch):
    _app()
    monkeypatch.setenv('AIRALERT_DATA', str(tmp_path))
    from airalert.ui.controller import Controller
    config = Config(tmp_path)
    controller = Controller(Station(config), tray=False)

    class Tray:
        messages = []

        def showMessage(self, *args):
            self.messages.append(args)
    assert controller.allowClose() is True  # no tray: closing quits
    controller.tray = Tray()
    assert controller.allowClose() is False and len(Tray.messages) == 1  # hides, explains once
    assert controller.allowClose() is False and len(Tray.messages) == 1
    config.values['close_to_tray'] = False
    assert controller.allowClose() is True
    config.values['close_to_tray'] = True
    controller._quitting = True
    assert controller.allowClose() is True
    controller.tray = None
    controller.shutdown()
