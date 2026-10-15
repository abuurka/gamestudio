# SPDX-License-Identifier: GPL-3.0-or-later
"""The gs.gameui builder: canvases (screens) and their elements in the open UI file (docs/GAME-UI.md §6).

Coordinates are canvas pixels: origin at the frame's top-left corner, y down. `at` is an element's
origin in the canvas, for children too; boxes (panels, images, buttons, bars) are centred on it and
text is aligned around it. Colours are "#RRGGBB" / "#RRGGBBAA" (sRGB) or linear RGBA lists.

Each element function creates the element, or updates it when an object of that name is already in
the canvas (a different kind is an error). Arguments left at None take the documented default when
the element is created and keep the element's current value when it is updated, so the same call
builds and changes an element. A call checks everything before it changes anything.

Elements made here carry the custom property gs_ui_kind (text, image, panel, button, bar, group,
icons, slider, toggle, model); the parts of buttons, bars, sliders, toggles, icon rows and bordered panels
carry gs_ui_part (bg, focus, label, focus_label, fill, border, handle, on, off, knob, slot<i>,
icon<i>), so later calls find them. A new element's Z (world Z is the draw order) is above every
element of its canvas, and the parts of one element sit 0.001 apart above their owner.

A 3D model (model(), docs/GAME-UI.md §11.1) is an empty with UiModel whose children are the model: a kit, an
asset instance, objects of the UI file, or an object of a level linked with its children ("scenes/main.blend:
Player": a library link gathered in a collection of its own, gs_ui_link, and instanced, so it follows the level).
They are not elements (is_element() leaves out everything inside a model): the game draws them lit, in a small view
of their own. In Blender they hang below the empty's Z, scaled to fit its view, so they cover what is drawn under
the model and nothing above it.

A panel with a game shader (panel(shader=...), §11.4) gets a material of its own (gs_ui_shaded), a copy of the
shared colour or image material, so the shader and its values never reach other panels.

animate() writes a canvas's open/close/loop presets (docs/GAME-UI.md §10.5) as keyframes on its
top-level elements and timeline markers. It tags what it made (gs_ui_anim on the canvas scene and on
each keyed object), so running it again replaces only those, and builder calls that move a keyed
element re-key it from its new place. An element can have a loop of its own too (loop=pulse/bob/blink on the
element functions and update()), keyed on it alone over the canvas's loop range and kept by animate().
"""

from __future__ import annotations

import json
import math
import pathlib
from typing import NamedTuple

import bpy
from mathutils import Matrix, Vector

from ..core import components, gameui as core, project, registry, schema
from ..core.log import get_logger
from . import files

log = get_logger(__name__)

KINDS = ("text", "image", "panel", "button", "bar", "group", "icons", "slider", "toggle", "model")
GROUP_KINDS = ("button", "bar", "group", "icons", "slider", "toggle", "model")  # elements that are empties
KIND_PROP = "gs_ui_kind"
PART_PROP = "gs_ui_part"
SIZE_PROP = "gs_ui_size"        # [w, h] design px of panels, buttons, bars, sliders, toggles and icon rows
RADIUS_PROP = "gs_ui_radius"    # corner radius in px
BORDER_PROP = "gs_ui_border"    # border width in px
COUNT_PROP = "gs_ui_count"      # icons: how many
ICON_SIZE_PROP = "gs_ui_icon_size"  # icons: one icon's size in px
SPACING_PROP = "gs_ui_spacing"  # icons: px between two icons
COLOR_KEY = "gs_ui_color"       # on the shared flat materials: their linear RGBA
TEXTURE_KEY = "gs_ui_texture"   # on the shared textured materials: their image's name
SHADED_KEY = "gs_ui_shaded"     # on a shaded panel's own material (a copy of a shared one): True
PROJECT_NAME_PROP = "gs_ui_project_name"  # a text whose body is the game's name (§11.7)
ANIM_PROP = "gs_ui_anim"        # animate(): its settings (canvas scene) and the keys it made (objects), JSON
PRESET_ACTION = "gs_ui_preset"  # on an action animate() created

ELEMENT_TYPES = {"FONT", "MESH", "CURVE", "SURFACE", "EMPTY"}
Z_STEP = 0.01
PART_Z = 0.001
MIN_Z, MAX_Z = -80.0, core.CAMERA_HEIGHT - 0.5  # inside the canvas camera's clip range
SNAP_PX = 10.0
GROUP_DISPLAY_SIZE = 0.2

ANCHORS = ("auto", "center", "top-left", "top", "top-right", "left", "right", "bottom-left", "bottom", "bottom-right")
STRETCHES = ("none", "width", "height", "both", "cover")
# Where an anchor sits on the screen, as fractions of its width and height.
ANCHOR_POINTS = {"center": (0.5, 0.5), "top-left": (0.0, 0.0), "top": (0.5, 0.0), "top-right": (1.0, 0.0),
                 "left": (0.0, 0.5), "right": (1.0, 0.5), "bottom-left": (0.0, 1.0), "bottom": (0.5, 1.0),
                 "bottom-right": (1.0, 1.0)}
ALIGNS = ("left", "center", "right", "justify", "flush")
VALIGNS = ("top", "center", "bottom", "top-baseline", "bottom-baseline")
DIRECTIONS = ("right", "left", "up", "down")
FOCUS_STATE = "focusedOrPressed"  # button art that shows while the button is focused or held down
MENU_BACKDROP = (0.0, 0.0, 0.0, 0.6)

TEXT_DEFAULTS = {"size": 48, "font": "", "color": "#FFFFFF", "align": "center", "valign": "center",
                 "outline": 0, "outline_color": "#000000"}
IMAGE_DEFAULTS = {"opacity": 1.0}
PANEL_DEFAULTS = {"color": "#000000AA", "radius": 0, "border": 0, "border_color": "#FFFFFF"}
BUTTON_DEFAULTS = {"size": (360, 84), "action": "event", "target": "", "font": "", "text_size": 40,
                   "color": "#FFFFFF33", "text_color": "#FFFFFF", "focus_color": "#FFD400",
                   "focus_text_color": "#17171C", "radius": 42, "order": 0}
BAR_DEFAULTS = {"value": "", "max": 1.0, "color": "#4CAF50", "back_color": "#00000080", "radius": 0,
                "direction": "right"}
# The accent of the controls is the buttons' focus yellow; their tracks are the buttons' translucent white.
ICONS_DEFAULTS = {"count": 3, "value": "lives", "image": "assets/ui/heart.png", "empty": "assets/ui/heart-empty.png",
                  "size": 52, "spacing": 12}
SLIDER_DEFAULTS = {"size": (360, 28), "value": "audio.music", "min": 0.0, "max": 1.0, "step": 0.1,
                   "color": "#FFD400", "back_color": "#FFFFFF33", "handle": True, "handle_color": "#FFFFFF",
                   "direction": "right", "order": 0}
TOGGLE_DEFAULTS = {"size": (96, 48), "on_color": "#FFD400", "off_color": "#FFFFFF33", "knob_color": "#FFFFFF",
                   "order": 0}
MODEL_DEFAULTS = {"size": (320, 320)}  # the other settings are UiModel's defaults
DEFAULTS = {"text": TEXT_DEFAULTS, "image": IMAGE_DEFAULTS, "panel": PANEL_DEFAULTS, "button": BUTTON_DEFAULTS,
            "bar": BAR_DEFAULTS, "group": {}, "icons": ICONS_DEFAULTS, "slider": SLIDER_DEFAULTS,
            "toggle": TOGGLE_DEFAULTS, "model": MODEL_DEFAULTS}
MODEL_FIELDS = ("view", "zoom", "spin", "clip", "light", "background")  # model() keywords = UiModel fields
MODEL_SOURCES = ("source", "kit", "asset")  # what a model shows: one of them
MODEL_FILL = 0.8  # in Blender, the model's longest side is this much of its view's shorter side
LINK_PROP = "gs_ui_link"  # on a model's link collection: "<project-relative .blend>:<Object>", what it links
LINK_BASIS_PROP = "gs_ui_link_basis"  # on the instance of it: the linked root's place and turn it cancels (16 floats)
LINK_SEPARATOR = ".blend:"  # model source "scenes/main.blend:Player"
VIEWS = ("threeQuarter", "front", "side", "top")
LIGHTS = ("studio", "soft", "flat")
MAX_ICONS = 20
HANDLE_SCALE = 1.5  # a slider's round handle is this many times the bar's thickness
# Python keyword -> UiCanvas field.
CANVAS_FIELDS = {"kind": "kind", "open": "open", "pause": "pause", "pause_menu": "pauseMenu", "back": "back",
                 "scale_mode": "scaleMode", "backdrop": "backdrop", "layer": "layer"}
UI_COMPONENTS = ("UiCanvas", "UiButton", "UiAnchor", "UiText", "UiBar", "UiState", "UiShow", "UiSlider", "UiToggle",
                 "UiSlice", "UiModel")
CONTROLS = ("UiButton", "UiSlider", "UiToggle")  # focusable: in the navigation order (§10.4)
SLICE_SIDES = ("left", "right", "top", "bottom")
_ALIGN_Y = {"top": "TOP", "center": "CENTER", "bottom": "BOTTOM", "top-baseline": "TOP_BASELINE",
            "bottom-baseline": "BOTTOM_BASELINE"}
UNSET = object()  # update(): "not given" for keys whose None means something


class GameUIError(ValueError):
    """A gs.gameui call was refused: `message` says why, `hint` what to do, `code` names the case."""

    def __init__(self, message: str, hint: str | None = None, code: str | None = None):
        super().__init__(message + (f" {hint}" if hint else ""))
        self.message = message
        self.hint = hint
        self.code = code


# --- The open UI file, canvases and their elements ---------------------------------------------

_ui_file_cache = {"path": None, "value": False}


def in_ui_file() -> bool:
    """is_ui_file() for the open file, cached per file path (draw code calls it on every redraw)."""
    path = bpy.data.filepath
    if _ui_file_cache["path"] != path:
        _ui_file_cache.update(path=path, value=core.is_ui_file(path) if path else False)
    return _ui_file_cache["value"]


def require_ui_file() -> None:
    if not core.is_ui_file():
        raise GameUIError("The open file is not a UI file (a .blend in the project's ui/ folder).",
                          hint="Open ui/ui.blend first: GameStudio > Edit Game UI, or the ui_open tool.",
                          code="ui_file_not_open")


def need_component(name: str) -> None:
    if registry.schema_ref(name) is None:
        missing = [n for n in UI_COMPONENTS if registry.schema_ref(n) is None]
        raise GameUIError(
            f"The game UI components ({', '.join(missing)}) are not in the project's component registry.",
            hint="Start the dev server: it lists the engine's built-in components in .gamestudio/components.json "
                 "(update @gamestudio/engine if they still do not appear), then try again.",
            code="ui_components_missing")


def canvas_names() -> list[str]:
    return [s.name for s in core.canvas_scenes()]


def get_canvas(canvas) -> bpy.types.Scene:
    """The canvas scene for a name or a Scene; GameUIError with the known names otherwise."""
    if isinstance(canvas, bpy.types.Scene):
        return canvas
    if not isinstance(canvas, str) or not canvas:
        raise GameUIError(f"canvas must be a canvas (scene) name, got {canvas!r}")
    scene = bpy.data.scenes.get(canvas)
    if scene is None or scene.name.startswith("gs_"):
        names = canvas_names()
        raise GameUIError(f"No canvas named '{canvas}'.{schema.suggest(canvas, names)}",
                          hint=f"Canvases: {', '.join(names)}." if names else "Create one with canvas(name).",
                          code="no_canvas")
    return scene


def canvas_of(obj) -> bpy.types.Scene | None:
    """The canvas an object belongs to (its first scene that is not a gs_ one)."""
    for scene in obj.users_scene:
        if not scene.name.startswith("gs_"):
            return scene
    return None


def is_model(obj) -> bool:
    """A 3D model element (§11.1): an empty with an enabled UiModel, as the export sees it (or one model() is
    making)."""
    if obj is None or obj.type != "EMPTY":
        return False
    if components.has_component(obj, "UiModel"):
        return components.is_enabled(obj, "UiModel")
    return obj.get(KIND_PROP) == "model"


def model_of(obj):
    """The model element obj is part of the 3D content of (its nearest UiModel ancestor), or None."""
    parent = obj.parent
    while parent is not None:
        if is_model(parent):
            return parent
        parent = parent.parent
    return None


def is_element(obj, scene=None) -> bool:
    """A canvas element: an object of a type the export draws, not the canvas camera, not inside a 3D model."""
    scene = scene or canvas_of(obj)
    return (scene is not None and obj.type in ELEMENT_TYPES and obj is not scene.camera
            and model_of(obj) is None)


def elements(canvas) -> list:
    """The canvas's elements, parents before children, siblings by Z then name (the draw order)."""
    scene = get_canvas(canvas)
    members = [o for o in scene.objects if is_element(o, scene)]
    present = set(members)
    out = []

    def visit(objs):
        for obj in sorted(objs, key=lambda o: (round(world_matrix(o).translation.z, 4), o.name)):
            out.append(obj)
            visit([c for c in obj.children if c in present])

    visit([o for o in members if o.parent is None or o.parent not in present])
    return out


def kind_of(obj) -> str:
    """text, image, panel, button, bar, group, icons, slider, toggle, model; mesh for other meshes, curves and
    surfaces; else other."""
    kind = obj.get(KIND_PROP)
    if kind in KINDS:
        return "model" if kind == "group" and is_model(obj) else kind  # UiModel added to a group by hand
    if obj.type == "FONT":
        return "text"
    if obj.type == "EMPTY":
        if obj.empty_display_type == "IMAGE":
            return "image"
        for component, name in (("UiModel", "model"), ("UiButton", "button"), ("UiBar", "bar"),
                                ("UiSlider", "slider"), ("UiToggle", "toggle")):
            if components.has_component(obj, component):
                return name
        return "group"
    if obj.type in ("MESH", "CURVE", "SURFACE"):
        return "mesh"
    return "other"


def export_type(obj) -> str:
    """The element type export writes (§2.2, §11.1): text, image, mesh, model or group."""
    if obj.type == "FONT":
        return "text"
    if obj.type == "EMPTY":
        if obj.empty_display_type == "IMAGE" and obj.data is not None:
            return "image"
        return "model" if components.is_enabled(obj, "UiModel") else "group"
    return "mesh"


def find_element(canvas, name):
    """The element called `name` in a canvas, or GameUIError (with suggestions)."""
    scene = get_canvas(canvas)
    obj = bpy.data.objects.get(name) if isinstance(name, str) else name
    if isinstance(obj, bpy.types.Object) and is_element(obj, scene) and scene in obj.users_scene:
        return obj
    names = [o.name for o in scene.objects if is_element(o, scene)]
    raise GameUIError(f"Canvas '{scene.name}' has no element '{getattr(obj, 'name', name)}'."
                      f"{schema.suggest(str(name), names)}", code="no_element")


def _existing(scene, name: str):
    obj = bpy.data.objects.get(name)
    if obj is None:
        return None
    if obj is scene.camera:
        raise GameUIError(f"'{name}' is the canvas camera; choose another element name.")
    if scene not in obj.users_scene:
        where = canvas_of(obj)
        raise GameUIError(f"'{name}' is already an object in {f'canvas {where.name!r}' if where else 'this file'}. "
                          "Names are unique in a UI file: choose another name.")
    if obj.type not in ELEMENT_TYPES:
        raise GameUIError(f"'{name}' is a {obj.type.lower()} object, not a UI element.")
    if model_of(obj) is not None:
        raise GameUIError(f"'{name}' is part of the 3D model '{model_of(obj).name}', not a UI element; choose "
                          "another name.")
    return obj


def _check_name(name) -> str:
    if not isinstance(name, str) or not name.strip():
        raise GameUIError(f"name must be a non-empty string, got {name!r}")
    return name


def free_name(base: str) -> str:
    """base, or base2, base3 ... whichever no object has yet."""
    if bpy.data.objects.get(base) is None:
        return base
    n = 2
    while bpy.data.objects.get(f"{base}{n}") is not None:
        n += 1
    return f"{base}{n}"


def _writable(obj) -> None:
    try:
        components.check_writable(obj)
    except components.ReadOnlyObjectError as exc:
        raise GameUIError(str(exc)) from None


# --- Geometry ------------------------------------------------------------------------------------


def world_matrix(obj) -> Matrix:
    """obj's world matrix from its parent chain, current even before the depsgraph has run."""
    m = obj.matrix_basis.copy()
    if obj.parent is not None:
        m = world_matrix(obj.parent) @ obj.matrix_parent_inverse @ m
    return m


def origin_px(scene, obj) -> tuple[float, float]:
    t = world_matrix(obj).translation
    return core.to_px(scene, (t.x, t.y))


def _place(obj, world: Vector) -> None:
    """Move obj so its origin is at `world`, whatever its parent."""
    if obj.parent is None:
        obj.location = world
        return
    parent = world_matrix(obj.parent) @ obj.matrix_parent_inverse
    obj.location = parent.inverted_safe() @ world


def move(scene, obj, at=None, z=None) -> None:
    """Put obj's origin at canvas px `at` and/or world Z `z` (None keeps that coordinate)."""
    current = world_matrix(obj).translation
    x, y = core.from_px(scene, at) if at is not None else (current.x, current.y)
    _place(obj, Vector((x, y, current.z if z is None else z)))


def _sync_matrix(obj) -> None:
    # Objects not in the evaluated scene keep a stale matrix_world; tools that read it (to_px of the
    # camera, the overlay) see the new transform at once.
    obj.matrix_world = world_matrix(obj)


def top_z(scene, exclude=()) -> float | None:
    zs = [world_matrix(o).translation.z for o in scene.objects if is_element(o, scene) and o not in exclude]
    return max(zs) if zs else None


def next_z(scene) -> float:
    top = top_z(scene)
    return 0.0 if top is None else min(round(top + Z_STEP, 4), MAX_Z)


def resolve_anchor(scene, obj) -> tuple[str, str, bool]:
    """(anchor, stretch, auto) of a top-level element: auto picks the anchor from where the origin sits,
    in thirds of the design size (§3.2)."""
    anchor, stretch = "auto", "none"
    if components.has_component(obj, "UiAnchor") and registry.schema_ref("UiAnchor") is not None:
        values = components.get_values(obj, "UiAnchor")
        anchor, stretch = values.get("anchor", "auto"), values.get("stretch", "none")
    if anchor != "auto":
        return anchor, stretch, False
    w, h = core.design_size(scene)
    x, y = origin_px(scene, obj)
    col = "left" if x < w / 3 else "right" if x > 2 * w / 3 else ""
    row = "top" if y < h / 3 else "bottom" if y > 2 * h / 3 else ""
    return "-".join(p for p in (row, col) if p) or "center", stretch, True


def anchor_point_px(scene, anchor: str) -> tuple[float, float]:
    fx, fy = ANCHOR_POINTS.get(anchor, (0.5, 0.5))
    w, h = core.design_size(scene)
    return fx * w, fy * h


def outline(w: float, h: float, r: float, segments: int) -> list[tuple[float, float]]:
    """Counter-clockwise points of a w x h rectangle centred on the origin with corner radius r."""
    if r <= 1e-9 or segments <= 0:
        return [(-w / 2, -h / 2), (w / 2, -h / 2), (w / 2, h / 2), (-w / 2, h / 2)]
    points = []
    corners = ((w / 2 - r, -h / 2 + r, -90.0), (w / 2 - r, h / 2 - r, 0.0), (-w / 2 + r, h / 2 - r, 90.0),
               (-w / 2 + r, -h / 2 + r, 180.0))
    for cx, cy, start in corners:
        for i in range(segments + 1):
            a = math.radians(start + 90.0 * i / segments)
            points.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    return points


def corner_segments(radius_px: float) -> int:
    return 0 if radius_px < 0.5 else max(3, min(16, math.ceil(radius_px / 4)))


def _distinct(points, keep=None):
    """Drop points that repeat the previous one (pill-shaped panels), or keep the given indices."""
    if keep is None:
        keep = [i for i, p in enumerate(points)
                if i == 0 or math.dist(p, points[i - 1]) > 1e-7]
        if len(keep) > 1 and math.dist(points[keep[-1]], points[keep[0]]) <= 1e-7:
            keep.pop()
    return [points[i] for i in keep], keep


