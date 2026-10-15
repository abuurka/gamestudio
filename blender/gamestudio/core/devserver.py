# SPDX-License-Identifier: GPL-3.0-or-later
"""Dev server management: `<npm> run dev` in the project root (docs/SPEC.md §5.1).

The dev server (Vite + the GameStudio plugin) writes .gamestudio/session.json
once its hub listens. A server counts as running when that file names a live
pid and its editor port accepts connections. Blender only stops servers it
started itself, and kills the whole process tree (npm -> node -> esbuild).
Output goes to .gamestudio/devserver.log.

Waiting for the session file is asynchronous: start() returns as soon as npm
runs (Play exports the scene meanwhile), and pump(), polled by a timer in the
UI (tests call it directly), connects the live link when the server is ready.
"""

import os
import pathlib
import socket
import subprocess
import sys
import time
import urllib.parse
import webbrowser

import bpy
from bpy.app.handlers import persistent

from . import link, project, toolchain, trust
from .log import get_logger
from .util import tag_redraw

log = get_logger(__name__)

START_TIMEOUT = 90.0
# A server that dies this soon after starting is started once more before the failure is
# reported (see pump()).
RETRY_WINDOW = 20.0
POLL_INTERVAL = 0.1     # while a server starts: Play goes on this soon after it is ready
IDLE_INTERVAL = 1.0
LOG_NAME = "devserver.log"

_IS_WINDOWS = sys.platform == "win32"
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_NEW_GROUP = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)


class DevServerError(RuntimeError):
    pass


class _State:
    def __init__(self):
        self.proc = None            # Popen of the server we started
        self.root = None            # project root of that server
        self.log_file = None
        self.started_at = 0.0       # wall-clock time of the start (compared with file mtimes)
        self.waiting = None         # {"deadline", "callbacks"} while waiting for session.json
        self.last_error = ""
        self.retried = False        # this start already retried once


_s = _State()


# --- Process helpers -----------------------------------------------------------


