# SPDX-License-Identifier: GPL-3.0-or-later
"""UI smoke tests: draw every panel with a layout stand-in that validates calls.

Panels are not drawn in --background, so a mock layout checks that every
property, operator, menu and icon used by the draw code exists, in the states
the user can be in: no project, no registry, no server, and fully connected.
"""

import contextlib
import json
import os
import sys
import time
import types
import unittest
from unittest import mock

import bpy

from gamestudio import prefs, ui
from gamestudio.core import components, export, link, project, registry, util
from gamestudio.host import editors

import helpers

ICONS = set(bpy.types.UILayout.bl_rna.functions["prop"].parameters["icon"].enum_items.keys())
LAYOUT_ATTRS = set(bpy.types.UILayout.bl_rna.properties.keys())
# context.workspace as Blender's draw wrapper of panels with appended draw functions reads it.
WORKSPACE = types.SimpleNamespace(use_filter_by_owner=False, owner_ids=())


class _OperatorProps:
    def __init__(self, test, idname):
        category, name = idname.split(".")
        rna = getattr(getattr(bpy.ops, category), name).get_rna_type()
        object.__setattr__(self, "_names", set(rna.properties.keys()))
        object.__setattr__(self, "_test", test)
        object.__setattr__(self, "_idname", idname)

    def __setattr__(self, name, value):
        self._test.assertIn(name, self._names, f"{self._idname} has no property {name!r}")


class MockLayout:
    # Read back by Blender's draw wrapper of panels and menus with appended draw functions.
    operator_context = "INVOKE_REGION_WIN"

    def __init__(self, test, texts=None):
        object.__setattr__(self, "_test", test)
        object.__setattr__(self, "texts", texts if texts is not None else [])

    def __setattr__(self, name, value):
        self._test.assertIn(name, LAYOUT_ATTRS, f"UILayout has no attribute {name!r}")
        object.__setattr__(self, name, value)

    def _icon(self, icon):
        self._test.assertIn(icon, ICONS, f"unknown icon {icon!r}")

    def _child(self, *_args, **_kwargs):
        return MockLayout(self._test, self.texts)

    row = column = box = split = grid_flow = column_flow = _child

    def panel(self, idname, default_closed=False):
        self._test.assertIsInstance(idname, str)
        return self._child(), self._child()

    def label(self, text="", icon="NONE", **_kwargs):
        self._icon(icon)
        self.texts.append(text)

    def prop(self, data, prop, text=None, icon="NONE", **_kwargs):
        self._icon(icon)
        self._test.assertIn(prop, data.bl_rna.properties, f"{data!r} has no property {prop!r}")

    def prop_enum(self, data, prop, value, text=None, icon="NONE", **_kwargs):
        self._icon(icon)
        self._test.assertIn(prop, data.bl_rna.properties, f"{data!r} has no property {prop!r}")
        name = bpy.types.UILayout.enum_item_name(data, prop, value)  # dynamic items too
        self._test.assertTrue(name, f"{prop} has no item {value!r}")
        self.texts.append(text if text is not None else name)

    def operator(self, idname, text="", icon="NONE", **_kwargs):
        self._icon(icon)
        if text:
            self.texts.append(text)
        return _OperatorProps(self._test, idname)

    def menu(self, menu, text="", icon="NONE", **_kwargs):
        self._icon(icon)
        self._test.assertTrue(hasattr(bpy.types, menu), f"menu {menu} is not registered")
        self.texts.append(text)

    def separator(self, **_kwargs):
        pass

    def context_pointer_set(self, name, data):
        self._test.assertIsInstance(name, str)
        self.texts.append(f"<context {name}={data.name}>")


class _FakeSelf:
    """Stands in for a Panel/Menu instance so its draw() can run on a MockLayout."""

    def __init__(self, cls, layout):
        self.layout = layout
        self._cls = cls

    def __getattr__(self, name):
        return types.MethodType(getattr(self._cls, name), self)


PANELS = (ui.GAMESTUDIO_PT_project, ui.GAMESTUDIO_PT_play, ui.GAMESTUDIO_PT_components,
          ui.GAMESTUDIO_PT_console, ui.GAMESTUDIO_PT_object)
