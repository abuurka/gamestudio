# SPDX-License-Identifier: GPL-3.0-or-later
"""The kit registry, parameter schemas and validation, and object creation.

A kit is a function build(mesh: MeshBuilder, p: dict, rng: random.Random) registered with @kit(...).
It adds geometry around the origin (metres, Z-up, front = -Y); create() turns that into one mesh
object at the requested location. Parameters are declared as JSON Schema and validated here, so the
build functions can trust every value they read.

A rigged kit also passes rig(p) -> ([rig.Bone], layout dict) and optionally clips(p) -> [clip kind].
create() then makes two objects: an armature at the location (the root, for components) and the
mesh as its skinned child, weighted by the build function with MeshBuilder.weigh(). Both carry
obj["gs_kit"].

An assembly kit passes parts(p) -> [Part]: child objects (meshes with their own build function, or
empties) that move on their own, such as wheels, a door leaf or a muzzle point. Each is named
<root name>_<suffix>, parented to the root with its origin at Part.location and carries
obj["gs_kit"] plus obj["gs_kit_part"] = suffix. Part clips are added to the child.

A kit with root="sun" builds no mesh: build(obj, p, rng) configures a sun lamp object (and the scene).
"""

import json
import math
import random
import re
from typing import NamedTuple

import bpy

from . import geometry, materials
from .errors import KitError

KIT_PROP = "gs_kit"  # obj[KIT_PROP] = '{"kind": ..., "params": {...}}' (what built it; see regenerate())
PART_PROP = "gs_kit_part"  # on an assembly's child: its suffix (Wheel_FL, Leaf, Muzzle)
SEED_MAX = 2**31 - 1
HEX_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")

PIVOTS = {
    "base": "origin at the bottom centre",
    "center": "origin at the centre (it spins in place)",
    "ground": "origin at the centre, at ground level",
    "grip": "origin at the grip, where a hand holds it",
}

_KITS: dict = {}


class Part(NamedTuple):
    """A child object of an assembly kit."""
    suffix: str                   # named <root name>_<suffix>
    location: tuple               # its origin, in the root's axes
    build: object = None          # build(m, p, rng) for a mesh around its own origin; None: an empty
    clips: tuple = ()             # (clip kind, clip params, clip name) added to the child
    display: str = "PLAIN_AXES"   # an empty's display type
    size: float = 0.1             # an empty's display size


class Kit:
    def __init__(self, kind, label, description, params, build, max_tris, pivot, closed, rig=None, clips=None,
                 parts=None, root="mesh"):
        self.kind = kind
        self.label = label
        self.description = description
        self.params = params          # property name -> JSON Schema
        self.build = build
        self.max_tris = max_tris      # triangle budget at any valid params
        self.pivot = pivot            # a key of PIVOTS
        self.closed = closed          # every shell is closed (watertight)
        self.rig = rig                # rig(p) -> ([rig.Bone], layout) for an armature + skinned mesh
        self.clips = clips            # clips(p) -> clip kinds a rigged kit adds (see animation.py); for a
        #                               mesh kit: [(clip kind, clip params, clip name)] on the object
        self.parts = parts            # parts(p) -> [Part]: child objects of an assembly
        self.root = root              # "mesh", or "sun": build(obj, p, rng) sets up a sun lamp

    def schema(self) -> dict:
        return {"type": "object", "additionalProperties": False,
                "properties": json.loads(json.dumps(self.params))}

    def entry(self) -> dict:
        if self.root != "mesh":
            return {"kind": self.kind, "label": self.label, "description": self.description, "params": self.schema()}
        pivot = PIVOTS[self.pivot]
        text = f"{self.description} Front faces -Y; {pivot}; at most {self.max_tris} triangles"
        text += " in all." if self.parts is not None else "."
        if self.parts is not None:
            text += (" create() returns the root first, then its children <name>_<Part>; "
                     "regenerate() and kit_params() accept any of them.")
        if self.rig is not None:
            text += (" Creates two objects: an armature at the location (the root: put components such as "
                     "Animator on it) and the skinned mesh <name>_Mesh as its child.")
        return {"kind": self.kind, "label": self.label, "description": text, "params": self.schema()}


