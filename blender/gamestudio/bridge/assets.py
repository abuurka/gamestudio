# SPDX-License-Identifier: GPL-3.0-or-later
"""Bridge methods for assets: kits, import, materials and the game-readiness report."""

import json
import math
import pathlib

import bmesh
import bpy
from mathutils import Vector

from .api import BridgeError, method, names_param, param, resolve, rnd, vec
from .server import INVALID_PARAMS

# --- Kits ----------------------------------------------------------------------------------


def _kits():
    try:
        from .. import kits
    except Exception as exc:  # a kit module that fails to import must not break the other tools
        raise BridgeError(f"The asset kits failed to load: {type(exc).__name__}: {exc}",
                          hint="Build the asset with run_python meanwhile, and report the error.") from None
    return kits


@method("list_kits")
def list_kits(params):
    kits = _kits()
    catalog = kits.catalog()
    try:
        preset_names = set(kits.materials.preset_names())
    except AttributeError:
        preset_names = set()
    builders = _procedural_catalog()
    kind = params.get("kind")
    if kind:
        entry = next((k for k in catalog if k["kind"] == kind), None)
        builder = next((b for b in builders if b["kind"] == kind), None)
        if entry is None and builder is None:
            raise BridgeError(f"Unknown kit '{kind}'.", hint=f"Kits: {', '.join(k['kind'] for k in catalog)}. "
                              f"Procedural builders: {', '.join(b['kind'] for b in builders) or 'none'}.")
        if entry is None:
            return {**builder, "params": _trim_material_enums(builder["params"], preset_names),
                    "procedural": True, "create": f'create_procedural {{"kind": "{kind}", "params": {{...}}}}'}
        out = {**entry, "params": _trim_material_enums(entry["params"], preset_names),
               "material_presets": sorted(preset_names)}
        if builder is not None:
            out["also_procedural"] = (f"There is a procedural builder '{kind}' too: its schema is under "
                                      "procedural_builder; build it with create_procedural.")
            out["procedural_builder"] = {**builder, "params": _trim_material_enums(builder["params"], preset_names)}
        return out
    verbose = bool(params.get("verbose"))
    if verbose:
        catalog = [{**k, "params": _trim_material_enums(k["params"], preset_names)} for k in catalog]
    else:
        catalog = [{"kind": k["kind"], "description": k["description"],
                    "params": ", ".join(f"{name}={json.dumps(s.get('default'))}"
                                        for name, s in k["params"].get("properties", {}).items())}
                   for k in catalog]
    out = {"count": len(catalog), "kits": catalog}
    try:
        presets = kits.materials.presets()
        out["material_presets"] = ([{"name": p["name"], "description": p["description"], "aliases": p["aliases"]}
                                    for p in presets] if verbose
                                   else {p["name"]: p["description"] for p in presets})
    except AttributeError:
        pass
    if not catalog:
        out["hint"] = ("No asset kits are installed yet. Build the asset with run_python (bmesh or bpy.ops "
                       "primitives), then validate_assets.")
    elif not verbose:
        out["hint"] = 'list_kits {"kind": "<kind>"} gives one kit\'s parameters with types, ranges and descriptions.'
    if builders:
        # Procedural builders (geometry nodes): forests, roads, rivers, cliffs, villages; create_procedural.
        out["procedural"] = ([{**b, "params": _trim_material_enums(b["params"], preset_names)} for b in builders]
                             if verbose else
                             [{"kind": b["kind"], "label": b["label"], "about": _first_sentence(b["description"]),
                               "needs": b["params"].get("required", [])} for b in builders])
        if not verbose:
            out["procedural_hint"] = ('create_procedural {"kind": "scatter", "params": {"surface": "Hill", "sources": '
                                      '["Tree_1", "Rock_1"]}} builds one; list_kits {"kind": "<kind>"} gives its '
                                      "schema.")
    return out


def _first_sentence(text: str) -> str:
    head, dot, _rest = text.partition(". ")
    return head + "." if dot else text


# --- Procedural builders (gamestudio/procedural): geometry-nodes worldbuilding ------------------------


def _procedural():
    try:
        from .. import procedural
    except Exception as exc:
        raise BridgeError(f"The procedural toolkit failed to load: {type(exc).__name__}: {exc}",
                          hint="Use run_python with gs.procedural meanwhile, and report the error.") from None
    return procedural


def _procedural_catalog() -> list:
    try:
        return _procedural().catalog()
    except BridgeError:
        return []


def _procedural_result(proc, obj) -> dict:
    """What create_procedural and set_procedural return: the object, its inputs and its cost."""
    target = obj.parent if obj.parent is not None and proc.kind_of(obj) == "collider_proxy" else obj
    out = {"object": target.name, "kind": proc.kind_of(target),
           "collection": target.users_collection[0].name if target.users_collection else None,
           "params": proc.params(target)}
    try:
        out["stats"] = proc.stats(target)
    except Exception as exc:  # the stats are a report; never fail the edit over them
        out["stats_error"] = f"{type(exc).__name__}: {exc}"
    return out


