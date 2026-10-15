# Game UI: menus and HUDs authored in Blender

Status: **Phases 1–3 implemented** (October 2026; §10 lists what Phase 2 added, §11 what Phase 3 added). This is the reference and the agreement between the Blender add-on (authoring, export, live link), the engine (drawing, input, `ctx.ui`, audio channels, translations) and the MCP server (agent tools). Each part is built against this file. When an implementation has to differ, update this file in the same change.

Game menus and HUDs are laid out in Blender, with Blender's own tools, fonts and art. The game draws them with WebGL or WebGPU over the 3D view, without tone mapping or post effects; 3D models inside them are lit and tone mapped like the level. Code is needed only for game-specific reactions: buttons, settings sliders and toggles, counters such as hearts, 9-slice art, open and close animations, 3D models, game shaders on panels and switching the language need none.

The DOM screens that exist today (`ctx.ui.screen(name, {title, buttons})`) and `ctx.hud` keep working unchanged. They are the quick, code-only path.

---

## 1. Files

```
my-game/
  scenes/main.blend        levels (unchanged)
  ui/ui.blend              UI file: one Blender scene per screen ("canvas")
  ui/locales/<code>.json   translations of the UI's texts, one table per language (§3.6)
  assets/fonts/*.ttf|otf|woff|woff2   fonts the UI uses
  assets/ui/*.png|jpg|webp            UI art (icons, 9-slice panels)
  .gamestudio/export/ui/index.json    exported list of canvases and languages
  .gamestudio/export/ui/<Canvas>.ui.json   one exported canvas
  .gamestudio/export/ui/<Canvas>.ui.glb    its 3D models, when it has any (§3.2)
  .gamestudio/export/ui/locales/<code>.json   the exported translation tables
```

- A **UI file** is any `.blend` directly in `<project>/ui/`. The default, and the one the tools create, is `ui/ui.blend`.
- Every user scene of a UI file is a **canvas** (a screen). Scenes whose names start with `gs_` are excluded. The canvas is named after its scene, for example `Title`, `Pause`, `HUD` or `GameOver`.
- Canvas names are unique in a project. When two UI files define the same name, export warns and the file that sorts first wins.
- UI files are never levels. `export_all()` also exports UI files (§4). Level export, level names and `scenes/` are unchanged.
- Texts are written in one language, `gamestudio.json` `locale` (default `"en"`), which has no table. Each other language is a table in `ui/locales/` (§3.6, §5.9).

## 2. A canvas in Blender

### 2.1 Frame and units

- The canvas camera is `scene.camera`. It must be **orthographic** and look straight down: rotation (0, 0, 0), looking along −Z. Elements lie in the XY plane, and the camera frame is the screen.
- The **design size** is `scene.render.resolution_x × resolution_y`, 1920 × 1080 by default. `resolution_percentage` is ignored.
- `ortho_scale` spans the larger side of the frame (sensor fit AUTO), so **pixels per Blender unit** is `k = max(resolution_x, resolution_y) / ortho_scale`. With sensor fit Horizontal it spans the width, with Vertical the height. Changing the design size keeps the camera's place and `k` (only `ortho_scale` changes).
  - New canvases use `ortho_scale` = 19.2, so 1 unit = 100 px.
  - The camera sits at (0, 0, 10), with clip range 0.1–100.
- **Canvas pixels** (px) have their origin at the frame's top-left corner, with x to the right and **y down**, as in CSS. For a world point (wx, wy) and a camera at (cx, cy):

  ```
  px = (wx − cx) · k + W / 2
  py = (cy − wy) · k + H / 2
  ```

  Camera `shift_x` and `shift_y` must be 0. Export warns otherwise, and also warns about a perspective or rotated camera, then uses the defaults.
- **Draw order:** world Z. A higher element draws on top. Ties keep outliner order: parents before children, then by name.
- **Colour management:** UI files use the view transform **Standard** with no look. The game draws UI colours exactly, without tone mapping, and Standard makes Blender show them the same way. The tools set this on new UI files and new canvases.

### 2.2 Elements: which objects become what

| Blender object | Element `type` | Notes |
|---|---|---|
| Text (`FONT`) | `text` | Body, font, size, alignment, text box, spacing, colour from material slot 0 (§3.3). Exported as data; the game draws the glyphs with the same font file |
| Empty with display type **Image** and an image | `image` | Image empty size and offset; opacity as for every element (below) |
| Mesh; also Curve and Surface, as evaluated meshes | `mesh` | Triangulated geometry in the object's local XY (Z dropped), with an unlit material (§3.5), optionally a game shader; 9-slice art with `UiSlice` |
| Empty with `UiModel` | `model` | A 3D model: its child objects are drawn lit, in a view of `UiModel.size` px centred on the empty (§2.3, §3.2, §5.3) |
| Any other empty | `group` | Groups children, anchors them, shows or hides them together |
| The canvas camera | none | Its `UiCanvas` component holds the canvas settings |
| Lights, other cameras, armatures, Grease Pencil, etc. | not exported | Export lists them once in a warning (lights silently) |

- **Hierarchy:** Blender parenting. Children follow their parent.
- **Start hidden:** "Disable in Renders" (`hide_render`, the camera icon) means the element starts hidden, and code shows it. The viewport eye icon (`hide_viewport` / `hide_get`) is for editing only and is ignored. `UiShow` hides an element by a rule as well (§2.3).
- **Opacity:** the object's colour alpha (`Object.color[3]`, Object › Viewport Display › Color) is the element's opacity, for every element type, and it multiplies down the hierarchy. Blender's Solid view shows it with Object colour; Material Preview does not, and an image empty shows it only with "Use Alpha" on. Open and close animations key it (§10.5).
- **Not exported:** objects in collections excluded from the view layer.
- **Inside a 3D model:** the objects under an empty with an enabled `UiModel`, at any depth (meshes, armatures with actions, collection instances of assets, kit objects), are the model, not elements. Export puts them in the canvas's GLB instead of flattening them into `mesh` elements (§3.2); the tools leave them out of the element list (§6), and a model can't be an element's parent. In Blender they are 3D objects under the canvas camera, seen from above, so the model's 2D footprint is its `size` (the overlay draws it, §7); the game-rendered preview (§11.2) shows the real view. An asset instance with `UiModel` on it is a model of its own.
- **Rotation and scale:** only rotation about Z is used; export warns about X/Y rotation. Scale X/Y are used. Negative scale mirrors.

### 2.3 Built-in UI components

These are engine built-ins (`category: 'UI'`), defined in `runtime/packages/engine/src/schema/builtins.ts`. They reach Blender through the normal registry (`.gamestudio/components.json`), so the Object › Game panel, `set_component`, live edits and export all work as for any component.

In UI files, the Add Component menu offers the UI category first. The runtime never makes ECS entities from canvas elements: these components are data for the UI layer.

**`UiCanvas`** (on the canvas camera)

| Field | Type | Default | Meaning |
|---|---|---|---|
| `kind` | enum `menu`, `hud` | `menu` | `menu`: one open at a time; takes keyboard and gamepad focus; can pause. `hud`: any number open; drawn under menus; mouse only |
| `open` | enum `manual`, `start`, `firstStart` | `manual` | `manual`: opened by a button or code. `start`: opens whenever the game starts or restarts. `firstStart`: opens on the first start after the page loads, after Stop and after a test `reset`, not after `ctx.restart()` (title screens) |
| `pause` | bool | `true` | Menus pause the game while open, through the normal pause path |
| `pauseMenu` | bool | `false` | This menu is the pause menu: Escape, P or gamepad Start open it while playing and close it again. Replaces `ctx.ui.pauseMenu()`. Opened that way it always pauses, and `back: none` acts as `resume` |
| `back` | enum `none`, `close`, `resume`, `previous`, `restart` | `none` | What Escape and gamepad B do while this menu is open. `resume` is the same as `close`. `previous` reopens the menu this one replaced. A pause menu always closes on its own keys |
| `scaleMode` | enum `fit`, `width`, `height`, `fixed` | `fit` | How the design size scales to the real canvas (§5.2) |
| `backdrop` | color | `[0,0,0,0]` | Full-screen colour drawn over the game and HUDs, behind this menu. New menus get `[0,0,0,0.6]` |
| `layer` | int | `0` | Order among open canvases of the same kind; higher draws on top |
| `focus` | entity | none | The button (or slider, or toggle) selected first. Default: the first in navigation order |

A scene without a `UiCanvas` on its camera is a manual menu with the defaults above.

**`UiButton`** (on any element; its hit area is the union of the screen rectangles of the element and its visible descendants)

| Field | Type | Default | Meaning |
|---|---|---|---|
| `action` | enum `event`, `close`, `resume`, `open`, `previous`, `restart`, `load`, `locale`, `none` | `event` | `close`/`resume`: close this canvas. `open`: open the canvas named `target`. `previous`: the menu this one replaced (with no history, it closes the menu). `restart`: `ctx.restart()`. `load`: close this menu, then `ctx.loadScene(target)`. `locale`: switch the UI language to the code in `target`; an empty `target` goes to the next language, wrapping (§5.9). `event` and `none`: nothing built in |
| `target` | string | `''` | Canvas to open, level to load, language code, or the event name |
| `disabled` | bool | `false` | Skipped by navigation; ignores clicks; shows `UiState` `disabled` children |
| `order` | int | `0` | Keyboard and gamepad order (1, 2, 3, …). With any `order` > 0 in a canvas, navigation follows order; otherwise it goes by position (§6.2) |
| `focusScale` | float | `1.06` | Scale about the element's centre while focused |
| `sound` | asset ogg/mp3/wav | `''` | Played when pressed (non-spatial) |
| `focusSound` | asset ogg/mp3/wav | `''` | Played when focus moves to it |

Every press runs the built-in action, then the handler registered with `ctx.ui.on(eventName)`. The event name is `target` when the action is `event` and `target` is set, otherwise the object's name. A `UiButton` on an element with `UiSlider` or `UiToggle` is ignored (export warns): those are controls of their own.

**`UiAnchor`** (only on top-level elements, i.e. elements without a parent; on a child, export warns and ignores it)

| Field | Type | Default | Meaning |
|---|---|---|---|
| `anchor` | enum `auto`, `center`, `top-left`, `top`, `top-right`, `left`, `right`, `bottom-left`, `bottom`, `bottom-right` | `auto` | The point of the screen this element keeps its distance to. `auto` picks it from where the element sits: thirds of the design width and height |
| `stretch` | enum `none`, `width`, `height`, `both`, `cover` | `none` | `width`/`height`: that side follows the screen's width/height instead of the uniform scale (bars spanning the screen). `both`: both sides (non-uniform). `cover`: uniform scale that covers the whole screen (background art) |

Top-level elements without `UiAnchor` behave as `anchor: auto`, `stretch: none`.

**`UiText`** (optional, on a text object)

| Field | Type | Default | Meaning |
|---|---|---|---|
| `outline` | float ≥ 0 | `0` | Outline width in design px |
| `outlineColor` | color | `[0,0,0,1]` | |
| `shadow` | vec2 | `[0,0]` | Drop-shadow offset in design px (x right, y down); `[0,0]` with no blur means no shadow |
| `shadowBlur` | float ≥ 0 | `0` | Design px |
| `shadowColor` | color | `[0,0,0,0.6]` | |
| `fit` | bool | `false` | Shrink the text until it fits its text box (needs a box with a width) |

**`UiBar`** (on an element that contains the fill)

| Field | Type | Default | Meaning |
|---|---|---|---|
| `value` | string | `''` | A value path (§3.4), a binding without braces, e.g. `health` or `save.best`. Empty: set from code with `ctx.ui.bar` |
| `max` | float > 0 | `1` | The value of a full bar |
| `fill` | entity | none | The element that grows. It is scaled along `direction` from its starting edge (its own bounds) |
| `direction` | enum `right`, `left`, `up`, `down` | `right` | Which way the fill grows |
| `smoothing` | float ≥ 0 | `0` | Seconds to ease toward a new value |

**`UiState`** (on a descendant of a `UiButton`: art that only shows in one state)

| Field | Type | Default | Meaning |
|---|---|---|---|
| `show` | enum `normal`, `focused`, `pressed`, `disabled`, `focusedOrPressed` | `focused` | The element is visible only while the nearest `UiButton` above it is in this state. The states are exclusive, with priority disabled, then pressed, then focused, then normal. So `focused` art hides while the button is pressed; highlight art uses `focusedOrPressed` |

**`UiShow`** (on any element: conditional visibility)

| Field | Type | Default | Meaning |
|---|---|---|---|
| `value` | string | `''` | A value path (§3.4): `lives`, `save.best`, `audio.music` |
| `compare` | enum `atLeast`, `below`, `equals`, `notEquals` | `atLeast` | How the value compares with `than` |
| `than` | float | `1` | The number it is compared with |

