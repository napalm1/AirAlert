import csv
import json
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from xml.sax.saxutils import escape

AIRCRAFT_FIELDS = ('registration', 'type', 'model', 'manufacturer', 'operator')
AIRCRAFT_FILE = 'aircraft.sqlite'
# Indexed expressions: queries must use exactly these texts for SQLite to use the indexes.
TYPE_EXPR = "json_extract(data, '$.type')"
OPERATOR_EXPR = "substr(json_extract(data, '$.callsign'), 1, 3)"


class Database:
    def __init__(self, path, aircraft_path=None):
        self.path = Path(path)
        self.conn = sqlite3.connect(path, timeout=3)
        self.conn.row_factory = sqlite3.Row
        # WAL with synchronous=NORMAL never corrupts on an application crash and only risks the last few
        # commits on a power cut, while saving an fsync per commit (several a second while monitoring).
        self.conn.executescript('''
        PRAGMA journal_mode=WAL;
        PRAGMA synchronous=NORMAL;
        PRAGMA cache_size=-16384;
        CREATE TABLE IF NOT EXISTS sightings (
            key TEXT PRIMARY KEY, kind TEXT, identifier TEXT, first REAL, last REAL,
            data TEXT, simulated INTEGER, encounters INTEGER DEFAULT 1);
        CREATE INDEX IF NOT EXISTS sightings_last ON sightings(last);
        CREATE TABLE IF NOT EXISTS positions (
            id INTEGER PRIMARY KEY, key TEXT, time REAL, lat REAL, lon REAL,
            altitude REAL, speed REAL, distance REAL, simulated INTEGER);
        CREATE INDEX IF NOT EXISTS positions_key_time ON positions(key,time);
        CREATE INDEX IF NOT EXISTS positions_time ON positions(time);
        CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY, time REAL, category TEXT, key TEXT, message TEXT, simulated INTEGER);
        CREATE INDEX IF NOT EXISTS events_time ON events(time);
        CREATE INDEX IF NOT EXISTS events_category_time ON events(category, time);
        CREATE TABLE IF NOT EXISTS sessions (id INTEGER PRIMARY KEY, start REAL, end REAL, mode TEXT);
        CREATE TABLE IF NOT EXISTS aircraft (icao TEXT PRIMARY KEY, registration TEXT, type TEXT);
        -- Statistics (airalert/insights.py). minute = unix time // 60, hour = unix time // 3600.
        CREATE TABLE IF NOT EXISTS minute_stats (
            minute INTEGER PRIMARY KEY, messages INTEGER, aircraft INTEGER, vessels INTEGER, simulated INTEGER);
        -- Hourly rollups of positions keep 30-day charts fast on large archives.
        -- key is the sightings key ('sim/' prefix = simulation).
        CREATE TABLE IF NOT EXISTS hourly_targets (hour INTEGER, key TEXT, PRIMARY KEY(hour, key)) WITHOUT ROWID;
        CREATE TABLE IF NOT EXISTS hourly_coverage (
            hour INTEGER, kind TEXT, simulated INTEGER, sector INTEGER, km REAL,
            PRIMARY KEY(hour, kind, simulated, sector)) WITHOUT ROWID;
        CREATE TABLE IF NOT EXISTS insights_state (key TEXT PRIMARY KEY, value TEXT);
        ''')
        if 'rule' not in {r[1] for r in self.conn.execute('PRAGMA table_info(events)')}:
            self.conn.execute('ALTER TABLE events ADD COLUMN rule TEXT')   # which alert rule fired (alerts only)
        try:  # lookups by registration, type and airline prefix without scanning every stored record
            self.conn.execute('CREATE INDEX IF NOT EXISTS sightings_registration '
                              "ON sightings(json_extract(data, '$.registration') COLLATE NOCASE)")
            self.conn.execute(f'CREATE INDEX IF NOT EXISTS sightings_type ON sightings({TYPE_EXPR})')
            self.conn.execute(f'CREATE INDEX IF NOT EXISTS sightings_operator ON sightings({OPERATOR_EXPR})')
        except sqlite3.Error:
            pass  # an SQLite build without JSON support: lookups still work, just by scanning
        self.samples = {}
        # Bundled or OpenSky-updated aircraft database, opened read-only (airalert/aircraftdb.py).
        self.aircraft_path = Path(aircraft_path) if aircraft_path else self.path.parent / AIRCRAFT_FILE
        self._aircraft = None
        self._aircraft_checked = None
        self._lookup_cache = {}

    @staticmethod
    def dbkey(t):
        return ('sim/' if t.simulated else '')+t.key

    def record(self, t, distance=None, sample_seconds=10, new=False):
        key = self.dbkey(t)
        self.conn.execute('''INSERT INTO sightings VALUES (?,?,?,?,?,?,?,1)
            ON CONFLICT(key) DO UPDATE SET last=excluded.last, data=excluded.data,
            encounters=sightings.encounters+?''',
            (key, t.kind, t.identifier, t.first_seen, t.last_seen, json.dumps(t.data), int(t.simulated), int(new)))
        last = self.samples.get(key, (0, None))
        if t.position and t.position_time == t.last_seen and (t.last_seen-last[0] >= sample_seconds) and (t.position != last[1] or t.last_seen-last[0] >= 60):
            self.conn.execute('INSERT INTO positions(key,time,lat,lon,altitude,speed,distance,simulated) VALUES(?,?,?,?,?,?,?,?)',
                (key,t.last_seen,*t.position,t.data.get('altitude'),t.data.get('speed'),distance,int(t.simulated)))
            self.samples[key] = (t.last_seen, t.position)

    def event(self, category, key, message, simulated=False, now=None, rule=None):
        self.conn.execute('INSERT INTO events(time,category,key,message,simulated,rule) VALUES(?,?,?,?,?,?)',
                          (now or time.time(), category, key, message, int(simulated), rule))

    def rule_activity(self, now=None, days=14):
        """How often each alert rule fired: {rule id: dict(total, day, week, last, daily=[days counts, oldest first])}."""
        now = time.time() if now is None else now
        midnight = datetime.fromtimestamp(now).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        first_day = midnight - (days - 1) * 86400
        out = {}
        for rule, total, last, day, week in self.conn.execute(
                '''SELECT rule, COUNT(*), MAX(time), SUM(time >= ?), SUM(time >= ?) FROM events
                   WHERE category='alert' AND rule IS NOT NULL GROUP BY rule''', (now - 86400, now - 7 * 86400)):
            out[rule] = dict(total=total, last=last, day=day or 0, week=week or 0, daily=[0] * days)
        for rule, index, count in self.conn.execute(
                '''SELECT rule, CAST((time - ?) / 86400 AS INTEGER), COUNT(*) FROM events
                   WHERE category='alert' AND rule IS NOT NULL AND time >= ? GROUP BY 1, 2''', (first_day, first_day)):
            if rule in out and 0 <= index < days:
                out[rule]['daily'][index] = count
        return out

    def type_history(self, type_code, simulated, exclude_key, since):
        """(sightings ever, sightings since `since`) of an aircraft type, not counting one sighting key."""
        ever, recent = self.conn.execute(
            f'''SELECT COUNT(*), SUM(last >= ?) FROM sightings WHERE {TYPE_EXPR} = ? AND kind='aircraft'
                AND simulated=? AND key <> ?''', (since, type_code, int(simulated), exclude_key)).fetchone()
        return ever or 0, recent or 0

    def operator_seen(self, prefix, simulated, exclude_key):
        """True when another aircraft sighting has a callsign starting with this three-letter airline code."""
        return self.conn.execute(
            f'''SELECT 1 FROM sightings WHERE {OPERATOR_EXPR} = ? AND kind='aircraft' AND simulated=? AND key <> ?
                LIMIT 1''', (prefix, int(simulated), exclude_key)).fetchone() is not None

    def sighting_exists(self, key):
        return self.conn.execute('SELECT 1 FROM sightings WHERE key=?', (key,)).fetchone() is not None

    def history(self, query='', kind='any', start=0, end=1e20, distance=None, limit=5000):
        sql = 'SELECT * FROM sightings s WHERE last>=? AND first<=?'
        args = [start, end]
        if query:  # with no text the newest rows come straight off the `last` index
            sql += ' AND (identifier LIKE ? OR data LIKE ?)'
            args += [f'%{query}%', f'%{query}%']
        if kind != 'any':
            sql += ' AND kind=?'
            args.append(kind)
        if distance is not None:
            sql += ' AND EXISTS(SELECT 1 FROM positions p WHERE p.key=s.key AND p.time BETWEEN ? AND ? AND p.distance<=?)'
            args += [start, end, distance]
        sql += ' ORDER BY last DESC LIMIT ?'
        return [dict(r) for r in self.conn.execute(sql, args+[limit])]

    def track(self, key, start=0, end=1e20, limit=20000):
        return [dict(r) for r in self.conn.execute('SELECT * FROM positions WHERE key=? AND time BETWEEN ? AND ? ORDER BY time LIMIT ?', (key,start,end,limit))]

    def events(self, query='', category='any', start=0, end=1e20):
        sql, args = 'SELECT * FROM events WHERE time BETWEEN ? AND ?', [start, end]
        if query:
            sql += ' AND (message LIKE ? OR key LIKE ?)'
            args += [f'%{query}%', f'%{query}%']
        if category != 'any':
            sql += ' AND category=?'
            args.append(category)
        return [dict(r) for r in self.conn.execute(sql + ' ORDER BY time DESC LIMIT 5000', args)]

    def sightings_of(self, kind, identifier):
        """(last seen, total encounters) over every sighting of one identifier or registration, real and simulated."""
        identifier = str(identifier).strip()
        ident = identifier.upper()
        last, encounters = self.conn.execute(
            '''SELECT MAX(last), SUM(encounters) FROM sightings WHERE kind=? AND (key IN (?, ?)
               OR json_extract(data, '$.registration') = ? COLLATE NOCASE)''',
            (kind, f'{kind}:{ident}', f'sim/{kind}:{ident}', identifier)).fetchone()
        return last or 0, encounters or 0

    def worth_vacuuming(self):
        """True when a quarter or more of the file (and at least 8 MB) is free pages that VACUUM would give back."""
        free = self.conn.execute('PRAGMA freelist_count').fetchone()[0]
        pages = self.conn.execute('PRAGMA page_count').fetchone()[0]
        size = self.conn.execute('PRAGMA page_size').fetchone()[0]
        return pages > 0 and free * size >= 8 * 1024 ** 2 and free * 4 >= pages

    def maintenance(self, cutoff, vacuum='auto'):
        """Delete records older than cutoff. VACUUM rewrites the whole file (seconds for a big archive), so it
        runs only when worthwhile ('auto'), always (True) or never (False)."""
        with self.conn:
            self.conn.execute('DELETE FROM positions WHERE time<?', (cutoff,))
            self.conn.execute('DELETE FROM events WHERE time<?', (cutoff,))
            self.conn.execute('DELETE FROM sightings WHERE last<?', (cutoff,))
            self.conn.execute('DELETE FROM sessions WHERE end<?', (cutoff,))
            self.conn.execute('DELETE FROM minute_stats WHERE minute<?', (int(cutoff // 60),))
            self.conn.execute('DELETE FROM hourly_targets WHERE hour<?', (int(cutoff // 3600),))
            self.conn.execute('DELETE FROM hourly_coverage WHERE hour<?', (int(cutoff // 3600),))
        if vacuum is True or (vacuum == 'auto' and self.worth_vacuuming()):
            self.conn.execute('VACUUM')
        # In WAL mode the vacuumed file only replaces the old one at a checkpoint.
        self.conn.execute('PRAGMA wal_checkpoint(TRUNCATE)')

    def import_aircraft(self, path):
        n = 0
        with open(path, newline='', encoding='utf-8-sig') as f, self.conn:
            for row in csv.DictReader(f):
                row = {k.strip().lower():v.strip() for k,v in row.items() if k}
                icao = row.get('icao') or row.get('icao24', '')
                if len(icao) == 6 and all(c in '0123456789abcdefABCDEF' for c in icao):
                    self.conn.execute('INSERT OR REPLACE INTO aircraft VALUES(?,?,?)', (icao.upper(),row.get('registration',''),row.get('type') or row.get('typecode','')))
                    n += 1
        self._lookup_cache.clear()
        return n

    # ------------------------------------------------------------ aircraft lookup
    def _aircraft_conn(self):
        """Read-only connection to the aircraft database; a missing file is re-checked every 30 s."""
        if self._aircraft is None and (self._aircraft_checked is None or time.monotonic() - self._aircraft_checked > 30):
            self._aircraft_checked = time.monotonic()
            if self.aircraft_path.is_file():
                try:
                    conn = sqlite3.connect(self.aircraft_path.resolve().as_uri() + '?mode=ro', uri=True, timeout=1,
                                           check_same_thread=False)
                    conn.execute('SELECT icao FROM aircraft LIMIT 1').fetchall()
                    self._aircraft = conn
                    self._lookup_cache.clear()
                except sqlite3.Error:
                    self._aircraft = None
        return self._aircraft

    def close_aircraft(self):
        """Release the aircraft database file (e.g. before it is replaced) and forget cached lookups."""
        if self._aircraft is not None:
            self._aircraft.close()
        self._aircraft = None
        self._aircraft_checked = None
        self._lookup_cache.clear()

    def lookup(self, icao):
        """Registration, type, model, manufacturer and operator for an ICAO address (non-empty keys only).

        Mappings imported from a CSV take precedence over the aircraft database."""
        icao = str(icao).strip().upper()
        cached = self._lookup_cache.get(icao)
        if cached is not None:
            return dict(cached)
        r = self.conn.execute('SELECT registration,type FROM aircraft WHERE icao=?', (icao,)).fetchone()
        if r is not None:
            result = {k: v for k, v in dict(r).items() if v}
        else:
            conn = self._aircraft_conn()
            if conn is None:
                return {}  # not cached: the database may appear later
            try:
                row = conn.execute('SELECT registration,type,model,manufacturer,operator FROM aircraft WHERE icao=?',
                                   (icao,)).fetchone()
            except sqlite3.Error:
                return {}
            result = {k: v for k, v in zip(AIRCRAFT_FIELDS, row) if v} if row else {}
        if len(self._lookup_cache) >= 20000:
            self._lookup_cache.clear()
        self._lookup_cache[icao] = result
        return dict(result)

    def close(self):
        self.close_aircraft()
        self.conn.commit()
        self.conn.close()


def export_kml(path, rows, name='AirAlert track'):
    """Write recorded positions (dicts with time, lat, lon and optional altitude in feet) as a Google Earth track."""
    points = [r for r in rows if r.get('lat') is not None and r.get('lon') is not None]
    if not points:
        raise ValueError('There are no positions to export.')
    airborne = all(r.get('altitude') is not None for r in points)

    def coordinate(r):
        return f"{r['lon']:.6f},{r['lat']:.6f},{(r['altitude'] * 0.3048 if airborne else 0):.0f}"

    def when(r):
        return datetime.fromtimestamp(r['time'], timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')

    def pin(label, r):
        return (f'<Placemark><name>{escape(label)}</name><TimeStamp><when>{when(r)}</when></TimeStamp>'
                f'<Point>{mode}<coordinates>{coordinate(r)}</coordinates></Point></Placemark>')
    mode = '<altitudeMode>absolute</altitudeMode>' if airborne else '<altitudeMode>clampToGround</altitudeMode>'
    title = escape(str(name))
    text = f'''<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2"><Document><name>{title}</name>
<Style id="track"><LineStyle><color>ff00b4ff</color><width>3</width></LineStyle><PolyStyle><color>3300b4ff</color></PolyStyle></Style>
<Placemark><name>{title}</name><description>{len(points)} positions recorded by AirAlert</description><styleUrl>#track</styleUrl>
<TimeSpan><begin>{when(points[0])}</begin><end>{when(points[-1])}</end></TimeSpan>
<LineString>{'<extrude>1</extrude>' if airborne else '<tessellate>1</tessellate>'}{mode}<coordinates>
{' '.join(coordinate(r) for r in points)}
</coordinates></LineString></Placemark>
{pin('Start', points[0])}
{pin('End', points[-1])}
</Document></kml>
'''
    Path(path).write_text(text, 'utf-8')


def export_rows(path, rows):
    p = Path(path)
    if p.suffix.lower() == '.json':
        p.write_text(json.dumps(rows, indent=2), 'utf-8')
    else:
        with p.open('w', newline='', encoding='utf-8-sig') as f:
            if rows:
                writer = csv.DictWriter(f, fieldnames=list(rows[0]))
                writer.writeheader()
                # Escape spreadsheet formulas in untrusted transmitted names/callsigns.
                writer.writerows({k:("'"+v if isinstance(v,str) and v.startswith(('=','+','-','@')) else v) for k,v in row.items()} for row in rows)
