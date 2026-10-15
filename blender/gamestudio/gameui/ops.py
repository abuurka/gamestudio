# SPDX-License-Identifier: GPL-3.0-or-later
"""Game UI operators and menus (docs/GAME-UI.md §7).

  GameStudio > Edit Game UI    opens ui/ui.blend, creating it with one empty menu, "Menu", when missing.
                               Blender asks first when the open file has unsaved changes (Save, Don't
                               Save, Cancel); from Python, unsaved='SAVE' or 'DISCARD' says what to do
  GameStudio > Back to Level   opens the project's start level (in UI files)
  Shift+A > Game UI            Text, Image..., 3D Model..., Panel, Button, Bar, Slider, Toggle, Group (in UI files), at
                               the 3D cursor when it is inside the canvas frame, else at the canvas centre. 3D Model...
                               asks what it shows: a kit, a library asset or an object of the file
  New Game UI Screen           a new canvas with the current one's design size (Scene > Game UI Screen)
  Show in Game, Frame Canvas   the canvas panel's buttons; Choose Font... (Text > Font)

When a UI file opens, the 3D views go to the canvas camera's view in Material Preview, with a grid
(and so increment snapping) of 10 design px.
"""

import pathlib

import bpy
from bpy.app.handlers import persistent
from bpy.props import EnumProperty, StringProperty

from ..core import gameui as core, link, project
from ..core.log import get_logger
from . import build, files

log = get_logger(__name__)

NO_PROJECT = "Open a level of a GameStudio project first: the UI file lives in its ui/ folder"
UNSAVED_ITEMS = (
    ("ASK", "Ask", "Ask what to do with unsaved changes (from Python: refuse)"),
    ("SAVE", "Save", "Save the open file first"),
    ("DISCARD", "Discard", "Open without saving the open file's changes"),
)
# Shift+A > Game UI: kind -> (label, icon, description)
ADD_KINDS = {
    "TEXT": ("Text", "OUTLINER_OB_FONT", "A text element: Blender text drawn by the game with the same font"),
    "PANEL": ("Panel", "MESH_PLANE", "A flat, rounded panel (a mesh with an unlit material)"),
    "BUTTON": ("Button", "MOUSE_LMB", "A button: a group with UiButton, its background, focus art and label"),
    "BAR": ("Bar", "SEQ_HISTOGRAM", "A bar (health, progress): a group with UiBar, its background and fill"),
    "SLIDER": ("Slider", "ARROW_LEFTRIGHT", "A slider (a music volume, say): a group with UiSlider, its track, fill "
                                            "and handle; it shows and sets audio.music until you change its value"),
    "TOGGLE": ("Toggle", "CHECKBOX_HLT", "An on/off switch: a group with UiToggle, its track and its On and Off "
                                         "looks; set the value it flips (UiToggle value) in Object > Game"),
    "GROUP": ("Group", "OUTLINER_OB_EMPTY", "An empty that groups, anchors, shows and hides its children"),
}


def _same_file(a, b) -> bool:
    return bool(a) and bool(b) and pathlib.Path(a).resolve() == pathlib.Path(b).resolve()


def _dirty() -> bool:
    from ..bridge import api

    return api.is_dirty()


class _OpensFile:
    """Opens another .blend. Invoked from a menu, Blender's own prompt asks about unsaved changes;
    called from Python, `unsaved` says what to do (ASK refuses)."""

    unsaved: EnumProperty(name="Unsaved Changes", items=UNSAVED_ITEMS, default="ASK", options={"SKIP_SAVE"})

    def open_file(self, path, load_ui: bool = True) -> set:
        if self.options.is_invoke and self.unsaved == "ASK":
            # Asks Save / Don't Save / Cancel when the file has unsaved changes, then opens it.
            bpy.ops.wm.open_mainfile("INVOKE_DEFAULT", filepath=str(path), display_file_selector=False,
                                     load_ui=load_ui)
            return {"FINISHED"}
        if _dirty():
            if self.unsaved == "ASK":
                self.report({"ERROR"}, "The open file has unsaved changes: save it first, or pass "
                                       "unsaved='SAVE' or 'DISCARD'")
                return {"CANCELLED"}
            if self.unsaved == "SAVE":
                if not bpy.data.filepath:
                    self.report({"ERROR"}, "The open file was never saved: save it into the project first")
                    return {"CANCELLED"}
                bpy.ops.wm.save_mainfile()
        bpy.ops.wm.open_mainfile(filepath=str(path), load_ui=load_ui)
        return {"FINISHED"}  # the operator is gone after the load: no self here


