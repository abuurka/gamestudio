# SPDX-License-Identifier: GPL-3.0-or-later
"""Baked lighting: Bake Lighting, Clear Baked Lighting and the Lighting panel (core/lightmap.py).

Bake Lighting saves a copy of the open file to .gamestudio/lightmaps/_bake_source.blend and bakes it in
a background Blender (lightmap.background_script) as the GameStudio job (environment.JOB), so the editor
stays usable; its output goes to .gamestudio/lightmap.log. When it is done, the scene is exported again
so a running game shows the new lighting. In --background (tests, scripts) the bake runs in this
Blender instead (lightmap.bake_lighting).

The Lighting panel is Scene Properties > Game Project > Lighting (ui_scene.py); the Game menu has
Bake Lighting (menus.py).
"""

import contextlib
import datetime
import json
import pathlib
import time

import bpy
from bpy.props import BoolProperty, EnumProperty

from . import environment, ui
from .core import export, fidelity, lightmap, project
from .core.log import get_logger
from .core.util import any_window
from .ops import NO_PROJECT

log = get_logger(__name__)

BAKE_TITLE = "Baking lighting"
LOG_NAME = "lightmap.log"
SOURCE_NAME = "_bake_source.blend"
STATUS_TTL = 2.0
QUALITY_ITEMS = (
    ("preview", "Preview", "Quick bake to check the lighting: few samples, small lightmaps"),
    ("final", "Final", "Slow, clean bake for the finished game: more samples, larger lightmaps"),
)


def baking() -> bool:
    """A Bake Lighting job is running."""
    return environment.JOB.running() and environment.JOB.title == BAKE_TITLE


# --- The stored bake (cached: draw code runs on every redraw) ---------------------------------

_status_cache = {"key": None, "time": 0.0, "status": None}


def invalidate_status() -> None:
    _status_cache.update(key=None, time=0.0, status=None)


def cached_status(scene, root) -> dict | None:
    """lightmap.status() of the scene, re-read every few seconds."""
    name = export.export_name(scene)
    key = (str(root), name)
    now = time.monotonic()
    if _status_cache["key"] != key or now - _status_cache["time"] > STATUS_TTL:
        try:
            status = lightmap.status(scene, root=root, name=name)
        except (OSError, ValueError, lightmap.LightmapError) as exc:
            log.warning("Could not read the baked lighting of '%s': %s", name, exc)
            status = None
        _status_cache.update(key=key, time=now, status=status)
    return _status_cache["status"]


def _count(value) -> int:
    if isinstance(value, (list, tuple, dict)):
        return len(value)
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _atlases(count: int) -> str:
    return f"{count} atlas{'es' if count != 1 else ''}"


def _size(count) -> str:
    try:
        return fidelity.format_bytes(float(count or 0))
    except (TypeError, ValueError):
        return "?"


def _timestamp(value) -> float | None:
    """lightmap.json's "created" (ISO 8601) as a POSIX time, or None."""
    try:
        return datetime.datetime.fromisoformat(str(value)).timestamp() if value else None
    except (TypeError, ValueError, OverflowError):
        return None


def status_lines(status: dict | None) -> list[str]:
    """What the panel says about the stored bake: "Not baked", or its size, quality and when it was baked."""
    if not status:
        return ["Not baked"]
    lines = [f"{ui.plural(_count(status.get('objects')), 'object')}, {_atlases(_count(status.get('atlases')))}, "
             f"{_size(status.get('texture_bytes'))}"]
    quality = str(status.get("quality") or "baked").title()
    when = _timestamp(status.get("created"))
    lines.append(f"{quality}, baked {ui.ago(when)}" if when is not None else quality)
    return lines


def summary_text(result: dict) -> str:
    """One line about a finished bake: what was baked, how big and how long it took."""
    if result.get("up_to_date"):
        return "The baked lighting is up to date"
    text = (f"Baked {ui.plural(_count(result.get('objects')), 'object')} into "
            f"{_atlases(_count(result.get('atlases')))} ({_size(result.get('texture_bytes'))}) in "
            f"{float(result.get('seconds') or 0):.0f} s")
    warnings = result.get("warnings") or []
    if warnings:
        text += f". {ui.plural(len(warnings), 'warning')}: {warnings[0]}"
    return text


