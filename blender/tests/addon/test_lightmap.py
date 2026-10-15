# SPDX-License-Identifier: GPL-3.0-or-later
"""Baked lighting (core/lightmap.py, API.md "Baked lighting"): static selection, lightmap UVs and atlases,
the Cycles bake in background mode, the export's TEXCOORD and manifest references, and that nothing is
written into the .blend."""

import json
import math

import bpy

from gamestudio.core import components, export, ids, lightmap
from gamestudio.core.util import any_window

import helpers


def box(name, size=2.0, location=(0, 0, 0)):
    bpy.ops.mesh.primitive_cube_add(size=size, location=location)
    obj = bpy.context.active_object
    obj.name = name
    return obj


def level():
    """A ground, a wall and a sun: enough for shadows and bounce light."""
    bpy.ops.mesh.primitive_plane_add(size=8)
    ground = bpy.context.active_object
    ground.name = "Ground"
    wall = box("Wall", 2.0, (0, 0, 1))
    sun = bpy.data.objects.new("Sun", bpy.data.lights.new("Sun", "SUN"))
    bpy.context.scene.collection.objects.link(sun)
    sun.data.energy = 3.0
    sun.rotation_euler = (math.radians(40), 0, math.radians(30))
    world = bpy.data.worlds.new("World")
    world.node_tree.nodes["Background"].inputs["Color"].default_value = (0.4, 0.5, 0.7, 1.0)
    bpy.context.scene.world = world
    return ground, wall, sun


def datablocks():
    return {kind: sorted(i.name for i in getattr(bpy.data, kind))
            for kind in ("images", "materials", "meshes", "objects", "scenes", "lights", "worlds")}


class StaticSelectionTest(helpers.ProjectTestCase):
    def test_rigid_bodies_movers_and_opt_out(self):
        ground, wall, _sun = level()
        crate = box("Crate", 1.0, (3, 0, 0.5))
        components.add_component(crate, "RigidBody")                        # dynamic: moves
        pillar = box("Pillar", 1.0, (-3, 0, 0.5))
        components.add_component(pillar, "RigidBody", {"type": "fixed"})    # fixed: static
        lid = box("Lid", 0.5, (3, 0, 1.2))
        lid.parent = crate                                                  # rides on the crate
        skip = box("Skip", 0.5, (0, 3, 0.25))
        skip[lightmap.PROP] = False                                         # opted out
        spinner = box("Spinner", 0.5, (0, -3, 0.25))
        spinner.animation_data_create().action = bpy.data.actions.new("Spin")
        static, skipped = lightmap.static_objects(bpy.context.scene)
        self.assertEqual(sorted(o.name for o in static), ["Ground", "Pillar", "Wall"])
        reasons = {o.name: r for o, r in skipped}
        self.assertIn("dynamic RigidBody", reasons["Crate"])
        self.assertIn("parent 'Crate'", reasons["Lid"])
        self.assertIn("off", reasons["Skip"])
        self.assertIn("animated", reasons["Spinner"])

    def test_light_modes(self):
        sun = bpy.data.objects.new("Sun", bpy.data.lights.new("Sun", "SUN"))
        area = bpy.data.objects.new("Area", bpy.data.lights.new("Area", "AREA"))
        lamp = bpy.data.objects.new("Lamp", bpy.data.lights.new("Lamp", "POINT"))
        lamp[lightmap.PROP] = "baked"
        self.assertEqual([lightmap.light_mode(o) for o in (sun, area, lamp)], ["realtime", "baked", "baked"])

    def test_atlas_plan_follows_texel_density(self):
        settings = dict(lightmap.QUALITY["preview"])
        one = lightmap.plan_atlases({"A": 10.0, "B": 2.0}, settings)
        self.assertEqual(len(one), 1)
        self.assertEqual(sorted(one[0]["objects"]), ["A", "B"])
        self.assertLessEqual(one[0]["size"], settings["max_size"])
        # More surface than one atlas holds at this density: several atlases, none over the limit.
        many = lightmap.plan_atlases({f"O{i}": 4000.0 for i in range(6)}, settings)
        self.assertGreater(len(many), 1)
        self.assertTrue(all(a["size"] <= settings["max_size"] for a in many))


