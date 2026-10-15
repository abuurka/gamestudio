# SPDX-License-Identifier: GPL-3.0-or-later
"""Game UI authoring in Blender (docs/GAME-UI.md §7): Edit Game UI and Back to Level, Shift+A > Game UI,
New Game UI Screen, Choose Font, the Layout box and its px properties, Scene > Game UI Screen, the Add
Component menu in UI files, the 3D view setup and the anchor overlay. Panels are drawn on test_ui's
MockLayout, which checks every property, operator, menu and icon they use."""

import pathlib
import shutil
import types
import unittest
from unittest import mock

import bpy

from gamestudio import gameui, menus, ui
from gamestudio.bridge import api
from gamestudio.core import components, gameui as core, project
from gamestudio.gameui import build, ops, overlay, panels

import helpers
from test_gameui_build import UiFileTestCase, builtin_font_file, ui_registry, write_png
from test_ui import WORKSPACE, MockLayout, _FakeSelf


class EditGameUiTest(helpers.ProjectTestCase):
    registry_data = ui_registry()

    def save_level(self):
        level = self.root / "scenes" / "main.blend"
        level.parent.mkdir(exist_ok=True)
        bpy.ops.wm.save_as_mainfile(filepath=str(level))
        api.agent_dirty = False
        return level

    def test_edit_game_ui_and_back_to_level(self):
        level = self.save_level()
        self.assertFalse(ops.GAMESTUDIO_OT_back_to_level.poll(bpy.context), "not in a UI file")
        self.assertEqual(bpy.ops.gamestudio.edit_game_ui(), {"FINISHED"})
        ui_file = self.root / "ui" / "ui.blend"
        self.assertEqual(pathlib.Path(bpy.data.filepath), ui_file, "created (in a background Blender), then opened")
        self.assertTrue(gameui.in_ui_file())
        self.assertEqual(gameui.canvas_names(), ["Menu"])
        self.assertTrue(components.has_component(bpy.data.scenes["Menu"].camera, "UiCanvas"))
        self.assertEqual(bpy.ops.gamestudio.edit_game_ui(), {"FINISHED"}, "already open: nothing to do")
        self.assertEqual(bpy.ops.gamestudio.back_to_level(), {"FINISHED"})
        self.assertEqual(pathlib.Path(bpy.data.filepath), level)
        self.assertFalse(gameui.in_ui_file())

        api.agent_dirty = True  # unsaved changes: from Python, unsaved says what to do
        with self.assertRaises(RuntimeError) as ctx:
            bpy.ops.gamestudio.edit_game_ui()
        self.assertIn("unsaved changes", str(ctx.exception))
        self.assertEqual(pathlib.Path(bpy.data.filepath), level)
        helpers.new_object("Marker")
        self.assertEqual(bpy.ops.gamestudio.edit_game_ui(unsaved="SAVE"), {"FINISHED"})
        self.assertEqual(pathlib.Path(bpy.data.filepath), ui_file)
        bpy.ops.wm.open_mainfile(filepath=str(level))
        self.assertIn("Marker", bpy.data.objects, "saved first")
        bpy.data.objects.remove(bpy.data.objects["Marker"])
        api.agent_dirty = True
        self.assertEqual(bpy.ops.gamestudio.edit_game_ui(unsaved="DISCARD"), {"FINISHED"})
        bpy.ops.wm.open_mainfile(filepath=str(level))
        self.assertIn("Marker", bpy.data.objects, "the change was not saved")

    def test_back_to_level_needs_the_start_level(self):
        gameui.new_ui_file(self.root)
        with self.assertRaises(RuntimeError) as ctx:
            bpy.ops.gamestudio.back_to_level()
        self.assertIn("scenes/main.blend does not exist", str(ctx.exception))

    def test_needs_a_project(self):
        project.set_project_root_override(None)
        self.assertFalse(ops.GAMESTUDIO_OT_edit_game_ui.poll(bpy.context))


