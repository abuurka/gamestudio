# SPDX-License-Identifier: GPL-3.0-or-later
"""Rigged characters and procedural animation: the character_rigged kit (armature + skinned mesh),
the humanoid and object clips of kits.animation, and how they are stored for the glTF exporter."""

import json
import math

import bpy
import numpy as np
from mathutils import Euler, Matrix, Quaternion, Vector

from gamestudio import kits
from gamestudio.core import components, export, ids
from gamestudio.kits import animation, base, rig

import helpers

LOCATION = (2.0, -1.0, 0.5)
HUMANOID_CLIPS = {"Idle": True, "Walk": True, "Run": True, "Jump": False, "JumpStart": False, "JumpAir": True,
                  "Land": False, "Wave": True, "Fall": True}
OBJECT_KINDS = ("spin", "bob", "pulse", "door_open", "door_close", "sway", "hover")


def character(params=None, location=(0, 0, 0)):
    arm, body = kits.create("character_rigged", params, location=location)
    return arm, body


def fcurves(action):
    return [fc for layer in action.layers for strip in layer.strips for bag in strip.channelbags for fc in bag.fcurves]


def evaluated(obj):
    return obj.evaluated_get(bpy.context.evaluated_depsgraph_get())


def deformed_z(body) -> np.ndarray:
    """World z of every deformed vertex of the skinned mesh at the current frame."""
    ev = evaluated(body)
    mesh = ev.to_mesh()
    co = np.empty(len(mesh.vertices) * 3)
    mesh.vertices.foreach_get("co", co)
    ev.to_mesh_clear()
    world = np.array(body.matrix_world)
    co = co.reshape(-1, 3)
    return co @ world[2, :3] + world[2, 3]


def frames_of(action):
    start, end = (int(round(f)) for f in action.frame_range)
    return range(start, end + 1)


class AnimTestCase(helpers.ProjectTestCase):
    def setUp(self):
        super().setUp()
        bpy.context.scene.render.fps = 30
        bpy.context.scene.render.fps_base = 1.0

    def same_rotation(self, a: Quaternion, b: Quaternion, msg=None, places=5):
        self.assertAlmostEqual(abs(a.normalized().dot(b.normalized())), 1.0, places=places, msg=msg)


