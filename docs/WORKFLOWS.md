# Making games with GameStudio and your coding agent

GameStudio turns Blender into a game editor. With a coding agent connected (Claude Code or Codex in one click, or any other MCP client), you can describe what you want, and the agent builds the level and assets in your open Blender, writes the gameplay code, plays the game, looks at the result and fixes problems. You watch it happen, and you can step in at any time. The examples here use Claude Code, the default; where Codex differs, it says so.

```
 you ──► your agent  ──MCP──► gamestudio MCP server ──► Blender (GameStudio extension)
              │                        └──────────────► running game (dev server, live link)
              └── edits src/*.ts in your project folder
```

## Setup

1. Install **Blender 5.2+** with the GameStudio extension. Two coding agents install with one click. **Claude Code**: when it is missing, **GameStudio › Open in Claude Code** offers *Install Claude Code (about 250 MB)* and runs Anthropic's official installer (the command from [code.claude.com/docs/en/setup](https://code.claude.com/docs/en/setup), into your user folder, no administrator rights), with the progress in the status bar; then it carries on opening the project. **Codex**, OpenAI's coding agent, works the same way from **GameStudio › Open in Codex** (*Install Codex*, about 160 MB: OpenAI's standalone installer from [developers.openai.com/codex/cli](https://developers.openai.com/codex/cli), into your user folder, no administrator rights). The **Coding agent** preference (Claude Code by default) picks the one the launcher, the welcome and the top bar open. GameStudio sets up the rest itself the first time you open or play a game: Node.js when you have no 22.12+, its game packages, and a game browser when there is no Chrome, Edge or Chromium. It asks once, says how much it downloads and where it goes (the extension's folder in your Blender profile), and shows the progress in the status bar.
2. Create a project: **New Project** in Blender. Every new project already contains:
   - `AGENTS.md`: the project rules the coding agent (Claude Code or Codex) reads in every session, and `CLAUDE.md`, which imports them for Claude Code (`@AGENTS.md`) and adds the few notes only Claude Code needs;
   - `.mcp.json`: connects Claude Code to the `gamestudio` MCP server (Open in Codex adds `.codex/config.toml`, the same for Codex);
   - `.claude/skills/`: step-by-step workflows (copied to `.agents/skills/` for Codex);
   - `.claude/settings.json`: Claude Code's settings, which enable the server and pre-approve its read-only tools, play controls, checkpoints and the scene edits that are one undo step each.
3. Open the project's level in Blender. The extension's agent bridge starts with Blender. If Blender isn't running, the agent can start it (`launch_blender`), with a window or headless.
4. Start your agent in the project. **GameStudio › Open in Claude Code** opens a terminal in the project with Claude Code running (the first time, it asks you to log in and to trust the folder). Or start `claude` in the project folder yourself, from any terminal, VS Code or the desktop app: the project's `.mcp.json` works there too. `/mcp` shows whether the `gamestudio` server connected. Ask "what's the status?" to check that the agent can see Blender and the game.
   **GameStudio › Open in Codex** does the same with Codex. The first time, Codex asks you to sign in (with your ChatGPT account, or an OpenAI API key) and whether to trust the folder: choose **Trust and continue**, because Codex reads the project's `.codex/config.toml`, where the `gamestudio` server is, only in a trusted folder. `/mcp` in Codex lists its servers too.
   Any other MCP client: start the project's MCP server from it and give it `AGENTS.md` and the skills ([MCP.md](MCP.md#other-agents)).

