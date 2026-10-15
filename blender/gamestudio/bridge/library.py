# SPDX-License-Identifier: GPL-3.0-or-later
"""Bridge methods for asset libraries (gamestudio/assetlib.py) and the scene twin (core/twin.py).

Library files (<project>/assets/<library>.blend) are written by a background Blender, so that part
of asset_add, and component edits on a library asset (asset_edit_components), are not undo steps
in the open level; the changes in the open level (move=True, spawn_only=True, placing, replacing,
making local) are. Instead, every library write is backed up first to
<project>/.gamestudio/library-backups/<library>/ (the last BACKUPS_KEPT per library), and
asset_library_restore puts a backup back.
"""

import datetime
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile

import bpy

from .api import BridgeError, method, names_param, param, resolve, rnd, vec
from .server import INVALID_PARAMS

BACKUP_DIR = "library-backups"
BACKUPS_KEPT = 20


def _lib():
    try:
        from .. import assetlib
    except Exception as exc:
        raise BridgeError(f"The asset library module failed to load: {type(exc).__name__}: {exc}",
                          hint="Use run_python meanwhile, and report the error.") from None
    return assetlib


# --- Backups of library files -------------------------------------------------------------------------


def _root() -> pathlib.Path:
    from ..core import project

    root = project.project_root()
    if root is None:
        raise BridgeError("The open .blend is not inside a GameStudio project.",
                          hint="Asset libraries live in <project>/assets/; open a level of the project first.")
    return pathlib.Path(root)


def _backup_folder(path) -> pathlib.Path:
    """<project>/.gamestudio/library-backups/<library path under assets/, '/' as '--'>/"""
    from ..core import project

    lib = _lib()
    root = _root()
    try:
        key = pathlib.Path(path).resolve().relative_to(lib.library_dir(root).resolve()).with_suffix("").as_posix()
    except ValueError:
        key = pathlib.Path(path).stem
    return project.gs_dir(root) / BACKUP_DIR / key.replace("/", "--")


def _sidecars(folder: pathlib.Path, backup_id: str) -> list:
    return [folder / f"{backup_id}{suffix}" for suffix in (".json", ".blend", ".scene.json", ".cats.txt")]


def _manifests(folder: pathlib.Path) -> list:
    """The backup manifests in one library's backup folder, newest first, each with "path"."""
    found = []
    for manifest in folder.glob("*.json") if folder.is_dir() else ():
        if manifest.name.endswith(".scene.json"):
            continue
        try:
            entry = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(entry, dict) or not isinstance(entry.get("id"), str):
            continue
        blend = folder / f"{entry['id']}.blend"
        entry["path"] = str(blend if blend.is_file() else manifest)
        found.append(entry)
    return sorted(found, key=lambda e: e["id"], reverse=True)


def backups(path) -> list:
    """The backups of one library file, newest first."""
    return _manifests(_backup_folder(path))


def backup_library(path, tool: str, asset=None, catalogs: bool = False, keep=None) -> dict:
    """Copy a library file (and its twin, and the catalog file when catalogs may change) before a write.

    A library that does not exist yet gets a backup that says so: restoring it removes the file again.
    Older backups beyond BACKUPS_KEPT are deleted (never `keep`). Returns the backup's manifest plus "path".
    """
    from ..core import twin

    lib = _lib()
    path = pathlib.Path(path)
    folder = _backup_folder(path)
    folder.mkdir(parents=True, exist_ok=True)
    now = datetime.datetime.now()
    slug = "".join(c if c.isalnum() else "-" for c in tool.lower()).strip("-") or "write"
    backup_id = f"{now:%Y%m%d-%H%M%S}-{now.microsecond // 1000:03d}-{slug}"
    entry = {"format": "gamestudio.library-backup", "id": backup_id, "library": lib._rel(path), "tool": tool,
             "asset": asset, "created": now.astimezone().isoformat(timespec="seconds"), "absent": not path.is_file()}
    if path.is_file():
        shutil.copy2(path, folder / f"{backup_id}.blend")
        entry["bytes"] = path.stat().st_size
        entry["stamp"] = [path.stat().st_mtime_ns, path.stat().st_size]
    twin_file = twin.twin_path(path)
    if twin_file.is_file():
        shutil.copy2(twin_file, folder / f"{backup_id}.scene.json")
        entry["twin"] = True
    cats = lib.library_dir(_root()) / lib.CATALOG_FILE
    if catalogs and cats.is_file():
        shutil.copy2(cats, folder / f"{backup_id}.cats.txt")
        entry["catalogs"] = True
    (folder / f"{backup_id}.json").write_text(json.dumps(entry, indent=2), encoding="utf-8")
    for old in [e for e in backups(path) if e["id"] != keep][BACKUPS_KEPT:]:
        for file in _sidecars(folder, old["id"]):
            file.unlink(missing_ok=True)
    entry["path"] = str(folder / f"{backup_id}.blend" if path.is_file() else folder / f"{backup_id}.json")
    return entry


