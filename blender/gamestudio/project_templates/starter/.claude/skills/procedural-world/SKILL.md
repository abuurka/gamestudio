---
name: procedural-world
description: Build outdoor levels with GameStudio's geometry-nodes toolkit (the create_procedural and set_procedural tools, or gs.procedural in run_python). It scatters forests, bushes and rocks; lays roads, rivers, fences and lamp rows along paths; rings a level with cliffs; and assembles village buildings. Everything stays parametric, and repeated objects export as GPU instances. Use when the user asks for a forest, woods, scattered rocks or grass, a road or path, a river or bridge, cliffs or walls around a level, a village or town, or to dress a large outdoor area.
argument-hint: "[what to build]"
---

# Build a procedural world

Geometry nodes are what Blender does that a game engine cannot: a forest, a road or a village stays a **live recipe** (a modifier with inputs) that you can change at any time, and the same seed always gives the same result. `gs.procedural` writes those node trees for you.

## Geometry nodes or kits?

| You want | Use |
|---|---|
| Many copies of a few objects: a forest, rocks, bushes, posts, lamps | `gs.procedural.scatter` or `gs.procedural.array_along`. Each source draws as one GPU-instanced mesh: 300 trees cost about 4 draw calls. Instances are scenery: they have no components. |
| Something that follows a line: a road, a river, a fence, a cliff wall | `road_along`, `river_along`, `fence_along`, `cliff`. Move the curve and it re-shapes. |
| A village or a street of houses | `gs.procedural.modular_building`, one call per house. |
| One object that you will hand-edit, animate or give gameplay components (a door, a key tree, a crate to push) | A kit (`create_from_kit`). Kits are also the sources that you scatter. |
| Five trees placed exactly where you choose | Kits and `set_transform`. |

## Calling it

**The tools first.** `create_procedural {kind, params, name?, collection?}` builds one part, and `set_procedural {object, params}` changes its inputs. Each is one undo step, and bad params change nothing. Kinds: `scatter`, `array`, `fence`, `road`, `river`, `cliff`, `rock_wall`, `building`. `list_kits` has a `procedural` section, and `list_kits {"kind": "road"}` gives one builder's schema. Results carry `params` and `stats` (instances per source, triangles, draw calls).

```json
create_procedural {"kind": "road", "name": "MainRoad", "params": {"curve": [[-30, -20], [0, 0], [30, 18]], "terrain": "Hill", "width": 5}}
create_procedural {"kind": "scatter", "name": "Forest", "params": {"surface": "Hill", "sources": ["Tree_1", "Tree_2", "Rock_1"], "density": 0.03, "keep_out": ["MainRoad"]}}
set_procedural {"object": "Forest", "params": {"density": 0.05, "seed": 3}}
```

**`gs.procedural` in `run_python`** does the same and more in one call: helpers such as `kit_sources`, `path`, `part`, `ground_height`, `remove` and `realize`, and recipes that build several parts at once (below). Every builder returns the new object. `catalog()` lists every input with its range and default.

```python
t = D.objects["Terrain"]
trees = gs.procedural.kit_sources("tree", [{"style": "round"}, {"style": "pine", "height": 8}], name="Trees")
forest = gs.procedural.scatter(t, trees, density=0.05, seed=1, min_distance=3, keep_out=["Road", "PlayerStart"])
gs.procedural.stats(forest)        # {"instances": 240, "triangles": ..., "draw_calls": 4, ...}
```

- **Placement and collections.** Builders work in metres in Blender space (Z up). Their objects sit at the origin, except buildings, which sit at `location`. Each kind goes in its own collection: `Scatter`, `Arrays`, `Fences`, `Roads`, `Rivers`, `Cliffs` or `Buildings`. Paths made from points go to `Paths`.
- **Paths.** Any `curve` argument takes a curve object's name or a list of points `[[x, y], ...]`. Points are dropped onto `terrain` when you give one. `gs.procedural.path(points, name=..., closed=..., smooth=...)` makes a reusable path. A list of point lists makes one curve with gaps, for example a wall with openings.
- **Sources.** Scatter and array sources (objects, names or a collection) move into the excluded **Procedural Sources** collection. They are seen only through their instances, so don't snap or render them on their own. `kit_sources(kind, variants, params)` makes several kit variants in one call. `part("lamp_post")` and `part("fence_post")` are ready-made sources.
- **Defaults.** Inputs you leave out keep their defaults. Read them all back with `gs.procedural.params(obj)`.

