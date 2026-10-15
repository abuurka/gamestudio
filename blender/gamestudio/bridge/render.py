# SPDX-License-Identifier: GPL-3.0-or-later
"""render_views: one labelled contact sheet that shows what is in the scene and how big it is.

* Views (front, back, left, right/side, top, bottom, iso, camera) are framed tightly on the
  targets' bounds with a margin, whatever their size, from a coin to a level.
* Shading: "studio" (default: Workbench studio light, material colours, cavity, outlines),
  "clay" (a neutral matcap for shape only) or "material" (EEVEE look-dev).
* Scale: orthographic views get rulers with world coordinates in metres and graph paper behind
  the objects, plus dimension lines for the targets' overall size; perspective views get a
  ground grid; a 1.8 m person stands next to the targets when that helps.
* Focus mode draws the rest of the scene ghosted and outlines the targets. Compare mode draws
  the same views of a checkpoint and of now, side by side.

Drawing goes through gpu.types.GPUOffScreen and a 3D Viewport's draw engine (gpu.init() makes
that work in --background too), with a Workbench render as the fallback. Only a temporary
scene that links the objects is drawn; helpers (grid, person) are temporary objects. Nothing in
the user's scenes, settings, selection or undo history changes.
"""

import base64
import math
import os
import struct
import tempfile
import time
import zlib

import bpy
import numpy as np
from mathutils import Matrix, Vector

from . import overlay
from .api import BridgeError, method, names_param, resolve, rnd
from .server import INVALID_PARAMS

# Direction from the targets' centre to the camera (Blender space, Z-up; front looks along +Y).
VIEW_DIRS = {
    "front": Vector((0, -1, 0)),
    "back": Vector((0, 1, 0)),
    "right": Vector((1, 0, 0)),
    "side": Vector((1, 0, 0)),
    "left": Vector((-1, 0, 0)),
    "top": Vector((0, 0, 1)),
    "bottom": Vector((0, 0, -1)),
    "iso": Vector((1, -1, 0.8)).normalized(),
}
VIEWS = (*VIEW_DIRS, "camera")
TITLES = {"front": "FRONT", "back": "BACK", "right": "RIGHT", "side": "SIDE (right)", "left": "LEFT",
          "top": "TOP", "bottom": "BOTTOM", "iso": "ISO", "camera": "CAMERA"}
DEFAULT_VIEWS = ("front", "side", "top", "iso")
COMPARE_VIEWS = ("iso", "top")
DEFAULT_SIZE = 768
SHADINGS = {"studio": "studio", "solid": "studio", "clay": "clay", "material": "material", "eevee": "material"}
ISO_FOV = math.radians(35.0)
MARGIN = 0.04
PERSON_HEIGHT = 1.8
GEOMETRY = ("MESH", "CURVE", "FONT", "SURFACE", "META", "VOLUME", "GPENCIL", "GREASEPENCIL", "POINTCLOUD", "CURVES")


# --- PNG ------------------------------------------------------------------------------------


def encode_png(pixels, width: int, height: int) -> bytes:
    """RGB PNG from a uint8 array of shape (height, width, 3+), top row first."""
    rows = np.ascontiguousarray(pixels[:, :, :3], dtype=np.uint8)
    raw = np.concatenate([np.zeros((height, 1), dtype=np.uint8), rows.reshape(height, width * 3)], axis=1)

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(raw.tobytes(), 6))
            + chunk(b"IEND", b""))


# --- Targets and bounds -------------------------------------------------------------------------


def _visible(obj) -> bool:
    try:
        return obj.visible_get()
    except RuntimeError:
        return not obj.hide_viewport


def _targets(params, scene) -> tuple[list, bool]:
    """(objects, whole_scene)."""
    target = params.get("target")
    if target in (None, "", "scene", "all"):
        objs = [o for o in scene.objects if _visible(o)]
        whole = True
    elif target == "selection":
        objs = list(bpy.context.selected_objects)
        whole = False
        if not objs:
            raise BridgeError("Nothing is selected.", hint="Pass object names as target.")
    elif isinstance(target, str) and target.startswith("collection:"):
        coll = bpy.data.collections.get(target.split(":", 1)[1])
        if coll is None:
            raise BridgeError(f"No collection '{target.split(':', 1)[1]}'.", hint="scene_summary lists the collections.")
        objs = list(coll.all_objects)
        whole = False
    else:
        objs = []
        for name in names_param(params, "target"):
            obj = resolve(name)
            for o in (obj, *obj.children_recursive):
                if o not in objs:
                    objs.append(o)
        whole = False
    if not objs:
        raise BridgeError("No objects to render.", hint="The scene is empty or everything is hidden.")
    return objs, whole


def _world_points(obj) -> list:
    if obj.type in GEOMETRY:
        return [obj.matrix_world @ Vector(c) for c in obj.bound_box]
    if obj.type == "EMPTY" and obj.instance_type == "COLLECTION" and obj.instance_collection:
        points = []
        offset = Matrix.Translation(-obj.instance_collection.instance_offset)
        for inner in obj.instance_collection.all_objects:
            if inner.type in GEOMETRY:
                points.extend(obj.matrix_world @ offset @ inner.matrix_world @ Vector(c) for c in inner.bound_box)
        return points
    return []


def bounds(objs) -> tuple[Vector, Vector]:
    points = [p for o in objs for p in _world_points(o)]
    if not points:
        points = [o.matrix_world.translation.copy() for o in objs] or [Vector((0, 0, 0))]
    lo = Vector([min(p[i] for p in points) for i in range(3)])
    hi = Vector([max(p[i] for p in points) for i in range(3)])
    pad = Vector([max(0.0, 0.05 - (hi[i] - lo[i])) / 2 for i in range(3)])  # never a zero-size box
    return lo - pad, hi + pad