# The 3D View sidebar's Game tab: shown only with the sidebar_tab preference.
SIDEBAR_PANELS = (ui.GAMESTUDIO_PT_play, ui.GAMESTUDIO_PT_entity, ui.GAMESTUDIO_PT_components,
                  ui.GAMESTUDIO_PT_project, ui.GAMESTUDIO_PT_console)


class UiDrawTest(helpers.ProjectTestCase):
    def tearDown(self):
        helpers.restore_global_link()
        super().tearDown()

    def draw_all(self, active_object=None) -> str:
        texts = []
        context = types.SimpleNamespace(scene=bpy.context.scene, active_object=active_object,
                                        window_manager=bpy.context.window_manager, workspace=WORKSPACE)
        for cls in PANELS + (ui.GAMESTUDIO_MT_add_component,):
            cls.draw(_FakeSelf(cls, MockLayout(self, texts)), context)
        return "\n".join(texts)

    def test_panels_with_appended_draw_functions(self):
        """Code another module appends to these panels runs through Blender's draw wrapper, which
        reads layout.operator_context and context.workspace."""
        def appended(panel, _context):
            panel.layout.operator_context = "INVOKE_DEFAULT"
            panel.layout.label(text="Appended row")

        obj = helpers.new_object("Thing")
        own = self.draw_all(obj).splitlines()
        originals = {cls: cls.draw for cls in PANELS}
        for cls in PANELS:
            cls.append(appended)
        try:
            text = self.draw_all(obj).splitlines()
        finally:
            for cls in PANELS:
                cls.remove(appended)
                cls.draw = originals[cls]  # the wrapper prints errors instead of raising: keep it out of other tests
        self.assertEqual(text.count("Appended row"), len(PANELS))
        self.assertEqual([line for line in text if line != "Appended row"], own, "and the panels' own rows")

    def test_no_project_no_registry_no_server(self):
        project.set_project_root_override(None)
        registry.reload_registry()
        obj = helpers.new_object("Lonely")
        text = self.draw_all(obj)
        self.assertIn("Save this .blend inside a GameStudio", text)
        self.assertNotIn("No component registry", text, "no project: nothing about a registry to add from")
        self.assertNotIn("component list is not read yet", text)
        self.assertIn("Messages from the running game appear here.", " ".join(text.split()))  # wrapped
        self.draw_all(None)

    def test_project_without_registry(self):
        project.registry_path(self.root).unlink()
        registry.reload_registry(self.root)
        text = self.draw_all(helpers.new_object("Thing"))
        self.assertIn("No registry yet", text)
        self.assertIn("Dev server stopped", text)

    def test_components_missing_and_disabled(self):
        obj = helpers.new_object("Hero")
        components.add_component(obj, "Showcase")
        components.add_component(obj, "Coin")
        components.add_component(obj, "Spin")
        components.set_enabled(obj, "Spin", False)
        registry.set_registry(helpers.modified_registry(
            lambda d: d["components"].remove(helpers.component(d, "Coin"))))
        text = self.draw_all(obj)
        self.assertIn("'Coin' is not in the registry.", text)
        self.assertIn("Add Component", text)

    def test_connected_with_console_messages(self):
        hub = helpers.FakeHub()
        try:
            client = helpers.connect_global_link(hub)
            client.logs.append({"t": "log", "level": "warn", "msg": "low fps\nsecond line"})
            client.logs.append({"t": "error", "level": "error", "msg": "Boom", "file": "src/systems/a.ts",
                                "line": 3, "col": 7})
            client.stats = {"fps": 60, "entities": 4, "bodies": 2}
            text = self.draw_all(helpers.new_object("Thing"))
        finally:
            hub.close()
        self.assertIn("Server running", text)
        self.assertIn("1 game connected", text)
        self.assertIn("    at a.ts:3:7", text)
        self.assertIn("Boom", text)

    def test_empty_registry_menu(self):
        registry.set_registry({"components": []})
        text = self.draw_all(helpers.new_object("Thing"))
        self.assertIn("The registry is empty", text)


