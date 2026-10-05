"""AirAlert entry point: logging, Qt application, QML engine and controller."""
import logging
import os
import sys
from logging.handlers import RotatingFileHandler

log = logging.getLogger('airalert')


def _qt_dll_search_path():
    """QML plugins are loaded with plain LoadLibrary, so the Qt DLLs next to
    PySide6 must be on PATH or plugins such as the Controls Basic style fail."""
    if sys.platform != 'win32':
        return
    import importlib.util
    spec = importlib.util.find_spec('PySide6')
    folders = list(spec.submodule_search_locations or []) if spec else []
    for folder in folders:
        if folder not in os.environ.get('PATH', ''):
            os.environ['PATH'] = folder + os.pathsep + os.environ.get('PATH', '')
        try:
            os.add_dll_directory(folder)
        except (OSError, AttributeError):
            pass


def qml_folder():
    from .core.receivers import resource_root
    return resource_root() / 'airalert' / 'qml'


def build(argv, tray=True, show=True, dialogs=True):
    """Create the application objects. Returns (app, engine, controller, window)."""
    _qt_dll_search_path()
    # The map is painted in Python; rendering on the GUI thread avoids the render
    # thread waiting on the Python interpreter lock (e.g. during window grabs).
    os.environ.setdefault('QSG_RENDER_LOOP', 'basic')
    from PySide6.QtCore import QUrl
    from PySide6.QtGui import QFont, QFontDatabase, QIcon
    from PySide6.QtQml import QQmlApplicationEngine, qmlRegisterType
    from PySide6.QtQuickControls2 import QQuickStyle
    from PySide6.QtWidgets import QApplication, QMessageBox

    from .core.receivers import resource_root
    from .engine import Station
    from .ui.controller import Controller
    from .ui.mapcanvas import MapCanvas

    app = QApplication.instance() or QApplication(argv)
    app.setApplicationName('AirAlert')
    app.setOrganizationName('AirAlert')
    app.setApplicationDisplayName('AirAlert')
    app.setWindowIcon(QIcon(str(resource_root() / 'assets/airalert.ico')))
    QQuickStyle.setStyle('Basic')
    families = set(QFontDatabase.families())
    family = next((f for f in ('Segoe UI Variable Text', 'Segoe UI', 'Arial') if f in families), None)
    if family:
        font = QFont(family)
        font.setPixelSize(13)
        app.setFont(font)

    try:
        station = Station()
    except Exception:
        log.exception('Startup failed')
        from .core.config import data_dir
        QMessageBox.critical(None, 'AirAlert could not start',
                             f'Local data could not be opened. Check disk space and folder permissions.\n'
                             f'Data folder: {data_dir()}')
        return app, None, None, None
    controller = Controller(station, tray=tray)
    qmlRegisterType(MapCanvas, 'AirAlert.Map', 1, 0, 'MapCanvas')
    engine = QQmlApplicationEngine()
    engine.warnings.connect(lambda warnings: [log.warning('QML: %s', w.toString()) for w in warnings])
    engine.rootContext().setContextProperty('app', controller)
    from .ui.bridges import create_bridges
    for name, bridge in create_bridges(controller).items():
        engine.rootContext().setContextProperty(name, bridge)
    engine.load(QUrl.fromLocalFile(str(qml_folder() / 'Main.qml')))
    if not engine.rootObjects():
        log.error('The interface could not be loaded')
        if dialogs:
            QMessageBox.critical(None, 'AirAlert', 'The interface could not be loaded. Details are in AirAlert.log.')
        return app, engine, controller, None
    window = engine.rootObjects()[0]
    controller.attach_window(window)
    app.aboutToQuit.connect(controller.shutdown)
    if show:
        window.show()
    return app, engine, controller, window


def main(argv=None):
    argv = list(argv or sys.argv)
    if '--self-test' in argv:
        os.environ['AIRALERT_DATA'] = argv[argv.index('--self-test') + 1]
    from .core.config import data_dir
    folder = data_dir()
    handler = RotatingFileHandler(folder / 'AirAlert.log', maxBytes=2_000_000, backupCount=5, encoding='utf-8')
    logging.basicConfig(level=logging.INFO, handlers=[handler],
                        format='%(asctime)s %(levelname)s %(name)s: %(message)s')
    from . import __version__
    log.info('AirAlert %s starting; data folder %s', __version__, folder)
    os.environ.setdefault('QT_QUICK_CONTROLS_STYLE', 'Basic')
    _qt_dll_search_path()

    from PySide6.QtWidgets import QApplication, QMessageBox

    def excepthook(kind, value, tb):
        logging.critical('Unhandled application error', exc_info=(kind, value, tb))
        if QApplication.instance():
            QMessageBox.critical(None, 'AirAlert', 'An operation failed. Details were written to AirAlert.log '
                                                   'in the local data folder.')
    sys.excepthook = excepthook

    if '--self-test' in argv:
        from .acceptance import run
        return run(argv[argv.index('--self-test') + 1])

    # One AirAlert per user: a second launch just brings the running one forward,
    # so two copies never compete for the receiver.
    app = QApplication.instance() or QApplication(argv)
    server = single_instance_server()
    if server is None:
        log.info('AirAlert is already running; showing the existing window')
        return 0
    minimized = '--minimized' in argv
    app, engine, controller, window = build(argv, show=not minimized)
    if window is None:
        return 1
    if minimized and controller.tray is None:
        window.show()  # no tray to live in: show the window after all

    def show_existing():
        connection = server.nextPendingConnection()
        log.info('Another launch asked to show this window')
        if connection is not None:
            connection.readyRead.connect(lambda: (connection.readAll(), controller.showWindow()))
            connection.disconnected.connect(connection.deleteLater)
            controller.showWindow()
    server.newConnection.connect(show_existing)
    result = app.exec()
    # Tear down the interface before the controller and feature bridges it binds to.
    window = None
    del engine
    return result


def instance_name():
    import getpass
    return os.environ.get('AIRALERT_INSTANCE') or f'AirAlert-{getpass.getuser()}'


def single_instance_server():
    """Return a listening QLocalServer, or None when another AirAlert already owns the name."""
    from PySide6.QtNetwork import QLocalServer, QLocalSocket
    name = instance_name()
    probe = QLocalSocket()
    probe.connectToServer(name)
    if probe.waitForConnected(400):
        probe.write(b'show')
        probe.flush()
        probe.waitForBytesWritten(400)
        probe.disconnectFromServer()
        return None
    QLocalServer.removeServer(name)  # clear a stale name left by a crash
    server = QLocalServer()
    if not server.listen(name):
        log.warning('Single-instance guard unavailable: %s', server.errorString())
    return server
