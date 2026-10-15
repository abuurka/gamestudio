# SPDX-License-Identifier: GPL-3.0-or-later
"""GameStudio's first-run setup (environment.py, core/toolchain.py, core/nodejs.py): the one readiness
check before Play, the Scene view, opening a project and Open in Claude Code; consent once; local
work without asking; the refresh after an update (bug 1); links into another GameStudio install
(bug 2); a game browser when there is none; and the macOS/Linux branches, run here with fakes.

Nothing goes online: npm is a fake script, the browser installer a fake that prints the real one's
JSON lines, and Node.js versions are mocked. Temporary folders go under GS_TEST_TMP.
"""

import ast
import io
import json
import os
import pathlib
import shutil
import sys
import tarfile
import tempfile
import threading
import time
import unittest
from unittest import mock

import bpy

from gamestudio import environment, ops, projects  # noqa: F401 - registers the project operators
from gamestudio.core import nodejs, project, stream, toolchain

from helpers import tmp_base

ADDON = pathlib.Path(environment.__file__).resolve().parent
WINDOWS = sys.platform == "win32"
NODE_OK = {"ok": True, "source": "system", "path": "node", "version": "24.21.0", "problem": ""}
NODE_MISSING = {"ok": False, "source": None, "path": "", "version": "", "problem": "not found"}


def write(path: pathlib.Path, data="") -> pathlib.Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data if isinstance(data, bytes) else data.encode("utf-8"))
    return path


def package(folder: pathlib.Path, name: str, version: str = "1.0.0", **fields) -> pathlib.Path:
    write(folder / "package.json", json.dumps({"name": name, "version": version, **fields}))
    return folder


def fake_npm(folder: pathlib.Path, exit_code: int = 0, output: str = "") -> str:
    """A command standing in for npm: records its arguments in npm-calls.txt next to it."""
    calls = folder / "npm-calls.txt"
    script = write(folder / "fake_npm.py", f'''
import sys
with open({str(calls)!r}, "a", encoding="utf-8") as out:
    out.write(" ".join(sys.argv[1:]) + "\\n")
print({output!r})
sys.exit({exit_code})
''')
    if WINDOWS:
        return str(write(folder / "fake-npm.cmd", f'@"{sys.executable}" "{script}" %*\r\n'))
    shim = write(folder / "fake-npm", f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n')
    shim.chmod(0o755)
    return str(shim)


class _Env(unittest.TestCase):
    """A scratch user folder, and the module state of environment.py put back afterwards."""

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix="gs_firstrun_", dir=tmp_base()))
        self._user = toolchain.user_dir()
        self.user = self.tmp / "user folder"  # a space, as in ~/Library/Application Support
        toolchain.set_user_dir(self.user)
        saved = (environment._consent, environment._asking, environment._last_run, environment._retry)
        self.addCleanup(self._restore, saved)
        environment._consent = environment._asking = environment._last_run = environment._retry = None
        environment.invalidate()
        environment._known.clear()
        self.patch(mock.patch.dict(os.environ, {}))
        for name in ("GAMESTUDIO_AUTO_SETUP", "npm_config_cache", "NPM_CONFIG_CACHE"):
            os.environ.pop(name, None)

    def _restore(self, saved):
        environment.JOB.cancel()
        environment.wait(20)
        environment.JOB.__init__()
        environment._consent, environment._asking, environment._last_run, environment._retry = saved
        environment.invalidate()
        environment._known.clear()
        toolchain.set_user_dir(self._user)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def patch(self, patcher):
        value = patcher.start()
        self.addCleanup(patcher.stop)
        return value

    def computer(self, node=True, browser="browser.exe", installer=None, online=True, can_ask=True):
        """What the computer has: Node.js, a browser, gamestudio-browser, online access, a window."""
        self.patch(mock.patch.object(toolchain, "node_status", lambda *_a: dict(NODE_OK if node else NODE_MISSING)))
        self.patch(mock.patch.object(environment, "find_browser", lambda: browser))
        self.patch(mock.patch.object(environment, "browser_installer", lambda: installer))
        self.patch(mock.patch.object(environment, "online", lambda: online))
        self.patch(mock.patch.object(environment, "_can_ask", lambda: can_ask))
        self.asked = []
        self.patch(mock.patch.object(environment, "_ask", self._fake_ask))
        self.runs = []

    def _fake_ask(self, root, purpose, then):
        environment._asking = {"root": root, "purpose": purpose, "then": [then] if then else [], "since": time.monotonic()}
        self.asked.append((root, purpose))

    def record_runs(self):
        def run(root, purpose, needs, then):
            self.runs.append((root, purpose, [n.key for n in needs], then))
        self.patch(mock.patch.object(environment, "_run", run))


