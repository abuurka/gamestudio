# SPDX-License-Identifier: GPL-3.0-or-later
"""Live link client: Blender <-> the dev server's hub (docs/SPEC.md §5).

A non-blocking TCP socket, polled with `selectors` from pump(). In the UI a
bpy.app.timers callback calls pump() every 20 ms; timers never fire in
--background, so headless code and tests call pump() themselves. No threads:
every handler and request callback runs on the main thread inside pump().

Framing is NDJSON. The first message is a hello with the token from
.gamestudio/session.json; the client counts as connected once the hub's
welcome arrives. While a session file exists, the client reconnects every
~2 s after a failure or disconnect.

Besides the protocol messages, handlers can subscribe to two local events:
"link.connected" and "link.disconnected". "*" receives every message.
"""

import collections
import errno
import itertools
import json
import selectors
import socket
import threading
import time

import bpy
from bpy.app.handlers import persistent

from . import project
from .log import get_logger
from .util import is_main_thread, tag_redraw

log = get_logger(__name__)

PROTOCOL = 1
TIMER_INTERVAL = 0.02
IDLE_INTERVAL = 0.1
RECONNECT_INTERVAL = 2.0
CONNECT_TIMEOUT = 3.0
HANDSHAKE_TIMEOUT = 5.0
MAX_LINE_BYTES = 64 * 1024 * 1024
MAX_READ_PER_PUMP = 4 * 1024 * 1024
LOG_LIMIT = 500
REDRAW_INTERVAL = 0.1  # at most 10 sidebar redraws a second, however chatty the game is

_CONNECT_PENDING = {0, errno.EINPROGRESS, errno.EWOULDBLOCK, errno.EALREADY, getattr(errno, "WSAEWOULDBLOCK", 10035)}
_REFUSED = {errno.ECONNREFUSED, getattr(errno, "WSAECONNREFUSED", 10061)}


def session_from_project() -> dict | None:
    """Connection info for the current project's dev server, or None."""
    root = project.project_root()
    if root is None:
        return None
    session = project.read_session(root)
    if session is None:
        return None
    return {
        "host": "127.0.0.1",
        "port": session["editorPort"],
        "token": session["token"],
        "url": session.get("url"),
        "gamePort": session.get("gamePort"),
        "pid": session.get("pid"),
        "root": str(root),
    }


def _client_name() -> str:
    return "blender " + bpy.app.version_string.split(" ")[0]


