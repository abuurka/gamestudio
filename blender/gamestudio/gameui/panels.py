# SPDX-License-Identifier: GPL-3.0-or-later
"""Game UI panels (docs/GAME-UI.md §7.5), shown in UI files only:

  Scene > Game UI Screen   the canvas: Show in Game, Frame Canvas, the design size, the camera's UiCanvas
                           settings (or Set Up Game UI Screen), New Game UI Screen
  Object > Game > Layout   the element's position and size in screen pixels (editable) and the anchor it
                           keeps its distance to, e.g. "Auto: top-left" (ui.draw_object_game calls draw_layout)
  Text > Font              Choose Font... (copies the file into assets/fonts/)

Object.gs_ui and Scene.gs_ui are computed properties (nothing is stored): they read and move the
element, or resize the canvas, in design pixels.
"""

import bpy
from bpy.props import FloatProperty, IntProperty, PointerProperty

from ..core import components, gameui as core, registry
from ..core.log import get_logger
from . import build

log = get_logger(__name__)


# --- Object.gs_ui: the element's box in canvas px ---------------------------------------------------


def _scene(props):
    obj = props.id_data
    scene = build.canvas_of(obj)
    return (obj, scene) if scene is not None and scene.camera is not None else (obj, None)


def _get_xy(props) -> tuple[float, float]:
    obj, scene = _scene(props)
    if scene is None:
        return 0.0, 0.0
    try:
        return build.origin_px(scene, obj)
    except Exception:  # draw code: never raise
        return 0.0, 0.0


def _set_xy(props, x=None, y=None) -> None:
    obj, scene = _scene(props)
    if scene is None:
        return
    build.to_rest_frame(scene)  # positions are where elements rest when the canvas has open/close animations
    cx, cy = _get_xy(props)
    build.move(scene, obj, (cx if x is None else x, cy if y is None else y))
    build._changed(scene)  # an element with open/close animations is keyed again from its new place


def _get_size(props) -> tuple[float, float]:
    obj, scene = _scene(props)
    if scene is None:
        return 0.0, 0.0
    try:
        return build.element_size(scene, obj) or (0.0, 0.0)
    except Exception:
        return 0.0, 0.0


def _set_size(props, w=None, h=None) -> None:
    obj, scene = _scene(props)
    if scene is None:
        return
    build.to_rest_frame(scene)
    cw, ch = _get_size(props)
    try:
        build.update(scene, obj.name, size=(cw if w is None else w, ch if h is None else h))
    except build.GameUIError as exc:
        log.error("%s", exc)


class GSUiElementProps(bpy.types.PropertyGroup):
    x: FloatProperty(name="X", description="Origin from the screen's left edge, in design pixels", subtype="PIXEL",
                     step=100, precision=0, get=lambda s: _get_xy(s)[0], set=lambda s, v: _set_xy(s, x=v))
    y: FloatProperty(name="Y", description="Origin from the screen's top edge (down), in design pixels",
                     subtype="PIXEL", step=100, precision=0, get=lambda s: _get_xy(s)[1],
                     set=lambda s, v: _set_xy(s, y=v))
    width: FloatProperty(name="Width", description="Width in design pixels", subtype="PIXEL", min=1.0, step=100,
                         precision=0, get=lambda s: _get_size(s)[0] or 1.0, set=lambda s, v: _set_size(s, w=v))
    height: FloatProperty(name="Height", description="Height in design pixels", subtype="PIXEL", min=1.0, step=100,
                          precision=0, get=lambda s: _get_size(s)[1] or 1.0, set=lambda s, v: _set_size(s, h=v))


# --- Scene.gs_ui: the canvas's design size --------------------------------------------------------------


def _set_design(props, w=None, h=None) -> None:
    scene = props.id_data
    cw, ch = core.design_size(scene)
    cam = core.setup_canvas_scene(scene, (cw if w is None else w, ch if h is None else h))
    cam.matrix_world = build.world_matrix(cam)


class GSUiScreenProps(bpy.types.PropertyGroup):
    width: IntProperty(name="Width", description="Design width in pixels (the camera keeps 100 px per unit)",
                       min=16, max=16384, get=lambda s: core.design_size(s.id_data)[0],
                       set=lambda s, v: _set_design(s, w=v))
    height: IntProperty(name="Height", description="Design height in pixels (the camera keeps 100 px per unit)",
                        min=16, max=16384, get=lambda s: core.design_size(s.id_data)[1],
                        set=lambda s, v: _set_design(s, h=v))


# --- Object > Game: Layout -------------------------------------------------------------------------------

SIZED_KINDS = ("panel", "image", "button", "bar", "slider", "toggle", "model")  # W/H resize them (9-slice too)


def anchor_text(scene, obj) -> str:
    """"Auto: top-left", "Center", "Bottom, stretch width" for a top-level element."""
    anchor, stretch, auto = build.resolve_anchor(scene, obj)
    name = anchor.replace("-", " ").capitalize()
    text = f"Auto: {anchor}" if auto else name
    return text + (f", stretch {stretch}" if stretch != "none" else "")


