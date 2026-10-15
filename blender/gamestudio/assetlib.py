# SPDX-License-Identifier: GPL-3.0-or-later
"""Asset libraries: assets reused across levels, kept in <project>/assets/<library>.blend.

An asset is a collection in a library file, marked as a Blender asset (preview, catalog, tags) and
as a GameStudio prefab (custom property gs_prefab, docs/SPEC.md §4.4). Levels link the collection
and place it with collection-instance empties: the prefab pattern. The export writes the asset's
objects, with their components, once as a template, and the runtime builds each instance from it
(entity ids "<instance id>/<template id>"); games can also ctx.spawn('<asset name>') it. Per-copy
components go on the instance empty.

Library files are written by a background Blender (bpy.app.binary_path, factory settings, a
throw-away profile), so the open level, its undo history and the user's settings are never
touched. Writing a library takes a second or two. When the library is the open file, it is edited
in place and saved instead.

The API (agents use it through run_python as gs.assetlib):

    library_path(name) / list_libraries() / create_library(name) / open_library(name)
    add_to_library(objects, library="props", name=None, *, move=False, separate=False, catalog="",
                   tags=(), description="", prefab=True, preview=True) -> [ {asset, library, ...} ]
    list_assets(library=None, *, catalog=None, tag=None) -> [ {name, library, catalog, tags, ...} ]
    place_asset(asset, library=None, *, location, rotation (degrees), scale, name, collection) -> Object
    make_spawnable(asset, library=None) -> Collection   (linked but not placed, for ctx.spawn)
    replace_asset(instance, asset, library=None) -> Object
    reload_libraries(libraries=None) -> [library paths]
    make_local(instance) -> [Objects]
    instances(asset=None, library=None) -> [Objects] / asset_of(obj) -> dict | None

Errors are AssetLibError (a ValueError) with a message for the user or agent.

UI: the Game Assets popover in the 3D View header (Object Mode), listing each library's assets with
Place (at the 3D cursor) and Replace (the selected instances); the Asset Library menu of the Game
menu; and the same list in the sidebar's Game tab when the sidebar preference is on.
"""

import json
import math
import os
import pathlib
import re
import subprocess
import sys
import tempfile
import uuid

import bpy
from bpy.props import BoolProperty, EnumProperty, StringProperty
from mathutils import Euler, Matrix, Vector

from . import prefs
from .core import components, export, ids, project, registry, twin
from .core.log import get_logger

log = get_logger(__name__)

LIBRARY_DIR = "assets"
CATALOG_FILE = twin.CATALOG_FILE
LIBRARY_SCENE = "Assets"
SPAWN_COLLECTION = "Spawnable"
STAGING_PREFIX = "gs_staging_"
PREVIEW_SIZE = 128
WORKER_TIMEOUT = 180.0
RESULT_TAG = "GS-RESULT "
# Fixed namespace so a catalog path always gets the same UUID (stable diffs, no merge conflicts).
CATALOG_NAMESPACE = uuid.UUID("6f3d2f8e-4c1b-5a7e-9d20-1b6f0c9a7e31")
RENDERABLE = {"MESH", "CURVE", "SURFACE", "META", "FONT", "CURVES", "POINTCLOUD", "VOLUME", "GREASEPENCIL"}


class AssetLibError(ValueError):
    """An asset library operation failed; the message says why and what to do."""


# --- Paths -------------------------------------------------------------------------------------


def _root(root=None) -> pathlib.Path:
    root = pathlib.Path(root) if root is not None else project.project_root()
    if root is None:
        raise AssetLibError("Save the level inside a GameStudio project (a folder with gamestudio.json) first; "
                            "asset libraries live in <project>/assets/.")
    return root


def library_dir(root=None) -> pathlib.Path:
    return _root(root) / LIBRARY_DIR


def library_path(name="props", root=None) -> pathlib.Path:
    """"props", "props.blend", "assets/props.blend" or a path -> <project>/assets/props.blend."""
    text = str(name)
    path = pathlib.Path(text)
    if path.is_absolute():
        return path if path.suffix == ".blend" else path.with_suffix(".blend")
    if not path.suffix:
        path = path.with_suffix(".blend")
    if path.suffix.lower() != ".blend":
        raise AssetLibError(f"A library is a .blend file; got {text!r}.")
    root = _root(root)
    if len(path.parts) > 1:
        return root / path
    return root / LIBRARY_DIR / path


def _rel(path, root=None) -> str:
    try:
        return pathlib.Path(os.path.relpath(path, _root(root))).as_posix()
    except (ValueError, AssetLibError):
        return pathlib.Path(path).as_posix()


def _norm(path) -> str:
    return os.path.normcase(os.path.normpath(os.path.abspath(str(path))))


def _same_file(a, b) -> bool:
    return bool(a) and bool(b) and _norm(a) == _norm(b)


def _lib_abspath(library) -> str:
    return bpy.path.abspath(library.filepath, library=library.library)


def list_libraries(root=None) -> list:
    """Project-relative paths of the library files in <project>/assets/ (and its subfolders)."""
    folder = library_dir(root)
    if not folder.is_dir():
        return []
    return [_rel(p, root) for p in sorted(folder.rglob("*.blend"))]


# --- Catalogs ----------------------------------------------------------------------------------


_CATALOG_HEADER = """# This is an Asset Catalog Definition file for Blender.
#
# Empty lines and lines starting with `#` will be ignored.
# The first non-ignored line should be the version indicator.
# Other lines are of the format "UUID:catalog/path/for/assets:simple catalog name"

VERSION 1
"""


def _clean_catalog(path: str) -> str:
    parts = [p.strip() for p in str(path).replace("\\", "/").split("/") if p.strip()]
    return "/".join(parts)


def ensure_catalog(path: str, root=None) -> tuple:
    """(uuid, simple name) of a catalog such as "Props/Crates", added to assets/blender_assets.cats.txt."""
    path = _clean_catalog(path)
    if not path or ":" in path:
        raise AssetLibError(f"Invalid catalog path {path!r}; use names separated by '/', e.g. 'Props/Crates'.")
    folder = library_dir(root)
    cats = folder / CATALOG_FILE
    entries = {}
    if cats.is_file():
        for line in cats.read_text(encoding="utf-8").splitlines():
            parts = line.strip().split(":")
            if len(parts) >= 3 and not line.startswith("#"):
                entries[parts[1]] = (parts[0], ":".join(parts[2:]))
    if path not in entries:
        entries[path] = (str(uuid.uuid5(CATALOG_NAMESPACE, path)), path.replace("/", "-"))
        lines = [_CATALOG_HEADER] + [f"{cid}:{p}:{simple}" for p, (cid, simple) in sorted(entries.items())]
        folder.mkdir(parents=True, exist_ok=True)
        cats.write_bytes(("\n".join(lines) + "\n").encode("utf-8"))
    return entries[path]


