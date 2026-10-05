"""UI-independent monitoring engine.

Receivers or simulation -> target store -> alert rules -> SQLite history.
The Qt Quick interface and the tests drive this same object.
"""
import json
import logging
import queue
import sqlite3
import time
import uuid
from datetime import datetime

from . import airports, phase, traits
from .core.alerts import AlertEngine, emergency_kind, squawk_code
from .core.config import Config, EXTRA_CONDITIONS
from .core.database import Database, export_rows
from .core.models import UNIT_KM, Target, TargetStore, distance_bearing
from .core.receivers import ReceiverManager
from .core.simulation import Simulation

log = logging.getLogger(__name__)

MODES = ['Simulation', 'Aircraft', 'Marine', 'Automatic switching', 'Dual receivers']
KINDS = ['aircraft', 'vessel', 'any']
FIELDS = ['registration', 'icao', 'callsign', 'type', 'squawk', 'mmsi', 'name', 'imo', 'identifier']
CONDITIONS = ['enter', 'leave', 'approach', 'first', 'signal', 'altitude_below', 'speed_above', 'zone_enter',
              'zone_leave', 'squawk_emergency', 'first_aircraft', 'first_type', 'first_operator', 'rare_type',
              'military', 'circling']
TRAIT_SENTENCES = {'first_aircraft': 'is heard at this station for the first time ever',
                   'first_type': 'is a type never seen at this station before',
                   'first_operator': 'flies for an airline never seen at this station before',
                   'military': 'is a military aircraft', 'circling': 'is circling (a full turn within a small area)'}
DISTANCE_CONDITIONS = ('enter', 'leave', 'approach')
ZONE_CONDITIONS = ('zone_enter', 'zone_leave')
DISTANCE_EXTRAS = ('within', 'beyond')
ZONE_EXTRAS = ('in_zone', 'out_zone')
STALE_SECONDS = 60
HISTORY_CATEGORIES = ['sightings', 'alert', 'detection', 'receiver', 'application']
UNIT_WORDS = {'mi': 'miles', 'nm': 'nautical miles', 'km': 'kilometers'}
COMPASS = ['north', 'northeast', 'east', 'southeast', 'south', 'southwest', 'west', 'northwest']
# Receiver settings that cannot change while a decoder owns the receiver.
RECEIVER_KEYS = ('mode', 'aircraft_device', 'marine_device', 'aircraft_seconds', 'marine_seconds', 'gain', 'ppm')
# AIS navigational status (ITU-R M.1371).
NAV_STATUS = {0: 'Under way (engine)', 1: 'At anchor', 2: 'Not under command', 3: 'Restricted manoeuvrability',
              4: 'Constrained by draught', 5: 'Moored', 6: 'Aground', 7: 'Fishing', 8: 'Under way (sailing)',
              11: 'Towing astern', 12: 'Pushing / towing alongside', 14: 'Emergency beacon (AIS-SART)'}
# AIS ship and cargo types; the tens digit groups most of them.
SHIP_TYPES = {30: 'Fishing', 31: 'Towing', 32: 'Towing (large)', 33: 'Dredging / underwater ops', 34: 'Diving ops',
              35: 'Military ops', 36: 'Sailing', 37: 'Pleasure craft', 50: 'Pilot vessel', 51: 'Search and rescue',
              52: 'Tug', 53: 'Port tender', 54: 'Anti-pollution', 55: 'Law enforcement', 58: 'Medical transport',
              59: 'Noncombatant ship'}
SHIP_TYPE_GROUPS = {2: 'Wing in ground', 4: 'High-speed craft', 6: 'Passenger', 7: 'Cargo', 8: 'Tanker', 9: 'Other'}


