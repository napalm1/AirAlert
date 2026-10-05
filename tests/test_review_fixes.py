"""Regression tests for the code-review fixes (legend, geofence renames, rate sampling, settings, vessels...)."""
import json
import logging
import os
import sqlite3
import time

import pytest

from airalert.core.config import Config
from airalert.core.models import destination
from airalert.engine import Station, nav_status_name, ship_type_name

HOME = [32.0, -118.0]
SQUARE = [[31.9, -118.1], [31.9, -117.9], [32.1, -117.9], [32.1, -118.1]]


@pytest.fixture
def station(tmp_path):
    config = Config(tmp_path)
    config.values.update(home=HOME, mode='Simulation', setup_done=True, sample_seconds=1)
    config.save()
    s = Station(config)
    yield s
    s.shutdown()


# ------------------------------------------------------------------ engine
def test_zone_rename_and_remove_follow_extra_conditions(station):
    assert station.create_zone('Harbor', SQUARE) == ''
    assert station.save_rule(-1, dict(name='Low in harbor', kind='aircraft', field='registration', value='',
                                      condition='altitude_below', threshold=3000, cooldown=0,
                                      extra=[dict(condition='in_zone', zone='Harbor')])) == ''
    assert station.linked_rules('Harbor') == 1
    assert station.rename_zone('Harbor', 'Marina') == ''
    assert station.config['rules'][0]['extra'][0]['zone'] == 'Marina'
    assert station.linked_rules('Marina') == 1 and station.rule_rows()[0]['inactive'] is False
    assert 'geofence “Marina”' in station.rule_rows()[0]['sentence']
    station.alerts.states[(station.config['rules'][0]['id'], 'aircraft:ABC123')] = True
    assert station.remove_zone('Marina') == ''
    assert station.rule_rows()[0]['inactive'] is True
    assert not station.alerts.states


def test_open_sessions_are_closed_at_their_last_record(tmp_path):
    config = Config(tmp_path)
    config.values.update(home=HOME)
    s = Station(config)
    conn = s.db.conn
    # Two sessions cut short by crashes, then a clean one.
    conn.executemany('INSERT INTO sessions(start, end, mode) VALUES (?, ?, ?)',
                     [(1000, None, 'Aircraft'), (5000, None, 'Aircraft'), (9000, 9100, 'Aircraft')])
    conn.execute('INSERT INTO events(time, category, key, message, simulated) VALUES (?, ?, ?, ?, ?)',
                 (1500, 'receiver', '', 'x', 0))
    conn.execute('INSERT INTO positions(key, time, lat, lon) VALUES (?, ?, ?, ?)', ('aircraft:A', 5600, 32, -118))
    conn.commit()
    s.shutdown()
    s = Station(Config(tmp_path), maintenance=False)
    ends = [r[0] for r in s.db.conn.execute('SELECT end FROM sessions ORDER BY start')]
    assert ends[:3] == [1500, 5600, 9100]
    assert s.statistics()['hours'] == round((500 + 600 + 100) / 3600, 2)
    s.shutdown()


def test_vessel_types_and_status_are_named(station):
    assert ship_type_name(70) == 'Cargo' and ship_type_name(36) == 'Sailing' and ship_type_name(84) == 'Tanker'
    assert ship_type_name(52) == 'Tug' and ship_type_name(0) == '' and ship_type_name(None) == ''
    assert ship_type_name('B738') == 'B738' and nav_status_name(5) == 'Moored' and nav_status_name(15) == ''
    lat, lon = destination(HOME, 3, 90)
    station.manager = None
    station.running = True
    station.store.update(dict(kind='vessel', identifier='366999712', name='SEA TEST', type=70, navigation_status=5,
                              lat=lat, lon=lon), time.time())
    t = station.store.targets['vessel:366999712']
    row = station.target_row(t)
    assert row['detail'] == '366999712 · Cargo' and row['secondary'] == 'Cargo'
    fields = station.details(t)
    labels = [k for k, _ in fields]
    assert len(labels) == len(set(labels)), labels  # no more two "Type" rows
    details = dict(fields)
    assert details['Category'] == 'Vessel' and details['Type'] == 'Cargo (70)'
    assert details['Navigation Status'] == 'Moored (5)'