def kit(kind, *, label, description, params, max_tris=0, pivot="base", closed=True, rig=None, clips=None,
        parts=None, root="mesh", default_name=None):
    """Register a build function as a kit. `params` maps names to schemas from the helpers below;
    every kit also gets `seed` and `name`. `rig` and `clips` make it a rigged kit, `parts` an
    assembly, root="sun" a sun lamp kit (see above)."""
    def register(build):
        props = dict(params)
        props["seed"] = integer(0, 0, SEED_MAX, "Variation seed: the same seed and params always build the same mesh")
        props["name"] = {"type": "string", "default": default_name or label, "minLength": 1, "maxLength": 60,
                         "description": "Object name (Blender adds .001 etc. if it is taken)"}
        _KITS[kind] = Kit(kind, label, description, props, build, max_tris, pivot, closed, rig, clips, parts, root)
        return build
    return register


# ------------------------------------------------------------------ schema helpers


def number(default, minimum, maximum, description):
    return {"type": "number", "default": default, "minimum": minimum, "maximum": maximum,
            "description": description}


def integer(default, minimum, maximum, description):
    return {"type": "integer", "default": default, "minimum": minimum, "maximum": maximum,
            "description": description}


def boolean(default, description):
    return {"type": "boolean", "default": default, "description": description}


def choice(default, options, description):
    return {"type": "string", "enum": list(options), "default": default, "description": description}


def material(default, description="Material preset", auto=False):
    names = (["auto"] if auto else []) + materials.preset_names()
    return choice(default, names, description + " (see the materials presets)")


def color(default, description, auto=True):
    pattern = r"^(auto|#[0-9a-fA-F]{6})$" if auto else HEX_COLOR.pattern
    return {"type": "string", "default": default, "pattern": pattern, "description": description}


# ------------------------------------------------------------------ validation


def resolve(k: Kit, params) -> dict:
    """Defaults filled in and every value checked. Raises KitError with a readable message."""
    return validate(k.kind, k.params, params)


def validate(where: str, schemas: dict, params) -> dict:
    """`params` checked against {name: schema}, with the defaults filled in (kits, animation clips)."""
    if params is None:
        params = {}
    if not isinstance(params, dict):
        raise KitError(f"{where}: params must be an object, not {type(params).__name__}")
    unknown = sorted(set(params) - set(schemas))
    if unknown:
        raise KitError(f"{where}: unknown param(s) {', '.join(unknown)}. "
                       f"Valid params: {', '.join(sorted(schemas)) or 'none'}")
    return {name: _check(where, name, schema, params.get(name, schema["default"]))
            for name, schema in schemas.items()}


def _check(kind, name, schema, value):
    where = f"{kind}.{name}"
    t = schema["type"]
    if t in ("number", "integer"):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise KitError(f"{where} must be a {t}, got {value!r}")
        if t == "integer":
            if float(value) != int(value):
                raise KitError(f"{where} must be a whole number, got {value!r}")
            value = int(value)
        else:
            value = float(value)
        if not schema["minimum"] <= value <= schema["maximum"]:
            raise KitError(f"{where} must be between {schema['minimum']} and {schema['maximum']}, got {value}")
        return value
    if t == "boolean":
        if not isinstance(value, bool):
            raise KitError(f"{where} must be true or false, got {value!r}")
        return value
    if t == "string":
        if not isinstance(value, str):
            raise KitError(f"{where} must be a string, got {value!r}")
        if "enum" in schema and value not in schema["enum"]:
            raise KitError(f"{where} must be one of {', '.join(schema['enum'])}; got {value!r}")
        if "pattern" in schema and not re.match(schema["pattern"], value):
            raise KitError(f"{where} must match {schema['pattern']} (a #RRGGBB hex colour), got {value!r}")
        if not schema.get("minLength", 0) <= len(value) <= schema.get("maxLength", 1 << 20):
            raise KitError(f"{where} must be 1 to {schema.get('maxLength')} characters long")
        return value
    raise KitError(f"{where}: unsupported schema type {t}")  # a bug in a kit definition


def _vec3(value, what="location"):
    try:
        x, y, z = (float(c) for c in value)
    except (TypeError, ValueError):
        raise KitError(f"{what} must be three numbers [x, y, z], got {value!r}") from None
    if not all(math.isfinite(c) for c in (x, y, z)):
        raise KitError(f"{what} must be finite, got {value!r}")
    return x, y, z


