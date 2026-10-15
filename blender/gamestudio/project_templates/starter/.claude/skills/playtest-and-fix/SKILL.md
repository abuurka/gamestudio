---
name: playtest-and-fix
description: Playtest the GameStudio game with scripted inputs, read the screenshots, state and logs, find the root cause of anything wrong, fix it in Blender or TypeScript, and re-run the same playtest to prove the fix. Use after any gameplay or level change, when the user reports a bug ("X doesn't work", "I fall through...", "it crashes"), and before saying a feature is done.
argument-hint: "[what to test or the bug]"
---

# Playtest and fix

Never call something fixed without a playtest that shows it working. Use the same inputs before and after the fix.

## 1. Write the test

- **Expected:** one sentence. For example, "walking forward 3 s from the start and jumping lands on the platform; Coins goes 0 → 1".
- **Inputs:** a timeline in game seconds, with `KeyboardEvent.code` names:
  - `KeyW`, `KeyA`, `KeyS`, `KeyD` (or the arrow keys) move relative to the camera;
  - `Space` jumps, and holding it keeps jumping;
  - `KeyE` and `KeyR` are typical interact and restart keys;
  - mouse buttons and movement as the game needs.
- **Probes:** which entities to `track` (the player, the object under test, `"Enemy*"` for spawned copies), `sample_every` when positions over time matter, and screenshots before, at and after the moment that must be seen.

## 2. Run it

1. `playtest` with the inputs and probes. It exports the level open in Blender and plays that level, so Blender edits are included; pass `export: false` when only code changed, and `scene: "main"` to play the whole game from the start scene (and its title) while editing another level. Every run opens a fresh page and starts from the exported scene. It returns screenshots, state and logs.
2. Read everything:
   - **Screenshots:** look at them. Is the player where you expected? Is the feedback visible?
   - **State:** `tracked` and `final` positions are runtime Y-up, so y is the height. Check the component values.
   - **Logs:** errors come with a file and line. Warnings matter too.

For frame-level detail, use `play`, `pause`, `step` (a few fixed steps) and `game_state` / `game_screenshot`, then `stop`. `game_stats` warns when it measured a title screen or a paused game: pass `keys: ["Enter"]`. Its draw calls include the post-effect passes.

A material or lighting bug needs no run: check materials in the level with `game_screenshot {"mode": "scene", "view": {"from": [x, y, z], "at": [x, y, z]}}` from a viewpoint (Blender coordinates; `{"camera": "Camera"}` looks through a camera). It is the game's renderer with its shaders, without a title screen, HUD or anything running.

### Timing

