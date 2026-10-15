# SPDX-License-Identifier: GPL-3.0-or-later
"""Project discovery and project files (docs/SPEC.md §1 and §5.1).

The project root is the nearest ancestor directory of the .blend file that
contains gamestudio.json. Everything the add-on generates lives under
<root>/.gamestudio/.
"""

import json
import pathlib
import time

import bpy

from .log import get_logger

log = get_logger(__name__)

PROJECT_FILE = "gamestudio.json"
GS_DIRNAME = ".gamestudio"

DEFAULT_CONFIG = {
    "name": "",
    "formatVersion": 1,
    "startScene": "main",
    "link": {"editorPort": 7710, "gamePort": 7711},
}

# project_root() runs in UI draw code, so cache the lookup per .blend path for
# a short time instead of walking the file system on every redraw.
_CACHE_TTL = 2.0
_root_cache = {"blend": None, "root": None, "time": 0.0}

# Lets tests and headless tools point the add-on at a project without saving
# the .blend inside it. None means "derive from bpy.data.filepath".
_root_override = None


def find_project_root(blend_path: str | None = None) -> pathlib.Path | None:
    """Return the nearest ancestor of blend_path that contains gamestudio.json."""
    if blend_path is None:
        blend_path = bpy.data.filepath
    if not blend_path:
        return None
    path = pathlib.Path(blend_path)
    try:
        path = path.resolve()
    except OSError:
        pass
    start = path if path.is_dir() else path.parent
    for directory in (start, *start.parents):
        if (directory / PROJECT_FILE).is_file():
            return directory
    return None


def project_root() -> pathlib.Path | None:
    """Project root for the current .blend; None if unsaved or outside a project."""
    if _root_override is not None:
        return _root_override
    blend = bpy.data.filepath
    now = time.monotonic()
    if _root_cache["blend"] == blend and now - _root_cache["time"] < _CACHE_TTL:
        return _root_cache["root"]
    root = find_project_root(blend) if blend else None
    _root_cache.update(blend=blend, root=root, time=now)
    return root


def set_project_root_override(root) -> None:
    """Force project_root() to return root (tests, headless tools). None clears it."""
    global _root_override
    _root_override = pathlib.Path(root) if root is not None else None
    invalidate_cache()


def invalidate_cache() -> None:
    _root_cache.update(blend=None, root=None, time=0.0)


def gs_dir(root) -> pathlib.Path:
    return pathlib.Path(root) / GS_DIRNAME


def export_dir(root) -> pathlib.Path:
    return gs_dir(root) / "export"


def registry_path(root) -> pathlib.Path:
    return gs_dir(root) / "components.json"


def session_path(root) -> pathlib.Path:
    return gs_dir(root) / "session.json"


def _read_json(path: pathlib.Path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


_config_cache = {"path": None, "mtime": None, "config": None}


def read_config(root) -> dict:
    """gamestudio.json merged over the defaults. Never raises; logs bad files."""
    config = json.loads(json.dumps(DEFAULT_CONFIG))
    if root is None:
        return config
    path = pathlib.Path(root) / PROJECT_FILE
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return config
    if _config_cache["path"] == path and _config_cache["mtime"] == mtime:
        return json.loads(json.dumps(_config_cache["config"]))
    try:
        data = _read_json(path)
    except FileNotFoundError:
        return config
    except (OSError, ValueError) as exc:
        log.warning("Could not read %s: %s", path, exc)
        data = {}  # cache the defaults so a broken file is reported once, not on every redraw
    _config_cache.update(path=path, mtime=mtime, config=_merge_config(config, data, path))
    return json.loads(json.dumps(_config_cache["config"]))


def _merge_config(config: dict, data, path) -> dict:
    if not isinstance(data, dict):
        log.warning("%s must contain a JSON object", path)
        return config
    link = data.get("link")
    config.update({k: v for k, v in data.items() if k != "link"})
    if isinstance(link, dict):
        config["link"].update(link)
    return config


def read_session(root) -> dict | None:
    """The live dev-server session (§5.1), or None if absent or malformed.

    The returned dict always has an int "editorPort" and a str "token"; it also
    carries "_mtime", the file's modification time.
    """
    if root is None:
        return None
    path = session_path(root)
    try:
        mtime = path.stat().st_mtime
        data = _read_json(path)
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        # The dev server may be mid-write; the caller simply retries later.
        log.debug("Could not read %s: %s", path, exc)
        return None
    if not isinstance(data, dict):
        return None
    port = data.get("editorPort")
    token = data.get("token")
    if not isinstance(port, int) or not isinstance(token, str):
        log.debug("Ignoring malformed session file %s", path)
        return None
    data["_mtime"] = mtime
    return data
