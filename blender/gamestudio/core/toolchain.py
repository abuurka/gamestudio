# SPDX-License-Identifier: GPL-3.0-or-later
"""Node.js, npm and the shared packages for game projects.

Projects use a shared tree of packages through links (directory junctions on Windows, which need
no admin rights): <project>/node_modules/<name> -> the shared package, plus .bin shims. A project
therefore stores no package files, and New Project needs no network once the tree exists. The
tree is one of:

  development checkout    runtime/packages and node_modules at the repository root
  GameStudio extension    the zip (tools/build_extension.py) carries the packages as sources:
                              bundle.json             {"kind": "extension", "runtime": {"hash": ...}}
                              runtime/packages/       @gamestudio/engine, devserver, mcp
                              examples/
                          They are copied to <user dir>/runtime (the extension's user folder),
                          where one `npm install` adds node_modules beside them (shared_runtime_*).
  GameStudio Editor       <install>/gamestudio/ (docs/fork/RELEASING.md section 6): bundle.json,
                          a portable node/, a prebuilt node_modules/ and runtime/packages/

Node.js: the Editor's bundled one; else the system's when it is 22.12 or newer; else the portable
copy GameStudio installs in <user dir>/node (core/nodejs.py).

Standard library only (no bpy, no relative imports), so editor/tests/tools and gsfork_package can
load it on its own.
"""

import hashlib
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import time
from typing import NamedTuple

BUNDLE_DIR = "gamestudio"
BUNDLE_MANIFEST = "bundle.json"
ENV_RUNTIME = "GAMESTUDIO_RUNTIME"
ENV_BROWSER_DIR = "GAMESTUDIO_BROWSER_DIR"  # where @gamestudio/devserver's gamestudio-browser keeps its browser
SCOPE = "@gamestudio/"
MIN_NODE = (22, 12, 0)
PORTABLE_NODE = "node"          # <user dir>/node
SHARED_RUNTIME = "runtime"      # <user dir>/runtime
BROWSER = "browser"             # <user dir>/browser: the game browser GameStudio downloads when there is none
SHARED_STAMP = "gamestudio-runtime.json"
RUNTIME_LOCK = "runtime.lock.json"  # core/: the shared runtime's package-lock.json (tools/runtime_lock.py)
_WINDOWS = sys.platform == "win32"
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

_user_dir: pathlib.Path | None = None

# Defaults for npm children (the user's own npm_config_* values win). The update check would go
# online on every run; the cache moves to a GameStudio folder the uninstaller can remove.
NPM_DEFAULTS = {"npm_config_update_notifier": "false", "npm_config_fund": "false",
                "npm_config_audit": "false"}


class Sdk(NamedTuple):
    packages: pathlib.Path              # @gamestudio/* sources: <root>/runtime/packages
    modules: pathlib.Path | None        # the shared node_modules: <root>/node_modules


# --- Where things are ---------------------------------------------------------------


def install_roots(start=None):
    """Folders that may hold runtime/ and node_modules/: each parent of `start` (default: this
    file, i.e. a development checkout or an install), and <parent>/gamestudio (packaged layout)."""
    for parent in pathlib.Path(start or __file__).resolve().parents:
        yield parent
        yield parent / BUNDLE_DIR


def bundle_root(start=None) -> pathlib.Path | None:
    """The folder with bundle.json: <install>/gamestudio in GameStudio Editor, the extension's
    folder in an installed extension; None in a development checkout."""
    for root in install_roots(start):
        if (root / BUNDLE_MANIFEST).is_file():
            return root
    return None


