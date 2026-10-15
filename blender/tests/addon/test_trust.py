# SPDX-License-Identifier: GPL-3.0-or-later
"""Trusted projects (core/trust.py) and the gates in front of a project's own code.

Opening someone else's project must not run its npm scripts, its packages' install scripts, its tools or
its dev server until the person trusts it. GameStudio's own projects (New Project, a copied example), the
recent projects of an earlier version and projects Blender would run scripts for are trusted.

The test runner trusts the temporary folders for its session (run_tests.py); these tests take that away
(_session) and keep the record in a scratch file. Nothing runs npm: the setup job, the dev server and the
browser are stubs. Temporary folders go under GS_TEST_TMP.
"""

import json
import os
import pathlib
import shutil
import sys
import tempfile
import types
import unittest
from unittest import mock

import bpy

from gamestudio import environment, projects
from gamestudio.assistant import tools
from gamestudio.bridge import api
from gamestudio.core import code_editor, compression, devserver, project, stream, toolchain, trust

from helpers import tmp_base

WINDOWS = sys.platform == "win32"
REAL_REFRESH = trust.refresh  # the tests below patch them; these read Blender and the command line for real
REAL_COMMAND_LINE = trust._command_line


class _Trust(unittest.TestCase):
    """A scratch trust record, no session trust, Blender's Auto Run off, and environment.py's trust state
    put back afterwards."""

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix="gs_trust_", dir=tmp_base()))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.record = self.tmp / "config" / trust.RECORD_FILE
        self.patch(mock.patch.dict(trust._record, {"path": self.record, "mtime": None, "data": None}))
        self.patch(mock.patch.object(trust, "_session", set()))
        self.patch(mock.patch.object(trust, "_blender", (False, [])))
        self.patch(mock.patch.object(trust, "refresh", lambda: None))
        self.patch(mock.patch.object(trust, "_command_line", lambda: None))
        self.patch(mock.patch.object(environment, "_not_now", set()))
        environment._trusting = None
        self.addCleanup(setattr, environment, "_trusting", None)
        environment.invalidate()
        self.root = self.game("game")

    def patch(self, patcher):
        value = patcher.start()
        self.addCleanup(patcher.stop)
        return value

    def game(self, name, dependencies=None) -> pathlib.Path:
        """A project someone else made: its package.json's registry script would start a program."""
        root = self.tmp / name
        (root / ".gamestudio").mkdir(parents=True)
        (root / "gamestudio.json").write_text(json.dumps({"name": name}), encoding="utf-8")
        manifest = {"name": name, "scripts": {"registry": "calc"}, "dependencies": dependencies or {}}
        (root / "package.json").write_text(json.dumps(manifest), encoding="utf-8")
        return root

    def forget_memory(self):
        """What the next Blender session knows: only the record on disk."""
        trust._record.update(mtime=None, data=None)
        trust._session.clear()


# --- The record ---------------------------------------------------------------------------------------


