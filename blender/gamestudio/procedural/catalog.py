# SPDX-License-Identifier: GPL-3.0-or-later
"""catalog(): a JSON-Schema description of every builder, like kits.catalog()."""

import json

from . import groups, parts

NAME = {"type": "string", "description": "Object name (Blender adds .001 if it is taken)"}
COLLECTION = {"type": "string", "description": "Collection for the object (default: one per builder)"}
OBJECT = {"type": "string", "description": "Object name"}
POINTS = {"type": "array", "items": {"type": "array", "items": {"type": "number"}, "minItems": 2, "maxItems": 3}}
CURVE = {"description": "Curve object name, or [[x, y], ...] points (a smooth path is made)",
         "anyOf": [OBJECT, POINTS]}
OBJECTS = {"description": "Object names (moved into Procedural Sources so they show only as instances), "
                          "or a collection name",
           "anyOf": [{"type": "string"}, {"type": "array", "items": {"type": "string"}}]}


def _colliders(default, instanced):
    text = ("none; simple = a mesh Collider on the object itself" if not instanced else
            "none; simple = trunk for tree kits, else mesh. hull, box and mesh put a static Collider on the "
            "object that the game fits to every instance (a convex hull or a box each, or one exact mesh; "
            "hull and box need GPU instancing, 4+ copies of a source); trunk adds an invisible proxy child of "
            "trunk cylinders")
    return {"type": "string", "enum": ["none", "simple", "hull", "box", "trunk", "mesh"], "default": default,
            "description": f"Colliders: {text}"}


def _schema(kind, skip=(), extra=None, required=()):
    props = {}
    for spec in groups.specs(kind):
        if spec.key in skip:
            continue
        props[spec.key] = spec.schema()
    props.update(extra or {})
    return {"type": "object", "properties": props, "required": list(required), "additionalProperties": False}


