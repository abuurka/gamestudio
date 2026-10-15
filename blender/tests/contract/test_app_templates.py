# SPDX-License-Identifier: GPL-3.0-or-later
"""App-template behaviour the GameStudio IDE shell depends on.

The shell *is* an app template: `--app-template GameStudio` must load its startup.blend
(workspaces), its userpref.blend (theme, enabled extension) and call its __init__.register()
(UI overrides). The template's .blend files are generated from Blender's factory startup by
editor/shell/tools/build_template.py.
"""

import shutil
import unittest

import bpy

import contract_support as cs

PROBE_TEMPLATE = "GSContractProbe"
FALLBACK_KEEP = ("Layout", "Modeling", "Shading", "Animation", "Geometry Nodes", "Scripting")


def _template_keep():
    """Factory workspace names build_template.py keeps (its `KEEP` dict), read from the source."""
    if cs.TEMPLATE_BUILDER.is_file():
        return tuple(cs.module_constant(cs.TEMPLATE_BUILDER, "KEEP"))
    return FALLBACK_KEEP


class TemplateLoadingTest(unittest.TestCase):
    """Build a throw-away template in one Blender process, then start another with it."""

    @classmethod
    def setUpClass(cls):
        cls.root = cs.make_tmp_dir("gs_contract_template_")
        scripts = cls.root / "scripts"
        template = scripts / "startup" / "bl_app_templates_system" / PROBE_TEMPLATE
        marker = cls.root / "registered.json"
        cls.built = cs.run_probe("probe_build_template", template)
        cls.session = cs.run_probe("probe_session", app_template=PROBE_TEMPLATE,
                                   env={"BLENDER_SYSTEM_SCRIPTS": scripts,
                                        "GS_CONTRACT_TEMPLATE_MARKER": marker})
        cls.marker = marker.read_text(encoding="utf-8") if marker.is_file() else None

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.root, ignore_errors=True)

    def test_template_is_selected(self):
        """`--app-template NAME` finds NAME under BLENDER_SYSTEM_SCRIPTS and makes it active.

        Breaks if changed: `gsfork run`/`gsfork test` and the stock-Blender fallback, which load
        the shell from editor/shell/scripts through BLENDER_SYSTEM_SCRIPTS with --app-template.
        """
        self.assertEqual(self.session["app_template"], PROBE_TEMPLATE)

    def test_template_startup_blend_loads(self):
        """With a template active, its startup.blend replaces the factory startup file.

        Breaks if changed: the IDE workspaces (Level, Model, Materials, Animate, Procedural, Debug)
        come only from the template's generated startup.blend.
        """
        self.assertIn("GSContractWorkspace", self.session["workspaces"])
        self.assertNotIn("Layout", self.session["workspaces"])
        self.assertIn("GSContractMarker", self.session["objects"])

    def test_template_userpref_blend_loads(self):
        """With a template active, its userpref.blend is applied (here: ui_scale), even with --factory-startup.

        Breaks if changed: the GameStudio theme and, above all, enabling the gamestudio system
        extension, which is stored in the template's userpref.blend.
        """
        self.assertAlmostEqual(self.session["ui_scale"], 1.25, places=3)

    def test_template_register_runs(self):
        """The template's __init__.register() runs (with bpy.data restricted).

        Breaks if changed: every UI override and menu addition of the IDE shell is installed by
        GameStudio/__init__.py register().
        """
        self.assertIsNotNone(self.marker, "the template's register() did not run")
        self.assertIn('"registered": true', self.marker)
        self.assertIn(f"bl_app_templates_system.{PROBE_TEMPLATE}", self.session["modules"])


class FactoryStartupTest(unittest.TestCase):
    def setUp(self):
        cs.factory_reset()

    def tearDown(self):
        cs.factory_reset()

    def test_factory_workspaces_for_template_builder(self):
        """The factory startup file still has the workspaces build_template.py renames.

        Breaks if changed: editor/shell/tools/build_template.py `KEEP` (`gsfork template` stops with
        "factory workspace '...' is missing" and the IDE ships without regenerated workspaces).
        """
        keep = _template_keep()
        names = {ws.name for ws in bpy.data.workspaces}
        missing = sorted(set(keep) - names)
        self.assertFalse(missing, f"factory workspaces missing: {missing}")

    def test_batch_remove_deletes_workspaces_in_background(self):
        """`bpy.data.batch_remove(ids=[workspaces])` deletes workspaces immediately in --background.

        Breaks if changed: build_template.py `build_workspaces()`; `bpy.ops.workspace.delete` defers
        to the event loop, which never runs headless, so the template would keep all 11 factory
        workspaces.
        """
        keep = set(_template_keep()) & {ws.name for ws in bpy.data.workspaces}
        unwanted = [ws for ws in bpy.data.workspaces if ws.name not in keep]
        self.assertTrue(keep and unwanted)
        bpy.data.batch_remove(ids=unwanted)
        self.assertEqual({ws.name for ws in bpy.data.workspaces}, keep)