class StatusBarRefreshTest(unittest.TestCase):
    """refresh_status_bar(): stock Blender redraws the status bar when the workspace status text is cleared."""

    def test_refresh_status_bar(self):
        cleared = []

        def window(name, modal=()):
            workspace = types.SimpleNamespace(status_text_set_internal=lambda text: cleared.append((name, text)))
            return types.SimpleNamespace(workspace=workspace, modal_operators=modal)

        wm = types.SimpleNamespace(windows=[window("main"), window("busy", modal=("transform",)),
                                            types.SimpleNamespace(workspace=None, modal_operators=())])
        fake_bpy = types.SimpleNamespace(context=types.SimpleNamespace(
            window_manager=wm, temp_override=lambda **_kw: contextlib.nullcontext()))
        self.assertIs(ui.refresh_status_bar, util.refresh_status_bar)
        with mock.patch.object(util, "bpy", fake_bpy), mock.patch.object(editors, "available", return_value=False):
            ui.refresh_status_bar()
        self.assertEqual(cleared, [("main", None)], "a window running a tool keeps its own status text")
        with mock.patch.object(util, "bpy", fake_bpy), mock.patch.object(editors, "available", return_value=True), \
                mock.patch.object(editors, "tag_redraw") as redraw:
            ui.refresh_status_bar()
        redraw.assert_called_once()
        self.assertEqual(len(cleared), 1, "GameStudio Editor redraws it directly")
        ui.refresh_status_bar()  # the real thing: no windows in --background


class PluralTest(unittest.TestCase):
    """plural(): the counts in panels (Game Project's "Last export: rev 3, 8 entities")."""

    def test_plural(self):
        self.assertEqual(ui.plural(1, "entity"), "1 entity")
        self.assertEqual(ui.plural(8, "entity"), "8 entities")
        self.assertEqual(ui.plural(0, "body"), "0 bodies")
        self.assertEqual(ui.plural(2, "game"), "2 games")
        self.assertEqual(ui.plural(3, "draw call"), "3 draw calls")
        self.assertEqual(ui.plural(2, "key"), "2 keys")  # a vowel before the y


class LinkedObjectUiTest(helpers.ProjectTestCase):
    def test_linked_object_is_read_only(self):
        lib_obj = helpers.new_object("LibCrate", helpers.new_mesh())
        lib_path = str(self.root / "lib.blend")
        bpy.data.libraries.write(lib_path, {lib_obj})
        helpers.reset_scene()
        with bpy.data.libraries.load(lib_path, link=True) as (_src, dst):
            dst.objects = ["LibCrate"]
        context = types.SimpleNamespace(scene=bpy.context.scene, active_object=dst.objects[0],
                                        object=dst.objects[0], window_manager=bpy.context.window_manager,
                                        workspace=WORKSPACE)
        for cls in (ui.GAMESTUDIO_PT_components, ui.GAMESTUDIO_PT_object):
            texts = []
            cls.draw(_FakeSelf(cls, MockLayout(self, texts)), context)
            self.assertIn("Linked or non-editable override data is read-only.", " ".join(texts), cls.bl_idname)
        self.assertIn("Read-only", texts)


