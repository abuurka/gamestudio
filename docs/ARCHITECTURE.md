# GameStudio architecture

GameStudio lets **a coding agent build games inside Blender**: Claude Code or Codex in one click,
or any other MCP client. One app, Blender with the GameStudio
extension, holds the 3D assets, the levels and the game objects. A TypeScript runtime plays the game
in a browser. An MCP server gives the agent one set of tools across all of it, so the agent never
switches between a modeling tool, an engine and an editor, and it can always see and test what it
built.

The person stays in charge: they describe the game, watch Blender update, play the result, and can
change anything by hand.

## 1. The loop

```
 brief ──► build in Blender ──► attach components ──► write systems (TypeScript)
              ▲                                                │
              │                                                ▼
             fix ◄── look (render_views) ◄── measure (game_state, logs) ◄── play (playtest)
```

Every step has a tool, and every tool returns something the agent can check:
- a rendered image;
- a validation report;
- a game screenshot;
- game state;
- a log line with file and line.

The skills that ship in each project (`.claude/skills/`, copied to `.agents/skills/` for Codex) walk
the loop for common tasks:
- `new-game-from-brief`
- `blockout-level`
- `make-prop`
- `procedural-world`
- `add-mechanic`
- `animate-character`
- `playtest-and-fix`
- `stylize-scene`
- `design-ui`
- `prepare-for-web`
- `make-template`

## 2. Pieces

| Piece | Where | Language, licence | What it does |
|---|---|---|---|
| Extension | `blender/gamestudio/` | Python, GPL | Components on objects, export, live link, Play (F5/F6/F7/F8), menus, launcher, workspaces, Node.js setup, new projects |
| Agent bridge | `blender/gamestudio/bridge/` | Python | Localhost JSON-RPC server inside Blender (token handshake, main-thread execution, one undo step per change, checkpoints) |
| Asset kits | `blender/gamestudio/kits/` | Python | 22 parametric generators for game-ready assets (props, terrain, water, sky, a rigged character with clips, and assemblies with named parts: vehicle, weapon, projectile, door, switch) and a 27-preset material palette |
| Procedural toolkit | `blender/gamestudio/procedural/` | Python | Geometry-nodes builders made in code: scatter, objects along curves, roads, fences, rivers, cliffs, rock walls, modular buildings; their inputs stay editable |
| MCP server | `runtime/packages/mcp/` | Node, MIT | Launched by the coding agent: Claude Code from the project's `.mcp.json`, Codex from `.codex/config.toml`. Turns tool calls into bridge, hub and headless-browser actions. 64 tools; see `docs/MCP.md` |
| Engine | `runtime/packages/engine/` | TypeScript, MIT | ECS on three.js and Rapier: components, systems, game shaders on Blender materials (with a library of 33), Animator, Locomotion, AudioSource, prefabs (`ctx.spawn`), timers, HUD, UI screens, particles and post effects, seeded `ctx.random`, levels (`ctx.loadScene`) and saves, animation state machines, navigation meshes (Recast), lightmaps, 2.5D (axis locks, plane modes, orthographic and side cameras) |
| Dev server | `runtime/packages/devserver/` | Node, MIT | Vite plugin: hot reload, component registry, the live-link hub between Blender, games and the MCP server |
| Stream host | `runtime/packages/devserver/bin/gamestudio-stream.js` | Node, MIT | Play in Blender: runs the game in headless Edge or Chrome, or the Chrome Headless Shell GameStudio downloads when neither is installed (GPU, sound on), and sends its frames to Blender over TCP; Blender sends input back (`blender/gamestudio/core/stream.py`) |

```
 Your agent  ──stdio──► @gamestudio/mcp ──TCP + token──► Blender + extension (bridge)
                              │                                  │ export .glb + manifest
                              ├──TCP──► dev-server hub ◄─────────┘ live edits
                              │              ▲
                              └──CDP──► headless browser ── game (engine) ──WebSocket──┘
```

Play in Blender: Blender's Python API cannot embed a web page, so `core/stream.py` starts the
stream host with the project's Node.js. The host prints `{"port", "token"}`, starts new headless
Edge/Chrome with puppeteer-core and sends CDP screencast frames (JPEG, passed through) to
127.0.0.1; a reader thread in Blender decodes them with `imbuf` and the area draws the newest one.
Mouse and keys go back as JSON and become CDP input events. The page's user agent carries
`GameStudioStream/<pid>`, so Blender tells the streamed game from a browser tab on the live link.

