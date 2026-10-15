# SPDX-License-Identifier: GPL-3.0-or-later
"""GUI half of the Game View play test (driven by tools/e2e/game_view_play_e2e.py).

Runs inside GameStudio Editor with its UI on examples/demo-game/scenes/main.blend.

Mode "play" (the editor runs with --enable-event-simulate, so the user's real mouse and
keyboard are ignored and every event comes from window.event_simulate):
  1. input on the demo game: click to capture, hold W (the player walks; its position comes
     from the running game over the live link), Space (it jumps), mouse and wheel, Shift+Esc
     gives the keys back to the editor, a click outside releases;
  2. robustness: resizing, two Game Views, switching workspace pauses and resumes, detach,
     killing the host process or its renderer, Restart, five start/stop cycles with process and
     memory checks;
  3. input details on tools/e2e/pages/input_probe.html (served from this process): pointer
     coordinates, buttons, wheel, keys, pointer lock (cursor grab), Esc and Shift+Esc;
  4. quit while the game runs (the driver then checks no host process survives).

Mode "cpu" (no event simulation): the editor's CPU use and which regions redraw while the game
runs docked, hidden (another workspace) and stopped.

Results go to the JSON file named after "--"; screenshots sit next to it.
"""

import http.server
import importlib
import json
import math
import pathlib
import sys
import threading
import time
import traceback

import bpy

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import gv_procs  # noqa: E402

ARGS = sys.argv[sys.argv.index("--") + 1:]
OUT = pathlib.Path(ARGS[0])
MODE = ARGS[1] if len(ARGS) > 1 else "play"
PAGES = pathlib.Path(__file__).resolve().parent / "pages"
TIMEOUT = 420.0 if MODE == "play" else 150.0
MARKER_KEY = "F19"
GV_WINDOW = "GAMESTUDIO_GAME_VIEW.WINDOW"
GV_HEADER = "GAMESTUDIO_GAME_VIEW.HEADER"

started = time.monotonic()
results = {"mode": MODE, "checks": [], "measurements": {}, "screenshots": [], "quit_descendants": []}


def check(name, ok, detail=""):
    results["checks"].append({"name": name, "ok": bool(ok), "detail": str(detail)})
    print(f"GVPLAY {name} {'PASS' if ok else 'FAIL'} {detail}", flush=True)
    return bool(ok)


def measure(name, value):
    results["measurements"][name] = value
    print(f"GVPLAY measure {name} = {json.dumps(value, default=str)}", flush=True)


# --- Shell access ----------------------------------------------------------------------


def ext(name):
    return importlib.import_module(f"bl_ext.system.gamestudio.{name}")


# The checkout's example runs its scripts (core/trust.py), for this run only: no question pops up.
try:
    if ext("core.project").project_root() is not None:
        ext("core.trust").trust(ext("core.project").project_root(), "tests", save=False)
except ImportError:  # the extension loads later: Play asks then
    pass


def gv():
    return ext("host").game_view


def status():
    return gv().status()


def frames(kind="uploaded"):
    return status()["frames"].get(kind, 0)


def live():
    return ext("core.link").get_link()


def window():
    wm = bpy.context.window_manager
    return wm.windows[0] if wm and wm.windows else None


def region_of(area, kind="WINDOW"):
    return next((r for r in area.regions if r.type == kind), None)


def game_view_areas():
    win = window()
    return [a for a in win.screen.areas if gv().shows_game_view(a)] if win else []


def largest_game_view():
    areas = game_view_areas()
    return max(areas, key=lambda a: a.width * a.height) if areas else None


def largest_other_area():
    """The largest ordinary area that does not show the Game View (the layout belongs to the shell)."""
    areas = [a for a in window().screen.areas
             if a.type not in ("TOPBAR", "STATUSBAR") and not gv().shows_game_view(a)]
    return max(areas, key=lambda a: a.width * a.height)


def docked_peer():
    """The docked game's live-link peer: its user agent names the host's pid."""
    pid = status().get("host_pid")
    client = live()
    if not pid or client is None or not client.connected:
        return None
    return next((p for p in client.game_peers() if f"(pid {pid})" in str(p.get("client", ""))), None)


def screenshot(name):
    path = OUT.with_name(f"{OUT.stem}_{name}.png")
    win = window()
    with bpy.context.temp_override(window=win, area=win.screen.areas[0]):
        bpy.ops.screen.screenshot(filepath=str(path))
    results["screenshots"].append(str(path))


# --- Coroutine runner: steps yield a delay in seconds, or Until(...) --------------------


class Until:
    def __init__(self, cond, timeout, interval=0.05):
        self.cond, self.deadline, self.interval = cond, time.monotonic() + timeout, interval


