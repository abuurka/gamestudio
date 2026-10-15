# {{name}}

A 3D game made with GameStudio. Levels and assets are built in Blender, which you drive through the `gamestudio` MCP server. Gameplay is TypeScript on the GameStudio engine (three.js + Rapier). The game ships as a static web build.

These instructions are for whichever coding agent runs here, Claude Code or Codex. Both get the same `gamestudio` tools: `.mcp.json` starts the server for Claude Code and `.codex/config.toml` for Codex. If the tools are missing, tell the user instead of working around them: in Claude Code, `/mcp` shows the server; Codex loads `.codex/config.toml` only after the user chose **Trust and continue** the first time Codex started in this folder.

## Read the scene twin first

The twin is the cheapest overview of a level: one line per object (type, collection, parent, entity id, transform, size, mesh stats, materials, kit params, components, the library asset an instance shows), plus collections, libraries, cameras, lights and scene settings.

- `scenes/<level>.scene.json` (and `assets/<library>.scene.json`) is the twin as last saved; reading it costs no tool call. `scene_twin` returns it live, with unsaved changes. Read one of them before `scene_summary`.
- Components list only non-default values (`list_component_types` has the defaults). Positions are Blender space. Big levels summarise the non-gameplay objects; `get_object` and `list_objects` give details.
- `save` rewrites the file. Never edit it. Commit it: its diff shows what each save changed.

## The loop

0. **Inspect** before changing anything: `status`, the twin, then `get_object` / `list_objects`. Results are compact; `verbose: true` gives everything.
1. **Build** in Blender:
   - `create_from_kit` first (`list_kits`); `items` builds many objects in one call.
   - `asset_place` for props kept in an asset library.
   - `create_procedural` for forests, roads, rivers, cliffs and villages (`procedural-world`); `set_procedural` tweaks them.
   - `run_python` when nothing fits (`gs.kits.geometry.MeshBuilder`; see `make-prop`).
   - Then `apply_material` and `set_transform` (`items` for many; `snap_to_ground: true` on uneven ground, `tilt: true` on slopes).
2. **Attach** behaviour: `add_component` (`components: {...}` attaches several at once) and `set_component`. Use the built-ins first (`RigidBody`, `Collider`, `CharacterController`, `FollowCamera`, `Spin`, `Animator`, `Locomotion`, `AudioSource`); `list_component_types` lists everything.
3. **Code**: data in `src/components/`, logic in `src/systems/`, game shaders in `src/shaders/`.
4. **Play**: `play` exports the scene and starts the game.
5. **Playtest**: `playtest` with timed inputs returns screenshots, state and logs. Playtest times are game seconds and runs are deterministic (`playtest-and-fix`, Timing). `track: ["Player", "Enemy*"]` with `sample_every` follows spawned copies over time without screenshots.
6. **Look**: `render_views` for Blender (`focus: true`: a target in its surroundings; `compare: "<checkpoint id>"`: before and after), `game_screenshot` for the game, `compare_views` for both side by side. A material with a game shader looks right only in the game. Look at the images before you say something works.
7. **Fix**, then repeat. `undo` reverts the last scene edit. `checkpoint` before risky edits; `restore_checkpoint` rolls back.

Measure with `ground_height` and `raycast`. For other queries, use `run_python` with `read_only: true`: it records no undo step and refuses changes.

Workflows for these steps are skills: `new-game-from-brief`, `blockout-level`, `make-prop`, `animate-character`, `add-mechanic`, `playtest-and-fix`, `stylize-scene`, `prepare-for-web`, `procedural-world` for forests, roads, rivers, cliffs and villages built with geometry nodes, `design-ui` for menus and the HUD, and `make-template`, which saves the game as a reusable New Project template (`save_template`).

## Layout