def _catalog_paths(library_file) -> dict:
    return twin.read_catalogs(pathlib.Path(library_file).parent / CATALOG_FILE)


# --- The background writer ---------------------------------------------------------------------


# Runs in a background Blender on the library file. The GameStudio add-on is not loaded there:
# component data travels as stored ID properties, which Blender keeps as they are.
_WORKER = r'''
import importlib, json, os, re, sys
import bpy

def remove_asset(coll):
    tree = set(coll.children_recursive)
    others = [c for c in bpy.data.collections if c is not coll and c not in tree]
    kept = {o for c in others for o in c.objects} | {o for s in bpy.data.scenes for o in s.collection.objects}
    doomed = [o for o in coll.all_objects if o not in kept]
    datas = {o.data for o in doomed if o.data is not None}
    mats = {m for d in datas for m in getattr(d, "materials", ()) if m is not None}
    subs = [c for c in tree if c.users <= 1]
    bpy.data.batch_remove(doomed + subs + [coll])
    bpy.data.batch_remove([d for d in datas if d.users == 0])
    bpy.data.batch_remove([m for m in mats if m.users == 0])

def share(before, originals):
    shared = []
    for attr in ("materials", "node_groups", "images"):
        data = getattr(bpy.data, attr)
        old = {i.name: i for i in before[attr] if i.library is None}
        for new in [i for i in data if i not in before[attr] and i.library is None]:
            match = re.match(r"^(.*)\.\d{3,}$", new.name)
            if not match or new.name in originals.get(attr, ()) or match.group(1) not in old:
                continue
            same = old[match.group(1)]
            if attr == "images" and bpy.path.abspath(same.filepath) != bpy.path.abspath(new.filepath):
                continue
            new.user_remap(same)
            data.remove(new)
            shared.append(same.name)
    return sorted(set(shared))

def add(spec, scene, args):
    name = spec["name"]
    old = next((c for c in bpy.data.collections if c.name == name and c.library is None), None)
    if old is not None:
        if not args.get("replace", True):
            raise RuntimeError(f"The library already has an asset named {name!r}")
        remove_asset(old)
    before = {attr: set(getattr(bpy.data, attr)) for attr in ("materials", "node_groups", "images")}
    with bpy.data.libraries.load(args["staging"], link=False) as (src, dst):
        dst.collections = [spec["staging"]]
    coll = dst.collections[0]
    if coll is None:
        raise RuntimeError(f"Staged asset {spec['staging']!r} is missing")
    coll.name = name
    for obj in coll.all_objects:
        wanted = spec["names"].get(obj.name)
        if wanted and wanted != obj.name:
            obj.name = wanted
        if "gs_id" in obj:
            obj["gs_id_owner"] = obj.name
    shared = share(before, spec.get("originals", {}))
    if coll.asset_data is None:
        coll.asset_mark()
    if coll.name not in scene.collection.children:
        scene.collection.children.link(coll)
    return {"name": coll.name, "objects": sorted(o.name for o in coll.all_objects),
            "replaced": old is not None, "shared": shared}

def main():
    args = json.loads(open(sys.argv[sys.argv.index("--") + 1], encoding="utf-8").read())
    bpy.context.preferences.filepaths.save_version = 0
    library = args["library"]
    if os.path.exists(library):
        bpy.ops.wm.open_mainfile(filepath=library, load_ui=False)
    else:
        bpy.ops.wm.read_homefile(use_empty=True)
        bpy.context.scene.name = args["scene"]
    scene = bpy.context.scene
    results = [add(spec, scene, args) for spec in args.get("assets", [])]
    staging = os.path.normcase(os.path.abspath(args.get("staging", "")))
    for lib in list(bpy.data.libraries):
        if os.path.normcase(os.path.abspath(bpy.path.abspath(lib.filepath))) == staging:
            bpy.data.libraries.remove(lib)  # appending leaves an empty link to the staging file
    for layer in scene.view_layers:
        layer.update()
    os.makedirs(os.path.dirname(library), exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=library)
    twin = None
    addon = args.get("addon")
    if addon:
        try:
            sys.path.insert(0, addon["path"])
            twin = importlib.import_module(addon["package"] + ".core.twin").write_twin(library)
        except Exception as exc:
            twin = {"error": f"{type(exc).__name__}: {exc}"}
    print("GS-RESULT " + json.dumps({"assets": results, "twin": twin}), flush=True)

main()
'''


def _addon_location() -> dict | None:
    package = pathlib.Path(__file__).resolve().parent
    return {"path": str(package.parent), "package": package.name} if package.name.isidentifier() else None


def _run_worker(args: dict) -> dict:
    """Run _WORKER on args["library"] in a background Blender and return its result."""
    with tempfile.TemporaryDirectory(prefix="gs_assetlib_") as tmp:
        argfile = pathlib.Path(tmp) / "args.json"
        argfile.write_text(json.dumps(args), encoding="utf-8")
        env = dict(os.environ, BLENDER_USER_RESOURCES=str(pathlib.Path(tmp) / "profile"))
        cmd = [bpy.app.binary_path, "--background", "--factory-startup", "--python-exit-code", "1",
               "--python-expr", _WORKER, "--", str(argfile)]
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                                  env=env, timeout=WORKER_TIMEOUT, creationflags=flags)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise AssetLibError(f"Could not run Blender to write {args['library']}: {exc}") from None
    for line in proc.stdout.splitlines():
        if line.startswith(RESULT_TAG):
            return json.loads(line[len(RESULT_TAG):])
    tail = "\n".join((proc.stderr or proc.stdout).strip().splitlines()[-8:])
    raise AssetLibError(f"Writing {args['library']} failed (exit code {proc.returncode}):\n{tail}")


def create_library(name="props", root=None) -> pathlib.Path:
    """The library file's path, creating an empty library (one scene, "Assets") if it is missing."""
    path = library_path(name, root)
    if not path.exists():
        _run_worker({"library": str(path), "scene": LIBRARY_SCENE, "assets": [], "addon": _addon_location()})
        log.info("Created asset library %s", path)
    return path


