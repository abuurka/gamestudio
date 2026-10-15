# SPDX-License-Identifier: GPL-3.0-or-later
"""The project's scripts: its components and systems, from the files and the registry.

Files are src/components/*.ts and src/systems/*.ts, the folders src/main.ts
loads with import.meta.glob (and the registry generator reads). Each file is
read once per change for the names it defines (defineComponent('Name', ...),
defineSystem({ name: 'NameSystem' })). The component registry adds what the
dev server knows: which components are in Add Component.

Draw code never scans the disk. It reads listing(); a timer polls the two
folders (one os.scandir each, no file reads unless something changed) and
redraws the editors when they change; scaffolding calls invalidate().
"""

import os
import pathlib
import re

import bpy

from . import project, registry
from .log import get_logger
from .util import tag_redraw

log = get_logger(__name__)

FOLDERS = {"component": "src/components", "system": "src/systems"}
SOURCE_RE = re.compile(r"\.(m?[jt]s|tsx)$")   # as the registry generator (devserver/src/registry.js)
POLL_INTERVAL = 1.0
MAX_FILES = 200
_MAX_READ = 512 * 1024
_COMPONENT_RE = re.compile(r"defineComponent\s*\(\s*['\"`]([A-Za-z_$][\w$]*)['\"`]")
_SYSTEM_RE = re.compile(r"defineSystem\s*\(\s*\{.*?\bname\s*:\s*['\"`]([^'\"`]+)['\"`]", re.S)


def _source_files(folder: pathlib.Path) -> list:
    """[(name, mtime_ns, size)] of the folder's script files, sorted; [] when it is missing."""
    try:
        with os.scandir(folder) as it:
            entries = [(e.name, e.stat().st_mtime_ns, e.stat().st_size) for e in it
                       if e.is_file() and SOURCE_RE.search(e.name) and not e.name.endswith(".d.ts")]
    except OSError:
        return []
    return sorted(entries, key=lambda e: e[0].lower())[:MAX_FILES]


def defined_names(text: str, kind: str) -> list:
    """The component or system names a source file defines, in order."""
    pattern = _COMPONENT_RE if kind == "component" else _SYSTEM_RE
    names = []
    for name in pattern.findall(text):
        if name not in names:
            names.append(name)
    return names


def _read_names(path: pathlib.Path, kind: str) -> list:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return defined_names(f.read(_MAX_READ), kind)
    except OSError:
        return []


class _Cache:
    def __init__(self):
        self.root = None
        self.signature = None
        self.scripts = {"component": [], "system": []}
        self.names = {}          # (path, mtime_ns, size) -> names, so unchanged files are not re-read
        self.generation = 0


_cache = _Cache()


def _signature(root: pathlib.Path) -> tuple:
    return tuple((kind, tuple(_source_files(root / rel))) for kind, rel in FOLDERS.items())


def scan(root, signature=None) -> dict:
    """Read the script folders now: {"component": [script], "system": [script]}.

    A script is {"kind", "path", "rel" ("src/systems/a.ts"), "file" ("a.ts"), "names"}.
    """
    root = pathlib.Path(root)
    signature = signature if signature is not None else _signature(root)
    names_cache, scripts = {}, {}
    for kind, files in signature:
        rel_dir = FOLDERS[kind]
        scripts[kind] = []
        for name, mtime, size in files:
            path = root / rel_dir / name
            key = (str(path), mtime, size)
            names = _cache.names.get(key)
            if names is None:
                names = _read_names(path, kind)
            names_cache[key] = names
            scripts[kind].append({"kind": kind, "path": path, "rel": f"{rel_dir}/{name}", "file": name,
                                  "names": names})
    _cache.root, _cache.signature, _cache.scripts, _cache.names = root, signature, scripts, names_cache
    _cache.generation += 1
    return scripts


def invalidate() -> None:
    _cache.root = None
    _cache.signature = None


def listing(root) -> dict:
    """The cached scripts of root; scans only the first time a project is shown or after invalidate()."""
    root = pathlib.Path(root)
    if _cache.root != root or _cache.signature is None:
        scan(root)
    return _cache.scripts


def poll() -> bool:
    """Rescan when the files changed. Returns True when the listing changed."""
    root = project.project_root()
    if root is None or _cache.root is None:
        return False
    if root != _cache.root:
        scan(root)
        return True
    signature = _signature(root)
    if signature == _cache.signature:
        return False
    scan(root, signature)
    return True


def component_names(root) -> set:
    """Component names defined in root's component files (in the registry or not)."""
    return {name for script in listing(root)["component"] for name in script["names"]}


def system_names(root) -> set:
    return {name for script in listing(root)["system"] for name in script["names"]}


def components(root) -> list:
    """The component scripts, each with "entries": [(name, registry entry or None)].

    Names come from the file and from registry entries whose source is the file
    (a name built at runtime is invisible to the file scan but not to the registry).
    """
    by_source = {}
    for name in registry.component_names():
        entry = registry.schema_ref(name)
        if entry and entry.get("source"):
            by_source.setdefault(str(entry["source"]).replace("\\", "/").lower(), []).append(name)
    out = []
    for script in listing(root)["component"]:
        names = list(script["names"])
        for name in by_source.get(script["rel"].lower(), []):
            if name not in names:
                names.append(name)
        out.append(dict(script, entries=[(name, registry.schema_ref(name)) for name in names]))
    return out


def systems(root) -> list:
    return list(listing(root)["system"])


def _timer():
    try:
        if poll():
            tag_redraw()
    except Exception:
        log.exception("Watching the script folders failed")
    return POLL_INTERVAL


def register():
    if not bpy.app.timers.is_registered(_timer):
        bpy.app.timers.register(_timer, first_interval=POLL_INTERVAL, persistent=True)


def unregister():
    if bpy.app.timers.is_registered(_timer):
        bpy.app.timers.unregister(_timer)
    invalidate()
    _cache.names = {}
    _cache.scripts = {"component": [], "system": []}

