# SPDX-License-Identifier: GPL-3.0-or-later
"""Play in Blender: the game runs in a browser GameStudio controls and its frames come here.

Blender's Python API cannot embed a web page. The stream host (@gamestudio/devserver's
bin/gamestudio-stream.js, run with the project's Node.js) plays the game in headless Edge or
Chrome on the GPU and sends its frames over TCP on 127.0.0.1; input goes back the same way. The
wire format is described in runtime/packages/devserver/src/stream-protocol.js:

    message   u32 big-endian payload length, u8 type, payload
    'J'       Blender -> host: JSON command (hello with the token first)
    'S'       host -> Blender: status JSON
    'F'       host -> Blender: 24-byte header (u32 width, height, frame number, u8 format,
              3 reserved, f64 draw time in ms since the epoch), then JPEG (format 1) or RGBA8
              rows top to bottom (format 0)

A reader thread per session receives frames and decodes the newest JPEG with imbuf (about 2-6 ms
at 960x540, off the main thread); poll(), called from a Blender timer, only swaps in the newest
frame and status. A session can start before the game has an address (Play starts the browser
while the dev server starts) and navigate() gives it one. Sessions are stopped when Blender quits,
and when a file is loaded unless kept (keep_on_load: the Game View's, which editors/game_stream.py
ends when the project changes). Works without bpy too (the protocol helpers and key_code are plain
Python).
"""

import atexit
import collections
import inspect
import json
import os
import pathlib
import select
import shutil
import socket
import struct
import subprocess
import sys
import threading
import time

from .log import get_logger

try:
    from bpy.app.handlers import persistent
except ImportError:  # outside Blender
    def persistent(fn):
        return fn

log = get_logger(__name__)

HOST_SCRIPT = pathlib.Path("@gamestudio", "devserver", "bin", "gamestudio-stream.js")
LOG_NAME = "stream.log"
HEADER = struct.Struct(">IB")             # payload length, message type
FRAME = struct.Struct(">IIIB3xd")         # width, height, frame number, format, draw time (ms)
FORMAT_RGBA = 0
FORMAT_JPEG = 1
MAX_MESSAGE = 64 * 1024 * 1024
START_TIMEOUT = 20.0                       # from spawn to the host's port line and connection
STOP_TIMEOUT = 3.0                         # a host that has not exited by then is killed
FPS_WINDOW = 1.0
ERROR_GRACE = 0.5                          # a broken connection waits this long for the host's exit code
# Frames above this cost more to decode than a 60 Hz stream allows (measured on the demo game:
# 3.5 ms at 960x540, 19 ms at 1920x1080); fit_size() keeps a stream under it.
MAX_PIXELS = 1280 * 720

_IS_WINDOWS = sys.platform == "win32"
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_NEW_GROUP = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
# Host status -> Session.status
_STATUS = {"starting": "starting", "ready": "running", "error": "error", "stopped": "stopped"}


class StreamError(Exception):
    pass


# --- Wire format ---------------------------------------------------------------


def encode(kind: bytes, payload: bytes) -> bytes:
    return HEADER.pack(len(payload), kind[0]) + payload


def encode_json(value: dict, kind: bytes = b"J") -> bytes:
    return encode(kind, json.dumps(value, separators=(",", ":")).encode("utf-8"))


def encode_frame(width: int, height: int, frame: int, fmt: int, pixels: bytes, timestamp: float = 0.0) -> bytes:
    return encode(b"F", FRAME.pack(width, height, frame, fmt, timestamp) + bytes(pixels))


class Reader:
    """Splits a byte stream into (type, payload) messages; payloads are memoryviews."""

    def __init__(self, max_message: int = MAX_MESSAGE):
        self.max_message = max_message
        self._buf = bytearray()

    def feed(self, data) -> list:
        self._buf += data
        out = []
        view = memoryview(self._buf)
        pos = 0
        try:
            while len(view) - pos >= HEADER.size:
                size, kind = HEADER.unpack_from(view, pos)
                if size > self.max_message:
                    raise StreamError(f"Stream message too large ({size} bytes)")
                end = pos + HEADER.size + size
                if end > len(view):
                    break
                out.append((chr(kind), bytes(view[pos + HEADER.size:end])))
                pos = end
        finally:
            view.release()
        if pos:
            del self._buf[:pos]
        return out


