import time
import re

# pyModeS and pyais take ~80 ms to import and are needed only once a decoder is running,
# so they load when a parser is created rather than when the app starts.


class ADSBParser:
    def __init__(self, home=None):
        from pyModeS import PipeDecoder
        self.pipe = PipeDecoder(surface_ref=tuple(home) if home else None)

    def parse(self, line, now=None):
        match=re.fullmatch(r'\*([0-9A-Fa-f]{14}|[0-9A-Fa-f]{28});',line.strip())
        if not match: return None
        try:
            d=self.pipe.decode(match[1],timestamp=now or time.time())
        except (ValueError,TypeError):
            return None
        if not d or not d.get('icao') or d.get('crc_valid') is False: return None
        result=dict(kind='aircraft',identifier=d['icao'])
        fields={'callsign':'callsign','altitude':'altitude','groundspeed':'speed','track':'heading',
                'vertical_rate':'vertical_speed','squawk':'squawk','latitude':'lat','longitude':'lon'}
        result.update({dest:d[src] for src,dest in fields.items() if d.get(src) is not None})
        return result


class AISParser:
    def __init__(self):
        from pyais import decode
        from pyais.exceptions import AISBaseException
        self._decode, self._decode_error = decode, AISBaseException
        self.fragments = {}

    def parse(self, line, now=None):
        now = now or time.monotonic()
        self.fragments = {k:v for k,v in self.fragments.items() if now-v[0] < 10}
        start = line.find('!AI')
        if start < 0:
            return None
        line = line[start:].strip()
        p = line.split(',')
        if len(p) != 7 or '*' not in p[-1]:
            return None
        # Verify NMEA checksum before accepting fragments.
        try:
            body, checksum = line[1:].split('*')
            check = 0
            for c in body:
                check ^= ord(c)
            if check != int(checksum[:2],16):
                return None
            total, index = int(p[1]), int(p[2])
            if not 1 <= index <= total <= 9:
                return None
            lines = [line]
            if total > 1:
                key = (p[0],p[3],p[4],total)
                if index == 1:
                    self.fragments[key] = (now,{})
                if key not in self.fragments:
                    return None
                parts = self.fragments[key][1]
                parts[index] = line
                if len(parts) != total:
                    return None
                lines = [parts[i] for i in range(1,total+1)]
                del self.fragments[key]
            d = self._decode(*lines).asdict()
        except (ValueError, IndexError, KeyError, TypeError, self._decode_error):
            return None
        if d.get('msg_type') not in (1,2,3,5,18,19,24,27):
            return None
        result = dict(kind='vessel', identifier=str(d['mmsi']))
        fields = {'shipname':'name','callsign':'callsign','imo':'imo','ship_type':'type',
                  'lat':'lat','lon':'lon','speed':'speed','course':'course','heading':'heading',
                  'status':'navigation_status','destination':'destination',
                  'to_bow':'to_bow','to_stern':'to_stern','to_port':'to_port','to_starboard':'to_starboard',
                  'draught':'draught','month':'eta_month','day':'eta_day','hour':'eta_hour','minute':'eta_minute'}
        for source, target in fields.items():
            v = d.get(source)
            if isinstance(v,str):
                v = v.strip('@ ')
            if v is not None and v != '':
                result[target] = int(v) if hasattr(v,'name') else v
        if abs(result.get('lat',91)) > 90 or abs(result.get('lon',181)) > 180:
            result.pop('lat',None); result.pop('lon',None)
        for key, invalid in (('heading',511),('course',360),('speed',102.3),('imo',0)):
            if result.get(key) == invalid:
                result.pop(key,None)
        return result
