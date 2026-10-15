# SPDX-License-Identifier: GPL-3.0-or-later
"""Open in Claude Code: finding the CLI, installing it, the terminal command per OS, the project's
agent files and .mcp.json, the operators, menus, Project panel, status bar, welcome popup and
preferences.

Runs inside run_shell_tests.py (extension enabled, --background). Nothing here opens a terminal, runs
claude or runs Claude Code's real installer: detection gets fake `which`/`isfile` functions, the
operator's spawn is patched, and the installer's process is a small Python stand-in.
"""

import importlib
import json
import os
import pathlib
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import types
import unittest
from unittest import mock

import bpy

import test_editors as te
import test_shell_ux as ux

EXTENSION = te.EXTENSION

cc = importlib.import_module(f"{EXTENSION}.claude_code")
menus = importlib.import_module(f"{EXTENSION}.menus")
launcher = importlib.import_module(f"{EXTENSION}.launcher")
bridge = importlib.import_module(f"{EXTENSION}.bridge")
toolchain = importlib.import_module(f"{EXTENSION}.core.toolchain")
project = te.project

WIN_HOME = "C:\\Users\\Jane Doe"
WIN_CLAUDE = WIN_HOME + "\\.local\\bin\\claude.exe"
WIN_PROJECT = "C:\\My Games\\Gem Island"
WT = "C:\\Users\\Jane Doe\\AppData\\Local\\Microsoft\\WindowsApps\\wt.exe"
CMD = "C:\\Windows\\System32\\cmd.exe"  # by its path (toolchain.system_tool): never one in the current folder
STARTER_MCP = (cc.projects.TEMPLATES / "starter" / ".mcp.json").read_text(encoding="utf-8")


def which_of(found: dict):
    """A shutil.which stand-in: name -> path for the names in found."""
    return lambda name, *_a, **_k: found.get(name)


def isfile_of(paths):
    return lambda path: path in set(paths)


class DetectionTests(unittest.TestCase):
    def test_path_comes_first(self):
        found = cc.find_claude("win32", env={}, home=WIN_HOME, which=which_of({"claude": "D:\\bin\\claude.EXE"}),
                               isfile=isfile_of([WIN_CLAUDE]))
        self.assertEqual(found, "D:\\bin\\claude.exe", "shutil.which spells the extension as PATHEXT does")

    def test_windows_native_installer(self):
        found = cc.find_claude("win32", env={"USERPROFILE": WIN_HOME}, which=which_of({}),
                               isfile=isfile_of([WIN_CLAUDE]))
        self.assertEqual(found, WIN_CLAUDE)

    def test_windows_other_installers(self):
        env = {"USERPROFILE": WIN_HOME, "LOCALAPPDATA": WIN_HOME + "\\AppData\\Local",
               "APPDATA": WIN_HOME + "\\AppData\\Roaming", "NPM_CONFIG_PREFIX": "D:\\npm-global"}
        places = cc.install_locations("windows", WIN_HOME, env)
        self.assertEqual(places[0], WIN_CLAUDE, "the native installer first")
        for place in (WIN_HOME + "\\AppData\\Local\\Microsoft\\WinGet\\Links\\claude.exe",
                      WIN_HOME + "\\AppData\\Roaming\\npm\\claude.cmd", WIN_HOME + "\\.claude\\local\\claude.exe",
                      "D:\\npm-global\\claude.cmd"):
            self.assertIn(place, places)
        self.assertFalse(any("AnthropicClaude" in place for place in places), "the desktop app is not the CLI")
        npm = WIN_HOME + "\\AppData\\Roaming\\npm\\claude.cmd"
        self.assertEqual(cc.find_claude("win32", env=env, which=which_of({}), isfile=isfile_of([npm])), npm)

    def test_macos_and_linux(self):
        home = "/Users/jane"
        self.assertEqual(cc.find_claude("darwin", env={}, home=home, which=which_of({}),
                                        isfile=isfile_of(["/Users/jane/.local/bin/claude"])),
                         "/Users/jane/.local/bin/claude")
        self.assertEqual(cc.find_claude("darwin", env={}, home=home, which=which_of({}),
                                        isfile=isfile_of(["/opt/homebrew/bin/claude"])),
                         "/opt/homebrew/bin/claude", "Homebrew, off a Dock-started Blender's PATH")
        self.assertEqual(cc.find_claude("linux", env={}, home="/home/jane", which=which_of({}),
                                        isfile=isfile_of(["/home/jane/.local/bin/claude"])),
                         "/home/jane/.local/bin/claude")
        places = cc.install_locations("macos", home, {"NPM_CONFIG_PREFIX": "/Users/jane/.npm-packages"})
        for place in ("/Users/jane/.claude/local/claude", "/Users/jane/.npm-packages/bin/claude",
                      "/Users/jane/.npm-global/bin/claude", "/Users/jane/.volta/bin/claude"):
            self.assertIn(place, places)

    def test_npmrc_prefix_and_nvm(self):
        home = pathlib.Path(tempfile.mkdtemp(prefix="gs_claude_home_"))
        try:
            (home / ".npmrc").write_text("registry=https://registry.npmjs.org/\nprefix=~/my-npm\n", encoding="utf-8")
            for version in ("v20.1.0", "v24.3.0"):
                (home / ".nvm" / "versions" / "node" / version / "bin").mkdir(parents=True)
            places = cc.install_locations("linux", str(home), {})
            self.assertIn(f"{home}/my-npm/bin/claude", places)
            nvm = [p for p in places if "/.nvm/" in p]
            self.assertEqual([p.split("/")[-3] for p in nvm], ["v24.3.0", "v20.1.0"], "the newest Node.js first")
        finally:
            shutil.rmtree(home, ignore_errors=True)

    def test_not_installed(self):
        for platform in ("win32", "darwin", "linux"):
            self.assertIsNone(cc.find_claude(platform, env={}, home="/nowhere", which=which_of({}),
                                             isfile=isfile_of([])), platform)

    def test_cached_lookup(self):
        with mock.patch.object(cc, "find_claude", return_value="/x/claude") as find:
            self.assertEqual(cc.claude_path(refresh=True), "/x/claude")
            cc.claude_path()
            self.assertEqual(find.call_count, 1)
        with mock.patch.object(cc, "find_claude", return_value=None):
            self.assertIsNone(cc.claude_path(refresh=True))
        cc._cache["time"] = -cc._DETECT_TTL  # the next lookup is real again

    def test_display_path(self):
        home = str(pathlib.Path.home().parent / "Jane Doe")
        inside = os.path.join(home, ".local", "bin", "claude")
        self.assertEqual(cc.display_path(inside, home=home), "~" + os.sep + os.path.join(".local", "bin", "claude"))
        sibling = os.path.join(home + "2", "claude")
        self.assertEqual(cc.display_path(sibling, home=home), sibling)

    def test_install_commands_are_the_official_ones(self):
        windows = dict(cc.install_commands("win32"))
        self.assertEqual(windows["PowerShell"], "irm https://claude.ai/install.ps1 | iex")
        self.assertIn("https://claude.ai/install.cmd", windows["Command Prompt"])
        for platform in ("darwin", "linux"):
            self.assertEqual(cc.install_commands(platform), (("Terminal",
                                                              "curl -fsSL https://claude.ai/install.sh | bash"),))
        self.assertTrue(cc.DOCS_URL.startswith("https://code.claude.com/docs/"))