class RigTest(AnimTestCase):
    def test_create_returns_the_armature_and_its_skinned_mesh(self):
        objs = kits.create("character_rigged", {"name": "Hero"}, location=LOCATION, collection="Actors")
        self.assertEqual([o.type for o in objs], ["ARMATURE", "MESH"])
        arm, body = objs
        self.assertEqual((arm.name, body.name), ("Hero", "Hero_Mesh"))
        self.assertEqual((arm.data.name, body.data.name), ("Hero", "Hero_Mesh"))
        for obj in objs:
            self.assertIn(obj.name, bpy.data.collections["Actors"].objects)
            self.assertEqual(tuple(obj.rotation_euler), (0, 0, 0))
            self.assertEqual(tuple(obj.scale), (1, 1, 1))
        self.assertLess((arm.location - Vector(LOCATION)).length, 1e-6)
        self.assertIsNone(arm.parent)
        self.assertIs(body.parent, arm)
        self.assertEqual(tuple(body.location), (0, 0, 0))
        self.assertEqual(body.matrix_parent_inverse, Matrix())
        self.assertEqual([(m.type, m.object) for m in body.modifiers], [("ARMATURE", arm)])
        self.assertIs(bpy.context.view_layer.objects.active, arm)
        kind, params = kits.kit_params(arm)
        self.assertEqual(kind, "character_rigged")
        self.assertEqual(kits.kit_params(body), (kind, params))
        self.assertEqual(base.rig_parts(body), (arm, body))
        self.assertEqual(base.rig_parts(arm), (arm, body))
        entry = next(e for e in kits.catalog() if e["kind"] == "character_rigged")
        self.assertIn("two objects", entry["description"])
        self.assertIn("Animator", entry["description"])

    def test_skeleton(self):
        arm, body = character()
        bones = arm.data.bones
        self.assertEqual(set(bones.keys()), set(rig.HUMANOID_BONES))
        for name, parent in rig.HUMANOID_PARENTS.items():
            self.assertEqual(bones[name].parent.name if bones[name].parent else None, parent, name)
            x_axis = bones[name].matrix_local.col[0].xyz
            self.assertGreater(x_axis.dot(Vector((1, 0, 0))), 0.98, f"{name}: local X is not +X")
            self.assertEqual(bones[name].use_deform, name != "root", name)
        for side, sign in (("L", 1), ("R", -1)):
            self.assertGreater(sign * bones[f"upper_arm_{side}"].head_local.x, 0, side)  # .L is the character's left (+X)
            self.assertGreater(sign * bones[f"upper_leg_{side}"].head_local.x, 0, side)
            self.assertLess(bones[f"foot_{side}"].tail_local.y, bones[f"foot_{side}"].head_local.y)  # toes forward (-Y)
        self.assertAlmostEqual(bones["head"].tail_local.z, 1.8, places=4)
        self.assertEqual(rig.layout(arm.data)["type"], "humanoid")

    def test_mesh_fits_the_rig(self):
        for params in ({}, {"height": 0.8, "head": 0.2, "legs": 0.3}, {"height": 3.5, "girth": 0.16, "arms": 0.42},
                       {"hair": False}):
            with self.subTest(params=params):
                arm, body = character(dict(params, clips=False))
                co = np.empty(len(body.data.vertices) * 3)
                body.data.vertices.foreach_get("co", co)
                co = co.reshape(-1, 3)
                h = params.get("height", 1.8)
                self.assertAlmostEqual(co[:, 2].min(), 0.0, delta=1e-4)        # feet on the origin
                self.assertAlmostEqual(co[:, 2].max(), h, delta=0.01 * h)      # height includes the hair
                self.assertLess(co[:, 1].min(), -0.05 * h)                      # toes and face at -Y
                self.assertLess(abs(co[:, 0].max() + co[:, 0].min()), 1e-4)    # left-right symmetric

    def test_weights_are_normalised_and_cover_every_vertex(self):
        arm, body = character({"clips": False})
        deform = {b.name for b in arm.data.bones if b.use_deform}
        names = [g.name for g in body.vertex_groups]
        self.assertEqual(set(names), deform)
        used = set()
        for v in body.data.vertices:
            self.assertTrue(v.groups, f"vertex {v.index} has no weights")
            self.assertAlmostEqual(sum(g.weight for g in v.groups), 1.0, places=5)
            used.update(names[g.group] for g in v.groups if g.weight > 0)
        self.assertEqual(used, deform)  # every deforming bone moves something
        blended = [v for v in body.data.vertices if len([g for g in v.groups if g.weight > 0.01]) > 1]
        self.assertTrue(blended, "the torso should blend hips, spine and chest")
        self.assertTrue({names[g.group] for v in blended for g in v.groups} <= {"hips", "spine", "chest"})

    def test_limbs_are_rigid_so_they_never_twist_or_stretch(self):
        arm, body = character()
        animation.preview(arm, "Wave")
        group = body.vertex_groups["lower_arm_R"].index
        verts = [v.index for v in body.data.vertices if any(g.group == group and g.weight > 0.999 for g in v.groups)]
        self.assertGreater(len(verts), 50)

        def shape(frame):
            bpy.context.scene.frame_set(frame)
            ev = evaluated(body)
            mesh = ev.to_mesh()
            pts = [mesh.vertices[i].co.copy() for i in verts]
            ev.to_mesh_clear()
            return pts
        rest, waved = shape(1), shape(5)
        moved = max((a - b).length for a, b in zip(rest, waved))
        self.assertGreater(moved, 0.01)  # the forearm moves...
        for i in range(0, len(verts), 7):  # ...without changing shape
            for j in range(3, len(verts), 11):
                self.assertAlmostEqual((rest[i] - rest[j]).length, (waved[i] - waved[j]).length, places=4)

    def test_regenerate_rebuilds_mesh_bones_and_clips(self):
        arm, body = character({"name": "Hero"}, location=(1, 2, 0))
        components.add_component(arm, "Spin", {"speed": 1})
        animation.add_clip(arm, "wave", {"arm": "left"}, name="WaveLeft")
        walk_keys = [list(fc.keyframe_points[3].co) for fc in fcurves(bpy.data.actions["Walk"])]
        same = kits.regenerate(body, {"height": 2.6})
        self.assertIs(same, body)
        self.assertEqual((arm.name, body.name, body.parent), ("Hero", "Hero_Mesh", arm))
        self.assertEqual(tuple(arm.location), (1.0, 2.0, 0.0))
        self.assertEqual(components.list_components(arm), ["Spin"])
        self.assertAlmostEqual(arm.data.bones["head"].tail_local.z, 2.6, places=4)
        self.assertAlmostEqual(max(v.co.z for v in body.data.vertices), 2.6, delta=0.01)
        self.assertEqual(kits.kit_params(arm)[1]["height"], 2.6)
        self.assertEqual(kits.kit_params(body)[1]["height"], 2.6)
        self.assertTrue(all(abs(sum(g.weight for g in v.groups) - 1) < 1e-5 for v in body.data.vertices))
        self.assertEqual([m.object for m in body.modifiers], [arm])
        names = [c["name"] for c in animation.clips(arm)]
        self.assertEqual(names, list(HUMANOID_CLIPS) + ["WaveLeft"])
        self.assertEqual(next(c for c in animation.clips(arm) if c["name"] == "WaveLeft")["params"]["arm"], "left")
        # The clips were re-baked for the longer legs, in the same actions.
        self.assertNotEqual([list(fc.keyframe_points[3].co) for fc in fcurves(bpy.data.actions["Walk"])], walk_keys)
        self.assertEqual(len(bpy.data.actions), len(HUMANOID_CLIPS) + 1)
        self.assertIs(kits.regenerate(arm, {"girth": 0.14}), arm)

    def test_regenerate_recreates_a_deleted_mesh(self):
        arm, body = character({"clips": False})
        bpy.data.objects.remove(body)
        kits.regenerate(arm, {"height": 2.0})
        _, body = base.rig_parts(arm)
        self.assertIsNotNone(body)
        self.assertEqual(body.parent, arm)
        self.assertEqual(len(body.vertex_groups), 15)

    def test_without_clips(self):
        arm, _ = character({"clips": False})
        self.assertEqual(animation.clips(arm), [])
        self.assertEqual(len(bpy.data.actions), 0)

    def test_bridge_create_from_kit_reports_the_armature_as_root(self):
        from gamestudio.bridge import assets

        out = assets.create_from_kit({"kind": "character_rigged", "params": {"name": "Hero", "clips": False},
                                      "location": [0, 0, 0]})
        self.assertEqual(out["root"], "Hero")
        self.assertEqual([c["type"] for c in out["created"]], ["ARMATURE", "MESH"])
        again = assets.regenerate_kit({"object": "Hero_Mesh", "params": {"height": 2.0}})
        self.assertEqual(again["params"]["height"], 2.0)


