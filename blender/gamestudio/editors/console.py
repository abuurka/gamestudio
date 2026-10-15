# SPDX-License-Identifier: GPL-3.0-or-later
"""The game's Console: runtime logs and errors from the running game.

Messages from the game (console output, uncaught errors) and from the dev
server (build errors: TypeScript and Vite transform failures), newest last,
with a level filter and Clear. Every message that names a source file opens it
at that line in the code editor, and with Stack on, so does each frame of an
error's stack (locations are parsed once per message: core/locations.py). The
newest error stays in a banner at the top, with Open, until it is dismissed.

It has two hosts:

Text Editor (the Game workspace's bottom area, workspaces.py)
    The "Game Console" text mirrors the log, one message per line plus its
    location ("at src/systems/coins.ts:12:5"), so Blender's Text Editor gives
    scrolling, selection, copy and Ctrl+F. Double-click or Ctrl+click a line that
    names a file opens it (GAMESTUDIO_OT_console_open_line, in the Text keymap; it
    passes the click on in any other text). The header gets the filters, Stack,
    the newest error with Open and Dismiss, and Clear (draw_text_header). The text
    is rewritten when the log changes (a timer), at most MAX_LINES lines, keeping
    the view at the bottom unless the user scrolled up; it is emptied while the
    file saves, so no log ends up in the .blend.
GAMESTUDIO_CONSOLE (host.editors)
    A real editor on GameStudio Editor; on stock Blender a 3D View sidebar tab,
    shown only with the sidebar preference (prefs.sidebar_enabled()).
"""

import re
import time

import bpy
from bpy.app.handlers import persistent
from bpy.props import BoolProperty, EnumProperty, IntProperty

from .. import host, prefs, ui
from ..core import link, locations, project
from ..core.util import tag_redraw
from . import common

panel = host.editors.panel

LEVEL_ITEMS = [
    ("ALL", "All", "Show every message"),
    ("WARN", "Warnings", "Show warnings and errors"),
    ("ERROR", "Errors", "Show errors only"),
]
_LEVELS = {"ALL": None, "WARN": {"warn", "warning", "error"}, "ERROR": {"error"}}
_ICONS = {"error": "CANCEL", "warn": "ERROR", "warning": "ERROR", "info": "INFO", "debug": "DOT"}
_MAX_TEXT = 160

TEXT_NAME = "Game Console"
TEXT_PROP = "gamestudio_console"  # marks the console text (it may be renamed)
MAX_LINES = 2000                  # the console text's line cap; older lines are dropped
MESSAGE_LINES = 12                # lines of one message the text shows
SYNC_INTERVAL = 0.25
_TAGS = {"error": "ERROR", "warn": "WARN", "warning": "WARN", "info": "info", "debug": "debug"}

# Errors up to this link-log time are hidden from the banner (Dismiss); None: none are.
_dismissed = {"time": None}


def _level(context) -> str:
    return getattr(context.window_manager, "gs_console_level", "ALL")


def entries(level: str = "ALL") -> list:
    """Log entries that pass the level filter, oldest first."""
    levels = _LEVELS.get(level)
    logs = list(link.get_link().logs)
    if levels is None:
        return logs
    return [e for e in logs if str(e.get("level", "info")).lower() in levels]


def newest_error():
    """The error the banner shows: the newest one since the last Dismiss, or None."""
    return locations.latest_error(link.get_link().logs, since=_dismissed["time"])


def _plain_location(entry) -> str:
    """Where an entry says it comes from, when that is not a file we can open."""
    file = entry.get("file") or entry.get("source")
    if not file:
        return ""
    where = locations.Location(locations.normalize_file(str(file)), entry.get("line"), entry.get("col"))
    return where.text() if entry.get("file") else locations.normalize_file(str(file)).rsplit("/", 1)[-1]


def open_button(layout, where, text="", icon="FILE_SCRIPT", emboss=False) -> None:
    op = layout.operator("gamestudio.open_path", text=text, icon=icon, emboss=emboss)
    op.filepath = str(where.path)
    op.line = where.line or 0
    op.column = where.col or 0


def _title(entry, text: str) -> str:
    """The banner's first line: "Build error: ...", or the message when it already names its error type."""
    if entry.get("kind") == "build":
        return f"Build error: {text}"
    return text if re.match(r"^[\w.$]*(Error|Exception)\b", text) else f"Error: {text}"


