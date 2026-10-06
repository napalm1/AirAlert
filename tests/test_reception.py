"""Reception check: gain ladder, scoring, decoder process handling, Qt bridge and dialog.

Everything runs against recordings or a fake decoder script; no receiver is needed.
"""
import json
import logging
import os
import sys
import threading
import time
from pathlib import Path

import pytest

from airalert import reception as rc
from airalert.core.config import DEFAULTS

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / 'tests' / 'fixtures' / 'modes1.bin'
sys.path.insert(0, str(ROOT / 'tools'))
from reception_snap import make_adsb_iq  # noqa: E402  (tools/ helper)

FRAME = '*8D40621D58C382D690C8AC2863A7;'


def fake_decoder(body):
    """A stand-in decoder process (python -c) for timing, cancel and failure tests."""
    return [sys.executable, '-u', '-c', body]


STREAM = ("import time\nwhile True:\n    print('*8D40621D58C382D690C8AC2863A7;', flush=True)\n"
          "    print('*8D40621D58C386435CC412692AD6;', flush=True)\n    time.sleep(0.02)")


class FakeLive(rc.ReceptionCheck):
    """Pretends to open receiver 0 and runs `script` instead of the real decoder."""
    script = STREAM
    launched = []

    def _resolve(self):
        return dict(index='0', name='Test receiver', serial='00000001')

    def _command(self, gain, exe, device):
        return fake_decoder(self.script)

    def _stop_process(self):
        with self._lock:
            if self._process is not None:
                FakeLive.launched.append(self._process)
        super()._stop_process()


def settings(**overrides):
    return dict(json.loads(json.dumps(DEFAULTS)), **overrides)


def leftover_children():
    with rc._children_lock:
        return [p for p in rc._children if p.poll() is None]


# ------------------------------------------------------------------- helpers
def test_ladder_and_gain_helpers():
    assert rc.ladder('auto') == ['auto', '20.7', '31.2', '38.2', '45.3', '48.8']
    assert rc.ladder('40') == ['auto', '20.7', '31.2', '38.2', '40', '45.3', '48.8']
    assert rc.ladder('38.2') == rc.ladder(None) == rc.ladder('auto')
    assert all(float(g) in rc.TUNER_GAINS for g in rc.LADDER if g != 'auto')
    assert rc.normalize_gain(' 38.20 dB ') == '38.2' and rc.normalize_gain('') == 'auto'
    with pytest.raises(ValueError):
        rc.normalize_gain('51')
    assert rc.safe_gain('loud') == 'auto'
    assert rc.gain_label('auto') == 'Auto' and rc.gain_label('20.7') == '20.7 dB'
    assert rc.score(100, 3, 20) == 150
    assert rc.duration_text(150) == '2 min 30 s' and rc.duration_text(9) == '9 s'


def test_recommend_best_score_ties_prefer_lower_gain():
    def row(gain, score, phase='done'):
        return dict(gain=gain, score=score, phase=phase)
    assert rc.recommend([row('auto', 5), row('31.2', 9), row('45.3', 9), row('48.8', 3)])['gain'] == '31.2'
    assert rc.recommend([row('45.3', 9), row('31.2', 9)])['gain'] == '31.2'
    assert rc.recommend([row('auto', 7), row('20.7', 7)])['gain'] == 'auto'
    assert rc.recommend([row('auto', 0), row('20.7', 0)]) is None
    assert rc.recommend([row('auto', 1), row('20.7', 50, 'skipped')])['gain'] == 'auto'


def test_diagnose_decoder_output():
    assert rc.diagnose(['usb_claim_interface error -3', 'Error opening the RTLSDR device']) == rc.BUSY
    assert rc.diagnose(['RTLSDR: Access denied (insufficient permissions)']) == rc.BUSY
    assert rc.diagnose(['Device Manager: cannot find device with SN 99999999.',
                        'Receiver: cannot set up device.']) == rc.ABSENT
    assert rc.diagnose(['Error opening the RTLSDR device `5`: 87: The parameter is incorrect.']) == rc.OPEN_FAILED
    assert rc.diagnose(['Found 1 device(s):', '0: Nooelec NESDR SMArt v5 SN: 00000001']) == ''
    assert 'SDR#' in rc.BUSY


