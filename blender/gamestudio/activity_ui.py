# SPDX-License-Identifier: GPL-3.0-or-later
"""The Agent popover, its status bar item and the lock count in the Outliner (activity.py has the logic).

  Top bar > agent button   the Agent popover (ui_topbar.py draws the button): live status and Pause,
                           Protected objects, Recent actions (click one to select and frame what it
                           touched; Undo to Here), their details and Open Activity Log
  Status bar               who is connected and what the agent does, in one short item: "Claude: idle",
                           "Codex: create_from_kit…", "Agent paused", "Claude not connected" (the agent
                           of the "Coding agent" preference while none is); a popover with the same
                           panel, so it works from any editor
  Outliner                 the number of protected objects in the header (a popover listing them),
                           and Protect / Unprotect in the object context menu
  3D View sidebar > Game   the same panel, only with the sidebar preference on (prefs.sidebar_enabled)
"""

import time

import bpy
from bpy.props import BoolProperty, IntProperty, StringProperty

from . import activity
from .core.log import get_logger
from .core.util import any_window, refresh_status_bar, tag_redraw

log = get_logger(__name__)

ROWS = 10
MORE_ROWS = 40
POPOVER_ROWS = 8
LOCK_ROWS = 6
RECENT_SECONDS = 4.0   # a finished call stays in the status bar this long
POPOVER_CHARS = 44     # characters per line in the popover (bl_ui_units_x = 16)


# --- Status ------------------------------------------------------------------------------------


def agent_name(short: bool = True) -> str:
    """Who is acting: "Claude" for Claude Code, "Codex", else the client's name, else "Agent"."""
    from . import bridge

    name = next((e["client"] for e in activity.entries() if e.get("client")), "")
    if not name:
        clients = bridge.clients_info() if bridge.is_running() else []
        name = clients[-1]["name"] if clients else ""
    if not name:
        return "Agent"
    label = bridge.client_label(name)
    return "Claude" if short and label == "Claude Code" else label


def status_item() -> tuple[str, str, bool, bool] | None:
    """(text, icon, alert, active) for the status bar: who is connected and what the agent does, in
    one item. None while the bridge is off outside a project (turned off on purpose)."""
    from . import claude_code
    from .core import project

    running = activity.current()
    if running is not None:
        return f"{agent_name()}: {running['method']}…", "SORTTIME", False, True
    if activity.is_paused():
        return "Agent paused", "PAUSE", True, True
    state, _port, clients = claude_code.bridge_state()
    if state == "off":
        return ("Agent bridge off", "UNLINKED", False, False) if project.project_root() is not None else None
    last = next(iter(activity.entries()), None)
    if last is not None and time.time() - last["start"] - (last["seconds"] or 0) < RECENT_SECONDS:
        icon = "CHECKMARK" if last["ok"] else "ERROR"
        return f"{agent_name()}: {last['method']}", icon, not last["ok"], last["changes"] or not last["ok"]
    if state == "listening":
        return f"{claude_code.expected_agent().short} not connected", "UNLINKED", False, False
    return agent_name() + (f" ({clients})" if clients > 1 else "") + ": idle", "LINKED", False, True


def topbar_item() -> tuple[str, str, bool, bool]:
    """(text, icon, alert, active) for the agent's button in the top bar: who, and an icon for what
    it does (the status bar has the words). While no agent is connected: the agent opened last in this
    session, else the one of the "Coding agent" preference (Claude Code or Codex)."""
    from . import claude_code

    if activity.current() is not None:
        return agent_name(), "SORTTIME", False, True
    if activity.is_paused():
        return "Paused", "PAUSE", True, True
    if claude_code.bridge_state()[0] == "connected":
        return agent_name(), "LINKED", False, True
    return claude_code.expected_agent().short, "UNLINKED", False, False


def entry_icon(e: dict) -> str:
    if e["ok"] is None:
        return "SORTTIME"
    if e.get("code") == "paused_by_user":
        return "PAUSE"
    if e.get("code") == "locked":
        return "LOCKED"
    if not e["ok"]:
        return "ERROR"
    return "CHECKMARK" if e["changes"] else "HIDE_OFF"


