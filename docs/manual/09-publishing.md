# Testing and web builds

[Manual contents](README.md) · Previous: [Working with coding agents](08-agents.md) · Next: [Troubleshooting and reference](10-reference.md)

## Test the whole game loop

A successful export confirms that files were produced. A useful playtest checks that a player can complete the intended loop. Test from a fresh start as well as from the middle of an editing session.

| Area | Check |
|---|---|
| Entry | Title screen loads, Start works and input reaches the game |
| Movement | Ground collision, slopes, jumps, obstacles and camera behavior |
| Objective | Progress changes correctly and every required target is reachable |
| Failure | Damage, falling, game over and recovery behave as intended |
| Completion | Victory occurs once, at the right time |
| Pause | Simulation stops while menu navigation remains available |
| Restart | Objects and run state reset consistently |
| Levels | Every destination loads with the intended player and state |
| UI | Text fits, focus is visible and settings persist when intended |
| Audio | Playback starts after interaction and volume controls affect the right channels |
| Browser | The built game works at the intended resolution and on target hardware |

Write down a short input sequence for a regression test. For example: start, collect the first pickup, pause, resume, collect the rest, win and restart. Repeat the same sequence after changing the relevant code.

## Automated playtests

The MCP workflow can run input sequences in a controlled browser and return screenshots, state and logs. The engine also exposes a testing API for deterministic stepping. Use a fixed seed and explicit test setup for comparisons that need to reproduce.

A useful test asserts a state change as well as looking at a screenshot: score increased, health fell, a menu opened or a level changed. Tests use isolated saved state rather than changing the player's normal browser save. See [MCP tools](../MCP.md) and the testing section of the [Engine API](../../runtime/packages/engine/API.md) for exact commands.

A headless browser is useful for repeatability, while a manual run remains necessary for the feel of movement, readable text, focus and audio. Do both before sharing a game widely.

## Measure performance

Use `game_stats` through the agent workflow to inspect frame time, draw calls and triangles. Measure from a representative camera and a busy section of the game. A nearly empty title screen is not a representative benchmark for an enemy wave or a dense forest.

| Symptom | Inspect first | Possible improvement |
|---|---|---|
| Many draw calls | Unique meshes and materials | Reuse materials and instances where appropriate |
| Heavy GPU load | Shadow coverage, particles, expensive shaders, resolution | Reduce the dominant visible cost and compare again |
| Slow load | Texture sizes, unused assets, large scene files | Reduce asset sizes and use supported compression |
| Physics slowdown | Complex dynamic collision and many active bodies | Use simpler colliders and fewer active simulations |
| Editor preview feels heavy | Scene view frame rate and geometry re-export frequency | Lower preview rate or temporarily disable live geometry reload |

Change one meaningful variable at a time and repeat the same view or test. The best tradeoff is the one that preserves the game's visual and interaction requirements on its target devices.

## Prepare source and exports

1. Save every changed level, asset library and `ui/ui.blend`.
2. Confirm `gamestudio.json` names the intended start scene.
3. Export all levels and UI from **Game Project > Levels > Export All**.
4. Re-bake any lighting that became stale after scene changes.
5. Review export warnings and resolve missing assets or unsupported assumptions.
6. Run the game's type check and its important playtests.

Generated exports can be stale even when a development game still appears to work from an earlier export. Building after a fresh Export All reduces that mismatch. A new clone may also need regenerated lightmaps because those files are not authored source.

## Build from Blender or the terminal

In Blender, expand **Properties > Scene > Game Project > Build** and choose **Build Web**. Inspect the log if it fails and use the output-folder control when it finishes.

For an existing game project with dependencies set up, the terminal equivalent is:

```sh
npm run typecheck
npm run build
```

Run these commands in the **game project's folder**. The repository root has separate commands for developing the add-on. The project's build writes a static site to `dist/`, including game code, exported scenes, UI and required assets.

## Test the built output locally

With Python available, run this from the game project's root:

```sh
python -m http.server 8080 --bind 127.0.0.1 --directory dist
```

Open `http://127.0.0.1:8080` in a browser, play the complete loop, then stop the temporary server with **Ctrl+C**. On a system whose Python command is `python3`, use that command instead.

This serves the build that will be uploaded. It does not run Vite or rebuild source. Rebuild after changing source or exports. Opening `dist/index.html` directly as a local file is not an equivalent test because browser asset-loading behavior differs from HTTP.

## Compression

Production builds compress copied scene geometry with meshopt by default. Development exports remain ordinary GLB files, keeping iteration straightforward. The engine decodes the compressed assets when the game loads.

WebP textures are an optional project setting. Merge this field into `gamestudio.json`, preserving the rest of the configuration:

```json
{
  "compression": {
    "textures": "webp"
  }
}
```

Texture conversion happens during export, so export again after changing the setting. Color maps use the configured WebP quality, while data textures use lossless conversion. Check the visual result on representative materials and measure the actual build size.

Compression does not remove the need to choose sensible texture sizes, geometry density and asset counts. It reduces transfer size rather than making every expensive scene cheap to render.

## Publish the static game

Upload the contents of the tested `dist/` folder to a static host, preserving its relative file structure. Use the host's normal HTTPS setup and test the deployed URL from a fresh browser session. If deploying below a subpath, verify the project's Vite base configuration and test that actual subpath so assets do not resolve against the wrong location.

The shipped game does not need the Blender authoring application or the development MCP bridge. Do not upload the whole project as the playable build: `.gamestudio/` can contain logs and local connection data, while source folders may contain material intended only for development.

Keep the generated `THIRD-PARTY-NOTICES.md` with the build. For the repository's license breakdown and template asset terms, consult [LICENSE.md](../../LICENSE.md) and the generated notices. Review separately any assets you added yourself.

## Release notes and support evidence

Record the extension version, project revision and tested browser alongside the release. Summarize changes in terms a player can observe, such as a corrected collision or a new level.

For a bug report, supply reproduction steps, expected and actual behavior, a relevant screenshot and the smallest useful log excerpt. Use a neutral test project where possible. Remove personal names, account data, home-directory paths, tokens and unrelated terminal history before sharing the report.

## Contributing to the add-on

The following commands are for the GameStudio **repository**, not a generated game:

| Purpose | Command |
|---|---|
| Install development dependencies | `npm install` |
| Blender suites | `python tools/test_blender.py` |
| Runtime and service tests | `npm test` |
| Type checking | `npm run typecheck` |
| Browser smoke checks | `npm run smoke` |
| Runtime feature checks | `npm run features` |
| Extension archive | `python tools/build_extension.py` |

The repository README describes environment setup and platform prerequisites. Run checks appropriate to the code you changed; a historical test count in an existing document is not a result for a new revision.
