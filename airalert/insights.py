"""Statistics behind the Statistics page (UI-independent).

* RateTracker     per-second message counts for the live sparkline and per-minute totals (minute_stats).
* catch_up()      incremental hourly rollups of the positions table (hourly_targets, hourly_coverage) so
                  30-day charts never scan millions of position rows.
* snapshot()      every chart series for a time range, from the rollups, minute_stats and sightings.
Distances are kilometres here; the bridge converts them to display units.
"""
import json
import math
import re
import sqlite3
import time
from collections import Counter, deque
from datetime import datetime, timedelta

from .aircraftdb import TYPE_NAMES

RANGES = {'24h': 86400, '7d': 7 * 86400, '30d': 30 * 86400}
# Rate-history bucket per range (minutes) -> 144, 168 and 180 points.
RATE_BUCKET = {'24h': 10, '7d': 60, '30d': 240}
SECTORS = 36
CHUNK = 20000  # positions rolled up per catch_up() call (a few tens of ms)
OPERATOR_CALLSIGN = re.compile(r'^[A-Z]{3}[0-9]')

# ICAO airline designators -> names for the top-operators chart (callsign prefixes like UAL123).
AIRLINES = {
    'AAL': 'American Airlines', 'UAL': 'United Airlines', 'DAL': 'Delta Air Lines', 'SWA': 'Southwest Airlines',
    'ASA': 'Alaska Airlines', 'JBU': 'JetBlue', 'NKS': 'Spirit Airlines', 'FFT': 'Frontier Airlines',
    'HAL': 'Hawaiian Airlines', 'SKW': 'SkyWest Airlines', 'ENY': 'Envoy Air', 'RPA': 'Republic Airways',
    'EDV': 'Endeavor Air', 'JIA': 'PSA Airlines', 'PDT': 'Piedmont Airlines', 'ASH': 'Mesa Airlines',
    'QXE': 'Horizon Air', 'AAY': 'Allegiant Air', 'SCX': 'Sun Country Airlines', 'MXY': 'Breeze Airways',
    'GJS': 'GoJet Airlines', 'CPZ': 'Compass Airlines', 'FDX': 'FedEx Express', 'UPS': 'UPS Airlines',
    'GTI': 'Atlas Air', 'ABX': 'ABX Air', 'CKS': 'Kalitta Air', 'EJA': 'NetJets', 'LXJ': 'Flexjet',
    'VJT': 'VistaJet', 'RCH': 'U.S. Air Force (Reach)', 'ACA': 'Air Canada', 'WJA': 'WestJet',
    'JZA': 'Jazz Aviation', 'TSC': 'Air Transat', 'POE': 'Porter Airlines', 'AMX': 'Aeroméxico',
    'VOI': 'Volaris', 'VIV': 'Viva Aerobus', 'BAW': 'British Airways', 'VIR': 'Virgin Atlantic',
    'DLH': 'Lufthansa', 'AFR': 'Air France', 'KLM': 'KLM', 'IBE': 'Iberia', 'SWR': 'Swiss', 'AUA': 'Austrian',
    'SAS': 'SAS', 'FIN': 'Finnair', 'EIN': 'Aer Lingus', 'RYR': 'Ryanair', 'EZY': 'easyJet', 'WZZ': 'Wizz Air',
    'THY': 'Turkish Airlines', 'UAE': 'Emirates', 'QTR': 'Qatar Airways', 'ETD': 'Etihad Airways',
    'SIA': 'Singapore Airlines', 'CPA': 'Cathay Pacific', 'JAL': 'Japan Airlines', 'ANA': 'All Nippon Airways',
    'KAL': 'Korean Air', 'AAR': 'Asiana Airlines', 'CAL': 'China Airlines', 'EVA': 'EVA Air', 'CCA': 'Air China',
    'CES': 'China Eastern', 'CSN': 'China Southern', 'QFA': 'Qantas', 'ANZ': 'Air New Zealand',
    'VOZ': 'Virgin Australia', 'LAN': 'LATAM', 'TAM': 'LATAM Brasil', 'AVA': 'Avianca', 'CMP': 'Copa Airlines',
    'ELY': 'El Al', 'SVA': 'Saudia', 'ETH': 'Ethiopian Airlines', 'MSR': 'EgyptAir', 'TAP': 'TAP Air Portugal',
    'AIC': 'Air India', 'PAL': 'Philippine Airlines', 'ICE': 'Icelandair', 'LOT': 'LOT Polish Airlines',
}


