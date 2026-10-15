# SPDX-License-Identifier: GPL-3.0-or-later
"""The builders: each makes one object driven by a geometry-nodes modifier (see groups.py), in a
collection named after the builder, and stores what it needs to keep in obj["gs_procedural"].

Kept beside the modifier inputs (the "extras" of each kind):
    colliders       none | simple | hull | box | trunk | mesh
    road: grade, shoulder, verge_material      river: carve, bed_material      building: style
Roads and rivers that follow a terrain also add a "GS Terrain Conform" modifier to it (a graded bed or
a carved channel); set_params() keeps it in step and remove() takes it away.
"""

import json
import math

import bpy
from mathutils import Vector

from ..core import components, registry
from ..kits import base as kit_base
from . import groups, modifiers, parts
from .errors import ProceduralError

META = "gs_procedural"
SOURCES_COLLECTION = "Procedural Sources"
COLLIDER_SUFFIX = "_Collider"
COLLIDERS = ("none", "simple", "hull", "box", "trunk", "mesh")

# kind: (modifier name, default collection, default object name)
KINDS = {
    "scatter": ("GS Scatter", "Scatter", "Scatter"),
    "array": ("GS Array Along", "Arrays", "Array"),
    "fence": ("GS Fence", "Fences", "Fence"),
    "road": ("GS Road", "Roads", "Road"),
    "river": ("GS River", "Rivers", "River"),
    "cliff": ("GS Cliff", "Cliffs", "Cliff"),
    "building": ("GS Modular Building", "Buildings", "Building"),
}
# Builders whose output is one realized mesh: "simple" colliders are a mesh Collider on the object
# itself. The others make instances: the engine fits a Collider on the object to every GPU instance
# (INSTANCE_SHAPES: a hull or a box each, or one trimesh with the object's own mesh), which adds no
# bytes to the GLB. Only "trunk" (tree kits) needs a proxy child, of cylinders that leave the canopies out.
REALIZED = {"road", "river", "cliff", "building"}
INSTANCE_SHAPES = {"hull": "convex", "box": "box", "mesh": "mesh"}
EXTRAS = {
    "scatter": {"colliders": "none"},
    "array": {"colliders": "none"},
    "fence": {"colliders": "none"},
    "road": {"colliders": "simple", "grade": True, "shoulder": 2.0, "verge_material": "dirt"},
    "river": {"colliders": "none", "carve": True, "bed_material": "sand"},
    "cliff": {"colliders": "simple"},
    "building": {"colliders": "simple", "style": "cottage"},
}
ROAD_GRADE_DEPTH = 0.05


# ------------------------------------------------------------------ collections and objects


def _scene():
    return bpy.context.scene


def sources_root():
    """The "Procedural Sources" collection, excluded from every view layer: objects that are only
    seen through instances (scatter sources, building parts, posts) live in it."""
    scene = _scene()
    coll = scene.collection.children.get(SOURCES_COLLECTION)
    if coll is None:
        coll = bpy.data.collections.get(SOURCES_COLLECTION)
        if coll is None or coll.library is not None:
            coll = bpy.data.collections.new(SOURCES_COLLECTION)
        scene.collection.children.link(coll)
    for layer in scene.view_layers:
        lc = layer.layer_collection.children.get(coll.name)
        if lc is not None and not lc.exclude:
            lc.exclude = True
    return coll


def _sources_collection(name: str):
    root = sources_root()
    coll = root.children.get(name)
    if coll is None:
        coll = bpy.data.collections.new(name)
        root.children.link(coll)
    return coll


def _collection(collection, kind):
    if isinstance(collection, bpy.types.Collection):
        return collection
    name = collection if isinstance(collection, str) and collection else KINDS[kind][1] if kind in KINDS else kind
    scene = _scene()
    coll = bpy.data.collections.get(name)
    if coll is None or coll.library is not None:
        coll = bpy.data.collections.new(name)
    if coll.name not in scene.collection.children and coll not in scene.collection.children_recursive:
        scene.collection.children.link(coll)
    return coll


def _new_object(name, coll):
    obj = bpy.data.objects.new(name, bpy.data.meshes.new(name))
    obj.data.name = obj.name
    coll.objects.link(obj)
    return obj


def _meta(obj) -> dict:
    try:
        data = json.loads(obj.get(META, ""))
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_meta(obj, data) -> None:
    obj[META] = json.dumps(data, sort_keys=True)


def kind_of(obj):
    """The builder kind that made obj (scatter, road, ...), or None."""
    return _meta(obj).get("kind") if obj is not None else None


def _modifier(obj, kind=None):
    kind = kind or kind_of(obj)
    for mod in obj.modifiers:
        if mod.type == "NODES" and mod.node_group is not None and mod.node_group.get(groups.GROUP_PROP) == kind:
            return mod
    return None


def _target(obj):
    obj = modifiers.resolve_object(obj, "object", allow_none=False)
    kind = kind_of(obj)
    if kind not in KINDS:
        if _meta(obj).get("kind") == "collider_proxy" and obj.parent is not None:
            return _target(obj.parent)
        raise ProceduralError(f"{obj.name} was not made by a procedural builder (no {META} property)")
    mod = _modifier(obj, kind)
    if mod is None:
        raise ProceduralError(f"{obj.name} has lost its {KINDS[kind][0]} modifier (realized or removed)")
    return obj, kind, mod


def _make_active(obj):
    kit_base._make_active(obj)


# ------------------------------------------------------------------ inputs


def _spec(kind, key):
    for spec in groups.specs(kind):
        if spec.key == key:
            return spec
    return None


def _apply(obj, kind, mod, values: dict) -> None:
    """Write builder inputs (by key) to the modifier, checked against the specs."""
    items = {}
    for key, value in values.items():
        spec = _spec(kind, key)
        if spec is None:
            raise ProceduralError(f"{kind}: unknown input {key!r}")
        items[spec.name] = modifiers.coerce(spec, value, kind)
    modifiers.write_inputs(mod, items)
    obj.update_tag()


