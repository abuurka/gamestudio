#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""gsfork package: the release archives for GameStudio Editor (Python stdlib only).

    import gsfork_package
    result = gsfork_package.package(gsfork.paths(), gsfork.lock(), REPO / "dist", verify=True)

For gamestudio_version 0.1.0 on Blender v5.2.2, `package()` writes into out_dir:

  GameStudio-Editor-0.1.0-blender5.2.2-windows-x64.zip
      The editor, under one top folder GameStudio-Editor-0.1.0/:
        blender.exe, 5.2/, license/, ...   Blender's install tree (the files CMake's install step
                                           recorded in <build>/install_manifest.txt), unchanged
        GameStudio.cmd                     the launcher users click
        NOTICE.md                          "based on Blender's open source code", licences, and
                                           where the Corresponding Source is (GPLv3 section 6(d))
        license/GameStudio-runtime-MIT.txt the runtime's licence, next to Blender's licence texts
        5.2/scripts/startup/bl_app_templates_system/GameStudio/   the app template (L3)
        5.2/extensions/system/gamestudio/                          the system extension (L3)
        gamestudio/runtime/packages/{engine,devserver,host}/       MIT runtime (optional)
        gamestudio/examples/demo-game/                             example project (optional)
  GameStudio-Editor-0.1.0-blender5.2.2-source.tar.gz
      GPL Corresponding Source, our side: editor/ (patches, native, shell, branding, build, tools,
      tests, upstream.lock.json), the repo-root build scripts and CI workflows, runtime/ and
      examples/, plus UPSTREAM.txt, HOW_TO_BUILD.md and COPYING.
  GameStudio-Editor-0.1.0-blender5.2.2-upstream-source.tar.gz
      Blender exactly at the locked commit: `git archive` of the local mirror, with Git LFS files
      filled in from the mirror's LFS store (each checked against its SHA-256) and tests/files
      left out, as Blender's own `make source_archive` does. Unmodified; the patches are in the
      source archive above.
  SHA256SUMS.txt

With --bundle (implied by --installer) the zip is self-contained, and gamestudio/ also holds:
        gamestudio/node/                   portable Node.js LTS (editor/installer/node.lock.json)
        gamestudio/node_modules/           one shared, prebuilt tree: the production dependencies of
                                           the runtime packages (Electron included) and of every
                                           project's toolchain, copied from the repository's
                                           node_modules and checked against package-lock.json
        gamestudio/bundle.json             marks the packaged layout (core/toolchain.py)
        gamestudio/THIRD-PARTY-NOTICES.md  every bundled package, its version, licence and source
  GameStudio-Editor-0.1.0-blender5.2.2-windows-x64-setup.exe   (--installer)
      The same tree as a per-user Inno Setup installer (editor/installer/gamestudio.iss).

The mirror is only read: one `git archive` process plus plain file reads of its LFS store.
`verify_zip()` extracts the zip and starts its blender.exe headless, without the development
environment variables and with a scratch BLENDER_USER_RESOURCES, to prove the package works alone.
`verify_installer()` installs silently into a scratch folder, checks the editor, creates a project
with npm cut off from the network and no system Node.js, starts its dev server, builds it, and
uninstalls, checking what is left behind.

Standalone use (the same as `gsfork package` once gsfork exposes it):
    python editor/tools/gsfork_package.py [--out DIR] [--verify] [--no-upstream-source] [--installer]
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.request
import zipfile
import zlib

REPO = pathlib.Path(__file__).resolve().parents[2]
PRODUCT = "GameStudio-Editor"
PLATFORM = "windows-x64"

# The IDE shell, as it sits in the repository and where it goes inside <install>/<X.Y>/.
TEMPLATE_SRC = "editor/shell/scripts/startup/bl_app_templates_system/GameStudio"
TEMPLATE_DST = "scripts/startup/bl_app_templates_system/GameStudio"
EXTENSION_SRC = "blender/gamestudio"
EXTENSION_DST = "extensions/system/gamestudio"
TEMPLATE_MODULE = "bl_app_templates_system.GameStudio"
EXTENSION_MODULE = "bl_ext.system.gamestudio"

# Runtime packages and examples shipped under <install>/gamestudio/ (MIT, an aggregate: GPLv3 section 5).
RUNTIME_DIR = "gamestudio"
RUNTIME_PACKAGES = ("engine", "devserver", "host")
EXAMPLE_PROJECTS = ("demo-game",)

# Our part of the Corresponding Source: everything needed to rebuild and repackage the editor.
SOURCE_ROOT_FILES = ("package.json", "package-lock.json", "tsconfig.base.json", ".gitignore", "README.md")
SOURCE_DIRS = ("blender", "editor", "runtime", "examples", "tools", ".github", "docs")

# Never shipped from our tree: caches, installs, local machine config, build outputs.
SKIP_DIR_NAMES = {"__pycache__", "node_modules", ".git", ".gamestudio", "dist", ".smoke", ".vite",
                  ".vite-temp", ".pytest_cache", ".mypy_cache", ".ruff_cache"}
SKIP_SUFFIXES = {".pyc", ".pyo", ".blend1", ".blend2"}
SKIP_REPO_PATHS = {
    "editor/gsfork.local.json",                                   # machine paths
    "blender/gamestudio/assistant/_vendor",  # pip-installed at run time
}

# On Windows the install prefix is the build's own output folder (<build>/bin), so the programs the
# build links are already in place and no install() rule lists them in install_manifest.txt.
RUNTIME_OUTPUTS = ("blender.exe", "blender-launcher.exe", "blender_cpu_check.dll")
# Build-time tools and link artifacts Ninja leaves in bin/. Never shipped.
BUILD_ONLY_FILES = {"datatoc.exe", "datatoc_icon.exe", "makesdna.exe", "makesrna.exe", "msgfmt.exe",
                    "shader_tool.exe", "glsl_preprocess.exe", "zstd_compress.exe", "blender.lib", "blender.exp"}
BUILD_ONLY_SUFFIXES = {".pdb", ".ilk", ".exp"}  # only used when there is no install_manifest.txt
NEVER_FROM_BIN = ("portable/",)  # a "portable" folder would make Blender keep user files in the install

# Inputs compiled into blender.exe. If any changed after the build, the source archive would not
# correspond to the binary (GPLv3 section 1), so the package is flagged as not releasable.
BUILD_INPUTS = ("editor/upstream.lock.json", "editor/patches", "editor/native", "editor/build",
                "editor/branding/overlay")

LFS_POINTER = b"version https://git-lfs.github.com/spec/v1"
UPSTREAM_EXCLUDE = "tests/files"  # Blender's test data; Blender's own source tarball leaves it out too
GPL_TEXT = "license/spdx/GPL-3.0-or-later.txt"

# The bundled runtime and the installer.
NODE_LOCK = "editor/installer/node.lock.json"
ISS_SCRIPT = "editor/installer/gamestudio.iss"
TOOLCHAIN_SRC = f"{EXTENSION_SRC}/core/toolchain.py"
PROJECT_TEMPLATES_SRC = f"{EXTENSION_SRC}/project_templates"
BRANDING = "editor/branding/generated"
APPID_SOURCE = "editor/build/gamestudio.cmake"  # BLENDER_WIN_APPID_PREFIX, the taskbar identity (S03)
BUNDLE_MANIFEST = "bundle.json"
NOTICES_NAME = "THIRD-PARTY-NOTICES.md"
SCOPE = "@gamestudio/"
INSTALLER_APP_ID = "{F56BAC38-D8A3-470F-BE3E-3CE0378FD7A7}_is1"  # gamestudio.iss AppId, as registered
# Licences of bundled npm packages that need nothing beyond shipping their texts and notices (MPL-2.0:
# unmodified, source location listed). Anything else makes the package "NOT releasable" until reviewed.
REVIEWED_LICENSES = {"MIT", "ISC", "BSD-2-Clause", "BSD-3-Clause", "Apache-2.0", "MPL-2.0", "0BSD", "CC0-1.0",
                     "BlueOak-1.0.0", "Unlicense", "Python-2.0", "Artistic-2.0", "CC-BY-4.0"}
# Wizard image: the cube from splash.png (1000x500), cropped to Inno Setup's 164:314 aspect.
WIZARD_CROP = (72, 0, 317, 470)
# The default install folder, C:\Users\<name>\AppData\Local\Programs\GameStudio\, with a 20-character name.
INSTALL_PREFIX_BUDGET = 64


class PackageError(RuntimeError):
    pass


# ----------------------------------------------------------------------------------- naming


def names(lock: dict) -> dict:
    """Every file and folder name a release uses, derived from upstream.lock.json."""
    version = lock["gamestudio_version"]
    tag = lock["blender"]["tag"]
    blender = tag[1:] if tag.startswith("v") else tag
    match = re.match(r"(\d+)\.(\d+)", blender)
    if not match:
        raise PackageError(f"Cannot read a Blender version from the tag {tag!r}")
    base = f"{PRODUCT}-{version}-blender{blender}"
    return {
        "version": version,
        "blender": blender,
        "series": f"{match.group(1)}.{match.group(2)}",  # the <X.Y> folder of a Blender install
        "release_tag": f"gs-v{version}",
        "top": f"{PRODUCT}-{version}",
        "zip": f"{base}-{PLATFORM}.zip",
        "installer": f"{base}-{PLATFORM}-setup.exe",
        "source": f"{base}-source.tar.gz",
        "source_prefix": f"{base}-source",
        "upstream": f"{base}-upstream-source.tar.gz",
        "upstream_prefix": f"blender-{blender}",
        "sums": "SHA256SUMS.txt",
    }


# ----------------------------------------------------------------------------------- helpers


def _say(msg: str) -> None:
    print(f"[gsfork] {msg}", flush=True)


