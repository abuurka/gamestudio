---
name: add-mechanic
description: Add a gameplay mechanic to this GameStudio game end to end. Writes a data component and a logic system in TypeScript, attaches the component to objects in Blender, adds feedback (sounds, particles, shader effects such as hit flashes, dissolves and outlines), then playtests until it works. Use when the user wants new behaviour, such as pickups, doors, moving platforms, enemies, damage, timers, triggers, scoring, spawning or abilities, or asks to "make X do Y".
argument-hint: "[mechanic]"
---

# Add a mechanic

A mechanic is three things: **data** (a component on objects), **rules** (a system), and **feedback** the player can see or hear. Ship all three, then prove it with a playtest.

## 1. Specify it in four lines

- **Data:** which objects carry what fields, with units. For example `MovingPlatform { offset: vec3 m, period: s }`.
- **Rule:** when → what, in one sentence each.
- **Feedback:** what the player sees or hears when it happens.
- **Test:** the input sequence that proves it works.

## 2. Reuse before you write

- `list_component_types`, and read the existing `src/components/` and `src/systems/`. Extend what is there rather than duplicating it.
- Built-ins:
  - `Spin` rotates.
  - `CharacterController` moves the player. `characterMotion(player).verticalVelocity = 12` launches the player.
  - `Collider {sensor: true}` makes a trigger.
  - `RigidBody` + `Collider` give physics props.
  - `Animator` plays a Blender clip by name; `AudioSource` plays a sound file from the project.
  - `NavAgent` walks an enemy or NPC to its `target` entity (or `ctx.nav.moveTo(e, point)`) around walls and props; the level needs a `NavMesh` on any one object (the floor). `ctx.nav.path(from, to)` gives the waypoints.
- Engine services (`API.md`): `ctx.spawn('<prefab>', {position})` creates copies of a prefab collection or library asset; `ctx.after(s, fn)` / `ctx.every(s, fn)` are game-time timers; `ctx.animation.play`, `ctx.audio.play`, `ctx.hud.set` / `bar`.
- **Randomness:** `ctx.random.range/int/pick/chance`, never `Math.random`. three.js and audio draw from `Math.random`, so a seeded playtest reshuffles whenever anything else changes.
- **Menus, HUD and levels:** screens made in Blender, in `ui/ui.blend` (the `design-ui` skill). A `HUD` text bound to a store value (`Keys {keys}`), a `bar` (`value: "health"`) or an `icons` row (hearts: `value: "lives"`) shows a mechanic's state with no code; `ctx.ui.open('GameOver')` opens a menu (it pauses the game properly), `ctx.ui.on('<button>', ...)` reacts to a button, and text code puts on a screen goes through `ctx.ui.translate('...')` so other languages get it. `ctx.ui.screen(...)` is the code-only quick version, and `ctx.hud` labels (`+1` over a pickup) hide while a menu is drawn. HUD icons ship in `assets/ui/` (hearts, stars, a coin, a gem, a key; `design-ui`, Lives on the HUD). `ctx.loadScene('level2', { keep: ['Player'] })` changes level; `ctx.store` holds what carries over (score, lives). See `new-game-from-brief`, steps 3 and 6.
- **2.5D (side-scroller, top-down):** `CharacterController {plane: 'side'}` runs left and right along the screen and jumps, and its depth never changes (`'top'`: up, down, left and right on the screen under a camera looking down). Give dynamic props `RigidBody {lockZ: true}` (game Z is Blender Y, the depth of a front-view level) so they stay in the plane. On an orthographic camera in front of the level (rotation 90°, 0, 0), `SideCamera {target: "Player", bounds: "LevelBounds"}` follows with a dead zone and look-ahead and keeps the view inside the level (a Cube empty scaled over it). Your own movers change only the plane's two axes; a kinematic body's locked axis is put back. Setup and limits: API.md §27.
- **A prefab to spawn:** build and tune the object, then `asset_add {"objects": "<Name>", "spawn_only": true}` moves it into a library and makes it spawnable in one call. Tune it later in place with `set_component {"asset": "<Name>", "component": ..., "values": ...}`. Library writes are backed up, and the result's `restore` (`asset_library_restore`) takes one back.
- Tested patterns for triggers, pickups, moving platforms, health, falls and lives, timers, chasers, interaction and spawners are in [PATTERNS.md](PATTERNS.md).

