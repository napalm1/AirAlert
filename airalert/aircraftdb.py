"""Local aircraft database: the bundled snapshot, its metadata and the optional OpenSky update.

aircraft.sqlite holds one row per ICAO 24-bit address:
    aircraft(icao TEXT PRIMARY KEY, registration, type, model, manufacturer, operator)  -- WITHOUT ROWID
    meta(key TEXT PRIMARY KEY, value)                                                   -- source, built, count, ...
Lookups go through core.database.Database.lookup(); user CSV imports take precedence.
"""
import csv
import io
import logging
import os
import shutil
import sqlite3
import time
import zipfile
from datetime import datetime
from pathlib import Path

from .core.database import AIRCRAFT_FILE
from .core.receivers import resource_root

log = logging.getLogger(__name__)

OPENSKY_URL = 'https://s3.opensky-network.org/data-samples/metadata/aircraftDatabase.zip'
SCHEMA = '''
CREATE TABLE aircraft (icao TEXT PRIMARY KEY, registration TEXT, type TEXT, model TEXT, manufacturer TEXT,
                       operator TEXT) WITHOUT ROWID;
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
'''
HEX = set('0123456789ABCDEF')


class Cancelled(Exception):
    pass


# Common ICAO type designators -> (manufacturer, model). Used when a source has a type code but no model name.
TYPE_NAMES = {
    # Airliners and regional aircraft
    'A306': ('Airbus', 'A300-600'), 'A310': ('Airbus', 'A310'), 'A318': ('Airbus', 'A318'),
    'A319': ('Airbus', 'A319'), 'A320': ('Airbus', 'A320'), 'A321': ('Airbus', 'A321'),
    'A19N': ('Airbus', 'A319neo'), 'A20N': ('Airbus', 'A320neo'), 'A21N': ('Airbus', 'A321neo'),
    'A332': ('Airbus', 'A330-200'), 'A333': ('Airbus', 'A330-300'), 'A338': ('Airbus', 'A330-800neo'),
    'A339': ('Airbus', 'A330-900neo'), 'A342': ('Airbus', 'A340-200'), 'A343': ('Airbus', 'A340-300'),
    'A345': ('Airbus', 'A340-500'), 'A346': ('Airbus', 'A340-600'), 'A359': ('Airbus', 'A350-900'),
    'A35K': ('Airbus', 'A350-1000'), 'A388': ('Airbus', 'A380-800'), 'BCS1': ('Airbus', 'A220-100'),
    'BCS3': ('Airbus', 'A220-300'), 'B712': ('Boeing', '717-200'), 'B733': ('Boeing', '737-300'),
    'B734': ('Boeing', '737-400'), 'B735': ('Boeing', '737-500'), 'B736': ('Boeing', '737-600'),
    'B737': ('Boeing', '737-700'), 'B738': ('Boeing', '737-800'), 'B739': ('Boeing', '737-900'),
    'B37M': ('Boeing', '737 MAX 7'), 'B38M': ('Boeing', '737 MAX 8'), 'B39M': ('Boeing', '737 MAX 9'),
    'B3XM': ('Boeing', '737 MAX 10'), 'B744': ('Boeing', '747-400'), 'B748': ('Boeing', '747-8'),
    'B752': ('Boeing', '757-200'), 'B753': ('Boeing', '757-300'), 'B762': ('Boeing', '767-200'),
    'B763': ('Boeing', '767-300'), 'B764': ('Boeing', '767-400'), 'B772': ('Boeing', '777-200'),
    'B77L': ('Boeing', '777-200LR / 777F'), 'B773': ('Boeing', '777-300'), 'B77W': ('Boeing', '777-300ER'),
    'B778': ('Boeing', '777-8'), 'B779': ('Boeing', '777-9'), 'B788': ('Boeing', '787-8'),
    'B789': ('Boeing', '787-9'), 'B78X': ('Boeing', '787-10'), 'MD11': ('McDonnell Douglas', 'MD-11'),
    'MD82': ('McDonnell Douglas', 'MD-82'), 'MD83': ('McDonnell Douglas', 'MD-83'),
    'MD88': ('McDonnell Douglas', 'MD-88'), 'MD90': ('McDonnell Douglas', 'MD-90'),
    'DC10': ('McDonnell Douglas', 'DC-10'), 'CRJ2': ('Bombardier', 'CRJ200'), 'CRJ7': ('Bombardier', 'CRJ700'),
    'CRJ9': ('Bombardier', 'CRJ900'), 'CRJX': ('Bombardier', 'CRJ1000'), 'E135': ('Embraer', 'ERJ 135'),
    'E145': ('Embraer', 'ERJ 145'), 'E170': ('Embraer', 'E170'), 'E75S': ('Embraer', 'E175'),
    'E75L': ('Embraer', 'E175'), 'E190': ('Embraer', 'E190'), 'E195': ('Embraer', 'E195'),
    'E290': ('Embraer', 'E190-E2'), 'E295': ('Embraer', 'E195-E2'), 'DH8A': ('De Havilland Canada', 'Dash 8-100'),
    'DH8B': ('De Havilland Canada', 'Dash 8-200'), 'DH8C': ('De Havilland Canada', 'Dash 8-300'),
    'DH8D': ('De Havilland Canada', 'Dash 8-400'), 'AT43': ('ATR', 'ATR 42-300'), 'AT45': ('ATR', 'ATR 42-500'),
    'AT72': ('ATR', 'ATR 72'), 'AT75': ('ATR', 'ATR 72-500'), 'AT76': ('ATR', 'ATR 72-600'),
    'B190': ('Beechcraft', '1900'), 'SF34': ('Saab', '340'),
    # Military and government
    'C130': ('Lockheed', 'C-130 Hercules'), 'C30J': ('Lockheed Martin', 'C-130J Super Hercules'),
    'C17': ('Boeing', 'C-17 Globemaster III'), 'K35R': ('Boeing', 'KC-135R Stratotanker'),
    'P8': ('Boeing', 'P-8 Poseidon'), 'F16': ('General Dynamics', 'F-16 Fighting Falcon'),
    'H47': ('Boeing', 'CH-47 Chinook'), 'H60': ('Sikorsky', 'H-60 Black Hawk / Seahawk'),
    'V22': ('Bell Boeing', 'V-22 Osprey'), 'UH1': ('Bell', 'UH-1 Iroquois'), 'T6': ('North American', 'T-6 Texan'),
    # Business jets
    'C500': ('Cessna', 'Citation I'), 'C501': ('Cessna', 'Citation I/SP'), 'C510': ('Cessna', 'Citation Mustang'),
    'C525': ('Cessna', 'CitationJet'), 'C25A': ('Cessna', 'CitationJet CJ2'), 'C25B': ('Cessna', 'CitationJet CJ3'),
    'C25C': ('Cessna', 'CitationJet CJ4'), 'C25M': ('Cessna', 'Citation M2'), 'C550': ('Cessna', 'Citation II'),
    'C55B': ('Cessna', 'Citation Bravo'), 'C560': ('Cessna', 'Citation V'), 'C56X': ('Cessna', 'Citation Excel / XLS'),
    'C650': ('Cessna', 'Citation III'), 'C680': ('Cessna', 'Citation Sovereign'), 'C68A': ('Cessna', 'Citation Latitude'),
    'C700': ('Cessna', 'Citation Longitude'), 'C750': ('Cessna', 'Citation X'), 'E50P': ('Embraer', 'Phenom 100'),
    'E55P': ('Embraer', 'Phenom 300'), 'E545': ('Embraer', 'Legacy 450 / Praetor 500'),
    'E550': ('Embraer', 'Legacy 500 / Praetor 600'), 'E35L': ('Embraer', 'Legacy 600 / 650'),
    'LJ35': ('Learjet', '35'), 'LJ45': ('Learjet', '45'), 'LJ60': ('Learjet', '60'), 'LJ75': ('Learjet', '75'),
    'CL30': ('Bombardier', 'Challenger 300'), 'CL35': ('Bombardier', 'Challenger 350'),
    'CL60': ('Bombardier', 'Challenger 600 series'), 'GLEX': ('Bombardier', 'Global Express'),
    'GL5T': ('Bombardier', 'Global 5000'), 'GL7T': ('Bombardier', 'Global 7500'), 'GLF2': ('Gulfstream', 'G-II'),
    'GLF3': ('Gulfstream', 'G-III'), 'GLF4': ('Gulfstream', 'G-IV'), 'GLF5': ('Gulfstream', 'G-V'),
    'GLF6': ('Gulfstream', 'G650'), 'G150': ('Gulfstream', 'G150'), 'G280': ('Gulfstream', 'G280'),
    'GALX': ('Gulfstream', 'G200'), 'ASTR': ('Gulfstream', 'G100 / Astra'), 'FA20': ('Dassault', 'Falcon 20'),
    'FA50': ('Dassault', 'Falcon 50'), 'F900': ('Dassault', 'Falcon 900'), 'F2TH': ('Dassault', 'Falcon 2000'),
    'FA7X': ('Dassault', 'Falcon 7X'), 'FA8X': ('Dassault', 'Falcon 8X'), 'H25B': ('Hawker', '800'),
    'BE40': ('Beechcraft', 'Beechjet 400'), 'PRM1': ('Beechcraft', 'Premier I'), 'HDJT': ('Honda', 'HondaJet'),
    'SF50': ('Cirrus', 'SF50 Vision Jet'), 'PC24': ('Pilatus', 'PC-24'),
    # Turboprops
    'BE9L': ('Beechcraft', 'King Air 90'), 'BE10': ('Beechcraft', 'King Air 100'), 'BE20': ('Beechcraft', 'King Air 200'),
    'B350': ('Beechcraft', 'King Air 350'), 'PC12': ('Pilatus', 'PC-12'), 'C208': ('Cessna', '208 Caravan'),
    'TBM7': ('Socata', 'TBM 700'), 'TBM8': ('Socata', 'TBM 850'), 'TBM9': ('Daher', 'TBM 900 series'),
    'P46T': ('Piper', 'PA-46T Malibu Meridian'), 'KODI': ('Quest', 'Kodiak 100'), 'P180': ('Piaggio', 'P.180 Avanti'),
    'DHC2': ('De Havilland Canada', 'DHC-2 Beaver'), 'DHC3': ('De Havilland Canada', 'DHC-3 Otter'),
    'DHC6': ('De Havilland Canada', 'DHC-6 Twin Otter'), 'AT5T': ('Air Tractor', 'AT-502'),
    'AT8T': ('Air Tractor', 'AT-802'),
    # Piston aircraft
    'C120': ('Cessna', '120'), 'C140': ('Cessna', '140'), 'C150': ('Cessna', '150'), 'C152': ('Cessna', '152'),
    'C170': ('Cessna', '170'), 'C172': ('Cessna', '172 Skyhawk'), 'C72R': ('Cessna', '172RG Cutlass RG'),
    'C175': ('Cessna', '175 Skylark'), 'C177': ('Cessna', '177 Cardinal'), 'C77R': ('Cessna', '177RG Cardinal RG'),
    'C180': ('Cessna', '180 Skywagon'), 'C182': ('Cessna', '182 Skylane'), 'C82R': ('Cessna', 'R182 Skylane RG'),
    'C82T': ('Cessna', 'T182 Turbo Skylane'), 'C185': ('Cessna', '185 Skywagon'), 'C195': ('Cessna', '195'),
    'C206': ('Cessna', '206 Stationair'), 'T206': ('Cessna', 'T206 Turbo Stationair'),
    'C210': ('Cessna', '210 Centurion'), 'T210': ('Cessna', 'T210 Turbo Centurion'),
    'P210': ('Cessna', 'P210 Pressurized Centurion'), 'C310': ('Cessna', '310'), 'C337': ('Cessna', '337 Skymaster'),
    'C340': ('Cessna', '340'), 'C414': ('Cessna', '414 Chancellor'), 'C421': ('Cessna', '421 Golden Eagle'),
    'P28A': ('Piper', 'PA-28 Cherokee / Warrior / Archer'), 'P28B': ('Piper', 'PA-28 Cherokee 235 / Dakota'),
    'P28R': ('Piper', 'PA-28R Arrow'), 'PA32': ('Piper', 'PA-32 Cherokee Six / Saratoga'),
    'P32R': ('Piper', 'PA-32R Lance / Saratoga'), 'PA11': ('Piper', 'PA-11 Cub Special'),
    'PA12': ('Piper', 'PA-12 Super Cruiser'), 'PA18': ('Piper', 'PA-18 Super Cub'), 'PA22': ('Piper', 'PA-22 Tri-Pacer'),
    'PA23': ('Piper', 'PA-23 Apache'), 'PA24': ('Piper', 'PA-24 Comanche'), 'PA25': ('Piper', 'PA-25 Pawnee'),
    'PA27': ('Piper', 'PA-23 Aztec'), 'PA30': ('Piper', 'PA-30 Twin Comanche'), 'PA31': ('Piper', 'PA-31 Navajo'),
    'PA34': ('Piper', 'PA-34 Seneca'), 'PA38': ('Piper', 'PA-38 Tomahawk'), 'PA44': ('Piper', 'PA-44 Seminole'),
    'PA46': ('Piper', 'PA-46 Malibu'), 'J3': ('Piper', 'J-3 Cub'), 'AEST': ('Piper', 'Aerostar'),
    'BE23': ('Beechcraft', '23 Musketeer'), 'BE24': ('Beechcraft', '24 Sierra'), 'BE33': ('Beechcraft', '33 Debonair'),
    'BE35': ('Beechcraft', '35 Bonanza'), 'BE36': ('Beechcraft', '36 Bonanza'), 'BE55': ('Beechcraft', '55 Baron'),
    'BE58': ('Beechcraft', '58 Baron'), 'SR20': ('Cirrus', 'SR20'), 'SR22': ('Cirrus', 'SR22'),
    'S22T': ('Cirrus', 'SR22T'), 'M20P': ('Mooney', 'M20'), 'M20T': ('Mooney', 'M20 Turbo'),
    'DA20': ('Diamond', 'DA20'), 'DA40': ('Diamond', 'DA40 Diamond Star'), 'DA42': ('Diamond', 'DA42 Twin Star'),
    'DA62': ('Diamond', 'DA62'), 'AA1': ('Grumman American', 'AA-1'), 'AA5': ('Grumman American', 'AA-5'),
    'AC11': ('Rockwell', 'Commander 112 / 114'), 'HUSK': ('Aviat', 'Husky'), 'RV12': ("Van's", 'RV-12'),
    'M7': ('Maule', 'M-7'), 'L8': ('Luscombe', '8 Silvaire'), 'AR11': ('Aeronca', '11 Chief'),
    'S108': ('Stinson', '108 Voyager'), 'ERCO': ('ERCO', 'Ercoupe'), 'NAVI': ('Navion', 'Navion'),
    'GC1': ('Globe', 'GC-1 Swift'), 'ST75': ('Boeing', 'Stearman 75'), 'G164': ('Grumman', 'G-164 Ag Cat'),
    'BL17': ('Bellanca', '17 Viking'),
    # Helicopters
    'R22': ('Robinson', 'R22'), 'R44': ('Robinson', 'R44'), 'R66': ('Robinson', 'R66'),
    'B06': ('Bell', '206 JetRanger'), 'B407': ('Bell', '407'), 'B412': ('Bell', '412'), 'B429': ('Bell', '429'),
    'B505': ('Bell', '505'), 'B47G': ('Bell', '47G'), 'AS50': ('Airbus Helicopters', 'AS350 Écureuil'),
    'EC30': ('Airbus Helicopters', 'EC130'), 'EC35': ('Airbus Helicopters', 'EC135 / H135'),
    'EC45': ('Airbus Helicopters', 'EC145 / H145'), 'AS65': ('Airbus Helicopters', 'AS365 Dauphin'),
    'A109': ('Leonardo', 'AW109'), 'A139': ('Leonardo', 'AW139'), 'S76': ('Sikorsky', 'S-76'),
    'H500': ('MD Helicopters', '500'), 'H269': ('Schweizer', '269 / 300'),
    # Other aircraft
    'GLID': ('', 'Glider'), 'BALL': ('', 'Balloon'), 'ULAC': ('', 'Ultralight'), 'GYRO': ('', 'Gyroplane'),
    'SHIP': ('', 'Airship'), 'UHEL': ('', 'Ultralight helicopter'),
}

