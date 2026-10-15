// playtest on game time: script checks, the tool schema, and how the browser side drives the
// engine's test clock (against a stand-in page and window.__gs.test).
import path from 'node:path';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { GAME_SIZE, GameBrowser, MAX_SAMPLE_POINTS, MAX_SAMPLES_PER_ENTITY, PLAYTEST_SIZE, decimateSamples, dropStill, placement, planPlaytest } from '../src/browser.js';
import { Context } from '../src/context.js';
import { createGameStudioServer, createTools } from '../src/index.js';
import { callTool, makeProject } from './helpers.js';

describe('planPlaytest', () => {
  it('times everything in game seconds and fixed steps', () => {
    const plan = planPlaytest({ inputs: [{ at: 0, key: 'KeyW', hold: 2 }, { at: 1, key: 'Space' }] });
    expect(plan.duration).toBe(2.5); // the last release + 0.5
    expect(plan.steps).toBe(150);
    expect(plan.shots).toEqual([0, 2.5]);
    expect(plan.shotSteps).toEqual([1, 150]); // a shot at 0 comes after the first step
    expect(planPlaytest({ duration: 3, screenshots: 4 }).shotSteps).toEqual([1, 60, 120, 180]);
    expect(planPlaytest({ duration: 3, screenshots: [2, 0.5] }).shotSteps).toEqual([30, 120]);
    expect(planPlaytest({}).duration).toBe(0.5);
  });

  it('catches times written in milliseconds and other mistakes', () => {
    expect(() => planPlaytest({ inputs: [{ at: 0, key: 'KeyW', hold: 3000 }] })).toThrow(/3000 s/);
    try {
      planPlaytest({ inputs: [{ at: 1500, key: 'KeyW' }] });
    } catch (error) {
      expect(error.hint).toMatch(/game seconds.*Did you mean 1\.5\?/);
    }
    expect(() => planPlaytest({ inputs: [{ at: 4, key: 'KeyW' }], duration: 2 })).toThrow(/starts at 4 s/);
    expect(() => planPlaytest({ duration: 2, screenshots: [3] })).toThrow(/after the end/);
    expect(() => planPlaytest({ inputs: [{ at: 0 }] })).toThrow(/key.*mouse/);
    expect(() => planPlaytest({ start: { name: 'Player' } })).toThrow(/start\[0\] needs a position/);
    expect(planPlaytest({ inputs: [{ at: 0, key: 'KeyW', hold: 10 }], duration: 5 }).duration).toBe(5); // held past the end: let go
  });

  it('converts start positions from Blender space', () => {
    expect(placement({ position: [1, 2, 3], yaw_deg: 90 })).toEqual({ position: [1, 2, 3], yaw: 90 });
    expect(placement({ position: [1, 2, 3], space: 'blender' })).toEqual({ position: [1, 3, -2] });
  });
});

