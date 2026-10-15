# SPDX-License-Identifier: GPL-3.0-or-later
"""The game UI shared by every host: the Game panel of Object properties, the status bar and
the optional 3D View sidebar tab "Game".

Object properties has the Game panel: the entity ID, Add Component and the
components (like the modifier stack), with a Bake Materials sub-panel for
procedural materials. In UI files (docs/GAME-UI.md) it also has the element's
Layout box (gameui/panels.py), and Add Component lists the UI components
first, the others under More. The sidebar's Game tab (play controls with the levels and
Export All Levels, the Inspector, the project and the console) shows only with
the "Game tab in the 3D View sidebar" preference (prefs.sidebar_enabled()).

Panel bodies are module-level `draw_*(layout, context)` functions, so other
places draw the same UI: the custom editors in editors/ (Inspector, Console,
Project), Scene properties and the top bar. The stable entry points are
draw_play, draw_levels, draw_project, draw_console, draw_entity_panel,
draw_components_panel, draw_object_game and draw_object_materials.

Also here: the Prefab checkbox in Collection properties, the Game panel of
Material properties (the material's game shader, draw_material_shader, with
the Game Shader menu: the project's shaders, then the shader library by
category), the clip menu of Animator.clip, the export report (size,
warnings) and the material bake status.

Draw code only reads state; it must work (and show hints instead of errors)
with no project, no registry and no server.
"""

import os
import pathlib
import time
import urllib.parse

import bpy
from bpy.props import BoolProperty, StringProperty

from . import prefs
from .core import (bake, components, devserver, export, fidelity, ids, link, live, locations, play, project, registry,
                   shaders)
from .core.util import any_window, refresh_status_bar, tag_redraw
from .ops import NO_PROJECT, component_category

CONSOLE_LINES = 10
_LEVEL_ICONS = {"error": "CANCEL", "warn": "ERROR", "warning": "ERROR", "info": "INFO", "debug": "DOT"}
# Console filters: the levels each one shows (None: everything).
LEVEL_FILTERS = {"ALL": None, "WARN": {"warn", "warning", "error"}, "ERROR": {"error"}}

ENTITY_HINT = "Select an object to edit its components"
# Component icons by registry category; components without one use DEFAULT_COMPONENT_ICON.
CATEGORY_ICONS = {
    "physics": "PHYSICS",
    "camera": "CAMERA_DATA",
    "audio": "SPEAKER",
    "rendering": "LIGHT",
    "lighting": "LIGHT",
    "animation": "ANIM",
    "gameplay": "CON_ACTION",
    "ui": "WINDOW",
}
DEFAULT_COMPONENT_ICON = "SCRIPT"
# String fields that name one of the entity's animation clips: they get a clip menu.
CLIP_FIELDS = {("Animator", "clip")}
EXPORT_WARNINGS_SHOWN = 8


def _wrap_width() -> int:
    region = getattr(bpy.context, "region", None)
    if region is None or region.width <= 1:
        return 40
    scale = bpy.context.preferences.system.ui_scale or 1.0
    return max(16, int(region.width / (7.0 * scale)) - 6)


def wrap_label(layout, text: str, icon: str = "NONE", width: int | None = None) -> None:
    """Draw text over several labels (the sidebar does not wrap)."""
    width = width or _wrap_width()
    words = text.split()
    lines, current = [], ""
    for word in words:
        if current and len(current) + 1 + len(word) > width:
            lines.append(current)
            current = word
        else:
            current = f"{current} {word}".strip()
    if current:
        lines.append(current)
    col = layout.column(align=True)
    for i, line in enumerate(lines or [""]):
        # Indent continuation lines under the text only when the first line has an icon.
        col.label(text=line, icon=icon if i == 0 else ("BLANK1" if icon != "NONE" else "NONE"))


def hint(layout, text: str, icon: str = "NONE") -> None:
    """A wrapped, dimmed line of help text."""
    col = layout.column()
    col.active = False
    wrap_label(col, text, icon=icon)


def ago(timestamp: float) -> str:
    seconds = max(0, int(time.time() - timestamp))
    if seconds < 60:
        return f"{seconds}s ago"
    if seconds < 3600:
        return f"{seconds // 60}m ago"
    return time.strftime("%H:%M", time.localtime(timestamp))


def plural(count: int, word: str) -> str:
    """'1 entity', '8 entities', '3 games'. A consonant and y becomes ies (entity, body)."""
    if count == 1:
        return f"{count} {word}"
    if len(word) > 1 and word.endswith("y") and word[-2] not in "aeiou":
        return f"{count} {word[:-1]}ies"
    return f"{count} {word}s"


class _GamePanel:
    """A panel of the 3D View sidebar's Game tab, shown only with the sidebar_tab preference."""

    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Game"

    @classmethod
    def poll(cls, context):
        return prefs.sidebar_enabled()


# --- Project & Server -----------------------------------------------------------------


def draw_no_project(layout) -> None:
    if not bpy.data.filepath:
        wrap_label(layout, "Save this .blend inside a GameStudio project (a folder with gamestudio.json) "
                           "to export and play it.", icon="INFO")
    else:
        wrap_label(layout, "This .blend is not inside a GameStudio project: no gamestudio.json "
                           "in its folder or any parent folder.", icon="ERROR")


def draw_project(layout, context) -> None:
    root = project.project_root()
    if root is None:
        draw_no_project(layout)
        return

    config = project.read_config(root)
    box = layout.box()
    box.label(text=config.get("name") or root.name, icon="FILE_FOLDER")
    hint(box, str(root))
    name = export.export_name(context.scene)
    start_scene = config.get("startScene")
    box.label(text=f"Scene exports as '{name}'", icon="SCENE_DATA")
    if start_scene and start_scene != name:
        wrap_label(box, f"The game starts with '{start_scene}'. Play loads this scene directly.", icon="INFO")
    last = export.last_export()
    if last:
        box.label(text=f"Last export: rev {last['rev']}, {plural(last['entities'], 'entity')}, {ago(last['time'])}",
                  icon="EXPORT")
        draw_export_report(box, last)

    draw_server(layout, root)
    draw_registry(layout, root)


