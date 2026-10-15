# SPDX-License-Identifier: GPL-3.0-or-later
"""Bridge methods: session, scene, components and game. Transforms are Blender space
(Z-up, metres, rotations in degrees, XYZ Euler). Every method runs on the main thread."""

import contextlib
import datetime
import json
import math
import os
import pathlib
import re
import time

import bpy

from ..core import components, export, ids, link, project, registry, shaders
from ..core.log import get_logger
from . import api
from .api import METHODS, BridgeError, method, names_param, param, resolve, rnd, vec
from .server import INVALID_PARAMS

log = get_logger(__name__)

CHECKPOINT_DIR = "checkpoints"
CHECKPOINT_MANIFEST = "checkpoint.json"


# --- Shared views of objects ----------------------------------------------------------


def rotation_deg(obj) -> list:
    return [round(math.degrees(a), 2) + 0.0 for a in obj.matrix_basis.to_euler("XYZ")]


def object_row(obj, verbose: bool = False, instances: dict | None = None) -> dict:
    """The compact one-line view used by scene_summary and list_objects (instances: from instanced())."""
    row = {"name": obj.name, "type": obj.type}
    if obj.parent is not None:
        row["parent"] = obj.parent.name
    row["location"] = rnd(obj.location)
    rot = rotation_deg(obj)
    if any(abs(a) > 1e-4 for a in rot):
        row["rotation_deg"] = rot
    if any(abs(s - 1.0) > 1e-5 for s in obj.scale):
        row["scale"] = rnd(obj.scale)
    carried = hierarchy_dimensions(obj)
    built = evaluated_bounds(obj, instances) if carried is None else None
    if carried is not None:  # an armature root, empty or asset instance: the size of what it carries
        row["dimensions"], row["dimensions_from"] = carried
    elif built is not None:  # geometry nodes: what they make, instances included
        row["dimensions"], row["dimensions_from"] = rnd(built[1] - built[0]), built[2]
        if built[3]:
            row["instances"] = built[3]
    elif obj.type in ("MESH", "CURVE", "FONT", "SURFACE", "META") or obj.dimensions.length > 0:
        row["dimensions"] = rnd(obj.dimensions)
    comps = components.list_components(obj)
    if comps:
        row["components"] = comps
    if verbose:
        if obj.type == "MESH" and obj.data is not None:
            row["faces"] = len(obj.data.polygons)
            row["materials"] = [m.name for m in obj.data.materials if m]
        row["collections"] = [c.name for c in obj.users_collection]
    if _in_view_layer(obj) and obj.hide_get():
        row["hidden"] = True
    if getattr(obj, "gs", None) is not None and obj.gs.ai_lock:
        row["ai_locked"] = True
    return row


def _in_view_layer(obj) -> bool:
    try:
        return obj.name in bpy.context.view_layer.objects
    except (AttributeError, ReferenceError):
        return False


def _project_config(root) -> dict:
    return project.read_config(root) if root is not None else {}


def _scenes_in(root) -> list[str]:
    """The project's levels, project-relative: <project>/scenes/*.blend only (§4.7). Other .blend files
    (asset libraries, inspection files under assets/) are not levels, even when one is the open file."""
    if root is None:
        return []
    return [path.relative_to(root).as_posix() for path in export.level_files(root)[:50]]


def _require_root() -> pathlib.Path:
    root = project.project_root()
    if root is None:
        raise BridgeError("The open .blend is not inside a GameStudio project (no gamestudio.json above it).",
                          hint="open_scene a .blend inside the project, or save this scene into the project's "
                               "scenes/ folder with save({path}).")
    return root


# --- Session ---------------------------------------------------------------------------


@method("methods")
def list_methods(params):
    return sorted(METHODS)


@method("ping")
def ping(params):
    return {"pong": True, "time": time.time()}


@method("status")
def status(params):
    from . import get_server
    from ..core import devserver
    from .. import kits

    root = project.project_root()
    config = _project_config(root)
    scene = bpy.context.scene
    client = link.get_link()
    server = get_server()
    session = devserver.running_session(root) if root is not None else None
    try:
        kit_count = len(kits.catalog())
    except Exception:
        kit_count = 0
    return {
        "bridge": {"port": server.port if server else None, "clients": server.clients if server else 0},
        "blender": {"version": bpy.app.version_string.split(" ")[0], "pid": os.getpid(),
                    "background": bool(bpy.app.background), "binary": bpy.app.binary_path},
        "file": {"path": bpy.data.filepath or None, "dirty": api.is_dirty()},
        "project": {"root": str(root), "name": config.get("name") or root.name,
                    "startScene": config.get("startScene"), "scenes": _scenes_in(root)} if root else None,
        "scene": {"name": scene.name, "objects": len(scene.objects), "mode": bpy.context.mode,
                  "all_scenes": [s.name for s in bpy.data.scenes]},
        "registry": {"components": len(registry.schemas()), "shaders": len(registry.shader_names()),
                     "origin": registry.source_info().get("origin"), "warnings": registry.warnings()[:5]},
        "link": {"state": client.state, "connected": client.connected,
                 "games": client.game_peers(), "game_state": client.game_state,
                 "last_error": client.last_error or None},
        "devserver": {"running": session is not None, "url": (session or {}).get("url"),
                      "starting": devserver.is_starting(), "started_by_blender": devserver.started_by_us(),
                      "last_error": devserver.last_error() or None},
        "kits": kit_count,
    }


def _check_unsaved(params) -> None:
    dirty = api.is_dirty()
    if dirty and bpy.data.filepath and not params.get("discard_changes") and not params.get("save_changes"):
        raise BridgeError("The open scene has unsaved changes.",
                          hint="Call save (or checkpoint) first, or pass save_changes: true or "
                               "discard_changes: true.")
    if dirty and not bpy.data.filepath and not params.get("discard_changes"):
        raise BridgeError("The open scene was never saved and has changes.",
                          hint="save({path}) it into the project first, or pass discard_changes: true.")
    if params.get("save_changes") and dirty and bpy.data.filepath:
        bpy.ops.wm.save_mainfile()


@method("open_scene")
def open_scene(params):
    path = pathlib.Path(param(params, "path", str))
    create = bool(params.get("create", False))
    if path.suffix.lower() != ".blend":
        raise BridgeError(f"{path} is not a .blend file.", code=INVALID_PARAMS)
    if create:
        if path.exists():
            raise BridgeError(f"{path} already exists.", hint="Call open_scene without create to open it.")
        _check_unsaved(params)
        path.parent.mkdir(parents=True, exist_ok=True)
        bpy.ops.wm.read_homefile(use_empty=True)
        bpy.ops.wm.save_as_mainfile(filepath=str(path))
    else:
        if not path.is_file():
            root = project.project_root()
            scenes = _scenes_in(root)
            raise BridgeError(f"{path} does not exist.",
                              hint=f"Scenes in the project: {', '.join(scenes)}" if scenes
                              else "Pass create: true to make a new, empty scene there.")
        _check_unsaved(params)
        bpy.ops.wm.open_mainfile(filepath=str(path), load_ui=bool(params.get("load_ui", True)))
    root = project.project_root()
    project.invalidate_cache()
    registry.reload_registry()
    scene = bpy.context.scene
    return {"opened": bpy.data.filepath, "created": create, "project": str(root) if root else None,
            "scene": scene.name, "objects": len(scene.objects)}


@contextlib.contextmanager
def _ui_exports():
    """Collect what the UI file's save handler exports (core.gameui_live: gameui_export.export_ui_file() after a
    save of a UI file): yields the list its results (or the exception it raised) are appended to."""
    from ..core import gameui_export

    found, original = [], gameui_export.export_ui_file

    def recording(*args, **kwargs):
        try:
            result = original(*args, **kwargs)
        except Exception as exc:
            found.append(exc)
            raise
        found.append(result)
        return result

    gameui_export.export_ui_file = recording
    try:
        yield found
    finally:
        gameui_export.export_ui_file = original


def _ui_export_summary(found) -> dict:
    """save's ui_export: what saving a UI file exported (docs/GAME-UI.md §4): {canvases: [{canvas, file, rev,
    written, elements} or {canvas, skipped}], removed?, locales, warnings}, or {error}. Without a save handler's
    export (the add-on's handlers off), the saved file is exported here."""
    from ..core import gameui_export

    result = found[-1] if found else None
    if result is None:
        try:
            result = gameui_export.export_ui_file(unsaved=False)
        except Exception as exc:  # noqa: BLE001 - the file is saved; the export is reported, never raised
            result = exc
    if isinstance(result, Exception):
        message = str(result) if isinstance(result, export.ExportError) else f"{type(result).__name__}: {result}"
        return {"error": message}
    canvases = [{k: c[k] for k in ("canvas", "file", "rev", "written", "elements", "skipped") if k in c}
                for c in result.get("canvases", [])]
    out = {"canvases": canvases, "locales": list(result.get("locales") or []),
           "warnings": list(result.get("warnings") or [])[:20]}
    if result.get("removed"):
        out["removed"] = list(result["removed"])
    return out


