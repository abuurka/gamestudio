# SPDX-License-Identifier: GPL-3.0-or-later
"""Scene export of animation clips, prefab templates and asset files (§4.2, §4.4, §4.5)."""

import json
import math
import os
import pathlib
import shutil
import tempfile

import bpy

from gamestudio.core import components, export, ids

import helpers

ANIMATOR = {
    "name": "Animator", "category": "Animation", "builtin": True,
    "fields": [
        {"name": "clip", "type": "string", "default": ""},
        {"name": "loop", "type": "bool", "default": True},
        {"name": "speed", "type": "float", "default": 1},
        {"name": "playOnStart", "type": "bool", "default": True},
        {"name": "fade", "type": "float", "default": 0.25, "min": 0, "unit": "time"},
    ],
}
AUDIO_SOURCE = {
    "name": "AudioSource", "category": "Audio", "builtin": True,
    "fields": [
        {"name": "clip", "type": "asset", "default": "", "extensions": ["ogg", "mp3", "wav"]},
        {"name": "volume", "type": "float", "default": 1, "min": 0},
        {"name": "loop", "type": "bool", "default": False},
        {"name": "autoplay", "type": "bool", "default": False},
        {"name": "spatial", "type": "bool", "default": True},
        {"name": "refDistance", "type": "float", "default": 2, "min": 0, "unit": "distance"},
        {"name": "maxDistance", "type": "float", "default": 50, "min": 0, "unit": "distance"},
        {"name": "rolloff", "type": "float", "default": 1, "min": 0},
        {"name": "distanceModel", "type": "enum", "default": "inverse", "options": ["inverse", "linear", "exponential"]},
    ],
}


def _with_builtins(data):
    data["components"] += [ANIMATOR, AUDIO_SOURCE]


def pyramid(name):
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata([(-.5, -.5, 0), (.5, -.5, 0), (.5, .5, 0), (-.5, .5, 0), (0, 0, 1)], [],
                     [(0, 1, 2, 3), (0, 1, 4), (1, 2, 4), (2, 3, 4), (3, 0, 4)])
    return mesh


def keyframes(obj, path, index, frames, target=None):
    target = target or obj
    for frame, value in frames:
        getattr(target, path)[index] = value
        target.keyframe_insert(path, index=index, frame=frame)


def read_gltf(path):
    gltf, _chunks = export.read_glb(path)
    return gltf


def node_names(gltf):
    return {n["name"]: n for n in gltf["nodes"]}


