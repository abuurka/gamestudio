# SPDX-License-Identifier: GPL-3.0-or-later
"""GameStudio UI smoke driver. Runs *inside* a GUI Blender started by run_ui_smoke.py.

Do not run it by hand in a real profile: it enables the extension and saves preferences (to the
runner's scratch profile).

Steps run one at a time from a `bpy.app.timers` callback, which fires in GUI mode once the
event loop is up. A step returns the delay before the next one, so Blender can apply deferred
changes (workspace switches, editor types, popups) and redraw in between. Popups are closed
with simulated Esc presses, so the runner starts Blender with --enable-event-simulate (which
also makes the session ignore the real mouse and keyboard).

Errors are collected three ways: a sys.excepthook (Blender reports draw errors through
PyErr_Print, which calls it), a tee on sys.stderr for tracebacks printed directly (appended
menu functions use traceback.print_exc), and a try/except around every step. The runner also
scans the whole stdout/stderr log, which catches errors raised before this script starts.

Output: <out>/report.json (rewritten after every step) and <out>/shots/*.png.
"""

import collections
import contextlib
import importlib
import inspect
import io
import json
import os
import pathlib
import re
import shutil
import sys
import time
import traceback
import types

import bpy

EXPECTED_WORKSPACES = ("Layout", "Level", "Debug")  # Blender's, then the two Add GameStudio Workspaces adds
EXTENSION = "bl_ext.system.gamestudio"
TOP_BAR_MENUS = "TOPBAR_MT_editor_menus"
REGISTRY_FIXTURE = pathlib.Path(__file__).resolve().parents[1] / "addon" / "fixtures" / "components.json"
# The 3D View sidebar tabs each GameStudio workspace opens (workspaces.py LAYOUT_PROP).
WORKSPACE_TABS = {"Level": ["Game"], "Debug": ["Console", "Project"]}

# Popups (wm.splash, wm.call_menu) and sidebar tabs are built synchronously, and screenshot()
# redraws the window first, so those are opened and captured in one step. Workspace and editor
# switches are applied by the event loop, hence a settle delay before their screenshot.
STEP_DELAY = 0.1     # between plain steps
SETTLE_DELAY = 0.4   # after a workspace / editor-type switch
CLOSE_DELAY = 0.25   # after a simulated Esc (handled on the next event-loop pass)
READY_TIMEOUT = 15.0
MAX_DEEP_MENUS = 80
MENU_DEPTH = 3


# --- Arguments and report -------------------------------------------------------------


def _args() -> dict:
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    opts = {"out": None, "kind": "stock", "t0": None, "timeout": "60", "inject-fault": False}
    it = iter(argv)
    for arg in it:
        key = arg[2:] if arg.startswith("--") else None
        if key == "inject-fault":
            opts[key] = True
        elif key in opts:
            opts[key] = next(it, None)
    if not opts["out"]:
        raise SystemExit("ui_smoke_driver.py needs `-- --out DIR` (run it through run_ui_smoke.py)")
    return opts


ARGS = _args()
OUT = os.path.abspath(ARGS["out"])
SHOTS = os.path.join(OUT, "shots")
REPORT_PATH = os.path.join(OUT, "report.json")
os.makedirs(SHOTS, exist_ok=True)

REPORT = {
    "schema": 1,
    "kind": ARGS["kind"],
    "started": time.time(),
    "finished": False,
    "aborted": None,
    "blender": {},
    "host_info": None,
    "app_template": None,
    "native": None,
    "isolated_profile": None,
    "window": {},
    "window_titles": {},
    "workspaces": {"expected": list(EXPECTED_WORKSPACES), "present": [], "visited": []},
    "menus": {"top_bar": [], "opened": [], "deep_drawn": [], "discovery_notes": []},
    "providers": {"mode": None, "visited": [], "notes": []},
    "shortcuts": {},
    "layouts": {},
    "steps": [],
    "screenshots": [],
    "errors": [],
    "notes": [],
}

_state = {"step": "startup", "quiet": False, "mouse": (0, 0)}


def _log(*parts) -> None:
    print("[ui-smoke]", *parts, flush=True)


def _write_report() -> None:
    tmp = REPORT_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(REPORT, f, indent=1, default=repr)
    os.replace(tmp, REPORT_PATH)


