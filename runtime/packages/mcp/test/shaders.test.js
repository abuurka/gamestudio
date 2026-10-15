// Game shaders: set_material_shader (bridge method of the same name, blender/gamestudio/core/shaders.py),
// the shaders list of list_component_types and status, and compare_views' tone-mapping note.
import { existsSync, readFileSync, readdirSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { Context } from '../src/context.js';
import { createGameStudioServer, createTools } from '../src/index.js';
import { firstSentence } from '../src/tools/components.js';
import { callTool, makeProject, startFakeBridge } from './helpers.js';

const REPO = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../../..');

/** What the bridge answers: the materials it changed. */
function setResult(p) {
  const names = p.material ? [p.material] : p.objects.map((o) => `${o}_Mat`);
  return {
    materials: names.map((name) => ({ name, shader: p.shader, values: p.shader ? { tile: 3.2, wet: 0, ...p.values } : {}, users: ['Beach'] })),
    warnings: [],
  };
}

const COMPONENTS = { count: 1, components: [{ name: 'Spin', builtin: true, fields: { speed: 'float = 1' } }] };

/** Library shaders (names from the shader library) and a project one, as an older extension lists them compactly:
 * every field, and no category. */
const lib = (name) => ({ name, description: `${name} look. Cheap: no textures.`, builtin: true, fields: { strength: 'float = 1 [0..1]' } });
const LIBRARY_COMPACT = [
  { name: 'Triplanar', description: 'Our triplanar', fields: { tile: 'float = 3.2 [0.01..]' } },
  ...['Water', 'WetSurface', 'Caustics', 'GrassWind', 'SnowCover', 'Dissolve', 'HitFlash', 'SelectionOutline', 'Toon'].map(lib),
];
/** Their raw entries (verbose), which carry the library category. */
const CATEGORIES = { Water: 'Water', WetSurface: 'Water', Caustics: 'Water', GrassWind: 'Nature', SnowCover: 'Nature', Dissolve: 'Magic & Sci-fi', HitFlash: 'Gameplay', SelectionOutline: 'Gameplay', Toon: 'Style' };
const LIBRARY_RAW = LIBRARY_COMPACT.map(({ fields: _fields, ...s }) => ({
  ...s,
  builtin: s.builtin ?? false,
  ...(CATEGORIES[s.name] && { category: CATEGORIES[s.name] }),
  ...(!s.builtin && { source: 'src/shaders/triplanar.ts', category: 'Surfaces' }),
  fields: [],
}));
/** The same as this extension lists them: library shaders in one line with their category, project ones with fields. */
const SHADERS_COMPACT = LIBRARY_COMPACT.map((s) =>
  s.builtin ? { name: s.name, category: CATEGORIES[s.name], description: `${s.name} look.`, builtin: true } : { ...s, category: 'Surfaces' },
);
/** What list_component_types shows for a library shader: its name and what it does (the group is its category). */
const line = (name) => ({ name, description: `${name} look.` });
const LIBRARY_HINT = 'Library shaders show what they do in one line; list_component_types {"type": "<Shader>"} gives one\'s fields.';

let project;
let bridge;
let server;
let ctx;

beforeEach(async () => {
  project = makeProject();
  bridge = await startFakeBridge({
    set_material_shader: (p) => {
      if (p.shader === 'Triplaner') {
        throw { code: -32000, message: "Unknown game shader 'Triplaner'. Did you mean 'Triplanar'?", data: { hint: 'Known shaders: Triplanar, Water.' } };
      }
      return setResult(p);
    },
  });
  bridge.writeDiscovery(project.root);
  ctx = new Context({ root: project.root, env: { HOME: project.root } });
  ({ server } = createGameStudioServer({ root: project.root, context: ctx }));
});
afterEach(async () => {
  await ctx.shutdown();
  await bridge.close();
  project.cleanup();
});

describe('set_material_shader', () => {
  it('is a write tool that says where shaders come from and how to see them', () => {
    const tool = createTools(ctx).find((t) => t.name === 'set_material_shader');
    expect(tool).toBeDefined();
    expect(tool.annotations).toMatchObject({ readOnlyHint: false, destructiveHint: false });
    expect(Object.keys(tool.inputSchema.properties)).toEqual(['material', 'objects', 'slot', 'shader', 'values']);
    expect(tool.inputSchema.required).toEqual(['shader']);
    expect(tool.inputSchema.properties.shader.type).toEqual(['string', 'null']);
    for (const words of ['src/shaders/', 'defineShader', 'list_component_types', 'one undo step', 'shader: null', 'export_scene', 'compare_views']) {
      expect(tool.description).toContain(words);
    }
  });

  it('forwards a material, or objects with slots, to Blender and passes the result on', async () => {
    const r = await callTool(server, 'set_material_shader', { material: 'GS_Sand', shader: 'Triplanar', values: { wet: 0.6 } });
    expect(r.isError).toBe(false);
    expect(bridge.calls.at(-1)).toEqual({ method: 'set_material_shader', params: { material: 'GS_Sand', shader: 'Triplanar', values: { wet: 0.6 } } });
    expect(r.data).toEqual({ materials: [{ name: 'GS_Sand', shader: 'Triplanar', values: { tile: 3.2, wet: 0.6 }, users: ['Beach'] }], warnings: [] });

    // One object name becomes a list, as the bridge takes it.
    await callTool(server, 'set_material_shader', { objects: 'Beach', slot: 0, shader: 'Water' });
    expect(bridge.calls.at(-1).params).toEqual({ objects: ['Beach'], slot: 0, shader: 'Water' });
    await callTool(server, 'set_material_shader', { objects: ['Beach', 'Dune'], slot: [0, 2], shader: null });
    expect(bridge.calls.at(-1).params).toEqual({ objects: ['Beach', 'Dune'], slot: [0, 2], shader: null });
    // Colour fields as sRGB hex go to Blender as they are (it makes them linear), as do other strings.
    await callTool(server, 'set_material_shader', { objects: 'Crate', shader: 'Dissolve', values: { edgeColor: '#FF8A3DCC', progress: 0.5 } });
    expect(bridge.calls.at(-1).params).toEqual({ objects: ['Crate'], shader: 'Dissolve', values: { edgeColor: '#FF8A3DCC', progress: 0.5 } });
    const tool = createTools(ctx).find((t) => t.name === 'set_material_shader');
    expect(tool.description).toContain('Colour fields take "#RRGGBB" or "#RRGGBBAA" (sRGB');
  });

  it('validates arguments before calling Blender', async () => {
    const cases = [
      [{ shader: 'Triplanar' }, { error: 'Pass material or objects.' }],
      [{ material: 'GS_Sand', objects: 'Beach', shader: 'Triplanar' }, { error: 'Pass material or objects, not both.' }],
      [{ material: 'GS_Sand', slot: 1, shader: 'Triplanar' }, { error: 'slot goes with objects.' }],
      [{ material: 'GS_Sand', shader: null, values: { wet: 1 } }, { error: expect.stringMatching(/^values goes with a shader/) }],
      [{ material: 'GS_Sand' }, { problems: ["arguments: missing required 'shader'"] }],
      [{ material: 'GS_Sand', shader: '' }, { problems: ['arguments.shader: must not be empty'] }],
      [{ objects: [], shader: 'Water' }, { problems: ['arguments.objects: needs at least 1 item(s)'] }],
      [{ objects: 'Beach', slot: -1, shader: 'Water' }, { problems: ['arguments.slot: must be >= 0'] }],
      [{ material: 'GS_Sand', shader: 'Water', values: [1] }, { problems: ['arguments.values: expected object, got array'] }],
      // A colour written as hex is "#RRGGBB" or "#RRGGBBAA" (Blender makes it linear).
      [{ objects: 'Crate', shader: 'Dissolve', values: { edgeColor: '#F80' } }, { error: expect.stringMatching(/^values\.edgeColor "#F80" is not a colour: use "#RRGGBB" or "#RRGGBBAA"/) }],
    ];
    for (const [args, expected] of cases) {
      const r = await callTool(server, 'set_material_shader', args);
      expect(r.isError, JSON.stringify(args)).toBe(true);
      expect(r.data, JSON.stringify(args)).toMatchObject(expected);
      if (expected.error) expect(r.data.code).toBe('invalid_arguments');
    }
    expect(bridge.calls).toHaveLength(0);
  });

  it("passes Blender's refusal on with its hint", async () => {
    const r = await callTool(server, 'set_material_shader', { material: 'GS_Sand', shader: 'Triplaner' });
    expect(r.isError).toBe(true);
    expect(r.data).toMatchObject({ code: 'blender_error', error: expect.stringMatching(/Did you mean 'Triplanar'/), hint: 'Known shaders: Triplanar, Water.' });
  });

  it('names the close matches of an unknown shader instead of every shader', async () => {
    bridge.handlers.list_component_types = () => ({ ...COMPONENTS, shaders: SHADERS_COMPACT });
    bridge.handlers.set_material_shader = (p) => {
      throw { code: -32000, message: `Unknown game shader '${p.shader}'.`, data: { hint: `Known game shaders: ${LIBRARY_COMPACT.map((s) => s.name).join(', ')}.` } };
    };
    const hint = async (shader) => (await callTool(server, 'set_material_shader', { material: 'GS_Sand', shader })).data.hint;
    const all = `list_component_types lists all ${LIBRARY_COMPACT.length} game shaders (src/shaders and the shader library); {"type": "<Shader>"} gives one's fields.`;
    const cases = [
      ['Triplaner', 'Close matches: Triplanar.'], // a typo
      ['water', 'Close matches: Water.'], // case
      ['hit_flash', 'Close matches: HitFlash.'], // separators
      ['ToonShader', 'Close matches: Toon.'], // a trailing "shader"
      ['Outline', 'Close matches: SelectionOutline.'], // part of a name
      ['Snow', 'Close matches: SnowCover.'],
      ['Wet', 'Close matches: WetSurface.'],
      ['Fireball', 'No close match.'],
    ];
    for (const [shader, expected] of cases) expect(await hint(shader), shader).toBe(`${expected} ${all}`);
    expect(bridge.calls.at(-1)).toEqual({ method: 'list_component_types', params: {} });
    // Nothing to compare with (an extension without the listing): Blender's own hint stays.
    delete bridge.handlers.list_component_types;
    expect(await hint('Fireball')).toBe(`Known game shaders: ${LIBRARY_COMPACT.map((s) => s.name).join(', ')}.`);
  });

  it('describes the shader library and how to customise a library shader', () => {
    const tool = createTools(ctx).find((t) => t.name === 'set_material_shader');
    for (const words of ['shader library', 'copy it into src/shaders/ under the same name', 'by category', '{"type": "<Shader>"}']) {
      expect(tool.description).toContain(words);
    }
  });

  it('is pre-approved wherever set_component is', () => {
    const files = [
      ...readdirSync(path.join(REPO, 'blender/gamestudio/project_templates')).map((t) => `blender/gamestudio/project_templates/${t}/.claude/settings.json`),
      ...readdirSync(path.join(REPO, 'examples')).map((e) => `examples/${e}/.claude/settings.json`),
    ].filter((rel) => existsSync(path.join(REPO, rel)));
    const approving = files.filter((rel) => JSON.parse(readFileSync(path.join(REPO, rel), 'utf8')).permissions?.allow?.includes('mcp__gamestudio__set_component'));
    expect(approving.length).toBeGreaterThan(0);
    for (const rel of approving) {
      expect(JSON.parse(readFileSync(path.join(REPO, rel), 'utf8')).permissions.allow, rel).toContain('mcp__gamestudio__set_material_shader');
    }
  });
});

describe('registry shaders', () => {
  it('list_component_types groups the shaders: the project first with fields, then the library by category in one line each', async () => {
    bridge.handlers.list_component_types = (p) => ({ ...COMPONENTS, shaders: p.verbose ? LIBRARY_RAW : SHADERS_COMPACT });
    const r = await callTool(server, 'list_component_types');
    expect(r.isError).toBe(false);
    expect(r.data.shaders).toEqual({
      Project: [{ name: 'Triplanar', description: 'Our triplanar', category: 'Surfaces', fields: { tile: 'float = 3.2 [0.01..]' } }],
      Water: [line('Caustics'), line('Water'), line('WetSurface')],
      Nature: [line('GrassWind'), line('SnowCover')],
      'Magic & Sci-fi': [line('Dissolve')],
      Gameplay: [line('HitFlash'), line('SelectionOutline')],
      Style: [line('Toon')],
    });
    expect(Object.keys(r.data.shaders)).toEqual(['Project', 'Water', 'Nature', 'Magic & Sci-fi', 'Gameplay', 'Style']);
    // The hint says where a library shader's fields are.
    expect({ ...r.data, shaders: undefined }).toEqual({ ...COMPONENTS, shaders: undefined, hint: LIBRARY_HINT });
    // The compact entries carry the category: one call.
    expect(bridge.calls).toEqual([{ method: 'list_component_types', params: {} }]);
    // One shader's fields and full description: the raw entry, as it is.
    bridge.handlers.list_component_types = (p) => (p.type ? LIBRARY_RAW.find((s) => s.name === p.type) : { ...COMPONENTS, shaders: SHADERS_COMPACT });
    expect((await callTool(server, 'list_component_types', { type: 'Water' })).data).toEqual(LIBRARY_RAW.find((s) => s.name === 'Water'));

    // An older extension sends the library's fields and no category: the categories come from the raw entries
    // (a second call), and the entries are cut to one line all the same.
    bridge.calls.length = 0;
    bridge.handlers.list_component_types = (p) => ({ ...COMPONENTS, hint: 'Bridge hint.', shaders: p.verbose ? LIBRARY_RAW : LIBRARY_COMPACT });
    const old = (await callTool(server, 'list_component_types')).data;
    expect(old.shaders).toEqual(r.data.shaders);
    expect(old.hint).toBe(`Bridge hint. ${LIBRARY_HINT}`);
    expect(bridge.calls).toEqual([
      { method: 'list_component_types', params: {} },
      { method: 'list_component_types', params: { verbose: true } },
    ]);

    // Unknown categories follow the library's; a built-in shader without one is "Other".
    bridge.calls.length = 0;
    const builtin = (name, category) => ({ name, category, description: `${name} look. More.`, builtin: true });
    bridge.handlers.list_component_types = () => ({
      ...COMPONENTS,
      shaders: [builtin('Rainbow', 'Weather'), builtin('Gem', 'Surfaces'), builtin('Swirl', ''), builtin('Aurora', 'Atmosphere')],
    });
    const grouped = (await callTool(server, 'list_component_types')).data.shaders;
    expect(Object.keys(grouped)).toEqual(['Surfaces', 'Atmosphere', 'Weather', 'Other']);
    expect(grouped.Surfaces).toEqual([line('Gem')]);
    expect(bridge.calls).toHaveLength(1);

    // verbose: the raw entries as a flat list.
    bridge.handlers.list_component_types = (p) => ({ ...COMPONENTS, shaders: p.verbose ? LIBRARY_RAW : SHADERS_COMPACT });
    expect((await callTool(server, 'list_component_types', { verbose: true })).data).toEqual({ ...COMPONENTS, shaders: LIBRARY_RAW });
  });

  it("cuts a library shader's description to its first sentence", () => {
    const cases = [
      ['Animated water: rolling waves. Cheap: no textures.', 'Animated water: rolling waves.'],
      ['Scales 0.5 to 2.0 m (about 1.5 m). Then more.', 'Scales 0.5 to 2.0 m (about 1.5 m).'],
      ['Set it with ctx.shaders.set(e, { hitAt: ctx.time }) in code! It fades.', 'Set it with ctx.shaders.set(e, { hitAt: ctx.time }) in code!'],
      ['One sentence without a stop', 'One sentence without a stop'],
      ['  Spaces around.  ', 'Spaces around.'],
      ['', ''],
      [undefined, ''],
    ];
    for (const [text, expected] of cases) expect(firstSentence(text), String(text)).toBe(expected);
  });

  it('list_component_types without shaders, or from an older extension, has none', async () => {
    bridge.handlers.list_component_types = () => ({ ...COMPONENTS, shaders: [] });
    expect((await callTool(server, 'list_component_types')).data).toEqual({ ...COMPONENTS, shaders: {} });
    expect(bridge.calls).toHaveLength(1);

    // An older extension sends no shaders.
    bridge.handlers.list_component_types = () => COMPONENTS;
    expect((await callTool(server, 'list_component_types')).data).toEqual({ ...COMPONENTS, shaders: {} });
    expect((await callTool(server, 'list_component_types', { verbose: true })).data).toEqual({ ...COMPONENTS, shaders: [] });
    // One raw entry stays as it is.
    bridge.handlers.list_component_types = (p) => ({ name: p.type, builtin: true, fields: [] });
    expect((await callTool(server, 'list_component_types', { type: 'Spin' })).data).toEqual({ name: 'Spin', builtin: true, fields: [] });
    expect(bridge.calls.at(-1)).toEqual({ method: 'list_component_types', params: { type: 'Spin' } });
  });

  it('status counts shader types next to component types', async () => {
    bridge.handlers.ping = () => ({ pong: true });
    const status = { blender: { pid: 1 }, file: { path: 'x.blend', dirty: false }, scene: { name: 'main', objects: 2 }, registry: { components: 14, shaders: 2 } };
    bridge.handlers.status = () => status;
    expect((await callTool(server, 'status')).data.blender).toMatchObject({ component_types: 14, shader_types: 2 });
    bridge.handlers.status = () => ({ ...status, registry: { components: 14 } }); // an older extension
    expect((await callTool(server, 'status')).data.blender).toMatchObject({ component_types: 14, shader_types: 0 });
  });
});

describe('the shader library docs', () => {
  it('API.md §29 has a row, with its thumbnail, for every library shader', () => {
    const library = path.join(REPO, 'runtime/packages/engine/src/shaders/library');
    const api = readFileSync(path.join(REPO, 'runtime/packages/engine/API.md'), 'utf8');
    const section = api.slice(api.indexOf('\n## 29. Shader library'));
    const folders = readdirSync(library).filter((f) => existsSync(path.join(library, f, 'meta.ts')));
    expect(folders.length).toBeGreaterThanOrEqual(31);
    for (const folder of folders) {
      const name = /defineShader\('([A-Za-z0-9]+)'/.exec(readFileSync(path.join(library, folder, 'meta.ts'), 'utf8'))?.[1];
      expect(name, folder).toBeDefined();
      const row = section.split('\n').find((l) => l.includes(`| \`${name}\` |`));
      expect(row, `API.md §29 lists ${name}`).toBeDefined();
      expect(row).toContain(`../../../docs/images/shaders/${folder}.png`);
    }
  });
});

describe('compare_views', () => {
  it('says the game follows the scene\'s view transform, not that tone mapping always differs', () => {
    const tool = createTools(ctx).find((t) => t.name === 'compare_views');
    expect(tool.description).not.toMatch(/AgX|ACES/);
    expect(tool.description).toContain('view transform and exposure');
  });
});
