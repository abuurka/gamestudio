# SPDX-License-Identifier: GPL-3.0-or-later
"""User templates (projects.py): the user templates folder beside the built-in templates, Save as
Template (save_as_template, its operator and the save_template bridge method), and New Project from
a template that ships its scenes and its game UI, which gets the new game's name.

Every test points GAMESTUDIO_TEMPLATES at a temporary folder: the real Documents/GameStudio/Templates
is never touched.
"""

import json
import os
import pathlib
import shutil
import tempfile
import unittest
from unittest import mock

import bpy

from gamestudio import gameui, projects
from gamestudio.bridge import api
from gamestudio.core import agentfiles, components, gameui as ui_core, gameui_export, project, registry

import helpers
from helpers import reset_scene, tmp_base
from test_bridge import BridgeTestCase
from test_gameui_build import ui_registry

# What a project made by New Project has that Save as Template must copy.
KEPT = {"template.json", "scenes/main.blend", "scenes/main.scene.json", "ui/ui.blend", "AGENTS.md", "CLAUDE.md",
        "ASSISTANT.md", ".mcp.json", "gamestudio.json",
        "package.json", "index.html", "tsconfig.json", "vite.config.ts", ".gitignore", ".gitattributes",
        "src/main.ts", "src/systems/menus.ts", "src/lib/names.ts", "assets/sfx/pickup.wav", "public/robots.txt",
        ".claude/settings.json", ".claude/skills/fox-tricks/SKILL.md", ".claude/skills/make-template/SKILL.md"}


def _files(folder: pathlib.Path) -> set[str]:
    return {p.relative_to(folder).as_posix() for p in folder.rglob("*") if p.is_file()}


def _snapshot(folder: pathlib.Path) -> dict:
    return {rel: (folder / rel).read_bytes() for rel in _files(folder) if not rel.startswith("node_modules/")}


def _write(path: pathlib.Path, text: str = "x") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


class UserTemplatesCase(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix="gs_user_templates_", dir=tmp_base()))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.folder = self.tmp / "Templates"
        patch = mock.patch.dict(os.environ, {projects.USER_TEMPLATES_ENV: str(self.folder)})
        patch.start()
        self.addCleanup(patch.stop)


