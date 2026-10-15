# SPDX-License-Identifier: GPL-3.0-or-later
"""Asset kits: every kit builds game-ready meshes with default and random params; the catalog,
validation, determinism, regeneration and the shared material palette."""

import json
import math
import random
import unittest

import bmesh
import bpy
from mathutils import Matrix, Vector

from gamestudio import kits
from gamestudio.kits import base, materials

import helpers

EXPECTED = {"rock", "crate", "barrel", "tree", "bush", "fence", "stairs", "platform", "building",
            "terrain", "collectible", "character", "character_rigged"}
LOCATION = (3.0, -2.0, 1.5)


def random_params(schema: dict, rng: random.Random) -> dict:
    out = {}
    for name, s in schema["properties"].items():
        if name == "name":
            continue
        if s["type"] == "number":
            out[name] = rng.uniform(s["minimum"], s["maximum"])
        elif s["type"] == "integer":
            out[name] = rng.randint(s["minimum"], min(s["maximum"], s["minimum"] + 100000))
        elif s["type"] == "boolean":
            out[name] = rng.random() < 0.5
        elif "enum" in s:
            out[name] = rng.choice(s["enum"])
        elif "pattern" in s:
            out[name] = "#%06X" % rng.randrange(1 << 24)
    return out


def extreme_params(schema: dict, high: bool) -> dict:
    """Every number and integer at its maximum (or minimum)."""
    out = {}
    for name, s in schema["properties"].items():
        if s["type"] in ("number", "integer") and name != "seed":
            out[name] = s["maximum"] if high else s["minimum"]
    return out


def vertex_hash(obj) -> list:
    return [tuple(round(c, 6) for c in v.co) for v in obj.data.vertices]