def test_event_search_by_category(station):
    station.record_event('receiver', '', 'Receiver line')
    assert {e['category'] for e in station.db.events()} >= {'application', 'receiver'}
    assert {e['category'] for e in station.db.events(category='receiver')} == {'receiver'}


def test_marine_decoder_gets_a_text_gain(tmp_path, monkeypatch):
    from airalert.core import receivers
    seen = []
    worker = receivers.DecoderWorker('vessel', '123', dict(gain=40.0, ppm=0, home=None), tmp_path,
                                     __import__('queue').Queue(), __import__('queue').Queue())

    def fake_popen(args, **kwargs):
        seen.append(args)
        worker.stop_event.set()
        raise OSError('stop here')
    monkeypatch.setattr(receivers, 'detect_devices', lambda: [dict(index='0', name='RTL', serial='123')])
    monkeypatch.setattr(receivers.subprocess, 'Popen', fake_popen)
    worker.run()
    assert seen and all(isinstance(a, str) for a in seen[0]) and seen[0][-1] == '40.0'


# ---------------------------------------------------------------------- UI
class QmlLog(logging.Handler):
    def __init__(self):
        super().__init__(logging.WARNING)
        self.records = []

    def emit(self, record):
        self.records.append(record.getMessage())


def pump(ms):
    from PySide6.QtCore import QEventLoop, QTimer
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def items(window):
    from PySide6.QtCore import QObject
    seen, out = set(), []
    stack = [window.contentItem()] + list(window.findChildren(QObject))
    while stack:
        o = stack.pop()
        if id(o) in seen:
            continue
        seen.add(id(o))
        out.append(o)
        if hasattr(o, 'childItems'):
            stack.extend(o.childItems())
    return out


def find(window, predicate):
    return next((o for o in items(window) if predicate(o)), None)


def shown(item):
    return item.property('visible') is True  # effective: false while any ancestor is hidden


@pytest.fixture(scope='module')
def ui(tmp_path_factory):
    folder = tmp_path_factory.mktemp('airalert-review')
    (folder / 'settings.json').write_text(json.dumps(dict(home=HOME, mode='Simulation', setup_done=True,
                                                          sample_seconds=1, theme='Dark', map_theme='Dark')), 'utf-8')
    os.environ['AIRALERT_DATA'] = str(folder)
    capture = QmlLog()
    logging.getLogger('airalert').addHandler(capture)
    from airalert.app import build
    app, engine, controller, window = build(['test'], tray=False, show=True, dialogs=False)
    assert window is not None, capture.records
    window.resize(1400, 880)
    pump(300)
    controller.start()
    station = controller.station
    base = time.time() - 40
    for i in range(20):
        station.sim_time -= 2
        station.tick(now=base + i * 2)
    controller.refresh()
    pump(100)
    yield app, controller, window, capture
    controller.shutdown()
    logging.getLogger('airalert').removeHandler(capture)


def qml_warnings(capture):
    return [r for r in capture.records if 'QML' in r]


def test_message_rate_is_sampled_once_a_second_only(ui):
    app, controller, window, capture = ui
    station = controller.station
    calls = []
    controller.refresh_hooks.append(calls.append)
    try:
        station.received = 50
        controller.setSearch('N1')
        controller.setSearch('')
        controller.setLayer('trails', True)
        assert station.received == 50 and not calls  # searching and filtering never reset the count
        controller._second()
        assert station.rate == 50 and station.received == 0 and len(calls) == 1
    finally:
        controller.refresh_hooks.remove(calls.append)