def sha256_file(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def walk(root: pathlib.Path, skip_rel: set[str] | None = None, rel_base: str = "") -> list[tuple[pathlib.Path, str]]:
    """Files under root as (path, relative posix path), sorted, without caches and excluded paths.

    skip_rel holds paths relative to rel_base + root, e.g. "editor/gsfork.local.json".
    """
    skip_rel = skip_rel or set()
    found = []
    root = pathlib.Path(root)
    if not root.is_dir():
        return found
    for dirpath, dirnames, filenames in os.walk(root):
        here = pathlib.Path(dirpath)
        rel_dir = here.relative_to(root).as_posix()
        rel_dir = "" if rel_dir == "." else rel_dir + "/"
        keep = []
        for d in sorted(dirnames):
            full_rel = rel_base + rel_dir + d
            if d in SKIP_DIR_NAMES or full_rel in skip_rel or (here / d).is_symlink():
                continue
            keep.append(d)
        dirnames[:] = keep
        for name in sorted(filenames):
            rel = rel_dir + name
            if pathlib.PurePath(name).suffix in SKIP_SUFFIXES or rel_base + rel in skip_rel:
                continue
            path = here / name
            if path.is_symlink() or not path.is_file():
                continue
            found.append((path, rel))
    found.sort(key=lambda item: item[1])
    return found


def _replace_atomically(partial: pathlib.Path, final: pathlib.Path) -> None:
    if final.exists():
        final.unlink()
    os.replace(partial, final)


class _TarGzWriter:
    """A deterministic-ish .tar.gz: sorted input, uid/gid 0, no user names, fixed gzip header time."""

    def __init__(self, path: pathlib.Path, pax_headers: dict | None = None, level: int = 6):
        self.raw = open(path, "wb")
        self.gz = gzip.GzipFile(filename="", mode="wb", fileobj=self.raw, mtime=0, compresslevel=level)
        self.tar = tarfile.open(fileobj=self.gz, mode="w", format=tarfile.PAX_FORMAT,
                                pax_headers=pax_headers or {})
        self.count = 0

    def add_bytes(self, name: str, data: bytes, mtime: float | None = None, mode: int = 0o644) -> None:
        info = tarfile.TarInfo(name)
        info.size = len(data)
        info.mtime = int(mtime if mtime is not None else time.time())
        info.mode = mode
        info.uid = info.gid = 0
        info.uname = info.gname = ""
        self.tar.addfile(info, io.BytesIO(data))
        self.count += 1

    def add_file(self, name: str, path: pathlib.Path) -> None:
        st = path.stat()
        info = tarfile.TarInfo(name)
        info.size = st.st_size
        info.mtime = int(st.st_mtime)
        info.mode = 0o755 if path.suffix in {".sh", ".py", ".mjs"} and _has_shebang(path) else 0o644
        info.uid = info.gid = 0
        info.uname = info.gname = ""
        with open(path, "rb") as f:
            self.tar.addfile(info, f)
        self.count += 1

    def add_member(self, info: tarfile.TarInfo, data: bytes | None = None) -> None:
        info.uid = info.gid = 0
        info.uname = info.gname = ""
        if data is None:
            self.tar.addfile(info)
        else:
            info.size = len(data)
            self.tar.addfile(info, io.BytesIO(data))
        self.count += 1

    def close(self) -> None:
        self.tar.close()
        self.gz.close()
        self.raw.close()


def _has_shebang(path: pathlib.Path) -> bool:
    try:
        with open(path, "rb") as f:
            return f.read(2) == b"#!"
    except OSError:
        return False


# ----------------------------------------------------------------------------- the editor zip


def bin_files(bin_dir: pathlib.Path, build_dir: pathlib.Path | None
              ) -> tuple[list[tuple[pathlib.Path, str]], str, list[str]]:
    """The installed files of the Blender build as (path, path relative to bin), how they were
    listed, and top-level programs in bin/ that were left out because nothing says they ship.

    Ninja builds install into the same bin/ the build tools are written to, so bin/ also holds
    makesdna.exe, makesrna.exe, their PDBs, runtime __pycache__ folders and so on. CMake's
    install_manifest.txt lists exactly what the install rules put there, which is what upstream
    ships; add the programs the build links in place (RUNTIME_OUTPUTS).
    """
    bin_dir = pathlib.Path(bin_dir)
    manifest = (pathlib.Path(build_dir) if build_dir else bin_dir.parent) / "install_manifest.txt"
    files: dict[str, pathlib.Path] = {}
    unknown: list[str] = []
    if manifest.is_file():
        root = os.path.normcase(os.path.normpath(str(bin_dir))) + os.sep
        for line in manifest.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            full = os.path.normpath(line)
            if not os.path.normcase(full).startswith(root) or not os.path.isfile(full):
                continue
            files[full[len(root):].replace(os.sep, "/")] = pathlib.Path(full)
        for name in RUNTIME_OUTPUTS:
            if (bin_dir / name).is_file():
                files.setdefault(name, bin_dir / name)
        unknown = sorted(p.name for p in bin_dir.iterdir()
                         if p.is_file() and p.suffix.lower() in {".exe", ".dll"} and p.name not in files
                         and p.name.lower() not in BUILD_ONLY_FILES)
        how = f"install manifest ({manifest}) plus {', '.join(RUNTIME_OUTPUTS)}"
    else:
        for dirpath, dirnames, filenames in os.walk(bin_dir):
            here = pathlib.Path(dirpath)
            rel_dir = here.relative_to(bin_dir).as_posix()
            rel_dir = "" if rel_dir == "." else rel_dir + "/"
            dirnames.sort()
            for name in sorted(filenames):
                rel = rel_dir + name
                if not rel_dir and (name.lower() in BUILD_ONLY_FILES
                                    or pathlib.PurePath(name).suffix.lower() in BUILD_ONLY_SUFFIXES):
                    continue
                if "/__pycache__/" in f"/{rel}" or name.endswith((".pyc", ".pyo")):
                    continue
                files[rel] = here / name
        how = f"directory walk of {bin_dir} (no install_manifest.txt; build tools and PDBs left out)"
    kept = [(path, rel) for rel, path in files.items() if not rel.startswith(NEVER_FROM_BIN)]
    kept.sort(key=lambda item: item[1])
    return kept, how, unknown


LAUNCHER = r"""@echo off
rem GameStudio Editor launcher. GameStudio Editor is based on Blender's open source code.
rem The editor binary keeps Blender's file name (blender.exe); this script is what users start.
setlocal
set "GS_HOME=%~dp0"
rem New Project finds the bundled runtime packages through GAMESTUDIO_RUNTIME.
if not defined GAMESTUDIO_RUNTIME if exist "%GS_HOME%gamestudio\runtime\packages\engine\package.json" set "GAMESTUDIO_RUNTIME=%GS_HOME%gamestudio\runtime\packages"
if exist "%GS_HOME%blender-launcher.exe" (
    start "" "%GS_HOME%blender-launcher.exe" %*
) else (
    start "" "%GS_HOME%blender.exe" %*
)
"""


def launcher_text() -> bytes:
    return LAUNCHER.replace("\r\n", "\n").replace("\n", "\r\n").encode("ascii")


def notice_text(lock: dict, n: dict, mit_text: str, with_runtime: bool,
                upstream_notices: tuple[str, ...] = ("license/",), bundle: dict | None = None) -> str:
    bl = lock["blender"]
    kept = ", ".join(f"`{name}`" for name in upstream_notices) or "`license/`"
    runtime_note = (
        "- **Runtime packages and examples** in `gamestudio/` (`@gamestudio/engine`, `@gamestudio/devserver`, "
        "`@gamestudio/host`, `examples/`) are released under the **MIT licence** below. They are separate "
        "programs shipped alongside the editor (an aggregate, GPLv3 section 5) and never link Blender code.\n"
        if with_runtime else
        "- The GameStudio runtime packages (MIT, licence text below) are distributed separately.\n"
    )
    if bundle:
        runtime_note += (
            f"- **Bundled third-party programs**, also separate programs in the same aggregate, each under its "
            f"own licence: **Node.js {bundle['node']}** in `gamestudio/node/` (MIT; the notices of the "
            f"components it includes are in `gamestudio/node/LICENSE`; npm is under the Artistic License 2.0, "
            f"`gamestudio/node/node_modules/npm/LICENSE`), **Electron {bundle['electron']}** in "
            f"`gamestudio/node_modules/electron/` (MIT; Chromium's notices in `dist/LICENSES.chromium.html`), "
            f"and {bundle['count']} npm packages in `gamestudio/node_modules/`. `gamestudio/{NOTICES_NAME}` lists "
            f"each one with its version, licence and source; its licence text is in its own folder.\n")
    start = ("Start GameStudio from its Start-menu shortcut (installer), or run `GameStudio.cmd` (zip)"
             if bundle else "To start the editor, run `GameStudio.cmd`")
    return f"""# GameStudio Editor {n['version']}

GameStudio Editor is based on Blender's open source code. It is built from Blender {n['blender']}
(`{bl['tag']}`, commit `{bl['commit']}`) with a small set of additional patches, a native module and
the GameStudio IDE shell.

GameStudio is an independent project. It is not made, endorsed or supported by the Blender
Foundation. Blender is a trademark of the Blender Foundation. Blender's credits and copyright
notices are kept unchanged in {kept}.

{start} (the program file itself is still called `blender.exe`).

## Licences

- **GameStudio Editor** (Blender, the GameStudio patches, the native module, the app template and
  the `gamestudio` system extension) is free software under the **GNU General Public License,
  version 3 or later**. The full text is `license/spdx/GPL-3.0-or-later.txt`. Blender's third-party
  libraries and their licences are listed in `license/license.md`.
{runtime_note}
## Source code

The complete Corresponding Source for this build is published next to this download, in the same
place (release `{n['release_tag']}`), at no further charge:

- `{n['source']}`: the GameStudio patches, native module, IDE shell, branding, build scripts
  (`editor/tools/gsfork.py`, `editor/tools/gsfork_package.py`, `editor/build/`), runtime and examples,
  with `HOW_TO_BUILD.md` and `UPSTREAM.txt`;
- `{n['upstream']}`: the unmodified Blender sources at commit `{bl['commit']}`
  (also available from {bl['remote']}, tag `{bl['tag']}`);
- `SHA256SUMS.txt`: checksums of every file in the release.

The source stays available for as long as this build is offered for download.

## MIT licence (GameStudio runtime)

```
{mit_text.strip()}
```
"""


def runtime_workspace(repo: pathlib.Path) -> bytes:
    """package.json for <install>/gamestudio: lets `npm install` there set up the example projects."""
    root = json.loads((repo / "package.json").read_text(encoding="utf-8")) if (repo / "package.json").is_file() else {}
    dev = {k: v for k, v in root.get("devDependencies", {}).items() if k in {"typescript", "vite", "@types/node"}}
    data = {
        "name": "gamestudio-runtime",
        "private": True,
        "description": "GameStudio runtime packages and examples bundled with GameStudio Editor (MIT).",
        "license": "MIT",
        "workspaces": ["runtime/packages/*", "examples/*"],
        "devDependencies": dev,
        "engines": root.get("engines", {"node": ">=22.12.0"}),
    }
    return (json.dumps(data, indent=2) + "\n").encode("utf-8")


RUNTIME_README = """GameStudio runtime, bundled with GameStudio Editor (MIT licence, see ../NOTICE.md)

runtime/packages/   @gamestudio/engine, @gamestudio/devserver, @gamestudio/host
examples/           example game projects

New Project links new games to runtime/packages (GameStudio.cmd sets GAMESTUDIO_RUNTIME).
To run an example outside the editor: install Node.js 22.12+, then run `npm install` in this
folder and `npm run dev -w examples/demo-game`.
"""

BUNDLED_README = """GameStudio runtime, bundled with GameStudio Editor (MIT licence, see ../NOTICE.md)

node/               portable Node.js {node} (licence: node/LICENSE)
node_modules/       the packages every game project shares, Electron included ({notices})
runtime/packages/   @gamestudio/engine, @gamestudio/devserver, @gamestudio/host
examples/           example game projects

The editor uses all of this by itself; nothing else needs to be installed and New Project needs
no network. A new game's node_modules links to node_modules/ and runtime/packages/ here (no
copies). Examples are copied to Documents\\GameStudio\\Examples before they open.
To use the bundled Node.js in a terminal:  set PATH=<this folder>\\node;%PATH%
"""


# ------------------------------------------------------------------------ the bundled runtime


def _is_link(path) -> bool:
    path = str(path)
    return os.path.islink(path) or (hasattr(os.path, "isjunction") and os.path.isjunction(path))


def walk_tree(root: pathlib.Path, skip_dirs=frozenset()) -> list[tuple[pathlib.Path, str]]:
    """Every file under root as (path, relative posix path), sorted. Links are never followed.
    Unlike walk(), nothing is skipped by name except skip_dirs (packages ship dist/, build/, ...)."""
    found = []
    root = pathlib.Path(root)
    for dirpath, dirnames, filenames in os.walk(root):
        here = pathlib.Path(dirpath)
        dirnames[:] = sorted(d for d in dirnames if d not in skip_dirs and not _is_link(here / d))
        rel_dir = here.relative_to(root).as_posix()
        rel_dir = "" if rel_dir == "." else rel_dir + "/"
        for name in sorted(filenames):
            if not _is_link(here / name):
                found.append((here / name, rel_dir + name))
    found.sort(key=lambda item: item[1])
    return found


def load_toolchain(repo: pathlib.Path = REPO):
    """The shell's core/toolchain.py (standard library only), so packaging and the editor agree
    on version ranges and on the packaged layout."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("gs_toolchain", repo / TOOLCHAIN_SRC)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def node_spec(repo: pathlib.Path = REPO) -> dict:
    return json.loads((pathlib.Path(repo) / NODE_LOCK).read_text(encoding="utf-8"))


def fetch_node(spec: dict, downloads: pathlib.Path, node_zip=None, log=_say) -> pathlib.Path:
    """The portable Node.js folder (node.exe, npm, LICENSE), downloaded once into `downloads`,
    or taken from node_zip, and always checked against the SHA-256 pinned in node.lock.json."""
    downloads = pathlib.Path(downloads)
    downloads.mkdir(parents=True, exist_ok=True)
    name = spec["url"].rsplit("/", 1)[-1]
    archive = pathlib.Path(node_zip) if node_zip else downloads / name
    if not archive.is_file():
        if node_zip:
            raise PackageError(f"--node-zip {archive} does not exist.")
        log(f"Downloading {spec['url']}")
        partial = archive.with_name(archive.name + ".partial")
        try:
            with urllib.request.urlopen(spec["url"], timeout=120) as response, open(partial, "wb") as f:
                shutil.copyfileobj(response, f, 1 << 20)
        except OSError as exc:
            partial.unlink(missing_ok=True)
            raise PackageError(f"Could not download Node.js ({exc}). Download {spec['url']} by hand "
                               "and pass --node-zip.") from exc
        os.replace(partial, archive)
    digest = sha256_file(archive)
    if digest != spec["sha256"]:
        raise PackageError(f"{archive} has SHA-256 {digest}, but {NODE_LOCK} pins {spec['sha256']}.")
    target = downloads / pathlib.PurePath(name).stem
    stamp = target / ".gsfork-sha256"
    if not (stamp.is_file() and stamp.read_text(encoding="utf-8").strip() == digest):
        partial = downloads / (target.name + ".partial")
        for stale in (target, partial):
            if stale.exists():
                shutil.rmtree(stale)
        with zipfile.ZipFile(archive) as zf:
            zf.extractall(partial)
        inner = list(partial.iterdir())
        os.replace(inner[0] if len(inner) == 1 and inner[0].is_dir() else partial, target)
        shutil.rmtree(partial, ignore_errors=True)
        stamp.write_text(digest + "\n", encoding="utf-8")
    for required in ("node.exe", "LICENSE", "node_modules/npm/bin/npm-cli.js"):
        if not (target / required).is_file():
            raise PackageError(f"{archive} is not a Windows Node.js zip ({required} is missing).")
    return target


def _module_starts(repo: pathlib.Path) -> list[tuple[pathlib.Path, str, str | None, bool, str]]:
    """Where the shared tree starts, as (resolve from, name, wanted spec or None, required, why):
    the runtime packages' production dependencies, and every dependency of the project templates
    and examples, which New Project and Open Example link instead of installing."""
    starts = []
    for pkg in RUNTIME_PACKAGES:
        folder = repo / "runtime" / "packages" / pkg
        data = json.loads((folder / "package.json").read_text(encoding="utf-8"))
        optional = data.get("optionalDependencies") or {}
        for name in data.get("dependencies") or {}:
            starts.append((folder, name, None, name not in optional, f"@gamestudio/{pkg}"))
        for field in ("optionalDependencies", "peerDependencies"):
            for name in data.get(field) or {}:
                starts.append((folder, name, None, False, f"@gamestudio/{pkg}"))
    projects = [p for p in sorted((repo / PROJECT_TEMPLATES_SRC).glob("*/package.json"))]
    projects += [repo / "examples" / name / "package.json" for name in EXAMPLE_PROJECTS]
    for manifest in projects:
        if not manifest.is_file():
            continue
        data = json.loads(manifest.read_text(encoding="utf-8"))
        why = manifest.parent.relative_to(repo).as_posix()
        for field in ("dependencies", "devDependencies"):
            for name, spec in (data.get(field) or {}).items():
                if not name.startswith(SCOPE):
                    starts.append((repo, name, spec, True, why))
    return starts


def _resolve_module(repo: pathlib.Path, from_dir: pathlib.Path, name: str) -> pathlib.Path | None:
    """Node's lookup of `name` from from_dir, up to the repository root."""
    folder = from_dir
    while True:
        candidate = folder / "node_modules" / name
        if (candidate / "package.json").is_file():
            return candidate
        if folder == repo or folder.parent == folder:
            return None
        folder = folder.parent


def _license_of(data: dict) -> str:
    value = data.get("license") or data.get("licenses") or ""
    if isinstance(value, dict):
        value = value.get("type", "")
    if isinstance(value, list):
        value = " OR ".join(item.get("type", "") if isinstance(item, dict) else str(item) for item in value)
    return str(value).strip() or "UNKNOWN"


def _source_of(data: dict) -> str:
    repo = data.get("repository")
    url = repo.get("url", "") if isinstance(repo, dict) else (repo or "")
    url = re.sub(r"^git\+", "", url).removesuffix(".git")
    url = re.sub(r"^(git|ssh)://(git@)?", "https://", url)
    if url.startswith("github:"):
        url = "https://github.com/" + url[len("github:"):]
    elif re.fullmatch(r"[\w.-]+/[\w.-]+", url):
        url = "https://github.com/" + url
    if not url.startswith("http"):
        url = data.get("homepage") or ""
    return url or f"https://www.npmjs.com/package/{data.get('name', '')}/v/{data.get('version', '')}"


def _licenses_ok(expression: str) -> bool:
    names = [t for t in re.findall(r"[A-Za-z0-9.+-]+", expression) if t not in ("OR", "AND", "WITH")]
    return bool(names) and all(name in REVIEWED_LICENSES for name in names)


def module_closure(repo: pathlib.Path = REPO) -> list[dict]:
    """Every package of the shared node_modules, copied from the repository's own install.

    Walks dependencies (and installed optional and peer dependencies) with Node's lookup from
    the starts above, so the bundle has exactly the tree `npm ci` made, nested folders included.
    Each package must match package-lock.json, and each template/example range must accept the
    installed version: otherwise the repository's node_modules is stale (run `npm ci`).
    """
    repo = pathlib.Path(repo)
    lock_file = repo / "package-lock.json"
    locked = json.loads(lock_file.read_text(encoding="utf-8")).get("packages", {}) if lock_file.is_file() else None
    satisfies = load_toolchain(repo).satisfies
    found: dict[str, dict] = {}
    problems: list[str] = []
    queue = list(_module_starts(repo))
    while queue:
        from_dir, name, spec, required, why = queue.pop(0)
        if name.startswith(SCOPE):
            continue  # shipped from runtime/packages
        folder = _resolve_module(repo, from_dir, name)
        if folder is None:
            if required:
                problems.append(f"{name} (needed by {why}) is not installed")
            continue
        rel = folder.relative_to(repo).as_posix()
        if _is_link(folder):
            problems.append(f"{rel} is a link; only @gamestudio/* may be (workspaces)")
            continue
        data = json.loads((folder / "package.json").read_text(encoding="utf-8"))
        version = data.get("version", "")
        if spec is not None and not satisfies(version, spec):
            problems.append(f"{why} asks for {name} {spec}, but {rel} is {version}")
        if rel in found:
            continue
        if locked is not None and (locked.get(rel) or {}).get("version") != version:
            problems.append(f"{rel} is {version}, package-lock.json says {(locked.get(rel) or {}).get('version')}")
        found[rel] = {"rel": rel, "name": name, "version": version, "path": folder,
                      "license": _license_of(data), "source": _source_of(data)}
        optional = data.get("optionalDependencies") or {}
        for dep in data.get("dependencies") or {}:
            queue.append((folder, dep, None, dep not in optional, f"{name}@{version}"))
        for field in ("optionalDependencies", "peerDependencies"):
            for dep in data.get(field) or {}:
                queue.append((folder, dep, None, False, f"{name}@{version}"))
    if problems:
        raise PackageError("The repository's node_modules does not match what the bundle needs; run "
                           "`npm ci` at the repository root:\n  " + "\n  ".join(sorted(set(problems))))
    return sorted(found.values(), key=lambda entry: entry["rel"])


def electron_binary(closure: list[dict]) -> tuple[pathlib.Path, str]:
    entry = next((e for e in closure if e["rel"] == "node_modules/electron"), None)
    if entry is None:
        raise PackageError("electron is not in the bundle, but @gamestudio/host needs it.")
    path_txt = entry["path"] / "path.txt"
    binary = entry["path"] / "dist" / (path_txt.read_text(encoding="utf-8").strip() if path_txt.is_file() else "")
    if not path_txt.is_file() or not binary.is_file():
        raise PackageError(f"Electron's binary is missing ({binary}). Run `node node_modules/electron/install.js` "
                           "at the repository root.")
    version_file = entry["path"] / "dist" / "version"
    if version_file.is_file() and version_file.read_text(encoding="utf-8").strip().lstrip("v") != entry["version"]:
        raise PackageError(f"{binary.parent} holds Electron {version_file.read_text().strip()}, but the package "
                           f"is {entry['version']}. Reinstall it (npm ci).")
    return binary, entry["version"]


def third_party_notices(n: dict, node: dict, closure: list[dict]) -> str:
    rows = "".join(f"| `{e['name']}` | {e['version']} | {e['license']} | `{e['rel']}` | {e['source']} |\n"
                   for e in closure)
    return f"""# Third-party software bundled with GameStudio Editor {n['version']}

These programs ship in `gamestudio/` so that the editor needs nothing else installed. They are
separate programs, not part of GameStudio Editor's GPL work (an aggregate, GPLv3 section 5), each
under its own licence. Every package keeps its licence file in its own folder.

## Node.js {node['version']}

`gamestudio/node/` is the official Windows build of Node.js {node['version']} ({node['url']}),
unmodified. Node.js is under the MIT licence; `gamestudio/node/LICENSE` holds it together with the
licences of the components Node.js includes (V8, libuv, OpenSSL, ICU and others). npm, in
`gamestudio/node/node_modules/npm/`, is under the Artistic License 2.0 (its `LICENSE` file).

## npm packages in `gamestudio/node_modules/`

Unmodified copies of the published packages. Packages under the Mozilla Public License 2.0 are
distributed in the form published to npm; their source code is at the address in the last column.

| Package | Version | Licence | Folder (under `gamestudio/`) | Source |
|---|---|---|---|---|
{rows}"""


def prepare_bundle(paths: dict, lock: dict, downloads=None, node_zip=None, log=_say) -> dict:
    """Everything --bundle adds, checked: the Node.js folder and the shared package tree."""
    repo = pathlib.Path(paths.get("repo") or REPO)
    spec = node_spec(repo)
    downloads = pathlib.Path(downloads or paths.get("downloads") or repo.parent / "gs-downloads")
    node = fetch_node(spec, downloads, node_zip, log=log)
    try:
        reported = subprocess.run([str(node / "node.exe"), "--version"], capture_output=True, text=True,
                                  timeout=60).stdout.strip()
    except (OSError, subprocess.SubprocessError) as exc:
        raise PackageError(f"The bundled node.exe does not run: {exc}") from exc
    if reported != f"v{spec['version']}":
        raise PackageError(f"{node / 'node.exe'} reports {reported!r}, expected v{spec['version']}.")
    closure = module_closure(repo)
    _binary, electron = electron_binary(closure)
    unreviewed = [f"{e['name']}@{e['version']} ({e['license']})" for e in closure if not _licenses_ok(e["license"])]
    log(f"Bundling Node.js {spec['version']} and {len(closure)} packages (Electron {electron})")
    return {"node_dir": node, "node": spec, "closure": closure, "electron": electron, "unreviewed": unreviewed}


def bundle_entries(n: dict, lock: dict, bundle: dict) -> dict:
    """The zip entries of the bundled runtime: {arcname: Path or bytes}."""
    base = f"{n['top']}/{RUNTIME_DIR}"
    plan: dict[str, pathlib.Path | bytes] = {}
    for path, rel in walk_tree(bundle["node_dir"]):
        if rel != ".gsfork-sha256":
            plan[f"{base}/node/{rel}"] = path
    for entry in bundle["closure"]:
        for path, rel in walk_tree(entry["path"], skip_dirs={"node_modules"}):
            plan[f"{base}/{entry['rel']}/{rel}"] = path
    manifest = {
        "comment": "Marks the packaged layout of GameStudio Editor (core/toolchain.py). Generated by gsfork package.",
        "gamestudio": n["version"], "blender": n["blender"], "node": bundle["node"]["version"],
        "electron": bundle["electron"], "node_dir": "node", "modules": "node_modules",
        "packages": "runtime/packages", "module_count": len(bundle["closure"]),
    }
    plan[f"{base}/{BUNDLE_MANIFEST}"] = (json.dumps(manifest, indent=2) + "\n").encode("utf-8")
    plan[f"{base}/{NOTICES_NAME}"] = third_party_notices(n, bundle["node"], bundle["closure"]).encode("utf-8")
    return plan


def changed_since_build(repo: pathlib.Path, exe: pathlib.Path, slack: float = 2.0) -> list[str]:
    """Build inputs (BUILD_INPUTS) modified after exe was linked, as repository-relative paths."""
    built = exe.stat().st_mtime + slack
    changed = []
    for rel in BUILD_INPUTS:
        path = repo / rel
        files = [(path, rel)] if path.is_file() else [(p, f"{rel}/{r}") for p, r in walk(path)]
        changed += [r for p, r in files if p.stat().st_mtime > built]
    return sorted(changed)


def _zip_text(zf: zipfile.ZipFile, arcname: str, data: bytes) -> None:
    info = zipfile.ZipInfo(arcname, date_time=time.localtime()[:6])
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o644 << 16
    zf.writestr(info, data)


def zip_plan(paths: dict, lock: dict, include_runtime: bool = True, bundle: dict | None = None) -> dict:
    """What goes into the zip: {arcname: Path or bytes}, plus metadata. No I/O beyond listing files.

    bundle: prepare_bundle()'s result, to make the package self-contained (Node.js and packages).
    """
    n = names(lock)
    if bundle and not include_runtime:
        raise PackageError("The bundled runtime needs the runtime packages (drop --no-runtime).")
    repo = pathlib.Path(paths.get("repo") or REPO)
    bin_dir = pathlib.Path(paths.get("bin") or pathlib.Path(paths["build"]) / "bin")
    build_dir = pathlib.Path(paths["build"]) if paths.get("build") else bin_dir.parent
    if not (bin_dir / "blender.exe").is_file():
        raise PackageError(f"No editor build at {bin_dir} (blender.exe missing). Run `gsfork build` first.")
    series_dir = bin_dir / n["series"]
    if not series_dir.is_dir():
        raise PackageError(f"{bin_dir} is not a Blender {n['series']} install ({series_dir} is missing).")

    top, ver = n["top"], n["series"]
    shell_prefixes = (f"{ver}/{TEMPLATE_DST}/", f"{ver}/{EXTENSION_DST}/")
    plan: dict[str, pathlib.Path | bytes] = {}
    files, how, unknown = bin_files(bin_dir, build_dir)
    for path, rel in files:
        if rel.startswith(shell_prefixes):
            continue  # the shell always comes from the source tree below
        plan[f"{top}/{rel}"] = path

    template, extension = repo / TEMPLATE_SRC, repo / EXTENSION_SRC
    for src, dst in ((template, TEMPLATE_DST), (extension, EXTENSION_DST)):
        if not src.is_dir():
            raise PackageError(f"The IDE shell is missing: {src}")
        src_rel = src.relative_to(repo).as_posix() + "/"
        for path, rel in walk(src, SKIP_REPO_PATHS, rel_base=src_rel):
            plan[f"{top}/{ver}/{dst}/{rel}"] = path

    mit = repo / "runtime" / "packages" / "engine" / "LICENSE"
    mit_text = mit.read_text(encoding="utf-8") if mit.is_file() else "Copyright (c) GameStudio contributors. MIT licence."
    with_runtime = False
    if include_runtime:
        for pkg in RUNTIME_PACKAGES:
            src = repo / "runtime" / "packages" / pkg
            if not (src / "package.json").is_file():
                raise PackageError(f"Runtime package missing: {src}")
            for path, rel in walk(src):
                plan[f"{top}/{RUNTIME_DIR}/runtime/packages/{pkg}/{rel}"] = path
        for example in EXAMPLE_PROJECTS:
            src = repo / "examples" / example
            for path, rel in walk(src):
                plan[f"{top}/{RUNTIME_DIR}/examples/{example}/{rel}"] = path
        plan[f"{top}/{RUNTIME_DIR}/package.json"] = runtime_workspace(repo)
        if (repo / "tsconfig.base.json").is_file():
            # The packages' and examples' tsconfig.json extend it; Vite reads them to compile the engine.
            plan[f"{top}/{RUNTIME_DIR}/tsconfig.base.json"] = repo / "tsconfig.base.json"
        readme = RUNTIME_README
        if bundle:
            plan.update(bundle_entries(n, lock, bundle))
            readme = BUNDLED_README.format(node=bundle["node"]["version"], notices=NOTICES_NAME)
        plan[f"{top}/{RUNTIME_DIR}/README.txt"] = readme.replace("\n", "\r\n").encode("utf-8")
        with_runtime = True

    upstream_notices = tuple(name for name in ("copyright.txt", "readme.html") if f"{top}/{name}" in plan)
    upstream_notices += ("license/",)
    bundle_note = None
    if bundle:
        bundle_note = {"node": bundle["node"]["version"], "electron": bundle["electron"],
                       "count": len(bundle["closure"])}
        icon = repo / BRANDING / "gamestudio.ico"
        if not icon.is_file():
            raise PackageError(f"The application icon is missing: {icon} (run `gsfork branding`).")
        plan[f"{top}/GameStudio.ico"] = icon
    plan[f"{top}/GameStudio.cmd"] = launcher_text()
    plan[f"{top}/NOTICE.md"] = notice_text(lock, n, mit_text, with_runtime, upstream_notices,
                                           bundle=bundle_note).encode("utf-8")
    plan[f"{top}/license/GameStudio-runtime-MIT.txt"] = mit_text.encode("utf-8")

    warnings = []
    if bundle and bundle["unreviewed"]:
        warnings.append("Bundled packages with licences nobody reviewed for redistribution: "
                        f"{', '.join(bundle['unreviewed'])}. Review them, then add the licence to "
                        "REVIEWED_LICENSES in gsfork_package.py.")
    stale = changed_since_build(repo, bin_dir / "blender.exe")
    if stale:
        shown = ", ".join(stale[:8]) + (f" and {len(stale) - 8} more" if len(stale) > 8 else "")
        warnings.append(f"Built inputs changed after blender.exe was built ({shown}); the source archive "
                        "would not match the binary. Run `gsfork apply` and `gsfork build`, then package again.")
    if unknown:
        warnings.append(f"Left out programs in {bin_dir} that no install rule lists: {', '.join(unknown)}. Add "
                        "each to RUNTIME_OUTPUTS (ships) or BUILD_ONLY_FILES (build tool) in gsfork_package.py.")
    if not any(a.startswith(f"{top}/license/") and not a.endswith("GameStudio-runtime-MIT.txt") for a in plan):
        warnings.append("Blender's license/ folder is not in the install; the zip lacks Blender's licence texts.")
    longest = max((len(a) - len(top) - 1 for a in plan), default=0)
    return {"plan": plan, "bin_listing": how, "warnings": warnings, "longest_path": longest,
            "bin_dir": bin_dir, "names": n, "bundle": bundle, "repo": repo}


def write_zip(plan: dict, out_path: pathlib.Path, level: int = 6) -> int:
    partial = out_path.with_name(out_path.name + ".partial")
    with zipfile.ZipFile(partial, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=level,
                         allowZip64=True, strict_timestamps=False) as zf:
        for arcname in sorted(plan):
            item = plan[arcname]
            if isinstance(item, (bytes, bytearray)):
                _zip_text(zf, arcname, bytes(item))
            else:
                zf.write(item, arcname)
    _replace_atomically(partial, out_path)
    return len(plan)


# ------------------------------------------------------------------------------ the installer


def png_decode(data: bytes) -> tuple[int, int, int, list[bytes]]:
    """(width, height, channels, rows) of an 8-bit RGB or RGBA, non-interlaced PNG."""
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("not a PNG file")
    pos, idat, header = 8, [], None
    while pos < len(data):
        length = int.from_bytes(data[pos:pos + 4], "big")
        kind, body = data[pos + 4:pos + 8], data[pos + 8:pos + 8 + length]
        pos += 12 + length
        if kind == b"IHDR":
            header = body
        elif kind == b"IDAT":
            idat.append(body)
        elif kind == b"IEND":
            break
    width, height = int.from_bytes(header[0:4], "big"), int.from_bytes(header[4:8], "big")
    depth, color, interlace = header[8], header[9], header[12]
    if depth != 8 or color not in (2, 6) or interlace:
        raise ValueError(f"unsupported PNG (bit depth {depth}, colour type {color}, interlace {interlace})")
    bpp = 3 if color == 2 else 4
    raw, stride = zlib.decompress(b"".join(idat)), width * bpp
    rows, previous = [], bytearray(stride)
    for y in range(height):
        start = y * (stride + 1)
        kind, line = raw[start], bytearray(raw[start + 1:start + 1 + stride])
        for i in range(stride):
            left = line[i - bpp] if i >= bpp else 0
            up, corner = previous[i], previous[i - bpp] if i >= bpp else 0
            if kind == 1:
                line[i] = (line[i] + left) & 0xFF
            elif kind == 2:
                line[i] = (line[i] + up) & 0xFF
            elif kind == 3:
                line[i] = (line[i] + ((left + up) >> 1)) & 0xFF
            elif kind == 4:
                p = left + up - corner
                pa, pb, pc = abs(p - left), abs(p - up), abs(p - corner)
                line[i] = (line[i] + (left if pa <= pb and pa <= pc else up if pb <= pc else corner)) & 0xFF
        rows.append(bytes(line))
        previous = line
    return width, height, bpp, rows


def png_encode(width: int, height: int, channels: int, rows: list[bytes]) -> bytes:
    def chunk(kind: bytes, body: bytes) -> bytes:
        return len(body).to_bytes(4, "big") + kind + body + zlib.crc32(kind + body).to_bytes(4, "big")

    header = width.to_bytes(4, "big") + height.to_bytes(4, "big") + bytes([8, 6 if channels == 4 else 2, 0, 0, 0])
    body = zlib.compress(b"".join(b"\x00" + row for row in rows), 9)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", body) + chunk(b"IEND", b"")


def wizard_image(splash: bytes, box: tuple[int, int, int, int] = WIZARD_CROP) -> bytes:
    """The installer's tall wizard image: the cube logo cropped out of the splash."""
    width, height, bpp, rows = png_decode(splash)
    x0, y0, x1, y1 = box
    if not (0 <= x0 < x1 <= width and 0 <= y0 < y1 <= height):
        raise ValueError(f"crop {box} is outside the {width}x{height} splash")
    return png_encode(x1 - x0, y1 - y0, bpp, [row[x0 * bpp:x1 * bpp] for row in rows[y0:y1]])


def app_user_model_id(repo: pathlib.Path, series: str) -> str:
    """The Windows app ID the editor's windows carry (S03: <prefix>.<X.Y>), for the shortcuts."""
    text = (repo / APPID_SOURCE).read_text(encoding="utf-8") if (repo / APPID_SOURCE).is_file() else ""
    match = re.search(r'set\(BLENDER_WIN_APPID_PREFIX\s+"([^"]+)"', text)
    return f"{match.group(1) if match else 'gamestudio.editor'}.{series}"


def numeric_version(version: str) -> str:
    parts = [int(p) for p in re.findall(r"\d+", version)[:4]]
    return ".".join(str(p) for p in parts + [0] * (4 - len(parts)))


def find_iscc(explicit=None) -> pathlib.Path | None:
    """Inno Setup's command-line compiler: --iscc, ISCC, PATH, then the usual install folders."""
    candidates = [explicit, os.environ.get("ISCC"), shutil.which("ISCC")]
    for base in (os.environ.get("LOCALAPPDATA", "") + r"\Programs", os.environ.get("ProgramFiles(x86)"),
                 os.environ.get("ProgramFiles")):
        if base:
            candidates.append(os.path.join(base, "Inno Setup 6", "ISCC.exe"))
    for candidate in candidates:
        if candidate and pathlib.Path(candidate).is_file():
            return pathlib.Path(candidate)
    return None


def stage_tree(plan: dict, top: str, stage: pathlib.Path) -> int:
    """Write the plan below `stage` as files (hard links to the build where possible)."""
    if stage.exists():
        shutil.rmtree(stage)
    for arcname in sorted(plan):
        item = plan[arcname]
        target = stage / arcname[len(top) + 1:]
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(item, (bytes, bytearray)):
            target.write_bytes(bytes(item))
            continue
        try:
            os.link(item, target)
        except OSError:
            shutil.copy2(item, target)
    return len(plan)


def installer_defines(n: dict, repo: pathlib.Path, stage: pathlib.Path, out_dir: pathlib.Path,
                      assets: pathlib.Path) -> dict:
    return {
        "AppVersion": n["version"], "AppVersionNumeric": numeric_version(n["version"]),
        "BlenderVersion": n["blender"], "Series": n["series"],
        "AppUserModelID": app_user_model_id(repo, n["series"]),
        "StageDir": str(stage), "OutputDir": str(out_dir), "OutputBase": n["installer"][:-len(".exe")],
        "SetupIcon": str(repo / BRANDING / "gamestudio.ico"),
        "WizardImage": str(assets / "wizard.png"), "WizardSmallImage": str(repo / BRANDING / "icon_256.png"),
        "InfoFile": str(assets / "notice.txt"),
    }


def build_installer(plan_info: dict, out_dir: pathlib.Path, iscc=None, keep_stage: bool = False,
                    log=_say) -> pathlib.Path:
    """Stage the zip's tree and compile editor/installer/gamestudio.iss into the setup .exe."""
    n, repo, plan = plan_info["names"], pathlib.Path(plan_info["repo"]).resolve(), plan_info["plan"]
    out_dir = pathlib.Path(out_dir).resolve()
    if not plan_info.get("bundle"):
        raise PackageError("The installer needs the bundled runtime (package(..., bundle=True)).")
    compiler = find_iscc(iscc)
    if compiler is None:
        raise PackageError("Inno Setup 6 was not found. Install it (winget install JRSoftware.InnoSetup) "
                           "or pass --iscc PATH\\ISCC.exe.")
    work = out_dir / "installer-work"
    stage, assets = work / "stage", work / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    (assets / "wizard.png").write_bytes(wizard_image((repo / BRANDING / "splash.png").read_bytes()))
    notice = plan[f"{n['top']}/NOTICE.md"]
    (assets / "notice.txt").write_bytes(notice.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))
    log(f"Staging {len(plan)} files in {stage}")
    stage_tree(plan, n["top"], stage)
    defines = installer_defines(n, repo, stage, out_dir, assets)
    cmd = [str(compiler), "/Q", *[f"/D{key}={value}" for key, value in defines.items()], str(repo / ISS_SCRIPT)]
    log(f"Compiling {n['installer']} with {compiler} (LZMA2, a few minutes)")
    started = time.monotonic()
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        raise PackageError(f"ISCC failed ({proc.returncode}):\n{(proc.stdout + proc.stderr)[-4000:]}")
    log(f"  compiled in {(time.monotonic() - started) / 60:.1f} min")
    if not keep_stage:
        shutil.rmtree(work, ignore_errors=True)
    return out_dir / n["installer"]


