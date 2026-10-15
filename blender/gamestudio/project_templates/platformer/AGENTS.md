# {{name}}

A third-person 3D platformer made from GameStudio's Platformer template. The level "Sky Garden" is a chain of floating islands: run and jump along it, collect coins, ride the ferry, pass the checkpoints, avoid the lava and reach the golden star.

## Play

- The title screen's Play (Enter) starts the game; Options sets the music and sound volumes. WASD, the arrow keys or the left stick move, relative to the camera. Space jumps. Escape or P pauses (Resume, Options, Restart). R restarts the run.
- The HUD counts the coins in the top left with your lives as hearts below them, and the clock runs in the top right. Falling off an island or touching lava costs a life and returns you to the last checkpoint; with no lives left the game is over. The star ends the level with confetti and a screen with your time, coins and best time. Music plays throughout.

## What is in the project

| Objects | Components | System (file) |
|---|---|---|
| `Player` (`character_rigged` kit: the armature; `Player_Mesh` is its skinned body) | `CharacterController {speed 6, jumpSpeed 7.5, gravity 20}`, kinematic `RigidBody`, capsule `Collider`, `Animator {graph: Player}` | built-in movement; the animation graph `Player` (`player-animation.ts`): idle, walk and run by speed, jump, air, fall and land, from how it moves; `RespawnSystem` (`respawn.ts`); `DustSystem` (`dust.ts`): dust and the jump sound on take-off, dust on hard landings |
| `Camera` | `FollowCamera {target: Player, offset [0, 4.5, 9]}`, `PostEffects {bloom}` (subtle: only bright things glow) | built-in |
| `Coin1`–`Coin15` | `Coin {value}` | `CoinSystem` (`coins.ts`): spins and bobs them, collects them within 1.1 m of the player's chest (a `pickup` burst, a `+1` label, `ctx.store.coins` and `score`) |
| `BouncePad` | `BouncePad {strength}`, `Collider` | `BouncePadSystem` (`bounce-pads.ts`): sets `characterMotion(player).verticalVelocity` |
| `Ferry` | `MovingPlatform {offset, period, phase}`, kinematic `RigidBody`, `Collider` | `MovingPlatformSystem` (`moving-platforms.ts`, fixed phase): glides there and back, carries whoever stands on it |
| `Checkpoint1`, `Checkpoint2` | `Checkpoint {radius}`; the child flag has `Animator {clip: FlagRaise}` | `RespawnSystem`: the last one passed is the respawn point; its flag clip plays once, with a `sparkle` burst |
| `Lava1`, `Lava2` | `KillZone` (no Collider: you walk into it) | `RespawnSystem`: a life (`ctx.store.lives`) and back to the checkpoint; so is falling below y = −3. No lives left: the `GameOver` screen |
| `GoalStar` | `Goal {radius}`, `Animator {clip: spin_bob}` | `GoalSystem` (`goal.ts`): the clock (`ctx.store.time`), confetti and the `Win` screen (best time in `ctx.save`), R to restart |
| `StarSparkles`, `LavaEmbers1`, `LavaEmbers2` (empties) | `ParticleEmitter` | ambient twinkles at the star and embers over the lava |
| `Sun` (`sky` kit) | — | the gradient sky and the sun; the world's mist is the game's distance fog |
| `Music` (empty) | `AudioSource {clip: assets/music/theme.ogg, loop, autoplay, spatial: false, channel: music, volume 0.5}` | built-in: starts on the first key press and plays under the menus |
| Islands, stairs, rocks, trees, fences, the hut | `Collider` (stairs and the hut: `mesh`); trees also `Animator {clip: sway}` | static |

Shared code in `src/lib/`: `contact.ts` (`boundsOverlap`, `isStandingOn`, `separation`), `level.ts` (the `ctx.store` keys the systems set and the screens show, `START_LIVES`, `formatTime`) and `sfx.ts` (the sounds in `assets/sfx/`, imported with `?url` so builds include them). The sounds are generated placeholders: replace the files and keep the names. The run's score and lives are `ctx.store.score` and `ctx.store.lives`: they carry over to later levels (`ctx.loadScene`), and Restart clears them.

