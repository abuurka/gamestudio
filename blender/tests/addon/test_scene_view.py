# SPDX-License-Identifier: GPL-3.0-or-later
"""The Scene view's Blender UI (editors/scene_view.py): the Game toggle in the 3D View's header,
GameStudio > Scene View (split the Game workspace's 3D View, the Scene view in the right half), the
registration, and the draw handlers doing nothing outside the Scene view's area.

The drawing itself (the frame under Blender's overlays) needs a GPU and a window: it is checked in a
UI Blender (docs/SPEC.md §7.3).
"""

import types
import unittest

import bpy

from gamestudio import menus
from gamestudio.core import project, sceneview
from gamestudio.editors import game_stream, scene_view

from test_game_stream import HeaderLayout
from test_sceneview import SceneArea, SceneViewCase
from test_ui import MockLayout


class SceneViewHeaderTest(SceneViewCase):
    def draw(self, area):
        layout = HeaderLayout(self)
        context = types.SimpleNamespace(area=area, screen=self.screen, region=area.regions[0])
        scene_view.draw_header(types.SimpleNamespace(layout=layout), context)
        return layout.texts

    def test_the_game_toggle(self):
        self.assertEqual(self.draw(self.big), ["Game"])
        self.connect([])
        self.assertEqual(sceneview.enable(self.window, self.screen, self.big), "")
        self.assertEqual(self.draw(self.big), ["<pressed Game>", "Game"])
        self.assertEqual(self.draw(self.small), ["Game"], "other 3D Views: off")
        context = types.SimpleNamespace(area=self.big, screen=self.screen)
        self.assertIn("Click to let Blender draw it again",
                      scene_view.GAMESTUDIO_OT_scene_view.description(context, None))
        context = types.SimpleNamespace(area=self.small, screen=self.screen)
        self.assertEqual(scene_view.GAMESTUDIO_OT_scene_view.description(context, None),
                         scene_view.GAMESTUDIO_OT_scene_view.bl_description)

    def test_the_view_menu_has_the_toggle_too(self):
        layout = HeaderLayout(self)
        context = types.SimpleNamespace(area=self.big, screen=self.screen)
        scene_view.draw_view_menu(types.SimpleNamespace(layout=layout), context)
        self.assertEqual(layout.texts, ["Scene View (Game Renderer)"])
        self.assertIn(scene_view.draw_view_menu, bpy.types.VIEW3D_MT_view.draw._draw_funcs)

    def test_no_toggle_outside_a_project(self):
        project.set_project_root_override(None)
        try:
            self.assertEqual(self.draw(self.big), [])
        finally:
            project.set_project_root_override(self.root)

    def test_scene_view_open_splits_the_3d_view(self):
        self.connect([])
        splits = []

        def split(window, screen, area):
            splits.append(area)
            right = SceneArea(width=area.width // 2, height=area.height, x=area.x + area.width // 2, y=area.y)
            area.width //= 2
            screen.areas.append(right)

        original = scene_view._split
        scene_view._split = split
        try:
            self.assertEqual(scene_view.split_and_enable(self.window), "")
            self.assertEqual(splits, [self.big], "the largest 3D View")
            right = self.screen.areas[-1]
            self.assertTrue(sceneview.is_area(right, self.screen), "the right half")
            self.assertFalse(sceneview.is_area(self.big, self.screen))
            self.assertEqual(right.space.shading.type, "SOLID")
            self.assertEqual(scene_view.split_and_enable(self.window), "", "already on: nothing more")
            self.assertEqual(len(splits), 1)
        finally:
            scene_view._split = original

    def test_scene_view_registered(self):
        import gamestudio

        self.assertIn("editors.scene_view", gamestudio._MODULE_NAMES)
        self.assertIn(scene_view.draw_header, bpy.types.VIEW3D_HT_header.draw._draw_funcs)
        self.assertIs(game_stream._viewer, sceneview)
        self.assertTrue(hasattr(bpy.ops.gamestudio, "scene_view"))
        self.assertTrue(hasattr(bpy.ops.gamestudio, "scene_view_open"))
        self.assertIn(sceneview._on_game_status, sceneview.link.get_link()._handlers.get("game.status", []))

    def test_scene_view_handlers_ignore_other_areas(self):
        # Background Blender: no area in the context; the handlers do nothing and raise nothing.
        scene_view._pre_view()
        scene_view._post_view()
        scene_view._post_pixel()
        self.assertIsNone(scene_view._capture["framebuffer"])


class SceneViewMenuTest(unittest.TestCase):
    def test_gamestudio_menu_has_scene_view(self):
        layout = MockLayout(self)
        context = types.SimpleNamespace(scene=bpy.context.scene, window_manager=bpy.context.window_manager)
        menus.draw_gamestudio_menu_end(types.SimpleNamespace(layout=layout), context)
        self.assertIn("Scene View", layout.texts)


if __name__ == "__main__":
    unittest.main()
