# SPDX-License-Identifier: GPL-3.0-or-later
"""Node.js, the shared packages, project setup and packaging: environment.py, core/nodejs.py,
core/toolchain.py, projects.py and tools/build_extension.py.

Runs inside run_shell_tests.py. Nothing goes online: downloads come from a local HTTP server, npm
is a fake script, and Node.js versions are mocked where a real node would have to match the pin.
"""

import functools
import hashlib
import http.server
import importlib
import importlib.util
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
import zipfile
from unittest import mock

import bpy

import test_editors as te

EXTENSION = te.EXTENSION
REPO = pathlib.Path(__file__).resolve().parents[3]
WINDOWS = sys.platform == "win32"

environment = importlib.import_module(f"{EXTENSION}.environment")
nodejs = importlib.import_module(f"{EXTENSION}.core.nodejs")
toolchain = importlib.import_module(f"{EXTENSION}.core.toolchain")
projects = importlib.import_module(f"{EXTENSION}.projects")
ops = importlib.import_module(f"{EXTENSION}.ops")
project = te.project


def _write(path: pathlib.Path, data="") -> pathlib.Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data if isinstance(data, bytes) else data.encode("utf-8"))
    return path


def _package(folder: pathlib.Path, name: str, version: str = "1.0.0", **fields) -> pathlib.Path:
    _write(folder / "package.json", json.dumps({"name": name, "version": version, **fields}))
    return folder


def _sha256(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fake_npm(folder: pathlib.Path, exit_code: int = 0, output: str = "") -> pathlib.Path:
    """A command standing in for npm: records its arguments; `install` creates the missing packages
    of the project in the current folder, `run registry` writes the registry."""
    script = _write(folder / "fake_npm.py", f'''
import json, os, pathlib, sys
cwd = pathlib.Path.cwd()
with open(cwd / "npm-calls.txt", "a", encoding="utf-8") as out:
    out.write(" ".join(sys.argv[1:]) + "\\n")
print({output!r})
if {exit_code}:
    sys.exit({exit_code})
if sys.argv[1:2] == ["install"]:
    manifest = json.loads((cwd / "package.json").read_text(encoding="utf-8"))
    for field in ("dependencies", "devDependencies"):
        for name in manifest.get(field) or {{}}:
            target = cwd / "node_modules" / name
            if not (target / "package.json").is_file():
                target.mkdir(parents=True, exist_ok=True)
                (target / "package.json").write_text(json.dumps({{"name": name, "version": "1.0.0"}}))
if sys.argv[1:3] == ["run", "registry"]:
    (cwd / ".gamestudio").mkdir(exist_ok=True)
    (cwd / ".gamestudio" / "components.json").write_text(json.dumps({{"components": []}}))
''')
    if WINDOWS:
        return _write(folder / "fake-npm.cmd", f'@"{sys.executable}" "{script}" %*\r\n')
    shim = _write(folder / "fake-npm", f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n')
    shim.chmod(0o755)
    return shim


class _Temp(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix="gs_env_"))
        self._user_dir = toolchain.user_dir()

    def tearDown(self):
        toolchain.set_user_dir(self._user_dir)
        environment.JOB.__init__()
        shutil.rmtree(self.tmp, ignore_errors=True)


# --- The Node.js pin ------------------------------------------------------------------------


class NodePinTests(unittest.TestCase):
    PLATFORMS = {"win-x64": "windows-x64", "win-arm64": "windows-arm64", "darwin-x64": "macos-x64",
                 "darwin-arm64": "macos-arm64", "linux-x64": "linux-x64", "linux-arm64": "linux-arm64"}

    def test_every_platform_is_pinned(self):
        lock = nodejs.pin()
        self.assertEqual(set(lock["files"]), set(self.PLATFORMS))
        for key, entry in lock["files"].items():
            self.assertRegex(entry["sha256"], r"^[0-9a-f]{64}$", key)
            archive = "zip" if key.startswith("win") else "tar.gz"
            self.assertEqual(entry["name"], f"node-v{lock['version']}-{key}.{archive}")
        self.assertEqual(nodejs.release(key="win-x64")["url"],
                         f"https://nodejs.org/dist/v{lock['version']}/node-v{lock['version']}-win-x64.zip")
        self.assertGreaterEqual(toolchain.parse_version(lock["version"]), toolchain.MIN_NODE)

    def test_same_pin_as_the_editor_installer(self):
        editor = REPO / "editor" / "installer" / "node.lock.json"
        if not editor.is_file():
            self.skipTest("no editor/installer/node.lock.json")
        theirs = json.loads(editor.read_text(encoding="utf-8"))
        ours = nodejs.release(key="win-x64")
        self.assertEqual((ours["version"], ours["url"], ours["sha256"]),
                         (theirs["version"], theirs["url"], theirs["sha256"]))

    def test_manifest_platforms_have_a_download(self):
        import tomllib

        path = pathlib.Path(nodejs.__file__).parents[1] / "blender_manifest.toml"
        manifest = tomllib.loads(path.read_text("utf-8"))
        self.assertEqual(set(manifest["platforms"]), set(self.PLATFORMS.values()))

    def test_this_platform(self):
        self.assertIn(nodejs.platform_key(), self.PLATFORMS)


# --- Downloading and unpacking Node.js -------------------------------------------------------------


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *_args):
        pass