def draw_server(layout, root) -> None:
    from .environment import draw_missing_packages

    client = link.get_link()
    box = layout.box()
    draw_missing_packages(box, root)
    if client.connected:
        url = (client.session or {}).get("url") or "?"
        box.label(text="Server running", icon="CHECKMARK")
        box.label(text=url, icon="URL")
        games = len(client.game_peers())
        box.label(text=f"{plural(games, 'game')} connected", icon="LINKED" if games else "UNLINKED")
    elif devserver.is_starting():
        box.label(text="Starting dev server...", icon="SORTTIME")
    elif client.state in ("connecting", "handshake"):
        box.label(text="Connecting to the dev server...", icon="SORTTIME")
    elif project.session_path(root).exists():
        box.label(text="Dev server found; reconnecting...", icon="SORTTIME")
    else:
        box.label(text="Dev server stopped", icon="RADIOBUT_OFF")
    if not client.connected:
        if client.last_error:
            wrap_label(box, client.last_error, icon="ERROR")
        if devserver.last_error():
            wrap_label(box, devserver.last_error().splitlines()[0], icon="ERROR")

    row = box.row(align=True)
    if not client.connected and not devserver.is_starting():
        row.operator("gamestudio.start_server", text="Start", icon="PLAY")
    if devserver.started_by_us():
        row.operator("gamestudio.stop_server", text="Stop", icon="SNAP_FACE")
    sub = row.row(align=True)
    sub.enabled = client.connected
    sub.operator("gamestudio.open_game", text="Open Game", icon="WINDOW")


def draw_registry(layout, root) -> None:
    box = layout.box()
    count = len(registry.component_names())
    row = box.row()
    if count:
        row.label(text=f"{plural(count, 'component')} in the registry", icon="PROPERTIES")
    elif project.registry_path(root).exists():
        row.label(text="Registry has no components", icon="ERROR")
    else:
        row.label(text="No registry yet", icon="INFO")
    row.operator("gamestudio.reload_registry", text="", icon="FILE_REFRESH")
    problems = registry.warnings()
    if problems:
        wrap_label(box, f"{len(problems)} registry problem(s): {problems[0]}", icon="ERROR")
    if not count and not project.registry_path(root).exists():
        hint(box, "Start the dev server; it writes .gamestudio/components.json.")


class GAMESTUDIO_PT_project(_GamePanel, bpy.types.Panel):
    bl_label = "Project"
    bl_idname = "GAMESTUDIO_PT_project"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        draw_project(self.layout, context)


# --- Play -----------------------------------------------------------------------------


def draw_play(layout, context) -> None:
    client = link.get_link()
    connected = client.connected and bool(client.game_peers())
    state = client.game_state if connected else None

    row = layout.row(align=True)
    row.scale_y = 1.5
    row.operator("gamestudio.play", text="Play", icon="PLAY", depress=state == "playing")
    sub = row.row(align=True)
    sub.enabled = connected
    paused = state == "paused"
    sub.operator("gamestudio.pause", text="Resume" if paused else "Pause", icon="PLAY" if paused else "PAUSE",
                 depress=paused)
    sub.operator("gamestudio.step", text="", icon="FRAME_NEXT")
    sub.operator("gamestudio.stop", text="", icon="SNAP_FACE")

    text, icon, alert = game_status()
    row = layout.row()
    row.alert = alert
    row.label(text=text, icon=icon)
    if client.stats and connected:
        stats = client.stats
        sub = row.row()
        sub.alignment = "RIGHT"
        sub.active = False
        sub.label(text=f"{stats.get('fps', 0):.0f} fps")

    scene_props = getattr(context.scene, "gs", None)
    if scene_props is not None:
        row = layout.row(align=True)
        row.prop(scene_props, "live_enabled", icon="LINKED" if scene_props.live_enabled else "UNLINKED")
        row.operator("gamestudio.export_scene", text="", icon="EXPORT")
        if live.is_active():
            hint(layout, "Streaming your edits to the game")
    if client.stats and connected:
        stats = client.stats
        hint(layout, f"{plural(stats.get('entities', 0), 'entity')}, {plural(stats.get('bodies', 0), 'body')}")
    last = export.last_export()
    if last.get("warnings"):
        header, body = layout.panel("GAMESTUDIO_export_report", default_closed=True)
        row = header.row()
        row.alert = True
        row.label(text=f"Export: {plural(len(last['warnings']), 'warning')}", icon="ERROR")
        if body is not None:
            draw_export_report(body, last)
    draw_levels(layout, context)


# The last Export All Levels: {"scenes": count, "errors": [{file, error}], "time"}, or None.
_levels_report = None


def draw_levels(layout, context, *, default_closed: bool = True) -> None:
    """The project's level files (scenes/*.blend) with Open buttons, and Export All Levels."""
    root = project.project_root()
    if root is None:
        return
    files = export.level_files(root)
    header, body = layout.panel("GAMESTUDIO_levels", default_closed=default_closed)
    header.label(text=f"Levels ({len(files)})", icon="SCENE_DATA")
    header.operator("gamestudio.export_all_levels", text="Export All", icon="EXPORT")
    if body is None:
        return
    report = _levels_report
    failed = {error.get("file") for error in report["errors"]} if report else set()
    current = pathlib.Path(bpy.data.filepath).resolve() if bpy.data.filepath else None
    start = project.read_config(root).get("startScene")
    col = body.column(align=True)
    col.operator_context = "INVOKE_DEFAULT"  # asks to save unsaved changes first
    for path in files:
        is_current = current is not None and path.resolve() == current
        row = col.row(align=True)
        label = path.stem + ("  (start)" if path.stem == start else "")
        row.alert = f"{export.SCENES_DIR}/{path.name}" in failed
        row.label(text=label, icon="CHECKMARK" if is_current else "FILE_BLEND")
        sub = row.row(align=True)
        sub.enabled = not is_current
        op = sub.operator("wm.open_mainfile", text="Open", icon="FILE_FOLDER")
        op.filepath = str(path)
        op.display_file_selector = False
    if not files:
        hint(body, f"No level files in {export.SCENES_DIR}/ yet.")
    if report:
        body.label(text=f"Exported {plural(report['scenes'], 'level')}, {ago(report['time'])}",
                   icon="ERROR" if report["errors"] else "CHECKMARK")
        for error in report["errors"][:EXPORT_WARNINGS_SHOWN]:
            row = body.row()
            row.alert = True
            wrap_label(row, f"{error.get('file')}: {error.get('error')}", icon="CANCEL")