ENTRIES = [
    ("scatter", "Scatter", "scatter",
     "Instances of kit objects (or any objects) over a surface: Poisson-disc spacing, no steep slopes, random "
     "scale and turn, a vertex-group mask and keep-out objects (paths, roads, the player start). Instances stay "
     "instances: each source exports as one GPU-instanced mesh (one draw call per material). Budget: "
     "max_instances (2000); triangles = instances x source triangles (a kit tree is at most 500).",
     dict(skip=("surface", "sources", "keep_out", "scale_min", "scale_max"),
          extra={"surface": OBJECT, "sources": OBJECTS,
                 "scale_range": {"type": "array", "items": {"type": "number"}, "minItems": 2, "maxItems": 2,
                                 "default": [0.8, 1.2], "description": "[min, max] random scale"},
                 "keep_out": {"type": "array", "items": {"type": "string"},
                              "description": "Objects to keep clear of (or a collection name)"},
                 "mask": {"type": "string", "description": "A vertex group on the surface (weight >= 0.5: no "
                                                           "instances), or keep-out object names"},
                 "colliders": _colliders("none", True), "name": NAME, "collection": COLLECTION},
          required=("surface", "sources"))),
    ("array", "Array along a path", "array_along",
     "Copies of objects every `spacing` metres along a curve, on it or `offset` to one or both sides, "
     "dropped onto a terrain: lamp posts, trees along an avenue, coins on a path, bridge planks. GPU-instanced.",
     dict(skip=("curve", "source", "scale_min", "scale_max"),
          extra={"curve": CURVE, "source": OBJECTS, "colliders": _colliders("none", True),
                 "scale_range": {"type": "array", "items": {"type": "number"}, "default": [1.0, 1.0]},
                 "name": NAME, "collection": COLLECTION},
          required=("curve", "source"))),
    ("fence", "Fence along a path", "fence_along",
     "Posts every `spacing` metres along a curve (instanced; a generated post unless post_source is given) "
     "with swept rails, rails and pickets, or posts only; offset sideways to line a road, dropped on a terrain.",
     dict(skip=("curve", "post"),
          extra={"curve": CURVE, "post_source": {"type": "string", "description": "Post object name (scaled to "
                                                                                  "height), or 'fence_post'"},
                 "colliders": _colliders("none", True), "name": NAME, "collection": COLLECTION},
          required=("curve",))),
    ("road", "Road along a path", "road_along",
     "A road mesh along a curve: level across, smoothed along, with kerbs, painted edge lines and centre dashes "
     "and a UV map for a tiling texture (u across, v along, `tile` metres per repeat). With a terrain it is "
     "draped on it, grades a flat bed (a modifier on the terrain) and lays a verge strip beside the kerbs. Open "
     "ends sink into the ground over `taper` metres (no step for walkers). About 2 x 16 triangles per metre at "
     "resolution 1.",
     dict(skip=("curve", "verge_width"),
          extra={"curve": CURVE, "grade": {"type": "boolean", "default": True,
                                           "description": "Flatten the terrain under the road"},
                 "shoulder": {"type": "number", "default": 2.0, "minimum": 0, "maximum": 100,
                              "description": "Width of the graded verge beside the road, metres"},
                 "verge_material": {"type": ["string", "null"], "default": "dirt",
                                    "description": "Strip beside the kerbs (on a terrain), or null for none"},
                 "colliders": _colliders("simple", False), "name": NAME, "collection": COLLECTION},
          required=("curve",))),
    ("river", "River along a path", "river_along",
     "A river along a curve: a see-through water surface with UVs, sitting in a channel `depth` deep that it "
     "carves into the terrain (a modifier on the terrain, with a sandy bed). Cross it with a bridge: a platform "
     "kit deck and fence_along rails.",
     dict(skip=("curve", "carved"),
          extra={"curve": CURVE, "carve": {"type": "boolean", "default": True},
                 "bed_material": {"type": ["string", "null"], "default": "sand"},
                 "colliders": _colliders("none", False), "name": NAME, "collection": COLLECTION},
          required=("curve",))),
    ("cliff", "Cliff wall", "cliff",
     "A displaced, layered rock wall along a path, or a ring around bounds ([xmin, ymin, xmax, ymax]) or a "
     "terrain's rim (inset metres in): level boundaries. Faceted, strata in two materials, grass on top. About "
     "(length / detail) x (2 x height / detail + 4) x 2 triangles.",
     dict(skip=("path",),
          extra={"path_or_bounds": {"description": "Curve name, [[x, y], ...] points, bounds {\"min\": [x, y], "
                                                   "\"max\": [x, y]} or [xmin, ymin, xmax, ymax], or a mesh object name "
                                                   "(its bounds)"},
                 "inset": {"type": "number", "default": 0.0}, "closed": {"type": ["boolean", "null"]},
                 "colliders": _colliders("simple", False), "name": NAME, "collection": COLLECTION},
          required=("path_or_bounds",))),
    ("rock_wall", "Rock wall", "rock_wall",
     "cliff() with low-wall proportions (2.5 m high, 1.2 m thick, small facets): field walls, terraces.",
     dict(skip=("path",),
          extra={"path_or_bounds": {"description": "As for cliff"},
                 "colliders": _colliders("simple", False), "name": NAME, "collection": COLLECTION},
          required=("path_or_bounds",))),
    ("building", "Modular building", "modular_building",
     "A building assembled from wall, window, door and corner modules over a footprint, with floor slabs and a "
     "gable, hip or flat roof; styles " + ", ".join(parts.STYLES) + ". Realized into one mesh by default "
     "(about 150 triangles per module, 5-7 draw calls); door in the middle of edge `door_side`.",
     dict(skip=("footprint", "wall_part", "window_part", "door_part", "corner_part", "roof_material",
                "gable_material"),
          extra={"footprint": {"description": "[width, depth] metres, [[x, y], ...] corners counter-clockwise, or "
                                              "a closed curve name", "default": [8, 6]},
                 "style": {"type": "string", "enum": list(parts.STYLES), "default": "cottage"},
                 "location": {"type": "array", "items": {"type": "number"}, "default": [0, 0, 0]},
                 "rotation": {"type": "number", "default": 0, "description": "Turn about Z, degrees"},
                 "terrain": {"type": "string", "description": "Drop the location onto this terrain"},
                 "colliders": _colliders("simple", False), "name": NAME, "collection": COLLECTION})),
]


def catalog() -> list[dict]:
    out = []
    for kind, label, function, description, schema in ENTRIES:
        group = "cliff" if kind == "rock_wall" else kind
        out.append({"kind": kind, "function": function, "label": label, "description": description,
                    "params": _schema(group, **schema)})
    return json.loads(json.dumps(out))
