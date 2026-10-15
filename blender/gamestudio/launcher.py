# SPDX-License-Identifier: GPL-3.0-or-later
"""The GameStudio launcher: a popup with new and open project, the examples and recent projects,
plus what a first run needs (Node.js, the coding agent, the GameStudio workspaces, the play shortcuts).

It opens from GameStudio > Launcher, and at every start when "Show launcher at startup" is on
(that preference turns Blender's splash off while it is on, so the two never stack). The first
time the extension is enabled from Preferences it opens once as the welcome: the loop in three
steps (open a project, open it in the coding agent, ask, watch and play) with New Project, Open Demo
and Open in Claude Code. The agent is the "Coding agent" preference's: Claude Code (the default) or
Codex. Blender's own splash, menus and Quick Setup are left as they are.
"""

import os
import time

import bpy
from bpy.app.handlers import persistent
from bpy.props import BoolProperty

from . import claude_code, environment, keymap, projects, workspaces
from .core import project
from .core.log import get_logger
from .prefs import get_prefs

log = get_logger(__name__)

WIDTH = 620
RECENT_ROWS = 5
STARTUP_TRIES = 40
DEMO = "demo-game"


def welcome_steps(agent=None) -> tuple:
    """The welcome's three steps, for the agent (default: the "Coding agent" preference's): (title,
    explanation, icon)."""
    label = (agent or claude_code.preferred_agent()).label
    return (
        ("Open a project", "Start a new one, or open the demo game", "FILE_FOLDER"),
        (f"Open it in {label}", f"A terminal opens in the project; {label} works in this Blender", "CONSOLE"),
        ("Ask, watch, play", "Describe your game, watch Blender update, press F5 to play", "PLAY"),
    )


WELCOME_STEPS = welcome_steps(claude_code.CLAUDE)  # with Claude Code, the default


def relative_time(timestamp, now=None) -> str:
    """'just now', '5 min ago', '3 h ago', 'Yesterday', 'Monday', '12 Oct' or '12 Oct 2025'."""
    if not timestamp:
        return ""
    now = time.time() if now is None else now
    seconds = max(0, int(now - timestamp))
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        return f"{seconds // 60} min ago"
    then, today = time.localtime(timestamp), time.localtime(now)
    days = (time.mktime(today[:3] + (0, 0, 0) + today[6:]) - time.mktime(then[:3] + (0, 0, 0) + then[6:])) / 86400
    if days < 1:
        return f"{seconds // 3600} h ago"
    if days < 2:
        return "Yesterday"
    if days < 7:
        return time.strftime("%A", then)
    if then.tm_year == today.tm_year:
        return f"{then.tm_mday} {time.strftime('%b', then)}"
    return f"{then.tm_mday} {time.strftime('%b %Y', then)}"


def short_path(path, limit=36) -> str:
    """A path for one launcher line: the home folder as ~ and, when too long, its last folders."""
    text = str(path)
    home = os.path.expanduser("~")
    if home and os.path.normcase(text).startswith(os.path.normcase(home)):
        text = "~" + text[len(home):]
    if len(text) <= limit:
        return text
    sep = "\\" if "\\" in text else "/"
    parts = [part for part in text.replace("\\", "/").split("/") if part]
    tail = sep.join(parts[-2:])
    if len(tail) + 4 > limit:
        tail = parts[-1]
    text = f"...{sep}{tail}"
    return text if len(text) <= limit else text[:limit - 3] + "..."


def display_name(name: str) -> str:
    """'demo-game' -> 'Demo Game': project folder names read better as titles."""
    if any(c.isupper() or c == " " for c in name):
        return name
    return " ".join(part.capitalize() for part in name.replace("_", "-").split("-") if part) or name


def _muted(layout):
    row = layout.row()
    row.active = False
    return row


def draw_start(col) -> None:
    col.label(text="Start")
    col.operator("gamestudio.project_new", text="New Project...", icon="FILE_NEW")
    projects.draw_waiting_projects(col)  # a new project that waits for the open file's unsaved changes
    col.operator("gamestudio.project_open", text="Open Project...", icon="FILE_FOLDER")
    if project.project_root() is not None:
        col.operator(claude_code.preferred_agent().open_op, icon="CONSOLE")
    found = projects.examples()[:3]
    if found:
        col.separator()
        col.label(text="Examples")
        for root in found:
            name = project.read_config(root).get("name") or root.name
            col.operator("gamestudio.project_open", text=display_name(name), icon="PACKAGE").path = str(root)


