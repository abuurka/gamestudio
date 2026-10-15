# SPDX-License-Identifier: GPL-3.0-or-later
"""The portable Node.js that GameStudio installs when the system has no Node.js 22.12+.

GameStudio's first-run setup (environment.py) downloads the official LTS archive pinned in
node.lock.json, checks it against the pinned SHA-256, and unpacks it into <user dir>/node, the
extension's own folder: nothing is installed system-wide and no admin rights are needed.
toolchain.node_dir() then puts it first on PATH for the dev server and npm, which runs as
`node npm-cli.js` (toolchain.npm_argv), never through a shell.

macOS and Linux get a .tar.gz: tar_filter() keeps the executable bits and the relative symlinks
(bin/npm, bin/npx) that the archive has, and paths with spaces (~/Library/Application Support/...)
need nothing special, as no shell is involved.

Standard library only (no bpy): install() runs on a worker thread.
"""

import hashlib
import json
import os
import pathlib
import platform
import shutil
import sys
import tarfile
import time
import urllib.error
import urllib.request
import zipfile

from . import toolchain

LOCK = pathlib.Path(__file__).with_name("node.lock.json")
MARKER = "gamestudio-node.json"
CHUNK = 1 << 16
TIMEOUT = 30


class NodeInstallError(RuntimeError):
    pass


def pin() -> dict:
    return json.loads(LOCK.read_text(encoding="utf-8"))


def platform_key() -> str | None:
    """'win-x64', 'darwin-arm64', ... in Node's release naming, or None when unsupported."""
    machine = platform.machine().lower()
    arch = {"amd64": "x64", "x86_64": "x64", "arm64": "arm64", "aarch64": "arm64"}.get(machine)
    system = {"win32": "win", "darwin": "darwin"}.get(sys.platform)
    if system is None and sys.platform.startswith("linux"):
        system = "linux"
    return f"{system}-{arch}" if system and arch else None


def release(lock: dict | None = None, key: str | None = None) -> dict | None:
    """{"name", "url", "sha256", "size_mb", "version"} of the archive for this platform, or None."""
    lock = lock or pin()
    entry = (lock.get("files") or {}).get(key or platform_key() or "")
    if not entry:
        return None
    return {**entry, "url": lock["base_url"] + entry["name"], "version": lock["version"]}


