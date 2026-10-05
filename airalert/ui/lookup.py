"""Optional online lookups for the selected aircraft, exposed to QML as ``lookup``: a photo and the likely route.

Both are off until switched on in Settings. When on, the selected aircraft's ICAO address (photo) or callsign
(route) is sent to adsbdb.com; photos it points to are fetched from airport-data.com. Nothing is requested for
simulated traffic, and nothing about your station or location is sent. Answers are remembered for the session
and photos are kept on disk for a month.
"""
import json
import logging
import re
import time

from PySide6.QtCore import QObject, QUrl, Property, Signal
from PySide6.QtGui import QImage
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest

from .. import __version__

log = logging.getLogger(__name__)
PHOTO_DAYS = 30
MAX_PHOTO_BYTES = 6 * 1024 ** 2
HEX = re.compile(r'^[0-9A-F]{6}$')
CALLSIGN = re.compile(r'^[A-Z0-9]{3,8}$')


class Lookup(QObject):
    changed = Signal()

    def __init__(self, controller):
        super().__init__(controller)
        self.controller = controller
        self.base = 'https://api.adsbdb.com/v0'
        self.network = QNetworkAccessManager(self)
        self.folder = controller.config.folder / 'photos'
        self._aircraft = {}     # hex -> dict(photo=url text or '', pending=bool)
        self._routes = {}       # callsign -> dict(route='', detail='', pending=bool)
        self._info = {}
        controller.selectionChanged.connect(self.refresh)
        controller.settingsChanged.connect(self.refresh)

    info = Property('QVariantMap', lambda self: self._info, notify=changed)

    # ----------------------------------------------------------------- state
    def refresh(self):
        """Look up the selected aircraft (once) and publish what is known about it."""
        settings = self.controller.config['online_lookup']
        key = self.controller.property('selectedKey')
        target = self.controller.lookup_target(key) if key else None
        info = {}
        if target is not None and target.kind == 'aircraft' and not target.simulated:
            address = target.identifier.upper()
            callsign = str(target.data.get('callsign') or '').strip().upper()
            if settings.get('photos') and HEX.match(address):
                entry = self._aircraft.get(address) or self._request_aircraft(address)
                info.update(photo=entry.get('photo', ''), photoCredit='Photo: airport-data.com' if entry.get('photo') else '',
                            photoPending=bool(entry.get('pending')))
            if settings.get('routes') and CALLSIGN.match(callsign):
                entry = self._routes.get(callsign) or self._request_route(callsign)
                info.update(route=entry.get('route', ''), routeDetail=entry.get('detail', ''),
                            routePending=bool(entry.get('pending')))
            if info:
                info['key'] = key
        if info != self._info:
            self._info = info
            self.changed.emit()

    # -------------------------------------------------------------- requests
    def _get(self, url, done, entry):
        request = QNetworkRequest(QUrl(url))
        request.setHeader(QNetworkRequest.KnownHeaders.UserAgentHeader, f'AirAlert/{__version__}')
        request.setTransferTimeout(15000)
        reply = self.network.get(request)

        def finished():
            try:
                ok = reply.error() == QNetworkReply.NetworkError.NoError
                done(bytes(reply.readAll()) if ok else None)
            except Exception:   # a malformed answer must never break the interface
                log.exception('Online lookup failed')
                entry['pending'] = False
            finally:
                reply.deleteLater()
                self.refresh()
        reply.finished.connect(finished)

    def _photo_file(self, address):
        return self.folder / f'{address}.jpg'

    def _request_aircraft(self, address):
        entry = self._aircraft[address] = dict(photo='', pending=True)
        cached = self._photo_file(address)
        try:
            if cached.is_file() and time.time() - cached.stat().st_mtime < PHOTO_DAYS * 86400:
                entry.update(photo=QUrl.fromLocalFile(str(cached)).toString(), pending=False)
                return entry
        except OSError:
            pass

        def got_record(payload):
            url = ''
            if payload:
                try:
                    aircraft = json.loads(payload)['response']['aircraft']
                    url = str(aircraft.get('url_photo') or aircraft.get('url_photo_thumbnail') or '')
                except (ValueError, KeyError, TypeError):
                    url = ''
            if not url.lower().startswith(('https://', 'http://127.0.0.1')):
                entry['pending'] = False
                return
            self._get(url, got_photo, entry)

        def got_photo(payload):
            entry['pending'] = False
            if not payload or len(payload) > MAX_PHOTO_BYTES or QImage.fromData(payload).isNull():
                return
            try:
                self.folder.mkdir(parents=True, exist_ok=True)
                cached.write_bytes(payload)
                entry['photo'] = QUrl.fromLocalFile(str(cached)).toString()
            except OSError:
                log.exception('Could not save the aircraft photo')
        self._get(f'{self.base}/aircraft/{address}', got_record, entry)
        return entry

    def _request_route(self, callsign):
        entry = self._routes[callsign] = dict(route='', detail='', pending=True)

        def got(payload):
            entry['pending'] = False
            if not payload:
                return
            try:
                route = json.loads(payload)['response']['flightroute']
                origin, destination = route['origin'], route['destination']
            except (ValueError, KeyError, TypeError):
                return

            def code(airport):
                return str(airport.get('iata_code') or airport.get('icao_code') or '?')

            def place(airport):
                return str(airport.get('municipality') or airport.get('name') or code(airport))
            entry.update(route=f'{code(origin)} → {code(destination)}',
                         detail=f'{place(origin)} → {place(destination)}')
        self._get(f'{self.base}/callsign/{callsign}', got, entry)
        return entry


def create(controller):
    return Lookup(controller)
