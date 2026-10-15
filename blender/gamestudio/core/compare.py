# SPDX-License-Identifier: GPL-3.0-or-later
"""Compare what Blender shows with what the game shows, from the same camera.

The Blender half of a side-by-side check for agents (the MCP tool compare_views):

    view = compare.render_view(camera="Camera", size=(800, 450))   # Blender's render, PNG bytes
    want = compare.game_request(camera="Camera", size=(800, 450))  # what the game screenshot must be
    # ...the MCP server opens the game headless as `want` says and takes a screenshot...
    result = compare.compare_images(view["png"], game_png)         # scores and a side-by-side PNG

compare_views(game_png) does the render and the comparison in one call.

The render keeps the scene's own Color Management (view transform, look and
exposure). The game follows the same view transform and exposure (the manifest's
settings.toneMapping and settings.exposure, docs/SPEC.md §4.2), so the two
tone-map alike; only transforms three.js lacks (Filmic, ACES 2.0, False Color,
Raw) and looks differ, and the export warns about those.

Scores (both images as displayed, 0-1 per channel):
- mean_abs_diff: the mean per-pixel difference (0 = identical).
- normalized_diff: the same after giving each image the same brightness and
  contrast, so light intensity and any remaining tone-mapping difference count
  less than content.
- detail_ratio: edge energy of the game image over Blender's. Well below 1 means
  detail went missing (flat procedural materials, low-res textures); well above
  1, noise or aliasing.
- worst_cells: the 4x4 grid cells that differ most, to look at first.
The side-by-side image is [Blender | game | difference x4].
"""

import os
import pathlib
import struct
import tempfile
import time
import zlib

import bpy

from . import ids
from .log import get_logger

log = get_logger(__name__)

DEFAULT_SIZE = (800, 450)
ENGINES = {"eevee": ("BLENDER_EEVEE", "BLENDER_EEVEE_NEXT"), "cycles": ("CYCLES",),
           "workbench": ("BLENDER_WORKBENCH",)}
GRID = 4
GAP = 4
# normalized_diff / detail_ratio limits of each verdict.
VERDICTS = (("match", 0.12, 0.75), ("close", 0.25, 0.5))


class CompareError(RuntimeError):
    pass


# --- PNG ---------------------------------------------------------------------------------


def encode_png(pixels) -> bytes:
    """RGB PNG from a uint8 array (height, width, 3), top row first."""
    import numpy as np

    rows = np.ascontiguousarray(pixels[:, :, :3], dtype=np.uint8)
    height, width = rows.shape[:2]
    raw = np.concatenate([np.zeros((height, 1), dtype=np.uint8), rows.reshape(height, width * 3)], axis=1)

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(raw.tobytes(), 6))
            + chunk(b"IEND", b""))


def decode_image(data: bytes):
    """float32 RGB array (height, width, 3) in 0-1, top row first, from PNG/JPEG/WebP bytes."""
    import numpy as np

    suffix = ".png" if data[:4] == b"\x89PNG" else ".jpg" if data[:2] == b"\xff\xd8" else ".webp"
    fd, path = tempfile.mkstemp(prefix="gs_compare_", suffix=suffix)
    image = None
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        image = bpy.data.images.load(path, check_existing=False)
        image.colorspace_settings.name = "Non-Color"  # the stored values, not a colour transform
        width, height = image.size
        if not width or not height:
            raise CompareError("could not decode the image")
        flat = np.empty(width * height * 4, dtype=np.float32)
        image.pixels.foreach_get(flat)
    finally:
        if image is not None:
            bpy.data.images.remove(image)
        try:
            os.remove(path)
        except OSError:
            pass
    rgba = flat.reshape(height, width, 4)[::-1]
    return rgba[:, :, :3] * rgba[:, :, 3:4]  # transparent areas count as black


# --- Blender half -----------------------------------------------------------------------------


def _camera(scene, camera):
    if camera is None:
        if scene.camera is None:
            raise CompareError("The scene has no active camera; pass one")
        return scene.camera
    obj = bpy.data.objects.get(camera) if isinstance(camera, str) else camera
    if obj is None or obj.type != "CAMERA":
        raise CompareError(f"'{camera}' is not a camera object")
    return obj


def _set_engine(render, engine: str) -> str:
    for name in ENGINES.get(engine, (engine,)):
        try:
            render.engine = name
            return name
        except TypeError:
            continue
    raise CompareError(f"Render engine '{engine}' is not available (use eevee, cycles or workbench)")