class HumanoidClipTest(AnimTestCase):
    def setUp(self):
        super().setUp()
        self.arm, self.body = character({"name": "Hero"})
        self.scene = bpy.context.scene

    def test_standard_clips(self):
        clips = {c["name"]: c for c in animation.clips(self.arm)}
        self.assertEqual(list(clips), list(HUMANOID_CLIPS))
        self.assertTrue(clips["Idle"]["active"])
        self.assertEqual(self.arm.animation_data.action.name, "Idle")
        for name, loop in HUMANOID_CLIPS.items():
            with self.subTest(clip=name):
                action = bpy.data.actions[name]
                clip = clips[name]
                self.assertEqual(clip["action"], name)
                self.assertEqual(clip["loop"], loop)
                self.assertEqual(action.use_cyclic, loop)
                start, end = action.frame_range
                self.assertEqual(start, 1.0)
                self.assertEqual(end - start, clip["frames"])
                self.assertAlmostEqual(clip["seconds"], clip["frames"] / 30, places=5)
                paths = {fc.data_path for fc in fcurves(action)}
                self.assertTrue(all(p.startswith('pose.bones["') for p in paths), paths)  # never the object
                self.assertNotIn('pose.bones["root"].rotation_quaternion', paths)       # no root motion
                keyed = {p.split('"')[1] for p in paths}
                self.assertEqual(keyed, set(rig.HUMANOID_BONES) - {"root"})              # every other bone
                for fc in fcurves(action):
                    self.assertEqual(len(fc.keyframe_points), clip["frames"] + 1)
                    self.assertEqual(fc.keyframe_points[0].interpolation, "LINEAR")
        self.assertEqual(clips["Walk"]["ground_speed"], 1.5)
        self.assertEqual(clips["Idle"]["frames"], 120)

    def test_looping_clips_end_where_they_start(self):
        for name, loop in HUMANOID_CLIPS.items():
            action = bpy.data.actions[name]
            by_bone = {}
            for fc in fcurves(action):
                by_bone.setdefault(fc.data_path, {})[fc.array_index] = fc
            worst = 0.0
            for path, chans in by_bone.items():
                if path.endswith("rotation_quaternion"):
                    q0 = Quaternion([chans[i].keyframe_points[0].co[1] for i in range(4)])
                    q1 = Quaternion([chans[i].keyframe_points[-1].co[1] for i in range(4)])
                    gap = 1 - abs(q0.normalized().dot(q1.normalized()))
                else:
                    gap = max(abs(chans[i].keyframe_points[0].co[1] - chans[i].keyframe_points[-1].co[1]) for i in chans)
                worst = max(worst, gap)
            if loop:
                self.assertLess(worst, 1e-6, f"{name} does not loop")
            elif name in ("JumpStart", "Land"):
                self.assertGreater(worst, 1e-3, f"{name} should end in a different pose")

    def foot_points(self, ev, side):
        """Heel, toe and ankle of a foot in armature space at the current frame."""
        r = animation.Rig(self.arm)
        pb = ev.pose.bones[f"foot_{side}"]
        rest = self.arm.data.bones[f"foot_{side}"].matrix_local
        ankle = r.ankle(side)
        points = [ankle, Vector((ankle.x, ankle.y + r.heel, 0.0)), Vector((ankle.x, ankle.y - r.toe, 0.0))]
        return [pb.matrix @ rest.inverted() @ p for p in points]

    def test_planted_feet_do_not_slide_or_sink(self):
        r = animation.Rig(self.arm)
        for name in ("Idle", "Wave"):
            action = animation.preview(self.arm, name)
            with self.subTest(clip=name):
                start = None
                for frame in frames_of(action):
                    self.scene.frame_set(frame)
                    ev = evaluated(self.arm)
                    points = [p for side in "LR" for p in self.foot_points(ev, side)]
                    start = start or points
                    drift = max((a - b).length for a, b in zip(points, start))
                    self.assertLess(drift, 1e-3, f"{name} frame {frame}: feet slid {drift:.4f} m")
                self.assertAlmostEqual(start[0].z, r.ankle_h, places=3)

    def test_feet_never_go_through_the_floor(self):
        for name in HUMANOID_CLIPS:
            action = animation.preview(self.arm, name)
            with self.subTest(clip=name):
                lowest = min(deformed_z(self.body).min() for frame in frames_of(action)
                             if self.scene.frame_set(frame) is None)
                self.assertGreater(lowest, -0.004, f"{name}: the mesh dips {lowest:.4f} m below the floor")
                if name in ("Idle", "Walk", "Wave", "Land", "JumpStart"):
                    self.assertLess(lowest, 0.004, f"{name}: the feet should touch the ground")

    def test_walk_stance_foot_matches_the_ground_speed(self):
        for name in ("Walk", "Run"):
            clip = next(c for c in animation.clips(self.arm) if c["name"] == name)
            action = animation.preview(self.arm, name)
            ys = []
            for frame in frames_of(action):
                self.scene.frame_set(frame)
                ys.append(self.foot_points(evaluated(self.arm), "L")[0].y)
            # Mid-stance of the left foot: the first quarter of a walk cycle, the first sixth of a run.
            span = clip["frames"] // (4 if name == "Walk" else 7)
            speed = (ys[span] - ys[1]) / ((span - 1) / 30)
            self.assertAlmostEqual(speed, clip["ground_speed"], delta=0.12 * clip["ground_speed"], msg=name)

    def test_in_place(self):
        for name in HUMANOID_CLIPS:
            action = animation.preview(self.arm, name)
            for frame in frames_of(action)[::3]:
                self.scene.frame_set(frame)
                hips = evaluated(self.arm).pose.bones["hips"].head
                self.assertLess(Vector((hips.x, hips.y)).length, 0.12, f"{name} frame {frame}")
            self.assertEqual(tuple(self.arm.location), (0.0, 0.0, 0.0))
            self.assertIsNone(next((fc for fc in fcurves(action) if not fc.data_path.startswith("pose.")), None))

    def test_skinning_deforms_the_mesh(self):
        animation.preview(self.arm, "Walk")
        self.scene.frame_set(1)
        a = deformed_z(self.body)
        self.scene.frame_set(8)
        b = deformed_z(self.body)
        self.assertGreater(np.abs(a - b).max(), 0.02)

    def test_params_rekey_the_same_clip(self):
        old = bpy.data.actions["Walk"]
        frames = next(c for c in animation.clips(self.arm) if c["name"] == "Walk")["frames"]
        action = animation.add_clip(self.arm, "walk", {"speed": 3.0, "stride": 1.2, "bounce": 0.5})
        self.assertIs(action, old)  # its only user: re-keyed in place, the name stays "Walk"
        clip = next(c for c in animation.clips(self.arm) if c["name"] == "Walk")
        self.assertEqual(clip["params"], {"speed": 3.0, "stride": 1.2, "bounce": 0.5})
        self.assertLess(clip["frames"], frames)
        self.assertEqual(clip["ground_speed"], 3.0)
        self.assertIs(self.arm.animation_data.action, action)  # the newest clip is active
        self.assertEqual(len(bpy.data.actions), len(HUMANOID_CLIPS))

    def test_custom_names_kind_spellings_and_removal(self):
        a = animation.add_clip(self.body, "JumpAir", {"speed": 2}, name="Float")  # the mesh finds its armature
        self.assertEqual(a.name, "Float")
        self.assertEqual(animation.clips(self.arm)[-1]["kind"], "jump_air")
        self.assertEqual(animation.add_clip(self.arm, "jump-air", name="Float2")["gs_clip"].count("jump_air"), 1)
        animation.remove_clip(self.arm, "Float")
        self.assertNotIn("Float", bpy.data.actions)
        self.assertNotIn("Float", [c["name"] for c in animation.clips(self.arm)])
        with self.assertRaises(kits.KitError):
            animation.remove_clip(self.arm, "Float")

    def test_bad_input(self):
        crate = kits.create("crate")[0]
        plain = bpy.data.objects.new("Plain", bpy.data.armatures.new("Plain"))
        bpy.context.scene.collection.objects.link(plain)
        for args, kwargs, fragment in (
            ((self.arm, "moonwalk"), {}, "Unknown clip kind"),
            ((self.arm, "walk", {"speed": 50}), {}, "between"),
            ((self.arm, "walk", {"pace": 1}), {}, "unknown param"),
            ((self.arm, "wave", {"arm": "middle"}), {}, "one of right, left"),
            ((crate, "walk"), {}, "humanoid clip"),
            ((plain, "idle"), {}, "not a GameStudio humanoid rig"),
            ((self.arm, ["walk", "spin"]), {}, "Only looping object clips"),
            ((self.arm, "walk"), {"name": ""}, "1 to 60"),
            ((None, "spin"), {}, "needs an object"),
        ):
            with self.subTest(fragment=fragment):
                with self.assertRaises(kits.KitError) as ctx:
                    animation.add_clip(*args, **kwargs)
                self.assertIn(fragment, str(ctx.exception))

    def test_identical_rigs_share_clips_and_different_ones_do_not(self):
        twin, _ = character({"name": "Twin"}, location=(3, 0, 0))
        self.assertIs(twin.animation_data.nla_tracks["Walk"].strips[0].action, bpy.data.actions["Walk"])
        self.assertEqual(len(bpy.data.actions), len(HUMANOID_CLIPS))
        kits.regenerate(twin, {"height": 1.2})
        own = twin.animation_data.nla_tracks["Walk"].strips[0].action
        self.assertEqual(own.name, "Walk.001")  # the runtime finds it as "Walk" on this entity
        self.assertIs(self.arm.animation_data.nla_tracks["Walk"].strips[0].action, bpy.data.actions["Walk"])
        self.assertEqual(len(bpy.data.actions), 2 * len(HUMANOID_CLIPS))
        bpy.data.objects.remove(twin)

    def test_30_fps_and_other_frame_rates(self):
        self.scene.render.fps = 24
        action = animation.add_clip(self.arm, "idle")
        self.assertEqual(action.frame_range[1] - action.frame_range[0], 96)  # 4 s at 24 fps
        self.scene.render.fps = 30


