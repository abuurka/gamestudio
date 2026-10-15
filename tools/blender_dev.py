# SPDX-License-Identifier: GPL-3.0-or-later
"""Run Blender with the GameStudio extension straight from this checkout.

Blender loads "system" extensions from BLENDER_SYSTEM_EXTENSIONS/system/<name>. This module keeps
`.dev/extensions/system/gamestudio` pointing at `blender/gamestudio` (a directory junction on
Windows, a symlink elsewhere), so edits to the extension need no install step: restart Blender.

Standard library only; used by tools/test_blender.py, the e2e tools and gsfork.
"""

import os
import pathlib
import shutil
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
EXTENSION = REPO / "blender" / "gamestudio"
TESTS = REPO / "blender" / "tests"
DEV_EXTENSIONS = REPO / ".dev" / "extensions"
# The shelved fork's app template. No suite uses it any more; dev_env(template=True) still can.
TEMPLATE_SCRIPTS = REPO / "editor" / "shell" / "scripts"


def dev_extensions_dir() -> pathlib.Path:
    """BLENDER_SYSTEM_EXTENSIONS for this checkout: <dir>/system/gamestudio -> blender/gamestudio."""
    link = DEV_EXTENSIONS / "system" / "gamestudio"
    target = EXTENSION.resolve()
    if os.path.lexists(link):
        try:
            if link.resolve() == target:
                return DEV_EXTENSIONS
        except OSError:
            pass
        if not _is_link(link):
            shutil.rmtree(link)
        elif sys.platform == "win32":
            os.rmdir(link)  # a junction: removes the link, never its target
        else:
            os.unlink(link)  # rmdir refuses a symlink to a directory
    link.parent.mkdir(parents=True, exist_ok=True)
    if sys.platform == "win32":
        import _winapi

        _winapi.CreateJunction(str(target), str(link))  # no admin rights needed
    else:
        os.symlink(target, link, target_is_directory=True)
    return DEV_EXTENSIONS


def _is_link(path: pathlib.Path) -> bool:
    return os.path.islink(path) or (hasattr(os.path, "isjunction") and os.path.isjunction(path))


def find_blender(explicit: str | None = None) -> pathlib.Path:
    """Blender 5.2+: an explicit path, $BLENDER, `blender` on PATH, or the usual install folders."""
    candidates = [explicit, os.environ.get("BLENDER"), shutil.which("blender")]
    if sys.platform == "win32":
        for drive in "CDEFG":
            for version in ("5.4", "5.3", "5.2"):
                candidates.append(f"{drive}:/Program Files/Blender Foundation/Blender {version}/blender.exe")
    elif sys.platform == "darwin":
        candidates.append("/Applications/Blender.app/Contents/MacOS/Blender")
    for candidate in candidates:
        if candidate and pathlib.Path(candidate).is_file():
            return pathlib.Path(candidate)
    sys.exit("Blender 5.2+ not found. Pass --blender PATH or set BLENDER.")


def dev_env(profile: str | os.PathLike, template: bool = False) -> dict:
    """Environment for a development run: the extension from this checkout, a scratch profile
    (never the user's Blender settings) and, with `template`, the fork's app template."""
    env = dict(os.environ, BLENDER_SYSTEM_EXTENSIONS=str(dev_extensions_dir()),
               BLENDER_USER_RESOURCES=str(profile))
    # Blender writes quit.blend (File > Recover > Last Session) and autosaves to the temp folder: a
    # scratch Blender must not replace the user's.
    temp = pathlib.Path(profile) / "tmp"
    temp.mkdir(parents=True, exist_ok=True)
    env.update(TEMP=str(temp), TMP=str(temp), TMPDIR=str(temp))
    if template:
        env["BLENDER_SYSTEM_SCRIPTS"] = str(TEMPLATE_SCRIPTS)
    return env
