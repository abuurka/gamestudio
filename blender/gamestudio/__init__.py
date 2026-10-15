# SPDX-License-Identifier: GPL-3.0-or-later
"""GameStudio: the Blender editor add-on for the GameStudio game engine.

Blender is the editor. The game runs in a separate TypeScript runtime that
talks to this add-on over a localhost live link (see docs/SPEC.md).
"""

import importlib

# Registration order matters: preferences and static property groups first,
# then the registry (which generates component PropertyGroups), then operators,
# their shortcuts and UI, the optional AI assistant, and the custom editors,
# which reuse the UI and assistant draw code (real editors on the fork, sidebar
# tabs on stock), then the Game View the game streams into (editors/game_stream.py) and the Scene
# view it also draws (editors/scene_view.py). The top bar menus come after the editors, which add to them.
_MODULE_NAMES = (
    "prefs",
    "core.props",
    "core.registry",
    "core.link",
    "core.live",
    "core.devserver",
    "core.stream",
    "core.twin",
    "core.gameui_live",
    "environment",
    "ops",
    "ops_lighting",
    "keymap",
    "ui",
    "projects",
    "assistant",
    "editors",
    "editors.game_stream",
    "editors.scene_view",
    "ui_scene",
    "claude_code",
    "codex",
    "menus",
    "ui_topbar",
    "launcher",
    "workspaces",
    "kits",
    "procedural",
    "assetlib",
    "gameui",
    "bridge",
    "activity",
)

_modules = []


def _load_modules():
    mods = []
    for name in _MODULE_NAMES:
        mod = importlib.import_module(f".{name}", __package__)
        mods.append(mod)
    return mods


def register():
    global _modules
    _modules = _load_modules()
    for mod in _modules:
        if hasattr(mod, "register"):
            mod.register()


def unregister():
    for mod in reversed(_modules):
        if hasattr(mod, "unregister"):
            mod.unregister()
    _modules.clear()


class glTF2ExportUserExtension:  # noqa: N801 (the name Blender's glTF exporter looks up)
    """Hooks into Blender's glTF exporter, which makes one of these for every export (it looks for the
    class on enabled add-ons). While GameStudio exports a scene, the clips it samples itself are taken
    out of the exporter's work (core/clips.py); for any other export the hook does nothing."""

    def gather_actions_hook(self, blender_object, actions, export_settings):
        from .core import clips

        clips.gather_actions_hook(blender_object, actions, export_settings)
