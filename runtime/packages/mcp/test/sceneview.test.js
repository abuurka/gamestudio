// game_screenshot mode "scene": the pose maths (Blender coordinates → the game's view.camera
// matrices), and the shot against a real hub, a fake game peer and a stand-in for its page.
import { mkdirSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import WebSocket from 'ws';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createHub } from '../../devserver/src/hub.js';
import { Context } from '../src/context.js';
import { createGameStudioServer, createTools } from '../src/index.js';
import {
  SCENE_TIMEOUTS, checkView, eulerPose, lookAtPose, matrixPose, orthographic, perspective, posedCamera, project as projectPoint, toGame, verticalFov, viewMatrix,
} from '../src/sceneview.js';
import { TOKEN, callTool, makeProject, startFakeBridge } from './helpers.js';

const close = (a, b, eps = 1e-6) => a.every((v, i) => Math.abs(v - b[i]) < eps);
const expectClose = (a, b, eps = 1e-6) => expect(close(a, b, eps), `${JSON.stringify(a)} ≈ ${JSON.stringify(b)}`).toBe(true);

/** Row-major 4×4 helpers for the contract's own formula. */
const mul = (a, b) => a.map((row, i) => row.map((_, j) => a[i].reduce((s, _v, k) => s + a[i][k] * b[k][j], 0)));
const transpose = (a) => a[0].map((_, j) => a.map((row) => row[j]));
const columnMajor = (a) => [0, 1, 2, 3].flatMap((j) => [0, 1, 2, 3].map((i) => a[i][j]));

