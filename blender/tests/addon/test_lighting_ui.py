# SPDX-License-Identifier: GPL-3.0-or-later
"""Bake Lighting and Clear Baked Lighting (ops_lighting.py), Scene Properties > Game Project > Lighting
and the Game menu's Bake Lighting.

Panels and menus draw on test_ui's MockLayout. The bakes are tiny "draft" bakes: the operator bakes in
this Blender (it does in --background), start_bake in a background Blender as the GameStudio job.
"""

import datetime
import json
import math
import unittest

import bpy

from gamestudio import environment, menus, ops_lighting, ui_scene
from gamestudio.core import bake, export, lightmap, project

import helpers
from test_ui import MockLayout, _FakeSelf
from test_ui_scene import draw, fake_context

JOB = environment.JOB
PANEL = ui_scene.GAMESTUDIO_PT_scene_lighting


def level():
    """A ground, a box on it and a sun."""
    bpy.ops.mesh.primitive_plane_add(size=6)
    bpy.context.active_object.name = "Ground"
    bpy.ops.mesh.primitive_cube_add(size=1, location=(0, 0, 0.5))
    bpy.context.active_object.name = "Box"
    sun = bpy.data.objects.new("Sun", bpy.data.lights.new("Sun", "SUN"))
    bpy.context.scene.collection.objects.link(sun)
    sun.rotation_euler = (math.radians(40), 0, math.radians(30))


class _LightingCase(helpers.ProjectTestCase):
    """Keeps the GameStudio job (which these tests fake or run) and the status cache out of other tests."""

    def setUp(self):
        super().setUp()
        self._job = vars(JOB).copy()
        ops_lighting.invalidate_status()

    def tearDown(self):
        vars(JOB).update(self._job)
        ops_lighting.invalidate_status()
        super().tearDown()

    def write_meta(self, quality="final", objects=2, size=512) -> None:
        folder = lightmap.lightmap_dir(self.root, export.export_name(bpy.context.scene))
        folder.mkdir(parents=True)
        meta = {"version": lightmap.VERSION, "quality": quality, "seconds": 3.0,
                "created": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
                "objects": {f"O{i}": {"name": f"O{i}", "atlas": 0} for i in range(objects)},
                "atlases": [{"file": "x.png", "size": size, "scale": 1.0}], "texture_bytes": size * size * 4}
        (folder / lightmap.META).write_text(json.dumps(meta), encoding="utf-8")


class LightingUiTest(_LightingCase):
    def test_operators_are_registered(self):
        self.assertEqual(ops_lighting.GAMESTUDIO_OT_bake_lighting.bl_label, "Bake Lighting")
        self.assertEqual(ops_lighting.GAMESTUDIO_OT_clear_lighting.bl_label, "Clear Baked Lighting")
        for name in ("bake_lighting", "clear_lighting"):
            self.assertTrue(hasattr(bpy.ops.gamestudio, name), name)
        rna = bpy.ops.gamestudio.bake_lighting.get_rna_type()
        self.assertEqual(list(rna.properties["quality"].enum_items.keys()), ["preview", "final"])
        self.assertEqual(rna.properties["quality"].default, "preview")

    def test_poll_needs_a_project_and_no_running_job(self):
        self.assertTrue(bpy.ops.gamestudio.bake_lighting.poll())
        self.assertTrue(bpy.ops.gamestudio.clear_lighting.poll())
        JOB.state, JOB.title = "running", "Installing Node.js"
        self.assertFalse(bpy.ops.gamestudio.bake_lighting.poll())
        self.assertTrue(bpy.ops.gamestudio.clear_lighting.poll(), "only a bake blocks Clear")
        JOB.title = ops_lighting.BAKE_TITLE
        self.assertFalse(bpy.ops.gamestudio.clear_lighting.poll())
        JOB.state = "done"
        project.set_project_root_override(None)
        self.assertFalse(bpy.ops.gamestudio.bake_lighting.poll())
        self.assertFalse(bpy.ops.gamestudio.clear_lighting.poll())
        self.assertFalse(PANEL.poll(fake_context()))

    def test_panel_without_a_bake(self):
        self.assertTrue(PANEL.poll(fake_context()))
        text = draw(self, PANEL, fake_context())
        for expected in ("Bake Preview", "Bake Final", "Not baked"):
            self.assertIn(expected, text)

    def test_panel_shows_the_stored_bake_and_the_job(self):
        self.write_meta(quality="final", objects=2, size=512)
        text = draw(self, PANEL, fake_context())
        self.assertIn("2 objects, 1 atlas, 1.0 MB", text)
        self.assertRegex(text, r"Final, baked \ds ago")
        self.assertNotIn("Not baked", text)

        JOB.state, JOB.title, JOB.root = "running", ops_lighting.BAKE_TITLE, self.root
        JOB.message, JOB.progress = "Baking lighting (final)... 3 s", None
        self.assertIn("Baking lighting (final)... 3 s", draw(self, PANEL, fake_context()))
        JOB.state, JOB.message = "failed", "Bake Lighting failed: Nothing to bake"
        self.assertIn("Bake Lighting failed: Nothing to bake", draw(self, PANEL, fake_context()))
        JOB.title = "Installing Node.js"  # other jobs show elsewhere
        self.assertNotIn("Nothing to bake", draw(self, PANEL, fake_context()))

    def test_game_menu_has_bake_lighting(self):
        texts = []
        menus.GAMESTUDIO_MT_game.draw(_FakeSelf(menus.GAMESTUDIO_MT_game, MockLayout(self, texts)), fake_context())
        self.assertIn("Bake Lighting", texts)
        self.assertEqual(texts.index("Bake Lighting"), texts.index("Export Scene") + 1)

    def test_texts(self):
        self.assertEqual(ops_lighting.status_lines(None), ["Not baked"])
        self.assertEqual(ops_lighting.status_lines({"quality": "preview", "objects": 1, "atlases": 2,
                                                    "texture_bytes": 0}), ["1 object, 2 atlases, 0 B", "Preview"])
        result = {"objects": [{"name": "A"}, {"name": "B"}], "atlases": [{"size": 512}],
                  "texture_bytes": 2 * 1024 * 1024, "seconds": 12.4, "warnings": []}
        self.assertEqual(ops_lighting.summary_text(result), "Baked 2 objects into 1 atlas (2.0 MB) in 12 s")
        self.assertEqual(ops_lighting.summary_text(dict(result, up_to_date=True)), "The baked lighting is up to date")

    def test_read_result_takes_the_last_result_line(self):
        path = project.gs_dir(self.root) / ops_lighting.LOG_NAME
        prefix = lightmap.RESULT_LINE
        path.write_text(f"# header\n{prefix}{json.dumps({'objects': []})}\nBlender quit\n", encoding="utf-8")
        self.assertEqual(ops_lighting.read_result(path, prefix), {"objects": []})
        with open(path, "a", encoding="utf-8") as out:
            out.write(f"{prefix}{json.dumps({'error': 'LightmapError: Nothing to bake'})}\r\n")
        result = ops_lighting.read_result(path, prefix)
        self.assertEqual(ops_lighting._error_text(result), "Nothing to bake")
        path.write_text("no result\n", encoding="utf-8")
        self.assertIsNone(ops_lighting.read_result(path, prefix))
        path.write_text(f"{prefix}{{broken\n", encoding="utf-8")
        self.assertIsNone(ops_lighting.read_result(path, prefix))
        self.assertIsNone(ops_lighting.read_result(path.with_name("missing.log"), prefix))

    def test_clear_lighting(self):
        self.write_meta()
        folder = lightmap.lightmap_dir(self.root, export.export_name(bpy.context.scene))
        self.assertIsNotNone(ops_lighting.cached_status(bpy.context.scene, self.root))
        self.assertEqual(bpy.ops.gamestudio.clear_lighting(export=False), {"FINISHED"})
        self.assertFalse(folder.exists())
        self.assertIsNone(ops_lighting.cached_status(bpy.context.scene, self.root), "the cache was dropped")
        self.assertEqual(bpy.ops.gamestudio.clear_lighting(export=False), {"FINISHED"}, "nothing to clear is fine")