# --- Baking ---------------------------------------------------------------------------------


def read_result(log_path, prefix: str) -> dict | None:
    """The result a background bake printed last (a line starting with prefix and its json), or None."""
    try:
        text = pathlib.Path(log_path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    for line in reversed(text.splitlines()):
        if line.startswith(prefix):
            try:
                result = json.loads(line[len(prefix):])
            except ValueError:
                return None
            return result if isinstance(result, dict) else None
    return None


def _error_text(result: dict) -> str:
    error = result.get("error")
    if isinstance(error, dict):
        error = error.get("message") or json.dumps(error)
    error = str(error)
    return error.removeprefix("LightmapError: ")  # the background run prints "<type>: <message>"


def export_current_scene() -> str:
    """Export the current scene the way Export Scene does (a running game reloads it). Returns "" or
    why the export failed; failures are logged, never raised."""
    window = None if getattr(bpy.context, "window", None) is not None else any_window()
    try:
        with bpy.context.temp_override(window=window) if window is not None else contextlib.nullcontext():
            bpy.ops.gamestudio.export_scene()
    except Exception as exc:  # the bake is done either way: say why the game does not show it yet
        log.warning("Exporting the scene after baking its lighting failed: %s", exc)
        return str(exc).strip() or type(exc).__name__
    return ""


def _current_scene_is(root, name) -> bool:
    window = any_window()
    scene = window.scene if window is not None else bpy.context.scene
    return project.project_root() == root and scene is not None and export.export_name(scene) == name


def start_bake(scene, quality: str = "preview", export_after: bool = True) -> str:
    """Bake the scene's lighting in a background Blender as the GameStudio job. Returns "" when it
    started, else why not."""
    if environment.JOB.running():
        return f"Wait for '{environment.JOB.title}' to finish"
    root = project.project_root()
    if root is None:
        return NO_PROJECT
    name = export.export_name(scene)
    source = lightmap.lightmap_dir(root, name).parent / SOURCE_NAME
    log_path = project.gs_dir(root) / LOG_NAME
    try:
        source.parent.mkdir(parents=True, exist_ok=True)
        # A copy: the open file keeps its path and its unsaved state.
        bpy.ops.wm.save_as_mainfile(filepath=str(source), copy=True)
    except (OSError, RuntimeError) as exc:
        return f"Could not save a copy of the scene to bake: {exc}"
    argv = [bpy.app.binary_path, "--background", "--factory-startup", str(source), "--python-exit-code", "1",
            "--python-expr", lightmap.background_script(name, quality)]
    prefix = lightmap.RESULT_LINE
    found = {}

    def step(job):  # worker thread: files and processes only
        try:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_path.write_text(f"# Bake Lighting ({quality}) of '{name}'\n", encoding="utf-8")
            try:
                job.run(argv, root, log_path, f"{BAKE_TITLE} ({quality})")
            except environment.SetupError as exc:
                result = read_result(log_path, prefix)  # the bake's own error says more than an exit code
                if str(exc) != "Cancelled" and result and result.get("error"):
                    raise environment.SetupError(f"Bake Lighting failed: {_error_text(result)}") from exc
                raise
            result = read_result(log_path, prefix)
            if result is None:
                raise environment.SetupError(f"The bake ended without a result. See {log_path}")
            if result.get("error"):
                raise environment.SetupError(f"Bake Lighting failed: {_error_text(result)}")
            found["result"] = result
        finally:
            for path in (source, source.with_name(source.name + "1")):  # and a .blend1 backup
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    log.warning("Could not delete %s", path)

    def done(ok, _message):  # main thread
        invalidate_status()
        result = found.get("result")
        if not ok or result is None:
            return
        text = summary_text(result)
        if export_after and _current_scene_is(root, name):
            problem = export_current_scene()
            if problem:
                text += f". The export failed: {problem}"
        environment.JOB.message = text
        log.info(text)

    environment.JOB.start(BAKE_TITLE, [step], on_done=done, root=root)
    return ""


# --- Operators ------------------------------------------------------------------------------


class GAMESTUDIO_OT_bake_lighting(bpy.types.Operator):
    bl_idname = "gamestudio.bake_lighting"
    bl_label = "Bake Lighting"
    bl_description = ("Bake the sky light, the bounce light and the baked lamps onto the scene's static meshes "
                      "(lightmaps, with Cycles, in the background), then export the scene so the game shows it")

    quality: EnumProperty(name="Quality", items=QUALITY_ITEMS, default="preview", options={"SKIP_SAVE"})
    export: BoolProperty(name="Export", default=True, options={"SKIP_SAVE"},
                         description="Export the scene afterwards so the game shows the new lighting")
    draft: BoolProperty(name="Draft", default=False, options={"HIDDEN", "SKIP_SAVE"},
                        description="A tiny, fast bake (tests)")

    @classmethod
    def poll(cls, context):
        if project.project_root() is None:
            cls.poll_message_set(NO_PROJECT)
            return False
        if environment.JOB.running():
            cls.poll_message_set(f"Wait for '{environment.JOB.title}' to finish")
            return False
        return True

    def execute(self, context):
        quality = "draft" if self.draft else self.quality
        if bpy.app.background:
            return self._bake_here(context, quality)
        problem = start_bake(context.scene, quality, export_after=self.export)
        if problem:
            self.report({"ERROR"}, problem)
            return {"CANCELLED"}
        self.report({"INFO"}, f"Baking lighting ({quality}) in the background...")
        return {"FINISHED"}

    def _bake_here(self, context, quality):
        """In --background: bake in this Blender and wait for it."""
        try:
            result = lightmap.bake_lighting(context.scene, quality=quality, root=project.project_root())
        except lightmap.LightmapError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        finally:
            invalidate_status()
        text = summary_text(result)
        problem = export_current_scene() if self.export else ""
        if problem:
            self.report({"WARNING"}, f"{text}. The export failed: {problem}")
        else:
            self.report({"INFO"}, text)
        return {"FINISHED"}


class GAMESTUDIO_OT_clear_lighting(bpy.types.Operator):
    bl_idname = "gamestudio.clear_lighting"
    bl_label = "Clear Baked Lighting"
    bl_description = "Delete the scene's lightmaps, then export the scene so the game lights it in realtime only"

    export: BoolProperty(name="Export", default=True, options={"SKIP_SAVE"},
                         description="Export the scene afterwards so the game drops the lightmaps")

    @classmethod
    def poll(cls, context):
        if project.project_root() is None:
            cls.poll_message_set(NO_PROJECT)
            return False
        if baking():
            cls.poll_message_set("Wait for the bake to finish")
            return False
        return True

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event, message="Delete the scene's baked lighting?",
                                                     confirm_text="Clear")

    def execute(self, context):
        try:
            removed = lightmap.clear(context.scene, root=project.project_root())
        except (OSError, lightmap.LightmapError) as exc:
            self.report({"ERROR"}, f"Could not clear the baked lighting: {exc}")
            return {"CANCELLED"}
        finally:
            invalidate_status()
        if not removed:
            self.report({"INFO"}, "The scene has no baked lighting")
            return {"FINISHED"}
        problem = export_current_scene() if self.export else ""
        if problem:
            self.report({"WARNING"}, f"Cleared the baked lighting. The export failed: {problem}")
        else:
            self.report({"INFO"}, "Cleared the baked lighting")
        return {"FINISHED"}


# --- UI -------------------------------------------------------------------------------------


def draw_lighting(layout, context) -> None:
    """Bake Preview / Bake Final, Clear, the stored bake, and the bake job while it runs."""
    root = project.project_root()
    if root is None:
        return
    row = layout.row(align=True)
    row.operator("gamestudio.bake_lighting", text="Bake Preview", icon="LIGHT_SUN").quality = "preview"
    row.operator("gamestudio.bake_lighting", text="Bake Final", icon="RENDER_STILL").quality = "final"
    row.operator("gamestudio.clear_lighting", text="", icon="TRASH")
    status = cached_status(context.scene, root)
    col = layout.column(align=True)
    for i, line in enumerate(status_lines(status)):
        col.label(text=line, icon=("CHECKMARK" if status else "INFO") if i == 0 else "BLANK1")
    if environment.JOB.title == BAKE_TITLE and environment.JOB.root == root:
        environment.draw_job(layout)


_CLASSES = (GAMESTUDIO_OT_bake_lighting, GAMESTUDIO_OT_clear_lighting)


def register():
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    invalidate_status()
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
