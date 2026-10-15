# GameStudio documentation

GameStudio is a Blender extension for creating and playing 3D web games. Use the illustrated manual to learn the editor, then use the technical references when you need an exact API or file format.

**[Read the user manual](manual/README.md)** · **[Open the illustrated offline edition](manual.html)**

**[Watch the narrated tutorials](videos/index.html)** — 20 click-by-click lessons, from installing GameStudio to publishing a game, with captions and transcripts.

The offline edition includes all manual chapters and embeds its screenshots. Open it in a browser, use the contents to navigate, or print it. Links to the full technical references work when the file stays in this repository's `docs/` folder.

## User manual

| Chapter | What you will learn |
|---|---|
| [Installation and setup](manual/01-installation.md) | Install the extension, complete runtime setup, update and remove it |
| [The editor and play controls](manual/02-editor.md) | Find panels, use Scene view, play, pause, step and read errors |
| [Your first game](manual/03-first-game.md) | Create a Starter project, change the level and add an interactive collectible |
| [Projects and templates](manual/04-projects.md) | Organize files, work with multiple levels, reuse and share templates |
| [Components and game logic](manual/05-gameplay.md) | Physics, cameras, custom TypeScript components, systems and runtime state |
| [Assets and rendering](manual/06-assets.md) | Asset libraries, procedural geometry, materials, shaders and baked lighting |
| [Menus, HUDs and audio](manual/07-interface.md) | Build screens in Blender, connect values, translate text and use sound |
| [Working with coding agents](manual/08-agents.md) | Connect an agent, give useful briefs, inspect actions and recover edits |
| [Testing and web builds](manual/09-publishing.md) | Test a complete game, measure performance and prepare a static build |
| [Troubleshooting and reference](manual/10-reference.md) | Diagnose failures, look up preferences, shortcuts and terminology |

## Technical references

| Reference | Purpose |
|---|---|
| [Engine API](../runtime/packages/engine/API.md) | Exact TypeScript APIs, built-in component fields and runtime examples |
| [MCP tools](MCP.md) | Agent tool arguments, results, conventions and bridge behavior |
| [Game UI](GAME-UI.md) | Canvas authoring, layout, bindings, export and UI runtime details |
| [Project templates](TEMPLATES.md) | Template format, inheritance, placeholders and copying rules |
| [Agent workflows](WORKFLOWS.md) | Detailed examples of the build, inspect and playtest loop |
| [Optional AI assistant](AI-ASSISTANT.md) | Blender's separate proposal-based assistant |
| [Architecture](ARCHITECTURE.md) | Extension, engine, dev server and communication architecture |
| [Specification](SPEC.md) | Design and implementation contracts; consult code for current behavior |
| [Changelog](../CHANGELOG.md) | Changes between versions |

The material under `docs/fork/` describes the shelved custom Blender editor. It is historical reference, not installation guidance for the extension.

## Maintaining this manual

See [Documentation maintenance](manual/MAINTENANCE.md) for rebuilding the offline edition, checking links and reviewing screenshot privacy.
