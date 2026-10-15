# Demo game: conventions for the AI assistant

A small 3D platformer: collect every coin, use bounce pads, and avoid kill zones. Scenes are authored in Blender (`scenes/*.blend`), and gameplay is TypeScript in `src/`.

**Before using any engine API, read `node_modules/@gamestudio/engine/API.md`.** It is the complete API. Never invent functions, fields or options that it doesn't list.

## Layout

| Path | Contents |
|---|---|
| `src/components/*.ts` | `defineComponent(...)` only. Components are data: no logic, no DOM, no Three.js objects. Import from `@gamestudio/engine/schema`. |
| `src/systems/*.ts` | One `defineSystem(...)` per file, exported as the default export. All behaviour lives here. Import from `@gamestudio/engine`. |
| `src/shaders/*.ts` | Game shaders: one `defineShader(...)` per file, for Blender materials that move or react in the game. Never look materials up by name in code. |
| `src/lib/*.ts` | Shared helper functions for systems (`contact.ts`: overlap tests). |
| `src/main.ts` | Entry point: registers components, systems and game shaders and calls `createGame`. Rarely needs edits. |
| `gamestudio.json` | Project config (start scene, link ports). |
| `.gamestudio/` | Generated. Never edit it by hand. |

## Naming

- Components: PascalCase noun (`BouncePad`), in a kebab-case file (`bounce-pad.ts`). Fields are camelCase with units in the description (`strength: field.float(12, { description: 'Launch speed in m/s' })`).
- Systems: `<Thing>System` (`CoinSystem`), in a plural kebab-case file (`coins.ts`). System names must be unique.
- Prefer one concept per component. Reuse the built-in components (`RigidBody`, `Collider`, `CharacterController`, `FollowCamera`, `Spin`) before adding new ones.

## Gameplay in this project

- The player is the entity with `CharacterController` (plus a kinematic `RigidBody` and a capsule `Collider`).
- `Coin { value }`: `CoinSystem` spins coins, collects them on touch or within 1.2 m, and shows "Coins n / total" and a win message on the HUD.
- `BouncePad { strength }`: `BouncePadSystem` sets `characterMotion(player).verticalVelocity` when the player stands on a pad.
- `KillZone {}`: `RespawnSystem` respawns the player when they touch one or fall below y = −20.
- `PlayerStart {}`: optional floor marker for respawns. Without one, the player respawns where they started.

## Rules

- Coordinates are Y-up and in metres. Angles are radians.
- Move kinematic and fixed objects through `e.object3d`. Move dynamic bodies with `ctx.physics.teleport` or Rapier forces.
- Read `ctx.physics.collisions` in `update`/`late` systems.
- Reset per-run state in a system's `start(ctx)`. Module variables reset on hot reload.
- Keep systems small and readable, and comment intent that isn't obvious.
- After editing, `npm run typecheck` must pass.
