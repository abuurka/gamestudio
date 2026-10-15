# SPDX-License-Identifier: MIT-0
"""Builds ui/ui.blend, the endless runner's menus and HUD (docs/GAME-UI.md), with gs.gameui.

New Project runs build(gs, root) once. Run it again to get the screens back as this file makes it.
It replaces ui/ui.blend and opens it in Blender (unsaved changes to the open file are lost), so put
changes you want to keep into this file, or stop using it once you edit the screens in Blender:

  Coding agent (MCP): run_python with
                        import runpy; runpy.run_path(r"<project>/tools/build_ui.py")["build"](gs)
  A terminal:         blender --background --python tools/build_ui.py

Saving the file exports the screens to .gamestudio/export/ui/. The design frame is 1920 x 1080 px,
origin top-left, y down. One font (Inter), one accent (#FFD400), one card style: the starter's look.

  Title     the game's name, the best distance (once there is one), Play and Options, the controls;
            opens on the first start (firstStart), not after Try again
  Pause     the pause menu (Escape, P, gamepad Start): Resume, Options, Restart
  Options   Music (audio.music) and Sound (audio.sfx) sliders, Back (previous: Title or Pause)
  GameOver  RunSystem opens it after a crash or a fall: the title it sets, distance, coins, score, the
            best distance and Try again (restart)
  HUD       opens on every start: the distance (top centre), the coins (top right), the power-up bars
            (top left, shown only while one runs), a banner and the controls for the first seconds

The HUD reads ctx.store (RunSystem, CoinSystem and PowerUpSystem keep it): distance, speed, score,
coins, magnetLeft and shieldLeft (0-1 of the power's time left) and banner; ctx.save: bestDistance.
"""

import pathlib

import bpy

PROJECT = pathlib.Path(__file__).resolve().parents[1]
ACCENT = "#FFD400"
SOFT = "#E6EAF2"  # subtitles and values
MUTED = "#B8C1CF"  # hints
CARD = {"color": "#12161FE6", "radius": 40, "border": 2, "border_color": "#FFFFFF1F", "anchor": "center"}
PILL = "#0000008C"  # behind HUD values
BACKDROP = (0.0, 0.0, 0.0, 0.55)  # linear RGBA over the game, behind the menus
CLICK = "assets/sfx/click.wav"  # the starter's sound, played by every button and slider
COLORS = {"magnet": "#FF6B9A", "shield": "#4DD4FF"}  # as src/systems/power-ups.ts colours their sparkles
CONTROLS = "A/D or ←/→ change lane · Space jump · Esc pause · R restart"
SHADOW = (0, 3, 10)  # text straight over the game