_CRAFT = {'L': 'airplane', 'S': 'seaplane', 'A': 'amphibian', 'G': 'gyroplane', 'H': 'helicopter', 'T': 'tiltrotor'}
_COUNT = {'1': 'Single-engine', '2': 'Twin-engine', '3': 'Three-engine', '4': 'Four-engine', '6': 'Six-engine',
          '8': 'Eight-engine'}


def class_description(desc):
    """ICAO aircraft description code (e.g. L2J) -> 'Twin-engine jet airplane'. '' when unknown."""
    desc = (desc or '').strip().upper()
    if len(desc) != 3 or desc[0] not in _CRAFT:
        return ''
    craft = _CRAFT[desc[0]]
    engine = {'P': 'piston', 'J': 'jet', 'E': 'electric',
              'T': 'turbine' if desc[0] in 'HGT' else 'turboprop'}.get(desc[2], '')
    count = _COUNT.get(desc[1], '')
    text = ' '.join(p for p in (count, engine, craft) if p)
    return text[:1].upper() + text[1:] if engine or count else craft.capitalize()


def clean_registration(reg):
    reg = (reg or '').strip().upper()
    short = len(reg) < 3 and not (len(reg) == 2 and reg[0] == 'N' and reg[1].isdigit())  # N1 is valid
    if not reg or short or 'UNKNOWN' in reg or reg.endswith('-') or reg.startswith('-'):
        return ''
    return reg[:16]


