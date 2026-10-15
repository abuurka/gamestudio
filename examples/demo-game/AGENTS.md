# Demo game: Coin Courtyard

GameStudio's demo, a small third-person 3D platformer. Collect all six coins and use the bounce pad to reach the high ledge. Touching the lava or falling off respawns you. The level is built in Blender (`scenes/main.blend`), which you drive through the `gamestudio` MCP server. Gameplay is TypeScript on the GameStudio engine (three.js + Rapier). This folder is a workspace of the GameStudio monorepo.

These instructions are for whichever coding agent runs here, Claude Code or Codex. Both get the same `gamestudio` tools: `.mcp.json` starts the server for Claude Code and `.codex/config.toml` for Codex. If the tools are missing, tell the user instead of working around them: in Claude Code, `/mcp` shows the server; Codex loads `.codex/config.toml` only after the user chose **Trust and continue** the first time Codex started in this folder.

## Read the scene twin first

The twin is the cheapest overview of a level: one line per object (name, type, collection, parent, entity id, transform, size, mesh stats, materials, modifiers, kit params, component values, the library asset an instance shows), plus collections, libraries, the camera, lights and scene settings.

- `scenes/main.scene.json` is the twin as last saved (by `save` or `tools/build_level.py`), and reading it costs no tool call. `scene_twin` returns it live, with unsaved changes. Use one of them before `scene_summary`, which adds totals, the selection and prefabs.
- Component values list only the fields set on the object; the others are defaults (`list_component_types`). Positions are Blender space.
- Never edit it. Its diff shows what each save changed.

## The loop

0. **Inspect** first: `status`, the twin, then `get_object` / `list_objects`. Results are compact; `verbose: true` gives everything.
1. **Build** in Blender: `create_from_kit` (`list_kits`; `items` builds many in one call), `asset_place` for library props, `create_procedural` for forests, roads, rivers and cliffs (`procedural-world`), `run_python` when nothing fits (`gs.kits.geometry.MeshBuilder`; see `make-prop`). Then `apply_material`, and `set_transform` (`items` for many; `snap_to_ground: true` on uneven ground, `tilt: true` on slopes).
2. **Attach**: `add_component` (`components: {...}` attaches several at once) / `set_component` (`list_component_types` lists them all). Animated characters are the `character_rigged` kit, with components on the armature: `Animator` for an NPC, or the player components plus `Locomotion` for a rigged player (its capsule fits the skinned mesh; `animate-character`).
3. **Code**: `src/components/` (data) and `src/systems/` (logic).
4. **Play**: `play` exports the scene and starts the game.
5. **Playtest**: `playtest` with timed inputs returns screenshots, state and logs. Playtest times are game seconds and runs are deterministic (`playtest-and-fix`, Timing). `track: ["Player", "Coin*"]` with `sample_every` follows every copy over time without screenshots.
6. **Look**: `render_views` (`focus: true` shows a target in its surroundings; `compare: "<checkpoint id>"` shows before and after) and `game_screenshot`; `compare_views` checks that the game shows what Blender shows. Check the images yourself.
7. **Fix** and repeat. `undo` reverts the last scene edit. `checkpoint` before risky edits; `restore_checkpoint` rolls back.

Measure with `ground_height` and `raycast`; for other queries, `run_python` with `read_only: true` records no undo step and refuses changes.

The workflows are skills: `new-game-from-brief`, `blockout-level`, `make-prop`, `animate-character`, `add-mechanic`, `playtest-and-fix`, `stylize-scene`, `prepare-for-web`, `procedural-world` (geometry-nodes forests, roads, rivers, cliffs and villages), `design-ui` (menus and HUDs made in Blender) and `make-template`. Their one source, for every agent, is `.claude/skills/<name>/SKILL.md`: add or change skills there. GameStudio copies them to `.agents/skills/` for Codex; never edit that copy.

## What is in the level

| Objects | Components | Behaviour (system) |
|---|---|---|
| `Player` | `CharacterController`, kinematic `RigidBody`, capsule `Collider` | WASD/arrows move, Space jumps |
| `Camera` | `FollowCamera {target: Player}` | Third-person follow |
| `Coin1`–`Coin6` | `Coin {value}` | `CoinSystem` (`coins.ts`): spins coins, collects them within 1.2 m, shows "Coins n / total" and a win message |
| `BouncePad` | `BouncePad {strength}`, `Collider` | `BouncePadSystem`: sets `characterMotion(player).verticalVelocity` |
| `LavaStrip` | `KillZone` | `RespawnSystem`: respawns the player on touch or below y = −20 |
| `PlayerStart` | `PlayerStart` | The respawn point (floor level) |
| `Crate1`–`Crate4` | dynamic `RigidBody`, `Collider` | Pushable |
| `Crystal` | `Spin` | Decoration |
| `Ground`, `Platform`, `Ramp`, `HighLedge`, `Wall1`–`Wall4` | `Collider` | Static level geometry |

`src/lib/contact.ts` has the shared overlap helpers: `startedTouching`, `boundsOverlap`, `isStandingOn`.

## Layout and commands

| Path | Contents |
|---|---|
| `scenes/main.blend` | The level (the start scene). Use one `.blend` per level. |
| `tools/build_level.py` | Rebuilds `scenes/main.blend` from scratch (`npm run build:level` from the repo root). |
| `src/components/*.ts` | `defineComponent(...)` only. Import from `@gamestudio/engine/schema`. |
| `src/systems/*.ts` | One `defineSystem(...)` per file, as the default export. |
| `src/shaders/*.ts` | Game shaders: one `defineShader(...)` per file, attached to Blender materials with `set_material_shader`. |
| `AGENTS.md` | These instructions, for every coding agent. `CLAUDE.md` imports them for Claude Code (`@AGENTS.md`). |
| `.gamestudio/` | Generated: exports, `components.json`, session. Never edit it. |

