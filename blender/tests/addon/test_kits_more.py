# SPDX-License-Identifier: GPL-3.0-or-later
"""The gameplay kits: vehicle, weapon, projectile, door, key, switch, pickup, water and sky, plus the
terrain relief. Assemblies (a root with child parts), part placement (wheels on their axles, the
door leaf on its hinge, the muzzle at the barrel tip), their clips, regeneration, the sky's world
and sun, and an export of a vehicle, a door and a projectile prefab."""

import json
import math
import random

import bmesh
import bpy
from mathutils import Matrix, Vector

from gamestudio import kits
from gamestudio.core import export, ids
from gamestudio.kits import animation, base, environment, geometry, interactive, materials, vehicles

import helpers
from test_kits import LOCATION, KitTestCase, extreme_params, random_params, vertex_hash

NEW = ("vehicle", "weapon", "projectile", "door", "key", "switch", "pickup", "water", "sky")
MESH_KITS = NEW[:-1]
WHEELS = ("Wheel_FL", "Wheel_FR", "Wheel_RL", "Wheel_RR")


def tris(mesh) -> int:
    return sum(len(p.vertices) - 2 for p in mesh.polygons)


def bounds_of(mesh, matrix=None):
    cos = [(matrix @ v.co) if matrix is not None else v.co for v in mesh.vertices]
    lo = Vector([min(c[i] for c in cos) for i in range(3)])
    hi = Vector([max(c[i] for c in cos) for i in range(3)])
    return lo, hi


def mesh_problems(mesh) -> list:
    """What keeps a mesh from being a set of closed, outward, clean shells."""
    bm = bmesh.new()
    bm.from_mesh(mesh)
    try:
        out = []
        if any(len(e.link_faces) != 2 for e in bm.edges):
            out.append("open or fanned edges")
        flipped = 0
        for e in bm.edges:
            if len(e.link_faces) == 2:
                dirs = []
                for f in e.link_faces:
                    loop = next(lp for lp in f.loops if lp.edge == e)
                    dirs.append((loop.vert.index, loop.link_loop_next.vert.index))
                flipped += dirs[0] == dirs[1]
        if flipped:
            out.append(f"{flipped} flipped edges")
        if bm.calc_volume(signed=True) <= 0:
            out.append("inward normals")
        if any(f.calc_area() < 1e-10 for f in bm.faces):
            out.append("zero-area faces")
        if any(len(f.verts) > 4 for f in bm.faces):
            out.append("n-gons")
        return out
    finally:
        bm.free()


def remove(objs):
    names = {o.name for root in objs for o in [root, *root.children_recursive]}
    for name in names:
        if name in bpy.data.objects:
            bpy.data.objects.remove(bpy.data.objects[name])


def child(root, suffix):
    return next(c for c in root.children if c.get(base.PART_PROP) == suffix)


def resolved(obj):
    kind, params = kits.kit_params(obj)
    return base.resolve(base.get(kind), params)


def at_end_of(obj, clip):
    """Show the clip's last frame and return the object's world matrix there."""
    action = animation.preview(obj, clip, fit=False)
    bpy.context.scene.frame_set(int(action.frame_range[1]))
    bpy.context.view_layer.update()
    return obj.matrix_world.copy()


class MoreKitsTestCase(KitTestCase):
    def setUp(self):
        super().setUp()
        bpy.context.scene.render.fps = 30
        bpy.context.scene.render.fps_base = 1.0

    def build(self, kind, params=None, location=LOCATION):
        """create() checked against the kit contract (test_kits) and the assembly contract."""
        objs = kits.create(kind, params, location=location)
        self.assert_game_ready(objs[0], kind, params or {}, location)
        self.assert_parts_ready(kind, objs)
        return objs

    def assert_parts_ready(self, kind, objs):
        root, parts = objs[0], objs[1:]
        k = base.get(kind)
        label = f"{kind} {kits.kit_params(root)[1]}"
        wanted = [f"{root.name}_{part.suffix}" for part in k.parts(resolved(root))] if k.parts else []
        self.assertEqual([o.name for o in parts], wanted, label)
        self.assertEqual(base.parts(root), parts, label)
        total = tris(root.data)
        for part in parts:
            self.assertIs(part.parent, root, label)
            self.assertEqual(part.matrix_parent_inverse, Matrix(), label)
            self.assertEqual(tuple(part.rotation_euler), (0.0, 0.0, 0.0), label)
            self.assertEqual(tuple(part.scale), (1.0, 1.0, 1.0), label)
            self.assertEqual(list(part.users_collection), list(root.users_collection), label)
            self.assertEqual(kits.kit_params(part), kits.kit_params(root), label)
            if part.type == "MESH":
                self.assertTrue(part.data.name.startswith(part.name), label)  # .001 while an old mesh has the name
                self.assertEqual(len(part.modifiers), 0, label)
                self.assertEqual(mesh_problems(part.data), [], f"{label}: {part.name}")
                self.assertEqual(len(part.data.uv_layers), 1, label)
                for mat in part.data.materials:
                    self.assertIn(mat.get(materials.PRESET_PROP), materials.preset_names(), label)
                total += tris(part.data)
            else:
                self.assertEqual(part.type, "EMPTY", label)
        self.assertLessEqual(total, k.max_tris, f"{label}: {total} triangles in all")