class GAMESTUDIO_OT_edit_game_ui(_OpensFile, bpy.types.Operator):
    bl_idname = "gamestudio.edit_game_ui"
    bl_label = "Edit Game UI"
    bl_description = ("Open the game's menus and HUDs (ui/ui.blend, one scene per screen) to lay them out. "
                      "Creates the file with an empty menu when the project has none")

    @classmethod
    def poll(cls, context):
        return project.project_root() is not None

    def execute(self, context):
        root = project.project_root()
        if root is None:
            self.report({"ERROR"}, NO_PROJECT)
            return {"CANCELLED"}
        path = core.ui_file(root)
        if _same_file(bpy.data.filepath, path):
            setup_views()
            self.report({"INFO"}, f"Editing {files.project_relative(path)}")
            return {"FINISHED"}
        created = not path.exists()
        if created:
            try:
                files.new_ui_file(root)
            except build.GameUIError as exc:
                self.report({"ERROR"}, str(exc))
                return {"CANCELLED"}
            if _same_file(bpy.data.filepath, path):  # built right here (an untitled session)
                setup_views()
                return {"FINISHED"}
        # A file the background Blender just wrote has its default screen layout: keep the user's.
        return self.open_file(path, load_ui=not created)


class GAMESTUDIO_OT_back_to_level(_OpensFile, bpy.types.Operator):
    bl_idname = "gamestudio.back_to_level"
    bl_label = "Back to Level"
    bl_description = "Open the project's start level again"

    @classmethod
    def poll(cls, context):
        return project.project_root() is not None and build.in_ui_file()

    def execute(self, context):
        from ..projects import start_scene

        root = project.project_root()
        if root is None:
            self.report({"ERROR"}, NO_PROJECT)
            return {"CANCELLED"}
        path = start_scene(root)
        if not path.is_file():
            self.report({"ERROR"}, f"The start level {files.project_relative(path)} does not exist")
            return {"CANCELLED"}
        return self.open_file(path)


def drop_point(context) -> tuple[float, float]:
    """Where a new element goes, in canvas px: the 3D cursor when it is inside the frame, else the centre."""
    scene = context.scene
    w, h = core.design_size(scene)
    cursor = scene.cursor.location
    x, y = core.to_px(scene, (cursor.x, cursor.y))
    if 0 <= x <= w and 0 <= y <= h:
        return round(x), round(y)
    return w / 2, h / 2


def select_only(context, obj) -> None:
    view_layer = getattr(context, "view_layer", None)
    if view_layer is None:
        return
    for other in list(context.selected_objects):
        other.select_set(False)
    try:
        obj.select_set(True, view_layer=view_layer)
        view_layer.objects.active = obj
    except RuntimeError:  # not in this view layer (an excluded collection)
        pass


class _InUiFile:
    @classmethod
    def poll(cls, context):
        return build.in_ui_file() and getattr(context, "mode", "OBJECT") == "OBJECT"


class GAMESTUDIO_OT_ui_add(_InUiFile, bpy.types.Operator):
    bl_idname = "gamestudio.ui_add"
    bl_label = "Add Game UI Element"
    bl_description = "Add a game UI element to this screen"
    bl_options = {"REGISTER", "UNDO"}

    kind: EnumProperty(name="Kind", items=[(k, v[0], v[2], v[1], i) for i, (k, v) in enumerate(ADD_KINDS.items())])

    @classmethod
    def description(cls, context, properties):
        return ADD_KINDS.get(properties.kind, ("", "", cls.bl_description))[2]

    def execute(self, context):
        scene = context.scene
        at = drop_point(context)
        label = ADD_KINDS[self.kind][0]
        name = build.free_name(label)
        try:
            if self.kind == "TEXT":
                obj = build.text(scene, name, "Text", at)
            elif self.kind == "PANEL":
                obj = build.panel(scene, name, at, (480, 270), radius=16)
            elif self.kind == "BUTTON":
                obj = build.button(scene, name, "Button", at)
            elif self.kind == "BAR":
                obj = build.bar(scene, name, at, (400, 32), radius=16)
            elif self.kind == "SLIDER":
                obj = build.slider(scene, name, at)
            elif self.kind == "TOGGLE":
                obj = build.toggle(scene, name, at, "")
            else:
                obj = build.group(scene, name, at)
        except build.GameUIError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        select_only(context, obj)
        return {"FINISHED"}