describe('playtest tool schema', () => {
  let project;
  let ctx;
  let server;
  beforeEach(() => {
    project = makeProject();
    ctx = new Context({ root: project.root, env: { HOME: project.root, APPDATA: path.join(project.root, 'none'), BLENDER_USER_RESOURCES: path.join(project.root, 'none') } });
    ({ server } = createGameStudioServer({ root: project.root, context: ctx }));
  });
  afterEach(async () => {
    await ctx.shutdown();
    project.cleanup();
  });

  it('describes game-time inputs with exact JSON', () => {
    const tool = createTools(ctx).find((t) => t.name === 'playtest');
    expect(tool.description).toContain('{"at": 0, "key": "KeyW", "hold": 2}');
    expect(tool.description).toContain('game seconds');
    expect(Object.keys(tool.inputSchema.properties)).toEqual(expect.arrayContaining(['inputs', 'duration', 'screenshots', 'track', 'start', 'size', 'realtime', 'seed', 'sample_every', 'track_max']));
    expect(tool.description).toContain('"Boulder*"');
    expect(tool.inputSchema.properties.duration_ms).toBeUndefined();
    // set seeds the run's ctx.store and ctx.save; save stays the boolean that writes the screenshots.
    expect(tool.inputSchema.properties.save).toMatchObject({ type: 'boolean' });
    expect(tool.inputSchema.properties.set).toMatchObject({ type: 'object', additionalProperties: false });
    expect(Object.keys(tool.inputSchema.properties.set.properties)).toEqual(['store', 'save']);
    for (const words of ['final.save', '{"set": {"save": {"bestTime": 60}}}', 'a pattern gives {pattern, matched, alive_at_end', 'instances[i].name']) {
      expect(tool.description).toContain(words);
    }
  });

  it('refuses set values that are not objects before starting anything', async () => {
    const r = await callTool(server, 'playtest', { set: { save: 3 } });
    expect(r.isError).toBe(true);
    expect(r.data.problems[0]).toMatch(/set\.save: expected object/);
    const stray = await callTool(server, 'playtest', { set: { saves: {} } });
    expect(stray.data.problems[0]).toMatch(/unknown argument 'saves'/);
  });

  it('lets play and game_screenshot size the headless game', async () => {
    const tools = createTools(ctx);
    for (const name of ['play', 'game_screenshot']) {
      const tool = tools.find((t) => t.name === name);
      expect(tool.inputSchema.properties.size).toMatchObject({ type: 'array', minItems: 2, maxItems: 2 });
      expect(tool.description).toContain('[480, 270]');
    }
    const bad = await callTool(server, 'play', { size: [480] });
    expect(bad.isError).toBe(true);
    expect(bad.data.problems[0]).toMatch(/size/);
  });

  it('rejects old and malformed arguments before starting anything', async () => {
    const old = await callTool(server, 'playtest', { inputs: [{ at: 0, key: 'KeyW', hold: 2000 }], duration_ms: 2500 });
    expect(old.isError).toBe(true);
    expect(old.data.problems[0]).toMatch(/unknown argument 'duration_ms'.*duration/);
    const ms = await callTool(server, 'playtest', { inputs: [{ at: 0, key: 'KeyW', hold: 2000 }] });
    expect(ms.isError).toBe(true);
    expect(ms.data.hint).toMatch(/game seconds/);
    const start = await callTool(server, 'playtest', { start: { position: [1, 2] } });
    expect(start.data.problems).toEqual(['arguments.start.position: needs at least 3 item(s)']);
    const many = await callTool(server, 'playtest', { start: [{ position: [1, 2, 3], space: 'world' }] });
    expect(many.data.problems[0]).toMatch(/start\[0\]\.space/);
  });
});

/** A stand-in for the game page: evaluate() runs the function here against a fake window.__gs. */
function fakeGame() {
  const calls = [];
  let step = 0;
  const test = {
    begin: (options) => calls.push(['begin', options]),
    place: (name, pose) => {
      calls.push(['place', name, pose]);
      if (name === 'Ghost') throw new Error('No entity named "Ghost"');
      return { name: name ?? 'Player', position: pose.position, rotation: [0, 0, 0, 1] };
    },
    track: (names) => calls.push(['track', names]),
    schedule: (inputs) => calls.push(['schedule', inputs]),
    advance: (k) => {
      calls.push(['advance', k]);
      step += k;
      return { step, loads: 0, idleSteps: 0, simulateMs: 12 };
    },
    status: () => ({ step, loads: 0, idleSteps: 0, simulateMs: 12 }),
    render: () => {
      calls.push(['render', step]);
      return 3;
    },
    positions: (names) => Object.fromEntries(names.map((n) => [n, [0, 0, -step / 60]])),
    report: () => [
      { name: 'Player', found: true, start: [0, 0, 0], final: [0, 0, -1], min: [0, 0, -1], max: [0, 1.2, 0], path: 1.3, destroyedAt: null, components: { GemHunt: { gems: 1 } }, changes: { 'GemHunt.gems': { start: 0, final: 1, min: 0, max: 1, count: 1, first: 0.5, last: 0.5 } } },
      { name: 'Gem_1', found: true, start: [0, 1, -1], final: [0, 1, -1], min: [0, 1, -1], max: [0, 1, -1], path: 0, destroyedAt: 0.5, components: null, changes: {} },
    ],
  };
  globalThis.window = {
    __gs: { test, state: 'playing', world: { query: () => [{ name: 'Player' }] }, game: { scene: { name: 'main' }, describe: () => [{ name: 'Player' }] } },
    __gsTestLogs: [{ level: 'log', msg: 'Collected Gem_1: 1 / 5', t: 0.5 }],
  };
  globalThis.document = { body: { innerText: 'Gems 1 / 5' } };
  const page = {
    evaluate: async (fn, ...args) => fn(...args),
    screenshot: async () => Buffer.from([step]),
    waitForFunction: async () => {},
    goto: async () => {},
    close: async () => {},
    isClosed: () => false,
  };
  return { calls, page };
}