class CatalogTest(MoreKitsTestCase):
    def test_the_new_kits_are_listed(self):
        self.assertTrue(set(NEW) <= set(self.catalog), set(self.catalog))
        for kind, suffixes in (("vehicle", ("_Wheel_FL", "_Wheel_RR")), ("door", ("_Leaf",)),
                               ("switch", ("_Handle",)), ("weapon", ("_Muzzle",))):
            text = self.catalog[kind]["description"]
            for suffix in suffixes:
                self.assertIn(suffix, text, kind)
            self.assertIn("returns the root first", text, kind)
        self.assertNotIn("triangles", self.catalog["sky"]["description"])
        self.assertIn("background", self.catalog["sky"]["description"])

    def test_door_clips_take_an_axis(self):
        obj = helpers.new_object("Lid", helpers.new_mesh())
        action = animation.add_clip(obj, "door_open", {"angle": 90, "seconds": 0.5, "axis": "X"})
        keyed = {(fc.data_path, fc.array_index): fc for layer in action.layers for strip in layer.strips
                 for bag in strip.channelbags for fc in bag.fcurves}
        rx = [k.co[1] for k in keyed[("rotation_euler", 0)].keyframe_points]
        self.assertAlmostEqual(rx[-1], math.radians(90), places=4)
        self.assertTrue(all(abs(k.co[1]) < 1e-6 for k in keyed[("rotation_euler", 2)].keyframe_points))


class BuildTest(MoreKitsTestCase):
    def cases(self, kind, rng, count=5):
        entry = self.catalog[kind]["params"]
        out = [{}, extreme_params(entry, False), extreme_params(entry, True)]
        for style in entry["properties"].get("style", {}).get("enum", []):
            out += [{"style": style}, dict(extreme_params(entry, False), style=style),
                    dict(extreme_params(entry, True), style=style)]
        return out + [random_params(entry, rng) for _ in range(count)]

    def test_defaults_random_and_extreme_params(self):
        rng = random.Random(20261011)
        for kind in MESH_KITS:
            for params in self.cases(kind, rng):
                location = (rng.uniform(-20, 20), rng.uniform(-20, 20), rng.uniform(-2, 2))
                with self.subTest(kind=kind, params=params):
                    remove(self.build(kind, params, location))

    def test_same_seed_and_params_same_assembly(self):
        rng = random.Random(9)
        for kind in MESH_KITS:
            params = random_params(self.catalog[kind]["params"], rng)
            with self.subTest(kind=kind):
                a, b = kits.create(kind, params), kits.create(kind, params)
                self.assertEqual(len(a), len(b))
                for x, y in zip(a, b):
                    self.assertEqual(x.type, y.type)
                    self.assertEqual(tuple(x.location), tuple(y.location))
                    if x.type == "MESH":
                        self.assertEqual(vertex_hash(x), vertex_hash(y))
                        self.assertEqual([f.material_index for f in x.data.polygons],
                                         [f.material_index for f in y.data.polygons])