def _unchanged(path, entry) -> bool:
    """True when a write that failed left the library file as the backup found it."""
    path = pathlib.Path(path)
    if entry.get("absent"):
        return not path.exists()
    return path.is_file() and [path.stat().st_mtime_ns, path.stat().st_size] == entry.get("stamp")


def _drop_backup(path, entry) -> None:
    for file in _sidecars(_backup_folder(path), entry["id"]):
        file.unlink(missing_ok=True)


def _written(path, entry) -> dict:
    """What a writing tool's result says about its backup and how to take the write back."""
    lib = _lib()
    restore = {"library": lib._rel(path), "backup": entry["id"]}
    return {"library_backup": entry["path"],
            "restore": f"asset_library_restore {json.dumps(restore)}",
            "note": ("The library file was written on disk, which undo does not cover; it was backed up first: "
                     "undo with asset_library_restore (the call above puts this backup back).")}


def _guarded_write(path, tool: str, write, asset=None, catalogs: bool = False):
    """Back up `path`, run write(); a failed write that changed nothing drops its backup again."""
    entry = backup_library(path, tool, asset=asset, catalogs=catalogs)
    try:
        result = write()
    except Exception:
        if _unchanged(path, entry):
            _drop_backup(path, entry)
        raise
    return result, entry


@method("asset_list")
def asset_list(params):
    lib = _lib()
    libraries = lib.list_libraries()
    assets = lib.list_assets(params.get("library"), catalog=params.get("catalog"), tag=params.get("tag")) \
        if libraries or params.get("library") else []
    out = {"libraries": libraries, "count": len(assets), "assets": assets}
    if params.get("backups"):
        from ..core import project

        folder = project.gs_dir(_root()) / BACKUP_DIR
        wanted = lib._rel(lib.library_path(params["library"])) if params.get("library") else None
        listed = [{k: e[k] for k in ("library", "id", "tool", "asset", "created", "bytes", "absent")
                   if e.get(k) not in (None, False)}
                  for sub in (folder.iterdir() if folder.is_dir() else ()) if sub.is_dir()
                  for e in _manifests(sub) if wanted is None or e.get("library") == wanted]
        out["backups"] = sorted(listed, key=lambda e: e["id"], reverse=True)[:100]
        out["backup_folder"] = str(folder)
    if not libraries:
        out["hint"] = ('No asset libraries yet: asset_add {"objects": [...], "library": "props"} creates '
                       "assets/props.blend.")
    return out