Entering game mode reuses everything it can:
- **Play overlaps the slow steps.** It starts the dev server (when it is down) and the stream host
  first, then exports, and sends `scene.load` + `play.start` once the game is up. The host can start
  without a URL and loads the game when Blender sends `navigate`, as soon as Vite answers.
- **The export does only what changed** (SPEC §4): nothing (`reuse`), the manifest alone
  (`layout`), or a full export whose slow parts come from caches.
- **Stop keeps the game.** It sends `play.stop` and turns the area back into the 3D View, and the
  browser and page keep running hidden: no frames are sent while no Game View shows the game, and a
  stopped game's headless window is minimized, so it stops rendering (about 3% of one core). The
  next Play shows the page again and reuses it: about 40 ms to a playing frame on an RTX 4070,
  against 2.8 s for a new browser. Freezing the page (`Page.setWebLifecycleState`) is not used: in
  headless Edge a thawed page stayed hidden and never drew again. The game closes when the user
  closes the Game View while it shows the game (its X, closing the area or changing its editor),
  when another project opens, when Blender quits, when the Window display is chosen, and after
  Preferences › "Close idle game after" minutes without a Play (15 by default; 0 never).
- **The engine keeps what did not change.** A manifest lists its GLBs' content hashes (`files`):
  an unchanged GLB is neither downloaded nor parsed again; the scene is cloned from the parse
  (shared geometry and textures, its own materials). The dev server answers hashed requests
  (`?h=`) with an immutable cache header once it has checked the hash, and closes every file when
  its request ends, so Blender can always replace the files.

The MCP server's headless game (`playtest`, `play`, `game_screenshot`) draws on the GPU too, with
the stream host's switches. It checks WebGL2 once per browser and restarts it with SwiftShader when
the GPU is missing or software, so CI and machines without a GPU still play. On an RTX 4070 a
10-second playtest takes 0.76 s instead of 2.6–2.9 s; results are identical on either renderer. The wire format is in
`runtime/packages/devserver/src/stream-protocol.js`; `node runtime/scripts/stream_probe.mjs`
measures it (demo game, RTX 4070: 60 fps at 960x540, 3.5 ms decode, about 45 ms from key to pixels).

The browser, for the stream host, the MCP server and the runtime scripts alike
(`runtime/packages/devserver/src/browser.js`; the MCP server keeps a copy in `src/discovery.js` and
a test keeps the two equal): `GAMESTUDIO_BROWSER` (or `GS_BROWSER`), else Edge, Chrome, Chromium,
Brave or Vivaldi installed on the system, per-user installs and Beta/Dev/Canary channels included
(`GAMESTUDIO_BROWSER_SYSTEM=0` skips them), else the Chrome Headless Shell GameStudio downloaded. The
stream host and the MCP server try the next one when a browser does not start (a broken install, a
confined snap). The extension's first-run setup downloads the shell when the machine has none (a
Mac with only Safari, a bare Linux): `devserver/bin/gamestudio-browser.js install --dir <user
folder>/browser` fetches the build puppeteer-core is made for (155.0.8059.39; 100–125 MB per
platform, SHA-256 pinned) with `@puppeteer/browsers`, runs it once (`--version`) and only then writes
`browser.json`, the marker discovery looks for in `GAMESTUDIO_BROWSER_DIR` and in the GameStudio
user folder of every Blender profile (so the MCP server, started by the agent without Blender's
environment, finds it too). The shell has no window (the host's `offscreen` mode becomes headless)
and draws with SwiftShader unless asked for the GPU, so both get `--enable-gpu` for it: then it uses
the platform's own backend (D3D11 on Windows, Metal on macOS; on an RTX 4070, 60 fps at 960x540),
falls back to software where the GPU fails, and screencast, input and the minimized page of a stopped
game work as in Edge. No Windows-only switch is passed elsewhere (`--use-angle=d3d11` is Windows only).

The bridge serves requests on Blender's main thread. In the UI a timer pumps it; in `--background`
mode (no timers) `serve_forever()` loops instead. The MCP server finds Blender through
`<project>/.gamestudio/blender.json` and can launch it (`launch_blender`) when none is running.

## 3. A project

```
my-game/
  gamestudio.json        project marker and settings
  scenes/*.blend         levels: the 3D source of truth (geometry, materials, rigs, animation, components)
  src/components/*.ts    component schemas (the fields edited in Blender)
  src/systems/*.ts       game logic
  src/shaders/*.ts       game shaders: how the game draws the Blender materials that name them
  AGENTS.md              conventions and the loop, for the coding agent (Claude Code or Codex)
  CLAUDE.md              @AGENTS.md, plus Claude Code's own notes
  .claude/skills/        workflows; .claude/settings.json pre-approves the read-only tools
  .mcp.json              starts @gamestudio/mcp for Claude Code
  .codex/config.toml     the same for Codex, with .agents/skills/ (Open in Codex adds them)
  .gamestudio/           generated: exports, registry, logs, checkpoints, discovery files
```

- **3D:** the `.blend` files keep Blender's non-destructive data (modifiers, geometry nodes, rigs,
  node materials), which any interchange format would lose. Agents change them through the bridge,
  never by editing bytes.