def draw_recent(col) -> None:
    col.label(text="Recent Projects")
    entries = projects.recent()[:RECENT_ROWS]
    if not entries:
        hint = col.column(align=True)
        hint.active = False
        hint.label(text="No recent projects yet.", icon="BLANK1")
        hint.label(text="Create one, or open an example.", icon="BLANK1")
        return
    for entry in entries:
        row = col.row()
        row.operator("gamestudio.project_open", text=display_name(entry.get("name", "?")),
                     icon="FILE_FOLDER").path = entry["path"]
        when = relative_time(entry.get("opened"))
        if when:
            sub = row.row()
            sub.alignment = "RIGHT"
            sub.ui_units_x = 4.5
            sub.active = False
            sub.label(text=when)
        _muted(col).label(text=short_path(entry["path"]), icon="BLANK1")


def draw_setup(col, context) -> None:
    """Node.js, the coding agent (Claude Code or Codex: the preference), the workspaces and the
    shortcuts: what a first run needs."""
    environment.draw_node_status(col)
    claude_code.draw_agent_status(col)
    if workspaces.missing():
        col.operator("gamestudio.add_workspaces", icon="WORKSPACE")
    keys = ", ".join(f"{keymap.shortcut_text(op)} {label}" for op, label in
                     (("gamestudio.play", "Play"), ("gamestudio.pause", "Pause"), ("gamestudio.step", "Step"),
                      ("gamestudio.stop", "Stop")))
    _muted(col).label(text=keys, icon="KEYINGSET")


def _draw_header(layout, title: str) -> None:
    from . import host

    header = layout.row()
    header.label(text=title, icon="PLAY")
    sub = header.row()
    sub.alignment = "RIGHT"
    sub.active = False
    sub.label(text=f"{host.gamestudio_version()} for Blender {bpy.app.version_string.split()[0]}")


def demo_project():
    """The demo game among the examples (else the first example), or None."""
    found = projects.examples()
    return next((root for root in found if root.name == DEMO), found[0] if found else None)


def draw_welcome(layout, context) -> None:
    """The first-run popup: the loop in three steps, and the three ways in."""
    layout.operator_context = "INVOKE_DEFAULT"
    _draw_header(layout, "Welcome to GameStudio")
    agent = claude_code.preferred_agent()
    _muted(layout).label(text=f"Make 3D games in Blender with {agent.label}, the coding agent.")
    layout.separator()

    col = layout.column(align=True)
    for number, (title, text, icon) in enumerate(welcome_steps(agent), 1):
        split = col.split(factor=0.36)
        split.label(text=f"{number}. {title}", icon=icon)
        _muted(split).label(text=text)
    layout.separator()

    row = layout.row()
    row.scale_y = 1.4
    row.operator("gamestudio.project_new", text="New Project...", icon="FILE_NEW")
    demo = demo_project()
    if demo is not None:
        row.operator("gamestudio.project_open", text="Open Demo", icon="PACKAGE").path = str(demo)
    row.operator(agent.open_op, icon="CONSOLE")
    layout.separator()

    col = layout.column()
    draw_setup(col, context)
    environment.draw_job(layout)

    layout.separator(type="LINE")
    split = layout.split(factor=0.47)
    left = split.row()
    left.operator("gamestudio.launcher", text="Launcher...", icon="WINDOW")
    left.menu("GAMESTUDIO_MT_claude_prompts", text="Starter Prompts", icon="COPYDOWN")
    prefs = get_prefs()
    if hasattr(prefs, "bl_rna"):
        split.prop(prefs, "show_launcher_at_startup")


def draw_launcher(layout, context) -> None:
    layout.operator_context = "INVOKE_DEFAULT"
    _draw_header(layout, "GameStudio")
    layout.separator()

    split = layout.split(factor=0.47)
    draw_start(split.column())
    draw_recent(split.column())

    layout.separator()
    col = layout.column()
    draw_setup(col, context)
    environment.draw_job(layout)

    layout.separator(type="LINE")
    split = layout.split(factor=0.47)
    left = split.column()
    left.operator("wm.open_mainfile", text="Open Scene File...", icon="FILE_BLEND")
    left.operator("wm.recover_last_session", text="Recover Last Session", icon="RECOVER_LAST")
    right = split.column()
    from .menus import DOCS_URL

    right.operator("wm.url_open", text="Documentation", icon="HELP").url = DOCS_URL
    prefs = get_prefs()
    if hasattr(prefs, "bl_rna"):
        right.prop(prefs, "show_launcher_at_startup")


