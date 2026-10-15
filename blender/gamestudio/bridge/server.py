# SPDX-License-Identifier: GPL-3.0-or-later
"""The bridge's socket server: newline-delimited JSON-RPC 2.0 on 127.0.0.1.

No threads: the listening socket and every connection are non-blocking and polled with
`selectors` from pump(), which runs on Blender's main thread (a bpy.app.timers callback in
the UI; serve_forever() or the tests in --background, where timers never fire). Requests are
therefore executed one at a time, in arrival order, on the main thread.

Authentication: the first request on a connection must be
    {"jsonrpc": "2.0", "id": 1, "method": "hello", "params": {"token": "<token>", "client": "..."}}
with the token from the discovery file. Anything else gets one error and the connection is
closed: another method or a wrong token (-32001), or a line that is not a JSON-RPC request,
such as the request line of a web page's HTTP POST to the port. The MCP server adds
"agent": {"name", "version"}, its own MCP client (e.g. claude-code), so Blender can say who
is connected.
"""

import errno
import hmac
import json
import os
import secrets
import selectors
import socket
import time
import traceback

from ..core.log import get_logger

log = get_logger(__name__)

BRIDGE_PROTOCOL = 1
MAX_LINE_BYTES = 32 * 1024 * 1024
MAX_READ_PER_PUMP = 8 * 1024 * 1024

# JSON-RPC error codes. -32000 is a tool error: the message says what failed and how to fix it.
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603
TOOL_ERROR = -32000
UNAUTHORIZED = -32001
PAUSED_BY_USER = -32002  # the person paused the agent in Blender (activity.py); read-only calls still work


class BridgeError(Exception):
    """An error for the agent: `message` says what failed, `hint` how to fix it."""

    def __init__(self, message: str, hint: str | None = None, code: int = TOOL_ERROR, data: dict | None = None):
        super().__init__(message)
        self.message = message
        self.hint = hint
        self.code = code
        self.data = data or {}

    def to_error(self) -> dict:
        data = dict(self.data)
        if self.hint:
            data["hint"] = self.hint
        error = {"code": self.code, "message": self.message}
        if data:
            error["data"] = data
        return error


class Connection:
    def __init__(self, sock: socket.socket, addr):
        self.sock = sock
        self.addr = addr
        self.rbuf = bytearray()
        self.wbuf = bytearray()
        self.authed = False
        self.client = ""
        self.agent = None          # {name, version} of the MCP client behind the MCP server, if it said
        self.connected_at = 0.0
        self.closing = False       # close once wbuf is flushed
        self.opened = time.monotonic()


