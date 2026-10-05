"""Alert delivery: sound, desktop pop-up, spoken voice and phone push (ntfy / Pushover).

Quiet hours silence sound, voice, desktop pop-ups and phone pushes unless the
rule overrides them; the in-app alert and History event are always recorded.
"""
import json
import logging
from datetime import datetime

from PySide6.QtCore import QByteArray, QObject, QProcess, QProcessEnvironment, QUrl, QUrlQuery, Signal
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest

try:
    import winsound
except ImportError:  # pragma: no cover - non-Windows development
    winsound = None

log = logging.getLogger(__name__)

PUSHOVER_URL = 'https://api.pushover.net/1/messages.json'
# The spoken text travels in an environment variable, never inside the command line.
SPEECH_SCRIPT = ("Add-Type -AssemblyName System.Speech; "
                 "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
                 "$s.Rate = 0; $s.Speak($env:AIRALERT_SAY)")


def _minutes(clock):
    hours, minutes = str(clock).split(':')
    return int(hours) * 60 + int(minutes)


def in_quiet_hours(quiet, when=None):
    """True when `when` (datetime) falls inside the quiet window; handles overnight windows."""
    if not quiet or not quiet.get('enabled'):
        return False
    when = when or datetime.now()
    start, end, now = _minutes(quiet.get('start', '22:00')), _minutes(quiet.get('end', '07:00')), \
        when.hour * 60 + when.minute
    if start == end:
        return True
    return start <= now < end if start < end else now >= start or now < end


class Notifier(QObject):
    """Delivers alert notices. `tray` may be None (no desktop pop-ups)."""
    phoneResult = Signal(str, bool, str)   # service, ok, message
    spoken = Signal(str)

    def __init__(self, config, tray=None, parent=None):
        super().__init__(parent)
        self.config = config
        self.tray = tray
        self.network = QNetworkAccessManager(self)
        self.speech_program = 'powershell.exe'
        self.speech_args = ['-NoProfile', '-NonInteractive', '-Command', SPEECH_SCRIPT]
        self._speech_queue = []
        self._speaker = None
        self.sent = []  # (service, payload) log for tests

    # ------------------------------------------------------------------ policy
    def quiet_now(self, when=None):
        return in_quiet_hours(self.config['quiet_hours'], when)

    def deliver(self, notice, when=None):
        """Apply quiet hours and the rule's delivery choices. Returns the channels used."""
        loud = not self.quiet_now(when) or notice.get('override_quiet')
        used = []
        if not loud:
            return used
        if notice.get('sound'):
            self.beep(notice.get('urgent'))
            used.append('sound')
        if notice.get('desktop') and self.tray is not None:
            from PySide6.QtWidgets import QSystemTrayIcon
            self.tray.showMessage('AirAlert', notice['text'], QSystemTrayIcon.MessageIcon.Warning, 8000)
            used.append('desktop')
        if notice.get('speak') and notice.get('speech'):
            self.speak(notice['speech'])
            used.append('voice')
        if notice.get('phone'):
            if self.send_phone('AirAlert' + (' — EMERGENCY' if notice.get('urgent') else ''), notice['text'],
                               urgent=bool(notice.get('urgent'))):
                used.append('phone')
        return used

    # ------------------------------------------------------------------- sound
    @staticmethod
    def beep(urgent=False):
        if winsound is None:
            return
        try:
            winsound.MessageBeep(winsound.MB_ICONHAND if urgent else winsound.MB_ICONEXCLAMATION)
        except RuntimeError:
            pass

    # ------------------------------------------------------------------- voice
    def speak(self, text):
        text = ' '.join(str(text).split())[:300]
        if not text:
            return
        if len(self._speech_queue) >= 3:
            self._speech_queue.pop(0)  # never fall far behind a burst of alerts
        self._speech_queue.append(text)
        self._next_speech()

    def _next_speech(self):
        if self._speaker is not None or not self._speech_queue:
            return
        text = self._speech_queue.pop(0)
        process = QProcess(self)
        environment = QProcessEnvironment.systemEnvironment()
        environment.insert('AIRALERT_SAY', text)
        process.setProcessEnvironment(environment)
        process.finished.connect(lambda *_: self._speech_done(process, text))
        process.errorOccurred.connect(lambda error: self._speech_done(process, text)
                                      if error == QProcess.ProcessError.FailedToStart else None)
        self._speaker = process
        process.start(self.speech_program, self.speech_args)

    def _speech_done(self, process, text):
        if self._speaker is process:
            self._speaker = None
            self.spoken.emit(text)
            process.deleteLater()
            self._next_speech()

    # ------------------------------------------------------------------- phone
    def phone_configured(self):
        p = self.config['phone']
        return bool((p.get('ntfy_enabled') and p.get('ntfy_topic')) or
                    (p.get('pushover_enabled') and p.get('pushover_user') and p.get('pushover_token')))

    def send_phone(self, title, message, urgent=False, only=None):
        """Queue pushes to every configured service (or only `only`). Returns True if any was sent."""
        p = self.config['phone']
        sent = False
        if (only in (None, 'ntfy')) and p.get('ntfy_enabled') and p.get('ntfy_topic'):
            server = (p.get('ntfy_server') or 'https://ntfy.sh').rstrip('/')
            payload = dict(topic=p['ntfy_topic'].strip(), title=title, message=message,
                           priority=5 if urgent else 4, tags=['rotating_light' if urgent else 'airplane'])
            request = QNetworkRequest(QUrl(server))
            request.setHeader(QNetworkRequest.KnownHeaders.ContentTypeHeader, 'application/json')
            request.setTransferTimeout(15000)
            if p.get('ntfy_token'):
                request.setRawHeader(b'Authorization', f"Bearer {p['ntfy_token'].strip()}".encode())
            body = json.dumps(payload).encode('utf-8')
            self._post('ntfy', request, body, payload)
            sent = True
        if (only in (None, 'pushover')) and p.get('pushover_enabled') and p.get('pushover_user') and \
                p.get('pushover_token'):
            query = QUrlQuery()
            fields = dict(token=p['pushover_token'].strip(), user=p['pushover_user'].strip(), title=title,
                          message=message, priority='1' if urgent else '0')
            for key, value in fields.items():
                query.addQueryItem(key, value)
            request = QNetworkRequest(QUrl(PUSHOVER_URL))
            request.setHeader(QNetworkRequest.KnownHeaders.ContentTypeHeader, 'application/x-www-form-urlencoded')
            request.setTransferTimeout(15000)
            body = query.toString(QUrl.ComponentFormattingOption.FullyEncoded).encode('utf-8')
            self._post('pushover', request, body, dict(fields, token='***', user='***'))
            sent = True
        return sent

    def _post(self, service, request, body, logged):
        self.sent.append((service, logged))
        reply = self.network.post(request, QByteArray(body))
        reply.finished.connect(lambda r=reply, s=service: self._posted(r, s))

    def _posted(self, reply, service):
        try:
            status = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
            if reply.error() == QNetworkReply.NetworkError.NoError and (status or 200) < 300:
                self.phoneResult.emit(service, True, f'{service} notification sent')
            else:
                detail = bytes(reply.readAll()).decode('utf-8', 'replace')[:200]
                message = f'{service} notification failed: {reply.errorString()}' + (f' ({detail})' if detail else '')
                log.warning(message)
                self.phoneResult.emit(service, False, message)
        finally:
            reply.deleteLater()
