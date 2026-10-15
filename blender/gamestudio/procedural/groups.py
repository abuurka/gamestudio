# SPDX-License-Identifier: GPL-3.0-or-later
"""The geometry-node groups behind the builders, written in code (no shipped .blend).

Each group has a kind, a version and a list of inputs (graph.Input) that the modifier exposes. ensure(kind)
returns the group, building it once per file; a group from an older version of this code is rebuilt
and every modifier that used it moves to the new one with its values kept.

Shared conventions: object inputs are read with Object Info in Relative space, so a builder object
may sit anywhere; curves are resampled to poly curves; `drape` drops points onto a terrain with a
ray from above (points that miss keep their height); `sweep` turns a curve plus a cross-section into
a mesh whose vertices are placed explicitly at point + side * s + up * h, so ribbons stay level
across and the UVs are exact (u across in metres / tile, v along).
"""

import math

import bpy

from .graph import Graph, Input, out, socket

GROUP_PROP = "gs_procedural"    # node_group[GROUP_PROP] = kind
VERSION_PROP = "gs_version"
BIG = 10000.0
ROAD_END_SINK = 0.05            # a tapered road end (and a verge's outer edge) finishes this far below the ground
VERGE_LIFT = 0.03               # a road verge lies this far above the ground, metres

# Building parts are made at this size and scaled to each module (parts.py builds them).
PART_WIDTH, PART_HEIGHT = 2.5, 3.0


def _f(key, name, default, lo, hi, text, subtype=""):
    return Input(key, name, "FLOAT", float(default), lo, hi, text, subtype=subtype)


def _i(key, name, default, lo, hi, text):
    return Input(key, name, "INT", int(default), lo, hi, text)


def _b(key, name, default, text):
    return Input(key, name, "BOOL", bool(default), None, None, text)


def _o(key, name, text):
    return Input(key, name, "OBJECT", None, None, None, text)


def _c(key, name, text):
    return Input(key, name, "COLLECTION", None, None, None, text)


def _m(key, name, default, text):
    return Input(key, name, "MATERIAL", default, None, None, text)


def _menu(key, name, options, default, text):
    return Input(key, name, "MENU", default, None, None, text, tuple(options))


SEED = _i("seed", "Seed", 0, 0, 2**31 - 1, "Variation seed: the same seed and inputs always give the same result")
TERRAIN = _o("terrain", "Terrain", "Ground to follow: points are dropped onto it from above (none: the curve's own "
                                   "height)")


# ------------------------------------------------------------------ input lists

SCATTER = [
    _o("surface", "Surface", "Mesh to scatter on (usually the terrain)"),
    _c("sources", "Sources", "Objects to instance; each point picks one at random"),
    _f("density", "Density", 0.05, 0.0, 50.0, "Most instances per square metre (Poisson disc: min_distance wins)"),
    _f("min_distance", "Min Distance", 2.0, 0.0, 100.0, "Smallest distance between two instances, metres",
       "DISTANCE"),
    SEED,
    _f("scale_min", "Scale Min", 0.8, 0.01, 100.0, "Smallest random scale"),
    _f("scale_max", "Scale Max", 1.2, 0.01, 100.0, "Largest random scale"),
    _f("rotation_random", "Rotation Random", 1.0, 0.0, 1.0, "Random turn about Z: 0 none, 1 any angle", "FACTOR"),
    _f("align_to_normal", "Align To Normal", 0.0, 0.0, 1.0, "Tilt toward the surface normal: 0 upright, 1 fully",
       "FACTOR"),
    _f("slope_limit", "Slope Limit", 35.0, 0.0, 90.0, "No instances on faces steeper than this, degrees"),
    Input("mask", "Mask", "STRING", "", None, None,
          "Vertex group (or float attribute) on the surface: weight >= 0.5 keeps instances out"),
    _b("invert_mask", "Invert Mask", False, "Scatter only where the mask is >= 0.5"),
    _c("keep_out", "Keep Out", "Objects to stay away from (paths, roads, buildings, the player start)"),
    _f("keep_out_distance", "Keep Out Distance", 1.5, 0.0, 100.0,
       "Clearance around the keep-out objects' surfaces, edges and origins, metres", "DISTANCE"),
    _f("sink", "Sink", 0.1, -10.0, 10.0, "Push instances into the ground by this much (times their scale), metres",
       "DISTANCE"),
    _i("max_instances", "Max Instances", 2000, 0, 100000, "Budget: at most this many instances (0 = no limit)"),
]

ARRAY = [
    _o("curve", "Curve", "Path to place along"),
    _c("source", "Source", "Objects to place; each spot picks one at random"),
    _f("spacing", "Spacing", 6.0, 0.05, 1000.0, "Distance between items along the path, metres", "DISTANCE"),
    _f("start", "Start", 0.0, 0.0, 10000.0, "Distance along the path before the first item, metres", "DISTANCE"),
    _f("offset", "Offset", 0.0, -1000.0, 1000.0, "Sideways distance from the path, metres (+ = right)",
       "DISTANCE"),
    _menu("sides", "Sides", ("Center", "Right", "Left", "Both"), "Center",
          "Where items go: on the path, its right or left (Offset away), or both sides"),
    _f("jitter", "Jitter", 0.0, 0.0, 100.0, "Random shift in X and Y, metres", "DISTANCE"),
    _f("rotation_jitter", "Rotation Jitter", 0.0, 0.0, 180.0, "Random turn about Z, degrees"),
    _f("scale_min", "Scale Min", 1.0, 0.01, 100.0, "Smallest random scale"),
    _f("scale_max", "Scale Max", 1.0, 0.01, 100.0, "Largest random scale"),
    _b("align", "Align", True, "Turn items to follow the path (offset items face it)"),
    SEED,
    TERRAIN,
    _f("height_offset", "Height Offset", 0.0, -100.0, 100.0, "Raise items above the ground or curve, metres",
       "DISTANCE"),
    _i("max_count", "Max Count", 1000, 0, 100000, "Budget: at most this many items (0 = no limit)"),
]

FENCE = [
    _o("curve", "Curve", "Path the fence follows"),
    _o("post", "Post", "Post object instanced at every Spacing (scaled to Height)"),
    _f("spacing", "Spacing", 2.0, 0.2, 50.0, "Distance between posts, metres", "DISTANCE"),
    _f("height", "Height", 1.0, 0.2, 10.0, "Fence height, metres", "DISTANCE"),
    _menu("rail_style", "Rail Style", ("Rails", "Pickets", "None"), "Rails",
          "Rails: horizontal bars; Pickets: bars and upright boards; None: posts only"),
    _i("rails", "Rails", 2, 1, 6, "Horizontal bars"),
    _f("rail_size", "Rail Size", 0.07, 0.01, 1.0, "Bar thickness, metres", "DISTANCE"),
    _f("picket_spacing", "Picket Spacing", 0.16, 0.05, 2.0, "Distance between pickets, metres", "DISTANCE"),
    _f("offset", "Offset", 0.0, -1000.0, 1000.0, "Sideways distance from the curve, metres (+ = right)",
       "DISTANCE"),
    TERRAIN,
    _f("height_offset", "Height Offset", 0.0, -100.0, 100.0, "Raise the fence above the ground or curve, metres",
       "DISTANCE"),
    _m("rail_material", "Rail Material", "wood", "Bars"),
    _m("picket_material", "Picket Material", "wood", "Pickets"),
]

