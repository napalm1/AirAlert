"""Public-release checks: simulation mode stays hidden until switched on in Settings, and the published files
carry no leftover references to earlier projects."""
import json
import logging
import os
import re
import time
from pathlib import Path

import pytest

from airalert.core.config import Config
from airalert.engine import MODES

from tests.test_features2 import FakeManager, QmlLog, plane, pump, texts, wait_until

ROOT = Path(__file__).resolve().parents[1]
HOME = [32.0, -118.0]
PUBLISHED = ('airalert', 'installer', 'tools', 'tests', 'README.md', 'THIRD_PARTY.md', 'LICENSE', 'main.py')


def test_fresh_settings_hide_simulation(tmp_path):
    config = Config(tmp_path)
    assert config['show_simulation'] is False
    assert config['mode'] == 'Aircraft'


def test_settings_saved_in_simulation_mode_keep_it_visible(tmp_path):
    (tmp_path / 'settings.json').write_text(json.dumps(dict(home=HOME, mode='Simulation', setup_done=True)), 'utf-8')
    config = Config(tmp_path)
    assert config['mode'] == 'Simulation' and config['show_simulation'] is True
    (tmp_path / 'settings.json').write_text(json.dumps(dict(home=HOME, mode='Aircraft', setup_done=True)), 'utf-8')
    assert Config(tmp_path)['show_simulation'] is False
    (tmp_path / 'settings.json').write_text(json.dumps(dict(mode='Aircraft', show_simulation='yes')), 'utf-8')
    assert 'could not be read' in Config(tmp_path).warning


def test_published_files_do_not_mention_earlier_projects():
    hits = []
    for top in PUBLISHED:
        path = ROOT / top
        files = [path] if path.is_file() else [p for p in path.rglob('*') if p.suffix in
                                               ('.py', '.md', '.qml', '.js', '.txt', '.json', '.ps1', '.html')]
        for file in files:
            if '__pycache__' in file.parts:
                continue
            text = file.read_text('utf-8', errors='replace')
            if re.search('harbor ?scope', text, re.IGNORECASE):
                hits.append(str(file.relative_to(ROOT)))
    assert hits == []


@pytest.fixture(scope='module')
def ui(tmp_path_factory):
    folder = tmp_path_factory.mktemp('airalert-public')
    (folder / 'settings.json').write_text(json.dumps(dict(home=HOME, setup_done=True, sample_seconds=1,
                                                          theme='Dark')), 'utf-8')
    os.environ['AIRALERT_DATA'] = str(folder)
    capture = QmlLog()
    logging.getLogger('airalert').addHandler(capture)
    from airalert.app import build
    app, engine, controller, window = build(['test'], tray=False, show=True, dialogs=False)
    assert window is not None, capture.records
    window.resize(1400, 880)
    controller.bridges['phonemap'].host = '127.0.0.1'      # never open the firewall prompt from a test
    pump(300)
    yield dict(app=app, controller=controller, window=window, capture=capture, folder=folder)
    controller.shutdown()
    logging.getLogger('airalert').removeHandler(capture)


def warnings(ui):
    return [r for r in ui['capture'].records if 'QML' in r]


def mentions(window):
    # Recorded history ("Monitoring started · Simulation") is data from the time the mode was on, not interface text.
    return [t for t in texts(window) if 'simulat' in t.lower() and not t.startswith('Monitoring started')]


def open_settings(window, section):
    from PySide6.QtCore import Q_ARG, QMetaObject, QObject
    dialog = window.findChild(QObject, 'settingsDialog')
    QMetaObject.invokeMethod(dialog, 'openWith', Q_ARG('QVariant', False), Q_ARG('QVariant', section))
    assert wait_until(lambda: dialog.property('opened'))
    pump(150)
    return dialog


def close_settings(dialog):
    dialog.close()
    assert wait_until(lambda: not dialog.property('visible'))


def test_simulation_is_absent_from_a_fresh_station(ui):
    from PySide6.QtCore import QObject
    controller, window = ui['controller'], ui['window']
    assert controller.property('simulationEnabled') is False
    assert list(controller.property('modes')) == [m for m in MODES if m != 'Simulation']
    assert controller.property('mode') == 'Aircraft'
    controller.setMode('Simulation')
    assert controller.property('mode') == 'Aircraft'
    assert controller.modesFor(True) == MODES and controller.modesFor(False) == controller.property('modes')

    # Live page while "monitoring" a receiver that hears nothing, then with real traffic.
    station = controller.station
    station.manager, station.running, station.sim = FakeManager(), True, None
    controller.stateChanged.emit()
    controller.refresh()
    pump(150)
    assert 'Reception depends on antenna, gain and local traffic.' in texts(window)
    assert mentions(window) == []
    station.manager.messages.put(dict(plane('A0F1BB', km=40, callsign='UAL1', altitude=31000, heading=90, speed=430),
                                      _received=time.time()))
    controller._tick()
    controller.refresh()
    controller.selectTarget('aircraft:A0F1BB')
    pump(200)
    assert mentions(window) == []

    # Statistics: no simulation switch, hints or source card.
    window.setProperty('page', 4)
    pump(400)
    assert mentions(window) == []
    switch = window.findChild(QObject, 'statsSimulation')
    assert switch is not None and switch.property('visible') is False
    insights = controller.bridges['insights']
    insights.setIncludeSimulation(True)
    assert insights.property('includeSimulation') is False     # hidden mode never counts
    window.setProperty('page', 0)
    pump(100)

    # Settings: only the switch itself talks about simulation.
    for section in ('general', 'location', 'receivers', 'connections'):
        dialog = open_settings(window, section)
        assert list(dialog.property('modeList')) == controller.property('modes')
        assert sorted(mentions(window)) == sorted(['Show simulation mode', 'Adds a Simulation monitoring mode with '
                                                   'fictional aircraft and vessels near your home location, for trying '
                                                   'alerts and geofences without a receiver.']
                                                  if section == 'general' else []), section
        close_settings(dialog)
    station.running, station.manager = False, None
    controller.stateChanged.emit()
    assert not warnings(ui), ui['capture'].records