class GAMESTUDIO_OT_export_all_levels(bpy.types.Operator):
    bl_idname = "gamestudio.export_all_levels"
    bl_label = "Export All Levels"
    bl_description = ("Export every level of the project: the scenes of this file, then every other "
                      "scenes/*.blend in a background Blender, so a game that changes level finds them all")

    @classmethod
    def poll(cls, context):
        return project.project_root() is not None

    def execute(self, context):
        global _levels_report
        window = getattr(context, "window", None)
        if window is not None:
            window.cursor_set("WAIT")
        try:
            result = export.export_all()
        except export.ExportError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        finally:
            if window is not None:
                window.cursor_set("DEFAULT")
        scenes, errors = result["scenes"], result["errors"]
        _levels_report = {"scenes": len(scenes), "errors": errors, "time": time.time()}
        if errors:
            self.report({"WARNING"}, f"Exported {plural(len(scenes), 'level')}; {len(errors)} failed: "
                                     f"{errors[0].get('file')}: {errors[0].get('error')}")
        else:
            self.report({"INFO"}, f"Exported {plural(len(scenes), 'level')}: "
                                  f"{', '.join(s['scene'] for s in scenes)}")
        tag_redraw()
        return {"FINISHED"}


def export_summary(last: dict) -> str:
    """"1.2 MB, 40k triangles, 12 draw calls" for an export result."""
    size = last.get("size") or {}
    if not size:
        return ""
    parts = [fidelity.format_bytes(size.get("bytes", 0))]
    triangles = size.get("triangles", 0)
    parts.append(f"{triangles / 1000:.0f}k triangles" if triangles >= 10000 else plural(triangles, "triangle"))
    parts.append(plural(size.get("draw_calls", 0), "draw call"))
    return ", ".join(parts)


def draw_export_report(layout, last: dict) -> None:
    """Size, largest items, bakes and warnings of an export result (export.last_export())."""
    summary = export_summary(last)
    if summary:
        layout.label(text=summary, icon="DISK_DRIVE")
    top = (last.get("size") or {}).get("top", [])
    if top:
        col = layout.column(align=True)
        col.active = False
        for item in top[:3]:
            col.label(text=f"{fidelity.format_bytes(item['bytes'])}  {item['kind']} {item['name']}  {item['detail']}",
                      icon="BLANK1")
    baked = last.get("bake") or {}
    if baked.get("units"):
        layout.label(text=f"{plural(len(baked['units']), 'object')} with baked materials "
                          f"({baked.get('cached', 0)} from the cache)", icon="NODE_TEXTURE")
    warnings = last.get("warnings") or []
    for message in warnings[:EXPORT_WARNINGS_SHOWN]:
        wrap_label(layout, message, icon="ERROR")
    if len(warnings) > EXPORT_WARNINGS_SHOWN:
        hint(layout, f"{len(warnings) - EXPORT_WARNINGS_SHOWN} more in the system console")


class GAMESTUDIO_PT_play(_GamePanel, bpy.types.Panel):
    bl_label = "Play"
    bl_idname = "GAMESTUDIO_PT_play"

    def draw(self, context):
        draw_play(self.layout, context)


# --- Inspector: the entity and its components -----------------------------------------


def object_icon(obj) -> str:
    return f"OUTLINER_OB_{obj.type.replace('_', '')}" if obj is not None else "OBJECT_DATA"


def component_icon(name: str) -> str:
    entry = registry.schema_ref(name)
    if entry is None:
        return "ERROR"
    return CATEGORY_ICONS.get(component_category(entry).lower(), DEFAULT_COMPONENT_ICON)


UI_CATEGORY = "UI"  # the game UI components (docs/GAME-UI.md §2.3): first in UI files, the rest under More


def _component_groups(context) -> tuple[dict, list, set]:
    """({category: names}, categories in menu order, names attached to the active object)."""
    obj = getattr(context, "active_object", None)
    attached = set(components.list_components(obj)) if obj is not None else set()
    groups = {}
    for name in registry.component_names():
        groups.setdefault(component_category(registry.schema_ref(name)), []).append(name)
    return groups, sorted(groups, key=lambda c: (c == "Other", c)), attached


def _ui_file_menu(groups) -> bool:
    from .gameui.build import in_ui_file

    return UI_CATEGORY in groups and len(groups) > 1 and in_ui_file()


def _draw_component_categories(layout, groups, categories, attached) -> None:
    for category in categories:
        layout.separator()
        layout.label(text=category)
        for name in groups[category]:
            row = layout.row()
            row.enabled = name not in attached
            row.operator("gamestudio.add_component", text=name, icon=component_icon(name)).component = name


class GAMESTUDIO_MT_add_component(bpy.types.Menu):
    bl_label = "Add Component"
    bl_idname = "GAMESTUDIO_MT_add_component"

    def draw(self, context):
        layout = self.layout
        names = registry.component_names()
        if not names:
            layout.label(text="The registry is empty", icon="INFO")
            layout.operator("gamestudio.reload_registry", icon="FILE_REFRESH")
            _draw_new_component(layout)
            return
        groups, categories, attached = _component_groups(context)
        layout.operator_context = "INVOKE_DEFAULT"
        layout.operator("gamestudio.add_component", text="Search...", icon="VIEWZOOM")
        if _ui_file_menu(groups):
            _draw_component_categories(layout, groups, [UI_CATEGORY], attached)
            layout.separator()
            layout.menu("GAMESTUDIO_MT_add_component_more", text="More", icon="THREE_DOTS")
        else:
            _draw_component_categories(layout, groups, categories, attached)
        _draw_new_component(layout)


class GAMESTUDIO_MT_add_component_more(bpy.types.Menu):
    """In UI files, the Add Component menu's other categories."""

    bl_label = "More Components"
    bl_idname = "GAMESTUDIO_MT_add_component_more"

    def draw(self, context):
        layout = self.layout
        layout.operator_context = "INVOKE_DEFAULT"
        groups, categories, attached = _component_groups(context)
        _draw_component_categories(layout, groups, [c for c in categories if c != UI_CATEGORY], attached)


