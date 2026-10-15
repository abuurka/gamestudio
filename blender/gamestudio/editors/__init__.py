# SPDX-License-Identifier: GPL-3.0-or-later
"""Custom editors: the providers behind GameStudio Editor's Extension Editor.

Each module describes one editor and registers it through host.editors, which
makes it a real editor on GameStudio Editor and a 3D View sidebar tab on stock
Blender (docs/fork/ARCHITECTURE.md section 3):

  inspector   GAMESTUDIO_INSPECTOR  the selected entity and its components
  console     GAMESTUDIO_CONSOLE    runtime logs and errors from the game
  project     GAMESTUDIO_PROJECT    scenes, scripts, dev server and web build
  assistant   GAMESTUDIO_ASSISTANT  the AI chat
  game_view   GAMESTUDIO_GAME_VIEW  native (C++); only its header is Python

The panel bodies are shared with the sidebar UI (ui.py, assistant/ui.py).
"""

import importlib

from .. import host

_MODULES = ("inspector", "console", "project", "assistant", "game_view")
_loaded = []


def register():
    for name in _MODULES:
        mod = importlib.import_module(f".{name}", __package__)
        mod.register()
        _loaded.append(mod)


def unregister():
    for mod in reversed(_loaded):
        mod.unregister()
    _loaded.clear()
    host.editors.unregister_all()
