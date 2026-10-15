# SPDX-License-Identifier: GPL-3.0-or-later
"""Generate the GameStudio app template's startup.blend and userpref.blend.

These .blend files are build outputs, so they are reproducible, reviewable as
code, and regenerated on every Blender upgrade. Nobody saves them by hand.

  startup.blend   the game workspaces, saved with stock editors only (so stock Blender opens
                  them unchanged), each with the GameStudio editors it gets on GameStudio
                  Editor (LAYOUTS; applied by the template when a workspace is first shown)
  userpref.blend  the accent theme, the enabled extension, and Blender's default shortcuts
                  that the play shortcuts replace, turned off

Run through gsfork, which sets the environment this script needs:
  python editor/tools/gsfork.py template [--blender PATH]

Environment:
  BLENDER_USER_RESOURCES      throw-away folder; save_userpref writes there
  BLENDER_SYSTEM_EXTENSIONS   editor/shell/extensions, so the extension can be enabled
"""

import importlib
import math
import os
import pathlib
import shutil
import sys

import bpy

REPO = pathlib.Path(__file__).resolve().parents[3]
TEMPLATE = REPO / "editor" / "shell" / "scripts" / "startup" / "bl_app_templates_system" / "GameStudio"
SPLASH = REPO / "editor" / "branding" / "generated" / "splash.png"
EXTENSION_MODULE = "bl_ext.system.gamestudio"

# (factory workspace, GameStudio name). Tabs keep the factory order.
KEEP = {
    "Layout": "Level",
    "Modeling": "Model",
    "Shading": "Materials",
    "Animation": "Animate",
    "Geometry Nodes": "Procedural",
    "Scripting": "Debug",
}

# What GameStudio Editor changes in each workspace when it is first shown (the template's
# __init__.py, LAYOUT_PROP). The file itself keeps stock editors so that stock Blender opens it
# unchanged: an area of an unknown editor type would become a 3D Viewport there.
LAYOUT_PROP = "gamestudio_layout"
LAYOUTS = {
    "Level": {
        "VIEW_3D": {"sidebar": "Game", "fork_sidebar": False},       # the Inspector replaces it
        "PROPERTIES": {"editor": "GAMESTUDIO_INSPECTOR"},
        "DOPESHEET_EDITOR": {"editor": "GAMESTUDIO_CONSOLE", "height": 0.27},  # the Timeline
    },
    "Debug": {
        "TEXT_EDITOR": {"editor": "GAMESTUDIO_GAME_VIEW"},
        "CONSOLE": {"editor": "GAMESTUDIO_CONSOLE"},                  # the Python console
        "INFO": {"close": True},                                      # the console takes its space
        "OUTLINER/LIBRARIES": {"editor": "GAMESTUDIO_PROJECT"},       # the "Blender File" view
        "PROPERTIES": {"editor": "GAMESTUDIO_INSPECTOR"},
    },
}

# GameStudio orange. Filled states (a pressed toggle, the active tool) use the deeper ACCENT_FILL,
# which keeps white text readable (3.8:1); thin marks (cursor, progress) use the brighter ACCENT.
ACCENT = (0.98, 0.55, 0.12, 1.0)
ACCENT_FILL = (0.824, 0.392, 0.059, 1.0)
FILLED_WIDGETS = ("wcol_tool", "wcol_toolbar_item", "wcol_radio", "wcol_option", "wcol_toggle", "wcol_regular",
                  "wcol_num", "wcol_list_item")


def fail(msg):
    print(f"BUILD_TEMPLATE_ERROR {msg}")
    sys.exit(1)


def build_workspaces():
    wm = bpy.context.window_manager
    window = wm.windows[0]
    window.workspace = bpy.data.workspaces["Layout"]
    # workspace.delete defers the removal to the GUI event loop, which never runs in
    # --background; batch_remove deletes the data blocks immediately.
    unwanted = [ws for ws in bpy.data.workspaces if ws.name not in KEEP]
    bpy.data.batch_remove(ids=unwanted)
    for old, new in KEEP.items():
        ws = bpy.data.workspaces.get(old)
        if ws is None:
            fail(f"factory workspace '{old}' is missing (Blender changed its startup file?)")
        ws.name = new
    window.workspace = bpy.data.workspaces["Level"]
    # The Level workspace opens with the sidebar (Game tab) visible.
    for area in bpy.data.workspaces["Level"].screens[0].areas:
        if area.type == "VIEW_3D":
            area.spaces.active.show_region_ui = True
    leftover = sorted(set(w.name for w in bpy.data.workspaces) - set(KEEP.values()))
    if leftover:
        fail(f"could not remove workspaces: {leftover}")
    for name, layout in LAYOUTS.items():
        ws = bpy.data.workspaces[name]
        present = {area.type for area in ws.screens[0].areas}
        missing = sorted(key for key in layout if key.partition("/")[0] not in present)
        if missing:
            fail(f"workspace '{name}' has no {missing} area (Blender changed its startup file?)")
        ws[LAYOUT_PROP] = layout


