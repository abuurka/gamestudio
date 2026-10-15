# SPDX-License-Identifier: GPL-3.0-or-later
"""Screenshot the Game workspace: .dev/ui-shot/game-workspace.png.

    python tools/screenshot_workspace.py [--blender PATH] [--out PNG] [--timeout SECONDS]

The 03-game-workspace shot of tools/screenshots.py, which has the details: a copy of
examples/demo-game, a UI Blender with --factory-startup, a scratch profile and temp folder and this
checkout's extension, the demo's start scene on the Game workspace with an object that has
components selected and a few game messages in the Console. Nothing is saved to the copied project.
"""

import argparse
import pathlib
import shutil
import sys

import screenshots

OUT = screenshots.REPO / ".dev" / "ui-shot" / "game-workspace.png"
SHOT = "03-game-workspace"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--blender", help="blender executable (default: found automatically)")
    parser.add_argument("--out", type=pathlib.Path, default=OUT, help=f"PNG to write (default: {OUT})")
    parser.add_argument("--timeout", type=float, default=90.0, help="seconds before Blender is stopped")
    args = parser.parse_args()
    out = args.out.resolve()
    taken = [path for path in screenshots.capture(args.blender, [SHOT], args.timeout) if path.stem == SHOT]
    if not taken:
        print("No screenshot was saved")
        return 1
    out.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(taken[0], out)
    print(f"Saved {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