| Path | Contents |
|---|---|
| `scenes/<level>.blend` | One level per file. `gamestudio.json` → `startScene` is the first one. |
| `ui/ui.blend` | The game's menus and HUD: one Blender scene per screen (Game UI, below). |
| `ui/locales/<code>.json` | Translations of the menus' texts, one table per language (`ui_translate`; the starter ships `fr.json`). |
| `assets/<library>.blend` | Asset libraries: one collection per reusable asset (`make-prop`, ASSET-LIBRARY.md). |
| `assets/sfx/*.wav` | Ready-made sound effects; use them before making new ones: `pickup`, `jump`, `damage`, `win`, `lose`, `click`, `whoosh`. In a system, `import pickup from '../../assets/sfx/pickup.wav?url'` (builds then include it) and `ctx.audio.play(pickup)`. |
| `assets/ui/*.png` | HUD icons, 128×128 on transparent: `heart`, `heart-empty`, `star`, `star-empty`, `coin`, `gem`, `key`; 9-slice art: `panel` (like the flat cards), `panel-solid`, `panel-framed`, `panel-light`, `button`, `slider-track`, `slider-fill`, `slider-knob`; other UI art goes here too (`design-ui`). |
| `assets/music/theme.ogg` | The background music, a 30 s loop that the level's `Music` object plays on the music channel. |
| `src/components/*.ts` | `defineComponent(...)` only: data, no logic. Import from `@gamestudio/engine/schema`. |
| `src/systems/*.ts` | One `defineSystem(...)` per file, as the default export. All behaviour lives here. |
| `src/shaders/*.ts` | The game's own shaders: one `defineShader(...)` per file, attached to Blender materials with `set_material_shader` like the engine's shader library (see Materials and colour). |
| `src/main.ts` | Entry point. Rarely needs edits. |
| `AGENTS.md` | These instructions, for every coding agent. `CLAUDE.md` imports them for Claude Code (`@AGENTS.md`), so put shared rules here. |
| `.claude/skills/<name>/SKILL.md` | The skills, the one source for every agent: add or change skills here. GameStudio copies them to `.agents/skills/` for Codex; never edit that copy. |
| `.gamestudio/` | Generated: exports, `components.json`, checkpoints, session. Never edit it. |

## Conventions

- **Blender space:** metres, Z-up, the front faces −Y. **Runtime space:** Y-up; Blender −Y becomes +Z. Vector component fields (such as `FollowCamera.offset`) and all TypeScript math use runtime Y-up.
- **Meshes:** origin at the base centre, rotation and scale applied, outward normals, a UV map. Kits already do this. `auto` colliders use the mesh's bounding box; use `mesh` for stairs, buildings and terrain.
- **Names:** unique PascalCase object names that say what the object is (`Player`, `Coin_03`, `Wall_North`). Use one collection per role: `Level`, `Props`, `Pickups`, `Actors`, `Lights`.
- **Copies:** a gameplay object that appears 3 or more times in a level (coins, crates, enemies), or in several levels, belongs in an asset library. Build one, give it its components, `asset_add {"objects": "Coin", "move": true}`, then `asset_place` the other copies. They are prefab instances: `set_component {"asset": "Coin", ...}` changes every copy, and `asset_make_local` frees one copy for a one-off edit. Whatever game code spawns: `asset_add {"spawn_only": true}`. Plain objects are for one-offs and for copies meant to differ (kit builds, each with its own `seed`). Scenery repeated dozens of times is a `create_procedural` scatter or array (`make-prop`, ASSET-LIBRARY.md; `procedural-world`).
- **Materials:** the shared presets through `apply_material` (stone, wood, metal, grass, plastic, emissive, ...) keep one style and few draw calls.
- **Code:** components are PascalCase nouns in kebab-case files (`bounce-pad.ts` → `BouncePad`). Systems are named `<Thing>System` and live in plural files (`coins.ts`). Systems find entities by component, not by name. A helper that takes `ctx` types it `SystemContext` (`import type { SystemContext } from '@gamestudio/engine'`).
- **Common setups:**
  - Player: `add_component {"objects": "Player", "components": {"RigidBody": {"type": "kinematic"}, "Collider": {"shape": "capsule"}, "CharacterController": {}}}`. The `character` kit's origin is at its feet: measure pickup and trigger distances from the body centre, about 0.9 m up.
  - Animated characters: the `character_rigged` kit; components go on the armature (`animate-character`). An NPC needs only `Animator` (`{"clip": "Wave"}`). A rigged player takes the three player components plus `Locomotion`, which plays Idle, Walk, Run, Jump and Fall with no code; its capsule fits the skinned mesh.
  - Static level geometry: `Collider` only. Triggers: `Collider {sensor: true}`. Camera: `FollowCamera {target: Player}`.

