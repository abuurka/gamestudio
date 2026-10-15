# GameStudio: extension listing (draft)

What to paste into the Blender Extensions platform (extensions.blender.org) when GameStudio is
submitted. The manifest (`blender/gamestudio/blender_manifest.toml`) holds the name, tagline, tags,
platforms and permissions; the website takes the rest from this page. Build the zip with
`python tools/build_extension.py`, which also runs `blender --command extension validate`.

## Name and tagline

**GameStudio**: Make 3D web games in Blender with Claude Code

## Short description

Build browser games inside Blender. Claude Code makes the levels, props and gameplay through the
GameStudio MCP server while you watch Blender update; press F5 to play the game in a TypeScript
runtime built on three.js and Rapier.

## Long description

GameStudio turns Blender into a game studio that a coding agent can drive. You describe the game.
Claude Code builds it in your open Blender: it blocks out the level, makes props, attaches game
components to objects, writes the gameplay in TypeScript, plays the game in a headless browser,
looks at the screenshots, and fixes what is wrong. You watch every change land in the viewport,
press **F5** to play, and change anything by hand at any time.

Blender stays the source of truth. Levels are ordinary `.blend` files, so modifiers, geometry
nodes, rigs and node materials keep working. Game behaviour is data on objects (components defined
in TypeScript), and the logic is TypeScript systems that hot-reload while the game runs. The game
ships as a static web build.

The loop takes three steps:

1. **Open a project.** New Project sets everything up (it downloads Node.js if you have none), or
   open the demo game.
2. **Open it in Claude Code.** One click opens a terminal in the project running `claude`. The
   project already has the MCP server configuration, an `AGENTS.md` with the conventions (read by Claude Code and Codex), and
   skills for the common jobs.
3. **Ask, watch, play.** "Make a small game where...", "Add a pickup that...", "Make the level
   prettier". The status bar shows when Claude Code is connected.

Claude Code is optional. Everything it does is also a button, a menu item or a panel: components
in the Inspector, Play and live editing, export, the dev server and the web build.

## Features

- **Open in Claude Code**: a terminal in the project folder running Claude Code, from the
  GameStudio menu, the launcher or the Project tab. Missing agent files are added to older
  projects, and starter prompts are one click away.
- **An agent bridge that is safe by default**: 127.0.0.1 only, a fresh token every session, every
  agent edit one undo step, checkpoints to roll back, and objects you lock are refused.
- **40 MCP tools**: scene queries, kits, materials, components, export, play, scripted playtests,
  game state, logs and labelled multi-view renders the agent checks its own work with.
- **Play in one key**: F5 Play, F6 Pause, F7 Step, F8 Stop. Edits stream into the running game.
- **Components on objects**: add, edit and remove game components in the Inspector (Game tab).
  The fields come from TypeScript schemas.
- **22 parametric asset kits** (props, terrain, water, sky, a rigged character with animation clips,
  and vehicles, weapons, doors and switches with named parts), geometry-nodes builders for whole
  worlds (scatter, roads, rivers, cliffs, buildings) and a 27-preset material palette, for game-ready
  assets with real-world scale, UVs and materials.
- **Project templates**: starter, platformer, arena shooter, time trial and endless runner, plus a
  playable demo game. Save your own games as templates too.
- **Claude Code skills in every project**: new game from a brief, block out a level, make a prop,
  build a procedural world, add a mechanic, animate a character, playtest and fix, stylize the scene,
  prepare for the web, save the game as a template.
- **Web builds**: Build Web in the Project tab makes a static site you can host anywhere.
- **Optional in-Blender AI assistant** that proposes changes for you to apply (needs an Anthropic
  API key; off by default).

## Permissions

| Permission | Why |
|---|---|
| **Network** | The live link between Blender, the dev server and the running game, and the agent bridge, all on `localhost` only. Downloads: Node.js from nodejs.org (a pinned, checksum-verified LTS release) when the system has no Node.js 22.12+, and the project's npm packages. The optional AI assistant calls the Anthropic API when you ask it something. Downloads respect Preferences > System > Network > Allow Online Access. |
| **Files** | Creates game projects in a folder you choose, and reads and writes their files: scenes, TypeScript, exports under `.gamestudio/`, checkpoints. Examples are copied to `Documents/GameStudio/Examples` before they open, so the installation is never changed. Settings and the shared runtime live in the extension's user folder. |
| **Clipboard** | Copies a starter prompt or the Claude Code install command when you click its copy button. GameStudio never reads the clipboard. |