class InstallCommandTests(unittest.TestCase):
    """What Install Claude Code runs: the official command, unchanged, through the platform's shell."""

    def test_windows_runs_it_in_powershell(self):
        builtin = "C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe"
        argv = cc.install_argv("win32", which=which_of({}), isfile=isfile_of([builtin]),
                               env={"SystemRoot": "C:\\Windows"})
        self.assertEqual(argv, [builtin, "-NoProfile", "-NonInteractive", "-Command",
                                "irm https://claude.ai/install.ps1 | iex"])
        argv = cc.install_argv("win32", which=which_of({"pwsh.exe": "D:\\pwsh\\pwsh.exe"}), isfile=isfile_of([]),
                               env={})
        self.assertEqual(argv[0], "D:\\pwsh\\pwsh.exe")
        self.assertIsNone(cc.install_argv("win32", which=which_of({}), isfile=isfile_of([]), env={}))
        self.assertIn("PowerShell", cc.install_problem("win32", which=which_of({}), isfile=isfile_of([]), env={}))

    def test_macos_and_linux_run_it_in_bash(self):
        for platform in ("darwin", "linux"):
            argv = cc.install_argv(platform, which=which_of({"curl": "/usr/bin/curl"}), isfile=isfile_of(["/bin/bash"]))
            self.assertEqual(argv, ["/bin/bash", "-o", "pipefail", "-c",
                                    "curl -fsSL https://claude.ai/install.sh | bash"], platform)
            self.assertEqual(cc.install_problem(platform, which=which_of({"curl": "/usr/bin/curl"}),
                                                isfile=isfile_of(["/bin/bash"])), "")

    def test_without_curl_the_dialog_guides(self):
        problem = cc.install_problem("linux", which=which_of({}), isfile=isfile_of(["/bin/bash"]))
        self.assertIn("curl", problem)
        self.assertIn("sudo apt install curl", problem)
        self.assertIsNone(cc.install_argv("linux", which=which_of({}), isfile=isfile_of(["/bin/bash"])))

    def test_sizes(self):
        for platform in ("win32", "darwin", "linux"):
            self.assertTrue(200 <= cc.install_size_mb(platform) <= 300, platform)

    def test_start_install_explains_when_it_cannot(self):
        with mock.patch.object(cc, "install_argv", return_value=None), \
                mock.patch.object(cc, "install_problem", return_value="no curl"):
            self.assertEqual(cc.start_install(), "no curl")
        self.assertFalse(cc.INSTALLER.running())


def fake_installer(script: str):
    """A stand-in for the installer's process: `script` in Blender's Python, its output to the log."""
    def popen(argv, **kwargs):
        return subprocess.Popen([sys.executable, "-I", "-c", script], **kwargs)
    return popen


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.home = pathlib.Path(tempfile.mkdtemp(prefix="gs_claude_install_"))
        self.log = self.home / "gamestudio" / cc.INSTALL_LOG

    def tearDown(self):
        cc.INSTALLER.cancel()
        deadline = time.monotonic() + 20
        while cc.INSTALLER.running() and time.monotonic() < deadline:
            time.sleep(0.1)
        if bpy.app.timers.is_registered(cc._install_tick):
            bpy.app.timers.unregister(cc._install_tick)
        cc.INSTALLER.__init__()
        cc._cache["time"] = -cc._DETECT_TTL
        shutil.rmtree(self.home, ignore_errors=True)

    def run_install(self, script: str, found, watch=None) -> list:
        done = []
        with mock.patch.object(cc, "_popen_installer", fake_installer(script)), \
                mock.patch.object(cc, "find_claude", return_value=found), \
                mock.patch.object(cc.environment, "notify") as notify:
            self.assertTrue(cc.INSTALLER.start(["the installer"], self.log, self.home, dict(os.environ),
                                               then=lambda ok, message: done.append((ok, message))))
            self.assertTrue(cc.INSTALLER.running())
            self.assertFalse(cc.INSTALLER.start(["again"], self.log, self.home, dict(os.environ)), "one at a time")
            deadline = time.monotonic() + 60
            while cc.INSTALLER.running() and time.monotonic() < deadline:
                if watch is not None:
                    watch()
                time.sleep(0.05)
            self.assertFalse(cc.INSTALLER.running(), "the stand-in installer ended")
            cc._install_tick()  # what the timer does on the main thread
        self.notified = notify.call_args_list
        return done

    def test_success_with_download_progress(self):
        downloads = (self.home / ".claude" / "downloads").as_posix()
        script = ("import os, time, pathlib\n"
                  f"folder = pathlib.Path({downloads!r}); folder.mkdir(parents=True, exist_ok=True)\n"
                  "with open(folder / 'claude-2.1.0-win32-x64.exe', 'wb') as out:\n"
                  "    for _ in range(8):\n"
                  "        out.write(b'x' * 1000000); out.flush(); time.sleep(0.15)\n"
                  "print('Setting up Claude Code...'); print('Installation complete!')\n")
        seen = set()
        done = self.run_install(script, found=WIN_CLAUDE, watch=lambda: seen.add(cc.INSTALLER.message.split(":")[0]))
        self.assertIn("Downloading Claude Code", seen)
        self.assertEqual(done, [(True, cc.INSTALLER.message)])
        self.assertEqual(cc.INSTALLER.state, "done")
        self.assertEqual(cc.INSTALLER.path, WIN_CLAUDE)
        self.assertIn("Claude Code is installed", cc.INSTALLER.message)
        self.assertIn("Installation complete!", self.log.read_text(encoding="utf-8"))
        self.assertEqual(cc.install_status()[:2], ("Claude Code is installed", "CHECKMARK"))
        self.assertIn(mock.call(cc.INSTALLER.message, "INFO"), self.notified)

    def test_offline_says_so(self):
        script = "import sys\nprint('curl: (6) Could not resolve host: claude.ai')\nsys.exit(6)\n"
        done = self.run_install(script, found=None)
        self.assertEqual(done, [(False, "Claude Code could not be downloaded: claude.ai could not be reached. Check "
                                        "the internet connection, then Try Again")])

    def test_failure_says_why(self):
        script = ("import sys\nprint('Setting up Claude Code...')\n"
                  "print('Claude Code does not support 32-bit Windows. Use a 64-bit Windows.')\nsys.exit(1)\n")
        done = self.run_install(script, found=None)
        self.assertEqual(len(done), 1)
        ok, message = done[0]
        self.assertFalse(ok)
        self.assertEqual(message, "Claude Code's installer failed (exit code 1): Claude Code does not support 32-bit "
                                  "Windows. Use a 64-bit Windows.")
        self.assertEqual(cc.install_status(), ("Claude Code was not installed", "ERROR", True))
        layout = ux.Recorder(self)
        with mock.patch.object(cc, "claude_path", return_value=None):
            cc.draw_cli_status(layout)
        self.assertIn("gamestudio.claude_code_install", ux.ids(layout), "Try Again")
        self.assertIn("gamestudio.open_path", ux.ids(layout), "Show Log")

    def test_claude_there_is_success_whatever_the_exit_code(self):
        done = self.run_install("import sys\nprint('shell integration failed')\nsys.exit(2)\n", found=WIN_CLAUDE)
        self.assertEqual(done, [(True, f"Claude Code is installed ({WIN_CLAUDE})")])

    def test_finished_but_not_found(self):
        done = self.run_install("print('done')\n", found=None)
        self.assertFalse(done[0][0])
        self.assertIn("cannot find claude", done[0][1])

    def test_cancel(self):
        done = []
        with mock.patch.object(cc, "_popen_installer", fake_installer("import time\ntime.sleep(60)\n")), \
                mock.patch.object(cc, "find_claude", return_value=None):
            cc.INSTALLER.start(["the installer"], self.log, self.home, dict(os.environ),
                               then=lambda ok, message: done.append((ok, message)))
            time.sleep(0.5)
            self.assertTrue(bpy.ops.gamestudio.claude_code_install_cancel.poll())
            bpy.ops.gamestudio.claude_code_install_cancel()
            deadline = time.monotonic() + 30
            while cc.INSTALLER.running() and time.monotonic() < deadline:
                time.sleep(0.1)
            cc._install_tick()
        self.assertEqual(done, [(False, "Cancelled")])
        self.assertIsNone(cc.install_status(), "a cancelled install says nothing")

    def test_status_bar_while_installing(self):
        header = bpy.types.STATUSBAR_HT_header
        self.assertIn(cc.draw_install_status, header.draw._draw_funcs)
        cc.INSTALLER.state, cc.INSTALLER.message, cc.INSTALLER.progress = "running", "Downloading Claude Code", 0.5
        layout = ux.Recorder(self)
        cc.draw_install_status(te._FakeSelf(header, layout), te.fake_context())
        self.assertEqual(layout.texts, ["Downloading Claude Code (50%)"])
        self.assertIn("gamestudio.claude_code_install_cancel", ux.ids(layout))
        menu = ux.draw_menu(self, "GAMESTUDIO_MT_projects")
        self.assertIn("gamestudio.claude_code_install_cancel", ux.ids(menu))
        cc.INSTALLER.state = ""
        layout = ux.Recorder(self)
        cc.draw_install_status(te._FakeSelf(header, layout), te.fake_context())
        self.assertEqual(layout.calls, [])