def build_scene():
    """The scene used by File > New: no default cube, just a camera and a sun."""
    scene = bpy.context.scene
    for obj in list(scene.objects):
        if obj.type == "MESH":
            bpy.data.objects.remove(obj, do_unlink=True)
    for obj in scene.objects:
        if obj.type == "LIGHT":
            obj.data.type = "SUN"
            obj.data.energy = 4.0
            obj.rotation_euler = (math.radians(45), math.radians(15), math.radians(30))


def build_theme(theme):
    ui = theme.user_interface
    for widget in FILLED_WIDGETS:
        getattr(ui, widget).inner_sel = ACCENT_FILL
    ui.wcol_numslider.item = ACCENT_FILL          # the filled part of a slider
    ui.wcol_progress.item = ACCENT
    ui.wcol_progress.inner_sel = ACCENT
    ui.widget_text_cursor = ACCENT[:3]


def disable_shadowed_keys():
    """Turn off the Blender defaults that GameStudio's play shortcuts replace (Shift+F5 switches an
    area to the 3D Viewport). Saved as a keymap change in userpref.blend, which app templates apply."""
    keymap = importlib.import_module(f"{EXTENSION_MODULE}.keymap")
    wm = bpy.context.window_manager
    # --background does not build the default keymaps. Then merge the extension's items into the
    # user keymaps: the saved change is a diff against default + add-on items, and must not record
    # the play shortcuts as removed.
    bpy.utils.keyconfig_init()
    wm.keyconfigs.update()
    disabled = []
    for km in wm.keyconfigs.user.keymaps:
        for kmi in km.keymap_items:
            for idname, key, shift, props in keymap.SHADOWED_DEFAULTS:
                if (kmi.idname == idname and kmi.type == key and kmi.shift == shift and not kmi.ctrl
                        and not kmi.alt and kmi.active
                        and all(getattr(kmi.properties, k, None) == v for k, v in props.items())):
                    kmi.active = False
                    disabled.append(f"{km.name}: {idname} {'Shift ' if shift else ''}{key}")
    if len(disabled) != len(keymap.SHADOWED_DEFAULTS):
        fail(f"expected to disable {len(keymap.SHADOWED_DEFAULTS)} default key(s), disabled {disabled}")
    wm.keyconfigs.update()
    window_km = wm.keyconfigs.user.keymaps[keymap.KEYMAP[0]]
    active = {(kmi.idname, kmi.type, kmi.shift) for kmi in window_km.keymap_items if kmi.active}
    missing = [item for item in keymap.PLAY_KEYS if item not in active]
    if missing:
        fail(f"play shortcuts missing from the user keymap after the change: {missing}")
    return disabled


def build_preferences():
    prefs = bpy.context.preferences
    build_theme(prefs.themes[0])
    prefs.view.show_splash = True
    result = bpy.ops.preferences.addon_enable(module=EXTENSION_MODULE)
    if result != {"FINISHED"} or EXTENSION_MODULE not in prefs.addons:
        fail(f"could not enable {EXTENSION_MODULE}; is BLENDER_SYSTEM_EXTENSIONS set to editor/shell/extensions?")
    return disable_shadowed_keys()


def main():
    if not os.environ.get("BLENDER_USER_RESOURCES"):
        fail("BLENDER_USER_RESOURCES must point to a scratch folder (never your real profile)")
    build_workspaces()
    build_scene()
    disabled = build_preferences()

    TEMPLATE.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=str(TEMPLATE / "startup.blend"), copy=True, compress=True)
    bpy.ops.wm.save_userpref()
    saved = pathlib.Path(bpy.utils.user_resource("CONFIG")) / "userpref.blend"
    if not saved.is_file():
        fail(f"save_userpref wrote nothing at {saved}")
    shutil.copy2(saved, TEMPLATE / "userpref.blend")
    if SPLASH.is_file():
        shutil.copy2(SPLASH, TEMPLATE / "splash.png")
    print(f"BUILD_TEMPLATE_OK workspaces={[w.name for w in bpy.data.workspaces]} keys off={disabled} -> {TEMPLATE}")


main()