def _error(source: str, text: str) -> None:
    text = text.rstrip()
    lines = [line for line in text.splitlines() if line.strip()]
    REPORT["errors"].append({
        "source": source,
        "step": _state["step"],
        "message": lines[-1].strip() if lines else text,
        "text": text,
    })
    _log(f"ERROR ({source}, step {_state['step']}): {lines[-1] if lines else text}")


def _note(text: str) -> None:
    REPORT["notes"].append(f"{_state['step']}: {text}")
    _log("note:", text)


# --- Error capture --------------------------------------------------------------------

_seen_exceptions = []  # strong refs, so id() of a recorded exception is never reused
_original_excepthook = sys.excepthook


def _excepthook(etype, value, tb):
    # bpy_class_call reports a draw error twice (once into a buffer for the report, once to
    # the console) with the same exception object: record it once.
    if not any(value is seen for seen in _seen_exceptions):
        _seen_exceptions.append(value)
        _error("excepthook", "".join(traceback.format_exception(etype, value, tb)))
    _state["in_hook"] = True
    try:
        _original_excepthook(etype, value, tb)
    finally:
        _state["in_hook"] = False


class _StderrTee:
    """Passes everything to the real stderr and records tracebacks printed outside excepthook."""

    def __init__(self, stream):
        self._stream = stream
        self._pending = ""
        self._block = None

    def write(self, text):
        if not _state.get("in_hook") and not _state["quiet"]:
            self._scan(text)
        return self._stream.write(text)

    def _scan(self, text):
        self._pending += text
        while "\n" in self._pending:
            line, self._pending = self._pending.split("\n", 1)
            self._line(line)

    def _line(self, line):
        if line.startswith("Traceback (most recent call last)"):
            self._block = [line]
        elif self._block is not None:
            self._block.append(line)
            if line and not line[0].isspace():  # the exception line ends the block
                _error("stderr", "\n".join(self._block))
                self._block = None

    def flush(self):
        self._stream.flush()

    def __getattr__(self, name):
        return getattr(self._stream, name)


sys.excepthook = _excepthook
sys.stderr = _StderrTee(sys.stderr)


# --- Step scheduler -------------------------------------------------------------------


class Again:
    """Returned by a step that wants to run again after `delay` seconds."""

    def __init__(self, delay):
        self.delay = delay


_plan = collections.deque()


def plan(name, fn, *args):
    _plan.append((name, fn, args))


def plan_next(steps):
    """Insert steps, given as (name, fn, *args), right after the current one."""
    for name, fn, *args in reversed(steps):
        _plan.insert(1, (name, fn, tuple(args)))


def _tick():
    try:
        return _run_one()
    except BaseException:  # never let the timer die silently: the runner would only see a timeout
        _error("driver", traceback.format_exc())
        _plan.clear()
        _quit()
        return None


def _run_one():
    if not _plan:
        return None
    name, fn, args = _plan[0]
    _state["step"] = name
    started = time.perf_counter()
    ok = True
    try:
        result = fn(*args)
    except Exception:
        ok = False
        result = None
        _error("step", traceback.format_exc())
    if isinstance(result, Again):
        return result.delay
    _plan.popleft()
    REPORT["steps"].append({"name": name, "ok": ok, "seconds": round(time.perf_counter() - started, 3)})
    _write_report()
    if not _plan:
        return None
    return float(result) if isinstance(result, (int, float)) else STEP_DELAY


def _abort():
    """Safety net shortly before the runner's hard timeout: keep the partial report."""
    REPORT["aborted"] = f"driver deadline reached during step {_state['step']!r}"
    _write_report()
    _log(REPORT["aborted"])
    os._exit(3)


# --- Context, events, screenshots -----------------------------------------------------


def window():
    return bpy.context.window_manager.windows[0]


def area_of(win, space_type=None):
    areas = [a for a in win.screen.areas if space_type is None or a.type == space_type]
    return max(areas, key=lambda a: a.width * a.height) if areas else None


def region_of(area, region_type="WINDOW"):
    for region in area.regions:
        if region.type == region_type:
            return region
    return area.regions[-1]


def override(area=None, region_type="WINDOW") -> dict:
    win = window()
    area = area or area_of(win)
    return dict(window=win, screen=win.screen, area=area, region=region_of(area, region_type))


def topbar_override() -> dict:
    area = area_of(window(), "TOPBAR")
    return override(area, "HEADER") if area is not None else override()


def simulate(event_type, value="PRESS"):
    x, y = _state["mouse"]
    window().event_simulate(type=event_type, value=value, x=x, y=y)


def move_mouse(x, y):
    _state["mouse"] = (int(x), int(y))
    simulate("MOUSEMOVE", "NOTHING")


