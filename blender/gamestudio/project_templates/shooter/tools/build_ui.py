# SPDX-License-Identifier: MIT-0
"""Builds ui/ui.blend, the arena shooter's menus and HUD (docs/GAME-UI.md), with gs.gameui.

New Project runs build(gs, root) once, before the level, after `npm run registry` (the UI components
are engine built-ins in the project's registry). It refuses when ui/ui.blend exists: edit that file
(GameStudio > Edit Game UI, the design-ui skill, the ui_* tools) rather than rebuilding it. To build it
again, delete ui/ui.blend (losing its edits), save the open file, then:

  Coding agent (MCP): run_python with
                        import runpy, pathlib; runpy.run_path(r"<project>/tools/build_ui.py")["build"](gs, pathlib.Path(r"<project>"))
                      (it opens the new UI file in Blender: open_scene goes back to the level)
  A terminal:         blender --background --python tools/build_ui.py

Screens (canvases), each a scene of ui/ui.blend, 1920 x 1080 design px:

- Title: opens on the first start and pauses: the game's name, the subtitle, the best score (once there
  is one), the controls, and TitleButtons: Play (closes it) and Options.
- Pause: the pause menu (Escape, P or gamepad Start; Escape resumes): Resume, Options, Restart.
- Options: Music and Sound sliders (the audio channels' volumes, saved by the engine) and Back
  (the menu it came from, selected first).
- GameOver: opened by HealthSystem (ctx.ui.open): the score, the wave reached, the best score or
  "New best score!" (shown by code), Try again.
- HUD: opens on every start: the player's health bar (top left), the wave (top centre), the score (top
  right), the wave banners in the middle and a controls hint along the bottom (shown by WaveSystem).

The HUD reads the game's state from ctx.store, which the systems keep up to date: score, health (the
player's hit points; the bar's max is the Player's Health.max, 100), healthLow (30 % or less: the bar
turns red), wave (0 before the first) and enemiesLeft. The best score is ctx.save's bestScore.
"""

import pathlib

import bpy

PROJECT = pathlib.Path(__file__).resolve().parents[1]
# The starter's look: one font (gs.gameui's default, Blender's Inter, copied to assets/fonts/), one accent
# (the engine's focus colour), dark translucent cards and pills.
ACCENT = "#FFD400"
SOFT_TEXT = "#E6EAF2"  # subtitles and values
MUTED_TEXT = "#B8C1CF"  # hints
CARD = {"color": "#12161FE6", "radius": 40, "border": 2, "border_color": "#FFFFFF1F"}
PILL = {"color": "#0000008C", "radius": 36}  # behind each HUD value
BACKDROP = (0.0, 0.0, 0.0, 0.55)  # linear RGBA over the game, behind the menus
CLICK = "assets/sfx/click.wav"  # every button's and slider's sound (the starter's)
HEALTH = "#51CF66"
HEALTH_LOW = "#FF6B6B"
CLEARED = "#69DB7C"
CONTROLS = "WASD move · mouse aim · hold the left button to fire · Esc pause · R restart"