@method("save")
def save(params):
    from ..core import gameui as ui_core

    path = params.get("path")
    with _ui_exports() as exported:
        if path:
            path = pathlib.Path(path)
            if path.suffix.lower() != ".blend":
                raise BridgeError(f"{path} is not a .blend path.", code=INVALID_PARAMS)
            path.parent.mkdir(parents=True, exist_ok=True)
            bpy.ops.wm.save_as_mainfile(filepath=str(path))
        elif bpy.data.filepath:
            bpy.ops.wm.save_mainfile()
        else:
            raise BridgeError("This scene has never been saved, so it has no path.",
                              hint="Pass path, e.g. <project>/scenes/level1.blend.")
    saved = bpy.data.filepath
    out = {"saved": saved, "bytes": os.path.getsize(saved), "project": str(project.project_root() or "") or None}
    if ui_core.is_ui_file():
        out["ui_export"] = _ui_export_summary(exported)  # the screens the game loads, as this save exported them
    try:
        from ..core import twin

        twin_file = twin.twin_path(saved)
        if twin_file.is_file():
            out["twin"] = str(twin_file)  # written on save: a text view of the level that git can diff
    except ImportError:
        pass
    return out


def _checkpoints_dir(root) -> pathlib.Path:
    return project.gs_dir(root) / CHECKPOINT_DIR


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40] or "checkpoint"


def make_checkpoint(label: str, auto: bool = False) -> dict:
    root = _require_root()
    if not bpy.data.filepath:
        raise BridgeError("Checkpoints need a saved scene.", hint="save({path}) the scene into the project first.")
    now = datetime.datetime.now()
    base = f"{now:%Y%m%d-%H%M%S}-{_slug(label)}"
    folder = _checkpoints_dir(root) / base
    n = 1
    while folder.exists():
        n += 1
        folder = _checkpoints_dir(root) / f"{base}-{n}"
    folder.mkdir(parents=True)
    source = pathlib.Path(bpy.data.filepath)
    blend = folder / source.name
    bpy.ops.wm.save_as_mainfile(filepath=str(blend), copy=True)
    try:
        rel_source = source.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        rel_source = str(source)
    scene = bpy.context.scene
    manifest = {
        "format": "gamestudio.checkpoint",
        "formatVersion": 1,
        "id": folder.name,
        "label": label,
        "auto": auto,
        "created": now.astimezone().isoformat(timespec="seconds"),
        "source": rel_source,
        "file": blend.name,
        "scene": scene.name,
        "objects": len(scene.objects),
        "dirty": api.is_dirty(),
        "blenderVersion": bpy.app.version_string.split(" ")[0],
        "bytes": blend.stat().st_size,
    }
    (folder / CHECKPOINT_MANIFEST).write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


@method("checkpoint")
def checkpoint(params):
    label = str(params.get("label") or "checkpoint")[:120]
    manifest = make_checkpoint(label)
    manifest["path"] = str(_checkpoints_dir(project.project_root()) / manifest["id"])
    return manifest


def _all_checkpoints(root) -> list[dict]:
    folder = _checkpoints_dir(root)
    found = []
    if not folder.is_dir():
        return found
    for sub in folder.iterdir():
        manifest_path = sub / CHECKPOINT_MANIFEST
        if not manifest_path.is_file():
            continue
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        data["id"] = sub.name
        found.append(data)
    found.sort(key=lambda d: d["id"], reverse=True)
    return found


@method("list_checkpoints")
def list_checkpoints(params):
    root = _require_root()
    items = _all_checkpoints(root)
    limit = int(params.get("limit", 50))
    return {"count": len(items), "checkpoints": [
        {k: d.get(k) for k in ("id", "label", "created", "source", "scene", "objects", "auto", "bytes")}
        for d in items[:limit]], "folder": str(_checkpoints_dir(root))}


@method("restore_checkpoint")
def restore_checkpoint(params):
    root = _require_root()
    wanted = param(params, "id", str)
    items = _all_checkpoints(root)
    match = next((d for d in items if d["id"] == wanted), None)
    if match is None:
        prefix = [d for d in items if d["id"].startswith(wanted) or d.get("label") == wanted]
        if len(prefix) == 1:
            match = prefix[0]
    if match is None:
        raise BridgeError(f"No checkpoint '{wanted}'.",
                          hint="Call list_checkpoints; ids look like 20261010-142501-before-walls.")
    folder = _checkpoints_dir(root) / match["id"]
    blend = folder / match["file"]
    if not blend.is_file():
        raise BridgeError(f"The checkpoint file {blend} is missing.")
    source = pathlib.Path(match["source"])
    target = source if source.is_absolute() else root / source
    backup = None
    if params.get("backup", True) and bpy.data.filepath:
        backup = make_checkpoint(f"before restore {match['id']}", auto=True)["id"]
    bpy.ops.wm.open_mainfile(filepath=str(blend), load_ui=False)
    target.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=str(target))
    project.invalidate_cache()
    registry.reload_registry()
    return {"restored": match["id"], "label": match.get("label"), "saved_as": str(target),
            "backup_checkpoint": backup, "scene": bpy.context.scene.name,
            "objects": len(bpy.context.scene.objects)}


@method("undo", undo="custom")
def undo(params):
    steps = int(params.get("steps", 1))
    done = 0
    for _ in range(max(1, min(steps, 50))):
        try:
            if "FINISHED" not in bpy.ops.ed.undo():
                break
        except RuntimeError:
            break
        done += 1
    if done == 0:
        raise BridgeError("Nothing to undo.",
                          hint="The undo history starts when a file is opened (launch_blender, open_scene, "
                               "restore_checkpoint) and holds the last "
                               f"{bpy.context.preferences.edit.undo_steps} steps. To go back further, use "
                               "restore_checkpoint (list_checkpoints shows them).")
    out = {"undone": done}
    if done < steps:
        out["note"] = f"Only {done} of {steps} step(s) could be undone: that is where the undo history starts."
    return out


@method("redo", undo="custom")
def redo(params):
    steps = int(params.get("steps", 1))
    done = 0
    for _ in range(max(1, min(steps, 50))):
        try:
            if "FINISHED" not in bpy.ops.ed.redo():
                break
        except RuntimeError:
            break
        done += 1
    if done == 0:
        raise BridgeError("Nothing to redo.")
    return {"redone": done}


@method("quit")
def quit_blender(params):
    from . import request_quit

    if not bpy.app.background and not params.get("force"):
        raise BridgeError("Refusing to quit an interactive Blender session.",
                          hint="Only background sessions started by the MCP server are closed this way.")
    if params.get("save") and bpy.data.filepath:
        bpy.ops.wm.save_mainfile()
    request_quit()
    return {"quitting": True}


# --- Scene -----------------------------------------------------------------------------


def _collection_tree(coll, depth=0) -> dict:
    node = {"name": coll.name, "objects": len(coll.objects)}
    if export.is_prefab(coll):
        node["prefab"] = True
    if coll.children and depth < 6:
        node["children"] = [_collection_tree(c, depth + 1) for c in coll.children]
    return node


def _mesh_stats(objects, instances: dict) -> dict:
    """Totals of the meshes as evaluated (modifiers and geometry nodes applied), plus what instances add."""
    depsgraph = bpy.context.evaluated_depsgraph_get()
    verts = faces = tris = 0
    for obj in objects:
        if obj.type == "MESH" and obj.data is not None:
            mesh = obj.evaluated_get(depsgraph).data if obj.modifiers else obj.data
            verts += len(mesh.vertices)
            faces += len(mesh.polygons)
            tris += max(0, len(mesh.loops) - 2 * len(mesh.polygons))  # sum of (corners - 2) per face
    shown = [instances[o] for o in objects if o in instances]
    out = {"vertices": verts, "faces": faces, "triangles": tris}
    if shown:
        out["instances"] = sum(e["count"] for e in shown)
        out["instanced_triangles"] = sum(e["triangles"] for e in shown)
    return out


INSTANCE_CORNERS_UP_TO = 20_000  # beyond, instances count by their origins only (keeps big scenes cheap)


