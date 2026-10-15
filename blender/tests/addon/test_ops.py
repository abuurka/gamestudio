# SPDX-License-Identifier: GPL-3.0-or-later
"""Operators: results, error reports, and play/stop/pause/step messages."""

from unittest import mock

import bpy

from gamestudio import prefs
from gamestudio.core import components, devserver, export, ids, link, play, project

import helpers


class OperatorTest(helpers.ProjectTestCase):
    def setUp(self):
        super().setUp()
        self.obj = helpers.new_object("Hero", helpers.new_mesh())
        bpy.context.view_layer.objects.active = self.obj
        self.hub = None
        self._open_window = prefs._defaults.open_game_window

    def tearDown(self):
        prefs._defaults.open_game_window = self._open_window
        play.cancel()
        helpers.restore_global_link()
        if self.hub is not None:
            self.hub.close()
        super().tearDown()

    def op(self, name, **kwargs):
        with bpy.context.temp_override(active_object=self.obj, object=self.obj):
            return getattr(bpy.ops.gamestudio, name)("EXEC_DEFAULT", **kwargs)

    def assert_error(self, text, name, **kwargs):
        # Operators that report an ERROR raise RuntimeError when called from Python.
        with self.assertRaises(RuntimeError) as cm:
            self.op(name, **kwargs)
        self.assertIn(text, str(cm.exception))

    def connect(self, peers=None):
        self.hub = helpers.FakeHub(peers=peers)
        return helpers.connect_global_link(self.hub)

    def test_component_operators(self):
        self.assertEqual(self.op("add_component", component="Spin"), {"FINISHED"})
        self.assertEqual(components.list_components(self.obj), ["Spin"])
        self.assertEqual(self.op("toggle_component_enabled", component="Spin"), {"FINISHED"})
        self.assertFalse(components.is_enabled(self.obj, "Spin"))
        self.assertEqual(self.op("remove_component", component="Spin"), {"FINISHED"})
        self.assertEqual(components.list_components(self.obj), [])
        self.assert_error("has no component 'Spin'", "remove_component", component="Spin")

    def test_assign_ids_and_repair(self):
        other = helpers.new_object("Other")
        ids.ensure_id(self.obj)
        copy = self.obj.copy()
        bpy.context.scene.collection.objects.link(copy)
        self.assertEqual(self.op("assign_ids"), {"FINISHED"})
        all_ids = [ids.get_id(o) for o in (self.obj, other, copy)]
        self.assertTrue(all(all_ids))
        self.assertEqual(len(set(all_ids)), 3)

    def test_reload_registry(self):
        self.assertEqual(self.op("reload_registry"), {"FINISHED"})
        project.registry_path(self.root).unlink()
        self.assertEqual(self.op("reload_registry"), {"CANCELLED"})  # warning only
        project.set_project_root_override(None)
        self.assert_error("Save the .blend inside a GameStudio project (folder with gamestudio.json)",
                          "reload_registry")

    def test_export_operator(self):
        self.assertEqual(self.op("export_scene"), {"FINISHED"})
        self.assertTrue(any((self.root / ".gamestudio" / "export").glob("*.scene.json")))

    def test_play_errors_are_reported(self):
        project.set_project_root_override(None)
        self.assert_error("Save the .blend inside a GameStudio project", "play")
        project.set_project_root_override(self.root)
        self.assert_error("No package.json", "play")
        self.assertFalse(play.is_pending())
        self.assert_error("No package.json", "start_server")
        self.assertEqual(self.op("stop_server"), {"CANCELLED"})
        self.assert_error("The dev server is not running", "open_game")

    def test_play_with_a_connected_game(self):
        self.connect()
        self.assertEqual(self.op("play"), {"FINISHED"})
        sent = lambda: [m["t"] for m in self.hub.messages() if m["t"].startswith(("scene.", "play."))]
        self.assertTrue(helpers.pump_until(lambda: len(sent()) >= 2, [link.get_link().pump]))
        self.assertEqual(sent(), ["scene.load", "play.start"])
        load = self.hub.messages("scene.load")[0]
        self.assertEqual(load["url"], f"/__gs/export/{load['scene']}.scene.json")
        self.assertEqual(load["rev"], bpy.context.scene["gs_rev"])

    def test_play_waits_for_a_game(self):
        prefs._defaults.open_game_window = False
        client = self.connect(peers=[])
        self.assertEqual(self.op("play"), {"FINISHED"})
        self.assertTrue(play.is_pending())
        for _ in range(3):
            client.pump()
            play.pump()
        self.assertEqual(self.hub.messages("scene.load"), [])
        self.hub.broadcast({"t": "peers", "peers": [{"peer": "g9", "role": "game"}]})
        self.assertTrue(helpers.pump_until(lambda: self.hub.messages("play.start"), [client.pump, play.pump]))
        self.assertFalse(play.is_pending())

    def test_play_starts_the_dev_server_before_exporting(self):
        # The dev server boots while the scene exports (devserver.start() does not wait for it).
        prefs._defaults.open_game_window = False
        calls = []
        real_export = export.export_scene

        def exporting(scene):
            calls.append("export")
            self.assertEqual(play.status(), play.EXPORTING)
            return real_export(scene)

        with mock.patch.object(devserver, "start", side_effect=lambda root, on_ready=None: calls.append(
                ("dev server", root)) or "started"), \
                mock.patch.object(devserver, "is_starting", return_value=True), \
                mock.patch.object(export, "export_scene", side_effect=exporting):
            self.assertEqual(self.op("play"), {"FINISHED"})
            self.assertEqual(calls, [("dev server", self.root), "export"])
            self.assertTrue(play.is_pending())
            self.assertEqual(play.status(), play.STARTING_SERVER)
        # The server comes up and a game joins: the scene goes out.
        client = self.connect(peers=[])
        self.hub.broadcast({"t": "peers", "peers": [{"peer": "g9", "role": "game"}]})
        self.assertTrue(helpers.pump_until(lambda: self.hub.messages("play.start"), [client.pump, play.pump]))
        self.assertFalse(play.is_pending())

    def test_a_failed_export_forgets_the_play(self):
        self.connect(peers=[])
        with mock.patch.object(export, "export_scene", side_effect=export.ExportError("Nothing to export")):
            self.assert_error("Nothing to export", "play")
        self.assertFalse(play.is_pending())
        self.assertEqual(play.status(), "")

    def test_stop_forgets_a_pending_play(self):
        prefs._defaults.open_game_window = False
        self.connect(peers=[])
        self.assertEqual(self.op("play"), {"FINISHED"})
        self.assertTrue(play.is_pending())
        self.assertEqual(self.op("stop"), {"CANCELLED"})  # no game to stop: a warning
        self.assertFalse(play.is_pending())

    def test_stop_pause_step(self):
        self.assertEqual(self.op("stop"), {"CANCELLED"})  # no game: warning, nothing sent
        client = self.connect()
        self.assertEqual(self.op("pause"), {"FINISHED"})
        self.assertEqual(self.op("pause"), {"FINISHED"})
        self.assertEqual(self.op("step", frames=3), {"FINISHED"})
        self.assertEqual(self.op("stop"), {"FINISHED"})
        wanted = ["play.pause", "play.pause", "play.pause", "play.step", "play.stop"]
        got = lambda: [m for m in self.hub.messages() if m["t"].startswith("play.")]
        self.assertTrue(helpers.pump_until(lambda: len(got()) >= len(wanted), [client.pump]))
        self.assertEqual([m["t"] for m in got()], wanted)
        self.assertEqual([m.get("paused") for m in got()[:3]], [True, False, True])
        self.assertEqual(got()[3]["frames"], 3)

    def test_clear_logs(self):
        link.get_link().logs.append({"t": "log", "level": "info", "msg": "x"})
        self.assertEqual(self.op("clear_logs"), {"FINISHED"})
        self.assertEqual(len(link.get_link().logs), 0)