# -------------------------------------------------------------------------- source archives


def _lfs_store(mirror: pathlib.Path) -> pathlib.Path:
    git_dir = mirror / ".git"
    if git_dir.is_file():  # a worktree or submodule: "gitdir: <path>"
        text = git_dir.read_text(encoding="utf-8").strip()
        if text.startswith("gitdir:"):
            git_dir = (mirror / text.split(":", 1)[1].strip()).resolve()
            common = git_dir / "commondir"
            if common.is_file():
                git_dir = (git_dir / common.read_text(encoding="utf-8").strip()).resolve()
    return git_dir / "lfs" / "objects"


def _parse_lfs_pointer(data: bytes) -> tuple[str, int] | None:
    if not data.startswith(LFS_POINTER) or len(data) > 1024:
        return None
    oid = size = None
    for line in data.decode("ascii", errors="replace").splitlines():
        if line.startswith("oid sha256:"):
            oid = line.split(":", 1)[1].strip()
        elif line.startswith("size "):
            try:
                size = int(line.split(" ", 1)[1])
            except ValueError:
                return None
    if oid and re.fullmatch(r"[0-9a-f]{64}", oid) and size is not None:
        return oid, size
    return None


def _lfs_object(store: pathlib.Path, oid: str, size: int) -> bytes | None:
    path = store / oid[0:2] / oid[2:4] / oid
    if not path.is_file():
        return None
    data = path.read_bytes()
    if len(data) != size or hashlib.sha256(data).hexdigest() != oid:
        raise PackageError(f"The LFS object {path} is corrupt (size or SHA-256 mismatch).")
    return data