class ExtensionBundle:
    """A fake installed extension: bundle.json and runtime/packages; environment.py looks there."""

    def __init__(self, test: _Env, packages: dict):
        self.test = test
        self.ext = test.tmp / "extension"
        self.packages = self.ext / "runtime" / "packages"
        for name, deps in packages.items():
            package(self.packages / name, f"@gamestudio/{name}", "0.1.0", dependencies=deps)
            write(self.packages / name / "src" / "index.js", "export {};\n")
        self.stamp()
        test.patch(mock.patch.object(environment, "__file__", str(self.ext / "environment.py")))
        test.patch(mock.patch.object(environment, "template_dependencies", lambda: {"three": "0.186.1"}))
        self.home = test.user / "runtime"

    def stamp(self):
        write(self.ext / "bundle.json", json.dumps({"kind": "extension",
                                                    "runtime": {"hash": toolchain.runtime_hash(self.packages)}}))

    def install(self, skip=()):
        """The shared runtime after a complete `npm install` (but `skip`)."""
        start = str(self.ext / "environment.py")
        toolchain.sync_shared_runtime({"three": "0.186.1"}, start)
        wanted = {"three": "0.186.1"}
        for name in toolchain.package_names(self.packages):
            wanted.update(toolchain.read_manifest(self.packages / name).get("dependencies") or {})
        for name, version in wanted.items():
            if name not in skip and not name.startswith("@gamestudio/"):
                package(self.home / "node_modules" / name, name, version)
        toolchain.mark_shared_runtime_installed(start)


# --- The readiness check ------------------------------------------------------------------------


