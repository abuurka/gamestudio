# SPDX-License-Identifier: GPL-3.0-or-later
"""Open in Codex: finding the CLI, installing it, the launcher script and terminal per OS, the project's
.codex/config.toml (written, merged, repaired, made portable), Codex's copy of the skills, AGENTS.md,
the operators and dialogs, and the "Coding agent" preference that the launcher, the welcome, the
Project panel, the status bar and the top bar follow.

Runs inside run_shell_tests.py (extension enabled, --background). Nothing here opens a terminal, runs
codex or runs Codex's real installer: detection gets fake `which`/`isfile` functions, the operator's
spawn is patched, and the installer's process is a small Python stand-in.
"""

import importlib
import os
import pathlib
import shlex
import shutil
import subprocess
import tempfile
import time
import tomllib
import types
import unittest
from unittest import mock

import bpy

import test_claude_code as tcc
import test_editors as te
import test_shell_ux as ux

EXTENSION = te.EXTENSION

cc = importlib.import_module(f"{EXTENSION}.claude_code")
cx = importlib.import_module(f"{EXTENSION}.codex")
launcher = importlib.import_module(f"{EXTENSION}.launcher")
activity_ui = importlib.import_module(f"{EXTENSION}.activity_ui")
bridge = importlib.import_module(f"{EXTENSION}.bridge")
environment = importlib.import_module(f"{EXTENSION}.environment")
projects = importlib.import_module(f"{EXTENSION}.projects")
agentfiles = importlib.import_module(f"{EXTENSION}.core.agentfiles")
project = te.project

which_of, isfile_of = tcc.which_of, tcc.isfile_of
WIN_HOME = "C:\\Users\\Jane Doe"
WIN_LOCAL = WIN_HOME + "\\AppData\\Local"
WIN_CODEX = WIN_LOCAL + "\\Programs\\OpenAI\\Codex\\bin\\codex.exe"
WIN_PROJECT = "C:\\My Games\\Gem Island"
WT = tcc.WT
CMD = tcc.CMD
STARTER_MCP = tcc.STARTER_MCP


def prefs():
    return bpy.context.preferences.addons[EXTENSION].preferences


class _Agent(unittest.TestCase):
    """Sets the "Coding agent" preference for a test and puts it back."""

    def use(self, key: str) -> None:
        saved = prefs().coding_agent
        prefs().coding_agent = key
        self.addCleanup(setattr, prefs(), "coding_agent", saved)


class DetectionTests(unittest.TestCase):
    def test_path_comes_first(self):
        found = cx.find_codex("win32", env={}, home=WIN_HOME, which=which_of({"codex": "D:\\bin\\codex.EXE"}),
                              isfile=isfile_of([WIN_CODEX]))
        self.assertEqual(found, "D:\\bin\\codex.exe")

    def test_windows_standalone_installer(self):
        env = {"USERPROFILE": WIN_HOME, "LOCALAPPDATA": WIN_LOCAL}
        self.assertEqual(cx.find_codex("win32", env=env, which=which_of({}), isfile=isfile_of([WIN_CODEX])), WIN_CODEX)
        self.assertEqual(cx.install_locations("windows", WIN_HOME, env)[0], WIN_CODEX, "the official installer first")
        self.assertEqual(cx.find_codex("win32", env={"USERPROFILE": WIN_HOME}, which=which_of({}),
                                       isfile=isfile_of([WIN_CODEX])), WIN_CODEX, "LOCALAPPDATA's default")
        moved = "D:\\Tools\\Codex\\codex.exe"
        places = cx.install_locations("windows", WIN_HOME, dict(env, CODEX_INSTALL_DIR="D:\\Tools\\Codex"))
        self.assertEqual(places[0], moved, "CODEX_INSTALL_DIR moves it")

    def test_windows_npm(self):
        env = {"USERPROFILE": WIN_HOME, "LOCALAPPDATA": WIN_LOCAL, "APPDATA": WIN_HOME + "\\AppData\\Roaming",
               "NPM_CONFIG_PREFIX": "D:\\npm-global"}
        places = cx.install_locations("windows", WIN_HOME, env)
        npm = WIN_HOME + "\\AppData\\Roaming\\npm\\codex.cmd"
        self.assertIn(npm, places)
        self.assertIn("D:\\npm-global\\codex.cmd", places)
        self.assertEqual(cx.find_codex("win32", env=env, which=which_of({}), isfile=isfile_of([npm])), npm)
        self.assertIsNone(cx.find_codex("win32", env=env, which=which_of({}), isfile=isfile_of([])))

    def test_macos_and_linux(self):
        home = "/Users/jane"
        mac = cx.install_locations("macos", home, {})
        self.assertEqual(mac[0], "/Users/jane/.local/bin/codex", "the standalone installer first")
        self.assertIn("/opt/homebrew/bin/codex", mac)
        self.assertIn("/Users/jane/.npm-global/bin/codex", mac)
        self.assertEqual(cx.find_codex("darwin", env={}, home=home, which=which_of({}),
                                       isfile=isfile_of(["/opt/homebrew/bin/codex"])), "/opt/homebrew/bin/codex")
        linux = cx.install_locations("linux", "/home/jane", {"CODEX_INSTALL_DIR": "/opt/codex"})
        self.assertEqual(linux[:2], ["/opt/codex/codex", "/home/jane/.local/bin/codex"])
        self.assertEqual(cx.find_codex("linux", env={}, home="/home/jane", which=which_of({"codex": "/usr/bin/codex"})),
                         "/usr/bin/codex")

    def test_cached(self):
        try:
            with mock.patch.object(cx, "find_codex", return_value="/a/codex") as find:
                self.assertEqual(cx.codex_path(refresh=True), "/a/codex")
                cx.codex_path()
                find.assert_called_once()
        finally:
            cx._cache["time"] = -cx._DETECT_TTL