class AddOperatorsTest(UiFileTestCase):
    def test_shift_a_kinds(self):
        scene = bpy.context.scene
        self.assertEqual(scene.name, "Menu")
        for kind, label in (("TEXT", "Text"), ("PANEL", "Panel"), ("BUTTON", "Button"), ("BAR", "Bar"),
                            ("SLIDER", "Slider"), ("TOGGLE", "Toggle"), ("GROUP", "Group")):
            self.assertEqual(bpy.ops.gamestudio.ui_add(kind=kind), {"FINISHED"})
            obj = bpy.context.view_layer.objects.active
            self.assertEqual((obj.name, build.kind_of(obj)), (label, kind.lower()))
            self.assertEqual([round(v) for v in build.origin_px(scene, obj)], [960, 540], "the cursor is at the centre")
        self.assertEqual(components.get_values(bpy.data.objects["Slider"], "UiSlider")["value"], "audio.music")
        self.assertEqual(components.get_values(bpy.data.objects["Toggle"], "UiToggle")["value"], "",
                         "set in Object > Game")
        bpy.ops.gamestudio.ui_add(kind="BUTTON")
        self.assertEqual(bpy.context.view_layer.objects.active.name, "Button2")
        scene.cursor.location = (-5.0, 2.0, 0.0)  # inside the frame: (460, 340) px
        bpy.ops.gamestudio.ui_add(kind="TEXT")
        self.assertEqual([round(v) for v in build.origin_px(scene, bpy.context.view_layer.objects.active)], [460, 340])
        scene.cursor.location = (50.0, 0.0, 0.0)  # outside: the centre
        bpy.ops.gamestudio.ui_add(kind="GROUP")
        self.assertEqual([round(v) for v in build.origin_px(scene, bpy.context.view_layer.objects.active)], [960, 540])

    def test_add_image_and_choose_font(self):
        png = write_png(self.outside / "my logo.png", 64, 32)
        self.assertEqual(bpy.ops.gamestudio.ui_add_image(filepath=str(png)), {"FINISHED"})
        obj = bpy.context.view_layer.objects.active
        self.assertEqual(obj.name, "my_logo")
        self.assertTrue((self.root / "assets" / "ui" / "my logo.png").is_file())
        self.assertEqual([round(v) for v in build.element_size(bpy.context.scene, obj)], [64, 32])
        bpy.ops.gamestudio.ui_add(kind="TEXT")
        font = builtin_font_file()
        shutil.copy2(font, self.outside / font.name)
        self.assertEqual(bpy.ops.gamestudio.ui_choose_font(filepath=str(self.outside / font.name)), {"FINISHED"})
        self.assertEqual(build.font_path(bpy.data.objects["Text"].data.font), f"assets/fonts/{font.name}")

    def test_add_3d_model(self):
        scene = bpy.context.scene
        self.assertEqual(bpy.ops.gamestudio.ui_add_model(source="KIT", kit="crate"), {"FINISHED"})
        model = bpy.context.view_layer.objects.active
        self.assertEqual((model.name, build.kind_of(model)), ("Model", "model"))
        self.assertEqual([round(v) for v in build.origin_px(scene, model)], [960, 540])
        self.assertEqual([c.name for c in model.children], ["ModelModel"])
        self.assertEqual(components.get_values(model, "UiModel")["size"], [320.0, 320.0])
        # An object of the file, moved into the model.
        statue = bpy.data.objects.new("Statue", helpers.new_mesh("StatueMesh"))
        self.assertEqual(bpy.ops.gamestudio.ui_add_model(source="OBJECT", object="Statue"), {"FINISHED"})
        self.assertIs(statue.parent, bpy.data.objects["Model2"])
        self.assertIn(scene, statue.users_scene)
        with self.assertRaises(RuntimeError) as ctx:
            bpy.ops.gamestudio.ui_add_model(source="OBJECT", object="Nothing")
        self.assertIn("No object or collection", str(ctx.exception))
        with self.assertRaises(RuntimeError) as ctx:
            bpy.ops.gamestudio.ui_add_model(source="ASSET")
        self.assertIn("No asset chosen", str(ctx.exception))
        self.assertNotIn("sky", [item[0] for item in ops._kit_enum(None, bpy.context)], "a sun is no model")
        # The overlay draws each model's view; the Layout box resizes it.
        frames = overlay.model_frames(types.SimpleNamespace(scene=scene))
        self.assertEqual(len(frames), 16, "two models, four sides each")
        self.assertEqual([round(v, 4) for v in frames[0][:2]], [-1.6, -1.6])
        model.gs_ui.width = 500
        self.assertEqual(components.get_values(model, "UiModel")["size"], [500.0, 320.0])

    def test_new_screen_and_set_up(self):
        bpy.context.scene.render.resolution_x = 1280
        bpy.context.scene.render.resolution_y = 720
        self.assertEqual(bpy.ops.gamestudio.ui_new_screen(name="HUD", kind="hud"), {"FINISHED"})
        hud = bpy.data.scenes["HUD"]
        self.assertEqual(core.design_size(hud), (1280, 720))
        self.assertEqual(components.get_values(hud.camera, "UiCanvas")["kind"], "hud")
        self.assertIs(bpy.context.window.scene, hud)
        bpy.ops.gamestudio.ui_new_screen(name="HUD")
        self.assertIn("HUD2", bpy.data.scenes)
        bare = bpy.data.scenes.new("Bare")
        bpy.context.window.scene = bare
        self.assertTrue(core.camera_problems(bare))
        self.assertEqual(bpy.ops.gamestudio.ui_setup_screen(), {"FINISHED"})
        self.assertFalse(core.camera_problems(bare))
        self.assertTrue(components.has_component(bare.camera, "UiCanvas"))

    def test_show_in_game_needs_a_game(self):
        self.assertTrue(ops.GAMESTUDIO_OT_ui_show_in_game.poll(bpy.context))
        with mock.patch.object(ops, "game_connected", return_value=False):
            self.assertEqual(bpy.ops.gamestudio.ui_show_in_game(), {"CANCELLED"})
        from gamestudio.core import gameui_live

        sent = []
        with mock.patch.object(ops, "game_connected", return_value=True), \
                mock.patch.object(gameui_live, "send_open", side_effect=sent.append, create=True):
            self.assertEqual(bpy.ops.gamestudio.ui_show_in_game(), {"FINISHED"})
        self.assertEqual(sent, ["Menu"])

    def test_not_offered_outside_ui_files(self):
        level = self.root / "scenes" / "main.blend"
        level.parent.mkdir(exist_ok=True)
        bpy.ops.wm.save_as_mainfile(filepath=str(level))
        self.assertFalse(gameui.in_ui_file())
        for cls in (ops.GAMESTUDIO_OT_ui_add, ops.GAMESTUDIO_OT_ui_add_image, ops.GAMESTUDIO_OT_ui_new_screen,
                    ops.GAMESTUDIO_OT_ui_frame_canvas, ops.GAMESTUDIO_OT_ui_show_in_game,
                    ops.GAMESTUDIO_OT_back_to_level):
            self.assertFalse(cls.poll(bpy.context), cls.bl_idname)
        self.assertFalse(panels.GAMESTUDIO_PT_scene_game_ui.poll(bpy.context))

    def test_views(self):
        count = ops.setup_views(frame=False)
        for window in bpy.context.window_manager.windows:
            for area in window.screen.areas:
                if area.type == "VIEW_3D":
                    space = area.spaces.active
                    self.assertEqual(space.shading.type, "MATERIAL")
                    self.assertEqual(space.region_3d.view_perspective, "CAMERA")
                    self.assertAlmostEqual(space.overlay.grid_scale, 0.1, places=5)
        self.assertGreaterEqual(count, 0)
        self.assertEqual(bpy.ops.gamestudio.ui_frame_canvas(), {"FINISHED"})