class ReadinessTests(_Env):
    def test_ready_is_a_cached_file_check(self):
        self.computer()
        calls = []
        real = environment._check
        self.patch(mock.patch.object(environment, "_check", lambda *a: calls.append(a) or real(*a)))
        self.assertEqual(environment.ensure_ready(None, "play"), environment.READY)
        self.assertEqual(environment.ensure_ready(None, "play"), environment.READY)
        self.assertEqual(len(calls), 1, "a check that found nothing missing holds a while")
        environment.invalidate()
        environment.ensure_ready(None, "play")
        self.assertEqual(len(calls), 2)

    def test_downloads_ask_once_and_the_answer_holds(self):
        self.computer(node=False)
        self.record_runs()
        done = []
        self.assertEqual(environment.ensure_ready(None, "load", then=lambda ok, m: done.append(ok)), environment.ASKING)
        self.assertEqual(self.asked, [(None, "load")])
        self.assertEqual(environment.ensure_ready(None, "play", then=lambda ok, m: done.append(ok)),
                         environment.ASKING, "one question at a time; the action waits for it")
        environment._answer(False)
        self.assertEqual(done, [False, False])
        self.assertTrue(environment.declined())
        # Set up in the background (a file loaded): never asked again this session.
        self.assertEqual(environment.ensure_ready(None, "load"), environment.READY)
        self.assertEqual(len(self.asked), 1)
        # Play cannot work without Node.js: it asks again, or says how, never "install Node.js".
        self.assertEqual(environment.ensure_ready(None, "play"), environment.ASKING)
        environment._asking = None
        problem = environment.ensure_ready(None, "play", ask=False)
        self.assertIn("Play needs GameStudio's one-time setup", problem)
        self.assertIn("Set Up GameStudio", problem)
        self.assertNotIn("install", problem.lower())

    def test_yes_sets_up_and_the_action_goes_on(self):
        self.computer(node=False)
        self.record_runs()
        done = []
        self.assertEqual(environment.ensure_ready(None, "play", then=lambda ok, m: done.append(ok)),
                         environment.ASKING)
        environment._answer(True)
        self.assertTrue(environment.consented())
        [(root, purpose, keys, then)] = self.runs
        self.assertEqual((root, purpose, keys), (None, "play", ["node"]))
        then(True, "")
        self.assertEqual(done, [True])

    def test_local_work_never_asks(self):
        self.computer()
        shared = self.tmp / "sdk" / "runtime" / "packages"
        package(shared / "engine", "@gamestudio/engine", "0.1.0")
        package(self.tmp / "sdk" / "node_modules" / "vite", "vite", "8.3.4")
        self.patch(mock.patch.dict(os.environ, {toolchain.ENV_RUNTIME: str(shared)}))
        root = self.tmp / "game"
        package(root, "game", dependencies={"@gamestudio/engine": "file:x"})
        self.assertEqual([n.key for n in environment.check(root, "open")], ["links"])
        self.assertEqual(environment.ensure_ready(root, "open"), environment.STARTED)
        self.assertEqual(self.asked, [], "links and the component list need no consent")
        self.assertTrue(environment.wait(30), environment.JOB.message)
        self.assertTrue((root / "node_modules" / "@gamestudio" / "engine" / "package.json").is_file())
        self.assertEqual(environment.ensure_ready(root, "open"), environment.READY)

    def test_without_a_browser_play_goes_on_and_the_scene_view_asks(self):
        self.computer(browser=None, installer=pathlib.Path("gamestudio-browser.js"))
        needs = environment.check(None, "play")
        self.assertEqual([n.key for n in needs], ["browser"])
        self.assertGreater(needs[0].size_mb, 90)
        environment._consent = False  # "Not now"
        self.assertEqual(environment.ensure_ready(None, "play"), environment.READY,
                         "Play shows the game in a window instead (core/play.py)")
        self.assertEqual(environment.ensure_ready(None, "sceneview"), environment.ASKING)
        environment._asking = None
        self.assertIn("The Scene view needs", environment.ensure_ready(None, "sceneview", ask=False))

    def test_no_installer_no_browser_step(self):
        self.computer(browser=None, installer=None)
        self.assertEqual(environment.check(None, "sceneview"), [])

    def test_offline_says_so_plainly(self):
        self.computer(node=False, online=False)
        environment._consent = True
        self.assertEqual(environment.ensure_ready(None, "play"), environment.ASKING,
                         "the dialog offers to turn online access on")
        environment._asking = None
        problem = environment.ensure_ready(None, "play", ask=False)
        self.assertIn("online access is off", problem)
        self.assertIn("Allow Online Access", problem)

    def test_blender_without_a_window_needs_the_preference(self):
        self.computer(node=False, can_ask=False)
        self.record_runs()
        problem = environment.ensure_ready(None, "play")
        self.assertIn(environment.AUTO_SETUP_ENV, problem)
        os.environ[environment.AUTO_SETUP_ENV] = "1"
        self.assertEqual(environment.ensure_ready(None, "play"), environment.STARTED)
        self.assertEqual(self.runs[0][2], ["node"])

    def test_an_action_during_a_setup_waits_for_it(self):
        self.computer()
        done = []
        release = threading.Event()
        environment.JOB.start("Setting up GameStudio", [lambda job: release.wait(10)])
        self.assertEqual(environment.ensure_ready(None, "play", then=lambda ok, m: done.append((ok, m))),
                         environment.STARTED)
        release.set()
        self.assertTrue(environment.wait(20))
        self.assertEqual(done, [(True, "")], "checked again once it ended: nothing missing, so it plays")

    def test_a_setup_that_did_not_help_is_not_run_again_and_again(self):
        self.computer(node=False)
        environment._consent = True
        self.record_runs()
        environment._last_run = ("", ("node",), time.monotonic())
        problem = environment.ensure_ready(None, "play")
        self.assertIn("still missing", problem)
        self.assertEqual(self.runs, [])

    def test_play_after_a_skipped_setup(self):
        root = self.tmp / "game"
        write(root / "gamestudio.json", "{}")
        project.set_project_root_override(root)
        self.addCleanup(project.set_project_root_override, None)
        notes, plays = [], []
        self.patch(mock.patch.object(environment, "notify", lambda message, level="WARNING": notes.append(message)))
        self.patch(mock.patch.object(ops, "any_window", lambda: plays.append(1)))
        with mock.patch.object(environment, "missing_essentials", lambda *_a: [environment.Need("node", "Node.js", 36)]):
            ops._play_when_ready(False, "GameStudio's setup was skipped")
        self.assertEqual((notes, plays), (["GameStudio's setup was skipped"], []), "no Play without Node.js")
        with mock.patch.object(environment, "missing_essentials", lambda *_a: []):
            ops._play_when_ready(False, "skipped")  # only the browser was missing: it plays, in a window
        self.assertEqual(plays, [1])

    def test_a_failure_says_what_and_offers_retry(self):
        self.computer()
        shared = self.tmp / "sdk" / "runtime" / "packages"
        package(shared / "engine", "@gamestudio/engine", "0.1.0")
        self.patch(mock.patch.dict(os.environ, {toolchain.ENV_RUNTIME: str(shared)}))
        npm = fake_npm(self.tmp, 1, "npm error code ENOTFOUND\nnpm error request to https://registry.npmjs.org/left-pad failed")
        self.patch(mock.patch.object(environment, "npm_command", lambda: npm))
        root = self.tmp / "game"
        package(root, "game", dependencies={"left-pad": "1.3.0"})
        environment._consent = True
        self.assertEqual(environment.ensure_ready(root, "play"), environment.STARTED)
        self.assertFalse(environment.wait(30))
        message = environment.JOB.message
        self.assertIn("could not be reached", message)
        self.assertIn("Retry", message)
        text, icon, alert = environment.status_item()
        self.assertIn(message[:60], text, "the reason, in the status bar")
        self.assertEqual((icon, alert), ("ERROR", True))
        self.assertEqual(environment._retry["root"], root)
        retried = []
        with mock.patch.object(environment, "ensure_ready", lambda *a, **k: retried.append(a) or environment.STARTED):
            environment.retry()
        self.assertEqual(retried, [(root, "play", None)])


# --- The shared runtime: bug 1, and the user's case -------------------------------------------------