class RecordTests(_Trust):
    def test_a_foreign_folder_is_not_trusted(self):
        self.assertFalse(trust.is_trusted(self.root))
        self.assertFalse(trust.is_trusted(None))

    def test_trust_persists(self):
        trust.trust(self.root)
        self.assertTrue(trust.is_trusted(self.root))
        self.forget_memory()
        self.assertTrue(trust.is_trusted(self.root), "read back from the record")
        [entry] = json.loads(self.record.read_text("utf-8"))["projects"]
        self.assertEqual((entry["path"], entry["how"]), (trust.key(self.root), "user"))
        self.assertTrue(trust.is_trusted(self.root / "scenes"), "the folders inside it too")
        self.assertFalse(trust.is_trusted(self.tmp / "game-other"), "not a folder whose name only starts the same")
        if WINDOWS:
            self.assertTrue(trust.is_trusted(str(self.root).upper()), "Windows paths ignore case")

    def test_for_this_session_only(self):
        trust.trust(self.root, "tests", save=False)
        self.assertTrue(trust.is_trusted(self.root))
        self.assertFalse(self.record.exists(), "nothing written")
        self.forget_memory()
        self.assertFalse(trust.is_trusted(self.root))

    def test_new_projects_and_copied_examples_are_trusted(self):
        made = self.tmp / "made"
        made.mkdir()
        projects.scaffold(made, "Made Here")
        self.forget_memory()
        self.assertTrue(trust.is_trusted(made))
        self.assertEqual(json.loads(self.record.read_text("utf-8"))["projects"][0]["how"], "created")

        bundle = self.tmp / "install"
        example = bundle / "examples" / "demo"
        (example / "scenes").mkdir(parents=True)
        (example / "gamestudio.json").write_text('{"name": "Demo"}', encoding="utf-8")
        home = self.tmp / "Documents" / "GameStudio" / "Examples"
        with mock.patch.object(toolchain, "bundle_root", lambda *_a: bundle), \
                mock.patch.object(projects, "examples_home", lambda: home):
            copy = projects.prepare_example(example)
        self.assertEqual(copy, home / "demo")
        self.assertTrue(trust.is_trusted(copy))
        self.assertFalse(trust.is_trusted(example), "only GameStudio's copy")

    def test_the_recent_projects_are_trusted_once(self):
        old, later = self.game("old"), self.game("later")
        self.assertTrue(trust.migrate([str(old)]))
        self.assertTrue(trust.is_trusted(old))
        self.assertFalse(trust.migrate([str(old), str(later)]), "once")
        self.assertFalse(trust.is_trusted(later), "opened after the update: asked about")
        self.forget_memory()
        self.assertTrue(json.loads(self.record.read_text("utf-8"))["migrated"])
        self.assertTrue(trust.is_trusted(old))

    def test_an_earlier_copy_of_an_example_is_trusted(self):
        bundle = self.tmp / "install"
        example = bundle / "examples" / "demo"
        example.mkdir(parents=True)
        home = self.tmp / "Documents" / "GameStudio" / "Examples"
        (home / "demo").mkdir(parents=True)
        (home / "demo" / "gamestudio.json").write_text('{"name": "Demo"}', encoding="utf-8")  # an older copy
        with mock.patch.object(toolchain, "bundle_root", lambda *_a: bundle), \
                mock.patch.object(projects, "examples_home", lambda: home):
            self.assertEqual(projects.prepare_example(example), home / "demo")
        self.assertTrue(trust.is_trusted(home / "demo"))

    def test_an_unreadable_record_does_not_migrate_again(self):
        self.record.parent.mkdir(parents=True, exist_ok=True)
        self.record.write_text("{not json", encoding="utf-8")
        self.assertFalse(trust.migrate([str(self.root)]), "the recent list may hold projects nobody trusted")
        self.assertFalse(trust.is_trusted(self.root))

    def test_a_record_that_cannot_be_written_still_migrates_for_the_session(self):
        with mock.patch.object(trust, "_write", lambda _data: False):
            self.assertTrue(trust.migrate([str(self.root)]))
        self.assertTrue(trust.is_trusted(self.root))

    def test_projects_opened_without_trusting_them_are_not_migrated(self):
        self.patch(mock.patch.object(projects, "_recent_path", lambda: self.tmp / "recent.json"))
        old = self.game("old")
        (self.tmp / "recent.json").write_text(json.dumps([{"path": str(old), "name": "old", "opened": 0}]),
                                              encoding="utf-8")  # the list an earlier version kept
        projects.remember(self.root)  # opened with Not now
        self.assertEqual([bool(e.get("untrusted")) for e in projects.recent()], [True, False])
        with mock.patch.object(projects, "CLASSES", ()):
            projects.register()
        self.assertTrue(trust.is_trusted(old))
        self.assertFalse(trust.is_trusted(self.root))

    def test_register_migrates_the_recent_list(self):
        old = self.game("old")
        with mock.patch.object(projects, "recent", lambda: [{"path": str(old), "name": "old", "opened": 0}]), \
                mock.patch.object(projects, "CLASSES", ()):
            projects.register()  # its load handler is there already
        self.assertTrue(trust.is_trusted(old))


