# SPDX-License-Identifier: GPL-3.0-or-later
"""GameStudio's Game workspace: the 3D Viewport, the Outliner, Object Properties and the game's
Console, made of Blender's own editors.

  ┌──────────────────────────────┬────────────┐
  │                              │ Outliner   │
  │  3D Viewport (sidebar closed)├────────────┤
  │                              │ Properties │
  ├──────────────────────────────┤ (Object)   │
  │ Text Editor: Game Console    │            │
  └──────────────────────────────┴────────────┘

It comes from resources/workspaces.blend, which tools/build_workspaces.py generates from Blender's
factory startup file (Layout, its Timeline turned into a Text Editor). The Console is a Text Editor
showing the "Game Console" text that editors/console.py keeps in sync with the game's log.

When it is added:
  New Project      the start scene opens on it (show_game)
  a project file   opening a level of a GameStudio project that lacks it adds it, without leaving
                   the workspace the file opened on, unless the file was saved with it before (the
                   user removed it); the project's .gamestudio/workspaces.json remembers which
  on request       GameStudio > Add Game Workspace, the launcher, or the workspace tab menu

The workspace carries STATE_PROP, {"version", "fingerprint"}: the VERSION it was made from and
its layout when added (the editors and their places, fingerprint()). Opening a project file whose
copy is older than VERSION replaces it when its layout is unchanged; a copy the user changed stays,
and Reset Game Workspace (the workspace tab menu) replaces it on request.

Some settings only take once a window shows the workspace, so the workspace carries them in
LAYOUT_PROP, {area key: {step: value}}; they are applied when a window first shows it, then the
property is removed and the user's own changes are kept. An area key is an area type ("VIEW_3D",
the largest such area), optionally "@n" for the n-th largest or "/display_mode" (e.g.
"OUTLINER/LIBRARIES"). Steps:

  sidebar  the sidebar tab to open          context  the Properties tab (e.g. "OBJECT")
  console  show the Game Console text       height   the area's share of the window height
"""

import json
import pathlib

import bpy
from bpy.app.handlers import persistent

from .core import project
from .core.log import get_logger

log = get_logger(__name__)

BLEND = pathlib.Path(__file__).resolve().parent / "resources" / "workspaces.blend"
NAME = "Game"
NAMES = (NAME,)
VERSION = 1  # bump when build_workspaces.py changes the layout; outdated unchanged copies are replaced
LAYOUT_PROP = "gamestudio_layout"
STATE_PROP = "gamestudio_workspace"
RECORD = "workspaces.json"  # in the project's .gamestudio folder: files saved with the workspace
RETRIES = 30

_tries = {}
_msgbus_owner = object()
_retired = []  # names of replaced workspaces still shown by a window, removed once none shows them


# --- The workspace in the open file ------------------------------------------------------


def find():
    """The open file's Game workspace (the one GameStudio added, even renamed), or None."""
    ours = [ws for ws in bpy.data.workspaces if STATE_PROP in ws]
    return next((ws for ws in ours if ws.name == NAME), ours[0] if ours else None)


def missing() -> list[str]:
    """The GameStudio workspaces the open file does not have yet."""
    return [] if find() is not None else [NAME]


def state(workspace) -> dict:
    value = workspace.get(STATE_PROP)
    return value.to_dict() if hasattr(value, "to_dict") else {}


def fingerprint(workspace) -> str:
    """The workspace's layout: its name and its editors with their places (column and row ranks,
    which survive window resizes). Area sizes, views and tabs are not part of it."""
    screen = workspace.screens[0] if len(workspace.screens) else None
    if screen is None:
        return ""
    xs, ys = sorted({a.x for a in screen.areas}), sorted({a.y for a in screen.areas})
    areas = sorted((xs.index(a.x), ys.index(a.y), a.ui_type) for a in screen.areas)
    return f"{workspace.name}|{len(workspace.screens)}|" + ";".join(f"{x},{y},{kind}" for x, y, kind in areas)


def outdated(workspace) -> bool:
    try:
        return int(state(workspace).get("version", 0)) < VERSION
    except (TypeError, ValueError):
        return True