class GAMESTUDIO_OT_ui_add_image(_InUiFile, bpy.types.Operator):
    bl_idname = "gamestudio.ui_add_image"
    bl_label = "Add Game UI Image"
    bl_description = ("Add an image (PNG, JPEG or WebP) at 1 image pixel = 1 screen pixel. A file from outside "
                      "the project is copied into assets/ui/")
    bl_options = {"REGISTER", "UNDO"}

    filepath: StringProperty(name="Image", subtype="FILE_PATH", options={"SKIP_SAVE"})
    filter_image: bpy.props.BoolProperty(default=True, options={"HIDDEN", "SKIP_SAVE"})
    filter_folder: bpy.props.BoolProperty(default=True, options={"HIDDEN", "SKIP_SAVE"})

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        if not self.filepath:
            self.report({"ERROR"}, "Choose an image file")
            return {"CANCELLED"}
        stem = "".join(ch if ch.isalnum() else "_" for ch in pathlib.Path(self.filepath).stem).strip("_")
        try:
            obj = build.image(context.scene, build.free_name(stem or "Image"), self.filepath, drop_point(context))
        except build.GameUIError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        select_only(context, obj)
        return {"FINISHED"}


MODEL_SOURCES = (
    ("KIT", "Kit", "A GameStudio kit: a gem, a crate, a character..."),
    ("ASSET", "Asset", "An asset of the project's library, placed as an instance"),
    ("OBJECT", "Object", "An object of this file (with its children), moved into the model"),
)
DEFAULT_MODEL_KIT = "collectible"
_kit_items: list = []


def _kit_enum(_self, _context):
    """The kits that make a model (not the sky's sun), kept referenced for Blender."""
    global _kit_items
    if not _kit_items:
        from .. import kits

        _kit_items = [(e["kind"], e["label"], e["description"]) for e in kits.catalog()
                      if kits.base.get(e["kind"]).root == "mesh"]
    return _kit_items


def _asset_enum(self, context):
    from .. import assetlib

    return assetlib._place_enum(self, context)


class GAMESTUDIO_OT_ui_add_model(_InUiFile, bpy.types.Operator):
    bl_idname = "gamestudio.ui_add_model"
    bl_label = "Add Game UI 3D Model"
    bl_description = ("Add a 3D model to this screen (a turning gem, a trophy, a character): the game draws it lit "
                      "in its own view. Choose a kit, a library asset or an object of this file")
    bl_options = {"REGISTER", "UNDO"}

    source: EnumProperty(name="Show", items=MODEL_SOURCES, default="KIT")
    kit: EnumProperty(name="Kit", items=_kit_enum)
    asset: EnumProperty(name="Asset", items=_asset_enum)
    object: StringProperty(name="Object", description="An object of this file (it moves into the model)")

    def invoke(self, context, event):
        from .. import assetlib

        assetlib._refresh_place_items()
        if any(item[0] == DEFAULT_MODEL_KIT for item in _kit_enum(self, context)) and not self.is_property_set("kit"):
            self.kit = DEFAULT_MODEL_KIT
        return context.window_manager.invoke_props_dialog(self, confirm_text="Add")

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "source", expand=True)
        if self.source == "KIT":
            layout.prop(self, "kit")
        elif self.source == "ASSET":
            layout.prop(self, "asset")
        else:
            layout.prop_search(self, "object", bpy.data, "objects")

    def execute(self, context):
        kwargs = {}
        if self.source == "KIT":
            kwargs["kit"] = self.kit
        elif self.source == "ASSET":
            if "|" not in (self.asset or ""):
                self.report({"ERROR"}, "No asset chosen (the project has no asset libraries yet)")
                return {"CANCELLED"}
            kwargs["asset"] = tuple(self.asset.split("|", 1))  # (library, asset)
        else:
            if not self.object:
                self.report({"ERROR"}, "Choose an object")
                return {"CANCELLED"}
            kwargs["source"] = self.object
        try:
            obj = build.model(context.scene, build.free_name("Model"), drop_point(context), **kwargs)
        except build.GameUIError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        select_only(context, obj)
        return {"FINISHED"}


def free_scene_name(base: str) -> str:
    if bpy.data.scenes.get(base) is None:
        return base
    n = 2
    while bpy.data.scenes.get(f"{base}{n}") is not None:
        n += 1
    return f"{base}{n}"