class DiscoveryTests(UserTemplatesCase):
    def write(self, rel, text):
        _write(self.folder / rel, text)

    def test_the_folder(self):
        self.assertEqual(projects.user_templates_dir(), self.folder)
        with mock.patch.dict(os.environ, {projects.USER_TEMPLATES_ENV: ""}):
            self.assertEqual(projects.user_templates_dir(),
                             pathlib.Path.home() / "Documents" / "GameStudio" / "Templates")
        self.assertEqual(projects._icon(projects.USER_TEMPLATE_ICON), projects.USER_TEMPLATE_ICON)

    def test_user_templates_come_after_the_built_in_ones_which_win_an_id(self):
        builtin = [t["id"] for t in projects.templates()]
        self.write("arena/template.json", json.dumps({"label": "Arena", "description": "A pit", "order": -5}))
        self.write("arena/gamestudio.json", "{}")
        self.write("older/gamestudio.json", json.dumps({"name": "{{name}}"}))  # no template.json
        self.write("starter/template.json", json.dumps({"label": "My Starter", "description": "Mine", "order": -1}))
        self.write("Bad Name/template.json", "{}")
        self.write(".arena.saving/template.json", "{}")
        self.write("broken/template.json", "[1, 2]")
        self.write("notes/readme.txt", "not a template")
        found = projects.templates()
        self.assertEqual([t["id"] for t in found], [*builtin, "arena", "older"])
        self.assertEqual([t["user"] for t in found], [False] * len(builtin) + [True, True])
        starter = projects.template_info("starter")
        self.assertEqual((starter["label"], starter["user"], starter["path"]),
                         ("Starter", False, projects.TEMPLATES / "starter"))
        arena = projects.template_info("arena")
        self.assertEqual((arena["label"], arena["user"], arena["path"], arena["icon"]),
                         ("Arena", True, self.folder / "arena", projects.USER_TEMPLATE_ICON))
        for bad in ("Bad Name", ".arena.saving", "broken", "notes", "missing"):
            self.assertIsNone(projects.template_info(bad), bad)
        self.assertEqual([i[0] for i in projects._templates_enum(None, None)], [t["id"] for t in found])

    def test_picker_heads_the_users_templates(self):
        self.write("arena/template.json", json.dumps({"label": "Arena", "description": "A pit"}))
        rows = []

        class Layout:
            active = True

            def column(self, **_kw):
                return self

            def split(self, **_kw):
                return self

            def row(self, **_kw):
                return self

            def label(self, text="", **_kw):
                rows.append(text)

            def prop_enum(self, data, prop, value, text="", icon="NONE"):
                rows.append(value)

        projects.draw_template_picker(Layout(), mock.Mock(template="arena"))
        self.assertLess(rows.index("runner"), rows.index("Your templates"))
        self.assertEqual(rows.index("Your templates") + 1, rows.index("arena"))

    def test_a_user_template_can_build_on_a_built_in_one(self):
        self.write("tiny/template.json", json.dumps({"label": "Tiny", "base": "starter", "exclude": ["src/systems"]}))
        self.write("tiny/AGENTS.md", "# {{name}}\n\nTiny intro.\n\n{{base}}\n")
        root = self.tmp / "tiny-game"
        root.mkdir()
        projects.scaffold(root, "Tiny Game", "tiny")
        self.assertFalse((root / "src" / "systems").exists())
        self.assertTrue((root / ".claude" / "skills" / "make-template" / "SKILL.md").is_file())
        self.assertTrue((root / "AGENTS.md").read_text("utf-8").startswith("# Tiny Game\n\nTiny intro."))
        self.assertEqual((root / "CLAUDE.md").read_bytes(), (projects.TEMPLATES / "starter" / "CLAUDE.md").read_bytes())
        self.assertEqual(projects.project_template(root), "tiny", "recorded, so Open in Claude Code finds it")
        from gamestudio import claude_code

        self.assertEqual(claude_code.agent_template(root), "tiny")

    def test_an_older_template_with_only_claude_md(self):
        """Saved by an older GameStudio: new projects get its instructions in AGENTS.md, and a CLAUDE.md
        that imports them, like the built-in templates."""
        old = "# {{name}}\n\nThe old rules.\n\n## Claude Code\n\nUse the slash commands.\n"
        self.write("older/gamestudio.json", json.dumps({"name": "{{name}}", "startScene": "main"}))
        self.write("older/package.json", json.dumps({"name": "{{slug}}"}))
        self.write("older/CLAUDE.md", old)
        root = self.tmp / "older-game"
        root.mkdir()
        projects.scaffold(root, "Older Game", "older")
        self.assertEqual((root / "AGENTS.md").read_text("utf-8"), "# Older Game\n\nThe old rules.\n")
        self.assertEqual((root / "CLAUDE.md").read_text("utf-8"),
                         "@AGENTS.md\n\n## Claude Code\n\nUse the slash commands.\n")
        self.assertEqual((self.folder / "older" / "CLAUDE.md").read_text("utf-8"), old, "the template is not changed")