def clean_type(code):
    code = (code or '').strip().upper()
    return '' if code in ('', 'ZZZZ', 'NONE', 'N/A') or len(code) > 4 else code


def tidy(text, limit=60):
    """Collapse whitespace, drop decoding debris and title-case ALL-CAPS names."""
    text = ' '.join(str(text or '').replace('�', '').split())
    if text.isupper() and len(text) > 3:
        text = text.title()
    return text[:limit]


def valid_icao(icao):
    icao = (icao or '').strip().upper()
    return icao if len(icao) == 6 and set(icao) <= HEX and icao != '000000' else ''


def describe(typecode, model='', manufacturer='', desc=''):
    """Fill model/manufacturer from the type designator table, else describe the aircraft class."""
    known = TYPE_NAMES.get(typecode)
    if not model and known:
        return known[1], known[0] or manufacturer  # keep the pair consistent
    return model or class_description(desc), manufacturer


# --------------------------------------------------------------------------- files
def bundled_path():
    return resource_root() / 'vendor' / 'aircraft' / AIRCRAFT_FILE


def local_path(folder):
    return Path(folder) / AIRCRAFT_FILE


def ensure_local(folder):
    """Copy the bundled database into the data folder when it is missing. Returns the local path."""
    target = local_path(folder)
    source = bundled_path()
    if not target.exists() and source.is_file():
        tmp = target.with_name(AIRCRAFT_FILE + '.copy')
        try:
            shutil.copyfile(source, tmp)
            os.replace(tmp, target)
            log.info('Installed the bundled aircraft database in %s', target)
        except OSError:
            log.exception('Could not copy the bundled aircraft database')
            tmp.unlink(missing_ok=True)
    return target