def instanced() -> dict:
    """{object: {lo, hi, count, triangles}} for every object that shows instances in the evaluated scene
    (geometry-nodes scatters and arrays, collection instances): one pass over the depsgraph."""
    from mathutils import Vector

    depsgraph = bpy.context.evaluated_depsgraph_get()
    out, triangles, seen = {}, {}, 0
    for inst in depsgraph.object_instances:
        if not inst.is_instance or inst.parent is None:
            continue
        seen += 1
        entry = out.setdefault(inst.parent.original, {"lo": None, "hi": None, "count": 0, "triangles": 0})
        entry["count"] += 1
        ob = inst.object
        data = ob.data
        if isinstance(data, bpy.types.Mesh):
            key = data.as_pointer()
            if key not in triangles:
                triangles[key] = max(0, len(data.loops) - 2 * len(data.polygons))
            entry["triangles"] += triangles[key]
        matrix = inst.matrix_world.copy()
        if seen <= INSTANCE_CORNERS_UP_TO:
            points = [matrix @ Vector(c) for c in ob.bound_box]
        else:
            points = [matrix.translation.copy()]
        lo = entry["lo"] or points[0].copy()
        hi = entry["hi"] or points[0].copy()
        for point in points:
            for i in range(3):
                lo[i] = min(lo[i], point[i])
                hi[i] = max(hi[i], point[i])
        entry["lo"], entry["hi"] = lo, hi
    return out


def evaluated_bounds(obj, instances: dict | None = None):
    """(lo, hi, source, instance count) of what geometry nodes make for obj, instances included, or None
    for objects without a geometry-nodes modifier (their own bounds are right)."""
    from mathutils import Vector

    if obj.type != "MESH" or not any(m.type == "NODES" for m in obj.modifiers):
        return None
    instances = instanced() if instances is None else instances
    evaluated = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
    own = evaluated.data is not None and len(evaluated.data.vertices) > 0
    points = [evaluated.matrix_world @ Vector(c) for c in evaluated.bound_box] if own else []
    entry = instances.get(obj)
    if entry is not None and entry["lo"] is not None:
        points += [entry["lo"], entry["hi"]]
    if not points:
        return None
    lo = Vector([min(p[i] for p in points) for i in range(3)])
    hi = Vector([max(p[i] for p in points) for i in range(3)])
    return lo, hi, "evaluated" if own else "instances", entry["count"] if entry is not None else 0


SUMMARY_LIMIT = 100


def _by_collection(objects) -> list:
    groups: dict[str, dict] = {}
    for obj in objects:
        coll = obj.users_collection[0].name if obj.users_collection else "(none)"
        g = groups.setdefault(coll, {"collection": coll, "objects": 0, "types": {}, "examples": []})
        g["objects"] += 1
        g["types"][obj.type] = g["types"].get(obj.type, 0) + 1
        if len(g["examples"]) < 5:
            g["examples"].append(obj.name)
    return sorted(groups.values(), key=lambda g: -g["objects"])


@method("scene_summary")
def scene_summary(params):
    scene = bpy.context.scene
    verbose = bool(params.get("verbose", False))
    limit = int(params.get("max_objects", SUMMARY_LIMIT))
    objects = list(scene.objects)
    by_type: dict[str, int] = {}
    for obj in objects:
        by_type[obj.type] = by_type.get(obj.type, 0) + 1
    root = project.project_root()
    active = bpy.context.view_layer.objects.active
    with_components = sum(1 for o in objects if components.list_components(o))
    instances = instanced()  # one pass over the evaluated scene: geometry-nodes and collection instances
    return {
        "scene": scene.name,
        "file": bpy.data.filepath or None,
        "dirty": api.is_dirty(),
        "project": str(root) if root else None,
        "space": "Blender world space: Z-up, metres; rotation_deg is XYZ Euler in degrees. "
                 "Component vec3 values are runtime space (Y-up).",
        "unit_scale": scene.unit_settings.scale_length,
        "camera": scene.camera.name if scene.camera else None,
        "world": scene.world.name if scene.world else None,
        "stats": {"objects": len(objects), "by_type": by_type, "with_components": with_components,
                  **_mesh_stats(objects, instances), "materials": len(bpy.data.materials),
                  "collections": len(bpy.data.collections)},
        "collections": _collection_tree(scene.collection),
        **_summary_objects(objects, limit, verbose, instances),
        "selection": [o.name for o in bpy.context.selected_objects][:50],
        "active": active.name if active else None,
        "prefabs": _prefab_names(),
        "animated": [o.name for o in objects if o.animation_data is not None
                     and (o.animation_data.action or len(o.animation_data.nla_tracks))][:100],
        "component_types": registry.component_names(),
        "shader_types": registry.shader_names(),
    }


def _summary_objects(objects, limit: int, verbose: bool, instances: dict) -> dict:
    """Every object as a row when there are few; above the limit, groups plus the largest objects."""
    if len(objects) <= limit:
        return {"objects": [object_row(o, verbose, instances) for o in objects]}
    largest = sorted(objects, key=lambda o: -max(o.dimensions) if o.type in ("MESH", "CURVE", "FONT") else 0)
    return {
        "objects_summarised": True,
        "by_collection": _by_collection(objects),
        "largest": [object_row(o, verbose, instances) for o in largest[:20]],
        "hint": (f"{len(objects)} objects: more than max_objects ({limit}). Drill down with list_objects, e.g. "
                 '{"filter": {"collection": "<name>"}, "limit": 100} or {"filter": {"name": "Coin*"}}, '
                 "or pass a larger max_objects."),
    }


def _match(pattern: str, name: str) -> bool:
    import fnmatch

    if any(ch in pattern for ch in "*?["):
        return fnmatch.fnmatchcase(name.lower(), pattern.lower())
    return pattern.lower() in name.lower()


@method("list_objects")
def list_objects(params):
    flt = params.get("filter") or {}
    if not isinstance(flt, dict):
        raise BridgeError("filter must be an object, e.g. {\"type\": \"MESH\", \"component\": \"Coin\"}.",
                          code=INVALID_PARAMS)
    limit = int(params.get("limit", flt.get("limit", 100)))
    offset = int(params.get("offset", 0))
    verbose = bool(params.get("verbose", False))
    name = flt.get("name")
    otype = (flt.get("type") or "").upper() or None
    comp = flt.get("component")
    coll = flt.get("collection")
    parent = flt.get("parent", ...)
    selected = flt.get("selected")
    rows = []
    for obj in bpy.context.scene.objects:
        if name and not _match(name, obj.name):
            continue
        if otype and obj.type != otype:
            continue
        if comp and comp not in components.list_components(obj):
            continue
        if coll and coll not in {c.name for c in obj.users_collection}:
            continue
        if parent is not ...:
            if (obj.parent.name if obj.parent else None) != parent:
                continue
        if selected is not None and bool(obj.select_get()) != bool(selected):
            continue
        rows.append(obj)
    page = rows[offset:offset + limit]
    instances = instanced() if any(any(m.type == "NODES" for m in o.modifiers) for o in page) else {}
    out = {"count": len(rows), "offset": offset, "objects": [object_row(o, verbose, instances) for o in page]}
    if offset + limit < len(rows):
        out["next_offset"] = offset + limit
        out["hint"] = f"{len(rows) - offset - limit} more: call again with offset {offset + limit}, or narrow the filter."
    return out