ROAD = [
    _o("curve", "Curve", "Centre line of the road"),
    _f("width", "Width", 6.0, 0.5, 100.0, "Width between the kerbs, metres", "DISTANCE"),
    _b("kerbs", "Kerbs", True, "Raised kerbs along both edges"),
    _f("kerb_width", "Kerb Width", 0.3, 0.05, 2.0, "Kerb width, metres", "DISTANCE"),
    _f("kerb_height", "Kerb Height", 0.15, 0.0, 1.0, "Kerb height above the road, metres", "DISTANCE"),
    _b("lane_lines", "Lane Lines", True, "Painted edge lines and a dashed centre line"),
    _f("dash_length", "Dash Length", 2.0, 0.1, 50.0, "Centre dash length, metres", "DISTANCE"),
    _f("dash_gap", "Dash Gap", 2.5, 0.1, 50.0, "Gap between centre dashes, metres", "DISTANCE"),
    _f("line_width", "Line Width", 0.15, 0.02, 2.0, "Painted line width, metres", "DISTANCE"),
    _f("resolution", "Resolution", 1.0, 0.1, 20.0, "Length of the road's segments, metres", "DISTANCE"),
    TERRAIN,
    _f("height_offset", "Height Offset", 0.08, -10.0, 10.0, "Road surface above the ground (or curve), metres",
       "DISTANCE"),
    _i("smooth", "Smooth", 4, 0, 100, "Smoothing passes over the heights along the road"),
    _f("taper", "End Taper", 2.0, 0.0, 50.0, "Length over which each open end (and its kerbs) sinks into the "
       "ground, so there is no step to walk off; 0 = square ends, metres", "DISTANCE"),
    _f("tile", "Tile", 4.0, 0.1, 1000.0, "Metres per texture repeat (UV map 'UVMap': u across, v along)",
       "DISTANCE"),
    _m("road_material", "Road Material", "asphalt", "Road surface"),
    _m("kerb_material", "Kerb Material", "concrete", "Kerbs (or the bevelled edges without kerbs)"),
    _m("line_material", "Line Material", "road_line", "Painted lines"),
    _f("verge_width", "Verge Width", 0.0, 0.0, 100.0, "Strip of Verge Material on the ground beside each kerb "
       "(set from shoulder; 0 = none), metres", "DISTANCE"),
    _m("verge_material", "Verge Material", None, "The verge strip"),
]

RIVER = [
    _o("curve", "Curve", "Centre line of the river"),
    _f("width", "Width", 6.0, 0.5, 200.0, "Width of the river bed, metres", "DISTANCE"),
    _f("depth", "Depth", 1.5, 0.05, 50.0, "Depth of the bed below the banks, metres", "DISTANCE"),
    _f("bank", "Bank", 3.0, 0.0, 100.0, "Width of the sloping banks, metres", "DISTANCE"),
    _f("water_level", "Water Level", 0.7, 0.0, 1.0, "Water surface height as a share of the depth", "FACTOR"),
    _f("resolution", "Resolution", 1.5, 0.1, 20.0, "Length of the water's segments, metres", "DISTANCE"),
    _i("smooth", "Smooth", 6, 0, 100, "Smoothing passes over the heights along the river"),
    TERRAIN,
    _b("carved", "Carved", False, "The terrain has this river's carve modifier (the water sits in the bed)"),
    _f("tile", "Tile", 6.0, 0.1, 1000.0, "Metres per texture repeat (UV map 'UVMap': u across, v along)",
       "DISTANCE"),
    _m("water_material", "Water Material", "water", "Water surface"),
]

CONFORM = [
    _o("curve", "Curve", "Path to shape the ground along"),
    _f("half_width", "Half Width", 3.0, 0.0, 500.0, "Flat half-width at the target height, metres", "DISTANCE"),
    _f("falloff", "Falloff", 2.0, 0.0, 500.0, "Width over which the ground blends back, metres", "DISTANCE"),
    _f("depth", "Depth", 0.05, -100.0, 100.0, "Target height below the path's (smoothed) ground height, metres",
       "DISTANCE"),
    _f("resolution", "Resolution", 1.0, 0.1, 20.0, "Sample spacing along the path, metres", "DISTANCE"),
    _i("smooth", "Smooth", 4, 0, 100, "Smoothing passes over the heights along the path"),
    _b("only_lower", "Only Lower", False, "Only dig (rivers); otherwise also fill (roads)"),
    _b("paint", "Paint", False, "Give the faces near the path Paint Material"),
    _m("paint_material", "Paint Material", None, "Bed or verge material"),
    _f("paint_width", "Paint Width", 3.0, 0.0, 500.0, "Faces whose centre is closer than this are painted, metres",
       "DISTANCE"),
]

CLIFF = [
    _o("path", "Path", "Line the wall follows (closed for a ring around a level)"),
    _f("height", "Height", 10.0, 0.5, 200.0, "Height above the ground, metres", "DISTANCE"),
    _f("thickness", "Thickness", 4.0, 0.2, 100.0, "Thickness at the base, metres", "DISTANCE"),
    _f("roughness", "Roughness", 0.5, 0.0, 1.0, "How lumpy and broken the faces are", "FACTOR"),
    SEED,
    _f("detail", "Detail", 2.0, 0.25, 20.0, "Size of the facets, metres (smaller = more triangles)", "DISTANCE"),
    _f("layer_height", "Layer Height", 1.6, 0.1, 50.0, "Height of the rock strata, metres", "DISTANCE"),
    _f("batter", "Batter", 0.3, 0.0, 0.95, "How much thinner the top is than the base", "FACTOR"),
    _f("sink", "Sink", 1.0, 0.0, 50.0, "Depth below the ground, metres", "DISTANCE"),
    _f("corner_radius", "Corner Radius", 0.0, 0.0, 500.0, "Round the path's sharp corners (poly paths), metres",
       "DISTANCE"),
    TERRAIN,
    _m("top_material", "Top Material", "grass", "Faces looking up"),
    _m("rock_material", "Rock Material", "stone", "Rock faces"),
    _m("layer_material", "Layer Material", "stone_dark", "Every other stratum"),
]

BUILDING = [
    _o("footprint", "Footprint", "Closed curve: the outer line of the walls"),
    _i("floors", "Floors", 2, 1, 12, "Storeys"),
    _f("floor_height", "Floor Height", 3.0, 2.2, 8.0, "Height of each storey, metres", "DISTANCE"),
    _f("module_width", "Module Width", 2.5, 1.0, 10.0, "Target width of a wall module, metres (fitted per wall)",
       "DISTANCE"),
    _o("wall_part", "Wall Part", "Plain wall module"),
    _o("window_part", "Window Part", "Wall module with a window"),
    _o("door_part", "Door Part", "Wall module with a door"),
    _o("corner_part", "Corner Part", "Corner trim at every footprint vertex"),
    _f("window_ratio", "Window Ratio", 0.85, 0.0, 1.0, "Share of the modules that get a window", "FACTOR"),
    SEED,
    _i("door_side", "Door Side", 0, 0, 1000, "Wall (footprint edge index) with the door, in the middle module"),
    _menu("roof", "Roof", ("Gable", "Hip", "Flat"), "Gable", "Roof shape; the ridge runs along the longer side"),
    _f("roof_pitch", "Roof Pitch", 32.0, 5.0, 60.0, "Roof slope, degrees"),
    _f("overhang", "Overhang", 0.4, 0.0, 3.0, "How far the roof reaches past the walls, metres", "DISTANCE"),
    _m("roof_material", "Roof Material", "roof", "Roof planes"),
    _m("gable_material", "Gable Material", "plaster", "Gable ends and roof edges"),
    _m("floor_material", "Floor Material", "wood", "Floor slabs"),
    _f("foundation", "Foundation", 0.8, 0.0, 20.0, "Depth of the plinth below the ground floor, metres (sloped "
                                                   "ground)", "DISTANCE"),
    _m("base_material", "Base Material", "stone_dark", "Foundation plinth"),
    _b("realize", "Realize", True, "One mesh (fewest draw calls); off keeps the modules as instances"),
]

PROXY = [
    _o("source", "Source", "Builder object whose output this approximates"),
    _menu("shape", "Shape", ("Hull", "Trunk", "Mesh"), "Hull",
          "Hull: a convex hull per instance; Trunk: an upright cylinder per instance; Mesh: everything as is"),
    _f("radius", "Radius", 0.3, 0.01, 50.0, "Trunk radius (times the instance scale), metres", "DISTANCE"),
    _f("height", "Height", 3.0, 0.01, 200.0, "Trunk height (times the instance scale), metres", "DISTANCE"),
    _m("material", "Material", "collider", "Material of the proxy (invisible)"),
]


# ------------------------------------------------------------------ shared pieces


