# SPDX-License-Identifier: GPL-3.0-or-later
"""The Project editor (GAMESTUDIO_PROJECT): the project at a glance.

Name and folder (and Open in VS Code), the scenes (scenes/*.blend) with Open
buttons, the scripts (the components and systems in src/components and
src/systems, with New Component / New System and buttons that open each in the
code editor), the dev server, and Build Web (`npm run build` on a worker
thread, output in .gamestudio/build.log). Draw code only reads cached state; it
never blocks. The script list comes from core/scripts.py, which a timer keeps
up to date as files change.

Its draw functions are also the Game Project panels of Scene Properties
(ui_scene.py). On stock Blender the editor falls back to a 3D View sidebar tab,
which shows only when the sidebar preference is on (prefs.sidebar_enabled()).

register() also adds the code editor and script actions to the Project menu.
"""

import os
import pathlib
import subprocess
import sys
import threading
import time

import bpy

from .. import host, prefs, ui
from ..core import code_editor, project, scripts, toolchain
from ..core.log import get_logger
from ..core.util import any_window, tag_redraw
from ..prefs import get_prefs

log = get_logger(__name__)
panel = host.editors.panel

MAX_FILES = 200
_LISTING_TTL = 2.0
BUILD_LOG = "build.log"
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


# --- Project files (cached: draw code runs on every redraw) -----------------------------

_listing = {"root": None, "time": 0.0, "scenes": []}


def _files(folder: pathlib.Path, pattern: str) -> list[pathlib.Path]:
    try:
        return sorted((p for p in folder.glob(pattern) if p.is_file()), key=lambda p: p.name.lower())[:MAX_FILES]
    except OSError:
        return []


def listing(root: pathlib.Path) -> dict:
    """{"scenes": [Path]} for the project, cached briefly. Scripts: core/scripts.py."""
    now = time.monotonic()
    if _listing["root"] != root or now - _listing["time"] > _LISTING_TTL:
        _listing.update(root=root, time=now, scenes=_files(root / "scenes", "*.blend"))
    return _listing


def invalidate() -> None:
    _listing.update(root=None, time=0.0)
    scripts.invalidate()


# --- Build Web --------------------------------------------------------------------------


class _Build:
    """`npm run build` on a worker thread; the main thread only reads these fields."""

    def __init__(self):
        self.state = ""        # "", "building", "done", "failed"
        self.message = ""
        self.root = None
        self.started = 0.0
        self.finished = 0.0

    def start(self, root: pathlib.Path, npm: list[str]) -> None:
        self.state, self.root, self.started, self.finished = "building", root, time.time(), 0.0
        self.message = "Building (npm run build)..."
        threading.Thread(target=self._run, args=(root, npm), daemon=True, name="gamestudio-build-web").start()
        if not bpy.app.timers.is_registered(_build_tick):
            bpy.app.timers.register(_build_tick, first_interval=0.5, persistent=True)

    def _run(self, root: pathlib.Path, npm: list[str]) -> None:
        log_path = build_log_path(root)
        try:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with open(log_path, "w", encoding="utf-8", errors="replace") as out:
                out.write(f"# {' '.join(npm)} run build  (cwd {root})\n")
                out.flush()
                env = toolchain.child_env(dict(os.environ, FORCE_COLOR="0", NO_COLOR="1"))
                kwargs = {"creationflags": _NO_WINDOW} if sys.platform == "win32" else {}
                proc = subprocess.run([*npm, "run", "build"], cwd=str(root), env=env, stdin=subprocess.DEVNULL,
                                      stdout=out, stderr=subprocess.STDOUT, check=False, **kwargs)
            ok = proc.returncode == 0
            message = "Web build finished." if ok else \
                f"`npm run build` failed (exit code {proc.returncode}). See {log_path.name}."
        except OSError as exc:
            ok, message = False, f"Could not run `npm run build`: {exc}"
        self.message, self.finished = message, time.time()
        self.state = "done" if ok else "failed"


BUILD = _Build()


def build_log_path(root) -> pathlib.Path:
    return project.gs_dir(root) / BUILD_LOG


def _redraw() -> None:
    host.editors.tag_redraw()
    tag_redraw(("PROPERTIES",), region_type=None)  # Scene Properties > Game Project > Build


def _build_tick():
    if BUILD.state == "building":
        _redraw()
        return 0.5
    if BUILD.state == "failed":
        log.error(BUILD.message)
    _redraw()
    return None


def _build_when_trusted(ok: bool, message: str) -> None:
    """Build Web again once the person answered the trust question."""
    from .. import environment

    if not ok:
        environment.notify(message)
        return
    window = any_window()
    try:
        with bpy.context.temp_override(**({"window": window} if window is not None else {})):
            bpy.ops.gamestudio.build_web()
    except RuntimeError as exc:
        environment.notify(f"Build Web did not start: {exc}", "ERROR")