def _corners(lo: Vector, hi: Vector) -> list:
    return [Vector((x, y, z)) for x in (lo.x, hi.x) for y in (lo.y, hi.y) for z in (lo.z, hi.z)]


# --- Framing -------------------------------------------------------------------------------------


def _axes(view: str):
    """(right, up, back) unit vectors of the view: the camera looks along -back."""
    back = VIEW_DIRS[view]
    up_hint = Vector((0, 1, 0)) if view in ("top", "bottom") else Vector((0, 0, 1))
    forward = -back
    right = forward.cross(up_hint).normalized()
    up = right.cross(forward).normalized()
    return right, up, back


class Frame:
    """A view's camera: matrices, and how world points map to tile pixels."""

    def __init__(self, view, width, height):
        self.view = view
        self.width = width
        self.height = height
        self.ortho = False
        self.half = 1.0          # ortho half-height in metres
        self.center = Vector()
        self.right = self.up = self.back = Vector()
        self.view_matrix = Matrix()
        self.projection = Matrix()
        self.camera_world = Matrix()
        self.fov = ISO_FOV
        self.near = 0.01
        self.far = 100.0

    @property
    def aspect(self):
        return self.width / self.height

    def to_pixel(self, p: Vector):
        """(x, y) in tile pixels, top-left origin, or None behind the camera."""
        clip = self.projection @ self.view_matrix @ Vector((p.x, p.y, p.z, 1.0))
        if clip.w <= 1e-9:
            return None
        return ((clip.x / clip.w) * 0.5 + 0.5) * self.width, (0.5 - (clip.y / clip.w) * 0.5) * self.height

    def pixels_per_metre(self, at: Vector | None = None) -> float:
        if self.ortho:
            return self.height / (2 * self.half)
        distance = (self.camera_world.translation - (at if at is not None else self.center)).length
        return self.height / (2 * distance * math.tan(self.fov / 2))


def frame_view(view: str, lo: Vector, hi: Vector, width: int, height: int, margin: float = MARGIN,
               bands=(0, 0, 0, 0)) -> Frame:
    """Frame the box tightly. bands = (left, right, top, bottom) pixels kept free for labels."""
    fr = Frame(view, width, height)
    right, up, back = _axes(view)
    fr.right, fr.up, fr.back = right, up, back
    center = (lo + hi) / 2
    corners = _corners(lo, hi)
    radius = max((hi - lo).length / 2, 0.05)
    aspect = width / height
    if view != "iso":
        fr.ortho = True
        xs = [(c - center).dot(right) for c in corners]
        ys = [(c - center).dot(up) for c in corners]
        left, right_band, top, bottom = bands
        avail_w = max(width - left - right_band, width * 0.4)
        avail_h = max(height - top - bottom, height * 0.4)
        span_x, span_y = max(xs) - min(xs), max(ys) - min(ys)
        mpp = max(span_x / avail_w, span_y / avail_h, 0.04 / height) * (1 + 2 * margin)  # metres per pixel
        # Centre the content in the free area, then centre the camera on the whole tile.
        free_cx = (min(xs) + max(xs)) / 2 - ((left - right_band) / 2) * mpp
        free_cy = (min(ys) + max(ys)) / 2 + ((top - bottom) / 2) * mpp
        fr.half = height * mpp / 2
        fr.center = center + right * free_cx + up * free_cy
        distance = radius * 4 + 1.0
        eye = fr.center + back * distance
        fr.near, fr.far = 0.01, distance + radius * 4
        r, t = fr.half * aspect, fr.half
        fr.projection = Matrix(((1 / r, 0, 0, 0), (0, 1 / t, 0, 0),
                                (0, 0, -2 / (fr.far - fr.near), -(fr.far + fr.near) / (fr.far - fr.near)),
                                (0, 0, 0, 1)))
    else:
        tan_h = math.tan(ISO_FOV / 2)
        tan_w = tan_h * aspect
        distance = 0.0
        for c in corners:
            d = c - center
            x, y, z = d.dot(right), d.dot(up), d.dot(back)
            distance = max(distance, z + abs(x) / tan_w, z + abs(y) / tan_h)
        distance *= 1 + 2 * margin + bands[2] / height
        # Shift the view so the content sits below the title band.
        mpp = 2 * distance * tan_h / height
        fr.center = center + up * (bands[2] / 2 * mpp)
        eye = fr.center + back * distance
        fr.near = max(distance - radius * 1.5, distance * 0.01)
        fr.far = distance + radius * 3
        f = 1 / tan_h
        n, fa = fr.near, fr.far
        fr.projection = Matrix(((f / aspect, 0, 0, 0), (0, f, 0, 0),
                                (0, 0, (fa + n) / (n - fa), 2 * fa * n / (n - fa)), (0, 0, -1, 0)))
    cam = Matrix((right, up, back)).transposed().to_4x4()
    cam.translation = eye
    fr.camera_world = cam
    fr.view_matrix = cam.inverted()
    return fr


def frame_camera(camera, scene, width: int, height: int) -> Frame:
    fr = Frame("camera", width, height)
    depsgraph = bpy.context.evaluated_depsgraph_get()
    fr.camera_world = camera.matrix_world.copy()
    fr.view_matrix = camera.matrix_world.inverted()
    fr.projection = camera.calc_matrix_camera(depsgraph, x=width, y=height,
                                              scale_x=scene.render.pixel_aspect_x, scale_y=scene.render.pixel_aspect_y)
    fr.ortho = camera.data.type == "ORTHO"
    return fr


