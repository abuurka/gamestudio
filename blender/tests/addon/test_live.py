# SPDX-License-Identifier: GPL-3.0-or-later
"""Live editing (§5.4/§5.5): transform batches, component updates, removals, re-export."""

import time

import bpy

from gamestudio.core import components, convert, ids, live

import helpers


class DepsgraphInBackgroundTest(helpers.ProjectTestCase):
    def test_depsgraph_update_post_fires_after_view_layer_update(self):
        obj = helpers.new_object("Mover")
        bpy.context.view_layer.update()
        seen = []

        def handler(_scene, depsgraph):
            seen.extend((u.id.name, u.is_updated_transform) for u in depsgraph.updates)

        bpy.app.handlers.depsgraph_update_post.append(handler)
        try:
            obj.location.x = 3
            bpy.context.view_layer.update()
        finally:
            bpy.app.handlers.depsgraph_update_post.remove(handler)
        self.assertIn(("Mover", True), seen)


class LiveTest(helpers.ProjectTestCase):
    def setUp(self):
        super().setUp()
        self._saved = (live.GEOMETRY_DEBOUNCE, live.COMPONENT_DEBOUNCE)
        bpy.context.scene.name = "main"
        mesh = helpers.new_mesh()
        self.parent = helpers.new_object("Parent", mesh, location=(1, 0, 0))
        self.child = helpers.new_object("Child", mesh, parent=self.parent, location=(0, 0, 1))
        self.other = helpers.new_object("Other", mesh, location=(-3, 0, 0))
        components.add_component(self.other, "Spin")
        for obj in (self.parent, self.child, self.other):
            ids.ensure_id(obj)
        bpy.context.view_layer.update()
        self.hub = helpers.FakeHub()
        self.client = helpers.connect_global_link(self.hub)
        live.pump()
        self.assertTrue(live.is_active())
        self.hub.clear()

    def tearDown(self):
        live.GEOMETRY_DEBOUNCE, live.COMPONENT_DEBOUNCE = self._saved
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

    def settle(self, seconds=0.15):
        helpers.pump_until(lambda: False, [self.pump], seconds)

    def move(self, obj, **loc):
        for axis, value in loc.items():
            setattr(obj.location, axis, value)
        bpy.context.view_layer.update()

    def test_moving_objects_sends_one_batch_with_local_y_up_trs(self):
        self.move(self.other, x=5)
        self.move(self.parent, z=2)
        batch = self.wait_for("entity.transform")[0]
        self.assertEqual(batch["scene"], "main")  # games showing another level ignore it (SPEC 5.4)
        items = {item["id"]: item for item in batch["items"]}
        self.assertEqual(set(items), {ids.get_id(self.other), ids.get_id(self.parent)})
        self.assertEqual(items[ids.get_id(self.other)], convert.transform_item(self.other, ids.get_id(self.other)))
        self.assertEqual(items[ids.get_id(self.parent)]["p"], [1.0, 2.0, 0.0])

    def test_moving_a_parent_does_not_send_its_children(self):
        self.move(self.parent, x=4)
        self.wait_for("entity.transform")
        self.settle()
        sent = [item["id"] for m in self.hub.messages("entity.transform") for item in m["items"]]
        self.assertEqual(sent, [ids.get_id(self.parent)])

    def test_transform_batches_are_rate_limited(self):
        start = time.monotonic()
        while time.monotonic() - start < 0.5:
            self.move(self.other, x=self.other.location.x + 0.01)
            self.pump()
        self.settle(0.1)
        batches = self.hub.messages("entity.transform")
        self.assertGreater(len(batches), 3)
        self.assertLessEqual(len(batches), int(0.6 * 30) + 1)
        last = batches[-1]["items"][-1]
        self.assertEqual(last, convert.transform_item(self.other, ids.get_id(self.other)))

    def test_component_edits_are_debounced_and_complete(self):
        components.set_field(self.other, "Spin", "speed", 4)
        self.other.gsc_Spin.axis = (1, 0, 0)  # a UI-style write goes through the update callback
        self.pump()
        self.assertEqual(self.hub.messages("entity.components"), [])
        msg = self.wait_for("entity.components")[0]
        self.assertEqual(msg, {"t": "entity.components", "id": ids.get_id(self.other),
                               "components": {"Spin": {"speed": 4.0, "axis": [1.0, 0.0, 0.0]}}, "scene": "main"})
        self.settle()
        self.assertEqual(len(self.hub.messages("entity.components")), 1)

    def test_unchanged_components_are_not_resent(self):
        components.set_field(self.other, "Spin", "speed", 1.0)  # same as before
        self.settle(0.4)
        self.assertEqual(self.hub.messages("entity.components"), [])

    def test_adding_and_disabling_components(self):
        components.add_component(self.parent, "Coin", {"value": 3})
        msg = self.wait_for("entity.components")[0]
        self.assertEqual(msg["components"], {"Coin": {"value": 3, "rarity": "common", "magnet": None}})
        components.set_enabled(self.parent, "Coin", False)
        msg = self.wait_for("entity.components", 2)[1]
        self.assertEqual(msg["components"], {})

    def test_deleting_objects_sends_entity_remove(self):
        gone = ids.get_id(self.other)
        bpy.data.objects.remove(self.other)
        bpy.context.view_layer.update()
        removed = self.wait_for("entity.remove")[0]
        self.assertEqual((removed["ids"], removed["scene"]), ([gone], "main"))

    def test_new_objects_trigger_reexport_and_scene_load(self):
        live.GEOMETRY_DEBOUNCE = 0.05
        rev_before = bpy.context.scene.get("gs_rev", 0)
        helpers.new_object("Spawned", helpers.new_mesh(), location=(0, 4, 0))
        bpy.context.view_layer.update()
        msg = self.wait_for("scene.load")[0]
        self.assertEqual(msg["scene"], "main")
        self.assertEqual(msg["url"], "/__gs/export/main.scene.json")
        self.assertEqual(msg["rev"], rev_before + 1)
        self.assertTrue((self.root / ".gamestudio" / "export" / "main.glb").exists())
        # After the reload, the new object streams like any other.
        self.hub.clear()
        spawned = bpy.data.objects["Spawned"]
        self.move(spawned, x=1)
        batch = self.wait_for("entity.transform")[0]
        self.assertEqual([i["id"] for i in batch["items"]], [ids.get_id(spawned)])

    def test_geometry_edits_trigger_reexport(self):
        live.GEOMETRY_DEBOUNCE = 0.05
        mesh = self.other.data
        mesh.vertices[0].co.x = 0.25
        mesh.update()
        bpy.context.view_layer.update()
        self.wait_for("scene.load")

    def test_keyframing_an_action_triggers_reexport(self):
        live.GEOMETRY_DEBOUNCE = 0.05
        self.other.keyframe_insert("location", frame=1)
        bpy.context.view_layer.update()
        self.wait_for("scene.load")

    def test_nla_changes_trigger_reexport(self):
        live.GEOMETRY_DEBOUNCE = 0.05
        action = bpy.data.actions.new("Wave")
        self.other.animation_data_create()
        self.settle()
        self.hub.clear()
        track = self.other.animation_data.nla_tracks.new()
        track.strips.new("Wave", 1, action)
        bpy.context.view_layer.update()
        self.wait_for("scene.load")
        self.settle(0.2)
        self.hub.clear()
        track.mute = True  # only tags the object, like most NLA edits
        bpy.context.view_layer.update()
        self.wait_for("scene.load")

    def test_marking_and_editing_off_scene_prefabs_triggers_reexport(self):
        from gamestudio.core import export

        live.GEOMETRY_DEBOUNCE = 0.05
        saved = live.PREFAB_CHECK_INTERVAL
        live.PREFAB_CHECK_INTERVAL = 0.05
        try:
            prefab = bpy.data.collections.new("Coin")  # not linked into the scene: no depsgraph updates
            part = bpy.data.objects.new("CoinMesh", helpers.new_mesh("CoinMesh"))
            prefab.objects.link(part)
            self.settle(0.2)
            self.assertEqual(self.hub.messages("scene.load"), [])
            export.set_prefab(prefab)
            self.wait_for("scene.load")
            self.settle(0.2)
            self.hub.clear()
            part.location.x = 2.0  # an edit no depsgraph sees
            self.wait_for("scene.load")
        finally:
            live.PREFAB_CHECK_INTERVAL = saved

    def test_vertex_edits_of_off_scene_prefabs_are_caught_on_save(self):
        from gamestudio.core import export

        live.GEOMETRY_DEBOUNCE = 0.05
        prefab = bpy.data.collections.new("Rock")
        part = bpy.data.objects.new("RockMesh", helpers.new_mesh("RockMesh"))
        prefab.objects.link(part)
        export.set_prefab(prefab)
        self.wait_for("scene.load")
        self.settle(0.2)
        self.hub.clear()
        part.data.vertices[0].co.z = 0.5  # same counts: the cheap check cannot see it
        self.settle(0.3)
        self.assertEqual(self.hub.messages("scene.load"), [])
        bpy.ops.wm.save_as_mainfile(filepath=str(self.root / "main.blend"))
        self.wait_for("scene.load")

    def test_component_edit_tags_do_not_trigger_reexport(self):
        # In the UI, writing a property with an update callback tags the object
        # and its mesh for transform/geometry/shading updates. Simulate that.
        live.GEOMETRY_DEBOUNCE = 0.05
        components.set_field(self.other, "Spin", "speed", 6)
        self.other.update_tag(refresh={"OBJECT", "DATA", "TIME"})
        self.other.data.update_tag()
        bpy.context.view_layer.update()
        self.wait_for("entity.components")
        self.settle(0.3)
        self.assertEqual(self.hub.messages("scene.load"), [])
        self.assertEqual(self.hub.messages("entity.transform"), [])

    def test_nothing_is_sent_without_a_game_or_with_live_off(self):
        bpy.context.scene.gs.live_enabled = False
        self.pump()
        self.assertFalse(live.is_active())
        self.move(self.other, x=9)
        components.set_field(self.other, "Spin", "speed", 9)
        self.settle(0.3)
        self.assertEqual(self.hub.messages(), [])

        bpy.context.scene.gs.live_enabled = True
        self.hub.broadcast({"t": "peers", "peers": [{"peer": "t1", "role": "tool"}]})
        helpers.pump_until(lambda: not self.client.game_peers(), [self.client.pump])
        self.pump()
        self.assertFalse(live.is_active())
        self.move(self.other, x=10)
        self.settle(0.2)
        self.assertEqual(self.hub.messages("entity.transform"), [])
