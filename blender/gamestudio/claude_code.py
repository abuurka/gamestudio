# SPDX-License-Identifier: GPL-3.0-or-later
"""Open in Claude Code: the bridge between Blender and the agent.

The person starts Claude Code in the project folder, watches Blender update and plays the result.
This module:

* finds the `claude` CLI: on PATH, then where the installers put it (the native installer:
  ~/.local/bin/claude, %USERPROFILE%\\.local\\bin\\claude.exe on Windows; Homebrew, WinGet, npm's
  global folders, ~/.claude/local). Blender started from a desktop shortcut often has an older PATH
  than a new terminal, so the folders matter;
* installs it when it is missing, after one click: Anthropic's official native installer
  (INSTALL_COMMANDS, the command code.claude.com/docs/en/setup gives) runs in the background with
  progress in the status bar, and Open in Claude Code goes on when `claude` is there;
* opens a terminal in the project running claude: a small launcher script in the project's
  .gamestudio/ (cd to the project, GameStudio's Node.js first on PATH, then claude) that Windows
  Terminal or cmd.exe, Terminal on macOS (`open`, which needs no Automation permission) or
  x-terminal-emulator / gnome-terminal / konsole on Linux run, or the command in the preferences;
* keeps the project's .mcp.json working wherever Claude Code starts (its own terminal, VS Code,
  the desktop app): the MCP server's script in the project's own node_modules, and `node` only
  when a Node.js 22.12+ is on the PATH every program gets, else the absolute path of the Node.js
  GameStudio uses. It is checked when a project's scene loads, after GameStudio's setup and before
  Claude Code opens, and written only when something changed;
* checks that the project has what Claude Code reads (.mcp.json, CLAUDE.md, .claude/) and adds
  the missing ones from the project's template (gamestudio.json "template"; the starter for older
  projects), and that the agent bridge runs;
* keeps an older project's agent files current when its scene loads (refresh_agent_files): the
  built-in skills its template gained since, and the ones nobody edited at GameStudio's version
  (core/skills.py), and a short note in AGENTS.md for each section the templates gained;
* offers starter prompts to paste, and shows the bridge (listening, Claude Code connected) in the
  GameStudio menu and the Project tab (the status bar and the top bar show it with what the agent
  does: activity_ui.status_item).

Nothing here runs a terminal, claude or its installer by itself: only Open in Claude Code and
Install Claude Code do, when the person clicks them.

Codex (codex.py) is the other coding agent GameStudio opens. It shares what is not about one agent:
the installer job (_Installer, which codex.py subclasses), the launcher script and the terminal
(terminal_command and open_terminal take an Agent), the bridge and its status, and the "Coding agent"
preference (preferred_agent) that the launcher, the welcome and the top bar follow.
"""

import copy
import json
import ntpath
import os
import pathlib
import posixpath
import re
import shlex
import shutil
import subprocess
import sys
import threading
import time
from typing import NamedTuple

import bpy
from bpy.app.handlers import persistent
from bpy.props import BoolProperty, IntProperty

from . import environment, projects
from .core import agentfiles, code_editor, project, skills, toolchain
from .core.log import get_logger
from .core.util import any_window, refresh_status_bar, tag_redraw
from .prefs import get_prefs

log = get_logger(__name__)

DOCS_URL = "https://code.claude.com/docs/en/setup"
# The official install commands (code.claude.com/docs/en/setup, "Native Install"). Install Claude
# Code runs the first one of the platform; the dialog shows them for installing by hand.
INSTALL_COMMANDS = {
    "windows": (("PowerShell", "irm https://claude.ai/install.ps1 | iex"),
                ("Command Prompt",
                 "curl -fsSL https://claude.ai/install.cmd -o install.cmd && install.cmd && del install.cmd")),
    "macos": (("Terminal", "curl -fsSL https://claude.ai/install.sh | bash"),),
    "linux": (("Terminal", "curl -fsSL https://claude.ai/install.sh | bash"),),
}
# About how much the installer downloads: the claude binary of the platform (manifest.json of the
# release, 240-260 MB in 2026).
INSTALL_MB = {"windows": 260, "macos": 250, "linux": 260}
INSTALL_LOG = "claude-code-install.log"     # in GameStudio's user folder
SHOW_RESULT_FOR = 120.0                     # the status bar shows a finished install this long
# What Claude Code reads in a project. AGENTS.md holds the instructions (CLAUDE.md imports it): it is
# missing for Claude Code only while CLAUDE.md is missing or imports it (missing_agent_files).
AGENT_FILES = (".mcp.json", "AGENTS.md", "CLAUDE.md", ".claude")
AGENT_TEMPLATE = "starter"
MCP_PACKAGE = "@gamestudio/mcp"
MCP_ENTRY = "node_modules/@gamestudio/mcp/bin/gamestudio-mcp.js"
MCP_SERVER = "gamestudio"                   # its name in .mcp.json
# The launcher script Open in Claude Code writes into the project's .gamestudio/ and runs.
SCRIPT_SUFFIXES = {"windows": ".cmd", "macos": ".command", "linux": ".sh"}
LAUNCH_SCRIPTS = {platform: "claude-code" + suffix for platform, suffix in SCRIPT_SUFFIXES.items()}
LAUNCH_LOG = "claude-code-terminal.log"     # .gamestudio/: what the terminal program said, when it failed


class Agent(NamedTuple):
    """A coding agent GameStudio opens in a terminal."""
    key: str           # the "Coding agent" preference's value
    label: str         # "Claude Code"
    short: str         # in the status bar: "Claude"
    program: str       # the CLI: "claude"
    script: str        # the launcher script in .gamestudio/, without its suffix
    open_op: str       # Open in ...
    install_op: str    # Install ...
    purpose: str       # environment.ensure_ready's purpose


CLAUDE = Agent("CLAUDE_CODE", "Claude Code", "Claude", "claude", "claude-code", "gamestudio.open_claude_code",
               "gamestudio.claude_code_install", "claude")
CODEX = Agent("CODEX", "Codex", "Codex", "codex", "codex", "gamestudio.open_codex", "gamestudio.codex_install",
              "codex")
AGENTS = (CLAUDE, CODEX)


def preferred_agent() -> Agent:
    """The agent of the "Coding agent" preference (Claude Code by default): the launcher, the welcome
    and the top bar's agent button open it."""
    key = getattr(get_prefs(), "coding_agent", CLAUDE.key)
    return next((agent for agent in AGENTS if agent.key == key), CLAUDE)


_opened = {"agent": None}   # the agent Open in ... started last in this session


def opened(agent: Agent | None) -> None:
    """Remember the agent that was just opened (None: forget, when the preference changes)."""
    _opened["agent"] = agent
    refresh_status_bar()


def expected_agent() -> Agent:
    """The agent the status bar and the top bar name while none is connected: the one opened last in
    this session (Open in Claude Code or Open in Codex), else the preferred one."""
    return _opened["agent"] or preferred_agent()


# (menu label, prompt): each matches a skill the project ships in .claude/skills/.
STARTER_PROMPTS = (
    ("Make a small game",
     "Make a small game where the player collects gems on a floating island before time runs out."),
    ("Add a pickup",
     "Add a pickup that gives the player a five second speed boost, and place three in the level."),
    ("Make the level prettier",
     "Make the level prettier: a coherent stylized look with materials, lighting and a few props."),
    ("Playtest and fix",
     "Playtest the game, find anything that doesn't work, and fix it."),
)

_DETECT_TTL = 10.0
_IS_WINDOWS = sys.platform == "win32"
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class ClaudeCodeError(RuntimeError):
    pass


def platform_key(platform: str | None = None) -> str:
    """"windows", "macos" or "linux" for a sys.platform value (default: this one)."""
    platform = platform or sys.platform
    if platform.startswith("win"):
        return "windows"
    return "macos" if platform == "darwin" else "linux"


# --- Finding the CLI ------------------------------------------------------------------------


def _npm_prefixes(platform: str, home: str, env: dict) -> list[str]:
    """npm's global prefix when the user moved it: NPM_CONFIG_PREFIX, else `prefix=` in ~/.npmrc."""
    found = [value for key, value in env.items() if key.upper() == "NPM_CONFIG_PREFIX" and value]
    paths = ntpath if platform == "windows" else posixpath
    try:
        with open(paths.join(home, ".npmrc"), encoding="utf-8") as rc:
            text = rc.read(65536)
    except (OSError, UnicodeDecodeError, ValueError):
        text = ""
    for line in text.splitlines():
        match = re.match(r"\s*prefix\s*=\s*(.+?)\s*$", line)
        if match:
            value = match.group(1).strip("\"'")
            if value.startswith("~"):
                value = home + value[1:]
            found.append(value.replace("${HOME}", home).replace("$HOME", home))
    return found


def _nvm_bins(home: str, program: str = "claude") -> list[str]:
    """program in the Node.js versions of nvm (newest first): npm's global bin of each."""
    folder = pathlib.Path(home) / ".nvm" / "versions" / "node"
    try:
        versions = sorted((p for p in folder.iterdir() if p.is_dir()),
                          key=lambda p: toolchain.parse_version(p.name) or (0, 0, 0), reverse=True)
    except OSError:
        return []
    return [posixpath.join(home, ".nvm", "versions", "node", p.name, "bin", program) for p in versions]


