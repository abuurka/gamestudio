// @ts-check
// SPDX-License-Identifier: MIT
// Game UI tools: menus and HUDs built in ui/ui.blend, one Blender scene per screen ("canvas"), which the game
// draws in WebGL over the 3D view (docs/GAME-UI.md §8). They wrap the bridge methods of the same name
// (blender/gamestudio/bridge/gameui.py, which call gs.gameui); arguments are checked here first, so a
// mistake comes back with what each kind of element takes instead of a Python error.

import { ToolError } from '../util.js';
import { DESTRUCTIVE, READ, WRITE, object } from './common.js';

/** @typedef {import('./common.js').Context} Context */

// Enum values: the UI built-ins (docs/GAME-UI.md §2.3, runtime/packages/engine/src/schema/builtins.ts) and
// Blender's text alignment (§3.2).
export const ELEMENT_KINDS = ['text', 'image', 'panel', 'button', 'bar', 'group', 'icons', 'slider', 'toggle', 'model'];
export const CANVAS_KINDS = ['menu', 'hud'];
export const OPEN_MODES = ['manual', 'start', 'firstStart'];
export const BACK = ['none', 'close', 'resume', 'previous', 'restart'];
export const SCALE_MODES = ['fit', 'width', 'height', 'fixed'];
export const ANCHORS = ['auto', 'center', 'top-left', 'top', 'top-right', 'left', 'right', 'bottom-left', 'bottom', 'bottom-right'];
export const STRETCH = ['none', 'width', 'height', 'both', 'cover'];
export const ACTIONS = ['event', 'close', 'resume', 'open', 'previous', 'restart', 'load', 'locale', 'none'];
export const DIRECTIONS = ['right', 'left', 'up', 'down'];
export const ALIGN = ['left', 'center', 'right', 'justify', 'flush'];
export const VALIGN = ['top-baseline', 'top', 'center', 'bottom', 'bottom-baseline'];
// UiModel (docs/GAME-UI.md §11.1).
export const VIEWS = ['threeQuarter', 'front', 'side', 'top'];
export const LIGHTS = ['studio', 'soft', 'flat'];
// gs.gameui.animate (docs/GAME-UI.md §10.5, blender/gamestudio/gameui/build.py PRESETS and LOOPS).
export const TRANSITIONS = ['fade', 'pop', 'slide-up', 'slide-down', 'slide-left', 'slide-right', 'none'];
export const LOOPS = ['pulse', 'bob', 'blink', 'none'];

export const UI_FILE_NOT_OPEN_HINT = 'Call ui_open first (it opens ui/ui.blend; pass save: true to save the open file first).';
const OLD_EXTENSION_HINT = "Blender's GameStudio extension has no Game UI tools yet: update the extension and restart Blender.";

const HEX = /^#(?:[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$/;
const SOUND_FILE = /\.(?:ogg|mp3|wav)$/i; // UiButton.sound and focusSound, UiSlider and UiToggle sound (asset fields)
const IMAGE_FILE = /\.(?:png|jpe?g|webp)$/i;
/** A language code (BCP 47-ish): a language, then an optional script and region: "fr", "pt-BR", "zh-Hans", "es-419". */
export const LOCALE_CODE = /^[A-Za-z]{2,3}(?:[-_][A-Za-z]{4})?(?:[-_](?:[A-Za-z]{2}|[0-9]{3}))?$/;
const NAME = { type: 'string', minLength: 1 };
const POINT = { type: 'array', items: { type: 'number' }, minItems: 2, maxItems: 2 };
const SIZE = { type: 'array', items: { type: 'number', minimum: 0 }, minItems: 2, maxItems: 2 };
/** What shader colour fields take (game shaders' `color` fields are linear RGBA). */
export const SHADER_COLORS = 'Colour fields take "#RRGGBB" or "#RRGGBBAA" (sRGB, as a colour picker shows it; made linear like every UI colour) or a linear [r, g, b, a] list.';
const COLOR = {
  type: ['string', 'array'],
  pattern: HEX.source,
  items: { type: 'number', minimum: 0 },
  minItems: 3,
  maxItems: 4,
  description: '"#RRGGBB" or "#RRGGBBAA" (sRGB, as a colour picker shows it), or linear [r, g, b, a].',
};

