# Gameplay patterns

Each pattern is a component file plus a system file, and each is typechecked against the engine API. Copy one, rename it, and keep only what you need. The coordinates are runtime space: Y-up, metres, radians.

Contents: [Trigger zone](#trigger-zone) · [Moving platform](#moving-platform) · [Health and hazards](#health-and-hazards) · [Fall respawn](#fall-respawn) · [Bounce pad](#bounce-pad) · [Countdown and restart](#countdown-and-restart) · [Chaser enemy](#chaser-enemy) · [Interact key](#interact-key) · [Spawner](#spawner)

Pickups (distance check, `destroy`, score on the HUD) usually exist already: the starter's `src/systems/collect.ts`, or the demo's `coins.ts`. To launch the player (bounce pads, double jumps), set `characterMotion(player).verticalVelocity`. For feedback, add a clip, a sound or a HUD label (`SKILL.md`, step 3), and a burst: `ctx.vfx.burst(entityOrPoint, 'pickup')` (`sparkle`, `dust`, `smoke`, `explosion`, `confetti`). Burst before `destroy()`: the effect plays on where the entity was. The run's score and lives go in `ctx.store` (`ctx.store.score ??= 0` in `start`, then `ctx.store.score += 10`): it survives level changes, and Restart clears it.

## Trigger zone

Fires once when the player enters a sensor. In Blender: `TriggerZone` + `Collider {sensor: true}` on an invisible or flat box.

```ts
// src/components/trigger-zone.ts
import { defineComponent, field } from '@gamestudio/engine/schema';

export const TriggerZone = defineComponent('TriggerZone', {
  description: 'Shows a message when the player enters. Needs Collider { sensor: true }.',
  category: 'Gameplay',
  fields: {
    message: field.string('Checkpoint!', { description: 'HUD text shown on entry' }),
    once: field.bool(true, { description: 'Fire only the first time' }),
    fired: field.bool(false, { description: 'Set at runtime once it has fired' }),
  },
});
```

```ts
// src/systems/trigger-zones.ts
import { CharacterController, defineSystem } from '@gamestudio/engine';
import { TriggerZone } from '../components/trigger-zone';

export default defineSystem({
  name: 'TriggerZoneSystem',
  query: [TriggerZone],
  phase: 'update', // collision events are complete in update/late
  start(ctx) {
    ctx.hud.clear('trigger');
  },
  update(ctx) {
    for (const { a, b, started } of ctx.physics.collisions) {
      if (!started) continue;
      const zone = a.has(TriggerZone) ? a : b.has(TriggerZone) ? b : undefined;
      const other = zone === a ? b : a;
      if (!zone || !other.has(CharacterController)) continue;
      const t = zone.get(TriggerZone);
      if (t.once && t.fired) continue;
      t.fired = true;
      ctx.hud.set('trigger', t.message, { position: 'top-center' });
      ctx.log(`${other.name} entered ${zone.name}`);
    }
  },
});
```

## Moving platform

It moves back and forth between its start and start + offset. In Blender: `MovingPlatform` + `RigidBody {type: "kinematic"}` + `Collider`.

```ts
// src/components/moving-platform.ts
import { defineComponent, field } from '@gamestudio/engine/schema';

export const MovingPlatform = defineComponent('MovingPlatform', {
  description: 'Moves between its start and start + offset. Needs RigidBody { type: kinematic } and a Collider.',
  category: 'Motion',
  fields: {
    offset: field.vec3([0, 0, 4], { description: 'Travel in metres, runtime space (Y up)' }),
    period: field.float(4, { min: 0.1, unit: 'time', description: 'Seconds for a full round trip' }),
  },
});
```

```ts
// src/systems/moving-platforms.ts
import { defineSystem } from '@gamestudio/engine';
import { Vector3 } from 'three';
import { MovingPlatform } from '../components/moving-platform';

const home = new Map<string, Vector3>(); // start position per entity id

export default defineSystem({
  name: 'MovingPlatformSystem',
  query: [MovingPlatform],
  phase: 'fixed', // move in step with physics so riders follow smoothly
  onAdd(_ctx, e) {
    home.set(e.id, e.object3d.position.clone()); // kinematic bodies sit at the scene root: local = world
  },
  onRemove(_ctx, e) {
    home.delete(e.id);
  },
  update(ctx, platforms) {
    for (const e of platforms) {
      const p = e.get(MovingPlatform);
      const start = home.get(e.id);
      if (!start) continue;
      const t = 0.5 - 0.5 * Math.cos((2 * Math.PI * ctx.time) / p.period); // 0 → 1 → 0, eased
      e.object3d.position.set(start.x + p.offset[0] * t, start.y + p.offset[1] * t, start.z + p.offset[2] * t);
    }
  },
});
```

## Health and hazards

Hazards hurt anything that has `Health`. At 0 HP the player respawns and other entities are destroyed. In Blender: `Health` on the player and enemies; `Hazard` + `Collider {sensor: true}` on spikes and lava.

```ts
// src/components/health.ts
import { defineComponent, field } from '@gamestudio/engine/schema';

export const Health = defineComponent('Health', {
  description: 'Hit points. At 0 the player respawns; other entities are destroyed.',
  category: 'Gameplay',
  fields: {
    max: field.int(3, { min: 1, description: 'Starting hit points' }),
    current: field.int(3, { min: 0, description: 'Hit points left (runtime)' }),
    invulnerable: field.float(0, { min: 0, unit: 'time', description: 'Seconds of invulnerability left (runtime)' }),
  },
});
```

```ts
// src/components/hazard.ts
import { defineComponent, field } from '@gamestudio/engine/schema';

export const Hazard = defineComponent('Hazard', {
  description: 'Damages Health entities that touch it. Give it Collider { sensor: true }.',
  category: 'Gameplay',
  fields: {
    damage: field.int(1, { min: 0, description: 'Hit points per touch' }),
  },
});
```

```ts
// src/systems/health.ts
import { CharacterController, defineSystem, snapCamera } from '@gamestudio/engine';
import { Vector3 } from 'three';
import { Hazard } from '../components/hazard';
import { Health } from '../components/health';

const spawnPoints = new Map<string, Vector3>();

export default defineSystem({
  name: 'HealthSystem',
  query: [Health],
  phase: 'update',
  start(ctx) {
    for (const e of ctx.world.query(Health)) {
      const h = e.get(Health);
      h.current = h.max;
      h.invulnerable = 0;
    }
  },
  onAdd(_ctx, e) {
    spawnPoints.set(e.id, e.object3d.getWorldPosition(new Vector3()));
  },
  update(ctx, entities) {
    for (const e of entities) {
      const h = e.get(Health);
      h.invulnerable = Math.max(0, h.invulnerable - ctx.dt);
    }
    for (const { a, b, started } of ctx.physics.collisions) {
      if (!started) continue;
      const hazard = a.has(Hazard) ? a : b.has(Hazard) ? b : undefined;
      const victim = hazard === a ? b : a;
      if (!hazard || !victim.has(Health)) continue;
      const h = victim.get(Health);
      if (h.invulnerable > 0) continue;
      h.current = Math.max(0, h.current - hazard.get(Hazard).damage);
      h.invulnerable = 1; // one second of grace
      if (h.current > 0) continue;
      if (victim.has(CharacterController)) {
        const spawn = spawnPoints.get(victim.id);
        if (spawn) ctx.physics.teleport(victim, spawn);
        snapCamera(victim); // the camera cuts there, even when the spawn is near
        h.current = h.max;
      } else {
        victim.destroy();
      }
    }
    const player = ctx.world.query(CharacterController, Health)[0];
    if (player) ctx.store.health = player.get(Health).current; // the HUD's bar shows it
  },
});
```

The HUD shows `ctx.store.health` with no more code: a `bar` with `"value": "health"` and `"max": 100` (`design-ui`), or a text `HP {health}`. A red copy of the bar with `UiShow {"value": "health", "compare": "below", "than": 30}` over it warns at low health. Without a HUD screen: `ctx.hud.set('health', 'HP ' + ctx.store.health, { position: 'top-right' })`.

`spawnPoints` is a module variable, and `onAdd` runs again for existing entities after a hot reload, so the spawn becomes wherever the player stood when you saved. For a respawn point that survives reloads, keep it in component fields, as in Fall respawn.

## Fall respawn

Falling below a height costs a life and puts the character back at its start, blinking and safe for a moment; losing the last life opens the `GameOver` screen. In Blender: `Respawn` on the player. The start is captured once into component fields, so hot reloads keep it. The lives are `ctx.store.lives`, which the HUD's hearts show (`design-ui`, Lives on the HUD) and Restart resets.

```ts
// src/components/respawn.ts
import { defineComponent, field } from '@gamestudio/engine/schema';

export const Respawn = defineComponent('Respawn', {
  description: 'Falling below fallY costs a life and puts it back at its start; the last life ends the run.',
  category: 'Gameplay',
  fields: {
    fallY: field.float(-12, { unit: 'distance', description: 'Respawn below this height, metres (runtime Y)' }),
    spawn: field.vec3([0, 0, 0], { description: 'Start position, runtime space (captured at load)' }),
    captured: field.bool(false, { description: 'Set once spawn holds the start position (runtime)' }),
    falls: field.int(0, { min: 0, description: 'Times it fell off (runtime)' }),
    blink: field.float(0, { min: 0, unit: 'time', description: 'Seconds of blinking left after a respawn (runtime)' }),
  },
});
```

```ts
// src/systems/respawns.ts
import { characterMotion, defineSystem, snapCamera } from '@gamestudio/engine';
import lose from '../../assets/sfx/lose.wav?url';
import { Respawn } from '../components/respawn';

export default defineSystem({
  name: 'RespawnSystem',
  query: [Respawn],
  start(ctx) {
    ctx.store.lives ??= 3; // the run's lives: kept across levels, cleared by Restart
  },
  onAdd(_ctx, e) {
    const r = e.get(Respawn);
    if (r.captured) return; // a hot reload keeps the captured start
    const p = e.object3d.position; // kinematic and dynamic bodies sit at the scene root: local = world
    r.spawn = [p.x, p.y + 0.05, p.z];
    r.captured = true;
  },
  update(ctx, entities) {
    for (const e of entities) {
      const r = e.get(Respawn);
      if (r.blink > 0) {
        r.blink = Math.max(0, r.blink - ctx.dt);
        e.object3d.visible = r.blink === 0 || Math.floor(r.blink * 8) % 2 === 0; // off and on, 4 times a second
      }
      if (e.object3d.position.y >= r.fallY || ctx.store.lives <= 0) continue;
      ctx.store.lives -= 1;
      r.falls += 1;
      if (ctx.store.lives === 0) { // the last life: the run is over
        const score = ctx.store.score ?? 0;
        if (score > ctx.save.get('best', 0)) ctx.save.set('best', score); // GameOver shows "Best {save.best}"
        ctx.audio.play(lose);
        ctx.after(1, (ctx) => ctx.ui.open('GameOver')); // a menu pauses everything: let the sound start first
        continue;
      }
      ctx.physics.teleport(e, r.spawn);
      characterMotion(e).verticalVelocity = 0; // or the fall speed carries over
      snapCamera(e); // the camera cuts to the start instead of sweeping up through the player
      r.blink = 1.5;
      ctx.log(`${e.name} fell off (${r.falls}), ${ctx.store.lives} lives left`);
    }
  },
});
```

- **The camera:** a `FollowCamera` or `SideCamera` cuts by itself when its target jumps more than 5 m in a frame, and `snapCamera(e)` makes a short teleport (a checkpoint near the edge, a hazard) cut too. Without a cut it would sweep from the pit up through the level and the player. It follows the player down while it falls; to have it hold at the edge instead, set the camera's `FollowCamera.target = null` once the player is a little below the lowest ground, and back to `e.id` before the teleport (`snapCamera(e)` then cuts: a new target alone pans).
- **Safe while blinking:** with Health and hazards in the game too, skip hits on a blinking player: `if (h.invulnerable > 0 || victim.tryGet(Respawn)?.blink) continue;`.
- **Game Over:** `GameOver` is a menu in `ui/ui.blend` with `Best {save.best}` and a `restart` button (`design-ui`, Lives on the HUD). Without one, `ctx.ui.open` warns: open a code-only `ctx.ui.screen('over', {...})` instead (Countdown and restart).
- **Test:** `playtest {"start": {"position": [0, -20, 0]}, "inputs": [{"at": 0, "key": "Enter"}], "duration": 1}` drops the player below `fallY` at once: `tracked` shows `falls` going to 1, and with the hearts on the HUD the last screenshot shows two.

**Checkpoints.** Give flags a `Checkpoint { index, radius, reached }` component. When the player's feet come within `radius` (horizontally, and within 3 m in height) of a flag with a higher `index` than the last one, overwrite `spawn` with the flag's position plus 0.1 m. Hazards then `teleport` to `spawn` and call `snapCamera(e)`, as above: a checkpoint may be closer than the 5 m that cuts on its own. Set `blink` there too, or a hazard still rolling through the checkpoint hits again at once.

## Bounce pad

Landing on top of the pad launches the player straight up, and the pad squashes as feedback. In Blender: `BouncePad` + a solid `Collider` (the player stands on it before the launch), with its top at most 1 m above the floor so that a jump reaches it. Launch height is v² / 40 with the controller's gravity of 20: 15 m/s rises 5.6 m.

```ts
// src/components/bounce-pad.ts
import { defineComponent, field } from '@gamestudio/engine/schema';

export const BouncePad = defineComponent('BouncePad', {
  description: 'Launches the player straight up when they land on top. Needs a solid Collider.',
  category: 'Gameplay',
  fields: {
    launchSpeed: field.float(15, { min: 0, description: 'Upward speed given to the player, m/s (height = v² / 2g)' }),
    radius: field.float(1, { min: 0.1, unit: 'distance', description: 'Horizontal reach from the pad centre, metres' }),
    bounces: field.int(0, { min: 0, description: 'Times it launched the player (runtime)' }),
  },
});
```

```ts
// src/systems/bounce-pads.ts
import { CharacterController, characterMotion, defineSystem } from '@gamestudio/engine';
import { Box3, Vector3 } from 'three';
import { BouncePad } from '../components/bounce-pad';

type Pad = { centre: Vector3; top: number; squash: number };
const pads = new Map<string, Pad>(); // rebuilt by onAdd, also after a hot reload
const box = new Box3();
const feet = new Vector3();

export default defineSystem({
  name: 'BouncePadSystem',
  query: [BouncePad],
  onAdd(_ctx, e) {
    box.setFromObject(e.object3d);
    pads.set(e.id, { centre: box.getCenter(new Vector3()), top: box.max.y, squash: 0 });
  },
  onRemove(_ctx, e) {
    pads.delete(e.id);
  },
  update(ctx, entities) {
    const player = ctx.world.query(CharacterController)[0];
    if (player) player.object3d.getWorldPosition(feet); // kit characters: the origin is at the feet
    for (const e of entities) {
      const pad = pads.get(e.id);
      if (!pad) continue;
      if (pad.squash > 0) {
        pad.squash = Math.max(0, pad.squash - ctx.dt);
        const t = pad.squash / 0.35; // 1 -> 0
        e.object3d.scale.set(1 + 0.25 * t, 1 - 0.35 * t, 1 + 0.25 * t);
      }
      if (!player) continue;
      const b = e.get(BouncePad);
      const dx = feet.x - pad.centre.x;
      const dz = feet.z - pad.centre.z;
      const onTop = dx * dx + dz * dz <= b.radius * b.radius && feet.y >= pad.top - 0.25 && feet.y <= pad.top + 0.3;
      const motion = characterMotion(player);
      if (!onTop || motion.verticalVelocity > 1) continue;
      motion.verticalVelocity = b.launchSpeed;
      b.bounces += 1;
      pad.squash = 0.35;
      ctx.hud.set('boing', 'Boing!', { at: e, offset: [0, 1.6, 0], duration: 0.8 });
    }
  },
});
```

## Countdown and restart

A global system (no `query`) with a time limit and a game over screen. Its Restart button and the R key call `ctx.restart()`: the start scene again with an empty `ctx.store` (score, lives) and no menus. `ctx.game.loadScene(ctx.game.scene.name)` only reloads the level and keeps `ctx.store`. A screen pauses the game, particles and sound effects included (music plays on), so open it a moment after the effect with `ctx.after`. With Blender screens, keep the time in `ctx.store.timeLeft` for a HUD text `{timeLeft:m:ss}` instead of `ctx.hud.set`, and `ctx.ui.open('GameOver')` instead of `ctx.ui.screen` (`design-ui`).

```ts
// src/systems/countdown.ts
import { CharacterController, defineSystem } from '@gamestudio/engine';

const LIMIT = 60; // seconds
let over = false;

export default defineSystem({
  name: 'CountdownSystem',
  phase: 'update',
  start(ctx) {
    over = false;
    ctx.hud.clear('timer');
  },
  update(ctx) {
    if (ctx.input.keyDown('KeyR')) void ctx.restart();
    if (over) return;
    const left = Math.max(0, LIMIT - ctx.time);
    ctx.hud.set('timer', `Time ${Math.ceil(left)}`, { position: 'top-center' });
    if (left === 0) {
      over = true;
      const player = ctx.world.query(CharacterController)[0];
      if (player) ctx.vfx.burst(player, 'smoke');
      ctx.after(1, (ctx) =>
        ctx.ui.screen('over', {
          title: 'Out of time!',
          subtitle: `Score ${ctx.store.score ?? 0}`,
          buttons: [{ label: 'Restart', action: 'restart' }],
        }),
      );
    }
  },
});
```

## Chaser enemy

It walks toward the player when the player is in range and turns to face them. In Blender: `Chaser` + `RigidBody {type: "kinematic"}` + `Collider {shape: "capsule"}` on a `character` kit, or on the armature of a `character_rigged` kit, which then plays its `Walk` and `Idle` clips (its capsule is sized from the skinned mesh). It moves through walls. To go around them, give it a `NavAgent` (`target`: the player) and put a `NavMesh` on the floor instead: no system needed (API.md §26).

```ts
// src/components/chaser.ts
import { defineComponent, field } from '@gamestudio/engine/schema';

export const Chaser = defineComponent('Chaser', {
  description: 'Walks toward the player within range. Needs RigidBody { type: kinematic } and a Collider.',
  category: 'AI',
  fields: {
    speed: field.float(2.5, { min: 0, description: 'Walking speed, m/s' }),
    range: field.float(10, { min: 0, unit: 'distance', description: 'Starts chasing within this distance, metres' }),
  },
});
```

```ts
// src/systems/chasers.ts
import { CharacterController, defineSystem } from '@gamestudio/engine';
import { Vector3 } from 'three';
import { Chaser } from '../components/chaser';

const WALK_CLIP_SPEED = 1.5; // m/s the character_rigged Walk clip's feet match
const target = new Vector3();
const toTarget = new Vector3();

export default defineSystem({
  name: 'ChaserSystem',
  query: [Chaser],
  phase: 'update',
  update(ctx, chasers) {
    const player = ctx.world.query(CharacterController)[0];
    if (!player) return;
    player.object3d.getWorldPosition(target);
    for (const e of chasers) {
      const c = e.get(Chaser);
      const pos = e.object3d.position; // kinematic: at the scene root, so local = world
      toTarget.subVectors(target, pos).setY(0);
      const distance = toTarget.length();
      const chasing = distance <= c.range && distance >= 0.8;
      if (ctx.animation.clips(e).length > 0) {
        ctx.animation.play(e, chasing ? 'Walk' : 'Idle', { fade: 0.2, speed: chasing ? c.speed / WALK_CLIP_SPEED : 1 });
      }
      if (!chasing) continue;
      pos.addScaledVector(toTarget.normalize(), Math.min(c.speed * ctx.dt, distance));
      e.object3d.lookAt(target.x, pos.y, target.z); // the Blender front (-Y) is +Z, which lookAt aims
    }
  },
});
```

## Interact key

Shows a prompt near interactable objects. E triggers the nearest one.

```ts
// src/components/interactable.ts
import { defineComponent, field } from '@gamestudio/engine/schema';

export const Interactable = defineComponent('Interactable', {
  description: 'The player can press E near it.',
  category: 'Gameplay',
  fields: {
    prompt: field.string('Press E', { description: 'HUD text when in reach' }),
    radius: field.float(2, { min: 0, unit: 'distance', description: 'Reach in metres' }),
    uses: field.int(0, { min: 0, description: 'Times used (runtime)' }),
  },
});
```

```ts
// src/systems/interactables.ts
import { CharacterController, defineSystem, type Entity } from '@gamestudio/engine';
import { Vector3 } from 'three';
import { Interactable } from '../components/interactable';

const playerPos = new Vector3();
const itemPos = new Vector3();

export default defineSystem({
  name: 'InteractableSystem',
  query: [Interactable],
  phase: 'update',
  start(ctx) {
    ctx.hud.clear('interact');
  },
  update(ctx, items) {
    const player = ctx.world.query(CharacterController)[0];
    if (!player) return;
    player.object3d.getWorldPosition(playerPos);
    let nearest: Entity | undefined;
    let best = Infinity;
    for (const e of items) {
      const d = e.object3d.getWorldPosition(itemPos).distanceTo(playerPos);
      if (d <= e.get(Interactable).radius && d < best) {
        best = d;
        nearest = e;
      }
    }
    if (!nearest) {
      ctx.hud.clear('interact');
      return;
    }
    const it = nearest.get(Interactable);
    ctx.hud.set('interact', it.prompt, { position: 'bottom-center' });
    if (ctx.input.keyDown('KeyE')) {
      it.uses += 1;
      ctx.log(`Used ${nearest.name} (${it.uses}x)`);
      // React here: toggle a light (nearest.object3d.visible), give an item, or open a door that has
      // a door_open clip: ctx.animation.play(nearest, 'door_open', { loop: false });
    }
  },
});
```

## Spawner

It spawns copies of a prefab on a timer and caps how many are alive. Spawned entities are cleaned up when the scene reloads. In Blender: `Spawner` on an empty where copies appear, and a prefab to spawn. For example, build a ball (`collectible` or `rock` kit) with `RigidBody` + `Collider {shape: "sphere"}`, then `asset_add {"objects": "Ball", "spawn_only": true}`: it moves the ball into a library and makes it spawnable in one call.

```ts
// src/components/spawner.ts
import { defineComponent, field } from '@gamestudio/engine/schema';

export const Spawner = defineComponent('Spawner', {
  description: 'Spawns copies of a prefab every few seconds.',
  category: 'Gameplay',
  fields: {
    prefab: field.string('Ball', { description: 'Prefab or library asset name (ctx.prefabs lists them)' }),
    interval: field.float(2, { min: 0.1, unit: 'time', description: 'Seconds between spawns' }),
    max: field.int(10, { min: 1, description: 'Most copies alive at once' }),
    timer: field.float(0, { min: 0, unit: 'time', description: 'Seconds since the last spawn (runtime)' }),
  },
});
```

```ts
// src/systems/spawners.ts
import { defineSystem, type Entity } from '@gamestudio/engine';
import { Vector3 } from 'three';
import { Spawner } from '../components/spawner';

const alive = new Map<string, Entity[]>(); // copies per spawner id
const at = new Vector3();

export default defineSystem({
  name: 'SpawnerSystem',
  query: [Spawner],
  phase: 'update',
  start() {
    alive.clear();
  },
  update(ctx, spawners) {
    for (const s of spawners) {
      const cfg = s.get(Spawner);
      cfg.timer += ctx.dt;
      if (cfg.timer < cfg.interval) continue;
      cfg.timer = 0;
      const copies = (alive.get(s.id) ?? []).filter((e) => e.alive);
      if (copies.length >= cfg.max) ctx.destroy(copies.shift());
      s.object3d.getWorldPosition(at);
      copies.push(ctx.spawn(cfg.prefab, { position: [at.x, at.y, at.z] }));
      alive.set(s.id, copies);
    }
  },
});
```

For one-off delays, `ctx.after(seconds, fn)` is simpler. Timers run in game time and are dropped when the scene reloads. `ctx.every(interval, fn)` started in `start` works as the spawn clock too; keep its handle and `cancel()` it at the top of `start`, which runs again after a hot reload.

**Random spawn points.** Use `ctx.random`, never `Math.random`: three.js and the audio system draw from `Math.random` too (every new object, spawned copy and sound), so with it a change to the player's route shifts where hazards appear, and a playtest that passed stops passing. `ctx.random` is the game's own stream, seeded by the playtest's `seed`:

```ts
// when spawning:
const x = at.x + ctx.random.range(-spread, spread);
const kind = ctx.random.pick(['Ball', 'Crate']);
if (ctx.random.chance(0.25)) ctx.spawn('Bonus', { position: [at.x, at.y, at.z] });
```

When one event spawns several things (a wave of enemies), draw all their choices when it starts, so other draws during it (shot spread, drops) don't move the later spawns. Or give the spawner a stream of its own, `const random = ctx.random.stream('spawns')` (seeded from the seed and the name), and draw from `random` instead: then nothing else moves its spawns.

**Rolling hazards** (boulders down a slope): give the prefab `RigidBody {type: "dynamic", linearDamping: 0.3, angularDamping: 0.3}` and `Collider {shape: "sphere"}`; a `convex` rock wobbles and stops. Push a new copy downhill once its body exists, in the next `update`: `ctx.physics.bodyOf(e)?.setLinvel({x: 0, y: 0, z: 4}, true)`. Remove copies after a lifetime or below a height. To hit the player, test the distance from the boulder's bounding-box centre (`new Box3().setFromObject(e.object3d).getCenter(v)`: the rock's origin is at its base, and it rolls) to the player's body centre, with a margin a little wider than the capsule (radius + 0.6 m), so the touch registers before the collision stops the player.

Tune a library prefab in place: `set_component {"asset": "Ball", "component": "RigidBody", "values": {"linearDamping": 0.2}}` (a background Blender writes the library in a few seconds; the level reloads it). Each library write is backed up first, and the result's `restore` call (`asset_library_restore`) takes it back.