class GAMESTUDIO_OT_build_web(bpy.types.Operator):
    bl_idname = "gamestudio.build_web"
    bl_label = "Build Web"
    bl_description = "Build the game for the web (npm run build); the output goes to the project's dist folder"

    @classmethod
    def poll(cls, context):
        if BUILD.state == "building":
            cls.poll_message_set("A build is already running")
            return False
        return project.project_root() is not None

    def execute(self, _context):
        from .. import environment

        root = project.project_root()
        problem = environment.require_trust(root, then=_build_when_trusted)  # `npm run build` runs its code
        if problem == environment.ASKING:
            return {"FINISHED"}  # it builds once the project is trusted
        if problem:
            self.report({"ERROR"}, problem)
            return {"CANCELLED"}
        command = (get_prefs().npm_command or "npm").strip()
        npm = toolchain.npm_argv(command)  # the bundled npm first in an installed GameStudio
        if npm is None:
            self.report({"ERROR"}, "Node.js is not set up yet: GameStudio > Set Up GameStudio... sets it up"
                        if command == "npm" else f"Could not find '{command}', the npm command in GameStudio's "
                        "preferences")
            return {"CANCELLED"}
        if not (root / "package.json").is_file():
            self.report({"ERROR"}, f"No package.json in {root}")
            return {"CANCELLED"}
        if not (root / "node_modules").is_dir():
            self.report({"ERROR"}, "The project's packages are not set up yet: GameStudio > Set Up GameStudio... "
                                   "sets them up")
            return {"CANCELLED"}
        BUILD.start(root, npm)
        self.report({"INFO"}, "Building the web version...")
        return {"FINISHED"}


# --- Panels -------------------------------------------------------------------------------


def _shown(context) -> bool:
    """A real editor on GameStudio Editor; the stock sidebar tab only behind the sidebar preference."""
    return host.editors.available() or prefs.sidebar_enabled()


def _has_project(context) -> bool:
    return _shown(context) and project.project_root() is not None


def _open_path(layout, path, text="", icon="FILE_SCRIPT"):
    layout.operator("gamestudio.open_path", text=text, icon=icon).filepath = str(path)


def draw_new_or_open(layout) -> None:
    """Why there is no project, and New Project / Open Project."""
    ui.draw_no_project(layout)
    row = layout.row(align=True)
    row.operator_context = "INVOKE_DEFAULT"
    row.operator("gamestudio.project_new", text="New Project...", icon="FILE_NEW")
    row.operator("gamestudio.project_open", text="Open Project...", icon="FILE_FOLDER")


def draw_open_buttons(layout) -> None:
    """Open in <code editor>, and the project folder."""
    row = layout.row(align=True)
    row.operator("gamestudio.open_code_project", text=f"Open in {code_editor.label()}", icon="TEXT")
    row.operator("gamestudio.project_reveal", text="", icon="FOLDER_REDIRECT")


def draw_overview(layout, context) -> None:
    root = project.project_root()
    if root is None:
        draw_new_or_open(layout)
        return
    config = project.read_config(root)
    row = layout.row()
    row.label(text=config.get("name") or root.name, icon="FILE_FOLDER")
    row.operator("gamestudio.project_reveal", text="", icon="FOLDER_REDIRECT")
    layout.label(text=str(root))
    layout.operator("gamestudio.open_code_project", text=f"Open in {code_editor.label()}", icon="TEXT")


def draw_scenes(layout, context) -> None:
    root = project.project_root()
    scenes = listing(root)["scenes"]
    if not scenes:
        layout.label(text="No scenes in scenes/ yet", icon="INFO")
        return
    current = pathlib.Path(bpy.data.filepath).resolve() if bpy.data.filepath else None
    start = project.read_config(root).get("startScene")
    col = layout.column(align=True)
    col.operator_context = "INVOKE_DEFAULT"  # asks to save unsaved changes first
    for path in scenes:
        is_current = current is not None and path.resolve() == current
        row = col.row(align=True)
        label = path.stem + ("  (start scene)" if path.stem == start else "")
        row.label(text=label, icon="CHECKMARK" if is_current else "FILE_BLEND")
        sub = row.row(align=True)
        sub.enabled = not is_current
        op = sub.operator("wm.open_mainfile", text="Open", icon="FILE_FOLDER")
        op.filepath = str(path)
        op.display_file_selector = False


def _script_row(col, script, names, icon) -> None:
    row = col.row(align=True)
    if names:
        row.label(text=", ".join(names), icon=icon)
        row.label(text=script["file"])
    else:
        row.label(text=script["file"], icon=icon)
    _open_path(row, script["path"], icon="FILE_SCRIPT")


