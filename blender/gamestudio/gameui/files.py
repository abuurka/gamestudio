# SPDX-License-Identifier: GPL-3.0-or-later
"""Files of the game UI: ui/ui.blend itself (new_ui_file), and the fonts and images its elements use.

Fonts and images chosen from outside the project are copied in (assets/fonts/, assets/ui/), so the
project stays self-contained (docs/GAME-UI.md §3.3); a file that is already there with the same
content is reused, a different one with the same name gets a -2, -3 ... suffix. A font's licence
comes along as <font name>-OFL.txt (Inter's for Blender's Inter, the default font).

new_ui_file() writes ui/ui.blend with its canvases. When another file is open it does so in a
background Blender (factory settings, a throw-away profile), so the open file, its undo history and
the user's settings are never touched; in a new, untitled session with no changes it builds the file
right here, and the UI file stays open.
"""

from __future__ import annotations

import filecmp
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile

import bpy

from ..core import gameui as core, project, registry
from ..core.log import get_logger

log = get_logger(__name__)

FONTS_DIR = "assets/fonts"
IMAGES_DIR = "assets/ui"
FONT_EXTENSIONS = (".ttf", ".otf", ".woff", ".woff2")
IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp")
SOUND_EXTENSIONS = (".ogg", ".mp3", ".wav")  # UiButton.sound and focusSound (asset fields)
BUILTIN_FONT = "<builtin>"
DEFAULT_FONT = "Inter.woff2"
DEFAULT_CANVAS = "Menu"
RESULT_TAG = "GS-RESULT "
WORKER_TIMEOUT = 180


def _error(message: str, hint: str | None = None, code: str | None = None):
    from .build import GameUIError

    return GameUIError(message, hint=hint, code=code)


def _root(root=None) -> pathlib.Path:
    root = pathlib.Path(root) if root else project.project_root()
    if root is None:
        raise _error("No GameStudio project: the open file is not saved inside one.",
                     hint="Open a level of the project first (or pass root).", code="no_project")
    return root


def project_relative(path) -> str:
    """A path relative to the project root, with forward slashes (absolute when outside it)."""
    if path is None or str(path) == "":
        return ""
    path = pathlib.Path(path)
    root = project.project_root()
    if root is not None:
        try:
            return path.resolve().relative_to(pathlib.Path(root).resolve()).as_posix()
        except ValueError:
            pass
    return path.as_posix()


def _resolve(path, what: str, extensions) -> pathlib.Path:
    if not isinstance(path, (str, os.PathLike)) or not str(path).strip():
        raise _error(f"{what} must be a file path, got {path!r}")
    text = str(path)
    if text.startswith("//"):
        text = bpy.path.abspath(text)
    file = pathlib.Path(text)
    if not file.is_absolute():
        root = project.project_root()
        file = (pathlib.Path(root) / file) if root is not None else file.resolve()
    if file.suffix.lower() not in extensions:
        raise _error(f"{what} {file.name!r} is not a {'/'.join(e.lstrip('.') for e in extensions)} file.")
    if not file.is_file():
        raise _error(f"{what} not found: {file}")
    return file


