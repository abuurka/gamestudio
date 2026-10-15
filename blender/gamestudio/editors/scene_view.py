# SPDX-License-Identifier: GPL-3.0-or-later
"""The Scene view in a 3D View: the game's frame drawn under Blender's overlays, the Game toggle in
the 3D View's header (also View > Scene View, for a header too narrow to show it), and GameStudio >
Scene View. The lifecycle is core/sceneview.py.

How the frame gets under the overlays (tried in a UI Blender 5.2; docs/SPEC.md §7.3):
a PRE_VIEW draw handler alone cannot do it, since the draw engines then write their whole result over
the viewport's render buffer; POST_VIEW draws over the overlays. So:

  PRE_VIEW    in the Scene view's main region, remember the bound framebuffer: the viewport's render
              framebuffer (scene-linear colour and depth). Nothing is drawn.
  (Blender)   Solid shading draws the scene as usual, which gives the depth its overlays are tested
              against: selection outlines, origins, lights, cameras, empties, the 3D cursor and Edit
              Mode's elements draw where Blender sees them, into the overlay buffer.
  POST_VIEW   bind the remembered framebuffer and draw the game's frame over the whole render colour,
              opaque, without depth; then forget it (it is valid during this draw only). The frame is
              an SRGB8_A8 texture, so sampling gives linear values, and Solid's view transform (the
              config's default, Standard) gives the browser's sRGB pixels back exactly.
  (Blender)   the overlay buffer is composited over the render colour, then the gizmos and the
              POST_PIXEL handler below (the status text).

The viewport background must be black meanwhile (core/sceneview.py sets it): the overlay pass adds the
background colour where Blender drew no object. Without a framebuffer from PRE_VIEW in the same draw
nothing is drawn (said once in the log): a frame drawn in POST_VIEW would hide the overlays.

In a UI file (docs/GAME-UI.md §11.2) PRE_VIEW also gives core/sceneview.py the screen's camera frame
in this region as it draws (note_view), and out of camera view POST_PIXEL draws one line at the bottom
saying how to get there instead of the status box.
"""

import time

import bpy

from ..core import project, sceneview
from ..core.log import get_logger
from . import game_stream

log = get_logger(__name__)

ICON = "SHADING_RENDERED"
SPLIT_WAIT = 3.0  # seconds GameStudio > Scene View waits for the window to show the Game workspace
RETRY_HINT = "Click Game in the header to turn the Scene view off, and again to try again"

_handles = []
_capture = {"region": 0, "framebuffer": None}
_frame = {"session": None, "id": -1, "texture": None, "size": (0, 0)}
_warned = set()


def _warn_once(key: str, message: str) -> None:
    if key not in _warned:
        _warned.add(key)
        log.warning(message)


# --- Drawing -----------------------------------------------------------------------------------


def _is_main(context) -> bool:
    area, region = context.area, context.region
    if region is None or not sceneview.is_area(area, context.screen):
        return False
    main = sceneview.main_region(area)
    return main is not None and main.as_pointer() == region.as_pointer()


def _texture():
    """The latest frame as an SRGB8_A8 texture, uploaded when a new one came; None without one."""
    import gpu
    import numpy as np

    current = game_stream.session()
    if current is None:
        return None
    frame_id = getattr(current, "frame_id", -1)
    if _frame["texture"] is not None and _frame["session"] is current and _frame["id"] == frame_id:
        return _frame["texture"]
    floats = getattr(current, "latest_frame_float", lambda: None)()
    frame = floats if floats is not None else current.latest_frame()
    if frame is None:
        return _frame["texture"] if _frame["session"] is current else None
    width, height, data = frame
    if width <= 0 or height <= 0:
        return None
    count = width * height * 4
    if floats is None:
        data = np.multiply(np.frombuffer(data, dtype=np.uint8, count=count), np.float32(1.0 / 255.0),
                           dtype=np.float32)
    texture = gpu.types.GPUTexture((width, height), format="SRGB8_A8",
                                   data=gpu.types.Buffer("FLOAT", count, data))
    _frame.update(session=current, id=frame_id, texture=texture, size=(width, height))
    return texture