def run(gen):
    state = {"value": None, "wait": None}

    def tick():
        try:
            if time.monotonic() - started > TIMEOUT:
                check("finished_in_time", False, f"timed out after {TIMEOUT:.0f}s")
                finish()
                return None
            wait = state["wait"]
            if wait is not None:
                ok = bool(wait.cond())
                if not ok and time.monotonic() < wait.deadline:
                    return wait.interval
                state["wait"], state["value"] = None, ok
            while True:
                step = gen.send(state["value"])
                state["value"] = None
                if isinstance(step, Until):
                    if step.cond():
                        state["value"] = True
                        continue
                    state["wait"] = step
                    return step.interval
                return max(0.0, float(step))
        except StopIteration:
            finish()
            return None
        except Exception:  # noqa: BLE001 - any error ends the run with a report
            check("no_python_errors", False, traceback.format_exc())
            finish()
            return None

    bpy.app.timers.register(tick, first_interval=1.0)


_finished = False


def finish(quit_running=False):
    global _finished
    if _finished:
        return
    _finished = True
    try:
        results["status"] = status()
    except Exception as exc:  # noqa: BLE001
        results["status"] = {"error": repr(exc)}
    try:
        _release_input()
    except Exception:  # noqa: BLE001
        pass
    if not quit_running:
        try:
            gv().stop()
        except Exception:  # noqa: BLE001
            pass
    if PROBE.server is not None:
        PROBE.server.shutdown()
    OUT.write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")
    bpy.ops.wm.quit_blender()


# --- Simulated input ---------------------------------------------------------------------


class Sim:
    """window.event_simulate with a remembered mouse position (every event carries one)."""

    def __init__(self):
        self.x = self.y = 10
        self.held = set()

    @property
    def win(self):
        return window()

    def move(self, x, y):
        self.x, self.y = int(round(x)), int(round(y))
        self.win.event_simulate("MOUSEMOVE", "NOTHING", x=self.x, y=self.y)

    def press(self, kind, **mods):
        self.win.event_simulate(kind, "PRESS", x=self.x, y=self.y, **mods)
        self.held.add((kind, tuple(sorted(mods.items()))))

    def release(self, kind, **mods):
        self.win.event_simulate(kind, "RELEASE", x=self.x, y=self.y, **mods)
        self.held.discard((kind, tuple(sorted(mods.items()))))

    def tap(self, kind, **mods):
        self.press(kind, **mods)
        self.release(kind, **mods)

    def click(self, x, y, button="LEFTMOUSE"):
        self.move(x, y)
        self.tap(button)

    def wheel(self, kind="WHEELDOWNMOUSE"):
        self.win.event_simulate(kind, "PRESS", x=self.x, y=self.y)

    def shift_esc(self):
        self.press("LEFT_SHIFT")
        self.tap("ESC", shift=True)
        self.release("LEFT_SHIFT")


SIM = Sim()


def _release_input():
    for kind, mods in list(SIM.held):
        SIM.release(kind, **dict(mods))


def region_point(region, fx=0.5, fy=0.5):
    """Window coordinates of a point inside a region, as fractions of its size."""
    return region.x + region.width * fx, region.y + region.height * fy


# --- Editor key marker: tells whether a key reached the editor or the game ---------------

MARKS = []


class GSTEST_OT_marker(bpy.types.Operator):
    bl_idname = "gstest.marker"
    bl_label = "Test marker"
    bl_options = {"INTERNAL"}

    def execute(self, _context):
        MARKS.append(time.monotonic())
        return {"FINISHED"}


def install_marker():
    bpy.utils.register_class(GSTEST_OT_marker)
    kc = bpy.context.window_manager.keyconfigs.addon
    km = kc.keymaps.new(name="Window", space_type="EMPTY", region_type="WINDOW")
    km.keymap_items.new(GSTEST_OT_marker.bl_idname, MARKER_KEY, "PRESS")


# --- Live-link queries --------------------------------------------------------------------


def query_state(filt, timeout=3.0):
    """Generator: the docked game's state.response entities (None on timeout)."""
    box = []
    msg = {"t": "state.request", "filter": filt}
    peer = docked_peer()
    if peer:
        msg["to"] = peer["peer"]
    live().request(msg, box.append, timeout=timeout)
    yield Until(lambda: box, timeout + 1)
    return (box[0] or {}).get("entities") if box and box[0] else None


def player_position():
    entities = yield from query_state({"component": "CharacterController"})
    return entities[0]["position"] if entities else None


def wait_frames(count, timeout=30.0):
    mark = frames()
    return Until(lambda: frames() - mark >= count or status()["state"] in ("failed", "closed"), timeout)


def wait_docked_game(timeout=40.0):
    """Generator: the docked game runs, uploads frames and is on the live link, playing."""
    ok = yield Until(lambda: status()["state"] == "connected" and status()["page_state"] == "running"
                     and frames() > 0, timeout)
    if not ok:
        return False
    ok = yield Until(lambda: docked_peer() is not None, timeout)
    return ok


# --- Probe page server ---------------------------------------------------------------------