def curve_in(g, name, resolution=None):
    """The object input's curves (and mesh edges as curves), as poly curves resampled every
    `resolution` metres when given."""
    geo = g.object_geometry(g.input(name))
    from_mesh = out(g.node("GeometryNodeMeshToCurve", {"Mesh": g.part(geo, "Mesh")}))
    curve = g.join(g.part(geo, "Curve"), from_mesh)
    if resolution is not None:
        curve = out(g.node("GeometryNodeResampleCurve", {"Curve": curve, "Mode": "Length", "Length": resolution}))
    return curve


def drape(g, geometry, terrain, offset=0.0):
    """Points dropped onto `terrain` from above, plus `offset`. Points beside it (or on its very edge)
    take the height of its nearest point; with no terrain they keep their own height."""
    x, y, z = g.sep(g.position())
    ray = g.node("GeometryNodeRaycast", {"Target Geometry": terrain, "Source Position": g.xyz(x, y, BIG),
                                          "Ray Direction": (0.0, 0.0, -1.0), "Ray Length": 2 * BIG})
    hit_z = g.sep(out(ray, "Hit Position"))[2]
    near = g.node("GeometryNodeProximity", {"Geometry": terrain, "Sample Position": g.xyz(x, y, z)},
                  target_element="FACES")
    near_z = g.switch("FLOAT", out(near, "Is Valid"), z, g.sep(out(near, "Position"))[2])
    ground = g.switch("FLOAT", out(ray, "Is Hit"), near_z, hit_z)
    return g.set_position(geometry, g.xyz(x, y, g.add(ground, offset)))


def terrain_mesh(g, name="Terrain"):
    return g.realize(g.object_geometry(g.input(name)))


def side(g):
    """Unit vector to the right of the curve's direction, level."""
    tx, ty, _ = g.sep(out(g.node("GeometryNodeInputTangent")))
    return g.vec("NORMALIZE", g.xyz(ty, g.mul(tx, -1.0), 0.0))


def smooth_z(g, curve, iterations):
    x, y, z = g.sep(g.position())
    blurred = out(g.node("GeometryNodeBlurAttribute", {"Value": z, "Iterations": iterations, "Weight": 1.0},
                         data_type="FLOAT"))
    return g.set_position(curve, g.xyz(x, y, blurred))


def polyline(g, points, cyclic=False):
    """A one-spline poly curve through `points` (tuples or vector sockets), in order."""
    count = len(points)
    line = out(g.node("GeometryNodeMeshLine", {"Count": count, "Offset": (1.0, 0.0, 0.0)}))
    line = g.set_position(line, g.index_switch("VECTOR", g.index(), points))
    curve = out(g.node("GeometryNodeMeshToCurve", {"Mesh": line}))
    if cyclic:
        curve = out(g.node("GeometryNodeSetSplineCyclic", {"Curve": curve, "Cyclic": True}))
    return curve


def sweep(g, curve, profile, fill_caps=False):
    """Mesh from `curve` and a cross-section `profile` whose points hold (s, h, k) in their position:
    s across (+ = right of the curve), h up, k any value to carry along. Returns (mesh, s, h, k, length,
    right): per-vertex fields for UVs and materials, and the curve's right vector. Faces point up for an
    open profile running from -s to +s, and outward for a closed one running clockwise in (s, h)."""
    curve, right = g.capture(curve, side(g), "POINT", "FLOAT_VECTOR")
    curve, at = g.capture(curve, g.position(), "POINT", "FLOAT_VECTOR")
    curve, along = g.capture(curve, out(g.node("GeometryNodeSplineParameter"), "Length"), "POINT", "FLOAT")
    profile, shk = g.capture(profile, g.position(), "POINT", "FLOAT_VECTOR")
    mesh = out(g.node("GeometryNodeCurveToMesh", {"Curve": curve, "Profile Curve": profile, "Fill Caps": fill_caps}))
    s, h, k = g.sep(shk)
    pos = g.vec("ADD", g.vec("ADD", at, g.vec("SCALE", right, scale=s)), g.xyz(0.0, 0.0, h))
    return g.set_position(mesh, pos), s, h, k, along, right


def uv(g, mesh, u, v, tile):
    return g.store(mesh, "UVMap", g.xyz(g.div(u, tile), g.div(v, tile), 0.0), data_type="FLOAT2", domain="CORNER")


def segment(g, k):
    """Index of the profile segment a face spans: the face's lowest profile index (k carries it)."""
    return out(g.node("FunctionNodeFloatToInt", [g.on_domain(k, "FACE")], rounding_mode="FLOOR"))


def yaw_rotation(g, angle):
    return out(g.node("FunctionNodeEulerToRotation", [g.xyz(0.0, 0.0, angle)]))


def atan2(g, y, x):
    return g.math("ARCTAN2", y, x)


def scaled_seed(g, offset):
    return g.add(g.input("Seed"), offset)


def pick_index(g, seed_offset):
    return g.random("INT", 0, 9999, scaled_seed(g, seed_offset))


def nearest_distance(g, target, element):
    """Distance to `target`'s points, edges or faces from each element; BIG when it has none."""
    prox = g.node("GeometryNodeProximity", {"Geometry": target, "Sample Position": g.position()},
                  target_element=element)
    return g.switch("FLOAT", out(prox, "Is Valid"), BIG, out(prox, "Distance"))


# ------------------------------------------------------------------ groups


def build_scatter():
    g = Graph.new("GS Scatter", SCATTER)
    surface = g.part(g.realize(g.object_geometry(g.input("Surface"))), "Mesh")
    nz = g.sep(out(g.node("GeometryNodeInputNormal"), "Normal"))[2]
    level = g.cmp("GREATER_EQUAL", nz, g.math("COSINE", g.degrees(g.input("Slope Limit"))))
    weight, exists = g.named(g.input("Mask"))
    masked = out(g.node("FunctionNodeBooleanMath", [g.cmp("GREATER_EQUAL", weight, 0.5), g.input("Invert Mask")],
                        operation="XOR"))
    keep = g.no(g.both(exists, masked))
    dist = g.node("GeometryNodeDistributePointsOnFaces", {
        "Mesh": surface, "Selection": g.both(level, keep), "Distance Min": g.input("Min Distance"),
        "Density Max": g.input("Density"), "Density Factor": 1.0, "Seed": g.input("Seed")},
        distribute_method="POISSON")
    points, normal = g.capture(out(dist, "Points"), out(dist, "Normal"), "POINT", "FLOAT_VECTOR")

    keep_out = g.node("GeometryNodeCollectionInfo", {"Collection": g.input("Keep Out"), "Separate Children": True,
                                                     "Reset Children": False}, transform_space="RELATIVE")
    real = g.realize(out(keep_out))
    shapes = g.join(g.part(real, "Mesh"), out(g.node("GeometryNodeCurveToMesh", {"Curve": g.part(real, "Curve")})))
    origins = out(g.node("GeometryNodeInstancesToPoints", {"Instances": out(keep_out)}))
    near = g.math("MINIMUM", nearest_distance(g, shapes, "FACES"), nearest_distance(g, shapes, "EDGES"))
    near = g.math("MINIMUM", near, nearest_distance(g, origins, "POINTS"))
    points = g.delete(points, g.cmp("LESS_THAN", near, g.input("Keep Out Distance")))
    # Points come out face by face: shuffle them (by seed) so a budget cap thins the whole surface evenly.
    points = out(g.node("GeometryNodeSortElements", {"Geometry": points,
                                                     "Sort Weight": g.random("FLOAT", 0.0, 1.0, scaled_seed(g, 5))},
                        domain="POINT"))
    limit = g.input("Max Instances")
    over = g.both(g.cmp("GREATER_THAN", limit, 0, "INT"), g.cmp("GREATER_EQUAL", g.index(), limit, "INT"))
    points = g.delete(points, over)

    scale = g.random("FLOAT", g.input("Scale Min"), g.input("Scale Max"), scaled_seed(g, 1))
    points = g.set_position(points, offset=g.xyz(0.0, 0.0, g.mul(g.mul(g.input("Sink"), scale), -1.0)))
    upright = out(g.node("FunctionNodeAlignRotationToVector", {"Factor": g.input("Align To Normal"),
                                                               "Vector": normal}, axis="Z"))
    turn = g.mul(g.random("FLOAT", 0.0, 2 * math.pi, scaled_seed(g, 2)), g.input("Rotation Random"))
    rotation = out(g.node("FunctionNodeRotateRotation", {"Rotation": upright, "Rotate By": yaw_rotation(g, turn)},
                          rotation_space="LOCAL"))
    sources = g.node("GeometryNodeCollectionInfo", {"Collection": g.input("Sources"), "Separate Children": True,
                                                    "Reset Children": True})
    instances = g.node("GeometryNodeInstanceOnPoints", {
        "Points": points, "Instance": out(sources), "Pick Instance": True, "Instance Index": pick_index(g, 3),
        "Rotation": rotation, "Scale": scale})
    g.output(out(instances))
    return g.finish()


