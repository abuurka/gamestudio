# SPDX-License-Identifier: GPL-3.0-or-later
"""The game stream client (core/stream.py) against a fake stream host: a Python script that speaks
the host's protocol, so no Node.js or browser is needed."""

import json
import pathlib
import shutil
import sys
import tempfile
import time
import unittest
from unittest import mock

import bpy
import imbuf
import numpy as np

from gamestudio.core import stream

import helpers

# Speaks the stream host's protocol. Records every command to --record (JSON lines) and sends what
# the commands ask for: {"type": "test", "send": "frames"} sends an RGBA and a JPEG frame.
FAKE_HOST = r'''
import json, socket, struct, sys, time
args = sys.argv[1:]
opt = lambda name, default=None: args[args.index(name) + 1] if name in args else default
record = open(opt("--record"), "a", encoding="utf-8")
server = socket.create_server(("127.0.0.1", 0))
token = "secret-token"
print(json.dumps({"port": server.getsockname()[1], "token": token}), flush=True)
if "--exit-early" in args:
    sys.stderr.write("fake host: giving up\n")
    sys.exit(3)
conn, _ = server.accept()

def send(kind, payload):
    conn.sendall(struct.pack(">IB", len(payload), ord(kind)) + payload)

def status(**kw):
    kw.setdefault("mode", "headless"); kw.setdefault("audio", True); kw.setdefault("renderer", "Fake GPU")
    send("S", json.dumps(kw).encode())

def frame(width, height, number, fmt, pixels):
    send("F", struct.pack(">IIIB3xd", width, height, number, fmt, time.time() * 1000) + pixels)

buf = b""
hello = False
while True:
    data = conn.recv(65536)
    if not data:
        break
    buf += data
    while len(buf) >= 5:
        size, kind = struct.unpack(">IB", buf[:5])
        if len(buf) < 5 + size:
            break
        cmd = json.loads(buf[5:5 + size]); buf = buf[5 + size:]
        record.write(json.dumps(cmd) + "\n"); record.flush()
        if not hello:
            if cmd.get("type") != "hello" or cmd.get("token") != token:
                sys.exit(4)
            hello = True
            status(status="starting", message="Starting the browser")
            if "--fail" in args:
                status(status="error", message="No browser here")
                time.sleep(0.2)
                sys.exit(0)
            status(status="ready", message="Playing in Fake 1", agent="GameStudioStream/fake")
        elif cmd["type"] == "test" and cmd["send"] == "frames":
            # 2x2 RGBA, rows top to bottom: red, red / blue, blue
            frame(2, 2, 1, 0, bytes([255, 0, 0, 255] * 2 + [0, 0, 255, 255] * 2))
            jpeg = open(opt("--jpeg"), "rb").read()
            frame(0, 0, 2, 1, jpeg)
        elif cmd["type"] == "stop" and "--ignore-stop" not in args:
            status(status="stopped", message="Stopped by Blender")
            time.sleep(0.1)
            sys.exit(0)
time.sleep(60 if "--ignore-stop" in args else 0)
'''


