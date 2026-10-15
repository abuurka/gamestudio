# SPDX-License-Identifier: GPL-3.0-or-later
"""The agent bridge (gamestudio.bridge): JSON-RPC framing, auth, main-thread execution,
discovery files and every method's Blender handler. Timers never fire headless, so the tests
pump the server themselves; a client thread checks requests only run inside pump()."""

import base64
import json
import math
import os
import pathlib
import shutil
import socket
import tempfile
import threading
import time
import unittest

import bpy

from gamestudio import bridge
from gamestudio.bridge import api, discovery, server
from gamestudio.core import components, ids, project, registry, shaders

import helpers
from test_shaders import shader_registry


class Client:
    """A JSON-RPC client on the test's main thread: send, then pump the bridge until the reply."""

    def __init__(self, port, token=None):
        self.sock = socket.create_connection(("127.0.0.1", port), timeout=5)
        self.sock.setblocking(False)
        self.buf = b""
        self.next_id = 1
        if token is not None:
            reply = self.call("hello", {"token": token, "client": "test"})
            assert "result" in reply, reply

    def send_raw(self, data: bytes):
        self.sock.sendall(data)

    def read(self, timeout=10.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            bridge.pump(0.005)
            try:
                chunk = self.sock.recv(1 << 20)
                if not chunk:
                    return None
                self.buf += chunk
            except BlockingIOError:
                pass
            except OSError:
                return None
            if b"\n" in self.buf:
                line, self.buf = self.buf.split(b"\n", 1)
                return json.loads(line)
        raise AssertionError("no reply from the bridge")

    def call(self, method, params=None, timeout=30.0):
        req_id = self.next_id
        self.next_id += 1
        self.send_raw((json.dumps({"jsonrpc": "2.0", "id": req_id, "method": method,
                                   "params": params or {}}) + "\n").encode())
        reply = self.read(timeout)
        assert reply is not None and reply.get("id") == req_id, reply
        return reply

    def result(self, method, params=None, timeout=30.0):
        reply = self.call(method, params, timeout)
        assert "result" in reply, f"{method} failed: {reply.get('error')}"
        return reply["result"]

    def error(self, method, params=None):
        reply = self.call(method, params)
        assert "error" in reply, f"{method} unexpectedly succeeded: {reply.get('result')}"
        return reply["error"]

    def close(self):
        self.sock.close()


class BridgeTestCase(helpers.ProjectTestCase):
    def setUp(self):
        super().setUp()
        self.user_dir = pathlib.Path(tempfile.mkdtemp(prefix="gs_bridge_user_", dir=helpers.tmp_base()))
        discovery.user_dir_override = self.user_dir
        self.server = bridge.start(force=True)
        self.client = Client(self.server.port, self.server.token)

    def tearDown(self):
        self.client.close()
        bridge.stop()
        discovery.user_dir_override = None
        shutil.rmtree(self.user_dir, ignore_errors=True)
        super().tearDown()

    def cube(self, name="Cube", location=(0, 0, 0), size=2.0):
        bpy.ops.mesh.primitive_cube_add(size=size, location=location)
        obj = bpy.context.active_object
        obj.name = name
        return obj

    def save_scene(self, name="main"):
        path = self.root / "scenes" / f"{name}.blend"
        path.parent.mkdir(exist_ok=True)
        bpy.ops.wm.save_as_mainfile(filepath=str(path))
        project.invalidate_cache()
        return path


# --- Protocol ----------------------------------------------------------------------------------


class ProtocolTest(BridgeTestCase):
    def test_hello_reports_the_session(self):
        info = self.client.result("hello", {"token": self.server.token})
        self.assertEqual(info["pid"], os.getpid())
        self.assertTrue(info["background"])
        self.assertEqual(info["bridge"], server.BRIDGE_PROTOCOL)

    def test_first_request_must_be_hello_with_the_token(self):
        anon = Client(self.server.port)
        err = anon.error("status")
        self.assertEqual(err["code"], server.UNAUTHORIZED)
        self.assertIn("blender.json", err["data"]["hint"])
        self.assertIsNone(anon.read(2.0))  # the bridge closed the connection
        anon.close()

        wrong = Client(self.server.port)
        err = wrong.error("hello", {"token": "0" * 32})
        self.assertEqual(err["code"], server.UNAUTHORIZED)
        wrong.close()

        odd = Client(self.server.port)  # a token compare_digest cannot take as str
        self.assertEqual(odd.error("hello", {"token": "é" * 32})["code"], server.UNAUTHORIZED)
        odd.close()

    def test_a_first_line_that_is_not_hello_closes_the_connection(self):
        hello = (json.dumps({"jsonrpc": "2.0", "id": 2, "method": "hello",
                             "params": {"token": self.server.token}}) + "\n").encode()
        for first in (b"{not json", b"[1, 2]", b'{"jsonrpc": "2.0", "id": 1, "method": "hello", "params": [1]}',
                      b"[" * 100_000):
            anon = Client(self.server.port)
            anon.send_raw(first + b"\n" + hello)
            self.assertIn("error", anon.read(), first[:40])
            self.assertIsNone(anon.read(2.0), first[:40])  # closed: the hello after it was never read
            anon.close()
        self.assertEqual(self.server.clients, 1)  # only setUp's client

    def test_a_web_page_cannot_post_run_python(self):
        """fetch(bridge, {method: "POST", mode: "no-cors", body}) sends HTTP headers, then a body
        with hello and run_python. Its first line is the request line, so it gets no further."""
        marker = self.root / "posted.txt"
        body = "\n" + "".join(json.dumps(m) + "\n" for m in [
            {"jsonrpc": "2.0", "id": 1, "method": "hello", "params": {"token": self.server.token}},
            {"jsonrpc": "2.0", "id": 2, "method": "run_python",
             "params": {"code": f"open({str(marker)!r}, 'w').write('ran')"}},
        ])
        page = Client(self.server.port)
        page.send_raw((f"POST / HTTP/1.1\r\nHost: 127.0.0.1:{self.server.port}\r\nOrigin: http://localhost:5173\r\n"
                       f"Content-Type: text/plain;charset=UTF-8\r\nContent-Length: {len(body)}\r\n\r\n{body}").encode())
        self.assertEqual(page.read()["error"]["code"], server.PARSE_ERROR)
        self.assertIsNone(page.read(2.0))  # closed after that one error
        page.close()
        for _ in range(20):
            bridge.pump(0.005)
        self.assertFalse(marker.exists(), "run_python ran")
        self.assertEqual(self.server.clients, 1)
        self.assertEqual(self.client.result("ping")["pong"], True)  # the bridge carries on

    def test_errors_follow_json_rpc(self):
        self.client.send_raw(b"{not json\n")
        self.assertEqual(self.client.read()["error"]["code"], server.PARSE_ERROR)
        self.client.send_raw(b'{"jsonrpc": "2.0", "id": 9, "method": "status", "params": [1]}\n')
        self.assertEqual(self.client.read()["error"]["code"], server.INVALID_PARAMS)
        err = self.client.error("scene_sumary")
        self.assertEqual(err["code"], server.METHOD_NOT_FOUND)
        self.assertIn("scene_summary", err["data"]["hint"])

    def test_notifications_get_no_reply(self):
        self.client.send_raw(b'{"jsonrpc": "2.0", "method": "ping"}\n')
        self.assertEqual(self.client.result("ping")["pong"], True)  # the next reply is the ping's

    def test_requests_run_on_the_main_thread_inside_pump(self):
        replies = []
        seen = {}

        def remote():
            sock = socket.create_connection(("127.0.0.1", self.server.port), timeout=10)
            f = sock.makefile("rwb")
            for i, (method, params) in enumerate([("hello", {"token": self.server.token}),
                                                  ("run_python", {"code": "import threading\n"
                                                   "threading.current_thread() is threading.main_thread()"})]):
                f.write((json.dumps({"jsonrpc": "2.0", "id": i, "method": method, "params": params}) + "\n").encode())
                f.flush()
                replies.append(json.loads(f.readline()))
            sock.close()

        thread = threading.Thread(target=remote, daemon=True)
        thread.start()
        time.sleep(0.3)
        seen["before_pump"] = len(replies)  # nothing is answered until the main thread pumps
        self.assertTrue(helpers.pump_until(lambda: len(replies) == 2, [lambda: bridge.pump(0.005)], 10))
        thread.join(2)
        self.assertEqual(seen["before_pump"], 0)
        self.assertIs(replies[1]["result"]["result"], True)

    def test_large_replies_are_streamed(self):
        reply = self.client.result("run_python", {"code": "'x' * 3_000_000"})
        self.assertEqual(len(reply["result"]), api_max_result())


def api_max_result():
    from gamestudio.bridge import pyexec

    return pyexec.MAX_RESULT_CHARS + len(f"... [truncated {3_000_002 - pyexec.MAX_RESULT_CHARS} characters]")


# --- Discovery ----------------------------------------------------------------------------------


class DiscoveryTest(BridgeTestCase):
    def test_project_and_user_files(self):
        blend = self.save_scene()
        bridge.sync_discovery(claim=True)
        info = json.loads((project.gs_dir(self.root) / "blender.json").read_text(encoding="utf-8"))
        self.assertEqual(info["pid"], os.getpid())
        self.assertEqual(info["port"], self.server.port)
        self.assertEqual(info["token"], self.server.token)
        self.assertEqual(pathlib.Path(info["blend"]), blend)
        self.assertEqual(pathlib.Path(info["project"]), self.root)
        self.assertTrue(info["background"])
        self.assertTrue(info["blenderVersion"].startswith("5."))
        user = json.loads((self.user_dir / "bridge.json").read_text(encoding="utf-8"))
        self.assertEqual(user["token"], self.server.token)
        install = json.loads((self.user_dir / "last-blender.json").read_text(encoding="utf-8"))
        self.assertTrue(install["binary"])
        self.assertNotIn("token", install)

        bridge.stop()
        self.assertFalse((project.gs_dir(self.root) / "blender.json").exists())
        self.assertFalse((self.user_dir / "bridge.json").exists())
        self.assertTrue((self.user_dir / "last-blender.json").exists(), "kept for launching Blender later")
        self.server = bridge.start(force=True)
        self.client = Client(self.server.port, self.server.token)

    def test_a_live_owner_is_not_overwritten_without_a_claim(self):
        self.save_scene()
        bridge.sync_discovery()  # saving claims the file once
        path = project.gs_dir(self.root) / "blender.json"
        other = {"pid": os.getppid(), "port": 1, "token": "t" * 32, "project": str(self.root)}
        path.write_text(json.dumps(other), encoding="utf-8")
        bridge.sync_discovery(claim=False)
        self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["pid"], os.getppid())
        bridge.sync_discovery(claim=True)  # opening or saving a scene claims it
        self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["pid"], os.getpid())

    def test_a_stale_file_is_replaced(self):
        self.save_scene()
        path = project.gs_dir(self.root) / "blender.json"
        path.write_text(json.dumps({"pid": 2 ** 22 + 7, "port": 1, "token": "x"}), encoding="utf-8")
        bridge.sync_discovery(claim=False)
        self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["pid"], os.getpid())

    def test_preference_exists_and_defaults_on(self):
        from gamestudio import prefs

        self.assertTrue(prefs.get_prefs().bridge_enabled)
        self.assertIn("bridge_enabled", prefs.GSPreferences.__annotations__)
        bridge.apply_preference()  # never starts or stops anything in --background
        self.assertTrue(bridge.is_running())


# --- Session --------------------------------------------------------------------------------------


