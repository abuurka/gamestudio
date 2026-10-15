# SPDX-License-Identifier: GPL-3.0-or-later
"""IDE shell tests: the GameStudio extension in stock Blender, the way users get it.

tools/test_blender.py runs this suite in the background with --factory-startup, a scratch profile
and blender/gamestudio as a system extension (BLENDER_SYSTEM_EXTENSIONS), without an app template.
The suite enables the extension as a user does in Preferences, then checks what it adds to
Blender's UI.

    python tools/test_blender.py shell

test_shell_ux.py (menus, play shortcuts, status bar, workspaces, launcher, Inspector, Assistant),
test_claude_code.py (Open in Claude Code, the bridge status, the welcome, the preferences),
test_codex.py (Open in Codex, its config.toml and skills, the Coding agent preference),
test_environment.py (Node.js, the shared packages, projects, packaging) and test_editors.py
(custom editors, host.editors) run in the same session. On a GameStudio Editor build
(GS_EDITOR_KIND=fork) the fork-only tests run too.
"""

import importlib
import os
import pathlib
import sys
import tempfile
import unittest

import bpy

sys.dont_write_bytecode = True  # keep __pycache__ out of the source tree
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

EXTENSION = "bl_ext.system.gamestudio"
FACTORY_WORKSPACES = {"Layout", "Modeling", "Sculpting", "UV Editing", "Texture Paint", "Shading", "Animation",
                      "Rendering", "Compositing", "Geometry Nodes", "Scripting"}

if EXTENSION not in bpy.context.preferences.addons:
    bpy.ops.preferences.addon_enable(module=EXTENSION)

# The projects the tests make in temporary folders run their scripts, as New Project's do: their folders are
# trusted for this session only (nothing is written to the trust record, core/trust.py).
for _folder in {tempfile.gettempdir(), os.environ.get("GS_TEST_TMP", "")} - {""}:
    importlib.import_module(f"{EXTENSION}.core.trust").trust(_folder, "tests", save=False)


def _draw_funcs(cls) -> list:
    return list(getattr(cls.draw, "_draw_funcs", []))