def install_locations(platform: str, home: str, env: dict) -> list[str]:
    """Where the installers put `claude` (platform is a platform_key()), most likely first: the native
    installer, the older local install (~/.claude/local), WinGet / Homebrew / the apt and dnf
    packages, then npm's global folders (its default, NPM_CONFIG_PREFIX or `prefix=` in ~/.npmrc,
    ~/.npm-global, Volta, nvm). A Blender started from the Dock or a desktop shortcut does not have
    the shell's PATH, so these are looked at after PATH. The Claude desktop app's claude.exe
    (%LOCALAPPDATA%\\AnthropicClaude) is not the CLI and is never one of them."""
    if platform == "windows":
        join = ntpath.join
        found = [join(home, ".local", "bin", "claude.exe"),
                 join(home, ".claude", "local", "claude.exe"), join(home, ".claude", "local", "claude.cmd")]
        if env.get("LOCALAPPDATA"):
            found.append(join(env["LOCALAPPDATA"], "Microsoft", "WinGet", "Links", "claude.exe"))
        if env.get("APPDATA"):
            found.append(join(env["APPDATA"], "npm", "claude.cmd"))
        found += [join(prefix, "claude.cmd") for prefix in _npm_prefixes(platform, home, env)]
        if env.get("LOCALAPPDATA"):
            found.append(join(env["LOCALAPPDATA"], "Volta", "bin", "claude.exe"))
        return list(dict.fromkeys(found))
    join = posixpath.join
    found = [join(home, ".local", "bin", "claude"), join(home, ".claude", "local", "claude")]
    if platform == "macos":
        found += ["/opt/homebrew/bin/claude", "/usr/local/bin/claude"]
    else:
        found += ["/usr/bin/claude", "/usr/local/bin/claude"]
    found += [join(prefix, "bin", "claude") for prefix in _npm_prefixes(platform, home, env)]
    found += [join(home, ".npm-global", "bin", "claude"), join(home, ".volta", "bin", "claude"), *_nvm_bins(home)]
    return list(dict.fromkeys(found))


def find_claude(platform: str | None = None, env: dict | None = None, home: str | None = None,
                which=toolchain.which, isfile=os.path.isfile) -> str | None:
    """The `claude` executable: on PATH, else in an install location, else None."""
    platform = platform_key(platform)
    env = os.environ if env is None else env
    if home is None:
        home = env.get("USERPROFILE") if platform == "windows" else None
        home = home or os.path.expanduser("~")
    found = which("claude")
    if found:
        root, ext = os.path.splitext(str(found))
        return root + ext.lower() if platform == "windows" else str(found)  # PATHEXT spells it .EXE
    return next((path for path in install_locations(platform, home, env) if isfile(path)), None)


_cache = {"time": -_DETECT_TTL, "path": None}


def claude_path(refresh: bool = False) -> str | None:
    """find_claude(), remembered briefly: menus and panels ask on every redraw."""
    now = time.monotonic()
    if refresh or now - _cache["time"] > _DETECT_TTL:
        _cache.update(time=now, path=find_claude())
    return _cache["path"]


def install_commands(platform: str | None = None) -> tuple:
    return INSTALL_COMMANDS[platform_key(platform)]


# --- Installing it --------------------------------------------------------------------------


def install_size_mb(platform: str | None = None) -> int:
    return INSTALL_MB[platform_key(platform)]


