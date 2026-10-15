# SPDX-License-Identifier: GPL-3.0-or-later
"""Add-on lifecycle: unregister cleans up completely; register again restores data."""

import bpy

import gamestudio
from gamestudio.core import components, devserver, link, live, registry

import helpers

_HANDLER_LISTS = ("load_post", "save_post", "undo_post", "redo_post", "depsgraph_update_post", "exit_pre")


def _our_handlers() -> list:
    found = []
    for name in _HANDLER_LISTS:
        for fn in getattr(bpy.app.handlers, name):
            if getattr(fn, "__module__", "").startswith("gamestudio"):
                found.append(f"{name}:{fn.__name__}")
    return found


class AddonLifecycleTest(helpers.ProjectTestCase):
    def test_unregister_then_register_keeps_component_data(self):
        obj = helpers.new_object("Hero")
        components.add_component(obj, "Spin", {"speed": 3})
        self.assertTrue(_our_handlers())

        gamestudio.unregister()
        try:
            self.assertNotIn("gs", bpy.types.Object.bl_rna.properties)
            self.assertNotIn("gsc_Spin", bpy.types.Object.bl_rna.properties)
            self.assertEqual(_our_handlers(), [])
            for timer in (link._timer, live._timer, devserver._timer, registry._timer):
                self.assertFalse(bpy.app.timers.is_registered(timer), timer.__module__)
            with self.assertRaises((KeyError, AttributeError, RuntimeError)):
                bpy.ops.gamestudio.play.get_rna_type()
            self.assertFalse(link.get_link().connected)
        finally:
            gamestudio.register()

        self.assertEqual(components.list_components(obj), ["Spin"])
        self.assertEqual(components.get_values(obj, "Spin")["speed"], 3.0)
        self.assertTrue(bpy.app.timers.is_registered(link._timer))