@method("create_procedural", mutating=True)
def create_procedural(params):
    proc = _procedural()
    kind = param(params, "kind", str)
    entries = {e["kind"]: e for e in proc.catalog()}
    if kind not in entries:
        raise BridgeError(f"Unknown procedural builder '{kind}'.", hint=f"Builders: {', '.join(entries)}.",
                          code=INVALID_PARAMS)
    entry = entries[kind]
    args = params.get("params") or {}
    if not isinstance(args, dict):
        raise BridgeError("params must be an object of builder parameters.", code=INVALID_PARAMS)
    known = entry["params"].get("properties", {})
    unknown = sorted(k for k in args if k not in known)
    if unknown:
        raise BridgeError(f"{kind}: unknown param(s) {', '.join(unknown)}.", hint=f"Params: {', '.join(known)}.",
                          code=INVALID_PARAMS)
    missing = [k for k in entry["params"].get("required", []) if k not in args]
    if missing:
        raise BridgeError(f"{kind} needs {', '.join(missing)}.",
                          hint=f'list_kits {{"kind": "{kind}"}} describes every param.', code=INVALID_PARAMS)
    for key in ("name", "collection"):
        if params.get(key) is not None:
            args = {**args, key: param(params, key, str)}
    obj = getattr(proc, entry["function"])(**args)  # ProceduralError (a ValueError) says what to change
    bpy.context.view_layer.update()
    return _procedural_result(proc, obj)


@method("set_procedural", mutating=True)
def set_procedural(params):
    proc = _procedural()
    obj = resolve(param(params, "object", str), writable=True)
    values = param(params, "params", dict)
    if not values:
        raise BridgeError("params is empty: pass the inputs to change, e.g. {\"density\": 0.1}.", code=INVALID_PARAMS)
    proc.set_params(obj, **values)  # validated; writes the modifier inputs and tags the object for update
    bpy.context.view_layer.update()
    return _procedural_result(proc, obj)


def _trim_material_enums(schema: dict, presets: set) -> dict:
    """Replace enums that list the material presets with a pointer to material_presets (said once)."""
    props = {}
    for name, sub_schema in schema.get("properties", {}).items():
        sub_schema = dict(sub_schema)
        if presets and set(sub_schema.get("enum", [])) >= presets:
            sub_schema.pop("enum")
            sub_schema["description"] = (sub_schema.get("description", "") + " (a material preset: see "
                                         "material_presets)").strip()
        props[name] = sub_schema
    return {**schema, "properties": props}


def _target_collection(name: str | None):
    if not name:
        return None
    coll = bpy.data.collections.get(name)
    if coll is None:
        coll = bpy.data.collections.new(name)
        bpy.context.scene.collection.children.link(coll)
    return coll


@method("create_from_kit", mutating=True)
def create_from_kit(params):
    items = params.get("items")
    if items is None:
        return _create_one(params)
    if not isinstance(items, list) or not items or not all(isinstance(i, dict) for i in items):
        raise BridgeError("items must be a non-empty list of {kind, params?, location?, name?, collection?}.",
                          code=INVALID_PARAMS)
    kits = _kits()
    defaults = {k: v for k, v in params.items() if k not in ("items",)}
    for i, item in enumerate(items):  # check every item, params included, before building any
        try:
            _check_item({**defaults, **item})
        except BridgeError as exc:
            exc.message = f"items[{i}]: {exc.message} Nothing was built."
            exc.args = (exc.message,)
            raise
    made = []
    for i, item in enumerate(items):
        try:
            made.append(_create_one({**defaults, **item}))
        except BridgeError as exc:
            exc.message = f"items[{i}]: {exc.message} ({len(made)} built before it; undo removes them)"
            exc.args = (exc.message,)
            raise
    return {"created": len(made), "items": [{"kind": m["kind"], "root": m["root"],
                                             "location": m["created"][0]["location"] if m["created"] else None,
                                             "dimensions": m["created"][0]["dimensions"] if m["created"] else None}
                                            for m in made]}


def _kit_request(params) -> tuple:
    """(kind, kit params with the name folded in, location) of one create_from_kit call, checked."""
    kits = _kits()
    kind = param(params, "kind", str)
    kit_params = params.get("params") or {}
    if not isinstance(kit_params, dict):
        raise BridgeError("params must be an object of kit parameters.", code=INVALID_PARAMS)
    location = vec(params, "location", default=[0.0, 0.0, 0.0])
    catalog = {k.get("kind"): k for k in kits.catalog()}
    if kind not in catalog:
        raise BridgeError(f"Unknown kit '{kind}'.",
                          hint=f"Available kits: {', '.join(sorted(catalog))}." if catalog
                          else "No kits are installed; use run_python to build the asset.", code=INVALID_PARAMS)
    name = params.get("name")
    if name is not None and (not isinstance(name, str) or not name):
        raise BridgeError("name must be a non-empty string.", code=INVALID_PARAMS)
    if name and "name" not in kit_params and "name" in catalog[kind].get("params", {}).get("properties", {}):
        # The kit names every object it makes from it (an armature "Hero" and its mesh "Hero_Mesh").
        kit_params = {**kit_params, "name": name}
    collection = params.get("collection")
    if collection is not None and not isinstance(collection, str):
        raise BridgeError("collection must be a collection name.", code=INVALID_PARAMS)
    return kind, kit_params, location