## Materials and colour

- **Blender owns** shape, UVs, which surface is which (the material slots) and static colour and roughness. **A game shader owns** anything that moves, reacts to the game, is projected in world space or isn't in glTF: wind, water, foam, triplanar scans, wetness.
- **The shader library first.** The engine ships 33 game shaders: Water, ShorelineFoam, Flow, Caustics, WetSurface, Lava; FoliageSway, GrassWind, FlagWave, SnowCover, MossOvergrowth, Ice; EnergyShield, Hologram, Dissolve, PortalVortex, ScannerPulse, HotMetal; Triplanar, InteriorMapping, ParallaxOcclusion, CarPaint, Iridescence, Gem; Petrification, GoldenTransmutation; SelectionOutline, XRaySilhouette, InteractGlint, HitFlash; Toon, Shine, Gradient (the last two on UI panels too). `set_material_shader {"objects": ["Pond"], "shader": "Water", "values": {"foam": 0.6}}` attaches one to the materials of those objects (`material` names one). The engine applies it to every mesh that draws the material, spawned prefab copies and reloaded levels included. `list_component_types` lists them in one line each; `list_component_types {"type": "Water"}` gives one's fields. Game code drives them: `ctx.shaders.set(enemy, { hitAt: ctx.time })` (API.md §29).
- **A shader of your own** only when the library lacks it: a `defineShader(...)` in `src/shaders/<kebab-name>.ts`, with typed `fields` (textures are `field.asset([...])` fields, never paths in code). A file there with a library shader's name replaces it, so to change one, copy its code there (API.md §29).
- **Never find materials by name in game code** (`material.name === 'Sand'`): a rename in Blender breaks it silently. Attach a game shader to the material instead.
- **Patch with `patchShader(material, key, edit)`**, never by setting `onBeforeCompile`, so patches compose with each other and with baked lightmaps. Animate with the shader context's `ctx.time` (game seconds), so playtests stay deterministic.
- **Judge** shape with `render_views`, and the look of a material with a game shader with `game_screenshot` or a `playtest`: Blender's viewport shows the material's Blender nodes.
- **Colour:** the game follows the scene's Color Management (view transform and exposure), so its colours match Blender's. New projects use Khronos PBR Neutral with no look. To make the game brighter or darker, change the scene's exposure in Blender (`run_python`: `C.scene.view_settings.exposure = 0.3`), never in code.

## Gameplay essentials

- **Randomness:** `ctx.random.range/int/pick/chance`, never `Math.random` (three.js and audio share it, so seeded playtests would reshuffle).
- **Juice:** `ctx.vfx.burst(entity, 'pickup')` (`sparkle`, `dust`, `smoke`, `explosion`, `confetti`) in one line; a `ParticleEmitter` on an object for lasting effects. Shader effects from the library: `HitFlash` on an enemy's material and `ctx.shaders.set(enemy, { hitAt: ctx.time })` on a hit; `Dissolve` and `{ startAt: ctx.time }` on death; `InteractGlint` on pickups; `SelectionOutline` on hover (`add-mechanic`).
- **Screens:** menus and the HUD are made in Blender, in `ui/ui.blend` (Game UI, below), or code-only with `ctx.ui.screen(...)` and `ctx.ui.pauseMenu()`. Menus pause the game, and playtests press their buttons with `{"key": "Enter"}`. The title shows once per page load, so **every playtest opens on the title**: start the inputs with `{"at": 0, "key": "Enter"}`. Restart (`ctx.restart()`) starts the run again with an empty `ctx.store` and skips the title. Open end screens a second after the win or lose effect (`ctx.after(1.2, (ctx) => ctx.ui.open('Win'))`): a menu pauses particles too.
- **Score and lives:** in `ctx.store` (`ctx.store.score ??= 0` in `start`): they survive level changes, and Restart clears them. Best scores that survive page reloads: `ctx.save`.
- **Look:** the `sky` kit's gradient and the world's mist (as fog) show in the game; `PostEffects` bloom with `bloomThreshold` about 2 makes only the brightest things glow (`stylize-scene`).
- **Levels:** one level per file (`scenes/level2.blend` is `'level2'`), each with its own `Player`; `ctx.loadScene('level2', { keep: ['Player'] })` moves on, and `ctx.store` carries the score. `export_scene {"all": true}` exports every level; a playtest exports only the open one and always starts at the start scene.
- **Test what you track:** values a playtest should see (hit points, ammo, score) belong in component fields; `tracked[].changes` lists only those.

