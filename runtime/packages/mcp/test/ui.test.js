// Game UI (docs/GAME-UI.md §8): the ui_* tools (bridge methods of the same name, blender/gamestudio/bridge/gameui.py),
// their checks before Blender is called, and game_screenshot's ui argument and result.
import { existsSync, mkdirSync, readFileSync, readdirSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { UI_TIMEOUTS } from '../src/browser.js';
import { Context } from '../src/context.js';
import { createGameStudioServer, createTools } from '../src/index.js';
import { exportedOpenSeconds } from '../src/tools/game.js';
import { ACTIONS, ANCHORS, BACK, CANVAS_KINDS, DIRECTIONS, ELEMENT_KINDS, KIND_ARGS, LIGHTS, LOCALE_CODE, LOOPS, OPEN_MODES, SCALE_MODES, STRETCH, TRANSITIONS, UI_FILE_NOT_OPEN_HINT, VIEWS, addParams } from '../src/tools/ui.js';
import { callTool, makeProject, startFakeBridge } from './helpers.js';

const REPO = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../../..');
const UI_TOOLS = ['ui_list', 'ui_open', 'ui_canvas', 'ui_add', 'ui_set', 'ui_remove', 'ui_strings', 'ui_translate'];

/** The values of `field: field.enum([...])` in a component of the engine's built-ins. */
function builtinEnum(component, field) {
  const source = readFileSync(path.join(REPO, 'runtime/packages/engine/src/schema/builtins.ts'), 'utf8');
  const start = source.indexOf(`export const ${component} = defineComponent(`);
  expect(start, component).toBeGreaterThanOrEqual(0);
  const body = source.slice(start, source.indexOf('\n});', start));
  const match = new RegExp(`\\b${field}: field\\.enum\\(\\s*\\[([^\\]]*)\\]`).exec(body);
  expect(match, `${component}.${field}`).not.toBeNull();
  return [...match[1].matchAll(/'([^']*)'/g)].map((m) => m[1]);
}

let project;
let bridge;
let server;
let ctx;

beforeEach(async () => {
  project = makeProject();
  bridge = await startFakeBridge({
    ui_list: (p) => ({ canvases: [{ name: p.canvas ?? 'Title', settings: { kind: 'menu' }, elements: [] }] }),
    ui_open: () => ({ file: 'ui/ui.blend', canvases: ['Menu'] }),
    ui_canvas: (p) => ({ name: p.name, settings: { kind: p.kind ?? 'menu' } }),
    ui_add: (p) => ({ name: p.name, type: p.kind }),
    ui_set: (p) => ({ name: p.name }),
    ui_remove: (p) => ({ removed: p.name ?? p.canvas }),
    ui_strings: (p) => ({ source: 'ui/ui.blend', source_locale: 'en', locale: p.locale ?? null, texts: [] }),
    ui_translate: (p) => ({ locale: p.locale, created: true, added: Object.keys(p.strings).length }),
  });
  bridge.writeDiscovery(project.root);
  ctx = new Context({ root: project.root, env: { HOME: project.root, APPDATA: path.join(project.root, 'none'), BLENDER_USER_RESOURCES: path.join(project.root, 'none') } });
  ({ server } = createGameStudioServer({ root: project.root, context: ctx }));
});
afterEach(async () => {
  await ctx.shutdown();
  await bridge.close();
  project.cleanup();
});

describe('ui tool definitions', () => {
  it('has the eight tools: ui_list and ui_strings read, ui_remove destroys, the rest write', () => {
    const tools = Object.fromEntries(createTools(ctx).filter((t) => t.name.startsWith('ui_')).map((t) => [t.name, t]));
    expect(Object.keys(tools).sort()).toEqual([...UI_TOOLS].sort());
    for (const name of ['ui_list', 'ui_strings']) expect(tools[name].annotations, name).toMatchObject({ readOnlyHint: true, destructiveHint: false });
    expect(tools.ui_remove.annotations).toMatchObject({ readOnlyHint: false, destructiveHint: true });
    for (const name of ['ui_open', 'ui_canvas', 'ui_add', 'ui_set', 'ui_translate']) expect(tools[name].annotations, name).toMatchObject({ readOnlyHint: false, destructiveHint: false });
    expect(Object.keys(tools.ui_list.inputSchema.properties)).toEqual(['canvas']);
    expect(Object.keys(tools.ui_strings.inputSchema.properties)).toEqual(['locale']);
    expect(tools.ui_strings.inputSchema.required).toEqual([]);
    expect(Object.keys(tools.ui_translate.inputSchema.properties)).toEqual(['locale', 'name', 'strings', 'remove']);
    expect(tools.ui_translate.inputSchema.required).toEqual(['locale', 'strings']);
    expect(Object.keys(tools.ui_open.inputSchema.properties)).toEqual(['create', 'save']);
    expect(Object.keys(tools.ui_canvas.inputSchema.properties)).toEqual(['name', 'kind', 'open', 'pause', 'pause_menu', 'back', 'scale_mode', 'backdrop', 'layer', 'size', 'focus', 'transition']);
    expect(tools.ui_canvas.inputSchema.properties.transition).toMatchObject({ type: ['object', 'null'], additionalProperties: false });
    expect(Object.keys(tools.ui_canvas.inputSchema.properties.transition.properties)).toEqual(['open', 'close', 'duration', 'loop']);
    expect(tools.ui_canvas.inputSchema.required).toEqual(['name']);
    expect(tools.ui_add.inputSchema.required).toEqual(['canvas', 'kind', 'name', 'at']);
    expect(tools.ui_set.inputSchema.required).toEqual(['canvas', 'name']);
    expect(Object.keys(tools.ui_set.inputSchema.properties)).toEqual([
      'canvas', 'name', 'at', 'z', 'size', 'body', 'font', 'text_size', 'color', 'text_color', 'focus_color', 'focus_text_color', 'align', 'valign', 'shadow', 'visible',
      'anchor', 'stretch', 'action', 'target', 'disabled', 'order', 'sound', 'focus_sound', 'value', 'min', 'max', 'step',
      'count', 'parent', 'loop', 'view', 'zoom', 'spin', 'clip', 'light', 'background', 'shader', 'shader_values', 'project_name',
    ]);
    // A text's shadow is [x, y] or [x, y, blur], in ui_add and ui_set alike.
    for (const shadow of [tools.ui_add.inputSchema.properties.shadow, tools.ui_set.inputSchema.properties.shadow]) {
      expect(shadow).toMatchObject({ type: 'array', minItems: 2, maxItems: 3 });
      expect(shadow.description).toContain('[x, y] or [x, y, blur]');
    }
    expect(tools.ui_remove.inputSchema.required).toEqual(['canvas']);
    // Every per-kind argument of ui_add is in its schema, and the schema has nothing no kind takes.
    const perKind = new Set(Object.values(KIND_ARGS).flat());
    const common = ['canvas', 'kind', 'name', 'at', 'parent', 'anchor', 'stretch', 'z', 'visible', 'loop'];
    expect(new Set(Object.keys(tools.ui_add.inputSchema.properties))).toEqual(new Set([...common, ...perKind]));
    // Every kind can start hidden, in ui_add as in ui_set.
    expect(tools.ui_add.inputSchema.properties.visible).toMatchObject({ type: 'boolean' });
    expect(tools.ui_add.inputSchema.properties.visible.description).toContain('ctx.ui.show');
  });

  it('takes the keys the Blender bridge takes (blender/gamestudio/bridge/gameui.py, gameui/build.py)', () => {
    const tools = Object.fromEntries(createTools(ctx).map((t) => [t.name, t]));
    const bridge = readFileSync(path.join(REPO, 'blender/gamestudio/bridge/gameui.py'), 'utf8');
    const build = readFileSync(path.join(REPO, 'blender/gamestudio/gameui/build.py'), 'utf8');
    const strings = (text) => [...text.matchAll(/"([a-z_]+)"/g)].map((m) => m[1]);
    /** The strings of the Python tuple that `start` (ending in its opening parenthesis) begins. */
    const tuple = (source, start) => {
      const from = source.indexOf(start);
      expect(from, start).toBeGreaterThanOrEqual(0);
      return strings(source.slice(from + start.length, source.indexOf(')', from)));
    };
    for (const kind of ELEMENT_KINDS) {
      // Node turns a text's text_size into size before calling Blender.
      expect(tuple(bridge, `"${kind}": (`), kind).toEqual(KIND_ARGS[kind].filter((k) => !(kind === 'text' && k === 'text_size')));
    }
    expect(tuple(bridge, 'CANVAS_KEYS = (')).toEqual(Object.keys(tools.ui_canvas.inputSchema.properties));
    // What every kind takes: Node folds stretch into anchor; canvas, kind, name and at are positional in Python.
    expect(tuple(bridge, 'COMMON = (')).toEqual(['parent', 'anchor', 'z', 'visible', 'loop']);
    expect(tuple(build, 'SET_KEYS = (')).toEqual(Object.keys(tools.ui_set.inputSchema.properties).slice(2));
    // ui_canvas transition: the keys and presets of gs.gameui.animate.
    expect(tuple(bridge, 'TRANSITION_KEYS = (')).toEqual(Object.keys(tools.ui_canvas.inputSchema.properties.transition.properties));
    // Phase 3: ui_translate and ui_strings take the same keys in Node and Python; model() takes what ui_add's model does.
    expect(tuple(bridge, 'TRANSLATE_KEYS = (')).toEqual(Object.keys(tools.ui_translate.inputSchema.properties));
    expect(bridge).toContain('check_keys(params, ("locale",), "ui_strings")');
    expect(tuple(build, 'MODEL_FIELDS = (')).toEqual(KIND_ARGS.model.filter((k) => ['view', 'zoom', 'spin', 'clip', 'light', 'background'].includes(k)));
    expect(tuple(build, 'MODEL_SOURCES = (')).toEqual(['source', 'kit', 'asset']);
    const modelSignature = /def model\(canvas, name, at, ([^)]*)\)/.exec(build);
    expect(modelSignature).not.toBeNull();
    const keywords = [...modelSignature[1].matchAll(/([a-z_]+)=None/g)].map((m) => m[1]);
    expect(keywords.filter((k) => !['parent', 'anchor', 'z', 'visible', 'loop'].includes(k))).toEqual(KIND_ARGS.model);
    const presets = /PRESETS = \(([^)]*)\)/.exec(build);
    expect(presets).not.toBeNull();
    expect([...presets[1].matchAll(/"([a-z-]+)"/g)].map((m) => m[1])).toEqual(TRANSITIONS);
    const loops = /LOOPS = \{([^}]*)\}/.exec(build);
    expect(loops).not.toBeNull();
    expect([...[...loops[1].matchAll(/"([a-z]+)":/g)].map((m) => m[1]), 'none']).toEqual(LOOPS);
  });

  it('uses the enum values of the engine built-ins (docs/GAME-UI.md §2.3)', () => {
    const tools = Object.fromEntries(createTools(ctx).map((t) => [t.name, t]));
    const canvas = tools.ui_canvas.inputSchema.properties;
    const add = tools.ui_add.inputSchema.properties;
    const set = tools.ui_set.inputSchema.properties;
    expect(CANVAS_KINDS).toEqual(builtinEnum('UiCanvas', 'kind'));
    expect(OPEN_MODES).toEqual(builtinEnum('UiCanvas', 'open'));
    expect(BACK).toEqual(builtinEnum('UiCanvas', 'back'));
    expect(SCALE_MODES).toEqual(builtinEnum('UiCanvas', 'scaleMode'));
    expect(ANCHORS).toEqual(builtinEnum('UiAnchor', 'anchor'));
    expect(STRETCH).toEqual(builtinEnum('UiAnchor', 'stretch'));
    expect(ACTIONS).toEqual(builtinEnum('UiButton', 'action'));
    expect(ACTIONS).toContain('locale');
    expect(VIEWS).toEqual(builtinEnum('UiModel', 'view'));
    expect(LIGHTS).toEqual(builtinEnum('UiModel', 'light'));
    expect(DIRECTIONS).toEqual(builtinEnum('UiBar', 'direction'));
    expect(DIRECTIONS).toEqual(builtinEnum('UiSlider', 'direction'));
    expect([canvas.kind.enum, canvas.open.enum, canvas.back.enum, canvas.scale_mode.enum]).toEqual([CANVAS_KINDS, OPEN_MODES, BACK, SCALE_MODES]);
    expect(add.kind.enum).toEqual(ELEMENT_KINDS);
    expect(ELEMENT_KINDS).toEqual(['text', 'image', 'panel', 'button', 'bar', 'group', 'icons', 'slider', 'toggle', 'model']);
    for (const props of [add, set]) expect([props.view.enum, props.light.enum], 'model').toEqual([VIEWS, LIGHTS]);
    expect([canvas.transition.properties.open.enum, canvas.transition.properties.close.enum]).toEqual([TRANSITIONS, TRANSITIONS]);
    expect(canvas.transition.properties.loop.enum).toEqual([...LOOPS, null]);
    for (const props of [add, set]) {
      expect([props.anchor.enum, props.stretch.enum, props.action.enum]).toEqual([ANCHORS, STRETCH, ACTIONS]);
      expect(props.align.enum).toEqual(['left', 'center', 'right', 'justify', 'flush']);
      expect(props.valign.enum).toEqual(['top-baseline', 'top', 'center', 'bottom', 'bottom-baseline']);
    }
    expect(add.direction.enum).toEqual(DIRECTIONS);
    // An element's own loop: the presets of a canvas loop, in ui_add (every kind) and ui_set alike.
    expect(add.loop).toMatchObject({ type: 'string', enum: LOOPS });
    expect(set.loop).toEqual(add.loop);
    expect(add.loop.description).toMatch(/pulse.*bob.*blink/);
  });

  it('teaches canvas pixels, actions, events, bindings and how to look at the result', () => {
    const tools = Object.fromEntries(createTools(ctx).map((t) => [t.name, t]));
    for (const words of ['origin top-left, x right, y down', 'ctx.ui.on(', '"Score {score}"', 'game_screenshot {"ui": "<Canvas>"}', '{"key": "Enter"}', '1920x1080']) {
      expect(tools.ui_add.description).toContain(words);
    }
    expect(tools.ui_canvas.description).toContain('"firstStart"');
    // focus: Play on a title, a safe button on settings screens; the settings are the canvas camera's UiCanvas.
    for (const words of ['"focus": "PlayButton"', '"focus": "BackButton"', 'UiCanvas component of the canvas camera, the object "<Canvas> Camera"']) {
      expect(tools.ui_canvas.description).toContain(words);
    }
    // Button sounds are ui_add / ui_set arguments now, not a set_component call; a column of buttons has a group.
    expect(tools.ui_add.description).not.toMatch(/sound\) take set_component/);
    expect(tools.ui_add.description).toContain('column of buttons under one group');
    expect(tools.ui_set.description).toContain('"sound": "assets/sfx/click.wav"');
    expect(tools.ui_open.description).toContain('save: true');
    expect(tools.ui_add.description).toContain('All take parent, anchor, stretch, z, visible');
    // Phase 2: a volume slider and hearts take no code; transitions are a ui_canvas setting.
    for (const words of ['{"kind": "slider", "value": "audio.music"}', '{"kind": "icons", "value": "lives"}', '9-slice', 'no code needed']) {
      expect(tools.ui_add.description).toContain(words);
    }
    for (const words of ['transition', 'slide-up', '"transition": {"open": "pop", "close": "fade"}']) expect(tools.ui_canvas.description).toContain(words);
    for (const words of ['animations {open, close, loop}', 'kind "slider"', 'kind "icons"']) expect(tools.ui_list.description).toContain(words);
    expect(tools.ui_set.description).toContain('"count": 5');
    // One element loops with ui_set loop; results name the element's own parts and the parts a change removed.
    expect(tools.ui_set.description).toContain('"loop": "pulse"');
    expect(tools.ui_canvas.description).toContain('use ui_set loop instead');
    for (const tool of ['ui_add', 'ui_set']) expect(tools[tool].description, tool).toMatch(/parts\?, removed\?/);
    expect(tools.ui_add.description).toContain('<name>Border');
    expect(tools.ui_list.description).toContain('transition.edited');
    // ui_list says it shows the shape of panels and buttons, to match an existing card or pill.
    for (const words of ['radius, border, border_color', 'rotation (degrees clockwise']) {
      expect(tools.ui_list.description).toContain(words);
    }
    expect(tools.game_screenshot.inputSchema.properties.ui).toMatchObject({ type: 'string', minLength: 1 });
    expect(tools.game_screenshot.description).toContain('{"ui": "Title"');
    // The UI state names the hidden elements of open canvases, in game_screenshot's ui and playtest's final.ui.
    expect(tools.game_screenshot.description).toContain('["HUD/Heart3"]');
    expect(tools.playtest.description).toContain('final.ui.hidden is ["HUD/Heart3"]');
    // Phase 3: 3D models, game shaders on panels, the game's title, a no-code Language button and translations.
    for (const words of ['model {size', 'one of kit (+ kit_params), asset or source', 'shader (a game shader such as "Shine"', 'project_name: true', 'action "locale"', '"{ui.localeName}"', 'ui_translate']) {
      expect(tools.ui_add.description).toContain(words);
    }
    for (const words of ['kind "model"', 'source', 'clips', 'shader_values', 'project_name']) expect(tools.ui_list.description).toContain(words);
    for (const words of ['view, zoom, spin, clip, light', 'ui_add it again with kit, asset or source', 'shader "" removes it']) expect(tools.ui_set.description).toContain(words);
    expect(tools.ui_add.inputSchema.properties.target.description).toContain('locale: "fr", "pt-BR"; "" the next language');
    for (const words of ['ui/locales/<locale>.json', 'keeps the text\'s bindings', '"action": "locale", "label": "{ui.localeName}"', 'ctx.ui.translate', 'missing']) {
      expect(tools.ui_translate.description).toContain(words);
    }
    for (const words of ['translation', 'unused', 'source_locale', 'exported ones', 'ui_translate']) expect(tools.ui_strings.description).toContain(words);
    // Tool fixes: hex shader colours, a button's colours and parts in ui_set, a level's object as a model, the
    // title out of missing, and texts' measured rect.
    for (const tool of ['ui_add', 'ui_set']) expect(tools[tool].inputSchema.properties.shader_values.description, tool).toContain('"#RRGGBB" or "#RRGGBBAA" (sRGB');
    for (const key of ['text_color', 'focus_color', 'focus_text_color']) expect(tools.ui_set.inputSchema.properties[key], key).toMatchObject({ type: ['string', 'array'] });
    for (const words of ['text_color, focus_color and focus_text_color', 'PlayButtonFocus', 'that part alone changes']) expect(tools.ui_set.description).toContain(words);
    expect(tools.ui_set.inputSchema.properties.name.description).toContain('PlayButtonFocus');
    expect(tools.ui_add.inputSchema.properties.source.description).toContain('"scenes/main.blend:Player"');
    expect(tools.ui_add.inputSchema.properties.source.description).toContain('linked from the level');
    expect(tools.ui_add.description).toContain('source "scenes/main.blend:Player"');
    expect(tools.ui_list.description).toContain('"linked": "scenes/main.blend:Player"');
    expect(tools.ui_list.description).toContain('rect [x, y, w, h]');
    expect(tools.ui_list.description).toContain('approximate');
    expect(tools.ui_strings.description).toContain('missing leaves out the game\'s title, project_name: true');
    expect(tools.ui_translate.description).toContain('the game\'s title left out');
    expect(tools.set_material_shader.inputSchema.properties.values.description).toContain('"#RRGGBB" or "#RRGGBBAA"');
    expect(tools.save.description).toContain('ui_export');
    expect(tools.game_screenshot.description).toContain('pixels of the returned image');
    expect(tools.playtest.description).toContain('gs.locale');
  });
});

