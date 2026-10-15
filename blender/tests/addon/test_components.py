# SPDX-License-Identifier: GPL-3.0-or-later
"""The §7 component API: add/set/get, coercion, errors, read-only objects."""

import os

import bpy

from gamestudio.core import components, ids, schema

import helpers


class ComponentApiTest(helpers.ProjectTestCase):
    def setUp(self):
        super().setUp()
        self.obj = helpers.new_object("Hero")

    def test_add_gives_defaults_and_an_id(self):
        self.assertIsNone(ids.get_id(self.obj))
        components.add_component(self.obj, "Spin")
        self.assertTrue(ids.is_valid_id(ids.get_id(self.obj)))
        self.assertEqual(components.list_components(self.obj), ["Spin"])
        self.assertEqual(components.get_values(self.obj, "Spin"), {"speed": 1.0, "axis": [0.0, 1.0, 0.0]})
        components.add_component(self.obj, "RigidBody", {"type": "kinematic", "mass": 2})
        self.assertEqual(components.list_components(self.obj), ["Spin", "RigidBody"])
        self.assertEqual(components.get_values(self.obj, "RigidBody", for_export=True), {
            "type": "kinematic", "mass": 2.0, "linearDamping": 0.0, "angularDamping": 0.05,
            "gravityScale": 1.0, "lockRotation": False, "ccd": False})

    def test_every_type_round_trips(self):
        target = helpers.new_object("Target")
        components.add_component(self.obj, "Showcase", {
            "speed": 3, "count": "4", "active": "no", "label": "hi there", "mode": "FLY",
            "size": (5, 6), "direction": [1, 0, 0], "tint": [1, 0, 0], "target": target, "sound": "sfx\\hit.wav"})
        self.assertEqual(components.get_values(self.obj, "Showcase"), {
            "speed": 3.0, "count": 4, "active": False, "label": "hi there", "mode": "fly",
            "size": [5.0, 6.0], "direction": [1.0, 0.0, 0.0], "tint": [1.0, 0.0, 0.0, 1.0],
            "target": "Target", "sound": "sfx/hit.wav"})

    def test_set_field_coercion(self):
        components.add_component(self.obj, "Coin")
        components.set_field(self.obj, "Coin", "value", 5.0)
        self.assertEqual(components.get_values(self.obj, "Coin")["value"], 5)
        components.set_field(self.obj, "Coin", "value", "7")
        self.assertEqual(components.get_values(self.obj, "Coin")["value"], 7)
        components.set_field(self.obj, "Coin", "rarity", "Rare")
        self.assertEqual(components.get_values(self.obj, "Coin")["rarity"], "rare")

    def test_helpful_errors(self):
        components.add_component(self.obj, "Spin")
        with self.assertRaises(ValueError) as cm:
            components.set_field(self.obj, "Spin", "speed", 150)
        self.assertTrue(str(cm.exception).startswith("Field 'speed' expects a number between 0 and 100"))
        with self.assertRaises(ValueError) as cm:
            components.set_field(self.obj, "Spin", "speed", "fast")
        self.assertIn("expects a number between 0 and 100", str(cm.exception))
        with self.assertRaises(ValueError) as cm:
            components.add_component(self.obj, "Spinn")
        msg = str(cm.exception)
        self.assertTrue(msg.startswith("Unknown component 'Spinn'. Known: RigidBody, Collider, "), msg)
        self.assertIn("Did you mean 'Spin'?", msg)
        with self.assertRaises(ValueError) as cm:
            components.set_field(self.obj, "Spin", "sped", 1)
        self.assertIn("Component 'Spin' has no field 'sped'. Fields: speed, axis.", str(cm.exception))
        self.assertIn("Did you mean 'speed'?", str(cm.exception))
        with self.assertRaises(ValueError) as cm:
            components.set_field(self.obj, "Spin", "axis", [1, 2])
        self.assertIn("expects a list of 3 numbers", str(cm.exception))
        with self.assertRaises(ValueError) as cm:
            components.set_field(self.obj, "Coin", "value", 1)
        self.assertIn("has no component 'Coin'; add it first", str(cm.exception))

    def test_coerce_value_messages(self):
        coin_value = {"name": "value", "type": "int", "min": 0, "max": 1000}
        self.assertEqual(components.coerce_value(coin_value, 3.0), 3)
        for bad in (2.5, True, None, "x", 1001):
            with self.assertRaises(ValueError):
                components.coerce_value(coin_value, bad)
        with self.assertRaises(ValueError) as cm:
            components.coerce_value({"name": "type", "type": "enum", "options": ["a", "b"]}, "c")
        self.assertIn("expects one of: a, b", str(cm.exception))
        with self.assertRaises(ValueError) as cm:
            components.coerce_value({"name": "tint", "type": "color"}, [2, 0, 0, 1])
        self.assertIn("between 0 and 1", str(cm.exception))
        with self.assertRaises(ValueError) as cm:
            components.coerce_value({"name": "on", "type": "bool"}, "maybe")
        self.assertIn("expects true or false", str(cm.exception))
        with self.assertRaises(ValueError) as cm:
            components.coerce_value({"name": "sound", "type": "asset", "extensions": [".ogg"]}, "a.mp3")
        self.assertIn("expects a file ending in .ogg", str(cm.exception))
        with self.assertRaises(ValueError) as cm:
            components.coerce_value({"name": "target", "type": "entity"}, "Nobody")
        self.assertIn("no object 'Nobody' exists", str(cm.exception))
        self.assertEqual(components.coerce_value({"name": "v", "type": "vec2", "min": 0}, {"x": 1, "y": 2}),
                         (1.0, 2.0))
        self.assertIs(components.coerce_value, schema.coerce_value)

    def test_invalid_add_changes_nothing(self):
        with self.assertRaises(ValueError):
            components.add_component(self.obj, "Coin", {"value": 3, "rarity": "legendary"})
        self.assertEqual(components.list_components(self.obj), [])
        self.assertIsNone(ids.get_id(self.obj))
        with self.assertRaises(ValueError):
            components.add_component(self.obj, "Coin", {"valu": 3})
        self.assertEqual(components.list_components(self.obj), [])

    def test_entity_values_by_name_and_id(self):
        target = helpers.new_object("Target")
        other = helpers.new_object("Other")
        other_id = ids.ensure_id(other)
        components.add_component(self.obj, "FollowCamera", {"target": "Target"})
        self.assertIsNone(ids.get_id(target))
        self.assertEqual(components.get_values(self.obj, "FollowCamera")["target"], "Target")
        exported = components.get_values(self.obj, "FollowCamera", for_export=True)
        self.assertEqual(exported["target"], ids.get_id(target))  # target got an ID on export
        self.assertTrue(ids.is_valid_id(exported["target"]))
        components.set_field(self.obj, "FollowCamera", "target", other_id)
        self.assertEqual(components.get_values(self.obj, "FollowCamera", for_export=True)["target"], other_id)
        components.set_field(self.obj, "FollowCamera", "target", None)
        self.assertIsNone(components.get_values(self.obj, "FollowCamera", for_export=True)["target"])

    def test_readding_after_remove_starts_from_defaults(self):
        components.add_component(self.obj, "Spin", {"speed": 9})
        components.remove_component(self.obj, "Spin")
        self.assertEqual(components.list_components(self.obj), [])
        components.add_component(self.obj, "Spin")
        self.assertEqual(components.get_values(self.obj, "Spin")["speed"], 1.0)
        with self.assertRaises(ValueError):
            components.remove_component(self.obj, "Coin")

    def test_add_existing_is_idempotent_and_reenables(self):
        components.add_component(self.obj, "Spin", {"speed": 4})
        components.set_enabled(self.obj, "Spin", False)
        self.assertEqual(components.export_components(self.obj), {})
        components.add_component(self.obj, "Spin", {"axis": [1, 0, 0]})
        self.assertEqual(components.list_components(self.obj), ["Spin"])
        self.assertTrue(components.is_enabled(self.obj, "Spin"))
        self.assertEqual(components.export_components(self.obj), {"Spin": {"speed": 4.0, "axis": [1.0, 0.0, 0.0]}})

    def test_asset_paths_are_project_relative_on_export(self):
        components.add_component(self.obj, "Showcase")
        path = os.path.join(str(self.root), "audio", "boom.ogg")
        components.set_field(self.obj, "Showcase", "sound", path)
        self.assertEqual(components.get_values(self.obj, "Showcase", for_export=True)["sound"], "audio/boom.ogg")

    def test_set_values_is_atomic(self):
        components.add_component(self.obj, "Spin")
        with self.assertRaises(ValueError):
            components.set_values(self.obj, "Spin", {"speed": 3, "axis": "up"})
        self.assertEqual(components.get_values(self.obj, "Spin")["speed"], 1.0)
        components.set_values(self.obj, "Spin", {"speed": 3, "axis": [0, 0, 1]})
        self.assertEqual(components.get_values(self.obj, "Spin"), {"speed": 3.0, "axis": [0.0, 0.0, 1.0]})


class ReadOnlyObjectTest(helpers.ProjectTestCase):
    def test_linked_objects_are_refused(self):
        lib_obj = helpers.new_object("LibCrate", helpers.new_mesh())
        lib_path = str(self.root / "lib.blend")
        bpy.data.libraries.write(lib_path, {lib_obj})
        helpers.reset_scene()
        with bpy.data.libraries.load(lib_path, link=True) as (data_from, data_to):
            data_to.objects = ["LibCrate"]
        linked = data_to.objects[0]
        self.assertIsNotNone(linked.library)
        self.assertFalse(components.is_writable(linked))
        with self.assertRaises(components.ReadOnlyObjectError) as cm:
            components.add_component(linked, "Spin")
        self.assertIn("linked from", str(cm.exception))
        self.assertIsInstance(cm.exception, ValueError)
        self.assertEqual(components.list_components(linked), [])
        # Linked objects still get a stable (derived) ID without being modified.
        self.assertEqual(ids.ensure_id(linked), ids.ensure_id(linked))
        self.assertIsNone(linked.get("gs_id"))
