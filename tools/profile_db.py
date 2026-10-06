"""Speed profile of everything that scales with the size of the history database.

Builds a database like a few months of use (default 20,000 sightings, 400,000 positions, 60,000 events,
30 days of per-minute rate rows), then times startup maintenance, the Statistics and Watchlist queries,
History searches and the playback loader.

    .venv\\Scripts\\python.exe tools\\profile_db.py [sightings]
"""
import json
import os
import random
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
HOME = [34.05, -118.25]
SIGHTINGS = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 20000
POSITIONS_PER = 20


def timed(label, fn):
    started = time.perf_counter()
    result = fn()
    print(f'{label:58s} {(time.perf_counter() - started) * 1000:10.1f} ms')
    return result


def build(folder):
    from airalert.core.config import Config
    from airalert.core.database import Database
    from airalert.core.models import destination
    config = Config(folder)
    config.values.update(home=HOME, setup_done=True, retention_days=365, sample_seconds=10,
                         watchlist=[dict(kind='aircraft', name='Club', identifier='N123AB', notes=''),
                                    dict(kind='aircraft', name='Ambulance', identifier='A1B2C3', notes=''),
                                    dict(kind='vessel', name='Ferry', identifier='366999712', notes='')])
    config.save()
    db = Database(config.folder / 'history.sqlite')
    rnd = random.Random(3)
    now = time.time()
    types = ['B738', 'A320', 'C172', 'E75L', 'B77W', 'PA28', 'SR22', 'CRJ9', 'A21N', 'GLF4']
    airlines = ['UAL', 'DAL', 'SWA', 'AAL', 'ASA', 'SKW', 'FDX', 'UPS']
    sightings, positions = [], []
    for i in range(SIGHTINGS):
        first = now - rnd.uniform(0, 30 * 86400)
        icao = f'{rnd.randrange(0xA00000, 0xAFFFFF):06X}'
        data = dict(callsign=f'{rnd.choice(airlines)}{rnd.randrange(1, 9999)}', type=rnd.choice(types),
                    registration=f'N{rnd.randrange(100, 999)}{rnd.choice("ABCDEFGH")}{rnd.choice("ABCDEFGH")}',
                    altitude=rnd.randrange(500, 40000), speed=rnd.randrange(80, 520), heading=rnd.randrange(360),
                    lat=HOME[0], lon=HOME[1], squawk='1200')
        key = f'aircraft:{icao}'
        sightings.append((key, 'aircraft', icao, first, first + POSITIONS_PER * 10, json.dumps(data), 0, 1))
        bearing, dist = rnd.uniform(0, 360), rnd.uniform(5, 150)
        for k in range(POSITIONS_PER):
            lat, lon = destination(HOME, max(1, dist - k * 2), bearing)
            positions.append((key, first + k * 10, lat, lon, data['altitude'], data['speed'], max(1, dist - k * 2), 0))
    events = [(now - rnd.uniform(0, 30 * 86400), rnd.choice(['detection', 'detection', 'alert', 'receiver']),
               sightings[i % len(sightings)][0], f'N{i} first detected', 0) for i in range(SIGHTINGS * 3)]
    minutes = [(int((now - m * 60) // 60), rnd.randrange(0, 4000), rnd.randrange(0, 60), rnd.randrange(0, 5), 0)
               for m in range(30 * 1440) if rnd.random() < 0.6]
    sessions = [(now - d * 86400 - 3600, now - d * 86400, 'Aircraft') for d in range(60)]
    with db.conn:
        db.conn.executemany('INSERT OR REPLACE INTO sightings VALUES (?,?,?,?,?,?,?,?)', sightings)
        db.conn.executemany('INSERT INTO positions(key,time,lat,lon,altitude,speed,distance,simulated) '
                            'VALUES (?,?,?,?,?,?,?,?)', positions)
        db.conn.executemany('INSERT INTO events(time,category,key,message,simulated) VALUES (?,?,?,?,?)', events)
        db.conn.executemany('INSERT OR REPLACE INTO minute_stats VALUES (?,?,?,?,?)', minutes)
        db.conn.executemany('INSERT INTO sessions(start,end,mode) VALUES (?,?,?)', sessions)
    db.close()
    return config.folder


def main():
    folder = Path(tempfile.mkdtemp(prefix='airalert-dbprofile-'))
    started = time.perf_counter()
    build(folder)
    size = sum(p.stat().st_size for p in folder.glob('history.sqlite*')) / 1024 ** 2
    print(f'built {SIGHTINGS:,} sightings / {SIGHTINGS * POSITIONS_PER:,} positions in '
          f'{time.perf_counter() - started:.1f} s · {size:.0f} MB\n')
    from airalert.core.config import Config
    from airalert.engine import Station
    from airalert import insights
    station = timed('Station() startup (maintenance + open)', lambda: Station(Config(folder)))
    db = station.db
    timed('Station() startup again (nothing to delete)', lambda: Station(Config(folder)).shutdown())
    timed('statistics() [Statistics page, every 4 s]', station.statistics)
    timed('watch_rows(), 3 entries [Watchlist page, every 5 s]', station.watch_rows)
    timed('insights.catch_up_all() first rollup', lambda: insights.catch_up_all(db.conn, HOME))
    for span in ('24h', '7d', '30d'):
        timed(f'insights.snapshot({span}) [Statistics page, every 45 s]',
              lambda span=span: insights.snapshot(db.conn, span, False))
    timed('history: all sightings (no query)', lambda: station.search_history('sightings'))
    timed('history: query "UAL1"', lambda: station.search_history('sightings', 'UAL1'))
    timed('history: max distance 20 mi', lambda: station.search_history('sightings', max_distance=20))
    timed('history: alerts category', lambda: station.search_history('alert'))
    timed('maintenance_info()', station.maintenance_info)
    rows = station.search_history('sightings')
    timed('map history rows -> titles (json.loads x N)', lambda: [json.loads(r['data']) for r in rows])
    from airalert.playback import PlaybackData
    end = time.time()
    timed('playback load: last 24 h', lambda: PlaybackData.load(db.conn, end - 86400, end))
    timed('db.maintenance() (delete + checkpoint + VACUUM)', lambda: db.maintenance(time.time() - 365 * 86400))
    station.shutdown()


if __name__ == '__main__':
    main()
