import { existsSync, readFileSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import WebSocket from 'ws';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createHub } from '../../devserver/src/hub.js';
import { TIMEOUTS } from '../src/blender.js';
import { expandInputs, normalizeKey, screenshotTimes } from '../src/browser.js';
import { Context } from '../src/context.js';
import { createGameStudioServer, createTools } from '../src/index.js';
import { lookup, sections } from '../src/tools/docs.js';
import { TOKEN, callTool, makeProject, startFakeBridge } from './helpers.js';

// The names other agents (Claude Code skills, project settings) rely on.
const REQUIRED = [
  'status', 'open_scene', 'save', 'checkpoint', 'list_checkpoints', 'restore_checkpoint',
  'scene_summary', 'list_objects', 'get_object', 'set_transform', 'delete_objects', 'run_python',
  'list_kits', 'create_from_kit', 'import_asset', 'apply_material', 'validate_assets', 'render_views',
  'list_component_types', 'add_component', 'set_component', 'remove_component',
  'export_scene', 'play', 'stop', 'pause', 'step', 'game_state', 'game_logs', 'playtest', 'game_screenshot',
  'engine_docs',
];
const EXTRA = ['launch_blender', 'undo', 'redo', 'regenerate_kit', 'set_prefab', 'list_clips', 'ground_height', 'raycast',
  'asset_list', 'asset_add', 'asset_place', 'asset_replace', 'asset_make_local', 'asset_spawnable', 'asset_reload', 'asset_library_restore', 'scene_twin',
  'compare_views', 'create_procedural', 'set_procedural', 'save_template', 'bake_lighting', 'set_material_shader',
  'ui_list', 'ui_open', 'ui_canvas', 'ui_add', 'ui_set', 'ui_remove', 'ui_strings', 'ui_translate'];
const PNG_1PX = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==';

let project;
let bridge;
let server;
let ctx;

beforeEach(async () => {
  project = makeProject();
  bridge = await startFakeBridge({
    scene_summary: (p) => ({ scene: 'main', max: p.max_objects ?? null }),
    set_transform: (p) => ({ updated: [p] }),
    open_scene: (p) => ({ opened: p.path }),
    import_asset: (p) => ({ imported: p.path }),
    run_python: (p) => ({ ok: !p.code.includes('raise'), result: 2, ...(p.code.includes('raise') && { error: 'ValueError: x', rolled_back: true }) }),
    render_views: (p) => ({ backend: 'offscreen', layout: [p.views ?? ['iso']], width: 1, height: 1, png: PNG_1PX }),
    scene_twin: () => ({ path: null, text: '{\n"format":"gamestudio.twin"\n}\n' }),
    export_scene: () => ({ glb: 'x.glb', manifest: 'x.scene.json', scene: 'main', rev: 4, entities: 2, seconds: 0.1, scene_load: { t: 'scene.load', scene: 'main', url: '/__gs/export/main.scene.json', rev: 4 } }),
  });
  bridge.writeDiscovery(project.root);
  const env = { HOME: project.root, APPDATA: path.join(project.root, 'none'), BLENDER_USER_RESOURCES: path.join(project.root, 'none') };
  ctx = new Context({ root: project.root, env });
  ({ server } = createGameStudioServer({ root: project.root, context: ctx }));
});
afterEach(async () => {
  await ctx.shutdown();
  await bridge.close();
  project.cleanup();
});

describe('tool definitions', () => {
  it('has every required tool with an object schema, a description and annotations', () => {
    const tools = createTools(ctx);
    const names = tools.map((t) => t.name);
    for (const name of [...REQUIRED, ...EXTRA]) expect(names).toContain(name);
    expect(new Set(names).size).toBe(names.length);
    for (const tool of tools) {
      expect(tool.inputSchema.type).toBe('object');
      expect(tool.inputSchema.additionalProperties).toBe(false);
      expect(tool.description.length).toBeGreaterThan(40);
      expect(tool.annotations).toBeDefined();
      expect(tool.name).toMatch(/^[a-z_]+$/);
    }
    const readOnly = tools.filter((t) => t.annotations?.readOnlyHint).map((t) => t.name);
    expect(readOnly).toEqual(expect.arrayContaining(['status', 'scene_summary', 'get_object', 'game_state', 'game_logs', 'engine_docs', 'ui_list']));
    expect(readOnly).not.toContain('run_python');
  });

  it('documents the playtest input format with an example', () => {
    const playtest = createTools(ctx).find((t) => t.name === 'playtest');
    expect(playtest.description).toContain('{"at": 0, "key": "KeyW", "hold": 2}');
  });
});