def entry_status(e: dict, undone: bool = False) -> str:
    if e["ok"] is None:
        return "running"
    if undone:
        return "undone"
    if e["ok"]:
        return "done" if e["changes"] else "read"
    return {"paused_by_user": "refused (paused)", "locked": "refused (locked objects)"}.get(e.get("code"), "failed")


def tooltip(e: dict) -> str:
    when = time.strftime("%H:%M:%S", time.localtime(e["start"]))
    parts = [f"{e['method']} ({entry_status(e, activity.is_undone(e))}, {when}"
             + (f", {e['seconds']:.2f} s)" if e["seconds"] is not None else ")")]
    if e["args"]:
        parts.append(e["args"])
    if e.get("error"):
        parts.append(activity.clip(e["error"], 300))
    for key in ("created", "changed", "deleted"):
        if e.get(key):
            parts.append(f"{key.capitalize()}: {', '.join(e[key][:8])}" + ("…" if len(e[key]) > 8 else ""))
    parts.append("Click to select and frame what it created or changed")
    return ".\n".join(parts)


# --- Drawing -----------------------------------------------------------------------------------


def _chars(width: int | None) -> int:
    """Characters per line: `width`, else what fits the region being drawn (the sidebar)."""
    from .ui import _wrap_width

    return width or _wrap_width()


def _hint(layout, text: str, width: int | None) -> None:
    from .ui import wrap_label

    col = layout.column()
    col.active = False
    wrap_label(col, text, width=_chars(width))


def draw_agent(layout, context, rows: int = ROWS, width: int | None = None) -> None:
    draw_status(layout, context, width)
    draw_locks(layout.box(), context, width=width)
    draw_log(layout, context, rows, width)


def draw_status(layout, context, width: int | None = None) -> None:
    from . import bridge

    paused = activity.is_paused()
    running = activity.current()
    split = layout.split(factor=0.62)
    row = split.row()
    if running is not None:
        elapsed = time.time() - running["start"]
        row.label(text=f"Running {running['method']}" + (f" ({elapsed:.0f} s)" if elapsed >= 1 else ""),
                  icon="SORTTIME")
    elif paused:
        sub = row.row()
        sub.alert = True
        sub.label(text="Paused", icon="PAUSE")
    else:
        row.label(text="Idle", icon="RADIOBUT_OFF")
    split.operator("gamestudio.agent_pause", text="Resume" if paused else "Pause",
                   icon="PLAY" if paused else "PAUSE", depress=paused)

    clients = bridge.clients_info() if bridge.is_running() else []
    line = layout.row()
    if not bridge.is_running():
        line.active = False
        line.label(text="Agent bridge off: no agent can reach Blender", icon="UNLINKED")
    elif not clients:
        line.active = False
        line.label(text="No agent connected", icon="UNLINKED")
    if not clients:
        _draw_open_agent(layout)
    for c in clients[:3]:
        version = f" {c['version']}" if c.get("version") else ""
        layout.label(text=f"{bridge.client_label(c['name']) or 'An agent'}{version} connected", icon="LINKED")
    if paused:
        _hint(layout, "The agent's changes are refused until you resume; it can still look at the scene.", width)


def _draw_open_agent(layout) -> None:
    """Open in Claude Code or Open in Codex (the "Coding agent" preference), in a project."""
    from . import claude_code
    from .core import project

    if project.project_root() is None:
        return
    row = layout.row()
    row.operator_context = "INVOKE_DEFAULT"
    row.operator(claude_code.preferred_agent().open_op, icon="CONSOLE")