class ToolchainSharedRuntimeTests(_Env):
    def setUp(self):
        super().setUp()
        self.computer()
        self.bundle = ExtensionBundle(self, {"engine": {"three": "0.186.1"},
                                             "devserver": {"puppeteer-core": "25.13.0", "ws": "8.22.0"}})
        self.npm = fake_npm(self.tmp)
        self.patch(mock.patch.object(environment, "npm_command", lambda: self.npm))

    def test_toolchain_first_time_downloads_the_packages(self):
        needs = environment.check(None, "play")
        self.assertEqual([(n.key, n.size_mb) for n in needs], [("runtime", environment.RUNTIME_MB)])

    def test_toolchain_an_update_is_copied_offline_without_asking(self):
        self.bundle.install()
        self.assertEqual(environment.check(None, "play"), [])
        write(self.bundle.packages / "engine" / "src" / "index.js", "export const updated = true;\n")
        self.bundle.stamp()  # the extension was updated
        environment.invalidate()
        self.assertEqual([(n.key, n.size_mb) for n in environment.check(None, "play")], [("refresh", 0)])
        self.assertEqual(environment.ensure_ready(None, "play"), environment.STARTED, "no question: nothing downloads")
        self.assertEqual(self.asked, [])
        self.assertTrue(environment.wait(30), environment.JOB.message)
        self.assertIn("updated", (self.bundle.home / "packages" / "engine" / "src" / "index.js").read_text("utf-8"))
        self.assertFalse((self.tmp / "npm-calls.txt").exists(), "no npm install")
        self.assertTrue(toolchain.shared_runtime_ready(str(self.bundle.ext / "environment.py")))

    def test_toolchain_an_update_that_needs_a_new_package_asks(self):
        self.bundle.install()
        devserver = toolchain.read_manifest(self.bundle.packages / "devserver")
        devserver["dependencies"]["@puppeteer/browsers"] = "3.2.4"
        write(self.bundle.packages / "devserver" / "package.json", json.dumps(devserver))
        self.bundle.stamp()
        environment.invalidate()
        needs = environment.check(None, "play")
        self.assertEqual([(n.key, n.size_mb) for n in needs], [("runtime", environment.RUNTIME_UPDATE_MB)])
        self.assertEqual(environment.ensure_ready(None, "play"), environment.ASKING)

    def test_toolchain_a_missing_puppeteer_core_is_found(self):
        """The user's case: `npm install` stopped half way. Only the declared packages were checked, so
        GameStudio called it ready and the stream asked for `npm install`."""
        self.bundle.install(skip=("puppeteer-core",))
        start = str(self.bundle.ext / "environment.py")
        self.assertEqual(toolchain.missing_runtime_dependencies({"three": "0.186.1"}, start), ["puppeteer-core"])
        self.assertFalse(toolchain.shared_runtime_ready(start))
        self.assertEqual([n.key for n in environment.check(None, "play")], ["runtime"])

    def test_toolchain_a_wrong_version_counts_as_missing(self):
        self.bundle.install()
        package(self.bundle.home / "node_modules" / "puppeteer-core", "puppeteer-core", "24.0.0")
        self.assertEqual(toolchain.missing_runtime_dependencies({}, str(self.bundle.ext / "environment.py")),
                         ["puppeteer-core"])

    def test_toolchain_an_update_with_a_new_lock_asks(self):
        write(self.bundle.ext / "core" / toolchain.RUNTIME_LOCK, '{"lockfileVersion": 3}\n')
        self.bundle.install()
        self.assertEqual(environment.check(None, "play"), [], "installed with this lock")
        write(self.bundle.ext / "core" / toolchain.RUNTIME_LOCK, '{"lockfileVersion": 3, "packages": {}}\n')
        environment.invalidate()
        needs = environment.check(None, "play")
        self.assertEqual([(n.key, n.size_mb) for n in needs], [("runtime", environment.RUNTIME_UPDATE_MB)],
                         "every package is there, but the lock changed: npm installs it")
        self.assertEqual(environment.ensure_ready(None, "play"), environment.ASKING)

    def test_toolchain_the_install_step_runs_npm_through_node_and_empties_its_cache(self):
        cache = self.user / toolchain.NPM_CACHE / "_cacache"
        cache.mkdir(parents=True)
        lock = write(self.bundle.ext / "core" / toolchain.RUNTIME_LOCK, '{"lockfileVersion": 3}\n')
        bundle = self.bundle

        class Job:
            warnings = []

            def set(self, *_a):
                pass

            def run(self, argv, cwd, log_path, label):
                self.argv = argv
                self.lock = (cwd / "package-lock.json").read_bytes()  # beside the package.json npm installs
                bundle.install()  # what npm leaves

            def note(self, _text):
                pass

        job = Job()
        environment._step_shared_runtime(self.npm, True)(job)
        self.assertEqual(job.argv[-5:], environment.RUNTIME_INSTALL)
        self.assertEqual(job.argv[-5:], [*environment.NPM_INSTALL, "--ignore-scripts"], "no install scripts run")
        self.assertEqual(job.lock, lock.read_bytes(), "npm installs the bundle's lock")
        self.assertFalse(cache.exists(), "GameStudio's npm cache is emptied after the install")
        stamp = json.loads((self.bundle.home / toolchain.SHARED_STAMP).read_text("utf-8"))
        self.assertEqual(stamp["lock"], toolchain.runtime_lock_hash(str(self.bundle.ext / "environment.py")))


# --- Links into another GameStudio install: bug 2 ---------------------------------------------------


