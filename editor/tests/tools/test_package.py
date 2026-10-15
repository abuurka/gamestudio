# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for editor/tools/gsfork_package.py against a fake Blender install and a fake upstream mirror.

    python editor/tests/tools/test_package.py          (system Python 3.12, no pytest, needs git)

The fake install has Blender's layout plus the build-tool clutter Ninja leaves in bin/; the fake
mirror is a one-commit git repository with a Git LFS pointer whose object sits in .git/lfs/objects,
a test-data pointer without an object, and core.autocrlf=true to prove the archive keeps LF endings.
The shell, runtime and examples come from this repository, so the test also checks the real
exclusions (__pycache__, assistant/_vendor, node_modules, .gamestudio, dist).
"""

import hashlib
import io
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
import zipfile

REPO = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "editor" / "tools"))

import gsfork_package as gp  # noqa: E402

HAVE_GIT = shutil.which("git") is not None
VERSION = "9.9.9"
TOP = f"GameStudio-Editor-{VERSION}"
SPLASH = b"\x89PNG\r\n\x1a\n" + bytes(range(256)) * 8  # binary, with CR LF bytes inside
CREATOR = b"int main()\n{\n  return 0;\n}\n"


def _write(path: pathlib.Path, data) -> pathlib.Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data if isinstance(data, bytes) else data.encode("utf-8"))
    return path


def _git(*args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def _pointer(data: bytes) -> tuple[bytes, str]:
    oid = hashlib.sha256(data).hexdigest()
    text = f"version https://git-lfs.github.com/spec/v1\noid sha256:{oid}\nsize {len(data)}\n"
    return text.encode("ascii"), oid


def make_fake_bin(build: pathlib.Path, with_manifest: bool = True, extra_program: bool = False) -> pathlib.Path:
    """Blender's Windows layout as a Ninja build leaves it: the install prefix is the output folder."""
    bin_dir = build / "bin"
    # Linked straight into bin/ by the build; no install() rule lists them (as in the real build).
    in_place = {
        "blender.exe": b"MZ fake blender",
        "blender-launcher.exe": b"MZ fake launcher",
        "blender_cpu_check.dll": b"MZ cpu check",
    }
    installed = {
        "blender.pdb": b"public symbols",  # installed by upstream (WITH_WINDOWS_RELEASE_PDB)
        "python313.dll": b"MZ python",
        "copyright.txt": "Blender copyright",
        "readme.html": "<html>Blender</html>",
        "license/license.md": "# Blender Third-Party Licenses",
        "license/spdx/GPL-3.0-or-later.txt": "GNU GENERAL PUBLIC LICENSE\nVersion 3, 29 June 2007\n",
        "5.2/datafiles/fonts/font.ttf": b"font",
        "5.2/scripts/startup/bl_app_templates_system/2D_Animation/__init__.py": "# upstream template",
        "5.2/extensions/system/readme.txt": "System extensions",
        "5.2/python/lib/os.py": "# stdlib",
    }
    clutter = {
        "makesdna.exe": b"MZ tool", "makesdna.pdb": b"pdb", "makesrna.exe": b"MZ tool", "datatoc.exe": b"MZ",
        "msgfmt.exe": b"MZ", "shader_tool.exe": b"MZ", "zstd_compress.exe": b"MZ", "blender.lib": b"lib",
        "blender.exp": b"exp", "5.2/python/lib/__pycache__/os.cpython-313.pyc": b"pyc from a test run",
        "portable/config/userpref.blend": b"someone's prefs",
        # A stale copy of the shell in the install must never win over the source tree.
        "5.2/scripts/startup/bl_app_templates_system/GameStudio/stale.py": "# stale",
    }
    if extra_program:
        clutter["mystery.exe"] = b"MZ unknown"
    for rel, data in {**in_place, **installed, **clutter}.items():
        _write(bin_dir / rel, data)
    if with_manifest:
        listed = [str(bin_dir / rel).replace("\\", "/") for rel in installed]
        listed.append(str(bin_dir / "portable/config/userpref.blend").replace("\\", "/"))  # filtered anyway
        listed.append(str(build / "elsewhere" / "not-in-bin.dll").replace("\\", "/"))      # outside bin
        listed.append(str(bin_dir / "deleted-since.dll").replace("\\", "/"))               # no longer exists
        (build / "install_manifest.txt").write_text("\n".join(listed) + "\n", encoding="utf-8")
    return bin_dir