class _Probe:
    server = None
    events = []
    info = {}
    lock = threading.Lock()

    def start(self):
        probe = self
        page = (PAGES / "input_probe.html").read_bytes()

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):  # noqa: N802
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(page)

            def do_POST(self):  # noqa: N802
                body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                try:
                    data = json.loads(body)
                except ValueError:
                    data = {}
                with probe.lock:
                    probe.info = {k: data.get(k) for k in ("dpr", "width", "height")}
                    probe.events.extend(data.get("events", []))
                self.send_response(204)
                self.end_headers()

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        return f"http://127.0.0.1:{self.server.server_address[1]}/"

    def take(self):
        with self.lock:
            events, self.events = self.events, []
        return events

    def seen(self, store, kind, **match):
        """Moves new events into `store`; True once one of type `kind` matches."""
        store.extend(self.take())
        return any(e["type"] == kind and all(e.get(k) == v for k, v in match.items()) for e in store)


PROBE = _Probe()


# --- Scenario: play mode ----------------------------------------------------------------------


def setup():
    ok = yield Until(lambda: _shell_ready(), 30)
    if not check("shell_ready", ok):
        return False
    ensure_window_shown()
    install_marker()
    # The first-run quick setup popup would take the first clicks.
    SIM.move(*region_point(bpy.context.window_manager.windows[0].screen.areas[0].regions[0]))
    SIM.tap("ESC")
    yield 0.3
    view3d = next(a for a in window().screen.areas if a.type == "VIEW_3D")
    check("game_view_opened", gv().open(view3d), f"{view3d.width}x{view3d.height}")
    return True


def ensure_window_shown():
    """A minimized editor draws nothing and pauses its Game View (by design): undo a minimize that
    happened on the desktop before the test started."""
    for hwnd in gv_procs.top_windows(_own_pid()):
        if gv_procs.is_minimized(hwnd):
            gv_procs.minimize(hwnd, False)
            print("GVPLAY note: the editor window was minimized; restored it", flush=True)


def _shell_ready():
    try:
        h = ext("host")
    except ImportError:
        return False
    win = window()
    return win is not None and h.game_view.available() and any(a.type == "VIEW_3D" for a in win.screen.areas)


def phase_input_demo():
    area = largest_game_view()
    with bpy.context.temp_override(window=window(), area=area, region=region_of(area)):
        outcome = bpy.ops.gamestudio.play()
    check("play_operator", outcome == {"FINISHED"}, outcome)
    ok = yield from wait_docked_game()
    check("docked_game_on_live_link", ok, f"{status()['state']} {status()['page_state']} peer={docked_peer()}")
    if not ok:
        return
    yield Until(lambda: live().game_state == "playing", 10)
    yield 1.5  # the player settles on the ground
    region = region_of(largest_game_view())
    check("game_sized_to_region", (status()["width"], status()["height"]) == (region.width, region.height),
          f"game {status()['width']}x{status()['height']}, region {region.width}x{region.height}")

    # Click to capture. The header says so (it is redrawn on the change, not only per frame).
    install_draw_counters()
    yield 0.3
    header_draws = DRAWS.get(GV_HEADER, 0)
    SIM.move(*region_point(region, 0.5, 0.5))
    SIM.press("LEFTMOUSE")
    ok = yield Until(lambda: status()["captured"], 3)
    SIM.release("LEFTMOUSE")
    check("click_captures_input", ok, status()["captured"])
    yield 0.3
    check("header_redraws_on_capture", DRAWS.get(GV_HEADER, 0) > header_draws,
          DRAWS.get(GV_HEADER, 0) - header_draws)

    # Hold W for one second: the player walks.
    before = yield from player_position()
    SIM.press("W")
    yield 1.0
    SIM.release("W")
    yield 0.4
    after = yield from player_position()
    walked = math.dist((before[0], before[2]), (after[0], after[2])) if before and after else 0.0
    check("hold_w_moves_player", walked > 1.0, f"{before} -> {after}: {walked:.2f} m")

    # Space: the player jumps.
    ground = (yield from player_position()) or [0, 0, 0]
    SIM.press("SPACE")
    peak = ground[1]
    t_end = time.monotonic() + 1.0
    while time.monotonic() < t_end:
        pos = yield from player_position()
        if pos:
            peak = max(peak, pos[1])
        yield 0.03
    SIM.release("SPACE")
    check("space_jumps", peak - ground[1] > 0.3, f"rose {peak - ground[1]:.2f} m")

    # Mouse motion and the wheel go to the game, not to the editor's View2D.
    for i in range(10):
        SIM.move(*region_point(region, 0.5 + i * 0.02, 0.5 - i * 0.01))
        yield 0.01
    SIM.wheel("WHEELDOWNMOUSE")
    SIM.wheel("WHEELUPMOUSE")
    yield 0.2
    check("still_captured_after_mouse", status()["captured"])

    # Keys go to the game while captured, not to the editor.
    marks = len(MARKS)
    SIM.tap(MARKER_KEY)
    yield 0.3
    check("captured_keys_skip_editor", len(MARKS) == marks, f"{len(MARKS) - marks} editor key presses")
    # So do the play shortcuts (F5 Play, Shift+F5 Stop, F6 Pause, F7 Step), as in Unity.
    SIM.tap("F6")
    SIM.tap("F7")
    SIM.press("LEFT_SHIFT")
    SIM.tap("F5", shift=True)
    SIM.release("LEFT_SHIFT")
    SIM.tap("F5")
    yield 0.8
    check("captured_play_keys_skip_editor", live().game_state == "playing", live().game_state)

    # Shift+Esc gives the mouse and keyboard back.
    header_draws = DRAWS.get(GV_HEADER, 0)
    SIM.shift_esc()
    ok = yield Until(lambda: not status()["captured"], 3)
    check("shift_esc_releases", ok)
    ok = yield Until(lambda: DRAWS.get(GV_HEADER, 0) > header_draws, 1)
    check("header_redraws_on_release", ok)
    SIM.tap(MARKER_KEY)
    ok = yield Until(lambda: len(MARKS) > marks, 3)
    check("editor_gets_keys_after_release", ok, f"{len(MARKS) - marks} editor key presses")
    # The play shortcuts work in the editor again, over the Game View too.
    SIM.tap("F6")
    ok = yield Until(lambda: live().game_state == "paused", 3)
    check("editor_pause_key_after_release", ok, live().game_state)
    SIM.tap("F6")
    ok = yield Until(lambda: live().game_state == "playing", 3)
    check("editor_resume_key_after_release", ok, live().game_state)

    # Click inside captures again; a click outside the Game View releases.
    SIM.click(*region_point(region, 0.3, 0.3))
    ok = yield Until(lambda: status()["captured"], 3)
    check("click_captures_again", ok)
    outside = largest_other_area()
    SIM.click(*region_point(region_of(outside) or outside.regions[0], 0.5, 0.5))
    ok = yield Until(lambda: not status()["captured"], 3)
    check("click_outside_releases", ok)
    screenshot("demo")