class StubBrowser extends GameBrowser {
  constructor(page) {
    super();
    this.page = page;
    this.opened = [];
  }
  async openGame(url, options) {
    this.opened.push(options);
    return { page: this.page, tag: 't', logs: [], size: options.size, loadMs: 5 };
  }
}

describe('playtest on the engine test clock', () => {
  afterEach(() => {
    delete globalThis.window;
    delete globalThis.document;
  });

  it('holds the clock, steps in chunks, renders only for screenshots and reports game time', async () => {
    const { calls, page } = fakeGame();
    const browser = new StubBrowser(page);
    const progress = [];
    const inputs = [{ at: 0, key: 'KeyW', hold: 14 }];
    const result = await browser.playtest('http://localhost:5173/', { inputs, screenshots: [0, 7], start: { position: [1, 0, 2] }, track: ['Player', 'Gem_1'] }, undefined, (done, total) => progress.push([done, total]));
    expect(browser.opened).toEqual([{ size: PLAYTEST_SIZE, hold: true }]);
    expect(calls.filter(([c]) => c !== 'render').map(([c, ...a]) => [c, ...a])).toEqual([
      ['begin', { seed: 1 }],
      ['place', null, { position: [1, 0, 2] }],
      ['track', ['Player', 'Gem_1']],
      ['schedule', inputs],
      ['advance', 1],
      ['advance', 419],
      ['advance', 450],
    ]);
    expect(calls.filter(([c]) => c === 'render')).toEqual([['render', 1], ['render', 420]]);
    expect(progress.at(-1)).toEqual([870, 870]);
    expect(result).toMatchObject({ mode: 'game time', game_time_s: 14.5, steps: 870, inputs: 2, size: [480, 270], warnings: [], hud: 'Gems 1 / 5' });
    expect(result.start).toEqual([{ name: 'Player', position: [1, 0, 2], rotation: [0, 0, 0, 1] }]);
    expect(result.screenshots.map((s) => [s.time_s, s.positions.Player[2]])).toEqual([[0.017, -1 / 60], [7, -7]]);
    expect(result.tracked[0]).toMatchObject({ name: 'Player', path_m: 1.3, moved_m: 1, changes: { 'GemHunt.gems': { start: 0, final: 1, count: 1, first_s: 0.5, last_s: 0.5 } } });
    expect(result.tracked[1]).toMatchObject({ name: 'Gem_1', destroyed_at_s: 0.5 });
    expect(result.logs).toEqual([{ level: 'log', msg: 'Collected Gem_1: 1 / 5', t: 0.5 }]);
    expect(result.final.entities).toBeUndefined();
  });

  it('reports the level, the level changes the game made and the open menu', async () => {
    const { page } = fakeGame();
    const test = globalThis.window.__gs.test;
    const advance = test.advance;
    const level = { scene: { name: 'level2', rev: 7 }, sceneChanges: 1, screen: 'pause' };
    test.advance = (k) => ({ ...advance(k), ...level });
    // Blender UI canvases (docs/GAME-UI.md §5.7) add open, texts and hidden to the menu state.
    const canvases = { open: ['HUD', 'pause'], texts: { 'HUD/Score': 'Score 120', 'pause/Title': 'Paused' }, hidden: ['HUD/Heart3'] };
    test.state = () => ({ ui: { screen: 'pause', focus: 'Resume', buttons: [{ label: 'Resume', name: 'ResumeButton', canvas: 'pause', disabled: false, x: 240, y: 120, width: 160, height: 36 }], ...canvases }, vfx: { emitters: 2, particles: 37 } });
    const result = await new StubBrowser(page).playtest('http://x/', { duration: 1, screenshots: 0 });
    expect(result).toMatchObject({ scene: 'level2', rev: 7, scene_changes: 1, screen: 'pause' });
    expect(result.final.ui.buttons[0]).toMatchObject({ label: 'Resume', name: 'ResumeButton', canvas: 'pause', x: 240, y: 120 });
    expect(result.final.ui).toMatchObject(canvases);
    expect(result.final.vfx).toEqual({ emitters: 2, particles: 37 });
    expect(result.warnings).toEqual([]); // a level change the game asked for repeats with the run: no warning
    test.advance = (k) => ({ ...advance(k), ...level, loads: 1 });
    const reloaded = await new StubBrowser(page).playtest('http://x/', { duration: 1, screenshots: 0 });
    expect(reloaded.warnings[0]).toMatch(/reloaded 1 time\(s\).*not reproducible/);
  });

  it('starts the run with set values and reports what its ctx.save holds at the end', async () => {
    const { calls, page } = fakeGame();
    const test = globalThis.window.__gs.test;
    // An engine whose test.begin takes them: game.test.begin({ seed, store, save }) and game.test.saved(), asked
    // for the engine's own gs. keys too (the UI language and the volumes a settings screen saves).
    const asked = [];
    test.saved = (options) => {
      asked.push(options);
      return { bestTime: 42.5, ...(options?.internal && { 'gs.audio': { music: 0.4 }, 'gs.locale': 'fr' }), subtitles: true };
    };
    const set = { store: { lives: 1 }, save: { bestTime: 60, subtitles: true } };
    const result = await new StubBrowser(page).playtest('http://x/', { duration: 1, screenshots: 0, set });
    expect(calls[0]).toEqual(['begin', { seed: 1, store: { lives: 1 }, save: { bestTime: 60, subtitles: true } }]);
    expect(asked).toEqual([{ internal: true }]);
    expect(result.final.save).toEqual({ bestTime: 42.5, 'gs.audio': { music: 0.4 }, 'gs.locale': 'fr', subtitles: true });
    expect(result.warnings).toEqual([]);
  });

  it('sets the values itself on an engine whose test.begin does not take them', async () => {
    const { calls, page } = fakeGame();
    const saved = new Map([['gs.audio', '{}']]);
    const game = globalThis.window.__gs.game;
    game.store = {};
    game.save = { set: (k, v) => saved.set(k, v), get: (k) => saved.get(k), keys: () => [...saved.keys()] };
    const result = await new StubBrowser(page).playtest('http://x/', { duration: 1, screenshots: 0, set: { store: { lives: 2 }, save: { best: 9 } } });
    expect(calls[0]).toEqual(['begin', { seed: 1 }]);
    expect(game.store).toEqual({ lives: 2 });
    expect(result.final.save, 'the engine\'s own gs. keys too, sorted').toEqual({ best: 9, 'gs.audio': '{}' });
    expect(Object.keys(result.final.save)).toEqual(['best', 'gs.audio']);
    // Nothing to set it in: a warning, and the run goes on.
    delete game.store;
    delete game.save;
    const old = await new StubBrowser(page).playtest('http://x/', { duration: 1, screenshots: 0, set: { store: { lives: 2 } } });
    expect(old.warnings).toEqual([expect.stringMatching(/cannot start a test with ctx\.store values/)]);
    expect(old.final.save).toBeUndefined();
  });

  it('says what drew the run: the WebGL renderer and whether it is a GPU', async () => {
    const { page } = fakeGame();
    const browser = new StubBrowser(page);
    const plain = await browser.playtest('http://x/', { duration: 1, screenshots: 0 });
    expect(plain).not.toHaveProperty('renderer'); // a browser that never started (the stub) has none
    const renderer = 'ANGLE (NVIDIA, NVIDIA GeForce RTX 4070 (0x00002786) Direct3D11 vs_5_0 ps_5_0, D3D11)';
    browser.rendering = { mode: 'auto', renderer, gpu: true };
    const result = await browser.playtest('http://x/', { duration: 1, screenshots: 0 });
    expect(result).toMatchObject({ load_ms: 5, renderer, gpu: true, size: PLAYTEST_SIZE });
  });

  it('follows spawned copies by pattern and samples positions without images', async () => {
    // A world where the player walks -z at 1 m/s and a "Boulder" spawns every second and rolls
    // +x at 2 m/s until it is destroyed 1.5 s later.
    const entities = [];
    const listeners = [];
    const entity = (id, name, pos) => ({
      id, name, alive: true, pos,
      object3d: { updateWorldMatrix() {}, get matrixWorld() { return { elements: [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, ...this.owner.pos, 1] }; } },
    });
    const add = (e) => {
      e.object3d.owner = e;
      entities.push(e);
      for (const l of listeners) l({ type: 'spawn', entity: e });
      return e;
    };
    const player = add(entity('gs_player', 'Player', [0, 0, 0]));
    let step = 0;
    let next = 1;
    const born = new Map();
    const world = {
      onChange: (l) => listeners.push(l),
      all: () => entities.filter((e) => e.alive),
      get: (id) => entities.find((e) => e.alive && e.id === id),
      findByName: (name) => entities.find((e) => e.alive && e.name === name),
      query: () => [player],
    };
    const test = {
      begin: () => {},
      track: () => {},
      schedule: () => {},
      advance: (k) => {
        for (let i = 0; i < k; i++) {
          step++;
          player.pos = [0, 0, -step / 60];
          for (const e of entities) {
            if (!e.alive || e === player) continue;
            e.pos = [e.pos[0] + 2 / 60, 0, 5];
            if (step - born.get(e) >= 90) {
              e.alive = false;
              for (const l of listeners) l({ type: 'destroy', entity: e });
            }
          }
          if (step % 60 === 0) born.set(add(entity(`rt_${next++}`, 'Boulder', [0, 0, 5])), step);
        }
        return { step, loads: 0, idleSteps: 0, simulateMs: 1 };
      },
      status: () => ({ step, loads: 0, idleSteps: 0, simulateMs: 1 }),
      render: () => 1,
      positions: (names) => Object.fromEntries(names.map((n) => [n, world.findByName(n)?.pos ?? null])),
      report: () => [{ name: 'Player', found: true, start: [0, 0, 0], final: player.pos, min: player.pos, max: [0, 0, 0], path: 4, destroyedAt: null, components: null, changes: {} }],
    };
    globalThis.window = { __gs: { test, world, state: 'playing', game: { scene: { name: 'main' }, describe: () => [] } }, __gsTestLogs: [] };
    globalThis.document = { body: { innerText: '' } };
    const page = { evaluate: async (fn, ...args) => fn(...args), screenshot: async () => Buffer.from([1]), waitForFunction: async () => {}, goto: async () => {}, close: async () => {}, isClosed: () => false };
    const browser = new StubBrowser(page);

    const result = await browser.playtest('http://x/', { duration: 4, track: ['Player', 'Boulder*'], sample_every: 0.5, screenshots: [2] });
    expect(result.warnings).toEqual([]);
    expect(result.sample_every_s).toBe(0.5);
    const [walker, boulders] = result.tracked;
    expect(walker.samples.map((s) => s[0])).toEqual([0, 0.5, 1, 1.5, 2, 2.5, 3, 3.5, 4]);
    expect(walker.samples[2]).toEqual([1, 0, 0, -1]);
    expect(boulders).toMatchObject({ pattern: 'Boulder*', matched: 4, alive_at_end: 2 });
    expect(boulders.instances.map((b) => [b.id, b.spawned_at_s, b.destroyed_at_s])).toEqual([['rt_1', 1, 2.5], ['rt_2', 2, 3.5], ['rt_3', 3, undefined], ['rt_4', 4, undefined]]);
    const first = boulders.instances[0];
    expect(first.start).toEqual([0, 0, 5]);
    expect(first.final[0]).toBeCloseTo(2.967, 3); // 89 steps at 2 m/s, then destroyed
    expect(first.samples).toEqual([[1, 0, 0, 5], [1.5, 1, 0, 5], [2, 2, 0, 5]]);
    expect(result.screenshots[0].positions).toEqual({ Player: [0, 0, -2], 'Boulder#rt_1': [2, 0, 5], 'Boulder#rt_2': [0, 0, 5] });

    // An exact name that several copies share is followed one at a time: the result says so.
    entities.length = 1;
    listeners.length = 0;
    step = 0;
    const plain = await browser.playtest('http://x/', { duration: 3, track: ['Player', 'Boulder'], screenshots: 0 });
    expect(plain.warnings.join(' ')).toMatch(/3 entities were named "Boulder".*"Boulder\*"/);
    expect(plain.tracked[0].samples).toBeUndefined();
  });

  it('aims at the nearest living target every step, through the game camera', async () => {
    class V {
      constructor(x = 0, y = 0, z = 0) { Object.assign(this, { x, y, z }); }
      clone() { return new V(this.x, this.y, this.z); }
      set(x, y, z) { return Object.assign(this, { x, y, z }); }
      distanceTo(o) { return Math.hypot(this.x - o.x, this.y - o.y, this.z - o.z); }
      project() { return new V(this.x / 10, this.y / 10, 0); } // a stand-in camera: 10 m to each canvas edge
    }
    const ent = (id, name, x) => ({
      id, name, alive: true,
      object3d: { position: new V(x, 0, 0), traverse(fn) { fn(this); }, updateWorldMatrix() {}, getWorldPosition(v) { return v.set(this.position.x, 0, 0); }, get matrixWorld() { return { elements: [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, this.position.x, 0, 0, 1] }; } },
    });
    const player = ent('p', 'Player', 0);
    const near = ent('g1', 'Grunt', -2);
    const far = ent('g2', 'Grunt', 5);
    const entities = [player, near, far];
    const moves = [];
    let step = 0;
    const test = {
      begin() {}, track() {}, report: () => [], status: () => ({ step, loads: 0, idleSteps: 0, simulateMs: 1 }),
      schedule: (inputs) => moves.push(...inputs.filter((i) => i.mouse === 'move')),
      advance: (k) => {
        step += k;
        if (step === 2) near.alive = false; // the nearest one dies: the aim moves on
        return { step, loads: 0, idleSteps: 0, simulateMs: 1 };
      },
      render: () => 1, positions: () => ({}),
    };
    const world = { onChange() {}, all: () => entities.filter((e) => e.alive), get: () => undefined, findByName: () => undefined, query: () => [player] };
    globalThis.window = { __gs: { test, world, state: 'playing', game: { three: { camera: { position: new V(0, 0, 0), updateMatrixWorld() {} } }, scene: { name: 'main' }, describe: () => [] } }, __gsTestLogs: [] };
    globalThis.document = { body: { innerText: '' }, querySelector: () => ({ getBoundingClientRect: () => ({ width: 200, height: 100 }) }) };
    const page = { evaluate: async (fn, ...args) => fn(...args), screenshot: async () => Buffer.from([1]), waitForFunction: async () => {}, goto: async () => {}, close: async () => {}, isClosed: () => false };
    const result = await new StubBrowser(page).playtest('http://x/', {
      inputs: [{ at: 0, mouse: 'aim', target: 'Grunt*', until: 0.05 }, { at: 0.02, mouse: 'click' }], duration: 0.1, screenshots: 0,
    });
    expect(moves).toEqual([{ step: 0, mouse: 'move', x: 80, y: 50 }, { step: 1, mouse: 'move', x: 80, y: 50 }, { step: 2, mouse: 'move', x: 150, y: 50 }]);
    expect(result.aims).toEqual([{ target: 'Grunt*', steps: 3, last: 'Grunt#g2' }]);
    expect(planPlaytest({ inputs: [{ at: 1, mouse: 'aim', target: 'Grunt*' }], duration: 4 }).aims[0]).toMatchObject({ from: 60, until: 240 });
    expect(() => planPlaytest({ inputs: [{ at: 1, mouse: 'aim' }] })).toThrow(/aim needs a target/);
    expect(planPlaytest({ inputs: [{ at: 1, mouse: 'move', world: [1, 2, 3], space: 'blender' }] }).aims[0]).toMatchObject({ from: 60, until: 61, world: [1, 3, -2] });
  });

  it('drops sample rows where nothing moved, keeping the last', () => {
    const still = [[0, 1, 0, 2], [0.5, 1, 0, 2], [1, 1.004, 0, 2], [1.5, 1, 0, 2]];
    expect(dropStill(still)).toEqual([[0, 1, 0, 2], [1.5, 1, 0, 2]]);
    expect(dropStill([[0, 0, 0, 0], [0.5, 0, 0, 0], [1, 0.2, 0, 0], [1.5, 0.2, 0, 0]])).toEqual([[0, 0, 0, 0], [1, 0.2, 0, 0], [1.5, 0.2, 0, 0]]);
    expect(dropStill([[0, 3, 0, 0]])).toEqual([[0, 3, 0, 0]]);
  });

  it('thins samples to fit the result', () => {
    const grid = (n, every) => Array.from({ length: n }, (_, i) => [Math.round(i * every * 1000) / 1000, i, 0, 0]);
    const small = decimateSamples([grid(10, 0.25)], 0.25);
    expect(small.factor).toBe(1);
    const long = decimateSamples([grid(480, 0.25), grid(40, 0.25)], 0.25);
    expect(long.factor).toBe(3);
    expect(long.lists[0].length).toBeLessThanOrEqual(MAX_SAMPLES_PER_ENTITY);
    expect(long.lists[0].slice(0, 3).map((s) => s[0])).toEqual([0, 0.75, 1.5]);
    const many = decimateSamples(Array.from({ length: 30 }, () => grid(100, 0.1)), 0.1);
    expect(many.lists.flat().length).toBeLessThanOrEqual(MAX_SAMPLE_POINTS);
  });

  it('says which start entity is missing', async () => {
    const { page } = fakeGame();
    await expect(new StubBrowser(page).playtest('http://x/', { start: { name: 'Ghost', position: [0, 0, 0] } })).rejects.toThrow(/No entity named "Ghost"/);
  });
});

