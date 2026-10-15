# SPDX-License-Identifier: GPL-3.0-or-later
"""Open in Codex: OpenAI's coding agent, next to Claude Code (claude_code.py).

What GameStudio does for Codex, and the OpenAI documentation each part follows (checked in October
2026; developers.openai.com/codex/... now redirects to learn.chatgpt.com/docs/...):

* finds the `codex` CLI: on PATH, then where the installers put it. The standalone installer, which
  is also how GameStudio installs it: %LOCALAPPDATA%\\Programs\\OpenAI\\Codex\\bin\\codex.exe on
  Windows, ~/.local/bin/codex on macOS and Linux (CODEX_INSTALL_DIR moves both); then Homebrew's
  cask and npm's global folders (`npm install -g @openai/codex`). Blender started from a shortcut
  often has an older PATH than a new terminal, so the folders matter;
* installs it when it is missing, after one click: OpenAI's standalone installer (INSTALL_COMMANDS,
  the commands developers.openai.com/codex/cli gives) in the background, with progress in the status
  bar (claude_code._Installer). It needs no administrator rights and adds its folder to the user's
  PATH. It runs with CODEX_NON_INTERACTIVE=1, so it asks nothing (it would offer to start Codex), and
  with its temporary folder in GameStudio's user folder, where its download can be measured;
* connects the project to Codex (connect), in plain words in the Open in Codex dialog:
  - .codex/config.toml gets [mcp_servers.gamestudio] with the command and args .mcp.json has (keys
    from developers.openai.com/codex/mcp: command, args, startup_timeout_sec), and keeps the rest of
    the file as it was written: a table is appended, or only its values change. Codex reads a
    project's .codex/ only once the person trusts the folder (it asks on its first start there);
  - .agents/skills/ gets a copy of each skill in .claude/skills/, the one source: Codex reads a
    repository's skills from .agents/skills (developers.openai.com/codex/skills), Claude Code from
    .claude/skills. A README there says so; each copy has a .gamestudio-copy.json. A copy follows its
    source when that changes, unless it was edited here; a folder without the file is the person's own
    skill and is never touched;
  - AGENTS.md, which Codex reads (developers.openai.com/codex/guides/agents-md): from the project's
    template, or, when the instructions are only in CLAUDE.md, moved there with the person's consent
    and copied otherwise (core/agentfiles.py), so no text of theirs is lost;
* keeps that working wherever Codex starts: when a scene of the project loads, after GameStudio's
  setup and before Codex opens (prepare_project), like .mcp.json: a Node.js path from another
  computer becomes this one's, a skill changed in .claude/skills is copied again. Save as Template
  makes .codex/config.toml portable (portable_codex_text) and leaves the copies out;
* opens a terminal in the project running codex: the launcher script .gamestudio/codex.cmd (macOS
  codex.command, which `open -a Terminal` runs; Linux codex.sh), GameStudio's Node.js first on PATH
  (claude_code.open_terminal).

Codex asks the person to sign in the first time it starts (Sign in with ChatGPT, or an OpenAI API
key): the dialogs say so. Nothing here runs a terminal, codex or its installer by itself: only Open in
Codex and Install Codex do, when the person clicks them.
"""

import copy
import hashlib
import json
import ntpath
import os
import pathlib
import posixpath
import re
import shutil
import subprocess
import sys
import time
import tomllib

import bpy
from bpy.app.handlers import persistent
from bpy.props import BoolProperty

from . import claude_code as cc
from . import environment, projects
from .claude_code import CODEX, ClaudeCodeError
from .core import agentfiles, project, toolchain
from .core.log import get_logger
from .core.util import any_window

log = get_logger(__name__)

DOCS_URL = "https://developers.openai.com/codex/cli"
INSTALL_PS1 = "irm https://chatgpt.com/codex/install.ps1 | iex"
INSTALL_SH = "curl -fsSL https://chatgpt.com/codex/install.sh | sh"
# The official install commands (developers.openai.com/codex/cli). Install Codex runs the first one of
# the platform (the standalone installer); the dialog shows them for installing by hand. Not the npm
# package's: it needs a Node.js on PATH, which a computer whose only Node.js is GameStudio's lacks.
INSTALL_COMMANDS = {
    "windows": (("PowerShell", f'powershell -ExecutionPolicy ByPass -c "{INSTALL_PS1}"'),),
    "macos": (("Terminal", INSTALL_SH), ("Homebrew", "brew install --cask codex")),
    "linux": (("Terminal", INSTALL_SH),),
}
# About how much the installer downloads: codex-package-<platform>.tar.gz of the release (rust-v0.162,
# October 2026: Windows x64 160 MB, macOS 132-144 MB, Linux 153-163 MB).
INSTALL_MB = {"windows": 160, "macos": 145, "linux": 165}
INSTALL_LOG = "codex-install.log"           # in GameStudio's user folder
INSTALL_TMP = "codex-install-tmp"           # the installer's temporary folder, in GameStudio's user folder

CONFIG = ".codex/config.toml"
SKILLS = ".agents/skills"                   # Codex's copy of the skills
SKILLS_SOURCE = ".claude/skills"            # the one source (Claude Code reads it)
COPY_MARK = ".gamestudio-copy.json"         # in each copy: what it was copied from
README = "README.md"
README_MARK = "<!-- gamestudio:codex-skills -->"
STARTUP_TIMEOUT = 30                        # seconds (Codex's default is 10; Node.js can be slow to start)
SERVER = cc.MCP_SERVER

_DETECT_TTL = 10.0
_IS_WINDOWS = sys.platform == "win32"
_FIND = object()


class CodexError(RuntimeError):
    pass


# --- Finding the CLI ------------------------------------------------------------------------