@method("asset_add", mutating=True)
def asset_add(params):
    lib = _lib()
    objects = names_param(params, "objects", "object")
    library = param(params, "library", str, "props")
    tags = params.get("tags") or ()
    if isinstance(tags, str):
        tags = [tags]
    move = bool(params.get("move", False))
    prefab = bool(params.get("prefab", True))
    spawn_only = bool(params.get("spawn_only", False))
    sources = []
    if spawn_only:
        if move:
            raise BridgeError("spawn_only and move both decide what happens to the objects; pass one.",
                              hint="spawn_only removes them and makes the asset spawnable; move puts an instance "
                                   "in their place.", code=INVALID_PARAMS)
        if not prefab:
            raise BridgeError("spawn_only makes the asset spawnable, which needs prefab: true.", code=INVALID_PARAMS)
        if not bpy.data.filepath:
            raise BridgeError("Save the level first: spawn_only links the new asset back into it.",
                              hint="save({path}) the scene into the project's scenes/ folder.")
        for name in objects:  # checked before the library is written: they are removed afterwards
            obj = resolve(name, writable=True)
            for o in (obj, *obj.children_recursive):
                if o not in sources:
                    sources.append(resolve(o.name, writable=True))
    path = lib.library_path(library)
    catalog = str(params.get("catalog") or "")
    added, backup = _guarded_write(path, "asset_add", lambda: lib.add_to_library(
        objects, library, params.get("name"),
        move=move, separate=bool(params.get("separate", False)),
        catalog=catalog, tags=tags, description=str(params.get("description") or ""),
        prefab=prefab), asset=params.get("name") or objects[0], catalogs=bool(catalog))
    out = {"assets": added, **_written(path, backup)}
    if move:
        for entry in added:  # the instance shares its name with the asset's object: every export would warn
            inst = bpy.data.objects.get(entry.get("instance") or "")
            inner = {o.name for o in inst.instance_collection.all_objects} if inst is not None and \
                inst.instance_collection is not None else set()
            if inst is not None and inst.name in inner:
                inst.name = lib._next_name(entry["asset"])
                entry["instance"] = inst.name
        out["level"] = "The objects in the open level were replaced by instances (one undo step)."
    if spawn_only:
        removed = sorted(o.name for o in sources)
        holders = {c for o in sources for c in o.users_collection}
        cleared = _remove_objects(sources)
        out["removed"] = removed
        emptied = _drop_emptied_prefabs(holders, {a["asset"] for a in added})
        if emptied:
            out["removed_collections"] = emptied
        colls = [lib.make_spawnable(a["asset"], a["library"]) for a in added]
        out["spawnable"] = [c.name for c in colls]
        out["level"] = ("The objects were removed from the open level and the asset is linked as a spawnable "
                        "template, not placed (one undo step). Export or play to use it.")
        if cleared:
            out["cleared_references"] = cleared
    if spawn_only or prefab:
        _spawn_report(out, [(a["asset"], path) for a in added])
    return out


def _drop_emptied_prefabs(holders, names: set) -> list:
    """Remove the local prefab collections that held the moved objects and are empty now. Left behind,
    such a collection keeps the plain prefab name, so the library asset of the same name would export
    as "Grunt [actors.blend]" and ctx.spawn('Grunt') would find nothing."""
    from ..core import export

    gone = []
    for coll in list(holders):
        if (coll.library is None and coll not in {s.collection for s in bpy.data.scenes} and not coll.all_objects
                and not coll.children and (export.is_prefab(coll) or coll.name in names)):
            gone.append(coll.name)
            bpy.data.collections.remove(coll)
    return sorted(gone)


def _spawn_name(lib, name, path):
    """The name ctx.spawn() takes for a library asset in the open level, as the export names its
    template, or None when the export has no template for it."""
    from ..core import export

    coll = lib._linked_collection(path, name)
    if coll is None or not export.is_prefab(coll):
        return None
    return export.prefab_names(export.prefab_collections()).get(coll.as_pointer())


def _spawn_report(out: dict, assets) -> None:
    """spawn calls with the names the export will use, and a warning when a name is taken."""
    lib = _lib()
    calls, clashes = [], []
    for name, path in assets:
        spawn = _spawn_name(lib, name, path)
        if spawn is None:
            continue
        calls.append(f"ctx.spawn('{spawn}', {{ position: [x, y, z] }})")
        if spawn != name:
            clashes.append(f"'{name}' exports as '{spawn}': another prefab in this level is called '{name}'. "
                           "Rename or unmark that one (set_prefab enabled: false) to spawn this as "
                           f"ctx.spawn('{name}').")
    if calls:
        out["spawn"] = calls
    if clashes:
        out["warnings"] = out.get("warnings", []) + clashes