// Every ui_add argument, with the kinds it goes with (all: every kind).
const ADD = {
  canvas: { ...NAME, description: 'The canvas (screen) name, e.g. "Title" or "HUD".' },
  kind: { type: 'string', enum: ELEMENT_KINDS },
  name: { ...NAME, description: 'The element (Blender object) name, unique in the UI file: buttons send it as their event name.' },
  at: { ...POINT, description: '[x, y] canvas px (origin top-left, y down): the centre of a box (panel, image, button, bar, slider, toggle, icon row); text is aligned around it.' },
  parent: { ...NAME, description: 'An element name: this one moves, shows and hides with it.' },
  anchor: { type: 'string', enum: ANCHORS, description: 'Top-level elements: the screen point it keeps its distance to on screens of other shapes. Default auto: from where it sits (thirds of the screen).' },
  stretch: { type: 'string', enum: STRETCH, description: 'Top-level elements: width / height / both follow the screen; cover fills it (backgrounds). Default none.' },
  z: { type: 'number', description: 'Draw order: higher draws on top (Blender Z). Default: above the highest element.' },
  visible: { type: 'boolean', description: 'false: the element starts hidden ("Disable in Renders"), and game code shows it with ctx.ui.show, e.g. a "New best!" text. Default true.' },
  loop: {
    type: 'string',
    enum: LOOPS,
    description:
      'This element alone plays a loop over and over while its canvas is open: pulse (scale to 104 %), bob (8 px up and down) or blink (opacity to 35 %), e.g. a Play button that pulses. Keyed on the element in Blender (children too) over the canvas\'s loop range, which is made when the canvas has none; it replaces the canvas\'s transition loop on this element. "none" removes it.',
  },
  body: { type: 'string', description: 'text: what it says. "{score}" shows ctx.store.score live, "{save.best}" a saved value, "{time:m:ss}" a formatted one, "{game.time:m:ss}" the game clock (ctx.time; no code needed), "{game.level}" the level\'s name; {{ and }} are braces.' },
  label: { type: 'string', description: 'button: its text (bindings work as in body).' },
  path: { ...NAME, description: 'image: a png, jpg or webp (project-relative or absolute; copied into assets/ui/ when outside the project).' },
  size: {
    type: ['number', 'array'],
    items: { type: 'number', minimum: 0 },
    minItems: 2,
    maxItems: 2,
    minimum: 0,
    description: 'text: the font size in px (default 48). icons: one icon\'s size in px (default 52). panel, bar (required), button (default [360, 84]), slider (default [360, 28]), toggle (default [96, 48]) and model (its view, default [320, 320]): [width, height] in px.',
  },
  text_size: { type: 'number', minimum: 0, description: "button: the label's font size in px (default 40). For a text it is the same as size." },
  font: { type: 'string', description: 'text, button: a font file (.ttf, .otf, .woff, .woff2; copied into assets/fonts/). Default: the bundled Inter.' },
  color: { ...COLOR, description: `text: its colour (default white); panel: the fill; button: the fill when not focused; bar, slider: the fill (slider default "#FFD400"). ${COLOR.description}` },
  align: { type: 'string', enum: ALIGN, description: 'text: horizontal alignment around at (or in its box). Default center.' },
  valign: { type: 'string', enum: VALIGN, description: 'text: vertical alignment around at. Default center.' },
  box: { ...SIZE, description: 'text: [width, height] of a text box in px; the text wraps at its width (height 0: as tall as needed).' },
  outline: { type: 'number', minimum: 0, description: 'text: outline width in px (default 0).' },
  outline_color: COLOR,
  shadow: { type: 'array', items: { type: 'number' }, minItems: 2, maxItems: 3, description: 'text: drop shadow [x, y] or [x, y, blur] in px (offset x right, y down; blur default 0: sharp). [0, 0]: none.' },
  width: { type: 'number', minimum: 0, description: "image: width in px (default: the image's own size; one of width and height keeps the aspect ratio)." },
  height: { type: 'number', minimum: 0, description: 'image: height in px.' },
  opacity: { type: 'number', minimum: 0, maximum: 1, description: 'image: 0..1 (default 1).' },
  radius: { type: 'number', minimum: 0, description: 'panel, button, bar, slider: corner radius in px (button default 42: round ends; slider default: half its thickness).' },
  border: { type: 'number', minimum: 0, description: 'panel: border width in px (default 0).' },
  border_color: COLOR,
  image: { type: 'string', description: 'panel: a png, jpg or webp the panel draws instead of a colour, stretched over it ("" makes it flat again); with slice it is 9-slice art. icons: one full icon (default "assets/ui/heart.png", in the starter template with star, coin, gem and key).' },
  slice: {
    type: ['number', 'array'],
    items: { type: 'number', minimum: 0 },
    minItems: 4,
    maxItems: 4,
    minimum: 0,
    description: 'panel with image: [left, right, top, bottom] image px (or one number for all four) that keep their size: 9-slice art whose corners stay sharp at any panel size (resize it with size, never by scaling).',
  },
  action: { type: 'string', enum: ACTIONS, description: 'button: what a press does (default event: only game code, ctx.ui.on). locale: switches the UI language (a Language button needs no code).' },
  target: { type: 'string', description: 'button: the canvas to open (action open), the level to load (load), the language code to switch to (locale: "fr", "pt-BR"; "" the next language), or the event name (event; default: the button name).' },
  text_color: COLOR,
  focus_color: { ...COLOR, description: `button: the fill while focused (default "#FFD400"). ${COLOR.description}` },
  focus_text_color: COLOR,
  order: { type: 'integer', minimum: 0, description: 'button, slider, toggle: keyboard and gamepad order 1, 2, 3...; 0 (default): by position.' },
  sound: { type: 'string', description: 'button: played when pressed (UiButton.sound); slider: on each change; toggle: when flipped. A .ogg, .mp3 or .wav file of the project, e.g. "assets/sfx/click.wav". "": none.' },
  focus_sound: { type: 'string', description: 'button: played when it is selected (UiButton.focusSound), a project sound file like sound. "": none.' },
  value: {
    type: 'string',
    description:
      'A value path without braces. bar: what it shows ("health", "save.best"; empty: set from code with ctx.ui.bar). slider: what it shows and sets (default "audio.music"; "audio.sfx", "save.difficulty"). toggle (required): what it flips ("save.subtitles"). icons: what they count (default "lives"). "audio.master", "audio.music", "audio.sfx", "audio.ui" are the volumes 0..1.',
  },
  max: { type: 'number', description: 'bar: the value of a full bar (default 1). slider: its highest value (default 1).' },
  min: { type: 'number', description: 'slider: its lowest value (default 0).' },
  step: { type: 'number', minimum: 0, description: 'slider: change per arrow key press, and what drags snap to (default 0.1; 0: smooth).' },
  back_color: { ...COLOR, description: `bar, slider: the track behind the fill (bar default "#00000080", slider "#FFFFFF33"). ${COLOR.description}` },
  direction: { type: 'string', enum: DIRECTIONS, description: 'bar, slider: which way the fill grows (default right; a vertical slider: up, with size [28, 360]).' },
  handle: { type: 'boolean', description: 'slider: a round handle at the value (default true).' },
  handle_color: { ...COLOR, description: `slider: the handle (default "#FFFFFF"). ${COLOR.description}` },
  on_color: { ...COLOR, description: `toggle: the track while on (default "#FFD400"). ${COLOR.description}` },
  off_color: { ...COLOR, description: `toggle: the track while off (default "#FFFFFF33"). ${COLOR.description}` },
  knob_color: { ...COLOR, description: `toggle: the knob (default "#FFFFFF"). ${COLOR.description}` },
  count: { type: 'integer', minimum: 1, maximum: 20, description: 'icons: how many (default 3): icon i shows while value is at least i.' },
  empty: { type: 'string', description: 'icons: the empty slot drawn under each icon (default "assets/ui/heart-empty.png"); "": none.' },
  spacing: { type: 'number', minimum: 0, description: 'icons: px between two icons (default 12).' },
  project_name: { type: 'boolean', description: "text: true marks the game's name (the title): a game made from a template saved from this one shows its own name there. false unmarks it." },
  shader: { type: 'string', description: 'panel: a game shader that draws it in the game, on a material of the panel\'s own, such as "Shine" (a highlight sweeping across) or "Gradient" (two colours); "" removes it. list_component_types lists the shaders and their fields.' },
  shader_values: { type: 'object', description: `panel: the shader's field values, e.g. {"width": 0.3, "speed": 1.5, "color": "#FFFFFF66"}; fields not given keep theirs. ${SHADER_COLORS}` },
  source: {
    ...NAME,
    description:
      'model: an object of the UI file (moved into the model with its children, e.g. one imported with import_asset while ui/ui.blend is open), a collection (instanced), or "<file>.blend:<Object>", an object of another .blend of the project with its children, such as the level\'s player: "scenes/main.blend:Player". That one is linked from the level (a library link, so it follows the level: its mesh, materials, rig and clips) and instanced at the model\'s origin, facing as it was made (its place and turn in the level are left out; its components are ignored); ui_list gives it as source [{"object", "linked": "scenes/main.blend:Player"}]. One of source, kit or asset.',
  },
  kit: { ...NAME, description: 'model: a GameStudio kit to build the model from (list_kits): "collectible" (a gem or coin), "character_rigged" (with clips), "crate", "tree"... One of source, kit or asset.' },
  kit_params: { type: 'object', description: 'model: the kit\'s params, as create_from_kit takes them, e.g. {"style": "gem"} for a collectible.' },
  asset: { ...NAME, description: 'model: an asset of the project\'s asset library (asset_list), placed as an instance. One of source, kit or asset.' },
  view: { type: 'string', enum: VIEWS, description: 'model: where the game\'s camera looks at it from: threeQuarter (default: 35° round and 20° up), front, side or top.' },
  zoom: { type: 'number', minimum: 0.05, description: 'model: framing; above 1 is closer (default 1).' },
  spin: { type: 'number', description: 'model: turntable speed in radians per second about its up axis (default 0.6; 0: still; negative: the other way).' },
  clip: { type: 'string', description: 'model: an animation clip of the model to loop, e.g. "Idle" or "Wave" for a character_rigged kit (ui_list lists its clips). "" (default): none.' },
  light: { type: 'string', enum: LIGHTS, description: 'model: studio (default: key, fill and rim), soft (even) or flat (an unlit look).' },
  background: { ...COLOR, description: `model: drawn behind it in its view (default transparent: the UI shows through). ${COLOR.description}` },
};