# ------------------------------------------------------- recordings (decoders)
def test_adsb_recording_runs_every_gain_and_prefers_lower_on_tie(tmp_path):
    events = []
    check = rc.ReceptionCheck('adsb', settings(gain='38.2', aircraft_device='00000001'), tmp_path,
                              gains=['auto', '38.2'], infile=FIXTURE, report=events.append)
    out = check.run()
    assert out['error'] == '' and not out['cancelled']
    assert [r['gain'] for r in out['results']] == ['auto', '38.2']
    for r in out['results']:
        assert r['phase'] == 'done'
        assert r['valid'] >= 200 and r['frames'] >= r['valid'] and r['targets'] == 1
        assert r['score'] == rc.score(r['valid'], r['targets'], r['positions'])
    rec = out['recommendation']
    assert rec['gain'] == 'auto' and out['results'][0]['best'] and not out['results'][1]['best']
    assert not rec['current'] and 'same score' in rec['text']
    # A dedicated config file with the gain overridden; the monitoring config is untouched.
    config = (tmp_path / rc.CONFIG_NAME).read_text('utf-8')
    assert 'gain = 38.2' in config and 'rtlsdr-ppm = 0' in config
    assert not (tmp_path / 'receiver-aircraft.cfg').exists()
    kinds = [e['type'] for e in events]
    assert kinds[-1] == 'done' and 'progress' in kinds
    assert all(0 <= e['progress'] <= 1 for e in events if e['type'] == 'progress')
    assert not leftover_children()


def test_recordings_per_gain_pick_the_best_gain(tmp_path):
    infile = {'auto': make_adsb_iq(tmp_path / 'weak.cu8', 20), '31.2': FIXTURE,
              '45.3': make_adsb_iq(tmp_path / 'medium.cu8', 60)}
    out = rc.ReceptionCheck('adsb', settings(gain='auto'), tmp_path / 'data', gains=list(infile),
                            infile=infile).run()
    by_gain = {r['gain']: r for r in out['results']}
    assert by_gain['auto']['valid'] < by_gain['45.3']['valid'] < by_gain['31.2']['valid']
    assert by_gain['45.3']['positions'] > 0  # alternating CPR frames decode to positions
    assert by_gain['31.2']['share'] == 1.0 and 0 < by_gain['auto']['share'] < 1
    rec = out['recommendation']
    assert rec['gain'] == '31.2' and by_gain['31.2']['best']
    assert 'better score than your current setting (Auto)' in rec['text'] and not rec['weak']


def test_ais_recording(tmp_path):
    from decoder_test import make_ais_iq
    make_ais_iq(tmp_path / 'ais.cf32')
    out = rc.ReceptionCheck('ais', settings(), tmp_path, gains=['auto', '48.8'], infile=tmp_path / 'ais.cf32').run()
    assert out['error'] == ''
    for r in out['results']:
        assert r['frames'] == r['valid'] > 0 and r['targets'] == 1 and r['positions'] == r['valid']
    rec = out['recommendation']
    assert rec['gain'] == 'auto' and rec['current'] and 'No change needed' in rec['text']
    assert rec['weak'] and '1 vessel' in rec['text']


def test_nothing_heard_explains_antenna_checks(tmp_path):
    quiet = tmp_path / 'quiet.cu8'
    quiet.write_bytes(bytes([127]) * 2_000_000)
    out = rc.ReceptionCheck('adsb', settings(), tmp_path, gains=['auto', '38.2'], infile=quiet).run()
    assert out['recommendation'] is None and out['error'] == ''
    assert out['message'].startswith('No signals heard at any gain — check the antenna connection')
    assert 'night' in out['message']
    assert all(r['phase'] == 'done' and r['score'] == 0 and not r['best'] for r in out['results'])


def test_missing_decoder_and_recording(tmp_path):
    out = rc.ReceptionCheck('adsb', settings(), tmp_path, root=tmp_path, infile=FIXTURE).run()
    assert 'decoder is missing' in out['error'] and out['recommendation'] is None
    out = rc.ReceptionCheck('ais', settings(), tmp_path, infile=tmp_path / 'none.cf32').run()
    assert 'does not exist' in out['error']
    assert all(r['phase'] == 'skipped' for r in out['results'])


