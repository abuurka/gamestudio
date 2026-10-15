# SPDX-License-Identifier: GPL-3.0-or-later
"""Procedural world toolkit (gamestudio.procedural): every builder builds in --background, the same seed
gives the same result, set_params() changes the output, exports keep GPU instancing, colliders and
budgets hold."""

import json
import math
from unittest import mock

import bpy
from mathutils import Vector

from gamestudio import kits
from gamestudio import procedural as P
from gamestudio.core import components, export, fidelity
from gamestudio.procedural import builders, groups

import helpers

ROAD = [(-14, -10), (-4, -2), (4, 1), (14, 9)]


def evaluated_mesh_data(obj):
    """(vertex positions, triangle count, material names per face) of obj's evaluated mesh."""
    depsgraph = bpy.context.evaluated_depsgraph_get()
    evaluated = obj.evaluated_get(depsgraph)
    mesh = evaluated.to_mesh()
    try:
        mesh.calc_loop_triangles()
        verts = [tuple(round(c, 4) for c in v.co) for v in mesh.vertices]
        slots = [m.name if m else None for m in mesh.materials]
        mats = [slots[p.material_index] if p.material_index < len(slots) else None for p in mesh.polygons]
        return verts, len(mesh.loop_triangles), mats
    finally:
        evaluated.to_mesh_clear()


def instance_transforms(obj):
    depsgraph = bpy.context.evaluated_depsgraph_get()
    out = []
    for inst in depsgraph.object_instances:
        if inst.is_instance and inst.parent is not None and inst.parent.original == obj:
            out.append((inst.object.original.name, tuple(round(v, 4) for row in inst.matrix_world for v in row)))
    return sorted(out)


def signed_volume(obj) -> float:
    """Positive when a closed mesh's faces point outward."""
    depsgraph = bpy.context.evaluated_depsgraph_get()
    evaluated = obj.evaluated_get(depsgraph)
    mesh = evaluated.to_mesh()
    try:
        mesh.calc_loop_triangles()
        total = 0.0
        for tri in mesh.loop_triangles:
            a, b, c = (mesh.vertices[i].co for i in tri.vertices)
            total += a.dot(b.cross(c)) / 6.0
        return total
    finally:
        evaluated.to_mesh_clear()


class ProceduralTestCase(helpers.ProjectTestCase):
    def setUp(self):
        super().setUp()
        bpy.context.scene.name = "main"
        self.terrain = kits.create("terrain", {"size": 36, "resolution": 18, "height": 1.5, "seed": 2},
                                   collection="Level")[0]

    def update(self):
        bpy.context.view_layer.update()

    def trees(self):
        return P.kit_sources("tree", [{"style": "round", "height": 3}, {"style": "pine", "height": 4}],
                             name="Trees")