describe('Blender tools', () => {
  it('forwards arguments to the bridge method', async () => {
    expect((await callTool(server, 'scene_summary', { max_objects: 5 })).data).toEqual({ scene: 'main', max: 5 });
    await callTool(server, 'set_transform', { name: 'Crate', location: [1, 2, 3] });
    expect(bridge.calls.at(-1)).toEqual({ method: 'set_transform', params: { name: 'Crate', location: [1, 2, 3] } });
  });

  it('accepts the batch and query forms', async () => {
    await callTool(server, 'set_transform', { items: [{ name: 'A', location: [1, 2, 0], snap_to_ground: true }], snap_to_ground: false });
    expect(bridge.calls.at(-1).params.items[0].snap_to_ground).toBe(true);
    const kits = await callTool(server, 'create_from_kit', { items: [{ kind: 'crate', location: [0, 0, 0] }, { kind: 'rock' }], collection: 'Props' });
    expect(kits.isError).toBe(true); // the fake bridge has no create_from_kit, but the arguments validated
    expect(kits.data.problems).toBeUndefined();
    const comps = await callTool(server, 'add_component', { objects: 'Hero', components: { RigidBody: { type: 'kinematic' }, Collider: {} } });
    expect(comps.data.problems).toBeUndefined();
    const query = await callTool(server, 'run_python', { code: 'len(D.objects)', read_only: true });
    expect(query.isError).toBe(false);
    expect(bridge.calls.at(-1)).toEqual({ method: 'run_python', params: { code: 'len(D.objects)', read_only: true } });
    const bad = await callTool(server, 'ground_height', { points: [[1, 2, 3]] });
    expect(bad.data.problems).toEqual(['arguments.points[0]: allows at most 2 items']);
    const scope = await callTool(server, 'validate_assets', { names: 'all', summary: true });
    expect(scope.data.problems).toBeUndefined();
  });

  it('validates compare_views arguments and needs a dev server', async () => {
    const bad = await callTool(server, 'compare_views', { size: [800], engine: 'octane' });
    expect(bad.data.problems).toEqual(['arguments.size: needs at least 2 item(s)', 'arguments.engine: must be one of "eevee", "cycles", "workbench"; got "octane"']);
    const noServer = await callTool(server, 'compare_views', {});
    expect(noServer.isError).toBe(true); // no dev server: it asks Blender (here a fake without one) to start it
  });

  it('edits library assets through the component tools', async () => {
    bridge.handlers.asset_edit_components = (p) => ({ asset: p.asset, op: p.op, library_backup: 'b.blend' });
    const set = await callTool(server, 'set_component', { asset: 'Boulder', component: 'RigidBody', values: { linearDamping: 0.2 } });
    expect(set.data).toEqual({ asset: 'Boulder', op: 'set', library_backup: 'b.blend' });
    expect(bridge.calls.at(-1)).toEqual({ method: 'asset_edit_components', params: { asset: 'Boulder', component: 'RigidBody', values: { linearDamping: 0.2 }, op: 'set' } });
    await callTool(server, 'add_component', { asset: 'Boulder', library: 'props', objects: 'Boulder', components: { Spin: {} } });
    expect(bridge.calls.at(-1).params).toMatchObject({ op: 'add', library: 'props', objects: 'Boulder' });
    await callTool(server, 'remove_component', { asset: 'Boulder', component: 'Spin' });
    expect(bridge.calls.at(-1).params.op).toBe('remove');
    const calls = bridge.calls.length;
    const missing = await callTool(server, 'set_component', { component: 'Spin', values: {} });
    expect(missing.data).toMatchObject({ code: 'invalid_arguments', error: expect.stringMatching(/objects is required/) });
    const stray = await callTool(server, 'set_component', { objects: 'A', library: 'props', component: 'Spin', values: {} });
    expect(stray.data.error).toMatch(/library goes with asset/);
    expect(bridge.calls).toHaveLength(calls); // neither reached Blender
    await callTool(server, 'set_component', { objects: 'A', component: 'Spin', values: { speed: 2 } });
    expect(bridge.calls.at(-1)).toEqual({ method: 'set_component', params: { objects: 'A', component: 'Spin', values: { speed: 2 } } });
    const spawn = await callTool(server, 'asset_add', { objects: 'Boulder', spawn_only: true });
    expect(spawn.data.problems).toBeUndefined();
    expect(bridge.calls.at(-1)).toEqual({ method: 'asset_add', params: { objects: 'Boulder', spawn_only: true } });
    await callTool(server, 'asset_library_restore', { library: 'props', backup: '20261010-0715' });
    expect(bridge.calls.at(-1)).toEqual({ method: 'asset_library_restore', params: { library: 'props', backup: '20261010-0715' } });
    expect((await callTool(server, 'asset_library_restore', {})).data.problems).toEqual(["arguments: missing required 'library'"]);
    expect((await callTool(server, 'asset_list', { backups: true })).data.problems).toBeUndefined();
  });

  it('passes procedural builds and edits on', async () => {
    bridge.handlers.create_procedural = (p) => ({ object: p.name, kind: p.kind, params: p.params });
    const made = await callTool(server, 'create_procedural', { kind: 'scatter', name: 'Forest', params: { surface: 'Hill', sources: ['Tree_1'], density: 0.03 } });
    expect(made.data).toEqual({ object: 'Forest', kind: 'scatter', params: { surface: 'Hill', sources: ['Tree_1'], density: 0.03 } });
    expect((await callTool(server, 'create_procedural', { kind: 'forest' })).data.problems[0]).toMatch(/kind: must be one of "scatter"/);
    await callTool(server, 'set_procedural', { object: 'Forest', params: { seed: 3 } });
    expect(bridge.calls.at(-1)).toEqual({ method: 'set_procedural', params: { object: 'Forest', params: { seed: 3 } } });
    expect((await callTool(server, 'set_procedural', { object: 'Forest' })).data.problems).toEqual(["arguments: missing required 'params'"]);
  });

  it('passes snap, label and camera options on', async () => {
    await callTool(server, 'set_transform', { items: [{ name: 'Rock', snap_to_ground: true, tilt: true }], align: 'average' });
    expect(bridge.calls.at(-1).params).toEqual({ items: [{ name: 'Rock', snap_to_ground: true, tilt: true }], align: 'average' });
    const badAlign = await callTool(server, 'set_transform', { name: 'Rock', snap_to_ground: true, align: 'middle' });
    expect(badAlign.data.problems[0]).toMatch(/align: must be one of "lowest", "average", "highest"/);
    await callTool(server, 'render_views', { names: ['Player', 'Key_1'] });
    expect(bridge.calls.at(-1).params).toEqual({ names: ['Player', 'Key_1'] });
    expect((await callTool(server, 'render_views', { labels: ['Player'] })).data.problems).toBeUndefined();
    const half = await callTool(server, 'compare_views', { camera: { from: [0, -10, 3] } });
    expect(half.data.problems).toEqual(["arguments.camera: missing required 'at'"]);
    const stray = await callTool(server, 'compare_views', { target: 'Hill' });
    expect(stray.data.error).toMatch(/target goes with view/);
    const both = await callTool(server, 'compare_views', { view: 'iso', camera: 'Cam' });
    expect(both.data.error).toMatch(/camera or view/);
    const posed = await callTool(server, 'compare_views', { camera: { from: [0, -10, 3], at: [0, 0, 1], lens: 50 } });
    expect(posed.data.problems).toBeUndefined(); // valid; it then fails for want of a dev server
    expect((await callTool(server, 'compare_views', { view: 'top', target: ['Hill'] })).data.problems).toBeUndefined();
  });

  it('returns the scene twin as plain text', async () => {
    const r = await callTool(server, 'scene_twin');
    expect(r.data).toEqual({ format: 'gamestudio.twin' });
    const add = await callTool(server, 'asset_add', { objects: ['Crate'], tags: 'props', library: 'props' });
    expect(add.data.problems).toBeUndefined(); // valid arguments (the fake bridge has no asset_add)
    const place = await callTool(server, 'asset_place', { asset: 'Crate', scale: [1, 2] });
    expect(place.data.problems).toEqual(['arguments.scale: needs at least 3 item(s)']);
  });

  it('resolves paths against the project', async () => {
    await callTool(server, 'open_scene', { path: 'scenes/main.blend' });
    expect(bridge.calls.at(-1).params.path).toBe(path.join(project.root, 'scenes', 'main.blend'));
    await callTool(server, 'import_asset', { path: 'assets/tree.glb' });
    expect(bridge.calls.at(-1).params.path).toBe(path.join(project.root, 'assets', 'tree.glb'));
  });

  it('validates arguments before calling Blender', async () => {
    const r = await callTool(server, 'set_transform', { name: 'Crate', location: [1, 2] });
    expect(r.isError).toBe(true);
    expect(r.data.problems).toEqual(['arguments.location: needs at least 3 item(s)']);
    expect(bridge.calls).toHaveLength(0);
  });

  it('marks failed run_python calls as errors', async () => {
    expect((await callTool(server, 'run_python', { code: '1 + 1' })).isError).toBe(false);
    const failed = await callTool(server, 'run_python', { code: 'raise ValueError()' });
    expect(failed.isError).toBe(true);
    expect(failed.data.rolled_back).toBe(true);
  });

  it('returns rendered views as image content and can save them', async () => {
    const r = await callTool(server, 'render_views', { target: 'Crate', views: ['front', 'iso'], focus: true, save: true });
    expect(r.images).toEqual([{ type: 'image', data: PNG_1PX, mimeType: 'image/png' }]);
    expect(r.data).toMatchObject({ layout: [['front', 'iso']], width: 1, height: 1 });
    expect(r.data.png).toBeUndefined(); // the image travels as image content, not inside the JSON
    expect(bridge.calls.at(-1).params).toEqual({ target: 'Crate', views: ['front', 'iso'], focus: true });
    expect(readFileSync(r.data.saved).subarray(1, 4).toString()).toBe('PNG');
    const bad = await callTool(server, 'render_views', { size: 4000 });
    expect(bad.data.problems).toEqual(['arguments.size: must be <= 2048']);
  });

  it('tells Blender which MCP client it serves, and passes on a pause by the user', async () => {
    const clientInfo = { name: 'claude-code', version: '2.1.3' };
    await server.handle({ jsonrpc: '2.0', id: 1, method: 'initialize', params: { protocolVersion: '2025-06-18', capabilities: {}, clientInfo } });
    await callTool(server, 'scene_summary');
    expect(bridge.hellos.at(-1)).toMatchObject({ token: TOKEN, agent: clientInfo });
    const hint = 'the user paused the agent in Blender; ask them or wait';
    bridge.handlers.delete_objects = () => {
      throw { code: -32002, message: 'The user paused the agent in Blender, so delete_objects was not run.', data: { hint } };
    };
    const refused = await callTool(server, 'delete_objects', { names: ['Crate'] });
    expect(refused.isError).toBe(true);
    expect(refused.data).toMatchObject({ code: 'paused_by_user', hint });
    bridge.handlers.ping = () => ({ pong: true });
    bridge.handlers.status = () => ({ blender: { pid: 1 }, file: { path: 'x.blend', dirty: false }, scene: { name: 'main', objects: 2 }, agent: { paused: true, locked_objects: 3 } });
    const status = await callTool(server, 'status');
    expect(status.data.blender).toMatchObject({ connected: true, paused: true, locked_objects: 3 });
    expect(status.data.next.join(' ')).toContain('paused');
  });

  it('explains how to start Blender when it is not running', async () => {
    await bridge.close();
    ctx.blender.client?.close();
    await new Promise((r) => setTimeout(r, 20));
    writeFileSync(path.join(project.root, '.gamestudio', 'blender.json'), '{}');
    const r = await callTool(server, 'get_object', { name: 'Crate' });
    expect(r.isError).toBe(true);
    expect(r.data.code).toBe('blender_not_running');
    expect(r.data.error).toContain('launch_blender');
    const status = await callTool(server, 'status');
    expect(status.isError).toBe(false);
    expect(status.data.blender.connected).toBe(false);
    expect(status.data.next[0]).toContain('launch_blender');
  });
});