class SaveAsTemplateTests(UserTemplatesCase):
    """A project made from the starter, with a scene, a custom skill and everything that must stay behind."""

    def setUp(self):
        super().setUp()
        reset_scene()
        self.root = self.tmp / "fox-run"
        self.root.mkdir()
        projects.scaffold(self.root, "Fox Run", "starter")
        helpers.write_registry(self.root, helpers.fixture_registry())
        _write(self.root / ".claude" / "skills" / "fox-tricks" / "SKILL.md", "---\nname: fox-tricks\n---\n# Tricks\n")
        _write(self.root / ".claude" / "settings.local.json", "{}")
        _write(self.root / "src" / "lib" / "names.ts",
               "export const hero = \"Fox Run\";\nexport const title = 'Fox Run';\n")
        _write(self.root / "src" / "systems" / "menus.ts", "ctx.ui.screen('over', { title: \"Fox Run\" });\n")
        _write(self.root / "ui" / "ui.blend", "the game's menus and HUD")  # New Project built it (the starter's)
        _write(self.root / "public" / "robots.txt")
        _write(self.root / "README.md", "# Fox Run\n")
        _write(self.root / "package-lock.json", "{}")
        for junk in ("node_modules/junk/index.js", "dist/index.html", ".gamestudio/export/main.glb", ".git/HEAD",
                     "src/__pycache__/x.pyc"):
            _write(self.root / junk)
        self.blend = projects.start_scene(self.root)
        bpy.context.scene.name = "main"
        bpy.ops.wm.save_as_mainfile(filepath=str(self.blend))
        project.invalidate_cache()
        registry.reload_registry(self.root)
        bpy.ops.mesh.primitive_cube_add(size=1.0)
        crate = bpy.context.active_object
        crate.name = "Crate"
        components.add_component(crate, "Spin", {"speed": 2.0})
        bpy.ops.wm.save_mainfile()
        shutil.copy2(self.blend, self.blend.with_suffix(".blend1"))
        api.agent_dirty = False

    def tearDown(self):
        api.agent_dirty = False
        reset_scene()
        project.invalidate_cache()
        registry.reload_registry(None)

    def test_files_for_a_template(self):
        files, left_out = projects.project_files_for_template(self.root)
        self.assertTrue(KEPT - {"template.json"} <= set(files), KEPT - set(files))
        for rel in files:
            self.assertFalse(rel.split("/")[0] in {"node_modules", "dist", ".gamestudio", ".git"}, rel)
            self.assertNotIn("__pycache__", rel)
        self.assertNotIn("scenes/main.blend1", files)
        self.assertNotIn(".claude/settings.local.json", files)
        self.assertEqual(sorted(left_out), ["README.md", "package-lock.json"])

    def test_round_trip(self):
        before = _snapshot(self.root)
        result = projects.save_as_template(self.root, "fox-run", "Fox Run", "A fox collects berries")
        target = self.folder / "fox-run"
        self.assertEqual(pathlib.Path(result["path"]), target)
        self.assertEqual(_snapshot(self.root), before, "the project is not changed")
        self.assertEqual(result["warnings"], [])
        self.assertEqual(sorted(result["left_out"]), ["README.md", "package-lock.json"])
        saved = _files(target)
        self.assertTrue(KEPT <= saved, KEPT - saved)
        self.assertEqual(result["files"], len(saved) - 1)
        for rel in saved:
            self.assertFalse(rel.split("/")[0] in {"node_modules", "dist", ".gamestudio", ".git"}, rel)
        self.assertFalse({"scenes/main.blend1", ".claude/settings.local.json", "README.md"} & saved)
        self.assertEqual(sorted(p.name for p in self.folder.iterdir()), ["fox-run"], "no staging folder left")

        # The project's own values are placeholders again, where the built-in templates have them.
        meta = json.loads((target / "template.json").read_text("utf-8"))
        self.assertEqual((meta["label"], meta["description"], meta["icon"]),
                         ("Fox Run", "A fox collects berries", projects.USER_TEMPLATE_ICON))
        self.assertEqual(meta["from"], {"project": "Fox Run", "template": "starter"})
        self.assertEqual(meta["sourceName"], "Fox Run", "New Project renames the game UI's texts that say it")
        package = json.loads((target / "package.json").read_text("utf-8"))
        self.assertEqual(package["name"], "{{slug}}")
        self.assertEqual(package["dependencies"]["@gamestudio/engine"], "{{engine}}")
        self.assertEqual(package["devDependencies"]["@gamestudio/devserver"], "{{devserver}}")
        self.assertEqual(package["dependencies"]["three"], "0.186.1")
        config = json.loads((target / "gamestudio.json").read_text("utf-8"))
        self.assertEqual((config["name"], config["startScene"]), ("{{name}}", "main"))
        self.assertNotIn("template", config)
        self.assertNotIn("engine", config, "New Project records the engine of the new game")
        self.assertIn("<title>{{name}}</title>", (target / "index.html").read_text("utf-8"))
        self.assertTrue((target / "AGENTS.md").read_text("utf-8").startswith("# {{name}}\n"))
        self.assertEqual((target / "CLAUDE.md").read_bytes(), (self.root / "CLAUDE.md").read_bytes(), "the import")
        self.assertTrue((target / "ASSISTANT.md").read_text("utf-8").startswith("# {{name}}: conventions"))
        self.assertIn('title: "{{name}}"', (target / "src" / "systems" / "menus.ts").read_text("utf-8"))
        names = (target / "src" / "lib" / "names.ts").read_text("utf-8")
        self.assertIn('hero = "Fox Run"', names, "only titles: other strings may name objects")
        self.assertIn("title = '{{name}}'", names)
        self.assertTrue({"package.json", "gamestudio.json", "index.html", "AGENTS.md", "ASSISTANT.md",
                         "src/systems/menus.ts", "src/lib/names.ts"} <= set(result["placeholders"]))
        self.assertEqual((target / "scenes" / "main.blend").read_bytes(), self.blend.read_bytes())
        self.assertEqual((target / "ui" / "ui.blend").read_bytes(), b"the game's menus and HUD", "the game UI travels")

        # New Project from it: its own name and package paths, the custom skill and the scene.
        self.assertIn("fox-run", [t["id"] for t in projects.templates()])
        new = self.tmp / "owl-hunt"
        new.mkdir()
        projects.scaffold(new, "Owl Hunt", "fox-run")
        self.assertFalse((new / "template.json").exists())
        package = json.loads((new / "package.json").read_text("utf-8"))
        self.assertEqual(package["name"], "owl-hunt")
        self.assertEqual(package["dependencies"]["@gamestudio/engine"],
                         "file:" + (projects.runtime_packages() / "engine").as_posix())
        config = json.loads((new / "gamestudio.json").read_text("utf-8"))
        self.assertEqual(list(config)[:2], ["name", "template"])
        self.assertEqual((config["name"], config["template"]), ("Owl Hunt", "fox-run"))
        self.assertEqual(config["engine"], projects.engine_version())
        self.assertEqual(projects.project_template(new), "fox-run")
        self.assertIn("<title>Owl Hunt</title>", (new / "index.html").read_text("utf-8"))
        self.assertTrue((new / "AGENTS.md").read_text("utf-8").startswith("# Owl Hunt\n"))
        self.assertEqual(agentfiles.instructions_state(new), agentfiles.SHARED)
        self.assertIn('title: "Owl Hunt"', (new / "src" / "systems" / "menus.ts").read_text("utf-8"))
        self.assertTrue((new / ".claude" / "skills" / "fox-tricks" / "SKILL.md").is_file())
        for path in new.rglob("*"):
            if path.is_file() and path.suffix in projects.TEXT_SUFFIXES:
                self.assertNotIn("{{", path.read_text("utf-8"), f"placeholder left in {path.relative_to(new)}")

        # Its start scene opens as saved (no level script, no starter scene) and is exported for Play.
        helpers.write_registry(new, helpers.fixture_registry())  # npm run registry, in New Project
        with self.assertLogs(projects.log, "ERROR"):  # its ui/ui.blend is no .blend: it can't get the new name
            path = projects.finish_new_project(new)
        self.assertEqual(path, new / "scenes" / "main.blend")
        self.assertEqual(pathlib.Path(bpy.data.filepath), path)
        self.assertEqual(project.project_root(), new.resolve())
        self.assertIn("Crate", bpy.data.objects)
        self.assertNotIn("Player", bpy.data.objects, "not the starter scene")
        manifest = json.loads((new / ".gamestudio" / "export" / "main.scene.json").read_text("utf-8"))
        crate = next(e for e in manifest["entities"] if e["name"] == "Crate")
        self.assertEqual(crate["components"]["Spin"]["speed"], 2.0)
        self.assertFalse((new / "scenes" / "main.blend1").exists(), "no save version of the template's copy")
        self.assertEqual((new / "ui" / "ui.blend").read_bytes(), b"the game's menus and HUD", "its own game UI")
        self.assertEqual(_snapshot(self.root), before, "nor by projects made from the template")

    def test_an_older_project_with_only_claude_md(self):
        """The template gets AGENTS.md with the instructions, and a CLAUDE.md that imports it; the project
        keeps its CLAUDE.md."""
        old = "# Fox Run\n\nFox rules.\n\n## Claude Code\n\nOnly for Claude.\n"
        (self.root / "AGENTS.md").unlink()
        _write(self.root / "CLAUDE.md", old)
        result = projects.save_as_template(self.root, "old-fox")
        target = self.folder / "old-fox"
        self.assertEqual((target / "AGENTS.md").read_text("utf-8"), "# {{name}}\n\nFox rules.\n")
        self.assertEqual((target / "CLAUDE.md").read_text("utf-8"),
                         "@AGENTS.md\n\n## Claude Code\n\nOnly for Claude.\n")
        self.assertIn("AGENTS.md", result["placeholders"])
        self.assertEqual(result["warnings"], [])
        self.assertEqual((self.root / "CLAUDE.md").read_text("utf-8"), old, "the project is not changed")
        self.assertFalse((self.root / "AGENTS.md").exists())

        # Declined in the project (GameStudio's copy for Codex): the template still gets CLAUDE.md's text.
        agentfiles.migrate_instructions(self.root, consent=False)
        _write(self.root / "CLAUDE.md", old.replace("Fox rules.\n", "Fox rules.\n\nNewer rule.\n"))
        projects.save_as_template(self.root, "copied-fox")
        target = self.folder / "copied-fox"
        self.assertEqual((target / "AGENTS.md").read_text("utf-8"), "# {{name}}\n\nFox rules.\n\nNewer rule.\n")
        self.assertEqual(agentfiles.instructions_state(target), agentfiles.SHARED)

        # CLAUDE.md imports an AGENTS.md the project lost: a warning.
        (self.root / "AGENTS.md").unlink()
        _write(self.root / "CLAUDE.md", "@AGENTS.md\n")
        warnings = projects.save_as_template(self.root, "lost-fox")["warnings"]
        self.assertTrue(any("AGENTS.md" in w for w in warnings), warnings)

    def test_refusals(self):
        for bad in ("", "Fox Run", "../x", "-x", "UPPER", "x" * 65, "a/b"):
            with self.subTest(template_id=bad), self.assertRaisesRegex(ValueError, "not a valid template id"):
                projects.save_as_template(self.root, bad)
        with self.assertRaisesRegex(projects.TemplateError, "built-in") as caught:
            projects.save_as_template(self.root, "starter")
        self.assertIn("another id", caught.exception.hint)
        with self.assertRaisesRegex(ValueError, "too long"):
            projects.save_as_template(self.root, "fox", label="x" * 41)
        with self.assertRaisesRegex(ValueError, "not a GameStudio project"):
            projects.save_as_template(self.tmp, "fox")
        with self.assertRaisesRegex(ValueError, "inside the project"):
            projects.save_as_template(self.root, "fox", folder=self.root / "templates")
        with mock.patch.dict(os.environ, {projects.USER_TEMPLATES_ENV: str(self.tmp)}):
            with self.assertRaisesRegex(ValueError, "inside the project or around it"):
                projects.save_as_template(self.root, "fox-run")  # the project's own folder

        api.agent_dirty = True  # an agent edit not saved yet
        with self.assertRaisesRegex(projects.TemplateError, "unsaved changes") as caught:
            projects.save_as_template(self.root, "fox")
        self.assertIn("Save", caught.exception.hint)
        api.agent_dirty = False
        self.assertFalse(self.folder.exists(), "nothing written by a refusal")

        projects.save_as_template(self.root, "fox")
        _write(self.folder / "fox" / "stale.txt")
        with self.assertRaisesRegex(projects.TemplateError, "already exists") as caught:
            projects.save_as_template(self.root, "fox")
        self.assertIn("overwrite", caught.exception.hint)
        self.assertTrue((self.folder / "fox" / "stale.txt").is_file(), "left as it was")
        result = projects.save_as_template(self.root, "fox", "Fox 2", overwrite=True)
        self.assertEqual(result["label"], "Fox 2")
        self.assertFalse((self.folder / "fox" / "stale.txt").exists(), "replaced as a whole")
        # The replaced template is kept, out of the picker's sight, so an agent's overwrite can be taken back.
        backup = pathlib.Path(result["replaced"])
        self.assertEqual(backup.parent, self.folder / projects.TEMPLATE_BACKUPS)
        self.assertTrue((backup / "stale.txt").is_file())
        self.assertEqual(sorted(p.name for p in self.folder.iterdir()), [projects.TEMPLATE_BACKUPS, "fox"])
        self.assertNotIn(projects.TEMPLATE_BACKUPS, {t["id"] for t in projects.templates()})
        self.assertIsNone(projects.save_as_template(self.root, "kit", "Kit")["replaced"])
        for _ in range(projects.KEEP_TEMPLATE_BACKUPS + 2):
            projects.save_as_template(self.root, "fox", overwrite=True)
        kept = [d for d in (self.folder / projects.TEMPLATE_BACKUPS).iterdir() if d.name.startswith("fox@")]
        self.assertEqual(len(kept), projects.KEEP_TEMPLATE_BACKUPS)

    def test_warnings(self):
        self.blend.unlink()
        _write(self.root / "tools" / "paths.json", json.dumps({"root": str(self.root)}))
        warnings = projects.save_as_template(self.root, "no-scene")["warnings"]
        self.assertEqual(len(warnings), 2, warnings)
        self.assertIn("scenes/main.blend", warnings[0])
        self.assertIn("tools/paths.json", warnings[1])

    def test_operator_and_menu(self):
        done = bpy.ops.gamestudio.project_save_template(template_id="op-made", label="Op", summary="From the menu")
        self.assertEqual(done, {"FINISHED"})
        meta = json.loads((self.folder / "op-made" / "template.json").read_text("utf-8"))
        self.assertEqual((meta["label"], meta["description"]), ("Op", "From the menu"))
        api.agent_dirty = True
        with self.assertRaises(RuntimeError):  # an ERROR report raises in the background
            bpy.ops.gamestudio.project_save_template(template_id="op-dirty", save_changes=False)
        self.assertFalse((self.folder / "op-dirty").exists())
        self.assertEqual(bpy.ops.gamestudio.project_save_template(template_id="op-saved"), {"FINISHED"})
        self.assertFalse(api.agent_dirty, "saved first")
        self.assertTrue((self.folder / "op-saved" / "scenes" / "main.blend").is_file())

        called = []

        class Layout:
            operator_context = ""

            def operator(self, idname, **_kw):
                called.append(idname)

            def menu(self, idname, **_kw):
                called.append(idname)

            def separator(self):
                pass

            def label(self, **_kw):
                pass

        projects.draw_project_actions(Layout(), bpy.context)
        self.assertEqual(called.index("gamestudio.project_save_template"),
                         called.index("gamestudio.project_reveal") + 1)


