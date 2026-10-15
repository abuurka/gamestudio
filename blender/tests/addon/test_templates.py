# SPDX-License-Identifier: GPL-3.0-or-later
"""Project templates (projects.py): discovery, composition from a base template, scaffolding every
template, the New Project picker, and each genre template's level script building a playable scene.

The level builds need Node.js (`npm run registry` reads the template's components); without it
those tests are skipped. tools/e2e/templates_e2e.mjs plays the games in a browser.
"""

import importlib.util
import json
import os
import pathlib
import shutil
import struct
import subprocess
import tempfile
import unittest
import wave
from unittest import mock

import bpy

from gamestudio import gameui, kits, projects
from gamestudio.core import agentfiles, export, gameui as ui_core, gameui_export, ids, project, registry, shaders, toolchain
from gamestudio.gameui import locales

import helpers
from helpers import reset_scene, tmp_base
from test_gameui_build import ui_registry

STARTER = projects.TEMPLATES / "starter"
MAKE_SFX = pathlib.Path(__file__).resolve().parents[3] / "tools" / "make_sfx.py"
SOUNDS = {"pickup", "jump", "damage", "win", "lose", "click", "whoosh"}
GENRES ={"platformer": "add-level-section", "shooter": "add-enemy-type", "driving": "tune-handling",
         "runner": "add-chunk"}
# What each genre's exported start scene must hold: component -> minimum number of entities.
LEVELS = {
    "platformer": {"CharacterController": 1, "Coin": 10, "Checkpoint": 2, "MovingPlatform": 1, "Goal": 1,
                   "KillZone": 1, "BouncePad": 1, "FollowCamera": 1},
    "shooter": {"CharacterController": 1, "Shooter": 1, "Health": 1, "WaveSpawner": 1, "SpawnPoint": 4,
                "AimMarker": 1, "FollowCamera": 1, "NavMesh": 1},
    "driving": {"Car": 1, "Wheel": 4, "Checkpoint": 4, "Race": 1, "Surface": 2, "ChaseCamera": 1,
                "CheckpointMarker": 1},
    "runner": {"Track": 1, "Backdrop": 6, "Collider": 1, "FollowCamera": 1, "PostEffects": 1,
               "ParticleEmitter": 1, "AudioSource": 1},
}
# The runner's track pieces: prefabs (in its Chunks scene) that the Track system streams in.
CHUNKS = {"ChunkStraight", "ChunkGap", "ChunkSlalom", "ChunkRamp", "ChunkNarrow", "ChunkSweeper"}
# The starter's game UI (projects.build_starter_ui): each canvas's settings and elements.
STARTER_UI = {
    "Title": {"kind": "menu", "open": "firstStart", "pause": True, "pauseMenu": False, "back": "none",
              "focus": "PlayButton"},
    "Pause": {"kind": "menu", "open": "manual", "pause": True, "pauseMenu": True, "back": "resume",
              "focus": "ResumeButton"},
    "Options": {"kind": "menu", "open": "manual", "pause": True, "pauseMenu": False, "back": "previous",
                "focus": "BackButton"},
    "Win": {"kind": "menu", "open": "manual", "pause": True, "pauseMenu": False, "back": "none",
            "focus": "PlayAgainButton"},
    "HUD": {"kind": "hud", "open": "start", "focus": None},
}
STARTER_TEXTS = {"Title": {"GameTitle": "Gem Hunt", "Subtitle": "Collect every gem",
                           "Hint": "WASD move · Space jump · Esc pause"},
                 "Pause": {"PauseTitle": "Paused"},
                 "Options": {"OptionsTitle": "Options", "MusicLabel": "Music", "SoundLabel": "Sound",
                             "LanguageLabel": "Language"},
                 "Win": {"WinTitle": "You win!", "WinScore": "Score {score}"},
                 "HUD": {"Score": "Score {score}"}}
# Button: (label, action, target).
STARTER_BUTTONS = {"Title": {"PlayButton": ("Play", "close", ""), "OptionsButton": ("Options", "open", "Options")},
                   "Pause": {"ResumeButton": ("Resume", "resume", ""),
                             "PauseOptionsButton": ("Options", "open", "Options"),
                             "RestartButton": ("Restart", "restart", "")},
                   "Options": {"LanguageButton": ("{ui.localeName}", "locale", ""),
                               "BackButton": ("Back", "previous", "")},
                   "Win": {"PlayAgainButton": ("Play again", "restart", "")},
                   "HUD": {}}
# Buttons of another size than the menus' 360 x 84: Language is a control in its Options row, as tall as the
# sliders' focus rings.
STARTER_BUTTON_SIZES = {"LanguageButton": [360.0, 64.0]}
STARTER_SLIDERS = {"MusicSlider": "audio.music", "SoundSlider": "audio.sfx"}  # on Options: no code, the game saves
STARTER_MENUS = ("Title", "Pause", "Options", "Win")  # each pops open and fades out in 0.25 s
# The starter's French table (ui/locales/fr.json, scaffolded with the template): every text of its UI but the
# game's name, which is the project's own.
STARTER_LOCALE = STARTER / "ui" / "locales" / "fr.json"
# The engine library's Shine (GAME-UI §11.4) as `npm run registry` lists it, for the title's Play button.
SHINE = {"name": "Shine", "description": "A band of light sweeps across", "builtin": True, "category": "Style",
         "ui": True, "fields": [{"name": "color", "type": "color", "default": [1, 1, 1, 0.6]},
                                {"name": "width", "type": "float", "default": 0.2, "min": 0.01, "max": 1},
                                {"name": "speed", "type": "float", "default": 1.5, "min": 0.05},
                                {"name": "interval", "type": "float", "default": 3, "min": 0, "unit": "time"},
                                {"name": "angle", "type": "float", "default": -0.5236, "unit": "angle"}]}
MAKE_MUSIC = MAKE_SFX.with_name("make_music.py")


def _files(folder: pathlib.Path) -> set[str]:
    return {p.relative_to(folder).as_posix() for p in folder.rglob("*") if p.is_file() and "__pycache__" not in p.parts}


def _node():
    return toolchain.node_executable(projects.__file__) or shutil.which("node")


def _project_name_texts() -> list[str]:
    """The open UI file's texts marked as the game's name, which New Project renames in a game made from a
    saved template (docs/GAME-UI.md §11.7)."""
    return sorted(o.name for o in bpy.data.objects if o.get(projects.UI_PROJECT_NAME))


class TemplateDiscoveryTests(unittest.TestCase):
    def test_templates_are_found_in_the_folder(self):
        found = projects.templates()
        ids = [t["id"] for t in found]
        self.assertEqual(ids[0], "starter", "the starter comes first")
        self.assertTrue(set(GENRES) <= set(ids), ids)
        for t in found:
            with self.subTest(template=t["id"]):
                self.assertTrue(t["label"].strip())
                self.assertTrue(0 < len(t["description"]) <= 78, "one line that fits the New Project dialog")
                self.assertNotIn("\n", t["description"])
                self.assertEqual(projects._icon(t["icon"]), t["icon"], "the icon exists in Blender")

    def test_folders_without_a_template_are_ignored(self):
        self.assertIsNone(projects.template_info("__pycache__"))
        self.assertIsNone(projects.template_info("no-such-template"))

    def test_enum_lists_every_template_with_its_description(self):
        items = projects._templates_enum(None, None)
        self.assertEqual([i[0] for i in items], [t["id"] for t in projects.templates()])
        self.assertTrue(all(i[2] for i in items))

    def test_picker_draws_a_row_per_template(self):
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
                rows.append(("label", text))

            def prop_enum(self, data, prop, value, text="", icon="NONE"):
                rows.append(("choice", value, text, icon))

        operator = mock.Mock(template="shooter")
        projects.draw_template_picker(Layout(), operator)
        choices = [r for r in rows if r[0] == "choice"]
        self.assertEqual([c[1] for c in choices], [t["id"] for t in projects.templates()])
        for info in projects.templates():
            self.assertIn(("label", info["description"]), rows)


class CompositionTests(unittest.TestCase):
    """template_files and {{base}} on a throw-away templates folder."""

    def setUp(self):
        self.folder = pathlib.Path(tempfile.mkdtemp(prefix="gs_templates_", dir=tmp_base()))
        patch = mock.patch.object(projects, "TEMPLATES", self.folder)
        patch.start()
        self.addCleanup(patch.stop)
        self.addCleanup(shutil.rmtree, self.folder, True)

    def write(self, rel, text):
        path = self.folder / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def test_overlay_replaces_extends_and_excludes(self):
        self.write("base/gamestudio.json", "{}")
        self.write("base/AGENTS.md", "# {{name}}\n\nShared intro.\n\n## Loop\n\nShared.\n")
        self.write("base/CLAUDE.md", "@AGENTS.md\n")
        self.write("base/src/systems/old.ts", "old")
        self.write("base/src/main.ts", "main")
        self.write("base/.claude/skills/a/SKILL.md", "skill a")
        self.write("genre/template.json", json.dumps({"label": "Genre", "description": "d", "base": "base",
                                                      "exclude": ["src/systems"]}))
        self.write("genre/AGENTS.md", "# {{name}}\n\nGenre intro.\n\n{{base}}\n")
        self.write("genre/src/systems/new.ts", "new")
        self.write("genre/.claude/skills/b/SKILL.md", "skill b")
        files = projects.template_files("genre")
        self.assertEqual(set(files), {"gamestudio.json", "AGENTS.md", "CLAUDE.md", "src/main.ts", "src/systems/new.ts",
                                      ".claude/skills/a/SKILL.md", ".claude/skills/b/SKILL.md"})
        self.assertEqual(projects._compose_text(files["AGENTS.md"]),
                         "# {{name}}\n\nGenre intro.\n\nShared intro.\n\n## Loop\n\nShared.\n")
        self.assertEqual(files["CLAUDE.md"], [self.folder / "base" / "CLAUDE.md"], "the base's import, as it is")
        self.assertEqual(files["src/main.ts"], [self.folder / "base" / "src" / "main.ts"])

    def test_template_json_is_never_copied(self):
        self.write("solo/template.json", json.dumps({"label": "Solo"}))
        self.write("solo/gamestudio.json", "{}")
        self.assertEqual(set(projects.template_files("solo")), {"gamestudio.json"})

    def test_errors(self):
        self.write("loop/template.json", json.dumps({"base": "loop"}))
        with self.assertRaisesRegex(ValueError, "its own base"):
            projects.template_files("loop")
        with self.assertRaisesRegex(ValueError, "Unknown project template"):
            projects.template_files("missing")
        self.write("orphan/template.json", "{}")
        self.write("orphan/AGENTS.md", "{{base}}")
        with self.assertRaisesRegex(ValueError, "base template has no such file"):
            projects._compose_text(projects.template_files("orphan")["AGENTS.md"])


