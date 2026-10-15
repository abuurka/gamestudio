# SPDX-License-Identifier: GPL-3.0-or-later
"""Live game shader values (§5.4 `material.values`): debounced and complete, sent only while live editing
is on, without a re-export for value edits (their depsgraph noise is ignored) but with one for a new
shader, a renamed material or a new asset; the journal's tier afterwards; undo and redo."""

import json
from unittest import mock

import bpy

from gamestudio.bridge import api
from gamestudio.core import export, ids, live, shaders

import helpers
from test_bake import principled
from test_export_shaders import mesh_with, shader_registry


class LiveShaderTest(helpers.ProjectTestCase):
    registry_data = shader_registry()

    def setUp(self):
        super().setUp()
        self._saved = live.GEOMETRY_DEBOUNCE
        live.GEOMETRY_DEBOUNCE = 0.05
        bpy.context.scene.name = "main"
        self.export_dir = self.root / ".gamestudio" / "export"
        self.sand = bpy.data.materials.new("GS_Sand")
        shaders.set_shader(self.sand, "Triplanar", {"wet": 0.25})
        ids.ensure_id(mesh_with("Ground", self.sand))
        bpy.context.view_layer.update()
        export.export_scene()  # what the game has, and the journal's snapshot
        self.hub = helpers.FakeHub()
        self.client = helpers.connect_global_link(self.hub)
        live.pump()
        self.assertTrue(live.is_active())
        self.hub.clear()

    def tearDown(self):
        live.GEOMETRY_DEBOUNCE = self._saved
        helpers.restore_global_link()
        live.pump()  # deactivates
        self.hub.close()
        super().tearDown()

    def pump(self):
        self.client.pump()
        live.pump()

    def wait_for(self, t, count=1, timeout=3.0):
        ok = helpers.pump_until(lambda: len(self.hub.messages(t)) >= count, [self.pump], timeout)
        self.assertTrue(ok, f"no {t!r} message; got {[m['t'] for m in self.hub.messages()]}")
        return self.hub.messages(t)

    def settle(self, seconds=0.3):
        helpers.pump_until(lambda: False, [self.pump], seconds)

    def manifest(self) -> dict:
        return json.loads((self.export_dir / "main.scene.json").read_text(encoding="utf-8"))

    def glbs(self) -> dict:
        return {p.name: p.read_bytes() for p in self.export_dir.glob("*.glb")}

    def test_value_edits_are_sent_at_once_without_a_reload(self):
        seen = []

        def record(_scene, depsgraph):
            seen.extend((type(u.id).__name__, u.is_updated_transform or u.is_updated_geometry,
                         u.is_updated_shading) for u in depsgraph.updates)

        bpy.app.handlers.depsgraph_update_post.append(record)
        try:
            grp = self.sand.gss_Triplanar
            grp.wet = 0.5  # as the Properties editor writes them: the update callbacks run
            bpy.context.view_layer.update()
            grp.wet = 0.75
            grp.tile = 2.0
            bpy.context.view_layer.update()
            self.pump()
            self.assertEqual(self.hub.messages("material.values"), [])  # debounced
            msg = self.wait_for("material.values")[0]
        finally:
            bpy.app.handlers.depsgraph_update_post.remove(record)
        self.assertEqual(msg, {"t": "material.values", "material": "GS_Sand",
                               "values": {"diffuse": "", "tile": 2.0, "wet": 0.75}, "scene": "main"})
        # The writes tagged the material and its node tree as property writes, and the mesh for shading.
        self.assertIn(("Material", True, True), seen)
        self.assertIn(("ShaderNodeTree", True, False), seen)
        self.assertIn(("Mesh", False, True), seen)
        self.settle()
        self.assertEqual(len(self.hub.messages("material.values")), 1)  # the latest values, once
        self.assertEqual(self.hub.messages("scene.load"), [])  # that noise did not re-export

    def test_api_and_bridge_edits_are_sent_too(self):
        shaders.set_values(self.sand, {"wet": 1})
        self.assertEqual(self.wait_for("material.values")[0]["values"], {"diffuse": "", "tile": 3.5, "wet": 1.0})
        api.dispatch("set_material_shader", {"material": "GS_Sand", "shader": "Triplanar", "values": {"tile": 8}})
        msg = self.wait_for("material.values", 2)[1]
        self.assertEqual(msg["values"], {"diffuse": "", "tile": 8.0, "wet": 1.0})
        shaders.set_values(self.sand, {"tile": 8})  # unchanged: nothing to send
        self.settle()
        self.assertEqual(len(self.hub.messages("material.values")), 2)
        self.assertEqual(self.hub.messages("scene.load"), [])

    def test_nothing_is_sent_with_live_off_or_without_a_game(self):
        bpy.context.scene.gs.live_enabled = False
        self.pump()
        self.assertFalse(live.is_active())
        self.sand.gss_Triplanar.wet = 0.5
        bpy.context.view_layer.update()
        self.settle(0.2)
        self.assertEqual(self.hub.messages(), [])

        bpy.context.scene.gs.live_enabled = True
        self.hub.broadcast({"t": "peers", "peers": [{"peer": "t1", "role": "tool"}]})
        helpers.pump_until(lambda: not self.client.game_peers(), [self.client.pump])
        self.pump()
        self.assertFalse(live.is_active())
        shaders.set_values(self.sand, {"wet": 0.75})
        self.settle(0.2)
        self.assertEqual(self.hub.messages("material.values"), [])

    def test_a_node_edit_right_after_a_value_edit_still_reloads(self):
        self.sand.gss_Triplanar.wet = 0.5
        bpy.context.view_layer.update()
        principled(self.sand).inputs["Roughness"].default_value = 0.2  # tags the material for shading alone
        bpy.context.view_layer.update()
        self.wait_for("scene.load")
        self.assertEqual(export.last_export()["tier"], "full")

    def test_a_new_shader_reloads_instead(self):
        glbs = self.glbs()
        shaders.set_shader(self.sand, "Foliage", {"sway": 2})
        self.wait_for("scene.load")
        self.assertEqual(export.last_export()["tier"], "layout")  # the GLBs stay as they are
        self.assertEqual(self.manifest()["materials"], {"GS_Sand": {"shader": "Foliage", "values": {"sway": 2.0}}})
        self.assertEqual(self.glbs(), glbs)
        self.assertEqual(self.hub.messages("material.values"), [])

        self.settle(0.2)
        self.hub.clear()
        self.sand.gss_Foliage.sway = 3  # the new shader's values are live now
        self.assertEqual(self.wait_for("material.values")[0]["values"], {"sway": 3.0})

        self.hub.clear()
        self.sand.gss_Foliage.sway = 4
        self.sand.gs.shader = ""  # a direct write, inside the value edit's quiet time
        self.wait_for("scene.load")
        self.assertNotIn("materials", self.manifest())
        self.assertEqual(self.hub.messages("material.values"), [])

    def test_a_renamed_material_reloads_instead(self):
        self.sand.name = "GS_Dune"
        self.sand.gss_Triplanar.wet = 0.5  # the game knows it as GS_Sand
        self.wait_for("scene.load")
        self.assertEqual(self.manifest()["materials"]["GS_Dune"]["values"]["wet"], 0.5)
        self.assertEqual(self.hub.messages("material.values"), [])

    def test_asset_values_reload_instead(self):
        (self.root / "assets").mkdir()
        (self.root / "assets" / "sand.png").write_bytes(b"\x89PNG")
        shaders.set_values(self.sand, {"diffuse": "assets/sand.png", "wet": 0.5})
        self.wait_for("scene.load")
        self.assertEqual(export.last_export()["tier"], "layout")
        manifest = self.manifest()
        self.assertEqual(manifest["materials"]["GS_Sand"]["values"],
                         {"diffuse": "assets/sand.png", "tile": 3.5, "wet": 0.5})
        self.assertTrue(manifest["assets"]["assets/sand.png"].startswith("files/assets/sand.png?v="))
        self.assertEqual(self.hub.messages("material.values"), [])

    def test_the_next_export_holds_the_values(self):
        glbs = self.glbs()
        self.sand.gss_Triplanar.wet = 0.625
        bpy.context.view_layer.update()
        self.wait_for("material.values")
        self.settle()
        self.assertEqual(self.hub.messages("scene.load"), [])
        # Play, a save or export_scene: the manifest is stale, the GLBs are not.
        with mock.patch.object(export, "_gltf", wraps=export._gltf) as gltf:
            result = export.export_scene()
        self.assertEqual(result["tier"], "layout", result.get("reason"))
        self.assertFalse(gltf.called)
        self.assertEqual(self.manifest()["materials"]["GS_Sand"]["values"]["wet"], 0.625)
        self.assertEqual(self.glbs(), glbs)

    def test_undo_and_redo_send_the_restored_values(self):
        api.undo_push("test: set up")
        self.sand.gss_Triplanar.wet = 0.875
        api.undo_push("test: wet")
        self.wait_for("material.values")
        self.hub.clear()
        window = bpy.context.window_manager.windows[0]
        with bpy.context.temp_override(window=window):
            self.assertIn("FINISHED", bpy.ops.ed.undo())
        self.assertEqual(self.wait_for("material.values")[0]["values"]["wet"], 0.25)
        self.hub.clear()
        with bpy.context.temp_override(window=window):
            self.assertIn("FINISHED", bpy.ops.ed.redo())
        self.assertEqual(self.wait_for("material.values")[0]["values"]["wet"], 0.875)