def open_library(name="props", save_changes: bool = True, root=None) -> pathlib.Path:
    """Open a library file in Blender to edit its assets (creating it if missing).

    The open file is saved first when save_changes is True and it has a path.
    """
    path = create_library(name, root)
    if bpy.data.filepath and save_changes:
        bpy.ops.wm.save_mainfile()
    bpy.ops.wm.open_mainfile(filepath=str(path))
    return path


# --- Adding assets -----------------------------------------------------------------------------


def _resolve_objects(objects) -> list:
    if isinstance(objects, (bpy.types.Object, str)):
        objects = [objects]
    found = []
    for item in objects or ():
        obj = bpy.data.objects.get(item) if isinstance(item, str) else item
        if not isinstance(obj, bpy.types.Object):
            raise AssetLibError(f"No object named {item!r}.")
        if obj not in found:
            found.append(obj)
    if not found:
        raise AssetLibError("No objects given; pass objects or their names.")
    return found


def _with_children(objs: list) -> list:
    found = list(objs)
    for obj in objs:
        for child in obj.children_recursive:
            if child not in found:
                found.append(child)
    return found


def _tops(objs: list) -> list:
    members = set(objs)
    return [o for o in objs if o.parent not in members]


def _local_matrix(obj) -> Matrix:
    # matrix_world is runtime data: it is identity for objects never evaluated (linked assets that
    # are only instanced, excluded collections, fresh copies). Rebuild it from the parents.
    if obj.parent is None:
        return obj.matrix_basis.copy()
    return _local_matrix(obj.parent) @ obj.matrix_parent_inverse @ obj.matrix_basis


def _world_corners(objs: list) -> list:
    corners = []
    for obj in objs:
        matrix = _local_matrix(obj)
        if obj.type in RENDERABLE:
            corners += [matrix @ Vector(c) for c in obj.bound_box]
        else:
            corners.append(matrix.translation.copy())
    return corners


def _placement(objs: list) -> tuple:
    """(matrices for the copies' top objects, instance location, instance rotation) for a group.

    One top object: the asset keeps its scale; its location and rotation go to the instance.
    Several: the asset is centred on the bottom centre of their bounds, which the instance takes.
    """
    tops = _tops(objs)
    if len(tops) == 1:
        loc, rot, scale = _local_matrix(tops[0]).decompose()
        return {tops[0]: Matrix.LocRotScale(None, None, scale)}, loc, rot.to_euler("XYZ")
    corners = _world_corners(objs)
    lo = Vector([min(c[i] for c in corners) for i in range(3)])
    hi = Vector([max(c[i] for c in corners) for i in range(3)])
    pivot = Vector(((lo.x + hi.x) / 2, (lo.y + hi.y) / 2, lo.z))
    shift = Matrix.Translation(-pivot)
    return {t: shift @ _local_matrix(t) for t in tops}, pivot, Euler((0, 0, 0))


def _remap_refs(copies: dict) -> None:
    """Point modifiers, constraints and entity fields of the copies at the other copies."""
    for new in copies.values():
        for mod in new.modifiers:
            target = getattr(mod, "object", None)
            if target in copies:
                mod.object = copies[target]
        for con in new.constraints:
            target = getattr(con, "target", None)
            if target in copies:
                con.target = copies[target]
        for name in components.list_components(new):
            grp = registry.group(new, name)
            entry = registry.schema_ref(name)
            if grp is None or entry is None:
                continue
            for f in entry["fields"]:
                if f["type"] == "entity" and getattr(grp, f["name"]) in copies:
                    setattr(grp, f["name"], copies[getattr(grp, f["name"])])


def _fresh_id(obj) -> None:
    for key in (ids.ID_PROP, ids.OWNER_PROP):
        if key in obj:
            del obj[key]
    ids.ensure_id(obj)


def _set_metadata(coll, catalog, tags, description, prefab, root) -> None:
    if coll.asset_data is None:
        coll.asset_mark()
    meta = coll.asset_data
    if catalog:
        meta.catalog_id = ensure_catalog(catalog, root)[0]
    for tag in list(meta.tags):
        meta.tags.remove(tag)
    for tag in tags:
        meta.tags.new(tag, skip_if_exists=True)
    meta.description = description or ""
    export.set_prefab(coll, prefab)


def _look_at(eye: Vector, target: Vector) -> Matrix:
    return (target - eye).to_track_quat("-Z", "Y").to_matrix().to_4x4()


def render_preview(coll, size: int = PREVIEW_SIZE) -> bool:
    """Render a small Workbench picture of the collection into its asset preview. False if empty."""
    objs = [o for o in coll.all_objects if o.type in RENDERABLE]
    if not objs:
        return False
    scene = bpy.data.scenes.new("gs_asset_preview")
    cam = image = None
    fd, path = tempfile.mkstemp(prefix="gs_asset_preview_", suffix=".png")
    os.close(fd)
    try:
        scene.collection.children.link(coll)
        corners = _world_corners(objs)
        lo = Vector([min(c[i] for c in corners) for i in range(3)])
        hi = Vector([max(c[i] for c in corners) for i in range(3)])
        center = (lo + hi) / 2
        radius = max((hi - lo).length / 2, 0.05)
        fov = math.radians(40)
        eye = center + Vector((1, -1, 0.8)).normalized() * (radius / math.sin(fov / 2) * 1.05)
        cam = bpy.data.objects.new("gs_asset_preview_cam", bpy.data.cameras.new("gs_asset_preview_cam"))
        cam.data.angle = fov
        cam.data.clip_start = max(0.001, radius * 0.01)
        cam.data.clip_end = radius * 10 + (eye - center).length
        cam.matrix_world = Matrix.Translation(eye) @ _look_at(eye, center)
        scene.collection.objects.link(cam)
        scene.camera = cam
        render = scene.render
        render.engine = "BLENDER_WORKBENCH"
        render.resolution_x = render.resolution_y = size
        render.resolution_percentage = 100
        render.film_transparent = True
        render.image_settings.file_format = "PNG"
        render.image_settings.color_mode = "RGBA"
        render.filepath = path
        shading = scene.display.shading
        shading.light = "STUDIO"
        shading.color_type = "TEXTURE"
        if "FINISHED" not in bpy.ops.render.render(write_still=True, scene=scene.name):
            return False
        image = bpy.data.images.load(path, check_existing=False)
        pixels = [0.0] * (size * size * 4)
        image.pixels.foreach_get(pixels)
        preview = coll.preview_ensure()
        preview.image_size = (size, size)
        preview.image_pixels_float.foreach_set(pixels)
        return True
    finally:
        if image is not None:
            bpy.data.images.remove(image)
        if cam is not None:
            data = cam.data
            bpy.data.objects.remove(cam)
            bpy.data.cameras.remove(data)
        bpy.data.scenes.remove(scene)
        try:
            os.remove(path)
        except OSError:
            pass


