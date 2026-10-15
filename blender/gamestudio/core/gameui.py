# SPDX-License-Identifier: GPL-3.0-or-later
"""Game UI files and canvas geometry (docs/GAME-UI.md §1, §2.1), shared by the export, the live
link, the authoring tools (gs.gameui) and the bridge.

A UI file is a .blend directly in <project>/ui/. Each of its user scenes is a canvas (a screen)
named after the scene. The canvas camera (scene.camera, orthographic, looking down -Z) frames the
screen: the design size is the render resolution, and k = max(resolution) / ortho_scale design
pixels per Blender unit. Canvas pixels have their origin at the frame's top-left corner, y down.
Phase 3 (§11): an empty with UiModel is a 3D model whose objects go into <Canvas>.ui.glb (is_model,
model_file), and ui/locales/<code>.json are the translation tables (locales_dir, source_locale).
"""

from __future__ import annotations

import os
import pathlib
import re
import time

import bpy

from . import project

UI_DIR = "ui"
DEFAULT_FILE = "ui.blend"
DEFAULT_SIZE = (1920, 1080)
DEFAULT_PX_PER_UNIT = 100.0
CAMERA_HEIGHT = 10.0
VIEW_TRANSFORM = "Standard"  # UI is drawn without tone mapping; Standard shows it the same way
EXPORT_SUBDIR = "ui"  # .gamestudio/export/ui
INDEX_FILE = "index.json"
CANVAS_SUFFIX = ".ui.json"
INDEX_FORMAT = "gamestudio.ui-index"
CANVAS_FORMAT = "gamestudio.ui"
FORMAT_VERSION = 1
SIZE_PROP = "gs_ui_size"  # [w, h] design px of a panel at scale 1, set by the gs.gameui builder (9-slice size)
ANIMATION_MARKERS = ("open", "close", "loop")  # timeline markers that name a canvas's animations (§10.5)
MODEL_COMPONENT = "UiModel"  # an empty whose children are a 3D model in the canvas (§11.1)
MODEL_SUFFIX = ".ui.glb"     # <Canvas>.ui.glb beside <Canvas>.ui.json: the canvas's models
LOCALES_DIR = "locales"      # ui/locales/<code>.json, exported to .gamestudio/export/ui/locales/ (§11.3)
DEFAULT_LOCALE = "en"        # gamestudio.json "locale": the language the texts are authored in


def ui_dir(root) -> pathlib.Path:
    return pathlib.Path(root) / UI_DIR


def ui_file(root=None) -> pathlib.Path | None:
    """<root>/ui/ui.blend (whether or not it exists); None outside a project."""
    root = root or project.project_root()
    return ui_dir(root) / DEFAULT_FILE if root else None


def ui_files(root) -> list[pathlib.Path]:
    """Every UI file of the project, sorted (the first one wins a duplicate canvas name)."""
    folder = ui_dir(root)
    if not folder.is_dir():
        return []
    return sorted(p for p in folder.glob("*.blend") if p.is_file())


def is_ui_file(path=None, root=None) -> bool:
    """True for a .blend directly in <project>/ui/ (default: the open file)."""
    path = path if path is not None else bpy.data.filepath
    if not path:
        return False
    blend = pathlib.Path(path)
    if blend.suffix.lower() != ".blend":
        return False
    root = root or project.find_project_root(str(blend))
    if root is None:
        return False
    # find_project_root resolves links, subst and mapped drives (a mapped drive becomes its UNC path):
    # compare resolved paths, or a project opened through one would never have UI files.
    return _real(blend.parent) == _real(ui_dir(root))


def _real(path) -> str:
    try:
        path = pathlib.Path(path).resolve()
    except OSError:
        pass
    return os.path.normcase(os.path.abspath(path))


_open_cache = {"blend": None, "value": False, "time": 0.0}
_OPEN_TTL = 2.0


def is_open_ui_file() -> bool:
    """is_ui_file() for the open file, cached for a moment (timers and draw code ask often)."""
    blend = bpy.data.filepath
    now = time.monotonic()
    if _open_cache["blend"] == blend and now - _open_cache["time"] < _OPEN_TTL:
        return _open_cache["value"]
    value = is_ui_file(blend) if blend else False
    _open_cache.update(blend=blend, value=value, time=now)
    return value


def source_path(root, path=None) -> str:
    """A UI file as the index names it: project-relative with "/" (default: the open file)."""
    blend = pathlib.Path(path if path is not None else bpy.data.filepath)
    try:
        return blend.resolve().relative_to(pathlib.Path(root).resolve()).as_posix()
    except (OSError, ValueError):
        return blend.as_posix()