def write_upstream_archive(mirror: pathlib.Path, lock: dict, out_path: pathlib.Path,
                           allow_incomplete: bool = False) -> dict:
    """Blender at the locked commit, from `git archive` of the mirror, with LFS files resolved."""
    n = names(lock)
    commit = lock["blender"]["commit"]
    prefix = n["upstream_prefix"] + "/"
    store = _lfs_store(mirror)
    env = dict(os.environ, GIT_LFS_SKIP_SMUDGE="1")  # never let git-lfs download into the mirror
    cmd = ["git", "-c", "core.autocrlf=false", "-c", "core.eol=lf", "-C", str(mirror), "archive",
           "--format=tar", f"--prefix={prefix}", commit, "--", ".", f":(exclude){UPSTREAM_EXCLUDE}"]
    partial = out_path.with_name(out_path.name + ".partial")
    resolved, missing, count, stream_error = 0, [], 0, None
    with tempfile.TemporaryFile() as err:
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=err, env=env)
        except OSError as exc:
            raise PackageError(f"Cannot run git: {exc}") from exc
        writer = _TarGzWriter(partial, pax_headers={"comment": commit})
        try:
            with tarfile.open(fileobj=proc.stdout, mode="r|") as src:
                for member in src:
                    if member.isfile():
                        data = src.extractfile(member).read()
                        pointer = _parse_lfs_pointer(data) if len(data) <= 1024 else None
                        if pointer:
                            blob = _lfs_object(store, *pointer)
                            if blob is None:
                                missing.append(member.name[len(prefix):])
                            else:
                                data = blob
                                resolved += 1
                        info = tarfile.TarInfo(member.name)
                        info.mtime, info.mode, info.type = member.mtime, member.mode, tarfile.REGTYPE
                        writer.add_member(info, data)
                        count += 1
                    else:
                        writer.add_member(member)
        except tarfile.TarError as exc:
            stream_error = exc
        finally:
            writer.close()
            proc.stdout.close()
            code = proc.wait()
        if code != 0 or stream_error is not None:
            err.seek(0)
            partial.unlink(missing_ok=True)
            raise PackageError(f"`git archive` of {commit[:10]} in {mirror} failed ({code}, {stream_error}): "
                               f"{err.read().decode('utf-8', 'replace').strip()}\nRun `gsfork sync` first.")
    if count == 0:
        partial.unlink(missing_ok=True)
        raise PackageError(f"`git archive` of {commit[:10]} in {mirror} produced no files.")
    fatal = [m for m in missing if not m.startswith("tests/")]
    if fatal and not allow_incomplete:
        partial.unlink(missing_ok=True)
        shown = "\n  ".join(fatal[:20])
        raise PackageError(f"{len(fatal)} Git LFS file(s) are not in the mirror's LFS store ({store}), so the "
                           f"source archive would be incomplete. Run `gsfork sync` (git lfs pull).\n  {shown}")
    _replace_atomically(partial, out_path)
    return {"files": count, "lfs_resolved": resolved, "lfs_missing": missing, "lfs_store": str(store)}