# ---------------------------------------------------------- device resolution
def test_device_resolution_like_the_decoder_worker(tmp_path):
    devices = [dict(index='0', name='Nooelec NESDR SMArt v5', serial='00000001')]
    by_serial = rc.ReceptionCheck('adsb', settings(aircraft_device='00000001'), tmp_path, detect=lambda: devices)
    assert by_serial._resolve()['index'] == '0'
    by_index = rc.ReceptionCheck('ais', settings(aircraft_device='0'), tmp_path, detect=lambda: devices)
    assert by_index._resolve()['serial'] == '00000001'
    out = rc.ReceptionCheck('adsb', settings(aircraft_device='12345678'), tmp_path, detect=lambda: devices).run()
    assert '12345678' in out['error'] and 'serial 00000001' in out['error']
    out = rc.ReceptionCheck('adsb', settings(), tmp_path, detect=lambda: []).run()
    assert out['error'] == rc.ABSENT and out['recommendation'] is None

    def broken():
        raise FileNotFoundError('AIS-catcher.exe')
    out = rc.ReceptionCheck('adsb', settings(), tmp_path, detect=broken).run()
    assert 'missing' in out['error']


# ------------------------------------------------ live process handling (fake)
def test_live_steps_stop_the_decoder_after_each_step(tmp_path):
    FakeLive.launched = []
    started = time.monotonic()
    out = FakeLive('adsb', settings(), tmp_path, seconds=1, gains=['auto', '38.2'], settle=0).run()
    elapsed = time.monotonic() - started
    assert out['error'] == '' and 1.8 < elapsed < 8
    assert all(r['frames'] > 10 and r['valid'] > 10 and r['targets'] == 1 for r in out['results'])
    assert len(FakeLive.launched) == 2 and all(p.poll() is not None for p in FakeLive.launched)
    assert not leftover_children()


def test_cancel_terminates_the_running_decoder(tmp_path):
    FakeLive.launched = []
    events = []
    check = FakeLive('adsb', settings(), tmp_path, seconds=60, report=events.append, settle=0)
    worker = threading.Thread(target=check.run)
    worker.start()
    deadline = time.time() + 10
    while not any(e['type'] == 'progress' and e['results'][0]['frames'] for e in events) and time.time() < deadline:
        time.sleep(0.05)
    check.cancel()
    worker.join(timeout=6)
    assert not worker.is_alive()
    done = events[-1]
    assert done['type'] == 'done' and done['cancelled'] and done['recommendation'] is None
    assert [r['phase'] for r in done['results']] == ['skipped'] * len(done['results'])
    assert FakeLive.launched and all(p.poll() is not None for p in FakeLive.launched)
    assert not leftover_children()


@pytest.mark.parametrize('script, expected', [
    ("print('usb_claim_interface error -3'); print('Error opening the RTLSDR device')", rc.BUSY),
    ("print('Device Manager: cannot find device with SN 00000001.')", rc.ABSENT),
    ("print('something odd happened')", 'The decoder stopped at Auto before listening'),
])
def test_decoder_failures_are_readable(tmp_path, script, expected):
    class Failing(FakeLive):
        pass
    Failing.script = script
    out = Failing('adsb', settings(), tmp_path, seconds=5, gains=['auto', '38.2'], settle=0).run()
    assert out['error'].startswith(expected)
    assert [r['phase'] for r in out['results']] == ['skipped', 'skipped']
    assert not leftover_children()


# --------------------------------------------------------------- Qt bridge
@pytest.fixture(scope='module')
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication(['test'])


def pump_until(condition, timeout=30):
    from PySide6.QtCore import QCoreApplication
    deadline = time.time() + timeout
    while not condition() and time.time() < deadline:
        QCoreApplication.processEvents()
        time.sleep(0.01)
    QCoreApplication.processEvents()
    return condition()


