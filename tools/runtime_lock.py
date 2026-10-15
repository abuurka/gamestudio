#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""The shared runtime's lock: blender/gamestudio/core/runtime.lock.json.

    python tools/runtime_lock.py            # make it again (npm and the npm registry)
    python tools/runtime_lock.py --check    # exit 1 when it no longer fits (offline, no npm)

An installed extension writes <user dir>/runtime/package.json (core/toolchain.py,
shared_runtime_manifest: the bundled @gamestudio packages as file:packages/<name>, and the project
template's third-party packages) and installs it with this lock beside it. So every computer gets
the same versions of every package, the indirect ones too, and one bad upstream release cannot
break new installs.

This tool builds that same package.json in a scratch folder, with the bundled packages' package.json
files under packages/, and runs `npm install --package-lock-only --ignore-scripts` there. There is
no node_modules, so npm lists every platform's optional packages (rolldown, lightningcss,
TypeScript's compiler, fsevents), not only this computer's. npm starts from the repository's
package-lock.json, so the versions are the ones the repository's tests ran with; the tool lists any
it had to change. The links are relative (packages/<name>): the lock fits any user folder.

--check compares the lock with that package.json and with the bundled packages' dependencies, as
npm does: a dependency added, removed or changed anywhere means the lock is stale.
"""

import argparse
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parents[1]
SOURCE = REPO / "blender" / "gamestudio"
TEMPLATE = SOURCE / "project_templates" / "starter"
REPO_LOCK = REPO / "package-lock.json"
REGISTRY = "https://registry.npmjs.org/"
# The fields of a bundled package that npm compares with its entry in the lock.
LINK_FIELDS = ("version", "dependencies", "devDependencies", "optionalDependencies", "peerDependencies")

sys.path.insert(0, str(REPO / "tools"))
import build_extension  # noqa: E402 - the packages the zip ships, and core/toolchain.py

toolchain = build_extension.load_toolchain()
LOCK = SOURCE / "core" / toolchain.RUNTIME_LOCK


def write_runtime(folder: pathlib.Path) -> dict:
    """The shared runtime's package.json in `folder`, as the extension writes it, beside
    packages/<name>/package.json for each bundled package. Returns the manifest."""
    for package in build_extension.runtime_packages():
        target = folder / "packages" / package.name
        target.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(package / "package.json", target / "package.json")
    extra = toolchain.third_party_dependencies(toolchain.read_manifest(TEMPLATE))
    manifest = toolchain.shared_runtime_manifest(folder / "packages", extra)
    (folder / "package.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8", newline="\n")
    return manifest


def seed(manifest: dict, names: list[str]) -> dict:
    """The repository's lock moved to the shared runtime's layout: runtime/packages/<name> becomes
    packages/<name>, the other workspaces (host, the examples) go. npm keeps every version that
    still fits and drops what nothing needs."""
    repo = json.loads(REPO_LOCK.read_text(encoding="utf-8"))
    entries = {"": {"name": manifest["name"], "version": manifest["version"],
                    "dependencies": manifest["dependencies"]}}
    for path, entry in repo.get("packages", {}).items():
        if not path:
            continue
        if path.startswith("runtime/packages/"):
            name = path.split("/")[2]
            if name in names:
                entries["packages/" + path[len("runtime/packages/"):]] = entry
            continue
        if not path.startswith("node_modules/"):
            continue  # another workspace: an example
        if entry.get("link"):
            resolved = str(entry.get("resolved", ""))
            name = resolved.split("/")[-1]
            if resolved.startswith("runtime/packages/") and name in names:
                entries[path] = {**entry, "resolved": f"packages/{name}"}
            continue
        entries[path] = entry
    return {"name": manifest["name"], "version": manifest["version"], "lockfileVersion": 3, "requires": True,
            "packages": entries}


def npm() -> list[str]:
    argv = toolchain.npm_argv("npm")
    if argv is None:
        sys.exit("[runtime-lock] npm was not found (Node.js 22.12 or newer)")
    return argv


def generate() -> dict:
    """A new lock, made in a scratch folder (under GS_TEST_TMP when it is set)."""
    scratch = pathlib.Path(tempfile.mkdtemp(prefix="gs_runtime_lock_", dir=os.environ.get("GS_TEST_TMP") or None))
    try:
        manifest = write_runtime(scratch)
        lock = seed(manifest, toolchain.package_names(scratch / "packages"))
        (scratch / "package-lock.json").write_text(json.dumps(lock, indent=2) + "\n", encoding="utf-8",
                                                   newline="\n")
        argv = [*npm(), "install", "--package-lock-only", "--ignore-scripts", "--no-audit", "--no-fund",
                "--lockfile-version=3", f"--registry={REGISTRY}"]
        print(f"[runtime-lock] npm install --package-lock-only in {scratch}")
        result = subprocess.run(argv, cwd=scratch, env=toolchain.child_env(), stdin=subprocess.DEVNULL)
        if result.returncode != 0:
            sys.exit(f"[runtime-lock] npm failed (exit code {result.returncode})")
        return json.loads((scratch / "package-lock.json").read_text(encoding="utf-8"))
    finally:
        if (scratch / "node_modules").exists():  # it may hold links: left for a look, never deleted through
            sys.exit(f"[runtime-lock] npm made {scratch / 'node_modules'}: the lock would miss other platforms' "
                     "packages")
        shutil.rmtree(scratch, ignore_errors=True)  # package.json files only


def problems(lock: dict, manifest: dict, packages: pathlib.Path) -> list[str]:
    """Why `lock` does not fit `manifest` and the bundled packages in `packages` (npm would change it)."""
    found = []
    if lock.get("lockfileVersion") != 3:
        found.append(f"lockfileVersion is {lock.get('lockfileVersion')!r}, not 3")
    entries = lock.get("packages") if isinstance(lock.get("packages"), dict) else {}
    root = entries.get("", {})
    for field in ("dependencies", "devDependencies", "optionalDependencies", "peerDependencies"):
        if (root.get(field) or {}) != (manifest.get(field) or {}):
            found.append(f"its {field} are not the package.json's")
    for name, spec in (manifest.get("dependencies") or {}).items():
        entry = entries.get(f"node_modules/{name}")
        if entry is None:
            found.append(f"{name} is missing")
        elif spec.startswith("file:"):
            if not entry.get("link") or entry.get("resolved") != spec[len("file:"):]:
                found.append(f"{name} is not linked to {spec[len('file:'):]}")
        elif not toolchain.satisfies(str(entry.get("version", "")), spec):
            found.append(f"{name} is {entry.get('version')}, the package.json asks for {spec}")
    for name in toolchain.package_names(packages):
        theirs = toolchain.read_manifest(packages / name)
        entry = entries.get(f"packages/{name}", {})
        for field in LINK_FIELDS:
            if (entry.get(field) or None) != (theirs.get(field) or None):
                found.append(f"packages/{name}: its {field} are not those of runtime/packages/{name}/package.json")
    for path, entry in entries.items():
        if path.startswith("packages/") and "/node_modules/" not in path and path.split("/")[1] not in \
                toolchain.package_names(packages):
            found.append(f"{path} is not a bundled package")
        if path.startswith("node_modules/") and entry.get("link") and "/node_modules/" not in path[13:] \
                and path[13:] not in (manifest.get("dependencies") or {}):
            found.append(f"{path} is a link the package.json does not ask for")
    return found


def _shown(path: pathlib.Path) -> str:
    return path.relative_to(REPO).as_posix() if path.is_relative_to(REPO) else str(path)


def check() -> list[str]:
    """Why the committed lock is stale ([] when it fits)."""
    if not LOCK.is_file():
        return [f"{_shown(LOCK)} does not exist"]
    try:
        lock = json.loads(LOCK.read_text(encoding="utf-8"))
    except ValueError as exc:
        return [f"{_shown(LOCK)} is no JSON: {exc}"]
    with tempfile.TemporaryDirectory(prefix="gs_runtime_lock_", dir=os.environ.get("GS_TEST_TMP") or None) as tmp:
        manifest = write_runtime(pathlib.Path(tmp))
        return problems(lock, manifest, pathlib.Path(tmp) / "packages")


def _package_name(path: str, entry: dict) -> str:
    return entry.get("name") or path.rsplit("node_modules/", 1)[-1]


def differences(lock: dict) -> list[str]:
    """The packages of `lock` at a version the repository's lock does not have (CI tested those)."""
    repo = json.loads(REPO_LOCK.read_text(encoding="utf-8")).get("packages", {})
    versions = {}
    for path, entry in repo.items():
        if "node_modules/" in path and not entry.get("link"):
            versions.setdefault(_package_name(path, entry), set()).add(entry.get("version"))
    found = []
    for path, entry in sorted(lock.get("packages", {}).items()):
        if "node_modules/" not in path or entry.get("link"):
            continue
        name = _package_name(path, entry)
        if entry.get("version") not in versions.get(name, set()):
            had = ", ".join(sorted(v for v in versions.get(name, set()) if v)) or "not there"
            found.append(f"{name}@{entry.get('version')} (the repository's lock: {had})")
    return found


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="exit 1 when the lock is stale (offline)")
    args = parser.parse_args(argv)
    rel = _shown(LOCK)
    if args.check:
        stale = check()
        if stale:
            print(f"[runtime-lock] {rel} is stale; run python tools/runtime_lock.py:\n  " + "\n  ".join(stale))
            return 1
        print(f"[runtime-lock] {rel} fits the shared runtime's package.json")
        return 0
    lock = generate()
    with tempfile.TemporaryDirectory(prefix="gs_runtime_lock_", dir=os.environ.get("GS_TEST_TMP") or None) as tmp:
        manifest = write_runtime(pathlib.Path(tmp))
        wrong = problems(lock, manifest, pathlib.Path(tmp) / "packages")
    if wrong:
        sys.exit("[runtime-lock] npm's lock does not fit:\n  " + "\n  ".join(wrong))
    LOCK.write_text(json.dumps(lock, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    count = sum(1 for path in lock["packages"] if "node_modules/" in path)
    print(f"[runtime-lock] wrote {rel} ({count} packages)")
    for line in differences(lock):
        print(f"[runtime-lock]   not the repository's version: {line}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