def _collection(collection):
    if collection is None:
        try:
            return bpy.context.view_layer.active_layer_collection.collection
        except AttributeError:
            return bpy.context.scene.collection
    if isinstance(collection, bpy.types.Collection):
        return collection
    if isinstance(collection, str) and collection:
        found = bpy.data.collections.get(collection)
        if found is None:
            found = bpy.data.collections.new(collection)
            bpy.context.scene.collection.children.link(found)
        return found
    raise KitError(f"collection must be a Collection or a collection name, got {collection!r}")


# ------------------------------------------------------------------ public entry points


def get(kind) -> Kit:
    k = _KITS.get(kind)
    if k is None:
        raise KitError(f"Unknown kit {kind!r}. Available kits: {', '.join(sorted(_KITS))}")
    return k


def kinds() -> list[str]:
    return sorted(_KITS)


def catalog() -> list[dict]:
    return [_KITS[kind].entry() for kind in sorted(_KITS)]


def build_mesh(k: Kit, p: dict, name: str):
    """A new mesh datablock with the kit's geometry, UVs and materials."""
    return _build(k, p, name)[0]


def _build(k: Kit, p: dict, name: str):
    """(mesh, vertex group names): the mesh as build_mesh() makes it, plus its skin groups."""
    rng = random.Random(f"{k.kind}:{p['seed']}")  # str seeds hash with SHA-512: stable across runs
    builder = geometry.MeshBuilder()
    k.build(builder, p, rng)
    mesh = builder.finish(name)
    for preset, overrides in builder.slots:
        mesh.materials.append(materials.material(preset, **overrides))
    return mesh, list(builder.groups)


def create(kind, params=None, *, location=(0, 0, 0), collection=None):
    k = get(kind)
    p = resolve(k, params)
    loc = _vec3(location)
    coll = _collection(collection)
    if k.root == "sun":
        return [_create_sun(k, p, loc, coll)]
    mesh, groups = _build(k, p, p["name"])
    if k.rig is not None:
        return _create_rigged(k, p, mesh, groups, loc, coll)
    obj = bpy.data.objects.new(p["name"], mesh)
    mesh.name = obj.name
    obj.location = loc
    coll.objects.link(obj)
    obj[KIT_PROP] = json.dumps({"kind": k.kind, "params": dict(p, name=obj.name)}, sort_keys=True)
    made = [obj]
    try:
        if k.parts is not None:
            made += _sync_parts(k, dict(p, name=obj.name), obj)
        _sync_clips(k, p, obj)
    except BaseException:
        for part in [obj, *obj.children_recursive]:
            _remove_object(part)
        raise
    _make_active(obj)
    return made


def _create_sun(k: Kit, p: dict, loc, coll):
    light = bpy.data.lights.new(p["name"], "SUN")
    obj = bpy.data.objects.new(p["name"], light)
    light.name = obj.name
    obj.location = loc
    coll.objects.link(obj)
    try:
        k.build(obj, p, random.Random(f"{k.kind}:{p['seed']}"))
    except BaseException:
        _remove_object(obj)
        raise
    obj[KIT_PROP] = json.dumps({"kind": k.kind, "params": dict(p, name=obj.name)}, sort_keys=True)
    _make_active(obj)
    return obj


def _remove_object(obj) -> None:
    data = obj.data
    bpy.data.objects.remove(obj)
    if data is not None and data.users == 0:
        if isinstance(data, bpy.types.Mesh):
            bpy.data.meshes.remove(data)
        elif isinstance(data, bpy.types.Light):
            bpy.data.lights.remove(data)


def parts(obj) -> list:
    """The child parts of an assembly kit object (root or child), in creation order."""
    root = _named(obj)
    return [c for c in root.children if c.get(PART_PROP)]


def _build_part(k: Kit, p: dict, part: Part, name: str):
    rng = random.Random(f"{k.kind}:{p['seed']}:{part.suffix}")
    builder = geometry.MeshBuilder()
    part.build(builder, p, rng)
    mesh = builder.finish(name)
    for preset, overrides in builder.slots:
        mesh.materials.append(materials.material(preset, **overrides))
    return mesh


def _sync_clips(k: Kit, p: dict, obj, old: dict | None = None) -> None:
    """Add a mesh kit's own clips (re-keyed if present); remove those `old` params had and `p` has not."""
    if k.clips is None:
        return
    from . import animation

    wanted = set()
    for clip_kind, clip_params, clip_name in k.clips(p):
        animation.add_clip(obj, clip_kind, clip_params, name=clip_name, activate=False)
        wanted.add(clip_name)
    for clip_kind, clip_params, clip_name in (k.clips(old) if old is not None else []):
        if clip_name not in wanted and any(c["name"] == clip_name for c in animation.clips(obj)):
            animation.remove_clip(obj, clip_name)


