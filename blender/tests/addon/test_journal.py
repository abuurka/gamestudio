# SPDX-License-Identifier: GPL-3.0-or-later
"""The export journal (journal.py): what export_scene() reuses, rewrites as a manifest only, or exports in full."""

import hashlib
import json
import os
import shutil
from unittest import mock

import bpy

from gamestudio.bridge import api
from gamestudio.core import components, convert, export, ids, live

import helpers


SOUND = {
    "name": "Sound", "category": "Audio",
    "fields": [{"name": "clip", "type": "asset", "default": "", "extensions": ["wav"]}],
}


def principled(mat):
    return next(n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED")


def cube(name):
    mesh = bpy.data.meshes.new(name)
    verts = [(x, y, z) for x in (-1, 1) for y in (-1, 1) for z in (-1, 1)]
    faces = [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)]
    mesh.from_pydata(verts, [], faces)
    return mesh


def same_trs(test, node, transform, places=1e-4):
    """A glTF node's TRS against a manifest "transform" (q and -q are the same rotation)."""
    test.assertLess(max(abs(a - b) for a, b in zip(node.get("translation", [0, 0, 0]), transform["p"])), places)
    test.assertLess(max(abs(a - b) for a, b in zip(node.get("scale", [1, 1, 1]), transform["s"])), places)
    rotation = node.get("rotation", [0, 0, 0, 1])
    test.assertLess(min(max(abs(a - b) for a, b in zip(rotation, transform["r"])),
                        max(abs(a + b) for a, b in zip(rotation, transform["r"]))), places)


class JournalTestCase(helpers.ProjectTestCase):
    registry_data = helpers.modified_registry(lambda data: data["components"].append(SOUND))

    def setUp(self):
        super().setUp()
        scene = bpy.context.scene
        scene.name = "main"
        scene.world = bpy.data.worlds.new("World")
        self.export_dir = self.root / ".gamestudio" / "export"
        self.paint = bpy.data.materials.new("Paint")
        mesh = helpers.new_mesh("CrateMesh")
        mesh.materials.append(self.paint)
        self.crate = helpers.new_object("Crate", mesh, location=(0, 0, 1))
        self.lid = helpers.new_object("Lid", helpers.new_mesh("LidMesh"), parent=self.crate, location=(0, 0, 0.5))
        self.coin = helpers.new_object("Coin", helpers.new_mesh("CoinMesh"), location=(2, 3, 1))
        components.add_component(self.coin, "Spin", {"speed": 2})
        self.camera = helpers.new_object("Camera", bpy.data.cameras.new("Camera"), location=(0, -10, 4))
        self.camera.rotation_euler = (1.2, 0.0, 0.3)
        scene.camera = self.camera
        self.lamp = helpers.new_object("Lamp", bpy.data.lights.new("Lamp", "POINT"), location=(1, -1, 5))
        self.prefab = bpy.data.collections.new("Barrel")
        export.set_prefab(self.prefab)
        self.prefab.instance_offset = (0, 0, 0.5)
        self.barrel_body = bpy.data.objects.new("BarrelBody", helpers.new_mesh("BarrelMesh"))
        self.prefab.objects.link(self.barrel_body)
        components.add_component(self.barrel_body, "Coin", {"value": 1})
        self.barrel = helpers.new_object("BarrelInstance", None, location=(4, 0, 0))
        self.barrel.instance_type = "COLLECTION"
        self.barrel.instance_collection = self.prefab
        bpy.context.view_layer.update()

    # --- Helpers ----------------------------------------------------------------------

    def glbs(self) -> dict:
        return {p.name: p.read_bytes() for p in self.export_dir.glob("*.glb")}

    def stamps(self) -> dict:
        return {p.name: p.stat().st_mtime_ns for p in self.export_dir.iterdir() if p.is_file()}

    def manifest(self) -> dict:
        return json.loads((self.export_dir / "main.scene.json").read_text(encoding="utf-8"))

    def entity(self, manifest, obj) -> dict:
        return next(e for e in manifest["entities"] if e["id"] == ids.get_id(obj))

    def export_without_exporter(self, tier: str) -> dict:
        """export_scene(), which must be a reuse or layout export (the glTF exporter does not run)."""
        with mock.patch.object(export, "_gltf", wraps=export._gltf) as gltf:
            result = export.export_scene()
        self.assertEqual(result["tier"], tier, result.get("reason"))
        self.assertFalse(gltf.called)
        return result

    def assert_full(self, change, reason=None):
        export.export_scene()  # the snapshot to compare with
        change()
        result = export.export_scene()
        self.assertEqual(result["tier"], "full", f"{reason}: {result.get('reason')}")
        if reason is not None:
            self.assertIn(reason, result["reason"])
        return result


