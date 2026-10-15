---
name: new-game-from-brief
description: Turn a short game idea into a playable first version in this GameStudio project. Writes a one-page plan, blocks out a level, adds a controllable player and camera, builds one core mechanic and a win or lose condition, then playtests it. Use when the user describes a game they want ("make a game where...", "prototype a...", "start my game") or asks for a first playable.
argument-hint: "[game idea]"
---

# New game from a brief

The goal is the smallest version that is fun to try, playable in one session. It is not a finished game.

Brief: $ARGUMENTS (if this is empty, use the user's message)

## 1. Plan (5 minutes, written down)

Write `docs/DESIGN.md`, one page at most:

- **Pitch:** one sentence.
- **Player verbs:** move, jump, plus at most one new verb (shoot, push, dash, grab).
- **Core loop:** what the player does every few seconds, and why.
- **Win / lose:** for example, collect all N coins / fall off or touch lava.
- **Camera and controls:** third-person `FollowCamera` with WASD and Space unless the brief says otherwise. `CharacterController` already handles them.
- **Level sketch:** areas with rough sizes in metres (see the `blockout-level` skill for player metrics).
- **Entities:** each entity with its components. Mark which components are built-in and which are new.
- **Systems** to write.
- **Look:** two or three palette presets and the kits to use.

Cut everything that the first playable doesn't need. If the brief is ambiguous in a way that changes the game (genre, camera, win condition), ask one question. Otherwise pick sensible defaults and state them in the plan.

## 2. Check the session

1. `status`: Blender is connected and the project root is right. If Blender is not running, `launch_blender` (`background: true` when nobody needs to watch). If no level is open, `open_scene` with `scenes/main.blend`.
2. Read the engine API once: `engine_docs`, or the engine's `API.md` (its path is in AGENTS.md).
3. `list_component_types` and `list_kits`. `list_kits {"kind": "terrain"}` gives one kit's full schema with its ranges; check them before you size the level.
4. Read the scene twin. A new project's scene holds:
   - `Ground`, a 30 m box with a `Collider`;
   - `Player`, the `character` kit with the three player components;
   - `Collectible1`–`Collectible3`, with the starter's `Collectible` component;
   - `Camera` with `FollowCamera {target: Player}`, and a `Sun` made by the `sky` kit (its gradient sky shows in the game);
   - `Music`, an `AudioSource` looping `assets/music/theme.ogg` on the music channel.

   Its code: `src/systems/collect.ts` (the score in `ctx.store.score`, a `pickup` burst, `ctx.ui.open('Win')` when no pickups are left). Its screens are made in Blender, in `ui/ui.blend`: `Title` (a turning 3D gem, the game's name, Play, Options), `Pause`, `Options` (music and sound volumes, the language), `Win` and `HUD` (`Score {score}`), with French in `ui/locales/fr.json`. Replace what the brief changes; keep the camera, the sun, the music and the screens (step 6).
5. `checkpoint` with the label "before first playable". Keep its id: `render_views {"compare": "<id>"}` shows your progress later.

## 3. Build

1. **Level:** follow the `blockout-level` skill: ground, bounds, the critical path, the player start, a sun, a camera.
2. **Player:** the starter `Player` already moves. Put it on the start with `set_transform {"name": "Player", "location": [x, y, 0], "snap_to_ground": true, "align": "highest"}`, and change its look with `regenerate_kit` (`height`, `color`).
   - The kit's origin is at the feet: in code, `player.object3d.position` is the feet and the body centre is about 0.9 m higher. Measure pickup and trigger distances from the centre. The starter's `collect.ts` does.
   - For an animated hero, replace it with the `character_rigged` kit plus the built-in `Locomotion` (Idle, Walk, Run, Jump and Fall with no code). Its capsule fits the skinned mesh, so it stands on the ground like the plain kit. `animate-character`, section 4, has the swap in five calls. A waving or idling NPC is the same kit with `Animator`.
3. **Camera:** the starter camera follows `Player`. `FollowCamera.offset` is in runtime Y-up space: `[0, 4, 8]` is 4 m up and 8 m behind. If you replace the player, `delete_objects` lists `FollowCamera.target` under `cleared_references`: set it again.
4. **Core mechanic:** follow the `add-mechanic` skill: component, system, attach, playtest. Without a running dev server, Blender only sees a new component after `npm run registry`.
5. **Win/lose and HUD:** one small system keeps the progress in `ctx.store` (`ctx.store.gems`), which a text of the `HUD` screen shows (`Gems {gems} / {total}`, step 6), and opens the win screen. The lose condition sends the player back to the start (falling below y = −20, or touching a `KillZone` sensor). Set the store keys in `start(ctx)`.
   - Keep the round's state in a component on the Player, for example `GemHunt { gems, total, elapsed, state: playing | won | lost }`, instead of module variables. It survives hot reloads (module variables don't), `game_state` and `playtest` show it, and every system finds it with `ctx.world.query(CharacterController, GemHunt)[0]`. In `start`, recount with `total = gems + remaining pickups`. That holds after a scene reset and after a hot reload.
   - Restart key: `if (ctx.input.keyDown('KeyR')) void ctx.restart()`: the start scene again with the authored component values and an empty `ctx.store`. (`ctx.game.loadScene(ctx.game.scene.name)` reloads only the level and keeps `ctx.store`.)
6. **Screens** are made in Blender, in `ui/ui.blend` (the `design-ui` skill; engine `API.md` §22). `ui_open`, then `ui_list` shows the starter's:
   - `Title` (opens on the first start, once per page load): give `GameTitle`, `Subtitle` and `Hint` the brief's words with `ui_set {"canvas": "Title", "name": "Subtitle", "body": "..."}`. `TitleGem` turns the starter's gem: show the game's own hero or prop instead by giving it a `kit`, `asset` or `source` again (the level's player, linked: `"source": "scenes/main.blend:Player"`; `ui_add` kind `model`, `design-ui`);
   - `Pause` (Escape or P: Resume, Options, Restart) and `Options` (Music and Sound sliders and a Language button, saved for the player with no code) as they are;
   - new or changed texts need French too: `ui_strings {"locale": "fr"}` lists what is missing, `ui_translate` adds it (`design-ui`, Languages);
   - `Win`, which code opens a second after the win effect, `ctx.after(1, (ctx) => ctx.ui.open('Win'))`: a menu pauses the game, so a burst opened with it would freeze. Add a `GameOver` screen for the lose condition the same way (`ui_canvas {"name": "GameOver"}`, a title, `Score {score}` and a button with `action: "restart"`: `ctx.restart()` clears `ctx.store` and goes back to the first level);
   - `HUD` (opens on every start): texts bound to `ctx.store` values that a system sets (`Score {score}`) or to the built-in level clock (`Time {game.time:m:ss}`, 0 again on each level), a `bar` for health, an `icons` row for lives (`design-ui`, Lives on the HUD).
   - Menus pause the game, so playtests start with `{"at": 0, "key": "Enter"}`. Arrows move the focus between buttons, or click where `final.ui.buttons[i]` says (`x`, `y`, in `final.ui.size` px). `game_screenshot {"ui": "Win"}` shows a screen as the game draws it; `open_scene` takes you back to the level.
   - For a quick prototype, code-only screens need no Blender work: `ctx.ui.screen('over', {title: 'Game Over', buttons: [{label: 'Restart', action: 'restart'}]})` and `ctx.ui.pauseMenu()`.