def _defaults(kind) -> dict:
    return {s.key: s.default for s in groups.specs(kind) if s.default is not None
            and s.kind not in ("OBJECT", "COLLECTION")}


def _curve(value, kind, name, closed=False):
    """A path object from an object, an object name or a list of points (made into a curve)."""
    if isinstance(value, (list, tuple)) and value and not isinstance(value, str):
        if all(isinstance(p, (list, tuple)) for p in value):
            return path(value, name=f"{name}_Path", closed=closed)
    obj = modifiers.resolve_object(value, f"{kind}.curve", allow_none=False)
    if obj.type not in ("CURVE", "MESH", "CURVES"):
        raise ProceduralError(f"{kind}: {obj.name} is a {obj.type.lower()}, not a curve")
    return obj


def _objects_collection(value, kind, name, label):
    """A collection from a Collection, its name, an object, an object name or a list of those. Loose
    objects are moved into "Procedural Sources/<name> <label>" so they show only through instances."""
    if value is None:
        return None
    if isinstance(value, bpy.types.Collection):
        return value
    if isinstance(value, str) and value in bpy.data.collections and value not in bpy.data.objects:
        return bpy.data.collections[value]
    items = value if isinstance(value, (list, tuple)) else [value]
    objs = []
    for item in items:
        if isinstance(item, str) and item in parts.GENERIC:
            objs.append(parts.part(item))
        else:
            objs.append(modifiers.resolve_object(item, f"{kind}.{label.lower()}", allow_none=False))
    if not objs:
        raise ProceduralError(f"{kind}: {label.lower()} is empty")
    coll = _sources_collection(f"{name} {label}")
    for obj in list(coll.objects):
        if obj not in objs:
            coll.objects.unlink(obj)
    root = sources_root()
    inside = {c.name for c in root.children_recursive} | {root.name}
    for obj in objs:
        if obj.name not in coll.objects:
            coll.objects.link(obj)
        for other in list(obj.users_collection):
            if other.name not in inside:
                other.objects.unlink(obj)  # a source shows only through its instances
    return coll


def _keep_out_collection(value, name):
    """Keep-out objects stay where they are; a collection under Procedural Sources lists them."""
    if value is None or isinstance(value, bpy.types.Collection):
        return value
    if isinstance(value, str) and value in bpy.data.collections and value not in bpy.data.objects:
        return bpy.data.collections[value]
    items = value if isinstance(value, (list, tuple)) else [value]
    objs = [modifiers.resolve_object(item, "scatter.keep_out", allow_none=False) for item in items]
    coll = _sources_collection(f"{name} Keep Out")
    for obj in list(coll.objects):
        if obj not in objs:
            coll.objects.unlink(obj)
    for obj in objs:
        if obj.name not in coll.objects:
            coll.objects.link(obj)
    return coll


def _check_colliders(value, explicit: bool, kind):
    if value not in COLLIDERS:
        raise ProceduralError(f"{kind}.colliders must be one of {', '.join(COLLIDERS)}; got {value!r}")
    if value != "none" and registry.schema_ref("Collider") is None:
        if explicit:
            raise ProceduralError("colliders need the Collider component, and the component registry is empty: "
                                  "start the dev server (play) or pass colliders='none'")
        return "none"
    return value


# ------------------------------------------------------------------ creation


def _build(kind, name, collection, inputs: dict, extras: dict, explicit_colliders=False, location=None):
    colliders = _check_colliders(extras.get("colliders", EXTRAS[kind]["colliders"]), explicit_colliders, kind)
    coll = _collection(collection, kind)
    tree = groups.ensure(kind)
    obj = _new_object(name or KINDS[kind][2], coll)
    try:
        if location is not None:
            obj.location = location
        mod = obj.modifiers.new(KINDS[kind][0], "NODES")
        mod.node_group = tree
        values = _defaults(kind)
        values.update(inputs)
        _apply(obj, kind, mod, values)
        meta = dict(EXTRAS[kind])
        meta.update(extras)
        meta.update(kind=kind, version=groups.BUILDERS[kind][0], colliders=colliders)
        _write_meta(obj, meta)
        _sync(obj, kind, mod)
    except BaseException:
        remove(obj)
        raise
    _make_active(obj)
    return obj


def path(points, *, name="Path", closed=False, smooth=True, collection=None):
    """A curve object through `points` ([x, y] or [x, y, z] in metres; z defaults to 0): smooth (Bezier
    with automatic handles) or straight segments. A list of point lists makes one spline each (a wall
    with gaps). Builders drop it onto a terrain when given one."""
    try:
        nested = bool(points) and all(isinstance(p[0], (list, tuple)) for p in points)
    except (TypeError, IndexError, KeyError):
        nested = False
    splines = [_points(p) for p in points] if nested else [_points(points)]
    data = bpy.data.curves.new(name, "CURVE")
    data.dimensions = "3D"
    for pts in splines:
        if smooth:
            spline = data.splines.new("BEZIER")
            spline.bezier_points.add(len(pts) - 1)
            for bp, co in zip(spline.bezier_points, pts):
                bp.co = co
                bp.handle_left_type = bp.handle_right_type = "AUTO"
        else:
            spline = data.splines.new("POLY")
            spline.points.add(len(pts) - 1)
            for sp, co in zip(spline.points, pts):
                sp.co = (*co, 1.0)
        spline.use_cyclic_u = bool(closed)
    obj = bpy.data.objects.new(name, data)
    data.name = obj.name
    _collection(collection or "Paths", "path").objects.link(obj)
    return obj


def _points(points):
    pts = []
    for p in points or []:
        try:
            coords = [float(c) for c in p]
        except (TypeError, ValueError):
            raise ProceduralError(f"path points must be [x, y] or [x, y, z], got {p!r}") from None
        if len(coords) not in (2, 3) or not all(math.isfinite(c) for c in coords):
            raise ProceduralError(f"path points must be [x, y] or [x, y, z], got {p!r}")
        pts.append(Vector((coords + [0.0])[:3]))
    if len(pts) < 2:
        raise ProceduralError("a path needs at least two points")
    return pts