@method("get_object")
def get_object(params):
    from ..assistant import tools as assistant_tools
    from ..assistant.proposals import ProposalError

    obj = resolve(param(params, "name", str))
    verbose = bool(params.get("verbose", False))
    try:
        info = assistant_tools.get_object({"name": obj.name}, None)
    except ProposalError as exc:
        raise BridgeError(str(exc)) from None
    if not verbose:
        for key in ("custom_properties", "collections", "linked_from_library"):
            info.pop(key, None)
        if not info.get("ai_locked"):
            info.pop("ai_locked", None)
        if len(info.get("children", [])) > 10:
            info["children"] = info["children"][:10] + [f"... {len(info['children']) - 10} more"]
    corners = [obj.matrix_world @ _v(c) for c in obj.bound_box]
    if corners:
        info["world_bbox"] = {"min": rnd([min(c[i] for c in corners) for i in range(3)]),
                              "max": rnd([max(c[i] for c in corners) for i in range(3)])}
    carried = hierarchy_dimensions(obj)
    if carried is not None:  # a rigged character's armature, a group empty, an asset instance
        info["dimensions"], info["dimensions_from"] = carried
        lo, hi = hierarchy_bounds(obj)
        info["world_bbox"] = {"min": rnd(lo), "max": rnd(hi), "of": carried[1]}
    built = evaluated_bounds(obj) if carried is None else None
    if built is not None:  # geometry nodes: their output, instances included
        lo, hi, source, count = built
        info["dimensions"], info["dimensions_from"] = rnd(hi - lo), source
        info["world_bbox"] = {"min": rnd(lo), "max": rnd(hi), "of": source}
        if count:
            info["instances"] = count
            info["instanced_triangles"] = instanced().get(obj, {}).get("triangles", 0)
    info["visible"] = obj.visible_get() if _in_view_layer(obj) else False
    if obj.type == "MESH" and obj.data is not None:
        mesh = obj.data
        info["mesh"]["triangles"] = sum(len(p.vertices) - 2 for p in mesh.polygons)
        if verbose:
            info["mesh"]["uv_layers"] = [uv.name for uv in mesh.uv_layers]
            info["material_slots"] = [slot.material.name if slot.material else None for slot in obj.material_slots]
    shaded = material_shaders(obj)
    if shaded:
        info["material_shaders"] = shaded
    if verbose:
        info["rotation_mode"] = obj.rotation_mode
    try:
        from .. import kits

        built = kits.kit_params(obj)
    except (ImportError, AttributeError):
        built = None
    if built is not None:
        info["kit"] = {"kind": built[0], "params": built[1]}
    animation = animation_info(obj)
    if animation is not None:
        info["animation"] = animation if verbose else {"action": animation["action"],
                                                        "clips": [c["name"] for c in animation["clips"]]}
    prefab = export.instanced_prefab(obj)
    if prefab is not None:
        info["prefab_instance_of"] = prefab.name
    prefabs_of = [c.name for c in obj.users_collection if export.is_prefab(c)]
    if prefabs_of:
        info["in_prefab"] = prefabs_of
    if obj.type == "LIGHT":
        info["light"] = {"type": obj.data.type, "energy": obj.data.energy, "color": rnd(obj.data.color)}
    if obj.type == "CAMERA":
        info["camera"] = {"type": obj.data.type, "lens": obj.data.lens, "is_scene_camera":
                          bpy.context.scene.camera == obj}
    return info


def _v(c):
    from mathutils import Vector

    return Vector(c)


def _transform_one(obj, spec: dict, relative: bool) -> dict:
    from mathutils import Euler

    from ..assistant import proposals

    location = vec(spec, "location")
    rotation = vec(spec, "rotation_deg")
    scale = vec(spec, "scale")
    if location is None and rotation is None and scale is None:
        raise BridgeError(f"set_transform for '{obj.name}' needs location, rotation_deg or scale.",
                          code=INVALID_PARAMS)
    op = {}
    if location is not None:
        op["location"] = [a + b for a, b in zip(obj.location, location)] if relative else location
    if rotation is not None:
        if relative:
            current = obj.matrix_basis.to_euler("XYZ")
            delta = Euler([math.radians(a) for a in rotation], "XYZ")
            combined = (delta.to_matrix() @ current.to_matrix()).to_euler("XYZ")
            op["rotation_deg"] = [math.degrees(a) for a in combined]
        else:
            op["rotation_deg"] = rotation
    if scale is not None:
        op["scale"] = [a * b for a, b in zip(obj.scale, scale)] if relative else scale
    proposals._set_transform(obj, op)
    return {"name": obj.name, "location": rnd(obj.location), "rotation_deg": rotation_deg(obj),
            "scale": rnd(obj.scale)}


SNAP_ALIGN = ("lowest", "average", "highest")


@method("set_transform", mutating=True)
def set_transform(params):
    relative = bool(params.get("relative", False))
    items = params.get("items")
    if items is None:
        items = [params]
    if not isinstance(items, list) or not items:
        raise BridgeError("items must be a non-empty list of {name, location?, rotation_deg?, scale?}.",
                          code=INVALID_PARAMS)
    resolved = [(resolve(param(item, "name", str), writable=True), item) for item in items]
    snap_all = bool(params.get("snap_to_ground", False))
    align_all = param(params, "align", str, "lowest", choices=SNAP_ALIGN)
    tilt_all = bool(params.get("tilt", False))
    snaps = {}
    for obj, item in resolved:
        if snap_all or item.get("snap_to_ground"):
            snaps[obj] = (param(item, "align", str, align_all, choices=SNAP_ALIGN), bool(item.get("tilt", tilt_all)))
    for obj, item in resolved:
        if obj not in snaps or any(k in item for k in ("location", "rotation_deg", "scale")):
            _transform_one(obj, item, relative)
    bpy.context.view_layer.update()
    done = []
    for obj, item in resolved:
        entry = {"name": obj.name}
        if obj in snaps:
            entry["snap"] = snap_to_ground(obj, *snaps[obj])
        entry.update(location=rnd(obj.location), rotation_deg=rotation_deg(obj), scale=rnd(obj.scale))
        done.append(entry)
    return {"updated": done}


# --- Shapes: bounds of hierarchies, the underside that rests on the ground ----------------------------

GEOMETRY = ("MESH", "CURVE", "FONT", "SURFACE", "META", "CURVES", "POINTCLOUD", "VOLUME")
SNAP_GRID = 7          # contact cells per side of the underside
SNAP_BAND = 0.1        # the underside: the lowest 10 % of the object (a trunk base, feet, a crate base)
SNAP_INSET = 0.05      # contacts sit this share of the way in from the underside's outline
MAX_TILT_DEG = 45.0
POINTS_PER_MESH = 60_000


def _basis_world(obj):
    # matrix_world is identity for objects never evaluated (an asset's contents seen through an
    # instance): rebuild it from the parents.
    if obj.parent is None:
        return obj.matrix_basis.copy()
    return _basis_world(obj.parent) @ obj.matrix_parent_inverse @ obj.matrix_basis


def shape_parts(obj) -> list:
    """(object, world matrix) for the geometry of obj, its children and the contents of asset instances."""
    from mathutils import Matrix

    parts = []
    for o in (obj, *obj.children_recursive):
        if o.type in GEOMETRY:
            parts.append((o, o.matrix_world.copy()))
        elif o.type == "EMPTY" and o.instance_type == "COLLECTION" and o.instance_collection is not None:
            offset = Matrix.Translation(-o.instance_collection.instance_offset)
            parts += [(inner, o.matrix_world @ offset @ _basis_world(inner))
                      for inner in o.instance_collection.all_objects if inner.type in GEOMETRY]
    return parts


def hierarchy_bounds(obj):
    """World (min, max) of obj's geometry with its children's and an instanced asset's, or None."""
    from mathutils import Vector

    points = [m @ Vector(c) for o, m in shape_parts(obj) for c in o.bound_box]
    if not points:
        return None
    return (Vector([min(p[i] for p in points) for i in range(3)]), Vector([max(p[i] for p in points) for i in range(3)]))


def hierarchy_dimensions(obj):
    """(size, source) for an object without geometry of its own (an armature root, an empty, an asset
    instance): the bounds of what it carries. None for geometry objects and empty hierarchies."""
    if obj.type in GEOMETRY:
        return None
    found = hierarchy_bounds(obj)
    if found is None:
        return None
    lo, hi = found
    source = "asset" if obj.instance_type == "COLLECTION" and obj.instance_collection is not None else "children"
    return rnd(hi - lo), source


def _shape_points(obj):
    """World-space vertices (numpy, N x 3) of what shape_parts finds; bound-box corners for non-meshes."""
    import numpy as np

    chunks = []
    for o, m in shape_parts(obj):
        mesh = o.data if o.type == "MESH" else None
        if mesh is not None and len(mesh.vertices):
            flat = np.empty(len(mesh.vertices) * 3, dtype=np.float32)
            mesh.vertices.foreach_get("co", flat)
            co = flat.reshape(-1, 3).astype(np.float64)
            if len(co) > POINTS_PER_MESH:
                co = co[::math.ceil(len(co) / POINTS_PER_MESH)]
        else:
            co = np.array([tuple(c) for c in o.bound_box], dtype=np.float64)
        mat = np.array(m, dtype=np.float64)
        chunks.append(co @ mat[:3, :3].T + mat[:3, 3])
    return np.concatenate(chunks) if chunks else None