def _draw_new_component(layout) -> None:
    """Write a new component script (the Project editor's New Component)."""
    layout.separator()
    layout.operator_context = "INVOKE_DEFAULT"
    layout.operator("gamestudio.new_component", text="New Component...", icon="FILE_NEW")


def draw_entity(layout, obj, editable_name: bool = False) -> bool:
    """The entity card: type icon, name and entity ID. Returns whether the object is writable."""
    writable = components.is_writable(obj)
    row = layout.row(align=True)
    if editable_name and writable:
        row.prop(obj, "name", text="", icon=object_icon(obj))
    else:
        row.label(text=obj.name, icon=object_icon(obj))
    draw_entity_id(layout, obj, writable)
    return writable


def draw_entity_id(layout, obj, writable: bool) -> None:
    """The entity ID, dimmed, and Read-only for linked or non-editable override data."""
    gs_id = ids.get_id(obj)
    row = layout.row()
    row.active = False
    row.label(text=f"Entity ID {gs_id}" if gs_id else "Entity ID: assigned on export", icon="COPY_ID")
    if not writable:
        row.label(text="Read-only", icon="LOCKED")


def draw_transform(layout, obj) -> None:
    header, body = layout.panel("GAMESTUDIO_transform", default_closed=True)
    header.label(text="Transform", icon="ORIENTATION_GLOBAL")
    if body is None:
        return
    col = body.column()
    col.use_property_split = True
    col.use_property_decorate = False
    col.enabled = components.is_writable(obj)
    col.prop(obj, "location")
    col.prop(obj, "rotation_euler", text="Rotation")
    col.prop(obj, "scale")


def draw_entity_panel(layout, context, editable_name: bool = True) -> None:
    """The Inspector's Entity panel, in the Inspector editor and the Game tab."""
    obj = getattr(context, "active_object", None)
    if obj is None:
        layout.label(text="No entity selected", icon="RESTRICT_SELECT_OFF")
        hint(layout, "Select an object in the viewport or the outliner.")
        return
    draw_entity(layout, obj, editable_name=editable_name)
    draw_transform(layout, obj)
    draw_material_status(layout, obj)


def draw_material_status(layout, obj) -> None:
    """What export does with the object's procedural materials (nothing shows for glTF-safe ones)."""
    found = bake.object_status(obj)
    if not found:
        return
    mode = export.export_settings(project.project_root())["bake"]
    problems = [w for w in export.last_export().get("warnings", []) if f"on '{obj.name}'" in w]
    col = layout.column(align=True)
    for _index, mat, info in found:
        if mode == "off":
            text, icon = f"{mat.name}: procedural, exports as flat colours (export.bake is off)", "ERROR"
        elif any(f"'{mat.name}'" in p for p in problems):
            text, icon = f"{mat.name}: procedural, could not be baked (see the export warnings)", "ERROR"
        else:
            text, icon = f"{mat.name}: procedural, baked to textures on export", "NODE_TEXTURE"
        wrap_label(col, text, icon=icon)
        for note in info["notes"]:
            hint(col, note)
    row = layout.row()
    row.enabled = project.project_root() is not None
    row.operator("gamestudio.bake_materials", text="Bake Materials Now", icon="RENDER_STILL")


def draw_component_list(layout, obj, writable: bool) -> None:
    refs = list(obj.gs.components)
    if not refs:
        layout.label(text="No components yet", icon="INFO")
        hint(layout, "Components give this entity data and behavior in the game.")
    for ref in refs:
        draw_component(layout, obj, ref, writable)


def draw_add_component(layout, writable: bool) -> None:
    if not registry.component_names():
        if project.project_root() is None:
            return  # no project: no components to add
        from .environment import setting_up

        if setting_up():
            hint(layout, "Setting up GameStudio: the components appear when it finishes.", icon="SORTTIME")
            return
        from .core import trust

        if not trust.is_trusted(project.project_root()):  # reading the list runs the project's script
            hint(layout, "GameStudio reads the project's component list once you trust the project.", icon="LOCKED")
            layout.operator("gamestudio.trust_project", text="Trust This Project...", icon="LOCKED")
            return
        hint(layout, "The project's component list is not read yet. GameStudio reads it when the project opens "
                     "and when the game runs.", icon="INFO")
        layout.operator("gamestudio.reload_registry", icon="FILE_REFRESH")
        return
    row = layout.row(align=True)
    row.scale_y = 1.2
    row.enabled = writable
    row.menu("GAMESTUDIO_MT_add_component", text="Add Component", icon="ADD")
    row.operator("gamestudio.add_component", text="", icon="VIEWZOOM")


def draw_component(layout, obj, ref, writable: bool) -> None:
    entry = registry.schema_ref(ref.name)
    header, body = layout.panel(f"GAMESTUDIO_comp_{ref.name}", default_closed=False)
    header.prop(ref, "enabled", text="")
    header.label(text=ref.name, icon=component_icon(ref.name))
    if writable:
        op = header.operator("gamestudio.remove_component", text="", icon="X", emboss=False)
        op.component = ref.name
    if body is None:
        return
    col = body.column()
    col.active = ref.enabled
    col.enabled = writable
    if entry is None:
        from .environment import setting_up

        if not registry.component_names():  # nothing read yet (a project just opened, or set up)
            wrap_label(col, "Setting up GameStudio: the component's fields appear when it finishes." if setting_up()
                       else "The project's component list is not read yet: its values are kept.", icon="INFO")
            return
        wrap_label(col, f"'{ref.name}' is not in the registry. Its values are kept until the "
                        "definition returns.", icon="ERROR")
        return
    if entry.get("description"):
        hint(col, entry["description"])
    grp = registry.group(obj, ref.name)
    if grp is None or not entry["fields"]:
        hint(col, "No fields")
        return
    col.use_property_split = True
    col.use_property_decorate = False
    for field in entry["fields"]:
        if (ref.name, field["name"]) in CLIP_FIELDS:
            draw_clip_field(col, obj, grp, field["name"])
        else:
            col.prop(grp, field["name"])


def draw_clip_field(layout, obj, grp, name: str) -> None:
    """A clip name with a menu of the entity's actions and NLA clips."""
    row = layout.row(align=True)
    row.prop(grp, name)
    row.menu("GAMESTUDIO_MT_animator_clips", text="", icon="DOWNARROW_HLT")
    value = getattr(grp, name, "")
    if value and value not in export.object_clips(obj):
        hint(layout, f"'{value}' is not an action or NLA clip of {obj.name} or its children", icon="ERROR")