class NodeInstallTests(_Temp):
    VERSION = "24.21.0"

    def setUp(self):
        super().setUp()
        self.served = self.tmp / "served"
        self.served.mkdir()
        handler = functools.partial(_QuietHandler, directory=str(self.served))
        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}/"
        self.user = self.tmp / "user"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        super().tearDown()

    def _zip(self, key="win-x64") -> pathlib.Path:
        top = f"node-v{self.VERSION}-{key}"
        path = self.served / f"{top}.zip"
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr(f"{top}/node.exe", b"MZ fake node")
            zf.writestr(f"{top}/node_modules/npm/bin/npm-cli.js", "// npm")
            zf.writestr(f"{top}/LICENSE", "MIT")
        return path

    def _tar(self, key="linux-x64") -> pathlib.Path:
        top = f"node-v{self.VERSION}-{key}"
        path = self.served / f"{top}.tar.gz"
        with tarfile.open(path, "w:gz") as tf:
            for name, data in ((f"{top}/bin/node", b"\x7fELF fake"),
                               (f"{top}/lib/node_modules/npm/bin/npm-cli.js", b"// npm")):
                info = tarfile.TarInfo(name)
                info.size, info.mode = len(data), 0o755
                tf.addfile(info, io.BytesIO(data))
            link = tarfile.TarInfo(f"{top}/bin/npm")
            link.type, link.linkname = tarfile.SYMTYPE, "../lib/node_modules/npm/bin/npm-cli.js"
            tf.addfile(link)
        return path

    def _lock(self, archive: pathlib.Path, key: str, sha256: str | None = None) -> dict:
        return {"version": self.VERSION, "base_url": self.base,
                "files": {key: {"name": archive.name, "sha256": sha256 or _sha256(archive), "size_mb": 1}}}

    def test_install_downloads_verifies_and_unpacks(self):
        key = "win-x64" if WINDOWS else "linux-x64"
        archive = self._zip(key) if WINDOWS else self._tar(key)
        seen = []
        with mock.patch.object(toolchain, "node_version", lambda _exe: (24, 21, 0)):
            folder = nodejs.install(self.user, progress=lambda text, fraction: seen.append((text, fraction)),
                                    lock=self._lock(archive, key), key=key)
        self.assertEqual(folder, self.user / "node")
        self.assertTrue((folder / ("node.exe" if WINDOWS else "bin/node")).is_file())
        self.assertEqual(nodejs.installed(self.user)["version"], self.VERSION)
        self.assertEqual(list((self.user / "downloads").iterdir()), [], "the archive is removed")
        self.assertTrue(any(text.startswith("Downloading Node.js") for text, _f in seen), seen)
        self.assertTrue(all(f is None or 0.0 <= f <= 1.0 for _text, f in seen), seen)

    def test_checksum_mismatch_installs_nothing(self):
        archive = self._zip()
        with self.assertRaises(nodejs.NodeInstallError) as cm:
            nodejs.install(self.user, lock=self._lock(archive, "win-x64", sha256="0" * 64), key="win-x64")
        self.assertIn("SHA-256", str(cm.exception))
        self.assertFalse((self.user / "node").exists())
        self.assertEqual(list((self.user / "downloads").iterdir()), [], "no partial download is left")

    def test_download_errors_say_what_happened(self):
        lock = {"version": self.VERSION, "base_url": self.base,
                "files": {"win-x64": {"name": "missing.zip", "sha256": "0" * 64, "size_mb": 1}}}
        with self.assertRaises(nodejs.NodeInstallError) as cm:
            nodejs.install(self.user, lock=lock, key="win-x64")
        self.assertIn("404", str(cm.exception))
        lock["base_url"] = "http://127.0.0.1:9/"
        with self.assertRaises(nodejs.NodeInstallError) as cm:
            nodejs.install(self.user, lock=lock, key="win-x64")
        self.assertIn("Check the internet connection", str(cm.exception))
        with self.assertRaises(nodejs.NodeInstallError):
            nodejs.install(self.user, lock={"version": "1", "base_url": "", "files": {}}, key="win-x64")

    def test_cancel(self):
        archive = self._zip()
        cancel = threading.Event()
        cancel.set()
        with self.assertRaises(nodejs.NodeInstallError) as cm:
            nodejs.install(self.user, cancel=cancel, lock=self._lock(archive, "win-x64"), key="win-x64")
        self.assertIn("cancelled", str(cm.exception))

    def test_tar_archives_lose_their_top_folder(self):
        archive = self._tar()
        nodejs.extract(archive, self.tmp / "node")
        self.assertTrue((self.tmp / "node" / "bin" / "node").is_file())
        self.assertTrue((self.tmp / "node" / "lib" / "node_modules" / "npm" / "bin" / "npm-cli.js").is_file())


