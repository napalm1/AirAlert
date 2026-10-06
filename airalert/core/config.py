import json
import os
import time
import math
from pathlib import Path


def data_dir():
    p = Path(os.environ.get('AIRALERT_DATA', Path(os.environ.get('LOCALAPPDATA', Path.home())) / 'AirAlert')).resolve()
    p.mkdir(parents=True, exist_ok=True)
    return p


DEFAULTS = dict(home=None, units='mi', mode='Aircraft', aircraft_device='0', marine_device='1',
                # Simulation mode (fictional traffic, no receiver) is hidden until this is switched on
                show_simulation=False,
                gain='auto', ppm=0, aircraft_seconds=60, marine_seconds=60,
                aircraft_ttl=120, vessel_ttl=900, trail_minutes=15, sample_seconds=10,
                retention_days=90, tiles=False, theme='Dark', map_theme='Follow app',
                map_layers=dict(aircraft=True,vessels=True,trails=True,rings=True,labels=True,
                                airports=True,headings=False,coverage=False),
                rings=[5, 10, 25, 50, 100],
                rules=[], watchlist=[], geofences=[], setup_done=False,
                # Background station
                close_to_tray=True, start_with_windows=False, auto_start=False,
                quiet_hours=dict(enabled=False, start='22:00', end='07:00'),
                # Phone notifications (opt-in)
                phone=dict(ntfy_enabled=False, ntfy_server='https://ntfy.sh', ntfy_topic='', ntfy_token='',
                           pushover_enabled=False, pushover_user='', pushover_token=''),
                # Map display
                heading_minutes=2,
                altitude_filter=dict(enabled=False, min=0, max=50000, include_unknown=True),
                # Snoozed alerts: unix times until which everything, a rule (by id) or a target (by key) stays quiet
                snooze=dict(all=0, rules={}, targets={}),
                daily_summary=dict(enabled=False, time='21:00', phone=True),
                health_warning=True,
                backup=dict(enabled=False, folder='', every_days=7, keep=5),
                # Internet features (all opt-in)
                online_lookup=dict(photos=False, routes=False),
                check_updates=True,
                phone_map=dict(enabled=False, port=8765, sessions=[]))

CONDITIONS = ('enter', 'leave', 'first', 'signal', 'zone_enter', 'zone_leave', 'altitude_below', 'speed_above',
              'approach', 'squawk_emergency', 'first_aircraft', 'first_type', 'first_operator', 'rare_type',
              'military', 'circling')
EXTRA_CONDITIONS = ('within', 'beyond', 'altitude_below', 'altitude_above', 'speed_above', 'speed_below',
                    'in_zone', 'out_zone')


def _is_clock(value):
    try:
        hours, minutes = str(value).split(':')
        return 0 <= int(hours) <= 23 and 0 <= int(minutes) <= 59 and len(minutes) == 2
    except ValueError:
        return False