def install_argv(platform: str | None = None, which=toolchain.which, isfile=os.path.isfile,
                 env: dict | None = None) -> list[str] | None:
    """The official install command of the platform (INSTALL_COMMANDS, unchanged) as the process that
    runs it: PowerShell on Windows; bash on macOS and Linux, with pipefail so that a failed download
    is a failure, not an empty script. None when this computer cannot run it (install_problem)."""
    platform = platform_key(platform)
    env = os.environ if env is None else env
    command = INSTALL_COMMANDS[platform][0][1]
    if platform == "windows":
        system_root = next((v for k, v in env.items() if k.upper() == "SYSTEMROOT" and v), "C:\\Windows")
        builtin = ntpath.join(system_root, "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
        shell = builtin if isfile(builtin) else (which("powershell.exe") or which("pwsh.exe"))
        return [str(shell), "-NoProfile", "-NonInteractive", "-Command", command] if shell else None
    bash = "/bin/bash" if isfile("/bin/bash") else which("bash")
    if not bash or not which("curl"):
        return None
    return [str(bash), "-o", "pipefail", "-c", command]


def install_problem(platform: str | None = None, which=toolchain.which, isfile=os.path.isfile,
                    env: dict | None = None) -> str:
    """Why GameStudio cannot run the installer here (then the dialog guides the person), or ""."""
    if install_argv(platform, which, isfile, env) is not None:
        return ""
    if platform_key(platform) == "windows":
        return "Windows PowerShell, which the installer needs, was not found on this computer."
    if not which("curl"):
        return ("The installer needs curl, which this computer does not have. Install it with your system's "
                "software manager (Ubuntu and Debian: sudo apt install curl), then try again.")
    return "The installer needs bash, which this computer does not have."


def _download_size(folder: pathlib.Path, since: float) -> int:
    """How far the installer's download is: the biggest file it wrote in ~/.claude/downloads (it deletes
    the file once Claude Code is set up)."""
    best = 0
    try:
        with os.scandir(folder) as entries:
            for entry in entries:
                if entry.name.startswith("claude") and entry.is_file():
                    stat = os.stat(entry.path)  # not entry.stat(): Windows lists a file being written as empty
                    if stat.st_mtime >= since - 5:
                        best = max(best, stat.st_size)
    except OSError:
        return 0
    return best


_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


def _last_error_line(log_path: pathlib.Path) -> str:
    """The installer's most telling line: its first error line, else its last line."""
    try:
        text = log_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    lines = [_ANSI.sub("", line).strip() for line in text.splitlines() if not line.startswith("# ")]
    lines = [line for line in lines if line]
    errors = [line for line in lines if re.search(r"error|failed|could not|cannot|denied|not found|curl: \(",
                                                  line, re.IGNORECASE)]
    return (errors[0] if errors else lines[-1] if lines else "")[:200]


_NETWORK = re.compile(r"Could not resolve host|Failed to connect|Connection (timed out|refused|reset)|"
                      r"remote name could not be resolved|Unable to connect to the remote server|"
                      r"No such host is known|SSL connect error|network is unreachable", re.IGNORECASE)


def _network_failure(log_path: pathlib.Path) -> bool:
    """The installer failed because it could not reach claude.ai (offline, DNS, a proxy)."""
    try:
        return bool(_NETWORK.search(log_path.read_text(encoding="utf-8", errors="replace")))
    except OSError:
        return False


class _Cancelled(Exception):
    pass


class _Installer:
    """Install Claude Code: the official installer on a worker thread, its progress for the UI, and
    what waits for it (Open in Claude Code). One at a time. codex.py's installer is a subclass: the
    class attributes and the methods above "Worker thread" are what differs between agents."""

    label = "Claude Code"
    program = "claude"
    host = "claude.ai"                         # where it downloads from (the offline message)
    install_op = "gamestudio.claude_code_install"
    cancel_op = "gamestudio.claude_code_install_cancel"
    docs_url = DOCS_URL

    def __init__(self):
        self.state = ""            # "", "running", "done", "failed"
        self.message = ""
        self.progress = None       # 0..1 while the download runs
        self.log_path = None
        self.path = None           # claude, once installed
        self.started = 0.0
        self.finished = 0.0
        self.callbacks = []
        self.shown = None          # what the status bar showed last (_tick)
        self._cancel = threading.Event()

    def size_mb(self) -> int:
        return install_size_mb()

    def find(self) -> str | None:
        """The agent's CLI where the installers put it (success, whatever the exit code says)."""
        return find_claude()

    def refresh(self) -> None:
        """Forget what the menus remember about the CLI (after the install)."""
        claude_path(refresh=True)

    def popen(self, argv, **kwargs):
        return _popen_installer(argv, **kwargs)

    def download_size(self, home: pathlib.Path) -> int:
        """How many bytes the installer has downloaded so far (0 when it cannot be told)."""
        return _download_size(home / ".claude" / "downloads", self.started)

    def tick(self):
        """The timer function that follows this installer (one per installer: timers are told apart by it)."""
        return _install_tick

    def running(self) -> bool:
        return self.state == "running"

    def status_text(self) -> str:
        if self.running() and self.progress is not None:
            return f"{self.message} ({int(self.progress * 100)}%)"
        return self.message

    def then(self, callback) -> None:
        """callback(ok, message) when the running install ends."""
        self.callbacks.append(callback)

    def cancel(self) -> None:
        self._cancel.set()

    def start(self, argv: list[str], log_path: pathlib.Path, home: pathlib.Path, env: dict, then=None) -> bool:
        if self.running():
            if then is not None:
                self.then(then)
            return False
        self.__init__()
        self.state, self.started, self.log_path = "running", time.time(), pathlib.Path(log_path)
        self.message = f"Downloading {self.label} (about {self.size_mb()} MB)"
        if then is not None:
            self.callbacks.append(then)
        threading.Thread(target=self._work, args=(argv, home, env), daemon=True,
                         name=f"gamestudio-{self.program}-install").start()
        tick = self.tick()
        if not bpy.app.timers.is_registered(tick):
            bpy.app.timers.register(tick, first_interval=0.25, persistent=True)
        tag_redraw()
        return True

    # Worker thread ------------------------------------------------------------------------

    def _work(self, argv, home: pathlib.Path, env: dict) -> None:
        try:
            code = self._run(argv, home, env)
            # Success is the CLI where the installers put it, whatever the exit code says.
            found = self.find()
            if found is None:
                raise RuntimeError(self._failure(code))
            if code != 0:
                log.warning("%s's installer ended with exit code %s, but %s is there", self.label, code, found)
            self.path, ok, message = found, True, f"{self.label} is installed ({display_path(found)})"
        except _Cancelled:
            ok, message = False, "Cancelled"
        except Exception as exc:  # report, never leave the install running
            ok, message = False, str(exc)
        self.message, self.progress, self.finished = message, None, time.time()
        self.state = "done" if ok else "failed"

    def _failure(self, code: int) -> str:
        if code != 0 and _network_failure(self.log_path):
            return (f"{self.label} could not be downloaded: {self.host} could not be reached. Check the internet "
                    "connection, then Try Again")
        last = _last_error_line(self.log_path)
        if code != 0:
            return f"{self.label}'s installer failed (exit code {code})" + (f": {last}" if last else "")
        return (f"{self.label}'s installer finished, but GameStudio cannot find {self.program}"
                + (f": {last}" if last else ""))

    def _run(self, argv, home: pathlib.Path, env: dict) -> int:
        """Run the installer to its end, with its progress; returns its exit code."""
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        expected = self.size_mb() * 1e6
        label = self.label
        with open(self.log_path, "w", encoding="utf-8", errors="replace") as out:
            out.write(f"# {subprocess.list2cmdline(argv) if _IS_WINDOWS else shlex.join(argv)}\n")
            out.flush()
            kwargs = {"creationflags": _NO_WINDOW} if _IS_WINDOWS else {"start_new_session": True}
            try:
                proc = self.popen(argv, cwd=str(home), env=env, stdin=subprocess.DEVNULL, stdout=out,
                                  stderr=subprocess.STDOUT, **kwargs)
            except OSError as exc:
                raise RuntimeError(f"Could not run {label}'s installer ({argv[0]}): {exc}") from exc
            begun, last_size, still = time.monotonic(), 0, time.monotonic()
            while proc.poll() is None:
                if self._cancel.is_set():
                    from .core.devserver import kill_tree

                    kill_tree(proc.pid)
                    raise _Cancelled()
                now = time.monotonic()
                size = self.download_size(home)
                if size > last_size:
                    last_size, still = size, now
                seconds = int(now - begun)
                if size and (now - still < 3.0 or size < expected * 0.5):
                    self.message = f"Downloading {label}: {size / 1e6:.0f} of about {expected / 1e6:.0f} MB"
                    self.progress = min(size / expected, 0.99)
                elif last_size:  # downloaded (the installer removes the file once it is set up)
                    self.message, self.progress = f"Setting up {label}... {seconds} s", None
                else:
                    self.message = f"Downloading {label} (about {expected / 1e6:.0f} MB)... {seconds} s"
                time.sleep(0.25)
        return proc.returncode


def _popen_installer(argv, **kwargs):
    """The installer's process (tests replace this: they never run the real installer)."""
    return subprocess.Popen([str(a) for a in argv], **kwargs)


INSTALLER = _Installer()


def install_log_path() -> pathlib.Path:
    return environment.user_dir() / INSTALL_LOG


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
    env = dict(os.environ)
    if not INSTALLER.start(argv, install_log_path(), home, env, then):
        return "Claude Code's installer is already running"
    log.info("Installing Claude Code: %s", argv)
    return ""


def _install_tick():
    return _tick(INSTALLER)


def _tick(installer: _Installer):
    """An installer's timer: the status bar while it runs; its result, then what waited for it."""
    item = install_status(installer)
    if item != installer.shown:
        installer.shown = item
        refresh_status_bar()  # the status bar is no area: tag_redraw does not reach it
    if installer.running():
        tag_redraw(area_types=("VIEW_3D", "PROPERTIES", "TOPBAR", "PREFERENCES"), region_type=None)
        return 0.25
    callbacks, installer.callbacks = installer.callbacks, []
    ok = installer.state == "done"
    installer.refresh()
    if ok:
        log.info(installer.message)
        environment.notify(installer.message, "INFO")
    elif installer.message != "Cancelled":
        log.error(installer.message)
        _show_install_failure(installer.message, installer)
    for callback in callbacks:
        try:
            callback(ok, installer.message)
        except Exception:  # noqa: BLE001 - one broken continuation must not stop the others
            log.exception("A continuation after installing %s failed", installer.label)
    tag_redraw(area_types=("VIEW_3D", "PROPERTIES", "TOPBAR", "STATUSBAR", "PREFERENCES"), region_type=None)
    refresh_status_bar()
    return None


def _show_install_failure(message: str, installer: _Installer | None = None) -> None:
    """A popup saying why, with Try Again, Show Log and the installation guide."""
    installer = installer or INSTALLER
    if bpy.app.background:
        return

    def show():
        window = any_window()
        if window is None:
            return None

        def draw(menu, _context):
            from .ui import wrap_label

            layout = menu.layout
            wrap_label(layout, message, width=70)
            row = layout.row()
            row.operator(installer.install_op, text="Try Again", icon="FILE_REFRESH").now = True
            if installer.log_path is not None and pathlib.Path(installer.log_path).is_file():
                row.operator("gamestudio.open_path", text="Show Log", icon="TEXT").filepath = str(installer.log_path)
            row.operator("wm.url_open", text="Installation Guide", icon="URL").url = installer.docs_url

        try:
            with bpy.context.temp_override(window=window, screen=window.screen):
                bpy.context.window_manager.popup_menu(draw, title=f"{installer.label} was not installed",
                                                      icon="ERROR")
        except RuntimeError as exc:
            log.warning("Could not show the install failure: %s", exc)
        return None

    bpy.app.timers.register(show, first_interval=0.1)


def install_status(installer: _Installer | None = None) -> tuple[str, str, bool] | None:
    """(text, icon, alert) for the status bar while an agent installs and a while after."""
    installer = installer or INSTALLER
    if installer.running():
        return installer.status_text(), "SORTTIME", False  # "Downloading Claude Code: 64 of about 260 MB (24%)"
    if (installer.state and time.time() - installer.finished < SHOW_RESULT_FOR
            and installer.message != "Cancelled"):
        if installer.state == "failed":
            return f"{installer.label} was not installed", "ERROR", True
        return f"{installer.label} is installed", "CHECKMARK", False
    return None


def draw_installer_status(layout, installer: _Installer) -> None:
    """An installer's item in the status bar: the install's progress, Cancel, or Try Again after a failure."""
    item = install_status(installer)
    if item is None:
        return
    text, icon, alert = item
    row = layout.row(align=True)
    row.alert = alert
    row.label(text=text, icon=icon)
    if installer.running():
        row.operator(installer.cancel_op, text="", icon="X", emboss=False)
    elif installer.state == "failed":
        row.operator(installer.install_op, text="Try Again", icon="FILE_REFRESH").now = True


def draw_install_status(self, context) -> None:
    """Appended to the status bar: Claude Code's install (draw_installer_status)."""
    draw_installer_status(self.layout, INSTALLER)


# The status bar functions of the agents' installers (codex.py adds its own): _status_tick keeps them
# in the status bar and redraws it when what they show changes.
INSTALL_STATUS_DRAWS = [draw_install_status]
INSTALLERS = [INSTALLER]


# --- The terminal ---------------------------------------------------------------------------


class Launch(NamedTuple):
    label: str                 # "Windows Terminal"
    argv: list | str           # a str is a Windows command line, passed as is (cmd.exe)
    cwd: str
    console: bool = False      # Windows: give the program a new console window of its own
    script: str = ""           # the launcher script it runs, written into .gamestudio/ first
    script_text: str = ""
    wait: bool = False         # it hands over to the terminal and exits (macOS `open`): wait, check its exit code


def _wt_arg(text: str) -> str:
    # wt.exe splits its command line into tabs at ";" (docs: "escape it with a backslash").
    return text.replace(";", "\\;")


def _windows_terminal(which, env: dict) -> str | None:
    found = which("wt.exe") or which("wt")
    if found:
        return str(found)
    if env.get("LOCALAPPDATA"):
        alias = ntpath.join(env["LOCALAPPDATA"], "Microsoft", "WindowsApps", "wt.exe")
        if os.path.isfile(alias):
            return alias
    return None


def _cmd_path(path: str, env: dict) -> str:
    """A path for a .cmd script: under %LOCALAPPDATA%, %APPDATA% or %USERPROFILE% through the variable,
    so a user name outside the console's code page needs no conversion."""
    for name in ("LOCALAPPDATA", "APPDATA", "USERPROFILE"):
        value = next((v for k, v in env.items() if k.upper() == name and v), "").rstrip("\\/")
        if value and path.lower().startswith(value.lower() + "\\"):
            return f"%{name}%" + path[len(value):]
    return path


def launch_script(platform: str | None, claude: str, path_prepend: str = "", env: dict | None = None,
                  agent: Agent | None = None) -> str:
    """The launcher script Open in Claude Code (or Open in Codex: agent) runs: it goes to the project
    (the folder above the script's), puts GameStudio's Node.js first on PATH (path_prepend) and runs
    the agent's CLI (claude). On macOS and Linux the window stays open when it fails, so its message
    can be read; Command Prompt stays open anyway (/k)."""
    platform = platform_key(platform)
    env = {} if env is None else env
    label = (agent or CLAUDE).label
    if platform == "windows":
        # cmd.exe looks for a program in the current folder before PATH: npm's claude.cmd starts a bare
        # `node`, which a node.exe in the project would replace. This turns that off for the window.
        lines = ["@echo off", f"rem GameStudio's Open in {label} writes this file: {label} in this project.",
                 f"title {label}", 'cd /d "%~dp0.."', 'set "NoDefaultCurrentDirectoryInExePath=1"']
        if path_prepend:
            lines.append(f'set "PATH={_cmd_path(path_prepend, env)};%PATH%"')
        program = f'"{_cmd_path(claude, env)}"'
        lines.append(f"call {program}" if claude.lower().endswith((".cmd", ".bat")) else program)
        return "\r\n".join(lines) + "\r\n"
    lines = ["#!/bin/sh", f"# GameStudio's Open in {label} writes this file: {label} in this project.",
             'cd "$(dirname "$0")/.." || exit 1']
    if path_prepend:
        lines.append(f'PATH={shlex.quote(path_prepend)}:"$PATH"; export PATH')
    lines.append(f"{shlex.quote(claude)} || {{ echo; echo '{label} stopped with an error (see above). "
                 "Press Enter to close this window.'; read -r _; }")
    return "\n".join(lines) + "\n"


def _script_bytes(windows: bool, text: str) -> bytes:
    """cmd.exe reads a .cmd in the console's code page: plain ASCII when it can be, else UTF-8 after
    `chcp 65001`."""
    if not windows:
        return text.encode("utf-8")
    try:
        return text.encode("ascii")
    except UnicodeEncodeError:
        return ("@chcp 65001 >nul\r\n" + text).encode("utf-8")


def _mac_app(template: str) -> str:
    """A terminal app named in the preferences on macOS ("iTerm", "Warp", "/Applications/iTerm.app"):
    Open in Claude Code opens the launcher script with it (open -a)."""
    text = template.strip()
    if not text or "{" in text:
        return ""
    try:
        args = code_editor.split_command(text)
    except code_editor.CodeEditorError:
        return ""
    if len(args) != 1 or ("/" in args[0] and not args[0].rstrip("/").endswith(".app")):
        return ""
    return args[0]


LINUX_TERMINALS = ("x-terminal-emulator", "gnome-terminal", "konsole", "xterm")
MAC_OPEN = "/usr/bin/open"


def terminal_command(project_dir, claude: str, platform: str | None = None, template: str = "",
                     which=toolchain.which, env: dict | None = None, path_prepend: str = "",
                     agent: Agent | None = None) -> Launch:
    """How to open a terminal in project_dir running claude (the CLI of agent: Claude Code by
    default). Raises ClaudeCodeError.

    The terminal runs the launcher script (launch_script, written to <project>/.gamestudio/ by
    open_terminal, named after the agent: claude-code.cmd, codex.cmd...), so it starts in the project
    with GameStudio's Node.js (path_prepend) first on PATH whatever environment the terminal gives its
    programs. template is the custom command from the preferences, with {project}, {agent} (or
    {claude}: the agent's CLI) and {script} placeholders; on macOS it can also name a terminal app
    ("iTerm", "Warp"). macOS opens Terminal with `open`, which needs no Automation permission
    (osascript does, and fails silently without it)."""
    platform = platform_key(platform)
    env = os.environ if env is None else env
    agent = agent or CLAUDE
    paths = ntpath if platform == "windows" else posixpath
    folder = paths.normpath(str(project_dir))
    claude = str(claude)
    name = agent.script + SCRIPT_SUFFIXES[platform]
    script = paths.join(folder, project.GS_DIRNAME, name)
    text = launch_script(platform, claude, path_prepend, env, agent)

    def launch(label: str, argv, **kwargs) -> Launch:
        return Launch(label, argv, folder, script=script, script_text=text, **kwargs)

    if template.strip():
        app = _mac_app(template) if platform == "macos" else ""
        if app:
            label = posixpath.basename(app.rstrip("/")).removesuffix(".app") or app
            return launch(label, [MAC_OPEN, "-a", app, script], wait=True)
        try:
            args = code_editor.split_command(template)
        except code_editor.CodeEditorError as exc:
            raise ClaudeCodeError(str(exc).replace("editor command", "terminal command")) from exc
        argv = [arg.replace("{project}", folder).replace("{claude}", claude).replace("{agent}", claude)
                .replace("{script}", script) for arg in args]
        return launch("Custom command", argv)

    if platform == "windows":
        # The script by its path inside the project (the working directory): nothing for cmd.exe or
        # wt.exe to parse, whatever characters the project's path has.
        relative = f"{project.GS_DIRNAME}\\{name}"
        cmd = toolchain.system_tool("cmd.exe", env)  # by its path: a cmd.exe in the project would run instead
        wt = _windows_terminal(which, env)
        if wt:
            return launch("Windows Terminal", [wt, "-d", _wt_arg(folder), cmd, "/k", relative])
        program = f'"{cmd}"' if " " in cmd else cmd
        return launch("Command Prompt", f"{program} /k {relative}", console=True)

    if platform == "macos":
        return launch("Terminal", [MAC_OPEN, "-a", "Terminal", script], wait=True)

    relative = f"./{project.GS_DIRNAME}/{name}"
    for term in LINUX_TERMINALS:
        found = which(term)
        if not found:
            continue
        if term == "gnome-terminal":
            return launch("GNOME Terminal", [found, f"--working-directory={folder}", "--", script])
        if term == "konsole":
            return launch("Konsole", [found, "--workdir", folder, "-e", script])
        return launch(term, [found, "-e", relative])
    raise ClaudeCodeError("No terminal found (x-terminal-emulator, gnome-terminal or konsole). Set a terminal "
                          "command in the GameStudio preferences (Coding agent)")


def terminal_label() -> str:
    """The terminal Open in Claude Code would use here, for the preferences."""
    try:
        return terminal_command(".", "claude", template=_template()).label
    except ClaudeCodeError:
        return ""


def _template() -> str:
    return getattr(get_prefs(), "claude_terminal_command", "") or ""


def terminal_env() -> tuple[dict, str]:
    """(environment, folder put first on PATH or ""): GameStudio's Node.js when the system has none."""
    folder = toolchain.node_dir(environment.__file__)
    bin_dir = "" if folder is None else str(folder if _IS_WINDOWS else folder / "bin")
    return toolchain.child_env(start=environment.__file__), bin_dir


def write_launch_script(launch: Launch) -> None:
    """Write the launcher script (only when it changed) and make it executable. Raises OSError."""
    if not launch.script:
        return
    path = pathlib.Path(launch.script)
    data = _script_bytes(path.suffix.lower() == ".cmd", launch.script_text)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.is_file() or path.read_bytes() != data:
        path.write_bytes(data)
    if not _IS_WINDOWS:
        os.chmod(path, 0o755)


def _launch_log(launch: Launch) -> pathlib.Path:
    return pathlib.Path(launch.cwd) / project.GS_DIRNAME / LAUNCH_LOG


def _spawn(launch: Launch, env: dict):
    """Start the terminal: the process to watch, or None. Raises OSError, ClaudeCodeError or
    subprocess.TimeoutExpired."""
    kwargs = {"cwd": launch.cwd, "env": env, "close_fds": True}
    if launch.wait:
        done = subprocess.run(launch.argv, stdin=subprocess.DEVNULL, capture_output=True, text=True, errors="replace",
                              timeout=30, **kwargs)
        if done.returncode != 0:
            said = (done.stderr or done.stdout or "").strip().splitlines()
            raise ClaudeCodeError(f"Could not open {launch.label}: "
                                  + (said[-1] if said else f"{launch.argv[0]} exited with code {done.returncode}"))
        return None
    if _IS_WINDOWS:
        # A console program needs its own window and console handles; the others draw their own.
        kwargs["creationflags"] = subprocess.CREATE_NEW_CONSOLE if launch.console else subprocess.CREATE_NO_WINDOW
    else:
        kwargs["start_new_session"] = True
    if launch.console:
        return subprocess.Popen(launch.argv, **kwargs)
    log_path = _launch_log(launch)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "w", encoding="utf-8", errors="replace") as out:
        return subprocess.Popen(launch.argv, stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT, **kwargs)