def press(key):
    simulate(key, "PRESS")
    simulate(key, "RELEASE")


def slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_").lower()


def pixel_stats(path: str) -> dict:
    """Blank-screenshot check: luminance spread and colour count, computed with Blender's numpy."""
    import numpy as np

    img = bpy.data.images.load(path, check_existing=False)
    try:
        width, height = img.size
        pixels = np.empty(width * height * 4, dtype=np.float32)
        img.pixels.foreach_get(pixels)
    finally:
        bpy.data.images.remove(img)
    rgb = pixels.reshape(-1, 4)[:, :3]
    luminance = rgb @ np.array([0.2126, 0.7152, 0.0722], dtype=np.float32)
    q = (rgb[::3] * 255.0 + 0.5).astype(np.uint32)
    codes = (q[:, 0] << 16) | (q[:, 1] << 8) | q[:, 2]
    _, counts = np.unique(codes, return_counts=True)
    return {
        "width": int(width),
        "height": int(height),
        "luma_std": round(float(luminance.std()) * 255.0, 3),
        "luma_mean": round(float(luminance.mean()) * 255.0, 3),
        "colors": int(len(counts)),
        "dominant_fraction": round(float(counts.max()) / len(codes), 4),
    }


def screenshot(name: str, caption: str) -> None:
    path = os.path.join(SHOTS, name + ".png")
    if os.path.exists(path):
        os.remove(path)
    with bpy.context.temp_override(**override()):
        bpy.ops.wm.redraw_timer(type="DRAW_WIN_SWAP", iterations=1)  # force a full redraw first
        bpy.ops.screen.screenshot(filepath=path, check_existing=False)
    entry = {"name": name, "file": f"shots/{name}.png", "caption": caption, "step": _state["step"],
             "workspace": window().workspace.name}
    if os.path.isfile(path):
        entry.update(pixel_stats(path))
    else:
        entry["missing"] = True
        _error("check", f"screenshot {name} was not written to {path}")
    REPORT["screenshots"].append(entry)
    _log("screenshot", name, {k: entry.get(k) for k in ("width", "height", "luma_std", "colors")})


def window_titles() -> list:
    """Titles of this process's visible top-level windows (Windows only; Window has no title in RNA)."""
    if os.name != "nt":
        return []
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    pid = os.getpid()
    titles = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def visit(hwnd, _lparam):
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid and user32.IsWindowVisible(hwnd):
            length = user32.GetWindowTextLengthW(hwnd)
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            if buf.value:
                titles.append(buf.value)
        return True

    user32.EnumWindows(visit, 0)
    return titles


def host_module():
    try:
        return importlib.import_module(f"{EXTENSION}.host")
    except ImportError:
        return None


# --- Menu discovery (records layout.menu() calls; never shown, never counted as errors) ---


class _RecordingLayout:
    """A stand-in UILayout: records menu ids passed to menu()/menu_contents(), ignores the rest."""

    def __init__(self, found):
        object.__setattr__(self, "_found", found)

    def __getattr__(self, name):
        if name in ("menu", "menu_contents"):
            def record(*args, **kwargs):
                menu = kwargs.get("menu", args[0] if args else None)
                if isinstance(menu, str):
                    self._found.append(menu)
                return self
            return record
        return self

    def __setattr__(self, name, value):
        pass

    def __call__(self, *args, **kwargs):
        return self

    def __iter__(self):
        return iter((self, self))  # `header, body = layout.panel(...)`

    def __bool__(self):
        return True


class _FakeMenu:
    """Stands in for a Menu instance so its draw() can run against a _RecordingLayout."""

    def __init__(self, cls, layout):
        object.__setattr__(self, "_cls", cls)
        object.__setattr__(self, "layout", layout)

    def __getattr__(self, name):
        raw = inspect.getattr_static(self._cls, name)
        if inspect.isfunction(raw):
            return types.MethodType(raw, self)
        return getattr(self._cls, name)


def is_menu(idname) -> bool:
    cls = getattr(bpy.types, idname, None)
    return isinstance(cls, type) and issubclass(cls, bpy.types.Menu)