def phase_robustness():
    url = gv_url()
    root = ext("core.project").project_root()

    # Resize: maximize the Game View; the game follows the region's size.
    area = largest_game_view()
    small = (status()["width"], status()["height"])
    with bpy.context.temp_override(window=window(), area=area, region=region_of(area)):
        bpy.ops.screen.screen_full_area()
    yield 0.2
    big_region = region_of(largest_game_view())
    want = (big_region.width, big_region.height)
    ok = yield Until(lambda: (status()["width"], status()["height"]) == want, 5)
    check("resize_changes_game_resolution", ok and want != small,
          f"{small} -> game {status()['width']}x{status()['height']}, region {want}")
    area = largest_game_view()
    with bpy.context.temp_override(window=window(), area=area, region=region_of(area)):
        bpy.ops.screen.screen_full_area()
    ok = yield Until(lambda: (status()["width"], status()["height"]) == small, 5)
    check("resize_back", ok, f"game {status()['width']}x{status()['height']}, want {small}")

    # Two Game Views: both draw the game, the larger one sizes it, either captures input.
    props = largest_other_area()
    props_index = list(window().screen.areas).index(props)
    previous_type = props.ui_type
    previous_provider = getattr(props.spaces.active, "provider_idname", None)
    gv().open(props)
    yield wait_frames(30)
    views = status().get("views", [])
    check("two_game_views_draw", len(views) == 2 and all(v.get("frames_drawn", 0) > 0 for v in views),
          views)
    big = largest_game_view()
    check("largest_view_sizes_game", (status()["width"], status()["height"]) == (
        region_of(big).width, region_of(big).height), f"{status()['width']}x{status()['height']}")
    second = window().screen.areas[props_index]
    SIM.click(*region_point(region_of(second), 0.5, 0.5))
    ok = yield Until(lambda: status()["captured"], 3)
    check("second_view_captures", ok)
    SIM.shift_esc()
    yield Until(lambda: not status()["captured"], 3)
    restored = window().screen.areas[props_index]
    restored.ui_type = previous_type
    if previous_provider and getattr(restored.spaces.active, "provider_idname", None) != previous_provider:
        try:
            restored.spaces.active.provider_idname = previous_provider
        except (AttributeError, TypeError):
            pass
    yield 0.3
    check("back_to_one_view", len(game_view_areas()) == 1, len(game_view_areas()))

    # Switching workspace away pauses the game (no frames, no simulation); back resumes it.
    win = window()
    home = win.workspace
    away = next(w for w in bpy.data.workspaces if w != home and not any(
        gv().shows_game_view(a) for s in w.screens for a in s.areas))
    coin = ext("core.ids").get_id(bpy.data.objects["Coin1"])
    win.workspace = away
    ok = yield Until(lambda: status()["paused"], 3)
    check("hidden_view_pauses", ok, f"workspace {away.name}: {status()['paused']}")
    yield 0.5
    mark = frames("delivered")
    spin0 = yield from query_state({"ids": [coin]})
    yield 1.0
    spin1 = yield from query_state({"ids": [coin]})
    check("paused_game_sends_no_frames", frames("delivered") - mark <= 1, frames("delivered") - mark)
    rot0 = spin0[0]["rotation"] if spin0 else None
    rot1 = spin1[0]["rotation"] if spin1 else None
    check("paused_game_does_not_simulate", rot0 is not None and rot0 == rot1, f"coin rotation {rot0} -> {rot1}")
    win.workspace = home
    ok = yield Until(lambda: not status()["paused"], 3)
    yield wait_frames(20, 5)
    check("visible_view_resumes", ok and not status()["paused"], status()["frames"])

    # A minimized editor draws nothing: the game pauses until the window is back.
    hwnds = gv_procs.top_windows(_own_pid())
    if check("editor_window_found", bool(hwnds), hwnds):
        gv_procs.minimize(hwnds[0], True)
        ok = yield Until(lambda: status()["paused"], 3)
        check("minimized_editor_pauses", ok and gv_procs.is_minimized(hwnds[0]), status()["paused"])
        gv_procs.minimize(hwnds[0], False)
        ok = yield Until(lambda: not status()["paused"], 3)
        yield wait_frames(10, 5)
        check("restored_editor_resumes", ok, status()["frames"])

    # Detach: the game moves into its own window and the docked view says so.
    check("detach", gv().detach())
    ok = yield Until(lambda: status()["mode"] == "window", 5)
    check("detached_mode_reported", ok and status().get("overlay") == "The game is running in its own window",
          status().get("overlay"))
    hdr = ext("editors.game_view").status_text(status())[0]
    check("detached_header", hdr == "Detached", hdr)
    yield 2.0
    screenshot("detached")
    mark = frames("delivered")
    yield 0.5
    check("detached_sends_no_frames", frames("delivered") == mark)
    # Closing the game's window ends the game cleanly: "Game stopped", not a crash.
    game_windows = gv_procs.top_windows(status()["host_pid"])
    check("detached_window_found", bool(game_windows), game_windows)
    for hwnd in game_windows:
        gv_procs.close_window(hwnd)
    ok = yield Until(lambda: status()["state"] in ("closed", "failed"), 8)
    check("closing_game_window_stops_cleanly", ok and status()["state"] == "closed"
          and status().get("overlay") == "Game stopped", f"{status()['state']}: {status()['message']}")
    gv().stop()
    yield Until(lambda: not status()["active"], 5)

    # Kill the host process: the view reports it, nothing is left behind, Restart recovers.
    yield from restart_from_header()
    tree = gv_procs.descendants(_own_pid())
    host_pid = status()["host_pid"]
    check("host_tree_found", any(p["pid"] == host_pid for p in tree), f"{len(tree)} processes")
    statusbar_draws = DRAWS.get("STATUSBAR.HEADER", 0)
    gv_procs.kill(host_pid)
    ok = yield Until(lambda: status()["state"] in ("failed", "closed"), 8)
    check("host_kill_reported", ok, f"{status()['state']}: {status()['message']} / {status().get('overlay')}")
    ok = yield Until(lambda: DRAWS.get("STATUSBAR.HEADER", 0) > statusbar_draws, 2)
    check("host_kill_redraws_status_bar", ok, DRAWS.get("STATUSBAR.HEADER", 0) - statusbar_draws)
    hdr = ext("editors.game_view").status_text(status())
    check("host_kill_header", hdr[0] == "Crashed", hdr)
    check("host_kill_overlay", status().get("overlay") == "Game stopped (crashed)"
          and "Restart" in status().get("overlay_detail", ""), status().get("overlay_detail"))
    yield 0.3
    screenshot("host_killed")
    ok = yield Until(lambda: not gv_procs.still_alive(tree), 6)
    check("host_kill_leaves_no_processes", ok, gv_procs.still_alive(tree))
    yield from restart_from_header()
    check("restart_after_host_kill", status()["state"] == "connected" and docked_peer() is not None,
          status()["state"])

    # Kill only the page's renderer: "crashed", then Restart.
    tree = [p for p in gv_procs.descendants(_own_pid()) if p["name"].lower() == "electron.exe"]
    lines = gv_procs.command_lines([p["pid"] for p in tree])
    renderer = next((pid for pid, line in lines.items() if "--type=renderer" in line), None)
    check("renderer_found", renderer is not None, lines)
    if renderer:
        gv_procs.kill(renderer)
        ok = yield Until(lambda: status()["page_state"] == "crashed", 8)
        check("renderer_crash_reported", ok and status().get("overlay") == "Game stopped (crashed)",
              f"{status()['page_state']} / {status().get('overlay')}")
        check("renderer_crash_header", ext("editors.game_view").status_text(status())[0] == "Crashed")
        yield from restart_from_header()
        check("restart_after_renderer_crash", status()["page_state"] == "running" and docked_peer() is not None,
              status()["page_state"])

    # Five start/stop cycles: no host process survives a stop, and the editor does not grow.
    usage = []
    leaked = []
    stop_ms = []
    for cycle in range(5):
        gv().start(url, project_root=root)
        ok = yield Until(lambda: frames() >= 10, 30)
        if not ok:
            check(f"cycle_{cycle}_frames", False, status())
            break
        tree = gv_procs.descendants(_own_pid())
        t0 = time.perf_counter()
        gv().stop()
        stop_ms.append((time.perf_counter() - t0) * 1000)
        ok = yield Until(lambda: not gv_procs.still_alive(tree), 6)
        if not ok:
            leaked.extend(gv_procs.still_alive(tree))
        yield 0.5
        sample = gv_procs.usage(_own_pid())
        sample["gpu"] = gv_procs.gpu_memory_mb(_own_pid()) if cycle in (0, 4) else {}
        usage.append(sample)
    measure("cycles", usage)
    measure("stop_call_ms", [round(ms, 1) for ms in stop_ms])
    check("cycles_leave_no_processes", len(usage) == 5 and not leaked, leaked)
    if len(usage) == 5:
        grow_handles = usage[4]["handles"] - usage[0]["handles"]
        grow_private = usage[4]["private_mb"] - usage[0]["private_mb"]
        gpu0, gpu4 = usage[0]["gpu"].get("dedicated"), usage[4]["gpu"].get("dedicated")
        check("cycles_no_handle_leak", grow_handles < 40, f"+{grow_handles} handles")
        check("cycles_no_memory_leak", grow_private < 60, f"+{grow_private:.1f} MB private")
        check("cycles_no_gpu_leak", gpu0 is None or gpu4 is None or gpu4 - gpu0 < 40,
              f"dedicated GPU {gpu0} -> {gpu4} MB")
    check("stop_does_not_block", max(stop_ms or [0]) < 300, f"{max(stop_ms or [0]):.0f} ms")


