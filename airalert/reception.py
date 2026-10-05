"""Reception check: listen at a ladder of receiver gains and score what each one hears.

Qt-free; ``airalert.ui.reception`` runs :class:`ReceptionCheck` on a worker thread.
Each step starts the same decoder the app uses (dump1090 for ADS-B, AIS-catcher for
AIS) at one gain, counts what it decodes for N seconds, then stops it again. Tests
and tools can pass ``infile`` to decode a recording instead of opening the receiver.
"""
import atexit
import logging
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path

from .core.parsers import ADSBParser, AISParser
from .core.receivers import FLAGS, adsb_config, detect_devices, resource_root

log = logging.getLogger(__name__)

MODES = ('adsb', 'ais')
SECONDS_CHOICES = (10, 20, 30)
DEFAULT_SECONDS = 20
# Gains (dB) the RTL-SDR tuner offers, as dump1090 lists them; settings accept 0-50.
TUNER_GAINS = (0.0, 3.4, 6.8, 10.2, 13.7, 17.1, 20.7, 24.0, 27.8, 31.2, 34.6, 38.2, 41.6, 45.3, 48.8, 52.7)
LADDER = ('auto', '20.7', '31.2', '38.2', '45.3', '48.8')
TARGET_WEIGHT = 10          # each distinct aircraft / vessel is worth this many messages
FEW_MESSAGES = 20           # below this the recommendation is flagged as tentative
SETTLE_SECONDS = 1.0        # pause between live steps so the USB receiver is released
STEP_OVERHEAD = 2.0         # rough decoder start + settle time per step, for estimates
FILE_TIMEOUT = 120          # safety cap when decoding a recording
CONFIG_NAME = 'reception-check.cfg'

NO_SIGNAL = {
    'adsb': 'No signals heard at any gain — check the antenna connection, place the antenna higher / near a '
            'window, and remember traffic may be light (e.g. at night).',
    'ais': 'No signals heard at any gain — check the antenna connection (AIS needs a marine VHF antenna, not a '
           '1090 MHz one), place the antenna higher / near a window with a view of the water, and remember '
           'traffic may be light (e.g. at night).',
}
BUSY = ('The receiver is busy or Windows denied access. Close other SDR apps such as SDR# (and any other '
        'AirAlert window), wait a few seconds and try again. If it keeps happening, check the WinUSB driver (Zadig).')
ABSENT = ('No RTL-SDR receiver was found. Check that it is plugged in and uses the WinUSB driver (Zadig). If it is '
          'connected, another program such as SDR# may be holding it — close other SDR apps and try again.')
OPEN_FAILED = ('The receiver was found but could not be opened. Close other SDR apps such as SDR#, check the '
               'WinUSB driver (Zadig), reconnect the receiver and try again.')
BUSY_PATTERNS = ('usb_claim_interface', 'access denied', 'libusb_error_access', 'libusb_error_busy',
                 'resource busy')
ABSENT_PATTERNS = ('cannot find device', 'no supported devices', 'no devices found', 'found 0 device',
                   'libusb_error_no_device', 'no such device')
OPEN_PATTERNS = ('error opening the rtlsdr device', 'cannot open device', 'cannot set up device',
                 'failed to open', 'rtlsdr_open')
DECODERS = {'adsb': ('vendor/adsb/dump1090.exe', 'aircraft (dump1090)'),
            'ais': ('vendor/ais/AIS-catcher.exe', 'marine (AIS-catcher)')}


class CheckError(Exception):
    """A readable reason the check cannot continue."""


# ----------------------------------------------------------------- helpers
def normalize_gain(gain):
    """'auto' or a dB value formatted like the settings store it (e.g. '38.2'). Raises ValueError."""
    text = str(gain if gain is not None else 'auto').strip().lower().removesuffix('db').strip()
    if text in ('', 'auto'):
        return 'auto'
    value = float(text)
    if not 0 <= value <= 50:
        raise ValueError('Gain must be "auto" or between 0 and 50 dB.')
    return f'{value:g}'


def safe_gain(gain):
    try:
        return normalize_gain(gain)
    except (TypeError, ValueError):
        return 'auto'


def gain_label(gain):
    return 'Auto' if gain == 'auto' else f'{gain} dB'


def gain_rank(gain):
    """Order used for the table and for ties: auto first, then ascending dB."""
    return -1.0 if gain == 'auto' else float(gain)