def menu_children(idname) -> list:
    cls = getattr(bpy.types, idname)
    draw = inspect.getattr_static(cls, "draw", None)
    if draw is None:
        return []
    found = []
    sink = io.StringIO()
    _state["quiet"] = True
    try:
        with bpy.context.temp_override(**topbar_override()), \
                contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
            draw(_FakeMenu(cls, _RecordingLayout(found)), bpy.context)
    except Exception as exc:  # discovery only: the real draw below reports real errors
        REPORT["menus"]["discovery_notes"].append(f"{idname}: {type(exc).__name__}: {exc}")
    finally:
        _state["quiet"] = False
    return list(dict.fromkeys(found))


def menu_tree(roots, depth=MENU_DEPTH) -> list:
    seen, frontier = [], list(roots)
    for _ in range(depth):
        following = []
        for name in frontier:
            if name in seen or len(seen) >= MAX_DEEP_MENUS:
                continue
            if not is_menu(name):
                REPORT["menus"]["discovery_notes"].append(f"{name}: not a registered menu")
                continue
            seen.append(name)
            following += menu_children(name)
        frontier = following
    return seen


# --- Steps ----------------------------------------------------------------------------

_ready = {"since": None}


def s_ready():
    if _ready["since"] is None:
        _ready["since"] = time.time()
    waited = time.time() - _ready["since"]
    wm = bpy.context.window_manager
    has_window = bool(wm and wm.windows and wm.windows[0].screen is not None)
    if has_window and EXTENSION not in bpy.context.preferences.addons:
        bpy.ops.preferences.addon_enable(module=EXTENSION)  # what a user does in Preferences
    has_extension = EXTENSION in bpy.context.preferences.addons
    if has_window and has_extension:
        return STEP_DELAY
    if waited < READY_TIMEOUT:
        return Again(0.25)
    if not has_window:
        raise RuntimeError("no Blender window after waiting %.0fs" % waited)
    _error("check", f"the {EXTENSION} extension could not be enabled")
    return STEP_DELAY