def restart_from_header():
    """Generator: the header's Start/Restart button, then wait for the docked game."""
    area = largest_game_view()
    with bpy.context.temp_override(window=window(), area=area, region=region_of(area, "HEADER")):
        outcome = bpy.ops.gamestudio.game_view_start()
    ok = outcome == {"FINISHED"}
    ok = ok and (yield from wait_docked_game())
    check("restart_runs_the_game", ok, f"{outcome} {status()['state']} {status()['page_state']}")


def phase_devtools():
    """The header's DevTools button opens Chromium DevTools for the docked page in its own window."""
    if not status()["active"]:
        yield from restart_from_header()
    area = largest_game_view()
    with bpy.context.temp_override(window=window(), area=area, region=region_of(area, "HEADER")):
        outcome = bpy.ops.gamestudio.game_view_devtools()
    check("devtools_operator", outcome == {"FINISHED"}, outcome)
    ok = yield Until(lambda: status().get("devtools"), 10)
    check("devtools_opened", ok, status().get("devtools"))
    yield wait_frames(10, 5)
    check("game_runs_with_devtools", status()["page_state"] == "running" and frames() > 0, status()["frames"])
    gv().open_devtools(False)
    ok = yield Until(lambda: not status().get("devtools"), 5)
    check("devtools_closed", ok)