# --- Helper geometry (grid, person) ----------------------------------------------------------------


def _helper_material(name: str, rgb) -> bpy.types.Material:
    mat = bpy.data.materials.new(name)
    mat.diffuse_color = (*rgb, 1.0)
    tree = mat.node_tree
    if tree is not None:
        bsdf = next((n for n in tree.nodes if n.type == "BSDF_PRINCIPLED"), None)
        if bsdf is not None:
            bsdf.inputs["Base Color"].default_value = (*rgb, 1.0)
            bsdf.inputs["Roughness"].default_value = 0.9
    return mat


def _person_mesh(name: str) -> bpy.types.Mesh:
    """A 1.8 m figure: legs, torso, arms and head as boxes and a sphere-ish head, origin at the feet."""
    import bmesh

    bm = bmesh.new()

    def box(cx, cy, cz, sx, sy, sz):
        geom = bmesh.ops.create_cube(bm, size=1.0)
        bmesh.ops.scale(bm, vec=(sx, sy, sz), verts=geom["verts"])
        bmesh.ops.translate(bm, vec=(cx, cy, cz), verts=geom["verts"])

    box(-0.1, 0, 0.43, 0.16, 0.2, 0.86)    # legs
    box(0.1, 0, 0.43, 0.16, 0.2, 0.86)
    box(0, 0, 1.17, 0.44, 0.24, 0.62)      # torso
    box(-0.29, 0, 1.12, 0.11, 0.13, 0.66)  # arms
    box(0.29, 0, 1.12, 0.11, 0.13, 0.66)
    head = bmesh.ops.create_icosphere(bm, subdivisions=2, radius=0.155)
    bmesh.ops.translate(bm, vec=(0, 0, PERSON_HEIGHT - 0.155), verts=head["verts"])
    mesh = bpy.data.meshes.new(name)
    bm.to_mesh(mesh)
    bm.free()
    return mesh


def _grid_mesh(name: str, x0, x1, y0, y1, step, z, width) -> bpy.types.Mesh:
    """Thin flat strips on the plane z: one per grid line (real faces, so they render and occlude)."""
    verts, faces = [], []

    def strip(ax, ay, bx, by, w):
        dx, dy = bx - ax, by - ay
        length = math.hypot(dx, dy) or 1.0
        nx, ny = -dy / length * w / 2, dx / length * w / 2
        i = len(verts)
        verts.extend([(ax + nx, ay + ny, z), (bx + nx, by + ny, z), (bx - nx, by - ny, z), (ax - nx, ay - ny, z)])
        faces.append((i, i + 1, i + 2, i + 3))

    n = 0
    x = math.ceil(x0 / step - 1e-6) * step
    while x <= x1 + 1e-6 and n < 400:
        strip(x, y0, x, y1, width * (2.0 if abs(x) < step * 1e-3 else 1.0))
        x += step
        n += 1
    y = math.ceil(y0 / step - 1e-6) * step
    while y <= y1 + 1e-6 and n < 800:
        strip(x0, y, x1, y, width * (2.0 if abs(y) < step * 1e-3 else 1.0))
        y += step
        n += 1
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(verts, [], faces)
    return mesh


# --- Temporary scenes ------------------------------------------------------------------------------


class Stage:
    """A throw-away scene that links objects (never copies them) plus helper objects."""

    def __init__(self, source_scene, objs, name="GS_AgentRender"):
        self.scene = bpy.data.scenes.new(name)
        self.scene.world = source_scene.world
        self.scene.render.fps = source_scene.render.fps
        self.scene.frame_current = source_scene.frame_current
        self.scene.display_settings.display_device = source_scene.display_settings.display_device
        self.objects = []
        for obj in objs:
            if obj.name not in self.scene.collection.objects:
                self.scene.collection.objects.link(obj)
                self.objects.append(obj)
        self.helpers = []    # (object, data) we created
        self.materials = []

    @property
    def view_layer(self):
        return self.scene.view_layers[0]

    def add_helper(self, name, mesh, material) -> bpy.types.Object:
        obj = bpy.data.objects.new(name, mesh)
        mesh.materials.append(material)
        obj.color = (*material.diffuse_color[:3], 1.0)
        self.scene.collection.objects.link(obj)
        self.helpers.append((obj, mesh))
        return obj

    def material(self, name, rgb):
        mat = _helper_material(name, rgb)
        self.materials.append(mat)
        return mat

    def remove_helper(self, obj) -> None:
        for i, (o, mesh) in enumerate(self.helpers):
            if o == obj:
                bpy.data.objects.remove(o, do_unlink=True)
                bpy.data.meshes.remove(mesh)
                del self.helpers[i]
                return

    def show_only(self, visible: set | None) -> None:
        """Hide everything not in `visible` (None shows all) in this stage's view layer."""
        layer = self.view_layer
        for obj in self.scene.collection.objects:
            want = visible is None or obj in visible
            if obj.hide_get(view_layer=layer) == want:
                obj.hide_set(not want, view_layer=layer)

    def update(self) -> None:
        self.view_layer.update()

    def close(self) -> None:
        for obj, mesh in self.helpers:
            bpy.data.objects.remove(obj, do_unlink=True)
            bpy.data.meshes.remove(mesh)
        self.helpers.clear()
        for mat in self.materials:
            bpy.data.materials.remove(mat)
        self.materials.clear()
        bpy.data.scenes.remove(self.scene)


# --- Drawing backends -------------------------------------------------------------------------------


