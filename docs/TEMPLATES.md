# Project templates

New Project copies a template into a new folder. A template is a folder of project files: code, agent files, sometimes scenes. GameStudio ships five, including Starter. You can save any game as your own template and start new games from it.

## Built-in templates

| Id | Label | What a new game starts with |
|---|---|---|
| `starter` | Starter | A player, a follow camera and three pickups on a lawn, with title, pause, options and win menus and a HUD made in Blender (`ui/ui.blend`, a turning 3D gem on the title, a Language button), a French translation (`ui/locales/fr.json`), and background music |
| `platformer` | Platformer | A third-person 3D platformer: jumps, coins, checkpoints, a ferry and a goal |
| `shooter` | Arena Shooter | A top-down arena shooter: mouse aim, waves of chasing enemies, health and score |
| `driving` | Time Trial | Arcade driving: a race track, checkpoints, a lap timer and a chase camera |
| `runner` | Endless Runner | Three lanes, coins and power-ups on a track built at runtime from prefab chunks; the reference for spawning prefabs from code |

They live in the extension, in `blender/gamestudio/project_templates/<id>/`. The genre templates build on the starter (see Composition), and each adds a skill for its genre: `add-level-section`, `add-enemy-type`, `tune-handling` and `add-chunk`. Every template's menus and HUD are screens made in Blender, in `ui/ui.blend` ([GAME-UI.md](GAME-UI.md)): the genre templates build their own with `tools/build_ui.py`.

The code and text a built-in template copies into your game (code, configuration, agent files, skills) are MIT-0, and so are the script templates New Component and New System copy (`blender/gamestudio/project_templates/LICENSE` and `script_templates/LICENSE`, which stay in the extension). The starter's sounds, music and UI images are CC0, a public domain dedication: every new game gets `assets/LICENSE.txt`, which lists them. Neither asks for credit, so you can license your game as you like.

## Where templates live

- **Built-in:** `project_templates/<id>/` inside the extension.
- **Yours:** `Documents/GameStudio/Templates/<id>/` in your home folder. Set the `GAMESTUDIO_TEMPLATES` environment variable to use another folder. Blender reads it at start-up, so restart Blender after you change it.

New Project lists both. Your templates come after the built-in ones, under "Your templates". When a folder of yours has the id of a built-in template, the built-in one wins and yours is not shown.

A folder in your templates folder is a template when:

- its name is a valid id: up to 64 lowercase letters, digits, `-` and `_`, starting with a letter or a digit;
- it holds a `template.json`, or at least a `gamestudio.json`.

Other folders are ignored, and so are folders whose names start with `.`.

## What New Project does