def s_environment():
    prefs = bpy.context.preferences
    win = window()
    REPORT["blender"] = {
        "version": list(bpy.app.version),
        "version_string": bpy.app.version_string,
        "build_hash": bpy.app.build_hash.decode("ascii", "replace"),
        "binary": bpy.app.binary_path,
    }
    REPORT["app_template"] = prefs.app_template
    REPORT["window"] = {"width": win.width, "height": win.height}
    REPORT["workspaces"]["present"] = sorted(w.name for w in bpy.data.workspaces)
    REPORT["window_titles"]["start"] = window_titles()
    try:
        import _gamestudio  # noqa: F401  (fork builds only)
        REPORT["native"] = True
    except ImportError:
        REPORT["native"] = False
    host = host_module()
    if host is None:
        _error("check", f"cannot import {EXTENSION}.host")
    else:
        REPORT["host_info"] = host.info()
    if prefs.app_template:
        _error("check", f"app template is {prefs.app_template!r}; GameStudio needs none")
    config = bpy.utils.user_resource("CONFIG")
    scratch = os.environ.get("BLENDER_USER_RESOURCES", "")
    REPORT["isolated_profile"] = bool(scratch) and os.path.normcase(os.path.abspath(config)).startswith(
        os.path.normcase(os.path.abspath(scratch)))
    REPORT["user_config"] = config
    if not REPORT["isolated_profile"]:
        _error("check", f"user config {config!r} is not inside BLENDER_USER_RESOURCES {scratch!r}")
    # The simulated cursor rests on the first item of every menu it opens: no tooltips over shots.
    prefs.view.show_tooltips = False
    move_mouse(win.width // 2, win.height // 2)
    if ARGS["inject-fault"]:
        inject_faults()


def inject_faults():
    """Harness self-test (run_ui_smoke.py --inject-fault): these errors must make the run FAIL."""
    _note("injecting faults: an appended Help menu item and a Game sidebar panel that raise")

    def broken_menu_item(_self, _context):
        raise RuntimeError("ui-smoke injected fault: appended menu draw")

    class UISMOKE_PT_injected_fault(bpy.types.Panel):
        bl_space_type = "VIEW_3D"
        bl_region_type = "UI"
        bl_category = "Game"
        bl_label = "Injected fault"

        def draw(self, _context):
            raise RuntimeError("ui-smoke injected fault: panel draw")

    bpy.types.TOPBAR_MT_help.append(broken_menu_item)
    bpy.utils.register_class(UISMOKE_PT_injected_fault)


def s_shot(name, caption):
    screenshot(name, caption)


def s_close_popup():
    press("ESC")
    return CLOSE_DELAY


def s_save_scratch_prefs():
    # Blender shows the Quick Setup instead of WM_MT_splash until a userpref.blend exists.
    if not REPORT["isolated_profile"]:
        _note("profile not isolated: not saving preferences, the launcher shot shows Quick Setup")
        return
    with bpy.context.temp_override(**override()):
        bpy.ops.wm.save_userpref()


def s_popup(operator_path, shot_name, caption):
    """Invoke a popup operator (wm.splash, gamestudio.launcher) and capture it."""
    module, name = operator_path.split(".")
    with bpy.context.temp_override(**override()):
        result = getattr(getattr(bpy.ops, module), name)("INVOKE_DEFAULT")
    if "CANCELLED" in result:
        _error("check", f"{operator_path} returned {result}")
    screenshot(shot_name, caption)


def s_add_workspaces():
    """GameStudio > Add GameStudio Workspaces."""
    with bpy.context.temp_override(**override()):
        result = bpy.ops.gamestudio.add_workspaces()
    if result != {"FINISHED"}:
        _error("check", f"gamestudio.add_workspaces returned {result}")
    return SETTLE_DELAY


def s_workspace(name):
    ws = bpy.data.workspaces.get(name)
    if ws is None:
        _error("check", f"workspace {name!r} is missing")
        return STEP_DELAY
    window().workspace = ws  # applied by the event loop, hence the settle delay
    return SETTLE_DELAY


def s_workspace_shot(name):
    if name not in bpy.data.workspaces:
        return
    # GameStudio's workspaces pick their sidebar tabs once the sidebar has drawn (workspaces.py).
    since = _state.setdefault(f"shown:{name}", time.time())
    if ext_module("workspaces").pending(bpy.data.workspaces[name]) and time.time() - since < READY_TIMEOUT:
        return Again(0.25)
    current = window().workspace.name
    if current != name:
        _error("check", f"switching to workspace {name!r} left {current!r} active")
    screenshot(f"10_ws_{slug(name)}", f"Workspace: {name}")
    REPORT["workspaces"]["visited"].append(name)
    tabs = sorted(region_of(a, "UI").active_panel_category for a in window().screen.areas if a.type == "VIEW_3D")
    REPORT["layouts"][f"{name} sidebar tabs"] = tabs
    if name in WORKSPACE_TABS and tabs != sorted(WORKSPACE_TABS[name]):
        _error("check", f"workspace {name!r} shows the sidebar tabs {tabs}, expected {WORKSPACE_TABS[name]}")


def s_menus():
    if not is_menu(TOP_BAR_MENUS):
        _error("check", f"{TOP_BAR_MENUS} is not registered")
        return
    top = [m for m in menu_children(TOP_BAR_MENUS)]
    REPORT["menus"]["top_bar"] = top
    if not top:
        _error("check", f"no menus found in {TOP_BAR_MENUS}.draw")
    win = window()
    move_mouse(16, win.height - 34)  # just under the top bar's left end: menus open downwards
    steps = []
    for i, name in enumerate(top):
        steps += [(f"menu:{name}", s_menu, name, f"20_menu_{i:02d}_{slug(name)}"),
                  (f"menu:{name}:close", s_close_popup)]
    steps += [("menus:deep_draw", s_menus_deep, top), ("menus:deep_close", s_close_popup)]
    plan_next(steps)


def s_menu(name, shot_name):
    """Open a top bar menu as a real popup (its draw runs now) and capture it."""
    if not is_menu(name):
        _error("check", f"top bar menu {name} is not registered")
        return
    with bpy.context.temp_override(**topbar_override()):
        result = bpy.ops.wm.call_menu(name=name)
    REPORT["menus"]["opened"].append({"menu": name, "result": sorted(result)})
    screenshot(shot_name, f"Top bar menu: {name}")


def s_menus_deep(top):
    """Draw every top-bar menu, its submenus and every GameStudio menu with a real layout."""
    names = menu_tree(list(top) + sorted(n for n in dir(bpy.types) if n.startswith("GAMESTUDIO_MT_")))
    REPORT["menus"]["deep_drawn"] = names

    def draw(popup, _context):
        layout = popup.layout
        for name in names:
            layout.label(text=name)
            layout.menu_contents(name)

    with bpy.context.temp_override(**topbar_override()):
        bpy.context.window_manager.popup_menu(draw, title="UI smoke: menu contents")


def s_providers_plan():
    try:
        import _gamestudio
    except ImportError:
        _gamestudio = None
    win = window()
    steps = []
    if _gamestudio is not None:
        REPORT["providers"]["mode"] = "extension_editor"
        names = [str(n) for n in _gamestudio.editors()]
        if not names:
            _error("check", "_gamestudio.editors() lists no providers")
        area = area_of(win, "VIEW_3D") or area_of(win)
        _state["provider_area"] = area
        _state["provider_restore"] = area.ui_type
        for name in names:
            steps += [(f"provider:{name}:open", s_provider_open, name),
                      (f"provider:{name}:shot", s_provider_shot, name)]
        steps.append(("provider:restore", s_provider_restore))
    else:
        REPORT["providers"]["mode"] = "sidebar"
        area = area_of(win, "VIEW_3D")
        if area is None:
            _error("check", "no 3D View in the first workspace for the GameStudio sidebar")
            return
        area.spaces.active.show_region_ui = True
        _state["sidebar_area"] = area
        # The Game tab shows only with this preference (off by default; the Inspector is in Object properties).
        bpy.context.preferences.addons[EXTENSION].preferences.sidebar_tab = True
        categories = set()
        for cls in bpy.types.Panel.__subclasses__():
            if (getattr(cls, "is_registered", False) and cls.__module__.startswith(EXTENSION)
                    and getattr(cls, "bl_space_type", "") == "VIEW_3D"
                    and getattr(cls, "bl_region_type", "") == "UI"):
                categories.add(getattr(cls, "bl_category", ""))
        ordered = sorted(categories - {""}, key=lambda c: (c != "Game", c))
        if not ordered:
            _error("check", "the GameStudio extension registered no 3D View sidebar panels")
        for category in ordered:
            steps.append((f"sidebar:{category}", s_sidebar_tab, category))
    plan_next(steps)
    return SETTLE_DELAY


def s_provider_open(name):
    _state["provider_area"].ui_type = name
    return SETTLE_DELAY


def s_provider_shot(name):
    area = _state["provider_area"]
    shown = getattr(area.spaces.active, "provider_idname", None)
    if area.ui_type != name and shown != name:
        _error("check", f"area.ui_type = {name!r} did not open it (ui_type {area.ui_type!r}, provider {shown!r})")
    screenshot(f"30_editor_{slug(name)}", f"Extension editor: {name}")
    REPORT["providers"]["visited"].append(name)


def s_provider_restore():
    _state["provider_area"].ui_type = _state["provider_restore"]
    return SETTLE_DELAY


def s_sidebar_tab(category):
    area = _state["sidebar_area"]
    region = region_of(area, "UI")
    try:
        region.active_panel_category = category
    except (AttributeError, TypeError, ValueError) as exc:
        _note(f"cannot select sidebar tab {category!r} ({exc}); the shot shows the default tab")
    else:
        if region.active_panel_category != category:
            _error("check", f"sidebar tab {category!r} not selected ({region.active_panel_category!r})")
    REPORT["providers"]["visited"].append(category)
    area.tag_redraw()
    screenshot(f"30_sidebar_{slug(category)}", f"3D View sidebar tab: {category}")


def ext_module(name):
    return importlib.import_module(f"{EXTENSION}.{name}")


def s_checks():
    """What the shell promises beyond drawing: play shortcuts, the status bar, the game layouts."""
    keymap = ext_module("keymap")
    wm = bpy.context.window_manager
    with bpy.context.temp_override(window=window()):
        for idname, _key, _shift in keymap.PLAY_KEYS:
            # The lookup menus and tooltips use to show a shortcut.
            found = wm.keyconfigs.find_item_from_operator(idname=idname)
            item = found[1] if found else None
            shown = (f"Shift {item.type}" if item.shift else item.type) if item is not None else None
            REPORT["shortcuts"][idname] = shown
            if shown != keymap.shortcut_text(idname, platform="blender"):
                _error("check", f"{idname}: menus and tooltips show shortcut {shown!r}, "
                                f"expected {keymap.shortcut_text(idname, platform='blender')!r}")
    for name in ("STATUSBAR_HT_header", "TOPBAR_MT_editor_menus", "TOPBAR_HT_upper_bar"):
        funcs = getattr(getattr(bpy.types, name).draw, "_draw_funcs", [])
        if not any(getattr(f, "__module__", "").startswith(EXTENSION) for f in funcs):
            _error("check", f"GameStudio added nothing to {name}")

    workspaces = ext_module("workspaces")
    for ws in bpy.data.workspaces:
        REPORT["layouts"][ws.name] = [area.type for area in ws.screens[0].areas]
        if ws.name in REPORT["workspaces"]["visited"] and workspaces.pending(ws):
            _error("check", f"workspace {ws.name!r} was shown but not set up: "
                            f"{ws[workspaces.LAYOUT_PROP].to_dict()}")
        if any(area.type == "EXTENSION_EDITOR" for area in ws.screens[0].areas):
            _error("check", f"workspace {ws.name!r} has an editor stock Blender does not know")


def s_prefs_popup():
    """The add-on preferences' Play Shortcuts box (editable key items) drawn with a real layout."""
    keymap = ext_module("keymap")

    def draw(popup, context):
        keymap.draw_preferences(popup.layout, context)

    with bpy.context.temp_override(**override()):
        bpy.context.window_manager.popover(draw, ui_units_x=26)  # a panel layout, as in Preferences
    screenshot("21_play_shortcuts", "Add-on preferences: the play shortcuts")


def s_scenario():
    """A small project with a component registry, an entity with components and some game logs, so
    the Inspector, the Console and the status bar show real content. It has no session.json, so
    nothing connects to a running dev server."""
    root = pathlib.Path(os.environ.get("BLENDER_USER_RESOURCES") or OUT) / "gs_smoke_project"
    (root / ".gamestudio").mkdir(parents=True, exist_ok=True)
    (root / "gamestudio.json").write_text(json.dumps({"name": "smoke-test", "startScene": "main"}),
                                          encoding="utf-8")
    shutil.copy2(REGISTRY_FIXTURE, root / ".gamestudio" / "components.json")
    ext_module("core.project").set_project_root_override(root)
    if not ext_module("core.registry").reload_registry(root):
        _error("check", "the scenario's component registry did not load")
    components = ext_module("core.components")

    import bmesh
    mesh = bpy.data.meshes.new("Crate")
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1.0)
    bm.to_mesh(mesh)
    bm.free()
    crate = bpy.data.objects.new("Crate", mesh)
    bpy.context.scene.collection.objects.link(crate)
    crate.location = (0.0, 0.0, 0.5)
    for name, values in (("RigidBody", {"mass": 2.0}), ("Collider", None), ("Spin", None), ("Coin", None)):
        components.add_component(crate, name, values)
    components.set_enabled(crate, "Spin", False)
    for obj in bpy.context.scene.objects:
        obj.select_set(obj == crate)
    bpy.context.view_layer.objects.active = crate

    now = time.time()
    ext_module("core.link").get_link().logs.extend([
        {"t": "log", "level": "info", "msg": "Scene 'main' loaded: 12 entities", "time": now},
        {"t": "log", "level": "warn", "msg": "Collider on 'Crate' has no mesh; using a box", "time": now},
        {"t": "error", "level": "error", "msg": "TypeError: coin.value is undefined",
         "file": "src/systems/collect.ts", "line": 14, "col": 9, "time": now},
    ])
    return s_workspace("Level")


