# SPDX-License-Identifier: GPL-3.0-or-later
"""Dev server management with a fake `npm` (a script that runs fake_dev_server.py)."""

import os
import pathlib
import socket
import stat
import subprocess
import sys
import time
import unittest
from unittest import mock

from gamestudio import prefs
from gamestudio.core import devserver, link, project

import helpers

WINDOWS = sys.platform == "win32"


def write_script(path: pathlib.Path, windows_body: str, posix_body: str) -> pathlib.Path:
    if WINDOWS:
        path = path.with_suffix(".cmd")
        path.write_text("@echo off\r\n" + windows_body.replace("\n", "\r\n"), encoding="utf-8")
    else:
        path.write_text("#!/bin/sh\n" + posix_body, encoding="utf-8")
        path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return path


class ProcessHelpersTest(unittest.TestCase):
    def test_pid_alive(self):
        self.assertTrue(devserver.pid_alive(os.getpid()))
        self.assertFalse(devserver.pid_alive(0))
        self.assertFalse(devserver.pid_alive(None))
        proc = subprocess.Popen([sys.executable, "-c", "pass"])
        proc.wait()
        self.assertFalse(devserver.pid_alive(proc.pid))

    def test_port_open(self):
        server = socket.create_server(("127.0.0.1", 0))
        port = server.getsockname()[1]
        self.assertTrue(devserver.port_open(port))
        server.close()
        self.assertFalse(devserver.port_open(port, timeout=0.2))