def _set_mesh(mesh, verts2d, faces, size) -> None:
    w, h = size
    mesh.clear_geometry()
    mesh.from_pydata([(x, y, 0.0) for x, y in verts2d], [], faces)
    uv = mesh.uv_layers.new(name="UVMap") if not mesh.uv_layers else mesh.uv_layers[0]
    for loop in mesh.loops:
        x, y = verts2d[loop.vertex_index]
        uv.data[loop.index].uv = ((x + w / 2) / w if w else 0.0, (y + h / 2) / h if h else 0.0)
    mesh.update()


def rect_mesh(mesh, w: float, h: float, r: float, segments: int) -> None:
    points, _keep = _distinct(outline(w, h, r, segments))
    _set_mesh(mesh, points, [list(range(len(points)))], (w, h))


def ring_mesh(mesh, w: float, h: float, r: float, b: float, segments: int) -> None:
    """The band between a w x h rounded rectangle and the same shape inset by b."""
    outer, keep = _distinct(outline(w, h, r, segments))
    inner, _ = _distinct(outline(max(w - 2 * b, 0.0), max(h - 2 * b, 0.0), max(r - b, 0.0), segments), keep)
    n = len(outer)
    faces = [(i, (i + 1) % n, n + (i + 1) % n, n + i) for i in range(n)]
    _set_mesh(mesh, outer + inner, faces, (w, h))