class ObjectClipTest(AnimTestCase):
    def keyed(self, action):
        return {(fc.data_path, fc.array_index): fc for fc in fcurves(action)}

    def test_every_object_kind_on_any_object(self):
        makers = (lambda: kits.create("crate", {"name": "Box"})[0],
                  lambda: helpers.new_object("Marker", None, location=(1, 2, 3)),
                  lambda: helpers.new_object("Lamp", bpy.data.lights.new("Lamp", "POINT"), location=(0, 0, 4)))
        for make in makers:
            for kind in OBJECT_KINDS:
                obj = make()
                obj.rotation_euler = (0.1, 0.2, 0.3)
                with self.subTest(obj=obj.name, kind=kind):
                    action = animation.add_clip(obj, kind)
                    self.assertEqual(action.name.split(".")[0], kind)
                    self.assertIs(obj.animation_data.action, action)
                    spec = animation._kind(kind)
                    channels = self.keyed(action)
                    self.assertTrue(channels)
                    first = {k: fc.keyframe_points[0].co[1] for k, fc in channels.items()}
                    last = {k: fc.keyframe_points[-1].co[1] for k, fc in channels.items()}
                    if spec.loop:
                        for key in channels:
                            if key[0] == "rotation_euler":
                                continue
                            self.assertAlmostEqual(first[key], last[key], places=5, msg=key)
                        if ("rotation_euler", 0) in channels:
                            e0 = Euler([first[("rotation_euler", i)] for i in range(3)])
                            e1 = Euler([last[("rotation_euler", i)] for i in range(3)])
                            self.same_rotation(e0.to_quaternion(), e1.to_quaternion())
                    if kind in ("spin", "bob", "pulse", "door_open"):  # these start at the placed transform
                        for (path, i), value in first.items():
                            if path == "rotation_euler":
                                self.assertAlmostEqual(value, (0.1, 0.2, 0.3)[i], places=4)
                            elif path == "location" and kind not in ("bob", "hover"):
                                self.assertAlmostEqual(value, obj.location[i], places=4)
                self.discard(obj)

    @staticmethod
    def discard(obj):
        bpy.data.objects.remove(obj)
        for action in list(bpy.data.actions):
            if action.users == 0:
                bpy.data.actions.remove(action)

    def test_bob_and_spin_keep_the_placement(self):
        coin = kits.create("collectible", {"name": "Coin"}, location=(3, -2, 1))[0]
        bob = animation.add_clip(coin, "bob", {"height": 0.25, "speed": 0.5})
        ch = self.keyed(bob)
        self.assertEqual(set(ch), {("location", 0), ("location", 1), ("location", 2)})
        zs = [k.co[1] for k in ch[("location", 2)].keyframe_points]
        self.assertAlmostEqual(min(zs), 0.75, places=3)
        self.assertAlmostEqual(max(zs), 1.25, places=3)
        self.assertTrue(all(abs(k.co[1] - 3) < 1e-6 for k in ch[("location", 0)].keyframe_points))
        self.assertEqual(len(zs), 61)  # 2 s at 30 fps, both ends keyed
        spin = animation.add_clip(coin, "spin", {"speed": 1.0})
        rz = [k.co[1] for k in self.keyed(spin)[("rotation_euler", 2)].keyframe_points]
        self.assertAlmostEqual(rz[0], 0.0, places=5)
        self.assertAlmostEqual(rz[-1], 2 * math.pi, places=4)  # continuous, a whole turn
        self.assertEqual([c["name"] for c in animation.clips(coin)], ["bob", "spin"])

    def test_quaternion_mode_and_axis_angle(self):
        obj = helpers.new_object("Q", None)
        obj.rotation_mode = "QUATERNION"
        action = animation.add_clip(obj, "sway")
        self.assertIn(("rotation_quaternion", 3), self.keyed(action))
        other = helpers.new_object("AA", None)
        other.rotation_mode = "AXIS_ANGLE"
        with self.assertRaises(kits.KitError):
            animation.add_clip(other, "spin")
        animation.add_clip(other, "bob")  # no rotation: fine

    def test_layered_clip(self):
        coin = kits.create("collectible", {"name": "Coin"}, location=(0, 0, 1))[0]
        action = animation.add_clip(coin, ["spin", "bob"], {"spin": {"speed": 0.5}, "bob": {"speed": 0.75}})
        self.assertEqual(action.name, "spin_bob")
        paths = {p for p, _ in self.keyed(action)}
        self.assertEqual(paths, {"location", "rotation_euler"})
        self.assertEqual(action.frame_range[1] - action.frame_range[0], 60)  # the longest layer: 2 s
        zs = [k.co[1] for k in self.keyed(action)[("location", 2)].keyframe_points]
        self.assertAlmostEqual(zs[0], zs[-1], places=5)
        self.assertEqual(json.loads(action["gs_clip"])["kind"], ["spin", "bob"])
        with self.assertRaises(kits.KitError):
            animation.add_clip(coin, ["spin", "door_open"])

    def test_identical_clips_share_one_action(self):
        coins = [kits.create("collectible", {"name": "Coin"}, location=(i, 0, 1))[0] for i in range(3)]
        actions = {animation.add_clip(c, "spin") for c in coins}
        self.assertEqual(len(actions), 1)
        shared = actions.pop()
        self.assertEqual(shared.name, "spin")
        self.assertEqual(len(shared.slots), 1)
        # Re-keying one coin does not touch the others.
        faster = animation.add_clip(coins[0], "spin", {"speed": 2.0})
        self.assertNotEqual(faster, shared)
        self.assertEqual(faster.name, "spin.001")
        self.assertIs(coins[1].animation_data.nla_tracks["spin"].strips[0].action, shared)
        self.assertEqual(json.loads(shared["gs_clip"])["params"]["speed"], 0.5)
        # Bob keys the location, so coins at different places get their own.
        bobs = {animation.add_clip(c, "bob") for c in coins}
        self.assertEqual(len(bobs), 3)

    def test_door(self):
        door = helpers.new_object("Door", helpers.new_mesh(), location=(1, 1, 0))
        door.rotation_euler = (0, 0, math.radians(90))
        action = animation.add_clip(door, "door_open", {"angle": 90, "seconds": 0.5})
        self.assertFalse(action.use_cyclic)
        self.assertEqual(action.frame_range[1] - action.frame_range[0], 15)
        rz = [k.co[1] for k in self.keyed(action)[("rotation_euler", 2)].keyframe_points]
        self.assertAlmostEqual(rz[0], math.radians(90), places=5)
        self.assertAlmostEqual(rz[-1], math.radians(180), places=4)
        close = animation.add_clip(door, "door_close", {"angle": 90, "seconds": 0.5})
        rz = [k.co[1] for k in self.keyed(close)[("rotation_euler", 2)].keyframe_points]
        self.assertAlmostEqual(rz[0], math.radians(180), places=4)
        self.assertAlmostEqual(rz[-1], math.radians(90), places=5)

    def test_hand_made_clip_of_the_same_name_is_kept(self):
        obj = helpers.new_object("Thing", None)
        obj.animation_data_create()
        mine = bpy.data.actions.new("spin")
        track = obj.animation_data.nla_tracks.new()
        track.name = "spin"
        track.strips.new("spin", 1, mine)
        with self.assertRaises(kits.KitError):
            animation.add_clip(obj, "spin")
        self.assertEqual(animation.add_clip(obj, "spin", name="spin_fast").name, "spin_fast")

    def test_catalog(self):
        entries = {e["kind"]: e for e in animation.catalog()}
        self.assertEqual(set(entries), set(animation.HUMANOID_SET) | set(OBJECT_KINDS))
        for kind, entry in entries.items():
            json.dumps(entry)
            self.assertIn(entry["target"], ("humanoid", "object"))
            self.assertTrue(entry["description"])
            for name, schema in entry["params"]["properties"].items():
                self.assertIn("default", schema, f"{kind}.{name}")
                self.assertTrue(schema.get("description"), f"{kind}.{name}")