def _check_item(params) -> None:
    """Everything create_from_kit would refuse about one item, without building it."""
    kits = _kits()
    kind, kit_params, _location = _kit_request(params)
    try:
        kits.base.resolve(kits.base.get(kind), kit_params)
    except kits.KitError as exc:
        raise BridgeError(f"Kit '{kind}' rejected the parameters: {exc}.",
                          hint=f'Call list_kits {{"kind": "{kind}"}} for its parameter schema.',
                          code=INVALID_PARAMS) from None


def _create_one(params):
    kits = _kits()
    kind, kit_params, location = _kit_request(params)
    name = params.get("name")
    collection = _target_collection(params.get("collection"))
    try:
        objects = kits.create(kind, kit_params, location=tuple(location), collection=collection)
    except kits.KitError as exc:
        raise BridgeError(f"Kit '{kind}' rejected the parameters: {exc}",
                          hint="Call list_kits for the kit's parameter schema.") from None
    objects = list(objects or [])
    roots = [o for o in objects if o.parent not in objects]
    if name and roots and roots[0].name.rsplit(".", 1)[0] != name:
        # A kit without a name param: rename the root, and the objects named after it the same way.
        old = roots[0].name
        for obj in objects:
            if obj.name == old or obj.name.startswith(old + "_"):
                obj.name = name + obj.name[len(old):]
    bpy.context.view_layer.update()
    return {"kind": kind, "created": [{"name": o.name, "type": o.type, "location": rnd(o.location),
                                       "dimensions": _dimensions(o)} for o in objects],
            "root": roots[0].name if roots else None}


def _dimensions(obj) -> list:
    """The object's size; for a rigged character's armature root, the size of the character it carries."""
    from .methods import hierarchy_dimensions

    carried = hierarchy_dimensions(obj)
    return carried[0] if carried is not None else rnd(obj.dimensions)


@method("regenerate_kit", mutating=True)
def regenerate_kit(params):
    kits = _kits()
    obj = resolve(param(params, "object", str), writable=True)
    changes = params.get("params") or {}
    if not isinstance(changes, dict):
        raise BridgeError("params must be an object of the kit parameters to change.", code=INVALID_PARAMS)
    found = kits.kit_params(obj)
    if found is None:
        raise BridgeError(f"'{obj.name}' was not made by a kit.",
                          hint="Only objects from create_from_kit can be regenerated; edit others with run_python.")
    try:
        kits.regenerate(obj, changes)
    except kits.KitError as exc:
        raise BridgeError(f"Kit '{found[0]}' rejected the parameters: {exc}",
                          hint="Call list_kits for the kit's parameter schema.") from None
    kind, now = kits.kit_params(obj)
    now = dict(now)
    if "name" in now:
        now["name"] = obj.name  # the object's real name, not the kit's default
    bpy.context.view_layer.update()
    return {"object": obj.name, "kind": kind, "params": now, "dimensions": rnd(obj.dimensions),
            "faces": len(obj.data.polygons) if obj.type == "MESH" else None}


# --- Import ----------------------------------------------------------------------------------

IMPORTERS = {
    ".glb": ("import_scene", "gltf"),
    ".gltf": ("import_scene", "gltf"),
    ".fbx": ("import_scene", "fbx"),
    ".obj": ("wm", "obj_import"),
}
# Importer options by operator. Without disable_bone_shape the glTF importer adds an Icosphere per armature
# (every bone's custom display shape); import_asset would move it into the level with the rest, and the
# export would ship it.
IMPORT_OPTIONS = {"gltf": {"disable_bone_shape": True}}