def draw_locks(layout, context, limit: int = LOCK_ROWS, width: int | None = None) -> None:
    from .ui import object_icon

    objs = activity.locked_objects()
    row = layout.row()
    row.label(text=f"Protected: {len(objs)}" if objs else "Nothing protected", icon="LOCKED" if objs else "UNLOCKED")
    buttons = layout.row(align=True)
    buttons.operator("gamestudio.agent_protect", text="Protect", icon="LOCKED").lock = True
    buttons.operator("gamestudio.agent_protect", text="Unprotect", icon="UNLOCKED").lock = False
    if not objs:
        _hint(layout, "Select objects the agent must not change, then Protect.", width)
        return
    col = layout.column(align=True)
    for obj in objs[:limit]:
        r = col.row(align=True)
        r.operator("gamestudio.agent_show", text=obj.name, icon=object_icon(obj), emboss=False).name = obj.name
        op = r.operator("gamestudio.agent_protect", text="", icon="X", emboss=False)
        op.lock, op.name = False, obj.name
    if len(objs) > limit:
        more = col.row()
        more.active = False
        more.label(text=f"and {len(objs) - limit} more")


def draw_log(layout, context, rows: int = ROWS, width: int | None = None) -> None:
    wm = context.window_manager
    header = layout.row(align=True)
    header.label(text="Recent actions", icon="TIME")
    header.prop(wm, "gs_agent_changes_only", text="", icon="FILTER")
    header.operator("gamestudio.agent_open_log", text="", icon="TEXT")
    items = activity.entries()
    if wm.gs_agent_changes_only:
        items = [e for e in items if e["changes"]]
    if not items:
        _hint(layout, "Nothing yet: what the agent does in Blender appears here, newest first.", width)
        return
    limit = MORE_ROWS if wm.gs_agent_more else rows
    chosen = activity.selected()
    chars = max(16, _chars(width) - 6)
    stamp = activity.current_stamp()
    col = layout.column(align=True)
    for e in items[:limit]:
        row = col.row(align=True)
        row.alert = e["ok"] is False
        undone = activity.is_undone(e, stamp)
        row.active = not undone
        split = row.split(factor=0.9, align=True)
        left = split.row(align=True)
        left.alignment = "LEFT"
        is_chosen = e["id"] == chosen
        left.operator("gamestudio.agent_show", text=activity.clip(f"{e['method']}  {e['args']}".strip(), chars),
                      icon=entry_icon(e), emboss=is_chosen, depress=is_chosen).entry = e["id"]
        right = split.row(align=True)
        right.alignment = "RIGHT"
        if activity.has_undo_step(e) and not undone:
            right.operator("gamestudio.agent_undo_to", text="", icon="LOOP_BACK", emboss=False).entry = e["id"]
        else:
            right.label(text="", icon="BLANK1")
    if len(items) > rows:
        shown = min(len(items), MORE_ROWS)
        layout.prop(wm, "gs_agent_more", toggle=True,
                    text="Show fewer" if wm.gs_agent_more else f"Show {shown - rows} more")
    e = activity.entry(chosen)
    if e is not None:
        draw_details(layout.box(), e, width)


def draw_details(layout, e: dict, width: int | None = None) -> None:
    from .ui import wrap_label

    chars = _chars(width) - 2
    undone = activity.is_undone(e)
    head = layout.row()
    head.alert = e["ok"] is False
    head.label(text=f"{e['method']}: {entry_status(e, undone)}", icon=entry_icon(e))
    meta = layout.row()
    meta.active = False
    when = time.strftime("%H:%M:%S", time.localtime(e["start"]))
    seconds = f", {e['seconds']:.2f} s" if e["seconds"] is not None else ""
    meta.label(text=f"{when}{seconds}" + (f", {e['client']}" if e.get("client") else ""))
    if e["args"]:
        wrap_label(layout, e["args"], width=chars)
    if e.get("error"):
        col = layout.column()
        col.alert = True
        wrap_label(col, activity.clip(e["error"], 400), width=chars)
    for key in ("created", "changed", "deleted"):
        names = e.get(key)
        if names:
            total = e.get("counts", {}).get(key, len(names))
            wrap_label(layout, f"{key.capitalize()}: {', '.join(names[:12])}"
                       + (f" and {total - 12} more" if total > 12 else ""), width=chars)
    row = layout.row(align=True)
    targets = [n for n in e["created"] + e["changed"] if n in bpy.data.objects]
    sub = row.row(align=True)
    sub.enabled = bool(targets)
    sub.operator("gamestudio.agent_show", text="Select & Frame", icon="ZOOM_SELECTED").entry = e["id"]
    if activity.has_undo_step(e) and not undone:
        row.operator("gamestudio.agent_undo_to", text="Undo to Here", icon="LOOP_BACK").entry = e["id"]