def _underside(obj, own_up: bool = False) -> tuple:
    """([(x, y, z)] lowest point per cell of the object's bottom band, height): where it can touch the ground.
    A tree's band is its trunk base, a crate's its base; an object without geometry is its origin. The band
    is measured along world Z, or along the object's own up axis (own_up, after tilting)."""
    import numpy as np

    pts = _shape_points(obj)
    if pts is None:
        t = obj.matrix_world.translation
        return [(t.x, t.y, t.z)], 0.0
    up = np.array([0.0, 0.0, 1.0])
    if own_up:
        axis = obj.matrix_world.to_3x3().col[2]
        up = np.array(axis.normalized() if axis.length > 1e-9 else (0.0, 0.0, 1.0))
    h = pts @ up
    hmin = float(h.min())
    height = float(h.max()) - hmin
    band = pts[h <= hmin + max(0.02, SNAP_BAND * height)]
    lo, hi = band[:, :2].min(axis=0), band[:, :2].max(axis=0)
    span = np.maximum(hi - lo, 1e-6)
    cells = np.clip(((band[:, :2] - lo) / span * SNAP_GRID).astype(int), 0, SNAP_GRID - 1)
    keys = cells[:, 0] * SNAP_GRID + cells[:, 1]
    order = np.lexsort((band[:, 2], keys))  # by cell, lowest first
    first = order[np.concatenate(([True], keys[order][1:] != keys[order][:-1]))]
    # A little inside the outline (towards the middle, staying on a tilted base), so an object flush with
    # a ledge's edge does not sample past the edge.
    cells = band[first]
    cells = cells + (cells.mean(axis=0) - cells) * SNAP_INSET
    return [tuple(float(v) for v in c) for c in cells], height


def _ignored(obj) -> set:
    ignore = {obj, *obj.children_recursive}
    for o in list(ignore):
        if o.instance_type == "COLLECTION" and o.instance_collection is not None:
            ignore.update(o.instance_collection.all_objects)
    return ignore


def _contacts(obj, own_up: bool = False) -> tuple:
    """([(x, y, underside z, ground z, ground object)], height, misses, top): the ground under each cell.

    The object is lifted above the rays' start while they are cast, so its own faces are never in the way,
    even where they lie exactly on the ground (as they do after a snap)."""
    under, height = _underside(obj, own_up)
    found = hierarchy_bounds(obj)
    bottom, ceiling = (found[0].z, found[1].z) if found else (obj.matrix_world.translation.z,) * 2
    top = max(ceiling, _scene_top()) + 1.0
    saved = obj.matrix_world.copy()
    lifted = saved.copy()
    lifted.translation.z += top + 1.0 - bottom
    obj.matrix_world = lifted
    bpy.context.view_layer.update()
    try:
        depsgraph = bpy.context.evaluated_depsgraph_get()
        ignore = _ignored(obj)
        hits = []
        for x, y, z in under:
            hit = cast_down(x, y, top, ignore, depsgraph)
            if hit is not None:
                hits.append((x, y, z, hit[0], hit[1]))
    finally:
        obj.matrix_world = saved
        bpy.context.view_layer.update()
    return hits, height, len(under) - len(hits), top


def _tilt(obj, contacts) -> dict:
    """Turn obj so its up axis follows the plane through the ground contacts (heading kept)."""
    import numpy as np
    from mathutils import Matrix, Vector

    pts = np.array([(c[0], c[1], c[3]) for c in contacts], dtype=np.float64)
    if len(pts) < 3:
        return {"tilt_deg": 0.0, "tilt_note": "Too few contacts to measure the slope."}
    a = np.column_stack([pts[:, 0], pts[:, 1], np.ones(len(pts))])
    solution, _res, rank, _sv = np.linalg.lstsq(a, pts[:, 2], rcond=None)
    if rank < 3:
        return {"tilt_deg": 0.0, "tilt_note": "The contacts are in a line: no slope to follow."}
    normal = Vector((-solution[0], -solution[1], 1.0)).normalized()
    up = (obj.matrix_world.to_3x3() @ Vector((0, 0, 1))).normalized()
    angle = math.degrees(up.angle(normal))
    if angle > MAX_TILT_DEG:
        return {"tilt_deg": 0.0, "tilt_note": f"The ground slopes {angle:.0f} deg (more than {MAX_TILT_DEG:.0f}): not tilted."}
    pivot = obj.matrix_world.translation.copy()
    turn = up.rotation_difference(normal).to_matrix().to_4x4()
    obj.matrix_world = Matrix.Translation(pivot) @ turn @ Matrix.Translation(-pivot) @ obj.matrix_world
    bpy.context.view_layer.update()
    return {"tilt_deg": round(angle, 2)}


def snap_to_ground(obj, align: str = "lowest", tilt: bool = False) -> dict:
    """Move obj down (or up) onto the ground under its underside, and report what is left of the gap.

    The underside is sampled in a grid of cells (the lowest point of each), and a ray finds the ground under
    each. align "lowest": the lowest contact decides, so no part floats (on a slope the uphill side sinks in);
    "highest": the highest decides, so nothing sinks (the downhill side floats); "average": in between.
    tilt first turns the object to the slope of the ground under it.
    """
    out = {"align": align}
    contacts, height, misses, top = _contacts(obj)
    if not contacts:
        return {"ground": None, "moved_z": 0.0, "align": align,
                "note": f"No surface under '{obj.name}' (searched down from z={top:.2f})."}
    if tilt:
        out.update(_tilt(obj, contacts))
        if out.get("tilt_deg"):
            contacts, height, misses, top = _contacts(obj, own_up=True)
            if not contacts:
                return {**out, "ground": None, "moved_z": 0.0, "note": f"No surface under '{obj.name}' after tilting."}
    lift = [c[3] - c[2] for c in contacts]  # how far each contact must move to touch its ground
    if align == "average":
        dz = sum(lift) / len(lift)
        decider = min(range(len(lift)), key=lambda i: abs(lift[i] - dz))
    else:
        decider = (min if align == "lowest" else max)(range(len(lift)), key=lambda i: lift[i])
        dz = lift[decider]
    world = obj.matrix_world.copy()
    world.translation.z += dz
    obj.matrix_world = world
    bpy.context.view_layer.update()
    gaps = [c[2] + dz - c[3] for c in contacts]  # > 0 floats above the ground there, < 0 is sunk into it
    hits = [c[4] for c in contacts]
    floating, sunk = max(0.0, max(gaps)), max(0.0, -min(gaps))
    out.update(ground=round(contacts[decider][3], 4), on=max(set(hits), key=hits.count), moved_z=round(dz, 4),
               contacts=len(contacts), float_m=round(floating, 3), sink_m=round(sunk, 3))
    if misses:
        out["no_ground_under"] = misses
    tolerance = max(0.05, 0.1 * height)
    if floating > tolerance or sunk > tolerance:
        side = (f"part of it sinks {sunk:.2f} m into the ground" if align == "lowest" else
                f"part of it floats {floating:.2f} m above the ground" if align == "highest" else
                f"it floats up to {floating:.2f} m and sinks up to {sunk:.2f} m")
        out["hint"] = (f"The ground under '{obj.name}' is uneven: {side}. "
                       + ("tilt: true follows a slope; " if not tilt else "")
                       + 'align "lowest" never floats, "highest" never sinks.')
    return out


def cast_down(x: float, y: float, top: float, ignore: set, depsgraph):
    """The first surface below (x, y) from height `top`, skipping objects in `ignore`: (z, name) or None."""
    from mathutils import Vector

    origin = Vector((x, y, top))
    scene = bpy.context.scene
    for _ in range(32):
        hit, location, _normal, _index, obj, _matrix = scene.ray_cast(depsgraph, origin, Vector((0, 0, -1)))
        if not hit:
            return None
        if obj is not None and (obj.original if hasattr(obj, "original") else obj) in ignore:
            origin = Vector((x, y, location.z - 1e-4))
            continue
        return location.z, (obj.original.name if obj is not None else None)
    return None


def _scene_top() -> float:
    """The highest point of the scene's meshes (where downward rays start)."""
    from mathutils import Vector

    tops = [max((o.matrix_world @ Vector(c)).z for c in o.bound_box)
            for o in bpy.context.scene.objects if o.type == "MESH"]
    return max(tops, default=0.0)


@method("ground_height")
def ground_height(params):
    points = params.get("points")
    if not isinstance(points, list) or not points or len(points) > 2000:
        raise BridgeError("points must be a list of 1-2000 [x, y] pairs (Blender space).", code=INVALID_PARAMS)
    ignore = {resolve(n) for n in (params.get("ignore") or [])}
    depsgraph = bpy.context.evaluated_depsgraph_get()
    top = _scene_top() + 1.0
    out = []
    for p in points:
        if not isinstance(p, (list, tuple)) or len(p) < 2:
            raise BridgeError(f"Each point must be [x, y], got {p!r}.", code=INVALID_PARAMS)
        hit = cast_down(float(p[0]), float(p[1]), top, ignore, depsgraph)
        out.append({"x": p[0], "y": p[1], "z": round(hit[0], 4) if hit else None, "object": hit[1] if hit else None})
    return {"points": out}