def modified(workspace) -> bool:
    """True when the user changed the workspace's layout since GameStudio added it."""
    return state(workspace).get("fingerprint") != fingerprint(workspace)


def _append():
    """Append the Game workspace from BLEND without showing it; None when that fails."""
    if not BLEND.is_file():
        log.error("%s is missing; reinstall GameStudio", BLEND)
        return None
    with bpy.data.libraries.load(str(BLEND)) as (source, target):
        if NAME not in source.workspaces:
            log.error("%s has no '%s' workspace; reinstall GameStudio", BLEND, NAME)
            return None
        target.workspaces = [NAME]
    _forget_library()
    workspace = target.workspaces[0] if target.workspaces else None
    if workspace is None:
        return None
    spec = workspace.get(LAYOUT_PROP)
    if spec:  # what needs no window happens now: the console shows its text before anyone looks
        remaining = apply_steps(None, workspace.screens[0], spec.to_dict())
        if remaining:
            workspace[LAYOUT_PROP] = remaining
        else:
            del workspace[LAYOUT_PROP]
    workspace[STATE_PROP] = {"version": int(state(workspace).get("version", VERSION)),
                             "fingerprint": fingerprint(workspace)}
    _tries.pop(workspace.name, None)
    schedule()
    return workspace


def _forget_library() -> None:
    """An append leaves a Library pointing at BLEND; nothing is linked from it, so remove it (twins
    and the asset tools would report a library inside the extension's folder)."""
    for library in list(bpy.data.libraries):
        try:
            same = pathlib.Path(bpy.path.abspath(library.filepath)).resolve() == BLEND
        except (OSError, ValueError):
            same = False
        if not same:
            continue
        linked = (item for collection in (bpy.data.workspaces, bpy.data.screens, bpy.data.texts, bpy.data.scenes,
                                          bpy.data.objects, bpy.data.worlds, bpy.data.brushes)
                  for item in collection if item.library == library)
        if next(linked, None) is None:
            bpy.data.libraries.remove(library)


def _main_window(window=None):
    if window is not None:
        return window
    window = getattr(bpy.context, "window", None)
    wm = getattr(bpy.context, "window_manager", None)
    if window is None and wm is not None and wm.windows:
        window = wm.windows[0]
    return window


def show(workspace, window=None) -> None:
    """Make a window (the current or first one) show the workspace; Blender switches on its next
    event loop."""
    window = _main_window(window)
    if window is not None and workspace is not None and window.workspace != workspace:
        window.workspace = workspace


def add(context=None, activate: bool = True) -> list[str]:
    """Add the Game workspace when the file has none; returns the names added. With activate, the
    window shows it."""
    if find() is not None:
        return []
    workspace = _append()
    if workspace is None:
        return []
    if activate:
        show(workspace, getattr(context, "window", None))
    return [workspace.name]


def show_game(window=None):
    """New Project: add the Game workspace if needed and show it. Returns it, or None."""
    workspace = find()
    if workspace is None:
        add(activate=False)
        workspace = find()
    show(workspace, window)
    return workspace


def replace(old):
    """Put a fresh Game workspace in the place of `old`: windows showing it switch to the new one,
    then `old` is removed (once no window shows it). Returns the new workspace or None."""
    wm = bpy.context.window_manager
    name = old.name
    shown = [w for w in wm.windows if w.workspace == old]
    del old[STATE_PROP]
    old.name = f"{name} (old)"
    new = _append()
    if new is None:
        old.name, old[STATE_PROP] = name, {"version": 0, "fingerprint": ""}
        return None
    new.name = name
    new[STATE_PROP] = {**state(new), "fingerprint": fingerprint(new)}
    for window in shown:
        window.workspace = new
    _retired.append(old.name)
    _remove_retired()
    return new


def _remove_retired() -> bool:
    """Remove the replaced workspaces no window shows any more. True when none are left."""
    wm = bpy.context.window_manager
    for name in list(_retired):
        workspace = bpy.data.workspaces.get(name)
        if workspace is None:
            _retired.remove(name)
            continue
        if wm is not None and any(w.workspace == workspace for w in wm.windows):
            continue
        screens = list(workspace.screens)
        bpy.data.batch_remove(ids=[workspace, *screens])
        _retired.remove(name)
    if _retired and not bpy.app.timers.is_registered(_retired_tick):
        bpy.app.timers.register(_retired_tick, first_interval=0.2)
    return not _retired