def info(path):
    """Metadata of an aircraft database file: {ok, count, source, built, dataDate, sizeMb}."""
    path = Path(path)
    result = dict(ok=False, count=0, source='', built='', dataDate='', sizeMb=0.0)
    if not path.is_file():
        return result
    try:
        conn = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=1)
        try:
            meta = dict(conn.execute('SELECT key, value FROM meta').fetchall())
            count = int(meta.get('count') or conn.execute('SELECT COUNT(*) FROM aircraft').fetchone()[0])
        finally:
            conn.close()
    except (sqlite3.Error, ValueError):
        log.exception('Could not read %s', path)
        return result
    return dict(ok=True, count=count, source=meta.get('source', ''), built=meta.get('built', ''),
                dataDate=meta.get('data_date', ''), sizeMb=round(path.stat().st_size / 1024 ** 2, 1))


def write_database(path, records, meta):
    """Write records [(icao, registration, type, model, manufacturer, operator)] to a new database file.

    Records are streamed (no full copy in memory); VACUUM then rewrites the table compactly in key order."""
    path = Path(path)
    path.unlink(missing_ok=True)
    conn = sqlite3.connect(path)
    try:
        conn.executescript('PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF; PRAGMA page_size=4096; '
                           'PRAGMA cache_size=-65536;' + SCHEMA)
        with conn:
            conn.executemany('INSERT OR REPLACE INTO aircraft VALUES (?,?,?,?,?,?)',
                             ((icao, *(v or None for v in rest)) for icao, *rest in records))
            count = conn.execute('SELECT COUNT(*) FROM aircraft').fetchone()[0]
            meta = dict(meta, count=str(count), built=meta.get('built') or datetime.now().strftime('%Y-%m-%d %H:%M'),
                        schema='1')
            conn.executemany('INSERT OR REPLACE INTO meta VALUES (?,?)', sorted(meta.items()))
        conn.execute('VACUUM')
    finally:
        conn.close()
    return count