def parse_frame(payload) -> tuple:
    """(width, height, frame, format, timestamp_ms, pixels) of an 'F' payload."""
    if len(payload) < FRAME.size:
        raise StreamError(f"Frame message too short ({len(payload)} bytes)")
    width, height, frame, fmt, timestamp = FRAME.unpack_from(payload, 0)
    return width, height, frame, fmt, timestamp, memoryview(payload)[FRAME.size:]


def decode_pixels(fmt: int, width: int, height: int, data) -> tuple:
    """(width, height, RGBA8 memoryview with rows top to bottom) of a frame's pixels."""
    if fmt == FORMAT_RGBA:
        if len(data) != width * height * 4:
            raise StreamError(f"RGBA frame of {width}x{height} has {len(data)} bytes")
        return width, height, memoryview(data).cast("B")
    if fmt != FORMAT_JPEG:
        raise StreamError(f"Unknown frame format {fmt}")
    import imbuf
    import numpy as np

    image = imbuf.load_from_buffer(data)
    try:
        width, height = image.size
        with image.with_buffer() as buf:
            pixels = np.asarray(buf)
            if pixels.dtype != np.uint8:
                raise StreamError("The decoded frame is not 8-bit")
            if pixels.shape[2] == 3:
                pixels = np.concatenate([pixels, np.full(pixels.shape[:2] + (1,), 255, np.uint8)], axis=2)
            # ImBuf rows run bottom to top.
            rgba = np.ascontiguousarray(pixels[::-1])
    finally:
        image.free()
    return width, height, memoryview(rgba).cast("B")


def _jpeg_supported() -> bool:
    try:
        import imbuf

        return hasattr(imbuf, "load_from_buffer")
    except ImportError:
        return False


# --- Keys ------------------------------------------------------------------------

def _key_table() -> dict:
    """Blender event type -> (DOM code, key, key with Shift on a US layout)."""
    table = {}
    for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
        table[letter] = (f"Key{letter}", letter.lower(), letter)
    for i, (name, shifted) in enumerate(zip(("ZERO", "ONE", "TWO", "THREE", "FOUR", "FIVE", "SIX", "SEVEN",
                                             "EIGHT", "NINE"), ")!@#$%^&*(")):
        table[name] = (f"Digit{i}", str(i), shifted)
        table[f"NUMPAD_{i}"] = (f"Numpad{i}", str(i), str(i))
    for i in range(1, 25):
        table[f"F{i}"] = (f"F{i}", f"F{i}", f"F{i}")
    named = {
        "SPACE": ("Space", " ", " "), "RET": ("Enter", "Enter", "Enter"), "ESC": ("Escape", "Escape", "Escape"),
        "TAB": ("Tab", "Tab", "Tab"), "BACK_SPACE": ("Backspace", "Backspace", "Backspace"),
        "DEL": ("Delete", "Delete", "Delete"), "INSERT": ("Insert", "Insert", "Insert"),
        "HOME": ("Home", "Home", "Home"), "END": ("End", "End", "End"),
        "PAGE_UP": ("PageUp", "PageUp", "PageUp"), "PAGE_DOWN": ("PageDown", "PageDown", "PageDown"),
        "LEFT_ARROW": ("ArrowLeft", "ArrowLeft", "ArrowLeft"), "RIGHT_ARROW": ("ArrowRight", "ArrowRight", "ArrowRight"),
        "UP_ARROW": ("ArrowUp", "ArrowUp", "ArrowUp"), "DOWN_ARROW": ("ArrowDown", "ArrowDown", "ArrowDown"),
        "LEFT_SHIFT": ("ShiftLeft", "Shift", "Shift"), "RIGHT_SHIFT": ("ShiftRight", "Shift", "Shift"),
        "LEFT_CTRL": ("ControlLeft", "Control", "Control"), "RIGHT_CTRL": ("ControlRight", "Control", "Control"),
        "LEFT_ALT": ("AltLeft", "Alt", "Alt"), "RIGHT_ALT": ("AltRight", "Alt", "Alt"),
        "OSKEY": ("MetaLeft", "Meta", "Meta"), "APP": ("ContextMenu", "ContextMenu", "ContextMenu"),
        "PAUSE": ("Pause", "Pause", "Pause"), "CAPSLOCK": ("CapsLock", "CapsLock", "CapsLock"),
        "LINE_FEED": ("Enter", "Enter", "Enter"),
        "MINUS": ("Minus", "-", "_"), "EQUAL": ("Equal", "=", "+"), "PLUS": ("Equal", "+", "+"),
        "LEFT_BRACKET": ("BracketLeft", "[", "{"), "RIGHT_BRACKET": ("BracketRight", "]", "}"),
        "BACK_SLASH": ("Backslash", "\\", "|"), "SEMI_COLON": ("Semicolon", ";", ":"),
        "QUOTE": ("Quote", "'", '"'), "ACCENT_GRAVE": ("Backquote", "`", "~"), "COMMA": ("Comma", ",", "<"),
        "PERIOD": ("Period", ".", ">"), "SLASH": ("Slash", "/", "?"), "GRLESS": ("IntlBackslash", "<", ">"),
        "NUMPAD_PERIOD": ("NumpadDecimal", ".", "."), "NUMPAD_SLASH": ("NumpadDivide", "/", "/"),
        "NUMPAD_ASTERIX": ("NumpadMultiply", "*", "*"), "NUMPAD_MINUS": ("NumpadSubtract", "-", "-"),
        "NUMPAD_PLUS": ("NumpadAdd", "+", "+"), "NUMPAD_ENTER": ("NumpadEnter", "Enter", "Enter"),
    }
    table.update(named)
    return table