- The element is visible only while `value compare than` holds **and** its own visibility (authored, or `show`/`hide`) is on: `hide()` still hides it, and `show()` doesn't show it while the rule fails.
- The rule is re-evaluated every rendered frame, like bindings. Hearts: `Heart2` has `{value: 'lives', compare: 'atLeast', than: 2}` (the builder's `icons` makes such rows, §6).
- `state().ui.hidden` does not list elements hidden only by their `UiShow` (§5.7).

**`UiSlider`** (on a group: a value the player drags or steps)

| Field | Type | Default | Meaning |
|---|---|---|---|
| `value` | string | `''` | The value path (§3.4) it shows and writes: `audio.music`, `save.difficulty`, `store.x` |
| `min`, `max` | float | `0`, `1` | The range of the value |
| `step` | float ≥ 0 | `0.1` | Change per key press; drags snap to it. `0`: smooth (keys move a tenth of the range) |
| `fill` | entity | none | The child that grows with the value. Its own local bounds are the range: it is scaled from its starting edge, like a `UiBar` fill, to `(v − min)/(max − min)`. Export warns without one |
| `handle` | entity | none | Optional knob, moved along the range to the value |
| `direction` | enum `right`, `left`, `up`, `down` | `right` | Which way the value grows |
| `disabled` | bool | `false` | As for `UiButton` |
| `order` | int | `0` | Navigation order, as for `UiButton` |
| `sound` | asset ogg/mp3/wav | `''` | Played on each change |

**`UiToggle`** (on a group: an on/off switch)

| Field | Type | Default | Meaning |
|---|---|---|---|
| `value` | string | `''` | The value path it shows and flips: truthy becomes `false`, anything else `true` |
| `on` | entity | none | Child visible while the value is truthy |
| `off` | entity | none | Child visible otherwise |
| `disabled`, `order` | | `false`, `0` | As for `UiButton` |
| `sound` | asset ogg/mp3/wav | `''` | Played when flipped |

- Sliders and toggles are **controls**: focusable and in the navigation order with the buttons, so `UiCanvas.focus` can name one. Input is in §5.6.
- Every change writes the value path and the slider or toggle always shows the path's current value, so an Options screen needs no code. A change plays `sound` and then calls `ctx.ui.on(<element name>)` with the new value (§5.5).
- A toggle's `on` and `off` follow the visibility rules of `UiShow`, not `state().ui.hidden`.

**`UiSlice`** (on a textured panel: 9-slice art)

| Field | Type | Default | Meaning |
|---|---|---|---|
| `left`, `right`, `top`, `bottom` | float ≥ 0 | `16` | Image px at each edge that keep their size |

- It goes on a mesh whose material has an Image Texture; on anything else export warns and ignores it.
- Whatever size the panel is laid out at (resized, scaled, stretched), its corners keep their image size times the canvas scale; edges stretch along one axis and the middle both ways (§5.3).
- The builder makes the mesh as 9 quads with matching UVs for its size, so Blender shows it as the game does (§6).

**`UiModel`** (on an empty: its child objects are a 3D model, §2.2)

| Field | Type | Default | Meaning |
|---|---|---|---|
| `size` | vec2 ≥ 1 | `[320, 320]` | The model's view in design px, centred on the empty: the element's local bounds |
| `view` | enum `threeQuarter`, `front`, `side`, `top` | `threeQuarter` | Where the camera looks from (§5.3): `front` from Blender's front, `side` from Blender's right, `top` from above, `threeQuarter` 35° round from the front and 20° up |
| `zoom` | float ≥ 0.05 | `1` | Multiplies the framing: 2 is twice as close |
| `spin` | float (angle) | `0.6` | Turntable speed in radians per second about the model's up axis; `0` is still |
| `clip` | string | `''` | An animation clip of the model to loop; empty: none |
| `light` | enum `studio`, `soft`, `flat` | `studio` | `studio`: key, fill and rim; `soft`: even; `flat`: ambient only, base colours as authored |
| `background` | color | `[0,0,0,0]` | Fills the view behind the model; alpha 0 lets the UI show through |

- A model is laid out, anchored, animated, faded and hidden like any element, and drawn at its place in the draw order.
- It is not clickable by itself. Inside a button it widens the button's area, like any visible descendant.

## 3. Export format

### 3.1 Index: `.gamestudio/export/ui/index.json`

```json
{
  "format": "gamestudio.ui-index",
  "formatVersion": 1,
  "canvases": {
    "Title": { "file": "Title.ui.json", "rev": 4, "source": "ui/ui.blend", "kind": "menu", "open": "firstStart",
               "files": { "Title.ui.glb": "5e0c41d2a7" } },
    "HUD":   { "file": "HUD.ui.json",   "rev": 2, "source": "ui/ui.blend", "kind": "hud",  "open": "start" }
  },
  "sourceLocale": "en",
  "locales": { "fr": { "file": "locales/fr.json", "name": "Français" } }
}
```

- `file` is relative to the index and is the sanitized canvas name (export's `_sanitize`) + `.ui.json`.
- `files` (Phase 3): a canvas with 3D models lists its GLB and the GLB's content hash, as the canvas does (§3.2).
- `sourceLocale` and `locales` (Phase 3): the language the texts are written in (`gamestudio.json` `locale`, default `"en"`) and the translation tables, `{code: {file, name}}`, `file` relative to the index and `name` the language's own name (§3.6). Every UI export writes them.
- Exporting a UI file rewrites the index. Entries from other UI files are kept, entries whose source file no longer exists are dropped, and the file's removed canvases are dropped along with their files.
- No index, or an empty `canvases`, means the game has no Blender UI. The engine then loads no UI code (§5.6); the translation tables an index lists still load, for `ctx.ui.translate` (§5.9).
- **Revisions:**
  - `rev` counts up per canvas file, from the file on disk and the session's highest. It is never stored in the `.blend`, because exporting on save would make the file dirty again.
  - A canvas file is rewritten only when its content changes (`rev` and `exportedAt` aside).

### 3.2 Canvas: `.gamestudio/export/ui/<Canvas>.ui.json`

```json
{
  "format": "gamestudio.ui",
  "formatVersion": 1,
  "canvas": "Title",
  "source": "ui/ui.blend",
  "rev": 4,
  "exportedAt": "2026-10-11T12:00:00.000Z",
  "size": [1920, 1080],
  "settings": { "kind": "menu", "open": "firstStart", "pause": true, "pauseMenu": false, "back": "none",
                "scaleMode": "fit", "backdrop": [0, 0, 0, 0.6], "layer": 0, "focus": "gs_0a1b2c3d4e5f" },
  "elements": [
    {
      "id": "gs_0a1b2c3d4e5f", "name": "PlayButton", "parent": null, "type": "group",
      "visible": true, "order": 3,
      "transform": { "x": 960, "y": 700, "rotation": 0, "scaleX": 1, "scaleY": 1 },
      "anchor": "center", "stretch": "none",
      "components": { "UiButton": { "action": "close", "target": "", "disabled": false, "order": 0,
                                    "focusScale": 1.06, "sound": "", "focusSound": "" } }
    },
    {
      "id": "gs_1b2c3d4e5f60", "name": "PlayLabel", "parent": "gs_0a1b2c3d4e5f", "type": "text",
      "visible": true, "order": 5,
      "transform": { "x": 0, "y": 0, "rotation": 0, "scaleX": 1, "scaleY": 1 },
      "components": {},
      "text": { "body": "Play", "font": "assets/fonts/Baloo2-Bold.ttf", "size": 44, "color": [0.09, 0.09, 0.11, 1],
                "align": "center", "valign": "center", "lineSpacing": 1, "letterSpacing": 1, "wordSpacing": 1,
                "box": null, "offset": [0, 0], "shear": 0 }
    },
    {
      "id": "gs_2c3d4e5f6071", "name": "PlayBg", "parent": "gs_0a1b2c3d4e5f", "type": "mesh",
      "visible": true, "order": 4,
      "transform": { "x": 0, "y": 0, "rotation": 0, "scaleX": 1, "scaleY": 1 },
      "components": {},
      "mesh": { "positions": [-180, -42, 180, -42, 180, 42, -180, 42], "indices": [0, 1, 2, 0, 2, 3],
                "uvs": null, "colors": null, "bounds": [-180, -42, 180, 42],
                "material": { "color": [1, 0.83, 0, 1], "texture": "", "blend": "alpha" } }
    },
    {
      "id": "gs_3d4e5f607182", "name": "Logo", "parent": null, "type": "image",
      "visible": true, "order": 2,
      "transform": { "x": 960, "y": 260, "rotation": 0, "scaleX": 1, "scaleY": 1 },
      "anchor": "top", "stretch": "none",
      "components": {},
      "image": { "src": "assets/ui/logo.png", "rect": [-400, -150, 800, 300], "opacity": 1, "pixelated": false }
    },
    {
      "id": "gs_4e5f60718293", "name": "Gem", "parent": null, "type": "model",
      "visible": true, "order": 1,
      "transform": { "x": 960, "y": 420, "rotation": 0, "scaleX": 1, "scaleY": 1 },
      "anchor": "center", "stretch": "none",
      "components": { "UiModel": { "size": [360, 360], "view": "threeQuarter", "zoom": 1, "spin": 1.2, "clip": "",
                                   "light": "studio", "background": [0, 0, 0, 0] } },
      "model": { "glb": "Title.ui.glb", "node": "gs_4e5f60718293", "clips": [] }
    }
  ],
  "assets": {
    "assets/fonts/Baloo2-Bold.ttf": "../files/assets/fonts/Baloo2-Bold.ttf?v=3f2a9c01b7",
    "assets/ui/logo.png": "../files/assets/ui/logo.png?v=8c01d4e2f9"
  },
  "files": { "Title.ui.glb": "5e0c41d2a7" }
}
```

Rules:

- **Order of `elements`:** parents always come before their children. `order` is the global draw order after sorting by §2.1: 0 is drawn first, and higher numbers draw on top.
- **`transform`:** local to the parent, in design px, y down. Rotation is in radians, **clockwise positive**, because y is down: a Blender rotation of +θ about Z becomes −θ. For top-level elements, `x`/`y` are canvas px. A child's are px in the parent's frame, with the parent's scale and rotation applied, as in a scene graph.
- **`anchor` and `stretch`:** present only on top-level elements. `anchor` is already resolved and is never `auto`: x < W/3 is left, x > 2W/3 is right, otherwise centre; the same for y with top and bottom.
- **`opacity`** (Phase 2): the element's rest opacity 0..1, its object colour alpha `Object.color[3]` (§2.2), written only when below 1 (absent: 1). It multiplies down the hierarchy. An image's alpha is `opacity` when present, else `image.opacity`; both come from the same Blender alpha, so they never multiply.
- **`components`:** `export_components(obj)` values (all fields, defaults filled in). Entity fields hold element ids. Asset fields are project-relative paths that `assets` maps.
- **`text`**, from Blender's `TextCurve`:
  - `body`: `body`, with bindings unresolved (§3.4).
  - `font`: a project-relative asset path.
    - The tools give new texts Blender's bundled `datafiles/fonts/Inter.woff2`, copied into the project as `assets/fonts/Inter.woff2`, so Blender and the game draw the same glyphs. Its licence goes next to every copy as `Inter-OFL.txt` (the SIL Open Font License wants it with the font), in the project and in each export; any font with a licence file beside it (`<font>-OFL.txt`, `OFL.txt`, `LICENSE.txt`, ...) brings it the same way, and a build lists it in `THIRD-PARTY-NOTICES.md`.
    - Blender's built-in font (`<builtin>`) is an older Type 1 font the browser can't load. It exports as Inter, sized to the same cap height, so widths differ by about 5%.
    - A font outside the project is copied the way any outside asset is (SPEC §4.5).
  - `size`: `size × k` px. It is Blender's text size, the unit for line advance and spacing. It is not the CSS font size (see `fontSize`).
  - `fontSize`: the CSS font size in px that draws glyphs as big as Blender does. Blender scales a font by 1 / (head.yMax − head.yMin), not by 1 / unitsPerEm, so this is `size · upem / (yMax − yMin)` (Arial: 0.733 × `size`). It is `null` when export could not read the font; the runtime then uses `ascent / fontBoundingBoxAscent` at 1 px.
  - `ascent`, `descent`: Blender's, in px: FreeType's ascender and descender (hhea, else OS/2) over (yMax − yMin), times `size`.
  - `overflow`: Blender's text-box overflow, `none`, `scale` or `truncate`.
  - `fontAxes` (Phase 3): for a variable font, the instance Blender draws, `{tag: value}`. Measured in Blender 5.2 (Inter, Sitka, Segoe UI Variable, Bahnschrift): text objects are laid out at optical size 16 whatever their size (the advances match HVAR at `opsz` 16), every other axis at its default. So `opsz` is 16, clamped to the axis range, and the other axes are their defaults. TTF, OTF and WOFF fonts are read (`fvar`). WOFF2 needs Brotli, which Blender's Python lacks: Blender's own Inter (and the built-in font, drawn as Inter) gives `{opsz: 16, wght: 400}`, and other variable WOFF2 fonts warn and get no axes. Static fonts have none.
  - `color`: linear RGBA from material slot 0: Emission Color for an Emission or Background shader, else Principled Base Color with Alpha, else the material's viewport colour. Default white.
  - `align`: `align_x` lowercased (`left`, `center`, `right`, `justify`, `flush`).
  - `valign`: `align_y` lowercased with `_` → `-` (`top-baseline`, `top`, `center`, `bottom`, `bottom-baseline`).
  - `lineSpacing`, `letterSpacing`, `wordSpacing`: `space_line`, `space_character`, `space_word`.
  - `box`: `[x, y, w, h]` of the first text box's frame in local px, y down, or `null` when the box has no width. Blender places the frame one `size` above the text box's y. Text wraps at the box width. A box without a height (`h` = 0, `y` = Blender's box y) only wraps.
  - `offset`: `offset_x/offset_y × k`, with y flipped.
  - `shear`: `shear`.
  - Per-character formatting is not carried; export warns when the body uses bold or italic. The runtime reproduces Blender's layout rules (§5.3).
- **`image`**, from an image empty:
  - `src`: the image's file as a project-relative asset. Packed or generated images are written as PNG into `.gamestudio/export/files/generated/<name>-<hash>.png` and listed in `assets` under the key `generated/<name>-<hash>.png`.
  - `rect`: `[x, y, w, h]` local px, y down. The longer image side is `empty_display_size × k`, the other side follows the aspect ratio, and the rect is placed by `empty_image_offset` with y flipped.
  - `opacity`: the empty's colour alpha when `use_empty_image_alpha` is on, else 1 (Phase 1; the element's `opacity` stands in for it where present).
  - `pixelated`: always `false` in Phase 1. Blender image empties have no interpolation setting, so a future source would be a component field.
- **`mesh`**, from the evaluated mesh (modifiers applied, triangulated):
  - `positions`: flat `[x, y, …]` in local px, y down: `(vx·k, −vy·k)`.
  - `indices`: triangles.
  - `uvs`: the active UV map, flat `[u, v, …]`, Blender convention (v up), or `null`.
  - `colors`: the active color attribute as linear RGBA per vertex, or `null`.
  - `bounds`: `[minX, minY, maxX, maxY]` in local px.
  - `material`: §3.5, with `shader: {name, values}` when the material names a game shader (Phase 3).
  - `slice` (Phase 2, a mesh with `UiSlice` and an image texture): `{ "insets": [l, r, t, b], "image": [w, h], "size": [w, h] }`. Insets are in image px, `image` is the texture's size, and `size` is the panel's design size at scale 1: the builder's `gs_ui_size` property, else the mesh bounds. Export warns when the insets are larger than the image.
  - Export warns above 20,000 triangles per canvas.
- **`model`** (Phase 3, an empty with `UiModel`, `type: "model"`): `{ glb, node, clips }`. Its local bounds are `UiModel.size` centred on the origin, and `components.UiModel` carries the settings.
  - `glb`: the canvas's GLB, `<Canvas>.ui.glb`, relative to the canvas file. It holds one root node per model of the canvas.
  - `node`: this model's root node, named after the element id. Its children are the model's objects in the empty's local space, with the empty's own transform left out. Blender Z-up becomes glTF Y-up, as for levels.
  - `clips`: the animation clips in the GLB that animate this model: the actions on its objects, with the exporter's clip naming.
  - The GLB is made as levels are, with Blender's glTF exporter: armature clips sampled by GameStudio (SPEC §4.1), procedural materials baked, textures processed.
  - As implemented: a temporary scene (`gs_ui_models`) holds, per model, an empty at the origin (its root node, renamed to the element id) with copies of the model's objects under it. The copies share their data, actions and materials, sit in the `UiModel` empty's space and point their modifiers, constraints and drivers at each other, so the user's objects never change and the exporter's frame changes touch only the copies. A signature of everything the GLB depends on (the objects, their local transforms, mesh data, modifiers, materials, actions, images, instanced collections, settings), kept in memory and in the GLB's asset extras, skips the exporter while it is unchanged.
- **`files`** (Phase 3, on the canvas and its index entry): the GLB → its content hash (a SHA-1 hex prefix, as in scene manifests). The runtime requests the GLB as `?h=<hash>` and parses it again only when the hash changes; without one, every new `rev` loads it again.
- **`materials`** (Phase 3, on the canvas): the game shaders of the models' materials, as in scene manifests: the material's name in the GLB → `{shader, values}`, asset values mapped by `assets`.
- **`visible`:** `not obj.hide_render`.
- **`animations`** (Phase 2, on the canvas): `{open?, close?, loop?}`, each `{duration, fps, tracks}`, present only when the canvas's timeline has `open`, `close` or `loop` markers. Authoring, the track format and the runtime are in §10.5.
- **`assets`:** maps every asset path the canvas uses (fonts, images, textures, button sounds, component asset fields, game shaders' asset values) to a copy under `.gamestudio/export/files/`. URLs are relative to the canvas file. Copies follow SPEC §4.5 (hash-versioned, unchanged files skipped).

### 3.3 Text colour and fonts

The tools copy fonts into `assets/fonts/` when they are chosen (§7.2), so projects stay self-contained. Bold and italic come from choosing a bold or italic font file, not from Blender's per-character formatting.

### 3.4 Bindings and value paths

Text bindings, `UiBar.value`, `UiShow`, `UiSlider` and `UiToggle` share one path syntax (a binding is a path in braces):

| Path | Reads | Writes (sliders, toggles) |
|---|---|---|
| `name`, `a.b`, `store.name` | `ctx.store` | `ctx.store`; missing objects on the way are created |
| `save.name` | `ctx.save.get(name)` | `ctx.save.set(name, value)` (persists) |
| `audio.master`, `audio.music`, `audio.sfx`, `audio.ui` | the channel's volume, 0..1 | the channel's volume (saved, §5.8) |
| `game.time` | `ctx.time`, in seconds (below) | nothing: read-only |
| `game.level` | the loaded level's name (`ctx.game.scene.name`, e.g. `main`) | nothing: read-only |
| `ui.locale` | the UI language's code, `ctx.ui.locale` (§5.9) | switches the language (saved) |
| `ui.localeName` | the current language's own name, e.g. `Français` | nothing: read-only |

- Where a number is needed (bars, `UiShow`, sliders), booleans read as 1/0 and missing values as 0. A binding shows a missing value as empty text.
- A path that can't be written (empty, through a value that is not an object, or `game.time` / `game.level` / `ui.localeName`) warns once.
- **Built-in values:** `game.time` and `game.level` need no code, so a HUD's run clock is `Time {game.time:m:ss}`.
  - `game.time` is `ctx.time`: game seconds since the level loaded. It counts only while the game plays: it stands still while the game is paused (a pausing menu, `game.pause`) or stopped, and during a level change's fade. It starts again at 0 whenever a level loads: `ctx.loadScene`, `ctx.restart()`, Stop, a re-export from Blender, and a test reset.
  - A clock that must survive a level change, or that leaves out time the game wants left out, is a `ctx.store` value the game keeps (`{time:m:ss}` reads `ctx.store.time`).
  - Only `game.time` and `game.level` are built in. Other `game.x` paths are `ctx.store`'s, and `store.game.time` reaches a store object named `game`. The same holds for `ui.locale` and `ui.localeName` among `ui.x` paths.

A text body can show live values:

- `{name}` is `ctx.store.name`. `{a.b}` is `ctx.store.a.b`. `{store.name}` says the same explicitly.
- `{save.best}` is `ctx.save.get('best')`; `{audio.music}` is the music volume.
- `{game.time}` is `ctx.time` and `{game.level}` the level's name (built in, above).
- `{ui.localeName}` is the current language's name: a Language button's label (§5.9).
- `{{` and `}}` are literal braces.
- A body is translated first (§5.9), then its bindings are evaluated, so a translation keeps the bindings it wants.
- **Formats** go after a colon:
  - `{score:000}`: integer, zero-padded to 3 digits;
  - `{speed:0.0}`: 1 decimal;
  - `{time:m:ss}`: seconds as minutes:seconds (`mm:ss` pads the minutes, `h:mm:ss` adds hours), rounded down. Here `time` is a `ctx.store` key the game sets; `{game.time:m:ss}` is the built-in clock;
  - `{time:m:ss.0}`, `{time:m:ss.00}`, `{time:m:ss.000}`: the same with tenths, hundredths or thousandths.
- A missing value shows as empty text.

The runtime re-evaluates bindings and `UiShow` rules every rendered frame and redraws text only when the result changes.

### 3.5 Mesh materials (unlit)

UI meshes draw flat. Lighting, tone mapping and post effects do not apply. The material is read from the node tree:

1. Material Output → **Emission** or **Background**: `color` is the shader's Color times Strength (clamped to 1), or an Image Texture (`texture`) when one is linked. Alpha is 1. In a Mix Shader with a Transparent BSDF, alpha = factor when Transparent is the first shader input and 1 − factor when it is the second.
2. → **Principled BSDF**: `color` is Base Color (value or Image Texture), alpha is Alpha.
3. Otherwise: `material.diffuse_color`. A mesh without a material is grey `[0.8, 0.8, 0.8, 1]`.

- Vertex colours force `alpha` blending.
- Meshes are drawn double-sided, because the y flip reverses triangle winding.

- `blend` is `alpha`, or `additive` when the material name ends with `.add` (rare: glows). `opaque` when alpha is 1 and there is no texture with alpha. The runtime still draws opaque meshes in the transparent pass, so draw order holds.
- Texture files are assets like any other.
- The kit materials use Emission, so Blender's Material Preview shows UI meshes as the game draws them.
- **Game shader** (Phase 3): a material that names a game shader (Material › Game, `set_material_shader`, or the builder's `panel(shader=)`, §6) writes `shader: {name, values}`, as level materials do; asset values are mapped by `assets`. The game applies only shaders made for flat UI meshes (§5.3): the library's `Shine` and `Gradient`, or a project shader with `ui: true`. Blender's viewport shows the material's own nodes; the game-rendered preview (§11.2) shows the shader.

### 3.6 Translation tables

- **Files:** `ui/locales/<code>.json` = `{ "name": "Français", "strings": { "<authored text>": "<translation>" } }`, UTF-8.
  - Keys are text bodies exactly as authored, bindings and formats included: `"Score {score:000}": "Points {score:000}"`. A translation keeps the same bindings (in any order).
  - `name` is the language's own name, which `ctx.ui.locales` and `{ui.localeName}` show.
  - The language the texts are written in, `gamestudio.json` `locale` (default `"en"`), has no table.
- **Codes** are a language of 2–3 letters, an optional script and an optional region (`fr`, `pt-BR`, `zh-Hans`, `es-419`), normalised by the tools (`pt_br` → `pt-BR`); the file name is the code.
- **Export** (`gameui_export.export_locales`): checks the tables, copies them to `.gamestudio/export/ui/locales/` and writes the index's `sourceLocale` and `locales` (§3.1). Every UI export runs it (saving a UI file, `export_all()`, Play), and so do `ui_translate` and the live link's poll (§4). Only tables that changed are copied.
- Texts that code shows go through `ctx.ui.translate` and are added to the same tables, by hand or with `ui_translate` (§5.9).

## 4. Export and the live link (add-on)

- **When exports happen:**
  - Saving a UI file exports all of its canvases and the index. So does opening one (or File › Revert) whose export is stale.
  - `export_all()` exports every UI file: the open one directly, others in a background Blender, like level files.
  - Play from any file exports UI files whose canvases are missing, older than the `.blend`, or exported from unsaved changes. The bridge's `export_scene` and `play_in_blender` from a level do the same (`ui_errors` on failure).
  - Canvases written from unsaved changes (live link, Play, Export) are listed in `.gamestudio/ui_unsaved.json`, outside the shipped export. Exporting the saved file clears the list, so discarded changes never stay in the export.
  - Without the UI components in the registry (after loading the project's), exporting a UI file refuses rather than writing canvases without their components.
  - Every one of these also exports the translation tables (§3.6).
  - A canvas with 3D models also writes `<Canvas>.ui.glb` (§3.2). Its signature skips the glTF exporter while the models are unchanged, so saving or playing again costs little.
- **Play from a UI file:**
  - It plays the project's start level using that level's existing export. A missing level export is made in a background Blender first.
  - Once the game runs (`game.ready` and `game.status: playing`), it opens the canvas being edited (`ui.open`). If the game doesn't have that canvas yet, it sends `ui.canvas` first.
- **Warm game pages:** a game page kept between plays re-fetches `ui/index.json` and the changed canvases on Play and on scene reloads.
- **Live link, when a UI file is open and a game is connected:**
  - A depsgraph change in a canvas scene (transform, text body, component, material, image, new or removed object) re-extracts that canvas, debounced 60 ms (at most 200 ms). A changed mesh, curve, material, node tree, image or font re-extracts every canvas that uses it. A scene update alone (selection) re-extracts nothing; a twice-a-second check sees design size, camera and excluded collections, and also timeline markers and object colour alpha (§10.5). Keyframe edits re-extract the canvases whose elements use the action.
  - It writes the canvas file and copies any new assets, then sends `ui.canvas` with the whole canvas inline.
  - A canvas with models exports its GLB again only when model content changed. The GLB's hash is in the canvas (`files`), so such a change sends `ui.canvas` too.
  - Removing a canvas scene sends `ui.remove`.
  - "Show in Game" (canvas panel) sends `ui.open`.
  - The twice-a-second poll also watches the translation tables (`ui/locales/*.json`) and `gamestudio.json`. When they change they are exported and, when that differs from what the game has, sent as `ui.locales`.
  - In a UI file, the Scene view's Game toggle shows the edited canvas as the game draws it, through `view.mode` with `mode: "ui"` (§11.2).
- **Messages** (editor → game; the hub routes by role, and engines ignore types they don't know). Like every link message, the type is in the key `t`, e.g. `{"t": "ui.canvas", "canvas": "Title", "data": {...}}`. `window.__gs.message(msg)` delivers the same messages from inside the page (MCP uses it for `ui.open` and `ui.close`):

| Type | Payload | Engine behaviour |
|---|---|---|
| `ui.canvas` | `{ canvas, data }` (data = §3.2 object) | Adds or replaces the canvas in place. An open canvas stays open, keeping the focused button by element id |
| `ui.remove` | `{ canvas }` | Closes the canvas and forgets it |
| `ui.open` | `{ canvas }` | Opens it as if `ctx.ui.open(canvas)` had been called |
| `ui.close` | `{ canvas? }` | `ctx.ui.close(canvas)` |
| `ui.locales` | `{ sourceLocale, locales, tables }`: the index's fields (§3.1) and `tables: {code: {name, strings}}` inline | Replaces the game's languages and tables; the shown texts switch at once |

- **Engine → editor:** nothing new. `game.status` is unchanged.

## 5. Runtime (engine)

### 5.1 Loading

- At startup the game fetches `ui/index.json` from the export base, next to the level manifests. A 404, or an empty index, means no canvases. The dev server answers a missing index with an empty one and builds write one, so games without UI log no errors.
- It then fetches every listed canvas and preloads its fonts (`FontFace` + `document.fonts.add`) and images, and the translation tables the index lists (§5.9).
- A canvas with 3D models fetches its GLB (`model.glb`) once per URL and content hash (`files`, else its `rev`), so a live `ui.canvas` replace parses it again only when it changed.
- A canvas opened before its fonts are ready opens when they are, or after 1 s with fallback fonts.
- Canvases survive level loads.
- `ctx.restart()`, Stop and a test `reset` close them all at once (no close animation) and drop code overrides (`text`, `show`, `bar`).
- `start` and `firstStart` canvases open after the first simulated step of a run, once systems' `start()` has run. A HUD's bindings therefore show values under the title, and a key pressed on that step still reaches the menu. When the game next plays after a close:
  - `start` canvases reopen after any of the three;
  - `firstStart` canvases reopen after Stop and a test `reset` (a fresh run, like a code-only title), not after `ctx.restart()`.

### 5.2 Layout

Each canvas has a design size (Wr, Hr). The real canvas is W × H CSS px.

```
s = fit: min(W/Wr, H/Hr) | width: W/Wr | height: H/Hr | fixed: 1
```

For a top-level element with anchor (ax, ay) ∈ {0, ½, 1}² and design position (x, y):

```
offset = (x − ax·Wr, y − ay·Hr)
screen position = (ax·W + offset.x·sx, ay·H + offset.y·sy)
element scale  ×= (sx, sy)
```

- `sx = sy = s`, except:
  - `stretch: width` → `sx = W/Wr`;
  - `stretch: height` → `sy = H/Hr`;
  - `stretch: both` → both;
  - `stretch: cover` → `sx = sy = max(W/Wr, H/Hr)`.
- Children compose their local transforms under that.
- Layout is pure math with no WebGL. It is recomputed on resize and when content changes, and it gives every element a screen-space matrix and rectangle. Hit testing, navigation, `state()` and `ctx.ui.element()` use it, so they work without a renderer (tests).

### 5.3 Drawing

- **When:** after the 3D frame and post effects, into the same WebGL canvas.
  - The pass sets `autoClear = false` and calls `clearDepth()`.
  - It draws an orthographic scene in canvas CSS px (y down).
  - Materials use `toneMapped: false`, `depthTest: false`, `depthWrite: false` and `transparent: true`, and `renderOrder` follows `order`.
  - It is drawn through `renderer.raw`, never `GameRenderer.render()`, so the draw-count and camera tests are untouched. It is skipped when there is no `raw`.
  - **Blending is in linear light**, as Blender composites. With WebGLRenderer the pass copies the frame into an `SRGB8_ALPHA8` render target (multisampled; the GPU decodes, blends and encodes each write), draws the canvases into it and copies it back. Blending straight into the 8-bit canvas would be in sRGB: a `#FFFFFF26` panel over a dark scene came out about a third as bright as Blender renders it. WebGPU composites the same way with three/webgpu render targets (**WebGPU**, below). Until three/webgpu has loaded, or if that fails, it draws straight into the canvas with sRGB blending, with a warning.
- **Stacking order**, bottom to top:
  1. open HUD canvases, by `layer`;
  2. the open menu's `backdrop`;
  3. the open menu.
- The DOM fade stays above everything; the error overlay stays DOM.
- `ctx.hud` is DOM above the WebGL canvas, so its labels (corner text, `+10`, bars over enemies) hide while a canvas menu is drawn, open or closing, and never cover a menu.
- **Text** is rasterised with Canvas2D in the element's font at `fontSize × scale × devicePixelRatio` into a `CanvasTexture` (sRGB) on a quad. It is redrawn only when its string, scale or style changes.
  - Layout follows Blender, as measured in Blender 5.2; `core/gameui_export.py` keeps the full rules. `src/ui/canvas/text.ts` ports Blender's `vfont_to_curve` (blenkernel `vfont_curve.cc`) for one text box.
    - **Glyphs** are drawn at `fontSize`, with no kerning (`fontKerning = 'none'`), because Blender applies none. Each character is drawn on its own at the position the layout gives it, so there are no ligatures either.
    - **Lines** advance by exactly `lineSpacing × size`.
    - **Letter spacing** s, with w = `wordSpacing` for a space and 1 otherwise:
      - when s < 1, a character advances by `advance · (w + s − 1)`;
      - otherwise it advances by `advance · w + (s − 1) · size / 2`.
      - A line's width leaves out the last character's `(s − 1) · size / 2`.
    - **Wrapping and alignment:** lines align left/center/right within the box width (around x without a box) and wrap at spaces. A word longer than the box is not broken. Shear slants each line about its own baseline, and `offset` moves box and text together.
      - A line wraps at the last space or hyphen before the character that crosses the box width. A hyphen stays at the end of its line.
      - `justify` spreads the spaces of lines that end in a wrap; `flush` spreads the characters of every line. Both work only in a box.
      - `overflow: 'scale'` shrinks the text as Blender does: to the box width without wrapping when the box has no height, else to the largest size whose lines fit. `'truncate'` drops the lines below the box, or everything after the first wrap when the box has no height.
    - **Vertical alignment:** L is the number of lines, a = `ascent`, d = `descent`, adv = `lineSpacing × size`, and T = a + d + (L − 1) · adv. The first baseline sits at:

      | `valign` | No box, or a box without height (from the origin, y down) | Box frame `[x, y, w, h]` |
      |---|---|---|
      | `top-baseline` | 0 | y + size |
      | `top` | a | y + a |
      | `center` | a − T/2 | y + h/2 − T/2 + a |
      | `bottom` | −d − (L−1)·adv | y + h − d − (L−1)·adv |
      | `bottom-baseline` | −(L−1)·adv | y + h − (L−1)·adv |
  - `UiText` outline and shadow are drawn in Canvas2D.
  - **Variable fonts** (Phase 3) draw the instance Blender draws, not the browser's automatic optical size or weight: each font file and set of axis values is a `FontFace` of its own with those values pinned (`variationSettings`). The values are the text's exported `fontAxes` (§3.2); without them, the runtime applies the same rule to the font's own `fvar` (every axis at its default, `opsz` 16 clamped to its range). A WOFF2 font's axes can't be read in the browser either, so a variable WOFF2 is pinned only when it is Inter (`'opsz' 16, 'wght' 400`).
  - **Glyphs a font lacks** (a translation's accents or script, §5.9) fall back to the browser's system fonts.
  - The add-on's export tests fix any measured correction to this rule; then this section is updated.
- **Images:** a textured quad (sRGB, mipmaps, `NearestFilter` when `pixelated`).
- **Meshes:** `BufferGeometry` from `positions`/`indices`/`uvs`/`colors` and a `MeshBasicMaterial` (linear colour, sRGB texture, vertex colours when present, additive blending for `additive`).
- **Game shaders on meshes** (Phase 3): a mesh whose `material.shader` names a game shader gets it bound with the same `defineShader` machinery as level materials (the material is named `<Canvas>/<element>` for that).
  - Only shaders made for flat UI meshes apply: their definition says `ui: true` (engine `API.md` §28), as the library's `Shine` (a band of light sweeping across, with `color`, `width`, `speed`, `interval` and `angle`) and `Gradient` (two colours along an `angle`, in place of the base colour) do. Others warn once and the mesh draws without them: a menu mesh is an unlit `MeshBasicMaterial` in pixels, without normals, lights or depth.
  - On a UI mesh the shader's `ctx.time` is the canvas's own clock, the seconds since it opened, running under a paused menu (test steps in a playtest), so a `Shine` sweeps as its menu opens. `ctx.ui` is true there; positions are local px with y down.
  - Values are set in Blender; `ctx.shaders` reaches level materials only (§9).
  - A shader defined later (a project shader, a hot reload) binds then.
- **9-slice meshes** (`mesh.slice`) are rebuilt as 9 quads whenever their laid-out size changes, from object scale or `stretch`: corners keep `insets × canvas scale`, edges stretch along one axis and the middle both ways.
  - An animated scale and the focus scale zoom the whole panel, corners included, as Blender shows a keyframed scale.
  - Corners shrink in proportion when a panel is smaller than its insets.
  - Vertex colours are ignored on sliced meshes.
- **3D models** (Phase 3, `model` elements):
  - Each gets a small three.js scene of its own: a copy of its GLB node, lights for `light` (`studio`: key, fill and rim; `soft`: even; `flat`: ambient only) and a perspective camera.
  - The camera frames the node's bounding sphere from `view`: `front` looks along −Z (glTF), from Blender's front; `side` from Blender's right; `top` looks down; `threeQuarter` is 35° round from the front and 20° up. `zoom` multiplies the framing. A spinning model stays in frame.
  - The scene is drawn into a render target the size of the element's screen rect (× devicePixelRatio, at most 2), shown as a textured quad at the element's draw order, with its opacity, animations and visibility like any element. `background` fills the target; with alpha 0 the UI shows through.
  - **Tone mapping** is the level's (its view transform and exposure): models are lit 3D, unlike flat UI. With WebGLRenderer the target is flagged as an output target, so three.js tone maps into it; with WebGPURenderer it is the renderer's output target while the model draws, with a linear output colour space, so its output pass tone maps into it. Either way the quad shows tone-mapped linear colour like any UI texture.
  - The target holds premultiplied alpha (an MSAA edge over a clear background is half colour, half alpha), so the quad blends with `ONE, ONE_MINUS_SRC_ALPHA` and its opacity scales colour and alpha.
  - `spin` turns the node about its up axis and `clip` loops on an AnimationMixer, both on the UI clock (real time, or test steps), so they run under a paused menu.
  - The models' materials take any game shader, as in a level, from the canvas's `materials` (§3.2), on the canvas's clock. A model that draws one is drawn again every frame; a still model only when something about it changed.
  - Hidden or closed canvases don't render their models.
- **Opacity:** an element's `opacity`, animated or not, multiplies into its own and its descendants' material opacity.
- **Animations** (§10.5) change transforms and opacity per frame. Text is rasterised at the rest scale, so a pop doesn't re-render text each frame.
- **Colours:** linear in the files. three.js output encoding makes them sRGB, as Blender's Standard view does. Translucent elements blend in linear light (see **When**).
- **Scene view** (`setView('scene')`): the canvas layer is hidden, like the DOM overlays. The UI preview (`setView('ui')`) draws one canvas at the camera frame of a Blender view (§11.2).
- **WebGPU** (`renderer: 'webgpu'`): the same objects; `MeshBasicMaterial` converts to node materials. The UI blends in linear light as with WebGL (**When**, above): `WebGpuComposite` draws it through three/webgpu render targets, because drawn straight, three's output pass would tone map the UI. Models tone map as described above. Game shaders on UI meshes, `Shine` and `Gradient` included, need the WebGL renderer: the WebGPU renderer ignores shader patches, so those meshes draw as exported (§9).

### 5.4 Accessibility mirror

- **Mirror:** every open canvas also has a visually hidden DOM subtree, `div.gs-ui-canvas[data-canvas=<name>]`, with `pointer-events: none`.
  - Each visible text element gets a `div[data-name]` whose `textContent` is the rendered string.
  - Each button gets `div[role=button][data-label][aria-label]` (with `aria-disabled`), but no text of its own, so `innerText` doesn't list its label twice.
- **Why:** `document.body.innerText` (used by MCP playtests) and screen readers see the UI.
- **Hiding:** the mirror uses a clip and 1-px size, not `display: none` or `visibility: hidden`, so `innerText` includes it.

### 5.5 `ctx.ui` (additions; everything that exists today keeps working)

```ts
interface UiAPI {
  // existing: screen, close, current, isOpen, focused, pauseMenu
  /** Opens the Blender canvas `name` (menu or HUD). Unknown names warn once. */
  open(name: string): void;
  /** Canvas names the game has (from ui/index.json). */
  readonly canvases: readonly string[];
  /** One handler per event name; registering again replaces it (safe in start()). Returns an unsubscribe.
   *  Buttons fire after their action; sliders and toggles after each change, under the element's name. */
  on(event: string, handler: (ctx: SystemContext, info: UiEventInfo) => void): () => void;
  /** Overrides an element's text ('Name' or 'Canvas/Name'); null restores the authored body (and its bindings). */
  text(path: string, value: string | null): void;
  /** Shows or hides an element (overrides its start visibility). */
  show(path: string, visible?: boolean): void;
  hide(path: string): void;
  /** Sets a UiBar to value / its max (overrides its binding until null). */
  bar(path: string, value: number | null): void;
  /** Snapshot of an element: null when no open or loaded canvas has it. */
  element(path: string): UiElementInfo | null;
  /** The UI language's code (§5.9). Setting it switches every text and saves the choice; unknown codes warn once. */
  locale: string;
  /** The languages: [{ code, name }], each name in its own language, the authored language first. */
  readonly locales: readonly UiLocale[];
  /** `text` in the current language: its translation, else `text` as it is. */
  translate(text: string): string;
}

interface UiEventInfo {
  canvas: string;
  button: string;                          // the button's, slider's or toggle's object name
  kind?: 'button' | 'slider' | 'toggle';
  value?: number | boolean;                // sliders and toggles: the new value, already written to the path
}

interface UiElementInfo {
  canvas: string; name: string; id: string; type: 'group' | 'text' | 'image' | 'mesh' | 'model';
  visible: boolean; text: string | null;
  /** Screen rectangle in canvas CSS px, as laid out now. */
  rect: { x: number; y: number; width: number; height: number };
}
```

- **`screen(name, options?)`:** if a canvas called `name` exists, it opens that canvas. Only `options.pause` and `options.back` apply; other options warn once. Otherwise it opens a DOM screen, as today.
- **`close(name?)`:** with no name it closes the open menu (canvas or DOM). With a name it closes that canvas, HUD or menu.
- **`current`** is the open menu's name, canvas or DOM. **`isOpen()`** with no name means a menu is open; HUDs don't count. **`focused`** is the focused button's label: the rendered text of its first visible text descendant, else the element name.
- **`previous` history:**
  - It holds canvas and code-only menus, up to 16.
  - It is cleared when no menu is open.
- **Paths:** `'Name'` searches the open canvases, then the loaded ones. Names are unique within a UI file; `'Canvas/Name'` disambiguates.
- **Pause menu:** a canvas with `pauseMenu: true` is the pause menu, unless code calls `ctx.ui.pauseMenu(false)`. Code calling `ctx.ui.pauseMenu(options)` with no such canvas keeps today's DOM pause menu.
- **Exports:** `@gamestudio/engine` exports the types `UiElementInfo`, `UiEventHandler`, `UiEventInfo`, `UiButtonState` and `UiLocale`. The UI component handles are `UiButton`, `UiState` and the rest from §2.3. The DOM screen types became `UiScreenButton` and `UiTestState`, and the DOM screens' element is `game.ui.overlay`.
- **Button, slider and toggle sounds** play on the audio `ui` channel (§5.8), which plays while the game is paused, so a click sounds at once under a menu. It follows the master and `ui` volumes.
- **`isOpen()` and closing animations:** a close is immediate for the game (§10.5): `isOpen()` is false, the game resumes and the canvas takes no input, though it draws until its `close` animation ends.

### 5.6 Input, focus and pause

- **Menus:** the open menu canvas behaves like today's DOM screens:
  - it pauses while open (when `pause`);
  - navigation keys, Enter/Space, Escape (`back`), gamepad d-pad, stick, A, B and Start all work;
  - keys it uses don't reach gameplay, and a movement key that only moved focus counts as held again after it closes;
  - input is read once per frame, paused or not, in the game's clock;
  - mouse hover focuses, and click activates on release over the same button;
  - clicks anywhere while a menu is open are consumed (`Input.consumeMouse`), so gameplay never sees them;
  - a menu that opens during a frame's simulation (a start canvas after the first step, a title opened from `start()`) gets that frame's input too: its keys are read again, and its mouse presses and releases that came before the menu opened are hit-tested again against it. A playtest's `{"at": 0, "mouse": "click"}` on the title's Play presses it, as `{"at": 0, "key": "Enter"}` does. A press still held from then on is the menu's (consumed). Presses from earlier frames never reach it.
- **HUD canvases:**
  - Hovering or clicking a HUD button works.
  - A press over a HUD button is consumed; other clicks pass through to gameplay.
  - HUDs get no keyboard focus.
- **Navigation:**
  - With any `UiButton.order` > 0 in the canvas: Up and Left go to the previous button, Down, Right and Tab to the next, wrapping.
  - Otherwise it goes by position. **Up and Down go row by row:**
    1. The candidates are the enabled buttons that way: those whose centre is farther up (or down) than half the smaller of the two buttons' heights. Nearer than that, a button shares the focused button's row and is never above or below it.
    2. The nearest row wins: the candidates whose centres lie within half the focused button's height of the nearest candidate's.
    3. In that row, the button that overlaps the focused one most horizontally wins; when none overlaps, or two overlap alike (within half a pixel), the nearest centre across. Up from a centred Back under a right-aligned toggle row goes to the toggle, not to the slider above it that lines up with Back.
    4. With none that way, it wraps to the farthest row the other way, chosen in the same way. With none there either, focus stays. Ties go to the first button in element order.
  - **Left and Right go as browsers' spatial navigation does:**
    1. Buttons in the focused button's beam win: those whose rectangle overlaps its own vertically (the same row), the nearest along the axis first, then the nearest centre across it. Right from "−" in a "− bar +" row is "+", never a Back button below the bar.
    2. With none of those that way: from the focused button's centre, the enabled button within ±60° of the pressed direction with the lowest `hypot(along, 2 · offAxis)`.
    3. With none, it wraps to the farthest button strictly the other way: in the beam when there is one, else along that axis. With none there either, focus stays.
  - Tab and Shift+Tab use reading order: top to bottom, then left to right. Buttons count as one row within half the smaller button's height.
- **Sliders and toggles** (§2.3) are navigated with the buttons (`order` as for `UiButton`).
  - A focused slider takes the arrows along its axis (Left/Right for `right` and `left`, Up/Down for `up` and `down`; the d-pad and stick too). Each press moves it by `step`, or a tenth of the range when `step` is 0, toward `max` for the key pointing along `direction`. They don't move focus; the other axis's keys do.
  - Pressing on a slider's rect focuses it and sets the value from the pointer along the range; dragging keeps setting it until the release, snapped to `step`.
  - Enter, Space, gamepad A or a click flips a toggle.
  - HUD sliders and toggles work with the mouse, like HUD buttons.
  - A slider writing `audio.music` under a paused menu is heard at once: music plays under pause (§5.8).
- **Animations:** input is accepted during `open` and `loop`, and hit tests use the animated positions. A closing canvas takes none (§10.5).
- **Visual state:** a focused button scales by `focusScale` about its rectangle's centre. `UiState` children show per state. "Pressed" lasts while the mouse is down on it, or one frame after a key activation.
- **Code splitting:** the canvas renderer (`src/ui/canvas/`) is a separate chunk, dynamically imported only when the index lists a canvas. Games without Blender UI do not download it.

### 5.7 Test state

`game.test.state().ui` keeps today's keys and adds seven:

```json
{
  "screen": "Title", "focus": "Play",
  "buttons": [ { "label": "Play", "name": "PlayButton", "canvas": "Title", "kind": "button", "disabled": false,
                 "x": 640, "y": 420, "width": 240, "height": 56 } ],
  "size": [1280, 720],
  "open": ["HUD", "Title"],
  "animating": ["Title"],
  "texts": { "HUD/Score": "Score 0", "Title/TitleText": "Gem Island" },
  "hidden": ["HUD/BossBar", "HUD/Heart3"],
  "locale": "en"
}
```

- **`buttons`:** the open menu's buttons, sliders and toggles, or the open HUDs' when no menu is open. `x`/`y` are the centres in canvas CSS px, where a scripted click lands on them (animated positions during an animation). `kind` is `button`, `slider` or `toggle`; sliders and toggles add `value` (a number, or `true`/`false`). To set a slider exactly, focus it and press its arrow key once per `step`.
- **`size`:** `[width, height]` of the canvas in CSS px. `x`/`y` are in the canvas size `size`; scale them if your playtest uses another size.
  - `state()`, `ctx.ui.element()` and hit tests re-lay out first when the canvas was resized since the last frame, so coordinates always match the canvas as it is when read.
- **`open`:** the open canvases, plus a DOM screen's name. A closing canvas stays listed (with its `texts`) until its close animation ends.
- **`animating`:** the canvases of `open` whose `open` or `close` animation (§10.5) is still playing, in the same order; a `loop` doesn't count. A canvas opened since the last frame is listed at once when it has an `open` animation. A screenshot tool waits until the menu it shows is no longer listed: `[]` means everything drawn is at rest (or looping).
- **`texts`:** the rendered string of every visible text element of every open canvas.
- **`hidden`:** `'Canvas/Name'` of every element of the open canvases that is hidden itself, sorted: by `ctx.ui.hide` / `show(false)`, or authored hidden ("Disable in Renders") and not shown since. Left out: elements hidden only because an ancestor is hidden (the ancestor is listed), `UiState` art, which shows by button state, and elements hidden only by their `UiShow` rule or as a toggle's `on`/`off` (`texts` and `ctx.ui.element(path).visible` show those). Hearts made with code (`ctx.ui.show`) are asserted without screenshots: `["HUD/Heart3"]` after a life is lost.
- **`locale`** (Phase 3): the language shown, `ctx.ui.locale` (§5.9). `texts`, `focus`, button labels and the accessibility mirror show the translation.

`status().screen` is unchanged: the open menu.

### 5.8 Audio channels

Options screens set volumes through value paths (§3.4), so the engine mixes sounds in channels. Engine `API.md` §16 is the full audio reference.

- **Channels:** `master`, `music`, `sfx` and `ui`. A sound's volume is `master × channel × source volume`.
  - `AudioSource.channel` (`sfx` | `music`, default `sfx`) picks a source's channel; `ctx.audio.play(clip, {channel})` takes `ui` too.
  - Button, slider and toggle sounds play on `ui`.
- **API:** `ctx.audio.volume` and `muted` stay the master. `ctx.audio.channel(name)` returns the live `{ name, volume, muted }`, which can be read and set.
- **Persistence:** channel volumes and mutes, master included, are saved in the game's save store (`ctx.save` key `gs.audio`) on every change and restored at start, before any sound plays. An Options screen needs no save code. Under a test clock `ctx.save` is a store of its own, so every playtest starts at the defaults (all 1, unmuted) and never changes the player's settings.
- **Pause:** when the game pauses (a menu, `game.pause`, stepping), `sfx` sounds pause where they are and resume from there; new `sfx` sounds started while paused wait for the resume. `music` keeps playing, so a volume slider in a pause menu is heard at once, and `ui` always plays. The AudioContext is no longer suspended on pause.
- **Stop:** Stop, a scene reload and a test reset stop every sound, except that a `ui` one-shot finishes across a scene change (the click of the button that loaded the level).

### 5.9 Translations

The tables are §3.6; engine `API.md` §22 (Translations) is the game-facing summary.

- **The language** is `ctx.ui.locale`, one of `sourceLocale` and the codes of `locales` (§3.1).
  - At start it is the player's saved choice (`ctx.save` key `gs.locale`), else the best match of `navigator.languages`, else `sourceLocale`. A match is the same code (any case, `_` as `-`), else the same language: `fr-CA` gets `fr`, else `fr-FR`.
  - Setting it switches every text at once and saves it. An unknown code warns once and changes nothing.
  - `ctx.ui.locales` lists `[{code, name}]`, the source language first, each name in its own language (the table's `name`; the source language's from `Intl.DisplayNames`, else its code).
- **Lookup:** every canvas text body is looked up in the current table as authored, before its bindings are evaluated. A body with no entry, or an empty one, shows as authored. Labels, `focus`, `state().ui.texts` and the accessibility mirror all show the translation.
- **Code strings:** `ctx.ui.translate(text)` looks a string up the same way: `ctx.ui.text('Banner', ctx.ui.translate('Crashed!'))`. It works without canvases too, for code-only screens.
- **No code:** value paths `ui.locale` (read and write) and `ui.localeName` (read, §3.4); a `UiButton` with `action: "locale"` switches to its `target`, or with an empty `target` to the next language, wrapping. A Language button's label can be `{ui.localeName}`, which shows `Français` while French is on.
- **Live:** `ui.locales` (§4) replaces the languages and tables of a running game, and the shown texts follow.
- **Tests:** a test has its own empty save, so a playtest starts in the browser's language. `game.test.begin({ save: { 'gs.locale': 'fr' } })` (`playtest`'s `set: {save: {"gs.locale": "fr"}}`) starts in French; `state().ui.locale` and `state().ui.texts` show the result (§5.7).
- **Fonts:** a glyph the font lacks falls back to the browser's system fonts (§5.3); pick fonts that cover the languages you ship.

## 6. Python API for tools and agents: `gs.gameui`

`blender/gamestudio/gameui/` is reachable from `run_python` as `gs.gameui`. The operators, the MCP bridge methods and template builders all use it.

- **Coordinates** are canvas px (origin top-left, y down).
- **`at`** is the element's origin. Boxes (panels, images, buttons, bars, sliders, toggles, icon rows) are centred on their origin. Text is aligned around it.
- **Colours** are `#RRGGBB` / `#RRGGBBAA` strings (sRGB) or linear RGBA lists.

```python
gameui.ui_file(root=None) -> pathlib.Path            # <root>/ui/ui.blend (not created)
gameui.is_ui_file(path=None) -> bool                  # default: the open file
gameui.new_ui_file(root=None, canvases=()) -> Path    # writes ui/ui.blend (background Blender when another file is open); refuses to overwrite
gameui.canvas(name, kind="menu", open="manual", pause=True, pause_menu=False, back="none",
              scale_mode="fit", backdrop=None, layer=0, size=(1920, 1080), focus=None) -> bpy.types.Scene   # create or update, open UI file only
gameui.text(canvas, name, body, at, size=48, font="", color="#FFFFFF", align="center", valign="center",
            box=None, parent=None, anchor=None, z=None, outline=0, outline_color="#000000", shadow=None,
            visible=True, project_name=False) -> Object   # project_name: the game's title (below)
gameui.image(canvas, name, path, at, width=None, height=None, parent=None, anchor=None, z=None, opacity=1.0,
             visible=True) -> Object
gameui.panel(canvas, name, at, size, color="#000000AA", radius=0, border=0, border_color="#FFFFFF",
             parent=None, anchor=None, z=None, visible=True,
             image=None, slice=None,
             shader=None, shader_values=None) -> Object  # flat mesh, Emission material; image: textured; slice: 9-slice;
                                                         # shader: a game shader on its own material (below)
gameui.button(canvas, name, label, at, size=(360, 84), action="event", target="", font="", text_size=40,
              color="#FFFFFF33", text_color="#FFFFFF", focus_color="#FFD400", focus_text_color="#17171C",
              radius=42, parent=None, anchor=None, z=None, order=0, sound="", focus_sound="",
              visible=True) -> Object   # group + normal panel + focused panel (UiState) + label
gameui.bar(canvas, name, at, size, value="", max=1.0, color="#4CAF50", back_color="#00000080", radius=0,
           direction="right", parent=None, anchor=None, z=None, visible=True) -> Object
gameui.group(canvas, name, at, parent=None, anchor=None, z=None, visible=True) -> Object
gameui.icons(canvas, name, at, count=3, value="lives", image="assets/ui/heart.png", empty="assets/ui/heart-empty.png",
             size=52, spacing=12, parent=None, anchor=None, z=None, visible=True) -> Object   # icon i: UiShow value >= i
gameui.slider(canvas, name, at, size=(360, 28), value="audio.music", min=0, max=1, step=0.1, color="#FFD400",
              back_color="#FFFFFF33", handle=True, handle_color="#FFFFFF", radius=None, direction="right",
              parent=None, anchor=None, z=None, order=0, sound="", visible=True, disabled=False) -> Object
gameui.toggle(canvas, name, at, value, size=(96, 48), on_color="#FFD400", off_color="#FFFFFF33", knob_color="#FFFFFF",
              parent=None, anchor=None, z=None, order=0, sound="", visible=True, disabled=False) -> Object
gameui.model(canvas, name, at, size=(320, 320), source=None, kit=None, kit_params=None, asset=None,
             view="threeQuarter", zoom=1, spin=0.6, clip="", light="studio", background=None,
             parent=None, anchor=None, z=None, visible=True) -> Object   # a 3D model (below)
gameui.animate(canvas, open="pop", close="fade", duration=0.25, loop=None) -> bpy.types.Scene   # §10.5 presets
gameui.strings(locale=None) -> dict                     # ui_strings: the texts to translate (below)
gameui.translate(locale, strings, name=None, remove=()) -> dict   # ui_translate: writes ui/locales/<locale>.json
gameui.set_font(target, path) -> bpy.types.VectorFont   # target: text object or canvas (all its texts); copies into assets/fonts/
gameui.remove(canvas, name) -> None
gameui.describe(canvas=None) -> dict                   # JSON-able: canvases, settings, elements with px rects and components (ui_list)
gameui.to_px(scene, xy) / gameui.from_px(scene, px)    # §2.1
```

- Also:
  - `gameui.update(canvas, name, **changes)` takes the `ui_set` keys: `value`, `min`, `max` and `step` for sliders (`value` and `max` for bars too, `value` for toggles and icon rows), `count` for icon rows, `loop` for any element, `size`, `view`, `zoom`, `spin`, `clip`, `light` and `background` for models, `shader` and `shader_values` for panels and other meshes, `project_name` for texts.
  - `gameui.remove(canvas)` with no name removes the whole canvas.
  - `gameui.describe_element`, `find_element`, `resolve_anchor` and `in_ui_file` are helpers.
- Errors are `gameui.GameUIError` (a `ValueError`) with `.code` and `.hint`.
- Names are object names. When a name is taken, the call updates that element instead of creating another: same kind → its settings and position change; a different kind is an error. On update, arguments left at `None` keep their current values.
- `box` is `[w, h]` centred on `at`, or `[x, y, w, h]`. `shadow` is `[x, y]` or `[x, y, blur]`; `update` takes it for texts too.
- **`visible`** (every element function): `False` makes the element start hidden, "Disable in Renders" (§2.2), until game code shows it (`ctx.ui.show`). A new element is visible; on an update, `None` keeps it.
- **`loop`** (every element function, and `update`): the element's own loop, `"pulse"`, `"bob"` or `"blink"` (the `animate` loop presets), played over and over while the canvas is open; `"none"` removes it (`update` takes `None` too), and `None` keeps it in an element function.
  - It keys that element only, in its local transform, so children loop in their parent's space, over the canvas's loop range. On a top-level element it replaces the canvas's `animate` loop.
  - A canvas without a loop range gets one, with its `loop` marker: one cycle of the slowest element loop, laid out between `open` and `close`, whose presets and keys stay. A range there already keeps its length. Each element loop fits the whole cycles that round to its own length (at least one).
  - The last element loop of a canvas without presets takes the range, the marker and the canvas's record with it; removing a looping element (or its loop) shortens the layout again.
  - The object's tag records it (`own_loop` in `gs_ui_anim`), so `animate` re-runs keep it, never duplicate it, and builder changes re-key it from the element's new place.
  - Refused (before anything changes) on a canvas whose `open`, `close` or `loop` markers were made by hand without `animate`, and when the range changes and a locked element would have to be keyed again.
- **`describe_element`** (each element of `describe`, `ui_list`) leaves defaults out. Besides name, kind, rect, visibility, anchor, text, colour and components it gives:
  - `radius` for panels, buttons, bars and their parts (the corner radius in px, as the builder made it; a panel's is at most half its shorter side);
  - `border` and `border_color` for panels with a border;
  - `rotation` for a rotated element: degrees, clockwise on the canvas (y down), the turn the export's `transform.rotation` (§3.2) has, but in degrees and with its parents' rotation added, like `at`;
  - `image` and `slice` for textured panels; `value`, `min`, `max` and `step` for sliders (`kind: "slider"`); `value` for toggles; `count`, `value`, `image`, `empty`, `icon_size` and `spacing` for icon rows (`kind: "icons"`);
  - `loop`, the element's own loop, while its keys and the `loop` marker are as the builder made them;
  - for a 3D model, `type: "model"`, `size`, `view`, `zoom`, `spin`, `clip`, `light`, `background` (hex), `source` (what it shows, per object: `{object, kit}`, `{object, asset, library}`, `{object, collection}` or `{object, type}`) and `clips` (the clips `clip` can name);
  - `shader` and the non-default `shader_values` for a mesh with a game shader, and `project_name: true` for the game's title text.
- **`describe_canvas`** (`ui_canvas`, `ui_list`) gives `animations` (`{open, close, loop}` seconds, from the markers) for a canvas that has them, and the `transition` presets `animate` made, as they still are: a preset whose marker is gone, or whose keys on a top-level element were changed by hand, is left out and named in `transition.edited` (`["loop"]`). A canvas whose only animations are element loops has no `transition`.
- **`own_parts(obj)`** lists the parts the builder made for an element (and their parts, such as a toggle's `OnKnob`), never the elements inside it or their parts.
- **Canvas `focus`** is the button selected first when the menu opens: the canvas camera's `UiCanvas.focus`. It is a button element of that canvas (one with `UiButton`), as a name or an object, so it is set after the buttons exist. `""` clears it: then the first button in navigation order is selected.
- **Button `sound` and `focus_sound`** set `UiButton.sound` (played when pressed) and `focusSound` (when selected). Each is a `.ogg`, `.mp3` or `.wav` file of the project, project-relative or absolute, and is stored project-relative. `""` clears it. A missing file, another file type or a file outside the project is refused. `update` takes them for any element with `UiButton`; another element becomes a button first (`action` in the same call).
- **Parts the builder makes** (all named after the element):
  - Button: a group with `UiButton` and children `<name>Bg`, `<name>Focus` (`UiState focusedOrPressed`), `<name>Label`, plus `<name>FocusLabel` (`focusedOrPressed`) when the focus text colour differs. They are stacked Bg < Label < Focus < FocusLabel, so the focus art covers the normal label; any update re-stacks older buttons.
  - Bar: `<name>Bg` and `<name>Fill`, with `UiBar.fill` pointing at the fill.
  - Panel border: a child ring, `<name>Border`.
  - Icon row (`icons`): a group centred on `at` with `count` slot images `<name>Slot<i>` (`empty`; `""` for none) and `count` icons `<name><i>` (`image`), i = 1 … count, `spacing` px apart. Icon i has `UiShow {value, atLeast, i}`. `size` is one icon's longer side in px. At most 20.
  - Slider: a group with `UiSlider` and `<name>Bg` (the track, `back_color`), `<name>Fill` (`color`; its bounds are the range) and, with `handle`, the round `<name>Handle` (1.5 × the thickness). `radius` defaults to half the thickness (a pill); a vertical slider is `size` [28, 360] with `direction: "up"`.
  - Toggle: a group with `UiToggle` and `<name>Bg` (the track in `off_color`), `<name>Off` (the knob on the left) and `<name>On` (the track in `on_color` with its knob `<name>OnKnob` on the right). Blender shows the On look; the game shows On while the value is truthy. `value` is required when it is created.
  - Model: an empty (`gs_ui_kind` `model`) with `UiModel`; a kit's content is named `<name>Model` unless `kit_params.name` says otherwise.
  - Textured panel (`image`): the image is drawn instead of a colour, stretched over the panel; `image=""` makes it flat again. With `slice` ([left, right, top, bottom] image px, or one number for all four; `[]` removes it) it is 9-slice art: 9 quads with matching UVs and `UiSlice`, rebuilt whenever the panel is resized through the builder, `ui_set size` or the Layout box W/H. Scaling the object with S does not rebuild it, so resize 9-slice panels with W/H. No `color`, `radius` or `border` with an image.
- **Controls** (sliders, toggles) are not buttons: `update` refuses `action`, `target` and `focus_sound` on them, and they take `disabled`, `order` and `sound` of their own.
- **3D models** (`model`, Phase 3):
  - One of `kit`, `asset` or `source` is needed when the model is new. `kit` builds with `gs.kits.create(kit, kit_params)`; kits that make no mesh (the sky's sun) are refused. `asset` places a library asset as a collection instance (`assetlib.place_asset`; a name, or `(library, name)` from Python). `source` is an object of the file, moved into the canvas scene and under the empty with its children, or a collection, instanced and unlinked from the canvases' collections so its objects are not elements too.
  - Giving one again replaces the content: the new one is made and checked first, then the old one is deleted.
  - In Blender the content is scaled uniformly so its longest side (unevaluated bounds, through collection instances) is 80 % of `size`'s shorter side, centred on the empty in x and y, with its top at the empty's Z: it hangs below the element, so it covers what is drawn under the model and nothing above. A new `size` scales it by the shorter sides' ratio.
  - `clip` is checked against the model's clips (the `list_clips` names: single-strip NLA tracks, then an active action, through instanced collections), on new content before the old goes.
  - Objects inside a model are left out of `elements()`, `describe`, the draw order of new elements, `find_element` and parenting (§2.2).
- **Game shaders on panels** (Phase 3): `panel(..., shader=, shader_values=)`, and `update` on panels and other meshes.
  - A shaded panel gets a material of its own (`gs_ui_shaded`), a copy of the shared colour or image material without its `gs_ui_color`/`gs_ui_texture` tag, named `UI <panel>`, and `core/shaders.set_shader` names the shader on it.
  - A new `color` or `image` makes a new copy that keeps the shader and its values (the old copy is deleted); `shader=""` puts the shared material back. Shared materials given a shader by hand are no longer reused for new panels of their colour.
  - `shader` must be in the registry (else the known names are suggested); `shader_values` are checked per field. Only shaders with `ui: true` draw on UI meshes (§5.3).
- **The game's name** (`project_name`, Phase 3): `text(..., project_name=True)` marks a text whose body is the game's name (`obj["gs_ui_project_name"] = True`), so a game made from a template saved from this one shows its own name there (§11.7). `None` keeps the mark on an update and `False` removes it. The starter's and the genre templates' `GameTitle` are marked.
- **Translations** (`gameui/locales.py`, Phase 3): `strings` and `translate` work on the tables of §3.6, from any file of the project.
  - Codes are checked and normalised (§3.6); anything else is `invalid_locale`. The source language has no table.
  - **`strings(locale=None)`** (`ui_strings`) reads the open UI file's canvases, or with another file open the exported canvases (`source: "export"`), so it never needs `ui_open`. Each text is listed once, `{text, elements: ["Canvas/Name", ...]}` (a button's label and focus label share one), with `project_name` for the title; bodies with no letters outside their bindings (`{score}`, `3`) are left out. It also gives `source_locale`, `locales` (`[{code, name, file, strings}]`), and with a locale `translation` per text, `translated`, `missing`, `name`, `file`, `exists` and `unused` (the table's strings no element shows: `ctx.ui.translate` strings, or texts changed since).
  - **`translate(locale, strings, name=None, remove=())`** (`ui_translate`) checks everything before writing: non-empty translations, the same bindings as the text (`{{`/`}}` are braces), and `name` (1–60 characters) when the table is new. It merges into the table, keeping its order, writes it as UTF-8 JSON, then exports the tables (`gameui_export.export_locales`) and sends them to a running game (`gameui_live.send_locales`). It writes files only: no undo step. Result: `{locale, name, file, created, added, changed, removed, strings, missing, unused, not_found?, exported: {locales, changed, warnings?} | export_error, sent}`.
  - **Language buttons:** the builder normalises a `locale` button's `target` and refuses one that is not a code (a button switched to `locale` keeping an `open` target such as `Options` is refused too); `""` is the next language.
- **`animate`** (§10.5) writes the presets as keyframes on the canvas's top-level elements (location, scale and the object colour's alpha) and the timeline markers `open`, `close` and `loop`, laid out from frame 1: open, then one loop cycle, then close. `open` and `close` are `fade`, `pop` (from 90 % of the size, with a fade), `slide-up`, `slide-down`, `slide-left`, `slide-right` (a tenth of the screen that way, with a fade) or `none`; `duration` is the seconds of each; `loop` is `pulse` (scale to 104 %, 1.2 s), `bob` (8 px up, 1.6 s), `blink` (opacity to 35 %, 1 s) or `None`.
  - Running it again replaces the keys and markers it made (a hand-made marker of the same name too); all `none` removes them. Element loops (`loop`) stay. It tags what it made (`gs_ui_anim` on the scene and each keyed object).
  - The scene's frame range covers the ranges, and its current frame is left at the rest frame (where `open` ends).
  - Builder calls work at the rest frame, and an element added, moved or resized through the builder (or the Layout box) is keyed again from its new place. A keyed top-level element moved by hand (G) snaps back to its keys: insert a keyframe, or move it with `ui_set` / the Layout box.
- **Materials:** Emission, shared per colour. With alpha < 1 they are mixed with a Transparent BSDF, drawn Blended.
- `new_ui_file` in an untitled, unchanged session builds the file in this Blender and leaves it open; otherwise it uses a background Blender.
- `canvas` is a canvas name or a Scene.
- `parent` is an element name.
- `anchor` takes a `UiAnchor.anchor` value, or a `(anchor, stretch)` pair.

## 7. Blender authoring tools (add-on)

1. **GameStudio › Edit Game UI.**
   - It opens `ui/ui.blend`. If the file is missing, it creates it with one empty menu canvas, "Menu".
   - When the open file has unsaved changes, it asks first: Save and Open, Don't Save, Cancel.
   - **GameStudio › Back to Level** opens the start level.
2. **Opening a UI file:**
   - 3D Views go to camera view of the canvas camera, in Material Preview, with increment snapping (10 px).
   - The canvas frame is the camera frame.
   - Switching scenes switches canvases.
3. **Shift+A › Game UI** (in UI files): Text, Image…, 3D Model…, Panel, Button, Bar, Slider, Toggle, Group.
   - New elements land at the 3D cursor when it is inside the canvas frame, else at the canvas centre, at a Z above the highest element.
   - Image… copies the file into `assets/ui/` when it is outside the project and sizes it 1 image px = 1 design px.
   - 3D Model… asks what it shows: a kit (default `collectible`), a library asset or an object of the file (§6).
   - A new Slider controls `audio.music`; a new Toggle has no value yet: set `UiToggle.value` in Object › Game.
4. **Scene › New Game UI Screen** (in UI files) copies the canvas camera setup.
5. **Properties:**
   - **Scene › Game UI Screen:** the canvas camera's `UiCanvas` fields, design size, "Show in Game" and "Frame Canvas".
   - **Object › Game:** UI components, plus a small "Layout" box with the element's px position and size (editable) and its resolved anchor. W/H resize panels (9-slice ones rebuild their quads), images, buttons, bars, sliders, toggles and 3D models. On an animated canvas the box works at the rest frame and re-keys what it moves.
   - **Text › Font:** "Choose Font…" copies into `assets/fonts/`.
   - **Material › Game:** a UI mesh's material picks a game shader as a level material does (§3.5); only shaders made for UI draw on it in the game.
6. **Add Component menu in UI files:** UI components first, other categories under "More".
7. **Viewport overlay in UI files:** the selected top-level element's anchor point on the frame and a thin line to it, and the view rectangle (`UiModel.size`) of every 3D model, which its empty alone doesn't show.
8. **Animations** are made with Blender's own timeline (§10.5): markers named `open`, `close` and `loop`, and keyframes on element location, Z rotation, scale and the Viewport Display colour's alpha. `gs.gameui.animate` (or `ui_canvas transition`) writes presets to start from.
9. **The game-rendered preview:** in a UI file, the Game toggle of a 3D View (the Scene view), looking through the canvas camera (Numpad 0), shows the active screen as the game draws it: fonts, text layout, backdrop, 9-slices, game shaders, loops, spinning models, and placeholders such as `{score}` for live values (§11.2).
10. **Translations** have no Blender panel: the tables are JSON files in `ui/locales/` (§3.6), edited by hand or with `ui_translate`. While the UI file is open, a running game gets a saved table at once (§4); `ui_translate` sends it from any file.

## 8. MCP tools

These are thin wrappers over bridge methods of the same name in `blender/gamestudio/bridge/gameui.py`, which call `gs.gameui`.

| Tool | R/W | Params | Result |
|---|---|---|---|
| `ui_list` | R | `canvas?` | `gameui.describe(canvas)` |
| `ui_open` | W | `create?` (true), `save?` (false) | Opens `ui/ui.blend` in Blender, creating it when missing. Refuses when the open file has unsaved changes unless `save: true`. Returns `{file, canvases}` |
| `ui_canvas` | W | `name`, `kind?`, `open?`, `pause?`, `pause_menu?`, `back?`, `scale_mode?`, `backdrop?`, `layer?`, `size?`, `focus?`, `transition?` | Create or update a canvas; returns its description |
| `ui_add` | W | `canvas`, `kind` (`text`, `image`, `panel`, `button`, `bar`, `group`, `icons`, `slider`, `toggle`, `model`), `name`, `at`, plus that kind's `gs.gameui` keyword arguments; every kind takes `parent`, `anchor`, `stretch`, `z`, `visible` and `loop` | Creates or updates an element; returns its description, its own parts and the parts the change removed |
| `ui_set` | W | `canvas`, `name`, any of `at`, `z`, `size`, `body`, `font`, `text_size`, `color`, `align`, `valign`, `shadow`, `visible`, `anchor`, `stretch`, `action`, `target`, `disabled`, `order`, `sound`, `focus_sound`, `value`, `min`, `max`, `step`, `count`, `parent`, `loop`, `view`, `zoom`, `spin`, `clip`, `light`, `background`, `shader`, `shader_values`, `project_name` | Updates an element |
| `ui_remove` | D | `canvas`, `name` (or only `canvas` to remove a whole canvas) | |
| `ui_strings` | R | `locale?` | `gameui.strings(locale)` (§6, Translations); works with a level open too (the exported texts) |
| `ui_translate` | W | `locale`, `name?`, `strings`, `remove?` | `gameui.translate(...)` (§6, Translations): writes `ui/locales/<locale>.json` (§3.6), exports and sends it; no undo step |

- **`at`:** always absolute canvas px, children included. The builder converts it to parent-local.
- **`ui_add`:**
  - A text's font size is `size` (a number). `text_size` is accepted too, and Node maps it to `size`.
  - Box kinds take `size: [w, h]`.
  - A text `box` is `[width, height]`; a text `shadow` is `[x, y]` or `[x, y, blur]` px.
  - Buttons take `sound` and `focus_sound` (§6).
  - `icons` take `count`, `value`, `image`, `empty`, `size` (one icon, a number) and `spacing`; `slider` takes `size`, `value`, `min`, `max`, `step`, `color`, `back_color`, `handle`, `handle_color`, `radius`, `direction`, `order` and `sound`; `toggle` takes `value` (required), `size`, `on_color`, `off_color`, `knob_color`, `order` and `sound`; `panel` also takes `image` and `slice`, and `shader` and `shader_values` (§6).
  - `model` takes one of `kit` (with `kit_params`, as `create_from_kit` takes them), `asset` (a library asset) or `source` (an object or collection of the UI file), needed when it is new, and `size`, `view`, `zoom`, `spin`, `clip`, `light` and `background` (§2.3, §6). Giving `kit`, `asset` or `source` again replaces what it shows.
  - A text takes `project_name` (true: its body is the game's name, §6).
  - A button with `action: "locale"` switches the language: `target` a code, or `""` for the next language (§5.9). Its label can be `{ui.localeName}`.
  - `visible: false` adds the element hidden (§6), e.g. a "New best!" text that code shows; no `ui_set` needed.
  - `loop` (`pulse`, `bob`, `blink`, `none`) gives the element a loop of its own (§6), e.g. a Play button that pulses.
  - `stretch` is sent to the bridge folded into `anchor: [anchor or 'auto', stretch]`.
  - Node refuses, before calling Blender:
    - arguments of another kind;
    - `open` or `load` actions without `target`;
    - `anchor`/`stretch` together with `parent`;
    - a `sound` or `focus_sound` that is not a `.ogg`, `.mp3` or `.wav` path (Blender checks the file is in the project);
    - a `value` in braces (a path is written without them), a slider `min` not below its `max`, a toggle without `value`, an icon row's `size` as a list;
    - `slice` without `image`, and `color`, `radius` or `border` with an `image` panel;
    - a model given more than one of `kit`, `asset` and `source`, and `kit_params` without `kit`;
    - `shader_values` without a shader (or with `shader: ""`);
    - a `locale` button's `target` that is not a language code.
- **`ui_set`:**
  - `size` is `[w, h]` only; the font size is `text_size`. On a 9-slice panel it rebuilds the quads.
  - `parent: null` makes the element top-level.
  - `anchor` and `stretch` are separate keys.
  - `shadow` goes with texts; `sound` and `focus_sound` with elements that have `UiButton` (`""` removes one).
  - `value` goes with bars, sliders, toggles and icon rows; `min`, `max` and `step` with sliders (`max` with bars too); `count` with icon rows. Sliders and toggles take `disabled`, `order` and `sound`, never `action` (§6).
  - `loop` goes with any element (§6); `"none"` removes it.
  - `view`, `zoom`, `spin`, `clip`, `light` and `background` go with 3D models, and `size` rescales a model in Blender too; what a model shows changes with `ui_add` (`kit`, `asset` or `source` again). `shader` and `shader_values` go with panels and other meshes (`shader: ""` removes it), `project_name` with texts.
- **`ui_strings` and `ui_translate`** (§6, Translations) work from any file of the project: `ui_strings` reads the exported canvases when the UI file is not open, and `ui_translate` writes JSON, not the scene, so it records no undo step. `ui_translate` exports the tables and sends them to a running game.
- **`ui_canvas`:**
  - `focus` names a button, slider or toggle of that canvas, selected first when the menu opens (§6). `null` or `""` clears it.
  - `transition: {open?, close?, duration?, loop?}` calls `gameui.animate` (§6, §10.5) after the settings. Keys left out keep the canvas's current presets (`pop` and `fade` on a canvas without), and `null` removes them all; element loops stay. It refuses locked top-level elements and locked elements with a loop of their own.
  - Node refuses `focus`, `pause`, `pause_menu`, `back` and `backdrop` together with `kind: "hud"`.
  - The settings are the `UiCanvas` component of the canvas camera, the object `<Canvas> Camera`.
- The write tools work on the open UI file; otherwise they return `ui_file_not_open`, whose hint suggests `ui_open`. `ui_strings` and `ui_translate` work from any file.
- **`export_scene` with a UI file open** (Play, `playtest`):
  - It exports the UI file's canvases and the index.
  - It returns the **start level's** export, made in a background Blender only when it is missing.
  - With `all: true` it exports every level and every UI file.
- Each write is one undo step ("Agent: ui_add …"), except `ui_open`, which loads a file. Writes refuse objects locked from AI, button and bar parts included.
- Unknown keys are refused with `INVALID_PARAMS` and a "Did you mean" hint.
- **Bridge error codes** (`data.code`): `ui_file_not_open`, `unsaved_changes`, `ui_file_missing`, `no_project`, `no_canvas`, `no_element`, `ui_components_missing` (the registry has no UI components yet: start the dev server or run `npm run registry`), `ui_file_exists`, `invalid_locale`.
- **Results:**
  - `ui_add`: `{canvas, created, element, parts?, removed?}`.
  - `ui_set`: `{canvas, element, parts?, removed?}`.
  - `parts` are the element's own parts (`own_parts`, §6), not those of elements inside it. `removed` names the parts the change deleted: the `<name>Border` ring of a bordered panel given 9-slice art (or `border: 0`), icons and slots beyond a smaller `count`, a slider's handle with `handle: false`, a button's `FocusLabel` once the focus text colour matches.
  - `ui_remove`: `{canvas, removed}`, or `{removed_canvas, removed, canvases}` when the whole canvas goes.
  - `ui_open`: `{file, created, canvases: [{name, kind, open, elements}]}`.
  - `ui_list` outside a UI file: `{file: null, open: false, ui_file, exists, canvases: [], hint}`.
  - Element descriptions (`ui_list`, `element`, `parts`) are `describe_element` (§6): with `radius`, `border`, `border_color` and `rotation` when they are not 0, and the settings of sliders, toggles, icon rows and textured panels.
  - Canvas descriptions (`ui_canvas`, `ui_list`) add `animations` and `transition` for an animated canvas (`transition.edited` for presets changed by hand, §6), and elements their `loop`.
- **`game_screenshot`** gains `ui?: string` and `set?: {store?, save?}`, which can't be combined with `mode: "scene"`.
  - `set` assigns `ctx.store` keys and calls `ctx.save.set` for each `save` key in the page first. That is the headless game page's real save, kept in its browser profile.
  - It waits up to 3 s for the name to appear in `window.__gs.game.ui.canvases`. That list is always an array, empty with no UI.
  - When another menu is open (`test.state().ui.screen`), it sends `ui.close` and waits (up to 3 s) until `ui.open` no longer lists it: its close animation has ended, so two menus never overlap. The result's `ui_closed` names it.
  - It then sends `ui.open`, waits until `test.state().ui.open` lists the canvas, then until `ui.animating` (§5.7) no longer lists it (up to 11 s, with a warning after). An engine without `ui.animating` waits the canvas's open duration + 100 ms instead, from its canvas data or else its export. Then two frames are drawn and it shoots; `wait_ms` waits on top.
  - The canvas stays open afterwards.
  - Unknown names give `unknown_canvas`; an engine without the list gives `ui_unsupported`.
  - Its result gains `ui` (§5.7) in game mode.
- **`compare_views`** closes open canvases (`ui.close`) before its game shot. Closing a pausing menu on a stopped game never starts the game.
- `playtest`'s `final.ui` gains `open`, `texts`, `size`, `hidden` and `locale` (§5.7). `playtest` also takes `set: {store?, save?}`, given to `game.test.begin({store, save})`, and reports `final.save` (`game.test.saved()`), so settings and records can be tested without code. `set: {save: {"gs.locale": "fr"}}` plays in French (§5.9).
- **Button coordinates:** `x`/`y` in `final.ui.buttons` and in `game_screenshot`'s `ui.buttons` are CSS px of the page's canvas, whose size is `ui.size` (§5.7). A playtest of another `size` scales them: `x · size[0] / ui.size[0]`, `y · size[1] / ui.size[1]`.
- The templates' `.claude/settings.json` pre-approves the read and write tools.
- **From the fourth cold-start trial:**
  - `game_screenshot` reads `ui` before restoring the page, so `ui.size` is the shot's size and button x/y are pixels of the returned image.
  - `shader_values` (and `set_material_shader` values) take `"#RRGGBB(AA)"` for colour fields, converted from sRGB to linear.
  - `ui_set` takes `text_color`, `focus_color` and `focus_text_color` for buttons, and works on part names (`PlayButtonFocus`).
  - A model's `source` may be `"scenes/<level>.blend:<Object>"`: that object's hierarchy is library-linked into the UI file, so it follows the level, with its position in the level cancelled.
  - `ui_strings` doesn't count `project_name` texts in `missing`.
  - Texts in `ui_list` carry an approximate `rect` from Blender's evaluated bounds.
  - Saving a UI file returns `ui_export`.
  - `playtest` `final.save` includes `gs.locale` and `gs.audio`.

## 9. Not done yet

Phase 2 (§10) added open, close and loop animations, 9-slice panels, sliders and toggles; Phase 3 (§11) 3D models, the game-rendered preview, translations, game shaders on UI meshes, variable fonts, linear blending on WebGPU and project names in saved templates. Still missing:

- per-level UI overrides (a level's own HUD layout or texts);
- pixel-art images: a source for `pixelated` (always `false`; Blender image empties have no interpolation setting, so it would be a component field);
- canvas elements that follow world entities (`ctx.hud.set(..., {at})` covers it);
- `Shine` and `Gradient` on WebGPU: the shader library doesn't run on the WebGPU renderer, which ignores shader patches, so UI meshes with a game shader draw as exported there (§5.3);
- `ctx.shaders` on UI materials: game code can't change a UI mesh's shader values; they are set in Blender;
- image fields of game shaders on UI meshes come out flipped: shader textures load in glTF orientation (`flipY` false) while UI meshes keep Blender's UVs (v up). `Shine` and `Gradient` read no image;
- variable WOFF2 fonts other than Inter: neither Blender's Python nor the browser can read their axes (Brotli), so they are not pinned and Chrome picks the optical size from the font size (§5.3);
- hover and press animations from Blender actions (buttons scale by `focusScale` and show `UiState` art);
- key rebinding.

## 10. Phase 2 (implemented)

Phase 2 (October 2026) is folded into the sections above:

- **Value paths** that sliders and toggles write as well as read: `ctx.store`, `save.x` and `audio.*` (§3.4).
- **Audio channels** `master`, `music`, `sfx` and `ui`, saved for the player; music keeps playing under a paused menu (§5.8, engine `API.md` §16).
- **`UiShow`** for conditional visibility (icon rows such as hearts), and **`UiSlider`** and **`UiToggle`** for settings screens without code (§2.3, §5.5–§5.7).
- **Open, close and loop animations** keyed on the canvas's timeline (§10.5, the reference), with builder presets (§6).
- **9-slice panels** with `UiSlice` (§2.3, §3.2, §5.3, §6) and **element opacity** from the object colour's alpha (§2.2, §3.2).
- **Builder and tools:** `icons`, `slider`, `toggle`, textured and 9-slice `panel`s and `animate` (§6); Shift+A › Game UI › Slider and Toggle (§7); `ui_add` kinds `icons`, `slider` and `toggle`, `ui_set` `min`, `step` and `count`, and `ui_canvas` `transition` (§8).
- **Every template on Blender screens** (§10.8).

The subsections keep the numbers that code comments cite.

### 10.1 Value paths

In §3.4: one path syntax for bindings, `UiBar`, `UiShow`, `UiSlider` and `UiToggle`, which sliders and toggles also write.

### 10.2 Audio channels

In §5.8; engine `API.md` §16 is the full audio reference.

### 10.3 `UiShow`

In §2.3, with its test state in §5.7.

### 10.4 `UiSlider` and `UiToggle`

In §2.3 (components), §5.5 (`UiEventInfo.kind` and `value`), §5.6 (input) and §5.7 (`state().ui.buttons` `kind` and `value`).

### 10.5 Open and close animations

- **Authoring:** a canvas animates with Blender keyframes on its own timeline.
  - Timeline markers name the ranges. `open` starts the opening animation, which runs to the next marker or the scene's end; `close` likewise.
  - `loop` is a range played over and over while the canvas is open, after `open`.
  - Keyframe element location, Z rotation, scale, and opacity: the object's colour alpha, `Object.color[3]`, the Viewport Display colour (§2.2). Blender's Solid view shows it with Object colour; Material Preview does not.
  - `gameui.animate` and `ui_canvas transition` write presets to start from (§6): `fade`, `pop` (scale 0.9 → 1 with a fade), `slide-up`, `slide-down`, `slide-left`, `slide-right` and `none` for `open` and `close`; `pulse`, `bob` and `blink` for `loop`. They key the canvas's top-level elements and place the markers.
- **Rest pose:** the layout the canvas has while simply open. It is the scene at the frame where `open` ends (the next marker, or the scene end). Without an `open` marker it is where `loop`, else `close`, starts (both start from the rest pose), so a scrubbed timeline never exports a mid-animation pose. Only a canvas with no markers uses the current frame. Export reads the static `transform`s and `opacity` at that frame. Keyed elements rest where their keys say: an element moved by hand without a new keyframe snaps back, so move animated elements with a keyframe, or through `ui_set` or the Layout box, which re-key them.
- **Export:** `animations` on the canvas (§3.2), present only when the canvas has markers:

  ```json
  "animations": {
    "open":  { "duration": 0.3, "fps": 30, "tracks": { "<element id>": { "x": [], "y": [], "rotation": [], "scaleX": [], "scaleY": [], "opacity": [] } } },
    "close": { },
    "loop":  { }
  }
  ```

  - Each track holds one value per frame, `duration × fps + 1` values, in the units of `transform` (local px, y down, radians clockwise), and opacity 0..1.
  - Only channels that change are written.
  - Opacity multiplies down the hierarchy.
- **Runtime:**
  - Opening a canvas plays `open` from its start, then `loop` repeatedly. Closing plays `close` and hides the canvas when it ends; a close interrupted by an open snaps to the open's start.
  - Animations run on the UI clock: real time, or game steps under a test clock, while the game is paused too (a 0.25 s close is 15 steps).
  - Input is accepted during `open` and `loop`.
  - `state().ui.open` lists a canvas from the moment it opens until its close animation ends, and `state().ui.animating` while its `open` or `close` plays (§5.7).
- **As implemented** (export):
  - **Markers:** `open`, `close` and `loop` match without case or surrounding spaces, and the first of a name counts. A range runs to the next marker of any name, or to `frame_end`; an empty range warns.
  - **Sampling:** the F-curves of each object's active action are evaluated per frame. The scene frame never changes, so unkeyed edits survive live exports.
  - **Channels:** a channel is written when it differs from the rest value anywhere in its range. Rotation is unwrapped across ±π.
  - **Not sampled (with a warning):** constraints, drivers, NLA strips, bone or vertex parents, camera animation.
  - **Live link:** keyframe edits re-export the canvases using the action. Marker and colour-alpha edits are caught by the twice-a-second poll.
- **As implemented** (engine):
  - **Closing is immediate for the game:** `isOpen()` is false, the game resumes, and the canvas takes no input. The canvas still draws until `close` ends.
  - **Backdrop:** a menu's backdrop fades with its open and close.
  - **Replacing a menu:** the new menu plays its open over the old menu's close.
  - **Unanimated closes:** restart, Stop, test reset, `ui.remove`, and a canvas never drawn hide at once.
  - **Hit tests and `state().ui.buttons`** use animated positions.
  - **Text** is rasterised at the rest scale, so a pop doesn't re-render text each frame.

### 10.6 9-slice panels

In §2.3 (`UiSlice`), §3.2 (`mesh.slice`), §5.3 (drawing), §6 (the builder's `image` and `slice`) and §7 (the Layout box's W/H). The starter ships 9-slice art in `assets/ui/`, drawn by `tools/make_ui_icons.py` (`--check` finds stale files), with these insets:

| File | Size | `slice` | What |
|---|---|---|---|
| `panel.png` | 128 × 128 | 32 | A card like the starter's menus (`#12161F` at 90 %, 2 px light border) |
| `panel-solid.png` | 128 × 128 | 44 | The starter's card, opaque (`#12161F`, 2 px light border, 40 px radius) |
| `panel-framed.png` | 128 × 128 | 32 | Opaque navy in a bevelled gold frame with corner studs; light text |
| `panel-light.png` | 128 × 128 | 32 | Opaque parchment with a brown outline; dark text (`#2B2118`) |
| `button.png` | 96 × 96 | 44 | A light button body with a dark outline; dark label text |
| `slider-track.png`, `slider-fill.png` | 64 × 32 | 14 | A slider's groove and its accent-yellow fill |
| `slider-knob.png` | 48 × 48 | none | A round handle (an image, not sliced) |

A panel smaller than its two insets shrinks the corners to fit, so a 28 px slider takes the 14 px insets whole.

### 10.7 Tools

In §6 (`gs.gameui`), §7 (Blender) and §8 (MCP).

### 10.8 Templates

- **Starter:**
  - `assets/music/theme.ogg`, a 30 s seamless loop generated by `tools/make_music.py`, plays from a `Music` object in the level: `AudioSource {volume: 0.5, loop, autoplay, spatial: false, channel: music}`, so it plays on under menus.
  - The title has Options under Play (in `TitleButtons`), and Pause has Options.
  - The `Options` screen has a Music slider (`audio.music`), a Sound slider (`audio.sfx`) and Back (`previous`, focused first).
  - Menus open with `pop` and close with `fade`. The HUD keeps the score.
- **Genre templates** (platformer, shooter, runner, driving):
  - Each builds its own `ui/ui.blend` at New Project with `tools/build_ui.py` (`build(gs, root)`, run like `tools/build_level.py`, in place of the starter's built-in UI; TEMPLATES.md): Title, Pause (with Options), Options, its end screens and a HUD fitting the genre.
  - Their systems keep the HUD's values in `ctx.store` and open canvases, using bindings, `UiShow` and `ctx.ui.text`, `show` and `bar` instead of `ctx.ui.screen` and corner `ctx.hud.set` text. `ctx.hud` stays for labels pinned to entities (`+10`, health bars over enemies).
  - **Platformer:** Title, Pause, Options, Win, GameOver and HUD. Store keys `coins`, `coinTotal`, `time`, `finished`, `newBest`, `score` and `lives`; the best time is `ctx.save` `bestTime`. Win shows the best time, or "New best time!" by `UiShow` on `newBest`.
  - **Shooter:** Title, Pause, Options, GameOver and HUD. Store keys `score`, `health`, `healthLow`, `wave` and `enemiesLeft`; the best score is `ctx.save` `bestScore`. The health bar has a red twin over it that `UiShow` shows while `healthLow` is 1, so the store drives the look without UI code.
  - **Runner:** screens Title, Pause, Options, GameOver and HUD.
    - HUD: a distance pill (`{distance:0} m`, score), a coin count, and Magnet/Shield bars, each shown only while `magnetLeft` / `shieldLeft` is above 0.
    - A `{banner}` text and a Controls hint that code shows.
    - Store keys: `distance`, `speed`, `score`, `coins`, `magnetLeft`, `shieldLeft`, `banner`; `ctx.save` `bestDistance`.
  - **Driving:** screens Title, Pause, Options, Results and HUD.
    - HUD: a start hint while `lap` is 0, a lap card (`Lap {lap}/{laps}` and the race time), the best lap, speed in km/h with an OFF ROAD flag, and a split banner.
    - Results: shows "New record!" or the saved record.
    - Store keys: `lap`, `laps`, `raceTime`, `lapTime`, `bestLap`, `speedKmh`, `offRoad`; `ctx.save` `bestRace`.
    - It no longer uses `ctx.hud`.
  - Their e2e expectations and AGENTS.md sections follow.
- **The game's name in templates:** a game saved as a template carries `ui/ui.blend` with its own name in the title. New Project renames texts marked `gs_ui_project_name`, and texts whose body is the template's `sourceName` (§11.7).

## 11. Phase 3 (implemented)

Phase 3 (October 2026) is folded into the sections above:

- **3D models in canvases:** `UiModel` on an empty whose children are the model (§2.2, §2.3), exported into `<Canvas>.ui.glb` (§3.2) and drawn lit in a view of its own, turning or playing a clip, with the level's tone mapping (§5.3). `gameui.model`, Shift+A › Game UI › 3D Model… and `ui_add` kind `model` build them (§6–§8).
- **A game-rendered preview of screens in Blender:** in a UI file, the Scene view's Game toggle in camera view shows the screen as the game draws it, with placeholders such as `{score}` (§11.2, the reference; §7).
- **Translations:** tables in `ui/locales/` keyed by the authored texts (§3.6); `ctx.ui.locale` (saved, matched to the browser), `ctx.ui.locales` and `ctx.ui.translate`; the value paths `ui.locale` and `ui.localeName`; `UiButton.action: "locale"` for a Language button without code (§2.3, §3.4, §5.5, §5.9); `ui_strings` and `ui_translate` (§6, §8).
- **Game shaders on UI meshes:** shaders made for flat UI (`ui: true`), among them the library's new `Shine` and `Gradient` (the library has 33 shaders); `panel(shader=)` and `ui_add` / `ui_set` `shader` (§3.5, §5.3, §6, §8).
- **Variable fonts:** `text.fontAxes`, the instance Blender draws, pinned in the game (§3.2, §5.3).
- **WebGPU** composites the UI in linear light like WebGL (§5.3).
- **Project names in saved templates:** `gs_ui_project_name` and `template.json` `sourceName` (§6, §11.7).
- **The starter** shows models, a shader, a Language button and a French table (§11.8).

What is still missing is in §9. The subsections keep the numbers that code comments cite.

### 11.1 3D models in canvases (`UiModel`)

In §2.2 (objects inside a model), §2.3 (`UiModel`), §3.2 (the `model` element, the canvas GLB, `files` and `materials`), §4 (export and the live link), §5.1 and §5.3 (loading and drawing), §6 (`gameui.model`), §7 (Shift+A › Game UI › 3D Model…, the Layout box, the overlay) and §8 (`ui_add` kind `model`, `ui_set`).

### 11.2 Game-rendered preview in Blender (`view.mode: "ui"`)

- **When:** in a UI file, the Game toggle of a 3D View (Scene view) shows the canvas of the active scene as the game draws it.
  - This works when the 3D View looks through the canvas camera (camera view, Numpad 0); otherwise the area shows a one-line hint.
  - Blender's overlays (selection, gizmos) stay on top, aligned with the camera frame.
- **Message:** `view.mode { mode: "ui", canvas, frame: [x, y, w, h], background? }` (live link, `to` one page).
  - `frame` is the camera frame's rectangle in the region's pixels, y down, recomputed on view changes.
  - `background` is `"level"` (the start level's last export, dimmed by the canvas backdrop as in game) or `"checker"`.
  - The engine lays the canvas out at `w × h` (its scale mode applies) and draws it at `[x, y]`, without playing: no systems, no pause, no input.
  - Open animations jump to the rest pose; loops and model spin still run.
  - Bindings with no value draw as their own text (`{score}`), so placeholders are visible.
  - Live edits (`ui.canvas`) update it as usual.
- **Leaving:** other view modes (`scene`, `game`) are unchanged; switching back clears the canvas.
- **As implemented** (add-on, `core/sceneview.py`, `editors/scene_view.py`):
  - **Turning it on** in a UI file exports no level. The canvases reach the page through the UI live link (§4): turning the Scene view on turns on the active scene's Live Link, and so does showing another scene in the window.
  - **Background:** once per page, the add-on sends `scene.load` of the start level's last export (`gamestudio.json` `startScene`, `.gamestudio/export/<name>.scene.json`) and `background: "level"`; without that file, nothing is loaded and `background` is `"checker"`. Nothing is exported in the background. After Stop the start level is loaded again, since the game may have moved to another level.
  - **The frame:** the corners of `camera.data.view_frame(scene=scene)`, placed by the camera's world matrix and projected with the region's `window_matrix @ view_matrix`, give a rectangle in region pixels from the top left. It is computed only when `region_3d.view_perspective` is `CAMERA` and the view looks through the scene's camera (an area's local camera counts only when it is that one).
  - **Message:** the add-on also sends `region: [width, height]`, the region's size, because the stream's page is often smaller than the region (at most 1280 × 720 pixels) and lags it while it resizes. It also sends `animate` (the Scene view preference). No `view.camera` is sent. Like the camera, the message is read when the region draws (`PRE_VIEW`) and by the timer, and is sent when it changed, at most 60 times a second, the latest winning. A page that reports a view other than `ui` gets it again.
  - **Hint:** out of camera view, the area shows "View through the screen's camera (Numpad 0) to see it drawn by the game" in one line at the bottom. Blender draws the view, and no frames come. Play, Stop and Off work as in the Scene view of a level (SPEC §7.3).
- **As implemented** (engine, `game.setView('ui', { canvas, frame, region?, background? })`, `src/game/ui-preview.ts`; SPEC §6.6):
  - **Placement:** the game's `<canvas>` element itself moves to the frame. Its inline style becomes `position: fixed`, with `left`/`top` at the frame mapped from `region` onto the page (`window.innerWidth`/`innerHeight`), CSS size `w × h`, and `transform: scale(page / region)`. The renderer, the canvas layout and the game's camera all see a `w × h` game. The level behind the canvas is therefore framed as a game of that size frames it, with the frame's aspect and its post effects. The page around the frame shows the page's own background. Leaving the view puts the canvas's own style back.
  - **Background:** `level` draws the loaded level from the game's camera (never the editor's), then a menu's `backdrop` over it. `checker` draws a 16 px grey checkerboard instead of the level, and so does a page with no level loaded.
  - **What is drawn:** the canvas alone, without the HUDs and menus the game had open. The game stops first, as for the Scene view. A menu is drawn over its backdrop, with its first focus shown (`focus`, else the first button), as it opens in the game. A HUD is drawn without a backdrop. Nothing opens or pauses, and no input reaches it: `ctx.ui`, `state().ui` and `isOpen()` see nothing open.
  - **Rest pose:** an `open` animation is skipped to its end each time the preview shows a canvas, including one that only arrives later. A `loop` then runs on the UI clock, and so does model spin. Text is rasterised at the rest scale.
  - **Placeholders:** a binding whose value is `undefined` or `null` renders as written, with its format: `{score:000}`. Bars, `UiShow`, sliders and toggles read values as in the game. A missing value is 0 or off, so a bound bar shows empty and a `UiShow` hides its element until the value is set.
  - **Canvas changes:** a new canvas name fetches `ui/index.json` again (`ui.refresh()`), for a screen exported since the page loaded. A canvas the page does not have yet shows once it comes, by `ui.canvas` or by that fetch.
  - **Selection:** hidden while the view is shown; it is kept for the other views.
  - **Leaving:** `play()` and `step()` leave the view as they leave the Scene view (title screens open as from a stopped game). `setView('scene' | 'game')` clears the preview. `view.mode` is ignored while a test holds the clock. An unusable `frame` or a missing `canvas` warns and changes nothing.
  - `game.view` is `'ui'`, and `game.status` reports `view: "ui"`. `game.uiPreview` holds the canvas, frame, region and background.
  - **Needs a window to check:** the drawing under Blender's overlays, the frame's alignment with Blender's camera border, and the CSS placement in Chrome are covered only by the logic tests (`test/ui-preview.test.ts`; `blender/tests/addon/test_scene_view_ui.py`).

### 11.3 Translations

In §1 and §3.6 (the tables and `gamestudio.json` `locale`), §2.3 (`UiButton.action: "locale"`), §3.1 (`sourceLocale`, `locales`), §3.4 (`ui.locale`, `ui.localeName`), §4 (export and `ui.locales`), §5.5 and §5.9 (`ctx.ui.locale`, `locales` and `translate`), §5.7 (`state().ui.locale`), §6 (`gameui.strings`, `gameui.translate`, Language buttons) and §8 (`ui_strings`, `ui_translate`).

### 11.4 Game shaders on UI materials

In §3.5 (`material.shader`), §5.3 (which shaders apply, the canvas clock, WebGPU), §6 (`panel(shader=, shader_values=)`), §7 (Material › Game) and §8 (`ui_add` / `ui_set` `shader`). The library's `Shine` (a diagonal highlight sweeping across, with `color`, `width`, `speed`, `interval` and `angle`) and `Gradient` (two colours along an `angle`) are UI- and 3D-safe; engine `API.md` §29 is their reference, and §28 says how a project shader opts in (`ui: true`).

### 11.5 Variable fonts

In §3.2 (`text.fontAxes`) and §5.3 (Text: variable fonts). Variable WOFF2 fonts other than Inter are in §9.

### 11.6 WebGPU

In §5.3 (**When** and **WebGPU**): the WebGPU path composites UI in linear light like WebGL, so translucent UI looks the same on both renderers. Game shaders on UI meshes still need WebGL (§9).

### 11.7 Project names in templates

- **Builder:** `gameui.text(..., project_name=True)` (`ui_add` text `project_name`) marks a text whose body is the game's name (`obj["gs_ui_project_name"] = True`, §6). The starter's and the genre templates' `GameTitle` are marked.
- **New Project from a template that ships `ui/ui.blend`** (user templates): marked texts get the new project's name. Save as Template records the game's name in `template.json` `sourceName`, and unmarked texts whose body is exactly that name are renamed too. The UI file is saved and exported. TEMPLATES.md ("The game's name in `ui/ui.blend`") has the details.

### 11.8 Templates

- **Starter** (`projects.build_starter_ui`):
  - `Title` has `TitleGem` above the game's name: a 3D model of the level's teal gem (the `collectible` kit's gem) in a 220 × 220 view, anchored to the top, turning at 0.6 rad/s under studio light. It pops in and fades out with the menu.
  - The focus art of the title's Play button (`PlayButtonFocus`) has a subtle `Shine`: a soft white band (alpha 0.4, width 0.25) sweeps across the yellow every few seconds, the first as the title opens. Without `Shine` in the registry the button goes without it, with a warning.
  - `Options` has a third row under the sliders, `LanguageLabel` and `LanguageButton`: a 360 × 64 pill with `action: "locale"`, no target, and the label `{ui.localeName}`, so each press switches to the next language and the label shows the current one's name ("English", "Français"). The card is 760 × 690, with the rows at y 465, 565 and 665 and Back at 785, so Up from Back reaches Language, then Sound and Music.
  - It ships `ui/locales/fr.json` ("Français"), with all 14 texts of its screens except `GameTitle`, the project's name, which differs per project and shows as it is. So the game starts in French in a French browser, and the Language button switches between English and French. New Project copies the table before the UI is built (the build checks for `ui/ui.blend`, not `ui/`), and the save exports it with the screens.
  - A game that adds or rewords texts keeps the table complete with `ui_strings {"locale": "fr"}`, then `ui_translate`; an untranslated text shows in English.
- **Genre templates** (platformer, shooter, runner, driving) leave out the starter's `ui/locales` (`template.json` `exclude`): their screens have other texts. They have one language and no Language button until a game adds a table (`ui_translate`) and a button. A template of your own built on the starter (`base`) whose texts differ should exclude `ui/locales` the same way; a template saved from a starter game carries its `ui/locales`.
- **Tests:** the templates' e2e browser runs with `--lang=en-US`, so the starter plays in English there whatever the machine's language.
