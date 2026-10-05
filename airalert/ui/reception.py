"""Reception check bridge, exposed to QML as ``reception`` (registered in ui/bridges.py).

The check itself (``airalert.reception``) runs on a worker thread; its progress
events are queued to the GUI thread through a Qt signal.
"""
import logging
import threading

from PySide6.QtCore import QCoreApplication, QObject, Qt, Property, Signal, Slot

from .. import reception as rc
from .models import RowModel

log = logging.getLogger(__name__)

ROLES = ['key', 'gain', 'label', 'frames', 'valid', 'targets', 'positions', 'score', 'best', 'current', 'phase',
         'share', 'note']
STOP_FIRST = 'Stop monitoring first — the receiver can only be used by one program at a time.'
RELEASING = 'Monitoring is still releasing the receiver. Try again in a few seconds.'
MONITORING_STARTED = ('Monitoring started, so the reception check was stopped — the receiver can only be used by '
                      'one program at a time.')
MODE_NAMES = {'adsb': 'adsb', 'ads-b': 'adsb', 'aircraft': 'adsb', '1090': 'adsb',
              'ais': 'ais', 'marine': 'ais', 'vessel': 'ais', 'vessels': 'ais'}


def create(controller):
    return ReceptionBridge(controller)


def _mode(value):
    return MODE_NAMES.get(str(value or '').strip().lower(), '')