def ladder(current=None):
    """Gains to test: auto plus a spread of tuner gains, plus the current manual gain if different."""
    gains = set(LADDER)
    if current is not None:
        gains.add(safe_gain(current))
    return sorted(gains, key=gain_rank)


def score(valid, targets, positions):
    return int(valid) + int(positions) + TARGET_WEIGHT * int(targets)


def recommend(results):
    """Best finished result, ties going to the lower gain; None when nothing was heard."""
    heard = [r for r in results if r.get('phase', 'done') == 'done' and r.get('score', 0) > 0]
    if not heard:
        return None
    return min(heard, key=lambda r: (-r['score'], gain_rank(r['gain'])))


def diagnose(lines):
    """Readable cause from decoder output, or '' when nothing recognisable was printed."""
    text = '\n'.join(lines).lower()
    for patterns, message in ((BUSY_PATTERNS, BUSY), (ABSENT_PATTERNS, ABSENT), (OPEN_PATTERNS, OPEN_FAILED)):
        if any(p in text for p in patterns):
            return message
    return ''


def duration_text(seconds):
    seconds = max(0, int(round(seconds)))
    if seconds < 60:
        return f'{seconds} s'
    minutes, rest = divmod(seconds, 60)
    return f'{minutes} min' + (f' {rest} s' if rest else '')


def estimate_seconds(gain_count, seconds):
    return gain_count * (seconds + STEP_OVERHEAD)


def blank_result(gain, current=None):
    return dict(key=gain, gain=gain, label=gain_label(gain), frames=0, valid=0, targets=0, positions=0,
                score=0, best=False, current=gain == current, phase='pending', share=0.0, note='')


# ------------------------------------------------------- child processes
_children = set()
_children_lock = threading.Lock()
_job = None


def _kill_on_close_job():
    """A Windows job object that kills its processes when AirAlert exits, even after a crash."""
    if sys.platform != 'win32':
        return None
    try:
        import ctypes
        from ctypes import wintypes

        class IoCounters(ctypes.Structure):
            _fields_ = [(name, ctypes.c_ulonglong) for name in (
                'ReadOperationCount', 'WriteOperationCount', 'OtherOperationCount',
                'ReadTransferCount', 'WriteTransferCount', 'OtherTransferCount')]

        class BasicLimits(ctypes.Structure):
            _fields_ = [('PerProcessUserTimeLimit', ctypes.c_int64), ('PerJobUserTimeLimit', ctypes.c_int64),
                        ('LimitFlags', wintypes.DWORD), ('MinimumWorkingSetSize', ctypes.c_size_t),
                        ('MaximumWorkingSetSize', ctypes.c_size_t), ('ActiveProcessLimit', wintypes.DWORD),
                        ('Affinity', ctypes.c_size_t), ('PriorityClass', wintypes.DWORD),
                        ('SchedulingClass', wintypes.DWORD)]

        class ExtendedLimits(ctypes.Structure):
            _fields_ = [('BasicLimitInformation', BasicLimits), ('IoInfo', IoCounters),
                        ('ProcessMemoryLimit', ctypes.c_size_t), ('JobMemoryLimit', ctypes.c_size_t),
                        ('PeakProcessMemoryUsed', ctypes.c_size_t), ('PeakJobMemoryUsed', ctypes.c_size_t)]

        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.CreateJobObjectW.restype = wintypes.HANDLE
        kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        kernel.SetInformationJobObject.restype = wintypes.BOOL
        kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        kernel.AssignProcessToJobObject.restype = wintypes.BOOL
        kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        job = kernel.CreateJobObjectW(None, None)
        if not job:
            return None
        limits = ExtendedLimits()
        limits.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not kernel.SetInformationJobObject(job, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            kernel.CloseHandle(job)
            return None
        return lambda process: kernel.AssignProcessToJobObject(job, int(process._handle))
    except (OSError, AttributeError, ValueError):
        log.debug('Job object unavailable', exc_info=True)
        return None


def _adopt(process):
    global _job
    with _children_lock:
        _children.add(process)
        if _job is None:
            _job = _kill_on_close_job() or False
        assign = _job
    if assign:
        try:
            assign(process)
        except (OSError, ValueError, AttributeError):
            log.debug('Could not add decoder to the job object', exc_info=True)


def _release(process):
    with _children_lock:
        _children.discard(process)


def _terminate(process, wait=True):
    if process.poll() is None:
        try:
            process.terminate()
        except OSError:
            pass
        if not wait:
            return
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=3)