class KitTestCase(unittest.TestCase):
    def setUp(self):
        helpers.reset_scene()
        self.catalog = {e["kind"]: e for e in kits.catalog()}

    def make(self, kind, params=None, location=LOCATION, **kwargs):
        """create() checked against the object contract; returns the mesh object. A rigged kit
        returns [armature, mesh] with the mesh skinned to the armature; an assembly its root first,
        then its parts named <root>_<suffix> and parented to it. Kits that build no mesh (the sky)
        are skipped here: test_kits_more covers them."""
        objs = kits.create(kind, params, location=location, **kwargs)
        kit = base.get(kind)
        if kit.root != "mesh":
            self.skipTest(f"{kind} builds no mesh")
        if kit.rig is None:
            if kit.parts is None:
                self.assertEqual(len(objs), 1, kind)
            root = objs[0]
            for part in objs[1:]:
                self.assertIs(part.parent, root, kind)
                self.assertTrue(part.name.startswith(root.name + "_"), part.name)
                self.assertEqual(kits.kit_params(part), kits.kit_params(root), kind)
            return root
        self.assertEqual([o.type for o in objs], ["ARMATURE", "MESH"], kind)
        arm, body = objs
        self.assertIs(body.parent, arm, kind)
        self.assertEqual(kits.kit_params(arm), kits.kit_params(body), kind)
        return body

    @staticmethod
    def discard(obj):
        """Remove a kit object with its parts (and the armature of a rigged one)."""
        root = obj.parent if obj.parent is not None and obj.parent.type == "ARMATURE" else None
        for part in obj.children_recursive:
            bpy.data.objects.remove(part)
        bpy.data.objects.remove(obj)
        if root is not None:
            bpy.data.objects.remove(root)

    def assert_game_ready(self, obj, kind, params, location=LOCATION):
        """The checks every kit's output has to pass (for a rigged kit `obj` is its mesh)."""
        label = f"{kind} {json.dumps(params, sort_keys=True)}"
        kit = base.get(kind)
        self.assertEqual(obj.type, "MESH", label)
        root = obj
        if kit.rig is not None:
            root = obj.parent
            self.assertEqual(root.type, "ARMATURE", label)
            self.assertIsNone(root.parent, label)
            self.assertEqual(tuple(obj.location), (0.0, 0.0, 0.0), label)
            self.assertEqual(tuple(tuple(r) for r in obj.matrix_parent_inverse), tuple(tuple(r) for r in Matrix()))
            self.assertEqual([(m.type, m.object) for m in obj.modifiers], [("ARMATURE", root)], label)
        else:
            self.assertIsNone(obj.parent, label)
            self.assertEqual(len(obj.modifiers), 0, label)
        self.assertLess((root.location - Vector(location)).length, 1e-4, label)  # float32 storage
        for o in {root, obj}:
            self.assertEqual(tuple(o.rotation_euler), (0.0, 0.0, 0.0), label)
            self.assertEqual(tuple(o.scale), (1.0, 1.0, 1.0), label)
        mesh = obj.data
        self.assertGreater(len(mesh.polygons), 0, label)

        tris = sum(len(p.vertices) - 2 for p in mesh.polygons)
        self.assertLessEqual(tris, kit.max_tris, f"{label}: {tris} triangles")

        self.assertEqual(len(mesh.uv_layers), 1, label)
        uvs = mesh.uv_layers[0].uv
        self.assertTrue(all(math.isfinite(d.vector.x) and math.isfinite(d.vector.y) for d in uvs), label)

        self.assertGreater(len(mesh.materials), 0, label)
        for mat in mesh.materials:
            self.assertIsNotNone(mat, label)
            self.assertIn(mat.get(materials.PRESET_PROP), materials.preset_names(), label)
        self.assertTrue(all(p.material_index < len(mesh.materials) for p in mesh.polygons), label)

        bm = bmesh.new()
        bm.from_mesh(mesh)
        try:
            boundary = [e for e in bm.edges if len(e.link_faces) == 1]
            fans = [e for e in bm.edges if len(e.link_faces) > 2]
            loose = [e for e in bm.edges if not e.link_faces] + [v for v in bm.verts if not v.link_faces]
            self.assertFalse(loose, f"{label}: loose geometry")
            if kit.closed:
                self.assertFalse(boundary, f"{label}: {len(boundary)} open edges")
                self.assertFalse(fans, f"{label}: {len(fans)} edges with more than two faces")
            flipped = 0
            for e in bm.edges:
                if len(e.link_faces) == 2:
                    dirs = []
                    for f in e.link_faces:
                        loop = next(lp for lp in f.loops if lp.edge == e)
                        dirs.append((loop.vert.index, loop.link_loop_next.vert.index))
                    flipped += dirs[0] == dirs[1]
            self.assertEqual(flipped, 0, f"{label}: inconsistent winding")
            self.assertGreater(bm.calc_volume(signed=True), 0.0, f"{label}: normals point inward")
            degenerate = [f for f in bm.faces if f.calc_area() < 1e-10]
            self.assertFalse(degenerate, f"{label}: {len(degenerate)} zero-area faces")
        finally:
            bm.free()

        points = [v.co for v in mesh.vertices]
        if kit.parts is not None:  # an assembly's footprint includes its parts (a car stands on its wheels)
            for part in obj.children:
                if part.type == "MESH":
                    points += [part.matrix_basis @ v.co for v in part.data.vertices]
        lo = Vector([min(co[i] for co in points) for i in range(3)])
        hi = Vector([max(co[i] for co in points) for i in range(3)])
        size = max((hi - lo).x, (hi - lo).y, (hi - lo).z)
        tol = 1e-4 + 1e-6 * size
        if kit.pivot == "base":
            self.assertAlmostEqual(lo.z, 0.0, delta=tol, msg=f"{label}: base not at the origin")
            self.assertTrue(lo.x <= 0 <= hi.x and lo.y <= 0 <= hi.y, f"{label}: origin outside the footprint")
        elif kit.pivot == "center":
            self.assertLess(((lo + hi) / 2).length, tol, f"{label}: origin not at the centre")
        elif kit.pivot == "grip":
            self.assertTrue(all(lo[i] <= 0 <= hi[i] for i in range(3)), f"{label}: origin outside the object")
        else:
            self.assertTrue(lo.z < 0 <= hi.z, f"{label}: ground level not at z=0")
            self.assertLess(abs((lo + hi).x) + abs((lo + hi).y), tol, f"{label}: not centred")

        kind_back, params_back = kits.kit_params(obj)
        self.assertEqual(kind_back, kind)
        for key, value in params.items():
            self.assertEqual(params_back[key], value, label)