def upstream_text(lock: dict, n: dict, upstream: dict | None) -> str:
    bl, libs = lock["blender"], lock.get("libs", {})
    lib_lines = "".join(f"  {name}: {info['remote']} @ {info['commit']}\n" for name, info in libs.items())
    if upstream:
        archive = (f"A complete copy of these upstream sources is published next to this archive:\n\n"
                   f"  {n['upstream']}\n  sha256 {upstream['sha256']}\n\n"
                   f"It was made with `git archive {bl['commit']}` (top folder {n['upstream_prefix']}/). Git LFS\n"
                   f"files ({upstream['lfs_resolved']}) were filled in from the LFS store and checked against their\n"
                   f"SHA-256; tests/files (test data) is left out, as in Blender's own `make source_archive`.\n"
                   f"The archive is unmodified upstream; GameStudio's changes are the patches in\n"
                   f"gamestudio/editor/patches/, applied in the order of gamestudio/editor/patches/series.\n")
        if upstream["lfs_missing"]:
            archive += (f"\n{len(upstream['lfs_missing'])} test-data file(s) outside tests/files are LFS pointers "
                        f"only; they are not needed to build or run.\n")
    else:
        archive = ("No upstream source archive was produced by this packaging run (development build).\n"
                   "A build distributed to anyone must be released with one; see docs/fork/RELEASING.md.\n")
    return f"""Upstream sources of GameStudio Editor {n['version']}

GameStudio Editor is based on Blender's open source code. The upstream Blender sources are:

  repository  {bl['remote']}
  tag         {bl['tag']}
  commit      {bl['commit']}

Precompiled libraries used by the build (Blender's own, unmodified, pinned by that commit):
{lib_lines}
{archive}
To check the archive against upstream:
  git clone --depth 1 --branch {bl['tag']} {bl['remote']} blender-check
  git -C blender-check rev-parse HEAD     (must print {bl['commit']})
  git -C blender-check lfs pull --exclude "tests/**"
  then compare the trees, ignoring tests/files and .git.
"""


def how_to_build_text(lock: dict, n: dict) -> str:
    bl = lock["blender"]
    lib = lock.get("libs", {}).get("windows_x64", {})
    return f"""# How to build GameStudio Editor {n['version']} from source

This archive and `{n['upstream']}` are the complete Corresponding Source of
`{n['zip']}`. GameStudio Editor is Blender `{bl['tag']}` (commit `{bl['commit']}`)
plus the GameStudio patches, native module, branding and IDE shell in `gamestudio/editor/`.

| Path | What it is |
|---|---|
| `UPSTREAM.txt` | The exact upstream repository, tag, commit and library commit |
| `gamestudio/editor/upstream.lock.json` | The same pins, as the build tool reads them |
| `gamestudio/editor/patches/` | The seams, applied in the order of `series` |
| `gamestudio/editor/branding/overlay/` | Files copied over the upstream tree (icons, splash, `.rc`) |
| `gamestudio/editor/native/` | Out-of-tree C++ module, compiled in through patch S01 (`-DGS_NATIVE_DIR`) |
| `gamestudio/editor/build/gamestudio.cmake` | Build options, loaded after Blender's `blender_full.cmake` |
| `gamestudio/editor/shell/` | The IDE shell: app template and the `gamestudio` system extension |
| `gamestudio/editor/tools/gsfork.py` | The build tool (sync, apply, build, test, package) |
| `gamestudio/editor/tools/gsfork_package.py` | The packaging script that made the release zip |
| `gamestudio/runtime/`, `gamestudio/examples/` | MIT runtime packages and example projects |

## Requirements (Windows x64)

- Visual Studio 2022 17.14.14 or newer with "Desktop development with C++" (MSVC 19.44.35216+).
- CMake 3.21+ and Ninja (both ship with the VS C++ CMake tools), git and git-lfs.
- Python 3.12 for the tooling (the build itself uses the Python that ships with Blender's libraries).
- Optional: sccache. Use folders without spaces; about 40 GB of free disk.

## Route A: the GameStudio tool (needs network access to projects.blender.org)

```
cd gamestudio
python editor/tools/gsfork.py sync      # Blender {bl['tag']} at {bl['commit'][:10]}, LFS files, lib/windows_x64
python editor/tools/gsfork.py apply     # clean worktree + patches (git am -3) + branding overlay
python editor/tools/gsfork.py build     # Ninja build and install into <build>/bin
python editor/tools/gsfork.py test      # optional: the shell and add-on tests
python editor/tools/gsfork.py package   # the release zip, these source archives and SHA256SUMS.txt
```

Machine paths default to folders next to `gamestudio/`; override them in
`gamestudio/editor/gsfork.local.json` (`{{"mirror": ..., "work": ..., "build": ..., "sccache": ...}}`).

## Route B: from the upstream source archive

1. Extract `{n['upstream']}` next to this folder, giving `{n['upstream_prefix']}/`.
2. Apply the patches in order, from inside `{n['upstream_prefix']}/`:
   ```
   git -c core.longpaths=true apply ../{n['source_prefix']}/gamestudio/editor/patches/<name>.patch
   ```
   once for each name in `series`, in order. (`core.longpaths` avoids "Filename too long" on Windows.)
3. Copy everything under `gamestudio/editor/branding/overlay/` over `{n['upstream_prefix']}/`.
4. Precompiled libraries: put `lib/windows_x64` at commit `{lib.get('commit', '?')}` of
   {lib.get('remote', 'https://projects.blender.org/blender/lib-windows_x64.git')} (git clone, checkout, `git lfs pull`)
   at `{n['upstream_prefix']}/lib/windows_x64`. To build them from source instead, use Blender's dependency
   builder in `build_files/build_environment/` (`make deps`), which fetches each library at the
   version and checksum listed in `build_files/build_environment/cmake/versions.cmake`.
5. In an "x64 Native Tools Command Prompt for VS 2022", write `gs_initial_cache.cmake`:
   ```
   include("<abs>/{n['upstream_prefix']}/build_files/cmake/config/blender_full.cmake")
   include("<abs>/gamestudio/editor/build/gamestudio.cmake")
   ```
   then configure, build and install:
   ```
   cmake -G Ninja -S {n['upstream_prefix']} -B build -C gs_initial_cache.cmake -DCMAKE_BUILD_TYPE=Release ^
         -DLIBDIR=<abs>/{n['upstream_prefix']}/lib/windows_x64 -DGS_NATIVE_DIR=<abs>/gamestudio/editor/native ^
         -DGAMESTUDIO_VERSION={n['version']}
   ninja -C build install
   ```
6. Package the result: `python gamestudio/editor/tools/gsfork_package.py` with `editor/gsfork.local.json`
   pointing `build` at your build folder (it copies the shell into `build/bin/{n['series']}/` and writes the zip).
   By hand: copy `editor/shell/scripts/startup/bl_app_templates_system/GameStudio` to
   `build/bin/{n['series']}/scripts/startup/bl_app_templates_system/GameStudio` and
   `blender/gamestudio` to `build/bin/{n['series']}/extensions/system/gamestudio`.

## Licences

The editor is GPL-3.0-or-later (`COPYING`). The runtime packages and examples are MIT
(`gamestudio/runtime/packages/*/LICENSE`).
"""