def _remove_objects(objs) -> list:
    """Delete objects from the open level; entity fields elsewhere that pointed at them are cleared."""
    from ..core import ids
    from .assets import entity_references, mark_cleared

    doomed = set(objs)
    names = {o: o.name for o in objs}
    cleared = []
    for other in bpy.context.scene.objects:
        if other in doomed:
            continue
        for comp, field, target in entity_references(other):
            if target is not None and target in doomed:
                cleared.append({"object": other.name, "component": comp, "field": field, "was": names[target]})
    mark_cleared(cleared)  # validate_assets reports these dangling fields until they are set again
    for obj in objs:
        bpy.data.objects.remove(obj, do_unlink=True)
    ids.invalidate_cache()
    return cleared


@method("asset_place", mutating=True)
def asset_place(params):
    lib = _lib()
    asset = param(params, "asset", str)
    location = vec(params, "location", default=[0.0, 0.0, 0.0])
    rotation = vec(params, "rotation_deg", default=[0.0, 0.0, 0.0])
    scale = params.get("scale", 1.0)
    if not isinstance(scale, (int, float)) or isinstance(scale, bool):
        scale = vec(params, "scale")
    inst = lib.place_asset(asset, params.get("library"), location=location, rotation=rotation, scale=scale,
                           name=params.get("name"), collection=params.get("collection"))
    bpy.context.view_layer.update()
    info = lib.asset_of(inst) or {}
    return {"instance": inst.name, "asset": info.get("asset", asset), "library": info.get("library"),
            "location": rnd(inst.location), "hint": "Per-copy components go on this instance (add_component)."}


@method("asset_replace", mutating=True)
def asset_replace(params):
    lib = _lib()
    obj = lib.replace_asset(param(params, "instance", str), param(params, "asset", str), params.get("library"))
    return {"instance": obj.name, **(lib.asset_of(obj) or {})}


@method("asset_make_local", mutating=True)
def asset_make_local(params):
    lib = _lib()
    copies = lib.make_local(param(params, "instance", str))
    return {"instance": param(params, "instance", str), "local_objects": [o.name for o in copies],
            "hint": "The empty is now the parent of editable copies; it no longer follows the library."}


@method("asset_spawnable", mutating=True)
def asset_spawnable(params):
    lib = _lib()
    coll = lib.make_spawnable(param(params, "asset", str), params.get("library"))
    out = {"asset": coll.name,
           "hint": "Exported as a prefab template only (not placed in the level). Export or play to use it."}
    _spawn_report(out, [(coll.name, lib._lib_abspath(coll.library))])
    if out.get("spawn"):
        out["spawn"] = out["spawn"][0]  # one asset: one call, as before
    return out


# --- Editing a library asset's components ---------------------------------------------------------

# Runs in a background Blender on the library file, like assetlib's writer. The add-on's property
# groups and the open Blender's component registry are registered there, so values go through the
# same component API (validation, coercion, entity names) as in the open level. Nothing is saved on
# an error.
_EDIT_WORKER = r'''
import importlib, json, sys
import bpy

def main():
    args = json.loads(open(sys.argv[sys.argv.index("--") + 1], encoding="utf-8").read())
    bpy.context.preferences.filepaths.save_version = 0
    bpy.ops.wm.open_mainfile(filepath=args["library"], load_ui=False)
    sys.path.insert(0, args["addon"]["path"])
    pkg = args["addon"]["package"]
    props = importlib.import_module(pkg + ".core.props")
    registry = importlib.import_module(pkg + ".core.registry")
    components = importlib.import_module(pkg + ".core.components")
    props.register()
    registry._apply(args["registry"])
    coll = next((c for c in bpy.data.collections if c.name == args["asset"] and c.library is None), None)
    if coll is None:
        return {"error": f"the library has no asset {args['asset']!r}"}
    members = {o.name: o for o in coll.all_objects}
    done = []
    try:
        for edit in args["edits"]:
            obj = members[edit["object"]]
            for name, values in edit.get("add", {}).items():
                components.add_component(obj, name, values or None)
            for name, values in edit.get("set", {}).items():
                components.set_values(obj, name, values)
            for name in edit.get("remove", []):
                components.remove_component(obj, name)
            changed = [*edit.get("add", {}), *edit.get("set", {})]
            entry = {"object": obj.name}
            if changed:
                entry["values"] = {n: components.get_values(obj, n) for n in changed}
            if edit.get("remove"):
                entry["removed"] = edit["remove"]
            entry["components"] = components.list_components(obj)
            done.append(entry)
    except ValueError as exc:
        return {"error": str(exc)}
    bpy.ops.wm.save_as_mainfile(filepath=args["library"])
    try:
        twin = importlib.import_module(pkg + ".core.twin").write_twin(args["library"])
    except Exception as exc:
        twin = {"error": f"{type(exc).__name__}: {exc}"}
    return {"objects": done, "twin": twin}

print("GS-RESULT " + json.dumps(main(), default=str), flush=True)
'''


