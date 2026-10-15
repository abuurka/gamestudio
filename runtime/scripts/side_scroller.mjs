// 2.5D proof: Blender authors a side-scroller level (side_scroller_scene.py: platforms, a plane-locked
// player, crates locked to the plane, an orthographic camera with SideCamera and level bounds), a dev
// server serves it, and headless Edge/Chrome plays it on the test clock: the player runs and jumps
// without leaving its depth, pushes the crates (which stay in the plane), the camera follows with
// look-ahead inside the bounds, and a portrait window keeps the orthographic height without stretching.
//
//   node runtime/scripts/side_scroller.mjs
//
// Uses a throw-away project in runtime/.smoke/side-project and a scratch Blender profile. Screenshots
// go to GS_SMOKE_OUT (default runtime/.smoke): side-start.png, side-run.png, side-portrait.png.
// Env: BLENDER, GS_BROWSER (default: found by programs.mjs), GS_SMOKE_OUT.

import { spawn } from 'node:child_process';
import { mkdir, mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import puppeteer from 'puppeteer-core';
import { createServer } from 'vite';
import { findBlender, findBrowser } from './programs.mjs';

const RUNTIME = fileURLToPath(new URL('..', import.meta.url));
const REPO = path.resolve(RUNTIME, '..');
const PROJECT = path.join(RUNTIME, '.smoke', 'side-project');
const OUT = path.resolve(process.env.GS_SMOKE_OUT ?? path.join(RUNTIME, '.smoke'));
const SCENE_SCRIPT = path.join(RUNTIME, 'scripts', 'side_scroller_scene.py');
const BLENDER = findBlender();
const BROWSER = findBrowser();

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

const FILES = {
  'gamestudio.json': JSON.stringify({ name: 'side', formatVersion: 1, startScene: 'main', link: { editorPort: 0, gamePort: 0 } }),
  'vite.config.ts': `import { gamestudio } from '@gamestudio/devserver';
import { defineConfig } from 'vite';

export default defineConfig({ plugins: [gamestudio()] });
`,
  'index.html': `<!doctype html>
<html><head><meta charset="utf-8"><title>side</title><link rel="icon" href="data:,">
<style>html,body{margin:0;height:100%;background:#111}canvas{display:block;width:100%;height:100%}</style></head>
<body><canvas id="game"></canvas><script type="module" src="/src/main.ts"></script></body></html>
`,
  'src/main.ts': `import { createGame } from '@gamestudio/engine';
createGame({ canvas: '#game', startScene: 'main' });
`,
};

async function writeProject() {
  await rm(PROJECT, { recursive: true, force: true });
  for (const [file, text] of Object.entries(FILES)) {
    await mkdir(path.dirname(path.join(PROJECT, file)), { recursive: true });
    await writeFile(path.join(PROJECT, file), text);
  }
}

/** Runs side_scroller_scene.py in a background Blender with a scratch profile; resolves its SIDE events. */
function runBlender() {
  return new Promise((resolve, reject) => {
    mkdtemp(path.join(os.tmpdir(), 'gs_side_profile_')).then((profile) => {
      const env = { ...process.env, BLENDER_USER_RESOURCES: profile, TEMP: profile, TMP: profile, TMPDIR: profile };
      const proc = spawn(BLENDER, ['--background', '--factory-startup', '--python-exit-code', '1', '--python', SCENE_SCRIPT, '--', PROJECT], { cwd: REPO, env });
      const events = [];
      const log = [];
      let carry = '';
      const feed = (chunk) => {
        carry += chunk;
        const lines = carry.split(/\r?\n/);
        carry = lines.pop();
        for (const line of lines) {
          log.push(line);
          if (line.startsWith('SIDE ')) events.push(JSON.parse(line.slice(5)));
        }
      };
      proc.stdout.on('data', feed);
      proc.stderr.on('data', feed);
      proc.on('exit', (code) => {
        rm(profile, { recursive: true, force: true }).catch(() => {});
        if (code === 0) resolve(events);
        else reject(new Error(`Blender exited with ${code}:\n${log.filter((l) => /Error|Traceback|SIDE|WARNING/.test(l)).slice(-20).join('\n')}`));
      });
    }, reject);
  });
}

/** The JSON chunk of a GLB. */
function glbJson(buffer) {
  const length = buffer.readUInt32LE(12);
  return JSON.parse(buffer.subarray(20, 20 + length).toString('utf8'));
}

async function main() {
  if (!BLENDER) throw new Error('Blender 5.2+ not found; set BLENDER');
  if (!BROWSER) throw new Error('No Edge, Chrome or Chromium found; set GS_BROWSER');
  await mkdir(OUT, { recursive: true });
  await writeProject();

  const server = await createServer({
    root: PROJECT,
    cacheDir: path.join(PROJECT, '.vite-cache'),
    logLevel: 'warn',
    server: { port: 5194, strictPort: false },
  });
  let browser;
  try {
    await server.listen();
    const url = server.resolvedUrls.local[0];
    console.log(`dev server: ${url} (project ${path.relative(REPO, PROJECT)})`);
    await waitFor(
      async () => {
        try {
          const data = JSON.parse(await readFile(path.join(PROJECT, '.gamestudio', 'components.json'), 'utf8'));
          return data.components.some((c) => c.name === 'SideCamera');
        } catch {
          return false;
        }
      },
      { what: 'components.json with SideCamera', timeout: 30000 },
    );

    // 1. Author and export in Blender.
    const exported = (await runBlender()).find((e) => e.kind === 'export');
    const manifest = JSON.parse(await readFile(exported.result.manifest, 'utf8'));
    const byName = Object.fromEntries(manifest.entities.map((e) => [e.name, e]));
    check(
      'the manifest carries the 2.5D components',
      byName.Player?.components.CharacterController?.plane === 'side' &&
        byName.Crate_1?.components.RigidBody?.lockZ === true &&
        byName.Camera?.components.SideCamera?.target === exported.ids.Player &&
        byName.Camera?.components.SideCamera?.bounds === exported.ids.LevelBounds,
      JSON.stringify(byName.Camera?.components.SideCamera),
    );
    const gltf = glbJson(await readFile(path.join(path.dirname(exported.result.manifest), manifest.glb)));
    const cam = gltf.cameras?.[0];
    check(
      'Blender exports the orthographic camera with its ortho scale (16 m wide at 16:9, 9 m high)',
      cam?.type === 'orthographic' && Math.abs(cam.orthographic.xmag - 8) < 1e-4 && Math.abs(cam.orthographic.ymag - 4.5) < 1e-4,
      JSON.stringify(cam),
    );

    // 2. Play it on the test clock.
    browser = await puppeteer.launch({
      executablePath: BROWSER,
      headless: true,
      args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--ignore-gpu-blocklist', '--no-first-run', '--window-size=1280,720'],
    });
    const page = await browser.newPage();
    await page.setViewport({ width: 1280, height: 720 });
    const errors = [];
    page.on('console', (msg) => {
      if (msg.type() === 'error' || ((msg.type() === 'warn' || msg.type() === 'warning') && msg.text().includes('[gamestudio]'))) errors.push(msg.text());
    });
    page.on('pageerror', (error) => errors.push(`pageerror: ${error.message}`));
    await page.evaluateOnNewDocument(() => {
      window.__gsTestHold = true;
    });
    await page.goto(`${url}?scene=main`, { waitUntil: 'load' });
    await page.waitForFunction(() => window.__gs?.ready === true, { timeout: 60000 });
    const shot = async (name) => {
      await page.evaluate(() => window.__gs.test.render());
      await page.screenshot({ path: path.join(OUT, name) });
    };
    const view = () =>
      page.evaluate(() => {
        const c = window.__gs.game.three.camera;
        return { ortho: !!c.isOrthographicCamera, left: c.left, right: c.right, top: c.top, bottom: c.bottom, position: c.position.toArray() };
      });

    await page.evaluate(() => window.__gs.test.begin());
    await page.evaluate(() => window.__gs.test.advance(10)); // the SideCamera snaps onto the player on the first step
    await page.evaluate(() => window.__gs.test.track(['Player', 'Crate_1', 'Crate_2', 'Camera']));
    const start = await page.evaluate(() => window.__gs.test.positions(['Player', 'Crate_1', 'Camera']));
    const startView = await view();
    check(
      'the camera is orthographic, 9 m high, 16 m wide at 16:9, held at the left and bottom bounds',
      startView.ortho && Math.abs(startView.top - 4.5) < 1e-6 && Math.abs(startView.right - 8) < 1e-3 &&
        start.Camera[0] === -13 + 8 && start.Camera[1] === -1 + 4.5 && start.Player[0] - start.Camera[0] < 0,
      JSON.stringify({ view: startView, player: start.Player }),
    );
    await shot('side-start.png');

    // Run right through the crates and over the steps, jumping now and then.
    await page.evaluate(() =>
      window.__gs.test.schedule([
        { at: 0, key: 'KeyD', hold: 4.5 },
        { at: 0, key: 'KeyW', hold: 4.5 }, // pushes "into the screen" in 3D; ignored in plane: 'side'
        { at: 2.2, key: 'Space' },
        { at: 3.0, key: 'Space' },
        { at: 3.8, key: 'Space' },
      ]),
    );
    await page.evaluate(() => window.__gs.test.advance(150));
    await shot('side-run.png');
    await page.evaluate(() => window.__gs.test.advance(150));
    // The report rounds to millimetres; the engine tests check that the locked axes stay bit-exact.
    const report = Object.fromEntries((await page.evaluate(() => window.__gs.test.report())).map((t) => [t.name, t]));
    const { Player: player, Crate_1: crate, Camera: camera } = report;
    check(
      'the player ran right and jumped, and its depth never changed',
      player.final[0] - player.start[0] > 10 && player.max[1] > 1.5 && player.min[2] === player.start[2] && player.max[2] === player.start[2],
      JSON.stringify({ start: player.start, final: player.final, min: player.min, max: player.max }),
    );
    check(
      'the crates were pushed along and stayed in the plane',
      crate.max[0] - crate.start[0] > 0.3 && crate.min[2] === crate.start[2] && crate.max[2] === crate.start[2],
      JSON.stringify({ start: crate.start, final: crate.final, min: crate.min, max: crate.max }),
    );
    const runView = await view();
    check(
      'the camera followed with look-ahead, kept its depth and stayed inside the level bounds',
      camera.min[2] === camera.max[2] && runView.position[0] > player.final[0] - 0.5 &&
        camera.min[0] - (runView.right - runView.left) / 2 >= -13 - 1e-6 && camera.min[1] - runView.top >= -1 - 1e-6,
      JSON.stringify({ camera: camera.final, player: player.final, min: camera.min, max: camera.max }),
    );

    // A portrait window: the same height, a narrower view, nothing stretched.
    await page.setViewport({ width: 540, height: 960 });
    await waitFor(async () => (await page.evaluate(() => window.innerWidth)) === 540, { what: 'the resize' });
    await sleep(200); // the ResizeObserver
    await page.evaluate(() => window.__gs.test.advance(2));
    await shot('side-portrait.png');
    const tall = await view();
    check(
      'a portrait window keeps the 9 m height and narrows the width to its aspect',
      Math.abs(tall.top - 4.5) < 1e-6 && Math.abs((tall.right - tall.left) / (tall.top - tall.bottom) - 540 / 960) < 1e-3,
      JSON.stringify(tall),
    );
    await page.evaluate(() => window.__gs.test.end());
    check('no errors or engine warnings', errors.length === 0, errors.join(' | '));
    console.log(`screenshots in ${OUT}: side-start.png, side-run.png, side-portrait.png`);
  } finally {
    await browser?.close();
    await server.close();
  }
  console.log(`\nside-scroller: ${results.filter((r) => r.ok).length}/${results.length} checks passed`);
}

main().then(
  () => process.exit(0),
  (error) => {
    console.error(`\nside-scroller FAILED: ${error instanceof Error ? error.stack : error}`);
    process.exit(1);
  },
);