def kit_sources(kind: str, variants=3, params=None, *, name=None):
    """Kit objects to scatter or array: `variants` seeds of one kit (or a list of param dicts, one
    object each, over `params`), in "Procedural Sources/<name>", replacing the kit objects an earlier
    call put there. Returns the collection."""
    from .. import kits

    label = name or f"{kind.title()} Sources"
    if isinstance(variants, int):
        if not 1 <= variants <= 32:
            raise ProceduralError("variants must be 1 to 32 (or a list of param dicts)")
        variants = [{"seed": i} for i in range(variants)]
    if not isinstance(variants, (list, tuple)) or not variants:
        raise ProceduralError("variants must be a count or a list of param dicts")
    coll = _sources_collection(label)
    for old in [o for o in coll.objects if kit_base.kit_params(o) is not None]:
        _delete_object(old)  # running a recipe again replaces its sources instead of adding more
    for i, extra in enumerate(variants):
        p = dict(params or {}, **(extra or {}))
        p.setdefault("seed", i)
        p.setdefault("name", f"{label.replace(' ', '')}_{i + 1}")
        kits.create(kind, p, collection=coll)
    return coll


def part(name):
    """A generated part to use as a source: "fence_post" or "lamp_post"."""
    return parts.part(name)


def scatter(surface, sources, density=0.05, seed=0, *, min_distance=2.0, scale_range=(0.8, 1.2),
            rotation_random=1.0, align_to_normal=0.0, slope_limit=35.0, mask=None, invert_mask=False,
            keep_out=None, keep_out_distance=1.5, sink=0.1, max_instances=2000, colliders="none",
            name="Scatter", collection=None):
    """Instances of `sources` over `surface` (see the package docstring)."""
    surface_obj = modifiers.resolve_object(surface, "scatter.surface", allow_none=False)
    name = name or "Scatter"
    group, keep = "", keep_out
    if isinstance(mask, str) and _has_attribute(surface_obj, mask):
        group = mask
    elif mask is not None:
        keep = list(keep or []) + (list(mask) if isinstance(mask, (list, tuple)) else [mask])
    lo, hi = _pair(scale_range, "scatter.scale_range")
    inputs = {"surface": surface_obj, "density": density, "seed": seed, "min_distance": min_distance,
              "scale_min": lo, "scale_max": hi, "rotation_random": rotation_random,
              "align_to_normal": align_to_normal, "slope_limit": slope_limit, "mask": group,
              "invert_mask": invert_mask, "keep_out_distance": keep_out_distance, "sink": sink,
              "max_instances": max_instances}
    _check_inputs("scatter", inputs)
    inputs["sources"] = _objects_collection(sources, "scatter", name, "Sources")
    inputs["keep_out"] = _keep_out_collection(keep, name)
    return _build("scatter", name, collection, inputs, {"colliders": colliders}, colliders != "none")


def array_along(curve, source, spacing=6.0, jitter=0.0, *, offset=0.0, sides="center", start=0.0,
                rotation_jitter=0.0, scale_range=(1.0, 1.0), align=True, seed=0, terrain=None, height_offset=0.0,
                max_count=1000, colliders="none", name="Array", collection=None):
    """Copies of `source` every `spacing` metres along `curve` (see the package docstring)."""
    name = name or "Array"
    lo, hi = _pair(scale_range, "array.scale_range")
    inputs = {"spacing": spacing, "jitter": jitter, "offset": offset, "sides": sides, "start": start,
              "rotation_jitter": rotation_jitter, "scale_min": lo, "scale_max": hi, "align": align, "seed": seed,
              "height_offset": height_offset, "max_count": max_count,
              "terrain": modifiers.resolve_object(terrain, "array.terrain")}
    _check_inputs("array", inputs)
    inputs["curve"] = _curve(curve, "array", name)
    inputs["source"] = _objects_collection(source, "array", name, "Source")
    return _build("array", name, collection, inputs, {"colliders": colliders}, colliders != "none")


def fence_along(curve, post_source=None, rail_style="rails", spacing=2.0, *, height=1.0, rails=2, rail_size=0.07,
                picket_spacing=0.16, offset=0.0, terrain=None, height_offset=0.0, rail_material="wood",
                picket_material="wood", colliders="none", name="Fence", collection=None):
    """A fence along `curve`: `post_source` instanced every `spacing` metres, rails swept between them."""
    name = name or "Fence"
    inputs = {"spacing": spacing, "rail_style": rail_style, "height": height, "rails": rails,
              "rail_size": rail_size, "picket_spacing": picket_spacing, "offset": offset,
              "height_offset": height_offset, "rail_material": rail_material, "picket_material": picket_material,
              "terrain": modifiers.resolve_object(terrain, "fence.terrain")}
    _check_inputs("fence", inputs)
    if post_source is None or post_source in parts.GENERIC:
        inputs["post"] = parts.part(post_source or "fence_post")
    else:
        inputs["post"] = modifiers.resolve_object(post_source, "fence.post_source", allow_none=False)
    inputs["curve"] = _curve(curve, "fence", name)
    return _build("fence", name, collection, inputs, {"colliders": colliders}, colliders != "none")


def road_along(curve, width=6.0, kerbs=True, lane_lines=True, *, terrain=None, grade=True, shoulder=2.0,
               kerb_width=0.3, kerb_height=0.15, line_width=0.15, dash_length=2.0, dash_gap=2.5, resolution=1.0,
               height_offset=0.08, smooth=4, taper=2.0, tile=4.0, road_material="asphalt", kerb_material="concrete",
               line_material="road_line", verge_material="dirt", colliders=None, name="Road", collection=None):
    """A road mesh along `curve`, draped on `terrain` (whose ground it grades), with UVs. Open ends sink
    into the ground over `taper` metres, so its collider has no step there."""
    name = name or "Road"
    inputs = {"width": width, "kerbs": kerbs, "lane_lines": lane_lines, "kerb_width": kerb_width,
              "kerb_height": kerb_height, "line_width": line_width, "dash_length": dash_length,
              "dash_gap": dash_gap, "resolution": resolution, "height_offset": height_offset, "smooth": smooth,
              "taper": taper, "tile": tile, "road_material": road_material, "kerb_material": kerb_material,
              "line_material": line_material, "terrain": modifiers.resolve_object(terrain, "road.terrain")}
    _check_inputs("road", inputs)
    extras = {"grade": _bool(grade, "road.grade"), "shoulder": _number(shoulder, 0.0, 100.0, "road.shoulder"),
              "verge_material": _material_name(verge_material)}
    if colliders is not None:
        extras["colliders"] = colliders
    inputs["curve"] = _curve(curve, "road", name)
    return _build("road", name, collection, inputs, extras, colliders not in (None, "none"))