def slice_mesh(mesh, w: float, h: float, insets, image_size, k: float) -> None:
    """A 9-slice panel of w x h design px centred on the origin (docs/GAME-UI.md §10.6): a 4 x 4 grid of vertices,
    9 quads. The corners keep the image's insets (left, right, top, bottom image px) at 1 image px = 1 design px,
    shrunk together when they do not fit; the edges stretch along one axis and the middle both ways. UVs map each
    quad to its part of the image (v up)."""
    left, right, top, bottom = insets
    iw, ih = image_size
    fx = min(1.0, w / (left + right)) if left + right > 0 else 1.0
    fy = min(1.0, h / (top + bottom)) if top + bottom > 0 else 1.0
    xs = (-w / 2, -w / 2 + left * fx, w / 2 - right * fx, w / 2)
    ys = (h / 2, h / 2 - top * fy, -h / 2 + bottom * fy, -h / 2)  # top row first (Blender y is up)
    us = (0.0, left / iw, 1.0 - right / iw, 1.0)
    vs = (1.0, 1.0 - top / ih, bottom / ih, 0.0)
    mesh.clear_geometry()
    mesh.from_pydata([(x / k, y / k, 0.0) for y in ys for x in xs], [],
                     [(4 * (j + 1) + i, 4 * (j + 1) + i + 1, 4 * j + i + 1, 4 * j + i)
                      for j in range(3) for i in range(3)])
    uv = mesh.uv_layers.new(name="UVMap") if not mesh.uv_layers else mesh.uv_layers[0]
    for loop in mesh.loops:
        uv.data[loop.index].uv = (us[loop.vertex_index % 4], vs[loop.vertex_index // 4])
    mesh.update()


# --- Colours and flat materials ------------------------------------------------------------------


def srgb_to_linear(c: float) -> float:
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def linear_to_srgb(c: float) -> float:
    c = min(max(c, 0.0), 1.0)
    return c * 12.92 if c <= 0.0031308 else 1.055 * c ** (1 / 2.4) - 0.055


def parse_color(value, what: str = "color") -> tuple:
    """Linear RGBA from "#RRGGBB" / "#RRGGBBAA" (sRGB, alpha as is) or 3-4 linear floats in 0-1."""
    problem = f"{what} must be '#RRGGBB' or '#RRGGBBAA' (sRGB) or [r, g, b, a] in 0-1 (linear), got {value!r}"
    if isinstance(value, str):
        text = value.strip().lstrip("#")
        if len(text) in (6, 8):
            try:
                parts = [int(text[i:i + 2], 16) / 255.0 for i in range(0, len(text), 2)]
            except ValueError:
                raise GameUIError(problem) from None
            return (*(srgb_to_linear(c) for c in parts[:3]), parts[3] if len(parts) == 4 else 1.0)
        raise GameUIError(problem)
    if (isinstance(value, (list, tuple)) and len(value) in (3, 4)
            and all(isinstance(c, (int, float)) and not isinstance(c, bool) and math.isfinite(c) and 0 <= c <= 1
                    for c in value)):
        return (*(float(c) for c in value[:3]), float(value[3]) if len(value) == 4 else 1.0)
    raise GameUIError(problem)


def to_hex(rgba) -> str:
    """"#RRGGBB" (sRGB) for an opaque linear colour, "#RRGGBBAA" otherwise."""
    rgb = "".join(f"{round(linear_to_srgb(c) * 255):02X}" for c in rgba[:3])
    alpha = rgba[3] if len(rgba) > 3 else 1.0
    return f"#{rgb}" if alpha >= 0.9995 else f"#{rgb}{round(min(max(alpha, 0.0), 1.0) * 255):02X}"


def flat_material(rgba) -> bpy.types.Material:
    """The shared unlit material for a colour (§3.5): Emission, mixed with Transparent BSDF when alpha < 1
    (alpha = 1 - factor), drawn Blended. Material Preview shows it as the game draws it."""
    key = ",".join(f"{c:.4f}" for c in rgba)
    for mat in bpy.data.materials:
        # Still that colour: someone may have edited the shared material in Blender since. One given a game
        # shader by hand is no longer the plain colour.
        if mat.get(COLOR_KEY) == key and mat.library is None and _same_color(material_color(mat), rgba) \
                and not material_shader(mat):
            return mat
    mat = bpy.data.materials.new(f"UI {to_hex(rgba)}")
    mat[COLOR_KEY] = key
    mat.diffuse_color = rgba
    mat.use_backface_culling = False
    tree = mat.node_tree
    if tree is None and hasattr(mat, "use_nodes"):  # Blender < 5.0
        mat.use_nodes = True
        tree = mat.node_tree
    tree.nodes.clear()
    out = tree.nodes.new("ShaderNodeOutputMaterial")
    out.location = (300, 0)
    emission = tree.nodes.new("ShaderNodeEmission")
    emission.location = (-200, 60)
    emission.inputs["Color"].default_value = (*rgba[:3], 1.0)
    emission.inputs["Strength"].default_value = 1.0
    if rgba[3] < 1.0:
        transparent = tree.nodes.new("ShaderNodeBsdfTransparent")
        transparent.location = (-200, -100)
        mix = tree.nodes.new("ShaderNodeMixShader")
        mix.location = (80, 0)
        mix.inputs[0].default_value = 1.0 - rgba[3]
        tree.links.new(emission.outputs[0], mix.inputs[1])
        tree.links.new(transparent.outputs[0], mix.inputs[2])
        tree.links.new(mix.outputs[0], out.inputs["Surface"])
        mat.surface_render_method = "BLENDED"
    else:
        tree.links.new(emission.outputs[0], out.inputs["Surface"])
    return mat


def texture_material(img) -> bpy.types.Material:
    """The shared unlit material that draws an image (§3.5), for textured and 9-slice panels: an Image Texture
    into Emission, its alpha mixing in a Transparent BSDF, drawn Blended. Material Preview shows it as the game
    draws it."""
    for mat in bpy.data.materials:
        if mat.get(TEXTURE_KEY) == img.name and mat.library is None and material_image(mat) is img \
                and not material_shader(mat):
            return mat
    mat = bpy.data.materials.new(f"UI {img.name}")
    mat[TEXTURE_KEY] = img.name
    mat.use_backface_culling = False
    tree = mat.node_tree
    if tree is None and hasattr(mat, "use_nodes"):  # Blender < 5.0
        mat.use_nodes = True
        tree = mat.node_tree
    tree.nodes.clear()
    out = tree.nodes.new("ShaderNodeOutputMaterial")
    out.location = (400, 0)
    tex = tree.nodes.new("ShaderNodeTexImage")
    tex.location = (-420, 0)
    tex.image = img
    tex.extension = "EXTEND"  # no bleeding from the opposite edge at the panel's border
    emission = tree.nodes.new("ShaderNodeEmission")
    emission.location = (-100, 60)
    emission.inputs["Strength"].default_value = 1.0
    transparent = tree.nodes.new("ShaderNodeBsdfTransparent")
    transparent.location = (-100, -100)
    mix = tree.nodes.new("ShaderNodeMixShader")  # factor = the image's alpha: Transparent first, Emission second
    mix.location = (180, 0)
    tree.links.new(tex.outputs["Color"], emission.inputs["Color"])
    tree.links.new(tex.outputs["Alpha"], mix.inputs[0])
    tree.links.new(transparent.outputs[0], mix.inputs[1])
    tree.links.new(emission.outputs[0], mix.inputs[2])
    tree.links.new(mix.outputs[0], out.inputs["Surface"])
    mat.surface_render_method = "BLENDED"
    return mat


def material_image(mat):
    """The image a material draws (its first Image Texture with an image), or None."""
    tree = getattr(mat, "node_tree", None) if mat is not None else None
    if tree is None:
        return None
    return next((n.image for n in tree.nodes if n.type == "TEX_IMAGE" and n.image is not None), None)


def panel_image(obj):
    """The image a mesh panel draws (material slot 0), or None for a flat one."""
    slots = getattr(obj.data, "materials", None) if obj.type == "MESH" else None
    return material_image(slots[0]) if slots and len(slots) else None


def _linked(socket):
    return socket.links[0].from_node if socket.is_linked and socket.links else None


def material_color(mat) -> tuple | None:
    """A material's flat colour as export reads it (§3.5): Emission/Background colour (in a Mix Shader with a
    Transparent BSDF, alpha is the factor when Transparent is the first shader, else 1 - factor, and 1 when the
    factor is linked), else Principled Base Color and Alpha, else the viewport colour."""
    if mat is None:
        return None
    tree = mat.node_tree
    out = None
    if tree is not None:
        outputs = [n for n in tree.nodes if n.type == "OUTPUT_MATERIAL"]
        out = next((n for n in outputs if n.is_active_output), outputs[0] if outputs else None)
    shader = _linked(out.inputs["Surface"]) if out is not None else None
    alpha = 1.0
    if shader is not None and shader.type == "MIX_SHADER":
        a, b = _linked(shader.inputs[1]), _linked(shader.inputs[2])
        transparent = next((n for n in (a, b) if n is not None and n.type == "BSDF_TRANSPARENT"), None)
        if transparent is not None:
            factor = shader.inputs[0]
            if not factor.is_linked:
                value = float(factor.default_value)
                alpha = value if transparent is a else 1.0 - value
            shader = b if transparent is a else a
    if shader is not None and shader.type in ("EMISSION", "BACKGROUND"):
        return (*shader.inputs["Color"].default_value[:3], alpha)
    if shader is not None and shader.type == "BSDF_PRINCIPLED":
        return (*shader.inputs["Base Color"].default_value[:3], float(shader.inputs["Alpha"].default_value))
    return tuple(mat.diffuse_color)


def object_color(obj) -> tuple | None:
    slots = getattr(obj.data, "materials", None)
    return material_color(slots[0]) if slots and len(slots) else None


def _set_color(obj, rgba) -> None:
    _set_material(obj, flat_material(rgba))


def _set_material(obj, mat) -> None:
    mats = obj.data.materials
    if len(mats):
        mats[0] = mat
    else:
        mats.append(mat)


# --- Game shaders on panels (§11.4) ---------------------------------------------------------------------


def material_shader(mat) -> str:
    """The game shader a material names (Material > Game), or ""."""
    gs = getattr(mat, "gs", None) if mat is not None else None
    return getattr(gs, "shader", "") or ""


def panel_material(obj):
    """A mesh element's material slot 0, or None."""
    slots = getattr(obj.data, "materials", None) if obj.type == "MESH" else None
    return slots[0] if slots and len(slots) else None


def panel_shader(obj) -> str:
    """The game shader that draws a panel (its material's), or ""."""
    return material_shader(panel_material(obj))


def _shader_hint() -> str:
    known = registry.shader_names()
    if not known:
        return ("The registry has no game shaders yet: start the dev server (it lists the engine's library and "
                "src/shaders/ in .gamestudio/components.json), then try again.")
    return f"Game shaders: {', '.join(known)}. UI materials take the ones marked for UI (Shine, Gradient...)."


def _check_shader(value) -> str:
    """shader: a game shader name of the registry, or "" for none."""
    if value == "":
        return ""
    name = _string(value, "shader")
    if registry.shader_schema_ref(name) is None:
        known = registry.shader_names()
        raise GameUIError(f"Unknown game shader '{name}'.{schema.suggest(name, known)}", hint=_shader_hint())
    return name


def linear_shader_values(name: str, values, what: str = "shader_values") -> dict:
    """values for game shader `name` with the "#RRGGBB" / "#RRGGBBAA" strings of its colour fields made linear RGBA
    lists, as parse_color() makes every UI colour (sRGB hex, alpha as is); everything else as given. A hex string
    that is not a colour is a GameUIError. Values of a shader the registry does not define are returned as they
    are. set_material_shader uses it too."""
    if not isinstance(values, dict) or not name:
        return values
    out = dict(values)
    for key, value in values.items():
        field = registry.get_shader_field(name, key) if isinstance(key, str) else None
        if field is not None and field.get("type") == "color" and isinstance(value, str):
            out[key] = list(parse_color(value, f"{what}.{key}"))
    return out


def _check_shader_values(name: str, values) -> dict:
    """shader_values for shader `name`, checked field by field (colour fields take sRGB hex too, made linear;
    the other values are kept as given)."""
    if not name:
        raise GameUIError("shader_values need a game shader: pass shader with them.", hint=_shader_hint())
    if not isinstance(values, dict):
        raise GameUIError(f"shader_values must be an object of field: value, got {values!r}")
    entry = registry.shader_schema_ref(name)
    if entry is None:
        raise GameUIError(f"Cannot check values for game shader '{name}': the registry does not define it.",
                          hint=_shader_hint())
    values = linear_shader_values(name, values)
    fields = [f["name"] for f in entry["fields"]]
    for key, value in values.items():
        field = registry.get_shader_field(name, key)
        if field is None:
            raise GameUIError(f"Game shader '{name}' has no field {key!r}.{schema.suggest(str(key), fields)}",
                              hint=f"Fields: {', '.join(fields) or '(none)'}.")
        try:
            schema.coerce_value(field, value)
        except (ValueError, TypeError) as exc:
            raise GameUIError(f"shader_values.{key}: {exc}") from None
    return dict(values)


def _copy_shader_values(src, dst, name: str) -> None:
    """Copy shader `name`'s stored values from material src to dst."""
    entry = registry.shader_schema_ref(name)
    a, b = registry.shader_group(src, name), registry.shader_group(dst, name)
    if entry is None or a is None or b is None:
        return
    for field in entry["fields"]:
        setattr(b, field["name"], getattr(a, field["name"]))


def _apply_shader(obj, t: dict, before) -> None:
    """After a panel change: give it the game shader it should have (t["shader"], else the one it had, before:
    (material, shader name) or None) on a material of its own, a copy of the shared one it now has, so the
    shader never reaches other panels. shader "" goes back to the shared colour or image material."""
    from ..core import shaders

    mat = panel_material(obj)
    name = t["shader"] if "shader" in t else (before[1] if before else "")
    if not name:
        if "shader" in t and mat is not None and material_shader(mat):
            img = material_image(mat)
            shared = texture_material(img) if img is not None else flat_material(
                material_color(mat) or parse_color(PANEL_DEFAULTS["color"]))
            _set_material(obj, shared)
            if mat.get(SHADED_KEY) and mat.users == 0:
                bpy.data.materials.remove(mat)
        return
    own = mat
    made = mat is None or not mat.get(SHADED_KEY) or mat.users > 1
    if made:
        base = mat if mat is not None else flat_material(parse_color(PANEL_DEFAULTS["color"]))
        own = base.copy()
        for key in (COLOR_KEY, TEXTURE_KEY):
            if key in own:
                del own[key]
        own[SHADED_KEY] = True
        own.name = f"UI {obj.name}"
        old = before[0] if before else None
        if old is not None and old is not base and before[1] == name:
            own.gs.shader = name  # its colour or image changed: the shader stays, with its values
            _copy_shader_values(old, own, name)
        _set_material(obj, own)
    if "shader" in t or "shader_values" in t:
        try:
            shaders.set_shader(own, name, t.get("shader_values"))
        except ValueError as exc:
            raise GameUIError(str(exc), hint=_shader_hint()) from None
    old = before[0] if before else None
    if old is not None and old is not own and old.get(SHADED_KEY) and old.users == 0:
        bpy.data.materials.remove(old)


# --- Checking arguments ---------------------------------------------------------------------------


def _number(value, what, low=None, high=None, integer=False) -> float:
    ok = isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
    if ok and integer:
        ok = float(value).is_integer()
    if ok and low is not None:
        ok = value >= low
    if ok and high is not None:
        ok = value <= high
    if not ok:
        bounds = (f" from {low} to {high}" if low is not None and high is not None
                  else f" >= {low}" if low is not None else f" <= {high}" if high is not None else "")
        raise GameUIError(f"{what} must be {'a whole number' if integer else 'a number'}{bounds}, got {value!r}")
    return int(value) if integer else float(value)


def _pair(value, what, positive=False) -> tuple[float, float]:
    if not isinstance(value, (list, tuple, Vector)) or len(value) != 2:
        raise GameUIError(f"{what} must be [x, y] in canvas px, got {value!r}" if not positive
                          else f"{what} must be [width, height] in px, got {value!r}")
    low = 1e-3 if positive else None
    return _number(value[0], what, low), _number(value[1], what, low)


def _choice(value, options, what) -> str:
    if not isinstance(value, str) or value not in options:
        raise GameUIError(f"{what} must be one of {', '.join(options)}; got {value!r}."
                          f"{schema.suggest(str(value), list(options))}")
    return value


def _string(value, what) -> str:
    if not isinstance(value, str):
        raise GameUIError(f"{what} must be a string, got {value!r}")
    return value


def _bool(value, what) -> bool:
    if not isinstance(value, bool):
        raise GameUIError(f"{what} must be true or false, got {value!r}")
    return value


def _anchor(value) -> tuple[str | None, str | None]:
    """anchor or (anchor, stretch) -> (anchor, stretch); either may be None (unchanged)."""
    if isinstance(value, str):
        return _choice(value, ANCHORS, "anchor"), None
    if isinstance(value, (list, tuple)) and len(value) == 2:
        a = _choice(value[0], ANCHORS, "anchor") if value[0] is not None else None
        s = _choice(value[1], STRETCHES, "stretch") if value[1] is not None else None
        return a, s
    raise GameUIError(f"anchor must be one of {', '.join(ANCHORS)}, or [anchor, stretch], got {value!r}")


def _box(value):
    """Text box: [w, h] centred on the origin, or [x, y, w, h] (top-left corner, local px, y down);
    an empty list or False removes it."""
    if value is False or (isinstance(value, (list, tuple)) and len(value) == 0):
        return ()
    if isinstance(value, (list, tuple)) and len(value) == 2:
        w, h = _number(value[0], "box width", 0), _number(value[1], "box height", 0)
        return (-w / 2, -h / 2, w, h)
    if isinstance(value, (list, tuple)) and len(value) == 4:
        x, y = _number(value[0], "box x"), _number(value[1], "box y")
        return (x, y, _number(value[2], "box width", 0), _number(value[3], "box height", 0))
    raise GameUIError(f"box must be [width, height] or [x, y, width, height] in px, got {value!r}")


def _shadow(value) -> tuple:
    if isinstance(value, (list, tuple)) and len(value) in (2, 3):
        dx, dy = _number(value[0], "shadow x"), _number(value[1], "shadow y")
        blur = _number(value[2], "shadow blur", 0) if len(value) == 3 else None
        return (dx, dy, blur)
    raise GameUIError(f"shadow must be [x, y] or [x, y, blur] in px, got {value!r}")


def _field_options(component: str, field: str, fallback) -> tuple:
    entry = registry.get_field(component, field)
    return tuple(entry.get("options") or fallback) if entry else tuple(fallback)


def _component_values(component: str, values: dict) -> dict:
    """Coerce component values now, so a bad one fails before anything changes."""
    need_component(component)
    out = {}
    for name, value in values.items():
        try:
            out[name] = schema.coerce_value(registry.get_field(component, name), value)
        except (ValueError, TypeError) as exc:
            raise GameUIError(f"{component}.{name}: {exc}") from None
    return out


def _parent(scene, value, obj=None):
    """parent: an element name of the same canvas, or "" for top level."""
    if value == "":
        return ""
    if isinstance(value, bpy.types.Object):
        value = value.name
    parent = find_element(scene, _string(value, "parent"))
    if obj is not None and (parent is obj or parent in obj.children_recursive):
        raise GameUIError(f"'{obj.name}' cannot be parented to '{parent.name}': that is itself or inside it.")
    if is_model(parent):
        raise GameUIError(f"'{parent.name}' is a 3D model: its children are the model, not UI elements.",
                          hint="Group the model and the element under a group instead.")
    return parent


def _check_common(scene, p: dict, obj=None) -> dict:
    """at, z, parent, anchor and visible, checked and normalised."""
    out = {}
    if "at" in p:
        out["at"] = _pair(p["at"], "at")
    if "z" in p:
        out["z"] = _number(p["z"], "z", MIN_Z, MAX_Z)
    if "parent" in p:
        out["parent"] = _parent(scene, p["parent"], obj)
    if "anchor" in p:
        out["anchor"] = _anchor(p["anchor"])
        need_component("UiAnchor")
        parent = out.get("parent", obj.parent if obj is not None else "")
        if parent not in ("", None):
            raise GameUIError("anchor applies to top-level elements only; this element has a parent "
                              f"('{parent.name}'), which it follows.")
    if "visible" in p:
        out["visible"] = _bool(p["visible"], "visible")
    return out


# --- Applying -------------------------------------------------------------------------------------


def _create(scene, name: str, kind: str):
    if kind == "text":
        data = bpy.data.curves.new(name, "FONT")
    elif kind == "panel":
        data = bpy.data.meshes.new(name)
    else:
        data = None
    obj = bpy.data.objects.new(name, data)
    scene.collection.objects.link(obj)
    if obj.name != name:
        bpy.data.objects.remove(obj)
        raise GameUIError(f"Blender could not name an object '{name}' (too long?); choose a shorter name.")
    if kind in GROUP_KINDS:
        obj.empty_display_type = "PLAIN_AXES"
        obj.empty_display_size = GROUP_DISPLAY_SIZE
    elif kind == "image":
        obj.empty_display_type = "IMAGE"
        obj.empty_image_offset = (-0.5, -0.5)
    obj[KIND_PROP] = kind
    return obj


def _apply_common(scene, obj, c: dict, creating: bool) -> None:
    if "parent" in c:
        parent = c["parent"] or None
        if parent is not obj.parent:
            world = world_matrix(obj)
            obj.parent = parent
            obj.matrix_parent_inverse = Matrix.Identity(4)
            obj.matrix_basis = (world_matrix(parent).inverted_safe() @ world) if parent else world
            if parent is not None and components.has_component(obj, "UiAnchor"):
                components.remove_component(obj, "UiAnchor")  # anchors are for top-level elements only
    if "at" in c or "z" in c:
        move(scene, obj, c.get("at"), c.get("z"))
    if "anchor" in c:
        anchor, stretch = c["anchor"]
        values = {k: v for k, v in (("anchor", anchor), ("stretch", stretch)) if v is not None}
        components.add_component(obj, "UiAnchor", values)
    if "visible" in c:
        obj.hide_render = not c["visible"]
    _sync_matrix(obj)
    for child in obj.children_recursive:
        _sync_matrix(child)


def _part(scene, owner, part: str, kind: str, suffix: str, index: int):
    """The owner's part (a child with gs_ui_part), created at the owner's origin when missing. Its Z above
    the owner is PART_Z * index (world Z is the draw order): an existing part is put back at its place in
    the stack, so elements built before a stacking change are fixed by any update."""
    for child in owner.children:
        if child.get(PART_PROP) == part:
            if abs(child.location.z - PART_Z * index) > 1e-9:
                child.location.z = PART_Z * index
                _sync_matrix(child)
            return child
    obj = _create(scene, free_name(owner.name + suffix), kind)
    obj[PART_PROP] = part
    obj.parent = owner
    obj.matrix_parent_inverse = Matrix.Identity(4)
    obj.location = (0.0, 0.0, PART_Z * index)
    _sync_matrix(obj)
    return obj


def find_part(owner, part: str):
    return next((c for c in owner.children if c.get(PART_PROP) == part), None)


def own_parts(obj) -> list:
    """The parts the builder made for obj (gs_ui_part children, and their parts: a toggle's OnKnob), not those of
    elements inside it."""
    out = []
    for child in obj.children:
        if child.get(PART_PROP):
            out += [child, *own_parts(child)]
    return out


def _drop(obj) -> None:
    """Delete an object (and its children), and its mesh or text data, and the preset action animate() made for
    it, when nothing else uses them."""
    for child in list(obj.children):
        _drop(child)
    data = obj.data
    action = obj.animation_data.action if obj.animation_data is not None else None
    link = _instanced(obj) if obj.instance_type == "COLLECTION" else None
    bpy.data.objects.remove(obj)
    if action is not None and action.get(PRESET_ACTION) and action.users == 0:
        bpy.data.actions.remove(action)
    if link is not None and link.library is None and link.get(LINK_PROP) and link.users == 0:
        _drop_link(link)  # a level object linked for a model (§11.1) that no model shows any more
    if data is not None and data.users == 0:
        if isinstance(data, bpy.types.Mesh):
            bpy.data.meshes.remove(data)
        elif isinstance(data, bpy.types.Curve):
            bpy.data.curves.remove(data)
        elif isinstance(data, bpy.types.Armature):  # a model's rig
            bpy.data.armatures.remove(data)


def _apply_text(scene, obj, t: dict) -> None:
    k = core.px_per_unit(scene)
    cu = obj.data
    box = cu.text_boxes[0]
    # Blender draws a box with a height one text size above its y (docs/GAME-UI.md §3.2, "box"): `box` is
    # that frame, and a new text size keeps the frame where it is.
    frame_top = box.y + cu.size if box.width > 0 and box.height > 0 else None
    if "body" in t:
        cu.body = t["body"]
    if "size" in t:
        cu.size = t["size"] / k
    if "align" in t:
        cu.align_x = t["align"].upper()
    if "valign" in t:
        cu.align_y = _ALIGN_Y[t["valign"]]
    if "box" in t:
        if t["box"]:
            x, y, w, h = t["box"]
            box.x, box.width, box.height = x / k, w / k, h / k
            box.y = -y / k - (cu.size if h > 0 else 0.0)
        else:
            box.x = box.y = box.width = box.height = 0.0
    elif frame_top is not None and "size" in t:
        box.y = frame_top - cu.size
    if "color" in t:
        _set_color(obj, t["color"])
    if "font" in t:
        _assign_font(obj, t["font"])
    if t.get("project_name"):
        obj[PROJECT_NAME_PROP] = True  # New Project from a template gives it the new game's name (§11.7)
    elif "project_name" in t and PROJECT_NAME_PROP in obj:
        del obj[PROJECT_NAME_PROP]
    ui_text = {}
    if "outline" in t:
        ui_text["outline"] = t["outline"]
    if "outline_color" in t and (t.get("outline") or components.has_component(obj, "UiText")):
        ui_text["outlineColor"] = t["outline_color"]
    if "shadow" in t:
        dx, dy, blur = t["shadow"]
        ui_text["shadow"] = (dx, dy)
        if blur is not None:
            ui_text["shadowBlur"] = blur
    if components.has_component(obj, "UiText") or ui_text.get("outline") or any(ui_text.get("shadow") or ()):
        components.add_component(obj, "UiText", ui_text)


def _check_text(t: dict) -> dict:
    out = {}
    if "body" in t:
        out["body"] = _string(t["body"], "body")
    if "size" in t:
        out["size"] = _number(t["size"], "size (text size in px)", 1, 4096)
    if "font" in t:
        out["font"] = files.font_source(t["font"])
    if "color" in t:
        out["color"] = parse_color(t["color"])
    if "align" in t:
        out["align"] = _choice(t["align"], ALIGNS, "align")
    if "valign" in t:
        out["valign"] = _choice(t["valign"], VALIGNS, "valign")
    if "box" in t:
        out["box"] = _box(t["box"])
    if "outline" in t:
        out["outline"] = _number(t["outline"], "outline", 0, 256)
    if "outline_color" in t:
        out["outline_color"] = parse_color(t["outline_color"], "outline_color")
    if "shadow" in t:
        out["shadow"] = _shadow(t["shadow"])
    if "project_name" in t:
        out["project_name"] = _bool(t["project_name"], "project_name")
    if out.get("outline") or (out.get("shadow") and any(out["shadow"][:2])):
        need_component("UiText")
    return out


def _assign_font(obj, source) -> None:
    font = files.load_font(source)
    cu = obj.data
    cu.font = cu.font_bold = cu.font_italic = cu.font_bold_italic = font


def _check_image(t: dict, obj) -> dict:
    out = {}
    if "path" in t:
        out["path"] = files.image_source(t["path"])
    for key in ("width", "height"):
        if key in t:
            out[key] = _number(t[key], key, 1e-3)
    if "opacity" in t:
        out["opacity"] = _number(t["opacity"], "opacity", 0, 1)
    if "path" not in out and obj is None:
        raise GameUIError("image needs path: the image file (project-relative or absolute).")
    return out


def image_size_px(obj, k) -> tuple[float, float]:
    """The image element's width and height in design px (its scale included)."""
    img = obj.data
    iw, ih = (img.size[0], img.size[1]) if img is not None else (1, 1)
    iw, ih = iw or 1, ih or 1
    longer = obj.empty_display_size * k
    w, h = (longer, longer * ih / iw) if iw >= ih else (longer * iw / ih, longer)
    return w * abs(obj.scale.x), h * abs(obj.scale.y)


def _apply_image(scene, obj, t: dict) -> None:
    k = core.px_per_unit(scene)
    if "path" in t:
        img = files.load_image(t["path"])
        if not img.size[0] or not img.size[1]:
            raise GameUIError(f"Blender could not read the image {img.filepath}.")
        obj.data = img
        if "width" not in t and "height" not in t:
            t = {**t, "natural": True}
    img = obj.data
    iw, ih = img.size[0] or 1, img.size[1] or 1
    if t.get("natural") or "width" in t or "height" in t:
        if "width" in t and "height" in t:
            w, h = t["width"], t["height"]
        elif "width" in t:
            w, h = t["width"], t["width"] * ih / iw
        elif "height" in t:
            w, h = t["height"] * iw / ih, t["height"]
        else:
            w, h = float(iw), float(ih)  # 1 image px = 1 design px
        longer = w if iw >= ih else h
        obj.empty_display_size = longer / k
        natural_w, natural_h = (longer, longer * ih / iw) if iw >= ih else (longer * iw / ih, longer)
        obj.scale = (w / natural_w, h / natural_h, 1.0)
    if "opacity" in t:
        obj.color[3] = t["opacity"]
        obj.use_empty_image_alpha = t["opacity"] < 1.0


def _slice(value) -> tuple:
    """slice: [left, right, top, bottom] image px, or one number for all four; [] or False removes it."""
    if value is False or (isinstance(value, (list, tuple)) and len(value) == 0):
        return ()
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return (_number(value, "slice", 0),) * 4
    if isinstance(value, (list, tuple)) and len(value) == 4:
        return tuple(_number(v, f"slice {side}", 0) for v, side in zip(value, SLICE_SIDES))
    raise GameUIError(f"slice must be [left, right, top, bottom] in image px (or one number for all four), "
                      f"got {value!r}")


def _check_panel(t: dict, obj, creating: bool) -> dict:
    out = {}
    if "size" in t:
        out["size"] = _pair(t["size"], "size", positive=True)
    elif creating:
        raise GameUIError("panel needs size: [width, height] in px.")
    if "color" in t:
        out["color"] = parse_color(t["color"])
    if "radius" in t:
        out["radius"] = _number(t["radius"], "radius", 0)
    if "border" in t:
        out["border"] = _number(t["border"], "border", 0)
    if "border_color" in t:
        out["border_color"] = parse_color(t["border_color"], "border_color")
    if "image" in t:
        out["image"] = "" if t["image"] == "" else files.image_source(t["image"])
    if "slice" in t:
        out["slice"] = _slice(t["slice"])
    has = obj is not None and obj.type == "MESH"
    textured = bool(out["image"]) if "image" in out else has and panel_image(obj) is not None
    sliced = bool(out["slice"]) if "slice" in out else has and components.has_component(obj, "UiSlice")
    if out.get("slice") and not textured:
        raise GameUIError("slice needs image: the panel art whose corners keep their size.",
                          hint="Pass image (a png, jpg or webp) with slice, e.g. image='assets/ui/panel.png', "
                               "slice=[24, 24, 24, 24].")
    if textured and "color" in out:
        raise GameUIError("A panel with an image draws the image; color does not apply to it.",
                          hint="Leave color out, or pass image='' to make it a flat panel again.")
    if textured and sliced and (out.get("radius") or out.get("border")):
        raise GameUIError("A 9-slice panel takes its corners and border from its image; radius and border do not "
                          "apply to it.", hint="Leave radius and border out, or slice=[] for a plain image panel.")
    if out.get("slice"):
        need_component("UiSlice")
    if "shader" in t:
        out["shader"] = _check_shader(t["shader"])
    if "shader_values" in t:
        name = out["shader"] if "shader" in out else (panel_shader(obj) if has else "")
        out["shader_values"] = _check_shader_values(name, t["shader_values"])
    return out


def panel_size(obj, k=None) -> tuple[float, float]:
    """A panel's width and height in px, without its object scale."""
    size = obj.get(SIZE_PROP)
    if size is not None and len(size) == 2:
        return float(size[0]), float(size[1])
    if obj.type == "MESH" and len(obj.data.vertices):
        xs = [v.co.x for v in obj.data.vertices]
        ys = [v.co.y for v in obj.data.vertices]
        k = k or core.px_per_unit(canvas_of(obj) or bpy.context.scene)
        return (max(xs) - min(xs)) * k, (max(ys) - min(ys)) * k
    return 0.0, 0.0


def slice_insets(obj) -> tuple | None:
    """A panel's UiSlice insets (left, right, top, bottom image px), or None."""
    if not components.has_component(obj, "UiSlice") or registry.schema_ref("UiSlice") is None:
        return None
    values = components.get_values(obj, "UiSlice")
    return tuple(float(values[side]) for side in SLICE_SIDES)


def _check_insets(insets, img) -> None:
    iw, ih = img.size[0], img.size[1]
    left, right, top, bottom = insets
    if left + right > iw or top + bottom > ih:
        raise GameUIError(f"slice {[_round(v) for v in insets]} does not fit the image '{img.name}' ({iw} x {ih} px): "
                          "left + right must be at most its width, top + bottom at most its height.")


def _load_image(source):
    img = files.load_image(source)
    if not img.size[0] or not img.size[1]:
        raise GameUIError(f"Blender could not read the image {img.filepath}.")
    return img


def _apply_panel(scene, obj, t: dict) -> None:
    if obj.type != "MESH":
        raise GameUIError(f"'{obj.name}' is a {obj.type.lower()}, not a mesh panel.")
    mat = panel_material(obj)
    before = (mat, material_shader(mat)) if material_shader(mat) else None
    _panel_shape(scene, obj, t)
    if before is not None or t.get("shader"):
        _apply_shader(obj, t, before)


def _panel_shape(scene, obj, t: dict) -> None:
    """A panel's mesh, its border ring and its colour or image (the material it shares with panels like it)."""
    k = core.px_per_unit(scene)
    w, h = t.get("size") or panel_size(obj, k)
    # The image and the 9-slice insets it will have, checked before anything changes.
    img = (_load_image(t["image"]) if t["image"] else None) if "image" in t else panel_image(obj)
    insets = (t["slice"] or None) if "slice" in t else slice_insets(obj)
    if img is not None and insets is not None:
        _check_insets(insets, img)
    if "image" in t:
        if img is not None:
            _set_material(obj, texture_material(img))
        else:
            _set_color(obj, t.get("color") or parse_color(PANEL_DEFAULTS["color"]))
            if components.has_component(obj, "UiSlice"):
                components.remove_component(obj, "UiSlice")
            insets = None
    if "slice" in t:
        if t["slice"]:
            components.add_component(obj, "UiSlice", dict(zip(SLICE_SIDES, t["slice"])))
        elif components.has_component(obj, "UiSlice"):
            components.remove_component(obj, "UiSlice")
    reshape = bool({"size", "radius", "border", "image", "slice"} & set(t)) or not len(obj.data.vertices)
    if img is not None and insets is not None:
        if reshape:  # resizing a 9-slice panel rebuilds its quads: the corners keep their size
            slice_mesh(obj.data, w, h, insets, tuple(img.size), k)
            obj[SIZE_PROP] = [w, h]
            obj[RADIUS_PROP] = 0.0
            obj[BORDER_PROP] = 0.0
            ring = find_part(obj, "border")
            if ring is not None:
                _drop(ring)
        return
    r = min(t.get("radius", obj.get(RADIUS_PROP, 0.0)), w / 2, h / 2)
    b = min(t.get("border", obj.get(BORDER_PROP, 0.0)), w / 2, h / 2)
    if reshape:
        segments = corner_segments(r)
        rect_mesh(obj.data, (w - 2 * b) / k, (h - 2 * b) / k, max(r - b, 0.0) / k, segments)
        obj[SIZE_PROP] = [w, h]
        obj[RADIUS_PROP] = r
        obj[BORDER_PROP] = b
        ring = find_part(obj, "border")
        if b > 0:
            border_color = t.get("border_color") or (object_color(ring) if ring else None) \
                or parse_color(PANEL_DEFAULTS["border_color"])
            ring = _part(scene, obj, "border", "panel", "Border", 1)
            ring_mesh(ring.data, w / k, h / k, r / k, b / k, segments)
            ring[SIZE_PROP] = [w, h]
            _set_color(ring, border_color)
        elif ring is not None:
            _drop(ring)
    if "color" in t:
        _set_color(obj, t["color"])
    elif not len(obj.data.materials):
        _set_color(obj, parse_color(PANEL_DEFAULTS["color"]))
    if "border_color" in t and find_part(obj, "border") is not None:
        _set_color(find_part(obj, "border"), t["border_color"])


def _check_button(t: dict, obj=None) -> dict:
    out = {}
    if "label" in t:
        out["label"] = _string(t["label"], "label")
    if "size" in t:
        out["size"] = _pair(t["size"], "size", positive=True)
    if "action" in t:
        out["action"] = _choice(t["action"], _field_options("UiButton", "action", ("event",)), "action")
    if "target" in t:
        out["target"] = _string(t["target"], "target")
    current = components.get_values(obj, "UiButton") if obj is not None and components.has_component(
        obj, "UiButton") and registry.schema_ref("UiButton") is not None else {}
    if out.get("action", current.get("action")) == "locale":
        # A Language button (§11.3): target is the language to switch to, "" the next one.
        from . import locales

        target = out.get("target", current.get("target", ""))
        try:
            out["target"] = locales.normalize(target) if target else ""
        except GameUIError as exc:
            raise GameUIError(f"target {target!r}: {exc.message}",
                              hint="A Language button's target is a language code such as 'fr' or 'pt-BR', or '' "
                                   "for the next language.") from None
    if "font" in t:
        out["font"] = files.font_source(t["font"])
    if "text_size" in t:
        out["text_size"] = _number(t["text_size"], "text_size", 1, 4096)
    for key in ("color", "text_color", "focus_color", "focus_text_color"):
        if key in t:
            out[key] = parse_color(t[key], key)
    if "radius" in t:
        out["radius"] = _number(t["radius"], "radius", 0)
    if "order" in t:
        out["order"] = _number(t["order"], "order", 0, integer=True)
    if "disabled" in t:
        out["disabled"] = _bool(t["disabled"], "disabled")
    for key in SOUND_KEYS:
        if key in t:
            out[key] = files.sound_source(t[key], key)
    if "align" in t:
        out["align"] = _choice(t["align"], ALIGNS, "align")
    if "valign" in t:
        out["valign"] = _choice(t["valign"], VALIGNS, "valign")
    return out


# Python keyword -> UiButton field.
BUTTON_FIELDS = {"action": "action", "target": "target", "order": "order", "disabled": "disabled",
                 "sound": "sound", "focus_sound": "focusSound"}
SOUND_KEYS = ("sound", "focus_sound")


def _button_fields(t: dict) -> dict:
    return {field: t[key] for key, field in BUTTON_FIELDS.items() if key in t}


def _apply_button(scene, obj, t: dict) -> None:
    fields = _button_fields(t)
    if fields or not components.has_component(obj, "UiButton"):
        components.add_component(obj, "UiButton", fields)
    if obj.type != "EMPTY":
        return  # UiButton on another kind of element (ui_set action): no parts to keep in step
    size = t.get("size") or tuple(obj.get(SIZE_PROP) or BUTTON_DEFAULTS["size"])
    obj[SIZE_PROP] = list(size)
    if "radius" in t:
        obj[RADIUS_PROP] = t["radius"]
    radius = obj.get(RADIUS_PROP, BUTTON_DEFAULTS["radius"])
    shape = {"size": size, "radius": radius} if ({"size", "radius"} & set(t) or not obj.children) else {}
    # Stacked Bg < Label < Focus < FocusLabel: the focus art covers the normal label, which would otherwise
    # show through the focus label's anti-aliased edges.
    bg = _part(scene, obj, "bg", "panel", "Bg", 1)
    _apply_panel(scene, bg, {**shape, **({"color": t["color"]} if "color" in t else {})})
    focus = _part(scene, obj, "focus", "panel", "Focus", 3)
    _apply_panel(scene, focus, {**shape, **({"color": t["focus_color"]} if "focus_color" in t else {})})
    if not components.has_component(focus, "UiState"):
        components.add_component(focus, "UiState", {"show": FOCUS_STATE})
    label_keys = {"label": "body", "font": "font", "text_size": "size", "align": "align", "valign": "valign"}
    shared = {dst: t[src] for src, dst in label_keys.items() if src in t}
    label_new = find_part(obj, "label") is None
    label = _part(scene, obj, "label", "text", "Label", 2)
    if label_new:
        shared = {"align": "center", "valign": "center", **shared}
    old_text = None if label_new else object_color(label)
    _apply_text(scene, label, {**shared, **({"color": t["text_color"]} if "text_color" in t else {})})
    text_color = t.get("text_color") or object_color(label)
    focus_label = find_part(obj, "focus_label")
    focus_text = t.get("focus_text_color") or (object_color(focus_label) if focus_label else None)
    if focus_text is None and "text_color" in t:
        focus_text = old_text  # a new text colour only: the focused label keeps the colour it had
    if focus_text is not None and text_color is not None and not _same_color(focus_text, text_color):
        if focus_label is None:
            focus_label = _part(scene, obj, "focus_label", "text", "FocusLabel", 4)
            shared = _text_settings(label)
            components.add_component(focus_label, "UiState", {"show": FOCUS_STATE})
        _apply_text(scene, focus_label, {**shared, "color": focus_text})
    elif focus_label is not None:
        _drop(focus_label)


def _same_color(a, b) -> bool:
    return all(abs(x - y) < 1e-4 for x, y in zip(a, b))


def _text_settings(obj) -> dict:
    """A text object's settings as _apply_text takes them (to copy a label)."""
    cu = obj.data
    k = core.px_per_unit(canvas_of(obj) or bpy.context.scene)
    font = cu.font
    return {"body": cu.body, "size": cu.size * k, "align": cu.align_x.lower(),
            "valign": cu.align_y.lower().replace("_", "-"),
            "font": "" if font is None or font.filepath == "<builtin>" else bpy.path.abspath(font.filepath)}


def _check_bar(t: dict, creating: bool) -> dict:
    out = {}
    if "size" in t:
        out["size"] = _pair(t["size"], "size", positive=True)
    elif creating:
        raise GameUIError("bar needs size: [width, height] in px.")
    if "value" in t:
        out["value"] = _string(t["value"], "value")
    if "max" in t:
        out["max"] = _number(t["max"], "max", 1e-9)
    for key in ("color", "back_color"):
        if key in t:
            out[key] = parse_color(t[key], key)
    if "radius" in t:
        out["radius"] = _number(t["radius"], "radius", 0)
    if "direction" in t:
        out["direction"] = _choice(t["direction"], _field_options("UiBar", "direction", DIRECTIONS), "direction")
    return out


def _apply_bar(scene, obj, t: dict) -> None:
    if obj.type == "EMPTY":
        size = t.get("size") or tuple(obj.get(SIZE_PROP) or (0.0, 0.0))
        obj[SIZE_PROP] = list(size)
        if "radius" in t:
            obj[RADIUS_PROP] = t["radius"]
        shape = {"size": size, "radius": obj.get(RADIUS_PROP, 0.0)} if ({"size", "radius"} & set(t)
                                                                         or not obj.children) else {}
        bg = _part(scene, obj, "bg", "panel", "Bg", 1)
        _apply_panel(scene, bg, {**shape, **({"color": t["back_color"]} if "back_color" in t else {})})
        fill = _part(scene, obj, "fill", "panel", "Fill", 2)
        _apply_panel(scene, fill, {**shape, **({"color": t["color"]} if "color" in t else {})})
    else:
        fill = None
    fields = {f: t[f] for f in ("value", "max", "direction") if f in t}
    if fill is not None and (not components.has_component(obj, "UiBar") or components.get_values(
            obj, "UiBar").get("fill") is None):
        fields["fill"] = fill
    if fields or not components.has_component(obj, "UiBar"):
        components.add_component(obj, "UiBar", fields)


# --- Icons, sliders and toggles (§10.7) -----------------------------------------------------------------


def _icon_image_source(value, what: str) -> str:
    try:
        return files.image_source(value)
    except GameUIError as exc:
        raise GameUIError(exc.message, hint=f"Pass {what} as a png, jpg or webp of the project (the starter template "
                                            "has assets/ui/heart.png and heart-empty.png, star, coin, gem and key)"
                                            + (", or '' for no empty slots." if what == "empty" else ".")) from None


def _check_icons(t: dict, creating: bool) -> dict:
    out = {}
    if "count" in t:
        out["count"] = _number(t["count"], "count", 1, MAX_ICONS, integer=True)
    if "value" in t:
        out["value"] = _string(t["value"], "value")
    if "size" in t:
        out["size"] = _number(t["size"], "size (one icon's size in px)", 1, 4096)
    if "spacing" in t:
        out["spacing"] = _number(t["spacing"], "spacing", 0, 4096)
    if "image" in t:
        out["image"] = _icon_image_source(t["image"], "image")
    elif creating:
        raise GameUIError("icons needs image: the picture of one full icon, e.g. 'assets/ui/heart.png'.")
    if "empty" in t:
        out["empty"] = "" if t["empty"] == "" else _icon_image_source(t["empty"], "empty")
    return out


def _icon_parts(obj, prefix: str) -> dict:
    """The parts slot<i> or icon<i> of an icon row, by i."""
    out = {}
    for child in obj.children:
        part = str(child.get(PART_PROP) or "")
        if part.startswith(prefix) and part[len(prefix):].isdigit():
            out[int(part[len(prefix):])] = child
    return out


def _set_icon(part, img, size: float, x: float, k: float) -> None:
    """An icon image that fits a size x size px box, its centre x Blender units right of the row's origin."""
    part.data = img
    part.empty_display_size = size / k  # the image's longer side
    part.scale = (1.0, 1.0, 1.0)
    part.location.x, part.location.y = x, 0.0
    _sync_matrix(part)


def icons_settings(obj) -> dict:
    """An icon row's count, value, image, empty, size (one icon, px) and spacing, as icons() takes them."""
    icons = _icon_parts(obj, "icon")
    slots = _icon_parts(obj, "slot")
    first = icons.get(1)
    value = ""
    if first is not None and components.has_component(first, "UiShow") and registry.schema_ref("UiShow"):
        value = components.get_values(first, "UiShow").get("value", "")

    def path(part):
        img = part.data if part is not None else None
        return files.project_relative(bpy.path.abspath(img.filepath)) if img is not None else ""

    return {"count": int(obj.get(COUNT_PROP, len(icons))), "value": value, "image": path(first),
            "empty": path(slots.get(1)), "size": float(obj.get(ICON_SIZE_PROP, ICONS_DEFAULTS["size"])),
            "spacing": float(obj.get(SPACING_PROP, ICONS_DEFAULTS["spacing"]))}


def _apply_icons(scene, obj, t: dict) -> None:
    k = core.px_per_unit(scene)
    current = icons_settings(obj)
    count = t.get("count", current["count"] or ICONS_DEFAULTS["count"])
    size = t.get("size", current["size"])
    spacing = t.get("spacing", current["spacing"])
    value = t.get("value", current["value"])
    icons, slots = _icon_parts(obj, "icon"), _icon_parts(obj, "slot")
    if "image" in t:
        icon_img = _load_image(t["image"])
    else:
        icon_img = icons[1].data if 1 in icons else None
        if icon_img is None:
            raise GameUIError(f"'{obj.name}' has no icon image to repeat.", hint="Pass image.")
    if "empty" in t:
        empty_img = _load_image(t["empty"]) if t["empty"] else None
    else:
        empty_img = slots[1].data if 1 in slots else None
    step = size + spacing
    for i in range(1, count + 1):
        x = (i - 1 - (count - 1) / 2) * step / k  # the row is centred on the origin
        if empty_img is not None:
            _set_icon(_part(scene, obj, f"slot{i}", "image", f"Slot{i}", 1), empty_img, size, x, k)
        icon = _part(scene, obj, f"icon{i}", "image", str(i), 2)
        _set_icon(icon, icon_img, size, x, k)
        components.add_component(icon, "UiShow", {"value": value, "compare": "atLeast", "than": float(i)})
    for i, part in icons.items():
        if i > count:
            _drop(part)
    for i, part in slots.items():
        if i > count or empty_img is None:
            _drop(part)
    obj[COUNT_PROP] = count
    obj[ICON_SIZE_PROP] = size
    obj[SPACING_PROP] = spacing
    obj[SIZE_PROP] = [count * size + (count - 1) * spacing, size]


def _check_control(t: dict, out: dict) -> None:
    """order, disabled and sound: what every control (slider, toggle) takes, as buttons do."""
    if "order" in t:
        out["order"] = _number(t["order"], "order", 0, integer=True)
    if "disabled" in t:
        out["disabled"] = _bool(t["disabled"], "disabled")
    if "sound" in t:
        out["sound"] = files.sound_source(t["sound"], "sound")


def _check_slider(t: dict, obj) -> dict:
    out = {}
    if "size" in t:
        out["size"] = _pair(t["size"], "size", positive=True)
    if "value" in t:
        out["value"] = _string(t["value"], "value")
    for key in ("min", "max"):
        if key in t:
            out[key] = _number(t[key], key)
    if "step" in t:
        out["step"] = _number(t["step"], "step", 0)
    for key in ("color", "back_color", "handle_color"):
        if key in t:
            out[key] = parse_color(t[key], key)
    if "handle" in t:
        out["handle"] = _bool(t["handle"], "handle")
    if "radius" in t:
        out["radius"] = _number(t["radius"], "radius", 0)
    if "direction" in t:
        out["direction"] = _choice(t["direction"], _field_options("UiSlider", "direction", DIRECTIONS), "direction")
    _check_control(t, out)
    current = components.get_values(obj, "UiSlider") if obj is not None and components.has_component(
        obj, "UiSlider") else {}
    low, high = out.get("min", current.get("min", 0.0)), out.get("max", current.get("max", 1.0))
    if not low < high:
        raise GameUIError(f"A slider's min must be below its max, got min {low} and max {high}.")
    return out


def _apply_slider(scene, obj, t: dict) -> None:
    k = core.px_per_unit(scene)
    size = t.get("size") or tuple(obj.get(SIZE_PROP) or SLIDER_DEFAULTS["size"])
    obj[SIZE_PROP] = list(size)
    if "radius" in t:
        obj[RADIUS_PROP] = t["radius"]
    elif obj.get(RADIUS_PROP) is None:
        obj[RADIUS_PROP] = min(size) / 2  # a pill
    has = components.has_component(obj, "UiSlider")
    current = components.get_values(obj, "UiSlider") if has else {}
    direction = t.get("direction") or current.get("direction") or "right"
    new = not obj.children
    shape = {"size": size, "radius": obj[RADIUS_PROP]} if ({"size", "radius"} & set(t) or new) else {}
    bg = _part(scene, obj, "bg", "panel", "Bg", 1)
    _apply_panel(scene, bg, {**shape, **({"color": t["back_color"]} if "back_color" in t else {})})
    fill = _part(scene, obj, "fill", "panel", "Fill", 2)
    _apply_panel(scene, fill, {**shape, **({"color": t["color"]} if "color" in t else {})})
    handle = find_part(obj, "handle")
    want = t["handle"] if "handle" in t else (handle is not None or new)
    made = False
    if want:
        made = handle is None
        handle = _part(scene, obj, "handle", "panel", "Handle", 3)
        w, h = size
        diameter = round(min(w, h) * HANDLE_SCALE, 3)
        knob = {"size": (diameter, diameter), "radius": diameter / 2} if made or "size" in t else {}
        color = t.get("handle_color") or (parse_color(SLIDER_DEFAULTS["handle_color"]) if made else None)
        _apply_panel(scene, handle, {**knob, **({"color": color} if color is not None else {})})
        # At the end of the range: Blender shows the slider full, as its fill is built.
        end = {"right": (w / 2, 0.0), "left": (-w / 2, 0.0), "up": (0.0, h / 2), "down": (0.0, -h / 2)}[direction]
        handle.location.x, handle.location.y = end[0] / k, end[1] / k
        _sync_matrix(handle)
    elif handle is not None:
        _drop(handle)
        handle = None
    fields = {f: t[f] for f in ("value", "min", "max", "step", "direction", "disabled", "order", "sound") if f in t}
    if not has or current.get("fill") is None:
        fields["fill"] = fill
    if want and (made or current.get("handle") is None):
        fields["handle"] = handle
    elif not want and current.get("handle") is not None:
        fields["handle"] = ""
    if fields or not has:
        components.add_component(obj, "UiSlider", fields)


def _check_toggle(t: dict, creating: bool) -> dict:
    out = {}
    if "value" in t:
        out["value"] = _string(t["value"], "value")
    elif creating:
        raise GameUIError("toggle needs value: the path it flips, e.g. 'save.subtitles' (a saved setting).")
    if "size" in t:
        out["size"] = _pair(t["size"], "size", positive=True)
    for key in ("on_color", "off_color", "knob_color"):
        if key in t:
            out[key] = parse_color(t[key], key)
    _check_control(t, out)
    return out


def _apply_toggle(scene, obj, t: dict) -> None:
    """Bg: the track in off_color. Off: the knob on the left. On: the lit track in on_color with its knob on the
    right (OnKnob). The runtime shows On or Off; Blender shows them stacked, the On look on top."""
    k = core.px_per_unit(scene)
    size = t.get("size") or tuple(obj.get(SIZE_PROP) or TOGGLE_DEFAULTS["size"])
    w, h = size
    obj[SIZE_PROP] = list(size)
    obj[RADIUS_PROP] = min(w, h) / 2
    reshape = "size" in t or not obj.children
    inset = max(2.0, round(min(w, h) / 12, 3))
    diameter = max(min(w, h) - 2 * inset, 1.0)
    reach = max(w / 2 - inset - diameter / 2, 0.0) / k  # the knob's centre from the middle
    track = {"size": size, "radius": min(w, h) / 2} if reshape else {}
    knob = {"size": (diameter, diameter), "radius": diameter / 2} if reshape else {}
    knob_color = {"color": t["knob_color"]} if "knob_color" in t else {}
    bg = _part(scene, obj, "bg", "panel", "Bg", 1)
    _apply_panel(scene, bg, {**track, **({"color": t["off_color"]} if "off_color" in t else {})})
    off = _part(scene, obj, "off", "panel", "Off", 2)
    _apply_panel(scene, off, {**knob, **knob_color})
    on = _part(scene, obj, "on", "panel", "On", 3)
    _apply_panel(scene, on, {**track, **({"color": t["on_color"]} if "on_color" in t else {})})
    on_knob = _part(scene, on, "knob", "panel", "Knob", 1)
    _apply_panel(scene, on_knob, {**knob, **knob_color})
    if reshape:
        off.location.x, on_knob.location.x = -reach, reach
        _sync_matrix(off)
        _sync_matrix(on_knob)
    has = components.has_component(obj, "UiToggle")
    current = components.get_values(obj, "UiToggle") if has else {}
    fields = {f: t[f] for f in ("value", "disabled", "order", "sound") if f in t}
    if current.get("on") is None:
        fields["on"] = on
    if current.get("off") is None:
        fields["off"] = off
    if fields or not has:
        components.add_component(obj, "UiToggle", fields)


# --- 3D models (§11.1) ------------------------------------------------------------------------------------
#
# A model is an empty with UiModel; its children are the model, in the empty's local space (the export leaves the
# empty's own transform out). The game frames the model itself from `view`; in Blender, seen from the canvas camera
# above, the builder scales the content so its longest side is MODEL_FILL of the view's shorter side, centred on
# the empty, hanging below the empty's Z: it covers the elements drawn under the model and none above it.


def _model_values(obj) -> dict:
    if components.has_component(obj, "UiModel") and registry.schema_ref("UiModel") is not None:
        return components.get_values(obj, "UiModel")
    return {}


def model_size(obj) -> tuple[float, float]:
    """A model's view size in design px (UiModel.size), without its object scale."""
    size = _model_values(obj).get("size") or obj.get(SIZE_PROP) or MODEL_DEFAULTS["size"]
    return float(size[0]), float(size[1])


def model_content(obj) -> list:
    """Every object of a model: its children, recursively."""
    return list(obj.children_recursive)


def _instanced(obj):
    return obj.instance_collection if obj.instance_type == "COLLECTION" else None


def _local_corners(obj) -> list:
    """The corners of an object's unevaluated local bounds (vertices for meshes), or []."""
    if obj.type == "MESH":
        mesh = obj.data
        n = len(mesh.vertices)
        if n:
            import numpy as np

            co = np.empty(n * 3, dtype=np.float64)
            mesh.vertices.foreach_get("co", co)
            co = co.reshape(n, 3)
            lo, hi = co.min(axis=0), co.max(axis=0)
            return [Vector((x, y, z)) for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])]
        return []
    if obj.type in ("CURVE", "SURFACE", "FONT", "META"):
        corners = [Vector(c) for c in obj.bound_box]
        return corners if any(c.length > 0 for c in corners) else []
    return []


