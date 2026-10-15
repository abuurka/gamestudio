# SPDX-License-Identifier: GPL-3.0-or-later
"""depsgraph_update_post: the live link's change signal (core/live.py)."""

import unittest

import bpy

import contract_support as cs


class DepsgraphHandlerTest(unittest.TestCase):
    def setUp(self):
        cs.factory_reset(use_empty=True)

    def tearDown(self):
        cs.factory_reset(use_empty=True)

    def test_update_post_reports_transform_after_view_layer_update(self):
        """After moving an object, `view_layer.update()` fires depsgraph_update_post with an
        update for that object whose `is_updated_transform` is True.

        Breaks if changed: core/live.py `_on_depsgraph_update` / `_collect`, which turn exactly
        these updates into `entity.transform` messages for the running game (and the add-on tests
        that drive them with `view_layer.update()`).
        """
        obj = bpy.data.objects.new("GSContractMover", None)
        bpy.context.scene.collection.objects.link(obj)
        bpy.context.view_layer.update()
        seen = []

        def handler(scene, depsgraph):
            for update in depsgraph.updates:
                if isinstance(update.id, bpy.types.Object) and update.id.original.name == obj.name:
                    seen.append(update.is_updated_transform)

        bpy.app.handlers.depsgraph_update_post.append(handler)
        try:
            obj.location.x += 1.0
            bpy.context.view_layer.update()
        finally:
            bpy.app.handlers.depsgraph_update_post.remove(handler)
        self.assertIn(True, seen, f"no transform update for the moved object (updates seen: {seen})")
