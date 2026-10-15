# SPDX-License-Identifier: GPL-3.0-or-later
"""GUI half of the docked Game View check (driven by tools/e2e/game_view_e2e.py).

Runs inside GameStudio Editor *with its UI* (the Game View draws on the GPU, which
background mode has none of), on examples/demo-game/scenes/main.blend:
1. turn the largest area other than the 3D View into a Game View;
2. press Play: the game must run docked, and its frames must reach the region's
   texture (frames "uploaded" counts uploads done by the region's draw code);
3. pause and resume the game;
4. take a screenshot, stop the docked game, and quit.

Everything runs from timers, because the UI must keep drawing. Results go to the JSON
file named after "--"; the screenshot sits next to it.
"""

import importlib
import json
import pathlib
import sys
import time
import traceback

import bpy

OUT = pathlib.Path(sys.argv[sys.argv.index("--") + 1])
SHOT = OUT.with_suffix(".png")
TIMEOUT = 150.0
FRAMES_WANTED = 60

started = time.monotonic()
results = {"checks": [], "status": None, "screenshot": ""}
state = {"step": "wait_shell", "since": time.monotonic(), "mark": 0}


def check(name, ok, detail=""):
    results["checks"].append({"name": name, "ok": bool(ok), "detail": str(detail)})
    print(f"GAMEVIEW {name} {'PASS' if ok else 'FAIL'} {detail}", flush=True)


def host():
    return importlib.import_module("bl_ext.system.gamestudio.host")


def window():
    wm = bpy.context.window_manager
    return wm.windows[0] if wm and wm.windows else None


def area_of_type(kind):
    win = window()
    areas = [a for a in win.screen.areas if a.type == kind] if win else []
    return max(areas, key=lambda a: a.width * a.height) if areas else None


def main_region(area):
    return next((r for r in area.regions if r.type == "WINDOW"), None)


def go(step):
    state["step"], state["since"] = step, time.monotonic()


def finish():
    try:
        results["status"] = host().game_view.status()
    except Exception as exc:  # noqa: BLE001 - report, then quit anyway
        results["status"] = {"error": repr(exc)}
    try:
        host().game_view.stop()
    except Exception:  # noqa: BLE001
        pass
    OUT.write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")
    bpy.ops.wm.quit_blender()


def frames(kind="uploaded"):
    return host().game_view.status()["frames"].get(kind, 0)


def tick():
    try:
        return step()
    except Exception:  # noqa: BLE001 - any error ends the run with a report
        check("no_python_errors", False, traceback.format_exc())
        finish()
        return None


def step():
    now = time.monotonic()
    if now - started > TIMEOUT:
        check("finished_in_time", False, f"stuck at step {state['step']}: {host().game_view.status()}")
        finish()
        return None
    waited = now - state["since"]
    s = state["step"]

    if s == "wait_shell":
        try:
            h = host()
        except ImportError:
            return 0.25
        if window() is None or area_of_type("VIEW_3D") is None:
            return 0.25
        check("native_game_view_available", h.game_view.available(), h.info()["capabilities"])
        # The checkout's example runs its scripts (core/trust.py), for this run only.
        root = importlib.import_module("bl_ext.system.gamestudio.core.project").project_root()
        importlib.import_module("bl_ext.system.gamestudio.core.trust").trust(root, "tests", save=False)
        target = max((a for a in window().screen.areas if a.type != "VIEW_3D"),
                     key=lambda a: a.width * a.height)
        check("game_view_opened", h.game_view.open(target), target.type)
        go("play")
        return 0.5

    if s == "play":
        view3d = area_of_type("VIEW_3D")
        with bpy.context.temp_override(window=window(), area=view3d, region=main_region(view3d)):
            outcome = bpy.ops.gamestudio.play()
        check("play_operator", outcome == {"FINISHED"}, outcome)
        go("wait_frames")
        return 0.25

    if s == "wait_frames":
        current = host().game_view.status()
        if current["state"] in ("failed", "closed"):
            check("docked_game_runs", False, current)
            finish()
            return None
        if current["frames"]["uploaded"] < FRAMES_WANTED:
            return 0.25
        check("docked_game_runs", True,
              f"{current['frames']['uploaded']} frames uploaded in {waited:.1f}s, "
              f"{current['width']}x{current['height']}, page {current['page_state']!r}, "
              f"latency {current['timings']['latency_ms']:.1f} ms, adapter {current['adapter']!r}")
        go("wait_link")
        return 0.25

    if s == "wait_link":
        # Live editing needs the docked game on the live link, like a game in a window.
        link = importlib.import_module("bl_ext.system.gamestudio.core.link").get_link()
        peers = link.game_peers() if link is not None and link.connected else []
        if not peers and waited < 20:
            return 0.25
        check("docked_game_joins_live_link", bool(peers),
              f"{len(peers)} game peer(s) after {waited:.1f}s, state {getattr(link, 'game_state', None)!r}")
        go("settle")
        return 3.0  # let the level finish loading for the screenshot

    if s == "settle":
        win = window()
        gv_area = next(a for a in win.screen.areas if host().game_view.shows_game_view(a))
        with bpy.context.temp_override(window=win, area=gv_area):
            bpy.ops.screen.screenshot(filepath=str(SHOT))
        results["screenshot"] = str(SHOT)
        check("screenshot", SHOT.is_file(), SHOT)
        host().game_view.set_paused(True)
        state["mark"] = frames("delivered")
        go("paused")
        return 1.5

    if s == "paused":
        if waited < 1.0:
            return 0.25
        state["mark_paused"] = frames("delivered")
        go("paused_hold")
        return 1.0

    if s == "paused_hold":
        grew = frames("delivered") - state["mark_paused"]
        check("pause_stops_frames", grew <= 2, f"{grew} frames while paused")
        host().game_view.set_paused(False)
        state["mark"] = frames("delivered")
        go("resumed")
        return 1.5

    if s == "resumed":
        grew = frames("delivered") - state["mark"]
        check("resume_restarts_frames", grew >= 20, f"{grew} frames in 1.5 s after resume")
        host().game_view.stop()
        go("stopped")
        return 0.5

    if s == "stopped":
        current = host().game_view.status()
        check("stop_ends_the_session", not current["active"], current["state"])
        check("finished_in_time", True, f"{now - started:.1f}s")
        finish()
        return None
    return None


bpy.app.timers.register(tick, first_interval=1.0)
