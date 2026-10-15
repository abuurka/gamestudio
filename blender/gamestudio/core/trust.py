# SPDX-License-Identifier: GPL-3.0-or-later
"""Which projects GameStudio may run code from.

A project's npm scripts (`npm run registry`, `npm run dev`, `npm run build`, `npm install` and its
packages' install scripts), its tools/*.py and the game's dev server (vite.config.ts) are the project's
own code, as a .blend's scripts are. Blender asks before it runs those (Auto Run Python Scripts);
GameStudio runs these only in a trusted project. Opening an untrusted one still opens its level and
links its packages, which runs nothing.

A project is trusted when
  - GameStudio made it: New Project from a built-in template or a trusted one (Save as Template from a
    trusted project), or its copy of an example (projects.py marks those);
  - the person chose Trust when GameStudio asked, or GameStudio > Trust This Project (environment.py);
  - Blender's Auto Run Python Scripts is on and the project is not in one of its excluded paths
    (Preferences > Save & Load), the way Blender decides for a .blend's scripts (blender_allows);
  - it was in the recent projects when this version first ran (migrate), so nobody is asked about the
    projects they already work on.

Trusting a folder trusts the projects inside it. The record is gamestudio_trusted.json in Blender's
config folder, next to the recent projects: {"migrated": true, "projects": [{"path", "how", "time"}]},
the paths resolved (and case-folded on Windows).
"""

import fnmatch
import json
import os
import pathlib
import sys
import threading
import time

from .log import get_logger

log = get_logger(__name__)

RECORD_FILE = "gamestudio_trusted.json"
# What GameStudio says when it does not run a project's code. ASK when the person can trust it there and
# then; ASK_USER when nobody is at the dialog (Blender without a window, an agent's call), for the agent to
# pass on.
ASK = "Trust this project first: GameStudio > Trust This Project"
ASK_USER = ("This project is not trusted yet, so GameStudio does not run its scripts or packages. Ask the user "
            "to choose GameStudio > Trust This Project in Blender")

_record = {"path": None, "mtime": None, "data": None}
_session: set = set()  # trusted this session (save=False, or the record could not be written)
_blender = None   # (auto run on, [(excluded path, glob)]) as the main thread last read it (other threads use it)
_keys: dict = {}  # str(root) -> key(root): draw code asks


def record_path() -> pathlib.Path:
    """<Blender's config folder>/gamestudio_trusted.json (read once: other threads ask too)."""
    if _record["path"] is None:
        from ..host import compat

        _record["path"] = pathlib.Path(compat.user_config_dir()) / RECORD_FILE
    return _record["path"]


def key(root) -> str:
    """The project's folder as the record keeps it: resolved, case-folded on Windows."""
    text = str(root)
    found = _keys.get(text)
    if found is None:
        try:
            resolved = pathlib.Path(text).resolve()
        except (OSError, RuntimeError):
            resolved = pathlib.Path(os.path.abspath(text))
        found = _keys[text] = os.path.normcase(str(resolved))
    return found


def _inside(path: str, folder: str) -> bool:
    return path == folder or path.startswith(folder.rstrip("\\/") + os.sep)


def _read() -> dict:
    path = record_path()
    try:
        mtime = path.stat().st_mtime
    except OSError:
        mtime = None
    if _record["data"] is None or mtime != _record["mtime"]:
        data = {}
        if mtime is not None:
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                log.warning("Could not read %s: %s", path, exc)
                data = {"migrated": True}  # it ran once: the recent list now has projects nobody trusted
        if not isinstance(data, dict) or not isinstance(data.get("projects"), list):
            data = {"migrated": bool(isinstance(data, dict) and data.get("migrated")), "projects": []}
        _record.update(mtime=mtime, data=data)
    return _record["data"]


def _write(data: dict) -> bool:
    path = record_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_name(path.name + ".tmp")
        temp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        os.replace(temp, path)
        written = True
    except OSError as exc:
        log.warning("Could not save the trusted projects in %s: %s", path, exc)
        written = False
    _record.update(mtime=None, data=None)  # read back, with the file's time
    return written


def trusted_paths() -> list[str]:
    paths = [str(e["path"]) for e in _read()["projects"] if isinstance(e, dict) and e.get("path")]
    return paths + sorted(_session)


def recorded(root) -> bool:
    """The record trusts the project (or a folder it is in)."""
    path = key(root)
    return any(_inside(path, folder) for folder in trusted_paths())