class CatalogTest(KitTestCase):
    def test_catalog_lists_the_starter_set(self):
        self.assertTrue(EXPECTED <= set(self.catalog), set(self.catalog))
        self.assertEqual(sorted(self.catalog), kits.kinds())

    def test_entries_follow_the_contract(self):
        for kind, entry in self.catalog.items():
            self.assertEqual(set(entry), {"kind", "label", "description", "params"}, kind)
            self.assertEqual(entry["kind"], kind)
            self.assertTrue(entry["label"] and entry["description"], kind)
            schema = entry["params"]
            self.assertEqual(schema["type"], "object", kind)
            self.assertFalse(schema["additionalProperties"], kind)
            json.dumps(schema)  # plain JSON
            props = schema["properties"]
            self.assertIn("seed", props, kind)
            self.assertIn("name", props, kind)
            for name, s in props.items():
                self.assertIn("default", s, f"{kind}.{name}")
                self.assertTrue(s.get("description"), f"{kind}.{name}")
                if s["type"] in ("number", "integer"):
                    self.assertLessEqual(s["minimum"], s["default"], f"{kind}.{name}")
                    self.assertLessEqual(s["default"], s["maximum"], f"{kind}.{name}")
                if "enum" in s:
                    self.assertIn(s["default"], s["enum"], f"{kind}.{name}")

    def test_catalog_is_a_copy(self):
        entry = kits.catalog()[0]
        entry["params"]["properties"].clear()
        self.assertTrue(kits.catalog()[0]["params"]["properties"])


class BuildTest(KitTestCase):
    def test_every_kit_builds_with_defaults(self):
        coll = bpy.data.collections.new("Kits")
        bpy.context.scene.collection.children.link(coll)
        for kind in self.catalog:
            with self.subTest(kind=kind):
                obj = self.make(kind, None, collection=coll)
                for o in {obj, obj.parent} - {None}:
                    self.assertIn(o.name, coll.objects)
                    self.assertTrue(o.name.startswith(self.catalog[kind]["label"]))
                    self.assertEqual(o.data.name, o.name)
                self.assert_game_ready(obj, kind, {})

    def test_every_kit_builds_with_random_params(self):
        rng = random.Random(20261010)
        for kind, entry in self.catalog.items():
            for i in range(6):
                params = random_params(entry["params"], rng)
                location = (rng.uniform(-50, 50), rng.uniform(-50, 50), rng.uniform(-5, 5))
                with self.subTest(kind=kind, params=params):
                    obj = self.make(kind, params, location=location)
                    self.assert_game_ready(obj, kind, params, location)
                    self.discard(obj)

    def test_extreme_params_stay_within_budget(self):
        for kind, entry in self.catalog.items():
            for high in (False, True):
                params = extreme_params(entry["params"], high)
                with self.subTest(kind=kind, high=high):
                    obj = self.make(kind, params)
                    self.assert_game_ready(obj, kind, params)
                    self.discard(obj)

    def test_busiest_building_stays_within_budget(self):
        params = {"width": 40, "depth": 40, "floors": 5, "windows": 10, "side_windows": 10,
                  "window_width": 0.3, "window_height": 0.3, "sill_height": 0.2, "door_width": 0.6}
        obj = kits.create("building", params, location=LOCATION)[0]
        self.assert_game_ready(obj, "building", params)

    def test_collection_by_name_and_active_object(self):
        obj = kits.create("crate", {"name": "SupplyCrate"}, collection="Props")[0]
        self.assertEqual(obj.name, "SupplyCrate")
        self.assertIn(obj.name, bpy.data.collections["Props"].objects)
        self.assertIn("Props", bpy.context.scene.collection.children)
        self.assertIs(bpy.context.view_layer.objects.active, obj)
        again = kits.create("crate", {"name": "SupplyCrate"}, collection="Props")[0]
        self.assertNotEqual(again.name, obj.name)
        self.assertEqual(len(bpy.data.collections["Props"].objects), 2)

    def test_kits_share_preset_materials(self):
        a = kits.create("crate")[0]
        b = kits.create("barrel")[0]
        self.assertIs(a.data.materials[0], b.data.materials[0])  # both GS_Wood
        self.assertEqual(a.data.materials[0].name, "GS_Wood")


