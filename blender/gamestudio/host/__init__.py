# SPDX-License-Identifier: GPL-3.0-or-later
"""The Host API: one facade between the IDE shell and whatever Blender it runs in.

Shell code asks *what is possible* (`host.has("game_view")`) instead of checking
version numbers, uses `host.compat` for API differences between Blender
releases and `host.editors` for custom editors (real editors on the fork,
sidebar tabs on stock). On GameStudio Editor the native module `_gamestudio` provides the
extra capabilities; on stock Blender every capability falls back to what Python
can do. This is why the shell works on both (docs/fork/ARCHITECTURE.md section 2).
"""

import bpy

from . import compat

try:  # Present only in GameStudio Editor builds (seam S01 + editor/native).
    import _gamestudio as _native
except ImportError:
    _native = None

_EXTENSION_ID = __package__.rsplit(".", 1)[0]


def native():
    """The `_gamestudio` module, or None on stock Blender."""
    return _native


def is_editor_build() -> bool:
    return _native is not None


def capabilities() -> set[str]:
    caps = {"live_link", "components", "assistant"}
    if _native is not None:
        caps |= set(_native.capabilities())
    return caps


def has(capability: str) -> bool:
    return capability in capabilities()


def gamestudio_version() -> str:
    if _native is not None:
        return _native.version()
    try:
        import tomllib
        import pathlib

        manifest = pathlib.Path(__file__).resolve().parents[1] / "blender_manifest.toml"
        return tomllib.loads(manifest.read_text(encoding="utf-8"))["version"]
    except (OSError, KeyError, ValueError):
        return "0.0.0"


def info() -> dict:
    prefs = bpy.context.preferences
    return {
        "product": "GameStudio Editor" if _native is not None else "GameStudio for Blender",
        "gamestudio": gamestudio_version(),
        "blender": tuple(bpy.app.version),
        "native": _native is not None,
        "capabilities": sorted(capabilities()),
        "app_template": prefs.app_template if hasattr(prefs, "app_template") else "",
        "gpu_backend": compat.gpu_backend(),
    }


# Imported last: host.editors and host.game_view use capabilities() and native() from this module.
from . import editors  # noqa: E402, F401 (public: host.editors)
from . import game_view  # noqa: E402, F401 (public: host.game_view)
