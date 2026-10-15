# SPDX-License-Identifier: GPL-3.0-or-later
"""host.game_view: the running game, docked in the editor where the build can do it.

GameStudio Editor builds with the "game_view" capability run the game in the
runtime host (Electron, `@gamestudio/host`) offscreen and draw its frames in
the native Game View editor, GAMESTUDIO_GAME_VIEW (docs/fork/ARCHITECTURE.md
section 4, docs/fork/SURFACE.md). Stock Blender has no docked view: start() raises
GameViewError and the caller opens the game in its own window instead
(core/devserver.open_game_window, today's behaviour).

    host.game_view.available()           # this build can dock the game
    host.game_view.areas()               # areas showing the Game View
    host.game_view.start(url, project_root=root)   # raises GameViewError
    host.game_view.status()              # dict, see _gamestudio.game_view_status
    host.game_view.stop() / detach() / set_paused(True) / open_devtools()
"""

import os
import pathlib
import sys

import bpy

from . import capabilities, native

IDNAME = "GAMESTUDIO_GAME_VIEW"
CAPABILITY = "game_view"
SPACE_TYPE = "EXTENSION_EDITOR"
HOST_PACKAGE = ("@gamestudio", "host")
LOG_NAME = "gameview-host.log"
# Override the runtime host lookup, e.g. for a packaged host executable.
ENV_ELECTRON = "GAMESTUDIO_ELECTRON"
ENV_HOST_DIR = "GAMESTUDIO_HOST_DIR"

# States of the native session (gs::gameview::State), plus "none" and "unavailable".
LIVE_STATES = ("created", "waiting", "connected")

_last_error = ""


class GameViewError(RuntimeError):
    """The docked Game View could not start; the message says why, for the user."""


def available() -> bool:
    """True on GameStudio Editor builds that can dock the game (capability "game_view")."""
    return CAPABILITY in capabilities()


def last_error() -> str:
    """Why the last start() failed, or ""."""
    return _last_error


# --- Areas ---------------------------------------------------------------------------


def shows_game_view(area) -> bool:
    if area is None or getattr(area, "type", "") != SPACE_TYPE:
        return False
    return getattr(area.spaces.active, "provider_idname", "") == IDNAME


def areas(context=None) -> list:
    """Areas showing the Game View in the visible screens of every window (empty on stock)."""
    if not available():
        return []
    context = context if context is not None else bpy.context
    wm = getattr(context, "window_manager", None)
    found = []
    for window in getattr(wm, "windows", ()):
        screen = window.screen
        for area in screen.areas if screen else ():
            if shows_game_view(area):
                found.append(area)
    return found


def visible(context=None) -> bool:
    return bool(areas(context))


def _main_region(area):
    for region in area.regions:
        if region.type == "WINDOW":
            return region
    return None


def open(area=None) -> bool:  # noqa: A001 - mirrors host.editors.open
    """Turn an area (default: the context area) into the Game View. False on stock Blender."""
    from . import editors

    return available() and editors.open(IDNAME, area)


# --- Locating the runtime host ---------------------------------------------------------


def _electron_executable(package: pathlib.Path) -> pathlib.Path:
    """The binary of an installed `electron` npm package, resolved the way require('electron') does."""
    try:
        relative = (package / "path.txt").read_text(encoding="utf-8").strip()
    except OSError:
        relative = ""
    if not relative:
        relative = "electron.exe" if sys.platform == "win32" else "electron"
    return package / "dist" / relative


def _search_roots(project_root) -> list[pathlib.Path]:
    """Node-style lookup: the project and its parents, then this source checkout and its parents."""
    roots = []
    starts = [pathlib.Path(project_root)] if project_root else []
    starts.append(pathlib.Path(__file__).resolve().parent)
    for start in starts:
        for directory in (start, *start.parents):
            if directory not in roots:
                roots.append(directory)
    return roots