const COMMON = ['canvas', 'kind', 'name', 'at', 'parent', 'anchor', 'stretch', 'z', 'visible', 'loop'];
/** What each kind takes besides COMMON (gs.gameui keyword arguments, docs/GAME-UI.md §6). */
export const KIND_ARGS = /** @type {Record<string, string[]>} */ ({
  text: ['body', 'size', 'text_size', 'font', 'color', 'align', 'valign', 'box', 'outline', 'outline_color', 'shadow', 'project_name'],
  image: ['path', 'width', 'height', 'opacity'],
  panel: ['size', 'color', 'radius', 'border', 'border_color', 'image', 'slice', 'shader', 'shader_values'],
  button: ['label', 'size', 'action', 'target', 'font', 'text_size', 'color', 'text_color', 'focus_color', 'focus_text_color', 'radius', 'order', 'sound', 'focus_sound'],
  bar: ['size', 'value', 'max', 'color', 'back_color', 'radius', 'direction'],
  group: [],
  icons: ['count', 'value', 'image', 'empty', 'size', 'spacing'],
  slider: ['size', 'value', 'min', 'max', 'step', 'color', 'back_color', 'handle', 'handle_color', 'radius', 'direction', 'order', 'sound'],
  toggle: ['value', 'size', 'on_color', 'off_color', 'knob_color', 'order', 'sound'],
  model: ['size', 'source', 'kit', 'kit_params', 'asset', 'view', 'zoom', 'spin', 'clip', 'light', 'background'],
});
const KIND_REQUIRED = /** @type {Record<string, string[]>} */ ({ text: ['body'], image: ['path'], panel: ['size'], button: ['label'], bar: ['size'], group: [], icons: [], slider: [], toggle: ['value'], model: [] });
/** What a model shows: one of these (Blender refuses a new model without one). */
const MODEL_SOURCES = ['source', 'kit', 'asset'];
const KIND_EXAMPLES = /** @type {Record<string, string>} */ ({
  text: '{"canvas": "HUD", "kind": "text", "name": "Score", "body": "Score {score}", "at": [40, 40], "size": 44, "align": "left", "valign": "top"}',
  image: '{"canvas": "Title", "kind": "image", "name": "Logo", "path": "assets/ui/logo.png", "at": [960, 260], "width": 800}',
  panel: '{"canvas": "Pause", "kind": "panel", "name": "Card", "at": [960, 540], "size": [640, 520], "color": "#17171CE6", "radius": 24}',
  button: '{"canvas": "Title", "kind": "button", "name": "Play", "label": "Play", "at": [960, 620], "action": "close", "order": 1, "sound": "assets/sfx/click.wav"}',
  bar: '{"canvas": "HUD", "kind": "bar", "name": "Health", "at": [260, 100], "size": [400, 28], "value": "health", "max": 100}',
  group: '{"canvas": "Title", "kind": "group", "name": "Buttons", "at": [960, 700]}',
  icons: '{"canvas": "HUD", "kind": "icons", "name": "Lives", "at": [140, 60], "value": "lives", "count": 3}',
  slider: '{"canvas": "Options", "kind": "slider", "name": "MusicVolume", "at": [1100, 460], "value": "audio.music"}',
  toggle: '{"canvas": "Options", "kind": "toggle", "name": "Subtitles", "at": [1100, 580], "value": "save.subtitles"}',
  model: '{"canvas": "Title", "kind": "model", "name": "Gem", "at": [960, 420], "kit": "collectible", "kit_params": {"style": "gem"}, "size": [360, 360]}',
});
const PANEL_SLICE_EXAMPLE = '{"canvas": "Pause", "kind": "panel", "name": "Card", "at": [960, 540], "size": [640, 520], "image": "assets/ui/panel.png", "slice": [24, 24, 24, 24]}';
const LANGUAGE_EXAMPLE = '{"canvas": "Options", "kind": "button", "name": "Language", "label": "{ui.localeName}", "at": [960, 700], "action": "locale"}';

// ui_set: what it can change.
const SET = {
  canvas: ADD.canvas,
  name: { ...NAME, description: 'The element to change, as ui_list names it; a part the tools made (a button\'s PlayButtonFocus or PlayButtonLabel, listed under parts) is an element too, and changes alone.' },
  at: ADD.at,
  z: ADD.z,
  size: { ...SIZE, description: '[width, height] in px of a panel, button, bar, image or model (a text\'s font size is text_size).' },
  body: ADD.body,
  font: ADD.font,
  text_size: { type: 'number', minimum: 0, description: "The font size in px of a text, or of a button's label." },
  color: ADD.color,
  text_color: { ...COLOR, description: `button: its label's colour when not focused (the focused label keeps its own colour unless focus_text_color is given too). ${COLOR.description}` },
  focus_color: ADD.focus_color,
  focus_text_color: { ...COLOR, description: `button: its label's colour while focused. ${COLOR.description}` },
  align: ADD.align,
  valign: ADD.valign,
  shadow: ADD.shadow,
  visible: { type: 'boolean', description: 'false: the element starts hidden (game code shows it with ctx.ui.show).' },
  anchor: ADD.anchor,
  stretch: ADD.stretch,
  action: ADD.action,
  target: ADD.target,
  disabled: { type: 'boolean', description: 'button: skipped by navigation, ignores clicks.' },
  order: ADD.order,
  sound: { ...ADD.sound, description: 'An element with UiButton (a button): played when pressed; a slider: on each change; a toggle: when flipped. A .ogg, .mp3 or .wav file of the project, e.g. "assets/sfx/click.wav". "": none.' },
  focus_sound: { ...ADD.focus_sound, description: 'An element with UiButton: played when it is selected, a project sound file like sound. "": none.' },
  value: { ...ADD.value, description: 'bar, slider, toggle, icons: the value path, without braces ("health", "audio.music", "save.subtitles", "lives").' },
  min: ADD.min,
  max: ADD.max,
  step: ADD.step,
  count: ADD.count,
  parent: { type: ['string', 'null'], minLength: 1, description: 'An element name, or null to make it top-level.' },
  loop: ADD.loop,
  view: ADD.view,
  zoom: ADD.zoom,
  spin: ADD.spin,
  clip: ADD.clip,
  light: ADD.light,
  background: ADD.background,
  shader: { ...ADD.shader, description: 'panel (or another mesh): a game shader that draws it in the game, on a material of its own, such as "Shine" or "Gradient"; "" removes it.' },
  shader_values: { ...ADD.shader_values, description: `panel: its shader's field values to change, e.g. {"speed": 2, "color": "#FFFFFF66"}. ${SHADER_COLORS}` },
  project_name: ADD.project_name,
};