def render_view(camera=None, scene=None, size=DEFAULT_SIZE, engine: str = "eevee", samples: int = 16,
                view_transform: str | None = None) -> dict:
    """Render scene from camera (default: the active camera) at size, with the scene's lights and world.

    The scene's view transform is used, as the game uses it (view_transform overrides it for this
    render only). Returns {"png", "width", "height", "camera", "camera_id", "engine", "seconds"}.
    Render settings are restored afterwards; nothing else in the file changes.
    """
    from .util import any_window

    scene = scene or bpy.context.scene
    cam = _camera(scene, camera)
    width, height = int(size[0]), int(size[1])
    render = scene.render
    image_settings = render.image_settings
    saved = {
        "camera": scene.camera, "engine": render.engine, "x": render.resolution_x, "y": render.resolution_y,
        "percentage": render.resolution_percentage, "border": render.use_border, "filepath": render.filepath,
        "film": render.film_transparent, "format": image_settings.file_format, "mode": image_settings.color_mode,
        "depth": image_settings.color_depth, "view": scene.view_settings.view_transform,
        "eevee": getattr(scene.eevee, "taa_render_samples", None),
        "cycles": getattr(getattr(scene, "cycles", None), "samples", None),
    }
    fd, path = tempfile.mkstemp(prefix="gs_view_", suffix=".png")
    os.close(fd)
    started = time.perf_counter()
    try:
        scene.camera = cam
        used = _set_engine(render, engine)
        render.resolution_x, render.resolution_y, render.resolution_percentage = width, height, 100
        render.use_border = False
        render.film_transparent = False
        render.filepath = path
        image_settings.file_format = "PNG"
        image_settings.color_mode = "RGB"
        image_settings.color_depth = "8"
        if view_transform:
            scene.view_settings.view_transform = view_transform
        if used.startswith("BLENDER_EEVEE") and saved["eevee"] is not None:
            scene.eevee.taa_render_samples = samples
        elif used == "CYCLES" and saved["cycles"] is not None:
            scene.cycles.samples = samples
        window = any_window()
        override = {"window": window, "scene": scene} if window is not None else {"scene": scene}
        with bpy.context.temp_override(**override):
            result = bpy.ops.render.render(write_still=True, scene=scene.name)
        if "FINISHED" not in result:
            raise CompareError(f"The render returned {sorted(result)}")
        png = pathlib.Path(path).read_bytes()
    finally:
        scene.camera = saved["camera"]
        render.engine = saved["engine"]
        render.resolution_x, render.resolution_y = saved["x"], saved["y"]
        render.resolution_percentage = saved["percentage"]
        render.use_border = saved["border"]
        render.filepath = saved["filepath"]
        render.film_transparent = saved["film"]
        image_settings.file_format = saved["format"]
        image_settings.color_mode = saved["mode"]
        image_settings.color_depth = saved["depth"]
        scene.view_settings.view_transform = saved["view"]
        if saved["eevee"] is not None:
            scene.eevee.taa_render_samples = saved["eevee"]
        if saved["cycles"] is not None:
            scene.cycles.samples = saved["cycles"]
        try:
            os.remove(path)
        except OSError:
            pass
    return {"png": png, "width": width, "height": height, "camera": cam.name, "camera_id": ids.get_id(cam),
            "engine": used, "seconds": round(time.perf_counter() - started, 3)}


def game_request(camera=None, scene=None, size=DEFAULT_SIZE) -> dict:
    """What the game half must screenshot to match render_view (see the module docstring and docs/MCP.md).

    {"scene": export name, "camera": name, "camera_id": gs_id (null before the first export),
     "active_camera": whether it is the scene camera (the game's default), "size": [w, h],
     "state": "stopped" (loaded state: no physics or animation has run), "frames": frames to wait}.
    """
    from . import export

    scene = scene or bpy.context.scene
    cam = _camera(scene, camera)
    return {"scene": export.export_name(scene), "camera": cam.name, "camera_id": ids.get_id(cam),
            "active_camera": cam == scene.camera, "size": [int(size[0]), int(size[1])], "state": "stopped",
            "frames": 3}


# --- Comparison -------------------------------------------------------------------------------


