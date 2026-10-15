# SPDX-License-Identifier: GPL-3.0-or-later
"""Opening a project never throws away unsaved changes without asking (projects.py). Clicked in the UI,
Open Project, Open Recent and Examples let Blender ask first (Save, Don't Save, Cancel). A New Project
whose setup ends while the open file has unsaved changes waits: the user opens it when ready (GameStudio >
Open <name>, or Open Project on it after a restart), and the rest of New Project then runs, once.

A --background Blender shows no dialogs, so these tests check what the operators decide, with the
question (projects._open_asking, projects.ask_to_open) replaced by a recorder. Unsaved changes are the
bridge's record (api.agent_dirty), which a --background session goes by.
"""

import json
import os
import pathlib
import shutil
import tempfile
import types
import unittest
from unittest import mock

import bpy

from gamestudio import environment, launcher, projects
from gamestudio.bridge import api
from gamestudio.core import project, registry

import helpers
from helpers import tmp_base
from test_ui import MockLayout


class Invoked(types.SimpleNamespace):
    """An operator as a click in the UI runs it (options.is_invoke): its class's invoke() and execute() run
    on this, which records the reports."""

    def __init__(self, cls, **props):
        super().__init__(options=types.SimpleNamespace(is_invoke=True), reports=[], **props)
        self._cls = cls

    def report(self, kind, text):
        self.reports.append((sorted(kind)[0], text))

    def invoke(self, context, event=None):
        return self._cls.invoke(self, context, event)

    def execute(self, context):
        return self._cls.execute(self, context)


class UnsavedChangesCase(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix="gs_unsaved_", dir=tmp_base()))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.addCleanup(registry.reload_registry, None)
        self.addCleanup(project.invalidate_cache)
        self.addCleanup(helpers.reset_scene)
        self.addCleanup(setattr, api, "agent_dirty", False)
        self.addCleanup(projects._waiting.clear)
        helpers.reset_scene()

    def unsaved_work(self):
        """An untitled file with an object the user made and did not save."""
        bpy.ops.wm.read_homefile(use_empty=True)
        helpers.new_object("Mine")
        api.agent_dirty = True  # loading cleared it


class OpenProjectTests(UnsavedChangesCase):
    """Open Project, Open Recent and Examples (gamestudio.project_open with a path, or the file browser)."""

    def setUp(self):
        super().setUp()
        self.root = (self.tmp / "game").resolve()
        self.level = self.root / "scenes" / "main.blend"
        self.level.parent.mkdir(parents=True)
        (self.root / "gamestudio.json").write_text(json.dumps({"name": "Game"}), encoding="utf-8")
        bpy.ops.wm.read_homefile(use_empty=True)
        helpers.new_object("Level")
        bpy.ops.wm.save_as_mainfile(filepath=str(self.level))
        self.checks = []
        patch = mock.patch.object(environment, "ensure_ready",
                                  lambda root, purpose, *a, **k: self.checks.append(purpose) or environment.READY)
        patch.start()
        self.addCleanup(patch.stop)

    def asking(self, asked, answer=("INTERFACE",)):
        """Blender's question, recorded: it is up (INTERFACE) until the user answers."""
        return mock.patch.object(projects, "_open_asking", lambda path: asked.append(pathlib.Path(path)) or set(answer))

    def test_a_click_asks_first_when_the_file_has_unsaved_changes(self):
        self.unsaved_work()
        asked = []
        op = Invoked(projects.GAMESTUDIO_OT_project_open, path=str(self.root), directory="")
        with self.asking(asked):
            self.assertEqual(op.invoke(bpy.context), {"FINISHED"})
        self.assertEqual(asked, [self.level], "Blender's own question: Save, Don't Save, Cancel")
        self.assertEqual(bpy.data.filepath, "", "nothing opened yet")
        self.assertIn("Mine", bpy.data.objects)
        self.assertEqual(self.checks, [], "the load checks the setup once the file opens (_on_load_post)")
        # Blender opened it at once (its Save Prompt preference is off): the check runs as before.
        with self.asking(asked, ("FINISHED",)):
            self.assertEqual(op.invoke(bpy.context), {"FINISHED"})
        self.assertEqual(self.checks, ["open"])

    def test_the_file_browser_asks_too(self):
        self.unsaved_work()
        asked = []
        op = Invoked(projects.GAMESTUDIO_OT_project_open, path="", directory=str(self.level.parent))
        with self.asking(asked):
            self.assertEqual(op.execute(bpy.context), {"FINISHED"})  # as after the folder is picked
        self.assertEqual(asked, [self.level])
        self.assertIn("Mine", bpy.data.objects)

    def test_without_unsaved_changes_a_click_opens_at_once(self):
        bpy.ops.wm.read_homefile(use_empty=True)
        self.assertFalse(projects.unsaved_changes())
        op = Invoked(projects.GAMESTUDIO_OT_project_open, path=str(self.root), directory="")
        with mock.patch.object(projects, "_open_asking", side_effect=AssertionError("asked")):
            self.assertEqual(op.invoke(bpy.context), {"FINISHED"})
        self.assertEqual(pathlib.Path(bpy.data.filepath), self.level)
        self.assertIn("Level", bpy.data.objects)
        self.assertEqual(self.checks, ["open"])

    def test_from_python_it_opens_at_once(self):
        self.unsaved_work()
        with mock.patch.object(projects, "_open_asking", side_effect=AssertionError("asked")):
            self.assertEqual(bpy.ops.gamestudio.project_open(path=str(self.root)), {"FINISHED"})
        self.assertEqual(pathlib.Path(bpy.data.filepath), self.level)
        self.assertEqual(self.checks, ["open"])