class BlenderAutoRunTests(_Trust):
    """Blender's Auto Run Python Scripts and its excluded paths (Preferences > Save & Load), read for real."""

    def setUp(self):
        super().setUp()
        filepaths = bpy.context.preferences.filepaths
        self.addCleanup(setattr, filepaths, "use_scripts_auto_execute", filepaths.use_scripts_auto_execute)
        self.added = []
        self.addCleanup(self.include_all)

    def exclude(self, path, glob=False):
        item = bpy.context.preferences.autoexec_paths.new()
        item.path, item.use_glob = path, glob
        self.added.append(path)

    def include_all(self):
        paths = bpy.context.preferences.autoexec_paths
        for item in [item for item in paths if item.path in self.added]:
            paths.remove(item)
        self.added.clear()

    def allows(self) -> bool:
        REAL_REFRESH()
        return trust.is_trusted(self.root)

    def test_auto_run_on_trusts_and_an_excluded_path_does_not(self):
        filepaths = bpy.context.preferences.filepaths
        filepaths.use_scripts_auto_execute = False
        self.assertFalse(self.allows())
        filepaths.use_scripts_auto_execute = True
        self.assertTrue(self.allows())
        for path, glob in ((str(self.tmp) + os.sep, False),             # the folder the project is in
                           (str(self.tmp).upper()[:8], False),           # a prefix, ignoring case (as Blender)
                           (str(self.root / "scenes") + os.sep, False),  # inside it: its scenes would not run
                           (f"*{os.sep}game{os.sep}*", True)):
            with self.subTest(path=path, glob=glob):
                self.exclude(path, glob)
                self.assertFalse(self.allows())
                self.include_all()
        self.exclude(str(self.tmp / "elsewhere") + os.sep)
        self.exclude(f"*{os.sep}other{os.sep}*", glob=True)
        self.assertTrue(self.allows())
        self.assertIn((str(self.tmp / "elsewhere") + os.sep, False), trust._blender[1], "Blender's own list")

    def test_a_glob_is_matched_against_the_blend_files(self):
        bpy.context.preferences.filepaths.use_scripts_auto_execute = True
        self.exclude(f"*{os.sep}scenes{os.sep}*.blend", glob=True)  # Blender matches the .blend's own path
        self.assertTrue(self.allows(), "no .blend there yet")
        (self.root / "scenes").mkdir()
        (self.root / "scenes" / "main.blend").write_bytes(b"")
        self.assertFalse(self.allows())

    def test_the_command_line_wins(self):
        with mock.patch.object(trust, "_command_line", lambda: True):
            self.assertTrue(trust.blender_allows(self.root), "-y: scripts run everywhere, as in Blender")
        bpy.context.preferences.filepaths.use_scripts_auto_execute = True
        REAL_REFRESH()
        with mock.patch.object(trust, "_command_line", lambda: False):
            self.assertFalse(trust.blender_allows(self.root), "-Y: never")
        for argv, expected in ((["blender", "-b", "-y", "-Y", "--python", "x.py", "--", "-y"], False),
                               (["blender", "--enable-autoexec", "game.blend"], True), (["blender", "-b"], None)):
            with mock.patch.object(sys, "argv", argv):
                self.assertIs(REAL_COMMAND_LINE(), expected, argv)


# --- The gates ----------------------------------------------------------------------------------------


