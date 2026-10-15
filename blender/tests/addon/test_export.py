# SPDX-License-Identifier: GPL-3.0-or-later
"""Scene export (§4): GLB extras, manifest contents, file layout, no side effects."""

import json
import pathlib
import struct
import tempfile

import bpy

from gamestudio.core import components, export, ids, project

import helpers


class ExportTest(helpers.ProjectTestCase):
    def build(self):
        scene = bpy.context.scene
        scene.name = "main"
        mesh = helpers.new_mesh()
        self.player = helpers.new_object("Player", mesh, location=(0, 0, 1))
        components.add_component(self.player, "RigidBody", {"type": "kinematic"})
        components.add_component(self.player, "Collider", {"shape": "capsule"})
        components.add_component(self.player, "CharacterController")
        self.crate = helpers.new_object("Crate", mesh, location=(0, 0, 4))
        self.lamp = helpers.new_object("CrateLamp", mesh, parent=self.crate, location=(0, 0, 0.8))
        self.coin = helpers.new_object("Coin", mesh, location=(2, 3, 1))
        components.add_component(self.coin, "Coin", {"value": 5, "magnet": self.player})
        components.add_component(self.coin, "Spin", {"speed": 2})
        components.set_enabled(self.coin, "Spin", False)
        self.camera = helpers.new_object("Camera", bpy.data.cameras.new("Camera"), location=(0, -12, 7))
        scene.camera = self.camera
        components.add_component(self.camera, "FollowCamera", {"target": "Player"})

        prefab = bpy.data.collections.new("Prefab")
        prefab.objects.link(bpy.data.objects.new("PrefabPart", mesh))
        self.instancer = helpers.new_object("PrefabInstance", None, location=(5, 0, 0))
        self.instancer.instance_type = "COLLECTION"
        self.instancer.instance_collection = prefab

        world = bpy.data.worlds.new("World")
        world.color = (0.9, 0.9, 0.9)  # viewport color; the Background node wins
        background = next(n for n in world.node_tree.nodes if n.type == "BACKGROUND")
        background.inputs["Color"].default_value = (0.1, 0.2, 0.3, 1.0)
        scene.world = world
        bpy.context.view_layer.update()

    def read_manifest(self, result):
        with open(result["manifest"], "r", encoding="utf-8") as f:
            return json.load(f)

    def test_glb_extras_and_manifest(self):
        self.build()
        result = export.export_scene()
        export_dir = self.root / ".gamestudio" / "export"
        self.assertEqual(pathlib.Path(result["glb"]), export_dir / "main.glb")
        self.assertEqual(pathlib.Path(result["manifest"]), export_dir / "main.scene.json")
        self.assertEqual(result["rev"], 1)
        self.assertEqual(sorted(p.name for p in export_dir.iterdir()), ["main.glb", "main.scene.json"])

        gltf, _chunks = export.read_glb(result["glb"])
        nodes = {n["name"]: n for n in gltf["nodes"]}
        scene_objects = list(bpy.context.scene.objects)
        for obj in scene_objects:
            self.assertEqual(nodes[obj.name]["extras"], {"gs_id": ids.get_id(obj)}, obj.name)
        self.assertNotIn("extras", nodes["PrefabPart"])  # instance contents are not entities

        manifest = self.read_manifest(result)
        self.assertEqual(manifest["format"], "gamestudio.scene")
        self.assertEqual(manifest["formatVersion"], 2)
        self.assertEqual((manifest["animations"], manifest["prefabs"], manifest["assets"]), ([], [], {}))
        self.assertEqual(manifest["scene"], "main")
        self.assertEqual(manifest["rev"], 1)
        self.assertEqual(manifest["glb"], "main.glb")
        self.assertTrue(manifest["exportedAt"].endswith("Z"))
        self.assertEqual(manifest["blenderVersion"], bpy.app.version_string.split(" ")[0])
        settings = manifest["settings"]
        self.assertTrue(all(abs(a - b) < 1e-5 for a, b in zip(settings["gravity"], [0, -9.81, 0])))
        self.assertTrue(all(abs(a - b) < 1e-5 for a, b in zip(settings["background"], [0.1, 0.2, 0.3])))
        self.assertEqual(settings["activeCamera"], ids.get_id(self.camera))
        self.assertEqual((settings["toneMapping"], settings["exposure"]), ("agx", 0.0))  # a new scene's AgX
        self.assertNotIn("materials", manifest)  # no material has a game shader

        entities = {e["id"]: e for e in manifest["entities"]}
        self.assertEqual(set(entities), {ids.get_id(o) for o in scene_objects})
        for entity in entities.values():
            self.assertEqual(set(entity), {"id", "name", "type", "parent", "components"})
        player = entities[ids.get_id(self.player)]
        self.assertEqual((player["name"], player["type"], player["parent"]), ("Player", "MESH", None))
        self.assertEqual(player["components"], {
            "RigidBody": {"type": "kinematic", "mass": 1.0, "linearDamping": 0.0, "angularDamping": 0.05,
                          "gravityScale": 1.0, "lockRotation": False, "ccd": False},
            "Collider": {"shape": "capsule", "friction": 0.5, "restitution": 0.0, "sensor": False},
            "CharacterController": {"speed": 5.0, "jumpSpeed": 6.0, "gravity": 20.0, "turnSpeed": 10.0},
        })
        self.assertEqual(entities[ids.get_id(self.lamp)]["parent"], ids.get_id(self.crate))
        coin = entities[ids.get_id(self.coin)]
        self.assertEqual(coin["components"], {"Coin": {"value": 5, "rarity": "common", "magnet": ids.get_id(self.player)}})
        camera = entities[ids.get_id(self.camera)]
        self.assertEqual(camera["type"], "CAMERA")
        self.assertEqual(camera["components"]["FollowCamera"],
                         {"target": ids.get_id(self.player), "offset": [0.0, 4.0, 8.0], "smoothing": 5.0,
                          "lookAtOffset": [0.0, 1.0, 0.0]})
        self.assertEqual(entities[ids.get_id(self.instancer)]["type"], "EMPTY")

        second = export.export_scene(force=True)  # unforced, an unchanged scene reuses rev 1 (test_journal.py)
        self.assertEqual(second["rev"], 2)
        self.assertEqual(bpy.context.scene["gs_rev"], 2)
        self.assertEqual(self.read_manifest(second)["rev"], 2)

    def test_glb_binary_layout(self):
        self.build()
        result = export.export_scene()
        data = pathlib.Path(result["glb"]).read_bytes()
        magic, version, length = struct.unpack_from("<4sII", data, 0)
        self.assertEqual((magic, version, length), (b"glTF", 2, len(data)))
        json_len, json_type = struct.unpack_from("<II", data, 12)
        self.assertEqual(json_type, export.CHUNK_JSON)
        self.assertEqual(json_len % 4, 0)
        json_bytes = data[20:20 + json_len]
        self.assertEqual(json_bytes, json_bytes.rstrip(b" ") + b" " * (len(json_bytes) - len(json_bytes.rstrip(b" "))))
        json.loads(json_bytes)
        bin_len, bin_type = struct.unpack_from("<II", data, 20 + json_len)
        self.assertEqual(bin_type, export.CHUNK_BIN)
        bin_bytes = data[28 + json_len:28 + json_len + bin_len]

        # The BIN chunk must be the exporter's, byte for byte.
        with tempfile.TemporaryDirectory(dir=helpers.tmp_base()) as tmp:
            raw = pathlib.Path(tmp) / "raw.glb"
            export._run_gltf_export(bpy.context.scene, str(raw))
            _gltf, chunks = export.read_glb(raw)
        self.assertEqual(chunks, [(export.CHUNK_BIN, bin_bytes)])

    def test_build_glb_pads_json_with_spaces(self):
        blob = export.build_glb({"asset": {"version": "2.0"}, "x": "é"}, [(export.CHUNK_BIN, b"\x01\x02\x03\x04")])
        with tempfile.NamedTemporaryFile(suffix=".glb", dir=helpers.tmp_base(), delete=False) as f:
            f.write(blob)
        gltf, chunks = export.read_glb(f.name)
        pathlib.Path(f.name).unlink()
        self.assertEqual(gltf["x"], "é")
        self.assertEqual(chunks, [(export.CHUNK_BIN, b"\x01\x02\x03\x04")])
        self.assertEqual(struct.unpack_from("<I", blob, 12)[0] % 4, 0)

    def test_selection_active_and_mode_are_preserved(self):
        self.build()
        view_layer = bpy.context.view_layer
        for obj in view_layer.objects:
            obj.select_set(obj in (self.coin, self.crate))
        view_layer.objects.active = self.crate
        with bpy.context.temp_override(active_object=self.crate, object=self.crate):
            bpy.ops.object.mode_set(mode="EDIT")
        self.assertEqual(self.crate.mode, "EDIT")
        export.export_scene()
        self.assertEqual(view_layer.objects.active, self.crate)
        self.assertEqual({o.name for o in view_layer.objects if o.select_get()}, {"Coin", "Crate"})
        self.assertEqual(self.crate.mode, "EDIT")
        with bpy.context.temp_override(active_object=self.crate, object=self.crate):
            bpy.ops.object.mode_set(mode="OBJECT")

    def test_duplicates_are_repaired_before_export(self):
        self.build()
        ids.ensure_id(self.player)
        copy = self.player.copy()
        bpy.context.scene.collection.objects.link(copy)
        result = export.export_scene()
        self.assertEqual(result["repaired"], 1)
        manifest = self.read_manifest(result)
        id_list = [e["id"] for e in manifest["entities"]]
        self.assertEqual(len(id_list), len(set(id_list)))
        self.assertIn(copy.name, [e["name"] for e in manifest["entities"]])

    def test_without_project_raises_clear_error(self):
        project.set_project_root_override(None)
        with self.assertRaises(export.ExportError) as cm:
            export.export_scene()
        self.assertIn("Save the .blend inside a GameStudio project (folder with gamestudio.json)", str(cm.exception))

    def test_unsafe_scene_names(self):
        bpy.context.scene.name = "My Level: 1"
        self.assertEqual(export.export_name(bpy.context.scene), "My_Level_1")