// ui_canvas transition: gs.gameui.animate's presets.
const TRANSITION = {
  type: ['object', 'null'],
  properties: {
    open: { type: 'string', enum: TRANSITIONS, description: 'How the canvas opens. Default pop.' },
    close: { type: 'string', enum: TRANSITIONS, description: 'How it closes. Default fade.' },
    duration: { type: 'number', minimum: 0.01, maximum: 10, description: 'Seconds of open and of close. Default 0.25.' },
    loop: { type: ['string', 'null'], enum: [...LOOPS, null], description: 'Played over and over on every top-level element while it is open, after open: pulse, bob or blink. null or "none": no loop (default). One element alone: ui_set loop.' },
  },
  additionalProperties: false,
  description:
    'Open and close animations, keyframed in Blender on the canvas\'s top-level elements with timeline markers (gs.gameui.animate): fade, pop (from 90 % with a fade), slide-up, slide-down, slide-left, slide-right (a tenth of the screen with a fade) or none. Keys not given keep the canvas\'s current ones (pop, fade, 0.25 s on a canvas without). null removes them; elements\' own loops (ui_set loop) stay.',
};

/** @param {string} message @param {string} hint */
const invalid = (message, hint) => new ToolError(message, { code: 'invalid_arguments', hint });

/** @param {string[]} names */
const list = (names) => names.map((n) => `"${n}"`).join(', ');

/** "A text", "An image", "An icon row". @param {string} kind */
const a = (kind) => {
  const noun = kind === 'icons' ? 'icon row' : kind;
  return `${/^[aeiou]/.test(noun) ? 'An' : 'A'} ${noun}`;
};

/**
 * Colour arguments must be "#RRGGBB" / "#RRGGBBAA" strings (or linear RGBA lists).
 * @param {Record<string, any>} args @param {string} example
 */
function checkColors(args, example) {
  for (const [key, value] of Object.entries(args)) {
    if (typeof value === 'string' && /color$/.test(key) && !HEX.test(value)) {
      throw invalid(`${key} "${value}" is not a colour: use "#RRGGBB" or "#RRGGBBAA" (sRGB hex), or a linear [r, g, b, a] list.`, `Example: ${example}`);
    }
  }
  for (const key of ['backdrop', 'background']) {
    if (typeof args[key] === 'string' && !HEX.test(args[key])) {
      throw invalid(`${key} "${args[key]}" is not a colour: use "#RRGGBB" or "#RRGGBBAA" (sRGB hex), or a linear [r, g, b, a] list.`, `Example: ${example}`);
    }
  }
}

/**
 * Game shader values (ui_add/ui_set shader_values, set_material_shader values): a string written as a hex colour
 * ("#" and hex digits) must be "#RRGGBB" or "#RRGGBBAA". Blender checks each against its field's type (colour
 * fields take hex or linear [r, g, b, a]).
 * @param {unknown} values @param {string} key @param {string} example
 */
export function checkShaderColors(values, key, example) {
  if (!values || typeof values !== 'object' || Array.isArray(values)) return;
  for (const [field, value] of Object.entries(values)) {
    if (typeof value === 'string' && /^#[0-9a-fA-F]*$/.test(value.trim()) && !HEX.test(value.trim())) {
      throw invalid(`${key}.${field} "${value}" is not a colour: use "#RRGGBB" or "#RRGGBBAA" (sRGB hex), or a linear [r, g, b, a] list.`, example);
    }
  }
}

/**
 * A Language button's target is a language code ("" for the next language).
 * @param {Record<string, any>} args
 */
function checkLocaleTarget(args) {
  if (args.action === 'locale' && typeof args.target === 'string' && args.target !== '' && !LOCALE_CODE.test(args.target)) {
    throw invalid(`target "${args.target}" is not a language code: a Language button (action "locale") switches to the language target names.`, `Use a code such as "fr" or "pt-BR", or "" (or no target) for the next language. Example: ${LANGUAGE_EXAMPLE}`);
  }
}

/** A language code argument of ui_strings and ui_translate. @param {unknown} locale */
function checkLocale(locale) {
  if (typeof locale === 'string' && !LOCALE_CODE.test(locale)) {
    throw invalid(`locale "${locale}" is not a language code.`, 'Use a code such as "fr", "de", "ja", "pt-BR" or "zh-Hans" (a language, then an optional script and region).');
  }
}

/**
 * A model shows one thing: source, kit or asset; kit_params go with kit. ui_set changes no model's content.
 * @param {Record<string, any>} args @param {string} example
 */
function checkModel(args, example) {
  const given = MODEL_SOURCES.filter((k) => args[k] !== undefined);
  if (given.length > 1) throw invalid(`A model shows one thing: pass one of source, kit or asset, not ${given.join(' and ')}.`, example);
  if (args.kit_params !== undefined && args.kit === undefined) throw invalid('kit_params go with kit: the kit they configure.', example);
}

/**
 * A button's sound and focus_sound are .ogg, .mp3 or .wav files ("" for none). Blender checks that the file is in
 * the project.
 * @param {Record<string, any>} args
 */
function checkSounds(args) {
  for (const key of ['sound', 'focus_sound']) {
    const value = args[key];
    if (typeof value === 'string' && value !== '' && !SOUND_FILE.test(value)) {
      throw invalid(`${key} "${value}" is not a sound file: use a .ogg, .mp3 or .wav file of the project.`, `Example: {"${key}": "assets/sfx/click.wav"} ("" removes it).`);
    }
  }
}

/** A value is a store path: "health", not "{health}". @param {unknown} value */
function checkBinding(value) {
  if (typeof value === 'string' && /[{}]/.test(value)) {
    const path = value.replace(/[{}]/g, '').trim() || 'health';
    const shows = path.startsWith('save.')
      ? `the saved value ${path.slice(5)}`
      : path.startsWith('audio.')
        ? `the ${path.slice(6)} volume`
        : `ctx.store.${path.replace(/^store\./, '')}`;
    throw invalid(`value "${value}" is a store path without braces.`, `Example: "value": "${path}" (shows ${shows}).`);
  }
}

/** A slider's range: min below max (when both are given; Blender checks against the current one). @param {Record<string, any>} args @param {string} example */
function checkRange(args, example) {
  if (typeof args.min === 'number' && typeof args.max === 'number' && !(args.min < args.max)) {
    throw invalid(`A slider's min must be below its max (got min ${args.min}, max ${args.max}).`, example);
  }
}