def local_day(ts):
    return datetime.fromtimestamp(ts).date()


# ---------------------------------------------------------------------- live rate
class RateTracker:
    """Messages per second for the last `window` seconds and completed per-minute totals."""

    def __init__(self, window=600):
        self.window = window
        self.seconds = deque()  # (unix second, messages) while monitoring
        self.minute = None
        self.acc = None

    def _reset(self, minute):
        self.minute = minute
        self.acc = dict(messages=0, aircraft=0, vessels=0, simulated=False, running=False)

    def add(self, now, messages, running, simulated, aircraft, vessels):
        """Record one refresh. Returns a finished minute_stats row (minute, messages, aircraft, vessels,
        simulated) when a monitored minute has just ended, else None."""
        second = int(now)
        minute = second // 60
        finished = None
        if self.minute is None:
            self._reset(minute)
        elif minute != self.minute:
            finished = self.flush()
            self._reset(minute)
        if running:
            if self.seconds and self.seconds[-1][0] == second:
                self.seconds[-1] = (second, self.seconds[-1][1] + messages)
            else:
                self.seconds.append((second, messages))
            a = self.acc
            a['messages'] += messages
            a['aircraft'] = max(a['aircraft'], aircraft)
            a['vessels'] = max(a['vessels'], vessels)
            a['simulated'] = a['simulated'] or bool(simulated)
            a['running'] = True
        while self.seconds and self.seconds[0][0] <= second - self.window:
            self.seconds.popleft()
        return finished

    def flush(self):
        """The current minute as a row (or None when nothing was monitored); does not reset."""
        a = self.acc
        if not a or not a['running']:
            return None
        return (self.minute, a['messages'], a['aircraft'], a['vessels'], int(a['simulated']))

    def sparkline(self, now, bin_seconds=5):
        """Average messages/second per bin across the window, oldest first; None where not monitoring."""
        end = int(now) + 1
        start = end - self.window
        bins = self.window // bin_seconds
        sums, counts = [0] * bins, [0] * bins
        for second, messages in self.seconds:
            i = (second - start) // bin_seconds
            if 0 <= i < bins:
                sums[i] += messages
                counts[i] += 1
        return [round(s / c, 2) if c else None for s, c in zip(sums, counts)]


def store_minute(conn, row):
    """Add a finished minute to minute_stats (merging with a partial minute from before a restart)."""
    if row:
        with conn:
            conn.execute('''INSERT INTO minute_stats(minute, messages, aircraft, vessels, simulated)
                VALUES (?,?,?,?,?) ON CONFLICT(minute) DO UPDATE SET messages=messages+excluded.messages,
                aircraft=MAX(aircraft, excluded.aircraft), vessels=MAX(vessels, excluded.vessels),
                simulated=MAX(simulated, excluded.simulated)''', row)


# ------------------------------------------------------------------------ rollups
def _ensure_math(conn):
    """SQLite builds without math functions get Python equivalents."""
    try:
        conn.execute('SELECT atan2(1, 1), mod(5, 3), degrees(1), radians(1), pow(2, 2), asin(0.5)').fetchone()
    except sqlite3.OperationalError:
        for name, fn, n in (('atan2', math.atan2, 2), ('mod', math.fmod, 2), ('degrees', math.degrees, 1),
                            ('radians', math.radians, 1), ('pow', math.pow, 2), ('asin', math.asin, 1),
                            ('sin', math.sin, 1), ('cos', math.cos, 1), ('sqrt', math.sqrt, 1)):
            conn.create_function(name, n, fn, deterministic=True)