@method("import_asset", mutating=True)
def import_asset(params):
    path = pathlib.Path(param(params, "path", str))
    suffix = path.suffix.lower()
    if suffix not in IMPORTERS:
        raise BridgeError(f"Unsupported format '{suffix}'.", hint="Import .glb, .gltf, .fbx or .obj files.",
                          code=INVALID_PARAMS)
    if not path.is_file():
        raise BridgeError(f"{path} does not exist.", hint="Pass an absolute path or one relative to the project.")
    module, name = IMPORTERS[suffix]
    operator = getattr(getattr(bpy.ops, module), name)
    before = set(bpy.data.objects)
    try:
        result = operator(filepath=str(path), **IMPORT_OPTIONS.get(name, {}))
    except RuntimeError as exc:
        raise BridgeError(f"Importing {path.name} failed: {exc}") from None
    if "FINISHED" not in result:
        raise BridgeError(f"The importer returned {sorted(result)} for {path.name}.")
    created = [o for o in bpy.data.objects if o not in before]
    roots = [o for o in created if o.parent not in created]

    coll_name = params.get("collection", path.stem)
    if coll_name:
        coll = _target_collection(coll_name)
        for obj in created:
            for other in list(obj.users_collection):
                if other != coll:
                    other.objects.unlink(obj)
            if coll not in obj.users_collection:
                coll.objects.link(obj)
    offset = vec(params, "location")
    scale = params.get("scale")
    for root in roots:
        if offset is not None:
            root.location = Vector(root.location) + Vector(offset)
        if isinstance(scale, (int, float)) and not isinstance(scale, bool):
            root.scale = Vector(root.scale) * float(scale)
    if params.get("name") and len(roots) == 1:
        roots[0].name = params["name"]
    bpy.context.view_layer.update()
    return {"imported": path.name, "objects": len(created), "roots": [o.name for o in roots],
            "collection": coll_name or None,
            "created": [{"name": o.name, "type": o.type, "dimensions": rnd(o.dimensions)} for o in created[:100]],
            "hint": "Run validate_assets on the roots before using them in the game."}


# --- Materials -------------------------------------------------------------------------------
# The palette lives in kits.materials (shared with the kits): presets such as stone, wood,
# wood_dark, leaves, plaster, roof, metal, emissive, glass, plus aliases (dark_wood, glow, steel).

_OVERRIDE_KEYS = ("color", "roughness", "metallic", "emission", "emission_strength", "alpha")


@method("apply_material", mutating=True)
def apply_material(params):
    materials = _kits().materials
    targets = [resolve(n, writable=True) for n in names_param(params, "objects", "object")]
    extra = params.get("params") or {}
    if not isinstance(extra, dict):
        raise BridgeError("params must be an object, e.g. {\"color\": \"#C0392B\", \"roughness\": 0.5}.",
                          code=INVALID_PARAMS)
    overrides = dict(extra)
    if "base_color" in overrides:
        overrides["color"] = overrides.pop("base_color")
    unknown = [k for k in overrides if k not in _OVERRIDE_KEYS]
    if unknown:
        raise BridgeError(f"Unknown material parameter(s): {', '.join(unknown)}.",
                          hint=f"Supported: base_color (or color), {', '.join(_OVERRIDE_KEYS[1:])}.",
                          code=INVALID_PARAMS)
    preset = params.get("preset")
    if preset is None:
        if not overrides:
            raise BridgeError("Pass a preset, params, or both.",
                              hint=f"Presets: {', '.join(materials.preset_names())}.", code=INVALID_PARAMS)
        preset = "plastic"  # the neutral base for a material given only by its params
    if params.get("name"):
        overrides["name"] = params["name"]
    slot = params.get("slot")
    slots = slot if isinstance(slot, list) else [slot]  # one slot, several, or every slot (None)
    if isinstance(slot, list) and (not slot or not all(isinstance(i, int) and not isinstance(i, bool) and i >= 0
                                                     for i in slot)):
        raise BridgeError("slot must be a slot index or a list of them, e.g. [0, 2].", code=INVALID_PARAMS)
    for obj in targets:
        if obj.data is None or not hasattr(obj.data, "materials"):
            raise BridgeError(f"'{obj.name}' ({obj.type}) cannot have materials.",
                              hint="Apply materials to meshes, curves or text objects.")
    # Validate once before touching any object.
    materials.resolve(preset, {k: v for k, v in overrides.items() if k != "name"})
    applied = None
    for obj in targets:
        for index in slots:
            kwargs = dict(overrides)
            if index is not None:
                kwargs["slot"] = index
            applied = materials.apply(obj, preset, **kwargs)
    return {"material": applied.name, "preset": materials.canonical(preset),
            "overrides": {k: v for k, v in overrides.items() if k != "name"}, "objects": [o.name for o in targets],
            "slots": {o.name: [s.material.name if s.material else None for s in o.material_slots] for o in targets}}


# --- Game-readiness report -------------------------------------------------------------------

GLTF_NODE_TYPES = {
    "BSDF_PRINCIPLED", "OUTPUT_MATERIAL", "TEX_IMAGE", "NORMAL_MAP", "TEX_COORD", "MAPPING", "UVMAP",
    "SEPRGB", "SEPARATE_COLOR", "SEPXYZ", "COMBRGB", "COMBINE_COLOR", "MIX", "MIX_RGB", "EMISSION",
    "VERTEX_COLOR", "ATTRIBUTE", "BUMP", "MATH", "VALUE", "RGB", "BSDF_TRANSPARENT", "ADD_SHADER",
    "MIX_SHADER", "CURVE_RGB", "GAMMA", "INVERT", "FRESNEL", "LAYER_WEIGHT", "BRIGHTCONTRAST", "HUE_SAT",
}


def _issue(issues, severity, check, message, fix=None):
    entry = {"severity": severity, "check": check, "message": message}
    if fix:
        entry["fix"] = fix
    issues.append(entry)