def _groups(objs: list, name, separate: bool) -> list:
    if separate:
        return [(top.name, _with_children([top])) for top in _tops(_with_children(objs))]
    everything = _with_children(objs)
    asset = name or _tops(everything)[0].name
    return [(asset, everything)]


def _check_name(name: str) -> str:
    name = str(name).strip()
    if not name or STAGING_PREFIX in name:
        raise AssetLibError(f"Invalid asset name {name!r}.")
    return name


def _stage(groups: list, placements: dict, staged: list, root, *, catalog, tags, description, prefab,
           preview) -> list:
    """Copy each group into an unlinked collection, in asset space; every new ID goes into `staged`."""
    specs = []
    for asset, objs in groups:
        coll = bpy.data.collections.new(STAGING_PREFIX + asset)
        staged.append(coll)
        top_matrices = placements[asset][0]
        copies = {}
        for src in objs:
            new = src.copy()
            # A unique name that cannot clash in the library; the writer restores the real one.
            new.name = f"{STAGING_PREFIX}{len(staged)}"
            staged.append(new)
            coll.objects.link(new)
            copies[src] = new
        for src, new in copies.items():
            if src in top_matrices:
                new.parent = None
                new.matrix_parent_inverse = Matrix.Identity(4)
                new.matrix_basis = top_matrices[src]
            else:
                new.parent = copies[src.parent]
            _fresh_id(new)
        _remap_refs(copies)
        _set_metadata(coll, catalog, tags, description, prefab, root)
        if preview:
            try:
                render_preview(coll)
            except Exception as exc:  # a missing preview must not stop the asset
                log.warning("No preview for asset '%s': %s", asset, exc)
        originals = {
            "materials": sorted({s.material.name for o in objs for s in o.material_slots if s.material}),
            "node_groups": sorted({m.node_group.name for o in objs for m in o.modifiers
                                   if getattr(m, "node_group", None) is not None}),
        }
        specs.append({"name": asset, "staging": coll.name, "originals": originals,
                      "names": {new.name: src.name for src, new in copies.items()}})
    return specs


def _remove_staged(staged: list) -> None:
    bpy.data.batch_remove(staged)
    ids.invalidate_cache()


def _add_in_place(groups, path, root, *, catalog, tags, description, prefab, preview) -> list:
    """The library is the open file: gather the objects into asset collections and save it."""
    scene = bpy.context.scene
    results = []
    for asset, objs in groups:
        coll = next((c for c in bpy.data.collections if c.name == asset and c.library is None), None)
        if coll is None:
            coll = bpy.data.collections.new(asset)
        if coll.name not in scene.collection.children:
            scene.collection.children.link(coll)
        for obj in objs:
            for other in list(obj.users_collection):
                if other != coll:
                    other.objects.unlink(obj)
            if obj.name not in coll.objects:
                coll.objects.link(obj)
            ids.ensure_id(obj)
        _set_metadata(coll, catalog, tags, description, prefab, root)
        if preview:
            try:
                render_preview(coll)
            except Exception as exc:
                log.warning("No preview for asset '%s': %s", asset, exc)
        results.append({"asset": coll.name, "library": _rel(path, root), "objects": sorted(o.name for o in objs),
                        "prefab": prefab, "catalog": _clean_catalog(catalog) or None, "tags": list(tags)})
    bpy.ops.wm.save_mainfile()
    return results


def _tags(tags) -> list:
    if isinstance(tags, str):
        tags = tags.split(",")
    return sorted({str(t).strip() for t in tags or () if str(t).strip()})


def add_to_library(objects, library="props", name=None, *, move: bool = False, separate: bool = False,
                   catalog: str = "", tags=(), description: str = "", prefab: bool = True,
                   preview: bool = True, replace: bool = True) -> list:
    """Copy (or move) objects into a library file as an asset; returns one dict per asset.

    `objects` are Objects or names; their children come along. By default all of them become one
    asset named `name` (default: the first top object's name); separate=True makes each top object
    its own asset, named after it. An asset with the same name in the library is replaced unless
    replace=False. The asset is stored at the origin: one top object keeps its scale but drops its
    location and rotation; several are centred on the bottom centre of their bounds.

    move=True replaces the objects in the open level with an instance of the new asset, where they
    stood (entity fields that pointed at a moved top object point at the instance). The library
    file is written by a background Blender; levels that link it are reloaded.
    """
    root = _root()
    path = library_path(library, root)
    objs = _resolve_objects(objects)
    tags = _tags(tags)
    groups = [(_check_name(n), members) for n, members in _groups(objs, name, separate)]
    if len({n for n, _ in groups}) != len(groups):
        raise AssetLibError("Two assets would get the same name; rename the objects or pass separate=False.")
    meta = {"catalog": catalog, "tags": tags, "description": description, "prefab": prefab, "preview": preview}
    if _same_file(path, bpy.data.filepath):
        return _add_in_place(groups, path, root, **meta)
    if move and not bpy.data.filepath:
        raise AssetLibError("Save the level first: move=True links the new asset back into it.")

    if catalog:
        ensure_catalog(catalog, root)  # validates the path before anything is staged
    placements = {asset: _placement(members) for asset, members in groups}
    staged = []
    try:
        specs = _stage(groups, placements, staged, root, **meta)
        with tempfile.TemporaryDirectory(prefix="gs_assetlib_stage_") as tmp:
            staging = pathlib.Path(tmp) / "staging.blend"
            colls = {i for i in staged if isinstance(i, bpy.types.Collection)}
            bpy.data.libraries.write(str(staging), colls, path_remap="ABSOLUTE", fake_user=True)
            result = _run_worker({"library": str(path), "staging": str(staging), "scene": LIBRARY_SCENE,
                                  "assets": specs, "replace": replace, "addon": _addon_location()})
    finally:
        _remove_staged(staged)

    if isinstance(result.get("twin"), dict) and result["twin"].get("error"):
        log.warning("Library twin for %s: %s", path, result["twin"]["error"])
    reload_libraries([path])
    out = []
    for (asset, members), done in zip(groups, result["assets"]):
        entry = {"asset": done["name"], "library": _rel(path, root), "objects": done["objects"], "prefab": prefab,
                 "catalog": _clean_catalog(catalog) or None, "tags": tags, "replaced": done["replaced"]}
        if done["shared"]:
            entry["shared_with_library"] = done["shared"]
        if move:
            _matrices, loc, rot = placements[asset]
            entry["instance"] = _replace_with_instance(members, done["name"], path, loc, rot).name
        out.append(entry)
    log.info("Added %s to %s", ", ".join(e["asset"] for e in out), path)
    return out