COVERAGE_SQL = '''
INSERT INTO hourly_coverage(hour, kind, simulated, sector, km)
SELECT hour, kind, simulated, sector, MAX(km) FROM (
    SELECT CAST(time / 3600 AS INTEGER) AS hour,
           CASE WHEN key LIKE 'vessel:%' OR key LIKE 'sim/vessel:%' THEN 'vessel' ELSE 'aircraft' END AS kind,
           simulated,
           CAST(mod(degrees(atan2(sin(radians(lon - :lon)) * cos(radians(lat)),
                                  cos(radians(:lat)) * sin(radians(lat))
                                  - sin(radians(:lat)) * cos(radians(lat)) * cos(radians(lon - :lon)))) + 360.0,
                    360.0) / 10 AS INTEGER) AS sector,
           12742.0176 * asin(min(1.0, sqrt(pow(sin(radians(lat - :lat) / 2), 2)
                + cos(radians(:lat)) * cos(radians(lat)) * pow(sin(radians(lon - :lon) / 2), 2)))) AS km
    FROM positions WHERE id > :lo AND id <= :hi AND lat IS NOT NULL AND lon IS NOT NULL)
WHERE km > 0.05 AND sector BETWEEN 0 AND 35
GROUP BY hour, kind, simulated, sector
ON CONFLICT(hour, kind, simulated, sector) DO UPDATE SET km = MAX(km, excluded.km)
'''


def catch_up(conn, home, limit=CHUNK):
    """Roll up to `limit` new positions into the hourly tables. Returns positions still waiting (0 = current).

    Coverage is measured from the current home; moving home rebuilds it from the stored positions."""
    _ensure_math(conn)
    state = dict(conn.execute('SELECT key, value FROM insights_state').fetchall())
    newest = conn.execute('SELECT MAX(id) FROM positions').fetchone()[0] or 0
    targets_id = int(state.get('targets_id') or 0)
    coverage_id = int(state.get('coverage_id') or 0)
    home_key = f'{home[0]:.5f},{home[1]:.5f}' if home else ''
    with conn:
        if targets_id > newest:  # positions were cleared; ids restarted
            targets_id = 0
        if state.get('coverage_home', '') != home_key or coverage_id > newest:
            conn.execute('DELETE FROM hourly_coverage')
            coverage_id = 0
        if targets_id < newest:
            hi = min(newest, targets_id + limit)
            conn.execute('''INSERT OR IGNORE INTO hourly_targets(hour, key)
                SELECT DISTINCT CAST(time / 3600 AS INTEGER), key FROM positions WHERE id > ? AND id <= ?''',
                         (targets_id, hi))
            targets_id = hi
        if home and coverage_id < newest:
            hi = min(newest, coverage_id + limit)
            conn.execute(COVERAGE_SQL, dict(lat=float(home[0]), lon=float(home[1]), lo=coverage_id, hi=hi))
            coverage_id = hi
        elif not home:
            coverage_id = newest
        records_id = int(state.get('records_id') or 0)
        if records_id > newest:
            records_id = 0
        if records_id < newest:
            hi = min(newest, records_id + limit)
            _update_records(conn, state, records_id, hi)
            records_id = hi
        conn.executemany('INSERT OR REPLACE INTO insights_state(key, value) VALUES (?, ?)',
                         [('targets_id', str(targets_id)), ('coverage_id', str(coverage_id)),
                          ('coverage_home', home_key), ('records_id', str(records_id))])
    return max(newest - targets_id, newest - coverage_id if home else 0, newest - records_id)


# ------------------------------------------------------------------------ records
# Station records are kept as positions are rolled up, so reading them never scans the archive. Each is the best
# value ever recorded (kept even after the position itself is deleted by retention). Loose caps drop garbage values.
RECORD_METRICS = (('farthest', 'distance', 'distance IS NOT NULL', "key NOT LIKE '%vessel:%'"),
                  ('farthest_vessel', 'distance', 'distance IS NOT NULL', "key LIKE '%vessel:%'"),
                  ('highest', 'altitude', 'altitude BETWEEN 1 AND 60000', "key NOT LIKE '%vessel:%'"),
                  ('fastest', 'speed', 'speed BETWEEN 1 AND 1500', "key NOT LIKE '%vessel:%'"))