def make_jpeg(path: pathlib.Path, width=64, height=32) -> bytes:
    """A JPEG whose top half is red and bottom half blue (ImBuf rows run bottom to top)."""
    image = imbuf.new((width, height))
    with image.with_buffer(write=True) as buf:
        pixels = np.asarray(buf)
        pixels[:] = 255
        pixels[height // 2:, :, :3] = (255, 0, 0)   # top rows
        pixels[:height // 2, :, :3] = (0, 0, 255)   # bottom rows
    image.file_type = "JPEG"
    image.quality = 95
    imbuf.write(image, filepath=str(path))
    return path.read_bytes()


class WireFormatTest(unittest.TestCase):
    def test_reader_splits_messages_whatever_the_chunking(self):
        data = (stream.encode_json({"type": "hello", "token": "t"}, b"S")
                + stream.encode_frame(3, 1, 7, stream.FORMAT_RGBA, bytes(range(12)), timestamp=12.5)
                + stream.encode(b"S", b"{}"))
        for size in (1, 5, 13, len(data)):
            reader = stream.Reader()
            got = []
            for i in range(0, len(data), size):
                got += reader.feed(data[i:i + size])
            self.assertEqual([kind for kind, _ in got], ["S", "F", "S"])
            self.assertEqual(json.loads(got[0][1]), {"type": "hello", "token": "t"})
            width, height, number, fmt, timestamp, pixels = stream.parse_frame(got[1][1])
            self.assertEqual((width, height, number, fmt, timestamp), (3, 1, 7, stream.FORMAT_RGBA, 12.5))
            self.assertEqual(bytes(pixels), bytes(range(12)))

    def test_reader_rejects_oversized_messages(self):
        with self.assertRaises(stream.StreamError):
            stream.Reader(max_message=10).feed(stream.encode(b"J", b"x" * 11))

    def test_decode_rgba_and_jpeg_top_to_bottom(self):
        width, height, pixels = stream.decode_pixels(stream.FORMAT_RGBA, 1, 2, bytes([1, 2, 3, 4, 5, 6, 7, 8]))
        self.assertEqual((width, height, bytes(pixels)), (1, 2, bytes([1, 2, 3, 4, 5, 6, 7, 8])))
        with self.assertRaises(stream.StreamError):
            stream.decode_pixels(stream.FORMAT_RGBA, 2, 2, b"\0" * 4)
        folder = pathlib.Path(tempfile.mkdtemp(prefix="gs_stream_"))
        try:
            jpeg = make_jpeg(folder / "frame.jpg")
        finally:
            shutil.rmtree(folder, ignore_errors=True)
        width, height, pixels = stream.decode_pixels(stream.FORMAT_JPEG, 0, 0, jpeg)
        self.assertEqual((width, height, len(pixels)), (64, 32, 64 * 32 * 4))
        rgba = np.frombuffer(pixels, np.uint8).reshape(height, width, 4)
        top, bottom = rgba[2, 32], rgba[29, 32]
        self.assertGreater(top[0], 200)
        self.assertLess(top[2], 60)
        self.assertGreater(bottom[2], 200)
        self.assertLess(bottom[0], 60)
        self.assertEqual(rgba[0, 0, 3], 255)


class KeyCodeTest(unittest.TestCase):
    def test_blender_events_map_to_dom_codes(self):
        self.assertEqual(stream.key_code("W"), ("KeyW", "w"))
        self.assertEqual(stream.key_code("W", shift=True), ("KeyW", "W"))
        self.assertEqual(stream.key_code("ZERO"), ("Digit0", "0"))
        self.assertEqual(stream.key_code("ONE", shift=True), ("Digit1", "!"))
        self.assertEqual(stream.key_code("SPACE"), ("Space", " "))
        self.assertEqual(stream.key_code("RET"), ("Enter", "Enter"))
        self.assertEqual(stream.key_code("ESC"), ("Escape", "Escape"))
        self.assertEqual(stream.key_code("TAB"), ("Tab", "Tab"))
        self.assertEqual(stream.key_code("LEFT_ARROW"), ("ArrowLeft", "ArrowLeft"))
        self.assertEqual(stream.key_code("UP_ARROW"), ("ArrowUp", "ArrowUp"))
        self.assertEqual(stream.key_code("LEFT_SHIFT"), ("ShiftLeft", "Shift"))
        self.assertEqual(stream.key_code("RIGHT_CTRL"), ("ControlRight", "Control"))
        self.assertEqual(stream.key_code("F5"), ("F5", "F5"))
        self.assertEqual(stream.key_code("F12"), ("F12", "F12"))
        self.assertEqual(stream.key_code("NUMPAD_7"), ("Numpad7", "7"))
        self.assertEqual(stream.key_code("NUMPAD_ENTER"), ("NumpadEnter", "Enter"))
        self.assertEqual(stream.key_code("SEMI_COLON", shift=True), ("Semicolon", ":"))
        self.assertEqual(stream.key_code("COMMA"), ("Comma", ","))
        self.assertIsNone(stream.key_code("LEFTMOUSE"))
        self.assertIsNone(stream.key_code("WHEELUPMOUSE"))

    def test_every_keyboard_event_type_of_this_blender_is_known(self):
        """Every key on a US keyboard Blender reports has a DOM code (mouse, NDOF, ... have none)."""
        items = bpy.types.Event.bl_rna.properties["type"].enum_items
        keyboard = [i.identifier for i in items if len(i.identifier) == 1 or i.identifier in (
            "ZERO", "NINE", "SPACE", "RET", "ESC", "TAB", "BACK_SPACE", "DEL", "HOME", "END", "PAGE_UP",
            "PAGE_DOWN", "LEFT_ARROW", "RIGHT_ARROW", "UP_ARROW", "DOWN_ARROW", "LEFT_SHIFT", "RIGHT_SHIFT",
            "LEFT_CTRL", "RIGHT_CTRL", "LEFT_ALT", "RIGHT_ALT", "OSKEY", "MINUS", "EQUAL", "SLASH",
            "BACK_SLASH", "SEMI_COLON", "QUOTE", "ACCENT_GRAVE", "COMMA", "PERIOD", "LEFT_BRACKET",
            "RIGHT_BRACKET", "NUMPAD_0", "NUMPAD_9", "NUMPAD_PERIOD", "NUMPAD_ENTER", "F1", "F24")]
        self.assertGreater(len(keyboard), 60)
        self.assertEqual([t for t in keyboard if stream.key_code(t) is None], [])


class AvailableTest(unittest.TestCase):
    def setUp(self):
        self.root = pathlib.Path(tempfile.mkdtemp(prefix="gs_stream_"))

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_reports_what_is_missing(self):
        stream._available_cache.clear()
        self.addCleanup(stream._available_cache.clear)
        modules = self.root / "node_modules"
        with mock.patch.object(stream, "host_script", return_value=None):
            ok, why = stream._check_available(self.root)
        self.assertFalse(ok)
        self.assertEqual(why, stream.NO_PACKAGES)
        script = modules / "@gamestudio" / "devserver" / "bin" / "gamestudio-stream.js"
        script.parent.mkdir(parents=True)
        script.write_text("// fake", encoding="utf-8")
        self.assertEqual(stream.host_script(self.root), script)
        ok, why = stream._check_available(self.root)
        self.assertFalse(ok)
        self.assertEqual(why, stream.NO_PUPPETEER, "says what is missing; GameStudio's setup provides it")
        (modules / "puppeteer-core").mkdir()
        (modules / "puppeteer-core" / "package.json").write_text("{}", encoding="utf-8")
        with mock.patch.object(stream, "node_command", return_value=(None, {})):
            self.assertIn("Node.js", stream._check_available(self.root)[1])
        with mock.patch.object(stream, "node_command", return_value=(sys.executable, {})), \
                mock.patch.object(stream, "browser_path", return_value=None):
            self.assertEqual(stream._check_available(self.root)[1], stream.NO_BROWSER)
        with mock.patch.object(stream, "node_command", return_value=(sys.executable, {})), \
                mock.patch.object(stream, "browser_path", return_value="browser.exe"):
            self.assertEqual(stream._check_available(self.root), (True, ""))
            self.assertEqual(stream.available(self.root), (True, ""))
        # Cached for a few seconds: UI code may ask while drawing.
        with mock.patch.object(stream, "browser_path", return_value=None):
            self.assertEqual(stream.available(self.root), (True, ""))

    def test_fit_size_keeps_the_aspect_under_the_pixel_budget(self):
        self.assertEqual(stream.fit_size(960, 540), (960, 540))
        self.assertEqual(stream.fit_size(1920, 1080), (1280, 720))
        self.assertEqual(stream.fit_size(3840, 1000, max_pixels=1_000_000), (1959, 510))
        self.assertEqual(stream.fit_size(5, 5), (16, 16))

    def test_this_checkout_has_the_host(self):
        script = stream.host_script(None)
        self.assertIsNotNone(script)
        self.assertEqual(script.name, "gamestudio-stream.js")


class SessionTest(unittest.TestCase):
    def setUp(self):
        self.root = pathlib.Path(tempfile.mkdtemp(prefix="gs_stream_"))
        self.host = self.root / "fake_host.py"
        self.host.write_text(FAKE_HOST, encoding="utf-8")
        self.record = self.root / "commands.jsonl"
        self.jpeg = self.root / "frame.jpg"
        make_jpeg(self.jpeg)
        self.statuses = []

    def tearDown(self):
        stream.stop_all(wait=True)
        shutil.rmtree(self.root, ignore_errors=True)

    def start(self, *extra, url="http://localhost:5173/", **kwargs):
        return stream.start(url, 320, 180, project_root=self.root, script=self.host,
                            node=sys.executable, browser="fake-browser",
                            on_status=lambda s: self.statuses.append((s.status, s.message)),
                            extra_args=["--record", str(self.record), "--jpeg", str(self.jpeg), *extra], **kwargs)

    def commands(self):
        if not self.record.is_file():
            return []
        return [json.loads(line) for line in self.record.read_text(encoding="utf-8").splitlines() if line]

    def test_status_frames_input_and_stop(self):
        session = self.start(scale=1.5, quality=70, max_fps=30)
        session.float_frames = True
        self.assertEqual(session.status, "starting")
        self.assertIsNone(session.latest_frame())
        self.assertTrue(helpers.pump_until(lambda: session.status == "running", [session.poll], timeout=15))
        self.assertEqual((session.message, session.mode, session.audio, session.renderer, session.agent),
                         ("Playing in Fake 1", "headless", True, "Fake GPU", "GameStudioStream/fake"))
        self.assertEqual(session._argv[2:4], ["--url", "http://localhost:5173/"])
        hello, size, quality = self.commands()[:3]
        self.assertEqual(hello["type"], "hello")
        self.assertEqual(hello["token"], "secret-token")
        self.assertIn("jpeg", hello["formats"])
        self.assertEqual(size, {"type": "size", "width": 320, "height": 180, "scale": 1.5})
        self.assertEqual(quality, {"type": "quality", "quality": 70, "max_fps": 30})

        # Frames: the newest decoded frame wins; rows top to bottom.
        session._send({"type": "test", "send": "frames"})
        self.assertTrue(helpers.pump_until(lambda: (session.latest_frame() or (0,))[0] == 64, [session.poll], timeout=10))
        width, height, pixels = session.latest_frame()
        self.assertEqual((width, height, len(pixels)), (64, 32, 64 * 32 * 4))
        self.assertGreater(pixels[(2 * 64 + 32) * 4], 200)          # red at the top
        self.assertGreater(pixels[(29 * 64 + 32) * 4 + 2], 200)     # blue at the bottom
        fw, fh, floats = session.latest_frame_float()
        self.assertEqual((fw, fh, floats.format, len(floats)), (64, 32, "f", 64 * 32 * 4))
        self.assertAlmostEqual(floats[(2 * 64 + 32) * 4], pixels[(2 * 64 + 32) * 4] / 255.0, places=5)
        self.assertGreaterEqual(session.frame_id, 1)
        self.assertGreater(session.fps, 0)
        self.assertGreater(session.decode_ms, 0)

        session.send_mouse("move", 10.5, 20, dx=0, dy=0)
        session.send_mouse("down", 10.5, 20, button=2)
        session.send_mouse("wheel", 10.5, 20, dx=0, dy=120)
        session.send_key("down", "KeyW", "w", shift=True)
        session.send_key("up", "KeyW", "w")
        session.resize(640, 360)
        session.resize(640, 360)  # unchanged: not sent again
        session.set_paused(True)
        session.set_paused(True)
        session.set_paused(True, suspend=True)  # nobody plays it either: the page is hidden too
        self.assertTrue(session.suspended)
        session.set_paused(False)
        self.assertFalse(session.suspended)
        session.send_text("hi")
        session.release_input()
        session.set_quality(max_fps=0)
        session.stop()
        self.assertEqual(session.status, "stopped")
        self.assertTrue(helpers.pump_until(lambda: not session.alive, [session.poll], timeout=10))
        sent = [c for c in self.commands()[3:] if c["type"] != "test"]
        self.assertEqual([c["type"] for c in sent],
                         ["mouse", "mouse", "mouse", "key", "key", "size", "pause", "pause", "resume", "text",
                          "release", "quality", "stop"])
        self.assertEqual(sent[1], {"type": "mouse", "kind": "down", "x": 10.5, "y": 20.0, "button": 2, "dx": 0.0,
                                   "dy": 0.0, "shift": False, "ctrl": False, "alt": False})
        self.assertEqual(sent[2]["dy"], 120.0)
        self.assertEqual(sent[3], {"type": "key", "kind": "down", "code": "KeyW", "key": "w", "shift": True,
                                   "ctrl": False, "alt": False, "repeat": False})
        self.assertEqual(sent[5], {"type": "size", "width": 640, "height": 360, "scale": 1.5})
        self.assertEqual(sent[6:8], [{"type": "pause", "suspend": False}, {"type": "pause", "suspend": True}])
        self.assertEqual(sent[9], {"type": "text", "text": "hi"})
        self.assertEqual(sent[11], {"type": "quality", "max_fps": 0})
        self.assertEqual([s for s, _ in self.statuses], ["starting", "running", "stopped"])
        self.assertNotIn(session, stream.sessions())

    def test_an_address_after_the_start(self):
        # Play starts the browser while the dev server starts; the game's address comes later.
        session = self.start(url=None)
        self.assertNotIn("--url", session._argv)
        self.assertEqual(session.url, "")
        session.navigate("http://localhost:5173/")  # before or after the connection: it reaches the host
        self.assertTrue(helpers.pump_until(lambda: session.status == "running", [session.poll], timeout=15))
        session.navigate("http://localhost:5173/")  # the same address: not sent again
        session.navigate("http://localhost:5174/")  # the dev server moved
        navigations = lambda: [c["url"] for c in self.commands() if c["type"] == "navigate"]  # noqa: E731
        self.assertTrue(helpers.pump_until(lambda: len(navigations()) >= 2, [session.poll], timeout=10))
        self.assertEqual(navigations(), ["http://localhost:5173/", "http://localhost:5174/"])
        self.assertEqual(session.url, "http://localhost:5174/")
        self.assertEqual(self.commands()[0]["type"], "hello")
        session.stop(wait=True)

    def test_host_error_status(self):
        session = self.start("--fail")
        self.assertTrue(helpers.pump_until(lambda: session.status == "error", [session.poll], timeout=15))
        self.assertEqual(session.message, "No browser here")
        self.assertTrue(helpers.pump_until(lambda: not session.alive, [session.poll], timeout=10))

    def test_host_that_exits_reports_its_output(self):
        session = self.start("--exit-early")
        self.assertTrue(helpers.pump_until(lambda: session.status == "error", [session.poll], timeout=15))
        self.assertIn("The stream host exited (code 3)", session.message)
        self.assertIn("giving up", session.message)
        self.assertIn(("error", session.message), self.statuses)

    def test_two_argument_status_callback(self):
        seen = []
        session = stream.start("http://localhost:5173/", 320, 180, project_root=self.root, script=self.host,
                               node=sys.executable, browser="fake-browser",
                               on_status=lambda status, message: seen.append(status),
                               extra_args=["--record", str(self.record)])
        self.assertTrue(helpers.pump_until(lambda: session.status == "running", [session.poll], timeout=15))
        session.stop(wait=True)
        self.assertEqual(seen, ["starting", "running", "stopped"])

    def test_a_host_that_ignores_stop_is_killed(self):
        session = self.start("--ignore-stop")
        self.assertTrue(helpers.pump_until(lambda: session.status == "running", [session.poll], timeout=15))
        with mock.patch.object(stream, "STOP_TIMEOUT", 0.3):
            started = time.monotonic()
            session.stop(wait=True)
        self.assertFalse(session.alive)
        self.assertLess(time.monotonic() - started, 10)

    def test_stop_all_on_file_load_and_handlers(self):
        self.assertIn(stream._on_load_pre, bpy.app.handlers.load_pre)
        self.assertIn(stream._on_exit, bpy.app.handlers.exit_pre)
        # Persistent, so loading a file keeps them; loading a file stops every stream but the kept
        # ones (the Game View's: it ends its own when the project changes).
        self.assertTrue(hasattr(stream._on_load_pre, "_bpy_persistent"))
        session, kept = self.start(), self.start()
        kept.keep_on_load = True
        self.assertTrue(helpers.pump_until(lambda: session.status == kept.status == "running",
                                           [session.poll, kept.poll], timeout=15))
        self.assertEqual(stream.sessions(), [session, kept])
        stream._on_load_pre(None)
        self.assertEqual(stream.sessions(), [kept])
        self.assertTrue(helpers.pump_until(lambda: not session.alive, [], timeout=10))
        self.assertEqual(session.status, "stopped")
        self.assertTrue(kept.alive)
        self.assertEqual(stream.stop_all(), 1, "Blender quits: every stream ends")
        self.assertFalse(kept.alive)

    def test_start_errors(self):
        with self.assertRaises(stream.StreamError):
            stream.start("http://x/", 10, 10, project_root=self.root, script=self.host, node=str(self.root / "no-node"),
                         browser="fake-browser")
        with mock.patch.object(stream, "browser_path", return_value=None), self.assertRaises(stream.StreamError):
            stream.start("http://x/", 10, 10, project_root=self.root, script=self.host, node=sys.executable)