class ExportTest(AnimTestCase):
    registry_data = helpers.modified_registry(lambda data: data["components"].append({
        "name": "Animator", "category": "Animation", "builtin": True,
        "fields": [{"name": "clip", "type": "string", "default": ""},
                   {"name": "loop", "type": "bool", "default": True},
                   {"name": "speed", "type": "float", "default": 1},
                   {"name": "playOnStart", "type": "bool", "default": True},
                   {"name": "fade", "type": "float", "default": 0.25, "min": 0}]}))

    def test_nla_layout_for_the_exporter(self):
        arm, _ = character()
        coin = kits.create("collectible", {"name": "Coin"}, location=(2, 0, 1))[0]
        animation.add_clip(coin, "spin")
        animation.add_clip(coin, "bob")
        for obj in (arm, coin):
            ad = obj.animation_data
            for track in ad.nla_tracks:
                with self.subTest(obj=obj.name, track=track.name):
                    self.assertEqual(len(track.strips), 1)
                    strip = track.strips[0]
                    self.assertEqual(strip.action.name, track.name)   # clip name = action name
                    self.assertFalse(strip.mute)                       # the exporter skips muted strips
                    self.assertTrue(track.mute)                        # but not muted tracks
                    self.assertEqual(len(strip.action.slots), 1)
                    self.assertEqual(strip.action_slot, strip.action.slots[0])
                    self.assertEqual((strip.frame_start, strip.frame_end), tuple(strip.action.frame_range))

    def test_scene_export_writes_every_clip(self):
        bpy.context.scene.name = "main"
        arm, body = character({"name": "Hero"})
        components.add_component(arm, "Animator", {"clip": "Walk"})
        coin = kits.create("collectible", {"name": "Coin"}, location=(2, 0, 1))[0]
        animation.add_clip(coin, "spin")
        animation.add_clip(coin, "bob")
        expected = {c["name"]: c["seconds"] for c in animation.clips(arm) + animation.clips(coin)}
        result = export.export_scene()
        with open(result["manifest"], "r", encoding="utf-8") as f:
            manifest = json.load(f)
        clips = {c["name"]: c for c in manifest["animations"]}
        self.assertEqual(set(clips), set(expected))
        for name, seconds in expected.items():
            self.assertAlmostEqual(clips[name]["duration"], seconds, places=3, msg=name)
        self.assertEqual(clips["Walk"]["entities"], [ids.get_id(arm)])
        self.assertEqual(clips["spin"]["entities"], [ids.get_id(coin)])

        gltf, _ = export.read_glb(result["glb"])
        nodes = gltf["nodes"]
        self.assertEqual(len(gltf["skins"]), 1)
        joints = {nodes[j]["name"] for j in gltf["skins"][0]["joints"]}
        self.assertTrue(set(rig.HUMANOID_BONES) - {"root"} <= joints, joints)
        hero = next(i for i, n in enumerate(nodes) if n["name"] == "Hero")
        mesh_node = next(i for i, n in enumerate(nodes) if n["name"] == "Hero_Mesh")
        self.assertIn(mesh_node, nodes[hero].get("children", []))  # the skinned mesh is under the armature
        walk = next(a for a in gltf["animations"] if a["name"] == "Walk")
        targets = {nodes[c["target"]["node"]]["name"] for c in walk["channels"]}
        self.assertTrue({"hips", "foot_L", "lower_arm_R"} <= targets)
        self.assertNotIn("Hero", targets)  # in place: the armature node itself is never animated
        spin = next(a for a in gltf["animations"] if a["name"] == "spin")
        self.assertEqual({(nodes[c["target"]["node"]]["name"], c["target"]["path"]) for c in spin["channels"]},
                         {("Coin", "rotation")})