class ObjectPropertiesTest(helpers.ProjectTestCase):
    """The Game panel of Object properties: the entity ID, Add Component and the components."""

    def context(self, obj=None, active=None):
        return types.SimpleNamespace(scene=bpy.context.scene, object=obj, active_object=active or obj,
                                     window_manager=bpy.context.window_manager, workspace=WORKSPACE)

    def draw(self, cls, context) -> str:
        texts = []
        cls.draw(_FakeSelf(cls, MockLayout(self, texts)), context)
        return " ".join(" ".join(texts).split())

    def test_registered_in_the_object_tab(self):
        for cls in (ui.GAMESTUDIO_PT_object, ui.GAMESTUDIO_PT_object_materials):
            self.assertTrue(cls.is_registered, cls.bl_idname)
            self.assertIs(getattr(bpy.types, cls.bl_idname), cls)
            self.assertEqual((cls.bl_space_type, cls.bl_region_type, cls.bl_context),
                             ("PROPERTIES", "WINDOW", "object"), cls.bl_idname)
        self.assertEqual(ui.GAMESTUDIO_PT_object.bl_label, "Game")
        self.assertNotIn("DEFAULT_CLOSED", getattr(ui.GAMESTUDIO_PT_object, "bl_options", set()))
        self.assertEqual(ui.GAMESTUDIO_PT_object.bl_order, 0, "the earliest place bl_order allows")
        self.assertEqual(ui.GAMESTUDIO_PT_object_materials.bl_parent_id, "GAMESTUDIO_PT_object")

    def test_poll(self):
        from test_bake import noise_ramp

        plain = helpers.new_object("Plain", helpers.new_mesh())
        self.assertTrue(ui.GAMESTUDIO_PT_object.poll(self.context(plain)))
        self.assertFalse(ui.GAMESTUDIO_PT_object.poll(self.context(None)))
        self.assertFalse(ui.GAMESTUDIO_PT_object.poll(types.SimpleNamespace()), "a context without objects")
        self.assertFalse(ui.GAMESTUDIO_PT_object_materials.poll(self.context(plain)), "no procedural materials")
        self.assertFalse(ui.GAMESTUDIO_PT_object_materials.poll(self.context(None)))
        hot = helpers.new_object("Hot", helpers.new_mesh("HotMesh"))
        hot.data.materials.append(noise_ramp())
        self.assertTrue(ui.GAMESTUDIO_PT_object_materials.poll(self.context(hot)))
        text = self.draw(ui.GAMESTUDIO_PT_object_materials, self.context(hot))
        self.assertIn("Lava: procedural, baked to textures on export", text)
        self.assertIn("Bake Materials Now", text)

    def test_components_like_the_modifier_stack(self):
        hero = helpers.new_object("Hero", helpers.new_mesh())
        self.assertIn("No components yet", self.draw(ui.GAMESTUDIO_PT_object, self.context(hero)))
        components.add_component(hero, "Spin")
        components.add_component(hero, "Coin")  # an entity field: an object picker
        components.set_enabled(hero, "Spin", False)
        text = self.draw(ui.GAMESTUDIO_PT_object, self.context(hero))
        self.assertIn("Entity ID", text)
        self.assertNotIn("Read-only", text)
        self.assertLess(text.index("Add Component"), text.index("Spin"), "Add Component on top, like Add Modifier")
        self.assertLess(text.index("Spin"), text.index("Coin"))
        self.assertNotIn("<context", text, "the active object needs no context override")
        registry.set_registry(helpers.modified_registry(
            lambda d: d["components"].remove(helpers.component(d, "Coin"))))
        self.assertIn("'Coin' is not in the registry.", self.draw(ui.GAMESTUDIO_PT_object, self.context(hero)))
        registry.set_registry({"components": []})
        text = self.draw(ui.GAMESTUDIO_PT_object, self.context(hero))
        self.assertIn("The project's component list is not read yet.", text)
        self.assertNotIn("is not in the registry", text, "nothing read yet is no error")
        environment = sys.modules["gamestudio.environment"]
        with mock.patch.object(environment, "setting_up", lambda *_a: True):
            text = self.draw(ui.GAMESTUDIO_PT_object, self.context(hero))
        self.assertIn("Setting up GameStudio: the components appear when it finishes.", text)
        self.assertIn("Select an object", self.draw(ui.GAMESTUDIO_PT_object, self.context(None)))

    def test_pinned_object(self):
        """A pinned Properties editor shows its object; operators and menus get it as the active object."""
        pinned = helpers.new_object("Pinned")
        components.add_component(pinned, "Spin")
        text = self.draw(ui.GAMESTUDIO_PT_object, self.context(pinned, active=helpers.new_object("Other")))
        self.assertTrue(text.startswith("<context active_object=Pinned>"), text)
        self.assertIn("Spin", text)


class SidebarPreferenceTest(helpers.ProjectTestCase):
    """The 3D View sidebar's Game tab shows only with the "Game tab in the 3D View sidebar" preference."""

    def test_hidden_by_default(self):
        self.assertIs(prefs.GSPreferences.bl_rna.properties["sidebar_tab"].default, False)
        self.assertFalse(prefs.sidebar_enabled())
        for cls in SIDEBAR_PANELS:
            self.assertFalse(cls.poll(bpy.context), cls.bl_idname)
        obj = helpers.new_object("Thing")
        self.assertTrue(ui.GAMESTUDIO_PT_object.poll(types.SimpleNamespace(object=obj)), "Object properties stays")

    def test_shown_with_the_preference(self):
        with mock.patch.object(prefs, "get_prefs", return_value=types.SimpleNamespace(sidebar_tab=True)):
            self.assertTrue(prefs.sidebar_enabled())
            for cls in SIDEBAR_PANELS:
                self.assertTrue(cls.poll(bpy.context), cls.bl_idname)

    def test_missing_or_broken_preferences(self):
        with mock.patch.object(prefs, "get_prefs", return_value=types.SimpleNamespace()):
            self.assertFalse(prefs.sidebar_enabled())
        with mock.patch.object(prefs, "get_prefs", side_effect=RuntimeError("restricted context")):
            self.assertFalse(prefs.sidebar_enabled())

    def test_every_game_tab_panel_is_gated(self):
        panels = [cls for cls in ui._CLASSES if issubclass(cls, bpy.types.Panel)]
        self.assertEqual(set(panels), set(SIDEBAR_PANELS))
        for cls in panels:
            self.assertEqual((cls.bl_space_type, cls.bl_region_type, cls.bl_category), ("VIEW_3D", "UI", "Game"))
            self.assertIs(cls.poll.__func__, ui._GamePanel.poll.__func__, cls.bl_idname)