class BuildTest(ProceduralTestCase):
    def test_every_builder_builds_named_in_its_collection(self):
        trees = self.trees()
        road_path = P.path(ROAD, name="MainRoadPath")
        made = {
            "scatter": P.scatter(self.terrain, trees, density=0.1, seed=1, name="Forest"),
            "array": P.array_along(road_path, "lamp_post", spacing=8, offset=4, sides="both", terrain=self.terrain,
                                   name="Lamps"),
            "fence": P.fence_along(road_path, offset=5, terrain=self.terrain, name="RoadFence"),
            "road": P.road_along(road_path, terrain=self.terrain, name="MainRoad"),
            "river": P.river_along([(-16, 12), (0, 6), (16, 14)], terrain=self.terrain, name="Brook"),
            "cliff": P.cliff(self.terrain, height=5, inset=1.5, terrain=self.terrain, name="Rim"),
            "rock_wall": P.rock_wall([(-10, -14), (10, -14)], terrain=self.terrain, name="FieldWall"),
            "building": P.modular_building((8, 6), floors=2, location=(-8, 4, 0), terrain=self.terrain,
                                           name="Cottage"),
        }
        self.update()
        expected_collections = {"scatter": "Scatter", "array": "Arrays", "fence": "Fences", "road": "Roads",
                                "river": "Rivers", "cliff": "Cliffs", "rock_wall": "Cliffs", "building": "Buildings"}
        for label, obj in made.items():
            with self.subTest(label):
                self.assertEqual(obj.users_collection[0].name, expected_collections[label])
                self.assertIn(builders.KINDS[P.kind_of(obj)][0], obj.modifiers)
                info = P.stats(obj)
                self.assertGreater(info["triangles"], 50, info)
                params = P.params(obj)
                self.assertEqual(params["kind"], P.kind_of(obj))
        self.assertEqual([o.name for o in bpy.data.collections["Scatter"].objects], ["Forest"])
        self.assertTrue(made["building"].location.z > -5)
        # sources show only through their instances
        layer = bpy.context.view_layer.layer_collection.children[builders.SOURCES_COLLECTION]
        self.assertTrue(layer.exclude)
        self.assertNotIn("Trees_1", [o.name for o in bpy.context.view_layer.objects])
        self.assertIn("Lamp Post", bpy.data.collections["Parts"].objects)
        # the road and river shape the terrain through modifiers that remove() takes away
        names = [m.name for m in self.terrain.modifiers]
        self.assertEqual(names, ["GS Conform MainRoad", "GS Conform Brook"])
        P.remove(made["road"])
        self.assertEqual([m.name for m in self.terrain.modifiers], ["GS Conform Brook"])
        self.assertNotIn("MainRoad", bpy.data.objects)

    def test_catalog_describes_every_builder(self):
        entries = {e["kind"]: e for e in P.catalog()}
        self.assertEqual(set(entries), {"scatter", "array", "fence", "road", "river", "cliff", "rock_wall",
                                        "building"})
        for kind, entry in entries.items():
            props = entry["params"]["properties"]
            self.assertIn("colliders", props, kind)
            self.assertTrue(callable(getattr(P, entry["function"])), kind)
            json.dumps(entry)
        scatter = entries["scatter"]["params"]
        self.assertEqual(scatter["required"], ["surface", "sources"])
        self.assertEqual(scatter["properties"]["density"]["default"], 0.05)
        self.assertEqual(entries["fence"]["params"]["properties"]["rail_style"]["enum"], ["rails", "pickets", "none"])

    def test_group_inputs_match_their_specs(self):
        for kind in groups.BUILDERS:
            tree = groups.ensure(kind)
            names = [i.name for i in tree.interface.items_tree if i.item_type == "SOCKET" and i.in_out == "INPUT"
                     and i.socket_type != "NodeSocketGeometry"]
            self.assertEqual(names, [s.name for s in groups.specs(kind)], kind)
            self.assertIs(groups.ensure(kind), tree)  # built once per file

    def test_bad_input_raises_and_builds_nothing(self):
        before = set(bpy.data.objects)
        with self.assertRaises(P.ProceduralError):
            P.scatter(self.terrain, self.trees(), density=-1)
        with self.assertRaises(P.ProceduralError):
            P.fence_along(ROAD, rail_style="barbed")
        with self.assertRaises(P.ProceduralError):
            P.modular_building(style="castle")
        with self.assertRaises(P.ProceduralError):
            P.road_along("NoSuchCurve")
        made = set(bpy.data.objects) - before
        self.assertEqual({o.name for o in made}, {"Trees_1", "Trees_2"})  # only the sources asked for


class DeterminismTest(ProceduralTestCase):
    def test_same_seed_same_result(self):
        trees = self.trees()
        a = P.scatter(self.terrain, trees, density=0.1, seed=4, name="A")
        b = P.scatter(self.terrain, trees, density=0.1, seed=4, name="B")
        c = P.scatter(self.terrain, trees, density=0.1, seed=5, name="C")
        self.update()
        ta, tb, tc = instance_transforms(a), instance_transforms(b), instance_transforms(c)
        self.assertGreater(len(ta), 10)
        self.assertEqual(ta, tb)
        self.assertNotEqual(ta, tc)

        w1 = P.cliff([(-10, 0), (10, 0)], seed=3, name="W1")
        w2 = P.cliff([(-10, 0), (10, 0)], seed=3, name="W2")
        w3 = P.cliff([(-10, 0), (10, 0)], seed=8, name="W3")
        self.update()
        self.assertEqual(evaluated_mesh_data(w1)[0], evaluated_mesh_data(w2)[0])
        self.assertNotEqual(evaluated_mesh_data(w1)[0], evaluated_mesh_data(w3)[0])

        b1 = P.modular_building((10, 8), seed=1, name="B1")
        b2 = P.modular_building((10, 8), seed=1, name="B2")
        self.update()
        self.assertEqual(evaluated_mesh_data(b1)[0], evaluated_mesh_data(b2)[0])