class ScaffoldTests(unittest.TestCase):
    """Every real template scaffolds into a complete project."""

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix="gs_scaffold_", dir=tmp_base()))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def scaffold(self, template) -> pathlib.Path:
        root = self.tmp / template
        root.mkdir()
        projects.scaffold(root, "Test Game", template)
        return root

    def test_every_template_scaffolds(self):
        for info in projects.templates():
            with self.subTest(template=info["id"]):
                root = self.scaffold(info["id"])
                package = json.loads((root / "package.json").read_text("utf-8"))
                self.assertEqual(package["name"], "test-game")
                self.assertTrue(package["dependencies"]["@gamestudio/engine"].startswith("file:"))
                config = json.loads((root / "gamestudio.json").read_text("utf-8"))
                self.assertEqual((config["name"], config["startScene"]), ("Test Game", "main"))
                self.assertEqual(list(config)[:2], ["name", "template"], "the template is recorded after the name")
                self.assertEqual(config["template"], info["id"])
                engine = json.loads((projects.runtime_packages() / "engine" / "package.json").read_text("utf-8"))
                self.assertEqual(config["engine"], engine["version"], "the engine it was made with")
                self.assertEqual(projects.project_template(root), info["id"])
                json.loads((root / ".mcp.json").read_text("utf-8"))
                json.loads((root / ".claude" / "settings.json").read_text("utf-8"))
                for rel in ("AGENTS.md", "CLAUDE.md", "ASSISTANT.md", "index.html", "src/main.ts", "tsconfig.json",
                            ".gitignore"):
                    self.assertTrue((root / rel).is_file(), rel)
                # One set of instructions for Claude Code and Codex: AGENTS.md, which CLAUDE.md imports.
                agents = (root / "AGENTS.md").read_text("utf-8")
                self.assertTrue(agents.startswith("# Test Game\n"))
                self.assertEqual(agentfiles.instructions_state(root), agentfiles.SHARED)
                self.assertEqual((root / "CLAUDE.md").read_text("utf-8").splitlines()[0], agentfiles.IMPORT_LINE)
                self.assertLess(len(agents.encode("utf-8")), 32 * 1024, "Codex reads 32 KiB of AGENTS.md by default")
                main = (root / "src" / "main.ts").read_text("utf-8")
                for folder in ("components", "systems", "shaders"):
                    self.assertIn(f"import.meta.glob('./{folder}/*.ts', {{ eager: true }})", main, "registers its game shaders")
                self.assertFalse((root / "template.json").exists())
                self.assertTrue((root / "scenes").is_dir() and (root / ".gamestudio").is_dir())
                for path in root.rglob("*"):
                    if path.is_file() and path.suffix in projects.TEXT_SUFFIXES:
                        self.assertNotIn("{{", path.read_text("utf-8"), f"placeholder left in {path.relative_to(root)}")

    def test_genre_templates_get_the_starters_agent_files(self):
        shared = sorted(p for p in _files(STARTER) if p.startswith(".claude/") or p in (".mcp.json", "CLAUDE.md"))
        self.assertTrue(any(p.endswith("SKILL.md") for p in shared))
        for genre, skill in GENRES.items():
            with self.subTest(template=genre):
                root = self.scaffold(genre)
                for rel in shared:
                    self.assertEqual((root / rel).read_bytes().replace(b"\r\n", b"\n"),
                                     (STARTER / rel).read_bytes().replace(b"\r\n", b"\n"), rel)
                self.assertTrue((root / ".claude" / "skills" / skill / "SKILL.md").is_file(), skill)
                agents = (root / "AGENTS.md").read_text("utf-8")
                self.assertTrue(agents.startswith("# Test Game\n"))
                self.assertIn(f"`{skill}`", agents, "AGENTS.md names the genre skill")
                self.assertIn("## The loop", agents, "and keeps the starter's sections")
                self.assertEqual(agents.count("\n# "), 0, "one title")
                self.assertEqual(agentfiles.instructions_state(root), agentfiles.SHARED, "the starter's CLAUDE.md")
                self.assertIn("conventions for the AI assistant", (root / "ASSISTANT.md").read_text("utf-8"))

    def test_instructions_are_for_any_coding_agent(self):
        """AGENTS.md is read by Claude Code and Codex: it never says "Claude" for the coding agent, and
        what only Claude Code needs is in CLAUDE.md, under the import."""
        for template in ("starter", *GENRES):
            with self.subTest(template=template):
                text = projects.render_text(projects.template_files(template)["AGENTS.md"], {"name": "Game"})
                for line in text.splitlines():
                    self.assertNotRegex(line.replace("Claude Code", "").replace("CLAUDE.md", ""), r"\bClaude\b", line)
        claude = (STARTER / "CLAUDE.md").read_text("utf-8")
        self.assertEqual(agentfiles.split_claude_sections(claude)[0].strip(), agentfiles.IMPORT_LINE,
                         "CLAUDE.md is the import and a Claude Code section, nothing else")
        self.assertIn(".claude/settings.json", claude)

    def test_missing_agent_files_come_back_from_the_projects_template(self):
        """Open in Claude Code's repair composes the project's own template as New Project did."""
        from gamestudio import claude_code

        for template in ("starter", *GENRES):
            with self.subTest(template=template):
                root = self.scaffold(template)
                agent = sorted(p for p in _files(root) if p.split("/")[0] in claude_code.AGENT_FILES)
                made = {rel: (root / rel).read_text("utf-8") for rel in agent}
                for name in claude_code.AGENT_FILES:
                    path = root / name
                    if path.is_dir():
                        shutil.rmtree(path)
                    elif path.exists():
                        path.unlink()
                self.assertEqual(claude_code.agent_template(root), template)
                self.assertEqual(claude_code.add_agent_files(root), list(claude_code.AGENT_FILES))
                self.assertEqual(sorted(p for p in _files(root) if p.split("/")[0] in claude_code.AGENT_FILES), agent)
                made[".mcp.json"] = made[".mcp.json"].replace(claude_code.MCP_ENTRY, claude_code.mcp_entry(root))
                for rel in agent:
                    self.assertEqual((root / rel).read_text("utf-8"), made[rel], rel)

    def test_projects_without_a_known_template_use_the_default(self):
        cases = (({"name": "Old"}, None), ({"template": "no-such-template"}, None), ({"template": "../starter"}, None),
                 ({"template": 3}, None), ({"template": "driving"}, "driving"))
        for index, (config, expected) in enumerate(cases):
            with self.subTest(config=config):
                root = self.tmp / str(index)
                root.mkdir()
                (root / "gamestudio.json").write_text(json.dumps(config), encoding="utf-8")
                self.assertEqual(projects.project_template(root, default="shooter"), expected or "shooter")
                self.assertEqual(projects.project_template(root), expected or "starter")
        self.assertEqual(projects.project_template(self.tmp), "starter", "no gamestudio.json")

    def test_genre_templates_keep_one_source_of_truth(self):
        """A genre folder holds only its own files: nothing of the starter's is forked."""
        starter = _files(STARTER)
        for genre in GENRES:
            with self.subTest(template=genre):
                own = _files(projects.TEMPLATES / genre)
                self.assertEqual(own & starter, {"AGENTS.md", "ASSISTANT.md", "template.json"})
                self.assertIn("{{base}}", (projects.TEMPLATES / genre / "AGENTS.md").read_text("utf-8"))
                self.assertNotIn("CLAUDE.md", own, "the starter's CLAUDE.md, which imports AGENTS.md")

    def test_starter_sound_effects(self):
        """Short 16-bit mono WAVs, normalised and silent at both ends, as tools/make_sfx.py makes them."""
        folder = STARTER / "assets" / "sfx"
        self.assertEqual({p.stem for p in folder.glob("*.wav")}, SOUNDS)
        for path in sorted(folder.glob("*.wav")):
            with self.subTest(sound=path.name), wave.open(str(path)) as w:
                self.assertEqual((w.getnchannels(), w.getsampwidth(), w.getframerate()), (1, 2, 22050))
                count = w.getnframes()
                values = struct.unpack(f"<{count}h", w.readframes(count))
                self.assertLessEqual(count / 22050, 1.5, "short")
                self.assertEqual((values[0], values[-1]), (0, 0), "no click at either end")
                self.assertAlmostEqual(max(map(abs, values)) / 32767, 0.85, delta=0.01, msg="normalised")
        spec = importlib.util.spec_from_file_location("make_sfx", MAKE_SFX)
        make_sfx = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(make_sfx)
        stale = [name for name, data in make_sfx.render().items() if (folder / name).read_bytes() != data]
        self.assertEqual(stale, [], "out of date: regenerate them with python tools/make_sfx.py")
        self.assertIn("assets/sfx/*.wav", (STARTER / "AGENTS.md").read_text("utf-8"))
        root = self.scaffold("shooter")
        self.assertTrue(SOUNDS <= {p.stem for p in (root / "assets" / "sfx").glob("*.wav")}, "genre projects get them")

    def test_starter_music(self):
        """assets/music/theme.ogg, tools/make_music.py's loop, which the starter level's Music object plays: Ogg
        Vorbis at 48 kHz (the rate the engine decodes at before the first click), stereo, small."""
        spec = importlib.util.spec_from_file_location("make_music", MAKE_MUSIC)
        make_music = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(make_music)
        path = STARTER / "assets" / "music" / "theme.ogg"
        info = make_music.ogg_info(path.read_bytes())
        self.assertEqual((info["rate"], info["channels"]), (48000, 2))
        self.assertEqual(info["frames"], make_music.LENGTH, "the whole loop, so it loops in time")
        self.assertLessEqual(info["bytes"], 400 * 1024, "small enough for every new game")
        self.assertEqual(projects.MUSIC["clip"], "assets/music/theme.ogg")
        root = self.scaffold("starter")
        self.assertEqual((root / "assets" / "music" / "theme.ogg").read_bytes(), path.read_bytes(), "the project's own")

    def test_genre_projects_have_their_code_and_level_script(self):
        for genre in GENRES:
            with self.subTest(template=genre):
                root = self.scaffold(genre)
                components = {p.name for p in (root / "src" / "components").glob("*.ts")}
                systems = {p.name for p in (root / "src" / "systems").glob("*.ts")}
                self.assertNotIn("collectible.ts", components, "the starter's sample code is excluded")
                self.assertFalse((root / "ui").exists(), "no game UI: the genres keep their code menus")
                self.assertGreaterEqual(len(components), 4)
                self.assertGreaterEqual(len(systems), 4)
                self.assertTrue((root / projects.LEVEL_SCRIPT).is_file())
                self.assertTrue(list((root / "assets" / "sfx").glob("*.wav")))
                for path in (root / "src").rglob("*.ts"):
                    for line in path.read_text("utf-8").splitlines():
                        if "../../assets/" in line:
                            target = (path.parent / line.split("'")[1].split("?")[0]).resolve()
                            self.assertTrue(target.is_file(), f"{path.name}: {target}")