class DrawFunctionsTest(helpers.ProjectTestCase):
    """The module-level draw functions that Scene properties, the top bar and the editors reuse."""

    STABLE = ("draw_play", "draw_levels", "draw_project", "draw_console", "draw_entity_panel",
              "draw_components_panel", "draw_components", "draw_object_game", "draw_object_materials")

    def tearDown(self):
        helpers.restore_global_link()
        export._last_export.clear()
        super().tearDown()

    def test_draw_functions_on_a_scene_with_components(self):
        import inspect

        hero = helpers.new_object("Hero", helpers.new_mesh())
        components.add_component(hero, "Spin")
        components.add_component(hero, "Showcase")
        export.export_scene(bake="off")
        context = types.SimpleNamespace(scene=bpy.context.scene, object=hero, active_object=hero,
                                        window_manager=bpy.context.window_manager, workspace=WORKSPACE)
        drawn = {}
        for name in self.STABLE:
            fn = getattr(ui, name)
            self.assertEqual(list(inspect.signature(fn).parameters)[:2], ["layout", "context"], name)
            texts = []
            fn(MockLayout(self, texts), context)
            drawn[name] = " ".join(" ".join(texts).split())
        self.assertIn("Play", drawn["draw_play"])
        self.assertIn("Levels (0)", drawn["draw_levels"])
        self.assertIn("Export All", drawn["draw_levels"])
        self.assertIn("Last export: rev 1", drawn["draw_project"])
        self.assertIn("Messages from the running game appear here.", drawn["draw_console"])
        self.assertIn("Spin", drawn["draw_components_panel"])
        self.assertIn("Showcase", drawn["draw_object_game"])
        self.assertEqual(drawn["draw_object_materials"], "", "no procedural materials")
        layout = MockLayout(self)
        with mock.patch.object(MockLayout, "panel", autospec=True, side_effect=MockLayout.panel) as panel:
            ui.draw_levels(layout, context)
            ui.draw_levels(layout, context, default_closed=False)  # Scene properties: Levels start open
        self.assertEqual([call.kwargs["default_closed"] for call in panel.call_args_list], [True, False])


ANIMATOR = {"name": "Animator", "category": "Animation", "builtin": True,
            "fields": [{"name": "clip", "type": "string", "default": ""},
                       {"name": "loop", "type": "bool", "default": True}]}


