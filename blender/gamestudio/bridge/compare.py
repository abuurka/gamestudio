# SPDX-License-Identifier: GPL-3.0-or-later
"""Bridge methods for compare_views (core/compare.py): Blender's render of a camera view, and the
comparison of that render with the game's screenshot of the same view.

The view is a camera object, or a temporary camera posed for the call ({from, at, lens} or a named
view framed on targets) that is removed again afterwards. A posed camera is not in the export, so
the game half gets its pose in runtime space and builds the same camera in the page."""

import base64
import math

import bpy

from .api import BridgeError, method, names_param, param, resolve, rnd, vec
from .server import INVALID_PARAMS

POSE_VIEWS = ("iso", "front", "back", "left", "right", "side", "top", "bottom")
DEFAULT_LENS = 35.0
MARGIN = 0.08


def _compare():
    from ..core import compare

    return compare


def _size(params) -> tuple:
    size = params.get("size") or [800, 450]
    if (not isinstance(size, list) or len(size) != 2 or not all(isinstance(v, int) for v in size)
            or not all(64 <= v <= 2048 for v in size)):
        raise BridgeError("size must be [width, height] in pixels, 64-2048 each.", code=INVALID_PARAMS)
    return tuple(size)


# --- A posed camera ----------------------------------------------------------------------------------


def _fovs(lens: float, sensor: float, width: int, height: int) -> tuple:
    """(horizontal, vertical) field of view in radians of Blender's render (sensor fit AUTO: the sensor
    spans the longer side)."""
    wide = 2 * math.atan(sensor / 2 / lens)
    if width >= height:
        return wide, 2 * math.atan(math.tan(wide / 2) * height / width)
    return 2 * math.atan(math.tan(wide / 2) * width / height), wide


def _axes(forward):
    from mathutils import Vector

    forward = forward.normalized()
    hint = Vector((0, 1, 0)) if abs(forward.z) > 0.999 else Vector((0, 0, 1))
    right = forward.cross(hint).normalized()
    up = right.cross(forward).normalized()
    return right, up, -forward


def _frame(view: str, objs, lens: float, size: tuple) -> tuple:
    """(eye, target, near, far) that fit the objects' bounds in a perspective view from `view`."""
    from mathutils import Vector

    from .render import VIEW_DIRS, bounds

    lo, hi = bounds(objs)
    center = (lo + hi) / 2
    radius = max((hi - lo).length / 2, 0.05)
    right, up, back = _axes(-VIEW_DIRS[view])
    fov_w, fov_h = _fovs(lens, 36.0, *size)
    tan_w, tan_h = math.tan(fov_w / 2), math.tan(fov_h / 2)
    distance = 0.0
    for x in (lo.x, hi.x):
        for y in (lo.y, hi.y):
            for z in (lo.z, hi.z):
                d = Vector((x, y, z)) - center
                along = d.dot(back)
                distance = max(distance, along + abs(d.dot(right)) / tan_w, along + abs(d.dot(up)) / tan_h)
    distance *= 1 + 2 * MARGIN
    eye = center + back * distance
    return eye, center, max(0.01, distance - radius * 2) * 0.5, distance + radius * 4


