# SPDX-License-Identifier: GPL-3.0-or-later
"""Asset libraries (assets/<name>.blend): create, add, list, place, replace, reload, make local, export."""

import json
import math

import bpy

from gamestudio import assetlib
from gamestudio.core import components, export, ids, registry, twin

import helpers


def box(name, size=1.0):
    h = size / 2
    mesh = bpy.data.meshes.new(name + "Mesh")
    mesh.from_pydata([(x, y, z) for x in (-h, h) for y in (-h, h) for z in (0, size)], [],
                     [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)])
    return mesh


class AssetLibraryTest(helpers.ProjectTestCase):
    def setUp(self):
        super().setUp()
        (self.root / "scenes").mkdir()
        self.level = self.root / "scenes" / "main.blend"
        bpy.context.scene.name = "main"
        self.props = bpy.data.collections.new("Props")
        bpy.context.scene.collection.children.link(self.props)
        wood = bpy.data.materials.new("Wood")
        crate = helpers.new_object("Crate", box("Crate"), location=(5, 2, 0), collection=self.props)
        crate.rotation_euler = (0, 0, math.radians(30))
        crate.data.materials.append(wood)
        components.add_component(crate, "Collider", {"shape": "box"})
        components.add_component(crate, "RigidBody", {"type": "dynamic", "mass": 3})
        lid = helpers.new_object("Lid", box("Lid", 0.5), parent=crate, location=(0, 0, 1), collection=self.props)
        components.add_component(lid, "Coin", {"value": 2, "magnet": crate})
        barrel = helpers.new_object("Barrel", box("Barrel", 0.8), location=(-3, 0, 0), collection=self.props)
        barrel.data.materials.append(wood)
        components.add_component(barrel, "Spin", {"speed": 2})
        bpy.ops.wm.save_as_mainfile(filepath=str(self.level))

    def leftovers(self):
        return [i.name for i in [*bpy.data.objects, *bpy.data.collections] if assetlib.STAGING_PREFIX in i.name]

    def test_create_add_and_list(self):
        path = assetlib.create_library("props")
        self.assertEqual(path, self.root / "assets" / "props.blend")
        self.assertTrue(path.is_file())
        self.assertEqual(assetlib.list_libraries(), ["assets/props.blend"])
        self.assertEqual(assetlib.list_assets(), [])
        before = sorted(o.name for o in bpy.data.objects)

        done = assetlib.add_to_library(["Crate"], "props", catalog="Props/Crates", tags="wood, box",
                                       description="A wooden crate.")
        self.assertEqual(len(done), 1)
        self.assertEqual(done[0]["asset"], "Crate")
        self.assertEqual(done[0]["objects"], ["Crate", "Lid"])  # children come along, names kept
        self.assertEqual(done[0]["library"], "assets/props.blend")

        # The open level is unchanged: no copies, no staging data, nothing linked.
        self.assertEqual(sorted(o.name for o in bpy.data.objects), before)
        self.assertEqual(self.leftovers(), [])
        self.assertEqual(len(bpy.data.libraries), 0)

        cats = (self.root / "assets" / "blender_assets.cats.txt").read_text(encoding="utf-8")
        self.assertIn(":Props/Crates:Props-Crates", cats)
        [entry] = assetlib.list_assets()
        self.assertEqual({k: entry[k] for k in ("name", "library", "catalog", "tags", "description", "prefab")},
                         {"name": "Crate", "library": "assets/props.blend", "catalog": "Props/Crates",
                          "tags": ["box", "wood"], "description": "A wooden crate.", "prefab": True})
        self.assertEqual(entry["components"], ["Coin", "Collider", "RigidBody"])
        self.assertEqual(entry["objects"], 2)
        # Stored at the origin without its level rotation: a 1 m cube with a 0.5 m lid on top.
        self.assertEqual(entry["size"], [1.0, 1.0, 1.5])
        self.assertEqual(entry["placed"], 0)
        self.assertEqual(assetlib.list_assets(tag="nope"), [])
        self.assertEqual(len(assetlib.list_assets(catalog="Props")), 1)

        # The library file: a proper scene, the asset collection with a preview, and its own twin.
        with bpy.data.libraries.load(str(path)) as (src, _dst):
            self.assertEqual(list(src.scenes), ["Assets"])
            self.assertEqual(sorted(src.materials), ["Wood"])
        library_twin = json.loads((self.root / "assets" / "props.scene.json").read_text(encoding="utf-8"))
        [coll] = [c for c in library_twin["collections"] if c["name"] == "Crate"]
        self.assertEqual(coll["asset"], {"catalog": "Props/Crates", "tags": ["box", "wood"],
                                         "description": "A wooden crate."})
        self.assertTrue(coll["prefab"])
        lib_objs = {o["name"]: o for o in library_twin["objects"]}
        self.assertEqual(lib_objs["Crate"]["loc"], [0, 0, 0])
        self.assertNotIn("rot", lib_objs["Crate"])
        self.assertEqual(lib_objs["Lid"]["comps"]["Coin"], {"magnet": "Crate", "value": 2})

        self.assertNotIn("libraries", library_twin)  # no leftover link to the staging file

        coll = assetlib.link_asset("Crate")
        self.assertIsNotNone(coll.asset_data)
        self.assertEqual(tuple(coll.preview.image_size), (assetlib.PREVIEW_SIZE,) * 2)
        self.assertTrue(export.is_prefab(coll))

        # In the level's twin, the linked asset shows its library and catalog.
        assetlib.place_asset("Crate")
        bpy.ops.wm.save_mainfile()
        level_twin = json.loads((self.root / "scenes" / "main.scene.json").read_text(encoding="utf-8"))
        [linked] = [c for c in level_twin["collections"] if c["name"] == "Crate" and "lib" in c]
        self.assertEqual((linked["lib"], linked["asset"]["catalog"], linked["instances"]),
                         ("assets/props.blend", "Props/Crates", 1))

    def test_place_export_replace_reload_and_make_local(self):
        done = assetlib.add_to_library(["Crate", "Barrel"], "props", separate=True, preview=False)
        self.assertEqual([d["asset"] for d in done], ["Crate", "Barrel"])

        inst = assetlib.place_asset("Crate", location=(4, 2, 0), rotation=(0, 0, 90), scale=2, collection="Props")
        self.assertEqual(inst.name, "Crate_01")
        self.assertEqual(inst.instance_collection.library.filepath.replace("\\", "/"), "//../assets/props.blend")
        self.assertEqual(assetlib.asset_of(inst), {"asset": "Crate", "library": "assets/props.blend", "prefab": True})
        self.assertEqual(assetlib.place_asset("Crate", location=(8, 0, 0)).name, "Crate_02")
        self.assertEqual([o.name for o in assetlib.instances("Crate")], ["Crate_01", "Crate_02"])
        self.assertEqual(assetlib.list_assets()[1]["placed"], 2)  # sorted: Barrel, Crate
        components.add_component(inst, "Spin", {"speed": 5})  # per-copy components go on the instance
        # Barrel is not placed, but linked for ctx.spawn: a template only, kept out of the scene.
        assetlib.make_spawnable("Barrel")
        holder = bpy.data.collections["Spawnable"]
        self.assertTrue(holder.use_fake_user)
        self.assertNotIn(holder, bpy.context.scene.collection.children_recursive)

        # Export: the instance is a prefab instance, the template carries the asset's components.
        out = self.root / ".gamestudio" / "export"
        result = export.export_scene_to(out)
        manifest = json.loads((out / "main.scene.json").read_text(encoding="utf-8"))
        entities = {e["name"]: e for e in manifest["entities"]}
        self.assertEqual(entities["Crate_01"]["prefab"], "Crate")
        self.assertEqual(entities["Crate_01"]["components"]["Spin"]["speed"], 5.0)
        prefabs = {p["name"]: p for p in manifest["prefabs"]}
        self.assertEqual(set(prefabs), {"Crate", "Barrel"})
        # The spawnable's objects are not level entities (the level's own Barrel is still there).
        self.assertEqual(len(manifest["entities"]), 5)
        self.assertNotIn(prefabs["Barrel"]["entities"][0]["id"], {e["id"] for e in manifest["entities"]})
        template = {e["name"]: e for e in prefabs["Crate"]["entities"]}
        self.assertEqual(template["Crate"]["components"]["RigidBody"]["mass"], 3.0)
        self.assertEqual(template["Crate"]["components"]["Collider"]["shape"], "box")
        self.assertEqual(template["Lid"]["parent"], template["Crate"]["id"])
        self.assertEqual(template["Lid"]["components"]["Coin"]["magnet"], template["Crate"]["id"])
        scene_ids = {e["id"] for e in manifest["entities"]}
        self.assertFalse(scene_ids & {e["id"] for e in prefabs["Crate"]["entities"]})  # no shared ids
        self.assertEqual(sorted(result["prefabs"]), ["Barrel", "Crate"])
        gltf, _chunks = export.read_glb(out / "main.glb")
        node = next(n for n in gltf["nodes"] if n.get("name") == "Crate_01")
        self.assertNotIn("children", node)  # contents come from the template
        for got, want in zip(node["translation"], [4, 0, -2]):  # Blender (4, 2, 0) in Y-up
            self.assertAlmostEqual(got, want, places=4)
        self.assertEqual([round(v, 4) for v in node["scale"]], [2, 2, 2])
        self.assertAlmostEqual(abs(node["rotation"][1]), math.sin(math.radians(45)), places=4)
        self.assertTrue((out / "main.prefabs.glb").is_file())

        # The level twin shows the instance and the library.
        bpy.ops.wm.save_mainfile()
        level_twin = json.loads((self.root / "scenes" / "main.scene.json").read_text(encoding="utf-8"))
        objs = {o["name"]: o for o in level_twin["objects"]}
        self.assertEqual((objs["Crate_01"]["inst"], objs["Crate_01"]["inst_lib"], objs["Crate_01"]["prefab"]),
                         ("Crate", "assets/props.blend", True))
        colls = {(c["name"], c.get("lib")): c for c in level_twin["collections"]}
        self.assertNotIn("parent", colls[("Spawnable", None)])
        self.assertEqual(colls[("Barrel", "assets/props.blend")]["parent"], "Spawnable")
        self.assertEqual(level_twin["libraries"], [{"path": "assets/props.blend", "collections": 2, "objects": 3}])

        # Replace: same empty, other asset.
        assetlib.replace_asset(inst, "Barrel")
        self.assertEqual(inst.instance_collection.name, "Barrel")
        self.assertEqual(components.list_components(inst), ["Spin"])
        assetlib.replace_asset("Crate_01", "Crate")

        # Changing the library reloads it in the open level.
        crate = bpy.data.objects["Crate"]
        components.set_field(crate, "RigidBody", "mass", 7)
        assetlib.add_to_library([crate], "props", preview=False)
        linked = assetlib.link_asset("Crate")
        body = next(o for o in linked.all_objects if o.name == "Crate")
        self.assertEqual(components.get_values(body, "RigidBody")["mass"], 7.0)
        self.assertEqual(assetlib.reload_libraries(), ["assets/props.blend"])
        self.assertEqual(assetlib.reload_libraries(["other"]), [])

        # Make local: editable copies under the empty, at the same world positions.
        inst = bpy.data.objects["Crate_01"]
        linked = assetlib.link_asset("Crate")
        template_ids = {ids.get_id(o) for o in linked.all_objects}
        # Linked objects that are only instanced are never evaluated: compose their matrices.
        expected = {o.name: inst.matrix_world @ assetlib._local_matrix(o).translation for o in linked.all_objects}
        copies = assetlib.make_local(inst)
        bpy.context.view_layer.update()
        self.assertEqual(inst.instance_type, "NONE")
        self.assertEqual(sorted(c.parent.name for c in copies if c.parent == inst), ["Crate_01"])
        by_source = {c.name.split(".")[0]: c for c in copies}
        for name, position in expected.items():
            self.assertLess((by_source[name].matrix_world.translation - position).length, 1e-4, name)
        for copy in copies:
            self.assertIsNone(copy.library)
            self.assertIsNone(copy.data.library)
            self.assertNotIn(ids.get_id(copy), template_ids)
        self.assertEqual(components.get_values(by_source["Crate"], "RigidBody")["mass"], 7.0)
        self.assertEqual(components.get_values(by_source["Lid"], "Coin")["magnet"], by_source["Crate"].name)
        self.assertEqual(components.list_components(inst), ["Spin"])
        self.assertEqual(self.leftovers(), [])

    def test_move_replaces_the_objects_with_an_instance(self):
        cam = helpers.new_object("Camera", bpy.data.cameras.new("Camera"))
        components.add_component(cam, "FollowCamera", {"target": bpy.data.objects["Barrel"]})
        done = assetlib.add_to_library(["Barrel"], "props", move=True, catalog="Props", preview=False)
        self.assertEqual(done[0]["instance"], "Barrel")
        inst = bpy.data.objects["Barrel"]
        self.assertEqual((inst.instance_type, inst.instance_collection.name), ("COLLECTION", "Barrel"))
        self.assertIsNotNone(inst.instance_collection.library)
        self.assertEqual(tuple(round(v, 4) for v in inst.location), (-3, 0, 0))
        self.assertEqual([c.name for c in inst.users_collection], ["Props"])
        self.assertNotIn("Barrel.moved", bpy.data.objects)
        self.assertEqual(components.get_values(cam, "FollowCamera")["target"], "Barrel")
        self.assertIs(registry.group(cam, "FollowCamera").target, inst)

    def test_errors_name_the_problem(self):
        with self.assertRaisesRegex(assetlib.AssetLibError, "No asset named 'Ghost'"):
            assetlib.place_asset("Ghost")
        with self.assertRaisesRegex(assetlib.AssetLibError, "No object named"):
            assetlib.add_to_library(["Nope"], "props")
        with self.assertRaisesRegex(assetlib.AssetLibError, "Invalid catalog"):
            assetlib.ensure_catalog("a:b")
        with self.assertRaisesRegex(assetlib.AssetLibError, "not an asset instance"):
            assetlib.make_local("Crate")
        with self.assertRaisesRegex(assetlib.AssetLibError, ".blend"):
            assetlib.library_path("props.txt")
        self.assertTrue(issubclass(assetlib.AssetLibError, ValueError))  # the bridge reports ValueErrors

    def test_library_open_in_blender_is_edited_in_place(self):
        path = assetlib.create_library("nature")
        bpy.ops.wm.open_mainfile(filepath=str(path))
        tree = helpers.new_object("Tree", box("Tree", 2))
        components.add_component(tree, "Collider", {"shape": "convex"})
        done = assetlib.add_to_library([tree], "nature", catalog="Nature", preview=False)
        self.assertEqual(done[0]["asset"], "Tree")
        catalogs = twin.read_catalogs(self.root / "assets" / assetlib.CATALOG_FILE)
        self.assertEqual(catalogs[bpy.data.collections["Tree"].asset_data.catalog_id], "Nature")
        self.assertTrue(twin.twin_path(path).exists())
        bpy.ops.wm.open_mainfile(filepath=str(self.level))
        self.assertEqual([a["name"] for a in assetlib.list_assets("nature")], ["Tree"])

    def test_ui_is_registered(self):
        for op in ("asset_add", "asset_place", "asset_reload", "asset_make_local"):
            self.assertTrue(hasattr(bpy.ops.gamestudio, op), op)
        self.assertTrue(bpy.types.GAMESTUDIO_PT_asset_library.is_registered)
        self.assertIn(assetlib._draw_game_menu, bpy.types.GAMESTUDIO_MT_game.draw._draw_funcs)
        self.assertEqual(bpy.ops.gamestudio.asset_reload(), {"FINISHED"})

        layer = bpy.context.view_layer
        barrel = bpy.data.objects["Barrel"]
        barrel.select_set(True)
        layer.objects.active = barrel
        self.assertEqual(bpy.ops.gamestudio.asset_add(library="ui", asset_name="UiBarrel", tags="a, b"), {"FINISHED"})
        self.assertEqual(assetlib._refresh_place_items()[0][0], "assets/ui.blend|UiBarrel")
        bpy.context.scene.cursor.location = (1, 2, 3)
        self.assertEqual(bpy.ops.gamestudio.asset_place(asset="assets/ui.blend|UiBarrel"), {"FINISHED"})
        inst = layer.objects.active
        self.assertEqual((inst.name, tuple(inst.location)), ("UiBarrel_01", (1, 2, 3)))
        self.assertEqual(bpy.ops.gamestudio.asset_make_local(), {"FINISHED"})
        self.assertEqual(inst.instance_type, "NONE")
        self.assertFalse(bpy.ops.gamestudio.asset_make_local.poll())