class InstallCommandTests(unittest.TestCase):
    """What Install Codex runs: OpenAI's standalone installer, through the platform's shell."""

    def test_windows_runs_it_in_powershell(self):
        builtin = "C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe"
        argv = cx.install_argv("win32", which=which_of({}), isfile=isfile_of([builtin]),
                               env={"SystemRoot": "C:\\Windows"})
        self.assertEqual(argv, [builtin, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "ByPass", "-Command",
                                "irm https://chatgpt.com/codex/install.ps1 | iex"])
        argv = cx.install_argv("win32", which=which_of({"pwsh.exe": "D:\\pwsh\\pwsh.exe"}), isfile=isfile_of([]),
                               env={})
        self.assertEqual(argv[0], "D:\\pwsh\\pwsh.exe")
        self.assertIsNone(cx.install_argv("win32", which=which_of({}), isfile=isfile_of([]), env={}))
        self.assertIn("PowerShell", cx.install_problem("win32", which=which_of({}), isfile=isfile_of([]), env={}))

    def test_macos_and_linux_run_it_in_bash(self):
        for platform in ("darwin", "linux"):
            argv = cx.install_argv(platform, which=which_of({"curl": "/usr/bin/curl"}), isfile=isfile_of(["/bin/bash"]))
            self.assertEqual(argv, ["/bin/bash", "-o", "pipefail", "-c",
                                    "curl -fsSL https://chatgpt.com/codex/install.sh | sh"], platform)
        problem = cx.install_problem("linux", which=which_of({}), isfile=isfile_of(["/bin/bash"]))
        self.assertIn("sudo apt install curl", problem)

    def test_the_commands_for_installing_by_hand(self):
        self.assertEqual(cx.install_commands("win32")[0],
                         ("PowerShell", 'powershell -ExecutionPolicy ByPass -c '
                                        '"irm https://chatgpt.com/codex/install.ps1 | iex"'))
        self.assertIn(("Homebrew", "brew install --cask codex"), cx.install_commands("darwin"))
        for platform in ("win32", "darwin", "linux"):
            self.assertFalse([c for _shell, c in cx.install_commands(platform) if "npm" in c],
                             "no npm: it needs a Node.js on PATH")
            self.assertTrue(100 <= cx.install_size_mb(platform) <= 250, platform)

    def test_installer_environment(self):
        base = {"PATH": "x", "TEMP": "C:\\Temp"}
        env = cx.install_env(pathlib.Path("F:/scratch/codex-install-tmp"), base)
        self.assertEqual(env["CODEX_NON_INTERACTIVE"], "1", "it never waits for an answer")
        for name in ("TEMP", "TMP", "TMPDIR"):
            self.assertEqual(pathlib.Path(env[name]), pathlib.Path("F:/scratch/codex-install-tmp"), name)
        self.assertEqual(base["TEMP"], "C:\\Temp", "the base is not changed")

    def test_start_install_explains_when_it_cannot(self):
        with mock.patch.object(cx, "install_argv", return_value=None), \
                mock.patch.object(cx, "install_problem", return_value="no curl"):
            self.assertEqual(cx.start_install(), "no curl")
        self.assertFalse(cx.INSTALLER.running())

    def test_start_install_runs_it_quietly(self):
        temp = pathlib.Path(tempfile.mkdtemp(prefix="gs_codex_start_")) / "codex-install-tmp"
        self.addCleanup(shutil.rmtree, temp.parent, True)
        started = []
        with mock.patch.object(cx, "install_argv", return_value=["the installer"]), \
                mock.patch.object(cx, "install_temp_dir", return_value=temp), \
                mock.patch.object(cx.INSTALLER, "start",
                                  side_effect=lambda *args, **kwargs: started.append(args) or True):
            self.assertEqual(cx.start_install(), "")
        argv, log_path, _home, env, _then = started[0]
        self.assertEqual(argv, ["the installer"])
        self.assertEqual(log_path.name, cx.INSTALL_LOG)
        self.assertEqual(env["CODEX_NON_INTERACTIVE"], "1")
        self.assertEqual(env["TEMP"], str(temp))
        self.assertTrue(temp.is_dir())


def fake_installer(script: str):
    return tcc.fake_installer(script)


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.home = pathlib.Path(tempfile.mkdtemp(prefix="gs_codex_install_"))
        self.log = self.home / "gamestudio" / cx.INSTALL_LOG
        self.temp = self.home / "gamestudio" / cx.INSTALL_TMP
        self.temp.mkdir(parents=True)

    def tearDown(self):
        cx.INSTALLER.cancel()
        deadline = time.monotonic() + 20
        while cx.INSTALLER.running() and time.monotonic() < deadline:
            time.sleep(0.1)
        if bpy.app.timers.is_registered(cx._install_tick):
            bpy.app.timers.unregister(cx._install_tick)
        cx.INSTALLER.__init__()
        cx._cache["time"] = -cx._DETECT_TTL
        shutil.rmtree(self.home, ignore_errors=True)

    def run_install(self, script: str, found, watch=None) -> list:
        done = []
        with mock.patch.object(cx, "_popen_installer", fake_installer(script)), \
                mock.patch.object(cx, "find_codex", return_value=found), \
                mock.patch.object(cx, "install_temp_dir", return_value=self.temp), \
                mock.patch.object(cx.environment, "notify") as notify:
            self.assertTrue(cx.INSTALLER.start(["the installer"], self.log, self.home, dict(os.environ),
                                               then=lambda ok, message: done.append((ok, message))))
            self.assertTrue(bpy.app.timers.is_registered(cx._install_tick), "Codex's own timer")
            self.assertFalse(cx.INSTALLER.start(["again"], self.log, self.home, dict(os.environ)), "one at a time")
            self.assertFalse(cc.INSTALLER.running(), "Claude Code's installer is another")
            deadline = time.monotonic() + 60
            while cx.INSTALLER.running() and time.monotonic() < deadline:
                if watch is not None:
                    watch()
                time.sleep(0.05)
            self.assertFalse(cx.INSTALLER.running(), "the stand-in installer ended")
            cx._install_tick()  # what the timer does on the main thread
        self.notified = notify.call_args_list
        return done

    def test_success_with_download_progress(self):
        # The standalone installer downloads into <TEMP>/codex-install-<guid>/ and extracts beside it.
        folder = (self.temp / "codex-install-1234").as_posix()
        script = ("import os, time, pathlib\n"
                  f"folder = pathlib.Path({folder!r}); folder.mkdir(parents=True, exist_ok=True)\n"
                  "print('==> Installing Codex CLI', flush=True)\n"
                  "with open(folder / 'codex-package-x86_64-pc-windows-msvc.tar.gz', 'wb') as out:\n"
                  "    for _ in range(8):\n"
                  "        out.write(b'x' * 4000000); out.flush(); time.sleep(0.15)\n"
                  "(folder / 'extract').mkdir(); (folder / 'extract' / 'codex.exe').write_bytes(b'y' * 9000000)\n"
                  "print('==> Codex CLI 0.162.1 installed successfully.', flush=True)\n")
        seen = set()
        done = self.run_install(script, found=WIN_CODEX, watch=lambda: seen.add(cx.INSTALLER.message.split(":")[0]))
        self.assertIn("Downloading Codex", seen)
        self.assertEqual(done, [(True, f"Codex is installed ({WIN_CODEX})")])
        self.assertEqual(cx.INSTALLER.path, WIN_CODEX)
        self.assertIn("installed successfully", self.log.read_text(encoding="utf-8"))
        self.assertEqual(cx.install_status()[:2], ("Codex is installed", "CHECKMARK"))
        self.assertIn(mock.call(cx.INSTALLER.message, "INFO"), self.notified)
        self.assertFalse(self.temp.exists(), "the installer's temporary folder is removed")

    def test_offline_says_so(self):
        script = ("import sys\nprint(\"Invoke-WebRequest : The remote name could not be resolved: "
                  "'releases.openai.com'\")\nsys.exit(1)\n")
        done = self.run_install(script, found=None)
        self.assertEqual(done, [(False, "Codex could not be downloaded: chatgpt.com could not be reached. Check the "
                                        "internet connection, then Try Again")])
        self.assertEqual(cx.install_status(), ("Codex was not installed", "ERROR", True))
        layout = ux.Recorder(self)
        with mock.patch.object(cx, "codex_path", return_value=None):
            cx.draw_cli_status(layout)
        self.assertIn("gamestudio.codex_install", ux.ids(layout), "Try Again")
        self.assertIn("gamestudio.open_path", ux.ids(layout), "Show Log")

    def test_failure_says_why(self):
        script = "import sys\nprint('Error: Codex requires a 64-bit Windows.')\nsys.exit(1)\n"
        done = self.run_install(script, found=None)
        self.assertEqual(done, [(False, "Codex's installer failed (exit code 1): Error: Codex requires a 64-bit "
                                        "Windows.")])

    def test_finished_but_not_found(self):
        done = self.run_install("print('done')\n", found=None)
        self.assertFalse(done[0][0])
        self.assertIn("cannot find codex", done[0][1])

    def test_cancel(self):
        done = []
        with mock.patch.object(cx, "_popen_installer", fake_installer("import time\ntime.sleep(60)\n")), \
                mock.patch.object(cx, "find_codex", return_value=None), \
                mock.patch.object(cx, "install_temp_dir", return_value=self.temp):
            cx.INSTALLER.start(["the installer"], self.log, self.home, dict(os.environ),
                               then=lambda ok, message: done.append((ok, message)))
            time.sleep(0.5)
            self.assertTrue(bpy.ops.gamestudio.codex_install_cancel.poll())
            bpy.ops.gamestudio.codex_install_cancel()
            deadline = time.monotonic() + 30
            while cx.INSTALLER.running() and time.monotonic() < deadline:
                time.sleep(0.1)
            cx._install_tick()
        self.assertEqual(done, [(False, "Cancelled")])
        self.assertIsNone(cx.install_status())

    def test_status_bar_while_installing(self):
        header = bpy.types.STATUSBAR_HT_header
        self.assertIn(cx.draw_install_status, header.draw._draw_funcs)
        self.assertIn(cx.draw_install_status, cc.INSTALL_STATUS_DRAWS, "kept in the status bar by the status tick")
        cx.INSTALLER.state, cx.INSTALLER.message, cx.INSTALLER.progress = "running", "Downloading Codex", 0.5
        layout = ux.Recorder(self)
        cx.draw_install_status(te._FakeSelf(header, layout), te.fake_context())
        self.assertEqual(layout.texts, ["Downloading Codex (50%)"])
        self.assertIn("gamestudio.codex_install_cancel", ux.ids(layout))
        menu = ux.draw_menu(self, "GAMESTUDIO_MT_projects")
        self.assertIn("gamestudio.codex_install_cancel", ux.ids(menu))
        self.assertNotIn("gamestudio.claude_code_install_cancel", ux.ids(menu))