def _pose_camera(params, size: tuple):
    """A temporary camera object for {camera: {from, at, lens?}} or {view, target?}, or None."""
    from mathutils import Matrix, Vector

    spec = params.get("camera")
    view = params.get("view")
    if not isinstance(spec, dict) and view is None:
        return None, None
    scene = bpy.context.scene
    lens = DEFAULT_LENS
    if isinstance(spec, dict):
        if view is not None:
            raise BridgeError("Pass camera: {from, at} or view, not both.", code=INVALID_PARAMS)
        eye, at = vec(spec, "from"), vec(spec, "at")
        if eye is None or at is None:
            raise BridgeError("camera as an object needs from and at: {\"from\": [x, y, z], \"at\": [x, y, z]} "
                              "(Blender space).", code=INVALID_PARAMS)
        lens = float(spec.get("lens") or (scene.camera.data.lens if scene.camera is not None
                                          and scene.camera.type == "CAMERA" else DEFAULT_LENS))
        eye, at = Vector(eye), Vector(at)
        if (at - eye).length < 1e-6:
            raise BridgeError("camera.from and camera.at are the same point.", code=INVALID_PARAMS)
        distance = (at - eye).length
        near, far = max(0.01, distance * 0.01), max(1000.0, distance * 20)
        label = "posed"
    else:
        view = param(params, "view", str, choices=POSE_VIEWS)
        if params.get("target"):
            objs = []
            for name in names_param(params, "target"):
                obj = resolve(name)
                objs += [o for o in (obj, *obj.children_recursive) if o not in objs]
        else:
            objs = [o for o in scene.objects if o.visible_get() and o.type not in ("CAMERA", "LIGHT")]
        if not objs:
            raise BridgeError("Nothing to frame.", hint="Pass target, or camera: {from, at}.")
        eye, at, near, far = _frame(view, objs, lens, size)
        label = view
    right, up, back = _axes(at - eye)
    data = bpy.data.cameras.new("GS_ComparePose")
    data.lens, data.sensor_width, data.sensor_fit = lens, 36.0, "AUTO"
    data.clip_start, data.clip_end = near, far
    cam = bpy.data.objects.new("GS_ComparePose", data)
    matrix = Matrix((right, up, back)).transposed().to_4x4()
    matrix.translation = eye
    cam.matrix_world = matrix
    scene.collection.objects.link(cam)
    bpy.context.view_layer.update()
    info = {"label": label, "from": rnd(eye), "at": rnd(at), "lens": round(lens, 2)}
    return cam, info


def _drop(cam) -> None:
    data = cam.data
    bpy.data.objects.remove(cam, do_unlink=True)
    bpy.data.cameras.remove(data)


def _runtime_pose(cam, size: tuple) -> dict:
    """The camera in the game's terms: runtime space (Y-up) position and quaternion [x, y, z, w], vertical fov."""
    from mathutils import Matrix

    loc, rot, _scale = (Matrix.Rotation(-math.pi / 2, 4, "X") @ cam.matrix_world).decompose()
    _wide, fov_v = _fovs(cam.data.lens, cam.data.sensor_width, *size)
    return {"position": [round(v, 5) for v in loc], "quaternion": [round(v, 6) for v in (rot.x, rot.y, rot.z, rot.w)],
            "fov_deg": round(math.degrees(fov_v), 5), "near": cam.data.clip_start, "far": cam.data.clip_end}


@method("render_camera_view")
def render_camera_view(params):
    compare = _compare()
    size = _size(params)
    camera = params.get("camera")
    engine = param(params, "engine", str, "eevee", choices=("eevee", "cycles", "workbench"))
    samples = int(params.get("samples", 16))
    posed, pose_info = _pose_camera(params, size)
    try:
        try:
            view = compare.render_view(posed if posed is not None else camera, size=size, engine=engine,
                                       samples=samples)
            if posed is None:
                request = compare.game_request(camera, size=size)
            else:
                request = {"scene": compare.game_request(posed, size=size)["scene"], "camera": f"({pose_info['label']})",
                           "camera_id": None, "active_camera": False, "size": list(size), "state": "stopped",
                           "frames": 3, "pose": _runtime_pose(posed, size)}
        except compare.CompareError as exc:
            raise BridgeError(str(exc), hint="Pass camera (a camera object's name), or set the scene camera.") from None
    finally:
        if posed is not None:
            _drop(posed)
    if posed is None and request["camera_id"] is None:
        raise BridgeError(f"Camera '{request['camera']}' has no entity id yet.", hint="Export the scene first.")
    out = {"png": base64.b64encode(view["png"]).decode("ascii"), "width": view["width"], "height": view["height"],
           "camera": request["camera"] if posed is not None else view["camera"],
           "camera_id": None if posed is not None else view["camera_id"], "engine": view["engine"],
           "seconds": view["seconds"], "request": request}
    if pose_info is not None:
        out["pose"] = pose_info
    return out


