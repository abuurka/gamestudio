# SPDX-License-Identifier: GPL-3.0-or-later
"""The Game View's header and controls (GAMESTUDIO_GAME_VIEW).

The Game View itself is native: C++ registers the provider, draws the running
game in its main region and forwards input while it is captured
(docs/fork/ARCHITECTURE.md section 4). Python supplies the header through
host.editors.set_header(), the Start/Stop/Detach operators and the click that
captures input (an add-on keymap item for the native capture operator).

Builds without the "game_view" capability (stock Blender) keep the play
controls and open the game in its own window.
"""

import bpy

from .. import host
from ..core import devserver, link, play, project

IDNAME = host.game_view.IDNAME
KEYMAP_NAME = "Game View"
CAPTURE_OPERATOR = "gamestudio.game_view_capture"
NO_PROJECT = "Save the .blend inside a GameStudio project (folder with gamestudio.json)"

_keymap_items = []  # (keymap, item) added to the add-on key configuration


def game_url() -> str | None:
    """URL of the project's running dev server (from the live link, else from session.json)."""
    client = link.get_link()
    if client is not None and client.connected and client.session:
        url = client.session.get("url")
        if url:
            return url
    root = project.project_root()
    session = devserver.running_session(root, check_port=False) if root is not None else None
    return (session or {}).get("url")


def status_text(current: dict) -> tuple[str, str]:
    """(text, icon) summarising the docked game for the header; ("", "NONE") when idle."""
    state = current.get("state")
    if state in ("created", "waiting"):
        return "Starting...", "SORTTIME"
    if state == "connected":
        page = current.get("page_state")
        if current.get("mode") == "window":
            return "Detached", "WINDOW"
        if page == "crashed":
            return "Crashed", "ERROR"
        if page == "error":
            return "Page error", "ERROR"
        if page != "running":
            return "Loading...", "SORTTIME"
        if current.get("paused"):
            return "Paused", "PAUSE"
        if current.get("captured"):
            return "Captured (Shift+Esc)", "MOUSE_LMB"
        return "Running", "NONE"
    if state == "closed":
        return "Stopped", "NONE"
    if state == "failed":
        # A host that got as far as the handshake (it has a pid) and then failed died or hung.
        return ("Crashed" if current.get("host_pid") else "Failed"), "ERROR"
    if host.game_view.last_error():
        return "Could not start", "ERROR"
    return "", "NONE"


# --- Operators -------------------------------------------------------------------------


class GAMESTUDIO_OT_game_view_start(bpy.types.Operator):
    bl_idname = "gamestudio.game_view_start"
    bl_label = "Start Game View"
    bl_description = ("Run the game docked in the Game View: export the scene, start the dev server if "
                      "needed and play. Restarts a game that is already running here")

    @classmethod
    def poll(cls, context):
        if not host.game_view.available():
            cls.poll_message_set("This build has no docked Game View")
            return False
        return True

    def execute(self, context):
        root = project.project_root()
        if root is None:
            self.report({"ERROR"}, NO_PROJECT)
            return {"CANCELLED"}
        if not host.game_view.visible(context):
            self.report({"ERROR"}, "Show a Game View first (editor type menu > Game View)")
            return {"CANCELLED"}
        url = game_url()
        if url:
            area = context.area if host.game_view.shows_game_view(context.area) else None
            try:
                host.game_view.start(url, project_root=root, area=area)
            except host.game_view.GameViewError as exc:
                self.report({"ERROR"}, str(exc))
                return {"CANCELLED"}
        # Play exports the scene, starts or reuses the dev server, and loads the scene as soon as
        # the docked game connects (it docks the game itself when no URL was known yet).
        return bpy.ops.gamestudio.play()


class GAMESTUDIO_OT_game_view_stop(bpy.types.Operator):
    bl_idname = "gamestudio.game_view_stop"
    bl_label = "Close Game"
    bl_description = ("Close the game running in the Game View: end its runtime host (the dev server keeps "
                      "running; Play starts it again)")

    @classmethod
    def poll(cls, context):
        return host.game_view.status().get("active", False)

    def execute(self, _context):
        play.cancel()
        host.game_view.stop()
        return {"FINISHED"}


class GAMESTUDIO_OT_game_view_detach(bpy.types.Operator):
    bl_idname = "gamestudio.game_view_detach"
    bl_label = "Detach"
    bl_description = "Move the game into its own window"

    def execute(self, _context):
        if host.game_view.status().get("mode") == "window":
            return {"CANCELLED"}  # already in its own window
        if host.game_view.detach():
            return {"FINISHED"}
        # Not docked here (or stock Blender): open the running game in a window, as before.
        return bpy.ops.gamestudio.open_game()