class TerminalTests(unittest.TestCase):
    def test_windows_terminal(self):
        launch = cc.terminal_command(WIN_PROJECT, WIN_CODEX, "win32", which=which_of({"wt.exe": WT}), env={},
                                     agent=cc.CODEX)
        self.assertEqual(launch.argv, [WT, "-d", WIN_PROJECT, CMD, "/k", ".gamestudio\\codex.cmd"])
        self.assertEqual(launch.script, WIN_PROJECT + "\\.gamestudio\\codex.cmd")
        lines = launch.script_text.split("\r\n")
        self.assertIn("title Codex", lines)
        self.assertIn('cd /d "%~dp0.."', lines)
        self.assertIn(f'"{WIN_CODEX}"', lines)

    def test_windows_script_puts_gamestudios_node_first(self):
        env = {"LOCALAPPDATA": WIN_LOCAL, "APPDATA": WIN_HOME + "\\AppData\\Roaming", "USERPROFILE": WIN_HOME}
        node = WIN_HOME + "\\AppData\\Roaming\\Blender Foundation\\Blender\\5.2\\extensions\\.user\\gamestudio\\node"
        text = cc.launch_script("win32", WIN_CODEX, path_prepend=node, env=env, agent=cc.CODEX)
        lines = text.split("\r\n")
        self.assertIn('set "PATH=%APPDATA%\\Blender Foundation\\Blender\\5.2\\extensions\\.user\\gamestudio\\node;'
                      '%PATH%"', lines)
        self.assertIn('"%LOCALAPPDATA%\\Programs\\OpenAI\\Codex\\bin\\codex.exe"', lines)
        self.assertLess(lines.index('cd /d "%~dp0.."'), len(lines) - 2)
        npm = cc.launch_script("win32", WIN_HOME + "\\AppData\\Roaming\\npm\\codex.cmd", env={}, agent=cc.CODEX)
        self.assertIn(f'call "{WIN_HOME}\\AppData\\Roaming\\npm\\codex.cmd"', npm.split("\r\n"),
                      "npm's codex.cmd is called")

    def test_command_prompt_without_windows_terminal(self):
        launch = cc.terminal_command(WIN_PROJECT, WIN_CODEX, "win32", which=which_of({}), env={}, agent=cc.CODEX)
        self.assertEqual(launch.argv, f"{CMD} /k .gamestudio\\codex.cmd")
        self.assertTrue(launch.console)

    def test_macos_terminal(self):
        folder, codex = "/Users/jane/My Games/Gem", "/Users/jane/.local/bin/codex"
        launch = cc.terminal_command(folder, codex, "darwin", which=which_of({}), env={}, agent=cc.CODEX,
                                     path_prepend="/Users/jane/Library/Application Support/GameStudio/node/bin")
        self.assertEqual(launch.argv, ["/usr/bin/open", "-a", "Terminal", folder + "/.gamestudio/codex.command"])
        self.assertTrue(launch.wait)
        lines = launch.script_text.splitlines()
        self.assertEqual(lines[0], "#!/bin/sh")
        self.assertIn('cd "$(dirname "$0")/.." || exit 1', lines)
        self.assertIn("PATH='/Users/jane/Library/Application Support/GameStudio/node/bin':\"$PATH\"; export PATH",
                      lines)
        self.assertEqual(shlex.split(lines[-1])[0], codex)
        self.assertIn("Codex stopped with an error", lines[-1])

    def test_linux_terminals(self):
        folder, codex = "/home/jane/gem", "/home/jane/.local/bin/codex"
        gnome = which_of({"gnome-terminal": "/usr/bin/gnome-terminal"})
        launch = cc.terminal_command(folder, codex, "linux", which=gnome, agent=cc.CODEX)
        self.assertEqual(launch.argv, ["/usr/bin/gnome-terminal", f"--working-directory={folder}", "--",
                                       folder + "/.gamestudio/codex.sh"])
        xterm = which_of({"xterm": "/usr/bin/xterm"})
        launch = cc.terminal_command(folder, codex, "linux", which=xterm, agent=cc.CODEX)
        self.assertEqual(launch.argv, ["/usr/bin/xterm", "-e", "./.gamestudio/codex.sh"])

    def test_custom_template(self):
        launch = cc.terminal_command("/home/j/game", "/x/codex", "linux", template="kitty -- {agent}", agent=cc.CODEX)
        self.assertEqual(launch.argv, ["kitty", "--", "/x/codex"])
        launch = cc.terminal_command("/home/j/game", "/x/codex", "linux", template="kitty {script}", agent=cc.CODEX)
        self.assertEqual(launch.argv, ["kitty", "/home/j/game/.gamestudio/codex.sh"])

    def test_claude_code_keeps_its_script(self):
        launch = cc.terminal_command(WIN_PROJECT, tcc.WIN_CLAUDE, "win32", which=which_of({}), env={})
        self.assertEqual(launch.argv, f"{CMD} /k .gamestudio\\claude-code.cmd")
        self.assertIn("title Claude Code", launch.script_text.split("\r\n"))

    @unittest.skipIf(os.name == "nt" or not shutil.which("sh"), "needs a POSIX shell")
    def test_posix_script_runs(self):
        base = pathlib.Path(tempfile.mkdtemp(prefix="gs_codex_script_"))
        try:
            folder = base / "My Game"
            fake = base / "bin" / "codex"
            fake.parent.mkdir(parents=True)
            fake.write_text('#!/bin/sh\necho "cwd=$(pwd)" > "$HOME/out"\n', encoding="utf-8")
            fake.chmod(0o755)
            launch = cc.terminal_command(folder, str(fake), "linux", which=which_of({"xterm": "/usr/bin/xterm"}),
                                         agent=cc.CODEX)
            cc.write_launch_script(launch)
            subprocess.run([launch.script], cwd=str(base), env=dict(os.environ, HOME=str(base)), check=True, timeout=30)
            self.assertEqual((base / "out").read_text(encoding="utf-8").strip(), f"cwd={folder}")
        finally:
            shutil.rmtree(base, ignore_errors=True)