def _mesh_report(obj, depsgraph, issues, max_tris: int) -> dict:
    evaluated = obj.evaluated_get(depsgraph)
    mesh = evaluated.to_mesh()
    try:
        bm = bmesh.new()
        bm.from_mesh(mesh)
        bm.verts.ensure_lookup_table()
        tris = sum(len(f.verts) - 2 for f in bm.faces)
        ngons = sum(1 for f in bm.faces if len(f.verts) > 4)
        boundary = sum(1 for e in bm.edges if len(e.link_faces) == 1)
        over = sum(1 for e in bm.edges if len(e.link_faces) > 2)
        wire = sum(1 for e in bm.edges if not e.link_faces)
        loose = sum(1 for v in bm.verts if not v.link_edges)
        degenerate = sum(1 for f in bm.faces if f.calc_area() < 1e-10)
        inconsistent = 0
        for e in bm.edges:
            if len(e.link_loops) == 2:
                a, b = e.link_loops
                if a.vert == b.vert:
                    inconsistent += 1
        volume = 0.0
        if boundary == 0 and over == 0 and bm.faces:
            volume = bm.calc_volume(signed=True)
        uv_layer = bm.loops.layers.uv.active
        zero_uv = 0
        if uv_layer is not None:
            for f in bm.faces:
                uvs = [loop[uv_layer].uv for loop in f.loops]
                area = 0.0
                for i in range(1, len(uvs) - 1):
                    a, b, c = uvs[0], uvs[i], uvs[i + 1]
                    area += abs((b.x - a.x) * (c.y - a.y) - (c.x - a.x) * (b.y - a.y)) / 2
                if area < 1e-12:
                    zero_uv += 1
        # With modifiers, the evaluated mesh: geometry nodes (procedural builders) add UV maps of their own.
        # Without, the object's own data, which is never stale (some edits do not tag it for evaluation).
        uv_names = [uv.name for uv in (mesh if obj.modifiers else obj.data).uv_layers]
        stats = {"vertices": len(bm.verts), "faces": len(bm.faces), "triangles": tris, "ngons": ngons,
                 "uv_layers": uv_names}
        bm.free()
    finally:
        evaluated.to_mesh_clear()

    if tris > max_tris:
        _issue(issues, "warning", "poly_count", f"{tris} triangles (budget {max_tris}).",
               "Reduce detail: lower subdivision levels, add a Decimate modifier, or simplify the mesh.")
    if ngons:
        _issue(issues, "info", "ngons", f"{ngons} n-gon(s); the exporter triangulates them, which can shade badly.")
    if over:
        _issue(issues, "warning", "non_manifold", f"{over} edge(s) shared by more than two faces.",
               "Select Non-Manifold in Edit Mode and separate or merge the faces.")
    if boundary:
        _issue(issues, "info" if boundary < 4 or stats["faces"] <= 2 else "warning", "open_mesh",
               f"{boundary} boundary edge(s): the mesh is not closed (fine for planes and terrain; mesh colliders "
               "and backface culling may show holes).")
    if wire or loose:
        _issue(issues, "warning", "loose_geometry", f"{wire} wire edge(s) and {loose} loose vertex(es).",
               "Edit Mode > Mesh > Clean Up > Delete Loose.")
    if degenerate:
        _issue(issues, "warning", "degenerate_faces", f"{degenerate} zero-area face(s).",
               "Edit Mode > Mesh > Clean Up > Degenerate Dissolve, or Merge by Distance.")
    if inconsistent:
        _issue(issues, "error", "normals", f"{inconsistent} edge(s) join faces with opposite winding: "
               "normals are inconsistent.", "Edit Mode > select all > Mesh > Normals > Recalculate Outside.")
    elif volume < -1e-9:
        _issue(issues, "error", "normals", "Normals point inward (the closed mesh has negative volume).",
               "Edit Mode > select all > Mesh > Normals > Recalculate Outside (or Flip).")
    if not stats["faces"]:
        pass  # only instances (a scatter, an array): their sources are checked on their own
    elif not uv_names:
        _issue(issues, "warning", "uvs", "No UV map: textures cannot be applied.",
               "Edit Mode > select all > UV > Smart UV Project.")
    elif zero_uv:
        _issue(issues, "warning", "uvs", f"{zero_uv} face(s) have zero UV area (textures smear there).",
               "Re-unwrap: UV > Smart UV Project or Unwrap.")
    return stats