def draw_layout(layout, context, obj) -> None:
    """The Layout box of Object > Game for an element of a UI file's canvas."""
    from ..ui import hint

    if obj is None or not build.in_ui_file():
        return
    scene = build.canvas_of(obj)
    if scene is None or scene.camera is None or not build.is_element(obj, scene):
        return
    box = layout.box()
    kind = build.kind_of(obj)
    box.label(text=f"Layout ({kind})", icon="SNAP_INCREMENT")
    col = box.column(align=True)
    col.enabled = components.is_writable(obj)
    row = col.row(align=True)
    row.prop(obj.gs_ui, "x")
    row.prop(obj.gs_ui, "y")
    if kind in SIZED_KINDS or (kind == "text" and obj.data.text_boxes[0].width > 0):
        row = col.row(align=True)
        row.prop(obj.gs_ui, "width")
        row.prop(obj.gs_ui, "height")
    if kind == "model":
        hint(box, "Its children are the 3D model: the game draws them lit in this view (UiModel), turning "
                  "with spin. Blender shows them from above.")
    if obj.parent is None:
        box.label(text=f"Anchor: {anchor_text(scene, obj)}", icon="PIVOT_BOUNDBOX")
        if not components.has_component(obj, "UiAnchor"):
            hint(box, "Add UiAnchor to choose the anchor or stretch with the screen.")
    else:
        box.label(text=f"Follows {obj.parent.name}", icon="CON_CHILDOF")


# --- Scene > Game UI Screen --------------------------------------------------------------------------------


def draw_screen(layout, context) -> None:
    from ..ui import hint, wrap_label

    scene = context.scene
    cam = scene.camera
    row = layout.row(align=True)
    row.operator("gamestudio.ui_show_in_game", text="Show in Game", icon="PLAY")
    row.operator("gamestudio.ui_frame_canvas", text="Frame Canvas", icon="VIEW_CAMERA")
    col = layout.column(align=True)
    col.use_property_split = True
    col.use_property_decorate = False
    col.prop(scene.gs_ui, "width", text="Design Size")
    col.prop(scene.gs_ui, "height", text=" ")
    problems = core.camera_problems(scene)
    for problem in problems:
        wrap_label(layout, problem, icon="ERROR")
    entry = registry.schema_ref("UiCanvas")
    if cam is not None and components.has_component(cam, "UiCanvas") and entry is not None:
        grp = registry.group(cam, "UiCanvas")
        fields = layout.column()
        fields.use_property_split = True
        fields.use_property_decorate = False
        fields.enabled = components.is_writable(cam)
        for field in entry["fields"]:
            fields.prop(grp, field["name"])
    elif entry is None:
        hint(layout, "The game UI components are not in the registry yet: start the dev server.", icon="INFO")
    else:
        hint(layout, "No UiCanvas yet: this screen is a menu opened by code, with the default settings.")
    if problems or (entry is not None and (cam is None or not components.has_component(cam, "UiCanvas"))):
        layout.operator("gamestudio.ui_setup_screen", text="Set Up Game UI Screen", icon="CAMERA_DATA")
    layout.separator()
    count = sum(1 for o in scene.objects if build.is_element(o, scene))
    hint(layout, f"{count} element{'s' if count != 1 else ''}. Shift+A > Game UI adds more; each scene of this "
                 "file is one screen.")
    layout.operator_context = "INVOKE_DEFAULT"
    layout.operator("gamestudio.ui_new_screen", text="New Game UI Screen...", icon="ADD")


class GAMESTUDIO_PT_scene_game_ui(bpy.types.Panel):
    bl_label = "Game UI Screen"
    bl_idname = "GAMESTUDIO_PT_scene_game_ui"
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "scene"

    @classmethod
    def poll(cls, context):
        return build.in_ui_file() and context.scene is not None

    def draw(self, context):
        draw_screen(self.layout, context)


# --- Text > Font: Choose Font... ----------------------------------------------------------------------


def draw_font_extra(self, context):
    """Appended to Blender's Font panel of text objects, in UI files."""
    from ..ui import hint

    obj = getattr(context, "object", None)
    if obj is None or obj.type != "FONT" or not build.in_ui_file():
        return
    layout = self.layout
    layout.operator_context = "INVOKE_DEFAULT"
    layout.operator("gamestudio.ui_choose_font", text="Choose Font...", icon="FILE_FONT")
    path = build.font_path(obj.data.font)
    hint(layout, f"The game draws {path}." if path else
         "Blender's built-in font: the game draws it as Inter, Blender's own font.")


_CLASSES = (GSUiElementProps, GSUiScreenProps, GAMESTUDIO_PT_scene_game_ui)


def register():
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Object.gs_ui = PointerProperty(type=GSUiElementProps)
    bpy.types.Scene.gs_ui = PointerProperty(type=GSUiScreenProps)
    bpy.types.DATA_PT_font.append(draw_font_extra)


def unregister():
    bpy.types.DATA_PT_font.remove(draw_font_extra)
    del bpy.types.Scene.gs_ui
    del bpy.types.Object.gs_ui
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
