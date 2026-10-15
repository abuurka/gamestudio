# SPDX-License-Identifier: GPL-3.0-or-later
"""GameStudio in Blender's top bar: the GameStudio and Game menus.

Everything here is added to Blender's own UI (Menu.append) and removed again on unregister; no
Blender class is replaced. The play buttons and the agent's button are ui_topbar.py, the status bar
items ui.draw_statusbar (the game, the dev server) and activity_ui.draw_statusbar (the agent), the
play shortcuts keymap.py.

  GameStudio   Launcher, New/Open Project, Open Recent, Examples, and Open <name> while a new project
               waits for the open file's unsaved changes (projects.GAMESTUDIO_MT_projects);
               Open in Code Editor, New Component/System (editors/project.py); Open in Claude
               Code and Open in Codex, the starter prompts and the agent bridge's status
               (claude_code.py, codex.py); Edit Game UI and Back to Level (gameui/ops.py); the Scene
               view (editors/scene_view.py), workspaces, preferences and help (below); the
               first-run setup's progress, Retry and Set Up (environment.draw_menu_status)
  Game         Play, Pause, Step, Stop, live edits, the game window, export, Bake Lighting, the dev server
"""

import bpy

from . import claude_code

DOCS_URL = "https://abuurka.github.io/gamestudio/"


class GAMESTUDIO_MT_game(bpy.types.Menu):
    """Play controls; their shortcuts come from keymap.py and show next to each item."""

    bl_idname = "GAMESTUDIO_MT_game"
    bl_label = "Game"

    def draw(self, context):
        layout = self.layout
        layout.operator("gamestudio.play", text="Play", icon="PLAY")
        layout.operator("gamestudio.pause", text="Pause / Resume", icon="PAUSE")
        layout.operator("gamestudio.step", text="Step", icon="FRAME_NEXT")
        layout.operator("gamestudio.stop", text="Stop", icon="SNAP_FACE")
        layout.separator()
        scene_props = getattr(context.scene, "gs", None)
        if scene_props is not None:
            layout.prop(scene_props, "live_enabled")
        layout.operator("gamestudio.open_game", text="Open Game Window", icon="WINDOW")
        layout.operator("gamestudio.export_scene", text="Export Scene", icon="EXPORT")
        layout.operator("gamestudio.bake_lighting", text="Bake Lighting", icon="LIGHT_SUN")
        layout.separator()
        layout.operator("gamestudio.start_server", text="Start Dev Server", icon="URL")
        layout.operator("gamestudio.stop_server", text="Stop Dev Server", icon="CANCEL")
        layout.separator()
        layout.operator("gamestudio.reload_registry", text="Reload Components", icon="FILE_REFRESH")
        layout.operator("gamestudio.assign_ids", text="Assign Entity IDs", icon="COPY_ID")


def draw_gamestudio_menu_end(self, context):
    """The end of the GameStudio menu, after the project and script actions."""
    from .gameui import ops as gameui_ops

    layout = self.layout
    layout.operator_context = "INVOKE_DEFAULT"
    layout.separator()
    gameui_ops.draw_menu_items(layout, context)  # Edit Game UI, Back to Level
    layout.separator()
    claude_code.draw_menu_section(layout, context)
    layout.separator()
    layout.operator("gamestudio.scene_view_open", text="Scene View", icon="SHADING_RENDERED")
    layout.operator("gamestudio.add_workspaces", icon="WORKSPACE")
    # GameStudio's setup (Node.js, the game packages, a browser) shows near the top of the menu while it
    # runs, failed or is still to do (environment.draw_menu_status); it starts on its own when needed.
    layout.separator()
    layout.operator("gamestudio.launcher", text="Launcher...", icon="WINDOW")
    op = layout.operator("preferences.addon_show", text="Preferences...", icon="PREFERENCES")
    op.module = __package__
    layout.operator("wm.url_open", text="Documentation", icon="HELP").url = DOCS_URL


def draw_editor_menus(self, context):
    """Appended to the top bar's menus (File, Edit, Render, Window, Help)."""
    layout = self.layout
    layout.menu("GAMESTUDIO_MT_projects", text="GameStudio")
    layout.menu("GAMESTUDIO_MT_game")


# (menu or header, function, prepend?)
_ADDITIONS = (
    ("TOPBAR_MT_editor_menus", draw_editor_menus, False),
    ("GAMESTUDIO_MT_projects", draw_gamestudio_menu_end, False),
)


def register():
    bpy.utils.register_class(GAMESTUDIO_MT_game)
    for name, func, first in _ADDITIONS:
        owner = getattr(bpy.types, name)
        (owner.prepend if first else owner.append)(func)


def unregister():
    for name, func, _first in reversed(_ADDITIONS):
        owner = getattr(bpy.types, name, None)
        if owner is not None:
            owner.remove(func)
    bpy.utils.unregister_class(GAMESTUDIO_MT_game)