def fake_controller(folder, gain='auto', mode='Aircraft'):
    from PySide6.QtCore import QObject, Signal

    class Station:
        running = False
        manager = None

    class Config:
        def __init__(self):
            self.values = settings(gain=gain, mode=mode)
            self.folder = Path(folder)

        def __getitem__(self, key):
            return self.values[key]

    class Controller(QObject):
        stateChanged = Signal()
        settingsChanged = Signal()
        toast = Signal(str, str)

        def __init__(self):
            super().__init__()
            self.station = Station()
            self.config = Config()
            self.applied = []

        def applyGain(self, value):
            if self.station.running:
                return 'Stop monitoring before changing the receiver gain.'
            self.applied.append(value)
            self.config.values['gain'] = value
            self.settingsChanged.emit()
            return ''

    return Controller()


def test_bridge_runs_check_and_applies_gain(qapp, tmp_path):
    from airalert.ui.reception import STOP_FIRST, create
    controller = fake_controller(tmp_path, gain='40')
    bridge = create(controller)
    opened = []
    bridge.openRequested.connect(lambda: opened.append(True))
    bridge.open()
    assert opened == [True]
    assert bridge.property('mode') == 'adsb' and bridge.property('secondsPerGain') == 20
    assert [r['gain'] for r in bridge.property('results')] == rc.ladder('40')
    assert bridge.property('statusText').startswith('Ready · 7 gains') and not bridge.property('canApply')

    controller.station.running = True
    assert bridge.start('adsb', 10) == STOP_FIRST
    assert bridge.property('errorText') == STOP_FIRST and not bridge.property('running')
    controller.station.running = False
    controller.stateChanged.emit()
    assert bridge.property('errorText') == ''

    bridge.infile = {'*': make_adsb_iq(tmp_path / 'weak.cu8', 12), '31.2': FIXTURE}
    assert bridge.start('aircraft', 10) == ''
    assert bridge.property('running') and bridge.property('secondsPerGain') == 10
    assert pump_until(lambda: not bridge.property('running'))
    rec = bridge.property('recommendation')
    assert rec['gain'] == '31.2' and bridge.property('progress') == 1.0
    assert bridge.property('statusText') == 'Check complete · best gain 31.2 dB'
    rows = bridge.property('results')
    assert [r['gain'] for r in rows if r['best']] == ['31.2'] and all(r['phase'] == 'done' for r in rows)
    model = bridge.property('resultsModel')
    assert model.rowCount() == len(rows) and model.get(2)['gain'] == '31.2' and model.get(2)['best']
    assert bridge.property('canApply')

    assert bridge.apply() == ''
    assert controller.applied == ['31.2'] and controller.config['gain'] == '31.2'
    assert bridge.property('recommendation')['current'] and not bridge.property('canApply')
    assert bridge.property('currentGainLabel') == '31.2 dB'
    assert [r['gain'] for r in bridge.property('results') if r['current']] == ['31.2']

    bridge.setMode('ais')
    assert bridge.property('mode') == 'ais' and bridge.property('targetNoun') == 'Vessels'
    assert not bridge.property('recommendation') and all(r['phase'] == 'pending' for r in bridge.property('results'))


def test_bridge_reports_no_signal_and_errors(qapp, tmp_path):
    from airalert.ui.reception import create
    controller = fake_controller(tmp_path)
    bridge = create(controller)
    quiet = tmp_path / 'quiet.cu8'
    quiet.write_bytes(bytes([127]) * 1_000_000)
    bridge.infile = quiet
    assert bridge.start('adsb', 10) == ''
    assert pump_until(lambda: not bridge.property('running'))
    assert bridge.property('noSignal') and 'No signals heard at any gain' in bridge.property('errorText')
    assert not bridge.property('recommendation') and not bridge.property('canApply')
    assert bridge.apply() == 'Run a reception check first.'

    bridge.infile = tmp_path / 'missing.cu8'
    assert bridge.start('adsb', 10) == ''
    assert pump_until(lambda: not bridge.property('running'))
    assert 'does not exist' in bridge.property('errorText') and not bridge.property('noSignal')
    assert bridge.property('statusText') == 'Check stopped'