def _label(conn, key):
    row = conn.execute('SELECT identifier, data FROM sightings WHERE key=?', (key,)).fetchone()
    if not row:
        return key.split(':', 1)[-1]
    try:
        data = json.loads(row[1] or '{}')
    except ValueError:
        data = {}
    return str(data.get('registration') or data.get('name') or data.get('callsign') or row[0])


def _update_records(conn, state, lo, hi):
    for simulated in (0, 1):
        name = f'records_{simulated}'
        try:
            records = json.loads(state.get(name) or '{}')
        except ValueError:
            records = {}
        changed = False
        for metric, column, valid, which in RECORD_METRICS:
            row = conn.execute(f'''SELECT key, time, {column} FROM positions WHERE id > ? AND id <= ? AND simulated=?
                                   AND {valid} AND {which} ORDER BY {column} DESC LIMIT 1''', (lo, hi, simulated)).fetchone()
            if row and row[2] > records.get(metric, {}).get('value', 0):
                records[metric] = dict(value=row[2], time=row[1], key=row[0], label=_label(conn, row[0]))
                changed = True
        if changed:
            state[name] = json.dumps(records)
            conn.execute('INSERT OR REPLACE INTO insights_state(key, value) VALUES (?, ?)', (name, state[name]))


def records(conn, include_sim=False, now=None):
    """Station records: {farthest, farthest_vessel, highest, fastest: dict(value, time, label)} from the rollups,
    plus busiest_hour, busiest_day (distinct aircraft) and peak_rate (messages/second in the busiest minute)."""
    now = time.time() if now is None else now
    state = dict(conn.execute("SELECT key, value FROM insights_state WHERE key LIKE 'records_%'").fetchall())
    out = {}
    for simulated in ((0, 1) if include_sim else (0,)):
        try:
            stored = json.loads(state.get(f'records_{simulated}') or '{}')
        except ValueError:
            stored = {}
        for metric, entry in stored.items():
            if isinstance(entry, dict) and entry.get('value', 0) > out.get(metric, {}).get('value', 0):
                out[metric] = dict(entry, simulated=bool(simulated))
    like = "(key LIKE 'aircraft:%'" + (" OR key LIKE 'sim/aircraft:%')" if include_sim else ')')
    hour = conn.execute(f'SELECT hour, COUNT(*) AS n FROM hourly_targets WHERE {like} GROUP BY hour '
                        'ORDER BY n DESC, hour DESC LIMIT 1').fetchone()
    if hour:
        out['busiest_hour'] = dict(value=hour[1], time=hour[0] * 3600)
    offset = int(datetime.fromtimestamp(now).astimezone().utcoffset().total_seconds())   # today's offset: close enough
    day = conn.execute(f'''SELECT CAST((hour * 3600 + ?) / 86400 AS INTEGER) AS d, COUNT(DISTINCT key) AS n
                           FROM hourly_targets WHERE {like} GROUP BY d ORDER BY n DESC, d DESC LIMIT 1''',
                       (offset,)).fetchone()
    if day:
        out['busiest_day'] = dict(value=day[1], time=day[0] * 86400 - offset)
    peak = conn.execute(f'SELECT minute, messages FROM minute_stats WHERE 1=1{_sim_clause(include_sim)} '
                        'ORDER BY messages DESC LIMIT 1').fetchone()
    if peak and peak[1]:
        out['peak_rate'] = dict(value=round(peak[1] / 60, 1), time=peak[0] * 60)
    return out


# ------------------------------------------------------------------- reception health
HEALTH_WINDOW = 15       # minutes of recent monitoring compared...
HEALTH_DAYS = 14         # ...with the same hour of day over this many earlier days
HEALTH_RATIO = 0.25      # "low" = below a quarter of normal
HEALTH_FLOOR = 3.0       # messages/second: quieter hours than this are too quiet to judge