class LoadTests(_Trust):
    """Loading or opening a project someone else made runs nothing of it; GameStudio asks once."""

    def setUp(self):
        super().setUp()
        self.runs, self.asked = [], []
        self.patch(mock.patch.object(environment, "_run", lambda root, purpose, needs, then: self.runs.append(
            (purpose, [n.key for n in needs]))))
        self.patch(mock.patch.object(environment, "_check", lambda root, purpose: [
            environment.Need("registry", "reads its components", 0)] if root is not None else []))
        self.patch(mock.patch.object(environment, "ask_trust", self._fake_ask))

    def _fake_ask(self, root, then=None):
        environment._trusting = {"root": pathlib.Path(root), "then": [then] if then else [], "since": 1e12}
        self.asked.append(pathlib.Path(root))

    def can_ask(self, yes: bool):
        self.patch(mock.patch.object(environment, "_can_ask", lambda: yes))

    def test_nobody_to_ask_no_npm_and_a_message_to_pass_on(self):
        self.can_ask(False)
        self.assertEqual(environment.ensure_ready(self.root, "load"), environment.READY)
        self.assertEqual(self.runs, [], "no `npm run registry`")
        problem = environment.ensure_ready(self.root, "play")
        self.assertEqual(problem, trust.ASK_USER)
        self.assertIn("Ask the user to choose GameStudio > Trust This Project", problem)
        self.assertNotIn("npm", problem)
        self.assertEqual(self.asked, [])

    def test_an_agents_call_does_not_ask(self):
        self.can_ask(True)
        with mock.patch.object(api, "calls", 1):
            self.assertEqual(environment.ensure_ready(self.root, "play"), trust.ASK_USER)
        self.assertEqual(self.asked, [])
        seen = []
        with mock.patch.dict(api.METHODS, {"gs_test_probe": api.Method("gs_test_probe", lambda p: seen.append(
                environment.can_ask_trust()) or {})}), mock.patch.object(api, "call_hook", None):
            api.dispatch("gs_test_probe", {})
        self.assertEqual((seen, api.calls), ([False], 0), "a bridge call counts while it runs")

    def test_asked_once_not_now_then_trust_later(self):
        self.can_ask(True)
        done = []
        self.assertEqual(environment.ensure_ready(self.root, "load"), environment.ASKING)
        self.assertEqual(environment.ensure_ready(self.root, "play", then=lambda ok, m: done.append((ok, m))),
                         environment.ASKING, "one question; Play waits for it")
        self.assertEqual(len(self.asked), 1)
        environment.answer_trust(False)
        self.assertEqual(done, [(False, trust.ASK)])
        self.assertEqual(self.runs, [], "Not now: nothing of the project runs")
        self.assertFalse(trust.is_trusted(self.root))
        self.assertEqual(environment.ensure_ready(self.root, "load"), environment.READY, "not asked again")
        self.assertEqual(environment.ensure_ready(self.root, "play"), "Trust this project first: GameStudio > "
                                                                      "Trust This Project")
        self.assertEqual(environment.require_trust(self.root), trust.ASK)
        self.assertEqual(len(self.asked), 1)

        project.set_project_root_override(self.root)
        self.addCleanup(project.set_project_root_override, None)
        layout = _Recorder()
        environment.draw_menu_status(layout, self.root)
        self.assertIn("gamestudio.trust_project", layout.operators, "GameStudio > Trust This Project")
        self.assertEqual(bpy.ops.gamestudio.trust_project(), {"FINISHED"})
        self.assertTrue(trust.is_trusted(self.root))
        self.assertEqual(self.runs[-1], ("open", ["registry"]), "what waited for it runs now")
        layout = _Recorder()
        environment.draw_menu_status(layout, self.root)
        self.assertNotIn("gamestudio.trust_project", layout.operators)

    def test_trust_goes_on_with_what_waited(self):
        self.can_ask(True)
        self.assertEqual(environment.ensure_ready(self.root, "open"), environment.ASKING)
        environment.answer_trust(True)
        self.assertTrue(trust.is_trusted(self.root))
        self.assertEqual(self.runs, [("open", ["registry"])])
        self.forget_memory()
        self.assertTrue(trust.is_trusted(self.root), "remembered")

    def test_the_question(self):
        layout = _Recorder()
        environment.draw_trust(layout, self.root)
        text = " ".join(layout.texts)
        self.assertIn(str(self.root), layout.texts)
        for words in ("run its scripts and packages", "its npm scripts, its tools and the game's dev server",
                      "Trust only projects from people you trust", "Trust This Project"):
            self.assertIn(words, text)

    def test_links_need_no_trust(self):
        self.can_ask(False)
        self.patch(mock.patch.object(environment, "_check", lambda root, purpose: [
            environment.Need("links", "links the project to them", 0)]))
        self.assertEqual(environment.ensure_ready(self.root, "load"), environment.STARTED)
        self.assertEqual(self.runs, [("load", ["links"])])

    def test_the_setup_steps_run_no_npm_for_it(self):
        calls = []

        class Job:
            warnings = []

            def set(self, *_a):
                pass

            def run(self, argv, *_a):
                calls.append(argv)

            def note(self, text):
                calls.append(text)

        root = self.game("deps", dependencies={"left-pad": "1.3.0"})
        with mock.patch.object(toolchain, "sdk", lambda *_a: None):
            environment._step_project(root, "npm", True, False)(Job())
        environment._registry(Job(), root, "npm", self.tmp / "setup.log", strict=False)
        with self.assertRaisesRegex(environment.SetupError, "Trust This Project"):
            environment._registry(Job(), root, "npm", self.tmp / "setup.log", strict=True)
        self.assertEqual(len(calls), 2)
        self.assertTrue(all(isinstance(c, str) and "not trusted" in c for c in calls), calls)

    def test_the_load_handler_runs_nothing(self):
        self.can_ask(False)
        project.set_project_root_override(self.root)
        self.addCleanup(project.set_project_root_override, None)
        with mock.patch.object(environment, "check_later", lambda root, purpose="load": environment.ensure_ready(
                root, purpose)), mock.patch.object(projects, "remember", lambda root: None), \
                mock.patch.object(environment.subprocess, "Popen", side_effect=AssertionError("ran a command")):
            projects._on_load_post()
        self.assertEqual(self.runs, [])


