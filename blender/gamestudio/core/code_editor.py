# SPDX-License-Identifier: GPL-3.0-or-later
"""The external code editor: find VS Code or Cursor and open files at a line.

GameStudio has no code editor of its own (docs/fork/ARCHITECTURE.md §6.7). Files
open in the editor chosen in the add-on preferences:

  AUTO    VS Code if it is installed, else Cursor, else the system default
  VSCODE  VS Code;  CURSOR  Cursor (an error if it is not installed)
  SYSTEM  the application the OS associates with the file (no line numbers)
  CUSTOM  a command line with {file} {line} {column} {project} placeholders

VS Code and Cursor get the project folder plus the file, `<exe> <project> -g
<file>:<line>:<column>`, so the window has the project's tsconfig and
node_modules and IntelliSense works. Commands are argument lists, never shell
strings, so paths with spaces stay one argument. On Windows the editor's GUI
executable (Code.exe) is preferred over its `code.cmd` shim: no console window
flashes and cmd.exe never re-parses the paths.
"""

import os
import pathlib
import re
import subprocess
import sys
import time
from typing import NamedTuple

from . import toolchain
from .log import get_logger

log = get_logger(__name__)

PRESETS = ("AUTO", "VSCODE", "CURSOR", "SYSTEM", "CUSTOM")
EXAMPLE_COMMAND = '"C:\\Program Files\\Notepad++\\notepad++.exe" -n{line} -c{column} "{file}"'
_PLACEHOLDER = re.compile(r"\{(file|line|column|col|project)\}")
_DETECT_TTL = 60.0
_IS_WINDOWS = sys.platform == "win32"
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# Text files that belong in the code editor rather than their OS default app
# (on Windows .ts is "MPEG transport stream" unless an editor claimed it).
SOURCE_SUFFIXES = {".ts", ".tsx", ".mts", ".cts", ".js", ".jsx", ".mjs", ".cjs", ".json", ".md", ".html",
                   ".css", ".glsl", ".wgsl", ".vert", ".frag", ".txt", ".log", ".yml", ".yaml", ".toml"}


class CodeEditorError(RuntimeError):
    pass


class Editor(NamedTuple):
    kind: str            # "vscode" | "cursor"
    label: str           # "VS Code"
    executable: str      # Code.exe (or the code.cmd shim when no exe was found)


class Target(NamedTuple):
    """What a preset resolves to: an editor, a custom command, or the OS default (both None)."""

    label: str
    editor: Editor | None = None
    custom: list | None = None   # the custom command, split into arguments


# --- Detection --------------------------------------------------------------------------


_EDITORS = {
    # kind: (label, command names on PATH, GUI executable, folders that hold the install)
    "vscode": ("VS Code", ("code",), "Code.exe" if _IS_WINDOWS else "code",
               (("LOCALAPPDATA", "Programs", "Microsoft VS Code"), ("ProgramFiles", "Microsoft VS Code"),
                ("ProgramFiles(x86)", "Microsoft VS Code"))),
    "cursor": ("Cursor", ("cursor",), "Cursor.exe" if _IS_WINDOWS else "cursor",
               (("LOCALAPPDATA", "Programs", "cursor"), ("ProgramFiles", "cursor"))),
}
_MAC_APPS = {"vscode": "/Applications/Visual Studio Code.app/Contents/Resources/app/bin/code",
             "cursor": "/Applications/Cursor.app/Contents/Resources/app/bin/cursor"}


def _gui_executable(cli: pathlib.Path, exe_name: str) -> pathlib.Path | None:
    """The editor's GUI executable for a CLI shim (bin\\code.cmd -> Code.exe), searching up a few folders."""
    if cli.suffix.lower() == ".exe":
        return cli
    for folder in list(cli.parents)[:4]:
        candidate = folder / exe_name
        if candidate.is_file():
            return candidate
    return None


def _install_candidates(kind: str, env) -> list:
    _label, _names, exe_name, installs = _EDITORS[kind]
    stem = exe_name.rsplit(".", 1)[0].lower()
    out = []
    for env_name, *parts in installs:
        base = env.get(env_name)
        if not base:
            continue
        folder = pathlib.Path(base, *parts)
        out += [folder / exe_name, folder / "bin" / f"{stem}.cmd", folder / "resources" / "app" / "bin" / f"{stem}.cmd"]
    if sys.platform == "darwin":
        out.append(pathlib.Path(_MAC_APPS[kind]))
    return out


