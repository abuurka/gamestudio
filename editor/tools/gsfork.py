#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""gsfork: build and maintain GameStudio Editor, a thin distribution of Blender.

GameStudio never commits Blender's sources. It keeps:
  editor/upstream.lock.json   the exact Blender tag, commit and libraries
  editor/patches/             the seams: small additive patches (series file = order)
  editor/branding/overlay/    files copied over the upstream tree (icons, splash, ...)
  editor/native/              out-of-tree C++ module, compiled in via seam S01
  editor/shell/               Python IDE shell (app template + system extensions)

Commands (run from anywhere; machine paths default to siblings of the repo):
  status            show the lock, local paths and their state
  sync              fetch the locked Blender commit, its LFS assets and precompiled libs
  apply             fresh worktree at the locked commit + patches + branding overlay
  export            turn commits made on the worktree back into editor/patches/
  build             configure (first time) and build with Ninja + sccache, then install
  run [-- ARGS]     start the built editor with the shell loaded from source
  test [--stock]    run the add-on/contract tests against the built editor (or stock Blender)
  canary REF        apply the patch series to another upstream ref and report every seam
                    (clean / 3-way / conflict); --build-check also checks native includes
  template          regenerate the app template's startup.blend / userpref.blend
  branding          render the splash and icons into editor/branding/generated
  overlay-lock      record the upstream files the branding overlay replaces (after review)

Override machine paths in editor/gsfork.local.json:
  {"mirror": "...", "work": "...", "build": "...", "sccache": "..."}
