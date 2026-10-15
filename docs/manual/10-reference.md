# Troubleshooting and reference

[Manual contents](README.md) · Previous: [Testing and web builds](09-publishing.md)

## Diagnose in a useful order

Identify which layer is failing: extension loading, project recognition, runtime setup, dev server, export, browser, game logic or agent connection. Read the first relevant message, make one targeted change and repeat the same action. This is more informative than reinstalling every dependency at once.

## Setup and project problems

| Symptom | Likely cause or first check | Action |
|---|---|---|
| No GameStudio menu | Extension disabled, wrong archive or incompatible Blender version | Verify Blender meets the manifest minimum and enable the built extension ZIP |
| No Game workspace | Older file predates the workspace | Choose GameStudio > Add Game Workspace |
| No recognized game project | Open file is unsaved or outside a folder with `gamestudio.json` in its ancestry | Open the project through GameStudio and save the level inside it |
| First-run setup asks to download files | Required runtime packages or browser are absent | Read the dialog, choose Set Up, then wait for completion |
| Setup fails during a download | Connectivity, proxy, permissions or package-service failure | Use Show Log, resolve the stated issue and Retry |
| New Project setup was interrupted | Folder exists but setup did not finish | Open its project folder again or create with the same name to resume setup |
| Newly created project does not replace the current file | Unsaved work is awaiting a decision | Use the pending Open action in the GameStudio menu and choose how to handle that file |
| Project scripts are refused | The received project is untrusted | Inspect the source and use Trust This Project if you choose to execute it |
| Project uses old runtime packages | It has not refreshed after an extension update | Reopen or play it, allow setup to refresh managed packages and inspect the setup log |
| macOS refuses access to Documents | Blender lacks folder access | Allow the relevant folder access in system privacy settings or choose another writable project location |

Do not delete the project to fix a dependency problem. Its source files and generated package state serve different purposes.

## Play and preview problems

| Symptom | Check | Action |
|---|---|---|
| F5 appears to do nothing | Function-key mode and status bar | Try Fn+F5, use Game > Play and inspect setup/dev-server status |
| Play opens a browser window | Display set to Window, or embedded browser unavailable | Select Embedded or complete the offered game-browser setup |
| Player does not move | Game has input, title menu closed, simulation playing | Click the game, use Start/Resume and inspect CharacterController setup |
| Mouse and keys remain captured | Embedded game owns input | Press Shift+Esc |
| Editing view is replaced by the game | Embedded display is active | Use Scene in the area's Scene/Game selector or stop with F8 |
| Pause and Stop controls unavailable | No running game is connected | Start the game and inspect the connection status |
| Scene view is slow | Preview rate or expensive scene | Lower Scene view frame rate and inspect rendering cost |
| Changes do not appear live | Live Link off, wrong level, or live geometry reload off | Enable the relevant setting, open the active level, export and restart if needed |
| An old scene appears after restart | Source or export not updated | Save the intended level, export it and verify the start-scene configuration |

Stopping gameplay does not necessarily close the background browser or dev server. Use Stop Dev Server when you need to end that service, and use the idle-browser preference to control retention between play sessions.

## Components and physics problems

| Symptom | Check | Action |
|---|---|---|
| New component missing from Add Component | File saved, registry built, schema valid | Start the dev server and use Game > Reload Components; inspect schema errors |
| Component exists but does nothing | Matching system imported and game playing | Check `src/main.ts`, query names and Console errors |
| Custom component crashes registry generation | Browser logic placed in a schema file | Keep schema files limited to schema imports and declarations |
| Player falls through the ground | Ground Collider and player physics setup | Add a solid floor collider; use kinematic body and capsule collider for the built-in controller |
| Dynamic object ignores scripted transform | Physics owns its position | Use an impulse, force or physics teleport |
| Trigger blocks movement | Collider Sensor is off | Enable Sensor and implement the overlap reaction |
| Ground contact does not trigger an event | Controller keeps a small skin gap | Use a sensor or distance check for the intended gameplay trigger |
| Side-scroller drifts in depth | Plane mode or axis lock uses the wrong coordinate | Use runtime Y-up conventions; depth is runtime Z |
| Camera follows the wrong object | Target reference or duplicate player setup | Select the intended entity in the camera's component and restart |
| Animation does not play | Missing clip, wrong owner, playback settings or pause | Verify exported clip names and Animator on the object carrying the clips |
| Navigation ignores a floor | Walkable geometry lacks a solid fixed collider | Add appropriate collision and inspect navigation configuration |

When debugging collision, simplify the visual model and collider relationship before adjusting many movement values. Check geometry size, scale, body type and sensor state separately.

## Rendering and UI problems

| Symptom | Check | Action |
|---|---|---|
| Game looks different from Solid view | Different renderers and shading modes | Compare Scene view or a game screenshot against an appropriate Blender render |
| Game shader appears static in Blender | Blender's material preview does not run the shader | Enable Scene view; check its Animate game shaders preference |
| Shader effect missing in game | Shader defined, material assignment, renderer support | Read the Console, verify the shader name and use the supported renderer |
| Light or shadow stays in an old location | Stale static lightmap | Re-bake after geometry, light or world changes |
| Moving object has baked lighting artifacts | Custom movement was not excluded from the bake | Set `gs_lightmap` to False for that object, then re-bake |
| UI screen absent | Correct UI file, exported canvas, open rule | Save the UI file, export all and check UiCanvas settings or the open call |
| Game unexpectedly paused | A menu intentionally owns pause | Close/resume the menu; ensure the HUD is configured as `hud` |
| Text shows no useful score | Game never sets the bound value | Initialize and update `ctx.store.score`, preserving `{score}` in text |
| UI layout changes badly with aspect ratio | Anchor, stretch or text box unsuitable | Adjust top-level anchors and test several window shapes |
| Translation is ignored | Key does not exactly match authored text | Update the JSON key, preserve binding tokens and check selected locale |
| Font looks wrong or misses glyphs | Font asset or language coverage | Use Choose Font to copy it into the project and inspect the runtime result |
| Sound is silent at page load | Browser interaction not yet granted, muted channel or missing clip | Click Start, check volume settings, path and Console |