def _run_edit_worker(args: dict) -> dict:
    lib = _lib()
    with tempfile.TemporaryDirectory(prefix="gs_assetedit_") as tmp:
        argfile = pathlib.Path(tmp) / "args.json"
        argfile.write_text(json.dumps(args), encoding="utf-8")
        env = dict(os.environ, BLENDER_USER_RESOURCES=str(pathlib.Path(tmp) / "profile"))
        cmd = [bpy.app.binary_path, "--background", "--factory-startup", "--python-exit-code", "1",
               "--python-expr", _EDIT_WORKER, "--", str(argfile)]
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                                  env=env, timeout=lib.WORKER_TIMEOUT, creationflags=flags)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise BridgeError(f"Could not run Blender to write {args['library']}: {exc}") from None
    for line in proc.stdout.splitlines():
        if line.startswith(lib.RESULT_TAG):
            return json.loads(line[len(lib.RESULT_TAG):])
    tail = "\n".join((proc.stderr or proc.stdout).strip().splitlines()[-8:])
    raise BridgeError(f"Writing {args['library']} failed (exit code {proc.returncode}); it was not changed:\n{tail}")


def _asset_members(path, name) -> tuple:
    """({object name: [component names]}, [top object names]) of a library asset, read without linking it."""
    from ..core import twin

    with bpy.data.temp_data() as data:
        with data.libraries.load(str(path), link=True) as (_src, dst):
            dst.collections = [name]
        coll = dst.collections[0]
        if coll is None:
            raise BridgeError(f"Could not read asset '{name}' from {path}.")
        objs = list(coll.all_objects)
        members = {o.name: [c for c, _enabled in twin.component_refs(o)] for o in objs}
        tops = sorted(o.name for o in objs if o.parent not in objs)
    return members, tops


def _checked_values(comp: str, values) -> dict:
    """Values checked against the registry here (fast errors); entity fields name objects in the library,
    so the background Blender resolves those."""
    from ..core import components, registry
    from .methods import _check_component

    _check_component(comp)
    if not values:
        return {}
    if not isinstance(values, dict):
        raise BridgeError(f"Values for {comp} must be an object of field -> value.", code=INVALID_PARAMS)
    entry = registry.get_schema(comp)
    entity = {f["name"] for f in entry["fields"] if f["type"] == "entity"}
    components._coerce_all(entry, {k: v for k, v in values.items() if k not in entity})
    return values