class _TempProject(tcc._TempProject):
    def setUp(self):
        super().setUp()
        cc._opened["agent"] = None  # no agent opened yet in this session
        self.addCleanup(cc._opened.__setitem__, "agent", None)

    def config_text(self) -> str:
        return (self.root / cx.CONFIG).read_text(encoding="utf-8")

    def config(self) -> dict:
        return tomllib.loads(self.config_text())

    def write_config(self, text: str) -> pathlib.Path:
        path = self.root / cx.CONFIG
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode("utf-8"))
        return path

    def skill(self, name: str, text: str = "", extra: dict | None = None) -> pathlib.Path:
        folder = self.root / ".claude" / "skills" / name
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "SKILL.md").write_text(text or f"---\nname: {name}\ndescription: Does {name}.\n---\n\nSteps.\n",
                                         encoding="utf-8")
        for rel, content in (extra or {}).items():
            (folder / rel).write_text(content, encoding="utf-8")
        return folder


NODE = "D:\\GameStudio User\\node\\node.exe"
USER_CONFIG = """# My Codex settings
model = "gpt-5-codex"   # the model I like
approval_policy = "on-request"

[mcp_servers.docs]
command = "npx"
args = ["-y", "@upstash/context7-mcp"]

[profiles.fast]
model = "gpt-5-mini"
"""