def _draw_frame(texture) -> None:
    """The texture over the whole bound framebuffer (rows top to bottom), opaque, without depth."""
    import gpu
    from gpu_extras.batch import batch_for_shader
    from mathutils import Matrix

    shader = gpu.shader.from_builtin("IMAGE")
    batch = batch_for_shader(shader, "TRI_FAN", {"pos": ((-1, -1), (1, -1), (1, 1), (-1, 1)),
                                                 "texCoord": ((0, 1), (1, 1), (1, 0), (0, 0))})
    with gpu.matrix.push_pop(), gpu.matrix.push_pop_projection():
        gpu.matrix.load_identity()
        gpu.matrix.load_projection_matrix(Matrix.Identity(4))
        gpu.state.depth_test_set("NONE")
        gpu.state.depth_mask_set(False)
        gpu.state.blend_set("NONE")
        shader.bind()
        shader.uniform_sampler("image", texture)
        batch.draw(shader)


def _pre_view() -> None:
    context = bpy.context
    _capture["region"], _capture["framebuffer"] = 0, None
    if not _is_main(context):
        return
    try:
        import gpu

        # The freshest camera for the game (in a UI file, the screen's camera frame in this region).
        sceneview.note_view(context.region_data, context.region, context.space_data, context.scene)
        if sceneview.showing():
            _capture["region"] = context.region.as_pointer()
            _capture["framebuffer"] = gpu.state.active_framebuffer_get()
    except Exception:  # noqa: BLE001 - never break Blender's drawing
        _warn_once("pre", "The Scene view could not prepare its frame")
        log.debug("Scene view PRE_VIEW failed", exc_info=True)


def _post_view() -> None:
    context = bpy.context
    framebuffer, region = _capture["framebuffer"], _capture["region"]
    _capture["region"], _capture["framebuffer"] = 0, None
    if not _is_main(context) or not sceneview.showing():
        return
    if framebuffer is None or region != context.region.as_pointer():
        _warn_once("capture", "The Scene view got no viewport framebuffer from this Blender's 3D View; "
                              "the game's frame is not drawn")
        return
    try:
        texture = _texture()
        if texture is None:
            return
        with framebuffer.bind():
            _draw_frame(texture)
    except Exception:  # noqa: BLE001
        _warn_once("post", "The Scene view could not draw the game's frame")
        log.debug("Scene view POST_VIEW failed", exc_info=True)


def _post_pixel() -> None:
    context = bpy.context
    if not _is_main(context):
        return
    try:
        _draw_status(context)
    except Exception:  # noqa: BLE001
        _warn_once("text", "The Scene view could not draw its status")
        log.debug("Scene view POST_PIXEL failed", exc_info=True)


def _draw_status(context) -> None:
    import blf
    import gpu

    region = context.region
    scale = context.preferences.system.ui_scale or 1.0
    small, large = 11 * scale, 15 * scale
    text, kind = sceneview.status()
    if kind == "hint":
        _draw_hint(region, text, 13 * scale, scale)
    elif text:
        hint = RETRY_HINT if kind == "error" else ""
        blf.size(0, large)
        width = blf.dimensions(0, text)[0]
        if hint:
            blf.size(0, small)
            width = max(width, blf.dimensions(0, hint)[0])
        x, y, pad = region.width / 2, region.height / 2, 12 * scale
        bottom = y - (1.8 * large if hint else 0.0) - pad
        gpu.state.blend_set("ALPHA")
        shader = gpu.shader.from_builtin("UNIFORM_COLOR")
        shader.bind()
        game_stream._rect(shader, x - width / 2 - pad, bottom, width + 2 * pad, y + large + pad - bottom,
                          (0.0, 0.0, 0.0, 0.55))
        color = (1.0, 0.45, 0.4, 1.0) if kind == "error" else (1.0, 1.0, 1.0, 0.9)
        game_stream._text(text, x, y, large, color, center=True)
        if hint:
            game_stream._text(hint, x, y - 1.8 * large, small, center=True)
        gpu.state.blend_set("NONE")
    elif sceneview.showing() and context.mode != "OBJECT":
        game_stream._text("The game shows the edit when you leave this mode", region.width / 2, 12 * scale, small,
                          (1, 1, 1, 0.8), center=True)
    if sceneview.showing() and (bpy.app.debug_value or bpy.app.debug):
        current = game_stream.session()
        size = _frame["size"]
        label = f"Scene view {getattr(current, 'fps', 0.0):.0f} fps  {size[0]} x {size[1]}"
        blf.size(0, small)
        game_stream._text(label, region.width - blf.dimensions(0, label)[0] - 10 * scale, 10 * scale, small,
                          (1, 1, 1, 0.7))