def build_array():
    g = Graph.new("GS Array Along", ARRAY)
    curve = curve_in(g, "Curve")
    end = out(g.node("GeometryNodeSplineLength"), "Length")
    curve = out(g.node("GeometryNodeTrimCurve", {"Curve": curve, "Start": g.input("Start"), "End": end},
                       mode="LENGTH"))
    curve = out(g.node("GeometryNodeResampleCurve", {"Curve": curve, "Mode": "Length",
                                                     "Length": g.input("Spacing")}))
    pts = g.node("GeometryNodeCurveToPoints", {"Curve": curve}, mode="EVALUATED")
    points, tangent = g.capture(out(pts, "Points"), out(pts, "Tangent"), "POINT", "FLOAT_VECTOR")
    amount = g.menu(ARRAY[5], "INT", {"Center": 1, "Right": 1, "Left": 1, "Both": 2})
    dup = g.node("GeometryNodeDuplicateElements", {"Geometry": points, "Amount": amount}, domain="POINT")
    points, copy = out(dup, "Geometry"), out(dup, "Duplicate Index")
    sign = g.menu(ARRAY[5], "FLOAT", {"Center": 0.0, "Right": 1.0, "Left": -1.0,
                                      "Both": g.sub(1.0, g.mul(copy, 2.0))})
    tx, ty, _ = g.sep(tangent)
    right = g.vec("NORMALIZE", g.xyz(ty, g.mul(tx, -1.0), 0.0))
    j = g.input("Jitter")
    shake = g.random("FLOAT_VECTOR", g.xyz(g.mul(j, -1.0), g.mul(j, -1.0), 0.0), g.xyz(j, j, 0.0), scaled_seed(g, 1))
    move = g.vec("ADD", g.vec("SCALE", right, scale=g.mul(g.input("Offset"), sign)), shake)
    points = g.set_position(points, offset=move)
    points = drape(g, points, terrain_mesh(g), g.input("Height Offset"))
    limit = g.input("Max Count")
    over = g.both(g.cmp("GREATER_THAN", limit, 0, "INT"), g.cmp("GREATER_EQUAL", g.index(), limit, "INT"))
    points = g.delete(points, over)

    rx, ry, _ = g.sep(g.vec("SCALE", right, scale=sign))
    facing = atan2(g, g.mul(rx, -1.0), ry)          # front (-Y) toward the path
    along = atan2(g, ty, tx)                        # +X along the path
    offset_side = g.cmp("GREATER_THAN", g.math("ABSOLUTE", g.mul(g.input("Offset"), sign)), 0.001)
    angle = g.switch("FLOAT", g.input("Align"), 0.0, g.switch("FLOAT", offset_side, along, facing))
    rj = g.degrees(g.input("Rotation Jitter"))
    angle = g.add(angle, g.random("FLOAT", g.mul(rj, -1.0), rj, scaled_seed(g, 2)))
    scale = g.random("FLOAT", g.input("Scale Min"), g.input("Scale Max"), scaled_seed(g, 3))
    source = g.node("GeometryNodeCollectionInfo", {"Collection": g.input("Source"), "Separate Children": True,
                                                   "Reset Children": True})
    instances = g.node("GeometryNodeInstanceOnPoints", {
        "Points": points, "Instance": out(source), "Pick Instance": True, "Instance Index": pick_index(g, 4),
        "Rotation": yaw_rotation(g, angle), "Scale": scale})
    g.output(out(instances))
    return g.finish()


def build_fence():
    g = Graph.new("GS Fence", FENCE)
    ground = terrain_mesh(g)
    line = curve_in(g, "Curve", 0.5)
    line = g.set_position(line, offset=g.vec("SCALE", side(g), scale=g.input("Offset")))
    line = drape(g, line, ground, g.input("Height Offset"))
    height = g.input("Height")

    posts_line = out(g.node("GeometryNodeResampleCurve", {"Curve": line, "Mode": "Length",
                                                          "Length": g.input("Spacing")}))
    pts = g.node("GeometryNodeCurveToPoints", {"Curve": posts_line}, mode="EVALUATED")
    tx, ty, _ = g.sep(out(pts, "Tangent"))
    post_box = g.node("GeometryNodeBoundBox", {"Geometry": g.node("GeometryNodeObjectInfo", {
        "Object": g.input("Post")}, transform_space="ORIGINAL").outputs["Geometry"]})
    post_height = g.sub(g.sep(out(post_box, "Max"))[2], g.sep(out(post_box, "Min"))[2])
    stretch = g.div(height, g.math("MAXIMUM", post_height, 0.01))
    post = g.node("GeometryNodeObjectInfo", {"Object": g.input("Post"), "As Instance": True})
    posts = out(g.node("GeometryNodeInstanceOnPoints", {
        "Points": out(pts, "Points"), "Instance": out(post, "Geometry"),
        "Rotation": yaw_rotation(g, atan2(g, ty, tx)), "Scale": g.xyz(1.0, 1.0, stretch)}))

    count = g.input("Rails")
    dup = g.node("GeometryNodeDuplicateElements", {"Geometry": posts_line, "Amount": count}, domain="SPLINE")
    k = out(dup, "Duplicate Index")
    spread = g.div(k, g.math("MAXIMUM", g.sub(count, 1), 1))
    level = g.switch("FLOAT", g.cmp("EQUAL", count, 1, "INT"), g.add(0.28, g.mul(0.55, spread)), 0.55)
    rails_line = g.set_position(out(dup, "Geometry"), offset=g.xyz(0.0, 0.0, g.mul(height, level)))
    r = g.input("Rail Size")
    half, tall = g.mul(r, 0.5), g.mul(r, 1.4)
    section = polyline(g, [g.xyz(g.mul(half, -1.0), g.mul(tall, -0.5)), g.xyz(half, g.mul(tall, -0.5)),
                           g.xyz(half, g.mul(tall, 0.5)), g.xyz(g.mul(half, -1.0), g.mul(tall, 0.5))][::-1],
                       cyclic=True)                 # clockwise in (s, h): outward normals
    rails, *_ = sweep(g, rails_line, section, fill_caps=True)
    rails = g.flat(g.material(rails, g.input("Rail Material")))

    pk_line = out(g.node("GeometryNodeResampleCurve", {"Curve": line, "Mode": "Length",
                                                       "Length": g.input("Picket Spacing")}))
    pk = g.node("GeometryNodeCurveToPoints", {"Curve": pk_line}, mode="EVALUATED")
    px, py, _ = g.sep(out(pk, "Tangent"))
    board_h = g.mul(height, 0.92)
    board = out(g.node("GeometryNodeMeshCube", {"Size": g.xyz(0.07, 0.025, board_h)}))
    board = g.set_position(board, offset=g.xyz(0.0, 0.0, g.mul(board_h, 0.5)))
    board = g.material(board, g.input("Picket Material"))
    pickets = out(g.node("GeometryNodeInstanceOnPoints", {
        "Points": out(pk, "Points"), "Instance": board, "Rotation": yaw_rotation(g, atan2(g, py, px))}))

    empty = g.join()
    rails = g.menu(FENCE[4], "GEOMETRY", {"Rails": rails, "Pickets": rails, "None": empty})
    pickets = g.menu(FENCE[4], "GEOMETRY", {"Rails": empty, "Pickets": pickets, "None": empty})
    g.output(g.join(posts, rails, pickets))
    return g.finish()