class ConfigTests(_TempProject):
    """.codex/config.toml: GameStudio's MCP server for Codex, next to the person's own settings."""

    def test_a_new_file(self):
        self.assertFalse(cx.has_server(self.root))
        self.assertEqual(cx.write_codex_config(self.root, node="node"), "added")
        config = self.config()
        self.assertEqual(config["mcp_servers"]["gamestudio"],
                         {"command": "node", "args": [cc.MCP_ENTRY], "startup_timeout_sec": 30})
        self.assertTrue(self.config_text().startswith("# Codex's settings for this project."))
        self.assertIn("you trust", self.config_text())
        self.assertTrue(cx.has_server(self.root))
        self.assertEqual(cx.write_codex_config(self.root, node="node"), "", "nothing to do the second time")

    def test_same_command_and_args_as_mcp_json(self):
        self.mcp()
        self.own_script()
        cc.sync_mcp_config(self.root, node=NODE)
        cx.write_codex_config(self.root, node=NODE)
        server = self.config()["mcp_servers"]["gamestudio"]
        self.assertEqual((server["command"], server["args"]), (self.server()["command"], self.server()["args"]))
        self.assertIn(f"command = '{NODE}'", self.config_text(), "a Windows path as a literal string")

    def test_a_projects_script_in_a_parent_folder(self):
        entry = self.base / cc.MCP_ENTRY
        entry.parent.mkdir(parents=True)
        entry.write_text("", encoding="utf-8")
        cx.write_codex_config(self.root, node="node")
        self.assertEqual(self.config()["mcp_servers"]["gamestudio"]["args"], ["../" + cc.MCP_ENTRY])

    def test_keeps_the_persons_settings(self):
        self.write_config(USER_CONFIG)
        self.assertEqual(cx.write_codex_config(self.root, node="node"), "added")
        text = self.config_text()
        self.assertTrue(text.startswith(USER_CONFIG), "appended: the person's text stays as it was written")
        config = self.config()
        self.assertEqual(config["model"], "gpt-5-codex")
        self.assertEqual(config["mcp_servers"]["docs"]["command"], "npx")
        self.assertEqual(config["profiles"]["fast"]["model"], "gpt-5-mini")
        self.assertEqual(config["mcp_servers"]["gamestudio"]["command"], "node")
        self.write_config(USER_CONFIG.rstrip("\n"))  # no line end at the end
        cx.write_codex_config(self.root, node="node")
        self.assertEqual(self.config()["mcp_servers"]["gamestudio"]["args"], [cc.MCP_ENTRY])

    def test_updates_only_its_values(self):
        theirs = USER_CONFIG + ("\r\n".join(["", "[mcp_servers.gamestudio]  # mine too",
                                             "command = \"/Users/bob/Library/GameStudio/node/bin/node\"",
                                             "args = [", f"  \"{cc.MCP_ENTRY}\",", "]",
                                             "startup_timeout_sec = 45", "tool_timeout_sec = 120", "",
                                             "[profiles.slow]", "model = \"gpt-5\"", ""]))
        path = self.write_config(theirs)
        self.assertTrue(cx.sync_codex_config(self.root, node=NODE))
        text = path.read_bytes().decode("utf-8")
        self.assertEqual(text, theirs.replace('"/Users/bob/Library/GameStudio/node/bin/node"', f"'{NODE}'"),
                         "only the command's value changes: comments, layout, line ends and other keys stay")
        self.assertEqual(self.config()["mcp_servers"]["gamestudio"]["tool_timeout_sec"], 120)
        self.assertFalse(cx.sync_codex_config(self.root, node=NODE), "stable from one open to the next")

    def test_write_on_a_connected_project_repairs(self):
        self.write_config(cx.table_text("/Users/bob/node/bin/node", [cc.MCP_ENTRY]))
        self.assertEqual(cx.write_codex_config(self.root, node="node"), "updated")
        self.assertEqual(self.config()["mcp_servers"]["gamestudio"]["command"], "node")

    def test_the_persons_own_server_is_left_alone(self):
        own = '[mcp_servers.gamestudio]\ncommand = "npx"\nargs = ["gamestudio-mcp"]\n'
        path = self.write_config(own)
        self.assertEqual(cx.write_codex_config(self.root, node=NODE), "")
        self.assertFalse(cx.sync_codex_config(self.root, node=NODE))
        self.assertEqual(path.read_text(encoding="utf-8"), own)
        self.assertTrue(cx.has_server(self.root))

    def test_what_cannot_be_edited_safely_is_not_written(self):
        for text in ('mcp_servers = { docs = { command = "npx" } }\n', "model = \n", "[mcp_servers]\nx = [1,\n"):
            path = self.write_config(text)
            with self.assertRaises(cx.CodexError):
                cx.write_codex_config(self.root, node="node")
            self.assertEqual(path.read_text(encoding="utf-8"), text, text)
            self.assertFalse(cx.sync_codex_config(self.root, node="node"))

    def test_inline_and_dotted_tables_are_updated_in_place(self):
        inline = f'[mcp_servers]\ngamestudio = {{ command = "/gone/node", args = ["{cc.MCP_ENTRY}"] }}\n'
        dotted = f'mcp_servers.gamestudio.command = "/gone/node"\nmcp_servers.gamestudio.args = ["{cc.MCP_ENTRY}"]\n'
        for text in (inline, dotted):
            self.write_config(text)
            self.assertTrue(cx.sync_codex_config(self.root, node="node"), text)
            self.assertEqual(self.config_text(), text.replace('"/gone/node"', '"node"'))

    def test_repaired_when_a_scene_loads(self):
        self.write_config(cx.table_text("/Users/bob/node/bin/node", [cc.MCP_ENTRY]))
        project.set_project_root_override(self.root)
        self.assertIn(cx._on_load_post, bpy.app.handlers.load_post)
        with mock.patch.object(cc, "mcp_node_command", return_value="node"):
            cx._on_load_post()
        self.assertEqual(self.config()["mcp_servers"]["gamestudio"]["command"], "node")

    def test_portable(self):
        text = cx.table_text(NODE, ["../../" + cc.MCP_ENTRY])
        self.assertEqual(cx.portable_codex_text(text), cx.table_text("node", [cc.MCP_ENTRY]))
        self.assertEqual(cx.portable_codex_text("not = [toml"), "not = [toml")
        self.assertEqual(cx.portable_codex_text(USER_CONFIG), USER_CONFIG)

    def test_save_as_template_makes_it_portable(self):
        folder = self.base / "copy"
        (folder / ".codex").mkdir(parents=True)
        (folder / cx.CONFIG).write_text(USER_CONFIG + "\n" + cx.table_text(NODE, [cc.MCP_ENTRY]), encoding="utf-8")
        changed = projects._to_placeholders(folder, "Gem Island")
        self.assertIn(cx.CONFIG, changed)
        config = tomllib.loads((folder / cx.CONFIG).read_text(encoding="utf-8"))
        self.assertEqual(config["mcp_servers"]["gamestudio"]["command"], "node")
        self.assertEqual(config["model"], "gpt-5-codex")


class SkillsTests(_TempProject):
    """.agents/skills: Codex's copy of .claude/skills."""

    def test_copies_each_skill_with_a_note(self):
        self.skill("make-prop", extra={"MODELING.md": "How to model.\n"})
        self.skill("playtest-and-fix")
        (self.root / ".claude" / "skills" / "notes.txt").write_text("not a skill", encoding="utf-8")
        result = cx.mirror_skills(self.root)
        self.assertEqual(result["copied"], ["make-prop", "playtest-and-fix"])
        copy = self.root / ".agents" / "skills" / "make-prop"
        self.assertEqual((copy / "SKILL.md").read_text(encoding="utf-8"),
                         (self.root / ".claude" / "skills" / "make-prop" / "SKILL.md").read_text(encoding="utf-8"))
        self.assertEqual((copy / "MODELING.md").read_text(encoding="utf-8"), "How to model.\n")
        self.assertEqual(cx.copy_info(copy)["source"], ".claude/skills/make-prop")
        readme = (self.root / ".agents" / "skills" / "README.md").read_text(encoding="utf-8")
        self.assertTrue(readme.startswith(cx.README_MARK))
        self.assertIn("Edit a skill in `.claude/skills/`", readme)
        self.assertTrue(cx.mirrored(self.root))
        self.assertEqual(cx.mirror_skills(self.root), {"copied": [], "updated": [], "removed": [], "kept": []})

    def test_the_templates_skills_have_what_codex_needs(self):
        """Codex needs `name` and `description` in SKILL.md's front matter (developers.openai.com/codex/skills)."""
        for folder in sorted((cc.projects.TEMPLATES / "starter" / ".claude" / "skills").iterdir()):
            text = (folder / "SKILL.md").read_text(encoding="utf-8")
            head = text.split("---", 2)[1]
            fields = dict(line.split(":", 1) for line in head.strip().splitlines() if ":" in line and line[0] != " ")
            self.assertEqual(fields.get("name", "").strip(), folder.name, folder.name)
            self.assertTrue(0 < len(fields.get("description", "").strip()) <= 1024, folder.name)

    def test_follows_its_source(self):
        source = self.skill("make-prop")
        cx.mirror_skills(self.root)
        (source / "SKILL.md").write_text("---\nname: make-prop\ndescription: Better.\n---\n", encoding="utf-8")
        self.assertEqual(cx.mirror_skills(self.root)["updated"], ["make-prop"])
        copy = self.root / ".agents" / "skills" / "make-prop"
        self.assertIn("Better.", (copy / "SKILL.md").read_text(encoding="utf-8"))
        shutil.rmtree(source)
        self.assertEqual(cx.mirror_skills(self.root)["removed"], ["make-prop"])
        self.assertFalse(copy.exists())

    def test_never_overwrites_the_persons_skills(self):
        self.skill("make-prop")
        own = self.root / ".agents" / "skills" / "make-prop"
        own.mkdir(parents=True)
        (own / "SKILL.md").write_text("---\nname: make-prop\ndescription: Mine.\n---\n", encoding="utf-8")
        mine = self.root / ".agents" / "skills" / "codex-only"
        mine.mkdir()
        (mine / "SKILL.md").write_text("---\nname: codex-only\ndescription: Mine too.\n---\n", encoding="utf-8")
        result = cx.mirror_skills(self.root)
        self.assertEqual(result["kept"], ["make-prop"])
        self.assertIn("Mine.", (own / "SKILL.md").read_text(encoding="utf-8"))
        self.assertTrue((mine / "SKILL.md").is_file(), "a skill without a source is the person's")
        self.assertIsNone(cx.copy_info(own))

    def test_an_edited_copy_is_left_alone(self):
        source = self.skill("make-prop")
        cx.mirror_skills(self.root)
        copy = self.root / ".agents" / "skills" / "make-prop"
        (copy / "SKILL.md").write_text("---\nname: make-prop\ndescription: Edited here.\n---\n", encoding="utf-8")
        (source / "SKILL.md").write_text("---\nname: make-prop\ndescription: Changed there.\n---\n", encoding="utf-8")
        self.assertEqual(cx.mirror_skills(self.root)["kept"], ["make-prop"])
        self.assertIn("Edited here.", (copy / "SKILL.md").read_text(encoding="utf-8"))
        shutil.rmtree(source)
        cx.mirror_skills(self.root)
        self.assertTrue(copy.exists(), "an edited copy outlives its source")

    def test_save_as_template_leaves_the_copies_out(self):
        self.skill("make-prop")
        cx.mirror_skills(self.root)
        mine = self.root / ".agents" / "skills" / "codex-only"
        mine.mkdir()
        (mine / "SKILL.md").write_text("---\nname: codex-only\ndescription: Mine.\n---\n", encoding="utf-8")
        cx.write_codex_config(self.root, node="node")
        files, left_out = projects.project_files_for_template(self.root)
        self.assertIn(".agents/skills/codex-only/SKILL.md", files)
        self.assertIn(".codex/config.toml", files)
        self.assertIn(".claude/skills/make-prop/SKILL.md", files)
        self.assertFalse([f for f in files if f.startswith(".agents/skills/make-prop")], files)
        self.assertNotIn(".agents/skills/README.md", files)
        self.assertNotIn(".agents/", left_out)
        self.assertNotIn(".codex/", left_out)