class ToolchainForeignLinkTests(_Env):
    def setUp(self):
        super().setUp()
        self.ours = self.user / "runtime"
        write(self.ours / toolchain.SHARED_STAMP, json.dumps({"installed": True}))
        package(self.ours / "packages" / "engine", "@gamestudio/engine", "0.2.0")
        package(self.ours / "node_modules" / "three", "three", "0.186.1")
        package(self.ours / "node_modules" / "vite", "vite", "8.3.4")
        self.shared = toolchain.Sdk(self.ours / "packages", self.ours / "node_modules")
        old = self.tmp / "Old GameStudio" / "gamestudio"  # a portable install: bundle.json
        write(old / toolchain.BUNDLE_MANIFEST, json.dumps({"kind": "editor"}))
        package(old / "runtime" / "packages" / "engine", "@gamestudio/engine", "0.1.0")
        package(old / "node_modules" / "three", "three", "0.186.1")
        self.old = old
        dev = self.tmp / "checkout" / "runtime" / "packages" / "devserver"  # a development checkout
        package(dev, "@gamestudio/devserver", "0.1.0")
        self.dev = dev
        self.root = self.tmp / "Documents" / "GameStudio" / "Examples" / "demo-game"
        package(self.root, "demo-game", dependencies={
            "@gamestudio/engine": "file:" + (old / "runtime" / "packages" / "engine").as_posix(),
            "three": "0.186.1"}, devDependencies={"@gamestudio/devserver": "file:" + dev.as_posix(),
                                                   "lodash": "4.17.21"})
        modules = self.root / "node_modules"
        toolchain.make_link(old / "runtime" / "packages" / "engine", modules / "@gamestudio" / "engine")
        toolchain.make_link(old / "node_modules" / "three", modules / "three")
        toolchain.make_link(dev, modules / "@gamestudio" / "devserver")
        package(modules / "lodash", "lodash", "4.17.21")  # npm's own install

    def target(self, name) -> str:
        return os.path.normcase(os.path.realpath(self.root / "node_modules" / name))

    def test_toolchain_found_and_linked_again(self):
        self.assertEqual(toolchain.install_root(self.old / "runtime" / "packages" / "engine"), self.old)
        self.assertEqual(toolchain.install_root(self.ours / "packages" / "engine"), self.ours)
        self.assertIsNone(toolchain.install_root(self.dev))
        foreign = toolchain.foreign_links(self.root, self.shared)
        self.assertEqual(foreign, ["@gamestudio/engine", "three"], "the checkout's link and npm's folder stay")
        result = toolchain.link_project(self.root, self.shared, relink=foreign)
        self.assertEqual(sorted(result["linked"]), foreign)
        self.assertEqual(self.target("@gamestudio/engine"),
                         os.path.normcase(os.path.realpath(self.ours / "packages" / "engine")))
        self.assertEqual(self.target("three"), os.path.normcase(os.path.realpath(self.ours / "node_modules" / "three")))
        self.assertEqual(self.target("@gamestudio/devserver"), os.path.normcase(os.path.realpath(self.dev)))
        self.assertFalse(toolchain.is_link(self.root / "node_modules" / "lodash"))
        self.assertTrue(self.old.joinpath("runtime", "packages", "engine", "package.json").is_file(), "theirs kept")
        self.assertEqual(toolchain.foreign_links(self.root, self.shared), [])

    def test_toolchain_package_json_points_here_again(self):
        changed = toolchain.repoint_specs(self.root, self.shared)
        self.assertEqual(changed, ["@gamestudio/engine"])
        manifest = toolchain.read_manifest(self.root)
        self.assertEqual(manifest["dependencies"]["@gamestudio/engine"],
                         "file:" + (self.ours / "packages" / "engine").as_posix())
        self.assertEqual(manifest["devDependencies"]["@gamestudio/devserver"], "file:" + self.dev.as_posix())
        self.assertEqual(toolchain.repoint_specs(self.root, self.shared), [])

    def test_toolchain_the_readiness_check_sees_it(self):
        self.computer()
        self.patch(mock.patch.object(toolchain, "sdk", lambda *_a: self.shared))
        self.assertIn("links", [n.key for n in environment.check(self.root, "play")])


class ToolchainBinShimTests(_Env):
    def test_toolchain_a_symlink_from_npm_is_replaced_not_written_through(self):
        script = write(self.tmp / "shared" / "vite" / "bin" / "vite.js", "#!/usr/bin/env node\n// vite\n")
        bin_dir = self.tmp / "game" / "node_modules" / ".bin"
        bin_dir.mkdir(parents=True)
        try:
            os.symlink(script, bin_dir / "vite")
        except (OSError, NotImplementedError):
            self.skipTest("this account cannot make symlinks")
        toolchain.write_bin_shims(bin_dir, "vite", "../vite/bin/vite.js")
        self.assertEqual(script.read_text("utf-8"), "#!/usr/bin/env node\n// vite\n", "the shared script is intact")
        self.assertFalse(os.path.islink(bin_dir / "vite"))
        self.assertIn("../vite/bin/vite.js", (bin_dir / "vite").read_text("utf-8"))

    def test_toolchain_a_file_is_replaced(self):
        bin_dir = self.tmp / ".bin"
        write(bin_dir / "vite", "old")
        toolchain.write_bin_shims(bin_dir, "vite", "../vite/bin/vite.js")
        self.assertIn("vite.js", (bin_dir / "vite").read_text("utf-8"))