# --- Which Node.js -------------------------------------------------------------------------------


class NodeChoiceTests(_Temp):
    """System Node.js 22.12+ first, then the portable one GameStudio installed."""

    def setUp(self):
        super().setUp()
        self.user = self.tmp / "user"
        toolchain.set_user_dir(self.user)
        self.exe = _write(self.user / "node" / ("node.exe" if WINDOWS else "bin/node"), b"")

    def _system(self, version):
        info = ("C:/Program Files/nodejs/node.exe", version) if version else (None, None)
        return mock.patch.object(toolchain, "_system_node_info", lambda: info)

    def test_system_node_wins_when_new_enough(self):
        with self._system((22, 12, 0)):
            self.assertIsNone(toolchain.node_dir())
            self.assertEqual(toolchain.node_status()["source"], "system")
            path = toolchain.child_env({"PATH": "X"})["PATH"].split(os.pathsep)
            self.assertEqual(path, [str(pathlib.Path("C:/Program Files/nodejs")), "X"], "the chosen Node.js first")

    def test_portable_node_without_a_usable_system_node(self):
        with self._system((20, 11, 1)), mock.patch.object(toolchain, "node_version", lambda _exe: (24, 21, 0)):
            self.assertEqual(toolchain.node_dir(), self.exe.parent if WINDOWS else self.exe.parent.parent)
            status = toolchain.node_status()
            self.assertEqual((status["source"], status["version"], status["ok"]), ("portable", "24.21.0", True))
            path = toolchain.child_env({"PATH": "X"})["PATH"].split(os.pathsep)
            self.assertEqual((path[0], path[-1]), (str(self.exe.parent), "X"))
            self.assertEqual(toolchain.child_env({"PATH": "X"})["npm_config_cache"],
                             str(self.user / toolchain.NPM_CACHE), "GameStudio's own npm cache, not ~/.npm")
            self.assertEqual(toolchain.child_env({"PATH": "X", "npm_config_cache": "Y"})["npm_config_cache"], "Y")

    def test_problems_are_explained(self):
        shutil.rmtree(self.user / "node")
        with self._system((20, 11, 1)):
            status = toolchain.node_status()
            self.assertFalse(status["ok"])
            self.assertIn("20.11.1 is too old", status["problem"])
        with self._system(None):
            self.assertIn("22.12.0 or newer was not found", toolchain.node_status()["problem"])
            self.assertEqual(environment.needs_download()[:1], ["node"])

    def test_install_step_reports_progress(self):
        shutil.rmtree(self.user / "node")
        seen = []

        def fake_install(folder, progress=None, cancel=None):
            progress("Downloading Node.js (19 of 38 MB)", 0.5)
            seen.append(environment.JOB.status_text())
            progress("Unpacking Node.js 24.21.0", None)
            seen.append(environment.JOB.status_text())

        environment.JOB.state = "running"
        with self._system(None), mock.patch.object(nodejs, "install", fake_install):
            environment._step_node(self.user, True)(environment.JOB)
            with self.assertRaises(environment.SetupError) as cm:
                environment._step_node(self.user, False)(environment.JOB)
        self.assertEqual(seen, ["Downloading Node.js (19 of 38 MB) 50%", "Unpacking Node.js 24.21.0"])
        self.assertIn("online access is off", str(cm.exception))

    def test_real_lookup_runs(self):
        status = toolchain.node_status()
        self.assertIn(status["source"], ("system", "portable", None))
        self.assertEqual(toolchain.parse_version("v22.12.0"), (22, 12, 0))
        self.assertIsNone(toolchain.parse_version("node: not found"))


