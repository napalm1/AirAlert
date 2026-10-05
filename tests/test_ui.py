"""Qt Quick interface: loads cleanly and the map responds to real pointer input."""
import json
import logging
import os
import time

import pytest

from airalert.core.models import destination

HOME = [38.4, -122.8]


class QmlLog(logging.Handler):
    def __init__(self):
        super().__init__(logging.WARNING)
        self.records = []

    def emit(self, record):
        self.records.append(record.getMessage())


@pytest.fixture(scope='module')
def ui(tmp_path_factory):
    folder = tmp_path_factory.mktemp('airalert-ui')
    (folder / 'settings.json').write_text(json.dumps(dict(home=HOME, mode='Simulation', setup_done=True,
                                                          sample_seconds=1, theme='Dark')), 'utf-8')
    os.environ['AIRALERT_DATA'] = str(folder)
    capture = QmlLog()
    logging.getLogger('airalert').addHandler(capture)
    from airalert.app import build
    app, engine, controller, window = build(['test'], tray=False, show=True, dialogs=False)
    assert window is not None, capture.records
    window.resize(1400, 880)
    pump(app, 300)
    yield app, controller, window, capture
    controller.shutdown()


def pump(app, ms):
    from PySide6.QtCore import QEventLoop, QTimer
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def wait_until(app, condition, timeout_ms=3000):
    """Pump events until condition() holds (dialogs report 'opened' only after their open animation)."""
    deadline = time.monotonic() + timeout_ms / 1000
    while not condition() and time.monotonic() < deadline:
        pump(app, 20)
    return condition()


def advance(controller, seconds=20):
    station = controller.station
    base = time.time() - seconds * 2
    for i in range(seconds):
        station.sim_time -= 2
        notices, _ = station.tick(now=base + i * 2)
        for n in notices:
            controller._raise_alert(dict(n, sound=False, desktop=False))
    controller.refresh()


def map_point(controller, lat, lon):
    from PySide6.QtCore import QPoint
    item = controller.map
    local = item.screen(lat, lon)
    scene = item.mapToScene(local)
    return QPoint(round(scene.x()), round(scene.y()))


def test_interface_loads_without_qml_warnings(ui):
    app, controller, window, capture = ui
    assert window.property('title') == 'AirAlert'
    assert not [r for r in capture.records if 'QML' in r], capture.records


def test_simulation_populates_list_map_and_alert_feed(ui):
    app, controller, window, capture = ui
    controller.station.save_rule(-1, dict(name='Near', kind='aircraft', field='registration', value='',
                                          condition='enter', threshold=40, cooldown=0, sound=False, desktop=False))
    controller._refresh_rules()
    controller.setMode('Simulation')
    controller.start()
    assert controller.station.running and controller.property('isSimulation')
    advance(controller)
    assert controller.trafficModel.rowCount() == 9
    assert controller.property('aircraftCount') == 5
    assert controller.alertFeed.rowCount() >= 1
    assert controller.property('unreadAlerts') >= 1
    controller.setSearch('N123AB')
    assert controller.trafficModel.rowCount() == 1
    controller.setSearch('')
    assert controller.trafficModel.rowCount() == 9
    controller.trafficModel.sortBy('altitude')
    first = controller.trafficModel.get(0)
    assert first['kind'] == 'aircraft'


def test_click_marker_selects_and_background_clears(ui):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    app, controller, window, capture = ui
    target = controller.station.store.targets['aircraft:A00001']
    controller.map.centerOn(*target.position)
    window.grabWindow()  # render once so hit targets exist
    QTest.mouseClick(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                     map_point(controller, *target.position))
    pump(app, 50)
    assert controller.property('selectedKey') == 'aircraft:A00001'
    details = controller.property('details')
    assert details['title'] == 'N123AB' and details['mode'] == 'target'
    window.grabWindow()
    # An open patch of map near the view center with no target under it.
    far = next(p for p in (destination(target.position, 12, b) for b in range(0, 360, 20))
               if not controller.map._hit(controller.map.screen(*p)))
    QTest.mouseClick(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, map_point(controller, *far))
    pump(app, 50)
    assert controller.property('selectedKey') == ''