def _material_report(obj, issues, has_uvs: bool = True) -> list:
    """obj is evaluated when it has modifiers: its slots then include the materials geometry nodes set."""
    mats = [slot.material for slot in obj.material_slots]
    if not mats:
        _issue(issues, "warning", "materials", "No material: the game shows it in the default grey.",
               "apply_material with a preset or base_color.")
        return []
    names = []
    for i, mat in enumerate(mats):
        if mat is None:
            _issue(issues, "warning", "materials", f"Material slot {i} is empty.", "Assign a material or remove the slot.")
            continue
        names.append(mat.name)
        tree = mat.node_tree
        if tree is None:
            continue
        unsupported = sorted({n.type for n in tree.nodes if n.type not in GLTF_NODE_TYPES and n.type != "FRAME"
                              and n.type != "REROUTE" and n.type != "GROUP_INPUT" and n.type != "GROUP_OUTPUT"})
        if unsupported:
            _issue(issues, "warning", "materials", f"'{mat.name}' uses nodes glTF cannot export: "
                   f"{', '.join(unsupported)}.", "Bake them to textures, or use a Principled BSDF with image textures.")
        if not any(n.type == "BSDF_PRINCIPLED" for n in tree.nodes):
            _issue(issues, "warning", "materials", f"'{mat.name}' has no Principled BSDF; the export may lose it.",
                   "Use a Principled BSDF (apply_material builds one).")
        for node in tree.nodes:
            if node.type == "TEX_IMAGE":
                image = node.image
                if image is None:
                    _issue(issues, "warning", "materials", f"'{mat.name}' has an Image Texture node with no image.")
                elif image.source == "FILE" and not image.packed_file and \
                        not pathlib.Path(bpy.path.abspath(image.filepath)).is_file():
                    _issue(issues, "error", "materials", f"'{mat.name}' uses a missing image file: {image.filepath}",
                           "Relink the image or pack it (File > External Data > Pack Resources).")
                if image is not None and not has_uvs:
                    _issue(issues, "error", "uvs", f"'{mat.name}' uses image textures but the mesh has no UVs.",
                           "Edit Mode > select all > UV > Smart UV Project.")
    return names


def _transform_report(obj, issues) -> dict:
    scale = list(obj.scale)
    if obj.matrix_world.determinant() < 0:
        _issue(issues, "error", "transform", f"Negative scale {rnd(scale)}: faces render inside-out in the game.",
               "Apply the scale (Object > Apply > Scale), then recalculate normals.")
    elif any(abs(s - 1.0) > 1e-4 for s in scale):
        _issue(issues, "warning", "transform", f"Unapplied scale {rnd(scale)}: colliders and physics use it, "
               "and non-uniform scale skews rotations of children.",
               "Object > Apply > Scale (run_python: bpy.ops.object.transform_apply(scale=True) with it selected).")
    rot = [round(math.degrees(a), 2) for a in obj.rotation_euler] if obj.rotation_mode not in (
        "QUATERNION", "AXIS_ANGLE") else None
    if rot and any(abs(a) > 1e-3 for a in rot):
        _issue(issues, "info", "transform", f"Rotation {rot} deg is not applied (fine for placed instances; "
               "apply it for a reusable asset).")
    dims = rnd(obj.dimensions)
    largest = max(dims) if dims else 0.0
    if obj.type == "MESH" and largest > 0:
        if largest < 0.01:
            _issue(issues, "warning", "scale", f"Very small ({dims} m). One Blender unit is one metre in the game.",
                   "Scale it up to real-world size and apply the scale.")
        elif largest > 1000:
            _issue(issues, "warning", "scale", f"Very large ({dims} m). One Blender unit is one metre in the game.",
                   "Scale it down to real-world size and apply the scale.")
    return {"location": rnd(obj.location), "rotation_deg": rot, "scale": rnd(scale), "dimensions_m": dims}


def _origin_report(obj, issues) -> dict:
    corners = [Vector(c) for c in obj.bound_box]
    lo = Vector([min(c[i] for c in corners) for i in range(3)])
    hi = Vector([max(c[i] for c in corners) for i in range(3)])
    size = hi - lo
    center = (lo + hi) / 2
    bottom = Vector((center.x, center.y, lo.z))
    span = max(size.length, 1e-6)
    origin = Vector((0, 0, 0))
    where = "custom"
    if (origin - bottom).length < 0.02 * span:
        where = "bottom-center"
    elif (origin - center).length < 0.02 * span:
        where = "center"
    inside = all(lo[i] - 0.05 * span <= 0 <= hi[i] + 0.05 * span for i in range(3))
    if not inside:
        _issue(issues, "warning", "origin", f"The origin is outside the geometry (offset {rnd(-center)} m from its "
               "center): it rotates and snaps around an unexpected point.",
               "Object > Set Origin > Origin to Geometry (or to the bottom center for props that stand on the "
               "ground).")
    return {"origin": where, "local_bbox_min": rnd(lo), "local_bbox_max": rnd(hi)}


def entity_references(obj) -> list:
    """(component, field, target or None) for every entity field of obj's attached components."""
    from ..core import components, registry

    found = []
    for comp in components.list_components(obj):
        schema = registry.get_schema(comp)
        group = registry.group(obj, comp) if schema else None
        if group is None:
            continue
        for f in schema["fields"]:
            if f["type"] == "entity":
                found.append((comp, f["name"], getattr(group, f["name"], None)))
    return found


# {"<Component>.<field>": "<deleted object>"} on an object: entity fields that a delete emptied (delete_objects,
# asset_add spawn_only). A field that was never set is an ordinary empty reference; one a delete emptied is a
# dangling reference, which validate_assets reports until set_component sets it (or remove_component removes it).
CLEARED_PROP = "gs_cleared_refs"