# --- The extension's shared runtime --------------------------------------------------------------


class SharedRuntimeTests(_Temp):
    """An installed extension: its packages are copied to the user folder and npm-installed once."""

    def setUp(self):
        super().setUp()
        self.ext = self.tmp / "ext"
        packages = self.ext / "runtime" / "packages"
        _package(packages / "engine", "@gamestudio/engine", "0.1.0", dependencies={"three": "0.186.1"})
        _write(packages / "engine" / "src" / "index.ts", "export {};\n")
        _package(packages / "devserver", "@gamestudio/devserver", "0.1.0", dependencies={"ws": "8.22.0"})
        self._bundle(toolchain.runtime_hash(packages))
        self.start = self.ext / "core" / "toolchain.py"
        self.user = self.tmp / "user"
        toolchain.set_user_dir(self.user)
        self.home = self.user / "runtime"

    def _bundle(self, digest):
        _write(self.ext / "bundle.json", json.dumps({"kind": "extension", "runtime": {"hash": digest}}))

    def _npm_install(self):
        """What `npm install` leaves in the shared runtime."""
        modules = self.home / "node_modules"
        for name in ("@gamestudio/engine", "@gamestudio/devserver"):
            if not os.path.lexists(modules / name):  # sync_shared_runtime links them itself
                toolchain.make_link(self.home / "packages" / name.split("/")[1], modules / name)
        for name, version in (("three", "0.186.1"), ("ws", "8.22.0"), ("vite", "8.3.4")):
            _package(modules / name, name, version)

    def test_bundle_detection(self):
        self.assertTrue(toolchain.is_extension_bundle(self.start))
        self.assertEqual(toolchain.bundled_packages(self.start), self.ext / "runtime" / "packages")
        self.assertEqual(toolchain.sdk(self.start), toolchain.Sdk(self.home / "packages", None))
        self.assertFalse(toolchain.shared_runtime_ready(self.start))
        # This checkout is no bundle: projects link to the repository's packages.
        self.assertFalse(toolchain.is_extension_bundle(environment.__file__))
        self.assertTrue(toolchain.shared_runtime_ready(environment.__file__))

    def test_sync_install_and_link(self):
        self.assertTrue(toolchain.sync_shared_runtime({"vite": "8.3.4", "@gamestudio/mcp": "x"}, self.start))
        self.assertEqual(toolchain.package_names(self.home / "packages"), ["devserver", "engine"])
        self.assertEqual(json.loads((self.home / "package.json").read_text("utf-8"))["dependencies"], {
            "@gamestudio/devserver": "file:packages/devserver", "@gamestudio/engine": "file:packages/engine",
            "vite": "8.3.4"})
        self.assertFalse(toolchain.shared_runtime_ready(self.start), "npm install has not run")
        self._npm_install()
        toolchain.mark_shared_runtime_installed(self.start)
        self.assertTrue(toolchain.shared_runtime_ready(self.start))
        self.assertFalse(toolchain.sync_shared_runtime({"vite": "8.3.4"}, self.start), "nothing to do now")
        shared = toolchain.sdk(self.start)
        self.assertEqual(shared.modules, self.home / "node_modules")

        game = self.tmp / "game"
        _package(game, "game", dependencies={"@gamestudio/engine": "file:x", "three": "0.186.1", "lodash": "4"})
        self.assertEqual(toolchain.unlinkable_dependencies(game, shared), ["lodash"])
        result = toolchain.link_project(game, shared)
        self.assertEqual(sorted(result["linked"]), ["@gamestudio/engine", "three"])
        self.assertTrue((game / "node_modules" / "@gamestudio" / "engine" / "src" / "index.ts").is_file())

    def test_an_update_recopies_the_packages(self):
        toolchain.sync_shared_runtime({}, self.start)
        self._npm_install()
        toolchain.mark_shared_runtime_installed(self.start)
        packages = self.ext / "runtime" / "packages"
        _write(packages / "engine" / "src" / "index.ts", "export const changed = 1;\n")
        shutil.rmtree(packages / "devserver")
        self._bundle(toolchain.runtime_hash(packages))
        self.assertFalse(toolchain.shared_runtime_ready(self.start))
        self.assertEqual(toolchain.shared_runtime_state({}, self.start), "refresh", "an update: copied, offline")
        self.assertFalse(toolchain.sync_shared_runtime({}, self.start), "it needs nothing new: no npm install")
        self.assertIn("changed", (self.home / "packages" / "engine" / "src" / "index.ts").read_text("utf-8"))
        self.assertEqual(toolchain.package_names(self.home / "packages"), ["engine"])
        self.assertFalse(os.path.lexists(self.home / "node_modules" / "@gamestudio" / "devserver"), "stale link gone")
        toolchain.mark_shared_runtime_installed(self.start)
        self.assertTrue(toolchain.shared_runtime_ready(self.start))

    def test_the_lock_goes_beside_the_package_json(self):
        lock = _write(self.ext / "core" / toolchain.RUNTIME_LOCK, '{\r\n  "lockfileVersion": 3\r\n}\r\n')
        self.assertTrue(toolchain.sync_shared_runtime({}, self.start))
        self.assertEqual((self.home / "package-lock.json").read_bytes(), b'{\n  "lockfileVersion": 3\n}\n')
        self._npm_install()
        toolchain.mark_shared_runtime_installed(self.start)
        stamp = json.loads((self.home / toolchain.SHARED_STAMP).read_text("utf-8"))
        self.assertEqual(stamp["lock"], toolchain.runtime_lock_hash(self.start))
        self.assertTrue(toolchain.shared_runtime_ready(self.start))
        _write(self.home / "package-lock.json", "{}")  # npm rewrote it
        self.assertFalse(toolchain.sync_shared_runtime({}, self.start), "the same lock: no npm install")
        self.assertEqual((self.home / "package-lock.json").read_bytes(), b'{\n  "lockfileVersion": 3\n}\n', "ours again")

        # An update with a new lock: npm installs it, though every package is there.
        _write(lock, '{"lockfileVersion": 3, "packages": {}}\n')
        self.assertEqual(toolchain.shared_runtime_state({}, self.start), "install")
        self.assertTrue(toolchain.sync_shared_runtime({}, self.start))
        self.assertIn(b'"packages"', (self.home / "package-lock.json").read_bytes())
        toolchain.mark_shared_runtime_installed(self.start)
        self.assertEqual(toolchain.shared_runtime_state({}, self.start), "")

        # An update with new package sources and the same lock: copied, offline.
        packages = self.ext / "runtime" / "packages"
        _write(packages / "engine" / "src" / "index.ts", "export const changed = 1;\n")
        self._bundle(toolchain.runtime_hash(packages))
        self.assertEqual(toolchain.shared_runtime_state({}, self.start), "refresh")
        self.assertFalse(toolchain.sync_shared_runtime({}, self.start), "the lock the last install used")
        toolchain.mark_shared_runtime_installed(self.start)
        self.assertTrue(toolchain.shared_runtime_ready(self.start))

    def test_runtime_hash(self):
        packages = self.ext / "runtime" / "packages"
        before = toolchain.runtime_hash(packages)
        _package(packages / "engine" / "node_modules" / "three", "three")
        self.assertEqual(toolchain.runtime_hash(packages), before, "node_modules never count")
        _write(packages / "engine" / "src" / "extra.ts", "")
        self.assertNotEqual(toolchain.runtime_hash(packages), before)