- `playtest` runs on game time: the engine steps its 60 Hz simulation as fast as it can and draws only the screenshots. The same script gives the same result on every run, at any `size`. All times are **game seconds** (`"at": 1.5, "hold": 2`, `duration`, `screenshots`). A 20 s route takes 1–3 s of wall time.
- **Write the critical path once, from the real start.** `{"at": 0, "key": "KeyW", "hold": 1}` moves `speed` metres on flat ground (5 m at the default). Uphill is slower: about 4.1 m/s instead of 4.9 on a 20° slope. Diagonals (`KeyW` + `KeyD`) are normalised, about 3.5 m/s along each axis. If a jump falls short, the level is wrong, not the test.
- **A tap jumps:** `{"at": 1, "key": "Space"}` (down, then up one step later). `hold` keeps a key down.
- **See paths without images:** `"track": ["Player", "<Prefab>*"], "sample_every": 0.5, "screenshots": 0`. Each tracked entity gets `samples: [[t, x, y, z], ...]`. A name with `*` is a pattern: it follows every copy, spawned ones included, each with its `id`, `spawned_at_s` and `destroyed_at_s`. A plain name follows only one copy. `track_max` caps the copies listed. Read the samples to plan a route past hazards and to fix the first leg that ends in the wrong place. Samples are capped (200 per entity, 1000 in all), so track only what you need and keep `sample_every` coarse on long runs. Rows where nothing moved (under 1 cm) are dropped: a still entity shows only its first and last rows.
- **Screenshots never disturb the run,** and each lists the tracked positions at that time. Take them where you need to *see* something (feedback, a menu, the look), not as position probes.
- **Legs along one axis** (`KeyW`, then `KeyD`) are easier to correct than diagonals. Keys move relative to the camera's current yaw, so behind a `FollowCamera` with `smoothing` a long sideways hold drifts a little (about 0.7 m/s at smoothing 6 and speed 7): read the samples and correct the next leg.
- **Test a late segment with `start`,** not by moving objects: `{"start": {"position": [x, y, z]}}` in runtime space, or with `"space": "blender"`. It applies to this run only (`yaw_deg` turns the entity, `name` moves another one, a list moves several). It cannot move an object whose clip keys its location (a bobbing pickup snaps back).
- **Read `tracked`:** `min`/`max` (jump height, falls), `path_m`, `destroyed_at_s` for pickups, and `changes`: every component field that changed, with `first_s`/`last_s`. A fall and respawn shows in `min` and in a counter such as `Respawn.falls`. `logs[].t` is the game time of each line. Only component fields show in `changes`: keep values you want to test (hit points, ammo, score) in a component, not in a module variable or a `WeakMap`.
- **Menus and levels:** press menu buttons with `{"key": "Enter"}` (arrows move the focus) or click at `final.ui.buttons[i].x`/`y` (canvas px of the run's `size`, given as `final.ui.size`, not the 1920×1080 design). A title screen pauses the game until it is closed, so start the script with `{"at": 0, "key": "Enter"}` (a click at 0 works too): every game made from a GameStudio template opens on one (`screen: "Title"` for a screen made in Blender, `"title"` for a code-only one, in the first screenshot's state). Escape and P open the pause menu (a `pause_menu` screen or `ctx.ui.pauseMenu()`), so keep them out of gameplay inputs. The result's `scene` and `screen` say which level and menu the run ended in (and scene_changes how often the game changed level), and `final.ui` gives the open menu with its buttons (sliders and toggles too, with `kind` and `value`), the open screens (`open`; a closed menu stays listed for its 0.25 s close animation, 15 steps, while `screen` is null at once), what each text of a Blender screen shows (`texts`: `{"HUD/Score": "Score 3"}`) and which elements code hides (`hidden`; icons over their count, such as lost hearts, are not listed: look at the screenshot), so most of the HUD can be checked without one. A screen that looks wrong: `game_screenshot {"ui": "<Screen>"}` and the `design-ui` skill.
- **Games with several levels:** a playtest plays the level open in Blender (`?scene=`), so open `level2` to test it on its own, or pass `scene: "main"` to play through from the start. Run `export_scene {"all": true}` after editing other levels: a level that was never exported is refused at once, and a warning says when the played level isn't the one just exported.
- **Long runs are cheap,** up to 120 s. Run lose tests past the time limit; shorten only limits over 120 s (`set_component`, then set it back).
- **Randomness:** `seed` (default 1) seeds `ctx.random` and `Math.random`, so a script repeats exactly. Gameplay code must use `ctx.random` (`range`, `int`, `pick`, `chance`): three.js and audio draw from `Math.random`, so with `Math.random` any change to the inputs moves later hazards. Draw a wave's or a spawner's choices when it starts, not one at a time, so shots and drops (which also draw) don't move the spawns that follow. Each level load starts a new `ctx.random` stream from the seed, and particles (`ctx.vfx`) draw from streams of their own, so effects never shift gameplay randomness. Give each kind of randomness a named stream, `ctx.random.stream('spawns')` (seeded from the seed and the name), so shot spread never moves where enemies appear.
- **Scripted fights:** enemies that steer around each other diverge from any change to the inputs, even the player turning to aim, so positions from one run predict the next only until the first difference. Plan a fight from a run whose inputs are the same up to the fight, re-run, plan again from the new run, and once a segment works, freeze its inputs and plan the next one after it. For cursor-aimed fights, hold `{"at": t0, "mouse": "down", "hold": d}` and aim with `{"at": t0, "mouse": "aim", "target": "Enemy*", "pick": "nearest", "until": t1}` instead of probing positions; the result's `aims` lists the steps that had a target. `{"mouse": "move", "world": [x, y, z]}` points at a world position.
- **Act on `warnings`** (scene reloaded mid-run, game paused). Use `realtime: true` only for frame-rate checks; its results vary.

## 3. Find the root cause