def _watch_launch(proc, launch: Launch, delay: float = 3.0) -> None:
    """Say so when the terminal program fails right away (it exits with an error instead of opening)."""
    def check():
        code = proc.poll()
        if code not in (None, 0):
            said = _last_error_line(_launch_log(launch))
            environment.notify(f"{launch.label} did not open (exit code {code}){': ' + said if said else ''}. "
                               "A terminal command can be set in GameStudio's preferences (Coding agent)", "ERROR")
        return None

    if not bpy.app.background:
        bpy.app.timers.register(check, first_interval=delay)


def open_terminal(root, claude: str, agent: Agent | None = None) -> Launch:
    """Open the terminal running claude (the CLI of agent: Claude Code by default) in the project.
    Raises ClaudeCodeError."""
    env, node_bin = terminal_env()
    launch = terminal_command(root, claude, template=_template(), env=env, path_prepend=node_bin, agent=agent)
    try:
        write_launch_script(launch)
    except OSError as exc:
        raise ClaudeCodeError(f"Could not write {launch.script}: {exc}") from exc
    try:
        proc = _spawn(launch, env)
    except subprocess.TimeoutExpired as exc:
        raise ClaudeCodeError(f"{launch.label} did not answer") from exc
    except OSError as exc:
        raise ClaudeCodeError(f"Could not open {launch.label}: {exc}") from exc
    if proc is not None:
        _watch_launch(proc, launch)
    log.info("Opened %s in %s: %s", (agent or CLAUDE).label, launch.label, launch.argv)
    return launch


# --- The project's agent files --------------------------------------------------------------


def missing_agent_files(root) -> list[str]:
    """Which of .mcp.json, AGENTS.md, CLAUDE.md and .claude/ the project lacks for Claude Code. AGENTS.md
    only while CLAUDE.md is missing or imports it (@AGENTS.md): instructions that are only in CLAUDE.md
    need nothing more (Open in Codex offers to move them, core/agentfiles.py)."""
    root = pathlib.Path(root)
    missing = [name for name in AGENT_FILES if not (root / name).exists()]
    if "AGENTS.md" in missing and "CLAUDE.md" not in missing:
        try:
            if agentfiles.instructions_state(root) != agentfiles.AGENTS_MISSING:
                missing.remove("AGENTS.md")
        except (OSError, ValueError):  # an unreadable CLAUDE.md: the person's, left alone
            missing.remove("AGENTS.md")
    return missing


def mcp_missing(root) -> bool:
    """True when the project's MCP server package cannot be found by Node."""
    return not toolchain.resolvable(root, MCP_PACKAGE)


def mcp_entry(root) -> str:
    """The MCP server script, relative to the project: in its node_modules, else in the nearest
    parent's (a project inside the GameStudio repository)."""
    root = pathlib.Path(root)
    for folder in (root, *root.parents):
        if (folder / MCP_ENTRY).is_file():
            return pathlib.Path(os.path.relpath(folder / MCP_ENTRY, root)).as_posix()
    return MCP_ENTRY


def agent_template(root) -> str:
    """The template the agent files come from: the one the project was made from (gamestudio.json
    "template"), else the starter (projects made before New Project recorded it)."""
    return projects.project_template(root, default=AGENT_TEMPLATE)


def add_agent_files(root, template: str | None = None, names=None) -> list[str]:
    """Copy the missing agent files from a project template (default: agent_template), composed as
    New Project composes them; never overwrites a file. names: the top-level files and folders to
    add when missing (default: what Claude Code lacks, missing_agent_files). Returns the names added,
    in AGENT_FILES order. Raises OSError or ValueError."""
    root = pathlib.Path(root)
    if names is None:
        missing = set(missing_agent_files(root))
    else:
        missing = {name for name in names if not (root / name).exists()}
    if not missing:
        return []
    name = project.read_config(root).get("name") or root.name
    values = {"name": name, "slug": projects.slugify(name)}
    added = set()
    for rel, sources in projects.template_files(template or agent_template(root)).items():
        top = rel.split("/", 1)[0]
        dst = root / rel
        if top not in missing or dst.exists():
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        if not projects.is_text_file(sources[-1]):
            shutil.copy2(sources[-1], dst)
        else:
            text = projects.render_text(sources, values)
            if rel == ".mcp.json":
                text = text.replace(MCP_ENTRY, mcp_entry(root))
            dst.write_text(text, encoding="utf-8", newline="\n")
        added.add(top)
    return [name for name in AGENT_FILES if name in added] + sorted(added - set(AGENT_FILES))


