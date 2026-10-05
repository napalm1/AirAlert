"""Build the bundled aircraft database vendor/aircraft/aircraft.sqlite from local, read-only sources.

    .venv\\Scripts\\python.exe tools\\build_aircraft_db.py [--opensky FILE] [--flightaware DIR] [--out FILE]

Sources (never modified):
  * OpenSky Network snapshot  aircraft-database.csv.sqlite, table aircrafts(icao24, reg, manufacturer, type, callsign)
    where 'type' is an ICAO aircraft description (L2J) and 'callsign' the operator's radio designator.
  * dump1090-fa database      db/<hex prefix>.json files {"<rest of hex>": {"r": reg, "t": typecode, "desc": L1P}}
    plus {"children": [...]} lists of further prefix files, and db/aircraft_types/icao_aircraft_types.json
    (typecode -> {"desc", "wtc"}).
Registrations come from OpenSky first (newer), type codes from dump1090-fa (e.g. B738).
"""
import os
import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from airalert.aircraftdb import (clean_registration, clean_type, describe, tidy, valid_icao,  # noqa: E402
                                 write_database)

OLD = Path(os.environ.get('AIRALERT_SOURCES', 'sources'))   # folder holding the downloaded source data
DEFAULT_OPENSKY = OLD / 'downloads' / 'Dump1090-1.0.1' / 'aircraft-database.csv.sqlite'
DEFAULT_FLIGHTAWARE = OLD / 'vendor' / 'adsb' / 'web_root-FlightAware' / 'db'


# The snapshot stores radio telephony designators ("SPEEDBIRD"); only well-known ones map to operator names.
DESIGNATOR_NAMES = {
    'DELTA': 'Delta Air Lines', 'AMERICAN': 'American Airlines', 'UNITED': 'United Airlines',
    'SOUTHWEST': 'Southwest Airlines', 'CHINA SOUTHERN': 'China Southern Airlines', 'FEDEX': 'FedEx Express',
    'RYANAIR': 'Ryanair', 'CHINA EASTERN': 'China Eastern Airlines', 'AIR CHINA': 'Air China',
    'LUFTHANSA': 'Lufthansa', 'SKYWEST': 'SkyWest Airlines', 'AEROFLOT': 'Aeroflot', 'EXECJET': 'NetJets',
    'EASY': 'easyJet', 'SPEEDBIRD': 'British Airways', 'EMIRATES': 'Emirates', 'JETBLUE': 'JetBlue',
    'GERMAN AIR FORCE': 'German Air Force', 'UPS': 'UPS Airlines', 'AIRFRANS': 'Air France',
    'QATARI': 'Qatar Airways', 'ALL NIPPON': 'All Nippon Airways', 'AIR CANADA': 'Air Canada',
    'INDIAN AIRFORCE': 'Indian Air Force', 'HAINAN': 'Hainan Airlines', 'FRENCH AIR FORCE': 'French Air Force',
    'SAUDIA': 'Saudia', 'KOREANAIR': 'Korean Air', 'SCANDINAVIAN': 'SAS', 'UTAIR': 'UTair',
    'SHENZHEN AIR': 'Shenzhen Airlines', 'CATHAY': 'Cathay Pacific', 'SINGAPORE': 'Singapore Airlines',
    'KLM': 'KLM', 'XIAMEN AIR': 'Xiamen Airlines', 'ENVOY': 'Envoy Air', 'SPIRIT WINGS': 'Spirit Airlines',
    'NETHERLANDS AIR FOR': 'Royal Netherlands Air Force', 'ETIHAD': 'Etihad Airways', 'WESTJET': 'WestJet',
    'QANTAS': 'Qantas', 'VUELING': 'Vueling', 'SI CHUAN': 'Sichuan Airlines', 'ALLEGIANT': 'Allegiant Air',
    'AIR BERLIN': 'Air Berlin', 'AIR PORTUGAL': 'TAP Air Portugal', 'AIRINDIA': 'Air India', 'ALITALIA': 'Alitalia',
    'NOR SHUTTLE': 'Norwegian', 'LION INTER': 'Lion Air', 'FRONTIER FLIGHT': 'Frontier Airlines',
    'REACH': 'U.S. Air Force Air Mobility Command', 'AEROMEXICO': 'Aeroméxico', 'COPA': 'Copa Airlines',
    'IBERIA': 'Iberia', 'AUSTRIAN': 'Austrian Airlines', 'JAZZ': 'Jazz Aviation', 'ETHIOPIAN': 'Ethiopian Airlines',
    'ASIANA': 'Asiana Airlines', 'SWISS': 'Swiss International Air Lines', 'DYNASTY': 'China Airlines',
    'SWISS AIR FORCE': 'Swiss Air Force', 'WIZZ AIR': 'Wizz Air', 'SHANGHAI AIR': 'Shanghai Airlines',
    'EVA': 'EVA Air', 'POLISH AIRFORCE': 'Polish Air Force', 'CHANNEX': 'Jet2', 'VOLARIS': 'Volaris',
    'MALAYSIAN': 'Malaysia Airlines', 'THAI': 'Thai Airways', 'EGYPTAIR': 'EgyptAir', 'FINNAIR': 'Finnair',
    'EUROWINGS': 'Eurowings', 'INTERJET': 'Interjet', 'AIR SPRING': 'Spring Airlines',
    'PHILIPPINE': 'Philippine Airlines', 'JETSTAR': 'Jetstar', 'EUROPA': 'Air Europa', 'GIANT': 'Atlas Air',
    'POLLOT': 'LOT Polish Airlines', 'NEW ZEALAND': 'Air New Zealand', 'JUNEYAO AIRLINES': 'Juneyao Airlines',
    'ROYALAIR MAROC': 'Royal Air Maroc', 'HAWAIIAN': 'Hawaiian Airlines', 'SUNEXPRESS': 'SunExpress',
    'SHAMROCK': 'Aer Lingus', 'REDWOOD': 'Virgin America', 'HORIZON AIR': 'Horizon Air', 'CONDOR': 'Condor',
    'SPICEJET': 'SpiceJet', 'ELAL': 'El Al', 'TURKISH': 'Turkish Airlines', 'LAN': 'LATAM Airlines',
    'TAM': 'LATAM Airlines Brasil', 'GOL TRANSPORTE': 'Gol', 'VIET NAM AIRLINES': 'Vietnam Airlines',
    'JET AIRWAYS': 'Jet Airways', 'SIBERIAN AIRLINES': 'S7 Airlines', 'INDONESIA': 'Garuda Indonesia',
    'BRAZILIAN AIR FORCE': 'Brazilian Air Force', 'GERMAN ARMY': 'German Army', 'SHANDONG': 'Shandong Airlines',
    'VIRGIN': 'Virgin Atlantic', 'ALASKA': 'Alaska Airlines',
}


