# SPDX-License-Identifier: GPL-3.0-or-later
"""Blender half of the end-to-end test (driven by tools/e2e/e2e.mjs).

This runs the real add-on against a real dev server and a real game in a browser:
1. Open examples/demo-game/scenes/main.blend, connect the live link, press Play.
2. Move a coin and edit a component in Blender, then ask the running game for
   its state: the change has to arrive in the game.
3. Call the AI assistant's runtime tools against the live game.
4. Serve the agent bridge while the Node side drives the MCP server, then check its
   edit was applied directly, as one undo step.

Blender timers don't run in --background, so this script pumps the modules itself.
Prints "E2E <name> PASS|FAIL detail" lines and exits non-zero on failure.
"""

import json
import pathlib
import sys
import time

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "blender"))
sys.dont_write_bytecode = True

import bpy  # noqa: E402

import gamestudio  # noqa: E402
from gamestudio import bridge as agent_bridge  # noqa: E402
from gamestudio.assistant import bridge, tools  # noqa: E402
from gamestudio.core import components, export, ids, link, live, play, project, registry  # noqa: E402

DEMO = REPO / "examples" / "demo-game"
SENTINEL = pathlib.Path(sys.argv[sys.argv.index("--") + 1])
failures = []


def check(name, ok, detail=""):
    print(f"E2E {name} {'PASS' if ok else 'FAIL'} {detail}", flush=True)
    if not ok:
        failures.append(name)


def pump(seconds=0.0, until=None, timeout=20.0):
    deadline = time.monotonic() + (timeout if until else seconds)
    while True:
        link.pump()
        live.pump()
        play.pump()
        agent_bridge.pump()
        if until is not None and until():
            return True
        if time.monotonic() > deadline:
            return until is None
        time.sleep(0.01)


def run_tool(name, args):
    box = []
    ctx = tools.ToolContext(source="assistant", project_root=project.project_root())
    tools.invoke(name, args, ctx, lambda ok, content: box.append((ok, content)))
    pump(until=lambda: box, timeout=10)
    return box[0] if box else (False, "no answer")


gamestudio.register()
bpy.ops.wm.open_mainfile(filepath=str(DEMO / "scenes" / "main.blend"))
registry.reload_registry(project.project_root())
agent_bridge.start(force=True)  # timers never fire here: pump() serves it
client = link.get_link()

# --- 1. Connect and play ----------------------------------------------------------
ok = pump(until=lambda: client.connected and client.game_peers(), timeout=40)
check("link_connected", ok, client.status_text)
if not ok:
    sys.exit(1)
bridge.ensure_handlers()

result = export.export_scene()
play.request_play(result, open_window=False)
pump(until=lambda: not play.is_pending(), timeout=10)
ok = pump(until=lambda: (client.game_ready or {}).get("rev") == result["rev"], timeout=40)
check("game_loaded_export", ok, f"rev {result['rev']}, {result['entities']} entities")

# --- 2. Live edits from Blender reach the running game --------------------------
coin = bpy.data.objects["Coin1"]
pad = bpy.data.objects["BouncePad"]
coin.location = (2.0, -2.0, 1.5)              # Blender Z-up
bpy.context.view_layer.update()               # fires depsgraph_update_post
pump(0.6)
components.set_field(pad, "BouncePad", "strength", 20.0)
bpy.context.view_layer.update()
pump(0.6)

box = []
client.request({"t": "state.request", "filter": {"ids": [ids.get_id(coin), ids.get_id(pad)]}},
               box.append, timeout=5)
pump(until=lambda: box, timeout=10)
entities = {e["name"]: e for e in ((box[0] or {}).get("entities", []) if box else [])}
coin_pos = entities.get("Coin1", {}).get("position")
check("live_transform", coin_pos is not None and
      all(abs(a - b) < 1e-3 for a, b in zip(coin_pos, [2.0, 1.5, 2.0])),
      f"game position {coin_pos} (expected Y-up [2, 1.5, 2])")
strength = entities.get("BouncePad", {}).get("components", {}).get("BouncePad", {}).get("strength")
check("live_component", strength == 20, f"BouncePad.strength in game = {strength}")

# --- 3. The assistant's runtime tools against the live game ---------------------
ok, content = run_tool("get_runtime_state", {"objects": ["Coin1"]})
check("ai_get_runtime_state", ok and "Coin1" in content, content[:120])
ok, content = run_tool("get_scene_overview", {"max_objects": 5})
check("ai_scene_overview", ok and json.loads(content)["game_connected"] is True, "game_connected=True")

# --- 4. Let the Node side drive the MCP server and send a log line --------------
agent_bridge.sync_discovery(claim=True)
bpy.ops.ed.undo_push(message="e2e: before the agent")
crate_before = tuple(bpy.data.objects["Crate1"].location)
print("E2E_WAITING", flush=True)
pump(until=SENTINEL.exists, timeout=90)
check("node_phase_done", SENTINEL.exists())

probe = [m for m in client.logs if "e2e-probe" in str(m.get("msg", ""))]
check("runtime_logs_to_blender", bool(probe), probe[0].get("msg", "") if probe else "no probe log")
ok, content = run_tool("get_runtime_logs", {"level": "warn"})
check("ai_get_runtime_logs", ok and "e2e-probe" in content)

crate_after = tuple(round(v, 4) for v in bpy.data.objects["Crate1"].location)
check("mcp_edit_applied", crate_after == (4.0, 4.0, 6.0), f"Crate1 at {crate_after}")
bpy.ops.ed.undo()
check("mcp_edit_is_one_undo_step", tuple(bpy.data.objects["Crate1"].location) == crate_before,
      "one Ctrl+Z restores Crate1")
agent_bridge.stop()

print(f"E2E_DONE failures={len(failures)}", flush=True)
sys.exit(1 if failures else 0)
