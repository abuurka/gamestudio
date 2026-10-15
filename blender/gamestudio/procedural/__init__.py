# SPDX-License-Identifier: GPL-3.0-or-later
"""Procedural world toolkit: non-destructive, parametric worldbuilding on geometry nodes.

Every builder makes one object whose geometry comes from a geometry-nodes modifier written in code
(groups.py; no shipped .blend). Its inputs stay editable in the modifier panel and through
set_params(), with no rebuild, and the same seed and inputs always give the same result. Agents call
them through run_python as gs.procedural.<name>(...):

    scatter(surface, sources, density=0.05, seed=0, *, min_distance=2.0, scale_range=(0.8, 1.2),
            rotation_random=1.0, align_to_normal=0.0, slope_limit=35.0, mask=None, keep_out=None,
            keep_out_distance=1.5, sink=0.1, max_instances=2000, colliders="none", name, collection)
        Instances of `sources` (objects, names or a collection) over a mesh with Poisson-disc spacing,
        none on faces steeper than slope_limit. mask: a vertex group on the surface (weight >= 0.5 keeps
        instances out; invert_mask flips it) or keep-out objects; keep_out: objects whose surfaces,
        edges (curves) and origins (empties) instances stay keep_out_distance away from.
    array_along(curve, source, spacing=6.0, jitter=0.0, *, offset=0.0, sides="center"|"right"|"left"|"both",
                start=0.0, rotation_jitter=0.0, scale_range=(1, 1), align=True, seed=0, terrain=None, ...)
        Copies every `spacing` metres along a curve, `offset` to the side (facing it), on a terrain.
    fence_along(curve, post_source=None, rail_style="rails"|"pickets"|"none", spacing=2.0, *, height=1.0,
                rails=2, offset=0.0, terrain=None, ...)
        Instanced posts (a generated one by default, scaled to `height`) and swept rails or pickets.
    road_along(curve, width=6.0, kerbs=True, lane_lines=True, *, terrain=None, grade=True, shoulder=2.0,
               taper=2.0, verge_material="dirt", ...)
        A road mesh, level across and smoothed along, with a UV map for a tiling texture ("UVMap": u across,
        v along, `tile` metres per repeat). On a terrain it is draped, grades the ground under it and lays
        a verge strip beside the kerbs. Open ends sink into the ground over `taper` metres.
    river_along(curve, width=6.0, depth=1.5, *, terrain=None, carve=True, bank=3.0, water_level=0.7, ...)
        A water surface in a channel it carves into the terrain (sandy bed).
    cliff(path_or_bounds, height=10.0, roughness=0.5, seed=0, *, thickness=4.0, inset=0.0, terrain=None, ...)
    rock_wall(path_or_bounds, height=2.5, roughness=0.35, seed=0, ...)
        Displaced, layered walls along a curve or [[x, y], ...] points, or a ring around bounds
        ([xmin, ymin, xmax, ymax] or {"min": [x, y], "max": [x, y]}) or a mesh's bounds (a terrain's rim,
        `inset` metres in).
    modular_building(footprint=(8, 6), floors=2, style="cottage"|"stone"|"brick"|"timber", seed=0, *,
                     location=(0, 0, 0), rotation=0.0, roof=None ("gable"|"hip"|"flat"), terrain=None, ...)
        Wall, window, door and corner modules over a footprint ((width, depth), corners or a closed curve),
        floor slabs and a roof; realized into one mesh unless realize=False.

    path(points, *, name="Path", closed=False, smooth=True) -> curve object through [x, y(, z)] points
    kit_sources(kind, variants=3, params=None, *, name=None) -> Collection of kit objects to scatter
    part("fence_post" | "lamp_post") -> a generated source object
    ground_height(terrain, x, y, default=0.0) -> the evaluated terrain's height at (x, y)
    params(obj) -> dict              every input by key, plus kind, colliders and the builder's extras
    set_params(obj, **inputs) -> dict    change inputs (validated; references by name); returns params(obj)
    stats(obj) -> dict               instances (per source), triangles, own triangles, expected draw calls
    realize(obj, mode="mesh"|"objects")  bake to a plain mesh (or one object per instance) for hand edits
    remove(obj)                      delete a builder object, its collider proxy and its terrain modifier
    catalog() -> list[dict]          a JSON Schema per builder, like kits.catalog()

Builders return the new object, named `name` (Blender adds .001), at the origin (a building at
`location`), in a collection per builder: Scatter, Arrays, Fences, Roads, Rivers, Cliffs, Buildings;
paths go to Paths. Objects that show only through instances (scatter sources, generated posts and
building parts) are moved into "Procedural Sources", a collection excluded from the view layer. Inputs
that take objects read them in the builder object's space, so moving a curve moves its road.

Game-readiness:
  * Instances stay instances: each source exports as one EXT_mesh_gpu_instancing node (one draw call per
    material of the source) once it repeats 4+ times under the builder object. Realized builders (road,
    river, cliff, building) export as one mesh with one draw call per material.
  * Colliders: the engine fits a Collider on a scatter, array or fence object to every GPU instance, so
    colliders="hull", "box" or "mesh" put a static Collider {shape: "convex" | "box" | "mesh"} on the object
    itself (no extra GLB bytes): one hull or box per instance (a source repeated under 4 times is not
    instanced, and its copies share one hull or box), or one exact trimesh. "trunk" adds an invisible child
    <name>_Collider of trunk cylinders (Wire display + Collider: exported, not drawn), so canopies do not
    block. "simple" is trunk for tree kits and mesh otherwise. A RigidBody on an instanced builder is still
    wrong. Realized builders put Collider {shape: "mesh"} on themselves (rivers: a sensor); a road's open
    ends taper into the ground (`taper`), so its collider has no step there. Defaults: none for scatter,
    array and fence; simple for road, cliff and building.
  * Budgets: scatter max_instances (2000) and array max_count (1000) cap instance counts; stats() reports
    triangles and draw calls before export; catalog() lists typical triangle counts.

Errors are ProceduralError (a ValueError) with a message that says what to change; nothing is left half
built.
"""

from .builders import (array_along, cliff, fence_along, ground_height, kind_of, kit_sources, modular_building, params,
                       part, path, realize, remove, river_along, road_along, rock_wall, scatter, set_params, stats)
from .catalog import catalog
from .errors import ProceduralError

__all__ = ["ProceduralError", "array_along", "catalog", "cliff", "fence_along", "ground_height", "kind_of", "kit_sources",
           "modular_building", "params", "part", "path", "realize", "remove", "river_along", "road_along",
           "rock_wall", "scatter", "set_params", "stats"]


def register():
    pass


def unregister():
    pass