# --- Node.js on macOS and Linux (fakes on any platform) ---------------------------------------------


class NodeTarTests(_Env):
    def tarball(self, extra_links=()) -> pathlib.Path:
        path = self.tmp / "node-v24.21.0-darwin-arm64.tar.gz"
        top = "node-v24.21.0-darwin-arm64"
        with tarfile.open(path, "w:gz") as tf:
            for name, data, mode in ((f"{top}/bin/node", b"\xcf\xfa\xed\xfe fake", 0o755),
                                     (f"{top}/lib/node_modules/npm/bin/npm-cli.js", b"// npm", 0o644)):
                info = tarfile.TarInfo(name)
                info.size, info.mode = len(data), mode
                tf.addfile(info, io.BytesIO(data))
            for name, target in (("bin/npm", "../lib/node_modules/npm/bin/npm-cli.js"), *extra_links):
                link = tarfile.TarInfo(f"{top}/{name}")
                link.type, link.linkname = tarfile.SYMTYPE, target
                tf.addfile(link)
        return path

    def test_the_filter_keeps_exec_bits_and_relative_links(self):
        dest = str(self.tmp / "dest")
        node = tarfile.TarInfo("top/bin/node")
        node.mode = 0o755
        self.assertEqual(nodejs.tar_filter(node, dest).mode & 0o700, 0o700)
        link = tarfile.TarInfo("top/bin/npm")
        link.type, link.linkname = tarfile.SYMTYPE, "../lib/node_modules/npm/bin/npm-cli.js"
        self.assertIsNotNone(nodejs.tar_filter(link, dest))
        absolute = tarfile.TarInfo("top/bin/evil")
        absolute.type, absolute.linkname = tarfile.SYMTYPE, "/etc/passwd"
        self.assertIsNone(nodejs.tar_filter(absolute, dest), "skipped, not a failed install")
        with self.assertRaises(tarfile.FilterError):
            nodejs.tar_filter(tarfile.TarInfo("../outside"), dest)

    def test_unpacks_into_a_folder_with_spaces(self):
        dest = self.tmp / "Library" / "Application Support" / "Blender" / "node"
        nodejs.extract(self.tarball(extra_links=[("bin/evil", "/etc/passwd")]), dest)
        self.assertTrue((dest / "bin" / "node").is_file())
        self.assertTrue((dest / "bin" / "npm").exists(), "the relative link (a copy where links are not allowed)")
        self.assertFalse(os.path.lexists(dest / "bin" / "evil"))
        if not WINDOWS:
            self.assertTrue(os.access(dest / "bin" / "node", os.X_OK))
            self.assertTrue(os.path.islink(dest / "bin" / "npm"))

    def test_platform_keys(self):
        for platform, machine, key in (("darwin", "arm64", "darwin-arm64"), ("darwin", "x86_64", "darwin-x64"),
                                       ("linux", "aarch64", "linux-arm64"), ("linux", "x86_64", "linux-x64"),
                                       ("win32", "AMD64", "win-x64"), ("freebsd14", "amd64", None)):
            with mock.patch.object(nodejs.sys, "platform", platform), \
                    mock.patch.object(nodejs.platform, "machine", lambda m=machine: m):
                self.assertEqual(nodejs.platform_key(), key, (platform, machine))
        for key in ("darwin-arm64", "darwin-x64", "linux-x64", "linux-arm64"):
            self.assertTrue(nodejs.release(key=key)["name"].endswith(".tar.gz"))

    def test_npm_runs_through_the_portable_node(self):
        folder = self.user / toolchain.PORTABLE_NODE
        write(folder / "bin" / "node", "")
        cli = write(folder / "lib" / "node_modules" / "npm" / "bin" / "npm-cli.js", "")
        with mock.patch.object(toolchain, "_WINDOWS", False), \
                mock.patch.object(toolchain, "_system_node_info", lambda: (None, None)), \
                mock.patch.object(toolchain, "bundled_node_dir", lambda *_a: None):
            self.assertEqual(toolchain.npm_argv("npm"), [str(folder / "bin" / "node"), str(cli)])
            env = toolchain.child_env({"PATH": "/usr/bin:/bin"})
        self.assertEqual(env["PATH"].split(os.pathsep)[0], str(folder / "bin"))
        self.assertEqual(env["npm_config_cache"], str(self.user / toolchain.NPM_CACHE), "never the user's ~/.npm")
        self.assertEqual(env[toolchain.ENV_BROWSER_DIR], str(self.user / toolchain.BROWSER))

    def test_the_system_npm_through_its_node(self):
        prefix = self.tmp / "opt homebrew"
        node = write(prefix / "bin" / "node", "")
        cli = write(prefix / "lib" / "node_modules" / "npm" / "bin" / "npm-cli.js", "")
        self.assertEqual(toolchain.system_npm_cli(str(node)), cli)
        win = self.tmp / "nodejs"
        exe = write(win / "node.exe", "")
        cli = write(win / "node_modules" / "npm" / "bin" / "npm-cli.js", "")
        self.assertEqual(toolchain.system_npm_cli(str(exe)), cli)

    def test_node_where_the_finder_does_not_look(self):
        """Blender started from the Finder: PATH=/usr/bin:/bin:/usr/sbin:/sbin, Node.js in Homebrew or nvm."""
        home = self.tmp / "home"
        for version in ("v20.11.1", "v22.12.0", "v24.1.0"):
            (home / ".nvm" / "versions" / "node" / version / "bin").mkdir(parents=True)
        dirs = toolchain.node_search_dirs(env={}, home=home, windows=False)
        self.assertEqual(dirs[:2], [pathlib.Path("/opt/homebrew/bin"), pathlib.Path("/usr/local/bin")])
        nvm = [d.parent.name for d in dirs if ".nvm" in d.parts]
        self.assertEqual(nvm, ["v24.1.0", "v22.12.0", "v20.11.1"], "newest first")
        self.assertEqual(toolchain.node_search_dirs(env={"ProgramFiles": "C:/PF"}, home=home, windows=True),
                         [pathlib.Path("C:/PF") / "nodejs"])
        brew = self.tmp / "brew bin"
        node = write(brew / ("node.exe" if WINDOWS else "node"), "")
        toolchain.forget_node()
        self.addCleanup(toolchain.forget_node)
        with mock.patch.object(toolchain.shutil, "which", lambda *_a, **_k: None), \
                mock.patch.object(toolchain, "node_search_dirs", lambda: [self.tmp / "nothing", brew]), \
                mock.patch.object(toolchain, "node_version", lambda _exe: (24, 1, 0)), \
                mock.patch.object(toolchain, "bundled_node_dir", lambda *_a: None):
            self.assertEqual(toolchain.system_node(), str(node))
            self.assertEqual(toolchain.node_status()["source"], "system")
            # Its folder goes first on every child's PATH: a custom npm command's `#!/usr/bin/env node` finds it.
            self.assertEqual(toolchain.child_env({"PATH": "/usr/bin"})["PATH"].split(os.pathsep)[0], str(brew))