class TerminalTests(unittest.TestCase):
    def test_windows_terminal(self):
        launch = cc.terminal_command(WIN_PROJECT + "\\", WIN_CLAUDE, "win32", which=which_of({"wt.exe": WT}), env={})
        self.assertEqual(launch.label, "Windows Terminal")
        self.assertEqual(launch.argv, [WT, "-d", WIN_PROJECT, CMD, "/k", ".gamestudio\\claude-code.cmd"])
        self.assertEqual(launch.cwd, WIN_PROJECT)
        self.assertFalse(launch.console)
        self.assertEqual(launch.script, WIN_PROJECT + "\\.gamestudio\\claude-code.cmd")
        lines = launch.script_text.split("\r\n")
        self.assertIn('cd /d "%~dp0.."', lines, "the project folder, wherever the terminal starts")
        self.assertIn(f'"{WIN_CLAUDE}"', lines)
        line = subprocess.list2cmdline(launch.argv)
        self.assertIn(f'-d "{WIN_PROJECT}" {CMD} /k .gamestudio\\claude-code.cmd', line,
                      "paths with spaces stay one argument; the script is named inside the project")

    def test_windows_terminal_escapes_its_separator(self):
        launch = cc.terminal_command("C:\\games\\a;b", "claude", "win32", which=which_of({"wt": WT}), env={})
        self.assertEqual(launch.argv[2], "C:\\games\\a\\;b")

    def test_a_cmd_shim_is_called(self):
        shim = WIN_HOME + "\\AppData\\Roaming\\npm\\claude.cmd"
        launch = cc.terminal_command(WIN_PROJECT, shim, "win32", which=which_of({"wt.exe": WT}), env={})
        lines = launch.script_text.split("\r\n")
        self.assertIn(f'call "{shim}"', lines)
        # The shim starts a bare `node`: a node.exe in the project must not be the one that runs.
        self.assertLess(lines.index('set "NoDefaultCurrentDirectoryInExePath=1"'), lines.index(f'call "{shim}"'))

    def test_command_prompt_without_windows_terminal(self):
        launch = cc.terminal_command(WIN_PROJECT, WIN_CLAUDE, "win32", which=which_of({}), env={})
        self.assertEqual(launch.label, "Command Prompt")
        self.assertEqual(launch.argv, f"{CMD} /k .gamestudio\\claude-code.cmd")
        self.assertEqual(launch.cwd, WIN_PROJECT, "the folder is the working directory, never parsed by cmd")
        self.assertTrue(launch.console)
        launch = cc.terminal_command("C:\\R&D (old)", "C:\\R&D (old)\\claude.exe", "win32", which=which_of({}),
                                     env={})
        self.assertEqual(launch.argv, f"{CMD} /k .gamestudio\\claude-code.cmd", "& and ( ) never reach cmd's parser")
        self.assertIn('"C:\\R&D (old)\\claude.exe"', launch.script_text, "inside quotes in the script")

    def test_windows_script_puts_gamestudios_node_first(self):
        env = {"USERPROFILE": "C:\\Users\\Zoë", "APPDATA": "C:\\Users\\Zoë\\AppData\\Roaming"}
        node = "C:\\Users\\Zoë\\AppData\\Roaming\\Blender Foundation\\Blender\\5.2\\extensions\\.user\\gamestudio\\node"
        claude = "C:\\Users\\Zoë\\.local\\bin\\claude.exe"
        text = cc.launch_script("win32", claude, path_prepend=node, env=env)
        lines = text.split("\r\n")
        self.assertIn('set "PATH=%APPDATA%\\Blender Foundation\\Blender\\5.2\\extensions\\.user\\gamestudio\\node;'
                      '%PATH%"', lines)
        self.assertIn('"%USERPROFILE%\\.local\\bin\\claude.exe"', lines, "no user name for cmd's code page")
        self.assertTrue(cc._script_bytes(True, text).startswith(b"@echo off"), "plain ASCII")
        other = cc.launch_script("win32", "D:\\Zoë\\claude.exe", env={})
        self.assertTrue(cc._script_bytes(True, other).startswith(b"@chcp 65001 >nul\r\n@echo off"))

    def test_macos_terminal_needs_no_automation_permission(self):
        folder, claude = "/Users/jane/My Games/O'Brien \"Quest\"", "/Users/jane/.local/bin/claude"
        launch = cc.terminal_command(folder, claude, "darwin", which=which_of({}), env={})
        self.assertEqual(launch.label, "Terminal")
        script = folder + "/.gamestudio/claude-code.command"
        self.assertEqual(launch.argv, ["/usr/bin/open", "-a", "Terminal", script])
        self.assertNotIn("osascript", launch.argv)
        self.assertTrue(launch.wait, "open exits at once: its exit code says whether Terminal opened")
        lines = launch.script_text.splitlines()
        self.assertEqual(lines[0], "#!/bin/sh")
        self.assertIn('cd "$(dirname "$0")/.." || exit 1', lines)
        self.assertEqual(shlex.split(lines[-1])[0], claude)

        launch = cc.terminal_command("/Users/jane/game", claude, "darwin", which=which_of({}), env={},
                                     path_prepend="/Users/jane/Library/GameStudio Node/bin")
        self.assertIn("PATH='/Users/jane/Library/GameStudio Node/bin':\"$PATH\"; export PATH",
                      launch.script_text.splitlines())

    def test_macos_terminal_apps_from_the_preferences(self):
        claude = "/Users/jane/.local/bin/claude"
        for template, label in (("iTerm", "iTerm"), ("Warp", "Warp"), ("/Applications/iTerm.app", "iTerm"),
                                ('"Ghostty"', "Ghostty")):
            launch = cc.terminal_command("/Users/jane/game", claude, "darwin", template=template, env={})
            self.assertEqual(launch.label, label, template)
            self.assertEqual(launch.argv, ["/usr/bin/open", "-a", template.strip('"'),
                                           "/Users/jane/game/.gamestudio/claude-code.command"])
        launch = cc.terminal_command("/Users/jane/game", claude, "darwin", template="open -a iTerm {script}", env={})
        self.assertEqual(launch.argv, ["open", "-a", "iTerm", "/Users/jane/game/.gamestudio/claude-code.command"])

    def test_spawn_reports_a_terminal_that_did_not_open(self):
        launch = cc.terminal_command("/Users/jane/game", "/x/claude", "darwin", template="NoSuchTerm", env={})
        failed = subprocess.CompletedProcess(launch.argv, 1, "", "Unable to find application named 'NoSuchTerm'\n")
        with mock.patch.object(cc.subprocess, "run", return_value=failed):
            with self.assertRaises(cc.ClaudeCodeError) as caught:
                cc._spawn(launch, {})
        self.assertIn("Unable to find application named 'NoSuchTerm'", str(caught.exception))
        with mock.patch.object(cc.subprocess, "run", return_value=subprocess.CompletedProcess(launch.argv, 0, "", "")):
            self.assertIsNone(cc._spawn(launch, {}))

    def test_linux_terminals(self):
        folder, claude = "/home/jane/My Games/gem", "/home/jane/.local/bin/claude"
        script = folder + "/.gamestudio/claude-code.sh"
        gnome = {"gnome-terminal": "/usr/bin/gnome-terminal"}
        launch = cc.terminal_command(folder, claude, "linux", which=which_of(gnome))
        self.assertEqual(launch.argv, ["/usr/bin/gnome-terminal", f"--working-directory={folder}", "--", script])
        launch = cc.terminal_command(folder, claude, "linux", which=which_of({"konsole": "/usr/bin/konsole"}))
        self.assertEqual(launch.argv, ["/usr/bin/konsole", "--workdir", folder, "-e", script])
        both = {"x-terminal-emulator": "/usr/bin/x-terminal-emulator", "konsole": "/usr/bin/konsole"}
        launch = cc.terminal_command(folder, claude, "linux", which=which_of(both))
        self.assertEqual(launch.argv, ["/usr/bin/x-terminal-emulator", "-e", "./.gamestudio/claude-code.sh"],
                         "the desktop's choice first; no spaces for terminals that split -e")
        self.assertEqual(launch.cwd, folder)
        self.assertEqual(launch.script, script)
        with self.assertRaises(cc.ClaudeCodeError):
            cc.terminal_command(folder, claude, "linux", which=which_of({}))

    @unittest.skipIf(os.name == "nt" or not shutil.which("sh"), "needs a POSIX shell")
    def test_posix_script_runs(self):
        base = pathlib.Path(tempfile.mkdtemp(prefix="gs_claude_script_"))
        try:
            folder = base / "My Game"
            fake = base / "bin" / "claude"
            fake.parent.mkdir(parents=True)
            fake.write_text('#!/bin/sh\necho "cwd=$(pwd) node=$(command -v node)" > "$HOME/out"\n', encoding="utf-8")
            fake.chmod(0o755)
            (base / "node" / "bin").mkdir(parents=True)
            (base / "node" / "bin" / "node").write_text("#!/bin/sh\n", encoding="utf-8")
            (base / "node" / "bin" / "node").chmod(0o755)
            launch = cc.terminal_command(folder, str(fake), "linux", which=which_of({"xterm": "/usr/bin/xterm"}),
                                         path_prepend=str(base / "node" / "bin"))
            cc.write_launch_script(launch)
            subprocess.run([launch.script], cwd=str(base), env=dict(os.environ, HOME=str(base)), check=True, timeout=30)
            out = (base / "out").read_text(encoding="utf-8").strip()
            self.assertEqual(out, f"cwd={folder} node={base / 'node' / 'bin' / 'node'}")
        finally:
            shutil.rmtree(base, ignore_errors=True)

    def test_custom_template(self):
        template = '"C:\\Tools\\My Term\\term.exe" --cwd "{project}" -- "{claude}"'
        launch = cc.terminal_command(WIN_PROJECT, WIN_CLAUDE, "win32", template=template, which=which_of({}))
        self.assertEqual(launch.label, "Custom command")
        self.assertEqual(launch.argv, ["C:\\Tools\\My Term\\term.exe", "--cwd", WIN_PROJECT, "--", WIN_CLAUDE])
        launch = cc.terminal_command("/home/j/My Game", "claude", "linux", template="kitty --directory {project}")
        self.assertEqual(launch.argv, ["kitty", "--directory", "/home/j/My Game"])
        launch = cc.terminal_command("/home/j/My Game", "claude", "linux", template="kitty {script}")
        self.assertEqual(launch.argv, ["kitty", "/home/j/My Game/.gamestudio/claude-code.sh"])
        with self.assertRaises(cc.ClaudeCodeError):
            cc.terminal_command(WIN_PROJECT, WIN_CLAUDE, "win32", template='"unbalanced {claude}')

    def test_this_platform_has_a_terminal_label(self):
        self.assertIsInstance(cc.terminal_label(), str)