KEYS = _key_table()


def key_code(event_type: str, shift: bool = False) -> tuple | None:
    """DOM (KeyboardEvent.code, KeyboardEvent.key) of a Blender event type, or None (mouse, NDOF, ...).

    The key is the unshifted one ("a", "1") unless shift is True ("A", "!"; US layout)."""
    entry = KEYS.get(event_type)
    if entry is None:
        return None
    return entry[0], entry[2] if shift else entry[1]


# --- Finding the host ------------------------------------------------------------------


def host_script(project_root=None) -> pathlib.Path | None:
    """gamestudio-stream.js of the project's @gamestudio/devserver, else the GameStudio runtime's."""
    candidates = [pathlib.Path(project_root) / "node_modules" / HOST_SCRIPT] if project_root is not None else []
    try:
        from . import toolchain

        for sdk in (toolchain.sdk(project_root), toolchain.sdk()):
            if sdk is not None:
                candidates.append(pathlib.Path(sdk.packages) / "devserver" / "bin" / HOST_SCRIPT.name)
    except Exception as exc:  # noqa: BLE001 - only a fallback location
        log.debug("No GameStudio runtime for the stream host: %s", exc)
    return next((c for c in candidates if c.is_file()), None)


def _resolvable_from(script: pathlib.Path, package: str) -> bool:
    """True if Node would find `package` from the script (its real path: node_modules links)."""
    try:
        start = script.resolve()
    except OSError:
        start = script
    return any((parent / "node_modules" / package / "package.json").is_file() for parent in start.parents)


def system_browsers_allowed() -> bool:
    """GAMESTUDIO_BROWSER_SYSTEM=0 skips Edge, Chrome and Chromium (tests, and a "computer without a
    browser" run on Windows, where Edge always exists), as the stream host and the MCP server do."""
    return os.environ.get("GAMESTUDIO_BROWSER_SYSTEM", "").strip().lower() not in ("0", "false", "off", "no")


def browser_path() -> str | None:
    """The browser the game runs in, looked up as the stream host and the MCP server look: GAMESTUDIO_BROWSER
    (or GS_BROWSER), else Edge, Chrome or Chromium, else the one GameStudio downloaded (Chrome Headless
    Shell in <user folder>/browser, environment.py)."""
    for env in ("GAMESTUDIO_BROWSER", "GS_BROWSER"):
        value = os.environ.get(env, "").strip()
        if value and os.path.isfile(value):
            return value
    if system_browsers_allowed():
        from .devserver import find_app_browser

        found = find_app_browser()
        if found:
            return found
    try:
        from . import toolchain

        return toolchain.downloaded_browser()
    except Exception:  # noqa: BLE001 - outside the extension (tests)
        return None


def node_command(project_root=None) -> tuple:
    """(node executable or None, environment) for running the host, as the dev server runs npm."""
    try:
        from . import toolchain

        return toolchain.node_executable(project_root), toolchain.child_env(start=project_root)
    except Exception:  # noqa: BLE001 - outside the extension (tests): the system's node
        return shutil.which("node"), dict(os.environ)


def _current_root():
    try:
        from . import project

        return project.project_root()
    except Exception:  # noqa: BLE001 - no bpy or no project
        return None


_available_cache: dict = {}
AVAILABLE_TTL = 10.0