class ProjectNameTests(UserTemplatesCase):
    """A template saved with its game UI (docs/GAME-UI.md §11.7): New Project gives the new game's ui/ui.blend
    its name in the texts marked as the game's name (the starter's GameTitle) and in unmarked texts that say
    exactly the saved game's name (template.json sourceName), then saves it, which exports the screens."""

    def setUp(self):
        super().setUp()
        reset_scene()
        self.root = self.tmp / "fox-run"
        self.root.mkdir()
        projects.scaffold(self.root, "Fox Run", "starter")
        helpers.write_registry(self.root, ui_registry())  # npm run registry, in New Project
        projects.build_starter_ui(self.root)  # left open
        gameui.text("Win", "WinName", "Fox Run", (960, 280))  # unmarked: the game's name exactly
        gameui.text("Win", "WinThanks", "Thanks for playing Fox Run", (960, 860))  # unmarked: more than the name
        bpy.ops.wm.save_mainfile()
        reset_scene()
        bpy.context.scene.name = "main"
        bpy.ops.wm.save_as_mainfile(filepath=str(projects.start_scene(self.root)))
        api.agent_dirty = False

    def tearDown(self):
        api.agent_dirty = False
        reset_scene()
        project.invalidate_cache()
        registry.reload_registry(None)

    def new_game(self, template: str, name: str) -> pathlib.Path:
        """New Project from the template, as it goes on once the setup is done."""
        new = self.tmp / "games" / projects.slugify(name)
        new.mkdir(parents=True)
        projects.scaffold(new, name, template)
        helpers.write_registry(new, ui_registry())  # npm run registry, in New Project
        level = new / "scenes" / "main.blend"
        self.assertEqual(projects.finish_new_project(new), level)
        self.assertEqual(pathlib.Path(bpy.data.filepath), level, "the level opens after the UI file")
        self.assertFalse((new / "ui" / "ui.blend1").exists(), "no save version of the template's copy")
        return new

    def texts(self, root: pathlib.Path) -> tuple[dict, dict]:
        """The UI file's texts as exported for the game, and as saved: {element name: body}."""
        exported = {}
        for entry in gameui_export.read_index(root)["canvases"].values():
            data = json.loads((ui_core.export_dir(root) / entry["file"]).read_text("utf-8"))
            exported.update({e["name"]: e["text"]["body"] for e in data["elements"] if e["type"] == "text"})
        bpy.ops.wm.open_mainfile(filepath=str(ui_core.ui_file(root)))
        saved = {o.name: o.data.body for o in bpy.data.objects if o.type == "FONT"}
        return exported, saved

    def test_new_project_gives_the_ui_its_name(self):
        result = projects.save_as_template(self.root, "fox-run")
        template = pathlib.Path(result["path"])
        self.assertEqual(json.loads((template / "template.json").read_text("utf-8"))["sourceName"], "Fox Run")
        self.assertEqual(projects.template_info("fox-run")["source_name"], "Fox Run")
        ui_file = (template / "ui" / "ui.blend").read_bytes()
        self.assertEqual(ui_file, (self.root / "ui" / "ui.blend").read_bytes(), "copied as it is")

        # Marked, and the old name exactly: the new name. A text that holds more than the name keeps it.
        new = self.new_game("fox-run", "Owl Hunt")
        expected = {"GameTitle": "Owl Hunt", "WinName": "Owl Hunt", "WinThanks": "Thanks for playing Fox Run",
                    "Subtitle": "Collect every gem", "WinTitle": "You win!"}
        for texts in self.texts(new):
            self.assertEqual({k: texts[k] for k in expected}, expected)
        self.assertEqual((template / "ui" / "ui.blend").read_bytes(), ui_file, "the template is not changed")

        # A template without sourceName (written by hand): only the marked texts.
        meta = json.loads((template / "template.json").read_text("utf-8"))
        del meta["sourceName"], meta["from"]
        (template / "template.json").write_text(json.dumps(meta), encoding="utf-8")
        new = self.new_game("fox-run", "Bat Cave")
        for texts in self.texts(new):
            self.assertEqual((texts["GameTitle"], texts["WinName"]), ("Bat Cave", "Fox Run"))

    def test_a_template_saved_before_source_name(self):
        """Saved by an older GameStudio: "from" names the game."""
        projects.save_as_template(self.root, "older-fox")
        path = self.folder / "older-fox" / "template.json"
        meta = json.loads(path.read_text("utf-8"))
        del meta["sourceName"]
        path.write_text(json.dumps(meta), encoding="utf-8")
        self.assertEqual(projects.template_info("older-fox")["source_name"], "Fox Run")
        new = self.new_game("older-fox", "Owl Hunt")
        for texts in self.texts(new):
            self.assertEqual((texts["GameTitle"], texts["WinName"]), ("Owl Hunt", "Owl Hunt"))

    def test_nothing_to_rename(self):
        """Nothing to rename leaves the UI file as the template has it, and the screens are still exported."""
        projects.save_as_template(self.root, "fox-run")
        ui_file = (self.folder / "fox-run" / "ui" / "ui.blend").read_bytes()
        same = self.new_game("fox-run", "Fox Run")  # the same name
        self.assertEqual((same / "ui" / "ui.blend").read_bytes(), ui_file, "not saved")
        for texts in self.texts(same):
            self.assertEqual((texts["GameTitle"], texts["WinName"]), ("Fox Run", "Fox Run"))

        # No marked text, and none says the old name exactly.
        bpy.ops.wm.open_mainfile(filepath=str(ui_core.ui_file(self.root)))
        del bpy.data.objects["GameTitle"][projects.UI_PROJECT_NAME]
        bpy.data.objects["GameTitle"].data.body = "FOX RUN"
        bpy.data.objects["WinName"].data.body = "Fox Run!"
        bpy.ops.wm.save_mainfile()
        api.agent_dirty = False
        projects.save_as_template(self.root, "plain-fox")
        ui_file = (self.folder / "plain-fox" / "ui" / "ui.blend").read_bytes()
        new = self.new_game("plain-fox", "Owl Hunt")
        self.assertEqual((new / "ui" / "ui.blend").read_bytes(), ui_file, "not saved")
        for texts in self.texts(new):
            self.assertEqual((texts["GameTitle"], texts["WinName"]), ("FOX RUN", "Fox Run!"))