class _TempProject(unittest.TestCase):
    def setUp(self):
        self.base = pathlib.Path(tempfile.mkdtemp(prefix="gs_claude_"))
        self.root = self.base / "My Game"
        self.root.mkdir()
        (self.root / "gamestudio.json").write_text(json.dumps({"name": "Gem Island"}), encoding="utf-8")

    def tearDown(self):
        project.set_project_root_override(None)
        cc._opened["agent"] = None  # Open in Claude Code remembers the agent it opened
        shutil.rmtree(self.base, ignore_errors=True)

    def mcp(self, text: str = STARTER_MCP) -> pathlib.Path:
        path = self.root / ".mcp.json"
        path.write_text(text, encoding="utf-8", newline="")
        return path

    def server(self) -> dict:
        return json.loads((self.root / ".mcp.json").read_text(encoding="utf-8"))["mcpServers"]["gamestudio"]

    def own_script(self) -> pathlib.Path:
        script = self.root / cc.MCP_ENTRY
        script.parent.mkdir(parents=True, exist_ok=True)
        script.write_text("", encoding="utf-8")
        return script


class AgentFileTests(_TempProject):
    def test_missing_files(self):
        self.assertEqual(cc.missing_agent_files(self.root), [".mcp.json", "AGENTS.md", "CLAUDE.md", ".claude"])
        (self.root / ".claude").mkdir()
        (self.root / "CLAUDE.md").write_text("# mine\n", encoding="utf-8")
        self.assertEqual(cc.missing_agent_files(self.root), [".mcp.json"],
                         "instructions only in CLAUDE.md: Claude Code needs no AGENTS.md")
        (self.root / "CLAUDE.md").write_text("@AGENTS.md\n", encoding="utf-8")
        self.assertEqual(cc.missing_agent_files(self.root), [".mcp.json", "AGENTS.md"], "CLAUDE.md imports it")

    def test_add_from_the_starter_template(self):
        added = cc.add_agent_files(self.root)
        self.assertEqual(added, [".mcp.json", "AGENTS.md", "CLAUDE.md", ".claude"])
        self.assertEqual(cc.missing_agent_files(self.root), [])
        self.assertTrue((self.root / "AGENTS.md").read_text(encoding="utf-8").startswith("# Gem Island\n"))
        self.assertTrue((self.root / "CLAUDE.md").read_text(encoding="utf-8").startswith("@AGENTS.md\n"))
        settings = json.loads((self.root / ".claude" / "settings.json").read_text(encoding="utf-8"))
        self.assertIn("gamestudio", settings["enabledMcpjsonServers"])
        self.assertTrue((self.root / ".claude" / "skills" / "new-game-from-brief" / "SKILL.md").is_file())
        self.assertEqual(self.server()["args"], [cc.MCP_ENTRY])
        self.assertEqual(cc.add_agent_files(self.root), [], "nothing left to add")

    def test_add_from_the_projects_own_template(self):
        (self.root / "gamestudio.json").write_text(json.dumps({"name": "Gem Island", "template": "shooter"}),
                                                   encoding="utf-8")
        self.assertEqual(cc.agent_template(self.root), "shooter")
        self.assertEqual(cc.add_agent_files(self.root), [".mcp.json", "AGENTS.md", "CLAUDE.md", ".claude"])
        agents = (self.root / "AGENTS.md").read_text(encoding="utf-8")
        self.assertTrue(agents.startswith("# Gem Island\n"))
        self.assertIn("`add-enemy-type`", agents, "the shooter's AGENTS.md")
        starter = (cc.projects.TEMPLATES / "starter" / "AGENTS.md").read_text(encoding="utf-8").split("\n", 1)[1]
        starter = starter.replace("{{name}}", "Gem Island").replace("{{slug}}", "gem-island").strip("\n")
        self.assertIn(starter, agents, "composed with the starter's ({{base}}), as New Project does")
        self.assertNotIn("{{", agents)
        claude = (self.root / "CLAUDE.md").read_text(encoding="utf-8")
        self.assertEqual(claude, (cc.projects.TEMPLATES / "starter" / "CLAUDE.md").read_text(encoding="utf-8"),
                         "the starter's CLAUDE.md: the import and the Claude Code section")
        for skill in ("add-enemy-type", "new-game-from-brief"):
            self.assertTrue((self.root / ".claude" / "skills" / skill / "SKILL.md").is_file(), skill)
        self.assertTrue((self.root / ".claude" / "settings.json").is_file())

    def test_unknown_template_falls_back_to_the_starter(self):
        (self.root / "gamestudio.json").write_text(json.dumps({"name": "Gem Island", "template": "gone"}),
                                                   encoding="utf-8")
        self.assertEqual(cc.agent_template(self.root), cc.AGENT_TEMPLATE)
        cc.add_agent_files(self.root)
        self.assertTrue((self.root / ".claude" / "skills" / "new-game-from-brief" / "SKILL.md").is_file())
        self.assertFalse((self.root / ".claude" / "skills" / "add-enemy-type").exists())

    def test_never_overwrites(self):
        (self.root / "CLAUDE.md").write_text("# My own notes\n", encoding="utf-8")
        (self.root / ".claude").mkdir()
        (self.root / ".claude" / "settings.json").write_text("{}", encoding="utf-8")
        self.assertEqual(cc.add_agent_files(self.root), [".mcp.json"])
        self.assertEqual((self.root / "CLAUDE.md").read_text(encoding="utf-8"), "# My own notes\n")
        self.assertEqual((self.root / ".claude" / "settings.json").read_text(encoding="utf-8"), "{}")
        self.assertFalse((self.root / ".claude" / "skills").exists(), "an existing .claude/ is the user's")

    def test_mcp_entry_in_a_parent_folder(self):
        entry = self.base / cc.MCP_ENTRY
        entry.parent.mkdir(parents=True)
        entry.write_text("", encoding="utf-8")
        self.assertEqual(cc.mcp_entry(self.root), "../" + cc.MCP_ENTRY)
        cc.add_agent_files(self.root)
        self.assertEqual(self.server()["args"], ["../" + cc.MCP_ENTRY])

    def test_older_project_gets_the_mcp_package(self):
        (self.root / "package.json").write_text(json.dumps({"name": "gem", "devDependencies": {"vite": "8"}}),
                                                encoding="utf-8")
        packages = self.base / "packages"
        (packages / "mcp").mkdir(parents=True)
        (packages / "mcp" / "package.json").write_text("{}", encoding="utf-8")
        self.assertTrue(cc.mcp_missing(self.root))
        with mock.patch.object(cc.environment, "setup_project", return_value="") as setup, \
                mock.patch.object(cc.projects, "runtime_packages", return_value=packages):
            note = cc.ensure_mcp_package(self.root)
        setup.assert_not_called()  # Open in Claude Code's readiness check links it, before the terminal opens
        self.assertEqual(note, cc.MCP_ADDED)
        manifest = json.loads((self.root / "package.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["devDependencies"][cc.MCP_PACKAGE], "file:" + (packages / "mcp").as_posix())
        self.assertEqual(manifest["devDependencies"]["vite"], "8")

    def test_an_older_project_catches_up_when_a_scene_loads(self):
        """Built-in skills added since the project was made, and a Game UI note in AGENTS.md, told once."""
        cc.add_agent_files(self.root)
        shutil.rmtree(self.root / ".claude" / "skills" / "design-ui")
        (self.root / "AGENTS.md").write_text("# Gem Island\n\nMy rules.\n", encoding="utf-8")
        project.set_project_root_override(self.root)
        with mock.patch.object(cc.environment, "notify") as notify, \
                mock.patch.object(cc, "mcp_node_command", return_value="node"):
            self.assertIn(cc._on_load_post, bpy.app.handlers.load_post)
            cc._on_load_post()
            cc._on_load_post()
        notify.assert_called_once_with("Added the design-ui skill and a Game UI note to AGENTS.md", "INFO")
        self.assertTrue((self.root / ".claude" / "skills" / "design-ui" / "SKILL.md").is_file())
        agents = (self.root / "AGENTS.md").read_text(encoding="utf-8")
        self.assertTrue(agents.startswith("# Gem Island\n\nMy rules.\n\n<!-- gamestudio: game-ui -->\n## Game UI\n"))
        self.assertEqual(agents.count("## Game UI"), 1)

    def test_no_package_json_no_change(self):
        with mock.patch.object(cc.environment, "setup_project") as setup:
            self.assertEqual(cc.ensure_mcp_package(self.root), "")
        setup.assert_not_called()

    def test_the_demo_declares_the_mcp_package(self):
        demo = next((p for p in cc.projects.examples() if p.name == "demo-game"), None)
        if demo is None:
            self.skipTest("no examples next to this GameStudio")
        manifest = json.loads((demo / "package.json").read_text(encoding="utf-8"))
        self.assertIn(cc.MCP_PACKAGE, manifest["devDependencies"], "setup links it into copies of the demo")
        self.assertIsNotNone(cc.mcp_server(json.loads((demo / ".mcp.json").read_text(encoding="utf-8"))))


class McpConfigTests(_TempProject):
    """The project's .mcp.json starts the MCP server wherever Claude Code runs."""

    def setUp(self):
        super().setUp()
        node = self.base / "Blender Foundation" / "gamestudio" / "node" / "node.exe"
        node.parent.mkdir(parents=True)
        node.write_bytes(b"")
        self.NODE = str(node)  # GameStudio's Node.js, by its path

    def test_node_by_name_when_the_system_has_one(self):
        with mock.patch.object(cc.toolchain, "system_node", return_value="/usr/local/bin/node"), \
                mock.patch.object(cc.toolchain, "bundled_node_dir", return_value=None), \
                mock.patch.object(cc.toolchain, "portable_node_dir", return_value=pathlib.Path("/gs/node")):
            self.assertEqual(cc.mcp_node_command(), "node")

    def test_gamestudios_node_by_path_without_one(self):
        status = {"ok": True, "path": "/gs/node/bin/node", "source": "portable", "version": "24.1.0", "problem": ""}
        with mock.patch.object(cc.toolchain, "system_node", return_value=None), \
                mock.patch.object(cc.toolchain, "node_status", return_value=status):
            self.assertEqual(cc.mcp_node_command(), "/gs/node/bin/node")
        with mock.patch.object(cc.toolchain, "system_node", return_value="/gs/editor/node/bin/node"), \
                mock.patch.object(cc.toolchain, "bundled_node_dir", return_value=pathlib.Path("/gs/editor/node")), \
                mock.patch.object(cc.toolchain, "portable_node_dir", return_value=None), \
                mock.patch.object(cc.toolchain, "node_status",
                                  return_value=dict(status, path="/gs/editor/node/bin/node")):
            self.assertEqual(cc.mcp_node_command(), "/gs/editor/node/bin/node",
                             "GameStudio Editor's Node.js is on Blender's PATH only")
        with mock.patch.object(cc.toolchain, "system_node", return_value=None), \
                mock.patch.object(cc.toolchain, "node_status", return_value=dict(status, ok=False, path="")):
            self.assertIsNone(cc.mcp_node_command(), "no Node.js yet: the setup brings it")

    def test_writes_gamestudios_node_once(self):
        self.mcp()
        self.own_script()
        self.assertTrue(cc.sync_mcp_config(self.root, node=self.NODE))
        self.assertEqual(self.server()["command"], self.NODE)
        expected = STARTER_MCP.replace('"command": "node"', '"command": ' + json.dumps(self.NODE))
        self.assertEqual((self.root / ".mcp.json").read_text(encoding="utf-8"), expected,
                         "only the command changes; the file keeps its layout")
        self.assertFalse(cc.sync_mcp_config(self.root, node=self.NODE), "stable from one open to the next")
        self.assertFalse(cc.sync_mcp_config(self.root, node="node"), "a path that works stays")

    def test_keeps_node_by_name(self):
        path = self.mcp()
        self.assertFalse(cc.sync_mcp_config(self.root, node="node"))
        self.assertFalse(cc.sync_mcp_config(self.root, node=None), "no Node.js yet: nothing to name")
        self.assertEqual(path.read_text(encoding="utf-8"), STARTER_MCP)

    def test_repairs_another_computers_path(self):
        theirs = STARTER_MCP.replace('"command": "node"', '"command": "/Users/bob/Library/GameStudio/node/bin/node"')
        self.mcp(theirs)
        self.assertTrue(cc.sync_mcp_config(self.root, node="node"))
        self.assertEqual(self.server()["command"], "node")
        self.mcp(theirs)
        self.assertTrue(cc.sync_mcp_config(self.root, node="/gs/node/bin/node"))
        self.assertEqual(self.server()["command"], "/gs/node/bin/node")
        self.mcp(theirs)
        self.assertTrue(cc.sync_mcp_config(self.root, node=None))
        self.assertEqual(self.server()["command"], "node")

    def test_points_at_the_projects_own_script(self):
        copied = STARTER_MCP.replace(cc.MCP_ENTRY, "../../" + cc.MCP_ENTRY)
        self.mcp(copied)
        self.assertFalse(cc.sync_mcp_config(self.root, node="node"), "nothing better exists yet")
        self.own_script()
        self.assertTrue(cc.sync_mcp_config(self.root, node="node"))
        self.assertEqual(self.server()["args"], [cc.MCP_ENTRY])
        self.assertEqual((self.root / ".mcp.json").read_text(encoding="utf-8"), STARTER_MCP)

    def test_a_working_parent_path_stays(self):
        """A project inside the GameStudio repository uses the repository's node_modules."""
        script = self.base / cc.MCP_ENTRY
        script.parent.mkdir(parents=True)
        script.write_text("", encoding="utf-8")
        self.mcp(STARTER_MCP.replace(cc.MCP_ENTRY, "../" + cc.MCP_ENTRY))
        self.assertFalse(cc.sync_mcp_config(self.root, node="node"))

    def test_leaves_other_servers_and_commands_alone(self):
        config = {"mcpServers": {"gamestudio": {"command": "npx", "args": ["gamestudio-mcp"]},
                                 "other": {"command": "node", "args": ["x.js"]}}}
        path = self.mcp(json.dumps(config))
        self.assertFalse(cc.sync_mcp_config(self.root, node=self.NODE))
        mixed = {"mcpServers": {"other": {"command": "node", "args": ["x.js"], "env": {"A": "1"}},
                                "gamestudio": {"command": "node", "args": [cc.MCP_ENTRY]}}}
        path.write_text(json.dumps(mixed, indent=4), encoding="utf-8")
        self.assertTrue(cc.sync_mcp_config(self.root, node=self.NODE))
        written = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(written["mcpServers"]["other"], mixed["mcpServers"]["other"])
        self.assertEqual(written["mcpServers"]["gamestudio"]["command"], self.NODE)
        self.assertFalse(cc.sync_mcp_config(self.base / "nowhere", node=self.NODE), "no .mcp.json")
        (self.root / ".mcp.json").write_text("{ not json", encoding="utf-8")
        self.assertFalse(cc.sync_mcp_config(self.root, node=self.NODE))

    def test_line_endings_stay(self):
        self.mcp(STARTER_MCP.replace("\n", "\r\n"))
        self.own_script()
        self.assertTrue(cc.sync_mcp_config(self.root, node="/gs/node/bin/node"))
        raw = (self.root / ".mcp.json").read_bytes()
        self.assertEqual(raw.count(b"\r\n"), STARTER_MCP.count("\n"))

    def test_copied_example_gets_its_own_script(self):
        demo = next((p for p in cc.projects.examples() if p.name == "demo-game"), None)
        if demo is None:
            self.skipTest("no examples next to this GameStudio")
        bundle = self.base / "bundle"
        example = bundle / "examples" / "demo-game"
        example.mkdir(parents=True)
        for name in (".mcp.json", "package.json", "gamestudio.json"):
            shutil.copy2(demo / name, example / name)
        packages = self.base / "packages"
        home = self.base / "Examples"
        with mock.patch.object(cc.projects.toolchain, "bundle_root", return_value=bundle), \
                mock.patch.object(cc.projects, "examples_home", return_value=home), \
                mock.patch.object(cc.projects.toolchain, "sdk", return_value=toolchain.Sdk(packages, None)):
            target = cc.projects.prepare_example(example)
        self.assertEqual(target, home / "demo-game")
        server = json.loads((target / ".mcp.json").read_text(encoding="utf-8"))["mcpServers"]["gamestudio"]
        self.assertEqual(server["args"], [cc.MCP_ENTRY], "not the repository's ../../node_modules")
        manifest = json.loads((target / "package.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["devDependencies"][cc.MCP_PACKAGE], "file:" + (packages / "mcp").as_posix())

    def test_templates_get_node_by_name(self):
        text = STARTER_MCP.replace('"command": "node"', '"command": ' + json.dumps(self.NODE))
        text = text.replace(cc.MCP_ENTRY, "../../" + cc.MCP_ENTRY)
        self.assertEqual(cc.portable_mcp_text(text), STARTER_MCP)
        self.assertEqual(cc.portable_mcp_text("not json"), "not json")

    def test_prepare_project_declares_the_package_and_syncs(self):
        self.mcp()
        (self.root / "package.json").write_text(json.dumps({"name": "gem", "devDependencies": {}}), encoding="utf-8")
        packages = self.base / "packages"
        (packages / "mcp").mkdir(parents=True)
        (packages / "mcp" / "package.json").write_text("{}", encoding="utf-8")
        with mock.patch.object(cc.projects, "runtime_packages", return_value=packages), \
                mock.patch.object(cc, "mcp_node_command", return_value="node"):
            self.assertEqual(cc.prepare_project(self.root), [cc.MCP_ADDED])
            self.assertEqual(cc.prepare_project(self.root), [], "once")
        (self.root / ".mcp.json").unlink()
        self.assertEqual(cc.prepare_project(self.root), [], "a project without Claude Code files is left alone")

    def test_checked_when_a_scene_loads(self):
        self.assertIn(cc._on_load_post, bpy.app.handlers.load_post)
        self.mcp(STARTER_MCP.replace('"command": "node"', '"command": "/Users/bob/node/bin/node"'))
        project.set_project_root_override(self.root)
        with mock.patch.object(cc, "mcp_node_command", return_value="node"):
            cc._on_load_post()
        self.assertEqual(self.server()["command"], "node")

    def test_checked_again_after_a_setup(self):
        project.set_project_root_override(self.root)
        job = types.SimpleNamespace(state="done", finished=123.0)
        saved = dict(cc._seen_job)
        try:
            cc._seen_job["finished"] = None
            with mock.patch.object(cc.environment, "JOB", job), \
                    mock.patch.object(cc, "prepare_project", return_value=[cc.MCP_ADDED]) as prepare, \
                    mock.patch.object(cc.environment, "check_later") as later:
                cc._after_setup()
                cc._after_setup()
                job.state = "running"
                cc._after_setup()
            prepare.assert_called_once_with(self.root)
            later.assert_called_once_with(self.root, "load")
        finally:
            cc._seen_job.update(saved)


class _FakeServer:
    running = True

    def __init__(self, clients=0, port=51234, agent="claude-code"):
        self.clients, self.port, self.agent = clients, port, agent

    def client_infos(self):
        agent = {"name": self.agent, "version": "2.1.0"} if self.agent else None
        return [{"client": "gamestudio-mcp 0.4.0", "agent": agent, "connected_at": 0.0}] * self.clients


def fake_bridge(clients=None, agent="claude-code"):
    """Patch the bridge: None = off, else a running server with that many clients of that agent."""
    server = None if clients is None else _FakeServer(clients, agent=agent)
    return mock.patch.multiple(bridge, get_server=lambda: server, is_running=lambda: server is not None)


class BridgeStatusTests(_TempProject):
    def test_states(self):
        with fake_bridge(None):
            self.assertEqual(cc.bridge_state(), ("off", 0, 0))
            self.assertIsNone(cc.status_item(), "off on purpose, no project: nothing to say")
            project.set_project_root_override(self.root)
            self.assertEqual(cc.status_item(), ("Agent bridge off", "UNLINKED", False))
        with fake_bridge(0):
            self.assertEqual(cc.bridge_state(), ("listening", 51234, 0))
            self.assertEqual(cc.status_item(), ("Claude Code not connected", "UNLINKED", False))
            self.assertEqual(cc.bridge_status(), ("Waiting for Claude Code", "UNLINKED", False))
        with fake_bridge(1):
            self.assertEqual(cc.status_item(), ("Claude Code connected", "LINKED", True))
            self.assertEqual(cc.bridge_status(), ("Claude Code connected", "LINKED", True))
        with fake_bridge(2):
            self.assertEqual(cc.status_item()[0], "Claude Code connected (2)")
        # Who is connected comes from the bridge hello: the MCP client, else the bridge client itself.
        with fake_bridge(1, agent="cursor-vscode"):
            self.assertEqual(cc.status_item(), ("cursor-vscode connected", "LINKED", True))
        with fake_bridge(1, agent=None):
            self.assertEqual(cc.bridge_status()[0], "gamestudio-mcp connected")

    def test_status_bar(self):
        """One item says who is connected and what the agent does (activity_ui's), not two."""
        activity_ui = importlib.import_module(f"{EXTENSION}.activity_ui")
        header = bpy.types.STATUSBAR_HT_header
        self.assertFalse(hasattr(cc, "draw_statusbar"))
        self.assertIn(activity_ui.draw_statusbar, header.draw._draw_funcs)
        for clients, expected in ((None, []), (0, ["Claude not connected"]), (1, ["Claude: idle"])):
            texts = []
            with fake_bridge(clients):
                activity_ui.draw_statusbar(te._FakeSelf(header, ux.Recorder(self, texts=texts)), te.fake_context())
            self.assertEqual(texts, expected)

    def test_status_tick_redraws_on_change(self):
        try:
            with fake_bridge(0), mock.patch.object(cc, "tag_redraw") as redraw:
                cc._shown["item"] = None
                self.assertEqual(cc._status_tick(), 0.5)
                cc._status_tick()
            self.assertEqual(redraw.call_count, 1)
            with fake_bridge(1), mock.patch.object(cc, "tag_redraw") as redraw:
                cc._status_tick()
            self.assertEqual(redraw.call_count, 1, "a client connected")
        finally:
            cc._shown["item"] = None

    def test_ensure_bridge(self):
        state = {"running": False}
        with mock.patch.multiple(bridge, is_running=lambda: state["running"],
                                 start=lambda force=False: state.update(running=True)):
            self.assertEqual(cc.ensure_bridge(), "")
            self.assertTrue(state["running"])
        with mock.patch.multiple(bridge, is_running=lambda: False, start=lambda force=False: None):
            self.assertIn("could not start", cc.ensure_bridge())


class OperatorTests(_TempProject):
    OPERATORS = ("open_claude_code", "claude_code_install", "claude_code_install_cancel", "claude_started",
                 "copy_starter_prompt", "copy_install_command", "bridge_enable")

    def test_operators_exist(self):
        for name in self.OPERATORS:
            self.assertTrue(hasattr(bpy.ops.gamestudio, name), name)

    def test_needs_a_project(self):
        project.set_project_root_override(None)
        with mock.patch.object(project, "project_root", return_value=None):
            self.assertFalse(bpy.ops.gamestudio.open_claude_code.poll())
        project.set_project_root_override(self.root)
        self.assertTrue(bpy.ops.gamestudio.open_claude_code.poll())

    def test_open_adds_files_starts_the_bridge_and_opens_the_terminal(self):
        project.set_project_root_override(self.root)
        spawned = []
        with mock.patch.object(cc, "claude_path", return_value=WIN_CLAUDE), \
                mock.patch.object(cc, "_spawn", lambda launch, env: spawned.append((launch, env))), \
                mock.patch.object(cc, "ensure_bridge", return_value="") as ensure:
            self.assertEqual(bpy.ops.gamestudio.open_claude_code(), {"FINISHED"})
        ensure.assert_called_once()
        self.assertEqual(len(spawned), 1)
        launch, env = spawned[0]
        self.assertEqual(pathlib.Path(launch.cwd), self.root)
        script = pathlib.Path(launch.script)
        self.assertEqual(script.parent, self.root / ".gamestudio")
        self.assertTrue(script.is_file(), "the launcher script is written before the terminal starts")
        self.assertIn(WIN_CLAUDE, script.read_bytes().decode("utf-8"))
        if os.name != "nt":
            self.assertTrue(os.access(script, os.X_OK))
        self.assertIn("PATH", {k.upper() for k in env})
        self.assertEqual(cc.missing_agent_files(self.root), [])

    def test_open_without_changes_when_unticked(self):
        project.set_project_root_override(self.root)
        with mock.patch.object(cc, "claude_path", return_value=WIN_CLAUDE), \
                mock.patch.object(cc, "_spawn", lambda launch, env: None), \
                mock.patch.object(cc, "ensure_bridge") as ensure:
            bpy.ops.gamestudio.open_claude_code(add_files=False, start_bridge=False)
        ensure.assert_not_called()
        self.assertEqual(len(cc.missing_agent_files(self.root)), 4)

    def test_open_syncs_mcp_json_before_the_terminal(self):
        project.set_project_root_override(self.root)
        self.mcp(STARTER_MCP.replace('"command": "node"', '"command": "/Users/bob/node/bin/node"'))
        seen = []
        with mock.patch.object(cc, "claude_path", return_value=WIN_CLAUDE), \
                mock.patch.object(cc, "mcp_node_command", return_value="node"), \
                mock.patch.object(cc, "_spawn", lambda launch, env: seen.append(self.server()["command"])), \
                mock.patch.object(cc, "ensure_bridge", return_value=""):
            bpy.ops.gamestudio.open_claude_code(add_files=False)
        self.assertEqual(seen, ["node"])

    def test_open_without_claude_does_nothing(self):
        project.set_project_root_override(self.root)
        with mock.patch.object(cc, "claude_path", return_value=None), \
                mock.patch.object(cc, "_spawn") as spawn, self.assertRaises(RuntimeError):  # its ERROR report
            bpy.ops.gamestudio.open_claude_code()
        spawn.assert_not_called()

    def test_terminal_failure_is_reported(self):
        project.set_project_root_override(self.root)

        def fail(launch, env):
            raise OSError("no such program")

        with mock.patch.object(cc, "claude_path", return_value=WIN_CLAUDE), mock.patch.object(cc, "_spawn", fail), \
                mock.patch.object(cc, "ensure_bridge", return_value=""):
            with self.assertRaises(RuntimeError):  # bpy.ops raises on an ERROR report
                bpy.ops.gamestudio.open_claude_code(add_files=False)

    def test_copy_starter_prompt(self):
        copied = []
        with mock.patch.object(cc, "_set_clipboard", lambda context, text: copied.append(text)):
            for index in range(len(cc.STARTER_PROMPTS)):
                bpy.ops.gamestudio.copy_starter_prompt(index=index)
            bpy.ops.gamestudio.copy_install_command(index=0)
            self.assertEqual(bpy.ops.gamestudio.copy_starter_prompt(index=99), {"CANCELLED"})
        self.assertEqual(copied[:-1], [prompt for _label, prompt in cc.STARTER_PROMPTS])
        self.assertEqual(copied[-1], cc.install_commands()[0][1])
        self.assertTrue(cc.GAMESTUDIO_OT_copy_starter_prompt.description(None, types.SimpleNamespace(index=1))
                        .startswith("Copy: Add a pickup"))

    def _install_patches(self, problem="", online=True):
        return (mock.patch.object(cc, "claude_path", return_value=None),
                mock.patch.object(cc, "install_problem", return_value=problem),
                mock.patch.object(cc.environment, "online", return_value=online),
                mock.patch.object(cc, "_allow_online"),
                mock.patch.object(cc, "start_install", return_value=""),
                mock.patch.object(cc, "_open_url"))

    def test_install_runs_the_installer(self):
        patches = self._install_patches()
        with patches[0], patches[1], patches[2], patches[3], patches[4] as start, patches[5] as open_url:
            self.assertEqual(bpy.ops.gamestudio.claude_code_install(), {"FINISHED"})
            start.assert_called_once_with(None)
            start.reset_mock()
            self.assertEqual(bpy.ops.gamestudio.claude_code_install(open_after=True), {"FINISHED"})
            start.assert_called_once_with(cc._open_after_install)
        open_url.assert_not_called()

    def test_install_guides_where_it_cannot_run(self):
        patches = self._install_patches(problem="The installer needs curl")
        with patches[0], patches[1], patches[2], patches[3], patches[4] as start, patches[5] as open_url:
            self.assertEqual(bpy.ops.gamestudio.claude_code_install(), {"FINISHED"})
        start.assert_not_called()
        open_url.assert_called_once_with(cc.DOCS_URL)

    def test_install_offline_asks_for_online_access(self):
        patches = self._install_patches(online=False)
        with patches[0], patches[1], patches[2], patches[3] as allow, patches[4] as start, patches[5]:
            with self.assertRaises(RuntimeError):  # still offline: its ERROR report
                bpy.ops.gamestudio.claude_code_install()
        allow.assert_called_once()
        start.assert_not_called()

    def test_installed_already(self):
        with mock.patch.object(cc, "claude_path", return_value=WIN_CLAUDE), \
                mock.patch.object(cc, "start_install") as start:
            self.assertEqual(bpy.ops.gamestudio.claude_code_install(), {"FINISHED"})
        start.assert_not_called()

    def test_open_goes_on_after_the_install(self):
        project.set_project_root_override(self.root)
        with mock.patch.object(cc.bpy.app.timers, "register") as register:
            cc._open_after_install(False, "failed")
            register.assert_not_called()
            cc._open_after_install(True, "")
            register.assert_called_once()

    def test_dialogs_draw(self):
        project.set_project_root_override(self.root)
        for cls in (cc.GAMESTUDIO_OT_open_claude_code, cc.GAMESTUDIO_OT_claude_code_install,
                    cc.GAMESTUDIO_OT_claude_started):
            layout = ux.Recorder(self)
            fake = te._FakeSelf(cls, layout)
            category, name = cls.bl_idname.split(".")
            fake.bl_rna = getattr(getattr(bpy.ops, category), name).get_rna_type()  # its properties
            with fake_bridge(None), mock.patch.object(cc, "install_problem", return_value=""):
                cls.draw(fake, te.fake_context())
            self.assertTrue(layout.texts, cls.__name__)
            if cls is cc.GAMESTUDIO_OT_open_claude_code:
                self.assertIn("Adds instructions for this game, Claude's skills and the link to Blender.",
                              layout.texts)
                self.assertIn("Files the project already has stay as they are.", layout.texts)
                self.assertFalse(any("Missing:" in text or "@gamestudio" in text for text in layout.texts),
                                 "plain words")
            if cls is cc.GAMESTUDIO_OT_claude_code_install:
                self.assertIn(f"The installer: {cc.install_commands()[0][1]}", layout.texts)
                self.assertIn("wm.url_open", ux.ids(layout), "the installation guide")
            if cls is cc.GAMESTUDIO_OT_claude_started:
                self.assertEqual(ux.ids(layout).count("gamestudio.copy_starter_prompt"), len(cc.STARTER_PROMPTS))
        layout = ux.Recorder(self)
        fake = te._FakeSelf(cc.GAMESTUDIO_OT_claude_code_install, layout)
        with mock.patch.object(cc, "install_problem", return_value="The installer needs curl, which ..."):
            cc.GAMESTUDIO_OT_claude_code_install.draw(fake, te.fake_context())
        self.assertIn("gamestudio.copy_install_command", ux.ids(layout), "the guided path keeps the commands")

    def test_connect_text(self):
        self.assertEqual(cc.connect_text([cc.MCP_PACKAGE]), "Adds the link to Blender.")
        self.assertEqual(cc.connect_text([]), "")


class MenuTests(_TempProject):
    def test_gamestudio_menu(self):
        with fake_bridge(1):
            layout = ux.draw_menu(self, "GAMESTUDIO_MT_projects")
        found = ux.ids(layout)
        self.assertIn("gamestudio.open_claude_code", found)
        self.assertIn("GAMESTUDIO_MT_claude_prompts", found)
        self.assertIn("Claude Code connected", layout.texts)
        self.assertLess(found.index("gamestudio.new_system"), found.index("gamestudio.open_claude_code"))
        self.assertLess(found.index("gamestudio.open_claude_code"), found.index("gamestudio.add_workspaces"))
        with fake_bridge(None):
            self.assertIn("gamestudio.bridge_enable", ux.ids(ux.draw_menu(self, "GAMESTUDIO_MT_projects")))

    def test_prompts_menu(self):
        layout = ux.draw_menu(self, "GAMESTUDIO_MT_claude_prompts")
        self.assertEqual(layout.texts, [label for label, _prompt in cc.STARTER_PROMPTS])
        self.assertEqual(set(ux.ids(layout)), {"gamestudio.copy_starter_prompt"})

    def test_project_panel(self):
        overview = [cls for cls in te.host.editors.registered_classes("GAMESTUDIO_PROJECT")
                    if cls.gs_spec.id == "overview"]
        self.assertEqual(len(overview), 1, "the Project tab's first panel")
        panel = overview[0]
        self.assertIn(cc.draw_project_panel, panel.draw._draw_funcs)
        self.assertNotIn(cc.draw_project_panel, bpy.types.GAMESTUDIO_PT_project.draw.__dict__.get(
            "_draw_funcs", ()), "ui.py's panels stay as they are")
        layout = ux.Recorder(self)
        cc.draw_project_panel(te._FakeSelf(panel, layout), te.fake_context())
        self.assertEqual(layout.calls, [], "nothing without a project")
        project.set_project_root_override(self.root)
        with fake_bridge(0), mock.patch.object(cc, "claude_path", return_value=None):
            cc.draw_project_panel(te._FakeSelf(panel, layout), te.fake_context())
        found = ux.ids(layout)
        for idname in ("gamestudio.open_claude_code", "GAMESTUDIO_MT_claude_prompts", "gamestudio.claude_code_install"):
            self.assertIn(idname, found)
        self.assertIn("Not connected to Claude yet: Open in Claude Code connects it", layout.texts)
        self.assertIn("Install...", layout.texts)

    def test_launcher_and_welcome(self):
        with mock.patch.object(cc, "claude_path", return_value=None):
            layout = ux.Recorder(self)
            launcher.draw_launcher(layout, te.fake_context())
        self.assertIn("Claude Code is not installed", layout.texts)
        self.assertIn("gamestudio.claude_code_install", ux.ids(layout))
        self.assertNotIn("gamestudio.open_claude_code", ux.ids(layout), "only with a project open")

        demo = self.base / "demo-game"
        with mock.patch.object(launcher.projects, "examples", return_value=[self.root, demo]), \
                mock.patch.object(cc, "claude_path", return_value=WIN_CLAUDE):
            layout = ux.Recorder(self)
            launcher.draw_welcome(layout, te.fake_context())
        self.assertIn("Welcome to GameStudio", layout.texts)
        for number, (title, text, _icon) in enumerate(launcher.WELCOME_STEPS, 1):
            self.assertIn(f"{number}. {title}", layout.texts)
            self.assertIn(text, layout.texts)
        for text in ("New Project...", "Open Demo"):
            self.assertIn(text, layout.texts)
        found = ux.ids(layout)
        self.assertIn("gamestudio.open_claude_code", found)
        self.assertIn("GAMESTUDIO_MT_claude_prompts", found)
        self.assertIn(f"Claude Code: {WIN_CLAUDE}", layout.texts)

    def test_demo_project(self):
        demo = self.base / "demo-game"
        for found, expected in (([self.root, demo], demo), ([self.root], self.root), ([], None)):
            with mock.patch.object(launcher.projects, "examples", return_value=found):
                self.assertEqual(launcher.demo_project(), expected)

    def test_first_enable_is_told_from_startup(self):
        """Add-ons register without a window; a file load right after means Blender is starting."""
        self.assertNotIn(launcher._file_loaded, bpy.app.handlers.load_post, "never in --background")
        saved = dict(launcher._state)
        try:
            launcher._state["interactive"] = True
            bpy.app.handlers.load_post.append(launcher._file_loaded)
            launcher._file_loaded()
            self.assertFalse(launcher._state["interactive"])
            self.assertNotIn(launcher._file_loaded, bpy.app.handlers.load_post, "one-shot")
        finally:
            launcher._state.update(saved)

    def test_welcome_opens_where_the_person_is(self):
        def window(*types_):
            return types.SimpleNamespace(screen=types.SimpleNamespace(areas=[types.SimpleNamespace(type=t)
                                                                             for t in types_]))

        main, prefs = window("VIEW_3D", "OUTLINER"), window("PREFERENCES")
        self.assertIs(launcher._launch_window(types.SimpleNamespace(windows=[main, prefs])), prefs)
        self.assertIs(launcher._launch_window(types.SimpleNamespace(windows=[main])), main)

    def test_welcome_opens_once_as_the_welcome(self):
        props = bpy.ops.gamestudio.launcher.get_rna_type().properties
        self.assertIn("welcome", props.keys())
        self.assertTrue(props["welcome"].is_skip_save)

    def test_preferences_are_grouped(self):
        prefs = bpy.context.preferences.addons[EXTENSION].preferences
        self.assertEqual(prefs.claude_terminal_command, "")
        texts = []
        with mock.patch("rna_keymap_ui.draw_kmi"):
            type(prefs).draw(te._FakeSelf(prefs, te.MockLayout(self, texts)), te.fake_context())
        for title in ("GameStudio", "Coding agent", "Agent bridge", "Setup", "Play", "Code editor",
                      "Play Shortcuts", "AI assistant (optional)"):
            self.assertIn(title, texts)
        self.assertEqual(prefs.coding_agent, "CLAUDE_CODE", "Claude Code by default")
        names = set(prefs.bl_rna.properties.keys())
        for name in ("show_launcher_at_startup", "welcomed", "npm_command", "open_game_window", "app_window",
                     "live_geometry", "code_editor", "code_editor_command", "assistant_enabled", "api_key",
                     "model", "effort", "max_turns", "bridge_enabled", "claude_terminal_command", "auto_setup",
                     "coding_agent"):
            self.assertIn(name, names, "preference names are kept")


def load_tests(loader, _tests=None, _pattern=None):
    suite = unittest.TestSuite()
    for case in (DetectionTests, InstallCommandTests, InstallerTests, TerminalTests, AgentFileTests, McpConfigTests,
                 BridgeStatusTests, OperatorTests, MenuTests):
        suite.addTests(loader.loadTestsFromTestCase(case))
    return suite
