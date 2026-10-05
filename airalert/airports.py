"""Airport reference data for the live map (OurAirports, public domain).

The raw OurAirports CSV copies in ``vendor/airports`` are condensed into
``vendor/airports/airports.tsv.gz`` by ``python -m airalert.airports``. At run
time that compact file is read once on a background thread and kept in a
per-tier spatial grid so the map can ask for the airports in view cheaply.

The source has no airport-type column, so tiers are a heuristic:
heliports (name mentions a heliport/helipad/helistop), major (has an IATA
code) and everything else. Airports are stored in rank order (tier, then
number of published frequencies, a good proxy for size), so a lower index
always wins when the map has to drop symbols to avoid clutter.
"""
import csv
import gzip
import logging
import math
import sys
import threading
from pathlib import Path

log = logging.getLogger(__name__)

MAJOR, OTHER, HELIPORT = 0, 1, 2
TIER_NAMES = ('Airport', 'Airfield', 'Heliport')
MIN_ZOOM = (6.0, 9.0, 11.0)       # map zoom at which each tier appears
CELL_DEG = (2.0, 0.5, 0.25)       # grid cell size per tier
DATA_NAME = 'airports.tsv.gz'
FORMAT = 'AIRALERT-AIRPORTS 1'
FREQ_ORDER = ['TWR', 'ATIS', 'GND', 'CTAF', 'UNIC', 'UNICOM', 'AFIS', 'A/G', 'APP', 'DEP', 'A/D', 'AWOS', 'ASOS',
              'CLD', 'ATF', 'MF', 'RDO', 'RADIO', 'INFO']
FREQ_LABELS = {'UNIC': 'UNICOM', 'A/D': 'APP/DEP', 'RDO': 'RADIO', 'CLD': 'CLNC DEL'}
MAX_FREQS = 4
HELI_WORDS = ('heliport', 'helipad', 'helistop')


def vendor_folder():
    from .core.receivers import resource_root
    return resource_root() / 'vendor' / 'airports'


def _clean(text):
    return ' '.join(str(text).replace('�', '').replace('\t', ' ').split())


def display_code(ident, iata):
    """Short map label: the ICAO/local ident when it is a short code, else the IATA code."""
    ident = ident.upper()
    if 3 <= len(ident) <= 4 and ident.isalnum():
        return ident
    return iata.upper() or ident


# --------------------------------------------------------------------- building
def build(source_folder, out_path):
    """Condense airport-codes.csv + airport-frequencies.csv into the compact rank-ordered file."""
    source_folder = Path(source_folder)
    freqs = {}
    with open(source_folder / 'airport-frequencies.csv', newline='', encoding='utf-8', errors='replace') as f:
        for row in csv.DictReader(f):
            ident = row.get('airport_ident', '').strip().upper()
            kind = row.get('type', '').strip().upper()
            try:
                mhz = float(row.get('frequency_mhz') or 0)
            except ValueError:
                continue
            if ident and kind and 2 <= mhz <= 1000:
                freqs.setdefault(ident, []).append((kind, mhz))
    rows = []
    with open(source_folder / 'airport-codes.csv', newline='', encoding='utf-8', errors='replace') as f:
        reader = csv.reader(f)
        next(reader, None)
        for r in reader:
            if len(r) < 7:
                continue
            ident, iata, name, _continent, city, lon, lat = (_clean(v) for v in r[:7])
            try:
                lat, lon = float(lat), float(lon)
            except ValueError:
                continue
            lowered = name.lower()
            if not ident or not -90 <= lat <= 90 or not -180 <= lon <= 180 or 'closed' in lowered \
                    or lowered.startswith('duplicate'):
                continue
            ident = ident.upper()
            iata = iata.upper() if len(iata) == 3 and iata.isalnum() else ''
            tier = HELIPORT if any(w in lowered for w in HELI_WORDS) else MAJOR if iata else OTHER
            published = freqs.get(ident, [])
            chosen, seen = [], set()
            for kind, mhz in sorted(published, key=lambda kv: FREQ_ORDER.index(kv[0]) if kv[0] in FREQ_ORDER else 99):
                label = FREQ_LABELS.get(kind, kind)
                if kind not in FREQ_ORDER or label in seen:
                    continue
                seen.add(label)
                chosen.append(f'{label} {mhz:.3f}')
                if len(chosen) == MAX_FREQS:
                    break
            rows.append((tier, -len(published), ident, iata, f'{lat:.5f}', f'{lon:.5f}', name, city, ';'.join(chosen)))
    rows.sort(key=lambda r: (r[0], r[1], r[2]))
    out_path = Path(out_path)
    with gzip.open(out_path, 'wt', encoding='utf-8', newline='\n', compresslevel=9) as f:
        f.write(FORMAT + '\n')
        for tier, _, ident, iata, lat, lon, name, city, fq in rows:
            f.write('\t'.join((str(tier), ident, iata, lat, lon, name, city, fq)) + '\n')
    return len(rows)