class DevServerTest(helpers.ProjectTestCase):
    def setUp(self):
        super().setUp()
        self._saved = (prefs._defaults.npm_command, prefs._defaults.app_window)

    def tearDown(self):
        devserver.stop()
        prefs._defaults.npm_command, prefs._defaults.app_window = self._saved
        helpers.restore_global_link()
        super().tearDown()

    def make_node_project(self):
        (self.root / "package.json").write_text('{"name": "test-game"}', encoding="utf-8")
        (self.root / "node_modules").mkdir()

    def test_start_reports_setup_problems(self):
        prefs._defaults.npm_command = "definitely-not-npm-4f2a"
        with self.assertRaises(devserver.DevServerError) as cm:
            devserver.start(self.root)
        self.assertIn("Could not find 'definitely-not-npm-4f2a'", str(cm.exception))
        prefs._defaults.npm_command = sys.executable
        with self.assertRaises(devserver.DevServerError) as cm:
            devserver.start(self.root)
        self.assertIn("No package.json", str(cm.exception))
        (self.root / "package.json").write_text("{}", encoding="utf-8")
        with self.assertRaises(devserver.DevServerError) as cm:
            devserver.start(self.root)
        self.assertIn("are not set up yet: GameStudio sets them up", str(cm.exception))
        self.assertNotIn("npm install", str(cm.exception))
        project.set_project_root_override(None)
        with self.assertRaises(devserver.DevServerError):
            devserver.start()

    def test_start_connect_and_stop_the_whole_tree(self):
        self.make_node_project()
        script = helpers.FIXTURES / "fake_dev_server.py"
        fake = write_script(self.root / "fake-npm", f'"{sys.executable}" "{script}" %*\n',
                            f'exec "{sys.executable}" "{script}" "$@"\n')
        prefs._defaults.npm_command = str(fake)
        ready = []
        self.assertEqual(devserver.start(self.root, on_ready=lambda ok, msg: ready.append((ok, msg))), "started")
        self.assertTrue(devserver.is_starting())
        client = link.get_link()
        client.reconnect_interval = 0.1
        ok = helpers.pump_until(lambda: ready and client.connected, [devserver.pump, client.pump], timeout=30)
        self.assertTrue(ok, f"server not ready: {ready} {client.last_error}\n{devserver.log_tail(self.root)}")
        self.assertTrue(ready[0][0])
        self.assertIn("args ['run', 'dev']", devserver.log_tail(self.root))

        session = project.read_session(self.root)
        server_pid = session["pid"]
        child_pid = int((self.root / ".gamestudio" / "child.pid").read_text())
        self.assertTrue(devserver.pid_alive(server_pid))
        self.assertTrue(devserver.pid_alive(child_pid))
        self.assertTrue(devserver.is_running(self.root))
        self.assertEqual(devserver.start(self.root), "running")

        self.assertTrue(devserver.stop())
        gone = helpers.pump_until(lambda: not devserver.pid_alive(server_pid) and not devserver.pid_alive(child_pid),
                                  timeout=10)
        self.assertTrue(gone, "the dev server's process tree survived stop()")
        self.assertFalse(project.session_path(self.root).exists())
        self.assertFalse(client.connected)
        self.assertFalse(devserver.stop())  # nothing of ours is left

    def test_start_does_not_wait_for_the_server(self):
        # Play exports the scene while the server boots: start() returns once npm runs, and pump()
        # (a timer) sees the server come up.
        self.make_node_project()
        script = helpers.FIXTURES / "fake_dev_server.py"
        fake = write_script(self.root / "fake-npm", f'"{sys.executable}" "{script}" %*\n',
                            f'exec "{sys.executable}" "{script}" "$@"\n')
        prefs._defaults.npm_command = str(fake)
        ready = []
        started = time.monotonic()
        self.assertEqual(devserver.start(self.root, on_ready=lambda ok, msg: ready.append(ok)), "started")
        self.assertLess(time.monotonic() - started, 5.0)
        self.assertTrue(devserver.is_starting())
        self.assertIsNone(project.read_session(self.root), "returned before the server was up")
        self.assertEqual(ready, [])
        self.assertTrue(helpers.pump_until(lambda: ready, [devserver.pump], timeout=30),
                        devserver.log_tail(self.root))
        self.assertEqual(ready, [True])
        self.assertEqual(devserver._timer(), devserver.IDLE_INTERVAL)
        self.assertLessEqual(devserver.POLL_INTERVAL, 0.1, "Play goes on soon after the server is up")

    def test_server_that_exits_is_reported(self):
        self.make_node_project()
        fake = write_script(self.root / "broken-npm", "echo Error: Cannot find module vite\nexit /b 3\n",
                            "echo Error: Cannot find module vite\nexit 3\n")
        prefs._defaults.npm_command = str(fake)
        ready = []
        devserver.start(self.root, on_ready=lambda ok, msg: ready.append((ok, msg)))
        self.assertTrue(helpers.pump_until(lambda: ready, [devserver.pump], timeout=15))
        ok, message = ready[0]
        self.assertFalse(ok)
        self.assertIn("exited (code 3)", message)
        self.assertIn("Cannot find module vite", message)
        self.assertFalse(devserver.is_starting())

    def test_a_server_that_dies_while_starting_is_started_once_more(self):
        # Like a file briefly locked by an antivirus scan right after installing GameStudio.
        self.make_node_project()
        script = helpers.FIXTURES / "fake_dev_server.py"
        marker = self.root / "failed-once"
        fake = write_script(
            self.root / "flaky-npm",
            f'if not exist "{marker}" (echo.> "{marker}" & echo Error: Cannot find module vite & exit /b 1)\n'
            f'"{sys.executable}" "{script}" %*\n',
            f'if [ ! -e "{marker}" ]; then touch "{marker}"; echo "Error: Cannot find module vite"; exit 1; fi\n'
            f'exec "{sys.executable}" "{script}" "$@"\n')
        prefs._defaults.npm_command = str(fake)
        ready = []
        devserver.start(self.root, on_ready=lambda ok, msg: ready.append((ok, msg)))
        self.assertTrue(helpers.pump_until(lambda: ready, [devserver.pump], timeout=30))
        self.assertTrue(marker.exists(), "the first start did not fail as staged")
        self.assertTrue(ready[0][0], f"{ready}\n{devserver.log_tail(self.root)}")
        self.assertTrue(devserver.stop())

    def test_open_game_window(self):
        out = self.root / "browser-args.txt"
        fake_browser = write_script(self.root / "fake-browser", f'echo %* > "{out}"\n', f'echo "$@" > "{out}"\n')
        prefs._defaults.app_window = True
        with mock.patch.object(devserver, "find_app_browser", return_value=str(fake_browser)):
            self.assertEqual(devserver.open_game_window("http://localhost:5173/"), "app")
        self.assertTrue(helpers.pump_until(lambda: out.exists() and out.read_text().strip(), timeout=10))
        self.assertIn("--app=http://localhost:5173/", out.read_text())

        prefs._defaults.app_window = False
        with mock.patch.object(devserver.webbrowser, "open") as opened:
            self.assertEqual(devserver.open_game_window("http://localhost:5173/"), "browser")
        opened.assert_called_once_with("http://localhost:5173/")
