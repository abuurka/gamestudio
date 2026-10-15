# SPDX-License-Identifier: GPL-3.0-or-later
"""Registry loading, property generation, and regeneration that keeps values."""

import bpy

from gamestudio.core import components, project, registry

import helpers


class RegistryLoadingTest(helpers.ProjectTestCase):
    def test_loads_fixture_in_order(self):
        self.assertTrue(registry.reload_registry(self.root))
        expected = [c["name"] for c in helpers.fixture_registry()["components"]]
        self.assertEqual(registry.component_names(), expected)
        self.assertEqual(registry.source_info()["origin"], "file")
        for name in expected:
            self.assertEqual(registry.component_class(name).__name__, f"GSC_{name}")
            self.assertIn(f"gsc_{name}", bpy.types.Object.bl_rna.properties)

    def test_schemas_are_copies(self):
        schemas = registry.schemas()
        schemas[0]["name"] = "Changed"
        registry.get_schema("Spin")["fields"].clear()
        self.assertEqual(registry.component_names()[0], "RigidBody")
        self.assertEqual(len(registry.get_schema("Spin")["fields"]), 2)
        self.assertIsNone(registry.get_schema("Nope"))

    def test_invalid_entries_are_skipped_with_warnings(self):
        data = helpers.fixture_registry()
        data["components"] += [
            {"name": "lowercase", "fields": []},
            {"name": "Spin", "fields": []},  # duplicate
            {"name": "BadFields", "fields": [
                {"name": "ok", "type": "float", "default": 2},
                {"name": "Bad Name", "type": "float"},
                {"name": "kind", "type": "enum"},
                {"name": "what", "type": "quaternion"},
                {"name": "lvl", "type": "int", "default": "high"},
            ]},
        ]
        registry.set_registry(data)
        self.assertIn("BadFields", registry.component_names())
        self.assertNotIn("lowercase", registry.component_names())
        fields = [f["name"] for f in registry.get_schema("BadFields")["fields"]]
        self.assertEqual(fields, ["ok", "lvl"])
        self.assertEqual(registry.get_field("BadFields", "lvl").get("default"), None)
        self.assertGreaterEqual(len(registry.warnings()), 5)

    def test_unusable_document_raises_and_keeps_registry(self):
        before = registry.component_names()
        with self.assertRaises(ValueError):
            registry.set_registry({"format": "something.else", "components": []})
        with self.assertRaises(ValueError):
            registry.set_registry(["not", "a", "dict"])
        self.assertEqual(registry.component_names(), before)

    def test_broken_file_keeps_current_registry(self):
        project.registry_path(self.root).write_text("{ not json", encoding="utf-8")
        self.assertFalse(registry.reload_registry(self.root))
        self.assertIn("Spin", registry.component_names())
        self.assertTrue(registry.warnings())

    def test_file_watch_reloads_on_change(self):
        data = helpers.modified_registry(lambda d: d["components"].append(
            {"name": "Extra", "fields": [{"name": "x", "type": "int"}]}))
        helpers.write_registry(self.root, data)
        registry.pump()
        self.assertIn("Extra", registry.component_names())

    def test_no_project_means_empty_registry(self):
        project.set_project_root_override(None)
        self.assertEqual(bpy.data.filepath, "")
        self.assertFalse(registry.reload_registry())
        self.assertEqual(registry.component_names(), [])
        self.assertNotIn("gsc_Spin", bpy.types.Object.bl_rna.properties)


class PropertyGenerationTest(helpers.ProjectTestCase):
    def prop(self, component, field):
        return registry.component_class(component).bl_rna.properties[field]

    def test_every_field_type(self):
        p = self.prop("Showcase", "speed")
        self.assertEqual((p.type, p.hard_min, p.hard_max, p.default), ("FLOAT", 0.0, 10.0, 2.5))
        self.assertEqual(p.unit, "TIME_ABSOLUTE")
        p = self.prop("Showcase", "count")
        self.assertEqual((p.type, p.hard_min, p.hard_max, p.default), ("INT", -5, 5, 3))
        p = self.prop("Showcase", "active")
        self.assertEqual((p.type, p.default), ("BOOLEAN", True))
        p = self.prop("Showcase", "label")
        self.assertEqual((p.type, p.default, p.subtype), ("STRING", "hello", "NONE"))
        p = self.prop("Showcase", "mode")
        self.assertEqual(p.type, "ENUM")
        self.assertEqual([i.identifier for i in p.enum_items], ["walk", "run", "fly"])
        self.assertEqual(p.default, "run")
        p = self.prop("Showcase", "size")
        self.assertEqual((p.type, p.array_length, tuple(p.default_array), p.hard_min), ("FLOAT", 2, (1.0, 2.0), 0.0))
        p = self.prop("Showcase", "direction")
        self.assertEqual((p.type, p.array_length, tuple(p.default_array)), ("FLOAT", 3, (0.0, 0.0, -1.0)))
        p = self.prop("Showcase", "tint")
        self.assertEqual((p.type, p.array_length, p.subtype), ("FLOAT", 4, "COLOR"))
        self.assertEqual(tuple(p.default_array), (1.0, 0.5, 0.25, 1.0))
        p = self.prop("Showcase", "target")
        self.assertEqual(p.type, "POINTER")
        self.assertEqual(p.fixed_type.identifier, "Object")
        p = self.prop("Showcase", "sound")
        self.assertEqual((p.type, p.subtype), ("STRING", "FILE_PATH"))

    def test_units_labels_descriptions(self):
        self.assertEqual(self.prop("CharacterController", "speed").unit, "LENGTH")
        self.assertEqual(self.prop("CharacterController", "turnSpeed").subtype, "ANGLE")
        self.assertEqual(self.prop("CharacterController", "jumpSpeed").name, "Jump Speed")
        self.assertEqual(self.prop("FollowCamera", "lookAtOffset").name, "Look At Offset")
        self.assertEqual(self.prop("Spin", "speed").description, "Radians per second")
        self.assertEqual(self.prop("Spin", "speed").step, 10)

    def test_everything_is_library_overridable(self):
        for name in registry.component_names():
            cls = registry.component_class(name)
            for field in registry.get_schema(name)["fields"]:
                self.assertTrue(cls.bl_rna.properties[field["name"]].is_overridable, f"{name}.{field['name']}")
            self.assertTrue(bpy.types.Object.bl_rna.properties[f"gsc_{name}"].is_overridable, name)
        gs = bpy.types.Object.bl_rna.properties["gs"]
        self.assertTrue(gs.is_overridable)
        self.assertTrue(gs.fixed_type.bl_rna.properties["components"].is_overridable)

    def test_defaults_on_a_new_object(self):
        obj = helpers.new_object("Thing")
        grp = obj.gsc_Showcase
        self.assertAlmostEqual(grp.speed, 2.5)
        self.assertEqual(grp.mode, "run")
        self.assertEqual(tuple(grp.size), (1.0, 2.0))
        self.assertIsNone(grp.target)
        self.assertEqual(obj.gsc_RigidBody.type, "dynamic")