class BridgeServer:
    """Owns the sockets. `dispatch(method, params, conn)` executes authenticated requests."""

    def __init__(self, dispatch, token: str | None = None, host: str = "127.0.0.1", port: int = 0):
        self.dispatch = dispatch
        self.token = token or secrets.token_hex(16)
        self.host = host
        self.requested_port = port
        self.port = 0
        self.started_at = time.time()
        self.requests = 0
        self.last_request = 0.0
        self._listener = None
        self._sel = None
        self._conns: dict[int, Connection] = {}
        self._pumping = False

    # --- Lifecycle -------------------------------------------------------------

    @property
    def running(self) -> bool:
        return self._listener is not None

    @property
    def clients(self) -> int:
        return sum(1 for c in self._conns.values() if c.authed)

    def client_infos(self) -> list[dict]:
        """The authenticated connections, oldest first: {client, agent, connected_at}."""
        conns = sorted((c for c in self._conns.values() if c.authed), key=lambda c: c.opened)
        return [{"client": c.client, "agent": c.agent, "connected_at": c.connected_at} for c in conns]

    def start(self) -> None:
        if self._listener is not None:
            return
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        if os.name == "nt":
            # Never share the port with another process (SO_REUSEADDR means that on Windows).
            listener.setsockopt(socket.SOL_SOCKET, getattr(socket, "SO_EXCLUSIVEADDRUSE", 0xFFFFFFFB), 1)
        listener.bind((self.host, self.requested_port))
        listener.listen(8)
        listener.setblocking(False)
        self.port = listener.getsockname()[1]
        self._listener = listener
        self._sel = selectors.DefaultSelector()
        self._sel.register(listener, selectors.EVENT_READ, None)
        self.started_at = time.time()
        log.info("Agent bridge listening on %s:%d", self.host, self.port)

    def stop(self) -> None:
        for conn in list(self._conns.values()):
            self._close(conn)
        if self._sel is not None:
            try:
                self._sel.close()
            except (OSError, ValueError):
                pass
        if self._listener is not None:
            try:
                self._listener.close()
            except OSError:
                pass
        self._listener = None
        self._sel = None

    # --- Pump ------------------------------------------------------------------

    def pump(self, timeout: float = 0.0) -> bool:
        """Accept, read, execute and answer. Main thread only. Returns True if anything happened."""
        if self._sel is None or self._pumping:
            return False
        self._pumping = True
        try:
            try:
                events = self._sel.select(timeout=timeout)
            except (OSError, ValueError):
                return False
            busy = bool(events)
            for key, mask in events:
                if key.data is None:
                    self._accept()
                    continue
                conn = key.data
                if mask & selectors.EVENT_READ:
                    self._read(conn)
                if mask & selectors.EVENT_WRITE and conn.sock is not None:
                    self._flush(conn)
            return busy
        finally:
            self._pumping = False

    def _accept(self) -> None:
        while True:
            try:
                sock, addr = self._listener.accept()
            except (BlockingIOError, InterruptedError):
                return
            except OSError:
                return
            sock.setblocking(False)
            try:
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            except OSError:
                pass
            conn = Connection(sock, addr)
            self._conns[sock.fileno()] = conn
            self._sel.register(sock, selectors.EVENT_READ, conn)

    def _read(self, conn: Connection) -> None:
        total = 0
        closed = False
        while total < MAX_READ_PER_PUMP:
            try:
                data = conn.sock.recv(65536)
            except (BlockingIOError, InterruptedError):
                break
            except OSError:
                closed = True
                break
            if not data:
                closed = True
                break
            total += len(data)
            conn.rbuf += data
        while conn.sock is not None and not conn.closing:
            newline = conn.rbuf.find(b"\n")
            if newline < 0:
                if len(conn.rbuf) > MAX_LINE_BYTES:
                    conn.closing = True
                    self._send(conn, _error_response(None, INVALID_REQUEST, "Request too large"))
                break
            line = bytes(conn.rbuf[:newline]).strip()
            del conn.rbuf[:newline + 1]
            if line:
                self._handle_line(conn, line)
        if closed and conn.sock is not None:
            self._close(conn)

    def _handle_line(self, conn: Connection, line: bytes) -> None:
        try:
            msg = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, ValueError, RecursionError):
            self._refuse(conn, _error_response(None, PARSE_ERROR, "Invalid JSON; send one JSON-RPC object per line"))
            return
        if not isinstance(msg, dict) or not isinstance(msg.get("method"), str):
            self._refuse(conn, _error_response(msg.get("id") if isinstance(msg, dict) else None, INVALID_REQUEST,
                                               "Expected a JSON-RPC request object with a string 'method'"))
            return
        req_id = msg.get("id")
        method = msg["method"]
        params = msg.get("params")
        if params is None:
            params = {}
        if not isinstance(params, dict):
            self._refuse(conn, _error_response(req_id, INVALID_PARAMS, "'params' must be an object"))
            return

        if not conn.authed:
            self._hello(conn, req_id, method, params)
            return
        if method == "hello":
            self._send(conn, _result_response(req_id, self.hello_info()))
            return

        self.requests += 1
        self.last_request = time.time()
        try:
            result = self.dispatch(method, params, conn)
            response = _result_response(req_id, result)
        except BridgeError as exc:
            response = {"jsonrpc": "2.0", "id": req_id, "error": exc.to_error()}
        except Exception as exc:  # a bug in a handler must not take the bridge down
            log.exception("Bridge method %s failed", method)
            tb = traceback.format_exc().strip().splitlines()[-6:]
            response = _error_response(req_id, INTERNAL_ERROR, f"{type(exc).__name__}: {exc}",
                                       {"traceback": tb, "hint": "This is a bug in the GameStudio bridge; "
                                        "run_python can work around it."})
        if req_id is not None:
            self._send(conn, response)

    def _refuse(self, conn: Connection, response: dict) -> None:
        """Answers a line that is not a usable request. Before hello it also closes the connection."""
        if not conn.authed:
            conn.closing = True  # closed once the error is written; later lines are never read
        self._send(conn, response)

    def _hello(self, conn, req_id, method, params) -> None:
        token = params.get("token")
        # As bytes: compare_digest refuses str with non-ASCII characters.
        if method != "hello" or not isinstance(token, str) or \
                not hmac.compare_digest(token.encode("utf-8"), self.token.encode("utf-8")):
            why = "Unauthorized: the first request must be hello with the bridge token" if method != "hello" \
                else "Unauthorized: wrong token"
            conn.closing = True  # closed once the error is written
            self._send(conn, _error_response(req_id, UNAUTHORIZED, why, {
                "hint": "Read the token from <project>/.gamestudio/blender.json (or the user-level bridge.json)."}))
            return
        conn.authed = True
        conn.client = str(params.get("client", ""))[:200]
        conn.agent = _agent_info(params.get("agent"))
        conn.connected_at = time.time()
        log.info("Agent bridge: client connected (%s)", conn.client or "unnamed")
        self._send(conn, _result_response(req_id, self.hello_info()))

    def hello_info(self) -> dict:
        import bpy

        return {"bridge": BRIDGE_PROTOCOL, "blender": bpy.app.version_string.split(" ")[0],
                "pid": os.getpid(), "background": bool(bpy.app.background)}

    # --- Output ----------------------------------------------------------------

    def _send(self, conn: Connection, msg: dict) -> None:
        if conn.sock is None:
            return
        data = (json.dumps(msg, separators=(",", ":"), ensure_ascii=False, default=_json_default) + "\n")
        conn.wbuf += data.encode("utf-8")
        self._flush(conn)

    def _flush(self, conn: Connection) -> None:
        while conn.wbuf and conn.sock is not None:
            try:
                sent = conn.sock.send(conn.wbuf)
            except (BlockingIOError, InterruptedError):
                break
            except OSError as exc:
                if exc.errno in (errno.EAGAIN, errno.EWOULDBLOCK):
                    break
                self._close(conn)
                return
            del conn.wbuf[:sent]
        if conn.sock is None:
            return
        if conn.wbuf:
            self._sel.modify(conn.sock, selectors.EVENT_READ | selectors.EVENT_WRITE, conn)
        else:
            if conn.closing:
                self._close(conn)
                return
            self._sel.modify(conn.sock, selectors.EVENT_READ, conn)

    def flush_all(self, timeout: float = 2.0) -> None:
        """Write out pending responses (before a blocking operation or shutdown)."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and any(c.wbuf for c in self._conns.values() if c.sock):
            for conn in list(self._conns.values()):
                if conn.wbuf:
                    self._flush(conn)
            time.sleep(0.005)

    def _close(self, conn: Connection) -> None:
        sock, conn.sock = conn.sock, None
        if sock is None:
            return
        self._conns.pop(sock.fileno(), None)
        try:
            self._sel.unregister(sock)
        except (KeyError, ValueError, OSError):
            pass
        try:
            sock.close()
        except OSError:
            pass


def _agent_info(value) -> dict | None:
    """hello's optional `agent`: the MCP client's clientInfo, e.g. {"name": "claude-code", "version": "2.1.0"}."""
    if not isinstance(value, dict) or not isinstance(value.get("name"), str) or not value["name"].strip():
        return None
    return {"name": value["name"].strip()[:100], "version": str(value.get("version") or "")[:50]}


def _json_default(value):
    try:
        return list(value)
    except TypeError:
        return str(value)


def _result_response(req_id, result) -> dict:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _error_response(req_id, code: int, message: str, data: dict | None = None) -> dict:
    error = {"code": code, "message": message}
    if data:
        error["data"] = data
    return {"jsonrpc": "2.0", "id": req_id, "error": error}
