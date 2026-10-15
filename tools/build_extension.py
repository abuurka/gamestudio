#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Build the GameStudio extension: dist/gamestudio-<version>.zip, ready for Blender 5.2+.

    python tools/build_extension.py                    # build and validate
    python tools/build_extension.py --blender PATH     # with a specific blender(.exe)
    python tools/build_extension.py --stage DIR        # also keep the staged folder in DIR

The zip is blender/gamestudio plus what a user without this repository needs:

    bundle.json            {"kind": "extension", ...}: core/toolchain.py reads it
    runtime/packages/      the GameStudio runtime packages as sources (all but host, which is
                           Electron for GameStudio Editor), without node_modules or tests
    examples/demo-game/    the demo, without node_modules, .gamestudio or dist

Never in it: tests, __pycache__, assistant/_vendor, .gamestudio. Blender builds the zip
(`blender --command extension build`) and checks it (`blender --command extension validate`),
both with factory settings and a scratch profile.
"""

import argparse
import importlib.util
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import tomllib
import zipfile

REPO = pathlib.Path(__file__).resolve().parents[1]
SOURCE = REPO / "blender" / "gamestudio"
PACKAGES = REPO / "runtime" / "packages"
EXAMPLES = ("demo-game",)
DIST = REPO / "dist"
EXCLUDED_PACKAGES = {"host"}

# What each part leaves out (shutil.ignore_patterns names, matched in every folder).
SKIP_EXTENSION = ("__pycache__", "*.pyc", ".ruff_cache", ".pytest_cache", ".gamestudio", "_vendor", "*.blend1")
SKIP_PACKAGE = ("node_modules", "test", "tests", "__pycache__", ".vite", "*.tsbuildinfo", ".gamestudio")
SKIP_EXAMPLE = ("node_modules", ".gamestudio", "dist", "__pycache__", "*.blend1", ".vite")

# Checked in the finished zip.
# Dotfiles count: the project template's .mcp.json, .claude/ and .gitignore go into every project,
# and Open in Claude Code (claude_code.py) adds the agent files to older projects from there.
REQUIRED = ("blender_manifest.toml", "__init__.py", "bundle.json", "core/node.lock.json", "core/skill_history.json",
            "resources/workspaces.blend", "project_templates/starter/package.json",
            "project_templates/starter/.gitignore", "project_templates/starter/.mcp.json",
            "project_templates/starter/AGENTS.md", "project_templates/starter/CLAUDE.md", "project_templates/starter/.claude/settings.json",
            "runtime/packages/engine/package.json", "runtime/packages/devserver/package.json",
            "runtime/packages/mcp/package.json", "runtime/packages/mcp/bin/gamestudio-mcp.js",
            "runtime/packages/devserver/bin/gamestudio-browser.js",
            "examples/demo-game/gamestudio.json", "examples/demo-game/scenes/main.blend")
# The licence texts (LICENSE.md at the repository root): the extension's GPL, which GPL section 4 says
# must go with every copy, the runtime packages' MIT, the templates' MIT-0 and the starter's CC0 note.
REQUIRED += ("LICENSE", "project_templates/LICENSE", "project_templates/starter/assets/LICENSE.txt",
             "script_templates/LICENSE", "runtime/packages/engine/LICENSE", "runtime/packages/devserver/LICENSE",
             "runtime/packages/mcp/LICENSE", "examples/demo-game/LICENSE")
# The shared runtime's lock: the setup installs its versions (tools/runtime_lock.py makes it).
REQUIRED += ("core/runtime.lock.json",)
# What games' web builds must carry: Inter's licence (copied with the font) and the engine's credits.
REQUIRED += ("resources/fonts/Inter-OFL.txt", "runtime/packages/engine/THIRD-PARTY-NOTICES.md")
FORBIDDEN_PARTS =("__pycache__", "node_modules", ".gamestudio", "_vendor", "test", "tests")


def load_toolchain():
    spec = importlib.util.spec_from_file_location("gs_toolchain", SOURCE / "core" / "toolchain.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def manifest_version() -> str:
    return tomllib.loads((SOURCE / "blender_manifest.toml").read_text(encoding="utf-8"))["version"]


def runtime_packages() -> list[pathlib.Path]:
    return sorted(p for p in PACKAGES.iterdir()
                  if (p / "package.json").is_file() and p.name not in EXCLUDED_PACKAGES)


def _ignore_vendor(directory, names):
    ignored = set(shutil.ignore_patterns(*SKIP_EXTENSION)(directory, names))
    # Only the assistant's vendored SDK is called _vendor; keep the name check that narrow.
    if "_vendor" in names and pathlib.Path(directory).name != "assistant":
        ignored.discard("_vendor")
    return ignored


def inline_tsconfig(folder: pathlib.Path, source: pathlib.Path) -> None:
    """The packages and examples extend the repository's tsconfig.base.json, which the zip does not
    have (and Vite's transformer fails on a missing base): fold it into their tsconfig.json."""
    path = folder / "tsconfig.json"
    if not path.is_file():
        return
    config = json.loads(path.read_text(encoding="utf-8"))
    reference = config.get("extends")
    if not isinstance(reference, str) or not reference.startswith("."):
        return
    base = json.loads((source / reference).read_text(encoding="utf-8"))
    base.pop("extends", None)
    merged = {**base, **{k: v for k, v in config.items() if k != "extends"}}
    merged["compilerOptions"] = {**base.get("compilerOptions", {}), **config.get("compilerOptions", {})}
    path.write_text(json.dumps(merged, indent=2) + "\n", encoding="utf-8", newline="\n")


def stage(target: pathlib.Path) -> dict:
    """Assemble the extension's folder in `target` (emptied first). Returns bundle.json."""
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(SOURCE, target, ignore=_ignore_vendor)
    names = []
    for package in runtime_packages():
        shutil.copytree(package, target / "runtime" / "packages" / package.name,
                        ignore=shutil.ignore_patterns(*SKIP_PACKAGE))
        inline_tsconfig(target / "runtime" / "packages" / package.name, package)
        names.append(package.name)
    for name in EXAMPLES:
        example = target / "examples" / name
        shutil.copytree(REPO / "examples" / name, example, ignore=shutil.ignore_patterns(*SKIP_EXAMPLE))
        inline_tsconfig(example, REPO / "examples" / name)
    toolchain = load_toolchain()
    bundle = {
        "comment": "Marks an installed GameStudio extension (core/toolchain.py). Made by tools/build_extension.py.",
        "kind": "extension",
        "gamestudio": manifest_version(),
        "runtime": {"packages": names, "hash": toolchain.runtime_hash(target / "runtime" / "packages")},
        "examples": list(EXAMPLES),
    }
    (target / "bundle.json").write_text(json.dumps(bundle, indent=2) + "\n", encoding="utf-8", newline="\n")
    return bundle


def check_zip(path: pathlib.Path) -> list[str]:
    """Problems with the finished zip: required files missing, excluded files present."""
    with zipfile.ZipFile(path) as zf:
        entries = [name.replace("\\", "/") for name in zf.namelist()]
    problems = [f"missing {name}" for name in REQUIRED if name not in entries]
    for name in entries:
        parts = name.split("/")
        if any(part in FORBIDDEN_PARTS for part in parts[:-1]) or name.endswith(".pyc"):
            problems.append(f"should not be in the zip: {name}")
        if name.startswith("runtime/packages/host/"):
            problems.append(f"should not be in the zip: {name}")
    return problems


def blender_run(blender: pathlib.Path, args: list[str]) -> subprocess.CompletedProcess:
    with tempfile.TemporaryDirectory(prefix="gs_build_ext_") as profile:
        env = dict(os.environ, BLENDER_USER_RESOURCES=profile)
        return subprocess.run([str(blender), "--factory-startup", "--command", *args], env=env,
                              capture_output=True, encoding="utf-8", errors="replace")


def _show(result: subprocess.CompletedProcess) -> None:
    text = ((result.stdout or "") + (result.stderr or "")).strip()
    if text:
        print("\n".join(f"  {line}" for line in text.splitlines()))


def build(blender: pathlib.Path, output_dir: pathlib.Path = DIST, keep_stage: pathlib.Path | None = None,
          validate: bool = True) -> pathlib.Path:
    version = manifest_version()
    output_dir.mkdir(parents=True, exist_ok=True)
    package = output_dir / f"gamestudio-{version}.zip"
    with tempfile.TemporaryDirectory(prefix="gs_stage_") as tmp:
        folder = keep_stage or pathlib.Path(tmp) / "gamestudio"
        bundle = stage(folder)
        print(f"[build] staged {folder} (runtime packages: {', '.join(bundle['runtime']['packages'])})")
        package.unlink(missing_ok=True)
        result = blender_run(blender, ["extension", "build", "--source-dir", str(folder),
                                       "--output-filepath", str(package)])
        _show(result)
        if result.returncode != 0 or not package.is_file():
            sys.exit(f"[build] blender --command extension build failed (exit code {result.returncode})")
    problems = check_zip(package)
    if problems:
        sys.exit("[build] the zip is wrong:\n  " + "\n  ".join(problems[:20]))
    # A .blend keeps folders it was used with (a File Browser's, Blender's Documents by default).
    check = [sys.executable, str(REPO / "tools" / "check_blend_paths.py"), "--blender", str(blender), str(package)]
    if subprocess.run(check).returncode != 0:
        sys.exit("[build] a .blend in the zip has an absolute path or a user name (see above)")
    if validate:
        result = blender_run(blender, ["extension", "validate", str(package)])
        _show(result)
        if result.returncode != 0:
            sys.exit(f"[build] blender --command extension validate failed (exit code {result.returncode})")
    with zipfile.ZipFile(package) as zf:
        count = len(zf.namelist())
    print(f"[build] {package} ({package.stat().st_size / 1e6:.1f} MB, {count} files)")
    return package


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--blender", help="blender executable (default: found automatically)")
    parser.add_argument("--output-dir", default=str(DIST), help="where the zip goes (default: dist/)")
    parser.add_argument("--stage", help="assemble the extension in this folder and keep it")
    parser.add_argument("--no-validate", action="store_true", help="skip `blender --command extension validate`")
    args = parser.parse_args()
    sys.path.insert(0, str(REPO / "tools"))
    import blender_dev
    import skill_history

    skill_history.main([])  # projects made with older skills recognise them (core/skills.py)
    # The lock must fit the package.json the setup writes, or npm would resolve versions afresh.
    if subprocess.run([sys.executable, str(REPO / "tools" / "runtime_lock.py"), "--check"]).returncode != 0:
        sys.exit("[build] core/runtime.lock.json is stale: run python tools/runtime_lock.py, then build again")
    build(blender_dev.find_blender(args.blender), pathlib.Path(args.output_dir),
          pathlib.Path(args.stage).resolve() if args.stage else None, validate=not args.no_validate)
    return 0


if __name__ == "__main__":
    sys.exit(main())