def _code(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return int(number) if number.is_integer() else None


def ship_type_name(value):
    """'Cargo' for AIS ship type 70 etc.; text values pass through; '' when unknown or not available."""
    code = _code(value)
    if code is None:
        return str(value).strip() if value is not None else ''
    if not 20 <= code <= 99:
        return ''
    return SHIP_TYPES.get(code) or SHIP_TYPE_GROUPS.get(code // 10, '')


def nav_status_name(value):
    code = _code(value)
    if code is None:
        return str(value).strip() if value is not None else ''
    return NAV_STATUS.get(code, '')


def compass(bearing):
    return COMPASS[int((bearing % 360 + 22.5) // 45) % 8]


def extra_sentence(e, units):
    c, threshold = e.get('condition'), e.get('threshold', 0) or 0
    if c in DISTANCE_EXTRAS:
        return f"is {'within' if c == 'within' else 'beyond'} {round(threshold / UNIT_KM[units], 2):g} {units} of home"
    if c in ZONE_EXTRAS:
        return f"is {'inside' if c == 'in_zone' else 'outside'} geofence “{e.get('zone') or '?'}”"
    return {'altitude_below': f'is below {threshold:g} ft', 'altitude_above': f'is above {threshold:g} ft',
            'speed_above': f'is faster than {threshold:g} kn',
            'speed_below': f'is slower than {threshold:g} kn'}.get(c, c)


def stamp(ts):
    return datetime.fromtimestamp(ts).strftime('%Y-%m-%d %H:%M:%S') if ts else 'Unknown'


def is_icao(text):
    return len(text) == 6 and all(c in '0123456789abcdefABCDEF' for c in text)


def rule_sentence(rule, units):
    """Plain-language description of an alert rule."""
    kind = {'aircraft': 'aircraft', 'vessel': 'vessel', 'any': 'target'}.get(rule.get('kind'), 'target')
    value = (rule.get('value') or '').strip()
    who = f"{'An' if kind[0] in 'aeiou' else 'A'} {kind} with {rule.get('field', 'identifier')} {value}" if value else f'Any {kind}'
    c = rule.get('condition', 'enter')
    threshold = rule.get('threshold', 0) or 0
    if c == 'approach':
        what = (f"is predicted to pass within {round(threshold / UNIT_KM[units], 2):g} {units} of home "
                f"in the next {rule.get('lookahead', 5):g} min")
    elif c in DISTANCE_CONDITIONS:
        what = f"{'comes within' if c == 'enter' else 'moves beyond'} {round(threshold / UNIT_KM[units], 2):g} {units} of home"
    elif c in ZONE_CONDITIONS:
        what = f"{'enters' if c == 'zone_enter' else 'leaves'} geofence “{rule.get('zone') or '?'}”"
    elif c == 'altitude_below':
        what = f'flies below {threshold:g} ft'
    elif c == 'speed_above':
        what = f'moves faster than {threshold:g} kn'
    elif c == 'signal':
        what = 'is heard (first valid message this sighting)'
    elif c == 'squawk_emergency':
        what = 'squawks an emergency code (7500, 7600 or 7700)'
    elif c == 'rare_type':
        what = f'is a type seen fewer than {max(1, threshold):g} times here in the last {traits.RARE_DAYS} days'
    elif c in TRAIT_SENTENCES:
        what = TRAIT_SENTENCES[c]
    else:
        what = 'is first detected this sighting'
    extras = [extra_sentence(e, units) for e in rule.get('extra') or []]
    return f'{who} {what}' + ''.join(f' and {x}' for x in extras)


class Station:
    def __init__(self, config=None, maintenance=True):
        self.config = config or Config()
        self.db = Database(self.config.folder / 'history.sqlite')
        self.store = TargetStore()
        self.alerts = AlertEngine()
        self.manager = None
        self.sim = None
        self.running = False
        self.mode = self.config['mode']
        self.receiver_health = {}
        self.received = 0
        self.rate = 0
        self.session = None
        self.sim_time = time.monotonic()
        self.last_maintenance = time.time()
        self._distance_cache = None
        self.paused_reason = ''
        if maintenance:
            self.db.maintenance(time.time() - self.config['retention_days'] * 86400)
        self._close_open_sessions()
        self.db.event('application', '', 'Application started')
        self.db.conn.commit()

    # ------------------------------------------------------------------ lifecycle
    def start(self, mode=None):
        """Begin monitoring. Returns '' on success or a readable reason."""
        if self.running:
            return ''
        if self.manager and self.manager.is_alive():
            return 'The decoder is still releasing the receiver. Try again in a moment.'
        mode = mode or self.config['mode']
        if mode not in MODES:
            return f'Unknown monitoring mode: {mode}'
        if mode == 'Simulation' and not self.config['home']:
            return 'Set a home location in Settings before starting simulation.'
        manager = None
        if mode != 'Simulation':
            try:
                manager = ReceiverManager(dict(self.config.values, mode=mode), self.config.folder)
            except ValueError as e:
                return str(e)
        self.store = TargetStore()
        self.alerts = AlertEngine()
        self.receiver_health = {}
        self.config.values['mode'] = mode
        self.config.save()
        self.mode = mode
        if manager:
            self.manager = manager
            self.manager.start()
            self.sim = None
        else:
            self.sim = Simulation(self.config['home'])
        self.running = True
        self.paused_reason = ''
        self.sim_time = time.monotonic()
        self.session = self.db.conn.execute('INSERT INTO sessions(start,mode) VALUES(?,?)', (time.time(), mode)).lastrowid
        self.record_event('receiver', '', f'Monitoring started · {mode}')
        return ''

    def _close_open_sessions(self):
        """A session still open at startup was cut short (crash, power loss): end it at its last record,
        so monitoring time does not keep growing."""
        self.db.conn.execute('''
            WITH bounds AS (SELECT id, start, COALESCE((SELECT MIN(s2.start) FROM sessions s2
                                                        WHERE s2.start > s.start), 1e20) AS next
                            FROM sessions s WHERE end IS NULL)
            UPDATE sessions SET end = (SELECT MAX(b.start,
                    COALESCE((SELECT MAX(time) FROM events WHERE time >= b.start AND time < b.next), b.start),
                    COALESCE((SELECT MAX(time) FROM positions WHERE time >= b.start AND time < b.next), b.start))
                FROM bounds b WHERE b.id = sessions.id)
            WHERE end IS NULL''')
        self.db.conn.commit()

    def stop(self, reason=''):
        was_running = self.running
        self.running = False
        self.sim = None
        if self.manager:
            self.manager.stop()
        if self.session:
            self.db.conn.execute('UPDATE sessions SET end=? WHERE id=?', (time.time(), self.session))
            self.session = None
        self.paused_reason = reason
        if was_running:
            self.record_event('receiver', '', reason or 'Monitoring stopped')

    def pause(self, reason):
        """Stop after a storage or data error without relying on the database."""
        self.running = False
        self.sim = None
        if self.manager:
            self.manager.stop()
        self.paused_reason = reason
        session, self.session = self.session, None
        try:
            if session:
                self.db.conn.execute('UPDATE sessions SET end=? WHERE id=?', (time.time(), session))
            self.record_event('receiver', '', reason)
        except (sqlite3.Error, OSError):
            log.exception('Could not record the pause in the database')

    def shutdown(self):
        self.stop()
        if self.manager:
            self.manager.stop()
            self.manager.join(timeout=18)
        self.db.event('application', '', 'Application closed')
        self.db.close()

    def record_event(self, category, key, text, simulated=False):
        log.info('%s: %s', category, text)
        self.db.event(category, key, text, simulated)
        self.db.conn.commit()

    # ---------------------------------------------------------------- processing
    def tick(self, now=None):
        """Drain decoders/simulation once. Returns (alert notices, expired keys)."""
        now = time.time() if now is None else now
        messages, notices = [], []
        if self.running and self.sim and time.monotonic() - self.sim_time >= 1:
            delta = time.monotonic() - self.sim_time
            self.sim_time = time.monotonic()
            messages = self.sim.step(delta)
        if self.manager:
            for _ in range(100):
                try:
                    kind, text = self.manager.status.get_nowait()
                except queue.Empty:
                    break
                self.receiver_health[kind] = text
                self.record_event('receiver', kind, text)
            if self.running and not self.sim:
                for _ in range(5000):
                    try:
                        messages.append(self.manager.messages.get_nowait())
                    except queue.Empty:
                        break
        self.received += len(messages)
        # Coalesce each batch per target so the UI and database never process every RF packet.
        merged = {}
        for msg in messages:
            merged.setdefault((msg['kind'], msg['identifier']), {}).update(msg)
        home = self.config['home']
        needed = traits.conditions_in_use(self.config['rules'])
        for msg in merged.values():
            received = msg.pop('_received', now)
            if msg['kind'] == 'aircraft' and not msg.get('simulated'):
                msg.update(self.db.lookup(msg['identifier']))
            target, new = self.store.update(msg, received)
            if new:
                self.db.event('detection', target.key, f'{target.label} first detected', target.simulated, received)
            traits.update(target, self.db, needed, new, received)
            distance = distance_bearing(home, target.position)[0] if home and target.position else None
            self.db.record(target, distance, self.config['sample_seconds'], new)
            for alert in self.alerts.evaluate(target, self.config['rules'], home, self.config['geofences'], received):
                rule = alert['rule']
                detail = self.alert_detail(rule, target, alert.get('detail') or {})
                text = ('SIMULATION · ' if target.simulated else '') + alert['text'] + (f' · {detail}' if detail else '')
                self.db.event('alert', target.key, text, target.simulated, received, rule=rule['id'])
                log.info('Alert: %s', text)
                emergency = rule.get('condition') == 'squawk_emergency'
                notices.append(dict(text=text, key=target.key, rule=rule['name'], rule_id=rule['id'],
                                    snoozed=self.is_snoozed(rule['id'], target.key, time.time()),
                                    label=target.label,
                                    sound=bool(rule.get('sound')), desktop=bool(rule.get('desktop')),
                                    speak=bool(rule.get('speak')), phone=bool(rule.get('phone')),
                                    override_quiet=bool(rule.get('override_quiet')), urgent=emergency,
                                    speech=self.speech_text(rule, target, alert.get('detail') or {}),
                                    time=received, simulated=target.simulated))
        expired = self.store.expire(now, self.config['aircraft_ttl'], self.config['vessel_ttl'])
        self.alerts.expire(expired)
        self.alerts.prune(now)
        for key in expired:
            self.db.samples.pop(key, None)
            self.db.samples.pop('sim/' + key, None)
        if messages:
            self.db.conn.commit()
        if now - self.last_maintenance > 86400:
            self.db.maintenance(now - self.config['retention_days'] * 86400)
            self.last_maintenance = now
        return notices, expired

    def sample_rate(self):
        self.rate, self.received = self.received, 0
        return self.rate

    # -------------------------------------------------------------- alert text
    def _distance_words(self, km):
        units = self.config['units']
        value = km / UNIT_KM[units]
        return f'{value:.1f}'.rstrip('0').rstrip('.') if value < 10 else f'{value:.0f}'

    def alert_detail(self, rule, target, detail):
        """Short extra context appended to the alert text."""
        units = self.config['units']
        if rule.get('condition') == 'approach' and 'cpa_km' in detail:
            eta = detail.get('eta_s', 0)
            when = 'now' if eta < 30 else f'in {max(1, round(eta / 60))} min'
            if detail['cpa_km'] / UNIT_KM[units] < 0.1:
                return f'passes overhead {when}'
            return f"passes {self._distance_words(detail['cpa_km'])} {units} from home {when}"
        if rule.get('condition') == 'squawk_emergency' and detail.get('squawk'):
            return f"squawk {detail['squawk']} ({detail.get('emergency', 'emergency')})"
        return self.trait_detail(detail.get('trait'), target, detail)

    @staticmethod
    def trait_detail(trait, target, detail):
        """Why a rarity / military / circling alert fired, e.g. 'first Boeing 747-8 (B748) seen here'."""
        data = target.data
        code = str(data.get('type') or '')
        kind = ' '.join(str(v) for v in (data.get('manufacturer'), data.get('model')) if v) or code
        kind = f'{kind} ({code})' if code and code not in kind else kind
        if trait == 'first_type':
            return f'first {kind} seen here' if kind else 'first of its type seen here'
        if trait == 'rare_type':
            n = detail.get('type_count', 0)
            return f"{kind or 'type'} seen {n} time{'' if n == 1 else 's'} in {traits.RARE_DAYS} days"
        if trait == 'first_aircraft':
            return 'first time seen here'
        if trait == 'first_operator':
            from .insights import AIRLINES
            prefix = traits.airline_prefix(data.get('callsign'))
            return f'first {AIRLINES.get(prefix, prefix)} flight seen here'
        if trait == 'military':
            return 'military' + (f" · {data['operator']}" if data.get('operator') else '')
        if trait == 'circling':
            return 'circling'
        return ''

    def speech_text(self, rule, target, detail):
        """Sentence for spoken alerts."""
        name = target.label
        if target.kind == 'aircraft' and name and name[0] == 'N' and len(name) <= 6:
            name = ' '.join(name)  # read tail numbers letter by letter
        units = UNIT_WORDS[self.config['units']]
        if rule.get('condition') == 'squawk_emergency':
            code = detail.get('squawk') or squawk_code(target) or ''
            return f"Emergency alert. {name} is squawking {' '.join(code)}, {detail.get('emergency', 'emergency')}."
        if rule.get('condition') == 'approach' and 'cpa_km' in detail:
            eta = detail.get('eta_s', 0)
            when = 'now' if eta < 30 else f"in {max(1, round(eta / 60))} minute{'s' if round(eta / 60) > 1 else ''}"
            if detail['cpa_km'] / UNIT_KM[self.config['units']] < 0.1:
                return f'{name} will pass overhead {when}.'
            return f"{name} will pass {self._distance_words(detail['cpa_km'])} {units} from home {when}."
        if detail.get('trait'):
            return f"Alert. {name}: {self.trait_detail(detail['trait'], target, detail).replace(' · ', ', ')}."
        parts = [f'Alert. {name}']
        home = self.config['home']
        if home and target.position:
            d, b = distance_bearing(home, target.position)
            parts.append(f'{self._distance_words(d)} {units} {compass(b)}')
        if target.kind == 'aircraft' and target.data.get('altitude') is not None:
            parts.append(f"{int(round(target.data['altitude'], -2)):,} feet")
        return ', '.join(parts) + '.'

    # ------------------------------------------------------------------- targets
    def watched_ids(self):
        ids = {w.get('identifier', '').strip().casefold() for w in self.config['watchlist']}
        ids.discard('')
        return ids

    def is_watched(self, target, ids=None):
        ids = self.watched_ids() if ids is None else ids
        return bool(ids.intersection((target.identifier.casefold(), str(target.data.get('registration', '')).casefold())))

    def target_row(self, t, now=None, ids=None):
        now = time.time() if now is None else now
        home = self.config['home']
        units = self.config['units']
        d, b = distance_bearing(home, t.position) if home and t.position else (None, None)
        altitude, speed = t.data.get('altitude'), t.data.get('speed')
        heading = t.data.get('heading')
        if heading is None:
            heading = t.data.get('course')
        secondary = t.data.get('callsign') if t.data.get('callsign') and t.data.get('callsign') != t.label else None
        # Vessels report a numeric AIS ship type; show its name.
        kind_text = ship_type_name(t.data.get('type')) if t.kind == 'vessel' else t.data.get('type')
        detail = []
        for part in (t.identifier, t.data.get('callsign'), kind_text):
            part = str(part).strip() if part is not None else ''
            if part and part != t.label and part not in detail:
                detail.append(part)
        doing = phase.flight_phase(t, now, airports.get(), units)
        return dict(key=t.key, label=t.label, kind=t.kind, identifier=t.identifier,
                    detail=' · '.join(detail) or t.identifier, phase=doing['text'], phaseCode=doing['code'],
                    secondary=secondary or kind_text and str(kind_text) or t.identifier,
                    altitude=round(altitude) if altitude is not None else None,
                    speed=round(speed, 1) if speed is not None else None,
                    distance=round(d / UNIT_KM[units], 1) if d is not None else None,
                    bearing=round(b) if b is not None else None,
                    heading=heading, lastSeen=t.last_seen,
                    lastSeenText=datetime.fromtimestamp(t.last_seen).strftime('%H:%M:%S'),
                    stale=not t.position_time or now - t.position_time > STALE_SECONDS,
                    hasPosition=t.position is not None,
                    watched=self.is_watched(t, ids), simulated=t.simulated,
                    squawk=squawk_code(t) or '', emergency=emergency_kind(t))

    def details(self, t, now=None):
        """Ordered (label, value) pairs for the inspector; None means unavailable."""
        fields = [('Source', 'Simulation' if t.simulated else 'Local RF'), ('Identifier', t.identifier),
                  ('Category', t.kind.capitalize()), ('First seen', stamp(t.first_seen)), ('Last seen', stamp(t.last_seen)),
                  ('Position age', f'{max(0, (time.time() if now is None else now) - t.position_time):.0f} s'
                   if t.position_time else None)]
        if t.kind == 'aircraft':
            keys = ['registration', 'callsign', 'type', 'model', 'manufacturer', 'operator', 'lat', 'lon',
                    'altitude', 'speed', 'vertical_speed', 'heading', 'squawk', 'signal_strength']
        else:
            keys = ['name', 'imo', 'callsign', 'type', 'lat', 'lon', 'speed', 'course', 'heading',
                    'navigation_status', 'destination', 'eta_month', 'eta_day', 'eta_hour', 'eta_minute',
                    'to_bow', 'to_stern', 'to_port', 'to_starboard', 'draught']
        units = {'altitude': ' ft', 'speed': ' kn', 'vertical_speed': ' ft/min', 'heading': '°',
                 'course': '°', 'to_bow': ' m', 'to_stern': ' m', 'to_port': ' m', 'to_starboard': ' m',
                 'draught': ' m'}
        for k in keys:
            v = t.data.get(k)
            if isinstance(v, float):
                v = round(v, 5 if k in ('lat', 'lon') else 1)
            if t.kind == 'vessel' and v is not None and k in ('type', 'navigation_status'):
                name = ship_type_name(v) if k == 'type' else nav_status_name(v)
                code = _code(v)
                v = f'{name} ({code})' if name and code is not None else name or v
            fields.append((k.replace('_', ' ').title(), f'{v}{units.get(k, "")}' if v is not None else None))
        home = self.config['home']
        if home and t.position:
            d, b = distance_bearing(home, t.position)
            u = self.config['units']
            fields += [('Distance', f'{d / UNIT_KM[u]:.2f} {u}'), ('Bearing', f'{b:.0f}°')]
        if t.kind == 'aircraft':
            clock = time.time() if now is None else now
            u = self.config['units']
            fields += [('Flight phase', phase.flight_phase(t, clock, airports.get(), u)['text'] or None),
                       ('Nearest airport', phase.describe_airport(t.position, airports.get(), u) or None)]
            if t.traits.get('military'):
                fields.append(('Military', 'Yes'))
        return fields

    # --------------------------------------------------------------------- rules
    def rule_rows(self, now=None):
        units = self.config['units']
        now = time.time() if now is None else now
        activity = self.db.rule_activity(now)
        snoozes = self.config['snooze'].get('rules', {})
        rows = []
        for i, r in enumerate(self.config['rules']):
            c = r.get('condition', 'enter')
            if c == 'approach':
                threshold = f"{round(r.get("threshold", 0) / UNIT_KM[units], 2):g} {units} \u00b7 {r.get('lookahead', 5):g} min"
            elif c in DISTANCE_CONDITIONS:
                threshold = f"{round(r.get("threshold", 0) / UNIT_KM[units], 2):g} {units}"
            elif c == 'squawk_emergency':
                threshold = '7500 \u00b7 7600 \u00b7 7700'
            elif c == 'altitude_below':
                threshold = f"{r.get('threshold', 0):g} ft"
            elif c == 'speed_above':
                threshold = f"{r.get('threshold', 0):g} kn"
            elif c in ZONE_CONDITIONS:
                threshold = r.get('zone') or '—'
            elif c == 'rare_type':
                threshold = f"fewer than {max(1, r.get('threshold', 3)):g} in {traits.RARE_DAYS} days"
            else:
                threshold = '—'
            fired = activity.get(r['id'], {})
            until = snoozes.get(r['id'], 0)
            zones = {z['name'] for z in self.config['geofences']}
            zone_missing = (c in ZONE_CONDITIONS and r.get('zone') not in zones) or \
                any(e.get('condition') in ZONE_EXTRAS and e.get('zone') not in zones for e in r.get('extra') or [])
            rows.append(dict(key=r['id'], row=i, name=r['name'], enabled=r.get('enabled', True), kind=r['kind'],
                             match=f"{r.get('field', 'identifier')} = {r.get('value') or '*'}", condition=c,
                             threshold=threshold, cooldown=r.get('cooldown', 60), sound=bool(r.get('sound')),
                             desktop=bool(r.get('desktop')), speak=bool(r.get('speak')), phone=bool(r.get('phone')),
                             overrideQuiet=bool(r.get('override_quiet')), extras=len(r.get('extra') or []),
                             sentence=rule_sentence(r, units), inactive=zone_missing,
                             firedTotal=fired.get('total', 0), firedDay=fired.get('day', 0),
                             firedWeek=fired.get('week', 0), daily=fired.get('daily', [0] * 14),
                             lastFired=self._when(fired['last'], now) if fired.get('last') else '',
                             snoozedUntil=self._when(until, now) if until > now else ''))
        return rows

    @staticmethod
    def _when(ts, now):
        """'14:03' today, otherwise 'Oct 3 14:03'."""
        moment = datetime.fromtimestamp(ts)
        same_day = moment.date() == datetime.fromtimestamp(now).date()
        return moment.strftime('%H:%M') if same_day else f"{moment.strftime('%b')} {moment.day} {moment.strftime('%H:%M')}"

    # -------------------------------------------------------------------- snooze
    def is_snoozed(self, rule_id, target_key, now=None):
        """True while everything, this rule or this target is snoozed. The alert is still recorded."""
        now = time.time() if now is None else now
        snooze = self.config['snooze']
        return max(snooze.get('all', 0), snooze.get('rules', {}).get(rule_id, 0),
                   snooze.get('targets', {}).get(target_key, 0)) > now

    def snooze(self, scope, key, until):
        """scope: 'all', 'rule' (key = rule id) or 'target' (key = target key). until <= now wakes it."""
        current = self.config['snooze']
        snooze = dict(all=current.get('all', 0), rules=dict(current.get('rules', {})),
                      targets=dict(current.get('targets', {})))
        now = time.time()
        if scope == 'all':
            snooze['all'] = until if until > now else 0
        elif scope in ('rule', 'target') and key:
            group = snooze['rules' if scope == 'rule' else 'targets']
            if until > now:
                group[key] = until
            else:
                group.pop(key, None)
        else:
            return 'Nothing to snooze.'
        return self._save_values(snooze=self._live_snoozes(snooze, now))

    @staticmethod
    def _live_snoozes(snooze, now):
        return dict(all=snooze['all'] if snooze.get('all', 0) > now else 0,
                    rules={k: v for k, v in snooze.get('rules', {}).items() if v > now},
                    targets={k: v for k, v in snooze.get('targets', {}).items() if v > now})

    def prune_snoozes(self, now=None):
        """Forget snoozes that have run out. Returns True when something changed."""
        now = time.time() if now is None else now
        live = self._live_snoozes(self.config['snooze'], now)
        if live == self.config['snooze']:
            return False
        self._save_values(snooze=live)
        return True

    def snooze_summary(self, now=None):
        """dict(all=text, rules=count, targets=[...], active=bool) for the interface."""
        now = time.time() if now is None else now
        live = self._live_snoozes(self.config['snooze'], now)
        return dict(all=self._when(live['all'], now) if live['all'] else '', rules=len(live['rules']),
                    targets=[dict(key=k, label=k.split(':', 1)[-1], until=self._when(v, now))
                             for k, v in sorted(live['targets'].items())],
                    active=bool(live['all'] or live['rules'] or live['targets']))

    def rule_form(self, rule=None):
        """Rule values for the editor with distance thresholds in display units."""
        r = dict(rule or {})
        units = self.config['units']
        condition = r.get('condition', 'enter')
        threshold = r.get('threshold', 25 * UNIT_KM[units])
        if condition in DISTANCE_CONDITIONS:
            threshold = threshold / UNIT_KM[units]
        extra = []
        for e in r.get('extra') or []:
            value = e.get('threshold', 0)
            extra.append(dict(condition=e.get('condition', 'altitude_below'), zone=e.get('zone', ''),
                              threshold=round(value / UNIT_KM[units], 4) if e.get('condition') in DISTANCE_EXTRAS
                              else value))
        return dict(id=r.get('id', ''), name=r.get('name', 'Aircraft proximity'), enabled=r.get('enabled', True),
                    kind=r.get('kind', 'aircraft'), field=r.get('field', 'registration'), value=r.get('value', ''),
                    condition=condition, threshold=round(threshold, 4), zone=r.get('zone', ''),
                    cooldown=r.get('cooldown', 60), sound=r.get('sound', True), desktop=r.get('desktop', True),
                    speak=r.get('speak', False), phone=r.get('phone', False),
                    override_quiet=r.get('override_quiet', False), lookahead=r.get('lookahead', 5), extra=extra)

    def save_rule(self, index, form):
        """Create (index < 0) or replace a rule. Returns '' or an error."""
        name = str(form.get('name', '')).strip()
        condition = form.get('condition', 'enter')
        if not name:
            return 'Give the alert a name.'
        if condition not in CONDITIONS or form.get('kind') not in KINDS or form.get('field') not in FIELDS:
            return 'Choose a valid target type, match field and condition.'
        zone = str(form.get('zone') or '')
        if condition in ZONE_CONDITIONS and not zone:
            return 'Draw and save a geofence on the map first, then choose it here.'
        try:
            threshold = float(form.get('threshold', 0))
            cooldown = float(form.get('cooldown', 60))
        except (TypeError, ValueError):
            return 'Threshold and cooldown must be numbers.'
        if threshold < 0 or not 0 <= cooldown <= 86400:
            return 'Threshold must be positive and cooldown between 0 and 86,400 seconds.'
        try:
            lookahead = float(form.get('lookahead', 5) or 5)
        except (TypeError, ValueError):
            return 'Look-ahead must be a number of minutes.'
        if not 1 <= lookahead <= 60:
            return 'Look-ahead must be between 1 and 60 minutes.'
        units = self.config['units']
        extra = []
        for e in form.get('extra') or []:
            c = e.get('condition')
            if c not in EXTRA_CONDITIONS:
                return 'Choose a valid additional condition.'
            if c in ZONE_EXTRAS:
                if not e.get('zone'):
                    return 'Choose a geofence for each inside/outside geofence condition.'
                extra.append(dict(condition=c, zone=str(e['zone']), threshold=0))
                continue
            try:
                value = float(e.get('threshold', 0))
            except (TypeError, ValueError):
                return 'Additional condition values must be numbers.'
            if value < 0:
                return 'Additional condition values must be positive.'
            extra.append(dict(condition=c, zone='', threshold=value * (UNIT_KM[units] if c in DISTANCE_EXTRAS else 1)))
        if len(extra) > 6:
            return 'Use at most six additional conditions.'
        rule = dict(id=form.get('id') or str(uuid.uuid4()), name=name, enabled=bool(form.get('enabled', True)),
                    kind=form['kind'], field=form['field'], value=str(form.get('value', '')).strip(),
                    condition=condition,
                    threshold=threshold * (UNIT_KM[units] if condition in DISTANCE_CONDITIONS else 1),
                    zone=zone, cooldown=int(cooldown) if cooldown.is_integer() else cooldown,
                    sound=bool(form.get('sound', True)), desktop=bool(form.get('desktop', True)),
                    speak=bool(form.get('speak', False)), phone=bool(form.get('phone', False)),
                    override_quiet=bool(form.get('override_quiet', False)),
                    lookahead=int(lookahead) if lookahead.is_integer() else lookahead, extra=extra)
        rules = list(self.config['rules'])
        if 0 <= index < len(rules):
            rules[index] = rule
        else:
            rules.append(rule)
        error = self._save_values(rules=rules)
        if not error:
            self.alerts = AlertEngine()
        return error

    def delete_rule(self, index):
        rules = list(self.config['rules'])
        if 0 <= index < len(rules):
            del rules[index]
            return self._save_values(rules=rules)
        return ''

    def set_rule_enabled(self, index, enabled):
        rules = [dict(r) for r in self.config['rules']]
        if 0 <= index < len(rules):
            rules[index]['enabled'] = bool(enabled)
            error = self._save_values(rules=rules)
            if not error:
                self.alerts = AlertEngine()
            return error
        return ''

    def rule_for_target(self, t):
        field = 'mmsi' if t.kind == 'vessel' else ('registration' if t.data.get('registration') else 'icao')
        value = t.identifier if field != 'registration' else t.data['registration']
        return self.rule_form(dict(name=f'{t.label} nearby', kind=t.kind, field=field, value=value))

    def rule_for_watch(self, index):
        w = self.config['watchlist'][index]
        field = 'mmsi' if w['kind'] == 'vessel' else ('icao' if is_icao(w['identifier']) else 'registration')
        return self.rule_form(dict(name=w.get('name') or w['identifier'], kind=w['kind'], field=field,
                                   value=w['identifier']))

    def emergency_rule_form(self):
        return self.rule_form(dict(name='Emergency squawk', kind='aircraft', field='registration', value='',
                                   condition='squawk_emergency', cooldown=300, sound=True, desktop=True,
                                   speak=True, phone=True, override_quiet=True))

    def has_emergency_rule(self):
        return any(r.get('condition') == 'squawk_emergency' for r in self.config['rules'])

    # ----------------------------------------------------------------- watchlist
    def watch_rows(self):
        rows = []
        targets = list(self.store.targets.values())
        for i, w in enumerate(self.config['watchlist']):
            ident = w['identifier'].casefold()
            last, encounters = self.db.sightings_of(w['kind'], w['identifier'])
            live = any(self.is_watched(t, {ident}) for t in targets)
            rows.append(dict(key=f'{i}:{w["identifier"]}', row=i, name=w.get('name', ''),
                             identifier=w['identifier'], kind=w['kind'], notes=w.get('notes', ''),
                             lastSeen=last,
                             lastSeenText='Live now' if live else (stamp(last) if last else 'Never'),
                             sightings=encounters, live=live))
        return rows

    def save_watch(self, index, entry):
        identifier = str(entry.get('identifier', '')).strip()
        if not identifier:
            return 'Enter an ICAO address, registration or MMSI.'
        kind = entry.get('kind', 'aircraft')
        if kind not in ('aircraft', 'vessel'):
            return 'Choose aircraft or vessel.'
        item = dict(kind=kind, name=str(entry.get('name', '')).strip(), identifier=identifier,
                    notes=str(entry.get('notes', '')))
        items = list(self.config['watchlist'])
        if 0 <= index < len(items):
            items[index] = item
        else:
            items.append(item)
        return self._save_values(watchlist=items)

    def delete_watch(self, index):
        items = list(self.config['watchlist'])
        if 0 <= index < len(items):
            del items[index]
            return self._save_values(watchlist=items)
        return ''

    def import_aircraft(self, path):
        return self.db.import_aircraft(path)

    # ----------------------------------------------------------------- geofences
    def zone(self, name):
        return next((z for z in self.config['geofences'] if z['name'] == name), None)

    @staticmethod
    def _uses_zone(rule, name):
        """True when the rule's condition or one of its extra conditions names this geofence."""
        return (rule.get('condition') in ZONE_CONDITIONS and rule.get('zone') == name) or \
            any(e.get('condition') in ZONE_EXTRAS and e.get('zone') == name for e in rule.get('extra') or [])

    def linked_rules(self, name):
        return sum(self._uses_zone(r, name) for r in self.config['rules'])

    @staticmethod
    def zone_center(zone):
        # Average longitude relative to the first vertex so date-line zones stay local.
        points = zone['points']
        anchor = points[0][1]
        lon = anchor + sum((p[1] - anchor + 180) % 360 - 180 for p in points) / len(points)
        return sum(p[0] for p in points) / len(points), (lon + 180) % 360 - 180

    def _check_zone_name(self, name, original=None):
        if not name:
            return 'Enter a name for this geofence.'
        if any(z['name'].casefold() == name.casefold() and z['name'] != original for z in self.config['geofences']):
            return 'That name is already in use. Choose a different name.'
        return ''

    def create_zone(self, name, points):
        name = name.strip()
        error = self._check_zone_name(name)
        if error:
            return error
        if len(points) < 3:
            return 'A geofence needs at least three points.'
        zones = list(self.config['geofences']) + [dict(name=name, points=[[float(a), float(b)] for a, b in points])]
        return self._save_values(geofences=zones)

    def rename_zone(self, original, name):
        name = name.strip()
        error = self._check_zone_name(name, original)
        if error:
            return error
        zones = [dict(z, name=name) if z['name'] == original else z for z in self.config['geofences']]
        # Rules (and their extra inside/outside conditions) follow the zone; alert occupancy and cooldowns are kept.
        rules = []
        for r in self.config['rules']:
            if r.get('zone') == original:
                r = dict(r, zone=name)
            if any(e.get('zone') == original for e in r.get('extra') or []):
                r = dict(r, extra=[dict(e, zone=name) if e.get('zone') == original else e for e in r['extra']])
            rules.append(r)
        return self._save_values(geofences=zones, rules=rules)

    def remove_zone(self, original):
        zones = [z for z in self.config['geofences'] if z['name'] != original]
        error = self._save_values(geofences=zones)
        if not error:
            affected = {r['id'] for r in self.config['rules'] if self._uses_zone(r, original)}
            self.alerts.states = {k: v for k, v in self.alerts.states.items() if k[0] not in affected}
        return error

    # ------------------------------------------------------------------- history
    def search_history(self, category='sightings', query='', kind='any', start=0, end=1e20, max_distance=None):
        if start > end:
            raise ValueError('The start time must be before the end time.')
        if category == 'sightings':
            distance = max_distance * UNIT_KM[self.config['units']] if max_distance is not None else None
            return self.db.history(query, kind, start, end, distance)
        return self.db.events(query, category, start, end)

    def track(self, key, start=0, end=1e20):
        return self.db.track(key, start, end)

    @staticmethod
    def history_target(row):
        return Target(row['kind'], row['identifier'], row['first'], row['last'], json.loads(row['data']),
                      simulated=bool(row['simulated']))

    @staticmethod
    def export(path, rows):
        export_rows(path, rows)

    def maintenance_info(self):
        oldest = self.db.conn.execute('SELECT MIN(time) FROM positions').fetchone()[0]
        size = sum(p.stat().st_size for p in self.config.folder.glob('history.sqlite*')) / 1024 ** 2
        counts = {name: self.db.conn.execute(f'SELECT COUNT(*) FROM {name}').fetchone()[0]
                  for name in ('sightings', 'positions', 'events')}
        return dict(sizeMb=round(size, 2), oldest=stamp(oldest) if oldest else 'No positions recorded',
                    retention=self.config['retention_days'], **counts)

    def delete_older_than(self, days):
        self.db.maintenance(time.time() - days * 86400, vacuum=True)  # asked for: give the space back now
        self._distance_cache = None

    # ---------------------------------------------------------------- statistics
    DISTANCE_CACHE_SECONDS = 60

    def _max_distances(self, now, present):
        """Farthest recorded position per (kind, simulated) that has sightings. It scans that kind's stored
        positions, so it is cached."""
        cached = self._distance_cache
        if cached is not None and now - cached[0] < self.DISTANCE_CACHE_SECONDS and cached[2] == present:
            return cached[1]
        result = {}
        for simulated, prefix in ((0, ''), (1, 'sim/')):
            for kind in ('aircraft', 'vessel'):
                if (kind, simulated) not in present:
                    continue
                # A literal prefix lets SQLite walk just this kind's slice of the (key, time) index.
                result[kind, simulated] = self.db.conn.execute(
                    f"SELECT MAX(distance) FROM positions WHERE simulated=? AND key GLOB '{prefix}{kind}:*'",
                    (simulated,)).fetchone()[0]
        self._distance_cache = (now, result, present)
        return result

    def statistics(self):
        units = self.config['units']
        conn, now = self.db.conn, time.time()
        seen = {(kind, simulated): (count, last) for kind, simulated, count, last in conn.execute(
            'SELECT kind, simulated, COUNT(*), MAX(last) FROM sightings GROUP BY kind, simulated')}
        alerts = {(kind, simulated): count for kind, simulated, count in conn.execute(
            "SELECT substr(key, 1, instr(key, ':') - 1), simulated, COUNT(*) FROM events "
            "WHERE category='alert' GROUP BY 1, 2")}
        farthest = self._max_distances(now, frozenset(seen))
        rows = []
        for simulated, source in ((0, 'Local RF'), (1, 'Simulation')):
            for kind in ('aircraft', 'vessel'):
                count, last = seen.get((kind, simulated), (0, None))
                maxd = farthest.get((kind, simulated))
                rows.append(dict(source=source, kind=kind, unique=count,
                                 maxDistance=f'{maxd / UNIT_KM[units]:.1f} {units}' if maxd is not None else 'Unknown',
                                 last=stamp(last) if last else 'Never', alerts=alerts.get((kind, simulated), 0)))
        uptime = conn.execute('SELECT SUM(COALESCE(end,?)-start) FROM sessions', (now,)).fetchone()[0] or 0
        sessions = conn.execute('SELECT COUNT(*) FROM sessions').fetchone()[0]
        return dict(rows=rows, hours=round(uptime / 3600, 2), sessions=sessions)

    # ------------------------------------------------------------------ settings
    def _save_values(self, **changes):
        previous = self.config.values
        self.config.values = dict(previous, **changes)
        try:
            self.config.save()
        except (OSError, ValueError) as e:
            self.config.values = previous
            log.exception('Could not save settings')
            return f'Could not save: {e}'
        return ''

    def save_settings(self, values):
        """Validate and persist the settings dialog. Returns '' or an error."""
        merged = dict(self.config.values)
        merged.update(values)
        try:
            if merged['gain'] != 'auto' and not 0 <= float(merged['gain']) <= 50:
                raise ValueError('Gain must be "auto" or between 0 and 50 dB.')
            rings = merged.get('rings', [])
            if len(rings) > 12 or any(not 0 < r <= 2000 for r in rings):
                raise ValueError('Use up to 12 range rings between 0 and 2000.')
            if merged['mode'] == 'Dual receivers' and merged['aircraft_device'] == merged['marine_device']:
                raise ValueError('Dual receivers needs two different receivers.')
            merged['setup_done'] = True
            self.config.validate(merged)
        except (ValueError, TypeError) as e:
            return str(e)
        error = self._save_values(**merged)
        if not error and not self.running:
            self.mode = merged['mode']
        return error