class GAMESTUDIO_OT_ui_new_screen(bpy.types.Operator):
    bl_idname = "gamestudio.ui_new_screen"
    bl_label = "New Game UI Screen"
    bl_description = "Add a screen (a scene of this UI file) with the same design size and camera setup"
    bl_options = {"REGISTER", "UNDO"}

    name: StringProperty(name="Name", default="Screen", options={"SKIP_SAVE"})
    kind: EnumProperty(name="Kind", items=(
        ("menu", "Menu", "One at a time; takes keyboard and gamepad focus; can pause the game"),
        ("hud", "HUD", "Stays on screen under menus; mouse only"),
    ), default="menu", options={"SKIP_SAVE"})

    @classmethod
    def poll(cls, context):
        return build.in_ui_file()

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, confirm_text="Create")

    def execute(self, context):
        size = core.design_size(context.scene) if context.scene is not None else core.DEFAULT_SIZE
        try:
            scene = build.canvas(free_scene_name(self.name.strip() or "Screen"), kind=self.kind, size=size)
        except build.GameUIError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        window = getattr(context, "window", None)
        if window is not None:
            window.scene = scene
        self.report({"INFO"}, f"Added the {self.kind} screen '{scene.name}'")
        return {"FINISHED"}


class GAMESTUDIO_OT_ui_setup_screen(bpy.types.Operator):
    bl_idname = "gamestudio.ui_setup_screen"
    bl_label = "Set Up Game UI Screen"
    bl_description = ("Make this scene a game UI screen: an orthographic camera looking straight down at the "
                      "origin, 100 px per unit, and UiCanvas settings on it")
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return build.in_ui_file() and context.scene is not None

    def execute(self, context):
        try:
            build.canvas(context.scene)
        except build.GameUIError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        return {"FINISHED"}


def game_connected() -> bool:
    client = link.get_link()
    return bool(client.connected and client.game_peers())


class GAMESTUDIO_OT_ui_show_in_game(bpy.types.Operator):
    bl_idname = "gamestudio.ui_show_in_game"
    bl_label = "Show in Game"
    bl_description = "Open this screen in the running game, as ctx.ui.open would"

    @classmethod
    def poll(cls, context):
        return build.in_ui_file() and context.scene is not None

    def execute(self, context):
        from ..core import gameui_live

        send = getattr(gameui_live, "send_open", None)
        if send is None:
            self.report({"ERROR"}, "This GameStudio version cannot open screens in the game yet")
            return {"CANCELLED"}
        if not game_connected():
            self.report({"WARNING"}, "No game is running: press Play first")
            return {"CANCELLED"}
        if send(context.scene.name) is False:
            self.report({"WARNING"}, f"Could not open '{context.scene.name}' in the game")
            return {"CANCELLED"}
        self.report({"INFO"}, f"Opened '{context.scene.name}' in the game")
        return {"FINISHED"}


class GAMESTUDIO_OT_ui_frame_canvas(bpy.types.Operator):
    bl_idname = "gamestudio.ui_frame_canvas"
    bl_label = "Frame Canvas"
    bl_description = "Look through the screen's camera in the 3D views and fit its frame (the screen) to the view"

    @classmethod
    def poll(cls, context):
        return build.in_ui_file()

    def execute(self, context):
        area = getattr(context, "area", None)
        setup_views(window=getattr(context, "window", None), area=area if area and area.type == "VIEW_3D" else None)
        return {"FINISHED"}


class GAMESTUDIO_OT_ui_choose_font(bpy.types.Operator):
    bl_idname = "gamestudio.ui_choose_font"
    bl_label = "Choose Font..."
    bl_description = ("Use a font file (TTF, OTF, WOFF, WOFF2) for the selected text. A file from outside the "
                      "project is copied into assets/fonts/, so the game has it too")
    bl_options = {"REGISTER", "UNDO"}

    filepath: StringProperty(name="Font", subtype="FILE_PATH", options={"SKIP_SAVE"})
    filter_font: bpy.props.BoolProperty(default=True, options={"HIDDEN", "SKIP_SAVE"})
    filter_folder: bpy.props.BoolProperty(default=True, options={"HIDDEN", "SKIP_SAVE"})

    @classmethod
    def poll(cls, context):
        obj = getattr(context, "active_object", None)
        return build.in_ui_file() and obj is not None and obj.type == "FONT"

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        texts = [o for o in context.selected_objects if o.type == "FONT"] or [context.active_object]
        try:
            for obj in texts:
                font = build.set_font(obj, self.filepath)
        except build.GameUIError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        self.report({"INFO"}, f"{len(texts)} text{'s' if len(texts) != 1 else ''} now use {font.name}")
        return {"FINISHED"}