@method("raycast")
def raycast(params):
    from mathutils import Vector

    origin = Vector(vec(params, "origin") or param(params, "origin", list))
    direction = Vector(vec(params, "direction", default=[0.0, 0.0, -1.0]))
    if direction.length < 1e-9:
        raise BridgeError("direction must not be zero.", code=INVALID_PARAMS)
    distance = float(params.get("max_distance", 1.0e4))
    ignore = {resolve(n) for n in (params.get("ignore") or [])}
    depsgraph = bpy.context.evaluated_depsgraph_get()
    start = origin.copy()
    direction.normalize()
    for _ in range(32):
        hit, location, normal, _i, obj, _m = bpy.context.scene.ray_cast(depsgraph, start, direction,
                                                                         distance=distance)
        if not hit:
            return {"hit": False}
        if obj is not None and obj.original in ignore:
            start = location + direction * 1e-4
            continue
        return {"hit": True, "location": rnd(location, 4), "normal": rnd(normal, 4),
                "distance": round((location - origin).length, 4), "object": obj.original.name if obj else None}
    return {"hit": False}


@method("delete_objects", mutating=True)
def delete_objects(params):
    names = names_param(params, "names", "name")
    with_children = bool(params.get("with_children", False))
    targets = []
    for name in names:
        obj = resolve(name, writable=True)
        if obj not in targets:
            targets.append(obj)
        if with_children:
            for child in obj.children_recursive:
                if child not in targets:
                    targets.append(child)
    from .assets import entity_references, mark_cleared

    doomed = set(targets)
    cleared = []
    for other in bpy.context.scene.objects:
        if other in doomed:
            continue
        for comp, field, target in entity_references(other):
            if target is not None and target in doomed:
                cleared.append({"object": other.name, "component": comp, "field": field, "was": target.name})
    mark_cleared(cleared)  # validate_assets reports these dangling fields until they are set again
    deleted = []
    for obj in targets:
        if not with_children:
            for child in list(obj.children):
                world = child.matrix_world.copy()
                child.parent = None
                child.matrix_world = world
        deleted.append(obj.name)
        bpy.data.objects.remove(obj, do_unlink=True)
    ids.invalidate_cache()
    out = {"deleted": deleted}
    if cleared:
        out["cleared_references"] = cleared
        out["hint"] = ("These component fields now point at no object; set them with set_component, or undo "
                       "to restore the deleted objects.")
    return out


# --- Components ------------------------------------------------------------------------


def _component_hint() -> str:
    known = registry.component_names()
    if not known:
        return ("The component registry is empty: start the dev server (play does it) so it writes "
                ".gamestudio/components.json, then retry.")
    return f"Known components: {', '.join(known)}. Call list_component_types for their fields."


def _field_summary(f: dict) -> str:
    text = f["type"]
    if "options" in f:
        text += "(" + "|".join(map(str, f["options"])) + ")"
    if "default" in f:
        text += f" = {json.dumps(f['default'])}"
    if "min" in f or "max" in f:
        text += f" [{f.get('min', '')}..{f.get('max', '')}]"
    if f.get("description"):
        text += f" - {f['description']}"
    return text


def _compact(entries: list) -> list:
    return [{"name": s["name"], **({"description": s["description"]} if s.get("description") else {}),
             **({"builtin": True} if s.get("builtin") else {}),
             "fields": {f["name"]: _field_summary(f) for f in s.get("fields", [])}} for s in entries]


_FIRST_SENTENCE = re.compile(r"^.+?[.!?](?=\s|$)", re.S)


def _first_sentence(text: str) -> str:
    text = (text or "").strip()
    match = _FIRST_SENTENCE.match(text)
    return match.group(0) if match else text


def _compact_shaders(entries: list) -> list:
    """Project shaders like components (with their category); the shader library's in one line each (name,
    category, the description's first sentence), since there are dozens: type gives one's fields."""
    out = []
    for s in entries:
        if s.get("builtin"):
            entry = {"name": s["name"], "category": s.get("category", "")}
            if s.get("description"):
                entry["description"] = _first_sentence(s["description"])
            entry["builtin"] = True
        else:
            entry = _compact([s])[0]
            if s.get("category"):
                entry["category"] = s["category"]
        out.append(entry)
    return out


@method("list_component_types")
def list_component_types(params):
    """The registry's components and game shaders (type: one of either, components first)."""
    schemas = registry.schemas()
    shader_schemas = registry.shader_schemas()
    one = params.get("type")
    if one:
        if registry.get_schema(one) is None and registry.get_shader_schema(one) is not None:
            return registry.get_shader_schema(one)
        _check_component(one)
        return registry.get_schema(one)
    verbose = bool(params.get("verbose"))
    out = {"count": len(schemas), "components": schemas if verbose else _compact(schemas),
           "shaders": shader_schemas if verbose else _compact_shaders(shader_schemas)}
    if not schemas:
        out["hint"] = _component_hint()
    warnings = registry.warnings()
    if warnings:
        out["warnings"] = warnings[:10]
    return out


def _check_component(name: str) -> None:
    if registry.get_schema(name) is None:
        from ..core import schema

        known = registry.component_names()
        raise BridgeError(f"Unknown component '{name}'.{schema.suggest(name, known)}", hint=_component_hint())


@method("add_component", mutating=True)
def add_component(params):
    many = params.get("components")
    if many is not None:
        if not isinstance(many, dict) or not many:
            raise BridgeError("components must be an object: {\"RigidBody\": {...values}, \"Collider\": {}}.",
                              code=INVALID_PARAMS)
        targets = [resolve(n, writable=True) for n in names_param(params, "objects", "object")]
        for comp, values in many.items():  # validate all before changing anything
            _check_component(comp)
            if values:
                components._coerce_all(registry.get_schema(comp), values)
        added = []
        for comp, values in many.items():
            for obj in targets:
                components.add_component(obj, comp, values or None)
            added.append(comp)
        return {"components": added, "objects": [o.name for o in targets],
                "values": {o.name: {c: components.get_values(o, c) for c in added} for o in targets[:20]}}
    name = param(params, "component", str)
    _check_component(name)
    values = params.get("values") or None
    targets = [resolve(n, writable=True) for n in names_param(params, "objects", "object")]
    # Validate on every target before changing any of them.
    for obj in targets:
        components.check_writable(obj)
    if values is not None:
        components._coerce_all(registry.get_schema(name), values)
    result = []
    for obj in targets:
        components.add_component(obj, name, values)
        result.append({"object": obj.name, "id": ids.get_id(obj),
                       "values": components.get_values(obj, name)})
    return {"component": name, "added": result}


@method("set_component", mutating=True)
def set_component(params):
    name = param(params, "component", str)
    _check_component(name)
    values = param(params, "values", dict)
    targets = [resolve(n, writable=True) for n in names_param(params, "objects", "object")]
    add_missing = bool(params.get("add_if_missing", False))
    for obj in targets:
        if not components.has_component(obj, name) and not add_missing:
            raise BridgeError(f"'{obj.name}' has no {name} component.",
                              hint="Call add_component first, or pass add_if_missing: true.")
    components._coerce_all(registry.get_schema(name), values)
    from .assets import forget_cleared

    result = []
    for obj in targets:
        if components.has_component(obj, name):
            components.set_values(obj, name, values)
            components._notify(obj)
        else:
            components.add_component(obj, name, values)
        forget_cleared(obj, name, set(values))  # an entity field a delete emptied is set (or left empty) on purpose
        result.append({"object": obj.name, "values": components.get_values(obj, name)})
    return {"component": name, "updated": result}


@method("remove_component", mutating=True)
def remove_component(params):
    name = param(params, "component", str)
    targets = [resolve(n, writable=True) for n in names_param(params, "objects", "object")]
    for obj in targets:
        if not components.has_component(obj, name):
            attached = components.list_components(obj)
            raise BridgeError(f"'{obj.name}' has no {name} component.",
                              hint=f"Attached: {', '.join(attached) or 'none'}.")
    from .assets import forget_cleared

    for obj in targets:
        components.remove_component(obj, name)
        forget_cleared(obj, name)
    return {"component": name, "removed_from": [o.name for o in targets]}


# --- Game shaders ----------------------------------------------------------------------


def material_shaders(obj) -> dict:
    """{material: {shader, slots, values?, missing_definition?}} for obj's slot materials that have a game
    shader; values holds only the fields that differ from their defaults."""
    out = {}
    for index, slot in enumerate(obj.material_slots):
        mat = slot.material
        name = shaders.get_shader(mat) if mat is not None else None
        if name is None:
            continue
        entry = out.get(mat.name)
        if entry is None:
            entry = out[mat.name] = {"shader": name, "slots": []}
            values = shaders.changed_values(mat)
            if values:
                entry["values"] = values
            if shaders.is_missing(mat):
                entry["missing_definition"] = True
        entry["slots"].append(index)
    return out


