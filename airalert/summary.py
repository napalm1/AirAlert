"""The daily summary: one message with what the station received today (received traffic only)."""
import json
from datetime import datetime

from .core.models import UNIT_KM

STATE_KEY = 'summary_date'


def day_bounds(now):
    start = datetime.fromtimestamp(now).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    return start, now


def day_summary(conn, units, now):
    """dict(text, aircraft, vessels, alerts, hours, farthest) for the local day so far."""
    start, end = day_bounds(now)
    counts = dict(conn.execute('''SELECT kind, COUNT(*) FROM sightings WHERE simulated=0 AND last>=? AND first<=?
                                  GROUP BY kind''', (start, end)).fetchall())
    aircraft, vessels = counts.get('aircraft', 0), counts.get('vessel', 0)
    alerts = conn.execute("SELECT COUNT(*) FROM events WHERE category='alert' AND simulated=0 AND time BETWEEN ? AND ?",
                          (start, end)).fetchone()[0]
    minutes = conn.execute('SELECT COUNT(*) FROM minute_stats WHERE simulated=0 AND minute>=? AND minute<=?',
                           (int(start // 60), int(end // 60))).fetchone()[0]
    far = conn.execute('''SELECT key, distance FROM positions WHERE simulated=0 AND time BETWEEN ? AND ?
                          AND distance IS NOT NULL ORDER BY distance DESC LIMIT 1''', (start, end)).fetchone()
    farthest = None
    if far:
        row = conn.execute('SELECT identifier, data FROM sightings WHERE key=?', (far[0],)).fetchone()
        label = far[0].split(':', 1)[-1]
        if row:
            try:
                data = json.loads(row[1] or '{}')
            except ValueError:
                data = {}
            label = data.get('registration') or data.get('name') or data.get('callsign') or row[0]
        farthest = dict(label=label, distance=round(far[1] / UNIT_KM[units], 1))

    def plural(n, word):
        return f'{n:,} {word}' if n == 1 or word == 'aircraft' else f'{n:,} {word}s'
    hours = round(minutes / 60, 1)
    if not aircraft and not vessels:
        text = (f'Today: nothing was received in {hours:g} h of monitoring.' if minutes
                else 'Today: AirAlert was not monitoring.')
    else:
        parts = [' and '.join(p for p in (plural(aircraft, 'aircraft') if aircraft else '',
                                          plural(vessels, 'vessel') if vessels else '') if p)]
        if farthest:
            parts.append(f"farthest {farthest['label']} at {farthest['distance']:g} {units}")
        parts.append(plural(alerts, 'alert'))
        if minutes:
            parts.append(f'monitored {hours:g} h')
        text = 'Today: ' + ' · '.join(parts)
    return dict(text=text, aircraft=aircraft, vessels=vessels, alerts=alerts, hours=hours, farthest=farthest,
                monitored=bool(minutes))


def due(conn, settings, now):
    """True when today's summary should go out now: enabled, past its time and not sent yet today."""
    if not settings.get('enabled'):
        return False
    moment = datetime.fromtimestamp(now)
    hours, minutes = str(settings.get('time', '21:00')).split(':')
    if (moment.hour, moment.minute) < (int(hours), int(minutes)):
        return False
    sent = conn.execute('SELECT value FROM insights_state WHERE key=?', (STATE_KEY,)).fetchone()
    return not sent or sent[0] != moment.date().isoformat()


def mark_sent(conn, now):
    with conn:
        conn.execute('INSERT OR REPLACE INTO insights_state(key, value) VALUES (?, ?)',
                     (STATE_KEY, datetime.fromtimestamp(now).date().isoformat()))