def model_bounds(obj):
    """(min, max) of a model's content in the empty's local space, or None when it has no geometry. Unevaluated
    (modifiers left out), through collection instances."""
    inv = world_matrix(obj).inverted_safe()
    lo, hi = Vector((math.inf,) * 3), Vector((-math.inf,) * 3)

    def add(matrix, item, depth):
        for corner in _local_corners(item):
            p = matrix @ corner
            for i in range(3):
                lo[i], hi[i] = min(lo[i], p[i]), max(hi[i], p[i])
        coll = _instanced(item)
        if coll is not None and depth < 8:
            base = matrix @ Matrix.Translation(-Vector(coll.instance_offset))
            for member in coll.all_objects:
                # From its own transforms: objects only instanced (linked ones too) may never have been evaluated.
                add(base @ world_matrix(member), member, depth + 1)

    for child in obj.children_recursive:
        add(inv @ world_matrix(child), child, 0)
    if _instanced(obj) is not None:  # an asset instance with UiModel on it is a model of its own
        add(Matrix.Identity(4), obj, 0)
    return (lo, hi) if lo.x <= hi.x else None


def _transform_roots(obj, matrix) -> None:
    """Apply `matrix` (in the model empty's local space) to the model's direct children."""
    for child in obj.children:
        inverse = child.matrix_parent_inverse
        child.matrix_basis = inverse.inverted_safe() @ matrix @ inverse @ child.matrix_basis
        _sync_matrix(child)
        for sub in child.children_recursive:
            _sync_matrix(sub)


def _fit_model(scene, obj, size) -> None:
    """Scale and place the content: its longest side MODEL_FILL of the view's shorter side, centred on the empty in
    x and y, its top at the empty's Z."""
    bounds = model_bounds(obj)
    if bounds is None:
        return
    lo, hi = bounds
    extent = max(hi - lo)
    if extent <= 1e-9:
        return
    f = MODEL_FILL * min(size) / core.px_per_unit(scene) / extent
    centre = Vector(((lo.x + hi.x) / 2, (lo.y + hi.y) / 2, hi.z))
    _transform_roots(obj, Matrix.Translation(-centre * f) @ Matrix.Diagonal((f, f, f, 1.0)))