def draw_banner(layout, context) -> None:
    entry = newest_error()
    if entry is None:
        return
    box = layout.box()
    message = str(entry.get("msg", "")).strip().splitlines() or [""]
    box.label(text=_title(entry, message[0])[:_MAX_TEXT], icon="CANCEL")
    found = locations.resolve_entry(entry, project.project_root())
    row = box.row(align=True)
    when = f"  ({ui.ago(entry['time'])})" if entry.get("time") else ""
    if found:
        row.label(text=f"at {found[0].text}{when}", icon="BLANK1")
        open_button(row, found[0], text="Open", icon="FILE_SCRIPT", emboss=True)
    else:
        where = _plain_location(entry)
        row.label(text=(f"at {where}" if where else "No source location") + when, icon="BLANK1")
    row.operator("gamestudio.dismiss_errors", text="", icon="X")


def draw_entry(col, entry, root, message_lines: int = 1, stack: bool = False) -> None:
    level = str(entry.get("level", "info")).lower()
    message = str(entry.get("msg", "")).strip().splitlines() or [""]
    prefix = "Build: " if entry.get("kind") == "build" else ""
    col.label(text=(prefix + message[0])[:_MAX_TEXT], icon=_ICONS.get(level, "INFO"))
    for extra in message[1:message_lines]:
        col.label(text=extra[:_MAX_TEXT], icon="BLANK1")
    found = locations.resolve_entry(entry, root)
    if not found:
        where = _plain_location(entry)
        if where:
            col.label(text=f"    at {where}", icon="BLANK1")
        return
    row = col.row(align=True)
    row.label(text=f"    at {found[0].text}", icon="BLANK1")
    open_button(row, found[0])
    if stack:
        for frame in found[1:]:
            row = col.row(align=True)
            row.label(text=f"        {frame.text}", icon="BLANK1")
            open_button(row, frame)


def draw_entries(layout, context, max_lines: int, level: str = "ALL", message_lines: int = 1,
                 stack: bool = False) -> None:
    """The game's log messages, newest last, each with buttons that open its source."""
    client = link.get_link()
    shown_entries = entries(level)
    if not shown_entries:
        if client.logs:
            layout.label(text=f"No matching messages ({ui.plural(len(client.logs), 'message')} in total).",
                         icon="FILTER")
        else:
            text = "No messages from the game yet." if client.connected else \
                "Messages from the running game and build errors appear here."
            ui.wrap_label(layout, text, icon="CONSOLE")
        return
    shown = shown_entries[-max_lines:] if max_lines > 0 else shown_entries
    col = layout.column(align=True)
    hidden = len(shown_entries) - len(shown)
    if hidden > 0:
        col.label(text=f"{ui.plural(hidden, 'older message')} not shown", icon="THREE_DOTS")
    root = project.project_root()
    for entry in shown:
        draw_entry(col, entry, root, message_lines=message_lines, stack=stack)


def draw_log(layout, context) -> None:
    wm = context.window_manager
    row = layout.row(align=True)
    row.prop(wm, "gs_console_level", expand=True)
    row.separator()
    row.prop(wm, "gs_console_stack", text="Stack", toggle=True)
    row.operator("gamestudio.clear_logs", text="Clear", icon="TRASH")
    draw_banner(layout, context)
    full = host.editors.in_editor(context)
    count = common.visible_rows(context, minimum=ui.CONSOLE_LINES, maximum=link.LOG_LIMIT, row_px=44.0)
    draw_entries(layout, context, max_lines=count, level=_level(context), message_lines=4 if full else 1,
                 stack=getattr(wm, "gs_console_stack", False))


def draw_header(layout, context) -> None:
    layout.separator_spacer()
    levels = [str(e.get("level", "info")).lower() for e in link.get_link().logs]
    errors = levels.count("error")
    warnings = levels.count("warn") + levels.count("warning")
    layout.label(text=f"{ui.plural(errors, 'error')}, {ui.plural(warnings, 'warning')}",
                 icon="CANCEL" if errors else ("ERROR" if warnings else "CHECKMARK"))
    if errors:
        layout.operator("gamestudio.open_latest_error", text="", icon="FILE_SCRIPT")


class GAMESTUDIO_OT_dismiss_errors(bpy.types.Operator):
    bl_idname = "gamestudio.dismiss_errors"
    bl_label = "Dismiss Errors"
    bl_description = "Hide the error banner until the next error (the messages stay in the console)"

    def execute(self, _context):
        newest = locations.latest_error(link.get_link().logs)
        _dismissed["time"] = max(time.time(), newest.get("time", 0.0) if newest else 0.0)
        tag_redraw()
        return {"FINISHED"}


# --- The Game Console text (the Game workspace's Text Editor) --------------------------------