def _road_profile(g, kerbs):
    """(curve, segment parts): 0 road, 1 kerb or edge, 2 painted line."""
    hw = g.mul(g.input("Width"), 0.5)
    kw, kh = g.input("Kerb Width"), g.input("Kerb Height")
    lw = g.input("Line Width")
    inner = g.sub(hw, 0.2)                 # painted edge line from inner - lw to inner
    line_in = g.sub(inner, lw)
    rows = []
    if kerbs:
        outer = g.add(hw, kw)
        rows += [(g.add(outer, 0.35), -0.4), (outer, kh), (hw, kh), (hw, 0.0)]
        parts = [1, 1, 1]
    else:
        rows += [(g.add(hw, 0.4), -0.4), (hw, 0.0)]
        parts = [1]
    rows += [(inner, 0.0), (line_in, 0.0)]
    parts += [0, 2, 0]
    left = [(g.mul(s, -1.0), h) for s, h in rows]
    right = [(s, h) for s, h in reversed(rows)]
    points = left + [(0.0, 0.0)] + right
    parts = parts + list(reversed(parts))          # one per segment, mirrored about the centre point
    return polyline(g, [g.xyz(s, h, float(i)) for i, (s, h) in enumerate(points)]), parts


def build_road():
    g = Graph.new("GS Road", ROAD)
    ground = terrain_mesh(g)
    center = curve_in(g, "Curve", g.input("Resolution"))
    center = drape(g, center, ground, 0.0)
    center, raw = g.capture(center, g.sep(g.position())[2], "POINT", "FLOAT")
    center = smooth_z(g, center, g.input("Smooth"))
    # Open ends ramp from Height Offset down to ROAD_END_SINK below the ground itself (not its smoothed
    # height) over End Taper metres, and the kerbs down to nothing, so the road meets the terrain with no
    # step to catch a walker or its collider.
    along = out(g.node("GeometryNodeSplineParameter"), "Length")
    to_end = g.math("MINIMUM", along, g.sub(out(g.node("GeometryNodeSplineLength"), "Length"), along))
    taper = g.input("End Taper")
    ramp = out(g.node("ShaderNodeMapRange", {"Value": to_end, "From Min": 0.0,
                                              "From Max": g.math("MAXIMUM", taper, 0.001), "To Min": 0.0,
                                              "To Max": 1.0}, interpolation_type="SMOOTHSTEP", clamp=True))
    square = g.either(out(g.node("GeometryNodeInputSplineCyclic")), g.cmp("LESS_EQUAL", taper, 0.0))
    ramp = g.switch("FLOAT", square, ramp, 1.0)
    lift = g.sub(g.mul(g.add(g.input("Height Offset"), ROAD_END_SINK), ramp), ROAD_END_SINK)
    x, y, z = g.sep(g.position())
    center = g.set_position(center, g.xyz(x, y, g.add(g.add(raw, g.mul(g.sub(z, raw), ramp)), lift)))
    center, ramp = g.capture(center, ramp, "POINT", "FLOAT")
    with_kerbs, kerb_parts = _road_profile(g, True)
    plain, plain_parts = _road_profile(g, False)
    kerbs = g.input("Kerbs")
    profile = out(g.node("GeometryNodeSwitch", {"Switch": kerbs, "False": plain, "True": with_kerbs},
                         input_type="GEOMETRY"))
    mesh, s, h, k, along, _ = sweep(g, center, profile)
    mesh = g.set_position(mesh, offset=g.xyz(0.0, 0.0, g.math("MINIMUM", g.mul(h, g.sub(ramp, 1.0)), 0.0)))
    mesh = uv(g, mesh, s, along, g.input("Tile"))
    seg = segment(g, k)
    part = g.switch("INT", kerbs, g.index_switch("INT", seg, plain_parts), g.index_switch("INT", seg, kerb_parts))
    painted = g.switch("MATERIAL", g.input("Lane Lines"), g.input("Road Material"), g.input("Line Material"))
    mesh = g.material(mesh, g.input("Road Material"))
    mesh = g.material(mesh, g.input("Kerb Material"), g.cmp("EQUAL", part, 1, "INT"))
    mesh = g.material(mesh, painted, g.cmp("EQUAL", part, 2, "INT"))

    # The verge: a strip beside each kerb draped on the ground vertex by vertex (smooth edges, where painting
    # the terrain's faces makes a staircase at its resolution); its outer edge and its tapered ends go under.
    vw = g.input("Verge Width")
    s0 = g.add(g.mul(g.input("Width"), 0.5), g.switch("FLOAT", kerbs, 0.1, g.add(g.input("Kerb Width"), 0.1)))
    rows = [(0.0, VERGE_LIFT), (0.75, VERGE_LIFT), (1.0, -ROAD_END_SINK)]
    right = [g.xyz(g.add(s0, g.mul(vw, f)), up, 0.0) for f, up in rows]
    left = [g.xyz(g.mul(g.add(s0, g.mul(vw, f)), -1.0), up, 0.0) for f, up in reversed(rows)]
    verge, vs, rise, _k, v_along, _ = sweep(g, center, g.join(polyline(g, left), polyline(g, right)))
    verge = drape(g, verge, ground, g.sub(g.mul(g.add(rise, ROAD_END_SINK), ramp), ROAD_END_SINK))
    verge = g.material(uv(g, verge, vs, v_along, g.input("Tile")), g.input("Verge Material"))
    faces = out(g.node("GeometryNodeAttributeDomainSize", {"Geometry": ground}, component="MESH"), "Face Count")
    on_ground = g.both(g.cmp("GREATER_THAN", vw, 0.0), g.cmp("GREATER_THAN", faces, 0, "INT"))
    verge = g.switch("GEOMETRY", on_ground, g.join(), verge)

    period = g.add(g.input("Dash Length"), g.input("Dash Gap"))
    clear = g.switch("FLOAT", square, taper, 0.0)          # the dashes are flat: keep them off the tapers
    marks = out(g.node("GeometryNodeTrimCurve", {
        "Curve": center, "Start": g.add(g.mul(g.input("Dash Gap"), 0.5), clear),
        "End": g.sub(out(g.node("GeometryNodeSplineLength"), "Length"),
                     g.switch("FLOAT", square, g.add(taper, g.input("Dash Length")), 0.0))}, mode="LENGTH"))
    dash_pts = g.node("GeometryNodeCurveToPoints", {"Curve": marks, "Length": period}, mode="LENGTH")
    dash = out(g.node("GeometryNodeMeshGrid", {"Size X": g.input("Dash Length"), "Size Y": g.input("Line Width"),
                                               "Vertices X": 2, "Vertices Y": 2}))
    dash = g.set_position(dash, offset=g.xyz(g.mul(g.input("Dash Length"), 0.5), 0.0, 0.03))
    turn = out(g.node("FunctionNodeAlignRotationToVector", {"Vector": out(dash_pts, "Tangent")}, axis="X"))
    dashes = g.realize(out(g.node("GeometryNodeInstanceOnPoints", {
        "Points": out(dash_pts, "Points"), "Instance": dash, "Rotation": turn})))
    dashes = g.material(dashes, g.input("Line Material"))
    dashes = g.switch("GEOMETRY", g.input("Lane Lines"), g.join(), dashes)
    g.output(g.flat(g.join(mesh, verge, dashes)))
    return g.finish()