class ConnectTests(_TempProject):
    def test_a_project_without_agent_files(self):
        notes = cx.connect(self.root)
        self.assertEqual(cx.missing_codex_files(self.root), [])
        self.assertTrue((self.root / "AGENTS.md").read_text(encoding="utf-8").startswith("# Gem Island\n"))
        self.assertEqual(agentfiles.instructions_state(self.root), agentfiles.SHARED)
        self.assertTrue((self.root / ".agents" / "skills" / "new-game-from-brief" / "SKILL.md").is_file())
        self.assertTrue(cx.has_server(self.root))
        self.assertTrue(any("Codex" in note for note in notes), notes)

    def test_instructions_only_in_claude_md_are_copied_without_consent(self):
        mine = "# Gem Island\n\nMy own rules: always use blue.\n"
        (self.root / "CLAUDE.md").write_text(mine, encoding="utf-8")
        self.assertTrue(cx.offers_move(self.root))
        self.assertIn("AGENTS.md", cx.missing_codex_files(self.root))
        cx.connect(self.root, move=False)
        self.assertEqual((self.root / "CLAUDE.md").read_text(encoding="utf-8"), mine, "CLAUDE.md as it was")
        agents = (self.root / "AGENTS.md").read_text(encoding="utf-8")
        self.assertIn("My own rules: always use blue.", agents)
        self.assertIn("A copy of CLAUDE.md", agents)
        self.assertNotIn("AGENTS.md", cx.missing_codex_files(self.root))
        # Changed in CLAUDE.md later: the copy follows when the project opens.
        (self.root / "CLAUDE.md").write_text(mine + "Also: red.\n", encoding="utf-8")
        cx.prepare_project(self.root)
        self.assertIn("Also: red.", (self.root / "AGENTS.md").read_text(encoding="utf-8"))

    def test_instructions_move_with_consent(self):
        mine = "# Gem Island\n\nMy own rules: always use blue.\n"
        (self.root / "CLAUDE.md").write_text(mine, encoding="utf-8")
        cx.connect(self.root, move=True)
        self.assertEqual((self.root / "AGENTS.md").read_text(encoding="utf-8"), mine)
        self.assertEqual((self.root / "CLAUDE.md").read_text(encoding="utf-8"), "@AGENTS.md\n")
        self.assertFalse(cx.offers_move(self.root))

    def test_never_overwrites(self):
        (self.root / "AGENTS.md").write_text("# Mine\n", encoding="utf-8")
        (self.root / "CLAUDE.md").write_text("# Also mine\n", encoding="utf-8")
        (self.root / ".claude").mkdir()
        cx.connect(self.root, move=True)
        self.assertEqual((self.root / "AGENTS.md").read_text(encoding="utf-8"), "# Mine\n")
        self.assertEqual((self.root / "CLAUDE.md").read_text(encoding="utf-8"), "# Also mine\n")
        self.assertFalse((self.root / ".claude" / "skills").exists(), "an existing .claude/ is the person's")

    def test_prepare_leaves_unconnected_projects_alone(self):
        self.skill("make-prop")
        self.assertEqual(cx.prepare_project(self.root), [])
        self.assertFalse((self.root / ".agents").exists())
        self.assertFalse((self.root / ".codex").exists())

    def test_prepare_refreshes_the_copies(self):
        source = self.skill("make-prop")
        cx.write_codex_config(self.root, node="node")
        self.assertTrue(cx.prepare_project(self.root), "the first copy")
        (source / "SKILL.md").write_text("---\nname: make-prop\ndescription: New.\n---\n", encoding="utf-8")
        self.assertIn(f"Updated Codex's copy of the skills ({cx.SKILLS})", cx.prepare_project(self.root))
        self.assertEqual(cx.prepare_project(self.root), [], "once")

    def test_connect_text(self):
        self.assertEqual(cx.connect_text([cx.CONFIG, "AGENTS.md", cx.SKILLS]),
                         "Adds instructions for this game, Codex's copy of the skills and the link to Blender.")
        self.assertEqual(cx.connect_text([cc.MCP_PACKAGE, cx.CONFIG]), "Adds the link to Blender.")