def mark_cleared(cleared) -> None:
    """Remember the entity fields a delete emptied: the {object, component, field, was} entries of
    cleared_references."""
    for entry in cleared:
        obj = bpy.data.objects.get(entry["object"])
        if obj is None or obj.library is not None:
            continue
        marks = _cleared_marks(obj)
        marks[f"{entry['component']}.{entry['field']}"] = entry["was"]
        obj[CLEARED_PROP] = marks


def forget_cleared(obj, component: str, fields=None) -> None:
    """set_component wrote these fields of component (None: remove_component removed all of them): they are no
    longer what a delete left behind."""
    marks = _cleared_marks(obj)
    if not marks:
        return
    keep = {key: was for key, was in marks.items()
            if not (key.split(".", 1)[0] == component and (fields is None or key.split(".", 1)[1] in fields))}
    if keep == marks:
        return
    if keep:
        obj[CLEARED_PROP] = keep
    else:
        del obj[CLEARED_PROP]


def _cleared_marks(obj) -> dict:
    marks = obj.get(CLEARED_PROP)
    if marks is None:
        return {}
    marks = marks.to_dict() if hasattr(marks, "to_dict") else marks
    return {str(k): str(v) for k, v in marks.items()} if isinstance(marks, dict) else {}


def _reference_report(obj, issues, scene=None) -> None:
    """Dangling entity fields: emptied by deleting their target, or pointing at an object outside the scene (a
    canvas, in a UI file). A field left empty on purpose (null, never set) is fine."""
    scene = scene or bpy.context.scene
    marks = _cleared_marks(obj)
    where = "canvas" if _in_ui_file() else "scene"
    for comp, field, target in entity_references(obj):
        if target is None:
            was = marks.get(f"{comp}.{field}")
            if was is not None:
                _issue(issues, "warning", "references", f"{comp}.{field} points at no object: '{was}', its target, "
                       "was deleted.",
                       f'set_component {{"objects": "{obj.name}", "component": "{comp}", "values": {{"{field}": '
                       '"<object>"}}, or null to leave it empty on purpose')
        elif target.name not in scene.objects:
            _issue(issues, "warning", "references", f"{comp}.{field} points at '{target.name}', which is not in this "
                   f"{where}.", f"Point it at an object of this {where} with set_component.")


def _in_ui_file() -> bool:
    from ..core import gameui

    return gameui.is_open_ui_file()


def _missing_file(image) -> bool:
    return (image.source == "FILE" and not image.packed_file
            and not pathlib.Path(bpy.path.abspath(image.filepath, library=image.library)).is_file())


def _ui_element_report(obj, issues) -> dict:
    """An element of a UI file's canvas. The game draws it flat and unlit (docs/GAME-UI.md §3.5): its materials
    are flat Emission by design and nothing goes through glTF, so the mesh, PBR material, UV and transform checks
    of level objects do not apply. Missing files do: fonts, images and texture images."""
    from ..gameui import build

    report = {"kind": build.kind_of(obj)}
    if obj.type == "FONT":
        font = obj.data.font
        if font is not None and font.filepath not in ("", "<builtin>") and font.packed_file is None \
                and not pathlib.Path(bpy.path.abspath(font.filepath, library=font.library)).is_file():
            _issue(issues, "error", "files", f"Uses a missing font file: {font.filepath}",
                   'ui_set {"font": "assets/fonts/<file>"} ("" is the default, Inter), or put the file back.')
    elif obj.type == "EMPTY" and obj.empty_display_type == "IMAGE":
        if obj.data is None:
            _issue(issues, "warning", "files", "An image empty without an image: the game draws nothing here.",
                   'ui_add {"kind": "image", "path": "assets/ui/<file>"} with this name sets one.')
        elif _missing_file(obj.data):
            _issue(issues, "error", "files", f"Uses a missing image file: {obj.data.filepath}",
                   'ui_add {"kind": "image", "path": "assets/ui/<file>"} with this name, or put the file back.')
    for slot in obj.material_slots:
        mat = slot.material
        if mat is None or mat.node_tree is None:
            continue
        for node in mat.node_tree.nodes:
            if node.type == "TEX_IMAGE" and node.image is not None and _missing_file(node.image):
                _issue(issues, "error", "files", f"'{mat.name}' uses a missing image file: {node.image.filepath}",
                       "Relink the image or pack it (File > External Data > Pack Resources).")
    return report


def _ui_scope() -> tuple[list, str]:
    """A UI file: the elements of every canvas (each one a scene), not only the one on screen."""
    from ..core import components, gameui

    canvases = gameui.canvas_scenes()
    objs, seen = [], set()
    for scene in canvases:
        for obj in scene.objects:
            if obj in seen:
                continue
            if (obj.type in ("MESH", "CURVE", "SURFACE", "FONT")
                    or (obj.type == "EMPTY" and obj.empty_display_type == "IMAGE")
                    or components.list_components(obj)):
                seen.add(obj)
                objs.append(obj)
    return objs, (f"all: {len(objs)} elements of the UI file's {len(canvases)} canvas(es) "
                  f"({', '.join(s.name for s in canvases)})")