class VehicleTest(MoreKitsTestCase):
    def test_wheels_sit_on_their_axles(self):
        rng = random.Random(4)
        cases = [{}, {"style": "kart"}, {"style": "truck"}, {"length": 6.0, "wheel_radius": 0.6},
                 {"style": "kart", "width": 3.0, "wheel_radius": 1.5}, {"length": 1.0, "width": 6.0}]
        cases += [random_params(self.catalog["vehicle"]["params"], rng) for _ in range(6)]
        for params in cases:
            with self.subTest(params=params):
                objs = self.build("vehicle", params)
                root = objs[0]
                d = vehicles.layout(resolved(root))
                self.assertEqual([o.name for o in objs[1:]], [f"{root.name}_{w}" for w in WHEELS])
                for suffix, side, end in (("Wheel_FL", 1, -1), ("Wheel_FR", -1, -1), ("Wheel_RL", 1, 1),
                                          ("Wheel_RR", -1, 1)):
                    wheel = child(root, suffix)
                    loc = wheel.location
                    self.assertGreater(side * loc.x, 0, suffix)       # left (+X) is the driver's left
                    self.assertGreater(end * loc.y, 0, suffix)        # front at -Y
                    self.assertAlmostEqual(loc.z, d.R, places=5)      # the axle is a radius up
                    self.assertAlmostEqual(loc.y, d.axles[0 if end < 0 else 1], places=5)
                    lo, hi = bounds_of(wheel.data)
                    self.assertLess(abs(lo.y + hi.y) + abs(lo.z + hi.z) + abs(lo.x + hi.x), 1e-5, suffix)  # centred
                    self.assertAlmostEqual((hi.z - lo.z) / 2, d.R, places=5)
                    self.assertAlmostEqual(loc.z + lo.z, 0.0, places=5)  # it touches the ground
                    self.assertLess(hi.x - lo.x, hi.y - lo.y)            # the axle runs along X
                fl, fr = child(root, "Wheel_FL").location, child(root, "Wheel_FR").location
                self.assertAlmostEqual(fl.x, -fr.x, places=5)
                lo, hi = bounds_of(root.data)
                self.assertAlmostEqual(lo.z, 0.0, places=5)  # the body's box Collider reaches the ground
                remove(objs)

    def test_spin_clip_on_every_wheel(self):
        root, *wheels = kits.create("vehicle", {"spin_clip": True})
        actions = set()
        for wheel in wheels:
            (clip,) = animation.clips(wheel)
            self.assertEqual((clip["name"], clip["loop"], clip["kind"]), ("spin", True, "spin"))
            action = bpy.data.actions[clip["action"]]
            actions.add(action)
            keyed = {(fc.data_path, fc.array_index): [k.co[1] for k in fc.keyframe_points]
                     for layer in action.layers for strip in layer.strips for bag in strip.channelbags
                     for fc in bag.fcurves}
            rx = keyed[("rotation_euler", 0)]
            self.assertAlmostEqual(rx[0], 0.0, places=5)
            self.assertAlmostEqual(rx[-1], 2 * math.pi, places=4)  # rolls forward, toward -Y
            self.assertTrue(all(abs(v) < 1e-6 for v in keyed[("rotation_euler", 2)]))
            self.assertIsNone(wheel.animation_data.action)  # not active: the wheels stay put in the editor
        self.assertEqual(len(actions), 1)  # the four wheels share one clip
        self.assertIsNone(root.animation_data)
        plain = kits.create("vehicle")
        self.assertTrue(all(o.animation_data is None for o in plain))

    def test_regenerate_from_any_part_keeps_names_and_follows_renames(self):
        root, *wheels = kits.create("vehicle", {"name": "Kart", "style": "kart"})
        objects = len(bpy.data.objects)
        fl = child(root, "Wheel_FL")
        fl["marker"] = 7
        self.assertEqual(kits.kit_params(fl)[1]["name"], "Kart")
        kits.regenerate(fl, {"length": 2.4, "spin_clip": True})
        self.assertEqual(len(bpy.data.objects), objects)
        self.assertEqual(sorted(c.name for c in root.children), sorted(f"Kart_{w}" for w in WHEELS))
        self.assertEqual(fl["marker"], 7)
        self.assertAlmostEqual(fl.location.y, vehicles.layout(resolved(root)).axles[0], places=5)
        self.assertEqual(kits.kit_params(root)[1]["length"], 2.4)
        self.assertEqual([c["name"] for c in animation.clips(fl)], ["spin"])
        kits.regenerate(root, {"spin_clip": False})
        self.assertEqual(animation.clips(fl), [])
        kits.regenerate(root, {"name": "Racer"})
        self.assertEqual(sorted(c.name for c in root.children), sorted(f"Racer_{w}" for w in WHEELS))
        root.name = "Bolt"  # renamed by hand: the parts follow on the next regenerate
        kits.regenerate(fl, {})
        self.assertEqual(fl.name, "Bolt_Wheel_FL")
        self.assertEqual(kits.kit_params(fl)[1]["name"], "Bolt")
        bpy.data.objects.remove(child(root, "Wheel_RR"))  # a deleted part comes back
        kits.regenerate(root, {})
        self.assertEqual(len(base.parts(root)), 4)
        orphan = child(root, "Wheel_RL")
        bpy.data.objects.remove(root)
        with self.assertRaises(kits.KitError):
            kits.regenerate(orphan, {})

    def test_two_vehicles_keep_their_parts_apart(self):
        a = kits.create("vehicle", {"name": "Car"})
        b = kits.create("vehicle", {"name": "Car"})
        self.assertEqual(a[0].name, "Car")
        self.assertEqual(b[0].name, "Car.001")
        self.assertEqual(b[1].name, "Car.001_Wheel_FL")
        self.assertEqual(kits.kit_params(b[1])[1]["name"], "Car.001")