class AuthoringUiTest(helpers.ProjectTestCase):
    """The Prefab checkbox, the Animator clip menu, export warnings and material bake status."""

    registry_data = helpers.modified_registry(lambda d: d["components"].append(ANIMATOR))

    def tearDown(self):
        export._last_export.clear()
        super().tearDown()

    def context(self, obj=None, collection=None):
        return types.SimpleNamespace(scene=bpy.context.scene, active_object=obj, collection=collection,
                                     window_manager=bpy.context.window_manager, workspace=WORKSPACE)

    def draw(self, cls, context) -> str:
        texts = []
        cls.draw(_FakeSelf(cls, MockLayout(self, texts)), context)
        return " ".join(" ".join(texts).split())  # wrapped labels read as one text

    def test_animator_clip_menu(self):
        rig = helpers.new_object("Rig", None)
        rig.animation_data_create()
        rig.animation_data.action = bpy.data.actions.new("Idle")
        rig.animation_data.nla_tracks.new().strips.new("Run", 1, bpy.data.actions.new("Run"))
        components.add_component(rig, "Animator", {"clip": "Jump"})
        text = self.draw(ui.GAMESTUDIO_PT_components, self.context(rig))
        self.assertIn("'Jump' is not an action or NLA clip of Rig or its children", text)
        self.assertEqual(self.draw(ui.GAMESTUDIO_MT_animator_clips, self.context(rig)), "Idle Run")
        with bpy.context.temp_override(active_object=rig, object=rig):
            self.assertEqual(bpy.ops.gamestudio.set_animator_clip(clip="Run"), {"FINISHED"})
        self.assertEqual(components.get_values(rig, "Animator")["clip"], "Run")
        self.assertNotIn("is not an action", self.draw(ui.GAMESTUDIO_PT_components, self.context(rig)))
        plain = helpers.new_object("Plain", None)
        self.assertIn("No actions", self.draw(ui.GAMESTUDIO_MT_animator_clips, self.context(plain)))

    def test_collection_prefab_checkbox(self):
        coins = bpy.data.collections.new("Coins")
        helpers.new_object("CoinBody", helpers.new_mesh(), collection=coins)
        self.assertTrue(ui.GAMESTUDIO_PT_collection.poll(self.context(collection=coins)))
        self.assertFalse(ui.GAMESTUDIO_PT_collection.poll(self.context(collection=bpy.context.scene.collection)))
        self.assertIn("A prefab is a template", self.draw(ui.GAMESTUDIO_PT_collection, self.context(collection=coins)))

        coins.gs_is_prefab = True
        self.assertTrue(export.is_prefab(coins))
        self.assertEqual(export.prefab_collections(), [coins])
        anchor = helpers.new_object("Anchor", None)
        helpers.new_object("Glint", None, parent=anchor, collection=coins)
        placed = helpers.new_object("CoinInstance", None)
        placed.instance_type = "COLLECTION"
        placed.instance_collection = coins
        text = self.draw(ui.GAMESTUDIO_PT_collection, self.context(collection=coins))
        self.assertIn("2 objects, 1 instance in this file", text)
        self.assertIn("'Glint' is parented to 'Anchor', outside the collection: not in the template", text)
        coins.gs_is_prefab = False
        self.assertNotIn("gs_prefab", coins.keys())

    def test_export_warnings_and_material_status(self):
        from test_bake import noise_ramp

        hot = helpers.new_object("Hot", helpers.new_mesh("HotMesh"))
        hot.data.materials.append(noise_ramp())
        hidden = helpers.new_object("Stashed", helpers.new_mesh("StashedMesh"))
        hidden.hide_set(True)
        context = self.context(hot)
        entity = self.draw(ui.GAMESTUDIO_PT_entity, context)
        self.assertIn("Lava: procedural, baked to textures on export", entity)
        self.assertIn("Bake Materials Now", entity)
        self.assertNotIn("Export:", self.draw(ui.GAMESTUDIO_PT_play, context))

        export.export_scene(bake="off")
        play = self.draw(ui.GAMESTUDIO_PT_play, context)
        self.assertIn("Export: 1 warning", play)
        self.assertIn("hidden in the viewport are exported and shown in the game: Stashed", play)
        project_text = self.draw(ui.GAMESTUDIO_PT_project, context)
        self.assertRegex(project_text, r"\d+(\.\d)? (B|KB), 4 triangles, 2 draw calls")
        self.assertIn("Last export: rev 1", project_text)

        config = json.loads((self.root / "gamestudio.json").read_text(encoding="utf-8"))
        config["export"] = {"bake": "off"}
        (self.root / "gamestudio.json").write_text(json.dumps(config), encoding="utf-8")
        stamp = time.time() + 5
        os.utime(self.root / "gamestudio.json", (stamp, stamp))
        self.assertIn("exports as flat colours (export.bake is off)", self.draw(ui.GAMESTUDIO_PT_entity, context))
        self.assertEqual(self.draw(ui.GAMESTUDIO_PT_entity, self.context(hidden)).count("procedural"), 0)

    def test_bake_operator(self):
        helpers.new_object("Plain", helpers.new_mesh())
        self.assertEqual(bpy.ops.gamestudio.bake_materials(), {"FINISHED"})  # nothing to bake is fine
        project.set_project_root_override(None)
        with self.assertRaises(RuntimeError):
            bpy.ops.gamestudio.bake_materials()  # no project: reported as an error (CANCELLED raises here)


if __name__ == "__main__":
    unittest.main()