def build(gs, root=PROJECT) -> pathlib.Path:
    root = pathlib.Path(root)
    ui = gs.gameui
    add = gs.core.components.add_component
    path = gs.core.gameui.ui_file(root)
    name = str(gs.core.project.read_config(root).get("name") or root.name)
    menu = {"kind": "menu", "backdrop": BACKDROP}
    ui.files.create_here(path, root, [
        {"name": "Title", **menu, "open": "firstStart"},
        {"name": "Pause", **menu, "pause_menu": True, "back": "resume"},
        {"name": "Options", **menu, "back": "previous"},
        {"name": "GameOver", **menu},
        {"name": "HUD", "kind": "hud", "open": "start"},
    ])

    # --- Title: the name and subtitle at the top, the buttons in one centred group, the controls below ---
    # project_name: a game made from a template saved from this one shows its own name there.
    title = ui.text("Title", "GameTitle", name, (960, 300), size=120, box=(1680, 0), anchor="top", shadow=(0, 6, 18),
                    project_name=True)
    add(title, "UiText", {"fit": True})  # a long name shrinks to one line
    ui.panel("Title", "TitleAccent", (960, 390), (120, 8), color=ACCENT, radius=4, anchor="top")
    ui.text("Title", "Subtitle", "Sunset Skyway: run as far as you can", (960, 446), size=48, color=SOFT,
            anchor="top", shadow=SHADOW)
    best = ui.text("Title", "TitleBest", "Best {save.bestDistance} m", (960, 530), size=40, color=ACCENT,
                   anchor="top", shadow=SHADOW)
    add(best, "UiShow", {"value": "save.bestDistance", "compare": "atLeast", "than": 1})  # once there is one
    ui.group("Title", "TitleButtons", (960, 690), anchor="center")
    ui.button("Title", "PlayButton", "Play", (960, 690), action="close", parent="TitleButtons", sound=CLICK)
    ui.button("Title", "OptionsButton", "Options", (960, 798), action="open", target="Options",
              parent="TitleButtons", sound=CLICK)
    ui.text("Title", "Hint", CONTROLS, (960, 1010), size=30, color=MUTED, anchor="bottom", shadow=SHADOW)

    # --- Pause, Options and GameOver: a card in the middle, a title, an accent, then the contents ------
    ui.panel("Pause", "PauseCard", (960, 540), (640, 600), **CARD)
    ui.text("Pause", "PauseTitle", "Paused", (960, 335), size=88, parent="PauseCard")
    ui.panel("Pause", "PauseAccent", (960, 405), (96, 8), color=ACCENT, radius=4, parent="PauseCard")
    ui.button("Pause", "ResumeButton", "Resume", (960, 520), action="resume", parent="PauseCard", sound=CLICK)
    ui.button("Pause", "PauseOptionsButton", "Options", (960, 628), action="open", target="Options",
              parent="PauseCard", sound=CLICK)
    ui.button("Pause", "RestartButton", "Restart", (960, 736), action="restart", parent="PauseCard", sound=CLICK)

    # Options: no code. The sliders write the channel volumes, which the game saves (gs.audio).
    ui.panel("Options", "OptionsCard", (960, 540), (760, 620), **CARD)
    ui.text("Options", "OptionsTitle", "Options", (960, 320), size=88, parent="OptionsCard")
    ui.panel("Options", "OptionsAccent", (960, 392), (96, 8), color=ACCENT, radius=4, parent="OptionsCard")
    for row, (label, slider, value) in enumerate((("Music", "MusicSlider", "audio.music"),
                                                  ("Sound", "SoundSlider", "audio.sfx"))):
        y = 480 + row * 100
        ui.text("Options", f"{label}Label", label, (650, y), size=44, color=SOFT, align="left", parent="OptionsCard")
        track = ui.slider("Options", slider, (1090, y), size=(360, 28), value=value, step=0.1,
                          parent="OptionsCard", sound=CLICK)
        # A ring under the track and its handle while it is selected (the arrow keys then change it).
        ring = ui.panel("Options", f"{slider}Focus", (1090, y), (412, 64), color=ACCENT + "1F", radius=32, border=3,
                        border_color=ACCENT, parent=slider, z=track.matrix_world.translation.z)
        add(ring, "UiState", {"show": "focusedOrPressed"})
    ui.button("Options", "BackButton", "Back", (960, 730), action="previous", parent="OptionsCard", sound=CLICK)

    # GameOver: RunSystem sets OverTitle ("Crashed!" or "You fell!") and shows the NewBest ribbon first.
    ui.panel("GameOver", "OverCard", (960, 540), (720, 620), **CARD)
    ui.text("GameOver", "OverTitle", "Crashed!", (960, 320), size=96, parent="OverCard")
    ui.panel("GameOver", "OverAccent", (960, 392), (96, 8), color=ACCENT, radius=4, parent="OverCard")
    ui.text("GameOver", "OverDistance", "{distance:0} m", (960, 470), size=80, color=ACCENT, parent="OverCard")
    ui.text("GameOver", "OverStats", "{coins} coins · Score {score}", (960, 548), size=40, color=SOFT,
            parent="OverCard")
    ui.text("GameOver", "OverBest", "Best {save.bestDistance} m", (960, 612), size=34, color=MUTED,
            parent="OverCard")
    ui.group("GameOver", "NewBest", (960, 230), parent="OverCard", visible=False)  # a ribbon on the card's edge
    ui.panel("GameOver", "NewBestRibbon", (960, 230), (300, 56), color=ACCENT, radius=28, parent="NewBest")
    ui.text("GameOver", "NewBestText", "New best!", (960, 230), size=34, color="#17171C", parent="NewBest")
    ui.button("GameOver", "TryAgainButton", "Try again", (960, 730), action="restart", parent="OverCard",
              sound=CLICK)

    # --- HUD: small, at the edges, on dark pills; values bound to ctx.store ------------------------------
    ui.panel("HUD", "DistancePanel", (960, 92), (320, 104), color=PILL, radius=52, anchor="top")
    distance = ui.text("HUD", "Distance", "{distance:0} m", (960, 92), size=76, box=(288, 0), parent="DistancePanel")
    add(distance, "UiText", {"fit": True})  # a very long run shrinks to fit the pill
    ui.text("HUD", "Score", "Score {score}", (960, 172), size=34, color=SOFT, parent="DistancePanel", shadow=SHADOW)
    ui.panel("HUD", "CoinPanel", (1748, 84), (232, 72), color=PILL, radius=36, anchor="top-right")
    ui.image("HUD", "CoinIcon", "assets/ui/coin.png", (1676, 84), width=48, parent="CoinPanel")
    ui.text("HUD", "Coins", "{coins}", (1712, 84), size=44, align="left", parent="CoinPanel")
    # A row per power-up, shown only while it runs: its UiShow hides it at 0 (PowerUpSystem writes the bars).
    ui.group("HUD", "PowerUps", (240, 84), anchor="top-left")
    for i, kind in enumerate(("magnet", "shield")):
        label, row, y = kind.capitalize(), f"{kind.capitalize()}Row", 84 + i * 80
        add(ui.group("HUD", row, (240, y), parent="PowerUps"), "UiShow",
            {"value": f"{kind}Left", "compare": "notEquals", "than": 0})
        ui.panel("HUD", f"{label}Panel", (240, y), (336, 64), color=PILL, radius=32, parent=row)
        ui.text("HUD", f"{label}Label", label, (100, y), size=34, color=COLORS[kind], align="left", parent=row)
        ui.bar("HUD", f"{label}Bar", (316, y), (160, 16), value=f"{kind}Left", max=1.0, color=COLORS[kind],
               back_color="#FFFFFF26", radius=8, parent=row)
    ui.text("HUD", "Banner", "{banner}", (960, 380), size=96, color=ACCENT, anchor="center", outline=3,
            outline_color="#3A2A00", shadow=(0, 6, 18))
    ui.group("HUD", "Controls", (960, 1010), anchor="bottom", visible=False)  # RunSystem shows it for a few seconds
    ui.panel("HUD", "ControlsPill", (960, 1010), (680, 56), color=PILL, radius=28, parent="Controls")
    ui.text("HUD", "ControlsText", CONTROLS, (960, 1010), size=30, color=SOFT, parent="Controls")

    for canvas, button in (("Title", "PlayButton"), ("Pause", "ResumeButton"), ("Options", "BackButton"),
                           ("GameOver", "TryAgainButton")):
        ui.canvas(canvas, focus=button)  # selected first when the menu opens
        ui.animate(canvas, open="pop", close="fade", duration=0.25)
    bpy.ops.wm.save_mainfile()  # exports the screens
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