class StarterUiTests(unittest.TestCase):
    """The starter's game UI (projects.build_starter_ui): New Project gives a starter game ui/ui.blend with
    the Title, Pause, Options, Win and HUD screens, and its French table ui/locales/fr.json, and never a game
    made from a template built on the starter."""

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix="gs_starter_ui_", dir=tmp_base()))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        reset_scene()

    def tearDown(self):
        reset_scene()
        project.invalidate_cache()
        registry.reload_registry(None)

    def scaffold(self, template="starter") -> pathlib.Path:
        root = self.tmp / template
        root.mkdir()
        projects.scaffold(root, "Gem Hunt", template)
        helpers.write_registry(root, dict(ui_registry(), shaders=[SHINE]))  # npm run registry, in New Project
        return root

    def test_only_the_starter_template_has_it(self):
        self.assertTrue(projects.template_info("starter")["starter_ui"])
        self.assertEqual([rel for rel in projects.template_files("starter") if rel.startswith("ui/")],
                         ["ui/locales/fr.json"], "its translations: New Project builds ui/ui.blend")
        for genre in GENRES:
            with self.subTest(template=genre):
                self.assertFalse(projects.template_info(genre)["starter_ui"], "the base's template.json is its own")
                # The starter's French table is for the starter's texts: the genres' UI scripts write others.
                self.assertIn("ui/locales", projects.template_info(genre)["exclude"])
                self.assertFalse([rel for rel in projects.template_files(genre) if rel.startswith("ui/")])

    def test_new_project_builds_it_for_the_starter_only(self):
        for template in ("starter", *GENRES):
            with self.subTest(template=template):
                root = self.scaffold(template)
                level = root / "scenes" / "main.blend"
                built, scripted = [], []
                with mock.patch.object(projects, "build_starter_ui", built.append), \
                        mock.patch.object(projects, "build_ui", scripted.append), \
                        mock.patch.object(projects, "build_level", return_value=level), \
                        mock.patch.object(projects, "build_starter_scene", return_value=level), \
                        mock.patch("gamestudio.workspaces.show_game"):
                    self.assertEqual(projects.finish_new_project(root), level)
                self.assertEqual(built, [root] if template == "starter" else [])
                # A genre with its own tools/build_ui.py builds its UI with it (its *UiTests check it).
                self.assertEqual(scripted, [root] if (root / projects.UI_SCRIPT).is_file() else [])

    def test_new_project_keeps_a_ui_file_and_goes_on_without_one(self):
        root = self.scaffold()
        level = root / "scenes" / "main.blend"
        patches = (mock.patch.object(projects, "build_starter_scene", return_value=level),
                   mock.patch("gamestudio.workspaces.show_game"))
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        with mock.patch.object(projects, "build_starter_ui", side_effect=ValueError("no UI components")), \
                self.assertLogs(projects.log, "ERROR"):
            self.assertEqual(projects.finish_new_project(root), level, "the level still opens")
        self.assertTrue((root / "ui" / "locales" / "fr.json").is_file(), "ui/ holds the template's translations")
        (root / "ui" / "ui.blend").write_bytes(b"")
        with mock.patch.object(projects, "build_starter_ui") as build:
            projects.finish_new_project(root)
        build.assert_not_called()

    def test_a_failed_build_leaves_nothing_behind(self):
        root = self.scaffold()
        helpers.write_registry(root, helpers.fixture_registry())  # an engine without the UI components
        table = (root / "ui" / "locales" / "fr.json").read_bytes()
        with self.assertRaises(gameui.GameUIError) as caught:
            projects.build_starter_ui(root)
        self.assertEqual(caught.exception.code, "ui_components_missing")
        self.assertEqual(sorted(p.relative_to(root).as_posix() for p in (root / "ui").rglob("*")),
                         ["ui/locales", "ui/locales/fr.json"], "no UI file; the template's translations stay")
        self.assertEqual((root / "ui" / "locales" / "fr.json").read_bytes(), table)
        self.assertFalse(ui_core.export_dir(root).exists(), "nor an export of them")
        self.assertEqual(bpy.data.filepath, "", "an empty file is open")
        # A project without ui/ (a template without translations): none again.
        shutil.rmtree(root / "ui")
        with self.assertRaises(gameui.GameUIError):
            projects.build_starter_ui(root)
        self.assertFalse((root / "ui").exists())
        self.assertFalse(ui_core.export_dir(root).exists())

    def test_without_the_shine_shader_it_goes_on(self):
        """An engine whose registry lacks the library's Shine: Play's focus art stays plain, and the UI is built."""
        root = self.scaffold()
        helpers.write_registry(root, ui_registry())
        with self.assertLogs(projects.log, "WARNING") as logs:
            projects.build_starter_ui(root)
        self.assertIn("Shine", "\n".join(logs.output))
        self.assertEqual(gameui.build.panel_shader(bpy.data.objects["PlayButtonFocus"]), "")
        self.assertTrue((root / "ui" / "ui.blend").is_file())

    def test_the_screens(self):
        root = self.scaffold()
        path = projects.build_starter_ui(root)
        self.assertEqual(path, root / "ui" / "ui.blend")
        self.assertEqual(pathlib.Path(bpy.data.filepath), path, "built in this Blender")
        self.assertFalse(bpy.data.is_dirty, "and saved")
        self.assertFalse(path.with_name("ui.blend1").exists(), "no save version of the half-built file")
        self.assertEqual(bpy.context.window.scene.name if bpy.context.window else bpy.context.scene.name, "Title")
        described = {c["name"]: c for c in gameui.describe()["canvases"]}
        self.assertEqual(set(described), set(STARTER_UI))
        for name, canvas in described.items():
            with self.subTest(canvas=name):
                self.assertEqual(canvas["size"], [1920, 1080])
                self.assertNotIn("problems", canvas)
                for key, value in STARTER_UI[name].items():
                    self.assertEqual(canvas["settings"][key], value, key)
                elements = {e["name"]: e for e in canvas["elements"]}
                for element, body in STARTER_TEXTS[name].items():
                    self.assertEqual(elements[element]["text"], body)
                for element, (label, action, target) in STARTER_BUTTONS[name].items():
                    self.assertEqual(elements[element]["kind"], "button")
                    self.assertEqual(elements[element + "Label"]["text"], label)
                    button = elements[element]["components"]["UiButton"]
                    self.assertEqual((button.get("action", "event"), button.get("target", ""), button["sound"]),
                                     (action, target, "assets/sfx/click.wav"))
                    self.assertEqual(elements[element]["size"], STARTER_BUTTON_SIZES.get(element, [360.0, 84.0]),
                                     "big enough for touch")
                self.assertEqual({e["font"] for e in elements.values() if "font" in e}, {"assets/fonts/Inter.woff2"},
                                 "one font, Blender's Inter, which the game draws the same")
                for element in elements.values():
                    x, y = element["at"]
                    self.assertTrue(48 <= x <= 1920 - 48 and 48 <= y <= 1080 - 48, f"{element['name']} in the margins")
        backdrop = described["Title"]["settings"]["backdrop"]
        self.assertAlmostEqual(backdrop[3], 0.55, places=3)
        for name in STARTER_MENUS:
            self.assertEqual(described[name]["settings"]["backdrop"], backdrop, name)
        title = {e["name"]: e for e in described["Title"]["elements"]}
        self.assertEqual((title["GameTitle"]["text_size"], title["GameTitle"]["anchor"]), (120.0, "top"))
        self.assertTrue(title["GameTitle"]["components"]["UiText"]["fit"], "a long name shrinks to fit")
        self.assertEqual(_project_name_texts(), ["GameTitle"], "the game's name, renamed in games from saved templates")
        self.assertEqual(title["Hint"]["anchor"], "bottom")
        # The title's buttons are a column under one centred group (design-ui): Play, then Options under it.
        self.assertEqual((title["PlayButton"]["at"], title["OptionsButton"]["at"]), ([960.0, 660.0], [960.0, 770.0]))
        self.assertEqual((title["PlayButton"]["parent"], title["OptionsButton"]["parent"]), ("TitleButtons",) * 2)
        self.assertEqual({k: title["TitleButtons"].get(k) for k in ("kind", "at", "anchor", "anchor_auto")},
                         {"kind": "group", "at": [960.0, 715.0], "anchor": "center", "anchor_auto": None})
        self.assertLess(title["TitleButtons"]["z"], title["PlayButton"]["z"])
        pause = {e["name"]: e for e in described["Pause"]["elements"]}
        self.assertEqual([pause[b]["at"][1] for b in STARTER_BUTTONS["Pause"]], [515.0, 625.0, 735.0],
                         "Resume, Options and Restart, top to bottom")
        hud = {e["name"]: e for e in described["HUD"]["elements"]}
        self.assertEqual(hud["ScorePanel"]["anchor"], "top-left")
        self.assertEqual(hud["Score"]["parent"], "ScorePanel", "it moves with its panel")
        self.assertLess(hud["ScorePanel"]["z"], hud["Score"]["z"], "drawn under the score")

        # Saving exported every screen for the game.
        index = gameui_export.read_index(root)["canvases"]
        self.assertEqual({n: (e["kind"], e["open"]) for n, e in index.items()},
                         {n: (s["kind"], s["open"]) for n, s in STARTER_UI.items()})
        for name in STARTER_UI:
            with self.subTest(exported=name):
                data = json.loads((ui_core.export_dir(root) / index[name]["file"]).read_text("utf-8"))
                elements = {e["name"]: e for e in data["elements"]}
                focus = STARTER_UI[name]["focus"]
                self.assertEqual(data["settings"]["focus"], ids.get_id(bpy.data.objects[focus]) if focus else None)
                for element, body in STARTER_TEXTS[name].items():
                    self.assertEqual(elements[element]["text"]["body"], body)
                    self.assertEqual(elements[element]["text"]["font"], "assets/fonts/Inter.woff2")
                self.assertIn("assets/fonts/Inter.woff2", data["assets"])
                if STARTER_BUTTONS[name]:
                    self.assertIn("assets/sfx/click.wav", data["assets"])
        self.assertTrue((root / "assets" / "fonts" / "Inter.woff2").is_file(), "the font is the project's own")
        hud = {e["name"]: e for e in json.loads(
            (ui_core.export_dir(root) / index["HUD"]["file"]).read_text("utf-8"))["elements"]}
        self.assertAlmostEqual(abs(hud["GemIcon"]["transform"]["rotation"]), 0.7854, places=3, msg="a diamond")
        title = {e["name"]: e for e in json.loads(
            (ui_core.export_dir(root) / index["Title"]["file"]).read_text("utf-8"))["elements"]}
        self.assertEqual(title["GameTitle"]["text"]["box"], [-840, 0, 1680, 0], "fit shrinks it to one line")

        with self.assertRaisesRegex(ValueError, "already exists"):
            projects.build_starter_ui(root)
        self.assertTrue(path.is_file(), "kept")

    def test_options_and_animations(self):
        """Options, from the title's and the pause menu's Options buttons: a Music and a Sound slider on the
        channels' volumes (the game writes and saves them), each with a focus ring, and Back. Every menu pops
        open and fades out; the HUD just appears."""
        root = self.scaffold()
        projects.build_starter_ui(root)
        described = {c["name"]: c for c in gameui.describe()["canvases"]}
        options = {e["name"]: e for e in described["Options"]["elements"]}
        self.assertEqual(options["OptionsCard"]["anchor"], "center")
        rows = []
        for slider, value in STARTER_SLIDERS.items():
            with self.subTest(slider=slider):
                element = options[slider]
                self.assertEqual({k: element.get(k) for k in ("kind", "value", "min", "max", "step", "size", "parent")},
                                 {"kind": "slider", "value": value, "min": 0.0, "max": 1.0, "step": 0.1,
                                  "size": [360.0, 28.0], "parent": "OptionsCard"})
                self.assertEqual(element["components"]["UiSlider"]["sound"], "assets/sfx/click.wav")
                ring = options[slider + "Focus"]
                self.assertEqual((ring["parent"], ring["components"]["UiState"]["show"]), (slider, "focusedOrPressed"))
                self.assertLess(ring["z"], options[slider + "Bg"]["z"], "drawn under the slider")
                label = options[slider.replace("Slider", "Label")]
                self.assertEqual(label["at"][1], element["at"][1], "its name on the same row")
                self.assertLess(label["at"][0], element["at"][0] - element["size"][0] / 2, "on the left")
                rows.append(element["at"][1])
        # The Language row: its name on the left, the button in the sliders' column.
        language, label = options["LanguageButton"], options["LanguageLabel"]
        self.assertEqual((label["at"][1], label["at"][0]), (language["at"][1], options["MusicLabel"]["at"][0]))
        self.assertEqual(language["at"][0], options["MusicSlider"]["at"][0])
        rows.append(language["at"][1])
        # Navigation goes by position: Up from Back (focused first) reaches Language, then Sound, then Music; the
        # rows are evenly spaced, and Back is under them, all inside the card.
        rows.append(options["BackButton"]["at"][1])
        self.assertEqual(rows, sorted(rows))
        self.assertEqual({b - a for a, b in zip(rows, rows[1:-1])}, {100.0})
        card = options["OptionsCard"]
        top, bottom = card["at"][1] - card["size"][1] / 2, card["at"][1] + card["size"][1] / 2
        self.assertTrue(top < options["OptionsTitle"]["at"][1] and rows[-1] + 84 / 2 < bottom - 48,
                        f"Back {rows[-1]} inside the card {top}..{bottom}")
        for name, canvas in described.items():
            with self.subTest(canvas=name):
                if name in STARTER_MENUS:
                    self.assertEqual(canvas["animations"], {"open": 0.25, "close": 0.25})
                    self.assertEqual(canvas["transition"], projects.UI_TRANSITION)
                else:
                    self.assertNotIn("animations", canvas)

        index = gameui_export.read_index(root)["canvases"]
        for name in STARTER_UI:
            with self.subTest(exported=name):
                data = json.loads((ui_core.export_dir(root) / index[name]["file"]).read_text("utf-8"))
                if name not in STARTER_MENUS:
                    self.assertNotIn("animations", data)
                    continue
                animations = data["animations"]
                self.assertEqual(set(animations), {"open", "close"})
                top = {e["id"] for e in data["elements"] if e["parent"] is None}
                for kind, channels in (("open", {"opacity", "scaleX", "scaleY"}), ("close", {"opacity"})):
                    self.assertAlmostEqual(animations[kind]["duration"], 0.25, places=3)
                    self.assertEqual(set(animations[kind]["tracks"]), top, "every top-level element is keyed")
                    for track in animations[kind]["tracks"].values():
                        self.assertEqual(set(track), channels, kind)
                        first, last = track["opacity"][0], track["opacity"][-1]
                        self.assertEqual((first, last), (0, 1) if kind == "open" else (1, 0))
                        if kind == "open":
                            self.assertAlmostEqual(track["scaleX"][0] / track["scaleX"][-1], 0.9, places=3)
        data = json.loads((ui_core.export_dir(root) / index["Options"]["file"]).read_text("utf-8"))
        elements = {e["name"]: e for e in data["elements"]}
        for slider, value in STARTER_SLIDERS.items():
            values = elements[slider]["components"]["UiSlider"]
            self.assertEqual((values["value"], values["fill"], values["handle"]),
                             (value, elements[slider + "Fill"]["id"], elements[slider + "Handle"]["id"]))
            self.assertEqual(elements[slider + "Focus"]["components"]["UiState"]["show"], "focusedOrPressed")
        self.assertEqual(elements["BackButton"]["components"]["UiButton"]["action"], "previous")

    def test_the_title_gem(self):
        """Above the game's name the level's teal gem turns: a 3D model (UiModel, GAME-UI §11.1) made with the
        collectible kit's gem, which the export puts into Title.ui.glb for the game to draw lit."""
        root = self.scaffold()
        projects.build_starter_ui(root)
        title = {e["name"]: e for e in gameui.describe("Title")["canvases"][0]["elements"]}
        gem = title["TitleGem"]
        self.assertEqual({k: gem.get(k) for k in ("kind", "size", "view", "zoom", "spin", "clip", "light", "anchor")},
                         {"kind": "model", "size": [220.0, 220.0], "view": "threeQuarter", "zoom": 1.0, "spin": 0.6,
                          "clip": "", "light": "studio", "anchor": "top"})
        self.assertEqual(gem["source"], [{"object": "TitleGemModel", "kit": "collectible"}])
        kind, params = kits.kit_params(bpy.data.objects["TitleGemModel"])
        self.assertEqual((kind, params["style"], params["material"]), ("collectible", "gem", "auto"),
                         "the level's pickups: a cut gem in the teal gem material")
        self.assertNotIn("TitleGemModel", title, "its objects are the model, not elements")
        # Centred above the name and clear of its line, inside the top margin.
        name = title["GameTitle"]
        self.assertEqual(gem["at"][0], name["at"][0])
        self.assertLess(gem["at"][1] + gem["size"][1] / 2, name["at"][1] - name["text_size"] / 2)
        self.assertGreaterEqual(gem["at"][1] - gem["size"][1] / 2, 48)
        self.assertEqual(gem["anchor"], name["anchor"], "it stays with the name on other screen shapes")
        mesh = bpy.data.objects["TitleGemModel"]
        # The export: one model element, its GLB beside the canvas file, hashed in the index.
        entry = gameui_export.read_index(root)["canvases"]["Title"]
        data = json.loads((ui_core.export_dir(root) / entry["file"]).read_text("utf-8"))
        element = next(e for e in data["elements"] if e["name"] == "TitleGem")
        self.assertEqual(element["type"], "model")
        self.assertEqual(element["model"], {"glb": "Title.ui.glb", "node": element["id"], "clips": []})
        settings = element["components"]["UiModel"]
        self.assertEqual({k: settings.get(k) for k in ("size", "view", "spin", "light")},
                         {"size": [220, 220], "view": "threeQuarter", "spin": 0.6, "light": "studio"})
        self.assertNotIn("TitleGemModel", {e["name"] for e in data["elements"]})
        glb = ui_core.export_dir(root) / "Title.ui.glb"
        self.assertEqual(entry["files"], {"Title.ui.glb": export.file_hash(glb.read_bytes())})
        gltf = export.read_glb(glb)[0]
        nodes = gltf["nodes"]
        roots = [nodes[i] for i in gltf["scenes"][gltf.get("scene", 0)]["nodes"]]
        self.assertEqual([n["name"] for n in roots], [element["id"]], "one root node per model, named by its id")
        content = [nodes[i] for i in roots[0].get("children", [])]
        self.assertEqual([n["name"] for n in content], ["TitleGemModel"])
        self.assertIn("mesh", content[0])
        self.assertEqual([m["name"] for m in gltf["materials"]], [mesh.data.materials[0].name])

    def test_the_play_button_shines(self):
        """Play, the title's call to action, has the library's Shine on its yellow focus art (GAME-UI §11.4): a
        soft band sweeps across it every few seconds. Nothing else in the UI shines."""
        root = self.scaffold()
        projects.build_starter_ui(root)
        focus = bpy.data.objects["PlayButtonFocus"]
        mat = focus.data.materials[0]
        self.assertEqual(shaders.get_shader(mat), "Shine")
        values = shaders.get_values(mat)
        for key, value in projects.UI_SHINE.items():
            with self.subTest(field=key):
                self.assertEqual(list(map(lambda v: round(v, 4), values[key])) if isinstance(value, list)
                                 else round(values[key], 4), value)
        self.assertLessEqual(values["color"][3], 0.5, "a soft highlight, not a flash")
        self.assertGreaterEqual(values["interval"], 3, "a sweep every few seconds")
        title = {e["name"]: e for e in gameui.describe("Title")["canvases"][0]["elements"]}
        self.assertEqual((title["PlayButtonFocus"]["color"], title["PlayButtonFocus"]["shader"]), ("#FFD400", "Shine"))
        shaded = sorted(o.name for o in bpy.data.objects if o.type == "MESH" and gameui.build.panel_shader(o))
        self.assertEqual(shaded, ["PlayButtonFocus"], "only Play shines")
        entry = gameui_export.read_index(root)["canvases"]["Title"]
        data = json.loads((ui_core.export_dir(root) / entry["file"]).read_text("utf-8"))
        element = next(e for e in data["elements"] if e["name"] == "PlayButtonFocus")
        shader = element["mesh"]["material"]["shader"]
        self.assertEqual(shader["name"], "Shine")
        self.assertAlmostEqual(shader["values"]["interval"], projects.UI_SHINE["interval"], places=4)
        self.assertAlmostEqual(shader["values"]["color"][3], projects.UI_SHINE["color"][3], places=4)

    def test_the_language_button_and_the_french_table(self):
        """Options' Language button switches the UI to the next language (action locale, no target) and shows the
        current one's name. The template ships French, ui/locales/fr.json, for every text of the starter's UI but
        the game's name (GAME-UI §11.3), and saving the UI file exports it for the game."""
        table = json.loads(STARTER_LOCALE.read_text("utf-8"))
        self.assertEqual(set(table), {"name", "strings"})
        self.assertEqual(table["name"], "Français")
        for text, translation in table["strings"].items():
            with self.subTest(text=text):
                self.assertTrue(translation.strip() and translation == translation.strip())
                self.assertEqual(locales.bindings(translation), locales.bindings(text), "the same bindings")
        root = self.scaffold()
        self.assertEqual(json.loads((root / "ui" / "locales" / "fr.json").read_text("utf-8")), table, "scaffolded")
        projects.build_starter_ui(root)
        # Every text of the starter's UI has its French, and the table has nothing the UI lacks.
        out = gameui.strings("fr")
        self.assertEqual((out["locale"], out["name"], out["exists"]), ("fr", "Français", True))
        missing = [item for item in out["texts"] if item["translation"] is None]
        self.assertEqual([item["elements"] for item in missing], [["Title/GameTitle"]],
                         "only the game's name, which is the project's own")
        self.assertTrue(missing[0].get("project_name"))
        self.assertEqual(out["unused"], {})
        self.assertEqual(out["translated"], len(table["strings"]))
        # The button: its label is the language's own name, which is not translated.
        options = {e["name"]: e for e in gameui.describe("Options")["canvases"][0]["elements"]}
        self.assertEqual(options["LanguageButtonLabel"]["text"], "{ui.localeName}")
        self.assertFalse(locales.translatable("{ui.localeName}"))
        # Exported with the screens: the index names the languages, the table is beside the canvases.
        index = gameui_export.read_index(root)
        self.assertEqual((index["sourceLocale"], index["locales"]),
                         ("en", {"fr": {"file": "locales/fr.json", "name": "Français"}}))
        exported = json.loads((ui_core.export_dir(root) / "locales" / "fr.json").read_text("utf-8"))
        self.assertEqual(exported["strings"], table["strings"])
        data = json.loads((ui_core.export_dir(root) / index["canvases"]["Options"]["file"]).read_text("utf-8"))
        button = next(e for e in data["elements"] if e["name"] == "LanguageButton")["components"]["UiButton"]
        self.assertEqual((button["action"], button.get("target", "")), ("locale", ""))


