# SPDX-License-Identifier: GPL-3.0-or-later
"""Small helpers shared by the editor providers."""

import bpy


def visible_rows(context, minimum: int, maximum: int, row_px: float = 22.0) -> int:
    """How many rows of about row_px pixels fit in the region, clamped to [minimum, maximum].

    A full editor region shows more than the sidebar; without a region (tests,
    background mode) this is `minimum`.
    """
    region = getattr(context, "region", None)
    height = getattr(region, "height", 0) or 0
    try:
        scale = bpy.context.preferences.system.ui_scale or 1.0
    except AttributeError:
        scale = 1.0
    rows = int(height / (row_px * scale)) if height > 1 else 0
    return max(minimum, min(maximum, rows))