def _clips(items) -> list[str]:
    """The animation clips of these objects (and of the collections they instance), as list_clips names them."""
    from ..kits import animation

    names, seen = [], set()

    def visit(objs, depth):
        for item in objs:
            if item in seen:
                continue
            seen.add(item)
            for clip in animation.clips(item):
                if clip["name"] not in names:
                    names.append(clip["name"])
            coll = _instanced(item)
            if coll is not None and depth < 8:
                visit(coll.all_objects, depth + 1)

    visit(items, 0)
    return names


def model_clips(obj) -> list[str]:
    """The animation clips of a model's objects (and of the assets it instances)."""
    return _clips([*model_content(obj), *([obj] if _instanced(obj) is not None else [])])


def _check_clip(clip: str, clips) -> None:
    if clip and clip not in clips:
        raise GameUIError(f"The model has no clip {clip!r}.{schema.suggest(clip, list(clips))}",
                          hint=f"Its clips: {', '.join(clips)}." if clips else
                          "It has no animation clips: leave clip empty (spin still turns it).")


def model_source(obj) -> list[dict]:
    """What a model shows, per child: {"object", "kit"} for a kit object, {"object", "asset", "library"} for an
    asset instance, {"object", "linked", "kit"?} for an object linked from a level ("linked": "scenes/main.blend:
    Player", as source takes it; "kit": the kit that made it), ({"object", "collection"} for a collection of this
    file), else {"object", "type"}."""
    from .. import kits

    out = []
    for child in [obj, *obj.children] if _instanced(obj) is not None else obj.children:
        entry = {"object": child.name}
        made = kits.kit_params(child)
        coll = _instanced(child)
        if made is not None:
            entry["kit"] = made[0]
        elif coll is not None and coll.library is None and coll.get(LINK_PROP):
            entry["linked"] = coll[LINK_PROP]
            root = link_root(coll)
            root_kit = kits.kit_params(root) if root is not None else None
            if root_kit is not None:
                entry["kit"] = root_kit[0]
        elif coll is not None and coll.library is not None:
            from .. import assetlib

            info = assetlib.asset_of(child) or {}
            entry.update(asset=coll.name, library=info.get("library"))
        elif coll is not None:
            entry["collection"] = coll.name
        else:
            entry["type"] = child.type.lower()
        out.append(entry)
    return out


class LinkedSource(NamedTuple):
    """A model source in another .blend of the project ("scenes/main.blend:Player"): the object and its children,
    linked from that file when the model is made."""
    file: pathlib.Path
    rel: str           # project-relative, as the link collection's tag holds it
    name: str          # the root object
    members: tuple     # the root's name, then its children's, recursively, as the file has them

    @property
    def key(self) -> str:
        return f"{self.rel}:{self.name}"


def split_link(value):
    """"<path>.blend:<Object>" -> (path, object name), or None for anything else."""
    if not isinstance(value, str):
        return None
    at = value.lower().rfind(LINK_SEPARATOR)
    if at < 0:
        return None
    return value[:at + len(".blend")], value[at + len(LINK_SEPARATOR):]


def _linked_source(path: str, name: str) -> LinkedSource:
    """Check a source in another .blend of the project and read its hierarchy, without linking anything here:
    the file's objects are linked into temporary data that is freed afterwards."""
    file = files.blend_source(path, "source")
    rel = files.project_relative(file)
    if not name.strip():
        raise GameUIError(f"source {path}:{name!r} names no object.",
                          hint=f"Name an object of {rel} after the colon, e.g. '{rel}:Player'.")
    with bpy.data.temp_data() as temp:
        try:
            with temp.libraries.load(str(file), link=True) as (src, dst):
                names = [str(n) for n in src.objects]
                if name in names:
                    dst.objects = list(names)  # Blender puts the objects in that list once they are linked
        except (OSError, RuntimeError, ValueError) as exc:
            raise GameUIError(f"Blender could not read {rel}: {exc}") from None
        if name not in names:
            raise GameUIError(f"{rel} has no object {name!r}.{schema.suggest(name, names)}",
                              hint=f"Its objects: {', '.join(sorted(names)[:20])}{' ...' if len(names) > 20 else ''}."
                              if names else f"{rel} has no objects.")
        root = next(o for o in temp.objects if o.name == name)
        if root.type in ("CAMERA", "LIGHT", "SPEAKER", "LIGHT_PROBE"):
            raise GameUIError(f"'{name}' in {rel} is a {root.type.lower().replace('_', ' ')}, not a 3D model.")
        members = [root]  # with its children, recursively (Object.children only looks in the file's own data)
        for obj in members:
            members += [o for o in temp.objects if o.parent == obj and o not in members]
        if not any(o.type in ("MESH", "CURVE", "SURFACE", "FONT", "META") or _instanced(o) is not None
                   for o in members):
            raise GameUIError(f"'{name}' in {rel} has nothing to draw: no mesh in it or under it.")
        return LinkedSource(file, rel, name, tuple(o.name for o in members))


def link_root(coll):
    """The level object a model's link collection (LINK_PROP) shows, or None."""
    split = split_link(coll.get(LINK_PROP)) if coll is not None else None
    if split is None:
        return None
    return next((o for o in coll.objects if o.name == split[1]), None)


def link_basis(root) -> Matrix:
    """The linked root's place and turn in its level (its world matrix without scale), which the model's instance
    cancels: the model shows it at its own origin, facing as it was made, wherever the level puts it."""
    loc, rot, _scale = world_matrix(root).decompose()
    return Matrix.Translation(loc) @ rot.to_matrix().to_4x4()


def _flat(matrix) -> list[float]:
    return [float(v) for row in matrix for v in row]


def _matrix(values) -> Matrix:
    values = list(values)
    return Matrix([values[i * 4:i * 4 + 4] for i in range(4)])


def _sync_link(inst) -> None:
    """A linked model's instance whose root moved or turned in its level since: cancel its new place instead."""
    coll = _instanced(inst)
    stored = inst.get(LINK_BASIS_PROP)
    root = link_root(coll) if coll is not None and coll.get(LINK_PROP) else None
    if root is None or stored is None or len(stored) != 16:
        return
    old, new = _matrix(stored), link_basis(root)
    if all(abs(a - b) < 1e-6 for a, b in zip(_flat(old), _flat(new))):
        return
    inst.matrix_basis = inst.matrix_basis @ old @ new.inverted_safe()
    inst[LINK_BASIS_PROP] = _flat(new)
    _sync_matrix(inst)


def _link_collection(source: LinkedSource):
    """Link the source's objects from its file (a relative library path) into a collection of this file tagged
    LINK_PROP, which no scene holds: one per source, shared by the models that show it, its members kept in step
    with the file's hierarchy. Returns (collection, root)."""
    def same(path):
        try:
            return pathlib.Path(bpy.path.abspath(path)).resolve() == source.file.resolve()
        except (OSError, ValueError):
            return False

    library = next((lib for lib in bpy.data.libraries if same(lib.filepath)), None)
    have = {o.name: o for o in bpy.data.objects if library is not None and o.library == library}
    try:
        with bpy.data.libraries.load(str(source.file), link=True, relative=True) as (src, dst):
            dst.objects = [n for n in source.members if n in src.objects and n not in have]
    except (OSError, RuntimeError, ValueError) as exc:
        raise GameUIError(f"Blender could not link from {source.rel}: {exc}") from None
    linked = [have[n] for n in source.members if n in have] + [o for o in dst.objects if o is not None]
    root = next((o for o in linked if o.name == source.name), None)
    if root is None:
        raise GameUIError(f"Blender could not link '{source.name}' from {source.rel}.")
    coll = next((c for c in bpy.data.collections if c.library is None and c.get(LINK_PROP) == source.key), None)
    if coll is None:
        coll = bpy.data.collections.new(f"{source.name} ({pathlib.Path(source.rel).name})")
        coll[LINK_PROP] = source.key
    wanted = {o.as_pointer() for o in linked}
    present = {o.as_pointer() for o in coll.objects}
    for member in linked:
        if member.as_pointer() not in present:
            coll.objects.link(member)
    for member in list(coll.objects):
        if member.as_pointer() not in wanted:
            coll.objects.unlink(member)
    return coll, root


def _drop_link(coll) -> None:
    """A link collection no model shows any more: it goes, with the linked objects only it held."""
    members = list(coll.objects)
    bpy.data.collections.remove(coll)
    for member in members:
        try:
            if member.library is not None and member.users == 0:
                bpy.data.objects.remove(member)
        except ReferenceError:  # removed with another one
            pass


def _model_source_target(value, obj):
    """source: an object or a collection of this file (by name, or itself) -> the Object or Collection; an object
    of another .blend of the project ("scenes/main.blend:Player") -> a LinkedSource."""
    if isinstance(value, LinkedSource):
        return value
    split = split_link(value)
    if split is not None:
        return _linked_source(*split)
    if isinstance(value, (bpy.types.Object, bpy.types.Collection)):
        found = value
    else:
        name = _string(value, "source")
        found = bpy.data.objects.get(name) or bpy.data.collections.get(name)
        if found is None:
            known = [o.name for o in bpy.data.objects if o.type in ("MESH", "ARMATURE", "EMPTY", "CURVE")
                     and o.get(KIND_PROP) is None and not any(o is s.camera for s in bpy.data.scenes)]
            raise GameUIError(f"No object or collection named {name!r} in this file.{schema.suggest(name, known)}",
                              hint="Import the model into the UI file first (import_asset, or File > Import), or "
                                   "use kit or asset.")
    if getattr(found, "library", None) is not None:
        raise GameUIError(f"'{found.name}' is linked from {found.library.filepath}, so it cannot be moved into a "
                          "model.", hint="Use asset for library assets.")
    if isinstance(found, bpy.types.Object):
        if any(found is s.camera for s in bpy.data.scenes):
            raise GameUIError(f"'{found.name}' is a camera of a canvas.")
        if found.get(KIND_PROP) in KINDS:
            raise GameUIError(f"'{found.name}' is a UI element made by the builder, not a 3D object.")
        if obj is not None and (found is obj or found in obj.children_recursive):
            if found is obj or found.parent is not obj:
                raise GameUIError(f"'{found.name}' is already inside '{obj.name}'.")
        elif obj is not None and obj in found.children_recursive:
            raise GameUIError(f"'{found.name}' holds the model '{obj.name}' itself.")
        if model_of(found) is not None and (obj is None or model_of(found) is not obj):
            raise GameUIError(f"'{found.name}' is part of the model '{model_of(found).name}'.")
        _writable(found)
        for child in found.children_recursive:
            _writable(child)
    elif obj is not None and any(o is obj for o in found.all_objects):
        raise GameUIError(f"The collection '{found.name}' holds the model '{obj.name}' itself.")
    return found


def _check_model(t: dict, obj, creating: bool) -> dict:
    from .. import kits

    out = {}
    if "size" in t:
        out["size"] = _pair(t["size"], "size", positive=True)
    if "view" in t:
        out["view"] = _choice(t["view"], _field_options("UiModel", "view", VIEWS), "view")
    if "zoom" in t:
        out["zoom"] = _number(t["zoom"], "zoom", 0.05, 100)
    if "spin" in t:
        out["spin"] = _number(t["spin"], "spin (radians per second)", -100, 100)
    if "clip" in t:
        out["clip"] = _string(t["clip"], "clip")
    if "light" in t:
        out["light"] = _choice(t["light"], _field_options("UiModel", "light", LIGHTS), "light")
    if "background" in t:
        out["background"] = parse_color(t["background"], "background")
    given = [key for key in MODEL_SOURCES if key in t]
    if len(given) > 1:
        raise GameUIError(f"A model shows one thing: pass one of source, kit or asset, not {' and '.join(given)}.")
    if "kit_params" in t and "kit" not in t:
        raise GameUIError("kit_params go with kit: the kit they configure.")
    if creating and not given:
        raise GameUIError("model needs what it shows: kit (a GameStudio kit such as 'collectible' or "
                          "'character_rigged'), asset (an asset of the project's library) or source (an object or "
                          "collection of this UI file, or a level's object such as 'scenes/main.blend:Player').",
                          hint="Example: gameui.model('Title', 'Trophy', (960, 420), kit='collectible').")
    if "kit" in t:
        kind = _string(t["kit"], "kit")
        params = t.get("kit_params") or {}
        if not isinstance(params, dict):
            raise GameUIError(f"kit_params must be an object of the kit's params, got {params!r}")
        try:
            k = kits.base.get(kind)
            if k.root != "mesh":
                raise GameUIError(f"The '{kind}' kit makes a {k.root}, not a model.")
            kits.base.resolve(k, params)
        except kits.KitError as exc:
            raise GameUIError(str(exc), hint="list_kits lists the kits and their params.") from None
        out["kit"] = (kind, dict(params))
    if "asset" in t:
        from .. import assetlib

        value = t["asset"]  # a name, or (library, name)
        library, name = (value[0], _string(value[1], "asset")) if isinstance(value, (list, tuple)) and \
            len(value) == 2 else (None, _string(value, "asset"))
        try:
            out["asset"] = assetlib.find_asset(name, library)  # (library file, asset name)
        except assetlib.AssetLibError as exc:
            raise GameUIError(str(exc), hint="asset_list lists the project's assets.") from None
    if "source" in t:
        source = out["source"] = _model_source_target(t["source"], obj)
        # Objects of the file are checked before they move (new kits, assets and links: once they are made).
        if not isinstance(source, LinkedSource):
            clip = out.get("clip", _model_values(obj).get("clip", "") if obj is not None else "")
            _check_clip(clip, _clips(source.all_objects if isinstance(source, bpy.types.Collection)
                                     else [source, *source.children_recursive]))
    if "clip" in out and not given and obj is not None:
        _check_clip(out["clip"], model_clips(obj))  # the content stays: check now
    return out


def _link_content(scene, obj, items) -> None:
    """Put objects (with their children) into the canvas scene and under the model empty, at its origin."""
    for item in items:
        for member in [item, *item.children_recursive]:
            if scene not in member.users_scene:
                for coll in list(member.users_collection):
                    coll.objects.unlink(member)
                scene.collection.objects.link(member)
        item.parent = obj
        item.matrix_parent_inverse = Matrix.Identity(4)


def _make_content(scene, obj, t: dict) -> list:
    """The new content of a model (kit, asset or source), under the empty. Returns its top objects."""
    from .. import kits

    if "kit" in t:
        kind, params = t["kit"]
        params = {"name": f"{obj.name}Model", **params}
        try:
            made = kits.create(kind, params, location=(0.0, 0.0, 0.0), collection=scene.collection)
        except kits.KitError as exc:
            raise GameUIError(str(exc)) from None
        roots = [o for o in made if o.parent is None or o.parent not in made]
        _link_content(scene, obj, roots)
        return roots
    if "asset" in t:
        from .. import assetlib

        path, name = t["asset"]
        try:
            inst = assetlib.place_asset(name, path, name=free_name(f"{obj.name}Model"), collection=scene.collection)
        except assetlib.AssetLibError as exc:
            raise GameUIError(str(exc)) from None
        _link_content(scene, obj, [inst])
        return [inst]
    source = t["source"]
    if isinstance(source, LinkedSource):
        # A level's object: linked (it follows its file), in a collection of its own, instanced under the model.
        # The instance cancels the root's place and turn in the level, so the model shows it as it was made.
        coll, root = _link_collection(source)
        basis = link_basis(root)
        inst = bpy.data.objects.new(free_name(f"{obj.name}Model"), None)
        inst.instance_type = "COLLECTION"
        inst.instance_collection = coll
        inst.empty_display_size = GROUP_DISPLAY_SIZE
        inst.matrix_basis = basis.inverted_safe()
        inst[LINK_BASIS_PROP] = _flat(basis)
        scene.collection.objects.link(inst)
        _link_content(scene, obj, [inst])
        return [inst]
    if isinstance(source, bpy.types.Collection):
        inst = bpy.data.objects.new(free_name(f"{obj.name}Model"), None)
        inst.instance_type = "COLLECTION"
        inst.instance_collection = source
        inst.empty_display_size = GROUP_DISPLAY_SIZE
        scene.collection.objects.link(inst)
        for canvas in core.canvas_scenes():  # its objects are the model now, not elements of a canvas
            for parent in [canvas.collection, *canvas.collection.children_recursive]:
                if source.name in parent.children and parent.children[source.name] is source:
                    parent.children.unlink(source)
        _link_content(scene, obj, [inst])
        return [inst]
    if source.parent is obj:
        return [source]
    _link_content(scene, obj, [source])
    return [source]


def _apply_model(scene, obj, t: dict) -> None:
    current = _model_values(obj)
    old_size = model_size(obj)
    size = t.get("size", old_size)
    for child in obj.children:
        _sync_link(child)  # a linked level object moved in its level since: still at the model's origin
    if any(key in t for key in MODEL_SOURCES):  # new content first: its clips are checked before the rest changes
        old = [child for child in obj.children]
        roots = _make_content(scene, obj, t)
        try:
            for child in old:
                if child not in roots:
                    child.parent = None  # out of the way while the new content is fitted
            _fit_model(scene, obj, size)
            _check_clip(t.get("clip", current.get("clip", "")), model_clips(obj))
        except Exception:
            for child in roots:
                if child.name in bpy.data.objects and child not in old:
                    if "source" in t and not _instanced(child):
                        child.parent = None  # the file's own object: never deleted
                    else:
                        _drop(child)
            for child in old:
                child.parent = obj
            raise
        for child in old:
            if child not in roots and child.name in bpy.data.objects:
                _drop(child)
    elif "size" in t and min(old_size) > 0 and abs(min(size) - min(old_size)) > 1e-9:
        r = min(size) / min(old_size)  # the content keeps its share of the view
        _transform_roots(obj, Matrix.Diagonal((r, r, r, 1.0)))
    fields = {f: t[f] for f in ("size", *MODEL_FIELDS) if f in t}
    if fields or not components.has_component(obj, "UiModel"):
        components.add_component(obj, "UiModel", fields)
    obj[SIZE_PROP] = list(size)


def _parts_writable(obj) -> None:
    _writable(obj)
    for child in obj.children_recursive:
        _writable(child)


# --- The element functions (§6) ---------------------------------------------------------------------


def _element(kind: str, canvas, name: str, params: dict):
    require_ui_file()
    scene = get_canvas(canvas)
    _check_name(name)
    to_rest_frame(scene)
    obj = _existing(scene, name)
    creating = obj is None
    if not creating:
        current = kind_of(obj)
        if current != kind and not (kind == "panel" and current == "mesh"):
            raise GameUIError(f"'{name}' is a {current} element, not a {kind}. Use another name, or remove it first.")
        _parts_writable(obj)
    p = {key: value for key, value in params.items() if value is not None}
    loop = UNSET
    if "loop" in p:  # the element's own loop (§10.5), keyed once the element is built
        loop = check_loop(p.pop("loop"))
        _check_loop_change(scene, obj, loop)
    if creating:
        defaults = DEFAULTS[kind]
        if kind == "panel" and p.get("image"):  # an image panel draws its image, not a colour
            defaults = {key: value for key, value in defaults.items() if key != "color"}
        p = {**defaults, **p}
    common = _check_common(scene, {key: p.pop(key) for key in ("at", "z", "parent", "anchor", "visible") if key in p},
                           obj)
    if kind == "text":
        t = _check_text(p)
    elif kind == "image":
        t = _check_image(p, obj)
    elif kind == "panel":
        t = _check_panel(p, obj, creating)
    elif kind == "button":
        need_component("UiButton")
        need_component("UiState")
        t = _check_button(p, obj)
    elif kind == "bar":
        need_component("UiBar")
        t = _check_bar(p, creating)
    elif kind == "icons":
        need_component("UiShow")
        t = _check_icons(p, creating)
    elif kind == "slider":
        need_component("UiSlider")
        t = _check_slider(p, obj)
    elif kind == "toggle":
        need_component("UiToggle")
        t = _check_toggle(p, creating)
    elif kind == "model":
        need_component("UiModel")
        t = _check_model(p, obj, creating)
    else:
        t = {}
    if creating:
        common.setdefault("z", next_z(scene))
        obj = _create(scene, name, kind)
    try:
        _apply_common(scene, obj, common, creating)
        _APPLY[kind](scene, obj, t)
        obj[KIND_PROP] = kind  # a mesh made by hand is a panel from now on
    except Exception:
        if creating and obj.name in bpy.data.objects:
            _drop(obj)
        raise
    if loop is not UNSET:
        _set_loop(scene, obj, loop)
    _changed(scene)
    return obj