# ---------------------------------------------------------------------- runtime
class AirportIndex:
    """Rank-ordered airport columns plus a per-tier spatial grid of indices."""

    def __init__(self, path):
        self.path = Path(path)
        self.idents, self.iatas, self.names, self.cities, self.freqs = [], [], [], [], []
        self.codes, self.tiers, self.lats, self.lons, self.nx, self.ny = [], [], [], [], [], []
        self.grid = ({}, {}, {})
        self._load()

    def _load(self):
        with gzip.open(self.path, 'rt', encoding='utf-8') as f:
            header = f.readline().strip()
            if header != FORMAT:
                raise ValueError(f'Unexpected airport data format: {header!r}')
            lines = f.read().split('\n')
        idents, iatas, names, cities, freqs = self.idents, self.iatas, self.names, self.cities, self.freqs
        codes, tiers, lats, lons, nx, ny = self.codes, self.tiers, self.lats, self.lons, self.nx, self.ny
        grid = self.grid
        radians, sin, log_ = math.radians, math.sin, math.log
        floor = math.floor
        for line in lines:
            if not line:
                continue
            tier, ident, iata, lat, lon, name, city, fq = line.split('\t')
            tier, lat, lon = int(tier), float(lat), float(lon)
            index = len(idents)
            idents.append(ident)
            iatas.append(iata)
            names.append(name)
            cities.append(city)
            freqs.append(fq)
            codes.append(display_code(ident, iata))
            tiers.append(tier)
            lats.append(lat)
            lons.append(lon)
            s = sin(radians(max(-85.0511, min(85.0511, lat))))
            nx.append((lon + 180) / 360)
            ny.append(0.5 - log_((1 + s) / (1 - s)) / (4 * math.pi))
            cell = CELL_DEG[tier]
            grid[tier].setdefault((floor(lat / cell), floor(lon / cell)), []).append(index)

    def __len__(self):
        return len(self.idents)

    def query(self, tier, south, north, west, east):
        """Indices of one tier inside a lat/lon box (west > east wraps the date line), in rank order."""
        cell = CELL_DEG[tier]
        grid = self.grid[tier]
        i0, i1 = math.floor(max(-90.0, south) / cell), math.floor(min(90.0, north) / cell)
        spans = [(west, east)] if west <= east else [(west, 180.0), (-180.0, east)]
        found = []
        for lo0, lo1 in spans:
            j0, j1 = math.floor(lo0 / cell), math.floor(min(179.9999, lo1) / cell)
            if (i1 - i0 + 1) * (j1 - j0 + 1) > 20000:
                continue
            for i in range(i0, i1 + 1):
                for j in range(j0, j1 + 1):
                    bucket = grid.get((i, j))
                    if bucket:
                        found.extend(bucket)
        found.sort()
        return found

    def nearest(self, lat, lon, radius_km=30.0, tiers=(MAJOR, OTHER)):
        """(index, km) of the nearest airport or airfield within radius_km, or None."""
        dlat = radius_km / 110.574
        dlon = radius_km / (111.32 * max(0.05, math.cos(math.radians(lat))))
        best, best_km = None, radius_km
        k = math.cos(math.radians(lat)) * 111.32
        for tier in tiers:
            west, east = (lon - dlon + 180) % 360 - 180, (lon + dlon + 180) % 360 - 180
            for i in self.query(tier, lat - dlat, lat + dlat, west, east):
                dx = ((self.lons[i] - lon + 180) % 360 - 180) * k
                km = math.hypot(dx, (self.lats[i] - lat) * 110.574)
                if km <= best_km:
                    best, best_km = i, km
        return (best, best_km) if best is not None else None

    def find(self, ident):
        ident = ident.upper()
        try:
            return self.idents.index(ident)
        except ValueError:
            return None

    def info(self, index):
        """Card data for QML."""
        freqs = []
        for item in self.freqs[index].split(';') if self.freqs[index] else []:
            kind, _, mhz = item.rpartition(' ')
            freqs.append(dict(kind=kind, mhz=mhz))
        ident, iata = self.idents[index], self.iatas[index]
        return dict(index=index, code=self.codes[index], ident=ident, iata=iata, name=self.names[index],
                    city=self.cities[index], tier=self.tiers[index], tierName=TIER_NAMES[self.tiers[index]],
                    icao=ident if len(ident) == 4 and ident.isalpha() else '', lat=self.lats[index],
                    lon=self.lons[index], freqs=freqs)


_lock = threading.Lock()
_state = dict(index=None, thread=None, error='')


def _load_worker(path):
    try:
        index = AirportIndex(path)
        with _lock:
            _state['index'] = index
        log.info('Loaded %d airports', len(index))
    except (OSError, ValueError, EOFError) as e:
        with _lock:
            _state['error'] = str(e)
        log.warning('Airport data unavailable: %s', e)


def get(path=None):
    """The shared index, or None while it loads in the background (loading starts on first call)."""
    index = _state['index']
    if index is not None or _state['error']:
        return index
    with _lock:
        if _state['thread'] is None:
            thread = threading.Thread(target=_load_worker, args=(path or vendor_folder() / DATA_NAME,),
                                      name='airport-data', daemon=True)
            _state['thread'] = thread
            thread.start()
    return _state['index']


def load(path=None, timeout=30):
    """Block until the shared index is loaded (tests, benchmarks). Returns it or None."""
    get(path)
    thread = _state['thread']
    if thread is not None:
        thread.join(timeout)
    return _state['index']


def main(argv):
    root = Path(__file__).resolve().parents[1]
    source = Path(argv[1]) if len(argv) > 1 else root / 'vendor' / 'airports'
    out = root / 'vendor' / 'airports' / DATA_NAME
    count = build(source, out)
    print(f'Wrote {count:,} airports to {out} ({out.stat().st_size / 1024:.0f} KB)')


if __name__ == '__main__':
    main(sys.argv)