class Config:
    def __init__(self, folder=None):
        self.folder = Path(folder or data_dir()).resolve()
        self.folder.mkdir(parents=True, exist_ok=True)
        self.path = self.folder / 'settings.json'
        self.warning = ''
        self.values = json.loads(json.dumps(DEFAULTS))
        if self.path.exists():
            try:
                loaded = json.loads(self.path.read_text('utf-8'))
                self.validate(loaded)
                if loaded.get('mode') == 'Simulation' and 'show_simulation' not in loaded:
                    loaded['show_simulation'] = True   # settings from before the switch existed keep working
                for key, value in loaded.items():
                    # Nested settings gain new default keys added by later versions.
                    if isinstance(DEFAULTS.get(key), dict) and isinstance(value, dict):
                        self.values[key] = dict(DEFAULTS[key], **value)
                    else:
                        self.values[key] = value
            except (ValueError, TypeError, OSError) as e:
                self.warning = f'Settings could not be read. Defaults restored; original preserved. {e}'
                self.path.rename(self.path.with_suffix(f'.invalid-{time.time_ns()}.json'))

    @staticmethod
    def validate(d):
        if not isinstance(d, dict):
            raise ValueError('Settings must be an object')
        h = d.get('home')
        if h is not None and (not isinstance(h, list) or len(h) != 2 or
                              not all(isinstance(v, (int, float)) for v in h) or
                              not -90 <= h[0] <= 90 or not -180 <= h[1] <= 180):
            raise ValueError('Invalid home coordinates')
        for k in ('aircraft_seconds', 'marine_seconds', 'aircraft_ttl', 'vessel_ttl', 'sample_seconds', 'retention_days'):
            if k in d and (not isinstance(d[k], (int, float)) or not 1 <= d[k] <= 1000000):
                raise ValueError(f'Invalid {k}')
        for k in ('rules', 'watchlist', 'geofences', 'rings'):
            if k in d and not isinstance(d[k], list):
                raise ValueError(f'Invalid {k}')
        if d.get('units', 'mi') not in ('mi', 'nm', 'km'):
            raise ValueError('Invalid distance units')
        if d.get('mode','Aircraft') not in ('Simulation','Aircraft','Marine','Automatic switching','Dual receivers'):
            raise ValueError('Invalid monitoring mode')
        if not isinstance(d.get('show_simulation', False), bool):
            raise ValueError('Invalid show_simulation')
        if d.get('theme','Dark') not in ('Dark','Light'):
            raise ValueError('Invalid appearance')
        if d.get('map_theme','Follow app') not in ('Follow app','Dark','Light','Scope only'):
            raise ValueError('Invalid map appearance')
        layers=d.get('map_layers',{})
        if not isinstance(layers,dict) or any(k not in DEFAULTS['map_layers'] or not isinstance(v,bool) for k,v in layers.items()):
            raise ValueError('Invalid map layers')
        if not isinstance(d.get('trail_minutes',15),(int,float)) or not 0<=d.get('trail_minutes',15)<=1440:
            raise ValueError('Invalid trail duration')
        if not isinstance(d.get('ppm',0),(int,float)) or not -150<=d.get('ppm',0)<=150:
            raise ValueError('Invalid PPM')
        gain=d.get('gain','auto')
        if gain!='auto' and (not isinstance(gain,(str,int,float)) or not 0<=float(gain)<=50):
            raise ValueError('Invalid gain')
        for r in d.get('rules',[]):
            if not isinstance(r,dict) or not all(k in r for k in ('id','name','kind','condition')):
                raise ValueError('Invalid alert rule')
            if not isinstance(r.get('threshold',0),(int,float)) or not math.isfinite(r.get('threshold',0)):
                raise ValueError('Invalid alert threshold')
            if r['kind'] not in ('aircraft','vessel','any') or r['condition'] not in CONDITIONS:
                raise ValueError('Invalid alert condition')
            if not isinstance(r.get('cooldown',60),(int,float)) or not 0<=r.get('cooldown',60)<=86400:
                raise ValueError('Invalid cooldown')
            if not isinstance(r.get('lookahead',5),(int,float)) or not 1<=r.get('lookahead',5)<=60:
                raise ValueError('Invalid approach look-ahead')
            for flag in ('sound','desktop','speak','phone','override_quiet','enabled'):
                if flag in r and not isinstance(r[flag],bool):
                    raise ValueError(f'Invalid alert option {flag}')
            extra=r.get('extra',[])
            if not isinstance(extra,list) or len(extra)>6:
                raise ValueError('Invalid extra alert conditions')
            for e in extra:
                if not isinstance(e,dict) or e.get('condition') not in EXTRA_CONDITIONS:
                    raise ValueError('Invalid extra alert condition')
                if not isinstance(e.get('threshold',0),(int,float)) or not math.isfinite(e.get('threshold',0)):
                    raise ValueError('Invalid extra alert threshold')
        for w in d.get('watchlist',[]):
            if not isinstance(w,dict) or not all(k in w for k in ('identifier','kind')):
                raise ValueError('Invalid watchlist entry')
        for z in d.get('geofences',[]):
            if not isinstance(z,dict) or not isinstance(z.get('name'),str) or len(z.get('points',[]))<3:
                raise ValueError('Invalid geofence')
            for point in z['points']:
                Config.validate({'home':list(point)})
        for r in d.get('rings',[]):
            if not isinstance(r,(int,float)) or not 0<r<=2000:
                raise ValueError('Invalid range ring')
        for k in ('close_to_tray','start_with_windows','auto_start'):
            if k in d and not isinstance(d[k],bool):
                raise ValueError(f'Invalid {k}')
        quiet=d.get('quiet_hours',{})
        if not isinstance(quiet,dict) or not isinstance(quiet.get('enabled',False),bool) or \
                not _is_clock(quiet.get('start','22:00')) or not _is_clock(quiet.get('end','07:00')):
            raise ValueError('Invalid quiet hours')
        phone=d.get('phone',{})
        if not isinstance(phone,dict) or any(not isinstance(phone[k],bool) for k in ('ntfy_enabled','pushover_enabled') if k in phone) \
                or any(not isinstance(v,str) for k,v in phone.items() if not k.endswith('_enabled')):
            raise ValueError('Invalid phone notification settings')
        if not isinstance(d.get('heading_minutes',2),(int,float)) or not 1<=d.get('heading_minutes',2)<=30:
            raise ValueError('Invalid heading line length')
        band=d.get('altitude_filter',{})
        if not isinstance(band,dict) or not isinstance(band.get('enabled',False),bool) or \
                not isinstance(band.get('include_unknown',True),bool) or \
                not all(isinstance(band.get(k,0),(int,float)) for k in ('min','max')) or \
                not 0<=band.get('min',0)<=band.get('max',50000)<=100000:
            raise ValueError('Invalid altitude filter')
        snooze=d.get('snooze',{})
        if not isinstance(snooze,dict) or not isinstance(snooze.get('all',0),(int,float)) or                 any(not isinstance(snooze.get(k,{}),dict) or
                    any(not isinstance(v,(int,float)) for v in snooze.get(k,{}).values()) for k in ('rules','targets')):
            raise ValueError('Invalid snooze settings')
        summary=d.get('daily_summary',{})
        if not isinstance(summary,dict) or not isinstance(summary.get('enabled',False),bool) or                 not isinstance(summary.get('phone',True),bool) or not _is_clock(summary.get('time','21:00')):
            raise ValueError('Invalid daily summary settings')
        backup=d.get('backup',{})
        if not isinstance(backup,dict) or not isinstance(backup.get('enabled',False),bool) or                 not isinstance(backup.get('folder',''),str) or                 not isinstance(backup.get('every_days',7),(int,float)) or not 1<=backup.get('every_days',7)<=365 or                 not isinstance(backup.get('keep',5),(int,float)) or not 1<=backup.get('keep',5)<=50:
            raise ValueError('Invalid backup settings')
        online=d.get('online_lookup',{})
        if not isinstance(online,dict) or any(not isinstance(v,bool) for v in online.values()):
            raise ValueError('Invalid online lookup settings')
        for k in ('health_warning','check_updates'):
            if k in d and not isinstance(d[k],bool):
                raise ValueError(f'Invalid {k}')
        web=d.get('phone_map',{})
        if not isinstance(web,dict) or not isinstance(web.get('enabled',False),bool) or                 not isinstance(web.get('port',8765),int) or not 1024<=web.get('port',8765)<=65535 or                 not isinstance(web.get('sessions',[]),list) or any(not isinstance(v,str) for v in web.get('sessions',[])):
            raise ValueError('Invalid phone map settings')

    def save(self):
        self.validate(self.values)
        tmp = self.path.with_suffix('.tmp')
        tmp.write_text(json.dumps(self.values, indent=2), 'utf-8')
        tmp.replace(self.path)

    def __getitem__(self, key):
        return self.values[key]