class PlatformerUiTests(unittest.TestCase):
    """The platformer's game UI: New Project runs its tools/build_ui.py (projects.build_ui), which writes
    ui/ui.blend with its menus and HUD, bound to the ctx.store keys its systems set."""

    SCREENS = {
        "Title": {"kind": "menu", "open": "firstStart", "pause": True, "pauseMenu": False, "back": "none",
                  "focus": "PlayButton"},
        "Pause": {"kind": "menu", "open": "manual", "pause": True, "pauseMenu": True, "back": "resume",
                  "focus": "ResumeButton"},
        "Options": {"kind": "menu", "open": "manual", "pause": True, "pauseMenu": False, "back": "previous",
                    "focus": "BackButton"},
        "Win": {"kind": "menu", "open": "manual", "pause": True, "pauseMenu": False, "back": "none",
                "focus": "PlayAgainButton"},
        "GameOver": {"kind": "menu", "open": "manual", "pause": True, "pauseMenu": False, "back": "none",
                     "focus": "TryAgainButton"},
        "HUD": {"kind": "hud", "open": "start", "focus": None},
    }
    CONTROLS = "WASD move · Space jump · Esc pause · R restart"
    TEXTS = {"Title": {"GameTitle": "Sky Hop", "Subtitle": "Sky Garden: reach the golden star", "Hint": CONTROLS},
             "Pause": {"PauseTitle": "Paused", "PauseHint": CONTROLS},
             "Options": {"OptionsTitle": "Options", "MusicLabel": "Music", "SoundLabel": "Sound"},
             "Win": {"WinTitle": "Level complete!", "WinTime": "Time {time:m:ss.00}",
                     "WinCoins": "Coins {coins} / {coinTotal}", "BestTime": "Best {save.bestTime:m:ss.00}",
                     "NewBest": "New best time!"},
             "GameOver": {"OverTitle": "Game Over", "OverCoins": "Coins {coins} / {coinTotal}"},
             "HUD": {"Coins": "{coins} / {coinTotal}", "Clock": "{time:m:ss}"}}
    # name: (label, action, target)
    BUTTONS = {"Title": {"PlayButton": ("Play", "close", ""), "OptionsButton": ("Options", "open", "Options")},
               "Pause": {"ResumeButton": ("Resume", "resume", ""),
                         "PauseOptionsButton": ("Options", "open", "Options"),
                         "RestartButton": ("Restart", "restart", "")},
               "Options": {"BackButton": ("Back", "previous", "")},
               "Win": {"PlayAgainButton": ("Play again", "restart", "")},
               "GameOver": {"TryAgainButton": ("Try again", "restart", "")},
               "HUD": {}}
    # The ctx.store keys the screens show, and the system that sets each (src/lib/level.ts lists them).
    STORE = {"coins": "coins.ts", "coinTotal": "coins.ts", "time": "goal.ts", "newBest": "goal.ts",
             "lives": "respawn.ts"}

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix="gs_platformer_ui_", dir=tmp_base()))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        reset_scene()

    def tearDown(self):
        reset_scene()
        project.invalidate_cache()
        registry.reload_registry(None)

    def scaffold(self) -> pathlib.Path:
        root = self.tmp / "platformer"
        root.mkdir()
        projects.scaffold(root, "Sky Hop", "platformer")
        helpers.write_registry(root, ui_registry())  # npm run registry, in New Project
        return root

    def test_new_project_runs_its_ui_script(self):
        root = self.scaffold()
        self.assertTrue((root / projects.UI_SCRIPT).is_file())
        level = root / "scenes" / "main.blend"
        with mock.patch.object(projects, "build_ui") as build, \
                mock.patch.object(projects, "build_starter_ui") as starter, \
                mock.patch.object(projects, "build_level", return_value=level), \
                mock.patch("gamestudio.workspaces.show_game"):
            self.assertEqual(projects.finish_new_project(root), level)
        build.assert_called_once_with(root)
        starter.assert_not_called()

    def test_the_screens(self):
        root = self.scaffold()
        path = projects.build_ui(root)
        self.assertEqual(path, root / "ui" / "ui.blend")
        self.assertEqual(pathlib.Path(bpy.data.filepath), path, "built in this Blender")
        self.assertFalse(bpy.data.is_dirty, "and saved")
        self.assertFalse(path.with_name("ui.blend1").exists(), "no save version of the half-built file")
        described = {c["name"]: c for c in gameui.describe()["canvases"]}
        self.assertEqual(set(described), set(self.SCREENS))
        for name, canvas in described.items():
            with self.subTest(canvas=name):
                self.assertEqual(canvas["size"], [1920, 1080])
                self.assertNotIn("problems", canvas)
                for key, value in self.SCREENS[name].items():
                    self.assertEqual(canvas["settings"][key], value, key)
                elements = {e["name"]: e for e in canvas["elements"]}
                for element, body in self.TEXTS[name].items():
                    self.assertEqual(elements[element]["text"], body, element)
                for element, (label, action, target) in self.BUTTONS[name].items():
                    self.assertEqual(elements[element]["kind"], "button")
                    self.assertEqual(elements[element + "Label"]["text"], label)
                    button = elements[element]["components"]["UiButton"]
                    self.assertEqual((button.get("action", "event"), button.get("target", ""), button["sound"]),
                                     (action, target, "assets/sfx/click.wav"))
                    self.assertEqual(elements[element]["size"], [360.0, 84.0], "big enough for touch")
                self.assertEqual({e["font"] for e in elements.values() if "font" in e}, {"assets/fonts/Inter.woff2"},
                                 "one font, Blender's Inter, which the game draws the same")
                for element in elements.values():
                    x, y = element["at"]
                    self.assertTrue(48 <= x <= 1920 - 48 and 48 <= y <= 1080 - 48, f"{element['name']} in the margins")
                if canvas["settings"]["kind"] == "menu":  # menus pop open and fade out
                    self.assertEqual(canvas["transition"], {"open": "pop", "close": "fade", "duration": 0.25})
                    self.assertAlmostEqual(canvas["settings"]["backdrop"][3], 0.55, places=3)
                else:
                    self.assertNotIn("transition", canvas)
        title = {e["name"]: e for e in described["Title"]["elements"]}
        self.assertTrue(title["GameTitle"]["components"]["UiText"]["fit"], "a long name shrinks to fit")
        self.assertEqual(_project_name_texts(), ["GameTitle"], "the game's name, renamed in games from saved templates")
        self.assertEqual(title["TitleStar"]["image"], "assets/ui/star.png")
        self.assertEqual((title["PlayButton"]["parent"], title["OptionsButton"]["parent"]), ("TitleButtons", "TitleButtons"))
        self.assertLess(title["PlayButton"]["at"][1], title["OptionsButton"]["at"][1], "Options under Play")
        pause = {e["name"]: e for e in described["Pause"]["elements"]}
        self.assertEqual(sorted(("ResumeButton", "PauseOptionsButton", "RestartButton"), key=lambda n: pause[n]["at"][1]),
                         ["ResumeButton", "PauseOptionsButton", "RestartButton"])
        options = {e["name"]: e for e in described["Options"]["elements"]}
        for slider, value in (("MusicSlider", "audio.music"), ("SoundSlider", "audio.sfx")):
            self.assertEqual({k: options[slider][k] for k in ("kind", "value", "min", "max", "step")},
                             {"kind": "slider", "value": value, "min": 0, "max": 1, "step": 0.1})
            self.assertEqual(options[slider]["components"]["UiSlider"]["sound"], "assets/sfx/click.wav")
        win = {e["name"]: e for e in described["Win"]["elements"]}
        # One of the two shows, by ctx.store.newBest (a boolean reads as 1 or 0).
        for element, compare in (("NewBest", "equals"), ("BestTime", "below")):
            show = win[element]["components"]["UiShow"]  # than 1 is the default, left out
            self.assertEqual((show["value"], show["compare"], show.get("than", 1)), ("newBest", compare, 1))
        hud = {e["name"]: e for e in described["HUD"]["elements"]}
        self.assertEqual({k: hud["Lives"][k] for k in ("kind", "count", "value", "image", "empty")},
                         {"kind": "icons", "count": 5, "value": "lives", "image": "assets/ui/heart.png",
                          "empty": "assets/ui/heart-empty.png"})
        self.assertIn("START_LIVES = 5;", (root / "src" / "lib" / "level.ts").read_text("utf-8"), "a heart per life")
        self.assertEqual(hud["CoinIcon"]["image"], "assets/ui/coin.png")
        self.assertEqual({hud[n]["anchor"] for n in ("CoinPanel", "LivesPanel")}, {"top-left"})
        self.assertEqual(hud["ClockPanel"]["anchor"], "top-right")
        self.assertEqual((hud["Coins"]["parent"], hud["Clock"]["parent"]), ("CoinPanel", "ClockPanel"))

        # Saving exported every screen, with the menus' animations and every file they use.
        index = gameui_export.read_index(root)["canvases"]
        self.assertEqual({n: (e["kind"], e["open"]) for n, e in index.items()},
                         {n: (s["kind"], s["open"]) for n, s in self.SCREENS.items()})
        for name, settings in self.SCREENS.items():
            with self.subTest(exported=name):
                data = json.loads((ui_core.export_dir(root) / index[name]["file"]).read_text("utf-8"))
                elements = {e["name"]: e for e in data["elements"]}
                focus = settings["focus"]
                self.assertEqual(data["settings"]["focus"], ids.get_id(bpy.data.objects[focus]) if focus else None)
                for element, body in self.TEXTS[name].items():
                    self.assertEqual(elements[element]["text"]["body"], body)
                self.assertEqual(set(data.get("animations", {})), {"open", "close"} if settings["kind"] == "menu" else set())
                if self.BUTTONS[name]:
                    self.assertIn("assets/sfx/click.wav", data["assets"])
        hud = json.loads((ui_core.export_dir(root) / index["HUD"]["file"]).read_text("utf-8"))
        self.assertTrue({"assets/ui/coin.png", "assets/ui/heart.png", "assets/ui/heart-empty.png"} <= set(hud["assets"]))
        self.assertTrue((root / "assets" / "fonts" / "Inter.woff2").is_file(), "the font is the project's own")

        with self.assertRaisesRegex(ValueError, "already exists"):
            projects.build_ui(root)
        self.assertTrue(path.is_file(), "kept")

    def test_the_systems_set_what_the_screens_show(self):
        """The screens are opened by name and read ctx.store: the systems set every key they bind, and draw no
        code screens or corner HUD texts (ctx.hud is for labels pinned to the world)."""
        systems = projects.TEMPLATES / "platformer" / "src" / "systems"
        code = {p.name: p.read_text("utf-8") for p in systems.glob("*.ts")}
        for key, system in self.STORE.items():
            self.assertRegex(code[system], rf"ctx\.store\.{key}\s*(=|\+=|-=|\?\?=)", f"{system} sets {key}")
        self.assertIn("ctx.save.set('bestTime'", code["goal.ts"])
        self.assertIn("ctx.ui.open('Win')", code["goal.ts"])
        self.assertIn("ctx.ui.open('GameOver')", code["respawn.ts"])
        everything = "\n".join(code.values())
        for gone in ("ctx.ui.screen(", "ctx.ui.pauseMenu(", "position: '"):
            self.assertNotIn(gone, everything)
        for call in everything.split("ctx.hud.set(")[1:]:
            self.assertIn("at: ", call.split(");")[0], "pinned to an entity or a point")