def build_river():
    """Water at a level along the river. On a terrain the surface is the terrain's own faces within
    the channel band, flattened to the water level (it fits any bend and its edges hide under the
    banks); without one it is a ribbon along the curve."""
    g = Graph.new("GS River", RIVER)
    ground = terrain_mesh(g)
    depth = g.input("Depth")
    center = curve_in(g, "Curve", g.input("Resolution"))
    center = drape(g, center, ground, 0.0)
    center = smooth_z(g, center, g.input("Smooth"))
    rise = g.switch("FLOAT", g.input("Carved"), 0.05, g.mul(depth, g.input("Water Level")))
    center = g.set_position(center, offset=g.xyz(0.0, 0.0, rise))
    hw = g.mul(g.input("Width"), 0.5)
    tile = g.input("Tile")

    reach = g.add(hw, g.input("Bank"))
    across = [g.mul(reach, f) for f in (-1.0, -0.5, 0.0, 0.5, 1.0)]
    profile = polyline(g, [g.xyz(s, 0.0, float(i)) for i, s in enumerate(across)])
    ribbon, s, _h, _k, along, _ = sweep(g, center, profile)
    ribbon = uv(g, ribbon, s, along, tile)

    center, level = g.capture(center, g.sep(g.position())[2], "POINT", "FLOAT")
    x, y, _z = g.sep(g.position())
    line = out(g.node("GeometryNodeCurveToMesh", {"Curve": g.set_position(center, g.xyz(x, y, 0.0))}))
    vx, vy, _vz = g.sep(g.position())
    here = g.xyz(vx, vy, 0.0)
    prox = g.node("GeometryNodeProximity", {"Geometry": line, "Sample Position": here}, target_element="EDGES")
    dist = g.switch("FLOAT", out(prox, "Is Valid"), BIG, out(prox, "Distance"))
    band = g.add(hw, g.mul(g.input("Bank"), 0.9))
    up = g.cmp("GREATER_THAN", g.sep(out(g.node("GeometryNodeInputNormal"), "Normal"))[2], 0.3)
    outside = g.either(g.cmp("GREATER_THAN", g.on_domain(dist, "FACE"), band), g.no(up))
    water = g.delete(ground, outside, "FACE")
    nearest = out(g.node("GeometryNodeSampleNearest", {"Geometry": line, "Sample Position": here}, domain="POINT"))
    water = g.set_position(water, g.xyz(vx, vy, g.sample_index(line, level, nearest)))
    water = out(g.node("GeometryNodeRemoveAttribute", {"Geometry": water, "Name": "UVMap"}))
    water = uv(g, water, vx, vy, tile)

    faces = out(g.node("GeometryNodeAttributeDomainSize", {"Geometry": ground}, component="MESH"), "Face Count")
    mesh = g.switch("GEOMETRY", g.cmp("GREATER_THAN", faces, 0, "INT"), ribbon, water)
    mesh = g.material(mesh, g.input("Water Material"))
    g.output(g.flat(mesh))
    return g.finish()


def build_conform():
    """A terrain modifier: the ground near a path goes to the path's own (smoothed) ground height
    minus Depth, blending back over Falloff. Roads grade a flat bed, rivers dig a channel."""
    g = Graph.new("GS Terrain Conform", CONFORM, geometry_input=True)
    terrain = g.input("Geometry")
    path = curve_in(g, "Curve", g.input("Resolution"))
    path = drape(g, path, terrain, 0.0)
    path = smooth_z(g, path, g.input("Smooth"))
    path, height = g.capture(path, g.sep(g.position())[2], "POINT", "FLOAT")
    x, y, _z = g.sep(g.position())
    flat = g.set_position(path, g.xyz(x, y, 0.0))
    line = out(g.node("GeometryNodeCurveToMesh", {"Curve": flat}))

    vx, vy, vz = g.sep(g.position())
    here = g.xyz(vx, vy, 0.0)
    prox = g.node("GeometryNodeProximity", {"Geometry": line, "Sample Position": here}, target_element="EDGES")
    dist = g.switch("FLOAT", out(prox, "Is Valid"), BIG, out(prox, "Distance"))
    nearest = out(g.node("GeometryNodeSampleNearest", {"Geometry": line, "Sample Position": here}, domain="POINT"))
    target = g.sub(g.sample_index(line, height, nearest), g.input("Depth"))
    hw = g.input("Half Width")
    blend = out(g.node("ShaderNodeMapRange", {"Value": dist, "From Min": hw,
                                              "From Max": g.add(hw, g.math("MAXIMUM", g.input("Falloff"), 0.001)),
                                              "To Min": 1.0, "To Max": 0.0},
                       interpolation_type="SMOOTHSTEP", clamp=True))
    z = g.add(vz, g.mul(g.sub(target, vz), blend))
    z = g.switch("FLOAT", g.input("Only Lower"), z, g.math("MINIMUM", z, vz))
    shaped = g.set_position(terrain, g.xyz(vx, vy, z))
    near = g.cmp("LESS_THAN", dist, g.input("Paint Width"))
    painted = g.material(shaped, g.input("Paint Material"), near)
    g.output(g.switch("GEOMETRY", g.input("Paint"), shaped, painted))
    return g.finish()


def build_cliff():
    g = Graph.new("GS Cliff", CLIFF)
    ground = terrain_mesh(g)
    path = curve_in(g, "Path")
    path = out(g.node("GeometryNodeFilletCurve", {"Curve": path, "Radius": g.input("Corner Radius"),
                                                  "Limit Radius": True, "Mode": "Poly", "Count": 6}))
    path = out(g.node("GeometryNodeResampleCurve", {"Curve": path, "Mode": "Length", "Length": g.input("Detail")}))
    path = drape(g, path, ground, g.mul(g.input("Sink"), -1.0))
    path = smooth_z(g, path, 2)

    t, total = g.input("Thickness"), g.add(g.input("Height"), g.input("Sink"))
    cuts = g.math("MAXIMUM", g.math("CEIL", g.div(total, g.input("Detail"))), 2.0)
    rect = out(g.node("GeometryNodeCurvePrimitiveQuadrilateral", {"Width": t, "Height": total}, mode="RECTANGLE"))
    rect = out(g.node("GeometryNodeSubdivideCurve", {"Curve": rect, "Cuts": g.sub(cuts, 1.0)}))
    px, py, _ = g.sep(g.position())
    up = g.add(py, g.mul(total, 0.5))                                    # 0 at the bottom, total at the top
    narrow = g.sub(1.0, g.mul(g.input("Batter"), g.div(up, total)))
    rect = g.set_position(rect, g.xyz(g.mul(px, narrow), up, 0.0))
    rect = out(g.node("GeometryNodeReverseCurve", {"Curve": rect}))     # clockwise in (s, h): outward normals
    mesh, s, h, _k, _along, right = sweep(g, path, rect, fill_caps=True)

    seed = g.input("Seed")
    rough = g.input("Roughness")
    layer_h = g.input("Layer Height")
    amp = g.mul(rough, g.math("MINIMUM", g.mul(g.input("Height"), 0.3), 5.0))
    base = g.position()
    w = g.mul(seed, 7.31)

    def noise(vector, scale, detail=3.0, w_offset=0.0):
        n = g.node("ShaderNodeTexNoise", {"Vector": vector, "W": g.add(w, w_offset), "Scale": scale,
                                          "Detail": detail, "Roughness": 0.6}, noise_dimensions="4D")
        return g.mul(g.sub(out(n, "Factor"), 0.5), 2.0)          # about -1..1

    bx, by, bz = g.sep(base)
    rx, ry, _ = g.sep(right)
    forward = g.xyz(g.mul(ry, -1.0), rx, 0.0)
    detail = g.input("Detail")
    inside = g.both(g.cmp("GREATER_THAN", h, 0.01), g.cmp("LESS_THAN", h, g.sub(total, 0.01)))
    shake = g.random("FLOAT_VECTOR", (-0.4, -0.4, -0.35), (0.4, 0.4, 0.35), g.add(seed, 31))
    sx, _sy, sz = g.sep(shake)
    shake = g.vec("ADD", g.vec("SCALE", forward, scale=g.mul(sx, detail)),
                  g.xyz(0.0, 0.0, g.switch("FLOAT", inside, 0.0, g.mul(sz, detail))))
    mesh = g.set_position(mesh, offset=shake)   # break the grid of the sweep
    base = g.position()
    bx, by, bz = g.sep(base)
    big = g.add(noise(base, 0.07), g.mul(noise(base, 0.22, 2.0, 2.0), 0.5))
    wavy = g.add(h, g.mul(g.mul(layer_h, 1.2), big))             # strata that wander
    layer = out(g.node("FunctionNodeFloatToInt", [g.div(wavy, layer_h)], rounding_mode="FLOOR"))
    strata = g.random("FLOAT", -1.0, 1.0, g.add(seed, 11), layer)
    cells = g.node("ShaderNodeTexVoronoi", {"Vector": g.xyz(bx, by, g.mul(bz, 2.5)), "W": g.add(w, 3.0),
                                            "Scale": 0.28}, voronoi_dimensions="4D", feature="F1")
    block = g.mul(g.sub(g.sep(out(cells, "Color"))[0], 0.5), 2.0)
    bulge = g.add(g.add(g.mul(big, 0.5), g.mul(block, 0.35)), g.mul(strata, 0.3))
    push = g.mul(amp, g.math("MAXIMUM", g.add(g.mul(bulge, 0.5), 0.5), 0.0))   # outward only: faces never cross
    foot = g.math("MINIMUM", g.div(h, g.math("MAXIMUM", g.input("Sink"), 0.5)), 1.0)
    push = g.mul(push, g.add(0.3, g.mul(0.7, foot)))
    sideways = g.vec("SCALE", right, scale=g.mul(push, g.math("SIGN", s)))
    skyline = g.mul(g.mul(amp, 1.1), noise(g.xyz(bx, by, 0.0), 0.04, 2.0, 5.0))
    jag = g.mul(g.mul(amp, 0.2), noise(base, 0.15, 1.0, 9.0))
    top = g.cmp("GREATER_THAN", h, g.sub(total, 0.01))
    lift = g.add(g.mul(skyline, g.div(h, total)), g.switch("FLOAT", top, 0.0, jag))
    mesh = g.set_position(mesh, offset=g.vec("ADD", sideways, g.xyz(0.0, 0.0, lift)))

    nz = g.sep(out(g.node("GeometryNodeInputNormal"), "Normal"))[2]
    face_layer = out(g.node("FunctionNodeFloatToInt", [g.div(g.on_domain(wavy, "FACE"), layer_h)],
                            rounding_mode="FLOOR"))
    odd = g.both(g.cmp("EQUAL", g.math("FLOORED_MODULO", face_layer, 2.0), 1.0),
                 g.cmp("GREATER_THAN", g.random("FLOAT", 0.0, 1.0, g.add(seed, 23), face_layer), 0.3))
    mesh = g.material(mesh, g.input("Rock Material"))
    mesh = g.material(mesh, g.input("Layer Material"), odd)
    mesh = g.material(mesh, g.input("Top Material"), g.cmp("GREATER_THAN", nz, 0.45))
    mesh = out(g.node("GeometryNodeTriangulate", {"Mesh": mesh}))       # flat-shaded facets
    g.output(g.flat(mesh))
    return g.finish()