def _view3d():
    wm = bpy.context.window_manager
    for window in getattr(wm, "windows", []):
        screen = window.screen
        if screen is None:
            continue
        for area in screen.areas:
            if area.type == "VIEW_3D":
                region = next((r for r in area.regions if r.type == "WINDOW"), None)
                if region is not None:
                    return area.spaces.active, region
    return None, None


def _apply_look(shading, look: str) -> None:
    if look == "material":
        shading.type = "MATERIAL"
        shading.use_scene_world = False
        shading.use_scene_lights = False
        return
    shading.type = "SOLID"
    shading.show_xray = False
    shading.show_cavity = True
    shading.cavity_type = "BOTH"
    shading.show_object_outline = True
    shading.object_outline_color = (0.05, 0.05, 0.06)
    shading.show_specular_highlight = True
    if look == "clay":
        shading.light = "STUDIO"
        try:
            shading.studio_light = "Default"
        except TypeError:
            pass
        shading.color_type = "SINGLE"
        shading.single_color = (0.62, 0.62, 0.63)
    else:
        shading.light = "STUDIO"
        try:
            shading.studio_light = "Default"
        except TypeError:
            pass
        shading.color_type = "TEXTURE"


_SHADING_PROPS = ("type", "light", "studio_light", "color_type", "single_color", "show_cavity", "cavity_type",
                  "show_object_outline", "object_outline_color", "show_specular_highlight", "show_xray",
                  "use_scene_world", "use_scene_lights", "background_type")


def _save_shading(shading) -> dict:
    saved = {}
    for name in _SHADING_PROPS:
        try:
            value = getattr(shading, name)
            saved[name] = tuple(value) if hasattr(value, "__len__") and not isinstance(value, str) else value
        except AttributeError:
            pass
    return saved


def _restore_shading(shading, saved: dict) -> None:
    for name in ("type", "light", "color_type"):
        if name in saved:
            setattr(shading, name, saved[name])
    for name, value in saved.items():
        try:
            setattr(shading, name, value)
        except (AttributeError, TypeError, ValueError):
            pass


class Painter:
    """Draws a stage from a Frame into RGBA pixels (top row first), offscreen or via Workbench."""

    def __init__(self, look: str, force_workbench: bool = False):
        self.look = look
        self.space, self.region = (None, None) if force_workbench else _view3d()
        self.backend = "offscreen" if self.space is not None else "workbench"
        self.saved = None
        if self.backend == "offscreen":
            overlay.ensure_gpu()
            self.saved = (_save_shading(self.space.shading), self.space.overlay.show_overlays)
            _apply_look(self.space.shading, look)
            self.space.overlay.show_overlays = False

    def close(self) -> None:
        if self.saved is not None:
            _restore_shading(self.space.shading, self.saved[0])
            self.space.overlay.show_overlays = self.saved[1]
            self.saved = None

    def draw(self, stage: Stage, fr: Frame) -> np.ndarray:
        if self.backend == "offscreen":
            try:
                return self._offscreen(stage, fr)
            except (SystemError, RuntimeError) as exc:
                self.close()
                self.backend = "workbench"
                self.fallback_reason = str(exc)
        return self._workbench(stage, fr)

    def _offscreen(self, stage, fr):
        import gpu

        offscreen = gpu.types.GPUOffScreen(fr.width, fr.height)
        try:
            offscreen.draw_view3d(stage.scene, stage.view_layer, self.space, self.region, fr.view_matrix,
                                  fr.projection, do_color_management=True, draw_background=False)
            with offscreen.bind():
                buf = gpu.state.active_framebuffer_get().read_color(0, 0, fr.width, fr.height, 4, 0, "UBYTE")
            buf.dimensions = fr.width * fr.height * 4
            return np.frombuffer(bytes(buf), dtype=np.uint8).reshape(fr.height, fr.width, 4)[::-1].copy()
        finally:
            offscreen.free()

    def _workbench(self, stage, fr):
        scene = stage.scene
        if self.look == "material":
            items = bpy.types.RenderSettings.bl_rna.properties["engine"].enum_items.keys()
            scene.render.engine = next((e for e in ("BLENDER_EEVEE", "BLENDER_EEVEE_NEXT") if e in items),
                                       "BLENDER_WORKBENCH")
        else:
            scene.render.engine = "BLENDER_WORKBENCH"
            _apply_look(scene.display.shading, self.look)
        data = bpy.data.cameras.new("GS_AgentRenderCam")
        cam = bpy.data.objects.new("GS_AgentRenderCam", data)
        scene.collection.objects.link(cam)
        cam.matrix_world = fr.camera_world
        if fr.view == "camera":
            source = bpy.context.scene.camera
            data.type, data.lens, data.sensor_width = source.data.type, source.data.lens, source.data.sensor_width
            data.ortho_scale, data.sensor_fit = source.data.ortho_scale, source.data.sensor_fit
            data.clip_start, data.clip_end = source.data.clip_start, source.data.clip_end
        elif fr.ortho:
            data.type = "ORTHO"
            data.ortho_scale = 2 * fr.half * max(fr.aspect, 1.0)
            data.clip_start, data.clip_end = fr.near, fr.far
        else:
            data.angle = fr.fov
            data.sensor_fit = "VERTICAL"
            data.clip_start, data.clip_end = fr.near, fr.far
        scene.camera = cam
        render = scene.render
        render.resolution_x, render.resolution_y, render.resolution_percentage = fr.width, fr.height, 100
        render.film_transparent = True
        render.image_settings.file_format = "PNG"
        render.image_settings.color_mode = "RGBA"
        fd, path = tempfile.mkstemp(prefix="gs_agent_render_", suffix=".png")
        os.close(fd)
        render.filepath = path
        image = None
        try:
            result = bpy.ops.render.render(write_still=True, scene=scene.name)
            if "FINISHED" not in result:
                raise BridgeError(f"The render returned {sorted(result)}.")
            image = bpy.data.images.load(path, check_existing=False)
            flat = np.empty(fr.width * fr.height * 4, dtype=np.float32)
            image.pixels.foreach_get(flat)
            return (flat.reshape(fr.height, fr.width, 4)[::-1] * 255).clip(0, 255).astype(np.uint8)
        finally:
            if image is not None:
                bpy.data.images.remove(image)
            bpy.data.objects.remove(cam, do_unlink=True)
            bpy.data.cameras.remove(data)
            try:
                os.remove(path)
            except OSError:
                pass