# --- The game browser -----------------------------------------------------------------------------------

FAKE_INSTALLER = r'''
import json, pathlib, sys
args = sys.argv[1:]
folder = pathlib.Path(args[args.index("--dir") + 1])
if (folder.parent / "fail").exists():
    print(json.dumps({"error": "Could not download the browser: no connection to the download server (ENOTFOUND). Check the internet connection and try again.", "offline": True}))
    sys.exit(1)
print(json.dumps({"stage": "download", "total": 124737213, "url": "https://example.invalid/shell.zip"}), flush=True)
for p in (0.25, 0.5, 1):
    print(json.dumps({"progress": p, "bytes": int(124737213 * p), "total": 124737213}), flush=True)
print(json.dumps({"stage": "extract"}), flush=True)
print(json.dumps({"stage": "verify"}), flush=True)
exe = folder / "chrome-headless-shell" / "win64-155" / "chrome-headless-shell-win64" / "chrome-headless-shell.exe"
exe.parent.mkdir(parents=True, exist_ok=True)
exe.write_text("fake")
(folder / "browser.json").write_text(json.dumps({"browser": "chrome-headless-shell",
    "executable": exe.relative_to(folder).as_posix()}))
print(json.dumps({"path": str(exe), "version": "155", "downloaded": True}), flush=True)
'''


class BrowserTests(_Env):
    def setUp(self):
        super().setUp()
        os.environ["GAMESTUDIO_BROWSER_SYSTEM"] = "0"  # a computer without Edge, Chrome or Chromium
        for name in ("GAMESTUDIO_BROWSER", "GS_BROWSER", toolchain.ENV_BROWSER_DIR):
            os.environ.pop(name, None)
        self.installer = write(self.tmp / "devserver" / "bin" / "gamestudio-browser.js", FAKE_INSTALLER)

    def test_the_marker_names_the_browser(self):
        folder = toolchain.browser_dir()
        self.assertEqual(folder, self.user / "browser")
        self.assertIsNone(toolchain.downloaded_browser())
        exe = write(folder / "chrome-headless-shell" / "x" / "chrome-headless-shell.exe", "")
        write(folder / "browser.json", json.dumps({"executable": "chrome-headless-shell/x/chrome-headless-shell.exe"}))
        self.assertEqual(toolchain.downloaded_browser(), str(exe))
        self.assertEqual(stream.browser_path(), str(exe), "after the system browsers, which are off here")
        exe.unlink()
        self.assertIsNone(stream.browser_path())
        self.assertIn("game browser is not set up", stream.NO_BROWSER)

    def test_progress_lines(self):
        job = environment._Job()
        found = {}
        environment._browser_line(job, '{"stage":"download","total":124737213}', found)
        self.assertEqual((job.message, job.progress), ("Downloading the game browser", 0.0))
        environment._browser_line(job, '{"progress":0.5,"bytes":62368606,"total":124737213}', found)
        self.assertEqual((job.message, job.progress), ("Downloading the game browser (62 of 125 MB)", 0.5))
        environment._browser_line(job, '{"stage":"extract"}', found)
        self.assertEqual(job.message, "Unpacking the game browser")
        environment._browser_line(job, '{"path":"C:/b/chrome-headless-shell.exe","downloaded":true}', found)
        environment._browser_line(job, "not json", found)
        environment._browser_line(job, '{"error":"The browser download was interrupted. Try again."}', found)
        self.assertEqual(found["path"], "C:/b/chrome-headless-shell.exe")
        self.assertEqual(found["error"], "The browser download was interrupted. Try again.")

    def _run_step(self):
        with mock.patch.object(environment, "browser_installer", lambda: self.installer), \
                mock.patch.object(toolchain, "node_executable", lambda *_a: sys.executable), \
                mock.patch.object(environment, "online", lambda: True):
            environment.JOB.start("Setting up GameStudio", [environment._step_browser(True, True)])
            return environment.wait(60)

    def test_the_step_downloads_once(self):
        self.assertTrue(self._run_step(), environment.JOB.message)
        self.assertTrue(environment.find_browser().endswith("chrome-headless-shell.exe"))
        self.assertIn('"progress": 0.5', (self.user / "browser" / "install.log").read_text("utf-8"))

    def test_a_failed_download_says_why(self):
        write(self.user / "fail", "")
        self.assertFalse(self._run_step())
        self.assertIn("Check the internet connection", environment.JOB.message)