/**
 * Checks ui_add's arguments for their kind and returns what the bridge gets: the gs.gameui keyword
 * arguments (a text's text_size is its size; stretch rides with anchor as an [anchor, stretch] pair).
 * @param {Record<string, any>} args
 * @returns {Record<string, any>}
 */
export function addParams(args) {
  const kind = args.kind;
  const allowed = [...COMMON, ...KIND_ARGS[kind]];
  const example = `Example: ${KIND_EXAMPLES[kind]}`;
  const stray = Object.keys(args).filter((k) => args[k] !== undefined && !allowed.includes(k));
  if (stray.length) {
    const owners = stray.map((k) => {
      const kinds = ELEMENT_KINDS.filter((x) => KIND_ARGS[x].includes(k));
      return `${k} (${kinds.join(', ')})`;
    });
    throw invalid(
      `${list(stray)} ${stray.length === 1 ? 'does' : 'do'} not go with kind "${kind}": ${owners.join('; ')}.`,
      `${a(kind)} takes ${KIND_ARGS[kind].length ? list(KIND_ARGS[kind]) : 'nothing more'} besides canvas, name, at, parent, anchor, stretch, z, visible and loop. ${example}`,
    );
  }
  const missing = KIND_REQUIRED[kind].filter((k) => args[k] === undefined);
  if (missing.length) throw invalid(`${a(kind)} needs ${list(missing)}.`, example);
  /** @type {Record<string, any>} */
  const params = { ...args };
  if (kind === 'text') {
    if (Array.isArray(args.size)) throw invalid('A text\'s size is its font size in px (a number), e.g. 48.', `For a wrapping text box, pass box: [width, height]. ${example}`);
    if (args.text_size !== undefined) {
      if (args.size !== undefined) throw invalid('Pass size or text_size, not both: for a text both are its font size.', example);
      params.size = args.text_size;
      delete params.text_size;
    }
  } else if (kind === 'icons') {
    if (Array.isArray(args.size)) throw invalid("An icon row's size is one icon's size in px (a number), e.g. 52.", `The row is count icons wide, spacing px apart. ${example}`);
    if (args.size !== undefined && args.size <= 0) throw invalid('size needs to be above 0.', example);
  } else if (args.size !== undefined && !Array.isArray(args.size)) {
    throw invalid(`${a(kind)}'s size is [width, height] in px.`, kind === 'button' ? `A button's label size is text_size. ${example}` : example);
  }
  if (Array.isArray(args.size) && args.size.some((/** @type {number} */ v) => v <= 0)) throw invalid('size needs a width and a height above 0.', example);
  if (kind === 'button' && (args.action === 'open' || args.action === 'load') && !args.target) {
    throw invalid(`action "${args.action}" needs target: the ${args.action === 'open' ? 'canvas to open' : 'level to load'}.`, `Example: {"action": "open", "target": "Options"}`);
  }
  checkLocaleTarget(args);
  if (kind === 'model') checkModel(args, example);
  if (kind === 'panel' && args.shader === '' && args.shader_values !== undefined) {
    throw invalid('shader_values need a shader: shader "" removes it.', 'Example: {"shader": "Shine", "shader_values": {"width": 0.3}}');
  }
  checkShaderColors(args.shader_values, 'shader_values', 'Example: {"shader": "Shine", "shader_values": {"color": "#FFFFFF66"}}');
  if (args.parent !== undefined && (args.anchor !== undefined || args.stretch !== undefined)) {
    throw invalid('anchor and stretch go with top-level elements; this one has a parent (it follows the parent).', 'Leave anchor and stretch out, or the parent.');
  }
  if (kind === 'toggle' && args.value === '') throw invalid('A toggle needs value: the path it flips.', example);
  if (kind === 'panel') {
    if (args.slice !== undefined && !args.image) throw invalid('slice goes with image: the panel art whose corners keep their size.', `Example: ${PANEL_SLICE_EXAMPLE}`);
    if (args.image && args.color !== undefined) throw invalid('A panel with an image draws the image; color does not apply to it.', `Leave color out (image: "" makes it a flat panel again). Example: ${PANEL_SLICE_EXAMPLE}`);
    if (args.slice !== undefined && (args.radius || args.border)) throw invalid('A 9-slice panel takes its corners and border from its image; radius and border do not apply to it.', `Example: ${PANEL_SLICE_EXAMPLE}`);
  }
  for (const key of ['image', 'empty']) {
    const value = args[key];
    if (typeof value === 'string' && value !== '' && !IMAGE_FILE.test(value)) {
      throw invalid(`${key} "${value}" is not an image file: use a .png, .jpg or .webp of the project.`, kind === 'panel' ? `Example: ${PANEL_SLICE_EXAMPLE}` : example);
    }
  }
  if (kind === 'icons' && args.image === '') throw invalid('An icon row needs image: one full icon.', example);
  checkColors(args, KIND_EXAMPLES[kind]);
  checkBinding(args.value);
  checkRange(args, example);
  checkSounds(args);
  if (args.stretch !== undefined) {
    params.anchor = [args.anchor ?? 'auto', args.stretch];
    delete params.stretch;
  }
  return params;
}

/**
 * Is this the bridge saying no UI file is open (`ui_file_not_open`)?
 * @param {ToolError} error
 */
function uiFileNotOpen(error) {
  const data = /** @type {Record<string, unknown> | undefined} */ (error.data);
  return error.code === 'ui_file_not_open' || data?.code === 'ui_file_not_open' || data?.reason === 'ui_file_not_open' || /\bui_file_not_open\b/.test(error.message);
}

/**
 * Calls the bridge method `method`; a refusal for want of an open UI file gets the code ui_file_not_open and
 * the ui_open hint.
 * @param {Context} ctx @param {string} method @param {Record<string, unknown>} params @param {AbortSignal} signal
 */
async function callUi(ctx, method, params, signal) {
  try {
    return await ctx.blender.call(method, params, { signal });
  } catch (error) {
    if (error instanceof ToolError) {
      if (uiFileNotOpen(error)) {
        error.code = 'ui_file_not_open';
        error.hint = UI_FILE_NOT_OPEN_HINT;
        if (error.data) {
          const { code: _code, reason: _reason, ...rest } = error.data;
          error.data = Object.keys(rest).length ? rest : undefined;
        }
      } else if (error.code === 'unknown_method') {
        error.hint = OLD_EXTENSION_HINT;
      }
    }
    throw error;
  }
}

