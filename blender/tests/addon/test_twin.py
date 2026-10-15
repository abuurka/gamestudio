# SPDX-License-Identifier: GPL-3.0-or-later
"""The scene twin (<scene>.scene.json next to the .blend): content, determinism and size caps."""

import json
import math
import pathlib
import shutil
import tempfile
import time

import bpy

from gamestudio.core import components, ids, registry, shaders, twin

import helpers


class TwinTest(helpers.ProjectTestCase):
    def setUp(self):
        super().setUp()
        (self.root / "scenes").mkdir()
        self.blend = self.root / "scenes" / "level.blend"
        self.path = self.root / "scenes" / "level.scene.json"

    def build_level(self):
        scene = bpy.context.scene
        scene.name = "level"
        level = bpy.data.collections.new("Level")
        props = bpy.data.collections.new("Props")
        scene.collection.children.link(level)
        scene.collection.children.link(props)
        mat = bpy.data.materials.new("Stone")
        ground = helpers.new_object("Ground", helpers.new_mesh("GroundMesh"), collection=level)
        ground.data.materials.append(mat)
        ground.scale = (10, 10, 1)
        ground.modifiers.new("Bevel", "BEVEL")
        components.add_component(ground, "Collider", {"shape": "mesh"})
        player = helpers.new_object("Player", helpers.new_mesh("PlayerMesh"), location=(0, -3, 1), collection=level)
        components.add_component(player, "RigidBody", {"type": "kinematic"})
        components.add_component(player, "Spin", {"speed": 0.05})
        components.set_enabled(player, "Spin", False)
        coin = helpers.new_object("Coin1", helpers.new_mesh("CoinMesh"), location=(2, 0, 1), collection=props)
        coin.rotation_euler = (math.radians(90), 0, 0)
        components.add_component(coin, "Coin", {"value": 5, "rarity": "epic", "magnet": player})
        coin["gs_kit"] = json.dumps({"kind": "collectible", "params": {"size": 0.7, "seed": 3, "shape": "coin"}})
        coin["team"] = "red"
        gem = helpers.new_object("Gem", parent=coin, location=(0, 0, 0.5), collection=props)
        gem.empty_display_type = "SPHERE"
        cam = helpers.new_object("Camera", bpy.data.cameras.new("Camera"), location=(0, -10, 5))
        components.add_component(cam, "FollowCamera", {"target": player, "offset": [0, 4, 8]})
        scene.camera = cam
        light = bpy.data.lights.new("Sun", "SUN")
        light.energy = 3.5
        helpers.new_object("Sun", light)
        prefab = bpy.data.collections.new("CratePrefab")
        prefab["gs_prefab"] = True
        helpers.new_object("CrateBody", helpers.new_mesh("CrateMesh"), collection=prefab)
        inst = helpers.new_object("Crate_01", location=(4, 4, 0), collection=props)
        inst.instance_type = "COLLECTION"
        inst.instance_collection = prefab
        return scene

    def save(self):
        bpy.ops.wm.save_as_mainfile(filepath=str(self.blend))
        return self.path.read_text(encoding="utf-8")

    def entries(self, text=None):
        doc = json.loads(text if text is not None else self.path.read_text(encoding="utf-8"))
        return doc, {e["name"]: e for e in doc["objects"]}

    def test_written_on_save_with_objects_components_and_kits(self):
        self.build_level()
        text = self.save()
        self.assertTrue(text.endswith("}\n"))
        doc, objs = self.entries(text)
        self.assertEqual((doc["format"], doc["version"], doc["file"]), ("gamestudio.twin", 1, "scenes/level.blend"))
        self.assertEqual(doc["scenes"][0]["camera"], "Camera")
        self.assertEqual(doc["stats"]["objects"], len(objs))
        self.assertEqual(doc["stats"]["with_components"], 4)

        ground = objs["Ground"]
        self.assertEqual((ground["type"], ground["coll"], ground["scale"]), ("MESH", "Level", [10, 10, 1]))
        self.assertEqual((ground["verts"], ground["tris"]), (4, 2))
        self.assertEqual((ground["mods"], ground["mats"]), (["BEVEL"], ["Stone"]))
        self.assertEqual(ground["comps"], {"Collider": {"shape": "mesh"}})
        self.assertEqual(ground["id"], ids.get_id(bpy.data.objects["Ground"]))

        player = objs["Player"]
        self.assertEqual(player["comps"]["RigidBody"], {"type": "kinematic"})
        self.assertEqual(player["comps"]["Spin"], {"speed": 0.05})  # float32 noise trimmed
        self.assertEqual(player["comps_off"], ["Spin"])
        self.assertEqual(player["loc"], [0, -3, 1])
        self.assertNotIn("rot", player)

        coin = objs["Coin1"]
        self.assertEqual(coin["comps"]["Coin"], {"magnet": "Player", "rarity": "epic", "value": 5})
        self.assertEqual(coin["kit"], {"kind": "collectible", "params": {"size": 0.7, "seed": 3, "shape": "coin"}})
        self.assertEqual(coin["props"], {"team": "red"})
        self.assertEqual(coin["rot"], [90, 0, 0])
        self.assertEqual(objs["Gem"]["parent"], "Coin1")
        self.assertEqual(objs["Camera"]["camera"]["type"], "PERSP")
        self.assertEqual(objs["Camera"]["comps"]["FollowCamera"]["target"], "Player")
        self.assertEqual(objs["Sun"]["light"], {"type": "SUN", "energy": 3.5})
        self.assertEqual((objs["Crate_01"]["inst"], objs["Crate_01"]["prefab"]), ("CratePrefab", True))
        # Prefab sources outside the scene are part of the file too.
        self.assertIn("CrateBody", objs)
        colls = {c["name"]: c for c in doc["collections"]}
        self.assertEqual(colls["CratePrefab"]["instances"], 1)
        self.assertTrue(colls["CratePrefab"]["prefab"])
        self.assertEqual(colls["Props"]["parent"], "Scene Collection")
        self.assertNotIn("libraries", doc)

    def test_no_diff_on_noop_save_and_volatile_state_is_ignored(self):
        scene = self.build_level()
        first = self.save()
        mtime = self.path.stat().st_mtime_ns
        self.assertEqual(twin.render(twin.build(str(self.blend), json.loads(first))), first)

        # Selection, active object, current frame and the export revision are not level edits.
        bpy.data.objects["Player"].select_set(True)
        bpy.context.view_layer.objects.active = bpy.data.objects["Coin1"]
        scene.frame_current = 42
        scene["gs_rev"] = 9
        result = twin.write_twin()
        self.assertFalse(result["changed"])
        self.assertEqual(self.save(), first)
        self.assertEqual(self.path.stat().st_mtime_ns, mtime)

        # A real edit changes exactly one line.
        components.set_field(bpy.data.objects["Coin1"], "Coin", "value", 6)
        second = self.save()
        changed = [(a, b) for a, b in zip(first.splitlines(), second.splitlines()) if a != b]
        self.assertEqual(len(first.splitlines()), len(second.splitlines()))
        self.assertEqual(len(changed), 1)
        self.assertIn('"name":"Coin1"', changed[0][1])
        self.assertIn('"value":6', changed[0][1])

    def test_same_text_without_the_registry(self):
        self.build_level()
        first = self.save()
        registry.set_registry({"components": []})
        registry_file = self.root / ".gamestudio" / "components.json"
        hidden = registry_file.with_name("components.hidden")
        registry_file.rename(hidden)
        try:
            self.assertEqual(registry.component_names(), [])
            # No registry at all (a fresh clone): enums and bools are decoded from the previous twin.
            self.assertFalse(twin.write_twin()["changed"])
            # No previous twin: the project's components.json decodes them.
            hidden.rename(registry_file)
            self.path.unlink()
            twin.write_twin()
            self.assertEqual(self.path.read_text(encoding="utf-8"), first)
        finally:
            if hidden.exists():
                hidden.rename(registry_file)
            registry.reload_registry(self.root)

    def test_materials_with_a_game_shader(self):
        from test_shaders import shader_registry

        helpers.write_registry(self.root, shader_registry())
        registry.reload_registry(self.root)
        self.build_level()
        doc, _objs = self.entries(self.save())
        self.assertNotIn("materials", doc)  # no game shaders: no list, and `about` is as before
        self.assertNotIn("game shader", doc["about"])
        sand, leaf = bpy.data.materials["Stone"], bpy.data.materials.new("Leaf")
        shaders.set_shader(sand, "Triplanar", {"wet": 0.6, "tile": 3.2})  # tile is the default: not listed
        shaders.set_shader(leaf, "Foliage", {"mode": "still", "count": 4})
        bpy.data.materials.new("Plain").use_fake_user = True
        leaf.use_fake_user = True
        first = self.save()
        doc, _objs = self.entries(first)
        self.assertEqual(doc["materials"],
                         [{"name": "Leaf", "shader": "Foliage", "values": {"count": 4, "mode": "still"}},
                          {"name": "Stone", "shader": "Triplanar", "values": {"wet": 0.6}}])
        self.assertIn('  {"name":"Leaf","shader":"Foliage","values":{"count":4,"mode":"still"}},', first)
        self.assertIn("materials lists the game shader", doc["about"])
        shaders.set_shader(leaf, None)
        doc, _objs = self.entries(self.save())
        self.assertEqual([m["name"] for m in doc["materials"]], ["Stone"])
        # Without the registry, enum values are decoded from the previous twin (and a stored default could not be
        # told apart, so none is stored here).
        shaders.set_shader(leaf, "Foliage")
        registry.shader_group(sand, "Triplanar").property_unset("tile")
        first = self.save()
        registry.set_registry({"components": []})
        registry_file = self.root / ".gamestudio" / "components.json"
        hidden = registry_file.with_name("components.hidden")
        registry_file.rename(hidden)
        try:
            self.assertFalse(twin.write_twin()["changed"])
        finally:
            hidden.rename(registry_file)
            registry.reload_registry(self.root)
        self.assertEqual(self.path.read_text(encoding="utf-8"), first)

    def test_size_caps_and_summaries(self):
        scene = bpy.context.scene
        clutter = bpy.data.collections.new("Clutter")
        scene.collection.children.link(clutter)
        mesh = helpers.new_mesh("RockMesh")
        for i in range(1500):
            helpers.new_object(f"Rock.{i:04d}", mesh, location=(i % 40, i // 40, 0), collection=clutter)
        for i in range(30):
            obj = helpers.new_object(f"Coin{i:02d}", helpers.new_mesh(f"C{i}"), collection=clutter)
            components.add_component(obj, "Coin", {"value": i})
        started = time.perf_counter()
        text = self.save()
        self.assertLess(time.perf_counter() - started, 5.0)
        self.assertLess(len(text.encode("utf-8")), 64_000)
        doc, objs = self.entries(text)
        self.assertEqual(doc["stats"]["objects"], 1530)
        self.assertEqual(doc["stats"]["listed"], twin.MAX_OBJECTS)
        self.assertEqual(len(objs), twin.MAX_OBJECTS)
        self.assertTrue(all(f"Coin{i:02d}" in objs for i in range(30)))  # gameplay objects keep their line
        more = doc["more"]
        self.assertEqual(sum(m["objects"] for m in more), 1530 - twin.MAX_OBJECTS)
        self.assertEqual(more[0]["coll"], "Clutter")
        self.assertEqual(more[0]["names"], ["Rock ×1280"])
        self.assertEqual(objs["Rock.0000"]["data"], "RockMesh")  # shared mesh is named
        self.assertIn("list_objects", doc["about"])

    def test_long_lists_and_texts_are_capped(self):
        obj = helpers.new_object("Busy", helpers.new_mesh("BusyMesh"))
        for i in range(12):
            obj.data.materials.append(bpy.data.materials.new(f"M{i}"))
            obj.modifiers.new(f"W{i}", "WELD")
        components.add_component(obj, "Showcase", {"label": "x" * 300})
        self.save()
        _doc, objs = self.entries()
        busy = objs["Busy"]
        self.assertEqual(len(busy["mats"]), twin.MAX_ITEMS + 1)
        self.assertEqual(busy["mats"][-1], "+4 more")
        self.assertEqual(len(busy["mods"]), twin.MAX_ITEMS + 1)
        self.assertEqual(len(busy["comps"]["Showcase"]["label"]), twin.MAX_TEXT)

    def test_not_written_for_generated_folders_outside_projects_or_when_off(self):
        helpers.new_object("Thing")
        checkpoint = self.root / ".gamestudio" / "checkpoints" / "a.blend"
        checkpoint.parent.mkdir(parents=True)
        bpy.ops.wm.save_as_mainfile(filepath=str(checkpoint), copy=True)
        self.assertFalse(twin.twin_path(checkpoint).exists())

        outside = pathlib.Path(tempfile.mkdtemp(prefix="gs_outside_", dir=helpers.tmp_base()))
        try:
            bpy.ops.wm.save_as_mainfile(filepath=str(outside / "loose.blend"))
            self.assertFalse((outside / "loose.scene.json").exists())
            self.assertIsNone(twin.write_twin())
        finally:
            shutil.rmtree(outside, ignore_errors=True)

        config = json.loads((self.root / "gamestudio.json").read_text(encoding="utf-8"))
        (self.root / "gamestudio.json").write_text(json.dumps(dict(config, twin=False)), encoding="utf-8")
        off = self.root / "scenes" / "off.blend"
        bpy.ops.wm.save_as_mainfile(filepath=str(off))
        self.assertFalse(twin.twin_path(off).exists())
        self.assertIsNotNone(twin.write_twin(str(off), force=True))
        self.assertTrue(twin.twin_path(off).exists())

    def test_handler_is_registered_and_persistent(self):
        self.assertIn(twin._on_save_post, bpy.app.handlers.save_post)
        helpers.reset_scene()
        self.assertIn(twin._on_save_post, bpy.app.handlers.save_post)