def ensure_mcp_package(root) -> str:
    """Give an older project the MCP server package: declare it in package.json when it is not.
    GameStudio's setup then links it (environment.ensure_ready, before Claude Code opens). Returns
    what happened, or ""."""
    root = pathlib.Path(root)
    if not mcp_missing(root):
        return ""
    manifest_path = root / "package.json"
    manifest = toolchain.read_manifest(root)
    if not manifest:
        return ""
    if MCP_PACKAGE in toolchain.declared_dependencies(manifest):
        return ""
    packages = projects.runtime_packages()
    if packages is None or not (packages / "mcp" / "package.json").is_file():
        return "This GameStudio has no MCP server package to add"
    manifest.setdefault("devDependencies", {})[MCP_PACKAGE] = "file:" + (packages / "mcp").as_posix()
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8", newline="\n")
    environment.invalidate()
    return MCP_ADDED


MCP_ADDED = "Added the GameStudio MCP server to the project's package.json"


# --- Keeping an older project's agent files current -----------------------------------------
#
# A project keeps the skills and AGENTS.md it was made with, and Open in Claude Code only adds whole
# files and folders that are missing. So when a project's scene loads, refresh_agent_files gives it
# the built-in skills its template has gained since and refreshes the ones nobody edited
# (core/skills.py), and points AGENTS.md to sections the templates gained (agentfiles.SECTION_POINTERS).


def builtin_skills(root, template: str | None = None) -> dict[str, dict[str, bytes]]:
    """The skills of the project's template (default: agent_template), composed with its base and
    filled in as New Project and add_agent_files do: name -> {path in the skill folder: contents}.
    Raises OSError or ValueError."""
    root = pathlib.Path(root)
    name = project.read_config(root).get("name") or root.name
    values = {"name": name, "slug": projects.slugify(name)}
    prefix = skills.SKILLS + "/"
    found = {}
    for rel, sources in projects.template_files(template or agent_template(root)).items():
        if not rel.startswith(prefix):
            continue
        skill, _, inner = rel[len(prefix):].partition("/")
        if not inner:
            continue  # a file of .claude/skills/ itself
        data = (projects.render_text(sources, values).encode("utf-8") if projects.is_text_file(sources[-1])
                else sources[-1].read_bytes())
        found.setdefault(skill, {})[inner] = data
    return {skill: files for skill, files in found.items() if skills.SKILL_FILE in files}


def _kept_by_hand(root: pathlib.Path) -> bool:
    """A template's folder, or one of GameStudio's examples opened where it ships (a development
    checkout): their agent files are kept with GameStudio's own, never changed on open."""
    if (root / projects.TEMPLATE_INFO).is_file():
        return True
    try:
        resolved = root.resolve()
        return any(example.resolve() == resolved for example in projects.examples())
    except OSError:
        return False


def _and(names) -> str:
    names = list(names)
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def agent_files_note(added=(), updated=(), sections=()) -> str:
    """What refresh_agent_files did, in one sentence: "Added the design-ui skill and a Game UI note to
    AGENTS.md", or ""."""
    what = []
    if added:
        what.append(f"the {_and(added)} skill{'s' if len(added) > 1 else ''}")
    if sections:
        what.append(f"{'a ' if len(sections) == 1 else ''}{_and(sections)} note{'s' if len(sections) > 1 else ''} "
                    "to AGENTS.md")
    said = [f"Added {' and '.join(what)}"] if what else []
    if updated:
        skills_text = (f"the {_and(updated)} skill{'s' if len(updated) > 1 else ''}" if len(updated) <= 3
                       else f"{len(updated)} skills")
        said.append(f"{'updated' if said else 'Updated'} {skills_text} to this GameStudio's version")
    return "; ".join(said)


def refresh_agent_files(root) -> str:
    """Keep an older project's agent files current, when one of its scenes loads: the built-in skills
    of its template (core/skills.py: the missing ones added, unless the user deleted them; the ones
    nobody edited follow GameStudio's version; the user's own and edited ones never touched), then
    Codex's copy of them when the project uses Codex, and a short marked note in AGENTS.md for each
    section the templates' AGENTS.md gained that it does not cover (agentfiles.add_section_pointers;
    not where the instructions are only in CLAUDE.md). Only a project with .claude/skills/ gets
    skills; template folders and GameStudio's own examples are left alone. Never raises; returns
    what it did in one sentence (agent_files_note), or ""."""
    root = pathlib.Path(root)
    if _kept_by_hand(root):
        return ""
    result = {"added": [], "updated": []}
    if (root / skills.SKILLS).is_dir():
        try:
            result = skills.update_skills(root, builtin_skills(root))
        except (OSError, ValueError) as exc:
            log.warning("Could not update the skills of %s: %s", root, exc)
        if result.get("adopted"):
            log.info("%s: the skills %s are GameStudio's", root, ", ".join(result["adopted"]))
        if result["added"] or result["updated"]:
            from . import codex

            try:
                if codex.uses_codex(root):
                    codex.mirror_skills(root)
            except OSError as exc:
                log.warning("Could not update %s: %s", root / codex.SKILLS, exc)
    sections = []
    try:
        sections = agentfiles.add_section_pointers(root)
    except (OSError, ValueError) as exc:
        log.warning("Could not add to %s: %s", root / agentfiles.AGENTS, exc)
    return agent_files_note(result["added"], result["updated"], sections)


# --- The project's .mcp.json ----------------------------------------------------------------
#
# Claude Code starts the project's MCP server from .mcp.json, wherever it runs: GameStudio's terminal,
# the person's own terminal, VS Code or the desktop app. `"command": "node"` works only where a
# Node.js is on that program's PATH, so on a computer whose only Node.js is GameStudio's the file
# names that Node.js by its path. The script is the project's own node_modules/@gamestudio/mcp
# (GameStudio's setup links it), or a parent folder's inside the GameStudio repository.


def _is_node(command) -> bool:
    return isinstance(command, str) and re.split(r"[\\/]", command)[-1].lower() in ("node", "node.exe")


def _is_bare(command: str) -> bool:
    return not re.search(r"[\\/]", command)


def _script_index(args) -> int | None:
    for index, arg in enumerate(args):
        if isinstance(arg, str) and arg.replace("\\", "/").endswith("@gamestudio/mcp/bin/gamestudio-mcp.js"):
            return index
    return None


def mcp_server(config) -> dict | None:
    """GameStudio's server in a parsed .mcp.json: "gamestudio", started by node (by name or path) with
    gamestudio-mcp.js; None otherwise (missing, or the person's own way of starting it: left alone)."""
    servers = config.get("mcpServers") if isinstance(config, dict) else None
    server = servers.get(MCP_SERVER) if isinstance(servers, dict) else None
    if not isinstance(server, dict) or not _is_node(server.get("command")):
        return None
    args = server.get("args")
    return server if isinstance(args, list) and _script_index(args) is not None else None


def _under(path: str, folder) -> bool:
    try:
        path, folder = os.path.normcase(os.path.abspath(path)), os.path.normcase(os.path.abspath(str(folder)))
        return os.path.commonpath([path, folder]) == folder
    except ValueError:  # another drive
        return False


def mcp_node_command(start=None) -> str | None:
    """What .mcp.json starts the MCP server with: "node" when a Node.js 22.12+ is on the PATH that
    programs get anyway (Claude Code in its own terminal, VS Code or the desktop app finds it too),
    else the absolute path of the Node.js GameStudio uses (its own download, or GameStudio Editor's);
    None while there is none (GameStudio's setup brings it)."""
    start = start or environment.__file__
    own = [folder for folder in (toolchain.bundled_node_dir(start), toolchain.portable_node_dir()) if folder]
    system = toolchain.system_node()
    if system and not any(_under(system, folder) for folder in own):
        return "node"
    status = toolchain.node_status(start)
    return status["path"] if status["ok"] and status["path"] else None


def mcp_config_changes(root, config, node: str | None, isfile=os.path.isfile) -> dict:
    """What the gamestudio server of a parsed .mcp.json needs to start on this computer, with `node`
    from mcp_node_command(): {"command": ...} and/or {"script": (index in args, path)}; {} when it
    works as it is, or is not GameStudio's (mcp_server).

    The command: `node` becomes `node`'s path when the computer's PATH has no Node.js, and a path that
    does not exist here (a project from another computer, through git) becomes `node` or this
    computer's path; a path that exists stays (the file stays the same from one open to the next).
    The script: one that does not exist becomes the project's (mcp_entry), once that exists."""
    server = mcp_server(config)
    if server is None:
        return {}
    changes = {}
    command = server["command"]
    if _is_bare(command):
        if node and node != "node":
            changes["command"] = node
    elif not isfile(command):
        changes["command"] = node or "node"
    if changes.get("command") == command:
        del changes["command"]
    root = pathlib.Path(root)
    index = _script_index(server["args"])
    current = server["args"][index]
    if not isfile(str(root / current)):
        wanted = mcp_entry(root)
        if wanted != current and isfile(str(root / wanted)):
            changes["script"] = (index, wanted)
    return changes


def _swap_json_string(text: str | None, key: str | None, old: str, new: str) -> str | None:
    """text with the JSON string `old` (the value of "key" when key is given) written as `new`, when it
    is there exactly once; else None."""
    if text is None:
        return None
    prefix = r'("' + re.escape(key) + r'"\s*:\s*)' if key else r"()"
    found = list(re.finditer(prefix + re.escape(json.dumps(old, ensure_ascii=False)), text))
    if len(found) != 1:
        return None
    match = found[0]
    return text[:match.start()] + match.group(1) + json.dumps(new, ensure_ascii=False) + text[match.end():]


def apply_mcp_changes(text: str, config: dict, changes: dict) -> str:
    """.mcp.json's text with the changes (mcp_config_changes) made in place: the rest of the file, its
    formatting and its line endings stay as they are (it is rewritten as a whole only when the
    values cannot be found once each)."""
    wanted = copy.deepcopy(config)
    server = wanted["mcpServers"][MCP_SERVER]
    new = text
    if "command" in changes:
        new = _swap_json_string(new, "command", server["command"], changes["command"])
        server["command"] = changes["command"]
    if "script" in changes:
        index, script = changes["script"]
        new = _swap_json_string(new, None, server["args"][index], script)
        server["args"][index] = script
    try:
        if new is not None and json.loads(new) == wanted:
            return new
    except ValueError:
        pass
    newline = "\r\n" if "\r\n" in text else "\n"
    return json.dumps(wanted, indent=2, ensure_ascii=False).replace("\n", newline) + newline


