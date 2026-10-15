# SPDX-License-Identifier: GPL-3.0-or-later
"""Operators (gamestudio.*): components, IDs, export, play controls, dev server, scripts.

Operators are thin: the work lives in core/. Every failure is reported with
self.report so the user sees why nothing happened.
"""

import pathlib

import bpy
from bpy.props import BoolProperty, EnumProperty, IntProperty, StringProperty

from . import environment
from .core import code_editor, components, devserver, export, ids, link, live, locations, play, project, registry
from .core import gameui, gameui_export, gameui_live, scaffold, scripts
from .core.util import any_window, tag_redraw
from .prefs import get_prefs

NO_PROJECT = "Save the .blend inside a GameStudio project (folder with gamestudio.json)"
NOT_CONNECTED = "No game is connected. Press Play (F5), or start the dev server and open the game"

# Blender keeps only pointers to the strings of dynamic enum items, so keep
# the last list alive here.
_component_enum_items = []


def component_category(entry: dict) -> str:
    return entry.get("category") or ("Built-in" if entry.get("builtin") else "Other")


def _component_items(_self, context):
    obj = getattr(context, "active_object", None) if context is not None else None
    attached = set(components.list_components(obj)) if obj is not None else set()
    items = []
    for number, name in enumerate(registry.component_names()):
        entry = registry.schema_ref(name)
        description = entry.get("description") or f"Add the {name} component"
        if name in attached:
            description = "Already attached. " + description
        items.append((name, f"{name}  ({component_category(entry)})", description, number))
    if not items:
        items.append(("NONE", "No components in the registry", "Start the dev server to generate the registry", 0))
    _component_enum_items[:] = items
    return _component_enum_items


def _active_object(op, context):
    obj = context.active_object
    if obj is None:
        op.report({"ERROR"}, "Select an object first")
    return obj


def _game_client(op):
    client = link.get_link()
    if client is None or not client.connected or not client.game_peers():
        op.report({"WARNING"}, NOT_CONNECTED)
        return None
    return client


# --- Components ------------------------------------------------------------------