def install(tmp, target, attempts=20):
    """Atomically replace target with tmp (retrying briefly while another reader closes the file)."""
    for i in range(attempts):
        try:
            os.replace(tmp, target)
            return
        except PermissionError:
            if i == attempts - 1:
                raise
            time.sleep(0.1)


# ------------------------------------------------------------------ OpenSky update
class _Counting(io.RawIOBase):
    """Byte-counting wrapper so CSV parsing can report progress through the compressed member."""
    def __init__(self, raw):
        self.raw = raw
        self.count = 0

    def readable(self):
        return True

    def readinto(self, buffer):
        data = self.raw.read(len(buffer))
        buffer[:len(data)] = data
        self.count += len(data)
        return len(data)


def opensky_records(text_stream, progress=None, cancel=None):
    """Yield database rows from an OpenSky aircraftDatabase.csv stream."""
    reader = csv.DictReader(text_stream)
    missing = {'icao24', 'registration', 'typecode'} - set(reader.fieldnames or [])
    if missing:
        raise ValueError(f'Unexpected OpenSky file (missing columns: {", ".join(sorted(missing))})')
    for n, row in enumerate(reader):
        if n % 20000 == 0:
            if cancel and cancel():
                raise Cancelled()
            if progress:
                progress()
        icao = valid_icao(row.get('icao24'))
        if not icao:
            continue
        typecode = clean_type(row.get('typecode'))
        model, manufacturer = describe(typecode, tidy(row.get('model')), tidy(row.get('manufacturername')),
                                       row.get('icaoaircrafttype'))
        record = (icao, clean_registration(row.get('registration')), typecode, model, manufacturer,
                  tidy(row.get('operator')))
        if any(record[1:]):
            yield record


