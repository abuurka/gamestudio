# SPDX-License-Identifier: GPL-3.0-or-later
"""Scaffolds every project template with New Project in a real (stock) Blender.

    python tools/e2e/templates_e2e.py OUT [template ...] [--blender PATH]

Blender runs in the background with a throw-away profile (never your Blender settings) and the
extension from this checkout (tools/blender_dev.py). Online mode is on, so `npm install` may
download what the shared packages lack. tools/e2e/templates_e2e.mjs calls this, then plays each
project in a browser.
"""

import argparse
import pathlib
import subprocess
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import blender_dev  # noqa: E402

SCRIPT = pathlib.Path(__file__).resolve().with_name("templates_e2e_blender.py")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("out", help="folder the projects are created in (one per template)")
    parser.add_argument("templates", nargs="*", help="template ids (default: all)")
    parser.add_argument("--blender", help="blender executable (default: found automatically)")
    args = parser.parse_args()
    exe = blender_dev.find_blender(args.blender)
    with tempfile.TemporaryDirectory(prefix="gs_templates_profile_") as profile:
        cmd = [str(exe), "--background", "--factory-startup", "--online-mode", "--python-exit-code", "1",
               "--python", str(SCRIPT), "--", str(pathlib.Path(args.out).resolve()), *args.templates]
        return subprocess.run(cmd, env=blender_dev.dev_env(profile), cwd=blender_dev.REPO).returncode


if __name__ == "__main__":
    sys.exit(main())
