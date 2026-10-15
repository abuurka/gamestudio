# SPDX-License-Identifier: GPL-3.0-or-later
"""End-to-end check of the docked Game View in GameStudio Editor (Windows).

  python tools/e2e/game_view_e2e.py [--config dev|full] [--out DIR]

Starts the editor build with its UI, a scratch profile and the IDE shell from this checkout,
opens the demo level and runs tools/e2e/blender_game_view.py: Game View, Play, frames,
pause/resume, screenshot, stop. Play uses the demo's dev server (started if needed).
Exits non-zero if any check fails. Needs the editor built (gsfork build) and electron
installed (npm install).
"""

import argparse
import json
import os
import pathlib
import subprocess
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "editor" / "tools"))

import gsfork  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", choices=sorted(gsfork.CONFIGS))
    parser.add_argument("--exe", help="GameStudio Editor binary to test (default: the build's)")
    parser.add_argument("--out", help="Where to write result.json and result.png (default: a temp dir)")
    parser.add_argument("--timeout", type=int, default=240)
    args = parser.parse_args()

    exe = pathlib.Path(args.exe) if args.exe else gsfork.editor_exe(gsfork.paths(), args.config)
    if not exe.is_file():
        sys.exit(f"No editor build at {exe}. Run: python editor/tools/gsfork.py build")
    out_dir = pathlib.Path(args.out) if args.out else pathlib.Path(tempfile.mkdtemp(prefix="gs_gameview_e2e_"))
    out_dir.mkdir(parents=True, exist_ok=True)
    result = out_dir / "result.json"
    result.unlink(missing_ok=True)

    with tempfile.TemporaryDirectory(prefix="gs_gameview_profile_") as profile:
        env = dict(os.environ, **gsfork.shell_env(gsfork.paths(), isolate=False),
                   BLENDER_USER_RESOURCES=profile, GS_EDITOR_KIND="fork")
        cmd = [str(exe), "--factory-startup", "--window-geometry", "0", "0", "1600", "900",
               str(REPO / "examples" / "demo-game" / "scenes" / "main.blend"),
               "--python", str(REPO / "tools" / "e2e" / "blender_game_view.py"), "--", str(result)]
        try:
            proc = subprocess.run(cmd, env=env, cwd=REPO, timeout=args.timeout,
                                  capture_output=True, text=True, encoding="utf-8", errors="replace")
            output = proc.stdout + proc.stderr
        except subprocess.TimeoutExpired as exc:
            output = (exc.stdout or "") if isinstance(exc.stdout, str) else ""
            print(output[-4000:])
            print(f"[gameview-e2e] the editor did not finish within {args.timeout}s")
            return 1

    for line in output.splitlines():
        if line.startswith("GAMEVIEW ") or "Error" in line or "Traceback" in line:
            print(line)
    if not result.is_file():
        print(output[-4000:])
        print("[gameview-e2e] no result written")
        return 1
    data = json.loads(result.read_text(encoding="utf-8"))
    failed = [c["name"] for c in data["checks"] if not c["ok"]]
    print(f"[gameview-e2e] {len(data['checks']) - len(failed)}/{len(data['checks'])} checks passed"
          + (f"; failed: {', '.join(failed)}" if failed else "") + f". Output: {out_dir}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
