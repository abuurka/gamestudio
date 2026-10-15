# SPDX-License-Identifier: GPL-3.0-or-later
"""Stable IDs (§2): assignment, lookup, and duplicate repair after copies."""

import types

import bpy

from gamestudio.core import ids

import helpers


class IdTest(helpers.ProjectTestCase):
    def test_ensure_id_format_and_owner(self):
        obj = helpers.new_object("Cube")
        gs_id = ids.ensure_id(obj)
        self.assertRegex(gs_id, r"^gs_[0-9a-f]{12}$")
        self.assertEqual(obj["gs_id"], gs_id)
        self.assertEqual(obj["gs_id_owner"], "Cube")
        self.assertEqual(ids.ensure_id(obj), gs_id)
        self.assertEqual(ids.get_id(obj), gs_id)

    def test_invalid_ids_are_replaced(self):
        obj = helpers.new_object("Cube")
        obj["gs_id"] = "player-1"
        self.assertIsNone(ids.get_id(obj))
        self.assertRegex(ids.ensure_id(obj), r"^gs_[0-9a-f]{12}$")

    def test_find_by_id(self):
        a = helpers.new_object("A")
        b = helpers.new_object("B")
        id_a, id_b = ids.ensure_id(a), ids.ensure_id(b)
        self.assertEqual(ids.find_by_id(id_a), a)
        b.name = "Renamed"
        self.assertEqual(ids.find_by_id(id_b), b)
        bpy.data.objects.remove(a)
        self.assertIsNone(ids.find_by_id(id_a))  # stale cache entry is detected, not returned
        self.assertIsNone(ids.find_by_id("gs_000000000000"))
        self.assertIsNone(ids.find_by_id("not-an-id"))

    def test_cache_survives_invalidation(self):
        a = helpers.new_object("A")
        gs_id = ids.ensure_id(a)
        ids.invalidate_cache()
        self.assertEqual(ids.find_by_id(gs_id), a)
        # IDs written by other code (e.g. a file load) are found on a miss.
        b = helpers.new_object("B")
        b["gs_id"] = "gs_abcdefabcdef"
        self.assertEqual(ids.find_by_id("gs_abcdefabcdef"), b)

    def test_copy_gets_a_new_id(self):
        original = helpers.new_object("Crate")
        gs_id = ids.ensure_id(original)
        copy = original.copy()
        bpy.context.scene.collection.objects.link(copy)
        self.assertEqual(copy.get("gs_id"), gs_id)  # Blender copied the property
        self.assertEqual(ids.repair_duplicate_ids(), 1)
        self.assertEqual(ids.get_id(original), gs_id)
        self.assertNotEqual(ids.get_id(copy), gs_id)
        self.assertEqual(copy["gs_id_owner"], copy.name)
        self.assertEqual(ids.find_by_id(gs_id), original)
        self.assertEqual(ids.repair_duplicate_ids(), 0)

    def test_owner_name_rule_without_session_tracking(self):
        original = helpers.new_object("Crate")
        gs_id = ids.ensure_id(original)
        copies = []
        for _ in range(3):
            copy = original.copy()
            bpy.context.scene.collection.objects.link(copy)
            copies.append(copy)
        ids._owner_uid.clear()  # as after reopening the file
        self.assertEqual(ids.repair_duplicate_ids(bpy.context.scene), 3)
        self.assertEqual(ids.get_id(original), gs_id)
        self.assertEqual(len({ids.get_id(o) for o in copies + [original]}), 4)

    def test_session_tracking_beats_renames(self):
        original = helpers.new_object("Crate")
        gs_id = ids.ensure_id(original)
        original.name = "Hero"  # owner name no longer matches
        copy = original.copy()
        bpy.context.scene.collection.objects.link(copy)
        copy.name = "Crate"     # the copy now matches the stored owner name
        self.assertEqual(ids.repair_duplicate_ids(), 1)
        self.assertEqual(ids.get_id(original), gs_id)
        self.assertEqual(original["gs_id_owner"], "Hero")  # owner record follows the rename

    def test_rename_alone_never_reassigns(self):
        obj = helpers.new_object("Crate")
        gs_id = ids.ensure_id(obj)
        obj.name = "Barrel"
        ids._owner_uid.clear()
        self.assertEqual(ids.repair_duplicate_ids(), 0)
        self.assertEqual(ids.get_id(obj), gs_id)


class LinkedIdTest(helpers.ProjectTestCase):
    """Objects linked from a library: derived IDs, and duplicates of their stored IDs."""

    def test_derived_ids_are_the_same_on_every_os(self):
        def linked(path, name="Barrel"):
            return types.SimpleNamespace(library=types.SimpleNamespace(filepath=path), name=name)

        windows = ids._linked_id(linked(r"//..\assets\props.blend"))
        self.assertEqual(ids._linked_id(linked("//../assets/props.blend")), windows)
        self.assertEqual(ids._linked_id(linked("//../assets/./kit/../props.blend")), windows)
        self.assertNotEqual(ids._linked_id(linked("//../assets/props.blend", "Crate")), windows)
        self.assertEqual(ids.library_key(types.SimpleNamespace(filepath=r"C:\games\a\..\lib.blend")),
                         "C:/games/lib.blend")

    def test_the_linked_object_keeps_its_id(self):
        barrel = helpers.new_object("Barrel", helpers.new_mesh("BarrelMesh"))
        gs_id = ids.ensure_id(barrel)
        path = self.root / "props.blend"
        bpy.data.libraries.write(str(path), {barrel})
        helpers.reset_scene()
        with bpy.data.libraries.load(str(path), link=True) as (_src, dst):
            dst.objects = ["Barrel"]
        linked = dst.objects[0]
        scene = bpy.context.scene
        scene.collection.objects.link(linked)
        local = linked.copy()  # a local copy carries the stored ID and owner name
        scene.collection.objects.link(local)
        self.assertEqual((ids.get_id(local), local["gs_id_owner"]), (gs_id, "Barrel"))
        local.name = "Barrel"  # the copy even has the owner's name (the linked one is "Barrel [props.blend]")
        ids._owner_uid[gs_id] = local.session_uid  # ...and is the one tracked in this session
        self.assertEqual(ids.repair_duplicate_ids(scene), 1)
        self.assertEqual(ids.get_id(linked), gs_id)
        self.assertNotEqual(ids.get_id(local), gs_id)
        self.assertEqual(ids.find_by_id(gs_id), linked)