# --- One tile ------------------------------------------------------------------------------------


class Scene3:
    """What one column of the sheet shows: a stage, its targets and its scale helpers."""

    def __init__(self, stage, targets, context, label):
        self.stage = stage
        self.targets = targets
        self.context = context
        self.label = label


def _ground(lo: Vector, hi: Vector) -> float:
    """The ground height to draw: world z = 0 when the targets stand on, cross or float near it."""
    size = max(hi - lo)
    if lo.z - 1e-4 <= 0.0 <= hi.z + 1e-4 or 0.0 <= lo.z <= max(1.0, size):
        return 0.0
    return lo.z


def _add_grid(stage: Stage, fr: Frame, lo, hi, ground: float, material):
    """A ground grid for perspective and top/bottom views, sized to what the view shows."""
    if fr.view in ("top", "bottom"):
        w = fr.half * fr.aspect
        x0, x1 = fr.center.x - w, fr.center.x + w
        y0, y1 = fr.center.y - fr.half, fr.center.y + fr.half
        span = 2 * fr.half
        step = overlay.nice_step(span, 6)
        width = 1.3 / fr.pixels_per_metre()
    else:
        size = max(hi.x - lo.x, hi.y - lo.y, 0.1)
        pad = size * 0.35
        span = size + 2 * pad
        step = overlay.nice_step(span, 7)
        x0, x1 = lo.x - pad, hi.x + pad
        y0, y1 = lo.y - pad, hi.y + pad
        width = 1.3 / fr.pixels_per_metre(Vector(((lo.x + hi.x) / 2, (lo.y + hi.y) / 2, ground)))
    x0, x1 = math.floor(x0 / step) * step, math.ceil(x1 / step) * step
    y0, y1 = math.floor(y0 / step) * step, math.ceil(y1 / step) * step
    lift = max(0.0005, 0.002 * max(hi - lo))
    mesh = _grid_mesh("GS_AgentGrid", x0, x1, y0, y1, step, ground + lift, width)
    obj = stage.add_helper("GS_AgentGrid", mesh, material)
    return obj, {"step": step, "x": (x0, x1), "y": (y0, y1), "z": ground}


def _metric_paper(img, fr: Frame, labels, ox: int, oy: int, size_px: int, ground: float) -> float:
    """Graph paper behind the objects and rulers along the edges of an orthographic tile."""
    w, h = fr.width, fr.height
    half_w = fr.half * fr.aspect
    step = overlay.nice_step(2 * fr.half, 6)
    ppm = fr.pixels_per_metre()
    cr, cu = fr.center.dot(fr.right), fr.center.dot(fr.up)
    # vertical lines: constant coordinate along `right`
    k0 = math.ceil((cr - half_w) / step - 1e-9)
    k1 = math.floor((cr + half_w) / step + 1e-9)
    every = max(1, math.ceil(40 / max(step * ppm, 1)))  # at least ~40 px between labels
    for k in range(k0, k1 + 1):
        s = k * step
        x = int(round((s - cr) * ppm + w / 2))
        overlay.vline(img, x, 0, h - 1, overlay.PAPER_MAJOR if k == 0 else overlay.PAPER_MINOR)
        if k % every == 0:
            labels.add(ox + x, oy + h - 9, overlay.fmt_m(_axis_value(fr.right, s), step), size=size_px - 2,
                       anchor="center", box=False, avoid=True)
    k0 = math.ceil((cu - fr.half) / step - 1e-9)
    k1 = math.floor((cu + fr.half) / step + 1e-9)
    every = max(1, math.ceil(24 / max(step * ppm, 1)))
    for k in range(k0, k1 + 1):
        s = k * step
        y = int(round(h / 2 - (s - cu) * ppm))
        overlay.hline(img, y, 0, w - 1, overlay.PAPER_MAJOR if k == 0 else overlay.PAPER_MINOR)
        if k % every == 0 and size_px * 2 + 4 < y < h - 18:
            labels.add(ox + 3, oy + y, overlay.fmt_m(_axis_value(fr.up, s), step), size=size_px - 2, box=False,
                       avoid=True)
    if abs(fr.up.z) > 0.9:  # side views: the ground line
        y = int(round(h / 2 - (ground * (1 if fr.up.z > 0 else -1) - cu) * ppm))
        overlay.hline(img, y, 0, w - 1, overlay.GROUND, width=2)
    return step



def _axis_name(v: Vector) -> str:
    i = max(range(3), key=lambda k: abs(v[k]))
    return ("-" if v[i] < 0 else "") + "xyz"[i]


def _axis_value(v: Vector, s: float) -> float:
    """The world coordinate on the axis `v` (+/- a world axis) for a distance s along v."""
    i = max(range(3), key=lambda k: abs(v[k]))
    return s if v[i] > 0 else -s