class DeterminismTest(KitTestCase):
    def test_same_seed_same_mesh(self):
        rng = random.Random(7)
        for kind, entry in self.catalog.items():
            params = random_params(entry["params"], rng)
            with self.subTest(kind=kind):
                a = self.make(kind, params)
                b = self.make(kind, params)
                self.assertEqual(vertex_hash(a), vertex_hash(b))
                self.assertEqual([f.material_index for f in a.data.polygons],
                                 [f.material_index for f in b.data.polygons])

    def test_seed_changes_organic_kits(self):
        for kind in ("rock", "bush", "tree", "terrain"):
            with self.subTest(kind=kind):
                a = kits.create(kind, {"seed": 1})[0]
                b = kits.create(kind, {"seed": 2})[0]
                self.assertNotEqual(vertex_hash(a), vertex_hash(b))

    def test_params_change_the_mesh(self):
        small = kits.create("rock", {"size": 1.0})[0]
        big = kits.create("rock", {"size": 4.0})[0]
        self.assertAlmostEqual(max(small.dimensions.x, small.dimensions.y), 1.0, places=3)
        self.assertAlmostEqual(max(big.dimensions.x, big.dimensions.y), 4.0, places=3)
        tall = kits.create("tree", {"height": 9.0})[0]
        self.assertAlmostEqual(tall.dimensions.z, 9.0, delta=0.9)
        stairs = kits.create("stairs", {"steps": 5, "step_height": 0.2})[0]
        self.assertAlmostEqual(stairs.dimensions.z, 1.0, places=4)