def _scope(params) -> tuple[list, str]:
    from ..core import components

    names = params.get("names")
    scene = bpy.context.scene
    if names in (None, [], "all"):
        if _in_ui_file():
            return _ui_scope()
        objs = [o for o in scene.objects if o.type == "MESH" or components.list_components(o)]
        return objs, f"all: {len(objs)} meshes and objects with components"
    if names == "selection":
        objs = list(bpy.context.selected_objects)
        return objs, f"selection: {len(objs)} object(s)"
    objs = [resolve(n) for n in names_param(params, "names")]
    return objs, f"{len(objs)} named object(s)"


SUMMARY_ABOVE = 25


@method("validate_assets")
def validate_assets(params):
    targets, scope = _scope(params)
    max_tris = int(params.get("max_tris", 20000))
    depsgraph = bpy.context.evaluated_depsgraph_get()
    ui_file = _in_ui_file()
    if ui_file:
        from ..gameui import build
    reports = []
    totals = {"error": 0, "warning": 0, "info": 0}
    for obj in targets[:500]:
        issues = []
        report = {"name": obj.name, "type": obj.type}
        if ui_file:  # a canvas element: drawn flat and unlit, so only its files and references matter
            canvas = build.canvas_of(obj)
            if canvas is not None:
                report["canvas"] = canvas.name
            report.update(_ui_element_report(obj, issues))
            _reference_report(obj, issues, canvas)
        elif obj.type == "MESH" and obj.data is not None:
            report["transform"] = _transform_report(obj, issues)
            report["origin"] = _origin_report(obj, issues)
            report["mesh"] = _mesh_report(obj, depsgraph, issues, max_tris)
            if report["mesh"]["faces"]:  # an object of instances only has no surface of its own to shade
                shaded = obj.evaluated_get(depsgraph) if obj.modifiers else obj  # nodes can set materials
                report["materials"] = _material_report(shaded, issues, bool(report["mesh"]["uv_layers"]))
            if obj.modifiers:
                report["modifiers"] = [f"{m.name} ({m.type})" for m in obj.modifiers]
                _issue(issues, "info", "modifiers", "Modifiers are applied on export; counts above include them.")
        elif obj.type in ("CURVE", "FONT", "SURFACE", "META"):
            _issue(issues, "info", "type", f"{obj.type} objects are converted to meshes on export.")
        if not ui_file:
            _reference_report(obj, issues)
        for issue in issues:
            totals[issue["severity"]] += 1
        report["ok"] = not any(i["severity"] == "error" for i in issues)
        report["issues"] = issues
        reports.append(report)
    out = {"scope": scope + (" (first 500 checked)" if len(targets) > 500 else ""), "checked": len(reports),
           "ok": totals["error"] == 0, "totals": totals, "budget": {"max_tris": max_tris}}
    if params.get("verbose"):
        out["objects"] = reports
        return out
    problems = [r for r in reports if any(i["severity"] != "info" for i in r["issues"])]
    summary = params.get("summary")
    if summary is True or (summary is None and len(problems) > SUMMARY_ABOVE):
        by_check: dict[str, dict] = {}
        for r in reports:
            for i in r["issues"]:
                if i["severity"] != "info":
                    entry = by_check.setdefault(i["check"], {"error": 0, "warning": 0, "objects": []})
                    entry[i["severity"]] += 1
                    if len(entry["objects"]) < 5 and r["name"] not in entry["objects"]:
                        entry["objects"].append(r["name"])

        def rank(r):
            errors = sum(1 for i in r["issues"] if i["severity"] == "error")
            warnings = sum(1 for i in r["issues"] if i["severity"] == "warning")
            return -errors, -warnings, -r.get("mesh", {}).get("triangles", 0)

        out["by_check"] = by_check
        out["worst"] = [{"name": r["name"], "issues": [f"{i['severity']}: {i['check']}" for i in r["issues"]
                                                      if i["severity"] != "info"]}
                        for r in sorted(problems, key=rank)[:10]]
        out["hint"] = ("Summary of a big check. Pass names (up to ~25) for the full issues with fixes, or "
                       "summary: false.")
        return out
    # Compact: only objects with errors or warnings, only those issues, and the key numbers.
    compact = []
    for r in problems:
        entry = {"name": r["name"], "ok": r["ok"], "issues": [i for i in r["issues"] if i["severity"] != "info"]}
        if "canvas" in r:
            entry["canvas"] = r["canvas"]
        if "mesh" in r:
            entry["triangles"] = r["mesh"]["triangles"]
            entry["dimensions_m"] = r["transform"]["dimensions_m"]
        compact.append(entry)
    out["objects"] = compact
    clean = len(reports) - len(compact)
    if clean:
        out["clean"] = f"{clean} object(s) without errors or warnings (verbose: true shows everything)"
    return out
