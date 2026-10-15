# SPDX-License-Identifier: MIT-0
"""Builds ui/ui.blend, the platformer's menus and HUD (docs: the design-ui skill, API.md "Menus and screens").

New Project runs build(gs, root) once, after `npm run registry` (the UI components are engine built-ins in the
project's registry). Each Blender scene of ui/ui.blend is a screen (a canvas):

  Title     the game's name, "Sky Garden: reach the golden star", the controls, Play and Options; opens on the
            first start after the page loads and holds the game until Play (playtests press Enter first)
  Pause     the pause menu (Escape, P or gamepad Start; Escape resumes): Resume, Options, Restart
  Options   Music and Sound sliders (the audio.music and audio.sfx channel volumes, saved by the engine), Back
  Win       opened by GoalSystem (ctx.ui.open('Win')): the time, the coins, the best time or "New best time!"
  GameOver  opened by RespawnSystem when the last life is lost: the coins, Try again
  HUD       on every start: the coins, the lives as hearts and the clock

Texts show the game's values through bindings: {coins}, {coinTotal}, {time:m:ss} and {newBest} are ctx.store
values that the systems set (src/lib/level.ts lists them), {save.bestTime} is ctx.save. No code draws them.

It refuses when ui/ui.blend exists: edit that file (Edit Game UI, or the ui_* tools) rather than rebuilding it.
To build it again, delete ui/ui.blend (losing its edits), save the open file, then:

  Coding agent (MCP): run_python with
                        import runpy, pathlib; runpy.run_path(r"<project>/tools/build_ui.py")["build"](gs, pathlib.Path(r"<project>"))
                      (it opens the new UI file in Blender: open_scene goes back to the level)
  A terminal:         blender --background --python tools/build_ui.py
"""

import pathlib

import bpy

PROJECT = pathlib.Path(__file__).resolve().parents[1]

# The starter's look: one font (Blender's Inter, copied to assets/fonts/), one accent (the focus yellow), dark
# translucent cards, on a 1920 x 1080 frame. The icons are the starter's, in assets/ui/.
ACCENT = "#FFD400"
SOFT = "#E6EAF2"  # subtitles and values
MUTED = "#B8C1CF"  # hints
DANGER = "#FF6B6B"  # Game Over
CARD = {"color": "#12161FE6", "radius": 40, "border": 2, "border_color": "#FFFFFF1F", "anchor": "center"}
PILL = {"color": "#0000008C", "radius": 36}  # HUD backgrounds
BACKDROP = (0.0, 0.0, 0.0, 0.55)  # linear RGBA over the game, behind the menus
CLICK = "assets/sfx/click.wav"  # every button and slider
CONTROLS = "WASD move · Space jump · Esc pause · R restart"
LIVES = 5  # hearts on the HUD: START_LIVES in src/lib/level.ts