## Agent and build problems

| Symptom | Check | Action |
|---|---|---|
| Agent cannot find Blender | Intended project open, bridge enabled, correct integration files | Reopen through GameStudio's agent launcher and ask for a scene inspection |
| Agent can read but edits fail | Paused bridge, protected object or tool permissions | Inspect the Agent panel and the concrete permission response |
| Playtests fail while manual editing works | Dev server or automated browser setup | Inspect their logs and finish game-browser setup |
| Build fails on TypeScript | Type or import error | Run the project's typecheck and fix the first relevant diagnostic |
| Build reports missing or stale levels | Export incomplete | Save and Export All, then rebuild |
| Built game fails when opened from disk | Browser file-loading restrictions | Serve `dist/` over HTTP and test the served URL |
| Deployed game cannot load assets | Missing files, wrong relative layout or deployment base | Compare upload contents with `dist/` and test the real deployment subpath |
| Build behaves differently from development | Stale export, state or production-only path | Test the rebuilt `dist/` from a fresh browser session and inspect its Console |

## Preferences

Open **GameStudio > Preferences**. These are the main user-facing controls; defaults below refer to the documented 0.1.0 implementation.

| Preference | Default or behavior | When to change it |
|---|---|---|
| Show launcher at startup | Off | You prefer choosing a game through the launcher each session |
| Game tab in the 3D View sidebar | Off | You want the older sidebar interface |
| Game display | Embedded | You prefer a separate game window |
| Close idle game after | 15 minutes; 0 keeps it until a project change | Balance fast restart against background resource use |
| Set up automatically | Off | You want needed runtime downloads to start without the setup question |
| npm command | `npm` | A custom environment needs another command |
| Open game window on Play | On | Control automatic opening for Window/fallback display |
| Use app-style window | On | Choose between a supported browser's app window and ordinary browser display |
| Live geometry reload | On | Large geometry edits cause expensive repeated exports |
| Scene view frame rate | 30; range 5–60 | Balance preview responsiveness and resource use |
| Animate game shaders in the Scene view | On | Freeze animated surfaces while inspecting them |
| Code editor | Automatic selection | Choose an editor or a custom launch command |
| Coding agent | Select the preferred integration | Change which agent the launcher and top bar open |
| Agent bridge (MCP) | Enables the local agent connection | Connect or disconnect external agent tools |
| Enable AI assistant | Off | Use the separate proposal-based assistant inside Blender |
| Play Shortcuts | F5, F6, F7, F8 | Resolve keyboard conflicts or accessibility needs |

A custom code-editor command can use `{file}`, `{line}`, `{column}` and `{project}` placeholders. Quote paths when needed. Keep machine-specific commands in local preferences rather than copying them into shared project documentation.

## Shortcut card

| Shortcut | Action |
|---|---|
| F5 | Play |
| F6 | Pause/resume |
| F7 | Step |
| F8 | Stop |
| Shift+Esc | Release embedded game input |
| Shift+A > Game UI | Add a UI element while editing a UI file |
| Numpad 0 | Look through the active Blender camera, useful for UI preview |
| Ctrl+Z | Undo a Blender edit on platforms/keymaps using that binding |

Use Fn with function keys when your keyboard requires it. The extension's play keys can be rebound. Blender's normal navigation, transform and saving shortcuts follow your selected Blender keymap.

## Glossary

| Term | Meaning |
|---|---|
| Asset library | A Blender file containing reusable objects or collections |
| Canvas | A UI screen authored as a Blender scene in a UI file |
| Component | Typed data attached to an entity |
| Dev server | Local service that runs the game and rebuilds development code |
| Entity | Runtime handle for an exported or spawned object |
| Export | Generated geometry and scene/UI data consumed by the runtime |
| Game view | A running game, with simulation, input and the game's camera |
| Hot reload | Updating code or supported data while a development game stays open |
| Lightmap | Baked lighting texture for static geometry |
| Live Link | Connection that carries supported Blender edits to a running game |
| MCP | Tool protocol used by coding agents to work with GameStudio |
| Navmesh | A representation of walkable areas used for pathfinding |
| Prefab | A reusable collection that can be instantiated during play |
| Registry | Generated component and shader definitions used by the editor |
| Scene twin | Readable generated description of a Blender level |
| Scene view | The game renderer applied to an editing viewport without playing |
| System | Code that runs game behavior on matching entities or global state |
| Template | Starting project copied by New Project |

## Collect useful diagnostics without personal data

Record the GameStudio and Blender versions, operating-system family, selected template or a neutral reproduction project, exact steps, expected result and observed result. Include the first relevant error and whether it occurs in development, a local build or deployment.

Review screenshots before sharing them: window titles, recent-project lists, preferences, file pickers and terminal output can reveal more than the game itself. Inspect image metadata as well as visible pixels. Share project-relative paths and short relevant log excerpts rather than raw session files. Never attach `.gamestudio/blender.json`, session tokens, API keys or authentication configuration as a debugging shortcut.

For deeper investigation, use the [Engine API](../../runtime/packages/engine/API.md), [MCP reference](../MCP.md), [Game UI reference](../GAME-UI.md) and [Architecture](../ARCHITECTURE.md).