def _shader_hint() -> str:
    known = registry.shader_names()
    if not known:
        return ("The registry has no game shaders: write one in src/shaders/ (defineShader) and start the dev server "
                "(play does it) so it writes .gamestudio/components.json, then retry.")
    return f"Known game shaders: {', '.join(known)}. Call list_component_types for their fields."


def _find_material(name: str):
    """A material by name (a local one before a linked one of the same name) or by its full name."""
    found = next((m for m in bpy.data.materials if m.name == name and m.library is None), None)
    found = found or next((m for m in bpy.data.materials if m.name == name or m.name_full == name), None)
    if found is None:
        import difflib

        close = difflib.get_close_matches(name, [m.name for m in bpy.data.materials], n=3)
        raise BridgeError(f"No material named '{name}'.",
                          hint=f"Did you mean: {', '.join(close)}?" if close else
                          "get_object lists an object's materials; or pass objects (and slot) instead.")
    return found


def _slot_materials(targets, slot) -> list:
    """The materials in the objects' slots: every slot, or the given index (or indices). Each material once."""
    picked = slot if isinstance(slot, list) else None if slot is None else [slot]
    if picked is not None and (not picked or not all(isinstance(i, int) and not isinstance(i, bool) and i >= 0
                                                     for i in picked)):
        raise BridgeError("slot must be a slot index or a list of them, e.g. [0, 2].", code=INVALID_PARAMS)
    found = []
    for obj in targets:
        count = len(obj.material_slots)
        if not count:
            raise BridgeError(f"'{obj.name}' has no material slots.",
                              hint="Give it a material first (apply_material), or pass material.")
        for index in picked if picked is not None else range(count):
            if index >= count:
                raise BridgeError(f"'{obj.name}' has {count} material slot(s); there is no slot {index}.",
                                  code=INVALID_PARAMS)
            mat = obj.material_slots[index].material
            if mat is None:
                if picked is not None:
                    raise BridgeError(f"Material slot {index} of '{obj.name}' is empty.",
                                      hint="Assign a material to it first (apply_material with slot).")
                continue
            if mat not in found:
                found.append(mat)
    if not found:
        raise BridgeError("Those material slots are all empty.", hint="Assign materials first (apply_material).")
    return found


@method("set_material_shader", mutating=True)
def set_material_shader(params):
    """Set (or with shader: null, clear) the game shader that draws materials in the game, by material name or
    through objects' material slots. Every material is checked before any changes; one undo step. Colour fields
    take "#RRGGBB" / "#RRGGBBAA" (sRGB, made linear) besides linear [r, g, b, a]."""
    if (params.get("material") is None) == (params.get("objects") is None):
        raise BridgeError("Pass exactly one of material (a material name) or objects (whose slots to use).",
                          code=INVALID_PARAMS)
    if "shader" not in params:
        raise BridgeError("Missing parameter 'shader' (a game shader name, or null to clear it).", code=INVALID_PARAMS)
    shader = param(params, "shader", str, None) or None
    values = param(params, "values", dict, None)
    if params.get("material") is not None:
        if params.get("slot") is not None:
            raise BridgeError("slot goes with objects, not material.", code=INVALID_PARAMS)
        materials = [_find_material(param(params, "material", str))]
    else:
        targets = [resolve(n, writable=True) for n in names_param(params, "objects")]
        materials = _slot_materials(targets, params.get("slot"))
    if shader is not None and registry.shader_schema_ref(shader) is None and registry.shader_names():
        from ..core import schema

        raise BridgeError(f"Unknown game shader '{shader}'.{schema.suggest(shader, registry.shader_names())}",
                          hint=_shader_hint())
    if shader is not None and values:  # colour fields take "#RRGGBB" / "#RRGGBBAA" (sRGB) too, made linear
        from ..gameui import build as gameui_build

        try:
            values = gameui_build.linear_shader_values(shader, values, "values")
        except gameui_build.GameUIError as exc:
            raise BridgeError(exc.message, hint='Colours are "#RRGGBB" or "#RRGGBBAA" (sRGB) or linear '
                                                '[r, g, b, a] lists.', code=INVALID_PARAMS) from None
    warnings = []
    for mat in materials:  # validate all before changing any
        for warning in shaders.validate_shader(mat, shader, values):
            if warning not in warnings:
                warnings.append(warning)
    for mat in materials:
        shaders.set_shader(mat, shader, values)
    return {"materials": [{"name": m.name, "shader": shaders.get_shader(m), "values": shaders.get_values(m),
                           "users": shaders.users(m)[:50]} for m in materials],
            "warnings": warnings}


# --- Game ------------------------------------------------------------------------------


# The highest export revision per manifest in this session (see _monotonic_rev).
_revisions: dict = {}


def _manifest_path(scene) -> pathlib.Path:
    return project.export_dir(_require_root()) / f"{export.export_name(scene)}.scene.json"


def _monotonic_rev(scene) -> pathlib.Path:
    """Make the next export's revision higher than any before it.

    The counter lives in the scene (scene["gs_rev"]), so undo, restore_checkpoint and opening an older
    copy bring back an older value, and the next export would repeat a revision a game or a tool has
    already seen with other content. Start from the last export on disk and this session's highest.
    """
    path = _manifest_path(scene)
    floor = _revisions.get(str(path), 0)
    try:
        floor = max(floor, int(json.loads(path.read_text(encoding="utf-8")).get("rev", 0)))
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    if int(scene.get(export.REV_PROP, 0) or 0) < floor:
        scene[export.REV_PROP] = floor
    return path


def _export(scene=None, compress=False, force=False) -> dict:
    from ..core import gameui, gameui_export

    if gameui.is_ui_file():
        # A UI file's scenes are screens (docs/GAME-UI.md §4): export them, and play the start level's
        # export (made in a background Blender when it is missing, or with force).
        return gameui_export.play_export(_require_root(), force=force)
    scene = scene or bpy.context.scene
    path = _monotonic_rev(scene)
    result = export.export_scene(scene, compress=compress, force=force)
    _revisions[str(path)] = max(_revisions.get(str(path), 0), int(result["rev"]))
    ui_errors = _export_stale_ui(_require_root())  # a level plays the UI files' screens too
    return {**result, "ui_errors": ui_errors} if ui_errors else result


def _tier(result: dict) -> dict:
    """How the export was done (export.export_scene): {"tier"}, plus "reason" for a full export."""
    return {k: result[k] for k in ("tier", "reason") if result.get(k)}


# Not an undo step, like Blender's own Play: undo means "undo my last edit". Revisions keep counting
# up across undo and checkpoint restores (_monotonic_rev). Only what changed since the last export is
# exported again ("tier": reuse, layout or full); force: true always exports in full.
@method("export_scene")
def export_scene_method(params):
    _require_root()
    try:
        result = _export(compress=bool(params.get("compress")), force=bool(params.get("force")))
    except export.ExportError as exc:
        raise BridgeError(f"Export failed: {exc}") from None
    if "ui" in result:  # a UI file: its screens, and the start level's export to play
        return {**compact_export(result), **_tier(result), "ui": result["ui"],
                "scene_load": export.scene_load_message(result)}
    seconds = export.last_export().get("seconds")
    if params.get("verbose"):
        return {**result, "scene_load": export.scene_load_message(result), "seconds": seconds}
    return {**compact_export(result), **_tier(result), "scene_load": export.scene_load_message(result),
            "seconds": seconds, **({"ui_errors": result["ui_errors"]} if result.get("ui_errors") else {})}


@method("export_all")
def export_all_method(params):
    """Every level of the project: the open file's scenes here, every other scenes/*.blend in a background
    Blender (those files are only read). Each exported scene comes with its scene.load message."""
    _require_root()
    try:
        result = export.export_all()
    except export.ExportError as exc:
        raise BridgeError(f"Export failed: {exc}") from None
    for summary in result["scenes"]:
        if summary.get("scene") and summary.get("rev") is not None:
            path = str(project.export_dir(_require_root()) / f"{summary['scene']}.scene.json")
            _revisions[path] = max(_revisions.get(path, 0), int(summary["rev"]))
    scenes = [{k: s[k] for k in ("file", "scene", "rev", "entities") if k in s}
              | ({"warnings": s["warnings"][:10]} if s.get("warnings") else {}) for s in result["scenes"]]
    ui = [{"file": u["file"], "canvases": [{k: c[k] for k in ("canvas", "rev", "skipped") if k in c}
                                           for c in u["canvases"]]}
          | ({"warnings": u["warnings"][:10]} if u.get("warnings") else {}) for u in result.get("ui", [])]
    return {"scenes": scenes, "ui": ui, "errors": result["errors"],
            "scene_loads": [export.scene_load_message(s) for s in result["scenes"] if "rev" in s]}