# The shooter's game UI (its tools/build_ui.py): each canvas's settings, its buttons (label, action, target) and texts.
SHOOTER_UI = {
    "Title": {"kind": "menu", "open": "firstStart", "pause": True, "pauseMenu": False, "back": "none",
              "focus": "PlayButton"},
    "Pause": {"kind": "menu", "open": "manual", "pause": True, "pauseMenu": True, "back": "resume",
              "focus": "ResumeButton"},
    "Options": {"kind": "menu", "open": "manual", "pause": True, "pauseMenu": False, "back": "previous",
                "focus": "BackButton"},
    "GameOver": {"kind": "menu", "open": "manual", "pause": True, "pauseMenu": False, "back": "none",
                 "focus": "TryAgainButton"},
    "HUD": {"kind": "hud", "open": "start", "focus": None},
}
SHOOTER_BUTTONS = {"Title": {"PlayButton": ("Play", "close", ""), "OptionsButton": ("Options", "open", "Options")},
                   "Pause": {"ResumeButton": ("Resume", "resume", ""),
                             "PauseOptionsButton": ("Options", "open", "Options"),
                             "RestartButton": ("Restart", "restart", "")},
                   "Options": {"BackButton": ("Back", "previous", "")},
                   "GameOver": {"TryAgainButton": ("Try again", "restart", "")},
                   "HUD": {}}
SHOOTER_TEXTS = {"Title": {"GameTitle": "Pit Test", "Subtitle": "The Pit: survive the waves",
                           "BestScore": "Best score {save.bestScore}"},
                 "GameOver": {"OverTitle": "Game Over", "OverScore": "Score {score}", "OverWave": "Reached wave {wave}",
                              "OverBest": "Best {save.bestScore}", "NewBest": "New best score!"},
                 "HUD": {"Health": "{health}", "Wave": "Wave {wave} · {enemiesLeft} left", "GetReady": "Get ready",
                         "Score": "Score {score}", "WaveBanner": "Wave {wave}", "ClearedBanner": "Wave {wave} cleared!"}}
# Shown by code (ctx.ui.show) or by a UiShow rule: element -> (value path, compare, than), or None for code.
SHOOTER_SHOWN = {"NewBest": None, "WaveBanner": None, "ClearedBanner": None, "ControlsHint": None,
                 "BestScore": ("save.bestScore", "atLeast", 1.0), "HealthBarLow": ("healthLow", "equals", 1.0),
                 "Wave": ("wave", "atLeast", 1.0), "GetReady": ("wave", "below", 1.0)}
SHOOTER_MENUS = ("Title", "Pause", "Options", "GameOver")  # each pops open and fades out in 0.25 s
SHOOTER = projects.TEMPLATES / "shooter"


class ShooterUiTests(unittest.TestCase):
    """The shooter's game UI: New Project runs its tools/build_ui.py, which builds ui/ui.blend with Title, Pause,
    Options, GameOver and a HUD bound to the ctx.store values its systems keep, and exports them."""

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix="gs_shooter_ui_", dir=tmp_base()))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        reset_scene()

    def tearDown(self):
        reset_scene()
        project.invalidate_cache()
        registry.reload_registry(None)

    def scaffold(self) -> pathlib.Path:
        root = self.tmp / "shooter"
        root.mkdir()
        projects.scaffold(root, "Pit Test", "shooter")
        helpers.write_registry(root, ui_registry())  # npm run registry, in New Project
        return root

    def test_new_project_runs_its_ui_script(self):
        root = self.scaffold()
        self.assertTrue((root / projects.UI_SCRIPT).is_file())
        level = root / "scenes" / "main.blend"
        with mock.patch.object(projects, "build_starter_ui") as starter, \
                mock.patch.object(projects, "build_level", return_value=level), \
                mock.patch("gamestudio.workspaces.show_game"):
            self.assertEqual(projects.finish_new_project(root), level)
        starter.assert_not_called()
        self.assertTrue(ui_core.ui_file(root).is_file())
        self.assertFalse(ui_core.ui_file(root).with_name("ui.blend1").exists())
        index = gameui_export.read_index(root)["canvases"]
        self.assertEqual({n: (e["kind"], e["open"]) for n, e in index.items()},
                         {n: (s["kind"], s["open"]) for n, s in SHOOTER_UI.items()})
        with self.assertRaisesRegex(ValueError, "already exists"):
            projects.build_ui(root)

    def test_the_screens(self):
        root = self.scaffold()
        path = projects.build_ui(root)
        self.assertEqual(path, root / "ui" / "ui.blend")
        self.assertEqual(pathlib.Path(bpy.data.filepath), path, "built in this Blender")
        self.assertFalse(bpy.data.is_dirty, "and saved")
        described = {c["name"]: c for c in gameui.describe()["canvases"]}
        self.assertEqual(set(described), set(SHOOTER_UI))
        everything = {}
        for name, canvas in described.items():
            with self.subTest(canvas=name):
                self.assertEqual(canvas["size"], [1920, 1080])
                self.assertNotIn("problems", canvas)
                for key, value in SHOOTER_UI[name].items():
                    self.assertEqual(canvas["settings"][key], value, key)
                if name in SHOOTER_MENUS:
                    self.assertAlmostEqual(canvas["settings"]["backdrop"][3], 0.55, places=3)
                    self.assertEqual(canvas["transition"], {"open": "pop", "close": "fade", "duration": 0.25})
                else:
                    self.assertNotIn("animations", canvas)
                elements = {e["name"]: e for e in canvas["elements"]}
                everything.update(elements)
                for element, body in SHOOTER_TEXTS.get(name, {}).items():
                    self.assertEqual(elements[element]["text"], body)
                for element, (label, action, target) in SHOOTER_BUTTONS[name].items():
                    self.assertEqual(elements[element]["kind"], "button")
                    self.assertEqual(elements[element + "Label"]["text"], label)
                    button = elements[element]["components"]["UiButton"]
                    self.assertEqual((button.get("action", "event"), button.get("target", ""), button["sound"]),
                                     (action, target, "assets/sfx/click.wav"))
                    self.assertEqual(elements[element]["size"], [360.0, 84.0], "big enough for touch")
                self.assertEqual({e["font"] for e in elements.values() if "font" in e}, {"assets/fonts/Inter.woff2"})
                for element in elements.values():
                    x, y = element["at"]
                    self.assertTrue(48 <= x <= 1920 - 48 and 48 <= y <= 1080 - 48, f"{element['name']} in the margins")
                    if "parent" not in element:
                        self.assertNotIn("anchor_auto", element, f"{element['name']} has its own anchor")
        self.assertEqual(_project_name_texts(), ["GameTitle"], "the game's name, renamed in games from saved templates")
        # Options: the channel volumes, written by the sliders with no code.
        for slider, value in (("MusicSlider", "audio.music"), ("SoundSlider", "audio.sfx")):
            self.assertEqual((everything[slider]["kind"], everything[slider]["value"]), ("slider", value))
        # The HUD: the store's health on a bar (max 100, the Player's Health.max), its red twin while healthLow.
        for bar, color in (("HealthBar", "#51CF66"), ("HealthBarLow", "#FF6B6B")):
            self.assertEqual(everything[bar]["components"]["UiBar"]["value"], "health")
            self.assertEqual(everything[bar]["components"]["UiBar"]["max"], 100)
            self.assertEqual(everything[bar + "Fill"]["color"], color)
        self.assertEqual({k: everything[k]["anchor"] for k in ("HealthPanel", "WavePanel", "ScorePanel")},
                         {"HealthPanel": "top-left", "WavePanel": "top", "ScorePanel": "top-right"})
        for element, rule in SHOOTER_SHOWN.items():
            with self.subTest(shown=element):
                show = everything[element].get("components", {}).get("UiShow")
                if rule is None:
                    self.assertFalse(everything[element]["visible"], "starts hidden: code shows it")
                    self.assertIsNone(show)
                else:
                    self.assertTrue(everything[element]["visible"])
                    self.assertEqual((show["value"], show.get("compare", "atLeast"), show.get("than", 1.0)), rule)

        # Saving exported every screen, with its animations, for the game.
        index = gameui_export.read_index(root)["canvases"]
        self.assertEqual(set(index), set(SHOOTER_UI))
        for name in SHOOTER_UI:
            with self.subTest(exported=name):
                data = json.loads((ui_core.export_dir(root) / index[name]["file"]).read_text("utf-8"))
                focus = SHOOTER_UI[name]["focus"]
                self.assertEqual(data["settings"]["focus"], ids.get_id(bpy.data.objects[focus]) if focus else None)
                self.assertEqual(set(data.get("animations", {})), {"open", "close"} if name in SHOOTER_MENUS else set())
                if SHOOTER_BUTTONS[name]:
                    self.assertIn("assets/sfx/click.wav", data["assets"])
        hud = json.loads((ui_core.export_dir(root) / index["HUD"]["file"]).read_text("utf-8"))
        self.assertIn("assets/ui/heart.png", hud["assets"])

    def test_the_code_uses_what_the_screens_have(self):
        """Every canvas and element the systems name exists, and every store value the screens show is one the
        systems keep."""
        import re

        root = self.scaffold()
        projects.build_ui(root)
        described = {c["name"]: c for c in gameui.describe()["canvases"]}
        elements = {(c, e["name"]) for c, canvas in described.items() for e in canvas["elements"]}
        code = "\n".join(p.read_text("utf-8") for p in sorted((SHOOTER / "src").rglob("*.ts")))
        named = set(re.findall(r"ctx\.ui\.(?:show|hide|text|bar)\('(\w+)/(\w+)'", code))
        named |= {("HUD", n) for n in re.findall(r"showBanner\(ctx, '(\w+)'", code)}  # WaveSystem's banners
        self.assertTrue(named, "the systems show and hide HUD elements")
        self.assertLessEqual(named, elements)
        self.assertLessEqual(set(re.findall(r"ctx\.ui\.open\('(\w+)'\)", code)), set(described))
        self.assertNotRegex(code, r"ctx\.ui\.(screen|pauseMenu)\(", "Blender screens only")
        self.assertNotRegex(code, r"ctx\.hud\.set\('(score|wave|banner|help)'", "the corner texts are the HUD's")
        bound = set()
        for canvas in described.values():
            for element in canvas["elements"]:
                bound |= set(re.findall(r"\{(\w+)[}:]", element.get("text", "")))
                for component in ("UiBar", "UiShow"):
                    value = element.get("components", {}).get(component, {}).get("value", "")
                    bound |= {value} if value and "." not in value else set()
        self.assertEqual(bound, {"score", "health", "healthLow", "wave", "enemiesLeft"})
        for key in bound:
            self.assertRegex(code, rf"ctx\.store\.{key}\s*(=|\?\?=)|{key}: ", f"a system keeps ctx.store.{key}")