def phase_input_probe():
    url = PROBE.start()
    gv().start(url)
    ok = yield Until(lambda: status()["page_state"] == "running" and frames() > 5 and PROBE.info, 30)
    if not check("probe_page_runs", ok, f"{status()['state']} {status()['page_state']} {PROBE.info}"):
        return
    region = region_of(largest_game_view())
    yield Until(lambda: (status()["width"], status()["height"]) == (region.width, region.height), 5)
    dpr = PROBE.info.get("dpr") or 1.0
    got = []
    PROBE.take()

    # A click at a known point lands at the same point in the page (CSS pixels).
    fx, fy = 0.25, 0.75
    SIM.move(*region_point(region, fx, fy))
    SIM.tap("LEFTMOUSE")
    ok = yield Until(lambda: PROBE.seen(got, "click"), 3)
    down = next((e for e in got if e["type"] == "pointerdown"), None)
    want = (region.width * fx / dpr, region.height * (1 - fy) / dpr)
    check("probe_click", ok and down and abs(down["x"] - want[0]) <= 1.5 and abs(down["y"] - want[1]) <= 1.5
          and down["button"] == 0 and down["buttons"] == 1 and down["target"] == "game",
          f"pointerdown {down and (down['x'], down['y'], down['button'], down['buttons'], down['target'])}, "
          f"want {want}")
    check("probe_focus", any(e["type"] == "focus" for e in got))

    # Right button, motion with movementX/Y, and a wheel notch.
    got.clear()
    SIM.tap("RIGHTMOUSE")
    ok = yield Until(lambda: PROBE.seen(got, "contextmenu"), 3)
    check("probe_right_button", ok and any(e["type"] == "pointerdown" and e["button"] == 2 and e["buttons"] == 2
                                           for e in got))
    got.clear()
    SIM.move(SIM.x + 40, SIM.y - 30)
    ok = yield Until(lambda: PROBE.seen(got, "pointermove"), 3)
    move = next((e for e in got if e["type"] == "pointermove"), None)
    check("probe_motion", ok and move and abs(move["mx"] - 40 / dpr) <= 1 and abs(move["my"] - 30 / dpr) <= 1,
          move)
    got.clear()
    SIM.wheel("WHEELDOWNMOUSE")
    ok = yield Until(lambda: PROBE.seen(got, "wheel"), 3)
    wheel = next((e for e in got if e["type"] == "wheel"), None)
    check("probe_wheel", ok and wheel and wheel["dy"] == 100, wheel)

    # Keys: code and key, with Shift.
    got.clear()
    SIM.tap("A")
    SIM.press("LEFT_SHIFT")
    SIM.tap("A", shift=True)
    SIM.release("LEFT_SHIFT")
    SIM.tap("SPACE")
    ok = yield Until(lambda: PROBE.seen(got, "keyup", code="Space"), 3)
    keys = [(e["type"], e["code"], e["key"]) for e in got if e["type"] in ("keydown", "keyup")]
    want_keys = [("keydown", "KeyA", "a"), ("keyup", "KeyA", "a"), ("keydown", "ShiftLeft", "Shift"),
                 ("keydown", "KeyA", "A"), ("keyup", "KeyA", "A"), ("keyup", "ShiftLeft", "Shift"),
                 ("keydown", "Space", " "), ("keyup", "Space", " ")]
    check("probe_keys", ok and keys == want_keys, keys)

    # The editor's play shortcuts reach the captured game.
    got.clear()
    SIM.tap("F5")
    SIM.tap("F6")
    SIM.tap("F7")
    SIM.press("LEFT_SHIFT")
    SIM.tap("F5", shift=True)
    SIM.release("LEFT_SHIFT")
    ok = yield Until(lambda: PROBE.seen(got, "keyup", code="ShiftLeft"), 3)
    downs = [(e["code"], e["shift"]) for e in got if e["type"] == "keydown" and e["code"] != "ShiftLeft"]
    check("probe_play_keys", ok and downs == [("F5", False), ("F6", False), ("F7", False), ("F5", True)], downs)

    # Pointer lock: the game asks (KeyL), the editor grabs the cursor and sends relative motion.
    got.clear()
    SIM.tap("L")
    ok = yield Until(lambda: status()["cursor_grabbed"], 3)
    check("pointer_lock_grabs_cursor", ok, status()["cursor_grabbed"])
    yield Until(lambda: PROBE.seen(got, "pointerlockchange", locked=True), 2)
    got.clear()
    for _ in range(5):
        SIM.move(SIM.x + 12, SIM.y)
        yield 0.02
    yield Until(lambda: PROBE.seen(got, "pointermove", locked=True)
                and sum(e["mx"] for e in got if e["type"] == "pointermove") >= 60 / dpr - 2, 3)
    moves = [e for e in got if e["type"] == "pointermove" and e["locked"]]
    total = sum(e["mx"] for e in moves)
    check("locked_relative_motion", abs(total - 60 / dpr) <= 2, f"{len(moves)} moves, movementX {total}")
    # Esc alone belongs to the game while it holds the pointer: it exits pointer lock itself.
    got.clear()
    SIM.tap("ESC")
    ok = yield Until(lambda: not status()["cursor_grabbed"], 3)
    seen = yield Until(lambda: PROBE.seen(got, "keydown", code="Escape"), 2)
    check("esc_reaches_locked_game", ok and seen and status()["captured"],
          f"grabbed {status()['cursor_grabbed']} captured {status()['captured']} page saw Escape {seen}")
    # Lock again, then Shift+Esc: capture and lock both end.
    got.clear()
    SIM.tap("L")
    ok = yield Until(lambda: status()["cursor_grabbed"], 3)
    SIM.shift_esc()
    ok = ok and (yield Until(lambda: not status()["captured"] and not status()["cursor_grabbed"], 3))
    yield Until(lambda: PROBE.seen(got, "blur"), 2)
    check("shift_esc_ends_pointer_lock", ok and any(e["type"] == "pointerlockchange" and not e["locked"]
                                                     for e in got), [e["type"] for e in got][-6:])
    # Nothing reaches the page after the release.
    got.clear()
    SIM.tap("B")
    SIM.move(SIM.x - 20, SIM.y)
    yield 0.4
    PROBE.seen(got, "keydown")
    check("released_input_skips_game", not any(e["type"] in ("keydown", "pointermove") for e in got),
          [e["type"] for e in got])