# --- What the user reads --------------------------------------------------------------------------


class MessageTests(unittest.TestCase):
    """No message tells the user to run npm, install Node.js or install a browser: GameStudio does it."""

    FORBIDDEN = ("npm install", "install node", "install one", "from nodejs.org", "run npm", "install a browser")

    def test_no_install_yourself_advice(self):
        found = []
        for path in sorted(ADDON.rglob("*.py")):
            rel = path.relative_to(ADDON).as_posix()
            if "/_vendor/" in f"/{rel}" or rel.startswith("tests/"):
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            docstrings = set()
            for node in ast.walk(tree):
                if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.body:
                    first = node.body[0]
                    if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                        docstrings.add(id(first.value))
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings:
                    text = node.value.lower()
                    hits = [phrase for phrase in self.FORBIDDEN if phrase in text]
                    if hits:
                        found.append(f"{rel}:{node.lineno}: {node.value[:80]!r}")
        self.assertEqual(found, [])

    def test_the_stream_says_what_is_missing(self):
        for text in (stream.NO_PACKAGES, stream.NO_PUPPETEER, stream.NO_NODE, stream.NO_BROWSER):
            self.assertNotIn("install", text.lower())
            self.assertNotIn("npm", text.lower())

    def test_the_consent_dialog(self):
        layout = _Recorder()
        needs = [environment.Need("node", "Node.js 24.21.0", 36), environment.Need("runtime", "the game packages", 60),
                 environment.Need("browser", "a game browser (there is no Chrome or Edge)", 125),
                 environment.Need("links", "the project's links to the game packages", 0)]
        with mock.patch.object(environment, "online", lambda: True):
            environment.draw_consent(layout, needs)
        self.assertIn("GameStudio needs to set itself up (one time):", layout.texts)
        for text in ("Node.js 24.21.0", "about 36 MB", "The game packages", "about 60 MB",
                     "A game browser (there is no Chrome or Edge)", "about 125 MB"):
            self.assertIn(text, layout.texts)
        self.assertTrue(any(t.startswith("They go in ") for t in layout.texts), layout.texts)
        self.assertTrue(any("without downloading" in t for t in layout.texts), layout.texts)
        layout = _Recorder()
        with mock.patch.object(environment, "online", lambda: False):
            environment.draw_consent(layout, needs[:1])
        self.assertTrue(any("online access is off" in t for t in layout.texts), layout.texts)


class _Recorder:
    def __init__(self):
        self.texts = []

    def __getattr__(self, name):
        return lambda *_a, **_k: self

    def __setattr__(self, name, value):
        object.__setattr__(self, name, value)

    def label(self, text="", **_kwargs):
        self.texts.append(text)


class ProjectOpenTests(_Env):
    """Open Project runs the readiness check (not only when packages are missing)."""

    def test_open_runs_the_check(self):
        root = self.tmp / "game"
        write(root / "gamestudio.json", json.dumps({"name": "Game"}))
        (root / "scenes").mkdir()
        bpy.ops.wm.read_homefile(use_empty=True)
        bpy.ops.wm.save_as_mainfile(filepath=str(root / "scenes" / "main.blend"))
        calls = []
        with mock.patch.object(environment, "ensure_ready", lambda root, purpose, *a, **k: calls.append(
                (pathlib.Path(root), purpose)) or environment.STARTED):
            self.assertEqual(bpy.ops.gamestudio.project_open(path=str(root)), {"FINISHED"})
        self.assertEqual([(r.resolve(), purpose) for r, purpose in calls], [(root.resolve(), "open")])
        project.invalidate_cache()
        bpy.ops.wm.read_homefile(use_empty=True)


if __name__ == "__main__":
    unittest.main()
