# SPDX-License-Identifier: GPL-3.0-or-later
"""Export with linked library data (§2, §4.4): name collisions with local data, duplicate prefab
names, objects in excluded collections, and missing library files."""

import json
import pathlib

import bpy

from gamestudio.core import components, export, ids

import helpers


def node_ids(gltf):
    return [(n.get("name"), (n.get("extras") or {}).get("gs_id"), n.get("translation")) for n in gltf["nodes"]]


class LinkedExportTest(helpers.ProjectTestCase):
    def setUp(self):
        super().setUp()
        self.library = self.root / "assets" / "props.blend"
        self.library.parent.mkdir()
        barrel = helpers.new_object("Barrel", helpers.new_mesh("BarrelMesh"), location=(5, 0, 0))
        components.add_component(barrel, "Spin", {"speed": 4})
        crate = bpy.data.collections.new("Crate")
        export.set_prefab(crate)
        body = helpers.new_object("CrateBody", helpers.new_mesh("CrateMesh"), collection=crate)
        components.add_component(body, "Spin", {"speed": 7})
        self.library_ids = {"Barrel": ids.ensure_id(barrel), "CrateBody": ids.ensure_id(body)}
        bpy.data.libraries.write(str(self.library), {barrel, crate})
        helpers.reset_scene()
        bpy.context.scene.name = "main"

    def link(self, objects=(), collections=()):
        with bpy.data.libraries.load(str(self.library), link=True) as (_src, dst):
            dst.objects = list(objects)
            dst.collections = list(collections)
        return dst

    def manifest(self, result):
        return json.loads(pathlib.Path(result["manifest"]).read_text(encoding="utf-8"))

    def test_local_and_linked_objects_with_one_name_keep_their_own_ids(self):
        local = helpers.new_object("Barrel", helpers.new_mesh("LocalMesh"), location=(-5, 0, 0))
        local_id = ids.ensure_id(local)
        linked = self.link(objects=["Barrel"]).objects[0]
        bpy.context.scene.collection.objects.link(linked)

        result = export.export_scene()
        gltf, _chunks = export.read_glb(result["glb"])
        by_id = {gs_id: (name, translation) for name, gs_id, translation in node_ids(gltf)}
        self.assertEqual(by_id[local_id], ("Barrel", [-5, 0, 0]))
        self.assertEqual(by_id[self.library_ids["Barrel"]], ("Barrel", [5, 0, 0]))
        entities = {e["id"]: e for e in self.manifest(result)["entities"]}
        self.assertEqual(entities[self.library_ids["Barrel"]]["components"], {"Spin": {"speed": 4.0,
                                                                                       "axis": [0.0, 1.0, 0.0]}})
        self.assertEqual(entities[local_id]["components"], {})
        self.assertEqual(local.name, "Barrel")  # the temporary export name is gone
        self.assertTrue(any("2 exported objects are named 'Barrel'" in w for w in result["warnings"]),
                        result["warnings"])

    def test_objects_of_excluded_collections_are_not_exported(self):
        local = helpers.new_object("Barrel", helpers.new_mesh("LocalMesh"), location=(-5, 0, 0))
        local_id = ids.ensure_id(local)
        shelf = bpy.data.collections.new("Shelf")
        bpy.context.scene.collection.children.link(shelf)
        shelf.objects.link(self.link(objects=["Barrel"]).objects[0])
        bpy.context.view_layer.layer_collection.children["Shelf"].exclude = True

        result = export.export_scene()
        self.assertEqual([(e["name"], e["id"]) for e in self.manifest(result)["entities"]], [("Barrel", local_id)])
        gltf, _chunks = export.read_glb(result["glb"])
        roots = gltf["scenes"][0]["nodes"]
        self.assertEqual([node_ids(gltf)[i] for i in roots], [("Barrel", local_id, [-5, 0, 0])])
        self.assertFalse(any("named 'Barrel'" in w for w in result["warnings"]))

    def test_local_and_linked_prefabs_with_one_name(self):
        linked = self.link(collections=["Crate"]).collections[0]
        local = bpy.data.collections.new("Crate")
        export.set_prefab(local)
        body = helpers.new_object("CrateBody", helpers.new_mesh("LocalCrateMesh"), collection=local)
        local_id = ids.ensure_id(body)
        for name, coll in (("LinkedCrate", linked), ("LocalCrate", local)):
            inst = helpers.new_object(name, None)
            inst.instance_type = "COLLECTION"
            inst.instance_collection = coll
        self.assertEqual(export.prefab_collections(), [local, linked])

        result = export.export_scene()
        manifest = self.manifest(result)
        templates = {t["name"]: t for t in manifest["prefabs"]}
        self.assertEqual(set(templates), {"Crate", "Crate [props.blend]"})
        self.assertEqual([(e["id"], e["components"]) for e in templates["Crate"]["entities"]], [(local_id, {})])
        (linked_body,) = templates["Crate [props.blend]"]["entities"]
        self.assertEqual(linked_body["id"], self.library_ids["CrateBody"])
        self.assertEqual(linked_body["components"]["Spin"]["speed"], 7.0)
        entities = {e["name"]: e for e in manifest["entities"]}
        self.assertEqual(entities["LinkedCrate"]["prefab"], "Crate [props.blend]")
        self.assertEqual(entities["LocalCrate"]["prefab"], "Crate")
        prefab_gltf, _chunks = export.read_glb(pathlib.Path(result["glb"]).with_name("main.prefabs.glb"))
        tagged = {gs_id: name for name, gs_id, _t in node_ids(prefab_gltf) if gs_id}
        self.assertEqual(tagged, {local_id: "CrateBody", self.library_ids["CrateBody"]: "CrateBody"})
        self.assertTrue(any("Prefab name 'Crate' is used by" in w for w in result["warnings"]), result["warnings"])

    def test_missing_library_is_an_export_error(self):
        linked = self.link(collections=["Crate"]).collections[0]
        inst = helpers.new_object("CrateInstance", None)
        inst.instance_type = "COLLECTION"
        inst.instance_collection = linked
        blend = self.root / "main.blend"
        bpy.ops.wm.save_as_mainfile(filepath=str(blend))
        self.assertEqual(export.export_scene()["prefabs"], ["Crate"])
        self.library.unlink()
        bpy.ops.wm.open_mainfile(filepath=str(blend))
        with self.assertRaises(export.ExportError) as caught:
            export.export_scene()
        message = str(caught.exception)
        self.assertIn("props.blend", message)
        self.assertIn("CrateInstance", message)