Programs it starts, only when you ask: Node.js and npm (installing packages, the dev server, the
web build), your browser or an app window (Play), your code editor, and a terminal running Claude
Code (Open in Claude Code). Claude Code then starts the GameStudio MCP server from the project's
`.mcp.json`.

## Requirements

- **Blender 5.2 or newer**, on Windows, macOS or Linux (x64 or arm64).
- **Node.js 22.12 or newer.** Optional: GameStudio downloads Node.js 24 LTS the first time it is
  needed if the system has none.
- **Claude Code**: optional, recommended. It needs a Claude Pro, Max, Team or Enterprise plan, or a
  Claude Console account. Install it with the official installer
  ([code.claude.com/docs/en/setup](https://code.claude.com/docs/en/setup)); GameStudio finds it on
  the PATH or where the installer puts it, and shows the install command if it is missing.
- **Microsoft Edge or Google Chrome** for the agent's headless playtests and screenshots.
- An internet connection the first time (Node.js and npm packages); after that, everything runs
  offline except Claude Code itself.

## Licence

The extension is GPL-3.0-or-later; its `LICENSE` is the GPL text. It bundles the runtime packages
(MIT, each with its `LICENSE`), the project templates' code and the demo game (MIT-0) and the
starter template's sounds, music and UI images (CC0-1.0, listed in its `assets/LICENSE.txt`). The
manifest's `license` names all four. A game made with GameStudio is its maker's to license as they
like: MIT-0 and CC0 ask for no credit, and the game runs on the MIT runtime, not on Blender.

## Screenshots

Captured from stock Blender 5.2.2 (a 1600 x 900 window, a scratch profile, the extension from this
checkout, a copy of the demo game) into `docs/images/extension/`. `04`, `05` and `06` are one editor
of that window; the others are the whole window. In `08` to `10` the agent is the real
`gamestudio-mcp` server driven by a scripted MCP session that introduces itself as Claude Code; it
built the tree, the rock and the barrel through `create_from_kit`. The game's messages in the
Console are sample log entries (no game runs). Regenerate the set with
`python tools/screenshots.py` (`--only 03-game-workspace ...` for some of them).
`embedded-play.png` is taken only on request, `python tools/screenshots.py --only embedded-play`:
it presses Play, so the copy's dev server and the game stream run (Node.js, the project's
packages and Edge or Chrome are needed), and the Console shows the game's own messages.

| File | Shows |
|---|---|
| `01-welcome.png` | The welcome after the extension is first enabled: the three steps, New Project, Open Demo, Open in Claude Code (greyed until a project is open), Node.js and Claude Code |
| `02-launcher.png` | The launcher with a project open: start, examples, recent projects, Node.js and Claude Code |
| `03-game-workspace.png` | The Game workspace: the level in the 3D View, the player selected with its components in Properties, the game's messages in the Console, Play / Pause / Step / Stop and the agent button in the top bar, the status bar |
| `04-object-game.png` | Properties > Object > Game: the entity ID, Add Component and the player's components with their fields |
| `05-scene-project.png` | Properties > Scene > Game Project: the project, the dev server, the component registry, Open in VS Code, Open in Claude Code, Live Link, Levels with Export All, Scripts and Build |
| `06-console.png` | The Console: the game's messages with an error, and its header with the filters, the newest error, Dismiss and Clear |
| `07-install-claude-code.png` | When Claude Code is missing: the install dialog with the official commands, and "Claude Code is not installed" in Game Project |
| `08-claude-connected.png` | The objects the agent just built, selected, with the agent in the top bar and "Claude: idle" in the status bar |
| `09-agent-popover.png` | The agent popover from the top bar: Pause, Claude Code connected, Protect, the recent actions with Undo, the newest one's details with Undo to Here, Open Activity Log |
| `10-game-assets.png` | The Game Assets popover in the 3D View header: a `props` library made from the agent's objects, with Place and Replace |
| `11-preferences.png` | The preferences, grouped: Claude Code, Agent bridge, Node.js, Play, Code editor, Interface (the optional sidebar tab) |
| `embedded-play.png` | Play with the Embedded game display: the demo game running inside the Game workspace's 3D View at 60 fps, with Scene / Game and "Click the game to play" in its header, Play pressed and the game display icon in the top bar |

## Before submitting

- The manifest has no `website` yet: add one when GameStudio has a public home page or repository.
  The Documentation menu item (`menus.DOCS_URL`) is a placeholder until then.
- Check the platform's current rules for extensions that bundle JavaScript sources and start
  external programs (Node.js, a terminal); the explanation above is written for that review.
- Re-capture the screenshots if the UI changes: `python tools/screenshots.py`.