def river_along(curve, width=6.0, depth=1.5, *, terrain=None, carve=True, bank=3.0, water_level=0.7,
                resolution=1.5, smooth=6, tile=6.0, water_material="water", bed_material="sand", colliders="none",
                name="River", collection=None):
    """A river along `curve`: a water surface, and a channel carved into `terrain`."""
    name = name or "River"
    inputs = {"width": width, "depth": depth, "bank": bank, "water_level": water_level, "resolution": resolution,
              "smooth": smooth, "tile": tile, "water_material": water_material,
              "terrain": modifiers.resolve_object(terrain, "river.terrain")}
    _check_inputs("river", inputs)
    extras = {"colliders": colliders, "carve": _bool(carve, "river.carve"),
              "bed_material": _material_name(bed_material)}
    inputs["curve"] = _curve(curve, "river", name)
    return _build("river", name, collection, inputs, extras, colliders != "none")


def cliff(path_or_bounds, height=10.0, roughness=0.5, seed=0, *, thickness=4.0, detail=2.0, layer_height=1.6,
          batter=0.3, sink=1.0, corner_radius=None, inset=0.0, closed=None, terrain=None, top_material="grass",
          rock_material="stone", layer_material="stone_dark", colliders=None, name="Cliff", collection=None):
    """A displaced, layered wall along a path (a curve or [[x, y], ...] points), or a ring around bounds
    ([xmin, ymin, xmax, ymax] or {"min": [x, y], "max": [x, y]}) or a mesh object's bounds (a terrain: its
    rim, moved in by `inset`)."""
    name = name or "Cliff"
    line, is_ring = _wall_path(path_or_bounds, name, inset, closed)
    if corner_radius is None:  # round the corners of straight-segment paths (bounds rings); curves are smooth
        poly = line.type == "CURVE" and all(s.type == "POLY" for s in line.data.splines)
        corner_radius = min(thickness * 2.0, 12.0) if poly else 0.0
    inputs = {"height": height, "roughness": roughness, "seed": seed, "thickness": thickness, "detail": detail,
              "layer_height": layer_height, "batter": batter, "sink": sink, "corner_radius": corner_radius,
              "top_material": top_material, "rock_material": rock_material, "layer_material": layer_material,
              "terrain": modifiers.resolve_object(terrain, "cliff.terrain")}
    _check_inputs("cliff", inputs)
    inputs["path"] = line
    extras = {} if colliders is None else {"colliders": colliders}
    return _build("cliff", name, collection, inputs, extras, colliders not in (None, "none"))


def rock_wall(path_or_bounds, height=2.5, roughness=0.35, seed=0, *, thickness=1.2, detail=0.6, layer_height=0.45,
              batter=0.15, sink=0.4, **kwargs):
    """cliff() with the proportions of a low dry-stone or rock wall."""
    kwargs.setdefault("name", "Rock Wall")
    kwargs.setdefault("top_material", "stone")
    return cliff(path_or_bounds, height, roughness, seed, thickness=thickness, detail=detail,
                 layer_height=layer_height, batter=batter, sink=sink, **kwargs)


def modular_building(footprint=(8.0, 6.0), floors=2, style="cottage", seed=0, *, location=(0.0, 0.0, 0.0),
                     rotation=0.0, floor_height=3.0, module_width=2.5, window_ratio=0.85, door_side=0, roof=None,
                     roof_pitch=32.0, overhang=0.4, realize=True, terrain=None, colliders=None, name="Building",
                     collection=None):
    """A building assembled from wall, window, door and corner modules over a footprint: (width,
    depth) in metres, a list of [x, y] corners (counter-clockwise, around `location`) or a closed curve.
    `terrain` drops `location` onto the ground."""
    name = name or "Building"
    if style not in parts.STYLES:
        raise ProceduralError(f"building.style must be one of {', '.join(parts.STYLES)}; got {style!r}")
    look = parts.STYLES[style]
    loc = Vector(_vec3(location, "building.location"))
    inputs = {"floors": floors, "floor_height": floor_height, "module_width": module_width,
              "window_ratio": window_ratio, "seed": seed, "door_side": door_side,
              "roof": roof or look["roof_shape"], "roof_pitch": roof_pitch, "overhang": overhang,
              "realize": realize, "roof_material": look["roof"], "gable_material": look["gable"],
              "base_material": look["base"]}
    ground = modifiers.resolve_object(terrain, "building.terrain")
    if ground is not None:
        # Sit on the highest ground under the footprint; the plinth reaches the lowest.
        spots = _footprint_spots(footprint, loc, rotation)
        heights = [ground_height(ground, x, y, None) for x, y in spots]
        heights = [h for h in heights if h is not None] or [loc.z]
        loc.z = max(heights) - 0.1
        foundation = max(0.8, loc.z - min(heights) + 0.5)
        inputs["foundation"] = min(foundation, 20.0)
    _check_inputs("building", inputs)
    for key, obj in parts.building_parts(style).items():
        inputs[f"{key}_part"] = obj
    extras = {"style": style}
    if colliders is not None:
        extras["colliders"] = colliders
    outline = _footprint(footprint, name)
    inputs["footprint"] = outline
    obj = _build("building", name, collection, inputs, extras, colliders not in (None, "none"), location=loc)
    obj.rotation_euler = (0.0, 0.0, math.radians(_number(rotation, -3600.0, 3600.0, "building.rotation")))
    if outline.get("gs_procedural_footprint"):
        outline.parent = obj
        outline.matrix_parent_inverse.identity()
        outline.location = (0.0, 0.0, 0.0)
        outline.hide_render = True
    return obj


