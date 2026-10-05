"""Update check: compare this version with a small ``latest.json`` published next to the installer.

    {"version": "1.1.0", "url": "https://example.com/AirAlert-Setup-1.1.0.exe", "sha256": "...", "notes": "..."}

``airalert.UPDATE_URL`` names that file (empty = update checks are not set up). AirAlert never downloads or
runs anything by itself: it only tells you a newer version exists and can open the download page.
"""
import json
import re


def parse_version(text):
    """'1.2.10' -> (1, 2, 10). Unknown text sorts lowest."""
    numbers = re.findall(r'\d+', str(text))
    return tuple(int(n) for n in numbers[:4]) if numbers else (0,)


def is_newer(candidate, current):
    a, b = parse_version(candidate), parse_version(current)
    width = max(len(a), len(b))
    return a + (0,) * (width - len(a)) > b + (0,) * (width - len(b))


def parse_feed(payload, current):
    """dict(version, url, sha256, notes, available) from the feed bytes. Raises ValueError when it is not valid."""
    try:
        data = json.loads(payload.decode('utf-8') if isinstance(payload, (bytes, bytearray)) else payload)
    except (ValueError, UnicodeDecodeError):
        raise ValueError('The update information could not be read.')
    if not isinstance(data, dict) or not isinstance(data.get('version'), str) or not parse_version(data['version'])[0:1]:
        raise ValueError('The update information has no version.')
    url = str(data.get('url') or '')
    if url and not url.lower().startswith('https://'):
        url = ''   # only ever offer a secure download link
    return dict(version=data['version'].strip(), url=url, sha256=str(data.get('sha256') or '').lower(),
                notes=str(data.get('notes') or '')[:400], available=is_newer(data['version'], current))