def test_settings_save_while_running_with_numeric_receiver_values(ui):
    app, controller, window, capture = ui
    assert controller.station.running
    saved = dict(controller.config.values)
    try:
        # Values hand-edited or copied from another app may be numbers rather than text.
        controller.config.values.update(gain=40, aircraft_seconds=60.0, ppm=0.0)
        form = controller.settingsData()
        assert controller.saveSettings(dict(form, trail_minutes='20')) == ''
        assert controller.config['trail_minutes'] == 20
        assert 'Stop monitoring' in controller.saveSettings(dict(form, gain='30'))
    finally:
        controller.config.values.update(gain=saved['gain'], aircraft_seconds=saved['aircraft_seconds'],
                                        ppm=saved['ppm'], trail_minutes=saved['trail_minutes'])
        controller.config.save()


def test_altitude_filter_top_means_and_above(ui):
    app, controller, window, capture = ui
    target = controller.station.store.targets['aircraft:A00001']
    old = target.data['altitude']
    try:
        target.data['altitude'] = 55000
        controller.setAltitudeFilter(True, 0, 50000, True)
        assert target in controller.current_targets()
        controller.setAltitudeFilter(True, 0, 40000, True)
        assert target not in controller.current_targets()
    finally:
        target.data['altitude'] = old
        controller.setAltitudeFilter(False, 0, 50000, True)


def test_focus_on_a_target_that_left_says_so(ui):
    app, controller, window, capture = ui
    toasts = []
    controller.toast.connect(lambda text, kind: toasts.append(text))
    controller.clearSelection()
    controller.focusTarget('aircraft:FFFFFF')
    assert toasts and 'no longer being tracked' in toasts[-1]
    assert controller.property('selectedKey') == ''


def test_inspector_vessel_fields(ui):
    app, controller, window, capture = ui
    controller.selectTarget('vessel:990000001')
    details = controller.property('details')
    labels = [f['label'] for f in details['fields']]
    assert len(labels) == len(set(labels))
    values = {f['label']: f['value'] for f in details['fields']}
    assert values['Category'] == 'Vessel' and values['Type'] == 'Cargo (70)'
    assert {m['label']: m['value'] for m in details['metrics']}['Status'] == 'Under way (engine)'
    assert 'Cargo' in details['subtitle']
    controller.clearSelection()


def test_map_legend_explains_aircraft_and_draws_one_ramp(ui):
    from PySide6.QtCore import QPointF
    from airalert.ui.mapcanvas import ALTITUDE_STOPS, PALETTES, UNKNOWN_ALTITUDE
    app, controller, window, capture = ui
    window.setProperty('page', 0)
    controller.clearSelection()
    pump(200)
    legend = find(window, lambda o: o.objectName() == 'mapLegend')
    assert legend is not None and legend.isVisible()
    texts = {o.property('text') for o in items(window) if o.metaObject().className().startswith('QQuickText')
             and shown(o) and legend.isAncestorOf(o)}
    assert {'Aircraft are colored by altitude', 'No altitude', 'Vessel', 'Stale', 'Watched'} <= texts, texts
    key = controller.property('mapKey')
    assert key['unknown'] == UNKNOWN_ALTITUDE['Dark'] and key['vessel'] == PALETTES['Dark']['vessel']
    assert [s['color'] for s in controller.property('panelAltitudeStops')] == [c for _, c in ALTITUDE_STOPS['Dark']]

    # The altitude ramp is one rounded bar: its top edge row is colored all along, with no notches.
    ramp = find(window, lambda o: o.objectName() == 'altitudeRamp')
    assert ramp is not None
    image = window.grabWindow()
    origin = ramp.mapToScene(QPointF(0, 0))
    dpr = image.devicePixelRatio()
    y = round((origin.y() + 1.5) * dpr)
    above = image.pixelColor(round((origin.x() + ramp.width() / 2) * dpr), round((origin.y() - 3) * dpr))
    gaps = []
    for x in range(round((origin.x() + ramp.property('radius') + 1) * dpr),
                   round((origin.x() + ramp.width() - ramp.property('radius') - 1) * dpr)):
        c = image.pixelColor(x, y)
        if abs(c.red() - above.red()) + abs(c.green() - above.green()) + abs(c.blue() - above.blue()) < 60:
            gaps.append(x)
    assert not gaps, f'ramp shows the panel background at x={gaps[:10]}'

    # With aircraft hidden the key still explains vessels, without the altitude ramp.
    controller.setLayer('aircraft', False)
    pump(100)
    assert legend.isVisible() and not shown(ramp)
    visible = {o.property('text') for o in items(window) if o.metaObject().className().startswith('QQuickText')
               and shown(o) and legend.isAncestorOf(o)}
    assert 'Vessel' in visible and 'No altitude' not in visible
    controller.setLayer('aircraft', True)
    controller.setLayer('vessels', False)
    controller.setLayer('aircraft', False)
    pump(100)
    assert not legend.isVisible()
    controller.setLayer('aircraft', True)
    controller.setLayer('vessels', True)
    assert not qml_warnings(capture), capture.records