class DogfoodFixesTest(KitTestCase):
    """Friction from building a floating-island game through MCP (.dev/dogfood/FRICTION.md F4, F22, F23)."""

    def materials_by_facing(self, obj):
        """Material presets of the upward faces, and the area of each on the downward ones."""
        mesh = obj.data
        names = [m.get(materials.PRESET_PROP) for m in mesh.materials]
        down = {}
        for poly in mesh.polygons:
            if poly.normal.z < -0.3:
                name = names[poly.material_index]
                down[name] = down.get(name, 0.0) + poly.area
        up = {names[p.material_index] for p in mesh.polygons if p.normal.z > 0.9}
        return up, down

    def test_round_and_island_terrain(self):
        for shape in ("round", "island"):
            for params in ({}, {"size": 60, "height": 6, "resolution": 64, "underside": 1.2, "flatten_center": 0.4},
                           {"size": 6, "resolution": 4, "height": 0}):
                params = dict(params, shape=shape)
                with self.subTest(params=params):
                    obj = self.make("terrain", params)
                    self.assert_game_ready(obj, "terrain", params)
                    size = params.get("size", 20.0)
                    lo = min(v.co.z for v in obj.data.vertices)
                    width = max(obj.dimensions.x, obj.dimensions.y)
                    up, down = self.materials_by_facing(obj)
                    self.assertIn("grass", up)
                    if shape == "round":
                        self.assertAlmostEqual(width, size, delta=1e-3 * size)  # a disc, not a square
                        self.assertAlmostEqual(min(obj.dimensions.x, obj.dimensions.y), width, delta=1e-3 * size)
                        self.assertAlmostEqual(lo, -1.0, places=4)              # the skirt
                    else:
                        depth = params.get("underside", 0.6) * size
                        self.assertAlmostEqual(lo, -depth, delta=0.1 * depth)   # tapers to a point
                        self.assertAlmostEqual(width, size, delta=0.2 * size)   # ragged outline
                        self.assertLessEqual(set(down), {"stone", "dirt"})      # a rocky underside under a dirt lip
                        self.assertGreater(down["stone"], 4 * down.get("dirt", 0.0))
                        near_tip = [v for v in obj.data.vertices if v.co.z < -0.9 * depth]
                        self.assertLess(max(abs(v.co.x) for v in near_tip), 0.25 * size)
                    self.discard(obj)
        a = self.make("terrain", {"shape": "island", "seed": 1})
        b = self.make("terrain", {"shape": "island", "seed": 2})
        self.assertNotEqual(vertex_hash(a), vertex_hash(b))

    def test_square_terrain_is_unchanged_by_the_new_shapes(self):
        obj = self.make("terrain", {"resolution": 8})
        self.assertEqual(len(obj.data.vertices), 9 * 9 + 4 * 8 + 1)
        self.assertEqual(len(obj.data.polygons), 8 * 8 + 2 * 4 * 8)

    def test_terrain_resolution_up_to_128(self):
        # Big terrains that rivers and roads carve need finer cells than the old cap of 64.
        self.assertEqual(base.get("terrain").max_tris, 2 * 128 * 128 + 12 * 128)
        for shape in ("square", "round", "island"):
            params = {"shape": shape, "resolution": 128, "size": 150, "height": 4}
            with self.subTest(shape=shape):
                obj = self.make("terrain", params)
                self.assert_game_ready(obj, "terrain", params)  # within max_tris, closed, ...
                if shape == "square":
                    self.assertEqual(len(obj.data.polygons), 128 * 128 + 2 * 4 * 128)
                self.discard(obj)
        with self.assertRaises(kits.KitError):
            kits.create("terrain", {"resolution": 129})

    def test_big_rocks(self):
        obj = self.make("rock", {"size": 30, "detail": 4, "seed": 3})
        self.assert_game_ready(obj, "rock", {"size": 30, "detail": 4})
        self.assertAlmostEqual(max(obj.dimensions.x, obj.dimensions.y), 30, places=2)
        self.assertEqual(sum(len(p.vertices) - 2 for p in obj.data.polygons), 5120)

    def test_no_ngons(self):
        for kind in self.catalog:
            for params in ({}, {"style": "pine"} if kind == "tree" else {}):
                with self.subTest(kind=kind, params=params):
                    obj = self.make(kind, params)
                    sizes = {len(p.vertices) for p in obj.data.polygons}
                    self.assertLessEqual(max(sizes), 4, f"{kind}: n-gons {sorted(sizes)}")
                    self.discard(obj)

    def test_params_report_the_real_name(self):
        obj = self.make("terrain", {"size": 10})
        obj.name = "Island_Top"
        self.assertEqual(kits.kit_params(obj)[1]["name"], "Island_Top")
        kits.regenerate(obj, {"height": 3.0})
        self.assertEqual(obj.name, "Island_Top")
        self.assertEqual(json.loads(obj[base.KIT_PROP])["params"]["name"], "Island_Top")
        kits.regenerate(obj, {"name": "Island_Base"})  # an explicit name renames it
        self.assertEqual((obj.name, obj.data.name, kits.kit_params(obj)[1]["name"]), ("Island_Base",) * 3)
        twin = self.make("crate", {"name": "Box"})
        again = self.make("crate", {"name": "Box"})
        self.assertEqual(kits.kit_params(again)[1]["name"], again.name)
        self.assertNotEqual(again.name, twin.name)

    def test_dimensions_are_current_right_after_regenerate(self):  # F11
        rock = self.make("rock", {"size": 1.0})
        bpy.context.view_layer.update()  # evaluated once, as in a live session
        kits.regenerate(rock, {"size": 5.0})
        self.assertAlmostEqual(max(rock.dimensions.x, rock.dimensions.y), 5.0, places=3)

    def test_rigged_names(self):
        body = self.make("character_rigged", {"name": "Hero", "clips": False})
        arm = body.parent
        arm.name = "Player"
        self.assertEqual(kits.kit_params(body)[1]["name"], "Player")
        kits.regenerate(body, {"name": "Knight"})
        self.assertEqual((arm.name, body.name, arm.data.name), ("Knight", "Knight_Mesh", "Knight"))


class MeshBuilderTest(KitTestCase):
    def test_custom_model_with_the_kit_toolkit(self):
        """The run_python modelling path: MeshBuilder primitives straight to a game-ready object."""
        from gamestudio.kits.geometry import MeshBuilder, capsule_profile

        m = MeshBuilder()
        wood, iron = m.slot("wood"), m.slot("iron")
        m.box((-0.6, -0.4, 0.7), (0.6, 0.4, 0.78), wood)
        for x in (-0.5, 0.5):
            for y in (-0.3, 0.3):
                m.lathe([(0.04, 0.0), (0.03, 0.7)], 6, iron, matrix=Matrix.Translation((x, y, 0.0)))
        m.prism([(-0.1, 0.78), (0.1, 0.78), (0.0, 0.9)], "X", -0.2, 0.2, wood)
        m.lathe(capsule_profile(0.05, 0.3, z0=0.78), 8, iron, matrix=Matrix.Translation((0.4, 0.0, 0.0)))
        obj = m.to_object("Table", location=(1.0, 2.0, 0.0), collection="Props")
        self.assertEqual(obj.name, "Table")
        self.assertIn(obj.name, bpy.data.collections["Props"].objects)
        self.assertEqual([mat.name for mat in obj.data.materials], ["GS_Wood", "GS_Iron"])
        self.assertLess((obj.location - Vector((1.0, 2.0, 0.0))).length, 1e-6)
        self.assertEqual(len(obj.data.uv_layers), 1)
        bm = bmesh.new()
        bm.from_mesh(obj.data)
        self.assertFalse([e for e in bm.edges if len(e.link_faces) != 2])
        self.assertGreater(bm.calc_volume(signed=True), 0)
        bm.free()