class RegenerationTest(helpers.ProjectTestCase):
    def setUp(self):
        super().setUp()
        self.target = helpers.new_object("Target")
        self.obj = helpers.new_object("Hero")
        components.add_component(self.obj, "Spin", {"speed": 7.5, "axis": [1, 0, 0]})
        components.add_component(self.obj, "Showcase", {
            "speed": 7.0, "count": -4, "active": False, "label": "kept", "mode": "fly",
            "size": [3, 4], "tint": [0.1, 0.2, 0.3, 0.4], "target": "Target", "sound": "audio/a.ogg"})
        components.add_component(self.obj, "Coin", {"value": 42, "rarity": "epic"})

    def test_values_survive_schema_changes(self):
        def mutate(data):
            spin = helpers.component(data, "Spin")
            spin["fields"] = [f for f in spin["fields"] if f["name"] != "axis"]  # removed
            spin["fields"].append({"name": "clockwise", "type": "bool", "default": True})  # added
            spin["description"] = "Changed description"
            show = helpers.component(data, "Showcase")
            for f in show["fields"]:
                if f["name"] == "mode":
                    f["options"] = ["swim", "fly", "walk", "run"]  # reordered + new option
                elif f["name"] == "speed":
                    f["type"] = "int"                            # float -> int
                    f.pop("unit")
                    f["default"] = 1
                elif f["name"] == "size":
                    f["type"] = "vec3"                           # vec2 -> vec3
                    f["default"] = [0, 0, 0]

        registry.set_registry(helpers.modified_registry(mutate))
        spin = components.get_values(self.obj, "Spin")
        self.assertEqual(spin, {"speed": 7.5, "clockwise": True})
        show = components.get_values(self.obj, "Showcase")
        self.assertEqual(show["mode"], "fly")
        self.assertEqual(show["speed"], 7)
        self.assertEqual(show["size"], [3.0, 4.0, 0.0])
        self.assertEqual(show["count"], -4)
        self.assertEqual(show["active"], False)
        self.assertEqual(show["label"], "kept")
        self.assertEqual(show["target"], "Target")
        self.assertEqual(show["sound"], "audio/a.ogg")
        self.assertEqual(show["tint"], [0.1, 0.2, 0.3, 0.4])
        self.assertEqual(components.get_values(self.obj, "Coin"), {"value": 42, "rarity": "epic", "magnet": None})

    def test_removed_enum_option_falls_back_to_default(self):
        def mutate(data):
            for f in helpers.component(data, "Coin")["fields"]:
                if f["name"] == "rarity":
                    f["options"] = ["common", "rare"]
        registry.set_registry(helpers.modified_registry(mutate))
        self.assertEqual(components.get_values(self.obj, "Coin")["rarity"], "common")
        self.assertEqual(components.get_values(self.obj, "Coin")["value"], 42)

    def test_component_removed_from_registry_keeps_data(self):
        registry.set_registry(helpers.modified_registry(
            lambda d: d["components"].remove(helpers.component(d, "Coin"))))
        self.assertNotIn("gsc_Coin", bpy.types.Object.bl_rna.properties)
        self.assertIn("Coin", components.list_components(self.obj))
        self.assertEqual(components.missing_components(self.obj), ["Coin"])
        self.assertNotIn("Coin", components.export_components(self.obj))
        with self.assertRaises(ValueError):
            components.get_values(self.obj, "Coin")

        registry.set_registry(helpers.fixture_registry())  # definition returns
        self.assertEqual(components.missing_components(self.obj), [])
        self.assertEqual(components.get_values(self.obj, "Coin")["value"], 42)
        self.assertEqual(components.get_values(self.obj, "Coin")["rarity"], "epic")

    def test_unchanged_components_are_not_re_registered(self):
        before = registry.component_class("RigidBody")
        registry.set_registry(helpers.modified_registry(
            lambda d: helpers.component(d, "Spin")["fields"].append({"name": "extra", "type": "int"})))
        self.assertIs(registry.component_class("RigidBody"), before)
        self.assertIn("extra", registry.component_class("Spin").bl_rna.properties)