def test_inspector_field_list_keeps_its_scroll_position_while_data_refreshes(ui):
    app, controller, window, capture = ui
    window.setProperty('page', 0)
    controller.selectTarget('aircraft:A00001')
    pump(400)
    fields = find(window, lambda o: o.objectName() == 'inspectorFields')
    assert fields is not None and fields.property('visible')
    bottom = fields.property('contentHeight') - fields.property('height') + fields.property('originY')
    assert bottom > 60, 'the inspector should need scrolling at this window size'
    fields.setProperty('contentY', bottom)
    pump(50)
    for _ in range(3):  # the once-a-second refresh rebuilds the selected target's details
        controller.station.sim_time -= 2
        controller.station.tick()
        controller._second()
        pump(150)
    assert fields.property('contentY') == pytest.approx(bottom, abs=1)
    assert fields.property('count') == len(controller.property('details')['fields'])
    # A different target starts at the top of its own fields.
    controller.selectTarget('aircraft:A00002')
    pump(150)
    assert fields.property('contentY') == pytest.approx(fields.property('originY'), abs=1)
    controller.clearSelection()


def test_theme_change_keeps_a_historical_track_historical(ui):
    app, controller, window, capture = ui
    controller.searchHistory({'category': 'sightings'})
    assert controller.historyModel.rowCount() > 0
    controller.openTrack(0)
    before = controller.property('details')
    assert before['mode'] == 'history' and 'trackPoints' in before
    controller.toggleTheme()
    after = controller.property('details')
    assert after['mode'] == 'history' and after['trackPoints'] == before['trackPoints']
    controller.toggleTheme()
    controller.clearSelection()


def test_bad_gain_text_gives_a_readable_message(ui):
    app, controller, window, capture = ui
    was_running = controller.station.running
    if was_running:
        controller.stop()
    form = controller.settingsData()
    assert 'Gain must be' in controller.saveSettings(dict(form, gain='lots'))
    assert controller.saveSettings(dict(form, gain='30 dB')) == '' and controller.config['gain'] == '30'
    assert controller.saveSettings(dict(form, gain='auto')) == ''
    if was_running:
        controller.start()
        station = controller.station          # restarting emptied the simulated traffic: bring it back for later tests
        base = time.time() - 40
        for i in range(20):
            station.sim_time -= 2
            station.tick(now=base + i * 2)
        controller.refresh()