# ------------------------------------------------------------------ helpers for the builders


def _has_attribute(obj, name):
    if obj.type == "MESH" and (name in obj.vertex_groups or name in obj.data.attributes):
        return True
    return False


def _pair(value, label):
    try:
        lo, hi = (float(v) for v in value)
    except (TypeError, ValueError):
        raise ProceduralError(f"{label} must be [min, max], got {value!r}") from None
    if lo > hi:
        raise ProceduralError(f"{label} must be [min, max] with min <= max, got {value!r}")
    return lo, hi


def _bool(value, label):
    if not isinstance(value, bool):
        raise ProceduralError(f"{label} must be true or false, got {value!r}")
    return value


def _number(value, lo, hi, label):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not lo <= value <= hi:
        raise ProceduralError(f"{label} must be a number from {lo} to {hi}, got {value!r}")
    return float(value)


def _vec3(value, label):
    try:
        coords = [float(c) for c in value]
    except (TypeError, ValueError):
        raise ProceduralError(f"{label} must be [x, y] or [x, y, z], got {value!r}") from None
    if len(coords) not in (2, 3):
        raise ProceduralError(f"{label} must be [x, y] or [x, y, z], got {value!r}")
    return (coords + [0.0])[:3]


def _material_name(value):
    if value is None:
        return None
    mat = modifiers.material(value)
    return value if isinstance(value, str) and value in modifiers.EXTRA_MATERIALS else mat.name


def _check_inputs(kind, inputs):
    """Validate everything before any object is made (references are resolved by the callers)."""
    for key, value in inputs.items():
        spec = _spec(kind, key)
        if spec is None:
            raise ProceduralError(f"{kind}: unknown input {key!r}")
        if spec.kind not in ("OBJECT", "COLLECTION"):
            modifiers.coerce(spec, value, kind)


def ground_height(terrain, x, y, default=0.0):
    """Height of `terrain`'s evaluated surface at (x, y), or default where it is not."""
    depsgraph = bpy.context.evaluated_depsgraph_get()
    evaluated = terrain.evaluated_get(depsgraph)
    inverse = evaluated.matrix_world.inverted()
    # cast from just above the top: from far up, float32 rounds the hit to ~8 mm
    top = max((evaluated.matrix_world @ Vector(corner)).z for corner in evaluated.bound_box) + 1.0
    origin = inverse @ Vector((x, y, top))
    direction = (inverse.to_3x3() @ Vector((0.0, 0.0, -1.0))).normalized()
    hit, location, _normal, _index = evaluated.ray_cast(origin, direction, distance=2e5, depsgraph=depsgraph)
    return (evaluated.matrix_world @ location).z if hit else default


def _wall_path(value, name, inset, closed):
    """(path object, is a closed ring) for cliff(): a curve, [[x, y], ...] points, bounds as
    {"min": [x, y], "max": [x, y]} or [xmin, ymin, xmax, ymax], or a mesh object (its bounds)."""
    corners = None
    if isinstance(value, dict):
        try:
            (x0, y0), (x1, y1) = value["min"][:2], value["max"][:2]
        except (KeyError, TypeError, ValueError):
            raise ProceduralError('cliff bounds must be {"min": [x, y], "max": [x, y]}') from None
        corners = (x0, y0), (x1, y1)
    elif isinstance(value, (list, tuple)) and len(value) == 4 and all(
            isinstance(v, (int, float)) and not isinstance(v, bool) for v in value):
        corners = tuple(value[:2]), tuple(value[2:])
    elif isinstance(value, (str, bpy.types.Object)):
        obj = modifiers.resolve_object(value, "cliff.path", allow_none=False)
        if obj.type in ("CURVE", "CURVES") or (obj.type == "MESH" and not obj.data.polygons):
            ring = closed if closed is not None else _is_cyclic(obj)
            return obj, ring
        pts = [obj.matrix_world @ Vector(c) for c in obj.bound_box]
        corners = ((min(p.x for p in pts), min(p.y for p in pts)), (max(p.x for p in pts), max(p.y for p in pts)))
    if corners is not None:
        (x0, y0), (x1, y1) = corners
        x0, x1 = sorted((float(x0), float(x1)))
        y0, y1 = sorted((float(y0), float(y1)))
        x0, y0, x1, y1 = x0 + inset, y0 + inset, x1 - inset, y1 - inset
        if x1 - x0 < 1.0 or y1 - y0 < 1.0:
            raise ProceduralError("cliff bounds are smaller than 1 m after the inset")
        ring = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
        return path(ring, name=f"{name}_Path", closed=True, smooth=False), True
    is_ring = bool(closed)
    return _curve(value, "cliff", name, closed=is_ring), is_ring


def _is_cyclic(obj):
    return obj.type == "CURVE" and any(s.use_cyclic_u for s in obj.data.splines)


def _footprint_spots(footprint, loc, rotation):
    """World (x, y) of the footprint's corners and centre, for dropping a building onto a terrain."""
    if isinstance(footprint, (str, bpy.types.Object)):
        return [(loc.x, loc.y)]
    values = list(footprint)
    if len(values) == 2 and all(isinstance(v, (int, float)) for v in values):
        w, d = values[0] / 2, values[1] / 2
        corners = [(-w, -d), (w, -d), (w, d), (-w, d)]
    else:
        corners = [tuple(v[:2]) for v in values]
    a = math.radians(rotation if isinstance(rotation, (int, float)) else 0.0)
    ca, sa = math.cos(a), math.sin(a)
    spots = [(loc.x + x * ca - y * sa, loc.y + x * sa + y * ca) for x, y in corners]
    return spots + [(loc.x, loc.y)]