def _retarget_entity_fields(old: set, new) -> None:
    for obj in bpy.data.objects:
        if obj.library is not None:
            continue
        for name in components.list_components(obj):
            grp = registry.group(obj, name)
            entry = registry.schema_ref(name)
            if grp is None or entry is None:
                continue
            for f in entry["fields"]:
                if f["type"] == "entity" and getattr(grp, f["name"]) in old:
                    setattr(grp, f["name"], new)


def _replace_with_instance(members: list, asset: str, path, loc, rot):
    tops = _tops(members)
    first = tops[0]
    target = first.users_collection[0] if first.users_collection else bpy.context.scene.collection
    name = first.name if len(tops) == 1 else asset
    parent = first.parent if len(tops) == 1 else None
    for obj in members:
        obj.name = obj.name + ".moved"  # free the names before the instance takes one
    coll = link_asset(asset, path)
    inst = bpy.data.objects.new(name, None)
    inst.instance_type = "COLLECTION"
    inst.instance_collection = coll
    target.objects.link(inst)
    world = Matrix.LocRotScale(loc, rot, None)
    if parent is not None:
        inst.parent = parent
        world = _local_matrix(parent).inverted_safe() @ world
    inst.matrix_basis = world
    _retarget_entity_fields(set(tops), inst)
    bpy.data.batch_remove(members)
    ids.invalidate_cache()
    return inst


# --- Listing -----------------------------------------------------------------------------------


_listing_cache: dict = {}


def _describe(coll, library_file, catalogs: dict, root) -> dict:
    objs = list(coll.all_objects)
    comps = set()
    for obj in objs:
        comps.update(name for name, _enabled in twin.component_refs(obj))
    corners = _world_corners([o for o in objs if o.type in RENDERABLE])
    meta = coll.asset_data
    entry = {"name": coll.name, "library": _rel(library_file, root),
             "catalog": (catalogs.get(meta.catalog_id) or meta.catalog_simple_name or None) if meta else None,
             "tags": sorted(t.name for t in meta.tags) if meta else [],
             "description": meta.description if meta else "",
             "prefab": export.is_prefab(coll), "objects": len(objs), "components": sorted(comps)}
    if corners:
        entry["size"] = [round(max(c[i] for c in corners) - min(c[i] for c in corners), 3) for i in range(3)]
    return entry


def _read_library(path: pathlib.Path, root) -> list:
    """The asset collections of one library file, read without touching the open file."""
    stat = path.stat()
    key = _norm(path)
    cached = _listing_cache.get(key)
    if cached is not None and cached[0] == (stat.st_mtime_ns, stat.st_size):
        return [dict(e) for e in cached[1]]
    catalogs = _catalog_paths(path)
    entries = []
    if _same_file(path, bpy.data.filepath):
        for coll in bpy.data.collections:
            if coll.library is None and coll.asset_data is not None:
                entries.append(_describe(coll, path, catalogs, root))
        return sorted(entries, key=lambda e: e["name"])
    with bpy.data.temp_data() as data:
        with data.libraries.load(str(path), link=True, assets_only=True) as (src, dst):
            dst.collections = list(src.collections)
        for coll in dst.collections:
            if coll is not None:
                entries.append(_describe(coll, path, catalogs, root))
    entries.sort(key=lambda e: e["name"])
    _listing_cache[key] = ((stat.st_mtime_ns, stat.st_size), entries)
    return [dict(e) for e in entries]


def list_assets(library=None, *, catalog: str | None = None, tag: str | None = None, root=None) -> list:
    """Asset collections across the project's library files (or one library).

    Each entry: {name, library, catalog, tags, description, prefab, objects, components, size,
    placed}. `size` is the asset's bounding size in metres; `placed` counts its instances in the
    open file. catalog filters by catalog path prefix, tag by tag.
    """
    root = _root(root)
    paths = [library_path(library, root)] if library is not None else \
        [root / p for p in list_libraries(root)]
    placed = {}
    for inst in instances():
        info = asset_of(inst)
        key = (info["asset"], info["library"])
        placed[key] = placed.get(key, 0) + 1
    found = []
    for path in paths:
        if not path.is_file():
            raise AssetLibError(f"No library file at {_rel(path, root)}. Libraries: "
                                f"{', '.join(list_libraries(root)) or '(none)'}.")
        for entry in _read_library(path, root):
            if catalog and not (entry["catalog"] or "").startswith(_clean_catalog(catalog)):
                continue
            if tag and tag not in entry["tags"]:
                continue
            entry["placed"] = placed.get((entry["name"], entry["library"]), 0)
            found.append(entry)
    return found


def _asset_names(path: pathlib.Path) -> list:
    if _same_file(path, bpy.data.filepath):
        return [c.name for c in bpy.data.collections if c.library is None and c.asset_data is not None]
    with bpy.data.libraries.load(str(path), assets_only=True) as (src, _dst):
        return list(src.collections)


def find_asset(asset: str, library=None, root=None) -> tuple:
    """(library file, asset name) for an asset name, searching every library when none is given."""
    root = _root(root)
    if library is not None:
        path = library_path(library, root)
        if not path.is_file():
            raise AssetLibError(f"No library file at {_rel(path, root)}.")
        names = _asset_names(path)
        if asset not in names:
            raise AssetLibError(f"{_rel(path, root)} has no asset {asset!r}. Assets: {', '.join(names) or '(none)'}.")
        return path, asset
    hits = []
    known = []
    for rel in list_libraries(root):
        names = _asset_names(root / rel)
        known += names
        if asset in names:
            hits.append(root / rel)
    if not hits:
        raise AssetLibError(f"No asset named {asset!r} in {LIBRARY_DIR}/. "
                            f"Assets: {', '.join(sorted(known)) or '(none)'}.")
    if len(hits) > 1:
        raise AssetLibError(f"{asset!r} is in several libraries ({', '.join(_rel(h, root) for h in hits)}); "
                            "pass library.")
    return hits[0], asset


