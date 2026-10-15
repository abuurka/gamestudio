# SPDX-License-Identifier: GPL-3.0-or-later
"""GameStudio in Blender's top bar: the play buttons and the agent's button.

  Play, Pause, Step, Stop   in the middle of the top bar, like a game engine's, while a game project
                            is open: Play stays pressed while the game runs, Pause while it is paused
                            (the operators behind F5-F8, keymap.py). The middle is the free space
                            right of the workspace tabs; when the window is too narrow for them there,
                            they move right, next to the scene selector, which Blender never clips.
  Game display              right of them, one icon for where Play shows the game (prefs.game_display):
                            a picture in the 3D View (Embedded: streamed into Blender) or a window
                            (Window). Click it to switch; its tooltip says which is on.
  Agent                     right of them, left of the scene selector: who is connected, and an icon
                            for what the agent does; while none is, the agent of the "Coding agent"
                            preference (Claude or Codex), unlinked. Click it for the Agent popover
                            (activity_ui.py): Open in Claude Code or Open in Codex while no agent is
                            connected, recent actions, Undo to Here, Pause, Protect, the activity log.

Both are drawn by functions added to TOPBAR_HT_upper_bar (one appended, for its left region, one
prepended, for its right region), which draws Blender's own items unchanged. Python cannot tag the
top bar for a redraw (it is not in Screen.areas), so a game state that changes on its own (the game
paused itself, F5-F8 from another editor) shows when Blender next redraws it: a click or hover there,
a scene or workspace change; the status bar shows it at once. The status bar items
are ui.draw_statusbar (the game, the dev server) and activity_ui.draw_statusbar (the agent, who is
connected included); the GameStudio and Game menus are menus.py.
"""

import blf
import bpy

from . import activity_ui
from .core import link, project


PLAY_UNITS = 6.0       # the play buttons, in UI units: four icon buttons, the display toggle, a little air
MARGIN_UNITS = 3.0     # free space they keep on both sides together, or they move right
TAB_PAD_UNITS = 1.0    # a workspace tab's padding around its name
MENU_PAD_UNITS = 0.8   # a top bar menu's padding around its name
MENUS = ("File", "Edit", "Render", "Window", "Help", "GameStudio", "Game")


# --- State -------------------------------------------------------------------------------------


def game_running() -> tuple[bool, str | None]:
    """(a game is connected, its state: "playing", "paused", "loading", "stopped" or None)."""
    client = link.get_link()
    running = bool(client is not None and client.connected and client.game_peers())
    return running, (client.game_state if running else None)


def show_play() -> bool:
    return project.project_root() is not None


def show_agent() -> bool:
    """The agent's button: in a project, or once the agent bridge is on or the agent did something."""
    return project.project_root() is not None or activity_ui.status_item() is not None


# --- Where the play buttons go -----------------------------------------------------------------


def _unit(context) -> float:
    """One UI unit in pixels (20 at 100%, scaled with the resolution and the line width)."""
    return 20.0 * (context.preferences.system.ui_scale or 1.0)


def _text_width(text: str, points: float) -> float:
    try:
        blf.size(0, points)
        return blf.dimensions(0, text)[0]
    except Exception:  # no font (should not happen): a typical average glyph
        return len(text) * points * 0.6


def _left_content_width(context, unit: float) -> float:
    """About how wide Blender's own items in the left part of the top bar are: the menus, then the
    workspace tabs (whose width depends on their names)."""
    points = context.preferences.ui_styles[0].widget.points * (context.preferences.system.ui_scale or 1.0)
    area = getattr(context, "area", None)
    if area is not None and not getattr(area, "show_menus", True):
        width = 1.5 * unit  # collapsed into one menu button
    else:
        width = sum(_text_width(name, points) + MENU_PAD_UNITS * unit for name in MENUS)
    width += unit  # the separator
    for workspace in bpy.data.workspaces:
        width += _text_width(workspace.name, points) + TAB_PAD_UNITS * unit
    return width


def _left_region(context):
    area = getattr(context, "area", None)
    regions = getattr(area, "regions", ()) if area is not None else ()
    return next((r for r in regions if r.type == "HEADER" and r.alignment != "RIGHT"), None)


def play_in_middle(context) -> bool:
    """Whether the play buttons fit right of the workspace tabs. Too narrow, they go right of them
    in the part Blender sizes to its content (the left part scrolls and would hide them)."""
    region = _left_region(context)
    if region is None:
        return True
    unit = _unit(context)
    return region.width >= _left_content_width(context, unit) + (PLAY_UNITS + MARGIN_UNITS) * unit


# --- Drawing -----------------------------------------------------------------------------------


def draw_play_controls(layout, context) -> None:
    """Play, Pause, Step and Stop. Play stays pressed while a game runs (paused too), Pause while it
    is paused; Pause, Step and Stop need a running game. Then the game display toggle."""
    running, state = game_running()
    row = layout.row(align=True)
    row.operator("gamestudio.play", text="", icon="PLAY", depress=state in ("playing", "paused"))
    sub = row.row(align=True)
    sub.enabled = running
    sub.operator("gamestudio.pause", text="", icon="PAUSE", depress=state == "paused")
    sub.operator("gamestudio.step", text="", icon="FRAME_NEXT")
    sub.operator("gamestudio.stop", text="", icon="SNAP_FACE")
    draw_display_toggle(layout)


def draw_display_toggle(layout) -> None:
    """One icon for where Play shows the game; a click switches Embedded and Window."""
    from .editors import game_stream

    layout.operator(game_stream.GAMESTUDIO_OT_toggle_game_display.bl_idname, text="",
                    icon=game_stream.DISPLAY_ICONS.get(game_stream.display(), "WINDOW"))


def draw_agent_button(layout, context) -> None:
    """The agent's status; a click opens the Agent popover."""
    text, icon, alert, active = activity_ui.topbar_item()
    row = layout.row(align=True)
    row.alert = alert
    row.active = active
    row.popover(panel="GAMESTUDIO_PT_agent_popover", text=text, icon=icon)


def draw_upper_bar_left(self, context):
    """Appended to the top bar: the play buttons in the free space right of the workspace tabs."""
    region = getattr(context, "region", None)
    if region is None or region.alignment == "RIGHT" or not show_play() or not play_in_middle(context):
        return
    layout = self.layout
    layout.separator_spacer()
    draw_play_controls(layout, context)
    layout.separator_spacer()


def draw_upper_bar_right(self, context):
    """Prepended to the top bar's right part: the play buttons when the window is too narrow for
    them in the middle, then the agent's button, left of the scene selector."""
    region = getattr(context, "region", None)
    if region is None or region.alignment != "RIGHT":
        return
    layout = self.layout
    drawn = False
    if show_play() and not play_in_middle(context):
        draw_play_controls(layout, context)
        drawn = True
    if show_agent():
        draw_agent_button(layout, context)
        drawn = True
    if drawn:
        layout.separator(type="LINE")


# (header, function, prepend?)
_ADDITIONS = (
    ("TOPBAR_HT_upper_bar", draw_upper_bar_left, False),
    ("TOPBAR_HT_upper_bar", draw_upper_bar_right, True),
)


def register():
    for name, func, first in _ADDITIONS:
        owner = getattr(bpy.types, name)
        (owner.prepend if first else owner.append)(func)


def unregister():
    for name, func, _first in reversed(_ADDITIONS):
        owner = getattr(bpy.types, name, None)
        if owner is not None:
            owner.remove(func)