def source_plan(paths: dict) -> list[tuple[pathlib.Path, str]]:
    """Our part of the Corresponding Source, as (path, path relative to the repository)."""
    repo = pathlib.Path(paths.get("repo") or REPO)
    files = []
    for name in SOURCE_ROOT_FILES:
        if (repo / name).is_file():
            files.append((repo / name, name))
    for folder in SOURCE_DIRS:
        files += [(path, f"{folder}/{rel}") for path, rel in walk(repo / folder, SKIP_REPO_PATHS, rel_base=f"{folder}/")]
    if not any(rel == "editor/upstream.lock.json" for _p, rel in files):
        raise PackageError(f"{repo} has no editor/upstream.lock.json")
    files.sort(key=lambda item: item[1])
    return files


def _gpl_text(paths: dict, bin_dir: pathlib.Path) -> bytes | None:
    for candidate in (bin_dir / GPL_TEXT, pathlib.Path(paths.get("work", "")) / "release" / GPL_TEXT):
        if str(candidate) and candidate.is_file():
            return candidate.read_bytes()
    return None


def write_source_archive(paths: dict, lock: dict, out_path: pathlib.Path, upstream: dict | None,
                         bin_dir: pathlib.Path) -> dict:
    n = names(lock)
    prefix = n["source_prefix"]
    files = source_plan(paths)
    partial = out_path.with_name(out_path.name + ".partial")
    writer = _TarGzWriter(partial)
    try:
        writer.add_bytes(f"{prefix}/UPSTREAM.txt", upstream_text(lock, n, upstream).encode("utf-8"))
        writer.add_bytes(f"{prefix}/HOW_TO_BUILD.md", how_to_build_text(lock, n).encode("utf-8"))
        gpl = _gpl_text(paths, bin_dir)
        if gpl:
            writer.add_bytes(f"{prefix}/COPYING", gpl)
        for path, rel in files:
            writer.add_file(f"{prefix}/gamestudio/{rel}", path)
    finally:
        writer.close()
    _replace_atomically(partial, out_path)
    return {"files": writer.count, "copying": gpl is not None}


# ------------------------------------------------------------------------------- package()


