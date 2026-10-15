// End-to-end smoke test: demo dev server + headless Edge/Chrome + a fake editor.
//
//   npm run smoke
//
// Runs on a throw-away copy of examples/demo-game (runtime/.smoke/smoke-project) with its own
// hub ports, so a dev server already serving the demo is never disturbed.
//
// Env: GS_SMOKE_OUT (screenshot folder, default runtime/.smoke),
//      GS_BROWSER (browser executable; default: Edge, then Chrome or Chromium; programs.mjs).

import { cp, mkdir, readFile, rm, writeFile } from 'node:fs/promises';
import net from 'node:net';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import puppeteer from 'puppeteer-core';
import { createServer } from 'vite';
import { findBrowser } from './programs.mjs';

const RUNTIME = fileURLToPath(new URL('..', import.meta.url));
const DEMO = path.resolve(RUNTIME, '..', 'examples', 'demo-game');
const PROJECT = path.join(RUNTIME, '.smoke', 'smoke-project');
const FIXTURE = path.join(RUNTIME, 'fixtures', 'basic');
const OUT = path.resolve(process.env.GS_SMOKE_OUT ?? path.join(RUNTIME, '.smoke'));

const IDS = { crate: 'gs_000000a00002', lamp: 'gs_000000a00003', coin: 'gs_000000a00006' };
const HMR_FILE = path.join(PROJECT, 'src', 'systems', '__smoke_hmr__.ts');
const COMPONENT_FILE = path.join(PROJECT, 'src', 'components', '__smoke_component__.ts');
// Written while the page runs to check hot reload. Line 9 throws (checked after source mapping).
const hmrSource = (version, fail) => `import { defineSystem } from '@gamestudio/engine';

// Temporary file written by runtime/scripts/smoke.mjs; it is deleted afterwards.
export default defineSystem({
  name: 'SmokeHmrSystem',
  update() {
    (window as unknown as { __smokeHmr: string }).__smokeHmr = '${version}';
    const reason: string = 'smoke-intentional';
    if (${fail}) throw new Error(\`\${reason} error\`);
  },
});
`;
// Visible from both the follow camera (playing) and the Blender camera (stopped).
const COIN_TARGET = [-1.5, 1.5, 2];

