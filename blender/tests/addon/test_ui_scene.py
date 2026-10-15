# SPDX-License-Identifier: GPL-3.0-or-later
"""Scene Properties > Game Project (ui_scene.py) and the Game Assets popover (assetlib.py).

Panels are not drawn in --background, so they draw on test_ui's MockLayout, which checks that every
property, operator, menu and icon they use exists.
"""

import types
from unittest import mock

import bpy

from gamestudio import assetlib, ui, ui_scene
from gamestudio.core import export, project
from gamestudio.editors import project as project_editor

import helpers
from test_ui import WORKSPACE, MockLayout, _FakeSelf

SCENE_PANELS = ui_scene._CLASSES


class PopoverLayout(MockLayout):
    """MockLayout that also records header popovers."""

    def _child(self, *_args, **_kwargs):
        return PopoverLayout(self._test, self.texts)

    row = column = box = split = grid_flow = column_flow = _child

    def popover(self, panel, text="", icon="NONE", **_kwargs):
        self._icon(icon)
        self._test.assertTrue(hasattr(bpy.types, panel), f"panel {panel} is not registered")
        self.texts.append(f"popover:{panel}:{text}")


def fake_context(obj=None, selected=(), mode="OBJECT"):
    return types.SimpleNamespace(scene=bpy.context.scene, active_object=obj, selected_objects=list(selected),
                                 mode=mode, window_manager=bpy.context.window_manager, workspace=WORKSPACE)


def draw(test, cls, context, layout_cls=MockLayout) -> str:
    texts = []
    cls.draw(_FakeSelf(cls, layout_cls(test, texts)), context)
    return " ".join(" ".join(texts).split())  # wrapped labels read as one text


class ScenePropertiesTest(helpers.ProjectTestCase):
    def tearDown(self):
        ui._levels_report = None
        export._last_export.clear()
        super().tearDown()

    def save_level(self, name):
        (self.root / export.SCENES_DIR).mkdir(exist_ok=True)
        path = self.root / export.SCENES_DIR / f"{name}.blend"
        bpy.ops.wm.save_as_mainfile(filepath=str(path), copy=name != "main")
        return path

    def test_panels_are_in_scene_properties(self):
        parent, *children = SCENE_PANELS
        for cls in SCENE_PANELS:
            self.assertTrue(cls.is_registered, cls.__name__)
            registered = getattr(bpy.types, cls.bl_idname)
            self.assertEqual((registered.bl_space_type, registered.bl_region_type, registered.bl_context),
                             ("PROPERTIES", "WINDOW", "scene"), cls.__name__)
            self.assertNotEqual(getattr(registered, "bl_category", ""), "Game")
        self.assertEqual(parent.bl_label, "Game Project")
        for cls in children:
            self.assertEqual(cls.bl_parent_id, parent.bl_idname)
        self.assertEqual([c.bl_label for c in children], ["Scripts", "Lighting", "Build"])

    def test_no_project(self):
        project.set_project_root_override(None)
        context = fake_context()
        text = draw(self, ui_scene.GAMESTUDIO_PT_scene_project, context)
        self.assertIn("Save this .blend inside a GameStudio project", text)
        self.assertIn("New Project...", text)
        self.assertIn("Open Project...", text)
        for cls in SCENE_PANELS[1:]:
            self.assertFalse(cls.poll(context), cls.__name__)

    def test_project_scene_draws_everything(self):
        self.save_level("main")
        self.save_level("level2")
        context = fake_context(helpers.new_object("Thing"))
        text = draw(self, ui_scene.GAMESTUDIO_PT_scene_project, context)
        for expected in ("test-game", "Scene exports as 'main'", "Dev server stopped", "in the registry",
                         "Open in ", "Levels (2)", "Export All", "main (start)", "level2", "Open"):
            self.assertIn(expected, text)
        self.assertNotIn("New Project...", text)
        for cls in SCENE_PANELS[1:]:
            self.assertTrue(cls.poll(context), cls.__name__)
        self.assertIn("New Component...", draw(self, ui_scene.GAMESTUDIO_PT_scene_scripts, context))
        draw(self, ui_scene.GAMESTUDIO_PT_scene_build, context)

    def test_build_state_shows(self):
        context = fake_context()
        build = project_editor.BUILD
        saved = vars(build).copy()
        try:
            build.state, build.root, build.message, build.finished = "failed", self.root, "Build broke.", 0.0
            self.assertIn("Build broke.", draw(self, ui_scene.GAMESTUDIO_PT_scene_build, context))
        finally:
            vars(build).update(saved)

    def test_export_all_levels(self):
        helpers.new_object("Thing", helpers.new_mesh())
        self.save_level("main")
        self.assertEqual(bpy.ops.gamestudio.export_all_levels(), {"FINISHED"})
        text = draw(self, ui_scene.GAMESTUDIO_PT_scene_project, fake_context())
        self.assertIn("Exported 1 level", text)
        self.assertIn("Levels (1)", text)
        self.assertTrue((project.gs_dir(self.root) / "export" / "main.scene.json").is_file())

    def test_project_tab_follows_the_sidebar_preference(self):
        from gamestudio import prefs

        overview = [cls for cls in project_editor.host.editors.registered_classes("GAMESTUDIO_PROJECT")
                    if cls.gs_spec.id == "overview"]
        if not overview:
            self.skipTest("no sidebar fallback on this host")
        for enabled in (False, True):
            with mock.patch.object(prefs, "sidebar_enabled", lambda e=enabled: e, create=True):
                self.assertEqual(overview[0].poll(fake_context()),
                                 enabled or project_editor.host.editors.available())


