# Changelog

## Unreleased (0.1.0 preview, 11 October 2026)

GameStudio became a Blender 5.2+ extension, an MCP server and Claude Code skills. The Blender fork (`editor/`, `docs/fork/`) is shelved.

### First-run setup
- **Install the zip, open a game, press Play.** No terminal, no npm, no Node.js or browser to get yourself. One readiness check (`environment.ensure_ready`) runs before Play, the Scene view, New Project, Open Project / Open Recent / Examples, Open in Claude Code and after a project's scene loads. With nothing missing it is a few cached file checks; otherwise it sets up what is missing in the background and the action goes on when it is done (Play plays, the Scene view turns on, Claude Code opens).
- **One question, once.** The first setup that downloads anything asks: Node.js 24 (only without a Node.js 22.12+, also found in Homebrew, nvm, Volta, asdf and fnm folders when Blender started from the Finder), the game packages, and a game browser (Chrome Headless Shell, only without Chrome, Edge or Chromium), each with its size and the folder they go in. **Set Up** also turns on Blender's Allow Online Access when it is off. Local work (links, the component list, the copy after an update) never asks. *Set up automatically* in the preferences skips the question; `GAMESTUDIO_AUTO_SETUP=1` does it for Blender without a window.
- **Progress and failures.** The status bar and the GameStudio menu show the step and its percentage; a failure says what failed in one sentence (a missing internet connection plainly), with **Retry** and **Show Log** in the status bar, the menu and a popup. No message tells anyone to run npm or install Node.js or a browser any more.
- **The same packages for everyone.** Setup installs the game packages at the exact versions in `core/runtime.lock.json`, the versions CI tests (made by `tools/runtime_lock.py` from the repository's lock; the build checks it is current), without running install scripts. An update downloads only what changed. New projects record the engine version in gamestudio.json (`"engine"`).
- **Without a browser,** Play falls back to the Window display (the default browser, Safari included) with a one-line note.
- **A game browser when there is none:** setup downloads Chrome Headless Shell (pinned to the build puppeteer-core targets, checksummed; about 100–125 MB) into the extension's folder. The game in Blender, the Scene view and Claude's headless playtests use it after the system's Edge, Chrome, Chromium, Brave or Vivaldi (per-user installs and Beta/Dev/Canary builds included), on the GPU.
- **Claude Code without a terminal:** Open in Claude Code installs Claude Code when it is missing (Anthropic's official installer, one click, with progress and Try Again), then opens it in the project. The project's `.mcp.json` names a Node.js that works from any terminal, VS Code or the desktop app (and copied examples point at their own packages); macOS opens Terminal through a `.command` script, so it needs no Automation permission. "Connect Claude to this project" replaces the old jargon.
- **Codex next to Claude Code:** GameStudio › Open in Codex installs OpenAI's Codex when it is missing (OpenAI's standalone installer, one click, with progress, Try Again and Show Log; the dialog says Codex signs in with a ChatGPT account or an API key), connects the project and opens it in a terminal. Connecting writes `[mcp_servers.gamestudio]` into `.codex/config.toml` with `.mcp.json`'s command (your other settings stay as written; repaired on open, made portable by Save as Template), copies `.claude/skills/` to `.agents/skills/` (refreshed when a skill changes, never over a skill of yours or an edited copy) and gives Codex `AGENTS.md`. The **Coding agent** preference (Claude Code by default) picks what the launcher, the welcome, the Project panel and the top bar's agent button open, and the status bar names that agent ([MCP.md](docs/MCP.md#codex)).
- **Fixes:** an extension update now refreshes the shared game packages when a project is opened or played (offline when nothing new is needed), so existing projects get the new engine; projects linked into another GameStudio install (an older portable one, another Blender version's) are linked to this one again, while links into a development checkout are left alone; readiness checks every package the GameStudio packages need from where they are (puppeteer-core included), so a half-finished install is completed instead of reported as ready; re-linking a project no longer writes through npm's `.bin` symlinks into the shared packages; npm runs as `node npm-cli.js` with the chosen Node.js first on every child's PATH (also a custom npm command), and uses a cache in the extension's folder that is emptied after the setup; the Node.js archive keeps its executable bits and links on macOS and Linux; nothing reads ~/Documents while drawing the New Project dialog, and macOS's refusal says what to allow; the play shortcuts read Fn+F5 on a Mac.

### Safety
- **Trusted projects.** GameStudio runs a project's own code (its npm scripts, its packages' install scripts, its `tools/` scripts, the game's dev server, Build Web, the type check) only after the project is trusted, as Blender does with scripts in a .blend. Projects you create or copy (New Project, examples), the recent projects of an earlier version and projects Blender would auto-run scripts for are trusted. Any other project still opens and can be edited, and asks once with its folder and what trusting means; GameStudio › Trust This Project trusts it later. Calls from an agent never ask: they are refused with a message to pass on. Templates are no way around it: Save as Template needs a trusted project, and New Project from a template someone else made asks once (trusting the project trusts the template). Package names from a project's `package.json` are checked before they are linked, a game address from `.gamestudio/session.json` opens only on this computer, programs are never run from the current folder on Windows (Open in Claude Code and Open in Codex included), and a file from a project or a game's log that would run if opened (`.bat`, `.js`, `.exe`, `.command`, ...) is shown in its folder instead.
- **The tokens stay out of web pages.** The game's dev server no longer serves `.gamestudio/` (where the Blender bridge and live-link tokens are kept) or the project's agent folders; exports are still served from `/__gs/export/`. The bridge and the hub close any connection whose first line isn't a valid hello, so a web page cannot reach them with an HTTP request; WebSocket peers can only be games, a reply reaches only the peer that asked, and only tool peers may call the assistant's tools.
- **Unsaved changes are kept.** Open Project, Open Recent and Examples ask Save, Don't Save or Cancel first, as File › Open does. When New Project finishes while the open file has unsaved changes, the project waits instead of replacing it: a popup says it is ready (Save, Don't Save, Later), and GameStudio › Open *name* opens it later. After a restart, Open Project or Open Recent opens it without setting it up again.

### Licences
- **Every part says its licence.** The extension zip carries the GPL-3.0 text (`LICENSE`), and the build fails without it or the other licence files. `LICENSE.md` lists the parts: the extension is GPL-3.0-or-later, the runtime packages MIT, the project and script templates' code and the demo game MIT-0, and the starter's sounds, music and UI images CC0. The manifest names all four and gains `copyright`.
- **Your game is yours.** Template code and the starter's assets ask for no credit; every new game gets `assets/LICENSE.txt`, which lists the CC0 files.
- **Web builds carry their notices.** `npm run build` writes `dist/THIRD-PARTY-NOTICES.md`: every bundled package's licence (Rapier's Apache-2.0 in full), the engine's credits for the shader code it borrows (simplex noise by McEwan and Gustavson, David Hoskins' hashes) and the licences of the game's fonts. Inter travels with its OFL text (`Inter-OFL.txt`) in projects and exports, and so does any font with a licence file beside it.
- **Web builds work from any folder.** `npm run build` writes relative URLs, so a game plays on itch.io or a GitHub project page (it was a blank page there); a project's own `base` still wins. Panels say "8 entities", not "8 entitys".
- **No personal paths in shipped files.** The demo game's scene no longer carries the author's Documents folder (Blender keeps the Shading workspace's File Browser folder in every .blend); its generator clears it, and `tools/check_blend_paths.py` fails the build when a shipped .blend holds an absolute path or a user name.

### Game UI: menus and HUDs made in Blender
- **Screens are Blender scenes.** `ui/ui.blend` holds one scene per screen (Title, Pause, HUD, Win, ...). Its orthographic camera frames the screen at a design size (1920 × 1080, 100 px per unit), and you lay it out with Blender's tools:
  - text objects in any `.ttf`/`.otf`/`.woff2` font (Blender's Inter by default, so Blender and the game draw the same glyphs);
  - image empties and flat Emission meshes for art and panels;
  - empties as groups ([GAME-UI.md](docs/GAME-UI.md)).
- **Behaviour without code:** built-in components on the objects.
  - `UiCanvas` on the camera: menu or HUD, when it opens, whether it pauses, the pause menu, the back key and the scale mode.
  - `UiButton`: close, open another screen, previous, restart, load a level, or an event for code.
  - `UiAnchor`: edges and corners, stretch and cover, for other aspect ratios.
  - `UiBar`, `UiText` (outline, shadow, shrink to fit) and `UiState` (art for focused, pressed or disabled buttons).
  - Text shows live values: `Score {score}`, `{save.best}`, `{time:m:ss}`.
- **Drawn by the game in WebGL** over the 3D view, after post effects and without tone mapping. Text uses Blender's own layout rules (measured: glyph scale, line advance, letter spacing, alignment) with the project's font files.
  - Menus pause the game and work with keyboard, gamepad and mouse; navigation goes by position.
  - A hidden DOM copy keeps playtests and screen readers reading the text.
  - Games without Blender UI load none of its code.
- **Code reacts:** `ctx.ui.open`, `ctx.ui.on('Play', ...)`, `ctx.ui.text`, `show`, `hide`, `bar` and `element`. `ctx.ui.screen(name, {...})` and `ctx.hud` keep working as the quick code-only path ([API.md §22](runtime/packages/engine/API.md#22-menus-and-screens-ctxui)).
- **In Blender:**
  - GameStudio › Edit Game UI and Back to Level.
  - Shift+A › Game UI: Text, Image, Panel, Button, Bar, Group.
  - The Scene › Game UI Screen panel, with Show in Game.
  - A Layout box in pixels with the resolved anchor, Choose Font, and an anchor overlay.
- **Live:** saving exports `.gamestudio/export/ui/`, and edits stream into a running game without reloading the level. Play from the UI file plays the start level and opens the screen you are editing.
- **Agents:** `ui_open`, `ui_list`, `ui_canvas`, `ui_add`, `ui_set` and `ui_remove`, and `gs.gameui` in `run_python`. `game_screenshot {ui}` opens a screen before the shot, and playtests report `final.ui.open` and `texts`. A new `design-ui` skill covers the workflow.
- **Tested on a cold start:** a fresh agent with only a starter project's files and tools built an Options screen (volume bar, − / + and Back) in about 7 minutes. What it tripped on is fixed:
  - Arrow keys prefer buttons in the same row or column.
  - A playtest click at 0 s presses the title's Play.
  - `ui.size` says which canvas size button positions are in.
  - `ui_add` takes button `sound`/`focus_sound` and a shadow blur, and `ui_canvas` takes `focus`.
  - The starter's Play button sits in a `TitleButtons` group.
  - design-ui has a settings-screen recipe and says what a paused menu does to sound.
- **HUD icons in every new project:** a second cold-start agent added three lives as hearts, a fall respawn and a Game Over screen with the best score in about 10 minutes, but had to write its own PNG encoder to draw the hearts. New projects now ship `assets/ui/` with `heart`, `heart-empty`, `star`, `star-empty`, `coin`, `gem` (the starter's teal gems) and `key`: 128 × 128 PNGs on a transparent background in one flat, outlined style, about 24 KB together, drawn by `tools/make_ui_icons.py` with the standard library (`--check` finds stale files). The genre templates get them through the starter. Its docs fixes:
  - design-ui has a Lives on the HUD recipe (hearts over empty slots in a pill, a tiny system that shows one per life, Game Over with `Best {save.best}` and a "New best!" line that starts hidden), lists the icons and says how to add other art (an image is 1 image px = 1 px without a `width`).
  - add-mechanic's Fall respawn costs a life, blinks for a moment, opens Game Over on the last life and calls `snapCamera`, so the camera cuts back to the start instead of sweeping up through the player.
  - Engine:
    - `FollowCamera` and `SideCamera` cut instead of sweeping when their target jumps more than 5 m in a frame.
    - `snapCamera(entity?)` cuts on demand, for checkpoints and short teleports.
    - `test.place()` snaps the camera.
    - `state().ui.hidden` lists the elements of open screens hidden by code or by Disable in Renders, so playtests can check HUD icons without screenshots.
  - Tools:
    - `ui_list` reports panel `radius`, `border`, `border_color` and `rotation`.
    - `ui_add` takes `visible`.
    - `validate_assets` no longer flags empty entity fields, only references emptied by deleting their target.
    - In the UI file, `validate_assets` checks every screen, without the PBR checks meant for levels.
  - The skills and `AGENTS.md` say that bound values are empty while the game is stopped, that translucent colours mix in linear light like Blender's (alpha `F2`–`FF` looks solid), and that playtests list hidden elements in `final.ui.hidden`.
- **Existing projects get it too:** when a project opens, GameStudio adds the built-in skills it lacks (`design-ui`) and a short marked Game UI note to an `AGENTS.md` without one, and says so in the status bar. Skills it put there that nobody edited follow GameStudio's updates; skills you edited, wrote or deleted stay as they are (`.claude/skills/.gamestudio-skills.json` records which are GameStudio's; [TEMPLATES.md](docs/TEMPLATES.md#instructions-for-coding-agents)). Copies of skills from older GameStudio versions are recognised by the hashes of every past built-in version (`core/skill_history.json`, made from git by `tools/skill_history.py` on every build), so they update too.
- **Every template's menus and HUD are Blender screens:** the starter builds its own in `ui/ui.blend`, the genre templates theirs with `tools/build_ui.py` (Phase 2, below).
- **Phase 2: settings, counters, animations and art without code** ([GAME-UI.md §10](docs/GAME-UI.md#10-phase-2-implemented)):
  - **Audio channels** `master`, `music`, `sfx` and `ui` (`AudioSource.channel`, `ctx.audio.channel(name)`). Volumes are saved for the player and restored at start; playtests start at the defaults. Music plays on under a paused menu, so a volume slider is heard at once; sound effects pause and resume; button sounds always play.
  - **Value paths** that sliders and toggles write as well as read: `ctx.store` keys, `save.x` and `audio.music`, `audio.sfx`, `audio.master`, `audio.ui`.
  - **`UiSlider` and `UiToggle`:** an Options screen is a few `ui_add` calls and no code. Arrow keys, gamepad and mouse drags; each change calls `ctx.ui.on(<name>)` with the value, and playtests see them in `final.ui.buttons` with `kind` and `value`.
  - **`UiShow`** shows an element while a value compares (`lives` at least 2). The builder's `icons` row makes hearts from it, so lives need no code.
  - **Open, close and loop animations** from the screen's timeline markers and keyframes (position, rotation, scale, and the object colour's alpha as opacity), with presets from `ui_canvas transition` or `gs.gameui.animate` (`fade`, `pop`, `slide-*`; loops `pulse`, `bob`, `blink`). For the game a close is at once; the backdrop fades with the menu.
  - **9-slice panels** (`UiSlice`): a panel with `image` and `slice` keeps its art's corners at any size, in Blender and in the game.
  - **Tools:** `ui_add` kinds `icons`, `slider` and `toggle` and panel `image`/`slice`; `ui_set` `min`, `step` and `count`; `ui_canvas` `transition`; `ui_list` reports them. Shift+A › Game UI gains Slider and Toggle. Bindings take `{time:m:ss.0}`.
  - **Templates:** the starter gains an `Options` screen (Music and Sound sliders, Back), opened from the title and the pause menu, menus that pop open and fade out, and a `Music` object playing `assets/music/theme.ogg` on the music channel. The platformer, shooter, runner and driving templates build Title, Pause, Options, their end screens and a HUD at New Project, and their systems drive them through `ctx.store`, bindings and `UiShow` (the shooter's red low-health bar is a `UiShow` twin). `ctx.hud` stays for labels over entities, which hide while a menu is drawn.
  - **Generated music and 9-slice art:** `assets/music/theme.ogg`, a 30 s seamless loop made by `tools/make_music.py` (standard-library synthesis, encoded by Blender's own FFmpeg), and `panel`, `button`, `slider-track`, `slider-fill` and `slider-knob` PNGs in `assets/ui/` with their `UiSlice` insets documented ([TEMPLATES.md](docs/TEMPLATES.md#for-maintainers)).
  - **Panel art that changes the look:** a third cold-start agent (an Options toggle for a HUD timer, a pulsing Play button, "proper panel art") found that `panel.png` looks just like the flat cards. The starter now also ships `panel-solid` (the card, opaque), `panel-framed` (navy in a gold frame with corner studs) and `panel-light` (parchment, for dark text), drawn for the 1920 × 1080 design so their corners stay readable at 1280 × 720; design-ui lists them with their insets and now uses the built-in `{game.time:m:ss}` for a level clock (a store value across levels), right-aligns toggles with the sliders in a row group, pulses one element with `loop`, shows Blender 5's layered keyframes, and seeds `store`/`save` values with `set` in screenshots and playtests.
  - **Engine and tools from the same trial:**
    - Values: `{game.time}` and `{game.level}` are built-in, read-only values.
    - Navigation: Up/Down go to the nearest row.
    - Animation: `state().ui.animating`; `loop: pulse | bob | blink` animates one element (`ui_add` / `ui_set`), and `ui_list` reports what is really keyed.
    - Results: `ui_add` / `ui_set` list only the element's own parts, plus any `removed`.
    - `game_screenshot {ui}` closes other menus and waits for the open animation.
    - Seeding: screenshots and playtests take `set: {store, save}`, and playtests report `final.save` (`game.test.begin({save, store})`, `game.test.saved()`).
    - A canvas without an `open` marker rests where its loop or close starts, so a scrubbed timeline can't export a mid-animation pose.
  - **Skills:** design-ui adds a setting to Options as a row with a slider or toggle and no code, makes hearts an `icons` row, and covers transitions, 9-slice art, value paths and the audio channels; add-mechanic, playtest-and-fix, new-game-from-brief and make-template follow.
- **Phase 3: 3D models, translations, shaders on panels and a preview in Blender** ([GAME-UI.md §11](docs/GAME-UI.md#11-phase-3-implemented)):
  - **3D models in screens:** `UiModel` on an empty shows its children (a kit, a library asset or objects of the UI file) lit, in a view of their own, framed from `view` (`threeQuarter`, `front`, `side`, `top`), turning (`spin`) or looping a clip, with the level's tone mapping. They export into `<Canvas>.ui.glb` (re-exported only when the model changed), pop, fade and hide with their screen, and turn under a paused menu. `gs.gameui.model`, Shift+A › Game UI › 3D Model… and `ui_add {"kind": "model"}` with `kit`, `asset` or `source`.
  - **A game-rendered preview in Blender:** in `ui/ui.blend`, the 3D View's Game toggle, looking through the screen's camera, draws the screen being edited as the game does, over the start level's last export (or a checkerboard), with placeholders such as `{score}` for values the game hasn't set. Edits and switching screens show at once (`game.setView('ui')`).
  - **Translations:** tables in `ui/locales/<code>.json` keyed by the authored texts, bindings included. The game starts in the player's saved language, else the browser's best match, and switches every text at once. `ctx.ui.locale`, `locales` and `translate`, the value paths `ui.locale` and `ui.localeName`, `UiButton.action: "locale"` for a Language button without code, and `state().ui.locale`. Agents list the texts with `ui_strings` and write tables with `ui_translate` (from any file; a running game updates live); playtests pick a language with `set: {"save": {"gs.locale": "fr"}}`.
  - **Game shaders on UI meshes:** a panel's material can name a game shader made for flat UI (`ui: true`): the library's new `Shine` (a band of light sweeping across) and `Gradient` (two colours along an angle), on the screen's own clock. `gs.gameui.panel(shader=, shader_values=)` and `ui_add` / `ui_set` `shader`. The WebGL renderer draws them; WebGPU doesn't yet.
  - **Variable fonts** draw the instance Blender draws (`text.fontAxes`: optical size 16, the other axes at their defaults), not the browser's automatic optical size or weight.
  - **WebGPU** composites the UI in linear light like WebGL, so translucent panels look the same on both.
  - **Games made from your templates show their own name** ([GAME-UI.md §11.7](docs/GAME-UI.md#117-project-names-in-templates)): Save as Template records the game's name in `template.json` (`sourceName`), and New Project gives the new game's `ui/ui.blend` its name in texts marked as the game's name (every template's `GameTitle`, `gs.gameui.text(..., project_name=True)`) and in texts that are exactly the old name, then saves and exports it; until now the title kept the old name.
  - **Starter:** a teal gem turns over the title (`TitleGem`, the `collectible` kit's gem), Play's focus art has a subtle Shine, Options has a Language button (`{ui.localeName}`), and `ui/locales/fr.json` translates every text but the game's name. The genre templates exclude `ui/locales`, and the templates' e2e browser runs in `en-US`.
  - **Skills:** design-ui covers 3D models, shader effects, languages and the Blender preview, condensed to stay about the size it was; new-game-from-brief, add-mechanic and make-template keep the language tables in mind, and `AGENTS.md` lists `ui/locales`.

### Scene view
- **See the level as the game draws it while you build it.** The **Game** toggle in a 3D View's header (also View › Scene View (Game Renderer), and GameStudio › Scene View, which splits the Game workspace's 3D View) makes that view show the level drawn by the game's own renderer, from that view's camera, without playing: game shaders, the scene's tone mapping, lighting, lightmaps and post effects as in the game. The view keeps working as a 3D View (navigation, camera view, selection, transforms, Edit Mode), and Blender's overlays (selection outlines, origins, lights, cameras, the 3D cursor, the grid, gizmos) draw over the game's picture. Moves, component edits and re-exported edits show without Play; game-shader animation runs (Preferences can freeze it) ([WORKFLOWS.md](docs/WORKFLOWS.md#scene-view)).
- **Instant shader values:** dragging a game shader's value in Material › Game updates the running game at once (`material.values` on the live link), with no re-export or reload; the next export is a manifest-only one.
- **Play from the Scene view** plays as before (the page is already warm with the scene loaded); Stop returns to the Scene view. A page Vite reloads during Play keeps playing.
- **Scene shots for Claude:** `game_screenshot` with `mode: "scene"` and a `view` (`{from, at, lens}` or a Blender camera) shows the level through the game's renderer from any viewpoint without playing.
- **Engine:** a Scene-view mode (`game.setView`, `view.mode`, `view.camera`, `?gs_view=scene`) that keeps the level loaded and stopped with UI hidden while the shader clock runs; `game.shadersReady()`.

### Shader library
- **33 ready-made game shaders** ship with the engine, listed in Properties › Material › Game by category and attached with no code: Water, ShorelineFoam, Flow, Caustics, WetSurface and Lava; FoliageSway, GrassWind, FlagWave, SnowCover, MossOvergrowth and Ice; EnergyShield, Hologram, Dissolve, PortalVortex, ScannerPulse and HotMetal; Triplanar, InteriorMapping, ParallaxOcclusion, CarPaint, Iridescence and Gem; Petrification and GoldenTransmutation; SelectionOutline, XRaySilhouette, InteractGlint and HitFlash; and Toon, Shine and Gradient (the last two also on menu panels). Most build on the Blender material (its colour, textures and roughness stay, and so do lighting, shadows, fog and lightmaps); they work on GPU instances from geometry-nodes scatters, and the ones that make sense on characters work on skinned meshes. A build downloads a shader's code only when a scene uses it ([API.md §29](runtime/packages/engine/API.md#29-shader-library)).
- **Live values from game code:** `ctx.shaders.set(entity | material, values)` and `ctx.shaders.get`. Setting an entity gives it its own copy of its materials without a recompile. Trigger fields hold a game time, so one call starts an effect: `ctx.shaders.set(enemy, { hitAt: ctx.time })`.
- **Customising:** a project file in `src/shaders/` with a library shader's name replaces it; the Game panel says where each shader comes from.
- **Shader gallery:** `node tools/shader-gallery/capture.mjs <Shader|--all> [--sheet]` draws each shader on a preview mesh lit like a game and reports compile errors and frame times.
- **Shorter listing:** `list_component_types` shows library shaders in one line each (name, category, what it does), so a fresh project's listing went from about 42 KB to 18 KB; `{"type": "<Shader>"}` gives one shader's fields.
- **Skills:** `stylize-scene`, `make-prop`, `add-mechanic`, `procedural-world`, `playtest-and-fix` and `prepare-for-web` reach for the library first.

### Game shaders and colour
- **Game shaders on Blender materials.** A material says which game shader draws it in the game (Properties › Material › Game, or `set_material_shader`), with typed values. Shaders are TypeScript files in `src/shaders/` (`defineShader`; textures are asset fields). The engine applies them wherever the material is drawn, prefabs spawned later and reloaded levels included, and saving a shader file updates the running game without reloading the level. `patchShader` chains shader edits, so they compose with each other and with baked lightmaps. Game code no longer finds materials by name, and an unknown or failing shader is reported with the material's name ([WORKFLOWS.md](docs/WORKFLOWS.md#game-shaders)).
- **Colour matches Blender.** The game tone-maps with the scene's view transform and exposure instead of always ACES: Khronos PBR Neutral, AgX, Standard and ACES 1.3 carry over; Filmic and ACES 2.0 fall back to ACES with an export warning. The templates, the demo and every scene New Project builds use Khronos PBR Neutral with no look ([WORKFLOWS.md](docs/WORKFLOWS.md#colour-matches-blender)).
- **Templates and skills:** `src/main.ts` loads `src/shaders/*.ts`, `.claude/settings.json` pre-approves `set_material_shader`, and `CLAUDE.md` and the skills say what Blender owns, what a game shader owns, and to judge shader materials from game screenshots.

### Faster Play
Entering game mode no longer rebuilds everything. On a level with 12 rigged characters, 9 of them sharing a skeleton, Play used to re-export for minutes; now it reuses what did not change.
- **GameStudio samples armature clips itself** (docs/SPEC.md §4.1). The glTF exporter sampled every clip by stepping the whole scene, skinning included, frame by frame. On a 224-object test level with 84 rig-clip pairs, the export went from 45–54 s to 2.7 s, or 1.9 s with the clips cached. The output matches the exporter (largest difference 6.4e-7 over 16,380 channels). Clips are sampled once per skeleton and shared, and cached in `.gamestudio/cache/clips/`. Rigs with constraints or drivers stay with the exporter.
- **Export does only what changed** (SPEC §4.8). Nothing changed: the last export is reused (about 0.05 s). Moves, component edits and scene settings: only the manifest is rewritten, with the new poses as `transform` (about 0.05 s). Everything else is a full export from caches. Results say which (`tier`), and `force` asks for a full export. The live link uses the same tiers.
- **Unchanged meshes are not extracted again.** The glTF exporter's mesh extraction is cached in memory by content, with byte-identical output: on the benchmark level a warm full export went from about 3.1 s to 2.4 s, and the exporter's own share from about 1.6 s to 1.05 s.
- **Cached bakes cost little.** An export whose bakes are all cached no longer starts a Cycles scene: 2.9 s → 2.3 s on 150 kit objects, 6.2 s → 4.5 s with Subsurf modifiers.
- **Play keeps the game warm.** Play starts the dev server and the game view while it exports. F8 still turns the area back into the 3D View, but the headless browser keeps running hidden (about 3% of one core), so the next Play reaches a playing frame in about 40 ms instead of 2.8 s. Preferences › "Close idle game after" closes an unused game (15 minutes).
- **The game keeps what did not change.** Manifests list their GLBs' content hashes (`files`): an unchanged GLB is neither downloaded nor parsed again, and a reload clones the cached parse. A `play.start` sent while the game reloads is no longer dropped.
- **Headless play on the GPU.** `playtest`, `play` and `game_screenshot` draw on the GPU when it works and fall back to SwiftShader (`GAMESTUDIO_BROWSER_GPU`, default `auto`). A 10-second playtest takes 0.76 s instead of 2.6–2.9 s; results are identical. `GAMESTUDIO_BROWSER_GPU=0` also makes the game in Blender and the Scene view draw in software, for a GPU driver that misbehaves.
- **Fixes:**
  - The dev server closes export files when a download is aborted. An open file made Blender's export fail on Windows with `PermissionError: [WinError 5]` while the game was open.
  - Only `scenes/*.blend` are levels in `status`, not every .blend in the project.
  - Imported rigged glTF files no longer bring the importer's Icosphere bone shape into the level.
  - A mesh with both a baked material and a lightmap is exported under its own name again.
  - MCP exports time out after 15 minutes, not 3, and say that Blender is still busy.
- `python tools/bench_play.py [--profile] [--browser]` measures every export tier on a heavy test level.

### Agent tools (MCP server, 64 tools)
- **Building:**
  - `create_procedural` and `set_procedural` make geometry-nodes builders (scatter, roads, rivers, fences, cliffs, rock walls, modular buildings) in one undo step each.
  - `create_from_kit {items}` checks every item before it builds anything.
  - `set_material_shader` attaches a game shader and its values to materials, by name or through objects' slots, in one undo step.
- **Playtests:**
  - They run on game time, so the same inputs give the same result on any machine.
  - They play the level open in Blender (`scene` picks another), press menu buttons, aim at moving targets (`mouse: "aim"`), and track every copy of a prefab (`"Grunt*"`).
  - Position samples leave out frames where nothing moved.
- **Measuring:**
  - `game_stats` counts every render pass and warns when it measured a paused game or a menu.
  - `compare_views` renders Blender and the game from the same camera, with the game's post effects off.
  - `scene_summary` and `get_object` give procedural objects their real size and triangle counts.
- **Asset libraries:**
  - Writes are backed up, and `asset_library_restore` takes them back.
  - `asset_add {spawn_only}` no longer leaves a prefab name clash behind.
- **Undo:** the first change after a file is loaded can be undone in background sessions too.
- **Templates:** `save_template` saves the game as a New Project template. Overwriting one keeps the old version in `.backups/`.
- **Linux:** `launch_blender` reports a missing or non-executable Blender clearly.

### Extension
- **A Game workspace instead of the sidebar.** New projects open on it:
  - play controls are in the top bar;
  - components are in Properties › Object › Game;
  - project settings, levels and Build Web are in Properties › Scene › Game Project;
  - Game Assets is a popover in the 3D View header;
  - the console is a Text Editor at the bottom (double-click an error to open the code, never saved into the .blend);
  - Claude's activity, Pause and Protect are behind the agent button in the top bar.
  The old sidebar tab is an option under Preferences › Interface.
- **Save as Template.** User templates appear in New Project under "Your templates", custom skills included ([docs/TEMPLATES.md](docs/TEMPLATES.md)).
- **New Project and online access:** New Project refuses before writing anything when it needs a download and online access is off. A setup that failed resumes from Open Project or New Project, and never deletes files.
- **Agent panel:** live agent activity, Pause, Protect and Undo to Here.
- **Levels:** the Play panel lists the levels, with Export All.
- **Code locations:** error locations resolve on Linux (Vite's `/src/...` paths).
- **Play in Blender, the default.** With the new Embedded game display, F5 streams the game into the largest 3D View of the window, which becomes the Game View while it plays (an Image Editor with no image: the frames are drawn as a GPU texture, letterboxed, and only when a new one comes; nothing goes into the .blend, and a save writes the 3D View). Click the game to play it; Shift+Esc or a click outside gives Blender the input back, and F5-F8 keep working. **Scene | Game** in its header shows the 3D View while the game keeps running. F8 turns the area back into the 3D View with its view unchanged. An icon next to ▶ in the top bar switches to the Window display (the game in its own window, as before). When the game cannot stream (no Edge or Chrome, missing packages) or the stream fails while it starts, that Play opens a window and the status bar says why.
- **Game stream host** for Play in Blender: `gamestudio-stream` (in `@gamestudio/devserver`, which now depends on `puppeteer-core`) plays the game in headless Edge or Chrome on the GPU with sound, and `core/stream.py` shows its frames in Blender and sends input back. On the demo game it measured 60 fps at 960x540 (75 uncapped), about 45 ms from a key press to the decoded frame, and 3.5 ms to decode a frame off the main thread.

### Engine
- **Screens and effects:** title, pause and end screens (`ctx.ui`), particles (`ctx.vfx`, `ParticleEmitter`), post effects, sky and fog.
- **Levels and saves:** `ctx.loadScene`, `ctx.store` and `ctx.save`.
- **Randomness:** `ctx.random`, plus independent named streams (`ctx.random.stream(name)`).
- **Determinism:** turning a capsule or sphere character no longer changes the physics of distant objects, so fights replay exactly.
- **Movement:**
  - Character movement follows the follow camera's rest direction, so long key holds don't drift.
  - A rigged character's capsule leaves out held props.
- **Colliders:** colliders on GPU-instanced scatters work per instance. A mesh with a `Collider` drawn as Wire or Bounds is an invisible collider.
- **Input:**
  - A screen opened on a frame gets that frame's keys, so a playtest's Enter at 0 s starts the game.
  - Movement keys held through a menu count as held once it closes.
- **Animation graphs:** `defineAnimationGraph` declares a typed state machine: 1D blend spaces, transitions on parameters, triggers that are used up, exit times and `next`, any-state transitions, crossfades, speed parameters, enter, exit and named events, and parameters filled from the movement (`from: 'speed'`). It runs from `Animator.graph` in Blender or from `ctx.animation.graph(entity)`, on game time, and picks up hot reloads. `game_state` and `test.state()` show each animated entity's state and parameters ([API.md §15](runtime/packages/engine/API.md#15-animation-ctxanimation-animator)).
- **Navigation:** navmeshes built at scene load from the level's static colliders (Recast via `recast-navigation`, cached, loaded only by scenes that use it), `ctx.nav.path`/`closest`/`debug`, and `NavMesh` and `NavAgent` components; the shooter's enemies path around cover ([API.md §26](runtime/packages/engine/API.md#26-navigation-ctxnav-navmesh-navagent)).
- **2.5D games:** `RigidBody.lockX/Y/Z` keep a body's coordinate exactly (one lock makes a 2D body turning only about it), `CharacterController.plane` `'side'` (left/right along the screen, jumps, the depth never changes) and `'top'` (screen-relative, also under a camera looking straight down), Blender orthographic cameras that keep their height and adapt their width on resize, and a `SideCamera` with a dead zone, look-ahead, smoothing, level bounds and pixel snapping. `node runtime/scripts/side_scroller.mjs` plays a side-scroller level authored in Blender ([API.md §27](runtime/packages/engine/API.md#27-25d-games-side-scrollers-top-down)).

### Export
- **Fidelity:** procedural materials are baked to textures, geometry-nodes instances export as GPU instances, and texture budgets apply.
- **Size:** unreachable data is pruned (the procedural showcase GLB shrank by 30%), and the size report counts draw calls correctly.
- **Levels:** `export_scene {all: true}` exports every level.
- **Smaller builds:** `npm run build` compresses scene geometry with meshopt (`EXT_meshopt_compression`). The compression is lossless and keeps nodes, ids, skins, morphs, clips and instancing, and the runner's scene files shrank by 25%, by 8% gzipped. The build prints each file's size before and after. `"compression": {"textures": "webp"}` makes the export write WebP textures, lossy for colour maps and lossless for data maps. `export_scene {compress: true}` reports what the build will ship. The engine decodes the files without extra decoder files ([WORKFLOWS.md](docs/WORKFLOWS.md#smaller-builds)).
- **Baked lighting:** Cycles bakes lightmaps for static objects, from the `bake_lighting` tool or **Bake Lighting** (Game Project › Lighting, or the Game menu). The game applies them as `lightMap` without lighting anything twice: the sun stays realtime, and the lightmap replaces the fill light on baked meshes ([API.md §25](runtime/packages/engine/API.md#25-baked-lighting-lightmaps)).

### Templates and skills
- **All templates:** starter, platformer, arena shooter, time trial and endless runner open on a title screen, have a pause menu, end on a win or game-over screen, and use particles, sky, fog and bloom.
- **One set of instructions for Claude Code and Codex:** the templates and the demo keep their instructions in `AGENTS.md`, written for any coding agent, and `CLAUDE.md` is the line `@AGENTS.md` plus a short `## Claude Code` section (its pre-approvals and slash commands). Save as Template keeps that shape and gives a project whose instructions are only in `CLAUDE.md` an `AGENTS.md` in the template; New Project does the same for templates saved before. An existing project's `CLAUDE.md` moves only when you agree; otherwise Codex gets a marked copy in `AGENTS.md`, refreshed while nobody edits it, and no text is lost (`core/agentfiles.py`, [TEMPLATES.md](docs/TEMPLATES.md#instructions-for-coding-agents)).
- **Platformer:** 5 lives and a game over. The player is a rigged character animated by an animation graph (idle, walk, run, jump, air, fall, land).
- **Arena shooter:**
  - Hit points live in `Health.current`, where playtests can read them.
  - Enemies sidestep toward open space, and wave spawns use their own random stream.
- **Time trial:** the car no longer stalls on the straight.
- **New: Endless Runner** (*Sunset Skyway*). A three-lane runner whose track is built at runtime from prefab chunks. It shows chunk streaming, Empty sockets inside prefabs, seeded weighted choices, per-instance overrides, event spawns, a nested prefab and a Blender-placed player start. It comes with the `add-chunk` skill.
- **Procedural world:** road ends taper into the ground, and roads draw a smooth verge.
- **Skills:**
  - 9 skills in every project, plus `make-template`.
  - Rewritten from three dogfood runs: tool friction went from 21% to 10%, and calls that need a permission prompt from 57% to 9%.

### Testing and CI
- `.github/workflows/extension.yml` runs the Blender suites, the runtime tests, smoke and features on Windows and Ubuntu, with a pinned Blender 5.2.2.
- `npm run e2e:templates` takes every template from New Project to its ending (56 checks).
- `node runtime/scripts/stream_probe.mjs` measures the game stream on a copy of the demo game: frame rate, latency, input round trip, CPU and sound, through Node and through background Blender.
- `python tools/screenshots.py --only embedded-play` plays a copy of the demo game in a UI Blender with the Embedded display, checks that a click captures input, that W moves the player and that F8 brings the 3D View back, and saves `docs/images/extension/embedded-play.png`.
- Linux was verified in WSL (Ubuntu 24.04). macOS is untested.