def package(paths: dict, lock: dict, out_dir, *, include_runtime: bool = True, upstream_source: bool = True,
            allow_incomplete_source: bool = False, verify: bool = False, verify_dir=None,
            bundle: bool = False, installer: bool = False, node_zip=None, downloads=None, iscc=None,
            verify_play: bool = False, log=_say) -> dict:
    """Build the release zip, the GPL source archives and SHA256SUMS.txt in out_dir.

    paths: gsfork.paths() ("build", "mirror", "work"; optional "bin", "repo" and "downloads").
    lock:  gsfork.lock(), the parsed editor/upstream.lock.json.
    bundle: make the zip self-contained (portable Node.js and the shared packages). installer: also
    build the per-user setup .exe from the same tree (implies bundle). verify then also checks the
    installer end to end (verify_installer); verify_play adds the docked Play run with the UI.
    Returns {"zip", "installer", "source", "upstream_source", "sha256sums": Path,
             "sha256": {name: hex}, "releasable": bool, "warnings": [...], "verify": dict | None, ...}.
    Raises PackageError when something needed for a complete, GPL-compliant release is missing.
    """
    out_dir = pathlib.Path(out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    n = names(lock)
    started = time.monotonic()
    bundle = bundle or installer

    log(f"Packaging GameStudio Editor {n['version']} (Blender {n['blender']}) into {out_dir}")
    bundled = prepare_bundle(paths, lock, downloads=downloads, node_zip=node_zip, log=log) if bundle else None
    plan = zip_plan(paths, lock, include_runtime=include_runtime, bundle=bundled)
    log(f"Blender files from the {plan['bin_listing']}")
    for warning in plan["warnings"]:
        log(f"WARNING: {warning}")
    if plan["longest_path"] > 200:
        log(f"Note: the longest path in the zip is {plan['longest_path']} characters below the top folder; "
            "extract it close to a drive root (Windows MAX_PATH is 260).")
    if installer and plan["longest_path"] > 259 - INSTALL_PREFIX_BUDGET:
        log(f"WARNING: the longest path ({plan['longest_path']} characters) may pass MAX_PATH in the default "
            r"install folder, C:\Users\<name>\AppData\Local\Programs\GameStudio, for long user names.")

    zip_path = out_dir / n["zip"]
    log(f"Writing {zip_path.name} ({len(plan['plan'])} files)")
    write_zip(plan["plan"], zip_path)
    installer_path = build_installer(plan, out_dir, iscc=iscc, log=log) if installer else None

    upstream = None
    upstream_path = out_dir / n["upstream"]
    if upstream_source:
        mirror = pathlib.Path(paths["mirror"])
        log(f"Writing {upstream_path.name} (git archive {lock['blender']['commit'][:10]} from {mirror})")
        upstream = write_upstream_archive(mirror, lock, upstream_path, allow_incomplete=allow_incomplete_source)
        upstream["sha256"] = sha256_file(upstream_path)
        log(f"  {upstream['files']} files, {upstream['lfs_resolved']} LFS file(s) resolved, "
            f"{len(upstream['lfs_missing'])} test-data pointer(s) left as is")

    source_path = out_dir / n["source"]
    log(f"Writing {source_path.name}")
    source = write_source_archive(paths, lock, source_path, upstream, plan["bin_dir"])
    if not source["copying"]:
        plan["warnings"].append("No GPL-3.0 text found for COPYING in the source archive.")

    produced = [zip_path, source_path] + ([upstream_path] if upstream else [])
    produced += [installer_path] if installer_path else []
    sums = {p.name: sha256_file(p) for p in produced}
    sums_path = out_dir / n["sums"]
    sums_path.write_text("".join(f"{digest}  {name}\n" for name, digest in sorted(sums.items())),
                         encoding="utf-8", newline="\n")

    result = {
        "zip": zip_path, "installer": installer_path, "source": source_path,
        "upstream_source": upstream_path if upstream else None,
        "sha256sums": sums_path, "sha256": sums, "names": n, "zip_files": len(plan["plan"]),
        "source_files": source["files"], "upstream": upstream, "bin_listing": plan["bin_listing"],
        "warnings": plan["warnings"], "releasable": upstream is not None and not plan["warnings"],
        "verify": None, "installer_verify": None,
    }
    if verify:
        result["verify"] = verify_zip(zip_path, lock, verify_dir, log=log)
        result["releasable"] = result["releasable"] and result["verify"]["ok"]
        if installer_path:
            result["installer_verify"] = verify_installer(installer_path, lock, play=verify_play, log=log)
            result["releasable"] = result["releasable"] and result["installer_verify"]["ok"]
    log(f"Done in {(time.monotonic() - started) / 60:.1f} min. "
        + ("Releasable." if result["releasable"] else "NOT releasable as is (see warnings above)."))
    return result


# --------------------------------------------------------------------------- verification


VERIFY_EXPR = r"""
import bpy, json, sys
def _file(name):
    module = sys.modules.get(name)
    return getattr(module, "__file__", None) if module else None
try:
    _host = __import__("bl_ext.system.gamestudio.host", fromlist=["info"]).info()
except Exception as exc:
    _host = {"error": repr(exc)}
print("GS_VERIFY " + json.dumps({
    "blender": bpy.app.version_string,
    "binary": bpy.app.binary_path,
    "app_template": bpy.context.preferences.app_template,
    "template_file": _file("bl_app_templates_system.GameStudio"),
    "extension_enabled": "bl_ext.system.gamestudio" in bpy.context.preferences.addons.keys(),
    "extension_file": _file("bl_ext.system.gamestudio"),
    "workspaces": sorted(w.name for w in bpy.data.workspaces),
    "user_config": bpy.utils.user_resource("CONFIG"),
    "user_root": bpy.utils.resource_path("USER"),
    "local_root": bpy.utils.resource_path("LOCAL"),
    "host": _host,
}, default=str), flush=True)
"""

# Development-only variables that would load the shell from the repository or redirect folders.
DEV_ENV_PREFIXES = ("BLENDER_", "GAMESTUDIO_", "GS_")
DEV_ENV_NAMES = {"PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP"}


def clean_env(extra: dict | None = None) -> dict:
    env = {k: v for k, v in os.environ.items()
           if not k.upper().startswith(DEV_ENV_PREFIXES) and k.upper() not in DEV_ENV_NAMES}
    env.update(extra or {})
    return env


def _snapshot(folder: pathlib.Path) -> dict | None:
    """Read-only fingerprint of a folder: {relative path: (size, mtime_ns)}."""
    if not folder.is_dir():
        return None
    shot = {}
    for dirpath, _dirs, filenames in os.walk(folder):
        for name in filenames:
            path = pathlib.Path(dirpath) / name
            try:
                st = path.stat()
            except OSError:
                continue
            shot[path.relative_to(folder).as_posix()] = (st.st_size, st.st_mtime_ns)
    return shot


def _run_blender(exe: pathlib.Path, env: dict, timeout: int) -> tuple[dict | None, str, int]:
    cmd = [str(exe), "--background", "--factory-startup", "--python-exit-code", "1",
           "--python-expr", VERIFY_EXPR]
    proc = subprocess.run(cmd, env=env, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=timeout, cwd=str(exe.parent))
    output = (proc.stdout or "") + (proc.stderr or "")
    report = None
    for line in output.splitlines():
        if line.startswith("GS_VERIFY "):
            report = json.loads(line[len("GS_VERIFY "):])
    return report, output, proc.returncode


def _inside(child: str | None, parent: pathlib.Path) -> bool:
    if not child:
        return False
    try:
        pathlib.Path(child).resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def verify_zip(zip_path, lock: dict, work_dir=None, *, timeout: int = 900, keep: bool = False, log=_say) -> dict:
    """Extract the zip and start its blender.exe headless, twice, without any development variables.

    Run 1 (BLENDER_USER_RESOURCES = scratch): the GameStudio template and the extension load from
    inside the package, and the user config is the scratch folder, not a Blender profile.
    Run 2 (no BLENDER_USER_RESOURCES; BLENDER_USER_CONFIG/SCRIPTS/EXTENSIONS/DATAFILES = scratch, so
    nothing is written to the default location): bpy.utils.resource_path('USER') is the compiled-in
    default, which seam S02 must have moved to a GameStudio folder.
    The real Blender profile is fingerprinted before and after and must be unchanged.
    """
    zip_path = pathlib.Path(zip_path)
    n = names(lock)
    own_dir = work_dir is None
    work = pathlib.Path(work_dir) if work_dir else pathlib.Path(tempfile.mkdtemp(prefix="gs_verify_"))
    extract = work / "x"
    if extract.exists():
        shutil.rmtree(extract)
    extract.mkdir(parents=True)
    log(f"Verifying {zip_path.name}: extracting into {extract}")
    with zipfile.ZipFile(zip_path) as zf:
        tops = {name.split("/", 1)[0] for name in zf.namelist()}
        zf.extractall(extract)
    top = extract / n["top"]
    exe = top / "blender.exe"
    failures = []
    if tops != {n["top"]}:
        failures.append(f"the zip must have exactly one top folder {n['top']}/, found {sorted(tops)}")
    if not exe.is_file():
        failures.append(f"{exe} is missing")
        return {"ok": False, "failures": failures, "extract": str(extract)}

    appdata = pathlib.Path(os.environ.get("APPDATA", pathlib.Path.home() / "AppData" / "Roaming"))
    blender_profile = appdata / "Blender Foundation" / "Blender"
    default_user = appdata / "GameStudio" / "Editor" / n["series"]
    before = _snapshot(blender_profile)
    default_existed = default_user.exists()

    profile1 = work / "profile"
    shutil.rmtree(profile1, ignore_errors=True)
    profile1.mkdir(parents=True)
    log("Run 1: headless, scratch BLENDER_USER_RESOURCES, no development variables")
    run1, out1, code1 = _run_blender(exe, clean_env({"BLENDER_USER_RESOURCES": str(profile1)}), timeout)

    profile2 = work / "profile-default"
    shutil.rmtree(profile2, ignore_errors=True)
    overrides = {}
    for kind in ("CONFIG", "SCRIPTS", "EXTENSIONS", "DATAFILES"):
        folder = profile2 / kind.lower()
        folder.mkdir(parents=True)
        overrides[f"BLENDER_USER_{kind}"] = str(folder)
    log("Run 2: headless, default user root (read-only: every user folder redirected to scratch)")
    run2, out2, code2 = _run_blender(exe, clean_env(overrides), timeout)
    after = _snapshot(blender_profile)

    if code1 != 0 or run1 is None:
        failures.append(f"run 1 exited with {code1} and {'no' if run1 is None else 'a'} report")
    else:
        if run1["app_template"] != "GameStudio":
            failures.append(f"app template is {run1['app_template']!r}, expected 'GameStudio'")
        if not _inside(run1["template_file"], top):
            failures.append(f"the GameStudio template did not load from the package: {run1['template_file']}")
        if not run1["extension_enabled"] or not _inside(run1["extension_file"], top):
            failures.append(f"the gamestudio extension did not load from the package: {run1['extension_file']}")
        if not _inside(run1["user_config"], profile1):
            failures.append(f"user config {run1['user_config']} is not the scratch profile")
        if "Blender Foundation" in (run1["user_config"] or ""):
            failures.append("user config points into the Blender profile")
        if "error" in (run1.get("host") or {}):
            failures.append(f"gamestudio.host.info() failed: {run1['host']['error']}")
    if code2 != 0 or run2 is None:
        failures.append(f"run 2 exited with {code2} and {'no' if run2 is None else 'a'} report")
    else:
        user_root = run2["user_root"] or ""
        if "GameStudio" not in user_root or "Blender Foundation" in user_root:
            failures.append(f"default user root {user_root!r} is not isolated from Blender (seam S02)")
    if before != after:
        failures.append(f"the real Blender profile {blender_profile} changed during verification")

    result = {
        "ok": not failures, "failures": failures, "extract": str(extract), "top": str(top),
        "run1": run1, "run2": run2,
        "blender_profile_untouched": before == after, "blender_profile_exists": before is not None,
        "default_user_root": (run2 or {}).get("user_root"),
        "default_user_root_existed_before": default_existed,
        "default_user_root_exists_after": default_user.exists(),
    }
    if failures:
        result["output_run1"] = out1[-4000:]
        result["output_run2"] = out2[-4000:]
    for line in ([f"OK: template {run1 and run1.get('template_file')}",
                  f"OK: extension {run1 and run1.get('extension_file')}",
                  f"OK: user config {run1 and run1.get('user_config')}",
                  f"OK: default user root {(run2 or {}).get('user_root')}"] if not failures else
                 [f"FAIL: {f}" for f in failures]):
        log("  " + line)
    if not keep and not failures:
        shutil.rmtree(extract, ignore_errors=True)
        if own_dir:
            shutil.rmtree(work, ignore_errors=True)
    return result


# Runs inside the installed editor (headless): New Project, its dev server, a web build, the
# runtime host lookup and Open Example, with npm cut off from the network. Writes a JSON report.
VERIFY_PROJECT_SCRIPT = r'''
import importlib, json, os, pathlib, shutil, subprocess, sys, time, traceback, urllib.request

where, out = (pathlib.Path(a) for a in sys.argv[sys.argv.index("--") + 1:][:2])
report = {}


def ext(name):
    return importlib.import_module("bl_ext.system.gamestudio." + name)


def size(folder):
    total = 0
    for dirpath, dirnames, filenames in os.walk(folder):
        dirnames[:] = [d for d in dirnames if not (os.path.islink(os.path.join(dirpath, d))
                                                   or os.path.isjunction(os.path.join(dirpath, d)))]
        total += sum(os.path.getsize(os.path.join(dirpath, f)) for f in filenames)
    return total


try:
    projects, toolchain, devserver, project = (ext(m) for m in ("projects", "core.toolchain", "core.devserver",
                                                                 "core.project"))
    host = ext("host")
    report["bundle"] = str(toolchain.bundle_root())
    report["node"] = toolchain.node_executable()
    report["npm"] = toolchain.npm_argv()
    report["which"] = {tool: shutil.which(tool) for tool in ("node", "npm", "git")}
    root = where / "offline-game"
    root.mkdir(parents=True)
    projects.scaffold(root, "Offline Game")
    started = time.monotonic()
    # The fork installer bundles every package, so linking alone must satisfy the project.
    linked = projects.ensure_dependencies(root)
    ok = not linked["unavailable"]
    message = "linked " + ", ".join(linked["linked"]) if ok else "missing " + ", ".join(linked["unavailable"])
    report["install"] = {"ok": ok, "message": message, "seconds": round(time.monotonic() - started, 1),
                         "log": (root / ".gamestudio" / "setup.log").read_text(encoding="utf-8",
                                                                               errors="replace")[-3000:],
                         "links": sorted(p.name for p in (root / "node_modules").iterdir())}
    report["scene"] = str(projects.finish_new_project(root))
    report["footprint_new"] = size(root)

    devserver.start(root)
    deadline = time.monotonic() + 150
    while devserver.is_starting() and time.monotonic() < deadline:
        devserver.pump()
        time.sleep(0.25)
    session = project.read_session(root)
    dev = {"ready": session is not None, "error": devserver.last_error(),
           "log": devserver.log_tail(root, 40)}
    with open(devserver.log_path(root), encoding="utf-8", errors="replace") as f:
        dev["command"] = f.readline().strip()
    if session is not None:
        for path in ("/", "/src/main.ts"):
            with urllib.request.urlopen(session["url"].rstrip("/") + path, timeout=60) as response:
                dev[path] = response.status
        browser = sys.argv[sys.argv.index("--") + 1:][2:]
        if browser:
            env = toolchain.child_env()  # the browser needs the real user profile folder to start
            env["USERPROFILE"] = os.environ.get("GS_VERIFY_USERPROFILE") or env.get("USERPROFILE", "")
            proc = subprocess.run([toolchain.node_executable(), browser[0], session["url"], *browser[1:]],
                                  env=env, capture_output=True, text=True, encoding="utf-8",
                                  errors="replace", timeout=300)
            try:
                dev["browser"] = json.loads(proc.stdout.strip().splitlines()[-1])
            except (ValueError, IndexError):
                dev["browser"] = {"error": (proc.stdout + proc.stderr)[-2000:]}
    devserver.stop()
    report["devserver"] = dev

    proc = subprocess.run([*toolchain.npm_argv(), "run", "build"], cwd=root, env=toolchain.child_env(),
                          capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600)
    report["build"] = {"code": proc.returncode, "index": (root / "dist" / "index.html").is_file(),
                       "tail": (proc.stdout + proc.stderr)[-1500:]}
    report["runtime_host"] = list(host.game_view.find_runtime_host(root))
    report["footprint_after"] = size(root)

    example = projects.examples()[0]
    copy = projects.prepare_example(example)
    linked = projects.ensure_dependencies(copy)
    proc = subprocess.run([*toolchain.npm_argv(), "run", "build"], cwd=copy, env=toolchain.child_env(),
                          capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600)
    report["example"] = {"source": str(example), "copy": str(copy), "linked": linked["linked"],
                         "unavailable": linked["unavailable"], "missing": toolchain.missing_dependencies(copy),
                         "manifest": json.loads((copy / "package.json").read_text(encoding="utf-8")),
                         "build": proc.returncode, "build_tail": (proc.stdout + proc.stderr)[-1500:]}
except Exception:
    report["error"] = traceback.format_exc()
out.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
'''

# Run by the bundled Node.js while the new project's dev server runs: the game page in headless
# Edge, through the repository's puppeteer-core. One Three.js, a canvas, no page errors.
BROWSER_CHECK_SCRIPT = r'''
const [url, browserPath, puppeteerUrl] = process.argv.slice(2);
const { default: puppeteer } = await import(puppeteerUrl);
const browser = await puppeteer.launch({ executablePath: browserPath, headless: true,
  args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--window-size=1280,720'] });
const messages = [];
try {
  const page = await browser.newPage();
  page.on('console', (m) => messages.push(`${m.type()}: ${m.text()}`));
  page.on('pageerror', (e) => messages.push(`pageerror: ${e.message}`));
  await page.goto(url, { waitUntil: 'load', timeout: 120000 });
  await new Promise((resolve) => setTimeout(resolve, 15000));
  const canvases = await page.evaluate(() => document.querySelectorAll('canvas').length);
  const three = await page.evaluate(() => globalThis.__THREE__ ?? null);
  console.log(JSON.stringify({ canvases, three,
    multipleThree: messages.filter((m) => m.includes('Multiple instances of Three.js')).length,
    errors: messages.filter((m) => m.startsWith('error') || m.startsWith('pageerror')),
    messages: messages.slice(0, 30) }));
} finally {
  await browser.close();
}
'''


def _browser_check_args(work: pathlib.Path) -> list[str]:
    """[script, browser, puppeteer-core URL] when headless Edge (or Chrome) and the repository's
    puppeteer-core are available, else [] (the browser check is skipped)."""
    puppeteer = REPO / "node_modules" / "puppeteer-core" / "lib" / "puppeteer" / "puppeteer-core.js"
    browsers = [os.environ.get("GS_BROWSER"),
                os.path.join(os.environ.get("ProgramFiles(x86)", ""), "Microsoft", "Edge", "Application", "msedge.exe"),
                os.path.join(os.environ.get("ProgramFiles", ""), "Google", "Chrome", "Application", "chrome.exe")]
    browser = next((b for b in browsers if b and os.path.isfile(b)), None)
    if browser is None or not puppeteer.is_file():
        return []
    script = work / "browser_check.mjs"
    script.write_text(BROWSER_CHECK_SCRIPT, encoding="utf-8")
    return [str(script), browser, puppeteer.as_uri()]


# Programs that would let a run use the system's Node.js or Git instead of the bundled ones.
SYSTEM_TOOLS = ("node.exe", "npm", "npm.cmd", "npx.cmd", "pnpm.cmd", "yarn.cmd", "git.exe")


def strip_tools_from_path(value: str) -> str:
    return os.pathsep.join(entry for entry in value.split(os.pathsep)
                           if entry and not any(os.path.isfile(os.path.join(entry, tool)) for tool in SYSTEM_TOOLS))


def offline_env(extra: dict | None = None) -> dict:
    """clean_env() as a machine without Node.js or Git would see it, with npm's network cut off."""
    env = clean_env(extra)
    for key in [k for k in env if k.upper() == "PATH"]:
        env[key] = strip_tools_from_path(env[key])
    env.update({"npm_config_offline": "true", "npm_config_registry": "http://127.0.0.1:9/"})
    return env


def _uninstall_key() -> str:
    return rf"Software\Microsoft\Windows\CurrentVersion\Uninstall\{INSTALLER_APP_ID}"


def _registry_snapshot() -> dict:
    """Read-only view of the per-user registry an installer could use to take over .blend files."""
    import winreg

    def read(path):
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as key:
                values, i = {}, 0
                while True:
                    try:
                        name, value, _kind = winreg.EnumValue(key, i)
                    except OSError:
                        break
                    values[name], i = str(value), i + 1
                subkeys, i = [], 0
                while True:
                    try:
                        subkeys.append(winreg.EnumKey(key, i))
                    except OSError:
                        break
                    i += 1
                return {"values": values, "subkeys": sorted(subkeys)}
        except OSError:
            return None

    classes = read(r"Software\Classes") or {"subkeys": []}
    return {
        ".blend": read(r"Software\Classes\.blend"),
        ".blend OpenWithProgids": read(r"Software\Classes\.blend\OpenWithProgids"),
        "FileExts .blend": read(r"Software\Microsoft\Windows\CurrentVersion\Explorer\FileExts\.blend"),
        "classes": [k for k in classes["subkeys"] if k.lower().startswith(("blender", "gamestudio"))],
        "uninstall": read(_uninstall_key()) is not None,
    }


def _tree_size(folder: pathlib.Path) -> int:
    total = 0
    for path, _rel in walk_tree(folder) if folder.is_dir() else []:
        try:
            total += path.stat().st_size
        except OSError:
            pass
    return total


def _installed_files(setup_log: pathlib.Path) -> list[str]:
    """Every file Inno Setup's log says it installed."""
    try:
        lines = setup_log.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    return [line.split("Dest filename: ", 1)[1].strip() for line in lines if "Dest filename: " in line
            and not line.rstrip().endswith(".lnk")]


def verify_installer(installer, lock, work_dir=None, *, play: bool = False, timeout: int = 900, keep: bool = False,
                     log=_say) -> dict:
    """Install silently into a scratch folder, use it, uninstall, and check what is left.

    1. /VERYSILENT install into <work>/GameStudio: files, the Start-menu shortcut, the uninstall entry.
    2. The editor headless with a scratch BLENDER_USER_RESOURCES: template, extension, workspaces.
    3. With PATH stripped of every Node.js, npm and Git and npm offline (registry unreachable):
       New Project links its packages, its dev server starts on the bundled Node.js and serves the
       game, `npm run build` writes the web build, the runtime host is the bundled Electron, and
       the bundled example opens as a copy. play=True also runs tools/e2e/blender_game_view.py with
       the UI on the new project (docked Game View, Play, frames).
    4. /VERYSILENT uninstall: the app folder, shortcut, uninstall entry and caches are gone; the
       project and the %APPDATA%\\GameStudio profile stay. The Blender profile folders and the
       .blend registration are compared before and after.
    """
    n = names(lock)
    installer = pathlib.Path(installer)
    work = pathlib.Path(work_dir or tempfile.mkdtemp(prefix="gs_setup_"))
    work.mkdir(parents=True, exist_ok=True)
    app, projects, home = work / "GameStudio", work / "projects", work / "home"
    for folder in (projects, home / "Documents"):
        folder.mkdir(parents=True, exist_ok=True)
    checks: list[tuple[str, bool, str]] = []

    def check(name, ok, detail=""):
        checks.append((name, bool(ok), str(detail)))
        log(f"  {'OK  ' if ok else 'FAIL'} {name}" + (f": {detail}" if detail and not ok else ""))

    if _registry_snapshot()["uninstall"]:
        return {"ok": False, "failures": ["GameStudio is already installed for this user; verification would "
                                          "replace it. Uninstall it first."], "checks": []}
    appdata = pathlib.Path(os.environ.get("APPDATA", pathlib.Path.home() / "AppData" / "Roaming"))
    local = pathlib.Path(os.environ.get("LOCALAPPDATA", pathlib.Path.home() / "AppData" / "Local"))
    watched = {"%APPDATA%\\Blender Foundation": appdata / "Blender Foundation",
               "%LOCALAPPDATA%\\Blender Foundation": local / "Blender Foundation",
               "%APPDATA%\\GameStudio": appdata / "GameStudio"}
    before = {name: _snapshot(folder) for name, folder in watched.items()}
    registry_before = _registry_snapshot()
    shortcut = appdata / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "GameStudio.lnk"
    npm_cache = local / "GameStudio" / "npm-cache"
    editor_cache = local / "GameStudio" / "Editor" / "Cache"

    log(f"Verifying {installer.name}: silent per-user install into {app}")
    proc = subprocess.run([str(installer), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/SP-", f"/DIR={app}",
                           f"/LOG={work / 'install.log'}"], timeout=timeout)
    check("install exits 0", proc.returncode == 0, proc.returncode)
    footprint, run1, report, play_result = 0, {}, {}, None
    try:
        expected = ("blender.exe", "blender-launcher.exe", "GameStudio.ico", "gamestudio/bundle.json",
                    "gamestudio/node/node.exe", "gamestudio/node/LICENSE", "gamestudio/node_modules/vite/package.json",
                    "gamestudio/node_modules/electron/dist/electron.exe")
        check("editor, Node.js and packages installed", all((app / rel).is_file() for rel in expected))
        check("Start-menu shortcut", shortcut.is_file(), shortcut)
        check("uninstall entry (HKCU)", _registry_snapshot()["uninstall"])
        written = _installed_files(work / "install.log")
        missing = [path for path in written if not os.path.isfile(path)]
        check(f"all {len(written)} files the setup wrote are present", written and not missing, missing[:10])
        footprint = _tree_size(app)

        log("Starting the installed editor headless (scratch user folder, no development variables)")
        env = clean_env({"BLENDER_USER_RESOURCES": str(work / "profile")})
        run1, out1, code1 = _run_blender(app / "blender.exe", env, timeout)
        run1 = run1 or {}
        check("headless start", code1 == 0 and bool(run1), f"exit {code1}: {out1[-1500:]}")
        check("app template GameStudio from the install",
              run1.get("app_template") == "GameStudio" and _inside(run1.get("template_file"), app),
              run1.get("template_file"))
        check("gamestudio extension from the install",
              run1.get("extension_enabled") and _inside(run1.get("extension_file"), app), run1.get("extension_file"))
        check("workspaces load", "Level" in (run1.get("workspaces") or []), run1.get("workspaces"))

        log("New Project with npm offline and no system Node.js or Git on PATH")
        script = work / "verify_project.py"
        script.write_text(VERIFY_PROJECT_SCRIPT, encoding="utf-8")
        report_path = work / "project.json"
        env = offline_env({"BLENDER_USER_RESOURCES": str(work / "profile-project"), "USERPROFILE": str(home),
                           "GS_VERIFY_USERPROFILE": os.environ.get("USERPROFILE", "")})
        browser_args = _browser_check_args(work)
        proc = subprocess.run([str(app / "blender.exe"), "--background", "--factory-startup", "--python-exit-code", "1",
                               "--python", str(script), "--", str(projects), str(report_path), *browser_args],
                              env=env, cwd=str(work), capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=timeout)
        report = json.loads(report_path.read_text(encoding="utf-8")) if report_path.is_file() else {}
        check("project script ran", proc.returncode == 0 and report and "error" not in report,
              report.get("error") or (proc.stdout + proc.stderr)[-2000:])
        node_dir = app / "gamestudio" / "node"
        check("bundled Node.js and npm are used", _inside(report.get("node"), node_dir)
              and _inside((report.get("npm") or [""])[0], node_dir), f"{report.get('node')} {report.get('npm')}")
        check("no system Git or Node.js visible", not (report.get("which") or {}).get("git")
              and _inside((report.get("which") or {}).get("node"), node_dir), report.get("which"))
        install = report.get("install") or {}
        check("New Project packages linked offline",
              install.get("ok") and "not available offline" not in install.get("log", ""), install)
        dev = report.get("devserver") or {}
        check("dev server starts with the bundled Node.js",
              dev.get("ready") and str(node_dir) in dev.get("command", ""), dev)
        check("dev server serves the game", dev.get("/") == 200 and dev.get("/src/main.ts") == 200, dev)
        if browser_args:
            page = dev.get("browser") or {}
            check("game page runs: one Three.js, a canvas, no errors", page.get("canvases", 0) >= 1
                  and page.get("multipleThree") == 0 and not page.get("errors") and "error" not in page, page)
        else:
            log("  (skipped the game page check: needs Edge or Chrome and `npm ci` in this repository)")
        check("web build (npm run build)", (report.get("build") or {}).get("code") == 0
              and (report.get("build") or {}).get("index"), report.get("build"))
        check("runtime host is the bundled Electron", _inside((report.get("runtime_host") or [""])[0], app),
              report.get("runtime_host"))
        example = report.get("example") or {}
        check("example opens as a linked copy that builds", _inside(example.get("copy"), home)
              and not example.get("missing") and example.get("build") == 0, example)

        play_result = None
        if play and report.get("scene"):
            play_result = _verify_play(app, pathlib.Path(report["scene"]), work, timeout, log)
            for item in play_result.get("checks", []):
                check(f"play: {item['name']}", item["ok"], item.get("detail", ""))
            if not play_result.get("checks"):
                check("play: ran", False, play_result.get("output", "")[-2000:])

    except Exception:  # noqa: BLE001 - whatever failed, uninstall below and report it
        import traceback

        check("verification ran to the end", False, traceback.format_exc()[-3000:])

    log("Uninstalling silently")
    uninstaller = app / "uninstall" / "unins000.exe"
    check("uninstaller present", uninstaller.is_file(), uninstaller)
    if uninstaller.is_file():
        subprocess.run([str(uninstaller), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART",
                        f"/LOG={work / 'uninstall.log'}"], timeout=timeout)
        deadline = time.monotonic() + 180  # the uninstaller finishes from a copy of itself in %TEMP%
        while time.monotonic() < deadline and (app.exists() or _registry_snapshot()["uninstall"]):
            time.sleep(1)
    leftovers = sorted(p.relative_to(app).as_posix() for p in app.rglob("*")) if app.exists() else []
    check("app folder removed", not app.exists(), leftovers[:20])
    check("Start-menu shortcut removed", not shortcut.exists())
    check("uninstall entry removed", not _registry_snapshot()["uninstall"])
    check("npm cache removed", not npm_cache.exists(), npm_cache)
    check("editor cache removed", not editor_cache.exists(), editor_cache)
    check("user project kept", (projects / "offline-game" / "gamestudio.json").is_file())
    after = {name: _snapshot(folder) for name, folder in watched.items()}
    for name in watched:
        check(f"{name} unchanged", before[name] == after[name])
    registry_after = _registry_snapshot()
    check(".blend registration unchanged", {k: v for k, v in registry_before.items() if k != "uninstall"}
          == {k: v for k, v in registry_after.items() if k != "uninstall"})

    failures = [f"{name}: {detail}" for name, ok, detail in checks if not ok]
    result = {"ok": not failures, "failures": failures, "checks": checks, "work": str(work),
              "installed_bytes": footprint, "installer_bytes": installer.stat().st_size,
              "project": report, "play": play_result, "headless": run1}
    log(f"Installer verification: {sum(ok for _n, ok, _d in checks)}/{len(checks)} checks passed")
    if not keep and not failures:
        shutil.rmtree(work, ignore_errors=True)
    return result


def _verify_play(app: pathlib.Path, scene: pathlib.Path, work: pathlib.Path, timeout: int, log) -> dict:
    """tools/e2e/blender_game_view.py on the installed editor, with its UI: docked Play on `scene`."""
    log("Play with the UI: docked Game View on the new project (tools/e2e/blender_game_view.py)")
    result = work / "play.json"
    env = offline_env({"BLENDER_USER_RESOURCES": str(work / "profile-play"), "GS_EDITOR_KIND": "fork"})
    cmd = [str(app / "blender.exe"), "--factory-startup", "--window-geometry", "0", "0", "1600", "900", str(scene),
           "--python", str(REPO / "tools" / "e2e" / "blender_game_view.py"), "--", str(result)]
    try:
        proc = subprocess.run(cmd, env=env, cwd=str(work), capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=timeout)
        output = proc.stdout + proc.stderr
    except subprocess.TimeoutExpired:
        output = "timed out"
    data = json.loads(result.read_text(encoding="utf-8")) if result.is_file() else {}
    data["output"] = output[-4000:]
    return data


# ------------------------------------------------------------------------------------- CLI


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="gsfork_package", description="Package GameStudio Editor for release.")
    parser.add_argument("--out", help="Output folder (default: <repo>/dist)")
    parser.add_argument("--verify", action="store_true", help="Extract the zip and check it headless")
    parser.add_argument("--verify-dir", help="Scratch folder for --verify (default: a temporary folder)")
    parser.add_argument("--no-runtime", action="store_true", help="Leave the runtime packages and examples out")
    parser.add_argument("--no-upstream-source", action="store_true",
                        help="Skip the upstream source archive (development only: not releasable)")
    add_bundle_arguments(parser)
    args = parser.parse_args(argv)
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
    import gsfork  # noqa: E402  (same folder)

    try:
        result = package(gsfork.paths(), gsfork.lock(), pathlib.Path(args.out) if args.out else REPO / "dist",
                         include_runtime=not args.no_runtime, upstream_source=not args.no_upstream_source,
                         verify=args.verify, verify_dir=args.verify_dir, **bundle_options(args))
    except PackageError as exc:
        print(f"[gsfork] package failed: {exc}", file=sys.stderr)
        return 1
    for name, digest in result["sha256"].items():
        print(f"  {digest}  {name}")
    return 0 if verification_ok(result) else 1


def add_bundle_arguments(parser) -> None:
    """The --bundle/--installer options, shared with `gsfork package`."""
    parser.add_argument("--bundle", action="store_true",
                        help="Make the zip self-contained: portable Node.js and the shared packages (Electron too)")
    parser.add_argument("--installer", action="store_true",
                        help="Also build the per-user Windows installer (Inno Setup 6; implies --bundle)")
    parser.add_argument("--node-zip",
                        help=f"Use this Node.js zip instead of downloading it (checked against {NODE_LOCK})")
    parser.add_argument("--downloads", help="Download cache for Node.js (default: ../gs-downloads)")
    parser.add_argument("--iscc", help="Inno Setup's ISCC.exe (default: found in the usual places)")
    parser.add_argument("--verify-play", action="store_true",
                        help="With --verify --installer: also press Play in the installed editor with its UI")


def bundle_options(args) -> dict:
    return {"bundle": args.bundle, "installer": args.installer, "node_zip": args.node_zip,
            "downloads": args.downloads, "iscc": args.iscc, "verify_play": args.verify_play}


def verification_ok(result: dict) -> bool:
    return all(r is None or r["ok"] for r in (result.get("verify"), result.get("installer_verify")))


if __name__ == "__main__":
    sys.exit(main())
