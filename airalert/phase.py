"""What an aircraft is doing: climbing, descending, level, on approach, departing or circling.

Worked out locally from the altitude, vertical rate, track and recent trail, plus the bundled airport data.
Airport elevations are not in that data, so "low" means below LOW_FEET above sea level: high-elevation
airports get fewer approach/departure tags, never wrong ones.
"""
import math

from .core.models import UNIT_KM, distance_bearing

CLIMB_FPM = 300
LOW_FEET = 6000
APPROACH_KM = 28           # about 15 nautical miles
DEPART_KM = 18
CIRCLE_WINDOW = 420        # seconds of trail examined for circling
CIRCLE_TURN = 330          # degrees turned in one direction, net
CIRCLE_EXTENT_KM = 14      # ...while staying inside a box this size
COMPASS8 = ('N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW')


def _angle(a, b):
    """Signed smallest turn from bearing a to bearing b, in degrees (-180..180]."""
    return (b - a + 180) % 360 - 180


def turning(trail, now, window=CIRCLE_WINDOW):
    """(net degrees turned, extent km) over the last `window` seconds of a trail of (time, lat, lon)."""
    points = []
    for sample in reversed(trail):
        if now - sample[0] > window:
            break
        if not points or points[-1][0] - sample[0] >= 8:   # about one point every 8 s is plenty
            points.append(sample)
    if len(points) < 6 or points[0][0] - points[-1][0] < 90:
        return 0.0, 0.0
    points.reverse()
    bearings = []
    for a, b in zip(points, points[1:]):
        km, bearing = distance_bearing(a[1:], b[1:])
        if km >= 0.12:                                      # ignore jitter while (nearly) stationary
            bearings.append(bearing)
    total = sum(_angle(a, b) for a, b in zip(bearings, bearings[1:]))
    lats = [p[1] for p in points]
    lons = [p[2] for p in points]
    extent = max(distance_bearing((min(lats), min(lons)), (max(lats), max(lons)))[0], 0.0)
    return total, extent


def is_circling(target, now):
    """True when the aircraft has turned (nearly) a full circle one way while staying in a small area."""
    if target.kind != 'aircraft' or len(target.trail) < 6:
        return False
    cached = target.traits.get('_circling')
    stamp = target.trail[-1][0]
    if cached and cached[0] == stamp:
        return cached[1]
    total, extent = turning(target.trail, now)
    result = abs(total) >= CIRCLE_TURN and extent <= CIRCLE_EXTENT_KM
    target.traits['_circling'] = (stamp, result)
    return result


def nearest_airport(position, index, radius_km=APPROACH_KM):
    """(code, km, bearing from the airport to the position) of the nearest airport or airfield, or None."""
    if index is None or not position:
        return None
    found = index.nearest(position[0], position[1], radius_km)
    if found is None:
        return None
    i, km = found
    bearing = distance_bearing((index.lats[i], index.lons[i]), position)[1]
    return index.codes[i], km, bearing


def describe_airport(position, index, units, radius_km=APPROACH_KM):
    """'KSTS · 4.2 mi NE' (where the aircraft is from the airport), or ''."""
    found = nearest_airport(position, index, radius_km)
    if not found:
        return ''
    code, km, bearing = found
    return f'{code} · {km / UNIT_KM[units]:.1f} {units} {COMPASS8[int((bearing + 22.5) // 45) % 8]}'


def flight_phase(target, now, index=None, units='mi'):
    """dict(code, text) for an aircraft; code is '' when nothing can be said (no data, stale, vessel)."""
    nothing = dict(code='', text='')
    if target.kind != 'aircraft' or not target.position or now - target.position_time > 60:
        return nothing
    data = target.data
    altitude, rate, track = data.get('altitude'), data.get('vertical_speed'), data.get('heading')
    signature = (target.position_time, altitude, rate, track, units, index is not None)
    cached = target.traits.get('_phase')
    if cached and cached[0] == signature:
        return cached[1]
    result = nothing
    if is_circling(target, now):
        result = dict(code='circling', text='Circling')
    elif rate is not None:
        low = altitude is not None and altitude < LOW_FEET
        airport = nearest_airport(target.position, index) if low and track is not None else None
        if airport:
            code, km, from_airport = airport
            distance = f'{km / UNIT_KM[units]:.0f} {units}' if km / UNIT_KM[units] >= 1 else 'under 1 ' + units
            # Heading at the airport = track opposite to the bearing from the airport to the aircraft.
            toward = abs(_angle(track, (from_airport + 180) % 360)) <= 25
            away = abs(_angle(track, from_airport)) <= 40
            if rate <= -200 and toward:
                result = dict(code='approach', text=f'On approach to {code} · {distance} out')
            elif rate >= CLIMB_FPM and away and km <= DEPART_KM:
                result = dict(code='departure', text=f'Departing {code}')
        if not result['code']:
            if rate >= CLIMB_FPM:
                result = dict(code='climb', text='Climbing')
            elif rate <= -CLIMB_FPM:
                result = dict(code='descent', text='Descending')
            elif altitude is not None and math.isfinite(altitude):
                result = dict(code='level', text='Level')
    target.traits['_phase'] = (signature, result)
    return result
