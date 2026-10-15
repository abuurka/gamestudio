# SPDX-License-Identifier: GPL-3.0-or-later
"""Compatibility shims for bpy differences between Blender releases.

This is the Python counterpart of editor/native/blender_adapter/: when a Blender
upgrade changes a Python API the shell uses, the fix goes here, once.
"""

import bpy


def gpu_backend() -> str:
    try:
        import gpu

        return gpu.platform.backend_type_get()
    except Exception:  # background mode without a GPU context, or older API
        return "NONE"


def system_properties(id_data):
    """Add-on (bpy.props) storage of an ID. Separate from custom properties since Blender 5.0."""
    getter = getattr(id_data, "bl_system_properties_get", None)
    return getter() if getter is not None else id_data


def user_config_dir() -> str:
    """Per-user config folder of the running app (the fork uses its own, see seam S02)."""
    return bpy.utils.user_resource("CONFIG", create=True)


def online_access() -> bool:
    return bool(getattr(bpy.app, "online_access", True))