def make_fake_mirror(root: pathlib.Path, missing_outside_tests: bool = False) -> str:
    root.mkdir(parents=True)
    _git("init", "-q", cwd=root)
    splash_pointer, splash_oid = _pointer(SPLASH)
    _write(root / ".git" / "lfs" / "objects" / splash_oid[:2] / splash_oid[2:4] / splash_oid, SPLASH)
    test_pointer, _ = _pointer(b"test fixture that the mirror never fetched")
    files = {
        "CMakeLists.txt": "project(Blender)\n",
        "source/creator/creator.cc": CREATOR,
        "release/datafiles/splash.png": splash_pointer,
        "release/license/spdx/GPL-3.0-or-later.txt": "GPL text\n",
        "tests/files/render/big.blend": b"test data that must not ship",
        "tests/python/fixture.png": test_pointer,
    }
    if missing_outside_tests:
        files["release/datafiles/icon.png"] = _pointer(b"icon bytes that are not in the store")[0]
    for rel, data in files.items():
        _write(root / rel, data)
    _git("-c", "core.autocrlf=false", "add", "-A", cwd=root)
    _git("-c", "user.name=test", "-c", "user.email=test@example.invalid", "-c", "commit.gpgsign=false",
         "commit", "-q", "-m", "upstream", cwd=root)
    _git("tag", "v5.2.2", cwd=root)
    _git("config", "core.autocrlf", "true", cwd=root)  # Git for Windows' default; must not leak into archives
    return _git("rev-parse", "HEAD", cwd=root)


def fake_lock(commit: str) -> dict:
    return {
        "gamestudio_version": VERSION,
        "blender": {"remote": "https://projects.blender.org/blender/blender.git", "tag": "v5.2.2",
                    "commit": commit},
        "libs": {"windows_x64": {"remote": "https://projects.blender.org/blender/lib-windows_x64.git",
                                 "commit": "60d6e96b917568278d400a4024c98da0fb777338"}},
    }


def _quiet(_msg):
    pass