def available(project_root=None) -> tuple:
    """(True, "") when a browser and the project's Node can run the stream host, else (False, why).
    Cached for a few seconds, so UI code can call it while drawing."""
    root = project_root if project_root is not None else _current_root()
    key = str(root)
    cached = _available_cache.get(key)
    if cached is not None and time.monotonic() - cached[0] < AVAILABLE_TTL:
        return cached[1]
    result = _check_available(root)
    _available_cache[key] = (time.monotonic(), result)
    return result


# Why the game cannot stream (available()). They read after "The game cannot stream into Blender (...)":
# GameStudio's setup (environment.py) provides each before Play, so they show only when it was
# declined, failed or is still running. Nothing here asks the user to install anything.
NO_PACKAGES = "the game packages are not set up yet (@gamestudio/devserver)"
NO_PUPPETEER = "the game packages are incomplete (puppeteer-core is missing)"
NO_NODE = "Node.js is not set up yet"
NO_BROWSER = "there is no Chrome, Edge or Chromium, and GameStudio's game browser is not set up"


def _check_available(root) -> tuple:
    script = host_script(root)
    if script is None:
        return False, NO_PACKAGES
    if not _resolvable_from(script, "puppeteer-core"):
        return False, NO_PUPPETEER
    node, _env = node_command(root)
    if not node:
        return False, NO_NODE
    if browser_path() is None:
        return False, NO_BROWSER
    if not _jpeg_supported():
        return False, "this Blender cannot decode JPEG from memory (imbuf.load_from_buffer, Blender 5.2+)"
    return True, ""


def fit_size(width: int, height: int, max_pixels: int = MAX_PIXELS) -> tuple:
    """A frame size with the aspect of width x height and at most max_pixels pixels, for an area
    bigger than the stream should be (Blender scales the frame up when drawing it)."""
    width, height = max(16, int(width)), max(16, int(height))
    if width * height <= max_pixels:
        return width, height
    factor = (max_pixels / (width * height)) ** 0.5
    return max(16, int(width * factor)), max(16, int(height * factor))


# --- Sessions ----------------------------------------------------------------------

_sessions: list = []


def sessions() -> list:
    """Sessions that have not been stopped."""
    return list(_sessions)


def start(url: str | None, width: int, height: int, *, project_root, on_status=None, scale: float = 1.0,
          quality: int = 80, max_fps: int = 60, mode: str | None = None, browser: str | None = None,
          script=None, node: str | None = None, extra_args=()) -> "Session":
    """Spawn the stream host for `url` at width x height frame pixels (`scale`: device pixels per CSS
    pixel), connect and send hello/size. Non-blocking: the connection is made by the session's
    thread and frames arrive later. Without a `url` the browser starts and waits for
    Session.navigate(). on_status(session) is called from poll() on status changes.
    Raises StreamError when the host cannot start."""
    root = pathlib.Path(project_root) if project_root is not None else None
    if root is not None:
        from . import trust

        if not trust.is_trusted(root):  # the host may be the project's (its node_modules), and so is the game
            raise StreamError(trust.ASK)
    script = pathlib.Path(script) if script is not None else host_script(root)
    if script is None:
        raise StreamError(available(root)[1] or "The stream host was not found")
    env = None
    if node is None:
        node, env = node_command(root)
        if not node:
            raise StreamError(NO_NODE[0].upper() + NO_NODE[1:])
    browser = browser or browser_path()
    if browser is None:
        raise StreamError(NO_BROWSER[0].upper() + NO_BROWSER[1:])
    width, height = max(16, int(width)), max(16, int(height))
    argv = [node, str(script), *(["--url", url] if url else []), "--width", str(width), "--height", str(height),
            "--scale", f"{float(scale):g}", "--quality", str(int(quality)), "--max-fps", str(int(max_fps)),
            "--parent-pid", str(os.getpid()), "--browser", browser]
    if mode:
        argv += ["--mode", mode]
    argv += list(extra_args)
    session = Session(argv, env=env, cwd=root, size=(width, height, float(scale)), quality=int(quality),
                      max_fps=int(max_fps), on_status=on_status, url=url or "")
    session._spawn()
    _sessions.append(session)
    return session


def _status_callback(on_status):
    """on_status(session), or on_status(status, message) for a two-argument callable."""
    if on_status is None:
        return None
    try:
        params = [p for p in inspect.signature(on_status).parameters.values()
                  if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD) and p.default is p.empty]
        two = len(params) >= 2
    except (TypeError, ValueError):
        two = False
    return (lambda s: on_status(s.status, s.message)) if two else on_status


