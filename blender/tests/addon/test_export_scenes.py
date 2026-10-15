# SPDX-License-Identifier: GPL-3.0-or-later
"""Levels (§4.7): level files export under their file name, export_all() exports every level, the sky
kit's gradient and the world mist reach the manifest, and the effect components export (§6.3)."""

import json
import os
import pathlib

import bpy

from gamestudio.core import components, export, project

import helpers


def _with_effects(data):
    with open(helpers.FIXTURES / "effects_components.json", "r", encoding="utf-8") as f:
        data["components"] += json.load(f)


def read(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


class LevelExportTest(helpers.ProjectTestCase):
    registry_data = helpers.modified_registry(_with_effects)

    def save_level(self, name):
        path = self.root / "scenes" / f"{name}.blend"
        path.parent.mkdir(exist_ok=True)
        bpy.ops.wm.save_as_mainfile(filepath=str(path))
        project.invalidate_cache()
        return path

    def test_a_level_file_exports_under_its_name(self):
        helpers.new_object("Ground", helpers.new_mesh())
        bpy.context.scene.name = "Scene"  # a new file's default name
        self.save_level("level2")
        self.assertEqual(export.export_name(bpy.context.scene), "level2")
        result = export.export_scene()
        self.assertEqual(result["scene"], "level2")
        self.assertEqual(pathlib.Path(result["manifest"]).name, "level2.scene.json")
        self.assertEqual(read(result["manifest"])["scene"], "level2")
        self.assertEqual(export.scene_load_message(result)["url"], "/__gs/export/level2.scene.json")
        # Several scenes in one file: each exports under its own name, as before.
        bpy.data.scenes.new("Arena")
        self.assertEqual(export.export_name(bpy.context.scene), "Scene")
        self.assertEqual(export.export_name(bpy.data.scenes["Arena"]), "Arena")

    def test_revisions_never_repeat_after_undo_or_restore(self):
        helpers.new_object("Ground", helpers.new_mesh())
        bpy.context.scene.name = "Scene"
        path = self.save_level("main")  # the file remembers gs_rev 0
        self.assertEqual(export.export_scene(bake="off")["rev"], 1)
        self.assertEqual(export.export_scene(bake="off", force=True)["rev"], 2)  # unforced: reused, rev 1
        bpy.context.scene[export.REV_PROP] = 1  # what an undo of the last export does
        self.assertEqual(export.export_scene(bake="off")["rev"], 3)
        self.assertEqual(bpy.context.scene[export.REV_PROP], 3)
        bpy.ops.wm.open_mainfile(filepath=str(path))  # a checkpoint restore: the saved file is older
        project.invalidate_cache()
        self.assertEqual(bpy.context.scene.get(export.REV_PROP, 0), 0)
        result = export.export_scene(bake="off")
        self.assertEqual(result["rev"], 4)
        self.assertEqual(read(result["manifest"])["rev"], 4)

    def test_files_outside_scenes_keep_the_scene_name(self):
        bpy.context.scene.name = "My Level: 1"
        bpy.ops.wm.save_as_mainfile(filepath=str(self.root / "level.blend"))
        project.invalidate_cache()
        self.assertEqual(export.export_name(bpy.context.scene), "My_Level_1")
        self.assertEqual(export.level_files(self.root), [])

    def test_export_all_exports_every_level(self):
        # level2.blend: written by this session, then left (export_all exports it in a background Blender).
        helpers.new_object("Arena", helpers.new_mesh())
        bpy.context.scene.name = "Scene"
        self.save_level("level2")
        helpers.reset_scene()
        bpy.context.scene.name = "main"
        helpers.new_object("Ground", helpers.new_mesh())
        main = self.save_level("main")
        self.assertEqual(export.level_files(self.root), [self.root / "scenes" / "level2.blend", main])
        out = export.export_all(bake="off")
        self.assertEqual(out["errors"], [])
        by_scene = {s["scene"]: s for s in out["scenes"]}
        self.assertEqual(set(by_scene), {"main", "level2"})
        self.assertEqual((by_scene["main"]["file"], by_scene["level2"]["file"]), ("scenes/main.blend", "scenes/level2.blend"))
        level2 = read(self.root / ".gamestudio" / "export" / "level2.scene.json")
        self.assertEqual([e["name"] for e in level2["entities"]], ["Arena"])
        self.assertEqual(bpy.data.filepath, str(main))  # the open file stays open
        # The other file's revision is not saved into it; the next export still counts up. The open file's
        # scene did not change: its export is reused (journal.py).
        again = export.export_all(bake="off")
        revs = {s["scene"]: s["rev"] for s in again["scenes"]}
        self.assertEqual(revs, {"main": by_scene["main"]["rev"], "level2": by_scene["level2"]["rev"] + 1})

    def test_export_all_reports_a_broken_level(self):
        bpy.context.scene.name = "main"
        self.save_level("main")
        (self.root / "scenes" / "broken.blend").write_bytes(b"not a blend file")
        out = export.export_all(bake="off")
        self.assertEqual([s["scene"] for s in out["scenes"]], ["main"])
        self.assertEqual([e["file"] for e in out["errors"]], ["scenes/broken.blend"])

    def test_sky_and_fog(self):
        bpy.context.scene.name = "main"
        helpers.new_object("Ground", helpers.new_mesh())
        world = bpy.data.worlds.new("GS_Sky")
        world.color = (0.52, 0.77, 0.89)
        world["gs_sky_colors"] = {"horizon": "#BFE3F2", "zenith": "#3C8DDB", "ground": "#8A9A88", "sun": "#FFF4E0"}
        bpy.context.scene.world = world
        settings = read(export.export_scene(bake="off")["manifest"])["settings"]
        self.assertEqual(set(settings), {"gravity", "background", "activeCamera", "sky", "toneMapping",
                                         "exposure"})  # mist is off
        sky = settings["sky"]
        self.assertAlmostEqual(sky["horizon"][0], (0xBF / 255 + 0.055) ** 2.4 / 1.055 ** 2.4, places=5)
        self.assertLess(sky["zenith"][0], sky["horizon"][0])
        self.assertEqual(len(sky["ground"]), 3)

        world.mist_settings.use_mist = True
        world.mist_settings.start = 12
        world.mist_settings.depth = 50
        fog = read(export.export_scene(bake="off")["manifest"])["settings"]["fog"]
        self.assertEqual(fog, {"color": sky["horizon"], "near": 12.0, "far": 62.0})
        world["gs_fog_color"] = "#FFFFFF"
        self.assertEqual(read(export.export_scene(bake="off")["manifest"])["settings"]["fog"]["color"], [1.0, 1.0, 1.0])

        # Without the sky kit: no sky; the Mist pass turns fog on too, in the background colour.
        plain = bpy.data.worlds.new("Plain")
        plain.color = (0.2, 0.3, 0.4)
        bpy.context.scene.world = plain
        bpy.context.view_layer.use_pass_mist = True
        settings = read(export.export_scene(bake="off")["manifest"])["settings"]
        self.assertNotIn("sky", settings)
        self.assertEqual(settings["fog"]["color"], settings["background"])

    def test_effect_components_export(self):
        bpy.context.scene.name = "main"
        (self.root / "assets").mkdir()
        (self.root / "assets" / "spark.png").write_bytes(b"\x89PNG fake")
        fountain = helpers.new_object("Fountain", helpers.new_mesh())
        components.add_component(fountain, "ParticleEmitter", {
            "rate": 40, "lifetime": [0.5, 1.5], "shape": "cone", "colorStart": [1, 0.5, 0, 1],
            "blending": "alpha", "image": "assets/spark.png"})
        camera = helpers.new_object("Camera", bpy.data.cameras.new("Camera"))
        components.add_component(camera, "PostEffects", {"bloom": True, "vignette": True, "vignetteAmount": 0.5})
        manifest = read(export.export_scene(bake="off")["manifest"])
        entities = {e["name"]: e for e in manifest["entities"]}
        emitter = entities["Fountain"]["components"]["ParticleEmitter"]
        self.assertEqual((emitter["rate"], emitter["lifetime"], emitter["shape"], emitter["blending"]),
                         (40.0, [0.5, 1.5], "cone", "alpha"))
        self.assertEqual(emitter["colorStart"], [1.0, 0.5, 0.0, 1.0])
        self.assertEqual((emitter["playOnStart"], emitter["maxParticles"], emitter["texture"]), (True, 200, "dot"))
        post = entities["Camera"]["components"]["PostEffects"]
        self.assertEqual((post["bloom"], post["vignette"], post["vignetteAmount"], post["exposure"]), (True, True, 0.5, 1.0))
        self.assertTrue(manifest["assets"]["assets/spark.png"].startswith("files/assets/spark.png?v="))
        self.assertTrue((self.root / ".gamestudio" / "export" / "files" / "assets" / "spark.png").is_file())
        self.assertEqual(os.path.basename(manifest["glb"]), "main.glb")