class DoorTest(MoreKitsTestCase):
    def test_leaf_hinges_on_its_edge_and_swings_away_from_the_front(self):
        for style in ("plain", "planked", "scifi"):
            for hinge in ("left", "right"):
                for angle in (100.0, -80.0):
                    params = {"style": style, "hinge": hinge, "open_angle": angle, "width": 1.2}
                    with self.subTest(params=params):
                        frame, leaf = self.build("door", params)
                        d = interactive.door_layout(resolved(frame))
                        self.assertEqual(leaf.name, f"{frame.name}_Leaf")
                        self.assertLess((leaf.location - Vector(d["pivot"])).length, 1e-6)
                        self.assertAlmostEqual(abs(leaf.location.x), 0.6 - interactive.GAP, places=6)
                        lo, hi = bounds_of(leaf.data)
                        sign = -d["hinge"]  # the leaf reaches across the opening from its hinge
                        self.assertAlmostEqual(lo.x if sign > 0 else hi.x, 0.0, places=5)
                        self.assertLessEqual(hi.x - lo.x, d["leaf_w"] + 1e-5)
                        edge = [v.co for v in leaf.data.vertices if abs(v.co.x) < 1e-6]
                        self.assertLess(min(abs(co.y) for co in edge), 0.015)  # the hinge runs along the edge
                        bpy.context.view_layer.update()
                        rest = leaf.matrix_world.copy()
                        free = Vector((sign * d["leaf_w"], -d["swing"] * d["t"] / 2, d["leaf_h"] / 2))
                        opened = at_end_of(leaf, "door_open")
                        for co in (Vector(), Vector((0.0, 0.0, d["leaf_h"]))):  # the hinge line stays put
                            self.assertLess((opened @ co - rest @ co).length, 1e-4)
                        moved = (opened @ free).y - (rest @ free).y
                        self.assertGreater(moved * angle, 0)  # positive angles open toward +Y
                        bpy.context.scene.frame_set(1)
                        remove([frame])

    def test_clips(self):
        frame, leaf = kits.create("door", {"hinge": "right", "open_angle": 90, "open_seconds": 0.5})
        clips = {c["name"]: c for c in animation.clips(leaf)}
        self.assertEqual(set(clips), {"door_open", "door_close"})
        self.assertFalse(clips["door_open"]["loop"])
        self.assertAlmostEqual(clips["door_open"]["seconds"], 0.5, places=4)
        self.assertEqual(clips["door_open"]["params"]["angle"], -90.0)  # a right-hand hinge turns clockwise
        self.assertIsNone(leaf.animation_data.action)  # closed in the editor
        kits.regenerate(leaf, {"clips": False})
        self.assertEqual(animation.clips(leaf), [])
        kits.regenerate(frame, {"clips": True, "hinge": "left"})
        self.assertEqual(animation.clips(leaf)[0]["params"]["angle"], 90.0)
        self.assertLess(leaf.location.x, 0)
        self.assertEqual(animation.clips(kits.create("door", {"clips": False})[1]), [])

    def test_the_frame_leaves_the_opening_open(self):
        W, H, f = 1.0, 2.1, 0.1
        frame = kits.create("door", {"width": W, "height": H, "frame": f})[0]
        front = sum(p.area for p in frame.data.polygons if p.normal.y < -0.99)
        self.assertAlmostEqual(front, 2 * f * H + (W + 2 * f) * f, places=5)