# --- Placing -----------------------------------------------------------------------------------


def _linked_collection(path, name):
    for coll in bpy.data.collections:
        if coll.library is not None and coll.name == name and _same_file(_lib_abspath(coll.library), path):
            return coll
    return None


def link_asset(asset: str, library=None):
    """The asset's collection in the open file, linking it from its library if needed."""
    path, name = find_asset(asset, library)
    if _same_file(path, bpy.data.filepath):
        return bpy.data.collections[name]
    if not bpy.data.filepath:
        raise AssetLibError("Save the level inside the project first, so the library link can be relative.")
    coll = _linked_collection(path, name)
    if coll is not None:
        return coll
    with bpy.data.libraries.load(str(path), link=True, relative=True) as (src, dst):
        dst.collections = [name]
    coll = dst.collections[0]
    if coll is None:
        raise AssetLibError(f"Could not link {name!r} from {path}.")
    return coll


def make_spawnable(asset: str, library=None):
    """Link an asset into the open level without placing it, so gameplay code can ctx.spawn() it.

    It is kept by the "Spawnable" collection, which has a fake user and is in no scene: the export
    writes the asset as a prefab template only, never as objects in the level. Returns the asset's
    collection.
    """
    coll = link_asset(asset, library)
    if not export.is_prefab(coll):
        raise AssetLibError(f"'{coll.name}' is not a prefab; add it to its library again with prefab=True.")
    holder = next((c for c in bpy.data.collections if c.name == SPAWN_COLLECTION and c.library is None), None)
    if holder is None:
        holder = bpy.data.collections.new(SPAWN_COLLECTION)
        holder.use_fake_user = True
    if coll.name not in holder.children:
        holder.children.link(coll)
    return coll


def _target_collection(collection):
    if collection is None:
        return bpy.context.collection or bpy.context.scene.collection
    if isinstance(collection, bpy.types.Collection):
        return collection
    coll = bpy.data.collections.get(str(collection))
    if coll is None or coll.library is not None:
        coll = bpy.data.collections.new(str(collection))
    scene = bpy.context.scene
    if coll not in scene.collection.children_recursive:
        scene.collection.children.link(coll)
    return coll


def _next_name(base: str) -> str:
    pattern = re.compile(rf"^{re.escape(base)}_(\d+)$")
    used = [int(m.group(1)) for o in bpy.data.objects if (m := pattern.match(o.name))]
    return f"{base}_{max(used, default=0) + 1:02d}"


def _scale(scale) -> tuple:
    if isinstance(scale, (int, float)):
        return (float(scale),) * 3
    values = tuple(float(v) for v in scale)
    if len(values) != 3:
        raise AssetLibError("scale is a number or [x, y, z].")
    return values


def place_asset(asset: str, library=None, *, location=(0.0, 0.0, 0.0), rotation=(0.0, 0.0, 0.0), scale=1.0,
                name: str | None = None, collection=None):
    """Place an instance of an asset in the open level and return the instance empty.

    location in metres, rotation in degrees (XYZ Euler), scale a number or [x, y, z] (Blender
    space). The instance is named <asset>_01, _02, ... unless `name` is given, and goes into
    `collection` (a Collection or a name, created if missing; default: the active collection).
    Per-copy components (RigidBody, Coin, ...) belong on the returned empty.
    """
    coll = link_asset(asset, library)
    inst = bpy.data.objects.new(name or _next_name(coll.name), None)
    inst.instance_type = "COLLECTION"
    inst.instance_collection = coll
    _target_collection(collection).objects.link(inst)
    inst.location = tuple(float(v) for v in location)
    inst.rotation_euler = tuple(math.radians(float(v)) for v in rotation)
    inst.scale = _scale(scale)
    return inst


def _check_instance(instance):
    obj = bpy.data.objects.get(instance) if isinstance(instance, str) else instance
    if not isinstance(obj, bpy.types.Object):
        raise AssetLibError(f"No object named {instance!r}.")
    if obj.instance_type != "COLLECTION" or obj.instance_collection is None:
        raise AssetLibError(f"'{obj.name}' is not an asset instance (an empty that instances a collection).")
    try:
        components.check_writable(obj)
    except components.ReadOnlyObjectError as exc:
        raise AssetLibError(str(exc)) from None
    return obj


def replace_asset(instance, asset: str, library=None):
    """Make an instance show another asset; it keeps its name, transform and components."""
    obj = _check_instance(instance)
    obj.instance_collection = link_asset(asset, library)
    return obj


def asset_of(obj) -> dict | None:
    """{"asset", "library", "prefab"} for a collection instance; library is None for local collections."""
    coll = obj.instance_collection if obj.instance_type == "COLLECTION" else None
    if coll is None:
        return None
    lib = _rel(_lib_abspath(coll.library)) if coll.library is not None else None
    return {"asset": coll.name, "library": lib, "prefab": export.is_prefab(coll)}


def instances(asset: str | None = None, library=None) -> list:
    """Collection instances in the open file, optionally of one asset (and library)."""
    path = library_path(library) if library is not None else None
    found = []
    for obj in bpy.data.objects:
        coll = obj.instance_collection if obj.instance_type == "COLLECTION" else None
        if coll is None or (asset is not None and coll.name != asset):
            continue
        if path is not None and (coll.library is None or not _same_file(_lib_abspath(coll.library), path)):
            continue
        found.append(obj)
    return sorted(found, key=lambda o: o.name)


def reload_libraries(libraries=None) -> list:
    """Reload linked library files (all, or the given names/paths) after they changed on disk."""
    wanted = None if libraries is None else {_norm(library_path(lib)) for lib in libraries}
    done = []
    for lib in list(bpy.data.libraries):
        path = _lib_abspath(lib)
        if wanted is not None and _norm(path) not in wanted:
            continue
        lib.reload()
        done.append(_rel(path))
    if done:
        ids.invalidate_cache()
        _listing_cache.clear()
    return done