def test_selection_ripple_plays_then_rests(ui):
    app, controller, window, capture = ui
    window.setProperty('page', 0)
    controller.clearSelection()
    pump(200)
    pulse = find(window, lambda o: o.objectName() == 'selectionPulse')
    assert pulse is not None
    controller.selectTarget('aircraft:A00001')
    controller.map.centerOn(*controller.station.store.targets['aircraft:A00001'].position)
    pump(300)
    assert pulse.property('rippling'), 'the ripple should play when a target is selected'
    deadline = time.monotonic() + 9
    while pulse.property('rippling') and time.monotonic() < deadline:
        pump(100)
    assert not pulse.property('rippling'), 'the ripple must come to rest (no endless redraws)'
    controller.selectTarget('aircraft:A00002')   # a new selection plays it again
    pump(300)
    assert pulse.property('rippling')
    controller.clearSelection()
    pump(100)
    assert not qml_warnings(capture), capture.records


def test_hidden_window_skips_ui_refresh_but_keeps_sampling_and_hooks(ui):
    app, controller, window, capture = ui
    calls, refreshed, scenes = [], [], []
    controller.refresh_hooks.append(calls.append)
    real_refresh, real_push = controller.refresh, controller._push_scene
    controller.refresh = lambda: (refreshed.append(1), real_refresh())[1]
    controller._push_scene = lambda: (scenes.append(1), real_push())[1]
    try:
        assert controller._ui_visible()
        controller._second()
        assert refreshed and calls                    # on screen: everything runs
        refreshed.clear(), calls.clear()
        window.hide()                                 # closed to the tray
        assert not controller._ui_visible()
        controller.station.received = 42
        controller._second()
        assert not refreshed and len(calls) == 1      # no view work, but hooks and the rate sample still run
        assert controller.station.rate == 42
        scenes.clear()
        controller.push_scene()
        assert not scenes                             # bridges' scene pushes are skipped too
        controller.showWindow()
        assert controller._ui_visible() and refreshed and scenes   # showing brings everything up to date
        window.showMinimized()
        pump(100)
        refreshed.clear()
        controller._second()
        if not controller._ui_visible():              # a minimized window is not drawn either
            assert not refreshed
    finally:
        controller.refresh, controller._push_scene = real_refresh, real_push
        controller.refresh_hooks.remove(calls.append)
        window.showNormal()
        pump(100)
    assert controller._ui_visible()


def test_sync_rows_updates_in_place_without_a_reset():
    from airalert.ui.models import RowModel
    model = RowModel(['key', 'value'])
    resets, inserts, removals = [], [], []
    model.modelReset.connect(lambda: resets.append(1))
    model.rowsInserted.connect(lambda parent, first, last: inserts.append((first, last)))
    model.rowsRemoved.connect(lambda parent, first, last: removals.append((first, last)))
    model.sync_rows([dict(key='b', value=1), dict(key='c', value=2)])
    model.sync_rows([dict(key='a', value=0), dict(key='b', value=1), dict(key='c', value=5)])  # new row on top
    assert [r['key'] for r in model.rows] == ['a', 'b', 'c'] and model.rows[2]['value'] == 5
    model.sync_rows([dict(key='a', value=0), dict(key='c', value=5), dict(key='d', value=7)])  # remove + append
    assert [r['key'] for r in model.rows] == ['a', 'c', 'd']
    assert not resets and (0, 0) in inserts and removals == [(1, 1)]
    model.sync_rows([dict(key='d', value=7), dict(key='a', value=0)])  # reordered: falls back to a reset
    assert [r['key'] for r in model.rows] == ['d', 'a'] and resets