def build_building():
    g = Graph.new("GS Modular Building", BUILDING)
    fp = curve_in(g, "Footprint")
    fp = out(g.node("GeometryNodeCurveSplineType", {"Curve": fp}, spline_type="POLY"))
    fp = out(g.node("GeometryNodeMergeByDistance", {"Geometry": fp, "Distance": 0.01}))
    ends = g.node("GeometryNodeInputMeshEdgeVertices")
    p1, p2 = out(ends, "Position 1"), out(ends, "Position 2")
    x1, y1, _ = g.sep(p1)
    x2, y2, _ = g.sep(p2)
    cross = g.sub(g.mul(x1, y2), g.mul(x2, y1))
    area = out(g.node("GeometryNodeAttributeStatistic", {
        "Geometry": out(g.node("GeometryNodeCurveToMesh", {"Curve": fp})), "Attribute": cross}, domain="EDGE"), "Sum")
    reversed_fp = out(g.node("GeometryNodeReverseCurve", {"Curve": fp}))
    fp = g.switch("GEOMETRY", g.cmp("GREATER_THAN", area, 0.0), reversed_fp, fp)   # always counter-clockwise
    ring = out(g.node("GeometryNodeCurveToMesh", {"Curve": fp}))
    edge_count = out(g.node("GeometryNodeAttributeDomainSize", {"Geometry": ring}, component="MESH"), "Edge Count")

    ring, a = g.capture(ring, p1, "EDGE", "FLOAT_VECTOR")
    ring, b = g.capture(ring, p2, "EDGE", "FLOAT_VECTOR")
    ring, edge = g.capture(ring, g.index(), "EDGE", "INT")
    mids = out(g.node("GeometryNodeMeshToPoints", {"Mesh": ring}, mode="EDGES"))
    span = g.vec("SUBTRACT", b, a)
    length = g.vec("LENGTH", span)
    count = g.math("MAXIMUM", g.math("ROUND", g.div(length, g.input("Module Width"))), 1.0)
    mids, n = g.capture(mids, count, "POINT", "FLOAT")
    dup = g.node("GeometryNodeDuplicateElements", {"Geometry": mids, "Amount": n}, domain="POINT")
    modules, k = out(dup, "Geometry"), out(dup, "Duplicate Index")
    modules, k = g.capture(modules, k, "POINT", "INT")
    floors = g.input("Floors")
    dup = g.node("GeometryNodeDuplicateElements", {"Geometry": modules, "Amount": floors}, domain="POINT")
    modules, floor = out(dup, "Geometry"), out(dup, "Duplicate Index")

    fh = g.input("Floor Height")
    width = g.div(length, n)
    direction = g.vec("NORMALIZE", span)
    centre = g.vec("ADD", a, g.vec("SCALE", direction, scale=g.mul(g.add(k, 0.5), width)))
    modules = g.set_position(modules, g.vec("ADD", centre, g.xyz(0.0, 0.0, g.mul(floor, fh))))
    dx, dy, _ = g.sep(direction)
    rotation = yaw_rotation(g, atan2(g, dy, dx))
    scale = g.xyz(g.div(width, PART_WIDTH), 1.0, g.div(fh, PART_HEIGHT))

    door_edge = g.math("FLOORED_MODULO", g.input("Door Side"), edge_count)
    is_door = g.both(g.both(g.cmp("EQUAL", edge, door_edge), g.cmp("EQUAL", k, g.math("FLOOR", g.div(n, 2.0)))),
                     g.cmp("EQUAL", floor, 0, "INT"))
    lucky = g.cmp("LESS_THAN", g.random("FLOAT", 0.0, 1.0, g.input("Seed")), g.input("Window Ratio"))
    is_window = g.both(g.no(is_door), lucky)
    is_wall = g.no(g.either(is_door, is_window))

    def place(part, selection):
        geo = out(g.node("GeometryNodeObjectInfo", {"Object": g.input(part), "As Instance": True}), "Geometry")
        return out(g.node("GeometryNodeInstanceOnPoints", {"Points": modules, "Selection": selection,
                                                           "Instance": geo, "Rotation": rotation, "Scale": scale}))

    walls = g.join(place("Wall Part", is_wall), place("Window Part", is_window), place("Door Part", is_door))

    corners = out(g.node("GeometryNodeMeshToPoints", {"Mesh": ring}, mode="VERTICES"))
    out_dir = g.sample_index(ring, direction, g.index(), "FLOAT_VECTOR", "EDGE")
    cx, cy, _ = g.sep(out_dir)
    corner_geo = out(g.node("GeometryNodeObjectInfo", {"Object": g.input("Corner Part"), "As Instance": True}),
                     "Geometry")
    full = g.mul(floors, fh)
    corners = out(g.node("GeometryNodeInstanceOnPoints", {
        "Points": corners, "Instance": corner_geo, "Rotation": yaw_rotation(g, atan2(g, cy, cx)),
        "Scale": g.xyz(1.0, 1.0, g.div(full, PART_HEIGHT))}))

    fill = out(g.node("GeometryNodeFillCurve", {"Curve": fp, "Mode": "N-gons"}))
    slab = out(g.node("GeometryNodeExtrudeMesh", {"Mesh": fill, "Offset": (0.0, 0.0, 1.0), "Offset Scale": 0.12}))
    slab = g.material(slab, g.input("Floor Material"))
    levels = out(g.node("GeometryNodePoints", {"Count": floors, "Position": g.xyz(0.0, 0.0, g.mul(g.index(), fh))}))
    slabs = g.realize(out(g.node("GeometryNodeInstanceOnPoints", {"Points": levels, "Instance": slab})))

    deep = g.input("Foundation")
    footing = g.set_position(fill, offset=g.xyz(0.0, 0.0, g.mul(deep, -1.0)))
    footing = out(g.node("GeometryNodeExtrudeMesh", {"Mesh": footing, "Offset": (0.0, 0.0, 1.0),
                                                     "Offset Scale": deep}))
    footing = g.material(footing, g.input("Base Material"))

    walls = g.join(walls, corners, slabs, footing, _roof(g, fp, ring, edge_count, full))
    g.output(g.switch("GEOMETRY", g.input("Realize"), walls, g.realize(walls)))
    return g.finish()