def _retired_tick():
    return None if _remove_retired() else 0.2


# --- Project files: added once, refreshed when unchanged ------------------------------------


def _record_path(root) -> pathlib.Path:
    return project.gs_dir(root) / RECORD


def _relative(root) -> str:
    try:
        return pathlib.Path(bpy.data.filepath).resolve().relative_to(pathlib.Path(root).resolve()).as_posix()
    except (OSError, ValueError):
        return pathlib.Path(bpy.data.filepath).as_posix()


def _saved_with_workspace(root) -> set[str]:
    try:
        data = json.loads(_record_path(root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set()
    files = data.get("saved_with_workspace") if isinstance(data, dict) else None
    return {str(f) for f in files} if isinstance(files, list) else set()


def _remember_saved(root) -> None:
    files = _saved_with_workspace(root)
    name = _relative(root)
    if name in files:
        return
    path = _record_path(root)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"saved_with_workspace": sorted(files | {name})}, indent=2) + "\n",
                        encoding="utf-8", newline="\n")
    except OSError as exc:
        log.warning("Could not write %s: %s", path, exc)


def check_project_file() -> str:
    """After a GameStudio project file opens: add the Game workspace (without showing it) unless the
    user removed it, or refresh an outdated copy the user has not changed. Returns what happened
    for the user ("" when nothing did)."""
    root = project.project_root()
    if root is None or not BLEND.is_file():
        return ""
    workspace = find()
    if workspace is None:
        if _relative(root) in _saved_with_workspace(root):
            return ""  # saved with it before: the user removed it
        return f"Added the {NAME} workspace (its tab is at the top of the window)" \
            if add(activate=False) else ""
    if not outdated(workspace):
        return ""
    if modified(workspace):
        return (f"The {workspace.name} workspace comes from an older GameStudio and has your changes; "
                f"right-click its tab > Reset Game Workspace to update it")
    return f"Updated the {NAME} workspace to this GameStudio's" if replace(workspace) is not None else ""


class GAMESTUDIO_OT_add_workspaces(bpy.types.Operator):
    bl_idname = "gamestudio.add_workspaces"
    bl_label = "Add Game Workspace"
    bl_description = ("Add the Game workspace to this file: the 3D Viewport, the Outliner, Object Properties "
                      "and the game's Console, made of Blender's editors")

    @classmethod
    def poll(cls, context):
        if getattr(context, "window", None) is None:
            return False
        if not missing():
            cls.poll_message_set("This file has the Game workspace")
            return False
        return True

    def execute(self, context):
        if not BLEND.is_file():
            self.report({"ERROR"}, f"{BLEND} is missing; reinstall GameStudio")
            return {"CANCELLED"}
        added = add(context)
        if not added:
            self.report({"ERROR"}, "Could not add the Game workspace")
            return {"CANCELLED"}
        self.report({"INFO"}, f"Added the {added[0]} workspace")
        return {"FINISHED"}


class GAMESTUDIO_OT_reset_game_workspace(bpy.types.Operator):
    bl_idname = "gamestudio.reset_game_workspace"
    bl_label = "Reset Game Workspace"
    bl_description = "Replace the Game workspace with this GameStudio's, undoing your changes to its layout"
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        if find() is None:
            cls.poll_message_set("This file has no Game workspace")
            return False
        return True

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event, title="Reset Game Workspace",
                                                     message="Your changes to its layout are lost.",
                                                     confirm_text="Reset", icon="WARNING")

    def execute(self, context):
        if replace(find()) is None:
            self.report({"ERROR"}, "Could not reset the Game workspace")
            return {"CANCELLED"}
        self.report({"INFO"}, "Reset the Game workspace")
        return {"FINISHED"}