class ReuseAndLayoutTest(JournalTestCase):
    def test_reuse_when_nothing_changed(self):
        first = export.export_scene()
        self.assertEqual((first["tier"], first["rev"]), ("full", 1))
        self.assertIn("first export", first["reason"])
        glbs, stamps = self.glbs(), self.stamps()
        again = self.export_without_exporter("reuse")
        self.assertEqual((again["tier"], again["rev"]), ("reuse", 1))
        self.assertEqual(set(again) | {"reason"}, set(first))  # the same shape
        self.assertEqual((again["entities"], again["prefabs"]), (first["entities"], first["prefabs"]))
        self.assertEqual((self.glbs(), self.stamps()), (glbs, stamps))  # nothing written
        self.assertEqual(bpy.context.scene["gs_rev"], 1)
        self.assertEqual(export.last_export()["tier"], "reuse")
        # Touched but with the same bytes: still the export's files.
        glb = self.export_dir / "main.glb"
        os.utime(glb, ns=(stamps["main.glb"] + 3_000_000_000, stamps["main.glb"] + 3_000_000_000))
        self.export_without_exporter("reuse")

    def test_moving_objects_rewrites_only_the_manifest(self):
        first = export.export_scene()
        files = self.manifest()["files"]
        glbs = self.glbs()
        moved = {self.crate: (1, 2, 3), self.camera: (3, -8, 5), self.lamp: (0, 0, 7), self.barrel: (6, 1, 0)}
        for obj, location in moved.items():
            obj.location = location
        self.camera.rotation_euler.z = 0.8
        self.lamp.rotation_euler.x = 0.4
        result = self.export_without_exporter("layout")
        self.assertEqual((result["tier"], result["rev"]), ("layout", first["rev"] + 1), result.get("reason"))
        self.assertNotIn("reason", result)
        self.assertEqual(self.glbs(), glbs)  # byte-identical
        manifest = self.manifest()
        self.assertEqual((manifest["rev"], bpy.context.scene["gs_rev"]), (result["rev"], result["rev"]))
        self.assertEqual(manifest["files"], files)
        for obj in moved:
            want = convert.transform_item(obj, ids.get_id(obj))
            got = self.entity(manifest, obj)["transform"]
            self.assertEqual(set(got), {"p", "r", "s"})
            for key in "prs":
                self.assertLess(max(abs(a - b) for a, b in zip(got[key], want[key])), 1e-6, (obj.name, key))
        for obj in (self.lid, self.coin):  # the lid moved with its parent, but not relative to it
            self.assertNotIn("transform", self.entity(manifest, obj))

        self.export_without_exporter("reuse")
        # The transforms are what a full export writes into the nodes.
        full = export.export_scene(force=True)
        gltf, _chunks = export.read_glb(full["glb"])
        nodes = {n["extras"]["gs_id"]: n for n in gltf["nodes"] if "gs_id" in (n.get("extras") or {})}
        for obj in moved:
            same_trs(self, nodes[ids.get_id(obj)], self.entity(manifest, obj)["transform"])
        self.assertFalse(any("transform" in e for e in self.manifest()["entities"]))

    def test_moving_back_drops_the_transform(self):
        export.export_scene()
        self.crate.location.x = 5
        self.assertEqual(export.export_scene()["tier"], "layout")
        self.assertIn("transform", self.entity(self.manifest(), self.crate))
        self.crate.location.x = 0
        result = export.export_scene()
        self.assertEqual((result["tier"], result["rev"]), ("layout", 3))
        self.assertNotIn("transform", self.entity(self.manifest(), self.crate))

    def test_component_edits_rewrite_only_the_manifest(self):
        first = export.export_scene()
        glbs = self.glbs()
        components.set_field(self.coin, "Spin", "speed", 5)
        components.set_enabled(self.coin, "Spin", False)
        components.add_component(self.crate, "Coin", {"value": 3})
        components.set_field(self.barrel_body, "Coin", "value", 9)  # a prefab template's entity
        # What a field edit in the UI also does: tag the object and its mesh for a geometry update.
        self.coin.update_tag(refresh={"OBJECT", "DATA", "TIME"})
        self.coin.data.update_tag()
        bpy.context.view_layer.update()
        result = self.export_without_exporter("layout")
        self.assertEqual((result["tier"], result["rev"]), ("layout", first["rev"] + 1))
        self.assertEqual(self.glbs(), glbs)
        manifest = self.manifest()
        self.assertEqual(self.entity(manifest, self.coin)["components"], {})
        self.assertEqual(self.entity(manifest, self.crate)["components"],
                         {"Coin": {"value": 3, "rarity": "common", "magnet": None}})
        body = next(e for e in manifest["prefabs"][0]["entities"] if e["name"] == "BarrelBody")
        self.assertEqual(body["components"]["Coin"]["value"], 9)
        self.assertFalse(any("transform" in e for e in manifest["entities"]))

    def test_a_geometry_edit_after_a_component_edit_is_not_missed(self):
        export.export_scene()
        components.set_field(self.coin, "Spin", "speed", 7)
        self.coin.data.vertices[0].co.x = 0.4
        self.coin.data.update()
        self.coin.update_tag(refresh={"OBJECT", "DATA"})
        result = export.export_scene()
        self.assertEqual(result["tier"], "full")
        self.assertIn("Coin", result["reason"])

    def test_scene_settings_rewrite_only_the_manifest(self):
        export.export_scene()
        scene = bpy.context.scene
        scene.gravity = (0, 0, -3)
        scene.world["gs_sky_colors"] = {"horizon": "#BFE3F2", "zenith": "#3C8DDB", "ground": "#8A9A88"}
        result = self.export_without_exporter("layout")
        self.assertEqual(result["tier"], "layout", result.get("reason"))
        settings = self.manifest()["settings"]
        self.assertAlmostEqual(settings["gravity"][1], -3.0, places=5)
        self.assertIn("sky", settings)
        scene.camera = None
        self.export_without_exporter("layout")
        self.assertIsNone(self.manifest()["settings"]["activeCamera"])

    def test_changed_asset_files_rewrite_only_the_manifest(self):
        assets = self.root / "assets"
        assets.mkdir()
        (assets / "beep.wav").write_bytes(b"RIFF one")
        components.add_component(self.coin, "Sound", {"clip": "assets/beep.wav"})
        export.export_scene()
        url = self.manifest()["assets"]["assets/beep.wav"]
        stamp = (assets / "beep.wav").stat().st_mtime_ns + 5_000_000_000
        (assets / "beep.wav").write_bytes(b"RIFF two!")
        os.utime(assets / "beep.wav", ns=(stamp, stamp))
        result = self.export_without_exporter("layout")
        self.assertEqual(result["tier"], "layout")
        self.assertNotEqual(self.manifest()["assets"]["assets/beep.wav"], url)
        self.assertEqual((self.export_dir / "files" / "assets" / "beep.wav").read_bytes(), b"RIFF two!")

    def test_prefabs_off_the_scene_are_compared_but_reused(self):
        kit = bpy.data.collections.new("Kit")  # no depsgraph evaluates it: its digests are compared instead
        export.set_prefab(kit)
        rig = bpy.data.objects.new("KitRig", bpy.data.armatures.new("KitRig"))
        kit.objects.link(rig)
        kit.objects.link(bpy.data.objects.new("KitCam", bpy.data.cameras.new("KitCam")))
        body = bpy.data.objects.new("KitBody", helpers.new_mesh("KitMesh"))
        body.data.materials.append(bpy.data.materials.new("KitPaint"))
        kit.objects.link(body)
        export.export_scene()
        self.export_without_exporter("reuse")
        bpy.data.cameras["KitCam"].lens = 85
        self.assertEqual(export.export_scene()["tier"], "full")

    def test_force_and_export_scene_to_are_full(self):
        export.export_scene()
        with mock.patch.object(export, "_gltf", wraps=export._gltf) as gltf:
            result = export.export_scene(force=True)
        self.assertTrue(gltf.called)
        self.assertEqual((result["tier"], result["reason"], result["rev"]), ("full", "forced", 2))
        other = self.root / "elsewhere"
        self.assertNotIn("tier", export.export_scene_to(other))
        self.assertEqual(export.export_scene()["tier"], "reuse")  # an export elsewhere changes nothing here

    def test_manifest_names_the_hash_of_every_glb(self):
        export.export_scene()
        manifest = self.manifest()
        self.assertEqual(set(manifest["files"]), {"main.glb", "main.prefabs.glb"})
        for name, digest in manifest["files"].items():
            self.assertEqual(digest, hashlib.sha1((self.export_dir / name).read_bytes()).hexdigest()[:16])
        self.assertEqual(manifest["glb"], "main.glb")
        self.assertEqual(manifest["prefabs"][0]["glb"], "main.prefabs.glb")