class ExportFeaturesTest(helpers.ProjectTestCase):
    registry_data = helpers.modified_registry(_with_builtins)

    def setUp(self):
        super().setUp()
        bpy.context.scene.name = "main"
        self.export_dir = self.root / ".gamestudio" / "export"

    def manifest(self, result):
        with open(result["manifest"], "r", encoding="utf-8") as f:
            return json.load(f)

    # --- Scene content -----------------------------------------------------------

    def build_animated(self):
        scene = bpy.context.scene
        box = helpers.new_object("Box", pyramid("BoxMesh"))
        box.animation_data_create()
        spin = bpy.data.actions.new("SpinAround")
        box.animation_data.action = spin
        keyframes(box, "rotation_euler", 2, ((1, 0), (25, math.pi)))
        track = box.animation_data.nla_tracks.new()
        track.strips.new("SpinAround", 1, spin)
        box.animation_data.action = bpy.data.actions.new("Bob")
        keyframes(box, "location", 2, ((1, 0), (13, 1), (25, 0)))
        components.add_component(box, "Animator", {"clip": "Bob"})

        rig = helpers.new_object("Rig", bpy.data.armatures.new("RigData"), location=(3, 0, 0))
        bpy.context.view_layer.objects.active = rig
        bpy.ops.object.mode_set(mode="EDIT")
        root = rig.data.edit_bones.new("Root")
        root.head, root.tail = (0, 0, 0), (0, 0, 1)
        tip = rig.data.edit_bones.new("Tip")
        tip.head, tip.tail = (0, 0, 1), (0, 0, 2)
        tip.parent = root
        bpy.ops.object.mode_set(mode="OBJECT")
        skin = helpers.new_object("Skin", pyramid("SkinMesh"), parent=rig)
        skin.modifiers.new("Armature", "ARMATURE").object = rig
        skin.vertex_groups.new(name="Root").add([0, 1, 2, 3], 1.0, "REPLACE")
        skin.vertex_groups.new(name="Tip").add([4], 1.0, "REPLACE")
        rig.animation_data_create()
        rig.animation_data.action = bpy.data.actions.new("Wave")
        bone = rig.pose.bones["Tip"]
        bone.rotation_mode = "XYZ"
        keyframes(rig, "rotation_euler", 0, ((1, 0), (13, 0.8), (25, 0)), target=bone)
        components.add_component(rig, "Animator", {"clip": "Wave", "speed": 1.5})
        scene.frame_set(7)
        bpy.context.view_layer.update()
        return box, rig, skin

    def build_prefab(self, offset=(0, 0, 0.5), link_to_scene=False):
        coin = bpy.data.collections.new("Coin")
        export.set_prefab(coin)
        coin.instance_offset = offset
        body = helpers.new_object("CoinBody", pyramid("CoinMesh"), location=(0, 0, 1), collection=coin)
        glint = helpers.new_object("CoinGlint", None, parent=body, location=(0, 0, 0.3), collection=coin)
        components.add_component(body, "Spin", {"speed": 3})
        components.add_component(body, "FollowCamera", {"target": glint})
        components.add_component(body, "Animator", {"clip": "CoinSpin"})
        body.animation_data_create()
        body.animation_data.action = bpy.data.actions.new("CoinSpin")
        keyframes(body, "rotation_euler", 2, ((1, 0), (25, math.pi * 2)))
        body.rotation_euler = (0, 0, 0)
        if link_to_scene:
            bpy.context.scene.collection.children.link(coin)
        return coin, body, glint

    def instance(self, name, collection, location=(0, 0, 0)):
        obj = helpers.new_object(name, None, location=location)
        obj.instance_type = "COLLECTION"
        obj.instance_collection = collection
        return obj

    # --- Animation ----------------------------------------------------------------

    def test_actions_nla_strips_and_skins_become_listed_clips(self):
        box, rig, _skin = self.build_animated()
        result = export.export_scene()
        manifest = self.manifest(result)
        self.assertEqual(manifest["formatVersion"], 2)
        clips = {c["name"]: c for c in manifest["animations"]}
        self.assertEqual(set(clips), {"Bob", "SpinAround", "Wave"})
        self.assertEqual(clips["Bob"]["entities"], [ids.get_id(box)])
        self.assertEqual(clips["SpinAround"]["entities"], [ids.get_id(box)])  # from the NLA strip
        self.assertEqual(clips["Wave"]["entities"], [ids.get_id(rig)])  # bone channels count for the armature
        for clip in clips.values():
            self.assertAlmostEqual(clip["duration"], 1.0, places=4)  # frames 1-25 at 24 fps, slid to 0 s
        self.assertEqual(result["animations"], 3)

        gltf = read_gltf(result["glb"])
        nodes = gltf["nodes"]
        self.assertEqual(len(gltf["skins"]), 1)
        self.assertEqual(sorted(nodes[j]["name"] for j in gltf["skins"][0]["joints"]), ["Root", "Tip"])
        wave = next(a for a in gltf["animations"] if a["name"] == "Wave")
        self.assertIn("Tip", {nodes[c["target"]["node"]]["name"] for c in wave["channels"]})
        for anim in gltf["animations"]:
            starts = [gltf["accessors"][s["input"]]["min"][0] for s in anim["samplers"]]
            self.assertAlmostEqual(min(starts), 0.0, places=5)

        entities = {e["name"]: e for e in manifest["entities"]}
        self.assertEqual(entities["Box"]["animations"], ["Bob", "SpinAround"])
        self.assertEqual(entities["Rig"]["animations"], ["Wave"])
        self.assertNotIn("animations", entities["Skin"])
        self.assertEqual(entities["Rig"]["components"]["Animator"],
                         {"clip": "Wave", "loop": True, "speed": 1.5, "playOnStart": True, "fade": 0.25})
        self.assertEqual(bpy.context.scene.frame_current, 7)  # the exporter's frame changes are undone

    def test_kit_convention_one_action_per_nla_track(self):
        """Clips stored as NLA tracks (no active action), as the character and prop kits do."""
        scene = bpy.context.scene
        scene.render.fps = 30

        def armature(name, x):
            rig = helpers.new_object(name, bpy.data.armatures.new(name + "Data"), location=(x, 0, 0))
            bpy.context.view_layer.objects.active = rig
            bpy.ops.object.mode_set(mode="EDIT")
            bone = rig.data.edit_bones.new("Spine")
            bone.head, bone.tail = (0, 0, 0), (0, 0, 1)
            bpy.ops.object.mode_set(mode="OBJECT")
            rig.animation_data_create()
            return rig

        def bone_clip(rig, name, frames):
            action = bpy.data.actions.new(name)
            rig.animation_data.action = action
            bone = rig.pose.bones["Spine"]
            bone.rotation_mode = "XYZ"
            keyframes(rig, "rotation_euler", 0, ((1, 0), (frames, 0.5)), target=bone)
            rig.animation_data.action = None
            return action

        def push(owner, action, track_name=None):
            track = owner.animation_data.nla_tracks.new()
            track.name = track_name or action.name
            track.strips.new(action.name, 1, action)
            return track

        hero = armature("Hero", 0)
        for name, frames in (("Idle", 61), ("Walk", 31)):
            push(hero, bone_clip(hero, name, frames))
        push(hero, bone_clip(hero, "Jump", 16), "[Action Stash]").mute = True  # Blender's Stash button
        combo = push(hero, bone_clip(hero, "ComboA", 11), "Combo")
        combo.strips.new("ComboB", 20, bone_clip(hero, "ComboB", 11))
        villain = armature("Villain", 3)
        push(villain, bone_clip(villain, "Idle", 41))  # Blender names it Idle.001
        spin = bpy.data.actions.new("spin")
        fans = []
        for i, name in enumerate(("Fan", "Fan2")):
            fan = helpers.new_object(name, pyramid(name + "Mesh"), location=(-3 - 2 * i, 0, 0))
            fan.animation_data_create()
            fan.animation_data.action = spin
            if i == 0:
                keyframes(fan, "rotation_euler", 2, ((1, 0), (31, math.tau)))
            fan.animation_data.action = None
            push(fan, spin)
            fans.append(fan)
        bpy.context.view_layer.update()

        with self.assertLogs("gamestudio.export", level="WARNING") as logs:
            result = export.export_scene()
        self.assertTrue(any("NLA track 'Combo' on 'Hero' has 2 strips" in line for line in logs.output), logs.output)
        manifest = self.manifest(result)
        clips = {c["name"]: c for c in manifest["animations"]}
        self.assertEqual(set(clips), {"Idle", "Walk", "Jump", "Idle.001", "spin"})
        self.assertAlmostEqual(clips["Idle"]["duration"], 2.0, places=4)  # 60 frames at 30 fps
        self.assertAlmostEqual(clips["Walk"]["duration"], 1.0, places=4)
        self.assertEqual(clips["Idle.001"]["entities"], [ids.get_id(villain)])  # no clips leak between rigs
        self.assertEqual(sorted(clips["spin"]["entities"]), sorted(ids.get_id(f) for f in fans))  # one shared clip
        entities = {e["name"]: e for e in manifest["entities"]}
        self.assertEqual(sorted(entities["Hero"]["animations"]), ["Idle", "Jump", "Walk"])
        self.assertEqual(entities["Villain"]["animations"], ["Idle.001"])
        self.assertEqual(entities["Fan2"]["animations"], ["spin"])

    # --- Prefabs ---------------------------------------------------------------------

    def test_prefab_collection_exports_as_template_and_instances_reference_it(self):
        coin, body, glint = self.build_prefab()
        placed = self.instance("CoinInstance", coin, location=(-3, 0, 0))
        decor = bpy.data.collections.new("Decor")
        helpers.new_object("Plant", pyramid("PlantMesh"), collection=decor)
        plain = self.instance("DecorInstance", decor, location=(4, 0, 0))
        users_before = coin.users
        scenes_before = list(bpy.data.scenes)
        objects_before = set(bpy.data.objects)

        result = export.export_scene()
        self.assertEqual(sorted(p.name for p in self.export_dir.iterdir()),
                         ["main.glb", "main.prefabs.glb", "main.scene.json"])
        self.assertEqual(result["prefabs"], ["Coin"])
        manifest = self.manifest(result)

        (template,) = manifest["prefabs"]
        self.assertEqual(template["name"], "Coin")
        self.assertEqual(template["glb"], "main.prefabs.glb")
        self.assertEqual(template["offset"], [0.0, 0.5, 0.0])  # Y-up
        by_name = {e["name"]: e for e in template["entities"]}
        self.assertEqual(set(by_name), {"CoinBody", "CoinGlint"})
        self.assertEqual(by_name["CoinBody"]["id"], ids.get_id(body))
        self.assertEqual(by_name["CoinBody"]["parent"], None)
        self.assertEqual(by_name["CoinGlint"]["parent"], ids.get_id(body))
        self.assertEqual(by_name["CoinBody"]["components"]["Spin"], {"speed": 3.0, "axis": [0.0, 1.0, 0.0]})
        self.assertEqual(by_name["CoinBody"]["components"]["FollowCamera"]["target"], ids.get_id(glint))
        self.assertEqual(template["animations"], [{"name": "CoinSpin", "duration": 1.0, "entities": [ids.get_id(body)]}])
        self.assertEqual(by_name["CoinBody"]["animations"], ["CoinSpin"])

        entities = {e["name"]: e for e in manifest["entities"]}
        self.assertEqual(set(entities), {"CoinInstance", "DecorInstance"})  # template objects are not scene entities
        self.assertEqual(entities["CoinInstance"]["prefab"], "Coin")
        self.assertNotIn("prefab", entities["DecorInstance"])

        scene_gltf = read_gltf(result["glb"])
        nodes = node_names(scene_gltf)
        self.assertNotIn("children", nodes["CoinInstance"])  # the runtime builds the instance
        self.assertEqual([scene_gltf["nodes"][c]["name"] for c in nodes["DecorInstance"]["children"]], ["Plant"])
        self.assertEqual(manifest["animations"], [])  # CoinSpin only targeted the stripped instance
        self.assertNotIn("animations", scene_gltf)

        prefab_gltf = read_gltf(result["glb"].replace("main.glb", "main.prefabs.glb"))
        prefab_nodes = node_names(prefab_gltf)
        self.assertEqual(prefab_nodes["CoinBody"]["extras"], {"gs_id": ids.get_id(body)})
        self.assertEqual(prefab_nodes["CoinGlint"]["extras"], {"gs_id": ids.get_id(glint)})
        self.assertEqual(prefab_nodes["CoinBody"]["translation"], [0, 1, 0])  # collection space
        self.assertEqual([a["name"] for a in prefab_gltf["animations"]], ["CoinSpin"])

        # The user's file is unchanged: no extra scenes, objects or collection users.
        self.assertEqual(list(bpy.data.scenes), scenes_before)
        self.assertEqual(set(bpy.data.objects), objects_before)
        self.assertEqual(coin.users, users_before)
        self.assertEqual(bpy.context.window.scene, bpy.context.scene)
        self.assertEqual(placed.instance_collection, coin)
        self.assertEqual(plain.instance_collection, decor)

        second = self.manifest(export.export_scene())
        self.assertEqual(second["prefabs"][0]["entities"], template["entities"])  # ids are stable

        export.set_prefab(coin, False)
        third = self.manifest(export.export_scene())
        self.assertEqual(third["prefabs"], [])
        self.assertFalse((self.export_dir / "main.prefabs.glb").exists())
        self.assertNotIn("prefab", {e["name"]: e for e in third["entities"]}["CoinInstance"])

    def test_visible_source_nested_prefabs_and_duplicate_ids(self):
        coin, body, _glint = self.build_prefab(offset=(0, 0, 0), link_to_scene=True)
        tower = bpy.data.collections.new("Tower")
        export.set_prefab(tower)
        base = helpers.new_object("TowerBase", pyramid("TowerMesh"), collection=tower)
        spot = self.instance("TowerCoin", coin, location=(0, 0, 3))
        bpy.context.scene.collection.objects.unlink(spot)
        tower.objects.link(spot)
        copy = base.copy()  # a duplicate inside the prefab shares the gs_id property
        ids.ensure_id(base)
        copy[ids.ID_PROP] = ids.get_id(base)
        copy.name = "TowerBase2"
        tower.objects.link(copy)

        result = export.export_scene()
        manifest = self.manifest(result)
        scene_entities = {e["name"] for e in manifest["entities"]}
        self.assertEqual(scene_entities, {"CoinBody", "CoinGlint"})  # the visible source is in the level too
        templates = {t["name"]: t for t in manifest["prefabs"]}
        self.assertEqual(set(templates), {"Coin", "Tower"})
        tower_entities = {e["name"]: e for e in templates["Tower"]["entities"]}
        self.assertEqual(tower_entities["TowerCoin"]["prefab"], "Coin")
        self.assertNotEqual(tower_entities["TowerBase"]["id"], tower_entities["TowerBase2"]["id"])
        self.assertGreaterEqual(result["repaired"], 1)

        prefab_gltf = read_gltf(str(self.export_dir / "main.prefabs.glb"))
        self.assertNotIn("children", node_names(prefab_gltf)["TowerCoin"])  # nested contents are rebuilt at runtime

    def test_preserves_selection_mode_and_frame_with_prefabs(self):
        self.build_prefab()
        crate = helpers.new_object("Crate", pyramid("CrateMesh"))
        view_layer = bpy.context.view_layer
        crate.select_set(True)
        view_layer.objects.active = crate
        with bpy.context.temp_override(active_object=crate, object=crate):
            bpy.ops.object.mode_set(mode="EDIT")
        bpy.context.scene.frame_set(12)
        export.export_scene()
        self.assertEqual(crate.mode, "EDIT")
        self.assertEqual(view_layer.objects.active, crate)
        self.assertEqual([o.name for o in view_layer.objects if o.select_get()], ["Crate"])
        self.assertEqual(bpy.context.scene.frame_current, 12)
        with bpy.context.temp_override(active_object=crate, object=crate):
            bpy.ops.object.mode_set(mode="OBJECT")

    # --- Asset files --------------------------------------------------------------

    def test_a_font_asset_brings_its_licence(self):
        """The OFL wants its text with every copy of a font; the dev server lists it in a build's notices."""
        fonts = self.root / "assets" / "fonts"
        fonts.mkdir(parents=True)
        (fonts / "Pixel.ttf").write_bytes(b"\x00\x01\x00\x00 fake font")
        (fonts / "OFL.txt").write_text("SIL Open Font License 1.1", encoding="utf-8")
        (self.root / "assets" / "other").mkdir()
        (self.root / "assets" / "other" / "Plain.otf").write_bytes(b"OTTO fake font without a licence")
        mapping = export.copy_assets(self.export_dir, ["assets/fonts/Pixel.ttf", "assets/other/Plain.otf"],
                                     root=self.root)
        self.assertEqual(len(mapping), 2)
        copied = self.export_dir / "files" / "assets"
        self.assertEqual((copied / "fonts" / "Pixel-OFL.txt").read_text("utf-8"), "SIL Open Font License 1.1")
        self.assertEqual([p.name for p in (copied / "other").iterdir()], ["Plain.otf"],
                         "no licence to bring: the build warns about it")

    def test_asset_files_are_copied_next_to_the_export(self):
        sfx = self.root / "assets" / "sfx"
        sfx.mkdir(parents=True)
        (sfx / "beep.wav").write_bytes(b"RIFF....WAVEfmt fake")
        outside = pathlib.Path(tempfile.mkdtemp(prefix="gs_assets_", dir=helpers.tmp_base()))
        self.addCleanup(shutil.rmtree, outside, True)
        (outside / "music.ogg").write_bytes(b"OggS fake")

        speaker = helpers.new_object("Speaker", None)
        components.add_component(speaker, "AudioSource", {"clip": "assets/sfx/beep.wav", "autoplay": True})
        radio = helpers.new_object("Radio", None)
        components.add_component(radio, "AudioSource", {"clip": str(outside / "music.ogg")})
        broken = helpers.new_object("Broken", None)
        components.add_component(broken, "AudioSource", {"clip": "assets/missing.ogg"})
        coin, body, _glint = self.build_prefab()
        components.add_component(body, "AudioSource", {"clip": "assets/sfx/beep.wav"})

        result = export.export_scene()
        assets = self.manifest(result)["assets"]
        self.assertEqual(result["assets"], 2)
        self.assertEqual(sorted(assets), sorted(["assets/sfx/beep.wav", components.get_values(radio, "AudioSource", True)["clip"]]))
        beep_url = assets["assets/sfx/beep.wav"]
        self.assertRegex(beep_url, r"^files/assets/sfx/beep\.wav\?v=[0-9a-f]{10}$")
        copied = self.export_dir / "files" / "assets" / "sfx" / "beep.wav"
        self.assertEqual(copied.read_bytes(), b"RIFF....WAVEfmt fake")
        external = [v for k, v in assets.items() if k != "assets/sfx/beep.wav"][0]
        self.assertRegex(external, r"^files/external/[0-9a-f]{8}-music\.ogg\?v=")
        self.assertTrue((self.export_dir / external.split("?")[0]).is_file())

        # Unchanged files are not copied again; changed ones are, with a new version.
        stamp = copied.stat().st_mtime_ns
        self.assertEqual(self.manifest(export.export_scene())["assets"]["assets/sfx/beep.wav"], beep_url)
        self.assertEqual(copied.stat().st_mtime_ns, stamp)
        (sfx / "beep.wav").write_bytes(b"RIFF....WAVEfmt changed!")
        os.utime(sfx / "beep.wav", ns=(stamp + 5_000_000_000, stamp + 5_000_000_000))
        newer = self.manifest(export.export_scene())["assets"]["assets/sfx/beep.wav"]
        self.assertNotEqual(newer, beep_url)
        self.assertEqual(copied.read_bytes(), b"RIFF....WAVEfmt changed!")