def _sync_parts(k: Kit, p: dict, root, old: dict | None = None) -> list:
    """Make root's children match k.parts(p): build or rebuild each part, add its clips, remove the
    parts and the generated clips that `old` params had but `p` no longer has. Returns the parts."""
    from . import animation

    data = root[KIT_PROP]
    have = {c[PART_PROP]: c for c in root.children if c.get(PART_PROP)}
    old_clips = {}
    if old is not None:
        for part in k.parts(old):
            old_clips[part.suffix] = {clip[2] for clip in part.clips}
    out = []
    for part in k.parts(p):
        name = f"{root.name}_{part.suffix}"
        child = have.pop(part.suffix, None)
        if child is not None and (child.type == "MESH") != (part.build is not None):
            _remove_object(child)
            child = None
        if child is None:
            mesh = _build_part(k, p, part, name) if part.build is not None else None
            child = bpy.data.objects.new(name, mesh)
            if mesh is not None:
                mesh.name = child.name
            for coll in root.users_collection:
                coll.objects.link(child)
            child.parent = root
        else:
            if child.name != name:
                child.name = name
            if part.build is not None:
                _swap_mesh(child, _build_part(k, p, part, child.data.name))
        if part.build is None:
            child.empty_display_type = part.display
            child.empty_display_size = part.size
        child.matrix_parent_inverse.identity()
        child.location = part.location
        child[KIT_PROP] = data
        child[PART_PROP] = part.suffix
        wanted = set()
        for clip_kind, clip_params, clip_name in part.clips:
            animation.add_clip(child, clip_kind, clip_params, name=clip_name, activate=False)
            wanted.add(clip_name)
        for clip_name in old_clips.get(part.suffix, set()) - wanted:
            if any(c["name"] == clip_name for c in animation.clips(child)):
                animation.remove_clip(child, clip_name)
        out.append(child)
    for child in have.values():  # parts the new params do not have (a sword has no muzzle)
        _remove_object(child)
    return out


def _create_rigged(k: Kit, p: dict, mesh, groups, loc, coll):
    from . import animation, rig

    made = []
    try:
        data = bpy.data.armatures.new(p["name"])
        arm = bpy.data.objects.new(p["name"], data)
        made.append(arm)
        data.name = arm.name
        data.display_type = "STICK"
        arm.show_in_front = True
        rig.build(data, *k.rig(p))
        arm.location = loc
        coll.objects.link(arm)
        body = bpy.data.objects.new(f"{arm.name}_Mesh", mesh)
        made.append(body)
        mesh.name = body.name
        coll.objects.link(body)
        rig.skin(body, arm, groups)
        arm[KIT_PROP] = body[KIT_PROP] = json.dumps({"kind": k.kind, "params": dict(p, name=arm.name)},
                                                    sort_keys=True)
        if k.clips is not None:
            animation.add_clips(arm, k.clips(p))
    except BaseException:
        for obj in made:
            data = obj.data
            bpy.data.objects.remove(obj)
            if isinstance(data, bpy.types.Armature) and data.users == 0:
                bpy.data.armatures.remove(data)
        if mesh.users == 0:
            bpy.data.meshes.remove(mesh)
        raise
    _make_active(arm)
    return [arm, body]


def kit_params(obj):
    """(kind, params) an object was built with, or None if a kit did not make it. params["name"] is
    the object's current name (for a rigged kit's mesh: its armature's)."""
    try:
        data = json.loads(obj.get(KIT_PROP, ""))
        kind, params = data["kind"], dict(data["params"])
    except (TypeError, ValueError, KeyError):
        return None
    if "name" in params:
        params["name"] = _named(obj).name
    return kind, params


def _named(obj):
    """The object a kit's `name` param names: the armature for a rigged kit's mesh, the root for an
    assembly's part, else obj."""
    parent = obj.parent
    if parent is not None and KIT_PROP in parent:
        if obj.get(PART_PROP) or (obj.type == "MESH" and parent.type == "ARMATURE"):
            return parent
    return obj