class NewProjectWaitsTests(UnsavedChangesCase):
    """New Project's setup ends (its on_done) while the open file has unsaved changes: the project waits.
    The setup job is a fake (environment.start), as in test_new_project_setup."""

    def setUp(self):
        super().setUp()
        self.root = (self.tmp / "arena").resolve()
        self.started = []

        def fake_start(title, root=None, **kwargs):
            self.started.append({"title": title, "root": root, **kwargs})
            return ""

        for patch in (mock.patch.object(environment, "start", fake_start),
                      mock.patch.object(environment, "online", lambda: True),
                      mock.patch.dict(os.environ, {projects.USER_TEMPLATES_ENV: str(self.tmp / "Templates")})):
            patch.start()
            self.addCleanup(patch.stop)
        self.assertEqual(projects.create_project(self.root, "Arena", "starter"), "")
        self.setup_done = self.started[-1]["on_done"]
        self.finished = []

    def finishing(self):
        """finish_new_project, recorded: the start scene it would build and open."""
        return mock.patch.object(projects, "finish_new_project",
                                 lambda root: self.finished.append(pathlib.Path(root)) or projects.start_scene(root))

    def test_it_waits_and_opens_once_when_the_user_says_so(self):
        self.unsaved_work()
        with self.finishing():
            self.setup_done(True, "done")
        self.assertEqual(self.finished, [], "the open file stays")
        self.assertIn("Mine", bpy.data.objects)
        self.assertTrue(projects.setup_pending(self.root).get("ready"), "set up: only the start scene waits")
        self.assertEqual(projects.waiting_projects(), [self.root])
        self.assertIn("Project ready: Arena", environment.JOB.message)
        self.assertEqual(pathlib.Path(projects.recent()[0]["path"]), self.root, "Open Recent lists it")
        for draw in (lambda layout: projects.draw_project_actions(layout, bpy.context), launcher.draw_start):
            texts = []
            draw(MockLayout(self, texts))
            self.assertIn("Open Arena", texts, "GameStudio > Open Arena, and in the launcher")

        # A click asks first; so does Open Project on the folder, which does not set it up again.
        asked = []
        with self.finishing(), mock.patch.object(projects, "ask_to_open",
                                                 lambda root, ready=False: asked.append(pathlib.Path(root))):
            op = Invoked(projects.GAMESTUDIO_OT_project_open_new, path=str(self.root), unsaved="ASK")
            self.assertEqual(op.invoke(bpy.context), {"FINISHED"})
            op = Invoked(projects.GAMESTUDIO_OT_project_open, path=str(self.root), directory="")
            self.assertEqual(op.invoke(bpy.context), {"FINISHED"})
        self.assertEqual(asked, [self.root, self.root])
        self.assertEqual((self.finished, len(self.started)), ([], 1))

        # From Python ASK refuses, and so does Save for a file never saved (a click shows Save As);
        # Cancel (Later) leaves everything as it is.
        with self.finishing():
            for unsaved, reason in (("ASK", "unsaved changes"), ("SAVE", "never saved")):
                with self.subTest(unsaved=unsaved), self.assertRaises(RuntimeError) as caught:
                    bpy.ops.gamestudio.project_open_new(path=str(self.root), unsaved=unsaved)
                self.assertIn(reason, str(caught.exception))
            self.assertEqual(bpy.ops.gamestudio.project_open_new(path=str(self.root), unsaved="CANCEL"),
                             {"CANCELLED"})
            self.assertEqual(self.finished, [])
            self.assertIn("Mine", bpy.data.objects)

            # Don't Save: the rest of New Project runs, once, and the project stops waiting.
            self.assertEqual(bpy.ops.gamestudio.project_open_new(path=str(self.root), unsaved="DISCARD"),
                             {"FINISHED"})
            self.assertEqual(self.finished, [self.root])
            self.assertIsNone(projects.setup_pending(self.root))
            self.assertEqual(projects.waiting_projects(), [])
            with self.assertRaises(RuntimeError):  # nothing waits any more
                bpy.ops.gamestudio.project_open_new(path=str(self.root), unsaved="DISCARD")
        self.assertEqual(self.finished, [self.root])

    def test_without_unsaved_changes_it_opens_at_once(self):
        bpy.ops.wm.read_homefile(use_empty=True)
        with self.finishing():
            self.setup_done(True, "done")
        self.assertEqual(self.finished, [self.root])
        self.assertIsNone(projects.setup_pending(self.root))
        self.assertEqual(projects.waiting_projects(), [])
        self.assertIn("Project ready", environment.JOB.message)

    def test_save_saves_the_open_file_first(self):
        mine = self.tmp / "mine.blend"
        bpy.ops.wm.read_homefile(use_empty=True)
        bpy.ops.wm.save_as_mainfile(filepath=str(mine))
        helpers.new_object("Mine")
        api.agent_dirty = True
        with self.finishing():
            self.setup_done(True, "done")
            self.assertEqual(self.finished, [])
            self.assertEqual(bpy.ops.gamestudio.project_open_new(path=str(self.root), unsaved="SAVE"), {"FINISHED"})
        self.assertEqual(self.finished, [self.root])
        bpy.ops.wm.open_mainfile(filepath=str(mine))
        self.assertIn("Mine", bpy.data.objects, "saved first")

    def test_after_a_restart_open_project_opens_it(self):
        self.unsaved_work()
        with self.finishing():
            self.setup_done(True, "done")
        projects._waiting.clear()  # Blender quit before the project was opened
        bpy.ops.wm.read_homefile(use_empty=True)  # the next session's untitled file
        self.assertEqual(pathlib.Path(projects.recent()[0]["path"]), self.root)
        op = Invoked(projects.GAMESTUDIO_OT_project_open, path=projects.recent()[0]["path"], directory="")
        with self.finishing():
            self.assertEqual(op.invoke(bpy.context), {"FINISHED"})
        self.assertEqual(self.finished, [self.root])
        self.assertEqual(len(self.started), 1, "not set up again")
        self.assertIsNone(projects.setup_pending(self.root))

    def test_the_rest_of_new_project_runs_in_order(self):
        """The real finish_new_project, run when the user opens the project: the starter's UI, a fresh
        file for the level (the open file's changes go: Don't Save), the level, the Game workspace."""
        steps = []
        level = projects.start_scene(self.root)
        with mock.patch.object(projects, "build_starter_ui", lambda root: steps.append("ui")), \
                mock.patch.object(projects, "build_starter_scene",
                                  lambda root: steps.append(("level", "Mine" in bpy.data.objects)) or level), \
                mock.patch("gamestudio.workspaces.show_game", lambda: steps.append("game workspace")):
            self.unsaved_work()
            self.setup_done(True, "done")
            self.assertEqual(steps, [])
            self.assertEqual(bpy.ops.gamestudio.project_open_new(path=str(self.root), unsaved="DISCARD"),
                             {"FINISHED"})
        self.assertEqual(steps, ["ui", ("level", False), "game workspace"])
        self.assertIsNone(projects.setup_pending(self.root))

    def test_a_shipped_start_scene_opens_after_dont_save(self):
        level = projects.start_scene(self.root)
        bpy.ops.wm.read_homefile(use_empty=True)
        bpy.context.scene.name = "main"
        helpers.new_object("Level")
        bpy.ops.wm.save_as_mainfile(filepath=str(level))  # as a user template ships it
        helpers.write_registry(self.root, helpers.fixture_registry())  # npm run registry, in New Project
        self.unsaved_work()
        with mock.patch.object(projects, "build_starter_ui", lambda root: None):
            self.setup_done(True, "done")
            self.assertIn("Mine", bpy.data.objects)
            self.assertEqual(bpy.ops.gamestudio.project_open_new(path=str(self.root), unsaved="DISCARD"),
                             {"FINISHED"})
        self.assertEqual(pathlib.Path(bpy.data.filepath), level)
        self.assertIn("Level", bpy.data.objects)
        self.assertTrue((self.root / ".gamestudio" / "export" / "main.scene.json").is_file(), "exported for Play")
        self.assertIsNone(projects.setup_pending(self.root))

    def test_the_question(self):
        for ready, expected in ((True, ["Save changes to Untitled.blend before opening Arena?", "Save", "Don't Save",
                                        "Later"]),
                                (False, ["Untitled.blend", "Save", "Don't Save", "Cancel"])):
            with self.subTest(ready=ready):
                texts = []
                projects._draw_question(MockLayout(self, texts), self.root, ready)
                self.assertEqual(texts, expected)


if __name__ == "__main__":
    unittest.main()