def _edit_mcp_config(root, compute) -> dict:
    """Apply compute(config) -> changes to the project's .mcp.json; returns the changes written."""
    path = pathlib.Path(root) / ".mcp.json"
    try:
        raw = path.read_bytes()
        text = raw.decode("utf-8-sig")
        config = json.loads(text)
    except (OSError, ValueError):  # missing, unreadable or not JSON: Claude Code would say so itself
        return {}
    if mcp_server(config) is None:
        return {}
    changes = compute(config)
    if changes:
        bom = b"\xef\xbb\xbf" if raw.startswith(b"\xef\xbb\xbf") else b""
        path.write_bytes(bom + apply_mcp_changes(text, config, changes).encode("utf-8"))
        log.info("%s now starts the MCP server with %s", path, changes)
    return changes


_FIND = object()


def sync_mcp_config(root, node=_FIND) -> bool:
    """Make the project's .mcp.json start the MCP server on this computer (mcp_config_changes, with
    mcp_node_command() unless `node` is given). Writes only when something changed. Raises OSError
    when it cannot write; returns True when it wrote."""
    def compute(config):
        return mcp_config_changes(root, config, mcp_node_command() if node is _FIND else node)
    return bool(_edit_mcp_config(root, compute))


def localize_mcp_config(root) -> bool:
    """A project copied out of its folder (an example out of GameStudio's install): a script path that
    climbs into a parent folder (the GameStudio repository's node_modules) becomes the project's own,
    which GameStudio's setup links. Returns True when it wrote."""
    root = pathlib.Path(root)

    def compute(config):
        args = mcp_server(config)["args"]
        index = _script_index(args)
        current = args[index].replace("\\", "/")
        if current != MCP_ENTRY and current.endswith("/" + MCP_ENTRY) and not (root / current).is_file():
            return {"script": (index, MCP_ENTRY)}
        return {}
    return bool(_edit_mcp_config(root, compute))


def portable_mcp_text(text: str) -> str:
    """.mcp.json for a template or another computer: `node` by name and the project's own script."""
    try:
        config = json.loads(text)
    except ValueError:
        return text
    server = mcp_server(config)
    if server is None:
        return text
    changes = {}
    if not _is_bare(server["command"]):
        changes["command"] = "node"
    index = _script_index(server["args"])
    if server["args"][index] != MCP_ENTRY and server["args"][index].replace("\\", "/").endswith(MCP_ENTRY):
        changes["script"] = (index, MCP_ENTRY)
    return apply_mcp_changes(text, config, changes) if changes else text


def prepare_project(root) -> list[str]:
    """Keep the project's Claude Code connection working on this computer: when a scene of the project
    loads, after GameStudio's setup and before Claude Code opens. Where .mcp.json starts GameStudio's
    MCP server but package.json lacks its package, it is declared (GameStudio's setup links it);
    then sync_mcp_config. Never raises; returns what it changed."""
    root = pathlib.Path(root)
    done = []
    try:
        config = json.loads((root / ".mcp.json").read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return done
    if mcp_server(config) is None:
        return done
    try:
        if ensure_mcp_package(root) == MCP_ADDED:
            done.append(MCP_ADDED)
        if sync_mcp_config(root):
            done.append("Updated .mcp.json for this computer")
    except (OSError, ValueError) as exc:
        log.warning("Could not update the Claude Code files of %s: %s", root, exc)
    return done


@persistent
def _on_load_post(*_args):
    root = project.project_root()
    if root is None:
        return
    try:
        for note in prepare_project(root):
            log.info("%s: %s", root, note)
        note = refresh_agent_files(root)
        if note:
            environment.notify(note, "INFO")  # the status bar and the Info log, once: it is done now
    except Exception:  # noqa: BLE001 - never break loading a file
        log.exception("Checking the Claude Code files failed")


_seen_job = {"finished": None}


def _after_setup() -> None:
    """Once a GameStudio setup job is done (it may have brought Node.js or linked the MCP server's
    package), the open project's .mcp.json is checked again."""
    job = environment.JOB
    if job.state != "done" or job.finished == _seen_job["finished"]:
        return
    _seen_job["finished"] = job.finished
    root = project.project_root()
    if root is None:
        return
    from . import codex

    codex.prepare_project(root)  # its .codex/config.toml too, when the project is connected to Codex
    if MCP_ADDED in prepare_project(root):
        environment.check_later(root, "load")  # links the package it declared


# --- The agent bridge -----------------------------------------------------------------------


def bridge_state() -> tuple[str, int, int]:
    """("off" | "listening" | "connected", port, clients)."""
    from . import bridge

    server = bridge.get_server()
    if server is None or not bridge.is_running():
        return "off", 0, 0
    clients = server.clients
    return ("connected" if clients else "listening"), server.port, clients


def connected_label() -> str:
    """Who is connected, as the clients said in the bridge hello: "Claude Code", "Claude Code + cursor"..."""
    from . import bridge

    names = []
    for info in bridge.clients_info():
        label = bridge.client_label(info["name"]) if info["name"] else "An agent"
        if label not in names:
            names.append(label)
    return " + ".join(names) or "An agent"


def bridge_status() -> tuple[str, str, bool]:
    """(text, icon, connected) for the menu and the Project panel. While nobody is connected it names
    the agent of the "Coding agent" preference."""
    state, _port, clients = bridge_state()
    label = expected_agent().label
    if state == "off":
        return f"Agent bridge off: {label} cannot reach Blender", "UNLINKED", False
    if state == "listening":
        return f"Waiting for {label}", "UNLINKED", False
    more = f" ({clients} sessions)" if clients > 1 else ""
    return f"{connected_label()} connected{more}", "LINKED", True


def status_item() -> tuple[str, str, bool] | None:
    """(text, icon, active) for the status bar, or None: nothing while the bridge is off outside a
    project (turned off on purpose), a quiet hint while it waits."""
    state, _port, clients = bridge_state()
    if state == "connected":
        return (f"{connected_label()} connected" + (f" ({clients})" if clients > 1 else "")), "LINKED", True
    if state == "listening":
        return f"{expected_agent().label} not connected", "UNLINKED", False
    if project.project_root() is not None:
        return "Agent bridge off", "UNLINKED", False
    return None


def ensure_bridge() -> str:
    """Turn the agent bridge on (the preference too). Returns a problem, or ""."""
    from . import bridge

    if bridge.is_running():
        return ""
    prefs = get_prefs()
    if hasattr(prefs, "bl_rna") and not prefs.bridge_enabled:
        prefs.bridge_enabled = True  # its update starts the bridge in the UI
    if not bridge.is_running():
        bridge.start(force=True)
    return "" if bridge.is_running() else "The agent bridge could not start (see the system console)"


# --- Drawing --------------------------------------------------------------------------------


def _muted(layout):
    row = layout.row()
    row.active = False
    return row


def display_path(path: str, home: str | None = None) -> str:
    """The path with the home folder as ~ (shorter, and no user name in screenshots)."""
    home = os.path.expanduser("~") if home is None else home
    if home and os.path.normcase(path).startswith(os.path.normcase(home.rstrip("\\/")) + os.sep):
        return "~" + path[len(home.rstrip("\\/")):]
    return path


def draw_install_progress(layout, installer: _Installer | None = None) -> bool:
    """The running install (progress and Cancel) or the one that just failed (Try Again, Show Log);
    False when there is none to show."""
    installer = installer or INSTALLER
    if installer.running():
        row = layout.row(align=True)
        if installer.progress is not None:
            row.progress(factor=installer.progress, text=installer.message)
        else:
            row.label(text=installer.message, icon="SORTTIME")
        row.operator(installer.cancel_op, text="", icon="X")
        return True
    if installer.state == "failed" and installer.message != "Cancelled":
        from .ui import wrap_label

        col = layout.column(align=True)
        wrap_label(col, installer.message, icon="ERROR")
        row = col.row(align=True)
        row.operator(installer.install_op, text="Try Again", icon="FILE_REFRESH").now = True
        if installer.log_path is not None and pathlib.Path(installer.log_path).is_file():
            row.operator("gamestudio.open_path", text="Show Log", icon="TEXT").filepath = str(installer.log_path)
        return True
    return False


def draw_program_status(layout, path: str | None, installer: _Installer) -> None:
    """Where an agent's CLI is, the install that runs, or the button that installs it."""
    if path:
        _muted(layout).label(text=f"{installer.label}: {display_path(path)}", icon="CHECKMARK")
        return
    if draw_install_progress(layout, installer):
        return
    row = layout.row()
    row.label(text=f"{installer.label} is not installed", icon="INFO")
    row.operator(installer.install_op, text="Install...", icon="IMPORT")


def draw_cli_status(layout) -> None:
    """Where claude is, the install that runs, or the button that installs it."""
    draw_program_status(layout, claude_path(), INSTALLER)


def draw_agent_status(layout, agent: Agent | None = None) -> None:
    """draw_cli_status of an agent (default: the preferred one)."""
    if (agent or preferred_agent()).key == CODEX.key:
        from . import codex

        codex.draw_cli_status(layout)
    else:
        draw_cli_status(layout)


def draw_bridge_status(layout) -> None:
    text, icon, connected = bridge_status()
    row = layout.row()
    row.active = connected
    row.label(text=text, icon=icon)
    if bridge_state()[0] == "off":
        layout.operator("gamestudio.bridge_enable", icon="LINKED")


def draw_menu_section(layout, context) -> None:
    """The coding agents' entries of the GameStudio menu: Open in Claude Code, Open in Codex, the
    starter prompts, an install that runs and the bridge."""
    layout.operator("gamestudio.open_claude_code", icon="CONSOLE")
    layout.operator(CODEX.open_op, icon="CONSOLE")
    layout.menu("GAMESTUDIO_MT_claude_prompts", icon="COPYDOWN")
    for installer in INSTALLERS:
        if installer.running():
            layout.label(text=installer.status_text()[:100], icon="SORTTIME")
            layout.operator(installer.cancel_op, icon="X")
    if bridge_state()[0] == "off":
        layout.operator("gamestudio.bridge_enable", icon="LINKED")
        return
    text, icon, connected = bridge_status()
    row = layout.row()
    row.active = connected
    row.label(text=text, icon=icon)


def draw_project_panel(self, context) -> None:
    """Appended to the Project editor's first panel (the Project tab), below Open in VS Code."""
    root = project.project_root()
    if root is None:
        return
    agent = preferred_agent()
    other = CODEX if agent.key == CLAUDE.key else CLAUDE
    layout = self.layout
    box = layout.box()
    row = box.row(align=True)
    row.scale_y = 1.3
    row.operator_context = "INVOKE_DEFAULT"
    row.operator(agent.open_op, icon="CONSOLE")
    row.menu("GAMESTUDIO_MT_claude_prompts", text="", icon="COPYDOWN")
    row = box.row()
    row.operator_context = "INVOKE_DEFAULT"
    row.operator(other.open_op, icon="CONSOLE")
    draw_bridge_status(box)
    if agent.key == CODEX.key:
        from . import codex

        if codex.missing_codex_files(root):
            _muted(box).label(text="Not connected to Codex yet: Open in Codex connects it", icon="INFO")
        if not codex.codex_path():
            codex.draw_cli_status(box)
        return
    missing = missing_agent_files(root)
    if missing:
        _muted(box).label(text="Not connected to Claude yet: Open in Claude Code connects it", icon="INFO")
    if not claude_path():
        draw_cli_status(box)


def draw_prompts(layout) -> None:
    """One copy button per starter prompt, with the prompt as its text."""
    col = layout.column(align=True)
    for index, (_label, prompt) in enumerate(STARTER_PROMPTS):
        row = col.row(align=True)
        row.operator("gamestudio.copy_starter_prompt", text="", icon="COPYDOWN").index = index
        row.label(text=prompt)


class GAMESTUDIO_MT_claude_prompts(bpy.types.Menu):
    bl_idname = "GAMESTUDIO_MT_claude_prompts"
    bl_label = "Copy Starter Prompt"

    def draw(self, context):
        layout = self.layout
        for index, (label, _prompt) in enumerate(STARTER_PROMPTS):
            layout.operator("gamestudio.copy_starter_prompt", text=label, icon="COPYDOWN").index = index


# --- Operators ------------------------------------------------------------------------------


_PLAIN_NAMES = {"AGENTS.md": "instructions for this game", "CLAUDE.md": "instructions for this game",
                ".claude": "Claude's skills", ".mcp.json": "the link to Blender", MCP_PACKAGE: "the link to Blender"}


def connect_text(files) -> str:
    """What "Connect Claude to this project" adds, in plain words (the file names are in its tooltip)."""
    order = list(_PLAIN_NAMES)
    files = sorted(files, key=lambda name: order.index(name) if name in order else len(order))
    names = list(dict.fromkeys(_PLAIN_NAMES.get(name, name) for name in files))
    if not names:
        return ""
    listed = names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]
    return f"Adds {listed}."