# --- Projects ------------------------------------------------------------------------------------


class ProjectSetupTests(_Temp):
    """New Project, opening a project and Play set up the packages through one background job."""

    def test_a_finished_setup_stops_offering_itself(self):
        """The GameStudio menu offers Set Up GameStudio... from what the last check found
        (cached_needs); once a setup succeeds, that list is old."""
        need = environment.Need("packages", "the game packages", 70)
        root = self.tmp / "game"
        with mock.patch.dict(environment._known, {environment._key(root, "open"): [need]}, clear=True), \
                mock.patch.object(environment, "show_failure", lambda _message: None):
            self.assertEqual(environment.cached_needs(root), [need])
            environment._finished(root, "open", True, ("packages",))(False, "Could not download it")
            self.assertEqual(environment.cached_needs(root), [need], "a failed setup leaves it offered")
            environment._finished(root, "open", True, ("packages",))(True, "")
            self.assertEqual(environment.cached_needs(root), [])

    def setUp(self):
        super().setUp()
        packages = self.tmp / "sdk" / "runtime" / "packages"
        for name in ("engine", "devserver"):
            _package(packages / name, f"@gamestudio/{name}", "0.1.0")
        _package(self.tmp / "sdk" / "node_modules" / "three", "three", "0.186.1")
        _package(self.tmp / "sdk" / "node_modules" / "vite", "vite", "8.3.4")
        self.packages = packages
        for patch in (mock.patch.dict(os.environ, {toolchain.ENV_RUNTIME: str(packages)}),
                      mock.patch.object(environment, "online", lambda: True)):  # factory settings have it off
            patch.start()
            self.addCleanup(patch.stop)
        self.prefs = bpy.context.preferences.addons[EXTENSION].preferences
        self._npm = self.prefs.npm_command
        self.root = self.tmp / "game"

    def tearDown(self):
        self.prefs.npm_command = self._npm
        project.set_project_root_override(None)
        super().tearDown()

    def _scaffold(self) -> dict:
        self.root.mkdir()
        projects.scaffold(self.root, "My Game")
        return json.loads((self.root / "package.json").read_text("utf-8"))

    def test_scaffold_depends_on_the_shipped_packages(self):
        manifest = self._scaffold()
        engine = "file:" + (self.packages / "engine").as_posix()
        self.assertEqual(manifest["dependencies"]["@gamestudio/engine"], engine)
        self.assertEqual(json.loads((self.root / "gamestudio.json").read_text("utf-8"))["engine"], "0.1.0")
        self.assertNotIn("@gamestudio/mcp", manifest["devDependencies"], "this fake build ships no mcp")
        shutil.rmtree(self.root)
        _package(self.packages / "mcp", "@gamestudio/mcp", "0.1.0")
        manifest = self._scaffold()
        self.assertEqual(manifest["devDependencies"]["@gamestudio/mcp"], "file:" + (self.packages / "mcp").as_posix())

    def test_template_asks_for_mcp(self):
        template = json.loads((projects.TEMPLATES / "starter" / "package.json").read_text("utf-8"))
        self.assertEqual(template["devDependencies"]["@gamestudio/mcp"], "{{mcp}}")

    def test_dead_package_paths_are_repaired(self):
        _package(self.root, "game", dependencies={"@gamestudio/engine": "file:Z:/nowhere/engine", "three": "1"},
                 devDependencies={"@gamestudio/devserver": "file:" + (self.packages / "devserver").as_posix()})
        self.assertEqual(projects.repair_package_paths(self.root), ["@gamestudio/engine"])
        manifest = json.loads((self.root / "package.json").read_text("utf-8"))
        engine = "file:" + (self.packages / "engine").as_posix()
        self.assertEqual(manifest["dependencies"]["@gamestudio/engine"], engine)
        self.assertEqual(projects.repair_package_paths(self.root), [])

    def test_setup_links_installs_and_reads_components(self):
        self.prefs.npm_command = str(fake_npm(self.tmp))
        _package(self.root, "game", dependencies={"@gamestudio/engine": "file:x", "three": "0.186.1", "lodash": "4"},
                 scripts={"registry": "gamestudio-registry"})
        self.assertEqual(environment.needs_download(self.root)[-1:], ["packages"])
        done = []
        self.assertEqual(environment.setup_project(self.root, on_done=lambda ok, msg: done.append(ok)), "")
        self.assertTrue(environment.JOB.running())
        self.assertTrue(environment.wait(60), environment.JOB.message)
        self.assertEqual(done, [True])
        calls = (self.root / "npm-calls.txt").read_text("utf-8").splitlines()
        self.assertEqual([c.split()[0:2] for c in calls], [["install", "--no-audit"], ["run", "registry"]])
        self.assertIn("--prefer-offline", calls[0])
        self.assertTrue(toolchain.is_link(self.root / "node_modules" / "three"), "linked, not downloaded")
        self.assertEqual(toolchain.missing_dependencies(self.root), [])
        self.assertTrue((self.root / ".gamestudio" / "components.json").is_file())
        self.assertIsNone(environment.status_item())

    def test_setup_failures_are_reported(self):
        self.prefs.npm_command = str(fake_npm(self.tmp, exit_code=3, output="npm error 404 Not Found - lodash"))
        _package(self.root, "game", dependencies={"lodash": "4"})
        environment.setup_project(self.root)
        self.assertFalse(environment.wait(60))
        self.assertIn("npm error 404 Not Found - lodash", environment.JOB.message)
        self.assertIn("setup.log", environment.JOB.message)
        self.assertEqual(environment.status_item()[1:], ("ERROR", True))

    def test_error_line_of_a_log(self):
        log = _write(self.tmp / "setup.log", "# npm run registry\n\x1b[31m[TSCONFIG_ERROR] \x1b[0mFailed to load "
                                             "tsconfig\nnpm error code 1\n\n")
        self.assertEqual(environment._last_error_line(log), "[TSCONFIG_ERROR] Failed to load tsconfig")
        _write(log, "added 120 packages\nexit\n")
        self.assertEqual(environment._last_error_line(log), "exit")
        self.assertEqual(environment._last_error_line(self.tmp / "missing.log"), "")

    def test_offline_never_downloads(self):
        self.prefs.npm_command = str(fake_npm(self.tmp))
        _package(self.root, "game", dependencies={"lodash": "4"})
        with mock.patch.object(environment, "online", lambda: False):
            environment.setup_project(self.root)
        self.assertFalse(environment.wait(30))
        self.assertIn("online access is off", environment.JOB.message)
        self.assertFalse((self.root / "npm-calls.txt").exists())

    def test_one_job_at_a_time_and_cancel(self):
        self.prefs.npm_command = str(fake_npm(self.tmp))

        def slow(job):
            job.run([sys.executable, "-c", "import time; time.sleep(30)"], self.tmp, self.tmp / "slow.log", "Waiting")

        self.assertEqual(environment.start("First", node=False, extra_steps=[slow]), "")
        self.assertIn("Wait for 'First'", environment.start("Second", node=False))
        deadline = time.monotonic() + 10
        while environment.JOB.message.startswith("First") and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertTrue(environment.JOB.status_text().startswith("Waiting..."), environment.JOB.status_text())
        began = time.monotonic()
        environment.JOB.cancel()
        self.assertFalse(environment.wait(20))
        self.assertLess(time.monotonic() - began, 10)
        self.assertEqual(environment.JOB.message, "Cancelled")

    def test_new_project_scaffolds_then_sets_up(self):
        started, finished = [], []

        def fake_start(title, root=None, **kwargs):
            started.append((title, root, kwargs.get("build_registry")))
            kwargs["on_done"](True, "done")
            return ""

        with mock.patch.object(environment, "start", fake_start), \
                mock.patch.object(projects, "finish_new_project", lambda root: finished.append(root) or root):
            self.assertEqual(projects.create_project(self.root, "My Game"), "")
        self.assertEqual(started, [("Creating My Game", self.root, True)])
        self.assertEqual(finished, [self.root])
        self.assertTrue((self.root / "gamestudio.json").is_file())
        self.assertTrue((self.root / "scenes").is_dir())
        self.assertIn("Project ready", environment.JOB.message)

    def test_play_installs_the_packages_first(self):
        _package(self.root, "game", dependencies={"lodash": "4"})
        _write(self.root / "gamestudio.json", "{}")
        project.set_project_root_override(self.root)
        calls = []

        def run(root, purpose, needs, then):
            calls.append((root, purpose, [n.key for n in needs], then))

        with mock.patch.object(environment, "_run", run), mock.patch.object(environment, "consented", lambda: True):
            environment.invalidate()
            self.assertEqual(bpy.ops.gamestudio.play(), {"FINISHED"})
        self.assertEqual(calls, [(self.root, "play", ["packages"], ops._play_when_ready)])

    def test_download_notes_in_the_new_project_dialog(self):
        texts = []
        needs = [environment.Need("node", "Node.js 24.21.0", 36), environment.Need("runtime", "the game packages", 60)]
        projects._notes.update(time=0.0)
        with mock.patch.object(environment, "check", lambda root=None, purpose="setup": needs):
            projects.draw_download_notes(te.MockLayout(self, texts))
        projects._notes.update(time=0.0)
        self.assertIn("GameStudio sets itself up first (one time):", texts)
        self.assertIn("Node.js 24.21.0 (about 36 MB)", texts)
        self.assertIn("The game packages (about 60 MB)", texts)
        self.assertTrue(any(text.startswith("They go in ") for text in texts), texts)