- **Behaviour data:** components stored on Blender objects, defined by TypeScript schemas
  (`defineComponent`). The registry the dev server generates keeps both sides in step.
- **Logic:** TypeScript systems, edited directly by the coding agent and hot-reloaded.
- **Look:** Blender owns meshes, UVs, materials and static colour. A material can name a game shader
  (`defineShader` in `src/shaders/`) for what moves or reacts in the game; the engine applies it
  wherever the material is drawn, so code never finds materials by name. The engine's shader library
  (`runtime/packages/engine/src/shaders/library/<name>/`, 33 shaders: `meta.ts` registers the fields,
  `impl.ts` holds the GLSL in a chunk loaded only by scenes that use it) is listed with the project's
  own; a project file with a library shader's name replaces it. The game tone-maps with the scene's
  Color Management (view transform and exposure), so its colours match Blender's.
- **Generated:** everything in `.gamestudio/`. Never edited, always rebuildable.

## 4. Installing and developing

- **Users:**
  - Install `dist/gamestudio-<version>.zip` in Blender 5.2+ (Get Extensions › Install from Disk).
  - New Project downloads a pinned Node.js if needed, sets up a shared copy of the runtime packages
    in the extension's user folder, and links each project to it.
- **Developers:** no build step.
  - `tools/blender_dev.py` points Blender at `blender/gamestudio` through a junction, so editing the
    Python takes effect after a Blender restart, and TypeScript changes hot-reload.
  - `python tools/test_blender.py` runs the Blender suites with a throw-away profile.
  - `npm test`, `npm run typecheck`, `npm run smoke` and `npm run features` cover the runtime.
  - `python tools/build_extension.py` makes the zip.
- **CI** (GitHub-hosted runners, `.github/workflows/`):
  - `extension.yml`, on every push and pull request, on Ubuntu and Windows: stock Blender 5.2.2
    (pinned URL and SHA-256, cached; `tools/ci/install_blender.py`), then typecheck, vitest, the
    Blender suites, the zip (an artifact from Ubuntu), smoke and features in headless Chrome.
  - `runtime.yml`: typecheck and vitest on Node 22.12 (the minimum) and the pinned portable Node.
  - `editor.yml` and `canary.yml` belong to the shelved fork and only run by hand.

## 5. Safety

- The bridge listens on 127.0.0.1 only and requires the per-session token from the discovery file.
- `run_python` can do anything Blender's Python can, so the project never pre-approves it: Claude Code
  asks before each call, and Codex follows its own approval settings. Every call is one undo step and
  rolls back on error.
- `checkpoint` and `restore_checkpoint` give coarse rollback.
- The person stays in control from Blender's **Agent panel** (`activity.py`, Game tab and status bar):
  - it shows each agent action live and logs it to `.gamestudio/agent-activity.jsonl`;
  - **Undo to Here** rewinds to just before any agent step;
  - **Pause** makes mutating calls fail with `paused_by_user` while reads keep working;
  - **Protect** locks objects against every tool, including `run_python`, which is rolled back if it touches one.
- Claude Code's project settings (`.claude/settings.json`) pre-approve read-only tools and reversible edits: scene edits are undo steps, and asset-library writes are backed up first (`asset_library_restore` reverses them). `run_python`, `delete_objects`, `restore_checkpoint` and `open_scene` always ask.
- Tests and tools never touch the user's Blender profile: they always use a scratch profile.

## 6. History

GameStudio began as a thin fork of Blender (`editor/`, documented in `docs/fork/`), with native
editors and a game view docked inside the editor. The real goal turned out to be the agent loop,
which needs none of the fork, so the fork is shelved and the extension is the product.