class GAMESTUDIO_MT_animator_clips(bpy.types.Menu):
    bl_label = "Clips"
    bl_idname = "GAMESTUDIO_MT_animator_clips"

    def draw(self, context):
        layout = self.layout
        obj = getattr(context, "active_object", None)
        clips = export.object_clips(obj)
        if not clips:
            layout.label(text="No actions on this object or its children", icon="INFO")
            return
        for clip in clips:
            layout.operator("gamestudio.set_animator_clip", text=clip, icon="ACTION").clip = clip


class GAMESTUDIO_OT_set_animator_clip(bpy.types.Operator):
    bl_idname = "gamestudio.set_animator_clip"
    bl_label = "Set Animator Clip"
    bl_description = "Play this clip with the active object's Animator"
    bl_options = {"REGISTER", "UNDO"}

    clip: StringProperty(name="Clip", options={"SKIP_SAVE"})

    def execute(self, context):
        obj = context.active_object
        if obj is None or not components.has_component(obj, "Animator"):
            self.report({"ERROR"}, "The active object has no Animator component")
            return {"CANCELLED"}
        try:
            components.set_field(obj, "Animator", "clip", self.clip)
        except ValueError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        return {"FINISHED"}


class GAMESTUDIO_OT_bake_materials(bpy.types.Operator):
    bl_idname = "gamestudio.bake_materials"
    bl_label = "Bake Materials"
    bl_description = ("Bake the procedural materials of the scene (or of the selected objects) to textures with "
                      "Cycles, into the project's bake cache; exports then use them")

    selected_only: BoolProperty(name="Selected Only", default=False, options={"SKIP_SAVE"})
    force: BoolProperty(name="Rebake", description="Bake again even when the cache has a bake", default=False,
                        options={"SKIP_SAVE"})

    def execute(self, context):
        root = project.project_root()
        if root is None:
            self.report({"ERROR"}, NO_PROJECT)
            return {"CANCELLED"}
        scene = context.scene
        objects = list(context.selected_objects) if self.selected_only else list(scene.objects)
        if not self.selected_only:
            objects += [o for coll in export.prefab_collections() for o in coll.all_objects]
        try:
            result = bake.bake_objects(objects, scene, cache_dir=project.gs_dir(root) / export.BAKE_DIR,
                                       settings=export.export_settings(root), force=self.force,
                                       window=getattr(context, "window", None) or any_window())
        except bake.BakeError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        if result["problems"]:
            self.report({"WARNING"}, result["problems"][0])
        if not result["units"] and not result["problems"]:
            self.report({"INFO"}, "No procedural materials to bake: every material exports as it is")
            return {"FINISHED"}
        self.report({"INFO"}, f"Baked {plural(result['baked'], 'object')} ({result['cached']} already in the cache) "
                              f"in {result['seconds']:.1f} s")
        return {"FINISHED"}


# --- Collection properties: Prefab ------------------------------------------------------


def _get_prefab(self) -> bool:
    return export.is_prefab(self)


def _set_prefab(self, value) -> None:
    export.set_prefab(self, bool(value))
    live.schedule_rebuild("a collection was marked or unmarked as a prefab")


def prefab_problems(coll) -> list:
    """Members a prefab's template leaves out: parented outside the collection, or disabled in viewports."""
    members = set(coll.all_objects)
    problems = []
    for obj in coll.all_objects:
        if obj.parent is not None and obj.parent not in members:
            problems.append(f"'{obj.name}' is parented to '{obj.parent.name}', outside the collection")
        elif obj.hide_viewport:
            problems.append(f"'{obj.name}' is disabled in viewports")
    return problems


def draw_collection_prefab(layout, coll) -> None:
    row = layout.row()
    row.enabled = coll.library is None and coll.override_library is None
    row.prop(coll, "gs_is_prefab", text="Prefab", icon="PACKAGE")
    if coll.library is not None:
        hint(layout, f"Linked from {coll.library.name}: mark it as a prefab in its own file.", icon="LINKED")
    if not export.is_prefab(coll):
        hint(layout, "A prefab is a template the game can place (collection instances) and spawn from code "
                     f"(ctx.spawn('{coll.name}')).")
        return
    count = len(coll.all_objects)
    instances = sum(1 for o in bpy.data.objects if export.instanced_prefab(o) is coll)
    layout.label(text=f"{plural(count, 'object')}, {plural(instances, 'instance')} in this file",
                 icon="OUTLINER_OB_GROUP_INSTANCE")
    for problem in prefab_problems(coll)[:5]:
        wrap_label(layout, f"{problem}: not in the template", icon="ERROR")
    if count == 0:
        wrap_label(layout, "The collection is empty: nothing to spawn.", icon="ERROR")


class GAMESTUDIO_PT_collection(bpy.types.Panel):
    bl_label = "Game"
    bl_idname = "GAMESTUDIO_PT_collection"
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "collection"

    @classmethod
    def poll(cls, context):
        coll = getattr(context, "collection", None)
        return coll is not None and coll != getattr(context.scene, "collection", None)

    def draw(self, context):
        draw_collection_prefab(self.layout, context.collection)


def draw_components_panel(layout, context) -> None:
    """The Inspector's Components panel, in the Inspector editor and the Game tab."""
    obj = getattr(context, "active_object", None)
    if obj is None:
        hint(layout, ENTITY_HINT)
        return
    writable = components.is_writable(obj)
    if not writable:
        wrap_label(layout, "Linked or non-editable override data is read-only.", icon="LOCKED")
    draw_component_list(layout, obj, writable)
    layout.separator()
    draw_add_component(layout, writable)


def draw_components(layout, context) -> None:
    """The whole component editor in one layout: entity card, components and Add Component."""
    obj = context.active_object
    if obj is None:
        wrap_label(layout, ENTITY_HINT, icon="INFO")
        return
    draw_entity(layout, obj)
    draw_components_panel(layout, context)


def _has_object(context) -> bool:
    return getattr(context, "active_object", None) is not None