def install_locations(platform: str, home: str, env: dict) -> list[str]:
    """Where the installers put `codex` (platform is a claude_code.platform_key()), most likely first:
    the standalone installer (CODEX_INSTALL_DIR, else its default), Homebrew, then npm's global
    folders (its default, NPM_CONFIG_PREFIX or `prefix=` in ~/.npmrc, ~/.npm-global, Volta, nvm)."""
    if platform == "windows":
        join = ntpath.join
        found = [join(env["CODEX_INSTALL_DIR"], "codex.exe")] if env.get("CODEX_INSTALL_DIR") else []
        local = env.get("LOCALAPPDATA") or join(home, "AppData", "Local")
        found.append(join(local, "Programs", "OpenAI", "Codex", "bin", "codex.exe"))
        if env.get("APPDATA"):
            found.append(join(env["APPDATA"], "npm", "codex.cmd"))
        found += [join(prefix, "codex.cmd") for prefix in cc._npm_prefixes(platform, home, env)]
        if env.get("LOCALAPPDATA"):
            found.append(join(env["LOCALAPPDATA"], "Volta", "bin", "codex.exe"))
        return list(dict.fromkeys(found))
    join = posixpath.join
    found = [join(env["CODEX_INSTALL_DIR"], "codex")] if env.get("CODEX_INSTALL_DIR") else []
    found.append(join(home, ".local", "bin", "codex"))
    if platform == "macos":
        found += ["/opt/homebrew/bin/codex", "/usr/local/bin/codex"]
    else:
        found += ["/usr/local/bin/codex", "/home/linuxbrew/.linuxbrew/bin/codex", "/usr/bin/codex"]
    found += [join(prefix, "bin", "codex") for prefix in cc._npm_prefixes(platform, home, env)]
    found += [join(home, ".npm-global", "bin", "codex"), join(home, ".volta", "bin", "codex"),
              *cc._nvm_bins(home, "codex")]
    return list(dict.fromkeys(found))


def find_codex(platform: str | None = None, env: dict | None = None, home: str | None = None,
               which=toolchain.which, isfile=os.path.isfile) -> str | None:
    """The `codex` executable: on PATH, else in an install location, else None."""
    platform = cc.platform_key(platform)
    env = os.environ if env is None else env
    if home is None:
        home = env.get("USERPROFILE") if platform == "windows" else None
        home = home or os.path.expanduser("~")
    found = which("codex")
    if found:
        root, ext = os.path.splitext(str(found))
        return root + ext.lower() if platform == "windows" else str(found)  # PATHEXT spells it .EXE
    return next((path for path in install_locations(platform, home, env) if isfile(path)), None)


_cache = {"time": -_DETECT_TTL, "path": None}


def codex_path(refresh: bool = False) -> str | None:
    """find_codex(), remembered briefly: menus and panels ask on every redraw."""
    now = time.monotonic()
    if refresh or now - _cache["time"] > _DETECT_TTL:
        _cache.update(time=now, path=find_codex())
    return _cache["path"]


def install_commands(platform: str | None = None) -> tuple:
    return INSTALL_COMMANDS[cc.platform_key(platform)]


def install_size_mb(platform: str | None = None) -> int:
    return INSTALL_MB[cc.platform_key(platform)]


# --- Installing it --------------------------------------------------------------------------