def _dimension_lines(img, fr: Frame, lo: Vector, hi: Vector, labels, ox, oy, size_px) -> None:
    """Engineering-style overall dimensions of the targets in an orthographic tile."""
    pts = [fr.to_pixel(c) for c in _corners(lo, hi)]
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    w, h = fr.width, fr.height
    extent = lambda v: abs((hi - lo).dot(Vector([abs(c) for c in v])))  # noqa: E731
    width_m, height_m = extent(fr.right), extent(fr.up)
    y = y1 + 12 if y1 + 22 < h - 16 else y0 - 12
    y = min(max(y, 8), h - 22)
    overlay.hline(img, y, x0, x1, overlay.DIM)
    overlay.vline(img, x0, y - 4, y + 4, overlay.DIM)
    overlay.vline(img, x1, y - 4, y + 4, overlay.DIM)
    labels.add(ox + (x0 + x1) / 2, oy + y, f"{overlay.fmt_m(width_m)} m", size=size_px, color=overlay.TEXT_DIM,
               anchor="center")
    x = x1 + 12 if x1 + 60 < w else x0 - 12
    x = min(max(x, 6), w - 8)
    overlay.vline(img, x, y0, y1, overlay.DIM)
    overlay.hline(img, y0, x - 4, x + 4, overlay.DIM)
    overlay.hline(img, y1, x - 4, x + 4, overlay.DIM)
    labels.add(ox + x + (6 if x1 + 60 < w else -6), oy + (y0 + y1) / 2, f"{overlay.fmt_m(height_m)} m",
               size=size_px, color=overlay.TEXT_DIM, anchor="left" if x1 + 60 < w else "right")


def _name_labels(fr: Frame, objs, labels, ox, oy, size_px, limit=40, whole=False) -> list:
    """Label objects at their tops: the largest first, or (whole) the given objects in order, each with the
    bounds of its children too (an armature root, an empty). Returns the names placed."""
    def points_of(obj):
        return [p for o in (obj, *obj.children_recursive) for p in _world_points(o)] if whole else _world_points(obj)

    placed = []
    ranked = list(objs) if whole else sorted((o for o in objs if _world_points(o)), key=lambda o: -max(o.dimensions))
    for obj in ranked[:limit * 2]:
        points = points_of(obj) or [obj.matrix_world.translation.copy()]
        lo = Vector([min(p[i] for p in points) for i in range(3)])
        hi = Vector([max(p[i] for p in points) for i in range(3)])
        top = fr.to_pixel(Vector(((lo.x + hi.x) / 2, (lo.y + hi.y) / 2, hi.z)))
        if top is None or not (0 <= top[0] <= fr.width and 0 <= top[1] <= fr.height):
            continue
        if labels.add(ox + top[0], oy + top[1] - size_px * 0.6, obj.name, size=size_px - 1, anchor="center",
                      avoid=not whole):
            placed.append(obj.name)
        if len(placed) >= limit:
            break
    return placed


def _chosen_labels(params):
    """The objects named by names: [...] (or labels: [...]) to label, or None for automatic names."""
    chosen = None
    for key in ("names", "labels"):
        value = params.get(key)
        if isinstance(value, list):
            if not all(isinstance(v, str) and v for v in value):
                raise BridgeError(f"{key} must be a list of object names.", code=INVALID_PARAMS)
            chosen = (chosen or []) + [n for n in value if n not in (chosen or [])]
    if chosen is None:
        return None
    return [resolve(n) for n in chosen]


# --- Checkpoints for compare mode -------------------------------------------------------------------

_ID_COLLECTIONS = ("objects", "meshes", "materials", "images", "textures", "node_groups", "collections",
                   "scenes", "worlds", "cameras", "lights", "curves", "actions", "armatures", "lattices",
                   "metaballs", "fonts", "grease_pencils", "particles", "volumes", "pointclouds",
                   "hair_curves", "lightprobes", "speakers")


def _id_sets() -> dict:
    out = {}
    for name in _ID_COLLECTIONS:
        coll = getattr(bpy.data, name, None)
        if coll is not None:
            out[name] = set(coll)
    return out


class CheckpointScene:
    """A checkpoint's scene appended into this file for one call, then removed again."""

    def __init__(self, checkpoint_id: str):
        from . import methods

        root = methods._require_root()
        match = next((d for d in methods._all_checkpoints(root)
                      if d["id"] == checkpoint_id or d["id"].startswith(checkpoint_id)), None)
        if match is None:
            raise BridgeError(f"No checkpoint '{checkpoint_id}'.", hint="Call list_checkpoints for the ids.")
        self.info = match
        path = methods._checkpoints_dir(root) / match["id"] / match["file"]
        before = _id_sets()
        with bpy.data.libraries.load(str(path), link=False) as (data_from, data_to):
            wanted = match.get("scene") if match.get("scene") in data_from.scenes else data_from.scenes[0]
            data_to.scenes = [wanted]
        self.scene = data_to.scenes[0]
        after = _id_sets()
        self.new_ids = [i for name, ids_now in after.items() for i in ids_now - before.get(name, set())]

    def objects(self, names=None):
        objs = [o for o in self.scene.objects if not o.hide_viewport and not o.hide_get(view_layer=self.scene.view_layers[0])]
        if names is None:
            return objs
        # Appended objects may have been renamed (Crate -> Crate.001): match by the original name too.
        wanted = set(names)
        return [o for o in objs if o.name in wanted or o.name.rsplit(".", 1)[0] in wanted]

    def close(self):
        bpy.data.batch_remove(self.new_ids)
        self.new_ids = []