class WeaponTest(MoreKitsTestCase):
    def test_muzzle_at_the_barrel_tip(self):
        for params in ({}, {"size": 2.0}, {"length": 0.5}, {"length": 2.0, "size": 0.25}):
            with self.subTest(params=params):
                gun, muzzle = self.build("weapon", params)
                self.assertEqual((muzzle.name, muzzle.type), (f"{gun.name}_Muzzle", "EMPTY"))
                lo, _ = bounds_of(gun.data)
                self.assertAlmostEqual(muzzle.location.y, lo.y, places=6)  # nothing reaches past the muzzle
                tip = [v.co for v in gun.data.vertices if v.co.y < lo.y + 1e-6]
                centre = sum(tip, Vector()) / len(tip)
                self.assertLess((centre - muzzle.location).length, 1e-5)  # on the barrel's axis
                remove([gun])

    def test_muzzle_comes_and_goes_with_the_style(self):
        bow, rest = self.build("weapon", {"style": "bow"})
        self.assertLess(rest.location.y, 0)  # in front of the grip
        self.assertLess(abs(rest.location.x), 1e-6)
        rest_name = rest.name
        sword = self.build("weapon", {"style": "sword"})
        self.assertEqual(len(sword), 1)
        blade = sword[0]
        lo, hi = bounds_of(blade.data)
        self.assertGreater(hi.z, 4 * -lo.z)  # the blade points up from the grip
        kits.regenerate(bow, {"style": "sword"})
        self.assertNotIn(rest_name, bpy.data.objects)
        self.assertEqual(base.parts(bow), [])
        kits.regenerate(blade, {"style": "blaster"})
        self.assertEqual([p.name for p in base.parts(blade)], [f"{blade.name}_Muzzle"])