def draw_scripts(layout, context) -> None:
    root = project.project_root()
    row = layout.row(align=True)
    row.operator_context = "INVOKE_DEFAULT"
    row.operator("gamestudio.new_component", text="New Component...", icon="FILE_NEW")
    row.operator("gamestudio.new_system", text="New System...", icon="FILE_NEW")

    pending = False
    folder = root / scripts.FOLDERS["component"]
    row = layout.row()
    row.label(text=f"Components ({scripts.FOLDERS['component']})", icon="PROPERTIES")
    if folder.is_dir():
        _open_path(row, folder, icon="FOLDER_REDIRECT")
    col = layout.column(align=True)
    found = scripts.components(root)
    if not found:
        col.label(text="No components yet", icon="BLANK1")
    for script in found:
        names = [name for name, _entry in script["entries"]]
        known = all(entry is not None for _name, entry in script["entries"])
        pending = pending or bool(names and not known)
        icon = "CHECKMARK" if names and known else ("TIME" if names else "QUESTION")
        _script_row(col, script, names, icon)
    if pending:
        ui.wrap_label(layout, "Components marked with a clock are not in Add Component yet: the dev server "
                              "adds them when it runs.", icon="TIME")

    layout.separator()
    folder = root / scripts.FOLDERS["system"]
    row = layout.row()
    row.label(text=f"Systems ({scripts.FOLDERS['system']})", icon="SETTINGS")
    if folder.is_dir():
        _open_path(row, folder, icon="FOLDER_REDIRECT")
    col = layout.column(align=True)
    found = scripts.systems(root)
    if not found:
        col.label(text="No systems yet", icon="BLANK1")
    for script in found:
        _script_row(col, script, script["names"], "FILE_SCRIPT")


def draw_server(layout, context) -> None:
    ui.draw_server(layout, project.project_root())


def draw_build(layout, context) -> None:
    root = project.project_root()
    row = layout.row(align=True)
    row.scale_y = 1.3
    row.operator("gamestudio.build_web", icon="WORLD")
    if BUILD.state and BUILD.root == root:
        if BUILD.state == "building":
            layout.label(text=f"Building... {int(time.time() - BUILD.started)}s", icon="TIME")
        else:
            icon = "CHECKMARK" if BUILD.state == "done" else "ERROR"
            ui.wrap_label(layout, f"{BUILD.message} ({ui.ago(BUILD.finished)})", icon=icon)
        log_path = build_log_path(root)
        if log_path.is_file():
            _open_path(layout, log_path, text="Show Build Log", icon="TEXT")
    dist = root / "dist"
    if dist.is_dir():
        _open_path(layout, dist, text="Open Output Folder", icon="FOLDER_REDIRECT")


def draw_header(layout, context) -> None:
    layout.separator_spacer()
    layout.operator("gamestudio.open_code_project", text="", icon="TEXT")
    layout.operator("gamestudio.project_reveal", text="", icon="FOLDER_REDIRECT")


def draw_project_menu(self, context) -> None:
    """Appended to the Project menu (GAMESTUDIO_MT_projects)."""
    layout = self.layout
    layout.separator()
    layout.operator_context = "INVOKE_DEFAULT"
    layout.operator("gamestudio.open_code_project", text=f"Open Project in {code_editor.label()}", icon="TEXT")
    layout.operator("gamestudio.new_component", text="New Component...", icon="FILE_NEW")
    layout.operator("gamestudio.new_system", text="New System...", icon="FILE_NEW")


_MENUS = (("GAMESTUDIO_MT_projects", draw_project_menu),)


class ProjectEditor:
    idname = "GAMESTUDIO_PROJECT"
    label = "Project"
    icon = "FILE_FOLDER"
    description = "The game project: scenes, scripts, dev server and web build"
    panels = [
        panel("overview", "Project", draw_overview, poll=_shown),
        panel("scenes", "Scenes", draw_scenes, poll=_has_project),
        panel("scripts", "Scripts", draw_scripts, poll=_has_project),
        panel("server", "Dev Server", draw_server, poll=_has_project),
        panel("build", "Build", draw_build, poll=_has_project),
    ]
    header_draw = staticmethod(draw_header)


def register():
    bpy.utils.register_class(GAMESTUDIO_OT_build_web)
    host.editors.register(ProjectEditor)
    scripts.register()
    for menu, draw in _MENUS:
        if hasattr(bpy.types, menu):
            getattr(bpy.types, menu).append(draw)


def unregister():
    for menu, draw in _MENUS:
        if hasattr(bpy.types, menu):
            getattr(bpy.types, menu).remove(draw)
    scripts.unregister()
    host.editors.unregister(ProjectEditor)
    if bpy.app.timers.is_registered(_build_tick):
        bpy.app.timers.unregister(_build_tick)
    bpy.utils.unregister_class(GAMESTUDIO_OT_build_web)
