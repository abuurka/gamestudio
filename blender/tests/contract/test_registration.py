# SPDX-License-Identifier: GPL-3.0-or-later
"""Add-on registration runs with bpy.data restricted.

Blender swaps bpy.data and bpy.context for stand-ins while add-ons and app templates register
(`_bpy_restrict_state.RestrictBlend`). GameStudio's `core/registry.register()` detects this and
defers reading the project's components.json to a one-shot timer (and to `load_post`).
"""

import importlib
import shutil
import sys
import unittest

import bpy

import contract_support as cs

PROBE_ADDON = "gs_contract_restricted_probe"
PROBE_SOURCE = '''
import bpy
SEEN = {}

def register():
    SEEN["data_type"] = type(bpy.data).__name__
    try:
        bpy.data.filepath
        SEEN["filepath_blocked"] = False
    except AttributeError:
        SEEN["filepath_blocked"] = True

def unregister():
    pass
'''


class RestrictedRegistrationTest(unittest.TestCase):
    def test_restrict_blend_exists(self):
        """`_bpy_restrict_state.RestrictBlend` is still the context manager Blender uses.

        Breaks if changed: the reasoning in core/registry.py `_blend_data_available()` and the
        deferred first load in `register()` (component PropertyGroups would never be generated
        at startup, so every object would lose its component UI until the project is reopened).
        """
        module = importlib.import_module("_bpy_restrict_state")
        self.assertTrue(hasattr(module, "RestrictBlend"))
        with module.RestrictBlend():
            pass

    def test_restrict_blend_blocks_filepath(self):
        """Inside RestrictBlend, `bpy.data.filepath` is not readable.

        Breaks if changed: core/registry.register() relies on bpy.data being off limits during
        registration; if Blender starts allowing it, the deferral is unnecessary, and if it starts
        raising a different error, project discovery (project.project_root) fails at startup.
        """
        from _bpy_restrict_state import RestrictBlend

        with RestrictBlend():
            with self.assertRaises(AttributeError):
                bpy.data.filepath  # noqa: B018 (the access itself is the test)
        self.assertIsInstance(bpy.data.filepath, str)

    def test_restricted_data_type_name(self):
        """Inside RestrictBlend, `type(bpy.data).__name__` is "_RestrictData".

        Breaks if changed: core/registry.py `_blend_data_available()` compares this exact name;
        a rename makes register() read bpy.data while it is restricted and raise.
        """
        from _bpy_restrict_state import RestrictBlend

        with RestrictBlend():
            self.assertEqual(type(bpy.data).__name__, "_RestrictData")
        self.assertNotEqual(type(bpy.data).__name__, "_RestrictData")

    def test_addon_register_runs_restricted(self):
        """`addon_utils.enable()` calls an add-on's register() with bpy.data restricted.

        Breaks if changed: this is the situation core/registry.register() defers for. If Blender
        stops restricting, the deferral still works but is wasted; if Blender restricts more (for
        example bpy.app.timers), registry.register() can no longer schedule its first load.
        """
        import addon_utils

        folder = cs.make_tmp_dir("gs_contract_addon_")
        (folder / f"{PROBE_ADDON}.py").write_text(PROBE_SOURCE, encoding="utf-8")
        sys.path.insert(0, str(folder))
        try:
            module = addon_utils.enable(PROBE_ADDON, default_set=False, handle_error=lambda ex: None)
            self.assertIsNotNone(module, "addon_utils.enable() could not load the probe add-on")
            self.assertEqual(module.SEEN.get("data_type"), "_RestrictData")
            self.assertTrue(module.SEEN.get("filepath_blocked"))
        finally:
            addon_utils.disable(PROBE_ADDON, default_set=False)
            sys.modules.pop(PROBE_ADDON, None)
            sys.path.remove(str(folder))
            shutil.rmtree(folder, ignore_errors=True)
