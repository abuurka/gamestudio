---
name: design-ui
description: Design and build the game's menus and HUD in Blender. Plans the screens, lays them out in ui/ui.blend with the ui_* tools (title, pause, options with volume sliders, toggles and a language button, game-over and win menus, score, a timer, hearts, health and icons on the HUD, buttons, live values, the best score, open, close and looping animations, 9-slice panel art, 3D models such as a turning gem, shine and gradient effects, translations), hooks them to code, then checks them in the game with screenshots and a playtest. Use when the user asks for a title screen, main menu, pause menu, options or settings screen (volume, a toggle, the language), game-over or win screen, HUD, score, timer, lives, hearts or health display, health bar, icons, buttons, a pulsing or shining button, menu transitions, panel art, a 3D model or character in a menu, another language, background music, or a better-looking UI.
argument-hint: "[screen or UI idea]"
---

# Design the game UI

Menus and the HUD are made in Blender, in `ui/ui.blend`, and the game draws them flat over the 3D view (no lighting, tone mapping or post effects; 3D models in them are lit like the level). Each Blender scene there is one **screen** (a canvas): `Title`, `Pause`, `Options`, `GameOver`, `HUD`. The tools lay them out; code only reacts. Settings, counters such as hearts, a level clock, animations, 3D models, shader effects and other languages need no code.