@method("asset_edit_components", mutating=True, undo="custom")
def asset_edit_components(params):
    """add_component / set_component / remove_component on the objects of a library asset (op)."""
    from ..core import registry

    lib = _lib()
    asset = param(params, "asset", str)
    op = param(params, "op", str, "set", choices=("add", "set", "remove"))
    path, name = lib.find_asset(asset, params.get("library"))
    rel = lib._rel(path)
    if lib._same_file(path, bpy.data.filepath):
        raise BridgeError(f"{rel} is the open file.",
                          hint="Edit its objects with add_component/set_component without asset, then save.")
    members, tops = _asset_members(path, name)
    if params.get("objects") is not None:
        targets = names_param(params, "objects")
    elif len(tops) == 1:
        targets = tops
    else:
        raise BridgeError(f"Asset '{name}' has several top objects; pass objects.",
                          hint=f"Objects in the asset: {', '.join(sorted(members))}.", code=INVALID_PARAMS)
    unknown = [t for t in targets if t not in members]
    if unknown:
        raise BridgeError(f"Asset '{name}' has no object {', '.join(map(repr, unknown))}.",
                          hint=f"Objects in the asset: {', '.join(sorted(members))}.")

    if op == "add":
        many = params.get("components")
        if many is not None:
            if not isinstance(many, dict) or not many:
                raise BridgeError('components must be an object: {"RigidBody": {...values}, "Collider": {}}.',
                                  code=INVALID_PARAMS)
            wanted = {c: _checked_values(c, v) for c, v in many.items()}
        else:
            comp = param(params, "component", str)
            wanted = {comp: _checked_values(comp, params.get("values"))}
        edits = [{"object": t, "add": wanted} for t in targets]
    elif op == "set":
        comp = param(params, "component", str)
        values = _checked_values(comp, param(params, "values", dict))
        add_missing = bool(params.get("add_if_missing", False))
        edits = []
        for t in targets:
            if comp in members[t]:
                edits.append({"object": t, "set": {comp: values}})
            elif add_missing:
                edits.append({"object": t, "add": {comp: values}})
            else:
                raise BridgeError(f"'{t}' in asset '{name}' has no {comp} component.",
                                  hint="Add it first (add_component with asset), or pass add_if_missing: true. "
                                       f"Attached: {', '.join(members[t]) or 'none'}.")
    else:
        comp = param(params, "component", str)
        for t in targets:
            if comp not in members[t]:
                raise BridgeError(f"'{t}' in asset '{name}' has no {comp} component.",
                                  hint=f"Attached: {', '.join(members[t]) or 'none'}.")
        edits = [{"object": t, "remove": [comp]} for t in targets]

    def write():
        result = _run_edit_worker({"library": str(path), "asset": name, "edits": edits,
                                   "registry": registry.schemas(), "addon": lib._addon_location()})
        if result.get("error"):
            raise BridgeError(f"{rel} was not changed: {result['error']}")
        return result

    result, backup = _guarded_write(path, f"{op}_component", write, asset=name)
    reloaded = _reload(path)
    spawn = _spawn_name(lib, name, path)  # placed or made spawnable: the export has a template either way
    out = {"asset": name, "library": rel, "op": op, "objects": result["objects"], **_written(path, backup),
           "instances": [o.name for o in lib.instances(name, path)][:50], "spawnable": spawn is not None,
           **({"spawn_name": spawn} if spawn else {})}
    if reloaded:
        out["hint"] = ("Reloaded: this level's instances and spawns use the new values. Export or play to send "
                       "them to the game.")
    else:
        out["hint"] = "This level does not link the asset; levels that do pick it up when they load or reload it."
    return out


def _reload(path) -> bool:
    """Reload a library in the open level (instances and spawn templates follow); False if it is not linked."""
    lib = _lib()
    try:
        reloaded = bool(lib.reload_libraries([path]))
    except RuntimeError:  # the file is gone (a restore of "no library yet")
        reloaded = False
    lib._listing_cache.clear()
    return reloaded