class DrawTest(UiFileTestCase):
    def context(self, obj=None):
        return types.SimpleNamespace(scene=bpy.context.scene, object=obj, active_object=obj,
                                     window_manager=bpy.context.window_manager, workspace=WORKSPACE,
                                     view_layer=bpy.context.view_layer)

    def draw(self, fn, context) -> str:
        texts = []
        fn(MockLayout(self, texts), context)
        return " ".join(" ".join(texts).split())

    def draw_cls(self, cls, context) -> str:
        texts = []
        cls.draw(_FakeSelf(cls, MockLayout(self, texts)), context)
        return " ".join(" ".join(texts).split())

    def test_layout_box_and_px_properties(self):
        card = gameui.panel("Menu", "Card", (300, 200), (400, 240))
        text = self.draw(ui.draw_object_game, self.context(card))
        self.assertIn("Layout (panel)", text)
        self.assertIn("Anchor: Auto: top-left", text)
        self.assertIn("Add UiAnchor", text)
        self.assertLess(text.index("Layout"), text.index("Add Component"))
        self.assertAlmostEqual(card.gs_ui.x, 300, places=3)
        self.assertAlmostEqual(card.gs_ui.y, 200, places=3)
        card.gs_ui.x = 960
        card.gs_ui.y = 540
        self.assertEqual([round(v, 3) for v in build.origin_px(bpy.context.scene, card)], [960, 540])
        card.gs_ui.width = 500
        self.assertEqual([round(v, 3) for v in build.panel_size(card)], [500, 240])
        self.assertAlmostEqual(card.gs_ui.height, 240, places=3)
        gameui.update("Menu", "Card", anchor="bottom", stretch="width")
        self.assertIn("Anchor: Bottom, stretch width", self.draw(ui.draw_object_game, self.context(card)))
        label = gameui.text("Menu", "CardTitle", "Hi", (960, 500), parent="Card")
        text = self.draw(ui.draw_object_game, self.context(label))
        self.assertIn("Follows Card", text)
        self.assertNotIn("Width", text, "no box: no size")
        cam = bpy.context.scene.camera
        self.assertNotIn("Layout", self.draw(ui.draw_object_game, self.context(cam)), "not for the camera")
        # Sliders and toggles are resized with W/H too.
        slider = gameui.slider("Menu", "Music", (960, 300))
        self.assertIn("Layout (slider)", self.draw(ui.draw_object_game, self.context(slider)))
        gem = gameui.model("Menu", "Gem", (300, 800), kit="crate")
        text = self.draw(ui.draw_object_game, self.context(gem))
        self.assertIn("Layout (model)", text)
        self.assertIn("model", panels.SIZED_KINDS)  # W/H resize it
        self.assertIn("the 3D model", text)
        self.assertNotIn("Layout", self.draw(ui.draw_object_game, self.context(gem.children[0])), "its objects")
        self.assertTrue({"slider", "toggle"} <= set(panels.SIZED_KINDS))
        slider.gs_ui.width = 500
        self.assertEqual([round(v, 3) for v in build.panel_size(build.find_part(slider, "fill"))], [500, 28])

    def test_add_component_menu_puts_ui_first(self):
        group = gameui.group("Menu", "G", (100, 100))
        bpy.context.view_layer.objects.active = group
        context = self.context(group)
        text = self.draw_cls(ui.GAMESTUDIO_MT_add_component, context)
        self.assertIn("UiButton", text)
        self.assertIn("More", text)
        self.assertNotIn("RigidBody", text)
        more = self.draw_cls(ui.GAMESTUDIO_MT_add_component_more, context)
        self.assertIn("RigidBody", more)
        self.assertNotIn("UiButton", more)
        level = self.root / "scenes" / "main.blend"
        level.parent.mkdir(exist_ok=True)
        bpy.ops.wm.save_as_mainfile(filepath=str(level))
        text = self.draw_cls(ui.GAMESTUDIO_MT_add_component, self.context(None))
        self.assertIn("RigidBody", text)
        self.assertNotIn("More", text, "levels list every category")

    def test_scene_panel(self):
        self.assertTrue(panels.GAMESTUDIO_PT_scene_game_ui.poll(bpy.context))
        text = self.draw_cls(panels.GAMESTUDIO_PT_scene_game_ui, self.context())
        for expected in ("Show in Game", "Frame Canvas", "0 elements", "New Game UI Screen"):
            self.assertIn(expected, text)
        self.assertNotIn("Set Up Game UI Screen", text)
        scene = bpy.context.scene
        scene.gs_ui.width = 1280
        self.assertEqual(core.design_size(scene), (1280, 1080))
        self.assertAlmostEqual(core.px_per_unit(scene), 100.0, places=4)
        bare = bpy.data.scenes.new("Bare")
        text = self.draw(panels.draw_screen, types.SimpleNamespace(scene=bare))
        self.assertIn("Set Up Game UI Screen", text)
        self.assertIn("has no camera", text)

    def test_menus(self):
        context = self.context()
        text = self.draw(ops.draw_menu_items, context)
        self.assertIn("Edit Game UI", text)
        self.assertIn("Back to Level", text)
        texts = []
        ops.draw_add_menu(types.SimpleNamespace(layout=MockLayout(self, texts)), context)
        self.assertEqual(texts, ["Game UI"])
        text = self.draw_cls(ops.GAMESTUDIO_MT_add_game_ui, context)
        self.assertEqual(text, "Text Image... 3D Model... Panel Button Bar Slider Toggle Group")
        texts = []
        menus.draw_gamestudio_menu_end(types.SimpleNamespace(layout=MockLayout(self, texts)), context)
        self.assertIn("Edit Game UI", texts)
        title = gameui.text("Menu", "Title", "Hi", (960, 100))
        texts = []
        panels.draw_font_extra(types.SimpleNamespace(layout=MockLayout(self, texts)), self.context(title))
        self.assertIn("Choose Font...", texts)

    def test_overlay(self):
        corner = gameui.text("Menu", "Score", "0", (100, 60))
        lines = overlay.overlay_lines(types.SimpleNamespace(active_object=corner, scene=bpy.context.scene))
        self.assertEqual(len(lines), 10)  # the line to the origin and the diamond's four sides
        self.assertEqual([round(v, 4) for v in lines[0][:2]], [-9.6, 5.4], "the top-left corner")
        child = gameui.text("Menu", "Sub", "x", (120, 60), parent="Score")
        self.assertIsNone(overlay.overlay_lines(types.SimpleNamespace(active_object=child, scene=bpy.context.scene)))
        self.assertIsNone(overlay.overlay_lines(types.SimpleNamespace(active_object=None, scene=bpy.context.scene)))


if __name__ == "__main__":
    unittest.main()