def build(gs, root=PROJECT) -> pathlib.Path:
    root = pathlib.Path(root)
    path = gs.gameui.ui_file(root)
    if path.exists():
        raise ValueError(f"{path} already exists: edit it, or delete it to build it again")
    ui = gs.gameui
    add = gs.core.components.add_component
    name = str(gs.core.project.read_config(root).get("name") or root.name)
    menu = {"kind": "menu", "backdrop": BACKDROP}
    ui.files.create_here(path, root, [
        {"name": "Title", **menu, "open": "firstStart"},
        {"name": "Pause", **menu, "pause_menu": True, "back": "resume"},
        {"name": "Options", **menu, "back": "previous"},
        {"name": "Win", **menu},
        {"name": "GameOver", **menu},
        {"name": "HUD", "kind": "hud", "open": "start"},
    ])

    def heading(canvas, prefix, body, y, card, color="#FFFFFF", size=88):
        """A card's title with the accent bar under it."""
        ui.text(canvas, f"{prefix}Title", body, (960, y), size=size, color=color, parent=card)
        ui.panel(canvas, f"{prefix}Accent", (960, y + 75), (96, 8), color=ACCENT, radius=4, parent=card)

    def button(canvas, name, label, y, action, parent, target=None):
        ui.button(canvas, name, label, (960, y), action=action, target=target, parent=parent, sound=CLICK)

    # Title: a star over the name, anchored to the top with the subtitle. The name's box has a width and no
    # height: a name too long for one line shrinks (UiText fit) instead of wrapping.
    ui.image("Title", "TitleStar", "assets/ui/star.png", (960, 170), width=112, anchor="top")
    # project_name: a game made from a template saved from this one shows its own name there.
    title = ui.text("Title", "GameTitle", name, (960, 320), size=120, box=(1680, 0), anchor="top", shadow=(0, 6, 18),
                    project_name=True)
    add(title, "UiText", {"fit": True})
    ui.panel("Title", "TitleAccent", (960, 410), (120, 8), color=ACCENT, radius=4, anchor="top")
    ui.text("Title", "Subtitle", "Sky Garden: reach the golden star", (960, 466), size=48, color=SOFT, anchor="top",
            shadow=(0, 3, 10))
    ui.group("Title", "TitleButtons", (960, 705), anchor="center")  # the column of buttons: more go under Options
    button("Title", "PlayButton", "Play", 650, "close", "TitleButtons")
    button("Title", "OptionsButton", "Options", 760, "open", "TitleButtons", target="Options")
    ui.text("Title", "Hint", CONTROLS, (960, 1010), size=30, color=MUTED, anchor="bottom", shadow=(0, 2, 8))

    # Pause, Options, Win and GameOver: a card in the middle, a title and the buttons stacked below it.
    ui.panel("Pause", "PauseCard", (960, 530), (640, 560), **CARD)
    heading("Pause", "Pause", "Paused", 340, "PauseCard")
    button("Pause", "ResumeButton", "Resume", 510, "resume", "PauseCard")
    button("Pause", "PauseOptionsButton", "Options", 620, "open", "PauseCard", target="Options")
    button("Pause", "RestartButton", "Restart", 730, "restart", "PauseCard")
    ui.text("Pause", "PauseHint", CONTROLS, (960, 1010), size=30, color=MUTED, anchor="bottom", shadow=(0, 2, 8))

    # Options: the channel volumes, 0 to 1 in tenths; the engine saves them (ctx.save "gs.audio"). Back
    # (Escape too) returns to the screen that opened it: the title or the pause menu.
    ui.panel("Options", "OptionsCard", (960, 540), (800, 560), **CARD)
    heading("Options", "Options", "Options", 350, "OptionsCard")
    for label, value, y in (("Music", "audio.music", 520), ("Sound", "audio.sfx", 620)):
        ui.text("Options", f"{label}Label", label, (640, y), size=44, color=SOFT, align="left", parent="OptionsCard")
        ui.slider("Options", f"{label}Slider", (1090, y), size=(400, 28), value=value, step=0.1, parent="OptionsCard",
                  sound=CLICK)
    button("Options", "BackButton", "Back", 740, "previous", "OptionsCard")

    # Win: the time and coins of the run; the best time from ctx.save, or "New best time!" (UiShow on newBest).
    ui.panel("Win", "WinCard", (960, 540), (800, 600), **CARD)
    heading("Win", "Win", "Level complete!", 330, "WinCard", color=ACCENT)
    ui.text("Win", "WinTime", "Time {time:m:ss.00}", (960, 475), size=52, parent="WinCard")
    ui.text("Win", "WinCoins", "Coins {coins} / {coinTotal}", (960, 545), size=44, color=SOFT, parent="WinCard")
    best = ui.text("Win", "BestTime", "Best {save.bestTime:m:ss.00}", (960, 610), size=36, color=MUTED,
                   parent="WinCard")
    add(best, "UiShow", {"value": "newBest", "compare": "below", "than": 1})
    new_best = ui.text("Win", "NewBest", "New best time!", (960, 610), size=36, color=ACCENT, parent="WinCard")
    add(new_best, "UiShow", {"value": "newBest", "compare": "equals", "than": 1})
    button("Win", "PlayAgainButton", "Play again", 735, "restart", "WinCard")

    ui.panel("GameOver", "OverCard", (960, 540), (640, 480), **CARD)
    heading("GameOver", "Over", "Game Over", 390, "OverCard", color=DANGER, size=96)
    ui.text("GameOver", "OverCoins", "Coins {coins} / {coinTotal}", (960, 545), size=48, color=SOFT, parent="OverCard")
    button("GameOver", "TryAgainButton", "Try again", 670, "restart", "OverCard")

    # HUD: the coins on a pill in the top-left corner with the lives as hearts under them (heart i shows while
    # ctx.store.lives >= i, over an empty heart), and the clock at the top right.
    ui.panel("HUD", "CoinPanel", (218, 84), (340, 72), **PILL, anchor="top-left")
    ui.image("HUD", "CoinIcon", "assets/ui/coin.png", (90, 84), width=52, parent="CoinPanel")
    ui.text("HUD", "Coins", "{coins} / {coinTotal}", (132, 84), size=40, align="left", parent="CoinPanel")
    ui.panel("HUD", "LivesPanel", (218, 166), (340, 72), **PILL, anchor="top-left")
    ui.icons("HUD", "Lives", (218, 166), count=LIVES, value="lives", size=48, spacing=10, parent="LivesPanel")
    ui.panel("HUD", "ClockPanel", (1782, 84), (180, 72), **PILL, anchor="top-right")
    ui.text("HUD", "Clock", "{time:m:ss}", (1782, 84), size=40, parent="ClockPanel")

    for canvas, first in (("Title", "PlayButton"), ("Pause", "ResumeButton"), ("Options", "BackButton"),
                          ("Win", "PlayAgainButton"), ("GameOver", "TryAgainButton")):
        ui.canvas(canvas, focus=first)  # selected first when the menu opens
        ui.animate(canvas, open="pop", close="fade", duration=0.25)
    bpy.ops.wm.save_mainfile()  # saving exports the screens for the game
    return path


def find_gamestudio():
    """The GameStudio package, when this file runs on its own (blender --python)."""
    import importlib
    import sys

    import addon_utils

    for name, module in list(sys.modules.items()):
        if name.split(".")[-1] == "gamestudio" and hasattr(module, "gameui"):
            return module
    for module in addon_utils.modules():
        if module.__name__.split(".")[-1] == "gamestudio":
            addon_utils.enable(module.__name__, default_set=False)
            return importlib.import_module(module.__name__)
    raise SystemExit("The GameStudio extension is not installed in this Blender.")


if __name__ == "__main__":
    print("UI_OK", build(find_gamestudio()))