def _apply_group(scene, obj, t: dict) -> None:
    pass


def _changed(scene) -> None:
    """After a builder change: keep the canvas's animate() presets in step with its top-level elements (a moved
    element is re-keyed from its new place, a new one is keyed), then tell the live link the canvas changed: a
    running game gets it at once, even when the window shows another canvas (its depsgraph updates would not
    say so)."""
    from ..core import gameui_live

    if scene is not None and scene.get(ANIM_PROP):
        try:
            sync_animation(scene)
        except Exception:  # never fail the change itself
            log.exception("Keeping the open/close animations of '%s' in step failed", scene.name)
    mark = getattr(gameui_live, "mark_canvas_dirty", None)
    if mark is not None and scene is not None:
        mark(scene)


_APPLY = {"text": _apply_text, "image": _apply_image, "panel": _apply_panel, "button": _apply_button,
          "bar": _apply_bar, "group": _apply_group, "icons": _apply_icons, "slider": _apply_slider,
          "toggle": _apply_toggle, "model": _apply_model}


# Every element function also takes visible: True (the default for a new element) shows it, False makes it start
# hidden ("Disable in Renders", hide_render) until game code shows it (ctx.ui.show); None keeps it on an update.
# And loop: pulse, bob or blink keys that element alone over the canvas's loop range, played over and over while the
# canvas is open (§10.5; a canvas without a loop range gets one); "none" removes it; None keeps it.


def text(canvas, name, body, at, size=None, font=None, color=None, align=None, valign=None, box=None, parent=None,
         anchor=None, z=None, outline=None, outline_color=None, shadow=None, visible=None,
         loop=None, project_name=None) -> bpy.types.Object:
    """A text element (FONT object). size: em size in px (48); font: a font file (copied into assets/fonts/
    when outside the project; "" is the default, Blender's Inter; "<builtin>" Blender's built-in font);
    color "#FFFFFF"; align left/center/right/justify/flush ("center"); valign top/center/bottom/top-baseline/bottom-baseline ("center"); box: [w, h] centred on
    `at`, or [x, y, w, h] local px, text wraps at its width ([] removes it); outline (px, 0), outline_color
    ("#000000") and shadow ([x, y] or [x, y, blur] px) add UiText; visible False: starts hidden. project_name
    True marks a text whose body is the game's name (gs_ui_project_name, §11.7): a game made from a template saved
    from this one shows its own name there; False unmarks it."""
    return _element("text", canvas, name, dict(body=body, at=at, size=size, font=font, color=color, align=align,
                                                valign=valign, box=box, parent=parent, anchor=anchor, z=z,
                                                outline=outline, outline_color=outline_color, shadow=shadow,
                                                visible=visible, loop=loop, project_name=project_name))


def image(canvas, name, path, at, width=None, height=None, parent=None, anchor=None, z=None,
          opacity=None, visible=None, loop=None) -> bpy.types.Object:
    """An image element (an image empty) centred on `at`. path: an image file (copied into assets/ui/ when
    outside the project). 1 image px = 1 design px unless width and/or height (px) are given; with one of
    them the other follows the aspect ratio. opacity 0-1 (1); visible False: starts hidden."""
    return _element("image", canvas, name, dict(path=path, at=at, width=width, height=height, parent=parent,
                                                 anchor=anchor, z=z, opacity=opacity, visible=visible, loop=loop))


def panel(canvas, name, at, size, color=None, radius=None, border=None, border_color=None, parent=None, anchor=None,
          z=None, visible=None, image=None, slice=None, loop=None, shader=None,
          shader_values=None) -> bpy.types.Object:
    """A flat panel (a mesh with an unlit material) of size [w, h] px centred on `at`. color "#000000AA";
    radius: corner radius px (0); border: px (0), drawn as a child ring "<name>Border" in border_color
    ("#FFFFFF"); visible False: starts hidden. image: a png, jpg or webp the panel draws instead of a colour
    (copied into assets/ui/ when outside the project; "" makes it flat again), stretched over the panel, or with
    slice [left, right, top, bottom] (image px; one number for all four; [] removes it) as 9-slice art: 9 quads
    whose corners keep their size (UiSlice), rebuilt whenever the panel is resized through the builder, ui_set
    size or the Layout box (not with S). shader: a game shader that draws the panel in the game (Material.gs.shader,
    §11.4), such as "Shine" or "Gradient", on a material of the panel's own ("" removes it); shader_values: its
    field values (fields not given keep theirs)."""
    return _element("panel", canvas, name, dict(at=at, size=size, color=color, radius=radius, border=border,
                                                 border_color=border_color, parent=parent, anchor=anchor, z=z,
                                                 visible=visible, image=image, slice=slice, loop=loop,
                                                 shader=shader, shader_values=shader_values))


def button(canvas, name, label, at, size=None, action=None, target=None, font=None, text_size=None, color=None,
           text_color=None, focus_color=None, focus_text_color=None, radius=None, parent=None, anchor=None, z=None,
           order=None, sound=None, focus_sound=None, visible=None, loop=None) -> bpy.types.Object:
    """A button: a group with UiButton, centred on `at`, holding "<name>Bg" (color "#FFFFFF33"), "<name>Focus"
    (focus_color "#FFD400", shown while focused or pressed), "<name>Label" (label in text_color "#FFFFFF",
    text_size 40 px, font) and, when focus_text_color ("#17171C") differs, "<name>FocusLabel". size [360, 84],
    radius 42; action event/close/resume/open/previous/restart/load/none ("event"), target, order (0); sound
    (played when pressed) and focus_sound (when selected): a .ogg, .mp3 or .wav file of the project ("" none);
    visible False: starts hidden."""
    return _element("button", canvas, name, dict(label=label, at=at, size=size, action=action, target=target,
                                                  font=font, text_size=text_size, color=color, text_color=text_color,
                                                  focus_color=focus_color, focus_text_color=focus_text_color,
                                                  radius=radius, parent=parent, anchor=anchor, z=z, order=order,
                                                  sound=sound, focus_sound=focus_sound, visible=visible,
                                                  loop=loop))


def bar(canvas, name, at, size, value=None, max=None, color=None, back_color=None, radius=None, direction=None,
        parent=None, anchor=None, z=None, visible=None, loop=None) -> bpy.types.Object:
    """A bar: a group with UiBar, centred on `at`, holding "<name>Bg" (back_color "#00000080") and the fill
    "<name>Fill" (color "#4CAF50"). value: a store path such as "health" or "save.best" ("" sets it from
    code), max (1.0), direction right/left/up/down ("right"), radius px (0); visible False: starts hidden."""
    return _element("bar", canvas, name, dict(at=at, size=size, value=value, max=max, color=color,
                                               back_color=back_color, radius=radius, direction=direction,
                                               parent=parent, anchor=anchor, z=z, visible=visible, loop=loop))


def group(canvas, name, at, parent=None, anchor=None, z=None, visible=None, loop=None) -> bpy.types.Object:
    """An empty that groups, anchors, shows and hides its children together; visible False: starts hidden
    (and its children with it)."""
    return _element("group", canvas, name, dict(at=at, parent=parent, anchor=anchor, z=z, visible=visible,
                                                 loop=loop))


def icons(canvas, name, at, count=None, value=None, image=None, empty=None, size=None, spacing=None, parent=None,
          anchor=None, z=None, visible=None, loop=None) -> bpy.types.Object:
    """A row of icons that shows a number, such as hearts for lives: a group centred on `at` holding `count` (3)
    slot images "<name>Slot<i>" (empty, "assets/ui/heart-empty.png"; "" for none) and `count` icons "<name><i>"
    (image, "assets/ui/heart.png"), i = 1 ... count. Icon i has UiShow {value, atLeast, i}: it shows while
    value ("lives"; a value path, §10.1) is at least i. size: one icon's box in px (52; the image's longer side),
    spacing: px between icons (12); visible False: starts hidden."""
    return _element("icons", canvas, name, dict(at=at, count=count, value=value, image=image, empty=empty, size=size,
                                                 spacing=spacing, parent=parent, anchor=anchor, z=z, visible=visible,
                                                 loop=loop))


def slider(canvas, name, at, size=None, value=None, min=None, max=None, step=None, color=None, back_color=None,
           handle=None, handle_color=None, radius=None, direction=None, parent=None, anchor=None, z=None, order=None,
           sound=None, visible=None, disabled=None, loop=None) -> bpy.types.Object:
    """A slider that shows and sets a value path, such as a music volume: a group with UiSlider centred on `at`,
    holding "<name>Bg" (back_color "#FFFFFF33"), the fill "<name>Fill" (color "#FFD400"; its bounds are the
    range) and, with handle (True), the round "<name>Handle" (handle_color "#FFFFFF") at the range's end. value
    ("audio.music"; "audio.sfx", "save.difficulty"...), min (0), max (1), step (0.1; 0: smooth), size [360, 28]
    (a vertical slider: [28, 360] with direction "up"), radius px (half the thickness: a pill), direction
    right/left/up/down ("right"), order (0: by position), sound (played on each change: a .ogg, .mp3 or .wav of
    the project), disabled; visible False: starts hidden. No code: the game writes the value as it is dragged."""
    return _element("slider", canvas, name, dict(at=at, size=size, value=value, min=min, max=max, step=step,
                                                  color=color, back_color=back_color, handle=handle,
                                                  handle_color=handle_color, radius=radius, direction=direction,
                                                  parent=parent, anchor=anchor, z=z, order=order, sound=sound,
                                                  visible=visible, disabled=disabled, loop=loop))


def toggle(canvas, name, at, value=None, size=None, on_color=None, off_color=None, knob_color=None, parent=None,
           anchor=None, z=None, order=None, sound=None, visible=None, disabled=None, loop=None) -> bpy.types.Object:
    """An on/off switch that flips a value path (needed when created), such as "save.subtitles": a group with
    UiToggle centred on `at`, holding "<name>Bg" (the track in off_color "#FFFFFF33"), "<name>Off" (the knob on
    the left, knob_color "#FFFFFF") and "<name>On" (the track in on_color "#FFD400" with its knob
    "<name>OnKnob" on the right). The game shows On while the value is truthy and Off otherwise; Blender shows
    the On look. size [96, 48], order (0: by position), sound (played when flipped), disabled; visible False:
    starts hidden."""
    return _element("toggle", canvas, name, dict(at=at, value=value, size=size, on_color=on_color,
                                                  off_color=off_color, knob_color=knob_color, parent=parent,
                                                  anchor=anchor, z=z, order=order, sound=sound, visible=visible,
                                                  disabled=disabled, loop=loop))


def model(canvas, name, at, size=None, source=None, kit=None, kit_params=None, asset=None, view=None, zoom=None,
          spin=None, clip=None, light=None, background=None, parent=None, anchor=None, z=None, visible=None,
          loop=None) -> bpy.types.Object:
    """A 3D model inside the UI (§11.1), such as a turning trophy or a character on a select screen: an empty with
    UiModel centred on `at` whose children are the model, which the game draws lit in a view of `size` [w, h] px
    ([320, 320]). What it shows, one of: kit (a GameStudio kit, e.g. "collectible" or "character_rigged", with
    kit_params), asset (an asset of the project's library, placed as a collection instance) or source (an object of
    this UI file, moved under the empty with its children, or a collection, instanced; or "<file>.blend:<Object>",
    an object of another .blend of the project such as the level's player, "scenes/main.blend:Player", linked from
    that file with its children and their data, so it follows the file, and instanced at the model's origin, facing
    as it was made: without its place and turn in the level; its components are ignored). Giving one again replaces
    the model. In Blender it is seen from above, scaled to fit the view; the game frames it itself from view
    ("threeQuarter", "front", "side", "top"), zoom (1; above 1 is closer), spin (0.6: radians per second about its
    up axis; 0 still), clip (an animation clip of the model to loop, "" none), light ("studio", "soft", "flat") and
    background (a colour behind it; transparent by default). visible False: starts hidden."""
    return _element("model", canvas, name, dict(at=at, size=size, source=source, kit=kit, kit_params=kit_params,
                                                 asset=asset, view=view, zoom=zoom, spin=spin, clip=clip, light=light,
                                                 background=background, parent=parent, anchor=anchor, z=z,
                                                 visible=visible, loop=loop))


# --- Canvases ----------------------------------------------------------------------------------------


def canvas(name, kind=None, open=None, pause=None, pause_menu=None, back=None, scale_mode=None, backdrop=None,
           layer=None, size=None, focus=None) -> bpy.types.Scene:
    """Create or update a canvas (a scene of the open UI file) and its camera's UiCanvas. Defaults when created:
    kind "menu", open "manual", pause True, pause_menu False, back "none", scale_mode "fit", backdrop
    [0, 0, 0, 0.6] for menus and none for HUDs, layer 0, size (1920, 1080), focus none (the first button in
    navigation order). focus: the button selected first when the menu opens, a button element of this canvas
    ("" clears it). On an update, None keeps a value. A scene that is not a canvas yet (no camera, or no
    UiCanvas on it) gets the canvas camera setup."""
    require_ui_file()
    need_component("UiCanvas")
    scene = name if isinstance(name, bpy.types.Scene) else bpy.data.scenes.get(_check_name(name))
    if scene is None and name.startswith("gs_"):
        raise GameUIError(f"Canvas names cannot start with gs_ (GameStudio's own scenes): {name!r}")
    creating = scene is None
    # The first time a scene becomes a canvas (new, or without UiCanvas yet) it gets the canvas defaults.
    fresh = creating or scene.camera is None or not components.has_component(scene.camera, "UiCanvas")
    given = {"kind": kind, "open": open, "pause": pause, "pause_menu": pause_menu, "back": back,
             "scale_mode": scale_mode, "backdrop": backdrop, "layer": layer}
    values = {CANVAS_FIELDS[key]: value for key, value in given.items() if value is not None}
    if "backdrop" in values:
        values["backdrop"] = parse_color(values["backdrop"], "backdrop")
    if fresh and "backdrop" not in values and values.get("kind", "menu") == "menu":
        values["backdrop"] = MENU_BACKDROP
    for key in ("pause", "pauseMenu"):
        if key in values:
            _bool(values[key], key)
    if "layer" in values:
        _number(values["layer"], "layer", integer=True)
    if focus is not None:
        values["focus"] = _focus_button(scene, focus)
    values = _component_values("UiCanvas", values)
    if size is not None:
        size = tuple(int(_number(v, "size", 16, 16384, integer=True)) for v in _pair(size, "size", positive=True))
    if not creating and scene.camera is not None:
        _writable(scene.camera)
    if creating:
        scene = bpy.data.scenes.new(name)
    if creating or size is not None or core.camera_problems(scene):
        cam = core.setup_canvas_scene(scene, size or (core.design_size(scene) if not creating else core.DEFAULT_SIZE))
        _sync_matrix(cam)
    if fresh:
        _snap_settings(scene)
    components.add_component(scene.camera, "UiCanvas", values)
    _changed(scene)
    return scene


def is_control(obj) -> bool:
    """A button, slider or toggle: focusable, in the navigation order (§10.4)."""
    return any(components.has_component(obj, name) for name in CONTROLS)


def _focus_button(scene, value):
    """canvas(focus=): a button (or slider, or toggle) element of the canvas, or "" for none."""
    if value == "":
        return ""
    name = _string(value.name if isinstance(value, bpy.types.Object) else value, "focus")
    if scene is None:
        raise GameUIError(f"focus: a new canvas has no buttons yet, so it cannot select '{name}' first.",
                          hint="Create the canvas, add its buttons, then set focus.")
    obj = find_element(scene, name)
    if not is_control(obj):
        buttons = [o.name for o in elements(scene) if is_control(o)]
        raise GameUIError(f"focus: '{obj.name}' is a {kind_of(obj)} element, not a button.",
                          hint=f"Buttons of '{scene.name}': {', '.join(buttons)}." if buttons
                          else f"'{scene.name}' has no buttons yet: add one first.")
    return obj


def _snap_settings(scene) -> None:
    """Increment snapping: moves go in SNAP_PX steps (the 3D views' grid scale, set when the file opens)."""
    ts = scene.tool_settings
    ts.use_snap = True
    ts.snap_elements = {"INCREMENT"}


def canvas_settings(scene) -> dict:
    """The canvas's UiCanvas values (defaults when its camera has none), entity fields as names."""
    entry = registry.schema_ref("UiCanvas")
    cam = scene.camera
    if entry is None:
        return {}
    if cam is not None and components.has_component(cam, "UiCanvas"):
        return components.get_values(cam, "UiCanvas")
    return {f["name"]: f.get("default") for f in entry["fields"]}


# --- Open and close animations (§10.5) -------------------------------------------------------------------
#
# animate() lays the ranges out from frame 1: open [1, 1 + n], then loop, then close, n = duration x fps. The rest
# pose (the layout while the canvas is simply open) is the frame where open ends, and every range starts and ends
# there, so open keys go from the preset's offset to the rest pose and close keys from the rest pose to the offset.
# The scene's frame range covers the ranges, and its current frame is left at the rest frame.
#
# An element can also have a loop of its own (the `loop` setting of the element functions and update(), ui_add and
# ui_set): its preset keyed on that element only, over the canvas's loop range, in its local transform (so children
# work too), in place of the canvas's loop preset on it. A canvas without a loop range gets one for it: one cycle of
# the slowest element loop, or the length it already has; each element loop fits as many whole cycles as round to its
# own length. The tags record it ("own_loop"), so animate() keeps it and builder changes re-key it.

PRESETS = ("fade", "pop", "slide-up", "slide-down", "slide-left", "slide-right", "none")
LOOPS = {"pulse": 1.2, "bob": 1.6, "blink": 1.0}  # loop presets and the seconds of one cycle
ANIMATE_DEFAULTS = {"open": "pop", "close": "fade", "duration": 0.25, "loop": None}
RANGES = ("open", "loop", "close")
FIRST_FRAME = 1
POP_SCALE = 0.9      # pop: from 90 % of the size (with a fade)
SLIDE = 0.1          # slide-*: a tenth of the design size along the slide (with a fade)
PULSE_SCALE = 1.04   # loop pulse: up to 104 %
BOB_PX = 8.0         # loop bob: up by 8 px
BLINK_ALPHA = 0.35   # loop blink: down to 35 % opacity
# The channels a preset keys: (data path, index, how a builder change moves its keys: add or multiply).
CHANNELS = (("location", 0, "add"), ("location", 1, "add"), ("scale", 0, "mul"), ("scale", 1, "mul"),
            ("color", 3, "add"))


def _anim_record(scene) -> dict | None:
    text = scene.get(ANIM_PROP)
    if not isinstance(text, str):
        return None
    try:
        record = json.loads(text)
    except ValueError:
        return None
    return record if isinstance(record, dict) else None


def _anim_tag(obj) -> dict | None:
    text = obj.get(ANIM_PROP)
    if not isinstance(text, str):
        return None
    try:
        tag = json.loads(text)
    except ValueError:
        return None
    return tag if isinstance(tag, dict) and isinstance(tag.get("keys"), list) else None


NO_PRESETS = {"open": "none", "close": "none", "duration": ANIMATE_DEFAULTS["duration"], "loop": None}


def _settings_of(record) -> dict:
    """The presets a record was laid out with ({open, close, duration, loop}); none for no record."""
    if record is None:
        return dict(NO_PRESETS)
    return {key: record.get(key, ANIMATE_DEFAULTS[key]) for key in ANIMATE_DEFAULTS}


def _no_presets(settings) -> bool:
    return settings["open"] == settings["close"] == "none" and not settings["loop"]


def animation_settings(scene) -> dict | None:
    """The canvas's animate() presets {open, close, duration, loop}, or None when it has none (a canvas whose only
    animations are elements' own loops has none)."""
    record = _anim_record(scene)
    if record is None:
        return None
    settings = _settings_of(record)
    return None if _no_presets(settings) else settings


def animations(scene) -> dict:
    """The canvas's animations as export reads them (§10.5): {open, close, loop} seconds of each marker range, to
    the next marker or the scene's end; only the ranges the canvas has markers for."""
    markers = sorted(scene.timeline_markers, key=lambda m: m.frame)
    fps = scene.render.fps / (scene.render.fps_base or 1.0)
    out = {}
    for name in ("open", "close", "loop"):
        marker = next((m for m in markers if m.name == name), None)
        if marker is None:
            continue
        end = next((m.frame for m in markers if m.frame > marker.frame), scene.frame_end)
        out[name] = round(max(end - marker.frame, 0) / fps, 3)
    return out