@atexit.register
def _stop_children():
    with _children_lock:
        children = list(_children)
    for process in children:
        try:
            if process.poll() is None:
                process.kill()
        except OSError:
            pass


def _read_lines(stream, lines):
    try:
        for line in stream:
            lines.put(line)
    except (OSError, ValueError):
        pass
    finally:
        lines.put(None)


# ------------------------------------------------------------------ runner
class ReceptionCheck:
    """One reception check. ``run()`` blocks; ``cancel()`` may be called from any thread.

    ``report(event)`` receives dicts: ``{'type': 'progress', ...}`` while listening and a
    final ``{'type': 'done', ...}`` (see :meth:`run`). ``infile`` decodes a recording
    instead of the receiver: a path, or a dict ``{gain: path}`` (``'*'`` = default).
    """

    def __init__(self, mode, settings, folder, seconds=DEFAULT_SECONDS, gains=None, infile=None, report=None,
                 detect=None, root=None, settle=SETTLE_SECONDS):
        if mode not in MODES:
            raise ValueError(f'Unknown reception check mode: {mode}')
        self.mode = mode
        self.settings = dict(settings)
        self.folder = Path(folder).resolve()  # decoders run with the data folder as working directory
        self.seconds = max(1.0, float(seconds))
        self.current = safe_gain(self.settings.get('gain', 'auto'))
        self.gains = sorted({safe_gain(g) for g in gains}, key=gain_rank) if gains else ladder(self.current)
        self.infile = infile
        self.root = Path(root) if root else resource_root()
        self.settle = settle
        self._report = report or (lambda event: None)
        self._detect = detect or detect_devices
        self._cancel = threading.Event()
        self._lock = threading.Lock()
        self._process = None
        self._started = time.monotonic()
        self.results = [blank_result(g, self.current) for g in self.gains]

    # ------------------------------------------------------------ public
    @property
    def cancelled(self):
        return self._cancel.is_set()

    def cancel(self):
        """Stop as soon as possible; the running decoder is terminated (the runner waits for it)."""
        self._cancel.set()
        with self._lock:
            process = self._process
        if process is not None:
            _terminate(process, wait=False)

    def run(self):
        """Run every step. Returns (and reports as type 'done') a dict with ``results``,
        ``recommendation`` (dict or None), ``error``, ``message`` (no-signal advice),
        ``cancelled`` and ``elapsed``."""
        self._started = time.monotonic()
        outcome = dict(type='done', mode=self.mode, recommendation=None, error='', message='', cancelled=False)
        try:
            self.folder.mkdir(parents=True, exist_ok=True)
            exe = self._decoder()
            device = None if self.infile else self._resolve()
            for i, gain in enumerate(self.gains):
                if self.cancelled:
                    break
                if i and not self.infile and self._cancel.wait(self.settle):
                    break
                self._step(i, gain, exe, device)
        except CheckError as e:
            outcome['error'] = str(e)
        except Exception as e:  # never leave the UI waiting
            log.exception('Reception check failed')
            outcome['error'] = f'The reception check failed: {e}'
        finally:
            self._stop_process()
        for r in self.results:
            if r['phase'] in ('pending', 'active'):
                r['phase'] = 'skipped'
                r['note'] = 'skipped'
        outcome['cancelled'] = self.cancelled and not outcome['error']
        if not outcome['error'] and not outcome['cancelled']:
            best = recommend(self.results)
            if best:
                best['best'] = True
                outcome['recommendation'] = self._recommendation(best)
            else:
                outcome['message'] = NO_SIGNAL[self.mode]
        self._update_shares()
        outcome['results'] = [dict(r) for r in self.results]
        outcome['elapsed'] = time.monotonic() - self._started
        self._emit(outcome)
        return outcome

    # ----------------------------------------------------------- set-up
    def _decoder(self):
        relative, name = DECODERS[self.mode]
        exe = self.root / relative
        if not exe.is_file():
            raise CheckError(f'The {name} decoder is missing ({relative}). Reinstall AirAlert to restore it.')
        return exe

    def _resolve(self):
        """Primary receiver from settings, matched like the decoder worker (serial, then short index)."""
        wanted = str(self.settings.get('aircraft_device', '0')).strip()
        try:
            devices = self._detect()
        except FileNotFoundError:
            raise CheckError('The receiver detection tool (vendor/ais/AIS-catcher.exe) is missing. '
                             'Reinstall AirAlert to restore it.')
        except (OSError, subprocess.SubprocessError) as e:
            raise CheckError(f'Receivers could not be listed ({e}). Reconnect the receiver and try again.')
        device = next((d for d in devices if d['serial'] == wanted), None)
        if device is None and wanted.isdigit() and len(wanted) < 3:
            device = next((d for d in devices if d['index'] == wanted), None)
        if device is not None:
            return device
        if not devices:
            raise CheckError(ABSENT)
        found = '; '.join(f"{d['name']} (serial {d['serial']})" for d in devices)
        raise CheckError(f'The receiver chosen in Settings → Receivers ({wanted or "none"}) was not found. '
                         f'Connected: {found}. Choose it under Settings → Receivers and try again.')

    def _input(self, gain):
        source = self.infile.get(gain, self.infile.get('*')) if isinstance(self.infile, dict) else self.infile
        if source is None:
            raise CheckError(f'No recording was given for gain {gain_label(gain)}.')
        source = Path(source).resolve()
        if not source.is_file():
            raise CheckError(f'The recording {source} does not exist.')
        return source

    def _command(self, gain, exe, device):
        """Decoder arguments for one step (the same settings the app uses, with the gain overridden)."""
        settings = dict(self.settings, gain=gain)
        source = self._input(gain) if self.infile else None
        if self.mode == 'adsb':
            config = self.folder / CONFIG_NAME
            adsb_config(config, settings)
            args = [str(exe), '--config', str(config)]
            args += ['--infile', str(source), '--samplerate', '2M'] if source else ['--device', str(device['index'])]
            return args + ['--raw']
        args = [str(exe)]
        args += ['-r', 'CF32', str(source), '-s', '96000'] if source else ['-d', device['serial'], '-s', '1536000']
        return args + ['-o', '1', '-p', str(settings.get('ppm', 0)), '-gr', 'TUNER', gain]

    # ------------------------------------------------------------ steps
    def _step(self, index, gain, exe, device):
        result = self.results[index]
        result.update(phase='active', note='starting')
        parser = ADSBParser(self.settings.get('home')) if self.mode == 'adsb' else AISParser()
        targets = set()
        tail = []

        def handle(line):
            text = line.strip()
            if not text:
                return
            frame = (text.startswith('*') and text.endswith(';')) if self.mode == 'adsb' else '!AI' in text
            if not frame:
                if len(tail) < 40:
                    tail.append(text[:300])
                return
            result['frames'] += 1
            try:
                message = parser.parse(text)
            except Exception:  # a decoder library hiccup must not end the check
                log.debug('Unparseable decoder line %r', text[:120], exc_info=True)
                return
            if message:
                result['valid'] += 1
                targets.add(message['identifier'])
                result['targets'] = len(targets)
                if message.get('lat') is not None and message.get('lon') is not None:
                    result['positions'] += 1

        args = self._command(gain, exe, device)
        log.info('Reception check %s at %s: %s', self.mode, gain_label(gain), ' '.join(args))
        exited = self._listen(args, handle, index, result)
        result['score'] = score(result['valid'], result['targets'], result['positions'])
        result['phase'] = 'done' if not self.cancelled else 'skipped'
        result['note'] = '' if not self.cancelled else 'cancelled'
        if tail:
            log.info('Decoder output at %s: %s', gain_label(gain), ' | '.join(tail[:12]))
        if self.cancelled or self.infile:
            return
        cause = diagnose(tail)
        if exited or (cause and result['frames'] == 0):
            result['phase'] = 'skipped'
            result['note'] = 'failed'
            if cause:
                raise CheckError(cause)
            last = tail[-1] if tail else 'no output'
            if result['frames']:
                raise CheckError(f'The receiver stopped responding at {gain_label(gain)}. Check the USB '
                                 f'connection and try again. (Decoder said: {last})')
            raise CheckError(f'The decoder stopped at {gain_label(gain)} before listening. Close other SDR apps '
                             f'such as SDR#, reconnect the receiver and try again. (Decoder said: {last})')

    def _listen(self, args, handle, index, result):
        """Run one decoder until the step time is up (or the recording ends).
        Returns True when a live decoder exited on its own before the time was up."""
        try:
            process = subprocess.Popen(args, cwd=str(self.folder), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                       stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='replace',
                                       bufsize=1, creationflags=FLAGS)
        except FileNotFoundError:
            raise CheckError(f'The decoder could not be found ({Path(args[0]).name}). Reinstall AirAlert.')
        except OSError as e:
            raise CheckError(f'The decoder could not start: {e}')
        _adopt(process)
        with self._lock:
            self._process = process
        if self.cancelled:
            _terminate(process, wait=False)
        lines = queue.Queue()
        reader = threading.Thread(target=_read_lines, args=(process.stdout, lines), daemon=True,
                                  name='reception-reader')
        reader.start()
        start = time.monotonic()
        limit = FILE_TIMEOUT if self.infile else self.seconds
        exited = False
        last_report = 0.0
        result['note'] = 'listening'
        try:
            while not self.cancelled:
                now = time.monotonic()
                if now - start >= limit:
                    break
                try:
                    line = lines.get(timeout=0.1)
                except queue.Empty:
                    line = ''
                if line is None:
                    exited = True
                    break
                if line:
                    handle(line)
                if now - last_report >= 0.25:
                    last_report = now
                    self._progress(index, result, min(1.0, (now - start) / self.seconds), limit - (now - start))
        finally:
            self._stop_process()
            reader.join(timeout=3)
            if not self.cancelled:  # lines already read when the time ran out still count
                while True:
                    try:
                        line = lines.get_nowait()
                    except queue.Empty:
                        break
                    if line is None:
                        break
                    handle(line)
            try:
                process.stdout.close()
            except OSError:
                pass
        self._progress(index, result, 1.0, 0)
        return exited and not self.infile and time.monotonic() - start < self.seconds - 0.5

    def _stop_process(self):
        with self._lock:
            process, self._process = self._process, None
        if process is not None:
            _terminate(process)
            _release(process)

    # --------------------------------------------------------- reporting
    def _emit(self, event):
        try:
            self._report(event)
        except Exception:
            log.exception('Reception progress callback failed')

    def _update_shares(self):
        top = max((r['valid'] for r in self.results), default=0)
        for r in self.results:
            r['share'] = r['valid'] / top if top else 0.0

    def noun(self, count=2):
        if self.mode == 'adsb':
            return 'aircraft'
        return 'vessel' if count == 1 else 'vessels'

    def _progress(self, index, result, fraction, remaining):
        total = len(self.gains)
        self._update_shares()
        live = '' if self.infile else f'{max(0, int(round(remaining)))} s left · '
        step = (f"{live}{result['frames']:,} frames · {result['valid']:,} valid · "
                f"{result['targets']:,} {self.noun(result['targets'])}")
        self._emit(dict(type='progress', progress=min(1.0, (index + fraction) / total),
                        status=f"Listening at {result['label']} · gain {index + 1} of {total}",
                        step=step, results=[dict(r) for r in self.results]))

    def _recommendation(self, best):
        current = next((r for r in self.results if r['gain'] == self.current and r['phase'] == 'done'), None)
        count = f"{best['valid']:,} valid message{'s' if best['valid'] != 1 else ''} from " \
                f"{best['targets']:,} {self.noun(best['targets'])}"
        if best['gain'] == self.current:
            text = f"Your current gain already hears the most: {count}. No change needed."
        elif current is None:
            text = f"Decoded {count}, the best of the gains tested."
        elif current['score'] == 0:
            text = f"Decoded {count}, while your current setting ({current['label']}) heard nothing."
        elif current['score'] >= best['score']:
            text = (f"Decoded {count}, the same score as your current setting ({current['label']}). Ties go to the "
                    f"lower gain, which is less likely to be overloaded by strong nearby signals.")
        else:
            better = 100 * (best['score'] - current['score']) / current['score']
            if better < 1:
                text = (f"Decoded {count}, about the same as your current setting ({current['label']}), so keeping "
                        f"it is fine too.")
            elif better < 5:
                text = (f"Decoded {count}, only {round(better)}% better than your current setting "
                        f"({current['label']}), so keeping it is fine too.")
            else:
                text = (f"Decoded {count}, a {round(better)}% better score than your current setting "
                        f"({current['label']}).")
        weak = best['valid'] < FEW_MESSAGES
        if weak:
            text += ' Only a few messages were heard, so repeat the check when traffic is busier to confirm.'
        return dict(gain=best['gain'], label=best['label'], score=best['score'], valid=best['valid'],
                    targets=best['targets'], positions=best['positions'], current=best['gain'] == self.current,
                    weak=weak, text=text)