def test_switching_simulation_on_and_off(ui):
    from PySide6.QtCore import QObject
    controller, window = ui['controller'], ui['window']
    form = controller.settingsData()
    assert controller.saveSettings(dict(form, show_simulation=True)) == ''
    assert controller.property('simulationEnabled') is True
    assert list(controller.property('modes')) == MODES
    controller.setMode('Simulation')
    assert controller.property('mode') == 'Simulation'
    controller.start()
    assert controller.station.running and controller.property('isSimulation')
    for _ in range(5):
        controller.station.sim_time -= 2
        controller.station.tick()
    controller.refresh()
    window.setProperty('page', 4)
    pump(400)
    switch = window.findChild(QObject, 'statsSimulation')
    assert switch.property('visible') is True
    insights = controller.bridges['insights']
    insights.setIncludeSimulation(True)
    assert insights.property('includeSimulation') is True
    assert mentions(window) != []
    dialog = open_settings(window, 'general')
    assert list(dialog.property('modeList')) == MODES
    assert dialog.property('mode') == 'Simulation'
    close_settings(dialog)

    # Hiding simulation while it runs stops monitoring and falls back to the aircraft receiver mode.
    form = controller.settingsData()
    assert form['mode'] == 'Simulation' and form['show_simulation'] is True
    assert controller.saveSettings(dict(form, show_simulation=False)) == ''
    assert not controller.station.running
    assert controller.config['mode'] == 'Aircraft' and controller.property('mode') == 'Aircraft'
    assert controller.property('simulationEnabled') is False
    assert insights.property('includeSimulation') is False
    pump(300)
    assert switch.property('visible') is False
    assert mentions(window) == []
    window.setProperty('page', 0)
    pump(100)
    assert not warnings(ui), ui['capture'].records


def test_update_feed_is_configured_but_switched_off_for_tests(ui):
    """The shipped copy checks GitHub for updates; tests (via conftest) and tools never do."""
    from airalert import UPDATE_URL
    assert UPDATE_URL.startswith('https://github.com/') and UPDATE_URL.endswith('/latest.json')
    controller = ui['controller']
    assert controller.update_url == '' and controller.property('updateInfo')['configured'] is False


def scene_point(item, x, y):
    from PySide6.QtCore import QPoint, QPointF
    p = item.mapToScene(QPointF(x, y))
    return QPoint(round(p.x()), round(p.y()))


def test_right_click_on_the_map_sets_the_station(ui):
    from PySide6.QtCore import QMetaObject, QObject, Qt
    from PySide6.QtTest import QTest
    controller, window = ui['controller'], ui['window']
    window.setProperty('page', 0)
    pump(200)
    item = controller.map
    menu = window.findChild(QObject, 'mapContextMenu')
    assert menu is not None and not menu.property('visible')
    toasts = []
    controller.toast.connect(lambda text, kind: toasts.append((text, kind)))
    x, y = item.width() * 0.6, item.height() * 0.55
    expected = item.coordinate(x, y)
    QTest.mouseClick(window, Qt.MouseButton.RightButton, Qt.KeyboardModifier.NoModifier, scene_point(item, x, y))
    assert wait_until(lambda: menu.property('visible'))
    got = (menu.property('lat'), menu.property('lon'))   # the click lands on whole pixels, hence the tolerance
    assert got == (pytest.approx(expected[0], abs=0.01), pytest.approx(expected[1], abs=0.01))
    shown = texts(window)
    assert any('Set station here' in t for t in shown) and any('°N' in t and '°W' in t for t in shown)
    QMetaObject.invokeMethod(menu, 'setStation')
    assert wait_until(lambda: not menu.property('visible'))
    assert controller.config['home'] == [round(got[0], 6), round(got[1], 6)]
    assert controller.property('homeSet') and toasts and toasts[-1][0].startswith('Station location set to ')
    assert toasts[-1][0].endswith('°W') and toasts[-1][1] == 'success'
    assert (item.property('centerLat'), item.property('centerLon')) == (pytest.approx(got[0], abs=1e-5),
                                                                        pytest.approx(got[1], abs=1e-5))

    # A right-button drag pans without opening the menu; while drawing a geofence it undoes a point instead.
    start = scene_point(item, x, y)
    QTest.mousePress(window, Qt.MouseButton.RightButton, Qt.KeyboardModifier.NoModifier, start)
    QTest.mouseMove(window, scene_point(item, x + 60, y + 40))
    QTest.mouseRelease(window, Qt.MouseButton.RightButton, Qt.KeyboardModifier.NoModifier, scene_point(item, x + 60, y + 40))
    pump(100)
    assert not menu.property('visible')
    controller.beginZone()
    item.vertices.append(item.coordinate(x, y))
    QTest.mouseClick(window, Qt.MouseButton.RightButton, Qt.KeyboardModifier.NoModifier, scene_point(item, x, y))
    pump(100)
    assert not menu.property('visible') and item.vertices == []
    controller.cancelZone()
    assert controller.setHome(95, 0) == 'That point is outside the map.'
    assert controller.setHome('x', 0) == 'That point has no usable coordinates.'
    controller.setHome(HOME[0], HOME[1])
    assert not warnings(ui), ui['capture'].records
