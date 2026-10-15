# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared test helpers: temporary projects, scene reset, a fake link hub."""

import copy
import json
import os
import pathlib
import shutil
import socket
import tempfile
import threading
import time
import unittest

import bpy

from gamestudio.core import link, project, registry

FIXTURES = pathlib.Path(__file__).resolve().parent / "fixtures"
TOKEN = "0123456789abcdef0123456789abcdef"


def tmp_base() -> str:
    base = os.environ.get("GS_TEST_TMP") or tempfile.gettempdir()
    os.makedirs(base, exist_ok=True)
    return base


def fixture_registry() -> dict:
    with open(FIXTURES / "components.json", "r", encoding="utf-8") as f:
        return json.load(f)


def make_project(registry_data=None, with_registry=True) -> pathlib.Path:
    root = pathlib.Path(tempfile.mkdtemp(prefix="gs_proj_", dir=tmp_base()))
    config = {"name": "test-game", "formatVersion": 1, "startScene": "main",
              "link": {"editorPort": 7710, "gamePort": 7711}}
    (root / "gamestudio.json").write_text(json.dumps(config), encoding="utf-8")
    (root / ".gamestudio").mkdir()
    if with_registry:
        write_registry(root, registry_data if registry_data is not None else fixture_registry())
    return root


def write_registry(root, data) -> None:
    path = project.registry_path(root)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    # Make sure the mtime changes even on coarse file systems.
    stamp = time.time() + 2.0 * (1 + getattr(write_registry, "_n", 0))
    write_registry._n = getattr(write_registry, "_n", 0) + 1
    os.utime(path, (stamp, stamp))


def reset_scene() -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)


def new_object(name, data=None, parent=None, location=(0, 0, 0), collection=None):
    obj = bpy.data.objects.new(name, data)
    (collection or bpy.context.scene.collection).objects.link(obj)
    obj.location = location
    if parent is not None:
        obj.parent = parent
    return obj


def new_mesh(name="Mesh"):
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0), (0, 0, 1)], [], [(0, 1, 2), (0, 1, 3)])
    return mesh


def modified_registry(mutate) -> dict:
    data = copy.deepcopy(fixture_registry())
    mutate(data)
    return data


def component(data, name) -> dict:
    return next(c for c in data["components"] if c["name"] == name)


class ProjectTestCase(unittest.TestCase):
    """Fresh empty scene plus a temporary project with the fixture registry."""

    registry_data = None
    with_registry = True

    def setUp(self):
        self.root = make_project(self.registry_data, self.with_registry)
        project.set_project_root_override(self.root)
        reset_scene()
        registry.reload_registry(self.root)

    def tearDown(self):
        project.set_project_root_override(None)
        shutil.rmtree(self.root, ignore_errors=True)


def pump_until(predicate, pumps=(), timeout=5.0, interval=0.005) -> bool:
    """Call each pump function until predicate() is true or the timeout passes."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for fn in pumps:
            fn()
        if predicate():
            return True
        time.sleep(interval)
    for fn in pumps:
        fn()
    return predicate()


class FakeHub:
    """A tiny NDJSON hub on 127.0.0.1 (plain sockets; never touches bpy).

    Answers hello with welcome (or an auth error), records every message, and
    can push messages to the connected editor.
    """

    def __init__(self, token=TOKEN, peers=None, registry_doc=None):
        self.token = token
        self.peers = peers if peers is not None else [{"peer": "g1", "role": "game"}]
        self.registry_doc = registry_doc
        self.received = []
        self.responders = {}       # t -> fn(msg) -> reply dict | None
        self.connections = []
        self.hellos = 0
        self._lock = threading.Lock()
        self._server = socket.create_server(("127.0.0.1", 0))
        self._server.settimeout(0.2)
        self.port = self._server.getsockname()[1]
        self._running = True
        self._thread = threading.Thread(target=self._accept_loop, daemon=True)
        self._thread.start()

    def session(self) -> dict:
        return {"host": "127.0.0.1", "port": self.port, "token": self.token, "url": "http://localhost:5173/"}

    def _accept_loop(self):
        while self._running:
            try:
                conn, _addr = self._server.accept()
            except (socket.timeout, OSError):
                continue
            with self._lock:
                self.connections.append(conn)
            threading.Thread(target=self._reader, args=(conn,), daemon=True).start()

    def _reader(self, conn):
        buf = b""
        while self._running:
            try:
                data = conn.recv(65536)
            except OSError:
                break
            if not data:
                break
            buf += data
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                if line.strip():
                    self._handle(conn, json.loads(line))

    def _handle(self, conn, msg):
        with self._lock:
            self.received.append(msg)
        if msg.get("t") == "hello":
            self.hellos += 1
            if msg.get("token") != self.token or msg.get("protocol") != 1:
                self.write(conn, {"t": "error", "code": "auth", "msg": "bad token"})
                conn.close()
                return
            self.write(conn, {"t": "welcome", "protocol": 1, "peer": f"e{self.hellos}", "peers": self.peers})
            if self.registry_doc is not None:
                self.write(conn, {"t": "registry", "registry": self.registry_doc})
            return
        responder = self.responders.get(msg.get("t"))
        if responder is not None:
            reply = responder(msg)
            if reply is not None:
                self.write(conn, reply)

    def write(self, conn, msg) -> None:
        self.write_raw(conn, (json.dumps(msg) + "\n").encode("utf-8"))

    def write_raw(self, conn, data: bytes) -> None:
        try:
            conn.sendall(data)
        except OSError:
            pass

    def broadcast(self, msg) -> None:
        with self._lock:
            conns = list(self.connections)
        for conn in conns:
            self.write(conn, msg)

    def broadcast_raw(self, data: bytes) -> None:
        with self._lock:
            conns = list(self.connections)
        for conn in conns:
            self.write_raw(conn, data)

    def messages(self, t=None) -> list:
        with self._lock:
            return [m for m in self.received if t is None or m.get("t") == t]

    def clear(self) -> None:
        with self._lock:
            self.received.clear()

    def drop_connections(self) -> None:
        with self._lock:
            conns, self.connections = self.connections, []
        for conn in conns:
            try:
                conn.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            conn.close()

    def close(self) -> None:
        self._running = False
        self.drop_connections()
        self._server.close()


def connect_global_link(hub, timeout=5.0) -> link.LinkClient:
    """Point the add-on's link client at a fake hub and wait for the welcome."""
    client = link.get_link()
    client.disconnect("")
    client.session_provider = hub.session
    client.reconnect_interval = 0.1
    client.reconnect_now()
    ok = pump_until(lambda: client.connected, [client.pump], timeout)
    if not ok:
        raise AssertionError(f"link did not connect: {client.last_error}")
    return client


def restore_global_link() -> None:
    client = link.get_link()
    client.disconnect("")
    client.session_provider = link.session_from_project
    client.reconnect_interval = link.RECONNECT_INTERVAL
    client.last_error = ""
    client.logs.clear()