def hint_box(region_width: float, text_width: float, size: float, scale: float) -> tuple:
    """(x, y, width, height) of the box behind a one-line hint: centred near the bottom of the region,
    in region pixels from the bottom left."""
    pad = 8 * scale
    width = text_width + 2 * pad
    return (region_width - width) / 2, 24 * scale, width, size + 2 * pad


def _draw_hint(region, text: str, size: float, scale: float) -> None:
    """One line at the bottom of the area over Blender's own drawing (a UI file out of its screen's
    camera view): it leaves the screen being edited in view."""
    import blf
    import gpu

    blf.size(0, size)
    x, y, width, height = hint_box(region.width, blf.dimensions(0, text)[0], size, scale)
    gpu.state.blend_set("ALPHA")
    shader = gpu.shader.from_builtin("UNIFORM_COLOR")
    shader.bind()
    game_stream._rect(shader, x, y, width, height, (0.0, 0.0, 0.0, 0.55))
    game_stream._text(text, region.width / 2, y + height / 2 - size * 0.35, size, (1.0, 1.0, 1.0, 0.9), center=True)
    gpu.state.blend_set("NONE")


# --- Header and operators ----------------------------------------------------------------------


def draw_header(self, context) -> None:
    """Appended to the 3D View's header, right of the shading buttons: the Game toggle (in a project)."""
    if project.project_root() is None or context.area is None:
        return
    on = sceneview.is_area(context.area, context.screen)
    self.layout.operator(GAMESTUDIO_OT_scene_view.bl_idname, text="Game", icon=ICON, depress=on)


def draw_view_menu(self, context) -> None:
    """Appended to the 3D View's View menu: the same toggle, for a header too narrow to show it."""
    if project.project_root() is None or context.area is None:
        return
    on = sceneview.is_area(context.area, context.screen)
    self.layout.separator()
    self.layout.operator(GAMESTUDIO_OT_scene_view.bl_idname, text="Scene View (Game Renderer)",
                         icon="CHECKBOX_HLT" if on else "CHECKBOX_DEHLT")


class GAMESTUDIO_OT_scene_view(bpy.types.Operator):
    bl_idname = "gamestudio.scene_view"
    bl_label = "Scene View"
    bl_description = ("Draw this 3D Viewport with the game's renderer: its shaders, lighting and effects, from "
                      "this view, without playing. Navigation, selection and Blender's overlays work as usual")

    @classmethod
    def description(cls, context, _properties):
        if context is not None and sceneview.is_area(getattr(context, "area", None), getattr(context, "screen", None)):
            return "Scene view on: the game draws this 3D Viewport. Click to let Blender draw it again"
        return cls.bl_description

    @classmethod
    def poll(cls, context):
        return context.area is not None and context.area.type == "VIEW_3D"

    def execute(self, context):
        problem = sceneview.toggle(context.window, context.screen, context.area, context.scene)
        if problem:
            self.report({"ERROR"}, problem)
            return {"CANCELLED"}
        return {"FINISHED"}


