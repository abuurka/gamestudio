# SPDX-License-Identifier: GPL-3.0-or-later
"""The agent bridge: lets the MCP server (runtime/packages/mcp, launched by Claude Code or any MCP
client) drive this Blender session directly, over a local, token-protected socket. See docs/MCP.md.

Agents act directly; every scene change they make is one undo step ("Agent: <tool>"), and
checkpoints save copies of the .blend under <project>/.gamestudio/checkpoints/.

* server.py: the 127.0.0.1 socket, NDJSON JSON-RPC 2.0 framing and token check.
* api.py: the method table, undo steps and shared helpers; methods.py, assets.py, render.py and
  pyexec.py: the methods.
* discovery.py: <project>/.gamestudio/blender.json and the user-level bridge.json.

Requests run on the main thread. In the UI a timer pumps the server every 10 ms (100 ms when no
client is connected). Timers never fire in --background, so background sessions call
serve_forever() (the MCP server's launcher does) and tests call pump().

The bridge starts automatically in the UI when the "Agent bridge" preference is on (default).
It never starts by itself in --background.
"""

import atexit
import os
import time

import bpy
from bpy.app.handlers import persistent

from ..core.log import get_logger
from . import api, discovery
from .server import BridgeServer

log = get_logger(__name__)

ACTIVE_INTERVAL = 0.01
IDLE_INTERVAL = 0.1
DISCOVERY_INTERVAL = 1.0

_server: BridgeServer | None = None
_quit_requested = False
_claim_pending = False
_last_sync = 0.0


def _load_methods() -> None:
    from . import assets, compare, gameui, library, lighting, methods, pyexec, render, templates  # noqa: F401  (they register their methods)


def get_server() -> BridgeServer | None:
    return _server


def is_running() -> bool:
    return _server is not None and _server.running


def enabled_in_preferences() -> bool:
    from ..prefs import get_prefs

    return bool(getattr(get_prefs(), "bridge_enabled", True))


def start(force: bool = False) -> BridgeServer | None:
    """Start the server (idempotent). Without force, respects the preference."""
    global _server, _claim_pending
    if _server is not None and _server.running:
        return _server
    if not force and not enabled_in_preferences():
        return None
    _load_methods()
    server = BridgeServer(api.dispatch)
    try:
        server.start()
    except OSError as exc:
        log.error("Agent bridge could not listen on 127.0.0.1: %s", exc)
        return None
    _server = server
    _claim_pending = True
    _ensure_timer()
    return server


def stop() -> None:
    global _server
    server, _server = _server, None
    if server is not None:
        server.flush_all(0.5)
        server.stop()
        log.info("Agent bridge stopped")
    discovery.remove_all()


def request_quit() -> None:
    """Ask serve_forever() to return after the current request is answered."""
    global _quit_requested
    _quit_requested = True


def sync_discovery(claim: bool = False) -> None:
    global _last_sync, _claim_pending
    if _server is None:
        return
    _last_sync = time.monotonic()
    claim = claim or _claim_pending
    _claim_pending = False
    try:
        discovery.sync(_server, claim=claim)
    except Exception:
        log.exception("Agent bridge: updating the discovery files failed")


def pump(timeout: float = 0.0) -> bool:
    """Serve pending requests. Main thread only. Returns True if anything happened."""
    if _server is None:
        return False
    busy = _server.pump(timeout)
    if _claim_pending or time.monotonic() - _last_sync >= DISCOVERY_INTERVAL:
        sync_discovery()
    return busy


def _timer():
    if _server is None:
        return None
    try:
        pump(0.0)
    except Exception:
        log.exception("Agent bridge pump failed")
    return ACTIVE_INTERVAL if _server is not None and _server.clients else IDLE_INTERVAL


def _ensure_timer() -> None:
    if bpy.app.background:
        return
    if not bpy.app.timers.is_registered(_timer):
        bpy.app.timers.register(_timer, first_interval=0.0, persistent=True)


def _pump_others() -> None:
    """Background sessions have no timers: drive the modules that rely on them."""
    from ..core import devserver, gameui_live, link, live, play, registry

    for fn in (link.pump, live.pump, gameui_live.pump, play.pump, devserver.pump, registry.pump):
        try:
            fn()
        except Exception:
            log.exception("Pump %s failed", fn.__module__)


