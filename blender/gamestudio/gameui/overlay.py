# SPDX-License-Identifier: GPL-3.0-or-later
"""The overlays of UI files (docs/GAME-UI.md §7.7): in the 3D views, the active top-level element's anchor, a
small diamond on the canvas frame (a corner, an edge's middle or the centre), and a thin line from it to the
element's origin. On other screen shapes the element keeps that distance to that point. And the view of every 3D
model (§11.1): the rectangle the game draws the model in, which its empty alone does not show.
"""

import bpy
import gpu
from gpu_extras.batch import batch_for_shader
from mathutils import Vector

from ..core import gameui as core
from . import build

COLOR = (1.0, 0.62, 0.1, 0.9)
MODEL_COLOR = (0.55, 0.75, 1.0, 0.6)
MARK_PX = 14.0  # the diamond's size in design px
LINE_WIDTH = 1.5


def model_frames(context) -> list | None:
    """The line segments (pairs of world points) of the views of the scene's 3D models, or None."""
    scene = getattr(context, "scene", None)
    if scene is None or scene.camera is None or not build.in_ui_file():
        return None
    k = core.px_per_unit(scene)
    lines = []
    for obj in scene.objects:
        if not build.is_model(obj) or not build.is_element(obj, scene):
            continue
        w, h = build.model_size(obj)
        matrix = build.world_matrix(obj)
        corners = [matrix @ Vector((x * w / 2 / k, y * h / 2 / k, 0.0))
                   for x, y in ((-1, -1), (1, -1), (1, 1), (-1, 1))]
        for i in range(4):
            lines += [tuple(corners[i]), tuple(corners[(i + 1) % 4])]
    return lines or None

_handle = None


def overlay_lines(context) -> list | None:
    """The overlay's line segments in world space (pairs of points), or None when there is nothing to show."""
    obj = getattr(context, "active_object", None)
    if obj is None or obj.parent is not None or not build.in_ui_file():
        return None
    scene = context.scene
    if scene is None or scene.camera is None or not build.is_element(obj, scene) or scene not in obj.users_scene:
        return None
    anchor, _stretch, _auto = build.resolve_anchor(scene, obj)
    ax, ay = core.from_px(scene, build.anchor_point_px(scene, anchor))
    origin = obj.matrix_world.translation
    z = origin.z
    r = MARK_PX / 2 / core.px_per_unit(scene)
    diamond = [(ax + r, ay, z), (ax, ay + r, z), (ax - r, ay, z), (ax, ay - r, z)]
    lines = [(ax, ay, z), (origin.x, origin.y, z)]
    for i in range(4):
        lines += [diamond[i], diamond[(i + 1) % 4]]
    return lines


def _draw():
    context = bpy.context
    try:
        sets = [(lines, color) for lines, color in ((model_frames(context), MODEL_COLOR),
                                                    (overlay_lines(context), COLOR)) if lines]
    except Exception:  # draw code: never raise
        return
    if not sets:
        return
    region = context.region
    shader = gpu.shader.from_builtin("POLYLINE_UNIFORM_COLOR")
    gpu.state.blend_set("ALPHA")
    gpu.state.depth_test_set("NONE")
    for lines, color in sets:
        batch = batch_for_shader(shader, "LINES", {"pos": lines})
        shader.uniform_float("viewportSize", (region.width, region.height))
        shader.uniform_float("lineWidth", LINE_WIDTH)
        shader.uniform_float("color", color)
        batch.draw(shader)
    gpu.state.blend_set("NONE")


def register():
    global _handle
    if _handle is None and not bpy.app.background:
        _handle = bpy.types.SpaceView3D.draw_handler_add(_draw, (), "WINDOW", "POST_VIEW")


def unregister():
    global _handle
    if _handle is not None:
        bpy.types.SpaceView3D.draw_handler_remove(_handle, "WINDOW")
        _handle = None