What Open in Claude Code does, in order: installs Claude Code if it is missing (after you click Install), connects the project to Claude Code when it is not yet (adds the missing `AGENTS.md` and `CLAUDE.md`, `.claude/`, `.mcp.json` and the `@gamestudio/mcp` package; your own files are never changed), turns the agent bridge on, lets GameStudio finish its setup, and opens the terminal. The terminal runs `.gamestudio/claude-code.cmd` (Windows), `.command` (macOS) or `.sh` (Linux), a small script it writes into the project: it goes to the project folder, puts GameStudio's Node.js first on `PATH`, and starts `claude`. Windows uses Windows Terminal, or Command Prompt without it; macOS opens Terminal with `open` (no Automation permission needed); Linux uses the desktop's terminal (`x-terminal-emulator`, GNOME Terminal, Konsole, xterm). To use another terminal, set **Terminal command** in the extension's preferences (Coding agent): on macOS just the app's name (`iTerm`, `Warp`), anywhere a command with `{project}`, `{agent}` (the agent's program; `{claude}` works too) or `{script}` (the launcher script), for example `kitty --directory {project} {script}`.

Open in Codex goes the same way and connects the project to Codex: `AGENTS.md` (the instructions; a project whose instructions are only in `CLAUDE.md` gets them moved there if you tick *Move the instructions to AGENTS.md*, else a marked copy), `.agents/skills/` (a copy of `.claude/skills/`, which Codex reads; edit the skills in `.claude/skills/`, and GameStudio copies them again when the project opens; folders there without a `.gamestudio-copy.json` are yours and never touched) and `.codex/config.toml` with `[mcp_servers.gamestudio]` (the same command and args as `.mcp.json`; the rest of the file is left as you wrote it). Its launcher script is `.gamestudio/codex.cmd`, `.command` or `.sh`, and it starts `codex`.

`.mcp.json` starts the MCP server with `node` when a Node.js 22.12+ is on your computer's `PATH`, else with the full path of the Node.js GameStudio set up, so it works wherever Claude Code starts. GameStudio checks it when the project opens, after its setup and before Claude Code opens, and only writes it when something changed. A path from another computer (a project shared through git) is repaired when the project opens. `.codex/config.toml` is kept the same way, and Save as Template turns both back into `node`.

The demo (`examples/demo-game`) is set up the same way. [MCP.md](MCP.md) documents every tool.

## A session

Describe the outcome, not the steps:

> Make a small game where a fox collects berries on floating islands before the sun sets. Keep it cute.

The agent follows a loop (the project's `AGENTS.md` teaches it):

1. **Inspect** the scene through its text twin (`scenes/<level>.scene.json`) and the project.
2. **Build** with asset kits, assets from your libraries, or custom modelling.
3. **Attach** components (physics, character, camera, animation, sound, your own).
4. **Write** systems in TypeScript.
5. **Play** the game.
6. **Playtest** with scripted inputs. Playtests run on game time, so the same script gives the same result on any machine.
7. **Look** at renders (before and after, side by side) and game screenshots.
8. **Fix**, then repeat.

It doesn't call something done until the playtest and the screenshots show that it works.

## Playing it yourself

Press **F5** (or ▶ in the top bar). With the **Embedded** game display, the default, the game runs inside Blender: the largest 3D View of the window becomes the Game View while it plays.

- **Click the game** to play it: the mouse and keyboard go to the game. **Shift+Esc**, or a click outside the Game View, gives them back to Blender. Esc alone goes to the game, for its pause menu.
- **F5 to F8** still work while you play: F6 pauses, F7 steps, F8 stops.
- **Scene | Game** in the area's header shows the 3D View while the game keeps running, and the game again.
- **F8 (Stop)** turns the area back into the 3D View, with the view you left.
- **The next F5 is near-instant.** After Stop the game stays ready in the background, hidden and not drawing, so Play shows it again in a few hundredths of a second. Nothing is exported again when nothing changed, and only the manifest is rewritten when you only moved things or edited components. The **X** in the Game View's header closes the game; Preferences › Add-ons › GameStudio › "Close idle game after (minutes)" closes it when you have not played for that long (15 by default, 0 never).
- While Play starts, the status bar and the Game View's header say what it is doing: starting the dev server, exporting, starting the game view, loading.
- Moving the mouse out of the Game View, or switching to another app, lets go of every held key, so nothing sticks.
- The icon next to ▶ in the top bar switches to the **Window** display: the game opens in its own browser window, as before. Preferences › Add-ons › GameStudio › Play has both, and the window options.

The game runs in a headless Edge or Chrome that GameStudio starts (on a computer with neither, in the Chrome Headless Shell that GameStudio's setup downloads); Blender shows its frames, at most 1280 x 720 and scaled to the area. Games that lock the pointer for mouse look get absolute mouse positions only; play those in the Window display.

### Scene view

See the level drawn by the game, with its game shaders, lighting, lightmaps, fog and post effects, while you edit, without pressing Play. Like Unity's Scene view, it is a 3D Viewport that the game's renderer draws.

- **GameStudio › Scene View** splits the Game workspace's 3D Viewport: Blender's own view on the left, the Scene view on the right. Or press **Game** at the right end of any 3D Viewport's header (also **View › Scene View (Game Renderer)**, for a header too narrow to show it). Press it again to turn it off. One Scene view at a time: turning it on in another area moves it.
- It is still a 3D Viewport: orbit, pan, zoom, fly, the numpad views and the camera view (Numpad 0), click and box select, G/R/S and the gizmos all work. Blender draws its selection outlines, origins, lights, cameras, empties, the 3D cursor and Edit Mode's vertices and edges on top of the game's picture. The floor grid is hidden while the game's picture shows; the Overlays popover brings it back.
- Edits show without Play: moves and component edits at once, game shader values at once, geometry and Blender material edits after a short re-export. Animated game shaders (water, foam, swaying grass, lava) move. Nothing else runs: no systems, physics, animation or sound. Code that builds things only while the game runs shows on Play, not here.
- **F5** plays as usual, in the Game View, and the Scene view says where the game plays. **F8** brings the Scene view back, from its own view. It keeps the game loaded, so Play starts at once.
- The first time it takes a few seconds: it starts the dev server and the game's browser and loads the scene ("Starting the game renderer..."). Turning it off keeps the game ready for Play.
- Preferences › Add-ons › GameStudio › Play: **Scene view frame rate** (30 by default; higher follows the view more smoothly and costs more) and **Animate game shaders in the Scene view** (off: they hold still until Play).
- While you orbit, the game's picture follows a few frames behind; Blender's overlays move at once. In Edit Mode the game shows the mesh as it was until you leave Edit Mode. The colours are the game's own (Blender's colour management does not touch them). Nothing about the Scene view is saved in the .blend.

**In a UI file** (`ui/ui.blend`, [GAME-UI.md](GAME-UI.md) §11.2), press **Game** and look through the screen's camera (**Numpad 0**). The 3D Viewport then shows the screen you are editing as the game draws it: its fonts, text layout, scale mode at the size of the camera frame, backdrop, 9-slices, loop animations, turning 3D models and Shine or Gradient panels.

- It is drawn over the start level's last export, or over a checkerboard when that level was never exported.
- Live values show as written, such as `{score}`, until the game sets them.
- Edits show as you make them, and switching to another screen shows that one.
- Out of camera view, a line at the bottom says how to get back.
- **F5** plays the game and **F8** comes back, as in a level.

### Skills

The agent picks these workflows automatically. You can also run one directly: in Claude Code each is a slash command, such as `/blockout-level a three-tier arena`; in Codex, ask for the skill by name.

| Skill | Use it to |
|---|---|
| `/new-game-from-brief` | Turn an idea into a first playable: a plan, a level, a player, one mechanic, a win condition |
| `/blockout-level` | Lay out a level to scale (jump distances, step heights, colliders) and walk it in a playtest |
| `/make-prop` | Make an object: library assets and kits first, custom modelling when nothing fits, then validate and look |
| `/procedural-world` | Build big spaces with geometry nodes: scatter with keep-out zones, roads, rivers, fences, cliffs, modular buildings, all editable afterwards |
| `/animate-character` | A rigged character with walk, run and jump clips, or props that spin, bob, sway or open |
| `/add-mechanic` | Add behaviour: a component, a system, attaching it in Blender, a playtest |
| `/playtest-and-fix` | Reproduce a bug with scripted input, find the root cause, prove the fix |
| `/stylize-scene` | A materials, lighting, set-dressing and motion pass, with library shaders for water, wind, snow, moss, wetness or a toon look, and before and after renders |
| `/design-ui` | Lay out menus and the HUD in Blender (`ui/ui.blend`): title, pause and end screens, score and health, buttons and live values, 3D models, shine effects and translations, checked in the game |
| `/prepare-for-web` | Check budgets, export every level, `npm run build`, smoke-test the build |
| `/make-template` | Turn the game into a template New Project offers, with its own skills ([TEMPLATES.md](TEMPLATES.md)) |

### Asset kits

Kits are parametric, seeded generators for game-ready meshes in a consistent stylized look. All of them use metres and Z-up, put the origin at the bottom, apply transforms, include UVs, and stay within a triangle budget.

| Kit | Main params |
|---|---|
| `rock` | size (up to 40 m, for cliffs), height, roughness, detail, moss |
| `crate` | width, depth, height, frame, inset, braces |
| `barrel` | height, radius, bulge, segments, hoops |
| `tree` | style (round, pine), height, trunk_radius, canopy_radius, layers, lean |
| `bush` | size, height, clumps |
| `fence` | length, height, style (pickets, rails), posts, rails |
| `stairs` | steps, width, step_height, step_depth |
| `platform` | width, depth, height, bevel, top and side materials |
| `building` | width, depth, floors, door, windows, side_windows, roof (gable, flat), overhang |
| `terrain` | shape (square, round, island), relief (hill, ramp, valley, ridge) with height and direction, size, resolution, height, feature_size, roughness, flatten_center, cliff_angle |
| `collectible` | style (coin, gem, star), size, thickness, segments |
| `character` | height, girth, head, arms, color |
| `character_rigged` | height, girth, head, legs, arms, hair, colours; a skeleton and clips (Idle, Walk, Run, Jump, Land, Wave, Fall, ...) |
| `vehicle` | style (car, kart, truck), length, width, wheel_radius, color; wheels are children `<name>_Wheel_FL/FR/RL/RR`, optional `spin` clip |
| `weapon` | style (blaster, sword, bow), size, length; blasters and bows have a `<name>_Muzzle` where projectiles spawn |
| `projectile` | style (bolt, orb, arrow), length, thickness, glow; made to be a prefab spawned at a Muzzle |
| `door` | style (plain, planked, scifi), width, height, hinge, open_angle; a hinged `<name>_Leaf` with `door_open`/`door_close` clips |
| `key` | style (classic, card), size, color |
| `switch` | style (lever, wall), throw; a `<name>_Handle` with `switch_on`/`switch_off` clips |
| `pickup` | style (health, ammo, energy, shield), size, color |
| `water` | shape (square, round), size, depth, waves, optional `bob` clip; a sensor collider fills it |
| `sky` | preset (day, sunset, night, overcast) or horizon/zenith/ground colours, sun elevation and azimuth; sets the world and a sun lamp |

Every kit also takes a `seed` (the same seed always gives the same mesh) and a `name`. The shared material palette has presets such as stone, wood, metal, grass, plastic and emissive. Using it keeps a project in one style. Ask the agent to "make the rocks bigger" and it rebuilds those kit objects in place, keeping their components.

### Asset libraries

Props you reuse live in `assets/<library>.blend`, one collection per asset. The agent adds props to a library, places linked copies in your levels, and swaps or edits them; editing an asset updates every level that uses it. Library assets are also prefabs, so game code can spawn them.

### Baked lighting

For soft light in corners and light that bounces off walls, ask the agent to bake the lighting, or press **Bake Preview** or **Bake Final** (Properties › Scene › Game Project › Lighting, or **Game › Bake Lighting** in the top bar). Cycles bakes lightmaps for the static objects. The sun stays realtime, so moving things still cast shadows.

- While you iterate, bake a preview: `bake_lighting {"quality": "preview"}`. It is quick and coarse.
- Before shipping, bake the final version: `bake_lighting {"quality": "final"}`. It is sharper and slower.
- Check the result with `compare_views` (Blender and the game side by side) and `game_screenshot`.
- Bake again after you move or add static objects, or change the lamps or the world. Export warns when a lightmap is out of date.
- Objects your code moves need the custom property `gs_lightmap` set to `False`. Set it to `"baked"` on a lamp to bake that lamp fully.

Baking runs without asking: it only writes generated files in `.gamestudio/`, never into the .blend. [API.md §25](../runtime/packages/engine/API.md#25-baked-lighting-lightmaps) says which objects are baked, how the lighting adds up, and the limits.

### Game shaders

Blender owns the shape of things: meshes, UVs, which surface is which (the material slots), and static colour and roughness. Anything that moves, reacts to the game, is projected in world space or isn't in glTF, such as wind in leaves, flowing water, foam, triplanar photo scans or wet sand, is a **game shader**: a TypeScript file in `src/shaders/` that says how the game draws a Blender material.

- GameStudio ships 33 of them, ready to use (see Shader library below). For anything else, the agent writes the shader with `defineShader` (typed fields; textures are asset fields) and attaches it to the material with `set_material_shader`, in one undo step. By hand: Properties › Material › **Game**, pick the shader and set its fields.
- The engine applies it wherever the material is drawn: the level, prefabs spawned later and reloaded levels. Saving the shader file updates the running game without reloading the level.
- Game code never looks materials up by name, so renaming a material in Blender breaks nothing.
- Shaders edit three.js materials through `patchShader`, which chains edits, so they work together and with baked lighting. Patch-based shaders need the WebGL renderer, the default.
- Blender's viewport shows the material's own nodes. Judge the look in the game: turn on the Scene view (above), press F5, or ask the agent for game screenshots.
- A material whose shader no file defines, or whose shader fails, keeps its Blender look, and the console says why.

### Shader library

GameStudio comes with 33 game shaders that need no code, in seven groups:

| Group | Shaders |
|---|---|
| Water | Water, ShorelineFoam, Flow, Caustics, WetSurface, Lava |
| Nature | FoliageSway, GrassWind, FlagWave, SnowCover, MossOvergrowth, Ice |
| Magic & Sci-fi | EnergyShield, Hologram, Dissolve, PortalVortex, ScannerPulse, HotMetal |
| Surfaces | Triplanar, InteriorMapping, ParallaxOcclusion, CarPaint, Iridescence, Gem |
| Transformations | Petrification, GoldenTransmutation |
| Gameplay | SelectionOutline, XRaySilhouette, InteractGlint, HitFlash |
| Style | Toon, Shine, Gradient (Shine and Gradient also work on UI panels) |

- **Pick one in Blender:** select a material, then in Properties › Material › **Game** open **Game Shader** (type to search). The library follows your project's own shaders, one sub-menu per group. The panel shows what the shader does and its fields; the defaults look right straight away. Or ask the agent: "make the pond water", "snow on the rocks", "the leaves should sway". It uses `set_material_shader`.
- **See it:** turn on the **Scene view** (GameStudio › Scene View), or press **F5** and look at the Game View. Blender's own 3D View keeps showing the material's Blender nodes, so give the material a similar colour for working in Blender.
- **Most build on your material:** snow, moss, wetness, dissolve, toon, outlines and hit flashes keep its colour, textures and roughness and add to them, so lights, shadows, fog and baked lighting still apply. Water, foam, lava, energy shield, hologram, portal and gem draw a look of their own, tinted by the material's colour.
- **Drive it from code:** game code changes the values while the game runs. `ctx.shaders.set(enemy, { hitAt: ctx.time })` flashes one enemy when it is hit, `ctx.shaders.set(enemy, { startAt: ctx.time })` dissolves it, and `ctx.shaders.set('GS_Leaves', { wind: 1.2 })` turns the wind up for every tree. Fields like `hitAt` and `startAt` hold the time an effect starts, and the shader plays it out by itself.
- **Customise one:** the panel's "Copy it into src/shaders to customise" means: copy the shader's code from the engine into a file in `src/shaders/` under the same name, and the game uses your copy. Ask the agent to do it.
- **Cost:** each shader downloads only when a scene uses it. Most are cheap; ParallaxOcclusion, Gem and the outline shaders cost more, so use them where they show.

[API.md §29](../runtime/packages/engine/API.md#29-shader-library) has the full table: what each shader does, its fields, how code drives it, what it costs, and the limits (the WebGL renderer, FlagWave's subdivided plane, reflections of the sky only).

### Colour matches Blender

The game tone-maps the way the scene's **Color Management** says (Properties › Render › Color Management): its view transform and its exposure.

- Khronos PBR Neutral, AgX and Standard play as they look in Blender, and ACES 1.3 plays as the game's ACES. Filmic, Filmic Log and ACES 2.0 have no three.js equivalent, so the game uses ACES and the export warns. Looks (such as AgX - Punchy) are not carried over, and the export warns about them too.
- The templates, the demo and every scene New Project builds use Khronos PBR Neutral with no look.
- To make the game brighter or darker, change the scene's exposure in Blender, not in code. A `PostEffects` exposure on the camera multiplies it.
- A scene exported by an older GameStudio plays with ACES, as before, until it is exported again.

### Smaller builds

`npm run build` loads everything in `dist/` by relative URLs, so the build works from any folder, for example on itch.io or GitHub Pages (`user.github.io/repo/`).

`npm run build` writes `dist/THIRD-PARTY-NOTICES.md` next to the game: the licences of the libraries it bundles (three.js, Rapier, recast, the GameStudio engine and the shader code it credits) and of its fonts. Keep it with every upload; it is what those licences ask in return.

`npm run build` makes the game smaller to download. While you develop, nothing changes: exports stay plain GLBs, so exporting and hot reloading stay fast.

- **Geometry:** the build compresses the scene GLBs it copies into `dist/` with meshopt (`EXT_meshopt_compression`). This is on by default. The compression is lossless: every vertex, index, animation key and GPU-instance transform decodes back exactly. Only the GLB's buffers change, so node names, ids, skins, morph targets, clips, instancing and lightmap UVs stay as they were. Each buffer is encoded only when that makes it smaller both as it is and after gzip. Most hosts send `.glb` files uncompressed, but some gzip them. Flat-shaded normals, for example, gzip better as plain floats, so they stay plain. On the runner template the scene files shrank by 25%, from 2.04 MB to 1.52 MB. Gzipped, they shrank by 8% (570 KB to 524 KB). The game draws pixel for pixel the same frames. The engine decodes the files itself, with no extra files and no CDN, and plain GLBs still load.
- **Textures:** add `"compression": {"textures": "webp"}` to `gamestudio.json`. The export then writes colour maps as WebP at `export.webpQuality` (90), and data maps (normal, roughness, occlusion) as lossless WebP. This happens at export, because Blender has the encoder, so the game looks the same in development as it does once shipped. Each conversion is cached, so re-exports cost nothing. `export.maxTextureSize` still caps every texture.
- **Settings:** `"compression": {"geometry": "meshopt" | "none", "textures": "webp" | "none"}`. The defaults are meshopt and none.
- **Sizes:** `npm run build` prints every scene file before and after, with its geometry and textures. `export_scene {"compress": true}` reports in Blender what the build will ship (`size.compressed`), without changing the export. `size.per_file` compares each GLB with what Blender's glTF exporter wrote.

Draco and KTX2 are not used. Draco needs decoder files served beside the game and decodes more slowly, and meshopt does the same job. A KTX2/Basis encoder needs native binaries, or a large WASM encoder in every build, and KTX2 textures need a transcoder at runtime. WebP covers the download size. Lightmap PNGs and asset files under `files/` are copied as they are.

## Staying in control

- **Undo:** each Blender change the agent makes is one undo step (`Ctrl+Z`), and that includes a whole `run_python` script. Writes to an asset library file are backed up first instead: `asset_library_restore` takes the last one back.
- **Checkpoints:** before risky work, the agent saves a checkpoint you can restore. Ask "go back to the checkpoint before the stylize pass", or "show me before and after".
- **Crashes:** if Blender crashes or hangs, the agent offers the ways back: the last save, Blender's autosave, or a checkpoint.
- **Locks:** objects you protect (the agent button in the top bar › **Protect**) are refused by every scene tool.
- **Permissions:** in Claude Code, read-only tools, play controls and **undoable scene edits** (creating and moving objects, components, materials, kits, placing assets, undo, save) run without asking. Each edit is one undo step, shows in Blender as it happens, and appears in the Agent panel, where you can pause the agent or protect objects. Asset-library writes are backed up first, and `asset_library_restore` takes them back. `run_python`, `delete_objects`, `restore_checkpoint` and `open_scene` always ask first. Change this with `/permissions`. GameStudio pre-approves nothing for Codex, so Codex's own approval settings apply.
- **Code:** the code is plain files in `src/`. Use git and review diffs as usual. The level twins (`*.scene.json`) make scene changes reviewable too.
- **Ownership:** you can keep editing in Blender while the agent works. Tell it what you changed so it re-inspects the scene.

## Tips

- Start small: one level and one mechanic, playable early. Add art last.
- Keep one level per `.blend` in `scenes/`. Put reused props in an asset library.
- Ask the agent to show you renders: "render the level from the top and from the player camera", or "show the tower in its surroundings".
- Leave the dev server running (`npm run dev`). Code changes hot-reload into the running game.
- For an invisible wall or a cheap collision shape, give a mesh a `Collider` and set its Viewport Display to **Wire** or **Bounds**. The game keeps it for physics and raycasts but doesn't draw it.
- Never hand-edit `.gamestudio/`. It is generated.
- Other languages: the menus' texts are translated by tables in `ui/locales/` (the starter ships `fr.json`), which the agent keeps complete with `ui_strings` and `ui_translate` after every new menu text. The game starts in the player's browser language, and a Language button in Options switches. Ask the agent for a playtest in French to see them.
- To start new games from this one, save it as a template (GameStudio menu, **Save as Template...**, or the `make-template` skill: `/make-template` in Claude Code). See [TEMPLATES.md](TEMPLATES.md).

## Troubleshooting

| Problem | Fix |
|---|---|
| `/mcp` shows `gamestudio` failed | Open the project in Blender once: GameStudio sets its packages up (GameStudio › Set Up GameStudio... does it now) and points `.mcp.json` (and, in a project connected to Codex, `.codex/config.toml`) at this computer's Node.js and the project's MCP server. Then restart the agent. `spawn node ENOENT` meant the file named a Node.js this computer does not have. |
| Installing Claude Code or Codex failed | The status bar and the popup say why, with **Try Again** and **Show Log** (`claude-code-install.log` or `codex-install.log` in the extension's user folder). Offline: reconnect, then Try Again. On Linux the installer needs `curl` (`sudo apt install curl`). The **Installation Guide** button opens Anthropic's or OpenAI's instructions for installing by hand. |
| Open in Claude Code or Open in Codex opens no terminal | The status bar says what failed. Set **Terminal command** in the extension's preferences (Coding agent) to your terminal, as described under Setup. |
| New Project says online access is off | Setting up downloads Node.js or packages. Turn on **Preferences > System > Network > Allow Online Access** (the dialog has a button). Nothing was written. |
| GameStudio asks to set itself up | The first time a game opens or plays, it lists what it downloads (Node.js, the game packages, a game browser), about how big, and the folder. **Set Up** (it turns on Allow Online Access when that is off) or **Cancel** (asked again only when you press Play or turn the Scene view on). *Set up automatically* in the extension's preferences skips the question. |
| The setup failed | The status bar and GameStudio menu say what failed, with **Retry** and **Show Log**. *The package server could not be reached* means no internet connection (or a proxy): reconnect, then Retry. Logs: `setup.log` in the extension's user folder, `runtime/npm-install.log` there, and the project's `.gamestudio/setup.log`. |
| An old project plays an old engine, or says packages are missing after an update | Opening or playing it brings it up to date by itself: the game packages are copied from the new version (no download unless it needs a new package), and links into another GameStudio install (an older portable one, another Blender version's) are replaced. Links into a development checkout are left alone. |
| A new project's setup failed or was cancelled | Open Project on its folder, or New Project with the same name, runs the setup again and keeps its files. |
| The agent says Blender isn't connected | Let the agent `launch_blender`, or open Blender with the GameStudio extension enabled and open the project's level. |
| Playtests or screenshots fail | The agent starts the dev server through Blender; the status bar says why when it cannot (the GameStudio menu's Set Up GameStudio... covers what is missing). |
| F5 opens a window instead of playing in Blender | The status bar says why: usually the game browser was not set up (you chose Cancel, or online access was off) and there is no Chrome, Edge or Chromium. That Play uses your default browser, Safari included. GameStudio › Set Up GameStudio... downloads the game browser; or pick the Window display with the icon next to ▶. |
| F5 does nothing on a Mac | F5–F8 are media keys there: press **Fn+F5**, or turn on *Use F1, F2, etc. keys as standard function keys* in System Settings › Keyboard. |
| macOS says Blender cannot use Documents | New Project and the examples go in Documents/GameStudio. Allow Blender in System Settings › Privacy & Security › Files and Folders (Documents Folder), or pick another location in New Project. |
| A new component isn't in Blender | Save the component file while the dev server runs (or press Play), then Reload Components. |
| The game looks different from Blender's viewport | The game follows the scene's view transform and exposure, but Blender cannot show game shaders or post effects, and Filmic or a look have no game equivalent (see Colour matches Blender). Judge the look from game screenshots. For more than that, ask the agent to compare them: it renders both side by side and measures the difference. |

## For maintainers

- Kits live in `blender/gamestudio/kits/` (`catalog()`, `create()`, `regenerate()`, `materials`, `animation`). To add a kit, register a build function with `@kit(...)` that uses `MeshBuilder`, then run `python tools/test_blender.py addon`. `test_kits.py` checks every kit for closed meshes, UVs, budgets, pivots and determinism.
- Library shaders live in `runtime/packages/engine/src/shaders/library/<kebab-name>/` (`meta.ts`, `impl.ts`; a folder is all it takes to add one). Each needs a row in [API.md §29](../runtime/packages/engine/API.md#29-shader-library) with its thumbnail at `docs/images/shaders/<kebab-name>.png` (`node tools/shader-gallery/capture.mjs <Shader>`); `runtime/packages/mcp/test/shaders.test.js` checks the rows.
- The agent files of the starter template are in `blender/gamestudio/project_templates/starter/` (`AGENTS.md`, `CLAUDE.md`, `.mcp.json`, `.claude/`). Write instructions for any coding agent in `AGENTS.md`; `CLAUDE.md` stays the line `@AGENTS.md` plus a short `## Claude Code` section. The demo has its own copy: keep the skills identical in both places. Genre templates compose their `AGENTS.md` (`{{base}}`) and take their skills and `CLAUDE.md` from the starter's ([TEMPLATES.md](TEMPLATES.md#instructions-for-coding-agents)).
- `runtime/packages/mcp/test/skills.test.js` (part of `npm test`) checks that every tool the skills, the `AGENTS.md` and `CLAUDE.md` files and `.claude/settings.json` name exists, and that the demo's skills match the starter's.