def _footprint(value, name):
    """A closed footprint curve: an existing one, or a new one from (width, depth) or corners."""
    if isinstance(value, (str, bpy.types.Object)):
        obj = modifiers.resolve_object(value, "building.footprint", allow_none=False)
        if obj.type not in ("CURVE", "MESH"):
            raise ProceduralError(f"building.footprint: {obj.name} is not a curve")
        return obj
    try:
        values = list(value)
    except TypeError:
        raise ProceduralError(f"building.footprint must be [width, depth], corners or a curve; got {value!r}") from None
    if len(values) == 2 and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in values):
        w, d = (float(v) / 2 for v in values)
        if w < 1.0 or d < 1.0:
            raise ProceduralError("building.footprint must be at least 2 x 2 m")
        corners = [(-w, -d), (w, -d), (w, d), (-w, d)]
    else:
        corners = [_vec3(v, "building.footprint corner")[:2] for v in values]
        if len(corners) < 3:
            raise ProceduralError("building.footprint needs at least three corners")
    curve = path(corners, name=f"{name}_Footprint", closed=True, smooth=False, collection=KINDS["building"][1])
    curve["gs_procedural_footprint"] = True
    return curve


# ------------------------------------------------------------------ sync: conform modifiers and colliders


def _sync(obj, kind, mod) -> None:
    meta = _meta(obj)
    if kind == "road":  # the road draws its verge itself (smooth edges; painted terrain faces stair-step)
        verge = meta.get("verge_material")
        on = verge is not None and modifiers.read_inputs(mod).get("Terrain") is not None
        modifiers.write_inputs(mod, {"Verge Width": meta.get("shoulder", 2.0) * 0.6 if on else 0.0,
                                     "Verge Material": modifiers.material(verge)})
    if kind in ("road", "river"):
        _sync_conform(obj, kind, mod, meta)
    _sync_colliders(obj, kind, mod, meta)


def _conform_name(obj):
    return f"GS Conform {obj.name}"[:63]


def _find_conform(meta):
    name = meta.get("conform")
    if not name:
        return None, None
    for other in bpy.data.objects:
        mod = other.modifiers.get(name)
        if mod is not None and mod.type == "NODES" and mod.node_group is not None \
                and mod.node_group.get(groups.GROUP_PROP) == "conform":
            return other, mod
    return None, None


def _sync_conform(obj, kind, mod, meta) -> None:
    values = modifiers.read_inputs(mod)
    terrain = values.get("Terrain")
    wanted = meta.get("grade" if kind == "road" else "carve", True) and terrain is not None
    holder, conform = _find_conform(meta)
    if conform is not None and (not wanted or holder != terrain):
        holder.modifiers.remove(conform)
        holder, conform = None, None
    if wanted and conform is None:
        conform = terrain.modifiers.new(_conform_name(obj), "NODES")
        conform.node_group = groups.ensure("conform")
        meta["conform"] = conform.name
    if not wanted:
        meta.pop("conform", None)
    if kind == "river":
        modifiers.write_inputs(mod, {"Carved": bool(wanted)})
    _write_meta(obj, meta)
    if not wanted:
        return
    if kind == "road":
        half = values["Width"] / 2 + ((values["Kerb Width"] + 0.35) if values["Kerbs"] else 0.4)
        settings = {"Curve": values["Curve"], "Half Width": half, "Falloff": meta.get("shoulder", 2.0),
                    "Depth": ROAD_GRADE_DEPTH, "Resolution": values["Resolution"], "Smooth": values["Smooth"],
                    "Only Lower": False, "Paint": False, "Paint Material": None, "Paint Width": 0.0}
    else:
        half = values["Width"] / 2
        settings = {"Curve": values["Curve"], "Half Width": half, "Falloff": values["Bank"],
                    "Depth": values["Depth"], "Resolution": values["Resolution"], "Smooth": values["Smooth"],
                    "Only Lower": True, "Paint": meta.get("bed_material") is not None,
                    "Paint Material": modifiers.material(meta.get("bed_material")),
                    "Paint Width": half + values["Bank"] * 0.3}
    modifiers.write_inputs(conform, settings)
    terrain.update_tag()


def _proxy(obj):
    for child in obj.children:
        if _meta(child).get("kind") == "collider_proxy":
            return child
    return None


def _auto_shape(kind, mod):
    """What colliders="simple" means for an instanced builder: trunks for tree kits, else the exact mesh
    (instances or not: a hull or box per copy needs GPU instancing, 4+ copies of a source)."""
    if kind == "fence":
        return "mesh"
    values = modifiers.read_inputs(mod)
    coll = values.get("Sources" if kind == "scatter" else "Source")
    objs = list(coll.all_objects) if coll is not None else []
    found = [kit_base.kit_params(o) for o in objs]
    if objs and all(f is not None and f[0] == "tree" for f in found):
        return "trunk"
    return "mesh"


def _trunk_size(mod, kind):
    values = modifiers.read_inputs(mod)
    coll = values.get("Sources" if kind == "scatter" else "Source")
    radius, height = 0.3, 3.0
    found = [kit_base.kit_params(o) for o in (coll.all_objects if coll else [])]
    trees = [p for k, p in (f for f in found if f) if k == "tree"]
    if trees:
        radius = max(min(p.get("trunk_radius", 0.2), 0.4 * p.get("canopy_radius", 1.0)) for p in trees) * 1.25
        height = max(1.5, min(p.get("height", 4.0) for p in trees) * 0.55)
    return radius, height


def _own_collider(obj, meta, values) -> None:
    """Put a Collider with `values` on obj, or take away (values None) the one this builder put there; a
    Collider someone else added stays as it is."""
    added = meta.get("collider_added") and components.has_component(obj, "Collider")
    if values is None:
        if added:
            components.remove_component(obj, "Collider")
        meta.pop("collider_added", None)
    elif added:
        components.set_values(obj, "Collider", values)
    elif not components.has_component(obj, "Collider"):
        components.add_component(obj, "Collider", values)
        meta["collider_added"] = True