def _roof(g, fp, ring, edge_count, base_z):
    """Gable, hip or flat roof over the footprint; the ridge runs along the longer of the first two
    walls. The base is the footprint grown by Overhang (exact for rectangles)."""
    ends = g.node("GeometryNodeInputMeshEdgeVertices")
    vec = g.vec("SUBTRACT", out(ends, "Position 2"), out(ends, "Position 1"))
    e0 = g.sample_index(ring, vec, 0, "FLOAT_VECTOR", "EDGE")
    e1 = g.sample_index(ring, vec, g.math("FLOORED_MODULO", 1.0, edge_count), "FLOAT_VECTOR", "EDGE")
    l0, l1 = g.vec("LENGTH", e0), g.vec("LENGTH", e1)
    first_long = g.cmp("GREATER_EQUAL", l0, l1)
    long_axis = g.vec("NORMALIZE", g.switch("VECTOR", first_long, e1, e0))
    short_axis = g.vec("NORMALIZE", g.switch("VECTOR", first_long, e0, e1))
    long_len = g.math("MAXIMUM", l0, l1)
    short_len = g.math("MAXIMUM", g.math("MINIMUM", l0, l1), 0.01)
    o = g.input("Overhang")

    fill = out(g.node("GeometryNodeFillCurve", {"Curve": fp, "Mode": "N-gons"}))
    grow_long = g.div(g.add(long_len, g.mul(o, 2.0)), long_len)
    grow_short = g.div(g.add(short_len, g.mul(o, 2.0)), short_len)
    base = out(g.node("GeometryNodeScaleElements", {"Geometry": fill, "Scale": grow_long, "Scale Mode": "Single Axis",
                                                    "Axis": long_axis}))
    base = out(g.node("GeometryNodeScaleElements", {"Geometry": base, "Scale": grow_short,
                                                    "Scale Mode": "Single Axis", "Axis": short_axis}))
    base = g.set_position(base, offset=g.xyz(0.0, 0.0, base_z))

    rise = g.mul(g.math("TANGENT", g.degrees(g.input("Roof Pitch"))), g.add(g.mul(short_len, 0.5), o))
    roof = g.node("GeometryNodeExtrudeMesh", {"Mesh": base, "Offset": g.xyz(0.0, 0.0, rise), "Offset Scale": 1.0})
    peaked = out(g.node("GeometryNodeScaleElements", {"Geometry": out(roof, "Mesh"), "Selection": out(roof, "Top"),
                                                      "Scale": 0.0, "Scale Mode": "Single Axis",
                                                      "Axis": short_axis}))
    hip = g.math("MAXIMUM", g.div(g.sub(long_len, short_len), long_len), 0.0)
    hipped = out(g.node("GeometryNodeScaleElements", {"Geometry": peaked, "Selection": out(roof, "Top"),
                                                      "Scale": hip, "Scale Mode": "Single Axis",
                                                      "Axis": long_axis}))
    flat = out(g.node("GeometryNodeExtrudeMesh", {"Mesh": base, "Offset": (0.0, 0.0, 1.0), "Offset Scale": 0.35}))
    roof = g.menu(BUILDING[11], "GEOMETRY", {"Gable": peaked, "Hip": hipped, "Flat": flat})
    roof = out(g.node("GeometryNodeMergeByDistance", {"Geometry": roof, "Distance": 0.001}))
    nz = g.sep(out(g.node("GeometryNodeInputNormal"), "Normal"))[2]
    roof = g.material(roof, g.input("Gable Material"))
    roof = g.material(roof, g.input("Roof Material"), g.cmp("GREATER_THAN", g.math("ABSOLUTE", nz), 0.3))
    return g.flat(roof)


def build_proxy():
    g = Graph.new("GS Collider Proxy", PROXY)
    geo = g.object_geometry(g.input("Source"))
    instances = g.part(geo, "Instances")
    real = g.realize(g.part(geo, "Mesh"))

    instances, size = g.capture(instances, out(g.node("GeometryNodeInputInstanceScale")), "INSTANCE",
                                "FLOAT_VECTOR")
    spots = out(g.node("GeometryNodeInstancesToPoints", {"Instances": instances}))
    trunk = out(g.node("GeometryNodeMeshCylinder", {"Vertices": 8, "Radius": g.input("Radius"),
                                                    "Depth": g.input("Height")}, fill_type="NGON"))
    trunk = g.set_position(trunk, offset=g.xyz(0.0, 0.0, g.mul(g.input("Height"), 0.5)))
    trunks = g.realize(out(g.node("GeometryNodeInstanceOnPoints", {"Points": spots, "Instance": trunk,
                                                                    "Scale": size})))

    start = g.nodes.new("GeometryNodeForeachGeometryElementInput")
    end = g.nodes.new("GeometryNodeForeachGeometryElementOutput")
    start.pair_with_output(end)
    end.domain = "INSTANCE"
    g.set(socket(start.inputs, "Geometry"), instances)
    hull = out(g.node("GeometryNodeConvexHull", {"Geometry": g.realize(out(start, "Element"))}))
    g.links.new(hull, end.inputs[1])
    hulls = end.outputs[2]

    shape = g.menu(PROXY[1], "GEOMETRY", {"Hull": hulls, "Trunk": trunks, "Mesh": g.realize(instances)})
    g.output(g.material(g.join(real, shape), g.input("Material")))
    return g.finish()


# ------------------------------------------------------------------ registry and versions

BUILDERS = {
    # kind: (version, input specs, build function)
    "scatter": (1, SCATTER, build_scatter),
    "array": (1, ARRAY, build_array),
    "fence": (1, FENCE, build_fence),
    "road": (2, ROAD, build_road),
    "river": (1, RIVER, build_river),
    "conform": (1, CONFORM, build_conform),
    "cliff": (1, CLIFF, build_cliff),
    "building": (1, BUILDING, build_building),
    "proxy": (1, PROXY, build_proxy),
}


def specs(kind) -> list:
    return BUILDERS[kind][1]


def find(kind):
    version = BUILDERS[kind][0]
    for tree in bpy.data.node_groups:
        if tree.get(GROUP_PROP) == kind and tree.get(VERSION_PROP) == version and tree.library is None:
            return tree
    return None


def ensure(kind):
    """The node group for `kind`, built on first use; older versions are replaced (modifiers keep
    their input values)."""
    tree = find(kind)
    if tree is not None:
        return tree
    version, _specs, build = BUILDERS[kind]
    tree = build()
    tree[GROUP_PROP] = kind
    tree[VERSION_PROP] = version
    for old in [t for t in bpy.data.node_groups if t.get(GROUP_PROP) == kind and t is not tree
                and t.library is None]:
        _migrate(old, tree)
    return tree


def _migrate(old, new) -> None:
    from . import modifiers

    for obj in bpy.data.objects:
        for mod in obj.modifiers:
            if mod.type == "NODES" and mod.node_group == old:
                values = modifiers.read_inputs(mod)
                mod.node_group = new
                modifiers.write_inputs(mod, values, strict=False)
    if old.users == 0:
        bpy.data.node_groups.remove(old)