class Session:
    """One stream: the host process, its connection and the newest frame. Main thread API except
    where noted; the reader thread only touches the fields guarded by _lock."""

    def __init__(self, argv, *, env=None, cwd=None, size=(960, 540, 1.0), quality=80, max_fps=60, on_status=None,
                 url=""):
        self.status = "starting"
        self.message = "Starting the game stream"
        self.fps = 0.0
        self.frame_id = 0
        self.mode = ""             # "headless" | "offscreen", from the host
        self.audio = False         # the game's sound plays through the default output device
        self.renderer = ""         # the browser's WebGL renderer
        self.gpu = None            # False: software rendering (slow)
        self.browser = ""
        self.latency_ms = 0.0      # draw time in the browser -> frame ready here (same clock)
        self.decode_ms = 0.0       # decode time of the last frame (reader thread)
        self.paused = False
        self.suspended = False     # paused, and the page hidden (set_paused(suspend=True))
        self.url = url             # the game's address ("" until navigate() gives one)
        self.agent = ""            # the host's mark in the page's user agent, from its status
        self.keep_on_load = False  # loading a file does not stop this session (its owner decides)
        # Also make float32 frames (0..1) in the reader thread, for gpu.types.GPUTexture, whose
        # Python upload takes FLOAT buffers only: 1.3 ms at 960x540 and 2.8 ms at 1280x720 that
        # poll()'s caller does not spend. See latest_frame_float().
        self.float_frames = False
        self.pid = 0
        self.log_path = None
        self._argv = argv
        self._env = env
        self._cwd = cwd
        self._size = size
        self._quality = quality
        self._max_fps = max_fps
        self._on_status = _status_callback(on_status)
        self._spawn_url = url      # the address on the host's command line
        self._proc = None
        self._log_file = None
        self._sock = None
        self._send_lock = threading.Lock()
        self._lock = threading.Lock()
        self._thread = None
        self._stopped = False
        self._started = time.monotonic()
        # Written by the reader thread under _lock, taken over by poll():
        self._pending_frame = None       # (width, height, memoryview, floats, latency_ms, decode_ms)
        self._pending_status = None      # host status dict
        self._thread_error = None
        self._host_done = False          # the host sent its last status (stopped or error)
        self._arrivals = collections.deque()  # monotonic times of received frames
        # Main thread:
        self._frame = None
        self._frame_float = None
        self._last_reported = None
        self._error = None               # (message, monotonic time) of a connection error

    # --- Process ------------------------------------------------------------------------

    def _spawn(self) -> None:
        log_path = self._open_log()
        kwargs = {"creationflags": _NO_WINDOW | _NEW_GROUP} if _IS_WINDOWS else {"start_new_session": True}
        try:
            self._proc = subprocess.Popen(
                self._argv, cwd=str(self._cwd) if self._cwd else None, env=self._env, stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=self._log_file or subprocess.DEVNULL, **kwargs)
        except OSError as exc:
            self._close_log()
            raise StreamError(f"Could not start the stream host: {exc}") from exc
        self.pid = self._proc.pid
        log.info("Started the game stream host (pid %d); log in %s", self.pid, log_path or "(none)")
        self._thread = threading.Thread(target=self._run, name="gamestudio-stream", daemon=True)
        self._thread.start()

    def _open_log(self):
        folder = None
        if self._cwd is not None:
            try:
                from . import project

                folder = project.gs_dir(self._cwd)
            except Exception:  # noqa: BLE001 - outside Blender
                folder = pathlib.Path(self._cwd) / ".gamestudio"
        if folder is None:
            return None
        try:
            folder.mkdir(parents=True, exist_ok=True)
            self.log_path = folder / LOG_NAME
            self._log_file = open(self.log_path, "w", encoding="utf-8", errors="replace")
            self._log_file.write(f"# {' '.join(map(str, self._argv))}\n")
            self._log_file.flush()
        except OSError:
            self._log_file = None
            self.log_path = None
        return self.log_path

    def _close_log(self) -> None:
        if self._log_file is not None:
            try:
                self._log_file.close()
            except OSError:
                pass
            self._log_file = None

    def log_tail(self, lines: int = 8) -> str:
        if self.log_path is None:
            return ""
        try:
            with open(self.log_path, "r", encoding="utf-8", errors="replace") as f:
                output = [line for line in f.readlines() if not line.startswith("# ")]  # not the command line
        except OSError:
            return ""
        return "".join(output[-lines:]).strip()

    # --- Reader thread --------------------------------------------------------------------

    def _run(self) -> None:
        try:
            line = self._proc.stdout.readline()
            if not line:
                raise StreamError("The stream host exited before it started")
            info = json.loads(line.decode("utf-8", "replace"))
            sock = socket.create_connection(("127.0.0.1", int(info["port"])), timeout=5.0)
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            formats = ["jpeg", "rgba"] if _jpeg_supported() else ["rgba"]
            width, height, scale = self._size
            with self._send_lock:
                if self._stopped:
                    sock.close()
                    return
                self._sock = sock
                sock.sendall(encode_json({"type": "hello", "token": info["token"], "formats": formats})
                             + encode_json({"type": "size", "width": width, "height": height, "scale": scale})
                             + encode_json({"type": "quality", "quality": self._quality, "max_fps": self._max_fps})
                             + (encode_json({"type": "pause", "suspend": self.suspended}) if self.paused else b"")
                             # An address navigate() was given before the connection.
                             + (encode_json({"type": "navigate", "url": self.url})
                                if self.url and self.url != self._spawn_url else b""))
            self._receive(sock)
        except Exception as exc:  # noqa: BLE001 - reported through poll()
            if not self._stopped:
                with self._lock:
                    self._thread_error = str(exc) or type(exc).__name__
        finally:
            try:
                self._proc.stdout.close()
            except (OSError, AttributeError):
                pass

    def _receive(self, sock) -> None:
        reader = Reader()
        chunk = bytearray(1 << 20)
        view = memoryview(chunk)
        while not self._stopped:
            ready, _, _ = select.select([sock], [], [], 0.25)
            if not ready:
                continue
            try:
                n = sock.recv_into(view)
            except (BlockingIOError, InterruptedError, socket.timeout):
                continue
            except OSError:
                if self._stopped:
                    return
                raise
            if n == 0:
                if not self._host_done:
                    with self._lock:
                        self._thread_error = "The stream host closed the connection"
                return
            frame = None
            for kind, payload in reader.feed(view[:n]):
                if kind == "F":
                    frame = payload        # only the newest frame of a batch is decoded
                    self._arrivals.append(time.monotonic())
                elif kind == "S":
                    try:
                        status = json.loads(payload.decode("utf-8", "replace"))
                    except ValueError:
                        continue
                    with self._lock:
                        self._pending_status = status
                        self._host_done = status.get("status") in ("stopped", "error")
            if frame is not None and not self.paused:
                try:
                    self._decode(frame)
                except Exception as exc:  # noqa: BLE001 - a bad frame is skipped, the next one may be fine
                    log.debug("Game stream: skipped a frame: %s", exc)

    def _decode(self, payload) -> None:
        t0 = time.perf_counter()
        width, height, _number, fmt, timestamp, data = parse_frame(payload)
        width, height, pixels = decode_pixels(fmt, width, height, data)
        floats = None
        if self.float_frames:
            import numpy as np

            floats = memoryview(np.multiply(np.frombuffer(pixels, np.uint8), np.float32(1.0 / 255.0),
                                            dtype=np.float32))
        decode_ms = (time.perf_counter() - t0) * 1000.0
        latency_ms = max(0.0, time.time() * 1000.0 - timestamp) if timestamp > 0 else 0.0
        with self._lock:
            self._pending_frame = (width, height, pixels, floats, latency_ms, decode_ms)

    # --- Main thread API ---------------------------------------------------------------------

    def poll(self) -> None:
        """Takes over the newest frame and status from the reader thread. Non-blocking; call it
        from a Blender timer."""
        if self._stopped:
            return
        with self._lock:
            frame, self._pending_frame = self._pending_frame, None
            status, self._pending_status = self._pending_status, None
            error, self._thread_error = self._thread_error, None
        now = time.monotonic()
        arrivals = self._arrivals
        while arrivals and now - arrivals[0] > FPS_WINDOW:
            arrivals.popleft()
        self.fps = float(len(arrivals)) / FPS_WINDOW
        if frame is not None:
            width, height, pixels, floats, latency_ms, decode_ms = frame
            self._frame = (width, height, pixels)
            self._frame_float = (width, height, floats) if floats is not None else None
            self.frame_id += 1
            self.decode_ms = decode_ms
            self.latency_ms = latency_ms
        if status is not None:
            self._apply_status(status)
        if error is not None and self._error is None:
            self._error = (error, now)
        exited = self._proc is not None and self._proc.poll() is not None
        if self._stopped:
            pass  # the host reported stopped or error
        elif exited and self.status in ("starting", "running"):
            self._fail(f"The stream host exited (code {self._proc.returncode})")
        elif self._error is not None and now - self._error[1] > ERROR_GRACE:
            self._fail(self._error[0])  # the connection broke but the host still runs
        elif self.status == "starting" and self._sock is None and now - self._started > START_TIMEOUT:
            self._fail(f"The stream host did not start within {int(START_TIMEOUT)} s")
        self._report()

    def _apply_status(self, status: dict) -> None:
        self.status = _STATUS.get(status.get("status"), self.status)
        self.message = str(status.get("message") or self.message)
        self.mode = str(status.get("mode") or self.mode)
        self.audio = bool(status.get("audio", self.audio))
        self.renderer = str(status.get("renderer") or self.renderer)
        self.browser = str(status.get("browser") or self.browser)
        self.agent = str(status.get("agent") or self.agent)
        if status.get("gpu") is not None:
            self.gpu = bool(status["gpu"])
        if self.status in ("error", "stopped"):
            self._shutdown(wait=False)

    def _fail(self, message: str) -> None:
        tail = self.log_tail()
        if tail and self.status != "error":
            message = f"{message}. Last output:\n{tail}"
        self.status = "error"
        self.message = message
        log.error("Game stream: %s", message)
        self._shutdown(wait=False)

    def _report(self) -> None:
        key = (self.status, self.message)
        if key == self._last_reported:
            return
        self._last_reported = key
        if self._on_status is not None:
            try:
                self._on_status(self)
            except Exception:  # noqa: BLE001 - a UI callback must not break polling
                log.exception("Game stream status callback failed")

    def latest_frame(self) -> tuple | None:
        """(width, height, RGBA8 bytes with rows top to bottom) of the newest frame, or None. Valid
        until the next poll() replaces it."""
        return self._frame

    def latest_frame_float(self) -> tuple | None:
        """(width, height, float32 RGBA 0..1 with rows top to bottom) of the newest frame when
        float_frames is on, else None. Same frame as latest_frame()."""
        return self._frame_float

    def _send(self, message: dict) -> bool:
        with self._send_lock:
            sock = self._sock
            if sock is None or self._stopped:
                return False
            try:
                sock.sendall(encode_json(message))
                return True
            except OSError as exc:
                log.debug("Game stream send failed: %s", exc)
                return False

    def resize(self, width: int, height: int, scale: float | None = None) -> None:
        """Frame size in pixels; `scale` is device pixels per CSS pixel (the page's devicePixelRatio)."""
        scale = self._size[2] if scale is None else float(scale)
        size = (max(16, int(width)), max(16, int(height)), scale)
        if size == self._size:
            return
        self._size = size
        self._send({"type": "size", "width": size[0], "height": size[1], "scale": scale})

    def set_quality(self, quality: int | None = None, max_fps: int | None = None) -> None:
        """JPEG quality (1-100) and the frame rate cap (0: none)."""
        message = {"type": "quality"}
        if quality is not None:
            self._quality = message["quality"] = int(quality)
        if max_fps is not None:
            self._max_fps = message["max_fps"] = int(max_fps)
        self._send(message)

    def send_mouse(self, kind: str, x: float, y: float, button: int = 0, dx: float = 0.0, dy: float = 0.0,
                   shift: bool = False, ctrl: bool = False, alt: bool = False) -> None:
        """kind: "move" | "down" | "up" | "wheel"; x, y in frame pixels from the top left; button
        0 left, 1 middle, 2 right; dx, dy: wheel distance in pixels (positive dy scrolls down)."""
        self._send({"type": "mouse", "kind": kind, "x": float(x), "y": float(y), "button": int(button),
                    "dx": float(dx), "dy": float(dy), "shift": bool(shift), "ctrl": bool(ctrl), "alt": bool(alt)})

    def send_key(self, kind: str, code: str, key: str, shift=False, ctrl=False, alt=False, repeat=False) -> None:
        """kind: "down" | "up"; code and key as DOM KeyboardEvent.code and .key (see key_code())."""
        self._send({"type": "key", "kind": kind, "code": code, "key": key, "shift": bool(shift),
                    "ctrl": bool(ctrl), "alt": bool(alt), "repeat": bool(repeat)})

    def send_text(self, text: str) -> None:
        """Types text into the focused element of the page (Input.insertText)."""
        if text:
            self._send({"type": "text", "text": text})

    def release_input(self) -> None:
        """Lifts every key and mouse button the game still sees as held (focus left the area)."""
        self._send({"type": "release"})

    def navigate(self, url: str) -> None:
        """Load the game at `url`: its address once the dev server has one (a host started without
        one waits for it), or a new one. Sent when the connection is up."""
        if not url or url == self.url:
            return
        with self._send_lock:
            self.url = url
            sock = self._sock
            if sock is None or self._stopped:
                return  # the reader thread sends it with the hello
            try:
                sock.sendall(encode_json({"type": "navigate", "url": url}))
            except OSError as exc:
                log.debug("Game stream send failed: %s", exc)

    def set_paused(self, paused: bool, suspend: bool = False) -> None:
        """Stop or restart frames (the game keeps running); pausing also releases held input. With
        `suspend` the host also hides the page once it has loaded (its browser window minimized):
        it renders nothing until frames restart, for a game nobody sees or plays, while its scripts
        and live link keep running."""
        paused = bool(paused)
        suspend = paused and bool(suspend)
        if (paused, suspend) == (self.paused, self.suspended):
            return
        self.paused, self.suspended = paused, suspend
        self._send({"type": "pause", "suspend": suspend} if paused else {"type": "resume"})

    def stop(self, wait: bool = False) -> None:
        """Ask the host to exit, close the socket, and kill the process tree if it lingers (in the
        background unless wait=True)."""
        if self._stopped:
            return
        self._send({"type": "stop"})
        self._shutdown(wait=wait)
        if self.status != "error":
            self.status = "stopped"
            self.message = "Stopped"
        self._report()

    def _shutdown(self, wait: bool) -> None:
        if self._stopped:
            return
        self._stopped = True
        with self._send_lock:
            sock, self._sock = self._sock, None
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            sock.close()
        with self._lock:
            self._pending_frame = None
        if self in _sessions:
            _sessions.remove(self)
        proc = self._proc
        if proc is None:
            self._close_log()
            return
        if wait:
            _reap(proc, STOP_TIMEOUT)
            self._close_log()
        else:
            def reap():
                _reap(proc, STOP_TIMEOUT)
                self._close_log()

            threading.Thread(target=reap, name="gamestudio-stream-stop", daemon=True).start()

    @property
    def alive(self) -> bool:
        """True while the host process runs."""
        return self._proc is not None and self._proc.poll() is None


