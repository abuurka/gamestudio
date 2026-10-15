---
name: blockout-level
description: Block out a playable level in Blender with GameStudio kits. Covers ground, bounds, platforms, stairs and building shells, colliders, the player start, lighting and a camera, sized to the character's movement. Then checks it by looking and by walking it in a playtest. Use when the user asks for a new level, area, arena or layout, or to rough out or greybox a space.
argument-hint: "[level idea or scene name]"
---

# Block out a level

A blockout answers "is this space fun to move through?" Make it quick, to scale and fully collidable. Detail comes later (`stylize-scene`).

## Player metrics (CharacterController defaults: speed 5, jumpSpeed 6, gravity 20)

| Metric | Value |
|---|---|
| Player | about 1.8 m tall and 0.7 m wide (the `character` kit) |
| Jump height | jumpSpeed² / (2 × gravity) = **0.9 m** (1.2 m with jumpSpeed 7) |
| Jump distance on flat ground | speed × 2 × jumpSpeed / gravity = **3 m**. Design gaps of 2 m or less. |
| Step up without jumping | **0.35 m** at most |
| Walkable slope | up to **50°** |
| Comfortable corridor | 2–3 m wide. Doors are at least 1.2 m wide and 2.2 m tall. |
| Launch (bounce pad: `characterMotion(player).verticalVelocity = v`) | Rises v² / (2 × gravity) above the pad, with the controller's gravity of 20 m/s², not the scene's 9.81. 15 m/s gives **5.6 m**. With W held the player keeps moving at full speed in the air: a 15 m/s launch carries it about 9 m forward over flat ground. |
| Something to jump onto | Its top at most 1 m above the floor (jump height minus a margin). |

Read the real values from the player's `CharacterController` if they were changed.

A pad right below a high ledge works well: the ledge wall stops the forward motion until the player is above it, and then the player lands 3–4 m in. Make the landing at least 5 m deep, or the player overshoots it.

## Steps

1. **Sketch first.** Write the layout as a short list of areas, each with a centre, a size and a height in metres (Blender: Z up, front = −Y). Mark the critical path from the start to the goal. Use one `.blend` per level: `open_scene` with `scenes/<level>.blend` (`create: true` for a new one).
2. `checkpoint` with the label "before blockout". Keep its id for `render_views {"compare": "<id>"}`.
3. **Ground**, in the `Level` collection:
   - Flat arena: the `platform` kit (say 30 × 30 × 0.5) at z = −0.5, so that its top is at z = 0.
   - Outdoor: the `terrain` kit, with `flatten_center` for the play area. Its origin is at ground level. `shape` is `square`, `round` (a disc whose hills fall off to the rim) or `island` (a round top over a tapered rocky underside, for floating islands). The largest `size` is 200 m.
   - A hill to climb or a long ramp: the kit has no slope option. Build a `square` terrain with low `height` (gentle bumps), then raise its top vertices with a height function in `run_python` (`make-prop`, MODELING.md, "A hill or a ramp from the terrain kit"). Under 25° the player walks it; boulders roll down it.
   - Give it a `Collider`: `{shape: "mesh"}` for terrain.
4. **Bounds:** walls (tall, thin `platform`s or `building` shells), `fence` segments, or a cliff (a `rock` up to 40 m). A gate or door that guards the goal needs walls on both sides, or the player walks around it. If the player can fall off, there must be a respawn: a `KillZone` sensor box under the level if the project has one, or a fall check (`add-mechanic`, PATTERNS.md, Fall respawn).
5. **Critical path:** platforms, `stairs` (`step_height` 0.35 or less), ramps (a rotated `platform`, slope 50° or less) and `building` shells (they have door and window openings). Space jumps using the metrics above.
   - Build the pieces in one call: `create_from_kit {"items": [{"kind": "platform", "params": {...}, "location": [...], "name": "Platform_Upper"}, ...], "collection": "Level"}`.
   - Every walkable piece gets a `Collider`: `auto`/`box` for blocks; `mesh` for stairs, terrain and buildings, because a box would seal the door; `convex` for rocks that move. `add_component` takes a list of `objects`.
