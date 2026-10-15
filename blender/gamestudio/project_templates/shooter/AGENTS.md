# {{name}}

A top-down arena shooter made from GameStudio's Arena Shooter template. In "The Pit", waves of enemies pour in from four red spawn pads and chase you. Aim with the mouse, shoot them down, survive as many waves as you can and push your score up.

## Play

- The title screen's Play (Enter) starts the game; its Options sets the music and sound volumes. WASD or the left stick move. The mouse aims (the cyan ring on the floor) and the left button fires while held. On a gamepad the right stick aims and fires, and the right trigger fires. Escape or P pauses (Resume, Options, Restart).
- The health bar is top left (red at 30 % or less), the wave and the enemies left are top centre, and the score is top right. Waves get bigger, and a banner announces each one and its end. At 0 HP the player explodes and the game over screen shows the score, the wave reached and the best score. R restarts.

## What is in the project

| Objects | Components | System (file) |
|---|---|---|
| `Player` (`character` kit) with child `PlayerGun` | `CharacterController {speed 7, jumpSpeed 0, turnSpeed 0}`, kinematic `RigidBody`, capsule `Collider`, `Shooter {fireRate, projectile, spread, muzzleHeight}`, `Health {max: 100}` | `AimSystem` (`aim.ts`) turns the player toward the aim point; `ShootingSystem` (`shooting.ts`) spawns the projectile prefab |
| `AimMarker` | `AimMarker` | `AimSystem` moves it to the aim point |
| `Waves` (an empty) | `WaveSpawner {enemies, firstWave, perWave, spawnInterval, breather}` | `WaveSystem` (`waves.ts`): a breather, the wave one enemy at a time (pads, enemy types and jitter from `ctx.random.stream('spawns')`), the next wave once it is cleared; the HUD's wave values, banners and controls hint |
| `SpawnPad1`–`SpawnPad4` | `SpawnPoint` | where enemies appear (pads near the player are skipped) |
| `SpawnPadGlow1`–`SpawnPadGlow4` (empties) | `ParticleEmitter` | ambient red embers rising off the pads |
| `Music` (an empty) | `AudioSource {clip: assets/music/theme.ogg, loop, autoplay, spatial: false, channel: music, volume 0.5}` | built-in: the starter's theme, which plays on under the menus |
| `Camera` | `FollowCamera`, `PostEffects {bloom}` (subtle: bullets, lamps and embers glow) | built-in |
| `Sun` (`sky` kit) | — | the sky and the sun; the world's mist is the sandy haze over the dunes |
| Prefabs `Grunt`, `Runner` | `Enemy {speed, damage, reach, attackInterval, points}`, `Health`, kinematic `RigidBody`, capsule `Collider`, `NavAgent` | `EnemySystem` (`enemies.ts`, fixed phase): points the `NavAgent` at the nearest player (it walks the navmesh around cover, with Rapier's character mover), with `Enemy.speed` and a stop just inside `reach`; faces the player up close; hits in reach |
| Prefab `Bullet` | `Projectile {speed, damage, lifetime}` (no physics body) | `ProjectileSystem` (`projectiles.ts`): flies straight, a raycast per frame finds what it hits |
| — | — | `HealthSystem` (`health.ts`): the player's health for the HUD, enemies' health bars, hit flashes, defeated enemies explode and score, the game over screen (grey through `ctx.post`, best score in `ctx.save`), R |
| Floor, walls, pillars, low walls, crates, barrels | `Collider` (the floor also `NavMesh`) | static cover; the navmesh is built from these colliders when the level loads (`ctx.nav.debug = true` draws it) |

The prefabs live in the **`Prefabs` scene** of `scenes/main.blend` (collections marked `gs_prefab`), so they are templates for `ctx.spawn('Grunt', {position, rotation})` and not part of the level. The score is the run's, in `ctx.store.score` (Restart clears it). Shared code in `src/lib/`: `arena.ts` (kills, wave, game over), `health.ts` (`hitPoints`, `damage`, `sinceHit`; the hit points themselves are `Health.current`, which `HealthSystem` fills to `max` when an entity appears, so `game_state` and playtest tracks show them) and `sfx.ts` (the sounds in `assets/sfx/`, imported with `?url`). The sounds are generated placeholders: replace the files and keep the names.

## Shooter conventions

- **Camera**: `FollowCamera` high above (`offset [0, 19, 11]`), looking along −Z. The `CharacterController` moves relative to it, so W is up the screen. `turnSpeed 0` leaves the facing to `AimSystem`; `jumpSpeed 0` means no jumping.
- **Yaw 0 faces runtime +Z** (Blender −Y, the kits' front): `rotation.y = Math.atan2(dx, dz)`. Projectiles fly along their own front.
- **Enemies are kinematic** and moved by code. Never make them dynamic. Projectiles have no body and are never moved by physics.
- **Spawning**: `ctx.spawn` for prefabs, `ctx.destroy` to remove. Enemy prefabs are one object (the root): a `character` kit with its components. Random choices come from `ctx.random`, so a playtest's seed repeats them; spawns draw from their own stream, `ctx.random.stream('spawns')`, so the aim spread of the player's shots never moves where enemies appear. Enemies step over seams and low edges (the mover's autostep).
- **Screens** are made in Blender, in `ui/ui.blend` (one scene per screen; the `design-ui` skill and the `ui_*` tools edit them): `Title` (opens on the first start: Play, Options), `Pause` (the pause menu: Escape, P or gamepad Start; Resume, Options, Restart), `Options` (Music and Sound sliders on the `audio.music` and `audio.sfx` channels, saved by the engine; Back), `GameOver` (`HealthSystem` opens it with `ctx.ui.open('GameOver')`; Try again) and `HUD`. Menus pause the game; Restart and Try again are `ctx.restart()`, which clears `ctx.store` and skips the title. **Playtests open on the title screen**: start the inputs with `{"at": 0, "key": "Enter"}`.
- **The HUD reads `ctx.store`** through bindings, so no code draws it: `score`, `health` (the player's hit points; the `HealthBar`'s max is 100, the Player's `Health.max`: change both), `healthLow` (true at 30 % or less: the red `HealthBarLow`, a `UiShow`), `wave` (0 before the first: "Get ready") and `enemiesLeft`; the best score is `ctx.save` `bestScore` (`{save.bestScore}`). Code shows what bindings can't: the `WaveBanner` and `ClearedBanner` texts and the `ControlsHint` (`ctx.ui.show`/`hide`), and "New best score!" on `GameOver`. A playtest reads them in `final.ui.texts` (`"HUD/Wave": "Wave 1 · 4 left"`). `ctx.hud` is for what follows the arena: enemies' health bars and the `+10` over a kill.
- **Juice**: `ctx.vfx.burst` for one-off effects: a muzzle flash per shot, `sparkle` on hits and spawns, `dust` on walls, `explosion` on kills, `confetti` when a wave is cleared.
- **Balance knobs**: `Shooter.fireRate`, `Projectile.damage`, `Enemy.speed`/`damage`/`points`, `Health.max` (on the prefabs) and the `WaveSpawner` fields. `WaveSpawner.enemies` lists prefab names, and `Name@N` makes one join from wave N.
- The arena is 30 × 30 m around the origin, with walls at ±15.5 m and spawn pads 12.5 m out. Enemies path around cover, so new cover just changes their routes; leave gaps wider than two agent radii (`NavMesh.agentRadius`, 0.4 m) where they should pass.

## Extending it

- **A new enemy type**: the `add-enemy-type` skill (a prefab, its stats, the wave roster, and a playtest).
- **New mechanics** (pickups, power-ups, dashes, enemy bullets): the `add-mechanic` skill. Spawn effects and bullets from prefabs.
- **Animated characters**: the `animate-character` skill. `character_rigged` enemies or players with `Locomotion` animate from their movement, and their colliders fit the skinned mesh.
- **Rebuilding the level**: `tools/build_level.py` builds `scenes/main.blend`, prefabs included, from scratch (its docstring says how to run it). It discards edits made in Blender, so put lasting changes into the script, or stop using it once you edit the level by hand. Never run it without telling the user. `tools/build_ui.py` built `ui/ui.blend` the same way; it refuses while that file exists, so edit the screens with the `design-ui` skill instead.
- **Your own template**: the `make-template` skill saves this game as a template that New Project offers next to the built-in ones (`save_template`).

## GameStudio basics

{{base}}
