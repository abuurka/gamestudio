# SPDX-License-Identifier: GPL-3.0-or-later
"""Source locations in game logs: the file and line a console entry points at.

Locations reach the link log (core/link.py) in these shapes (docs/SPEC.md §5.4):

  error  {file, line, col, stack}  from the engine; the dev server has mapped them
         to the TypeScript source ("src/systems/a.ts"), and absolute paths for
         files outside the project ("F:/.../engine/src/ecs/scheduler.ts")
  log    {source: "src/systems/a.ts:12:5"}
  build  {file, line, col, kind: "build"}  Vite/TypeScript transform errors, sent
         by the dev server itself, so they arrive even when no game runs

and inside text (stacks, messages, build output): served URLs
(http://localhost:5173/src/a.ts?t=17:3:9, /@fs/F:/x/a.ts:1:2), Windows and
POSIX paths (F:\\game\\src\\a.ts:3:9), tsc's src/a.ts(3,9) and Rollup's
src/a.ts (3:9). Lines and columns are 1-based. A URL the dev server could not
map still opens its file, at the served line.
"""

import pathlib
import re
import urllib.parse
from typing import NamedTuple

_EXT = r"(?:[cm]?[jt]sx?|json|vue|css|html|glsl|wgsl|vert|frag)"
_END = r"(?=$|[\s)\]}'\",;>])"
_POS = r"(?::(\d+)(?::(\d+))?|\((\d+),(\d+)\)| \((\d+):(\d+)\))"
_PATTERNS = (
    # Served URLs: the host may carry a port and the path a ?query.
    re.compile(r"((?:https?|file)://[^\s()'\"<>]+?)" + r":(\d+)(?::(\d+))?" + _END),
    # Windows paths may contain spaces, so they are anchored by the drive and the extension.
    re.compile(r"(?<![\w/])([A-Za-z]:[\\/][^\r\n:*?\"<>|()]*?\." + _EXT + r")" + _POS),
    # POSIX absolute and project-relative paths.
    re.compile(r"(?<![\w/\\:.@-])((?:\.{0,2}/)?(?:[\w@.$+-]+/)*[\w@.$+-]+\." + _EXT + r")(?:\?[^\s:()]*)?" + _POS),
    # A bare relative path with a folder ("Failed to reload /src/systems/a.ts."): no line.
    re.compile(r"(?<![\w/\\:.@-])(/?(?:[\w@.$+-]+/)+[\w@.$+-]+\." + _EXT + r")(?![\w/(:])"),
)
# Served modules that are not source files: pre-bundled dependencies and Vite's own.
_NOT_SOURCE = ("node_modules/.vite/", "@vite/", "/@id/", "__x00__", "\0")
_CACHE_KEY = "_gs_where"
MAX_FRAMES = 12


class Location(NamedTuple):
    file: str
    line: int | None = None
    col: int | None = None

    def text(self, basename: bool = True) -> str:
        where = pathlib.PurePath(self.file).name if basename else self.file
        if self.line:
            where += f":{self.line}"
            if self.col:
                where += f":{self.col}"
        return where


class Resolved(NamedTuple):
    """A location whose file exists: what the console shows and opens."""

    path: pathlib.Path
    line: int | None
    col: int | None
    text: str           # "coins.ts:12:5"
    in_project: bool


def _int(value) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def normalize_file(file: str) -> str:
    """A path from a location: no URL scheme, host, query or /@fs/ prefix; percent-decoded."""
    text = str(file).strip()
    if text.startswith("file://"):
        text = urllib.parse.unquote(text[7:])
        if re.match(r"^/[A-Za-z]:[\\/]", text):
            text = text[1:]
        return text
    if "://" in text:
        text = "/" + text.split("://", 1)[1].partition("/")[2]
        text = urllib.parse.unquote(text.split("?", 1)[0].split("#", 1)[0])
    else:
        text = text.split("?", 1)[0]
    if text.startswith("/@fs/"):
        text = text[5:]
    if re.match(r"^/[A-Za-z]:[\\/]", text):
        text = text[1:]
    return text