class EntryPointTests(_Trust):
    """Every way into the project's code refuses for an untrusted project, with nobody to ask."""

    def setUp(self):
        super().setUp()
        self.patch(mock.patch.object(environment, "_can_ask", lambda: False))

    def test_setup_and_resume_refuse(self):
        started = []
        self.assertEqual(environment.start("Setting up", self.root), trust.ASK_USER)
        self.assertEqual(environment.setup_project(self.root), trust.ASK_USER)
        (self.root / ".gamestudio" / projects.SETUP_PENDING).write_text('{"name": "game"}', encoding="utf-8")
        with mock.patch.object(environment, "start", lambda *a, **k: started.append(a) or ""):
            self.assertEqual(projects.resume_setup(self.root), trust.ASK_USER)
            self.assertEqual(projects.create_project(self.root, "game"), trust.ASK_USER, "New Project on it resumes")
        self.assertEqual(started, [])

    def test_resume_asks_then_goes_on(self):
        self.patch(mock.patch.object(environment, "_can_ask", lambda: True))
        asked, started = [], []
        self.patch(mock.patch.object(environment, "ask_trust", lambda root, then=None: asked.append(then)))
        (self.root / ".gamestudio" / projects.SETUP_PENDING).write_text('{"name": "game"}', encoding="utf-8")
        with mock.patch.object(environment, "start", lambda *a, **k: started.append(a) or ""), \
                mock.patch.object(environment, "online", lambda: True):
            self.assertEqual(projects.resume_setup(self.root), "", "it asks first")
            self.assertEqual(started, [])
            trust.trust(self.root)
            asked[0](True, "")
        self.assertEqual(len(started), 1)

    def test_its_scripts_do_not_run(self):
        (self.root / "tools").mkdir()
        marker = self.tmp / "ran.txt"
        script = f"open({str(marker)!r}, 'w').write('ran')\ndef build(gs, root):\n    return root\n"
        for rel in (projects.LEVEL_SCRIPT, projects.UI_SCRIPT):
            (self.root / rel).write_text(script, encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "not trusted"):
            projects.build_level(self.root)
        with self.assertRaisesRegex(ValueError, "not trusted"):
            projects.build_ui(self.root)
        self.assertFalse(marker.exists())

    def test_open_with_a_ready_note_refuses_then_asks(self):
        """A folder whose note says New Project's setup is done ("ready") opens straight into its level and
        UI scripts: a planted note must not skip the question."""
        (self.root / ".gamestudio" / projects.SETUP_PENDING).write_text('{"name": "game", "ready": true}',
                                                                       encoding="utf-8")
        opened = []
        self.patch(mock.patch.object(projects, "open_new_project", lambda root: opened.append(root)))
        with self.assertRaises(RuntimeError) as cm:  # an ERROR report raises in the background
            bpy.ops.gamestudio.project_open(path=str(self.root))
        self.assertIn("Trust This Project", str(cm.exception))
        self.assertEqual(opened, [])

        self.patch(mock.patch.object(environment, "_can_ask", lambda: True))
        asked = []
        self.patch(mock.patch.object(environment, "ask_trust", lambda root, then=None: asked.append(then)))
        self.assertEqual(bpy.ops.gamestudio.project_open(path=str(self.root)), {"FINISHED"})
        self.assertEqual((opened, len(asked)), ([], 1), "it asks first")
        trust.trust(self.root)
        asked[0](True, "")
        self.assertEqual(opened, [self.root])

    def test_dev_server_build_and_typecheck_refuse(self):
        (self.root / "node_modules").mkdir()
        with mock.patch.object(devserver.subprocess, "Popen", side_effect=AssertionError("ran npm run dev")):
            with self.assertRaisesRegex(devserver.DevServerError, "Trust This Project"):
                devserver.start(self.root)
        project.set_project_root_override(self.root)
        self.addCleanup(project.set_project_root_override, None)
        from gamestudio.editors import project as project_editor

        with mock.patch.object(project_editor.BUILD, "start", side_effect=AssertionError("ran npm run build")):
            with self.assertRaises(RuntimeError) as cm:  # an ERROR report raises in the background
                bpy.ops.gamestudio.build_web()
        self.assertIn("Trust This Project", str(cm.exception))
        with mock.patch.object(tools.subprocess, "run", side_effect=AssertionError("ran npm run typecheck")):
            with self.assertRaisesRegex(tools.ToolError, "Trust This Project"):
                tools.run_typecheck({}, tools.ToolContext("mcp", self.root))

    def test_the_stream_host_and_the_compressor_refuse(self):
        with self.assertRaisesRegex(stream.StreamError, "Trust This Project"):
            stream.start("http://127.0.0.1:5173/", 64, 64, project_root=self.root, node=sys.executable)
        with mock.patch.object(compression, "settings", lambda root=None: {"geometry": "meshopt"}):
            self.assertIn("Trust This Project", compression.measure(self.root, [])["error"])