class ValidationTest(KitTestCase):
    def assert_kit_error(self, fragment, fn, *args, **kwargs):
        with self.assertRaises(kits.KitError) as ctx:
            fn(*args, **kwargs)
        self.assertIn(fragment, str(ctx.exception))
        self.assertIsInstance(ctx.exception, ValueError)

    def test_bad_input_raises_kit_error(self):
        objects = len(bpy.data.objects)
        self.assert_kit_error("Unknown kit 'castle'", kits.create, "castle")
        self.assert_kit_error("Available kits", kits.create, "castle")
        self.assert_kit_error("unknown param(s) colour", kits.create, "rock", {"colour": "red"})
        self.assert_kit_error("between 0.1 and 40.0", kits.create, "rock", {"size": 100})
        self.assert_kit_error("must be a number", kits.create, "rock", {"size": "big"})
        self.assert_kit_error("must be a number", kits.create, "rock", {"size": True})
        self.assert_kit_error("whole number", kits.create, "rock", {"detail": 1.5})
        self.assert_kit_error("one of round, pine", kits.create, "tree", {"style": "palm"})
        self.assert_kit_error("true or false", kits.create, "crate", {"braces": "yes"})
        self.assert_kit_error("#RRGGBB", kits.create, "character", {"color": "blue"})
        self.assert_kit_error("params must be an object", kits.create, "rock", [1, 2])
        self.assert_kit_error("three numbers", kits.create, "rock", location=(1, 2))
        self.assert_kit_error("collection must be", kits.create, "rock", collection=42)
        self.assertEqual(len(bpy.data.objects), objects, "a failed create left objects behind")

    def test_integral_floats_are_accepted_for_integers(self):
        obj = kits.create("rock", {"detail": 3.0, "seed": 5.0})[0]
        self.assertEqual(kits.kit_params(obj)[1]["detail"], 3)


class RegenerateTest(helpers.ProjectTestCase):
    def test_regenerate_keeps_the_object(self):
        from gamestudio.core import components

        obj = kits.create("rock", {"size": 1.0, "seed": 4}, location=(5, 0, 0))[0]
        obj.name = "BigRock"
        components.add_component(obj, "Spin", {"speed": 2})
        materials.apply(obj, "gold")
        old_mesh = obj.data.name
        same = kits.regenerate(obj, {"size": 3.0})
        self.assertIs(same, obj)
        self.assertEqual(obj.name, "BigRock")
        self.assertEqual(tuple(obj.location), (5.0, 0.0, 0.0))
        self.assertAlmostEqual(max(obj.dimensions.x, obj.dimensions.y), 3.0, places=3)
        self.assertEqual(components.list_components(obj), ["Spin"])
        self.assertEqual(obj.data.materials[0].name, "GS_Gold")  # the user's material survives
        self.assertNotIn(old_mesh, bpy.data.meshes)               # the old mesh is gone
        kind, params = kits.kit_params(obj)
        self.assertEqual((kind, params["size"], params["seed"]), ("rock", 3.0, 4))

    def test_regenerate_rejects_other_objects(self):
        obj = helpers.new_object("Plain", helpers.new_mesh())
        with self.assertRaises(kits.KitError):
            kits.regenerate(obj, {})
        rock = kits.create("rock")[0]
        with self.assertRaises(kits.KitError):
            kits.regenerate(rock, {"size": -1})