def gv_url():
    return ext("editors.game_view").game_url()


def _own_pid():
    import os
    return os.getpid()


def play_scenario():
    if not (yield from setup()):
        return
    yield from phase_input_demo()
    yield from phase_robustness()
    yield from phase_devtools()
    yield from phase_input_probe()
    # Quit with the game running: the driver checks that nothing survives the editor.
    if not status()["active"]:
        gv().start(PROBE_URL())
    yield Until(lambda: frames() > 0, 20)
    results["quit_descendants"] = gv_procs.descendants(_own_pid())
    check("finished_in_time", True, f"{time.monotonic() - started:.1f}s")
    finish(quit_running=True)


def PROBE_URL():  # noqa: N802
    return f"http://127.0.0.1:{PROBE.server.server_address[1]}/"


# --- Scenario: CPU mode ---------------------------------------------------------------------

DRAWS = {}


def _count():
    area, region = bpy.context.area, bpy.context.region
    if area is None or region is None:
        return
    try:
        kind = area.ui_type or area.type
    except (AttributeError, TypeError, ValueError):
        kind = area.type  # global areas (top bar, status bar) have no ui_type
    key = f"{kind}.{region.type}"
    DRAWS[key] = DRAWS.get(key, 0) + 1


_counters = []


def install_draw_counters():
    """Counts region draws by "<area ui_type>.<region type>" (GAMESTUDIO_GAME_VIEW.WINDOW, ...)."""
    if _counters:
        return
    for name in dir(bpy.types):
        space = getattr(bpy.types, name)
        if not (name.startswith("Space") and isinstance(space, type) and hasattr(space, "draw_handler_add")):
            continue
        for region in ("WINDOW", "HEADER", "UI"):
            try:
                _counters.append((space, space.draw_handler_add(_count, (), region, "POST_PIXEL")))
            except (TypeError, ValueError, RuntimeError):
                pass

    # The status bar has no Python space type; its header's draw runs on every redraw.
    def count_statusbar(_self, _context):
        DRAWS["STATUSBAR.HEADER"] = DRAWS.get("STATUSBAR.HEADER", 0) + 1

    bpy.types.STATUSBAR_HT_header.append(count_statusbar)
    DRAWS.setdefault("STATUSBAR.HEADER", 0)


