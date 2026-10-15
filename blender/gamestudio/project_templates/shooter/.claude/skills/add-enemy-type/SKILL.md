---
name: add-enemy-type
description: Add a new enemy type to this arena shooter. Designs its role and stats, builds it as a prefab in the Prefabs scene, adds it to the wave roster, gives it its own behaviour if it needs one, then playtests it until it spawns, chases, hurts and dies as designed. Use when the user wants a new enemy, a boss, a tank, a fast or ranged enemy, more variety in the waves, or "make it harder".
argument-hint: "[the enemy: role, look, behaviour]"
---

# Add an enemy type

Every enemy is a **prefab**: a collection marked `gs_prefab` in the `Prefabs` scene of `scenes/main.blend`, with one root object that carries `Enemy`, `Health`, a kinematic `RigidBody` and a capsule `Collider`. `WaveSystem` spawns prefabs by name with `ctx.spawn`, and `EnemySystem`, `ProjectileSystem` and `HealthSystem` handle anything with those components. A new type is often just a new prefab with new numbers.

## 1. Design it in four lines

- **Role**: what it changes for the player. A tank soaks bullets, a runner punishes standing still, a swarm overwhelms one direction.
- **Stats**, compared with the existing ones:

  | | Grunt | Runner |
  |---|---|---|
  | `Enemy.speed` (m/s) | 3.2 | 5.6 |
  | `Health.max` (hits) | 3 | 1 |
  | `Enemy.damage` / `attackInterval` | 10 / 0.8 s | 5 / 0.6 s |
  | `Enemy.reach` (m) | 1.3 | 1.1 |
  | `Enemy.points` | 10 | 15 |

  The player has 100 HP and fires 8 shots a second, at one hit point each.
- **Look**: height, girth and colour. Keep it readable from above: a distinct colour and size.
- **First wave**: when it joins (`Name@N` in `WaveSpawner.enemies`).

## 2. Build the prefab

`checkpoint` with the label "before <name>", then `run_python`. The level script's `enemy()` helper makes the prefab collection and the body with its physics:

```python
import pathlib, runpy
lvl = runpy.run_path(str(pathlib.Path(bpy.data.filepath).parents[1] / "tools" / "build_level.py"))
brute = lvl["enemy"](gs, D.scenes["Prefabs"].collection, "Brute", height=2.3, girth=0.26, color="#7048E8")
add = gs.core.components.add_component
add(brute, "Enemy", {"speed": 2.2, "damage": 25, "reach": 1.6, "attackInterval": 1.2, "points": 40})
add(brute, "Health", {"max": 12})
result = brute.name
```

- Without the helper, use `create_from_kit` kind `character` in a new collection, `gs.core.export.set_prefab(collection)`, and add `RigidBody {type: kinematic}` and `Collider {shape: capsule}` yourself. Keep the collection in the `Prefabs` scene, never in the level: prefab objects in the level's scene become live enemies at the origin.
- Extra parts (armour, a weapon) go **inside the prefab collection, parented to the root**. Child entities don't change the collider.
- Keep the root's origin at its feet (the `character` kit already does this), because spawn points are at floor level.

## 3. Add it to the waves

`set_component` on `Waves`, component `WaveSpawner`, values `{"enemies": "Grunt, Runner@3, Brute@5"}`. Each spawn picks at random among the names unlocked in that wave. To test the new type alone, set `"enemies": "Brute"` for a moment and restore it afterwards.

## 4. Its own behaviour (only if numbers aren't enough)

A charger, a shooter or a splitter needs a component and a system:

1. `src/components/charger.ts`: `Charger { dashSpeed, dashEvery, dashTime }`, data only.
2. `src/systems/chargers.ts`: `defineSystem({ name: 'ChargerSystem', query: [Enemy, Charger], ... })`. Change what `EnemySystem` uses rather than moving the body a second time. For example, raise `enemy.get(Enemy).speed` during a dash and restore it after, with `ctx.after(dashTime, ...)`. Something that fires uses a projectile prefab and `ctx.spawn`, like `ShootingSystem`. Enemy bullets need their own component, so `ProjectileSystem` doesn't let them hurt enemies.
3. Add the component to the prefab root with `add_component`. `npm run typecheck` must pass.

Read `node_modules/@gamestudio/engine/API.md` (§17 prefabs, §18 timers) for every API you use.

## 5. Verify

1. `export_scene`, then `scene_summary`: its `prefabs` must list the new prefab under its plain name. `Name [actors.blend]` means another prefab takes the name (see Common failures).
2. `playtest` with the roster set to the new type alone. Use about 12 s with only `{"at": 0, "key": "Enter"}` (Play, on the title), `screenshots: 4` and `state: {"component": "Enemy"}`. The new type must spawn at the pads, walk to the player and hurt it: the HUD's health drops (`final.ui.texts["HUD/Health"]`, the player's `Health.current`). Read `game_logs`: a warning "WaveSpawner: cannot spawn" means the name in the roster does not match the prefab.
3. `playtest` again, aiming at it with the mouse held down: `{"at": 3, "mouse": "aim", "target": "<Name>*", "until": 12}` and `{"at": 3, "mouse": "down", "hold": 9}`. The aim follows the nearest one every step through the game camera. Its health bar must drop, and "<Name> defeated" must appear in the logs with the points added; `track: ["<Name>*"]` gives each copy's `destroyed_at_s`.
4. Restore the real roster and run one normal playtest of waves 1–2.
5. `save`. Add the same lines to `tools/build_level.py` (after the `runner` lines) so a rebuild keeps the new type, or tell the user the script is out of date.

## Common failures

| Symptom | Fix |
|---|---|
| It never spawns | The roster name differs from the collection name, or `@N` is later than the waves played. Compare the roster with the prefab collections (step 5.1). |
| `cannot spawn "X": Unknown prefab "X"`, and the list shows `X [actors.blend]` | The enemy moved to an asset library (`asset_add {"spawn_only": true}`) while another prefab in the level is still named `X`. `spawn_only` removes an empty source collection itself and warns about any other: rename that one or `set_prefab {"collection": "X", "enabled": false}`, then export again. |
| An enemy stands at the origin from the start | The prefab collection is in the level's scene. Move it to the `Prefabs` scene. |
| It floats or is half sunk | The root's origin is not at its feet. Rebuild it with the `character` kit, or move its mesh so its feet are at z = 0. |
| Bullets pass through it | It has no `Collider`, or the collider is on a child. Put the capsule on the root. |
| It slides through walls | It has a dynamic or missing `RigidBody`. Enemies are `kinematic` and are moved by `EnemySystem`. |
| It gets stuck on cover | It is wider than the navmesh's agent: raise `NavMesh.agentRadius` (on `ArenaFloor`) to its capsule radius, or make it slimmer. Without a `NavAgent` (an old prefab) `EnemySystem` adds one. `ctx.nav.debug = true` draws the navmesh and each path in screenshots. |
| The game is suddenly too hard | Count the damage per second near the player (damage / attackInterval × how many). Raise `@N` or lower `perWave`. |
