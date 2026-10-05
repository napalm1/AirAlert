"""AirAlert tracking canvas: Web Mercator street/grid map and a radar-scope view.

Geometry is computed and painted inside paint(), which Qt Quick calls while the
GUI thread is blocked, so reading the live target store here is safe. Anything
QML binds to (selection point, hover, range text) is computed on the GUI thread.

The display clock is ``scene['now']`` during history playback and wall time
otherwise; every age-dependent style (stale color, trail fading, heading lines)
uses it.
"""
import logging
import math
import time
from bisect import bisect_left, bisect_right

from PySide6.QtCore import (QEasingCurve, QLineF, QPointF, QRectF, Qt, QTimer, QVariantAnimation, Property, Signal,
                            Slot)
from PySide6.QtGui import (QBrush, QColor, QConicalGradient, QFont, QFontMetricsF, QImage, QPainter,
                           QPainterPath, QPen, QPolygonF, QRadialGradient, QTransform, QWindow)
from PySide6.QtQuick import QQuickPaintedItem

from .. import airports
from ..core.models import UNIT_KM, destination, distance_bearing

log = logging.getLogger(__name__)

EARTH_KM = 40075.016686
EARTH_RADIUS_KM = 6371.0088  # as core.models.distance_bearing
MIN_ZOOM, MAX_ZOOM, TILE_MAX_ZOOM = 2.0, 18.0, 19
STALE_SECONDS = 60
SWEEP_DEG_PER_SECOND = 45
KNOT_KM_H = 1.852
EMERGENCY_SQUAWKS = {'7500': 'HIJACK', '7600': 'RADIO FAILURE', '7700': 'EMERGENCY'}
AIRPORT_LABEL_ZOOM = (7.0, 10.0, 12.5)   # per tier: major, other, heliport
AIRPORT_TOP_RANK = 1000                  # the busiest major airports are labeled from their first zoom
AIRPORT_SPACING = 10.0                   # declutter grid for airport symbols (px)
AIRPORT_HIT_PX = 8.0
TRAIL_TOLERANCE_PX = 3.0                 # map trail vertices closer than this add nothing visible
TRAIL_WIDTH = (1.0, 1.0, 1.0, 1.0, 1.7, 1.7)   # per age band, oldest first
TRAIL_ALPHA = (60, 92, 124, 156, 196, 232)
TRAIL_REBUILD_BUDGET = 0.004             # seconds per frame spent refining trails after a zoom change

PALETTES = {
    'Dark': dict(background='#09111f', grid='#142238', grid_text='#4a6283', ring='#4b6a96', ring_text='#9db4d6',
                 home='#f1f5ff', zone='#a78bfa', history='#fbbf24', label='#0c1628', label_text='#e8eef8',
                 label_muted='#8fa3c2', vessel='#2dd4bf', stale='#6c7b92', outline='#050b16', selection='#ffffff',
                 star='#fbbf24', draft='#f472b6', scale='#c3d2ea', airport='#86a7d6', airport_text='#b3c6e3',
                 airport_minor='#6f8bb4', coverage='#38bdf8', emergency='#ff3b4f', emergency_text='#ffffff'),
    'Light': dict(background='#e6edf6', grid='#d0dbe9', grid_text='#7a8ca6', ring='#6d86aa', ring_text='#3b5170',
                  home='#16233a', zone='#7c3aed', history='#b45309', label='#ffffff', label_text='#122036',
                  label_muted='#5d6f89', vessel='#0d9488', stale='#8b98ab', outline='#ffffff', selection='#10213a',
                  star='#d97706', draft='#db2777', scale='#34475f', airport='#355c93', airport_text='#1f3657',
                  airport_minor='#5a7599', coverage='#0284c7', emergency='#d91c33', emergency_text='#ffffff'),
    'Scope': dict(background='#010604', face='#03130a', ring='#1d5a33', ring_text='#6fcf91', tick='#2c7a48',
                  tick_minor='#17462a', text='#9ff0bb', aircraft='#c2ffd8', vessel='#5cf09a', stale='#44684f',
                  zone='#62d38c', history='#e2ec8a', home='#d3fbe1', selection='#eafff1', label='#021008',
                  star='#f4f19a', draft='#f6a6d0', outline='#010604', airport='#46b86e', airport_text='#63c486',
                  airport_minor='#2f7d4b', coverage='#3fbf6a', emergency='#ff4d5e', emergency_text='#ffffff'),
}
ALTITUDE_STOPS = {
    'Dark': [(0, '#ff9f43'), (5000, '#ffd166'), (10000, '#5ee27a'), (20000, '#38d6f5'), (30000, '#5b8cff'),
             (40000, '#b07cff')],
    'Light': [(0, '#d9730d'), (5000, '#b38600'), (10000, '#1f9d55'), (20000, '#0e8fb3'), (30000, '#2f5fe0'),
              (40000, '#7c3aed')],
}
UNKNOWN_ALTITUDE = {'Dark': '#7cc4ff', 'Light': '#1d6fe0'}


def norm(lat, lon):
    """Normalized Web Mercator coordinates in [0, 1)."""
    lat = max(-85.0511, min(85.0511, lat))
    s = math.sin(math.radians(lat))
    return (lon + 180) / 360, 0.5 - math.log((1 + s) / (1 - s)) / (4 * math.pi)


def denorm(x, y):
    return math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y)))), (x % 1.0) * 360 - 180


def _mix(a, b, f):
    ca, cb = QColor(a), QColor(b)
    return QColor.fromRgbF(ca.redF() + (cb.redF() - ca.redF()) * f, ca.greenF() + (cb.greenF() - ca.greenF()) * f,
                           ca.blueF() + (cb.blueF() - ca.blueF()) * f)


def altitude_color(altitude, theme='Dark'):
    if altitude is None:
        return QColor(UNKNOWN_ALTITUDE[theme])
    stops = ALTITUDE_STOPS[theme]
    if altitude <= stops[0][0]:
        return QColor(stops[0][1])
    for (a0, c0), (a1, c1) in zip(stops, stops[1:]):
        if altitude <= a1:
            return _mix(c0, c1, (altitude - a0) / (a1 - a0))
    return QColor(stops[-1][1])


def _alpha(color, alpha):
    c = QColor(color)
    c.setAlpha(max(0, min(255, int(alpha))))
    return c


def _polygon_path(points, scale=1.0):
    path = QPainterPath()
    path.moveTo(points[0][0] * scale, points[0][1] * scale)
    for x, y in points[1:]:
        path.lineTo(x * scale, y * scale)
    path.closeSubpath()
    return path


PLANE = _polygon_path([(0, -11), (1.6, -8), (1.8, -3), (10, 2.5), (10, 4.6), (1.8, 2.4), (1.3, 7.2), (4.6, 9.6),
                       (4.6, 11), (0, 10), (-4.6, 11), (-4.6, 9.6), (-1.3, 7.2), (-1.8, 2.4), (-10, 4.6),
                       (-10, 2.5), (-1.8, -3), (-1.6, -8)])
SHIP = _polygon_path([(0, -10.5), (4.6, -4), (4.6, 8.5), (0, 10.5), (-4.6, 8.5), (-4.6, -4)])
STAR = _polygon_path([(math.cos(math.radians(-90 + i * 36)) * (4.6 if i % 2 == 0 else 2.0),
                       math.sin(math.radians(-90 + i * 36)) * (4.6 if i % 2 == 0 else 2.0)) for i in range(10)])


def nice_distance(value):
    """Largest 1/2/5 x 10^n not above value."""
    if value <= 0:
        return 0
    base = 10 ** math.floor(math.log10(value))
    for m in (5, 2, 1):
        if m * base <= value:
            return m * base
    return base


def distance_text(value):
    return f'{value:.0f}' if value >= 10 else f'{value:.1f}'.rstrip('0').rstrip('.') if value >= 1 else f'{value:.2f}'


def emergency_code(target):
    """'7500' / '7600' / '7700' when an aircraft squawks an emergency code, else ''."""
    if target.kind != 'aircraft':
        return ''
    value = target.data.get('squawk')
    if value is None:
        return ''
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    text = str(value).strip()
    if not text.isdigit():
        return ''
    text = text.zfill(4)
    return text if text in EMERGENCY_SQUAWKS else ''


def emergency_text(code):
    return f'SQUAWK {code} · {EMERGENCY_SQUAWKS[code]}'


def target_track(target):
    """Direction of travel in degrees: aircraft 'heading' holds the track; vessels prefer course over ground."""
    for key in (('course', 'heading') if target.kind == 'vessel' else ('heading',)):
        value = target.data.get(key)
        if value is not None:
            try:
                return float(value) % 360
            except (TypeError, ValueError):
                continue
    return None