def regenerate(obj, params=None):
    """Rebuild a kit object's mesh in place with some params changed (e.g. {"size": 2}).

    The object keeps its name (unless params has a new "name"), transform, parent, components and
    custom properties. Materials the user assigned are kept when the new mesh has the same number
    of slots.

    For a rigged kit `obj` may be the armature or its mesh: both are rebuilt, the bones keep their
    names, and the clips the animation library made are re-baked for the new proportions.

    For an assembly `obj` may be the root or any part: the root and every part are rebuilt, parts
    keep their components and are named <root name>_<suffix>, and their clips are re-keyed.
    """
    found = kit_params(obj)
    if found is None:
        raise KitError(f"{obj.name} was not made by a kit (it has no {KIT_PROP} property)")
    kind, old = found
    k = get(kind)
    merged = {key: value for key, value in old.items() if key in k.params}
    previous = dict(merged)
    merged.update(params or {})
    p = resolve(k, merged)
    root = _named(obj)
    if k.parts is not None and root.get(PART_PROP):
        raise KitError(f"{obj.name} is a part of a {kind} whose root object was deleted; create a new {kind}")
    if k.rig is not None:
        _regenerate_rigged(k, p, obj)
    else:
        if root.name != p["name"]:
            root.name = p["name"]
            p["name"] = root.name  # Blender may have added .001
        if k.root == "sun":
            k.build(root, p, random.Random(f"{k.kind}:{p['seed']}"))
        else:
            _swap_mesh(root, build_mesh(k, p, root.data.name))
        root[KIT_PROP] = json.dumps({"kind": kind, "params": p}, sort_keys=True)
        try:
            old_p = resolve(k, previous)
        except KitError:
            old_p = None
        if k.parts is not None:
            _sync_parts(k, p, root, old_p)
        if k.root == "mesh":
            _sync_clips(k, p, root, old_p)
    try:
        bpy.context.view_layer.update()  # so obj.dimensions reports the new size right away
    except (AttributeError, RuntimeError):
        pass
    return obj


def _swap_mesh(obj, mesh):
    old_mesh = obj.data
    if len(old_mesh.materials) == len(mesh.materials):
        for i, mat in enumerate(old_mesh.materials):
            mesh.materials[i] = mat
    obj.data = mesh
    if old_mesh.users == 0:
        bpy.data.meshes.remove(old_mesh)
    mesh.name = obj.name


def rig_parts(obj):
    """(armature, mesh) of a rigged kit given either object; the mesh is None if it was deleted."""
    arm = obj if obj.type == "ARMATURE" else obj.parent
    if arm is None or arm.type != "ARMATURE" or kit_params(arm) is None:
        raise KitError(f"{obj.name} is not part of a rigged kit (no armature with {KIT_PROP} above it)")
    body = next((c for c in arm.children if c.type == "MESH" and kit_params(c) is not None), None)
    return arm, body


def _regenerate_rigged(k: Kit, p: dict, obj):
    from . import animation, rig

    arm, body = rig_parts(obj)
    if arm.name != p["name"]:
        arm.name = p["name"]
        p["name"] = arm.data.name = arm.name
        if body is not None:
            body.name = f"{arm.name}_Mesh"
    mesh, groups = _build(k, p, body.data.name if body is not None else f"{arm.name}_Mesh")
    if body is None:  # the mesh was deleted: make a new one
        body = bpy.data.objects.new(f"{arm.name}_Mesh", mesh)
        mesh.name = body.name
        for coll in arm.users_collection:
            coll.objects.link(body)
    else:
        _swap_mesh(body, mesh)
    rig.build(arm.data, *k.rig(p))
    rig.skin(body, arm, groups)
    arm[KIT_PROP] = body[KIT_PROP] = json.dumps({"kind": k.kind, "params": p}, sort_keys=True)
    animation.rebake(arm)
    if k.clips is not None:
        have = {c["kind"] for c in animation.clips(arm)}
        missing = [kind for kind in k.clips(p) if kind not in have]
        if missing:
            animation.add_clips(arm, missing, activate=False)


def _make_active(obj):
    try:
        layer = bpy.context.view_layer
        for other in layer.objects.selected:
            if other is not None:
                other.select_set(False)
        obj.select_set(True)
        layer.objects.active = obj
    except (AttributeError, RuntimeError):
        pass  # not in this view layer (an excluded collection) or no context: leave the selection