class TemplateTests(_Trust):
    """Templates must not be a way around the question: one saved from an untrusted project, or dropped into
    the templates folder by someone else, does not make the projects made from it trusted."""

    def setUp(self):
        super().setUp()
        self.patch(mock.patch.object(projects, "user_templates_dir", lambda: self.tmp / "Templates"))

    def template(self, name, base=None) -> pathlib.Path:
        folder = self.tmp / "Templates" / name
        folder.mkdir(parents=True)
        meta = {"label": name.title(), **({"base": base} if base else {})}
        (folder / projects.TEMPLATE_INFO).write_text(json.dumps(meta), encoding="utf-8")
        (folder / "gamestudio.json").write_text(json.dumps({"name": name}), encoding="utf-8")
        (folder / "package.json").write_text('{"name": "game", "dependencies": {}}', encoding="utf-8")
        return folder

    def test_projects_from_an_untrusted_template_are_not_trusted(self):
        shared = self.template("shared")
        built_on = self.template("built-on", base="shared")
        self.assertEqual(projects.untrusted_templates("starter"), [], "built-in")
        self.assertEqual(projects.untrusted_templates("built-on"), [built_on, shared], "and what it is built on")
        made = self.tmp / "made"
        made.mkdir()
        projects.scaffold(made, "Made", "shared")
        self.assertFalse(trust.is_trusted(made))
        trust.trust(built_on, "template")
        self.assertEqual(projects.untrusted_templates("built-on"), [shared])

    def test_new_project_from_it_asks_once(self):
        shared = self.template("shared")
        started, asked = [], []
        self.patch(mock.patch.object(projects, "_start_setup", lambda root, name: started.append(root) or ""))
        self.patch(mock.patch.object(projects, "offline_problem", lambda *a, **k: ""))
        self.patch(mock.patch.object(environment, "_can_ask", lambda: True))
        self.patch(mock.patch.object(environment, "ask_trust", lambda root, then=None: asked.append(then)))
        first, second = self.tmp / "first", self.tmp / "second"
        self.assertEqual(projects.create_project(first, "First", "shared"), "")
        self.assertEqual((started, len(asked)), ([], 1), "it asks before its setup runs")
        trust.trust(first)  # what Trust does, then what waited goes on
        asked[0](True, "")
        self.assertEqual(started, [first])
        self.assertTrue(trust.recorded(shared), "trusting the project trusts its template")
        self.assertEqual(projects.create_project(second, "Second", "shared"), "")
        self.assertEqual((started, len(asked)), ([first, second], 1), "the next project does not ask")

    def test_save_as_template_needs_a_trusted_project_and_trusts_the_template(self):
        with self.assertRaisesRegex(projects.TemplateError, "not trusted"):
            projects.save_as_template(self.root, "from-theirs")
        self.assertFalse((self.tmp / "Templates" / "from-theirs").exists())
        made = self.tmp / "made"
        made.mkdir()
        projects.scaffold(made, "Made Here")
        result = projects.save_as_template(made, "from-mine")
        self.assertTrue(trust.recorded(result["path"]))
        self.assertEqual(projects.untrusted_templates("from-mine"), [])