def check_animation(open="pop", close="fade", duration=0.25, loop=None) -> dict:
    """animate()'s settings, checked: {open, close, duration, loop} (loop None for none)."""
    out = {"open": _choice(open, PRESETS, "open"), "close": _choice(close, PRESETS, "close"),
           "duration": _number(duration, "duration (seconds)", 0.01, 10)}
    out["loop"] = check_loop(loop)
    return out


def check_loop(loop) -> str | None:
    """A loop preset (a canvas's, or an element's own): pulse, bob or blink; None for "none" or None."""
    return None if loop in (None, "none") else _choice(loop, tuple(LOOPS) + ("none",), "loop")


def top_level(scene) -> list:
    """The canvas's top-level elements (the ones animate() keys), in draw order."""
    members = [o for o in scene.objects if is_element(o, scene)]
    present = set(members)
    return sorted((o for o in members if o.parent is None or o.parent not in present),
                  key=lambda o: (round(world_matrix(o).translation.z, 4), o.name))


def _fcurve(obj, path: str, index: int):
    """The F-curve of obj's action for one channel, or None."""
    from bpy_extras import anim_utils

    data = obj.animation_data
    action = data.action if data is not None else None
    if action is None:
        return None
    bag = anim_utils.action_get_channelbag_for_slot(action, data.action_slot)
    return bag.fcurves.find(path, index=index) if bag is not None else None


def _rest(scene, obj, record) -> tuple:
    """obj's rest pose (location x, y, scale x, y, colour alpha): its values now, less what its animation changes
    between the rest frame and the current frame. A builder change made at any frame moves the rest pose with it."""
    rest_frame = record.get("rest_frame") if record else None
    now = scene.frame_current
    out = []
    for path, index, mode in CHANNELS:
        value = float(getattr(obj, path)[index])
        fcurve = _fcurve(obj, path, index) if rest_frame is not None and now != rest_frame else None
        if fcurve is not None:
            current, rest = fcurve.evaluate(now), fcurve.evaluate(rest_frame)
            if mode == "add":
                value += rest - current
            elif abs(current) > 1e-9:
                value *= rest / current
        out.append(value)
    return tuple(out)


def to_rest_frame(scene) -> None:
    """Builder changes are made in the rest pose: a canvas with animate() presets goes to its rest frame first, so
    `at` and sizes are always where elements rest, at whatever frame the timeline was left."""
    record = _anim_record(scene)
    rest_frame = record.get("rest_frame") if record else None
    if isinstance(rest_frame, int) and scene.frame_current != rest_frame:
        scene.frame_set(rest_frame)


def _set_rest(obj, rest) -> None:
    for (path, index, _mode), value in zip(CHANNELS, rest):
        getattr(obj, path)[index] = value
    _sync_matrix(obj)
    for child in obj.children_recursive:
        _sync_matrix(child)


def _fps(scene) -> float:
    return scene.render.fps / (scene.render.fps_base or 1.0)


def _layout(scene, settings, loops=(), old=None) -> dict:
    """Where the ranges go: {ranges: {name: [start, end]}, rest_frame, end}. The loop range is one cycle of the
    canvas's loop preset; without one, elements' own loops (loops: their presets) keep the length the range had
    (old: the canvas's record), else get one cycle of the slowest of them."""
    fps = _fps(scene)
    frames = max(1, round(settings["duration"] * fps))
    frame, ranges = FIRST_FRAME, {}
    if settings["open"] != "none":
        ranges["open"] = [frame, frame + frames]
        frame += frames
    rest_frame = frame
    cycle = None
    loops = [loop for loop in loops if loop in LOOPS]
    if settings["loop"]:
        cycle = max(2, round(LOOPS[settings["loop"]] * fps))
    elif loops:
        span = ((old or {}).get("ranges") or {}).get("loop")
        if isinstance(span, list) and len(span) == 2 and isinstance(span[0], int) and isinstance(span[1], int) \
                and span[1] - span[0] >= 2:
            cycle = span[1] - span[0]
        else:
            cycle = max(2, round(max(LOOPS[loop] for loop in loops) * fps))
    if cycle:
        ranges["loop"] = [frame, frame + cycle]
        frame += cycle
    if settings["close"] != "none":
        ranges["close"] = [frame, frame + frames]
        frame += frames
    return {"ranges": ranges, "rest_frame": rest_frame, "end": frame}


def _preset_keys(scene, name: str, preset: str, start: int, end: int, rest) -> list:
    """The keys of one range: (path, index, frame, value, interpolation, easing). Open goes from the offset to the
    rest pose, close from the rest pose to the offset, a loop from the rest pose back to it."""
    k = core.px_per_unit(scene)
    w, h = core.design_size(scene)
    lx, ly, sx, sy, alpha = rest
    keys = []
    if name == "loop":
        mid = start + (end - start) // 2
        # (path, index, rest value, the value half way through the cycle)
        wave = {"pulse": [("scale", 0, sx, sx * PULSE_SCALE), ("scale", 1, sy, sy * PULSE_SCALE)],
                "bob": [("location", 1, ly, ly + BOB_PX / k)],
                "blink": [("color", 3, alpha, alpha * BLINK_ALPHA)]}[preset]
        for path, index, at_rest, peak in wave:
            keys += [(path, index, start, at_rest, "SINE", "EASE_IN_OUT"),
                     (path, index, mid, peak, "SINE", "EASE_IN_OUT"), (path, index, end, at_rest, "SINE", "AUTO")]
        return keys
    opening = name == "open"
    easing = "EASE_OUT" if opening else "EASE_IN"
    # (path, index, the value away from the rest pose, its rest value, interpolation)
    channels = [("color", 3, 0.0, alpha, "CUBIC")]
    dx, dy = SLIDE * w / k, SLIDE * h / k
    if preset == "pop":
        channels += [("scale", 0, sx * POP_SCALE, sx, "BACK" if opening else "CUBIC"),
                     ("scale", 1, sy * POP_SCALE, sy, "BACK" if opening else "CUBIC")]
    elif preset.startswith("slide-"):
        # The direction it moves: slide-up opens from below and closes upwards (Blender y is up).
        sign = {"slide-up": 1, "slide-down": -1, "slide-right": 1, "slide-left": -1}[preset]
        axis, distance, at_rest = (1, dy, ly) if preset in ("slide-up", "slide-down") else (0, dx, lx)
        channels.append(("location", axis, at_rest + (-sign if opening else sign) * distance, at_rest, "CUBIC"))
    for path, index, away, at_rest, interpolation in channels:
        first, last = (away, at_rest) if opening else (at_rest, away)
        keys += [(path, index, start, first, interpolation, easing), (path, index, end, last, "CUBIC", "AUTO")]
    return keys