UI request: $ARGUMENTS (if this is empty, use the user's message)

## 1. Plan the screens (written down, a few lines each)

- **Which screens:** usually `Title` (opens on the first start), `Pause` (the Escape and P menu), `Options` (volumes, language; Title and Pause open it), `GameOver` and/or `Win` (code opens them) and `HUD` (open while playing). A sub-menu (Levels, Controls) is another menu that a button opens.
- **Per screen:** its texts, which values are live (`Score {score}`, `Time {game.time:m:ss}`, `Best {save.best}`), each button with what it does (close, restart, open another screen, load a level, switch the language, or an event that code handles), and each setting with the value path it writes (Build, step 5).
- **What code must provide:** the `ctx.store` keys the screens show (a store key shows only what a system sets), and where code opens the screens no button opens (`ctx.ui.open('GameOver')`). The built-in `game.time` and `game.level` need none.
- **The look:** one font, one accent colour, one panel style (the flat cards, or one panel art: Build, step 8), one transition for every screen, and at most one eye-catcher per screen (a turning 3D model, a Shine on the main button).
- **Languages:** every new text needs a translation in each table of `ui/locales/` (the starter ships French; Languages, below).

## 2. Open the UI file

1. `ui_open` (`"save": true` when the open level has unsaved changes). It loads `ui/ui.blend` in Blender, creating it with one empty menu, `Menu`, when it is missing. A starter project already has `Title` (`TitleGem`, a turning 3D gem over the name; `PlayButton`, focused first, with a `Shine` on its focus art, and `OptionsButton` in the group `TitleButtons`), `Pause` (Resume, Options and Restart in `PauseCard`), `Options` (Music and Sound sliders, `LanguageButton`, `BackButton`), `Win` and `HUD`, and a French table, `ui/locales/fr.json`. Its menus pop open and fade out in 0.25 s.
2. `ui_list`: every screen with its settings and animations, and every element with its rectangle in px, parent, anchor, text and UI components. Change what is there rather than adding a second title; reuse its sizes and colours (`ui_list {"canvas": "Pause"}`).
3. The level tools need a level: `open_scene` with `scenes/main.blend` when you are done here. `play` and `playtest` work from the UI file too (they play the start level).

## 3. Build

**The frame:** 1920×1080 px, origin top-left, y down. `at` is the centre of a box (panel, image, button, bar, slider, toggle, icon row, model), or the point a text aligns around (`align`, `valign`). Children's `at` is in screen px too.

1. **Screens:** `ui_canvas` creates one or changes its settings. `{"name": "GameOver", "kind": "menu"}`: one menu at a time; it takes the keys, gamepad and mouse, pauses the game (`"pause": false`: it runs on) and darkens it with `backdrop` (pass the one `ui_list` shows for `Pause`, so every menu matches). `"open"`: `"firstStart"` for a title (not after Restart), `"start"` for a HUD, `"manual"` (default) for the rest. `"pause_menu": true` makes the Escape and P menu; `"back"` is what Escape does (`resume`, `close`, `previous`, `restart`); `"focus"` is the button selected first; `"transition"` animates it. `{"name": "HUD", "kind": "hud", "open": "start"}` is drawn under the menus, mouse only. `ui_remove {"canvas": "Menu"}` drops an unused default.
2. **Elements** with `ui_add` and a `kind`; names are unique in the whole file (`OverRestartButton`, not a second `RestartButton`):
   ```json
   ui_add {"canvas": "GameOver", "kind": "panel", "name": "OverCard", "at": [960, 540], "size": [640, 480], "color": "#12161FE6", "radius": 40, "anchor": "center"}
   ui_add {"canvas": "GameOver", "kind": "text", "name": "OverTitle", "body": "Game over", "at": [960, 395], "size": 104, "parent": "OverCard"}
   ui_add {"canvas": "GameOver", "kind": "text", "name": "OverScore", "body": "Score {score} · Best {save.best}", "at": [960, 520], "size": 52, "color": "#E6EAF2", "parent": "OverCard"}
   ui_add {"canvas": "GameOver", "kind": "button", "name": "OverRestartButton", "label": "Try again", "at": [960, 670], "action": "restart", "parent": "OverCard", "sound": "assets/sfx/click.wav"}
   ```
   `ui_set` changes any of them later (`{"canvas": "Title", "name": "Subtitle", "body": "Find the lost crown"}`); `ui_remove` deletes one with everything inside it. A `group` holds elements that move together (a settings row), so moving them is one `ui_set` of its `at`.
3. **Buttons:** a `button` is a group with `UiButton`, a panel, a focus panel in `focus_color` (`#FFD400`) shown while it is focused or pressed, and its label. `action`: `close` or `resume` (close this menu), `restart`, `open` (`target`: a screen), `previous` (the menu this one replaced), `load` (`target`: a level), `locale` (the next language, or `target: "fr"`), or `event` for code. `sound` plays on a press (the starter's use `assets/sfx/click.wav`), `focus_sound` when the focus arrives. Keys and gamepad move by position (Up and Down to the nearest row, Left and Right within it); give `order` 1, 2, 3 only when a keyboard playtest goes wrong.
4. **Live values:** a text body binds `{score}` (`ctx.store.score`), `{player.name}` (nested), `{save.best}` (`ctx.save.get('best')`) and the built-ins `{game.time}` (`ctx.time`: the level's game seconds, still while paused, 0 again after a level change, Restart or Stop) and `{game.level}` (the level's name), with formats `{score:000}`, `{speed:0.0}`, `{game.time:m:ss.0}`. Other names are `ctx.store` keys, which show only what a system sets: nothing fills `{time}`. A bar fills by itself: `ui_add {"canvas": "HUD", "kind": "bar", "name": "HealthBar", "at": [228, 150], "size": [360, 24], "value": "health", "max": 100, "radius": 12, "anchor": "top-left"}`. Stopped, no system has run, so a `game_screenshot` of the HUD shows `Score `, no hearts, and what code hides, unless you pass values (Check, step 2).
5. **Value paths** are what bindings, bars, icon rows, sliders and toggles read, and what sliders and toggles write (without braces outside texts):

   | Path | Reads | A slider or toggle writes |
   |---|---|---|
   | `score`, `player.name`, `store.score` | `ctx.store` | the store |
   | `save.best` | `ctx.save` | `ctx.save`, kept across page loads |
   | `audio.music`, `audio.sfx`, `audio.master`, `audio.ui` | a channel's volume, 0–1 | the volume, saved for the player |
   | `game.time`, `game.level` | `ctx.time` (seconds since the level loaded); the scene name | nothing: read-only (it warns once) |
   | `ui.locale`, `ui.localeName` | the language's code; its own name (`Français`) | use a Language button instead |

   **Sliders and toggles** write their path as the player changes them: no code, no save code. A slider takes Left/Right while selected and follows a drag, from `min` to `max` in `step`s (0, 1, 0.1 by default; difficulty: `"value": "save.difficulty", "min": 1, "max": 3, "step": 1`); a toggle flips its path between `true` and `false` (missing is off). A setting the game applies itself: a default in `start` (`if (!ctx.save.has('difficulty')) ctx.save.set('difficulty', 2);`), then read it where it is used, or react in `ctx.ui.on('<its name>', (ctx, { value }) => ...)`.
6. **Art:** `font` takes a `.ttf`, `.otf`, `.woff` or `.woff2` (copied into `assets/fonts/`; the default is Inter). Texts take `outline` and `shadow` (`[x, y]` or `[x, y, blur]`); `set_component` sets `UiText.fit`, which shrinks a text into its `box`. Art that shows only while a button, slider or toggle is selected: a child with `UiState {"show": "focusedOrPressed"}`. `"visible": false` starts an element hidden, for code to show (`ctx.ui.show`).
7. **Images and icons:** `assets/ui/` has a HUD icon set, 128×128 PNGs in one flat style: `heart.png` and `heart-empty.png` (lives), `star.png` and `star-empty.png`, `coin.png`, `gem.png` (the starter's teal gems) and `key.png`. `ui_add {"canvas": "HUD", "kind": "image", "name": "GemImage", "path": "assets/ui/gem.png", "at": [92, 84], "width": 44, "parent": "ScorePanel"}` puts a gem on the starter's score pill (`ui_remove` its `GemIcon` square first). An image is 1 image px = 1 design px unless `width` or `height` is given. A row that counts (hearts, stars) is an `icons` element (Lives on the HUD). Other art is any PNG, JPG or WebP (copied into `assets/ui/`), drawn at about 2× the size it shows. A project older than this art: `run_python {"code": "str(gs.projects.TEMPLATES / 'starter' / 'assets' / 'ui')", "read_only": true}` names the folder.
8. **Panel art (9-slice):** a `panel` with `image` and `slice` (image px kept at each edge; one number for all four) keeps its art's corners at any size. The starter's art in `assets/ui/`:

   | File | `slice` | Look |
   |---|---|---|
   | `panel.png` | 32 | the flat card as an image |
   | `panel-solid.png` | 44 | that card, opaque |
   | `panel-framed.png` | 32 | opaque navy in a bevelled gold frame with gold corner studs; light text |
   | `panel-light.png` | 32 | opaque parchment in a brown outline; dark text (`#2B2118`) |
   | `button.png` | 44 | a light button body; dark label text |
   | `slider-track.png`, `slider-fill.png` | 14 | a slider's groove and its yellow fill |
   | `slider-knob.png` | none | a round knob: an `image`, not sliced |

   "Proper panel art" means `panel-framed`, `panel-light` or art of your own, on every card alike: `ui_set {"canvas": "Options", "name": "OptionsCard", "image": "assets/ui/panel-framed.png", "slice": 32}` (its flat border goes). `panel-light` takes the light-card colours (Layout rules, Contrast). An image panel takes no `color`, `radius` or `border`; resize it with `ui_set` `size` or the Layout box's W/H, never with S. Art of your own: corners scale with the canvas (2/3 at 720 px high), so draw lines 2 px or more, and keep what changes along an edge inside the insets.
9. **3D models:** a `model` shows a 3D object in a small view of its own, lit and tone mapped like the level: a gem turning on the title (the starter's `TitleGem`), a trophy on the win screen, a character waving on a select screen.
   ```json
   ui_add {"canvas": "Win", "kind": "model", "name": "WinStar", "at": [960, 190], "size": [200, 200], "kit": "collectible", "kit_params": {"style": "star"}, "spin": 1.2, "anchor": "top"}
   ui_add {"canvas": "Title", "kind": "model", "name": "TitleHero", "at": [960, 160], "size": [220, 220], "source": "scenes/main.blend:Player", "view": "front", "spin": 0.5, "anchor": "top"}
   ```
   It shows one of `kit` (a `list_kits` kit, `kit_params` as `create_from_kit` takes them), `asset` (a library asset, `asset_list`) or `source` (an object or collection in the UI file, or `<level file>:<object>`); `ui_add` with the same name and another one replaces it. **The player (or any level object) on a screen:** `"source": "scenes/main.blend:Player"` links the level's own, so it follows the level as it changes (above: in place of the title's gem, `ui_remove` `TitleGem` first). A copy that won't follow: the `kit` and `kit_params` the level's twin lists for it, or an `asset`. `size` is its view in px, and the game frames the whole model in it from `view` (`threeQuarter`, the default, `front`, `side`, `top`), closer with `zoom` above 1. `spin` turns it (radians per second; 0.6 by default), `clip` loops one of its `clips` (`ui_list`), `light` is `studio`, `soft` or `flat`, `background` fills the view (transparent by default); `ui_set` changes these. Blender shows it from above inside its view's outline: judge it in `game_screenshot`. A model can't be a parent: group it with what belongs to it.
10. **Shader effects on panels:** a panel's `shader` names a game shader the game draws it with; only `Shine` and `Gradient` work on flat UI. In `shader_values`, colours are `"#RRGGBB"` (sRGB, converted) or linear `[r, g, b, a]`, and angles are radians (Blender shows degrees). Blender shows the panel's own `color`, `game_screenshot` the effect; `"shader": ""` removes it.
    - **Shine** sweeps a band of light across every few seconds (`color`, its alpha the strength, `width`, `speed`, `interval`, `angle`). Put it on one call to action, as on the starter's `PlayButtonFocus`: `ui_set {"canvas": "Pause", "name": "ResumeButtonFocus", "shader": "Shine", "shader_values": {"color": [1, 1, 1, 0.4], "width": 0.25, "speed": 1.2, "interval": 3.5}}`.
    - **Gradient** paints `startColor` to `endColor` along `angle` (1.5708, the default: bottom to top; 0: left to right) in place of the panel's colour, whose alpha still multiplies it: give the panel an opaque `color` (alpha `FF`, not the cards' `E6`) near the gradient's middle. A gold Win card:
      ```json
      ui_set {"canvas": "Win", "name": "WinCard", "color": "#E4B55AFF", "shader": "Gradient", "shader_values": {"startColor": "#C98A2A", "endColor": "#FFE08A", "angle": 1.5708}}
      ```
      Then **recheck contrast** in `game_screenshot`: a light gradient takes the light-card colours (Layout rules, Contrast).

### Layout rules of thumb

- **Safe margins:** nothing closer than 48 px to an edge: TVs and phone notches crop it.
- **Anchors:** each top-level element gets the anchor of where it belongs, so it stays there on other screen shapes: HUD values a corner (`top-left`, ...), titles `top`, hints `bottom`, menus and cards `center`. Related things go under one parent (a card, a `group`); children follow it and take no anchor. A menu's buttons always share one parent (the starter's `TitleButtons`), or they split apart on a tall phone screen. `stretch: "width"` for a bar across the screen, `"cover"` for background art.
- **Type scale** (at 1080 px high): titles 88–120, headings 64, body and values 40–52, hints 28–32; never under 24.
- **Buttons:** at least 320×80 (the default 360×84) and 24 px apart, stacked vertically, main action first. Labels are verbs: Play, Resume, Restart.
- **Settings rows:** the name left-aligned on the left, the control on the right, every control ending at one right edge, one row every 100 px.
- **Contrast:** light text on a dark panel (`#12161FE6`) or a menu's backdrop; text straight over the game gets a `shadow` (`[0, 4, 12]`) or an `outline`. Keep the accent for what matters. **Light cards** (`panel-light`, a light Gradient) turn it round, the yellow accent too: texts `#2B2118`, and each button `ui_set` with `"color": "#2B211833", "text_color": "#2B2118", "focus_color": "#2B2118", "focus_text_color": "#FFD400"` (focused: dark, with a yellow label).
- **Beside a text** (a model next to the game's name): place it from the text's `rect` in `ui_list` (approximate), 24 px or more away, and inside the margins for a longer name too (each project's own, translations); or stack them, as `TitleGem` sits above the name.
- **Translucency** mixes in linear light, as Blender shows it, not as CSS does: a card that should look solid takes alpha `F2`–`FF` (`#12161FF2`) or opaque art (`panel-solid.png`).
- **HUD:** small and at the edges; the middle belongs to the game. A pill (a panel with `radius` half its height) behind each value.
- **Room for translations:** French and German run about 30 % longer than English: leave labels room, or give them a `box` and `UiText.fit`.

### Transitions

- **Presets:** `ui_canvas {"name": "GameOver", "transition": {"open": "pop", "close": "fade"}}` keys the screen's top-level elements and puts markers on its timeline. `open` and `close`: `fade`, `pop`, `slide-up` (`-down`, `-left`, `-right`) or `none`; `duration` [0.25 s each]; `loop`: `pulse`, `bob` or `blink` over the whole screen while it is open. New menus take the starter's `pop` and `fade`; `"transition": null` removes them.
- **One element:** `ui_set {"canvas": "Title", "name": "PlayButton", "loop": "pulse"}` (`bob`, `blink`; `"none"` stops it; `ui_add` takes `loop` too) keys just that element over the screen's loop range, after the open animation, inside a group too.
- **By hand in Blender**, on the screen's timeline: markers `open`, `close` and `loop` start each range. Keyframe location, Z rotation, scale and opacity (the object colour's alpha, which multiplies into children); where `open` ends is the rest pose. From `run_python`: `obj.keyframe_insert("scale", frame=f)` (opacity: `"color", index=3`); Blender 5 actions are layered: the curves are `ad.action.layers[0].strips[0].channelbag(ad.action_slot).fcurves` (`ad = obj.animation_data`). A hand-made loop ends on the rest values, or it jumps. Add hand keys last: a `transition` or `loop` change moves the ranges, not them.
- **Moving an animated element:** a keyed element moved by hand (G) snaps back to its keys. Move it with `ui_set` `at` or the Layout box, which key it again, or insert a keyframe at the rest frame.
- **For the game a close is at once** (`isOpen()` is false, `screen` is null, the game resumes) while it plays. In a playtest a 0.25 s animation is 15 steps, `final.ui.open` lists a closing screen until it ends, and `final.ui.animating` the screens mid-animation.

## 4. Hook up the code

- **Open** what no button opens: `ctx.after(1, (ctx) => ctx.ui.open('GameOver'))`, a second after the lose effect (a menu pauses the game, particles too). `ctx.ui.close()` closes the open menu.
- **React** to a button, after its action, or to a slider or toggle, after it changed: register in a system's `start`. The event is the `target` of an `event` button, else the object's name. Handlers run at once, even while the menu holds the game paused.
  ```ts
  start(ctx) {
    ctx.ui.on('buy', (ctx) => { ctx.store.coins -= 10; });
    ctx.ui.on('OverRestartButton', (ctx) => ctx.save.set('tries', ctx.save.get('tries', 0) + 1));
    ctx.ui.on('Difficulty', (ctx, { value }) => { ctx.store.enemySpeed = 2 + Number(value); });
  },
  ```
- **Values:** set every `ctx.store` key a screen shows in `start` (`ctx.store.lives ??= 3`), so it never shows empty. Best scores and settings go in `ctx.save`, with a default set in `start` (`if (!ctx.save.has('best')) ctx.save.set('best', 0);`). A one-level clock needs no code (`{game.time:m:ss}`); a run clock across levels is a store value a system keeps, `ctx.store.runTime = (ctx.store.runTime ?? 0) + ctx.dt;` in `update`, shown as `{runTime:m:ss}`.
- **Overrides** for what bindings can't do: `ctx.ui.text('Combo', 'x3')`, `ctx.ui.show('BossBar')` / `hide`, `ctx.ui.bar('BossBar', hp)`; `null` restores the Blender text or binding. They work on screens that aren't open too, so set a menu's texts first, then open it. Restart, Stop and a test reset drop them. Text from code goes through `ctx.ui.translate` (Languages). Engine `API.md` §22 lists the rest.
- A helper function that takes `ctx` types it `SystemContext` (`import type { SystemContext } from '@gamestudio/engine'`). `npm run typecheck`.

## 5. Check it in the game

1. `save`: it writes `ui/ui.blend`, exports the screens and reports the export.
2. `game_screenshot {"ui": "GameOver", "size": [1280, 720]}` for each screen. It closes any other menu, opens the screen, waits for its open animation and returns it as drawn, with `ui`: the buttons with their centres (px of `size`) and the texts with their values. Look for overlaps, contrast, alignment, the focused button, and that models and shader effects show. What depends on a value or a setting needs it set: `game_screenshot {"ui": "HUD", "set": {"store": {"score": 12, "lives": 2}, "save": {"showTimer": true}}}` (`save` alone is the flag that writes the screenshot files). A menu stays open, holding the game paused, until `stop`.
3. Another screen shape: `game_screenshot {"ui": "HUD", "size": [960, 720]}`. Corner items must stay in their corners, inside the margins.
4. `playtest` the flow as a player. On the title, `{"at": 0, "key": "Enter"}` presses Play; `Escape` opens the pause menu. Walk every menu with the arrow keys: `final.ui.focus` names the focused control. `final.ui.buttons` lists buttons, sliders and toggles (`kind`, `value`) with `x`/`y` in the run's canvas px, `final.ui.size` (`[480, 270]` by default). `final.ui` also has `screen`, `open`, `texts` (`{"HUD/Score": "Score 3"}`), `hidden` and `locale`. `set: {"save": {...}, "store": {...}}` seeds values (a playtest's `ctx.save` starts empty), and `final.save` proves a changed setting is kept (the volumes in `gs.audio`, the language in `gs.locale`).
5. `game_logs` has no warnings: an unknown screen in `ctx.ui.open`, a shader that doesn't work on UI and a Language button with an unknown code warn once.

**Live editing:** while the UI file is open and the game runs (`play`), every edit shows in the game at once: `ui_set`, then `game_screenshot {"ui": "Pause"}`. **Tell the user:** in `ui/ui.blend`, the 3D View header's **Game** toggle, through the screen's camera (Numpad 0), draws the screen as the game does while they edit (fonts, shaders, models, `{score}` placeholders).

## Languages

The texts are written in one language (`gamestudio.json` `locale`, default `en`). Each other language is a table, `ui/locales/<code>.json`, keyed by the texts exactly as written, bindings included. The game starts in the player's language when it has a table for it, remembers their choice, and needs no code. The starter ships French and a Language button on `Options`.

1. `ui_strings {"locale": "fr"}` lists every text once, with the elements that show it, its `translation` (or `null`) and the `missing` count; texts a table has that nothing shows any more are `unused`. The game's name (`project_name`) shows as it is in every language and isn't counted.
2. `ui_translate {"locale": "fr", "strings": {"Show timer": "Afficher le chrono", "Score {score} · Best {save.best}": "Score {score} · Record {save.best}"}}` adds or changes translations; others are kept, and `remove` drops old ones. A new language needs its own name: `{"locale": "de", "name": "Deutsch", "strings": {...}}`. A translation keeps every binding of its text. It works from any file, live. After adding or rewording texts, translate them in every table: an untranslated text shows as written.
3. **A Language button**, no code: `ui_add {"canvas": "Options", "kind": "button", "name": "LanguageButton", "label": "{ui.localeName}", "action": "locale", ...}` goes to the next language and shows the current one's name; `"target": "fr"` picks one.
4. **Text from code:** `ctx.ui.text('Banner', ctx.ui.translate('Crashed!'))`, with `"Crashed!"` added to the tables by `ui_translate`. `ctx.ui.locale = 'fr'` switches in code (saved for the player).
5. **Check:** a playtest starts in the browser's language. `playtest {"set": {"save": {"gs.locale": "fr"}}, "duration": 0.5}` starts in French: its screenshots show the title in French, `final.ui.locale` is `"fr"` and `final.ui.texts` the translations. Look for texts that overflow. The choice is kept: a playtest that presses the Language button ends with the new code in `final.save['gs.locale']`. Pick a font with every language's glyphs (Chinese or Japanese need their own); missing ones fall back to a system font.

## Sound and music

- **Channels:** `sfx` (the default: game sounds) pauses with the game; `music` plays on under a paused menu, so the Music slider is heard at once; `ui` (button, slider and toggle sounds) always plays. The game saves the volumes for the player; a playtest starts at 1.
- **Music:** the starter level's `Music` object plays `assets/music/theme.ogg`, a 30 s loop (`AudioSource {clip, volume: 0.5, loop, autoplay, spatial: false, channel: "music"}`). Other music: an `.ogg` in `assets/music/`, then in the level `set_component {"objects": "Music", "component": "AudioSource", "values": {"clip": "assets/music/boss.ogg"}}`. Sound starts on the first click or key (a browser rule). In code: `ctx.audio.play(clip, { loop: true, channel: 'music' })`.

## Recipe: Lives on the HUD

Three hearts in the top-right corner that empty as lives are lost, and a Game Over screen with the best score. The lives are `ctx.store.lives`; the mechanic that loses them lowers it and opens `GameOver` on the last one (a fall: `add-mechanic`, PATTERNS.md, Fall respawn).

1. **The hearts:** a pill like the starter's `ScorePanel`, mirrored to the top-right, holding an icon row. Heart *i* shows while `lives` is at least *i*, over an empty slot:
   ```json
   ui_add {"canvas": "HUD", "kind": "panel", "name": "LivesPanel", "at": [1764, 84], "size": [216, 72], "color": "#0000008C", "radius": 36, "anchor": "top-right"}
   ui_add {"canvas": "HUD", "kind": "icons", "name": "Lives", "at": [1764, 84], "value": "lives", "count": 3, "parent": "LivesPanel"}
   ```
   Defaults: `heart.png` over `heart-empty.png`, 52 px, 12 px apart (`image`, `empty`, `size`, `spacing`; no slots: `"empty": ""`). Five lives: `ui_set {"canvas": "HUD", "name": "Lives", "count": 5}`, and widen the pill.
2. **Game Over:** a `GameOver` menu built as in Build, step 2 (its card, title, `Score {score} · Best {save.best}` and a `restart` button), plus a line that starts hidden and code shows, then translate the new texts (Languages, step 2):
   `ui_add {"canvas": "GameOver", "kind": "text", "name": "NewBest", "body": "New best!", "at": [960, 585], "size": 40, "color": "#FFD400", "parent": "OverCard", "visible": false}`
3. **Code:** the defaults in a system's `start`, `ctx.store.lives ??= 3;` (Fall respawn's does it; Restart clears `ctx.store`) and `if (!ctx.save.has('best')) ctx.save.set('best', 0);`, and where the run ends, before the screen opens (in Fall respawn, in place of its last life's `best` and `ctx.after` lines):
   ```ts
   const score = ctx.store.score ?? 0;
   if (score > ctx.save.get('best', 0)) {
     ctx.save.set('best', score);
     ctx.ui.show('GameOver/NewBest'); // works before GameOver opens; Restart hides it again
   }
   ctx.after(1, (ctx) => ctx.ui.open('GameOver'));
   ```
4. **Check:** `save`, then `game_screenshot {"ui": "HUD", "set": {"store": {"lives": 2}}}`: two red hearts and an empty slot. With Fall respawn, `playtest {"start": {"position": [0, -20, 0]}, "inputs": [{"at": 0, "key": "Enter"}], "duration": 1}` drops the player once: `tracked` shows `Respawn.falls` at 1, the last screenshot two hearts. After the last life, `final.ui.screen` is `"GameOver"`, with `"GameOver/NewBest": "New best!"` in `final.ui.texts` when the score beat the save (`"set": {"save": {"best": 50}}` tests one that doesn't).

## Recipe: a setting on the Options screen

The starter's `Options` has `OptionsCard` (760×690, centred) holding `OptionsTitle`, `OptionsAccent`, three rows, `MusicLabel` + `MusicSlider` (`audio.music`, y 465), `SoundLabel` + `SoundSlider` (`audio.sfx`, y 565) and `LanguageLabel` + `LanguageButton` (y 665), and `BackButton` (`previous`, focused first) at y 785. Labels are left-aligned at x 660; the 360 px controls sit at x 1055, ending at x 1235; each slider has a `<Slider>Focus` ring. To add a setting, add a row, here a toggle that shows a timer on the HUD:

1. **Room:** `ui_set {"canvas": "Options", "name": "OptionsCard", "size": [760, 790], "at": [960, 590]}` makes the card 100 px taller with its top edge kept. Its children move down 50 px with it: `ui_set` them back to `OptionsTitle` [960, 285], `OptionsAccent` [960, 360], `MusicLabel` [660, 465], `MusicSlider` [1055, 465], `SoundLabel` [660, 565], `SoundSlider` [1055, 565], `LanguageLabel` [660, 665] and `LanguageButton` [1055, 665], and `BackButton` to [960, 885].
2. **The row** at y 765, in a group. The toggle (96 px) is right-aligned with the sliders at x 1187. A selected toggle has no highlight of its own: give it a ring like `MusicSliderFocus` (a child panel with `UiState`; pass the toggle's `z` from `ui_list` so the ring draws under it):
   ```json
   ui_add {"canvas": "Options", "kind": "group", "name": "TimerRow", "at": [960, 765], "parent": "OptionsCard"}
   ui_add {"canvas": "Options", "kind": "text", "name": "TimerLabel", "body": "Show timer", "at": [660, 765], "size": 44, "align": "left", "color": "#E6EAF2", "parent": "TimerRow"}
   ui_add {"canvas": "Options", "kind": "toggle", "name": "TimerToggle", "at": [1187, 765], "value": "save.showTimer", "parent": "TimerRow", "sound": "assets/sfx/click.wav"}
   ui_add {"canvas": "Options", "kind": "panel", "name": "TimerToggleFocus", "at": [1187, 765], "size": [148, 84], "color": "#FFD4001F", "radius": 42, "border": 3, "border_color": "#FFD400", "parent": "TimerToggle"}
   add_component {"objects": "TimerToggleFocus", "components": {"UiState": {"show": "focusedOrPressed"}}}
   ui_translate {"locale": "fr", "strings": {"Show timer": "Afficher le chrono"}}
   ```
   A slider row is the same with `"kind": "slider"` at x 1055 and a [412, 64] ring. **Long labels:** a label has room to about x 850 beside a slider or the Language button (8 letters at 44 px) and to 1110 beside a toggle, in every language (`ui_list` shows its right edge).
3. **The timer on the HUD**, shown only while the toggle is on, with no code: a pill at the top centre with the built-in clock, `game.time` (Build, step 4):
   ```json
   ui_add {"canvas": "HUD", "kind": "panel", "name": "TimerPanel", "at": [960, 84], "size": [240, 72], "color": "#0000008C", "radius": 36, "anchor": "top"}
   ui_add {"canvas": "HUD", "kind": "text", "name": "Timer", "body": "Time {game.time:m:ss}", "at": [960, 84], "size": 40, "parent": "TimerPanel"}
   add_component {"objects": "TimerPanel", "components": {"UiShow": {"value": "save.showTimer", "compare": "equals", "than": 1}}}
   ```
   `true` reads as 1, and the toggle starts off (missing is off). Translate `Time {game.time:m:ss}` too. Code reads the setting with `ctx.save.get('showTimer', false)`. A time that must stop at the finish is a store value set where the run ends (`ctx.store.finishTime = ctx.time;`, `{finishTime:m:ss.00}`).
4. **Check:** `game_screenshot {"ui": "Options"}`, and `game_screenshot {"ui": "HUD", "set": {"save": {"showTimer": true}}}` for the timer. A keyboard playtest: `{"at": 0, "key": "ArrowDown"}` and `{"at": 0.1, "key": "Enter"}` open Options from the title, with Back selected; `ArrowUp` at 0.4 selects the toggle and `Enter` at 0.6 flips it: `final.ui.focus` is `TimerToggle`, `final.ui.buttons` lists it with `"kind": "toggle"` and `"value": true`, and `final.save.showTimer` is `true`. One more `ArrowUp` per row must reach each control above; give the controls `order` only if one is skipped.

## Verify

- Every screen in the plan exists, with its settings (`kind`, `open`, `pauseMenu`, `focus`) and the same transition on every menu (`ui_list`).
- Each one looks right in `game_screenshot` at 16:9 and at another shape, with `set` values for what depends on them.
- A playtest goes title → play → pause → resume → the end screen, the arrow keys reach every control, `final.ui.texts` shows the live values, and `final.save` keeps the settings changed.
- `ui_strings {"locale": "<each table>"}` reports `missing: 0` (the game's name isn't counted), a playtest in that language shows texts that fit, and switching language is kept (`final.save['gs.locale']`).
- `npm run typecheck` passes, `game_logs` is clean, and the UI file is saved.

## Common failures

| Symptom | Fix |
|---|---|
| The write tools say the open file is not a UI file | `ui_open` first; it refuses unsaved level changes unless `"save": true`. |
| The UI components are missing | Start the dev server (`play`) or run `npm run registry`, then try again. |
| A text shows `Score ` with no number, or no hearts show | In a `game_screenshot`, pass the values (`"set": {"store": {"score": 12}}`); stopped, nothing has set them. While playing, the key is not set yet or misspelt: `ctx.store.score ??= 0` in `start`. |
| A timer such as `{time:m:ss}` stays empty, or restarts on the next level | A store key shows only what a system sets; the level's clock is built in: `{game.time:m:ss}`. Across levels, keep a run time in the store. |
| A button does nothing | An `event` button runs only its `ctx.ui.on` handler: check the name (its `target`, else its object name) and that `start` registers it. |
| `game_screenshot` refuses the screen (`unknown_canvas`) | The game loaded before the screen existed: `save` the UI file, then `stop` and `play`. |
| A playtest stops on a menu, or its click misses | Close the title first (`{"at": 0, "key": "Enter"}`); clicks go at `final.ui.buttons` x/y, in `final.ui.size` px. |
| A HUD click also fires the gun | Make it a button: a press on a HUD button never reaches gameplay. |
| A 3D model is tiny, cut off or dark | The view frames the whole model: raise `zoom` or `size`, try another `view`; `light: "soft"` for dark materials. |
| A Shine or Gradient doesn't show, or a Gradient is faint | Look in `game_screenshot`: Blender shows the plain panel. The panel's `color` alpha multiplies a Gradient: make it opaque. Other shaders don't work on UI (`game_logs`). |
| A text stays English in French | It is new or reworded since the table was made: `ui_strings {"locale": "fr"}` lists it as missing; `ui_translate` it. |