@unittest.skipUnless(HAVE_GIT, "git is required")
class PackageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = pathlib.Path(tempfile.mkdtemp(prefix="gs_pkg_test_"))
        cls.build = cls.tmp / "build"
        make_fake_bin(cls.build)
        cls.commit = make_fake_mirror(cls.tmp / "mirror")
        cls.lock = fake_lock(cls.commit)
        cls.paths = {"build": cls.build, "mirror": cls.tmp / "mirror", "work": cls.tmp / "work", "repo": REPO}
        cls.out = cls.tmp / "dist"
        cls.result = gp.package(cls.paths, cls.lock, cls.out, log=_quiet)
        with zipfile.ZipFile(cls.result["zip"]) as zf:
            cls.zip_names = zf.namelist()
            cls.zip_read = {name: zf.read(name) for name in cls.zip_names
                            if name.endswith(("GameStudio.cmd", "NOTICE.md", "package.json", "MIT.txt"))}
        cls.source = cls._tar(cls.result["source"])
        cls.upstream = cls._tar(cls.result["upstream_source"])
        with tarfile.open(cls.result["upstream_source"]) as tf:
            cls.upstream_pax = dict(tf.pax_headers)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    @staticmethod
    def _tar(path) -> dict:
        with tarfile.open(path, "r:gz") as tf:
            return {m.name: tf.extractfile(m).read() for m in tf.getmembers() if m.isfile()}

    # -- outputs ---------------------------------------------------------------------------

    def test_output_names(self):
        base = f"GameStudio-Editor-{VERSION}-blender5.2.2"
        self.assertEqual(self.result["zip"].name, f"{base}-windows-x64.zip")
        self.assertEqual(self.result["source"].name, f"{base}-source.tar.gz")
        self.assertEqual(self.result["upstream_source"].name, f"{base}-upstream-source.tar.gz")
        self.assertEqual(sorted(p.name for p in self.out.iterdir()),
                         sorted([f"{base}-windows-x64.zip", f"{base}-source.tar.gz",
                                 f"{base}-upstream-source.tar.gz", "SHA256SUMS.txt"]))
        self.assertTrue(self.result["releasable"], self.result["warnings"])

    def test_sha256sums(self):
        lines = (self.out / "SHA256SUMS.txt").read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 3)
        for line in lines:
            digest, name = line.split("  ", 1)
            self.assertEqual(digest, hashlib.sha256((self.out / name).read_bytes()).hexdigest(), name)

    # -- the editor zip --------------------------------------------------------------------

    def test_zip_has_one_top_folder(self):
        self.assertEqual({n.split("/", 1)[0] for n in self.zip_names}, {TOP})

    def test_zip_keeps_the_install_and_adds_the_shell(self):
        expected = [
            "blender.exe", "blender-launcher.exe", "blender_cpu_check.dll", "blender.pdb", "python313.dll",
            "copyright.txt",
            "license/license.md", "license/spdx/GPL-3.0-or-later.txt", "5.2/python/lib/os.py",
            "5.2/scripts/startup/bl_app_templates_system/2D_Animation/__init__.py",
            "5.2/extensions/system/readme.txt",
            "GameStudio.cmd", "NOTICE.md", "license/GameStudio-runtime-MIT.txt",
            "5.2/scripts/startup/bl_app_templates_system/GameStudio/__init__.py",
            "5.2/scripts/startup/bl_app_templates_system/GameStudio/startup.blend",
            "5.2/scripts/startup/bl_app_templates_system/GameStudio/userpref.blend",
            "5.2/extensions/system/gamestudio/__init__.py",
            "5.2/extensions/system/gamestudio/blender_manifest.toml",
            "5.2/extensions/system/gamestudio/projects.py",
            "5.2/extensions/system/gamestudio/assistant/deps.py",
            "5.2/extensions/system/gamestudio/project_templates/starter/package.json",
            "gamestudio/package.json", "gamestudio/README.txt",
            "gamestudio/runtime/packages/engine/package.json",
            "gamestudio/runtime/packages/engine/LICENSE",
            "gamestudio/runtime/packages/devserver/package.json",
            "gamestudio/runtime/packages/host/package.json",
            "gamestudio/examples/demo-game/gamestudio.json",
            "gamestudio/examples/demo-game/scenes/main.blend",
        ]
        names = set(self.zip_names)
        for rel in expected:
            self.assertIn(f"{TOP}/{rel}", names, rel)

    def test_zip_leaves_out_build_clutter_and_caches(self):
        forbidden = ("makesdna", "makesrna", "datatoc", "msgfmt", "shader_tool", "zstd_compress", "blender.lib",
                     "blender.exp", "/portable/", "stale.py", "__pycache__", ".pyc", "assistant/_vendor",
                     "node_modules", "/.gamestudio/", "demo-game/dist/", "deleted-since", "not-in-bin")
        for name in self.zip_names:
            for bad in forbidden:
                self.assertNotIn(bad, name)

    def test_launcher(self):
        cmd = self.zip_read[f"{TOP}/GameStudio.cmd"]
        self.assertNotIn(b"\n", cmd.replace(b"\r\n", b""), "the .cmd file must use CRLF line endings")
        text = cmd.decode("ascii")
        self.assertIn('"%GS_HOME%blender-launcher.exe"', text)
        self.assertIn('"%GS_HOME%blender.exe"', text)
        self.assertIn("GAMESTUDIO_RUNTIME=%GS_HOME%gamestudio\\runtime\\packages", text)

    def test_notice(self):
        notice = self.zip_read[f"{TOP}/NOTICE.md"].decode("utf-8")
        self.assertIn("GameStudio Editor is based on Blender's open source code", notice)
        self.assertIn("not made, endorsed or supported by the Blender", notice)
        self.assertIn("GNU General Public License", notice)
        self.assertIn("MIT License", notice)
        self.assertIn("Permission is hereby granted, free of charge", notice)
        self.assertIn(self.result["source"].name, notice)
        self.assertIn(self.result["upstream_source"].name, notice)
        self.assertIn(self.commit, notice)
        self.assertIn("`copyright.txt`", notice)
        self.assertIn("gs-v9.9.9", notice)

    def test_runtime_workspace_package_json(self):
        data = json.loads(self.zip_read[f"{TOP}/gamestudio/package.json"])
        self.assertEqual(data["workspaces"], ["runtime/packages/*", "examples/*"])
        self.assertTrue(data["private"])

    # -- our source archive ---------------------------------------------------------------

    def test_source_archive_contents(self):
        prefix = f"GameStudio-Editor-{VERSION}-blender5.2.2-source/"
        self.assertTrue(all(name.startswith(prefix) for name in self.source))
        rels = {name[len(prefix):] for name in self.source}
        series = (REPO / "editor" / "patches" / "series").read_text(encoding="utf-8")
        patches = [line.strip() for line in series.splitlines() if line.strip() and not line.startswith("#")]
        expected = ["UPSTREAM.txt", "HOW_TO_BUILD.md", "COPYING",
                    "gamestudio/editor/upstream.lock.json", "gamestudio/editor/patches/series",
                    "gamestudio/editor/native/CMakeLists.txt", "gamestudio/editor/build/gamestudio.cmake",
                    "gamestudio/editor/tools/gsfork.py", "gamestudio/editor/tools/gsfork_package.py",
                    "gamestudio/editor/branding/overlay/release/windows/icons/winblender.rc",
                    "gamestudio/blender/gamestudio/__init__.py",
                    "gamestudio/editor/shell/scripts/startup/bl_app_templates_system/GameStudio/__init__.py",
                    "gamestudio/editor/shell/tools/build_template.py",
                    "gamestudio/package.json", "gamestudio/package-lock.json", "gamestudio/tsconfig.base.json",
                    "gamestudio/runtime/packages/engine/package.json", "gamestudio/runtime/vitest.config.ts",
                    "gamestudio/tools/run_blender.mjs", "gamestudio/examples/demo-game/package.json"]
        expected += [f"gamestudio/editor/patches/{p}" for p in patches]
        for rel in expected:
            self.assertIn(rel, rels, rel)
        for rel in rels:
            for bad in ("gsfork.local.json", "node_modules/", "__pycache__", ".pyc", "assistant/_vendor/",
                        "/dist/", "/.gamestudio/"):
                self.assertNotIn(bad, rel)
        self.assertEqual(self.source[prefix + "COPYING"], b"GNU GENERAL PUBLIC LICENSE\nVersion 3, 29 June 2007\n")

    def test_upstream_txt(self):
        text = self.source[f"GameStudio-Editor-{VERSION}-blender5.2.2-source/UPSTREAM.txt"].decode("utf-8")
        self.assertIn("projects.blender.org/blender/blender.git", text)
        self.assertIn("v5.2.2", text)
        self.assertIn(self.commit, text)
        self.assertIn(self.result["upstream_source"].name, text)
        self.assertIn(self.result["sha256"][self.result["upstream_source"].name], text)
        self.assertIn("60d6e96b917568278d400a4024c98da0fb777338", text)
        how = self.source[f"GameStudio-Editor-{VERSION}-blender5.2.2-source/HOW_TO_BUILD.md"].decode("utf-8")
        self.assertIn("gsfork.py sync", how)
        self.assertIn("-DGS_NATIVE_DIR=", how)
        self.assertIn("git -c core.longpaths=true apply", how)

    # -- the upstream source archive ------------------------------------------------------

    def test_upstream_archive_is_the_commit_with_lfs_resolved(self):
        self.assertEqual(self.upstream_pax.get("comment"), self.commit)
        self.assertTrue(all(name.startswith("blender-5.2.2/") for name in self.upstream))
        self.assertEqual(self.upstream["blender-5.2.2/release/datafiles/splash.png"], SPLASH)
        self.assertEqual(self.upstream["blender-5.2.2/source/creator/creator.cc"], CREATOR,
                         "LF endings must survive core.autocrlf=true")
        self.assertNotIn("blender-5.2.2/tests/files/render/big.blend", self.upstream)
        self.assertTrue(self.upstream["blender-5.2.2/tests/python/fixture.png"].startswith(gp.LFS_POINTER))
        self.assertEqual(self.result["upstream"]["lfs_resolved"], 1)
        self.assertEqual(self.result["upstream"]["lfs_missing"], ["tests/python/fixture.png"])

    def test_missing_lfs_outside_tests_is_fatal(self):
        root = self.tmp / "mirror-incomplete"
        commit = make_fake_mirror(root, missing_outside_tests=True)
        out = self.tmp / "incomplete.tar.gz"
        with self.assertRaises(gp.PackageError) as ctx:
            gp.write_upstream_archive(root, fake_lock(commit), out)
        self.assertIn("release/datafiles/icon.png", str(ctx.exception))
        self.assertFalse(out.exists())
        self.assertFalse(out.with_name(out.name + ".partial").exists())

    def test_unknown_commit_is_reported(self):
        with self.assertRaises(gp.PackageError):
            gp.write_upstream_archive(self.tmp / "mirror", fake_lock("0" * 40), self.tmp / "nope.tar.gz")