class ReceptionBridge(QObject):
    changed = Signal()
    resultsChanged = Signal()
    openRequested = Signal()
    finished = Signal()
    _event = Signal(object)  # (run id, event) from the worker thread

    def __init__(self, controller):
        super().__init__(controller)
        self.controller = controller
        self.infile = None  # tests/tools: decode this recording instead of opening the receiver
        self._mode = 'ais' if controller.config['mode'] == 'Marine' else 'adsb'
        self._seconds = rc.DEFAULT_SECONDS
        self._running = False
        self._progress = 0.0
        self._status = ''
        self._step = ''
        self._error = ''
        self._no_signal = False
        self._results = []
        self._recommendation = {}
        self._cancel_reason = ''
        self._runner = None
        self._thread = None
        self._run = 0
        self._monitoring = bool(controller.station.running)
        self._model = RowModel(ROLES, key='key', parent=self)
        self._event.connect(self._on_event, Qt.ConnectionType.QueuedConnection)
        controller.stateChanged.connect(self._state_changed)
        controller.settingsChanged.connect(self._settings_changed)
        app = QCoreApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self.shutdown)
        self._plan()

    # ------------------------------------------------------------ properties
    def _current_gain(self):
        return rc.safe_gain(self.controller.config['gain'])

    def _can_apply(self):
        rec = self._recommendation
        return bool(rec) and not rec.get('current') and not self._running and not self.controller.station.running

    def _estimate(self):
        seconds = rc.estimate_seconds(len(self._results) or len(rc.ladder(self._current_gain())), self._seconds)
        return 'about ' + rc.duration_text(round(seconds / 10) * 10)

    def _set_mode(self, value):
        mode = _mode(value)
        if mode and mode != self._mode and not self._running:
            self._mode = mode
            self._plan()

    def _set_seconds(self, value):
        seconds = max(1, min(120, int(value or rc.DEFAULT_SECONDS)))
        if seconds != self._seconds and not self._running:
            self._seconds = seconds
            if not self._recommendation and not any(r['phase'] == 'done' for r in self._results):
                self._status = self._ready_text()
            self.changed.emit()

    running = Property(bool, lambda self: self._running, notify=changed)
    mode = Property(str, lambda self: self._mode, _set_mode, notify=changed)
    secondsPerGain = Property(int, lambda self: self._seconds, _set_seconds, notify=changed)
    secondsChoices = Property('QVariantList', lambda self: list(rc.SECONDS_CHOICES), constant=True)
    progress = Property(float, lambda self: self._progress, notify=changed)
    statusText = Property(str, lambda self: self._status, notify=changed)
    stepText = Property(str, lambda self: self._step, notify=changed)
    errorText = Property(str, lambda self: self._error, notify=changed)
    noSignal = Property(bool, lambda self: self._no_signal, notify=changed)
    recommendation = Property('QVariantMap', lambda self: dict(self._recommendation), notify=changed)
    canApply = Property(bool, _can_apply, notify=changed)
    monitoring = Property(bool, lambda self: bool(self.controller.station.running), notify=changed)
    currentGain = Property(str, _current_gain, notify=changed)
    currentGainLabel = Property(str, lambda self: rc.gain_label(self._current_gain()), notify=changed)
    estimateText = Property(str, _estimate, notify=changed)
    targetNoun = Property(str, lambda self: 'Aircraft' if self._mode == 'adsb' else 'Vessels', notify=changed)
    results = Property('QVariantList', lambda self: [dict(r) for r in self._results], notify=resultsChanged)
    resultsModel = Property(QObject, lambda self: self._model, constant=True)

    # ---------------------------------------------------------------- slots
    @Slot()
    def open(self):
        self.openRequested.emit()

    @Slot(str)
    def setMode(self, mode):
        self._set_mode(mode)

    @Slot(str, int, result=str)
    def start(self, mode, seconds):
        """Begin a check. Returns '' or the reason it cannot start (also shown as errorText)."""
        if self._running:
            return 'A reception check is already running.'
        self._set_mode(mode)
        self._set_seconds(seconds)
        station = self.controller.station
        error = STOP_FIRST if station.running else \
            RELEASING if station.manager is not None and station.manager.is_alive() else ''
        if error:
            self._error = error
            self._no_signal = False
            self.changed.emit()
            return error
        self._run += 1
        run = self._run
        runner = rc.ReceptionCheck(self._mode, dict(self.controller.config.values), self.controller.config.folder,
                                   self._seconds, infile=self.infile,
                                   report=lambda event, run=run: self._event.emit((run, event)))
        self._runner = runner
        self._set_results(runner.results)
        self._recommendation = {}
        self._error = ''
        self._no_signal = False
        self._cancel_reason = ''
        self._progress = 0.0
        self._status = f'Starting · {len(runner.gains)} gains × {rc.duration_text(self._seconds)}'
        self._step = 'Opening the receiver…' if not self.infile else 'Decoding the recording…'
        self._running = True
        self._thread = threading.Thread(target=self._work, args=(runner, run), daemon=True, name='reception-check')
        self._thread.start()
        log.info('Reception check started · %s · %s s per gain', self._mode, self._seconds)
        self.changed.emit()
        return ''

    @Slot()
    def cancel(self):
        if self._running and self._runner is not None:
            self._runner.cancel()
            self._status = 'Cancelling…'
            self._step = 'Stopping the decoder and releasing the receiver.'
            self.changed.emit()

    @Slot(result=str)
    def apply(self):
        """Save the recommended gain through the controller. Returns '' or an error."""
        rec = self._recommendation
        if not rec:
            return 'Run a reception check first.'
        if self._running:
            return 'Wait for the reception check to finish.'
        error = self.controller.applyGain(rec['gain'])
        if error:
            self._error = error
            self._no_signal = False
            self.changed.emit()
            return error
        self._recommendation = dict(rec, current=True)
        self._set_results([dict(r, current=r['gain'] == rec['gain']) for r in self._results])
        self._status = f"Receiver gain set to {rec['label']}"
        self._error = ''
        self.changed.emit()
        return ''

    @Slot()
    def shutdown(self):
        """Stop a running check and wait for its decoder to exit (app exit)."""
        runner, thread = self._runner, self._thread
        if runner is not None:
            runner.cancel()
        if thread is not None and thread.is_alive():
            thread.join(timeout=8)

    # -------------------------------------------------------------- internals
    def _ready_text(self):
        count = len(self._results)
        return f'Ready · {count} gains × {rc.duration_text(self._seconds)} each · {self._estimate()}'

    def _plan(self):
        """Idle state: pending rows for the gains the next check will try."""
        self._set_results([rc.blank_result(g, self._current_gain()) for g in rc.ladder(self._current_gain())])
        self._recommendation = {}
        self._error = ''
        self._no_signal = False
        self._progress = 0.0
        self._status = self._ready_text()
        self._step = ''
        self.changed.emit()

    def _set_results(self, rows):
        rows = [{k: r.get(k) for k in ROLES} for r in rows]
        if [r['key'] for r in rows] == [r['key'] for r in self._model.rows]:
            self._model.update_rows(rows)
        else:
            self._model.set_rows(rows)
        self._results = rows
        self.resultsChanged.emit()

    def _work(self, runner, run):
        try:
            runner.run()
        except Exception as e:  # run() reports its own failures; this is a last resort
            log.exception('Reception check crashed')
            self._event.emit((run, dict(type='done', results=[dict(r) for r in runner.results], recommendation=None,
                                        error=f'The reception check failed: {e}', message='', cancelled=False,
                                        elapsed=0)))

    def _on_event(self, payload):
        run, event = payload
        if run != self._run:
            return  # a superseded check
        if event['type'] == 'progress':
            if not self._running:
                return
            self._set_results(event['results'])
            self._progress = event['progress']
            if not self._runner or not self._runner.cancelled:
                self._status = event['status']
                self._step = event['step']
            self.changed.emit()
            return
        if event['type'] != 'done':
            return
        self._running = False
        self._runner = None
        self._set_results(event['results'])
        rec = event.get('recommendation') or {}
        self._recommendation = dict(rec)
        tested = sum(r['phase'] == 'done' for r in event['results'])
        total = len(event['results'])
        error = event.get('error') or (self._cancel_reason if event.get('cancelled') else '')
        self._no_signal = False
        if error:
            self._status = 'Check stopped'
            self._step = f'{tested} of {total} gains tested'
            self._error = error
        elif event.get('cancelled'):
            self._status = 'Check cancelled'
            self._step = f'{tested} of {total} gains tested · the receiver is free again'
            self._error = ''
        else:
            self._progress = 1.0
            self._step = f"{total} gains tested in {rc.duration_text(event.get('elapsed', 0))}"
            if rec:
                self._status = f"Check complete · best gain {rec['label']}"
                self._error = ''
            else:
                self._status = 'Check complete · no signals heard'
                self._error = event.get('message', '')
                self._no_signal = True
        log.info('Reception check finished · %s · %s', self._status, self._error or rec.get('text', ''))
        self.changed.emit()
        self.finished.emit()

    def _state_changed(self):
        station = self.controller.station
        monitoring = bool(station.running)
        if monitoring and self._running and not self._cancel_reason:
            self._cancel_reason = MONITORING_STARTED
            self.cancel()
        changed = monitoring != self._monitoring
        self._monitoring = monitoring
        releasing = station.manager is not None and station.manager.is_alive()
        if not monitoring and not releasing and self._error in (STOP_FIRST, RELEASING):
            self._error = ''  # the receiver is free again
            changed = True
        if changed:
            self.changed.emit()

    def _settings_changed(self):
        if not self._running and not any(r['phase'] != 'pending' for r in self._results):
            self._plan()  # the current gain may have changed the ladder
        else:
            self.changed.emit()