class OpenWithSystemTests(unittest.TestCase):
    """A path from the project or a game's log is never run by "opening" it with its default application."""

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix="gs_open_", dir=tmp_base()))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.planted = self.tmp / "glow.js"
        self.planted.write_text("WScript.Echo('ran')", encoding="utf-8")

    def test_which_files_run(self):
        for name in ("x.exe", "x.BAT", "x.cmd", "x.js", "x.vbs", "x.hta", "x.lnk", "x.command", "x.app", "x.sh"):
            self.assertTrue(code_editor.runs_when_opened(self.tmp / name), name)
        for name in ("x.ts", "x.png", "x.json", "x.blend", "x.txt"):
            self.assertFalse(code_editor.runs_when_opened(self.tmp / name), name)

    def test_the_system_opener_shows_its_folder(self):
        with mock.patch.object(code_editor.os, "startfile", create=True) as startfile, \
                mock.patch.object(code_editor.subprocess, "Popen") as popen:
            code_editor._open_with_system(self.planted)
        opened = startfile.call_args[0][0] if WINDOWS else popen.call_args[0][0][1]
        self.assertEqual(pathlib.Path(opened), self.tmp)

    def test_open_path_shows_its_folder(self):
        from gamestudio import ops as gs_ops

        planted = self.tmp / "x.bat"
        planted.write_text("@echo ran", encoding="utf-8")
        opened = []
        wm = types.SimpleNamespace(path_open=lambda filepath: opened.append(filepath) or {"FINISHED"})
        with mock.patch.object(gs_ops, "bpy", types.SimpleNamespace(ops=types.SimpleNamespace(wm=wm))):
            bpy.ops.gamestudio.open_path(filepath=str(planted))
        self.assertEqual([pathlib.Path(p) for p in opened], [self.tmp])


# --- Package names, the game's address, programs ------------------------------------------------------