def canvas_scenes() -> list:
    """The open UI file's canvases: its scenes, without GameStudio's temporary gs_ ones."""
    return [s for s in bpy.data.scenes if not s.name.startswith("gs_")]


def design_size(scene) -> tuple[int, int]:
    return int(scene.render.resolution_x), int(scene.render.resolution_y)


def frame_span(scene, size=None) -> int:
    """The design px that the camera's ortho_scale spans: the frame's larger side (sensor fit Auto), its
    width (Horizontal) or its height (Vertical)."""
    w, h = size or design_size(scene)
    fit = getattr(getattr(scene.camera, "data", None), "sensor_fit", "AUTO")
    return int(w if fit == "HORIZONTAL" else h if fit == "VERTICAL" else max(w, h))


def px_per_unit(scene) -> float:
    """Design pixels per Blender unit: ortho_scale spans frame_span() px."""
    cam = scene.camera
    if cam is None or cam.type != "CAMERA" or cam.data.type != "ORTHO" or cam.data.ortho_scale <= 0:
        return DEFAULT_PX_PER_UNIT
    return frame_span(scene) / cam.data.ortho_scale


def _camera_matrix(cam):
    """The camera's world matrix, current even before the depsgraph evaluates (right after
    setup_canvas_scene or a scripted move): its own matrix when it has no parent."""
    return cam.matrix_basis if cam.parent is None else cam.matrix_world


def camera_center(scene) -> tuple[float, float]:
    cam = scene.camera
    if cam is None:
        return 0.0, 0.0
    loc = _camera_matrix(cam).translation
    return float(loc.x), float(loc.y)


def to_px(scene, xy) -> tuple[float, float]:
    """World (x, y) in Blender units -> canvas px (origin top-left, y down)."""
    k = px_per_unit(scene)
    w, h = design_size(scene)
    cx, cy = camera_center(scene)
    return (xy[0] - cx) * k + w / 2, (cy - xy[1]) * k + h / 2


def from_px(scene, px) -> tuple[float, float]:
    """Canvas px (origin top-left, y down) -> world (x, y) in Blender units."""
    k = px_per_unit(scene)
    w, h = design_size(scene)
    cx, cy = camera_center(scene)
    return cx + (px[0] - w / 2) / k, cy - (px[1] - h / 2) / k


def camera_problems(scene) -> list[str]:
    """Why this scene's camera does not frame a canvas the standard way (export warns, uses defaults)."""
    cam = scene.camera
    if cam is None or cam.type != "CAMERA":
        return [f"UI screen '{scene.name}' has no camera; using a {DEFAULT_SIZE[0]}x{DEFAULT_SIZE[1]} frame at the origin"]
    problems = []
    if cam.data.type != "ORTHO":
        problems.append(f"UI screen '{scene.name}': the camera must be orthographic")
    if _rotated(cam):
        problems.append(f"UI screen '{scene.name}': the camera must look straight down (rotation 0, 0, 0)")
    if abs(cam.data.shift_x) > 1e-6 or abs(cam.data.shift_y) > 1e-6:
        problems.append(f"UI screen '{scene.name}': camera shift must be 0")
    return problems


def _rotated(cam) -> bool:
    rot = _camera_matrix(cam).to_euler()
    return max(abs(rot.x), abs(rot.y), abs(rot.z)) > 1e-4


def setup_canvas_scene(scene, size=DEFAULT_SIZE):
    """Give a scene the standard canvas setup: design size, an orthographic camera looking straight down,
    and Standard colour management. Returns the camera object. A camera that already frames a canvas
    (orthographic, not rotated) keeps its place and its px per unit, so a new design size changes only
    its ortho_scale and the elements stay where they are on the frame; a new camera, or one that cannot
    frame a canvas, looks down at the origin from (0, 0, 10) with 100 px per unit."""
    cam = scene.camera
    keep = cam is not None and cam.type == "CAMERA" and cam.data.type == "ORTHO" and not _rotated(cam)
    k = px_per_unit(scene) if keep else DEFAULT_PX_PER_UNIT
    scene.render.resolution_x, scene.render.resolution_y = int(size[0]), int(size[1])
    scene.render.resolution_percentage = 100
    scene.view_settings.view_transform = VIEW_TRANSFORM
    scene.view_settings.look = "None"
    if cam is None or cam.type != "CAMERA":
        data = bpy.data.cameras.new(f"{scene.name} Camera")
        cam = bpy.data.objects.new(f"{scene.name} Camera", data)
        scene.collection.objects.link(cam)
        scene.camera = cam
    cam.data.type = "ORTHO"
    cam.data.ortho_scale = frame_span(scene) / k
    cam.data.clip_start, cam.data.clip_end = 0.1, 100.0
    cam.data.shift_x = cam.data.shift_y = 0.0
    if not keep:
        cam.location = (0.0, 0.0, CAMERA_HEIGHT)
        cam.rotation_euler = (0.0, 0.0, 0.0)
    return cam