class FullExportTest(JournalTestCase):
    def test_data_changes_need_a_full_export(self):
        def edit_mesh():
            self.crate.data.vertices[0].co.x = 0.25
            self.crate.data.update()

        def add_modifier():
            self.crate.modifiers.new("Bevel", "BEVEL")

        def change_modifier():
            self.crate.modifiers["Bevel"].width = 0.3

        def edit_material():
            principled(self.paint).inputs["Base Color"].default_value = (1.0, 0.0, 0.0, 1.0)

        def change_light():
            self.lamp.data.energy = 250.0

        def keyframe():
            self.coin.keyframe_insert("location", frame=1)

        def edit_keys():
            self.coin.location.z = 4.0
            self.coin.keyframe_insert("location", frame=20)

        def change_world():
            self.scene_world().color = (0.9, 0.1, 0.1)

        for label, change, reason in (("mesh edit", edit_mesh, "Crate"), ("new modifier", add_modifier, "Crate"),
                                      ("modifier setting", change_modifier, "Crate"),
                                      ("material node", edit_material, None), ("light", change_light, "Lamp"),
                                      ("new action", keyframe, None), ("action keys", edit_keys, None),
                                      ("world", change_world, None)):
            with self.subTest(label):
                self.assert_full(change, reason)

    def scene_world(self):
        return bpy.context.scene.world

    def test_object_changes_need_a_full_export(self):
        def add():
            helpers.new_object("Spawned", helpers.new_mesh("SpawnedMesh"), location=(0, 5, 0))

        def delete():
            bpy.data.objects.remove(bpy.data.objects["Spawned"])

        def rename():
            self.lid.name = "Hatch"

        def reparent():
            self.lid.parent = self.coin

        def hide():
            self.coin.hide_set(True)

        def unmark_prefab():
            export.set_prefab(self.prefab, False)

        for label, change, reason in (("new object", add, "Spawned"), ("deleted object", delete, "Spawned"),
                                      ("rename", rename, "name"), ("re-parent", reparent, "parent"),
                                      ("hidden", hide, "visibility"), ("prefab unmarked", unmark_prefab, "prefab")):
            with self.subTest(label):
                self.assert_full(change, reason)

    def test_prefab_contents_need_a_full_export(self):
        gem = bpy.data.collections.new("Gem")  # a prefab nothing in the scene shows: no depsgraph sees it
        export.set_prefab(gem)
        stone = bpy.data.objects.new("GemStone", helpers.new_mesh("GemMesh"))
        gem.objects.link(stone)
        stone.data.materials.append(bpy.data.materials.new("GemPaint"))

        def edit_vertices():
            stone.data.vertices[1].co.z = 0.7

        def move_member():
            stone.location.x = 2.0

        def move_instanced_member():
            self.barrel_body.location.y = 1.5

        def edit_material():
            principled(stone.data.materials[0]).inputs["Roughness"].default_value = 0.1

        for label, change in (("vertices", edit_vertices), ("member moved", move_member),
                              ("instanced member moved", move_instanced_member), ("material", edit_material)):
            with self.subTest(label):
                self.assert_full(change)

    def test_an_object_following_a_moved_one_needs_a_full_export(self):
        offset = helpers.new_object("Offset", None, location=(1, 0, 0))
        arrayed = helpers.new_object("Arrayed", cube("ArrayedMesh"), location=(0, 6, 0))
        array = arrayed.modifiers.new("Array", "ARRAY")
        array.use_relative_offset = False
        array.use_object_offset = True
        array.offset_object = offset
        block = helpers.new_object("Block", cube("BlockMesh"), location=(0, -6, 0))
        cutter = helpers.new_object("Cutter", cube("CutterMesh"), location=(0.5, -6, 0.5))
        block.modifiers.new("Cut", "BOOLEAN").object = cutter
        export.export_scene()
        self.coin.location.x = 3  # nothing depends on it
        self.export_without_exporter("layout")
        self.assert_full(lambda: setattr(offset.location, "x", 2.0), "Arrayed")
        self.assert_full(lambda: setattr(cutter.location, "z", 0.8), "Block")
        # Blender sends geometry updates that change nothing too (the scene's gravity tags every object).
        export.export_scene()
        bpy.context.scene.gravity = (0, 0, -5)
        self.export_without_exporter("layout")

    def test_keys_edited_on_a_muted_track_need_a_full_export(self):
        # The kit convention: every clip but the active one sits on a muted NLA track, which no depsgraph
        # evaluates, so an edit of its keys sends no update.
        for frame, z in ((1, 1.0), (10, 2.0)):
            self.coin.location.z = z
            self.coin.keyframe_insert("location", index=2, frame=frame)
        anim = self.coin.animation_data
        action = anim.action
        anim.action = None
        track = anim.nla_tracks.new()
        track.strips.new("Hop", 1, action)
        track.mute = True
        export.export_scene()
        self.export_without_exporter("reuse")
        fcurve = action.layers[0].strips[0].channelbags[0].fcurves[0]
        fcurve.keyframe_points[1].co.y = 5.0  # what a script (run_python) does
        result = export.export_scene()
        self.assertEqual(result["tier"], "full")
        self.assertIn(action.name, result["reason"])
        self.export_without_exporter("reuse")
        fcurve.keyframe_points[0].interpolation = "LINEAR"
        self.assertEqual(export.export_scene()["tier"], "full")

    def test_settings_and_files_need_a_full_export(self):
        def configure():
            path = self.root / "gamestudio.json"
            config = json.loads(path.read_text(encoding="utf-8"))
            config["export"] = {"maxTextureSize": 512}
            path.write_text(json.dumps(config), encoding="utf-8")
            stamp = path.stat().st_mtime + 2
            os.utime(path, (stamp, stamp))  # read_config caches by mtime

        def delete_glb():
            (self.export_dir / "main.glb").unlink()

        def rewrite_glb():
            path = self.export_dir / "main.prefabs.glb"
            path.write_bytes(path.read_bytes() + b"    ")

        def rewrite_manifest():
            path = self.export_dir / "main.scene.json"
            path.write_text(path.read_text(encoding="utf-8").replace('"Crate"', '"Box"'), encoding="utf-8")

        def frame():
            self.coin.keyframe_insert("location", frame=1)

        for label, change, reason in (("export settings", configure, "export settings"),
                                      ("deleted GLB", delete_glb, "main.glb is missing"),
                                      ("rewritten GLB", rewrite_glb, "main.prefabs.glb"),
                                      ("rewritten manifest", rewrite_manifest, "main.scene.json")):
            with self.subTest(label):
                self.assert_full(change, reason)
        self.assertEqual(export.export_scene(bake="off")["tier"], "full")  # another bake mode
        self.assertEqual(export.export_scene(bake="off")["tier"], "reuse")
        frame()
        export.export_scene(bake="off")
        bpy.context.scene.frame_set(12)  # animated: the frame decides the pose the GLB holds
        self.assertEqual(export.export_scene(bake="off")["tier"], "full")

    def test_images_need_a_full_export(self):
        path = self.root / "albedo.png"
        generated = bpy.data.images.new("Generated", 8, 8)
        generated.filepath_raw = str(path)
        generated.file_format = "PNG"
        generated.save()
        bpy.data.images.remove(generated)
        image = bpy.data.images.load(str(path))
        tex = self.paint.node_tree.nodes.new("ShaderNodeTexImage")
        tex.image = image
        self.paint.node_tree.links.new(tex.outputs["Color"], principled(self.paint).inputs["Base Color"])

        def rewrite_file():
            other = bpy.data.images.new("Other", 16, 16)
            other.generated_color = (1.0, 0.0, 0.0, 1.0)
            other.filepath_raw = str(self.root / "other.png")
            other.file_format = "PNG"
            other.save()
            bpy.data.images.remove(other)
            stamp = path.stat().st_mtime_ns + 5_000_000_000
            shutil.copyfile(self.root / "other.png", path)
            os.utime(path, ns=(stamp, stamp))

        self.assert_full(rewrite_file, "image")

    def test_undo_and_reload_need_a_full_export(self):
        export.export_scene()
        api.undo_push("test: exported")
        self.coin.location.x = 7
        api.undo_push("test: moved")
        with bpy.context.temp_override(window=bpy.context.window_manager.windows[0]):
            self.assertIn("FINISHED", bpy.ops.ed.undo())
        result = export.export_scene()
        self.assertEqual(result["tier"], "full")
        self.assertIn("undo", result["reason"])

        path = self.root / "main.blend"
        bpy.ops.wm.save_as_mainfile(filepath=str(path))
        self.assertEqual(export.export_scene()["tier"], "reuse")
        bpy.ops.wm.open_mainfile(filepath=str(path))
        result = export.export_scene()
        self.assertEqual(result["tier"], "full")
        self.assertIn("first export", result["reason"])


