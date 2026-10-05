"""History playback: recorded positions replayed as map targets.

``PlaybackData.load`` reads one time window of the ``positions`` table (plus
the matching ``sightings`` rows for identity and last-known details) into
per-target sorted arrays. ``targets_at(t)`` then builds ``Target`` objects for
the playback clock using bisection, reusing one object per target and only
touching it when its sample or trail window changes, so a frame stays cheap.

Rules at playback time ``t``:
- position = last recorded sample at or before ``t``; hidden after ``MAX_GAP``
  seconds without a newer sample (or before the first sample);
- trail = samples in ``[t - trail_minutes*60, t]`` (``trail_minutes`` 0 = the
  whole loaded window);
- data = the sighting's stored details merged with the sample's position,
  altitude and speed. Stored headings are the final values of the sighting, so
  the direction of travel is derived from consecutive samples (speed too when
  the sample has none);
- position_time = last_seen = sample time, first_seen = first sample.
"""
import json
from bisect import bisect_left, bisect_right
from collections import deque

from .core.models import Target, distance_bearing

MAX_ROWS = 400_000
MAX_GAP = 300.0          # seconds a recorded position stays on the map without a newer one
KNOTS_PER_KMH = 1 / 1.852
PER_SAMPLE_FIELDS = ('lat', 'lon', 'altitude', 'speed', 'heading', 'course', 'vertical_speed')


class Track:
    """One target's samples in time order and its reusable playback Target."""
    __slots__ = ('key', 'kind', 'identifier', 'simulated', 'details', 'times', 'lats', 'lons', 'altitudes',
                 'speeds', 'target', 'sample', 'window')

    def __init__(self, key, kind, identifier, simulated, details):
        self.key = key
        self.kind = kind
        self.identifier = identifier
        self.simulated = simulated
        self.details = {k: v for k, v in details.items() if k not in PER_SAMPLE_FIELDS}
        self.times, self.lats, self.lons, self.altitudes, self.speeds = [], [], [], [], []
        self.target = None
        self.sample = -1
        self.window = None

    def motion(self, i):
        """(track degrees, speed knots) from the samples around i, or (None, None)."""
        times, n = self.times, len(self.times)
        for a, b in ((i - 1, i), (i, i + 1)):
            if a < 0 or b >= n:
                continue
            dt = times[b] - times[a]
            if not 0 < dt <= MAX_GAP:
                continue
            km, bearing = distance_bearing((self.lats[a], self.lons[a]), (self.lats[b], self.lons[b]))
            if km >= 0.01:
                return bearing, km / (dt / 3600) * KNOTS_PER_KMH
        return None, None

    def target_at(self, i, t, trail_minutes):
        target = self.target
        if target is None:
            target = self.target = Target(self.kind, self.identifier, self.times[0], self.times[i],
                                          dict(self.details), simulated=self.simulated)
        if self.sample != i:
            self.sample = i
            data = target.data
            data['lat'], data['lon'] = self.lats[i], self.lons[i]
            altitude, speed = self.altitudes[i], self.speeds[i]
            track, derived_speed = self.motion(i)
            if speed is None:
                speed = derived_speed
            for key, value in (('altitude', altitude), ('speed', speed)):
                if value is None:
                    data.pop(key, None)
                else:
                    data[key] = value
            for key in (('heading', 'course') if self.kind == 'vessel' else ('heading',)):
                if track is None:
                    data.pop(key, None)
                else:
                    data[key] = round(track, 1)
            target.last_seen = target.position_time = self.times[i]
        lo = bisect_left(self.times, t - trail_minutes * 60) if trail_minutes else 0
        if self.window != (lo, i):
            self.window = (lo, i)
            target.trail = deque(zip(self.times[lo:i + 1], self.lats[lo:i + 1], self.lons[lo:i + 1]), maxlen=7200)
        return target


