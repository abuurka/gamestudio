# SPDX-License-Identifier: GPL-3.0-or-later
"""Serves the assistant's tools to "tool" peers on the live link (docs/SPEC.md §7.5).

A tool peer on the dev-server hub sends `tool.list` / `tool.call`; they are answered
here, on Blender's main thread, from the same registry the built-in assistant uses,
with the General role in Propose mode: tools can read and stage proposals, and only
the developer's click in Blender applies them.

Coding agents such as Claude Code no longer come this way: the GameStudio MCP server
(runtime/packages/mcp) drives Blender directly through the agent bridge
(gamestudio/bridge, docs/MCP.md). This proposal-only path remains for tool peers
that want the assistant's review flow.
"""

import threading

from ..core import link as link_mod
from ..core import project
from ..prefs import get_prefs
from . import audit, tools

_attached_to = None


def ensure_handlers() -> None:
    """Attach the handlers to the current link client (it is recreated on reconnect)."""
    global _attached_to
    try:
        client = link_mod.get_link()
    except Exception:
        return
    if client is None or client is _attached_to:
        return
    client.on("tool.list", _on_tool_list)
    client.on("tool.call", _on_tool_call)
    _attached_to = client


def _reply(msg: dict, payload: dict) -> None:
    client = link_mod.get_link()
    if client is None:
        return
    out = {**payload, "re": msg.get("id")}
    if msg.get("from"):
        out["to"] = msg["from"]
    client.send(out)


def _on_tool_list(msg: dict) -> None:
    if not get_prefs().assistant_enabled:
        _reply(msg, {"t": "tool.list.result", "tools": [],
                     "error": "The GameStudio AI tools are disabled in Blender's add-on preferences."})
        return
    allowed = tools.allowed_names("general", "propose")
    listed = [{"name": t.name, "description": t.description, "input_schema": t.input_schema,
               "tier": t.tier} for t in tools.tools_for("general") if t.name in allowed]
    _reply(msg, {"t": "tool.list.result", "tools": listed})


def _on_tool_call(msg: dict) -> None:
    name = msg.get("name", "")
    args = msg.get("input") or {}
    if not get_prefs().assistant_enabled:
        _reply(msg, {"t": "tool.result", "ok": False,
                     "content": "The GameStudio AI tools are disabled in Blender's add-on preferences."})
        return
    root = project.project_root()
    ctx = tools.ToolContext(source="mcp", project_root=root)

    def done(ok: bool, content: str) -> None:
        audit.log(root, "mcp_tool_call", tool=name, input=args, ok=ok, result=content)
        _reply(msg, {"t": "tool.result", "ok": ok, "content": content})

    allowed = tools.allowed_names("general", "propose")
    t = tools.TOOLS.get(name)
    if t is not None and not t.main_thread:
        # Slow tools that never touch bpy (e.g. the type check) run off the UI thread.
        threading.Thread(target=tools.invoke, args=(name, args, ctx, done, allowed), daemon=True).start()
    else:
        tools.invoke(name, args, ctx, done, allowed=allowed)