## Recipes

Run each recipe in one `run_python` call, after a `checkpoint`. `T` is the terrain, from `create_from_kit` with `terrain` (size 100–200, resolution 64 for 100 m and up to 128 for the 150–200 m terrains that roads and rivers carve, `flatten_center` for a village).

**Cliffs around a level, open where a road and a river pass.** A mesh object gives its bounds: `inset` moves the ring inward.

```python
P = gs.procedural
P.cliff(T, height=12, thickness=6, inset=6, terrain=T, name="Cliffs")  # a closed ring
e = 69  # or a wall with gaps, made from three straight runs:
rim = P.path([[(17, -e), (e, -e), (e, 12)], [(e, 33), (e, e), (-e, e), (-e, 51)], [(-e, 30), (-e, -e), (0, -e)]],
             name="CliffsPath", smooth=False)
P.cliff(rim, height=13, thickness=7, roughness=0.6, corner_radius=14, terrain=T, name="Cliffs")
```

**Road with fences and lamps.** The road grades a flat bed into the terrain and puts a mesh Collider on itself. Its open ends sink into the ground over `taper` metres (2 by default), kerbs and verge too, so players and enemies walk on and off with no step. `taper=0` gives square ends.

```python
road_path = P.path([(10, -76), (6, -58), (-6, -42), (0, -14)], name="RoadPath")
road = P.road_along(road_path, width=6, terrain=T, name="Road")       # kerbs and lane lines on by default
P.fence_along(road_path, offset=-5.2, terrain=T, name="RoadFenceWest")
P.fence_along(road_path, offset=5.2, rail_style="pickets", terrain=T, name="RoadFenceEast")
P.array_along(road_path, "lamp_post", spacing=14, offset=4.1, sides="both", terrain=T, name="RoadLamps")
```

For a footpath, use `road_along(..., width=2.4, kerbs=False, lane_lines=False, road_material="dirt", kerb_material="dirt", verge_material=None)`. The verge is a strip of the road's own mesh beside the kerbs (`shoulder` × 0.6 wide), draped on the terrain with smooth edges; `verge_material=None` drops it.

**Coins along a path.** Array the coin object that carries the pickup component, then turn the instances into objects so that each one is an entity: `gs.procedural.realize(P.array_along(road_path, "Coin", spacing=3, height_offset=1, terrain=T, name="Coins"), mode="objects")`. Each copy gets the source's components.

**River with a bridge.** The river carves its channel into the terrain, with a sandy bed. Roads and rivers must not cross: end the paths at the banks and bridge the gap.

```python
river = P.river_along([(-80, 44), (-26, 38), (0, 33), (22, 22), (80, 24)], width=8, depth=1.6, bank=4,
                      terrain=T, name="River")
bx, by = 16.0, 25.5                                    # a point on the river; the deck spans 20 m north-south
z = P.ground_height(T, bx, by - 9.5) + 0.35            # the bank height
deck = gs.kits.create("platform", {"name": "BridgeDeck", "width": 3.2, "depth": 20, "height": 0.4,
                                   "top_material": "wood", "side_material": "wood_dark"},
                      location=(bx, by, z - 0.4), collection="Level")[0]
rail = P.path([(bx, by - 10, z), (bx, by + 10, z)], name="BridgePath", smooth=False)
P.fence_along(rail, offset=-1.45, name="BridgeRailWest")
P.fence_along(rail, offset=1.45, name="BridgeRailEast")
```

Then give the deck `Collider {shape: "box"}` with `add_component`.

**A village.** Styles are `cottage`, `stone`, `brick` (hip roof) and `timber`. The door is in the middle of the front wall (−Y before `rotation`). `terrain` sets the house on the highest ground under its footprint, with a plinth down to the lowest.