class GameAssetsUiTest(helpers.ProjectTestCase):
    def tearDown(self):
        assetlib._listing_cache.clear()
        super().tearDown()

    def write_library(self, *names):
        """A library file with asset collections, written directly (no background Blender)."""
        colls = set()
        for name in names:
            coll = bpy.data.collections.new(name)
            helpers.new_object(f"{name}Body", helpers.new_mesh(f"{name}Mesh"), collection=coll)
            coll.asset_mark()
            export.set_prefab(coll, True)
            colls.add(coll)
        path = assetlib.library_path("props")
        path.parent.mkdir(parents=True, exist_ok=True)
        bpy.data.libraries.write(str(path), colls, fake_user=True)
        bpy.data.batch_remove([o for c in colls for o in c.all_objects] + list(colls))
        return path

    def test_registered_where_users_look(self):
        self.assertTrue(assetlib.GAMESTUDIO_PT_game_assets.is_registered)
        self.assertEqual((assetlib.GAMESTUDIO_PT_game_assets.bl_space_type,
                          assetlib.GAMESTUDIO_PT_game_assets.bl_region_type), ("VIEW_3D", "HEADER"))
        self.assertIn(assetlib._draw_view3d_menus, bpy.types.VIEW3D_MT_editor_menus.draw._draw_funcs)

    def test_header_popover_only_in_object_mode_with_a_project(self):
        def header(context):
            texts = []
            assetlib._draw_view3d_menus(types.SimpleNamespace(layout=PopoverLayout(self, texts)), context)
            return texts

        self.assertEqual(header(fake_context()), ["popover:GAMESTUDIO_PT_game_assets:Game Assets"])
        self.assertEqual(header(fake_context(mode="EDIT_MESH")), [])
        project.set_project_root_override(None)
        self.assertEqual(header(fake_context()), [])

    def test_sidebar_panel_follows_the_preference(self):
        from gamestudio import prefs

        for enabled in (False, True):
            with mock.patch.object(prefs, "sidebar_enabled", lambda e=enabled: e, create=True):
                self.assertEqual(assetlib.GAMESTUDIO_PT_asset_library.poll(fake_context()), enabled)

    def test_list_place_and_replace(self):
        cls = assetlib.GAMESTUDIO_PT_game_assets
        text = draw(self, cls, fake_context())
        self.assertIn("No asset libraries in assets/ yet", text)
        self.assertIn("Add Selected to Library...", text)

        self.write_library("Crate", "Barrel")
        bpy.ops.wm.save_as_mainfile(filepath=str(self.root / "level.blend"))  # links need a saved level
        text = draw(self, cls, fake_context())
        self.assertIn("props", text)
        self.assertIn("Barrel", text)
        self.assertIn("Crate", text)
        self.assertEqual(text.count("Place"), 3, "one per asset, and Place Asset...")
        self.assertEqual(text.count("Replace"), 2)

        self.assertEqual(bpy.ops.gamestudio.asset_place(asset_name="Crate", library="assets/props.blend"),
                         {"FINISHED"})
        inst = bpy.context.view_layer.objects.active
        self.assertEqual(assetlib.asset_of(inst)["asset"], "Crate")
        text = draw(self, cls, fake_context(inst, [inst]))
        self.assertIn("Crate (1 placed)", text)
        self.assertIn("Active: instance of Crate", text)

        with bpy.context.temp_override(active_object=inst, selected_objects=[inst]):
            self.assertEqual(bpy.ops.gamestudio.asset_replace(asset_name="Barrel", library="assets/props.blend"),
                             {"FINISHED"})
        self.assertEqual(assetlib.asset_of(inst)["asset"], "Barrel")
        plain = helpers.new_object("Plain", None)
        with bpy.context.temp_override(active_object=plain, selected_objects=[plain]):
            self.assertFalse(bpy.ops.gamestudio.asset_replace.poll())

    def test_broken_library_does_not_break_the_popover(self):
        path = assetlib.library_path("broken")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"not a blend file")
        text = draw(self, assetlib.GAMESTUDIO_PT_game_assets, fake_context())
        self.assertIn("broken", text)