def installed(user_dir) -> dict:
    """The marker of the portable Node in <user dir>/node ({} when there is none)."""
    try:
        return json.loads((pathlib.Path(user_dir) / toolchain.PORTABLE_NODE / MARKER).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _report(progress, fraction, text):
    """progress(text, fraction): fraction is 0..1, or None when the size is unknown."""
    if progress is not None:
        progress(text, fraction)


def download(url: str, dest: pathlib.Path, sha256: str, progress=None, cancel=None) -> pathlib.Path:
    """Download `url` to `dest`, checking the SHA-256 as it arrives. Raises NodeInstallError."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    partial = dest.with_name(dest.name + ".part")
    digest = hashlib.sha256()
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "GameStudio"})
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response, open(partial, "wb") as out:
            total = int(response.headers.get("Content-Length") or 0)
            done, shown = 0, 0.0
            while True:
                if cancel is not None and cancel.is_set():
                    raise NodeInstallError("The download was cancelled")
                chunk = response.read(CHUNK)
                if not chunk:
                    break
                out.write(chunk)
                digest.update(chunk)
                done += len(chunk)
                now = time.monotonic()
                if now - shown > 0.2:
                    shown = now
                    mb = f"{done / 1e6:.0f} of {total / 1e6:.0f} MB" if total else f"{done / 1e6:.0f} MB"
                    _report(progress, done / total if total else None, f"Downloading Node.js ({mb})")
    except NodeInstallError:
        partial.unlink(missing_ok=True)
        raise
    except urllib.error.HTTPError as exc:
        partial.unlink(missing_ok=True)
        raise NodeInstallError(f"Could not download {url}: the server answered {exc.code} {exc.reason}") from exc
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        partial.unlink(missing_ok=True)
        reason = getattr(exc, "reason", exc)
        raise NodeInstallError(f"Could not download {url} ({reason}). Check the internet connection "
                               "and try again") from exc
    got = digest.hexdigest()
    if got != sha256.lower():
        partial.unlink(missing_ok=True)
        raise NodeInstallError(f"The downloaded {dest.name} does not match its pinned SHA-256 (expected {sha256}, "
                               f"got {got}). It was deleted; nothing was installed")
    os.replace(partial, dest)
    return dest


def _single_top(names: list[str]) -> str:
    tops = {name.replace("\\", "/").lstrip("/").split("/", 1)[0] for name in names if name.strip("/")}
    return tops.pop() if len(tops) == 1 else ""


def long_path(path) -> str:
    """`path` for file APIs that must reach past Windows' 260-character limit: npm's own files sit
    deep inside node/, and the extension's user folder is already deep in the Blender profile."""
    text = os.path.abspath(str(path))
    if sys.platform != "win32" or text.startswith("\\\\?\\"):
        return text
    return "\\\\?\\UNC\\" + text[2:] if text.startswith("\\\\") else "\\\\?\\" + text


def remove_tree(path) -> None:
    shutil.rmtree(long_path(path), ignore_errors=True)


def tar_filter(member: tarfile.TarInfo, path: str) -> tarfile.TarInfo | None:
    """tarfile's "data" filter, which keeps what Node.js needs on macOS and Linux: the executable bits
    (bin/node) and the relative symlinks inside the archive (bin/npm -> ../lib/node_modules/npm/...).
    A link it refuses (absolute, or leading out of the folder) is skipped instead of failing the whole
    install; anything else it refuses (a path outside the folder, a device) still fails."""
    try:
        return tarfile.data_filter(member, path)
    except tarfile.FilterError:
        if member.issym() or member.islnk():
            return None
        raise


def _make_executable(folder: pathlib.Path) -> None:
    """bin/node and the scripts in bin/ stay executable (macOS, Linux), whatever the archive said."""
    bin_dir = folder / "bin"
    if sys.platform == "win32" or not bin_dir.is_dir():
        return
    for path in bin_dir.iterdir():
        try:
            if path.is_file():
                path.chmod(path.stat().st_mode | 0o755)
        except OSError:
            pass


def extract(archive: pathlib.Path, dest: pathlib.Path) -> None:
    """Unpack a Node.js release archive into `dest`, without its top folder (node-v24-.../)."""
    staging = dest.with_name(dest.name + ".unpack")
    remove_tree(staging)
    staging.mkdir(parents=True)
    if archive.name.endswith(".zip"):
        with zipfile.ZipFile(archive) as zf:
            top = _single_top(zf.namelist())
            zf.extractall(long_path(staging))  # zipfile drops ".." and absolute parts
    else:
        with tarfile.open(archive) as tf:
            top = _single_top(tf.getnames())
            tf.extractall(long_path(staging), filter=tar_filter)
    source = staging / top if top else staging
    remove_tree(dest)
    os.replace(source, dest)
    remove_tree(staging)
    _make_executable(dest)


def install(user_dir, progress=None, cancel=None, lock: dict | None = None, key: str | None = None) -> pathlib.Path:
    """Download, verify and unpack the pinned Node.js into <user dir>/node. Returns that folder.
    Raises NodeInstallError with a message for the user."""
    entry = release(lock, key)
    if entry is None:
        raise NodeInstallError(f"Node.js has no release for this platform ({platform_key() or sys.platform} "
                               f"{platform.machine()}), so GameStudio cannot set it up here; it uses a Node.js "
                               f"{'.'.join(map(str, toolchain.MIN_NODE))} or newer found on the PATH")
    user_dir = pathlib.Path(user_dir)
    downloads = user_dir / "downloads"
    archive = downloads / entry["name"]
    _report(progress, 0.0, f"Downloading Node.js {entry['version']}")
    try:
        download(entry["url"], archive, entry["sha256"], progress, cancel)
        _report(progress, None, f"Unpacking Node.js {entry['version']}")
        target = user_dir / toolchain.PORTABLE_NODE
        extract(archive, target)
    except OSError as exc:
        raise NodeInstallError(f"Could not set up Node.js in {user_dir}: {exc}") from exc
    finally:
        archive.unlink(missing_ok=True)
    exe = target / ("node.exe" if sys.platform == "win32" else "bin/node")
    version = toolchain.node_version(exe)
    if version is None or ".".join(map(str, version)) != entry["version"]:
        raise NodeInstallError(f"The installed Node.js in {target} does not run (got {version}). "
                               "Delete that folder and try again")
    marker = {"version": entry["version"], "platform": key or platform_key(), "sha256": entry["sha256"],
              "installed": time.time()}
    (target / MARKER).write_text(json.dumps(marker, indent=2) + "\n", encoding="utf-8")
    toolchain.forget_node()
    return target