class GAMESTUDIO_OT_open_claude_code(bpy.types.Operator):
    bl_idname = "gamestudio.open_claude_code"
    bl_label = "Open in Claude Code"
    bl_description = ("Open a terminal in the project folder running Claude Code, which builds the game in this "
                      "Blender. When Claude Code is not installed yet, GameStudio offers to install it")

    add_files: BoolProperty(name="Connect Claude to this project", default=True, options={"SKIP_SAVE"},
                            description="Add what Claude Code reads in this project: instructions for this game "
                                        "(AGENTS.md and CLAUDE.md, which imports it), its skills (.claude/) and its "
                                        "link to Blender (.mcp.json and the @gamestudio/mcp package), from the "
                                        "project's template. Files the project already has are never changed")
    start_bridge: BoolProperty(name="Let Claude work in this Blender", default=True, options={"SKIP_SAVE"},
                               description="Turn on the agent bridge, through which Claude Code builds in this "
                                           "Blender. Only programs on this computer can use it (127.0.0.1, with a "
                                           "per-session token)")

    @classmethod
    def poll(cls, context):
        if project.project_root() is None:
            cls.poll_message_set("Open or create a game project first")
            return False
        return True

    @staticmethod
    def _needs(root) -> tuple[list[str], bool]:
        """(what the project lacks for Claude Code, whether the bridge is off)."""
        files = missing_agent_files(root)
        if mcp_missing(root) and toolchain.read_manifest(root):
            files.append(MCP_PACKAGE)
        return files, bridge_state()[0] == "off"

    def invoke(self, context, event):
        if INSTALLER.running():
            INSTALLER.then(_open_after_install)
            self.report({"INFO"}, "Claude Code is being installed (see the status bar); it opens when it is ready")
            return {"FINISHED"}
        if claude_path(refresh=True) is None:
            bpy.ops.gamestudio.claude_code_install("INVOKE_DEFAULT", open_after=True)
            return {"CANCELLED"}
        files, bridge_off = self._needs(project.project_root())
        if files or bridge_off:
            scale = context.preferences.system.ui_scale or 1.0
            return context.window_manager.invoke_props_dialog(self, width=int(520 * scale), confirm_text="Open",
                                                              title="Open in Claude Code")
        return self.execute(context)

    def draw(self, context):
        layout = self.layout
        files, bridge_off = self._needs(project.project_root())
        if files:
            layout.prop(self, "add_files")
            col = layout.column(align=True)
            col.active = False
            col.label(text=connect_text(files), icon="BLANK1")
            col.label(text="Files the project already has stay as they are.", icon="BLANK1")
        if bridge_off:
            layout.prop(self, "start_bridge")
            _muted(layout).label(text="You watch it build the game here, and can step in any time", icon="BLANK1")

    def execute(self, context):
        root = project.project_root()
        claude = claude_path(refresh=True)
        if claude is None:
            self.report({"ERROR"}, "Claude Code is not installed: GameStudio > Open in Claude Code installs it")
            return {"CANCELLED"}
        if self.add_files:
            template = agent_template(root)
            try:
                added = add_agent_files(root, template)
                note = ensure_mcp_package(root)
            except (OSError, ValueError) as exc:
                self.report({"ERROR"}, f"Could not connect Claude to the project: {exc}")
                return {"CANCELLED"}
            if added:
                label = (projects.template_info(template) or {}).get("label", template)
                self.report({"INFO"}, f"Added {', '.join(added)} from the {label} template")
            if note:
                self.report({"INFO"}, note)
        if self.start_bridge:
            problem = ensure_bridge()
            if problem:
                self.report({"WARNING"}, problem)
        # The MCP server Claude Code starts needs Node.js and the project's packages: GameStudio sets
        # them up first when they are missing, and Claude Code opens once they are ready.
        show_prompts = self.options.is_invoke and not bpy.app.background
        state = environment.ensure_ready(root, "claude", then=_open_when_ready(root, claude, show_prompts))
        if state in (environment.STARTED, environment.ASKING):
            self.report({"INFO"}, "Setting up GameStudio first (see the status bar); Claude Code opens when it "
                                  "is ready")
            return {"FINISHED"}
        if state != environment.READY:
            self.report({"ERROR"}, state)
            return {"CANCELLED"}
        try:
            launch = _launch(root, claude)
        except ClaudeCodeError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        self.report({"INFO"}, f"Claude Code is starting in {launch.label}")
        if show_prompts:
            _show_after_launch(context.window)
        return {"FINISHED"}


def _launch(root, claude: str) -> Launch:
    """The project's .mcp.json made right for this computer (Node.js may have just been set up), then the
    terminal. Raises ClaudeCodeError."""
    for note in prepare_project(root):
        log.info("%s: %s", root, note)
    launch = open_terminal(root, claude)
    opened(CLAUDE)
    return launch


def _open_after_install(ok: bool, _message: str = "") -> None:
    """Open in Claude Code again once its install is done (from a timer: the install ends in one)."""
    if not ok or project.project_root() is None:
        return

    def go():
        window = any_window()
        try:
            if window is not None:
                with bpy.context.temp_override(window=window, screen=window.screen):
                    bpy.ops.gamestudio.open_claude_code("INVOKE_DEFAULT")
            else:
                bpy.ops.gamestudio.open_claude_code()
        except RuntimeError as exc:
            environment.notify(f"Claude Code did not open: {exc}", "ERROR")
        return None

    bpy.app.timers.register(go, first_interval=0.3)


def _open_when_ready(root, claude: str, show_prompts: bool):
    """Open in Claude Code's second half, once GameStudio's setup is done."""
    def done(ok, message):
        if not ok and environment.missing_essentials(root, "claude"):
            environment.notify(message or "Claude Code did not open: GameStudio's setup did not finish", "ERROR")
            return
        try:
            launch = _launch(root, claude)
        except ClaudeCodeError as exc:
            environment.notify(str(exc), "ERROR")
            return
        environment.notify(f"Claude Code is starting in {launch.label}", "INFO")
        if show_prompts:
            _show_after_launch(any_window())
    return done


def _show_after_launch(window) -> None:
    """The starter prompts, once the dialog that started Claude Code has closed."""
    def show():
        try:
            if window is not None and window in bpy.context.window_manager.windows[:]:
                with bpy.context.temp_override(window=window, screen=window.screen):
                    bpy.ops.gamestudio.claude_started("INVOKE_DEFAULT")
            else:
                bpy.ops.gamestudio.claude_started("INVOKE_DEFAULT")
        except RuntimeError as exc:
            log.warning("Could not show the starter prompts: %s", exc)
        return None

    bpy.app.timers.register(show, first_interval=0.2)


class GAMESTUDIO_OT_claude_started(bpy.types.Operator):
    bl_idname = "gamestudio.claude_started"
    bl_label = "Claude Code Is Starting"
    bl_description = "What to say to Claude Code first: starter prompts to copy"
    bl_options = {"INTERNAL"}

    def invoke(self, context, event):
        scale = context.preferences.system.ui_scale or 1.0
        return context.window_manager.invoke_props_dialog(self, width=int(640 * scale), title=self.bl_label,
                                                          confirm_text="Done")

    def draw(self, context):
        layout = self.layout
        _muted(layout).label(text="The first time, it asks you to log in and to trust the project folder.")
        layout.label(text="Then tell it what to make. Copy a prompt and paste it into Claude Code:")
        draw_prompts(layout)
        _muted(layout).label(text="The status bar shows \"Claude Code connected\" once it works in Blender.",
                             icon="INFO")

    def execute(self, context):
        return {"FINISHED"}


