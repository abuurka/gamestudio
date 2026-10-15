---
name: tune-handling
description: Tune how the car in this time-trial game drives (top speed, acceleration, steering, grip, drifting, off-road slowdown, camera feel) by measuring with playtests, changing one thing at a time and checking that the track stays drivable. Use when the user says the car feels too slow, fast, twitchy, floaty, heavy, slippery or grippy, wants drifting, arcade or sim handling, or a different camera.
argument-hint: "[how it should feel]"
---

# Tune the handling

Handling comes from a few numbers on the `Car` component, the `Surface` of the ground and the `ChaseCamera`. Change them in Blender with `set_component`: the running game picks them up live. Then prove the change with the same playtest before and after.

## The knobs

| Field | Default | What it does | Useful range |
|---|---|---|---|
| `Car.maxSpeed` | 32 m/s (115 km/h) | Top speed on the road | 20 (kart) – 45 (fast arcade) |
| `Car.acceleration` | 14 m/s² | 0 to top speed in maxSpeed / acceleration s | 8 (heavy) – 22 (twitchy) |
| `Car.braking` | 30 m/s² | Braking; also how fast off-road bleeds speed | 18 – 40 |
| `Car.reverseSpeed` | 8 m/s | Top speed backwards | 5 – 12 |
| `Car.steering` | 2.1 rad/s | Turn rate at full lock. Full rate is reached above 6 m/s and drops 30% at top speed | 1.5 (heavy) – 3.2 (kart) |
| `Car.grip` | 7 | How fast sideways sliding dies. Low values slide on every corner | 4 (loose) – 12 (on rails) |
| `Car.handbrakeGrip` | 1.2 | Grip with Space held: lower gives longer drifts | 0.4 – 3 |
| `Surface.speedFactor` on `Ground` | 0.45 | Top speed on the grass, as a share of the road's | 0.3 – 0.8 |
| `Surface.grip` on `Ground` | 0.7 | Grip on the grass | 0.4 – 1 |
| `ChaseCamera.distance` / `height` | 8 / 3.2 m | How much track you see ahead | 6–11 / 2.5–5 |
| `ChaseCamera.stiffness` | 5 | How tightly the camera follows (low: swings wide) | 3 – 9 |

Recipes to start from:

- **Faster arcade**: maxSpeed 40, acceleration 18, steering 2.4, grip 8.
- **Drift**: grip 4.5, handbrakeGrip 0.6, steering 2.6.
- **Heavy muscle car**: acceleration 9, braking 20, steering 1.7, grip 6.
- **Go-kart**: maxSpeed 22, acceleration 16, steering 3.0, grip 11, camera distance 6 and height 2.5.

## Keep the track drivable

At top speed the car turns on a circle of about maxSpeed / (0.7 × steering) metres: 22 m with the defaults. The tightest corners of the circuit are about 20 m. Faster or less steering means the player must brake for them, which is fine, but a circle wider than about 40 m makes the hairpins frustrating. Barriers stand 9.5 m from the centre line, so the car is never stuck, but each hit costs the speed lost against the wall.

## The loop

1. **Measure first.** `stop`, then `playtest` with `track: ["Car"]`:
   - Straight line: `{"at": 0, "key": "KeyW", "hold": 4000}`, `duration_ms` 4500, `screenshots: [4000]`. The result's distance moved and the HUD's speed (`final.ui.texts["HUD/Speed"]`, km/h) give the acceleration and top speed.
   - Corner: `{"at": 0, "key": "KeyW", "hold": 6000}`, `{"at": 3000, "key": "KeyD", "hold": 1500}`, `screenshots: [3000, 4500, 6000]`. Look at how far it turns and whether it slides.
   - Drift: add `{"at": 3200, "key": "Space", "hold": 800}` to the corner run.
2. **Change one or two fields** with `set_component` on `Car` (or `Ground`, `Camera`).
3. **Run the same playtest again** and compare the distance, the speed, the screenshots and `game_logs` with the earlier run. Explain the difference in the player's terms ("reaches top speed a second sooner, slides wider in the first corner").
4. **Drive a lap.** A playtest of about 30 s, with W held and steering at the corners, must pass "Checkpoint 1/3 … 3/3" in `game_logs`. If it can't, the car is now too fast or too slow to steer for this track.
5. `save`. The defaults that `tools/build_level.py` writes are in `add(car, "Car")`: put the new values there as `add(car, "Car", {...})` so that a rebuild keeps them.

## Beyond the numbers

The model is in `src/systems/car-controller.ts` and it hot-reloads. The constants at the top are there to change: `COAST` (slowdown with no throttle), `FULL_TURN_SPEED`, `GRAVITY`, and the mover's autostep and snap distances in `moverFor`. Ideas that fit the model:

- **Speed-sensitive steering**: change the `grip` factor in the turn-rate line (the one with `0.3 *`).
- **Boost pads**: a component on sensor-free trigger volumes, plus a system that adds speed along the car's front through `carState(car).velocity` (`src/lib/car-state.ts`).
- **Drift boost**: count how long `handbrake` and a big `sideways` value last, then add speed on release.

`npm run typecheck` must pass after code changes. Playtest as above.

## Common failures

| Symptom | Fix |
|---|---|
| The car spins on the spot when starting | Steering applies only with speed (`FULL_TURN_SPEED`). If you changed that, put it back to 4 m/s or more. |
| It slides out of every corner | `grip` is too low, or the `Ground` grip is low and it's on the grass. |
| It never reaches the top speed | `speedFactor` on the surface under it (the HUD says "OFF ROAD": `HUD/OffRoad` is in `final.ui.texts`), or the acceleration is low. Check `Surface` on `Track`. |
| It hops or stalls on the kerbs | The kerb is higher than the mover's autostep (0.12 m) in `moverFor`. Keep road pieces within 6 cm of each other. |
| The camera clips into the barriers | Raise `ChaseCamera.height` or shorten `distance`. |
| A change does nothing | The game isn't playing, or you edited a different object. `get_object` on `Car` shows the values. Values set from code reset when the scene reloads. |
