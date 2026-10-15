# SPDX-License-Identifier: GPL-3.0-or-later
"""The person's control over the agent (gamestudio.activity): the activity log and its file, Pause,
AI locks on every path that changes the scene (run_python included), Undo to Here, who is connected,
and the Agent panel (the top bar's popover), status bar item and Outliner header drawing in every state."""

import json
import types
import unittest
from unittest import mock

import bpy

from gamestudio import activity, activity_ui, bridge
from gamestudio.bridge import api, server

import helpers
from test_bridge import BridgeTestCase, Client
from test_ui import MockLayout, _FakeSelf


class AgentTestCase(BridgeTestCase):
    def setUp(self):
        super().setUp()
        activity.clear()
        activity.set_paused(False)
        self.crate = self.cube("Crate", (1, 0, 0))
        self.other = self.cube("Other", (4, 0, 0))
        api.undo_push("test: setup")

    def tearDown(self):
        activity.set_paused(False)
        activity.clear()
        super().tearDown()

    def newest(self) -> dict:
        return activity.entries()[0]

    def x(self, name="Crate") -> float:
        return bpy.data.objects[name].location.x


class ActivityLogTest(AgentTestCase):
    def test_ok_and_error_calls_are_logged(self):
        self.client.result("set_transform", {"name": "Crate", "location": [2, 0, 0]})
        self.client.error("get_object", {"name": "Nope"})
        self.client.error("no_such_method")
        moved, missing, unknown = reversed(activity.entries()[:3])
        self.assertEqual((moved["method"], moved["ok"], moved["changes"], moved["client"]),
                         ("set_transform", True, True, "test"))
        self.assertEqual(moved["args"], "name=Crate, location=[2, 0, 0]")
        self.assertEqual(moved["changed"], ["Crate"])
        self.assertEqual(moved["step"], moved["id"])
        self.assertGreaterEqual(moved["seconds"], 0.0)
        self.assertEqual((missing["ok"], missing["code"], missing["changes"], missing["step"]),
                         (False, "error", False, None))
        self.assertIn("No object named 'Nope'", missing["error"])
        self.assertEqual(unknown["code"], "unknown_method")
        self.assertLess(moved["id"], missing["id"])
        self.assertIsNone(activity.current())

    def test_created_changed_and_deleted_objects(self):
        out = self.client.result("run_python", {"code": "o = D.objects.new('Ghost', None)\n"
                                                        "C.scene.collection.objects.link(o)\n"
                                                        "D.objects['Crate'].location.z = 3"})
        self.assertTrue(out["ok"])
        e = self.newest()
        self.assertEqual((e["created"], e["changed"], e["deleted"]), (["Ghost"], ["Crate"], []))
        self.assertEqual(e["step"], e["id"])
        self.client.result("delete_objects", {"names": "Ghost"})
        self.assertEqual(self.newest()["deleted"], ["Ghost"])
        self.client.result("add_component", {"objects": ["Crate", "Other"], "component": "Spin"})
        self.assertEqual(self.newest()["changed"], ["Crate", "Other"])
        out = self.client.result("run_python", {"code": "D.objects['Crate'].location.x = 50\n1 / 0"})
        e = self.newest()
        self.assertEqual((e["ok"], e["code"], e["changed"], e["step"]), (False, "script_error", [], None))
        self.assertIn("ZeroDivisionError", e["error"])
        self.client.result("scene_summary")
        self.assertEqual((self.newest()["changes"], self.newest()["created"]), (False, []))

    def test_argument_summaries(self):
        s = activity.summarize_args
        code = "import bmesh\n\nme = D.meshes.new('Ramp')\n" + "x = 1\n" * 500
        self.assertEqual(s("run_python", {"code": code, "label": "ramp"}), "ramp: import bmesh (+501 lines)")
        self.assertEqual(s("run_python", {"code": "len(D.objects)", "read_only": True}), "len(D.objects) [read-only]")
        self.assertEqual(s("set_transform", {"items": [{"name": "A"}, {"name": "B"}], "snap_to_ground": True}),
                         "items=[2 items], snap_to_ground=true")
        self.assertEqual(s("create_from_kit", {"kind": "crate", "params": {"size": 1.25, "seed": 3},
                                               "location": [0, 6.123456, 0]}),
                         "kind=crate, params={size=1.25, seed=3}, location=[0, 6.123, 0]")
        self.assertEqual(s("delete_objects", {"names": ["A", "B"], "with_children": False}),
                         "names=[A, B], with_children=false")
        image = s("compare_images", {"a": "iVBORw0KGgo" + "A" * 100_000, "b": "x"})
        self.assertLess(len(image), 100)
        self.assertIn("\u2026", image)
        many = s("anything", {f"key{i}": "v" * 30 for i in range(20)})
        self.assertEqual(len(many), activity.ARGS_MAX)
        self.assertTrue(many.endswith("\u2026"))
        self.assertEqual(s("ping", {}), "")
        self.assertNotIn("secret", s("hello", {"token": "secret", "client": "c"}))
        long_code = activity.summarize_args("run_python", {"code": "x = '" + "y" * 500 + "'"})
        self.assertLessEqual(len(long_code), 80)

    def test_the_log_file_is_appended_and_capped(self):
        path = self.root / ".gamestudio" / activity.FILE_NAME
        with mock.patch.multiple(activity, FILE_MAX_LINES=6, FILE_KEEP_LINES=3):
            for _ in range(8):
                self.client.result("ping")
        records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(len(records), 4)  # cut to 3 at the 7th line, then one more
        self.assertEqual([r["id"] for r in records], [e["id"] for e in reversed(activity.entries()[:4])])
        last = records[-1]
        self.assertEqual((last["method"], last["ok"], last["client"]), ("ping", True, "test"))
        self.assertIn("session", last)
        self.assertRegex(last["time"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d$")
        self.assertFalse([k for k in last if k.startswith("_")])

    def test_memory_keeps_the_newest_entries(self):
        for _ in range(activity.MAX_ENTRIES + 5):
            api.dispatch("ping", {})
        items = activity.entries()
        self.assertEqual(len(items), activity.MAX_ENTRIES)
        self.assertEqual(items[0]["id"] - items[-1]["id"], activity.MAX_ENTRIES - 1)


class PauseTest(AgentTestCase):
    def test_pause_refuses_changes_and_allows_reads(self):
        activity.set_paused(True)
        err = self.client.error("set_transform", {"name": "Crate", "location": [9, 9, 9]})
        self.assertEqual(err["code"], server.PAUSED_BY_USER)
        self.assertEqual(err["data"]["hint"], "the user paused the agent in Blender; ask them or wait")
        self.assertEqual(self.x(), 1.0)
        for method, params in (("run_python", {"code": "D.objects['Crate'].location.x = 5"}), ("undo", {}),
                               ("redo", {}), ("save", {}), ("open_scene", {"path": "x.blend"}),
                               ("delete_objects", {"names": "Crate"}), ("restore_checkpoint", {"id": "x"})):
            self.assertEqual(self.client.error(method, params)["code"], server.PAUSED_BY_USER, method)
        self.assertEqual(self.x(), 1.0)
        refused = self.newest()
        self.assertEqual((refused["ok"], refused["code"]), (False, "paused_by_user"))
        # Reading still works, run_python included when it is read-only.
        self.assertEqual(self.client.result("get_object", {"name": "Crate"})["name"], "Crate")
        self.client.result("scene_summary")
        self.assertEqual(self.client.result("run_python", {"code": "len(D.objects)", "read_only": True})["result"], 2)
        status = self.client.result("status")
        self.assertEqual(status["agent"]["paused"], True)
        self.assertEqual(status["agent"]["locked_objects"], 0)
        activity.set_paused(False)
        self.client.result("set_transform", {"name": "Crate", "location": [9, 9, 9]})
        self.assertEqual(self.x(), 9.0)
        self.assertFalse(self.client.result("status")["agent"]["paused"])


class LockTest(AgentTestCase):
    def lock(self, obj) -> None:
        obj.gs.ai_lock = True
        api.undo_push("test: lock")

    def py(self, code, **params):
        return self.client.result("run_python", {"code": code, **params})

    def test_run_python_cannot_change_locked_objects(self):
        self.lock(self.crate)
        out = self.py("D.objects['Crate'].location.x = 7\nD.objects['Other'].location.x = 7")
        self.assertFalse(out["ok"])
        self.assertTrue(out["rolled_back"])
        self.assertIn("'Crate' (transform)", out["error"])
        self.assertIn("unlock", out["hint"])
        self.assertEqual((self.x("Crate"), self.x("Other")), (1.0, 4.0))  # the whole call is undone
        self.assertEqual(self.newest()["code"], "locked")
        for code, part in (("D.objects['Crate'].gs.ai_lock = False", "unlocked"),
                           ("D.objects['Crate'].modifiers.new('B', 'BEVEL')", "modifiers"),
                           ("D.objects.remove(D.objects['Crate'])", "deleted"),
                           ("D.objects['Crate'].name = 'Box'", "name"),
                           ("D.objects['Crate'].data.vertices[0].co.z += 1", "geometry"),
                           ("D.objects['Crate'].hide_render = True", "visibility")):
            out = self.py(code)
            self.assertFalse(out["ok"], code)
            self.assertIn(part, out["error"], code)
            crate = bpy.data.objects.get("Crate")
            self.assertIsNotNone(crate, code)
            self.assertTrue(crate.gs.ai_lock, code)
            self.assertEqual((len(crate.modifiers), crate.hide_render), (0, False), code)
        # Rolled back even when the code fails with rollback off.
        out = self.py("D.objects['Crate'].location.x = 7\n1 / 0", rollback_on_error=False)
        self.assertTrue(out["rolled_back"])
        self.assertIn("'Crate' (transform)", out["locked_changed"][0])
        self.assertEqual(self.x(), 1.0)
        # read_only checks objects and meshes; a modifier slips past that check but not past the lock.
        out = self.py("D.objects['Crate'].modifiers.new('B', 'BEVEL')", read_only=True)
        self.assertFalse(out["ok"])
        self.assertEqual(len(bpy.data.objects["Crate"].modifiers), 0)
        # Everything else stays editable.
        self.assertTrue(self.py("D.objects['Other'].location.x = 7")["ok"])
        self.assertEqual(self.x("Other"), 7.0)
        self.assertTrue(self.py("D.objects['Crate'].location.x", read_only=True)["ok"])

    def test_structured_tools_cannot_change_locked_objects(self):
        child = helpers.new_object("Child", None, parent=self.crate, location=(0, 0, 1))
        self.lock(child)
        for method, params in (("set_transform", {"name": "Child", "location": [0, 0, 0]}),
                               ("add_component", {"objects": "Child", "component": "Spin"}),
                               ("delete_objects", {"names": "Child"}),
                               ("asset_replace", {"instance": "Child", "asset": "Rock"}),
                               ("asset_add", {"objects": "Child", "move": True})):
            self.assertIn("locked", self.client.error(method, params)["message"], method)
        # Paths that reach a locked object through another one are taken back afterwards.
        err = self.client.error("delete_objects", {"names": "Crate", "with_children": True})
        self.assertIn("'Child' (deleted)", err["message"])
        self.assertIn("undone", err["message"])
        self.assertEqual(err["data"]["locked_changed"], ["'Child' (deleted)"])
        err = self.client.error("delete_objects", {"names": "Crate"})  # would unparent Child
        self.assertRegex(err["message"], r"'Child' \(.*parent")
        self.assertEqual(bpy.data.objects["Child"].parent.name, "Crate")
        self.assertEqual(self.newest()["code"], "locked")
        self.assertEqual(self.client.result("status")["agent"]["locked_objects"], 1)

    def test_locks_survive_taking_a_change_back(self):
        self.other.gs.ai_lock = True  # set by a script: not an undo step of its own
        out = self.py("D.objects['Other'].location.x = 9")
        self.assertFalse(out["ok"])
        other = bpy.data.objects["Other"]
        self.assertEqual((other.gs.ai_lock, other.location.x), (True, 4.0))
        child = helpers.new_object("Child", None, parent=bpy.data.objects["Crate"])  # undo renewed the objects
        api.undo_push("test: child")
        child.gs.ai_lock = True
        self.assertIn("'Child'", self.client.error("delete_objects", {"names": "Crate"})["message"])
        self.assertTrue(bpy.data.objects["Child"].gs.ai_lock)
        # Protect from the panel is an undo step of its own.
        self.assertEqual(activity.set_locked([bpy.data.objects["Crate"]], True), 1)
        self.assertTrue(bpy.data.objects["Crate"].gs.ai_lock)
        bpy.ops.ed.undo()
        self.assertFalse(bpy.data.objects["Crate"].gs.ai_lock)

    def test_the_agent_cannot_undo_a_lock(self):
        self.client.result("set_transform", {"name": "Other", "location": [5, 0, 0]})
        self.lock(self.other)  # the person's own step, on top of the agent's
        err = self.client.error("undo")
        self.assertIn("'Other' (unlocked)", err["message"])
        other = bpy.data.objects["Other"]
        self.assertEqual((other.gs.ai_lock, other.location.x), (True, 5.0))


class UndoToHereTest(AgentTestCase):
    def move(self, x) -> int:
        self.client.result("set_transform", {"name": "Crate", "location": [x, 0, 0]})
        return self.newest()["id"]

    def test_undo_to_here(self):
        a = self.move(2)
        b = self.move(3)
        bpy.data.objects["Crate"].scale.x = 2
        api.undo_push("user: scale")  # the person's step between two agent steps
        c = self.move(4)
        self.client.result("get_object", {"name": "Crate"})
        read = self.newest()["id"]
        self.assertFalse(activity.has_undo_step(activity.entry(read)))
        ok, message = activity.undo_to(read)
        self.assertFalse(ok)
        self.assertIn("no undo step", message)

        ok, message = activity.undo_to(b)
        self.assertTrue(ok, message)
        self.assertIn("Undid 3 steps", message)
        crate = bpy.data.objects["Crate"]
        self.assertEqual((crate.location.x, crate.scale.x), (2.0, 1.0))
        self.assertEqual([activity.is_undone(activity.entry(i)) for i in (a, b, c)], [False, True, True])
        ok, message = activity.undo_to(c)
        self.assertFalse(ok)
        self.assertIn("already undone", message)
        for _ in range(3):
            bpy.ops.ed.redo()
        crate = bpy.data.objects["Crate"]
        self.assertEqual((crate.location.x, crate.scale.x), (4.0, 2.0))
        self.assertTrue(activity.undo_to(a)[0])
        self.assertEqual(self.x(), 1.0)

        self.client.result("run_python", {"code": "D.objects['Crate'].location.x = 10"})
        script = self.newest()["id"]
        self.assertTrue(activity.undo_to(script)[0])
        self.assertEqual(self.x(), 1.0)

        activity._on_load()  # a file load ends the undo history of earlier entries
        ok, message = activity.undo_to(script)
        self.assertFalse(ok)
        self.assertIn("closed or reloaded", message)

    def test_undo_to_here_right_after_a_file_load(self):
        path = self.save_scene()
        self.client.result("open_scene", {"path": str(path)})  # a new, empty undo history
        first = self.move(2)
        self.move(3)
        ok, message = activity.undo_to(first)
        self.assertTrue(ok, message)
        self.assertEqual(self.x(), 1.0)

    def test_steps_beyond_the_undo_history(self):
        edit = bpy.context.preferences.edit
        saved = edit.undo_steps
        edit.undo_steps = 2
        try:
            first = self.move(2)
            for x in (3, 4, 5):
                self.move(x)
            ok, message = activity.undo_to(first)
            self.assertFalse(ok)
            self.assertIn("no longer has the undo steps", message)
            self.assertEqual(self.x(), 5.0)  # back where it was
        finally:
            edit.undo_steps = saved


class ClientsTest(AgentTestCase):
    def test_clients_say_who_they_are(self):
        other = Client(self.server.port)
        reply = other.call("hello", {"token": self.server.token, "client": "gamestudio-mcp 0.4.0",
                                     "agent": {"name": "claude-code", "version": "2.1.3"}})
        self.assertIn("result", reply)
        infos = bridge.clients_info()
        self.assertEqual([i["name"] for i in infos], ["test", "claude-code"])
        self.assertEqual((infos[1]["version"], infos[1]["via"]), ("2.1.3", "gamestudio-mcp 0.4.0"))
        self.assertGreater(infos[1]["connected_at"], 0)
        self.assertEqual(bridge.client_label("claude-code"), "Claude Code")
        self.assertEqual(bridge.client_label("cursor-vscode"), "cursor-vscode")
        other.result("ping")
        self.assertEqual(self.newest()["client"], "claude-code")
        self.assertEqual(activity_ui.agent_name(), "Claude")
        other.close()
        self.assertTrue(helpers.pump_until(lambda: len(bridge.clients_info()) == 1, [bridge.pump]))


class Layout(MockLayout):
    """test_ui's validating layout, with popovers."""

    def _child(self, *_args, **_kwargs):
        return Layout(self._test, self.texts)

    row = column = box = split = grid_flow = column_flow = _child

    def popover(self, panel, text="", icon="NONE", **_kwargs):
        self._icon(icon)
        self._test.assertTrue(hasattr(bpy.types, panel), f"panel {panel} is not registered")
        self.texts.append(text)


class AgentUiTest(AgentTestCase):
    def draw(self, fn, *args) -> str:
        texts = []
        context = types.SimpleNamespace(window_manager=bpy.context.window_manager, scene=bpy.context.scene,
                                        view_layer=bpy.context.view_layer)
        fn(Layout(self, texts), context, *args)
        return " ".join(" ".join(texts).split())

    def statusbar(self) -> str:
        texts = []
        header = bpy.types.STATUSBAR_HT_header
        activity_ui.draw_statusbar(_FakeSelf(header, Layout(self, texts)), types.SimpleNamespace())
        return " ".join(texts)

    def outliner(self) -> str:
        texts = []
        activity_ui.draw_outliner_header(_FakeSelf(bpy.types.OUTLINER_HT_header, Layout(self, texts)), None)
        return " ".join(texts)

    def test_panel_status_bar_and_outliner_in_every_state(self):
        for name in ("GAMESTUDIO_PT_agent", "GAMESTUDIO_PT_agent_popover", "GAMESTUDIO_PT_agent_locks"):
            self.assertTrue(hasattr(bpy.types, name), name)
        self.assertIn(activity_ui.draw_statusbar, bpy.types.STATUSBAR_HT_header.draw._draw_funcs)
        text = self.draw(activity_ui.draw_agent)
        self.assertIn("Idle", text)
        self.assertIn("Nothing yet", text)
        self.assertIn("Nothing protected", text)
        self.assertEqual(self.statusbar(), "test: idle")  # who is connected, and what it does
        self.assertEqual(self.outliner(), "")

        self.client.result("set_transform", {"name": "Crate", "location": [2, 0, 0]})
        self.client.error("get_object", {"name": "Nope"})
        self.assertIn("test: get_object", self.statusbar())  # just finished
        self.lock(self.crate)
        activity.select(activity.entries()[1]["id"])
        activity.set_paused(True)
        self.client.error("delete_objects", {"names": "Other"})
        text = self.draw(activity_ui.draw_agent)
        for expected in ("Paused", "Resume", "Protected: 1", "Crate", "set_transform name=Crate",
                         "get_object name=Nope", "delete_objects", "set_transform: done", "Undo to Here",
                         "Select & Frame", "Changed: Crate"):
            self.assertIn(expected, text)
        self.assertEqual(self.statusbar(), "Agent paused")
        self.assertEqual(self.outliner(), "1")
        short = self.draw(activity_ui.draw_agent, 1)
        self.assertIn("delete_objects", short)
        self.assertNotIn("get_object", short)  # one row, then "Show 2 more"
        self.assertIn("set_transform: done", short)  # the chosen entry's details stay
        e = activity.entries()[1]
        self.assertIn("Click to select", activity_ui.tooltip(e))
        self.assertEqual(activity_ui.entry_icon(activity.entries()[0]), "PAUSE")
        self.assertEqual(activity_ui.entry_icon(e), "ERROR")

    def lock(self, obj):
        obj.gs.ai_lock = True


if __name__ == "__main__":
    unittest.main()