def parse_location(text: str) -> Location | None:
    """One location string: "src/a.ts:12:5", "src/a.ts:12", a served URL, "src/a.ts(12,5)" or a bare path."""
    if not text:
        return None
    found = parse_text(str(text))
    if found:
        return found[0]
    match = re.match(r"^(.*?)(?::(\d+))?(?::(\d+))?$", str(text).strip())
    if not match or not match.group(1):
        return None
    return Location(normalize_file(match.group(1)), _int(match.group(2)), _int(match.group(3)))


def parse_text(text: str) -> list:
    """Every location in a text (a stack trace, an error message, build output), in order."""
    if not text:
        return []
    taken, found = [], []
    for pattern in _PATTERNS:
        for match in pattern.finditer(text):
            start, end = match.span()
            if any(start < b and a < end for a, b in taken):
                continue
            groups = [g for g in match.groups()[1:] if g is not None]
            line, col = (_int(groups[0]) if groups else None), (_int(groups[1]) if len(groups) > 1 else None)
            taken.append((start, end))
            found.append((start, Location(normalize_file(match.group(1)), line, col)))
    found.sort(key=lambda item: item[0])
    return [location for _start, location in found]


def entry_locations(entry: dict) -> list:
    """The locations of a link log entry, most useful first: its own location, then stack frames, then the message."""
    out = []
    file = entry.get("file")
    if isinstance(file, str) and file:
        out.append(Location(normalize_file(file), _int(entry.get("line")), _int(entry.get("col"))))
    source = entry.get("source")
    if isinstance(source, str) and source:
        location = parse_location(source)
        if location is not None:
            out.append(location)
    for key in ("stack", "msg"):
        value = entry.get(key)
        if isinstance(value, str):
            out += parse_text(value)
    unique = []
    for location in out:
        if location not in unique:
            unique.append(location)
    return unique


def resolve(location: Location, root) -> pathlib.Path | None:
    """The existing file a location names (project-relative names resolve against root), or None."""
    text = location.file.replace("\\", "/")
    if not text or any(marker in text for marker in _NOT_SOURCE):
        return None
    candidates = []
    absolute = pathlib.Path(location.file)
    # Vite reports project files as "/src/...", which is absolute on POSIX: try the project first.
    if root is not None and (text.startswith("/") or not absolute.is_absolute()):
        candidates.append(pathlib.Path(root) / text.lstrip("/"))
    if absolute.is_absolute():
        candidates.append(absolute)
    for path in candidates:
        try:
            if path.is_file():
                return path
        except OSError:
            continue
    return None


def _inside(path: pathlib.Path, root) -> bool:
    if root is None:
        return False
    try:
        path.resolve().relative_to(pathlib.Path(root).resolve())
    except (OSError, ValueError):
        return False
    return "node_modules" not in path.parts


def resolve_entry(entry: dict, root) -> list:
    """[Resolved] for an entry's locations whose files exist, project files first; at most MAX_FRAMES.

    Draw code calls this on every redraw, so the result is cached on the entry
    (per project root); file checks happen once per entry.
    """
    key = str(root)
    cached = entry.get(_CACHE_KEY)
    if cached is not None and cached[0] == key:
        return cached[1]
    resolved, seen = [], set()
    for location in entry_locations(entry):
        path = resolve(location, root)
        if path is None:
            continue
        ident = (str(path).lower(), location.line, location.col)
        if ident in seen:
            continue
        seen.add(ident)
        resolved.append(Resolved(path, location.line, location.col, location.text(), _inside(path, root)))
        if len(resolved) >= MAX_FRAMES:
            break
    primary, rest = resolved[:1], resolved[1:]
    result = primary + sorted(rest, key=lambda r: not r.in_project)
    entry[_CACHE_KEY] = (key, result)
    return result


def is_error(entry: dict) -> bool:
    return str(entry.get("level", "")).lower() == "error" or entry.get("t") == "error"


def latest_error(entries, since: float | None = None, with_location_in=None):
    """The newest error entry (newer than `since`, a link log time), or None.

    With with_location_in=root, the newest error that has a location in an existing file.
    """
    for entry in reversed(list(entries)):
        if not is_error(entry) or (since is not None and entry.get("time", 0.0) <= since):
            continue
        if with_location_in is not None and not resolve_entry(entry, with_location_in):
            continue
        return entry
    return None