class GAMESTUDIO_PT_entity(_GamePanel, bpy.types.Panel):
    bl_label = "Entity"
    bl_idname = "GAMESTUDIO_PT_entity"

    def draw(self, context):
        draw_entity_panel(self.layout, context)


class GAMESTUDIO_PT_components(_GamePanel, bpy.types.Panel):
    bl_label = "Components"
    bl_idname = "GAMESTUDIO_PT_components"

    def draw(self, context):
        draw_components_panel(self.layout, context)


# --- Object properties: the Game panel ------------------------------------------------


def properties_object(context):
    """The object Object properties shows: the pinned one, else the active one."""
    obj = getattr(context, "object", None)
    return obj if obj is not None else getattr(context, "active_object", None)


def draw_object_game(layout, context) -> None:
    """The Game panel of Object properties: the entity ID, Add Component, then each component
    as a collapsible row, like the modifier stack. Blender's own panels show the name and transform."""
    obj = properties_object(context)
    if obj is None:
        hint(layout, ENTITY_HINT)
        return
    if obj != getattr(context, "active_object", None):
        # Pinned: the component operators and menus act on context.active_object.
        layout.context_pointer_set("active_object", obj)
    writable = components.is_writable(obj)
    draw_entity_id(layout, obj, writable)
    if not writable:
        wrap_label(layout, "Linked or non-editable override data is read-only.", icon="LOCKED")
    from .gameui import panels as gameui_panels

    gameui_panels.draw_layout(layout, context, obj)  # UI files: the element's px position, size and anchor
    draw_add_component(layout, writable)
    draw_component_list(layout, obj, writable)


def draw_object_materials(layout, context) -> None:
    """The Bake Materials sub-panel: what export does with the object's procedural materials."""
    obj = properties_object(context)
    if obj is not None:
        draw_material_status(layout, obj)


class _ObjectPanel:
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "object"

    @classmethod
    def poll(cls, context):
        return properties_object(context) is not None


class GAMESTUDIO_PT_object(_ObjectPanel, bpy.types.Panel):
    bl_label = "Game"
    bl_idname = "GAMESTUDIO_PT_object"
    # bl_order is unsigned and Blender's object panels are 0, so the earliest place is after the
    # order-0 group: below Transform and the closed Relations ... Visibility panels, and above
    # Viewport Display and Line Art (10) and Custom Properties (1000).
    bl_order = 0

    def draw(self, context):
        draw_object_game(self.layout, context)


class GAMESTUDIO_PT_object_materials(_ObjectPanel, bpy.types.Panel):
    bl_label = "Bake Materials"
    bl_idname = "GAMESTUDIO_PT_object_materials"
    bl_parent_id = "GAMESTUDIO_PT_object"

    @classmethod
    def poll(cls, context):
        obj = properties_object(context)
        return obj is not None and bool(bake.object_status(obj))

    def draw(self, context):
        draw_object_materials(self.layout, context)


# --- Material properties: the Game panel (the material's game shader) -------------------


SHADER_MENU = "GAMESTUDIO_MT_material_shader"


def shader_category_menu(category: str) -> str:
    """The Game Shader sub-menu that lists one library category (registry.SHADER_CATEGORIES, "Other")."""
    slug = "".join(ch if ch.isalnum() else " " for ch in category.lower()).split()
    return "GAMESTUDIO_MT_shader_library_" + "_".join(slug)


def _menu_material(context):
    mat = getattr(context, "material", None)
    return mat if isinstance(mat, bpy.types.Material) else None


def draw_shader_menu(layout, mat) -> None:
    """The Game Shader menu: None (and the stored shader when the registry lacks it), the project's own
    shaders, then the shader library with a sub-menu per category. Each item's tooltip is the shader's
    description; typing searches the menu and its sub-menus."""
    if mat is None:
        layout.label(text="No material", icon="INFO")
        return
    if layout.operator_context == "EXEC_REGION_WIN":  # opened as a menu, not drawn for a menu search
        layout.operator_context = "INVOKE_REGION_WIN"
        layout.operator("wm.search_single_menu", text="Search...", icon="VIEWZOOM").menu_idname = SHADER_MENU
        layout.separator()
    layout.prop_enum(mat.gs, "shader_menu", registry.NO_SHADER)
    if shaders.is_missing(mat):
        layout.prop_enum(mat.gs, "shader_menu", mat.gs.shader, icon="ERROR")  # menus draw radio icons otherwise
    project_names, library = registry.shader_groups()
    if project_names:
        layout.separator()
        layout.label(text="Project")
        for name in project_names:
            layout.prop_enum(mat.gs, "shader_menu", name)
    if library:
        layout.separator()
        layout.label(text="Library")
        layout.context_pointer_set("material", mat)  # for the sub-menus
        for category, _names in library:
            layout.menu(shader_category_menu(category), text=category,
                        icon=registry.SHADER_CATEGORY_ICONS[category])
    if not project_names and not library:
        layout.separator()
        layout.label(text="No game shaders in the registry", icon="INFO")


def draw_shader_category(layout, mat, category: str) -> None:
    """One library category's shaders, for its sub-menu of the Game Shader menu."""
    names = dict(registry.shader_groups()[1]).get(category, [])
    if mat is None or not names:
        layout.label(text="No material" if mat is None else "No shaders", icon="INFO")
        return
    for name in names:
        layout.prop_enum(mat.gs, "shader_menu", name)


class GAMESTUDIO_MT_material_shader(bpy.types.Menu):
    bl_label = "Game Shader"
    bl_idname = SHADER_MENU
    bl_options = {"SEARCH_ON_KEY_PRESS"}

    def draw(self, context):
        draw_shader_menu(self.layout, _menu_material(context))


def _category_menu_class(category: str):
    def draw(self, context):
        draw_shader_category(self.layout, _menu_material(context), category)

    idname = shader_category_menu(category)
    return type(idname, (bpy.types.Menu,), {"bl_idname": idname, "bl_label": category, "draw": draw,
                                            "__module__": __name__})


SHADER_CATEGORY_MENUS = tuple(_category_menu_class(c) for c in registry.SHADER_CATEGORIES + (registry.OTHER_CATEGORY,))