describe('game tools against a real hub', () => {
  let hub;
  let game;
  const sent = [];

  beforeEach(async () => {
    hub = await createHub({ editorPort: 0, gamePort: 0, token: TOKEN });
    writeFileSync(path.join(project.root, '.gamestudio', 'session.json'),
      JSON.stringify({ pid: process.pid, url: 'http://localhost:5999/', editorPort: hub.editorPort, gamePort: hub.gamePort, token: TOKEN }));
    sent.length = 0;
    await ctx.hub.ensure(); // before the game connects, so its first messages reach the tool peer
    let state = 'playing';
    game = new WebSocket(`ws://127.0.0.1:${hub.gamePort}`);
    await new Promise((resolve) => game.once('open', resolve));
    const reply = (msg) => game.send(JSON.stringify(msg));
    game.on('message', (data) => {
      const msg = JSON.parse(String(data));
      if (msg.t === 'welcome') {
        reply({ t: 'game.status', state });
        reply({ t: 'game.ready', scene: 'main', rev: 3 });
        reply({ t: 'log', level: 'info', msg: 'hello from the game' });
        reply({ t: 'error', msg: 'TypeError: boom', file: 'src/systems/coins.ts', line: 12 });
        return;
      }
      sent.push(msg);
      if (msg.t === 'state.request') {
        reply({ t: 'state.response', re: msg.id, to: msg.from, entities: [
          { id: 'gs_000000000001', name: 'Player', position: [0, 1, 2], rotation: [0, 0, 0, 1], visible: true, components: {} },
          { id: 'gs_000000000002', name: 'Coin1', position: [3, 1, 0], rotation: [0, 0, 0, 1], visible: true, components: { Coin: { value: 1 } } },
        ] });
      } else if (msg.t === 'play.pause') {
        state = msg.paused ? 'paused' : 'playing';
        reply({ t: 'game.status', state });
      } else if (msg.t === 'play.stop') {
        state = 'stopped';
        reply({ t: 'game.status', state });
      } else if (msg.t === 'scene.load') {
        reply({ t: 'game.ready', scene: msg.scene, rev: msg.rev });
      }
    });
    game.send(JSON.stringify({ t: 'hello', role: 'game', token: TOKEN, protocol: 1, client: 'test-game' }));
    await ctx.hub.waitFor(() => ctx.hub.logs.length >= 2 && ctx.hub.gamePeers()[0]?.rev === 3, 2000);
  });
  afterEach(async () => {
    game.close();
    ctx.hub.close();
    await hub.close();
  });

  it('reads live state, filtered by name', async () => {
    const r = await callTool(server, 'game_state', { names: ['Coin1', 'Ghost'] });
    expect(r.data.entities.map((e) => e.name)).toEqual(['Coin1']);
    expect(r.data.entities[0]).toEqual({ name: 'Coin1', id: 'gs_000000000002', position: [3, 1, 0], components: { Coin: { value: 1 } } });
    const full = await callTool(server, 'game_state', { names: ['Player'], verbose: true });
    expect(full.data.entities[0]).toMatchObject({ rotation: [0, 0, 0, 1], visible: true });
    expect(r.data.missing).toEqual(['Ghost']);
    expect(r.data.game).toMatchObject({ state: 'playing', scene: 'main', rev: 3 });
    expect(sent.find((m) => m.t === 'state.request').filter).toEqual({ limit: 2000 });
  });

  it('returns game logs with a cursor and a level filter', async () => {
    const all = await callTool(server, 'game_logs');
    expect(all.data.logs.map((l) => l.msg)).toEqual(['hello from the game', 'TypeError: boom']);
    expect(all.data.logs[1]).toMatchObject({ level: 'error', file: 'src/systems/coins.ts', line: 12 });
    const errors = await callTool(server, 'game_logs', { level: 'error' });
    expect(errors.data.count).toBe(1);
    const newer = await callTool(server, 'game_logs', { since: all.data.next });
    expect(newer.data.logs).toEqual([]);
  });

  it('pauses, resumes and stops every game', async () => {
    expect((await callTool(server, 'pause')).data.games[0].state).toBe('paused');
    expect((await callTool(server, 'pause', { paused: false })).data.games[0].state).toBe('playing');
    expect((await callTool(server, 'stop')).data.games[0].state).toBe('stopped');
    await callTool(server, 'step', { frames: 3 });
    expect(sent.find((m) => m.t === 'play.step')).toMatchObject({ frames: 3 });
  });

  it('export_scene {all} exports every level and reloads each game\'s own', async () => {
    bridge.handlers.export_all = () => ({
      scenes: [{ file: 'scenes/main.blend', scene: 'main', rev: 9, entities: 4 }, { file: 'scenes/level2.blend', scene: 'level2', rev: 2, entities: 3 }],
      errors: [],
      scene_loads: [{ t: 'scene.load', scene: 'main', url: '/__gs/export/main.scene.json', rev: 9 }, { t: 'scene.load', scene: 'level2', url: '/__gs/export/level2.scene.json', rev: 2 }],
    });
    const r = await callTool(server, 'export_scene', { all: true });
    expect(r.data.scenes.map((s) => s.scene)).toEqual(['main', 'level2']);
    expect(r.data.scene_loads).toBeUndefined();
    expect(r.data.reloaded_games).toHaveLength(1); // the game shows main: it gets main's export only
    await ctx.hub.waitFor(() => ctx.hub.gamePeers()[0]?.rev === 9, 2000);
    expect(sent.filter((m) => m.t === 'scene.load')).toEqual([expect.objectContaining({ scene: 'main', rev: 9 })]);
    expect((await callTool(server, 'export_scene', { all: true, verbose: true })).data.error).toMatch(/verbose/);
  });

  it('playtest refuses a level that was never exported', async () => {
    const r = await callTool(server, 'playtest', { scene: 'level9', duration: 1 });
    expect(r.isError).toBe(true);
    expect(r.data.error).toMatch(/'level9' has never been exported/);
    expect(r.data.hint).toMatch(/export_scene \{"all": true\}/);
  });

  it('export_scene reloads connected games', async () => {
    const r = await callTool(server, 'export_scene');
    expect(r.data.rev).toBe(4);
    expect(r.data.reloaded_games).toHaveLength(1);
    await ctx.hub.waitFor(() => ctx.hub.gamePeers()[0]?.rev === 4, 2000);
    expect(sent.find((m) => m.t === 'scene.load')).toMatchObject({ scene: 'main', rev: 4, url: '/__gs/export/main.scene.json' });
  });

  it('export_scene passes force to Blender and shows the tier', async () => {
    bridge.handlers.export_scene = (p) => ({ scene: 'main', rev: 4, entities: 2, tier: p.force ? 'full' : 'layout', scene_load: { t: 'scene.load', scene: 'main', url: '/__gs/export/main.scene.json', rev: 4 } });
    expect((await callTool(server, 'export_scene')).data.tier).toBe('layout');
    expect((await callTool(server, 'export_scene', { force: true })).data.tier).toBe('full');
    expect(bridge.calls.filter((c) => c.method === 'export_scene').map((c) => c.params)).toEqual([
      { verbose: false, compress: false, force: false },
      { verbose: false, compress: false, force: true },
    ]);
    // export_all has no force: refused rather than ignored.
    const all = await callTool(server, 'export_scene', { all: true, force: true });
    expect(all.isError).toBe(true);
    expect(all.data.error).toMatch(/force goes with a single export/);
    expect(bridge.calls.some((c) => c.method === 'export_all')).toBe(false);
  });

  /** play's answer and how long it took. */
  const timedPlay = async (args) => {
    const started = Date.now();
    const r = await callTool(server, 'play', args);
    return { r, ms: Date.now() - started };
  };

  it('play settles at once on a "reuse" export the game already shows (same rev), without reloading it', async () => {
    bridge.handlers.export_scene = (p) => ({ scene: 'main', rev: 3, entities: 2, tier: 'reuse', force: p.force, scene_load: { t: 'scene.load', scene: 'main', url: '/__gs/export/main.scene.json', rev: 3 } });
    const { r, ms } = await timedPlay({ force: false });
    expect(r.isError).toBe(false);
    expect(r.data.warning).toBeUndefined();
    expect(r.data.export).toEqual({ scene: 'main', rev: 3, entities: 2, tier: 'reuse' });
    expect(ms).toBeLessThan(5000);
    expect(sent.filter((m) => m.t === 'scene.load')).toEqual([]);
    await vi.waitFor(() => expect(sent.filter((m) => m.t === 'play.start')).toHaveLength(1));
    expect(bridge.calls.find((c) => c.method === 'export_scene').params).toEqual({ force: false });
    await callTool(server, 'play', { force: true });
    expect(bridge.calls.filter((c) => c.method === 'export_scene').at(-1).params).toEqual({ force: true });
  });

  it('play accepts a game that already shows a newer rev of the scene', async () => {
    bridge.handlers.export_scene = () => ({ scene: 'main', rev: 2, entities: 2, tier: 'reuse', scene_load: { t: 'scene.load', scene: 'main', url: '/__gs/export/main.scene.json', rev: 2 } });
    const { r, ms } = await timedPlay({});
    expect(r.data.warning).toBeUndefined();
    expect(ms).toBeLessThan(5000);
    expect(sent.filter((m) => m.t === 'scene.load')).toEqual([]);
  });

  it('play reloads a game that shows another level, even at the same rev, and waits for that load', async () => {
    game.send(JSON.stringify({ t: 'game.ready', scene: 'level2', rev: 3 }));
    await ctx.hub.waitFor(() => ctx.hub.gamePeers()[0]?.scene === 'level2', 2000);
    bridge.handlers.export_scene = () => ({ scene: 'main', rev: 3, entities: 2, tier: 'reuse', scene_load: { t: 'scene.load', scene: 'main', url: '/__gs/export/main.scene.json', rev: 3 } });
    const { r, ms } = await timedPlay({});
    expect(r.data.warning).toBeUndefined();
    expect(ms).toBeLessThan(5000);
    expect(sent.filter((m) => m.t === 'scene.load')).toEqual([expect.objectContaining({ scene: 'main', rev: 3 })]);
    expect(r.data.games[0]).toMatchObject({ scene: 'main', rev: 3, state: 'playing' });
  });

  it('play in the editor passes force, shows the tier and does not wait for a rev that cannot come', async () => {
    bridge.handlers.play_in_blender = (p) => ({ export: { rev: 3, entities: 2, scene: 'main', tier: p.force ? 'full' : 'reuse' }, sent_now: true, status: null });
    const { r, ms } = await timedPlay({ where: 'editor' });
    expect(r.data.export).toEqual({ rev: 3, entities: 2, scene: 'main', tier: 'reuse' });
    expect(r.data.warning).toBeUndefined();
    expect(ms).toBeLessThan(5000);
    expect(bridge.calls.find((c) => c.method === 'play_in_blender').params).toEqual({ open_window: true, force: false });
    await callTool(server, 'play', { where: 'editor', force: true });
    expect(bridge.calls.filter((c) => c.method === 'play_in_blender').at(-1).params).toEqual({ open_window: true, force: true });
  });

  it('playtest passes force to the export it runs first', async () => {
    bridge.handlers.export_scene = (p) => ({ scene: 'main', rev: 5, entities: 2, tier: p.force ? 'full' : 'reuse', scene_load: { t: 'scene.load', scene: 'main', url: '/__gs/export/main.scene.json', rev: 5 } });
    const r = await callTool(server, 'playtest', { scene: 'level9', duration: 1, force: true }); // refused after the export
    expect(r.data.error).toMatch(/'level9' has never been exported/);
    expect(bridge.calls.find((c) => c.method === 'export_scene').params).toEqual({ force: true });
  });
});