class SessionMethodsTest(BridgeTestCase):
    def test_status(self):
        status = self.client.result("status")
        self.assertEqual(status["blender"]["pid"], os.getpid())
        self.assertEqual(status["project"]["root"], str(self.root))
        self.assertEqual(status["registry"]["components"], len(helpers.fixture_registry()["components"]))
        self.assertIn("devserver", status)
        self.assertIn("link", status)

    def test_status_lists_only_scenes_as_levels(self):
        self.save_scene()
        library = self.root / "assets" / "props.blend"
        library.parent.mkdir(exist_ok=True)
        bpy.ops.wm.save_as_mainfile(filepath=str(library), copy=True)
        inspection = self.root / "assets" / "models" / "crate" / "crate.blend"  # an inspection file, now open
        inspection.parent.mkdir(parents=True)
        bpy.ops.wm.save_as_mainfile(filepath=str(inspection))
        status = self.client.result("status")
        self.assertEqual(status["project"]["scenes"], ["scenes/main.blend"])
        self.assertEqual(pathlib.Path(status["file"]["path"]), inspection)  # still the open file
        err = self.client.error("open_scene", {"path": str(self.root / "scenes" / "missing.blend")})
        self.assertEqual(err["data"]["hint"], "Scenes in the project: scenes/main.blend")

    def test_save_open_and_unsaved_changes(self):
        blend = self.save_scene()
        self.client.result("run_python", {"code": "bpy.ops.mesh.primitive_cube_add()"})
        self.assertTrue(self.client.result("status")["file"]["dirty"])
        err = self.client.error("open_scene", {"path": str(blend)})
        self.assertIn("unsaved changes", err["message"])
        self.assertIn("save_changes", err["data"]["hint"])
        saved = self.client.result("save")
        self.assertEqual(pathlib.Path(saved["saved"]), blend)
        self.assertFalse(self.client.result("status")["file"]["dirty"])
        opened = self.client.result("open_scene", {"path": str(blend)})
        self.assertEqual(opened["objects"], 1)

        new = self.root / "scenes" / "level2.blend"
        created = self.client.result("open_scene", {"path": str(new), "create": True})
        self.assertTrue(new.is_file())
        self.assertEqual(created["objects"], 0)
        err = self.client.error("open_scene", {"path": str(self.root / "scenes" / "missing.blend")})
        self.assertIn("main.blend", err["data"]["hint"])

    def test_checkpoint_list_restore(self):
        blend = self.save_scene()
        cube = self.cube()
        cube.location.x = 1.0
        first = self.client.result("checkpoint", {"label": "one cube at x=1"})
        folder = pathlib.Path(first["path"])
        self.assertTrue((folder / "main.blend").is_file())
        manifest = json.loads((folder / "checkpoint.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["label"], "one cube at x=1")
        self.assertEqual(manifest["source"], "scenes/main.blend")
        self.assertEqual(manifest["objects"], 1)

        bpy.data.objects["Cube"].location.x = 9.0
        self.cube("Second")
        listed = self.client.result("list_checkpoints")
        self.assertEqual(listed["checkpoints"][0]["id"], first["id"])

        restored = self.client.result("restore_checkpoint", {"id": first["id"]})
        self.assertEqual(pathlib.Path(restored["saved_as"]), blend)
        self.assertEqual(pathlib.Path(bpy.data.filepath), blend)
        self.assertEqual(bpy.data.objects["Cube"].location.x, 1.0)
        self.assertNotIn("Second", bpy.data.objects)
        backup = restored["backup_checkpoint"]
        self.assertTrue(backup and "before-restore" in backup)
        self.assertEqual(self.client.result("list_checkpoints")["count"], 2)
        err = self.client.error("restore_checkpoint", {"id": "nope"})
        self.assertIn("list_checkpoints", err["data"]["hint"])

    def test_checkpoint_needs_a_saved_scene(self):
        err = self.client.error("checkpoint", {"label": "x"})
        self.assertIn("save", err["data"]["hint"])

    def test_one_tool_call_is_one_undo_step(self):
        cube = self.cube()
        api.undo_push("test: cube")
        self.client.result("set_transform", {"name": "Cube", "location": [3, 0, 0]})
        self.client.result("apply_material", {"objects": "Cube", "preset": "wood"})
        self.client.result("undo")
        cube = bpy.data.objects["Cube"]
        self.assertEqual(tuple(cube.location), (3.0, 0.0, 0.0))
        self.assertNotIn("GS_Wood", [s.material.name for s in cube.material_slots if s.material])
        self.client.result("undo")
        self.assertEqual(tuple(bpy.data.objects["Cube"].location), (0.0, 0.0, 0.0))
        self.client.result("redo")
        self.assertEqual(tuple(bpy.data.objects["Cube"].location), (3.0, 0.0, 0.0))


# --- Scene ------------------------------------------------------------------------------------------


class SceneMethodsTest(BridgeTestCase):
    def setUp(self):
        super().setUp()
        self.a = self.cube("CrateA", (1, 2, 0))
        self.b = self.cube("CrateB", (4, 0, 0))
        self.lamp = helpers.new_object("Lamp", bpy.data.lights.new("Lamp", "POINT"), location=(0, 0, 3))
        components.add_component(self.a, "Spin", {"speed": 2})
        api.undo_push("test: setup")

    def test_scene_summary(self):
        summary = self.client.result("scene_summary")
        self.assertEqual(summary["stats"]["objects"], 3)
        self.assertEqual(summary["stats"]["by_type"], {"MESH": 2, "LIGHT": 1})
        self.assertEqual(summary["stats"]["triangles"], 24)
        rows = {r["name"]: r for r in summary["objects"]}
        self.assertEqual(rows["CrateA"]["location"], [1.0, 2.0, 0.0])
        self.assertEqual(rows["CrateA"]["components"], ["Spin"])
        self.assertIn("Spin", summary["component_types"])
        self.assertNotIn("scale", rows["CrateB"])  # defaults are left out to keep it compact
        self.assertNotIn("faces", rows["CrateB"])
        verbose = {r["name"]: r for r in self.client.result("scene_summary", {"verbose": True})["objects"]}
        self.assertEqual(verbose["CrateB"]["faces"], 6)

    def test_big_scenes_are_summarised(self):
        for i in range(12):
            helpers.new_object(f"Coin{i}", helpers.new_mesh(f"Coin{i}"), location=(i, 0, 0))
        summary = self.client.result("scene_summary", {"max_objects": 10})
        self.assertTrue(summary["objects_summarised"])
        self.assertNotIn("objects", summary)
        self.assertEqual(sum(g["objects"] for g in summary["by_collection"]), 15)
        self.assertLessEqual(len(summary["largest"]), 20)
        self.assertIn("list_objects", summary["hint"])
        page = self.client.result("list_objects", {"filter": {"name": "Coin*"}, "limit": 5})
        self.assertEqual((page["count"], len(page["objects"]), page["next_offset"]), (12, 5, 5))
        self.assertIn("offset 5", page["hint"])
        last = self.client.result("list_objects", {"filter": {"name": "Coin*"}, "limit": 5, "offset": 10})
        self.assertNotIn("next_offset", last)

    def test_list_objects_filters(self):
        self.assertEqual([o["name"] for o in self.client.result("list_objects", {"filter": {"type": "mesh"}})["objects"]],
                         ["CrateA", "CrateB"])
        self.assertEqual(self.client.result("list_objects", {"filter": {"name": "crate*"}})["count"], 2)
        self.assertEqual(self.client.result("list_objects", {"filter": {"component": "Spin"}})["objects"][0]["name"],
                         "CrateA")
        page = self.client.result("list_objects", {"limit": 1, "offset": 1})
        self.assertEqual((page["count"], len(page["objects"]), page["next_offset"]), (3, 1, 2))

    def test_get_object(self):
        info = self.client.result("get_object", {"name": "CrateA"})
        self.assertEqual(info["components"]["Spin"]["values"]["speed"], 2.0)
        self.assertEqual(info["world_bbox"]["min"], [0.0, 1.0, -1.0])
        self.assertEqual(info["mesh"]["triangles"], 12)
        by_id = self.client.result("get_object", {"name": ids.get_id(self.a)})
        self.assertEqual(by_id["name"], "CrateA")
        err = self.client.error("get_object", {"name": "CrateC"})
        self.assertIn("CrateA", err["data"]["hint"])

    def test_set_transform(self):
        done = self.client.result("set_transform", {"name": "CrateA", "location": [0, 0, 5], "rotation_deg": [0, 0, 90],
                                                    "scale": [2, 2, 2]})
        self.assertEqual(done["updated"][0]["rotation_deg"], [0.0, 0.0, 90.0])
        self.assertEqual(tuple(self.a.location), (0.0, 0.0, 5.0))
        self.client.result("set_transform", {"name": "CrateA", "location": [1, 0, 0], "relative": True})
        self.assertEqual(tuple(self.a.location), (1.0, 0.0, 5.0))
        self.client.result("set_transform", {"items": [{"name": "CrateA", "location": [0, 0, 0]},
                                                       {"name": "CrateB", "location": [0, 0, 1]}]})
        self.assertEqual(self.b.location.z, 1.0)
        err = self.client.error("set_transform", {"name": "CrateA"})
        self.assertEqual(err["code"], server.INVALID_PARAMS)

    def test_locked_objects_are_refused(self):
        self.b.gs.ai_lock = True
        err = self.client.error("set_transform", {"name": "CrateB", "location": [9, 9, 9]})
        self.assertIn("locked", err["message"])
        self.assertEqual(self.b.location.x, 4.0)
        self.client.error("delete_objects", {"names": ["CrateB"]})

    def test_delete_objects(self):
        child = helpers.new_object("Child", None, parent=self.b, location=(0, 0, 1))
        bpy.context.view_layer.update()
        world = child.matrix_world.translation.copy()
        self.client.result("delete_objects", {"names": ["CrateB"]})
        self.assertNotIn("CrateB", bpy.data.objects)
        child = bpy.data.objects["Child"]
        self.assertIsNone(child.parent)
        self.assertAlmostEqual((child.matrix_world.translation - world).length, 0.0, places=5)
        self.client.result("delete_objects", {"names": "CrateA", "with_children": True})
        self.assertNotIn("CrateA", bpy.data.objects)

    def test_run_python(self):
        out = self.client.result("run_python", {"code": "print('hi')\nlen(D.objects)"})
        self.assertEqual((out["ok"], out["result"], out["stdout"]), (True, 3, "hi\n"))
        out = self.client.result("run_python", {"code": "result = {'v': Vector((1, 2, 3)), 'o': D.objects['CrateA']}"})
        self.assertEqual(out["result"], {"v": [1.0, 2.0, 3.0], "o": {"type": "Object", "name": "CrateA"}})
        self.assertIn("components", self.client.result("run_python", {"code": "dir(gs.core)"})["result"])

    def test_run_python_errors_roll_back(self):
        out = self.client.result("run_python", {"code": "D.objects['CrateA'].location.x = 50\n1 / 0"})
        self.assertFalse(out["ok"])
        self.assertIn("ZeroDivisionError", out["error"])
        self.assertIn('File "<agent>", line 2', out["traceback"])
        self.assertTrue(out["rolled_back"])
        self.assertEqual(bpy.data.objects["CrateA"].location.x, 1.0)

        out = self.client.result("run_python", {"code": "D.objects['CrateA'].location.x = 50\n1 / 0",
                                                "rollback_on_error": False})
        self.assertFalse(out["rolled_back"])
        self.assertEqual(bpy.data.objects["CrateA"].location.x, 50.0)

        err = self.client.error("run_python", {"code": "def broken(:\n  pass"})
        self.assertIn("SyntaxError", err["message"])

    def test_run_python_timeout_and_system_exit(self):
        out = self.client.result("run_python", {"code": "while True:\n    pass", "timeout": 1})
        self.assertIn("Timed out", out["error"])
        out = self.client.result("run_python", {"code": "raise SystemExit(3)"})
        self.assertIn("SystemExit", out["error"])
        self.assertEqual(self.client.result("ping")["pong"], True)


# --- Assets -------------------------------------------------------------------------------------------


class AssetMethodsTest(BridgeTestCase):
    def test_kits_follow_the_contract(self):
        listed = self.client.result("list_kits")
        if not listed["kits"]:
            self.assertIn("run_python", listed["hint"])
            self.client.error("create_from_kit", {"kind": "crate"})
            return
        kit = next((k for k in listed["kits"] if k["kind"] == "crate"), listed["kits"][0])
        for key in ("kind", "description", "params"):
            self.assertIn(key, kit)
        self.assertIn("seed=", kit["params"])  # compact: names and defaults on one line
        full = self.client.result("list_kits", {"kind": kit["kind"]})
        self.assertEqual(full["params"]["type"], "object")
        made = self.client.result("create_from_kit", {"kind": kit["kind"], "location": [2, 0, 0], "name": "Made",
                                                      "collection": "Props"})
        self.assertEqual(made["root"], "Made")
        self.assertEqual(tuple(bpy.data.objects["Made"].location)[:1], (2.0,))
        self.assertIn("Made", bpy.data.collections["Props"].objects)
        info = self.client.result("get_object", {"name": "Made"})
        self.assertEqual(info["kit"]["kind"], kit["kind"])
        err = self.client.error("create_from_kit", {"kind": "no-such-kit"})
        self.assertIn(kit["kind"], err["data"]["hint"])

    def test_regenerate_kit(self):
        kits = [k for k in self.client.result("list_kits")["kits"] if k["kind"] == "crate"]
        if not kits:
            self.skipTest("no crate kit")
        self.client.result("create_from_kit", {"kind": "crate", "name": "Box"})
        components.add_component(bpy.data.objects["Box"], "Spin")
        out = self.client.result("regenerate_kit", {"object": "Box", "params": {"width": 2.0}})
        self.assertEqual(out["params"]["width"], 2.0)
        self.assertAlmostEqual(bpy.data.objects["Box"].dimensions.x, 2.0, places=3)
        self.assertEqual(components.list_components(bpy.data.objects["Box"]), ["Spin"])
        self.cube("Plain")
        self.assertIn("not made by a kit", self.client.error("regenerate_kit", {"object": "Plain", "params": {}})["message"])

    def test_import_asset(self):
        source = self.cube("Exported")
        path = self.root / "assets" / "box.glb"
        path.parent.mkdir()
        bpy.ops.export_scene.gltf(filepath=str(path), export_format="GLB", use_selection=True)
        bpy.data.objects.remove(source)
        out = self.client.result("import_asset", {"path": str(path), "location": [5, 0, 0], "name": "Imported"})
        self.assertEqual(out["roots"], ["Imported"])
        self.assertEqual(bpy.data.objects["Imported"].location.x, 5.0)
        self.assertIn("Imported", bpy.data.collections["box"].objects)
        self.assertIn("Unsupported", self.client.error("import_asset", {"path": str(self.root / "x.stl")})["message"])
        self.assertIn("does not exist", self.client.error("import_asset", {"path": str(self.root / "x.glb")})["message"])

    def test_import_asset_rigged_gltf_adds_no_bone_shape(self):
        rig = helpers.new_object("Rig", bpy.data.armatures.new("RigData"))
        bpy.context.view_layer.objects.active = rig
        bpy.ops.object.mode_set(mode="EDIT")
        root = rig.data.edit_bones.new("Root")
        root.head, root.tail = (0, 0, 0), (0, 0, 1)
        tip = rig.data.edit_bones.new("Tip")
        tip.head, tip.tail = (0, 0, 1), (0, 0, 2)
        tip.parent = root
        bpy.ops.object.mode_set(mode="OBJECT")
        skin = helpers.new_object("Skin", helpers.new_mesh("SkinMesh"), parent=rig)
        skin.modifiers.new("Armature", "ARMATURE").object = rig
        skin.vertex_groups.new(name="Root").add([0, 1, 2], 1.0, "REPLACE")
        skin.vertex_groups.new(name="Tip").add([3], 1.0, "REPLACE")
        path = self.root / "assets" / "rig.glb"
        path.parent.mkdir()
        bpy.ops.export_scene.gltf(filepath=str(path), export_format="GLB")
        for obj in (skin, rig):
            bpy.data.objects.remove(obj)

        out = self.client.result("import_asset", {"path": str(path)})
        made = [bpy.data.objects[c["name"]] for c in out["created"]]
        self.assertEqual(sorted(o.type for o in made), ["ARMATURE", "MESH"], out["created"])
        self.assertEqual(out["objects"], 2)
        armature = next(o for o in made if o.type == "ARMATURE")
        self.assertEqual(out["roots"], [armature.name])
        self.assertEqual(len(armature.pose.bones), 2)
        self.assertTrue(all(b.custom_shape is None for b in armature.pose.bones))
        self.assertFalse([o.name for o in bpy.data.objects if o.name.startswith("Icosphere")])
        self.assertNotIn("glTF_not_exported", bpy.data.collections)
        self.assertEqual(set(bpy.data.collections["rig"].objects), set(made))

    def test_apply_material(self):
        self.cube("A")
        self.cube("B")
        out = self.client.result("apply_material", {"objects": ["A", "B"], "preset": "dark_wood"})
        self.assertEqual(out["preset"], "wood_dark")
        self.assertEqual(bpy.data.objects["A"].material_slots[0].material.name, out["material"])
        out = self.client.result("apply_material", {"objects": "A", "params": {"base_color": "#FF0000", "roughness": 0.2}})
        mat = bpy.data.objects["A"].material_slots[0].material
        self.assertEqual(mat.name, out["material"])
        self.assertAlmostEqual(mat.diffuse_color[0], 1.0, places=3)
        self.assertNotEqual(bpy.data.objects["B"].material_slots[0].material, mat)
        err = self.client.error("apply_material", {"objects": "A", "preset": "lava-ish"})
        self.assertIn("preset", err["message"])
        err = self.client.error("apply_material", {"objects": "A", "params": {"shininess": 1}})
        self.assertIn("shininess", err["message"])

    def test_validate_assets(self):
        good = self.cube("Good")
        self.client.result("apply_material", {"objects": "Good", "preset": "stone"})
        bad = self.cube("Bad", (3, 0, 0))
        bad.scale = (2, 1, 1)
        bpy.context.view_layer.objects.active = bad
        bpy.ops.object.mode_set(mode="EDIT")
        bpy.ops.mesh.select_all(action="SELECT")
        bpy.ops.mesh.flip_normals()
        bpy.ops.object.mode_set(mode="OBJECT")
        bad.data.uv_layers.remove(bad.data.uv_layers[0])
        compact = self.client.result("validate_assets", {"names": ["Good", "Bad"]})
        self.assertEqual([o["name"] for o in compact["objects"]], ["Bad"])
        self.assertIn("1 object(s) without", compact["clean"])
        report = self.client.result("validate_assets", {"names": ["Good", "Bad"], "max_tris": 10, "verbose": True})
        by_name = {o["name"]: o for o in report["objects"]}
        self.assertFalse(report["ok"])
        good_checks = {i["check"] for i in by_name["Good"]["issues"]}
        self.assertEqual(good_checks, {"poly_count"})  # 12 triangles > budget 10
        bad_checks = {i["check"]: i for i in by_name["Bad"]["issues"]}
        self.assertEqual(bad_checks["normals"]["severity"], "error")
        self.assertIn("Recalculate", bad_checks["normals"]["fix"])
        self.assertIn("transform", bad_checks)
        self.assertIn("uvs", bad_checks)
        self.assertIn("materials", bad_checks)
        self.assertEqual(by_name["Good"]["origin"]["origin"], "center")
        self.assertEqual(by_name["Bad"]["mesh"]["triangles"], 12)

    def _png_size(self, out):
        png = base64.b64decode(out["png"])
        self.assertEqual(png[:8], b"\x89PNG\r\n\x1a\n")
        return int.from_bytes(png[16:20], "big"), int.from_bytes(png[20:24], "big")

    def test_render_views_contact_sheet(self):
        self.cube("Box")
        out = self.client.result("render_views", {"target": "Box", "size": 256}, timeout=120)
        self.assertEqual(out["backend"], "offscreen")  # gpu.init() makes offscreen drawing work in --background
        self.assertEqual(out["layout"], [["front", "side"], ["top", "iso"]])
        self.assertEqual(self._png_size(out), (256, 256))
        self.assertEqual(out["bounds"]["size_m"], [2.0, 2.0, 2.0])
        self.assertTrue(out["person"])
        front = out["views"][0]
        self.assertEqual(front["axes"], {"right": "x", "up": "z"})
        self.assertGreater(front["grid_m"], 0)
        self.assertNotIn("note", out)  # labels were drawn
        self.assertEqual(len(bpy.data.scenes), 1, "the temporary scenes are removed")
        self.assertFalse([o for o in bpy.data.objects if o.name.startswith("GS_Agent")])
        self.assertFalse([m for m in bpy.data.meshes if m.name.startswith("GS_Agent")])
        self.assertFalse([m for m in bpy.data.materials if m.name.startswith("GS_Agent")])

        workbench = self.client.result("render_views", {"target": ["Box"], "views": ["top", "front"], "size": 256,
                                                        "backend": "workbench", "person": False}, timeout=120)
        self.assertEqual(workbench["backend"], "workbench")
        self.assertEqual(self._png_size(workbench), (256, 128))
        self.assertFalse(workbench["person"])

    def test_render_views_options(self):
        self.cube("Box")
        self.cube("Other", (4, 0, 0))
        cam = helpers.new_object("Cam", bpy.data.cameras.new("Cam"), location=(0, -10, 3))
        cam.rotation_euler = (1.4, 0, 0)
        bpy.context.scene.camera = cam
        focus = self.client.result("render_views", {"target": "Box", "focus": True, "views": ["iso"], "size": 128,
                                                    "shading": "clay"}, timeout=120)
        self.assertIn("ghosted", focus["legend"])
        self.assertEqual(focus["shading"], "clay")
        self.assertEqual(self._png_size(focus), (128, 128))
        cam_view = self.client.result("render_views", {"views": ["camera"], "size": 128, "labels": False}, timeout=120)
        self.assertEqual(cam_view["layout"], [["camera"]])
        err = self.client.error("render_views", {"views": ["diagonal"]})
        self.assertIn("Unknown view", err["message"])
        self.assertIn("shading", self.client.error("render_views", {"shading": "toon"})["message"])

    def test_render_views_levels_default_to_top_and_iso(self):
        bpy.ops.mesh.primitive_plane_add(size=40)
        self.cube("Box")
        out = self.client.result("render_views", {"size": 256}, timeout=120)
        self.assertEqual(out["layout"], [["top", "iso"]])

    def test_render_views_compare_with_a_checkpoint(self):
        self.save_scene()
        self.cube("Box")
        before = self.client.result("checkpoint", {"label": "one box"})
        self.cube("Box2", (3, 0, 0))
        out = self.client.result("render_views", {"compare": before["id"], "size": 256}, timeout=120)
        self.assertEqual(out["layout"], [["iso before", "iso now"], ["top before", "top now"]])
        self.assertEqual(out["compare"]["label"], "one box")
        self.assertEqual(self._png_size(out), (256, 256))
        # The checkpoint's data was appended for the call and removed again.
        self.assertEqual(len(bpy.data.scenes), 1)
        self.assertEqual(sorted(o.name for o in bpy.data.objects), ["Box", "Box2"])
        err = self.client.error("render_views", {"compare": "nope"})
        self.assertIn("list_checkpoints", err["data"]["hint"])


# --- Components and game ---------------------------------------------------------------------------------


class ComponentMethodsTest(BridgeTestCase):
    def setUp(self):
        super().setUp()
        self.obj = self.cube("Thing")
        self.other = self.cube("Other", (3, 0, 0))

    def test_list_component_types(self):
        out = self.client.result("list_component_types")
        self.assertEqual(out["count"], len(helpers.fixture_registry()["components"]))
        spin = next(c for c in out["components"] if c["name"] == "Spin")
        self.assertTrue(spin["fields"]["speed"].startswith("float = 1"))
        raw = self.client.result("list_component_types", {"verbose": True})
        self.assertIn("default", raw["components"][0]["fields"][0])

    def test_add_set_remove(self):
        out = self.client.result("add_component", {"objects": ["Thing", "Other"], "component": "Spin",
                                                   "values": {"speed": 4}})
        self.assertEqual([a["values"]["speed"] for a in out["added"]], [4.0, 4.0])
        self.assertTrue(ids.get_id(self.obj))
        out = self.client.result("set_component", {"objects": "Thing", "component": "Spin", "values": {"speed": 1.5}})
        self.assertEqual(out["updated"][0]["values"]["speed"], 1.5)
        self.assertEqual(components.get_values(self.other, "Spin")["speed"], 4.0)
        self.client.result("remove_component", {"objects": "Thing", "component": "Spin"})
        self.assertEqual(components.list_components(self.obj), [])

    def test_validation_errors(self):
        err = self.client.error("add_component", {"objects": "Thing", "component": "Spinn"})
        self.assertIn("Did you mean 'Spin'", err["message"])
        err = self.client.error("add_component", {"objects": "Thing", "component": "Spin", "values": {"speed": "fast"}})
        self.assertIn("speed", err["message"])
        self.assertEqual(components.list_components(self.obj), [], "nothing changed after a failed call")
        err = self.client.error("set_component", {"objects": "Thing", "component": "Spin", "values": {"speed": 1}})
        self.assertIn("add_if_missing", err["data"]["hint"])
        self.client.result("set_component", {"objects": "Thing", "component": "Spin", "values": {"speed": 1},
                                             "add_if_missing": True})
        self.assertIn("Attached", self.client.error("remove_component", {"objects": "Other",
                                                                         "component": "Spin"})["data"]["hint"])


class PrefabAndClipMethodsTest(BridgeTestCase):
    def test_set_prefab(self):
        from gamestudio.core import export

        coll = bpy.data.collections.new("Coin")
        bpy.context.scene.collection.children.link(coll)
        coll.objects.link(bpy.data.objects.new("CoinMesh", helpers.new_mesh("CoinMesh")))
        inst = helpers.new_object("Coin.001", None, location=(2, 0, 0))
        inst.instance_type = "COLLECTION"
        inst.instance_collection = coll
        api.undo_push("test: setup")
        out = self.client.result("set_prefab", {"collection": "Coin"})
        self.assertTrue(out["prefab"])
        self.assertEqual(out["objects"], ["CoinMesh"])
        self.assertEqual(out["instances"], ["Coin.001"])
        self.assertTrue(export.is_prefab(coll))
        self.assertEqual(self.client.result("scene_summary")["prefabs"], ["Coin"])
        self.assertEqual(self.client.result("get_object", {"name": "Coin.001"})["prefab_instance_of"], "Coin")
        self.assertEqual(self.client.result("get_object", {"name": "CoinMesh"})["in_prefab"], ["Coin"])
        self.client.result("undo")
        self.assertFalse(export.is_prefab(bpy.data.collections["Coin"]))
        self.client.result("set_prefab", {"collection": "Coin", "enabled": False})
        self.assertIn("Coin", self.client.error("set_prefab", {"collection": "Coins"})["data"]["hint"])

    def test_list_clips(self):
        bpy.context.scene.render.fps = 24
        hero = self.cube("Hero")
        hero.keyframe_insert("location", frame=1)
        hero.location.z = 1
        hero.keyframe_insert("location", frame=25)
        idle = hero.animation_data.action
        idle.name = "Idle"
        walk = bpy.data.actions.new("Walk")
        jump = bpy.data.actions.new("Jump")
        wave = bpy.data.actions.new("Wave")
        track = hero.animation_data.nla_tracks.new()
        track.name = "walk"
        track.strips.new("Walk", 1, walk)
        busy = hero.animation_data.nla_tracks.new()
        busy.name = "busy"
        busy.strips.new("Jump", 1, jump)
        busy.strips.new("Wave", 40, wave)
        self.cube("Static", (3, 0, 0))

        out = self.client.result("list_clips")
        self.assertEqual([o["object"] for o in out["objects"]], ["Hero"])
        hero_info = out["objects"][0]
        self.assertEqual(hero_info["action"], "Idle")
        clips = {c["name"]: c for c in hero_info["clips"]}
        self.assertEqual(set(clips), {"Idle", "Walk"})
        self.assertEqual(clips["Idle"]["seconds"], 1.0)
        self.assertEqual(clips["Walk"]["from"], "NLA track 'walk'")
        self.assertIn("busy", hero_info["warnings"][0])
        self.assertEqual([t["name"] for t in hero_info["nla_tracks"]], ["walk", "busy"])
        self.assertEqual(self.client.result("get_object", {"name": "Hero"})["animation"]["action"], "Idle")
        static = self.client.result("list_clips", {"objects": ["Static"]})["objects"][0]
        self.assertEqual(static["clips"], [])


class DogfoodFixesTest(BridgeTestCase):
    """Friction found when an agent built a whole game through the tools (.dev/dogfood/FRICTION.md)."""

    def test_validate_assets_checks_everything_by_default_and_says_so(self):
        for i in range(3):
            self.cube(f"Box{i}", (i * 3, 0, 1))
        bpy.ops.object.select_all(action="DESELECT")
        bpy.data.objects["Box0"].select_set(True)
        out = self.client.result("validate_assets")
        self.assertEqual(out["checked"], 3)
        self.assertTrue(out["scope"].startswith("all: 3"))
        sel = self.client.result("validate_assets", {"names": "selection"})
        self.assertEqual((sel["checked"], sel["scope"]), (1, "selection: 1 object(s)"))

    def test_validate_assets_summary_for_big_checks(self):
        for i in range(30):
            obj = self.cube(f"Wide{i}", (i * 3, 0, 1))
            obj.scale = (2, 1, 1)  # an unapplied-scale warning each
        out = self.client.result("validate_assets")
        self.assertIn("transform", out["by_check"])
        self.assertEqual(out["by_check"]["transform"]["warning"], 30)
        self.assertEqual(len(out["worst"]), 10)
        self.assertNotIn("objects", out)
        full = self.client.result("validate_assets", {"summary": False})
        self.assertEqual(len(full["objects"]), 30)

    def test_references_emptied_by_a_delete_are_flagged_and_unset_ones_are_not(self):
        player = self.cube("Player")
        cam = helpers.new_object("Camera", bpy.data.cameras.new("Camera"), location=(0, -8, 4))
        components.add_component(cam, "FollowCamera", {"target": "Player"})
        spare = helpers.new_object("Spare", bpy.data.cameras.new("Spare"), location=(0, 8, 4))
        components.add_component(spare, "FollowCamera")  # target never set (null): an ordinary empty reference
        api.undo_push("test: setup")
        def references(report):
            return [(o["name"], i) for o in report["objects"] for i in o["issues"] if i["check"] == "references"]

        self.assertEqual(references(self.client.result("validate_assets")), [])
        out = self.client.result("delete_objects", {"names": ["Player"]})
        self.assertEqual(out["cleared_references"],
                         [{"object": "Camera", "component": "FollowCamera", "field": "target", "was": "Player"}])
        [(name, issue)] = references(self.client.result("validate_assets"))
        self.assertEqual(name, "Camera")
        self.assertIn("FollowCamera.target", issue["message"])
        self.assertIn("'Player'", issue["message"])
        self.assertIn("set_component", issue["fix"])
        # Undo brings the target back, and the field with it: nothing dangles.
        self.client.result("undo")
        self.assertEqual(references(self.client.result("validate_assets")), [])
        self.client.result("delete_objects", {"names": ["Player"]})
        # Left empty on purpose (set_component null), it is an ordinary empty reference again.
        self.client.result("set_component", {"objects": "Camera", "component": "FollowCamera",
                                             "values": {"target": None}})
        self.assertEqual(references(self.client.result("validate_assets")), [])
        self.assertNotIn("gs_cleared_refs", bpy.data.objects["Camera"])
        # A field pointing at an object outside the scene still dangles.
        elsewhere = bpy.data.objects.new("Elsewhere", None)  # in no scene
        components.set_field(bpy.data.objects["Spare"], "FollowCamera", "target", elsewhere)  # undo renewed Spare
        [(name, issue)] = references(self.client.result("validate_assets"))
        self.assertEqual(name, "Spare")
        self.assertIn("not in this scene", issue["message"])
        del player, spare

    def test_snap_to_ground_ground_height_and_raycast(self):
        bpy.ops.mesh.primitive_plane_add(size=20, location=(0, 0, 0))
        ramp = self.cube("Step", (4, 0, 0.5), size=1.0)  # a 1 m block: its top is at z=1
        crate = self.cube("Crate", (0, 0, 7), size=1.0)
        out = self.client.result("set_transform", {"name": "Crate", "snap_to_ground": True})
        self.assertAlmostEqual(bpy.data.objects["Crate"].matrix_world.translation.z, 0.5, places=4)
        self.assertEqual(out["updated"][0]["snap"]["on"], "Plane")
        self.client.result("set_transform", {"items": [{"name": "Crate", "location": [4, 0, 3], "snap_to_ground": True}]})
        self.assertAlmostEqual(bpy.data.objects["Crate"].matrix_world.translation.z, 1.5, places=4)  # on the step
        heights = self.client.result("ground_height", {"points": [[0, 0], [4, 0], [50, 50]]})["points"]
        self.assertEqual([h["z"] for h in heights], [0.0, 2.0, None])  # the crate now sits on the step
        self.assertEqual(heights[1]["object"], "Crate")
        seen_through = self.client.result("ground_height", {"points": [[4, 0]], "ignore": ["Crate"]})["points"][0]
        self.assertEqual((seen_through["z"], seen_through["object"]), (1.0, "Step"))
        hit = self.client.result("raycast", {"origin": [-5, 0, 0.5], "direction": [1, 0, 0]})
        self.assertEqual(hit["object"], "Step")
        self.assertAlmostEqual(hit["location"][0], 3.5, places=3)
        self.assertFalse(self.client.result("raycast", {"origin": [0, 0, 5], "direction": [0, 0, 1]})["hit"])
        del ramp, crate

    def test_batch_add_components_and_one_component_type(self):
        self.cube("Hero")
        out = self.client.result("add_component", {"objects": "Hero", "components": {"RigidBody": {"type": "kinematic"},
                                                                                      "Collider": {},
                                                                                      "CharacterController": {}}})
        self.assertEqual(out["components"], ["RigidBody", "Collider", "CharacterController"])
        self.assertEqual(components.list_components(bpy.data.objects["Hero"]),
                         ["RigidBody", "Collider", "CharacterController"])
        err = self.client.error("add_component", {"objects": "Hero", "components": {"Spin": {"speed": "fast"}}})
        self.assertIn("speed", err["message"])
        one = self.client.result("list_component_types", {"type": "Spin"})
        self.assertEqual(one["name"], "Spin")
        self.assertIn("default", one["fields"][0])

    def test_run_python_read_only(self):
        self.cube("Box")
        api.undo_push("test: setup")
        out = self.client.result("run_python", {"code": "len(D.objects)", "read_only": True})
        self.assertEqual((out["ok"], out["result"], out["undo"]), (True, 1, False))
        out = self.client.result("run_python", {"code": "D.objects['Box'].location.x = 5", "read_only": True})
        self.assertFalse(out["ok"])
        self.assertIn("object 'Box' changed", out["error"])
        self.assertTrue(out["rolled_back"])
        self.assertEqual(bpy.data.objects["Box"].location.x, 0.0)

    def test_create_from_kit_names_every_object(self):
        kinds = {k["kind"] for k in self.client.result("list_kits")["kits"]}
        if "character_rigged" not in kinds:
            self.skipTest("no character_rigged kit")
        made = self.client.result("create_from_kit", {"kind": "character_rigged", "name": "Hero"}, timeout=120)
        self.assertEqual(made["root"], "Hero")
        self.assertEqual(sorted(o["name"] for o in made["created"]), ["Hero", "Hero_Mesh"])

    def test_kits_batch_trimmed_schema_and_regenerate_name(self):
        catalog = self.client.result("list_kits")["kits"]
        kinds = {k["kind"] for k in catalog}
        if not {"crate", "rock"} <= kinds:
            self.skipTest("kits not available")
        made = self.client.result("create_from_kit", {"items": [{"kind": "crate", "location": [0, 0, 0], "name": "C1"},
                                                                {"kind": "rock", "location": [3, 0, 0]}],
                                                      "collection": "Props"})
        self.assertEqual(made["created"], 2)
        self.assertEqual(made["items"][0]["root"], "C1")
        self.assertIn(made["items"][1]["root"], bpy.data.collections["Props"].objects)
        err = self.client.error("create_from_kit", {"items": [{"kind": "crate"}, {"kind": "nope"}]})
        self.assertIn("items[1]", err["message"])
        crate = self.client.result("list_kits", {"kind": "crate"})
        enums = [v.get("enum") for v in crate["params"]["properties"].values() if "material" in v.get("description", "")]
        self.assertTrue(all(e is None for e in enums))  # the 27 presets are listed once, in material_presets
        self.assertIn("wood", crate["material_presets"])
        out = self.client.result("regenerate_kit", {"object": "C1", "params": {"width": 1.2}})
        self.assertEqual(out["params"]["name"], "C1")


class Dogfood2FixesTest(BridgeTestCase):
    """Friction from the second dogfood run (.dev/dogfood2/FRICTION.md)."""

    def slope(self, degrees=20.0, size=40.0):
        bpy.ops.mesh.primitive_plane_add(size=size, location=(0, 0, 0))
        plane = bpy.context.active_object
        plane.name = "Slope"
        plane.rotation_euler = (0, math.radians(degrees), 0)  # rises towards -x
        bpy.context.view_layer.update()
        return plane

    def test_create_from_kit_checks_every_item_before_building(self):
        kinds = {k["kind"] for k in self.client.result("list_kits")["kits"]}
        if not {"crate", "terrain"} <= kinds:
            self.skipTest("kits not available")
        before = set(bpy.data.objects)
        err = self.client.error("create_from_kit", {"items": [{"kind": "crate", "name": "C1"},
                                                              {"kind": "terrain", "params": {"size": 240}}]})
        self.assertIn("items[1]", err["message"])
        self.assertIn("terrain.size", err["message"])
        self.assertIn("Nothing was built", err["message"])
        self.assertEqual(set(bpy.data.objects), before)
        err = self.client.error("create_from_kit", {"items": [{"kind": "crate", "params": {"colour": "red"}}]})
        self.assertIn("unknown param", err["message"])
        self.assertEqual(set(bpy.data.objects), before)

    def test_snap_to_ground_on_a_slope(self):
        self.slope(20)
        box = self.cube("Rock", (0, 0, 6), size=1.0)
        box.scale = (3, 3, 1)  # 3 m wide, 1 m tall
        drop = 3 * 0.95 * math.tan(math.radians(20))  # measured at the contacts, 5 % in towards the centre
        out = self.client.result("set_transform", {"name": "Rock", "snap_to_ground": True})["updated"][0]["snap"]
        self.assertEqual((out["align"], out["on"]), ("lowest", "Slope"))
        self.assertAlmostEqual(out["float_m"], 0.0, places=3)  # nothing floats ...
        self.assertAlmostEqual(out["sink_m"], drop, delta=0.02)  # ... the uphill side is in the ground
        self.assertIn("uneven", out["hint"])
        high = self.client.result("set_transform", {"name": "Rock", "snap_to_ground": True,
                                                    "align": "highest"})["updated"][0]["snap"]
        self.assertAlmostEqual(high["float_m"], drop, delta=0.02)
        self.assertAlmostEqual(high["sink_m"], 0.0, places=3)
        tilted = self.client.result("set_transform", {"items": [{"name": "Rock", "snap_to_ground": True,
                                                                  "tilt": True}]})["updated"][0]["snap"]
        self.assertAlmostEqual(tilted["tilt_deg"], 20.0, delta=0.5)
        self.assertLess(tilted["float_m"], 0.02)
        self.assertLess(tilted["sink_m"], 0.02)
        self.assertNotIn("hint", tilted)
        err = self.client.error("set_transform", {"name": "Rock", "snap_to_ground": True, "align": "middle"})
        self.assertIn("align", err["message"])

    def test_snap_uses_the_underside_not_the_canopy(self):
        bpy.ops.mesh.primitive_plane_add(size=20, location=(0, 0, 0))
        bpy.ops.mesh.primitive_cube_add(size=2, location=(1.4, 0, 3))  # a ledge under the canopy's edge
        bpy.context.active_object.name = "Ledge"
        trunk = self.cube("Trunk", (0, 0, 5), size=0.4)
        canopy = self.cube("Canopy", (0, 0, 7), size=3.0)
        canopy.parent = trunk
        canopy.matrix_parent_inverse = trunk.matrix_world.inverted()
        bpy.context.view_layer.update()
        out = self.client.result("set_transform", {"name": "Trunk", "snap_to_ground": True})["updated"][0]["snap"]
        self.assertEqual(out["on"], "Plane")  # the trunk base decides; the ledge under the canopy does not
        self.assertAlmostEqual(trunk.matrix_world.translation.z, 0.2, places=3)

    def test_rigged_and_grouped_roots_report_what_they_carry(self):
        group = helpers.new_object("Group", None, location=(5, 0, 0))
        child = self.cube("Part", (5, 0, 1), size=2.0)
        child.parent = group
        child.matrix_parent_inverse = group.matrix_world.inverted()
        bpy.context.view_layer.update()
        info = self.client.result("get_object", {"name": "Group"})
        self.assertEqual((info["dimensions"], info["dimensions_from"]), ([2.0, 2.0, 2.0], "children"))
        self.assertEqual(info["world_bbox"]["min"], [4.0, -1.0, 0.0])
        row = next(r for r in self.client.result("scene_summary")["objects"] if r["name"] == "Group")
        self.assertEqual(row["dimensions"], [2.0, 2.0, 2.0])
        kinds = {k["kind"] for k in self.client.result("list_kits")["kits"]}
        if "character_rigged" not in kinds:
            return
        made = self.client.result("create_from_kit", {"items": [{"kind": "character_rigged", "name": "Hero"}]},
                                  timeout=120)
        self.assertGreater(made["items"][0]["dimensions"][2], 1.0)  # the character's height, not 0
        hero = self.client.result("get_object", {"name": "Hero"})
        self.assertEqual(hero["dimensions_from"], "children")
        self.assertGreater(hero["dimensions"][2], 1.0)

    def test_export_revisions_never_repeat(self):
        bpy.context.scene.name = "main"
        self.cube("Thing")
        self.save_scene()
        first = self.client.result("export_scene", timeout=120)
        self.assertEqual(first["tier"], "full")
        first = first["rev"]
        unchanged = self.client.result("export_scene", timeout=120)
        self.assertEqual((unchanged["tier"], unchanged["rev"]), ("reuse", first))  # the same rev for the same files
        force = {"force": True}  # a new export each time, as before the journal
        second = self.client.result("export_scene", force, timeout=120)["rev"]
        bpy.context.scene["gs_rev"] = first - 1  # what undo or restore_checkpoint brings back
        third = self.client.result("export_scene", force, timeout=120)["rev"]
        self.assertEqual((second, third), (first + 1, first + 2))
        checkpoint = self.client.result("checkpoint", {"label": "old"})["id"]
        self.client.result("export_scene", force, timeout=120)
        self.client.result("restore_checkpoint", {"id": checkpoint, "backup": False}, timeout=120)
        self.assertGreater(self.client.result("export_scene", timeout=120)["rev"], first + 3)

    def test_apply_material_to_a_list_of_slots(self):
        floor = self.cube("Floor")
        for _ in range(4):
            floor.data.materials.append(None)
        out = self.client.result("apply_material", {"objects": "Floor", "preset": "metal", "slot": [0, 2]})
        self.assertEqual([m is not None for m in out["slots"]["Floor"]], [True, False, True, False])
        err = self.client.error("apply_material", {"objects": "Floor", "preset": "metal", "slot": []})
        self.assertIn("list", err["message"])

    def test_the_first_change_after_a_load_can_be_undone(self):
        # No undo_push of its own here: in --background a file load leaves the undo history empty, and the
        # bridge records the base step itself before the first change.
        self.cube("Box")
        path = self.save_scene()
        self.client.result("open_scene", {"path": str(path)})
        err = self.client.error("undo")
        self.assertIn("restore_checkpoint", err["data"]["hint"])
        self.client.result("set_transform", {"name": "Box", "location": [5, 0, 0]})
        self.assertEqual(self.client.result("undo"), {"undone": 1})
        self.assertEqual(bpy.data.objects["Box"].location.x, 0.0)
        self.client.result("redo")
        self.assertEqual(bpy.data.objects["Box"].location.x, 5.0)
        self.client.result("save", {"path": str(path.with_name("copy.blend"))})  # Save As keeps the history
        self.client.result("set_transform", {"name": "Box", "location": [7, 0, 0]})
        self.assertEqual(self.client.result("undo", {"steps": 5})["undone"], 2)
        self.assertEqual(bpy.data.objects["Box"].location.x, 0.0)

    def test_export_all_levels(self):
        self.cube("Thing")
        self.save_scene("main")
        bpy.ops.wm.save_as_mainfile(filepath=str(self.root / "scenes" / "level2.blend"), copy=True)
        out = self.client.result("export_all", timeout=300)
        self.assertEqual(out["errors"], [])
        self.assertEqual(sorted(s["scene"] for s in out["scenes"]), ["level2", "main"])
        self.assertEqual(sorted((s["file"], s["scene"]) for s in out["scenes"]),
                         [("scenes/level2.blend", "level2"), ("scenes/main.blend", "main")])
        self.assertEqual({m["scene"]: m["url"] for m in out["scene_loads"]},
                         {"main": "/__gs/export/main.scene.json", "level2": "/__gs/export/level2.scene.json"})
        self.assertTrue((self.root / ".gamestudio" / "export" / "level2.scene.json").is_file())
        self.assertEqual(pathlib.Path(bpy.data.filepath).name, "main.blend")

    def test_render_views_labels_a_chosen_list(self):
        for i, name in enumerate(("Player", "Key_1", "Tree")):
            self.cube(name, (i * 3, 0, 1))
        out = self.client.result("render_views", {"views": ["top"], "names": ["Player", "Key_1"], "size": 256},
                                 timeout=120)
        self.assertEqual(out["labelled"], ["Key_1", "Player"])
        out = self.client.result("render_views", {"target": "Tree", "views": ["top"], "labels": ["Player"],
                                                  "size": 256}, timeout=120)
        self.assertEqual(out.get("not_in_view"), ["Player"])  # framed on the tree: the player is off the tile
        err = self.client.error("render_views", {"names": ["Ghost"]})
        self.assertIn("Ghost", err["message"])


class ProceduralToolsTest(BridgeTestCase):
    """create_procedural / set_procedural over gamestudio.procedural, and how the other tools see its objects."""

    def setUp(self):
        super().setUp()
        bpy.context.scene.name = "main"
        try:
            from gamestudio import kits, procedural  # noqa: F401
        except ImportError:
            self.skipTest("no procedural toolkit")
        self.terrain = kits.create("terrain", {"size": 36, "resolution": 18, "height": 1.5, "seed": 2},
                                   collection="Level")[0]
        self.cube("Shrub", (60, 0, 0.5), size=1.0)

    def scatter(self, density=0.1):
        return self.client.result("create_procedural", {
            "kind": "scatter", "name": "Forest",
            "params": {"surface": self.terrain.name, "sources": ["Shrub"], "density": density, "seed": 1}}, timeout=180)

    def test_list_kits_lists_the_builders(self):
        out = self.client.result("list_kits")
        builders = {b["kind"]: b for b in out["procedural"]}
        self.assertEqual(set(builders), {"scatter", "array", "fence", "road", "river", "cliff", "rock_wall", "building"})
        self.assertEqual(builders["scatter"]["needs"], ["surface", "sources"])
        self.assertLess(len(json.dumps(out["procedural"])), 3000)  # compact; the schemas come per kind
        road = self.client.result("list_kits", {"kind": "road"})
        self.assertTrue(road["procedural"])
        self.assertIn("curve", road["params"]["properties"])
        fence = self.client.result("list_kits", {"kind": "fence"})  # a kit and a builder share the name
        self.assertIn("procedural_builder", fence)
        self.assertEqual(fence["procedural_builder"]["kind"], "fence")

    def test_create_and_set_are_one_undo_step_each(self):
        api.undo_push("test: setup")
        made = self.scatter()
        self.assertEqual((made["object"], made["kind"], made["collection"]), ("Forest", "scatter", "Scatter"))
        self.assertEqual(made["params"]["surface"], self.terrain.name)
        self.assertGreater(made["stats"]["instances"], 3)
        changed = self.client.result("set_procedural", {"object": "Forest", "params": {"density": 0.02}})
        self.assertAlmostEqual(changed["params"]["density"], 0.02, places=5)
        self.assertLess(changed["stats"]["instances"], made["stats"]["instances"])
        self.client.result("undo")
        bpy.context.view_layer.update()
        from gamestudio import procedural

        self.assertAlmostEqual(procedural.params(bpy.data.objects["Forest"])["density"], 0.1, places=5)
        self.client.result("undo")
        self.assertNotIn("Forest", bpy.data.objects)

    def test_bad_requests_build_nothing(self):
        before = set(bpy.data.objects)
        self.assertIn("Builders:", self.client.error("create_procedural", {"kind": "forest"})["data"]["hint"])
        self.assertIn("needs surface", self.client.error("create_procedural", {"kind": "scatter",
                                                                               "params": {"sources": ["Shrub"]}})["message"])
        self.assertIn("unknown param", self.client.error("create_procedural", {"kind": "road", "params": {
            "curve": [[0, 0], [5, 0]], "lanes": 4}})["message"])
        self.assertIn("density", self.client.error("create_procedural", {"kind": "scatter", "params": {
            "surface": self.terrain.name, "sources": ["Shrub"], "density": "lots"}})["message"])
        self.assertEqual(set(bpy.data.objects), before)
        self.assertIn("procedural", self.client.error("set_procedural", {"object": "Shrub",
                                                                         "params": {"density": 1}})["message"])

    def test_summary_and_get_object_measure_what_the_nodes_make(self):
        made = self.scatter()
        self.client.result("create_procedural", {"kind": "road", "name": "MainRoad", "params": {
            "curve": [[-14, -10], [0, 0], [14, 9]], "terrain": self.terrain.name}}, timeout=180)
        summary = self.client.result("scene_summary")
        rows = {r["name"]: r for r in summary["objects"]}
        forest = rows["Forest"]
        self.assertEqual((forest["dimensions_from"], forest["instances"]), ("instances", made["stats"]["instances"]))
        self.assertGreater(forest["dimensions"][0], 5.0)  # spread over the terrain, not 0 x 0 x 0
        self.assertEqual(rows["MainRoad"]["dimensions_from"], "evaluated")
        self.assertGreater(rows["MainRoad"]["dimensions"][0], 20.0)
        self.assertEqual(summary["stats"]["instanced_triangles"], made["stats"]["triangles"])
        self.assertGreater(summary["stats"]["triangles"], 100)  # the road's evaluated mesh counts
        info = self.client.result("get_object", {"name": "Forest"})
        self.assertEqual((info["dimensions_from"], info["instances"]), ("instances", made["stats"]["instances"]))
        self.assertEqual(info["instanced_triangles"], made["stats"]["triangles"])
        listed = self.client.result("list_objects", {"filter": {"name": "Forest"}})["objects"][0]
        self.assertEqual(listed["dimensions"], forest["dimensions"])

    def test_validate_and_twin_see_what_the_nodes_make(self):
        self.scatter()
        self.client.result("create_procedural", {"kind": "road", "name": "MainRoad", "params": {
            "curve": [[-14, -10], [0, 0], [14, 9]], "terrain": self.terrain.name}}, timeout=180)
        report = self.client.result("validate_assets", {"names": ["MainRoad", "Forest"], "verbose": True})
        for row in report["objects"]:
            with self.subTest(row["name"]):
                messages = [i["message"] for i in row["issues"]]
                self.assertFalse([m for m in messages if m.startswith(("No UV map", "No material"))], messages)
        road = next(r for r in report["objects"] if r["name"] == "MainRoad")
        self.assertIn("UVMap", road["mesh"]["uv_layers"])
        text = self.client.result("scene_twin")["text"]
        line = next(json.loads(l.rstrip(",")) for l in text.splitlines() if '"name":"MainRoad"' in l.replace(" ", ""))
        self.assertEqual(line["procedural"]["kind"], "road")
        self.assertEqual(line["procedural"]["inputs"]["terrain"], self.terrain.name)
        forest = next(json.loads(l.rstrip(",")) for l in text.splitlines() if '"name":"Forest"' in l.replace(" ", ""))
        self.assertEqual(forest["procedural"]["inputs"]["surface"], self.terrain.name)
        self.assertNotIn("gs_procedural", forest.get("props", {}))


class CompareReasonAndPoseTest(BridgeTestCase):
    def setUp(self):
        super().setUp()
        bpy.context.scene.name = "main"
        self.cube("Box")

    def test_verdict_reason_names_the_deciding_metric(self):
        from gamestudio.bridge import compare

        reason, failed = compare.verdict_reason(0.095, 2.26, "different")
        self.assertEqual(failed, ["detail_ratio"])
        self.assertIn("detail_ratio 2.26 is outside 0.5-2", reason)
        self.assertIn("normalized_diff 0.095 alone would allow close", reason)
        reason, failed = compare.verdict_reason(0.2, 1.0, "close")
        self.assertEqual(failed, ["normalized_diff"])
        self.assertTrue(reason.startswith("close, not match: normalized_diff 0.2 is above 0.12"))
        self.assertEqual(compare.verdict_reason(0.05, 1.0, "match")[1], [])

    def test_posed_camera_for_compare(self):
        self.client.result("export_scene", timeout=120)
        before = set(bpy.data.objects)
        view = self.client.result("render_camera_view", {"size": [96, 64], "engine": "workbench",
                                                         "camera": {"from": [0, -8, 2], "at": [0, 0, 0]}},
                                  timeout=180)
        pose = view["request"]["pose"]
        self.assertEqual((view["camera"], view["camera_id"]), ("(posed)", None))
        # Blender (0, -8, 2) is runtime (0, 2, 8); the vertical fov of a 35 mm lens at 3:2.
        self.assertEqual([round(v, 3) for v in pose["position"]], [0.0, 2.0, 8.0])
        self.assertAlmostEqual(pose["fov_deg"], math.degrees(2 * math.atan(math.tan(math.atan(18 / 35)) * 64 / 96)),
                               places=3)
        self.assertEqual(set(bpy.data.objects), before)  # the temporary camera is gone
        iso = self.client.result("render_camera_view", {"size": [96, 64], "engine": "workbench", "view": "iso"},
                                 timeout=180)
        self.assertEqual(iso["pose"]["label"], "iso")
        self.assertEqual(set(bpy.data.objects), before)
        same = self.client.result("compare_images", {"blender_png": iso["png"], "game_png": iso["png"]})
        self.assertEqual((same["verdict"], same["decided_by"]), ("match", []))
        err = self.client.error("render_camera_view", {"camera": {"from": [0, 0, 0]}, "size": [64, 64]})
        self.assertIn("from and at", err["message"])


class LibraryAndTwinTest(BridgeTestCase):
    """The asset library (gamestudio/assetlib.py) and scene twin (core/twin.py) through the bridge."""

    def test_asset_library_round_trip(self):
        self.save_scene()
        self.assertIn("asset_add", self.client.result("asset_list")["hint"])
        crate = self.cube("Crate", (5, 0, 0.5), size=1.0)
        components.add_component(crate, "Spin", {"speed": 2})
        self.cube("Barrel", (8, 0, 0.5), size=1.0)
        added = self.client.result("asset_add", {"objects": ["Crate", "Barrel"], "separate": True,
                                                 "library": "props", "tags": ["test"]}, timeout=240)
        self.assertEqual(sorted(a["asset"] for a in added["assets"]), ["Barrel", "Crate"])
        self.assertTrue(added["library_backup"].endswith(".json"))  # the library was new: its backup says so
        self.assertIn("asset_library_restore", added["restore"])
        listed = self.client.result("asset_list", {"tag": "test"})
        self.assertEqual(listed["libraries"], ["assets/props.blend"])
        self.assertEqual(sorted(a["name"] for a in listed["assets"]), ["Barrel", "Crate"])
        self.assertEqual(next(a for a in listed["assets"] if a["name"] == "Crate")["components"], ["Spin"])

        placed = self.client.result("asset_place", {"asset": "Crate", "location": [0, 4, 0], "rotation_deg": [0, 0, 90]})
        inst = bpy.data.objects[placed["instance"]]
        self.assertEqual(inst.instance_collection.name, "Crate")
        self.assertAlmostEqual(inst.rotation_euler.z, 1.5708, places=3)
        self.assertEqual(self.client.result("asset_list", {"library": "props"})["assets"][1]["placed"], 1)
        replaced = self.client.result("asset_replace", {"instance": inst.name, "asset": "Barrel"})
        self.assertEqual(replaced["asset"], "Barrel")
        spawn = self.client.result("asset_spawnable", {"asset": "Crate"})
        self.assertIn("ctx.spawn('Crate'", spawn["spawn"])
        self.assertEqual(self.client.result("asset_reload")["reloaded"], ["assets/props.blend"])
        local = self.client.result("asset_make_local", {"instance": inst.name})
        self.assertTrue(local["local_objects"])
        self.assertIsNone(bpy.data.objects[inst.name].instance_collection)
        err = self.client.error("asset_place", {"asset": "Ghost"})
        self.assertIn("No asset named 'Ghost'", err["message"])

    def test_spawn_only_and_library_component_edits(self):
        from gamestudio import assetlib

        self.save_scene()
        boulder = self.cube("Boulder", (0, 0, 3), size=1.0)
        components.add_component(boulder, "RigidBody", {"linearDamping": 0.5})
        cam = helpers.new_object("Camera", bpy.data.cameras.new("Camera"), location=(0, -8, 4))
        components.add_component(cam, "FollowCamera", {"target": "Boulder"})
        api.undo_push("test: setup")
        out = self.client.result("asset_add", {"objects": "Boulder", "spawn_only": True}, timeout=240)
        self.assertEqual((out["removed"], out["spawnable"]), (["Boulder"], ["Boulder"]))
        self.assertEqual(out["cleared_references"][0]["field"], "target")
        self.assertNotIn("Boulder", [o.name for o in bpy.context.scene.objects])
        spawn = bpy.data.collections[assetlib.SPAWN_COLLECTION]
        self.assertEqual([c.name for c in spawn.children], ["Boulder"])
        self.assertIn("Boulder", self.client.result("scene_summary")["prefabs"])
        self.assertIn("spawn_only and move",
                      self.client.error("asset_add", {"objects": "Camera", "spawn_only": True, "move": True})["message"])

        def stored(name="Boulder"):
            linked = next(o for o in bpy.data.collections["Boulder"].all_objects if o.name == name)
            return {c: components.get_values(linked, c) for c in components.list_components(linked)}

        out = self.client.result("asset_edit_components", {"asset": "Boulder", "op": "set", "component": "RigidBody",
                                                           "values": {"linearDamping": 0.2}}, timeout=240)
        self.assertTrue(pathlib.Path(out["library_backup"]).is_file())
        self.assertIn("asset_library_restore", out["note"])
        self.assertTrue(out["spawnable"])
        self.assertAlmostEqual(out["objects"][0]["values"]["RigidBody"]["linearDamping"], 0.2, places=5)
        self.assertAlmostEqual(stored()["RigidBody"]["linearDamping"], 0.2, places=5)  # reloaded in this level
        self.client.result("asset_edit_components", {"asset": "Boulder", "op": "add", "objects": "Boulder",
                                                     "components": {"Spin": {"speed": 3}}}, timeout=240)
        self.assertEqual(stored()["Spin"]["speed"], 3.0)
        self.client.result("asset_edit_components", {"asset": "Boulder", "op": "remove", "component": "Spin"},
                           timeout=240)
        self.assertNotIn("Spin", stored())
        err = self.client.error("asset_edit_components", {"asset": "Boulder", "op": "set", "component": "Spin",
                                                          "values": {"speed": 1}})
        self.assertIn("add_if_missing", err["data"]["hint"])
        err = self.client.error("asset_edit_components", {"asset": "Boulder", "op": "set", "objects": "Rock",
                                                          "component": "RigidBody", "values": {"mass": 2}})
        self.assertIn("no object 'Rock'", err["message"])
        err = self.client.error("asset_edit_components", {"asset": "Boulder", "op": "set", "component": "RigidBody",
                                                          "values": {"mass": "heavy"}})
        self.assertIn("mass", err["message"])  # refused here, before any Blender is started
        err = self.client.error("asset_edit_components", {"asset": "Boulder", "op": "add",
                                                          "component": "FollowCamera",
                                                          "values": {"target": "Nobody"}}, )
        self.assertIn("was not changed", err["message"])  # entity names are resolved in the library
        self.assertNotIn("FollowCamera", stored())

    def test_spawn_only_frees_the_prefab_name(self):
        from gamestudio.core import export

        self.save_scene()
        for name in ("Grunt", "Runner"):  # the shooter template: each enemy in its own prefab collection
            coll = bpy.data.collections.new(name)
            bpy.context.scene.collection.children.link(coll)
            export.set_prefab(coll)
            obj = self.cube(name, (len(name), 0, 0.5), size=1.0)
            for other in list(obj.users_collection):
                other.objects.unlink(obj)
            coll.objects.link(obj)
        api.undo_push("test: setup")
        out = self.client.result("asset_add", {"objects": ["Grunt", "Runner"], "separate": True, "library": "actors",
                                               "spawn_only": True}, timeout=240)
        self.assertEqual(out["removed_collections"], ["Grunt", "Runner"])
        self.assertEqual(sorted(out["spawn"]), ["ctx.spawn('Grunt', { position: [x, y, z] })",
                                                "ctx.spawn('Runner', { position: [x, y, z] })"])
        self.assertNotIn("warnings", out)
        self.assertEqual(sorted(self.client.result("scene_summary")["prefabs"]), ["Grunt", "Runner"])
        edit = self.client.result("asset_edit_components", {"asset": "Grunt", "op": "add", "component": "Spin"},
                                  timeout=240)
        self.assertEqual((edit["spawnable"], edit["spawn_name"]), (True, "Grunt"))
        self.client.result("undo")  # the level part: the objects and their collections come back
        self.assertIn("Grunt", [c.name for c in bpy.data.collections if c.library is None])
        clash = self.client.result("asset_spawnable", {"asset": "Grunt"})  # the local "Grunt" prefab is back
        self.assertEqual(clash["spawn"], "ctx.spawn('Grunt [actors.blend]', { position: [x, y, z] })")
        self.assertIn("set_prefab", clash["warnings"][0])

    def test_library_writes_are_backed_up_and_restored(self):
        from gamestudio import assetlib
        from gamestudio.bridge import library

        self.save_scene()
        crate = self.cube("Crate", (0, 0, 0.5), size=1.0)
        components.add_component(crate, "RigidBody", {"mass": 1})
        added = self.client.result("asset_add", {"objects": "Crate", "move": True}, timeout=240)
        self.assertEqual(added["assets"][0]["instance"], "Crate_01")  # not the name of the asset's own object
        path = assetlib.library_path("props")

        def mass():
            linked = next(o for o in bpy.data.collections["Crate"].all_objects if o.name == "Crate")
            return components.get_values(linked, "RigidBody")["mass"]

        for value in (5, 9):
            self.client.result("asset_edit_components", {"asset": "Crate", "op": "set", "component": "RigidBody",
                                                         "values": {"mass": value}}, timeout=240)
        self.assertEqual(mass(), 9.0)
        self.client.error("asset_edit_components", {"asset": "Crate", "op": "set", "component": "RigidBody",
                                                    "values": {"mass": "heavy"}})  # refused before any backup
        self.client.error("asset_edit_components", {"asset": "Crate", "op": "add", "component": "FollowCamera",
                                                    "values": {"target": "Nobody"}})  # nothing written: dropped
        listed = self.client.result("asset_list", {"backups": True})["backups"]
        self.assertEqual([b["tool"] for b in listed], ["set_component", "set_component", "asset_add"])
        self.assertTrue(listed[2]["absent"])  # the library did not exist before asset_add

        out = self.client.result("asset_library_restore", {"library": "props"})  # the latest: before mass 9
        self.assertEqual((out["restored"], out["reloaded"]), (listed[0]["id"], True))
        self.assertEqual(mass(), 5.0)  # the placed instance follows
        self.client.result("asset_library_restore", {"library": "assets/props.blend", "backup": listed[1]["id"][:22]})
        self.assertEqual(mass(), 1.0)
        undo = json.loads(out["undo"].split(" ", 1)[1])
        self.client.result("asset_library_restore", undo)  # takes the first restore back
        self.assertEqual(mass(), 9.0)
        err = self.client.error("asset_library_restore", {"library": "props", "backup": "nope"})
        self.assertIn("matches 'nope'", err["message"])

        gone = self.client.result("asset_library_restore", {"library": "props", "backup": listed[2]["id"]})
        self.assertTrue(gone["removed_library"])
        self.assertFalse(path.exists())
        self.client.result("asset_library_restore", json.loads(gone["undo"].split(" ", 1)[1]))
        self.assertTrue(path.is_file())

        kept = library.BACKUPS_KEPT
        library.BACKUPS_KEPT = 3
        try:
            for _ in range(3):
                library.backup_library(path, "test")
            self.assertEqual(len(library.backups(path)), 3)
        finally:
            library.BACKUPS_KEPT = kept

    def test_scene_twin_and_save(self):
        blend = self.save_scene()
        self.cube("Rock", (1, 2, 0))
        text = self.client.result("scene_twin")["text"]
        self.assertIn('"name":"Rock"', text.replace(" ", ""))  # unsaved changes are included
        saved = self.client.result("save")
        twin_file = pathlib.Path(saved["twin"])
        self.assertEqual(twin_file, blend.with_name("main.scene.json"))
        self.assertTrue(twin_file.is_file())


class CompareAndExportTest(BridgeTestCase):
    """compare_views' Blender half, and the export report passthrough."""

    def setUp(self):
        super().setUp()
        bpy.context.scene.name = "main"
        self.cube("Box")
        cam = helpers.new_object("Cam", bpy.data.cameras.new("Cam"), location=(0, -8, 2))
        cam.rotation_euler = (1.45, 0, 0)
        bpy.context.scene.camera = cam

    def test_render_camera_view_and_compare_images(self):
        self.assertIn("no entity id", self.client.error("render_camera_view", {"size": [64, 64],
                                                                               "engine": "workbench"})["message"])
        self.client.result("export_scene", timeout=120)
        view = self.client.result("render_camera_view", {"size": [96, 64], "engine": "workbench"}, timeout=180)
        self.assertEqual((view["width"], view["height"], view["camera"]), (96, 64, "Cam"))
        self.assertEqual(view["request"], {"scene": "main", "camera": "Cam", "camera_id": view["camera_id"],
                                           "active_camera": True, "size": [96, 64], "state": "stopped", "frames": 3})
        same = self.client.result("compare_images", {"blender_png": view["png"], "game_png": view["png"]})
        self.assertEqual((same["verdict"], same["mean_abs_diff"], same["normalized_diff"]), ("match", 0.0, 0.0))
        self.assertTrue(base64.b64decode(same["side_by_side_png"]).startswith(b"\x89PNG"))
        blank = self.client.result("render_camera_view", {"size": [96, 64], "engine": "workbench",
                                                          "camera": "Cam", "samples": 1}, timeout=180)
        bpy.data.objects["Box"].location.z = 50  # out of view: the next render differs
        moved = self.client.result("render_camera_view", {"size": [96, 64], "engine": "workbench"}, timeout=180)
        diff = self.client.result("compare_images", {"blender_png": blank["png"], "game_png": moved["png"]})
        self.assertGreater(diff["mean_abs_diff"], 0.0)
        self.assertEqual(len(diff["worst_cells"]), 3)
        err = self.client.error("render_camera_view", {"camera": "Box", "size": [64, 64]})
        self.assertIn("not a camera", err["message"])

    def test_export_report_is_compact_unless_verbose(self):
        out = self.client.result("export_scene", timeout=120)
        self.assertIn("bytes", out["size"])
        self.assertLessEqual(set(out["size"]), {"bytes", "top"})
        for key in ("scene", "rev", "entities", "scene_load", "seconds"):
            self.assertIn(key, out)
        full = self.client.result("export_scene", {"verbose": True, "force": True}, timeout=120)
        self.assertIn("glb", full["size"])
        self.assertEqual((full["rev"], full["tier"], full["reason"]), (out["rev"] + 1, "full", "forced"))
        self.assertEqual([f["file"] for f in full["size"]["per_file"]], [pathlib.Path(full["glb"]).name])
        measured = self.client.result("export_scene", {"compress": True}, timeout=120)["size"]["compressed"]
        self.assertTrue("files" in measured or "error" in measured, measured)  # error: no Node.js here

    def test_prefab_names_follow_the_export(self):
        from gamestudio.core import export

        coll = bpy.data.collections.new("Crate")
        export.set_prefab(coll)
        self.assertEqual(self.client.result("scene_summary")["prefabs"],
                         list(export.prefab_names(export.prefab_collections()).values()))


class GameMethodsTest(BridgeTestCase):
    def test_export_scene(self):
        bpy.context.scene.name = "main"
        obj = self.cube("Thing")
        components.add_component(obj, "Spin")
        out = self.client.result("export_scene", timeout=120)
        self.assertTrue(pathlib.Path(out["glb"]).is_file())
        self.assertEqual(out["scene_load"], {"t": "scene.load", "scene": "main",
                                             "url": "/__gs/export/main.scene.json", "rev": out["rev"]})

    def test_devserver_status_and_link_status(self):
        out = self.client.result("devserver", {"action": "status"})
        self.assertFalse(out["running"])
        self.assertIn("devserver.log", out["log"])
        self.assertEqual(self.client.result("link_status")["connected"], False)

    def test_quit_ends_serve_forever(self):
        self.client.result("quit")
        self.assertTrue(bridge._quit_requested)
        bridge._quit_requested = False


class LightingMethodsTest(BridgeTestCase):
    """bake_lighting (bridge/lighting.py) over core/lightmap.py, which test_lightmap.py tests in depth."""

    def level(self):
        bpy.context.scene.name = "main"
        bpy.ops.mesh.primitive_plane_add(size=6)
        bpy.context.active_object.name = "Ground"
        self.cube("Wall", (0, 0, 1))
        mover = self.cube("Mover", (2, 2, 0.5), 1.0)
        components.add_component(mover, "RigidBody")
        sun = bpy.data.objects.new("Sun", bpy.data.lights.new("Sun", "SUN"))
        bpy.context.scene.collection.objects.link(sun)

    def test_bake_lighting_bakes_static_objects_and_exports(self):
        self.level()
        self.assertFalse(api.METHODS["bake_lighting"].mutating)  # generated files only: no undo step
        out = self.client.result("bake_lighting", {"quality": "draft"}, timeout=300)
        self.assertEqual(sorted(o["name"] for o in out["objects"]), ["Ground", "Wall"])
        self.assertIn("Mover", [s["name"] for s in out["skipped"]])
        self.assertIsInstance(out["texture_mb"], float)
        self.assertIsInstance(out["seconds"], float)
        self.assertEqual(out["export"]["lightmaps"]["objects"], 2)
        self.assertEqual(out["export"]["scene_load"]["scene"], "main")
        same = self.client.result("bake_lighting", {"quality": "draft", "export": False}, timeout=300)
        self.assertTrue(same["up_to_date"])
        self.assertIn("force", same["note"])
        self.assertNotIn("export", same)
        # Named objects are baked again even when nothing changed; the others keep their lightmaps.
        wall = self.client.result("bake_lighting", {"quality": "draft", "objects": "Wall", "export": False},
                                  timeout=300)
        self.assertFalse(wall["up_to_date"])
        self.assertEqual(sorted(o["name"] for o in wall["objects"]), ["Ground", "Wall"])

    def test_bake_lighting_errors_say_what_to_fix(self):
        self.level()
        moving = self.client.error("bake_lighting", {"quality": "draft", "objects": ["Mover"]})
        self.assertIn("Mover", moving["message"])
        self.assertIn("fixed", moving["data"]["hint"])
        unknown = self.client.error("bake_lighting", {"objects": ["Ghost"]})
        self.assertIn("No object named 'Ghost'", unknown["message"])
        bad = self.client.error("bake_lighting", {"quality": "ultra"})
        self.assertEqual(bad["code"], server.INVALID_PARAMS)


# --- Game shaders on materials -------------------------------------------------------------------------


class ShaderMethodsTest(BridgeTestCase):
    registry_data = shader_registry()

    def setUp(self):
        super().setUp()
        self.sand, self.rock, self.leaf = (bpy.data.materials.new(n) for n in ("Sand", "Rock", "Leaf"))
        self.ground = self.cube("Ground")
        self.ground.data.materials.append(self.sand)
        self.wall = self.cube("Wall", (3, 0, 0))
        self.wall.data.materials.append(self.sand)
        self.floor = self.cube("Floor", (6, 0, 0))
        for mat in (self.rock, None, self.leaf):
            self.floor.data.materials.append(mat)
        api.undo_push("test: setup")

    def shader_of(self, name):
        return shaders.get_shader(bpy.data.materials[name])  # undo replaces the datablocks

    def test_set_by_material_and_clear(self):
        out = self.client.result("set_material_shader", {"material": "Sand", "shader": "Triplanar",
                                                         "values": {"wet": 0.5}})
        self.assertEqual(out, {"materials": [{"name": "Sand", "shader": "Triplanar",
                                              "values": {"diffuse": "", "tile": 3.2, "wet": 0.5},
                                              "users": ["Ground", "Wall"]}], "warnings": []})
        # Fields left out keep their stored values, like set_component.
        out = self.client.result("set_material_shader", {"material": "Sand", "shader": "Triplanar",
                                                         "values": {"tile": 2}})
        self.assertEqual(out["materials"][0]["values"], {"diffuse": "", "tile": 2.0, "wet": 0.5})
        out = self.client.result("set_material_shader", {"material": "Sand", "shader": None})
        self.assertEqual(out["materials"][0]["shader"], None)
        self.assertEqual(out["materials"][0]["values"], {})
        self.assertIsNone(self.shader_of("Sand"))

    def test_objects_and_slots(self):
        out = self.client.result("set_material_shader", {"objects": ["Floor", "Ground", "Wall"], "shader": "Foliage"})
        self.assertEqual([m["name"] for m in out["materials"]], ["Rock", "Leaf", "Sand"])  # each once, empty slot skipped
        self.assertEqual({self.shader_of(n) for n in ("Rock", "Leaf", "Sand")}, {"Foliage"})
        out = self.client.result("set_material_shader", {"objects": ["Floor"], "slot": 2, "shader": "Triplanar"})
        self.assertEqual([m["name"] for m in out["materials"]], ["Leaf"])
        self.assertEqual(out["materials"][0]["users"], ["Floor"])
        out = self.client.result("set_material_shader", {"objects": ["Floor"], "slot": [0, 2], "shader": None})
        self.assertEqual([m["name"] for m in out["materials"]], ["Rock", "Leaf"])
        self.assertIsNone(self.shader_of("Leaf"))
        empty = self.client.error("set_material_shader", {"objects": ["Floor"], "slot": [0, 1], "shader": "Foliage"})
        self.assertIn("slot 1 of 'Floor' is empty", empty["message"])
        missing = self.client.error("set_material_shader", {"objects": ["Floor"], "slot": 5, "shader": "Foliage"})
        self.assertIn("no slot 5", missing["message"])
        self.assertIsNone(self.shader_of("Rock"), "nothing changed after a failed call")

    def test_bad_requests_change_nothing(self):
        unknown = self.client.error("set_material_shader", {"material": "Sand", "shader": "Triplanr"})
        self.assertIn("Did you mean 'Triplanar'", unknown["message"])
        self.assertIn("Known game shaders: Triplanar, Foliage", unknown["data"]["hint"])
        bad = self.client.error("set_material_shader", {"objects": ["Floor", "Wall"], "shader": "Triplanar",
                                                        "values": {"wet": 3}})
        self.assertIn("wet", bad["message"])
        for params in ({"shader": "Foliage"}, {"material": "Sand", "objects": ["Wall"], "shader": "Foliage"},
                       {"material": "Sand"}, {"material": "Sand", "slot": 0, "shader": "Foliage"},
                       {"objects": ["Floor"], "slot": [], "shader": "Foliage"}):
            self.assertEqual(self.client.error("set_material_shader", params)["code"], server.INVALID_PARAMS, params)
        self.assertIn("Did you mean: Sand", self.client.error("set_material_shader", {"material": "Sandd",
                                                                                      "shader": None})["data"]["hint"])
        self.assertEqual([self.shader_of(n) for n in ("Sand", "Rock", "Leaf")], [None, None, None])

    def test_one_undo_step_for_the_whole_call(self):
        self.client.result("set_material_shader", {"objects": ["Floor", "Ground"], "shader": "Triplanar",
                                                   "values": {"tile": 7}})
        self.assertEqual([self.shader_of(n) for n in ("Sand", "Rock", "Leaf")], ["Triplanar"] * 3)
        self.assertEqual(self.client.result("undo"), {"undone": 1})
        self.assertEqual([self.shader_of(n) for n in ("Sand", "Rock", "Leaf")], [None, None, None])
        self.client.result("redo")
        self.assertEqual(shaders.get_values(bpy.data.materials["Rock"])["tile"], 7.0)

    def test_locked_objects_pause_and_linked_materials(self):
        from gamestudio import activity

        self.floor.gs.ai_lock = True
        err = self.client.error("set_material_shader", {"objects": ["Floor"], "shader": "Foliage"})
        self.assertIn("locked", err["message"])
        self.assertIsNone(self.shader_of("Rock"))
        activity.set_paused(True)
        try:
            err = self.client.error("set_material_shader", {"material": "Sand", "shader": "Foliage"})
            self.assertEqual(err["code"], server.PAUSED_BY_USER)
        finally:
            activity.set_paused(False)
        self.assertIsNone(self.shader_of("Sand"))
        lib_path = str(self.root / "lib.blend")
        lib_mat = bpy.data.materials.new("LibStone")
        bpy.data.libraries.write(lib_path, {lib_mat})
        bpy.data.materials.remove(lib_mat)
        with bpy.data.libraries.load(lib_path, link=True) as (_src, dst):
            dst.materials = ["LibStone"]
        err = self.client.error("set_material_shader", {"material": "LibStone", "shader": "Foliage"})
        self.assertIn("linked from", err["message"])

    def test_empty_registry_stores_the_name_with_a_warning(self):
        registry.set_registry(helpers.fixture_registry())
        out = self.client.result("set_material_shader", {"material": "Sand", "shader": "Water"})
        self.assertEqual(out["materials"][0]["shader"], "Water")
        self.assertEqual(len(out["warnings"]), 1)
        self.assertIn("no game shaders yet", out["warnings"][0])

    def test_reads_show_shaders(self):
        status = self.client.result("status")
        self.assertEqual((status["registry"]["components"], status["registry"]["shaders"]),
                         (len(helpers.fixture_registry()["components"]), 2))
        types_ = self.client.result("list_component_types")
        tri = next(s for s in types_["shaders"] if s["name"] == "Triplanar")
        self.assertTrue(tri["fields"]["tile"].startswith("float = 3.2"))
        self.assertEqual(tri["description"], "World-space photo scans with wetness")
        self.assertIn("default", self.client.result("list_component_types", {"verbose": True})["shaders"][0]["fields"][1])
        self.assertEqual(self.client.result("list_component_types", {"type": "Foliage"})["source"],
                         "src/shaders/foliage.ts")
        self.assertEqual(self.client.result("scene_summary")["shader_types"], ["Triplanar", "Foliage"])
        self.assertNotIn("material_shaders", self.client.result("get_object", {"name": "Floor"}))
        self.client.result("set_material_shader", {"objects": ["Floor"], "shader": "Triplanar", "values": {"wet": 0.25}})
        self.client.result("set_material_shader", {"objects": ["Floor"], "slot": 0, "shader": "Foliage"})
        info = self.client.result("get_object", {"name": "Floor"})
        self.assertEqual(info["material_shaders"], {"Rock": {"shader": "Foliage", "slots": [0]},
                                                    "Leaf": {"shader": "Triplanar", "slots": [2], "values": {"wet": 0.25}}})
        registry.set_registry(shader_registry(lambda found: found.pop()))  # Foliage's file was deleted
        info = self.client.result("get_object", {"name": "Floor"})
        self.assertTrue(info["material_shaders"]["Rock"]["missing_definition"])
        text = self.client.result("scene_twin")["text"]
        self.assertIn('{"name":"Leaf","shader":"Triplanar","values":{"wet":0.25}}', text)
        self.assertIn('{"name":"Rock","shader":"Foliage"}', text)

    def test_library_shaders_list_in_one_line(self):
        """The shader library's dozens of shaders list compactly: name, category and the description's first
        sentence. Project shaders keep their fields; type gives a library shader's, verbose every raw entry."""
        long = "Waves, foam and depth colour. Cheap: no textures, about ten waves (0.5 ms) per pixel."

        def mutate(found):
            next(s for s in found if s["name"] == "Water")["description"] = long
            next(s for s in found if s["name"] == "Triplanar")["category"] = "Surfaces"

        registry.set_registry(shader_registry(mutate, library=True))
        listed = {s["name"]: s for s in self.client.result("list_component_types")["shaders"]}
        self.assertEqual(listed["Water"], {"name": "Water", "category": "Water",
                                           "description": "Waves, foam and depth colour.", "builtin": True})
        self.assertEqual(listed["Swirl"]["category"], "")  # none: the MCP lists it under "Other"
        self.assertNotIn("fields", listed["Dissolve"])
        self.assertTrue(listed["Triplanar"]["fields"]["tile"].startswith("float = 3.2"))
        self.assertEqual(listed["Triplanar"]["category"], "Surfaces")
        self.assertNotIn("category", listed["Foliage"])
        self.assertNotIn("builtin", listed["Foliage"])
        one = self.client.result("list_component_types", {"type": "Water"})
        self.assertEqual((one["description"], [f["name"] for f in one["fields"]]), (long, ["speed"]))
        raw = {s["name"]: s for s in self.client.result("list_component_types", {"verbose": True})["shaders"]}
        self.assertEqual(raw["Dissolve"]["fields"][0]["name"], "progress")


if __name__ == "__main__":
    unittest.main()