6. **Props for cover and readability:** use `make-prop` (rocks, crates, trees). Give pushable props `RigidBody {type: "dynamic"}` and a `Collider`. Rocks take `Collider {shape: "convex"}`. Leave trees without a collider and off the path: every shape is sized from the whole tree, so it would block the area under the canopy.
   - **On uneven ground,** give only x and y and snap: `set_transform {"items": [{"name": "Rock_1", "location": [3, 2, 0]}, ...], "snap_to_ground": true}`. Each object's underside then rests on the surface under it, and by default (`align: "lowest"`) nothing floats. Snap again after any terrain change.
   - On a slope, add `tilt: true` so a prop lies flush with the ground. Use `align: "highest"` for a player start or anything that must not sink (over a ledge, a step). Each result reports `float_m` and `sink_m`, the gap left, and a `hint` when the ground under the object is uneven.
   - `ground_height {"points": [[x, y], ...]}` gives the heights before you place things.
7. **Start, light, camera:**
   - Put the player (or an empty named `PlayerStart`) on the floor at the start, with `snap_to_ground` and `align: "highest"`, so the capsule starts above the ground, not in it.
   - Keep one sun. If there is none, add it with `run_python`: `bpy.data.lights.new(name, "SUN")`, energy about 3–4, angled about 45°.
   - Keep a camera with `FollowCamera` (see `new-game-from-brief`).
8. **Name and organize:**
   - Meaningful unique names (`Platform_Upper`, `Stairs_Tower`).
   - Collections: `Level`, `Props`, `Pickups`, `Actors`, `Lights`.
   - Snap heights so that tops land on round numbers.

## Verification gates

1. `render_views` with `views: ["top", "iso"]` (the scene is the default target). Look at it. Is the path readable? Are the gaps sane next to the 1.8 m person? Nothing floats or intersects by accident. `names: true` labels the pieces; `focus: true` with a `target` shows one area in context.
2. `validate_assets {}` reports no errors. Fix scale and rotation warnings on objects you modelled yourself.
3. `playtest` along the critical path. For example: hold `KeyW` for 2 s, press `Space` at the gap, and take screenshots at the key points. Runs are in game time and repeat exactly, so the metrics above hold. `tracked` must show the player where you expected; `start` tests a late segment on its own. If a jump fails, move the platforms; don't make the player stronger.
4. `save`.

## More than one level

- One level per file: `scenes/level2.blend` (`open_scene {"path": "scenes/level2.blend", "create": true}`) loads in the game as `'level2'`. `gamestudio.json` → `startScene` is the first.
- Give every level its own `Player`, camera and HUD objects, so each also plays on its own. The game moves on with `ctx.loadScene('level2', { keep: ['Player'] })`: the kept player takes the place of level 2's `Player` (its position, and the entity fields that pointed at it), with its components. Code that remembers `ctx.time` per entity must reset in `start`: `ctx.time` starts again at 0 in every level.
- What a level's code spawns must be spawnable in that level: keep bullets, enemies and drops in an asset library and `asset_spawnable` them in each level (`make-prop`, ASSET-LIBRARY.md).
- `export_scene {"all": true}` exports every level. Do it before any playtest that changes level: a playtest exports only the open file.
- A dev-only key that jumps to a level helps test it (`playtest-and-fix`, Timing).

## Reusable assets

Assets used in several levels, or many times in one, belong in an asset library (`assets/<library>.blend`) and are placed with `asset_place`. See `../make-prop/ASSET-LIBRARY.md`.

## Common failures

| Symptom | Fix |
|---|---|
| The player walks through a building wall, or is blocked at its door | Use `Collider {shape: "mesh"}` on buildings and stairs. `auto` makes one box. |
| The player is stuck at the bottom of the stairs | `step_height` is above 0.35 m. Rebuild the stairs (`regenerate_kit {"object": "Stairs_1", "params": {"step_height": 0.3}}`) or use a ramp. |
| Props float or sink after a terrain change | Snap them again: `set_transform {"items": [...], "snap_to_ground": true}`, with `tilt: true` on slopes. |
| The player jitters on terrain or falls through it | Terrain needs a `mesh` collider. Keep the `resolution` moderate (24–48). |
| Everything is dark or flat in the game | Add a sun. One sun casts the shadows; the engine adds a fill light. |
| Platforms end up in the wrong place | Kit origins are at the bottom centre, so z is the bottom of the object, not its centre. |
