"""Facts about an aircraft that alert rules can ask for: new or rare at this station, military, circling.

They are stored in ``target.traits`` and answered by ``core.alerts`` (TRAIT_CONDITIONS). Database lookups run
once per aircraft and only for the kinds of alert that are actually in use. "Seen before" compares against
the same kind of history (received traffic against received, simulation against simulation).
"""
import re

from .core.alerts import TRAIT_CONDITIONS
from .phase import is_circling

RARE_DAYS = 30
AIRLINE_CALLSIGN = re.compile(r'^[A-Z]{3}[0-9]')
# ICAO 24-bit address blocks that states reserve for military aircraft (the commonly published ones).
# A heuristic: some military aircraft use civil addresses and the list is not complete.
MILITARY_RANGES = [
    (0xADF7C8, 0xAFFFFF), (0x010070, 0x01008F), (0x0A4000, 0x0A4FFF), (0x33FF00, 0x33FFFF), (0x350000, 0x37FFFF),
    (0x3AA000, 0x3AFFFF), (0x3B7000, 0x3BFFFF), (0x3EA000, 0x3EBFFF), (0x3F4000, 0x3FBFFF), (0x400000, 0x40003F),
    (0x43C000, 0x43CFFF), (0x444000, 0x446FFF), (0x44F000, 0x44FFFF), (0x457000, 0x457FFF), (0x45F400, 0x45F4FF),
    (0x468000, 0x4683FF), (0x473C00, 0x473C0F), (0x478100, 0x4781FF), (0x480000, 0x480FFF), (0x48D800, 0x48D87F),
    (0x497C00, 0x497CFF), (0x498420, 0x49842F), (0x4B7000, 0x4B7FFF), (0x4B8200, 0x4B82FF), (0x70C070, 0x70C07F),
    (0x710258, 0x71028F), (0x710380, 0x71039F), (0x738A00, 0x738AFF), (0x7CF800, 0x7CFAFF), (0x800200, 0x8002FF),
    (0xC20000, 0xC3FFFF), (0xE40000, 0xE41FFF), (0xE80600, 0xE806FF),
]
MILITARY_WORDS = ('air force', 'navy', 'army', 'marine corps', 'marines', 'coast guard', 'national guard',
                  'air national', 'military', 'luftwaffe', 'royal air', 'armee', 'fuerza aerea', 'aeronautica militare',
                  'department of defense', 'air mobility', 'usaf', 'us navy', 'us army')


def is_military(icao, operator=''):
    """True for an address in a military block or an operator that names armed forces."""
    try:
        address = int(str(icao), 16)
    except ValueError:
        address = -1
    if any(low <= address <= high for low, high in MILITARY_RANGES):
        return True
    text = str(operator or '').casefold()
    return any(word in text for word in MILITARY_WORDS)


def airline_prefix(callsign):
    text = str(callsign or '').strip().upper()
    return text[:3] if AIRLINE_CALLSIGN.match(text) else ''


def conditions_in_use(rules):
    return {r.get('condition') for r in rules if r.get('enabled', True)} & set(TRAIT_CONDITIONS)


def update(target, db, needed, new, now):
    """Fill in the traits the active rules need. Call before the target's sighting is (re)recorded."""
    if target.kind != 'aircraft':
        return
    traits, data, key = target.traits, target.data, db.dbkey(target)
    if new:
        # Cheap, so always known: one primary-key lookup and no database access at all.
        traits['first_aircraft'] = not db.sighting_exists(key)
        traits['military'] = is_military(target.identifier, data.get('operator'))
    if not needed:
        return
    if needed & {'first_type', 'rare_type'} and 'type_count' not in traits and data.get('type'):
        ever, recent = db.type_history(str(data['type']), target.simulated, key, now - RARE_DAYS * 86400)
        traits['first_type'] = ever == 0
        traits['type_count'] = recent
    if 'first_operator' in needed and 'first_operator' not in traits:
        prefix = airline_prefix(data.get('callsign'))
        if prefix:
            traits['first_operator'] = not db.operator_seen(prefix, target.simulated, key)
    if 'military' in needed and not traits.get('military') and data.get('operator'):
        traits['military'] = is_military(target.identifier, data['operator'])   # the operator may arrive later
    if 'circling' in needed:
        traits['circling'] = is_circling(target, now)