def test_drag_pans_and_wheel_zooms_around_pointer(ui):
    from PySide6.QtCore import QPoint, QPointF, Qt
    from PySide6.QtGui import QWheelEvent
    from PySide6.QtTest import QTest
    app, controller, window, capture = ui
    item = controller.map
    controller.setMapTheme('Follow app')
    item.centerOnZoom(HOME[0], HOME[1], 9)
    start = map_point(controller, *HOME)
    QTest.mousePress(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start)
    for step in range(1, 6):
        QTest.mouseMove(window, start + QPoint(step * 12, step * 6), 10)
    QTest.mouseRelease(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start + QPoint(60, 30))
    moved = map_point(controller, *HOME)
    assert abs(moved.x() - start.x() - 60) <= 1 and abs(moved.y() - start.y() - 30) <= 1
    anchor = QPointF(moved.x() + 80, moved.y() - 40)
    geo_before = item.coordinate(*[v - o for v, o in zip((anchor.x(), anchor.y()), _offset(item))])
    event = QWheelEvent(anchor, anchor, QPoint(), QPoint(0, 240), Qt.MouseButton.NoButton,
                        Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False)
    app.sendEvent(window, event)
    pump(app, 400)
    assert item.property('zoom') == pytest.approx(10.0)
    after = item.screen(*geo_before)
    ox, oy = _offset(item)
    assert abs(after.x() + ox - anchor.x()) < 1.5 and abs(after.y() + oy - anchor.y()) < 1.5


def _offset(item):
    p = item.mapToScene(item.position() * 0)
    return p.x(), p.y()


def test_geofence_drawn_with_mouse_then_named(ui):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    app, controller, window, capture = ui
    controller.map.centerOnZoom(HOME[0], HOME[1], 10)
    controller.beginZone()
    assert controller.property('zoneMode') == 'draw'
    for bearing in (0, 120, 240):
        lat, lon = destination(HOME, 6, bearing)
        QTest.mouseClick(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                         map_point(controller, lat, lon))
    pump(app, 50)
    assert controller.map.property('vertexCount') == 3
    controller.finishZone()
    assert controller.property('zoneMode') == 'create'
    controller.commitZone('Home triangle')
    assert controller.station.zone('Home triangle') is not None
    assert controller.property('zoneMode') == ''
    controller.editZone('Home triangle', 'rename')
    controller.commitZone('Station')
    controller.editZone('Station', 'remove')
    controller.commitZone('')
    assert controller.config['geofences'] == []


def test_scope_mode_renders_and_restores(ui):
    app, controller, window, capture = ui
    controller.setMapTheme('Scope only')
    assert controller.map.property('styleName') == 'Scope'
    assert controller.map.property('rangeText').endswith('mi')
    assert not window.grabWindow().isNull()
    controller.setMapTheme('Follow app')
    assert controller.map.property('styleName') == 'Dark'


def test_history_track_and_theme(ui):
    app, controller, window, capture = ui
    controller.searchHistory({'category': 'sightings'})
    assert controller.historyModel.rowCount() == 9
    controller.openTrack(0)
    assert controller.property('details')['mode'] == 'history'
    controller.clearSelection()
    controller.setTheme('Light')
    assert controller.property('theme') == 'Light'
    assert not window.grabWindow().isNull()
    controller.setTheme('Dark')


def test_settings_while_running_lock_only_receiver_fields(ui):
    app, controller, window, capture = ui
    if not controller.station.running:
        controller.start()
    running_form = controller.settingsData()
    assert running_form['running'] is True
    assert controller.saveSettings(dict(running_form, quietEnabled=True, quietStart='23:00', quietEnd='6:30')) == ''
    assert controller.config['quiet_hours'] == dict(enabled=True, start='23:00', end='06:30')
    assert 'Stop monitoring' in controller.saveSettings(dict(running_form, mode='Aircraft'))
    assert 'Stop monitoring' in controller.saveSettings(dict(running_form, gain='30'))
    assert 'HH:MM' in controller.saveSettings(dict(running_form, quietStart='25:00'))
    assert 'ntfy topic' in controller.saveSettings(dict(running_form, ntfy_enabled=True, ntfy_topic=''))
    assert controller.saveSettings(dict(running_form, ntfy_enabled=True, ntfy_topic='airalert-x1')) == ''
    assert controller.config['phone']['ntfy_topic'] == 'airalert-x1'
    controller.stop()
    form = controller.settingsData()
    form.update(units='nm', rings='5, 10, 20', ntfy_enabled=False, quietEnabled=False)
    assert controller.saveSettings(form) == ''
    assert controller.config['units'] == 'nm' and controller.config['rings'] == [5.0, 10.0, 20.0]
    form.update(homeEnabled=True, lat='95', lon='0')
    assert controller.saveSettings(form) != ''
    assert not [r for r in capture.records if 'QML' in r], capture.records