class _Occupancy:
    """Screen rectangles bucketed on a coarse grid, so overlap tests stay cheap with many labels."""
    CELL = 32.0

    def __init__(self):
        self.cells = {}

    def _keys(self, r):
        c = self.CELL
        x0, x1 = int(r.left() // c), int(r.right() // c)
        y0, y1 = int(r.top() // c), int(r.bottom() // c)
        return [(i, j) for i in range(x0, x1 + 1) for j in range(y0, y1 + 1)]

    def append(self, rect):
        cells = self.cells
        for k in self._keys(rect):
            bucket = cells.get(k)
            if bucket is None:
                cells[k] = [rect]
            else:
                bucket.append(rect)

    def intersects(self, rect):
        cells = self.cells
        for k in self._keys(rect):
            bucket = cells.get(k)
            if bucket:
                for other in bucket:
                    if rect.intersects(other):
                        return True
        return False


class MapCanvas(QQuickPaintedItem):
    targetClicked = Signal(str)
    backgroundClicked = Signal()
    zoneFinished = Signal('QVariantList')
    viewChanged = Signal()
    drawingChanged = Signal()
    hoverChanged = Signal()
    airportHoverChanged = Signal()
    selectionPointChanged = Signal()
    emergencyChanged = Signal()
    insetsChanged = Signal()
    styleChanged = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptedMouseButtons(Qt.MouseButton.LeftButton | Qt.MouseButton.RightButton)
        self.setAcceptHoverEvents(True)
        self.setAntialiasing(True)
        self.setOpaquePainting(True)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.tiles = None
        self.scene = dict(targets=[], selected='', watched=set(), home=None, rings=[], units='mi', zones=[],
                          history=[], draft=[], trail_minutes=15, layers={}, tiles=False, theme='Dark',
                          map_theme='Follow app', heading_minutes=2, now=None)
        self._lat, self._lon, self._zoom = 0.0, 0.0, 3.0
        self._insets = [0.0, 0.0, 0.0, 0.0]  # left, top, right, bottom
        self.drawing = False
        self.vertices = []
        self._hover_geo = None
        self._press = None
        self._press_geo = None
        self._last_pos = None
        self._dragged = False
        self._hits = []
        self._airport_hits = []
        self._hover = ('', 0.0, 0.0)
        self._airport_hover = (-1, 0.0, 0.0)
        self._selection = (0.0, 0.0, False)
        self._emergency_points = []
        self._trail_cache = {}
        self._trail_poly_cache = {}
        self._trail_deadline = 0.0
        self._scope_trail_cache = {}
        self._heading_cache = {}
        self._coverage_cache = None
        self._ring_cache = {}
        self._sprites = {}
        self._scope_cache = None
        self._scope_blips = []
        self._scope_badges = ([], None)
        self._sweep_elapsed = 0.0
        self._sweep_started = None
        self._zoom_anchor = None
        self._zoom_anim = QVariantAnimation(self)
        self._zoom_anim.setDuration(170)
        self._zoom_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._zoom_anim.valueChanged.connect(self._animate_zoom)
        self._sweep_timer = QTimer(self)
        self._sweep_timer.setInterval(33)
        self._sweep_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._sweep_timer.timeout.connect(self._sweep_frame)
        self._sweep_timer.start()
        self.widthChanged.connect(self._geometry_changed)
        self.heightChanged.connect(self._geometry_changed)

    # ------------------------------------------------------------------ styling
    def style_name(self):
        theme = self.scene.get('map_theme', 'Follow app')
        if theme == 'Scope only':
            return 'Scope'
        if theme in ('Dark', 'Light'):
            return theme
        return self.scene.get('theme', 'Dark')

    def _get_style(self):
        return self.style_name()

    styleName = Property(str, _get_style, notify=styleChanged)

    def set_scene(self, scene):
        old_style = self.style_name()
        self.scene = scene
        self._scope_cache = None
        if self.style_name() != old_style:
            self.styleChanged.emit()
            self.viewChanged.emit()
        self._update_selection_point()
        self._update_emergency_points()
        self.update()

    def _now(self):
        """Display clock: the history playback time when replaying, else wall time."""
        return self.scene.get('now') or time.time()

    # ----------------------------------------------------------------- geometry
    def _view_rect(self):
        left, top, right, bottom = self._insets
        w, h = max(1.0, self.width()), max(1.0, self.height())
        rect = QRectF(left, top, max(80.0, w - left - right), max(80.0, h - top - bottom))
        return rect

    def _world(self):
        return 256 * 2 ** self._zoom

    def _center_px(self):
        W = self._world()
        nx, ny = norm(self._lat, self._lon)
        return nx * W, ny * W

    def _scope_geometry(self):
        rect = self._view_rect()
        radius = max(40.0, min(rect.width(), rect.height()) / 2 - 34)
        km_per_px = EARTH_KM * math.cos(math.radians(self._lat)) / self._world()
        return rect.center(), radius, max(0.05, radius * km_per_px)

    def screen(self, lat, lon):
        """Screen point for a coordinate (nearest world copy in map mode)."""
        if self.style_name() == 'Scope':
            center, radius, rng = self._scope_geometry()
            d, b = distance_bearing((self._lat, self._lon), (lat, lon))
            r = d / rng * radius
            return QPointF(center.x() + math.sin(math.radians(b)) * r, center.y() - math.cos(math.radians(b)) * r)
        W = self._world()
        cx, cy = self._center_px()
        c = self._view_rect().center()
        nx, ny = norm(lat, lon)
        dx = nx * W - cx
        dx -= round(dx / W) * W
        return QPointF(c.x() + dx, c.y() + ny * W - cy)

    def coordinate(self, x, y):
        if self.style_name() == 'Scope':
            center, radius, rng = self._scope_geometry()
            dx, dy = x - center.x(), y - center.y()
            d = math.hypot(dx, dy) / radius * rng
            b = math.degrees(math.atan2(dx, -dy)) % 360
            return destination((self._lat, self._lon), d, b) if d > 0 else (self._lat, self._lon)
        W = self._world()
        cx, cy = self._center_px()
        c = self._view_rect().center()
        return denorm((cx + x - c.x()) / W, max(0.0, min(1.0, (cy + y - c.y()) / W)))

    def _set_center(self, lat, lon):
        self._lat = max(-85.0, min(85.0, lat))
        self._lon = (lon + 180) % 360 - 180
        self._view_moved()

    def _view_moved(self):
        self._scope_cache = None
        self._update_selection_point()
        self._update_emergency_points()
        if self._hover[0]:
            self._hover = ('', 0.0, 0.0)
            self.hoverChanged.emit()
        if self._airport_hover[0] >= 0:
            self._airport_hover = (-1, 0.0, 0.0)
            self.airportHoverChanged.emit()
        self.viewChanged.emit()
        self.update()

    def _geometry_changed(self):
        self._scope_cache = None
        self._update_selection_point()
        self._update_emergency_points()
        self.viewChanged.emit()

    def _update_selection_point(self):
        key = self.scene.get('selected')
        target = next((t for t in self.scene.get('targets', []) if t.key == key), None) if key else None
        if target is not None and target.position:
            pt = self.screen(*target.position)
            visible = self._point_visible(pt)
            value = (pt.x(), pt.y(), visible)
        else:
            value = (0.0, 0.0, False)
        if value != self._selection:
            self._selection = value
            self.selectionPointChanged.emit()

    def _update_emergency_points(self):
        points = []
        for t in self.scene.get('targets', []):
            if t.kind == 'aircraft' and t.position and emergency_code(t):
                pt = self.screen(*t.position)
                if self._point_visible(pt):
                    points.append(dict(x=pt.x(), y=pt.y(), key=t.key))
        if points != self._emergency_points:
            self._emergency_points = points
            self.emergencyChanged.emit()

    def _point_visible(self, pt):
        if self.style_name() == 'Scope':
            center, radius, _ = self._scope_geometry()
            return math.hypot(pt.x() - center.x(), pt.y() - center.y()) <= radius
        return 0 <= pt.x() <= self.width() and 0 <= pt.y() <= self.height()

    # ------------------------------------------------------------ QML properties
    def _get_zoom(self):
        return self._zoom

    def _get_lat(self):
        return self._lat

    def _get_lon(self):
        return self._lon

    def _range_text(self):
        units = self.scene.get('units', 'mi')
        if self.style_name() == 'Scope':
            _, _, rng = self._scope_geometry()
            return f'{distance_text(rng / UNIT_KM[units])} {units}'
        return ''

    def _scale_text(self):
        units = self.scene.get('units', 'mi')
        km_per_px = EARTH_KM * math.cos(math.radians(self._lat)) / self._world()
        return f'{distance_text(nice_distance(110 * km_per_px / UNIT_KM[units]))} {units}'

    zoom = Property(float, _get_zoom, notify=viewChanged)
    centerLat = Property(float, _get_lat, notify=viewChanged)
    centerLon = Property(float, _get_lon, notify=viewChanged)
    rangeText = Property(str, _range_text, notify=viewChanged)
    scaleText = Property(str, _scale_text, notify=viewChanged)

    def _is_drawing(self):
        return self.drawing

    def _vertex_count(self):
        return len(self.vertices)

    isDrawing = Property(bool, _is_drawing, notify=drawingChanged)
    vertexCount = Property(int, _vertex_count, notify=drawingChanged)

    hoverKey = Property(str, lambda self: self._hover[0], notify=hoverChanged)
    hoverX = Property(float, lambda self: self._hover[1], notify=hoverChanged)
    hoverY = Property(float, lambda self: self._hover[2], notify=hoverChanged)
    selectedX = Property(float, lambda self: self._selection[0], notify=selectionPointChanged)
    selectedY = Property(float, lambda self: self._selection[1], notify=selectionPointChanged)
    selectedVisible = Property(bool, lambda self: self._selection[2], notify=selectionPointChanged)
    emergencyPoints = Property('QVariantList', lambda self: self._emergency_points, notify=emergencyChanged)

    def _airport_info(self):
        index = airports.get() if self._airport_hover[0] >= 0 else None
        return index.info(self._airport_hover[0]) if index is not None else {}

    airportInfo = Property('QVariantMap', _airport_info, notify=airportHoverChanged)
    airportX = Property(float, lambda self: self._airport_hover[1], notify=airportHoverChanged)
    airportY = Property(float, lambda self: self._airport_hover[2], notify=airportHoverChanged)

    def _make_inset(index):
        def getter(self):
            return self._insets[index]

        def setter(self, value):
            value = float(value)
            if abs(value - self._insets[index]) < 0.01:
                return
            if self.style_name() != 'Scope' and self.width() > 1:
                # Keep the map still on screen while panels open or close: the
                # geographic point already under the new view center becomes the center.
                W = self._world()
                cx, cy = self._center_px()
                old_center = self._view_rect().center()
                self._insets[index] = value
                new_center = self._view_rect().center()
                self._lat, self._lon = denorm((cx + new_center.x() - old_center.x()) / W,
                                              max(0.0, min(1.0, (cy + new_center.y() - old_center.y()) / W)))
            else:
                self._insets[index] = value
            self.insetsChanged.emit()
            self._view_moved()
        return getter, setter

    leftInset = Property(float, *_make_inset(0), notify=insetsChanged)
    topInset = Property(float, *_make_inset(1), notify=insetsChanged)
    rightInset = Property(float, *_make_inset(2), notify=insetsChanged)
    bottomInset = Property(float, *_make_inset(3), notify=insetsChanged)
    del _make_inset

    # ------------------------------------------------------------------ actions
    @Slot(float, float)
    def centerOn(self, lat, lon):
        self._set_center(lat, lon)

    @Slot(float, float, float)
    def centerOnZoom(self, lat, lon, zoom):
        self._zoom = max(MIN_ZOOM, min(MAX_ZOOM, zoom))
        self._set_center(lat, lon)

    @Slot()
    def zoomIn(self):
        self._zoom_to(round(self._zoom) + 1, None)

    @Slot()
    def zoomOut(self):
        self._zoom_to(round(self._zoom) - 1, None)

    def _zoom_to(self, target, anchor):
        target = max(MIN_ZOOM, min(MAX_ZOOM, target))
        if abs(target - self._zoom) < 1e-6:
            return
        if anchor is None:
            anchor = self._view_rect().center()
        self._zoom_anchor = (anchor, self.coordinate(anchor.x(), anchor.y()))
        self._zoom_anim.stop()
        self._zoom_anim.setStartValue(float(self._zoom))
        self._zoom_anim.setEndValue(float(target))
        self._zoom_anim.start()

    def _animate_zoom(self, value):
        anchor, geo = self._zoom_anchor
        self._zoom = float(value)
        if self.style_name() == 'Scope':
            center, radius, rng = self._scope_geometry()
            dx, dy = anchor.x() - center.x(), anchor.y() - center.y()
            d = math.hypot(dx, dy) / radius * rng
            if d > 1e-6:
                b = math.degrees(math.atan2(dx, -dy))
                lat, lon = destination(geo, d, (b + 180) % 360)
            else:
                lat, lon = geo
        else:
            W = self._world()
            gx, gy = norm(*geo)
            c = self._view_rect().center()
            lat, lon = denorm((gx * W - (anchor.x() - c.x())) / W, max(0.0, min(1.0, (gy * W - (anchor.y() - c.y())) / W)))
        self._set_center(lat, lon)

    @Slot()
    def beginZone(self):
        self.drawing = True
        self.vertices = []
        self._hover_geo = None
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.drawingChanged.emit()
        self.update()

    @Slot()
    def finishZone(self):
        if self.drawing and len(self.vertices) >= 3:
            points = [list(v) for v in self.vertices]
            self._end_drawing()
            self.zoneFinished.emit(points)

    @Slot()
    def undoVertex(self):
        if self.drawing and self.vertices:
            self.vertices.pop()
            self.drawingChanged.emit()
            self.update()

    @Slot()
    def cancelZone(self):
        if self.drawing or self.vertices:
            self._end_drawing()

    def _end_drawing(self):
        self.drawing = False
        self.vertices = []
        self._hover_geo = None
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.drawingChanged.emit()
        self.update()

    # ------------------------------------------------------------------- input
    def mousePressEvent(self, e):
        pos = e.position()
        self._press = pos
        self._last_pos = pos
        self._press_geo = self.coordinate(pos.x(), pos.y())
        self._dragged = False
        if not self.drawing:
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
        e.accept()

    def mouseMoveEvent(self, e):
        if self._press is None:
            return
        pos = e.position()
        if not self._dragged and (pos - self._press).manhattanLength() < 4:
            return
        self._dragged = True
        if self.style_name() == 'Scope':
            center, radius, rng = self._scope_geometry()
            dx, dy = pos.x() - center.x(), pos.y() - center.y()
            d = math.hypot(dx, dy) / radius * rng
            if d > 1e-6:
                b = math.degrees(math.atan2(dx, -dy))
                self._set_center(*destination(self._press_geo, d, (b + 180) % 360))
            else:
                self._set_center(*self._press_geo)
        else:
            W = self._world()
            cx, cy = self._center_px()
            delta = pos - self._last_pos
            self._set_center(*denorm((cx - delta.x()) / W, max(0.0, min(1.0, (cy - delta.y()) / W))))
        self._last_pos = pos
        e.accept()

    def mouseReleaseEvent(self, e):
        pos = e.position()
        if self._press is None:  # release that ends a double-click
            e.accept()
            return
        was_drag = self._dragged
        self._press = None
        self._dragged = False
        self.setCursor(Qt.CursorShape.CrossCursor if self.drawing else Qt.CursorShape.OpenHandCursor)
        if was_drag:
            e.accept()
            return
        if self.drawing:
            if e.button() == Qt.MouseButton.RightButton:
                self.undoVertex()
            elif self.style_name() != 'Scope' or self._point_visible(pos):
                self.vertices.append(self.coordinate(pos.x(), pos.y()))
                self.drawingChanged.emit()
                self.update()
        elif e.button() == Qt.MouseButton.LeftButton:
            key = self._hit(pos)
            if key:
                self.targetClicked.emit(key)
            elif self._airport_hit(pos) is None:
                # A click on an airport only shows its card; it never changes the selection.
                self.backgroundClicked.emit()
        e.accept()

    def mouseDoubleClickEvent(self, e):
        self._press = None
        if self.drawing:
            # The first click of the double-click already added a vertex.
            self.finishZone()
        e.accept()

    def wheelEvent(self, e):
        steps = e.angleDelta().y() / 120.0
        if steps:
            base = self._zoom_anim.endValue() if self._zoom_anim.state() == QVariantAnimation.State.Running else self._zoom
            self._zoom_to(float(base) + steps * 0.5, e.position())
        e.accept()

    def hoverMoveEvent(self, e):
        pos = e.position()
        if self.drawing:
            self._hover_geo = self.coordinate(pos.x(), pos.y())
            self.update()
            return
        key = self._hit(pos)
        hit = next(((x, y) for k, x, y in self._hits if k == key), (pos.x(), pos.y())) if key else (0.0, 0.0)
        value = (key or '', hit[0], hit[1])
        if value != self._hover:
            self._hover = value
            self.setCursor(Qt.CursorShape.PointingHandCursor if key else Qt.CursorShape.OpenHandCursor)
            self.hoverChanged.emit()
        airport = (self._airport_hit(pos) if not key else None) or (-1, 0.0, 0.0)
        if airport != self._airport_hover:
            self._airport_hover = airport
            self.airportHoverChanged.emit()

    def hoverLeaveEvent(self, e):
        if self._hover[0]:
            self._hover = ('', 0.0, 0.0)
            self.hoverChanged.emit()
        if self._airport_hover[0] >= 0:
            self._airport_hover = (-1, 0.0, 0.0)
            self.airportHoverChanged.emit()
        if self.drawing:
            self._hover_geo = None
            self.update()

    def _hit(self, pos):
        best, best_d = '', 18.0
        for key, x, y in self._hits:
            d = math.hypot(x - pos.x(), y - pos.y())
            if d < best_d:
                best, best_d = key, d
        return best

    def _airport_hit(self, pos):
        """(airport index, x, y) of the drawn airport symbol under the pointer, or None."""
        best, best_d = None, AIRPORT_HIT_PX
        px, py = pos.x(), pos.y()
        for x, y, index in self._airport_hits:
            if abs(x - px) < best_d and abs(y - py) < best_d:
                d = math.hypot(x - px, y - py)
                if d < best_d:
                    best, best_d = (index, x, y), d
        return best

    # --------------------------------------------------------------- animation
    def _sweep_running(self):
        if self.style_name() != 'Scope' or not self.isVisible():
            return False
        window = self.window()
        return bool(window and window.isExposed() and window.visibility() != QWindow.Visibility.Minimized)

    def _sweep_frame(self):
        running = self._sweep_running()
        if running and self._sweep_started is None:
            self._sweep_started = time.monotonic()
        elif not running and self._sweep_started is not None:
            self._sweep_elapsed += time.monotonic() - self._sweep_started
            self._sweep_started = None
        if running:
            self.update()

    def sweep_angle(self):
        elapsed = self._sweep_elapsed
        if self._sweep_started is not None:
            elapsed += time.monotonic() - self._sweep_started
        return elapsed * SWEEP_DEG_PER_SECOND % 360

    # ---------------------------------------------------------------- painting
    def paint(self, p):
        try:
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
            if self.style_name() == 'Scope':
                self._paint_scope(p)
            else:
                self._paint_map(p)
        except Exception:
            log.exception('Map paint failed')

    def _dpr(self):
        window = self.window()
        return window.devicePixelRatio() if window else 1.0

    # Shared helpers -----------------------------------------------------------
    def _trail_raw(self, target):
        """Trail samples in normalized, longitude-unwrapped coordinates, computed once per sample."""
        trail = target.trail
        raw = self._trail_cache.get(target.key)
        if raw is None or trail[-1][0] < raw['last'] or (raw['ts'] and trail[0][0] < raw['ts'][0]):
            raw = self._trail_cache[target.key] = dict(ts=[], x=[], y=[], v=[], last=-math.inf)
            fresh = list(trail)
        else:
            fresh = []
            last = raw['last']
            for sample in reversed(trail):
                if sample[0] <= last:
                    break
                fresh.append(sample)
            fresh.reverse()
        ts, xs, ys, vs = raw['ts'], raw['x'], raw['y'], raw['v']
        for t, lat, lon in fresh:
            nx, ny = norm(lat, lon)
            phi, lam = math.radians(lat), math.radians(lon)
            vs.append((math.cos(phi) * math.cos(lam), math.cos(phi) * math.sin(lam), math.sin(phi)))
            if xs:
                prev = xs[-1]
                while nx - prev > 0.5:
                    nx -= 1.0
                while prev - nx > 0.5:
                    nx += 1.0
            ts.append(t)
            xs.append(nx)
            ys.append(ny)
        if fresh:
            raw['last'] = fresh[-1][0]
        if ts and ts[0] < trail[0][0]:  # samples dropped from the front of the deque
            k = bisect_left(ts, trail[0][0])
            del ts[:k], xs[:k], ys[:k], vs[:k]
        return raw

    def _trail_simplified(self, target, raw, zoom_key):
        """Trail vertices at least ~3 px apart at zoom_key (the newest sample is always kept), built incrementally.

        After a zoom change the previous simplification stays in use until the
        per-frame rebuild budget allows refining it, so zoom frames stay cheap.
        """
        simp = self._trail_poly_cache.get(target.key)
        rts, rx, ry, rv = raw['ts'], raw['x'], raw['y'], raw['v']
        rebuild = simp is None or simp['raw'] is not raw or (simp['ts'] and rts and simp['ts'][-1] > rts[-1])
        if not rebuild and simp['zoom_key'] != zoom_key and time.perf_counter() < self._trail_deadline:
            rebuild = True
        if rebuild:
            simp = self._trail_poly_cache[target.key] = dict(raw=raw, zoom_key=zoom_key, ts=[], x=[], y=[], v=[],
                                                             pts=[], result=None, signature=None)
        kts, kx, ky, kv, kpts = simp['ts'], simp['x'], simp['y'], simp['v'], simp['pts']
        if kts and rts and kts[0] < rts[0]:
            k = bisect_left(kts, rts[0])
            del kts[:k], kx[:k], ky[:k], kv[:k], kpts[:k]
        start = bisect_right(rts, kts[-1]) if kts else 0
        if start < len(rts):
            tolerance = TRAIL_TOLERANCE_PX / (256 * 2 ** (simp['zoom_key'] / 2))
            for i in range(start, len(rts)):
                if len(kts) >= 2 and abs(kx[-1] - kx[-2]) < tolerance and abs(ky[-1] - ky[-2]) < tolerance:
                    # The previous newest point sits within tolerance of the one before it: drop it.
                    kts.pop()
                    kx.pop()
                    ky.pop()
                    kv.pop()
                    kpts.pop()
                kts.append(rts[i])
                kx.append(rx[i])
                ky.append(ry[i])
                kv.append(rv[i])
                kpts.append(QPointF(rx[i], ry[i]))
        if len(self._trail_poly_cache) > 4 * max(64, len(self.scene.get('targets', []))):
            live = {t.key for t in self.scene.get('targets', [])}
            self._trail_poly_cache = {k: v for k, v in self._trail_poly_cache.items() if k in live}
            self._trail_cache = {k: v for k, v in self._trail_cache.items() if k in live}
            self._scope_trail_cache = {k: v for k, v in self._scope_trail_cache.items() if k in live}
        return simp

    def _trail_bands(self, kts, now):
        """Index bounds of the six age bands (oldest first) within the trail window, or None."""
        minutes = self.scene.get('trail_minutes', 15)
        lo = bisect_left(kts, now - minutes * 60) if minutes else 0
        hi = bisect_right(kts, now)
        if hi - lo < 2:
            return None
        duration = max(1.0, minutes * 60 if minutes else now - kts[lo])
        return [lo] + [bisect_left(kts, now - duration * (1 - k / 6), lo, hi) for k in range(1, 6)] + [hi]

    def _trail_polys(self, target, now):
        """Trail as up to six age-band polylines in unwrapped normalized coordinates.

        Vertices are cached per target and extended as samples arrive; age bands
        are timestamp slices of them, so the once-a-second refresh, panning and
        zooming only re-slice lists and change the painter transform.
        """
        trail = target.trail
        if len(trail) < 2:
            return None
        simp = self._trail_simplified(target, self._trail_raw(target), round(self._zoom * 2))
        bounds = self._trail_bands(simp['ts'], now)
        if bounds is None:
            return None
        kts = simp['ts']
        lo, hi = bounds[0], bounds[-1]
        signature = (tuple(bounds), len(kts), kts[-1], kts[0], simp['zoom_key'])
        if simp['signature'] == signature:
            return simp['result']
        kx, ky, kpts = simp['x'], simp['y'], simp['pts']
        polys = []
        for band in range(6):
            a, b = bounds[band], bounds[band + 1]
            if b <= a:
                continue
            first = a - 1 if a > lo else a  # join to the previous band
            if b - first >= 2:
                polys.append((band, QPolygonF(kpts[first:b])))
        xs, ys = kx[lo:hi], ky[lo:hi]
        result = (polys, (min(xs), min(ys), max(xs), max(ys)), kx[hi - 1])
        simp['signature'], simp['result'] = signature, result
        return result

    def _draw_trail_fast(self, p, target, now, W, cx, cy, c, color, selected):
        entry = self._trail_polys(target, now)
        if not entry:
            return
        polys, (minx, miny, maxx, maxy), last_x = entry
        ox = c.x() - cx - round((last_x * W - cx) / W) * W
        oy = c.y() - cy
        if (maxx * W + ox < -60 or minx * W + ox > self.width() + 60 or
                maxy * W + oy < -60 or miny * W + oy > self.height() + 60):
            return
        p.save()
        p.translate(ox, oy)
        p.scale(W, W)
        p.setBrush(Qt.BrushStyle.NoBrush)
        for band, poly in polys:
            # Older segments fade and thin to 1 px, which also takes Qt's fast raster path.
            pen = QPen(_alpha(color, TRAIL_ALPHA[band]), 2.6 if selected else TRAIL_WIDTH[band])
            pen.setCosmetic(True)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.BevelJoin)
            p.setPen(pen)
            p.drawPolyline(poly)
        p.restore()

    def _scope_basis(self):
        """Unit vectors of the scope center and its local east / north directions."""
        center = (self._lat, self._lon)
        cached = getattr(self, '_basis_cache', None)
        if cached is None or cached[0] != center:
            phi, lam = math.radians(self._lat), math.radians(self._lon)
            sp, cp, sl, cl = math.sin(phi), math.cos(phi), math.sin(lam), math.cos(lam)
            cached = self._basis_cache = (center, ((cp * cl, cp * sl, sp), (-sl, cl, 0.0), (-sp * cl, -sp * sl, cp)))
        return cached[1]

    def _scope_trail_polys(self, target, now):
        """Band polylines in exact azimuthal-equidistant km offsets from the scope center.

        Uses the map's simplified trail vertices; each vertex is projected once per
        scope center (vector math, no trigonometry per point), so the
        once-a-second refresh only re-slices bands and zooming only costs drawing.
        """
        if len(target.trail) < 2:
            return []
        simp = self._trail_simplified(target, self._trail_raw(target), round(self._zoom * 2))
        bounds = self._trail_bands(simp['ts'], now)
        if bounds is None:
            return []
        view = (self._lat, self._lon)
        entry = self._scope_trail_cache.get(target.key)
        if entry is None or entry[0] != view:
            entry = self._scope_trail_cache[target.key] = (view, {})
        offsets = entry[1]
        (c0, c1, c2), (e0, e1, e2), (n0, n1, n2) = self._scope_basis()
        kts, kv = simp['ts'], simp['v']
        lo, hi = bounds[0], bounds[-1]
        points = []
        atan2, sqrt = math.atan2, math.sqrt
        for i in range(lo, hi):
            q = offsets.get(kts[i])
            if q is None:
                x, y, z = kv[i]
                east, north = x * e0 + y * e1 + z * e2, x * n0 + y * n1 + z * n2
                horizontal = sqrt(east * east + north * north)
                if horizontal > 1e-12:
                    k = EARTH_RADIUS_KM * atan2(horizontal, x * c0 + y * c1 + z * c2) / horizontal
                    q = QPointF(east * k, -north * k)
                else:
                    q = QPointF(0.0, 0.0)
                offsets[kts[i]] = q
            points.append(q)
        if len(offsets) > (hi - lo) + 400:
            keep = set(kts[lo:hi])
            entry[1].clear()
            entry[1].update({k: v for k, v in offsets.items() if k in keep})
        polys = []
        for band in range(6):
            a, b = bounds[band], bounds[band + 1]
            first = a - 1 if a > lo else a
            if b > a and b - first >= 2:
                polys.append((band, QPolygonF(points[first - lo:b - lo])))
        return polys

    def _map_path(self, norm_points, W, cx, cy, c, close=False, anchor=None):
        """Unwrap longitudes and place the path on the world copy nearest the view."""
        pts = []
        prev = None
        for nx, ny in norm_points:
            x = nx * W
            if prev is not None:
                while x - prev > W / 2:
                    x -= W
                while prev - x > W / 2:
                    x += W
            pts.append((x, ny * W))
            prev = x
        if not pts:
            return []
        ref = pts[-1][0] if anchor is None else anchor * W
        shift = -round((ref - cx) / W) * W
        return [QPointF(c.x() + x + shift - cx, c.y() + y - cy) for x, y in pts]

    def _label_text(self, t):
        if t.kind == 'aircraft':
            alt = t.data.get('altitude')
            sub = (f'{alt / 1000:.1f}k ft' if alt >= 1000 else f'{alt:.0f} ft') if alt is not None else ''
        else:
            speed = t.data.get('speed')
            sub = f'{speed:.0f} kn' if speed is not None else ''
        return t.label, sub

    def _layout_labels(self, items, bounds, occupied, font, sub_font):
        """items: (target, point, color, watched, selected, emergency). Returns label placements.

        Emergency, selected and watched targets are placed first; emergency and
        selected targets are always labeled (clamped inside the view if needed).
        """
        fm, fs = QFontMetricsF(font), QFontMetricsF(sub_font)
        show_all = self.scene.get('layers', {}).get('labels', True)
        order = sorted(items, key=lambda row: (not row[5], not row[4], not row[3], row[0].identifier))
        placed = []
        for target, pt, color, watched, selected, emergency in order:
            forced = selected or bool(emergency)
            if not show_all and not forced:
                continue
            title, sub = self._label_text(target)
            if watched:
                title = '★ ' + title
            title = fm.elidedText(title, Qt.TextElideMode.ElideRight, 170)
            w = fm.horizontalAdvance(title) + (fs.horizontalAdvance(sub) + 8 if sub else 0) + 16
            h = 22.0
            x, y = pt.x(), pt.y()
            candidates = [QRectF(x + 15, y - h - 3, w, h), QRectF(x + 15, y + 3, w, h),
                          QRectF(x - w - 15, y - h - 3, w, h), QRectF(x - w - 15, y + 3, w, h),
                          QRectF(x - w / 2, y - h - 16, w, h), QRectF(x - w / 2, y + 16, w, h)]
            rect = next((r for r in candidates if bounds.contains(r) and not occupied.intersects(r)), None)
            if rect is None and forced:
                rect = QRectF(candidates[0])
                rect.moveLeft(max(bounds.left(), min(rect.left(), bounds.right() - w)))
                rect.moveTop(max(bounds.top(), min(rect.top(), bounds.bottom() - h)))
            if rect is None:
                continue
            occupied.append(rect.adjusted(-3, -3, 3, 3))
            placed.append((rect, title, sub, color, watched, selected))
        return placed

    def _draw_labels(self, p, placed, font, sub_font, pal, scope=False):
        for rect, title, sub, color, _watched, selected in placed:
            self._draw_label(p, rect, title, sub, color, selected, font, sub_font, pal, scope)

    def _draw_label(self, p, rect, title, sub, color, selected, font, sub_font, pal, scope):
        path = QPainterPath()
        path.addRoundedRect(rect, 6 if not scope else 2, 6 if not scope else 2)
        bg = QColor(pal['label'])
        bg.setAlpha(235 if selected else 210)
        p.setPen(QPen(_alpha(color, 230 if selected else 110), 1.4 if selected else 1))
        p.setBrush(bg)
        p.drawPath(path)
        p.setPen(QColor(pal['text'] if scope else pal['label_text']))
        p.setFont(font)
        fm = QFontMetricsF(font)
        tw = fm.horizontalAdvance(title)
        p.drawText(QRectF(rect.left() + 8, rect.top(), tw + 1, rect.height()), Qt.AlignmentFlag.AlignVCenter, title)
        if sub:
            p.setFont(sub_font)
            p.setPen(_alpha(color, 255))
            p.drawText(QRectF(rect.left() + 16 + tw, rect.top(), rect.width() - tw - 16, rect.height()),
                       Qt.AlignmentFlag.AlignVCenter, sub)

    # Emergencies ---------------------------------------------------------------
    def _layout_badges(self, items, bounds, occupied, font):
        """Always-shown 'SQUAWK 7700 · EMERGENCY' badges under emergency targets (reserved before labels)."""
        fm = QFontMetricsF(font)
        badges = []
        for _target, pt, _, _, _, code in items:
            if not code:
                continue
            text = emergency_text(code)
            w, h = fm.horizontalAdvance(text) + 18, 19.0
            rect = QRectF(pt.x() - w / 2, pt.y() + 19, w, h)
            rect.moveLeft(max(bounds.left(), min(rect.left(), bounds.right() - w)))
            rect.moveTop(max(bounds.top(), min(rect.top(), bounds.bottom() - h)))
            occupied.append(rect.adjusted(-2, -2, 2, 2))
            badges.append((rect, text))
        return badges

    def _draw_badges(self, p, badges, font, pal):
        if not badges:
            return
        red = QColor(pal['emergency'])
        p.setFont(font)
        for rect, text in badges:
            p.setPen(QPen(_alpha(pal['outline'], 200), 1))
            p.setBrush(red)
            p.drawRoundedRect(rect, rect.height() / 2, rect.height() / 2)
            p.setPen(QColor(pal['emergency_text']))
            p.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)

    def _draw_emergency_ring(self, p, pt, pal, scale=1.0):
        red = QColor(pal['emergency'])
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(_alpha(red, 34))
        p.drawEllipse(pt, 23 * scale, 23 * scale)
        p.setPen(QPen(_alpha(red, 245), 2.6))
        p.setBrush(_alpha(red, 40))
        p.drawEllipse(pt, 16 * scale, 16 * scale)

    def _draw_glyph(self, p, target, pt, color, outline, selected, watched, pal, scale=1.0, alpha=255):
        heading = target.data.get('heading')
        if heading is None:
            heading = target.data.get('course')
        fill = _alpha(color, alpha)
        p.save()
        p.translate(pt)
        if selected:
            p.setPen(QPen(_alpha(pal['selection'], 235), 2))
            p.setBrush(_alpha(color, 45))
            p.drawEllipse(QPointF(0, 0), 17 * scale, 17 * scale)
        p.setPen(QPen(_alpha(outline, min(255, alpha + 20)), 1.3))
        p.setBrush(fill)
        if heading is None:
            if target.kind == 'aircraft':
                p.drawEllipse(QPointF(0, 0), 5.5 * scale, 5.5 * scale)
            else:
                p.drawRect(QRectF(-5 * scale, -5 * scale, 10 * scale, 10 * scale))
        else:
            p.rotate(float(heading))
            p.scale(scale, scale)
            p.drawPath(PLANE if target.kind == 'aircraft' else SHIP)
            p.scale(1 / scale, 1 / scale)
            p.rotate(-float(heading))
        if watched:
            p.translate(10 * scale, -10 * scale)
            p.setPen(QPen(_alpha(outline, 220), 1))
            p.setBrush(QColor(pal['star']))
            p.drawPath(STAR)
        p.restore()

    def _target_color(self, target, now, style):
        pal = PALETTES[style]
        if not target.position_time or now - target.position_time > STALE_SECONDS:
            return QColor(pal['stale'])
        if style == 'Scope':
            return QColor(pal['aircraft' if target.kind == 'aircraft' else 'vessel'])
        if target.kind == 'aircraft':
            return altitude_color(target.data.get('altitude'), style)
        return QColor(pal['vessel'])

    # Heading lines -------------------------------------------------------------
    def _heading_minutes(self):
        try:
            return max(1, min(30, int(self.scene.get('heading_minutes') or 2)))
        except (TypeError, ValueError):
            return 2

    def _heading_state(self, t, now, minutes):
        """Projected track for a fresh, moving target: geodesic minute points, cached per target state."""
        pos = t.position
        if pos is None or not t.position_time or now - t.position_time > STALE_SECONDS:
            return None
        track = target_track(t)
        try:
            speed = float(t.data.get('speed'))
        except (TypeError, ValueError):
            return None
        if track is None or not speed >= 0.5 or not math.isfinite(speed):
            return None
        signature = (pos, track, speed, minutes)
        cached = self._heading_cache.get(t.key)
        if cached is not None and cached[0] == signature:
            return cached[1]
        step = speed * KNOT_KM_H / 60
        geo = [pos] + [destination(pos, step * m, track) for m in range(1, minutes + 1)]
        pts, prev = [], None
        for lat, lon in geo:
            nx, ny = norm(lat, lon)
            if prev is not None:
                while nx - prev > 0.5:
                    nx -= 1.0
                while prev - nx > 0.5:
                    nx += 1.0
            pts.append((nx, ny))
            prev = nx
        xs, ys = [x for x, _ in pts], [y for _, y in pts]
        entry = dict(geo=geo, norm=pts, poly=QPolygonF([QPointF(x, y) for x, y in pts]),
                     bbox=(min(xs), min(ys), max(xs), max(ys)), ticks=None, scope=None)
        self._heading_cache[t.key] = (signature, entry)
        if len(self._heading_cache) > 4 * max(64, len(self.scene.get('targets', []))):
            live = {x.key for x in self.scene.get('targets', [])}
            self._heading_cache = {k: v for k, v in self._heading_cache.items() if k in live}
        return entry

    @staticmethod
    def _tick_segments(points, px_per_unit):
        """Perpendicular minute ticks along a polyline given in any unit; px_per_unit converts to pixels.

        Every minute when ticks are at least ~7 px apart, else every 5 minutes, else none.
        Returns (x0, y0, x1, y1) segments in the input unit.
        """
        if len(points) < 2:
            return []
        first = math.hypot(points[1][0] - points[0][0], points[1][1] - points[0][1]) * px_per_unit
        every = 1 if first >= 7 else 5 if first * 5 >= 7 else 0
        if not every:
            return []
        segments = []
        last = len(points) - 1
        for m in range(every, len(points), every):
            (x0, y0), (x1, y1) = points[m - 1], points[m]
            dx, dy = x1 - x0, y1 - y0
            length = math.hypot(dx, dy) or 1.0
            major = m == last or (every == 1 and m % 5 == 0 and last >= 5)
            half = (5.0 if major else 3.2) / px_per_unit
            ox, oy = -dy / length * half, dx / length * half
            segments.append((x1 - ox, y1 - oy, x1 + ox, y1 + oy))
        return segments

    def _draw_headings_map(self, p, targets, now, W, cx, cy, c, style, selected_key):
        minutes = self._heading_minutes()
        zoom_key = round(self._zoom * 2)
        w, h = self.width(), self.height()
        base = p.transform()
        p.setBrush(Qt.BrushStyle.NoBrush)
        drew = False
        for t in targets:
            entry = self._heading_state(t, now, minutes)
            if entry is None:
                continue
            minx, miny, maxx, maxy = entry['bbox']
            ox = c.x() - cx - round((entry['norm'][0][0] * W - cx) / W) * W
            oy = c.y() - cy
            if maxx * W + ox < -20 or minx * W + ox > w + 20 or maxy * W + oy < -20 or miny * W + oy > h + 20:
                continue
            ticks = entry['ticks']
            if ticks is None or ticks[0] != zoom_key:
                segments = self._tick_segments(entry['norm'], 256 * 2 ** (zoom_key / 2))
                ticks = entry['ticks'] = (zoom_key, [QLineF(*s) for s in segments])
            color = self._target_color(t, now, style)
            selected = t.key == selected_key
            transform = QTransform(base)
            transform.translate(ox, oy)
            transform.scale(W, W)
            p.setTransform(transform)
            drew = True
            # 1 px cosmetic pens take Qt's fast raster path; only the selected target's line is heavier.
            pen = QPen(_alpha(color, 250 if selected else 225), 1.9 if selected else 1.0, Qt.PenStyle.CustomDashLine)
            pen.setDashPattern([4, 3] if selected else [6, 4])
            pen.setCosmetic(True)
            p.setPen(pen)
            p.drawPolyline(entry['poly'])
            if ticks[1]:
                tick_pen = QPen(_alpha(color, 250 if selected else 235), 1.6 if selected else 1.0)
                tick_pen.setCosmetic(True)
                p.setPen(tick_pen)
                p.drawLines(ticks[1])
        if drew:
            p.setTransform(base)

    def _draw_headings_scope(self, p, targets, now, center, radius, rng, selected_key):
        minutes = self._heading_minutes()
        view = (self._lat, self._lon)
        scale = radius / rng
        for t in targets:
            entry = self._heading_state(t, now, minutes)
            if entry is None:
                continue
            cached = entry['scope']
            if cached is None or cached[0] != view:
                offsets = []
                for lat, lon in entry['geo']:
                    d, b = distance_bearing(view, (lat, lon))
                    rad = math.radians(b)
                    offsets.append((d * math.sin(rad), -d * math.cos(rad)))
                cached = entry['scope'] = (view, offsets)
            offsets = cached[1]
            if min(math.hypot(x, y) for x, y in offsets) > rng * 1.02:
                continue
            pts = [(center.x() + x * scale, center.y() + y * scale) for x, y in offsets]
            color = self._target_color(t, now, 'Scope')
            selected = t.key == selected_key
            pen = QPen(_alpha(color, 240 if selected else 200), 1.8 if selected else 1.0, Qt.PenStyle.CustomDashLine)
            pen.setDashPattern([4, 3] if selected else [6, 4])
            pen.setCosmetic(True)
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPolyline(QPolygonF([QPointF(x, y) for x, y in pts]))
            segments = self._tick_segments(pts, 1.0)
            if segments:
                tick_pen = QPen(_alpha(color, 225), 1.5 if selected else 1.0)
                tick_pen.setCosmetic(True)
                p.setPen(tick_pen)
                p.drawLines([QLineF(*s) for s in segments])

    # Coverage -------------------------------------------------------------------
    def _coverage_geo(self):
        coverage = self.scene.get('coverage')
        if not isinstance(coverage, dict) or not coverage.get('home') or not coverage.get('sectors'):
            return None
        try:
            home = (float(coverage['home'][0]), float(coverage['home'][1]))
            sectors = [float(km) if km else 0.0 for km in list(coverage['sectors'])[:36]]
        except (TypeError, ValueError, IndexError):
            return None
        key = (home, tuple(sectors))
        if self._coverage_cache is not None and self._coverage_cache[0] == key:
            return self._coverage_cache[1]
        result = None
        if any(km > 0 for km in sectors):
            geo = []
            span = 360 / len(sectors)
            for i, km in enumerate(sectors):
                if km > 0:
                    geo += [destination(home, km, i * span + span * k / 4) for k in range(5)]
                elif not geo or geo[-1] != home:
                    geo.append(home)
            result = dict(home=home, geo=geo, norm=[norm(*g) for g in geo])
        self._coverage_cache = (key, result)
        return result

    def _draw_coverage(self, p, pts, pal):
        if len(pts) < 3:
            return
        color = QColor(pal['coverage'])
        polygon = QPolygonF(pts)
        # A large translucent fill: skip antialiasing (the 1 px outline smooths the edge), 10x cheaper.
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(_alpha(color, 30))
        p.drawPolygon(polygon)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        pen = QPen(_alpha(color, 190), 1.0)
        pen.setCosmetic(True)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPolygon(polygon)

    # Airports -------------------------------------------------------------------
    def _sprites_for(self, style, dpr):
        key = (style, round(dpr, 3))
        sprites = self._sprites.get(key)
        if sprites is None:
            sprites = self._sprites[key] = dict(symbols=[self._airport_symbol(style, tier, dpr) for tier in range(3)],
                                                labels={})
        return sprites

    @staticmethod
    def _airport_symbol(style, tier, dpr):
        size = 18
        image = QImage(max(1, round(size * dpr)), max(1, round(size * dpr)), QImage.Format.Format_ARGB32_Premultiplied)
        image.setDevicePixelRatio(dpr)
        image.fill(Qt.GlobalColor.transparent)
        pal = PALETTES[style]
        qp = QPainter(image)
        qp.setRenderHint(QPainter.RenderHint.Antialiasing)
        qp.translate(size / 2, size / 2)
        ink = QColor(pal['airport'] if tier == airports.MAJOR else pal['airport_minor'])
        halo = QPen(_alpha(pal['face'] if style == 'Scope' else pal['background'], 190), 4.0)
        halo.setCapStyle(Qt.PenCapStyle.RoundCap)
        fill = QColor(pal['face'] if style == 'Scope' else pal['label'])
        if style == 'Scope':
            qp.setPen(QPen(ink, 1.3))
            qp.setBrush(fill)
            qp.drawRect(QRectF(-3.5, -3.5, 7, 7))
        elif tier == airports.MAJOR:
            # Chart-style "airport with services": ring with four ticks.
            for pen in (halo, QPen(ink, 1.7)):
                pen.setCapStyle(Qt.PenCapStyle.FlatCap)
                qp.setPen(pen)
                qp.setBrush(fill)
                qp.drawEllipse(QPointF(0, 0), 4.0, 4.0)
                for a in range(4):
                    rad = math.radians(a * 90)
                    qp.drawLine(QPointF(math.sin(rad) * 4.2, -math.cos(rad) * 4.2),
                                QPointF(math.sin(rad) * 7.2, -math.cos(rad) * 7.2))
        elif tier == airports.OTHER:
            for pen in (halo, QPen(ink, 1.5)):
                qp.setPen(pen)
                qp.setBrush(fill)
                qp.drawEllipse(QPointF(0, 0), 3.3, 3.3)
        else:
            for pen in (halo, QPen(ink, 1.2)):
                qp.setPen(pen)
                qp.setBrush(fill)
                qp.drawEllipse(QPointF(0, 0), 5.0, 5.0)
            qp.setPen(QPen(ink, 1.3))
            qp.drawLine(QPointF(-1.9, -2.5), QPointF(-1.9, 2.5))
            qp.drawLine(QPointF(1.9, -2.5), QPointF(1.9, 2.5))
            qp.drawLine(QPointF(-1.9, 0), QPointF(1.9, 0))
        qp.end()
        return image

    def _airport_label(self, sprites, code, tier, style, dpr):
        key = (code, tier)
        image = sprites['labels'].get(key)
        if image is not None:
            return image
        if len(sprites['labels']) > 4000:
            sprites['labels'].clear()
        pal = PALETTES[style]
        scope = style == 'Scope'
        font = QFont('Consolas' if scope else 'Segoe UI')
        font.setPixelSize(11 if tier == airports.MAJOR else 10)
        font.setWeight(QFont.Weight.Bold if scope or tier == airports.MAJOR else QFont.Weight.DemiBold)
        fm = QFontMetricsF(font)
        pad = 2.0
        w = math.ceil(fm.horizontalAdvance(code) + pad * 2 + 1)
        h = math.ceil(fm.ascent() + fm.descent() + pad)
        image = QImage(max(1, round(w * dpr)), max(1, round(h * dpr)), QImage.Format.Format_ARGB32_Premultiplied)
        image.setDevicePixelRatio(dpr)
        image.fill(Qt.GlobalColor.transparent)
        qp = QPainter(image)
        qp.setRenderHint(QPainter.RenderHint.Antialiasing)
        path = QPainterPath()
        path.addText(pad, pad / 2 + fm.ascent(), font, code)
        halo = QPen(_alpha(pal['face'] if scope else pal['background'], 225), 3.2)
        halo.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        qp.strokePath(path, halo)
        text = pal['airport_text'] if tier == airports.MAJOR else pal['airport_minor']
        qp.fillPath(path, QColor(text))
        qp.end()
        sprites['labels'][key] = image
        return image

    def _draw_airport_points(self, p, points, style, occupied, dpr, label_tiers, top_rank=0, spacing=AIRPORT_SPACING,
                             fits=None, dim_unlabeled=False):
        """points: (x, y, index) in rank order. Declutters, draws symbols and labels; returns hit points.

        label_tiers are labeled where space allows; so are airports ranked below top_rank. fits(rect)
        optionally rejects label positions (e.g. outside the scope face).
        """
        index = airports.get()
        sprites = self._sprites_for(style, dpr)
        symbols = sprites['symbols']
        half = symbols[0].width() / dpr / 2
        tiers, codes = index.tiers, index.codes
        used = set()
        hits = []
        for x, y, i in points:
            gx, gy = int(x // spacing), int(y // spacing)
            if (gx, gy) in used:
                continue
            used.update(((gx - 1, gy - 1), (gx, gy - 1), (gx + 1, gy - 1), (gx - 1, gy), (gx, gy), (gx + 1, gy),
                         (gx - 1, gy + 1), (gx, gy + 1), (gx + 1, gy + 1)))
            tier = tiers[i]
            hits.append((x, y, i))
            label = rect = None
            if tier in label_tiers or (top_rank and i < top_rank):
                label = self._airport_label(sprites, codes[i], tier, style, dpr)
                lw, lh = label.width() / dpr, label.height() / dpr
                rect = QRectF(x + 7, y - lh / 2, lw, lh)
                if occupied.intersects(rect) or (fits and not fits(rect)):
                    rect = QRectF(x - 7 - lw, y - lh / 2, lw, lh)
                    if occupied.intersects(rect) or (fits and not fits(rect)):
                        rect = None
            if rect is None and dim_unlabeled:
                p.setOpacity(0.5)  # unlabeled symbols recede so the labeled, busier airports stand out
                p.drawImage(QPointF(x - half, y - half), symbols[tier])
                p.setOpacity(1.0)
                continue
            p.drawImage(QPointF(x - half, y - half), symbols[tier])
            if rect is not None:
                occupied.append(rect)
                p.drawImage(rect.topLeft(), label)
        return hits

    def _draw_airports_map(self, p, W, cx, cy, c, style, occupied, dpr):
        index = airports.get()
        zoom = self._zoom
        if index is None or zoom < airports.MIN_ZOOM[0] - 1e-6:
            return []
        w, h = self.width(), self.height()
        margin = 12
        north, west = self.coordinate(-margin, -margin)
        south, east = self.coordinate(w + margin, h + margin)
        nx, ny = index.nx, index.ny
        ox, oy = c.x(), c.y() - cy
        points = []
        for tier in range(3):
            if zoom < airports.MIN_ZOOM[tier] - 1e-6:
                break
            for i in index.query(tier, south, north, west, east):
                dx = nx[i] * W - cx
                dx -= round(dx / W) * W
                x, y = ox + dx, ny[i] * W + oy
                if -margin <= x <= w + margin and -margin <= y <= h + margin:
                    points.append((x, y, i))
        label_tiers = {tier for tier in range(3) if zoom >= AIRPORT_LABEL_ZOOM[tier] - 1e-6}
        spacing = AIRPORT_SPACING if zoom >= 8 else AIRPORT_SPACING * 1.4
        return self._draw_airport_points(p, points, style, occupied, dpr, label_tiers, AIRPORT_TOP_RANK, spacing,
                                         dim_unlabeled=zoom < 8)

    def _draw_airports_scope(self, p, center, radius, rng, occupied, dpr):
        """Major airports inside the scope range as small green squares with their code."""
        index = airports.get()
        if index is None or rng > 1500:
            return []
        lat0, lon0 = self._lat, self._lon
        dlat = rng / 110.574 * 1.05
        dlon = rng / (111.32 * max(0.02, math.cos(math.radians(min(89.0, abs(lat0) + dlat))))) * 1.05
        if dlon >= 180:
            west, east = -180.0, 180.0
        else:
            west, east = (lon0 - dlon + 180) % 360 - 180, (lon0 + dlon + 180) % 360 - 180
        view = (lat0, lon0)
        lats, lons = index.lats, index.lons
        points = []
        for i in index.query(airports.MAJOR, lat0 - dlat, lat0 + dlat, west, east):
            d, b = distance_bearing(view, (lats[i], lons[i]))
            if d > rng:
                continue
            r = d / rng * radius
            rad = math.radians(b)
            points.append((center.x() + math.sin(rad) * r, center.y() - math.cos(rad) * r, i))
        cx0, cy0, limit = center.x(), center.y(), (radius - 2) ** 2

        def inside(rect):
            return all((x - cx0) ** 2 + (y - cy0) ** 2 <= limit
                       for x in (rect.left(), rect.right()) for y in (rect.top(), rect.bottom()))
        return self._draw_airport_points(p, points, 'Scope', occupied, dpr, {airports.MAJOR}, fits=inside)

    # Map mode -----------------------------------------------------------------
    def _paint_map(self, p):
        style = self.style_name()
        pal = PALETTES[style]
        w, h = self.width(), self.height()
        W = self._world()
        cx, cy = self._center_px()
        c = self._view_rect().center()
        now = self._now()
        layers = self.scene.get('layers', {})
        p.fillRect(QRectF(0, 0, w, h), QColor(pal['background']))
        drew_tiles = self.scene.get('tiles') and self.tiles is not None and self._draw_tiles(p, W, cx, cy, c, style)
        self._draw_grid(p, W, cx, cy, c, pal, faint=bool(drew_tiles))
        occupied = _Occupancy()
        self._draw_zones(p, W, cx, cy, c, pal, occupied)
        home = self.scene.get('home')
        if home and layers.get('rings', True):
            self._draw_rings(p, W, cx, cy, c, pal, occupied)
        if layers.get('coverage', False):
            coverage = self._coverage_geo()
            if coverage:
                self._draw_coverage(p, self._map_path(coverage['norm'], W, cx, cy, c, anchor=norm(*coverage['home'])[0]),
                                    pal)
        history = self.scene.get('history') or []
        if history:
            pts = self._map_path([norm(la, lo) for la, lo in history], W, cx, cy, c)
            self._draw_history(p, pts, pal)
        targets = self.scene.get('targets', [])
        selected_key = self.scene.get('selected', '')
        watched = self.scene.get('watched', set())
        # Place target glyphs, emergency badges and labels first so airports never take their space.
        visible, hits = [], []
        margin = 40
        for t in targets:
            if not t.position:
                continue
            pt = self.screen(*t.position)
            if not (-margin <= pt.x() <= w + margin and -margin <= pt.y() <= h + margin):
                continue
            is_watched = bool(watched.intersection((t.identifier.casefold(), str(t.data.get('registration', '')).casefold())))
            visible.append((t, pt, self._target_color(t, now, style), is_watched, t.key == selected_key,
                            emergency_code(t)))
            hits.append((t.key, pt.x(), pt.y()))
        visible.sort(key=lambda r: (bool(r[5]), r[4], r[3]))  # emergency, selected and watched draw on top
        for row in visible:
            pt = row[1]
            occupied.append(QRectF(pt.x() - 12, pt.y() - 12, 24, 24))
        font = QFont('Segoe UI', 9)
        font.setWeight(QFont.Weight.DemiBold)
        sub_font = QFont('Segoe UI', 8)
        badge_font = QFont('Segoe UI', 8)
        badge_font.setWeight(QFont.Weight.Bold)
        bounds = self._view_rect().adjusted(6, 6, -6, -6)
        badges = self._layout_badges(visible, bounds, occupied, badge_font)
        placed = self._layout_labels(visible, bounds, occupied, font, sub_font)
        if layers.get('airports', True):
            self._airport_hits = self._draw_airports_map(p, W, cx, cy, c, style, occupied, self._dpr())
        else:
            self._airport_hits = []
        if layers.get('trails', True):
            self._trail_deadline = time.perf_counter() + TRAIL_REBUILD_BUDGET
            for t in targets:
                self._draw_trail_fast(p, t, now, W, cx, cy, c, self._target_color(t, now, style),
                                      t.key == selected_key)
        if layers.get('headings', False):
            self._draw_headings_map(p, targets, now, W, cx, cy, c, style, selected_key)
        if home:
            self._draw_home(p, self.screen(*home), pal)
        outline = QColor(pal['outline'])
        for t, pt, color, is_watched, sel, code in visible:
            if code:
                self._draw_emergency_ring(p, pt, pal)
            self._draw_glyph(p, t, pt, color, outline, sel, is_watched, pal)
        self._draw_labels(p, placed, font, sub_font, pal)
        self._draw_badges(p, badges, badge_font, pal)
        self._draw_draft(p, pal)
        self._draw_scale(p, pal)
        if self.scene.get('tiles'):
            self._draw_attribution(p, pal)
        self._hits = hits

    def _draw_tiles(self, p, W, cx, cy, c, style):
        w, h = self.width(), self.height()
        tz = int(max(0, min(TILE_MAX_ZOOM, round(self._zoom))))
        n = 2 ** tz
        ts = W / n
        left, top = cx - c.x(), cy - c.y()
        x0, x1 = math.floor(left / ts), math.floor((left + w) / ts)
        y0, y1 = max(0, math.floor(top / ts)), min(n - 1, math.floor((top + h) / ts))
        night = style == 'Dark'
        if abs(ts - 256) > 0.5:
            p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        order = sorted(((tx, ty) for ty in range(y0, y1 + 1) for tx in range(x0, x1 + 1)),
                       key=lambda k: (k[0] + .5 - (left + w / 2) / ts) ** 2 + (k[1] + .5 - (top + h / 2) / ts) ** 2)
        drew = False
        for tx, ty in order:
            rect = QRectF(tx * ts - left, ty * ts - top, ts + 0.6, ts + 0.6)
            image = self.tiles.get(tz, tx % n, ty, night)
            if image is not None:
                p.drawImage(rect, image)
                drew = True
                continue
            # Show a scaled ancestor until the tile arrives.
            for dz in range(1, 5):
                if tz - dz < 0:
                    break
                parent = self.tiles.peek(tz - dz, (tx % n) >> dz, ty >> dz, night)
                if parent is not None:
                    f = 2 ** dz
                    sub = 256 / f
                    source = QRectF(((tx % n) % f) * sub, (ty % f) * sub, sub, sub)
                    p.drawImage(rect, parent, source)
                    drew = True
                    break
        return drew

    def _draw_grid(self, p, W, cx, cy, c, pal, faint=False):
        w, h = self.width(), self.height()
        deg_px = W / 360
        step = next((s for s in (0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 15, 30, 45) if s * deg_px >= 120), 45)
        color = QColor(pal['grid'])
        if faint:
            color.setAlpha(70)
        p.setPen(QPen(color, 1))
        left = (cx - c.x()) / W * 360 - 180
        right = left + w / deg_px
        top_lat, _ = denorm(0, max(0.0, (cy - c.y()) / W))
        bottom_lat, _ = denorm(0, min(1.0, (cy - c.y() + h) / W))
        text_color = QColor(pal['grid_text'])
        if faint:
            text_color.setAlpha(150)
        font = QFont('Segoe UI', 7)
        p.setFont(font)
        lon = math.floor(left / step) * step
        labels = []
        while lon <= right + step:
            x = (lon + 180) / 360 * W - cx + c.x()
            p.drawLine(QPointF(x, 0), QPointF(x, h))
            wrapped = (lon + 180) % 360 - 180
            labels.append((QRectF(x + 4, h - 18, 70, 14), f'{abs(wrapped):.{self._decimals(step)}f}°{"E" if wrapped >= 0 else "W"}'))
            lon += step
        lat = math.floor(bottom_lat / step) * step
        while lat <= top_lat + step:
            if -85 < lat < 85:
                y = norm(lat, 0)[1] * W - cy + c.y()
                p.drawLine(QPointF(0, y), QPointF(w, y))
                labels.append((QRectF(self._insets[0] + 6, y - 15, 70, 14),
                               f'{abs(lat):.{self._decimals(step)}f}°{"N" if lat >= 0 else "S"}'))
            lat += step
        p.setPen(text_color)
        for rect, text in labels:
            p.drawText(rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, text)

    @staticmethod
    def _decimals(step):
        return 0 if step >= 1 else 1 if step >= 0.5 else 2

    def _ring_norms(self, home, km):
        key = (tuple(home), round(km, 6))
        cached = self._ring_cache.get(key)
        if cached is None:
            cached = [norm(*destination(home, km, b)) for b in range(0, 360, 3)]
            if len(self._ring_cache) > 64:
                self._ring_cache.clear()
            self._ring_cache[key] = cached
        return cached

    def _draw_rings(self, p, W, cx, cy, c, pal, occupied):
        home = self.scene['home']
        anchor = norm(*home)[0]
        font = QFont('Segoe UI', 8)
        font.setWeight(QFont.Weight.DemiBold)
        fm = QFontMetricsF(font)
        pen = QPen(_alpha(pal['ring'], 150), 1.1, Qt.PenStyle.CustomDashLine)
        pen.setDashPattern([5, 5])
        for km, text in self.scene.get('rings', []):
            pts = self._map_path(self._ring_norms(home, km), W, cx, cy, c, anchor=anchor)
            if len(pts) < 3:
                continue
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPolygon(QPolygonF(pts))
            label_pt = pts[15]  # 45 degrees
            tw = fm.horizontalAdvance(text) + 10
            rect = QRectF(label_pt.x() - tw / 2, label_pt.y() - 9, tw, 18)
            if rect.width() < 200:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(_alpha(pal['background'], 200))
                p.drawRoundedRect(rect, 9, 9)
                p.setPen(QColor(pal['ring_text']))
                p.setFont(font)
                p.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)
                occupied.append(rect)

    def _draw_home(self, p, pt, pal):
        p.save()
        p.translate(pt)
        p.setPen(QPen(_alpha(pal['home'], 90), 1))
        p.setBrush(_alpha(pal['home'], 28))
        p.drawEllipse(QPointF(0, 0), 13, 13)
        p.setPen(QPen(QColor(pal['outline']), 1.5))
        p.setBrush(QColor(pal['home']))
        path = QPainterPath()
        path.moveTo(0, -7)
        path.lineTo(6, -1)
        path.lineTo(4.5, -1)
        path.lineTo(4.5, 6)
        path.lineTo(-4.5, 6)
        path.lineTo(-4.5, -1)
        path.lineTo(-6, -1)
        path.closeSubpath()
        p.drawPath(path)
        p.restore()

    def _draw_zones(self, p, W, cx, cy, c, pal, occupied, scope=False):
        zones = [(z['name'], z['points'], False) for z in self.scene.get('zones', [])]
        draft = self.scene.get('draft') or []
        if len(draft) >= 3:
            zones.append((self.scene.get('draft_name') or 'New geofence', draft, True))
        font = QFont('Segoe UI', 8)
        font.setWeight(QFont.Weight.DemiBold)
        for name, points, is_draft in zones:
            color = QColor(pal['draft' if is_draft else 'zone'])
            if scope:
                pts = [self.screen(la, lo) for la, lo in points]
            else:
                pts = self._map_path([norm(la, lo) for la, lo in points], W, cx, cy, c, anchor=norm(*points[0])[0])
            if len(pts) < 3:
                continue
            pen = QPen(_alpha(color, 220), 1.8, Qt.PenStyle.CustomDashLine if is_draft else Qt.PenStyle.SolidLine)
            if is_draft:
                pen.setDashPattern([4, 3])
            p.setPen(pen)
            p.setBrush(_alpha(color, 34 if not is_draft else 44))
            p.drawPolygon(QPolygonF(pts))
            sx = sum(pt.x() for pt in pts) / len(pts)
            sy = sum(pt.y() for pt in pts) / len(pts)
            fm = QFontMetricsF(font)
            tw = min(180.0, fm.horizontalAdvance(name) + 14)
            rect = QRectF(sx - tw / 2, sy - 10, tw, 20)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(_alpha(color, 200))
            p.drawRoundedRect(rect, 10, 10)
            p.setPen(QColor('#ffffff' if not scope else '#021008'))
            p.setFont(font)
            p.drawText(rect, Qt.AlignmentFlag.AlignCenter, fm.elidedText(name, Qt.TextElideMode.ElideRight, tw - 10))
            occupied.append(rect)

    def _draw_history(self, p, pts, pal):
        if len(pts) < 1:
            return
        color = QColor(pal['history'])
        pen = QPen(_alpha(color, 230), 2.2, Qt.PenStyle.CustomDashLine)
        pen.setDashPattern([4, 2.5])
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        path = QPainterPath(pts[0])
        for pt in pts[1:]:
            path.lineTo(pt)
        p.drawPath(path)
        p.setPen(QPen(QColor(pal['outline']), 1.5))
        p.setBrush(QColor(pal['background']))
        p.drawEllipse(pts[0], 4.5, 4.5)
        p.setBrush(color)
        p.drawEllipse(pts[-1], 5.5, 5.5)

    def _draw_draft(self, p, pal):
        if not self.drawing:
            return
        color = QColor(pal['draft'])
        pts = [self.screen(la, lo) for la, lo in self.vertices]
        if self.style_name() != 'Scope' and pts:
            # Keep consecutive vertices on the same world copy.
            W = self._world()
            for i in range(1, len(pts)):
                while pts[i].x() - pts[i - 1].x() > W / 2:
                    pts[i].setX(pts[i].x() - W)
                while pts[i - 1].x() - pts[i].x() > W / 2:
                    pts[i].setX(pts[i].x() + W)
        if len(pts) >= 3:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(_alpha(color, 36))
            p.drawPolygon(QPolygonF(pts))
        p.setPen(QPen(_alpha(color, 235), 2))
        for i in range(1, len(pts)):
            p.drawLine(pts[i - 1], pts[i])
        if pts and self._hover_geo:
            hover = self.screen(*self._hover_geo)
            pen = QPen(_alpha(color, 180), 1.4, Qt.PenStyle.DashLine)
            p.setPen(pen)
            p.drawLine(pts[-1], hover)
            if len(pts) >= 2:
                p.drawLine(hover, pts[0])
        p.setPen(QPen(QColor(pal['outline']), 2))
        for i, pt in enumerate(pts):
            p.setBrush(QColor('#ffffff') if i == 0 else color)
            p.drawEllipse(pt, 5 if i == 0 else 4.2, 5 if i == 0 else 4.2)

    def _draw_scale(self, p, pal):
        units = self.scene.get('units', 'mi')
        km_per_px = EARTH_KM * math.cos(math.radians(self._lat)) / self._world()
        value = nice_distance(110 * km_per_px / UNIT_KM[units])
        if value <= 0:
            return
        length = value * UNIT_KM[units] / km_per_px
        rect = self._view_rect()
        x, y = rect.left() + 18, rect.bottom() - 18
        color = QColor(pal['scale'])
        p.setPen(QPen(_alpha(pal['background'], 170), 5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawLine(QPointF(x, y), QPointF(x + length, y))
        p.setPen(QPen(color, 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawLine(QPointF(x, y), QPointF(x + length, y))
        p.drawLine(QPointF(x, y - 5), QPointF(x, y + 1))
        p.drawLine(QPointF(x + length, y - 5), QPointF(x + length, y + 1))
        font = QFont('Segoe UI', 8)
        font.setWeight(QFont.Weight.DemiBold)
        p.setFont(font)
        p.drawText(QRectF(x, y - 22, max(80.0, length), 16), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   f'{distance_text(value)} {units}')

    def _draw_attribution(self, p, pal):
        font = QFont('Segoe UI', 7)
        p.setFont(font)
        text = '© OpenStreetMap contributors'
        tw = QFontMetricsF(font).horizontalAdvance(text) + 12
        rect = self._view_rect()
        box = QRectF(rect.right() - tw - 8, rect.bottom() - 22, tw, 16)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(_alpha(pal['background'], 190))
        p.drawRoundedRect(box, 4, 4)
        p.setPen(QColor(pal['label_muted']))
        p.drawText(box, Qt.AlignmentFlag.AlignCenter, text)

    # Scope mode ---------------------------------------------------------------
    def _scope_key(self):
        return (round(self.width()), round(self.height()), round(self._zoom, 4), round(self._lat, 7),
                round(self._lon, 7), tuple(self._insets), self._dpr(), id(self.scene), int(time.time()),
                self.drawing, len(self.vertices), self._hover_geo)

    def _paint_scope(self, p):
        pal = PALETTES['Scope']
        w, h = self.width(), self.height()
        key = self._scope_key()
        if self._scope_cache is None or self._scope_cache[0] != key:
            self._build_scope(key, pal)
        p.drawImage(QRectF(0, 0, w, h), self._scope_cache[1])
        center, radius, _range = self._scope_geometry()
        angle = self.sweep_angle()
        # Cosmetic sweep: it never changes what is tracked or how fresh it is.
        clip = QPainterPath()
        clip.addEllipse(center, radius, radius)
        p.save()
        p.setClipPath(clip)
        gradient = QConicalGradient(center, 90 - angle)
        gradient.setColorAt(0.0, QColor(90, 255, 150, 70))
        gradient.setColorAt(0.04, QColor(70, 230, 125, 38))
        gradient.setColorAt(0.18, QColor(40, 160, 80, 0))
        gradient.setColorAt(1.0, QColor(40, 160, 80, 0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(gradient))
        p.drawEllipse(center, radius, radius)
        rad = math.radians(angle)
        end = QPointF(center.x() + math.sin(rad) * radius, center.y() - math.cos(rad) * radius)
        p.setPen(QPen(QColor(110, 255, 165, 50), 5))
        p.drawLine(center, end)
        p.setPen(QPen(QColor(170, 255, 200, 190), 1.3))
        p.drawLine(center, end)
        p.restore()
        # Blips glow brightest just after the sweep passes their bearing.
        outline = QColor(pal['outline'])
        for t, pt, color, bearing, watched, selected, code in self._scope_blips:
            since = (angle - bearing) % 360
            glow = 0.38 + 0.62 * max(0.0, 1 - since / 300)
            if selected or code:
                glow = max(glow, 0.85)
            halo = QRadialGradient(pt, 14)
            halo.setColorAt(0, _alpha(color, 110 * glow))
            halo.setColorAt(1, _alpha(color, 0))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(halo))
            p.drawEllipse(pt, 14, 14)
            if code:
                self._draw_emergency_ring(p, pt, pal, scale=0.9)
            self._draw_glyph(p, t, pt, color, outline, selected, watched, pal, scale=0.85, alpha=int(90 + 165 * glow))
        self._draw_badges(p, *self._scope_badges, pal)

    def _build_scope(self, key, pal):
        w, h = self.width(), self.height()
        dpr = key[6]
        image = QImage(max(1, int(w * dpr)), max(1, int(h * dpr)), QImage.Format.Format_ARGB32_Premultiplied)
        image.setDevicePixelRatio(dpr)
        image.fill(QColor(pal['background']))
        p = QPainter(image)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        center, radius, rng = self._scope_geometry()
        units = self.scene.get('units', 'mi')
        layers = self.scene.get('layers', {})
        # Face
        glow = QRadialGradient(center, radius)
        glow.setColorAt(0, QColor('#082a15'))
        glow.setColorAt(0.75, QColor('#04180b'))
        glow.setColorAt(1, QColor('#020c06'))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(glow))
        p.drawEllipse(center, radius, radius)
        p.setBrush(Qt.BrushStyle.NoBrush)
        for bearing in range(0, 360, 30):
            rad = math.radians(bearing)
            p.setPen(QPen(QColor('#0f3a20'), 1))
            p.drawLine(QPointF(center.x() + math.sin(rad) * 8, center.y() - math.cos(rad) * 8),
                       QPointF(center.x() + math.sin(rad) * (radius - 2), center.y() - math.cos(rad) * (radius - 2)))
        mono = QFont('Consolas', 8)
        ring_labels = []
        if layers.get('rings', True):
            p.setFont(mono)
            for fraction in (0.25, 0.5, 0.75):
                rr = radius * fraction
                pen = QPen(QColor(pal['ring']), 1, Qt.PenStyle.CustomDashLine)
                pen.setDashPattern([2, 4])
                p.setPen(pen)
                p.drawEllipse(center, rr, rr)
                if radius >= 110:
                    rad = math.radians(135)
                    pt = QPointF(center.x() + math.sin(rad) * rr, center.y() - math.cos(rad) * rr)
                    text = f'{distance_text(rng * fraction / UNIT_KM[units])} {units}'
                    rect = QRectF(pt.x() + 3, pt.y() - 1, 64, 14)
                    p.setPen(QColor(pal['ring_text']))
                    p.drawText(rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, text)
                    ring_labels.append(QRectF(rect.left(), rect.top(), QFontMetricsF(mono).horizontalAdvance(text) + 2,
                                              rect.height()))
        for bearing in range(0, 360, 5):
            rad = math.radians(bearing)
            major = bearing % 30 == 0
            p.setPen(QPen(QColor(pal['tick'] if major else pal['tick_minor']), 1.2 if major else 1))
            inner, outer = radius + 3, radius + (10 if major else 6)
            p.drawLine(QPointF(center.x() + math.sin(rad) * inner, center.y() - math.cos(rad) * inner),
                       QPointF(center.x() + math.sin(rad) * outer, center.y() - math.cos(rad) * outer))
        p.setPen(QPen(QColor('#2f8a52'), 1.4))
        p.drawEllipse(center, radius, radius)
        p.setPen(QPen(QColor('#0e3119'), 1))
        p.drawEllipse(center, radius + 14, radius + 14)
        label_font = QFont('Consolas', 9)
        label_font.setWeight(QFont.Weight.Bold)
        small = QFont('Consolas', 7)
        for bearing in range(0, 360, 30):
            rad = math.radians(bearing)
            pt = QPointF(center.x() + math.sin(rad) * (radius + 25), center.y() - math.cos(rad) * (radius + 25))
            cardinal = {0: 'N', 90: 'E', 180: 'S', 270: 'W'}.get(bearing)
            if cardinal is None and radius < 150:
                continue
            p.setFont(label_font if cardinal else small)
            p.setPen(QColor(pal['text'] if cardinal else pal['ring_text']))
            p.drawText(QRectF(pt.x() - 16, pt.y() - 9, 32, 18), Qt.AlignmentFlag.AlignCenter, cardinal or f'{bearing:03d}')
        p.setPen(QPen(QColor('#58b077'), 1))
        p.drawLine(center + QPointF(-6, 0), center + QPointF(6, 0))
        p.drawLine(center + QPointF(0, -6), center + QPointF(0, 6))
        # Content clipped to the scope face.
        clip = QPainterPath()
        clip.addEllipse(center, radius, radius)
        p.setClipPath(clip)
        occupied = _Occupancy()
        for rect in ring_labels:
            occupied.append(rect)
        self._draw_zones(p, 0, 0, 0, center, pal, occupied, scope=True)
        if layers.get('coverage', False):
            coverage = self._coverage_geo()
            if coverage:
                self._draw_coverage(p, [self.screen(la, lo) for la, lo in coverage['geo']], pal)
        home = self.scene.get('home')
        now = self._now()
        history = self.scene.get('history') or []
        if history:
            self._draw_history(p, [self.screen(la, lo) for la, lo in history], pal)
        targets = self.scene.get('targets', [])
        selected_key = self.scene.get('selected', '')
        watched_ids = self.scene.get('watched', set())
        blips, hits = [], []
        view_center = (self._lat, self._lon)
        for t in targets:
            if not t.position:
                continue
            d, b = distance_bearing(view_center, t.position)
            if d > rng * 1.02:
                continue
            r = d / rng * radius
            pt = QPointF(center.x() + math.sin(math.radians(b)) * r, center.y() - math.cos(math.radians(b)) * r)
            is_watched = bool(watched_ids.intersection((t.identifier.casefold(), str(t.data.get('registration', '')).casefold())))
            blips.append((t, pt, self._target_color(t, now, 'Scope'), b, is_watched, t.key == selected_key,
                          emergency_code(t)))
            hits.append((t.key, pt.x(), pt.y()))
        for blip in blips:
            pt = blip[1]
            occupied.append(QRectF(pt.x() - 11, pt.y() - 11, 22, 22))
        font = QFont('Consolas', 8)
        font.setWeight(QFont.Weight.Bold)
        sub_font = QFont('Consolas', 8)
        badge_font = QFont('Consolas', 8)
        badge_font.setWeight(QFont.Weight.Bold)
        bounds = QRectF(center.x() - radius, center.y() - radius, radius * 2, radius * 2)
        items = [(t, pt, color, wtc, sel, code) for t, pt, color, _, wtc, sel, code in blips]
        badges = self._layout_badges(items, bounds, occupied, badge_font)
        placed = self._layout_labels(items, bounds, occupied, font, sub_font)
        if layers.get('airports', True):
            self._airport_hits = self._draw_airports_scope(p, center, radius, rng, occupied, dpr)
        else:
            self._airport_hits = []
        if layers.get('trails', True):
            self._trail_deadline = time.perf_counter() + TRAIL_REBUILD_BUDGET
            for t, _, color, _, _, selected, _ in blips:
                polys = self._scope_trail_polys(t, now)
                if polys:
                    p.save()
                    p.translate(center)
                    p.scale(radius / rng, radius / rng)
                    for band, poly in polys:
                        pen = QPen(_alpha(color, TRAIL_ALPHA[band]), 2.6 if selected else TRAIL_WIDTH[band])
                        pen.setCosmetic(True)
                        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
                        pen.setJoinStyle(Qt.PenJoinStyle.BevelJoin)
                        p.setPen(pen)
                        p.drawPolyline(poly)
                    p.restore()
        if layers.get('headings', False):
            self._draw_headings_scope(p, targets, now, center, radius, rng, selected_key)
        if home:
            hp = self.screen(*home)
            p.setPen(QPen(QColor(pal['home']), 1.4))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRect(QRectF(hp.x() - 5, hp.y() - 5, 10, 10))
            p.drawPoint(hp)
        self._draw_draft(p, pal)
        p.setClipping(False)
        self._draw_labels(p, placed, font, sub_font, pal, scope=True)
        p.end()
        blips.sort(key=lambda b: (bool(b[6]), b[5], b[4]))
        self._scope_cache = (key, image)
        self._scope_blips = blips
        self._scope_badges = (badges, badge_font)  # drawn over the live blips every frame
        self._hits = hits
