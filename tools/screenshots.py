# SPDX-License-Identifier: GPL-3.0-or-later
"""Regenerate the extension-listing screenshots in docs/images/extension (docs/EXTENSION-LISTING.md).

    python tools/screenshots.py [--blender PATH] [--out DIR] [--only NAME ...] [--timeout SECONDS]

Copies examples/demo-game (without node_modules, dist, exports and logs) to .dev/shots/demo-game and
starts a UI Blender, 1600 x 900, with --factory-startup, a scratch profile (.dev/shots/profile), a
scratch temp folder (.dev/shots/tmp, so its quit.blend never replaces your "Recover Last Session"),
this checkout's extension (tools/blender_dev.py) and --enable-event-simulate (it ignores your mouse
and keyboard, and never takes the focus). Blender then walks the UI and captures each SHOTS entry;
for the agent shots this script drives the real gamestudio-mcp server
(runtime/packages/mcp/bin/gamestudio-mcp.js) through a scripted MCP stdio session, which builds a
few objects with create_from_kit. Nothing is saved to the copied project; the real demo is never
opened. The Blender it started is killed (by PID) if it hangs.

The shots go to .dev/shots/out first. When all of them were taken they replace the PNGs in --out
(default docs/images/extension; with --only, just those files). tools/screenshot_workspace.py
captures one shot with the same code.

OPTIONAL_SHOTS are taken only when --only names them. "--only embedded-play" presses Play with the
Embedded game display, which starts the copy's dev server and streams the game into the Game
workspace's 3D View (it needs Node.js, the project's packages and Edge or Chrome). It then clicks
the game, holds W and checks over the live link that the player moved, gives the input back with
Shift+Esc, presses F8 and checks the 3D View is back; those checks are printed, and their pictures
go to .dev/shots/out/checks.

Run inside Blender (it runs itself there), it does the Blender part.
"""

import json
import os
import pathlib
import queue
import shutil
import subprocess
import sys
import threading
import time

REPO = pathlib.Path(__file__).resolve().parents[1]
SHOT_DIR = REPO / ".dev" / "shots"
OUT = REPO / "docs" / "images" / "extension"
DEMO = REPO / "examples" / "demo-game"
MCP_SERVER = REPO / "runtime" / "packages" / "mcp" / "bin" / "gamestudio-mcp.js"
EXTENSION = "bl_ext.system.gamestudio"
WIDTH, HEIGHT = 1600, 900

# (file name without .png, what it shows): the order they are taken in is the scenario's.
SHOTS = (
    ("01-welcome", "The welcome after the extension is first enabled"),
    ("02-launcher", "The launcher with a project open"),
    ("03-game-workspace", "The Game workspace with an entity selected and the game's messages"),
    ("04-object-game", "Object Properties > Game: the entity ID, Add Component and the components"),
    ("05-scene-project", "Scene Properties > Game Project"),
    ("06-console", "The Console with an error, and its header"),
    ("07-install-claude-code", "When Claude Code is missing"),
    ("08-claude-connected", "Objects the agent just built, and the agent in the status bar"),
    ("09-agent-popover", "The agent popover from the top bar"),
    ("10-game-assets", "The Game Assets popover"),
    ("11-preferences", "The preferences"),
)
AGENT_SHOTS = {"08-claude-connected", "09-agent-popover", "10-game-assets"}
# Taken only with --only (see the module docstring).
OPTIONAL_SHOTS = (
    ("embedded-play", "Play with the Embedded game display: the game streamed into the Game workspace"),
)

# What the scripted MCP session builds: (kit, location, name).
AGENT_BUILDS = (
    ("tree", [-2.5, -3.5, 0.0], "Oak"),
    ("rock", [-1.0, -5.5, 0.0], "Boulder"),
    ("barrel", [-4.5, -6.0, 0.0], "Barrel"),
)