describe('ui tools call the bridge', () => {
  it('forwards each tool to the bridge method of the same name', async () => {
    const cases = [
      ['ui_list', {}],
      ['ui_list', { canvas: 'HUD' }],
      ['ui_open', { create: true, save: false }],
      ['ui_canvas', { name: 'Title', kind: 'menu', open: 'firstStart', pause: true, back: 'none', scale_mode: 'fit', backdrop: '#00000099', layer: 1, size: [1280, 720] }],
      ['ui_canvas', { name: 'Fog', backdrop: [0, 0, 0, 0.5] }],
      ['ui_add', { canvas: 'Title', kind: 'button', name: 'Play', label: 'Play', at: [960, 620], action: 'close', order: 1, color: '#FFFFFF33', focus_color: '#FFD400' }],
      ['ui_add', { canvas: 'HUD', kind: 'bar', name: 'Health', at: [260, 100], size: [400, 28], value: 'health', max: 100, direction: 'right' }],
      ['ui_add', { canvas: 'Title', kind: 'group', name: 'Buttons', at: [960, 700], anchor: 'bottom' }],
      ['ui_add', { canvas: 'Title', kind: 'image', name: 'Logo', path: 'assets/ui/logo.png', at: [960, 260], width: 800, opacity: 0.9 }],
      ['ui_add', { canvas: 'Title', kind: 'panel', name: 'Card', at: [960, 540], size: [640, 520], radius: 24, border: 2, border_color: '#FFFFFF', parent: 'Buttons' }],
      ['ui_add', { canvas: 'Options', kind: 'button', name: 'BackButton', label: 'Back', at: [960, 900], action: 'previous', sound: 'assets/sfx/click.wav', focus_sound: 'assets/sfx/tick.ogg' }],
      ['ui_add', { canvas: 'Title', kind: 'text', name: 'TitleText', body: 'Gem Island', at: [960, 300], shadow: [0, 6, 18] }],
      ['ui_add', { canvas: 'GameOver', kind: 'text', name: 'NewBest', body: 'New best!', at: [960, 420], visible: false }],
      ['ui_add', { canvas: 'HUD', kind: 'group', name: 'Lives', at: [80, 60], visible: true }],
      ['ui_add', { canvas: 'GameOver', kind: 'button', name: 'Again', label: 'Again', at: [960, 700], action: 'restart', visible: false }],
      ['ui_canvas', { name: 'Options', back: 'previous', focus: 'BackButton' }],
      ['ui_canvas', { name: 'Options', focus: null }],
      ['ui_canvas', { name: 'Options', focus: '' }],
      ['ui_set', { canvas: 'HUD', name: 'Score', body: 'Score {score:000}', anchor: 'top-left', visible: false }],
      ['ui_set', { canvas: 'Title', name: 'TitleText', shadow: [2, 4] }],
      ['ui_set', { canvas: 'Title', name: 'PlayButton', sound: 'assets/sfx/click.mp3', focus_sound: '' }],
      ['ui_set', { canvas: 'HUD', name: 'Score', parent: null }],
      ['ui_remove', { canvas: 'HUD', name: 'Score' }],
      ['ui_remove', { canvas: 'Old' }],
      // Phase 2: icons, sliders, toggles, 9-slice panels, transitions and the new ui_set keys.
      ['ui_add', { canvas: 'HUD', kind: 'icons', name: 'Lives', at: [140, 60], value: 'lives', count: 5, image: 'assets/ui/star.png', empty: '', size: 48, spacing: 8 }],
      ['ui_add', { canvas: 'Options', kind: 'slider', name: 'Music', at: [1100, 460], value: 'audio.music', min: 0, max: 1, step: 0.05, handle: false, direction: 'right', order: 1, sound: 'assets/sfx/tick.wav' }],
      ['ui_add', { canvas: 'Options', kind: 'toggle', name: 'Subtitles', at: [1100, 580], value: 'save.subtitles', size: [96, 48], on_color: '#4CAF50', knob_color: '#FFFFFF', order: 2 }],
      ['ui_add', { canvas: 'Pause', kind: 'panel', name: 'Card', at: [960, 540], size: [640, 520], image: 'assets/ui/panel.png', slice: [24, 24, 24, 24] }],
      ['ui_add', { canvas: 'Pause', kind: 'panel', name: 'Card', at: [960, 540], size: [640, 520], image: '', color: '#17171CE6' }],
      ['ui_canvas', { name: 'Pause', transition: { open: 'pop', close: 'fade', duration: 0.3 } }],
      ['ui_canvas', { name: 'Title', transition: { loop: 'pulse' } }],
      ['ui_canvas', { name: 'Title', transition: { loop: null } }],
      ['ui_canvas', { name: 'HUD', kind: 'hud', transition: { open: 'slide-down', close: 'none' } }],
      ['ui_canvas', { name: 'Title', transition: null }],
      ['ui_set', { canvas: 'Options', name: 'Music', value: 'audio.sfx', min: 0, max: 2, step: 0.25 }],
      ['ui_set', { canvas: 'HUD', name: 'Lives', count: 4 }],
      // An element's own loop.
      ['ui_add', { canvas: 'Title', kind: 'button', name: 'Play', label: 'Play', at: [960, 620], loop: 'pulse' }],
      ['ui_add', { canvas: 'HUD', kind: 'text', name: 'Hint', body: 'Press Enter', at: [960, 980], loop: 'blink' }],
      ['ui_set', { canvas: 'Title', name: 'Play', loop: 'bob' }],
      ['ui_set', { canvas: 'Title', name: 'Play', loop: 'none' }],
      // Phase 3: 3D models, panel shaders, the game's title, Language buttons and translations.
      ['ui_add', { canvas: 'Title', kind: 'model', name: 'Gem', at: [960, 420], kit: 'collectible', kit_params: { style: 'gem' }, size: [360, 360], spin: 1.2, view: 'front', light: 'soft', background: '#00000000' }],
      ['ui_add', { canvas: 'Select', kind: 'model', name: 'Hero', at: [700, 540], asset: 'Knight', clip: 'Idle', zoom: 1.3 }],
      ['ui_add', { canvas: 'Select', kind: 'model', name: 'Statue', at: [1200, 540], source: 'Statue' }],
      ['ui_add', { canvas: 'Select', kind: 'model', name: 'Statue', at: [1200, 540], view: 'side' }],
      ['ui_add', { canvas: 'Title', kind: 'panel', name: 'Card', at: [960, 540], size: [640, 520], color: '#17171CE6', shader: 'Shine', shader_values: { width: 0.3 } }],
      ['ui_add', { canvas: 'Title', kind: 'text', name: 'GameTitle', body: 'Gem Island', at: [960, 300], project_name: true }],
      ['ui_add', { canvas: 'Options', kind: 'button', name: 'Language', label: '{ui.localeName}', at: [960, 700], action: 'locale' }],
      ['ui_add', { canvas: 'Options', kind: 'button', name: 'French', label: 'Français', at: [960, 800], action: 'locale', target: 'fr' }],
      ['ui_set', { canvas: 'Title', name: 'Gem', spin: 0, zoom: 2, clip: '', light: 'flat', background: [0, 0, 0, 0.5], size: [400, 300] }],
      ['ui_set', { canvas: 'Title', name: 'Card', shader: 'Gradient', shader_values: { angle: 45 } }],
      ['ui_set', { canvas: 'Title', name: 'Card', shader: '' }],
      ['ui_set', { canvas: 'Title', name: 'GameTitle', project_name: false }],
      ['ui_set', { canvas: 'Options', name: 'French', target: 'pt-BR' }],
      // Tool fixes: shader colours as hex, a button's colours through ui_set, a part by its name, a level's player.
      ['ui_add', { canvas: 'Title', kind: 'panel', name: 'Card', at: [960, 540], size: [640, 520], shader: 'Shine', shader_values: { color: '#FFFFFF66', width: 0.3 } }],
      ['ui_set', { canvas: 'Title', name: 'PlayButtonFocus', shader_values: { color: '#FFD40080' } }],
      ['ui_set', { canvas: 'Title', name: 'Card', shader_values: { color: [1, 1, 1, 0.4] } }],
      ['ui_set', { canvas: 'Title', name: 'PlayButton', color: '#FFFFFF', text_color: '#17171C', focus_color: '#FFD400', focus_text_color: [0, 0, 0, 1] }],
      ['ui_add', { canvas: 'Title', kind: 'model', name: 'Hero', at: [1400, 540], source: 'scenes/main.blend:Player', view: 'front', spin: 0 }],
      ['ui_strings', {}],
      ['ui_strings', { locale: 'fr' }],
      ['ui_translate', { locale: 'fr', name: 'Français', strings: { Play: 'Jouer', 'Score {score}': 'Score : {score}' } }],
      ['ui_translate', { locale: 'pt-BR', strings: {}, remove: ['Old'] }],
    ];
    for (const [tool, args] of cases) {
      const r = await callTool(server, tool, args);
      expect(r.isError, `${tool} ${JSON.stringify(r.data)}`).toBe(false);
      expect(bridge.calls.at(-1), tool).toEqual({ method: tool, params: args });
    }
    expect((await callTool(server, 'ui_open')).data).toEqual({ file: 'ui/ui.blend', canvases: ['Menu'] });
  });

  it("gives a text's text_size to gs.gameui as size, and stretch as an [anchor, stretch] pair", async () => {
    await callTool(server, 'ui_add', { canvas: 'HUD', kind: 'text', name: 'Score', body: 'Score {score}', at: [40, 40], text_size: 44, align: 'left', valign: 'top' });
    expect(bridge.calls.at(-1).params).toEqual({ canvas: 'HUD', kind: 'text', name: 'Score', body: 'Score {score}', at: [40, 40], size: 44, align: 'left', valign: 'top' });
    await callTool(server, 'ui_add', { canvas: 'HUD', kind: 'panel', name: 'TopBar', at: [960, 40], size: [1920, 80], anchor: 'top', stretch: 'width' });
    expect(bridge.calls.at(-1).params).toMatchObject({ anchor: ['top', 'width'] });
    expect(bridge.calls.at(-1).params.stretch).toBeUndefined();
    await callTool(server, 'ui_add', { canvas: 'Title', kind: 'image', name: 'Bg', path: 'assets/ui/bg.png', at: [960, 540], stretch: 'cover' });
    expect(bridge.calls.at(-1).params).toMatchObject({ anchor: ['auto', 'cover'] });
    // ui_set passes anchor and stretch as they are.
    await callTool(server, 'ui_set', { canvas: 'HUD', name: 'TopBar', anchor: 'top', stretch: 'width' });
    expect(bridge.calls.at(-1).params).toEqual({ canvas: 'HUD', name: 'TopBar', anchor: 'top', stretch: 'width' });
  });

  it('checks every kind of element before calling Blender', async () => {
    const add = (extra) => ({ canvas: 'Title', name: 'X', at: [960, 540], ...extra });
    const cases = [
      // arguments of another kind
      [add({ kind: 'text', body: 'Hi', radius: 8 }), /^"radius" does not go with kind "text": radius \(panel, button, bar, slider\)/],
      [add({ kind: 'group', label: 'Play', path: 'a.png' }), /^"label", "path" do not go with kind "group"/],
      [add({ kind: 'image', path: 'a.png', color: '#FFFFFF' }), /"color" does not go with kind "image"/],
      [add({ kind: 'panel', size: [10, 10], body: 'x' }), /"body" does not go with kind "panel"/],
      [add({ kind: 'bar', size: [10, 10], label: 'x' }), /"label" does not go with kind "bar"/],
      [add({ kind: 'button', label: 'Go', value: 'hp' }), /"value" does not go with kind "button"/],
      // what a kind needs
      [add({ kind: 'text' }), /^A text needs "body"/],
      [add({ kind: 'image' }), /^An image needs "path"/],
      [add({ kind: 'panel' }), /^A panel needs "size"/],
      [add({ kind: 'bar' }), /^A bar needs "size"/],
      [add({ kind: 'button' }), /^A button needs "label"/],
      // size, colours, bindings, actions, anchors
      [add({ kind: 'text', body: 'Hi', size: [300, 80] }), /size is its font size/],
      [add({ kind: 'text', body: 'Hi', size: 40, text_size: 40 }), /size or text_size, not both/],
      [add({ kind: 'panel', size: 40 }), /panel's size is \[width, height\]/],
      [add({ kind: 'button', label: 'Go', size: 40 }), /button's size is \[width, height\]/],
      [add({ kind: 'panel', size: [0, 40] }), /width and a height above 0/],
      [add({ kind: 'panel', size: [10, 10], color: 'red' }), /color "red" is not a colour/],
      [add({ kind: 'button', label: 'Go', focus_text_color: '#FFF' }), /focus_text_color "#FFF" is not a colour/],
      [add({ kind: 'bar', size: [10, 10], value: '{health}' }), /value "\{health\}" is a store path without braces/],
      [add({ kind: 'button', label: 'Options', action: 'open' }), /action "open" needs target: the canvas to open/],
      [add({ kind: 'button', label: 'Next', action: 'load' }), /action "load" needs target: the level to load/],
      [add({ kind: 'group', parent: 'Card', anchor: 'top' }), /anchor and stretch go with top-level elements/],
      [{ canvas: 'HUD', name: 'X' }, /Nothing to change/, 'ui_set'],
      [{ canvas: 'HUD', name: 'X', color: '#12345' }, /color "#12345" is not a colour/, 'ui_set'],
      [{ canvas: 'HUD', name: 'X', value: 'save.{best}' }, /store path without braces/, 'ui_set'],
      [{ canvas: 'HUD', name: 'X', size: [0, 10] }, /width and a height above 0/, 'ui_set'],
      [add({ kind: 'button', label: 'Go', sound: 'assets/sfx/click' }), /^sound "assets\/sfx\/click" is not a sound file: use a \.ogg, \.mp3 or \.wav file/],
      [add({ kind: 'text', body: 'Hi', sound: 'assets/sfx/click.wav' }), /"sound" does not go with kind "text": sound \(button, slider, toggle\)/],
      [{ canvas: 'Title', name: 'PlayButton', focus_sound: 'assets/ui/tick.png' }, /^focus_sound "assets\/ui\/tick\.png" is not a sound file/, 'ui_set'],
      [{ name: 'HUD', kind: 'hud', pause_menu: true, back: 'close' }, /^"pause_menu", "back" are menu settings; a HUD has none/, 'ui_canvas'],
      [{ name: 'HUD', kind: 'hud', focus: 'Pause' }, /^"focus" is a menu setting; a HUD has none/, 'ui_canvas'],
      [{ name: 'Pause', backdrop: 'black' }, /backdrop "black" is not a colour/, 'ui_canvas'],
      // Phase 2 kinds
      [add({ kind: 'icons', size: [52, 52] }), /^An icon row's size is one icon's size in px \(a number\)/],
      [add({ kind: 'icons', label: 'x' }), /^"label" does not go with kind "icons"/],
      [add({ kind: 'icons', image: 'assets/ui/heart.svg' }), /^image "assets\/ui\/heart\.svg" is not an image file/],
      [add({ kind: 'icons', image: '' }), /^An icon row needs image/],
      [add({ kind: 'icons', value: '{lives}' }), /value "\{lives\}" is a store path without braces/],
      [add({ kind: 'slider', size: 40 }), /^A slider's size is \[width, height\] in px/],
      [add({ kind: 'slider', min: 1, max: 1 }), /^A slider's min must be below its max/],
      [add({ kind: 'slider', value: '{audio.music}' }), /store path without braces/],
      [add({ kind: 'slider', focus_sound: 'assets/sfx/tick.wav' }), /^"focus_sound" does not go with kind "slider": focus_sound \(button\)/],
      [add({ kind: 'toggle' }), /^A toggle needs "value"/],
      [add({ kind: 'toggle', value: '' }), /^A toggle needs value/],
      [add({ kind: 'toggle', value: 'save.x', color: '#FFFFFF' }), /^"color" does not go with kind "toggle"/],
      [add({ kind: 'toggle', value: 'save.x', on_color: 'green' }), /^on_color "green" is not a colour/],
      [add({ kind: 'panel', size: [10, 10], slice: 8 }), /^slice goes with image/],
      [add({ kind: 'panel', size: [10, 10], image: 'assets/ui/panel.png', color: '#000000' }), /^A panel with an image draws the image; color does not apply/],
      [add({ kind: 'panel', size: [10, 10], image: 'assets/ui/panel.png', slice: 8, radius: 4 }), /^A 9-slice panel takes its corners and border from its image/],
      [add({ kind: 'panel', size: [10, 10], image: 'panel' }), /^image "panel" is not an image file/],
      [{ canvas: 'Options', name: 'Music', min: 2, max: 1 }, /^A slider's min must be below its max/, 'ui_set'],
      // Phase 3
      [add({ kind: 'model', kit: 'crate', asset: 'Tree' }), /^A model shows one thing: pass one of source, kit or asset, not kit and asset/],
      [add({ kind: 'model', kit_params: { style: 'gem' } }), /^kit_params go with kit/],
      [add({ kind: 'model', kit: 'crate', size: 320 }), /^A model's size is \[width, height\] in px/],
      [add({ kind: 'model', kit: 'crate', background: 'clear' }), /^background "clear" is not a colour/],
      [add({ kind: 'model', kit: 'crate', color: '#FFFFFF' }), /^"color" does not go with kind "model"/],
      [add({ kind: 'text', body: 'Hi', shader: 'Shine' }), /^"shader" does not go with kind "text": shader \(panel\)/],
      [add({ kind: 'panel', size: [10, 10], project_name: true }), /^"project_name" does not go with kind "panel": project_name \(text\)/],
      [add({ kind: 'panel', size: [10, 10], shader: '', shader_values: { width: 1 } }), /^shader_values need a shader/],
      [add({ kind: 'button', label: 'Lang', action: 'locale', target: 'French' }), /^target "French" is not a language code/],
      [{ canvas: 'Options', name: 'Lang', action: 'locale', target: 'english' }, /^target "english" is not a language code/, 'ui_set'],
      [{ canvas: 'Title', name: 'Gem', background: 'red' }, /^background "red" is not a colour/, 'ui_set'],
      [{ canvas: 'Title', name: 'Card', shader: '', shader_values: { width: 1 } }, /^shader_values need a shader/, 'ui_set'],
      [add({ kind: 'panel', size: [10, 10], shader: 'Shine', shader_values: { color: '#FFF' } }), /^shader_values\.color "#FFF" is not a colour: use "#RRGGBB" or "#RRGGBBAA"/],
      [{ canvas: 'Title', name: 'Card', shader_values: { endColor: '#12345' } }, /^shader_values\.endColor "#12345" is not a colour/, 'ui_set'],
      [{ canvas: 'Title', name: 'PlayButton', text_color: 'black' }, /^text_color "black" is not a colour/, 'ui_set'],
      [{ canvas: 'Title', name: 'PlayButton', focus_text_color: '#0000' }, /^focus_text_color "#0000" is not a colour/, 'ui_set'],
      [{ locale: 'french' }, /^locale "french" is not a language code/, 'ui_strings'],
      [{ locale: 'fr_FR_x', strings: {} }, /^locale "fr_FR_x" is not a language code/, 'ui_translate'],
      [{ locale: 'fr', strings: { Play: '' } }, /^strings "Play": each translation is a non-empty string/, 'ui_translate'],
      [{ locale: 'fr', strings: { Play: 3 } }, /^strings "Play": each translation is a non-empty string/, 'ui_translate'],
    ];
    for (const [args, error, tool = 'ui_add'] of cases) {
      const r = await callTool(server, tool, args);
      expect(r.isError, JSON.stringify(args)).toBe(true);
      expect(r.data.code, JSON.stringify(args)).toBe('invalid_arguments');
      expect(r.data.error, JSON.stringify(args)).toMatch(error);
      expect(r.data.hint, JSON.stringify(args)).toBeTruthy();
    }
    // A stray argument's hint says what the kind does take, with an example.
    const stray = await callTool(server, 'ui_add', add({ kind: 'text', body: 'Hi', radius: 8 }));
    expect(stray.data.hint).toMatch(/^A text takes "body", "size", "text_size", "font", "color".*Example: \{"canvas": "HUD", "kind": "text"/);
    expect(bridge.calls).toHaveLength(0);
  });

  it('checks types and enums from the schema before calling Blender', async () => {
    const cases = [
      ['ui_add', { canvas: 'Title', kind: 'label', name: 'X', at: [0, 0] }, /arguments\.kind: must be one of "text", "image", "panel", "button", "bar", "group", "icons", "slider", "toggle"/],
      ['ui_add', { canvas: 'HUD', kind: 'icons', name: 'X', at: [0, 0], count: 0 }, /arguments\.count: must be >= 1/],
      ['ui_add', { canvas: 'HUD', kind: 'icons', name: 'X', at: [0, 0], count: 2.5 }, /arguments\.count: expected integer/],
      ['ui_add', { canvas: 'Pause', kind: 'panel', name: 'X', at: [0, 0], size: [10, 10], image: 'a.png', slice: [1, 2, 3] }, /arguments\.slice: needs at least 4 item\(s\)/],
      ['ui_add', { canvas: 'Options', kind: 'slider', name: 'X', at: [0, 0], step: -1 }, /arguments\.step: must be >= 0/],
      ['ui_add', { canvas: 'Options', kind: 'slider', name: 'X', at: [0, 0], handle: 'yes' }, /arguments\.handle: expected boolean/],
      ['ui_canvas', { name: 'Title', transition: { open: 'zoom' } }, /arguments\.transition\.open: must be one of "fade", "pop", "slide-up"/],
      ['ui_canvas', { name: 'Title', transition: { opne: 'pop' } }, /arguments\.transition: unknown argument 'opne'/],
      ['ui_canvas', { name: 'Title', transition: { loop: 'spin' } }, /arguments\.transition\.loop: must be one of "pulse", "bob", "blink", "none", null/],
      ['ui_canvas', { name: 'Title', transition: { duration: 0 } }, /arguments\.transition\.duration: must be >= 0\.01/],
      ['ui_canvas', { name: 'Title', transition: 'pop' }, /arguments\.transition: expected object or null, got string/],
      ['ui_set', { canvas: 'HUD', name: 'Lives', count: 0 }, /arguments\.count: must be >= 1/],
      ['ui_add', { canvas: 'Title', kind: 'group', name: 'X' }, /missing required 'at'/],
      ['ui_add', { canvas: 'Title', kind: 'group', name: 'X', at: [1, 2, 3] }, /arguments\.at: allows at most 2 items/],
      ['ui_add', { canvas: 'Title', kind: 'group', name: 'X', at: [0, 0], anchor: 'middle' }, /arguments\.anchor: must be one of "auto", "center", "top-left"/],
      ['ui_add', { canvas: 'Title', kind: 'button', name: 'X', label: 'X', at: [0, 0], action: 'quit' }, /arguments\.action: must be one of "event", "close"/],
      ['ui_add', { canvas: 'Title', kind: 'image', name: 'X', path: 'a.png', at: [0, 0], opacity: 2 }, /arguments\.opacity: must be <= 1/],
      ['ui_add', { canvas: 'Title', kind: 'group', name: 'X', at: [0, 0], blur: 2 }, /unknown argument 'blur'/],
      ['ui_add', { canvas: 'Title', kind: 'text', name: 'X', body: 'x', at: [0, 0], visible: 'no' }, /arguments\.visible: expected boolean/],
      ['ui_add', { canvas: 'Title', kind: 'text', name: 'X', body: 'x', at: [0, 0], shadow: [0, 6, 18, 1] }, /arguments\.shadow: allows at most 3 items/],
      ['ui_set', { canvas: 'Title', name: 'X', shadow: [4] }, /arguments\.shadow: needs at least 2 item\(s\)/],
      ['ui_canvas', { name: 'Title', focus: 3 }, /arguments\.focus/],
      ['ui_canvas', { name: 'Title', open: 'onStart' }, /arguments\.open: must be one of "manual", "start", "firstStart"/],
      ['ui_canvas', { name: 'Title', back: 'quit' }, /arguments\.back: must be one of "none", "close", "resume", "previous", "restart"/],
      ['ui_canvas', { name: 'Title', scale_mode: 'stretch' }, /arguments\.scale_mode: must be one of "fit", "width", "height", "fixed"/],
      ['ui_canvas', { kind: 'hud' }, /missing required 'name'/],
      ['ui_canvas', { name: 'Title', size: [1920] }, /arguments\.size: needs at least 2 item\(s\)/],
      ['ui_set', { canvas: 'HUD', name: 'X', stretch: 'fill' }, /arguments\.stretch: must be one of "none", "width", "height", "both", "cover"/],
      ['ui_set', { canvas: 'HUD', name: 'X', size: 40 }, /arguments\.size: expected array, got number/],
      ['ui_remove', { name: 'X' }, /missing required 'canvas'/],
      ['ui_list', { canvas: '' }, /arguments\.canvas: must not be empty/],
      ['ui_set', { canvas: 'Title', name: 'Play', loop: 'spin' }, /arguments\.loop: must be one of "pulse", "bob", "blink", "none"/],
      ['ui_add', { canvas: 'Title', kind: 'group', name: 'X', at: [0, 0], loop: true }, /arguments\.loop: expected string/],
      ['ui_add', { canvas: 'Title', kind: 'model', name: 'X', at: [0, 0], kit: 'crate', view: 'back' }, /arguments\.view: must be one of "threeQuarter", "front", "side", "top"/],
      ['ui_add', { canvas: 'Title', kind: 'model', name: 'X', at: [0, 0], kit: 'crate', light: 'neon' }, /arguments\.light: must be one of "studio", "soft", "flat"/],
      ['ui_add', { canvas: 'Title', kind: 'model', name: 'X', at: [0, 0], kit: 'crate', zoom: 0 }, /arguments\.zoom: must be >= 0\.05/],
      ['ui_add', { canvas: 'Title', kind: 'model', name: 'X', at: [0, 0], kit: '' }, /arguments\.kit: must not be empty/],
      ['ui_add', { canvas: 'Title', kind: 'model', name: 'X', at: [0, 0], kit: 'crate', kit_params: 'gem' }, /arguments\.kit_params: expected object/],
      ['ui_add', { canvas: 'Title', kind: 'panel', name: 'X', at: [0, 0], size: [1, 1], shader_values: [1] }, /arguments\.shader_values: expected object/],
      ['ui_add', { canvas: 'Title', kind: 'text', name: 'X', body: 'x', at: [0, 0], project_name: 'yes' }, /arguments\.project_name: expected boolean/],
      ['ui_set', { canvas: 'Title', name: 'Gem', spin: 'fast' }, /arguments\.spin: expected number/],
      ['ui_translate', { locale: 'fr' }, /missing required 'strings'/],
      ['ui_translate', { strings: {} }, /missing required 'locale'/],
      ['ui_translate', { locale: 'fr', strings: {}, remove: 'Play' }, /arguments\.remove: expected array/],
      ['ui_translate', { locale: 'fr', strings: {}, name: '' }, /arguments\.name: must not be empty/],
      ['ui_strings', { locale: 'fr', canvas: 'Title' }, /unknown argument 'canvas'/],
    ];
    for (const [tool, args, problem] of cases) {
      const r = await callTool(server, tool, args);
      expect(r.isError, `${tool} ${JSON.stringify(args)}`).toBe(true);
      expect(r.data.problems.join('\n'), `${tool} ${JSON.stringify(args)}`).toMatch(problem);
    }
    expect(bridge.calls).toHaveLength(0);
  });

  it('addParams leaves arguments that fit their kind as they are', () => {
    const button = { canvas: 'T', kind: 'button', name: 'B', label: 'Go', at: [1, 2], size: [300, 80], text_size: 36, font: 'assets/fonts/Baloo2-Bold.ttf' };
    expect(addParams(button)).toEqual(button);
    expect(addParams({ canvas: 'T', kind: 'text', name: 'T1', body: 'Hi', at: [1, 2], size: 30, color: [1, 1, 1, 1] })).toMatchObject({ size: 30, color: [1, 1, 1, 1] });
    const hearts = { canvas: 'HUD', kind: 'icons', name: 'Lives', at: [140, 60], value: 'lives', size: 52 };
    expect(addParams(hearts)).toEqual(hearts); // an icon row's size is a number
    const slider = { canvas: 'Options', kind: 'slider', name: 'Music', at: [1100, 460], value: 'audio.music', stretch: 'none' };
    expect(addParams(slider)).toEqual({ canvas: 'Options', kind: 'slider', name: 'Music', at: [1100, 460], value: 'audio.music', anchor: ['auto', 'none'] });
    // A model without kit, asset or source is an update (Blender refuses a new one without); a locale button
    // without target switches to the next language.
    const update = { canvas: 'Title', kind: 'model', name: 'Gem', at: [960, 420], zoom: 2 };
    expect(addParams(update)).toEqual(update);
    const language = { canvas: 'Options', kind: 'button', name: 'Language', label: '{ui.localeName}', at: [960, 700], action: 'locale' };
    expect(addParams(language)).toEqual(language);
    for (const code of ['fr', 'pt-BR', 'zh-Hans', 'zh-Hant-TW', 'es-419', 'pt_br']) expect(LOCALE_CODE.test(code), code).toBe(true);
    for (const code of ['french', 'f', 'en-', 'en-USA-x', '']) expect(LOCALE_CODE.test(code), code).toBe(false);
  });
});

describe('ui tool errors', () => {
  it('says to call ui_open when no UI file is open', async () => {
    // However the bridge says it: a code in the error data, or the code name in the message.
    const refusals = [
      () => {
        throw { code: -32000, message: 'The open file is not a UI file (ui/*.blend).', data: { code: 'ui_file_not_open', hint: 'Open it.', file: 'scenes/main.blend' } };
      },
      () => {
        throw { code: -32000, message: 'ui_file_not_open: scenes/main.blend is open, not a UI file.' };
      },
    ];
    for (const refuse of refusals) {
      for (const tool of ['ui_canvas', 'ui_add', 'ui_set', 'ui_remove', 'ui_list']) bridge.handlers[tool] = refuse;
      const r = await callTool(server, 'ui_add', { canvas: 'Title', kind: 'group', name: 'G', at: [960, 540] });
      expect(r.isError).toBe(true);
      expect(r.data.code).toBe('ui_file_not_open');
      expect(r.data.hint).toBe(UI_FILE_NOT_OPEN_HINT);
      expect(r.data.hint).toMatch(/call ui_open first.*save: true/i);
      for (const tool of ['ui_canvas', 'ui_set', 'ui_remove', 'ui_list']) {
        const args = { ui_canvas: { name: 'HUD' }, ui_set: { canvas: 'HUD', name: 'S', visible: true }, ui_remove: { canvas: 'HUD' }, ui_list: {} }[tool];
        expect((await callTool(server, tool, args)).data.code, tool).toBe('ui_file_not_open');
      }
    }
    // Other data from Blender stays.
    bridge.handlers.ui_add = refusals[0];
    expect((await callTool(server, 'ui_add', { canvas: 'Title', kind: 'group', name: 'G', at: [0, 0] })).data).toMatchObject({ file: 'scenes/main.blend' });
  });

  it("passes Blender's other refusals on with their hint, and points an old extension at an update", async () => {
    bridge.handlers.ui_add = () => {
      throw { code: -32000, message: "'Play' is a button, not a text.", data: { hint: 'Use another name, or ui_remove it first.' } };
    };
    const r = await callTool(server, 'ui_add', { canvas: 'Title', kind: 'text', name: 'Play', body: 'Play', at: [960, 620] });
    expect(r.data).toMatchObject({ code: 'blender_error', error: "'Play' is a button, not a text.", hint: 'Use another name, or ui_remove it first.' });
    delete bridge.handlers.ui_list;
    const old = await callTool(server, 'ui_list');
    expect(old.data).toMatchObject({ code: 'unknown_method', hint: expect.stringMatching(/update the extension/) });
  });
});

describe('pre-approval', () => {
  it('pre-approves the read and write ui tools wherever set_component is, but not ui_remove', () => {
    const files = [
      ...readdirSync(path.join(REPO, 'blender/gamestudio/project_templates')).map((t) => `blender/gamestudio/project_templates/${t}/.claude/settings.json`),
      ...readdirSync(path.join(REPO, 'examples')).map((e) => `examples/${e}/.claude/settings.json`),
    ].filter((rel) => existsSync(path.join(REPO, rel)));
    const approving = files.filter((rel) => JSON.parse(readFileSync(path.join(REPO, rel), 'utf8')).permissions?.allow?.includes('mcp__gamestudio__set_component'));
    expect(approving.length).toBeGreaterThan(0);
    for (const rel of approving) {
      const allow = JSON.parse(readFileSync(path.join(REPO, rel), 'utf8')).permissions.allow;
      for (const tool of ['ui_list', 'ui_open', 'ui_canvas', 'ui_add', 'ui_set', 'ui_strings', 'ui_translate']) expect(allow, rel).toContain(`mcp__gamestudio__${tool}`);
      expect(allow, rel).not.toContain('mcp__gamestudio__ui_remove');
    }
  });
});

/**
 * A stand-in for the headless game page: evaluate() runs the function here against a fake window.__gs.
 * menu: a menu open already. animating: test.state() calls a canvas's open animation lasts (ui.animating), or
 * null for an engine without ui.animating; layerSeconds: such an engine's canvas data open duration.
 */
function fakeGamePage({ canvases = ['Title', 'HUD', 'Pause'], opens = true, menu = null, animating = 0, layerSeconds = null } = {}) {
  /** What test.state().ui reports (docs/GAME-UI.md §5.7). */
  const ui = { screen: menu, focus: null, buttons: [], open: menu ? ['HUD', menu] : ['HUD'], texts: { 'HUD/Score': 'Score 0' }, hidden: ['HUD/Heart3'], ...(animating !== null && { animating: [] }) };
  const messages = [];
  const store = {};
  const saved = {};
  /** What the game had when a canvas was asked to open: its store and save, and the time. */
  const atOpen = [];
  let polls = 0;
  const gs = {
    state: 'playing',
    game: {
      scene: { name: 'main', rev: 3 },
      store,
      save: { set: (k, v) => (saved[k] = v) },
      ...(canvases && { ui: { canvases, ...(layerSeconds !== null && { screens: { layer: { data: () => ({ animations: { open: { duration: layerSeconds } } }) } } }) } }),
    },
    test: {
      state: () => {
        if (ui.animating?.length && ++polls > animating) ui.animating = [];
        return { step: 0, ui: JSON.parse(JSON.stringify(ui)), entities: [] };
      },
    },
    message(message) {
      messages.push(message);
      if (message.t === 'ui.close' && ui.screen) {
        Object.assign(ui, { open: ui.open.filter((n) => n !== ui.screen), screen: null, buttons: [] });
      }
      if (message.t === 'ui.open' && opens) {
        atOpen.push({ store: { ...store }, saved: { ...saved }, at: Date.now() });
        Object.assign(ui, {
          screen: message.canvas,
          focus: 'Play',
          buttons: [{ label: 'Play', name: 'PlayButton', canvas: message.canvas, disabled: false, x: 240, y: 160, width: 120, height: 28 }],
          open: [...ui.open.filter((n) => n !== message.canvas), message.canvas],
          texts: { ...ui.texts, [`${message.canvas}/TitleText`]: 'Gem Island' },
          ...(ui.animating && animating > 0 && { animating: [message.canvas] }),
        });
        polls = 0;
      }
      return [];
    },
  };
  globalThis.window = { __gs: gs };
  const shots = [];
  const page = {
    evaluate: async (fn, ...args) => fn(...args),
    viewport: () => ({ width: 480, height: 270 }),
    setViewport: async () => {},
    screenshot: async () => {
      shots.push({ ...JSON.parse(JSON.stringify(ui)), at: Date.now() });
      return Buffer.from('PNG');
    },
    isClosed: () => false,
  };
  return { page, messages, shots, ui, store, saved, atOpen };
}

describe('game_screenshot ui', () => {
  const timeouts = { ...UI_TIMEOUTS };
  let fake;
  beforeEach(() => {
    ctx.ensureDevServer = async () => ({ url: 'http://localhost:5999/' });
    ctx.hub.ensure = async () => {};
    ctx.browser.ensureGame = async () => ({ page: fake.page, tag: 'test-game', url: 'http://localhost:5999/', logs: [], size: [480, 270], opened: false });
    Object.assign(UI_TIMEOUTS, { known: 200, open: 200, close: 200, animation: 400 });
  });
  afterEach(() => {
    Object.assign(UI_TIMEOUTS, timeouts);
    delete globalThis.window;
  });

  it('opens the canvas with ui.open before the shot and reports the UI', async () => {
    fake = fakeGamePage();
    const r = await callTool(server, 'game_screenshot', { ui: 'Title', save: true });
    expect(r.isError, JSON.stringify(r.data)).toBe(false);
    expect(fake.messages).toEqual([{ t: 'ui.open', canvas: 'Title' }]);
    expect(fake.shots[0].open).toEqual(['HUD', 'Title']); // open when the picture was taken
    expect(r.images).toHaveLength(1);
    expect(r.data).toMatchObject({
      game: { state: 'playing', scene: 'main', rev: 3 },
      ui: {
        screen: 'Title',
        focus: 'Play',
        buttons: [{ label: 'Play', name: 'PlayButton', canvas: 'Title', x: 240, y: 160 }],
        open: ['HUD', 'Title'],
        texts: { 'HUD/Score': 'Score 0', 'Title/TitleText': 'Gem Island' },
        hidden: ['HUD/Heart3'],
      },
    });
    expect(r.data.warnings).toBeUndefined();
    expect(r.data.saved).toMatch(/screenshots[\\/].*ui-Title\.png$/);
  });

  it('reports the UI without ui too, and refuses ui with mode "scene"', async () => {
    fake = fakeGamePage();
    const r = await callTool(server, 'game_screenshot', {});
    expect(fake.messages).toEqual([]);
    expect(r.data.ui).toMatchObject({ screen: null, open: ['HUD'], texts: { 'HUD/Score': 'Score 0' } });
    const scene = await callTool(server, 'game_screenshot', { mode: 'scene', ui: 'Title' });
    expect(scene.isError).toBe(true);
    expect(scene.data).toMatchObject({ code: 'invalid_arguments', error: expect.stringMatching(/^ui goes with mode "game"/) });
  });

  it('names the canvases the game has when it does not know the one asked for', async () => {
    fake = fakeGamePage();
    const r = await callTool(server, 'game_screenshot', { ui: 'title' });
    expect(r.isError).toBe(true);
    expect(r.data).toMatchObject({ code: 'unknown_canvas', error: "The game has no UI canvas 'title'. Its canvases: Title, HUD, Pause." });
    expect(r.data.hint).toMatch(/ui_list/);
    expect(fake.messages).toEqual([]);
    fake = fakeGamePage({ canvases: [] });
    expect((await callTool(server, 'game_screenshot', { ui: 'Title' })).data.error).toMatch(/It has none\./);
  });

  it('says when the engine cannot open canvases, or the canvas did not open', async () => {
    fake = fakeGamePage({ canvases: null });
    const old = await callTool(server, 'game_screenshot', { ui: 'Title' });
    expect(old.data).toMatchObject({ code: 'ui_unsupported', hint: expect.stringMatching(/Update @gamestudio\/engine/) });
    fake = fakeGamePage({ opens: false });
    const late = await callTool(server, 'game_screenshot', { ui: 'Title' });
    expect(late.isError).toBe(false);
    expect(late.data.warnings).toEqual(["The UI canvas 'Title' did not report open in time; the shot may not show it. Check game_logs."]);
    expect(fake.messages).toEqual([{ t: 'ui.open', canvas: 'Title' }]);
  });

  it('closes another open menu first, so two menus never overlap', async () => {
    fake = fakeGamePage({ menu: 'Pause' });
    const r = await callTool(server, 'game_screenshot', { ui: 'Title' });
    expect(fake.messages).toEqual([{ t: 'ui.close' }, { t: 'ui.open', canvas: 'Title' }]);
    expect(fake.shots[0].open).toEqual(['HUD', 'Title']);
    expect(r.data.ui_closed).toBe('Pause');
    // The menu asked for, already open, is not closed; nor is a HUD.
    fake = fakeGamePage({ menu: 'Title' });
    const again = await callTool(server, 'game_screenshot', { ui: 'Title' });
    expect(fake.messages).toEqual([{ t: 'ui.open', canvas: 'Title' }]);
    expect(again.data.ui_closed).toBeUndefined();
  });

  it('shoots once the open animation has ended (ui.animating), or after the open duration on older engines', async () => {
    fake = fakeGamePage({ animating: 3 });
    const r = await callTool(server, 'game_screenshot', { ui: 'Title' });
    expect(fake.shots[0].animating).toEqual([]);
    expect(r.data.warnings).toBeUndefined();
    // Still animating when the wait runs out: shot anyway, with a warning.
    fake = fakeGamePage({ animating: 1e9 });
    const slow = await callTool(server, 'game_screenshot', { ui: 'Title' });
    expect(fake.shots[0].animating).toEqual(['Title']);
    expect(slow.data.warnings).toEqual([expect.stringMatching(/^The UI canvas 'Title' was still playing its open animation/)]);
    // No ui.animating: the canvas's open duration + 100 ms, from the game's canvas data, else from the export.
    fake = fakeGamePage({ animating: null, layerSeconds: 0.2 });
    await callTool(server, 'game_screenshot', { ui: 'Title' });
    expect(fake.shots[0].at - fake.atOpen[0].at).toBeGreaterThanOrEqual(290);
    const dir = path.join(project.root, '.gamestudio', 'export', 'ui');
    mkdirSync(dir, { recursive: true });
    writeFileSync(path.join(dir, 'Title.ui.json'), JSON.stringify({ animations: { open: { duration: 0.15 } } }));
    expect([exportedOpenSeconds(project.root, 'Title'), exportedOpenSeconds(project.root, 'HUD')]).toEqual([0.15, 0]);
    fake = fakeGamePage({ animating: null });
    await callTool(server, 'game_screenshot', { ui: 'Title' });
    expect(fake.shots[0].at - fake.atOpen[0].at).toBeGreaterThanOrEqual(240);
  });

  it('reads the UI state at the shot\'s size: ui.size and button x/y are pixels of the returned image', async () => {
    fake = fakeGamePage();
    // A page whose game lays the UI out at its viewport's size, as state() does after a resize, and that draws a
    // frame (requestAnimationFrame) at the new size before the shot.
    let viewport = { width: 480, height: 270 };
    const sizes = [];
    fake.page.viewport = () => viewport;
    fake.page.setViewport = async (v) => {
      viewport = v;
      sizes.push([v.width, v.height]);
    };
    const shotAt = [];
    fake.page.screenshot = async () => {
      shotAt.push([viewport.width, viewport.height]);
      return Buffer.from('PNG');
    };
    const state = globalThis.window.__gs.test.state;
    globalThis.window.__gs.test.state = (...a) => {
      const s = state(...a);
      const k = viewport.width / 480;
      s.ui.size = [viewport.width, viewport.height];
      s.ui.buttons = s.ui.buttons.map((b) => ({ ...b, x: b.x * k, y: b.y * k, width: b.width * k, height: b.height * k }));
      return s;
    };
    globalThis.requestAnimationFrame = (fn) => setTimeout(fn, 0);
    try {
      const r = await callTool(server, 'game_screenshot', { ui: 'Title', size: [960, 540] });
      expect(r.isError, JSON.stringify(r.data)).toBe(false);
      expect(shotAt).toEqual([[960, 540]]);
      expect(sizes).toEqual([[960, 540], [480, 270]], 'the page goes back to its own size after the shot');
      expect(r.data.size).toEqual([960, 540]);
      expect(r.data.ui.size).toEqual([960, 540]);
      expect(r.data.ui.buttons[0]).toMatchObject({ name: 'PlayButton', x: 480, y: 320, width: 240, height: 56 });
      // Without size: the page's own size, read as it is.
      const own = await callTool(server, 'game_screenshot', { ui: 'Title' });
      expect(own.data.ui.size).toEqual([480, 270]);
      expect(own.data.ui.buttons[0]).toMatchObject({ x: 240, y: 160 });
      expect(sizes).toHaveLength(2);
    } finally {
      delete globalThis.requestAnimationFrame;
    }
  });

  it('sets ctx.store and ctx.save values before opening the canvas (set), and keeps save for the PNG', async () => {
    fake = fakeGamePage();
    const r = await callTool(server, 'game_screenshot', { ui: 'Title', set: { store: { coins: 7 }, save: { subtitles: true } }, save: true });
    expect(r.isError, JSON.stringify(r.data)).toBe(false);
    expect(fake.atOpen[0]).toMatchObject({ store: { coins: 7 }, saved: { subtitles: true } });
    expect(r.data.saved).toMatch(/ui-Title\.png$/);
    fake = fakeGamePage();
    await callTool(server, 'game_screenshot', { set: { store: { lives: 1 } } });
    expect(fake.store).toEqual({ lives: 1 });
    expect(fake.messages).toEqual([]);
    const tools = Object.fromEntries(createTools(ctx).map((t) => [t.name, t]));
    expect(tools.game_screenshot.inputSchema.properties.save).toMatchObject({ type: 'boolean' });
    expect(Object.keys(tools.game_screenshot.inputSchema.properties.set.properties)).toEqual(['store', 'save']);
    for (const words of ['{"set": {"save": {"subtitles": true}}, "ui": "Options"}', 'real save', 'ui_closed']) {
      expect(tools.game_screenshot.description).toContain(words);
    }
    const scene = await callTool(server, 'game_screenshot', { mode: 'scene', set: { store: { lives: 1 } } });
    expect(scene.data).toMatchObject({ code: 'invalid_arguments', error: expect.stringMatching(/^set goes with mode "game"/) });
    const bad = await callTool(server, 'game_screenshot', { set: { store: 3 } });
    expect(bad.data.problems[0]).toMatch(/set\.store: expected object/);
  });
});