def s_scenario_shots():
    screenshot("40_level_entity", "Level with a selected entity: Inspector, Console and status bar")
    area = area_of(window(), "VIEW_3D")
    _state["scenario_sidebar"] = area.spaces.active.show_region_ui
    area.spaces.active.show_region_ui = True
    bpy.context.preferences.addons[EXTENSION].preferences.sidebar_tab = True  # the Game tab is opt-in
    return SETTLE_DELAY


def s_scenario_sidebar():
    area = area_of(window(), "VIEW_3D")
    try:
        region_of(area, "UI").active_panel_category = "Game"
    except (AttributeError, TypeError, ValueError) as exc:
        _note(f"cannot select the Game tab ({exc})")
    area.tag_redraw()
    screenshot("41_sidebar_game_entity", "3D View sidebar, Game tab, with the same entity")
    area.spaces.active.show_region_ui = _state.pop("scenario_sidebar")


def s_scenario_assistant():
    """An assistant conversation with proposals, drawn without the SDK or a network call."""
    bpy.context.preferences.addons[EXTENSION].preferences.assistant_enabled = True
    deps = ext_module("assistant.deps")
    _state["sdk_available"] = deps.sdk_available
    deps.sdk_available = lambda: True
    agent = ext_module("assistant.agent")
    proposals = ext_module("assistant.proposals")
    proposals.set_project_root(ext_module("core.project").project_root())
    agent.SESSION.transcript[:] = [
        {"role": "user", "text": "Add a coin above the crate and make it spin."},
        {"role": "tool", "text": "get_scene_overview: 3 objects, 1 with components"},
        {"role": "assistant", "text": "I staged a scene change: a coin 1.5 m above 'Crate' with the Coin and Spin "
                                      "components. Spin uses its default speed; preview it before applying."},
        {"role": "system", "text": "p1 staged for review"},
    ]
    proposals.STORE.add(proposals.Proposal(
        id="p1", kind="scene", title="Add a spinning coin above the crate", rationale="", source="assistant",
        risk="medium", risk_reasons=["creates an object"],
        operations=[{"op": "create_object", "primitive": "sphere", "name": "Coin", "location": [0, 0, 2]},
                    {"op": "add_component", "object": "Coin", "component": "Coin", "values": {"value": 5}},
                    {"op": "add_component", "object": "Coin", "component": "Spin"}]))
    proposals.STORE.add(proposals.Proposal(
        id="p2", kind="file", title="Pull coins toward the player", rationale="", source="mcp",
        file_mode="create", path="src/systems/coin_magnet.ts"))
    area = area_of(window(), "VIEW_3D")
    _state["assistant_area"] = (area, area.ui_type, area.spaces.active.show_region_ui)
    if REPORT["native"]:
        area.ui_type = "GAMESTUDIO_ASSISTANT"
    else:
        area.spaces.active.show_region_ui = True
    return SETTLE_DELAY