describe('scene view pose maths', () => {
  it('maps Blender points to where a camera at {from, at} sees them', () => {
    const camera = posedCamera([0, -10, 2], [0, 0, 2], 35, [320, 180]);
    expect(camera.ortho).toBe(false);
    expectClose(projectPoint(camera, toGame([0, 0, 2])).view, [0, 0, -10]); // straight ahead, 10 m
    expectClose(projectPoint(camera, toGame([1, 0, 2])).view, [1, 0, -10]); // Blender +X is to the right
    expectClose(projectPoint(camera, toGame([0, 0, 3])).view, [0, 1, -10]); // Blender +Z is up
    // A 35 mm lens on a 36 mm sensor spans the wider side: a point on its edge lands on the frame's edge.
    const edge = projectPoint(camera, toGame([(10 * 18) / 35, 0, 2])).ndc;
    expectClose([edge[0], edge[1]], [1, 0]);
    const top = projectPoint(camera, toGame([0, 0, 2 + ((10 * 18) / 35) * (180 / 320)])).ndc;
    expectClose([top[0], top[1]], [0, 1]);
    // compare_views' clip range: 1% and 20× the distance (at least 1000 m).
    expect([camera.near, camera.far]).toEqual([0.1, 1000]);
  });

  it('matches the contract formula game_view = blender_view @ R^T', () => {
    const from = [7, -6, 4];
    const at = [0.5, 1, 0.8];
    const f = [at[0] - from[0], at[1] - from[1], at[2] - from[2]];
    const n = Math.hypot(...f);
    const fw = f.map((v) => v / n);
    const right = [fw[1], -fw[0], 0].map((v) => v / Math.hypot(fw[0], fw[1])); // fw × Z
    const up = [right[1] * fw[2] - right[2] * fw[1], right[2] * fw[0] - right[0] * fw[2], right[0] * fw[1] - right[1] * fw[0]];
    const back = fw.map((v) => -v);
    // Blender's view matrix: the inverse of the camera's world matrix (rows: its axes).
    const dot = (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
    const blenderView = [[...right, -dot(right, from)], [...up, -dot(up, from)], [...back, -dot(back, from)], [0, 0, 0, 1]];
    const R = [[1, 0, 0, 0], [0, 0, 1, 0], [0, -1, 0, 0], [0, 0, 0, 1]]; // Blender world → game world
    expectClose(viewMatrix(lookAtPose(from, at)), columnMajor(mul(blenderView, transpose(R))));
  });

  it('reads a Blender rotation and a game camera matrix to the same pose', () => {
    // Blender's camera looks down its −Z: rotated 90° about X it looks along +Y.
    expectClose(viewMatrix(eulerPose([0, -10, 2], [90, 0, 0])), viewMatrix(lookAtPose([0, -10, 2], [0, 0, 2])));
    // ... and then 90° about Z, along −X.
    expectClose(viewMatrix(eulerPose([10, 0, 0], [90, 0, 90])), viewMatrix(lookAtPose([10, 0, 0], [0, 0, 0])));
    // A world matrix (three.js elements: the axes and the position as columns), scale dropped.
    const pose = lookAtPose([3, -4, 5], [0, 0, 1]);
    const world = [...pose.right.map((v) => v * 2), 0, ...pose.up.map((v) => v * 2), 0, ...pose.back.map((v) => v * 2), 0, ...pose.position, 1];
    expectClose(viewMatrix(matrixPose(world)), viewMatrix(pose));
  });

  it('builds perspective and orthographic projections like three.js', () => {
    // Portrait: the sensor spans the height.
    expect(verticalFov(36, [200, 400])).toBeCloseTo(2 * Math.atan(0.5), 9);
    expect(verticalFov(36, [400, 200])).toBeCloseTo(2 * Math.atan(0.25), 9);
    const p = perspective(Math.PI / 2, 2, 1, 101);
    expect(p[0]).toBeCloseTo(0.5, 9);
    expect(p[5]).toBeCloseTo(1, 9);
    expect(p[11]).toBe(-1);
    const camera = { view: viewMatrix({ position: [0, 0, 0], right: [1, 0, 0], up: [0, 1, 0], back: [0, 0, 1] }), projection: orthographic(10, [400, 200], 0.1, 100) };
    expectClose(projectPoint(camera, [5, 2.5, -1]).ndc.slice(0, 2), [1, 1]); // scale spans the wider side
    expectClose([projectPoint(camera, [0, 0, -0.1]).ndc[2], projectPoint(camera, [0, 0, -100]).ndc[2]], [-1, 1]);
  });

  it('checks views', () => {
    expect(() => checkView(undefined)).toThrow(/needs a view/);
    expect(() => checkView({ camera: 'Cam', lens: 50 })).toThrow(/camera goes alone/);
    expect(() => checkView({ from: [0, 0, 1] })).toThrow(/from and at/);
    expect(() => checkView({ from: [1, 2, 3], at: [1, 2, 3] })).toThrow(/same point/);
    expect(checkView({ from: [0, -5, 1], at: [0, 0, 1], lens: 24 })).toEqual({ from: [0, -5, 1], at: [0, 0, 1], lens: 24 });
  });
});

// --- The tool ---------------------------------------------------------------------------------------

class FakeElement {
  constructor(name) {
    this.name = name;
    this.style = { visibility: '' };
  }
  contains(other) {
    return other === this;
  }
}

/**
 * The game's page and window.__gs (shared with the fake game peer below, which plays the engine's
 * link handlers: view.mode, view.camera, scene.load, play.start).
 */
function fakePage() {
  const canvas = new FakeElement('canvas');
  const hud = new FakeElement('hud');
  const editor = { isCamera: true, name: 'gs_editor_camera', near: 0, far: 0, matrixWorldInverse: { elements: Array(16).fill(0) }, source: { elements: Array(16).fill(0) }, projectionMatrix: { elements: [] }, updateMatrixWorld() {} };
  const gameCamera = { isCamera: true, matrixWorldInverse: { elements: Array(16).fill(0) }, updateMatrixWorld() {} };
  /** Camera entities in the game, by gs id. */
  const cameras = new Map();
  const game = {
    view: 'game',
    animate: true,
    state: 'playing',
    edited: false,
    scene: { name: 'main', rev: 3 },
    canvas,
    three: { camera: gameCamera },
    get viewCamera() {
      return this.view === 'scene' && game.edited ? editor : this.three.camera;
    },
    idle: async () => {},
    shaders: { loading: new Set(), textures: new Map([['srgb|/a.png', { texture: {}, promise: Promise.resolve({}) }]]) },
  };
  const entity = (id) => {
    const camera = cameras.get(id);
    if (!camera) return undefined;
    const object = { isCamera: true, isPerspectiveCamera: true, fov: 40, near: 0.2, far: 500, matrixWorld: { elements: camera.elements }, updateWorldMatrix() {} };
    return { id, name: camera.name, object3d: { traverse: (fn) => fn(object) } };
  };
  globalThis.window = {
    __gs: {
      game,
      world: { get: (id) => entity(id), findByName: (name) => entity([...cameras].find(([, c]) => c.name === name)?.[0]) },
      get view() {
        return game.view;
      },
      get state() {
        return game.state;
      },
    },
  };
  globalThis.document = { querySelector: () => canvas, body: { querySelectorAll: () => [canvas, hud] } };
  globalThis.HTMLElement = FakeElement;
  globalThis.requestAnimationFrame = (fn) => setTimeout(() => fn(performance.now()), 1);
  const shots = [];
  const page = {
    vp: { width: 480, height: 270 },
    viewports: [],
    viewport() {
      return { ...this.vp };
    },
    async setViewport(v) {
      this.vp = { ...v };
      this.viewports.push([v.width, v.height]);
    },
    evaluate: async (fn, ...args) => fn(...args),
    async screenshot() {
      shots.push({ size: [this.vp.width, this.vp.height], view: game.view, hud: hud.style.visibility, camera: [...editor.matrixWorldInverse.elements] });
      return Buffer.from('PNG');
    },
    isClosed: () => false,
  };
  return { page, game, editor, hud, cameras, shots };
}

describe('game_screenshot mode "scene"', () => {
  let project;
  let bridge;
  let ctx;
  let server;
  let hub;
  let peer;
  let fake;
  const sent = [];
  const timeouts = { ...SCENE_TIMEOUTS };

  beforeEach(async () => {
    project = makeProject();
    bridge = await startFakeBridge({
      export_scene: () => ({ scene: 'main', rev: 4, entities: 3, tier: 'layout', scene_load: { t: 'scene.load', scene: 'main', url: '/__gs/export/main.scene.json', rev: 4 } }),
    });
    bridge.writeDiscovery(project.root);
    ctx = new Context({ root: project.root, env: { HOME: project.root, APPDATA: path.join(project.root, 'none'), BLENDER_USER_RESOURCES: path.join(project.root, 'none') } });
    ({ server } = createGameStudioServer({ root: project.root, context: ctx }));
    hub = await createHub({ editorPort: 0, gamePort: 0, token: TOKEN });
    writeFileSync(path.join(project.root, '.gamestudio', 'session.json'),
      JSON.stringify({ pid: process.pid, url: 'http://localhost:5999/', editorPort: hub.editorPort, gamePort: hub.gamePort, token: TOKEN }));
    sent.length = 0;
    fake = fakePage();
    ctx.browser.ensureGame = async () => ({ page: fake.page, tag: 'test-game', url: 'http://localhost:5999/', logs: [], size: [480, 270], opened: false });
    Object.assign(SCENE_TIMEOUTS, { view: 300, camera: 300, settle: 1000 });
    await ctx.hub.ensure();
    peer = new WebSocket(`ws://127.0.0.1:${hub.gamePort}`);
    await new Promise((resolve) => peer.once('open', resolve));
    const reply = (msg) => peer.send(JSON.stringify(msg));
    const { game, editor } = fake;
    peer.on('message', (data) => {
      const msg = JSON.parse(String(data));
      if (msg.t === 'welcome') {
        reply({ t: 'game.status', state: game.state, view: game.view });
        reply({ t: 'game.ready', scene: 'main', rev: 3 });
        return;
      }
      sent.push(msg);
      if (fake.ignoreView && msg.t.startsWith('view.')) return; // an engine without scene mode
      if (msg.t === 'view.mode') {
        if (typeof msg.animate === 'boolean') game.animate = msg.animate;
        game.view = msg.mode;
        if (msg.mode === 'scene') game.state = 'stopped';
        reply({ t: 'game.status', state: game.state, view: game.view });
      } else if (msg.t === 'view.camera') {
        editor.matrixWorldInverse.elements = msg.view;
        editor.source.elements = msg.projection;
        Object.assign(editor, { near: msg.near, far: msg.far });
        game.edited = true;
      } else if (msg.t === 'play.start') {
        game.view = 'game';
        game.state = 'playing';
        reply({ t: 'game.status', state: game.state, view: game.view });
      } else if (msg.t === 'scene.load') {
        game.scene = { name: msg.scene, rev: msg.rev };
        reply({ t: 'game.ready', scene: msg.scene, rev: msg.rev });
      }
    });
    reply({ t: 'hello', role: 'game', token: TOKEN, protocol: 1, client: 'test-game' });
    await ctx.hub.waitFor(() => ctx.hub.gamePeers()[0]?.rev === 3, 2000);
  });
  afterEach(async () => {
    Object.assign(SCENE_TIMEOUTS, timeouts);
    peer.close();
    await ctx.shutdown();
    await hub.close();
    await bridge.close();
    project.cleanup();
    for (const key of ['window', 'document', 'HTMLElement', 'requestAnimationFrame']) delete globalThis[key];
  });

  const ofView = () => sent.filter((m) => ['scene.load', 'view.mode', 'view.camera', 'play.start', 'play.pause'].includes(m.t));

  it('has the arguments, and refuses scene arguments without mode "scene"', async () => {
    const tool = createTools(ctx).find((t) => t.name === 'game_screenshot');
    expect(tool.inputSchema.properties.mode.enum).toEqual(['game', 'scene']);
    expect(Object.keys(tool.inputSchema.properties.view.properties)).toEqual(['from', 'at', 'lens', 'camera']);
    expect(tool.description).toContain('{"mode": "scene", "view": {"from": [8, -8, 5], "at": [0, 0, 1]}}');
    const stray = await callTool(server, 'game_screenshot', { view: { camera: 'Cam' } });
    expect(stray.isError).toBe(true);
    expect(stray.data.error).toMatch(/view goes with mode "scene"/);
    const none = await callTool(server, 'game_screenshot', { mode: 'scene' });
    expect(none.data.error).toMatch(/needs a view/);
    const bad = await callTool(server, 'game_screenshot', { mode: 'scene', view: { from: [0, 0, 1], at: [1, 1, 1], fov: 50 } });
    expect(bad.data.problems[0]).toMatch(/unknown argument 'fov'/);
    expect(ofView()).toEqual([]);
  });

  it('poses a camera from Blender coordinates, shoots the level without the HUD and puts the game back', async () => {
    const r = await callTool(server, 'game_screenshot', { mode: 'scene', view: { from: [0, -10, 2], at: [0, 0, 2] }, size: [320, 180], save: true });
    expect(r.isError, JSON.stringify(r.data)).toBe(false);
    expect(r.images).toEqual([{ type: 'image', data: Buffer.from('PNG').toString('base64'), mimeType: 'image/png' }]);
    expect(r.data).toMatchObject({
      mode: 'scene',
      view: { from: [0, -10, 2], at: [0, 0, 2], lens: 35, lens_from: 'default' },
      size: [320, 180],
      export: { scene: 'main', rev: 4, tier: 'layout' },
      game: { scene: 'main', rev: 4 },
      restored: { view: 'game', state: 'playing', restarted: true },
    });
    expect(r.data.warnings).toBeUndefined();
    expect(r.data.saved).toMatch(/screenshots[\\/].*scene\.png$/);

    // The export reached the game first, then the Scene view and its camera; afterwards the game is put back.
    expect(ofView().map((m) => [m.t, m.mode ?? m.rev ?? '', m.animate ?? ''])).toEqual([
      ['scene.load', 4, ''],
      ['view.mode', 'scene', false],
      ['view.camera', '', ''],
      ['view.mode', 'game', true],
      ['play.start', '', ''],
    ]);
    const camera = sent.find((m) => m.t === 'view.camera');
    expectClose(projectPoint(camera, toGame([0, 0, 2])).view, [0, 0, -10]);
    expectClose(projectPoint(camera, toGame([(10 * 18) / 35, 0, 2])).ndc.slice(0, 2), [1, 0]); // drawn at 320×180

    // The shot: scene mode, at its size, overlays hidden, through that camera. Then all back.
    expect(fake.shots).toEqual([{ size: [320, 180], view: 'scene', hud: 'hidden', camera: camera.view }]);
    expect(fake.page.viewports).toEqual([[320, 180], [480, 270]]);
    expect(fake.hud.style.visibility).toBe('');
    expect([fake.game.view, fake.game.state, fake.game.animate]).toEqual(['game', 'playing', true]);
  });

  it('looks through a Blender camera: its lens from Blender, its pose from the export the game shows', async () => {
    bridge.handlers.get_object = (p) => (p.name === 'Cube'
      ? { name: 'Cube', type: 'MESH', id: 'gs_cube' }
      : { name: 'Cam', type: 'CAMERA', id: 'gs_cam', camera: { type: 'PERSP', lens: 50, is_scene_camera: false }, world_location: [0, -10, 2], rotation_deg: [90, 0, 0], parent: null });
    // In the game the camera stands at (3, 2, 10) facing −Z (where the export put it).
    fake.cameras.set('gs_cam', { name: 'Cam', elements: [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 3, 2, 10, 1] });
    const r = await callTool(server, 'game_screenshot', { mode: 'scene', view: { camera: 'Cam' }, export: false });
    expect(r.isError, JSON.stringify(r.data)).toBe(false);
    expect(r.data.view).toEqual({ camera: 'Cam', id: 'gs_cam', pose_from: 'game export', lens: 50 });
    expect(r.data.export).toBeUndefined();
    const camera = sent.find((m) => m.t === 'view.camera');
    expectClose(camera.view, [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, -3, -2, -10, 1]);
    expect(camera.projection[5]).toBeCloseTo(1 / Math.tan(verticalFov(50, [480, 270]) / 2), 9);
    expect([camera.near, camera.far]).toEqual([0.2, 500]);
    expect(sent.some((m) => m.t === 'scene.load')).toBe(false); // export: false shows what the game has

    // Not in the game: placed by its Blender transform.
    fake.cameras.clear();
    sent.length = 0;
    const blender = await callTool(server, 'game_screenshot', { mode: 'scene', view: { camera: 'Cam' }, export: false });
    expect(blender.data.view.pose_from).toBe('blender');
    expectClose(sent.find((m) => m.t === 'view.camera').view, viewMatrix(lookAtPose([0, -10, 2], [0, 0, 2])), 1e-9);

    // Not a camera: refused, and the game is put back all the same.
    sent.length = 0;
    const mesh = await callTool(server, 'game_screenshot', { mode: 'scene', view: { camera: 'Cube' }, export: false });
    expect(mesh.isError).toBe(true);
    expect(mesh.data.error).toMatch(/'Cube' is a MESH object, not a camera/);
    await vi.waitFor(() => expect(fake.game.state).toBe('playing'));
    expect(ofView().map((m) => [m.t, m.mode ?? ''])).toEqual([['view.mode', 'scene'], ['view.mode', 'game'], ['play.start', '']]);
    expect(fake.game.view).toBe('game');
  });

  it('takes the scene camera\'s lens by default, like compare_views', async () => {
    mkdirSync(path.join(project.root, '.gamestudio', 'export'), { recursive: true });
    writeFileSync(path.join(project.root, '.gamestudio', 'export', 'main.scene.json'), JSON.stringify({ scene: 'main', settings: { activeCamera: 'gs_cam' } }));
    bridge.handlers.get_object = (p) => ({ name: 'Camera', type: 'CAMERA', id: p.name, camera: { type: 'PERSP', lens: 24 } });
    const r = await callTool(server, 'game_screenshot', { mode: 'scene', view: { from: [5, -5, 3], at: [0, 0, 0] } });
    expect(r.data.view).toMatchObject({ lens: 24, lens_from: 'scene camera' });
    expect(bridge.calls.find((c) => c.method === 'get_object').params).toEqual({ name: 'gs_cam' });
    const lens = await callTool(server, 'game_screenshot', { mode: 'scene', view: { from: [5, -5, 3], at: [0, 0, 0], lens: 85 } });
    expect(lens.data.view).toEqual({ from: [5, -5, 3], at: [0, 0, 0], lens: 85 });
  });

  it('keeps a game that is already in scene mode there, with the editor\'s camera given back', async () => {
    fake.game.view = 'scene';
    fake.game.state = 'stopped';
    fake.game.edited = true;
    const theirs = [...viewMatrix(lookAtPose([1, -1, 1], [0, 0, 0]))];
    fake.editor.matrixWorldInverse.elements = theirs;
    fake.editor.source.elements = perspective(1, 1.5, 0.1, 100);
    const r = await callTool(server, 'game_screenshot', { mode: 'scene', view: { from: [0, -10, 2], at: [0, 0, 2] }, export: false });
    expect(r.isError, JSON.stringify(r.data)).toBe(false);
    expect(r.data.restored).toEqual({ view: 'scene', state: 'stopped' });
    const cameras = ofView();
    expect(cameras.map((m) => m.t)).toEqual(['view.camera', 'view.camera']); // ours, then theirs
    expectClose(cameras[1].view, theirs); // (JSON makes -0 into 0)
    expect(fake.game.view).toBe('scene');
  });

  it('says when the engine has no scene mode, and leaves the game running', async () => {
    fake.ignoreView = true;
    delete globalThis.window.__gs.game.view;
    Object.defineProperty(globalThis.window.__gs, 'view', { get: () => undefined });
    const r = await callTool(server, 'game_screenshot', { mode: 'scene', view: { from: [0, -10, 2], at: [0, 0, 2] }, export: false });
    expect(r.isError).toBe(true);
    expect(r.data.code).toBe('scene_mode_unsupported');
    expect(ofView().map((m) => m.t)).toEqual(['view.mode']);
    expect(fake.game.state).toBe('playing');
    expect(fake.page.viewports).toEqual([]);
  });
});