def split_and_enable(window) -> str:
    """Split the largest 3D View of the window's screen vertically and turn the Scene view on in the
    right half; a screen whose Scene view is on already is left as it is. Returns "" or why not."""
    screen = window.screen
    if sceneview.is_on() and any(sceneview.is_area(a, screen) for a in screen.areas):
        return ""
    areas = [a for a in screen.areas if a.type == "VIEW_3D"]
    if not areas:
        return "The window has no 3D Viewport to split"
    target = max(areas, key=lambda a: a.width * a.height)
    before = {a.as_pointer() for a in screen.areas}
    try:
        _split(window, screen, target)
    except (RuntimeError, TypeError) as exc:
        return f"Could not split the 3D Viewport: {exc}"
    halves = [target] + [a for a in screen.areas if a.as_pointer() not in before and a.type == "VIEW_3D"]
    right = max(halves, key=lambda a: a.x)
    return sceneview.enable(window, screen, right, window.scene)


def _split(window, screen, area) -> None:
    """Split the area into a left and a right half (both keep its 3D View and its view)."""
    with bpy.context.temp_override(window=window, screen=screen, area=area, region=sceneview.main_region(area)):
        bpy.ops.screen.area_split(direction="VERTICAL", factor=0.5)


def _window_by_pointer(pointer: int):
    wm = getattr(bpy.context, "window_manager", None)
    return next((w for w in (wm.windows if wm is not None else ()) if w.as_pointer() == pointer), None)


def _finish_split(pointer: int, workspace_name: str, deadline: float):
    """A timer: once the window shows the Game workspace, split its 3D View (see split_and_enable)."""
    window = _window_by_pointer(pointer)
    if window is None:
        return None
    if window.workspace.name != workspace_name and time.monotonic() < deadline:
        return 0.05
    problem = split_and_enable(window)
    if problem:
        game_stream.notify(problem, "ERROR")
    return None


class GAMESTUDIO_OT_scene_view_open(bpy.types.Operator):
    bl_idname = "gamestudio.scene_view_open"
    bl_label = "Scene View"
    bl_description = ("Show the level drawn by the game's renderer next to Blender's view: the Game "
                      "workspace's 3D Viewport is split and its right half becomes the Scene view")

    def execute(self, context):
        from .. import workspaces

        if project.project_root() is None:
            self.report({"ERROR"}, "Open a GameStudio project first")
            return {"CANCELLED"}
        window = context.window
        workspace = workspaces.find()
        if workspace is not None and window.workspace != workspace:
            workspaces.show(workspace, window)
            bpy.app.timers.register(
                lambda: _finish_split(window.as_pointer(), workspace.name, time.monotonic() + SPLIT_WAIT),
                first_interval=0.05)
            return {"FINISHED"}
        problem = split_and_enable(window)
        if problem:
            self.report({"ERROR"}, problem)
            return {"CANCELLED"}
        return {"FINISHED"}


_CLASSES = (GAMESTUDIO_OT_scene_view, GAMESTUDIO_OT_scene_view_open)


def register():
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    space = bpy.types.SpaceView3D
    _handles.extend((
        (space.draw_handler_add(_pre_view, (), "WINDOW", "PRE_VIEW"), "WINDOW"),
        (space.draw_handler_add(_post_view, (), "WINDOW", "POST_VIEW"), "WINDOW"),
        (space.draw_handler_add(_post_pixel, (), "WINDOW", "POST_PIXEL"), "WINDOW"),
    ))
    bpy.types.VIEW3D_HT_header.append(draw_header)
    bpy.types.VIEW3D_MT_view.append(draw_view_menu)
    game_stream.set_viewer(sceneview)
    sceneview.register()


def unregister():
    sceneview.unregister()
    game_stream.set_viewer(None)
    bpy.types.VIEW3D_MT_view.remove(draw_view_menu)
    bpy.types.VIEW3D_HT_header.remove(draw_header)
    for handle, region_type in _handles:
        bpy.types.SpaceView3D.draw_handler_remove(handle, region_type)
    _handles.clear()
    _frame.update(session=None, id=-1, texture=None, size=(0, 0))
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