def _text(raw):
    if isinstance(raw, bytes):
        try:
            return raw.decode('utf-8')
        except UnicodeDecodeError:
            return raw.decode('cp1252', 'replace')
    return raw


def read_opensky(path):
    """icao -> (registration, manufacturer, description code, operator designator)."""
    conn = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)
    conn.text_factory = _text
    try:
        rows = conn.execute('SELECT icao24, reg, manufacturer, type, callsign FROM aircrafts').fetchall()
    finally:
        conn.close()
    result = {}
    for icao, reg, manufacturer, desc, callsign in rows:
        icao = valid_icao(icao)
        if icao:
            result[icao] = (reg or '', manufacturer or '', (desc or '').strip().upper(), callsign or '')
    return result


def read_flightaware(folder):
    """icao -> {'r', 't', 'desc'}, following the prefix files and their 'children' lists."""
    folder = Path(folder)
    result = {}
    pending = [p.stem for p in folder.glob('*.json')]
    seen = set()
    while pending:
        prefix = pending.pop()
        if prefix in seen:
            continue
        seen.add(prefix)
        path = folder / f'{prefix}.json'
        if not path.is_file():
            continue
        for key, entry in json.loads(path.read_text('utf-8')).items():
            if key == 'children':
                pending.extend(str(c) for c in entry)
                continue
            icao = valid_icao(prefix + key)
            if icao and isinstance(entry, dict):
                result[icao] = entry
    return result


def read_types(folder):
    path = Path(folder) / 'aircraft_types' / 'icao_aircraft_types.json'
    return {k.upper(): v.get('desc', '') for k, v in json.loads(path.read_text('utf-8')).items()} if path.is_file() else {}


def merge(opensky, flightaware, types):
    records = []
    stats = dict(type_conflicts=0)
    for icao in set(opensky) | set(flightaware):
        os_reg, os_manufacturer, os_desc, designator = opensky.get(icao, ('', '', '', ''))
        fa = flightaware.get(icao, {})
        os_reg, fa_reg = clean_registration(os_reg), clean_registration(fa.get('r'))
        typecode = clean_type(fa.get('t'))
        type_desc = types.get(typecode, '')
        # A dump1090-fa type whose class contradicts OpenSky for a re-registered address is probably stale.
        if typecode and os_desc and type_desc and type_desc != os_desc and \
                (not fa_reg or fa_reg.replace('-', '') != os_reg.replace('-', '')):
            typecode = ''
            stats['type_conflicts'] += 1
        desc = os_desc or (fa.get('desc') or '').strip().upper() or types.get(typecode, '')
        model, manufacturer = describe(typecode, '', tidy(os_manufacturer), desc)
        operator = DESIGNATOR_NAMES.get(' '.join(designator.upper().split()), '')
        record = (icao, os_reg or fa_reg, typecode, model, manufacturer, operator)
        if any(record[1:]):
            records.append(record)
    return records, stats


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--opensky', default=str(DEFAULT_OPENSKY))
    parser.add_argument('--flightaware', default=str(DEFAULT_FLIGHTAWARE))
    parser.add_argument('--out', default=str(ROOT / 'vendor' / 'aircraft' / 'aircraft.sqlite'))
    args = parser.parse_args()
    started = time.time()
    opensky = read_opensky(args.opensky)
    flightaware = read_flightaware(args.flightaware)
    types = read_types(args.flightaware)
    print(f'OpenSky snapshot: {len(opensky):,} addresses · dump1090-fa: {len(flightaware):,} · types: {len(types):,}')
    records, stats = merge(opensky, flightaware, types)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix('.tmp')
    count = write_database(tmp, records, dict(
        source='OpenSky Network aircraft database snapshot with dump1090-fa type codes (bundled)',
        data_date='', url=''))
    tmp.replace(out)
    filled = {name: sum(1 for r in records if r[i]) for i, name in
              enumerate(('registration', 'type', 'model', 'manufacturer', 'operator'), 1)}
    print(f'Wrote {count:,} aircraft to {out} ({out.stat().st_size / 1024 ** 2:.1f} MB) in {time.time() - started:.1f} s')
    print('Filled:', ', '.join(f'{k} {v:,}' for k, v in filled.items()), f'· type conflicts dropped {stats["type_conflicts"]:,}')


if __name__ == '__main__':
    main()