@method("asset_library_restore", mutating=True, undo="custom")
def asset_library_restore(params):
    """Put a library file back as a backup has it (default: the latest), backing up the current one first."""
    from ..core import twin

    lib = _lib()
    path = lib.library_path(param(params, "library", str))
    rel = lib._rel(path)
    if lib._same_file(path, bpy.data.filepath):
        raise BridgeError(f"{rel} is the open file.", hint="Open a level that links it first, then restore.")
    found = backups(path)
    if not found:
        raise BridgeError(f"No backups of {rel}.",
                          hint='asset_list {"backups": true} lists the backups of every library.')
    wanted = params.get("backup")
    if wanted is None:
        entry = found[0]
    else:
        matches = [e for e in found if e["id"] == wanted] or [e for e in found if e["id"].startswith(str(wanted))]
        if len(matches) != 1:
            raise BridgeError(f"{'No' if not matches else 'More than one'} backup of {rel} matches '{wanted}'.",
                              hint=f"Backups, newest first: {', '.join(e['id'] for e in found[:10])}.")
        entry = matches[0]
    folder = _backup_folder(path)
    current = backup_library(path, "asset_library_restore", catalogs=bool(entry.get("catalogs")), keep=entry["id"])
    if entry.get("absent"):
        path.unlink(missing_ok=True)
        twin.twin_path(path).unlink(missing_ok=True)
    else:
        source = folder / f"{entry['id']}.blend"
        if not source.is_file():
            _drop_backup(path, current)
            raise BridgeError(f"The backup file {source} is missing.")
        _replace(source, path)
        if entry.get("twin"):
            _replace(folder / f"{entry['id']}.scene.json", twin.twin_path(path))
    if entry.get("catalogs"):
        _merge_catalogs(folder / f"{entry['id']}.cats.txt", lib.library_dir(_root()) / lib.CATALOG_FILE)
    reloaded = _reload(path)
    undo = {"library": rel, "backup": current["id"]}
    out = {"restored": entry["id"], "library": rel, "from": {k: entry.get(k) for k in ("tool", "asset", "created")},
           "removed_library": bool(entry.get("absent")), "reloaded": reloaded,
           "backup_of_replaced": current["path"], "undo": f"asset_library_restore {json.dumps(undo)}"}
    if entry.get("absent"):
        out["hint"] = ("The library did not exist before that write, so it was removed. Levels that link it now "
                       "show missing data: undo their part (e.g. the asset_add move or spawn_only step).")
    else:
        out["hint"] = ("This level's instances and spawns use the restored file now." if reloaded else
                       "This level does not link it; levels that do pick it up when they load or reload it.")
    return out


def _replace(source: pathlib.Path, target: pathlib.Path) -> None:
    """Copy source over target in one step (a partial copy never replaces the file)."""
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(f".{target.name}.restoring")
    shutil.copy2(source, temp)
    os.replace(temp, target)


def _merge_catalogs(backup: pathlib.Path, current: pathlib.Path) -> None:
    """Catalogs only ever get added and every library in assets/ shares the file: restore the backup's
    entries without dropping any added since."""
    if not backup.is_file():
        return
    if not current.is_file():
        _replace(backup, current)
        return
    have = current.read_text(encoding="utf-8").splitlines()
    ids = {line.split(":", 1)[0] for line in have if ":" in line and not line.startswith("#")}
    extra = [line for line in backup.read_text(encoding="utf-8").splitlines()
             if ":" in line and not line.startswith("#") and line.split(":", 1)[0] not in ids]
    if extra:
        current.write_text("\n".join(have + extra) + "\n", encoding="utf-8")


@method("asset_reload")
def asset_reload(params):
    lib = _lib()
    libraries = params.get("libraries")
    if libraries is not None and (not isinstance(libraries, list) or not all(isinstance(x, str) for x in libraries)):
        raise BridgeError("libraries must be a list of library names.", code=INVALID_PARAMS)
    return {"reloaded": lib.reload_libraries(libraries)}


@method("scene_twin")
def scene_twin(params):
    """The twin text of the open file (unsaved changes included), not the one on disk."""
    from ..core import project, twin

    blend = bpy.data.filepath
    if not blend:
        root = project.project_root()
        blend = str((root / "scenes" / "untitled.blend") if root else "untitled.blend")
    previous = twin.read_twin(blend) if bpy.data.filepath else None
    text = twin.render(twin.build(blend, previous))
    return {"path": str(twin.twin_path(blend)) if bpy.data.filepath else None, "text": text}