class LiveRebuildTest(JournalTestCase):
    def setUp(self):
        super().setUp()
        self._saved = live.GEOMETRY_DEBOUNCE
        live.GEOMETRY_DEBOUNCE = 0.05
        self.hub = helpers.FakeHub()
        self.client = helpers.connect_global_link(self.hub)
        live.pump()
        self.assertTrue(live.is_active())

    def tearDown(self):
        live.GEOMETRY_DEBOUNCE = self._saved
        helpers.restore_global_link()
        live.pump()  # deactivates
        self.hub.close()
        super().tearDown()

    def pump(self):
        self.client.pump()
        live.pump()

    def settle(self, seconds=0.3):
        helpers.pump_until(lambda: False, [self.pump], seconds)

    def test_rebuilds_reuse_or_rewrite_the_manifest(self):
        first = export.export_scene()
        glbs = self.glbs()
        self.hub.clear()
        reused = live.stats()["reused"]
        live.schedule_rebuild("a change the export does not hold")
        self.settle()
        self.assertEqual(self.hub.messages("scene.load"), [])  # nothing to reload
        self.assertEqual(live.stats()["reused"], reused + 1)

        self.coin.location.x = 6
        bpy.context.view_layer.update()
        live.schedule_rebuild("test")
        ok = helpers.pump_until(lambda: self.hub.messages("scene.load"), [self.pump], 3.0)
        self.assertTrue(ok, [m["t"] for m in self.hub.messages()])
        self.assertEqual(self.hub.messages("scene.load")[0]["rev"], first["rev"] + 1)
        self.assertEqual(export.last_export()["tier"], "layout")
        self.assertEqual(self.glbs(), glbs)
        self.assertIn("transform", self.entity(self.manifest(), self.coin))