def is_trusted(root) -> bool:
    """GameStudio may run the project's code: recorded, or Blender would run the scripts of its .blend
    files. Any thread (Blender's preferences as the main thread last read them)."""
    if root is None:
        return False
    return recorded(root) or blender_allows(root)


def trust(root, how: str = "user", save: bool = True) -> None:
    """Trust the project from now on (how: "user", "created", "copied", "recent"); without `save`, for this
    session only (the test runners' temporary folders)."""
    data = _read()
    path = key(root)
    if any(_inside(path, folder) for folder in trusted_paths()):
        return
    _session.add(path)
    if save:
        _write({**data, "projects": [*data["projects"], {"path": path, "how": how, "time": time.time()}]})
    log.info("Trusted %s (%s)", root, how)


def migrate(roots) -> bool:
    """Once: trust the projects in the recent list (`roots`) when this version first runs, so the person
    is not asked about their own projects. Call it before any project loads (projects.register). True when
    it ran now."""
    data = _read()
    if data.get("migrated"):
        return False
    entries = list(data["projects"])
    known = {str(e.get("path")) for e in entries if isinstance(e, dict)}
    added = []
    for root in roots:
        path = key(root)
        if path not in known:
            entries.append({"path": path, "how": "recent", "time": time.time()})
            known.add(path)
            added.append(path)
    if not _write({**data, "migrated": True, "projects": entries}):
        _session.update(added)  # trusted this session at least; the next one migrates again
    return True


# --- Blender's Auto Run Python Scripts ---------------------------------------------------------------


def _command_line() -> bool | None:
    """-y / --enable-autoexec (True) or -Y / --disable-autoexec (False) on Blender's command line, the
    last one winning as in Blender; None without either. Arguments after "--" are a script's."""
    args = sys.argv[1:sys.argv.index("--")] if "--" in sys.argv else sys.argv[1:]
    found = None
    for arg in args:
        if arg in ("-y", "--enable-autoexec"):
            found = True
        elif arg in ("-Y", "--disable-autoexec"):
            found = False
    return found


def refresh() -> None:
    """Read Blender's Auto Run Python Scripts and its excluded paths (main thread only)."""
    global _blender
    try:
        import bpy

        prefs = bpy.context.preferences
        enabled = bool(prefs.filepaths.use_scripts_auto_execute)
        excluded = [(str(p.path), bool(p.use_glob)) for p in prefs.autoexec_paths if p.path]
    except (AttributeError, ImportError, RuntimeError):
        return
    _blender = (enabled, excluded)


def blender_allows(root) -> bool:
    """Blender would run the scripts of the project's .blend files: Auto Run Python Scripts is on (or
    -y on the command line, which skips the excluded paths, as in Blender), and no excluded path covers
    the project's folder, its scenes/ or a folder inside it. Blender's own check (BKE_autoexec_match):
    a glob matches the .blend's path with fnmatch (here also its folders), case-folded on Windows; any other path is a prefix of
    it, ignoring case."""
    if threading.current_thread() is threading.main_thread():
        refresh()
    command_line = _command_line()
    if command_line is not None:
        return command_line
    if _blender is None or not _blender[0]:
        return False
    folders = []
    for text in (str(root), key(root)):
        folder = text.rstrip("\\/") + os.sep
        folders += [folder, folder + "scenes" + os.sep]
    if sys.platform == "win32":
        folders = [f.replace("/", "\\") for f in folders]
    blends = None
    for path, glob in _blender[1]:
        pattern = path.replace("/", "\\") if sys.platform == "win32" else path
        if glob and blends is None:
            blends = _blend_files(root)  # Blender matches a glob against the .blend's own path
        for folder in folders + (blends if glob else []):
            if glob:
                hit = (fnmatch.fnmatchcase(folder.lower(), pattern.lower()) if sys.platform == "win32"
                       else fnmatch.fnmatchcase(folder, pattern))
            else:  # the excluded folder holds the project, or is inside it
                hit = folder.lower().startswith(pattern.lower()) or pattern.lower().startswith(folder.lower())
            if hit:
                return False
    return True


def _blend_files(root) -> list[str]:
    """The project's .blend files (in its folder, scenes/, ui/ and assets/), for the excluded-path globs."""
    base = pathlib.Path(root)
    found = []
    for folder in (base, base / "scenes", base / "ui", base / "assets"):
        try:
            found += [str(path) for path in folder.glob("*.blend")]
        except OSError:
            continue
    return [path.replace("/", "\\") for path in found] if sys.platform == "win32" else found
