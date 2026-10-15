# Projects and templates

[Manual contents](README.md) · Previous: [Your first game](03-first-game.md) · Next: [Components and game logic](05-gameplay.md)

## Project structure

```text
garden-quest/
  gamestudio.json
  package.json
  index.html
  vite.config.ts
  scenes/
    main.blend
    main.scene.json
  ui/
    ui.blend
    locales/
      fr.json
  assets/
    props.blend
    fonts/
    sfx/
    ui/
  src/
    main.ts
    components/
    systems/
    shaders/
  AGENTS.md
  CLAUDE.md
  .mcp.json
  .claude/skills/
  .agents/skills/
  .gamestudio/
  dist/
```

Not every project needs every optional folder. New Project supplies the files required by the chosen template.

| File or folder | Role | How to maintain it |
|---|---|---|
| `gamestudio.json` | Project identity, start scene and optional runtime/export settings | Edit deliberately and keep in version control |
| `scenes/*.blend` | Editable levels | Save and back up as source |
| `scenes/*.scene.json` | Generated readable descriptions of levels | Useful for reviewing changes; edit the Blender source instead |
| `ui/ui.blend` | Screens, one Blender scene per canvas | Save independently of the level file |
| `ui/locales/` | Translation tables | Keep with the game's source |
| `assets/` | Libraries, textures, fonts, audio and UI art | Use project-relative references |
| `src/` | TypeScript schemas, systems and shaders | Review code changes and type-check |
| Agent instruction files | Project conventions and workflows | Keep project instructions; exclude local credentials and sessions |
| `.gamestudio/` | Generated exports, caches, logs and local connection data | Do not hand-edit exports or publish the folder |
| `node_modules/` | Installed packages | Regenerate through setup rather than treating it as authored content |
| `dist/` | Web build | Regenerate before release |

Keep the whole project when making a backup. A lone level file may depend on asset libraries, code, fonts and UI stored elsewhere in the project.

## Project configuration

A minimal configuration can look like this:

```json
{
  "name": "Garden Quest",
  "formatVersion": 1,
  "startScene": "main"
}
```

`startScene` is a level identifier, not the display text on a title screen. For the normal one-scene-per-file workflow, `scenes/main.blend` exports as `main` and `scenes/cave.blend` exports as `cave`. Preserve configuration fields created by the template when editing the name or start level.

## Multiple levels

Keep one level per `.blend` file under `scenes/`. This makes opening, exporting and reviewing a level straightforward.

1. Save the existing level before switching files.
2. Create another level file under `scenes/` using Blender's normal file tools.
3. Confirm it appears in **Game Project > Levels**.
4. Set the start scene in `gamestudio.json` if needed.
5. Use **Export All** before building or testing transitions between levels.

Game code can call `ctx.loadScene('cave')` from a system or event handler to change levels. `ctx.store` can carry run state across that transition, and `ctx.save` can preserve selected values across browser reloads. See [Runtime state](05-gameplay.md#runtime-state-and-level-changes).

UI files under `ui/` are not levels. Naming a canvas Title does not make it a scene that `ctx.loadScene('Title')` can load.

## Choose a template

There are five built-in choices, including Starter. Their folder IDs can differ from the labels in the picker.

| Label | ID | Useful starting point |
|---|---|---|
| Starter | `starter` | Small collection game, player, camera, menus and a translated options example |
| Platformer | `platformer` | Jumps, coins, checkpoints, a moving platform and a goal |
| Arena Shooter | `shooter` | Top-down aiming, enemy waves, projectiles, health and score |
| Time Trial | `driving` | Arcade car handling, track gates, lap timing and chase camera |
| Endless Runner | `runner` | Three lanes, power-ups and track chunks spawned from prefabs |

![Platformer template showing floating islands, collectible coins and a character approaching a jump](../images/templates/platformer.png)

*Platformer demonstrates readable jumps, collectibles and a camera following the character.*

![Arena Shooter template showing enemy waves, health and score in a top-down arena](../images/templates/shooter.png)

*Arena Shooter demonstrates a combat loop with spawned enemies, aiming and a health display.*

![Time Trial template with an arcade car, checkpoint marker, lap counter and race timer](../images/templates/driving.png)

*Time Trial provides a complete driving loop that can be adapted to another track.*

![Endless Runner template with a three-lane track, coins, crates, score and distance](../images/templates/runner.png)

*Endless Runner is especially useful for studying streamed prefab chunks and seeded choices.*

The screenshots show the templates in play. New Project copies a template into an independent project; experimenting there does not edit the bundled template.

## Save your own template

1. Bring the game to a useful starting state and test it from its title screen.
2. Save all source files that should travel with it.
3. Choose **GameStudio > Save as Template**.
4. Enter a stable ID, a readable label and a short description.
5. Leave **Save the scene first** enabled when the current level has changes you want to retain.
6. Save, then open **New Project** and select your template to test a fresh copy.

A template can include project levels, code, assets, UI, instructions and custom agent skills. Generated exports, installed packages, local agent state and build outputs are excluded by the template workflow. The full inclusion rules are in [Project templates](../TEMPLATES.md).

A saved template is a starting point, not a complete archival backup: some top-level files are intentionally omitted. Keep a separate backup or repository for the original game.

New Project replaces supported project-name placeholders and updates marked game-title text in `ui/ui.blend`. It does not rewrite every arbitrary sentence or object name containing the old title. Review menus after making a new game from a custom template.

## Collaborate and share source

Commit Blender source, code, configuration, asset files and useful instruction files together. Keep generated caches and private connection files out of the shared repository. Before sharing a template or project, review text, asset metadata, code comments, logs and embedded file paths for information that does not belong in the shared example.

Use project-relative asset locations and test a copy from a different folder. An absolute reference to a local texture can work on one machine while failing for a collaborator. Opening a received project may require explicit trust before its code runs; share the source and expected setup steps so the recipient can make that decision.