def find_runtime_host(project_root=None) -> tuple[str, str]:
    """(electron executable, host app dir) for docked play. Raises GameViewError if missing.

    Same layout as `npm run gameview:probe`: electron from node_modules/electron
    (its path.txt names the binary under dist/), the host app from the
    `@gamestudio/host` package or a source checkout's runtime/packages/host.
    A packaged install's <install>/gamestudio (the bundled runtime) is searched last.
    GAMESTUDIO_ELECTRON and GAMESTUDIO_HOST_DIR override both.
    """
    from ..core import toolchain

    env_electron = os.environ.get(ENV_ELECTRON, "").strip()
    env_host_dir = os.environ.get(ENV_HOST_DIR, "").strip()
    electron = pathlib.Path(env_electron) if env_electron else None
    app_dir = pathlib.Path(env_host_dir) if env_host_dir else None
    not_downloaded = None  # (expected binary, directory to run the download in)

    for root in _search_roots(project_root) + toolchain.runtime_roots(__file__):
        if electron is not None and app_dir is not None:
            break
        modules = root / "node_modules"
        if app_dir is None:
            for candidate in (modules.joinpath(*HOST_PACKAGE), root / "runtime" / "packages" / "host"):
                if (candidate / "package.json").is_file():
                    app_dir = candidate.resolve()
                    break
        if electron is None and (modules / "electron" / "package.json").is_file():
            binary = _electron_executable(modules / "electron")
            if binary.is_file():
                electron = binary
            elif not_downloaded is None:
                not_downloaded = (binary, root)

    if app_dir is None:
        raise GameViewError("The GameStudio runtime host (@gamestudio/host) is not among the project's packages")
    if electron is None:
        if not_downloaded is not None:
            binary, root = not_downloaded
            raise GameViewError(f"Electron is installed but its binary is missing ({binary}). "
                                f"It downloads on first use: run  node -e \"require('electron')\"  in {root}")
        raise GameViewError("Electron is not among the project's packages")
    if not electron.is_file():
        raise GameViewError(f"{ENV_ELECTRON} points to a missing file: {electron}")
    return str(electron), str(app_dir)


# --- Session -------------------------------------------------------------------------


def _unavailable_status() -> dict:
    return {"active": False, "state": "unavailable", "message": "", "page_state": "", "mode": "",
            "paused": False, "user_paused": False, "devtools": False, "captured": False,
            "cursor_grabbed": False, "host_pid": 0, "overlay": "", "overlay_detail": "",
            "width": 0, "height": 0, "frame_id": 0, "frames": {}, "timings": {}, "views": []}


def status() -> dict:
    """The docked game's state (see _gamestudio.game_view_status); state "unavailable" on stock."""
    if not available():
        return _unavailable_status()
    return native().game_view_status()


def is_running() -> bool:
    """A docked (or detached) game is starting or running, and has not crashed."""
    current = status()
    return current["state"] in LIVE_STATES and current.get("page_state") not in ("crashed", "error")


def start(url: str, *, project_root=None, area=None, log_file=None, executable=None, app_dir=None) -> None:
    """Run the game at `url` in the Game View. Restarts a running one. Raises GameViewError.

    The native side sizes the game to the largest visible Game View; `area` only
    gives the initial size when none is visible. The host's output goes to
    <project>/.gamestudio/gameview-host.log.
    """
    global _last_error
    try:
        if not available():
            raise GameViewError("This build has no docked Game View")
        if executable is None or app_dir is None:
            found_executable, found_app_dir = find_runtime_host(project_root)
            executable = executable or found_executable
            app_dir = found_app_dir if app_dir is None else app_dir
        if log_file is None and project_root:
            log_dir = pathlib.Path(project_root) / ".gamestudio"
            log_dir.mkdir(parents=True, exist_ok=True)
            log_file = str(log_dir / LOG_NAME)
        width, height = 1280, 720
        targets = [area] if area is not None else areas()
        region = _main_region(targets[0]) if targets else None
        if region is not None and region.width > 1 and region.height > 1:
            width, height = region.width, region.height
        try:
            native().game_view_start(executable, url, app_dir=app_dir or "", width=width, height=height,
                                     log_file=log_file or "")
        except (RuntimeError, ValueError, TypeError) as exc:
            raise GameViewError(str(exc)) from exc
    except GameViewError as exc:
        _last_error = str(exc)
        raise
    except OSError as exc:
        _last_error = str(exc)
        raise GameViewError(_last_error) from exc
    _last_error = ""


def stop() -> bool:
    """Stop the docked game. False if there was nothing to stop (or no Game View)."""
    if not available():
        return False
    was_active = native().game_view_status()["active"]
    native().game_view_stop()
    return bool(was_active)


def detach() -> bool:
    """Move the docked game into its own window. False if no game is connected."""
    return available() and bool(native().game_view_detach())


def set_paused(paused: bool) -> bool:
    """Pause or resume the docked game (frames and animation). False if no game runs."""
    return available() and bool(native().game_view_set_paused(bool(paused)))


def open_devtools(open: bool = True) -> bool:  # noqa: A002 - mirrors the native call
    """Open (or focus) Chromium DevTools for the running game in their own window; False closes them.
    False if no game is connected."""
    return available() and bool(native().game_view_devtools(bool(open)))