class GAMESTUDIO_OT_workspace_note(bpy.types.Operator):
    """Shows a message from the load handler in the status bar (handlers cannot report)."""

    bl_idname = "gamestudio.workspace_note"
    bl_label = "Game Workspace"
    bl_options = {"INTERNAL"}

    message: bpy.props.StringProperty(options={"SKIP_SAVE"})

    def execute(self, _context):
        self.report({"INFO"}, self.message)
        return {"FINISHED"}


def _note(message: str) -> None:
    log.info(message)

    def report():
        window = _main_window()
        if window is not None:
            with bpy.context.temp_override(window=window):
                bpy.ops.gamestudio.workspace_note(message=message)

    if not bpy.app.background:
        bpy.app.timers.register(report, first_interval=0.5)


def draw_workspace_menu(self, context):
    """Appended to the workspace tab's context menu."""
    layout = self.layout
    layout.separator()
    if find() is None:
        layout.operator(GAMESTUDIO_OT_add_workspaces.bl_idname, icon="WORKSPACE")
    else:
        layout.operator(GAMESTUDIO_OT_reset_game_workspace.bl_idname, icon="FILE_REFRESH")


# --- Layout steps, once a window shows the workspace -----------------------------------------


def find_area(screen, key: str):
    """The area an area key names (see the module docstring), or None."""
    base, _, index = key.partition("@")
    area_type, _, mode = base.partition("/")
    areas = [a for a in screen.areas if a.type == area_type
             and (not mode or getattr(a.spaces.active, "display_mode", None) == mode)]
    areas.sort(key=lambda a: a.width * a.height, reverse=True)
    position = int(index or 0)
    return areas[position] if position < len(areas) else None


def set_sidebar_tab(area, tab: str) -> bool:
    for region in area.regions:
        if region.type == "UI":
            try:
                region.active_panel_category = tab
            except (AttributeError, TypeError, ValueError):
                return False  # the sidebar has not drawn its tabs yet
            return region.active_panel_category == tab
    return True  # no sidebar: nothing to do


def set_region_shown(window, screen, area, flag: str, shown: bool) -> bool:
    """Show or hide one of an area's regions (flag: a space's show_region_* property). Blender
    re-initialises the area through the context window when this changes, and a timer or a load
    handler has none (a crash), so it is done only while `window` shows `screen`, with that window
    as the context; returns False until then."""
    space = area.spaces.active
    if getattr(space, flag) == shown:
        return True
    if window is None or window.screen != screen:
        return False
    try:
        with bpy.context.temp_override(window=window, screen=screen, area=area):
            setattr(space, flag, shown)
    except (TypeError, RuntimeError):
        return False  # no usable window context: never toggle a region without one
    return True


def show_console(area, window=None, screen=None) -> bool:
    """Make a Text Editor the Game Console: its text, word wrap, no line numbers or highlighting,
    no footer (that last part waits for a window showing the screen; see set_region_shown)."""
    from .editors import console

    space = area.spaces.active
    space.text = console.console_text(create=True)
    space.show_line_numbers = False
    space.show_syntax_highlight = False
    space.show_word_wrap = True
    return set_region_shown(window, screen, area, "show_region_footer", False)


def set_properties_tab(area, tab: str) -> bool:
    try:
        area.spaces.active.context = tab
    except (TypeError, ValueError, AttributeError):
        return False  # not available yet (no active object)
    return area.spaces.active.context == tab


