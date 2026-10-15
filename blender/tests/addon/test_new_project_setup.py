# SPDX-License-Identifier: GPL-3.0-or-later
"""New Project when setting up cannot finish (projects.py): it refuses before writing anything when
setup would download with online access off, and a project whose setup failed is set up again by Open
Project or by New Project on the same folder, keeping its files.

The setup job itself is replaced by a fake (environment.start): these tests check what New Project
asks of it and what it does with the result. GAMESTUDIO_TEMPLATES points at a temporary folder.
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

from gamestudio import environment, projects
from gamestudio.core import project, toolchain

from helpers import tmp_base


class Recorder:
    """A UILayout stand-in: labels, operators (with the properties set on them) and everything else."""

    def __init__(self):
        self.texts, self.operators = [], []

    def __getattr__(self, name):
        return lambda *_args, **_kwargs: self

    def label(self, text="", **_kwargs):
        self.texts.append(text)

    def operator(self, idname, **_kwargs):
        props = types.SimpleNamespace()
        self.operators.append((idname, props))
        return props


class NewProjectSetupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix="gs_new_setup_", dir=tmp_base()))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.root = self.tmp / "arena"
        self.started = []

        def fake_start(title, root=None, **kwargs):
            self.started.append({"title": title, "root": root, **kwargs})
            return ""

        for patch in (mock.patch.object(environment, "start", fake_start),
                      mock.patch.object(environment, "online", lambda: True),
                      mock.patch.dict(os.environ, {projects.USER_TEMPLATES_ENV: str(self.tmp / "Templates")})):
            patch.start()
            self.addCleanup(patch.stop)
        self.assertFalse(environment.JOB.running())

    def offline(self, needed, packages=()):
        return mock.patch.multiple(environment, online=lambda: False, needs_download=lambda root=None: list(needed),
                                   npm_command=lambda: "npm"), \
            mock.patch.object(projects, "template_downloads", lambda template: list(packages))

    def test_offline_refusal_writes_nothing(self):
        cases = ((["node", "runtime"], (), ["Node.js and GameStudio's packages"], "yourself"),
                 (["node"], (), ["Node.js"], "yourself"),
                 ([], ("left-pad",), ["the project's packages (left-pad)"], "yourself"))
        for needed, packages, expected, absent in cases:
            with self.subTest(needed=needed, packages=packages):
                patches = self.offline(needed, packages)
                with patches[0], patches[1]:
                    problem = projects.create_project(self.root, "Arena", "shooter")
                self.assertIn("online access is off", problem)
                self.assertIn("Preferences > System > Network > Allow Online Access", problem)
                for text in expected:
                    self.assertIn(text, problem)
                if absent:
                    self.assertNotIn(absent, problem)
                self.assertFalse(self.root.exists(), "nothing written")
                self.assertEqual(self.started, [])
        patches = self.offline([])  # offline, but nothing to download: it goes ahead
        with patches[0], patches[1]:
            self.assertEqual(projects.create_project(self.root, "Arena", "shooter"), "")
        self.assertEqual(len(self.started), 1)

    def test_a_custom_npm_command_downloads_no_node(self):
        with mock.patch.multiple(environment, online=lambda: False, needs_download=lambda root=None: ["node"],
                                 npm_command=lambda: "pnpm"):
            self.assertEqual(projects.offline_problem(), "")

    def test_template_packages_the_shared_runtime_lacks(self):
        folder = self.tmp / "Templates" / "extra"
        folder.mkdir(parents=True)
        (folder / "template.json").write_text(json.dumps({"label": "Extra", "base": "starter"}), encoding="utf-8")
        starter = json.loads((projects.TEMPLATES / "starter" / "package.json").read_text("utf-8"))
        starter["dependencies"]["gs-no-such-package"] = "1.0.0"
        (folder / "package.json").write_text(json.dumps(starter), encoding="utf-8")
        missing = projects.template_downloads("extra")
        self.assertIn("gs-no-such-package", missing)
        self.assertFalse([name for name in missing if name.startswith(toolchain.SCOPE)])
        self.assertEqual(projects.template_downloads("no-such-template"), [])
        with mock.patch.multiple(environment, online=lambda: False, needs_download=lambda root=None: []):
            self.assertIn("gs-no-such-package", projects.offline_problem(template="extra"))

    def test_a_failed_setup_is_resumed_and_keeps_the_files(self):
        self.assertEqual(projects.create_project(self.root, "Arena", "shooter"), "")
        self.assertEqual(self.started[-1]["title"], "Creating Arena")
        note = projects.setup_pending(self.root)
        self.assertEqual((note["name"], note["template"]), ("Arena", "shooter"))

        environment.JOB.message = "Installing packages (npm install) failed (exit code 1)"
        self.started[-1]["on_done"](False, environment.JOB.message)
        self.assertIn("resumes the setup", environment.JOB.message)
        self.assertIsNotNone(projects.setup_pending(self.root), "still pending")
        mine = self.root / "src" / "mine.ts"
        mine.write_text("export const mine = 1;\n", encoding="utf-8")

        # New Project with the same name sets the project up again instead of "not empty".
        self.assertEqual(projects.create_project(self.root, "Arena", "starter"), "")
        self.assertEqual((len(self.started), self.started[-1]["root"]), (2, self.root))
        self.assertTrue(self.started[-1]["build_registry"])
        self.assertTrue(mine.is_file())
        self.assertEqual(json.loads((self.root / "gamestudio.json").read_text("utf-8"))["template"], "shooter")

        # So does Open Project, which used to stop at the missing start scene.
        self.assertEqual(bpy.ops.gamestudio.project_open(path=str(self.root)), {"FINISHED"})
        self.assertEqual(len(self.started), 3)

        # Still offline: refused with the way forward, nothing removed.
        patches = self.offline(["runtime"])
        with patches[0], patches[1]:
            self.assertIn("Allow Online Access", projects.resume_setup(self.root))
            with self.assertRaises(RuntimeError):  # an ERROR report raises in the background
                bpy.ops.gamestudio.project_open(path=str(self.root))
        self.assertEqual(len(self.started), 3)
        self.assertTrue(mine.is_file())

        # It works this time: the start scene opens and the note goes.
        finished = []
        with mock.patch.object(projects, "finish_new_project", lambda root: finished.append(root) or root):
            self.started[-1]["on_done"](True, "done")
        self.assertEqual(finished, [self.root])
        self.assertIsNone(projects.setup_pending(self.root))
        self.assertIn("Project ready", environment.JOB.message)
        with self.assertRaisesRegex(ValueError, "not empty"):
            projects.create_project(self.root, "Arena", "shooter")

    def test_the_dialog_says_it_resumes_and_offers_the_network_preferences(self):
        self.assertEqual(projects.create_project(self.root, "Arena", "shooter"), "")
        dialog = types.SimpleNamespace(layout=Recorder(), name="Arena", location=str(self.tmp), template="starter")
        projects.GAMESTUDIO_OT_project_new.draw(dialog, bpy.context)
        self.assertTrue(any("setup did not finish" in text for text in dialog.layout.texts), dialog.layout.texts)
        self.assertTrue(any("from Arena Shooter" in text for text in dialog.layout.texts), dialog.layout.texts)

        layout = Recorder()
        needs = [environment.Need("runtime", "the game packages", 60)]
        projects._notes.update(time=0.0)
        with mock.patch.multiple(environment, online=lambda: False, check=lambda root=None, purpose="setup": needs):
            projects.draw_download_notes(layout)
        projects._notes.update(time=0.0)
        self.assertIn("The game packages (about 60 MB)", layout.texts)
        [(idname, props)] = layout.operators
        self.assertEqual((idname, props.section), ("screen.userpref_show", "SYSTEM"))
        sections = bpy.ops.screen.userpref_show.get_rna_type().properties["section"].enum_items.keys()
        self.assertIn("SYSTEM", sections)

    def test_a_project_without_the_note_opens_as_before(self):
        self.root.mkdir()
        projects.scaffold(self.root, "Arena", "starter")
        self.assertIsNone(projects.setup_pending(self.root))
        with self.assertRaises(RuntimeError):  # no start scene, and nothing to resume
            bpy.ops.gamestudio.project_open(path=str(self.root))
        self.assertEqual(self.started, [])
        project.invalidate_cache()


if __name__ == "__main__":
    unittest.main()