def test_bridge_stops_when_monitoring_starts_and_on_shutdown(qapp, tmp_path, monkeypatch):
    from airalert.ui.reception import MONITORING_STARTED, create
    FakeLive.launched = []
    monkeypatch.setattr(rc.ReceptionCheck, '_resolve', FakeLive._resolve)
    monkeypatch.setattr(rc.ReceptionCheck, '_command', FakeLive._command)
    monkeypatch.setattr(rc.ReceptionCheck, 'script', STREAM, raising=False)
    controller = fake_controller(tmp_path)
    bridge = create(controller)
    assert bridge.start('adsb', 30) == ''
    assert pump_until(lambda: any(r['frames'] for r in bridge.property('results')), 10)
    assert bridge.property('stepText').endswith('aircraft') and 's left' in bridge.property('stepText')
    controller.station.running = True
    controller.stateChanged.emit()
    assert pump_until(lambda: not bridge.property('running'), 10)
    assert bridge.property('errorText') == MONITORING_STARTED and not bridge.property('recommendation')
    assert not leftover_children()

    controller.station.running = False
    controller.stateChanged.emit()
    assert bridge.start('adsb', 30) == ''
    assert pump_until(lambda: any(r['frames'] for r in bridge.property('results')), 10)
    runner_thread = bridge._thread
    bridge.shutdown()  # app exit
    assert not runner_thread.is_alive() and not leftover_children()


# ------------------------------------------------------------ QML dialog
class QmlLog(logging.Handler):
    def __init__(self):
        super().__init__(logging.WARNING)
        self.records = []

    def emit(self, record):
        self.records.append(record.getMessage())


def test_dialog_renders_results_without_qml_warnings(qapp, tmp_path):
    from PySide6.QtCore import QObject, QUrl
    from PySide6.QtQml import QQmlComponent
    folder = tmp_path / 'data'
    folder.mkdir()
    (folder / 'settings.json').write_text(json.dumps(dict(home=[34.05, -118.25], mode='Aircraft', setup_done=True,
                                                          gain='40')), 'utf-8')
    previous = os.environ.get('AIRALERT_DATA')
    os.environ['AIRALERT_DATA'] = str(folder)
    capture = QmlLog()
    logging.getLogger('airalert').addHandler(capture)
    try:
        from airalert.app import build, qml_folder
        app, engine, controller, window = build(['test'], tray=False, show=True, dialogs=False)
        assert window is not None, capture.records
        window.resize(1400, 880)
        bridge = controller.bridges['reception']
        dialog = window.findChild(QObject, 'receptionDialog')
        if dialog is None:  # until Main.qml includes it
            component = QQmlComponent(engine, QUrl.fromLocalFile(str(qml_folder() / 'ReceptionDialog.qml')))
            dialog = component.create()
            assert dialog is not None, [e.toString() for e in component.errors()]
            dialog.setParent(window.contentItem())
            dialog.setProperty('parent', window.contentItem())
        bridge.open()
        assert pump_until(lambda: dialog.property('opened'), 5)
        assert not window.grabWindow().isNull()
        bridge.infile = {'*': make_adsb_iq(tmp_path / 'weak.cu8', 12), '38.2': FIXTURE}
        assert bridge.start('adsb', 10) == ''
        assert pump_until(lambda: not bridge.property('running'))
        assert bridge.property('recommendation')['gain'] == '38.2'
        for theme in ('Light', 'Dark'):
            controller.setTheme(theme)
            assert not window.grabWindow().isNull()
        assert bridge.apply() == '' and controller.config['gain'] == '38.2'
        controller.setMode('Simulation')  # never open the real receiver here
        controller.start()  # the dialog shows the monitoring banner
        assert bridge.property('monitoring') and 'Stop monitoring' in bridge.start('adsb', 10)
        assert not window.grabWindow().isNull()
        controller.stop()
        dialog.close()
        assert pump_until(lambda: not dialog.property('opened'), 5)
        assert not [r for r in capture.records if 'QML' in r], capture.records
        controller.shutdown()
    finally:
        logging.getLogger('airalert').removeHandler(capture)
        if previous is None:
            os.environ.pop('AIRALERT_DATA', None)
        else:
            os.environ['AIRALERT_DATA'] = previous