"""

import argparse
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import time

REPO = pathlib.Path(__file__).resolve().parents[2]
EDITOR = REPO / "editor"
LOCK_FILE = EDITOR / "upstream.lock.json"
PATCHES = EDITOR / "patches"
SERIES = PATCHES / "series"
OVERLAY = EDITOR / "branding" / "overlay"
OVERLAY_LOCK = EDITOR / "branding" / "overlay.lock.json"
NATIVE = EDITOR / "native"
SHELL = EDITOR / "shell"
BUILD_OPTIONS = EDITOR / "build" / "gamestudio.cmake"
# Build configs. "dev" (default) skips subsystems game development never uses (see
# editor/build/gamestudio-dev.cmake) so clean builds compile far less; "full" is the release set.
CONFIGS = {
    "dev": {"suffix": "-dev", "extra": [EDITOR / "build" / "gamestudio-dev.cmake"]},
    "full": {"suffix": "", "extra": []},
}
LOCAL_CONFIG = EDITOR / "gsfork.local.json"
LIB_PLATFORM = "windows_x64"
PATCH_BRANCH = "gs/patched"


# ----------------------------------------------------------------------- helpers


def lock() -> dict:
    return json.loads(LOCK_FILE.read_text(encoding="utf-8"))


def paths() -> dict:
    base = REPO.parent
    cfg = {
        "mirror": base / "blender-upstream",
        "work": base / "gs-blender",
        "build": base / "gs-build",
        "sccache": base / "gs-sccache",
        "userdata": base / "gs-userdata",
    }
    if LOCAL_CONFIG.is_file():
        for key, value in json.loads(LOCAL_CONFIG.read_text(encoding="utf-8")).items():
            cfg[key] = pathlib.Path(value)
    for key, value in cfg.items():
        if " " in str(value):
            sys.exit(f"Blender cannot be built from a path with spaces: {key} = {value}")
    return cfg


def say(msg: str) -> None:
    print(f"[gsfork] {msg}", flush=True)


def run(cmd, cwd=None, env=None, check=True, capture=False, quiet=False) -> subprocess.CompletedProcess:
    if not quiet:
        shown = " ".join(str(c) for c in cmd)
        print(f"  $ {shown}" + (f"   (in {cwd})" if cwd else ""), flush=True)
    full_env = dict(os.environ, **(env or {}))
    result = subprocess.run([str(c) for c in cmd], cwd=cwd, env=full_env, text=True,
                            encoding="utf-8", errors="replace",
                            capture_output=capture)
    if check and result.returncode != 0:
        if capture:
            print((result.stdout or "") + (result.stderr or ""))
        sys.exit(f"[gsfork] command failed ({result.returncode}): {cmd[0]} {cmd[1] if len(cmd) > 1 else ''}")
    return result


def git(*args, cwd, check=True, capture=True, env=None, quiet=True) -> str:
    return run(["git", *args], cwd=cwd, env=env, check=check, capture=capture, quiet=quiet).stdout.strip()


def has_commit(repo: pathlib.Path, commit: str) -> bool:
    return run(["git", "cat-file", "-e", f"{commit}^{{commit}}"], cwd=repo, check=False,
               capture=True, quiet=True).returncode == 0


def series() -> list[str]:
    if not SERIES.is_file():
        return []
    names = []
    for line in SERIES.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            names.append(line)
    return names


# ------------------------------------------------------------------------- sync


def cmd_sync(args) -> None:
    lk, p = lock(), paths()
    mirror = p["mirror"]
    no_smudge = {"GIT_LFS_SKIP_SMUDGE": "1"}
    if not (mirror / ".git").exists():
        say(f"Cloning Blender {lk['blender']['tag']} into {mirror} (shallow)")
        run(["git", "clone", "--depth", "1", "--branch", lk["blender"]["tag"], "--single-branch",
             lk["blender"]["remote"], mirror], env=no_smudge)
    if not has_commit(mirror, lk["blender"]["commit"]):
        say(f"Fetching {lk['blender']['tag']}")
        run(["git", "fetch", "--depth", "1", "origin", "tag", lk["blender"]["tag"]], cwd=mirror, env=no_smudge)
    if not has_commit(mirror, lk["blender"]["commit"]):
        sys.exit(f"Tag {lk['blender']['tag']} does not point at the locked commit {lk['blender']['commit']}")
    if git("rev-parse", "HEAD", cwd=mirror) != lk["blender"]["commit"]:
        run(["git", "checkout", "--detach", lk["blender"]["commit"]], cwd=mirror, env=no_smudge)

    say("Fetching Git LFS assets (excluding test data)")
    run(["git", "lfs", "pull", "--exclude", "tests/**"], cwd=mirror)

    lib_dir = mirror / "lib" / LIB_PLATFORM
    want = lk["libs"][LIB_PLATFORM]["commit"]
    have = git("rev-parse", "HEAD", cwd=lib_dir, check=False) if (lib_dir / ".git").exists() else ""
    if have != want:
        say(f"Fetching precompiled libraries lib/{LIB_PLATFORM} @ {want[:10]} (several GB)")
        run(["git", "-c", f"submodule.lib/{LIB_PLATFORM}.update=checkout", "submodule", "update",
             "--init", "--depth", "1", f"lib/{LIB_PLATFORM}"], cwd=mirror, env=no_smudge)
        have = git("rev-parse", "HEAD", cwd=lib_dir)
        if have != want:
            sys.exit(f"lib/{LIB_PLATFORM} is at {have}, expected {want}. Update upstream.lock.json.")
    run(["git", "lfs", "pull"], cwd=lib_dir)
    say("Sync complete.")


# ------------------------------------------------------------------------ apply


def _clear_overlay_flags(work: pathlib.Path) -> None:
    flagged = [line[2:] for line in git("ls-files", "-v", cwd=work).splitlines() if line.startswith("h ")]
    if flagged:
        run(["git", "update-index", "--no-assume-unchanged", *flagged], cwd=work, quiet=True)


def _overlay_files() -> list[tuple[pathlib.Path, str]]:
    if not OVERLAY.is_dir():
        return []
    return [(src, src.relative_to(OVERLAY).as_posix()) for src in sorted(OVERLAY.rglob("*"))
            if src.is_file() and not (src.name == "README.md" and src.parent == OVERLAY)]


def _upstream_blob(repo: pathlib.Path, commit: str, rel: str) -> str:
    return run(["git", "rev-parse", f"{commit}:{rel}"], cwd=repo, check=False, capture=True,
               quiet=True).stdout.strip()


def _check_overlay_bases(work: pathlib.Path, commit: str) -> list[str]:
    """Overlay files replace whole upstream files. Flag any whose upstream original changed."""
    locked = json.loads(OVERLAY_LOCK.read_text(encoding="utf-8")) if OVERLAY_LOCK.is_file() else {}
    stale = []
    for _src, rel in _overlay_files():
        base = _upstream_blob(work, commit, rel)
        if base and locked.get(rel) != base:
            stale.append(rel)
    return stale


def cmd_overlay_lock(args) -> None:
    lk, p = lock(), paths()
    entries = {}
    for _src, rel in _overlay_files():
        base = _upstream_blob(p["mirror"], lk["blender"]["commit"], rel)
        if base:
            entries[rel] = base
    OVERLAY_LOCK.write_text(json.dumps(entries, indent=2) + "\n", encoding="utf-8", newline="\n")
    say(f"Recorded upstream bases for {len(entries)} overlay file(s) in {OVERLAY_LOCK.relative_to(REPO)}")


def _apply_overlay(work: pathlib.Path) -> int:
    """Copy branding files over the tree. They stay uncommitted and hidden from `git status`."""
    tracked, untracked = [], []
    for src, rel in _overlay_files():
        dst = work / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        is_tracked = run(["git", "ls-files", "--error-unmatch", rel], cwd=work, check=False,
                         capture=True, quiet=True).returncode == 0
        (tracked if is_tracked else untracked).append(rel)
    if tracked:
        run(["git", "update-index", "--assume-unchanged", *tracked], cwd=work, quiet=True)
    if untracked:
        exclude = pathlib.Path(git("rev-parse", "--git-path", "info/exclude", cwd=work))
        if not exclude.is_absolute():
            exclude = work / exclude
        existing = exclude.read_text(encoding="utf-8").splitlines() if exclude.is_file() else []
        with open(exclude, "a", encoding="utf-8") as f:
            for rel in untracked:
                if f"/{rel}" not in existing:
                    f.write(f"/{rel}\n")
    return len(tracked) + len(untracked)


def cmd_apply(args) -> None:
    lk, p = lock(), paths()
    mirror, work, commit = p["mirror"], p["work"], lk["blender"]["commit"]
    if not has_commit(mirror, commit):
        sys.exit("The mirror lacks the locked commit. Run `gsfork sync` first.")
    no_smudge = {"GIT_LFS_SKIP_SMUDGE": "1"}
    if not (work / ".git").exists():
        say(f"Creating worktree {work}")
        run(["git", "worktree", "add", "--force", "--detach", work, commit], cwd=mirror, env=no_smudge)
    else:
        say(f"Resetting worktree {work} to {lk['blender']['tag']}")
        run(["git", "am", "--abort"], cwd=work, check=False, capture=True, quiet=True)
        _clear_overlay_flags(work)
        run(["git", "checkout", "--force", "--detach", commit], cwd=work, env=no_smudge, quiet=True)
        run(["git", "clean", "-fdq"], cwd=work, quiet=True)
    run(["git", "lfs", "checkout"], cwd=work, quiet=True)
    run(["git", "checkout", "-B", PATCH_BRANCH, commit], cwd=work, quiet=True, capture=True)
    # Commits made by `git am` need an identity; keep it local to the worktree.
    run(["git", "config", "user.name", "gsfork"], cwd=work, quiet=True)
    run(["git", "config", "user.email", "gsfork@localhost"], cwd=work, quiet=True)

    names = series()
    for name in names:
        patch = PATCHES / name
        if not patch.is_file():
            sys.exit(f"Missing patch listed in series: {name}")
        result = run(["git", "am", "-3", "--keep-cr", "--committer-date-is-author-date", patch],
                     cwd=work, check=False, capture=True, quiet=True)
        if result.returncode != 0:
            print(result.stdout + result.stderr)
            say(f"FAILED to apply {name}.")
            say(f"Resolve it in {work} (edit, `git add`, `git am --continue`), then run `gsfork export`.")
            sys.exit(1)
        say(f"applied {name}")
    stale = _check_overlay_bases(work, commit)
    overlaid = _apply_overlay(work)
    say(f"Worktree ready: {len(names)} patch(es), {overlaid} overlay file(s) on {lk['blender']['tag']}.")
    if stale:
        say("WARNING: upstream changed these files since their overlay copies were made. Re-derive the "
            "overlay from the new upstream file, then run `gsfork overlay-lock`:")
        for rel in stale:
            print(f"    {rel}")


# ----------------------------------------------------------------------- export


def _slug(subject: str) -> str:
    match = re.match(r"\s*(S\d{2})\s*[:\-]\s*(.*)", subject)
    if not match:
        sys.exit(f"Commit subject must start with a seam id like 'S08: ...': {subject!r}")
    words = re.sub(r"[^a-z0-9]+", "-", match.group(2).lower()).strip("-")
    return f"{match.group(1)}-{words[:50].rstrip('-')}.patch"


def cmd_export(args) -> None:
    lk, p = lock(), paths()
    work, commit = pathlib.Path(args.worktree) if args.worktree else p["work"], lk["blender"]["commit"]
    shas = git("rev-list", "--reverse", f"{commit}..HEAD", cwd=work).split()
    names = []
    for sha in shas:
        subject = git("log", "-1", "--format=%s", sha, cwd=work)
        name = _slug(subject)
        # Bytes, never text: some upstream files carry CRLF in the repository, and a text-mode
        # round trip would silently turn the patch into a corrupt one.
        body = subprocess.run(["git", "format-patch", "-1", "--stdout", "--zero-commit", "--full-index",
                               "--no-signature", "--no-numbered", sha], cwd=work, capture_output=True,
                              check=True).stdout
        (PATCHES / name).write_bytes(body)
        names.append(name)
    for stale in PATCHES.glob("S*.patch"):
        if stale.name not in names:
            stale.unlink()
            say(f"removed stale {stale.name}")
    header = [line for line in SERIES.read_text(encoding="utf-8").splitlines() if line.startswith("#")]
    SERIES.write_text("\n".join(header + names) + "\n", encoding="utf-8", newline="\n")
    say(f"Exported {len(names)} patch(es) to {PATCHES.relative_to(REPO)}")
    budget(names)


def budget(names=None) -> None:
    names = names if names is not None else series()
    added = removed = 0
    files = set()
    for name in names:
        for line in (PATCHES / name).read_text(encoding="utf-8").splitlines():
            if line.startswith("diff --git "):
                files.add(line.split(" b/", 1)[-1])
            elif line.startswith("+") and not line.startswith("+++"):
                added += 1
            elif line.startswith("-") and not line.startswith("---"):
                removed += 1
    say(f"Seam budget: {len(names)}/15 patches, +{added}/-{removed} lines (target <= 250), "
        f"{len(files)} upstream file(s)")


# ------------------------------------------------------------------------ build


def _vs_install() -> pathlib.Path:
    vswhere = pathlib.Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / \
        "Microsoft Visual Studio" / "Installer" / "vswhere.exe"
    out = run([vswhere, "-version", "[17.0,18.0)", "-products", "*", "-requires",
               "Microsoft.VisualStudio.Component.VC.Tools.x86.x64", "-latest", "-property", "installationPath"],
              capture=True, quiet=True).stdout.strip()
    if not out:
        sys.exit("Visual Studio 2022 with the C++ workload was not found.")
    return pathlib.Path(out)


def _find_tool(name: str, extra: list[pathlib.Path]) -> pathlib.Path | None:
    found = shutil.which(name)
    if found:
        return pathlib.Path(found)
    return next((d / f"{name}.exe" for d in extra if (d / f"{name}.exe").is_file()), None)


def cmd_build(args) -> None:
    lk, p = lock(), paths()
    config = args.config or default_config()
    work, build, mirror = p["work"], build_dir(p, config), p["mirror"]
    if not (work / ".git").exists():
        sys.exit("No worktree. Run `gsfork apply` first.")
    vs = _vs_install()
    vcvars = vs / "VC" / "Auxiliary" / "Build" / "vcvarsall.bat"
    ninja = _find_tool("ninja", [vs / "Common7/IDE/CommonExtensions/Microsoft/CMake/Ninja"])
    sccache = _find_tool("sccache", [pathlib.Path(os.environ["LOCALAPPDATA"]) / "Microsoft/WinGet/Links"])
    cmake = _find_tool("cmake", [vs / "Common7/IDE/CommonExtensions/Microsoft/CMake/CMake/bin"])
    if not (ninja and cmake):
        sys.exit("Ninja and CMake are required (both ship with the VS C++ CMake tools).")
    build.mkdir(parents=True, exist_ok=True)
    p["sccache"].mkdir(parents=True, exist_ok=True)

    # Blender's full config, then GameStudio's options, then the config's own (dev turns
    # subsystems off). A changed file means changed options, so it forces a reconfigure.
    includes = [work / "build_files/cmake/config/blender_full.cmake", BUILD_OPTIONS,
                *CONFIGS[config]["extra"]]
    cache_text = f"# Generated by gsfork (config: {config}).\n" + "".join(
        f'include("{path.as_posix()}")\n' for path in includes)
    initial_cache = build / "gs_initial_cache.cmake"
    options_changed = not initial_cache.is_file() or initial_cache.read_text(encoding="utf-8") != cache_text
    initial_cache.write_text(cache_text, encoding="utf-8")

    extra_path = [str(t.parent) for t in (ninja, cmake, sccache) if t]
    env_lines = [
        f'call "{vcvars}" x64 >NUL',
        f'set "PATH={";".join(extra_path)};%PATH%"',
        f'set "SCCACHE_DIR={p["sccache"]}"',
        "set SCCACHE_CACHE_SIZE=50G",
    ]
    configure = not (build / "build.ninja").is_file() or args.reconfigure or options_changed
    steps = []
    if configure:
        cmake_args = [
            f'"{cmake}"', "-G", "Ninja", f'-S "{work}"', f'-B "{build}"', f'-C "{initial_cache}"',
            "-DCMAKE_BUILD_TYPE=Release",
            f'-DLIBDIR="{(mirror / "lib" / LIB_PLATFORM).as_posix()}"',
        ]
        if sccache:
            cmake_args.append("-DWITH_WINDOWS_SCCACHE=ON")
        if NATIVE.is_dir() and (NATIVE / "CMakeLists.txt").is_file():
            cmake_args.append(f'-DGS_NATIVE_DIR="{NATIVE.as_posix()}"')
        cmake_args.append(f'-DGAMESTUDIO_VERSION={lk["gamestudio_version"]}')
        steps.append(" ".join(cmake_args))
    target = args.target or "install"
    keep_going = " -k 0" if args.keep_going else ""
    steps.append(f'"{ninja}" -C "{build}"{keep_going} {target}')

    script = build / "gsfork_build.cmd"
    lines = ["@echo off", *env_lines]
    for step in steps:
        lines += [step, "if errorlevel 1 exit /b 1"]
    script.write_text("\r\n".join(lines) + "\r\n", encoding="utf-8")
    say(f"{'Configuring and building' if configure else 'Building'} in {build}")
    started = time.monotonic()
    result = subprocess.run(["cmd.exe", "/d", "/c", str(script)])
    minutes = (time.monotonic() - started) / 60
    if result.returncode != 0:
        sys.exit(f"[gsfork] build failed after {minutes:.1f} min")
    say(f"Build ({config}) finished in {minutes:.1f} min: {editor_exe(p, config)}")


def default_config() -> str:
    if LOCAL_CONFIG.is_file():
        return json.loads(LOCAL_CONFIG.read_text(encoding="utf-8")).get("config", "dev")
    return "dev"


def build_dir(p, config=None) -> pathlib.Path:
    config = config or default_config()
    return pathlib.Path(str(p["build"]) + CONFIGS[config]["suffix"])


def editor_exe(p, config=None) -> pathlib.Path:
    return build_dir(p, config) / "bin" / "blender.exe"


# ------------------------------------------------------------------- run / test


def _dev_extensions() -> pathlib.Path:
    """The extension lives in blender/gamestudio; tools/blender_dev.py links it where Blender looks."""
    sys.path.insert(0, str(REPO / "tools"))
    import blender_dev

    return blender_dev.dev_extensions_dir()


def shell_env(p, isolate=True) -> dict:
    env = {
        # Load the IDE shell from source so Python edits need no rebuild.
        "BLENDER_SYSTEM_SCRIPTS": str(SHELL / "scripts"),
        "BLENDER_SYSTEM_EXTENSIONS": str(_dev_extensions()),
    }
    if isolate:
        # Never touch the user's real Blender profile from development runs.
        p["userdata"].mkdir(parents=True, exist_ok=True)
        env["BLENDER_USER_RESOURCES"] = str(p["userdata"])
    return env


def cmd_run(args) -> None:
    p = paths()
    exe = editor_exe(p, args.config)
    if not exe.is_file():
        sys.exit(f"No build at {exe}. Run `gsfork build`.")
    extra = args.rest[1:] if args.rest[:1] == ["--"] else args.rest
    cmd = [exe, *extra]
    say(" ".join(str(c) for c in cmd))
    sys.exit(subprocess.run([str(c) for c in cmd], env=dict(os.environ, **shell_env(p))).returncode)


def cmd_test(args) -> None:
    p = paths()
    if args.stock:
        exe = pathlib.Path(args.stock)
    elif args.exe:
        exe = pathlib.Path(args.exe)  # an editor build elsewhere, e.g. a snapshot copy of bin/
    else:
        exe = editor_exe(p, args.config)
    if not exe.is_file():
        sys.exit(f"Editor binary not found: {exe}")
    # (suite, app template). The add-on suite imports and registers the add-on itself, so it must
    # run without the template (which would also enable the installed extension). The shell
    # suite tests the template exactly as users get it.
    suites = [
        (REPO / "blender" / "tests" / "addon" / "run_tests.py", "default"),
        (REPO / "blender" / "tests" / "shell" / "run_shell_tests.py", "GameStudio"),
    ]
    contract = REPO / "blender" / "tests" / "contract" / "run_contract.py"
    if contract.is_file():
        suites.append((contract, "default"))
    failed = []
    for suite, template in suites:
        say(f"Running {suite.relative_to(REPO)} with {exe} (app template: {template})")
        with tempfile.TemporaryDirectory(prefix="gs_test_profile_") as profile:
            env = dict(os.environ, **shell_env(p, isolate=False), BLENDER_USER_RESOURCES=profile,
                       GS_EDITOR_KIND="stock" if args.stock else "fork")
            result = subprocess.run([str(exe), "--background", "--factory-startup", "--app-template", template,
                                     "--python-exit-code", "1", "--python", str(suite)], env=env)
        if result.returncode != 0:
            failed.append(suite.name)
    if failed:
        sys.exit(f"[gsfork] FAILED: {', '.join(failed)}")
    say("All suites passed.")


# ------------------------------------------------------------ generated shell data


def _blender_for_tools(args) -> pathlib.Path:
    """The editor build if there is one, else a stock Blender of the same version."""
    if getattr(args, "blender", None):
        return pathlib.Path(args.blender)
    built = editor_exe(paths())
    if built.is_file():
        return built
    stock = shutil.which("blender")
    for candidate in [stock] + [f"{d}:/Program Files/Blender Foundation/Blender 5.2/blender.exe" for d in "CDEF"]:
        if candidate and pathlib.Path(candidate).is_file():
            return pathlib.Path(candidate)
    sys.exit("No Blender found. Build the editor or pass --blender PATH.")


def _run_blender_script(exe: pathlib.Path, script: pathlib.Path, extra_env: dict, marker: str) -> None:
    with tempfile.TemporaryDirectory(prefix="gs_tool_") as scratch:
        env = dict(os.environ, BLENDER_USER_RESOURCES=scratch, **extra_env)
        result = subprocess.run([str(exe), "--background", "--factory-startup", "--app-template", "default",
                                 "--python-exit-code", "1", "--python", str(script)],
                                env=env, capture_output=True, text=True, encoding="utf-8", errors="replace")
    lines = (result.stdout + result.stderr).splitlines()
    for line in lines:
        if marker in line or "Traceback" in line or "Error" in line:
            print("  " + line)
    if result.returncode != 0 or not any(f"{marker}_OK" in line for line in lines):
        sys.exit(f"[gsfork] {script.name} failed")


def cmd_template(args) -> None:
    exe = _blender_for_tools(args)
    say(f"Generating the GameStudio app template with {exe}")
    _run_blender_script(exe, SHELL / "tools" / "build_template.py",
                        {"BLENDER_SYSTEM_EXTENSIONS": str(_dev_extensions())}, "BUILD_TEMPLATE")


def cmd_branding(args) -> None:
    exe = _blender_for_tools(args)
    say(f"Rendering branding assets with {exe}")
    _run_blender_script(exe, EDITOR / "branding" / "make_branding.py", {}, "BRANDING")


# ----------------------------------------------------------------------- canary


CANARY_REFS = "refs/gsfork/canary"
CANARY_ENV = {"GIT_LFS_SKIP_SMUDGE": "1"}
NATIVE_SOURCE_SUFFIXES = (".c", ".cc", ".cpp", ".h", ".hh", ".hpp", ".mm")


def _canary_ref(ref: str) -> str:
    """Where a canary ref is fetched in the mirror. Nothing else in the mirror is ever written."""
    name = re.sub(r"^refs/(heads|tags)/", "", ref)
    return f"{CANARY_REFS}/" + re.sub(r"[^A-Za-z0-9._/-]", "_", name)


def _rmtree(path: pathlib.Path) -> None:
    def make_writable(func, target, _exc):
        os.chmod(target, 0o700)  # git marks pack and index files read-only on Windows
        func(target)

    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=make_writable)
    else:
        shutil.rmtree(path, onerror=make_writable)


def _canary_clone(mirror: pathlib.Path, tmp: pathlib.Path) -> None:
    """A throw-away repository that reads the mirror's objects through `alternates`.

    The mirror is only read: its refs, index, config, worktree list and object store stay as they
    are. Commits made by `git am` and the blobs of the base pass go into the scratch clone.
    """
    common = pathlib.Path(git("rev-parse", "--path-format=absolute", "--git-common-dir", cwd=mirror))
    run(["git", "init", "--quiet", tmp], quiet=True)
    gitdir = tmp / ".git"
    # Bytes, not text: a CR from Windows newline translation would end up in the path.
    (gitdir / "objects" / "info" / "alternates").write_bytes(((common / "objects").as_posix() + "\n").encode())
    if (common / "shallow").is_file():
        # The mirror is shallow; without its boundary list git would look for missing parents.
        shutil.copyfile(common / "shallow", gitdir / "shallow")
    for key, value in (("core.autocrlf", "false"), ("core.longpaths", "true"), ("rerere.enabled", "false"),
                       ("am.keepcr", "true"), ("user.name", "gsfork"), ("user.email", "gsfork@localhost")):
        run(["git", "config", key, value], cwd=tmp, quiet=True)


def _canary_base_blobs(tmp: pathlib.Path, base: str, names: list[str]) -> list[str]:
    """Apply the series to the locked commit in a scratch index (no checkout).

    This creates every pre-image blob a later patch's `index` line names (S05's base is the tree
    *after* S01, for example), so `git am -3` can fall back to a 3-way merge on the canary ref.
    Returns the patches that do not even apply to their own base.
    """
    env = dict(CANARY_ENV, GIT_INDEX_FILE=str(tmp / ".git" / "gsfork_base_index"))
    run(["git", "read-tree", base], cwd=tmp, env=env, quiet=True)
    broken = []
    for name in names:
        r = run(["git", "apply", "--cached", "--whitespace=nowarn", PATCHES / name], cwd=tmp, env=env,
                check=False, capture=True, quiet=True)
        if r.returncode != 0:
            broken.append(name)
    return broken


def _canary_apply(tmp: pathlib.Path, names: list[str]) -> list[dict]:
    """`git am -3` every patch in order. A failure is recorded and skipped, never fatal."""
    results = []
    for name in names:
        r = run(["git", "am", "-3", "--keep-cr", PATCHES / name], cwd=tmp, env=CANARY_ENV, check=False,
                capture=True, quiet=True)
        log = (r.stdout or "") + (r.stderr or "")
        if r.returncode == 0:
            results.append({"patch": name, "result": "3-way" if "3-way merge" in log else "clean", "files": [],
                            "log": []})
            continue
        conflicted = git("diff", "--name-only", "--diff-filter=U", cwd=tmp, check=False).split()
        results.append({
            "patch": name,
            "result": "conflict" if conflicted else "failed",
            "files": conflicted,
            "log": [line.strip() for line in log.splitlines()
                    if line.startswith(("CONFLICT", "error:", "fatal:", "Patch failed"))],
        })
        run(["git", "am", "--skip"], cwd=tmp, env=CANARY_ENV, check=False, capture=True, quiet=True)
        if (tmp / ".git" / "rebase-apply").exists():
            run(["git", "am", "--abort"], cwd=tmp, check=False, capture=True, quiet=True)
        run(["git", "reset", "--hard", "--quiet", "HEAD"], cwd=tmp, env=CANARY_ENV, check=False, capture=True,
            quiet=True)
    return results


def _canary_build_check(tmp: pathlib.Path) -> list[str]:
    """Quoted includes in editor/native that exist neither in editor/native nor in the patched tree.

    A cheap stand-in for compiling: it catches upstream header renames such as BLI_listbase.h ->
    BLI_listbase.hh (5.3). Matching is by file name, as Blender's include directories are flat.
    """
    tree = {path.rsplit("/", 1)[-1] for path in git("ls-files", cwd=tmp).splitlines()}
    sources = [s for s in sorted(NATIVE.rglob("*")) if s.is_file() and s.suffix in NATIVE_SOURCE_SUFFIXES]
    own = {s.name for s in sources}
    missing = []
    for src in sources:
        for lineno, line in enumerate(src.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            match = re.match(r'\s*#\s*include\s+"([^"]+)"', line)
            if not match:
                continue
            header = match.group(1).rsplit("/", 1)[-1]
            if header not in own and header not in tree:
                missing.append(f"{src.relative_to(REPO).as_posix()}:{lineno}: {match.group(1)}")
    return missing


def cmd_canary(args) -> None:
    """Apply the patch series to an upstream ref and report every seam (docs/fork/ARCHITECTURE.md section 7).

    The build mirror is never checked out or given a worktree: the ref is fetched into
    refs/gsfork/canary/<ref> of the mirror, and the patches are applied in a scratch clone next to
    the work tree that borrows the mirror's objects. Exit code 1 if any patch (or --build-check) fails.
    """
    lk, p = lock(), paths()
    mirror, base = p["mirror"], lk["blender"]["commit"]
    if not (mirror / ".git").exists():
        sys.exit("No mirror. Run `gsfork sync` first.")
    names = series()
    local_ref = _canary_ref(args.ref)
    say(f"Fetching upstream {args.ref} into {local_ref} of the mirror")
    run(["git", "-c", "core.autocrlf=false", "fetch", "--depth", "1", "--no-tags", "--no-write-fetch-head",
         "origin", f"+{args.ref}:{local_ref}"], cwd=mirror, env=CANARY_ENV)
    target = git("rev-parse", f"{local_ref}^{{commit}}", cwd=mirror)
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="gs_canary_", dir=str(p["work"].parent)))
    missing = []
    try:
        _canary_clone(mirror, tmp)
        base_broken = []
        if has_commit(mirror, base):
            base_broken = _canary_base_blobs(tmp, base, names)
        else:
            say(f"WARNING: the mirror lacks the locked commit {base[:10]}; 3-way merges may fail. "
                "Run `gsfork sync`.")
        say(f"Checking out {args.ref} ({target[:10]}) in {tmp}")
        run(["git", "checkout", "--quiet", "--detach", target], cwd=tmp, env=CANARY_ENV, quiet=True)
        results = _canary_apply(tmp, names)
        if args.build_check:
            missing = _canary_build_check(tmp)
    finally:
        if args.keep:
            say(f"Kept the scratch clone {tmp} (delete it when done)")
        else:
            _rmtree(tmp)

    width = max((len(r["patch"]) for r in results), default=0)
    for r in results:
        detail = ", ".join(r["files"]) if r["files"] else "; ".join(r["log"][:2])
        print(f"  {r['result']:9} {r['patch']:{width}}  {detail}".rstrip())
    for name in base_broken:
        print(f"  NOTE      {name} does not apply to its own base {lk['blender']['tag']}; fix the series first")
    applied = [r for r in results if r["result"] in ("clean", "3-way")]
    failed = [r for r in results if r not in applied]
    say(f"Canary vs {args.ref} ({target[:10]}): {len(applied)}/{len(results)} patches apply"
        + (f"; {len(failed)} fail: " + ", ".join(r["patch"].split("-", 1)[0] for r in failed) if failed else ""))
    if args.build_check:
        if missing:
            # Headers the series itself adds are missing when the seam that adds them failed.
            added_by = {}
            for r in failed:
                text = (PATCHES / r["patch"]).read_text(encoding="utf-8", errors="replace")
                for path in re.findall(r"^diff --git a/\S+ b/(\S+)\nnew file mode", text, re.M):
                    added_by[path.rsplit("/", 1)[-1]] = r["patch"].split("-", 1)[0]
            say(f"Build check: {len(missing)} include(s) of editor/native are missing from {args.ref}:")
            for entry in missing:
                header = entry.rsplit(": ", 1)[-1].rsplit("/", 1)[-1]
                note = f"   (added by {added_by[header]}, which failed above)" if header in added_by else ""
                print(f"    {entry}{note}")
        else:
            say(f"Build check: every Blender header editor/native includes exists in {args.ref}")
    if failed or missing:
        sys.exit(1)


# ----------------------------------------------------------------------- package


def cmd_package(args) -> None:
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
    import gsfork_package

    p = paths()
    p["build"] = build_dir(p, args.config)  # releases ship the full config
    try:
        r = gsfork_package.package(p, lock(), pathlib.Path(args.out) if args.out else REPO / "dist",
                                   include_runtime=not args.no_runtime,
                                   upstream_source=not args.no_upstream_source,
                                   verify=args.verify, verify_dir=args.verify_dir,
                                   **gsfork_package.bundle_options(args), log=say)
    except gsfork_package.PackageError as exc:
        sys.exit(f"[gsfork] package failed: {exc}")
    for name, digest in sorted(r["sha256"].items()):
        print(f"  {digest}  {name}")
    if not gsfork_package.verification_ok(r):
        sys.exit("[gsfork] the packaged editor failed verification")


# ----------------------------------------------------------------------- status


def cmd_status(args) -> None:
    lk, p = lock(), paths()
    print(f"GameStudio Editor {lk['gamestudio_version']} on Blender {lk['blender']['tag']} "
          f"({lk['blender']['commit'][:10]})")
    for key in ("mirror", "work", "build", "sccache", "userdata"):
        print(f"  {key:9} {p[key]}  {'(exists)' if p[key].exists() else '(missing)'}")
    lib = p["mirror"] / "lib" / LIB_PLATFORM
    if (lib / ".git").exists():
        have = git("rev-parse", "HEAD", cwd=lib, check=False)
        print(f"  libs      {have[:10]}  {'OK' if have == lk['libs'][LIB_PLATFORM]['commit'] else 'MISMATCH'}")
    if (p["work"] / ".git").exists():
        n = git("rev-list", "--count", f"{lk['blender']['commit']}..HEAD", cwd=p["work"], check=False)
        print(f"  worktree  {n} commit(s) on top of the tag")
    for config in CONFIGS:
        exe = editor_exe(p, config)
        print(f"  {config:9} {exe if exe.is_file() else '(not built)'}")
    budget()


def main() -> None:
    parser = argparse.ArgumentParser(prog="gsfork", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status")
    sub.add_parser("sync")
    sub.add_parser("apply")
    e = sub.add_parser("export")
    e.add_argument("--worktree", help="Export from this worktree instead of the configured one")
    b = sub.add_parser("build")
    b.add_argument("--reconfigure", action="store_true")
    b.add_argument("--target", help="Ninja target (default: install)")
    b.add_argument("--keep-going", action="store_true",
                   help="build everything that does not depend on a failed step, to see every error at once")
    b.add_argument("--config", choices=sorted(CONFIGS), help="dev (default) or full")
    r = sub.add_parser("run")
    r.add_argument("--config", choices=sorted(CONFIGS))
    r.add_argument("rest", nargs=argparse.REMAINDER)
    t = sub.add_parser("test")
    t.add_argument("--stock", help="Path to a stock blender.exe to test the shell's fallback mode")
    t.add_argument("--config", choices=sorted(CONFIGS))
    t.add_argument("--exe", help="Test this GameStudio Editor binary instead of the build's (fork mode)")
    c = sub.add_parser("canary")
    c.add_argument("ref", help="Upstream ref, e.g. main or blender-v5.3-release")
    c.add_argument("--build-check", action="store_true",
                   help="also check that every Blender header editor/native includes exists in that ref")
    c.add_argument("--keep", action="store_true", help="keep the scratch clone (to resolve conflicts there)")
    for name in ("template", "branding"):
        g = sub.add_parser(name)
        g.add_argument("--blender", help="Blender executable (default: the editor build, else stock 5.2)")
    sub.add_parser("overlay-lock")
    k = sub.add_parser("package", help="Zip the editor build plus its GPL source (see docs/fork/RELEASING.md)")
    k.add_argument("--out", help="Output directory (default: dist/)")
    k.add_argument("--config", choices=sorted(CONFIGS), default="full", help="Build to package (default: full)")
    k.add_argument("--verify", action="store_true", help="Extract the zip and smoke-test it with a scratch profile")
    k.add_argument("--verify-dir", help="Where to extract for --verify (default: a temp dir)")
    k.add_argument("--no-runtime", action="store_true", help="Leave out the game runtime and examples")
    k.add_argument("--no-upstream-source", action="store_true",
                   help="Skip the upstream source archive (never for public releases)")
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
    import gsfork_package

    gsfork_package.add_bundle_arguments(k)
    args = parser.parse_args()
    {"status": cmd_status, "sync": cmd_sync, "apply": cmd_apply, "export": cmd_export, "build": cmd_build,
     "run": cmd_run, "test": cmd_test, "canary": cmd_canary, "template": cmd_template,
     "branding": cmd_branding, "overlay-lock": cmd_overlay_lock, "package": cmd_package}[args.command](args)


if __name__ == "__main__":
    main()