def set_height(window, screen, area, share: float) -> bool:
    """Move the area's top edge so it takes `share` of the window's height. Needs a window showing
    the screen; tried once, so a later resize by the user stays."""
    if window is None or window.screen != screen or area.height <= 1 or window.height <= 1:
        return False
    delta = int(window.height * share) - area.height
    if abs(delta) > 4:
        try:
            with bpy.context.temp_override(window=window, screen=screen, area=area):
                bpy.ops.screen.area_move(x=area.x + area.width // 2, y=area.y + area.height + 1, delta=delta)
        except (RuntimeError, TypeError) as exc:
            log.debug("Could not size the %s area: %s", area.type, exc)
    return True


def apply_steps(window, screen, spec: dict) -> dict:
    """Apply a LAYOUT_PROP spec to a screen; returns the steps still to retry."""
    remaining = {}
    for key, slot in spec.items():
        area = find_area(screen, key)
        if area is None:
            continue  # the user already changed this layout
        left = {}
        for step, value in slot.items():
            if step == "sidebar":
                done = (set_region_shown(window, screen, area, "show_region_ui", True)
                        and set_sidebar_tab(area, value))
            elif step == "context":
                done = set_properties_tab(area, value)
            elif step == "console":
                done = show_console(area, window, screen)
            elif step == "height":
                done = set_height(window, screen, area, float(value))
            else:
                done = True  # from a newer GameStudio
            if not done:
                left[step] = value
        if left:
            remaining[key] = left
    return remaining


def apply_layout(window) -> bool:
    """Set up the workspace `window` shows. Returns True when nothing is left to retry."""
    workspace = window.workspace
    spec = workspace.get(LAYOUT_PROP) if workspace is not None else None
    if not spec:
        return True
    remaining = apply_steps(window, window.screen, spec.to_dict())
    tries = _tries[workspace.name] = _tries.get(workspace.name, 0) + 1
    if remaining and tries < RETRIES:
        workspace[LAYOUT_PROP] = remaining
        return False
    del workspace[LAYOUT_PROP]
    return True


def pending(workspace) -> bool:
    return bool(workspace.get(LAYOUT_PROP))


def _tick():
    wm = getattr(bpy.context, "window_manager", None)
    if wm is None or not wm.windows:
        return 0.25
    for window in wm.windows:
        if window.screen is not None and not getattr(window.screen, "is_temporary", False):
            apply_layout(window)
    return 0.5 if any(pending(ws) for ws in bpy.data.workspaces) else None


def schedule(delay: float = 0.0) -> None:
    if bpy.app.timers.is_registered(_tick):
        bpy.app.timers.unregister(_tick)
    bpy.app.timers.register(_tick, first_interval=delay, persistent=True)


def _on_workspace_changed(*_args):
    schedule()


def _subscribe():
    """Set up a workspace right when its tab is clicked (the 0.5 s poll covers Ctrl+PageUp)."""
    bpy.msgbus.clear_by_owner(_msgbus_owner)
    try:
        bpy.msgbus.subscribe_rna(key=(bpy.types.Window, "workspace"), owner=_msgbus_owner, args=(),
                                 notify=_on_workspace_changed)
    except (TypeError, ValueError, KeyError):
        pass


@persistent
def _on_load_post(*_args):
    _tries.clear()
    _retired.clear()
    _subscribe()
    try:
        message = check_project_file()
    except Exception:  # noqa: BLE001 - a load handler must not fail the file's load
        log.exception("Could not check the Game workspace")
        message = ""
    if message:
        _note(message)
    if any(pending(ws) for ws in bpy.data.workspaces):
        schedule()


@persistent
def _on_save_post(*_args):
    root = project.project_root()
    if root is not None and find() is not None:
        _remember_saved(root)


_CLASSES = (GAMESTUDIO_OT_add_workspaces, GAMESTUDIO_OT_reset_game_workspace, GAMESTUDIO_OT_workspace_note)
_HANDLERS = (("load_post", _on_load_post), ("save_post", _on_save_post))


def register():
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    for name, handler in _HANDLERS:
        handlers = getattr(bpy.app.handlers, name)
        if handler not in handlers:
            handlers.append(handler)
    menu = getattr(bpy.types, "TOPBAR_MT_workspace_menu", None)
    if menu is not None:
        menu.append(draw_workspace_menu)
    _subscribe()


def unregister():
    bpy.msgbus.clear_by_owner(_msgbus_owner)
    menu = getattr(bpy.types, "TOPBAR_MT_workspace_menu", None)
    if menu is not None:
        menu.remove(draw_workspace_menu)
    for name, handler in _HANDLERS:
        handlers = getattr(bpy.app.handlers, name)
        if handler in handlers:
            handlers.remove(handler)
    for timer in (_tick, _retired_tick):
        if bpy.app.timers.is_registered(timer):
            bpy.app.timers.unregister(timer)
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
