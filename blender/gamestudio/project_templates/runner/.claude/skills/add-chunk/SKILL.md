---
name: add-chunk
description: Add a new track chunk to this endless runner. Designs what the player does in it, builds it as a prefab in the Chunks scene from kits or procedural builders, adds Socket empties for obstacles, coins and pickups, sets its TrackChunk weight and minDistance, then playtests it with a fixed seed until it streams in, plays fair and looks right. Use when the user wants a new section of track, more variety, a harder or easier stretch, a new obstacle pattern, or "a part where you have to ...".
argument-hint: "[the chunk: what the player does in it]"
---

# Add a track chunk

The road is made of **chunk prefabs**: collections marked `gs_prefab` in the `Chunks` scene of `scenes/main.blend`, each with a root empty that carries `TrackChunk {length, weight, minDistance}`. `TrackSystem` (`src/systems/track.ts`) treats every prefab whose root has a `TrackChunk` as a chunk, picks them by weight from `ctx.random.stream('track')`, and `src/lib/sockets.ts` fills each copy's `Socket` empties with obstacles, coins and pickups. A new chunk is a new prefab: no code changes.

## 1. Design it in four lines

- **The idea**: one thing the player does: weave, jump a gap, pick a side, stay on a narrow path, time a moving hazard.
- **Metrics**: lanes at x = −2.2, 0 and 2.2; the road's top at z = 0 and 7.6 m wide. The runner moves at 10 m/s at the start and 20 m/s from 2 km, jumps 1.35 m high and 6 to 12 m far (0.6 s in the air), and changes lane in about 0.2 s. Hurdles are 0.75 m high (jump them), crates 1.85 m and boulders 1.5 m (go around them).
- **Fairness**: every row of obstacles leaves a lane free or is jumpable. Keep the first and last 4 m clear: any chunk can come before or after this one. Nothing to react to within 8 m after a gap or a drop.
- **Difficulty**: its `weight` and `minDistance` next to the others:

  | Chunk | Length | Weight | minDistance | What it asks |
  |---|---|---|---|---|
  | `ChunkStraight` | 30 | 3 | 0 | two obstacles in random lanes |
  | `ChunkGap` | 30 | 2 | 80 | jump a 3.5 m gap |
  | `ChunkSlalom` | 36 | 2 | 160 | three rows, one free lane each |
  | `ChunkRamp` | 36 | 1.5 | 240 | up a ramp, off a 2.2 m drop |
  | `ChunkNarrow` | 30 | 1.5 | 320 | a one-lane bridge with a hurdle |
  | `ChunkSweeper` | 30 | 1.5 | 400 | a drum rolling across the lanes |

## 2. Build the prefab

`checkpoint` with the label "before <name>", then `run_python` with the level script's `Chunk` helper. Coordinates are local to the chunk's start: x across, y along the road (0 to `length`), z up.

```python
import pathlib, runpy
lvl = runpy.run_path(str(pathlib.Path(bpy.data.filepath).parents[1] / "tools" / "build_level.py"))
c = lvl["Chunk"](gs, "ChunkPillars", length=30, weight=1.5, min_distance=300)  # marks the collection gs_prefab
c.floor(0, 30)                                   # a slab with a Collider, lane lines and curbs
c.pillar(15)                                     # decor under the road
for y, lane in ((8, 0), (15, 2), (22, 1)):
    c.socket("obstacle", (lvl["LANES"][lane], y), options="Crates:2, Boulder:1")
c.socket("coins", (0, 4), any_lane=True, chance=0.8)
c.socket("pickup", (0, 26), any_lane=True, chance=0.15)
c.lamp(-1, 6)                                    # a LampPost collection instance (a nested prefab)
c.lamp(1, 21)
column = gs.kits.create("rock", {"name": "ChunkPillars_Rock", "size": 2.5, "seed": 3}, collection=c.coll)[0]
c.attach(column, (5.5, 12, -1.0))                # anything else: build it, then attach it to the root
c.finish()                                       # the decor mesh
result = c.root.name
```

| `Chunk` method | Makes |
|---|---|
| `floor(y0, y1, x0=-3.8, x1=3.8, top=0, planks=False)` | A floor slab with a `Collider`. Narrower x0/x1 leave lanes without floor (falling); `planks` makes it a wooden bridge. |
| `ramp(y0, y1, y2, rise)` | A ramp up to `rise` at y1, a deck to y2, then a drop (a convex `Collider`). |
| `socket(kind, (x, y[, z]), chance=1, any_lane=False, options="")` | A `Socket` empty (section 3). |
| `instance(prefab, (x, y), rotation=0)` | A collection instance of another prefab (`"CoinArc"`, `"Hurdle"`): a full instance at runtime. |
| `lamp(side, y)`, `pillar(y)`, `warn(y0, y1, x0, x1)`, `rock((x, y, z), size, seed)` | A lamp post, a support under the road, a glowing strip before a drop or a gap, a floating island beside the road. |
| `attach(obj, (x, y[, z]), rotation=0)` | Puts any object in the chunk, parented to its root. |