- The engine API is `../../runtime/packages/engine/API.md` (or `engine_docs`). It is the complete API: if something isn't there, it doesn't exist.
- `npm run typecheck` runs in this folder. `npm run dev` starts the dev server on http://localhost:5173. It is often already running: don't start a second one, and don't stop it.
- `npm run build` writes `dist/`. Export the scene first.

## Gameplay essentials

- **Randomness:** `ctx.random.range/int/pick/chance`, never `Math.random` (three.js and audio share it, so seeded playtests would reshuffle).
- **Juice:** `ctx.vfx.burst(entity, 'pickup')` (`sparkle`, `dust`, `smoke`, `explosion`, `confetti`) in one line; a `ParticleEmitter` on an object for lasting effects; shader effects from the library, such as `InteractGlint` on the coins or `HitFlash` on a hit (`add-mechanic`).
- **Screens:** `ctx.ui.screen('title' | 'over' | 'win', {...})` and `ctx.ui.pauseMenu()`; they pause the game. Playtests press their buttons with `{"key": "Enter"}`. This demo's menus are these code-only screens; menus and HUDs can also be laid out in Blender, in `ui/ui.blend` (`ui_open`, `ui_add`; the `design-ui` skill), and a screen there takes the place of a code-only one of the same name.
- **Levels:** one level per file (`scenes/level2.blend` is `'level2'`), each with its own `Player`; `ctx.loadScene('level2', { keep: ['Player'] })` moves on, and `ctx.store` carries the score. `export_scene {"all": true}` exports every level; a playtest exports only the open one and always starts at the start scene.
- **Test what you track:** values a playtest should see (hit points, ammo, score) belong in component fields; `tracked[].changes` lists only those.

## Conventions

- **Blender:** metres, Z-up, front −Y. **Runtime:** Y-up; Blender −Y becomes +Z. Vector component fields (`FollowCamera.offset`) and TypeScript math are Y-up.
- **Names:** unique PascalCase names with an index for repeats (`Coin7`). Collections: `Level`, `Props`, `Pickups`, `Actors`.
- **Materials:** the existing ones (`Grass`, `Stone`, `Wood`, `Gold`, `Lava`...) or the shared presets through `apply_material`. Keep the bright, simple look.
- **Game shaders:** Blender owns shape, UVs, which surface is which and static colour; a game shader owns what moves, reacts to the game or is projected in world space (flowing lava, wind, water). Use the engine's shader library first: 33 shaders such as Water, Lava, FoliageSway, GrassWind, SnowCover, Dissolve, HitFlash, InteractGlint, SelectionOutline and Toon (`list_component_types` lists them, `{"type": "Lava"}` gives one's fields). `set_material_shader {"objects": ["LavaStrip"], "shader": "Lava"}` attaches one, and systems drive it with `ctx.shaders.set(coin, { strength: 1 })` (API.md §29). Write your own in `src/shaders/*.ts` (`defineShader`, patched with `patchShader` so it composes with lightmaps) only when the library lacks it. Never find materials by name in code, and judge the look from `game_screenshot`: Blender's viewport shows the Blender nodes.
- **Colour:** the game follows the scene's Color Management (Khronos PBR Neutral, exposure). Change the exposure in Blender, not in code.
- **Code:** components are PascalCase nouns in kebab-case files. Systems are `<Thing>System` in plural files. Systems find entities by component, not by name. Reuse the `contact.ts` helpers.
- **Jumps:** the player has `speed 6` and `jumpSpeed 7` (gravity 20). That gives a jump about 1.2 m high and about 4 m long. Keep gaps at or below 3 m.

## Hot reload

- Saving `src/systems/*.ts` reloads the system and keeps the game running. Module variables reset, so reset state in `start(ctx)`.
- Saving `src/components/*.ts` makes the dev server rewrite `components.json`, and Blender's component list follows. Saving `src/shaders/*.ts` does the same for game shaders and re-applies the shader in the running game.
- Transform and component edits in Blender stream live. New objects and geometry changes re-export and reload the scene.

## Verify before you say it's done

`validate_assets {}` reports no errors (it checks every mesh and every object with components), `render_views` looks right, `npm run typecheck` passes, `playtest` shows it working, `game_logs` has no errors, and you have called `save`. After a change, collecting all six coins must still be possible.

Playtest times are game seconds and runs are deterministic (`playtest-and-fix`, Timing): at `speed` 6, `{"at": 0, "key": "KeyW", "hold": 1}` moves 6 m. Write the critical path once, from the real start; `start` begins a run elsewhere, such as at the bounce pad.

If Blender crashes or hangs, the failed call's `hint` lists the ways back (`launch_blender` with `scene` or `checkpoint`, or `kill_unresponsive: true`). Pick one, then `save`.

## Don't

- Edit `.gamestudio/` or `dist/`.
- Edit `scenes/main.blend` through MCP **and** run `tools/build_level.py` without warning: the script rebuilds the level from scratch and discards MCP edits. To keep a change reproducible, also put it into `build_level.py`.
- Edit `ASSISTANT.md` (it belongs to the in-Blender assistant) or files outside this folder, unless you're asked to.
- Invent engine APIs, put logic in components, or move dynamic bodies through `object3d`.
- Delete or rebuild the user's work without a `checkpoint` first.
- Leave dangling references: `delete_objects` lists the fields it cleared (`cleared_references`, such as `FollowCamera.target`). Point them at something again.