| Evidence | Likely cause | Where to fix |
|---|---|---|
| An error in `game_logs` with `src/...:line` | A bug in a system, or an API that doesn't exist | The TypeScript file. Check `API.md` |
| No error, and the system's `ctx.log` never prints | No entity matches the `query`, or the file isn't loaded | `get_object` to check components; the file path |
| A component value is wrong (`tracked` `changes`, `final`) | A wrong default or a missing `values` entry in Blender | `set_component` / the component default |
| An entity field is empty (`FollowCamera.target: null`) | Its object was deleted or renamed; `validate_assets {}` reports it | `set_component` with the new name |
| The player falls through or gets stuck | Missing or wrong `Collider` shape (`auto` box on stairs or buildings), or a spawn inside geometry | `add_component` / `set_component`, `set_transform` |
| A jump falls short | The gap exceeds the jump distance (default 3 m) | Move the platforms (see `blockout-level` metrics) |
| A trigger never fires for the player | The player makes no contact events (2 cm skin) | `Collider {sensor: true}` on the trigger |
| It works once, then breaks after a restart | State kept in module variables; HUD not cleared | Reset in `start(ctx)` |
| A text of a Blender screen shows an empty value (`"Score "` in `final.ui.texts`) | The `ctx.store` key its binding names (`{score}`) is not set yet, or is misspelt | `ctx.store.score ??= 0` in `start`; `ui_list` shows the text's body |
| Something moves in the wrong direction | Y-up vs Z-up confusion | Blender +Z = runtime +Y; Blender −Y = runtime +Z |
| A dynamic body teleports back | Code writes `object3d` on a dynamic body | `ctx.physics.teleport` or impulses |
| After a respawn or checkpoint the camera sweeps across the level or through the player | A camera cuts by itself only when its target jumps more than 5 m; it smooths over shorter jumps | `snapCamera(player)` after `ctx.physics.teleport` (`add-mechanic`, PATTERNS.md, Fall respawn) |
| A material draws its plain Blender look in the game, with a `game_logs` warning naming it | No `src/shaders` file or library shader defines its game shader, the shader's code threw, or no mesh draws a material of that name | The shader file, or `set_material_shader` with the right name (`list_component_types` lists them) |
| A shader effect that code starts never shows | Trigger fields (`hitAt`, `startAt`, `pingAt`) stay off until code sets them to `ctx.time`; a `strength` or `progress` set to 0 in Blender stays 0 until code raises it. `ctx.shaders.set` ignores an unknown field, a material name no mesh draws and an entity without shaded materials, with one warning per load in `game_logs` | The `ctx.shaders.set` call and its field names (`list_component_types {"type": "<Shader>"}`); a screenshot just after the trigger |
| A library shader looks wrong, not missing | UV-driven shaders (`Flow`, `ShorelineFoam`, `FlagWave`, `PortalVortex`) need the unwrap their description gives; a flag needs a subdivided plane | The mesh's UVs or subdivision in Blender |
| A material effect works on the level but not on spawned copies or after a level change | Code finds the material by name in a system's `start` | A game shader on the material (`set_material_shader`) |
| The whole game is darker, brighter or tinted compared with Blender | The scene's Color Management (view transform, look, exposure): the game follows it | The scene's exposure in Blender, not code; see `export_scene` warnings |

Change one thing at a time. Before a risky change, `checkpoint`.

## 4. Fix, then prove it

1. Make the smallest fix that addresses the cause.
2. `npm run typecheck` (for code fixes).
3. Re-run the **same** `playtest`. Runs repeat exactly, so every difference from the failing run comes from your fix.
4. Run the critical path again: start → goal, plus the other mechanics, to catch regressions.
5. `save`.

## Report

Tell the user what was wrong (the cause, not the symptom), what you changed (files and objects), and the evidence: the playtest result, a screenshot description and a clean log.

## Common failures

| Symptom | Fix |
|---|---|
| `playtest` shows a stale scene | You passed `export: false` after Blender edits. A new object or a geometry change needs a re-export. |
| An input is ignored, or a jump never happens | The player wasn't grounded yet (for example a `start` above the ground): jump a little later, from about 0.3 s. |
| `hold: 2000` is refused | Times are game seconds: `hold: 2`. |
| The screenshots are black or show the wrong view | No active scene camera, or the camera is inside geometry. Check `scene.camera` and `FollowCamera.offset`. |
| Results vary between runs | Code uses `performance.now()`, `Date.now()` or `setTimeout` instead of `ctx.dt`, `ctx.time` or `ctx.after`, or `realtime: true`. (`ctx.random` and `Math.random` are seeded.) |
| Hazards move to new places after a change to the route | Gameplay randomness draws from `Math.random`, which three.js and audio also use, or shares one stream with shots. Use `ctx.random`, with a `ctx.random.stream(name)` per kind (PATTERNS.md, Spawner). |
| `scene` is not the level you expected, or another level shows old content | A playtest plays the level open in Blender (`scene: "main"` starts from the start scene) and exports only that one: `export_scene {"all": true}` after editing others. |
| The run stops at the title screen (`screen: "Title"` or `"title"`, many paused steps) | Close the menu first: `{"at": 0, "key": "Enter"}`. |
| `tracked` says `"found": false` | No entity has that name. Check the twin, or track by `gs_id`. |