class LinkClient:
    """One connection to the hub. See the module docstring for the threading model."""

    def __init__(self, role: str = "editor", session_provider=session_from_project):
        self.role = role
        self.connected = False
        self.state = "idle"            # idle | connecting | handshake | connected
        self.peer_id = None
        self.peers = []
        self.logs = collections.deque(maxlen=LOG_LIMIT)
        self.session = None            # the session dict used for the current connection
        self.last_error = ""
        self.game_state = None         # last game.status state
        self.game_ready = None         # last game.ready message
        self.stats = None              # last stats message
        self.auto_connect = True
        self.reconnect_interval = RECONNECT_INTERVAL
        self.session_provider = session_provider

        self._sock = None
        self._sel = None
        self._token = None
        self._rbuf = bytearray()
        self._wbuf = bytearray()
        self._lock = threading.Lock()
        self._handlers = {}
        self._pending = {}             # request id -> (callback, deadline)
        self._due = collections.deque()  # (callback, response) to run in pump
        self._ids = itertools.count(1)
        self._next_attempt = 0.0
        self._deadline = 0.0
        self._failures = 0
        self._dirty_ui = False
        self._last_redraw = 0.0

    # --- Handlers --------------------------------------------------------------

    def on(self, t: str, handler) -> None:
        """Call handler(msg) on the main thread for each message of type t."""
        handlers = self._handlers.setdefault(t, [])
        if handler not in handlers:
            handlers.append(handler)

    def off(self, t: str, handler) -> None:
        handlers = self._handlers.get(t, [])
        if handler in handlers:
            handlers.remove(handler)

    def _emit(self, t: str, msg: dict) -> None:
        for handler in list(self._handlers.get(t, ())) + list(self._handlers.get("*", ())):
            try:
                handler(msg)
            except Exception:
                log.exception("Link handler for %r failed", t)

    # --- Sending ---------------------------------------------------------------

    def send(self, msg: dict) -> bool:
        """Queue a message. Thread-safe and non-blocking.

        Returns False (and drops the message) when not connected.
        """
        if self.state != "connected":
            return False
        self._enqueue(msg)
        return True

    def request(self, msg: dict, callback, timeout: float = 5.0) -> str:
        """Send a request and call callback(response | None) on the main thread.

        None means the request timed out or the link is (or went) down.
        Returns the request id.
        """
        req_id = msg.get("id") or f"blender-{next(self._ids)}"
        msg = dict(msg, id=req_id)
        if self.state != "connected":
            self._due.append((callback, None))
            return req_id
        with self._lock:
            self._pending[req_id] = (callback, time.monotonic() + timeout)
        self._enqueue(msg)
        return req_id

    def _enqueue(self, msg: dict) -> None:
        data = (json.dumps(msg, separators=(",", ":"), ensure_ascii=False) + "\n").encode("utf-8")
        with self._lock:
            self._wbuf += data
        if is_main_thread():
            self._flush()

    def _flush(self) -> None:
        if self._sock is None or self.state not in ("handshake", "connected"):
            return
        error = None
        with self._lock:
            while self._wbuf:
                try:
                    sent = self._sock.send(self._wbuf)
                except (BlockingIOError, InterruptedError):
                    break
                except OSError as exc:
                    error = exc
                    break
                del self._wbuf[:sent]
        if error is not None:
            self._drop(f"Connection lost ({error})")

    # --- Connection ------------------------------------------------------------

    def connect(self, port: int, token: str, host: str = "127.0.0.1", session: dict | None = None) -> None:
        """Start connecting to a hub. Completion happens in pump()."""
        if self._sock is not None:
            self._close_socket()
        self._token = token
        self.session = dict(session) if session else {"host": host, "port": port, "token": token}
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setblocking(False)
        try:
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except OSError:
            pass
        err = sock.connect_ex((host, port))
        if err not in _CONNECT_PENDING:
            sock.close()
            self._drop(self._connect_error_text(err, host, port), error=True)
            return
        self._sock = sock
        self._sel = selectors.DefaultSelector()
        self._sel.register(sock, selectors.EVENT_READ | selectors.EVENT_WRITE)
        self.state = "connecting"
        self._deadline = time.monotonic() + CONNECT_TIMEOUT
        self._dirty_ui = True

    def disconnect(self, reason: str = "") -> None:
        """Close the connection. Auto-connect (if on) retries after the interval."""
        self._drop(reason, deliberate=True)

    def reconnect_now(self) -> None:
        """Try to (re)connect on the next pump instead of waiting for the interval."""
        self._next_attempt = 0.0

    def reset(self) -> None:
        """Drop the connection and reconnect for the current project (file changed)."""
        self._drop("")
        self.session = None
        self._failures = 0
        self.reconnect_now()

    def game_peers(self) -> list:
        return [p for p in self.peers if isinstance(p, dict) and p.get("role") == "game"]

    @property
    def status_text(self) -> str:
        if self.state == "connected":
            n = len(self.game_peers())
            return f"Connected, {n} game{'s' if n != 1 else ''}"
        if self.state in ("connecting", "handshake"):
            return "Connecting..."
        return "Disconnected"

    def _connect_error_text(self, err: int, host: str, port: int) -> str:
        if err in _REFUSED:
            return f"Nothing is listening on {host}:{port} (is the dev server running?)"
        return f"Could not connect to {host}:{port} (error {err})"

    def _close_socket(self) -> None:
        if self._sel is not None:
            try:
                self._sel.close()
            except (OSError, ValueError):
                pass
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
        self._sock = None
        self._sel = None

    def _drop(self, reason: str, error: bool = False, deliberate: bool = False) -> None:
        was_connected = self.connected
        had_socket = self._sock is not None
        self._close_socket()
        self.state = "idle"
        self.connected = False
        self.peer_id = None
        self.peers = []
        self.game_state = None
        self._rbuf.clear()
        with self._lock:
            self._wbuf.clear()
            pending, self._pending = self._pending, {}
        for callback, _deadline in pending.values():
            self._due.append((callback, None))
        self._next_attempt = time.monotonic() + self.reconnect_interval
        if error:
            self.last_error = reason
        if reason and deliberate:
            log.info("Live link closed: %s", reason)
        elif reason and (had_socket or error):
            # A stale session file makes every retry fail; say so once, not every 2 s.
            self._failures += 1
            if was_connected or self._failures == 1:
                log.warning("Live link: %s", reason)
            else:
                log.debug("Live link: %s", reason)
        if was_connected:
            self._emit("link.disconnected", {"t": "link.disconnected", "reason": reason})
        self._dirty_ui = True

    # --- Pump ------------------------------------------------------------------

    def pump(self) -> None:
        """Do all pending I/O and run handlers. Main thread only."""
        now = time.monotonic()
        if self.state == "idle" and self.auto_connect and now >= self._next_attempt:
            self._next_attempt = now + self.reconnect_interval
            self._auto_connect()
        if self._sock is not None:
            self._poll()
        self._check_timeouts(time.monotonic())
        while self._due:
            callback, response = self._due.popleft()
            try:
                callback(response)
            except Exception:
                log.exception("Link request callback failed")
        if self._dirty_ui and time.monotonic() - self._last_redraw >= REDRAW_INTERVAL:
            self._dirty_ui = False
            self._last_redraw = time.monotonic()
            tag_redraw()

    def _auto_connect(self) -> None:
        if self.session_provider is None:
            return
        try:
            session = self.session_provider()
        except Exception:
            log.exception("Reading the dev-server session failed")
            return
        if session:
            self.connect(session["port"], session["token"], session.get("host", "127.0.0.1"), session)

    def _poll(self) -> None:
        try:
            events = self._sel.select(timeout=0)
        except (OSError, ValueError) as exc:
            self._drop(f"Socket error ({exc})")
            return
        for _key, mask in events:
            if self._sock is None:
                return
            if self.state == "connecting":
                err = self._sock.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR)
                if err:
                    session = self.session or {}
                    self._drop(self._connect_error_text(err, session.get("host", "?"), session.get("port", 0)),
                               error=True)
                    return
                if mask & (selectors.EVENT_WRITE | selectors.EVENT_READ):
                    self._on_socket_connected()
            if self._sock is not None and mask & selectors.EVENT_READ and self.state != "connecting":
                self._read()
        self._flush()

    def _on_socket_connected(self) -> None:
        self.state = "handshake"
        self._deadline = time.monotonic() + HANDSHAKE_TIMEOUT
        self._sel.modify(self._sock, selectors.EVENT_READ)
        self._enqueue({
            "t": "hello",
            "role": self.role,
            "token": self._token,
            "protocol": PROTOCOL,
            "client": _client_name(),
        })

    def _read(self) -> None:
        closed = None
        total = 0
        while total < MAX_READ_PER_PUMP:
            try:
                data = self._sock.recv(65536)
            except (BlockingIOError, InterruptedError):
                break
            except OSError as exc:
                closed = f"Connection lost ({exc})"
                break
            if not data:
                closed = "The dev server closed the connection"
                break
            total += len(data)
            self._rbuf += data
        self._process_lines()
        if closed and self._sock is not None:
            self._drop(closed)

    def _process_lines(self) -> None:
        while True:
            newline = self._rbuf.find(b"\n")
            if newline < 0:
                if len(self._rbuf) > MAX_LINE_BYTES:
                    self._drop("Message from the hub is too large", error=True)
                return
            line = bytes(self._rbuf[:newline]).strip()
            del self._rbuf[:newline + 1]
            if not line:
                continue
            try:
                msg = json.loads(line.decode("utf-8"))
            except (UnicodeDecodeError, ValueError):
                log.warning("Ignoring malformed message from the hub: %r", line[:200])
                continue
            if isinstance(msg, dict) and isinstance(msg.get("t"), str):
                self._dispatch(msg)
            if self._sock is None:
                return

    def _check_timeouts(self, now: float) -> None:
        if self.state == "connecting" and now > self._deadline:
            self._drop("Timed out connecting to the dev server", error=True)
        elif self.state == "handshake" and now > self._deadline:
            self._drop("The dev server did not answer the hello", error=True)
        expired = []
        with self._lock:
            for req_id, (callback, deadline) in list(self._pending.items()):
                if now > deadline:
                    expired.append(callback)
                    del self._pending[req_id]
        for callback in expired:
            self._due.append((callback, None))

    # --- Incoming messages -----------------------------------------------------

    def _dispatch(self, msg: dict) -> None:
        t = msg["t"]
        is_hub_error = t == "error" and "code" in msg and "from" not in msg
        if is_hub_error and (self.state == "handshake" or msg.get("code") in ("auth", "protocol")):
            text = f"The dev server rejected the link ({msg.get('code')}): {msg.get('msg', '')}"
            self._drop(text, error=True)
            return
        if self.state == "handshake" and t == "welcome":
            self._on_welcome(msg)
            return

        if t == "peers":
            self.peers = list(msg.get("peers") or [])
            self._dirty_ui = True
        elif t == "ping":
            self.send({"t": "pong"})
        elif t in ("log", "error"):
            entry = dict(msg)
            entry.setdefault("level", "error" if t == "error" else "info")
            entry["time"] = time.time()
            self.logs.append(entry)
            self._dirty_ui = True
        elif t == "game.status":
            self.game_state = msg.get("state")
            self._dirty_ui = True
        elif t == "game.ready":
            self.game_ready = msg
            self._dirty_ui = True
        elif t == "stats":
            self.stats = msg

        req_id = msg.get("re")
        if req_id is not None:
            with self._lock:
                pending = self._pending.pop(req_id, None)
            if pending is not None:
                try:
                    pending[0](msg)
                except Exception:
                    log.exception("Link request callback failed")
                return
        self._emit(t, msg)

    def _on_welcome(self, msg: dict) -> None:
        if msg.get("protocol", PROTOCOL) != PROTOCOL:
            self._drop(f"Protocol mismatch: hub speaks {msg.get('protocol')}, Blender speaks {PROTOCOL}", error=True)
            return
        self.state = "connected"
        self.connected = True
        self.peer_id = msg.get("peer")
        self.peers = list(msg.get("peers") or [])
        self.last_error = ""
        self._failures = 0
        self._dirty_ui = True
        log.info("Live link connected as %s (%d game peer(s))", self.peer_id, len(self.game_peers()))
        self._emit("welcome", msg)
        self._emit("link.connected", {"t": "link.connected", "peer": self.peer_id})


# --- Module-level client ------------------------------------------------------

_client = LinkClient()


def get_link() -> LinkClient | None:
    """The add-on's link client (always present; check .connected)."""
    return _client


def pump() -> None:
    _client.pump()


def _timer():
    try:
        _client.pump()
    except Exception:
        log.exception("Live link pump failed")
    return TIMER_INTERVAL if _client.state != "idle" else IDLE_INTERVAL


@persistent
def _on_load_post(*_args):
    _client.reset()


def register():
    _client.auto_connect = True
    if _on_load_post not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_on_load_post)
    if not bpy.app.timers.is_registered(_timer):
        bpy.app.timers.register(_timer, first_interval=0.2, persistent=True)


def unregister():
    if bpy.app.timers.is_registered(_timer):
        bpy.app.timers.unregister(_timer)
    if _on_load_post in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_on_load_post)
    _client.auto_connect = False
    _client.disconnect("")
    _client.session = None