1. It copies the template's files into the new folder. Text files (`.json`, `.ts`, `.md`, `.html`, `.css`, `.txt`, `.gitignore`) get their placeholders filled in. Other files are copied as they are.
2. It records the template's id in the new `gamestudio.json` (`"template"`). Open in Claude Code and Open in Codex use it to restore missing agent files from the same template.
3. It sets up Node.js and the packages, links the project to GameStudio's packages and runs `npm run registry`.
4. It opens the start scene, `scenes/<startScene>.blend`:
   - if the template ships that file, it opens the new project's copy and exports it, so Play works at once;
   - else, if the template has `tools/build_level.py`, it runs its `build(gs, root)`;
   - else it builds a small starter scene.

   Before that, it builds the game UI, `ui/ui.blend`, when the template ships none ([GAME-UI.md](GAME-UI.md)). It checks for `ui/ui.blend`, not for `ui/`: the starter ships `ui/locales/fr.json`, copied in step 1, and the save that ends the build exports it with the screens:
   - a template with `tools/build_ui.py` runs its `build(gs, root)`, as for `tools/build_level.py`. The script writes the file with `gs.gameui` (it starts with `gs.gameui.files.create_here(path, root, canvases)`), saves it, which exports the screens, and returns its path (`projects.build_ui`, `UI_SCRIPT`). It wins over the starter's built-in UI. The platformer, shooter, runner and driving templates ship one;
   - else a template whose `template.json` has `"starterUi": true` (the starter) gets the starter's game UI: the screens `Title`, `Pause`, `Options`, `Win` and `HUD`, built with `gs.gameui` in the project's name.

   If a build fails, what it wrote is removed, the log says why and the game plays without its UI.

   A template that ships `ui/ui.blend` (one of yours) gets no new game UI: New Project opens the new game's copy first and gives its title the game's name ([below](#the-games-name-in-uiuiblend)).

   A scene New Project builds starts with the view transform Khronos PBR Neutral and no look, which the game follows (Color Management; [WORKFLOWS.md](WORKFLOWS.md#colour-matches-blender)). The built-in level scripts set it themselves too, so a rebuild keeps it. A shipped scene keeps its own settings.

## template.json

`template.json` describes a template. It is never copied into a project.

| Field | Meaning |
|---|---|
| `label` | The name in the New Project picker. Default: the id. |
| `description` | One line for the picker. Keep it short: the dialog shows about 78 characters. |
| `icon` | A Blender icon name, such as `FILE_NEW` or `AUTO`. Default: `FILE_BLANK` (yours: `USER`). |
| `order` | A number that sorts the picker, lowest first. Default: 100. |
| `base` | The id of another template to start from (see Composition). |
| `exclude` | Paths of the base to leave out, such as `["src/systems"]`. A folder path drops everything in it. |
| `starterUi` | `true`: New Project builds the starter's game UI, `ui/ui.blend` (above). A template that builds on this one does not inherit it. |

Save as Template also writes `sourceName` (the game's name, which New Project replaces in `ui/ui.blend`; [below](#the-games-name-in-uiuiblend)), `saved` (the date) and `from` (the project and the template it came from). New Project ignores `saved` and `from`, except that a template saved before `sourceName` existed uses the project name in `from` for it.

## Composition

A template with a `base` gets the base's files first, minus its `exclude` paths. Its own files are added on top, and replace base files with the same path. The base can be a built-in template or one of yours, and can have a base of its own.

This keeps one copy of shared files. The genre templates take the starter's skills, `CLAUDE.md`, `.mcp.json`, `.claude/settings.json`, configuration files, sounds (`assets/sfx/`) and HUD icons (`assets/ui/`), and leave out its `src/components`, `src/systems` and `ui/locales` (`"exclude": ["src/components", "src/systems", "ui/locales"]`): the starter's French table translates its own screens' texts, not theirs. `template.json` is never inherited, so they don't get the starter's game UI either: each builds its own with `tools/build_ui.py`. A template of yours built on the starter whose screens say other things should exclude `ui/locales` too; otherwise its games get a table of texts they never show (`ui_strings` lists them as `unused`).

A text file can hold the line `{{base}}`. New Project replaces it with the base's file of the same path, without that file's title line. The genre templates' `AGENTS.md` uses this: a genre section, then the starter's text.

## Instructions for coding agents

Every template gives its projects one set of instructions for the coding agent, whichever it is: Claude Code and Codex read them as they are, and any other agent can be pointed at `AGENTS.md` and the skills ([MCP.md](MCP.md#other-agents)):

- `AGENTS.md` holds them: what the game is, the build-play-look loop, the conventions. Codex reads it.
- `CLAUDE.md` is the line `@AGENTS.md`, which makes Claude Code read `AGENTS.md`, and a short `## Claude Code` section for what only Claude Code needs (its `.claude/settings.json` pre-approvals, slash commands).
- `.claude/skills/` holds the skills for both: GameStudio copies them to `.agents/skills/` for Codex.

A project keeps the skills and `AGENTS.md` it was made with. When one of its scenes opens, GameStudio brings it up to date without touching your own work, and says what it added in the status bar:

- **Skills:** a skill of the project's template (with its base's) that the project lacks is added, such as `design-ui`, and a skill GameStudio put there that nobody edited follows GameStudio's new version. Skills you edited or wrote are never changed, and a skill you deleted is not added again. `.claude/skills/.gamestudio-skills.json` records which skills are GameStudio's (a hash of their files); keep it in version control with them. In a project made before that file existed, the skills identical to GameStudio's become GameStudio's and the others count as yours. Codex's copy in `.agents/skills/` follows. Only projects with a `.claude/skills/` folder get skills.
- **`AGENTS.md`:** one without a section on the game UI (no `Game UI` heading, no mention of `ui/ui.blend`) gets a short one at its end, marked `<!-- gamestudio: game-ui -->`, once. Nothing else in the file changes. Projects whose instructions are only in `CLAUDE.md` are left alone.

Template folders and GameStudio's own examples are kept by hand and never changed this way.

A template saved by an older GameStudio may have its instructions only in `CLAUDE.md`. New Project gives the new game that same shape: the instructions in `AGENTS.md`, and a `CLAUDE.md` with the import and the `## Claude Code` section, if there was one. The template itself is not changed.

## Placeholders

New Project fills these in, in every text file:

| Placeholder | Becomes | Used in |
|---|---|---|
| `{{name}}` | The game's name, as typed | `gamestudio.json` `name`, the `<title>` in `index.html`, the title line of `AGENTS.md` and `ASSISTANT.md`, `title: "{{name}}"` in code |
| `{{slug}}` | The name in lowercase with dashes, such as `my-game` | `package.json` `name` |
| `{{engine}}`, `{{devserver}}`, `{{mcp}}` | `file:` paths to this GameStudio's packages | `package.json` dependencies |

New Project drops `@gamestudio/*` dependencies that this GameStudio does not have.

## Making your own

Finish a game until it is a good starting point. Then save it as a template in one of three ways:

- **In Blender:** GameStudio menu, **Save as Template...** Give it an id, a label and a one-line description. If the scene has unsaved changes, the dialog saves them first, unless you untick that.
- **With your coding agent:** ask for it, or use the `make-template` skill (`/make-template` in Claude Code). The skill tidies the scene, playtests it, writes a section in `AGENTS.md` that says what the template contains and how to extend it, can add a skill for the genre, and then saves it.
- **The `save_template` tool:** `save_template {"id": "arena-shooter", "label": "Arena Shooter", "description": "..."}`. See [MCP.md](MCP.md).

Saving copies the project into your templates folder. The project itself is not changed.

It refuses, and says why, when:

- the id is not valid, or is a built-in template's id;
- a template with that id exists (pass `overwrite: true`, or tick **Replace the existing template** in the dialog, to replace it as a whole);
- the open scene of the project has unsaved changes (save first);
- the templates folder is inside the project.

The result lists the files where placeholders were put back, the top-level files and folders it left out, and warnings: no start scene, a library linked from outside the project, or a file that names the project's folder.

To change a template, edit the files in its folder, or save the game again with `overwrite`. The version it replaces moves to `.backups/<id>@<time>` in the templates folder (the result's `replaced` names it, and the five newest are kept), so you can get it back. To remove a template, delete its folder.

## What travels with a template

Save as Template copies these from the project:

- `src/` (components, systems and game shaders), `scenes/` (the `.blend` files and their twins), `ui/` (the menus and HUD, and their translations in `ui/locales/`), `assets/`, and `public/`, `audio/` and `tools/` if they exist;
- `.claude/`: the project's settings and **all its skills, your own included**;
- `.codex/` (Codex's `config.toml`, made portable) and `.agents/` (skills you wrote for Codex only; GameStudio's copies of `.claude/skills/` stay behind), if they exist;
- `AGENTS.md`, `CLAUDE.md`, `ASSISTANT.md`, `.mcp.json`, `gamestudio.json`, `package.json`, `index.html`, `tsconfig.json`, `vite.config.ts`, `.gitignore` and `.gitattributes`.

It leaves out `node_modules/`, `.gamestudio/` (exports, checkpoints, backups), `dist/`, `.git/`, Blender's backups (`*.blend1`), `.claude/settings.local.json` (your personal permissions) and `.claude/skills/.gamestudio-skills.json` (GameStudio's record of the skills it put in this project). Other top-level files, such as `README.md` or `package-lock.json`, are left out too, and the result names them.

Custom skills are the main point. A skill you wrote while making this game, such as "add an enemy type" or "tune the handling", is in every game made from the template, and your coding agent finds it there (Claude Code in `.claude/skills/`, Codex in GameStudio's copy in `.agents/skills/`).

Before saving, the project's own values become placeholders again, where the built-in templates have them:

- the name in `gamestudio.json`, the `<title>` of `index.html`, the title lines of `AGENTS.md`, `CLAUDE.md` and `ASSISTANT.md`, and `title: "<name>"` in code become `{{name}}`. Other text keeps the name: a string in code may name an object. `.blend` files are copied as they are, and New Project renames the game's name in `ui/ui.blend` (below);
- instructions that are only in `CLAUDE.md` (a project made before GameStudio used `AGENTS.md`) move to the template's `AGENTS.md`, and its `CLAUDE.md` becomes the import with any `## Claude Code` section. So do instructions GameStudio copied from `CLAUDE.md` to `AGENTS.md` for Codex, while nobody has edited that copy. When `AGENTS.md` and `CLAUDE.md` were written separately, both are kept as they are;
- the name in `package.json` becomes `{{slug}}`, and the `@gamestudio/engine`, `devserver` and `mcp` dependencies become `{{engine}}`, `{{devserver}}` and `{{mcp}}`;
- `gamestudio.json` loses its `template` entry, which New Project writes again;
- a `.mcp.json` that reaches the MCP server in a parent folder (a project inside the GameStudio repository) gets the usual project path;
- a `tsconfig.json` that extends a file outside the project gets that file folded in.

A game made from your template opens its own copy of your start scene and of your `ui/ui.blend`. A `tools/build_level.py` in the template is not run, and neither is a `tools/build_ui.py` while `ui/ui.blend` is there.

### The game's name in `ui/ui.blend`

Save as Template records the game's name in `template.json` as `sourceName`. Before New Project opens the start scene of a game made from the template, it opens the game's `ui/ui.blend` and gives these texts the new game's name:

- texts marked as the game's name: the title screen's `GameTitle` in the starter and the genre templates, and any text made with `ui_add` `project_name: true` (`gs.gameui.text(..., project_name=True)`, which sets the custom property `gs_ui_project_name`);
- other texts whose whole body is `sourceName`: `Fox Run` becomes the new name, `Thanks for playing Fox Run` stays as it is.

It saves the file, which exports the screens, only when a text changed. If the file can't be opened or saved, New Project goes on, the log says why, and the texts keep the old name until you change them: `ui_set {"canvas": "Title", "name": "GameTitle", "body": "New Name"}`.

## Permissions

`save_template` writes only to your templates folder, never into the project. It replaces a template only when called with `overwrite: true`, and then keeps the old one in `.backups/`. The starter's `.claude/settings.json` pre-approves it for Claude Code, like `checkpoint`. The `make-template` skill tells the agent to ask you before it replaces a template.

## For maintainers

- The code is in `blender/gamestudio/projects.py`: `template_info`, `templates`, `template_files`, `scaffold`, `finish_new_project`, `name_shipped_ui`, `user_templates_dir` and `save_as_template`. The bridge method is in `blender/gamestudio/bridge/templates.py`, the MCP tool in `runtime/packages/mcp/src/tools/templates.js`.
- Moving instructions from `CLAUDE.md` to `AGENTS.md` is `blender/gamestudio/core/agentfiles.py` (`migrate_instructions`), which New Project, Save as Template and connecting a coding agent to an older project use; it never moves them in a project without the user's consent.
- Bringing older projects up to date is `claude_code.refresh_agent_files`, run when a project's scene loads: `core/skills.py` for the skills and `agentfiles.add_section_pointers` for `AGENTS.md`. When the templates' `AGENTS.md` gains a section that older projects should know about, add a short version to `agentfiles.SECTION_POINTERS`. Skills that older GameStudio versions wrote are recognised by `core/skill_history.json`, the hashes of every past built-in version from git. `tools/build_extension.py` regenerates it with `python tools/skill_history.py` (`--check` reports a stale file). A copy that matches a past version follows the current one, even in a project whose record calls it the user's.
- Tests: `blender/tests/addon/test_templates.py` (built-in templates), `test_user_templates.py` (user templates, Save as Template, the bridge method), `test_agentfiles.py` and `test_skills.py`, and `runtime/packages/mcp/test/templates.test.js`. The test runner points `GAMESTUDIO_TEMPLATES` at a temporary folder, so tests never see or change your templates.
- The starter's sounds, music, HUD icons and UI art are generated with the standard library, with no recordings or downloaded art, and dedicated to the public domain (CC0, `assets/LICENSE.txt`). Add a new file to that list only when the project made it; anything else needs its own licence note. `--check` reports files that differ from what the script makes.
  - `python tools/make_sfx.py` writes `assets/sfx/*.wav`.
  - `python tools/make_music.py` writes `assets/music/theme.ogg`: a 30 s seamless loop (16 bars at 128 BPM, 48 kHz stereo, about −14 LUFS, about 340 KB), encoded to Ogg Vorbis by the FFmpeg in a background Blender 5.2+ with a scratch profile. Vorbis bytes differ between Blender versions, so its `--check` compares the length, the size (within 15 %) and the decoded sound with the music, not the bytes.
  - `python tools/make_ui_icons.py` writes `assets/ui/*.png`: the icons (heart, heart-empty, star, star-empty, coin, gem, key; 128×128) and 9-slice art for `UiSlice` panels, with these insets (`slice=[left, right, top, bottom]`): `panel` 128×128 `[32, 32, 32, 32]` (a dark translucent card with a light border, like the starter's flat cards), `panel-solid` 128×128 `[44, 44, 44, 44]` (the same card, opaque, with the cards' 40 px radius), `panel-framed` 128×128 `[32, 32, 32, 32]` (opaque navy in a bevelled gold frame with a gold hairline and corner studs; light text), `panel-light` 128×128 `[32, 32, 32, 32]` (opaque parchment on a darker lip in a brown outline; dark text), `button` 96×96 `[44, 44, 44, 44]` (a light button body; dark text), `slider-track` and `slider-fill` 64×32 `[14, 14, 14, 14]`, and `slider-knob` 48×48 (not sliced). The art is drawn at 1 image px = 1 design px of the 1920×1080 frame, and the game draws 9-slice corners at their image size times the canvas scale (2/3 at 1280×720), so corner details are sized to stay readable there. About 40 KB together.
- `tools/e2e/templates_e2e.mjs` plays the built-in templates in a browser, launched with `--lang=en-US` so the starter plays in English on any machine.