class OperatorTests(_TempProject, _Agent):
    OPERATORS = ("open_codex", "codex_install", "codex_install_cancel", "codex_started", "copy_codex_install_command")

    def test_operators_exist(self):
        for name in self.OPERATORS:
            self.assertTrue(hasattr(bpy.ops.gamestudio, name), name)
        project.set_project_root_override(None)
        with mock.patch.object(project, "project_root", return_value=None):
            self.assertFalse(bpy.ops.gamestudio.open_codex.poll())

    def test_open_connects_and_opens_the_terminal(self):
        project.set_project_root_override(self.root)
        spawned = []
        with mock.patch.object(cx, "codex_path", return_value=WIN_CODEX), \
                mock.patch.object(cc, "_spawn", lambda launch, env: spawned.append((launch, env))), \
                mock.patch.object(cc, "mcp_node_command", return_value="node"), \
                mock.patch.object(cc, "ensure_bridge", return_value="") as ensure:
            self.assertEqual(bpy.ops.gamestudio.open_codex(), {"FINISHED"})
        ensure.assert_called_once()
        self.assertEqual(len(spawned), 1)
        launch, env = spawned[0]
        script = pathlib.Path(launch.script)
        self.assertEqual(script.parent, self.root / ".gamestudio")
        self.assertEqual(script.stem, "codex")
        self.assertIn(WIN_CODEX, script.read_bytes().decode("utf-8"))
        self.assertIn("PATH", {k.upper() for k in env})
        self.assertEqual(cx.missing_codex_files(self.root), [])
        self.assertEqual(self.config()["mcp_servers"]["gamestudio"]["command"], "node")

    def test_open_without_changes_when_unticked(self):
        project.set_project_root_override(self.root)
        with mock.patch.object(cx, "codex_path", return_value=WIN_CODEX), \
                mock.patch.object(cc, "_spawn", lambda launch, env: None), \
                mock.patch.object(cc, "ensure_bridge") as ensure:
            bpy.ops.gamestudio.open_codex(add_files=False, start_bridge=False)
        ensure.assert_not_called()
        self.assertEqual(cx.missing_codex_files(self.root), [cx.CONFIG, "AGENTS.md", cx.SKILLS])

    def test_open_moves_the_instructions_only_when_ticked(self):
        project.set_project_root_override(self.root)
        (self.root / "CLAUDE.md").write_text("# Gem Island\n\nMine.\n", encoding="utf-8")
        with mock.patch.object(cx, "codex_path", return_value=WIN_CODEX), \
                mock.patch.object(cc, "_spawn", lambda launch, env: None), \
                mock.patch.object(cc, "ensure_bridge", return_value=""):
            bpy.ops.gamestudio.open_codex(move_instructions=True)
        self.assertEqual((self.root / "CLAUDE.md").read_text(encoding="utf-8"), "@AGENTS.md\n")

    def test_open_without_codex_does_nothing(self):
        project.set_project_root_override(self.root)
        with mock.patch.object(cx, "codex_path", return_value=None), \
                mock.patch.object(cc, "_spawn") as spawn, self.assertRaises(RuntimeError):
            bpy.ops.gamestudio.open_codex()
        spawn.assert_not_called()

    def _install_patches(self, problem="", online=True):
        return (mock.patch.object(cx, "codex_path", return_value=None),
                mock.patch.object(cx, "install_problem", return_value=problem),
                mock.patch.object(cx.environment, "online", return_value=online),
                mock.patch.object(cc, "_allow_online"),
                mock.patch.object(cx, "start_install", return_value=""),
                mock.patch.object(cc, "_open_url"))

    def test_install_runs_the_installer(self):
        patches = self._install_patches()
        with patches[0], patches[1], patches[2], patches[3], patches[4] as start, patches[5] as open_url:
            self.assertEqual(bpy.ops.gamestudio.codex_install(), {"FINISHED"})
            start.assert_called_once_with(None)
            start.reset_mock()
            self.assertEqual(bpy.ops.gamestudio.codex_install(open_after=True), {"FINISHED"})
            start.assert_called_once_with(cx._open_after_install)
        open_url.assert_not_called()

    def test_install_guides_where_it_cannot_run(self):
        patches = self._install_patches(problem="The installer needs curl")
        with patches[0], patches[1], patches[2], patches[3], patches[4] as start, patches[5] as open_url:
            self.assertEqual(bpy.ops.gamestudio.codex_install(), {"FINISHED"})
        start.assert_not_called()
        open_url.assert_called_once_with(cx.DOCS_URL)

    def test_install_offline_asks_for_online_access(self):
        patches = self._install_patches(online=False)
        with patches[0], patches[1], patches[2], patches[3] as allow, patches[4] as start, patches[5]:
            with self.assertRaises(RuntimeError):
                bpy.ops.gamestudio.codex_install()
        allow.assert_called_once()
        start.assert_not_called()

    def test_open_goes_on_after_the_install(self):
        project.set_project_root_override(self.root)
        with mock.patch.object(cx.bpy.app.timers, "register") as register:
            cx._open_after_install(False, "failed")
            register.assert_not_called()
            cx._open_after_install(True, "")
            register.assert_called_once()

    def test_dialogs_draw(self):
        project.set_project_root_override(self.root)
        (self.root / "CLAUDE.md").write_text("# Gem Island\n\nMine.\n", encoding="utf-8")
        texts = {}
        for cls in (cx.GAMESTUDIO_OT_open_codex, cx.GAMESTUDIO_OT_codex_install, cx.GAMESTUDIO_OT_codex_started):
            layout = ux.Recorder(self)
            fake = te._FakeSelf(cls, layout)
            category, name = cls.bl_idname.split(".")
            fake.bl_rna = getattr(getattr(bpy.ops, category), name).get_rna_type()
            fake.add_files = True  # the operator's own property values
            with tcc.fake_bridge(None), mock.patch.object(cx, "install_problem", return_value=""):
                cls.draw(fake, te.fake_context())
            texts[cls] = layout.texts
        opened = texts[cx.GAMESTUDIO_OT_open_codex]
        self.assertIn("Adds instructions for this game, Codex's copy of the skills and the link to Blender.", opened)
        self.assertTrue(any("only Claude Code reads" in text for text in opened), "the move is offered")
        self.assertTrue(any("ChatGPT account" in text for text in opened), "sign in")
        self.assertTrue(any("Trust and continue" in text for text in opened), "trust the folder")
        installed = texts[cx.GAMESTUDIO_OT_codex_install]
        self.assertIn(f"The installer: {cx.install_commands()[0][1]}", installed)
        self.assertTrue(any("OpenAI API key" in text for text in installed), "API key")
        self.assertTrue(any("ChatGPT account" in text for text in installed), "ChatGPT")
        started = texts[cx.GAMESTUDIO_OT_codex_started]
        self.assertTrue(any("Sign in with ChatGPT" in text for text in started))
        layout = ux.Recorder(self)
        with mock.patch.object(cx, "install_problem", return_value="The installer needs curl, which ..."):
            cx.GAMESTUDIO_OT_codex_install.draw(te._FakeSelf(cx.GAMESTUDIO_OT_codex_install, layout),
                                                te.fake_context())
        self.assertIn("gamestudio.copy_codex_install_command", ux.ids(layout), "the guided path keeps the commands")

    def test_copy_install_command(self):
        copied = []
        with mock.patch.object(cc, "_set_clipboard", lambda context, text: copied.append(text)):
            bpy.ops.gamestudio.copy_codex_install_command(index=0)
        self.assertEqual(copied, [cx.install_commands()[0][1]])