def sample_cpu(seconds):
    """Generator: process CPU (% of one core), frames uploaded and draws per second over `seconds`."""
    DRAWS.clear()
    cpu0, wall0, up0 = time.process_time(), time.perf_counter(), frames()
    yield seconds
    wall = time.perf_counter() - wall0
    return {"cpu_percent_of_one_core": round(100 * (time.process_time() - cpu0) / wall, 1),
            "frames_uploaded_per_s": round((frames() - up0) / wall, 1),
            "draws_per_s": {k: round(v / wall, 1) for k, v in sorted(DRAWS.items())},
            "upload_ms": round(status()["timings"].get("upload_ms", 0), 2),
            "game_size": f"{status()['width']}x{status()['height']}"}


def cpu_scenario():
    ok = yield Until(lambda: _shell_ready(), 30)
    if not check("shell_ready", ok):
        return
    ensure_window_shown()
    view3d = next(a for a in window().screen.areas if a.type == "VIEW_3D")
    gv().open(view3d)
    install_draw_counters()
    yield 2.0
    measure("cpu_idle_no_game", (yield from sample_cpu(5)))
    area = largest_game_view()
    with bpy.context.temp_override(window=window(), area=area, region=region_of(area)):
        bpy.ops.gamestudio.play()
    ok = yield from wait_docked_game()
    if not check("docked_game_runs", ok, status()):
        return
    yield 3.0
    running = yield from sample_cpu(10)
    measure("cpu_game_docked", running)
    others = {k: v for k, v in running["draws_per_s"].items() if not k.startswith("GAMESTUDIO_GAME_VIEW.")}
    check("only_game_view_redraws", all(v < 2 for v in others.values()) and
          running["draws_per_s"].get(GV_WINDOW, 0) > 30, running["draws_per_s"])
    win = window()
    home = win.workspace
    win.workspace = next(w for w in bpy.data.workspaces if w != home and not any(
        gv().shows_game_view(a) for s in w.screens for a in s.areas))
    yield 1.5
    measure("cpu_game_hidden", (yield from sample_cpu(5)))
    win.workspace = home
    yield 1.0
    gv().stop()
    yield 1.5
    measure("cpu_after_stop", (yield from sample_cpu(5)))
    check("finished_in_time", True, f"{time.monotonic() - started:.1f}s")


run(play_scenario() if MODE == "play" else cpu_scenario())