# --- Packaging -----------------------------------------------------------------------------------


def _load_build_tool():
    spec = importlib.util.spec_from_file_location("gs_build_extension", REPO / "tools" / "build_extension.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PackagingTests(_Temp):
    """tools/build_extension.py: what the zip holds (the zip itself needs `blender --command`)."""

    @classmethod
    def setUpClass(cls):
        cls.tool = _load_build_tool()

    def test_stage(self):
        folder = self.tmp / "gamestudio"
        bundle = self.tool.stage(folder)
        self.assertEqual(bundle["kind"], "extension")
        self.assertIn("engine", bundle["runtime"]["packages"])
        self.assertNotIn("host", bundle["runtime"]["packages"])
        self.assertEqual(bundle["runtime"]["hash"], toolchain.runtime_hash(folder / "runtime" / "packages"))
        for rel in self.tool.REQUIRED:
            self.assertTrue((folder / rel).is_file(), rel)
        for path in folder.rglob("*"):
            rel = path.relative_to(folder)
            self.assertFalse(set(rel.parts[:-1] if path.is_file() else rel.parts) & set(self.tool.FORBIDDEN_PARTS),
                             rel)
            self.assertNotEqual(path.suffix, ".pyc", rel)
        demo = folder / "examples" / "demo-game"
        self.assertFalse((demo / "dist").exists())
        self.assertNotIn("extends", json.loads((demo / "tsconfig.json").read_text("utf-8")))
        staged = folder / "core" / "toolchain.py"
        self.assertTrue(toolchain.is_extension_bundle(staged))
        self.assertEqual(toolchain.bundled_packages(staged), folder / "runtime" / "packages")

    def test_check_zip(self):
        good = self.tmp / "good.zip"
        with zipfile.ZipFile(good, "w") as zf:
            for name in self.tool.REQUIRED:
                zf.writestr(name, "")
        self.assertEqual(self.tool.check_zip(good), [])
        bad = self.tmp / "bad.zip"
        with zipfile.ZipFile(bad, "w") as zf:
            for name in ("core/__pycache__/x.pyc", "runtime/packages/engine/node_modules/three/package.json",
                         "assistant/_vendor/anthropic/__init__.py", "runtime/packages/host/package.json",
                         "runtime/packages/engine/test/a.test.ts"):
                zf.writestr(name, "")
        problems = self.tool.check_zip(bad)
        self.assertEqual(len([p for p in problems if p.startswith("should not")]), 5, problems)
        self.assertTrue(any(p.startswith("missing") for p in problems))

    def test_the_runtime_lock_fits(self):
        """core/runtime.lock.json fits the package.json an installed extension writes (tools/runtime_lock.py)."""
        spec = importlib.util.spec_from_file_location("gs_runtime_lock", REPO / "tools" / "runtime_lock.py")
        tool = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(tool)
        self.assertEqual(tool.check(), [], "stale: run python tools/runtime_lock.py")
        lock = json.loads(tool.LOCK.read_text("utf-8"))
        entries = lock["packages"]
        links = {path: entry["resolved"] for path, entry in entries.items() if entry.get("link")}
        self.assertEqual(links, {f"node_modules/@gamestudio/{p.name}": f"packages/{p.name}"
                                 for p in self.tool.runtime_packages()}, "relative: it fits any user folder")
        for name in ("@rolldown/binding-darwin-arm64", "@rolldown/binding-linux-x64-gnu", "@rolldown/binding-win32-x64-msvc",
                     "@typescript/typescript-linux-x64", "lightningcss-darwin-arm64", "fsevents"):
            self.assertIn(f"node_modules/{name}", entries, "every platform's packages")
        resolved = [e["resolved"] for e in entries.values() if "resolved" in e and not e.get("link")]
        self.assertTrue(resolved and all(r.startswith(tool.REGISTRY) for r in resolved), "from the npm registry only")
        folder = pathlib.Path(tempfile.mkdtemp(dir=self.tmp))
        manifest = tool.write_runtime(folder)
        manifest["dependencies"]["vite"] = "0.0.1"
        self.assertTrue(any(p.startswith("vite is ") for p in tool.problems(lock, manifest, folder / "packages")))


def load_tests(loader, _tests=None, _pattern=None):
    suite = unittest.TestSuite()
    for case in (NodePinTests, NodeInstallTests, NodeChoiceTests, SharedRuntimeTests, ProjectSetupTests,
                 PackagingTests):
        suite.addTests(loader.loadTestsFromTestCase(case))
    return suite