def test_rule_editor_new_options_through_qml(ui):
    from PySide6.QtCore import QMetaObject, QObject
    app, controller, window, capture = ui
    editor = window.findChild(QObject, 'ruleEditor')
    count = len(controller.config['rules'])
    controller.newEmergencyRule()
    assert wait_until(app, lambda: editor.property('opened'))
    assert editor.property('condition') == 'squawk_emergency'
    assert editor.property('overrideQuiet') and editor.property('speak')
    QMetaObject.invokeMethod(editor, 'save')
    assert wait_until(app, lambda: not editor.property('visible'))
    assert controller.property('hasEmergencyRule') and len(controller.config['rules']) == count + 1
    controller.newRule()
    assert wait_until(app, lambda: editor.property('opened'))
    editor.setProperty('name', 'Low and close')
    editor.setProperty('condition', 'approach')
    editor.setProperty('threshold', '2')
    editor.setProperty('lookahead', '6')
    QMetaObject.invokeMethod(editor, 'addExtra')
    pump(app, 100)
    assert 'predicted to pass within 2' in editor.property('sentence') and 'below 3000 ft' in editor.property('sentence')
    QMetaObject.invokeMethod(editor, 'save')
    pump(app, 150)
    assert editor.property('error') == '', editor.property('error')
    saved = controller.config['rules'][-1]
    assert saved['condition'] == 'approach' and saved['lookahead'] == 6 and saved['extra'][0]['threshold'] == 3000
    assert not [r for r in capture.records if 'QML' in r], capture.records


def test_sorted_tables_resolve_the_right_record(ui):
    app, controller, window, capture = ui
    for name in ('Zulu', 'Mike', 'Alpha'):
        assert controller.saveWatch(-1, dict(kind='aircraft', name=name, identifier=name.upper() + '1')) == ''
    model = controller.watchModel
    names = [model.get(i)['name'] for i in range(model.rowCount())]
    assert names == sorted(names)
    for i in range(model.rowCount()):
        row = model.get(i)
        assert controller.config['watchlist'][row['row']]['name'] == row['name']
    controller.searchHistory({'category': 'sightings'})
    history = controller.historyModel
    history.sortBy('title')
    for i in range(history.rowCount()):
        row = history.get(i)
        assert controller._history_rows[row['row']]['identifier'] in row['title']
    for _ in range(3):
        controller.deleteWatch(0)


def test_dialog_save_buttons_round_trip_through_qml(ui):
    from PySide6.QtCore import Q_ARG, QMetaObject, QObject
    app, controller, window, capture = ui
    controller.stop()
    settings = window.findChild(QObject, 'settingsDialog')
    QMetaObject.invokeMethod(settings, 'openWith', Q_ARG('QVariant', False), Q_ARG('QVariant', 'map'))
    assert wait_until(app, lambda: settings.property('opened'))
    settings.setProperty('units', 'km')
    QMetaObject.invokeMethod(settings, 'save', Q_ARG('QVariant', False))
    assert wait_until(app, lambda: not settings.property('visible'))
    assert settings.property('error') == ''
    assert controller.config['units'] == 'km'

    editor = window.findChild(QObject, 'ruleEditor')
    count = len(controller.config['rules'])
    controller.newRule()
    assert wait_until(app, lambda: editor.property('opened'))
    editor.setProperty('name', 'QML path rule')
    editor.setProperty('threshold', '12')
    assert 'within 12 km' in editor.property('sentence')
    QMetaObject.invokeMethod(editor, 'save')
    assert wait_until(app, lambda: not editor.property('visible'))
    assert editor.property('error') == ''
    rule = controller.config['rules'][count]
    assert rule['name'] == 'QML path rule' and rule['threshold'] == pytest.approx(12)
    controller.newRule()
    assert wait_until(app, lambda: editor.property('opened'))
    editor.setProperty('name', '')
    QMetaObject.invokeMethod(editor, 'save')
    pump(app, 100)
    assert editor.property('error') != '' and editor.property('opened')
    editor.close()
    assert not [r for r in capture.records if 'QML' in r], capture.records