class PreferenceTests(_TempProject, _Agent):
    """The "Coding agent" preference: what the launcher, the welcome, the panels and the bars open and say."""

    def setUp(self):
        super().setUp()
        patch = mock.patch.object(activity_ui.activity, "entries", return_value=[])  # no earlier agent's calls
        patch.start()
        self.addCleanup(patch.stop)

    def test_codex_is_named_by_the_bridge(self):
        self.assertEqual(bridge.client_label("codex-mcp-client"), "Codex")
        self.assertEqual(bridge.client_label("claude-code"), "Claude Code")
        with tcc.fake_bridge(1, agent="codex-mcp-client"):
            self.assertEqual(cc.status_item(), ("Codex connected", "LINKED", True))
            self.assertEqual(activity_ui.agent_name(), "Codex")

    def test_both_are_in_the_gamestudio_menu(self):
        with tcc.fake_bridge(1):
            found = ux.ids(ux.draw_menu(self, "GAMESTUDIO_MT_projects"))
        self.assertEqual(found.index("gamestudio.open_codex"), found.index("gamestudio.open_claude_code") + 1)

    def test_claude_code_by_default(self):
        self.assertEqual(cc.preferred_agent(), cc.CLAUDE)
        layout = ux.Recorder(self)
        launcher.draw_welcome(layout, te.fake_context())
        self.assertIn("gamestudio.open_claude_code", ux.ids(layout))
        self.assertIn("Make 3D games in Blender with Claude Code, the coding agent.", layout.texts)

    def test_codex(self):
        self.use("CODEX")
        self.assertEqual(cc.preferred_agent(), cc.CODEX)
        project.set_project_root_override(self.root)
        with mock.patch.object(cx, "codex_path", return_value=None), \
                mock.patch.object(launcher.projects, "examples", return_value=[]):
            layout = ux.Recorder(self)
            launcher.draw_welcome(layout, te.fake_context())
            self.assertIn("Make 3D games in Blender with Codex, the coding agent.", layout.texts)
            self.assertIn("2. Open it in Codex", layout.texts)
            self.assertIn("gamestudio.open_codex", ux.ids(layout))
            self.assertNotIn("gamestudio.open_claude_code", ux.ids(layout))
            self.assertIn("Codex is not installed", layout.texts)
            self.assertIn("gamestudio.codex_install", ux.ids(layout))
            layout = ux.Recorder(self)
            launcher.draw_launcher(layout, te.fake_context())
            self.assertIn("gamestudio.open_codex", ux.ids(layout))

            overview = [cls for cls in te.host.editors.registered_classes("GAMESTUDIO_PROJECT")
                        if cls.gs_spec.id == "overview"][0]
            layout = ux.Recorder(self)
            with tcc.fake_bridge(0):
                cc.draw_project_panel(te._FakeSelf(overview, layout), te.fake_context())
        found = ux.ids(layout)
        self.assertLess(found.index("gamestudio.open_codex"), found.index("gamestudio.open_claude_code"),
                        "the preferred agent first")
        self.assertIn("Not connected to Codex yet: Open in Codex connects it", layout.texts)
        self.assertIn("Waiting for Codex", layout.texts)

    def test_status_bar_and_top_bar(self):
        project.set_project_root_override(self.root)
        with tcc.fake_bridge(0):
            self.assertEqual(activity_ui.status_item()[0], "Claude not connected")
            self.use("CODEX")
            self.assertEqual(activity_ui.status_item(), ("Codex not connected", "UNLINKED", False, False))
            self.assertEqual(activity_ui.topbar_item(), ("Codex", "UNLINKED", False, False))
            layout = ux.Recorder(self)
            context = types.SimpleNamespace(window_manager=bpy.context.window_manager, scene=bpy.context.scene,
                                            view_layer=bpy.context.view_layer)
            activity_ui.draw_status(layout, context)
            self.assertIn("gamestudio.open_codex", ux.ids(layout), "the popover opens the preferred agent")
        with tcc.fake_bridge(1, agent="codex-mcp-client"):
            self.assertEqual(activity_ui.topbar_item(), ("Codex", "LINKED", False, True))

    def test_the_bars_name_the_agent_opened_last(self):
        project.set_project_root_override(self.root)
        with tcc.fake_bridge(0), mock.patch.object(cx, "codex_path", return_value=WIN_CODEX), \
                mock.patch.object(cc, "_spawn", lambda launch, env: None), \
                mock.patch.object(cc, "ensure_bridge", return_value=""):
            self.assertEqual(activity_ui.topbar_item()[0], "Claude", "the preference: Claude Code")
            bpy.ops.gamestudio.open_codex(add_files=False)
            self.assertEqual(cc.expected_agent(), cc.CODEX)
            self.assertEqual(activity_ui.topbar_item()[0], "Codex", "Codex was just opened")
            self.assertEqual(activity_ui.status_item()[0], "Codex not connected")
            self.assertEqual(cc.bridge_status()[0], "Waiting for Codex")
            self.use("CODEX")
            cc.opened(cc.CODEX)
            prefs().coding_agent = "CLAUDE_CODE"  # choosing an agent forgets the one opened last
            self.assertEqual(activity_ui.topbar_item()[0], "Claude")

    def test_preferences_section(self):
        texts = []
        with mock.patch("rna_keymap_ui.draw_kmi"):
            type(prefs()).draw(te._FakeSelf(prefs(), te.MockLayout(self, texts)), te.fake_context())
        self.assertIn("Coding agent", texts)
        self.assertTrue(any(text.startswith("Codex") for text in texts), texts)
        items = [item.identifier for item in prefs().bl_rna.properties["coding_agent"].enum_items]
        self.assertEqual(items, ["CLAUDE_CODE", "CODEX"])

    def test_setup_purpose(self):
        self.assertEqual(environment.ESSENTIAL["codex"], environment.ESSENTIAL["claude"])
        self.assertIn("codex", environment.REGISTRY_PURPOSES)


def load_tests(loader, _tests=None, _pattern=None):
    suite = unittest.TestSuite()
    for case in (DetectionTests, InstallCommandTests, InstallerTests, TerminalTests, ConfigTests, SkillsTests,
                 ConnectTests, OperatorTests, PreferenceTests):
        suite.addTests(loader.loadTestsFromTestCase(case))
    return suite