def export_dir(root) -> pathlib.Path:
    """<root>/.gamestudio/export/ui"""
    return project.export_dir(root) / EXPORT_SUBDIR


def model_file(canvas_file: str) -> str:
    """The models GLB of a canvas file: Title.ui.json -> Title.ui.glb (§11.1)."""
    stem = canvas_file[:-len(CANVAS_SUFFIX)] if canvas_file.endswith(CANVAS_SUFFIX) else canvas_file
    return stem + MODEL_SUFFIX


def is_model(obj) -> bool:
    """An empty with an enabled UiModel the registry defines: its children are a 3D model (§11.1)."""
    from . import components, registry

    return obj.type == "EMPTY" and components.is_enabled(obj, MODEL_COMPONENT) and \
        registry.schema_ref(MODEL_COMPONENT) is not None


# --- Translations (docs/GAME-UI.md §11.3) --------------------------------------------------------------


def locales_dir(root) -> pathlib.Path:
    """<root>/ui/locales: the translation tables, <code>.json each."""
    return ui_dir(root) / LOCALES_DIR


def source_locale(root) -> str:
    """The language the UI texts are authored in: gamestudio.json "locale" in its usual form ("pt-br" ->
    "pt-BR", "zh_hans" -> "zh-Hans"; as written when it is not a language code), else "en"."""
    value = project.read_config(root).get("locale") if root is not None else None
    if not isinstance(value, str) or not value.strip():
        return DEFAULT_LOCALE
    match = re.match(r"^([A-Za-z]{2,3})(?:[-_]([A-Za-z]{4}))?(?:[-_]([A-Za-z]{2}|[0-9]{3}))?$", value.strip())
    if match is None:
        return value.strip()
    language, script, region = match.groups()
    return "-".join(p for p in (language.lower(), script.title() if script else None,
                                region.upper() if region else None) if p)


# --- Animations (docs/GAME-UI.md §10.5) ----------------------------------------------------------------


def scene_fps(scene) -> float:
    """The frame rate a canvas's animations are sampled at."""
    return scene.render.fps / (scene.render.fps_base or 1.0)


def _markers(scene) -> list[tuple[int, str]]:
    """(frame, name lowercased and stripped) of the timeline markers, by frame."""
    return sorted((int(m.frame), m.name.strip().lower()) for m in scene.timeline_markers)


def _range_end(markers, start: int, scene) -> int:
    """Where a range from `start` ends: the next marker of any name, else the scene's end."""
    return next((f for f, _n in markers if f > start), int(scene.frame_end))


def animation_ranges(scene, problems: list | None = None) -> dict[str, tuple[int, int]]:
    """The animations a canvas's timeline markers name: {"open" | "close" | "loop": (first frame, last frame)},
    in that order. Each runs from its marker to the next marker of any name, or to the scene's end. Names are
    matched without case; with two markers of one name the first counts. A marker with no frames after it is
    left out. problems (a list) collects why markers were ignored."""
    markers = _markers(scene)
    ranges = {}
    for name in ANIMATION_MARKERS:
        found = [f for f, n in markers if n == name]
        if not found:
            continue
        start = found[0]
        if len(found) > 1 and problems is not None:
            problems.append(f"UI screen '{scene.name}' has {len(found)} '{name}' markers; the one at frame {start} "
                            "is used")
        end = _range_end(markers, start, scene)
        if end <= start:
            if problems is not None:
                problems.append(f"UI screen '{scene.name}': the '{name}' marker (frame {start}) has no frames after "
                                f"it (the scene ends at frame {scene.frame_end}); its animation is not exported")
            continue
        ranges[name] = (start, end)
    return ranges


def rest_frame(scene) -> int:
    """The frame of a canvas's rest pose, the layout it has while simply open: where its open animation ends
    (the next marker, or the scene's end); without an open animation, where its loop, else its close,
    starts (both start from the rest pose, as gs.gameui keys them), so a scrubbed timeline never exports a
    mid-animation pose; with no markers, the current frame."""
    markers = _markers(scene)
    start = next((f for f, n in markers if n == "open"), None)
    if start is not None:
        return max(start, _range_end(markers, start, scene))
    for name in ("loop", "close"):
        start = next((f for f, n in markers if n == name), None)
        if start is not None:
            return int(start)
    return int(scene.frame_current)
