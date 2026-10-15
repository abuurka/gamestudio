# SPDX-License-Identifier: MIT-0
"""Builds ui/ui.blend, the time trial's menus and HUD, with gs.gameui (docs/GAME-UI.md in GameStudio).

New Project runs build(gs, root) once, before the level. One Blender scene per screen:

  Title    opens on the first start: the game's name, the circuit, the controls, Play and Options
  Pause    Escape, P or gamepad Start: Resume, Options, Restart
  Options  Music and Sound sliders (the audio.music and audio.sfx volumes, which the game saves) and Back
  Results  RaceSystem opens it after the last lap: total time, best lap, the record or, in its place,
           "New record!" (hidden until RaceSystem shows it), and Race again
  HUD      while racing: the lap and the race time (top left), the best lap (top right), the speed (bottom
           right), the start-line hint before the first lap, and the split banner RaceSystem shows at gates

The texts bind ctx.store values that RaceSystem (src/systems/race.ts) keeps up to date: lap, laps, raceTime,
bestLap and speedKmh ({raceTime:m:ss.00} draws 83.42 as 1:23.42), and the record ctx.save.bestRace. UiShow
rules on lap, bestLap and offRoad show the HUD's cards with no code.

To change the screens, edit ui/ui.blend (the ui_* tools, or Blender). To get them back as this file makes
them, delete ui/ui.blend, save the file open in Blender, then run this file; it leaves ui/ui.blend open:

  Coding agent (MCP): run_python with
                        import runpy; runpy.run_path(r"<project>/tools/build_ui.py")["build"](gs)
  A terminal:         blender --background --python tools/build_ui.py
"""

import pathlib

import bpy

PROJECT = pathlib.Path(__file__).resolve().parents[1]

# The starter's look: one font (gs.gameui's default, Blender's Inter, copied to assets/fonts/), dark
# translucent cards and one accent, the engine's focus colour, on a 1920 x 1080 design frame.
ACCENT = "#FFD400"
SOFT = "#E6EAF2"  # values and subtitles
MUTED = "#B8C1CF"  # labels and the controls hint
CARD = {"color": "#12161FE6", "radius": 40, "border": 2, "border_color": "#FFFFFF1F"}
# Behind the HUD's values. The game blends in linear light, as Blender does: over the bright sky a card lets
# much more through than its alpha suggests, so the HUD's are nearly as solid as the menus'.
PILL = "#12161FD9"
OFF_ROAD = "#FF922B"
BACKDROP = (0.0, 0.0, 0.0, 0.55)  # linear RGBA over the game, behind the menus
CLICK = "assets/sfx/click.wav"  # the starter's sound, played by every button and slider
CONTROLS = "W/S drive · A/D steer · Space handbrake · Esc pause · R restart"


def build(gs, root=PROJECT) -> pathlib.Path:
    root = pathlib.Path(root)
    ui = gs.gameui
    path = ui.ui_file(root)
    if path.exists():
        raise ValueError(f"{path} already exists: delete it to build the game UI again")
    name = str(gs.core.project.read_config(root).get("name") or root.name)
    menu = {"kind": "menu", "backdrop": BACKDROP}
    ui.files.create_here(path, root, [
        {"name": "Title", **menu, "open": "firstStart"},
        {"name": "Pause", **menu, "pause_menu": True, "back": "resume"},
        {"name": "Options", **menu, "back": "previous"},
        {"name": "Results", **menu},
        {"name": "HUD", "kind": "hud", "open": "start"},
    ])
    title(gs, ui, name)
    pause(ui)
    options(ui)
    results(ui)
    hud(gs, ui)
    for canvas, button in (("Title", "PlayButton"), ("Pause", "ResumeButton"), ("Options", "BackButton"),
                           ("Results", "RaceAgainButton")):
        ui.canvas(canvas, focus=button)  # selected first when the menu opens
        ui.animate(canvas, open="pop", close="fade")
    bpy.ops.wm.save_mainfile()  # saving exports the screens for the game
    return path


def show_if(gs, obj, value, compare, than):
    """The element shows only while the value path compares so with `than` (UiShow, checked every frame);
    missing values and false read as 0, true as 1."""
    gs.core.components.add_component(obj, "UiShow", {"value": value, "compare": compare, "than": than})


def title(gs, ui, name):
    # The name, anchored to the top with its subtitle. Its box has a width and no height: in the game, a
    # name too long for one line shrinks (UiText fit) instead of wrapping.
    # project_name: a game made from a template saved from this one shows its own name there.
    text = ui.text("Title", "GameTitle", name, (960, 330), size=120, box=(1680, 0), anchor="top", shadow=(0, 6, 18),
                   project_name=True)
    gs.core.components.add_component(text, "UiText", {"fit": True})
    ui.panel("Title", "TitleAccent", (960, 420), (120, 8), color=ACCENT, radius=4, anchor="top")
    ui.text("Title", "Subtitle", "Pine Valley Circuit · {laps} laps against the clock", (960, 476), size=48,
            color=SOFT, anchor="top", shadow=(0, 3, 10))
    # The buttons: a column under one centred group, so they stay together on any screen shape.
    ui.group("Title", "TitleButtons", (960, 744), anchor="center")
    ui.button("Title", "PlayButton", "Play", (960, 690), action="close", parent="TitleButtons", sound=CLICK)
    ui.button("Title", "OptionsButton", "Options", (960, 798), action="open", target="Options",
              parent="TitleButtons", sound=CLICK)
    ui.text("Title", "Hint", CONTROLS, (960, 1010), size=30, color=MUTED, anchor="bottom")