def _inside(path: pathlib.Path, root: pathlib.Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def import_file(path: pathlib.Path, folder: str) -> pathlib.Path:
    """The file itself when it is inside the project, else its copy in <project>/<folder>/."""
    root = project.project_root()
    if root is None or _inside(path, pathlib.Path(root)):
        return path
    target_dir = pathlib.Path(root) / folder
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / path.name
    n = 2
    while target.exists() and not filecmp.cmp(path, target, shallow=False):
        target = target_dir / f"{path.stem}-{n}{path.suffix}"
        n += 1
    if not target.exists():
        shutil.copy2(path, target)
        log.info("Copied %s into %s", path, target)
    return target


def _blend_path(path: pathlib.Path) -> str:
    """A path as Blender should store it: relative to the saved .blend when possible."""
    if bpy.data.filepath:
        try:
            return bpy.path.relpath(str(path))
        except ValueError:  # another drive on Windows
            pass
    return str(path)


def bundled_font() -> pathlib.Path | None:
    """Blender's own Inter.woff2 (datafiles/fonts): the default UI font. Blender's built-in text font is
    an older one the game cannot load, so texts get Inter and both draw the same glyphs."""
    datafiles = bpy.utils.system_resource("DATAFILES")
    path = pathlib.Path(datafiles) / "fonts" / DEFAULT_FONT if datafiles else None
    return path if path is not None and path.is_file() else None


def _import_font_licence(source: pathlib.Path, file: pathlib.Path) -> None:
    """A font copied into the project gets its licence next to it, as <font name>-OFL.txt (or -LICENSE.txt):
    the one next to the original or, for Blender's Inter, Inter's OFL from the add-on (resources/fonts/).
    The OFL wants its copyright notice and licence with every copy; exports and builds carry the file on."""
    if file == source:
        return  # the font was in the project already
    from ..core import gameui_export

    licence = gameui_export.font_licence(source)
    if licence is None:
        return
    target = file.with_name(gameui_export.font_licence_name(file.name, licence))
    if not target.exists():
        shutil.copyfile(licence, target)
        log.info("Copied %s into %s", licence, target)


def font_source(path) -> str:
    """Check a font argument without changing anything: "" (the default font, Inter), "<builtin>"
    (Blender's built-in font) or the file."""
    if path is None or path == "":
        return ""
    if path == BUILTIN_FONT:
        return BUILTIN_FONT
    return str(_resolve(path, "font", FONT_EXTENSIONS))


def load_font(source: str) -> bpy.types.VectorFont:
    """The VectorFont for a font_source(), copying the file into assets/fonts/ first when needed. The
    default font is Blender's Inter, copied into the project (the built-in font outside a project)."""
    if not source:
        inter = bundled_font()
        source = str(inter) if inter is not None and project.project_root() is not None else BUILTIN_FONT
    if source == BUILTIN_FONT:
        for font in bpy.data.fonts:
            if font.filepath == BUILTIN_FONT:
                return font
        return bpy.data.fonts.load(BUILTIN_FONT)
    file = import_file(pathlib.Path(source), FONTS_DIR)
    _import_font_licence(pathlib.Path(source), file)
    for font in bpy.data.fonts:
        if font.library is None and font.filepath != BUILTIN_FONT and \
                os.path.normcase(os.path.abspath(bpy.path.abspath(font.filepath))) == os.path.normcase(str(file)):
            return font
    try:
        return bpy.data.fonts.load(_blend_path(file), check_existing=True)
    except RuntimeError as exc:
        raise _error(f"Blender could not load the font {file}: {exc}") from None


def image_source(path) -> str:
    return str(_resolve(path, "image", IMAGE_EXTENSIONS))


def sound_source(path, what: str = "sound") -> str:
    """Check a button sound without changing anything: "" (none), or a .ogg, .mp3 or .wav file of the
    project (project-relative or absolute), returned project-relative as UiButton's asset fields hold it."""
    if path is None or path == "":
        return ""
    file = _resolve(path, what, SOUND_EXTENSIONS)
    root = project.project_root()
    if root is None or not _inside(file, pathlib.Path(root)):
        raise _error(f"{what} {file} is outside the project.",
                     hint="Copy it into the project (assets/sfx/, say) and pass that path.")
    return project_relative(file)


def blend_source(path, what: str = "source") -> pathlib.Path:
    """A .blend of the project to link from (a 3D model's source "scenes/main.blend:Player"), project-relative or
    absolute: the file. Not one outside the project (the link would break when the project moves) and not the open
    file itself."""
    file = _resolve(path, what, (".blend",))
    root = project.project_root()
    if root is None or not _inside(file, pathlib.Path(root)):
        raise _error(f"{what} {file} is outside the project.",
                     hint="Link from a .blend of the project, such as a level: 'scenes/main.blend:Player'.")
    if bpy.data.filepath and file.resolve() == pathlib.Path(bpy.data.filepath).resolve():
        raise _error(f"{what}: {project_relative(file)} is the open file.",
                     hint="Name the object alone to use an object of this file.")
    return file


def load_image(source: str) -> bpy.types.Image:
    """The Image for an image_source(), copying the file into assets/ui/ first when needed."""
    file = import_file(pathlib.Path(source), IMAGES_DIR)
    for img in bpy.data.images:
        if img.library is None and img.source == "FILE" and \
                os.path.normcase(os.path.abspath(bpy.path.abspath(img.filepath))) == os.path.normcase(str(file)):
            return img
    try:
        return bpy.data.images.load(_blend_path(file), check_existing=True)
    except RuntimeError as exc:
        raise _error(f"Blender could not load the image {file}: {exc}") from None


# --- ui/ui.blend -----------------------------------------------------------------------------------


def canvas_specs(canvases) -> list[dict]:
    """canvases as new_ui_file() takes them (names, or dicts of gs.gameui.canvas() arguments with a name)
    -> a list of argument dicts; one "Menu" when empty."""
    if isinstance(canvases, (str, dict)):
        canvases = [canvases]
    specs = []
    for item in canvases or ():
        spec = {"name": item} if isinstance(item, str) else dict(item) if isinstance(item, dict) else None
        if spec is None or not isinstance(spec.get("name"), str) or not spec["name"].strip():
            raise _error(f"Each canvas must be a name or a dict with a name, got {item!r}")
        if spec["name"].startswith("gs_"):
            raise _error(f"Canvas names cannot start with gs_: {spec['name']!r}")
        specs.append(spec)
    names = [s["name"] for s in specs]
    duplicates = sorted({n for n in names if names.count(n) > 1})
    if duplicates:
        raise _error(f"Canvas names must be unique: {', '.join(duplicates)}")
    return specs or [{"name": DEFAULT_CANVAS}]


def _dirty() -> bool:
    from ..bridge import api

    return api.is_dirty()


def new_ui_file(root=None, canvases=()) -> pathlib.Path:
    """Write <root>/ui/ui.blend with the given canvases (default: one empty menu, "Menu"). Refuses to
    overwrite. In a background Blender when another file is open; in an untitled session without changes,
    right here (the new file is then the open one)."""
    root = _root(root)
    path = core.ui_file(root)
    if path.exists():
        raise _error(f"{project_relative(path)} already exists.",
                     hint="Open it (GameStudio > Edit Game UI, or ui_open) instead.", code="ui_file_exists")
    specs = canvas_specs(canvases)
    from .build import need_component

    if registry.schema_ref("UiCanvas") is None:  # an untitled session has not loaded this project's registry
        registry.reload_registry(root)
    need_component("UiCanvas")
    if not bpy.data.filepath and not _dirty():
        create_here(path, root, specs)
    else:
        _run_worker({"path": str(path), "root": str(root), "canvases": specs, "addon": _addon_location()})
    log.info("Created %s", path)
    return path


def create_here(path, root, specs) -> None:
    """Build the UI file in this Blender: an empty file, saved as `path`, then its canvases."""
    from . import build

    path, root = pathlib.Path(path), pathlib.Path(root)
    bpy.ops.wm.read_homefile(use_empty=True)
    first = bpy.data.scenes[0]
    for scene in bpy.data.scenes[1:]:
        bpy.data.scenes.remove(scene)
    first.name = specs[0]["name"]
    core.setup_canvas_scene(first, specs[0].get("size") or core.DEFAULT_SIZE)  # a camera before the first save
    path.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=str(path))
    project.invalidate_cache()
    if build.registry.schema_ref("UiCanvas") is None:
        registry.reload_registry(root)
    try:
        for spec in specs:
            build.canvas(**spec)
    except Exception:
        bpy.ops.wm.read_homefile(use_empty=True)
        path.unlink(missing_ok=True)
        raise
    bpy.ops.wm.save_mainfile()


