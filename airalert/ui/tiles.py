"""Optional OpenStreetMap tiles with a bounded disk and memory cache."""
import logging
import time
from collections import OrderedDict

from PySide6.QtCore import QObject, QUrl, Signal
from PySide6.QtGui import QImage
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkDiskCache, QNetworkReply, QNetworkRequest

log = logging.getLogger(__name__)

TILE_URL = 'https://tile.openstreetmap.org/{z}/{x}/{y}.png'
USER_AGENT = b'AirAlert/1.0 (local ADS-B/AIS desktop viewer)'
MAX_PENDING = 8
MEMORY_TILES = 256


def _night_table():
    """Luminance -> deep navy ramp; bright paper turns dark, dark ink turns light."""
    table = []
    for gray in range(256):
        ink = 255 - gray
        level = min(255.0, 16 + ink * 1.18)
        table.append(0xFF000000 | (int(level * .72) << 16) | (int(level * .84) << 8) | min(255, int(level * 1.0 + 10)))
    return table


NIGHT_TABLE = _night_table()


def night_tile(image):
    gray = image.convertToFormat(QImage.Format.Format_Grayscale8)
    raw = bytes(gray.constBits())
    indexed = QImage(raw, gray.width(), gray.height(), gray.bytesPerLine(), QImage.Format.Format_Indexed8)
    indexed.setColorTable(NIGHT_TABLE)
    return indexed.convertToFormat(QImage.Format.Format_RGB32)


class TileManager(QObject):
    tileReady = Signal()
    statusChanged = Signal(str)

    def __init__(self, folder, parent=None):
        super().__init__(parent)
        self.network = QNetworkAccessManager(self)
        cache = QNetworkDiskCache(self)
        cache.setCacheDirectory(str(folder / 'tiles'))
        cache.setMaximumCacheSize(128 * 1024 * 1024)
        self.network.setCache(cache)
        self.images = OrderedDict()
        self.night = OrderedDict()
        self.pending = set()
        self.failed = {}
        self.status = 'Street tiles off'
        self.enabled = False

    def set_enabled(self, enabled):
        self.enabled = enabled
        self._status('Street tiles on' if enabled else 'Street tiles off')

    def _status(self, text):
        if text != self.status:
            self.status = text
            self.statusChanged.emit(text)

    def get(self, z, x, y, night=False):
        """Return a cached QImage or None (and request it)."""
        key = (z, x, y)
        store = self.night if night else self.images
        image = store.get(key)
        if image is not None:
            store.move_to_end(key)
            return image
        original = self.images.get(key)
        if original is not None:
            if night:
                converted = night_tile(original)
                self.night[key] = converted
                if len(self.night) > MEMORY_TILES:
                    self.night.popitem(last=False)
                return converted
            return original
        self.request(key)
        return None

    def peek(self, z, x, y, night=False):
        """Cached tile or None, without requesting it."""
        key = (z, x, y)
        image = (self.night if night else self.images).get(key)
        if image is None and night and key in self.images:
            image = self.night[key] = night_tile(self.images[key])
            if len(self.night) > MEMORY_TILES:
                self.night.popitem(last=False)
        return image

    def request(self, key):
        if not self.enabled or key in self.pending or len(self.pending) >= MAX_PENDING:
            return
        if time.monotonic() - self.failed.get(key, -1e9) < 60:
            return
        z, x, y = key
        request = QNetworkRequest(QUrl(TILE_URL.format(z=z, x=x, y=y)))
        request.setRawHeader(b'User-Agent', USER_AGENT)
        request.setTransferTimeout(7000)  # a stalled download must not hold a request slot forever
        request.setAttribute(QNetworkRequest.Attribute.CacheLoadControlAttribute,
                             QNetworkRequest.CacheLoadControl.PreferCache)
        reply = self.network.get(request)
        self.pending.add(key)
        reply.finished.connect(lambda r=reply, k=key: self._finished(r, k))

    def _finished(self, reply, key):
        self.pending.discard(key)
        try:
            if reply.error() != QNetworkReply.NetworkError.NoError:
                self.failed[key] = time.monotonic()
                self._status('Street tiles offline · cached tiles and grid shown')
                return
            image = QImage()
            if image.loadFromData(bytes(reply.readAll())):
                self.images[key] = image.convertToFormat(QImage.Format.Format_RGB32)
                if len(self.images) > MEMORY_TILES:
                    self.images.popitem(last=False)
                self._status('Street tiles on')
                self.tileReady.emit()
        finally:
            reply.deleteLater()