def bundle_manifest(start=None) -> dict:
    root = bundle_root(start)
    if root is None:
        return {}
    try:
        return json.loads((root / BUNDLE_MANIFEST).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def is_extension_bundle(start=None) -> bool:
    """True in an installed GameStudio extension (packages as sources, no node_modules)."""
    return bundle_manifest(start).get("kind") == "extension"


def runtime_roots(start=None) -> list[pathlib.Path]:
    """Extra Node-style search roots for the runtime host and Electron (the bundle, if any)."""
    root = bundle_root(start)
    return [root] if root is not None else []


def set_user_dir(path) -> None:
    """The extension's user folder (bpy.utils.extension_path_user): portable Node, shared runtime."""
    global _user_dir
    _user_dir = pathlib.Path(path) if path else None
    _node_cache.clear()


def user_dir() -> pathlib.Path | None:
    return _user_dir


def shared_runtime_dir() -> pathlib.Path | None:
    return _user_dir / SHARED_RUNTIME if _user_dir is not None else None


def browser_dir() -> pathlib.Path | None:
    """<user dir>/browser: Chrome Headless Shell, when the computer has no Edge, Chrome or Chromium
    (GAMESTUDIO_BROWSER_DIR overrides it, as for the Node.js side)."""
    env = os.environ.get(ENV_BROWSER_DIR, "").strip()
    if env:
        return pathlib.Path(env)
    return _user_dir / BROWSER if _user_dir is not None else None


BROWSER_MARKER = "browser.json"  # written by gamestudio-browser once the browser ran


def downloaded_browser(folder=None) -> str | None:
    """The executable of the browser @gamestudio/devserver's gamestudio-browser downloaded, or None:
    <folder>/browser.json names it relative to the folder (forward slashes), and it has to exist."""
    folder = pathlib.Path(folder) if folder is not None else browser_dir()
    if folder is None:
        return None
    relative = _read_json(folder / BROWSER_MARKER).get("executable")
    if not isinstance(relative, str) or not relative:
        return None
    path = folder / pathlib.PurePosixPath(relative)
    return str(path) if path.is_file() else None


def sdk(start=None) -> Sdk | None:
    """The engine packages and the shared node_modules beside them: GAMESTUDIO_RUNTIME first.

    In an installed extension this is the copy in the user folder, which may not be set up yet
    (shared_runtime_ready); its packages folder is where projects link either way."""
    candidates = []
    env = os.environ.get(ENV_RUNTIME, "").strip()
    if env:
        candidates.append(pathlib.Path(env))
    elif is_extension_bundle(start) and shared_runtime_dir() is not None:
        home = shared_runtime_dir()
        modules = home / "node_modules"
        return Sdk(home / "packages", modules if (modules / "vite" / "package.json").is_file() else None)
    candidates += [root / "runtime" / "packages" for root in install_roots(start)]
    for packages in candidates:
        if (packages / "engine" / "package.json").is_file():
            modules = packages.parent.parent / "node_modules"
            return Sdk(packages, modules if (modules / "vite" / "package.json").is_file() else None)
    return None


def bundled_node_dir(start=None) -> pathlib.Path | None:
    """GameStudio Editor's bundled Node.js folder, or None."""
    root = bundle_root(start)
    if root is None:
        return None
    folder = root / "node"
    return folder if _node_exe(folder).is_file() else None


def portable_node_dir() -> pathlib.Path | None:
    """The portable Node.js GameStudio installed in the user folder, or None."""
    if _user_dir is None:
        return None
    folder = _user_dir / PORTABLE_NODE
    return folder if _node_exe(folder).is_file() else None


def node_dir(start=None) -> pathlib.Path | None:
    """The Node.js folder GameStudio puts first on PATH for its children, or None for the
    system's: the bundled one, else the portable one when the system has no Node.js 22.12+."""
    folder = bundled_node_dir(start)
    if folder is not None:
        return folder
    portable = portable_node_dir()
    if portable is None or system_node() is not None:
        return None
    return portable


def _node_exe(folder: pathlib.Path) -> pathlib.Path:
    return folder / "node.exe" if _WINDOWS else folder / "bin" / "node"


def _npm_cli(folder: pathlib.Path) -> pathlib.Path | None:
    for rel in ("node_modules/npm/bin/npm-cli.js", "lib/node_modules/npm/bin/npm-cli.js"):
        if (folder / rel).is_file():
            return folder / rel
    return None


def which(name: str, path: str | None = None) -> str | None:
    """shutil.which without the current folder. Windows looks there first, and a Blender opened by
    double-clicking a .blend has the .blend's folder as its current folder: a node.exe or npm.cmd left
    next to it would run. Relative folders on PATH are skipped too."""
    if not _WINDOWS or os.path.dirname(name):
        return shutil.which(name, path=path)
    for folder in (os.environ.get("PATH", "") if path is None else path).split(os.pathsep):
        folder = folder.strip().strip('"')
        if folder and os.path.isabs(folder):
            found = shutil.which(os.path.join(folder, name))  # a folder given: shutil.which looks only there
            if found:
                return found
    return None


def system_tool(name: str, env: dict | None = None) -> str:
    """A Windows program by its full path in System32 (taskkill.exe, cmd.exe): started by its bare name,
    Windows would look in the current folder first."""
    import ntpath

    env = os.environ if env is None else env
    system_root = next((v for k, v in env.items() if k.upper() == "SYSTEMROOT" and v), "C:\\Windows")
    return ntpath.join(system_root, "System32", name)


def node_executable(start=None) -> str | None:
    folder = node_dir(start)
    if folder is not None:
        return str(_node_exe(folder))
    return system_node() or which("node")


def node_bin_dir(start=None) -> pathlib.Path | None:
    """The folder of the Node.js GameStudio runs (bundled, portable or the system's): first on the
    PATH of every child, so `#!/usr/bin/env node` scripts (npm's, a custom npm command's) find that
    same Node.js, also where Blender's own PATH has none (started from the Finder or the Dock)."""
    folder = node_dir(start)
    if folder is not None:
        return folder if _WINDOWS else folder / "bin"
    system = system_node()
    return pathlib.Path(system).parent if system else None


# --- Node.js versions -----------------------------------------------------------------

_NODE_TTL = 30.0
_node_cache: dict = {}


def parse_version(text: str) -> tuple[int, int, int] | None:
    return _parse(text) if re.match(r"^\s*v?\d", text or "") else None


def node_version(executable) -> tuple[int, int, int] | None:
    """`node --version` of an executable, or None if it does not run."""
    try:
        out = subprocess.run([str(executable), "--version"], capture_output=True, text=True, timeout=15,
                             stdin=subprocess.DEVNULL, creationflags=_NO_WINDOW)
    except (OSError, subprocess.SubprocessError):
        return None
    return parse_version(out.stdout.strip()) if out.returncode == 0 else None


def _own_folder(path: str) -> bool:
    """True for a node executable in the portable Node's folder (activate() may put it on PATH)."""
    folder = portable_node_dir()
    if folder is None:
        return False
    here = os.path.normcase(os.path.dirname(os.path.abspath(path)))
    return here in (os.path.normcase(str(folder)), os.path.normcase(str(folder / "bin")))


def node_search_dirs(env: dict | None = None, home=None, windows: bool | None = None) -> list[pathlib.Path]:
    """Where a Node.js the user installed may be when PATH does not show it: a Blender started from the
    Finder or the Dock has PATH=/usr/bin:/bin:/usr/sbin:/sbin. Homebrew (Apple Silicon, Intel), the
    official installer and MacPorts, Volta, asdf, nvm and fnm (newest version first); on Windows the
    official installer's folder and nvm-windows' link."""
    env = os.environ if env is None else env
    home = pathlib.Path(home) if home is not None else pathlib.Path.home()
    windows = _WINDOWS if windows is None else windows
    dirs = []
    if windows:
        for name in ("ProgramFiles", "ProgramW6432"):
            if env.get(name):
                dirs.append(pathlib.Path(env[name]) / "nodejs")
        if env.get("NVM_SYMLINK"):
            dirs.append(pathlib.Path(env["NVM_SYMLINK"]))
        return dirs
    dirs += [pathlib.Path("/opt/homebrew/bin"), pathlib.Path("/usr/local/bin"), pathlib.Path("/opt/local/bin"),
             home / ".volta" / "bin", home / ".asdf" / "shims"]
    managed = [(pathlib.Path(env.get("NVM_DIR") or home / ".nvm") / "versions" / "node", "bin"),
               (home / ".local" / "share" / "fnm" / "node-versions", "installation/bin"),
               (home / "Library" / "Application Support" / "fnm" / "node-versions", "installation/bin")]
    for versions, rel in managed:
        try:
            found = [(parse_version(p.name), p) for p in versions.iterdir() if p.is_dir()]
        except OSError:
            continue
        for _version, folder in sorted(((v, p) for v, p in found if v), reverse=True):
            dirs.append(folder / rel)
    return dirs


_version_cache: dict = {}  # (executable, mtime) -> its version: draw code asks, and `node --version` takes a while


def _cached_version(path: str):
    try:
        key = (path, os.stat(path).st_mtime)
    except OSError:
        return None
    if key not in _version_cache:
        _version_cache[key] = node_version(path)
    return _version_cache[key]


def _system_node_info() -> tuple[str, tuple] | tuple[None, None]:
    """(path, version) of the system's Node.js that is not GameStudio's own copy: `node` on PATH, or,
    when that is missing or too old, the first Node.js 22.12+ in node_search_dirs(). Cached briefly,
    as draw code asks."""
    key = os.environ.get("PATH", "")
    cached = _node_cache.get("system")
    if cached is not None and cached[0] == key and time.monotonic() - cached[1] < _NODE_TTL:
        return cached[2]
    found = which("node")
    if found is not None and _own_folder(found):
        rest = os.pathsep.join(p for p in key.split(os.pathsep)
                               if p and not _own_folder(os.path.join(p, "node.exe" if _WINDOWS else "node")))
        found = which("node", path=rest)
    info = (found, _cached_version(found)) if found else (None, None)
    if not (info[1] and info[1] >= MIN_NODE):
        for folder in node_search_dirs():
            candidate = folder / ("node.exe" if _WINDOWS else "node")
            if candidate.is_file() and not _own_folder(str(candidate)):
                version = _cached_version(str(candidate))
                if version and version >= MIN_NODE:
                    info = (str(candidate), version)
                    break
    _node_cache["system"] = (key, time.monotonic(), info)
    return info


def system_node() -> str | None:
    """The system's Node.js when it is MIN_NODE or newer, else None."""
    path, version = _system_node_info()
    return path if path and version and version >= MIN_NODE else None


def node_status(start=None) -> dict:
    """Which Node.js game projects use: {"source": "bundled" | "system" | "portable" | None,
    "path", "version" ("24.21.0" or ""), "ok", "problem" (why it cannot be used, or "")}."""
    folder = bundled_node_dir(start)
    if folder is not None:
        return {"source": "bundled", "path": str(_node_exe(folder)), "version": "", "ok": True, "problem": ""}
    path, version = _system_node_info()
    text = ".".join(map(str, version)) if version else ""
    if path and version and version >= MIN_NODE:
        return {"source": "system", "path": path, "version": text, "ok": True, "problem": ""}
    portable = portable_node_dir()
    if portable is not None:
        cached = _node_cache.get("portable")
        if cached is None or cached[0] != str(portable):
            cached = (str(portable), node_version(_node_exe(portable)))
            _node_cache["portable"] = cached
        if cached[1]:
            return {"source": "portable", "path": str(_node_exe(portable)), "ok": True, "problem": "",
                    "version": ".".join(map(str, cached[1]))}
    minimum = ".".join(map(str, MIN_NODE))
    if path and version:
        problem = f"Node.js {text} is too old; GameStudio needs {minimum} or newer"
    elif path:
        problem = f"Could not run {path}"
    else:
        problem = f"Node.js {minimum} or newer was not found"
    return {"source": None, "path": path or "", "version": text, "ok": False, "problem": problem}


def forget_node() -> None:
    """Look Node.js up again (after installing one)."""
    _node_cache.clear()


def npm_argv(command: str = "npm", start=None) -> list[str] | None:
    """The command line that runs npm, or None.

    A non-default `command` (the add-on preference) is used as given. Otherwise the bundled npm
    runs through the bundled node.exe directly (no npm.cmd, which may switch to a global npm),
    then the system's npm.
    """
    command = (command or "npm").strip()
    if command != "npm":
        found = which(command)
        return [found] if found else None
    folder = node_dir(start)
    if folder is not None:
        cli = _npm_cli(folder)
        if cli is not None:
            return [str(_node_exe(folder)), str(cli)]
    system = system_node()
    if system is not None:
        # The system's npm through its own node: no npm.cmd (cmd.exe parsing) and no `#!/usr/bin/env node`
        # script, which needs node on the PATH of the child (a Blender started from the Dock has a short PATH).
        cli = system_npm_cli(system)
        if cli is not None:
            return [system, str(cli)]
    found = which("npm")
    return [found] if found else None


def system_npm_cli(node: str) -> pathlib.Path | None:
    """npm-cli.js of the npm that comes with a system Node.js: <dir>/node_modules/npm (Windows), or
    <prefix>/lib/node_modules/npm next to <prefix>/bin/node (macOS, Linux; also through the symlinks
    of Homebrew and the official installer)."""
    try:
        real = pathlib.Path(os.path.realpath(node))
    except (OSError, ValueError):
        return None
    for folder in (real.parent, real.parent.parent):
        cli = _npm_cli(folder)
        if cli is not None:
            return cli
    return None


NPM_CACHE = "npm-cache"  # <user dir>/npm-cache: npm's cache for GameStudio's setup (prune_npm_cache)


def npm_cache_dir() -> pathlib.Path:
    """GameStudio Editor's npm cache (where its uninstaller finds it)."""
    if _WINDOWS:
        base = os.environ.get("LOCALAPPDATA") or str(pathlib.Path.home() / "AppData" / "Local")
        return pathlib.Path(base) / "GameStudio" / "npm-cache"
    return pathlib.Path.home() / ".cache" / "gamestudio" / "npm-cache"


def _env_get(env: dict, name: str) -> str | None:
    for key, value in env.items():
        if key.upper() == name.upper():
            return value
    return None


def _env_set(env: dict, name: str, value: str) -> None:
    for key in [k for k in env if k.upper() == name.upper()]:
        del env[key]
    env[name] = value


def child_env(base: dict | None = None, start=None) -> dict:
    """Environment for node/npm children: GameStudio's Node first on PATH, quiet npm defaults."""
    env = dict(os.environ if base is None else base)
    folder = node_dir(start)
    bin_dir = node_bin_dir(start)
    if bin_dir is not None:
        own = {os.path.normcase(os.path.normpath(str(p))) for p in (folder, bin_dir) if p is not None}
        parts = [p for p in (_env_get(env, "PATH") or "").split(os.pathsep)
                 if p and os.path.normcase(os.path.normpath(p)) not in own]
        _env_set(env, "PATH", os.pathsep.join([str(bin_dir), *parts]))
    # npm's cache: GameStudio Editor's where its uninstaller finds it; the extension's in its user
    # folder (prune_npm_cache empties it after the setup), never the user's ~/.npm, which may hold
    # files an old `sudo npm` left (EACCES). The user's own npm_config_cache wins.
    if _env_get(env, "npm_config_cache") is None:
        if folder is not None and folder == bundled_node_dir(start):
            env["npm_config_cache"] = str(npm_cache_dir())
        elif _user_dir is not None:
            env["npm_config_cache"] = str(_user_dir / NPM_CACHE)
    for name, value in NPM_DEFAULTS.items():
        if _env_get(env, name) is None:
            env[name] = value
    # The stream host, the MCP server's headless game and the playtest scripts look for the browser
    # GameStudio downloaded there (after Edge, Chrome and Chromium).
    if _user_dir is not None and _env_get(env, ENV_BROWSER_DIR) is None:
        env[ENV_BROWSER_DIR] = str(_user_dir / BROWSER)
    return env


def prune_npm_cache() -> bool:
    """Empty <user dir>/npm-cache after the setup's installs (about 180 MB GameStudio does not need
    again), unless the user chose a cache of their own. True when something was removed."""
    if _user_dir is None:
        return False
    cache = _user_dir / NPM_CACHE
    chosen = _env_get(dict(os.environ), "npm_config_cache")  # activate() puts ours here too
    if chosen and os.path.normcase(os.path.abspath(chosen)) != os.path.normcase(os.path.abspath(cache)):
        return False
    if not cache.exists():
        return False
    shutil.rmtree(cache, ignore_errors=True)
    return True


def activate(start=None) -> pathlib.Path | None:
    """Put GameStudio's Node (bundled, or the portable one without a system Node.js) first on this
    process's PATH, so every child (and shutil.which) finds it. Returns that folder or None."""
    folder = node_dir(start)
    if folder is not None:
        env = child_env(dict(os.environ), start)
        for name in ("PATH", "npm_config_cache", *NPM_DEFAULTS):
            value = _env_get(env, name)
            if value is not None:
                os.environ[name] = value
    return folder


# --- Linking a project to the shared packages --------------------------------------------


def read_manifest(root) -> dict:
    try:
        return json.loads((pathlib.Path(root) / "package.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


# npm's package names: an optional @scope/, then a name; no "..", no path, no backslash or drive letter. Each
# name becomes a folder under node_modules (links, .bin shims): one that is not could reach outside it.
PACKAGE_NAME = re.compile(r"(?:@[A-Za-z0-9~-][A-Za-z0-9._~-]*/)?[A-Za-z0-9~-][A-Za-z0-9._~-]*")
_skipped_names: set = set()


def valid_package_name(name) -> bool:
    return isinstance(name, str) and len(name) <= 214 and PACKAGE_NAME.fullmatch(name) is not None


def declared_dependencies(manifest: dict) -> dict:
    """The manifest's dependencies (dev, optional and regular) whose names npm allows; the others are
    skipped, and logged once."""
    deps = {}
    for field in ("devDependencies", "optionalDependencies", "dependencies"):
        value = manifest.get(field) if isinstance(manifest, dict) else None
        deps.update(value if isinstance(value, dict) else {})
    for name in [name for name in deps if not valid_package_name(name)]:
        del deps[name]
        if name not in _skipped_names:
            import logging  # a child of "gamestudio" (core/log.py); this module also loads on its own

            _skipped_names.add(name)
            logging.getLogger("gamestudio.core.toolchain").warning(
                "Skipped the dependency %r of a package.json: not a package name npm allows", name)
    return deps


def third_party_dependencies(manifest: dict) -> dict:
    """The dependencies of a project template's manifest that are not GameStudio's own: what the
    shared runtime installs for it (environment.template_dependencies, tools/runtime_lock.py)."""
    return {name: spec for name, spec in declared_dependencies(manifest).items() if not name.startswith(SCOPE)}


def resolvable(root, name: str) -> bool:
    """Node's lookup: <dir>/node_modules/<name> in the project folder or any parent."""
    for folder in (pathlib.Path(root), *pathlib.Path(root).parents):
        if (folder / "node_modules" / name / "package.json").is_file():
            return True
    return False


def missing_dependencies(root) -> list[str]:
    """Declared dependencies of the project that Node cannot resolve (sorted)."""
    return sorted(name for name in declared_dependencies(read_manifest(root)) if not resolvable(root, name))


def shared_package(name: str, shared: Sdk) -> pathlib.Path | None:
    if name.startswith(SCOPE):
        folder = shared.packages / name[len(SCOPE):]
    elif shared.modules is not None:
        folder = shared.modules / name
    else:
        return None
    return folder if (folder / "package.json").is_file() else None


def _version(folder: pathlib.Path) -> str:
    return read_manifest(folder).get("version", "")


def _parse(version: str) -> tuple[int, int, int] | None:
    match = re.match(r"^\s*v?(\d+)(?:\.(\d+))?(?:\.(\d+))?", version or "")
    if not match:
        return None
    return tuple(int(part or 0) for part in match.groups())


def satisfies(version: str, spec: str) -> bool:
    """Enough of npm's semver ranges for project manifests: exact, ^, ~, >=, *, x and file:."""
    spec = (spec or "").strip()
    if spec in ("", "*", "latest", "x") or spec.startswith(("file:", "link:", "workspace:")):
        return True
    if spec.lstrip("=v") == version:
        return True
    have = _parse(version)
    if have is None or "-" in version:
        return False
    if spec.startswith(">="):
        want = _parse(spec[2:])
        return want is not None and have >= want
    if spec[0] in "^~":
        want = _parse(spec[1:])
        if want is None or have < want:
            return False
        if spec[0] == "~":
            return have[:2] == want[:2]
        if want[0] > 0:
            return have[0] == want[0]
        return have[:2] == want[:2] if want[1] > 0 else have == want
    match = re.fullmatch(r"=?v?(\d+)(?:\.(\d+|[xX*]))?(?:\.(\d+|[xX*]))?", spec)
    if not match:
        return False  # other range forms: leave them to npm
    fixed = []
    for part in match.groups():
        if part is None or not part.isdigit():
            break
        fixed.append(int(part))
    return list(have[:len(fixed)]) == fixed


def is_link(path) -> bool:
    path = str(path)
    return os.path.islink(path) or (hasattr(os.path, "isjunction") and os.path.isjunction(path))


def _remove_link(path: pathlib.Path) -> None:
    if _WINDOWS:
        os.rmdir(path)  # removes a junction or directory symlink, never its target
    else:
        os.unlink(path)


def make_link(target: pathlib.Path, link: pathlib.Path) -> None:
    """A directory link that needs no admin rights: a junction on Windows, a symlink elsewhere."""
    link.parent.mkdir(parents=True, exist_ok=True)
    if _WINDOWS:
        import _winapi

        _winapi.CreateJunction(str(pathlib.Path(target).resolve()), str(link))
    else:
        os.symlink(pathlib.Path(target).resolve(), link, target_is_directory=True)


def package_bins(folder: pathlib.Path) -> dict[str, str]:
    """{command: script path relative to the package}, from package.json "bin"."""
    manifest = read_manifest(folder)
    value = manifest.get("bin")
    if isinstance(value, str):
        return {manifest.get("name", folder.name).rsplit("/", 1)[-1]: value}
    if isinstance(value, dict):
        return {name: path for name, path in value.items() if isinstance(path, str)}
    return {}


CMD_SHIM = """@ECHO off\r
GOTO start\r
:find_dp0\r
SET dp0=%~dp0\r
EXIT /b\r
:start\r
SETLOCAL\r
CALL :find_dp0\r
\r
IF EXIST "%dp0%\\node.exe" (\r
  SET "_prog=%dp0%\\node.exe"\r
) ELSE (\r
  SET "_prog=node"\r
  SET PATHEXT=%PATHEXT:;.JS;=;%\r
)\r
\r
endLocal & goto #_undefined_# 2>NUL || title %COMSPEC% & "%_prog%"  "%dp0%\\{target}" %*\r
"""

SH_SHIM = """#!/bin/sh
basedir=$(dirname "$(echo "$0" | sed -e 's,\\\\,/,g')")

case `uname` in
    *CYGWIN*|*MINGW*|*MSYS*)
        if command -v cygpath > /dev/null 2>&1; then
            basedir=`cygpath -w "$basedir"`
        fi
    ;;
esac

if [ -x "$basedir/node" ]; then
  exec "$basedir/node"  "$basedir/{target}" "$@"
else
  exec node  "$basedir/{target}" "$@"
fi
"""


def write_bin_shims(bin_dir: pathlib.Path, command: str, target: str) -> list[pathlib.Path]:
    """npm-style launchers for a package script; `target` is relative to bin_dir (posix)."""
    bin_dir.mkdir(parents=True, exist_ok=True)
    sh = bin_dir / command
    _unlink_entry(sh)
    sh.write_bytes(SH_SHIM.replace("{target}", target).encode("utf-8"))
    if not _WINDOWS:
        sh.chmod(0o755)
    written = [sh]
    if _WINDOWS:
        cmd = bin_dir / f"{command}.cmd"
        _unlink_entry(cmd)
        cmd.write_bytes(CMD_SHIM.replace("{target}", target.replace("/", "\\")).encode("utf-8"))
        written.append(cmd)
    return written


def _unlink_entry(path: pathlib.Path) -> None:
    """Remove a .bin entry before writing ours: npm makes them symlinks (macOS, Linux) to the
    package's script, and writing through one would overwrite that script, in the shared packages."""
    if os.path.islink(path) or path.is_file():
        path.unlink()


def _link_target(name: str, spec: str, shared: Sdk | None) -> pathlib.Path | None:
    target = shared_package(name, shared) if shared is not None else None
    if target is None or (not name.startswith(SCOPE) and not satisfies(_version(target), spec)):
        return None
    return target


def unlinkable_dependencies(root, shared: Sdk | None = None) -> list[str]:
    """Missing dependencies the shared tree cannot provide: what `npm install` has to download."""
    declared = declared_dependencies(read_manifest(root))
    return [name for name in missing_dependencies(root)
            if _link_target(name, str(declared.get(name, "")), shared) is None]


def install_root(path) -> pathlib.Path | None:
    """The GameStudio install a package folder belongs to: the nearest folder at or above it with
    bundle.json (an installed extension, GameStudio Editor) or gamestudio-runtime.json (an
    extension's shared runtime). None for anything else, such as a development checkout."""
    path = pathlib.Path(path)
    for folder in (path, *path.parents):
        if (folder / BUNDLE_MANIFEST).is_file() or (folder / SHARED_STAMP).is_file():
            return folder
    return None


def _real(path) -> str:
    try:
        return os.path.normcase(os.path.realpath(path))
    except (OSError, ValueError):
        return os.path.normcase(os.path.abspath(path))


def _same_install(a: pathlib.Path | None, b: pathlib.Path | None) -> bool:
    return a is not None and b is not None and _real(a) == _real(b)


def foreign_links(root, shared: Sdk | None = None) -> list[str]:
    """The project's linked dependencies that lead into ANOTHER GameStudio install (an older portable
    install, another Blender version's shared runtime, ...) while this one has the package: they get
    linked here again. A link into a development checkout (no install marker) is the developer's and
    is left alone, and so are folders npm installed. Sorted names."""
    root = pathlib.Path(root)
    shared = shared if shared is not None else sdk()
    if shared is None:
        return []
    ours = install_root(shared.packages)
    found = []
    modules = root / "node_modules"
    for name, spec in declared_dependencies(read_manifest(root)).items():
        link = modules / name
        if not is_link(link) or not (link / "package.json").is_file():
            continue  # not a link (npm's own install), or a dead one (missing_dependencies has it)
        theirs = install_root(os.path.realpath(link))
        if theirs is None or _same_install(theirs, ours):
            continue
        target = _link_target(name, str(spec), shared)
        if target is not None and _real(target) != _real(link):
            found.append(name)
    return sorted(found)


def repoint_specs(root, shared: Sdk | None = None) -> list[str]:
    """Point the `file:` specs of @gamestudio packages in the project's package.json at this
    GameStudio's packages where they lead nowhere (the project came from another computer, or
    GameStudio moved) or into another GameStudio install (bundle.json or a shared runtime's marker
    there). Specs into a development checkout are left alone. Returns the names changed."""
    root = pathlib.Path(root)
    shared = shared if shared is not None else sdk()
    path = root / "package.json"
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if shared is None or not isinstance(manifest, dict):
        return []
    ours = install_root(shared.packages)
    changed = []
    for field in ("dependencies", "devDependencies"):
        for name, spec in list((manifest.get(field) or {}).items()):
            if not (name.startswith(SCOPE) and valid_package_name(name) and isinstance(spec, str)
                    and spec.startswith("file:")):
                continue
            current = root / spec[len("file:"):]  # an absolute spec replaces root
            wanted = shared.packages / name[len(SCOPE):]
            if _real(current) == _real(wanted):
                continue
            if (current / "package.json").is_file():
                theirs = install_root(current)
                if theirs is None or _same_install(theirs, ours) or not (wanted / "package.json").is_file():
                    continue
            manifest[field][name] = "file:" + wanted.as_posix()
            changed.append(name)
    if changed:
        path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8", newline="\n")
    return changed


def link_project(root, shared: Sdk | None = None, relink=()) -> dict:
    """Link the project's unresolvable dependencies to the shared packages, offline.

    Never touches a real folder in node_modules (npm's own installs win), only adds links,
    replaces links whose target is gone (e.g. GameStudio moved) and the links named in `relink`
    (foreign_links). Returns {"linked": [names], "unavailable": [names the shared tree lacks or has
    at a version the manifest rejects]}.
    """
    root = pathlib.Path(root)
    shared = shared if shared is not None else sdk()
    declared = declared_dependencies(read_manifest(root))
    linked, unavailable = [], []
    modules = root / "node_modules"
    names = missing_dependencies(root)
    names += [name for name in relink if name not in names and is_link(modules / name)]
    for name in names:
        target = _link_target(name, str(declared.get(name, "")), shared)
        if target is None:
            unavailable.append(name)
            continue
        link = modules / name
        if os.path.lexists(link):
            if not is_link(link):
                unavailable.append(name)  # a real (broken) install: leave it to npm
                continue
            _remove_link(link)
        make_link(target, link)
        for command, script in package_bins(target).items():
            write_bin_shims(modules / ".bin", command, f"../{name}/{pathlib.PurePosixPath(script)}")
        linked.append(name)
    return {"linked": linked, "unavailable": unavailable}


# --- The extension's shared runtime --------------------------------------------------------
#
# <user dir>/runtime holds a copy of the bundled packages and a package.json that depends on them
# (file:packages/<name>) and on the third-party packages new projects use. One `npm install` there
# fills node_modules, so the packages' imports resolve from where projects link them, and
# link_project() links three, vite, typescript and the rest too. A copy, not the bundle itself:
# the extension's folder is replaced on every update and may not be writable.
#
# The bundle's core/runtime.lock.json goes beside that package.json as its package-lock.json, so
# npm installs the very versions it lists, the indirect ones too (tools/runtime_lock.py makes it).
# The stamp records which lock the install used: a different one is an update that needs npm.


def bundled_packages(start=None) -> pathlib.Path | None:
    root = bundle_root(start)
    folder = root / "runtime" / "packages" if root is not None else None
    return folder if folder is not None and folder.is_dir() else None


def bundled_lock(start=None) -> pathlib.Path | None:
    """The bundle's core/runtime.lock.json, or None (a bundle without one)."""
    root = bundle_root(start)
    path = root / "core" / RUNTIME_LOCK if root is not None else None
    return path if path is not None and path.is_file() else None


_lock_hashes: dict = {}  # (path, mtime, size) -> its hash: the readiness check asks often


def runtime_lock_hash(start=None) -> str:
    """A hash of the bundle's lock (line endings aside), or "" without one."""
    path = bundled_lock(start)
    if path is None:
        return ""
    try:
        info = path.stat()
        key = (str(path), info.st_mtime_ns, info.st_size)
        if key not in _lock_hashes:
            _lock_hashes[key] = hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()
    except OSError:
        return ""
    return _lock_hashes[key]


def package_names(packages) -> list[str]:
    packages = pathlib.Path(packages)
    try:
        return sorted(p.name for p in packages.iterdir() if (p / "package.json").is_file())
    except OSError:
        return []


def _read_json(path: pathlib.Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def shared_runtime_manifest(packages, extra_dependencies: dict | None = None) -> dict:
    deps = {f"{SCOPE}{name}": f"file:packages/{name}" for name in package_names(packages)}
    for name, spec in sorted((extra_dependencies or {}).items()):
        if not name.startswith(SCOPE):
            deps.setdefault(name, spec)
    return {"name": "gamestudio-shared-runtime", "version": "0.0.0", "private": True,
            "description": "Packages GameStudio game projects link to. Generated by GameStudio; do not edit.",
            "dependencies": deps}


def runtime_hash(packages) -> str:
    """A hash of the package sources (relative paths and contents), as bundle.json records it."""
    packages = pathlib.Path(packages)
    digest = hashlib.sha256()
    for path in sorted(p for p in packages.rglob("*") if p.is_file() and "node_modules" not in p.parts):
        digest.update(path.relative_to(packages).as_posix().encode("utf-8") + b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def _bundle_runtime_hash(start=None) -> str:
    return str((bundle_manifest(start).get("runtime") or {}).get("hash", ""))


def resolve_package(start, name: str) -> pathlib.Path | None:
    """Where Node finds `name` from a file in `start`: <dir>/node_modules/<name> in start or a parent."""
    start = pathlib.Path(start)
    for folder in (start, *start.parents):
        candidate = folder / "node_modules" / name
        if (candidate / "package.json").is_file():
            return candidate
    return None


_SIMPLE_SPEC = re.compile(r"\s*(?:[\^~]|>=)?\s*=?\s*v?\d+(?:\.(?:\d+|[xX*]))?(?:\.(?:\d+|[xX*]))?\s*")


def spec_satisfied(folder: pathlib.Path, spec) -> bool:
    """The package in `folder` is a version `spec` accepts. A range satisfies() cannot read counts as
    met (npm decides that when it installs): only a version GameStudio can tell is wrong asks for a
    download."""
    spec = str(spec or "").strip()
    if not _SIMPLE_SPEC.fullmatch(spec):
        return True
    return satisfies(_version(folder), spec)


def missing_runtime_dependencies(extra_dependencies: dict | None = None, start=None) -> list[str]:
    """The third-party packages the shared runtime lacks, or has at a version the bundle rejects: what
    `npm install` there has to download. Those projects link (the template's: three, vite, ...) and
    those the bundled @gamestudio packages import themselves (puppeteer-core, ws, ...), checked from
    where the packages are, as Node looks them up. [] outside an installed extension."""
    home = shared_runtime_dir()
    source = bundled_packages(start)
    if not is_extension_bundle(start) or home is None or source is None:
        return []
    missing = set()
    for name, spec in (extra_dependencies or {}).items():
        if name.startswith(SCOPE):
            continue
        folder = home / "node_modules" / name  # projects link this very folder (shared_package)
        if not (folder / "package.json").is_file() or not spec_satisfied(folder, spec):
            missing.add(name)
    for package in package_names(source):
        manifest = read_manifest(source / package)
        optional = {name for name, meta in (manifest.get("peerDependenciesMeta") or {}).items()
                    if isinstance(meta, dict) and meta.get("optional")}
        wanted = {**{n: v for n, v in (manifest.get("peerDependencies") or {}).items() if n not in optional},
                  **(manifest.get("dependencies") or {})}
        for name, spec in wanted.items():
            if name.startswith(SCOPE):
                continue  # GameStudio's own: link_runtime_packages()
            found = resolve_package(home / "packages" / package, name)
            if found is None or not spec_satisfied(found, spec):
                missing.add(name)
    return sorted(missing)


def shared_runtime_state(extra_dependencies: dict | None = None, start=None) -> str:
    """What the shared runtime needs before projects can use it:
      ""         nothing (or this is no installed extension)
      "refresh"  the bundle changed (an update) or its links are missing: copy its packages from the
                 extension, offline; everything they need is installed already
      "install"  `npm install` has to download packages (the first time, an update needs more, or
                 its lock differs from the one the last install used)
    """
    if not is_extension_bundle(start):
        return ""
    home = shared_runtime_dir()
    if home is None:
        return "install"
    stamp = _read_json(home / SHARED_STAMP)
    if (not stamp or missing_runtime_dependencies(extra_dependencies, start)
            or stamp.get("lock", "") != runtime_lock_hash(start)):
        return "install"
    source = bundled_packages(start)
    names = package_names(source) if source is not None else []
    if (not stamp.get("installed") or stamp.get("hash") != _bundle_runtime_hash(start)
            or package_names(home / "packages") != names
            or any(not (home / "node_modules" / f"{SCOPE}{name}" / "package.json").is_file() for name in names)):
        return "refresh"
    return ""


def shared_runtime_ready(start=None, extra_dependencies: dict | None = None) -> bool:
    """True when projects can link everything from the shared runtime: always outside an installed
    extension; there, once its packages match the bundle and every package they and the projects
    need is installed (puppeteer-core included)."""
    if not is_extension_bundle(start):
        return True
    if shared_runtime_dir() is None:
        return False
    if extra_dependencies is None:
        manifest = read_manifest(shared_runtime_dir())
        extra_dependencies = {name: spec for name, spec in (manifest.get("dependencies") or {}).items()
                              if not name.startswith(SCOPE)}
    return shared_runtime_state(extra_dependencies, start) == ""


def _remove_tree(path: pathlib.Path) -> None:
    def retry_writable(func, target, _exc):
        os.chmod(target, 0o700)
        func(target)

    if is_link(path):
        _remove_link(path)
    elif path.exists():
        shutil.rmtree(path, onexc=retry_writable) if sys.version_info >= (3, 12) else shutil.rmtree(path)


def link_runtime_packages(home: pathlib.Path) -> list[str]:
    """<home>/node_modules/@gamestudio/<name> -> <home>/packages/<name>, as `npm install` makes them
    (file: dependencies), for each package; stale ones removed. Offline. Returns the names linked."""
    modules = home / "node_modules" / SCOPE.rstrip("/")
    names = package_names(home / "packages")
    linked = []
    for name in names:
        link = modules / name
        target = home / "packages" / name
        if (link / "package.json").is_file() and _real(link) == _real(target):
            continue
        if os.path.lexists(link):
            _remove_tree(link)
        make_link(target, link)
        linked.append(name)
    if modules.is_dir():
        for stale in [p for p in modules.iterdir() if p.name not in names and is_link(p)]:
            _remove_link(stale)
    return linked


def sync_shared_runtime(extra_dependencies: dict | None = None, start=None) -> bool:
    """Bring <user dir>/runtime up to date with the bundle: copy the packages when they changed (an
    update keeps the node_modules npm put inside them), write package.json and the bundle's lock
    (package-lock.json) and link the packages into its node_modules. Returns True when `npm install`
    has to run there because packages are missing (missing_runtime_dependencies) or the lock is not
    the one the last install used; either way call mark_shared_runtime_installed() once they are
    all there. Raises OSError."""
    source = bundled_packages(start)
    home = shared_runtime_dir()
    if source is None or home is None:
        raise OSError("This GameStudio extension has no bundled runtime packages")
    wanted = _bundle_runtime_hash(start)
    stamp = _read_json(home / SHARED_STAMP)
    target = home / "packages"
    names = package_names(source)
    if stamp.get("hash") != wanted or package_names(target) != names:
        target.mkdir(parents=True, exist_ok=True)
        for name in names:
            fresh = target / f".{name}.new"
            _remove_tree(fresh)
            shutil.copytree(source / name, fresh, ignore=shutil.ignore_patterns("node_modules", "__pycache__"))
            if (target / name / "node_modules").is_dir():
                os.replace(target / name / "node_modules", fresh / "node_modules")
            _remove_tree(target / name)
            os.replace(fresh, target / name)
        for stale in set(package_names(target)) - set(names):
            _remove_tree(target / stale)
        stamp = {"hash": wanted, "installed": False, "installed_once": bool(stamp.get("installed")
                                                                             or stamp.get("installed_once")),
                 "lock": stamp.get("lock", "")}
        (home / SHARED_STAMP).write_text(json.dumps(stamp, indent=2) + "\n", encoding="utf-8")
    manifest = json.dumps(shared_runtime_manifest(target, extra_dependencies), indent=2) + "\n"
    path = home / "package.json"
    if not path.is_file() or path.read_text(encoding="utf-8") != manifest:
        path.write_text(manifest, encoding="utf-8", newline="\n")
    lock = bundled_lock(start)
    if lock is not None:
        # npm rewrites its package-lock.json as it installs: ours goes back each time.
        data = lock.read_bytes().replace(b"\r\n", b"\n")
        copy = home / "package-lock.json"
        if not copy.is_file() or copy.read_bytes() != data:
            copy.write_bytes(data)
    link_runtime_packages(home)
    return (bool(missing_runtime_dependencies(extra_dependencies, start))
            or stamp.get("lock", "") != runtime_lock_hash(start))


def shared_runtime_installed_once() -> bool:
    """`npm install` completed in the shared runtime at least once: an update's install downloads
    only what changed."""
    home = shared_runtime_dir()
    stamp = _read_json(home / SHARED_STAMP) if home is not None else {}
    return bool(stamp.get("installed") or stamp.get("installed_once"))


def shared_runtime_time() -> float:
    """When the shared runtime was last brought up to date (0.0: never, or no installed extension)."""
    home = shared_runtime_dir()
    stamp = _read_json(home / SHARED_STAMP) if home is not None else {}
    try:
        return float(stamp.get("time") or 0.0) if stamp.get("installed") else 0.0
    except (TypeError, ValueError):
        return 0.0


def mark_shared_runtime_installed(start=None) -> None:
    home = shared_runtime_dir()
    stamp = {"hash": _bundle_runtime_hash(start), "installed": True, "time": time.time(),
             "lock": runtime_lock_hash(start)}
    (home / SHARED_STAMP).write_text(json.dumps(stamp, indent=2) + "\n", encoding="utf-8")