describe('export timeouts', () => {
  it('allow 15 minutes for exports, and a timeout says Blender is still exporting', async () => {
    expect(TIMEOUTS.export_scene).toBe(900_000);
    expect(TIMEOUTS.play_in_blender).toBe(900_000);
    expect(TIMEOUTS.export_all).toBe(900_000);
    bridge.handlers.export_scene = () => new Promise(() => {}); // still exporting
    const error = await ctx.blender.call('export_scene', {}, { timeoutMs: 50 }).catch((e) => e);
    expect(error.code).toBe('blender_timeout');
    expect(error.message).toMatch(/still exporting.*busy.*until that export finishes/);
    expect(error.hint).toMatch(/status/);
    // Other methods keep the generic message.
    bridge.handlers.scene_summary = () => new Promise(() => {});
    const other = await ctx.blender.call('scene_summary', {}, { timeoutMs: 50 }).catch((e) => e);
    expect(other.message).toMatch(/did not answer scene_summary/);
  });
});

describe('game tools without a dev server', () => {
  it('say what to do', async () => {
    const r = await callTool(server, 'game_state');
    expect(r.isError).toBe(true);
    expect(r.data.code).toBe('devserver_not_running');
    expect(r.data.hint).toContain('play');
    const logs = await callTool(server, 'game_logs');
    expect(logs.isError).toBe(false);
    expect(logs.data.logs).toEqual([]);
  });
});

