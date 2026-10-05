import math
from dataclasses import dataclass, field
from collections import deque

UNIT_KM = {'mi': 1.609344, 'nm': 1.852, 'km': 1.0}


def distance_bearing(a, b):
    lat1, lat2 = math.radians(a[0]), math.radians(b[0])
    dl = math.radians(b[1] - a[1])
    v = math.sin((lat2-lat1)/2)**2 + math.cos(lat1)*math.cos(lat2)*math.sin(dl/2)**2
    distance = 6371.0088 * 2 * math.atan2(math.sqrt(min(1, v)), math.sqrt(max(0, 1-v)))
    bearing = math.degrees(math.atan2(math.sin(dl)*math.cos(lat2),
                          math.cos(lat1)*math.sin(lat2)-math.sin(lat1)*math.cos(lat2)*math.cos(dl))) % 360
    return distance, bearing


def destination(home, km, bearing):
    p, l, b, d = math.radians(home[0]), math.radians(home[1]), math.radians(bearing), km/6371.0088
    lat = math.asin(math.sin(p)*math.cos(d)+math.cos(p)*math.sin(d)*math.cos(b))
    lon = l + math.atan2(math.sin(b)*math.sin(d)*math.cos(p), math.cos(d)-math.sin(p)*math.sin(lat))
    return math.degrees(lat), (math.degrees(lon)+180)%360-180


def in_polygon(point, polygon):
    # Unwrap around the tested longitude to handle zones crossing the date line.
    y, origin = point
    pts = [(lat, (lon-origin+180)%360-180) for lat, lon in polygon]
    inside = False
    if len(pts) < 3:
        return False
    for (y1, x1), (y2, x2) in zip(pts, pts[1:]+pts[:1]):
        if (y1 > y) != (y2 > y) and 0 < (x2-x1)*(y-y1)/(y2-y1)+x1:
            inside = not inside
    return inside


@dataclass
class Target:
    kind: str
    identifier: str
    first_seen: float
    last_seen: float
    data: dict = field(default_factory=dict)
    trail: deque = field(default_factory=lambda: deque(maxlen=7200))
    position_time: float = 0
    simulated: bool = False
    traits: dict = field(default_factory=dict)   # derived facts: rarity, military, circling, cached flight phase

    @property
    def key(self):
        return f'{self.kind}:{self.identifier}'

    @property
    def label(self):
        return self.data.get('registration') or self.data.get('name') or self.data.get('callsign') or self.identifier

    @property
    def position(self):
        if self.data.get('lat') is not None and self.data.get('lon') is not None:
            return self.data['lat'], self.data['lon']


class TargetStore:
    def __init__(self):
        self.targets = {}

    def update(self, msg, now):
        kind, ident = msg['kind'], str(msg['identifier']).upper()
        key = f'{kind}:{ident}'
        new = key not in self.targets
        t = self.targets.setdefault(key, Target(kind, ident, now, now))
        t.last_seen = now
        t.simulated = msg.get('simulated', False)
        t.data.update({k: v for k, v in msg.items() if k not in ('kind', 'identifier', 'simulated') and v is not None})
        if msg.get('lat') is not None and msg.get('lon') is not None:
            if not (-90 <= msg['lat'] <= 90 and -180 <= msg['lon'] <= 180):
                t.data.pop('lat', None)
                t.data.pop('lon', None)
            else:
                t.position_time = now
                if not t.trail or now-t.trail[-1][0] >= 2:
                    t.trail.append((now, *t.position))
        return t, new

    def expire(self, now, aircraft_ttl=120, vessel_ttl=900):
        expired = [k for k, t in self.targets.items() if now-t.last_seen > (aircraft_ttl if t.kind == 'aircraft' else vessel_ttl)]
        for k in expired:
            del self.targets[k]
        return expired


def matches_identity(target, field, value):
    if not value.strip():
        return True
    actual = target.identifier if field in ('identifier', 'icao', 'mmsi') else target.data.get(field, '')
    return str(actual).strip().casefold() == value.strip().casefold()