class SaveTemplateBridgeTest(BridgeTestCase):
    def setUp(self):
        super().setUp()
        self.folder = pathlib.Path(tempfile.mkdtemp(prefix="gs_bridge_templates_", dir=tmp_base())) / "Templates"
        self.addCleanup(shutil.rmtree, self.folder.parent, True)
        patch = mock.patch.dict(os.environ, {projects.USER_TEMPLATES_ENV: str(self.folder)})
        patch.start()
        self.addCleanup(patch.stop)

    def test_save_template(self):
        self.assertIn("save_template", self.client.result("methods"))
        self.save_scene()
        self.cube("Crate")
        bpy.ops.wm.save_mainfile()
        made = self.client.result("save_template", {"id": "bridge-made", "label": "Bridge Made"})
        self.assertEqual(pathlib.Path(made["path"]), self.folder / "bridge-made")
        self.assertTrue((self.folder / "bridge-made" / "scenes" / "main.blend").is_file())
        self.assertEqual(json.loads((self.folder / "bridge-made" / "template.json").read_text("utf-8"))["label"],
                         "Bridge Made")
        self.assertIn("New Project", made["hint"])

        err = self.client.error("save_template", {"id": "bridge-made"})
        self.assertIn("already exists", err["message"])
        self.assertIn("overwrite", err["data"]["hint"])
        self.assertEqual(self.client.result("save_template", {"id": "bridge-made", "overwrite": True})["id"],
                         "bridge-made")
        err = self.client.error("save_template", {"id": "Not An Id"})
        self.assertIn("not a valid template id", err["message"])

        self.client.result("run_python", {"code": "bpy.ops.mesh.primitive_cube_add()"})
        err = self.client.error("save_template", {"id": "bridge-dirty"})
        self.assertIn("unsaved changes", err["message"])
        self.assertIn("Save", err["data"]["hint"])
        self.assertFalse((self.folder / "bridge-dirty").exists())


if __name__ == "__main__":
    unittest.main()
