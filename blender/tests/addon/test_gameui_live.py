# SPDX-License-Identifier: GPL-3.0-or-later
"""Game UI live link (docs/GAME-UI.md §4): ui.canvas after edits, ui.remove, ui.open / ui.close."""

import json
import os
import time
from unittest import mock

import bpy

from gamestudio.bridge import api
from gamestudio.core import components, export, gameui, gameui_export, gameui_live, live, project

import helpers
from test_gameui_export import UiFileTestCase, curve, emission_material, keys


class GameUiLiveTest(UiFileTestCase):
    def setUp(self):
        super().setUp()
        self._saved = (gameui_live.DEBOUNCE, gameui_live.MAX_WAIT, gameui_live.POLL_INTERVAL)
        self.label = self.text("Label", "Play")
        components.add_component(self.label, "UiButton", {"action": "close"})
        self.hud = bpy.data.scenes.new("HUD")
        gameui.setup_canvas_scene(self.hud)
        self.score = self.text("Score", "Score {score}", scene=self.hud)
        self.save()  # exports both canvases: what a game loads
        self.hub = helpers.FakeHub()
        self.client = helpers.connect_global_link(self.hub)
        gameui_live.pump()
        self.assertTrue(gameui_live.is_active())
        self.settle(0.3)  # the first export finds the files up to date: nothing is sent
        self.assertEqual(self.hub.messages("ui.canvas"), [])
        self.hub.clear()

    def tearDown(self):
        gameui_live.DEBOUNCE, gameui_live.MAX_WAIT, gameui_live.POLL_INTERVAL = self._saved
        gameui_live._s.pending_open = None
        helpers.restore_global_link()
        gameui_live.pump()  # deactivates
        self.hub.close()
        super().tearDown()

    def pump(self):
        self.client.pump()
        gameui_live.pump()

    def settle(self, seconds=0.15):
        helpers.pump_until(lambda: False, [self.pump], seconds)

    def wait_for(self, t, count=1, timeout=3.0):
        ok = helpers.pump_until(lambda: len(self.hub.messages(t)) >= count, [self.pump], timeout)
        self.assertTrue(ok, f"no {t!r} message; got {[m['t'] for m in self.hub.messages()]}")
        return self.hub.messages(t)

    def canvas_message(self, name="Title") -> dict:
        def arrived():
            return [m for m in self.hub.messages("ui.canvas") if m["canvas"] == name]

        helpers.pump_until(lambda: arrived(), [self.pump], 3.0)
        self.assertTrue(arrived(), f"no ui.canvas for {name}; got {[m.get('canvas') for m in self.hub.messages()]}")
        return arrived()[-1]

    def test_transform_edit_sends_the_whole_canvas(self):
        self.label.location.x = 1.0
        bpy.context.view_layer.update()
        msg = self.canvas_message("Title")
        data = msg["data"]
        self.assertEqual(data["format"], "gamestudio.ui")
        self.assertEqual(self.element(data, "Label")["transform"]["x"], 1060.0)
        on_disk = json.loads((self.ui_dir() / "Title.ui.json").read_text(encoding="utf-8"))
        self.assertEqual(on_disk, data)  # the file is written with what the game got
        self.assertEqual(gameui_export.read_index(self.root)["canvases"]["Title"]["rev"], data["rev"])
        self.assertEqual([m["canvas"] for m in self.hub.messages("ui.canvas")], ["Title"])

    def test_text_and_component_edits(self):
        self.label.data.body = "Start"
        bpy.context.view_layer.update()
        self.assertEqual(self.element(self.canvas_message()["data"], "Label")["text"]["body"], "Start")
        self.hub.clear()
        components.set_field(self.label, "UiButton", "target", "Options")  # in --background only the callback tells
        data = self.canvas_message()["data"]
        self.assertEqual(self.element(data, "Label")["components"]["UiButton"]["target"], "Options")

    def test_edits_are_debounced(self):
        gameui_live.DEBOUNCE, gameui_live.MAX_WAIT = 0.3, 2.0
        for x in (0.25, 0.5, 0.75):
            self.label.location.x = x
            bpy.context.view_layer.update()
            self.settle(0.05)
        self.assertEqual(self.hub.messages("ui.canvas"), [])
        data = self.canvas_message()["data"]
        self.assertEqual(self.element(data, "Label")["transform"]["x"], 1035.0)
        self.settle(0.4)
        self.assertEqual(len(self.hub.messages("ui.canvas")), 1)

    def test_steady_edits_still_send_within_the_maximum_wait(self):
        gameui_live.DEBOUNCE, gameui_live.MAX_WAIT = 1.0, 0.2
        started = time.monotonic()
        while not self.hub.messages("ui.canvas") and time.monotonic() - started < 1.0:
            self.label.location.x += 0.01
            bpy.context.view_layer.update()
            self.settle(0.03)
        self.assertTrue(self.hub.messages("ui.canvas"))
        self.assertLess(time.monotonic() - started, 0.8)

    def test_unchanged_canvas_is_not_sent(self):
        gameui_live.mark_canvas_dirty("Title")
        gameui_live.mark_canvas_dirty(self.hud)
        self.settle(0.3)
        self.assertEqual(self.hub.messages("ui.canvas"), [])

    def test_edit_in_a_canvas_the_window_does_not_show(self):
        gameui_live.POLL_INTERVAL = 0.05
        self.score.location.y = -2.0  # the HUD scene is not evaluated: only the poll sees this
        data = self.canvas_message("HUD")["data"]
        self.assertEqual(self.element(data, "Score")["transform"]["y"], 740.0)

    def test_removed_and_added_canvases(self):
        gameui_live.POLL_INTERVAL = 0.05
        bpy.data.scenes.remove(self.hud)
        removes = self.wait_for("ui.remove")
        self.assertEqual(removes[0]["canvas"], "HUD")
        self.assertNotIn("HUD", gameui_export.read_index(self.root)["canvases"])
        self.assertFalse((self.ui_dir() / "HUD.ui.json").exists())
        pause = bpy.data.scenes.new("Pause")
        gameui.setup_canvas_scene(pause)
        self.assertEqual(self.canvas_message("Pause")["data"]["canvas"], "Pause")
        self.assertIn("Pause", gameui_export.read_index(self.root)["canvases"])

    def test_send_open_and_close(self):
        self.assertTrue(gameui_live.send_open("HUD"))
        opens = self.wait_for("ui.open")
        self.assertEqual(opens[0], {"t": "ui.open", "canvas": "HUD"})
        self.assertEqual(self.hub.messages("ui.canvas"), [])  # the game has HUD as exported
        self.assertTrue(gameui_live.send_close())
        self.assertTrue(gameui_live.send_close(self.hud))
        closes = self.wait_for("ui.close", 2)
        self.assertEqual(closes, [{"t": "ui.close"}, {"t": "ui.close", "canvas": "HUD"}])

    def test_open_after_play_waits_for_the_reloaded_game(self):
        gameui_live.open_after_play(self.scene)
        self.hub.broadcast({"t": "game.status", "state": "playing"})  # still the game from before Play
        self.settle(0.2)
        self.assertEqual(self.hub.messages("ui.open"), [])
        self.hub.broadcast({"t": "game.ready", "scene": "main", "rev": 1})
        self.hub.broadcast({"t": "game.status", "state": "playing"})
        self.assertEqual(self.wait_for("ui.open")[0]["canvas"], "Title")
        self.assertIsNone(gameui_live._s.pending_open)

    def exports(self):
        """Counts the canvases exported while the with-block runs (mock of export_canvas)."""
        return mock.patch.object(gameui_export, "export_canvas", wraps=gameui_export.export_canvas)

    def test_selection_and_text_edits_export_only_what_they_change(self):
        with self.exports() as exported:
            self.label.select_set(True)
            bpy.context.view_layer.objects.active = self.label
            bpy.context.view_layer.update()  # a Scene update alone: nothing to export
            self.settle(0.3)
            self.assertEqual(exported.call_count, 0)
            self.label.data.body = "Start"  # the text and its curve: its canvas only, not every canvas
            bpy.context.view_layer.update()
            self.canvas_message("Title")
            self.settle(0.1)
        self.assertEqual({c.args[0].name for c in exported.call_args_list}, {"Title"})

    def test_a_shared_material_exports_the_canvases_using_it(self):
        mat = emission_material("Gold", (1.0, 0.8, 0.0, 1.0))
        emission = next(n for n in mat.node_tree.nodes if n.type == "EMISSION")
        self.label.data.materials.append(mat)
        bpy.context.view_layer.update()
        self.canvas_message("Title")
        self.hub.clear()
        with self.exports() as exported:
            emission.inputs["Color"].default_value = (0.0, 0.5, 1.0, 1.0)
            bpy.context.view_layer.update()
            self.canvas_message("Title")
            self.settle(0.1)
        self.assertEqual({c.args[0].name for c in exported.call_args_list}, {"Title"})  # the HUD does not use it
        # The HUD uses it too: the window shows Title, so only the material says the HUD changed.
        self.score.data.materials.append(mat)
        gameui_live.mark_canvas_dirty(self.hud)
        self.canvas_message("HUD")
        gameui_live.POLL_INTERVAL = 100.0  # not the poll
        self.hub.clear()
        emission.inputs["Color"].default_value = (1.0, 0.0, 0.0, 1.0)
        bpy.context.view_layer.update()
        hud = self.canvas_message("HUD")["data"]
        self.assertEqual(self.element(hud, "Score")["text"]["color"], [1.0, 0.0, 0.0, 1.0])

    def test_canvases_using_follows_materials_node_groups_images_and_fonts(self):
        image = bpy.data.images.new("Art", 4, 4)
        group = bpy.data.node_groups.new("Look", "ShaderNodeTree")
        group.nodes.new("ShaderNodeTexImage").image = image
        mat = emission_material("Textured", (1.0, 1.0, 1.0, 1.0))
        mat.node_tree.nodes.new("ShaderNodeGroup").node_tree = group
        self.score.data.materials.append(mat)
        for data in (image, group, mat, self.score.data):
            self.assertEqual(gameui_live.canvases_using({data.as_pointer()}), {"HUD"}, data.name)
        self.assertEqual(gameui_live.canvases_using({image.as_pointer(), self.label.data.as_pointer()}),
                         {"HUD", "Title"})
        if self.label.data.font is not None and self.label.data.font == self.score.data.font:
            self.assertEqual(gameui_live.canvases_using({self.label.data.font.as_pointer()}), {"HUD", "Title"})
        self.assertEqual(gameui_live.canvases_using({bpy.data.materials.new("Unused").as_pointer()}), set())

    def test_excluding_a_collection_is_seen_by_the_poll(self):
        gameui_live.POLL_INTERVAL = 0.05
        extra = bpy.data.collections.new("Extra")
        self.hud.collection.children.link(extra)
        tip = bpy.data.objects.new("Tip", bpy.data.curves.new("Tip", "FONT"))
        extra.objects.link(tip)
        self.assertIn("Tip", [e["name"] for e in self.canvas_message("HUD")["data"]["elements"]])
        self.hub.clear()
        self.hud.view_layers[0].layer_collection.children["Extra"].exclude = True
        self.assertNotIn("Tip", [e["name"] for e in self.canvas_message("HUD")["data"]["elements"]])

    def test_writes_from_unsaved_changes_keep_the_file_stale_until_it_is_saved(self):
        self.addCleanup(setattr, api, "agent_dirty", False)
        api.agent_dirty = True  # unsaved changes (in --background the bridge's record; in the UI is_dirty)
        self.label.location.x = 1.0
        bpy.context.view_layer.update()
        self.canvas_message("Title")
        self.assertEqual(gameui_export.unsaved_canvases(self.root), {"ui/ui.blend": ["Title"]})
        self.assertEqual(gameui_export.stale_ui_files(self.root), [self.root / "ui" / "ui.blend"])
        self.save()  # exports the file as saved
        self.assertEqual(gameui_export.unsaved_canvases(self.root), {})
        self.assertFalse((project.gs_dir(self.root) / gameui_export.UNSAVED_FILE).exists())
        self.assertEqual(gameui_export.stale_ui_files(self.root), [])

    def test_reopening_after_discarded_changes_exports_the_saved_file(self):
        self.addCleanup(setattr, api, "agent_dirty", False)
        api.agent_dirty = True
        self.label.location.x = 1.0
        bpy.context.view_layer.update()
        self.assertEqual(self.element(self.canvas_message("Title")["data"], "Label")["transform"]["x"], 1060.0)
        # Closed without saving and opened again (File > Revert does the same): the export follows the file.
        bpy.ops.wm.open_mainfile(filepath=str(self.root / "ui" / "ui.blend"))
        on_disk = json.loads((self.ui_dir() / "Title.ui.json").read_text(encoding="utf-8"))
        self.assertEqual(self.element(on_disk, "Label")["transform"]["x"], 960.0)
        self.assertEqual(gameui_export.unsaved_canvases(self.root), {})
        self.assertEqual(gameui_export.stale_ui_files(self.root), [])
        self.assertFalse(bpy.data.is_dirty)

    def animate_label(self):
        """Markers open at 1 and close at 11 (10 fps, the scene ending at 16); Label fades in over open."""
        self.scene.render.fps = 10
        self.scene.frame_end = 16
        for name, frame in (("open", 1), ("close", 11)):
            self.scene.timeline_markers.new(name, frame=frame)
        keys(self.label, "color", 3, {1: 0.0, 11: 1.0})

    def canvas_where(self, name, predicate) -> dict:
        """The data of the last ui.canvas of `name` that matches predicate(data), waiting for one."""
        def matching():
            return [m["data"] for m in self.hub.messages("ui.canvas") if m["canvas"] == name and predicate(m["data"])]

        helpers.pump_until(lambda: matching(), [self.pump], 3.0)
        self.assertTrue(matching(), f"no matching ui.canvas for {name}; got {len(self.hub.messages('ui.canvas'))}")
        return matching()[-1]

    def test_markers_and_keyframe_edits_send_the_animations(self):
        gameui_live.POLL_INTERVAL = 0.05
        self.animate_label()  # markers send no depsgraph update: the poll sees them
        data = self.canvas_where("Title", lambda d: "animations" in d)
        label_id = self.element(data, "Label")["id"]
        self.assertEqual(data["animations"]["open"]["tracks"][label_id]["opacity"][-1], 1.0)
        self.assertEqual(data["animations"]["close"], {"duration": 0.5, "fps": 10, "tracks": {}})
        gameui_live.POLL_INTERVAL = 100.0  # a keyframe edit comes through the depsgraph, not the poll
        self.settle(0.1)
        self.hub.clear()
        fc = curve(self.label, "color", 3)
        fc.keyframe_points[1].co.y = 0.5
        fc.update()
        self.label.animation_data.action.update_tag()
        bpy.context.view_layer.update()
        data = self.canvas_where("Title", lambda d: self.element(d, "Label").get("opacity") == 0.5)
        self.assertEqual(data["animations"]["open"]["tracks"][label_id]["opacity"][-1], 0.5)

    def test_a_timeline_change_is_sent_without_the_poll(self):
        gameui_live.POLL_INTERVAL = 0.05
        self.animate_label()
        self.canvas_where("Title", lambda d: "animations" in d)
        gameui_live.POLL_INTERVAL = 100.0
        self.settle(0.1)
        self.scene.render.fps = 20  # a scene update: its timeline changed
        bpy.context.view_layer.update()
        data = self.canvas_where("Title", lambda d: d.get("animations", {}).get("open", {}).get("fps") == 20)
        self.assertEqual(data["animations"]["open"]["duration"], 0.5)
        self.hub.clear()
        self.scene.frame_set(3)  # scrubbing: the rest pose and the tracks come from the keys, nothing changes
        bpy.context.view_layer.update()
        self.settle(0.3)
        self.assertEqual(self.hub.messages("ui.canvas"), [])

    def test_a_shared_action_exports_every_canvas_using_it(self):
        self.animate_label()
        action = self.label.animation_data.action
        self.score.animation_data_create()
        self.score.animation_data.action = action
        self.score.animation_data.action_slot = self.label.animation_data.action_slot
        gameui_live.POLL_INTERVAL = 100.0
        gameui_live.mark_canvas_dirty(self.hud)
        gameui_live.mark_canvas_dirty(self.scene)
        self.settle(0.3)
        with self.exports() as exported:
            fc = curve(self.label, "color", 3)
            fc.keyframe_points[0].co.y = 0.25
            fc.update()
            action.update_tag()
            bpy.context.view_layer.update()  # the window shows Title: only the action says the HUD changed
            self.settle(0.3)
        self.assertEqual({c.args[0].name for c in exported.call_args_list}, {"HUD", "Title"})

    def test_level_live_link_stays_off_and_toggle_is_respected(self):
        live.pump()
        self.assertFalse(live.is_active())
        self.scene.gs.live_enabled = False
        gameui_live.pump()
        self.assertFalse(gameui_live.is_active())
        self.label.location.x = 2.0
        bpy.context.view_layer.update()
        self.settle(0.3)
        self.assertEqual(self.hub.messages("ui.canvas"), [])

    def test_translation_tables_are_sent_when_they_change(self):
        """§11.3: the poll watches ui/locales; a changed table is exported and sent as ui.locales."""
        gameui_live.POLL_INTERVAL = 0.05
        self.settle(0.2)
        self.assertEqual(self.hub.messages("ui.locales"), [])  # nothing changed since the game loaded
        folder = self.root / "ui" / "locales"
        folder.mkdir()
        table = {"name": "Français", "strings": {"Play": "Jouer", "Score {score}": "Score : {score}"}}
        (folder / "fr.json").write_text(json.dumps(table, ensure_ascii=False), encoding="utf-8")
        msg = self.wait_for("ui.locales")[-1]
        self.assertEqual(msg, {"t": "ui.locales", "sourceLocale": "en",
                               "locales": {"fr": {"file": "locales/fr.json", "name": "Français"}},
                               "tables": {"fr": table}})
        self.assertTrue((self.ui_dir() / "locales" / "fr.json").is_file())
        self.settle(0.2)
        self.assertEqual(len(self.hub.messages("ui.locales")), 1)
        table["strings"]["Play"] = "Commencer"
        (folder / "fr.json").write_text(json.dumps(table, ensure_ascii=False), encoding="utf-8")
        os.utime(folder / "fr.json", (time.time() + 5, time.time() + 5))
        self.assertEqual(self.wait_for("ui.locales", 2)[-1]["tables"]["fr"]["strings"]["Play"], "Commencer")
        (folder / "fr.json").unlink()
        self.assertEqual(self.wait_for("ui.locales", 3)[-1]["locales"], {})
        self.assertEqual(gameui_live.stats()["locales"], 3)

    def test_a_model_edit_sends_the_canvas_with_the_new_glb(self):
        """§11.1: the GLB is exported again only for model edits; its hash in the canvas tells the game."""
        model = self.empty("Gem Model", at=(-3.0, 1.0, 0.0))
        components.add_component(model, "UiModel")
        mesh = bpy.data.meshes.new("Gem")
        mesh.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0), (0, 0, 1)], [], [(0, 1, 2), (0, 1, 3)])
        gem = self.link(bpy.data.objects.new("Gem", mesh))
        gem.parent = model
        bpy.context.view_layer.update()
        first = self.canvas_where("Title", lambda d: "files" in d)
        self.assertEqual(self.element(first, "Gem Model")["model"]["glb"], "Title.ui.glb")
        glb_hash = first["files"]["Title.ui.glb"]
        self.hub.clear()
        with mock.patch.object(export, "_gltf", wraps=export._gltf) as gltf:
            self.label.location.x = 1.0  # not the model: the canvas goes, the GLB stays
            bpy.context.view_layer.update()
            moved = self.canvas_where("Title", lambda d: self.element(d, "Label")["transform"]["x"] == 1060.0)
            self.assertEqual((gltf.call_count, moved["files"]["Title.ui.glb"]), (0, glb_hash))
            mesh.vertices[1].co.x = 2.0
            mesh.update()
            bpy.context.view_layer.update()
            edited = self.canvas_where("Title", lambda d: d["files"]["Title.ui.glb"] != glb_hash)
            self.assertEqual(gltf.call_count, 1)
        self.assertGreater(edited["rev"], moved["rev"])
        on_disk = (self.ui_dir() / "Title.ui.glb").read_bytes()
        self.assertEqual(export.file_hash(on_disk), edited["files"]["Title.ui.glb"])
