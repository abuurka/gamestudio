# Components and game logic

[Manual contents](README.md) · Previous: [Projects and templates](04-projects.md) · Next: [Assets and rendering](06-assets.md)

## Objects, entities, components and systems

A Blender object can become an entity in the exported game. Its components hold typed data, and systems act on entities with the components they need. For example, a pickup can have a points value, while a collection system detects the player, updates the score and removes the pickup from the running game.

| Concept | Example | Where you edit it |
|---|---|---|
| Object | A mesh named `BonusPickup` | Blender |
| Component schema | `Collectible` with a numeric `points` field | `src/components/collectible.ts` |
| Component values | `points = 5` on this pickup | Object Properties > Game |
| System | The rule that collects nearby pickups | `src/systems/collect.ts` |
| Runtime entity | This pickup in the running game | Through the engine API |

GameStudio assigns stable entity IDs so references survive ordinary authoring changes. Prefer component queries and entity references over repeatedly finding objects by name in every frame.

## Coordinates and units

Blender transforms use Z-up; runtime transforms and vector component fields use Y-up. For positions, the conversion is:

```text
Blender (x, y, z)  ->  runtime (x, z, -y)
Blender (2, 3, 4)  ->  runtime (2, 4, -3)
```

An object's normal Blender Transform panel remains in Blender coordinates. A component's vector field, a `ctx.spawn` position or a TypeScript vector is in runtime coordinates. Distances use metres, time uses seconds and runtime angles use radians.

This distinction matters for a follow-camera offset, a gravity vector and a side-scroller's depth lock. A runtime vertical offset is `[0, 2, 0]`; a runtime depth lock is `lockZ`.

## Built-in components

| Component | Purpose | Typical placement |
|---|---|---|
| `RigidBody` | Choose fixed, dynamic or kinematic physics | Objects that participate in physics |
| `Collider` | Collision or overlap shape | Ground, obstacles, characters and triggers |
| `CharacterController` | Built-in player movement | A player with a kinematic body and capsule collider |
| `FollowCamera` | Follow an entity with an offset | The game camera |
| `SideCamera` | Follow across a fixed screen plane | Side-scroller or top-down camera |
| `Spin` | Constant rotation | Decorative objects and simple pickups |
| `Animator` | Play clips or an animation graph | A rig or animated object |
| `Locomotion` | Pick gait clips from movement | A character with suitable animation clips |
| `AudioSource` | Attached or non-spatial sound playback | An emitter or music object |
| `ParticleEmitter` | Continuous or burst particles | An effects object |
| `PostEffects` | Bloom, vignette and grading settings | Commonly the camera |
| `NavMesh` | Configure walkable navigation | One configuration object in a level |
| `NavAgent` | Move along navigation paths | An NPC with kinematic physics and a capsule collider |

The [Engine API](../../runtime/packages/engine/API.md) lists every field, default and supported operation. Template components such as `Collectible`, `Checkpoint` or `Car` are project code, so their fields depend on the selected template.

## Physics recipes

| Object | Setup | Reason |
|---|---|---|
| Floor or wall | Collider, with no RigidBody or a fixed RigidBody | Remains stationary and blocks movement |
| Pushable crate | Dynamic RigidBody and box or convex Collider | Physics controls its motion |
| Player | Kinematic RigidBody, capsule Collider, CharacterController | Controller drives collision-aware movement |
| Trigger | Collider with Sensor enabled | Detects overlap without blocking |
| Moving platform | Kinematic RigidBody and Collider, moved by a system | Scripted motion can interact with dynamic objects |

Dynamic bodies are owned by physics. Move them through impulses, forces or `ctx.physics.teleport`; changing their visual transform directly can be overwritten by the next simulation step. Kinematic objects follow their authored or scripted transforms.

Collider shapes are sized from geometry bounds and scale. A sphere encloses its bounds; a capsule is upright in runtime Y. A mesh collider is useful for stationary complex terrain; a dynamic mesh collider falls back to a convex hull. Keep a player's collision body simple even when its visual model is detailed.

To create an invisible collision proxy, put a Collider on a mesh and set **Object Properties > Viewport Display > Display As** to **Wire** or **Bounds**. The game uses the mesh for collision while omitting it from the visible scene. A sensor plus a system is a useful trigger; adding a sensor alone does not implement its gameplay reaction.

For a side-scroller, combine `CharacterController.plane = side` with a suitable camera and lock depth where required. For top-down movement, use `plane = top`. Axis locks are in runtime coordinates.

## Create a custom component

The following complete example makes a **Turntable** component with configurable rotation. The engine already supplies Spin; this small example teaches how to create your own schema and system.