7. **Score and lives across levels** go in `ctx.store` (`ctx.store.score = (ctx.store.score ?? 0) + 10`); state for one level stays in components. Change level with `ctx.loadScene('level2', { keep: ['Player'] })` (`blockout-level`, More than one level).
8. **Juice:** a `ctx.vfx.burst` on each event the player causes: `pickup` on pickups, `sparkle` on hits and checkpoints, `explosion` on kills, `dust` on jumps and landings, `confetti` on a win. One or two ambient `ParticleEmitter`s where they fit (sparkles at the goal). Gameplay randomness comes from `ctx.random`.

## 4. Verify (all of these must hold)

- `npm run typecheck` passes.
- `validate_assets {}` reports no errors.
- `render_views` with `views: ["iso", "top"]`: the level reads clearly and the scale looks right next to the player.
- `playtest` proves each rule with its own short run (`playtest-and-fix`): a pickup counts, the lose or respawn rule fires, the core mechanic works, and each screen opens and closes (`screen`, `final.ui`). `start` begins a run next to a late segment, and `tracked` shows the counters change. `game_logs` has no errors.
- Write the critical path as one playtest from the real start to the win state; use `start` to test a late segment on its own. With moving hazards, plan it leg by leg (`playtest-and-fix`, Timing). `playtest` plays the level open in Blender: while you work on `level2`, pass `scene: "main"` to run from the start. For a fight aimed with the mouse cursor, `{"at": 9, "mouse": "aim", "target": "Grunt*", "until": 30}` with a held `{"at": 9, "mouse": "down", "hold": 21}` keeps the pointer on the nearest enemy.
- `game_screenshot` looks like a game, not a debug scene. If it doesn't, do a quick `stylize-scene` pass.
- `save`.

## 5. Hand off

Tell the user what you built, the controls, how to play (Play in Blender, or `npm run dev`), what you cut, and the next three things you would add. Keep `docs/DESIGN.md` updated.

## Common failures

| Symptom | Fix |
|---|---|
| The player falls through the ground | The ground needs a `Collider`; terrain needs `{shape: "mesh"}`. The player must start above the floor, not inside it: `snap_to_ground`. |
| The player doesn't move | It needs all three: `CharacterController`, a kinematic `RigidBody` and a capsule `Collider`. Click the game view or canvas first so it gets keyboard focus. |
| The camera points the wrong way or sits inside the player | `FollowCamera.offset` is Y-up: `[0, 4, 8]` means up 4 and behind 8. `target` is the Player object's name. |
| A new component is missing from `list_component_types` | The dev server regenerates `components.json` when the file is saved. Check `game_logs` and the typecheck for errors in the component file, or run `npm run registry`. |
| The HUD text is stale after a restart | Call `ctx.hud.clear(key)` or `set` it again in `start(ctx)`. |
