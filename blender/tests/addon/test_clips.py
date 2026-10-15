# SPDX-License-Identifier: GPL-3.0-or-later
"""Armature clips sampled from their F-curves (core/clips.py): the GLB animations and the manifest
are the glTF exporter's own (a stock reference is exported with the hook disarmed), clips are cached
and shared, and rigs the sampler cannot handle stay with the exporter."""

import contextlib
import json
import math
import os
import pathlib
from unittest import mock

import bpy
import numpy as np
from bpy_extras import anim_utils

from gamestudio import kits
from gamestudio.core import clips, export, fidelity, ids

import helpers

TOLERANCE = 1e-4

# name, parent, head, tail, connected, roll, rotation mode
BONES = (
    ("Root", None, (0, 0, 0), (0, 0.2, 1), False, 0.3, "QUATERNION"),
    ("Spine", "Root", (0, 0.2, 1), (0.1, 0.1, 1.8), True, -0.5, "ZXY"),
    ("Arm", "Spine", (0.3, 0, 1.6), (0.9, 0.1, 1.5), False, 1.1, "AXIS_ANGLE"),
    ("Hand", "Arm", (0.9, 0.1, 1.5), (1.2, 0.2, 1.4), True, 0.0, "QUATERNION"),
    ("Tail", "Root", (0, -0.3, 0.2), (0, -0.9, 0.1), False, 0.7, "XYZ"),
    ("Fin", "Root", (0.2, 0, 0.5), (0.5, 0, 0.6), False, -1.2, "QUATERNION"),
    ("Leaf", "Fin", (0.5, 0, 0.6), (0.7, 0.1, 0.6), True, 0.2, "YXZ"),
)


def make_rig(name, location=(0, 0, 0), bones=BONES):
    arm = bpy.data.armatures.new(name)
    obj = helpers.new_object(name, arm, location=location)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode="EDIT")
    made = {}
    for bone, parent, head, tail, connected, roll, _mode in bones:
        eb = arm.edit_bones.new(bone)
        eb.head, eb.tail, eb.roll = head, tail, roll
        if parent:
            eb.parent = made[parent]
            eb.use_connect = connected
        made[bone] = eb
    bpy.ops.object.mode_set(mode="OBJECT")
    for bone, *_rest, mode in bones:
        obj.pose.bones[bone].rotation_mode = mode
    obj.animation_data_create()
    return obj


def new_action(name, obj):
    action = bpy.data.actions.new(name)
    slot = action.slots.new(id_type="OBJECT", name=obj.name)
    return action, slot, anim_utils.action_ensure_channelbag_for_slot(action, slot)


def key(bag, bone, prop, index, points, interpolation="BEZIER"):
    fc = bag.fcurves.new(f'pose.bones["{bone}"].{prop}', index=index, group_name=bone)
    for frame, value in points:
        fc.keyframe_points.insert(frame, value).interpolation = interpolation
    fc.update()
    return fc


def wave(frames, amplitude=0.5, phase=0.0, offset=0.0):
    return [(f, offset + amplitude * math.sin(0.4 * f + phase)) for f in frames]


def add_clip(obj, action, slot, track=None, active=False):
    anim = obj.animation_data
    if track is not None:
        nla = anim.nla_tracks.new()
        nla.name = track
        strip = nla.strips.new(action.name, int(action.frame_range[0]), action)
        strip.action_slot = slot
        nla.mute = True  # as the kits do: the exporter writes it, the viewport does not blend it in
    if active:
        anim.action = action
        anim.action_slot = slot