def _expected_location() -> str:
    """Where the native installer puts claude, for the dialog."""
    return "your user folder (.local\\bin)" if _IS_WINDOWS else "~/.local/bin"


def _allow_online(context) -> None:
    """Blender's own Allow Online Access (the person chose "Allow Online Access and Install")."""
    try:
        if hasattr(bpy.ops.extensions, "userpref_allow_online"):
            bpy.ops.extensions.userpref_allow_online()
            return
    except (AttributeError, RuntimeError) as exc:
        log.debug("extensions.userpref_allow_online: %s", exc)
    try:
        context.preferences.system.use_online_access = True
    except (AttributeError, TypeError) as exc:
        log.warning("Could not turn on online access: %s", exc)


class GAMESTUDIO_OT_claude_code_install(bpy.types.Operator):
    bl_idname = "gamestudio.claude_code_install"
    bl_label = "Install Claude Code"
    bl_description = ("Install Claude Code, the coding agent that builds games in GameStudio, with Anthropic's "
                      "official installer: about 250 MB, for your user only, no administrator rights")

    open_after: BoolProperty(options={"HIDDEN", "SKIP_SAVE"})  # Open in Claude Code goes on once it is installed
    now: BoolProperty(options={"HIDDEN", "SKIP_SAVE"})         # Try Again: no dialog

    def invoke(self, context, event):
        if INSTALLER.running():
            if self.open_after:
                INSTALLER.then(_open_after_install)
            self.report({"INFO"}, "Claude Code is being installed: see the status bar")
            return {"FINISHED"}
        found = claude_path(refresh=True)
        if found is not None:
            self.report({"INFO"}, f"Claude Code is installed: {display_path(found)}")
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
            confirm = f"Install Claude Code (about {install_size_mb()} MB)"
        scale = context.preferences.system.ui_scale or 1.0
        return context.window_manager.invoke_props_dialog(self, width=int(600 * scale), title=self.bl_label,
                                                          confirm_text=confirm)

    def draw(self, context):
        from .ui import wrap_label

        layout = self.layout
        layout.label(text="Claude Code, the coding agent that builds your game, is not on this computer yet.",
                     icon="INFO")
        problem = install_problem()
        if problem:
            wrap_label(layout, problem, icon="ERROR", width=90)
            layout.label(text="Or install it by hand: open a terminal, paste this command, then come back:")
            col = layout.column(align=True)
            for index, (shell, command) in enumerate(install_commands()):
                row = col.row(align=True)
                row.operator("gamestudio.copy_install_command", text="", icon="COPYDOWN").index = index
                row.label(text=f"{shell}:  {command}")
        else:
            col = layout.column(align=True)
            col.label(text=f"GameStudio installs it for you with Anthropic's official installer "
                           f"(about {install_size_mb()} MB).", icon="IMPORT")
            _muted(col).label(text=f"It goes in {_expected_location()}: no administrator rights. It keeps itself "
                                   "up to date.", icon="BLANK1")
            _muted(col).label(text=f"The installer: {install_commands()[0][1]}", icon="BLANK1")
            if not environment.online():
                layout.label(text="Blender's online access is off: Install turns it on (Preferences > System > "
                                  "Network)", icon="ERROR")
        layout.separator()
        col = layout.column(align=True)
        _muted(col).label(text="Claude Code needs a Claude subscription (Pro, Max, Team or Enterprise) or a Claude "
                               "Console account.", icon="BLANK1")
        _muted(col).label(text="It asks you to log in the first time it starts.", icon="BLANK1")
        row = layout.row()
        row.operator("wm.url_open", text="Installation Guide", icon="URL").url = DOCS_URL

    def execute(self, context):
        if claude_path(refresh=True) is not None:
            if self.open_after:
                _open_after_install(True)
            return {"FINISHED"}
        if install_problem():
            _open_url(DOCS_URL)  # the guided path: this computer cannot run the installer
            return {"FINISHED"}
        if not environment.online():
            _allow_online(context)
            if not environment.online():
                self.report({"ERROR"}, "Blender's online access is off (it was started with --offline-mode), so "
                                       "Claude Code cannot be downloaded")
                return {"CANCELLED"}
        problem = start_install(_open_after_install if self.open_after else None)
        if problem:
            self.report({"ERROR"}, problem)
            return {"CANCELLED"}
        self.report({"INFO"}, f"Installing Claude Code (about {install_size_mb()} MB): see the status bar"
                              + ("; it opens in the project when it is ready" if self.open_after else ""))
        return {"FINISHED"}


class GAMESTUDIO_OT_claude_code_install_cancel(bpy.types.Operator):
    bl_idname = "gamestudio.claude_code_install_cancel"
    bl_label = "Cancel Installing Claude Code"
    bl_description = "Stop Claude Code's installer"

    @classmethod
    def poll(cls, context):
        return INSTALLER.running()

    def execute(self, context):
        INSTALLER.cancel()
        return {"FINISHED"}


def _open_url(url: str) -> None:
    bpy.ops.wm.url_open(url=url)


def _set_clipboard(context, text: str) -> None:
    context.window_manager.clipboard = text


class _CopyOperator:
    bl_options = {"INTERNAL"}

    index: IntProperty(options={"HIDDEN", "SKIP_SAVE"})

    @classmethod
    def entries(cls) -> tuple:
        """((label, text), ...): what the index picks from."""
        raise NotImplementedError

    @classmethod
    def description(cls, context, properties):
        entries = cls.entries()
        index = getattr(properties, "index", 0)
        return f"Copy: {entries[index][1]}" if 0 <= index < len(entries) else cls.bl_description

    def execute(self, context):
        entries = self.entries()
        if not 0 <= self.index < len(entries):
            return {"CANCELLED"}
        _set_clipboard(context, entries[self.index][1])
        self.report({"INFO"}, f"Copied: {entries[self.index][1]}")
        return {"FINISHED"}


class GAMESTUDIO_OT_copy_starter_prompt(_CopyOperator, bpy.types.Operator):
    bl_idname = "gamestudio.copy_starter_prompt"
    bl_label = "Copy Starter Prompt"
    bl_description = "Copy a prompt to paste into the coding agent (Claude Code or Codex)"

    @classmethod
    def entries(cls):
        return STARTER_PROMPTS


class GAMESTUDIO_OT_copy_install_command(_CopyOperator, bpy.types.Operator):
    bl_idname = "gamestudio.copy_install_command"
    bl_label = "Copy Install Command"
    bl_description = "Copy the command that installs Claude Code"

    @classmethod
    def entries(cls):
        return install_commands()


class GAMESTUDIO_OT_bridge_enable(bpy.types.Operator):
    bl_idname = "gamestudio.bridge_enable"
    bl_label = "Turn On Agent Bridge"
    bl_description = ("Let the coding agent (Claude Code or Codex) drive this Blender through the GameStudio MCP "
                      "server (127.0.0.1 only, token-protected). Agent edits are undo steps")

    def execute(self, context):
        problem = ensure_bridge()
        if problem:
            self.report({"ERROR"}, problem)
            return {"CANCELLED"}
        _status_tick()
        return {"FINISHED"}


# --- Keeping the status bar current ---------------------------------------------------------

_shown = {"item": None}


def _status_tick():
    """Redraw what shows the connection when it changes (the status bar item is activity_ui's) or the
    install's result expires, and check the project's .mcp.json again after a GameStudio setup job
    (_after_setup)."""
    try:
        item = (status_item(), *(install_status(installer) for installer in INSTALLERS))
        _after_setup()
        header = bpy.types.STATUSBAR_HT_header
        funcs = getattr(header.draw, "_draw_funcs", None)
        for draw in INSTALL_STATUS_DRAWS:
            if funcs is not None and draw not in funcs:
                header.append(draw)  # WorkSpace.status_text_set() can restore an older draw
    except Exception:  # never let the timer die: the status is cosmetic
        return 1.0
    if item != _shown["item"]:
        _shown["item"] = item
        tag_redraw(area_types=("VIEW_3D", "TOPBAR", "STATUSBAR", "PREFERENCES"), region_type=None)
        refresh_status_bar()
    return 0.5


CLASSES = (GAMESTUDIO_MT_claude_prompts, GAMESTUDIO_OT_open_claude_code, GAMESTUDIO_OT_claude_started,
           GAMESTUDIO_OT_claude_code_install, GAMESTUDIO_OT_claude_code_install_cancel,
           GAMESTUDIO_OT_copy_starter_prompt, GAMESTUDIO_OT_copy_install_command, GAMESTUDIO_OT_bridge_enable)


def _project_panels() -> list:
    """The Project editor's first panel (editors/project.py): the Project tab of the sidebar on
    stock Blender, the Project editor on GameStudio Editor."""
    from .host import editors

    return [cls for cls in editors.registered_classes("GAMESTUDIO_PROJECT")
            if getattr(getattr(cls, "gs_spec", None), "id", "") == "overview"]


_appended = []


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    for panel in _project_panels():
        panel.append(draw_project_panel)
        _appended.append(panel)
    bpy.types.STATUSBAR_HT_header.append(draw_install_status)
    if _on_load_post not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_on_load_post)
    _seen_job["finished"] = environment.JOB.finished if environment.JOB.state else None
    if not bpy.app.background and not bpy.app.timers.is_registered(_status_tick):
        bpy.app.timers.register(_status_tick, first_interval=1.0, persistent=True)


def unregister():
    if bpy.app.timers.is_registered(_status_tick):
        bpy.app.timers.unregister(_status_tick)
    if bpy.app.timers.is_registered(_install_tick):
        bpy.app.timers.unregister(_install_tick)
    INSTALLER.cancel()
    if _on_load_post in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_on_load_post)
    bpy.types.STATUSBAR_HT_header.remove(draw_install_status)
    _shown["item"] = None
    for panel in _appended:
        panel.remove(draw_project_panel)
    _appended.clear()
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
