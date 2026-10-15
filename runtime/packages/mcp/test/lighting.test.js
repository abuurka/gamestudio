// bake_lighting: lightmaps baked in Blender (blender/gamestudio/bridge/lighting.py, core/lightmap.py),
// then exported so connected games reload.
import { readFileSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import WebSocket from 'ws';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { createHub } from '../../devserver/src/hub.js';
import { Context } from '../src/context.js';
import { createGameStudioServer, createTools } from '../src/index.js';
import { BAKE_TIMEOUTS_MS } from '../src/tools/lighting.js';
import { TOKEN, callTool, makeProject, startFakeBridge } from './helpers.js';

const REPO = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../../..');
const SCENE_LOAD = { t: 'scene.load', scene: 'main', url: '/__gs/export/main.scene.json', rev: 5 };

/** What the bridge answers: the bake summary, plus the export when it ran. */
function bakeResult(p) {
  return {
    scene: 'main',
    quality: p.quality ?? 'preview',
    objects: [{ name: 'Ground', atlas: 0, size: 512 }],
    atlases: [{ file: 'main_ab12_0.png', size: 1024, scale: 1.37 }],
    skipped: [{ name: 'Player', reason: 'CharacterController' }],
    baked_lights: [],
    texture_bytes: 1048576,
    texture_mb: 1,
    seconds: 4.2,
    up_to_date: false,
    dir: '/p/.gamestudio/lightmaps/main',
    warnings: [],
    ...(p.export !== false && { export: { glb: 'x.glb', manifest: 'x.scene.json', scene: 'main', rev: 5, entities: 2, seconds: 0.1, scene_load: SCENE_LOAD } }),
  };
}

let project;
let bridge;
let server;
let ctx;

beforeEach(async () => {
  project = makeProject();
  bridge = await startFakeBridge({
    bake_lighting: (p) => {
      if (p.objects === 'Ghost') {
        throw { code: -32000, message: "No object named 'Ghost'.", data: { hint: 'Call list_objects to see the names.' } };
      }
      if (p.objects === 'Player') {
        throw { code: -32000, message: 'Bake failed: nothing static to bake.', data: { hint: 'Mark objects static: remove the RigidBody or set its type to fixed.' } };
      }
      return bakeResult(p);
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

describe('bake_lighting', () => {
  it('is a write tool that says when to use it', () => {
    const tool = createTools(ctx).find((t) => t.name === 'bake_lighting');
    expect(tool).toBeDefined();
    expect(tool.annotations).toMatchObject({ readOnlyHint: false, destructiveHint: false });
    expect(tool.inputSchema.required).toEqual([]);
    expect(Object.keys(tool.inputSchema.properties)).toEqual(['quality', 'objects', 'force', 'export']);
    expect(tool.inputSchema.properties.quality.enum).toEqual(['preview', 'final']);
    for (const words of ['compare_views', 'gs_lightmap = "baked"', 'gs_lightmap = False', '.gamestudio/lightmaps', 'preview', 'final']) {
      expect(tool.description).toContain(words);
    }
  });

  it('forwards the arguments to Blender and passes the result on', async () => {
    const r = await callTool(server, 'bake_lighting');
    expect(r.isError).toBe(false);
    expect(bridge.calls.at(-1)).toEqual({ method: 'bake_lighting', params: {} });
    expect(r.data).toMatchObject({ scene: 'main', quality: 'preview', texture_mb: 1, seconds: 4.2, objects: [{ name: 'Ground' }], skipped: [{ name: 'Player' }] });
    expect(r.data.export).toMatchObject({ rev: 5, entities: 2 });
    expect(r.data.export.scene_load).toBeUndefined(); // sent to games, not shown to the agent
    expect(r.data.export.reloaded_games).toBeUndefined(); // no dev server

    await callTool(server, 'bake_lighting', { quality: 'final', objects: ['Ground', 'Wall'], force: true, export: false });
    expect(bridge.calls.at(-1)).toEqual({ method: 'bake_lighting', params: { quality: 'final', objects: ['Ground', 'Wall'], force: true, export: false } });
    const single = await callTool(server, 'bake_lighting', { objects: 'Ground', export: false });
    expect(single.data.export).toBeUndefined();
  });

  it('gives a final bake more time than a preview', () => {
    expect(BAKE_TIMEOUTS_MS.final).toBeGreaterThan(BAKE_TIMEOUTS_MS.preview);
    expect(BAKE_TIMEOUTS_MS.preview).toBeGreaterThanOrEqual(300_000);
  });

  it('validates arguments before calling Blender', async () => {
    const bad = await callTool(server, 'bake_lighting', { quality: 'draft', objects: [], force: 'yes', lights: true });
    expect(bad.isError).toBe(true);
    expect(bad.data.problems).toEqual(expect.arrayContaining([
      'arguments.quality: must be one of "preview", "final"; got "draft"',
      expect.stringMatching(/^arguments\.objects/),
      expect.stringMatching(/^arguments\.force/),
      expect.stringMatching(/lights/),
    ]));
    expect(bridge.calls).toHaveLength(0);
  });

  it("passes Blender's refusal on with its hint", async () => {
    const unknown = await callTool(server, 'bake_lighting', { objects: 'Ghost' });
    expect(unknown.isError).toBe(true);
    expect(unknown.data).toMatchObject({ code: 'blender_error', error: "No object named 'Ghost'.", hint: expect.stringContaining('list_objects') });
    const nothing = await callTool(server, 'bake_lighting', { objects: 'Player' });
    expect(nothing.data).toMatchObject({ code: 'blender_error', error: expect.stringMatching(/nothing static/), hint: expect.stringMatching(/RigidBody/) });
  });

  it('is pre-approved in the starter and the demo, like export_scene', () => {
    for (const rel of ['blender/gamestudio/project_templates/starter/.claude/settings.json', 'examples/demo-game/.claude/settings.json']) {
      const allow = JSON.parse(readFileSync(path.join(REPO, rel), 'utf8')).permissions.allow;
      expect(allow, rel).toContain('mcp__gamestudio__bake_lighting');
      expect(allow, rel).toContain('mcp__gamestudio__export_scene');
    }
  });
});

describe('bake_lighting with a game connected', () => {
  let hub;
  let game;
  const sent = [];

  beforeEach(async () => {
    hub = await createHub({ editorPort: 0, gamePort: 0, token: TOKEN });
    writeFileSync(path.join(project.root, '.gamestudio', 'session.json'),
      JSON.stringify({ pid: process.pid, url: 'http://localhost:5999/', editorPort: hub.editorPort, gamePort: hub.gamePort, token: TOKEN }));
    sent.length = 0;
    await ctx.hub.ensure();
    game = new WebSocket(`ws://127.0.0.1:${hub.gamePort}`);
    await new Promise((resolve) => game.once('open', resolve));
    const reply = (msg) => game.send(JSON.stringify(msg));
    game.on('message', (data) => {
      const msg = JSON.parse(String(data));
      if (msg.t === 'welcome') {
        reply({ t: 'game.status', state: 'playing' });
        reply({ t: 'game.ready', scene: 'main', rev: 3 });
        return;
      }
      sent.push(msg);
      if (msg.t === 'scene.load') reply({ t: 'game.ready', scene: msg.scene, rev: msg.rev });
    });
    game.send(JSON.stringify({ t: 'hello', role: 'game', token: TOKEN, protocol: 1, client: 'test-game' }));
    await ctx.hub.waitFor(() => ctx.hub.gamePeers()[0]?.rev === 3, 2000);
  });
  afterEach(async () => {
    game.close();
    ctx.hub.close();
    await hub.close();
  });

  it('reloads connected games with the new export', async () => {
    const r = await callTool(server, 'bake_lighting');
    expect(r.data.export.reloaded_games).toHaveLength(1);
    await ctx.hub.waitFor(() => ctx.hub.gamePeers()[0]?.rev === 5, 2000);
    expect(sent.find((m) => m.t === 'scene.load')).toMatchObject({ scene: 'main', rev: 5, url: '/__gs/export/main.scene.json' });
  });

  it('leaves games alone without an export', async () => {
    const r = await callTool(server, 'bake_lighting', { export: false });
    expect(r.data.export).toBeUndefined();
    expect(sent.filter((m) => m.t === 'scene.load')).toEqual([]);
  });
});