_text_state = {"signature": None, "lines": [], "refs": [], "saving": None}


def console_text(create: bool = False):
    """The "Game Console" text of the open file (None without one, unless create)."""
    for text in bpy.data.texts:
        if text.get(TEXT_PROP):
            return text
    if not create:
        return None
    text = bpy.data.texts.new(TEXT_NAME)
    text[TEXT_PROP] = True
    sync(force=True)
    return text


def is_console(text) -> bool:
    return text is not None and bool(text.get(TEXT_PROP))


def _where_text(where, root) -> str:
    """A location as the text shows it: project files relative to the project, so a line still
    opens after the file is reloaded (parse_text finds it again)."""
    path = where.path
    try:
        name = path.resolve().relative_to(root.resolve()).as_posix() if root is not None else None
    except (OSError, ValueError):
        name = None
    text = name or path.as_posix()
    if where.line:
        text += f":{where.line}"
        if where.col:
            text += f":{where.col}"
    return text


def render(shown: list, total: int, root, stack: bool = False) -> tuple[list[str], list]:
    """The console text for log entries: (lines, the entry each line comes from, or None)."""
    lines = []
    refs = []
    if not shown:
        if total:
            lines.append(f"No matching messages ({ui.plural(total, 'message')} in total): the filter in the "
                         "header hides them.")
        else:
            lines += ["Messages from the running game and build errors appear here (Play: F5).",
                      "Double-click a line that names a file to open it in your code editor."]
        return lines, [None] * len(lines)
    for entry in shown:
        level = str(entry.get("level", "info")).lower()
        stamp = time.strftime("%H:%M:%S", time.localtime(entry["time"])) if entry.get("time") else "--:--:--"
        head = f"{stamp}  {_TAGS.get(level, level)[:5]:<5}  "
        indent = " " * len(head)
        message = str(entry.get("msg", "")).rstrip().splitlines() or [""]
        prefix = "Build: " if entry.get("kind") == "build" else ""
        body = [head + prefix + message[0]] + [indent + extra for extra in message[1:MESSAGE_LINES]]
        if len(message) > MESSAGE_LINES:
            body.append(indent + f"({ui.plural(len(message) - MESSAGE_LINES, 'more line')})")
        found = locations.resolve_entry(entry, root)
        if found:
            body += [indent + "at " + _where_text(where, root) for where in (found if stack else found[:1])]
        else:
            where = _plain_location(entry)
            if where:
                body.append(indent + "at " + where)
        lines += body
        refs += [entry] * len(body)
    if len(lines) > MAX_LINES:
        dropped = len(lines) - MAX_LINES + 1
        lines = [f"({ui.plural(dropped, 'older line')} not shown)"] + lines[dropped:]
        refs = [None] + refs[dropped:]
    return lines, refs