## 3. Write the code

Read the engine's `API.md` (its path is in AGENTS.md) or `engine_docs` for every API you use. If it isn't listed there, it doesn't exist.

1. **Component:** `src/components/<kebab-name>.ts`. Contains only `defineComponent` and imports from `@gamestudio/engine/schema`. Use PascalCase names and camelCase fields, and give every field a `description` with its unit. Use `min`/`max` where they make sense.
2. **System:** `src/systems/<plural-kebab>.ts`, with one `export default defineSystem({...})` named `<Thing>System`.
   - Set `query: [YourComponent]`.
   - Choose `phase`: `update` for game logic, collisions and input edges; `fixed` only for physics forces; `late` for cameras.
   - Reset per-run state in `start(ctx)`, and per-entity setup in `onAdd`.
3. **Feedback**, one line each:
   - a clip: `ctx.animation.play(door, 'door_open', { loop: false })`. Prop clips (`door_open`, `pulse`, `spin`, `bob`...) are generated in Blender (`animate-character`, section 3);
   - a sound: new projects ship `assets/sfx/*.wav` (`pickup`, `jump`, `damage`, `win`, `lose`, `click`, `whoosh`); use those first. In a system, `import pickup from '../../assets/sfx/pickup.wav?url'` and `ctx.audio.play(pickup)` (builds include it). Or name the file in an `AudioSource {clip, spatial}` on the object and play it with `ctx.audio.play(entity)`. A one-shot finishes even if the entity is destroyed right after. Sounds play on the `sfx` channel, which pauses with the game (the Options screen's Sound slider sets its volume); music is `AudioSource {channel: "music"}`, which plays on under menus;
   - juice in one line: `ctx.vfx.burst(entity, 'pickup')` (also `'sparkle'`, `'dust'`, `'smoke'`, `'explosion'`, `'confetti'`) on pickups, hits, landings and wins. For a lasting effect (a fountain, a torch, a portal), add a `ParticleEmitter` to the object in Blender;
   - text: `ctx.hud.set('pickup', '+1', { at: [x, y, z], color: '#ffd400', duration: 0.8 })`;
   - a shader effect: a library shader on the object's material (`set_material_shader`), driven with `ctx.shaders.set` (table below), never a system that finds the material by name.
4. `npm run typecheck` must pass before you touch Blender.

**Shader feedback.** The engine's shader library has the usual effects ready. Put the shader on the material in Blender, then one line in the system starts the effect. Pass the entity to change only that object (it gets its own copy of the material); pass a material name to change every object that draws it.

| When | Shader on the material | In the system |
|---|---|---|
| It takes damage | `HitFlash` | `ctx.shaders.set(enemy, { hitAt: ctx.time })` |
| It dies | `Dissolve` | `ctx.shaders.set(enemy, { startAt: ctx.time }); ctx.after(1.5, () => ctx.destroy(enemy));` (1.5 s is its `duration`) |
| It spawns in | `Dissolve` with `reverse: true` | `ctx.shaders.set(e, { startAt: ctx.time })` right after `ctx.spawn` |
| The player is near a pickup or lever | `InteractGlint`, `strength` 0.2 | `ctx.shaders.set(item, { strength: near ? 1 : 0.2 })` |
| Hover, selection, lock-on | `SelectionOutline`, `strength` 0 | `ctx.shaders.set(target, { strength: 1 })`, and 0 when it ends |
| A scan ability | `ScannerPulse` on the ground's material, `interval` 0 | `ctx.shaders.set('GS_Ground', { origin: [p.x, p.y, p.z], startAt: ctx.time })` |
| A shield is hit | `EnergyShield` | `ctx.shaders.set(shield, { hitPoint: [x, y, z], hitAt: ctx.time, charge: hp / maxHp })` |
| Allies or loot behind walls | `XRaySilhouette` | `ctx.shaders.set(ally, { pingAt: ctx.time })`; `{ strength: 0 }` hides it |
| A curse or a freeze | `Petrification` (`progress` 0) or `Ice` (`freeze` 0) | `ctx.shaders.set(victim, { progress: t })` (Ice: `{ freeze: t }`) each frame while it spreads |

- `hitAt`, `startAt` and `pingAt` hold a game time: one call starts the effect and the shader plays it out, nothing per frame. They start off. Fields like `progress` and `strength` are yours to steer; set their start value in Blender (the table says which), because some default to full effect.
- `list_component_types {"type": "HitFlash"}` gives a shader's fields. Positions are runtime Y-up, like `ctx` positions.
- One shader per material: an enemy that should flash and dissolve needs a project shader doing both (copy the two into `src/shaders/`, API.md §29), or the effects on different material slots.
- A playtest sees shader values only in screenshots (`tracked` lists component fields): take one just after the trigger.

## 4. Attach in Blender

1. The dev server regenerates `.gamestudio/components.json` when you save a component file. Check that `list_component_types {"type": "<Name>"}` finds the new component. If it doesn't, run `npm run registry`.
2. `add_component` with `objects` (one name or a list) and the `component` with its `values` (only the non-default ones), or `components: {"Name": {...}, ...}` for several. Entity fields take an object name (`{"target": "Player"}`). Vector fields are in runtime Y-up space.
3. Add the physics the rule needs:
   - A trigger needs `Collider {sensor: true}`.
   - Anything that moves needs `RigidBody {type: "kinematic"}` (moved by code) or `{type: "dynamic"}` (moved by physics).
4. Use `get_object` to confirm the components and values.

## 5. Prove it

1. `playtest` with the input sequence from step 1 (it exports first). `track` the objects involved, and take screenshots before, during and after the event.
2. `tracked` shows each field that changed and when (`changes`), and the positions; `logs` has no errors or warnings from your system. Add a temporary `ctx.log(...)` at the key moment if you need more proof, and remove it afterwards.
3. Look at the screenshots. The feedback must be visible.
4. Iterate. Saving a system hot-reloads it; pass `export: false` when only code changed.

## Verification gates

- `npm run typecheck` passes.
- The playtest shows the rule firing and its feedback, and `game_logs` is clean.
- Nothing else broke: the player still moves and the existing mechanics still work.
- `save` the `.blend`.

## Common failures

| Symptom | Fix |
|---|---|
| The component doesn't appear in Blender | Errors in the component file (check typecheck and `game_logs`), logic or imports beyond `@gamestudio/engine/schema` in it, or the dev server isn't running. `npm run registry` regenerates the list. |
| The system never runs | The file isn't in `src/systems/`, the `query` names a component no object has, or the game is paused or stopped. Check that `start` logs once. |
| Collision events never fire for the player | `CharacterController` keeps a 2 cm skin and makes no contact events. Use a `sensor` collider on the other object, or a distance or bounds check (see PATTERNS.md). |
| `e.get(X)` throws | The entity lacks X. Use `e.tryGet(X)` or `e.has(X)`. |
| State resets on save | Module variables reset on hot reload. Keep state in component fields, or rebuild it in `start`. |
| A dynamic body ignores position changes | Physics owns dynamic bodies. Use `ctx.physics.teleport(e, {x, y, z})` or impulses through `ctx.physics.bodyOf(e)`. |
| After a respawn the camera sweeps across the level, or through the player | Cameras smooth toward a target that moved less than 5 m in a frame. After `ctx.physics.teleport`, call `snapCamera(player)` (from `@gamestudio/engine`): the camera cuts there (PATTERNS.md, Fall respawn). |
| An object moves the wrong way | Runtime is Y-up and Blender is Z-up. Blender +Z is runtime +Y, and Blender −Y (front) is runtime +Z. |
| `ctx.spawn` throws an unknown name | The prefab isn't in the level. `set_prefab` its collection, or `asset_spawnable` the library asset, then re-export. If the message lists it as `Name [library.blend]`, a local collection with the same name is still in the level (an empty source left behind): remove it with `run_python` and export again. |
| The win message is still showing after a restart | Clear the HUD in `start(ctx)`. |