# --- The method ------------------------------------------------------------------------------------


@method("render_views")
def render_views(params):
    started = time.perf_counter()
    source = bpy.context.scene
    compare_id = params.get("compare")
    views = params.get("views") or list(COMPARE_VIEWS if compare_id else DEFAULT_VIEWS)
    if isinstance(views, str):
        views = [views]
    unknown = [v for v in views if v not in VIEWS]
    if unknown:
        raise BridgeError(f"Unknown view(s): {', '.join(map(str, unknown))}.", hint=f"Views: {', '.join(VIEWS)}.",
                          code=INVALID_PARAMS)
    size = int(params.get("size", DEFAULT_SIZE))
    if not 128 <= size <= 2048:
        raise BridgeError("size must be between 128 and 2048 pixels (the sheet width).", code=INVALID_PARAMS)
    look = SHADINGS.get(params.get("shading", "studio"))
    if look is None:
        raise BridgeError("shading must be 'studio' (default), 'clay' (shape only) or 'material' (EEVEE).",
                          code=INVALID_PARAMS)
    if "camera" in views and source.camera is None:
        raise BridgeError("The scene has no active camera.", hint="Use another view, or set scene.camera.")
    focus = bool(params.get("focus", False))
    show_labels = params.get("labels", True) is not False
    chosen = _chosen_labels(params)

    targets, whole_scene = _targets(params, source)
    if not params.get("views") and not compare_id:
        t_lo, t_hi = bounds(targets)
        dims = t_hi - t_lo
        if dims.z < 0.35 * max(dims.x, dims.y) and max(dims.x, dims.y) > 8:
            views = ["top", "iso"]  # a level: the front and side views would be thin strips
    context = []
    if focus and not whole_scene:
        context = [o for o in source.objects if _visible(o) and o not in targets]
    lo, hi = bounds(targets)
    size_m = hi - lo
    largest = max(size_m)
    person = params.get("person", "auto")
    want_person = (0.4 <= largest <= 80) if person == "auto" else bool(person)
    names = params.get("names", "auto")
    mesh_targets = [o for o in targets if o.type in GEOMETRY]
    want_names = chosen is None and ((1 < len(mesh_targets) <= 12 and not whole_scene) if names == "auto"
                                     else bool(names))
    labelled = set()

    # Columns: the scene now, and a checkpoint on the left in compare mode.
    columns = []
    checkpoint = None
    stages = []
    painter = None
    try:
        if compare_id:
            checkpoint = CheckpointScene(str(compare_id))
            names_now = None if whole_scene else [o.name for o in targets]
            old_targets = checkpoint.objects(names_now)
            if not old_targets:
                raise BridgeError("The targets do not exist in that checkpoint.",
                                  hint="Compare the whole scene (no target), or other objects.")
            old_lo, old_hi = bounds(old_targets)
            lo = Vector([min(lo[i], old_lo[i]) for i in range(3)])
            hi = Vector([max(hi[i], old_hi[i]) for i in range(3)])
            stage = Stage(source, old_targets, "GS_AgentRenderBefore")
            stages.append(stage)
            columns.append(Scene3(stage, old_targets, [], f"BEFORE: {checkpoint.info.get('label', '')}"[:40]))
        stage = Stage(source, targets + context)
        stages.append(stage)
        columns.append(Scene3(stage, targets, context, "NOW" if compare_id else ""))

        ground = _ground(lo, hi)
        person_at = None
        if want_person:
            # Front-left of the targets: beside them in every view, never in front.
            gap = max(0.3, 0.08 * largest)
            person_at = Vector((lo.x - gap - 0.4, lo.y - gap - 0.15, ground))
            frame_lo = Vector((person_at.x - 0.4, person_at.y - 0.15, min(lo.z, ground)))
            frame_hi = Vector((hi.x, hi.y, max(hi.z, ground + PERSON_HEIGHT)))
        else:
            frame_lo, frame_hi = lo.copy(), hi.copy()
        frame_lo.z = min(frame_lo.z, ground)

        n_cols = len(columns) if compare_id else (1 if len(views) == 1 else (2 if len(views) <= 4 else 3))
        n_rows = len(views) if compare_id else math.ceil(len(views) / n_cols)
        tile = max(64, size // n_cols)
        font = max(11, min(18, round(tile / 30)))
        sheet = np.zeros((tile * n_rows, tile * n_cols, 3), dtype=np.uint8)
        labels = overlay.Labels()
        painter = Painter(look, force_workbench=params.get("backend") == "workbench")

        grid_mat = stages[-1].material("GS_AgentGridMat", (0.62, 0.62, 0.6))
        person_mat = stages[-1].material("GS_AgentPersonMat", (0.38, 0.55, 0.85))
        for stage in stages:
            if person_at is not None:
                obj = stage.add_helper("GS_AgentPerson", _person_mesh("GS_AgentPerson"), person_mat)
                obj.location = person_at

        info_views = []
        for vi, view in enumerate(views):
            for ci, column in enumerate(columns):
                r, c = (vi, ci) if compare_id else divmod(vi, n_cols)
                ox, oy = c * tile, r * tile
                stage = column.stage
                if view == "camera":
                    fr = frame_camera(source.camera, source, tile, tile)
                elif view == "iso":
                    fr = frame_view(view, frame_lo, frame_hi, tile, tile, bands=(0, 0, font * 2 if show_labels else 0, 0))
                else:
                    b = (font * 3.2, font * 5.0, font * 2.6, font * 3.0) if show_labels else (0, 0, font * 1.8, 0)
                    fr = frame_view(view, frame_lo, frame_hi, tile, tile, bands=b)
                bg = overlay.gradient(tile, tile)
                step = None
                grid_obj = None
                is_camera = view == "camera"
                if not is_camera and (not fr.ortho or view in ("top", "bottom")):
                    grid_obj, grid = _add_grid(stage, fr, lo, hi, ground, grid_mat)
                    step = grid["step"]
                helpers = set() if is_camera else {o for o, _m in stage.helpers}
                if fr.ortho and not is_camera:
                    paper_step = _metric_paper(bg, fr, labels, ox, oy, font, ground)
                    step = step if step is not None else paper_step

                if column.context:
                    # Ghosted context, then the helpers, then the targets on top with an outline.
                    stage.show_only(set(column.context))
                    stage.update()
                    img = overlay.ghost(bg, painter.draw(stage, fr))
                    if helpers:
                        stage.show_only(helpers)
                        stage.update()
                        img = overlay.composite(img, painter.draw(stage, fr))
                    stage.show_only(set(column.targets))
                    stage.update()
                    tgt_rgba = painter.draw(stage, fr)
                    img = overlay.composite(img, tgt_rgba)
                    mask = tgt_rgba[:, :, 3] > 40
                    if mask.any() and not mask.all():
                        overlay.outline(img, mask, overlay.HIGHLIGHT, 2)
                else:
                    stage.show_only(set(column.targets) | set(column.context) | helpers)
                    stage.update()
                    img = overlay.composite(bg, painter.draw(stage, fr))

                if grid_obj is not None:
                    stage.remove_helper(grid_obj)

                if show_labels and not is_camera:
                    if fr.ortho:
                        _dimension_lines(img, fr, lo, hi, labels, ox, oy, font)
                    if person_at is not None and fr.ortho and abs(fr.up.z) > 0.9:
                        head = fr.to_pixel(person_at + Vector((0, 0, PERSON_HEIGHT)))
                        if head is not None:
                            labels.add(ox + head[0], oy + head[1] - font, "1.8 m", size=font - 2, anchor="center",
                                       avoid=True)
                    if want_names:
                        _name_labels(fr, column.targets, labels, ox, oy, font)
                if chosen is not None and show_labels:
                    named = chosen if column.stage is stages[-1] else [
                        o for o in column.targets + column.context
                        if o.name in {c.name for c in chosen} or o.name.rsplit(".", 1)[0] in {c.name for c in chosen}]
                    labelled.update(_name_labels(fr, named, labels, ox, oy, font, limit=len(named), whole=True))
                title = TITLES[view]
                if fr.ortho and not is_camera:
                    title += f" ({_axis_name(fr.right)} right, {_axis_name(fr.up)} up)"
                title += f"  {column.label}" if column.label else ""
                if step is not None:
                    title += f"  grid {overlay.fmt_m(step)} m"
                if view == "iso" and show_labels:
                    title += f"  {overlay.fmt_m(size_m.x)}x{overlay.fmt_m(size_m.y)}x{overlay.fmt_m(size_m.z)} m"
                labels.add(ox + 5, oy + font, title, size=font, box=True)
                sheet[oy:oy + tile, ox:ox + tile] = img
                if ci == len(columns) - 1:
                    info_views.append({"view": view, "grid_m": step, "ortho": fr.ortho,
                                       **({"axes": {"right": _axis_name(fr.right), "up": _axis_name(fr.up)}}
                                          if fr.ortho and not is_camera else {}),
                                       "metres_per_pixel": round(1 / fr.pixels_per_metre(), 5) if fr.ortho else None})
        # Separators between tiles.
        for c in range(1, n_cols):
            sheet[:, c * tile - 1:c * tile + 1] = 18
        for r in range(1, n_rows):
            sheet[r * tile - 1:r * tile + 1, :] = 18
        text_ok = labels.draw(sheet)
        backend = painter.backend
    finally:
        if painter is not None:
            painter.close()
        for stage in stages:
            stage.close()
        if checkpoint is not None:
            checkpoint.close()

    h, w = sheet.shape[:2]
    legend = ("Blender space, Z-up. Rulers and grids are world coordinates in metres (graph paper behind the "
              "objects in side views); yellow lines give the targets' overall size")
    if person_at is not None:
        legend += "; the blue figure is a 1.8 m person"
    if context:
        legend += "; other objects are ghosted, the targets outlined in orange"
    out = {
        "backend": backend,
        "shading": look,
        "layout": ([[f"{v} before", f"{v} now"] for v in views] if compare_id
                   else [views[r * n_cols:(r + 1) * n_cols] for r in range(n_rows)]),
        "width": w, "height": h,
        "target": [o.name for o in targets][:30] + (["..."] if len(targets) > 30 else []),
        "objects": len(targets),
        "bounds": {"min": rnd(lo), "max": rnd(hi), "size_m": rnd(hi - lo)},
        "views": info_views,
        "person": person_at is not None,
        "legend": legend,
        "seconds": round(time.perf_counter() - started, 3),
        "png": base64.b64encode(encode_png(sheet, w, h)).decode("ascii"),
    }
    if compare_id:
        out["compare"] = {"checkpoint": checkpoint.info["id"], "label": checkpoint.info.get("label")}
    if chosen is not None:
        out["labelled"] = sorted(labelled)
        missed = [o.name for o in chosen if o.name not in labelled]
        if missed:
            out["not_in_view"] = missed
    if not text_ok:
        out["note"] = "Labels could not be drawn (no GPU); the image has no text."
    return out