def pid_alive(pid) -> bool:
    """True if a process with this pid exists. Never sends a signal on Windows."""
    if not isinstance(pid, int) or pid <= 0:
        return False
    if _IS_WINDOWS:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        kernel32.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
        kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return ctypes.get_last_error() == 5  # access denied: exists but not ours
        try:
            code = wintypes.DWORD()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return False
            return code.value == 259  # STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def port_open(port: int, host: str = "127.0.0.1", timeout: float = 0.3) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def kill_tree(pid: int) -> None:
    """Kill a process and its children (npm -> node -> esbuild)."""
    if _IS_WINDOWS:
        subprocess.run(
            [toolchain.system_tool("taskkill.exe"), "/T", "/F", "/PID", str(pid)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=_NO_WINDOW, timeout=15, check=False)
    else:
        import signal

        try:
            os.killpg(os.getpgid(pid), signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass


# --- Status --------------------------------------------------------------------


def running_session(root=None, check_port: bool = True) -> dict | None:
    """The session of a running dev server for the project, or None."""
    root = root if root is not None else project.project_root()
    if root is None:
        return None
    session = project.read_session(root)
    if session is None or not pid_alive(session.get("pid")):
        return None
    if check_port and not port_open(session["editorPort"]):
        return None
    return session


def is_running(root=None) -> bool:
    return running_session(root) is not None


def is_starting() -> bool:
    return _s.waiting is not None


def started_by_us() -> bool:
    return _s.proc is not None and _s.proc.poll() is None


def last_error() -> str:
    return _s.last_error


def log_path(root) -> str:
    return str(project.gs_dir(root) / LOG_NAME)


def log_tail(root, lines: int = 15) -> str:
    try:
        with open(log_path(root), "r", encoding="utf-8", errors="replace") as f:
            return "".join(f.readlines()[-lines:]).strip()
    except OSError:
        return ""


# --- Start / stop --------------------------------------------------------------


def start(root=None, on_ready=None, _retry=False) -> str:
    """Make sure a dev server runs for the project.

    Returns "running" if one already runs (the link is told to connect now),
    "starting" if one is booting, or "started" after launching `<npm> run dev`.
    on_ready(ok: bool, message: str) is called from pump() when the server is
    ready or failed. Raises DevServerError with a user-facing message.
    """
    from ..prefs import get_prefs

    root = root if root is not None else project.project_root()
    if root is None:
        raise DevServerError("Save the .blend inside a GameStudio project (folder with gamestudio.json)")
    root = pathlib.Path(root)
    if not trust.is_trusted(root):  # `npm run dev` runs the project's vite.config.ts and its plugins
        from .. import environment

        raise DevServerError(environment.untrusted_message())

    if running_session(root) is not None:
        link.get_link().reconnect_now()
        if on_ready is not None:
            on_ready(True, "Dev server already running")
        return "running"
    if _s.waiting is not None and _s.proc is not None and _s.proc.poll() is None:
        if on_ready is not None:
            _s.waiting["callbacks"].append(on_ready)
        return "starting"

    command = (get_prefs().npm_command or "npm").strip()
    npm = toolchain.npm_argv(command)  # the bundled npm first, then the system's
    if npm is None:
        if command == "npm":
            raise DevServerError("Node.js is not set up yet: GameStudio sets it up when you press Play "
                                 "(or GameStudio > Set Up GameStudio...)")
        raise DevServerError(f"Could not find '{command}', the npm command in GameStudio's preferences")
    if not (root / "package.json").is_file():
        raise DevServerError(f"No package.json in {root}; this does not look like a GameStudio game project")
    if toolchain.missing_dependencies(root):
        # Links to the packages an installed GameStudio ships (offline, quick): repairs a project
        # whose links broke, e.g. after GameStudio moved, before npm fails on them.
        toolchain.link_project(root, toolchain.sdk(__file__))
    if not (root / "node_modules").is_dir():
        raise DevServerError(f"The packages of {root.name} are not set up yet: GameStudio sets them up when you "
                             "press Play (or GameStudio > Set Up GameStudio...)")

    gs = project.gs_dir(root)
    gs.mkdir(parents=True, exist_ok=True)
    stale = project.session_path(root)
    if stale.exists():
        try:
            stale.unlink()  # left behind by a server that died; it is regenerated on start
        except OSError:
            pass

    log_file = open(log_path(root), "w", encoding="utf-8", errors="replace")
    log_file.write(f"# {' '.join(npm)} run dev  (cwd {root})\n")
    log_file.flush()
    env = toolchain.child_env(dict(os.environ, FORCE_COLOR="0", NO_COLOR="1", GAMESTUDIO_EDITOR="blender"))
    kwargs = {}
    if _IS_WINDOWS:
        kwargs["creationflags"] = _NO_WINDOW | _NEW_GROUP
    else:
        kwargs["start_new_session"] = True
    try:
        proc = subprocess.Popen(
            [*npm, "run", "dev"], cwd=str(root), env=env,
            stdin=subprocess.DEVNULL, stdout=log_file, stderr=subprocess.STDOUT, **kwargs)
    except OSError as exc:
        log_file.close()
        raise DevServerError(f"Could not start '{command} run dev': {exc}") from exc

    _s.proc = proc
    _s.root = root
    _s.log_file = log_file
    _s.started_at = time.time()
    _s.last_error = ""
    _s.retried = _retry
    _s.waiting = {"deadline": time.monotonic() + START_TIMEOUT, "callbacks": [on_ready] if on_ready else []}
    log.info("Started dev server (pid %d) in %s; output in %s", proc.pid, root, log_path(root))
    tag_redraw()
    return "started"


def _finish_wait(ok: bool, message: str) -> None:
    waiting, _s.waiting = _s.waiting, None
    if not ok:
        _s.last_error = message
        log.error(message)
    for callback in (waiting or {}).get("callbacks", []):
        try:
            callback(ok, message)
        except Exception:
            log.exception("Dev server callback failed")
    tag_redraw()


def _close_log() -> None:
    if _s.log_file is not None:
        try:
            _s.log_file.close()
        except OSError:
            pass
        _s.log_file = None


def stop() -> bool:
    """Stop the server Blender started. Returns False if it did not start one."""
    proc = _s.proc
    if proc is None:
        return False
    if _s.waiting is not None:
        _finish_wait(False, "Dev server start was cancelled")
    if proc.poll() is None:
        log.info("Stopping dev server (pid %d)", proc.pid)
        kill_tree(proc.pid)
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
    root = _s.root
    _s.proc = None
    _close_log()
    link.get_link().disconnect("Dev server stopped")
    if root is not None:
        session = project.read_session(root)
        if session is not None and not pid_alive(session.get("pid")):
            try:
                project.session_path(root).unlink()
            except OSError:
                pass
    tag_redraw()
    return True


def pump() -> None:
    """Watch a starting or running server. Main thread only."""
    proc = _s.proc
    if proc is not None and proc.poll() is not None:
        code = proc.returncode
        root = _s.root
        _s.proc = None
        _close_log()
        tail = log_tail(root) if root is not None else ""
        message = f"The dev server exited (code {code})."
        if tail:
            message += f" Last output:\n{tail}"
        if _s.waiting is not None and root is not None and not _s.retried \
                and time.time() - _s.started_at < RETRY_WINDOW:
            # Right after GameStudio is installed, antivirus scans can briefly lock the new
            # files, and Node reports a locked file as missing. One retry rides that out; a
            # second failure is real and is reported.
            log.warning("%s Starting it once more.", message)
            callbacks, _s.waiting = _s.waiting["callbacks"], None
            try:
                start(root, _retry=True)
            except DevServerError as exc:
                _s.waiting = {"deadline": 0.0, "callbacks": callbacks}
                _finish_wait(False, str(exc))
                return
            if _s.waiting is not None:
                _s.waiting["callbacks"].extend(callbacks)
            return
        if _s.waiting is not None:
            _finish_wait(False, message)
        else:
            _s.last_error = message
            log.warning(message)
        return
    if _s.waiting is None:
        return
    session = project.read_session(_s.root)
    if session is not None and session["_mtime"] >= _s.started_at - 1.0 and pid_alive(session.get("pid")):
        link.get_link().reconnect_now()
        _finish_wait(True, f"Dev server ready at {session.get('url', '?')}")
        return
    if time.monotonic() > _s.waiting["deadline"]:
        _finish_wait(False, f"The dev server did not start within {int(START_TIMEOUT)} s. See {log_path(_s.root)}")


def _timer():
    try:
        pump()
    except Exception:
        log.exception("Dev server pump failed")
    return POLL_INTERVAL if _s.waiting is not None else IDLE_INTERVAL


@persistent
def _on_exit(*_args):
    if started_by_us():
        stop()


# --- Game window ---------------------------------------------------------------


def _app_browsers(env=None, platform: str | None = None) -> list:
    """Edge, Chrome and Chromium where they are usually installed, then on PATH: the same list, in
    the same order, as @gamestudio/devserver's src/browser.js systemBrowserCandidates (the stream
    host and the MCP server look there too)."""
    env = os.environ if env is None else env
    platform = platform or sys.platform
    candidates = []
    home = env.get("HOME") or env.get("USERPROFILE") or os.path.expanduser("~")
    if platform == "win32":
        for name in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA"):
            base = env.get(name)
            if base:
                candidates.append(os.path.join(base, "Microsoft", "Edge", "Application", "msedge.exe"))
                candidates.append(os.path.join(base, "Google", "Chrome", "Application", "chrome.exe"))
    elif platform == "darwin":
        for apps in ("/Applications", os.path.join(home, "Applications")):
            candidates += [
                os.path.join(apps, "Google Chrome.app", "Contents", "MacOS", "Google Chrome"),
                os.path.join(apps, "Microsoft Edge.app", "Contents", "MacOS", "Microsoft Edge"),
                os.path.join(apps, "Chromium.app", "Contents", "MacOS", "Chromium"),
            ]
    sep = ";" if platform == "win32" else ":"
    path_dirs = [d for d in (env.get("PATH") or env.get("Path") or "").split(sep) if d]
    if platform.startswith("linux"):
        path_dirs += ["/usr/bin", "/snap/bin"]  # Blender started from a desktop launcher may have a short PATH
    for name in ("microsoft-edge", "google-chrome", "google-chrome-stable", "chromium", "chromium-browser"):
        for folder in path_dirs:
            candidates.append(os.path.join(folder, f"{name}.exe" if platform == "win32" else name))
    return list(dict.fromkeys(candidates))


def find_app_browser() -> str | None:
    for path in _app_browsers():
        if os.path.isfile(path):
            return path
    return None


def local_url(url) -> bool:
    """An http(s) address on this computer, as the dev server's is. The address comes from the project's
    .gamestudio/session.json, which anything in the project can write: nothing else is opened from it
    (the default browser would open a file or another program's link too)."""
    try:
        parts = urllib.parse.urlsplit(str(url))
        parts.port  # noqa: B018 - raises ValueError for a port that is not a number
    except ValueError:
        return False
    return parts.scheme in ("http", "https") and parts.hostname in ("127.0.0.1", "localhost", "::1")


def open_game_window(url: str) -> str:
    """Open the game: a chromeless Edge/Chrome app window if allowed, else the default browser.

    Returns "app" or "browser", or "" for an address that is not the dev server's (local_url).
    """
    from ..prefs import get_prefs

    if not local_url(url):
        log.warning("Did not open %r: the game runs at an http address on this computer", url)
        return ""
    if get_prefs().app_window:
        browser = find_app_browser()
        if browser is not None:
            try:
                kwargs = {"creationflags": _NO_WINDOW} if _IS_WINDOWS else {"start_new_session": True}
                subprocess.Popen([browser, f"--app={url}", "--window-size=1280,760"],
                                 stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL, **kwargs)
                return "app"
            except OSError as exc:
                log.warning("Could not open %s (%s); using the default browser", browser, exc)
    webbrowser.open(url)
    return "browser"


def register():
    if _on_exit not in bpy.app.handlers.exit_pre:
        bpy.app.handlers.exit_pre.append(_on_exit)
    if not bpy.app.timers.is_registered(_timer):
        bpy.app.timers.register(_timer, first_interval=0.5, persistent=True)


def unregister():
    if bpy.app.timers.is_registered(_timer):
        bpy.app.timers.unregister(_timer)
    if _on_exit in bpy.app.handlers.exit_pre:
        bpy.app.handlers.exit_pre.remove(_on_exit)
    # Disabling the add-on should not leave an orphaned server behind.
    if started_by_us():
        stop()
