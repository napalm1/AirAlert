import math

from .models import distance_bearing, in_polygon, matches_identity

EMERGENCY_SQUAWKS = {'7500': 'hijack', '7600': 'radio failure', '7700': 'emergency'}
FRESH_SECONDS = 60
# Conditions answered by facts the station works out per aircraft (airalert/traits.py); None = not known yet.
TRAIT_CONDITIONS = ('first_aircraft', 'first_type', 'first_operator', 'rare_type', 'military', 'circling')


def squawk_code(target):
    """Four-digit squawk string, or None."""
    value = target.data.get('squawk')
    if value is None or str(value).strip() == '':
        return None
    text = str(value).strip()
    return text.zfill(4) if text.isdigit() else text


def emergency_kind(target):
    """'emergency' / 'hijack' / 'radio failure' for emergency squawks, else ''."""
    return EMERGENCY_SQUAWKS.get(squawk_code(target) or '', '') if target.kind == 'aircraft' else ''


def track_of(target):
    for key in (('course', 'heading') if target.kind == 'vessel' else ('heading', 'course')):
        if target.data.get(key) is not None:
            return float(target.data[key])
    return None


def closest_approach(home, target):
    """(closest distance km, seconds until then) along the current track, or None.

    Straight-line prediction in a local east/north plane around home; accurate
    for the tens-of-km distances that matter for "passing overhead".
    """
    track, speed = track_of(target), target.data.get('speed')
    if not home or not target.position or track is None or not speed or speed <= 0.5:
        return None
    d, b = distance_bearing(home, target.position)
    x, y = d * math.sin(math.radians(b)), d * math.cos(math.radians(b))
    v = speed * 1.852 / 3600  # km per second
    vx, vy = v * math.sin(math.radians(track)), v * math.cos(math.radians(track))
    t = max(0.0, -(x * vx + y * vy) / (vx * vx + vy * vy))
    return math.hypot(x + vx * t, y + vy * t), t


def _fresh(target, now):
    return target.position is not None and now - target.position_time <= FRESH_SECONDS


def extra_satisfied(e, target, home, zones, now):
    """Additional AND condition of a rule. Unknown data never satisfies it."""
    c, threshold = e.get('condition'), e.get('threshold', 0)
    if c in ('within', 'beyond'):
        if not home or not _fresh(target, now):
            return False
        d = distance_bearing(home, target.position)[0]
        return d <= threshold if c == 'within' else d > threshold
    if c in ('in_zone', 'out_zone'):
        zone = next((z for z in zones if z['name'] == e.get('zone')), None)
        if not zone or not _fresh(target, now):
            return False
        inside = in_polygon(target.position, zone['points'])
        return inside if c == 'in_zone' else not inside
    if c in ('altitude_below', 'altitude_above', 'speed_above', 'speed_below'):
        v = target.data.get('altitude' if c.startswith('altitude') else 'speed')
        if v is None:
            return False
        return v < threshold if c.endswith('below') else v > threshold
    return False


class AlertEngine:
    def __init__(self):
        self.states = {}
        self.fired = {}

    def evaluate(self, target, rules, home, zones, now):
        events = []
        for r in rules:
            if not r.get('enabled', True) or r['kind'] not in ('any', target.kind):
                continue
            if not matches_identity(target, r.get('field', 'identifier'), r.get('value', '')):
                continue
            key = (r['id'], target.key)
            condition = r.get('condition', 'enter')
            inside = False
            detail = {}
            if condition in ('first', 'signal'):
                inside = True
            elif condition in ('enter', 'leave'):
                if not home or not target.position or now-target.position_time > 60:
                    continue
                inside = distance_bearing(home, target.position)[0] <= r['threshold']
            elif condition in ('zone_enter', 'zone_leave'):
                zone = next((z for z in zones if z['name'] == r.get('zone')), None)
                if not zone or not target.position or now-target.position_time > 60:
                    continue
                inside = in_polygon(target.position, zone['points'])
            elif condition in ('altitude_below', 'speed_above'):
                v = target.data.get('altitude' if condition == 'altitude_below' else 'speed')
                if v is None:
                    continue
                inside = v < r['threshold'] if condition == 'altitude_below' else v > r['threshold']
            elif condition == 'squawk_emergency':
                kind = emergency_kind(target)
                inside = bool(kind)
                detail = dict(squawk=squawk_code(target), emergency=kind)
            elif condition == 'approach':
                if not home or not _fresh(target, now):
                    continue
                current = distance_bearing(home, target.position)[0]
                predicted = closest_approach(home, target)
                if current <= r['threshold']:
                    inside = True
                    detail = dict(cpa_km=current, eta_s=0.0)
                elif predicted is not None:
                    cpa, eta = predicted
                    inside = cpa <= r['threshold'] and eta <= r.get('lookahead', 5) * 60
                    detail = dict(cpa_km=cpa, eta_s=eta)
            elif condition in TRAIT_CONDITIONS:
                if target.kind != 'aircraft':
                    continue
                if condition == 'rare_type':
                    count = target.traits.get('type_count')
                    if count is None:
                        continue
                    inside = count < max(1, r.get('threshold', 3))
                    detail = dict(trait=condition, type_count=count)
                else:
                    value = target.traits.get(condition)
                    if value is None:
                        continue
                    inside = bool(value)
                    detail = dict(trait=condition)
            leaving = condition in ('leave', 'zone_leave')
            extras = r.get('extra') or []
            extras_ok = all(extra_satisfied(e, target, home, zones, now) for e in extras) if extras else True
            if not leaving:
                # AND conditions: the rule becomes true only when every condition holds.
                inside = inside and extras_ok
            previous = self.states.get(key)
            crossing = previous is True and not inside if leaving else inside and previous is not True
            if leaving and not extras_ok:
                crossing = False  # the exit happened, but not while the extra conditions held
            self.states[key] = inside
            if crossing and now-self.fired.get(key, -1e20) >= r.get('cooldown', 60):
                self.fired[key] = now
                events.append(dict(rule=r, target=target.key, text=f"{r['name']} · {target.label}", detail=detail))
        return events

    def expire(self, keys):
        # Preserve cooldown across target expiration, but re-arm appearance/entry.
        for key in list(self.states):
            if key[1] in keys:
                del self.states[key]

    def prune(self, now):
        for k in list(self.fired):
            if now-self.fired[k] > 86400 and k not in self.states:
                del self.fired[k]
