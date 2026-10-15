# {{name}}

An endless runner made from GameStudio's Endless Runner template. In "Sunset Skyway" the runner sprints down a stone road above a sea of clouds at sunset: change lanes around crates and boulders, jump hurdles and gaps, collect coins, see how far you get. The road is streamed at runtime from **chunk prefabs**, and every obstacle, coin line and pickup on it is a prefab spawned into a socket: the template is a reference for using prefabs at runtime, each technique in its own file (below).

## Play

- The title screen's Play (Enter) starts the run. The runner runs by itself. A/D or the arrow keys (gamepad: d-pad or a flick of the left stick) move it a lane, and Space (W, up, gamepad A) jumps. Escape or P pauses (Resume, Options, Restart). R restarts.
- The HUD shows the distance, the score (a point per metre, 10 per coin), the coins and power-up timers. The speed ramps from 10 to 20 m/s over the first 2 km, and harder chunks unlock as you go. Running into an obstacle or falling off the road ends the run: the game over screen shows the distance, coins, score and best distance (`ctx.save`).
- Pickups: the **magnet** (pink) pulls coins in for 8 s; the **shield** (blue) breaks any obstacle you hit and bounces you out of gaps for 8 s.

## What is in the project

| Objects | Components | System (file) |
|---|---|---|
| `PlayerStart` (an empty on the start deck) | — | `RunSystem` (`run.ts`) finds it by name and spawns the `Runner` prefab there, named `Player`; it also keeps the HUD's `ctx.store` values, milestones and the `GameOver` screen |
| Prefab `Runner` (`character_rigged` kit) | `Runner {startSpeed, maxSpeed, rampDistance, laneSpeed, jumpSpeed, gravity}` and runtime fields (`lane`, `distance`, `speed`, `magnet`, `shield`), kinematic `RigidBody`, capsule `Collider` | `RunnerSystem` (`runner.ts`, fixed phase): runs, changes lanes and jumps with Rapier's character mover; plays Run, JumpStart, JumpAir and Fall |
| `Track` (an empty where the deck ends) | `Track {ahead, behind, warmup, obstacles, coins, pickups}` | `TrackSystem` (`track.ts`): streams the chunks |
| Chunk prefabs `ChunkStraight`, `ChunkGap`, `ChunkSlalom`, `ChunkRamp`, `ChunkNarrow`, `ChunkSweeper` | root: `TrackChunk {length, weight, minDistance}`; floors: `Collider`; empties: `Socket {kind, chance, anyLane, options}`; `LampPost` (and `CoinArc` in `ChunkGap`) collection instances | `TrackSystem`; sockets filled by `src/lib/sockets.ts` |
| Prefabs `Hurdle` (jumpable), `Crates`, `Boulder`, `Sweeper` | `Obstacle {debris}`; `Sweeper {travel, speed, radius}` | `CrashSystem` (`crash.ts`, fixed phase); `SweeperSystem` (`sweepers.ts`) rolls the drum across the lanes |
| Prefabs `CoinLine` (5 coins), `CoinArc` (7 coins along a jump) | `Coin {value}` on each coin | `CoinSystem` (`coins.ts`, fixed phase): spin, magnet, pickup (`ctx.store.coins`) |
| Prefabs `Magnet`, `Shield`; `MagnetAura`, `ShieldBubble` | `PowerUp {kind, duration, aura}`; the icons `Spin`, the sparkles `ParticleEmitter` | `PowerUpSystem` (`power-ups.ts`) |
| Prefabs `DebrisWood`, `DebrisStone` | `Debris {force, lifetime}`; pieces with a dynamic `RigidBody` and a convex `Collider` | `DebrisSystem` (`debris.ts`) |
| `CloudSea` (`terrain` kit), `FarIsland1`–`4`, `WindMotes` (`ParticleEmitter`) | `Backdrop` | `BackdropSystem` (`backdrop.ts`): they travel with the runner, so the scenery never ends |
| `Camera` | `FollowCamera {offset [0, 3.4, 6.8], lookAtOffset [0, 1.4, -6]}` (its target is set at runtime), `PostEffects {bloom}` | built-in |
| `Sun` (`sky` kit), `StartDeck`, `StartDecor`, `StartIsland1`–`3` | `StartDeck`: `Collider` | the sunset sky; the world's mist is the fog, in the clouds' colour |
| `Music` (an empty) | `AudioSource` (`assets/music/theme.ogg`, looping, channel `music`) | built-in: it plays on under menus; the Options Music slider sets it |