class ShellTests(unittest.TestCase):
    def test_no_app_template(self):
        self.assertEqual(bpy.context.preferences.app_template, "")

    def test_extension_is_enabled(self):
        self.assertIn(EXTENSION, bpy.context.preferences.addons.keys())

    def test_blender_workspaces_are_left_alone(self):
        """GameStudio adds its workspaces only when asked (Add Game Workspace), or in a GameStudio project."""
        names = {w.name for w in bpy.data.workspaces}
        self.assertTrue(FACTORY_WORKSPACES <= names, names)
        self.assertNotIn("Game", names)

    def test_blender_ui_is_extended_not_replaced(self):
        import bl_operators.wm as upstream_wm
        import bl_ui.space_statusbar as upstream_statusbar
        import bl_ui.space_topbar as upstream_topbar

        for name, module in (("TOPBAR_MT_editor_menus", upstream_topbar), ("TOPBAR_HT_upper_bar", upstream_topbar),
                             ("STATUSBAR_HT_header", upstream_statusbar), ("WM_MT_splash", upstream_wm),
                             ("WM_MT_splash_about", upstream_wm), ("WM_MT_splash_quick_setup", upstream_wm)):
            self.assertIs(getattr(bpy.types, name), getattr(module, name), name)
        menus = importlib.import_module(f"{EXTENSION}.menus")
        topbar = importlib.import_module(f"{EXTENSION}.ui_topbar")
        ui = importlib.import_module(f"{EXTENSION}.ui")
        self.assertIn(menus.draw_editor_menus, _draw_funcs(bpy.types.TOPBAR_MT_editor_menus))
        self.assertEqual(_draw_funcs(bpy.types.TOPBAR_HT_upper_bar)[0], topbar.draw_upper_bar_right, "prepended")
        self.assertEqual(_draw_funcs(bpy.types.TOPBAR_HT_upper_bar)[-1], topbar.draw_upper_bar_left, "appended")
        self.assertIn(ui.draw_statusbar, _draw_funcs(bpy.types.STATUSBAR_HT_header))

    def test_blender_preferences_are_left_alone(self):
        prefs = bpy.context.preferences
        self.assertTrue(prefs.filepaths.use_load_ui)
        self.assertTrue(prefs.view.show_splash)

    def test_operators(self):
        for op in ("project_new", "project_open", "project_reveal", "play", "stop", "pause", "step", "launcher",
                   "add_workspaces", "install_node", "setup_project", "setup_cancel"):
            self.assertTrue(hasattr(bpy.ops.gamestudio, op), op)

    def test_host_reports_the_right_mode(self):
        host = importlib.import_module(f"{EXTENSION}.host")
        info = host.info()
        expect_native = os.environ.get("GS_EDITOR_KIND") == "fork"
        self.assertEqual(info["native"], expect_native, info)
        self.assertEqual(info["blender"][:2], (5, 2))
        if not expect_native:
            self.assertEqual(info["product"], "GameStudio for Blender")
            self.assertEqual(info["app_template"], "")

    def test_user_profile_is_isolated(self):
        config = bpy.utils.user_resource("CONFIG")
        self.assertNotIn(os.path.join("Blender Foundation", "Blender"), config,
                         "the tests must never use the real Blender profile")

    def test_user_folder_is_the_extensions(self):
        environment = importlib.import_module(f"{EXTENSION}.environment")
        toolchain = importlib.import_module(f"{EXTENSION}.core.toolchain")
        folder = environment.user_dir()
        self.assertEqual(folder, pathlib.Path(bpy.utils.extension_path_user(EXTENSION)))
        self.assertEqual(toolchain.user_dir(), folder)

    def test_unregister_gives_back_stock_ui(self):
        ext = importlib.import_module(EXTENSION)
        menus = importlib.import_module(f"{EXTENSION}.menus")
        topbar = importlib.import_module(f"{EXTENSION}.ui_topbar")
        ui = importlib.import_module(f"{EXTENSION}.ui")
        ext.unregister()
        try:
            self.assertNotIn(menus.draw_editor_menus, _draw_funcs(bpy.types.TOPBAR_MT_editor_menus))
            for func in (topbar.draw_upper_bar_left, topbar.draw_upper_bar_right):
                self.assertNotIn(func, _draw_funcs(bpy.types.TOPBAR_HT_upper_bar))
            self.assertNotIn(ui.draw_statusbar, _draw_funcs(bpy.types.STATUSBAR_HT_header))
            self.assertFalse(hasattr(bpy.types, "GAMESTUDIO_MT_game"))
            self.assertFalse(hasattr(bpy.types, "GAMESTUDIO_MT_projects"))
        finally:
            ext.register()
        self.assertIn(menus.draw_editor_menus, _draw_funcs(bpy.types.TOPBAR_MT_editor_menus))
        for func in (topbar.draw_upper_bar_left, topbar.draw_upper_bar_right):
            self.assertEqual(_draw_funcs(bpy.types.TOPBAR_HT_upper_bar).count(func), 1)


def main():
    import test_claude_code
    import test_codex
    import test_editors  # imported here: they need the extension enabled
    import test_environment
    import test_shell_ux

    loader = unittest.defaultTestLoader
    # test_editors last: its final test unregisters and re-registers the whole extension.
    suite = unittest.TestSuite([loader.loadTestsFromTestCase(ShellTests), test_shell_ux.load_tests(loader),
                                test_claude_code.load_tests(loader), test_codex.load_tests(loader),
                                test_environment.load_tests(loader),
                                test_editors.load_tests(loader)])
    result = unittest.TextTestRunner(verbosity=2, stream=sys.stdout).run(suite)
    print(f"\nShell tests: {result.testsRun} run, {len(result.failures)} failures, {len(result.errors)} errors, "
          f"{len(result.skipped)} skipped")
    sys.exit(0 if result.wasSuccessful() else 1)


main()
