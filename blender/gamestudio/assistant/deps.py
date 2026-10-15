# SPDX-License-Identifier: GPL-3.0-or-later
"""Optional dependency handling for the Anthropic SDK.

Only the Anthropic (Claude) provider needs it. The OpenAI-compatible provider
(providers.py) uses the standard library, so it needs nothing installed.

The assistant is optional, so the SDK is not bundled with the add-on. On request
it is installed with Blender's own Python into a private folder, which keeps it
out of Blender's shared extension site-packages (see research/04 section 10).

When the shell runs as an extension (always, in GameStudio Editor), that folder is
the extension's user folder in the GameStudio profile, so nothing is ever written
into the install folder. Loaded from a source checkout as a plain add-on, it is
``assistant/_vendor`` next to this file.
"""

import pathlib
import subprocess
import sys

SDK_REQUIREMENT = "anthropic>=1.12,<2"
_LOCAL_VENDOR_DIR = pathlib.Path(__file__).resolve().parent / "_vendor"


def _user_vendor_dir() -> pathlib.Path | None:
    import bpy

    extension = (__package__ or "").rpartition(".")[0]  # "bl_ext.<repo>.gamestudio"
    if not extension.startswith("bl_ext."):
        return None
    try:
        return pathlib.Path(bpy.utils.extension_path_user(extension, path="vendor", create=False))
    except (ValueError, TypeError, AttributeError):
        return None


# Resolved at import (registration, on the main thread); install_sdk runs on a worker thread.
VENDOR_DIR = _user_vendor_dir() or _LOCAL_VENDOR_DIR


def _add_vendor_path():
    # The checkout folder too, for SDKs installed there before the user folder was used.
    for folder in dict.fromkeys((VENDOR_DIR, _LOCAL_VENDOR_DIR)):
        path = str(folder)
        if folder.is_dir() and path not in sys.path:
            # Appended, so packages Blender already ships keep precedence.
            sys.path.append(path)


def sdk_available() -> bool:
    _add_vendor_path()
    try:
        import anthropic  # noqa: F401
    except ImportError:
        return False
    return True


def install_sdk() -> tuple[bool, str]:
    """Install the SDK into the vendor directory. Blocking: call from a worker thread."""
    VENDOR_DIR.mkdir(parents=True, exist_ok=True)
    cmd = [
        sys.executable, "-m", "pip", "install",
        "--disable-pip-version-check", "--no-input", "--upgrade",
        "--target", str(VENDOR_DIR),
        SDK_REQUIREMENT,
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"pip failed to run: {exc}"
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout).strip().splitlines()[-5:]
        return False, "pip install failed:\n" + "\n".join(tail)
    _add_vendor_path()
    return True, "Installed the Anthropic SDK."