class GAMESTUDIO_OT_add_component(bpy.types.Operator):
    bl_idname = "gamestudio.add_component"
    bl_label = "Add Component"
    bl_description = "Attach a component from the project's registry to the active object"
    bl_options = {"REGISTER", "UNDO"}
    bl_property = "component"

    component: EnumProperty(name="Component", items=_component_items)

    @classmethod
    def poll(cls, context):
        if context.active_object is None:
            cls.poll_message_set("Select an object first")
            return False
        return True

    def invoke(self, context, _event):
        if self.properties.is_property_set("component"):
            return self.execute(context)
        if not registry.component_names():
            self.report({"ERROR"}, "The component registry is empty. Start the dev server or reload the registry")
            return {"CANCELLED"}
        context.window_manager.invoke_search_popup(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        obj = _active_object(self, context)
        if obj is None:
            return {"CANCELLED"}
        if self.component == "NONE":
            self.report({"ERROR"}, "The component registry is empty. Start the dev server or reload the registry")
            return {"CANCELLED"}
        try:
            components.add_component(obj, self.component)
        except ValueError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        self.report({"INFO"}, f"Added {self.component} to {obj.name}")
        return {"FINISHED"}


class GAMESTUDIO_OT_remove_component(bpy.types.Operator):
    bl_idname = "gamestudio.remove_component"
    bl_label = "Remove Component"
    bl_description = "Detach this component from the active object and discard its values"
    bl_options = {"REGISTER", "UNDO"}

    component: StringProperty(name="Component", options={"SKIP_SAVE"})

    def execute(self, context):
        obj = _active_object(self, context)
        if obj is None:
            return {"CANCELLED"}
        try:
            components.remove_component(obj, self.component)
        except ValueError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        self.report({"INFO"}, f"Removed {self.component} from {obj.name}")
        return {"FINISHED"}


class GAMESTUDIO_OT_toggle_component_enabled(bpy.types.Operator):
    bl_idname = "gamestudio.toggle_component_enabled"
    bl_label = "Toggle Component"
    bl_description = "Enable or disable this component (disabled components are not exported)"
    bl_options = {"REGISTER", "UNDO"}

    component: StringProperty(name="Component", options={"SKIP_SAVE"})

    def execute(self, context):
        obj = _active_object(self, context)
        if obj is None:
            return {"CANCELLED"}
        try:
            components.set_enabled(obj, self.component, not components.is_enabled(obj, self.component))
        except ValueError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        return {"FINISHED"}


class GAMESTUDIO_OT_reload_registry(bpy.types.Operator):
    bl_idname = "gamestudio.reload_registry"
    bl_label = "Reload Components"
    bl_description = "Re-read .gamestudio/components.json and rebuild the component properties"

    def execute(self, _context):
        root = project.project_root()
        if root is None:
            self.report({"ERROR"}, NO_PROJECT)
            return {"CANCELLED"}
        if registry.reload_registry(root):
            count = len(registry.component_names())
            self.report({"INFO"}, f"Loaded {count} component{'s' if count != 1 else ''}")
            return {"FINISHED"}
        if not project.registry_path(root).exists():
            self.report({"WARNING"},
                        "No .gamestudio/components.json yet. Start the dev server; it writes the registry")
        else:
            problems = registry.warnings()
            self.report({"ERROR"}, problems[0] if problems else "Could not load the component registry")
        return {"CANCELLED"}


class GAMESTUDIO_OT_assign_ids(bpy.types.Operator):
    bl_idname = "gamestudio.assign_ids"
    bl_label = "Assign Entity IDs"
    bl_description = "Give every object in the scene a stable GameStudio ID and repair duplicated IDs"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        scene = context.scene
        repaired = ids.repair_duplicate_ids(scene)
        missing = [obj for obj in scene.objects if ids.get_id(obj) is None]
        for obj in missing:
            ids.ensure_id(obj)
        self.report({"INFO"}, f"Assigned {len(missing)} new ID(s), repaired {repaired} duplicate(s)")
        return {"FINISHED"}


# --- Export and play ---------------------------------------------------------------


def _export(op, context):
    try:
        return export.export_scene(context.scene)
    except export.ExportError as exc:
        op.report({"ERROR"}, str(exc))
    except (OSError, RuntimeError) as exc:
        op.report({"ERROR"}, f"Export failed: {exc}")
    return None


def _play_export(op, context, root):
    """Play's export. From a level: the scene, then the UI files whose screens are missing or older than
    their .blend (they never stop the Play). From a UI file (docs/GAME-UI.md §4): the UI file and the stale
    ones, then the start level's existing export (made in a background Blender when it is missing)."""
    if gameui.is_ui_file():
        try:
            result = gameui_export.play_export(root)
        except export.ExportError as exc:
            op.report({"ERROR"}, str(exc))
            return None
        except (OSError, RuntimeError) as exc:
            op.report({"ERROR"}, f"Export failed: {exc}")
            return None
        for error in result["ui"]["errors"]:
            op.report({"WARNING"}, f"UI export of {error['file']} failed: {error['error']}")
        return result
    result = _export(op, context)
    if result is not None:
        try:
            ui = gameui_export.export_stale(root)
        except (OSError, RuntimeError) as exc:
            op.report({"WARNING"}, f"UI export failed: {exc}")
        else:
            for error in ui["errors"]:
                op.report({"WARNING"}, f"UI export of {error['file']} failed: {error['error']}")
    return result


class GAMESTUDIO_OT_export_scene(bpy.types.Operator):
    bl_idname = "gamestudio.export_scene"
    bl_label = "Export Scene"
    bl_description = "Write the scene's .glb and .scene.json to .gamestudio/export (and reload it in a running game)"

    def execute(self, context):
        if gameui.is_ui_file():  # UI screens, not a level: the live link sends changed screens to games
            try:
                result = gameui_export.export_ui_file()
            except (export.ExportError, OSError, RuntimeError) as exc:
                self.report({"ERROR"}, f"Export failed: {exc}")
                return {"CANCELLED"}
            self.report({"INFO"}, f"Exported {len(result['canvases'])} UI screen(s)")
            return {"FINISHED"}
        result = _export(self, context)
        if result is None:
            return {"CANCELLED"}
        client = link.get_link()
        if client.connected and client.game_peers():
            client.send(export.scene_load_message(result))
            live.reseed(context.scene)
        self.report({"INFO"}, f"Exported '{result['scene']}' rev {result['rev']} ({result['entities']} entities)")
        return {"FINISHED"}


SETTING_UP = "Setting up GameStudio first (see the status bar); {what} when it is ready"


def _play_when_ready(ok: bool, message: str) -> None:
    """GameStudio's setup ended: play. When it was skipped or failed, play anyway if only the game
    browser is missing (the game opens in a window instead), else say why not."""
    root = project.project_root()
    if root is None:
        return
    if not ok and environment.missing_essentials(root, "play"):
        if message:
            environment.notify(message, "ERROR")
        return
    window = any_window()
    if window is not None:
        with bpy.context.temp_override(window=window):
            bpy.ops.gamestudio.play()


_SERVER_MESSAGES = {
    "started": "Starting the dev server...",
    "starting": "The dev server is starting...",
    "running": "Connecting to the dev server...",
}


class GAMESTUDIO_OT_play(bpy.types.Operator):
    bl_idname = "gamestudio.play"
    bl_label = "Play"
    bl_description = ("Export the scene, start the dev server if needed, and play it: streamed into the 3D "
                      "Viewport (Embedded game display) or in its own window (Window), as the top bar's "
                      "toggle next to Play sets")

    def execute(self, context):
        root = project.project_root()
        if root is None:
            self.report({"ERROR"}, NO_PROJECT)
            return {"CANCELLED"}
        # Node.js, the game packages, the project's links and a browser: set up first when missing
        # (a few cached file checks when nothing is), and this Play goes on once they are.
        state = environment.ensure_ready(root, "play", then=_play_when_ready)
        if state in (environment.STARTED, environment.ASKING):
            self.report({"INFO"}, SETTING_UP.format(what="the game starts"))
            return {"FINISHED"}
        if state != environment.READY:
            self.report({"ERROR"}, state)
            return {"CANCELLED"}
        prefs = get_prefs()
        # What takes seconds starts first and runs while the scene exports: the dev server, and in
        # the Embedded display the game's browser (or the game kept from the last Play is reused).
        try:
            server = play.prepare(root, open_window=prefs.open_game_window)
        except devserver.DevServerError as exc:
            play.cancel()
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        result = None
        ui_canvas = context.scene.name if gameui.is_ui_file() else None
        try:
            result = _play_export(self, context, root)
        finally:
            if result is None:
                play.abandon()
        if result is None:
            return {"CANCELLED"}
        sent = play.request_play(result, start=True, open_window=prefs.open_game_window)
        if ui_canvas is not None:
            gameui_live.open_after_play(ui_canvas)  # the screen being edited, once the level plays
        if sent:
            self.report({"INFO"}, f"Playing '{result['scene']}' (rev {result['rev']})")
            return {"FINISHED"}

        client = link.get_link()
        if not client.connected:
            message = _SERVER_MESSAGES.get(server, "Connecting to the dev server...")
        elif play.embedded():
            message = play.status() or "Opening the game..."
        elif prefs.open_game_window:
            message = "Opening the game..."
        else:
            message = "Waiting for a game to connect. Open the game to start playing"
        self.report({"INFO"}, message)
        return {"FINISHED"}


class GAMESTUDIO_OT_stop(bpy.types.Operator):
    bl_idname = "gamestudio.stop"
    bl_label = "Stop"
    bl_description = ("Stop the game and reset it to the loaded scene. The Game View turns back into the 3D "
                      "Viewport; the game stays ready, hidden, so the next Play shows it at once")

    def execute(self, _context):
        play.stop()
        client = _game_client(self)
        if client is None:
            return {"CANCELLED"}
        client.send({"t": "play.stop"})
        client.game_state = "stopped"
        tag_redraw()
        return {"FINISHED"}


class GAMESTUDIO_OT_pause(bpy.types.Operator):
    bl_idname = "gamestudio.pause"
    bl_label = "Pause"
    bl_description = "Pause or resume the game's simulation"

    def execute(self, _context):
        client = _game_client(self)
        if client is None:
            return {"CANCELLED"}
        paused = client.game_state != "paused"
        client.send({"t": "play.pause", "paused": paused})
        client.game_state = "paused" if paused else "playing"  # until game.status confirms
        tag_redraw()
        return {"FINISHED"}


class GAMESTUDIO_OT_step(bpy.types.Operator):
    bl_idname = "gamestudio.step"
    bl_label = "Step"
    bl_description = "Advance the paused game by fixed steps"

    frames: IntProperty(name="Frames", default=1, min=1, max=600)

    def execute(self, _context):
        client = _game_client(self)
        if client is None:
            return {"CANCELLED"}
        if client.game_state != "paused":
            client.send({"t": "play.pause", "paused": True})
            client.game_state = "paused"
        client.send({"t": "play.step", "frames": self.frames})
        return {"FINISHED"}


# --- Dev server --------------------------------------------------------------------


def _start_server_when_ready(ok: bool, message: str) -> None:
    root = project.project_root()
    if root is None:
        return
    if not ok and environment.missing_essentials(root, "play"):
        if message:
            environment.notify(message, "ERROR")
        return
    try:
        devserver.start(root)
    except devserver.DevServerError as exc:
        environment.notify(str(exc), "ERROR")


class GAMESTUDIO_OT_start_server(bpy.types.Operator):
    bl_idname = "gamestudio.start_server"
    bl_label = "Start Dev Server"
    bl_description = "Run the project's dev server ('npm run dev') and connect the live link"

    def execute(self, _context):
        root = project.project_root()
        if root is None:
            self.report({"ERROR"}, NO_PROJECT)
            return {"CANCELLED"}
        ready = environment.ensure_ready(root, "play", then=_start_server_when_ready)
        if ready in (environment.STARTED, environment.ASKING):
            self.report({"INFO"}, SETTING_UP.format(what="the dev server starts"))
            return {"FINISHED"}
        if ready != environment.READY:
            self.report({"ERROR"}, ready)
            return {"CANCELLED"}
        try:
            state = devserver.start(root)
        except devserver.DevServerError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        messages = {
            "started": "Starting the dev server...",
            "starting": "The dev server is already starting",
            "running": "The dev server is already running",
        }
        self.report({"INFO"}, messages[state])
        return {"FINISHED"}


class GAMESTUDIO_OT_stop_server(bpy.types.Operator):
    bl_idname = "gamestudio.stop_server"
    bl_label = "Stop Dev Server"
    bl_description = "Stop the dev server that GameStudio started"

    def execute(self, _context):
        play.cancel()
        if devserver.stop():
            self.report({"INFO"}, "Dev server stopped")
            return {"FINISHED"}
        if devserver.running_session(check_port=False) is not None:
            self.report({"WARNING"}, "This dev server was not started by GameStudio; stop it in its terminal")
        else:
            self.report({"INFO"}, "No dev server is running")
        return {"CANCELLED"}


class GAMESTUDIO_OT_open_game(bpy.types.Operator):
    bl_idname = "gamestudio.open_game"
    bl_label = "Open Game"
    bl_description = "Open the running game in a browser window"

    def execute(self, _context):
        root = project.project_root()
        if root is None:
            self.report({"ERROR"}, NO_PROJECT)
            return {"CANCELLED"}
        session = (link.get_link().session or {}) if link.get_link().connected else project.read_session(root)
        url = (session or {}).get("url")
        if not url:
            self.report({"ERROR"}, "The dev server is not running. Start it first")
            return {"CANCELLED"}
        devserver.open_game_window(url)
        return {"FINISHED"}


class GAMESTUDIO_OT_open_path(bpy.types.Operator):
    bl_idname = "gamestudio.open_path"
    bl_label = "Open in Editor"
    bl_description = ("Open this file: scripts and other text files in the code editor (at the line, when "
                      "given), folders and other files with their default application")

    filepath: StringProperty(name="File", subtype="FILE_PATH", options={"SKIP_SAVE"})
    line: IntProperty(name="Line", description="Line to show (0: none)", default=0, min=0, options={"SKIP_SAVE"})
    column: IntProperty(name="Column", description="Column to show (0: none)", default=0, min=0,
                        options={"SKIP_SAVE"})

    def execute(self, _context):
        path = pathlib.Path(self.filepath)
        if not path.is_absolute():
            root = project.project_root()
            path = (root / path) if root is not None else path
        if not path.exists():
            self.report({"ERROR"}, f"{path} does not exist")
            return {"CANCELLED"}
        if path.is_file() and code_editor.is_source_file(path):
            return open_in_code_editor(self, path, self.line, self.column)
        if path.is_file() and code_editor.runs_when_opened(path):
            # The path can come from the project or a game's log: opening a program would run it.
            self.report({"WARNING"}, f"{path.name} would run if opened: showing its folder instead")
            path = path.parent
        try:
            bpy.ops.wm.path_open(filepath=str(path))
        except RuntimeError as exc:
            self.report({"ERROR"}, f"Could not open {path.name}: {exc}")
            return {"CANCELLED"}
        return {"FINISHED"}


# --- Code editor and scripts -------------------------------------------------------


def open_in_code_editor(op, path=None, line=0, column=0) -> set:
    """Open a file at line:column in the code editor, in the project's window; without a path, the project."""
    try:
        message = code_editor.open_location(path, line or None, column or None, project=project.project_root())
    except code_editor.CodeEditorError as exc:
        op.report({"ERROR"}, str(exc))
        return {"CANCELLED"}
    op.report({"INFO"}, message)
    return {"FINISHED"}


class GAMESTUDIO_OT_open_code_project(bpy.types.Operator):
    bl_idname = "gamestudio.open_code_project"
    bl_label = "Open Project in Code Editor"
    bl_description = ("Open the project folder in your code editor (VS Code or Cursor unless the GameStudio "
                      "preferences say otherwise), with TypeScript IntelliSense for the engine")

    @classmethod
    def poll(cls, context):
        if project.project_root() is None:
            cls.poll_message_set(NO_PROJECT)
            return False
        return True

    def execute(self, _context):
        return open_in_code_editor(self)


class GAMESTUDIO_OT_open_latest_error(bpy.types.Operator):
    bl_idname = "gamestudio.open_latest_error"
    bl_label = "Open Newest Error"
    bl_description = "Open the source of the newest game or build error in the code editor, at its line"

    def execute(self, _context):
        root = project.project_root()
        logs = link.get_link().logs
        entry = locations.latest_error(logs, with_location_in=root)
        if entry is None:
            if locations.latest_error(logs) is None:
                self.report({"WARNING"}, "No errors from the game or the build")
            else:
                self.report({"WARNING"}, "The errors do not point at a file in this project")
            return {"CANCELLED"}
        where = locations.resolve_entry(entry, root)[0]
        return open_in_code_editor(self, where.path, where.line, where.col)


# Blender keeps only pointers to the strings of dynamic enum items.
_query_items = []


def _query_component_items(_self, _context):
    items = [("NONE", "None (game-wide)", "A system without a query: its update runs once per frame")]
    found = {}
    for name in registry.component_names():
        entry = registry.schema_ref(name) or {}
        found[name] = (bool(entry.get("builtin")), entry.get("description") or name)
    root = project.project_root()
    for name in scripts.component_names(root) if root is not None else ():
        found.setdefault(name, (False, "Not in the registry yet: start the dev server"))
    ordered = sorted(found.items(), key=lambda item: (item[1][0], item[0]))
    for number, (name, (builtin, description)) in enumerate(ordered, start=1):
        items.append((name, f"{name}  (built-in)" if builtin else name, description, number))
    _query_items[:] = items
    return _query_items


class _NewScript:
    """New Component / New System: a dialog with the name and a preview of the file, then scaffold.create."""

    kind = ""

    name: StringProperty(name="Name", description="PascalCase name, e.g. HealthPack", options={"SKIP_SAVE"})
    open_editor: BoolProperty(name="Open in Code Editor", description="Open the new file in the code editor",
                              default=True, options={"SKIP_SAVE"})

    @classmethod
    def poll(cls, context):
        if project.project_root() is None:
            cls.poll_message_set(NO_PROJECT)
            return False
        return True

    def query(self) -> str:
        return ""

    def invoke(self, context, _event):
        return context.window_manager.invoke_props_dialog(self, width=440, confirm_text="Create")

    def draw(self, context):
        layout = self.layout
        layout.activate_init = True
        layout.prop(self, "name")
        self.draw_options(layout)
        layout.prop(self, "open_editor")
        if not self.name.strip():
            example = "HealthPack" if self.kind == "component" else "Regen (defines RegenSystem)"
            layout.label(text=f"PascalCase, e.g. {example}", icon="INFO")
            return
        try:
            info = scaffold.plan(project.project_root(), self.kind, self.name, self.query())
        except scaffold.ScaffoldError as exc:
            layout.label(text=str(exc), icon="ERROR")
            return
        layout.label(text=f"Creates {info['rel']} ({info['name']})", icon="FILE_NEW")

    def draw_options(self, layout):
        pass

    def done_hint(self) -> str:
        return ""

    def execute(self, context):
        root = project.project_root()
        try:
            info = scaffold.create(root, self.kind, self.name, self.query())
        except scaffold.ScaffoldError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        tag_redraw()
        if info["warning"]:
            self.report({"WARNING"}, info["warning"])
        if self.open_editor:
            try:
                code_editor.open_location(info["path"], info["line"], 1, project=root)
            except code_editor.CodeEditorError as exc:
                self.report({"WARNING"}, f"Created {info['rel']}, but could not open it: {exc}")
                return {"FINISHED"}
        self.report({"INFO"}, f"Created {info['rel']}. {self.done_hint()}".strip())
        return {"FINISHED"}


class GAMESTUDIO_OT_new_component(_NewScript, bpy.types.Operator):
    bl_idname = "gamestudio.new_component"
    bl_label = "New Component"
    bl_description = ("Create a component (data you attach to objects) in src/components from a commented "
                      "template, and open it in the code editor")
    kind = "component"

    def done_hint(self) -> str:
        if link.get_link().connected:
            return "It appears in Add Component when the dev server has read it"
        return "Start the dev server to add it to Add Component"


class GAMESTUDIO_OT_new_system(_NewScript, bpy.types.Operator):
    bl_idname = "gamestudio.new_system"
    bl_label = "New System"
    bl_description = ("Create a system (game logic that runs every frame) in src/systems from a commented "
                      "template, and open it in the code editor")
    kind = "system"

    component: EnumProperty(name="Query", description="The component whose entities the system updates",
                            items=_query_component_items)

    def query(self) -> str:
        return "" if self.component in ("", "NONE") else self.component

    def draw_options(self, layout):
        layout.prop(self, "component")

    def done_hint(self) -> str:
        return "The running game picks it up when the dev server reloads it"


class GAMESTUDIO_OT_clear_logs(bpy.types.Operator):
    bl_idname = "gamestudio.clear_logs"
    bl_label = "Clear Console"
    bl_description = "Clear the game messages shown in the Console"

    def execute(self, _context):
        link.get_link().logs.clear()
        tag_redraw()
        return {"FINISHED"}


_CLASSES = (
    GAMESTUDIO_OT_add_component,
    GAMESTUDIO_OT_remove_component,
    GAMESTUDIO_OT_toggle_component_enabled,
    GAMESTUDIO_OT_reload_registry,
    GAMESTUDIO_OT_assign_ids,
    GAMESTUDIO_OT_export_scene,
    GAMESTUDIO_OT_play,
    GAMESTUDIO_OT_stop,
    GAMESTUDIO_OT_pause,
    GAMESTUDIO_OT_step,
    GAMESTUDIO_OT_start_server,
    GAMESTUDIO_OT_stop_server,
    GAMESTUDIO_OT_open_game,
    GAMESTUDIO_OT_open_path,
    GAMESTUDIO_OT_open_code_project,
    GAMESTUDIO_OT_open_latest_error,
    GAMESTUDIO_OT_new_component,
    GAMESTUDIO_OT_new_system,
    GAMESTUDIO_OT_clear_logs,
)


def register():
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    if not bpy.app.timers.is_registered(play._timer):
        bpy.app.timers.register(play._timer, first_interval=0.5, persistent=True)


def unregister():
    if bpy.app.timers.is_registered(play._timer):
        bpy.app.timers.unregister(play._timer)
    play.cancel()
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