```python
houses = [("HouseNorth", (9, 7), 2, "cottage", (-11, 15), 0), ("HouseEast", (10, 7), 2, "timber", (20, 2), -90),
          ("HouseWest", (9, 8), 2, "brick", (-20, -4), 90), ("Tower", (6, 6), 3, "stone", (-12, -18), 45)]
for i, (name, size, floors, style, (x, y), rot) in enumerate(houses):
    P.modular_building(size, floors=floors, style=style, seed=i, location=(x, y, 0), rotation=rot,
                       terrain=T, name=name)
```

**A forest edge that stays off everything.** Build it last, so it can keep out of the rest. `keep_out` clears objects by their surfaces, their curves and their origins, so empties such as `PlayerStart` work too.

```python
keep = ["Road", "River", "Cliffs", "Plaza", "BridgeDeck", "PlayerStart"] + [o.name for o in D.collections["Buildings"].objects]
P.scatter(T, trees, density=0.05, min_distance=3.2, slope_limit=32, keep_out=keep, keep_out_distance=3.5,
          colliders="simple", name="Forest")
P.scatter(T, gs.procedural.kit_sources("bush", 2), density=0.02, min_distance=3, keep_out=keep, name="Shrubs")
P.scatter(T, gs.procedural.kit_sources("rock", 3), density=0.006, min_distance=6, scale_range=(0.7, 1.6),
          align_to_normal=0.6, sink=0.25, keep_out=keep, colliders="hull", name="Boulders")
```

You can also mask with a vertex group: `mask="NoTrees"` keeps instances off vertices with a weight of 0.5 or more, and `invert_mask=True` scatters only there. For a clearing, paint the group in `run_python` and pass its name.

## Wind, weather and water: library shaders

The engine's shader library brings a built world to life with no code, and it works on GPU instances (each scattered tree sways in its own phase). A shader goes on a material, so it reaches every instance and every object that draws that material: `set_material_shader {"material": "GS_Leaves", "shader": "FoliageSway"}`. Kit materials are named `GS_<Preset>` (`GS_Leaves`, `GS_Pine`, `GS_Bark`, `GS_Stone`, `GS_Water`). More in `stylize-scene`, section 5.

- **Forests and bushes:** `FoliageSway` on `GS_Leaves` and `GS_Pine`, and on `GS_Bark` with the same `wind` and `windAngle` but `rustle` and `translucency` 0, so trunks bend with their crowns.
- **Grass:** `GrassWind` on the scattered blades' own material, never on the terrain's `GS_Grass` (it would bend the ground). Same `wind` and `windAngle` as the trees, so gusts roll through both.
- **Rocks and cliffs:** `SnowCover` (`amount`) for winter, `MossOvergrowth` (`progress`) for old ruins. `GS_Stone` is shared by rocks, cliffs and stone houses; give the rocks a material of their own first if only they should change.
- **Rivers:** `Flow` on the river's water. The river's UVs run U across and V along its curve, which is what Flow wants, and Flow runs towards V 0, the start of the curve: a negative `speed` turns it round. The river uses the shared `GS_Water`, so give it its own copy (`run_python`: `m = D.materials["GS_Water"].copy(); m.name = "GS_River"`, then `set_procedural {"object": "River", "params": {"water_material": "GS_River"}}`) before you put Flow on it.
- **Lakes and the sea:** `Water` on the `water` kit's surface (`set_material_shader {"objects": ["Lake"], "slot": 0, "shader": "Water"}`; `get_object` lists the slots), `waveLength` 4 for a lake, 15 for the sea. For surf around an island or rocks, add a ring a few metres wide just above the water, unwrapped V 0 at the shore to 1 out at sea, with `ShorelineFoam`.

Judge them from `game_screenshot`: Blender shows the materials' own nodes.

## Tweak without rebuilding

`set_procedural {"object": "Forest", "params": {...}}` changes any input, one undo step. In `run_python`, `gs.procedural.set_params(obj, **inputs)` does the same for any input that `params(obj)` lists, and returns the new params. Bad values raise a `ProceduralError` and change nothing. Examples:

- `set_params(forest, density=0.08, seed=4)`
- `set_params(road, width=8, lane_lines=False)`
- `set_params(house, floors=3, style="stone", roof="flat")`
- `set_params(fence, rail_style="none")`
- `set_params(forest, colliders="none")`