## Platformer conventions

- **Jump metrics** (speed 6, jumpSpeed 7.5, gravity 20): 1.4 m high, 0.75 s in the air, about 4.5 m long on flat ground. Keep gaps at 3 m or less and steps up at 1 m or less per jump. A `BouncePad` of strength 14 reaches about 4.9 m (strength² / 40). Read the real values from the `Player` if they were changed.
- **The course runs toward Blender +Y** (the player's forward, runtime −Z), so the follow camera looks along it. `MovingPlatform.offset` is in runtime space: Blender +Y is −Z.
- **Islands** are `platform` kits with the top at a round height, 3 m thick so they read as floating rock. The water plane at z = −6 has no collider: falling is the hazard.
- **Animation**: the player's clips come with the `character_rigged` kit, and its graph (`src/systems/player-animation.ts`, API.md §15) picks them: its parameters fill themselves from the movement (`from`), so no system sets them. Add a state there (an attack, a wave) with a trigger that your system sets with `ctx.animation.graph(player)?.trigger(...)`. `Animator.state` shows the state in `game_state` and playtests. The flags' `FlagRaise` is an action keyed in `tools/build_level.py`; the star and trees use clips from `gs.kits.animation` (`spin_bob`, `sway`). Clips are 30 fps. A clip that keys an object's location overrides code that moves it: animate a child instead.
- **Systems find things by component.** New pickups, hazards and triggers are components plus a system, using the `contact.ts` helpers: the player raises no collision events (2 cm skin).
- **Screens and HUD** are Blender screens in `ui/ui.blend`, which `tools/build_ui.py` made at New Project; change them there (the `design-ui` skill). `Title` opens once per page load (Play, Options); `Pause` is the pause menu (Escape, P or gamepad Start: Resume, Options, Restart); `Options` has Music and Sound sliders on `audio.music` and `audio.sfx` (the engine saves them) and Back; `goal.ts` and `respawn.ts` open `Win` and `GameOver` with `ctx.ui.open`, and their buttons restart (`ctx.restart()` clears `ctx.store` and skips the title); the `HUD` is always open. Menus pop open and fade out. Texts bind the `ctx.store` keys the systems set (`src/lib/level.ts` lists them): `HUD/Coins` is `{coins} / {coinTotal}`, `HUD/Clock` `{time:m:ss}`, the `Lives` hearts show by `lives` (one per `START_LIVES`), and `Win` has `{time:m:ss.00}`, `{save.bestTime:m:ss.00}` and "New best time!" (`UiShow` on `newBest`). To show something new, set a store key and bind a text to it. `ctx.hud` only pins labels to the world (`+1`, `Checkpoint!`, `Ouch!`). **Playtests open on the title screen**: start the inputs with `{"at": 0, "key": "Enter"}`; `final.ui.texts` then has `"HUD/Coins": "3 / 15"`.
- **Juice**: `ctx.vfx.burst(entityOrPoint, preset)` for one-off effects (`pickup` on coins, `dust` on jumps, `sparkle` at checkpoints and respawns, `smoke` in the lava, `confetti` at the star), and `ParticleEmitter` empties for ambient ones. Randomness in gameplay code comes from `ctx.random`.

## Extending it

- **More level**: the `add-level-section` skill adds a section (islands, jumps, coins, a checkpoint) after the last one and playtests it.
- **New mechanics** (keys and doors, enemies, timers, double jump): the `add-mechanic` skill.
- **More animation** (new clips, tuned walk and run speeds, NPCs): the `animate-character` skill. The player's collider is sized from its skinned mesh.
- **Rebuilding the level**: `tools/build_level.py` builds `scenes/main.blend` from scratch (see its docstring for how to run it). It discards edits made in Blender, so put lasting changes into the script, or stop using it once you edit the level by hand. Never run it without telling the user. `tools/build_ui.py` made `ui/ui.blend` the same way; it refuses while that file exists, so edit the screens instead.
- **Your own template**: the `make-template` skill saves this game as a template that New Project offers next to the built-in ones (`save_template`).

## GameStudio basics

{{base}}