class SetParamsTest(ProceduralTestCase):
    def test_set_params_changes_the_output(self):
        trees = self.trees()
        forest = P.scatter(self.terrain, trees, density=0.05, min_distance=1.0, seed=1, name="Forest")
        self.update()
        before = P.stats(forest)["instances"]
        result = P.set_params(forest, density=0.2, scale_range=[1.5, 2.0])
        self.update()
        self.assertGreater(P.stats(forest)["instances"], before * 2)
        self.assertEqual((result["density"], result["scale_min"], result["scale_max"]), (0.2, 1.5, 2.0))
        P.set_params(forest, max_instances=12)
        self.update()
        self.assertEqual(P.stats(forest)["instances"], 12)
        ys = [t[1][7] for t in instance_transforms(forest)]
        self.assertTrue(min(ys) < -6 and max(ys) > 6, ys)  # the cap thins the whole terrain, not one end

        building = P.modular_building((8, 6), floors=1, name="House")
        self.update()
        top = lambda: max(v[2] for v in evaluated_mesh_data(building)[0])  # noqa: E731
        low = top()
        P.set_params(building, floors=3, style="stone", roof="flat")
        self.update()
        self.assertGreater(top(), low + 3.5)
        params = P.params(building)
        self.assertEqual((params["style"], params["roof"], params["wall_part"]), ("stone", "flat",
                                                                                 "Building Stone Wall"))

        road = P.road_along(ROAD, width=4, name="Road")
        self.update()
        narrow = evaluated_mesh_data(road)
        P.set_params(road, width=10, lane_lines=False)
        self.update()
        wide = evaluated_mesh_data(road)
        span = lambda verts: max(v[0] for v in verts) - min(v[0] for v in verts)  # noqa: E731
        self.assertGreater(span(wide[0]), span(narrow[0]))
        self.assertNotIn("GS_RoadLine", wide[2])
        self.assertIn("GS_RoadLine", narrow[2])

        fence = P.fence_along(ROAD, rail_style="rails", name="Fence")
        self.update()
        rails = P.stats(fence)
        P.set_params(fence, rail_style="pickets")
        self.update()
        pickets = P.stats(fence)
        self.assertGreater(pickets["instances"], rails["instances"] + 50)
        self.assertEqual(P.params(fence)["rail_style"], "pickets")

    def test_set_params_validates_and_changes_nothing_on_error(self):
        wall = P.cliff([(-8, 0), (8, 0)], height=6, name="Wall")
        before = P.params(wall)
        for bad in ({"height": 1000}, {"heigth": 3}, {"seed": 1.5}, {"terrain": "Missing"}, {"colliders": "maybe"}):
            with self.subTest(bad), self.assertRaises(P.ProceduralError):
                P.set_params(wall, **bad)
        self.assertEqual(P.params(wall), before)
        with self.assertRaises(P.ProceduralError):
            P.params(self.terrain)  # not made by a builder

    def test_road_and_river_shape_the_terrain(self):
        flat = kits.create("terrain", {"size": 30, "resolution": 30, "height": 0.0}, collection="Level")[0]
        river = P.river_along([(-15, 0), (15, 0)], width=6, depth=1.2, bank=2, terrain=flat, name="River")
        self.update()
        surface = lambda: [v for v in evaluated_mesh_data(flat)[0] if abs(v[2] + 1.0) > 1e-3]  # not the skirt  # noqa: E731
        verts = surface()
        middle = min(v[2] for v in verts if abs(v[1]) < 0.6 and abs(v[0]) < 10)
        far = min(v[2] for v in verts if abs(v[1]) > 8)
        self.assertAlmostEqual(middle, -1.2, delta=0.05)
        self.assertAlmostEqual(far, 0.0, delta=0.01)
        water, _tris, _mats = evaluated_mesh_data(river)
        self.assertTrue(all(abs(v[2] - (-1.2 + 0.7 * 1.2)) < 0.05 for v in water))
        P.set_params(river, carve=False)
        self.update()
        self.assertEqual(len(flat.modifiers), 0)
        self.assertAlmostEqual(min(v[2] for v in surface() if abs(v[1]) < 0.6 and abs(v[0]) < 10), 0.0, delta=0.01)

    def test_road_ends_sink_into_the_ground_and_the_verge_is_smooth(self):
        """Walking off either end of a road (its collider is its own mesh), on any line along it, on the
        kerbs and on the verge, meets no step higher than 2 cm. The verge is a strip of the road's own mesh
        with straight edges, not terrain faces painted at the terrain's resolution."""
        terrain_dirt = lambda: evaluated_mesh_data(self.terrain)[2].count("GS_Dirt")  # its sides  # noqa: E731
        sides = terrain_dirt()
        road = P.road_along([(-10, 0), (0, 0), (10, 0)], terrain=self.terrain, name="Road")
        self.update()
        verts, _tris, mats = evaluated_mesh_data(road)
        self.assertIn("GS_Dirt", mats)
        self.assertEqual(terrain_dirt(), sides)  # no painted terrain faces
        edge = 3.0 + 0.3 + 0.1 + 2.0 * 0.6  # half width + kerb + gap + shoulder * 0.6
        outer = [v for v in verts if abs(abs(v[1]) - edge) < 0.01]
        self.assertGreater(len(outer), 2 * 20)  # both outer edges are straight lines along the road
        self.assertLess(max(abs(v[1]) for v in verts), edge + 0.01)

        def worst_step():
            worst = 0.0
            for sign in (1, -1):
                for y in (-4.0, -3.15, -1.6, 0.0, 1.6, 3.15, 4.0):  # verge, kerb, lane, centre, ...
                    walk = [sign * (8.2 + 0.05 * i) for i in range(100)]  # past the last dash, off the end
                    tops = [max(P.ground_height(road, x, y, -1e9), P.ground_height(self.terrain, x, y)) for x in walk]
                    worst = max(worst, *(abs(b - a) for a, b in zip(tops, tops[1:])))
            return worst

        self.assertLessEqual(worst_step(), 0.02)
        P.set_params(road, taper=0.0)  # square ends: the old step
        self.update()
        self.assertGreater(worst_step(), 0.05)
        P.set_params(road, verge_material=None)
        self.update()
        self.assertNotIn("GS_Dirt", evaluated_mesh_data(road)[2])


