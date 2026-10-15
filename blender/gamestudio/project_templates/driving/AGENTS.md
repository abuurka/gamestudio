# {{name}}

An arcade driving game made from GameStudio's Time Trial template. "Pine Valley Circuit" is a 390 m loop through a pine valley: cross the start line to start the clock, pass the three checkpoint gates in order and set the best lap over three laps.

## Play

- The title screen's Play (Enter) starts the game; its Options sets the music and sound volumes. W or up accelerates, S or down brakes and then reverses, A and D steer, Space is the handbrake (slides into drifts). The left stick works too. Escape or P pauses (Resume, Options, Restart). R restarts.
- The HUD: until the start line, what to do; then the lap and the race time top left, the best lap top right and the speed bottom right, with "OFF ROAD" on the grass, where the car is slower and kicks up dust. A banner says GO!, each checkpoint's split and each lap's time. The yellow arrow hangs in the next gate. After the last lap, confetti and the Results screen: total time, best lap and the record, or "New record!".

## What is in the project

| Objects | Components | System (file) |
|---|---|---|
| `Car` (MeshBuilder body) | `Car {maxSpeed, acceleration, braking, reverseSpeed, steering, grip, handbrakeGrip}`, kinematic `RigidBody`, box `Collider` | `CarControllerSystem` (`car-controller.ts`, fixed phase): arcade physics on Rapier's character mover |
| `Car_WheelFL` … `Car_WheelRR` (children of `Car`) | `Wheel {radius, steers}` | `WheelSystem` (`wheels.ts`): spin with the speed, front wheels steer, rear wheels kick up `dust` off the road and tyre `smoke` in drifts |
| `Camera` | `ChaseCamera {target: Car, distance, height, lookAhead, stiffness}` | `ChaseCameraSystem` (`chase-camera.ts`, late phase) |
| `StartLine`, `Checkpoint1`–`Checkpoint3` (empties) | `Checkpoint {index}`; `StartLine` also `Race {laps}` | `RaceSystem` (`race.ts`): gates in order (a `sparkle` burst), laps (`confetti`), the clock, the best lap and the speed in `ctx.store` for the HUD, the split banner, the Results screen (record in `ctx.save`), R |
| `StartLineDust` (an empty) | `ParticleEmitter` | ambient dust drifting over the grid |
| `Sun` (`sky` kit) | — | the gradient sky and the sun; the world's mist is the game's distance fog |
| `Music` (an empty) | `AudioSource {clip: assets/music/theme.ogg, loop, autoplay, spatial: false, channel: music, volume 0.5}` | the starter's theme; music plays on under the menus |
| `NextCheckpoint` | `CheckpointMarker` | `RaceSystem` hangs it in the next gate |
| `Track` (road, kerbs, centre line) | mesh `Collider`, `Surface {speedFactor 1, grip 1}` | read by `CarControllerSystem` (a raycast down) |
| `Ground` (`terrain` kit) | mesh `Collider`, `Surface {speedFactor 0.45, grip 0.7}` | the grass slows the car |
| `Barriers` | mesh `Collider` | walls on both sides; speed lost against them is gone (only wall contacts cost speed, never the road) |
| `Cone1`–`Cone5` | dynamic `RigidBody`, convex `Collider` | knocked over by the car |
| Gates, stands, tyre stacks, trees (`Animator {clip: sway}`), rocks | — | scenery |

The menus and the HUD are in `ui/ui.blend` (Screens, below). Shared code in `src/lib/`: `car-state.ts` (`carState(car)`: velocity, speed, steering and off-road, like the engine's `characterMotion`; `formatTime`, the HUD's `m:ss.00`) and `sfx.ts` (the sounds in `assets/sfx/`, imported with `?url`). The sounds are generated placeholders: replace the files and keep the names.

## Driving conventions

- **The car is kinematic**: `CarControllerSystem` computes the motion and Rapier's character mover resolves walls, kerbs and the ground. Never make the car dynamic or move it through `object3d` from other systems. To launch or stop it, change `carState(car).velocity`.
- **Its front is Blender −Y** (runtime +Z), like every kit. The box `Collider` comes from the body mesh alone (the wheels are child entities), and the dark under-tray takes it down to the ground.
- **Checkpoints** are empties shown as cubes. The scaled 1 m cube is the volume the car must enter. Indexes give the order around the lap, and 0 is the start/finish line. Place them across the whole track width, so a car cannot miss one, and keep `Race.laps` on one object.
- **Screens** are in `ui/ui.blend`, one Blender scene each: `Title` (opens on the first start, not after Restart; Play, Options), `Pause` (the pause menu: Escape, P or gamepad Start; Resume, Options, Restart), `Options` (Music and Sound sliders on `audio.music` and `audio.sfx`, saved by the engine; Back returns to the menu it came from), `Results` (`ctx.ui.open('Results')` after the last lap; its `NewRecord` starts hidden and takes `RecordText`'s place after a record: `ctx.ui.show`) and `HUD`. Menus pause the game; Restart is `ctx.restart()` (it clears `ctx.store` and skips the title). **Playtests open on the title screen**: start the inputs with `{"at": 0, "key": "Enter"}`.
- **The HUD reads `ctx.store`**, which `RaceSystem` keeps: `lap` (0 before the start line), `laps`, `raceTime` and `lapTime` (seconds; `raceTime` stops at the finish), `bestLap` (after the first lap), `speedKmh`, `offRoad`; the record is `ctx.save` `bestRace`. Texts bind them (`Lap {lap}/{laps}`, `{raceTime:m:ss.00}`, `{speedKmh:0}`), and `UiShow` rules show the start hint (`lap` equals 0), the lap card (`lap` at least 1), the best-lap card and `OFF ROAD`. The banner is `HUD/Split`: `banner(ctx, text, seconds)` in `race.ts` sets `SplitText` and shows it. Change the layout with the `ui_*` tools (`design-ui` skill), and set a new value in `ctx.store` before binding it. A playtest's `final.ui.texts` has `"HUD/LapText": "Lap 1/3"`, `"HUD/Speed": "87"` and the rest.
- **Speed**: m/s in code, km/h on the HUD. `Car.maxSpeed` 32 m/s is 115 km/h. `Surface.speedFactor` scales the top speed on that ground.
- **The track** is the closed spline `CONTROL` in `tools/build_level.py`. The road, kerbs and barriers are swept along it, so change the points (keep corners wider than about 20 m) and rebuild. Do not edit the `Track` mesh by hand.

## Extending it

- **Handling** (faster, grippier, driftier, heavier): the `tune-handling` skill: a tuning table, a playtest per change, and how to keep the track drivable.
- **New mechanics** (boost pads, ghost car, AI rivals, a countdown): the `add-mechanic` skill. Read `carState(car)` instead of re-deriving speeds.
- **Another track**: copy `scenes/main.blend` to a new level, or change `CONTROL` and `CHECKPOINTS` and rebuild.
- **Rebuilding the level**: `tools/build_level.py` builds `scenes/main.blend` from scratch (its docstring says how to run it). It discards edits made in Blender, so put lasting changes into the script, or stop using it once you edit the level by hand. Never run it without telling the user. `tools/build_ui.py` does the same for `ui/ui.blend` (only once that file is deleted).
- **Your own template**: the `make-template` skill saves this game as a template that New Project offers next to the built-in ones (`save_template`).

## GameStudio basics

{{base}}
