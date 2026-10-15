# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for the bundled runtime and the installer (editor/tools/gsfork_package.py) and for the
shell's Node/npm/package resolution (blender/gamestudio/core/toolchain.py).

    python editor/tests/tools/test_bundle.py          (system Python 3.12, no pytest)

Everything runs against fakes in a temporary folder: a fake repository with node_modules and
package-lock.json, a fake Node.js zip, a fake install. The checks against this repository's own
files (the .iss script, the project template, the splash) need nothing installed; the one against
the repository's node_modules is skipped when `npm ci` has not been run. ISCC is only used, to
compile a tiny installer, when Inno Setup is installed; nothing is ever installed.
"""

import hashlib
import importlib.util
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
import zlib
from unittest import mock

REPO = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "editor" / "tools"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import gsfork_package as gp  # noqa: E402
from test_package import fake_lock, make_fake_bin  # noqa: E402

TOOLCHAIN_FILE = REPO / gp.TOOLCHAIN_SRC
WINDOWS = sys.platform == "win32"


def load_toolchain():
    spec = importlib.util.spec_from_file_location("toolchain_under_test", TOOLCHAIN_FILE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


tc = load_toolchain()


def _write(path: pathlib.Path, data) -> pathlib.Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data if isinstance(data, bytes) else data.encode("utf-8"))
    return path


def _package(folder: pathlib.Path, name: str, version: str, **fields) -> pathlib.Path:
    _write(folder / "package.json", json.dumps({"name": name, "version": version, **fields}))
    return folder


def _quiet(_msg):
    pass


# --------------------------------------------------------------------------- toolchain.py


class SatisfiesTests(unittest.TestCase):
    def test_ranges(self):
        yes = [("0.186.1", "0.186.1"), ("0.186.1", "=0.186.1"), ("0.186.1", "^0.186.0"), ("8.3.4", "^8.0.0"),
               ("8.3.4", "~8.3.1"), ("8.3.4", "8.x"), ("8.3.4", "8.3"), ("8.3.4", ">=8.0.0"), ("1.0.0", "*"),
               ("1.0.0", ""), ("0.1.0", "file:C:/GameStudio/gamestudio/runtime/packages/engine"),
               ("1.0.0-beta.1", "1.0.0-beta.1"), ("0.0.3", "^0.0.3")]
        no = [("0.186.2", "0.186.1"), ("0.187.0", "^0.186.0"), ("9.0.0", "^8.0.0"), ("8.4.0", "~8.3.1"),
              ("7.9.9", ">=8.0.0"), ("0.0.4", "^0.0.3"), ("1.0.0-beta.1", "^1.0.0"), ("1.2.3", ">1.0.0 <2"),
              ("1.2.3", "1.x || 2.x"), ("garbage", "1.0.0")]
        for version, spec in yes:
            self.assertTrue(tc.satisfies(version, spec), f"{version} should satisfy {spec!r}")
        for version, spec in no:
            self.assertFalse(tc.satisfies(version, spec), f"{version} should not satisfy {spec!r}")


class ToolchainTests(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix="gs_toolchain_"))
        self.env = mock.patch.dict(os.environ, {}, clear=False)
        self.env.start()
        os.environ.pop(tc.ENV_RUNTIME, None)

    def tearDown(self):
        self.env.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def make_install(self) -> tuple[pathlib.Path, pathlib.Path]:
        """A packaged install; returns (where toolchain.py would sit, <install>/gamestudio)."""
        install = self.tmp / "GameStudio"
        bundle = install / "gamestudio"
        _write(bundle / "bundle.json", "{}")
        _write(bundle / "node" / ("node.exe" if WINDOWS else "bin/node"), b"MZ")
        _write(bundle / "node" / ("node_modules" if WINDOWS else "lib/node_modules") / "npm/bin/npm-cli.js", "//")
        start = _write(install / "5.2/extensions/system/gamestudio/core/toolchain.py", "#")
        self.make_shared(bundle)
        return start, bundle

    def make_shared(self, root: pathlib.Path) -> tc.Sdk:
        _package(root / "runtime/packages/engine", "@gamestudio/engine", "0.1.0")
        _package(root / "runtime/packages/devserver", "@gamestudio/devserver", "0.1.0",
                 bin={"gamestudio-registry": "./bin/gamestudio-registry.js"})
        _package(root / "node_modules/vite", "vite", "8.3.4", bin={"vite": "bin/vite.js"})
        _package(root / "node_modules/three", "three", "0.186.1")
        _package(root / "node_modules/typescript", "typescript", "7.0.2", bin={"tsc": "./bin/tsc"})
        _package(root / "node_modules/@types/three", "@types/three", "0.186.0")
        return tc.Sdk(root / "runtime/packages", root / "node_modules")

    def make_project(self, deps=None, dev=None) -> pathlib.Path:
        root = self.tmp / "projects" / "my-game"
        deps = {"@gamestudio/engine": "file:x", "three": "0.186.1"} if deps is None else deps
        dev = {"@gamestudio/devserver": "file:x", "vite": "8.3.4", "typescript": "7.0.2",
               "@types/three": "0.186.0"} if dev is None else dev
        _write(root / "package.json", json.dumps({"name": "my-game", "dependencies": deps, "devDependencies": dev}))
        return root

    def test_packaged_layout_is_found_without_environment(self):
        start, bundle = self.make_install()
        self.assertEqual(tc.bundle_root(start), bundle)
        self.assertEqual(tc.runtime_roots(start), [bundle])
        self.assertEqual(tc.node_dir(start), bundle / "node")
        shared = tc.sdk(start)
        self.assertEqual(shared, tc.Sdk(bundle / "runtime/packages", bundle / "node_modules"))
        argv = tc.npm_argv("npm", start)
        self.assertEqual(pathlib.Path(argv[0]).parent, bundle / "node" if WINDOWS else bundle / "node" / "bin")
        self.assertTrue(argv[1].endswith("npm-cli.js"))
        self.assertEqual(tc.node_executable(start), argv[0])

    def test_development_checkout_has_no_bundle(self):
        self.assertIsNone(tc.bundle_root())
        self.assertEqual(tc.runtime_roots(), [])
        shared = tc.sdk()
        self.assertEqual(shared.packages, REPO / "runtime" / "packages")

    def test_runtime_environment_variable_wins(self):
        start, _bundle = self.make_install()
        other = self.make_shared(self.tmp / "elsewhere")
        os.environ[tc.ENV_RUNTIME] = str(other.packages)
        self.assertEqual(tc.sdk(start), other)
        _write(self.tmp / "bare" / "engine" / "package.json", "{}")
        os.environ[tc.ENV_RUNTIME] = str(self.tmp / "bare")
        self.assertEqual(tc.sdk(start), tc.Sdk(self.tmp / "bare", None))

    def test_custom_npm_command_is_used_as_given(self):
        start, _bundle = self.make_install()
        self.assertIsNone(tc.npm_argv("definitely-not-npm-4f2a", start))
        self.assertEqual(tc.npm_argv(sys.executable, start), [shutil.which(sys.executable)])

    def test_child_env_puts_the_bundled_node_first(self):
        start, bundle = self.make_install()
        base = {"Path": os.pathsep.join([r"C:\other", str(bundle / "node")]), "NPM_CONFIG_FUND": "true"}
        env = tc.child_env(base, start)
        self.assertEqual(env["PATH"].split(os.pathsep), [str(bundle / "node"), r"C:\other"])
        self.assertNotIn("Path", env)
        self.assertEqual(env["npm_config_cache"], str(tc.npm_cache_dir()))
        self.assertEqual(env["npm_config_update_notifier"], "false")
        self.assertEqual(env["NPM_CONFIG_FUND"], "true")  # the user's value, not a duplicate key
        self.assertNotIn("npm_config_fund", env)
        env = tc.child_env({"PATH": "x", "npm_config_cache": "mine"}, start)
        self.assertEqual(env["npm_config_cache"], "mine")
        plain = tc.child_env({"PATH": "x"}, self.tmp / "nowhere" / "file.py")
        self.assertEqual(plain["PATH"], "x")
        self.assertNotIn("npm_config_cache", plain)

    def test_activate_changes_path_only_in_an_install(self):
        start, bundle = self.make_install()
        os.environ["PATH"] = r"C:\somewhere"
        self.assertIsNone(tc.activate(self.tmp / "nowhere" / "file.py"))
        self.assertEqual(os.environ["PATH"], r"C:\somewhere")
        self.assertEqual(tc.activate(start), bundle / "node")
        self.assertEqual(os.environ["PATH"].split(os.pathsep)[0], str(bundle / "node"))
        tc.activate(start)
        self.assertEqual(os.environ["PATH"].split(os.pathsep).count(str(bundle / "node")), 1)

    def test_link_project(self):
        shared = self.make_shared(self.tmp / "sdk")
        root = self.make_project()
        self.assertEqual(len(tc.missing_dependencies(root)), 6)
        result = tc.link_project(root, shared)
        self.assertEqual(result["unavailable"], [])
        self.assertEqual(sorted(result["linked"]), sorted(["@gamestudio/engine", "@gamestudio/devserver", "three",
                                                           "vite", "typescript", "@types/three"]))
        self.assertEqual(tc.missing_dependencies(root), [])
        modules = root / "node_modules"
        for name in result["linked"]:
            self.assertTrue(tc.is_link(modules / name), name)
        self.assertEqual((modules / "three").resolve(), (shared.modules / "three").resolve())
        self.assertEqual((modules / "@gamestudio" / "engine").resolve(), (shared.packages / "engine").resolve())
        self.assertFalse(tc.is_link(modules / "@types"))  # scope folders are real
        self.assertFalse(any(p.name == "electron" for p in modules.iterdir()))
        sh = (modules / ".bin" / "vite").read_text(encoding="utf-8")
        self.assertIn('"$basedir/../vite/bin/vite.js"', sh)
        self.assertIn('"$basedir/../typescript/bin/tsc"', (modules / ".bin" / "tsc").read_text(encoding="utf-8"))
        if WINDOWS:
            cmd = (modules / ".bin" / "gamestudio-registry.cmd").read_bytes()
            self.assertIn(b'"%dp0%\\..\\@gamestudio/devserver\\bin\\gamestudio-registry.js"'.replace(b"/", b"\\"), cmd)
            self.assertNotIn(b"\n", cmd.replace(b"\r\n", b""))
        # Linking again changes nothing; deleting the project never reaches the shared files.
        self.assertEqual(tc.link_project(root, shared), {"linked": [], "unavailable": []})
        shutil.rmtree(root)
        self.assertTrue((shared.modules / "three" / "package.json").is_file())

    def test_link_project_repairs_links_but_never_touches_real_folders(self):
        shared = self.make_shared(self.tmp / "sdk")
        moved = self.make_shared(self.tmp / "old-install")
        root = self.make_project()
        tc.link_project(root, moved)
        shutil.rmtree(self.tmp / "old-install")  # GameStudio moved: every link dangles
        self.assertEqual(len(tc.missing_dependencies(root)), 6)
        dangling = root / "node_modules" / "three"
        os.rmdir(dangling) if WINDOWS else os.unlink(dangling)
        dangling.mkdir()  # a real folder without package.json: a broken npm install, npm's business
        result = tc.link_project(root, shared)
        self.assertEqual(result["unavailable"], ["three"])
        self.assertIn("vite", result["linked"])
        self.assertEqual((root / "node_modules" / "vite").resolve(), (shared.modules / "vite").resolve())
        self.assertTrue((root / "node_modules" / "three").is_dir())
        self.assertFalse(tc.is_link(root / "node_modules" / "three"))

    def test_link_project_respects_manifest_versions(self):
        shared = self.make_shared(self.tmp / "sdk")
        root = self.make_project(deps={"three": "^0.150.0", "left-pad": "1.0.0"}, dev={"vite": "^8.1.0"})
        result = tc.link_project(root, shared)
        self.assertEqual(result, {"linked": ["vite"], "unavailable": ["left-pad", "three"]})
        self.assertEqual(tc.missing_dependencies(root), ["left-pad", "three"])

    def test_resolution_walks_up_like_node(self):
        shared = self.make_shared(self.tmp / "workspace")
        root = self.tmp / "workspace" / "examples" / "demo"
        _write(root / "package.json", json.dumps({"dependencies": {"three": "0.186.1"}}))
        self.assertEqual(tc.missing_dependencies(root), [])
        self.assertEqual(tc.link_project(root, shared), {"linked": [], "unavailable": []})
        self.assertFalse((root / "node_modules").exists())


# ------------------------------------------------------------------- the bundled runtime


def make_fake_repo(root: pathlib.Path, stale_lock: bool = False, template_vite: str = "8.3.4") -> pathlib.Path:
    """runtime packages, a template and an example, node_modules as npm ci leaves it, and its lock."""
    _write(root / gp.TOOLCHAIN_SRC, TOOLCHAIN_FILE.read_bytes())
    _package(root / "runtime/packages/engine", "@gamestudio/engine", "0.1.0",
             dependencies={"three": "0.186.1"}, devDependencies={"@gamestudio/devserver": "0.1.0", "vitest": "1"})
    _package(root / "runtime/packages/devserver", "@gamestudio/devserver", "0.1.0",
             dependencies={"ws": "8.22.0"}, peerDependencies={"vite": "^8.0.0"})
    _package(root / "runtime/packages/host", "@gamestudio/host", "0.1.0", dependencies={"electron": "44.4.4"})
    _package(root / gp.PROJECT_TEMPLATES_SRC / "starter", "{{slug}}", "0.1.0",
             dependencies={"@gamestudio/engine": "{{engine}}", "three": "0.186.1"},
             devDependencies={"@gamestudio/devserver": "{{devserver}}", "vite": template_vite})
    _package(root / "examples/demo-game", "demo-game", "0.1.0", dependencies={"@gamestudio/engine": "0.1.0"},
             devDependencies={"vite": "8.3.4"})
    modules = root / "node_modules"
    packages = {
        "three": ("0.186.1", {}),
        "ws": ("8.22.0", {"peerDependencies": {"bufferutil": "^4"}}),
        "vite": ("8.3.4", {"dependencies": {"rolldown": "1.0.0", "fsevents": "2"},
                           "optionalDependencies": {"fsevents": "2"}, "bin": {"vite": "bin/vite.js"}}),
        "rolldown": ("1.0.0", {"dependencies": {"picomatch": "4"}}),
        "rolldown/node_modules/picomatch": ("4.0.0", {}),  # nested: a version conflict npm kept apart
        "picomatch": ("2.3.1", {}),
        "electron": ("44.4.4", {"dependencies": {"extract-zip": "1"}}),
        "extract-zip": ("1.0.0", {"license": "BSD-2-Clause"}),
        "vitest": ("1.0.0", {}),  # a dev dependency of the engine: never bundled
    }
    lock = {"name": "repo", "lockfileVersion": 3, "packages": {"": {}}}
    for rel, (version, fields) in packages.items():
        name = rel.rsplit("node_modules/", 1)[-1]
        folder = modules / rel
        _package(folder, name, version, license=fields.pop("license", "MIT"),
                 repository={"type": "git", "url": f"git+https://github.com/example/{name}.git"}, **fields)
        _write(folder / "index.js", f"// {name}")
        _write(folder / "dist" / "build.js", "// built")  # walk() would skip dist/; packages need it
        lock["packages"][f"node_modules/{rel}"] = {"version": "0.0.0" if stale_lock and name == "ws" else version}
    _write(modules / "electron" / "path.txt", "electron.exe")
    _write(modules / "electron" / "dist" / "electron.exe", b"MZ electron")
    _write(modules / "electron" / "dist" / "version", "v44.4.4")
    _write(modules / ".package-lock.json", "{}")
    _write(root / "package-lock.json", json.dumps(lock))
    if WINDOWS:  # the workspace links npm makes
        (modules / "@gamestudio").mkdir()
        tc.make_link(root / "runtime/packages/engine", modules / "@gamestudio" / "engine")
    return root


class ClosureTests(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix="gs_bundle_"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_closure_is_the_production_tree(self):
        repo = make_fake_repo(self.tmp / "repo")
        closure = gp.module_closure(repo)
        self.assertEqual([e["rel"] for e in closure], [
            "node_modules/electron", "node_modules/extract-zip", "node_modules/rolldown",
            "node_modules/rolldown/node_modules/picomatch", "node_modules/three", "node_modules/vite",
            "node_modules/ws"])
        by_rel = {e["rel"]: e for e in closure}
        self.assertEqual(by_rel["node_modules/rolldown/node_modules/picomatch"]["version"], "4.0.0")
        self.assertEqual(by_rel["node_modules/extract-zip"]["license"], "BSD-2-Clause")
        self.assertEqual(by_rel["node_modules/vite"]["source"], "https://github.com/example/vite")
        self.assertEqual(gp.electron_binary(closure)[1], "44.4.4")

    def test_stale_install_is_refused(self):
        with self.assertRaises(gp.PackageError) as ctx:
            gp.module_closure(make_fake_repo(self.tmp / "stale", stale_lock=True))
        self.assertIn("node_modules/ws is 8.22.0, package-lock.json says 0.0.0", str(ctx.exception))
        with self.assertRaises(gp.PackageError) as ctx:
            gp.module_closure(make_fake_repo(self.tmp / "template", template_vite="9.0.0"))
        self.assertIn("asks for vite 9.0.0", str(ctx.exception))
        repo = make_fake_repo(self.tmp / "missing")
        shutil.rmtree(repo / "node_modules" / "three")
        with self.assertRaises(gp.PackageError) as ctx:
            gp.module_closure(repo)
        self.assertIn("three (needed by @gamestudio/engine) is not installed", str(ctx.exception))

    def test_electron_binary_must_be_downloaded(self):
        repo = make_fake_repo(self.tmp / "repo")
        (repo / "node_modules" / "electron" / "dist" / "electron.exe").unlink()
        with self.assertRaises(gp.PackageError) as ctx:
            gp.electron_binary(gp.module_closure(repo))
        self.assertIn("install.js", str(ctx.exception))

    @unittest.skipUnless((REPO / "node_modules" / "vite" / "package.json").is_file(), "npm ci not run")
    def test_this_repository_bundles_what_new_project_needs(self):
        closure = gp.module_closure(REPO)
        rels = {e["rel"] for e in closure}
        template = json.loads((REPO / gp.PROJECT_TEMPLATES_SRC / "starter" / "package.json").read_text())
        for field in ("dependencies", "devDependencies"):
            for name, spec in template[field].items():
                if not name.startswith(gp.SCOPE):
                    self.assertIn(f"node_modules/{name}", rels)
        for dev_only in ("vitest", "puppeteer-core"):
            self.assertNotIn(f"node_modules/{dev_only}", rels)
        self.assertEqual([e["rel"] for e in closure if not gp._licenses_ok(e["license"])], [])
        gp.electron_binary(closure)


class NodeFetchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix="gs_node_"))
        self.zip = self.tmp / "node-v1.2.3-win-x64.zip"
        with zipfile.ZipFile(self.zip, "w") as zf:
            for rel in ("node.exe", "LICENSE", "npm.cmd", "node_modules/npm/bin/npm-cli.js",
                        "node_modules/npm/node_modules/semver/package.json"):
                zf.writestr(f"node-v1.2.3-win-x64/{rel}", rel)
        self.spec = {"version": "1.2.3", "url": "https://example.invalid/node-v1.2.3-win-x64.zip",
                     "sha256": hashlib.sha256(self.zip.read_bytes()).hexdigest()}

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_extracts_once_and_checks_the_pin(self):
        downloads = self.tmp / "downloads"
        node = gp.fetch_node(self.spec, downloads, self.zip, log=_quiet)
        self.assertEqual(node, downloads / "node-v1.2.3-win-x64")
        self.assertTrue((node / "node_modules/npm/node_modules/semver/package.json").is_file())
        (node / "marker").write_text("kept")
        self.assertEqual(gp.fetch_node(self.spec, downloads, self.zip, log=_quiet), node)
        self.assertTrue((node / "marker").is_file(), "an extraction with the same hash is reused")
        files = {rel for _p, rel in gp.walk_tree(node)}
        self.assertIn("node_modules/npm/node_modules/semver/package.json", files)
        with self.assertRaises(gp.PackageError) as ctx:
            gp.fetch_node(dict(self.spec, sha256="0" * 64), downloads, self.zip, log=_quiet)
        self.assertIn("pins", str(ctx.exception))

    def test_the_pinned_node_is_an_lts_zip(self):
        spec = gp.node_spec(REPO)
        self.assertRegex(spec["version"], r"^\d+\.\d+\.\d+$")
        self.assertEqual(int(spec["version"].split(".")[0]) % 2, 0, "Node.js LTS lines are even")
        self.assertTrue(spec["url"].endswith(f"/v{spec['version']}/node-v{spec['version']}-win-x64.zip"))
        self.assertRegex(spec["sha256"], r"^[0-9a-f]{64}$")


class BundlePlanTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = pathlib.Path(tempfile.mkdtemp(prefix="gs_bundle_plan_"))
        cls.build = cls.tmp / "build"
        make_fake_bin(cls.build)
        repo = make_fake_repo(cls.tmp / "modules-repo")
        node = cls.tmp / "node"
        for rel in ("node.exe", "LICENSE", "node_modules/npm/bin/npm-cli.js", ".gsfork-sha256"):
            _write(node / rel, rel)
        cls.bundle = {"node_dir": node, "node": {"version": "24.21.0", "url": "https://nodejs.org/x.zip"},
                      "closure": gp.module_closure(repo), "electron": "44.4.4", "unreviewed": []}
        cls.result = gp.zip_plan({"build": cls.build, "repo": REPO}, fake_lock("abc"), bundle=cls.bundle)
        cls.plan = cls.result["plan"]
        cls.top = gp.names(fake_lock("abc"))["top"]

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def rel(self, arcname):
        return arcname[len(self.top) + 1:]

    def test_bundle_files(self):
        rels = {self.rel(a) for a in self.plan}
        for rel in ("gamestudio/node/node.exe", "gamestudio/node/LICENSE",
                    "gamestudio/node/node_modules/npm/bin/npm-cli.js",
                    "gamestudio/node_modules/vite/dist/build.js", "gamestudio/node_modules/electron/dist/electron.exe",
                    "gamestudio/node_modules/rolldown/node_modules/picomatch/index.js",
                    "gamestudio/bundle.json", "gamestudio/THIRD-PARTY-NOTICES.md", "GameStudio.ico",
                    "gamestudio/runtime/packages/engine/package.json", "blender-launcher.exe"):
            self.assertIn(rel, rels)
        for rel in rels:
            self.assertNotIn(".gsfork-sha256", rel)
            self.assertNotIn("vitest", rel)
            self.assertFalse(rel.startswith("gamestudio/node_modules/@gamestudio"), rel)
            self.assertFalse(rel.startswith("gamestudio/node_modules/.package-lock"), rel)

    def test_manifest_notices_and_readme(self):
        manifest = json.loads(self.plan[f"{self.top}/gamestudio/bundle.json"])
        self.assertEqual((manifest["node"], manifest["electron"], manifest["module_count"]), ("24.21.0", "44.4.4", 7))
        notices = self.plan[f"{self.top}/gamestudio/THIRD-PARTY-NOTICES.md"].decode("utf-8")
        self.assertIn("Node.js 24.21.0", notices)
        self.assertIn("Artistic License 2.0", notices)
        self.assertIn("| `extract-zip` | 1.0.0 | BSD-2-Clause |", notices)
        notice = self.plan[f"{self.top}/NOTICE.md"].decode("utf-8")
        self.assertIn("GameStudio Editor is based on Blender's open source code", notice)
        self.assertIn("**Node.js 24.21.0**", notice)
        self.assertIn("**Electron 44.4.4**", notice)
        self.assertIn("Start-menu shortcut", notice)
        readme = self.plan[f"{self.top}/gamestudio/README.txt"].decode("utf-8")
        self.assertIn("needs no network", readme.replace("\r\n", " "))

    def test_every_tsconfig_base_ships(self):
        """Vite reads tsconfig.json (and what it extends) to compile the engine and the examples."""
        configs = [a for a in self.plan if a.endswith("/tsconfig.json") and "/gamestudio/" in a]
        self.assertTrue(any("/runtime/packages/engine/" in a for a in configs))
        for arcname in configs:
            item = self.plan[arcname]
            data = json.loads(item if isinstance(item, bytes) else item.read_bytes())
            if "extends" in data:
                target = os.path.normpath(os.path.join(os.path.dirname(arcname), data["extends"])).replace("\\", "/")
                self.assertIn(target, self.plan, f"{arcname} extends a file the package lacks")

    def test_unreviewed_licences_block_the_release(self):
        bundle = dict(self.bundle, unreviewed=["odd@1.0.0 (SSPL-1.0)"])
        result = gp.zip_plan({"build": self.build, "repo": REPO}, fake_lock("abc"), bundle=bundle)
        self.assertTrue(any("SSPL-1.0" in w for w in result["warnings"]))
        self.assertTrue(gp._licenses_ok("(MIT OR Apache-2.0)"))
        self.assertFalse(gp._licenses_ok("GPL-3.0-only"))
        self.assertFalse(gp._licenses_ok("UNKNOWN"))

    def test_bundle_needs_the_runtime(self):
        with self.assertRaises(gp.PackageError):
            gp.zip_plan({"build": self.build, "repo": REPO}, fake_lock("abc"), include_runtime=False,
                        bundle=self.bundle)


# ------------------------------------------------------------------------------ installer


def _png_filter(kind: int, line: bytes, previous: bytes, bpp: int) -> bytes:
    out = bytearray(len(line))
    for i, value in enumerate(line):
        left = line[i - bpp] if i >= bpp else 0
        up, corner = previous[i], previous[i - bpp] if i >= bpp else 0
        if kind == 0:
            predicted = 0
        elif kind == 1:
            predicted = left
        elif kind == 2:
            predicted = up
        elif kind == 3:
            predicted = (left + up) >> 1
        else:
            p = left + up - corner
            pa, pb, pc = abs(p - left), abs(p - up), abs(p - corner)
            predicted = left if pa <= pb and pa <= pc else up if pb <= pc else corner
        out[i] = (value - predicted) & 0xFF
    return bytes(out)


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix="gs_installer_"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_png_decoder_handles_every_filter(self):
        width, height, bpp = 7, 5, 4
        rows = [bytes((x * 37 + y * 11 + c * 5) % 256 for x in range(width) for c in range(bpp)) for y in range(height)]
        raw, previous = b"", bytes(width * bpp)
        for y, row in enumerate(rows):
            kind = y % 5
            raw += bytes([kind]) + _png_filter(kind, row, previous, bpp)
            previous = row
        header = width.to_bytes(4, "big") + height.to_bytes(4, "big") + bytes([8, 6, 0, 0, 0])

        def chunk(kind, body):
            return len(body).to_bytes(4, "big") + kind + body + zlib.crc32(kind + body).to_bytes(4, "big")

        png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")
        self.assertEqual(gp.png_decode(png), (width, height, bpp, rows))
        self.assertEqual(gp.png_decode(gp.png_encode(width, height, bpp, rows)), (width, height, bpp, rows))

    def test_wizard_image_is_cropped_from_the_splash(self):
        splash = (REPO / gp.BRANDING / "splash.png").read_bytes()
        width, height, bpp, rows = gp.png_decode(gp.wizard_image(splash))
        self.assertEqual((width, height), (245, 470))
        self.assertAlmostEqual(width / height, 164 / 314, places=2)  # Inno Setup's wizard image aspect
        _w, _h, _b, full = gp.png_decode(splash)
        self.assertEqual(rows[200], full[200][72 * bpp:317 * bpp])

    def test_identity_values(self):
        self.assertEqual(gp.app_user_model_id(REPO, "5.2"), "gamestudio.editor.5.2")
        self.assertEqual(gp.numeric_version("0.1.0"), "0.1.0.0")
        self.assertEqual(gp.numeric_version("1.2.3-rc.4"), "1.2.3.4")
        n = gp.names(fake_lock("abc"))
        self.assertEqual(n["installer"], "GameStudio-Editor-9.9.9-blender5.2.2-windows-x64-setup.exe")

    def test_script_and_defines_agree(self):
        script = (REPO / gp.ISS_SCRIPT).read_text(encoding="utf-8")
        used = set(re.findall(r"\{#(\w+)\}", script)) | set(re.findall(r"#ifndef (\w+)", script))
        defines = gp.installer_defines(gp.names(fake_lock("abc")), REPO, self.tmp, self.tmp, self.tmp)
        self.assertEqual(used - set(defines), set())

    def test_script_policy(self):
        """Per-user, no admin, no file types, never deletes {app} wholesale or user data."""
        script = (REPO / gp.ISS_SCRIPT).read_text(encoding="utf-8")
        setup = dict(re.findall(r"^(\w+)=(.*)$", script, re.M))
        self.assertEqual(setup["PrivilegesRequired"], "lowest")
        self.assertNotIn("PrivilegesRequiredOverridesAllowed", setup)
        self.assertEqual(setup["DefaultDirName"], r"{autopf}\GameStudio")
        self.assertEqual(setup["ChangesAssociations"], "no")
        self.assertEqual("{" + setup["AppId"][2:] + "_is1", gp.INSTALLER_APP_ID)
        self.assertNotIn("[Registry]", script)
        self.assertNotIn(".blend", script.lower())
        self.assertNotIn("blender foundation", script.lower())
        for line in re.findall(r"^Type: filesandordirs; Name: \"([^\"]+)\"", script, re.M):
            self.assertNotIn(line, (r"{app}", r"{app}\*", r"{userappdata}\GameStudio", r"{userdocs}"))
            self.assertFalse(line.startswith(r"{userdocs}"), line)
        icons = re.findall(r"^Name: \"\{auto(?:programs|desktop)\}\\GameStudio\".*$", script, re.M)
        self.assertEqual(len(icons), 2)
        for icon in icons:
            self.assertIn(r'Filename: "{app}\blender-launcher.exe"', icon)
            self.assertIn('AppUserModelID: "{#AppUserModelID}"', icon)
        self.assertRegex(script, r'Name: "desktopicon";.*Flags: unchecked')
        self.assertIn("/REMOVEPROFILE", script)
        self.assertIn("MB_DEFBUTTON2", script)  # keeping the settings is the default answer

    def test_stage_tree(self):
        source = _write(self.tmp / "src" / "big.bin", b"x" * 100)
        stage = self.tmp / "stage"
        _write(stage / "stale.txt", "from an older run")
        count = gp.stage_tree({"Top/a/big.bin": source, "Top/NOTICE.md": b"notice"}, "Top", stage)
        self.assertEqual(count, 2)
        self.assertEqual((stage / "a" / "big.bin").read_bytes(), b"x" * 100)
        self.assertEqual((stage / "NOTICE.md").read_bytes(), b"notice")
        self.assertFalse((stage / "stale.txt").exists())

    def test_offline_environment(self):
        tools = self.tmp / "nodejs"
        _write(tools / "node.exe", b"MZ")
        git = _write(self.tmp / "git" / "cmd" / "git.exe", b"MZ").parent
        keep = self.tmp / "keep"
        keep.mkdir()
        value = os.pathsep.join([str(tools), str(keep), str(git)])
        self.assertEqual(gp.strip_tools_from_path(value), str(keep))
        with mock.patch.dict(os.environ, {"PATH": value, "BLENDER_SYSTEM_SCRIPTS": "x", "GAMESTUDIO_RUNTIME": "y"}):
            env = gp.offline_env({"BLENDER_USER_RESOURCES": "scratch"})
        self.assertEqual(env["PATH"], str(keep))
        self.assertEqual(env["npm_config_offline"], "true")
        self.assertNotIn("BLENDER_SYSTEM_SCRIPTS", env)
        self.assertNotIn("GAMESTUDIO_RUNTIME", env)
        self.assertEqual(env["BLENDER_USER_RESOURCES"], "scratch")

    @unittest.skipUnless(WINDOWS and gp.find_iscc(), "Inno Setup 6 is not installed")
    def test_tiny_installer_compiles(self):
        """The real .iss with ISCC over a three-file tree. Compiled only, never run."""
        out = self.tmp / "out"
        out.mkdir()
        n = gp.names(fake_lock("abc"))
        plan = {f"{n['top']}/blender-launcher.exe": b"MZ", f"{n['top']}/NOTICE.md": b"GameStudio notice\n",
                f"{n['top']}/gamestudio/bundle.json": b"{}"}
        installer = gp.build_installer({"names": n, "repo": REPO, "plan": plan, "bundle": {"node": {}}}, out,
                                       log=_quiet)
        self.assertEqual(installer.name, n["installer"])
        self.assertGreater(installer.stat().st_size, 100_000)
        self.assertFalse((out / "installer-work").exists())
        with self.assertRaises(gp.PackageError):
            gp.build_installer({"names": n, "repo": REPO, "plan": plan, "bundle": None}, out, log=_quiet)


if __name__ == "__main__":
    unittest.main(verbosity=2)