class ScatterRulesTest(ProceduralTestCase):
    def test_keep_out_slope_and_mask(self):
        trees = self.trees()
        start = helpers.new_object("PlayerStart", None, location=(0, 0, 0))
        forest = P.scatter(self.terrain, trees, density=0.5, min_distance=0.8, seed=2, keep_out=[start],
                           keep_out_distance=6.0, name="Forest")
        self.update()
        spots = [Vector(t[1][3::4][:3]) for t in instance_transforms(forest)]
        self.assertGreater(len(spots), 50)
        self.assertGreater(min((p.xy - Vector((0, 0))).length for p in spots), 5.9)

        steep = kits.create("platform", {"width": 10, "depth": 10, "height": 10, "bevel": 0},
                            location=(60, 0, 0), collection="Level")[0]
        walls = P.scatter(steep, trees, density=1.0, min_distance=0.5, slope_limit=30, name="OnTop")
        self.update()
        tops = [t[1][11] for t in instance_transforms(walls)]
        self.assertTrue(tops and all(z > 9.5 for z in tops), "only the flat top is below the slope limit")

        group = self.terrain.vertex_groups.new(name="NoTrees")
        group.add([v.index for v in self.terrain.data.vertices if v.co.x < 0], 1.0, "REPLACE")
        masked = P.scatter(self.terrain, trees, density=0.3, min_distance=0.8, mask="NoTrees", name="East")
        self.update()
        xs = [t[1][3] for t in instance_transforms(masked)]
        self.assertTrue(xs and min(xs) > -2.5, min(xs))
        P.set_params(masked, invert_mask=True)
        self.update()
        xs = [t[1][3] for t in instance_transforms(masked)]
        self.assertTrue(xs and max(xs) < 2.5, max(xs))

    def test_kit_sources_replace_their_earlier_objects(self):
        first = self.trees()
        forest = P.scatter(self.terrain, first, density=0.05, name="Forest")
        again = self.trees()
        self.update()
        self.assertIs(first, again)
        self.assertEqual(sorted(o.name for o in again.objects), ["Trees_1", "Trees_2"])
        self.assertEqual(set(P.stats(forest)["by_source"]), {"Trees_1", "Trees_2"})

    def test_sources_move_out_of_sight_and_curves_from_points(self):
        rock = kits.create("rock", {"seed": 1}, location=(3, 3, 0), collection="Props")[0]
        scatter = P.scatter(self.terrain, [rock], density=0.05, name="Rocks")
        self.assertEqual([c.name for c in rock.users_collection], ["Rocks Sources"])
        self.assertEqual(P.params(scatter)["sources"], "Rocks Sources")
        lamps = P.array_along([(0, 0), (20, 0)], "lamp_post", spacing=5, name="Lamps")
        self.update()
        self.assertEqual(P.stats(lamps)["instances"], 5)
        self.assertEqual(P.params(lamps)["curve"], "Lamps_Path")
        self.assertEqual(bpy.data.objects["Lamps_Path"].users_collection[0].name, "Paths")
        P.set_params(lamps, sides="both", offset=3.0)
        self.update()
        self.assertEqual(P.stats(lamps)["instances"], 10)