The user can change the same inputs in the modifier panel. Moving or editing a path curve updates its road, fence or river live. Use `gs.procedural.realize(obj)` only for one-off hand edits: it bakes the object to a plain mesh, and set_params no longer works on it. `gs.procedural.remove(obj)` deletes a builder object along with its collider proxy and the terrain modifier it added.

## Colliders and physics

- **The default.** Roads, cliffs and buildings carry `Collider {shape: "mesh"}`. Scatters, arrays and fences carry none.
- **Instanced builders.** Give a scatter, array or fence colliders with `colliders`, not by hand. `"hull"`, `"box"` and `"mesh"` put a static `Collider` on the builder object, and the game fits it to every instance: a convex hull each (rocks), a box each (crates, posts) or one exact mesh. They add nothing to the GLB, but a source repeated fewer than 4 times is not instanced, and its copies share one hull or box. `"trunk"` adds an invisible child `<name>_Collider` of trunk cylinders, so canopies do not block. `"simple"` is trunk for tree kits and mesh otherwise. Never put a `RigidBody` on them.
- **Invisible colliders.** A mesh with a `Collider` drawn as Wire or Bounds (Object Properties > Viewport Display > Display As) is exported but not drawn in the game: use one for an invisible wall or a simpler collision shape.
- **Things that move.** For objects that must move, roll or be picked up, use kit objects with their own components, or prefabs spawned in code. Scattered instances cannot.
- **The terrain.** It keeps its own `Collider {shape: "mesh"}`. Road and river shaping is part of the terrain's mesh, so the terrain collider follows it.

## Verify

1. `gs.procedural.stats(obj)` for each builder: instances, triangles and the draw calls it will cost. A forest of 4 tree variants is about 8 draw calls however many trees it has.
2. `render_views {"views": ["top", "iso"]}`. Look at it. Check that nothing grows on the road, river or plaza, that the houses stand on the ground, and that the cliffs close the level where they should. Add `focus: true` with a `target` to inspect one area.
3. `compare_views` shows Blender and the game side by side from the scene camera. They should match: same trees, same colours, water see-through in both. Materials with a game shader (flowing water, leaves in the wind, snow; the section above) differ by design: judge those from `game_screenshot`.
4. `game_stats`. Draw calls count the shadow pass too, roughly 2× the visible meshes. Instanced sources show as one mesh each with many instances. A valley with 900 instances, six houses, a road, a river and cliffs runs at about 95 draw calls for the main pass.
5. `playtest`: walk the road and cross the bridge. The player stops at the cliffs and at tree trunks when the forest has `colliders="simple"`.
6. `save`.

## Common failures

| Symptom | Fix |
|---|---|
| Trees on the road or inside a house | Add the object to `keep_out` (`set_params(forest, keep_out=[...])`) or raise `keep_out_distance`. Build the forest after the things it must avoid. |
| A road dips into a river, or water covers a road | They cross. End the road at the banks and add a bridge (recipe above). |
| Water floats above the banks, or no channel shows | The river needs `terrain` and `carve=True`, plus enough terrain resolution: at least 3 cells across the width, so use `width` 6–10 with cells of 2 m or less (resolution 64 for 100 m, 128 for 200 m). |
| Houses float on one side | Pass `terrain` when you build them, or raise the plinth: `gs.procedural.set_params(house, foundation=2)`. |
| A builder changed but the game shows the old one | Export again: `play` or `export_scene` (`{"all": true}` in a game with several levels). |
| An enemy stops at the end of a road and only gets round after a while | A road from before tapered ends has a small step there. Any `set_procedural` on it upgrades it, for example `{"taper": 2}`. |
| Thousands of instances and slow exports | Lower `density` or `max_instances`. The export warns from 5,000 instances per object. |
| `validate_assets` warns "No UV map", "No material" or "origin outside the geometry" on a builder object | Expected: it reads the object's empty base mesh, not the geometry-nodes result, and builder objects sit at the origin by design. Check `gs.procedural.stats(obj)` and `render_views` instead. |
| `params` raises "not made by a procedural builder" | The object was realized, or it is a source. Use the builder object (Scatter, Roads, ... collections). |