def make_local(instance) -> list:
    """Turn an asset instance into local copies of the asset's objects, for one-off edits.

    The empty stays (with its name, transform, components and id) and becomes the parent of the
    copies; it no longer instances the asset. Meshes are copied so they can be edited; materials
    stay shared with the library. The copies get new entity ids. Returns the copies.
    """
    obj = _check_instance(instance)
    coll = obj.instance_collection
    offset = Matrix.Translation(-coll.instance_offset)
    targets = list(obj.users_collection) or [bpy.context.scene.collection]
    sources = sorted(coll.all_objects, key=lambda o: o.name)
    copies = {}
    for src in sources:
        new = src.copy()
        if src.data is not None:
            new.data = src.data.copy()
        for target in targets:
            target.objects.link(new)
        copies[src] = new
    for src, new in copies.items():
        if src.parent in copies:
            new.parent = copies[src.parent]
        else:
            new.parent = obj
            new.matrix_parent_inverse = Matrix.Identity(4)
            new.matrix_basis = offset @ src.matrix_basis
        _fresh_id(new)
    _remap_refs(copies)
    obj.instance_type = "NONE"
    obj.instance_collection = None
    return [copies[s] for s in sources]


# --- UI ------------------------------------------------------------------------------------------


_place_items: list = []


def _refresh_place_items() -> list:
    global _place_items
    try:
        assets = list_assets()
    except AssetLibError:
        assets = []
    multi = len({a["library"] for a in assets}) > 1
    _place_items = [(f"{a['library']}|{a['name']}", f"{a['name']} ({pathlib.Path(a['library']).stem})" if multi
                     else a["name"], a.get("description") or a["library"]) for a in assets]
    return _place_items


def _place_enum(_self, _context):
    return _place_items or [("", "(no assets)", "Add objects to a library first")]


class GAMESTUDIO_OT_asset_add(bpy.types.Operator):
    bl_idname = "gamestudio.asset_add"
    bl_label = "Add Selected to Library"
    bl_description = "Copy the selected objects into an asset library file (assets/<library>.blend) as an asset"
    bl_options = {"REGISTER", "UNDO"}

    library: StringProperty(name="Library", default="props", description="Library file in assets/ (no .blend)")
    asset_name: StringProperty(name="Asset Name", description="Defaults to the active object's name")
    catalog: StringProperty(name="Catalog", description="Catalog path, e.g. Props/Crates")
    tags: StringProperty(name="Tags", description="Comma-separated tags")
    separate: BoolProperty(name="One Asset per Object", default=False)
    move: BoolProperty(name="Replace with Instance", default=False,
                       description="Remove the objects from this level and place an instance of the new asset")
    prefab: BoolProperty(name="Prefab", default=True,
                         description="Its objects and components become entities in every level (and ctx.spawn)")

    @classmethod
    def poll(cls, context):
        return bool(context.selected_objects) and project.project_root() is not None

    def invoke(self, context, _event):
        if not self.asset_name and context.active_object is not None:
            self.asset_name = context.active_object.name
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        try:
            done = add_to_library(list(context.selected_objects), self.library, self.asset_name or None,
                                  move=self.move, separate=self.separate, catalog=self.catalog, tags=self.tags,
                                  prefab=self.prefab)
        except AssetLibError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        self.report({"INFO"}, f"Added {', '.join(d['asset'] for d in done)} to {done[0]['library']}")
        return {"FINISHED"}


class GAMESTUDIO_OT_asset_place(bpy.types.Operator):
    bl_idname = "gamestudio.asset_place"
    bl_label = "Place Asset"
    bl_description = "Place an instance of a library asset at the 3D cursor"
    bl_options = {"REGISTER", "UNDO"}

    asset: EnumProperty(name="Asset", items=_place_enum)
    # Set by the Game Assets list instead of `asset`: no dialog.
    asset_name: StringProperty(name="Asset Name", options={"SKIP_SAVE", "HIDDEN"})
    library: StringProperty(name="Library", options={"SKIP_SAVE", "HIDDEN"})

    @classmethod
    def poll(cls, context):
        return project.project_root() is not None

    def invoke(self, context, _event):
        if self.asset_name:
            return self.execute(context)
        _refresh_place_items()
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        if self.asset_name:
            library, name = self.library or None, self.asset_name
        else:
            if not _place_items:
                _refresh_place_items()
            if "|" not in (self.asset or ""):
                self.report({"ERROR"}, "No asset chosen (the project has no asset libraries yet)")
                return {"CANCELLED"}
            library, name = self.asset.split("|", 1)
        try:
            inst = place_asset(name, library, location=context.scene.cursor.location)
        except AssetLibError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        for obj in context.selected_objects:
            obj.select_set(False)
        if inst.name in context.view_layer.objects:
            inst.select_set(True)
            context.view_layer.objects.active = inst
        return {"FINISHED"}


def _is_instance(obj) -> bool:
    return obj is not None and obj.instance_type == "COLLECTION" and obj.instance_collection is not None


def _instances_to_replace(context) -> list:
    """The selected asset instances, and the active object when it is one."""
    found = [o for o in getattr(context, "selected_objects", ()) if _is_instance(o)]
    active = getattr(context, "active_object", None)
    if _is_instance(active) and active not in found:
        found.append(active)
    return found


class GAMESTUDIO_OT_asset_replace(bpy.types.Operator):
    bl_idname = "gamestudio.asset_replace"
    bl_label = "Replace with Asset"
    bl_description = ("Make the selected asset instances show this asset instead; they keep their names, "
                      "transforms and components")
    bl_options = {"REGISTER", "UNDO"}

    asset_name: StringProperty(name="Asset Name", options={"SKIP_SAVE"})
    library: StringProperty(name="Library", options={"SKIP_SAVE"})

    @classmethod
    def poll(cls, context):
        if not _instances_to_replace(context):
            cls.poll_message_set("Select an asset instance first")
            return False
        return project.project_root() is not None

    def execute(self, context):
        targets = _instances_to_replace(context)
        try:
            for obj in targets:
                replace_asset(obj, self.asset_name, self.library or None)
        except AssetLibError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        self.report({"INFO"}, f"Replaced {len(targets)} instance(s) with {self.asset_name}")
        return {"FINISHED"}


class GAMESTUDIO_OT_asset_reload(bpy.types.Operator):
    bl_idname = "gamestudio.asset_reload"
    bl_label = "Reload Libraries"
    bl_description = "Reload the linked asset libraries after they changed on disk"
    bl_options = {"REGISTER"}

    def execute(self, context):
        done = reload_libraries()
        self.report({"INFO"}, f"Reloaded {len(done)} librar{'y' if len(done) == 1 else 'ies'}")
        return {"FINISHED"}