class DrivingUiTests(unittest.TestCase):
    """The Time Trial's game UI: New Project runs its tools/build_ui.py before the level, which builds
    ui/ui.blend with Title, Pause, Options, Results and a racing HUD bound to the ctx.store values RaceSystem
    keeps, and exports them."""

    SCREENS = {
        "Title": {"kind": "menu", "open": "firstStart", "pause": True, "pauseMenu": False, "back": "none",
                  "focus": "PlayButton"},
        "Pause": {"kind": "menu", "open": "manual", "pause": True, "pauseMenu": True, "back": "resume",
                  "focus": "ResumeButton"},
        "Options": {"kind": "menu", "open": "manual", "pause": True, "pauseMenu": False, "back": "previous",
                    "focus": "BackButton"},
        "Results": {"kind": "menu", "open": "manual", "pause": True, "pauseMenu": False, "back": "none",
                    "focus": "RaceAgainButton"},
        "HUD": {"kind": "hud", "open": "start", "focus": None},
    }
    CONTROLS = "W/S drive · A/D steer · Space handbrake · Esc pause · R restart"
    TEXTS = {"Title": {"GameTitle": "Lap Test", "Subtitle": "Pine Valley Circuit · {laps} laps against the clock",
                       "Hint": CONTROLS},
             "Pause": {"PauseTitle": "Paused"},
             "Options": {"OptionsTitle": "Options", "MusicLabel": "Music", "SoundLabel": "Sound"},
             "Results": {"ResultsTitle": "Finished!", "TotalTime": "{raceTime:m:ss.00}",
                         "BestLapText": "Best lap {bestLap:m:ss.00}", "RecordText": "Record {save.bestRace:m:ss.00}",
                         "NewRecord": "New record!"},
             "HUD": {"StartText": "Drive through the start line", "StartControls": CONTROLS,
                     "LapText": "Lap {lap}/{laps}", "RaceTime": "{raceTime:m:ss.00}",
                     "BestLap": "Best lap {bestLap:m:ss.00}", "Speed": "{speedKmh:0}", "SpeedUnit": "km/h",
                     "OffRoad": "OFF ROAD"}}
    # name: (label, action, target)
    BUTTONS = {"Title": {"PlayButton": ("Play", "close", ""), "OptionsButton": ("Options", "open", "Options")},
               "Pause": {"ResumeButton": ("Resume", "resume", ""),
                         "PauseOptionsButton": ("Options", "open", "Options"),
                         "RestartButton": ("Restart", "restart", "")},
               "Options": {"BackButton": ("Back", "previous", "")},
               "Results": {"RaceAgainButton": ("Race again", "restart", "")},
               "HUD": {}}
    # Shown by code (ctx.ui.show) or by a UiShow rule: element -> (value path, compare, than), or None for code.
    SHOWN = {"NewRecord": None, "Split": None, "StartHint": ("lap", "equals", 0.0),
             "LapCard": ("lap", "atLeast", 1.0), "BestCard": ("bestLap", "notEquals", 0.0),
             "OffRoad": ("offRoad", "equals", 1.0)}
    MENUS = ("Title", "Pause", "Options", "Results")  # each pops open and fades out in 0.25 s
    SOURCE = projects.TEMPLATES / "driving" / "src"

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix="gs_driving_ui_", dir=tmp_base()))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        reset_scene()

    def tearDown(self):
        reset_scene()
        project.invalidate_cache()
        registry.reload_registry(None)

    def scaffold(self) -> pathlib.Path:
        root = self.tmp / "driving"
        root.mkdir()
        projects.scaffold(root, "Lap Test", "driving")
        helpers.write_registry(root, ui_registry())  # npm run registry, in New Project
        return root

    def test_new_project_builds_and_exports_it(self):
        root = self.scaffold()
        self.assertTrue((root / projects.UI_SCRIPT).is_file())
        level = root / "scenes" / "main.blend"
        with mock.patch.object(projects, "build_starter_ui") as starter, \
                mock.patch.object(projects, "build_level", return_value=level), \
                mock.patch("gamestudio.workspaces.show_game"):
            self.assertEqual(projects.finish_new_project(root), level)
        starter.assert_not_called()
        path = ui_core.ui_file(root)
        self.assertTrue(path.is_file())
        self.assertFalse(path.with_name("ui.blend1").exists(), "no save version of the half-built file")
        index = gameui_export.read_index(root)["canvases"]
        self.assertEqual({n: (e["kind"], e["open"]) for n, e in index.items()},
                         {n: (s["kind"], s["open"]) for n, s in self.SCREENS.items()})
        for name, entry in index.items():
            with self.subTest(exported=name):
                data = json.loads((ui_core.export_dir(root) / entry["file"]).read_text("utf-8"))
                elements = {e["name"]: e for e in data["elements"]}
                names = {e["id"]: e["name"] for e in data["elements"]}
                self.assertEqual(names.get(data["settings"]["focus"]), self.SCREENS[name]["focus"])
                for element, body in self.TEXTS[name].items():
                    self.assertEqual(elements[element]["text"]["body"], body)
                    self.assertEqual(elements[element]["text"]["font"], "assets/fonts/Inter.woff2")
                self.assertEqual(set(data.get("animations", {})), {"open", "close"} if name in self.MENUS else set())
                if self.BUTTONS[name]:
                    self.assertIn("assets/sfx/click.wav", data["assets"])
        with self.assertRaisesRegex(ValueError, "already exists"):
            projects.build_ui(root)

    def test_the_screens(self):
        root = self.scaffold()
        path = projects.build_ui(root)
        self.assertEqual(path, root / "ui" / "ui.blend")
        self.assertEqual(pathlib.Path(bpy.data.filepath), path, "built in this Blender")
        self.assertFalse(bpy.data.is_dirty, "and saved")
        described = {c["name"]: c for c in gameui.describe()["canvases"]}
        self.assertEqual(set(described), set(self.SCREENS))
        everything = {}
        for name, canvas in described.items():
            with self.subTest(canvas=name):
                self.assertEqual(canvas["size"], [1920, 1080])
                self.assertNotIn("problems", canvas)
                for key, value in self.SCREENS[name].items():
                    self.assertEqual(canvas["settings"][key], value, key)
                if name in self.MENUS:
                    self.assertAlmostEqual(canvas["settings"]["backdrop"][3], 0.55, places=3)
                    self.assertEqual(canvas["transition"], {"open": "pop", "close": "fade", "duration": 0.25})
                else:
                    self.assertNotIn("transition", canvas)
                elements = {e["name"]: e for e in canvas["elements"]}
                everything.update(elements)
                for element, body in self.TEXTS[name].items():
                    self.assertEqual(elements[element]["text"], body, element)
                for element, (label, action, target) in self.BUTTONS[name].items():
                    self.assertEqual(elements[element]["kind"], "button")
                    self.assertEqual(elements[element + "Label"]["text"], label)
                    button = elements[element]["components"]["UiButton"]
                    self.assertEqual((button.get("action", "event"), button.get("target", ""), button["sound"]),
                                     (action, target, "assets/sfx/click.wav"))
                    self.assertEqual(elements[element]["size"], [360.0, 84.0], "big enough for touch")
                self.assertEqual({e["font"] for e in elements.values() if "font" in e}, {"assets/fonts/Inter.woff2"})
                for element in elements.values():
                    x, y = element["at"]
                    self.assertTrue(48 <= x <= 1920 - 48 and 48 <= y <= 1080 - 48, f"{element['name']} in the margins")
                    if "parent" not in element:
                        self.assertNotIn("anchor_auto", element, f"{element['name']} has its own anchor")
        self.assertTrue(everything["GameTitle"]["components"]["UiText"]["fit"], "a long name shrinks to fit")
        self.assertEqual(_project_name_texts(), ["GameTitle"], "the game's name, renamed in games from saved templates")
        self.assertEqual((everything["PlayButton"]["parent"], everything["OptionsButton"]["parent"]),
                         ("TitleButtons", "TitleButtons"))
        self.assertLess(everything["PlayButton"]["at"][1], everything["OptionsButton"]["at"][1], "Options under Play")
        self.assertEqual(sorted(("ResumeButton", "PauseOptionsButton", "RestartButton"),
                                key=lambda n: everything[n]["at"][1]),
                         ["ResumeButton", "PauseOptionsButton", "RestartButton"])
        for slider, value in (("MusicSlider", "audio.music"), ("SoundSlider", "audio.sfx")):
            self.assertEqual({k: everything[slider][k] for k in ("kind", "value", "min", "max", "step")},
                             {"kind": "slider", "value": value, "min": 0, "max": 1, "step": 0.1})
            self.assertEqual(everything[slider]["components"]["UiSlider"]["sound"], "assets/sfx/click.wav")
        # The HUD's corners: lap and race time top left, best lap top right, speed bottom right.
        self.assertEqual({k: everything[k]["anchor"] for k in ("StartHint", "LapCard", "BestCard", "SpeedCard", "Split")},
                         {"StartHint": "top", "LapCard": "top-left", "BestCard": "top-right",
                          "SpeedCard": "bottom-right", "Split": "top"})
        self.assertEqual({everything[k]["parent"] for k in ("LapText", "RaceTime")}, {"LapCard"})
        self.assertEqual({everything[k]["parent"] for k in ("Speed", "SpeedUnit", "OffRoad")}, {"SpeedCard"})
        self.assertEqual(everything["SplitText"]["parent"], "Split")
        for element, rule in self.SHOWN.items():
            with self.subTest(shown=element):
                show = everything[element].get("components", {}).get("UiShow")
                if rule is None:
                    self.assertFalse(everything[element]["visible"], "starts hidden: code shows it")
                    self.assertIsNone(show)
                else:
                    self.assertTrue(everything[element]["visible"])
                    self.assertEqual((show["value"], show.get("compare", "atLeast"), show.get("than", 1.0)), rule)

    def test_the_code_uses_what_the_screens_have(self):
        """Every canvas and element RaceSystem names exists, every store value the screens show is one it keeps,
        and no code-only screen or corner HUD text is left."""
        import re

        root = self.scaffold()
        projects.build_ui(root)
        described = {c["name"]: c for c in gameui.describe()["canvases"]}
        elements = {e["name"] for canvas in described.values() for e in canvas["elements"]}
        code = "\n".join(p.read_text("utf-8") for p in sorted(self.SOURCE.rglob("*.ts")))
        named = set(re.findall(r"ctx\.ui\.(?:show|hide|text|bar)\('(\w+)'", code))
        self.assertEqual(named, {"SplitText", "Split", "NewRecord", "RecordText"})
        self.assertLessEqual(named, elements)
        self.assertEqual(set(re.findall(r"ctx\.ui\.open\('(\w+)'\)", code)), {"Results"})
        self.assertNotRegex(code, r"ctx\.ui\.(screen|pauseMenu)\(", "Blender screens only")
        self.assertNotIn("ctx.hud.", code, "no corner texts: the HUD canvas shows them")
        bound = set()
        for canvas in described.values():
            for element in canvas["elements"]:
                bound |= set(re.findall(r"\{(\w+)[}:]", element.get("text", "")))
                value = element.get("components", {}).get("UiShow", {}).get("value", "")
                bound |= {value} if value and "." not in value else set()
        self.assertEqual(bound, {"lap", "laps", "raceTime", "bestLap", "speedKmh", "offRoad"})
        for key in bound:
            self.assertRegex(code, rf"ctx\.store\.{key}\s*=|{key}: ", f"RaceSystem keeps ctx.store.{key}")
        self.assertIn("ctx.save.set('bestRace'", code, "the record the Results screen shows")