Shared code in `src/lib/`: `sockets.ts` (fills a chunk's sockets), `spawn.ts` (`spawnAt`: a prefab at a world transform under a parent), `weighted.ts` (`parseWeighted`, `pickWeighted`), `lanes.ts` (`LANES`), `motion.ts` (`runnerMotion(runner)`: vertical speed, grounded, air time), `contact.ts` (obstacle boxes), `run.ts` (the run's state, `score`, `difficulty`, `banner`) and `sfx.ts` (the sounds in `assets/sfx/` and three of the starter's). The sounds are generated placeholders: replace the files and keep the names.

## Runtime prefab patterns

| Technique | What it does here | File |
|---|---|---|
| **Chunk streaming** | Chunk prefabs are spawned end to end ahead of the runner (`Track.ahead` metres, past the fog) and destroyed once they are `Track.behind` metres behind it, so about seven chunks and 250 entities exist however long the run. | `src/systems/track.ts` |
| **Sockets: empties inside prefabs** | Each chunk holds `Socket` empties. Right after a chunk spawns, `ctx.world.descendants(chunk, Socket)` finds that copy's sockets, and an obstacle, coin line or pickup is spawned at each socket's world transform, attached to the chunk, so it goes when the chunk goes. This is Unity's spawn-point pattern, inside a prefab. | `src/lib/sockets.ts`, `src/lib/spawn.ts` |
| **Seeded, weighted selection** | Chunks are picked by `TrackChunk.weight` among those whose `minDistance` the run has reached, and socket contents by the weights in `Track.obstacles` or `Socket.options`, all from `ctx.random.stream('track')`. The same seed builds the same track, so playtests repeat; debris has its own stream. | `src/systems/track.ts`, `src/lib/weighted.ts` |
| **Per-instance overrides** | Spawned copies get their own values: coins are worth more further into the run, sweepers roll faster, and the camera's `FollowCamera.target` is pointed at the spawned player. | `src/lib/sockets.ts`, `src/systems/run.ts` |
| **Event-driven spawns** | Hitting an obstacle spawns its `Obstacle.debris` prefab where it stood; a power-up spawns its aura prefab as a child of the runner, removed when the timer runs out. | `src/systems/crash.ts`, `src/systems/debris.ts`, `src/systems/power-ups.ts` |
| **A nested prefab** | Chunks contain collection instances of `LampPost`, and `ChunkGap` one of `CoinArc`. Each becomes a full instance under its chunk at runtime: the arc's coins are collected like any other. | `tools/build_level.py` (`Chunk.lamp`, `Chunk.instance`) |
| **A spawn point placed in Blender** | `ctx.world.findByName('PlayerStart')` (Unity's `GameObject.Find`) gives the empty; the `Runner` prefab is spawned at its world position and rotation. | `src/systems/run.ts` |

Probing the catalog: `TrackSystem` spawns each prefab once when the scene starts (its components live on its instances), keeps the ones whose root has a `TrackChunk`, and destroys them in the same frame.

## Where the chunks are, and adding one

The prefabs are in `scenes/main.blend`, in two scenes besides `main`, so they are templates and not part of the level: **`Chunks`** holds the track pieces side by side, 50 m apart along X, and **`Prefabs`** everything else that is spawned (the runner, obstacles, coins, pickups, auras, debris, `LampPost`). A chunk is a collection marked `gs_prefab` whose root empty sits at the chunk's start, with everything else parented to it: the road runs toward the root's +Y from 0 to `TrackChunk.length`, lanes are at x = −2.2, 0 and 2.2, and the road's top is at z = 0. Floors need a `Collider`; decor doesn't. Socket empties are drawn as cubes (obstacles), spheres (coins) and cones (pickups).

To add one, use the **`add-chunk` skill**: it builds the chunk with the `Chunk` helper in `tools/build_level.py`, adds sockets and lamps, sets its weight and `minDistance`, and playtests it with a fixed seed. By hand: make a collection in the `Chunks` scene, mark it (`set_prefab`), add an empty as its root with `TrackChunk`, parent floors with a `Collider` and `Socket` empties to it, and set the collection's instance offset to the root's location. Nothing in the code lists the chunks.

## Runner conventions

- **The track runs toward Blender +Y** (runtime −Z) along x = 0. The runner faces it; `Runner.distance` counts metres from `PlayerStart`, and the `Track` empty is where chunks start (12 m further).
- **Jump metrics** (jumpSpeed 9, gravity 30): 1.35 m high, 0.6 s in the air, 6 m long at the start speed and 12 m at the top speed. Hurdles are 0.75 m high, gaps 3.5 m. Tall obstacles (crates 1.85 m, boulders 1.5 m) must be passed in another lane: keep at least one lane free in every row.
- **Obstacles are boxes, not physics.** `CrashSystem` tests the runner's body against each `Obstacle`'s bounds (children included), less 12 cm of forgiveness; obstacles need no `Collider`. Floors need one: the runner falls where there is none. Debris pieces are dynamic bodies that the runner's mover ignores.
- **Fairness**: give each chunk a few metres free at both ends (another chunk can come before or after any chunk), keep sockets with `anyLane` in different rows, and keep a jumpable obstacle out of the landing zone after a gap or a drop. No obstacles spawn in the first `Track.warmup` metres.
- **Screens and HUD** are scenes of `ui/ui.blend` (rebuilt by `tools/build_ui.py`; the `design-ui` skill edits them): `Title` (first start only: Play, Options), `Pause` (Escape, P, gamepad Start: Resume, Options, Restart), `Options` (Music and Sound sliders on `audio.music` and `audio.sfx`, saved by the engine; Back), `GameOver` (opened by `RunSystem`, which sets `OverTitle` and shows the `NewBest` ribbon; Try again is `ctx.restart()`, which clears `ctx.store`) and `HUD`.
- **The HUD binds `ctx.store`**: `{distance}` and `{score}` (`RunSystem` keeps them, and `speed`, from the `Runner` each frame), `{coins}`, `magnetLeft` and `shieldLeft` (0–1 of the time left; a power-up's row shows only above 0) and `{banner}` (`banner(ctx, text, seconds)` in `src/lib/run.ts`); `{save.bestDistance}` is the best. `ctx.hud` only pins `+N` over coins.
- **Playtests open on the title screen**: start the inputs with `{"at": 0, "key": "Enter"}`; `final.ui.texts` shows the HUD (`HUD/Distance`). The player is spawned at runtime and named `Player`: `track: ["Player"]` follows it.
- **Testing a long run**: give the `Runner` prefab `shield: 999` for a moment (`set_component`): obstacles break and gaps bounce, so a playtest reaches the far chunks. Restore it afterwards.
- **Juice**: `ctx.vfx.burst` for one-off effects (`pickup` on coins, `dust` on jumps and landings, `sparkle` on power-ups and shield hits, `explosion` on a crash, `confetti` every 500 m), `ParticleEmitter` empties for the wind and the pickups' sparkles.

## Extending it

- **A new chunk**: the `add-chunk` skill.
- **New obstacles or pickups**: a prefab in the `Prefabs` scene with `Obstacle` (or `PowerUp`), then its name in `Track.obstacles` (or a socket's `options`). A new kind of power needs a field on `Runner` and a few lines in `power-ups.ts`.
- **New mechanics** (sliding under barriers, a double jump, a boss chase): the `add-mechanic` skill.
- **Rebuilding**: `tools/build_level.py` rebuilds `scenes/main.blend` (chunks and prefabs too) and `tools/build_ui.py` `ui/ui.blend`, from scratch (see their docstrings). Both discard edits made in Blender: put lasting changes into the script, or stop using it once you edit by hand. Never run one without telling the user.

## GameStudio basics

{{base}}