def _addon_location() -> dict:
    package = pathlib.Path(__file__).resolve().parents[1]
    return {"path": str(package.parent), "package": package.name}


# Runs in a background Blender: register the property groups and the component registry of the add-on
# (nothing else: no timers, no servers), then build the file with the same code.
_WORKER = r'''
import importlib, json, sys
sys.dont_write_bytecode = True  # no __pycache__ in the add-on's folder
args = json.loads(open(sys.argv[-1], encoding="utf-8").read())
sys.path.insert(0, args["addon"]["path"])
package = args["addon"]["package"]
importlib.import_module(package + ".core.props").register()
registry = importlib.import_module(package + ".core.registry")
registry.register()
registry.reload_registry(args["root"])
files = importlib.import_module(package + ".gameui.files")
try:
    files.create_here(args["path"], args["root"], args["canvases"])
    result = {"ok": True}
except ValueError as exc:
    result = {"error": str(exc), "code": getattr(exc, "code", None)}
print("GS-RESULT " + json.dumps(result), flush=True)
'''


def _run_worker(args: dict) -> dict:
    with tempfile.TemporaryDirectory(prefix="gs_gameui_") as tmp:
        argfile = pathlib.Path(tmp) / "args.json"
        argfile.write_text(json.dumps(args), encoding="utf-8")
        env = dict(os.environ, BLENDER_USER_RESOURCES=str(pathlib.Path(tmp) / "profile"))
        for key in ("BLENDER_SYSTEM_EXTENSIONS", "BLENDER_SYSTEM_SCRIPTS"):  # the add-on is imported, not enabled
            env.pop(key, None)
        cmd = [bpy.app.binary_path, "--background", "--factory-startup", "--python-exit-code", "1",
               "--python-expr", _WORKER, "--", str(argfile)]
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                                  env=env, timeout=WORKER_TIMEOUT, creationflags=flags)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise _error(f"Could not run Blender to write {args['path']}: {exc}") from None
    for line in proc.stdout.splitlines():
        if line.startswith(RESULT_TAG):
            result = json.loads(line[len(RESULT_TAG):])
            if result.get("error"):
                raise _error(result["error"], code=result.get("code"))
            return result
    tail = "\n".join((proc.stderr or proc.stdout).strip().splitlines()[-8:])
    raise _error(f"Writing {args['path']} failed (exit code {proc.returncode}):\n{tail}")