def s_scenario_assistant_shot():
    area, ui_type, sidebar = _state.pop("assistant_area")
    if not REPORT["native"]:
        try:
            region_of(area, "UI").active_panel_category = "Assistant"
        except (AttributeError, TypeError, ValueError) as exc:
            _note(f"cannot select the Assistant tab ({exc})")
        area.tag_redraw()
    screenshot("42_assistant_chat", "Assistant: a conversation and two proposals to review")
    area.ui_type = ui_type
    area.spaces.active.show_region_ui = sidebar
    ext_module("assistant.deps").sdk_available = _state.pop("sdk_available")
    # The launcher again, now with the scenario's project in Recent Projects (scratch profile).
    ext_module("projects").remember(ext_module("core.project").project_root())
    return SETTLE_DELAY


def s_finish():
    REPORT["window_titles"]["end"] = window_titles()
    REPORT["finished"] = True
    REPORT["duration"] = round(time.time() - REPORT["started"], 2)
    _write_report()
    _quit()


def _quit():
    _write_report()
    _log("quitting")
    bpy.context.preferences.view.use_save_prompt = False
    with bpy.context.temp_override(window=window()):
        bpy.ops.wm.quit_blender()


# --- Plan -----------------------------------------------------------------------------


def build_plan():
    plan("ready", s_ready)
    plan("environment", s_environment)
    plan("startup:shot", s_shot, "00_startup", "Startup (fresh profile: Blender's own first-run splash)")
    plan("startup:close", s_close_popup)
    plan("prefs:save_scratch", s_save_scratch_prefs)
    plan("launcher", s_popup, "gamestudio.launcher", "01_launcher", "GameStudio launcher (GameStudio > Launcher)")
    plan("launcher:close", s_close_popup)
    plan("workspaces:add", s_add_workspaces)
    for name in EXPECTED_WORKSPACES:
        plan(f"workspace:{name}", s_workspace, name)
        plan(f"workspace:{name}:shot", s_workspace_shot, name)
    plan("menus:workspace", s_workspace, "Level")  # menus and sidebar tabs on Level
    plan("checks", s_checks)
    plan("menus", s_menus)
    plan("prefs", s_prefs_popup)
    plan("prefs:close", s_close_popup)
    plan("providers", s_providers_plan)
    plan("scenario", s_scenario)
    plan("scenario:shots", s_scenario_shots)
    plan("scenario:sidebar", s_scenario_sidebar)
    plan("scenario:assistant", s_scenario_assistant)
    plan("scenario:assistant:shot", s_scenario_assistant_shot)
    plan("scenario:launcher", s_popup, "gamestudio.launcher", "43_launcher_recent", "Launcher with a recent project")
    plan("scenario:launcher:close", s_close_popup)
    plan("finish", s_finish)


build_plan()
_write_report()
bpy.app.timers.register(_tick, first_interval=1.0)
if ARGS["t0"]:
    remaining = float(ARGS["t0"]) + float(ARGS["timeout"]) - 4.0 - time.time()
    bpy.app.timers.register(_abort, first_interval=max(5.0, remaining))
_log("driver registered; output in", OUT)