def draw_shader_selector(layout, mat, writable: bool) -> None:
    """The Game Shader row, laid out like a property: the shader as a menu button (with the error icon when
    the registry lacks it) and a button that searches the menu."""
    name = shaders.get_shader(mat)
    split = layout.split(factor=0.4)
    label = split.row()
    label.alignment = "RIGHT"
    label.label(text="Game Shader")
    row = split.row(align=True)
    row.enabled = writable
    row.context_pointer_set("material", mat)
    row.menu(SHADER_MENU, text=name or "None", icon=registry.shader_icon(name) if name else "NONE")
    row.operator("wm.search_single_menu", text="", icon="VIEWZOOM").menu_idname = SHADER_MENU


def draw_shader_origin(layout, entry: dict) -> None:
    """One line: whether the shader comes from the project (its file opens on click) or from the shader
    library, which ships with GameStudio."""
    source = entry.get("source")
    if not entry.get("builtin"):
        if source:
            layout.operator("gamestudio.open_path", text=f"From the project: {source}",
                            icon=registry.PROJECT_SHADER_ICON).filepath = source
        else:
            layout.label(text="From the project", icon=registry.PROJECT_SHADER_ICON)
        return
    text = f"From the shader library ({entry.get('category') or registry.OTHER_CATEGORY})"
    root = project.project_root()
    path = pathlib.Path(source) if source else None
    if path is not None and not path.is_absolute():
        path = root / path if root is not None else None
    if path is not None and path.is_file():
        layout.operator("gamestudio.open_path", text=f"{text}: {source}", icon="ASSET_MANAGER").filepath = str(path)
    else:
        layout.label(text=text, icon="ASSET_MANAGER")
    hint(layout, "Copy it into src/shaders to customise.")


def draw_material_shader(layout, mat) -> None:
    """The Game panel of Material properties: the game shader that draws the material in the game, its
    fields and where it comes from. The viewport keeps showing the material's nodes."""
    writable = shaders.is_writable(mat)
    name = shaders.get_shader(mat)
    draw_shader_selector(layout, mat, writable)
    if not writable:
        wrap_label(layout, "Linked or non-editable override material: read-only.", icon="LOCKED")
    if not registry.shader_names():
        hint(layout, "No game shaders in the registry yet. Start the dev server: it lists the shader library and "
                     "src/shaders/ in .gamestudio/components.json.", icon="INFO")
    if name is None:
        return
    entry = registry.shader_schema_ref(name)
    if entry is None:
        wrap_label(layout, f"'{name}' is not in the registry: no src/shaders file or library shader defines it. "
                           "Its values are kept until the definition returns.", icon="ERROR")
        return
    if entry.get("description"):
        hint(layout, entry["description"])
    grp = registry.shader_group(mat, name)
    if grp is not None and entry["fields"]:
        fields = layout.column()
        fields.use_property_split = True
        fields.use_property_decorate = False
        fields.enabled = writable
        for field in entry["fields"]:
            fields.prop(grp, field["name"])
    draw_shader_origin(layout, entry)
    hint(layout, f"In the game, {name} draws this material. The viewport shows its Blender nodes.")


class MATERIAL_PT_gamestudio_shader(bpy.types.Panel):
    bl_label = "Game"
    bl_idname = "MATERIAL_PT_gamestudio_shader"
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "material"

    @classmethod
    def poll(cls, context):
        return getattr(context, "material", None) is not None

    def draw(self, context):
        draw_material_shader(self.layout, context.material)


# --- Runtime console ------------------------------------------------------------------


def console_entries(level: str = "ALL") -> list:
    """Game log entries that pass the filter ("ALL", "WARN" or "ERROR"), oldest first."""
    levels = LEVEL_FILTERS.get(level)
    entries = list(link.get_link().logs)
    if levels is None:
        return entries
    return [e for e in entries if str(e.get("level", "info")).lower() in levels]


def source_path(location) -> pathlib.Path | None:
    """The project file a log location points at ("src/a.ts", a dev-server URL), if it exists."""
    root = project.project_root()
    if root is None or not location:
        return None
    text = str(location).split("?", 1)[0].split("#", 1)[0]
    if "://" in text:
        text = text.split("://", 1)[1].partition("/")[2]
    path = pathlib.Path(text)
    if not path.is_absolute():
        path = root / text.lstrip("/")
    return path if path.is_file() else None


def _location_text(entry) -> str:
    location = entry.get("file") or entry.get("source")
    if not location:
        return ""
    where = os.path.basename(str(location))
    if entry.get("line") is not None:
        where += f":{entry['line']}"
        if entry.get("col") is not None:
            where += f":{entry['col']}"
    return where


def draw_log_entry(col, entry, message_lines: int = 1, open_files: bool = False) -> None:
    level = str(entry.get("level", "info")).lower()
    message = str(entry.get("msg", "")).strip().splitlines() or [""]
    col.label(text=message[0][:160], icon=_LEVEL_ICONS.get(level, "INFO"))
    for extra in message[1:message_lines]:
        col.label(text=extra[:160], icon="BLANK1")
    where = _location_text(entry)
    if not where:
        return
    found = locations.resolve_entry(entry, project.project_root()) if open_files else []
    if not found:
        col.label(text=f"    at {where}", icon="BLANK1")
        return
    row = col.row(align=True)
    row.label(text=f"    at {where}", icon="BLANK1")
    op = row.operator("gamestudio.open_path", text="", icon="FILE_SCRIPT", emboss=False)
    op.filepath, op.line, op.column = str(found[0].path), found[0].line or 0, found[0].col or 0


def draw_console(layout, context, max_lines: int = CONSOLE_LINES, level: str = "ALL",
                 message_lines: int = 1, open_files: bool = False, footer: bool = True) -> None:
    """The game's log messages, newest last. The Console editor shows more, filtered."""
    client = link.get_link()
    entries = console_entries(level)
    if not entries:
        if client.logs:
            layout.label(text=f"No matching messages ({plural(len(client.logs), 'message')} in total).",
                         icon="FILTER")
        else:
            text = "No messages from the game yet." if client.connected else \
                "Messages from the running game appear here."
            wrap_label(layout, text, icon="CONSOLE")
            return
    shown = entries[-max_lines:] if max_lines > 0 else entries
    col = layout.column(align=True)
    hidden = len(entries) - len(shown)
    if hidden > 0:
        col.label(text=f"{plural(hidden, 'older message')} not shown", icon="THREE_DOTS")
    for entry in shown:
        draw_log_entry(col, entry, message_lines=message_lines, open_files=open_files)
    if footer:
        row = layout.row()
        row.label(text=plural(len(client.logs), "message"))
        row.operator("gamestudio.clear_logs", text="Clear", icon="TRASH")


