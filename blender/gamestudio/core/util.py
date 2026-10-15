# SPDX-License-Identifier: GPL-3.0-or-later
"""Small helpers shared by the core modules."""

import threading

import bpy


def is_main_thread() -> bool:
    return threading.current_thread() is threading.main_thread()


def tag_redraw(area_types=("VIEW_3D",), region_type="UI") -> None:
    """Redraw the sidebar (or region_type=None: whole areas) of the given area types, and every
    Properties editor (GameStudio panels in the Object and Scene tabs).

    On GameStudio Editor the custom editors (Inspector, Console, ...) show the
    same state, so they are redrawn too. Safe in background mode and a no-op off
    the main thread.
    """
    if not is_main_thread():
        return
    from ..host import editors

    if editors.available():
        editors.tag_redraw()
    wm = getattr(bpy.context, "window_manager", None)
    if wm is None:
        return
    for window in wm.windows:
        screen = window.screen
        if screen is None:
            continue
        for area in screen.areas:
            if area.type == "PROPERTIES":  # the Object and Scene tabs show GameStudio state too
                area.tag_redraw()
                continue
            if area.type not in area_types:
                continue
            if region_type is None:
                area.tag_redraw()
                continue
            for region in area.regions:
                if region.type == region_type:
                    region.tag_redraw()


def refresh_status_bar() -> None:
    """Redraw the status bar of every window now (after what a status bar item shows has changed).

    GameStudio Editor redraws it with its editors. Stock Blender has no Python access to the status
    bar region, but clearing a window's workspace status text redraws it; windows where a running
    tool shows its own status text are left alone. Safe in background mode and a no-op off the main
    thread.
    """
    if not is_main_thread():
        return
    from ..host import editors

    if editors.available():
        editors.tag_redraw()
        return
    wm = getattr(bpy.context, "window_manager", None)
    for window in getattr(wm, "windows", ()):
        if len(getattr(window, "modal_operators", ())) or window.workspace is None:
            continue  # a running tool shows its own status text
        setter = getattr(window.workspace, "status_text_set_internal", None)
        if setter is not None:
            with bpy.context.temp_override(window=window):
                setter(None)


def any_window():
    """The context window, or the first window of the window manager, or None."""
    window = getattr(bpy.context, "window", None)
    if window is not None:
        return window
    wm = getattr(bpy.context, "window_manager", None)
    if wm is not None and len(wm.windows):
        return wm.windows[0]
    return None