def _resize(image, height: int, width: int):
    import numpy as np

    rows = (np.arange(height) * image.shape[0] / height).astype(int)
    cols = (np.arange(width) * image.shape[1] / width).astype(int)
    return image[rows][:, cols]


def _standardize(image):
    import numpy as np

    mean = image.mean(axis=(0, 1), keepdims=True)
    std = image.std(axis=(0, 1), keepdims=True)
    return (image - mean) / np.maximum(std, 1e-3)


def _edge_energy(image) -> float:
    import numpy as np

    luma = image @ np.array([0.2126, 0.7152, 0.0722], dtype=np.float32)
    return float(np.abs(np.diff(luma, axis=0)).mean() + np.abs(np.diff(luma, axis=1)).mean())


def _heat(diff):
    """Difference (0-1) as a black-red-yellow image."""
    import numpy as np

    d = np.clip(diff * 4.0, 0.0, 1.0)
    return np.stack([np.clip(d * 2, 0, 1), np.clip(d * 2 - 1, 0, 1), np.zeros_like(d)], axis=-1)


def compare_images(blender_png: bytes, game_png: bytes) -> dict:
    """Scores and pictures for two images of the same view (the game image is resized to Blender's).

    Returns {"mean_abs_diff", "normalized_diff", "detail_ratio", "worst_cells", "verdict", "size",
    "side_by_side_png", "diff_png"}.
    """
    import numpy as np

    a = decode_image(blender_png)
    b = decode_image(game_png)
    height, width = a.shape[:2]
    if b.shape[:2] != (height, width):
        b = _resize(b, height, width)
    diff = np.abs(a - b).mean(axis=2)
    normalized = float(np.abs(_standardize(a) - _standardize(b)).mean() / 2.0)
    energy = _edge_energy(a)
    ratio = _edge_energy(b) / energy if energy > 1e-6 else 1.0
    cells = []
    for row in range(GRID):
        for col in range(GRID):
            y0, y1 = row * height // GRID, (row + 1) * height // GRID
            x0, x1 = col * width // GRID, (col + 1) * width // GRID
            cells.append({"cell": [col, row], "box": [x0, y0, x1, y1],
                          "diff": round(float(diff[y0:y1, x0:x1].mean()), 4)})
    cells.sort(key=lambda c: -c["diff"])
    verdict = "different"
    for name, limit, detail in VERDICTS:
        if normalized <= limit and detail <= ratio <= 1 / detail:
            verdict = name
            break
    heat = _heat(diff)
    gap = np.ones((height, GAP, 3), dtype=np.float32)
    sheet = np.concatenate([a, gap, b, gap, heat], axis=1)
    to_bytes = lambda img: (np.clip(img, 0, 1) * 255 + 0.5).astype(np.uint8)  # noqa: E731
    return {
        "mean_abs_diff": round(float(diff.mean()), 4),
        "normalized_diff": round(normalized, 4),
        "detail_ratio": round(float(ratio), 3),
        "worst_cells": cells[:3],
        "verdict": verdict,
        "size": [width, height],
        "side_by_side_png": encode_png(to_bytes(sheet)),
        "diff_png": encode_png(to_bytes(heat)),
    }


def compare_views(game_png: bytes, camera=None, scene=None, size=None, engine: str = "eevee", samples: int = 16,
                  out_dir=None) -> dict:
    """Render Blender's view from camera at the game screenshot's size and compare the two.

    With out_dir, also writes blender.png, game.png and side_by_side.png there. Returns the
    compare_images() result plus "blender_png", "camera", "camera_id", "engine", "render_seconds"
    (and "files" with out_dir).
    """
    if size is None:
        from .fidelity import image_size

        size = image_size(game_png) or DEFAULT_SIZE
    view = render_view(camera, scene, size, engine, samples)
    result = compare_images(view["png"], game_png)
    result.update(blender_png=view["png"], camera=view["camera"], camera_id=view["camera_id"],
                  engine=view["engine"], render_seconds=view["seconds"])
    if out_dir is not None:
        folder = pathlib.Path(out_dir)
        folder.mkdir(parents=True, exist_ok=True)
        files = {"blender": folder / "blender.png", "game": folder / "game.png",
                 "side_by_side": folder / "side_by_side.png"}
        files["blender"].write_bytes(view["png"])
        files["game"].write_bytes(game_png)
        files["side_by_side"].write_bytes(result["side_by_side_png"])
        result["files"] = {k: str(v) for k, v in files.items()}
    return result
