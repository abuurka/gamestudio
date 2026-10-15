# SPDX-License-Identifier: GPL-3.0-or-later
"""Play shortcuts: F5 Play, F6 Pause/Resume, F7 Step, F8 Stop.

The items live in the add-on key configuration, in the "Window" keymap, so they work over every
editor. Blender adds add-on items ahead of the default ones in a keymap, and lists them in menus
and tooltips; users change them in the extension's preferences.

Stop is F8, not Visual Studio's Shift+F5: in Blender's default keymap Shift+F1..F12 switch the
area under the mouse to another editor (Shift+F5: 3D Viewport), and an extension must not take
Blender's own keys from its users. F5 to F8 have no default binding, so the four play keys sit in
one row; Godot uses F8 for Stop too.
"""

import sys

import bpy

KEYMAP = ("Window", "EMPTY", "WINDOW")  # (name, space_type, region_type)

# (operator, key, shift): what each shortcut runs.
PLAY_KEYS = (
    ("gamestudio.play", "F5", False),
    ("gamestudio.pause", "F6", False),
    ("gamestudio.step", "F7", False),
    ("gamestudio.stop", "F8", False),
)

# Blender defaults the play keys replace: none (GameStudio Editor's template used to turn
# Shift+F5 off; editor/shell/tools/build_template.py still reads this).
SHADOWED_DEFAULTS = ()

_items = []  # (keymap, item) added to the add-on key configuration


def shortcut_text(idname: str, platform: str | None = None) -> str:
    """The shortcut of a play operator as Blender spells it ("F5", "Shift F5"), or "". On a Mac the
    F keys are media keys unless Fn is held (or the keyboard setting "Use F1, F2, etc. keys as
    standard function keys" is on): "Fn F5"."""
    mac = (platform or sys.platform) == "darwin"
    for op, key, shift in PLAY_KEYS:
        if op == idname:
            key = f"Fn {key}" if mac else key
            return f"Shift {key}" if shift else key
    return ""


def registered_items() -> list:
    return list(_items)


def register():
    wm = getattr(bpy.context, "window_manager", None)
    kc = wm.keyconfigs.addon if wm is not None else None
    if kc is None:  # e.g. --background without a window manager
        return
    name, space_type, region_type = KEYMAP
    km = kc.keymaps.new(name=name, space_type=space_type, region_type=region_type)
    for idname, key, shift in PLAY_KEYS:
        _items.append((km, km.keymap_items.new(idname, key, "PRESS", shift=shift)))


def unregister():
    for km, kmi in _items:
        try:
            km.keymap_items.remove(kmi)
        except (ReferenceError, RuntimeError):
            pass  # already gone with its key configuration
    _items.clear()


def draw_preferences(layout, context) -> None:
    """The play shortcuts, editable, for the add-on preferences."""
    import rna_keymap_ui

    box = layout.box()
    box.label(text="Play Shortcuts", icon="KEYINGSET")
    row = box.row()
    row.active = False
    row.label(text="Play, pause, step and stop the game from any editor")
    kc_user = context.window_manager.keyconfigs.user
    km_user = kc_user.keymaps.get(KEYMAP[0]) if kc_user else None
    if km_user is None:
        box.label(text="Key configuration not available")
        return
    col = box.column()
    for _km, kmi in _items:
        user_item = next((item for item in km_user.keymap_items
                          if item.idname == kmi.idname and item.type == kmi.type and item.shift == kmi.shift), None)
        if user_item is None:
            user_item = next((item for item in km_user.keymap_items if item.idname == kmi.idname), None)
        if user_item is not None:
            col.context_pointer_set("keymap", km_user)
            rna_keymap_ui.draw_kmi([], kc_user, km_user, user_item, col, 0)