def build_from_opensky(zip_path, out_path, progress=None, cancel=None):
    """Build an aircraft database from OpenSky's aircraftDatabase.zip. progress(fraction) and cancel() -> bool."""
    with zipfile.ZipFile(zip_path) as archive:
        members = [i for i in archive.infolist() if i.filename.lower().endswith('.csv')]
        if not members:
            raise ValueError('The OpenSky download does not contain a CSV file.')
        member = max(members, key=lambda i: i.file_size)
        data_date = datetime(*member.date_time).strftime('%Y-%m-%d')
        with archive.open(member) as raw:
            counter = _Counting(raw)
            text = io.TextIOWrapper(io.BufferedReader(counter, 1 << 20), encoding='utf-8', errors='replace',
                                    newline='')
            report = (lambda: progress(min(0.95, 0.9 * counter.count / max(1, member.file_size)))) if progress else None
            count = write_database(out_path, opensky_records(text, report, cancel),
                                   dict(source='OpenSky Network aircraft database', data_date=data_date,
                                        url=OPENSKY_URL))
    if cancel and cancel():
        raise Cancelled()
    if count < 1000:
        Path(out_path).unlink(missing_ok=True)
        raise ValueError('The OpenSky download contained too few aircraft; the current database was kept.')
    if progress:
        progress(1.0)
    return count