def _sync_colliders(obj, kind, mod, meta) -> None:
    mode = meta.get("colliders", "none")
    proxy = _proxy(obj)
    if kind in REALIZED:
        if proxy is not None:
            _delete_object(proxy)
        values = None if mode == "none" else {"shape": "mesh", **({"sensor": True} if kind == "river" else {})}
        _own_collider(obj, meta, values)
        _write_meta(obj, meta)
        return
    shape = _auto_shape(kind, mod) if mode == "simple" else mode
    _own_collider(obj, meta, {"shape": INSTANCE_SHAPES[shape]} if shape in INSTANCE_SHAPES else None)
    _write_meta(obj, meta)
    if shape != "trunk":
        if proxy is not None:
            _delete_object(proxy)
        return
    if proxy is None:
        proxy = _new_object(f"{obj.name}{COLLIDER_SUFFIX}", obj.users_collection[0])
        proxy.parent = obj
        proxy.display_type = "WIRE"  # with its Collider: exported, but the game does not draw it
        proxy.visible_camera = proxy.visible_shadow = False  # nor do Blender renders (render_views, F12)
        pmod = proxy.modifiers.new("GS Collider Proxy", "NODES")
        pmod.node_group = groups.ensure("proxy")
        _write_meta(proxy, {"kind": "collider_proxy", "source": obj.name})
        components.add_component(proxy, "Collider", {"shape": "mesh"})
    pmod = _modifier(proxy, "proxy")
    radius, height = _trunk_size(mod, kind)
    material = modifiers.material("collider")  # alpha 0: hidden in EEVEE renders too; export drops it
    modifiers.write_inputs(pmod, {"Source": obj, "Shape": shape.title(), "Radius": radius, "Height": height,
                                  "Material": material})


def _delete_object(obj) -> None:
    data = obj.data
    bpy.data.objects.remove(obj)
    if isinstance(data, bpy.types.Mesh) and data.users == 0:
        bpy.data.meshes.remove(data)


# ------------------------------------------------------------------ params, set_params, remove, realize


def params(obj) -> dict:
    """Every input of a builder object by key, plus its kind and extras (colliders, style, ...).
    References are names; menu items are lower case."""
    obj, kind, mod = _target(obj)
    out = {"kind": kind}
    raw = modifiers.read_inputs(mod)
    for spec in groups.specs(kind):
        if spec.name in raw:
            out[spec.key] = modifiers.plain(spec, raw[spec.name])
    meta = _meta(obj)
    for key in EXTRAS[kind]:
        out[key] = meta.get(key, EXTRAS[kind][key])
    proxy = _proxy(obj)
    if proxy is not None:
        out["collider_object"] = proxy.name
    if meta.get("conform"):
        holder, conform = _find_conform(meta)
        if conform is not None:
            out["terrain_modifier"] = [holder.name, conform.name]
    return out


def set_params(obj, **values) -> dict:
    """Change a builder object's inputs (any key params() lists, plus aliases such as scale_range
    and lists of objects for sources/keep_out). Unknown keys and bad values raise ProceduralError
    and change nothing. Returns params(obj)."""
    obj, kind, mod = _target(obj)
    groups.ensure(kind)  # a group from an older file moves to this version first (its inputs kept)
    meta = _meta(obj)
    inputs, extras = {}, {}
    for key, value in values.items():
        if key == "scale_range" and kind in ("scatter", "array"):
            inputs["scale_min"], inputs["scale_max"] = _pair(value, f"{kind}.scale_range")
        elif key in EXTRAS[kind]:
            extras[key] = value
        elif kind == "fence" and key == "post_source":
            inputs["post"] = value
        elif _spec(kind, key) is not None:
            inputs[key] = value
        else:
            valid = sorted({s.key for s in groups.specs(kind)} | set(EXTRAS[kind]))
            raise ProceduralError(f"{kind}: unknown param {key!r}. Valid: {', '.join(valid)}")
    if "colliders" in extras:
        extras["colliders"] = _check_colliders(extras["colliders"], True, kind)
    if kind == "road":
        for key, check in (("grade", lambda v: _bool(v, "road.grade")),
                           ("shoulder", lambda v: _number(v, 0.0, 100.0, "road.shoulder")),
                           ("verge_material", _material_name)):
            if key in extras:
                extras[key] = check(extras[key])
    if kind == "river":
        if "carve" in extras:
            extras["carve"] = _bool(extras["carve"], "river.carve")
        if "bed_material" in extras:
            extras["bed_material"] = _material_name(extras["bed_material"])
    if kind == "building" and "style" in extras:
        style = extras["style"]
        if style not in parts.STYLES:
            raise ProceduralError(f"building.style must be one of {', '.join(parts.STYLES)}; got {style!r}")
        for key, part_obj in parts.building_parts(style).items():
            inputs.setdefault(f"{key}_part", part_obj)
        inputs.setdefault("roof_material", parts.STYLES[style]["roof"])
        inputs.setdefault("gable_material", parts.STYLES[style]["gable"])
        inputs.setdefault("base_material", parts.STYLES[style]["base"])
        inputs.setdefault("roof", parts.STYLES[style]["roof_shape"])

    resolved = {}
    for key, value in inputs.items():
        spec = _spec(kind, key)
        if spec.kind == "COLLECTION" and not (value is None or isinstance(value, (str, bpy.types.Collection))):
            label = "Keep Out" if key == "keep_out" else ("Sources" if key == "sources" else "Source")
            value = (_keep_out_collection(value, obj.name) if key == "keep_out"
                     else _objects_collection(value, kind, obj.name, label))
        elif spec.kind == "OBJECT" and key in ("curve", "path") and isinstance(value, (list, tuple)):
            value = _curve(value, kind, obj.name)
        elif spec.kind == "OBJECT" and isinstance(value, str) and value in parts.GENERIC:
            value = parts.part(value)
        resolved[spec.name] = modifiers.coerce(spec, value, kind)
    modifiers.write_inputs(mod, resolved)
    meta.update(extras)
    _write_meta(obj, meta)
    _sync(obj, kind, mod)
    obj.update_tag()
    return params(obj)


def remove(obj) -> None:
    """Delete a builder object with its collider proxy and the terrain modifier it added."""
    obj = modifiers.resolve_object(obj, "object", allow_none=False)
    meta = _meta(obj)
    holder, conform = _find_conform(meta)
    if conform is not None:
        holder.modifiers.remove(conform)
    proxy = _proxy(obj)
    if proxy is not None:
        _delete_object(proxy)
    for child in list(obj.children):
        if child.get("gs_procedural_footprint"):
            _delete_object(child)
    _delete_object(obj)