describe('playtest scripts', () => {
  it('expands inputs into timed key and mouse actions', () => {
    expect(expandInputs([{ at: 100, key: 'w', hold: 500 }, { at: 0, key: 'Space', action: 'press' }, { at: 50, mouse: 'click', x: 1, y: 2 }])).toEqual([
      { at: 0, kind: 'key', action: 'press', key: 'Space' },
      { at: 50, kind: 'mouse', action: 'click', x: 1, y: 2, button: 'left', dx: 0, dy: 0 },
      { at: 100, kind: 'key', action: 'down', key: 'KeyW' },
      { at: 600, kind: 'key', action: 'up', key: 'KeyW' },
    ]);
    expect(() => expandInputs([{ at: 0 }])).toThrow(/key.*mouse/);
    expect(normalizeKey('up')).toBe('ArrowUp');
    expect(normalizeKey('7')).toBe('Digit7');
    expect(normalizeKey('ShiftLeft')).toBe('ShiftLeft');
  });

  it('spreads screenshot times', () => {
    expect(screenshotTimes(undefined, 3000)).toEqual([0, 3000]);
    expect(screenshotTimes(3, 3000)).toEqual([0, 1500, 3000]);
    expect(screenshotTimes(1, 3000)).toEqual([3000]);
    expect(screenshotTimes([5000, 100], 3000)).toEqual([100, 3000]);
  });
});

describe('engine docs', () => {
  const doc = '# API\nIntro\n## defineSystem\nSystems run.\n## Input\nctx.input.key()\n';
  it('splits by heading and finds topics by heading, then by content', () => {
    expect(sections(doc).map(([h]) => h)).toEqual(['API', 'defineSystem', 'Input']);
    expect(lookup(doc, 'definesystem')).toBe('## defineSystem\nSystems run.');
    expect(lookup(doc, 'ctx.input')).toContain('## Input');
    expect(lookup(doc, 'nothing-like-this')).toMatchObject({ error: "No section matching 'nothing-like-this'." });
  });

  it('reads the engine API reference', async () => {
    const r = await callTool(server, 'engine_docs', { topic: 'defineComponent' });
    expect(r.isError).toBe(false);
    expect(String(r.data)).toContain('defineComponent');
    expect(existsSync(path.join(project.root, 'node_modules'))).toBe(false); // found through the checkout
  });
});