class BakeTest(helpers.ProjectTestCase):
    def setUp(self):
        super().setUp()
        self.ground, self.wall, self.sun = level()
        self.folder = lightmap.lightmap_dir(self.root, export.export_name(bpy.context.scene))

    def bake(self, **kwargs):
        return lightmap.bake_lighting(quality="draft", root=self.root, **kwargs)

    def test_bake_writes_files_only(self):
        before = datablocks()
        uv_before = {o.name: [l.name for l in o.data.uv_layers] for o in (self.ground, self.wall)}
        window_scene = any_window().scene
        result = self.bake()
        self.assertFalse(result["up_to_date"])
        self.assertEqual(sorted(o["name"] for o in result["objects"]), ["Ground", "Wall"])
        self.assertGreaterEqual(len(result["atlases"]), 1)
        self.assertGreater(result["texture_bytes"], 0)
        meta = json.loads((self.folder / lightmap.META).read_text(encoding="utf-8"))
        for atlas in meta["atlases"]:
            png = (self.folder / atlas["file"]).read_bytes()
            self.assertEqual(png[:4], b"\x89PNG")
            self.assertGreater(atlas["scale"], 0.0)
        for entry in meta["objects"].values():
            self.assertTrue((self.folder / entry["uv"]).is_file())
        # Nothing new in the file: no images (packed or not), materials, meshes or UV maps.
        self.assertEqual(datablocks(), before)
        self.assertEqual({o.name: [l.name for l in o.data.uv_layers] for o in (self.ground, self.wall)}, uv_before)
        self.assertTrue(all(i.packed_file is None for i in bpy.data.images))
        self.assertEqual(any_window().scene, window_scene)
        self.assertFalse(self.sun.hide_render)

    def test_lighting_reaches_the_lightmap(self):
        self.bake()
        meta = json.loads((self.folder / lightmap.META).read_text(encoding="utf-8"))
        image = bpy.data.images.load(str(self.folder / meta["atlases"][0]["file"]))
        try:
            import numpy as np

            px = np.empty(image.size[0] * image.size[1] * 4, dtype=np.float32)
            image.pixels.foreach_get(px)
            self.assertGreater(float(px.reshape(-1, 4)[:, :3].max()), 0.2)  # the sky lights the scene
        finally:
            bpy.data.images.remove(image)

    def test_unchanged_scene_is_up_to_date_and_partial_bakes_keep_the_rest(self):
        first = self.bake()
        self.assertTrue(self.bake()["up_to_date"])
        self.assertFalse(self.bake(force=True)["up_to_date"])
        self.sun.data.energy = 5.0
        self.assertFalse(self.bake()["up_to_date"])
        partial = self.bake(objects=["Wall"])
        self.assertEqual(sorted(o["name"] for o in partial["objects"]), ["Ground", "Wall"])
        self.assertEqual(len(partial["atlases"]), len(first["atlases"]) + 1)
        with self.assertRaises(lightmap.LightmapError):
            self.bake(objects=["Sun"])

    def test_export_writes_uv1_and_manifest_references(self):
        components.add_component(self.ground, "Collider")
        mover = box("Mover", 0.5, (2, 2, 0.25))
        components.add_component(mover, "RigidBody")
        self.bake()
        result = export.export_scene()
        self.assertEqual(result["lightmaps"]["objects"], 2)
        self.assertEqual(result["lightmaps"]["stale"], [])
        manifest = json.loads(open(result["manifest"], encoding="utf-8").read())
        section = manifest["lightmaps"]
        self.assertEqual(set(section["objects"]), {ids.get_id(self.ground), ids.get_id(self.wall)})
        self.assertNotIn(ids.get_id(mover), section["objects"])
        out = export.project.export_dir(self.root)
        for atlas in section["atlases"]:
            self.assertTrue((out / atlas["file"].split("?", 1)[0]).is_file())
        gltf, _chunks = export.read_glb(result["glb"])
        by_id = {n.get("extras", {}).get("gs_id"): n for n in gltf["nodes"]}
        for gs_id, info in section["objects"].items():
            mesh = gltf["meshes"][by_id[gs_id]["mesh"]]
            for prim in mesh["primitives"]:
                self.assertIn(f"TEXCOORD_{info['uv']}", prim["attributes"])
            self.assertEqual(info["uv"], 1)  # the cube's own UV map stays TEXCOORD_0
        mover_mesh = gltf["meshes"][by_id[ids.get_id(mover)]["mesh"]]
        self.assertNotIn("TEXCOORD_1", mover_mesh["primitives"][0]["attributes"])
        # The export put the user's meshes back.
        self.assertNotIn(lightmap.LM_UV, self.wall.data.uv_layers)
        self.assertFalse(any(m.name.startswith("gs_lm_") for m in bpy.data.meshes))

        # Moving a baked object marks its lighting stale; reshaping it drops its lightmap.
        self.wall.location.x += 1.0
        self.assertEqual(export.export_scene()["lightmaps"]["stale"], ["Wall"])
        bpy.context.view_layer.objects.active = self.wall
        bpy.ops.object.modifier_add(type="SUBSURF")
        again = export.export_scene()
        self.assertEqual(again["lightmaps"]["missing"], ["Wall"])

    def test_linked_duplicates_keep_their_own_lightmaps(self):
        twin = self.wall.copy()  # shares the wall's mesh: the export reads its digests once
        twin.name = "Twin"
        twin.location = (-2.5, -2.5, 1)
        bpy.context.scene.collection.objects.link(twin)
        self.bake()
        out = export.project.export_dir(self.root)
        before = datablocks()
        result = export.export_scene_to(out)
        self.assertEqual(result["lightmaps"]["objects"], 3)
        self.assertEqual((result["lightmaps"]["stale"], result["lightmaps"]["missing"]), ([], []))
        self.assertEqual(datablocks(), before)  # the meshes the export swapped in are gone
        self.assertIs(twin.data, self.wall.data)
        twin.location.x += 1.0
        self.assertEqual(export.export_scene_to(out)["lightmaps"]["stale"], ["Twin"])
        self.wall.data.vertices[0].co.z += 0.1  # the shared mesh: both are stale
        self.assertEqual(sorted(export.export_scene_to(out)["lightmaps"]["stale"]), ["Twin", "Wall"])

    def test_baked_lights_are_listed(self):
        area = bpy.data.objects.new("Panel", bpy.data.lights.new("Panel", "AREA"))
        bpy.context.scene.collection.objects.link(area)
        lamp = bpy.data.objects.new("Lamp", bpy.data.lights.new("Lamp", "POINT"))
        bpy.context.scene.collection.objects.link(lamp)
        lamp[lightmap.PROP] = "baked"
        result = self.bake()
        self.assertEqual(sorted(result["baked_lights"]), ["Lamp", "Panel"])
        manifest = json.loads(open(export.export_scene()["manifest"], encoding="utf-8").read())
        # The game switches both off (the exporter writes the area light as a node without a light).
        self.assertEqual(sorted(manifest["lightmaps"]["bakedLights"]), sorted([ids.get_id(lamp), ids.get_id(area)]))
        self.assertNotIn(ids.get_id(self.sun), manifest["lightmaps"]["bakedLights"])

    def test_background_script_compiles(self):
        compile(lightmap.background_script("main", "preview", ["Wall"], True), "<bake>", "exec")