def find_editor(kind: str, env=None, which=toolchain.which) -> Editor | None:
    """Find VS Code ("vscode") or Cursor ("cursor"): its command on PATH first, then the usual install folders."""
    env = os.environ if env is None else env
    label, names, exe_name, _installs = _EDITORS[kind]
    candidates = []
    for name in names:
        found = which(name)
        if found:
            path = pathlib.Path(found)
            # Windows' bin folder also holds a bash script without an extension; use its .cmd sibling.
            if _IS_WINDOWS and not path.suffix and path.with_suffix(".cmd").is_file():
                path = path.with_suffix(".cmd")
            candidates.append(path)
    candidates += _install_candidates(kind, env)
    for cli in candidates:
        if not cli.is_file():
            continue
        exe = _gui_executable(cli, exe_name) if _IS_WINDOWS else None
        return Editor(kind, label, str(exe or cli))
    return None


_detected = {"time": -_DETECT_TTL, "editors": {}}


def detect(refresh: bool = False) -> dict:
    """{"vscode": Editor | None, "cursor": Editor | None}, cached for a while (preferences draw it)."""
    now = time.monotonic()
    if refresh or now - _detected["time"] > _DETECT_TTL:
        _detected.update(time=now, editors={kind: find_editor(kind) for kind in _EDITORS})
    return _detected["editors"]


# --- Commands ---------------------------------------------------------------------------


def split_command(text: str) -> list:
    """Split a command line into arguments. Double or single quotes group; backslashes are literal (Windows paths)."""
    args, current, quote, has_arg = [], [], None, False
    for char in text.strip():
        if quote:
            if char == quote:
                quote = None
            else:
                current.append(char)
        elif char in "\"'":
            quote, has_arg = char, True
        elif char.isspace():
            if current or has_arg:
                args.append("".join(current))
            current, has_arg = [], False
        else:
            current.append(char)
    if quote:
        raise CodeEditorError(f"Unbalanced quote in the editor command: {text}")
    if current or has_arg:
        args.append("".join(current))
    return args


def expand(template: list, file=None, line=None, column=None, project=None) -> list:
    """Fill {file} {line} {column} {project} into each argument of a command template.

    Opening only the project (no file): arguments with {line} or {column} are dropped,
    together with a flag right before them (`-g {file}:{line}`), and a lone {file}
    gets the folder unless the template has {project}. When no argument names the
    file or the folder, it is appended.
    """
    file = str(file) if file else None
    project = str(project) if project else (str(pathlib.Path(file).parent) if file else None)
    values = {"file": file, "line": str(max(1, int(line or 1))), "column": str(max(1, int(column or 1))),
              "project": project}
    values["col"] = values["column"]
    if file is None and not any("{project}" in arg for arg in template):
        values["file"] = project
    out, placed = [], False
    for index, arg in enumerate(template):
        names = set(_PLACEHOLDER.findall(arg))
        missing = [name for name in names if values[name] is None]
        if file is None and names & {"line", "column", "col"}:
            missing.append("line")
        if missing:
            previous = template[index - 1] if index else None
            if out and previous == out[-1] and previous.startswith("-") and not _PLACEHOLDER.search(previous):
                out.pop()
            continue
        placed = placed or bool(names & {"file", "project"})
        out.append(_PLACEHOLDER.sub(lambda m: values[m.group(1)], arg))
    if not placed and (file or project):
        out.append(file or project)
    return out


def editor_command(editor: Editor, file=None, line=None, column=None, project=None) -> list:
    """VS Code / Cursor: the project folder plus the file at line:column."""
    argv = [editor.executable]
    if project:
        argv.append(str(project))
    if file:
        argv += ["-g", f"{file}:{max(1, int(line or 1))}:{max(1, int(column or 1))}"]
    return argv


def resolve(preset: str, custom: str = "") -> Target:
    """What the preference points at. Raises CodeEditorError when it cannot be used."""
    preset = preset if preset in PRESETS else "AUTO"
    if preset == "CUSTOM":
        args = split_command(custom or "")
        if not args:
            raise CodeEditorError("The custom editor command is empty. Set it in the GameStudio preferences")
        return Target("Custom command", custom=args)
    if preset == "SYSTEM":
        return Target("System default")
    editors = detect()
    if preset == "AUTO":
        editor = editors.get("vscode") or editors.get("cursor")
        return Target(editor.label, editor=editor) if editor else Target("System default")
    editor = editors.get("vscode" if preset == "VSCODE" else "cursor")
    if editor is None:
        name = "VS Code" if preset == "VSCODE" else "Cursor"
        raise CodeEditorError(f"{name} was not found. Install it, or choose another code editor in the "
                              "GameStudio preferences")
    return Target(editor.label, editor=editor)