def draw_statusbar(self, context) -> None:
    item = status_item()
    if item is None:
        return
    text, icon, alert, active = item
    row = self.layout.row(align=True)
    row.alert = alert
    row.active = active
    row.popover(panel="GAMESTUDIO_PT_agent_popover", text=text, icon=icon)


def draw_outliner_header(self, context) -> None:
    """Prepended: the header's right end, where appended items go, is clipped in a narrow Outliner."""
    count = activity.lock_count()
    if count:
        self.layout.popover(panel="GAMESTUDIO_PT_agent_locks", text=str(count), icon="LOCKED")


def draw_outliner_menu(self, context) -> None:
    layout = self.layout
    layout.separator()
    layout.operator("gamestudio.agent_protect", text="Protect from Agent", icon="LOCKED").lock = True
    layout.operator("gamestudio.agent_protect", text="Unprotect from Agent", icon="UNLOCKED").lock = False


def sidebar_enabled() -> bool:
    """The GameStudio panels in the 3D View sidebar are a preference (off: the top bar has this one)."""
    from . import prefs

    return prefs.sidebar_enabled()


class GAMESTUDIO_PT_agent(bpy.types.Panel):
    """The Agent popover in the 3D View sidebar, with the sidebar preference on."""

    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Game"
    bl_label = "Agent"
    bl_idname = "GAMESTUDIO_PT_agent"

    @classmethod
    def poll(cls, context):
        return sidebar_enabled()

    def draw_header(self, context):
        if activity.is_paused():
            self.layout.label(text="", icon="PAUSE")

    def draw(self, context):
        draw_agent(self.layout, context)


class GAMESTUDIO_PT_agent_popover(bpy.types.Panel):
    """The Agent panel from the top bar and the status bar, in any workspace."""

    bl_space_type = "TOPBAR"
    bl_region_type = "HEADER"
    bl_label = "Agent"
    bl_idname = "GAMESTUDIO_PT_agent_popover"
    bl_ui_units_x = 16

    def draw(self, context):
        layout = self.layout
        layout.label(text="Agent", icon="CONSOLE")
        draw_agent(layout, context, rows=POPOVER_ROWS, width=POPOVER_CHARS)
        layout.separator()
        layout.operator("gamestudio.agent_open_log", icon="TEXT")


class GAMESTUDIO_PT_agent_locks(bpy.types.Panel):
    bl_space_type = "TOPBAR"
    bl_region_type = "HEADER"
    bl_label = "Protected from the Agent"
    bl_idname = "GAMESTUDIO_PT_agent_locks"
    bl_ui_units_x = 12

    def draw(self, context):
        draw_locks(self.layout, context, limit=30, width=30)


# --- Operators ---------------------------------------------------------------------------------


def _view3d(context):
    area = context.area if context.area is not None and context.area.type == "VIEW_3D" else None
    screen = context.window.screen if context.window is not None else None
    if area is None and screen is not None:
        area = max((a for a in screen.areas if a.type == "VIEW_3D"), key=lambda a: a.width * a.height, default=None)
    region = next((r for r in area.regions if r.type == "WINDOW"), None) if area is not None else None
    return area, region