class GAMESTUDIO_OT_game_view_devtools(bpy.types.Operator):
    bl_idname = "gamestudio.game_view_devtools"
    bl_label = "Open DevTools"
    bl_description = ("Open Chromium DevTools for the game in the Game View, in a window of its own: "
                      "console, breakpoints in the TypeScript sources, performance")

    @classmethod
    def poll(cls, _context):
        if host.game_view.status().get("state") != "connected":
            cls.poll_message_set("No game is running in the Game View")
            return False
        return True

    def execute(self, _context):
        if not host.game_view.open_devtools():
            self.report({"ERROR"}, "No game is running in the Game View")
            return {"CANCELLED"}
        return {"FINISHED"}


_CLASSES = (
    GAMESTUDIO_OT_game_view_start,
    GAMESTUDIO_OT_game_view_stop,
    GAMESTUDIO_OT_game_view_detach,
    GAMESTUDIO_OT_game_view_devtools,
)


# --- Header --------------------------------------------------------------------------


def draw_header(layout, context) -> None:
    client = link.get_link()
    connected = client.connected and bool(client.game_peers())
    state = client.game_state if connected else None

    # The same controls, in the same order, as the top bar and the Game menu.
    row = layout.row(align=True)
    row.operator("gamestudio.play", text="", icon="PLAY", depress=state == "playing")
    sub = row.row(align=True)
    sub.enabled = connected
    sub.operator("gamestudio.pause", text="", icon="PAUSE", depress=state == "paused")
    sub.operator("gamestudio.step", text="", icon="FRAME_NEXT")
    sub.operator("gamestudio.stop", text="", icon="SNAP_FACE")
    if state:
        layout.label(text=state.capitalize())

    layout.separator_spacer()
    if host.game_view.available():
        current = host.game_view.status()
        text, icon = status_text(current)
        if text:
            layout.label(text=text, icon=icon)
        # Play starts the docked game; once there is one, it can be restarted or closed here.
        if current.get("active"):
            row = layout.row(align=True)
            row.operator(GAMESTUDIO_OT_game_view_start.bl_idname, text="Restart", icon="FILE_REFRESH")
            row.operator(GAMESTUDIO_OT_game_view_stop.bl_idname, text="", icon="SNAP_FACE")
        connected = current.get("state") == "connected"
        tools = layout.row(align=True)
        devtools = tools.row(align=True)
        devtools.enabled = connected
        devtools.operator(GAMESTUDIO_OT_game_view_devtools.bl_idname, text="DevTools", icon="CONSOLE",
                          depress=bool(current.get("devtools")))
        detach = tools.row(align=True)
        detached = connected and current.get("mode") == "window"
        detach.enabled = not detached and (connected or client.connected)
        detach.operator(GAMESTUDIO_OT_game_view_detach.bl_idname, text="Detach", icon="WINDOW")
        return

    detach = layout.row()
    detach.enabled = client.connected
    detach.operator("gamestudio.open_game", text="Detach", icon="WINDOW")


# --- Click to capture input (fork only) ----------------------------------------------


def _register_keymap() -> None:
    """Clicking a running Game View captures input. The keymap itself is native; the items live in
    the add-on key configuration, which Blender keeps when it rebuilds the default one."""
    if not host.game_view.available():
        return
    wm = getattr(bpy.context, "window_manager", None)
    kc = wm.keyconfigs.addon if wm is not None else None
    if kc is None:
        return
    km = kc.keymaps.new(name=KEYMAP_NAME, space_type="EXTENSION_EDITOR", region_type="WINDOW")
    for button in ("LEFTMOUSE", "RIGHTMOUSE"):
        _keymap_items.append((km, km.keymap_items.new(CAPTURE_OPERATOR, button, "PRESS")))


def _unregister_keymap() -> None:
    for km, kmi in _keymap_items:
        try:
            km.keymap_items.remove(kmi)
        except (ReferenceError, RuntimeError):
            pass  # already gone with its key configuration
    _keymap_items.clear()


def register():
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    host.editors.set_header(IDNAME, draw_header)
    _register_keymap()


def unregister():
    _unregister_keymap()
    host.editors.set_header(IDNAME, None)
    # Like the dev server, a disabled extension leaves no game running behind it.
    host.game_view.stop()
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