def _parent_alive(pid: int | None) -> bool:
    if not pid:
        return True
    from ..core import devserver

    return devserver.pid_alive(pid)


def serve_forever(parent_pid: int | None = None, idle_timeout: float | None = None) -> None:
    """Serve requests until `quit` is called, the parent process exits, or idle_timeout passes
    without a request. For --background sessions (the MCP server's launcher calls it)."""
    global _quit_requested
    server = start(force=True)
    if server is None:
        raise RuntimeError("The agent bridge could not start")
    _quit_requested = False
    sync_discovery(claim=True)
    print(f"GameStudio agent bridge: serving on 127.0.0.1:{server.port} (pid {os.getpid()})", flush=True)
    last_check = 0.0
    try:
        while not _quit_requested:
            pump(0.02)
            _pump_others()
            now = time.monotonic()
            if now - last_check > 1.0:
                last_check = now
                if not _parent_alive(parent_pid):
                    log.info("Agent bridge: the parent process %s exited; stopping", parent_pid)
                    break
                if idle_timeout and server.last_request and time.time() - server.last_request > idle_timeout:
                    log.info("Agent bridge: idle for %d s; stopping", idle_timeout)
                    break
    except KeyboardInterrupt:
        pass
    finally:
        if _server is not None:
            _server.flush_all(2.0)
        stop()


def clients_info() -> list[dict]:
    """Who is connected, oldest first: [{name, version, connected_at, via}]. name and version are the
    agent's (the MCP server's own client, e.g. claude-code) when the MCP server passed them in hello,
    else the bridge client's ("gamestudio-mcp 0.4.0"), else ""; via is the bridge client."""
    server = get_server()
    infos = getattr(server, "client_infos", None)
    out = []
    for info in (infos() if callable(infos) else []):
        agent = info.get("agent") or {}
        client = info.get("client") or ""
        client_name, _, client_version = client.partition(" ")
        out.append({"name": agent.get("name") or client_name,
                    "version": agent.get("version") if agent else client_version,
                    "connected_at": info.get("connected_at") or 0.0, "via": client})
    return out


# MCP clients' names (clientInfo.name) and how Blender shows them. Codex calls itself codex-mcp-client.
CLIENT_LABELS = {"claude-code": "Claude Code", "codex-mcp-client": "Codex", "codex": "Codex"}


def client_label(name: str) -> str:
    """A display name for a client: "Claude Code" for claude-code, "Codex" for codex-mcp-client, else
    the name it gave."""
    return CLIENT_LABELS.get(name.strip().lower().replace(" ", "-"), name)


def status_text() -> str:
    if _server is None or not _server.running:
        return "Off"
    n = _server.clients
    return f"Listening on 127.0.0.1:{_server.port}, {n} client{'s' if n != 1 else ''}"


def apply_preference() -> None:
    """Called when the preference changes."""
    if bpy.app.background:
        return
    if enabled_in_preferences():
        start()
    else:
        stop()


@persistent
def _on_file_change(*_args):
    global _claim_pending
    api.agent_dirty = False
    if _server is not None:
        _claim_pending = True


@persistent
def _on_load(*_args):
    api.undo_base_needed = True  # the loaded file starts a new undo history (see api.ensure_undo_base)


@persistent
def _on_exit(*_args):
    stop()


_HANDLERS = (("load_post", _on_file_change), ("load_post", _on_load), ("save_post", _on_file_change),
             ("exit_pre", _on_exit))


def register():
    for name, fn in _HANDLERS:
        handlers = getattr(bpy.app.handlers, name)
        if fn not in handlers:
            handlers.append(fn)
    atexit.register(discovery.remove_all)
    if not bpy.app.background and enabled_in_preferences():
        start()


def unregister():
    if bpy.app.timers.is_registered(_timer):
        bpy.app.timers.unregister(_timer)
    for name, fn in _HANDLERS:
        handlers = getattr(bpy.app.handlers, name)
        if fn in handlers:
            handlers.remove(fn)
    atexit.unregister(discovery.remove_all)
    stop()