def realize(obj, mode="mesh"):
    """Turn a builder object into plain data for one-off edits; the modifier goes away.

    mode "mesh": everything (instances too) becomes the object's own mesh. Its collider proxy, if any,
    is realized the same way, and a hull or box Collider the builder put on it becomes a mesh one.
    mode "objects": every instance becomes an object sharing its source's mesh (linked duplicates) and
    components, named <name>_001..., each with that Collider; the rest stays the object's mesh.
    Objects are entities (pickups work), so the exporter no longer draws them as GPU instances.
    Returns the objects made or kept.
    """
    obj, kind, mod = _target(obj)
    if mode not in ("mesh", "objects"):
        raise ProceduralError(f"realize mode must be 'mesh' or 'objects', got {mode!r}")
    meta = _meta(obj)
    proxy = _proxy(obj)
    own = kind not in REALIZED and meta.get("collider_added") and components.has_component(obj, "Collider")
    shape = components.get_values(obj, "Collider").get("shape") if own else None
    made = []
    if mode == "objects":
        made = _instances_to_objects(obj)
    _bake_mesh(obj, realize_instances=(mode == "mesh"))
    obj.modifiers.remove(mod)
    if shape is not None:  # the engine fitted it to each GPU instance: there are none now
        for other in made:
            if not components.has_component(other, "Collider"):
                components.add_component(other, "Collider", {"shape": shape})
        _own_collider(obj, meta, {"shape": "mesh"} if len(obj.data.vertices) else None)
    if proxy is not None:
        _bake_mesh(proxy, realize_instances=True)
        for pmod in [m for m in proxy.modifiers if m.type == "NODES"]:
            proxy.modifiers.remove(pmod)
        _write_meta(proxy, {"kind": "collider_realized", "source": obj.name})
    meta["realized"] = mode
    meta["kind"] = f"realized_{kind}"
    _write_meta(obj, meta)
    return [obj, *made]


def _bake_mesh(obj, realize_instances: bool) -> None:
    helper = None
    if realize_instances:
        helper = obj.modifiers.new("GS Realize", "NODES")
        helper.node_group = _realize_group()
    try:
        depsgraph = bpy.context.evaluated_depsgraph_get()
        obj.update_tag()
        depsgraph.update()
        evaluated = obj.evaluated_get(depsgraph)
        mesh = bpy.data.meshes.new_from_object(evaluated, preserve_all_data_layers=True, depsgraph=depsgraph)
    finally:
        if helper is not None:
            obj.modifiers.remove(helper)
    old = obj.data
    mesh.name = obj.name
    obj.data = mesh
    if old.users == 0:
        bpy.data.meshes.remove(old)


def _realize_group():
    tree = bpy.data.node_groups.get("GS Realize")
    if tree is None or tree.get(groups.GROUP_PROP) != "realize":
        from .graph import Graph

        g = Graph.new("GS Realize", [], geometry_input=True)
        g.output(g.realize(g.input("Geometry")))
        tree = g.finish()
        tree[groups.GROUP_PROP] = "realize"
    return tree


def _instances_to_objects(obj) -> list:
    depsgraph = bpy.context.evaluated_depsgraph_get()
    made, meshes = [], {}
    coll = obj.users_collection[0]
    for inst in depsgraph.object_instances:
        if not inst.is_instance or inst.parent is None or inst.parent.original != obj:
            continue
        source = inst.object.original
        if source is not None and source != obj and source.type == "MESH":
            data = source.data
        else:
            key = inst.object.data.name if inst.object.data else None
            if key not in meshes:
                meshes[key] = bpy.data.meshes.new_from_object(inst.object, depsgraph=depsgraph)
            data = meshes[key]
        copy = bpy.data.objects.new(f"{obj.name}_{len(made) + 1:03d}", data)
        coll.objects.link(copy)
        copy.matrix_world = inst.matrix_world.copy()
        if source is not None and source != obj:
            for component in components.list_components(source):  # coins stay collectible, etc.
                components.add_component(copy, component, components.get_values(source, component))
        made.append(copy)
    return made


# ------------------------------------------------------------------ stats


def stats(obj) -> dict:
    """What a builder object costs: instances (per source), triangles drawn (instances included),
    the triangles of its own mesh, and the draw calls the game will make for it (GPU instancing
    draws each source's material once per group of 4 or more)."""
    obj = modifiers.resolve_object(obj, "object", allow_none=False)
    depsgraph = bpy.context.evaluated_depsgraph_get()
    evaluated = obj.evaluated_get(depsgraph)
    by_source, tris_of, mats_of = {}, {}, {}
    for inst in depsgraph.object_instances:
        if not inst.is_instance or inst.parent is None or inst.parent.original != obj:
            continue
        data = inst.object.data
        key = inst.object.original.name if inst.object.original not in (None, obj) else (data.name if data else "?")
        by_source[key] = by_source.get(key, 0) + 1
        if key not in tris_of and isinstance(data, bpy.types.Mesh):
            data.calc_loop_triangles()
            tris_of[key] = len(data.loop_triangles)
            mats_of[key] = max(1, len({p.material_index for p in data.polygons}))
    own = 0
    own_materials = 0
    mesh = evaluated.to_mesh() if evaluated.type == "MESH" else None
    if mesh is not None:
        mesh.calc_loop_triangles()
        own = len(mesh.loop_triangles)
        own_materials = len({p.material_index for p in mesh.polygons}) if mesh.polygons else 0
        evaluated.to_mesh_clear()
    draw_calls = own_materials
    for key, count in by_source.items():
        draw_calls += mats_of.get(key, 1) * (1 if count >= 4 else count)
    triangles = own + sum(tris_of.get(k, 0) * n for k, n in by_source.items())
    return {"object": obj.name, "kind": kind_of(obj), "instances": sum(by_source.values()),
            "by_source": dict(sorted(by_source.items())), "triangles": triangles, "own_triangles": own,
            "draw_calls": draw_calls}