# The runner's game UI (its tools/build_ui.py): each canvas's settings, its buttons (label, action, target) and texts.
RUNNER_UI = {
    "Title": {"kind": "menu", "open": "firstStart", "pause": True, "pauseMenu": False, "back": "none",
              "focus": "PlayButton"},
    "Pause": {"kind": "menu", "open": "manual", "pause": True, "pauseMenu": True, "back": "resume",
              "focus": "ResumeButton"},
    "Options": {"kind": "menu", "open": "manual", "pause": True, "pauseMenu": False, "back": "previous",
                "focus": "BackButton"},
    "GameOver": {"kind": "menu", "open": "manual", "pause": True, "pauseMenu": False, "back": "none",
                 "focus": "TryAgainButton"},
    "HUD": {"kind": "hud", "open": "start", "focus": None},
}
RUNNER_BUTTONS = {"Title": {"PlayButton": ("Play", "close", ""), "OptionsButton": ("Options", "open", "Options")},
                  "Pause": {"ResumeButton": ("Resume", "resume", ""),
                            "PauseOptionsButton": ("Options", "open", "Options"),
                            "RestartButton": ("Restart", "restart", "")},
                  "Options": {"BackButton": ("Back", "previous", "")},
                  "GameOver": {"TryAgainButton": ("Try again", "restart", "")},
                  "HUD": {}}
RUNNER_TEXTS = {"Title": {"GameTitle": "Sky Test", "Subtitle": "Sunset Skyway: run as far as you can",
                          "TitleBest": "Best {save.bestDistance} m"},
                "GameOver": {"OverTitle": "Crashed!", "OverDistance": "{distance:0} m",
                             "OverStats": "{coins} coins · Score {score}", "OverBest": "Best {save.bestDistance} m",
                             "NewBestText": "New best!"},
                "HUD": {"Distance": "{distance:0} m", "Score": "Score {score}", "Coins": "{coins}",
                        "Banner": "{banner}"}}
# Shown by code (ctx.ui.show) or by a UiShow rule: element -> (value path, compare, than), or None for code.
RUNNER_SHOWN = {"NewBest": None, "Controls": None, "TitleBest": ("save.bestDistance", "atLeast", 1.0),
                "MagnetRow": ("magnetLeft", "notEquals", 0.0), "ShieldRow": ("shieldLeft", "notEquals", 0.0)}
RUNNER_MENUS = ("Title", "Pause", "Options", "GameOver")  # each pops open and fades out in 0.25 s
RUNNER = projects.TEMPLATES / "runner"


class RunnerUiTests(unittest.TestCase):
    """The runner's game UI: New Project runs its tools/build_ui.py, which builds ui/ui.blend with Title, Pause,
    Options, GameOver and a HUD bound to the ctx.store values its systems keep, and exports them."""

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix="gs_runner_ui_", dir=tmp_base()))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        reset_scene()

    def tearDown(self):
        reset_scene()
        project.invalidate_cache()
        registry.reload_registry(None)

    def scaffold(self) -> pathlib.Path:
        root = self.tmp / "runner"
        root.mkdir()
        projects.scaffold(root, "Sky Test", "runner")
        helpers.write_registry(root, ui_registry())  # npm run registry, in New Project
        return root

    def test_new_project_runs_its_ui_script(self):
        root = self.scaffold()
        self.assertTrue((root / projects.UI_SCRIPT).is_file())
        level = root / "scenes" / "main.blend"
        with mock.patch.object(projects, "build_starter_ui") as starter, \
                mock.patch.object(projects, "build_level", return_value=level), \
                mock.patch("gamestudio.workspaces.show_game"):
            self.assertEqual(projects.finish_new_project(root), level)
        starter.assert_not_called()
        self.assertTrue(ui_core.ui_file(root).is_file())
        self.assertFalse(ui_core.ui_file(root).with_name("ui.blend1").exists())
        index = gameui_export.read_index(root)["canvases"]
        self.assertEqual({n: (e["kind"], e["open"]) for n, e in index.items()},
                         {n: (s["kind"], s["open"]) for n, s in RUNNER_UI.items()})
        for name, entry in index.items():
            self.assertTrue((ui_core.export_dir(root) / entry["file"]).is_file(), name)
        with self.assertRaisesRegex(ValueError, "already exists"):
            projects.build_ui(root)

    def test_the_screens(self):
        root = self.scaffold()
        path = projects.build_ui(root)
        self.assertEqual(path, root / "ui" / "ui.blend")
        self.assertEqual(pathlib.Path(bpy.data.filepath), path, "built in this Blender")
        self.assertFalse(bpy.data.is_dirty, "and saved")
        described = {c["name"]: c for c in gameui.describe()["canvases"]}
        self.assertEqual(set(described), set(RUNNER_UI))
        everything = {}
        for name, canvas in described.items():
            with self.subTest(canvas=name):
                self.assertEqual(canvas["size"], [1920, 1080])
                self.assertNotIn("problems", canvas)
                for key, value in RUNNER_UI[name].items():
                    self.assertEqual(canvas["settings"][key], value, key)
                if name in RUNNER_MENUS:
                    self.assertAlmostEqual(canvas["settings"]["backdrop"][3], 0.55, places=3)
                    self.assertEqual(canvas["transition"], {"open": "pop", "close": "fade", "duration": 0.25})
                else:
                    self.assertNotIn("animations", canvas)
                elements = {e["name"]: e for e in canvas["elements"]}
                everything.update(elements)
                for element, body in RUNNER_TEXTS.get(name, {}).items():
                    self.assertEqual(elements[element]["text"], body)
                for element, (label, action, target) in RUNNER_BUTTONS[name].items():
                    self.assertEqual(elements[element]["kind"], "button")
                    self.assertEqual(elements[element + "Label"]["text"], label)
                    button = elements[element]["components"]["UiButton"]
                    self.assertEqual((button.get("action", "event"), button.get("target", ""), button["sound"]),
                                     (action, target, "assets/sfx/click.wav"))
                    self.assertEqual(elements[element]["size"], [360.0, 84.0], "big enough for touch")
                self.assertEqual({e["font"] for e in elements.values() if "font" in e}, {"assets/fonts/Inter.woff2"})
                for element in elements.values():
                    x, y = element["at"]
                    self.assertTrue(48 <= x <= 1920 - 48 and 48 <= y <= 1080 - 48, f"{element['name']} in the margins")
                    if "parent" not in element:
                        self.assertNotIn("anchor_auto", element, f"{element['name']} has its own anchor")
        self.assertEqual(_project_name_texts(), ["GameTitle"], "the game's name, renamed in games from saved templates")
        # The title's buttons are a column under one centred group, as the starter's.
        self.assertEqual({everything[b]["parent"] for b in RUNNER_BUTTONS["Title"]}, {"TitleButtons"})
        self.assertEqual(everything["TitleButtons"]["anchor"], "center")
        # Options: the channel volumes, written by the sliders with no code; a ring shows the selected one.
        for slider, value in (("MusicSlider", "audio.music"), ("SoundSlider", "audio.sfx")):
            self.assertEqual((everything[slider]["kind"], everything[slider]["value"]), ("slider", value))
            ring = everything[slider + "Focus"]
            self.assertEqual((ring["parent"], ring["components"]["UiState"]["show"]), (slider, "focusedOrPressed"))
            self.assertLess(ring["z"], everything[slider + "Bg"]["z"], "drawn under the slider")
        # The HUD: the distance top centre, the coins (with the coin icon) top right, power-up bars top left.
        self.assertEqual({k: everything[k]["anchor"] for k in ("DistancePanel", "CoinPanel", "PowerUps", "Banner")},
                         {"DistancePanel": "top", "CoinPanel": "top-right", "PowerUps": "top-left", "Banner": "center"})
        self.assertEqual(everything["CoinIcon"]["kind"], "image")
        for kind in ("magnet", "shield"):
            bar = everything[f"{kind.capitalize()}Bar"]["components"]["UiBar"]
            self.assertEqual((bar["value"], bar.get("max", 1.0)), (f"{kind}Left", 1.0))
        for element, rule in RUNNER_SHOWN.items():
            with self.subTest(shown=element):
                show = everything[element].get("components", {}).get("UiShow")
                if rule is None:
                    self.assertFalse(everything[element]["visible"], "starts hidden: code shows it")
                    self.assertIsNone(show)
                else:
                    self.assertTrue(everything[element]["visible"])
                    self.assertEqual((show["value"], show.get("compare", "atLeast"), show.get("than", 1.0)), rule)

        # Saving exported every screen, with its animations, for the game.
        index = gameui_export.read_index(root)["canvases"]
        self.assertEqual(set(index), set(RUNNER_UI))
        for name in RUNNER_UI:
            with self.subTest(exported=name):
                data = json.loads((ui_core.export_dir(root) / index[name]["file"]).read_text("utf-8"))
                focus = RUNNER_UI[name]["focus"]
                self.assertEqual(data["settings"]["focus"], ids.get_id(bpy.data.objects[focus]) if focus else None)
                self.assertEqual(set(data.get("animations", {})), {"open", "close"} if name in RUNNER_MENUS else set())
                if RUNNER_BUTTONS[name]:
                    self.assertIn("assets/sfx/click.wav", data["assets"])
        hud = json.loads((ui_core.export_dir(root) / index["HUD"]["file"]).read_text("utf-8"))
        self.assertIn("assets/ui/coin.png", hud["assets"])
        self.assertFalse((root / ".gamestudio" / "ui_unsaved.json").exists(), "exported from the saved file")

    def test_the_code_uses_what_the_screens_have(self):
        """Every canvas and element the systems name exists, and every store value the screens show is one the
        systems keep."""
        import re

        root = self.scaffold()
        projects.build_ui(root)
        described = {c["name"]: c for c in gameui.describe()["canvases"]}
        elements = {(c, e["name"]) for c, canvas in described.items() for e in canvas["elements"]}
        code = "\n".join(p.read_text("utf-8") for p in sorted((RUNNER / "src").rglob("*.ts")))
        named = set(re.findall(r"ctx\.ui\.(?:show|hide|text|bar)\('(\w+)/(\w+)'", code))
        self.assertTrue(named, "the systems show and set elements")
        self.assertLessEqual(named, elements)
        self.assertLessEqual(set(re.findall(r"ctx\.ui\.open\('(\w+)'\)", code)), set(described))
        self.assertNotRegex(code, r"ctx\.ui\.(screen|pauseMenu)\(", "Blender screens only")
        self.assertNotRegex(code, r"ctx\.hud\.(set\('(coins|score|distance|banner|help|powerup)'|bar\()",
                            "the corner texts and bars are the HUD canvas's")
        self.assertIn("ctx.hud.set(`coin-", code, "ctx.hud keeps the labels pinned over coins")
        bound = set()
        for canvas in described.values():
            for element in canvas["elements"]:
                bound |= set(re.findall(r"\{(\w+)[}:]", element.get("text", "")))
                for component in ("UiBar", "UiShow"):
                    value = element.get("components", {}).get(component, {}).get("value", "")
                    bound |= {value} if value and "." not in value else set()
        self.assertEqual(bound, {"distance", "score", "coins", "banner", "magnetLeft", "shieldLeft"})
        for key in bound:  # PowerUpSystem writes ctx.store[LEFT[kind]], LEFT naming the keys
            self.assertRegex(code, rf"ctx\.store\.{key}\s*(=|\?\?=|\+=)|{key}: |'{key}'",
                             f"a system keeps ctx.store.{key}")
        self.assertIn("ctx.save.set('bestDistance'", code, "the best distance the screens show")


def _view(scene) -> tuple[str, str]:
    return scene.view_settings.view_transform, scene.view_settings.look


class ColorManagementTests(unittest.TestCase):
    """Start scenes New Project builds use Khronos PBR Neutral, with no look: the game follows the scene's
    Color Management, so this is how every template game is tone-mapped."""

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix="gs_view_", dir=tmp_base()))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        reset_scene()
        self.addCleanup(reset_scene)

    def test_the_view_transform_exists(self):
        scene = bpy.context.scene
        scene.view_settings.view_transform, scene.view_settings.look = "AgX", "AgX - Punchy"
        projects.use_template_color_management(scene)
        self.assertEqual(_view(scene), ("Khronos PBR Neutral", "None"))

    def test_level_scripts_build_in_it(self):
        """build_level sets it on the scene the script builds in, whatever the startup file had."""
        bpy.context.scene.view_settings.view_transform = "Standard"
        (self.tmp / "tools").mkdir()
        (self.tmp / projects.LEVEL_SCRIPT).write_text(
            "import bpy\n\n"
            "def build(gs, root):\n"
            "    view = bpy.context.scene.view_settings\n"
            "    (root / 'seen.txt').write_text(view.view_transform + '|' + view.look)\n"
            "    return root / 'scenes' / 'main.blend'\n", encoding="utf-8")
        projects.build_level(self.tmp)
        self.assertEqual((self.tmp / "seen.txt").read_text(), "Khronos PBR Neutral|None")

    def test_genre_level_scripts_set_it_themselves(self):
        """So a rebuild from a terminal (blender --background --python tools/build_level.py) keeps it."""
        for genre in GENRES:
            with self.subTest(template=genre):
                script = (projects.TEMPLATES / genre / projects.LEVEL_SCRIPT).read_text("utf-8")
                self.assertIn(f'"{projects.VIEW_TRANSFORM}", "None"', script)