def synthetic_clips(rig, prefix=""):
    """Clips on rig: every rotation mode, connected bones with location keys, scale keys, constant
    (stepped) keys on every frame, keys between frames, a muted curve, a modifier, frames below zero,
    a single-frame clip and named and default NLA tracks."""
    sway, slot, bag = new_action(prefix + "Sway", rig)
    for i in range(3):
        key(bag, "Root", "location", i, wave((1, 7.5, 14, 20), 0.2, i))
    q = [(1, (1, 0, 0, 0)), (10, (0.9, 0.3, 0.1, -0.2)), (20, (0.8, -0.2, 0.4, 0.1))]
    for i in range(4):
        key(bag, "Root", "rotation_quaternion", i, [(f, v[i]) for f, v in q])
    for i in range(3):
        key(bag, "Spine", "rotation_euler", i, wave(range(1, 21, 3), 0.6, i))
        key(bag, "Spine", "location", i, wave((1, 20), 0.3, i))  # connected: Blender ignores it
        key(bag, "Hand", "scale", i, wave((1, 8, 20), 0.3, i, 1.0))
        key(bag, "Tail", "rotation_euler", i, wave(range(1, 21), 0.8, i), "CONSTANT")
    for i, base in enumerate((0.4, 0.2, 1.0, 0.3)):
        key(bag, "Arm", "rotation_axis_angle", i, wave((1, 11, 20), 0.3, i, base), "LINEAR")
    key(bag, "Fin", "location", 0, wave((1, 20), 1.0)).mute = True
    noisy = key(bag, "Fin", "rotation_quaternion", 2, wave((1, 20), 0.2))
    noisy.modifiers.new("NOISE").strength = 0.3
    add_clip(rig, sway, slot, active=True)

    hop, slot, bag = new_action(prefix + "Hop", rig)
    for i in range(4):
        key(bag, "Fin", "rotation_quaternion", i, [(5, (1, 0, 0, 0)[i]), (15, (0.7, 0.7, 0, 0)[i])], "LINEAR")
    key(bag, "Root", "location", 2, wave(range(5, 16), 0.5))
    add_clip(rig, hop, slot, track="Hop")

    back, slot, bag = new_action(prefix + "Back", rig)
    for i in range(3):
        key(bag, "Leaf", "rotation_euler", i, wave((-3, 0, 6), 0.4, i))
    add_clip(rig, back, slot, track="NlaTrack")

    blink, slot, bag = new_action(prefix + "Blink", rig)
    key(bag, "Tail", "scale", 1, [(4, 1.5)])
    add_clip(rig, blink, slot, track="Blink")
    return [sway, hop, back, blink]


# --- Reading the result -----------------------------------------------------------------------------


def channel_table(path) -> tuple:
    """(animation names, {animation: [owner ids of its channels]}, {(animation, owner gs_id, node,
    path): (interpolation, input accessor, times, output accessor, values)})."""
    gltf, chunks = export.read_glb(path)
    glb = fidelity.Glb(gltf, chunks)
    nodes = gltf.get("nodes", [])
    parent = {c: i for i, n in enumerate(nodes) for c in n.get("children", [])}

    def owner(index):
        while index is not None:
            extras = nodes[index].get("extras") or {}
            if extras.get("gs_id"):
                return extras["gs_id"]
            index = parent.get(index)
        return None

    names, owners, table = [], {}, {}
    for anim in gltf.get("animations", []):
        names.append(anim["name"])
        order = owners.setdefault(anim["name"], [])
        for channel in anim["channels"]:
            sampler = anim["samplers"][channel["sampler"]]
            node = channel["target"]["node"]
            who = owner(node)
            if who not in order:
                order.append(who)
            table[(anim["name"], who, nodes[node]["name"], channel["target"]["path"])] = (
                sampler["interpolation"], sampler["input"], glb.read_accessor(sampler["input"]),
                sampler["output"], glb.read_accessor(sampler["output"]))
    return names, owners, table