def select_and_frame(context, names) -> list:
    """Select the objects (the first one active) and frame them in a 3D View. Returns those found."""
    layer = context.view_layer
    objs = [o for o in (layer.objects.get(n) for n in dict.fromkeys(names)) if o is not None]
    if not objs:
        return []
    for obj in layer.objects:
        if obj.select_get():
            obj.select_set(False)
    for obj in objs:
        obj.select_set(True)
    layer.objects.active = objs[0]
    area, region = _view3d(context)
    if area is not None and region is not None:
        try:
            with context.temp_override(window=context.window, area=area, region=region):
                bpy.ops.view3d.view_selected()
        except RuntimeError:
            pass
    return objs


class GAMESTUDIO_OT_agent_show(bpy.types.Operator):
    bl_idname = "gamestudio.agent_show"
    bl_label = "Show Agent Action"
    bl_description = "Select and frame the objects this agent action created or changed"
    bl_options = {"REGISTER", "UNDO"}

    entry: IntProperty(options={"SKIP_SAVE"})
    name: StringProperty(options={"SKIP_SAVE"})

    @classmethod
    def description(cls, context, properties):
        if properties.name:
            return f"Select and frame {properties.name}"
        e = activity.entry(properties.entry)
        return tooltip(e) if e is not None else cls.bl_description

    def execute(self, context):
        if self.name:
            names = [self.name]
        else:
            e = activity.entry(self.entry)
            if e is None:
                return {"CANCELLED"}
            activity.select(e["id"])
            redraw()
            names = e["created"] + e["changed"]
        if not select_and_frame(context, names):
            return {"CANCELLED"}  # nothing to select: no undo step for it
        return {"FINISHED"}


class GAMESTUDIO_OT_agent_undo_to(bpy.types.Operator):
    bl_idname = "gamestudio.agent_undo_to"
    bl_label = "Undo to Here"
    bl_description = ("Undo back to just before this agent action. Everything after it is undone too, your own "
                      "edits included; Redo (Ctrl+Shift+Z) brings it back")
    bl_options = {"REGISTER"}  # it walks the undo history itself

    entry: IntProperty(options={"SKIP_SAVE"})

    def invoke(self, context, event):
        e = activity.entry(self.entry)
        what = e["method"] if e is not None else "this action"
        return context.window_manager.invoke_confirm(
            self, event, title="Undo to Here",
            message=f"Undo {what} and everything after it, your own edits included?", confirm_text="Undo")

    def execute(self, context):
        ok, message = activity.undo_to(self.entry)
        self.report({"INFO"} if ok else {"WARNING"}, message)
        redraw()
        return {"FINISHED"} if ok else {"CANCELLED"}


class GAMESTUDIO_OT_agent_pause(bpy.types.Operator):
    bl_idname = "gamestudio.agent_pause"
    bl_label = "Pause Agent"
    bl_description = ("Pause or resume the agent. While paused, its changes to the scene and the open file are "
                      "refused, and it is told that you paused it; it can still look at the scene")

    def execute(self, context):
        activity.set_paused(not activity.is_paused())
        self.report({"INFO"}, "Agent paused: its changes are refused" if activity.is_paused() else "Agent resumed")
        redraw()
        return {"FINISHED"}


class GAMESTUDIO_OT_agent_protect(bpy.types.Operator):
    bl_idname = "gamestudio.agent_protect"
    bl_label = "Protect from Agent"
    bl_description = "Lock the selected objects against AI changes"
    bl_options = {"REGISTER"}  # activity.set_locked records the undo step, also when called from a script

    lock: BoolProperty(name="Lock", default=True)
    name: StringProperty(options={"SKIP_SAVE"})

    @classmethod
    def description(cls, context, properties):
        target = properties.name or "the selected objects"
        if properties.lock:
            return f"Protect {target}: the agent must not change them (every agent tool refuses, run_python included)"
        return f"Unprotect {target}: the agent may change them again"

    def execute(self, context):
        obj = bpy.data.objects.get(self.name) if self.name else None
        objs = [obj] if obj is not None else ([] if self.name else list(context.selected_objects))
        if not objs:
            self.report({"WARNING"}, "Select the objects to protect first")
            return {"CANCELLED"}
        changed = activity.set_locked(objs, self.lock)
        verb = "Protected" if self.lock else "Unprotected"
        self.report({"INFO"}, f"{verb} {changed} object{'s' if changed != 1 else ''}")
        redraw()
        return {"FINISHED"} if changed else {"CANCELLED"}


