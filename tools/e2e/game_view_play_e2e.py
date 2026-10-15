# SPDX-License-Identifier: GPL-3.0-or-later
"""Plays the game in the docked Game View, end to end (Windows).

  python tools/e2e/game_view_play_e2e.py [--config dev|full] [--only play|cpu] [--out DIR]

Runs GameStudio Editor with its UI, a scratch profile and the IDE shell from this checkout on
the demo level, driving tools/e2e/blender_game_view_play.py:
  play  with --enable-event-simulate (the real mouse and keyboard are ignored): input capture,
        keys, mouse, wheel, pointer lock, release; resize, two views, workspace pause, detach,
        host and renderer crashes, Restart, start/stop cycles, DevTools; then the editor quits
        with the game running and this driver checks that no host process survives it.
  cpu   without event simulation: the editor's CPU use and which regions redraw while a game
        runs docked, hidden and stopped.
Play uses the demo's dev server (started if needed). Exits non-zero if any check fails.
"""

import argparse
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import time

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "editor" / "tools"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import gsfork  # noqa: E402
import gv_procs  # noqa: E402

SCRIPT = REPO / "tools" / "e2e" / "blender_game_view_play.py"


def run_editor(exe: pathlib.Path, mode: str, out_dir: pathlib.Path, timeout: int) -> dict | None:
    result = out_dir / f"{mode}.json"
    result.unlink(missing_ok=True)
    with tempfile.TemporaryDirectory(prefix="gs_gameview_play_") as profile:
        env = dict(os.environ, **gsfork.shell_env(gsfork.paths(), isolate=False),
                   BLENDER_USER_RESOURCES=profile, GS_EDITOR_KIND="fork")
        cmd = [str(exe), "--factory-startup", "--window-geometry", "0", "0", "1600", "900"]
        if mode == "play":
            cmd.append("--enable-event-simulate")
        cmd += [str(REPO / "examples" / "demo-game" / "scenes" / "main.blend"),
                "--python", str(SCRIPT), "--", str(result), mode]
        log = out_dir / f"{mode}.log"
        with open(log, "w", encoding="utf-8", errors="replace") as log_file:
            proc = subprocess.Popen(cmd, env=env, cwd=REPO, stdout=log_file, stderr=subprocess.STDOUT)
            try:
                proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                proc.kill()  # its job objects take the game hosts with it
                proc.wait()
                print(f"[gameview-play] {mode}: the editor did not finish within {timeout}s (killed)")
    output = log.read_text(encoding="utf-8", errors="replace")
    for line in output.splitlines():
        if line.startswith("GVPLAY ") or "Traceback" in line or "Error:" in line:
            print(line)
    if not result.is_file():
        print(output[-4000:])
        print(f"[gameview-play] {mode}: no result written (log: {log})")
        return None
    return json.loads(result.read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", choices=sorted(gsfork.CONFIGS))
    parser.add_argument("--exe", help="GameStudio Editor binary to test (default: the build's)")
    parser.add_argument("--only", choices=("play", "cpu"))
    parser.add_argument("--out", help="Where to write results, logs and screenshots (default: a temp dir)")
    parser.add_argument("--timeout", type=int, default=480)
    args = parser.parse_args()

    exe = pathlib.Path(args.exe) if args.exe else gsfork.editor_exe(gsfork.paths(), args.config)
    if not exe.is_file():
        sys.exit(f"No editor build at {exe}. Run: python editor/tools/gsfork.py build")
    out_dir = pathlib.Path(args.out) if args.out else pathlib.Path(tempfile.mkdtemp(prefix="gs_gameview_play_"))
    out_dir.mkdir(parents=True, exist_ok=True)

    checks = []
    for mode in ("play", "cpu"):
        if args.only and args.only != mode:
            continue
        data = run_editor(exe, mode, out_dir, args.timeout)
        if data is None:
            checks.append({"name": f"{mode}_result_written", "ok": False})
            continue
        checks += data["checks"]
        if mode == "play":
            # The editor quit with the game running: its whole host tree must be gone.
            tree = data.get("quit_descendants", [])
            deadline = time.monotonic() + 10
            while gv_procs.still_alive(tree) and time.monotonic() < deadline:
                time.sleep(0.2)
            left = gv_procs.still_alive(tree)
            ok = bool(tree) and not left
            checks.append({"name": "quit_leaves_no_host_processes", "ok": ok})
            print(f"GVPLAY quit_leaves_no_host_processes {'PASS' if ok else 'FAIL'} "
                  f"{len(tree)} processes while running, left: {left}")
        for name, value in data.get("measurements", {}).items():
            (out_dir / "measurements.json").open("a", encoding="utf-8").write(
                json.dumps({name: value}, default=str) + "\n")

    failed = [c["name"] for c in checks if not c["ok"]]
    print(f"[gameview-play] {len(checks) - len(failed)}/{len(checks)} checks passed"
          + (f"; failed: {', '.join(failed)}" if failed else "") + f". Output: {out_dir}")
    return 1 if failed or not checks else 0


if __name__ == "__main__":
    sys.exit(main())