class MaterialsTest(KitTestCase):
    def test_presets(self):
        names = [p["name"] for p in materials.presets()]
        for required in ("stone", "wood", "metal", "grass", "plastic", "emissive"):
            self.assertIn(required, names)
        for p in materials.presets():
            self.assertRegex(p["color"], r"^#[0-9A-F]{6}$")
            self.assertTrue(p["material"].startswith("GS_"))
            self.assertTrue(p["description"])
        json.dumps(materials.presets())

    def test_apply_replaces_every_slot_or_one(self):
        obj = kits.create("barrel")[0]
        self.assertEqual(len(obj.material_slots), 3)
        mat = materials.apply(obj, "metal")
        self.assertEqual({s.material.name for s in obj.material_slots}, {"GS_Metal"})
        self.assertIs(mat, obj.material_slots[0].material)
        materials.apply(obj, "gold", slot=1)
        self.assertEqual([s.material.name for s in obj.material_slots], ["GS_Metal", "GS_Gold", "GS_Metal"])
        with self.assertRaises(kits.KitError):
            materials.apply(obj, "gold", slot=3)

    def test_apply_to_an_object_without_slots(self):
        obj = helpers.new_object("Plain", helpers.new_mesh())
        materials.apply(obj, "stone")
        self.assertEqual([s.material.name for s in obj.material_slots], ["GS_Stone"])

    def test_values_and_overrides(self):
        mat = materials.material("emissive")
        bsdf = next(n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED")
        self.assertGreater(bsdf.inputs["Emission Strength"].default_value, 0)
        gold = materials.material("gold")
        self.assertEqual(next(n for n in gold.node_tree.nodes if n.type == "BSDF_PRINCIPLED")
                         .inputs["Metallic"].default_value, 1.0)

        red = materials.material("plastic", color="#FF0000", roughness=0.2)
        self.assertNotEqual(red.name, "GS_Plastic")
        self.assertTrue(red.name.startswith("GS_Plastic_"))
        bsdf = next(n for n in red.node_tree.nodes if n.type == "BSDF_PRINCIPLED")
        self.assertAlmostEqual(bsdf.inputs["Base Color"].default_value[0], 1.0, places=5)
        self.assertAlmostEqual(bsdf.inputs["Base Color"].default_value[1], 0.0, places=5)
        self.assertAlmostEqual(bsdf.inputs["Roughness"].default_value, 0.2, places=5)
        self.assertIs(materials.material("plastic", roughness=0.2, color="#FF0000"), red)  # reused

        grey = materials.material("plastic", color="#808080")  # sRGB hex -> linear
        self.assertAlmostEqual(grey.diffuse_color[0], 0.2158605, places=4)
        linear = materials.material("plastic", color=[0.5, 0.25, 0.0])
        self.assertAlmostEqual(linear.diffuse_color[0], 0.5, places=5)

        named = materials.material("stone", name="CliffRock", color="#445566")
        self.assertEqual(named.name, "CliffRock")
        self.assertEqual(named.get(materials.PRESET_PROP), "stone")

    def test_aliases_and_see_through_presets(self):
        self.assertIs(materials.material("dark_wood"), materials.material("wood_dark"))
        self.assertEqual(materials.material("glow").name, "GS_Emissive")
        glass = materials.material("glass")
        self.assertLess(glass.diffuse_color[3], 1.0)
        entry = next(p for p in materials.presets() if p["name"] == "wood_dark")
        self.assertIn("dark_wood", entry["aliases"])
        with self.assertRaises(kits.KitError):
            materials.material(None)

    def test_shared_material_keeps_user_tweaks(self):
        stone = materials.material("stone")
        stone.diffuse_color = (1, 0, 0, 1)
        stone.name = "MyStone"
        self.assertIs(materials.material("stone"), stone)
        self.assertEqual(tuple(stone.diffuse_color), (1, 0, 0, 1))

    def test_bad_material_input(self):
        obj = kits.create("crate")[0]
        for fn, args, kwargs, fragment in (
            (materials.apply, (obj, "marble"), {}, "Unknown material preset"),
            (materials.apply, (obj, "stone"), {"shine": 1}, "Unknown material override"),
            (materials.apply, (obj, "stone"), {"roughness": 2}, "from 0 to 1"),
            (materials.apply, (obj, "stone"), {"color": "red"}, "#RRGGBB"),
            (materials.apply, (obj, "stone"), {"color": [1, 2]}, "3 or 4"),
        ):
            with self.subTest(fragment=fragment):
                with self.assertRaises(kits.KitError) as ctx:
                    fn(*args, **kwargs)
                self.assertIn(fragment, str(ctx.exception))
        empty = helpers.new_object("Empty")
        with self.assertRaises(kits.KitError):
            materials.apply(empty, "stone")