def install_argv(platform: str | None = None, which=toolchain.which, isfile=os.path.isfile,
                 env: dict | None = None) -> list[str] | None:
    """The official standalone installer as the process that runs it: Windows PowerShell with the
    execution policy the official command gives (-ExecutionPolicy ByPass); bash on macOS and Linux,
    with pipefail so that a failed download is a failure, not an empty script. None when this computer
    cannot run it (install_problem)."""
    platform = cc.platform_key(platform)
    env = os.environ if env is None else env
    if platform == "windows":
        system_root = next((v for k, v in env.items() if k.upper() == "SYSTEMROOT" and v), "C:\\Windows")
        builtin = ntpath.join(system_root, "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
        shell = builtin if isfile(builtin) else (which("powershell.exe") or which("pwsh.exe"))
        return ([str(shell), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "ByPass", "-Command", INSTALL_PS1]
                if shell else None)
    bash = "/bin/bash" if isfile("/bin/bash") else which("bash")
    if not bash or not which("curl"):
        return None
    return [str(bash), "-o", "pipefail", "-c", INSTALL_SH]


def install_problem(platform: str | None = None, which=toolchain.which, isfile=os.path.isfile,
                    env: dict | None = None) -> str:
    """Why GameStudio cannot run the installer here (then the dialog guides the person), or ""."""
    if install_argv(platform, which, isfile, env) is not None:
        return ""
    if cc.platform_key(platform) == "windows":
        return "Windows PowerShell, which the installer needs, was not found on this computer."
    if not which("curl"):
        return ("The installer needs curl, which this computer does not have. Install it with your system's "
                "software manager (Ubuntu and Debian: sudo apt install curl), then try again.")
    return "The installer needs bash, which this computer does not have."


def install_log_path() -> pathlib.Path:
    return environment.user_dir() / INSTALL_LOG


def install_temp_dir() -> pathlib.Path:
    return environment.user_dir() / INSTALL_TMP


def install_env(temp: pathlib.Path, base: dict | None = None) -> dict:
    """The installer's environment: no questions (CODEX_NON_INTERACTIVE), and its temporary folder
    (where it downloads) in GameStudio's user folder."""
    env = dict(os.environ if base is None else base)
    env["CODEX_NON_INTERACTIVE"] = "1"
    for name in ("TEMP", "TMP", "TMPDIR"):
        env[name] = str(temp)
    return env


def _download_size(folder: pathlib.Path, since: float) -> int:
    """How far the installer's download is: the biggest Codex archive below its temporary folder
    (codex-package-<platform>.tar.gz, or the older codex-npm-*.tgz)."""
    best = 0
    base = len(pathlib.Path(folder).parts)
    for dirpath, dirnames, filenames in os.walk(folder):
        if len(pathlib.Path(dirpath).parts) - base >= 2:
            dirnames[:] = []  # the extracted files are no download
        for name in filenames:
            if name.startswith("codex") and name.endswith((".tar.gz", ".tgz", ".zip")):
                try:
                    stat = os.stat(os.path.join(dirpath, name))  # not DirEntry.stat(): see claude_code
                except OSError:
                    continue
                if stat.st_mtime >= since - 5:
                    best = max(best, stat.st_size)
    return best


def _popen_installer(argv, **kwargs):
    """The installer's process (tests replace this: they never run the real installer)."""
    return subprocess.Popen([str(a) for a in argv], **kwargs)


class _CodexInstaller(cc._Installer):
    """Install Codex: claude_code's installer job with OpenAI's standalone installer."""

    label = "Codex"
    program = "codex"
    host = "chatgpt.com"
    install_op = CODEX.install_op
    cancel_op = "gamestudio.codex_install_cancel"
    docs_url = DOCS_URL

    def size_mb(self) -> int:
        return install_size_mb()

    def find(self) -> str | None:
        return find_codex()

    def refresh(self) -> None:
        codex_path(refresh=True)

    def popen(self, argv, **kwargs):
        return _popen_installer(argv, **kwargs)

    def download_size(self, home: pathlib.Path) -> int:
        return _download_size(install_temp_dir(), self.started)

    def tick(self):
        return _install_tick

    def _run(self, argv, home, env) -> int:
        try:
            return super()._run(argv, home, env)
        finally:  # before the result shows: the installer removes its own folder, this is the one around it
            shutil.rmtree(install_temp_dir(), ignore_errors=True)


INSTALLER = _CodexInstaller()


def start_install(then=None) -> str:
    """Run the official installer in the background; then(ok, message) when it ends. Returns "" when
    it started (or was already running: `then` waits for it), else why it cannot."""
    if INSTALLER.running():
        if then is not None:
            INSTALLER.then(then)
        return ""
    argv = install_argv()
    if argv is None:
        return install_problem()
    home = pathlib.Path(os.environ.get("USERPROFILE") if _IS_WINDOWS and os.environ.get("USERPROFILE")
                        else os.path.expanduser("~"))
    temp = install_temp_dir()
    shutil.rmtree(temp, ignore_errors=True)
    try:
        temp.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return f"Could not make the installer's folder {temp}: {exc}"
    if not INSTALLER.start(argv, install_log_path(), home, install_env(temp), then):
        return "Codex's installer is already running"
    log.info("Installing Codex: %s", argv)
    return ""


def _install_tick():
    return cc._tick(INSTALLER)


def install_status() -> tuple[str, str, bool] | None:
    return cc.install_status(INSTALLER)


def draw_install_status(self, context) -> None:
    """Appended to the status bar: Codex's install, its progress, Cancel or Try Again."""
    cc.draw_installer_status(self.layout, INSTALLER)


def draw_cli_status(layout) -> None:
    """Where codex is, the install that runs, or the button that installs it."""
    cc.draw_program_status(layout, codex_path(), INSTALLER)


# --- .codex/config.toml ---------------------------------------------------------------------
#
# Codex starts the project's MCP server from [mcp_servers.gamestudio] in the project's
# .codex/config.toml, in the folder it runs in (no `cwd`: like .mcp.json, the script is the
# project's own node_modules/@gamestudio/mcp). The command is `node`, or the absolute path of the
# Node.js GameStudio uses on a computer without a Node.js 22.12+ on its PATH, exactly as in
# .mcp.json (claude_code.mcp_config_changes). The file can hold the person's own settings: GameStudio
# appends its table, or changes only the values in it, and writes nothing it has not read back as
# the TOML it means.

_HEADER_COMMENT = ("# Codex's settings for this project. GameStudio added the gamestudio MCP server below:\n"
                   "# Codex builds this game in Blender through it. GameStudio keeps its command working on\n"
                   "# this computer; the rest of this file is yours. Codex reads this file only in a folder\n"
                   "# you trust (it asks the first time it starts here).\n")
_TABLE_COMMENT = "# GameStudio's MCP server: Codex builds this game in Blender through it.\n"
_STRING = re.compile(r'"(?:[^"\\\n]|\\.)*"|\'[^\'\n]*\'')
_HEADER = re.compile(r"^[ \t]*\[(?P<array>\[)?[ \t]*(?P<key>[^\[\]\r\n]+?)[ \t]*\](?(array)\])[ \t]*"
                     r"(?:#[^\r\n]*)?\r?$", re.MULTILINE)


def toml_string(value: str) -> str:
    """A TOML string: a literal string for a Windows path ('C:\\Users\\...', no doubled backslashes),
    else a basic string."""
    if "\\" in value and "'" not in value and not re.search(r"[\x00-\x1f\x7f]", value):
        return f"'{value}'"
    return json.dumps(value, ensure_ascii=False).replace("\x7f", "\\u007f")


def table_text(command: str, args: list) -> str:
    """GameStudio's [mcp_servers.gamestudio] table."""
    return (f"[mcp_servers.{SERVER}]\n"
            f"command = {toml_string(command)}\n"
            f"args = [{', '.join(toml_string(str(arg)) for arg in args)}]\n"
            f"startup_timeout_sec = {STARTUP_TIMEOUT}\n")


def codex_server(data) -> dict | None:
    """GameStudio's server in a parsed config.toml ([mcp_servers.gamestudio], started by node with
    gamestudio-mcp.js), else None (missing, or the person's own way of starting it: left alone)."""
    servers = data.get("mcp_servers") if isinstance(data, dict) else None
    return cc.mcp_server({"mcpServers": servers}) if isinstance(servers, dict) else None


def has_server(root) -> bool:
    """True when the project's .codex/config.toml has a gamestudio MCP server (in any form)."""
    try:
        data = tomllib.loads((pathlib.Path(root) / CONFIG).read_bytes().decode("utf-8-sig"))
    except (OSError, ValueError):
        return False
    servers = data.get("mcp_servers")
    return isinstance(servers, dict) and isinstance(servers.get(SERVER), dict)


def _table_span(text: str) -> tuple[int, int] | None:
    """Where the body of the [mcp_servers.gamestudio] table is in text (after its header, up to the
    next header), or None when it has no such header (an inline table, dotted keys)."""
    headers = list(_HEADER.finditer(text))
    for index, match in enumerate(headers):
        if not match.group("array") and re.sub(r"[\s\"']", "", match.group("key")) == f"mcp_servers.{SERVER}":
            return match.end(), headers[index + 1].start() if index + 1 < len(headers) else len(text)
    return None


def _swap(part: str, key: str | None, old: str, new: str) -> str | None:
    """part with the TOML string whose value is old (the value of `key` when key is given) written as
    new, when there is exactly one; else None."""
    found = []
    for match in _STRING.finditer(part):
        try:
            value = tomllib.loads("v = " + match.group(0))["v"]
        except tomllib.TOMLDecodeError:
            continue
        if value != old:
            continue
        line = part[:match.start()].rsplit("\n", 1)[-1]
        if key is not None and not re.search(r"(?:^|[\s{,.])" + re.escape(key) + r"\s*=\s*$", line):
            continue
        found.append(match)
    if len(found) != 1:
        return None
    match = found[0]
    return part[:match.start()] + toml_string(new) + part[match.end():]


def apply_config_changes(text: str, data: dict, changes: dict) -> str | None:
    """config.toml's text with the changes (claude_code.mcp_config_changes) made in its gamestudio
    table, the rest as it was; None when they cannot be made safely (the result must read back as the
    same settings with only those values changed)."""
    wanted = copy.deepcopy(data)
    server = wanted["mcp_servers"][SERVER]
    start, end = _table_span(text) or (0, len(text))
    part = text[start:end]
    if "command" in changes:
        part = _swap(part, "command", server["command"], changes["command"])
        server["command"] = changes["command"]
    if part is not None and "script" in changes:
        index, script = changes["script"]
        part = _swap(part, None, server["args"][index], script)
        server["args"][index] = script
    if part is None:
        return None
    new = text[:start] + part + text[end:]
    try:
        return new if tomllib.loads(new) == wanted else None
    except tomllib.TOMLDecodeError:
        return None


def _read_config(path: pathlib.Path) -> tuple[bytes, str, dict] | None:
    """(the BOM or b"", the text, the settings) of a config.toml, or None when there is none. Raises
    CodexError when it is not TOML, OSError when it cannot be read."""
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return None
    try:
        text = raw.decode("utf-8-sig")
        return (b"\xef\xbb\xbf" if raw.startswith(b"\xef\xbb\xbf") else b""), text, tomllib.loads(text)
    except ValueError as exc:  # not UTF-8, or not TOML
        raise CodexError(f"{CONFIG} is not valid TOML ({exc}); GameStudio leaves it as it is") from exc


def _write_text(path: pathlib.Path, data: bytes) -> None:
    """Write through a temporary file, so a failure leaves the old file whole."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.gamestudio-tmp")
    try:
        tmp.write_bytes(data)
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


def server_command(root, node: str | None) -> tuple[str, list]:
    """The command and args of GameStudio's MCP server for this project on this computer: those of
    its .mcp.json made right here (claude_code.mcp_config_changes), else `node` (or GameStudio's
    Node.js by its path) and the project's own script."""
    root = pathlib.Path(root)
    try:
        config = json.loads((root / ".mcp.json").read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        config = None
    server = cc.mcp_server(config) if config is not None else None
    if server is None:
        return node or "node", [cc.mcp_entry(root)]
    changes = cc.mcp_config_changes(root, config, node)
    args = list(server["args"])
    if "script" in changes:
        index, script = changes["script"]
        args[index] = script
    return changes.get("command", server["command"]), args


def write_codex_config(root, node=_FIND) -> str:
    """Give the project's .codex/config.toml GameStudio's MCP server (server_command), keeping the
    rest of the file: a new file, a table appended, or the table's values made right for this
    computer (sync_codex_config). Returns "added", "updated" or "" (nothing to do). Raises OSError,
    or CodexError when the file is not TOML or the table cannot be added safely."""
    root = pathlib.Path(root)
    path = root / CONFIG
    node = cc.mcp_node_command() if node is _FIND else node
    read = _read_config(path)
    if read is None:
        command, args = server_command(root, node)
        _write_text(path, (_HEADER_COMMENT + "\n" + table_text(command, args)).encode("utf-8"))
        log.info("Wrote %s", path)
        return "added"
    bom, text, data = read
    servers = data.get("mcp_servers", {})
    if not isinstance(servers, dict):
        raise CodexError(f"mcp_servers in {CONFIG} is not a table; GameStudio leaves the file as it is")
    if SERVER in servers:
        return "updated" if sync_codex_config(root, node) else ""
    command, args = server_command(root, node)
    newline = "\r\n" if "\r\n" in text else "\n"
    body = (_TABLE_COMMENT + table_text(command, args)).replace("\n", newline)
    if text.strip():
        body = ("" if text.endswith("\n") else newline) + newline + body
    new = text + body
    expected = copy.deepcopy(data)
    expected.setdefault("mcp_servers", {})[SERVER] = {"command": command, "args": args,
                                                      "startup_timeout_sec": STARTUP_TIMEOUT}
    try:
        ok = tomllib.loads(new) == expected
    except tomllib.TOMLDecodeError:
        ok = False
    if not ok:  # e.g. mcp_servers is an inline table, which cannot get another table
        raise CodexError(f"GameStudio could not add its MCP server to {CONFIG} without changing what is there: "
                         "add the [mcp_servers.gamestudio] table by hand (docs/MCP.md, Codex)")
    _write_text(path, bom + new.encode("utf-8"))
    log.info("Added the gamestudio MCP server to %s", path)
    return "added"


def sync_codex_config(root, node=_FIND) -> bool:
    """Make the project's .codex/config.toml start the MCP server on this computer (the changes
    .mcp.json would get, claude_code.mcp_config_changes, with mcp_node_command() unless `node` is
    given). Writes only when something changed and the rest stays as it was. Raises OSError when it
    cannot write; returns True when it wrote."""
    root = pathlib.Path(root)
    path = root / CONFIG
    try:
        read = _read_config(path)
    except (OSError, CodexError) as exc:
        log.warning("%s", exc)
        return False
    if read is None or codex_server(read[2]) is None:
        return False
    bom, text, data = read
    node = cc.mcp_node_command() if node is _FIND else node
    changes = cc.mcp_config_changes(root, {"mcpServers": data["mcp_servers"]}, node)
    if not changes:
        return False
    new = apply_config_changes(text, data, changes)
    if new is None:
        log.warning("Could not update %s for this computer (%s): it is left as it is", path, changes)
        return False
    _write_text(path, bom + new.encode("utf-8"))
    log.info("%s now starts the MCP server with %s", path, changes)
    return True


def portable_codex_text(text: str) -> str:
    """.codex/config.toml for a template or another computer: `node` by name and the project's own
    script (like claude_code.portable_mcp_text)."""
    try:
        data = tomllib.loads(text)
    except ValueError:
        return text
    server = codex_server(data)
    if server is None:
        return text
    changes = {}
    if not cc._is_bare(server["command"]):
        changes["command"] = "node"
    index = cc._script_index(server["args"])
    current = server["args"][index]
    if current != cc.MCP_ENTRY and current.replace("\\", "/").endswith(cc.MCP_ENTRY):
        changes["script"] = (index, cc.MCP_ENTRY)
    if not changes:
        return text
    return apply_config_changes(text, data, changes) or text


# --- .agents/skills: Codex's copy of the skills ----------------------------------------------

_README = f"""{README_MARK}
# Skills for Codex

GameStudio copies each skill in `.claude/skills/` here, because Codex reads a project's skills from
`.agents/skills/` and Claude Code from `.claude/skills/`. Edit a skill in `.claude/skills/`: GameStudio
copies it here again when you open the project or open it in Codex.

A folder here with a `{COPY_MARK}` file is such a copy. A copy you edit here is left as it is and no
longer follows its source. Folders without that file are yours: GameStudio never changes them, so add
skills that only Codex should have that way.
"""


def _digest(folder: pathlib.Path) -> str:
    """A hash of a skill folder's files (names and contents), its COPY_MARK aside."""
    digest = hashlib.sha256()
    for path in sorted(p for p in folder.rglob("*") if p.is_file()):
        rel = path.relative_to(folder).as_posix()
        if rel == COPY_MARK:
            continue
        digest.update(rel.encode("utf-8") + b"\0" + path.read_bytes() + b"\0")
    return digest.hexdigest()


def source_skills(root) -> dict[str, pathlib.Path]:
    """The project's skills for Claude Code (.claude/skills/<name>/SKILL.md), by name."""
    folder = pathlib.Path(root) / SKILLS_SOURCE
    try:
        return {p.name: p for p in sorted(folder.iterdir()) if p.is_dir() and (p / "SKILL.md").is_file()}
    except OSError:
        return {}


def copy_info(folder: pathlib.Path) -> dict | None:
    """What a copy in .agents/skills was copied from ({"source", "digest"}), or None: not a copy."""
    try:
        info = json.loads((folder / COPY_MARK).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return info if isinstance(info, dict) and isinstance(info.get("digest"), str) else None


def _copy_skill(src: pathlib.Path, dest: pathlib.Path, digest: str) -> None:
    """Copy a skill folder (replacing dest, an unedited copy) through a temporary folder."""
    tmp = dest.with_name(f".{dest.name}.gamestudio-tmp")
    shutil.rmtree(tmp, ignore_errors=True)
    shutil.copytree(src, tmp, ignore=shutil.ignore_patterns("__pycache__", COPY_MARK))
    note = {"source": f"{SKILLS_SOURCE}/{src.name}", "digest": digest,
            "note": f"GameStudio's copy of {SKILLS_SOURCE}/{src.name} for Codex: edit the skill there. Delete "
                    "this file to make this folder yours (GameStudio then leaves it alone)."}
    (tmp / COPY_MARK).write_text(json.dumps(note, indent=2) + "\n", encoding="utf-8", newline="\n")
    if dest.exists():
        shutil.rmtree(dest)
    os.replace(tmp, dest)


def mirrored(root) -> bool:
    """True when the project has Codex's copy of the skills (GameStudio's README in .agents/skills)."""
    try:
        with open(pathlib.Path(root) / SKILLS / README, encoding="utf-8") as readme:
            return readme.readline().strip() == README_MARK
    except (OSError, UnicodeDecodeError):
        return False


def mirror_skills(root) -> dict:
    """Copy .claude/skills/ to .agents/skills/ for Codex. A copy that is missing is made, one whose
    source changed is made again, one whose source is gone is removed, except where the copy was
    edited here (it no longer matches what GameStudio copied); a folder without COPY_MARK is the
    person's and never touched. Returns {"copied", "updated", "removed", "kept"} (skill names; kept:
    left alone, the person's own or edited). Raises OSError."""
    root = pathlib.Path(root)
    folder = root / SKILLS
    result = {"copied": [], "updated": [], "removed": [], "kept": []}
    sources = source_skills(root)
    folder.mkdir(parents=True, exist_ok=True)
    readme = folder / README
    if not readme.exists() or mirrored(root):
        if not readme.exists() or readme.read_text(encoding="utf-8") != _README:
            readme.write_text(_README, encoding="utf-8", newline="\n")
    for name, src in sources.items():
        dest = folder / name
        digest = _digest(src)
        if not dest.exists():
            _copy_skill(src, dest, digest)
            result["copied"].append(name)
            continue
        info = copy_info(dest)
        if info is None or not dest.is_dir():
            result["kept"].append(name)  # the person's own skill of that name
        elif info["digest"] == digest:
            continue  # up to date
        elif _digest(dest) != info["digest"]:
            result["kept"].append(name)  # edited here
        else:
            _copy_skill(src, dest, digest)
            result["updated"].append(name)
    for dest in sorted(p for p in folder.iterdir() if p.is_dir() and p.name not in sources):
        info = copy_info(dest)
        if info is not None and _digest(dest) == info["digest"]:
            shutil.rmtree(dest)
            result["removed"].append(dest.name)
    if any(result[key] for key in ("copied", "updated", "removed")):
        log.info("Codex's copy of the skills in %s: %s", folder, result)
    return result


def generated_paths(root) -> set[str]:
    """The project-relative paths of what GameStudio made in .agents/skills (its README and the
    unedited copies): Save as Template leaves them out (a new project gets them when it is connected)."""
    root = pathlib.Path(root)
    folder = root / SKILLS
    found = set()
    if mirrored(root):
        found.add(f"{SKILLS}/{README}")
    try:
        entries = [p for p in folder.iterdir() if p.is_dir()]
    except OSError:
        return found
    for dest in entries:
        info = copy_info(dest)
        if info is not None and _digest(dest) == info["digest"]:
            found.update(p.relative_to(root).as_posix() for p in dest.rglob("*") if p.is_file())
    return found


# --- Connecting a project -------------------------------------------------------------------


def _state(root) -> str:
    try:
        return agentfiles.instructions_state(root)
    except (OSError, ValueError):  # an unreadable file: the person's, left alone
        return agentfiles.SEPARATE


def offers_move(root) -> bool:
    """True when the instructions are only in CLAUDE.md (or GameStudio's copy of it is AGENTS.md):
    the Open in Codex dialog asks whether to move them to AGENTS.md."""
    return _state(root) in (agentfiles.CLAUDE_ONLY, agentfiles.COPY)


def missing_codex_files(root) -> list[str]:
    """What Codex lacks in the project: CONFIG (no gamestudio MCP server there), "AGENTS.md" (no
    instructions Codex reads) and SKILLS (no copy of the skills yet)."""
    root = pathlib.Path(root)
    missing = []
    if not has_server(root):
        missing.append(CONFIG)
    if _state(root) in (agentfiles.NONE, agentfiles.AGENTS_MISSING, agentfiles.CLAUDE_ONLY):
        missing.append("AGENTS.md")
    if not mirrored(root):
        missing.append(SKILLS)
    return missing


_PLAIN_NAMES = {"AGENTS.md": "instructions for this game", SKILLS: "Codex's copy of the skills",
                CONFIG: "the link to Blender", cc.MCP_PACKAGE: "the link to Blender"}


def connect_text(files) -> str:
    """What "Connect Codex to this project" adds, in plain words (the file names are in its tooltip)."""
    order = list(_PLAIN_NAMES)
    files = sorted(files, key=lambda name: order.index(name) if name in order else len(order))
    names = list(dict.fromkeys(_PLAIN_NAMES.get(name, name) for name in files))
    if not names:
        return ""
    listed = names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]
    return f"Adds {listed}."


def connect(root, move: bool = False, template: str | None = None) -> list[str]:
    """Connect the project to Codex: AGENTS.md (from the template when there are no instructions; the
    ones only in CLAUDE.md moved there when `move`, the person's consent, else copied), .claude/ when
    missing (the skills' source), .codex/config.toml, the copy of the skills and the MCP server's
    package. Never overwrites a file of the person's. Returns what it did, in sentences. Raises
    OSError, ValueError or CodexError."""
    root = pathlib.Path(root)
    template = template or cc.agent_template(root)
    notes = []
    state = agentfiles.instructions_state(root)
    names = []
    if state in (agentfiles.NONE, agentfiles.AGENTS_MISSING):
        names.append("AGENTS.md")
    if state == agentfiles.NONE:
        names.append("CLAUDE.md")  # the import, so both agents read the same instructions
    if not (root / ".claude").exists():
        names.append(".claude")
    added = cc.add_agent_files(root, template, names)
    if added:
        label = (projects.template_info(template) or {}).get("label", template)
        notes.append(f"Added {', '.join(added)} from the {label} template")
    state = agentfiles.instructions_state(root)
    if state in (agentfiles.CLAUDE_ONLY, agentfiles.COPY):
        # A CLAUDE.md that just came from an older template holds nothing of the person's.
        result = agentfiles.migrate_instructions(root, consent=move or "CLAUDE.md" in added)
        if result["message"]:
            notes.append(result["message"])
    what = write_codex_config(root)
    if what:
        notes.append(f"{'Added' if what == 'added' else 'Updated'} GameStudio's MCP server in {CONFIG}")
    mirror = mirror_skills(root)
    copied = len(mirror["copied"]) + len(mirror["updated"])
    if copied:
        notes.append(f"Copied {copied} skill{'s' if copied != 1 else ''} to {SKILLS} for Codex")
    if mirror["kept"]:
        notes.append(f"Left {', '.join(mirror['kept'])} in {SKILLS} as they are")
    note = cc.ensure_mcp_package(root)
    if note:
        notes.append(note)
    return notes


def uses_codex(root) -> bool:
    """True when the project is connected to Codex: a gamestudio server in .codex/config.toml, or the
    copy of the skills."""
    return has_server(root) or mirrored(root)


def prepare_project(root) -> list[str]:
    """Keep a project's Codex connection working on this computer: when a scene of the project loads,
    after GameStudio's setup and before Codex opens. Only for a project connected to Codex
    (uses_codex): the server's command (sync_codex_config), the copy of the skills (mirror_skills) and
    GameStudio's copy of CLAUDE.md in AGENTS.md. Never raises; returns what it changed."""
    root = pathlib.Path(root)
    if not uses_codex(root):
        return []
    done = []
    try:
        if sync_codex_config(root):
            done.append(f"Updated {CONFIG} for this computer")
    except OSError as exc:
        log.warning("Could not update %s: %s", root / CONFIG, exc)
    try:
        mirror = mirror_skills(root)
        if mirror["copied"] or mirror["updated"] or mirror["removed"]:
            done.append(f"Updated Codex's copy of the skills ({SKILLS})")
    except OSError as exc:
        log.warning("Could not update %s: %s", root / SKILLS, exc)
    try:
        if agentfiles.instructions_state(root) == agentfiles.COPY:
            result = agentfiles.migrate_instructions(root, consent=False)
            if result["action"] == agentfiles.REFRESHED:
                done.append(result["message"])
    except (OSError, ValueError) as exc:
        log.warning("Could not update %s: %s", root / "AGENTS.md", exc)
    return done


@persistent
def _on_load_post(*_args):
    root = project.project_root()
    if root is None:
        return
    try:
        for note in prepare_project(root):
            log.info("%s: %s", root, note)
    except Exception:  # noqa: BLE001 - never break loading a file
        log.exception("Checking the Codex files failed")


# --- Operators ------------------------------------------------------------------------------


def _muted(layout):
    row = layout.row()
    row.active = False
    return row


def _launch(root, program: str) -> cc.Launch:
    """The project's MCP settings made right for this computer (Node.js may have just been set up),
    then the terminal running codex. Raises ClaudeCodeError."""
    for note in (*cc.prepare_project(root), *prepare_project(root)):
        log.info("%s: %s", root, note)
    launch = cc.open_terminal(root, program, CODEX)
    cc.opened(CODEX)
    return launch


def _open_after_install(ok: bool, _message: str = "") -> None:
    """Open in Codex again once its install is done (from a timer: the install ends in one)."""
    if not ok or project.project_root() is None:
        return

    def go():
        window = any_window()
        try:
            if window is not None:
                with bpy.context.temp_override(window=window, screen=window.screen):
                    bpy.ops.gamestudio.open_codex("INVOKE_DEFAULT")
            else:
                bpy.ops.gamestudio.open_codex()
        except RuntimeError as exc:
            environment.notify(f"Codex did not open: {exc}", "ERROR")
        return None

    bpy.app.timers.register(go, first_interval=0.3)


def _open_when_ready(root, program: str, show_prompts: bool):
    """Open in Codex's second half, once GameStudio's setup is done."""
    def done(ok, message):
        if not ok and environment.missing_essentials(root, CODEX.purpose):
            environment.notify(message or "Codex did not open: GameStudio's setup did not finish", "ERROR")
            return
        try:
            launch = _launch(root, program)
        except ClaudeCodeError as exc:
            environment.notify(str(exc), "ERROR")
            return
        environment.notify(f"Codex is starting in {launch.label}", "INFO")
        if show_prompts:
            _show_after_launch(any_window())
    return done


def _show_after_launch(window) -> None:
    """The first-start notes and starter prompts, once the dialog that started Codex has closed."""
    def show():
        try:
            if window is not None and window in bpy.context.window_manager.windows[:]:
                with bpy.context.temp_override(window=window, screen=window.screen):
                    bpy.ops.gamestudio.codex_started("INVOKE_DEFAULT")
            else:
                bpy.ops.gamestudio.codex_started("INVOKE_DEFAULT")
        except RuntimeError as exc:
            log.warning("Could not show the starter prompts: %s", exc)
        return None

    bpy.app.timers.register(show, first_interval=0.2)


SIGN_IN_LINES = ("Codex asks you to sign in the first time it starts: with your ChatGPT account (Plus, Pro,",
                 "Business, Edu or Enterprise) or with an OpenAI API key.")
TRUST_LINES = ("When it asks whether to trust this folder, choose Trust and continue: Codex loads",
               "GameStudio's tools (.codex/config.toml) only in a folder you trust.")


class GAMESTUDIO_OT_open_codex(bpy.types.Operator):
    bl_idname = "gamestudio.open_codex"
    bl_label = "Open in Codex"
    bl_description = ("Open a terminal in the project folder running Codex, OpenAI's coding agent, which builds the "
                      "game in this Blender. When Codex is not installed yet, GameStudio offers to install it")

    add_files: BoolProperty(name="Connect Codex to this project", default=True, options={"SKIP_SAVE"},
                            description="Add what Codex reads in this project: instructions for this game "
                                        "(AGENTS.md), a copy of the skills (.agents/skills/, copied from "
                                        ".claude/skills/) and its link to Blender (.codex/config.toml and the "
                                        "@gamestudio/mcp package). Files the project already has are never "
                                        "overwritten; .codex/config.toml keeps your own settings")
    move_instructions: BoolProperty(name="Move the instructions to AGENTS.md", default=False, options={"SKIP_SAVE"},
                                    description="This game's instructions are in CLAUDE.md, which only Claude Code "
                                                "reads. Move them, word for word, to AGENTS.md, which Claude Code "
                                                "and Codex both read; CLAUDE.md then holds @AGENTS.md (and any "
                                                "section about Claude Code). Unticked, CLAUDE.md stays as it is and "
                                                "Codex gets a copy in AGENTS.md, refreshed while you do not edit it")
    start_bridge: BoolProperty(name="Let Codex work in this Blender", default=True, options={"SKIP_SAVE"},
                               description="Turn on the agent bridge, through which Codex builds in this Blender. "
                                           "Only programs on this computer can use it (127.0.0.1, with a "
                                           "per-session token)")

    @classmethod
    def poll(cls, context):
        if project.project_root() is None:
            cls.poll_message_set("Open or create a game project first")
            return False
        return True

    @staticmethod
    def _needs(root) -> tuple[list[str], bool, bool]:
        """(what the project lacks for Codex, whether the bridge is off, whether to offer the move)."""
        files = missing_codex_files(root)
        if cc.mcp_missing(root) and cc.toolchain.read_manifest(root):
            files.append(cc.MCP_PACKAGE)
        return files, cc.bridge_state()[0] == "off", offers_move(root)

    def invoke(self, context, event):
        if INSTALLER.running():
            INSTALLER.then(_open_after_install)
            self.report({"INFO"}, "Codex is being installed (see the status bar); it opens when it is ready")
            return {"FINISHED"}
        if codex_path(refresh=True) is None:
            bpy.ops.gamestudio.codex_install("INVOKE_DEFAULT", open_after=True)
            return {"CANCELLED"}
        files, bridge_off, _offer = self._needs(project.project_root())
        if files or bridge_off:
            scale = context.preferences.system.ui_scale or 1.0
            return context.window_manager.invoke_props_dialog(self, width=int(560 * scale), confirm_text="Open",
                                                              title="Open in Codex")
        return self.execute(context)

    def draw(self, context):
        layout = self.layout
        files, bridge_off, offer = self._needs(project.project_root())
        if files:
            layout.prop(self, "add_files")
            col = layout.column(align=True)
            col.active = False
            col.label(text=connect_text(files), icon="BLANK1")
            col.label(text="Files the project already has stay as they are.", icon="BLANK1")
        if offer:
            col = layout.column(align=True)
            col.active = self.add_files
            col.prop(self, "move_instructions")
            hint = col.column(align=True)
            hint.active = False
            hint.label(text="This game's instructions are in CLAUDE.md, which only Claude Code reads. Moved, both",
                       icon="BLANK1")
            hint.label(text="agents read AGENTS.md and CLAUDE.md points to it. Unticked, Codex gets a copy.",
                       icon="BLANK1")
        if bridge_off:
            layout.prop(self, "start_bridge")
            _muted(layout).label(text="You watch it build the game here, and can step in any time", icon="BLANK1")
        layout.separator()
        col = layout.column(align=True)
        col.active = False
        for line in (*SIGN_IN_LINES, *TRUST_LINES):
            col.label(text=line, icon="BLANK1")

    def execute(self, context):
        root = project.project_root()
        program = codex_path(refresh=True)
        if program is None:
            self.report({"ERROR"}, "Codex is not installed: GameStudio > Open in Codex installs it")
            return {"CANCELLED"}
        if self.add_files:
            try:
                notes = connect(root, move=self.move_instructions)
            except (OSError, ValueError, CodexError) as exc:
                self.report({"ERROR"}, f"Could not connect Codex to the project: {exc}")
                return {"CANCELLED"}
            for note in notes:
                self.report({"INFO"}, note)
        if self.start_bridge:
            problem = cc.ensure_bridge()
            if problem:
                self.report({"WARNING"}, problem)
        # The MCP server Codex starts needs Node.js and the project's packages: GameStudio sets them up
        # first when they are missing, and Codex opens once they are ready.
        show_prompts = self.options.is_invoke and not bpy.app.background
        state = environment.ensure_ready(root, CODEX.purpose, then=_open_when_ready(root, program, show_prompts))
        if state in (environment.STARTED, environment.ASKING):
            self.report({"INFO"}, "Setting up GameStudio first (see the status bar); Codex opens when it is ready")
            return {"FINISHED"}
        if state != environment.READY:
            self.report({"ERROR"}, state)
            return {"CANCELLED"}
        try:
            launch = _launch(root, program)
        except ClaudeCodeError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        self.report({"INFO"}, f"Codex is starting in {launch.label}")
        if show_prompts:
            _show_after_launch(context.window)
        return {"FINISHED"}


class GAMESTUDIO_OT_codex_started(bpy.types.Operator):
    bl_idname = "gamestudio.codex_started"
    bl_label = "Codex Is Starting"
    bl_description = "What Codex asks the first time, and what to say to it first: starter prompts to copy"
    bl_options = {"INTERNAL"}

    def invoke(self, context, event):
        scale = context.preferences.system.ui_scale or 1.0
        return context.window_manager.invoke_props_dialog(self, width=int(640 * scale), title=self.bl_label,
                                                          confirm_text="Done")

    def draw(self, context):
        layout = self.layout
        col = layout.column(align=True)
        col.active = False
        col.label(text="The first time, Codex asks you to sign in: Sign in with ChatGPT, or use an OpenAI API key.")
        col.label(text="It also asks whether to trust this folder: choose Trust and continue, so it loads "
                       "GameStudio's tools.")
        layout.label(text="Then tell it what to make. Copy a prompt and paste it into Codex:")
        cc.draw_prompts(layout)
        _muted(layout).label(text="The status bar shows \"Codex connected\" once it works in Blender.", icon="INFO")

    def execute(self, context):
        return {"FINISHED"}


def _expected_location() -> str:
    """Where the standalone installer puts codex, for the dialog."""
    return "your user folder (AppData\\Local\\Programs\\OpenAI\\Codex)" if _IS_WINDOWS else "~/.local/bin"


class GAMESTUDIO_OT_codex_install(bpy.types.Operator):
    bl_idname = "gamestudio.codex_install"
    bl_label = "Install Codex"
    bl_description = ("Install Codex, OpenAI's coding agent, with OpenAI's official installer: about 160 MB, for "
                      "your user only, no administrator rights")

    open_after: BoolProperty(options={"HIDDEN", "SKIP_SAVE"})  # Open in Codex goes on once it is installed
    now: BoolProperty(options={"HIDDEN", "SKIP_SAVE"})         # Try Again: no dialog

    def invoke(self, context, event):
        if INSTALLER.running():
            if self.open_after:
                INSTALLER.then(_open_after_install)
            self.report({"INFO"}, "Codex is being installed: see the status bar")
            return {"FINISHED"}
        found = codex_path(refresh=True)
        if found is not None:
            self.report({"INFO"}, f"Codex is installed: {cc.display_path(found)}")
            if self.open_after:
                _open_after_install(True)
            return {"FINISHED"}
        if self.now:
            return self.execute(context)
        if install_problem():
            confirm = "Open the Installation Guide"
        elif not environment.online():
            confirm = "Allow Online Access and Install"
        else:
            confirm = f"Install Codex (about {install_size_mb()} MB)"
        scale = context.preferences.system.ui_scale or 1.0
        return context.window_manager.invoke_props_dialog(self, width=int(600 * scale), title=self.bl_label,
                                                          confirm_text=confirm)

    def draw(self, context):
        from .ui import wrap_label

        layout = self.layout
        layout.label(text="Codex, OpenAI's coding agent that builds your game, is not on this computer yet.",
                     icon="INFO")
        problem = install_problem()
        if problem:
            wrap_label(layout, problem, icon="ERROR", width=90)
            layout.label(text="Or install it by hand: open a terminal, paste one of these commands, then come back:")
            col = layout.column(align=True)
            for index, (shell, command) in enumerate(install_commands()):
                row = col.row(align=True)
                row.operator("gamestudio.copy_codex_install_command", text="", icon="COPYDOWN").index = index
                row.label(text=f"{shell}:  {command}")
        else:
            col = layout.column(align=True)
            col.label(text=f"GameStudio installs it for you with OpenAI's official installer "
                           f"(about {install_size_mb()} MB).", icon="IMPORT")
            _muted(col).label(text=f"It goes in {_expected_location()}: no administrator rights.", icon="BLANK1")
            _muted(col).label(text="It adds that folder to your PATH, so codex also starts in your own terminals.",
                              icon="BLANK1")
            _muted(col).label(text=f"The installer: {install_commands()[0][1]}", icon="BLANK1")
            if not environment.online():
                layout.label(text="Blender's online access is off: Install turns it on (Preferences > System > "
                                  "Network)", icon="ERROR")
        layout.separator()
        col = layout.column(align=True)
        for line in SIGN_IN_LINES:
            _muted(col).label(text=line, icon="BLANK1")
        row = layout.row()
        row.operator("wm.url_open", text="Installation Guide", icon="URL").url = DOCS_URL

    def execute(self, context):
        if codex_path(refresh=True) is not None:
            if self.open_after:
                _open_after_install(True)
            return {"FINISHED"}
        if install_problem():
            cc._open_url(DOCS_URL)  # the guided path: this computer cannot run the installer
            return {"FINISHED"}
        if not environment.online():
            cc._allow_online(context)
            if not environment.online():
                self.report({"ERROR"}, "Blender's online access is off (it was started with --offline-mode), so "
                                       "Codex cannot be downloaded")
                return {"CANCELLED"}
        problem = start_install(_open_after_install if self.open_after else None)
        if problem:
            self.report({"ERROR"}, problem)
            return {"CANCELLED"}
        self.report({"INFO"}, f"Installing Codex (about {install_size_mb()} MB): see the status bar"
                              + ("; it opens in the project when it is ready" if self.open_after else ""))
        return {"FINISHED"}


class GAMESTUDIO_OT_codex_install_cancel(bpy.types.Operator):
    bl_idname = "gamestudio.codex_install_cancel"
    bl_label = "Cancel Installing Codex"
    bl_description = "Stop Codex's installer"

    @classmethod
    def poll(cls, context):
        return INSTALLER.running()

    def execute(self, context):
        INSTALLER.cancel()
        return {"FINISHED"}


class GAMESTUDIO_OT_copy_codex_install_command(cc._CopyOperator, bpy.types.Operator):
    bl_idname = "gamestudio.copy_codex_install_command"
    bl_label = "Copy Install Command"
    bl_description = "Copy a command that installs Codex"

    @classmethod
    def entries(cls):
        return install_commands()


CLASSES = (GAMESTUDIO_OT_open_codex, GAMESTUDIO_OT_codex_started, GAMESTUDIO_OT_codex_install,
           GAMESTUDIO_OT_codex_install_cancel, GAMESTUDIO_OT_copy_codex_install_command)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.STATUSBAR_HT_header.append(draw_install_status)
    if draw_install_status not in cc.INSTALL_STATUS_DRAWS:
        cc.INSTALL_STATUS_DRAWS.append(draw_install_status)
    if INSTALLER not in cc.INSTALLERS:
        cc.INSTALLERS.append(INSTALLER)
    if _on_load_post not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_on_load_post)


def unregister():
    if bpy.app.timers.is_registered(_install_tick):
        bpy.app.timers.unregister(_install_tick)
    INSTALLER.cancel()
    if _on_load_post in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_on_load_post)
    if draw_install_status in cc.INSTALL_STATUS_DRAWS:
        cc.INSTALL_STATUS_DRAWS.remove(draw_install_status)
    if INSTALLER in cc.INSTALLERS:
        cc.INSTALLERS.remove(INSTALLER)
    bpy.types.STATUSBAR_HT_header.remove(draw_install_status)
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
