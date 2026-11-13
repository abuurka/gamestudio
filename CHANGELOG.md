# Changelog

## Unreleased

- **Blender 5.3 support.** GameStudio, the extension for Blender 5.2+, is now tested with Blender 5.3.0 next to 5.2.2. The mesh cache recognises 5.3's glTF exporter, so repeat exports stay fast. Live game-shader edits stay instant under 5.3's changed update tagging. Drawing was checked on Vulkan, 5.3's default graphics backend on Windows and Linux. CI runs the extension suites on both versions (the 5.3 jobs don't block a merge until they have passed).
- **Test runs leave your Blender's cache alone.** Blender 5.3 keeps its shader cache outside the profile folder, so a test run could write into your real Blender's cache. The suites, the UI smoke test and the end-to-end scripts now keep that cache in their throw-away profile as well. The UI smoke test follows the Game workspace and takes `--gpu-backend opengl|vulkan`, and the stream probe's Blender phase runs again.
- **Install from the release.** The README and the manual now start from the zip on the latest GitHub release (drag it onto Blender's window). Building the zip yourself is only for people working from a clone.
- **macOS in CI** (experimental): the extension and runtime suites run on Apple Silicon runners against Blender 5.2.2 for macOS. They don't block a merge yet.
- **Fixes for CI failures.** On Linux, a test picked up the runner's own browsers, and spotting Chrome Headless Shell from a Windows path failed (`isHeadlessShell`, in the dev server and the MCP server).
- **One version number.** The `@gamestudio/*` packages now carry the extension's version (0.1.1), and a test keeps them in step.
- **A smaller repository.** The tutorial videos and promo clips are now assets of the `site-media` release. The website workflow fetches them and builds `manual.html` when it deploys, so neither is in git any more.
- **For contributors:** `CONTRIBUTING.md`, issue forms for bugs (asking for versions, OS, agent and the log from **Show Log**) and features, a pull request template, and CI badges in the README.

## 0.1.1 (15 October 2026)

- **Works with your agent, not just Claude Code.** The add-on, the MCP server's messages, the docs, the manual and the website now speak of your coding agent. Claude Code stays the default; Codex and any other MCP client are first-class. The MCP server's hints now name each agent's own settings (`.mcp.json` for Claude Code, `.codex/config.toml` for Codex).
- **The in-Blender AI assistant can use other models** (experimental). Next to Anthropic (Claude), the default, a new "OpenAI-compatible" provider works with OpenAI, OpenRouter, Ollama, LM Studio and other servers with that API, with nothing extra to install. A local server needs no key or internet access. Models without a known price show "cost unknown" instead of a wrong cost.
- **Templates:** the `make-template` skill and `save_template` now say that `.codex/` and `.agents/` travel with a template.
- **Docs:** corrected the tool count (64), Codex setup, what Claude Code's project settings pre-approve, and the shader count; new screenshots of the welcome, launcher, Game Project panel and preferences.

## 0.1.0 (15 October 2026)

The first public release, an early preview.

- **Your AI agent builds the game.** A Blender 5.2+ extension and an MCP server with 64 tools: any MCP-capable agent can make assets, build levels, write gameplay code, play the game and fix what's wrong. Claude Code and Codex connect in one click.
- **Build:** 22 parametric kits, procedural worlds on geometry nodes, asset libraries placed as prefabs, and a 27-preset material palette.
- **Make it a game:** components and TypeScript systems with hot reload, animation state machines, navigation meshes, audio, particles, several levels with saved progress, and 2.5D support.
- **Make it look good:** 33 ready-made game shaders, baked lighting, and menus and HUDs laid out in Blender, with translations.
- **Play and test:** play inside Blender (F5) or in a window, scripted playtests in a headless browser, and frame-time and draw-call stats.
- **Stay in control:** every agent action shows live with Undo to Here, plus pause, protected objects, checkpoints, and trusted projects.
- **Ship:** static web builds with compressed geometry and WebP textures, licence notices included.
- **Templates:** Starter, Platformer, Arena Shooter, Time Trial and Endless Runner. Save your own game as a template too.
- **Setup:** the first run downloads what it needs (Node.js, the game packages and, if needed, a browser) into the extension's own folder, after one question.

Tested on Windows with Blender 5.2.2, and on Linux in WSL. macOS is untested.