# --- Comparing ---------------------------------------------------------------------------------------


def _range(detail: float) -> str:
    return f"{detail:g}-{1 / detail:.3g}"


def verdict_reason(normalized: float, ratio: float, verdict: str) -> tuple:
    """(reason, metrics that kept it from the next better verdict) for compare_images' scores."""
    compare = _compare()
    limits = {name: (limit, detail) for name, limit, detail in compare.VERDICTS}
    better = {"close": "match", "different": "close"}.get(verdict)
    if better is None:
        limit, detail = limits["match"]
        return (f"match: normalized_diff {normalized:g} <= {limit:g} and detail_ratio {ratio:g} within "
                f"{_range(detail)}.", [])
    limit, detail = limits[better]
    failed = []
    reasons = []
    if normalized > limit:
        failed.append("normalized_diff")
        reasons.append(f"normalized_diff {normalized:g} is above {limit:g}")
    if not detail <= ratio <= 1 / detail:
        failed.append("detail_ratio")
        side = ("the game image has more fine detail than Blender's (noise, aliasing, shadow acne or banding)"
                if ratio > 1 else "the game image has less fine detail than Blender's (flat or low-res materials, "
                "blur)")
        reasons.append(f"detail_ratio {ratio:g} is outside {_range(detail)}: {side}")
    text = f"{verdict}, not {better}: " + "; ".join(reasons)
    if failed == ["detail_ratio"]:
        text += f" (normalized_diff {normalized:g} alone would allow {better})"
    elif failed == ["normalized_diff"]:
        text += f" (detail_ratio {ratio:g} is fine)"
    return text + ".", failed


def _detail_cells(blender_png: bytes, game_png: bytes, grid: int, ratio: float) -> list:
    """The grid cells that push detail_ratio most the way it is off: the most extra edge detail in the game
    (ratio above 1) or the most missing (below 1). Each with its own game / Blender detail ratio."""
    compare = _compare()
    a = compare.decode_image(blender_png)
    b = compare.decode_image(game_png)
    height, width = a.shape[:2]
    if b.shape[:2] != (height, width):
        b = compare._resize(b, height, width)
    cells = []
    for row in range(grid):
        for col in range(grid):
            y0, y1 = row * height // grid, (row + 1) * height // grid
            x0, x1 = col * width // grid, (col + 1) * width // grid
            ea, eb = compare._edge_energy(a[y0:y1, x0:x1]), compare._edge_energy(b[y0:y1, x0:x1])
            push = (eb - ea) if ratio > 1 else (ea - eb)
            cells.append((push, {"cell": [col, row], "box": [x0, y0, x1, y1],
                                 "detail_ratio": round(min(eb / ea, 99.0), 3) if ea > 1e-6 else 99.0}))
    cells.sort(key=lambda c: -c[0])
    return [c for push, c in cells[:3] if push > 0]


@method("compare_images")
def compare_images(params):
    compare = _compare()
    try:
        blender_png = base64.b64decode(param(params, "blender_png", str))
        game_png = base64.b64decode(param(params, "game_png", str))
    except ValueError:
        raise BridgeError("blender_png and game_png must be base64 PNG data.", code=INVALID_PARAMS) from None
    result = compare.compare_images(blender_png, game_png)
    reason, failed = verdict_reason(result["normalized_diff"], result["detail_ratio"], result["verdict"])
    result["verdict_reason"] = reason
    result["decided_by"] = failed
    if "detail_ratio" in failed:
        result["detail_cells"] = _detail_cells(blender_png, game_png, compare.GRID, result["detail_ratio"])
    for key in ("side_by_side_png", "diff_png"):
        result[key] = base64.b64encode(result[key]).decode("ascii")
    return result
