# SPDX-License-Identifier: GPL-3.0-or-later
"""The scene twin: a small, readable text view of a .blend, written next to it as <name>.scene.json.

Agents read the twin instead of opening Blender to get an overview of a level, and git diffs it
to show what a save changed. It is rewritten on every save of a .blend inside a project (outside
.gamestudio/, node_modules/ and dist/) and on demand with write_twin(). "twin": false in
gamestudio.json turns it off.

Format: JSON with a fixed key order and one entry per line, so a diff shows exactly which object
changed. Values are in Blender space (Z-up, metres); loc/rot/scale are local to the parent, rot is
XYZ Euler in degrees. `comps` lists only the fields stored on the object, so the twin follows the
.blend, not the component registry. `materials` lists the materials that have a game shader, with
their stored values that differ from the shader's defaults. It leaves out everything that changes
without a real edit: selection, view and UI state, the current frame, the export revision, times
and versions.

Size: the first MAX_OBJECTS objects (objects with components, kits, instances, cameras and lights
first) get a line each; the rest are summarised per collection under "more". Lists and texts in a
line are capped.
"""

import json
import math
import os
import pathlib
import re
import zlib

import bpy
from bpy.app.handlers import persistent

from . import ids, project
from .log import get_logger

log = get_logger(__name__)

FORMAT = "gamestudio.twin"
VERSION = 1
SUFFIX = ".scene.json"
SKIP_DIRS = {project.GS_DIRNAME, "node_modules", "dist", ".git"}
CATALOG_FILE = "blender_assets.cats.txt"
# Owned by other modules; repeated here so the twin can be written without registering the add-on
# (the asset library writes library twins from a background Blender).
KIT_PROP = "gs_kit"            # kits/base.py
PROCEDURAL_PROP = "gs_procedural"  # procedural/builders.py
PREFAB_PROP = "gs_prefab"      # core/export.py
COMPONENT_PREFIX = "gsc_"      # core/registry.py
SHADER_PREFIX = "gss_"         # core/registry.py
HIDDEN_PROPS = {ids.ID_PROP, ids.OWNER_PROP, KIT_PROP, PROCEDURAL_PROP, PREFAB_PROP, "gs_rev"}

MAX_OBJECTS = 250
MAX_COLLECTIONS = 120
MAX_LIBRARIES = 40
MAX_ITEMS = 8
MAX_TEXT = 80
MAX_NAME_GROUPS = 10

ABOUT = ("Overview of {file}, rewritten on every save; do not edit. Blender space: Z-up, metres. "
         "loc/rot/scale are local to the parent (rot: XYZ Euler, degrees); dim is the bounding size. "
         "comps lists only fields set on the object (others are defaults, see list_component_types); "
         "comps_off are disabled. inst is a collection instance of that asset. "
         "More about one object: MCP get_object {{name}}; objects not listed here: list_objects.")
# Added to `about` when the file has materials with a game shader.
ABOUT_MATERIALS = (" materials lists the game shader that draws each material that has one, with the values that "
                   "differ from the shader's defaults.")


# --- Small helpers -----------------------------------------------------------------------------


def _num(value, places: int):
    try:
        value = round(float(value), places)
    except (TypeError, ValueError):
        return 0
    if not math.isfinite(value):
        return 0
    return int(value) if value.is_integer() and abs(value) < 1e15 else value


def _vec(values, places: int) -> list:
    return [_num(v, places) for v in values]


def _sig(value):
    """A stored float with its float32 noise trimmed (0.05, not 0.0500000007)."""
    if not math.isfinite(value):
        return 0
    value = float(f"{value:.6g}")
    return int(value) if value.is_integer() and abs(value) < 1e15 else value


def _text(value: str) -> str:
    return value if len(value) <= MAX_TEXT else value[:MAX_TEXT - 1] + "…"


def _capped(items: list) -> list:
    return items if len(items) <= MAX_ITEMS else items[:MAX_ITEMS] + [f"+{len(items) - MAX_ITEMS} more"]