def pause(ui):
    ui.panel("Pause", "PauseCard", (960, 540), (640, 580), anchor="center", **CARD)
    ui.text("Pause", "PauseTitle", "Paused", (960, 340), size=88, parent="PauseCard")
    ui.panel("Pause", "PauseAccent", (960, 415), (96, 8), color=ACCENT, radius=4, parent="PauseCard")
    for i, (button, label, action, target) in enumerate((("ResumeButton", "Resume", "resume", ""),
                                                         ("PauseOptionsButton", "Options", "open", "Options"),
                                                         ("RestartButton", "Restart", "restart", ""))):
        ui.button("Pause", button, label, (960, 520 + 108 * i), action=action, target=target, parent="PauseCard",
                  sound=CLICK)


def options(ui):
    # Back reopens the menu Options replaced (Title or Pause). The sliders need no code: they set the channel
    # volumes, which the game saves and restores; music plays on under the paused menus.
    ui.panel("Options", "OptionsCard", (960, 540), (720, 580), anchor="center", **CARD)
    ui.text("Options", "OptionsTitle", "Options", (960, 340), size=88, parent="OptionsCard")
    ui.panel("Options", "OptionsAccent", (960, 415), (96, 8), color=ACCENT, radius=4, parent="OptionsCard")
    for i, (key, value) in enumerate((("Music", "audio.music"), ("Sound", "audio.sfx"))):
        y = 510 + 100 * i
        ui.text("Options", f"{key}Label", key, (680, y), size=44, color=SOFT, align="left", parent="OptionsCard")
        ui.slider("Options", f"{key}Slider", (1090, y), size=(380, 28), value=value, step=0.1, parent="OptionsCard",
                  sound=CLICK)
    ui.button("Options", "BackButton", "Back", (960, 736), action="previous", parent="OptionsCard", sound=CLICK)


def results(ui):
    ui.panel("Results", "ResultsCard", (960, 540), (720, 600), anchor="center", **CARD)
    ui.text("Results", "ResultsTitle", "Finished!", (960, 325), size=96, color=ACCENT, parent="ResultsCard")
    ui.text("Results", "TotalLabel", "Total time", (960, 410), size=34, color=MUTED, parent="ResultsCard")
    ui.text("Results", "TotalTime", "{raceTime:m:ss.00}", (960, 475), size=88, parent="ResultsCard")
    ui.text("Results", "BestLapText", "Best lap {bestLap:m:ss.00}", (960, 560), size=40, color=SOFT,
            parent="ResultsCard")
    # One of these two shows: RaceSystem hides the record and shows "New record!" when the race beat it.
    ui.text("Results", "RecordText", "Record {save.bestRace:m:ss.00}", (960, 616), size=40, color=SOFT,
            parent="ResultsCard")
    ui.text("Results", "NewRecord", "New record!", (960, 616), size=44, color=ACCENT, parent="ResultsCard",
            visible=False)
    ui.button("Results", "RaceAgainButton", "Race again", (960, 740), action="restart", parent="ResultsCard",
              sound=CLICK)


def hud(gs, ui):
    # Before the first lap: what to do, at the top (the lap card takes over once the clock runs).
    show_if(gs, ui.panel("HUD", "StartHint", (960, 104), (1000, 136), color=PILL, radius=40, anchor="top"),
            "lap", "equals", 0)
    ui.text("HUD", "StartText", "Drive through the start line", (960, 80), size=44, parent="StartHint")
    ui.text("HUD", "StartControls", CONTROLS, (960, 134), size=28, color=MUTED, parent="StartHint")
    # Top left: the lap and the race time.
    show_if(gs, ui.panel("HUD", "LapCard", (248, 126), (400, 180), color=PILL, radius=36, anchor="top-left"),
            "lap", "atLeast", 1)
    ui.text("HUD", "LapText", "Lap {lap}/{laps}", (88, 84), size=40, color=ACCENT, align="left", parent="LapCard")
    ui.text("HUD", "RaceTime", "{raceTime:m:ss.00}", (88, 152), size=72, align="left", parent="LapCard")
    # Top right, once a lap is done: the best lap.
    show_if(gs, ui.panel("HUD", "BestCard", (1672, 84), (400, 72), color=PILL, radius=36, anchor="top-right"),
            "bestLap", "notEquals", 0)
    ui.text("HUD", "BestLap", "Best lap {bestLap:m:ss.00}", (1672, 84), size=40, color=ACCENT, parent="BestCard")
    # Bottom right: the speed, big, on one baseline with its unit; "OFF ROAD" on the grass.
    ui.panel("HUD", "SpeedCard", (1690, 950), (360, 170), color=PILL, radius=40, anchor="bottom-right")
    ui.text("HUD", "Speed", "{speedKmh:0}", (1734, 972), size=110, align="right", valign="top-baseline",
            parent="SpeedCard")
    ui.text("HUD", "SpeedUnit", "km/h", (1752, 972), size=36, color=SOFT, align="left", valign="top-baseline",
            parent="SpeedCard")
    show_if(gs, ui.text("HUD", "OffRoad", "OFF ROAD", (1690, 1006), size=26, color=OFF_ROAD, parent="SpeedCard"),
            "offRoad", "equals", 1)
    # The split banner: hidden until RaceSystem shows it for a moment (GO!, checkpoint splits, lap times).
    ui.group("HUD", "Split", (960, 300), anchor="top", visible=False)
    ui.panel("HUD", "SplitBg", (960, 300), (760, 100), color=PILL, radius=50, border=3, border_color=ACCENT,
             parent="Split")
    ui.text("HUD", "SplitText", "Checkpoint 1/3   0:12.34", (960, 300), size=52, parent="Split")


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