class ProjectileTest(MoreKitsTestCase):
    def emission(self, obj):
        out = []
        for mat in obj.data.materials:
            bsdf = next(n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED")
            out.append(bsdf.inputs["Emission Strength"].default_value)
        return out

    def test_glow_and_flight_direction(self):
        for style in ("bolt", "orb"):
            obj = self.build("projectile", {"style": style, "color": "#FF2E2E"})[0]
            self.assertGreater(max(self.emission(obj)), 0.0, style)
            self.assertTrue(all(m.get(materials.PRESET_PROP) == "emissive" for m in obj.data.materials))
            dark = self.build("projectile", {"style": style, "glow": 0.0})[0]
            self.assertEqual(max(self.emission(dark)), 0.0)
        bolt = kits.create("projectile")[0]
        front = min(bolt.data.vertices, key=lambda v: v.co.y)
        self.assertLess(abs(front.co.x) + abs(front.co.z), 1e-6)  # the point leads, along -Y
        arrow = kits.create("projectile", {"style": "arrow"})[0]
        head = min(arrow.data.vertices, key=lambda v: v.co.y).index
        face = next(p for p in arrow.data.polygons if head in p.vertices)
        self.assertEqual(arrow.data.materials[face.material_index].get(materials.PRESET_PROP), "iron")


class SmallPropsTest(MoreKitsTestCase):
    def test_keys_stand_facing_the_front(self):
        for style in ("classic", "card"):
            key = self.build("key", {"style": style, "size": 0.4})[0]
            dims = key.dimensions
            self.assertAlmostEqual(dims.x, 0.4, delta=0.02)
            self.assertGreater(dims.x, dims.z)
            self.assertGreater(dims.z, dims.y)

    def test_switch_handle_turns_on_its_axle(self):
        for style, sign in (("lever", 1), ("wall", -1)):
            with self.subTest(style=style):
                base_obj, handle = self.build("switch", {"style": style, "throw": 60})
                pivot = Vector(interactive.switch_layout(resolved(base_obj))["pivot"])
                self.assertLess((handle.location - pivot).length, 1e-6)
                (clip_on,) = [c for c in animation.clips(handle) if c["name"] == "switch_on"]
                self.assertEqual(clip_on["params"]["axis"], "X")
                self.assertEqual(clip_on["params"]["angle"], sign * 60.0)
                knob_local = max(handle.data.vertices, key=lambda v: v.co.length).co.copy()
                bpy.context.view_layer.update()
                before = handle.matrix_world @ knob_local
                after = at_end_of(handle, "switch_on") @ knob_local
                self.assertLess(abs(after.x - before.x), 1e-5)  # it turns about X
                if style == "lever":
                    self.assertGreater(before.y, LOCATION[1] + pivot.y)  # off: leaning back
                    self.assertLess(after.y, LOCATION[1] + pivot.y)      # on: leaning forward
                else:
                    self.assertLess(before.z, LOCATION[2] + pivot.z)     # off: down
                    self.assertGreater(after.z, LOCATION[2] + pivot.z)   # on: up
                self.assertIn("switch_off", [c["name"] for c in animation.clips(handle)])
                bpy.context.scene.frame_set(1)

    def test_pickups(self):
        for style in ("health", "ammo", "energy", "shield"):
            obj = self.build("pickup", {"style": style})[0]
            self.assertAlmostEqual(max(obj.dimensions), 0.5, delta=0.08, msg=style)


class WaterTest(MoreKitsTestCase):
    def test_surface_rim_and_depth(self):
        for shape in ("square", "round"):
            obj = self.build("water", {"shape": shape, "size": 6.0, "depth": 2.0, "waves": 0.2})[0]
            lo, hi = bounds_of(obj.data)
            self.assertAlmostEqual(lo.z, -2.0, places=6)
            self.assertGreater(hi.z, 0.0)
            names = [m.get(materials.PRESET_PROP) for m in obj.data.materials]
            self.assertEqual(names, ["water", "water"])
            top = [p for p in obj.data.polygons if p.normal.z > 0.5]
            self.assertTrue(all(p.material_index == 0 for p in top))
            rim = [v for v in obj.data.vertices if v.co.z > -1e-6 and math.hypot(v.co.x, v.co.y) > 2.9
                   and (shape == "round" or max(abs(v.co.x), abs(v.co.y)) > 2.999)]
            self.assertTrue(rim)
            self.assertTrue(all(abs(v.co.z) < 1e-6 for v in rim))  # the edge meets the shore at z = 0
        flat = kits.create("water", {"waves": 0.0, "resolution": 1})[0]
        self.assertEqual(sorted({round(v.co.z, 6) for v in flat.data.vertices}), [-1.0, 0.0])

    def test_bob_clip(self):
        obj = kits.create("water", {"bob": True}, location=(0, 0, 1))[0]
        (clip,) = animation.clips(obj)
        self.assertEqual((clip["name"], clip["loop"]), ("bob", True))
        kits.regenerate(obj, {"bob": False})
        self.assertEqual(animation.clips(obj), [])


class SkyTest(MoreKitsTestCase):
    def test_world_gradient_and_matching_sun(self):
        (sun,) = kits.create("sky", location=(0, 0, 10))
        self.assertEqual((sun.name, sun.type, sun.data.type), ("Sun", "LIGHT", "SUN"))
        self.assertIs(bpy.context.view_layer.objects.active, sun)
        horizon, zenith, ground, color, energy, elevation = environment.SKIES["day"]
        world = bpy.context.scene.world
        self.assertEqual(world.get(environment.SKY_PROP), "Sun")
        self.assertEqual(world[environment.SKY_COLORS_PROP].to_dict(),
                         {"horizon": horizon, "zenith": zenith, "ground": ground, "sun": color})
        self.assertLess((Vector(world.color) - Vector(materials.hex_to_linear(horizon))).length, 1e-5)
        background = next(n for n in world.node_tree.nodes if n.type == "BACKGROUND")
        self.assertTrue(background.inputs["Color"].is_linked)
        self.assertEqual(export._background(bpy.context.scene),
                         [round(c, 6) for c in materials.hex_to_linear(horizon)])  # what the game gets
        self.assertAlmostEqual(sun.data.energy, energy, places=5)
        el, az = math.radians(elevation), math.radians(210.0)
        to_sun = Vector((math.cos(el) * math.sin(az), math.cos(el) * math.cos(az), math.sin(el)))
        shines = sun.matrix_basis.to_3x3() @ Vector((0, 0, -1))
        self.assertLess((shines + to_sun).length, 1e-5)  # a sun lamp shines along its -Z
        self.assertEqual(kits.kit_params(sun)[0], "sky")

    def test_presets_colours_and_regenerate(self):
        (sun,) = kits.create("sky", {"preset": "night"})
        world = bpy.context.scene.world
        self.assertTrue(any(n.type == "TEX_VORONOI" for n in world.node_tree.nodes))  # stars
        worlds = len(bpy.data.worlds)
        kits.regenerate(sun, {"preset": "sunset", "sun_disc": False, "sun_strength": 1.25})
        self.assertIs(bpy.context.scene.world, world)
        self.assertEqual(len(bpy.data.worlds), worlds)
        horizon = environment.SKIES["sunset"][0]
        self.assertLess((Vector(world.color) - Vector(materials.hex_to_linear(horizon))).length, 1e-5)
        self.assertFalse(any(n.type == "TEX_VORONOI" for n in world.node_tree.nodes))
        self.assertFalse(any(n.type == "VECT_MATH" and n.operation == "DOT_PRODUCT" for n in world.node_tree.nodes))
        self.assertAlmostEqual(sun.data.energy, 1.25, places=5)
        kits.regenerate(sun, {"horizon": "#FF0000"})
        self.assertLess((Vector(world.color) - Vector((1, 0, 0))).length, 1e-5)
        (other,) = kits.create("sky", {"name": "Moon"})
        self.assertIs(bpy.context.scene.world, world)  # one sky per scene: the new one takes it over
        self.assertEqual(world.get(environment.SKY_PROP), "Moon")


class GeometryTest(MoreKitsTestCase):
    def test_concave_outlines_triangulate_inside_themselves(self):
        stairs = kits.create("stairs", {"steps": 5, "step_height": 0.2, "step_depth": 0.3, "width": 1.0})[0]
        side = sum(p.area for p in stairs.data.polygons if p.normal.x > 0.99)
        self.assertAlmostEqual(side, sum(0.2 * (i + 1) * 0.3 for i in range(5)), places=5)
        m = geometry.MeshBuilder()
        m.prism([(0, 0), (1, 0), (2, 0), (2, 1), (1.5, 0.2), (1, 1), (0.5, 0.2), (0, 1)], "Y", 0.0, 0.1)
        mesh = m.finish("Comb")
        self.assertEqual(mesh_problems(mesh), [])  # three corners in a line, concave notches


class TerrainReliefTest(MoreKitsTestCase):
    def heights(self, params):
        obj = kits.create("terrain", dict({"height": 0.0, "size": 20.0, "resolution": 20}, **params),
                          location=LOCATION)[0]
        self.assert_game_ready(obj, "terrain", params)
        return {(round(v.co.x, 4), round(v.co.y, 4)): v.co.z for v in obj.data.vertices if v.co.z > -0.5}, obj

    def test_relief_shapes(self):
        z, _ = self.heights({"relief": "hill", "relief_height": 6.0})
        self.assertAlmostEqual(z[0.0, 0.0], 6.0, places=5)
        self.assertAlmostEqual(z[10.0, 10.0], 0.0, places=5)
        z, obj = self.heights({"relief": "ramp", "relief_height": 4.0, "relief_size": 1.0})
        self.assertAlmostEqual(z[3.0, 10.0], 4.0, places=5)   # climbs toward +Y (direction 0)
        self.assertAlmostEqual(z[3.0, -10.0], 0.0, places=5)
        self.assertAlmostEqual(z[-5.0, 0.0], 2.0, places=5)
        self.assertAlmostEqual(min(v.co.z for v in obj.data.vertices), -1.0, places=6)  # the skirt stays below
        z, _ = self.heights({"relief": "ramp", "relief_height": 4.0, "relief_direction": 90.0, "relief_size": 0.5})
        self.assertAlmostEqual(z[10.0, -3.0], 4.0, places=5)   # toward +X, flat at both ends
        self.assertAlmostEqual(z[6.0, -3.0], 4.0, places=5)
        self.assertAlmostEqual(z[-6.0, 2.0], 0.0, places=5)
        self.assertAlmostEqual(z[0.0, 2.0], 2.0, places=5)
        z, _ = self.heights({"relief": "valley", "relief_height": 3.0})
        self.assertAlmostEqual(z[0.0, 7.0], 0.0, places=5)     # runs along Y
        self.assertAlmostEqual(z[8.0, 7.0], 3.0, places=5)
        z, _ = self.heights({"relief": "ridge", "relief_height": 3.0})
        self.assertAlmostEqual(z[0.0, -7.0], 3.0, places=5)
        self.assertAlmostEqual(z[-8.0, -7.0], 0.0, places=5)

    def test_round_and_island_shapes_take_a_relief(self):
        for shape in ("round", "island"):
            for relief in ("hill", "ramp", "valley", "ridge"):
                params = {"shape": shape, "relief": relief, "relief_height": 8.0, "relief_direction": 30.0}
                with self.subTest(params=params):
                    obj = self.make("terrain", params)
                    self.assert_game_ready(obj, "terrain", params)
                    self.discard(obj)

    def test_no_relief_is_the_terrain_as_before(self):
        plain = kits.create("terrain", {"seed": 3})[0]
        ignored = kits.create("terrain", {"seed": 3, "relief_height": 9.0, "relief_direction": 30.0})[0]
        zero = kits.create("terrain", {"seed": 3, "relief": "hill", "relief_height": 0.0})[0]
        self.assertEqual(vertex_hash(plain), vertex_hash(ignored))
        self.assertEqual(vertex_hash(plain), vertex_hash(zero))


class ExportTest(helpers.ProjectTestCase):
    def setUp(self):
        super().setUp()
        bpy.context.scene.name = "main"
        bpy.context.scene.render.fps = 30

    def test_vehicle_door_and_projectile_prefab_survive_the_export(self):
        car, *wheels = kits.create("vehicle", {"name": "Car", "spin_clip": True})
        frame, leaf = kits.create("door", {"name": "Door"}, location=(4, 0, 0))
        bolts = bpy.data.collections.new("Bolt")  # kept out of the level, spawned by the game
        export.set_prefab(bolts)
        (bolt,) = kits.create("projectile", {"name": "BoltMesh"}, collection=bolts)
        kits.create("sky")
        result = export.export_scene()
        with open(result["manifest"], "r", encoding="utf-8") as f:
            manifest = json.load(f)
        entities = {e["name"]: e for e in manifest["entities"]}
        for wheel in wheels:
            self.assertEqual(entities[wheel.name]["parent"], ids.get_id(car))
        self.assertEqual(entities["Door_Leaf"]["parent"], ids.get_id(frame))
        clips = {c["name"]: c for c in manifest["animations"]}
        self.assertEqual(set(clips), {"spin", "door_open", "door_close"})
        self.assertEqual(sorted(clips["spin"]["entities"]), sorted(ids.get_id(w) for w in wheels))
        self.assertEqual(clips["door_open"]["entities"], [ids.get_id(leaf)])
        self.assertEqual(sorted(entities["Door_Leaf"]["animations"]), ["door_close", "door_open"])
        self.assertEqual(manifest["settings"]["background"],
                         [round(c, 6) for c in materials.hex_to_linear(environment.SKIES["day"][0])])
        self.assertEqual([p["name"] for p in manifest["prefabs"]], ["Bolt"])
        self.assertNotIn("BoltMesh", entities)

        gltf, _ = export.read_glb(result["glb"])
        nodes = gltf["nodes"]
        index = {n["name"]: i for i, n in enumerate(nodes)}
        self.assertEqual(sorted(nodes[c]["name"] for c in nodes[index["Car"]]["children"]),
                         sorted(w.name for w in wheels))
        fl = nodes[index["Car_Wheel_FL"]]["translation"]  # Y-up: Blender (x, y, z) -> (x, z, -y)
        layout = vehicles.layout(resolved(car))
        expected = layout.wheels[0][1]
        self.assertLess((Vector(fl) - Vector((expected[0], expected[2], -expected[1]))).length, 1e-4)
        door_open = next(a for a in gltf["animations"] if a["name"] == "door_open")
        self.assertEqual({(nodes[c["target"]["node"]]["name"], c["target"]["path"]) for c in door_open["channels"]},
                         {("Door_Leaf", "rotation")})
        self.assertIn("KHR_lights_punctual", gltf.get("extensionsUsed", []))
        prefab, _ = export.read_glb(result["glb"].replace("main.glb", "main.prefabs.glb"))
        self.assertIn("BoltMesh", [n["name"] for n in prefab["nodes"]])