@unittest.skipUnless(bake.cycles_available(), "this Blender has no Cycles")
class LightingBakeTest(_LightingCase):
    def test_operator_bakes_in_background_mode_and_exports(self):
        level()
        name = export.export_name(bpy.context.scene)
        self.assertEqual(bpy.ops.gamestudio.bake_lighting(draft=True), {"FINISHED"})
        status = lightmap.status(bpy.context.scene)
        self.assertEqual((status["quality"], status["objects"]), ("draft", 2))
        manifest = json.loads((project.export_dir(self.root) / f"{name}.scene.json").read_text(encoding="utf-8"))
        self.assertEqual(len(manifest["lightmaps"]["objects"]), 2)

    def test_no_static_meshes_is_an_error(self):
        with self.assertRaisesRegex(RuntimeError, "Nothing to bake"):
            bpy.ops.gamestudio.bake_lighting(draft=True, export=False)

    def test_job_bakes_a_copy_in_a_background_blender(self):
        level()
        path = self.root / export.SCENES_DIR / "main.blend"
        path.parent.mkdir()
        bpy.ops.wm.save_as_mainfile(filepath=str(path))
        source = lightmap.lightmap_dir(self.root, "main").parent / ops_lighting.SOURCE_NAME

        self.assertEqual(ops_lighting.start_bake(bpy.context.scene, "draft"), "")
        self.assertEqual(JOB.title, ops_lighting.BAKE_TITLE)
        self.assertNotEqual(ops_lighting.start_bake(bpy.context.scene, "draft"), "", "one job at a time")
        ok = environment.wait(timeout=300)
        log_path = project.gs_dir(self.root) / ops_lighting.LOG_NAME
        self.assertTrue(ok, f"{JOB.message}\n{log_path.read_text(encoding='utf-8', errors='replace')[-3000:]}")
        self.assertTrue(JOB.message.startswith("Baked 2 objects into 1 atlas"), JOB.message)
        self.assertEqual(bpy.data.filepath, str(path), "the open file keeps its path")
        self.assertFalse(source.exists(), "the copy is deleted")
        status = lightmap.status(bpy.context.scene)
        self.assertEqual((status["quality"], status["objects"]), ("draft", 2))
        manifest = json.loads((project.export_dir(self.root) / "main.scene.json").read_text(encoding="utf-8"))
        self.assertEqual(len(manifest["lightmaps"]["objects"]), 2, "exported again when the bake was done")
        self.assertIn("2 objects", draw(self, PANEL, fake_context()))