class UnitTests(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix="gs_pkg_unit_"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_names(self):
        n = gp.names(fake_lock("abc"))
        self.assertEqual(n["zip"], "GameStudio-Editor-9.9.9-blender5.2.2-windows-x64.zip")
        self.assertEqual(n["top"], "GameStudio-Editor-9.9.9")
        self.assertEqual(n["series"], "5.2")

    def test_bin_files_without_manifest_drops_build_tools(self):
        bin_dir = make_fake_bin(self.tmp / "build", with_manifest=False)
        files, how, unknown = gp.bin_files(bin_dir, self.tmp / "build")
        rels = {rel for _p, rel in files}
        self.assertIn("directory walk", how)
        self.assertEqual(unknown, [])
        for good in ("blender.exe", "blender-launcher.exe", "blender_cpu_check.dll", "license/license.md"):
            self.assertIn(good, rels)
        for bad in ("makesdna.exe", "makesdna.pdb", "blender.pdb", "shader_tool.exe", "zstd_compress.exe",
                    "blender.lib", "portable/config/userpref.blend", "5.2/python/lib/__pycache__/os.cpython-313.pyc"):
            self.assertNotIn(bad, rels)

    def test_unknown_program_in_bin_is_flagged(self):
        build = self.tmp / "build"
        bin_dir = make_fake_bin(build, extra_program=True)
        files, _how, unknown = gp.bin_files(bin_dir, build)
        self.assertEqual(unknown, ["mystery.exe"])
        self.assertNotIn("mystery.exe", {rel for _p, rel in files})
        plan = gp.zip_plan({"build": build, "repo": REPO}, fake_lock("abc"), include_runtime=False)
        self.assertTrue(any("mystery.exe" in w for w in plan["warnings"]))

    def test_inputs_changed_after_the_build_are_flagged(self):
        repo = self.tmp / "repo"
        exe = _write(self.tmp / "bin" / "blender.exe", b"MZ")
        patch = _write(repo / "editor" / "patches" / "S01-x.patch", "diff")
        shell = _write(repo / "editor" / "shell" / "x.py", "# copied at package time, never stale")
        lock = _write(repo / "editor" / "upstream.lock.json", "{}")
        built = exe.stat().st_mtime
        for path in (patch, shell, lock):
            os.utime(path, (built - 100, built - 100))
        self.assertEqual(gp.changed_since_build(repo, exe), [])
        for path in (patch, shell):
            os.utime(path, (built + 100, built + 100))
        self.assertEqual(gp.changed_since_build(repo, exe), ["editor/patches/S01-x.patch"])

    def test_missing_build_is_an_error(self):
        (self.tmp / "build" / "bin").mkdir(parents=True)
        with self.assertRaises(gp.PackageError):
            gp.zip_plan({"build": self.tmp / "build", "repo": REPO}, fake_lock("abc"))

    def test_clean_env_drops_development_variables(self):
        saved = dict(os.environ)
        try:
            os.environ.update({"BLENDER_SYSTEM_SCRIPTS": "x", "BLENDER_SYSTEM_EXTENSIONS": "x",
                               "BLENDER_USER_RESOURCES": "x", "GAMESTUDIO_RUNTIME": "x", "PYTHONPATH": "x"})
            env = gp.clean_env({"BLENDER_USER_RESOURCES": "scratch"})
        finally:
            os.environ.clear()
            os.environ.update(saved)
        for key in ("BLENDER_SYSTEM_SCRIPTS", "BLENDER_SYSTEM_EXTENSIONS", "GAMESTUDIO_RUNTIME", "PYTHONPATH"):
            self.assertNotIn(key, env)
        self.assertEqual(env["BLENDER_USER_RESOURCES"], "scratch")
        self.assertIn("PATH", {k.upper() for k in env})

    def test_lfs_pointer_parsing(self):
        self.assertEqual(gp._parse_lfs_pointer(_pointer(b"abc")[0]), (hashlib.sha256(b"abc").hexdigest(), 3))
        self.assertIsNone(gp._parse_lfs_pointer(b"version https://git-lfs.github.com/spec/v1\noid sha256:xyz\n"))
        self.assertIsNone(gp._parse_lfs_pointer(b"plain text"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
