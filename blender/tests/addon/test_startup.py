# SPDX-License-Identifier: GPL-3.0-or-later
"""Registration the way Blender itself does it.

Blender registers add-ons at startup, and when one is enabled in Preferences,
with bpy.data swapped for a restricted stand-in. Code that reads bpy.data
during register(), such as bpy.data.filepath, works in scripts but fails in
real use. RestrictBlend reproduces that state.
"""

import unittest

import bpy

try:  # Blender 5.0 made this bundled module private
    from _bpy_restrict_state import RestrictBlend
except ImportError:
    from bpy_restrict_state import RestrictBlend

import gamestudio


class StartupRegistrationTests(unittest.TestCase):
    def test_register_with_restricted_blend_data(self):
        gamestudio.unregister()
        error = None
        try:
            with RestrictBlend():
                gamestudio.register()
        except Exception as exc:  # report it, but leave the suite in a registered state
            error = exc
            try:
                gamestudio.unregister()
            except Exception:
                pass
            gamestudio.register()
        self.assertIsNone(error, f"register() failed under Blender's startup restrictions: {error}")
        self.assertTrue(hasattr(bpy.types.Object, "gs"))
        self.assertTrue(hasattr(bpy.types.WindowManager, "gs_ai"))
