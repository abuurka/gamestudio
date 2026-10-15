# Menus, HUDs and audio

[Manual contents](README.md) · Previous: [Assets and rendering](06-assets.md) · Next: [Working with coding agents](08-agents.md)

## Screens are Blender scenes

Game UI lives in `ui/ui.blend`, with one Blender scene per screen, also called a canvas. Typical names are Title, HUD, Pause, Options and Win. A canvas contains text, art, controls and optional 3D models; the runtime draws it over the game.

Choose **GameStudio > Edit Game UI**. If the file does not exist, GameStudio creates a UI file with a menu canvas. Save the current level when prompted. Switch canvases with Blender's scene selector, and use **GameStudio > Back to Level** when you finish.

The template gameplay captures in [Projects and templates](04-projects.md) show the resulting HUDs: counters, health, score, time and distance. Their editable source is the corresponding template's `ui/ui.blend`.

## Canvas settings

Open **Properties > Scene > Game UI Screen**. This panel provides the design size, **Frame Canvas**, **Show in Game** and the camera's UiCanvas fields.

| Setting | Meaning | Example |
|---|---|---|
| `kind` | Menu or HUD | A pause screen is `menu`; score is `hud` |
| `open` | When the canvas opens | `manual`, `start` or `firstStart` |
| `pause` | Whether a menu pauses gameplay | Enabled for an options menu |
| `pauseMenu` | Designate the game's pause menu | Enabled on Pause |
| `back` | Back/Escape behavior | Close, resume or return to the previous menu |

New canvases use a 1920 × 1080 design size, an orthographic camera looking down at an XY layout and a scale of 100 design pixels per Blender unit. Higher Z values draw on top. Keep the generated canvas camera setup unless you understand the export requirements; a perspective or rotated canvas camera is not a substitute for a UI model view.

Menus take navigation focus and can pause the simulation. HUDs can stay visible while gameplay continues. Avoid configuring a score HUD as a pausing menu.

## Add and position elements

In a UI file, use **Shift+A > Game UI** to add Text, Image, 3D Model, Panel, Button, Bar, Slider, Toggle or Group.

1. Add an element and give it a descriptive name.
2. Select it and open **Properties > Object > Game > Layout**.
3. Set its design-pixel position and, where available, width and height.
4. Parent related pieces to a group when they should move together.
5. Add or adjust UiAnchor on top-level elements when you need explicit anchoring or stretch.

Design-pixel coordinates start at the top-left, with Y increasing downward. The Layout controls handle conversion into Blender's scene. Anchors keep an element's distance from an edge or the center as the viewport changes shape. Children follow their parent rather than taking independent top-level anchors.

Test at more than one aspect ratio. A centered title, corner score and full-screen background have different layout requirements. UiSlice supports nine-slice panels whose borders keep their appearance while the center stretches.

## Edit text and fonts

Text objects use their text body, font and material color. **Choose Font** copies the font into `assets/fonts/`, and importing UI images copies external art into `assets/ui/`. This makes the project easier to move between computers.

UiText adds outline, shadow and fit settings. Fit needs a meaningful text box to shrink into. Use a font with glyph coverage for every language you plan to show, and test the longest translated label in the actual game.

## Bind a score or timer

Text can include live values in braces. In a game whose systems update `ctx.store.score`, set a text body to:

```text
Score {score}
```

| Text or value path | Reads |
|---|---|
| `{score}` | The `score` value in the run store |
| `{save.best}` | A persistent `best` value |
| `{game.time:m:ss}` | Simulated time formatted as minutes and seconds |
| `audio.music` | Music channel volume when used as a control's value path |

Braces belong in text bindings. A slider, bar or visibility component uses a plain value path such as `health` or `audio.music`, without braces.

For a health bar, set UiBar's value path, maximum and fill element. For a row of hearts, use UiShow rules: the second heart can show while `lives` is at least `2`. Your game still needs to set those run values; a label does not create a scoring or damage system.

## Configure buttons

Use the UiButton fields on a button element. Built-in actions cover common navigation such as closing a menu, resuming, restarting or opening another canvas. The `target` field supplies the destination where the selected action requires one.

For a custom event, set the action to `event`, set its target to a name such as `buy`, and register a handler in a system's `start` hook. Validate the game's conditions inside that handler before spending currency or changing state.

Use descriptive labels and visible focus states. Test mouse, keyboard and gamepad navigation rather than relying only on pointer clicks. Back navigation should return to the expected previous menu without leaving gameplay unexpectedly paused.

## Options controls

A newly added Slider controls `audio.music`. Use its UiSlider fields to set the value path, range, step, fill and optional handle. Set another slider to `audio.sfx` for sound effects. The runtime writes these channel values and keeps player settings.

A Toggle needs a value path and references to its on/off artwork. It can write a persistent setting such as `save.showHints`; a system or UI visibility rule must then use that setting to affect the game.

Controls remain interactive under a paused menu. Music can continue while the menu is open so the player hears a volume change immediately.

## Preview and animated screens

Look through the canvas camera with **Numpad 0**, then enable the 3D View's **Game** toggle for a runtime preview. This displays fonts, bindings, nine-slice art, UI shaders and model views as the game draws them. **Show in Game** helps inspect the screen in the running game.

Screens can have open, close and loop animation segments authored with Blender timeline markers and supported element keyframes. Their detailed marker, timing and property rules are in [Game UI](../GAME-UI.md). Start from the template transitions before building a complex sequence, and check that controls become available when the transition finishes.

UiModel displays its child objects as a lit 3D view inside the canvas, useful for a turning collectible or a character. Those children are 3D content rather than flattened UI elements. Shine and Gradient can decorate UI panels; ordinary world shaders are not all suitable for flat UI art.

## Translations

Set the authored language with `locale` in `gamestudio.json`; it defaults to `en`. Additional languages are JSON tables under `ui/locales/`. The keys are the authored text bodies, including their bindings.

For example, `ui/locales/fr.json` can contain:

```json
{
  "Play": "Jouer",
  "Score {score}": "Score {score}",
  "Options": "Options"
}
```

Preserve `{score}` and other binding tokens in the translated value. A text without a translation falls back to the authored text. If you change the authored wording, update the translation key too.

The runtime selects a saved language choice, otherwise a matching browser language, otherwise the authored language. A button with the `locale` action can cycle available languages or select a target language. Its label can use `{ui.localeName}`.

Translations are files rather than a dedicated Blender translation panel. Agents can use `ui_strings` and `ui_translate` to inspect and update them. Check wrapping, focus labels and glyph coverage after switching languages.

## Sound and music

Use AudioSource for a sound attached to an entity. Choose a project-relative clip, volume, looping, spatial behavior and an appropriate channel. A non-spatial music source plays at a consistent level; a spatial source changes with the listener's distance.

Game systems can also call `ctx.audio` for one-shot effects. The templates show pickup, win and action sounds. Keep sound assets in the project and review their redistribution terms before including them in a published game.

Browsers commonly require player interaction before audio starts. Test from the title screen with a real click or key press before diagnosing silent autoplay as a missing file. Sound effects pause with gameplay, while music can continue under menus; UI sounds have their own channel.

## Check a complete interface

Start at the title, enter play, pause, change a setting, return, win or lose, and restart. Confirm the HUD updates and the correct menu has input focus. Repeat at a narrow aspect ratio and in another language. Save `ui/ui.blend` and use Export All before the release build so the shipped screens match what you edited.