class GAMESTUDIO_MT_add_game_ui(bpy.types.Menu):
    bl_idname = "GAMESTUDIO_MT_add_game_ui"
    bl_label = "Game UI"

    def draw(self, context):
        layout = self.layout
        layout.operator_context = "INVOKE_REGION_WIN"  # Image... opens the file browser, 3D Model... a dialog
        for kind in ("TEXT", "IMAGE", "MODEL", "PANEL", "BUTTON", "BAR", "SLIDER", "TOGGLE", "GROUP"):
            if kind == "IMAGE":
                layout.operator("gamestudio.ui_add_image", text="Image...", icon="OUTLINER_OB_IMAGE")
            elif kind == "MODEL":
                layout.operator("gamestudio.ui_add_model", text="3D Model...", icon="MESH_MONKEY")
            else:
                layout.operator("gamestudio.ui_add", text=ADD_KINDS[kind][0], icon=ADD_KINDS[kind][1]).kind = kind


def draw_add_menu(self, context):
    """Prepended to Shift+A in UI files."""
    if not build.in_ui_file():
        return
    layout = self.layout
    layout.menu(GAMESTUDIO_MT_add_game_ui.bl_idname, text="Game UI", icon="WINDOW")
    layout.separator()


def draw_menu_items(layout, context) -> None:
    """GameStudio menu: Edit Game UI, and Back to Level in UI files."""
    layout.operator("gamestudio.edit_game_ui", text="Edit Game UI", icon="WINDOW")
    if build.in_ui_file():
        layout.operator("gamestudio.back_to_level", text="Back to Level", icon="LOOP_BACK")


# --- The 3D views of a UI file ------------------------------------------------------------------------


def setup_views(window=None, area=None, frame: bool = True) -> int:
    """Camera view of the canvas camera, Material Preview, and a grid of SNAP_PX design px (increment
    snapping moves by it) in the 3D views of `window` (default: every window). Returns how many."""
    wm = getattr(bpy.context, "window_manager", None)
    windows = [window] if window is not None else list(getattr(wm, "windows", ()))
    count = 0
    for win in windows:
        scene = win.scene
        if scene is None or scene.camera is None or win.screen is None:
            continue
        k = core.px_per_unit(scene)
        for ar in ([area] if area is not None else [a for a in win.screen.areas if a.type == "VIEW_3D"]):
            space = ar.spaces.active
            space.shading.type = "MATERIAL"
            space.overlay.grid_scale = build.SNAP_PX / k
            space.region_3d.view_perspective = "CAMERA"
            count += 1
            region = next((r for r in ar.regions if r.type == "WINDOW"), None)
            if frame and region is not None:
                try:
                    with bpy.context.temp_override(window=win, area=ar, region=region):
                        bpy.ops.view3d.view_center_camera()
                except RuntimeError:
                    pass
    return count


def _setup_after_load():
    try:
        if build.in_ui_file():
            setup_views()
    except Exception:
        log.exception("Setting up the 3D views of the UI file failed")
    return None


@persistent
def _on_load_post(*_args):
    build._ui_file_cache["path"] = None
    if not bpy.app.background and build.in_ui_file():
        bpy.app.timers.register(_setup_after_load, first_interval=0.05)  # once the window has its screen


_CLASSES = (GAMESTUDIO_OT_edit_game_ui, GAMESTUDIO_OT_back_to_level, GAMESTUDIO_OT_ui_add, GAMESTUDIO_OT_ui_add_image,
            GAMESTUDIO_OT_ui_add_model, GAMESTUDIO_OT_ui_new_screen, GAMESTUDIO_OT_ui_setup_screen,
            GAMESTUDIO_OT_ui_show_in_game, GAMESTUDIO_OT_ui_frame_canvas, GAMESTUDIO_OT_ui_choose_font,
            GAMESTUDIO_MT_add_game_ui)


def register():
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.VIEW3D_MT_add.prepend(draw_add_menu)
    if _on_load_post not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_on_load_post)


def unregister():
    if _on_load_post in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_on_load_post)
    bpy.types.VIEW3D_MT_add.remove(draw_add_menu)
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