def reception_health(conn, now=None):
    """dict(status 'ok' | 'low' | 'unknown', current, typical) for received (not simulated) traffic.

    'unknown' when there is too little recent monitoring or too little history for this hour of the day."""
    now = time.time() if now is None else now
    minute = int(now // 60)
    count, total = conn.execute('''SELECT COUNT(*), SUM(messages) FROM minute_stats WHERE simulated=0
                                   AND minute >= ? AND minute < ?''', (minute - HEALTH_WINDOW, minute)).fetchone()
    result = dict(status='unknown', current=None, typical=None)
    if (count or 0) < HEALTH_WINDOW - 3:
        return result
    result['current'] = round((total or 0) / (count * 60), 2)
    offset = int(datetime.fromtimestamp(now).astimezone().utcoffset().total_seconds()) // 60
    local_hour = datetime.fromtimestamp(now).hour
    midnight = minute - ((minute + offset) % 1440)
    rows, messages, days = conn.execute(
        '''SELECT COUNT(*), SUM(messages), COUNT(DISTINCT (minute + ?) / 1440) FROM minute_stats
           WHERE simulated=0 AND minute >= ? AND minute < ? AND ((minute + ?) / 60) % 24 = ?''',
        (offset, midnight - HEALTH_DAYS * 1440, midnight, offset, local_hour)).fetchone()
    if (rows or 0) < 90 or (days or 0) < 3:
        return result
    result['typical'] = round(messages / (rows * 60), 2)
    if result['typical'] < HEALTH_FLOOR:
        return result
    result['status'] = 'low' if result['current'] < HEALTH_RATIO * result['typical'] else 'ok'
    return result


def catch_up_all(conn, home, limit=CHUNK):
    while catch_up(conn, home, limit):
        pass


# ------------------------------------------------------------------------- series
def _sim_clause(include_sim, column='simulated'):
    return '' if include_sim else f' AND {column} = 0'


def rate_series(conn, start, end, include_sim, bucket_minutes):
    """[{t, value}] average messages/second per bucket while monitoring; value None = not monitoring."""
    first, last = int(start // 60) // bucket_minutes, int(end // 60) // bucket_minutes
    rows = conn.execute(f'''SELECT minute / ? AS b, SUM(messages), COUNT(*) FROM minute_stats
        WHERE minute >= ? AND minute <= ?{_sim_clause(include_sim)} GROUP BY b''',
                        (bucket_minutes, first * bucket_minutes, int(end // 60))).fetchall()
    found = {b: (total, minutes) for b, total, minutes in rows}
    points = []
    for b in range(first, last + 1):
        total, minutes = found.get(b, (0, 0))
        points.append(dict(t=b * bucket_minutes * 60, value=round(total / (minutes * 60), 2) if minutes else None))
    totals = conn.execute(f'''SELECT SUM(messages), COUNT(*), MAX(messages) FROM minute_stats
        WHERE minute >= ? AND minute <= ?{_sim_clause(include_sim)}''', (int(start // 60), int(end // 60))).fetchone()
    messages, minutes, peak = totals[0] or 0, totals[1] or 0, totals[2] or 0
    return dict(points=points, messages=messages, minutes=minutes,
                average=round(messages / (minutes * 60), 1) if minutes else None,
                peak=round(peak / 60, 1) if minutes else None)


def _target_hours(conn, start_hour, end_hour, include_sim, kinds=('aircraft', 'vessel')):
    prefixes = [f'{k}:' for k in kinds] + ([f'sim/{k}:' for k in kinds] if include_sim else [])
    like = ' OR '.join('key LIKE ?' for _ in prefixes)
    return conn.execute(f'SELECT hour, key FROM hourly_targets WHERE hour >= ? AND hour <= ? AND ({like})',
                        (start_hour, end_hour, *[p + '%' for p in prefixes])).fetchall()


def hour_of_day(conn, start, end, include_sim):
    """Distinct aircraft seen in each local hour of the day (0-23) over the range."""
    local = {}
    seen = [set() for _ in range(24)]
    for hour, key in _target_hours(conn, int(start // 3600), int(end // 3600), include_sim, ('aircraft',)):
        h = local.get(hour)
        if h is None:
            h = local[hour] = datetime.fromtimestamp(hour * 3600).hour
        seen[h].add(key[4:] if key.startswith('sim/') else key)
    return [len(s) for s in seen]


def daily_counts(conn, now, include_sim, days=14):
    """Unique aircraft and vessels per local day for the last `days` days, oldest first."""
    today = local_day(now)
    first = datetime.combine(today - timedelta(days=days - 1), datetime.min.time())
    dates = [(first + timedelta(days=i)).date() for i in range(days)]
    sets = {d: (set(), set()) for d in dates}
    day_of = {}
    for hour, key in _target_hours(conn, int(first.timestamp() // 3600), int(now // 3600), include_sim):
        d = day_of.get(hour)
        if d is None:
            d = day_of[hour] = local_day(hour * 3600)
        if d in sets:
            plain = key[4:] if key.startswith('sim/') else key
            sets[d][0 if plain.startswith('aircraft:') else 1].add(plain)
    return [dict(date=d.isoformat(), label=d.strftime('%a'), short=f'{d.strftime("%b")} {d.day}', today=d == today,
                 aircraft=len(sets[d][0]), vessels=len(sets[d][1])) for d in dates]


def aircraft_breakdown(conn, start, end, include_sim, limit=8):
    """One pass over the aircraft sightings in the range: (top types, top operators, aircraft count).

    Operators are the 3-letter ICAO airline prefix of callsigns such as UAL123."""
    types, operators = Counter(), Counter()
    total = 0
    for code, callsign in conn.execute(f'''SELECT upper(trim(json_extract(data, '$.type'))),
            upper(trim(json_extract(data, '$.callsign'))) FROM sightings
            WHERE kind = 'aircraft' AND last >= ? AND first <= ?{_sim_clause(include_sim)}''', (start, end)):
        total += 1
        if code:
            types[code] += 1
        if callsign and OPERATOR_CALLSIGN.match(callsign):
            operators[callsign[:3]] += 1

    def top(counter, names):
        ranked = sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))[:limit]
        return [dict(key=k, label=k, detail=names(k), value=n) for k, n in ranked]

    def type_name(code):
        known = TYPE_NAMES.get(code)
        return ' '.join(p for p in known if p) if known else ''
    return top(types, type_name), top(operators, lambda k: AIRLINES.get(k, '')), total


def coverage(conn, start, end, include_sim):
    """Maximum range (km) per 10-degree sector: {'aircraft': [36], 'vessel': [36], 'all': [36]} (None = no data)."""
    result = {k: [None] * SECTORS for k in ('aircraft', 'vessel', 'all')}
    rows = conn.execute(f'''SELECT kind, sector, MAX(km) FROM hourly_coverage WHERE hour >= ? AND hour <= ?
        {_sim_clause(include_sim)} GROUP BY kind, sector''', (int(start // 3600), int(end // 3600))).fetchall()
    for kind, sector, km in rows:
        if kind in result and 0 <= sector < SECTORS:
            result[kind][sector] = round(km, 2)
            current = result['all'][sector]
            result['all'][sector] = round(km, 2) if current is None else max(current, round(km, 2))
    return result


def snapshot(conn, range_key='24h', include_sim=False, now=None):
    """All chart data for the Statistics page."""
    now = time.time() if now is None else now
    span = RANGES.get(range_key, RANGES['24h'])
    start = now - span
    started = time.perf_counter()
    data = dict(range=range_key, includeSim=bool(include_sim), start=start, end=now,
                rate=rate_series(conn, start, now, include_sim, RATE_BUCKET.get(range_key, 10)),
                hours=hour_of_day(conn, start, now, include_sim),
                daily=daily_counts(conn, now, include_sim),
                coverage=coverage(conn, start, now, include_sim))
    data['types'], data['operators'], data['aircraft'] = aircraft_breakdown(conn, start, now, include_sim)
    data['elapsedMs'] = round((time.perf_counter() - started) * 1000, 1)
    return data