class GAMESTUDIO_OT_launcher(bpy.types.Operator):
    bl_idname = "gamestudio.launcher"
    bl_label = "GameStudio Launcher"
    bl_description = "Start a new game project, open one, or open an example or a recent project"

    welcome: BoolProperty(options={"HIDDEN", "SKIP_SAVE"})

    def invoke(self, context, event):
        scale = context.preferences.system.ui_scale or 1.0
        return context.window_manager.invoke_popup(self, width=int(WIDTH * scale))

    def execute(self, context):
        return {"FINISHED"}

    def draw(self, context):
        (draw_welcome if self.welcome else draw_launcher)(self.layout, context)


# --- At startup, and after the extension is enabled ------------------------------------------
#
# Blender's splash is up when Blender starts, so the launcher opens then only when "Show launcher
# at startup" is on (which turns the splash off). Enabled by hand (Preferences, Install from Disk),
# it opens once, as the welcome; the "welcomed" preference keeps it to once.
#
# Add-ons register in a restricted context without a window, so which case this is shows only
# afterwards: at startup Blender loads its startup file right after registering add-ons (a
# load_post, before any timer runs); enabled by hand, nothing is loaded.

_state = {"tries": 0, "interactive": False}


def should_show(prefs, filepath: str, background: bool, interactive: bool) -> bool:
    """Show the launcher now: never over a file Blender was started with, nor in --background;
    at startup when the preference asks; once when the extension is first enabled by hand."""
    if background or filepath or not hasattr(prefs, "bl_rna"):
        return False
    if interactive:
        return not prefs.welcomed
    return bool(prefs.show_launcher_at_startup)


def _launch_window(wm):
    """Where the person is: a window showing the Preferences, else the main window."""
    for window in wm.windows:
        if window.screen is not None and any(area.type == "PREFERENCES" for area in window.screen.areas):
            return window
    return wm.windows[0]


def _override(window) -> dict:
    """The context for the launcher in `window`. The Preferences window shows a temporary screen, which
    temp_override refuses (TypeError): there the window alone is given and Blender finds its screen."""
    screen = window.screen
    if screen is None or getattr(screen, "is_temporary", False):
        return {"window": window}
    return {"window": window, "screen": screen}


@persistent
def _file_loaded(*_args):
    _state["interactive"] = False  # Blender is starting (or opening a file): not enabled by hand
    _forget_load_handler()


def _forget_load_handler() -> None:
    if _file_loaded in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_file_loaded)


def _startup_tick():
    wm = getattr(bpy.context, "window_manager", None)
    if wm is None or not wm.windows:
        _state["tries"] += 1
        return 0.25 if _state["tries"] < STARTUP_TRIES else None
    _forget_load_handler()
    prefs = get_prefs()
    if not should_show(prefs, bpy.data.filepath, bpy.app.background, _state["interactive"]):
        return None
    welcome = not prefs.welcomed
    window = _launch_window(wm)
    try:
        with bpy.context.temp_override(**_override(window)):
            bpy.ops.gamestudio.launcher("INVOKE_DEFAULT", welcome=welcome)
    except (RuntimeError, TypeError) as exc:
        log.warning("Could not open the launcher: %s", exc)
        return None
    prefs.welcomed = True  # only once it showed: a failure leaves the welcome for the next time
    if welcome and bpy.context.preferences.use_preferences_save:
        bpy.ops.wm.save_userpref()  # welcome once, not on every enable
    return None


def register():
    bpy.utils.register_class(GAMESTUDIO_OT_launcher)
    # Scripted sessions (--enable-event-simulate: the UI smoke test) enable it while Blender's splash
    # may still be up, which a person cannot do: no welcome there.
    _state.update(tries=0, interactive=not (bpy.app.background or bpy.app.use_event_simulate))
    if bpy.app.background:
        return
    if _file_loaded not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_file_loaded)
    if not bpy.app.timers.is_registered(_startup_tick):
        bpy.app.timers.register(_startup_tick, first_interval=0.5)


def unregister():
    if bpy.app.timers.is_registered(_startup_tick):
        bpy.app.timers.unregister(_startup_tick)
    _forget_load_handler()
    bpy.utils.unregister_class(GAMESTUDIO_OT_launcher)