def _loop_keys(scene, preset: str, start: int, end: int, rest) -> list:
    """A loop preset over the loop range [start, end]: the whole cycles that fit it best for the preset's own
    length (at least one, of 2 frames or more), each from the rest pose back to it."""
    span = end - start
    cycles = max(1, min(span // 2, round(span / (LOOPS[preset] * _fps(scene)))))
    bounds = [start + round(i * span / cycles) for i in range(cycles + 1)]
    keys = []
    for a, b in zip(bounds, bounds[1:]):
        keys += _preset_keys(scene, "loop", preset, a, b, rest)
    return keys


def _key(scene, obj, rest, settings, layout, top=True, own_loop=None) -> None:
    """Write an element's keys, tagged so they can be found again: a top-level element (top) gets the canvas's
    presets, with its own loop (own_loop) in place of the canvas's loop; an element inside another gets only its
    own loop. With nothing to key it is left at its rest pose, untagged."""
    keys, loop = [], None
    for name in RANGES:  # in time order: a range's first key sets the easing of the key it shares
        span = layout["ranges"].get(name)
        if span is None:
            continue
        if name == "loop":
            loop = own_loop or (settings["loop"] if top else None)
            if loop:
                keys += _loop_keys(scene, loop, span[0], span[1], rest)
        elif top:
            keys += _preset_keys(scene, name, settings[name], span[0], span[1], rest)
    if not keys:
        _set_rest(obj, rest)
        return
    had_action = obj.animation_data is not None and obj.animation_data.action is not None
    written, touched = [], set()
    for path, index, frame, value, interpolation, easing in keys:
        getattr(obj, path)[index] = value
        obj.keyframe_insert(path, index=index, frame=frame)
        fcurve = _fcurve(obj, path, index)
        if fcurve is not None:
            point = next((p for p in fcurve.keyframe_points if abs(p.co.x - frame) < 1e-3), None)
            if point is not None:
                point.interpolation = interpolation
                point.easing = easing
            touched.add(fcurve)
        if [path, index, frame] not in written:
            written.append([path, index, frame])
    for fcurve in touched:
        fcurve.update()
    action = obj.animation_data.action if obj.animation_data is not None else None
    if not had_action and action is not None:
        action[PRESET_ACTION] = True
    _set_rest(obj, rest)
    if obj.type == "EMPTY" and obj.empty_display_type == "IMAGE" and any(k[0] == "color" for k in keys):
        obj.use_empty_image_alpha = True  # so Blender shows an image's fade too
    tag = {"rest": [round(v, 6) for v in rest], "keys": written}
    if loop:
        tag["loop"] = loop  # the loop preset keyed: its own, or the canvas's
    if own_loop:
        tag["own_loop"] = own_loop
    if not top:
        tag["top"] = False
    obj[ANIM_PROP] = json.dumps(tag)


def _unkey(obj) -> None:
    """Remove the keys animate() wrote on obj (and its action when that is empty and animate() made it)."""
    tag = _anim_tag(obj)
    if tag is not None:
        for entry in tag["keys"]:
            try:
                path, index, frame = entry
                obj.keyframe_delete(path, index=int(index), frame=frame)
            except (RuntimeError, TypeError, ValueError):
                pass  # already gone
    data = obj.animation_data
    action = data.action if data is not None else None
    if action is not None and action.get(PRESET_ACTION):
        from bpy_extras import anim_utils

        bag = anim_utils.action_get_channelbag_for_slot(action, data.action_slot)
        if bag is None or not len(bag.fcurves):
            data.action = None
            if action.users == 0:
                bpy.data.actions.remove(action)
    if data is not None and data.action is None and not len(data.drivers) and not len(data.nla_tracks):
        obj.animation_data_clear()
    if ANIM_PROP in obj:
        del obj[ANIM_PROP]


def _put_marker(scene, name: str, frame: int) -> None:
    for marker in list(scene.timeline_markers):
        if marker.name == name:  # a canvas has one of each range
            scene.timeline_markers.remove(marker)
    scene.timeline_markers.new(name, frame=frame)


def _remove_markers(scene, record) -> None:
    for entry in (record or {}).get("markers", ()):
        try:
            name, frame = entry
        except (TypeError, ValueError):
            continue
        marker = next((m for m in scene.timeline_markers if m.name == name and m.frame == frame), None)
        if marker is not None:
            scene.timeline_markers.remove(marker)


def _keyable(obj) -> bool:
    return components.is_writable(obj) and not getattr(getattr(obj, "gs", None), "ai_lock", False)


def _own_loop(obj) -> str | None:
    """The element's own loop preset, as its tag records it, or None."""
    tag = _anim_tag(obj)
    loop = tag.get("own_loop") if tag is not None else None
    return loop if loop in LOOPS else None


def own_loops(scene) -> dict:
    """{element: preset} of the canvas's elements with a loop of their own."""
    return {obj: loop for obj in scene.objects if (loop := _own_loop(obj)) is not None}


def _apply_animation(scene, settings, loops) -> None:
    """Lay the canvas's animations out again: the presets (settings) on its top-level elements and the elements' own
    loops (loops: {element: preset}), replacing the keys and markers this module made. Nothing at all removes them
    and the canvas's record. Locks are the caller's to check."""
    targets = top_level(scene)
    to_rest_frame(scene)
    old = _anim_record(scene)
    tagged = [o for o in scene.objects if _anim_tag(o) is not None]
    rests = {obj: _rest(scene, obj, old) for obj in {*targets, *tagged, *loops}}
    for obj in tagged:
        _unkey(obj)
    _remove_markers(scene, old)
    if _no_presets(settings) and not loops:
        if ANIM_PROP in scene:
            del scene[ANIM_PROP]
        for obj, rest in rests.items():
            _set_rest(obj, rest)
        return
    layout = _layout(scene, settings, loops.values(), old)
    markers = []
    for name in RANGES:
        if name in layout["ranges"]:
            _put_marker(scene, name, layout["ranges"][name][0])
            markers.append([name, layout["ranges"][name][0]])
    for obj in targets:
        _key(scene, obj, rests[obj], settings, layout, True, loops.get(obj))
    for obj, loop in loops.items():
        if obj not in targets:
            _key(scene, obj, rests[obj], settings, layout, False, loop)
    for obj in tagged:
        if obj not in targets and obj not in loops:
            _set_rest(obj, rests[obj])
    scene[ANIM_PROP] = json.dumps({**settings, "ranges": layout["ranges"], "rest_frame": layout["rest_frame"],
                                   "markers": markers})
    scene.frame_start = FIRST_FRAME
    scene.frame_end = layout["end"]
    scene.frame_current = layout["rest_frame"]


def animate(canvas, open="pop", close="fade", duration=0.25, loop=None) -> bpy.types.Scene:
    """Open and close animations for a canvas (§10.5): keyframes on its top-level elements and the timeline
    markers open, close and loop. open and close: fade, pop (from 90 % of the size, with a fade), slide-up,
    slide-down, slide-left, slide-right (a tenth of the screen that way, with a fade) or none; duration: seconds
    of each (0.25); loop: pulse, bob, blink or None, played over and over while the canvas is open. Running it
    again replaces the keys and markers it made (a hand-made marker of the same name too); all 'none' removes
    them. Elements' own loops (update(loop=...)) stay, and replace the canvas's loop on their element. Elements
    added or moved later through the builder are keyed from their new place."""
    require_ui_file()
    scene = get_canvas(canvas)
    settings = check_animation(open, close, duration, loop)
    loops = own_loops(scene)
    for obj in {*top_level(scene), *loops}:
        _writable(obj)
    _apply_animation(scene, settings, loops)
    _changed(scene)
    return scene


def _relayout_needed(scene, record, loops) -> bool:
    """Whether the canvas's ranges change when its elements' own loops become `loops` ({element: preset})."""
    if record is None:
        return bool(loops)
    settings = _settings_of(record)
    if _no_presets(settings) and not loops:
        return True  # the record and the markers go
    wanted = _layout(scene, settings, loops.values(), record)
    return wanted["ranges"] != (record.get("ranges") or {}) or wanted["rest_frame"] != record.get("rest_frame")


def _loops_after(scene, obj, loop) -> dict:
    """The canvas's own loops once obj has `loop` (obj None: an element not made yet)."""
    loops = {o: preset for o, preset in own_loops(scene).items() if o is not obj}
    if loop is not None:
        loops[obj] = loop
    return loops


def _check_loop_change(scene, obj, loop) -> None:
    """Refuse an element loop (or its removal) that cannot be made, before anything changes: on a canvas whose
    open/close/loop markers were made by hand, or when the canvas's ranges change and a locked element would have
    to be keyed again. obj is None for an element about to be made."""
    if loop is None and (obj is None or _own_loop(obj) is None):
        return  # nothing to remove
    record = _anim_record(scene)
    if record is None and loop is not None:
        hand = sorted({m.name.strip().lower() for m in scene.timeline_markers} & set(RANGES))
        if hand:
            raise GameUIError(f"Canvas '{scene.name}' has {', '.join(hand)} marker(s) made by hand, so the builder "
                              "does not lay its timeline out.",
                              hint="Key the loop on Blender's timeline, or lay the markers out with ui_canvas "
                                   "transition (gs.gameui.animate) first: it replaces them.")
    loops = _loops_after(scene, obj, loop)
    if _relayout_needed(scene, record, loops):
        locked = sorted(o.name for o in {*top_level(scene), *loops}
                        if o is not None and o is not obj and not _keyable(o))
        if locked:
            raise GameUIError(f"This changes the loop range of '{scene.name}', so its animated elements are keyed "
                              f"again, but {', '.join(locked)} {'is' if len(locked) == 1 else 'are'} locked.",
                              hint="Unlock them (Lock from AI) first.")


def _set_loop(scene, obj, loop) -> None:
    """Give obj its own loop (a preset of LOOPS), or None to remove it (§6 loop). Checked by _check_loop_change."""
    if loop == _own_loop(obj) and (loop is None or element_loop(obj) == loop):
        return
    loops = _loops_after(scene, obj, loop)
    record = _anim_record(scene)
    to_rest_frame(scene)
    if _relayout_needed(scene, record, loops):
        _apply_animation(scene, _settings_of(record), loops)
        return
    # The ranges stay: only this element's keys change.
    layout = {"ranges": record.get("ranges") or {}, "rest_frame": record.get("rest_frame", FIRST_FRAME)}
    rest = _rest(scene, obj, record)
    _unkey(obj)
    _key(scene, obj, rest, _settings_of(record), layout, obj in top_level(scene), loop)


def _same_rest(tag, rest) -> bool:
    return len(tag.get("rest") or ()) == len(rest) and all(abs(a - b) < 1e-5 for a, b in zip(tag["rest"], rest))


def sync_animation(scene) -> None:
    """Keep the keys in step after builder changes: a top-level element without them (new, or no longer a child) is
    keyed, one whose rest pose moved is keyed again from there, an element that is no longer top-level keeps only
    its own loop, and a canvas whose own loops came or went is laid out again (its loop range changes). Locked
    elements are left as they are."""
    record = _anim_record(scene)
    if record is None:
        return
    settings = _settings_of(record)
    loops = own_loops(scene)
    targets = top_level(scene)
    if _relayout_needed(scene, record, loops) and all(_keyable(o) for o in {*targets, *loops}):
        _apply_animation(scene, settings, loops)
        return
    layout = {"ranges": record.get("ranges") or {}, "rest_frame": record.get("rest_frame", FIRST_FRAME)}
    for obj in scene.objects:
        tag = _anim_tag(obj)
        if obj in targets or tag is None or not _keyable(obj):
            continue
        rest = _rest(scene, obj, record)
        own = loops.get(obj)
        if own and tag.get("top") is False and _same_rest(tag, rest):
            continue
        _unkey(obj)
        if own:
            _key(scene, obj, rest, settings, layout, False, own)
        else:
            _set_rest(obj, rest)
    presets = not _no_presets(settings)
    for obj in targets:
        if not _keyable(obj):
            continue
        tag = _anim_tag(obj)
        own = loops.get(obj)
        if tag is None and not own and not presets:
            continue  # nothing to key on it
        rest = _rest(scene, obj, record)
        if tag is not None and tag.get("top", True) and _same_rest(tag, rest):
            continue
        _unkey(obj)
        _key(scene, obj, rest, settings, layout, True, own)


def _keys_intact(obj, tag, span) -> bool:
    """Whether the keys obj's tag records within frames span [start, end] are all still there."""
    for entry in tag.get("keys") or ():
        try:
            path, index, frame = entry
            index, frame = int(index), float(frame)
        except (TypeError, ValueError):
            continue
        if not span[0] <= frame <= span[1]:
            continue
        fcurve = _fcurve(obj, path, index)
        if fcurve is None or not any(abs(p.co.x - frame) < 1e-3 for p in fcurve.keyframe_points):
            return False
    return True


def _range_span(scene, record, name):
    """[start, end] of a range the record laid out, while its marker is still there; else None."""
    span = ((record or {}).get("ranges") or {}).get(name)
    if not (isinstance(span, list) and len(span) == 2):
        return None
    return span if any(m.name == name and m.frame == span[0] for m in scene.timeline_markers) else None


def element_loop(obj) -> str | None:
    """obj's own loop (§6 loop) while it is as the builder made it (its keys and the loop marker), else None."""
    loop = _own_loop(obj)
    scene = canvas_of(obj) if loop else None
    span = _range_span(scene, _anim_record(scene), "loop") if scene is not None else None
    return loop if span is not None and _keys_intact(obj, _anim_tag(obj), span) else None


def transition(scene) -> dict | None:
    """The canvas's animate() presets as they still are, for describe: {open, close, duration, loop?}. A preset whose
    marker is gone, or whose keys on the top-level elements were changed by hand, is left out and named in
    `edited`. None without presets."""
    settings = animation_settings(scene)
    if settings is None:
        return None
    record = _anim_record(scene)
    out = {key: value for key, value in settings.items() if value is not None}
    tagged = [(o, t) for o in top_level(scene) if (t := _anim_tag(o)) is not None and t.get("top", True)]
    edited = []
    for name in RANGES:
        preset = settings[name]
        if not preset or preset == "none":
            continue
        span = _range_span(scene, record, name)
        holders = [(o, t) for o, t in tagged if not (name == "loop" and t.get("own_loop"))]
        if span is None or not all(_keys_intact(o, t, span) for o, t in holders):
            edited.append(name)
            out.pop(name, None)
    if edited:
        out["edited"] = edited
    return out


# --- set_font, remove, update --------------------------------------------------------------------------


def set_font(target, path) -> bpy.types.VectorFont:
    """Give a text element, or every text of a canvas (button labels too), the font file at `path`
    (copied into assets/fonts/ when outside the project; "" is the default, Blender's Inter; "<builtin>" is
    Blender's built-in font)."""
    require_ui_file()
    source = files.font_source(path)
    obj = target if isinstance(target, bpy.types.Object) else None
    if obj is None and isinstance(target, str):
        found = bpy.data.objects.get(target)
        if found is not None and found.type == "FONT":
            obj = found
    if obj is not None:
        if obj.type != "FONT":
            raise GameUIError(f"'{obj.name}' is not a text element.")
        texts = [obj]
    else:
        scene = get_canvas(target)
        texts = [o for o in scene.objects if o.type == "FONT" and model_of(o) is None]
    for obj in texts:
        _writable(obj)
    font = files.load_font(source)
    for obj in texts:
        cu = obj.data
        cu.font = cu.font_bold = cu.font_italic = cu.font_bold_italic = font
    for scene in {s for obj in texts for s in obj.users_scene}:
        _changed(scene)
    return font


def remove(canvas, name=None) -> list[str]:
    """Remove an element with everything inside it, or with no name the whole canvas (its scene and its
    objects). Returns the removed object names."""
    require_ui_file()
    scene = get_canvas(canvas)
    if name is None:
        others = [s for s in core.canvas_scenes() if s is not scene]
        if not others:
            raise GameUIError(f"'{scene.name}' is the only canvas of this UI file, and a .blend needs a scene.",
                              hint="Create the next canvas first, then remove this one.")
        objs = [o for o in scene.objects if len(o.users_scene) == 1]
        for obj in objs:
            _writable(obj)
        names = [o.name for o in objs]
        for window in bpy.context.window_manager.windows:
            if window.scene is scene:
                window.scene = others[0]
        for obj in objs:
            if obj.name in bpy.data.objects:
                _drop(obj)
        bpy.data.scenes.remove(scene)
        return names
    obj = find_element(scene, name)
    _parts_writable(obj)
    names = [obj.name] + [c.name for c in obj.children_recursive]
    _drop(obj)
    _changed(scene)
    return names


# ui_set keys (§8) and what they mean for each kind.
SET_KEYS = ("at", "z", "size", "body", "font", "text_size", "color", "text_color", "focus_color", "focus_text_color",
            "align", "valign", "shadow", "visible", "anchor", "stretch", "action", "target", "disabled", "order",
            "sound", "focus_sound", "value", "min", "max", "step", "count", "parent", "loop", "view", "zoom", "spin",
            "clip", "light", "background", "shader", "shader_values", "project_name")
_CONTROL_SET = {"disabled": "disabled", "order": "order", "sound": "sound"}
_SHADER_SET = {"shader": "shader", "shader_values": "shader_values"}
_SET_BY_KIND = {
    "text": {"body": "body", "font": "font", "text_size": "size", "color": "color", "align": "align",
             "valign": "valign", "size": "box", "shadow": "shadow", "project_name": "project_name"},
    "image": {"size": "size"},
    "panel": {"size": "size", "color": "color", **_SHADER_SET},
    "mesh": {"color": "color", **_SHADER_SET},
    "model": {"size": "size", **{key: key for key in MODEL_FIELDS}},
    "button": {"body": "label", "font": "font", "text_size": "text_size", "color": "color", "text_color": "text_color",
               "focus_color": "focus_color", "focus_text_color": "focus_text_color", "align": "align",
               "valign": "valign", "size": "size", "action": "action", "target": "target", "disabled": "disabled",
               "order": "order", "sound": "sound", "focus_sound": "focus_sound"},
    "bar": {"size": "size", "color": "color", "value": "value", "max": "max"},
    "group": {},
    "icons": {"count": "count", "value": "value"},
    "slider": {"size": "size", "color": "color", "value": "value", "min": "min", "max": "max", "step": "step",
               **_CONTROL_SET},
    "toggle": {"size": "size", "value": "value", **_CONTROL_SET},
}
_BUTTON_KEYS = ("action", "target", "disabled", "order")


def update(canvas, name, **changes) -> bpy.types.Object:
    """Change some settings of an element (the ui_set tool): any of SET_KEYS. size is [w, h] px (a text's box;
    a 9-slice panel's quads are rebuilt); shadow [x, y] or [x, y, blur] px (texts); parent "" (or None) makes it
    top-level; action/target/disabled/order make any element a button; sound and focus_sound go with buttons
    (elements with UiButton); value, min, max and step with sliders (value and max also with bars, value with
    toggles and icon rows); count with icon rows; disabled, order and sound with sliders and toggles too; color,
    text_color, focus_color and focus_text_color with buttons (their parts recoloured as button() does: Bg, Label,
    Focus, and FocusLabel, made or dropped as the focus text colour differs from the text colour; a new text_color
    alone keeps the focused label's colour). A part the builder made (PlayButtonFocus, PlayButtonLabel: own_parts)
    is an element too, of its own kind (a panel, a text): update(canvas, part) changes that part alone, e.g. a
    shader on a button's focus art; loop
    (any kind): pulse, bob or blink keyed on this element alone over the canvas's loop range (§10.5), "none" or
    None removes it. Phase 3: size, view, zoom, spin, clip, light and background with models (a new size rescales
    the model in Blender); shader and shader_values with panels and meshes; project_name with texts."""
    require_ui_file()
    unknown = sorted(set(changes) - set(SET_KEYS))
    if unknown:
        raise GameUIError(f"Unknown setting(s) {', '.join(unknown)}.{schema.suggest(unknown[0], list(SET_KEYS))}",
                          hint=f"Settings: {', '.join(SET_KEYS)}.")
    scene = get_canvas(canvas)
    obj = find_element(scene, name)
    _parts_writable(obj)
    to_rest_frame(scene)
    kind = kind_of(obj)
    common = {}
    for key in ("at", "z", "visible"):
        if key in changes:
            common[key] = changes[key]
    if "parent" in changes:
        common["parent"] = changes["parent"] or ""
    if "anchor" in changes or "stretch" in changes:
        common["anchor"] = (changes.get("anchor"), changes.get("stretch"))
    common = _check_common(scene, common, obj)
    loop = UNSET
    if "loop" in changes:
        loop = check_loop(changes["loop"])
        _check_loop_change(scene, obj, loop)
    mapping = _SET_BY_KIND.get(kind, {})
    t, button_extra = {}, {}
    for key, value in changes.items():
        if key in ("at", "z", "visible", "parent", "anchor", "stretch", "loop"):
            continue
        if key in mapping:
            t[mapping[key]] = value
        elif kind in ("slider", "toggle") and (key in _BUTTON_KEYS or key in SOUND_KEYS):
            settings = ", ".join(["at", "z", "visible", "parent"] + list(mapping))
            raise GameUIError(f"'{key}' does not apply to {obj.name}: a {kind} is a control of its own, not a button "
                              "(a UiButton on it would be ignored).", hint=f"Settings for a {kind}: {settings}.")
        elif key in _BUTTON_KEYS or key in SOUND_KEYS:
            button_extra[key] = value  # any element can be a button (§2.3)
        else:
            raise GameUIError(f"'{key}' does not apply to {obj.name}, a {kind} element.",
                              hint=f"Settings for a {kind}: {', '.join(['at', 'z', 'visible', 'parent'] + list(mapping))}.")
    sounds = [key for key in SOUND_KEYS if key in button_extra]
    if sounds and not components.has_component(obj, "UiButton") and not set(_BUTTON_KEYS) & set(button_extra):
        raise GameUIError(f"'{sounds[0]}' goes with buttons; {obj.name} is a {kind} element without UiButton.",
                          hint="Set it on a button, or make this element one first (ui_set action).")
    if kind == "text":
        if "size" in t:
            t["size"] = _number(t["size"], "text_size", 1, 4096)
        t = _check_text(t)
    elif kind == "image":
        if "size" in t:
            t["width"], t["height"] = _pair(t.pop("size"), "size", positive=True)
        t = _check_image(t, obj)
    elif kind in ("panel", "mesh"):
        if kind == "mesh" and "color" in t and obj.type != "MESH":
            raise GameUIError(f"'{obj.name}' is a {obj.type.lower()}; only text and mesh elements take a color.")
        t = _check_panel(t, obj, False)
    elif kind == "button":
        t = _check_button(t, obj)
    elif kind == "bar":
        need_component("UiBar")
        t = _check_bar(t, False)
    elif kind == "icons":
        need_component("UiShow")
        t = _check_icons(t, False)
    elif kind == "slider":
        need_component("UiSlider")
        t = _check_slider(t, obj)
    elif kind == "toggle":
        need_component("UiToggle")
        t = _check_toggle(t, False)
    elif kind == "model":
        need_component("UiModel")
        t = _check_model(t, obj, False)
    if button_extra:
        need_component("UiButton")
        button_extra = _check_button(button_extra, obj)
    _apply_common(scene, obj, common, False)
    if t:
        _APPLY["panel" if kind == "mesh" else kind](scene, obj, t)
    if button_extra and kind != "button":
        components.add_component(obj, "UiButton", _button_fields(button_extra))
    if loop is not UNSET:
        _set_loop(scene, obj, loop)
    _changed(scene)
    return obj


# --- describe -----------------------------------------------------------------------------------------


def _round(v: float, digits: int = 1) -> float:
    return round(float(v), digits) + 0.0


def element_size(scene, obj) -> tuple[float, float] | None:
    """The element's box in design px (object scale included), or None (groups, text without a box)."""
    k = core.px_per_unit(scene)
    kind = kind_of(obj)
    sx, sy = abs(obj.scale.x), abs(obj.scale.y)
    if kind == "image":
        return image_size_px(obj, k) if obj.data is not None else None
    if kind == "model":
        w, h = model_size(obj)
        return w * sx, h * sy
    if kind in ("button", "bar", "icons", "slider", "toggle") and obj.get(SIZE_PROP) is not None:
        w, h = obj[SIZE_PROP]
        return w * sx, h * sy
    if kind in ("panel", "mesh") and obj.type == "MESH":
        w, h = panel_size(obj, k)
        return w * sx, h * sy
    if kind == "text":
        box = obj.data.text_boxes[0]
        if box.width > 0:
            return box.width * k * sx, box.height * k * sy
    return None


def _non_default(component: str, values: dict) -> dict:
    entry = registry.schema_ref(component)
    if entry is None:
        return values
    out = {}
    for field in entry["fields"]:
        name = field["name"]
        value, default = values.get(name), field.get("default")
        if isinstance(value, list) and isinstance(default, list) and len(value) == len(default):
            if all(abs(a - b) < 1e-4 for a, b in zip(value, default)):
                continue
        elif value == default or (value in ("", None) and default in ("", None)):
            continue
        out[name] = value
    return out


def rotation_deg(obj) -> float:
    """The element's rotation on the canvas in degrees, clockwise (canvas px, y down), parents included: a
    Blender rotation of +θ about Z is −θ here, as in the export (§3.2). A mirrored element keeps its mirror on
    X rather than a half turn, as the export does. In [-180, 180)."""
    m = world_matrix(obj)
    a, b, c, d = m[0][0], m[1][0], m[0][1], m[1][1]
    if math.hypot(a, b) < 1e-12:
        return 0.0
    theta = math.atan2(b, a)
    if a * d - b * c < 0:
        theta += math.pi
    return (-math.degrees(theta) + 180.0) % 360.0 - 180.0


def _depsgraph(scene):
    """The evaluated depsgraph of a canvas, shown in the window or not (brought up to date)."""
    from ..core import export

    view_layer = export._export_view_layer(scene)
    len(view_layer.objects)  # the layer's object list in step with its collections first
    view_layer.update()
    return view_layer.depsgraph


def text_rect(scene, obj, depsgraph=None) -> list[float] | None:
    """[x, y, w, h]: the box a text's glyphs fill in canvas px (top-left corner, y down), from Blender's evaluated
    layout of its body as written (a binding measures as its braces: "{score}"), rotation included (the box around
    it); None for an empty one. Approximate: the game lays the text out with the same font and rules, so its own box
    differs by a few px, more for a binding that grows."""
    if obj.type != "FONT" or not obj.data.body.strip():
        return None
    depsgraph = depsgraph or _depsgraph(scene)
    corners = [Vector(c) for c in obj.evaluated_get(depsgraph).bound_box]
    if not any(c.length > 0 for c in corners):
        return None
    m = world_matrix(obj)
    xs, ys = zip(*(core.to_px(scene, (m @ c).xy) for c in corners))
    return [_round(min(xs)), _round(min(ys)), _round(max(xs) - min(xs)), _round(max(ys) - min(ys))]


def font_path(font) -> str:
    """A font's project-relative path, or "" for Blender's built-in font."""
    if font is None or font.filepath in ("", "<builtin>"):
        return ""
    return files.project_relative(bpy.path.abspath(font.filepath))


def describe_element(scene, obj, depsgraph=None) -> dict:
    """One element, compactly: what ui_list shows and the write tools return. Defaults are left out: rotation
    only when the element is rotated, radius and border only when they are above 0. A text gives rect, its
    measured box (text_rect(): approximate); depsgraph: the canvas's evaluated one, when the caller has it."""
    kind = kind_of(obj)
    x, y = origin_px(scene, obj)
    out = {"name": obj.name, "kind": kind, "type": export_type(obj)}
    part = obj.get(PART_PROP)
    if part:
        out["part"] = part
    if obj.parent is not None:
        out["parent"] = obj.parent.name
    out["at"] = [_round(x), _round(y)]
    out["z"] = round(world_matrix(obj).translation.z, 3) + 0.0
    size = element_size(scene, obj)
    if size is not None:
        out["size"] = [_round(size[0]), _round(size[1])]
    rotation = _round(rotation_deg(obj))
    if rotation:
        out["rotation"] = rotation
    out["visible"] = not obj.hide_render
    if obj.parent is None:
        anchor, stretch, auto = resolve_anchor(scene, obj)
        out["anchor"] = anchor
        if auto:
            out["anchor_auto"] = True
        if stretch != "none":
            out["stretch"] = stretch
    k = core.px_per_unit(scene)
    if obj.type == "FONT":
        cu = obj.data
        out["text"] = cu.body
        out["font"] = font_path(cu.font)
        out["text_size"] = _round(cu.size * k)
        out["align"] = cu.align_x.lower()
        out["valign"] = cu.align_y.lower().replace("_", "-")
        rect = text_rect(scene, obj, depsgraph)
        if rect is not None:
            out["rect"] = rect
        if obj.get(PROJECT_NAME_PROP):
            out["project_name"] = True
    texture = panel_image(obj) if obj.type == "MESH" else None
    if texture is not None:  # an image panel: the image, and its 9-slice insets
        out["image"] = files.project_relative(bpy.path.abspath(texture.filepath)) if texture.filepath \
            else texture.name
        insets = slice_insets(obj)
        if insets is not None:
            out["slice"] = [_round(v) for v in insets]
    elif obj.type in ("FONT", "MESH"):
        color = object_color(obj)
        if color is not None:
            out["color"] = to_hex(color)
    # The shape the builder made (panels, buttons, bars and their parts), to match it in a new element.
    radius = obj.get(RADIUS_PROP)
    if isinstance(radius, (int, float)) and radius > 0:
        out["radius"] = _round(radius)
    border = obj.get(BORDER_PROP)
    if isinstance(border, (int, float)) and border > 0:
        out["border"] = _round(border)
        ring = find_part(obj, "border")
        ring_color = object_color(ring) if ring is not None else None
        if ring_color is not None:
            out["border_color"] = to_hex(ring_color)
    if kind == "image" and obj.data is not None:
        out["image"] = files.project_relative(bpy.path.abspath(obj.data.filepath))
        if obj.use_empty_image_alpha and obj.color[3] < 1:
            out["opacity"] = round(obj.color[3], 3)
    shader = panel_shader(obj) if obj.type == "MESH" else ""
    if shader:  # a game shader draws it (§11.4): its name and the values that are not the defaults
        from ..core import shaders

        out["shader"] = shader
        values = shaders.changed_values(panel_material(obj))
        if values:
            out["shader_values"] = values
    # The settings of the controls and icon rows, as ui_add takes them (the component values below leave defaults
    # out).
    if kind == "slider" and components.has_component(obj, "UiSlider") and registry.schema_ref("UiSlider"):
        values = components.get_values(obj, "UiSlider")
        out.update(value=values["value"], min=_round(values["min"], 4), max=_round(values["max"], 4),
                   step=_round(values["step"], 4))
    elif kind == "toggle" and components.has_component(obj, "UiToggle") and registry.schema_ref("UiToggle"):
        out["value"] = components.get_values(obj, "UiToggle")["value"]
    elif kind == "icons" and obj.type == "EMPTY":
        settings = icons_settings(obj)
        out.update(count=settings["count"], value=settings["value"], image=settings["image"],
                   empty=settings["empty"], icon_size=_round(settings["size"]), spacing=_round(settings["spacing"]))
    elif kind == "model" and obj.type == "EMPTY":
        values = _model_values(obj)
        if values:
            out.update(view=values["view"], zoom=_round(values["zoom"], 4), spin=_round(values["spin"], 4),
                       clip=values["clip"], light=values["light"], background=to_hex(values["background"]))
        out["source"] = model_source(obj)
        clips = model_clips(obj)
        if clips:
            out["clips"] = clips
    loop = element_loop(obj)  # its own loop (§10.5), while its keys are as the builder made them
    if loop:
        out["loop"] = loop
    comps = {}
    for ref in obj.gs.components:
        if registry.schema_ref(ref.name) is not None:
            comps[ref.name] = _non_default(ref.name, components.get_values(obj, ref.name))
        else:
            comps[ref.name] = {"missing_definition": True}
    if comps:
        out["components"] = comps
    if kind in ("button", "bar", "icons", "slider", "toggle") and obj.type == "EMPTY":
        out["parts"] = [c.name for c in obj.children if c.get(PART_PROP)]
    return out


def describe_canvas(scene) -> dict:
    """A canvas as ui_list and ui_canvas return it: its design size, UiCanvas settings, open/close animations
    (durations in seconds per marker range, and the animate() presets that made them) and elements."""
    out = {"name": scene.name, "size": list(core.design_size(scene)), "settings": canvas_settings(scene)}
    problems = core.camera_problems(scene)
    if problems:
        out["problems"] = problems
    durations = animations(scene)
    if durations:
        out["animations"] = durations
    preset = transition(scene)
    if preset is not None:
        out["transition"] = preset
    items = elements(scene)
    depsgraph = _depsgraph(scene) if any(o.type == "FONT" for o in items) else None
    out["elements"] = [describe_element(scene, o, depsgraph) for o in items]
    return out


def describe(canvas=None) -> dict:
    """The open UI file's canvases with their settings and elements (px positions and sizes, resolved
    anchors, components with their non-default values): what the ui_list tool returns. Outside a UI file:
    where ui/ui.blend is and whether it exists."""
    root = project.project_root()
    if not core.is_ui_file():
        path = core.ui_file(root)
        return {"file": None, "open": False,
                "ui_file": files.project_relative(path) if path is not None else None,
                "exists": bool(path is not None and path.exists()), "canvases": [],
                "hint": "ui_open opens ui/ui.blend (creating it when missing) to read and change the game's UI."}
    scenes = [get_canvas(canvas)] if canvas is not None else core.canvas_scenes()
    return {"file": files.project_relative(bpy.data.filepath), "open": True,
            "canvases": [describe_canvas(s) for s in scenes]}
