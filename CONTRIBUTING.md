# Contributing to GameStudio

Thanks for helping. GameStudio is an early preview, so bug reports from real machines are the most useful thing you can send, especially from **macOS**, which nobody on the project has tested yet.

## Reporting a bug

[Open a bug report](https://github.com/abuurka/gamestudio/issues/new?template=bug_report.yml). The form asks for:

- **Versions:** GameStudio (Edit › Preferences › Get Extensions, under GameStudio), Blender (Help › About) and your operating system.
- **Your agent**, if one was involved: Claude Code, Codex or another MCP client, and the model.
- **Steps to reproduce**, starting from a template or the demo game if you can.
- **The log.** When setup, Play or an agent install fails, the message has a **Show Log** button that opens the log file. Paste the relevant part. Other logs are in your project's `.gamestudio/` folder: `devserver.log` (the game's dev server), `stream.log` (the game view inside Blender) and `build.log` (web builds).

Read logs and screenshots before you post them. Paths can show your user name, and window titles and recent-file lists can show more. **Never post** `.gamestudio/blender.json`, tokens, API keys or your agent's settings files.

Security problems: please don't open a public issue. Use [Report a vulnerability](https://github.com/abuurka/gamestudio/security/advisories/new) instead.

## Suggesting a feature

[Open a feature request](https://github.com/abuurka/gamestudio/issues/new?template=feature_request.yml). Describe the game you were making and where GameStudio got in the way. That helps more than a proposed solution alone.

## Working on the code

The [README](README.md#developing-gamestudio) shows how to run the extension from a clone, and [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) explains how the parts fit together. In short:

```bash
npm install                        # runtime packages and tools (Node.js 22.12+)
python tools/test_blender.py       # Blender suites (addon, shell, contract), Blender 5.2+
                                   # (CI runs them on Blender 5.2.2 and 5.3.0)
npm test && npm run typecheck      # engine, dev server, MCP server
npm run smoke && npm run features  # games in a headless browser
```

A few rules keep the project healthy:

- **Tests never touch your Blender profile.** The runners start Blender with `--factory-startup` and a throwaway user folder. Any script you add that starts Blender, even a one-off check, must do the same (`tools/blender_dev.py` has `dev_env()` for this). That folder also holds Blender's cache (Blender 5.3 keeps its Vulkan shader caches there), so a test run never writes into your real Blender cache. The UI smoke test (`python blender/tests/ui/run_ui_smoke.py --blender PATH --out DIR`) opens a real Blender window for about half a minute, or over a minute on Blender 5.3 with Vulkan; pass `--gpu-backend opengl` or `--gpu-backend vulkan` to choose the backend it draws with.
- **Add or update tests with your change.** CI runs everything above on Windows and Linux (macOS is experimental), and a pull request should be green.
- **No new manual setup for users.** Anything that has to be downloaded goes through the extension's first-run setup and its one consent prompt (`environment.ensure_ready`). User-facing text never says "run npm" or "install X".
- **Say "your agent".** GameStudio works with any MCP agent; Claude Code and Codex are examples, not the only option.
- **Licences differ by folder:** the extension is GPL-3.0-or-later, the runtime packages are MIT, and the templates are MIT-0 or CC0 ([LICENSE.md](LICENSE.md)). Your contribution is licensed the same way as the folder it goes into. Keep the runtime free of GPL code, because games ship with it.
- **Media stays out of git.** Videos and promo clips are hosted on the `site-media` release ([tools/site_media.py](tools/site_media.py)), and `docs/manual.html` is built when the site deploys.

Keep each pull request to one subject, and say in the description what you tested and on which OS.

## Releases

A release is the extension zip built by `python tools/build_extension.py`, attached to a GitHub release (not a pre-release, so the website's Download button finds it). The version is `version` in `blender/gamestudio/blender_manifest.toml`, and the `@gamestudio/*` packages carry the same number. Add a section to [CHANGELOG.md](CHANGELOG.md) for each release.

To cut a release:

1. **Set the version everywhere it lives.**
   - `version` in `blender/gamestudio/blender_manifest.toml`.
   - `version` in the four `runtime/packages/*/package.json` files, and every `@gamestudio/*` dependency in `runtime/packages/engine/package.json` and `examples/demo-game/package.json`.
   - The two lockfiles, regenerated rather than edited: `npm install --package-lock-only`, then `python tools/runtime_lock.py`. Only the version lines should change.

   `runtime/packages/devserver/test/versions.test.ts` fails while any of these disagree.
2. **Update the changelog and the docs.** Turn "Unreleased" in `CHANGELOG.md` into a dated section. Search the repository for the old version number: the manual (`docs/manual/README.md`, `docs/manual/10-reference.md`), the README's install step and the bug report form name it. Leave `docs/videos/` alone; each lesson names the version it was recorded with. Then run `python tools/build_manual.py --check` ([docs/manual/MAINTENANCE.md](docs/manual/MAINTENANCE.md)).
3. **Test on every Blender the release supports**, today 5.2.2 and 5.3.0: the commands under [Working on the code](#working-on-the-code) with `--blender PATH` for each, and the UI smoke test. Run the full Blender suites one at a time; a few tests are timing-sensitive on a loaded machine.
4. **Build the zip.** `python tools/build_extension.py` writes `dist/gamestudio-<version>.zip`. It refreshes `blender/gamestudio/core/skill_history.json` (commit it if it changed: projects made with older skills depend on it), refuses a stale `core/runtime.lock.json`, and has Blender build and validate the archive.
5. **Commit, tag and publish.** Commit as `Release <version>`, tag that commit `v<version>` and push both. Create a GitHub release for the tag with the zip attached and the changelog section as its notes. It must be a normal release marked as the latest, not a pre-release.
6. **Check the result.** The website redeploys when `docs/` changes on `main`. Once it has, its Download button should offer the new zip and the manual should show the new version. Installing the zip over the previous version in Blender should keep your projects working.
