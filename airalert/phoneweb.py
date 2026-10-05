"""A small read-only web server that shows the live map on a phone or tablet on the same network.

Qt-free. The interface thread hands over a ready-made JSON snapshot once a second (``set_state``); request
threads only ever read that snapshot, so they never touch the engine.

Safety:
* only clients on private (home network) addresses are answered;
* a device must be paired once with the 6-digit code shown in Settings; it then holds a long random token in a
  cookie, and only a hash of that token is kept on this computer;
* wrong codes are rate limited and the code changes after every successful pairing;
* nothing can be changed through it: there are no control endpoints.
"""
import hashlib
import hmac
import ipaddress
import json
import logging
import secrets
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

log = logging.getLogger(__name__)

COOKIE = 'airalert'
MAX_FAILURES = 5
LOCK_SECONDS = 300
MAX_SESSIONS = 20


def token_hash(token):
    return hashlib.sha256(token.encode('utf-8')).hexdigest()


def is_private(address):
    """True for loopback, link-local and home-network addresses (IPv4 or IPv6)."""
    try:
        ip = ipaddress.ip_address(address.split('%')[0])
    except ValueError:
        return False
    if ip.version == 6 and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return ip.is_loopback or ip.is_private or ip.is_link_local


def local_address():
    """This computer's address on the home network (no packet is sent), or '' when offline."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(('10.255.255.255', 1))
        address = probe.getsockname()[0]
    except OSError:
        return ''
    finally:
        probe.close()
    return address if is_private(address) and not address.startswith('127.') else ''


class PhoneServer:
    def __init__(self, page, sessions=(), on_paired=None):
        self.page = page if isinstance(page, bytes) else page.encode('utf-8')
        self.sessions = list(sessions)[-MAX_SESSIONS:]      # sha256 hashes of paired devices' tokens
        self.on_paired = on_paired or (lambda sessions: None)
        self.code = self._new_code()
        self.port = 0
        self._state = b'{}'
        self._lock = threading.Lock()
        self._failures = 0
        self._locked_until = 0.0
        self._server = None
        self._thread = None

    # ------------------------------------------------------------------ control
    @staticmethod
    def _new_code():
        return f'{secrets.randbelow(1_000_000):06d}'

    @property
    def running(self):
        return self._server is not None

    def start(self, port, host='0.0.0.0'):
        """Listen on port (0 = any free port). Returns the port in use. Raises OSError when it is taken."""
        self.stop()
        owner = self

        class Handler(BaseHTTPRequestHandler):
            server_version = 'AirAlert'
            sys_version = ''
            protocol_version = 'HTTP/1.1'

            def log_message(self, fmt, *args):   # keep request logs out of stderr
                pass

            def do_GET(self):
                owner._handle(self, 'GET')

            def do_POST(self):
                owner._handle(self, 'POST')

        server = ThreadingHTTPServer((host, int(port)), Handler)
        server.daemon_threads = True
        self._server = server
        self.port = server.server_address[1]
        self._thread = threading.Thread(target=server.serve_forever, name='phone-map', daemon=True)
        self._thread.start()
        log.info('Phone map listening on port %s', self.port)
        return self.port

    def stop(self):
        server, self._server = self._server, None
        if server is not None:
            server.shutdown()
            server.server_close()
            if self._thread is not None:
                self._thread.join(timeout=5)
            self._thread = None
            log.info('Phone map stopped')

    def set_state(self, payload):
        """payload: the JSON bytes served to paired devices."""
        with self._lock:
            self._state = payload

    def forget_devices(self):
        with self._lock:
            self.sessions = []
            self.code = self._new_code()

    # ------------------------------------------------------------------ requests
    def _send(self, handler, status, body=b'', kind='application/json', headers=()):
        handler.send_response(status)
        handler.send_header('Content-Type', kind)
        handler.send_header('Content-Length', str(len(body)))
        handler.send_header('Cache-Control', 'no-store')
        handler.send_header('X-Content-Type-Options', 'nosniff')
        handler.send_header('Referrer-Policy', 'no-referrer')
        for name, value in headers:
            handler.send_header(name, value)
        handler.end_headers()
        handler.wfile.write(body)

    def _paired(self, handler):
        for part in (handler.headers.get('Cookie') or '').split(';'):
            name, _, value = part.strip().partition('=')
            if name == COOKIE and value:
                digest = token_hash(value)
                with self._lock:
                    return any(hmac.compare_digest(digest, known) for known in self.sessions)
        return False

    def _handle(self, handler, method):
        try:
            if not is_private(handler.client_address[0]):
                return self._send(handler, 403, b'{"error":"local network only"}')
            path = handler.path.split('?', 1)[0]
            if method == 'GET' and path in ('/', '/index.html'):
                return self._send(handler, 200, self.page, 'text/html; charset=utf-8')
            if method == 'GET' and path == '/api/state':
                if not self._paired(handler):
                    return self._send(handler, 401, b'{"error":"pair this device first"}')
                with self._lock:
                    body = self._state
                return self._send(handler, 200, body)
            if method == 'POST' and path == '/api/pair':
                return self._pair(handler)
            return self._send(handler, 404, b'{"error":"not found"}')
        except (OSError, ValueError):
            pass   # the phone went away mid-request

    def _pair(self, handler):
        now = time.monotonic()
        with self._lock:
            if now < self._locked_until:
                wait = int(self._locked_until - now) + 1
                locked = True
            else:
                locked = False
        if locked:
            return self._send(handler, 429, json.dumps(dict(error='Too many wrong codes. Try again later.',
                                                            wait=wait)).encode())
        length = min(int(handler.headers.get('Content-Length') or 0), 200)
        try:
            code = str(json.loads(handler.rfile.read(length) or b'{}').get('code', '')).strip()
        except (ValueError, AttributeError):
            code = ''
        with self._lock:
            ok = len(code) == 6 and hmac.compare_digest(code, self.code)
            if ok:
                token = secrets.token_urlsafe(32)
                self.sessions = (self.sessions + [token_hash(token)])[-MAX_SESSIONS:]
                self.code = self._new_code()          # a code pairs one device
                self._failures = 0
                sessions = list(self.sessions)
            else:
                self._failures += 1
                if self._failures >= MAX_FAILURES:
                    self._failures = 0
                    self._locked_until = now + LOCK_SECONDS
                    self.code = self._new_code()
        if not ok:
            return self._send(handler, 403, b'{"error":"That code is not right."}')
        self._send(handler, 200, b'{"ok":true}', headers=[(
            'Set-Cookie', f'{COOKIE}={token}; Max-Age=31536000; Path=/; HttpOnly; SameSite=Strict')])
        try:
            self.on_paired(sessions)
        except Exception:
            log.exception('Could not save the paired device')
