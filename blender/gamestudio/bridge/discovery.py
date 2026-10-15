# SPDX-License-Identifier: GPL-3.0-or-later
"""Discovery files that tell the MCP server (runtime/packages/mcp) how to reach this session.

* `<project>/.gamestudio/blender.json`: written while a scene of that project is open.
* `<extension user folder>/bridge.json`: the latest session of this Blender profile, project or
  not (`bpy.utils.extension_path_user`).
* `<extension user folder>/last-blender.json`: the Blender executable and version, kept after
  exit so the MCP server can launch Blender again when none is running.

The session files hold the port and the random token, and are removed on exit. A session
claims the project file when it opens or saves a scene there; otherwise it only rewrites the
file when it is missing or its owner process is gone, so two Blenders on one project don't fight.
"""

import datetime
import json
import os
import pathlib
import sys
import time

import bpy

from ..core import project
from ..core.log import get_logger

log = get_logger(__name__)

FORMAT = "gamestudio.blender-bridge"
FORMAT_VERSION = 1
PROJECT_FILE = "blender.json"
USER_FILE = "bridge.json"
INSTALL_FILE = "last-blender.json"

# Tests (and hosts that load the add-on outside an extension repository) set this.
user_dir_override: pathlib.Path | None = None

_written = {"project_file": None, "user_file": None}


def _extension_package() -> str:
    return (__package__ or "").rpartition(".")[0]  # "bl_ext.<repo>.gamestudio" when installed


def user_dir(create: bool = True) -> pathlib.Path | None:
    """The extension's user folder, or None when the add-on is not loaded as an extension."""
    if user_dir_override is not None:
        if create:
            user_dir_override.mkdir(parents=True, exist_ok=True)
        return user_dir_override
    package = _extension_package()
    if not package.startswith("bl_ext."):
        return None
    try:
        return pathlib.Path(bpy.utils.extension_path_user(package, path="", create=create))
    except (ValueError, TypeError, AttributeError, OSError):
        return None


def project_file(root) -> pathlib.Path:
    return project.gs_dir(root) / PROJECT_FILE


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def session_info(server, root) -> dict:
    blend = bpy.data.filepath or None
    scene = getattr(bpy.context, "scene", None)
    return {
        "format": FORMAT,
        "formatVersion": FORMAT_VERSION,
        "pid": os.getpid(),
        "host": server.host,
        "port": server.port,
        "token": server.token,
        "blend": blend,
        "project": str(root) if root is not None else None,
        "scene": scene.name if scene is not None else None,
        "blenderVersion": bpy.app.version_string.split(" ")[0],
        "binary": bpy.app.binary_path or sys.executable,
        "background": bool(bpy.app.background),
        "startedAt": datetime.datetime.fromtimestamp(server.started_at, datetime.timezone.utc)
        .isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        "updatedAt": _now(),
    }


def _write_json(path: pathlib.Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    for attempt in range(10):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:  # a reader has it open on Windows
            if attempt == 9:
                raise
            time.sleep(0.02)


def read_json(path) -> dict | None:
    try:
        data = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _pid_alive(pid) -> bool:
    from ..core import devserver

    return devserver.pid_alive(pid)


def _remove_if_ours(path) -> None:
    if path is None:
        return
    path = pathlib.Path(path)
    data = read_json(path)
    if data is not None and data.get("pid") != os.getpid():
        return
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    except OSError as exc:
        log.warning("Could not remove %s: %s", path, exc)


def sync(server, claim: bool = False) -> None:
    """Write, refresh or remove the discovery files for the current file and project."""
    if claim:
        project.invalidate_cache()
    root = project.project_root()
    info = session_info(server, root)

    old = _written["project_file"]
    new = project_file(root) if root is not None else None
    if old is not None and (new is None or pathlib.Path(old) != new):
        _remove_if_ours(old)
        _written["project_file"] = None
    if new is not None:
        current = read_json(new)
        mine = current is not None and current.get("pid") == os.getpid()
        stale = current is None or not _pid_alive(current.get("pid"))
        if claim or stale or (mine and _differs(current, info)):
            try:
                _write_json(new, info)
                _written["project_file"] = str(new)
            except OSError as exc:
                log.warning("Could not write %s: %s", new, exc)
        elif mine:
            _written["project_file"] = str(new)

    folder = user_dir()
    if folder is not None:
        path = folder / USER_FILE
        current = read_json(path)
        mine = current is not None and current.get("pid") == os.getpid()
        stale = current is None or not _pid_alive(current.get("pid"))
        try:
            if claim or stale or (mine and _differs(current, info)):
                _write_json(path, info)
                _written["user_file"] = str(path)
            install = folder / INSTALL_FILE
            if claim or not install.exists():
                _write_json(install, {"binary": info["binary"], "blenderVersion": info["blenderVersion"],
                                      "updatedAt": info["updatedAt"]})
        except OSError as exc:
            log.warning("Could not write the bridge discovery file in %s: %s", folder, exc)


def _differs(current: dict, info: dict) -> bool:
    keys = ("port", "token", "blend", "project", "scene", "background")
    return any(current.get(k) != info.get(k) for k in keys)


def remove_all() -> None:
    """Remove the session files this process wrote (exit, unregister, bridge turned off)."""
    _remove_if_ours(_written["project_file"])
    _remove_if_ours(_written["user_file"])
    _written["project_file"] = None
    _written["user_file"] = None


def written() -> dict:
    return dict(_written)