def test_alert_lists_keep_their_scroll_position(ui):
    app, controller, window, capture = ui
    station = controller.station
    first_new = len(controller.config['rules'])
    for i in range(12):
        assert station.save_rule(-1, dict(name=f'Scroll rule {i}', kind='aircraft', field='registration', value='',
                                          condition='enter', threshold=5, cooldown=0)) == ''
    for i in range(40):
        station.db.event('alert', 'aircraft:A00001', f'Scroll alert {i}', True, time.time() - 1000 + i)
    controller._refresh_rules()
    controller._load_feed()
    window.setProperty('page', 1)
    pump(300)
    rules = find(window, lambda o: o.objectName() == 'rulesList')
    feed = find(window, lambda o: o.objectName() == 'alertFeedList')

    def to_bottom(view):
        bottom = view.property('contentHeight') - view.property('height') + view.property('originY')
        assert bottom > 100
        view.setProperty('contentY', bottom)
        pump(50)
        return bottom
    rules_bottom, feed_bottom = to_bottom(rules), to_bottom(feed)
    controller.setRuleEnabled(first_new, False)  # pausing a rule rebuilds the rule rows
    station.db.event('alert', 'aircraft:A00001', 'Scroll alert new', True, time.time())
    controller._load_feed()  # a new alert arrives
    pump(150)
    assert rules.property('contentY') == pytest.approx(rules_bottom, abs=1)
    assert feed.property('contentY') - feed.property('originY') > 100, 'the feed jumped back to the top'
    for _ in range(12):
        controller.deleteRule(first_new)
    window.setProperty('page', 0)
    pump(100)


def test_chart_tables_keep_their_scroll_position_when_statistics_refresh(ui):
    app, controller, window, capture = ui
    insights = controller.bridges['insights']
    insights.setIncludeSimulation(True)
    window.setProperty('page', 4)
    pump(400)
    card = find(window, lambda o: o.objectName() == 'hoursCard')
    card.setProperty('showTable', True)
    pump(200)
    flick = find(window, lambda o: o.objectName() == 'chartTableFlick' and card.isAncestorOf(o))
    assert flick is not None and flick.property('visible')
    bottom = flick.property('contentHeight') - flick.property('height')
    assert bottom > 100
    flick.setProperty('contentY', bottom)
    insights.reload()  # the page refreshes its charts every 45 s
    pump(200)
    assert flick.property('contentY') == pytest.approx(bottom, abs=1)
    card.setProperty('showTable', False)
    insights.setIncludeSimulation(False)
    window.setProperty('page', 0)
    pump(100)
    assert not qml_warnings(capture), capture.records


def test_rule_editor_resets_threshold_when_the_unit_changes(ui):
    from PySide6.QtCore import Q_ARG, QMetaObject, QObject
    app, controller, window, capture = ui
    editor = window.findChild(QObject, 'ruleEditor')
    controller.newRule()
    pump(150)
    assert editor.property('threshold') == '25'

    def pick(condition):
        QMetaObject.invokeMethod(editor, 'pickCondition', Q_ARG('QVariant', condition))
        return editor.property('threshold')
    assert pick('altitude_below') == '3000'
    assert 'below 3000 ft' in editor.property('sentence')
    assert pick('speed_above') == '250'
    assert pick('first') == '250'  # no threshold: keep what is there
    assert pick('enter') == '25'
    editor.setProperty('threshold', '12')
    assert pick('approach') == '12'  # same unit: keep the user's value
    editor.close()
    pump(100)


def test_rule_editor_flags_a_removed_geofence_in_extra_conditions(ui):
    app, controller, window, capture = ui
    from PySide6.QtCore import QObject
    station = controller.station
    assert station.create_zone('Pier', SQUARE) == ''
    assert station.save_rule(-1, dict(name='Pier watch', kind='any', field='identifier', value='', condition='first',
                                      threshold=0, cooldown=0, extra=[dict(condition='in_zone', zone='Pier')])) == ''
    controller._refresh_rules()
    assert {z['name']: z['rules'] for z in controller.property('zones')}['Pier'] == 1
    assert station.remove_zone('Pier') == ''
    assert station.create_zone('Dock', SQUARE) == ''
    editor = window.findChild(QObject, 'ruleEditor')
    controller.editRule(len(controller.config['rules']) - 1)
    pump(150)
    assert editor.property('missingExtraZone') == 'Pier'
    assert editor.property('extras').toVariant()[0]['zone'] == 'Dock'
    editor.close()
    pump(100)
    assert not qml_warnings(capture), capture.records