const results = [];
function check(name, ok, detail = '') {
  results.push({ name, ok });
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${detail ? `  (${detail})` : ''}`);
  if (!ok) throw new Error(`Check failed: ${name} ${detail}`);
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
async function waitFor(fn, { timeout = 15000, interval = 100, what = 'condition' } = {}) {
  const end = Date.now() + timeout;
  for (;;) {
    const value = await fn();
    if (value) return value;
    if (Date.now() > end) throw new Error(`Timed out waiting for ${what}`);
    await sleep(interval);
  }
}
const near = (a, b, eps) => a.length === b.length && a.every((v, i) => Math.abs(v - b[i]) <= eps);
const fmt = (v) => `[${v.map((n) => n.toFixed(3)).join(', ')}]`;

/** Minimal NDJSON editor client. */
function connectEditor(port) {
  return new Promise((resolve, reject) => {
    const socket = net.connect(port, '127.0.0.1');
    const messages = [];
    const waiters = [];
    let buffer = '';
    socket.setEncoding('utf8');
    socket.on('data', (chunk) => {
      buffer += chunk;
      let i;
      while ((i = buffer.indexOf('\n')) >= 0) {
        const line = buffer.slice(0, i).trim();
        buffer = buffer.slice(i + 1);
        if (!line) continue;
        const msg = JSON.parse(line);
        messages.push(msg);
        for (const w of [...waiters]) if (w.pred(msg)) waiters.splice(waiters.indexOf(w), 1) && w.resolve(msg);
      }
    });
    socket.once('error', reject);
    socket.once('connect', () =>
      resolve({
        messages,
        send: (msg) => socket.write(`${JSON.stringify(msg)}\n`),
        next: (pred, timeout = 10000) => {
          const found = messages.find(pred);
          if (found) return Promise.resolve(found);
          return new Promise((res, rej) => {
            const t = setTimeout(() => rej(new Error('Timed out waiting for an editor message')), timeout);
            waiters.push({ pred, resolve: (m) => (clearTimeout(t), res(m)) });
          });
        },
        close: () => socket.destroy(),
      }),
    );
  });
}

/** Copies the demo's sources and the fixture export into PROJECT (ephemeral hub ports). */
async function prepareProject() {
  await rm(PROJECT, { recursive: true, force: true });
  for (const item of ['index.html', 'vite.config.ts', 'src']) {
    await cp(path.join(DEMO, item), path.join(PROJECT, item), { recursive: true });
  }
  const config = JSON.parse(await readFile(path.join(DEMO, 'gamestudio.json'), 'utf8'));
  await writeFile(path.join(PROJECT, 'gamestudio.json'), JSON.stringify({ ...config, link: { editorPort: 0, gamePort: 0 } }, null, 2));
  const exportDir = path.join(PROJECT, '.gamestudio', 'export');
  await mkdir(exportDir, { recursive: true });
  // A format-1 export from before clips, prefabs and assets: old exports must keep loading.
  await cp(path.join(FIXTURE, 'basic.glb'), path.join(exportDir, 'basic.glb'));
  await cp(path.join(FIXTURE, 'basic.scene.json'), path.join(exportDir, 'basic.scene.json'));
}

async function main() {
  await mkdir(OUT, { recursive: true });
  await prepareProject();

  const executablePath = findBrowser();
  if (!executablePath) throw new Error('No Edge, Chrome or Chromium found; set GS_BROWSER');

  const server = await createServer({
    root: PROJECT,
    // Its own dependency cache: the throw-away projects share the repo's node_modules, and another
    // project's optimized deps would make Vite re-optimize mid-run (stale "504 Outdated Optimize Dep").
    cacheDir: path.join(PROJECT, '.vite-cache'),
    logLevel: process.env.GS_VITE_LOG ?? 'warn',
    server: { port: 5190, strictPort: false },
  });
  let browser;
  let editor;
  try {
    await server.listen();
    const url = server.resolvedUrls.local[0];
    console.log(`dev server: ${url}`);

    const sessionFile = path.join(PROJECT, '.gamestudio', 'session.json');
    const hub = server.config.plugins.find((p) => p.name === 'gamestudio').api.hub;
    const session = await waitFor(
      async () => {
        try {
          const s = JSON.parse(await readFile(sessionFile, 'utf8'));
          return hub && s.token && s.editorPort === hub.editorPort && s.gamePort === hub.gamePort ? s : null;
        } catch {
          return null;
        }
      },
      { what: 'session.json' },
    );
    check('session.json written', /^[0-9a-f]{32}$/.test(session.token), `editor ${session.editorPort}, game ${session.gamePort}`);

    browser = await puppeteer.launch({
      executablePath,
      headless: true,
      args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--ignore-gpu-blocklist', '--no-first-run', '--window-size=1280,720'],
    });
    const page = await browser.newPage();
    await page.setViewport({ width: 1280, height: 720 });
    const consoleErrors = [];
    page.on('console', (msg) => {
      if (msg.type() === 'error') consoleErrors.push(msg.text());
    });
    page.on('pageerror', (error) => consoleErrors.push(`pageerror: ${error.message}`));
    page.on('response', (res) => {
      if (res.status() >= 400) consoleErrors.push(`HTTP ${res.status()} ${res.url()}`);
    });

    await page.goto(`${url}?scene=basic`, { waitUntil: 'load' });
    await page.waitForFunction(() => window.__gs?.ready === true, { timeout: 60000 });
    const info = await page.evaluate(() => ({
      state: window.__gs.state,
      entities: window.__gs.world.size,
      renderer: window.__gs.game.three.renderer.kind,
    }));
    check('scene "basic" loaded and playing', info.state === 'playing' && info.entities === 8, JSON.stringify(info));

    // The crate starts at y = 4 and must come to rest on the ground (y ≈ 0.5). "At rest" is measured
    // in game time: with software rendering on a busy machine one frame can take most of a second.
    const crate = () =>
      page.evaluate((id) => {
        const o = window.__gs.world.get(id).object3d;
        o.updateWorldMatrix(true, false);
        return { y: o.matrixWorld.elements[13], time: window.__gs.test.status().gameTime };
      }, IDS.crate);
    let last = await crate();
    let stableSince = null;
    const rest = await waitFor(
      async () => {
        const now = await crate();
        if (Math.abs(now.y - last.y) < 1e-4 && now.y < 3.9) stableSince ??= last.time;
        else stableSince = null;
        last = now;
        return stableSince !== null && now.time - stableSince > 0.75 ? now.y : null;
      },
      { timeout: 60000, interval: 100, what: 'the crate to come to rest' },
    );
    check('Crate fell and came to rest on the ground', Math.abs(rest - 0.5) < 0.05, `y = ${rest.toFixed(3)}`);
    await page.screenshot({ path: path.join(OUT, 'smoke-playing.png') });

    // Act as the editor over TCP NDJSON.
    editor = await connectEditor(session.editorPort);
    editor.send({ t: 'hello', role: 'editor', token: session.token, protocol: 1, client: 'smoke-test' });
    const welcome = await editor.next((m) => m.t === 'welcome' || m.t === 'error');
    check('editor authenticated', welcome.t === 'welcome' && welcome.peers.some((p) => p.role === 'game'), `peer ${welcome.peer}`);
    const registry = await editor.next((m) => m.t === 'registry');
    check('editor received the registry', registry.registry.components.some((c) => c.name === 'Coin'), `${registry.registry.components.length} components`);
    let requestCount = 0;
    /** state.request → state.response entities. */
    const request = async (filter) => {
      const id = `smoke-req-${++requestCount}`;
      editor.send({ t: 'state.request', id, filter });
      return (await editor.next((m) => m.t === 'state.response' && m.re === id)).entities;
    };
    const one = async (id) => (await request({ ids: [id] }))[0];
    const pageState = () => page.evaluate(() => window.__gs.state);

    const stats = await editor.next((m) => m.t === 'stats');
    check('game sends stats', stats.entities === 8 && stats.bodies === 5, `fps ${stats.fps}, frame ${stats.frameMs} ms`);

    editor.send({ t: 'entity.transform', items: [{ id: IDS.coin, p: COIN_TARGET, r: [0, 0, 0, 1], s: [1, 1, 1] }] });
    editor.send({ t: 'state.request', id: 'smoke-1', filter: { ids: [IDS.coin, IDS.crate] } });
    const response = await editor.next((m) => m.t === 'state.response' && m.re === 'smoke-1');
    const coin = response.entities.find((e) => e.id === IDS.coin);
    check('state.response shows the moved Coin', coin && near(coin.position, COIN_TARGET, 1e-3), coin && fmt(coin.position));
    check('state.response includes component values', coin?.components?.Spin?.speed === 2 && coin.components.Collider.sensor === true);
    await sleep(300);
    await page.screenshot({ path: path.join(OUT, 'smoke-edited.png') });

    editor.send({ t: 'play.stop' });
    await page.waitForFunction(() => window.__gs.state === 'stopped', { timeout: 10000 });
    let stopped;
    await waitFor(
      async () => {
        const id = `smoke-stop-${Date.now()}`;
        editor.send({ t: 'state.request', id, filter: { ids: [IDS.crate, IDS.coin] } });
        stopped = await editor.next((m) => m.t === 'state.response' && m.re === id);
        const crate = stopped.entities.find((e) => e.id === IDS.crate);
        return crate && near(crate.position, [0, 4, 0], 1e-3);
      },
      { timeout: 10000, interval: 200, what: 'the crate to reset' },
    );
    const crateAfter = stopped.entities.find((e) => e.id === IDS.crate);
    const coinAfter = stopped.entities.find((e) => e.id === IDS.coin);
    check('play.stop reset the Crate to its start', true, fmt(crateAfter.position));
    check('editor edits survive play.stop', near(coinAfter.position, COIN_TARGET, 1e-3), fmt(coinAfter.position));
    const status = await editor.next((m) => m.t === 'game.status' && m.state === 'stopped');
    check('game.status reported "stopped"', !!status);

    await sleep(300); // Let a few frames render after the reset.
    const shot = path.join(OUT, 'smoke-stopped.png');
    await page.screenshot({ path: shot });
    console.log(`screenshots in ${OUT}: smoke-playing.png, smoke-edited.png, smoke-stopped.png`);

    // --- Remaining editor → game messages ------------------------------------------
    editor.send({ t: 'play.step', frames: 30 });
    let stepped;
    await waitFor(
      async () => {
        stepped = await one(IDS.crate);
        return (await pageState()) === 'paused' && stepped.position[1] < 3.5;
      },
      { what: 'play.step to advance physics' },
    );
    await sleep(200);
    const still = await one(IDS.crate);
    check('play.step advances physics, then stays paused', near(still.position, stepped.position, 1e-6), `crate y ${fmt(still.position)}`);
    editor.send({ t: 'play.start' });
    await waitFor(async () => (await pageState()) === 'playing', { what: 'play.start' });
    editor.send({ t: 'play.pause', paused: true });
    await waitFor(async () => (await pageState()) === 'paused', { what: 'play.pause' });
    editor.send({ t: 'play.pause', paused: false });
    await waitFor(async () => (await pageState()) === 'playing', { what: 'play.pause false' });
    check('play.start / play.pause change the play state', true);

    editor.send({ t: 'entity.components', id: IDS.coin, components: { Collider: { shape: 'box', sensor: true } } });
    editor.send({ t: 'entity.visibility', id: IDS.coin, visible: false });
    const coinEdited = await one(IDS.coin);
    check(
      'entity.components replaces the set; entity.visibility hides',
      !coinEdited.components.Spin && coinEdited.components.Collider.shape === 'box' && coinEdited.visible === false,
      JSON.stringify(coinEdited.components),
    );
    editor.send({ t: 'select', ids: [IDS.crate] });
    await waitFor(() => page.evaluate(() => !!window.__gs.game.three.scene.getObjectByName('gs_selection')), { what: 'selection box' });
    check('select highlights the entity', true);
    editor.send({ t: 'entity.remove', ids: [IDS.lamp] });
    await waitFor(async () => (await request({ ids: [IDS.lamp] })).length === 0, { what: 'entity.remove' });
    check('entity.remove removes the entity', (await request({})).length === 7);

    editor.send({ t: 'scene.load', scene: 'basic', url: '/__gs/export/basic.scene.json', rev: 1 });
    const ready = await editor.next((m) => m.t === 'game.ready');
    const reloaded = await request({});
    check('scene.load reloads (and drops live edits)', ready.scene === 'basic' && ready.rev === 1 && reloaded.length === 8);

    // --- Test clock (window.__gs.test): scripted, deterministic stepping -------------------
    const testRun = (renderEvery) =>
      page.evaluate(async (every) => {
        const test = window.__gs.test;
        await test.begin({ reset: true });
        test.track(['Player', 'Crate']);
        test.schedule([{ at: 0, key: 'KeyW', hold: 1 }, { at: 0.5, key: 'KeyD', hold: 0.5 }, { at: 1.2, key: 'Space' }]);
        for (let i = 0; i < 150; i++) {
          await test.advance(1);
          if (every && i % every === 0) test.render();
        }
        return { report: test.report(), status: test.status() };
      }, renderEvery);
    // Rendering every 10th step: each draw queues real GPU work (SwiftShader), which real time then waits for.
    const unrendered = await testRun(0);
    const rendered = await testRun(10);
    check(
      'test clock: the same script gives identical results with and without rendering',
      JSON.stringify(unrendered.report) === JSON.stringify(rendered.report) && rendered.status.renders === 15 && unrendered.status.renders === 0,
      `Player ${fmt(unrendered.report[0].final)}, ${unrendered.status.simulateMs} ms for 150 steps`,
    );
    const [walker] = unrendered.report;
    check('test clock: W moved the player and a one-step Space press jumped', walker.path > 5 && walker.max[1] > walker.start[1] + 0.5, `path ${walker.path} m, peak ${walker.max[1]}`);
    const heldTime = await page.evaluate(() => window.__gs.test.status().gameTime);
    editor.send({ t: 'play.pause', paused: true });
    await sleep(300);
    check(
      'test clock: real time and editor play controls wait while a test holds the clock',
      (await page.evaluate(() => window.__gs.test.status().gameTime)) === heldTime && (await pageState()) === 'playing',
    );
    editor.send({ t: 'scene.load', scene: 'basic', url: '/__gs/export/basic.scene.json', rev: 1 });
    await editor.next((m) => m.t === 'game.ready' && m !== ready);
    const afterReload = await page.evaluate(() => window.__gs.test.advance(10));
    check('test clock: a scene reload during a test is applied and reported', afterReload.loads === 1 && afterReload.step === 160, JSON.stringify(afterReload));
    await page.evaluate(() => window.__gs.test.end());
    await waitFor(async () => (await page.evaluate(() => window.__gs.test.status().gameTime)) > 0.5, { timeout: 30000, what: 'real time to resume after test.end()' });
    check('test clock: end() gives time back to the real-time loop', true);

    // --- Component files regenerate the registry and reach the editor -------------
    await writeFile(
      COMPONENT_FILE,
      [
        "import { defineComponent, field } from '@gamestudio/engine/schema';",
        "export const SmokeTemp = defineComponent('SmokeTemp', { fields: { n: field.int(3) } });",
        '',
      ].join('\n'),
    );
    const added = await editor.next((m) => m.t === 'registry' && m.registry.components.some((c) => c.name === 'SmokeTemp'));
    const entry = added.registry.components.find((c) => c.name === 'SmokeTemp');
    await rm(COMPONENT_FILE, { force: true });
    await editor.next((m) => m.t === 'registry' && !m.registry.components.some((c) => c.name === 'SmokeTemp'));
    check('editing src/components regenerates and broadcasts the registry', entry.source === 'src/components/__smoke_component__.ts');
    await page.waitForFunction(() => window.__gs?.ready, { timeout: 30000 }); // The glob change reloaded the page.

    // --- Hot reload of a system keeps the world; errors are source-mapped ----------
    await writeFile(HMR_FILE, hmrSource('v1', false));
    // A new file changes main.ts's import.meta.glob, so this one is a full reload.
    await page.waitForFunction(() => window.__smokeHmr === 'v1' && window.__gs?.ready, { timeout: 30000 });
    await page.evaluate(() => {
      window.__smokeMarker = window.__gs.world.findByName('Crate');
    });
    await writeFile(HMR_FILE, hmrSource('v2', true));
    await page.waitForFunction(() => window.__smokeHmr === 'v2', { timeout: 20000 });
    const preserved = await page.evaluate(() => window.__smokeMarker === window.__gs.world.findByName('Crate'));
    check('editing a system hot-swaps it without a reload (world preserved)', preserved);
    const error = await editor.next((m) => m.t === 'error' && String(m.msg).includes('smoke-intentional'));
    check(
      'system errors reach the editor with the original file and line',
      error.file === 'src/systems/__smoke_hmr__.ts' && error.line === 9,
      `${error.msg} at ${error.file}:${error.line}:${error.col}`,
    );
    const logs = editor.messages.filter((m) => m.t === 'log');
    check('console output is forwarded as log messages', logs.length > 0, `${logs.length} log messages`);

    const unexpected = consoleErrors.filter((e) => !e.includes('smoke-intentional'));
    check('no console errors', unexpected.length === 0, unexpected.join(' | '));
  } finally {
    editor?.close();
    await browser?.close();
    await rm(HMR_FILE, { force: true });
    await rm(COMPONENT_FILE, { force: true });
    await server.close();
  }
  console.log(`\nsmoke: ${results.filter((r) => r.ok).length}/${results.length} checks passed`);
}

main().then(
  () => process.exit(0),
  (error) => {
    console.error(`\nsmoke FAILED: ${error instanceof Error ? error.stack : error}`);
    process.exit(1);
  },
);