class PackageNameTests(_Trust):
    def test_npm_names(self):
        for name in ("three", "@gamestudio/engine", "@types/node", "lodash.merge", "JSONStream", "left-pad",
                     "@scope/name-1.2_x~"):
            self.assertTrue(toolchain.valid_package_name(name), name)
        for name in ("../evil", "@gamestudio/../../evil", "@scope/../x", "..", ".hidden", "_private", "/abs/path",
                     "C:\\Windows", "C:evil", "a\\b", "a/b/c", "@scope/", "@/x", "", "x" * 215, "with space", None):
            self.assertFalse(toolchain.valid_package_name(name), name)

    def test_invalid_names_are_skipped_and_never_linked(self):
        shared = self.tmp / "shared"
        modules, packages = shared / "node_modules", shared / "runtime" / "packages"
        for folder, name in ((modules / "three", "three"), (packages / "engine", "@gamestudio/engine")):
            folder.mkdir(parents=True)
            (folder / "package.json").write_text(json.dumps({"name": name, "version": "1.0.0"}), encoding="utf-8")
        deps = {"three": "*", "../../outside": "*", "@gamestudio/../../../outside": "*", "..\\..\\outside": "*"}
        root = self.game("linky", dependencies=deps)
        link = root / "node_modules" / "three"
        self.addCleanup(lambda: toolchain.is_link(link) and toolchain._remove_link(link))  # before the folder goes
        self.assertEqual(list(toolchain.declared_dependencies(toolchain.read_manifest(root))), ["three"])
        self.assertEqual(toolchain.missing_dependencies(root), ["three"])
        self.assertEqual(toolchain.link_project(root, toolchain.Sdk(packages, modules))["linked"], ["three"])
        self.assertEqual([p.name for p in (root / "node_modules").iterdir()], ["three"])
        self.assertFalse(os.path.lexists(self.tmp / "outside"), "nothing made outside node_modules")


class GameAddressTests(unittest.TestCase):
    def test_only_the_dev_server_on_this_computer(self):
        for url in ("http://localhost:5173/", "http://127.0.0.1:5173/?gs=1", "https://localhost/",
                    "http://[::1]:5173/", "HTTP://LOCALHOST:5173/"):
            self.assertTrue(devserver.local_url(url), url)
        for url in ("file:///C:/Windows/System32/calc.exe", "C:\\Windows\\System32\\calc.exe", "calc",
                    "http://example.com/", "http://localhost.example.com/", "http://127.0.0.1@example.com/",
                    "ms-settings:", "javascript:alert(1)", "http://localhost:notaport/", "", None):
            self.assertFalse(devserver.local_url(url), url)

    def test_a_planted_address_is_not_opened(self):
        with mock.patch.object(devserver.webbrowser, "open") as opened, \
                mock.patch.object(devserver.subprocess, "Popen") as started:
            self.assertEqual(devserver.open_game_window("file:///C:/Windows/System32/calc.exe"), "")
        opened.assert_not_called()
        started.assert_not_called()


class ProgramLookupTests(unittest.TestCase):
    @unittest.skipUnless(WINDOWS, "Windows looks in the current folder first")
    def test_not_from_the_current_folder(self):
        folder = pathlib.Path(tempfile.mkdtemp(prefix="gs_planted_", dir=tmp_base()))
        self.addCleanup(shutil.rmtree, folder, True)
        (folder / "gs-planted-tool.cmd").write_text("@echo planted\r\n", encoding="utf-8")
        here = os.getcwd()
        os.chdir(folder)
        self.addCleanup(os.chdir, here)
        if not os.environ.get("NoDefaultCurrentDirectoryInExePath"):
            self.assertIsNotNone(shutil.which("gs-planted-tool"), "the case this guards against")
        self.assertIsNone(toolchain.which("gs-planted-tool"))
        self.assertIsNone(toolchain.which("gs-planted-tool", path=f".{os.pathsep}{folder.name}"), "relative folders")
        self.assertEqual(toolchain.which("gs-planted-tool", path=str(folder)).lower(),
                         str(folder / "gs-planted-tool.cmd").lower(), "a folder PATH names is searched")

    def test_windows_programs_by_their_path(self):
        self.assertEqual(toolchain.system_tool("taskkill.exe", {"SystemRoot": "D:\\Win"}),
                         "D:\\Win\\System32\\taskkill.exe")
        self.assertEqual(toolchain.system_tool("cmd.exe", {}), "C:\\Windows\\System32\\cmd.exe")


class _Recorder:
    """A UILayout stand-in: labels and operator ids."""

    def __init__(self):
        self.texts, self.operators = [], []

    def __getattr__(self, name):
        return lambda *_a, **_k: self

    def __setattr__(self, name, value):
        object.__setattr__(self, name, value)

    def label(self, text="", **_kwargs):
        self.texts.append(text)

    def operator(self, idname, **_kwargs):
        self.operators.append(idname)
        return self


if __name__ == "__main__":
    unittest.main()