@unittest.skipUnless(_node(), "Node.js is needed to read the templates' components")
class LevelScriptTests(unittest.TestCase):
    """Each genre's tools/build_level.py, run the way New Project runs it, exports a playable scene."""

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix="gs_levels_", dir=tmp_base()))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        reset_scene()

    def tearDown(self):
        reset_scene()
        project.invalidate_cache()
        registry.reload_registry(None)

    def build(self, template) -> dict:
        root = self.tmp / template
        root.mkdir()
        projects.scaffold(root, "Level Test", template)
        toolchain.link_project(root, toolchain.sdk(projects.__file__))
        cli = root / "node_modules" / "@gamestudio" / "devserver" / "bin" / "gamestudio-registry.js"
        done = subprocess.run([_node(), str(cli)], cwd=root, capture_output=True, text=True, timeout=180,
                              env=toolchain.child_env(dict(os.environ)))
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        path = projects.finish_new_project(root)  # the level script, as New Project runs it
        self.assertEqual(path, root / "scenes" / "main.blend")
        self.assertTrue(path.is_file())
        self.assertEqual(pathlib.Path(bpy.data.filepath), path)
        if (root / projects.UI_SCRIPT).is_file():  # its tools/build_ui.py, run first, exported the game UI
            self.assertTrue(gameui_export.read_index(root)["canvases"])
        else:
            self.assertFalse((root / "ui").exists(), "no game UI: the genres keep their code menus")
            self.assertFalse(ui_core.export_dir(root).exists())
        self.assertEqual(bpy.context.scene.render.fps, 30)
        self.assertEqual(_view(bpy.context.scene), (projects.VIEW_TRANSFORM, "None"))
        return json.loads((root / ".gamestudio" / "export" / "main.scene.json").read_text("utf-8"))

    def check(self, template, manifest):
        counts = {}
        for entity in manifest["entities"]:
            for name in entity["components"]:
                counts[name] = counts.get(name, 0) + 1
        for component, least in LEVELS[template].items():
            self.assertGreaterEqual(counts.get(component, 0), least, f"{template}: {component}")
        self.assertIsNotNone(manifest["settings"]["activeCamera"])
        self.assertEqual(manifest["settings"].get("toneMapping"), "neutral", "the game follows the scene's view")
        if "CharacterController" in LEVELS[template]:
            self.assertAlmostEqual(bpy.data.objects["Player"].location.z, 0.0, msg="the player starts on the floor")

    def test_platformer_level(self):
        manifest = self.build("platformer")
        self.check("platformer", manifest)
        clips = {c["name"] for c in manifest["animations"]}
        self.assertTrue({"FlagRaise", "spin_bob", "sway"} <= clips, clips)
        # A rigged player whose Animator runs the "Player" graph (src/systems/player-animation.ts).
        self.assertTrue({"Idle", "Walk", "Run", "JumpStart", "JumpAir", "Fall", "Land"} <= clips, clips)
        player = next(e for e in manifest["entities"] if e["name"] == "Player")
        self.assertEqual(player["components"]["Animator"]["graph"], "Player")
        # The starter's theme on the music channel, which plays under the menus (the Options slider sets it).
        music = next(e for e in manifest["entities"] if e["name"] == "Music")["components"]["AudioSource"]
        self.assertEqual({k: music[k] for k in ("clip", "loop", "autoplay", "spatial", "channel", "volume")},
                         {"clip": "assets/music/theme.ogg", "loop": True, "autoplay": True, "spatial": False,
                          "channel": "music", "volume": 0.5})
        self.assertIn("assets/music/theme.ogg", manifest["assets"])
        # tools/build_ui.py made the menus and the HUD first (PlatformerUiTests checks them).
        root = self.tmp / "platformer"
        self.assertEqual(set(gameui_export.read_index(root)["canvases"]),
                         {"Title", "Pause", "Options", "Win", "GameOver", "HUD"})
        title = json.loads((ui_core.export_dir(root) / "Title.ui.json").read_text("utf-8"))
        texts = {e["name"]: e["text"]["body"] for e in title["elements"] if e["type"] == "text"}
        self.assertEqual(texts["GameTitle"], "Level Test", "the game's name")

    def test_shooter_level(self):
        manifest = self.build("shooter")
        self.check("shooter", manifest)
        self.assertEqual(sorted(p["name"] for p in manifest["prefabs"]), ["Bullet", "Grunt", "Runner"])
        names = {e["name"] for e in manifest["entities"]}
        self.assertFalse(names & {"Grunt", "Runner", "Bullet"}, "prefab templates are not level entities")
        grunt = next(p for p in manifest["prefabs"] if p["name"] == "Grunt")
        self.assertTrue({"Enemy", "Health", "RigidBody", "Collider"} <= set(grunt["entities"][0]["components"]))
        # The music: the starter's theme on the music channel (the Options screen's Music slider), everywhere.
        music = next(e for e in manifest["entities"] if e["name"] == "Music")["components"]["AudioSource"]
        self.assertEqual({k: music[k] for k in ("clip", "loop", "autoplay", "spatial", "channel")},
                         {"clip": "assets/music/theme.ogg", "loop": True, "autoplay": True, "spatial": False,
                          "channel": "music"})
        self.assertIn("assets/music/theme.ogg", manifest["assets"])
        # The game UI, built by its tools/build_ui.py before the level, with the registry `npm run registry` wrote.
        root = self.tmp / "shooter"
        index = gameui_export.read_index(root)["canvases"]
        self.assertEqual({n: (e["kind"], e["open"]) for n, e in index.items()},
                         {n: (s["kind"], s["open"]) for n, s in SHOOTER_UI.items()})
        title = json.loads((ui_core.export_dir(root) / index["Title"]["file"]).read_text("utf-8"))
        texts = {e["name"]: e["text"]["body"] for e in title["elements"] if e["type"] == "text"}
        self.assertEqual(texts["GameTitle"], "Level Test", "the game's name")

    def test_driving_level(self):
        manifest = self.build("driving")
        self.check("driving", manifest)
        indexes = sorted(e["components"]["Checkpoint"]["index"] for e in manifest["entities"]
                         if "Checkpoint" in e["components"])
        self.assertEqual(indexes, [0, 1, 2, 3])
        # The starter's theme on the music channel, which plays under the menus (the Options slider sets it).
        music = next(e for e in manifest["entities"] if e["name"] == "Music")["components"]["AudioSource"]
        self.assertEqual({k: music[k] for k in ("clip", "loop", "autoplay", "spatial", "channel", "volume")},
                         {"clip": "assets/music/theme.ogg", "loop": True, "autoplay": True, "spatial": False,
                          "channel": "music", "volume": 0.5})
        self.assertIn("assets/music/theme.ogg", manifest["assets"])
        # tools/build_ui.py made the menus and the HUD first (DrivingUiTests checks them).
        root = self.tmp / "driving"
        index = gameui_export.read_index(root)["canvases"]
        self.assertEqual({n: (e["kind"], e["open"]) for n, e in index.items()},
                         {n: (s["kind"], s["open"]) for n, s in DrivingUiTests.SCREENS.items()})
        title = json.loads((ui_core.export_dir(root) / index["Title"]["file"]).read_text("utf-8"))
        texts = {e["name"]: e["text"]["body"] for e in title["elements"] if e["type"] == "text"}
        self.assertEqual(texts["GameTitle"], "Level Test", "the game's name")

    def test_runner_level(self):
        manifest = self.build("runner")
        self.check("runner", manifest)
        names = {e["name"] for e in manifest["entities"]}
        self.assertIn("PlayerStart", names, "RunSystem spawns the Runner there")
        prefabs = {p["name"]: p for p in manifest["prefabs"]}
        self.assertTrue(CHUNKS | {"Runner"} <= set(prefabs), sorted(prefabs))
        self.assertFalse(names & set(prefabs), "prefab templates are not level entities")
        for name in sorted(n for n in prefabs if n.startswith("Chunk")):
            with self.subTest(chunk=name):
                components = [c for e in prefabs[name]["entities"] for c in e["components"]]
                self.assertEqual(components.count("TrackChunk"), 1)
                self.assertGreaterEqual(components.count("Socket"), 1)
        runner = {c for e in prefabs["Runner"]["entities"] for c in e["components"]}
        self.assertTrue({"Runner", "RigidBody", "Collider"} <= runner, runner)
        # The music: the starter's theme in the level (not a prefab), on the music channel the Options slider sets.
        music = next(e for e in manifest["entities"] if e["name"] == "Music")["components"]["AudioSource"]
        self.assertEqual({k: music[k] for k in ("clip", "loop", "autoplay", "spatial", "channel")},
                         {"clip": "assets/music/theme.ogg", "loop": True, "autoplay": True, "spatial": False,
                          "channel": "music"})
        self.assertIn("assets/music/theme.ogg", manifest["assets"])

    def test_starter_scene_uses_the_character_kit(self):
        root = self.tmp / "starter"
        root.mkdir()
        projects.scaffold(root, "Starter Test", "starter")
        toolchain.link_project(root, toolchain.sdk(projects.__file__))
        cli = root / "node_modules" / "@gamestudio" / "devserver" / "bin" / "gamestudio-registry.js"
        subprocess.run([_node(), str(cli)], cwd=root, check=True, capture_output=True, timeout=180)
        projects.finish_new_project(root)
        player = bpy.data.objects["Player"]
        self.assertIn("gs_kit", player, "made by the character kit")
        self.assertAlmostEqual(player.location.z, 0.0, msg="its origin is at the feet, on the floor")
        self.assertEqual(bpy.context.scene.render.fps, 30)
        self.assertEqual(_view(bpy.context.scene), (projects.VIEW_TRANSFORM, "None"), "the startup file had AgX")
        manifest = json.loads((root / ".gamestudio" / "export" / "main.scene.json").read_text("utf-8"))
        entity = next(e for e in manifest["entities"] if e["name"] == "Player")
        self.assertTrue({"CharacterController", "RigidBody", "Collider"} <= set(entity["components"]))
        self.assertEqual(manifest["settings"].get("toneMapping"), "neutral")
        # The background music: a loop on the music channel, under the sound effects (Options sets its volume).
        music = next(e for e in manifest["entities"] if e["name"] == "Music")["components"]["AudioSource"]
        self.assertEqual({k: music[k] for k in projects.MUSIC}, {**projects.MUSIC, "volume": 0.5})
        self.assertEqual((music["loop"], music["autoplay"], music["spatial"], music["channel"]),
                         (True, True, False, "music"))
        self.assertTrue((root / ".gamestudio" / "export" / "files" / "assets" / "music" / "theme.ogg").is_file(),
                        "exported with the level")
        # The game UI, built first with the registry `npm run registry` wrote, and exported for Play.
        self.assertEqual(pathlib.Path(bpy.data.filepath), root / "scenes" / "main.blend", "the level is open")
        self.assertTrue(ui_core.ui_file(root).is_file())
        index = gameui_export.read_index(root)["canvases"]
        self.assertEqual({n: (e["kind"], e["open"]) for n, e in index.items()},
                         {n: (s["kind"], s["open"]) for n, s in STARTER_UI.items()})
        title = json.loads((ui_core.export_dir(root) / index["Title"]["file"]).read_text("utf-8"))
        texts = {e["name"]: e["text"]["body"] for e in title["elements"] if e["type"] == "text"}
        self.assertEqual(texts["GameTitle"], "Starter Test", "the game's name")


class LocalLinkTests(unittest.TestCase):
    """A project made inside another Node project still gets its own node_modules links."""

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix="gs_nested_", dir=tmp_base()))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_packages_found_only_in_a_parent_are_linked_into_the_project(self):
        outer = self.tmp / "outer"
        (outer / "node_modules" / "three").mkdir(parents=True)
        (outer / "node_modules" / "three" / "package.json").write_text('{"name": "three", "version": "0.186.1"}')
        root = outer / "game"
        root.mkdir()
        (root / "package.json").write_text(json.dumps({"name": "game", "dependencies": {
            "three": "0.186.1", "not-installed-anywhere": "1.0.0"}}))
        self.assertTrue(toolchain.resolvable(root, "three"))
        self.assertEqual(projects.link_locally(root), ["three"])
        self.assertTrue(toolchain.is_link(root / "node_modules" / "three"))
        self.assertFalse((root / "node_modules" / "not-installed-anywhere").exists(), "left to npm install")
        self.assertEqual(projects.link_locally(root), [], "nothing to do the second time")