## Game UI

Menus and the HUD are laid out in Blender and drawn by the game over the 3D view, without tone mapping or post effects. Code only reacts. The `design-ui` skill designs one end to end.

- **Where:** `ui/ui.blend`, one Blender scene per screen (a canvas). Templates ship theirs (the starter: `Title` with a turning 3D gem, Play and Options, `Pause` with Resume, Options and Restart, `Options` with Music and Sound sliders and Language, `Win`, `HUD`); `ui_open` creates the file in any project. Saving it exports the screens, and while it is open a running game shows every edit. `open_scene` goes back to a level.
- **Tools:** `ui_open` (`save: true` saves the open level first), `ui_list` (every element with its rectangle in px: read it first), `ui_canvas` (a screen and its settings), `ui_add` (`text`, `image`, `panel`, `button`, `bar`, `group`, `icons`, `slider`, `toggle`, `model`; `"visible": false` starts one hidden for `ctx.ui.show`), `ui_set`, `ui_remove`, `ui_strings` and `ui_translate`; `add_component` and `set_component` for the rest (`UiShow`, `UiText.fit`). In `run_python` it is `gs.gameui`.
- **Pixels:** a 1920×1080 frame, origin top-left, y down; `at` is a box's centre, or the point a text aligns around. Keep 48 px from the edges, and one font and one accent (focus is `#FFD400`). Translucent colours mix in linear light, as in Blender: alpha `F2`–`FF` looks solid. On screens of another shape a top-level element keeps its distance to its `anchor` (`top-left`, `center`, ...); children follow their parent.
- **Screens (`ui_canvas`):** `kind` `menu` (one at a time, takes the keys, pauses) or `hud` (under the menus, mouse only); `open` `firstStart` (title), `start` (HUD) or `manual`; `pause_menu: true` for the Escape and P menu; `back` for what Escape does; `focus` for the button selected first; `backdrop`; `transition` (`{"open": "pop", "close": "fade"}`, the starter's) animates opening and closing.
- **Buttons need no code** for `action` `close`, `resume`, `restart`, `previous`, `open` (`target`: a screen), `load` (`target`: a level) and `locale` (the next language; label it `{ui.localeName}`). Otherwise `action: "event"`: after its action, every press calls the handler a system's `start` registers with `ctx.ui.on(name, (ctx) => ...)`, `name` being an `event` button's `target`, else its object name.
- **Live values:** text bodies bind `{score}` (`ctx.store.score`), `{player.name}` and `{save.best}` (`ctx.save`), with formats such as `{score:000}` and `{time:m:ss.0}`; a `bar` with `value: "health"` fills by itself, an `icons` row with `value: "lives"` shows a heart per life, and `UiShow` shows an element while a value holds. Values exist only while the game plays: a stopped `game_screenshot` shows `Score `. Code does the rest: `ctx.ui.open('Win')`, `ctx.ui.close()`, `ctx.ui.text`, `show` / `hide`, `bar` (engine `API.md` §22).
- **Settings need no code:** a `slider` or `toggle` writes its value path as the player changes it: `audio.music` or `audio.sfx` (volumes the game saves for the player), `save.<key>` or a store key. **Sound:** `sfx` (the default) pauses with the game, `music` (`AudioSource.channel`, as on the level's `Music` object) plays on under menus, button sounds always play.
- **3D models and effects:** `ui_add` kind `model` shows a kit, a library asset or an object, lit, in a view of its own (`spin`, `view`, `clip`); `"source": "scenes/main.blend:Player"` links the level's player, so it follows the level. A panel's `shader` takes `Shine` or `Gradient` (colours `"#RRGGBB"`, angles in radians), the only game shaders that work on flat UI. Blender shows neither as the game does: check with `game_screenshot`.
- **Languages:** each table in `ui/locales/` translates the texts as written. After adding or rewording a text, `ui_strings {"locale": "fr"}` lists what is missing and `ui_translate` adds it; untranslated texts show as written. Text from code goes through `ctx.ui.translate('...')`.
- **Check:** `game_screenshot {"ui": "Title"}` opens a screen in the game and returns it as drawn, with its buttons and texts; it stays open until `stop`. A `playtest`'s `final.ui` has `open` (a closed menu stays for its 0.25 s close), `texts` (`{"HUD/Score": "Score 3"}`), `hidden`, `locale` and `buttons`, whose x/y are px of `final.ui.size`. Playtests start in the browser's language: `"set": {"save": {"gs.locale": "fr"}}` plays in French. In `ui/ui.blend`, the 3D View's Game toggle in camera view shows the user the screen as the game draws it.
- **Code-only screens** (`ctx.ui.screen(name, {title, buttons})`) are for quick prototypes; a Blender screen of the same name takes their place. `ctx.hud` labels hide while a menu is drawn.

## Engine API

`node_modules/@gamestudio/engine/API.md` (also available through `engine_docs`) is the complete API. Read it before writing gameplay code. Anything it does not list does not exist.

## Hot reload

- **Systems:** saving `src/systems/*.ts` reloads the system in the running game and keeps the game's state. Module-level variables reset, so keep durable state in components or rebuild it in `start(ctx)`.
- **Components:** saving `src/components/*.ts` makes the dev server rewrite `.gamestudio/components.json`, and Blender's component list follows. Before the first `play` or `playtest` (no dev server yet), run `npm run registry` so `add_component` knows a new component.
- **Game shaders:** saving `src/shaders/*.ts` applies the new version to its materials in the running game, without reloading the level, and updates Blender's shader list the same way (`npm run registry` before the first `play`, for `set_material_shader`).
- **Scene edits:** transform and component edits in Blender stream into the running game. New objects and geometry changes re-export and reload the scene.

## Verify before you say it's done

- `validate_assets {}` reports no errors. It checks every mesh and every object with components.
- `render_views` images look right. Look at them.
- `npm run typecheck` passes.
- `playtest` shows the mechanic working, and `game_logs` has no errors. Playtest times are game seconds and runs are deterministic (`playtest-and-fix`, Timing), so prove the critical path with one run from the real start.
- `save` the `.blend`.

If Blender crashes or hangs, the failed call's `hint` lists the ways back (`launch_blender` with `scene` or `checkpoint`, or `kill_unresponsive: true`). Pick one, then `save`.

## Don't

- Edit anything in `.gamestudio/` or `dist/`. Both are generated.
- Invent engine APIs or guess component fields. Check `API.md` and `list_component_types`.
- Put logic, DOM access or three.js objects in `src/components/`.
- Move dynamic bodies through `object3d`. Use `ctx.physics.teleport` or forces.
- Look up or patch materials by name in game code, or set the renderer's tone mapping or exposure. Use a game shader and the scene's Color Management.
- Use `run_python` when a dedicated tool does the job, or `bpy.ops` when `bpy.data` or `bmesh` can (Blender 5.x dropped some older calls).
- Delete or rebuild the user's work without a `checkpoint` first.
- Leave dangling references: `delete_objects` lists the fields it cleared (`cleared_references`, such as `FollowCamera.target`). Point them at something again.
- Put several levels in one `.blend`, or copy the same asset into several levels.