/** @param {Context} ctx @returns {import('./common.js').ToolDefinition[]} */
export function uiTools(ctx) {
  return [
    {
      name: 'ui_list',
      title: 'List game UI',
      description:
        'The game\'s menus and HUDs as built in the open UI file (ui/ui.blend; ui_open opens it): every canvas (a ' +
        'screen, one Blender scene each) with its settings (kind, open, pause, pause_menu, back, scale_mode, ' +
        'backdrop, layer, focus, size) and its elements: name, type, at and size in canvas px (origin top-left, x ' +
        'right, y down), rotation (degrees clockwise, only when rotated), parent, anchor, visible, text, colour; a ' +
        'text also gives rect [x, y, w, h], the box its glyphs fill in canvas px, measured from Blender\'s text ' +
        'layout (approximate: the game lays text out with the same font and rules, within a few px; a binding ' +
        'measures as written, "{score}"), so you can place things next to a title without a screenshot; the shape of panels, ' +
        'buttons and bars (radius, border, border_color; left out when 0), an image panel\'s image and slice, and UI ' +
        'components (UiButton action and target, UiBar value): match an existing card or pill from it. Sliders ' +
        '(kind "slider") give value, min, max and step, toggles value, icon rows (kind "icons") count, value, ' +
        'image, empty, icon_size and spacing. A canvas with open/close animations gives animations {open, close, ' +
        'loop} in seconds and the transition presets that made them, as they still are: a preset whose keys or ' +
        'marker were changed by hand is left out and named in transition.edited. An element with a loop of its ' +
        'own (ui_set loop) gives loop. 3D models (kind "model") give view, zoom, spin, clip, light, background, ' +
        'source (what they show: a kit, an asset, objects, or "linked": "scenes/main.blend:Player" for a level\'s ' +
        'object) and clips (the animation clips they have); panels ' +
        'with a game shader give shader and shader_values; the game\'s title text gives project_name. canvas: only ' +
        'that one. Read it before changing a canvas and to check what you built; game_screenshot {"ui": "<Canvas>"} ' +
        'shows how the game draws it.',
      inputSchema: object({ canvas: { ...NAME, description: 'One canvas by name. Default: all.' } }),
      annotations: READ,
      handler: (args, call) => callUi(ctx, 'ui_list', args, call.signal),
    },
    {
      name: 'ui_open',
      title: 'Open UI file',
      description:
        'Open the project\'s UI file, ui/ui.blend, in Blender: game menus and HUDs are built there, one Blender scene ' +
        'per screen ("canvas"), and the game draws them in WebGL over the 3D view. create (default true) makes the ' +
        'file when it is missing, with one empty menu canvas "Menu". Refuses when the open file has unsaved changes ' +
        'unless save: true (saves it first). Returns {file, canvases}. The other ui_ tools work on this file; while ' +
        'it is open, a running game gets every change live, and save exports its canvases for the game. Blender then ' +
        'shows the UI file instead of the level: open_scene scenes/<level>.blend goes back (save first).',
      inputSchema: object({
        create: { type: 'boolean', description: 'Create ui/ui.blend when it is missing. Default true.' },
        save: { type: 'boolean', description: 'Save the open file first when it has unsaved changes. Default false.' },
      }),
      annotations: WRITE,
      handler: (args, call) => callUi(ctx, 'ui_open', args, call.signal),
    },
    {
      name: 'ui_canvas',
      title: 'Create or set a UI canvas',
      description:
        'Create a canvas (a screen: a menu or a HUD) in the open UI file (ui_open), or change one (one undo step); ' +
        'only the given settings change. It is a Blender scene whose orthographic camera frames the design size ' +
        '(size, default [1920, 1080]); every ui_add position is in its pixels, origin top-left, y down. kind: "menu" ' +
        '(default; one open at a time, takes keyboard and gamepad focus, pauses the game unless pause: false) or ' +
        '"hud" (any number open, drawn under menus, mouse only). open: "manual" (default; a button or ctx.ui.open ' +
        'opens it), "start" (on every start and restart: HUDs) or "firstStart" (once per page load: title screens). ' +
        'pause_menu: true makes it the pause menu (Escape, P and gamepad Start open and close it). back: what Escape ' +
        'and gamepad B do while it is open (none, close, resume, previous, restart). scale_mode: fit (default: the ' +
        'whole design stays visible), width, height or fixed. backdrop: a colour drawn over the game behind a menu ' +
        '(new menus: "#00000099"). layer: order among open canvases of the same kind. focus: the button selected ' +
        'first when the menu opens (a button of this canvas; null or "" clears it: then the first in navigation ' +
        'order); set it after adding the buttons: Play on a title screen, a safe button such as Back on a settings ' +
        'screen, so Enter never changes a setting by accident. These settings live on the UiCanvas component of ' +
        'the canvas camera, the object "<Canvas> Camera". transition: open and close animations, keyframed in ' +
        'Blender on its top-level elements: {open, close} each fade, pop, slide-up, slide-down, slide-left, ' +
        'slide-right or none, duration (seconds, default 0.25), loop (pulse, bob or blink on every top-level ' +
        'element, played while open; to loop one element, such as a pulsing Play button, use ui_set loop instead); ' +
        'keys not given are kept, null removes them (elements\' own loops stay); elements added or moved later are ' +
        'keyed too. Returns the canvas as ' +
        'ui_list describes it, with animations {open, close, loop} in seconds. Examples: ' +
        '{"name": "Title", "open": "firstStart", "focus": "PlayButton"}, {"name": "HUD", "kind": "hud", "open": ' +
        '"start"}, {"name": "Pause", "pause_menu": true, "back": "resume", "transition": {"open": "pop", "close": ' +
        '"fade"}}, {"name": "Options", "back": "previous", "focus": "BackButton", "transition": {"open": ' +
        '"slide-up"}}, {"name": "GameOver", "back": "restart"}.',
      inputSchema: object(
        {
          name: { ...NAME, description: 'The canvas (scene) name: Title, HUD, Pause, GameOver...' },
          kind: { type: 'string', enum: CANVAS_KINDS },
          open: { type: 'string', enum: OPEN_MODES },
          pause: { type: 'boolean', description: 'menu: pause the game while open. Default true.' },
          pause_menu: { type: 'boolean', description: 'menu: this is the pause menu (Escape, P, gamepad Start). Default false.' },
          back: { type: 'string', enum: BACK, description: 'menu: what Escape and gamepad B do. Default none.' },
          scale_mode: { type: 'string', enum: SCALE_MODES, description: 'How the design size scales to the real screen. Default fit.' },
          backdrop: { ...COLOR, description: `menu: drawn over the game behind it. ${COLOR.description}` },
          layer: { type: 'integer', description: 'Order among open canvases of the same kind; higher draws on top. Default 0.' },
          size: { type: 'array', items: { type: 'integer', minimum: 16, maximum: 8192 }, minItems: 2, maxItems: 2, description: 'The design size [width, height] in px. Default [1920, 1080].' },
          focus: { type: ['string', 'null'], description: 'menu: the name of the button selected first (UiCanvas.focus), a button, slider or toggle of this canvas: Play on a title, a safe one such as Back on settings screens. null or "": the first in navigation order.' },
          transition: TRANSITION,
        },
        ['name'],
      ),
      annotations: WRITE,
      handler: (args, call) => {
        const menuOnly = ['pause', 'pause_menu', 'back', 'backdrop', 'focus'].filter((k) => args[k] !== undefined);
        if (args.kind === 'hud' && menuOnly.length) {
          throw invalid(`${list(menuOnly)} ${menuOnly.length === 1 ? 'is a menu setting' : 'are menu settings'}; a HUD has none.`, 'Example: {"name": "HUD", "kind": "hud", "open": "start"}');
        }
        checkColors(args, '{"name": "Pause", "backdrop": "#00000099"}');
        return callUi(ctx, 'ui_canvas', args, call.signal);
      },
    },
    {
      name: 'ui_add',
      title: 'Add a UI element',
      description:
        'Add an element to a canvas of the open UI file (ui_open, ui_canvas), one undo step. An element of that name ' +
        'is updated instead (it must be of the same kind). Positions are canvas px of the design size (1920x1080 unless ui_canvas set ' +
        'another): origin top-left, x right, y down. at is the centre of a box; text is aligned around it. Kinds and ' +
        'what each takes: text {body, size (font px, default 48), font, color, align, valign, box, outline, ' +
        'outline_color, shadow ([x, y] or [x, y, blur] px)}; image {path, width, height, opacity}; panel {size [w, h], ' +
        'color, radius, border, border_color, image, slice}: a flat rectangle, or with image a picture, and with ' +
        'slice [left, right, top, bottom] 9-slice art whose corners keep their size at any size; button {label, ' +
        'size (default [360, 84]), action, ' +
        'target, font, text_size, color, text_color, focus_color, focus_text_color, radius, order, sound and ' +
        'focus_sound (.ogg/.mp3/.wav files of the project, played when pressed / selected)}: a panel with a label ' +
        'and a focused look; ' +
        'bar {size, value, max, color, back_color, radius, direction}: value is a store path such as "health"; group ' +
        '{}: moves, shows and hides its children together; icons {count, value, image, empty, size (one icon, px), ' +
        'spacing}: a row of icons that counts a value, icon i showing while value >= i: hearts are {"kind": "icons", ' +
        '"value": "lives"} (assets/ui/heart.png and heart-empty.png by default); slider {size, value, min, max, ' +
        'step, color, back_color, handle, handle_color, radius, direction, order, sound}: shows and sets a value ' +
        'path as it is dragged or moved with the arrow keys, no code needed: a volume slider is {"kind": "slider", ' +
        '"value": "audio.music"} ("audio.sfx" for sounds; volumes are saved); toggle {value (required), size, ' +
        'on_color, off_color, knob_color, order, sound}: flips a value path, e.g. "save.subtitles"; model {size ' +
        '(its view, default [320, 320]), one of kit (+ kit_params), asset or source, view, zoom, spin, clip, ' +
        'light, background}: a 3D model the game draws lit and turning in the UI, such as a gem on a title screen ' +
        'or a character playing clip "Idle" on a select screen (Blender shows it from above; game_screenshot ' +
        'shows the real view); source "scenes/main.blend:Player" shows the level\'s own player, linked so it ' +
        'follows the level. Sliders and ' +
        'toggles are focusable like buttons and fire ctx.ui.on(<their name>) with info.value on each change. A ' +
        'panel takes shader (a game shader such as "Shine", a highlight sweeping across, or "Gradient") and ' +
        'shader_values (colour fields as "#RRGGBBAA" too); a text project_name: true for the game\'s title. A Language button needs no code: action ' +
        '"locale" switches to the language target names ("fr"), or with no target to the next one, and its label ' +
        '"{ui.localeName}" shows the current language (translations: ui_translate). All ' +
        'take parent, anchor, stretch, z, visible (false: ' +
        'starts hidden until code calls ctx.ui.show, e.g. a "New best!" text) and loop (pulse, bob or blink: this ' +
        'element alone loops while the canvas is open). Text bodies and ' +
        'labels bind live values: "Score {score}" shows ctx.store.score, "{save.best}" a saved value, "{time:m:ss}" ' +
        'a formatted one. A button\'s action: close or resume (close this menu), open (the canvas named target), ' +
        'previous, restart, load (the level target) or event (default: only game code). Every press also calls the ' +
        'handler of ctx.ui.on(<target, else the button name>, (ctx, info) => ...), registered in a system\'s start(). ' +
        'anchor keeps a top-level element at its screen edge or corner on other screen shapes (default: from where ' +
        'it sits, by thirds). A good menu: a title text in the top third, then a column of buttons under one group ' +
        '(their parent) in the middle, about 110 px apart with order 1, 2, 3, the first one Play with action close ' +
        '(ui_canvas focus selects it first). Then look: game_screenshot {"ui": "<Canvas>"}, and a playtest ' +
        'pressing {"key": "Enter"} or clicking a button at its ui.buttons x/y, from t=0 on (scaled when the ' +
        'playtest size differs from ui.size; final.ui says what is open). Colours: "#RRGGBB" or ' +
        '"#RRGGBBAA". Other component fields (UiText fit, UiButton focusScale) take set_component. Returns ' +
        '{canvas, created, element, parts?, removed?}: parts are the pieces the builder made for this element ' +
        '(a button\'s Bg, Focus and Label; not the elements inside it), removed names the parts the change ' +
        'deleted (an updated panel given 9-slice art loses its <name>Border ring; fewer icons lose their slots). ' +
        `Example: ${KIND_EXAMPLES.button}.`,
      inputSchema: object(ADD, ['canvas', 'kind', 'name', 'at']),
      annotations: WRITE,
      handler: (args, call) => callUi(ctx, 'ui_add', addParams(args), call.signal),
    },
    {
      name: 'ui_set',
      title: 'Change a UI element',
      description:
        'Change an element of a canvas in the open UI file (one undo step); only the given properties change. at ' +
        '[x, y] canvas px (origin top-left, y down), z, size [w, h] (panel, button, bar, image, slider, toggle; a ' +
        '9-slice panel is rebuilt with its corners kept), body (text; bindings ' +
        'such as "Score {score}" work), font, text_size (a text, or a button\'s label), color (a text, a panel, a ' +
        'button\'s fill), text_color, focus_color and focus_text_color (buttons: their label, focused fill and ' +
        'focused label, recoloured part by part as ui_add does; a new text_color alone keeps the focused label\'s ' +
        'colour), align, valign, ' +
        'shadow (text: [x, y] or [x, y, blur] px), visible (false: starts hidden; code shows it with ctx.ui.show), ' +
        'anchor and stretch (top-level elements), action, target, disabled and order (buttons; disabled, order ' +
        'and sound also sliders and toggles), sound and ' +
        'focus_sound (elements with UiButton: .ogg/.mp3/.wav files of the project, "" removes), value (bars, ' +
        'sliders, toggles, icon rows), min, max and step (sliders; max also bars), count (icon rows), parent (an ' +
        'element name, or null for top-level), loop (any element: pulse, bob or blink, played over and over while ' +
        'the canvas is open, keyed on this element only; "none" removes it), view, zoom, spin, clip, light and ' +
        'background (3D models; to change what a model shows, ui_add it again with kit, asset or source), shader ' +
        'and shader_values (panels; shader "" removes it; colour fields take "#RRGGBB"/"#RRGGBBAA" or linear ' +
        'lists), project_name (texts). name can be a part the tools made (ui_list parts: PlayButtonFocus, ' +
        'PlayButtonLabel): that part alone changes, as the panel or text it is, e.g. a shader on a button\'s focus ' +
        'art; recolour a whole button through the button. Other fields of the UI components take ' +
        'set_component. Returns {canvas, element, parts?, removed?}: the element\'s own parts, and removed: the ' +
        'parts the change deleted (e.g. a smaller count drops icons and slots). Examples: {"canvas": "HUD", "name": ' +
        '"Score", "body": "Score {score:000}", "anchor": "top-left"}, {"canvas": "Title", "name": "Gem", "spin": ' +
        '1.2, "view": "front"}, {"canvas": "Title", "name": "PlayButton", ' +
        '"loop": "pulse"}, {"canvas": "Title", "name": "PlayButton", "sound": "assets/sfx/click.wav"}, {"canvas": ' +
        '"HUD", "name": "Lives", "count": 5}, {"canvas": "Title", "name": "PlayButton", "text_color": "#17171C", ' +
        '"focus_color": "#FFFFFF"}, {"canvas": "Title", "name": "PlayButtonFocus", "shader_values": {"color": ' +
        '"#FFFFFF80"}}.',
      inputSchema: object(SET, ['canvas', 'name']),
      annotations: WRITE,
      handler: (args, call) => {
        const changes = Object.keys(args).filter((k) => k !== 'canvas' && k !== 'name' && args[k] !== undefined);
        if (!changes.length) {
          throw invalid('Nothing to change: pass at least one property.', `It takes ${list(Object.keys(SET).slice(2))}. Example: {"canvas": "HUD", "name": "Score", "at": [40, 40]}`);
        }
        if (Array.isArray(args.size) && args.size.some((/** @type {number} */ v) => v <= 0)) throw invalid('size needs a width and a height above 0.', 'Example: {"size": [360, 84]}');
        checkColors(args, '{"canvas": "HUD", "name": "Score", "color": "#FFD400"}');
        checkBinding(args.value);
        checkRange(args, 'Example: {"canvas": "Options", "name": "Difficulty", "min": 1, "max": 3, "step": 1}');
        checkSounds(args);
        checkLocaleTarget(args);
        if (args.shader === '' && args.shader_values !== undefined) throw invalid('shader_values need a shader: shader "" removes it.', 'Example: {"canvas": "Title", "name": "Card", "shader_values": {"speed": 2}}');
        checkShaderColors(args.shader_values, 'shader_values', 'Example: {"canvas": "Title", "name": "PlayButtonFocus", "shader_values": {"color": "#FFFFFF66"}}');
        return callUi(ctx, 'ui_set', args, call.signal);
      },
    },
    {
      name: 'ui_remove',
      title: 'Remove UI',
      description:
        'Remove an element from a canvas of the open UI file, or, with canvas only, the whole canvas (its Blender ' +
        'scene) (one undo step; undo brings it back). The game drops it live.',
      inputSchema: object({ canvas: ADD.canvas, name: { ...NAME, description: 'The element to remove. Leave it out to remove the whole canvas.' } }, ['canvas']),
      annotations: DESTRUCTIVE,
      handler: (args, call) => callUi(ctx, 'ui_remove', args, call.signal),
    },
    {
      name: 'ui_strings',
      title: 'List UI texts to translate',
      description:
        'Every translatable text of the game UI, each once: {text, elements: ["Canvas/Name", ...]} (a button\'s ' +
        'label and its focused twin show the same text; texts that are only bindings, such as "{score}", are left ' +
        'out; the game\'s title has project_name). The texts are the open UI file\'s, or with a level open the ' +
        'exported ones (source says which). With locale ("fr", "pt-BR"): each text\'s translation from ' +
        'ui/locales/<locale>.json, or null when it has none, the counts translated and missing (missing leaves ' +
        'out the game\'s title, project_name: true, which is listed but usually stays as it is; translate it ' +
        'all the same if the name changes in that language), and unused: the ' +
        'table\'s strings no element shows (strings for ctx.ui.translate in code, or texts that changed). Also ' +
        'source_locale (gamestudio.json locale, default "en": the language the UI is written in) and locales, the ' +
        'tables there are. Read it, then translate the missing texts with ui_translate.',
      inputSchema: object({ locale: { ...NAME, description: 'A language code such as "fr", "de", "ja" or "pt-BR": the texts come with its translations.' } }),
      annotations: READ,
      handler: (args, call) => {
        checkLocale(args.locale);
        return callUi(ctx, 'ui_strings', args, call.signal);
      },
    },
    {
      name: 'ui_translate',
      title: 'Translate the UI',
      description:
        'Create or update the translation table of a language, ui/locales/<locale>.json: strings maps each ' +
        'authored text exactly as ui_strings lists it to its translation, and the translation keeps the text\'s ' +
        'bindings ("Score {score}" -> "{score} points"; formats such as {time:m:ss} too). Texts already in the ' +
        'table and not given are kept; remove drops texts. name is the language\'s own name, as a Language ' +
        'button shows it ("Français", "Deutsch"), needed when the table is new. The game picks the player\'s ' +
        'language (navigator.languages, else the source language) and saves their choice; a Language button is ' +
        'ui_add {"kind": "button", "action": "locale", "label": "{ui.localeName}"}; code strings use ' +
        'ctx.ui.translate("Crashed!") and are translated here too. Works from any file of the project (no undo ' +
        'step: it writes the JSON file) and sends the table to the game. Returns {locale, name, file, created, ' +
        'added, changed, removed, strings, missing (UI texts still untranslated, the game\'s title left out), ' +
        'unused}. Example: {"locale": ' +
        '"fr", "name": "Français", "strings": {"Play": "Jouer", "Score {score}": "Score : {score}"}}.',
      inputSchema: object(
        {
          locale: { ...NAME, description: 'The language code: "fr", "de", "es", "ja", "pt-BR", "zh-Hans"... Not the source language (gamestudio.json locale, default "en").' },
          name: { type: 'string', minLength: 1, maxLength: 60, description: 'The language\'s own name: "Français", "Deutsch", "Português (Brasil)". Needed when the table is new.' },
          strings: { type: 'object', description: 'Authored text -> translation, e.g. {"Play": "Jouer", "Best {save.best}": "Record {save.best}"}.' },
          remove: { type: 'array', items: { type: 'string' }, description: 'Authored texts to drop from the table.' },
        },
        ['locale', 'strings'],
      ),
      annotations: WRITE,
      handler: (args, call) => {
        checkLocale(args.locale);
        const bad = Object.entries(args.strings).filter(([text, translation]) => !text || typeof translation !== 'string' || !translation.trim());
        if (bad.length) {
          throw invalid(`strings ${list(bad.map(([text]) => text))}: each translation is a non-empty string (remove drops a text).`, 'Example: {"locale": "fr", "strings": {"Play": "Jouer"}}');
        }
        return callUi(ctx, 'ui_translate', args, call.signal);
      },
    },
  ];
}