Create `src/components/turntable.ts`, or start with **GameStudio > New Component** and replace its generated schema:

```ts
import { defineComponent, field } from '@gamestudio/engine/schema';

export const Turntable = defineComponent('Turntable', {
  description: 'Rotates a decorative object around its local up axis.',
  category: 'Gameplay',
  fields: {
    speed: field.float(1, {
      min: -10,
      max: 10,
      description: 'Radians per second; negative reverses direction',
    }),
    active: field.bool(true),
  },
});
```

Component files are also evaluated by the registry builder in Node.js. Keep them limited to schema declarations and schema imports. Browser APIs, Three.js scene operations and game-side effects belong in system files.

Start the dev server or press Play, save the component, then use **Game > Reload Components** if the new entry has not appeared. Select a decorative mesh and add **Turntable** in Object Properties.

## Add the system

Create `src/systems/turntable.ts`:

```ts
import { defineSystem } from '@gamestudio/engine';
import { Turntable } from '../components/turntable';

export default defineSystem({
  name: 'TurntableSystem',
  query: [Turntable],
  phase: 'update',

  update(ctx, entities) {
    for (const entity of entities) {
      const settings = entity.get(Turntable);
      if (settings.active) {
        entity.object3d.rotateY(settings.speed * ctx.dt);
      }
    }
  },
});
```

The supplied templates import files directly under `src/components/`, `src/systems/` and `src/shaders/` using eager glob imports in `src/main.ts`. If you place files in nested directories or change that bootstrap, adjust the imports accordingly.

Press Play. Changing Speed changes rotation, a negative value reverses it and disabling Active stops it. Multiplication by `ctx.dt` makes the value a rate per second rather than a rate per rendered frame. Test this on a decorative object without a dynamic physics body or another system that also rotates it.

## System lifecycle

| Hook or phase | When it runs | Suitable work |
|---|---|---|
| `start(ctx)` | At system startup after a scene load/reset, and after hot reload | Initialize run data or register UI handlers |
| `onAdd(ctx, entity)` | When an entity starts matching the query | Set up per-entity state |
| `onRemove(ctx, entity)` | When an entity stops matching the query | Release per-entity state; account for removed components |
| `fixed` | Before fixed physics steps, normally at 60 Hz | Physics-related game rules |
| `update` | During each playing frame | General gameplay, input and effects |
| `late` | After update | Camera follow and work that depends on final positions |

Systems stop advancing while the game is stopped or paused. Menus can still draw and accept input. A query matches entities that have all the named components; omitting it creates a global system.

Module variables reset when a module hot-reloads. Store lasting gameplay state in components or `ctx.store`, and initialize it deliberately. `start` can run again, so avoid unintentionally adding duplicate listeners or resetting a score on every code save.

## Runtime state and level changes

Use these APIs inside a system hook or a callback that receives `ctx`:

```ts
// Run state persists across level changes.
ctx.store.score = (ctx.store.score ?? 0) + 10;

// Persistent state survives page reloads in this game's browser storage.
ctx.save.set('bestScore', ctx.store.score);

// The destination must have been exported as a level.
await ctx.loadScene('cave');
```

The last line requires an `async` function. A transition can preserve named entities with a `keep` option, but decide which player and inventory state should transfer before using it. Restart clears the run store and reloads the start scene; it is different from loading another level.

Use `ctx.after` and `ctx.every` for game-time timers so they follow simulation pause. Use `ctx.random` for reproducible gameplay choices rather than `Math.random` when tests need the same sequence.

## Animation and navigation

Author animation clips in Blender, export them and add Animator to the object carrying them. Select the clip, loop behavior, speed and fade. Locomotion can switch between appropriately named Idle, Walk, Run, Jump and Fall clips. For richer transitions, animation graphs are defined in TypeScript with `defineAnimationGraph`; see the animation section of the Engine API.

Navigation is built from solid fixed colliders when the level loads. Add NavMesh configuration, give each walking NPC a NavAgent with a kinematic RigidBody and capsule Collider, then drive destinations through the navigation API. A visible floor without a collider is not necessarily walkable navigation geometry. Test narrow passages against the configured agent radius.

Keep movement, animation and camera behavior coordinated: a respawn may need a physics teleport, the character's fall velocity reset and a camera cut. The Engine API provides `characterMotion` and `snapCamera` for that case.

## Validate code changes

Run this inside the game project after dependencies are installed:

```sh
npm run typecheck
```

Then play the affected mechanic, inspect the Console and restart from the beginning. Type checking catches schema and API mistakes; only a playtest confirms the intended game behavior. Missing imports, misspelled component names and scripts placed outside the bootstrap's glob are common reasons for a component that appears to do nothing.