- **From kits or procedural builders**: `gs.kits.create` in the same `run_python`, then `c.attach(obj, ...)` as above. Objects made in their own call (`create_from_kit`, `create_procedural`) join later with `run_python`: link them into the chunk's collection only, set `obj.parent = D.objects["ChunkPillars"]`, and give them a location relative to the root. Everything must be in the chunk's collection and parented to its root, or the export drops it.
- **Solid parts** (floors, walls the runner should stand on) need a `Collider`. **Obstacles** placed in the chunk itself rather than through a socket need `Obstacle {debris}`, no `Collider`.
- **Never** add chunk parts to the `main` scene: prefab objects in the level's scene are live entities at the origin.

## 3. Sockets

A `Socket` empty is a spawn point inside the chunk. When a copy of the chunk spawns, each socket rolls its `chance` and then spawns one prefab at its world transform, attached to the chunk:

| Field | Meaning |
|---|---|
| `kind` | `obstacle` (a cube), `coins` (a sphere) or `pickup` (a cone). Without `options` it picks from the `Track` empty's list: `obstacles` "Hurdle:3, Crates:2, Boulder:2", `coins` "CoinLine:3, CoinArc:1", `pickups` "Magnet, Shield". |
| `options` | Its own list, `"Name:weight, ..."`: `"Hurdle"` for a must-jump, `"Sweeper"` for the rolling drum, `"CoinArc"` over a gap. |
| `anyLane` | A random lane instead of the socket's own x. Two `anyLane` obstacles in one row can block every lane: keep them in different rows. |
| `chance` | 0 to 1. Pickups about 0.15, extra obstacles 0.6 to 0.8. |

A `CoinLine` runs 8 m forward from its socket, a `CoinArc` 8 m along a jump's arc. Put coin sockets where the runner should go: they show the way (the slalom's coins sit in the free lanes). Obstacles are skipped in the first `Track.warmup` metres of a run.

## 4. Weight, unlock distance, export

The `Chunk` helper sets `TrackChunk`. To change it later: `set_component {"objects": "ChunkPillars", "component": "TrackChunk", "values": {"weight": 1, "minDistance": 500}}`. Then `export_scene` and `scene_summary`: `prefabs` must list `ChunkPillars`. `render_views {"target": "collection:ChunkPillars", "views": ["top", "iso"]}` shows it from above: the sockets in their lanes, the floors closed where they should be.

## 5. Playtest with a fixed seed

Make it come up at once, and make the runner survive the other chunks, for this test only:

1. `set_component {"objects": "ChunkPillars", "component": "TrackChunk", "values": {"weight": 50, "minDistance": 0}}`.
2. `set_component {"objects": "Runner", "component": "Runner", "values": {"shield": 999}}`: obstacles break and gaps bounce the runner back up.
3. `playtest` with a seed, so the run repeats exactly:

   ```json
   playtest {"seed": 3, "duration": 20, "inputs": [{"at": 0, "key": "Enter"}], "screenshots": 6,
             "track": ["Player"], "state": {"component": "TrackChunk"}}
   ```

   The chunk must appear in `final.entities` and in the screenshots once the runner passes `Track.warmup` (60 m, about 7 s), and `game_logs` must have no "cannot spawn" warnings. Look at the screenshots: the sockets' contents in the right lanes, nothing floating or sunk.
4. Set `shield` back to 0 and play it for real: inputs that pass the chunk (`{"at": 8.2, "key": "KeyD"}`, `{"at": 9, "key": "Space"}`). The same seed gives the same track, so adjust the times until the run passes, and check the result's logs for "Crashed into" or "Fell off". If a fair line through it is hard to find, the chunk is too hard: widen the spacing or lower a `chance`.
5. Restore the real `weight` and `minDistance`, then one normal playtest of about 30 s.

## 6. Keep it

`save`. Add the same lines to `tools/build_level.py` as a `chunk_pillars(gs)` function and call it in `build()` after the others, so a rebuild keeps the chunk, or tell the user the script is out of date.

## Common failures

| Symptom | Fix |
|---|---|
| The chunk never appears | Its root has no `TrackChunk`, `weight` is 0, `minDistance` is beyond the run, or the collection is not a prefab (`set_prefab {"collection": "ChunkPillars"}`). `scene_summary` lists the prefabs. |
| Parts of it are missing in the game | They are not in the chunk's collection, or not parented to its root. The export log says "Prefab 'ChunkPillars': not exported" with their names. |
| A piece of it stands at the level's origin | It is in the `main` scene. Move it to the chunk's collection in the `Chunks` scene. |
| The runner falls through the floor | The floor has no `Collider`, or its top is not at z = 0 where it meets the next chunk. |
| The runner bumps at the seam with the next chunk | The floor ends before `length`, or `length` is longer than the floor: they must match. |
| An obstacle spawns inside a wall or over a gap | The socket is in the wrong place, or `anyLane` sends it where the floor is missing (the narrow bridge's side lanes). Use a fixed lane there. |
| A row blocks every lane | Two `anyLane` obstacle sockets in the same row, or a fixed row of three. Spread them out. |
| "cannot spawn" in `game_logs` | A name in `options` or in the `Track` lists is not a prefab: compare it with `scene_summary`'s `prefabs`. |