describe('the persistent headless game', () => {
  /** A page that records its viewport changes; evaluate() stands in for waiting on animation frames. */
  function sizedPage() {
    const viewports = [];
    let viewport = null;
    const page = {
      viewports,
      setViewport: async (v) => {
        viewport = v;
        viewports.push([v.width, v.height]);
      },
      viewport: () => viewport,
      evaluate: async () => true,
      screenshot: async () => Buffer.from(viewport ? `${viewport.width}x${viewport.height}` : 'none'),
      goto: async () => {},
      close: async () => {},
      isClosed: () => false,
    };
    return page;
  }

  class SizedBrowser extends GameBrowser {
    constructor(page) {
      super();
      this.page = page;
      this.opened = [];
    }
    async openGame(url, options) {
      this.opened.push(options.size);
      await this.page.setViewport({ width: options.size[0], height: options.size[1] });
      return { page: this.page, tag: 't', logs: [], size: options.size, loadMs: 5 };
    }
  }

  it('opens small, takes play\'s size and keeps it, and shoots at another size without keeping it', async () => {
    const page = sizedPage();
    const browser = new SizedBrowser(page);
    expect(GAME_SIZE).toEqual([480, 270]);
    expect((await browser.ensureGame('http://x/')).size).toEqual(GAME_SIZE);
    expect(browser.opened).toEqual([GAME_SIZE]);

    const resized = await browser.ensureGame('http://x/', { size: [640, 360] }); // play with a size
    expect(resized).toMatchObject({ opened: false, size: [640, 360] });
    expect((await browser.ensureGame('http://x/')).size).toEqual([640, 360]); // game_screenshot keeps it
    expect(browser.opened).toHaveLength(1);

    const decode = (b64) => Buffer.from(b64, 'base64').toString();
    expect(decode(await browser.screenshot(page))).toBe('640x360');
    expect(decode(await browser.screenshot(page, [1280, 720]))).toBe('1280x720');
    expect(page.viewport()).toMatchObject({ width: 640, height: 360 }); // set back after the shot
    expect(decode(await browser.screenshot(page, [640, 360]))).toBe('640x360');
    expect(page.viewports).toEqual([[480, 270], [640, 360], [1280, 720], [640, 360]]);

    await browser.ensureGame('http://y/'); // another dev server: a new page at the default size
    expect(browser.opened).toEqual([GAME_SIZE, GAME_SIZE]);
  });
});