def build(target: Target, file=None, line=None, column=None, project=None) -> list | None:
    """The command for a target, or None for the system default (which has no command)."""
    if target.editor is not None:
        return editor_command(target.editor, file, line, column, project)
    if target.custom is not None:
        argv = expand(target.custom, file, line, column, project) or [""]
        program = toolchain.which(argv[0]) or (argv[0] if os.path.isfile(argv[0]) else None)
        if program is None:
            raise CodeEditorError(f"Could not find the editor program '{argv[0]}'. "
                                  "Check the custom command in the GameStudio preferences")
        return [program, *argv[1:]]
    return None


def command_line(argv: list) -> str:
    """argv as one line, quoted the way Windows passes it to the program (for display and logs)."""
    return subprocess.list2cmdline([str(a) for a in argv])


def is_source_file(path) -> bool:
    return pathlib.Path(path).suffix.lower() in SOURCE_SUFFIXES


# --- Launching --------------------------------------------------------------------------


def _settings():
    from ..prefs import get_prefs

    prefs = get_prefs()
    return getattr(prefs, "code_editor", "AUTO"), getattr(prefs, "code_editor_command", "")


def current() -> Target:
    """The target of the current preferences (raises CodeEditorError when it cannot be used)."""
    return resolve(*_settings())


def label() -> str:
    """A short name for buttons: "VS Code", "Cursor", "Code Editor"."""
    try:
        target = current()
    except CodeEditorError:
        return "Code Editor"
    return target.editor.label if target.editor is not None else "Code Editor"


def _spawn(argv: list) -> None:
    env = dict(os.environ)
    env.pop("ELECTRON_RUN_AS_NODE", None)  # would make Code.exe run the folder as a Node script
    kwargs = {"creationflags": _NO_WINDOW} if _IS_WINDOWS else {"start_new_session": True}
    subprocess.Popen([str(a) for a in argv], env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, close_fds=True, **kwargs)


# Files the system runs when it "opens" them (a .js runs in Windows Script Host, a .command in Terminal).
# A path from a project or a game's log may be one, so GameStudio shows its folder instead.
RUNS_WHEN_OPENED = frozenset((
    ".exe", ".com", ".bat", ".cmd", ".ps1", ".psm1", ".vbs", ".vbe", ".js", ".jse", ".mjs", ".cjs", ".wsf",
    ".wsh", ".hta", ".lnk", ".url", ".scr", ".pif", ".msi", ".msp", ".msc", ".cpl", ".reg", ".inf", ".scf",
    ".jar", ".py", ".pyw", ".appref-ms", ".application", ".gadget", ".settingcontent-ms",
    ".app", ".command", ".tool", ".sh", ".bash", ".zsh", ".csh", ".pkg", ".mpkg", ".terminal", ".workflow",
    ".scpt", ".applescript", ".desktop", ".appimage", ".run", ".bin",
))


def runs_when_opened(path) -> bool:
    """The system would run this file rather than show it: GameStudio never opens it with its default
    application (open_path, the Console's links, _open_with_system)."""
    path = pathlib.Path(path)
    if path.suffix.lower() in RUNS_WHEN_OPENED:
        return True
    return not _IS_WINDOWS and path.is_file() and os.access(path, os.X_OK)


def _open_with_system(path) -> None:
    if path is not None and runs_when_opened(path):
        path = pathlib.Path(path).parent  # its folder: opening the file would run it
    if _IS_WINDOWS:
        os.startfile(str(path))  # noqa: S606 - the documented way to open with the associated app
    else:
        subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(path)],
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def open_location(file=None, line=None, column=None, project=None) -> str:
    """Open a file at line:column (or just the project folder) in the code editor.

    Returns a short description for the status bar. Raises CodeEditorError.
    """
    target = current()
    argv = build(target, file, line, column, project)
    try:
        if argv is None:
            _open_with_system(file or project)
        else:
            log.debug("Opening the code editor: %s", command_line(argv))
            _spawn(argv)
    except OSError as exc:
        raise CodeEditorError(f"Could not start {target.label}: {exc}") from exc
    where = pathlib.Path(file).name if file else pathlib.Path(project).name
    if file and line:
        where += f":{line}"
    return f"Opened {where} in {target.label}"
