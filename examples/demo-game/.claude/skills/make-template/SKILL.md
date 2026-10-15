---
name: make-template
description: Turn the current GameStudio game into a reusable project template that New Project offers next to the built-in ones. Tidies the scene, proves it plays, writes an AGENTS.md section saying what the template contains and how to extend it, optionally adds a skill for its genre, then saves it with save_template. Use when the user wants to reuse this game as a starting point, make a template or starter kit from it, or start other games from this one.
argument-hint: "[template id or name]"
---

# Make a template

A template is a copy of this project that New Project lists under "Your templates". A game made from it gets its own name and package paths, and copies of the scenes as saved, the code, the assets, `AGENTS.md` (with the `CLAUDE.md` that imports it) and `.claude/skills/`. Custom skills travel with it, so the next game starts with what this one learnt.

Template: $ARGUMENTS (if this is empty, propose an id, a label and a one-line description, and ask the user)

## 1. Decide what the template is

Write down, in three lines:

- **Starts with:** the level, the player, and the systems that work.
- **For:** the genre, the camera and the controls.
- **Change first:** what someone making a new game from it will replace.

A template is a clean starting point, not this game's finished content. Agree with the user what to strip: extra levels, placeholder art, debug objects, half-built mechanics.

## 2. Tidy the scene

1. `checkpoint` with the label "before template". Keep its id.
2. For each level in `scenes/`, read its twin, then:
   - delete debug and stray objects, test cameras and test lights (`delete_objects`);
   - give objects clear names and put them in the role collections that AGENTS.md lists;
   - `validate_assets {}` reports no errors.
3. Remove code in `src/` that nothing uses, and notes in `docs/` that only apply to this game. Keep every system the scene relies on.
4. Check that `startScene` in `gamestudio.json` names the level a new game should open.
5. Translations in `ui/locales/` travel with `ui/`: make each table complete for the template's texts (`ui_strings {"locale": "fr"}` reports `missing: 0`, then `ui_translate`), or delete a table it should not ship. A template written by hand on the starter (`"base": "starter"`) with texts of its own leaves the starter's out with `"exclude": ["ui/locales"]` in `template.json`, as the genre templates do.

## 3. Prove it plays

1. `npm run typecheck` passes.
2. `play`, then `playtest` the critical path from the start: the player moves, the core mechanic works, and the win or lose rule fires (`playtest-and-fix`). `game_logs {"level": "error"}` is empty.
3. `game_screenshot`: it looks like a game you would want to start from.
4. `save` every level you changed. `save_template` refuses a scene with unsaved changes.

## 4. Write the template's section in AGENTS.md

Put it right under the title. Keep the title line `# <game name>`: New Project writes the new game's name there. An older project may have its instructions only in `CLAUDE.md`: write the section there, and Save as Template moves them into `AGENTS.md` (with a `CLAUDE.md` that imports it) in the template.

```markdown
## This template: <label>

<What a new game starts with, and what the template is for. One paragraph.>

| Objects | Components | System (file) |
|---|---|---|
| `Player` | `CharacterController`, ... | `RespawnSystem` (`respawn.ts`) |

### Extending it

- To add <a typical thing>: <the skill, file or component to use>.
- Tuning: <the numbers that matter, such as jump metrics or wave sizes, and where they live>.
- Keep: <the conventions the template relies on>.
```

Describe what is there now. Remove sections about this game's story, or about levels the template no longer has.

## 5. Optional: a skill for the genre

If this game taught a workflow worth repeating (add an enemy type, add a level section, tune the handling), write it as `.claude/skills/<name>/SKILL.md`. Every game made from the template gets it. Match the other skills in `.claude/skills/`: frontmatter with `name`, a `description` that says when to use it, and an `argument-hint`; numbered steps that name the tools; a verify step; a "Common failures" table. Name the skill in the AGENTS.md section.

## 6. Save it

`save_template {"id": "arena-shooter", "label": "Arena Shooter", "description": "Survive waves of enemies in a walled pit"}`

- **id:** lowercase letters, digits, `-` and `_`. It can't be a built-in template's id (`starter`, `platformer`, `shooter`, `driving`, `runner`).
- **label:** up to 40 characters. **description:** one line, up to 100.
- If a template with that id exists, it refuses. Ask the user before you pass `"overwrite": true`, which replaces the old one completely.

Read the result:

- `placeholders`: the files where the game's name and package paths became placeholders, which New Project fills in for each new game. In code, only `title: "<name>"` is replaced; other strings may name objects.
- `left_out`: top-level files and folders that were not copied. Only `src/`, `scenes/`, `ui/`, `assets/`, `public/`, `audio/`, `tools/`, `.claude/`, `.codex/`, `.agents/` (your own Codex skills: GameStudio's copies of the skills stay behind) and the project files travel. Move what a new game needs into one of them, then save again with `"overwrite": true`.
- `warnings`: a missing start scene, a library linked from outside the project, or a file that names this project's folder. Fix each one and save again.

## 7. Report

Tell the user the label and the `path`, what a new game starts with, and which custom skills it carries. To use it: GameStudio menu, **New Project...**, then pick it under "Your templates". A new game opens its own copy of the start scene and of `ui/ui.blend` as you saved them; a `tools/build_level.py` or `tools/build_ui.py` in the template is not run. A new game's title shows its own name: New Project gives it to the texts of `ui/ui.blend` marked as the game's name (the `Title` screen's `GameTitle`, or any text added with `project_name: true`) and to texts that are exactly this game's name (`sourceName` in the template's `template.json`).

## Common failures

| Symptom | Fix |
|---|---|
| "has unsaved changes" | `save`, then `save_template` again. |
| "already exists" | Pick another id, or ask the user and pass `"overwrite": true`. |
| A new game opens the wrong level | `startScene` in `gamestudio.json` names it. Fix it and save the template again. |
| A new game shows this game's name somewhere | It was plain text outside the title lines, or a text of `ui/ui.blend` with more than the name in it (only texts that are exactly the name, or marked as it, are renamed). Make the text generic here (`ui_set`) and save again, or tell the user to change it in each new game. |
| A new game misses assets | They lived outside `assets/`, `public/` or `audio/`, or a library was linked from another project. Move them into the project. |
| The template is not in New Project | Check `path` in the result. `GAMESTUDIO_TEMPLATES` changes the folder for saving and for New Project. |