def build(gs, root=PROJECT) -> pathlib.Path:
    root = pathlib.Path(root)
    ui = gs.gameui
    path = ui.ui_file(root)
    if path.exists():
        raise ValueError(f"{path} already exists: edit it, or delete it to build it again")
    name = str(gs.core.project.read_config(root).get("name") or root.name)
    menu = {"kind": "menu", "backdrop": BACKDROP}
    gs.gameui.files.create_here(path, root, [
        {"name": "Title", **menu, "open": "firstStart"},
        {"name": "Pause", **menu, "pause_menu": True, "back": "resume"},
        {"name": "Options", **menu, "back": "previous"},
        {"name": "GameOver", **menu},
        {"name": "HUD", "kind": "hud", "open": "start"},
    ])

    def show_if(element, value, compare, than):
        """UiShow: the element shows only while `value compare than` holds (checked every frame)."""
        gs.core.components.add_component(element, "UiShow", {"value": value, "compare": compare, "than": than})

    def button(canvas, element, label, at, parent, **kw):
        return ui.button(canvas, element, label, at, parent=parent, sound=CLICK, **kw)

    def card(canvas, prefix, size, title, title_color="#FFFFFF", title_size=88):
        """A centred card "<prefix>Card" with its title and an accent bar under it, near the card's top."""
        top = 540 - size[1] / 2
        ui.panel(canvas, f"{prefix}Card", (960, 540), size, anchor="center", **CARD)
        ui.text(canvas, f"{prefix}Title", title, (960, top + 95), size=title_size, color=title_color,
                parent=f"{prefix}Card")
        ui.panel(canvas, f"{prefix}Accent", (960, top + 170), (96, 8), color=ACCENT, radius=4, parent=f"{prefix}Card")

    # --- Title: the name, anchored to the top with its subtitle. Its box has a width and no height: in the
    # game, a name too long for one line shrinks (UiText fit) instead of wrapping.
    # project_name: a game made from a template saved from this one shows its own name there.
    title = ui.text("Title", "GameTitle", name, (960, 340), size=120, box=(1680, 0), anchor="top", shadow=(0, 6, 18),
                    project_name=True)
    gs.core.components.add_component(title, "UiText", {"fit": True})
    ui.panel("Title", "TitleAccent", (960, 430), (120, 8), color=ACCENT, radius=4, anchor="top")
    ui.text("Title", "Subtitle", "The Pit: survive the waves", (960, 486), size=48, color=SOFT_TEXT, anchor="top",
            shadow=(0, 3, 10))
    best = ui.text("Title", "BestScore", "Best score {save.bestScore}", (960, 556), size=36, color=ACCENT,
                   anchor="top", shadow=(0, 3, 10))
    show_if(best, "save.bestScore", "atLeast", 1)  # not before the first game over
    # The buttons: a column under one centred group, the main action first.
    ui.group("Title", "TitleButtons", (960, 690), anchor="center")
    button("Title", "PlayButton", "Play", (960, 690), "TitleButtons", action="close")
    button("Title", "OptionsButton", "Options", (960, 798), "TitleButtons", action="open", target="Options")
    ui.text("Title", "Hint", CONTROLS, (960, 1010), size=30, color=MUTED_TEXT, anchor="bottom")

    # --- Pause: a card with Resume, Options and Restart.
    card("Pause", "Pause", (640, 580), "Paused")
    button("Pause", "ResumeButton", "Resume", (960, 520), "PauseCard", action="resume")
    button("Pause", "PauseOptionsButton", "Options", (960, 628), "PauseCard", action="open", target="Options")
    button("Pause", "RestartButton", "Restart", (960, 736), "PauseCard", action="restart")

    # --- Options: the music and sound volumes (the engine's audio channels, saved in ctx.save) and Back.
    card("Options", "Options", (720, 600), "Options")
    for label, value, y in (("Music", "audio.music", 500), ("Sound", "audio.sfx", 600)):
        ui.text("Options", f"{label}Label", label, (640, y), size=44, color=SOFT_TEXT, align="left",
                parent="OptionsCard")
        ui.slider("Options", f"{label}Slider", (1100, y), size=(320, 28), value=value, step=0.1, color=ACCENT,
                  parent="OptionsCard", sound=CLICK)
    button("Options", "BackButton", "Back", (960, 740), "OptionsCard", action="previous")

    # --- GameOver: the run's score and wave, the best score; "New best score!" replaces it (HealthSystem).
    card("GameOver", "Over", (640, 580), "Game Over", title_color=HEALTH_LOW, title_size=96)
    ui.text("GameOver", "OverScore", "Score {score}", (960, 490), size=64, parent="OverCard")
    ui.text("GameOver", "OverWave", "Reached wave {wave}", (960, 562), size=40, color=SOFT_TEXT, parent="OverCard")
    ui.text("GameOver", "OverBest", "Best {save.bestScore}", (960, 622), size=36, color=MUTED_TEXT, parent="OverCard")
    ui.text("GameOver", "NewBest", "New best score!", (960, 622), size=40, color=ACCENT, parent="OverCard",
            visible=False)
    button("GameOver", "TryAgainButton", "Try again", (960, 736), "OverCard", action="restart")

    # --- HUD: pills at the top edge, bound to ctx.store; the middle belongs to the game.
    ui.panel("HUD", "HealthPanel", (252, 84), (424, 72), anchor="top-left", **PILL)
    ui.image("HUD", "HeartIcon", "assets/ui/heart.png", (84, 84), width=44, parent="HealthPanel")
    ui.bar("HUD", "HealthBar", (240, 84), (240, 24), value="health", max=100, color=HEALTH, back_color="#FFFFFF26",
           radius=12, parent="HealthPanel")
    # The same bar in red over it while health is low: no code, UiShow on the store's healthLow.
    low = ui.bar("HUD", "HealthBarLow", (240, 84), (240, 24), value="health", max=100, color=HEALTH_LOW,
                 back_color="#00000000", radius=12, parent="HealthPanel")
    show_if(low, "healthLow", "equals", 1)
    ui.text("HUD", "Health", "{health}", (380, 84), size=36, align="left", parent="HealthPanel")
    ui.panel("HUD", "WavePanel", (960, 84), (480, 72), anchor="top", **PILL)
    wave = ui.text("HUD", "Wave", "Wave {wave} · {enemiesLeft} left", (960, 84), size=40, parent="WavePanel")
    show_if(wave, "wave", "atLeast", 1)
    ready = ui.text("HUD", "GetReady", "Get ready", (960, 84), size=40, color=SOFT_TEXT, parent="WavePanel")
    show_if(ready, "wave", "below", 1)
    ui.panel("HUD", "ScorePanel", (1716, 84), (312, 72), anchor="top-right", **PILL)
    ui.text("HUD", "Score", "Score {score}", (1716, 84), size=40, color=ACCENT, parent="ScorePanel")
    # Shown for a moment by WaveSystem (ctx.ui.show / hide): a wave starts, a wave is cleared.
    ui.text("HUD", "WaveBanner", "Wave {wave}", (960, 380), size=120, anchor="center", shadow=(0, 6, 18),
            visible=False)
    ui.text("HUD", "ClearedBanner", "Wave {wave} cleared!", (960, 380), size=96, color=CLEARED, anchor="center",
            shadow=(0, 6, 18), visible=False)
    ui.text("HUD", "ControlsHint", CONTROLS, (960, 1010), size=30, color=SOFT_TEXT, anchor="bottom", outline=3,
            outline_color="#000000B3", shadow=(0, 3, 10), visible=False)  # outlined: over the bright floor

    for canvas, first in (("Title", "PlayButton"), ("Pause", "ResumeButton"), ("Options", "BackButton"),
                          ("GameOver", "TryAgainButton")):
        ui.canvas(canvas, focus=first)  # selected first when the menu opens
        ui.animate(canvas, open="pop", close="fade", duration=0.25)
    bpy.ops.wm.save_mainfile()  # saving exports the canvases for the game
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