def _crc(text: str) -> int:
    return zlib.crc32(text.encode("utf-8")) & 0x7FFFFFFF  # registry._enum_items


def _plain(value, depth: int = 0):
    """A JSON-safe, rounded copy of a custom property or kit parameter value."""
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return _sig(value)
    if isinstance(value, str):
        return _text(value)
    if isinstance(value, bpy.types.ID):
        return value.name
    if depth > 3:
        return "…"
    if hasattr(value, "to_dict"):
        value = value.to_dict()
    elif hasattr(value, "to_list"):
        value = value.to_list()
    if isinstance(value, dict):
        return {str(k): _plain(v, depth + 1) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return _capped([_plain(v, depth + 1) for v in value])
    return _text(str(value))


def twin_path(blend_path) -> pathlib.Path:
    """scenes/main.blend -> scenes/main.scene.json."""
    path = pathlib.Path(blend_path)
    return path.with_name(path.stem + SUFFIX)


def wants_twin(blend_path) -> bool:
    """True for a .blend inside a project, outside generated folders, with twins enabled."""
    if not blend_path:
        return False
    root = project.find_project_root(str(blend_path))
    if root is None:
        return False
    try:
        rel = pathlib.Path(blend_path).resolve().relative_to(root.resolve())
    except (OSError, ValueError):
        return False
    if any(part in SKIP_DIRS for part in rel.parts[:-1]):
        return False
    return project.read_config(root).get("twin", True) is not False


# --- Context shared by one build ---------------------------------------------------------------


class _Context:
    def __init__(self, blend_path: pathlib.Path, root, previous: dict | None):
        self.blend = blend_path
        self.base_dir = str(blend_path.parent)
        self.root = pathlib.Path(root) if root is not None else None
        self.previous = _previous_values(previous)
        self.schemas = _schemas(self.root)
        self.shader_schemas = _schemas(self.root, "shaders")
        self._catalogs = {"": _catalogs(blend_path.parent, self.root)}
        self._libs: dict = {}

    def catalogs(self, library) -> dict:
        """Catalog paths for this file's assets, or for a library's (its catalog file is beside it)."""
        key = library.filepath if library is not None else ""
        if key not in self._catalogs:
            folder = pathlib.Path(bpy.path.abspath(key, start=self.base_dir)).parent
            self._catalogs[key] = _catalogs(folder, self.root)
        return self._catalogs[key]

    def rel(self, path: str) -> str:
        """A Blender path as a project-relative POSIX path (or as stored, if outside the project)."""
        if not path:
            return ""
        absolute = bpy.path.abspath(path, start=self.base_dir) if path.startswith("//") else path
        absolute = os.path.normpath(absolute)
        if self.root is not None:
            try:
                return pathlib.Path(os.path.relpath(absolute, self.root)).as_posix()
            except ValueError:  # another drive on Windows
                pass
        return pathlib.PurePath(path.replace("\\", "/")).as_posix()

    def lib(self, library) -> str:
        if library is None:
            return ""
        key = library.filepath
        if key not in self._libs:
            self._libs[key] = self.rel(key)
        return self._libs[key]

    def field(self, component: str, name: str, shader: bool = False):
        for f in ((self.shader_schemas if shader else self.schemas).get(component) or {}).get("fields", []):
            if f.get("name") == name:
                return f
        return None

    def value(self, owner, component: str, key: str, raw, shader: bool = False):
        """A stored field value as the twin shows it. owner: the object's name, or ("material", name)."""
        field = self.field(component, key, shader)
        ftype = field.get("type") if field else None
        if isinstance(raw, bpy.types.ID):
            return raw.name
        if isinstance(raw, bool):
            return raw
        if isinstance(raw, int):
            if ftype == "bool":
                return bool(raw)
            if ftype == "enum":
                for option in field.get("options", []):
                    if _crc(option) == raw:
                        return option
            # Without the registry, the previous twin tells how this value was shown.
            prev = self.previous.get((owner, component, key))
            if isinstance(prev, bool) and raw in (0, 1):
                return bool(raw)
            if isinstance(prev, str) and _crc(prev) == raw:
                return prev
            return raw
        if isinstance(raw, float):
            return _sig(raw)
        if isinstance(raw, str):
            return _text(raw)
        return _plain(raw)


def _schemas(root, key: str = "components") -> dict:
    """Component (or, with key "shaders", game shader) schemas by name: the loaded registry, else the
    project's components.json."""
    try:
        from . import registry

        if key == "shaders":
            names, ref = registry.shader_names(), registry.shader_schema_ref
        else:
            names, ref = registry.component_names(), registry.schema_ref
        if names:
            return {n: ref(n) for n in names}
    except Exception:  # the registry is optional here
        pass
    if root is None:
        return {}
    try:
        with open(project.registry_path(root), "r", encoding="utf-8") as f:
            data = json.load(f)
        return {c["name"]: c for c in data.get(key) or [] if isinstance(c, dict) and "name" in c}
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return {}


def _previous_values(previous: dict | None) -> dict:
    found = {}
    for entry in (previous or {}).get("objects", []):
        if not isinstance(entry, dict):
            continue
        for comp, values in (entry.get("comps") or {}).items():
            if isinstance(values, dict):
                for key, value in values.items():
                    found[(entry.get("name"), comp, key)] = value
    for entry in (previous or {}).get("materials", []):
        if isinstance(entry, dict) and isinstance(entry.get("values"), dict):
            for key, value in entry["values"].items():
                found[(("material", entry.get("name")), entry.get("shader"), key)] = value
    return found


def _catalogs(folder: pathlib.Path, root) -> dict:
    """catalog UUID -> path, from the nearest blender_assets.cats.txt (up to the project root)."""
    stop = pathlib.Path(root).resolve() if root is not None else None
    try:
        folder = folder.resolve()
    except OSError:
        return {}
    for directory in (folder, *folder.parents):
        path = directory / CATALOG_FILE
        if path.is_file():
            return read_catalogs(path)
        if stop is None or directory == stop:
            break
    return {}


def read_catalogs(path) -> dict:
    """Parse a Blender catalog definition file into {uuid: catalog path}."""
    found = {}
    try:
        lines = pathlib.Path(path).read_text(encoding="utf-8").splitlines()
    except OSError:
        return found
    for line in lines:
        line = line.strip()
        if not line or line.startswith(("#", "VERSION")):
            continue
        parts = line.split(":")
        if len(parts) >= 2:
            found[parts[0]] = parts[1]
    return found


# --- Reading stored GameStudio data ------------------------------------------------------------


def _system_props(id_data):
    try:
        return id_data.bl_system_properties_get()
    except (AttributeError, TypeError, RuntimeError):
        return None


def component_refs(obj) -> list:
    """[(name, enabled)] from the stored component list; works without the add-on registered."""
    stored = _system_props(obj)
    gs = stored.get("gs") if stored is not None else None
    refs = gs.get("components") if gs is not None else None
    found = []
    seen = set()
    for ref in refs or ():
        name = ref.get("name") if hasattr(ref, "get") else None
        if isinstance(name, str) and name and name not in seen:
            seen.add(name)
            found.append((name, bool(ref.get("enabled", True))))
    return found


def _components(obj, ctx: _Context) -> tuple:
    stored = _system_props(obj)
    comps = {}
    off = []
    for name, enabled in component_refs(obj):
        raw = stored.get(COMPONENT_PREFIX + name) if stored is not None else None
        values = {}
        if raw is not None and hasattr(raw, "keys"):
            for key in sorted(raw.keys()):
                values[key] = ctx.value(obj.name, name, key, raw[key])
        comps[name] = values
        if not enabled:
            off.append(name)
    return comps, off


def material_shader(mat) -> str | None:
    """The game shader stored on a material (Material.gs.shader), or None; works without the add-on registered."""
    stored = _system_props(mat)
    gs = stored.get("gs") if stored is not None else None
    name = gs.get("shader") if gs is not None else None
    return name if isinstance(name, str) and name else None


def _material_entry(mat, shader: str, ctx: _Context) -> dict:
    """A material's game shader and the stored values that differ from the shader's defaults."""
    from .schema import default_value

    entry = {"name": mat.name}
    if mat.library is not None:
        entry["lib"] = ctx.lib(mat.library)
    entry["shader"] = shader
    stored = _system_props(mat)
    raw = stored.get(SHADER_PREFIX + shader) if stored is not None else None
    values = {}
    if raw is not None and hasattr(raw, "keys"):
        for key in sorted(raw.keys()):
            value = ctx.value(("material", mat.name), shader, key, raw[key], shader=True)
            field = ctx.field(shader, key, shader=True)
            try:
                if field is not None and _same(value, default_value(field)):
                    continue
            except (KeyError, TypeError):  # a malformed field in components.json
                pass
            values[key] = value
    if values:
        entry["values"] = values
    return entry


def _ai_locked(obj) -> bool:
    stored = _system_props(obj)
    gs = stored.get("gs") if stored is not None else None
    return bool(gs.get("ai_lock", False)) if gs is not None else False


def kit_info(obj):
    try:
        data = json.loads(obj.get(KIT_PROP, ""))
        return {"kind": str(data["kind"]), "params": _plain(dict(data["params"]))}
    except (TypeError, ValueError, KeyError):
        return None


def procedural_info(obj):
    """{kind, inputs, ...} for an object made by a procedural builder: its inputs that differ from the
    builder's defaults (references by name) and its extras (colliders, style, ...). None otherwise."""
    try:
        meta = json.loads(obj.get(PROCEDURAL_PROP, ""))
    except (TypeError, ValueError):
        return None
    if not isinstance(meta, dict) or not isinstance(meta.get("kind"), str):
        return None
    out = {"kind": meta["kind"]}
    if meta["kind"] == "collider_proxy":
        return out
    try:
        from ..procedural import builders, groups

        now = builders.params(obj)
        defaults = {spec.key: spec.default for spec in groups.specs(meta["kind"])}
        extras = builders.EXTRAS.get(meta["kind"], {})
    except Exception:  # an older add-on, or the modifier is gone (realized): the stored extras only
        now, defaults, extras = {}, {}, {}
    inputs = {}
    for key, value in now.items():
        if key in ("kind", "collider_object", "terrain_modifier") or key in extras:
            continue
        default = defaults.get(key)
        if value in (None, "") or _same(value, default):
            continue
        inputs[key] = _plain(value)
    if inputs:  # what it is built from first, then the other inputs
        refs = ("surface", "sources", "source", "curve", "path", "terrain", "keep_out")
        ordered = sorted(inputs.items(), key=lambda item: (item[0] not in refs, item[0]))
        out["inputs"] = dict(ordered[:MAX_ITEMS + 4])
    for key, value in sorted(meta.items()):
        if key not in ("kind", "version", "conform") and not _same(value, extras.get(key, object())):
            out[key] = _plain(value)
    return out


def _same(value, default) -> bool:
    if isinstance(value, float) and isinstance(default, (int, float)) and not isinstance(default, bool):
        return abs(value - default) < 1e-6
    if isinstance(value, (list, tuple)) and isinstance(default, (list, tuple)) and len(value) == len(default):
        return all(_same(a, b) for a, b in zip(value, default))
    if isinstance(value, str) and isinstance(default, str):
        return value.lower() == default.lower()
    return value == default


def is_prefab(coll) -> bool:
    return coll is not None and bool(coll.get(PREFAB_PROP))


# --- Entries -----------------------------------------------------------------------------------


def _collection_order() -> tuple:
    """(ordered collections, {collection: tree parent name}, {collection: excluded}) for the file."""
    order = []
    parents = {}
    excluded = {}
    seen = set()

    def walk(coll, parent_name, layer):
        for child in coll.children:
            child_layer = layer.children.get(child.name) if layer is not None else None
            if child not in seen:
                seen.add(child)
                order.append(child)
                parents[child] = parent_name
                if child_layer is not None and child_layer.exclude:
                    excluded[child] = True
            walk(child, child.name, child_layer)

    for scene in bpy.data.scenes:
        layer = scene.view_layers[0].layer_collection if len(scene.view_layers) else None
        walk(scene.collection, scene.collection.name, layer)
    rest = [c for c in bpy.data.collections if c not in seen]
    rest.sort(key=lambda c: (c.library.filepath if c.library else "", c.name))
    for coll in rest:  # collections kept outside scenes (prefab sources, spawnables)
        for child in coll.children:
            if child not in seen:
                parents.setdefault(child, coll.name)
    return order + rest, parents, excluded


def _objects() -> list:
    """Scene objects (local or linked) and local objects kept in local collections outside scenes."""
    found = {}
    for scene in bpy.data.scenes:
        for obj in scene.objects:
            found.setdefault(obj, None)
    for coll in bpy.data.collections:
        if coll.library is None:
            for obj in coll.objects:
                found.setdefault(obj, None)
    return list(found)


def _anim(obj) -> list:
    data = obj.animation_data
    if data is None:
        return []
    names = set()
    if data.action is not None:
        names.add(data.action.name)
    for track in data.nla_tracks:
        for strip in track.strips:
            if strip.action is not None:
                names.add(strip.action.name)
    return sorted(names)


def _custom_props(obj) -> dict:
    found = {}
    for key in sorted(obj.keys()):
        if key in HIDDEN_PROPS or key.startswith(("_", "cycles")):
            continue
        found[key] = _plain(obj[key])
        if len(found) >= MAX_ITEMS:
            break
    return found


def _mesh_counts(mesh) -> tuple:
    # Triangles of n-gons without iterating: sum(len(p) - 2) == loops - 2 * polygons.
    return len(mesh.vertices), max(0, len(mesh.loops) - 2 * len(mesh.polygons))


def _is_detailed(obj) -> bool:
    """Objects that matter most to an agent keep their line when the scene is too big."""
    return bool(component_refs(obj) or obj.get(KIT_PROP) or obj.get(PROCEDURAL_PROP)
                or obj.instance_type == "COLLECTION"
                or obj.type in ("CAMERA", "LIGHT", "ARMATURE") or obj.library is not None)


def _object_entry(obj, ctx: _Context, coll_index: dict, multi_scene: bool) -> dict:
    entry = {"name": obj.name, "type": obj.type}
    colls = sorted(obj.users_collection, key=lambda c: coll_index.get(c, -1))
    if colls:
        entry["coll"] = colls[0].name if len(colls) == 1 else [c.name for c in colls]
    if multi_scene:
        scenes = [s.name for s in obj.users_scene]
        entry["scene"] = scenes[0] if len(scenes) == 1 else scenes
    if obj.parent is not None:
        entry["parent"] = obj.parent.name
        if obj.parent_type == "BONE" and obj.parent_bone:
            entry["parent_bone"] = obj.parent_bone
    gs_id = ids.get_id(obj)
    if gs_id:
        entry["id"] = gs_id
    if obj.library is not None:
        entry["lib"] = ctx.lib(obj.library)
    elif obj.override_library is not None and obj.override_library.reference is not None:
        entry["override"] = ctx.lib(obj.override_library.reference.library)

    basis = obj.matrix_basis
    entry["loc"] = _vec(basis.to_translation(), 3)
    rot = _vec((math.degrees(a) for a in basis.to_euler("XYZ")), 2)
    if any(rot):
        entry["rot"] = rot
    scale = _vec(basis.to_scale(), 3)
    if any(s != 1 for s in scale):
        entry["scale"] = scale
    dims = _vec(obj.dimensions, 3)
    if any(dims):
        entry["dim"] = dims

    data = obj.data
    if obj.type == "MESH" and data is not None:
        entry["verts"], entry["tris"] = _mesh_counts(data)
        if data.users > 1 + int(data.use_fake_user):
            entry["data"] = data.name  # shared with other objects (linked duplicates)
    elif obj.type == "ARMATURE" and data is not None:
        entry["bones"] = len(data.bones)
    elif obj.type == "CAMERA" and data is not None:
        cam = {"type": data.type}
        if data.type == "ORTHO":
            cam["ortho_scale"] = _num(data.ortho_scale, 3)
        else:
            cam["lens"] = _num(data.lens, 1)
        entry["camera"] = cam
    elif obj.type == "LIGHT" and data is not None:
        light = {"type": data.type, "energy": _num(data.energy, 3)}
        color = _vec(data.color, 3)
        if color != [1, 1, 1]:
            light["color"] = color
        entry["light"] = light

    mods = [f"{m.type}:{m.node_group.name}" if m.type == "NODES" and m.node_group else m.type
            for m in obj.modifiers]
    if mods:
        entry["mods"] = _capped(mods)
    mats = [slot.material.name if slot.material else None for slot in obj.material_slots]
    if mats:
        entry["mats"] = _capped(mats)

    coll = obj.instance_collection if obj.instance_type == "COLLECTION" else None
    if coll is not None:
        entry["inst"] = coll.name
        if coll.library is not None:
            entry["inst_lib"] = ctx.lib(coll.library)
        if is_prefab(coll):
            entry["prefab"] = True

    kit = kit_info(obj)
    if kit is not None:
        entry["kit"] = kit
    built = procedural_info(obj)
    if built is not None:
        entry["procedural"] = built
    comps, off = _components(obj, ctx)
    if comps:
        entry["comps"] = comps
    if off:
        entry["comps_off"] = off
    anim = _anim(obj)
    if anim:
        entry["anim"] = _capped(anim)
    props = _custom_props(obj)
    if props:
        entry["props"] = props
    if obj.hide_viewport:
        entry["hide_viewport"] = True
    if _ai_locked(obj):
        entry["ai_lock"] = True
    return entry


def _name_groups(names: list) -> list:
    groups: dict = {}
    for name in names:
        base = re.sub(r"[._ -]?\d+$", "", name) or name
        groups[base] = groups.get(base, 0) + 1
    ordered = sorted(groups.items(), key=lambda kv: (-kv[1], kv[0]))
    shown = [f"{base} ×{n}" if n > 1 else base for base, n in ordered[:MAX_NAME_GROUPS]]
    if len(ordered) > MAX_NAME_GROUPS:
        shown.append(f"+{len(ordered) - MAX_NAME_GROUPS} more names")
    return shown


def _summaries(objs: list, coll_index: dict) -> list:
    """One line per collection for objects that did not get their own line."""
    by_coll: dict = {}
    for obj in objs:
        colls = sorted(obj.users_collection, key=lambda c: coll_index.get(c, -1))
        key = colls[0].name if colls else ""
        by_coll.setdefault(key, []).append(obj)
    lines = []
    for name, members in by_coll.items():
        types: dict = {}
        tris = 0
        for obj in members:
            types[obj.type] = types.get(obj.type, 0) + 1
            if obj.type == "MESH" and obj.data is not None:
                tris += _mesh_counts(obj.data)[1]
        line = {"coll": name, "objects": len(members), "types": dict(sorted(types.items()))}
        if tris:
            line["tris"] = tris
        line["names"] = _name_groups(sorted(o.name for o in members))
        lines.append(line)
    return lines


def _background(world) -> list | None:
    if world is None:
        return None
    tree = world.node_tree
    if tree is not None:
        for node in tree.nodes:
            if node.type == "BACKGROUND":
                color = node.inputs.get("Color")
                if color is not None and not color.is_linked:
                    return _vec(color.default_value[:3], 3)
                break
    return _vec(world.color[:3], 3)


def _scene_entry(scene) -> dict:
    entry = {"name": scene.name, "camera": scene.camera.name if scene.camera else None,
             "fps": _num(scene.render.fps / (scene.render.fps_base or 1.0), 3),
             "gravity": _vec(scene.gravity, 3) if scene.use_gravity else [0, 0, 0]}
    if scene.world is not None:
        entry["world"] = scene.world.name
        entry["background"] = _background(scene.world)
    if abs(scene.unit_settings.scale_length - 1.0) > 1e-6:
        entry["unit_scale"] = _num(scene.unit_settings.scale_length, 4)
    if len(scene.view_layers) > 1:
        entry["view_layers"] = [v.name for v in scene.view_layers]
    return entry


def _collection_entry(coll, ctx: _Context, parents: dict, excluded: dict, instancers: dict) -> dict:
    entry = {"name": coll.name}
    if coll.library is not None:
        entry["lib"] = ctx.lib(coll.library)
    if coll in parents:
        entry["parent"] = parents[coll]
    entry["objects"] = len(coll.objects)
    if excluded.get(coll):
        entry["excluded"] = True
    if coll.hide_viewport:
        entry["hide_viewport"] = True
    if is_prefab(coll):
        entry["prefab"] = True
    meta = coll.asset_data
    if meta is not None:
        asset = {}
        catalog = ctx.catalogs(coll.library).get(meta.catalog_id) or meta.catalog_simple_name
        if catalog:
            asset["catalog"] = catalog
        tags = sorted(t.name for t in meta.tags)
        if tags:
            asset["tags"] = _capped(tags)
        if meta.description:
            asset["description"] = _text(meta.description)
        entry["asset"] = asset
    offset = _vec(coll.instance_offset, 3)
    if any(offset):
        entry["offset"] = offset
    if instancers.get(coll):
        entry["instances"] = instancers[coll]
    return entry


def build(blend_path=None, previous: dict | None = None) -> dict:
    """The twin of the open file as a dict, in output order. `previous` is the last twin, if any."""
    blend_path = pathlib.Path(blend_path or bpy.data.filepath)
    root = project.find_project_root(str(blend_path))
    ctx = _Context(blend_path, root, previous)
    file_label = ctx.rel(str(blend_path)) if root is not None else blend_path.name

    collections, parents, excluded = _collection_order()
    coll_index = {c: i for i, c in enumerate(collections)}
    for scene in bpy.data.scenes:
        coll_index[scene.collection] = -1
    objs = _objects()
    objs.sort(key=lambda o: (min((coll_index.get(c, -1) for c in o.users_collection), default=-2),
                             o.name, ctx.lib(o.library)))

    if len(objs) > MAX_OBJECTS:
        ranked = sorted(range(len(objs)), key=lambda i: (not _is_detailed(objs[i]), i))
        keep = set(ranked[:MAX_OBJECTS])
        listed = [o for i, o in enumerate(objs) if i in keep]
        rest = [o for i, o in enumerate(objs) if i not in keep]
    else:
        listed, rest = objs, []

    types: dict = {}
    verts = tris = with_comps = instances = 0
    materials = set()
    instancers: dict = {}
    for obj in objs:
        types[obj.type] = types.get(obj.type, 0) + 1
        if obj.type == "MESH" and obj.data is not None:
            v, t = _mesh_counts(obj.data)
            verts += v
            tris += t
        if component_refs(obj):
            with_comps += 1
        if obj.instance_type == "COLLECTION" and obj.instance_collection is not None:
            instances += 1
            instancers[obj.instance_collection] = instancers.get(obj.instance_collection, 0) + 1
        for slot in obj.material_slots:
            if slot.material is not None:
                materials.add(slot.material)

    stats = {"objects": len(objs), "listed": len(listed), "types": dict(sorted(types.items())), "verts": verts,
             "tris": tris, "with_components": with_comps, "instances": instances, "materials": len(materials),
             "collections": len(collections)}
    multi_scene = len(bpy.data.scenes) > 1

    doc = {
        "format": FORMAT,
        "version": VERSION,
        "file": file_label,
        "about": ABOUT.format(file=file_label),
        "stats": stats,
        "scenes": [_scene_entry(s) for s in bpy.data.scenes],
        "collections": [_collection_entry(c, ctx, parents, excluded, instancers)
                        for c in collections[:MAX_COLLECTIONS]],
        "objects": [_object_entry(o, ctx, coll_index, multi_scene) for o in listed],
    }
    if len(collections) > MAX_COLLECTIONS:
        doc["collections_omitted"] = len(collections) - MAX_COLLECTIONS
    if rest:
        doc["more"] = _summaries(rest, coll_index)
    shaded = [(m, material_shader(m)) for m in sorted(bpy.data.materials, key=lambda m: (ctx.lib(m.library), m.name))]
    shaded = [_material_entry(m, shader, ctx) for m, shader in shaded if shader is not None]
    if shaded:
        doc["about"] += ABOUT_MATERIALS
        doc["materials"] = shaded
    libraries = []
    for lib in sorted(bpy.data.libraries, key=lambda lib: ctx.lib(lib)):
        absolute = bpy.path.abspath(lib.filepath, start=ctx.base_dir)
        entry = {"path": ctx.lib(lib),
                 "collections": sum(1 for c in bpy.data.collections if c.library == lib),
                 "objects": sum(1 for o in bpy.data.objects if o.library == lib)}
        if not os.path.exists(absolute):
            entry["missing"] = True
        libraries.append(entry)
    if libraries:
        doc["libraries"] = libraries[:MAX_LIBRARIES]
    return doc


# --- Text --------------------------------------------------------------------------------------


def _dumps(value) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def render(doc: dict) -> str:
    """Pretty enough to read, compact enough to be cheap: lists of entries get one line each."""
    lines = ["{"]
    keys = list(doc)
    for i, key in enumerate(keys):
        value = doc[key]
        comma = "," if i < len(keys) - 1 else ""
        if isinstance(value, list) and value and all(isinstance(v, dict) for v in value):
            lines.append(f"{_dumps(key)}: [")
            for j, item in enumerate(value):
                lines.append("  " + _dumps(item) + ("," if j < len(value) - 1 else ""))
            lines.append("]" + comma)
        else:
            lines.append(f"{_dumps(key)}: {_dumps(value)}{comma}")
    lines.append("}")
    return "\n".join(lines) + "\n"


def read_twin(blend_path=None) -> dict | None:
    """The twin written for a .blend (default: the open file), or None."""
    path = twin_path(blend_path or bpy.data.filepath)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def write_twin(blend_path=None, force: bool = False) -> dict | None:
    """Write <name>.scene.json next to the .blend (default: the open file) from the open file's data.

    Returns {"path", "changed", "bytes", "objects"}, or None if the file is not in a project (or
    twins are off there) and force is False. The file is only rewritten when its text changes.
    """
    blend_path = blend_path or bpy.data.filepath
    if not blend_path:
        return None
    if not force and not wants_twin(blend_path):
        return None
    path = twin_path(blend_path)
    try:
        old = path.read_text(encoding="utf-8")
    except OSError:
        old = None
    previous = None
    if old:
        try:
            previous = json.loads(old)
        except ValueError:
            previous = None
    doc = build(blend_path, previous)
    text = render(doc)
    changed = old is None or old.replace("\r\n", "\n") != text
    if changed:
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_bytes(text.encode("utf-8"))
        os.replace(tmp, path)
    return {"path": str(path), "changed": changed, "bytes": len(text.encode("utf-8")),
            "objects": doc["stats"]["objects"]}


@persistent
def _on_save_post(filepath=None, *_args):
    path = filepath if isinstance(filepath, str) and filepath else bpy.data.filepath
    try:
        result = write_twin(path)
    except Exception:  # never let the twin break saving
        log.exception("Could not write the scene twin for %s", path)
        return
    if result and result["changed"]:
        log.info("Scene twin: %s (%d objects, %d bytes)", result["path"], result["objects"], result["bytes"])


def register():
    if _on_save_post not in bpy.app.handlers.save_post:
        bpy.app.handlers.save_post.append(_on_save_post)


def unregister():
    if _on_save_post in bpy.app.handlers.save_post:
        bpy.app.handlers.save_post.remove(_on_save_post)