GAME_LOGS = [
    {"msg": "GameStudio engine ready: 14 entities, 6 systems", "level": "info", "source": "src/main.ts:12:3"},
    {"msg": "Player spawned at PlayerStart", "level": "info", "source": "src/main.ts:20:5"},
    {"msg": "Coin collected (score 1)", "level": "info", "source": "src/systems/coins.ts:18:7"},
    {"msg": "Coin collected (score 2)", "level": "info", "source": "src/systems/coins.ts:18:7"},
    {"msg": "Bounce pad has no target height; using 4", "level": "warn", "source": "src/systems/bounce-pads.ts:9:11"},
    {"msg": "TypeError: Cannot read properties of undefined (reading 'value')", "level": "error",
     "file": "src/systems/coins.ts", "line": 24, "col": 15,
     "stack": "at collect (src/systems/coins.ts:24:15)\nat run (src/main.ts:31:9)"},
]


# --- Inside Blender ------------------------------------------------------------------------


def in_blender(opts: dict) -> None:
    import importlib

    import bpy

    out = pathlib.Path(opts["out"])
    signal = pathlib.Path(opts["signal"])
    demo = pathlib.Path(opts["demo"])
    wanted = set(opts["only"].split(",")) if opts.get("only") else {name for name, _ in SHOTS}
    out.mkdir(parents=True, exist_ok=True)
    state = {"mouse": (0, 0)}

    def note(kind, text):
        print(f"SCREENSHOT_{kind} {text}", flush=True)

    def module(name):
        return importlib.import_module(f"{EXTENSION}.{name}")

    # The copy of the example made for the shots runs its scripts (core/trust.py), for this run only: no question.
    module("core.trust").trust(demo, "screenshots", save=False)

    # Window, areas and simulated events (--enable-event-simulate).

    def window():
        return bpy.context.window_manager.windows[0]

    def area(kind):
        areas = [a for a in window().screen.areas if a.type == kind]
        return max(areas, key=lambda a: a.width * a.height) if areas else None

    def region(owner, kind="WINDOW"):
        return next((r for r in owner.regions if r.type == kind), None)

    def override(owner=None, kind="WINDOW"):
        win = window()
        owner = owner or area("VIEW_3D") or win.screen.areas[0]
        return dict(window=win, screen=win.screen, area=owner, region=region(owner, kind))

    def mouse(x, y):
        state["mouse"] = (int(x), int(y))
        window().event_simulate(type="MOUSEMOVE", value="NOTHING", x=int(x), y=int(y))

    def press(key):
        x, y = state["mouse"]
        window().event_simulate(type=key, value="PRESS", x=x, y=y)
        window().event_simulate(type=key, value="RELEASE", x=x, y=y)

    def drag(x, y, dx, dy):
        """Drag a screen edge from (x, y), as a person resizes areas (area_move polls false here)."""
        mouse(x, y)
        yield 0.1
        window().event_simulate(type="LEFTMOUSE", value="PRESS", x=int(x), y=int(y))
        yield 0.1
        for step in range(1, 5):
            mouse(x + dx * step / 4, y + dy * step / 4)
            yield 0.05
        window().event_simulate(type="LEFTMOUSE", value="RELEASE", x=int(x + dx), y=int(y + dy))
        yield 0.3
        mouse(WIDTH // 2, HEIGHT // 2)
        yield 0.1

    def click(x, y):
        """Click at (x, y): opens a popover from its button, as a person does."""
        mouse(x, y)
        yield 0.2
        window().event_simulate(type="LEFTMOUSE", value="PRESS", x=int(x), y=int(y))
        window().event_simulate(type="LEFTMOUSE", value="RELEASE", x=int(x), y=int(y))
        yield 1.0

    def wheel(owner, steps):
        """Scroll an editor with the mouse wheel over it (down for positive steps)."""
        mouse(owner.x + owner.width // 2, owner.y + owner.height // 2)
        for _ in range(abs(steps)):
            press("WHEELDOWNMOUSE" if steps > 0 else "WHEELUPMOUSE")
            yield 0.05

    def scroll(owner, steps):
        """Scroll an editor's main region by 40 px steps (down for positive steps)."""
        with bpy.context.temp_override(**override(owner)):
            for _ in range(abs(steps)):
                (bpy.ops.view2d.scroll_down if steps > 0 else bpy.ops.view2d.scroll_up)()

    def shoot(name, owner=None, folder=None):
        """The whole window, or one area; only the wanted shots (checks: any name, in `folder`)."""
        if folder is None and name not in wanted:
            return
        path = (folder or out) / f"{name}.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.unlink(missing_ok=True)
        with bpy.context.temp_override(**override()):
            bpy.ops.wm.redraw_timer(type="DRAW_WIN_SWAP", iterations=1)
        if owner is None:
            with bpy.context.temp_override(**override()):
                bpy.ops.screen.screenshot(filepath=str(path), check_existing=False, hide_props_region=True)
        else:
            with bpy.context.temp_override(**override(owner)):
                bpy.ops.screen.screenshot_area(filepath=str(path), check_existing=False, hide_props_region=True)
        note("OK" if path.is_file() else "ERROR", f"{name} {path}")

    def properties(context):
        props = area("PROPERTIES")
        props.spaces.active.context = context
        scroll(props, -60)  # back to the top
        return props

    def select(objects, active=None):
        view_layer = bpy.context.view_layer
        for obj in view_layer.objects:
            obj.select_set(obj in objects)
        view_layer.objects.active = active or (objects[0] if objects else None)

    def agent_shots():
        """08-10: this script's MCP session builds a few objects through the agent bridge."""
        bridge = module("bridge")
        if not bridge.is_running():
            bridge.start(force=True)
        deadline = time.monotonic() + 15
        while not (demo / ".gamestudio" / "blender.json").is_file() and time.monotonic() < deadline:
            yield 0.25
        (signal / "mcp-start").write_text("go", encoding="utf-8")
        deadline = time.monotonic() + 120
        while not any((signal / n).exists() for n in ("mcp-done", "mcp-failed")) and time.monotonic() < deadline:
            yield 0.25
        if not (signal / "mcp-done").exists():
            note("ERROR", "the MCP session did not finish; no agent shots")
            return
        yield 1.0
        built = [bpy.data.objects[name] for _kit, _where, name in AGENT_BUILDS if name in bpy.data.objects]
        properties("OBJECT")
        select(built, built[-1] if built else None)
        activity = module("activity")
        yield 5.0  # the status bar shows the last call for a few seconds: wait for "Claude: idle"

        # 08: what the agent built, and who is connected.
        shoot("08-claude-connected")

        # 09: the agent popover, with the newest action's details (Undo to Here).
        newest = next((e for e in activity.entries() if e["changes"]), None)
        if newest is not None:
            activity.select(newest["id"])
        if "09-agent-popover" in wanted:
            # The top bar is not in screen.areas. The agent button ends about 350 px left of the window's
            # right edge: the scene and view layer selectors right of it have fixed widths.
            yield from click(WIDTH - int(opts.get("agent_from_right", 365)), HEIGHT - 12)
            shoot("09-agent-popover")
            press("ESC")
            mouse(WIDTH // 2, HEIGHT // 2)
            yield 0.5

        # 10: Game Assets, with what the agent built added to a "props" library.
        if "10-game-assets" in wanted:
            assetlib = module("assetlib")
            try:
                assetlib.add_to_library(built, library="props", separate=True)
            except Exception as exc:
                note("WARNING", f"add_to_library: {exc}")
            select(built[:1])
            view3d = area("VIEW_3D")
            header = region(view3d, "HEADER")
            # Game Assets follows Object Mode, View, Select, Add and Object in the header.
            yield from click(header.x + int(opts.get("assets_x", 386)), header.y + header.height // 2)
            shoot("10-game-assets")
            press("ESC")
            yield 0.5


    def embedded_play():
        """Play with the Embedded game display: the game streams into the Game workspace's 3D View.
        Then a click captures input, W moves the player (checked over the live link), Shift+Esc
        gives the input back, and F8 (Stop) brings the 3D View back."""
        game_stream, play, link = module("editors.game_stream"), module("core.play"), module("core.link")
        checks = out / "checks"
        bpy.context.preferences.addons[EXTENSION].preferences.game_display = "EMBEDDED"
        link.get_link().logs.clear()  # the Console shows the streamed game's own messages
        module("editors.console").sync(force=True)
        view3d = area("VIEW_3D")
        before = (view3d.spaces.active.region_3d.view_distance, view3d.spaces.active.shading.type)
        with bpy.context.temp_override(**override(view3d)):
            bpy.ops.gamestudio.play()
        deadline = time.monotonic() + float(opts.get("play_timeout", 180))
        session = None
        while time.monotonic() < deadline:
            session = game_stream.session()
            if session is not None and session.status == "error":
                break
            if (session is not None and session.status == "running" and game_stream._s.texture is not None
                    and not play.is_pending() and link.get_link().game_state == "playing"):
                break
            yield 0.5
        game = area("IMAGE_EDITOR")
        if session is None or session.status != "running" or game is None:
            note("ERROR", f"the game did not stream: {getattr(session, 'message', None) or play.status()}")
            return
        yield 3.0  # a few seconds of play
        note("INFO", f"streaming {session.fps:.0f} fps, frame {game_stream.frame_size()}, mode {session.mode}")
        shoot("embedded-play")

        def player(found):
            client = link.get_link()
            peer = game_stream.stream_peer(client)
            if peer is None:
                return
            client.request({"t": "state.request", "to": peer["peer"], "filter": {"component": "CharacterController"}},
                           lambda reply: found.append((reply or {}).get("entities") or []))
            wait = time.monotonic() + 5
            while not found and time.monotonic() < wait:
                yield 0.1

        main = region(game)
        x, y = main.x + main.width // 2, main.y + main.height // 2
        mouse(x, y)
        yield 0.5
        start = []
        yield from player(start)
        window().event_simulate(type="LEFTMOUSE", value="PRESS", x=x, y=y)
        window().event_simulate(type="LEFTMOUSE", value="RELEASE", x=x, y=y)
        yield 0.5
        note("INFO", f"captured: {game_stream._s.captured}")
        window().event_simulate(type="W", value="PRESS", x=x, y=y)
        yield 1.5
        shoot("captured", folder=checks)
        window().event_simulate(type="W", value="RELEASE", x=x, y=y)
        yield 0.5
        end = []
        yield from player(end)
        if start and end and start[-1] and end[-1]:
            a, b = start[-1][0]["position"], end[-1][0]["position"]
            moved = sum((p - q) ** 2 for p, q in zip(a, b)) ** 0.5
            note("INFO" if moved > 0.5 else "ERROR", f"W moved the player {moved:.2f} m: {a} -> {b}")
        else:
            note("ERROR", "could not read the player's position")
        window().event_simulate(type="ESC", value="PRESS", x=x, y=y, shift=True)
        window().event_simulate(type="ESC", value="RELEASE", x=x, y=y, shift=True)
        yield 0.5
        note("INFO", f"released: {not game_stream._s.captured}")
        press("F8")
        yield 1.5
        view3d = area("VIEW_3D")
        after = (view3d.spaces.active.region_3d.view_distance, view3d.spaces.active.shading.type)
        restored = area("IMAGE_EDITOR") is None and not game_stream.view_open() and after == before
        note("INFO" if restored else "ERROR", f"Stop brought the 3D View back: {restored} ({before} -> {after})")
        shoot("stopped", folder=checks)

    # The scenario: a generator that yields the seconds to wait before its next step.

    def scenario():
        bpy.context.preferences.view.use_save_prompt = False  # quit without "Save changes?"
        if EXTENSION not in bpy.context.preferences.addons:
            bpy.ops.preferences.addon_enable(module=EXTENSION)
        prefs = bpy.context.preferences.addons[EXTENSION].preferences
        prefs.show_launcher_at_startup = False
        yield 1.0
        press("ESC")  # Blender's splash, if any
        yield 0.5

        # 01: the welcome, over Blender's startup scene.
        if "01-welcome" in wanted:
            mouse(WIDTH // 2 - 310, HEIGHT - 190)  # the popup opens right of the mouse
            with bpy.context.temp_override(**override()):
                bpy.ops.gamestudio.launcher("INVOKE_DEFAULT", welcome=True)
            yield 1.0
            shoot("01-welcome")
            press("ESC")
            yield 0.5

        # The demo copy, as Open Project opens it (without installing its packages).
        projects, workspaces = module("projects"), module("workspaces")
        console, link, components = module("editors.console"), module("core.link"), module("core.components")
        projects.remember(demo)
        bpy.ops.wm.open_mainfile(filepath=str(demo / "scenes" / "main.blend"))
        yield 2.0
        if workspaces.show_game(window()) is None:
            note("ERROR", "no Game workspace")
        yield 1.5
        scene_objects = list(bpy.context.scene.objects)
        with_components = sorted((o for o in scene_objects if components.list_components(o)),
                                 key=lambda o: (o.name != "Player", -len(components.list_components(o)), o.name))
        entity = with_components[0] if with_components else (scene_objects[0] if scene_objects else None)
        select([entity] if entity else [])
        now = time.time()
        logs = link.get_link().logs
        for offset, entry in enumerate(GAME_LOGS):
            logs.append({"t": "log", "time": now - len(GAME_LOGS) + offset, **entry})
        console.sync(force=True)
        yield 2.5

        # 03: the Game workspace, Properties scrolled to the Game panel.
        scroll(area("PROPERTIES"), int(opts.get("object_scroll", 13)))
        yield 0.8
        if window().workspace.name != workspaces.NAME:
            note("WARNING", f"the window shows {window().workspace.name}")
        shoot("03-game-workspace")

        if "embedded-play" in wanted:
            yield from embedded_play()

        # 06: the Console, taller.
        text_area = area("TEXT_EDITOR")
        if text_area is not None:
            text_area.show_menus = False
            yield from drag(text_area.x + text_area.width // 2, text_area.y + text_area.height + 1, 0, 100)
            shoot("06-console", area("TEXT_EDITOR"))

        # 04 and 05: Properties, made wider, scrolled to GameStudio's panels.
        props = area("PROPERTIES")
        yield from drag(props.x - 1, props.y + props.height // 2, -int(opts.get("widen", 240)), 0)
        props = properties("OBJECT")
        scroll(props, int(opts.get("object_scroll", 13)))
        yield 0.8
        shoot("04-object-game", props)
        props = properties("SCENE")
        scroll(props, int(opts.get("scene_scroll", 10)))
        yield 0.8
        shoot("05-scene-project", props)

        # 02: the launcher, with the project in Recent Projects.
        if "02-launcher" in wanted:
            mouse(WIDTH // 2 - 310, HEIGHT - 190)
            with bpy.context.temp_override(**override()):
                bpy.ops.gamestudio.launcher("INVOKE_DEFAULT")
            yield 1.0
            shoot("02-launcher")
            press("ESC")
            yield 0.5

        # 07: Claude Code missing: Scene > Game Project says so, and the install dialog.
        if "07-install-claude-code" in wanted:
            claude_code = module("claude_code")
            found = claude_code.claude_path
            claude_code.claude_path = lambda refresh=False: None
            props = properties("SCENE")
            scroll(props, int(opts.get("scene_scroll", 10)))
            mouse(WIDTH // 2 - 120, HEIGHT // 2 + 120)
            with bpy.context.temp_override(**override()):
                bpy.ops.gamestudio.claude_code_install("INVOKE_DEFAULT")
            yield 1.0
            shoot("07-install-claude-code")
            press("ESC")
            claude_code.claude_path = found
            yield 0.5

        if wanted & AGENT_SHOTS:
            yield from agent_shots()

        # 11: the preferences in the main window.
        if "11-preferences" in wanted:
            view3d = area("VIEW_3D")
            view3d.ui_type = "PREFERENCES"
            bpy.context.preferences.active_section = "ADDONS"
            # Automatic shows the code editor's full path, which has the user's name in it.
            prefs.code_editor = "SYSTEM"
            bpy.context.window_manager.addon_search = "GameStudio"
            with bpy.context.temp_override(**override(view3d)):
                bpy.ops.screen.screen_full_area()
            yield 0.8
            with bpy.context.temp_override(**override(area("PREFERENCES"))):
                bpy.ops.preferences.addon_expand(module=EXTENSION)
            yield 0.5
            yield from wheel(area("PREFERENCES"), int(opts.get("prefs_scroll", 7)))
            yield 0.8
            shoot("11-preferences")
        (signal / "shots-done").write_text("done", encoding="utf-8")
        yield from quit_blender()

    def quit_blender():
        note("DONE", str(out))
        yield 0.3
        bpy.ops.wm.quit_blender()

    steps = scenario()

    def tick():
        try:
            return next(steps)
        except StopIteration:
            return None
        except Exception:
            import traceback

            note("ERROR", traceback.format_exc().replace("\n", " | "))
            os._exit(1)

    bpy.app.timers.register(tick, first_interval=1.0, persistent=True)


# --- Outside: the MCP session ----------------------------------------------------------------


class McpSession:
    """The gamestudio-mcp server on stdio, spoken to like Claude Code does: NDJSON JSON-RPC 2.0."""

    def __init__(self, project: pathlib.Path, env: dict, log: pathlib.Path):
        node = shutil.which("node")
        if node is None:
            raise RuntimeError("Node.js is needed for the MCP server")
        self.log = open(log, "w", encoding="utf-8")
        self.proc = subprocess.Popen([node, str(MCP_SERVER), "--project", str(project)], cwd=str(project), env=env,
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.log, text=True,
                                     encoding="utf-8")
        self.lines = queue.Queue()
        self.next_id = 1
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        for line in self.proc.stdout:
            if line.strip():
                self.lines.put(json.loads(line))

    def send(self, message: dict) -> None:
        self.proc.stdin.write(json.dumps({"jsonrpc": "2.0", **message}) + "\n")
        self.proc.stdin.flush()

    def rpc(self, method: str, params: dict | None = None, timeout: float = 60.0) -> dict:
        msg_id, self.next_id = self.next_id, self.next_id + 1
        self.send({"id": msg_id, "method": method, "params": params or {}})
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                msg = self.lines.get(timeout=0.25)
            except queue.Empty:
                continue
            if msg.get("id") == msg_id:
                if "error" in msg:
                    raise RuntimeError(f"{method}: {msg['error']}")
                return msg["result"]
        raise RuntimeError(f"{method} timed out")

    def call(self, name: str, arguments: dict) -> str:
        result = self.rpc("tools/call", {"name": name, "arguments": arguments})
        text = next((c.get("text", "") for c in result.get("content", []) if c.get("type") == "text"), "")
        if result.get("isError"):
            raise RuntimeError(f"{name}: {text[:300]}")
        return text

    def close(self) -> None:
        try:
            self.proc.stdin.close()
            self.proc.wait(timeout=10)
        except (OSError, subprocess.TimeoutExpired):
            self.proc.kill()
        self.log.close()


def agent_session(project: pathlib.Path, env: dict) -> McpSession:
    """Connect as Claude Code would (the bridge shows the client's name) and build AGENT_BUILDS."""
    session = McpSession(project, env, SHOT_DIR / "mcp.log")
    session.rpc("initialize", {"protocolVersion": "2025-11-25", "capabilities": {},
                               "clientInfo": {"name": "claude-code", "version": ""}})
    session.send({"method": "notifications/initialized"})
    session.call("scene_summary", {"max_objects": 5})
    for kind, location, name in AGENT_BUILDS:
        session.call("create_from_kit", {"kind": kind, "location": location, "name": name})
    return session


# --- Outside: copy the demo, start Blender, wait ---------------------------------------------


def copy_demo(target: pathlib.Path) -> pathlib.Path:
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(DEMO, target, ignore=shutil.ignore_patterns(
        "node_modules", "dist", "export", "checkpoints", "*.blend1", "*.log", "blender.json"))
    return target


def capture(blender: str | None, only: list[str] | None, timeout: float, extra: dict | None = None) -> list:
    """Take the shots into SHOT_DIR/out; returns the paths taken."""
    sys.path.insert(0, str(REPO / "tools"))
    import blender_dev

    exe = blender_dev.find_blender(blender)
    profile, tmp, signal, staged = (SHOT_DIR / name for name in ("profile", "tmp", "signal", "out"))
    for folder in (profile, tmp, signal, staged):
        shutil.rmtree(folder, ignore_errors=True)
        folder.mkdir(parents=True)
    demo = copy_demo(SHOT_DIR / "demo-game")
    if only and "embedded-play" in only:
        # Its packages resolve from this checkout's node_modules, like the demo's (whose own folder
        # is empty); the dev server only wants the folder. Its tsconfig extends the checkout's.
        (demo / "node_modules").mkdir(exist_ok=True)
        tsconfig = demo / "tsconfig.json"
        base = os.path.relpath(REPO / "tsconfig.base.json", demo).replace(os.sep, "/")
        tsconfig.write_text(tsconfig.read_text(encoding="utf-8").replace("../../tsconfig.base.json", base),
                            encoding="utf-8")
    env = blender_dev.dev_env(str(profile))
    env.update(TEMP=str(tmp), TMP=str(tmp), TMPDIR=str(tmp))  # Blender's quit.blend goes here
    args = {"out": staged, "signal": signal, "demo": demo, **(extra or {})}
    if only:
        args["only"] = ",".join(only)
    cmd = [str(exe), "--factory-startup", "--enable-event-simulate", "--no-window-focus",
           "--window-geometry", "0", "0", str(WIDTH), str(HEIGHT),
           "--python", str(pathlib.Path(__file__).resolve()), "--"]
    for key, value in args.items():
        cmd += [f"--{key}", str(value)]
    output = SHOT_DIR / "blender.log"
    with open(output, "w", encoding="utf-8") as log:
        proc = subprocess.Popen(cmd, env=env, cwd=str(SHOT_DIR), stdout=log, stderr=subprocess.STDOUT)
    print(f"Blender started (PID {proc.pid}); its output: {output}")
    session, started = None, False
    deadline = time.monotonic() + timeout
    try:
        while proc.poll() is None and time.monotonic() < deadline:
            time.sleep(0.25)
            if not started and (signal / "mcp-start").exists():
                started = True
                try:
                    session = agent_session(demo, env)
                    (signal / "mcp-done").write_text("done", encoding="utf-8")
                except (OSError, RuntimeError) as exc:
                    print(f"MCP session failed: {exc}")
                    (signal / "mcp-failed").write_text(str(exc), encoding="utf-8")
            if session is not None and (signal / "shots-done").exists():
                session.close()
                session = None
    finally:
        if proc.poll() is None:
            print(f"Blender (PID {proc.pid}) did not finish in {timeout:.0f} s; stopping it")
            proc.kill()
            proc.wait()
        if session is not None:
            session.close()
    lines = output.read_text(encoding="utf-8", errors="replace").splitlines()
    print("\n".join(line for line in lines if "SCREENSHOT" in line or "Error" in line or "Traceback" in line))
    return sorted(staged.glob("*.png"))


def run(blender: str | None, out: pathlib.Path, only: list[str] | None, timeout: float) -> int:
    names = [name for name, _ in SHOTS]
    unknown = sorted(set(only or ()) - set(names) - {name for name, _ in OPTIONAL_SHOTS})
    if unknown:
        print(f"Unknown shots: {', '.join(unknown)}. Known: {', '.join(names + [n for n, _ in OPTIONAL_SHOTS])}")
        return 2
    taken = capture(blender, only, timeout)
    expected = only or names
    missing = sorted(set(expected) - {path.stem for path in taken})
    if missing:
        print(f"Not taken: {', '.join(missing)}; {out} is unchanged")
        return 1
    out.mkdir(parents=True, exist_ok=True)
    if not only:
        for old in out.glob("*.png"):
            old.unlink()
    for path in taken:
        shutil.copy2(path, out / path.name)
    print(f"Saved {len(taken)} screenshots to {out}")
    return 0


def _blender_args(argv: list[str]) -> dict:
    opts = {}
    it = iter(argv)
    for arg in it:
        if arg.startswith("--"):
            opts[arg[2:]] = next(it, "")
    return opts


def main() -> int:
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else sys.argv[1:]
    try:
        import bpy  # noqa: F401
    except ImportError:
        import argparse

        parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
        parser.add_argument("--blender", help="blender executable (default: found automatically)")
        parser.add_argument("--out", type=pathlib.Path, default=OUT, help=f"folder for the PNGs (default: {OUT})")
        parser.add_argument("--only", nargs="+", metavar="NAME",
                            help="take only these shots: " + ", ".join(name for name, _ in SHOTS + OPTIONAL_SHOTS))
        parser.add_argument("--timeout", type=float, default=300.0, help="seconds before Blender is stopped")
        args = parser.parse_args(argv)
        return run(args.blender, args.out.resolve(), args.only, args.timeout)
    in_blender(_blender_args(argv))
    return 0


if __name__ == "__main__":
    code = main()
    if "bpy" not in sys.modules:
        sys.exit(code)