def _visible_lines(space, area) -> int:
    """How many lines a Text Editor shows; estimated from its height before it is first drawn."""
    return max(1, getattr(space, "visible_lines", 0) or area.height // 22)


def _views(text) -> list:
    """The Text Editors showing the text: [(space, visible lines, top, at_bottom)]."""
    count = len(text.lines)
    views = []
    for screen in bpy.data.screens:
        for area in screen.areas:
            space = area.spaces.active
            if area.type == "TEXT_EDITOR" and space.text == text:
                visible = _visible_lines(space, area)
                views.append((space, visible, space.top, space.top + visible >= count - 1))
    return views


def _write(text, lines: list[str], views=None, cursor=None) -> None:
    """Replace the text, keeping the cursor, and each view where it was or at the bottom."""
    views = _views(text) if views is None else views
    if cursor is None:
        cursor = (text.current_line_index, text.current_character, text.select_end_line_index,
                  text.select_end_character)
    text.from_string("\n".join(lines))
    last = len(text.lines) - 1
    try:
        line, char, end_line, end_char = cursor
        text.current_line_index, text.current_character = min(line, last), char
        text.select_end_line_index, text.select_end_character = min(end_line, last), end_char
    except (AttributeError, TypeError, ValueError):
        pass
    for space, visible, top, at_bottom in views:
        try:
            space.top = max(0, len(lines) - visible + 1) if at_bottom else min(top, last)
        except ReferenceError:
            pass


def _settings() -> tuple[str, bool]:
    wm = getattr(bpy.context, "window_manager", None)
    return getattr(wm, "gs_console_level", "ALL"), bool(getattr(wm, "gs_console_stack", False))


def sync(force: bool = False) -> bool:
    """Bring the console text up to date with the log. Returns True when it was rewritten."""
    if _text_state["saving"] is not None:
        return False
    text = console_text()
    if text is None:
        return False
    logs = link.get_link().logs
    level, stack = _settings()
    root = project.project_root()
    newest = logs[-1] if logs else None
    signature = (len(logs), id(newest), newest.get("time") if newest else None, id(logs[0]) if logs else None,
                 level, stack, str(root), text.as_pointer(), len(text.lines))
    if not force and signature == _text_state["signature"]:
        return False
    lines, refs = render(entries(level), len(logs), root, stack=stack)
    _write(text, lines)
    _text_state.update(signature=(*signature[:-1], len(text.lines)), lines=lines, refs=refs)
    return True


def location_at(text, index: int):
    """The source a console line points at (a Resolved), or None: a location in the line itself
    (a stack frame), else the location of the message the line belongs to."""
    if text is None or not 0 <= index < len(text.lines):
        return None
    root = project.project_root()
    body = text.lines[index].body
    for location in locations.parse_text(body):
        path = locations.resolve(location, root)
        if path is not None:
            return locations.Resolved(path, location.line, location.col, location.text(),
                                      locations._inside(path, root))
    lines, refs = _text_state["lines"], _text_state["refs"]
    if index < len(refs) and refs[index] is not None and lines[index] == body:
        found = locations.resolve_entry(refs[index], root)
        if found:
            return found[0]
    return None


class GAMESTUDIO_OT_console_open_line(bpy.types.Operator):
    bl_idname = "gamestudio.console_open_line"
    bl_label = "Open Console Line in Code Editor"
    bl_description = "Open the file this Game Console line names in the code editor, at its line"

    line: IntProperty(name="Line", description="Line of the console text (-1: the cursor's)", default=-1,
                      options={"SKIP_SAVE"})

    @classmethod
    def poll(cls, context):
        space = getattr(context, "space_data", None)
        return getattr(space, "type", "") == "TEXT_EDITOR" and is_console(space.text)

    def invoke(self, context, event):
        # Ctrl+click has not moved the cursor yet; a double-click's first click has.
        try:
            bpy.ops.text.cursor_set(x=event.mouse_region_x, y=event.mouse_region_y)
        except RuntimeError:
            pass
        text = context.space_data.text
        where = location_at(text, text.current_line_index)
        if where is None:
            return {"PASS_THROUGH"}  # a plain line: the click selects as usual
        return self._open(where)

    def execute(self, context):
        text = context.space_data.text
        where = location_at(text, self.line if self.line >= 0 else text.current_line_index)
        if where is None:
            self.report({"WARNING"}, "This line names no file of the project")
            return {"CANCELLED"}
        return self._open(where)

    @staticmethod
    def _open(where) -> set:
        """The Console's own way to open a source: gamestudio.open_path (ops.py)."""
        return bpy.ops.gamestudio.open_path(filepath=str(where.path), line=where.line or 0, column=where.col or 0)


def draw_text_header(self, context) -> None:
    """Appended to the Text Editor's header: the console's controls, when it shows the console."""
    space = context.space_data
    if not is_console(getattr(space, "text", None)):
        return
    layout = self.layout
    wm = context.window_manager
    layout.separator()
    row = layout.row(align=True)
    row.prop(wm, "gs_console_level", expand=True)
    row.prop(wm, "gs_console_stack", text="Stack", toggle=True)
    entry = newest_error()
    if entry is not None:
        message = str(entry.get("msg", "")).strip().splitlines() or [""]
        row = layout.row(align=True)
        row.alert = True
        row.operator("gamestudio.open_latest_error", text=_title(entry, message[0])[:60], icon="CANCEL")
        row.operator("gamestudio.dismiss_errors", text="", icon="X")
    else:
        draw_header(layout, context)
    layout.operator("gamestudio.clear_logs", text="Clear", icon="TRASH")


def fit_views(text) -> bool:
    """Scroll back the views that show empty space below the last line (an editor drawn for the
    first time, or made taller). Returns True when one moved."""
    moved = False
    for screen in bpy.data.screens:
        for area in screen.areas:
            space = area.spaces.active
            if area.type != "TEXT_EDITOR" or space.text != text or not getattr(space, "visible_lines", 0):
                continue
            best = max(0, len(text.lines) - space.visible_lines + 1)
            if space.top > best:
                space.top = best
                moved = True
    return moved


def _sync_tick():
    try:
        changed = sync()
        text = console_text()
        if (text is not None and fit_views(text)) or changed:
            tag_redraw(area_types=("TEXT_EDITOR",), region_type="WINDOW")
    except (ReferenceError, AttributeError, RuntimeError) as exc:  # e.g. while a file loads
        _text_state["signature"] = None
        print(f"GameStudio: the Game Console text was not updated: {exc}")
    return SYNC_INTERVAL


def _on_filter_changed(_self, _context):
    sync(force=True)
    tag_redraw()


@persistent
def _on_save_pre(*_args):
    """No log in the .blend: the console text is saved empty, and refilled after the save."""
    text = console_text()
    if text is None:
        return
    cursor = (text.current_line_index, text.current_character, text.select_end_line_index,
              text.select_end_character)
    _text_state["saving"] = (_views(text), cursor)
    text.clear()


@persistent
def _on_save_post(*_args):
    saved, _text_state["saving"] = _text_state["saving"], None
    text = console_text()
    if text is None or not sync(force=True) or saved is None:
        return
    _write(text, _text_state["lines"], views=saved[0], cursor=saved[1])  # the view as it was


@persistent
def _on_load_post(*_args):
    _text_state.update(signature=None, lines=[], refs=[], saving=None)
    sync(force=True)


# Double-click (Blender selects a word) and Ctrl+click (free) on a line that names a file.
_KEYS = (("LEFTMOUSE", "DOUBLE_CLICK", False), ("LEFTMOUSE", "PRESS", True))
_keymap_items = []
_HANDLERS = (("save_pre", _on_save_pre), ("save_post", _on_save_post), ("load_post", _on_load_post))


def _register_keys() -> None:
    kc = bpy.context.window_manager.keyconfigs.addon
    if kc is None:  # --background
        return
    km = kc.keymaps.new(name="Text", space_type="TEXT_EDITOR")
    for key, value, ctrl in _KEYS:
        kmi = km.keymap_items.new(GAMESTUDIO_OT_console_open_line.bl_idname, key, value, ctrl=ctrl)
        _keymap_items.append((km, kmi))


def _unregister_keys() -> None:
    for km, kmi in _keymap_items:
        try:
            km.keymap_items.remove(kmi)
        except (ReferenceError, RuntimeError):
            pass
    _keymap_items.clear()


def _shown(_context) -> bool:
    """The Console panel: always in GameStudio Editor's Console editor; in the stock 3D View sidebar
    only with the sidebar preference (the Game workspace has the Text Editor console)."""
    return host.editors.available() or prefs.sidebar_enabled()


class ConsoleEditor:
    idname = "GAMESTUDIO_CONSOLE"
    label = "Console"
    icon = "CONSOLE"
    description = "Log messages and errors from the running game and its build, linked to their source"
    panels = [
        panel("log", "Console", draw_log, poll=_shown, options={"HIDE_HEADER"}),
    ]
    header_draw = staticmethod(draw_header)


def register():
    bpy.types.WindowManager.gs_console_level = EnumProperty(
        name="Level", description="Which game messages the console shows", items=LEVEL_ITEMS, default="ALL",
        update=_on_filter_changed)
    bpy.types.WindowManager.gs_console_stack = BoolProperty(
        name="Stack", description="Show the stack frames of errors, each with a button that opens its source",
        default=False, update=_on_filter_changed)
    bpy.utils.register_class(GAMESTUDIO_OT_dismiss_errors)
    bpy.utils.register_class(GAMESTUDIO_OT_console_open_line)
    host.editors.register(ConsoleEditor)
    bpy.types.TEXT_HT_header.append(draw_text_header)
    for name, handler in _HANDLERS:
        handlers = getattr(bpy.app.handlers, name)
        if handler not in handlers:
            handlers.append(handler)
    _register_keys()
    if not bpy.app.timers.is_registered(_sync_tick):
        bpy.app.timers.register(_sync_tick, first_interval=SYNC_INTERVAL, persistent=True)


def unregister():
    if bpy.app.timers.is_registered(_sync_tick):
        bpy.app.timers.unregister(_sync_tick)
    _unregister_keys()
    for name, handler in _HANDLERS:
        handlers = getattr(bpy.app.handlers, name)
        if handler in handlers:
            handlers.remove(handler)
    bpy.types.TEXT_HT_header.remove(draw_text_header)
    host.editors.unregister(ConsoleEditor)
    bpy.utils.unregister_class(GAMESTUDIO_OT_console_open_line)
    bpy.utils.unregister_class(GAMESTUDIO_OT_dismiss_errors)
    del bpy.types.WindowManager.gs_console_stack
    del bpy.types.WindowManager.gs_console_level
