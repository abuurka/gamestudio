# SPDX-License-Identifier: GPL-3.0-or-later
"""gs.gameui: build game menus and HUDs in ui/*.blend (docs/GAME-UI.md §6, §7).

The API (agents use it through run_python as gs.gameui; the operators and the bridge's ui_* methods
call it too). Coordinates are canvas px, origin top-left, y down; `at` is the element's origin,
absolute in the canvas; colours are "#RRGGBB" / "#RRGGBBAA" (sRGB) or linear RGBA lists:

    ui_file(root=None) -> Path / is_ui_file(path=None) -> bool
    new_ui_file(root=None, canvases=()) -> Path
    canvas(name, kind="menu", open="manual", pause=True, pause_menu=False, back="none", scale_mode="fit",
           backdrop=None, layer=0, size=(1920, 1080), focus=None) -> Scene   # focus: the button selected first
    text(canvas, name, body, at, size=48, font="", color="#FFFFFF", align="center", valign="center", box=None,
         parent=None, anchor=None, z=None, outline=0, outline_color="#000000", shadow=None) -> Object
    image(canvas, name, path, at, width=None, height=None, parent=None, anchor=None, z=None, opacity=1.0)
    panel(canvas, name, at, size, color="#000000AA", radius=0, border=0, border_color="#FFFFFF", parent, anchor, z,
          image=None, slice=None)                    # image: drawn instead of a colour; slice [l, r, t, b]: 9-slice
    button(canvas, name, label, at, size=(360, 84), action="event", target="", font="", text_size=40,
           color="#FFFFFF33", text_color="#FFFFFF", focus_color="#FFD400", focus_text_color="#17171C", radius=42,
           parent=None, anchor=None, z=None, order=0, sound="", focus_sound="") -> Object
    bar(canvas, name, at, size, value="", max=1.0, color="#4CAF50", back_color="#00000080", radius=0,
        direction="right", parent=None, anchor=None, z=None) -> Object
    group(canvas, name, at, parent=None, anchor=None, z=None) -> Object
    icons(canvas, name, at, count=3, value="lives", image="assets/ui/heart.png", empty="assets/ui/heart-empty.png",
          size=52, spacing=12, parent, anchor, z) -> Object            # hearts: icon i shows while value >= i
    slider(canvas, name, at, size=(360, 28), value="audio.music", min=0, max=1, step=0.1, color="#FFD400",
           back_color="#FFFFFF33", handle=True, handle_color="#FFFFFF", radius, direction="right", parent, anchor,
           z, order=0, sound="") -> Object
    toggle(canvas, name, at, value, size=(96, 48), on_color="#FFD400", off_color="#FFFFFF33",
           knob_color="#FFFFFF", parent, anchor, z, order=0, sound="") -> Object
    model(canvas, name, at, size=(320, 320), source=None, kit=None, kit_params=None, asset=None,
          view="threeQuarter", zoom=1, spin=0.6, clip="", light="studio", background=None, parent, anchor, z)
                                                     # a 3D model in the UI (§11.1): a kit, an asset or objects;
                                                     # source "scenes/main.blend:Player" links a level's object
    animate(canvas, open="pop", close="fade", duration=0.25, loop=None) -> Scene   # open/close presets (§10.5)
    update(canvas, name, **changes) -> Object        # the ui_set tool: at, z, size, body, font, text_size, loop, ...
    set_font(target, path) -> VectorFont / remove(canvas, name=None) -> [names]
    describe(canvas=None) -> dict / elements(canvas) -> [Object] / to_px(scene, xy) / from_px(scene, px)
    text_rect(scene, obj) -> [x, y, w, h] | None     # a text's measured box in canvas px (approximate)
    strings(locale=None) -> dict                     # the ui_strings tool: the UI's texts and a language's table
    translate(locale, strings, name=None, remove=()) -> dict   # ui_translate: write ui/locales/<locale>.json

Every element call also takes visible=True (False: the element starts hidden, "Disable in Renders") and
loop=None ("pulse", "bob" or "blink": that element alone loops while the canvas is open; "none" removes it).
panel() also takes shader and shader_values (a game shader on the panel's own material, §11.4; colour fields take
"#RRGGBB" / "#RRGGBBAA" too, made linear), text()
project_name (True: its body is the game's name, §11.7), and a button's action "locale" switches the language
(target: a code such as "fr", "" the next one).
Each element call creates the element or updates the one of that name (None keeps a value on update).
Errors are GameUIError (a ValueError) with a message for the user or agent.

UI (§7): ops.py (Edit Game UI, Back to Level, Shift+A > Game UI, New Game UI Screen, Show in Game, Frame
Canvas, Choose Font, the 3D view setup when a UI file opens), panels.py (Scene > Game UI Screen, the
Layout box of Object > Game, Text > Font), overlay.py (the selected element's anchor in the viewport, and the
views of 3D models).
"""

from ..core.gameui import from_px, is_ui_file, to_px, ui_file  # noqa: F401  (part of the API)
from .build import (GameUIError, animate, animation_settings, animations, bar, button, canvas,  # noqa: F401
                    canvas_names, describe, describe_canvas, describe_element, elements, find_element, group, icons,
                    image, in_ui_file, kind_of, model, panel, remove, resolve_anchor, set_font, slider, text, text_rect,
                    toggle, update)
from .files import new_ui_file  # noqa: F401
from .locales import strings, translate  # noqa: F401

_SUBMODULES = ("ops", "panels", "overlay")


def _modules():
    import importlib

    return [importlib.import_module(f".{name}", __name__) for name in _SUBMODULES]


def register():
    for mod in _modules():
        mod.register()


def unregister():
    for mod in reversed(_modules()):
        mod.unregister()