class GAMESTUDIO_OT_agent_open_log(bpy.types.Operator):
    bl_idname = "gamestudio.agent_open_log"
    bl_label = "Open Activity Log"
    bl_description = "Open the project's agent activity log (.gamestudio/agent-activity.jsonl), one JSON line per call"

    def execute(self, context):
        path = activity.log_path()
        if path is None or not path.is_file():
            self.report({"WARNING"}, "No activity log yet (it is written in the project's .gamestudio folder)")
            return {"CANCELLED"}
        bpy.ops.wm.path_open(filepath=str(path))
        return {"FINISHED"}


# --- Keeping it current ------------------------------------------------------------------------


def redraw(now: bool = False) -> None:
    """Redraw the Agent panels, the Outliner headers and the status bar; `now` draws immediately,
    for a long call that is about to freeze Blender."""
    tag_redraw(area_types=("VIEW_3D",), region_type="UI")
    tag_redraw(area_types=("OUTLINER",), region_type="HEADER")
    refresh_status_bar()
    if now and not bpy.app.background:
        window = any_window()
        if window is not None:
            with bpy.context.temp_override(window=window):
                bpy.ops.wm.redraw_timer(type="DRAW_WIN_SWAP", iterations=1)


def _ensure_statusbar() -> None:
    """Keep the item in the status bar (WorkSpace.status_text_set() can drop it)."""
    header = bpy.types.STATUSBAR_HT_header
    funcs = getattr(header.draw, "_draw_funcs", None)
    if funcs is None or draw_statusbar not in funcs:
        header.append(draw_statusbar)


_shown = {"item": None, "version": -1}


def _tick():
    try:
        _ensure_statusbar()
        item, version = status_item(), activity.version()
    except Exception:  # never let the timer die: this is cosmetic
        log.debug("Agent status tick failed", exc_info=True)
        return 1.0
    if item != _shown["item"] or version != _shown["version"]:
        _shown.update(item=item, version=version)
        redraw()
    return 0.25


CLASSES = (GAMESTUDIO_PT_agent, GAMESTUDIO_PT_agent_popover, GAMESTUDIO_PT_agent_locks, GAMESTUDIO_OT_agent_show,
           GAMESTUDIO_OT_agent_undo_to, GAMESTUDIO_OT_agent_pause, GAMESTUDIO_OT_agent_protect,
           GAMESTUDIO_OT_agent_open_log)
# (header or menu, function, prepend?)
_ADDITIONS = (("STATUSBAR_HT_header", draw_statusbar, False), ("OUTLINER_HT_header", draw_outliner_header, True),
              ("OUTLINER_MT_object", draw_outliner_menu, False))


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    wm = bpy.types.WindowManager
    wm.gs_agent_more = BoolProperty(name="More", description="Show more of the agent's recent actions")
    wm.gs_agent_changes_only = BoolProperty(
        name="Changes Only", description="Show only the agent actions that change the scene or the open file")
    for name, func, first in _ADDITIONS:
        owner = getattr(bpy.types, name)
        (owner.prepend if first else owner.append)(func)
    if not bpy.app.background and not bpy.app.timers.is_registered(_tick):
        bpy.app.timers.register(_tick, first_interval=0.5, persistent=True)


def unregister():
    if bpy.app.timers.is_registered(_tick):
        bpy.app.timers.unregister(_tick)
    _shown.update(item=None, version=-1)
    for name, func, _first in reversed(_ADDITIONS):
        owner = getattr(bpy.types, name, None)
        if owner is not None:
            owner.remove(func)
    del bpy.types.WindowManager.gs_agent_more
    del bpy.types.WindowManager.gs_agent_changes_only
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