class GameReadinessTest(ProceduralTestCase):
    def test_export_keeps_instances_as_gpu_instancing(self):
        trees = self.trees()
        forest = P.scatter(self.terrain, trees, density=0.1, seed=3, name="Forest")
        fence = P.fence_along(ROAD, rail_style="pickets", terrain=self.terrain, name="Fence")
        lamps = P.array_along(ROAD, "lamp_post", spacing=6, offset=3, sides="both", terrain=self.terrain,
                              name="Lamps")
        self.update()
        expected = {o.name: P.stats(o) for o in (forest, fence, lamps)}

        result = export.export_scene(bake="off")
        gltf, _chunks = export.read_glb(result["glb"])
        nodes = gltf["nodes"]
        instanced = [n for n in nodes if fidelity.INSTANCING in (n.get("extensions") or {})]
        counts = {}
        for node in instanced:
            first = next(iter(node["extensions"][fidelity.INSTANCING]["attributes"].values()))
            parent = next(p["name"] for p in nodes if nodes.index(node) in p.get("children", []))
            counts[parent] = counts.get(parent, 0) + gltf["accessors"][first]["count"]
        for name, info in expected.items():
            with self.subTest(name):
                self.assertEqual(counts.get(name), info["instances"])
                self.assertEqual(result["instances"][name], info["instances"])
        self.assertEqual(len([n for n in nodes if n.get("name") == "GN Instance"]), 0)  # nothing left loose
        # one instanced node per source mesh
        self.assertEqual(sorted(i["mesh"] for i in result["instancing"] if i["parent"] == "Forest"),
                         ["Trees_1", "Trees_2"])
        self.assertIn(fidelity.INSTANCING, gltf["extensionsUsed"])
        manifest = json.loads(open(result["manifest"], encoding="utf-8").read())
        names = {e["name"] for e in manifest["entities"]}
        self.assertTrue({"Forest", "Fence", "Lamps", "Terrain"} <= names)
        self.assertFalse({"Trees_1", "Trees_2", "Lamp Post", "Fence Post"} & names)  # sources are not entities

    def test_colliders(self):
        trees = self.trees()
        plain = P.scatter(self.terrain, trees, density=0.05, name="Plain")
        self.assertEqual(plain.children, ())
        forest = P.scatter(self.terrain, trees, density=0.05, colliders="simple", name="Forest")
        self.update()
        proxy = bpy.data.objects["Forest_Collider"]
        self.assertIs(proxy.parent, forest)
        self.assertEqual(components.get_values(proxy, "Collider")["shape"], "mesh")
        self.assertFalse(components.has_component(forest, "Collider"))
        self.assertEqual(proxy.display_type, "WIRE")
        self.assertEqual(set(evaluated_mesh_data(proxy)[2]), {"GS_Collider"})
        verts, tris, _mats = evaluated_mesh_data(proxy)
        count = P.stats(forest)["instances"]
        self.assertEqual(len(verts), count * 16)  # an 8-sided trunk per tree
        self.assertEqual(P.params(forest)["collider_object"], "Forest_Collider")

        # other shapes: a Collider on the builder object, which the engine fits to every GPU instance
        for mode, shape in (("hull", "convex"), ("box", "box"), ("mesh", "mesh")):
            P.set_params(forest, colliders=mode)
            self.assertNotIn("Forest_Collider", bpy.data.objects)
            self.assertEqual(components.get_values(forest, "Collider")["shape"], shape)
        P.set_params(forest, colliders="none")
        self.assertFalse(components.has_component(forest, "Collider"))
        rocks = P.scatter(self.terrain, P.kit_sources("rock", 2, name="Rocks"), density=0.05, colliders="simple",
                          name="Boulders")
        self.assertEqual(rocks.children, ())  # no proxy: no extra bytes in the GLB
        self.assertEqual(components.get_values(rocks, "Collider")["shape"], "mesh")
        P.set_params(rocks, colliders="trunk")
        self.assertFalse(components.has_component(rocks, "Collider"))
        self.assertEqual([c.name for c in rocks.children], ["Boulders_Collider"])
        components.add_component(plain, "Collider", {"shape": "box"})  # someone else's Collider stays
        P.set_params(plain, colliders="hull")
        P.set_params(plain, colliders="none")
        self.assertEqual(components.get_values(plain, "Collider")["shape"], "box")

        cliff = P.cliff([(-5, 0), (5, 0)], name="Wall")
        self.assertEqual(components.get_values(cliff, "Collider")["shape"], "mesh")
        road = P.road_along(ROAD, colliders="none", name="Road")
        self.assertFalse(components.has_component(road, "Collider"))
        P.set_params(road, colliders="simple")
        self.assertTrue(components.has_component(road, "Collider"))

        result = export.export_scene(bake="off")
        manifest = json.loads(open(result["manifest"], encoding="utf-8").read())
        by_name = {e["name"]: e for e in manifest["entities"]}
        self.assertIn("Collider", by_name["Wall"]["components"])
        self.assertIn("Collider", by_name["Road"]["components"])

    def test_export_writes_sources_once_and_does_not_draw_collider_proxies(self):
        from test_export_fidelity import nodes_named, unused

        forest = P.scatter(self.terrain, self.trees(), density=0.08, seed=3, colliders="simple", name="Forest")
        pile = P.scatter(self.terrain, P.kit_sources("crate", [{"width": 1.0}], name="Crates"), density=0.02,
                         seed=4, name="Pile")
        components.add_component(pile, "Collider", {"shape": "box"})  # the engine boxes each instance
        self.update()
        self.assertGreaterEqual(P.stats(pile)["instances"], 4)
        result = export.export_scene(bake="off")
        gltf, _chunks = export.read_glb(result["glb"])
        nodes = nodes_named(gltf)
        self.assertFalse({"Trees_1", "Trees_2", "Crates_1"} & set(nodes))  # the sources: only their instances
        self.assertEqual(unused(gltf), {})
        self.assertGreater(result["pruned"]["meshes"], 0)
        manifest = json.loads(open(result["manifest"], encoding="utf-8").read())
        drawn = {e["name"]: e.get("drawn", True) for e in manifest["entities"]}
        self.assertEqual({k for k, v in drawn.items() if not v}, {"Forest_Collider"})
        self.assertTrue({"Terrain", "Forest", "Pile"} <= set(drawn))
        self.assertEqual(result["not_drawn"], ["Forest_Collider"])
        proxy = gltf["meshes"][nodes["Forest_Collider"]["mesh"]]["primitives"]
        self.assertEqual([list(p["attributes"]) for p in proxy], [["POSITION"]])  # no material, normals, UVs
        self.assertNotIn("material", proxy[0])
        self.assertNotIn("GS_Collider", [m["name"] for m in gltf["materials"]])
        trunks = gltf["accessors"][proxy[0]["attributes"]["POSITION"]]["count"]
        self.assertEqual(trunks, P.stats(forest)["instances"] * 16)  # 8-sided trunks, corners welded
        terrain = len(gltf["meshes"][nodes["Terrain"]["mesh"]]["primitives"])
        self.assertEqual(result["size"]["draw_calls"],
                         terrain + P.stats(forest)["draw_calls"] + P.stats(pile)["draw_calls"])
        self.assertIn({"parent": "Pile", "mesh": "Crates_1", "count": P.stats(pile)["instances"]},
                      result["instancing"])

    def test_colliders_need_the_registry(self):
        with mock.patch("gamestudio.core.registry.schema_ref", return_value=None):
            with self.assertRaises(P.ProceduralError):
                P.scatter(self.terrain, self.trees(), colliders="simple", name="X")
            wall = P.cliff([(-5, 0), (5, 0)], name="Wall")  # the default quietly skips
        self.assertFalse(components.has_component(wall, "Collider"))

    def test_faces_point_outward(self):
        cliff = P.cliff([(-10, 0), (0, 6), (10, 0)], name="Cliff")
        building = P.modular_building((9, 7), floors=2, roof="gable", name="House")
        hip = P.modular_building([(0, 0), (8, 0), (8, 5), (4, 9), (0, 5)], floors=1, roof="hip", name="Odd")
        road = P.road_along(ROAD, name="Road")
        self.update()
        self.assertGreater(signed_volume(cliff), 100.0)
        self.assertGreater(signed_volume(building), 10.0)
        self.assertGreater(signed_volume(hip), 10.0)
        depsgraph = bpy.context.evaluated_depsgraph_get()
        mesh = road.evaluated_get(depsgraph).to_mesh()
        asphalt = [p.normal.z for p in mesh.polygons if mesh.materials[p.material_index].name == "GS_Asphalt"]
        road.evaluated_get(depsgraph).to_mesh_clear()
        self.assertTrue(asphalt and min(asphalt) > 0.9)

    def test_budgets(self):
        trees = self.trees()
        forest = P.scatter(self.terrain, trees, density=50, min_distance=0.0, max_instances=300, name="Dense")
        wall = P.cliff(self.terrain, height=8, terrain=self.terrain, name="Rim")
        house = P.modular_building((12, 8), floors=3, name="House")
        road = P.road_along(ROAD, terrain=self.terrain, name="Road")
        self.update()
        self.assertEqual(P.stats(forest)["instances"], 300)
        self.assertLessEqual(P.stats(forest)["draw_calls"], 4)  # 2 tree kits x 2 materials, instanced
        self.assertLess(P.stats(wall)["triangles"], 10000)
        self.assertLess(P.stats(house)["triangles"], 8000)
        self.assertLessEqual(P.stats(house)["draw_calls"], 8)
        length = sum((Vector(b) - Vector(a)).length for a, b in zip(ROAD, ROAD[1:]))
        self.assertLess(P.stats(road)["triangles"], 40 * length)

    def test_realize(self):
        trees = self.trees()
        forest = P.scatter(self.terrain, trees, density=0.05, colliders="simple", name="Forest")
        self.update()
        expected = P.stats(forest)["triangles"]
        P.realize(forest)
        self.update()
        self.assertEqual(len(forest.modifiers), 0)
        self.assertEqual(P.stats(forest)["own_triangles"], expected)
        self.assertEqual(len(bpy.data.objects["Forest_Collider"].modifiers), 0)
        with self.assertRaises(P.ProceduralError):
            P.set_params(forest, density=1.0)
        rocks = P.scatter(self.terrain, P.kit_sources("rock", 2, name="Rocks"), density=0.05, colliders="hull",
                          name="Boulders")
        P.realize(rocks)  # one mesh: a hull per instance would be one hull around everything
        self.assertEqual(components.get_values(rocks, "Collider")["shape"], "mesh")

        coin = kits.create("collectible", {"name": "Coin"}, collection="Props")[0]
        components.add_component(coin, "Spin", {"speed": 3.0})
        coins = P.array_along(ROAD, coin, spacing=10, colliders="box", name="Coins")
        self.update()
        count = P.stats(coins)["instances"]
        made = P.realize(coins, mode="objects")
        self.assertEqual(len(made), count + 1)
        self.assertTrue(all(o.data == coin.data for o in made[1:]))  # linked duplicates
        self.assertTrue(all(components.get_values(o, "Spin")["speed"] == 3.0 for o in made[1:]))
        self.assertTrue(all(components.get_values(o, "Collider")["shape"] == "box" for o in made[1:]))
        self.assertFalse(components.has_component(coins, "Collider"))  # nothing left to collide with

    def test_old_group_versions_are_migrated(self):
        wall = P.cliff([(-5, 0), (5, 0)], height=7.5, seed=9, name="Wall")
        old = wall.modifiers["GS Cliff"].node_group
        version, specs, build = groups.BUILDERS["cliff"]
        with mock.patch.dict(groups.BUILDERS, {"cliff": (version + 1, specs, build)}):
            new = groups.ensure("cliff")
        self.assertIsNot(new, old)
        self.assertIs(wall.modifiers["GS Cliff"].node_group, new)
        self.assertNotIn(old, list(bpy.data.node_groups))
        self.assertEqual((P.params(wall)["height"], P.params(wall)["seed"]), (7.5, 9))
