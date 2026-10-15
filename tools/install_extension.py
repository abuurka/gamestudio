#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Install the GameStudio extension zip into a Blender 5.2+ and enable it.

    python tools/install_extension.py                       # build dist/gamestudio-<version>.zip, install it
    python tools/install_extension.py --zip PATH            # install a zip you already have
    python tools/install_extension.py --blender PATH        # into this Blender
    python tools/install_extension.py --profile DIR         # into a scratch profile (BLENDER_USER_RESOURCES)

It runs `blender --command extension install-file -r user_default -e <zip>`, the same as
Edit > Preferences > Get Extensions > Install from Disk. Without --profile that is your own
Blender profile. Restart Blender afterwards; GameStudio's menus are in the top bar. To remove it:
Preferences > Get Extensions, or `blender --command extension remove gamestudio`.
"""

import argparse
import os
import pathlib
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))

import blender_dev  # noqa: E402
import build_extension  # noqa: E402


def install(blender: pathlib.Path, package: pathlib.Path, profile: pathlib.Path | None = None) -> int:
    env = dict(os.environ)
    if profile is not None:
        profile.mkdir(parents=True, exist_ok=True)
        env["BLENDER_USER_RESOURCES"] = str(profile)
    cmd = [str(blender), "--command", "extension", "install-file", "-r", "user_default", "-e", str(package)]
    print("$", " ".join(f'"{c}"' if " " in c else c for c in cmd))
    # Blender (and other add-ons) print UTF-8; never decode with the Windows code page.
    result = subprocess.run(cmd, env=env, capture_output=True, encoding="utf-8", errors="replace")
    out = ((result.stdout or "") + (result.stderr or "")).strip()
    if out:
        print(out)
    # Blender exits with 0 even when the zip is no extension or cannot be unpacked.
    if result.returncode == 0 and 'Installed "gamestudio"' not in out:
        return 1
    return result.returncode


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--blender", help="blender executable (default: found automatically)")
    parser.add_argument("--zip", help="the extension zip (default: build it with tools/build_extension.py)")
    parser.add_argument("--profile", help="a scratch Blender profile folder (BLENDER_USER_RESOURCES)")
    args = parser.parse_args()
    blender = blender_dev.find_blender(args.blender)
    package = pathlib.Path(args.zip) if args.zip else build_extension.build(blender)
    if not package.is_file():
        sys.exit(f"{package} does not exist")
    code = install(blender, package, pathlib.Path(args.profile).resolve() if args.profile else None)
    if code != 0:
        sys.exit(f"Installing failed (exit code {code})")
    print("Installed and enabled. Restart Blender: GameStudio and Game are in the top bar menus.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
