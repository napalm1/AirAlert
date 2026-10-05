"""History playback bridge, exposed to QML as ``playback``.

Loads a recorded time window on a worker thread (its own read-only SQLite
connection, so the interface stays responsive), then replaces the controller's
live targets with the playback targets and drives the display clock
(``scene_extras['now']``) with a ~10 Hz timer.
"""
import logging
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from time import monotonic, time as wall_time

from PySide6.QtCore import QObject, QTimer, Property, Signal, Slot

from ..playback import MAX_ROWS, PlaybackData, auto_speed
from .controller import _parse_time

log = logging.getLogger(__name__)

SPEEDS = (1, 10, 60, 300, 1200)
FRAME_MS = 100
QUIET_SKIP_SECONDS = 120   # while playing, jump over gaps longer than this with nothing on the map


def _stamp(ts, fmt):
    return datetime.fromtimestamp(ts).strftime(fmt) if ts else ''


class PlaybackBridge(QObject):
    stateChanged = Signal()
    timeChanged = Signal()

    def __init__(self, controller):
        super().__init__(controller)
        self.controller = controller
        self._data = None
        self._active = False
        self._playing = False
        self._loading = False
        self._time = 0.0
        self._speed = 60
        self._count = 0
        self._clock = None
        self._job = None
        self._timer = QTimer(self)
        self._timer.setInterval(FRAME_MS)
        self._timer.timeout.connect(self._advance)
        self._poll = QTimer(self)
        self._poll.setInterval(40)
        self._poll.timeout.connect(self._check_loaded)

    # ------------------------------------------------------------ properties
    active = Property(bool, lambda self: self._active, notify=stateChanged)
    playing = Property(bool, lambda self: self._playing, notify=stateChanged)
    loading = Property(bool, lambda self: self._loading, notify=stateChanged)
    speed = Property(int, lambda self: self._speed, notify=stateChanged)
    speeds = Property('QVariantList', lambda self: list(SPEEDS), constant=True)
    truncated = Property(bool, lambda self: bool(self._data and self._data.truncated), notify=stateChanged)
    start = Property(float, lambda self: self._data.start if self._data else 0.0, notify=stateChanged)
    end = Property(float, lambda self: self._data.end if self._data else 0.0, notify=stateChanged)
    time = Property(float, lambda self: self._time, notify=timeChanged)
    targetCount = Property(int, lambda self: self._count, notify=timeChanged)
    timeText = Property(str, lambda self: _stamp(self._time, '%H:%M:%S'), notify=timeChanged)
    dateText = Property(str, lambda self: _stamp(self._time, '%a %d %b %Y'), notify=timeChanged)

    def _range_text(self):
        data = self._data
        if not data:
            return ''
        a, b = datetime.fromtimestamp(data.start), datetime.fromtimestamp(data.end)
        text = f"{a:%b %d %H:%M} – {b:%H:%M}" if a.date() == b.date() else f"{a:%b %d %H:%M} – {b:%b %d %H:%M}"
        return f'{text} · {len(data):,} target{"s" if len(data) != 1 else ""}'

    rangeText = Property(str, _range_text, notify=stateChanged)
    limitText = Property(str, lambda self: f'Limited to the first {MAX_ROWS:,} positions' if self.truncated else '',
                         notify=stateChanged)

    # ----------------------------------------------------------------- slots
    @Slot(str, str, bool)
    def openRange(self, from_text, to_text, include_simulated):
        try:
            start = _parse_time(from_text, 0)
            end = _parse_time(to_text, wall_time())
        except ValueError as e:
            self.controller.toast.emit(str(e), 'warning')
            return
        self.open(start, end, include_simulated)

    @Slot(float, float, bool)
    def open(self, start, end, include_simulated=True):
        if self._loading:
            return
        if end <= start:
            self.controller.toast.emit('The start time must be before the end time.', 'warning')
            return
        self._pause()
        self._loading = True
        self.stateChanged.emit()
        job = dict(result=None, error='', done=threading.Event())
        self._job = job
        path = getattr(self.controller.station.db, 'path', None)
        if path is None or str(path) == ':memory:':
            self._load(job, None, start, end, include_simulated)
        else:
            threading.Thread(target=self._load, args=(job, str(path), start, end, include_simulated),
                             name='history-playback', daemon=True).start()
        self._poll.start()
        self._check_loaded()

    def _load(self, job, path, start, end, include_simulated):
        conn = None
        try:
            if path is None:
                job['result'] = PlaybackData.load(self.controller.station.db.conn, start, end, include_simulated)
            else:
                try:
                    conn = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True, timeout=5)
                except sqlite3.Error:
                    conn = sqlite3.connect(path, timeout=5)
                job['result'] = PlaybackData.load(conn, start, end, include_simulated)
        except (sqlite3.Error, OSError, ValueError) as e:
            log.exception('Could not load history for playback')
            job['error'] = str(e)
        finally:
            if conn is not None:
                conn.close()
            job['done'].set()

    def wait_loaded(self, timeout=30):
        """Block until a pending load finishes and apply it (tests)."""
        if self._job is not None:
            self._job['done'].wait(timeout)
        self._check_loaded()

    def _check_loaded(self):
        job = self._job
        if job is None or not job['done'].is_set():
            return
        self._job = None
        self._poll.stop()
        self._loading = False
        data = job['result']
        if job['error'] or data is None:
            self.controller.toast.emit(f'Could not load recorded history: {job["error"] or "unknown error"}', 'error')
            self.stateChanged.emit()
            return
        if not len(data):
            self.controller.toast.emit('No recorded positions in that period. Try a wider date range.', 'info')
            self.stateChanged.emit()
            return
        self._data = data
        self._speed = auto_speed(data.end - data.start, SPEEDS)
        self._active = True
        controller = self.controller
        controller.target_provider = self._targets
        controller.requestPage.emit(0)
        self._set_time(data.start, push=False)
        controller.refresh()
        self.stateChanged.emit()
        self._play()
        note = ' (first 400,000 positions only)' if data.truncated else ''
        controller.toast.emit(f'Replaying {len(data):,} recorded targets{note}. Space pauses.', 'info')

    @Slot()
    def close(self):
        if not self._active and not self._loading:
            return
        self._pause()
        self._job = None
        self._poll.stop()
        self._loading = False
        self._active = False
        self._data = None
        self._count = 0
        controller = self.controller
        controller.target_provider = None
        controller.scene_extras.pop('now', None)
        selected = controller.property('selectedKey')
        if selected and selected not in controller.station.store.targets:
            controller.clearSelection()
        controller.refresh()
        self.stateChanged.emit()
        self.timeChanged.emit()

    @Slot()
    def togglePlay(self):
        if not self._active:
            return
        if self._playing:
            self._pause()
        else:
            if self._time >= self._data.end - 0.5:
                self._set_time(self._data.start)
            self._play()
        self.stateChanged.emit()

    @Slot(int)
    def setSpeed(self, speed):
        speed = int(speed)
        if speed in SPEEDS and speed != self._speed:
            self._speed = speed
            self.stateChanged.emit()

    @Slot(float)
    def seek(self, t):
        if self._active:
            self._set_time(t)

    @Slot(float)
    def step(self, seconds):
        if self._active:
            self._set_time(self._time + seconds)

    # -------------------------------------------------------------- internals
    def _targets(self):
        if self._data is None:
            return []
        return self._data.targets_at(self._time, self.controller.config['trail_minutes'])

    def _play(self):
        if not self._playing:
            self._playing = True
            self._clock = monotonic()
            self._timer.start()
            self.stateChanged.emit()

    def _pause(self):
        if self._playing:
            self._playing = False
            self._timer.stop()
            self.stateChanged.emit()

    def _advance(self):
        if not self._active:
            self._pause()
            return
        now = monotonic()
        elapsed, self._clock = min(1.0, now - self._clock), now
        t = self._time + elapsed * self._speed
        if not self._count:
            # Nothing on the map: skip long quiet stretches instead of replaying an empty map.
            upcoming = self._data.next_sample_after(self._time)
            if upcoming is not None and upcoming - t > QUIET_SKIP_SECONDS:
                t = upcoming - 1
        if t >= self._data.end:
            t = self._data.end
            self._pause()
        self._set_time(t)

    def _set_time(self, t, push=True):
        data = self._data
        self._time = max(data.start, min(data.end, float(t)))
        self.controller.scene_extras['now'] = self._time
        self._count = len(self._targets())
        if push:
            self.controller.push_scene()
        self.timeChanged.emit()


def create(controller):
    return PlaybackBridge(controller)