def compact_export(result: dict) -> dict:
    """The essentials of an export result (verbose: true gives everything)."""
    out = {k: result[k] for k in ("glb", "manifest", "scene", "rev", "entities", "prefabs", "animations", "assets",
                                  "repaired") if k in result}
    size = result.get("size")
    if isinstance(size, dict):
        out["size"] = {"bytes": size.get("bytes"),
                       "top": [{"name": i.get("name"), "bytes": i.get("bytes")} for i in (size.get("top") or [])[:5]]}
        if size.get("compressed"):  # compress: true asked what npm run build ships (verbose adds per_file)
            out["size"]["compressed"] = size["compressed"]
    if result.get("warnings"):
        out["warnings"] = result["warnings"][:20]
    bake = result.get("bake")
    if isinstance(bake, dict):
        out["bake"] = {k: bake.get(k) for k in ("baked", "cached") if bake.get(k) is not None}
    for key, limit in (("textures", 10), ("hidden_meshes", 20)):
        value = result.get(key)
        if value:
            out[key] = value[:limit] if isinstance(value, list) else value
            if isinstance(value, list) and len(value) > limit:
                out[f"{key}_count"] = len(value)
    if result.get("instancing"):
        out["instancing"] = result["instancing"]
    if result.get("lightmaps"):  # baked lighting: counts, and the stale / missing lightmaps to bake again
        out["lightmaps"] = result["lightmaps"]
    return out


@method("devserver")
def devserver_method(params):
    from ..core import devserver

    action = param(params, "action", str, "status", choices=("status", "start", "stop"))
    root = _require_root()
    started = None
    if action == "start":
        try:
            started = devserver.start(root)
        except devserver.DevServerError as exc:
            raise BridgeError(f"Could not start the dev server: {exc}",
                              hint=f"See {devserver.log_path(root)}.") from None
    elif action == "stop":
        started = "stopped" if devserver.stop() else "not started by Blender"
    session = devserver.running_session(root)
    return {"action": action, "result": started, "running": session is not None,
            "starting": devserver.is_starting(), "url": (session or {}).get("url"),
            "last_error": devserver.last_error() or None, "log": devserver.log_path(root),
            "log_tail": devserver.log_tail(root, 8) if devserver.last_error() else None}


@method("play_in_blender")
def play_in_blender(params):
    """Blender's own Play: export (only what changed; force: true for a full export), then load and start the
    game in the Game View or a window."""
    from ..core import gameui_live, play

    _require_root()
    result = _export(force=bool(params.get("force")))  # from a level: stale UI files too (ui_errors)
    sent = play.request_play(result, start=True, open_window=bool(params.get("open_window", True)))
    if "ui" in result:  # played from a UI file: open the screen being edited once the level plays
        gameui_live.open_after_play(bpy.context.scene.name)
    return {"export": {"rev": result["rev"], "entities": result["entities"], "scene": result["scene"],
                       **_tier(result)},
            "sent_now": sent, "status": play.status() or None,
            **({"ui_errors": result["ui_errors"]} if result.get("ui_errors") else {})}


def _export_stale_ui(root) -> list:
    """An export from a level, as Blender's Play does (docs/GAME-UI.md §4): export the UI files whose screens
    are missing, older than their .blend or made from unsaved changes. Their failures never stop the export
    or the Play: [{file, error}]."""
    from ..core import gameui_export

    try:
        return gameui_export.export_stale(root)["errors"]
    except (OSError, RuntimeError) as exc:
        return [{"file": "ui/", "error": str(exc)}]


@method("link_status")
def link_status(params):
    client = link.get_link()
    return {"state": client.state, "connected": client.connected, "games": client.game_peers(),
            "game_state": client.game_state, "game_ready": client.game_ready, "stats": client.stats}



# --- Prefabs and animation clips ------------------------------------------------------------------


@method("set_prefab", mutating=True)
def set_prefab(params):
    from ..core import live

    name = param(params, "collection", str)
    enabled = bool(params.get("enabled", True))
    coll = bpy.data.collections.get(name)
    if coll is None:
        import difflib

        close = difflib.get_close_matches(name, [c.name for c in bpy.data.collections], n=3)
        raise BridgeError(f"No collection named '{name}'.",
                          hint=f"Did you mean: {', '.join(close)}?" if close else
                          "scene_summary lists the collections; create one with run_python.")
    if coll.library is not None:
        raise BridgeError(f"Collection '{name}' is linked from a library and cannot be changed here.")
    export.set_prefab(coll, enabled)
    live.schedule_rebuild(f"prefab '{name}' {'marked' if enabled else 'unmarked'}")
    instances = [o.name for o in bpy.context.scene.objects
                 if o.instance_type == "COLLECTION" and o.instance_collection == coll]
    return {"collection": coll.name, "prefab": export.is_prefab(coll),
            "objects": [o.name for o in coll.all_objects][:200],
            "in_scene": coll.name in {c.name for c in bpy.context.scene.collection.children_recursive},
            "instances": instances[:200], "prefabs": _prefab_names(),
            "hint": "Spawn it in game code with ctx.spawn('<name>', {position}), or place collection instances of it "
                    "in Blender (run_python: bpy.data.objects.new(name, None) with instance_type 'COLLECTION')."}


def _fps(scene) -> float:
    return scene.render.fps / (scene.render.fps_base or 1.0)


def _action_info(action, fps: float) -> dict:
    start, end = action.frame_range
    return {"name": action.name, "frames": [round(start, 3), round(end, 3)],
            "seconds": round(max(0.0, end - start) / fps, 4) if fps else None}


def animation_info(obj) -> dict | None:
    """An object's action and NLA tracks, and the clips the exporter makes from them (§4.1):
    the active action plus the action of every track with exactly one unmuted strip."""
    anim = obj.animation_data
    if anim is None or (anim.action is None and not len(anim.nla_tracks)):
        return None
    fps = _fps(bpy.context.scene)
    clips = []
    tracks = []
    if anim.action is not None:
        clips.append({**_action_info(anim.action, fps), "from": "active action"})
    for track in anim.nla_tracks:
        strips = [{"action": s.action.name if s.action else None, "mute": s.mute,
                   "frames": [round(s.frame_start, 3), round(s.frame_end, 3)]} for s in track.strips]
        tracks.append({"name": track.name, "mute": track.mute, "strips": strips})
        live_strips = [s for s in track.strips if s.action is not None and not s.mute]
        if len(live_strips) == 1 and not any(c["name"] == live_strips[0].action.name for c in clips):
            clips.append({**_action_info(live_strips[0].action, fps), "from": f"NLA track '{track.name}'"})
    out = {"action": anim.action.name if anim.action else None, "nla_tracks": tracks, "clips": clips}
    warnings = export.check_nla([obj])
    if warnings:
        out["warnings"] = warnings
    return out


def _exported_clips() -> dict:
    """{entity name: [clip names]} and the clip table from the last export's manifest, if any."""
    root = project.project_root()
    if root is None:
        return {}
    path = project.export_dir(root) / f"{export.export_name(bpy.context.scene)}.scene.json"
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    by_name = {e.get("name"): e.get("animations") or [] for e in manifest.get("entities", [])}
    return {"rev": manifest.get("rev"), "clips": manifest.get("animations") or [], "entities": by_name}


@method("list_clips")
def list_clips(params):
    names = params.get("objects")
    if names:
        targets = [resolve(n) for n in names_param(params, "objects")]
    else:
        targets = [o for o in bpy.context.scene.objects if animation_info(o) is not None]
    exported = _exported_clips()
    objects = []
    for obj in targets[:200]:
        info = animation_info(obj) or {"action": None, "nla_tracks": [], "clips": []}
        entry = {"object": obj.name, **info}
        if exported:
            entry["exported_clips"] = exported["entities"].get(obj.name, [])
        objects.append(entry)
    out = {"fps": round(_fps(bpy.context.scene), 3), "objects": objects,
           "actions": [a.name for a in bpy.data.actions][:200]}
    if exported:
        out["last_export"] = {"rev": exported["rev"], "clips": exported["clips"]}
    if not objects:
        out["hint"] = ("No object here has an action or NLA tracks. Keyframe one with run_python "
                       "(obj.keyframe_insert(...)), or use a kit that adds clips.")
    return out


def _prefab_names() -> list:
    """Template names as the export writes them: a duplicate name gets its full name ("Crate [props.blend]")."""
    return list(export.prefab_names(export.prefab_collections()).values())