def manifest_clips(path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        manifest = json.load(f)
    return {"animations": manifest["animations"],
            "entities": {e["id"]: e.get("animations") for e in manifest["entities"]},
            "prefabs": [(t["name"], t["animations"], {e["id"]: e.get("animations") for e in t["entities"]})
                        for t in manifest["prefabs"]]}


@contextlib.contextmanager
def disarmed():
    """The exporter samples every clip itself, as without GameStudio's hook."""
    with mock.patch.object(clips, "gather_actions_hook", lambda *_args: None):
        yield


class ClipTestCase(helpers.ProjectTestCase):
    def setUp(self):
        super().setUp()
        bpy.context.scene.name = "main"
        bpy.context.scene.render.fps = 30
        bpy.context.scene.render.fps_base = 1.0
        self.exports = 0

    def export(self, label="fast", forget=True):
        """Export to <root>/<label>/export (its own clip cache). forget: start without the clips the
        session keeps in memory (tests build identical rigs, whose clips have the same keys)."""
        self.exports += 1
        if forget:
            clips.forget()
        out = self.root / label / "export"
        return export.export_scene_to(out, bake="off")

    def stock_and_fast(self):
        with disarmed():
            stock = self.export("stock")
        fast = self.export("fast")
        return stock, fast

    def assertSameClips(self, stock, fast, files=("glb",)):
        """The same animations, channels, keys and manifest clips as the stock exporter's."""
        self.assertEqual(manifest_clips(stock["manifest"]), manifest_clips(fast["manifest"]))
        paths = [(stock["glb"], fast["glb"])]
        if "prefabs" in files:
            paths.append(tuple(str(pathlib.Path(r["glb"]).with_suffix(".prefabs.glb")) for r in (stock, fast)))
        for stock_path, fast_path in paths:
            stock_names, stock_owners, stock_table = channel_table(stock_path)
            fast_names, fast_owners, fast_table = channel_table(fast_path)
            self.assertEqual(stock_names, fast_names)
            self.assertEqual(stock_owners, fast_owners)
            self.assertEqual(sorted(stock_table), sorted(fast_table))
            self.assertTrue(stock_table)
            for name, (interp, _i, times, _o, values) in stock_table.items():
                f_interp, _fi, f_times, _fo, f_values = fast_table[name]
                self.assertEqual(times.shape, f_times.shape, name)
                self.assertLess(float(np.abs(times - f_times).max()), 1e-6, name)
                self.assertEqual(values.shape, f_values.shape, name)
                diff = np.abs(values - f_values).reshape(len(values), -1).max(axis=1)
                if name[3] == "rotation":  # q and -q are the same rotation
                    diff = np.minimum(diff, np.abs(values + f_values).max(axis=1))
                self.assertLess(float(diff.max()), TOLERANCE, name)
                # Two keys of nearly equal values: the exporter's choice of STEP depends on float noise.
                if not (len(times) == 2 and float(np.abs(values[0] - values[1]).max()) < 1e-5):
                    self.assertEqual(interp, f_interp, name)
        return stock_table


class SampledClipsTest(ClipTestCase):
    def test_kit_character_matches_the_exporter(self):
        hero, _body = kits.create("character_rigged", {"name": "Hero"})
        kits.create("character_rigged", {"name": "Tall", "height": 2.2}, location=(3, 0, 0))
        bpy.context.view_layer.update()
        stock, fast = self.stock_and_fast()
        self.assertSameClips(stock, fast)
        pairs = len(export.object_clips(hero)) * 2
        self.assertGreaterEqual(pairs, 18)
        self.assertEqual(fast["clips"]["fast"], pairs)
        self.assertEqual(fast["clips"]["stock"], [])
        self.assertEqual(stock["clips"]["fast"], 0)
        self.assertIn("did not call", stock["clips"]["stock"][0]["reason"])

    def test_synthetic_rigs_match_the_exporter(self):
        rig = make_rig("Rig")
        synthetic_clips(rig)
        for i in range(2):  # copies share the armature, its pose modes and the actions
            copy = rig.copy()
            copy.location.x += 2 * (i + 1)
            bpy.context.scene.collection.objects.link(copy)
        other = make_rig("Other", location=(0, 4, 0), bones=BONES[:5])
        hop2, slot, bag = new_action("Hop2", other)  # a track named "Hop" too: the exporter slides both to frame 3
        key(bag, "Arm", "rotation_axis_angle", 0, wave(range(3, 12), 0.5, 0, 0.4))
        for i, value in enumerate((0.0, 1.0, 0.0)):
            key(bag, "Arm", "rotation_axis_angle", i + 1, [(3, value), (11, value)])
        add_clip(other, hop2, slot, track="Hop", active=True)
        bpy.context.view_layer.update()
        stock, fast = self.stock_and_fast()
        table = self.assertSameClips(stock, fast)
        self.assertEqual(fast["clips"]["fast"], 3 * 4 + 1)
        self.assertEqual(fast["clips"]["sampled"], 5)  # four clips of the shared skeleton, one of Other
        hop = [v for k, v in table.items() if k[0] == "Hop" and k[2] == "Root" and k[3] == "translation"]
        self.assertAlmostEqual(float(hop[0][2][0]), 2 / 30, places=6)  # slid to frame 3, not 5
        tail = [v for k, v in table.items() if k[0] == "Sway" and k[2] == "Tail" and k[3] == "rotation"]
        self.assertEqual(tail[0][0], "STEP")
        blink = [v for k, v in table.items() if k[0] == "Blink" and k[2] == "Tail" and k[3] == "scale"]
        self.assertEqual(len(blink[0][2]), 1)

    def test_rigs_sharing_a_clip_share_its_accessors(self):
        rig = make_rig("Rig")
        synthetic_clips(rig)
        copy = rig.copy()
        bpy.context.scene.collection.objects.link(copy)
        bpy.context.view_layer.update()
        result = self.export()
        _names, _owners, table = channel_table(result["glb"])
        rig_id, copy_id = ids.get_id(rig), ids.get_id(copy)
        mine = {(k[0], k[2], k[3]): v for k, v in table.items() if k[1] == rig_id}
        theirs = {(k[0], k[2], k[3]): v for k, v in table.items() if k[1] == copy_id}
        self.assertEqual(set(mine), set(theirs))
        for name, (_interp, input_, _t, output, _v) in mine.items():
            self.assertEqual((input_, output), theirs[name][1:4:2], name)
        # Every clip with the same frames shares one time accessor.
        sway = {v[1] for k, v in mine.items() if k[0] == "Sway" and len(v[2]) > 2}
        self.assertEqual(len(sway), 1)


class FallbackTest(ClipTestCase):
    def test_constraints_and_drivers_stay_with_the_exporter(self):
        rig = make_rig("Rig")
        synthetic_clips(rig)
        constrained = rig.copy()  # shares every action with Rig: their animations merge
        constrained.name = "Constrained"
        bpy.context.scene.collection.objects.link(constrained)
        limit = constrained.pose.bones["Leaf"].constraints.new("LIMIT_ROTATION")
        limit.use_limit_x, limit.max_x = True, 0.1
        driven = rig.copy()
        driven.name = "Driven"
        bpy.context.scene.collection.objects.link(driven)
        driver = driven.pose.bones["Leaf"].driver_add("location", 0).driver
        driver.expression = "frame / 100"
        quiet = rig.copy()  # a disabled constraint does not count
        quiet.name = "Quiet"
        bpy.context.scene.collection.objects.link(quiet)
        quiet.pose.bones["Fin"].constraints.new("COPY_LOCATION").enabled = False
        bpy.context.view_layer.update()
        stock, fast = self.stock_and_fast()
        self.assertSameClips(stock, fast)
        reasons = {s["object"]: s["reason"] for s in fast["clips"]["stock"]}
        self.assertEqual(set(reasons), {"Constrained", "Driven"})
        self.assertIn("constraint", reasons["Constrained"])
        self.assertIn("drivers", reasons["Driven"])
        self.assertEqual(fast["clips"]["fast"], 8)

    def test_other_channels_and_kept_instances_stay_with_the_exporter(self):
        rig = make_rig("Rig")
        synthetic_clips(rig)
        mover = make_rig("Mover", location=(5, 0, 0))
        action, slot, bag = new_action("Move", mover)
        key(bag, "Root", "location", 0, wave((1, 10)))
        fc = bag.fcurves.new("location", index=1)  # the armature object itself moves
        fc.keyframe_points.insert(1, 0.0)
        fc.keyframe_points.insert(10, 2.0)
        add_clip(mover, action, slot, active=True)
        crowd = bpy.data.collections.new("Crowd")  # not a prefab: the GLB keeps instance contents
        extra = make_rig("Extra", location=(0, -4, 0))
        synthetic_clips(extra, prefix="Extra")
        bpy.context.scene.collection.objects.unlink(extra)
        crowd.objects.link(extra)
        bpy.context.scene.collection.children.link(crowd)
        instance = helpers.new_object("CrowdInstance", None, location=(8, 8, 0))
        instance.instance_type = "COLLECTION"
        instance.instance_collection = crowd
        bpy.context.view_layer.update()
        stock, fast = self.stock_and_fast()
        self.assertSameClips(stock, fast)
        reasons = {s["object"]: s["reason"] for s in fast["clips"]["stock"]}
        self.assertIn("animates location", reasons["Mover"])
        self.assertIn("CrowdInstance", reasons["Extra"])
        self.assertEqual(fast["clips"]["fast"], 4)


class CacheTest(ClipTestCase):
    def test_the_second_export_samples_nothing(self):
        rig = make_rig("Rig")
        synthetic_clips(rig)
        first = self.export()
        self.assertEqual((first["clips"]["sampled"], first["clips"]["cached"]), (4, 0))
        files = sorted((self.root / "fast" / "cache" / "clips").glob("*.clip"))
        self.assertEqual(len(files), 4)
        never = AssertionError("not from memory")
        with mock.patch.object(clips, "sample", side_effect=AssertionError("sampled again")):
            with mock.patch.object(clips.ClipData, "from_bytes", side_effect=never):
                second = self.export(forget=False)  # the session's memory
            third = self.export()  # the disk cache
        for result in (second, third):
            self.assertEqual((result["clips"]["sampled"], result["clips"]["cached"], result["clips"]["fast"]),
                             (0, 4, 4))
        first_table, third_table = channel_table(first["glb"])[2], channel_table(third["glb"])[2]
        self.assertEqual(first_table.keys(), third_table.keys())
        for name, (_interp, _i, _times, _o, values) in first_table.items():
            self.assertTrue(np.array_equal(values, third_table[name][4]), name)

    def test_edits_invalidate_the_cache(self):
        rig = make_rig("Rig")
        sway, _hop, _back, _blink = synthetic_clips(rig)
        self.export()
        bag = anim_utils.action_get_channelbag_for_slot(sway, sway.slots[0])
        bag.fcurves.find('pose.bones["Tail"].rotation_euler', index=1).keyframe_points[3].co.y += 0.5
        edited = self.export()
        self.assertEqual((edited["clips"]["sampled"], edited["clips"]["cached"]), (1, 3))
        bpy.context.view_layer.objects.active = rig
        bpy.ops.object.mode_set(mode="EDIT")
        rig.data.edit_bones["Hand"].tail.z += 0.2  # the rest pose: every clip of the skeleton changes
        bpy.ops.object.mode_set(mode="OBJECT")
        rested = self.export()
        self.assertEqual((rested["clips"]["sampled"], rested["clips"]["cached"]), (4, 0))
        with disarmed():
            stock = self.export("stock")
        self.assertSameClips(stock, rested)

    def test_old_entries_are_pruned(self):
        folder = self.root / "cache"
        folder.mkdir()
        old, new = folder / ("a" * 40 + clips.SUFFIX), folder / ("b" * 40 + clips.SUFFIX)
        old.write_bytes(b"x")
        new.write_bytes(b"x")
        stamp = 1_000_000_000
        os.utime(old, (stamp, stamp))
        self.assertEqual(clips.prune(folder), 1)
        self.assertEqual(sorted(p.name for p in folder.iterdir()), [new.name])


class DigestTest(ClipTestCase):
    def test_action_digest_follows_edits_not_selection(self):
        rig = make_rig("Rig")
        sway, hop, _back, _blink = synthetic_clips(rig)
        slot = sway.slots[0]
        before = clips.action_digest(sway, slot)
        self.assertIs(clips._raw, True)  # this Blender's keyframe array has the expected layout
        self.assertEqual(before, clips.action_digest(sway, slot))
        with mock.patch.object(clips, "_raw", False):  # foreach_get instead of the keyframe array
            self.assertEqual(before, clips.action_digest(sway, slot))
        bag = anim_utils.action_get_channelbag_for_slot(sway, slot)
        root = bag.fcurves.find('pose.bones["Root"].rotation_quaternion', index=1)
        for point in root.keyframe_points:
            point.select_control_point = not point.select_control_point
        self.assertEqual(before, clips.action_digest(sway, slot))
        root.keyframe_points[1].handle_left.y += 0.1  # a Bezier handle: the curve between keys changes
        handled = clips.action_digest(sway, slot)
        self.assertNotEqual(before, handled)
        root.keyframe_points[1].interpolation = "LINEAR"
        self.assertNotEqual(handled, clips.action_digest(sway, slot))
        changed = clips.action_digest(sway, slot)
        sway.use_frame_range = True
        sway.frame_start, sway.frame_end = 1, 30
        self.assertNotEqual(changed, clips.action_digest(sway, slot))
        digests = clips.clip_digests([rig])
        self.assertEqual(set(digests), {(a.name_full, a.slots[0].identifier) for a in (sway, hop, _back, _blink)})
        self.assertEqual(digests[(hop.name_full, hop.slots[0].identifier)], clips.action_digest(hop, hop.slots[0]))


class PrefabClipsTest(ClipTestCase):
    def test_prefab_clips_match_the_exporter(self):
        guard = bpy.data.collections.new("Guard")
        export.set_prefab(guard)
        rig = make_rig("GuardRig")
        bpy.context.scene.collection.objects.unlink(rig)
        guard.objects.link(rig)
        synthetic_clips(rig, prefix="Guard")
        door = helpers.new_object("Door", helpers.new_mesh(), location=(3, 0, 0))  # an object clip: the exporter's
        door.animation_data_create()
        door.animation_data.action = bpy.data.actions.new("Open")
        door.rotation_mode = "XYZ"
        door.keyframe_insert("rotation_euler", index=2, frame=1)
        door.rotation_euler.z = 1.5
        door.keyframe_insert("rotation_euler", index=2, frame=12)
        for i in range(3):
            instance = helpers.new_object(f"Guard{i}", None, location=(i * 2, 6, 0))
            instance.instance_type = "COLLECTION"
            instance.instance_collection = guard
        bpy.context.view_layer.update()
        with mock.patch.object(clips, "sample", wraps=clips.sample) as sampled:
            stock, fast = self.stock_and_fast()
        self.assertSameClips(stock, fast, files=("glb", "prefabs"))
        self.assertEqual(fast["clips"]["fast"], 4)  # the template's; the instances' copies are dropped
        self.assertEqual(sampled.call_count, 8)  # four clips, in each export
        manifest = manifest_clips(fast["manifest"])
        self.assertEqual(sorted(c["name"] for c in manifest["prefabs"][0][1]),
                         ["GuardBack", "GuardBlink", "GuardHop", "GuardSway"])
        self.assertEqual([c["name"] for c in manifest["animations"]], ["Open"])

    def test_excluded_collections_are_not_sampled(self):
        stash = bpy.data.collections.new("Stash")
        bpy.context.scene.collection.children.link(stash)
        rig = make_rig("Stashed")
        bpy.context.scene.collection.objects.unlink(rig)
        stash.objects.link(rig)
        synthetic_clips(rig)
        bpy.context.view_layer.layer_collection.children["Stash"].exclude = True
        live = make_rig("Live", location=(3, 0, 0))
        synthetic_clips(live, prefix="Live")
        bpy.context.view_layer.update()
        stock, fast = self.stock_and_fast()
        self.assertSameClips(stock, fast)
        self.assertEqual(fast["clips"]["fast"], 4)


class BackgroundExportTest(ClipTestCase):
    def test_export_file_samples_clips(self):
        rig = make_rig("Rig")
        synthetic_clips(rig)
        copy = rig.copy()
        bpy.context.scene.collection.objects.link(copy)
        bpy.context.scene.name = "Scene"
        path = self.root / "scenes" / "level2.blend"
        path.parent.mkdir()
        bpy.ops.wm.save_as_mainfile(filepath=str(path))
        with disarmed():
            stock = self.export("stock")  # assigns the ids the saved file keeps
        bpy.ops.wm.save_as_mainfile(filepath=str(path))
        summary = export.export_file(path, bake="off")
        self.assertEqual(summary["scene"], "level2")
        glb = pathlib.Path(summary["manifest"]).with_name("level2.glb")
        fast = {"glb": str(glb), "manifest": summary["manifest"]}
        self.assertSameClips(stock, fast)
        # Only the sampler shares accessors between rigs: proof the hook ran in the background Blender.
        gltf, _chunks = export.read_glb(glb)
        outputs = [s["output"] for a in gltf["animations"] for s in a["samplers"]]
        self.assertLess(len(set(outputs)), len(outputs))
        self.assertTrue(any((self.root / ".gamestudio" / "cache" / "clips").glob("*.clip")))
