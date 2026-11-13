#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Download, verify and unpack the stock Blender that CI tests against (tools/ci/blender.lock.json).

    python tools/ci/install_blender.py --dest DIR [--version 5.3.0] [--github-env]

--version picks one of the lock's Blenders (default: the lock's "default", the declared minimum).
Picks this platform's archive from the lock (a portable .zip on Windows, a .tar.xz on Linux, a
.dmg on macOS), checks it against the pinned SHA-256, unpacks it into DIR and prints the blender
executable's path (DIR/blender-<version>-<platform>/blender(.exe), or on macOS
DIR/Blender.app/Contents/MacOS/Blender). A DIR that already holds that archive's Blender (the CI
cache) is reused without downloading. --github-env also writes BLENDER=<path> to $GITHUB_ENV for
the next steps.

macOS: the .dmg is mounted read-only with hdiutil (no Finder window), Blender.app is copied out
with ditto (keeps the bundle's symlinks, permissions and code signature) and the image is detached
again, even when the copy fails.

Keep DIR short on Windows: Blender's own files sit about 110 characters below it, and Blender does
not open paths longer than 260 characters (its glTF exporter then fails to import).

Standard library only. Never touches a Blender profile.
"""

import argparse
import hashlib
import json
import os
import pathlib
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import zipfile

LOCK = pathlib.Path(__file__).with_name("blender.lock.json")
MARKER = "gamestudio-ci-blender.json"
CHUNK = 1 << 20


def platform_key() -> str:
    machine = platform.machine().lower()
    arch = {"amd64": "x64", "x86_64": "x64", "arm64": "arm64", "aarch64": "arm64"}.get(machine, machine)
    system = "windows" if sys.platform == "win32" else "macos" if sys.platform == "darwin" else "linux"
    return f"{system}-{arch}"


def executable(folder: pathlib.Path) -> pathlib.Path | None:
    """blender(.exe) in the unpacked release (one top folder, e.g. blender-5.2.2-linux-x64/), or on
    macOS the app bundle's Blender.app/Contents/MacOS/Blender."""
    if sys.platform == "darwin":
        name = "Blender.app/Contents/MacOS/Blender"
    else:
        name = "blender.exe" if sys.platform == "win32" else "blender"
    for candidate in (folder / name, *(p / name for p in sorted(folder.iterdir()) if p.is_dir())):
        if candidate.is_file():
            return candidate
    return None


def lock_entry(lock: dict, version: str | None, key: str) -> tuple[str, str, str, str]:
    """(version, url, sha256, archive name) of one Blender in the lock for platform key."""
    version = version or lock["default"]
    release = lock["versions"].get(version)
    if release is None:
        sys.exit(f"[blender] {LOCK.name} has no Blender {version} (has: {', '.join(lock['versions'])})")
    entry = release["files"].get(key)
    if entry is None:
        sys.exit(f"[blender] {LOCK.name} has no Blender {version} archive for {key} (has: {', '.join(release['files'])})")
    return version, release["base_url"] + entry["name"], entry["sha256"].lower(), entry["name"]


def download(url: str, dest: pathlib.Path, sha256: str) -> None:
    digest = hashlib.sha256()
    request = urllib.request.Request(url, headers={"User-Agent": "GameStudio-CI"})
    with urllib.request.urlopen(request, timeout=60) as response, open(dest, "wb") as out:
        while chunk := response.read(CHUNK):
            out.write(chunk)
            digest.update(chunk)
    if digest.hexdigest() != sha256.lower():
        dest.unlink()
        sys.exit(f"[blender] {url}: SHA-256 {digest.hexdigest()} does not match the pinned {sha256}")


def copy_from_dmg(image: pathlib.Path, dest: pathlib.Path) -> None:
    """Copy the .app bundle(s) of a macOS disk image into dest (the image's "Applications" link
    and other files are left behind)."""
    mount = pathlib.Path(tempfile.mkdtemp(prefix=f".{dest.name}-mount-", dir=dest.parent))
    # "Y" answers a licence prompt, should the image ever carry one; it is ignored otherwise.
    subprocess.run(["hdiutil", "attach", str(image), "-nobrowse", "-readonly", "-noautoopen",
                    "-mountpoint", str(mount)], input=b"Y\n", stdout=subprocess.DEVNULL, check=True)
    try:
        apps = [p for p in sorted(mount.iterdir()) if p.suffix == ".app" and p.is_dir() and not p.is_symlink()]
        if not apps:
            sys.exit(f"[blender] no .app bundle in {image.name}")
        for app in apps:
            subprocess.run(["ditto", str(app), str(dest / app.name)], check=True)
    finally:
        detach = ["hdiutil", "detach", str(mount)]
        if subprocess.run(detach, stdout=subprocess.DEVNULL).returncode != 0:
            subprocess.run([*detach, "-force"], stdout=subprocess.DEVNULL)
        try:
            mount.rmdir()
        except OSError:
            pass


def unpack(archive: pathlib.Path, dest: pathlib.Path) -> None:
    if archive.name.endswith(".dmg"):
        copy_from_dmg(archive, dest)
    elif archive.name.endswith(".zip"):
        with zipfile.ZipFile(archive) as zf:
            zf.extractall(dest)  # zipfile drops ".." and absolute parts
    else:
        with tarfile.open(archive) as tf:
            tf.extractall(dest, filter="data")  # keeps the libraries' relative symlinks and exec bits


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dest", required=True, help="folder to unpack into (replaced unless it holds this release)")
    parser.add_argument("--version", help="which Blender of the lock (default: its 'default')")
    parser.add_argument("--github-env", action="store_true", help="append BLENDER=<path> to $GITHUB_ENV")
    args = parser.parse_args()

    lock = json.loads(LOCK.read_text(encoding="utf-8"))
    version, url, sha256, name = lock_entry(lock, args.version, platform_key())

    dest = pathlib.Path(args.dest).expanduser().resolve()
    try:
        cached = json.loads((dest / MARKER).read_text(encoding="utf-8")).get("sha256") == sha256
    except (OSError, ValueError):
        cached = False
    exe = executable(dest) if cached else None
    if exe is None:
        if dest.exists():
            shutil.rmtree(dest)
        dest.mkdir(parents=True)
        archive = dest.parent / f".{dest.name}-{name}"
        print(f"[blender] downloading {url}", flush=True)
        try:
            download(url, archive, sha256)
            print(f"[blender] SHA-256 ok; unpacking into {dest}", flush=True)
            unpack(archive, dest)
        finally:
            archive.unlink(missing_ok=True)
        exe = executable(dest)
        if exe is None:
            sys.exit(f"[blender] no blender executable in {dest}")
        (dest / MARKER).write_text(json.dumps({"url": url, "sha256": sha256}) + "\n", encoding="utf-8")
    else:
        print(f"[blender] using the cached Blender {version} in {dest}", flush=True)
    print(exe)
    if args.github_env:
        with open(os.environ["GITHUB_ENV"], "a", encoding="utf-8") as env:
            env.write(f"BLENDER={exe}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