class GAMESTUDIO_PT_console(_GamePanel, bpy.types.Panel):
    bl_label = "Console"
    bl_idname = "GAMESTUDIO_PT_console"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        draw_console(self.layout, context, open_files=True)


# --- Status bar: the game and the dev server, in every workspace ---------------------------


def _error_count() -> int:
    return sum(1 for e in link.get_link().logs if str(e.get("level", "")).lower() == "error")


def game_status() -> tuple[str, str, bool]:
    """(text, icon, alert) for the game: Stopped, Starting..., Playing, Paused, or what went wrong."""
    from . import host

    waiting = play.status()
    if waiting:
        # While Play is under way, what it waits for ("Starting the dev server...", "Exporting the
        # scene...", "Loading the game..."). play keeps its status text after giving up: a failure,
        # until the next Play.
        return (waiting, "SORTTIME", False) if play.is_pending() else (waiting, "ERROR", True)
    if host.game_view.available():
        docked = host.game_view.status()
        state = docked.get("state")
        if state in ("created", "waiting"):
            return "Starting...", "SORTTIME", False
        if state == "failed" or docked.get("page_state") in ("crashed", "error"):
            # A killed host is a crash too: the native overlay says so ("Game stopped (crashed)").
            crashed = docked.get("page_state") == "crashed" or "crashed" in docked.get("overlay", "")
            return "Game crashed" if crashed else "Game failed", "ERROR", True
    client = link.get_link()
    state = client.game_state if client.connected and client.game_peers() else None
    if state == "playing":
        return "Playing", "PLAY", False
    if state == "paused":
        return "Paused", "PAUSE", False
    if state == "loading":
        return "Loading...", "SORTTIME", False
    return "Stopped", "RADIOBUT_OFF", False


def server_status() -> tuple[str, str, bool]:
    """(text, icon, alert) for the project's dev server; ("", "NONE", False) without a project."""
    root = project.project_root()
    if root is None:
        return "", "NONE", False
    client = link.get_link()
    if client.connected:
        port = urllib.parse.urlsplit((client.session or {}).get("url") or "").port
        return (f"Dev server :{port}" if port else "Dev server running"), "LINKED", False
    if devserver.is_starting() or client.state in ("connecting", "handshake"):
        return "Dev server starting...", "SORTTIME", False
    if devserver.last_error() or client.last_error:
        return "Dev server failed", "ERROR", True
    return "Dev server stopped", "RADIOBUT_OFF", False


def status_items() -> list[tuple[str, str, bool]]:
    """What the status bar shows, left to right: a running setup (Node.js, packages) first."""
    from .environment import status_item

    setup = status_item()
    items = [setup] if setup is not None else []
    if project.project_root() is None:
        return items + [("No game project", "FILE_FOLDER", False)]
    items += [game_status(), server_status()]
    errors = _error_count()
    if errors:
        items.append((plural(errors, "error"), "CANCEL", True))
    return items


def draw_statusbar(self, context):
    from .environment import draw_statusbar_actions, status_item

    layout = self.layout
    setup = status_item()
    for item in status_items():
        text, icon, alert = item
        row = layout.row()
        row.alert = alert
        if not alert and icon in ("RADIOBUT_OFF", "FILE_FOLDER"):
            row.active = False  # idle states stay quiet
        row.label(text=text, icon=icon)
        if setup is not None and item == setup:
            draw_statusbar_actions(row)  # Retry and Show Log after a failed setup


_status_shown = None


def _statusbar_tick():
    """Redraw the status bar (and the sidebar) when what it shows changes."""
    global _status_shown
    header = bpy.types.STATUSBAR_HT_header
    funcs = getattr(header.draw, "_draw_funcs", None)
    if funcs is None or draw_statusbar not in funcs:
        header.append(draw_statusbar)  # WorkSpace.status_text_set() can restore an older draw
    try:
        shown = tuple(status_items())
    except Exception:  # never let the timer die: the status bar is cosmetic
        return 1.0
    if shown != _status_shown:
        _status_shown = shown
        tag_redraw()
        refresh_status_bar()
    return 0.5


_CLASSES = (
    GAMESTUDIO_MT_add_component,
    GAMESTUDIO_PT_play,
    GAMESTUDIO_PT_entity,
    GAMESTUDIO_PT_components,
    GAMESTUDIO_PT_project,
    GAMESTUDIO_PT_console,
)
# Outside the sidebar's Game tab (whose panel order is tested).
_OTHER_CLASSES = (
    GAMESTUDIO_MT_add_component_more,
    GAMESTUDIO_OT_export_all_levels,
    GAMESTUDIO_MT_animator_clips,
    GAMESTUDIO_OT_set_animator_clip,
    GAMESTUDIO_OT_bake_materials,
    GAMESTUDIO_PT_object,
    GAMESTUDIO_PT_object_materials,
    GAMESTUDIO_PT_collection,
    GAMESTUDIO_MT_material_shader,
    *SHADER_CATEGORY_MENUS,
    MATERIAL_PT_gamestudio_shader,
)


def register():
    for cls in _CLASSES + _OTHER_CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Collection.gs_is_prefab = BoolProperty(
        name="Prefab", description="Export this collection as a prefab template the game can place and spawn "
                                   "(docs/SPEC.md 4.4)", get=_get_prefab, set=_set_prefab)
    bpy.types.STATUSBAR_HT_header.append(draw_statusbar)
    if not bpy.app.timers.is_registered(_statusbar_tick):
        bpy.app.timers.register(_statusbar_tick, first_interval=1.0, persistent=True)


def unregister():
    global _status_shown
    if bpy.app.timers.is_registered(_statusbar_tick):
        bpy.app.timers.unregister(_statusbar_tick)
    _status_shown = None
    bpy.types.STATUSBAR_HT_header.remove(draw_statusbar)
    del bpy.types.Collection.gs_is_prefab
    for cls in reversed(_CLASSES + _OTHER_CLASSES):
        bpy.utils.unregister_class(cls)