def _reap(proc, timeout: float) -> None:
    """Waits for the host to exit after stop, then kills its process tree."""
    try:
        proc.wait(timeout=timeout)
        return
    except subprocess.TimeoutExpired:
        pass
    kill_tree(proc.pid)
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


def kill_tree(pid: int) -> None:
    """Kills the host and its browser (taskkill /T on Windows; the process group elsewhere, and the
    host's SIGTERM handler closes the browser, which puppeteer starts in its own group)."""
    if _IS_WINDOWS:
        from .toolchain import system_tool  # by its path: a taskkill.exe in the current folder would run

        subprocess.run([system_tool("taskkill.exe"), "/T", "/F", "/PID", str(pid)], stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, creationflags=_NO_WINDOW, timeout=15, check=False)
        return
    import signal

    try:
        os.killpg(os.getpgid(pid), signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        pass


def stop_all(wait: bool = True) -> int:
    """Stops every session (Blender quits, a file is loaded); returns how many were running."""
    running = list(_sessions)
    for session in running:
        try:
            session.stop(wait=False)
        except Exception:  # noqa: BLE001 - cleanup must go on
            log.exception("Could not stop a game stream")
    if wait:
        for session in running:
            if session._proc is not None:
                _reap(session._proc, STOP_TIMEOUT)
    _sessions.clear()
    return len(running)


atexit.register(stop_all)


# --- Blender handlers ------------------------------------------------------------------

@persistent
def _on_load_pre(*_args):
    """Another file: its sessions end, except the ones kept (their owner ends them if it must)."""
    for session in [s for s in _sessions if not s.keep_on_load]:
        try:
            session.stop(wait=False)
        except Exception:  # noqa: BLE001 - loading must go on
            log.exception("Could not stop a game stream")


@persistent
def _on_exit(*_args):
    stop_all(wait=True)


def register():
    import bpy

    for handlers, fn in ((bpy.app.handlers.load_pre, _on_load_pre), (bpy.app.handlers.exit_pre, _on_exit)):
        if fn not in handlers:
            handlers.append(fn)


def unregister():
    import bpy

    for handlers, fn in ((bpy.app.handlers.load_pre, _on_load_pre), (bpy.app.handlers.exit_pre, _on_exit)):
        if fn in handlers:
            handlers.remove(fn)
    stop_all(wait=True)