class PlaybackData:
    def __init__(self, tracks, start, end, rows=0, truncated=False):
        self.tracks = tracks
        self.start = start
        self.end = end
        self.rows = rows
        self.truncated = truncated
        self._cache = (None, [])

    @classmethod
    def load(cls, conn, start, end, include_simulated=True, max_rows=MAX_ROWS):
        """Read positions in [start, end] (oldest first, at most max_rows) and their sightings."""
        cursor = conn.cursor()
        cursor.row_factory = None  # plain tuples: several times faster than sqlite3.Row for bulk reads
        sql = 'SELECT key,time,lat,lon,altitude,speed,simulated FROM positions WHERE time BETWEEN ? AND ?'
        if not include_simulated:
            sql += ' AND simulated=0'
        sql += ' ORDER BY time LIMIT ?'
        rows = cursor.execute(sql, (start, end, max_rows + 1)).fetchall()
        truncated = len(rows) > max_rows
        if truncated:
            rows = rows[:max_rows]
        by_key = {}
        for key, ts, lat, lon, altitude, speed, simulated in rows:
            if lat is None or lon is None:
                continue
            columns = by_key.get(key)
            if columns is None:
                columns = by_key[key] = ([], [], [], [], [], bool(simulated))
            columns[0].append(ts)
            columns[1].append(lat)
            columns[2].append(lon)
            columns[3].append(altitude)
            columns[4].append(speed)
        sightings = {}
        keys = list(by_key)
        for chunk in range(0, len(keys), 500):
            part = keys[chunk:chunk + 500]
            query = f'SELECT key,kind,identifier,data,simulated FROM sightings WHERE key IN ({",".join("?" * len(part))})'
            for key, kind, identifier, data, simulated in cursor.execute(query, part):
                try:
                    details = json.loads(data) if data else {}
                except ValueError:
                    details = {}
                sightings[key] = (kind, identifier, details if isinstance(details, dict) else {}, bool(simulated))
        tracks = {}
        for key, (times, lats, lons, altitudes, speeds, simulated) in by_key.items():
            if key in sightings:
                kind, identifier, details, simulated = sightings[key]
            else:  # sighting removed by maintenance: recover identity from the key
                kind, _, identifier = key.split('/')[-1].partition(':')
                details = {}
            if kind not in ('aircraft', 'vessel') or not identifier:
                continue
            track = Track(key, kind, str(identifier).upper(), simulated, details)
            track.times, track.lats, track.lons, track.altitudes, track.speeds = times, lats, lons, altitudes, speeds
            target_key = f'{track.kind}:{track.identifier}'
            other = tracks.get(target_key)
            # A real and a simulated sighting of the same identity: keep the real one.
            if other is None or (other.simulated and not simulated) or \
                    (other.simulated == simulated and len(times) > len(other.times)):
                tracks[target_key] = track
        tracks = sorted(tracks.values(), key=lambda tr: tr.times[0])
        if tracks:
            first = min(tr.times[0] for tr in tracks)
            last = max(tr.times[-1] for tr in tracks)
        else:
            first, last = start, end
        return cls(tracks, first, last, len(rows), truncated)

    def __len__(self):
        return len(self.tracks)

    def targets_at(self, t, trail_minutes=15):
        """Targets visible at playback time t (same objects every call, updated in place)."""
        if self._cache[0] == (t, trail_minutes):
            return self._cache[1]
        result = []
        for track in self.tracks:
            times = track.times
            if t < times[0]:
                break  # tracks are sorted by their first sample
            if t - times[-1] > MAX_GAP:
                continue
            i = bisect_right(times, t) - 1
            if t - times[i] > MAX_GAP:
                continue
            result.append(track.target_at(i, t, trail_minutes))
        self._cache = ((t, trail_minutes), result)
        return result

    def next_sample_after(self, t):
        """Earliest recorded sample time strictly after t (for skipping quiet gaps), or None."""
        best = None
        for track in self.tracks:
            i = bisect_right(track.times, t)
            if i < len(track.times) and (best is None or track.times[i] < best):
                best = track.times[i]
        return best


def auto_speed(duration, speeds=(1, 10, 60, 300, 1200), target_seconds=600):
    """Slowest playback speed that replays the window in about target_seconds or less."""
    for speed in speeds:
        if duration / speed <= target_seconds:
            return speed
    return speeds[-1]