class GAMESTUDIO_OT_asset_make_local(bpy.types.Operator):
    bl_idname = "gamestudio.asset_make_local"
    bl_label = "Make Instance Local"
    bl_description = "Replace the active asset instance with editable local copies of its objects"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj is not None and obj.instance_type == "COLLECTION" and obj.instance_collection is not None

    def execute(self, context):
        try:
            copies = make_local(context.active_object)
        except AssetLibError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        self.report({"INFO"}, f"Made {len(copies)} local object(s)")
        return {"FINISHED"}


def draw_asset_menu(layout) -> None:
    layout.operator_context = "INVOKE_DEFAULT"
    layout.operator(GAMESTUDIO_OT_asset_add.bl_idname, text="Add Selected to Library...", icon="ASSET_MANAGER")
    layout.operator(GAMESTUDIO_OT_asset_place.bl_idname, text="Place Asset...", icon="OUTLINER_OB_GROUP_INSTANCE")
    layout.operator(GAMESTUDIO_OT_asset_reload.bl_idname, text="Reload Libraries", icon="FILE_REFRESH")
    layout.operator(GAMESTUDIO_OT_asset_make_local.bl_idname, text="Make Instance Local", icon="UNLINKED")


class GAMESTUDIO_MT_asset_library(bpy.types.Menu):
    bl_idname = "GAMESTUDIO_MT_asset_library"
    bl_label = "Asset Library"

    def draw(self, context):
        draw_asset_menu(self.layout)


def _ui_assets() -> tuple:
    """(assets, error text) for the UI. Never raises: a broken library must not break the header."""
    try:
        return list_assets(), ""
    except AssetLibError as exc:
        return [], str(exc)
    except Exception as exc:  # noqa: BLE001 - drawn as an error instead
        log.debug("Could not list the asset libraries: %s", exc)  # drawn on every redraw
        return [], f"Could not read the asset libraries: {exc}"


def _muted(layout):
    col = layout.column(align=True)
    col.active = False
    return col


def draw_assets(layout, context) -> None:
    """The project's libraries and their assets, with Place (at the 3D cursor) and Replace (the
    selected instances), then the library actions and the active instance's asset."""
    root = project.project_root()
    if root is None:
        layout.label(text="Save the level inside a project", icon="INFO")
        return
    obj = getattr(context, "active_object", None)
    info = asset_of(obj) if obj is not None else None
    can_replace = bool(_instances_to_replace(context))
    assets, error = _ui_assets()
    if error:
        layout.label(text=error, icon="ERROR")
    by_library = {}
    for entry in assets:
        by_library.setdefault(entry["library"], []).append(entry)
    libraries = list_libraries(root)
    if not libraries:
        col = _muted(layout)
        col.label(text=f"No asset libraries in {LIBRARY_DIR}/ yet", icon="INFO")
        col.label(text="Select objects, then Add Selected to Library", icon="BLANK1")
    for library in libraries:
        col = layout.column(align=True)
        col.label(text=pathlib.Path(library).stem, icon="ASSET_MANAGER")
        entries = by_library.get(library, [])
        if not entries:
            _muted(col).label(text="No assets", icon="BLANK1")
        for entry in entries:
            row = col.row(align=True)
            placed = f"  ({entry['placed']} placed)" if entry["placed"] else ""
            row.label(text=entry["name"] + placed, icon="OUTLINER_OB_GROUP_INSTANCE")
            op = row.operator(GAMESTUDIO_OT_asset_place.bl_idname, text="Place")
            op.asset_name, op.library = entry["name"], entry["library"]
            sub = row.row(align=True)
            sub.enabled = can_replace
            op = sub.operator(GAMESTUDIO_OT_asset_replace.bl_idname, text="Replace")
            op.asset_name, op.library = entry["name"], entry["library"]
        layout.separator()
    draw_asset_menu(layout.column(align=True))
    if info is not None:
        col = layout.column(align=True)
        col.separator()
        col.label(text=f"Active: instance of {info['asset']}", icon="OUTLINER_OB_GROUP_INSTANCE")
        _muted(col).label(text=info["library"] or "Local collection", icon="LINKED" if info["library"] else "BLANK1")


# The 3D View header's popover, next to View, Select, Add and Object (the docstring is its tooltip).
class GAMESTUDIO_PT_game_assets(bpy.types.Panel):
    """Place assets from the project's asset libraries, or swap the selected instances to another asset"""

    bl_space_type = "VIEW_3D"
    bl_region_type = "HEADER"
    bl_label = "Game Assets"
    bl_idname = "GAMESTUDIO_PT_game_assets"
    bl_ui_units_x = 16

    def draw(self, context):
        draw_assets(self.layout, context)


def _draw_view3d_menus(self, context):
    """Appended to VIEW3D_MT_editor_menus: the popover, in Object Mode inside a project."""
    if context.mode != "OBJECT" or project.project_root() is None:
        return
    self.layout.popover(GAMESTUDIO_PT_game_assets.bl_idname, text="Game Assets")


# The same list in the sidebar's Game tab, only with the sidebar preference on.
class GAMESTUDIO_PT_asset_library(bpy.types.Panel):
    """Place assets from the project's asset libraries, or swap the selected instances to another asset"""

    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Game"
    bl_label = "Asset Library"
    bl_idname = "GAMESTUDIO_PT_asset_library"
    bl_options = {"DEFAULT_CLOSED"}

    @classmethod
    def poll(cls, context):
        return prefs.sidebar_enabled()

    def draw(self, context):
        draw_assets(self.layout, context)


def _draw_game_menu(self, context):
    self.layout.separator()
    self.layout.menu(GAMESTUDIO_MT_asset_library.bl_idname, icon="ASSET_MANAGER")


_CLASSES = (GAMESTUDIO_OT_asset_add, GAMESTUDIO_OT_asset_place, GAMESTUDIO_OT_asset_replace,
            GAMESTUDIO_OT_asset_reload, GAMESTUDIO_OT_asset_make_local, GAMESTUDIO_MT_asset_library,
            GAMESTUDIO_PT_game_assets, GAMESTUDIO_PT_asset_library)


def register():
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    menu = getattr(bpy.types, "GAMESTUDIO_MT_game", None)
    if menu is not None:
        menu.append(_draw_game_menu)
    bpy.types.VIEW3D_MT_editor_menus.append(_draw_view3d_menus)


def unregister():
    bpy.types.VIEW3D_MT_editor_menus.remove(_draw_view3d_menus)
    menu = getattr(bpy.types, "GAMESTUDIO_MT_game", None)
    if menu is not None:
        menu.remove(_draw_game_menu)
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
    _listing_cache.clear()
    _place_items.clear()
