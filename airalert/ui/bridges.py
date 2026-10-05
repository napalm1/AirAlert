"""Feature bridges exposed to QML as extra context properties.

Each module listed here provides ``create(controller) -> QObject``. The bridge
may register ``controller.refresh_hooks``, set ``controller.scene_extras`` /
``controller.target_provider`` and call ``controller.push_scene()``.
"""
import importlib
import logging

log = logging.getLogger(__name__)

# QML context-property name -> module providing create(controller)
BRIDGES = {
    'insights': 'airalert.ui.insights',     # aircraft database, statistics charts, coverage
    'playback': 'airalert.ui.playback',     # history playback on the map
    'reception': 'airalert.ui.reception',
           'phonemap': 'airalert.ui.phonemap', 'lookup': 'airalert.ui.lookup',   # receiver reception check
}


def create_bridges(controller):
    bridges = {}
    for name, module_name in BRIDGES.items():
        try:
            module = importlib.import_module(module_name)
        except ModuleNotFoundError as e:
            if e.name == module_name:
                continue  # feature not present in this build
            raise
        bridges[name] = module.create(controller)
    controller.bridges = bridges
    return bridges
