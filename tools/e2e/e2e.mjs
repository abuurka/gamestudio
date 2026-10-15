// SPDX-License-Identifier: MIT
// End-to-end test: real Blender + real add-on + real dev server + real game in a browser.
//
//   node tools/e2e/e2e.mjs [--blender PATH]   (from the repository root; needs Blender 5.2 and Edge,
//                                              Chrome or Chromium; BLENDER and GS_BROWSER override the
//                                              lookup in runtime/scripts/programs.mjs)
//
// 1. Start the demo game's dev server and open the game in headless Edge or Chrome.
// 2. Run Blender headless with tools/e2e/blender_e2e.py. It opens scenes/main.blend,
//    connects, presses Play, makes live edits and queries the game.
// 3. While Blender waits, send a console line from the game and drive the MCP
//    server (runtime/packages/mcp) as an external agent would: it reaches Blender
//    through the agent bridge and edits the scene directly.
// 4. Play the game with the keyboard and save screenshots.
import { spawn } from 'node:child_process';
import { existsSync } from 'node:fs';
import { mkdir, rm, writeFile, copyFile } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import puppeteer from 'puppeteer-core';
import { createServer } from 'vite';
import { findBlender, findBrowser } from '../../runtime/scripts/programs.mjs';

const REPO = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..');
const DEMO = path.join(REPO, 'examples', 'demo-game');
const OUT = process.env.GS_E2E_OUT ?? path.join(REPO, 'runtime', '.smoke', 'e2e');
const argv = process.argv.slice(2);
const BLENDER = (argv.includes('--blender') ? argv[argv.indexOf('--blender') + 1] : undefined) ?? findBlender();
const BROWSER = findBrowser();

const results = [];
function check(name, ok, detail = '') {
  results.push({ name, ok });
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${detail ? `  (${detail})` : ''}`);
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/** Minimal MCP stdio client for runtime/packages/mcp, started the way Claude Code does. */
function startMcp() {
  const proc = spawn(process.execPath, [path.join(REPO, 'runtime', 'packages', 'mcp', 'bin', 'gamestudio-mcp.js')], {
    cwd: DEMO,
    stdio: ['pipe', 'pipe', 'pipe'],
  });
  let buffer = '';
  const waiting = new Map();
  proc.stdout.on('data', (chunk) => {
    buffer += chunk;
    let nl;
    while ((nl = buffer.indexOf('\n')) >= 0) {
      const line = buffer.slice(0, nl);
      buffer = buffer.slice(nl + 1);
      if (!line.trim()) continue;
      const msg = JSON.parse(line);
      waiting.get(msg.id)?.(msg);
      waiting.delete(msg.id);
    }
  });
  let nextId = 1;
  const rpc = (method, params = {}) =>
    new Promise((resolve, reject) => {
      const id = nextId++;
      const timer = setTimeout(() => reject(new Error(`MCP ${method} timed out`)), 30000);
      waiting.set(id, (msg) => {
        clearTimeout(timer);
        resolve(msg);
      });
      proc.stdin.write(JSON.stringify({ jsonrpc: '2.0', id, method, params }) + '\n');
    });
  return { rpc, close: () => proc.kill() };
}

async function main() {
  if (!BLENDER) throw new Error('Blender 5.2 not found; set BLENDER=path/to/blender');
  if (!BROWSER) throw new Error('No Edge, Chrome or Chromium found; set GS_BROWSER');
  if (!existsSync(path.join(DEMO, 'scenes', 'main.blend'))) {
    throw new Error('Build the demo level first: blender -b --python examples/demo-game/tools/build_level.py');
  }
  await mkdir(OUT, { recursive: true });
  const sentinel = path.join(os.tmpdir(), `gs_e2e_${process.pid}.done`);
  await rm(sentinel, { force: true });

  const server = await createServer({ root: DEMO, logLevel: 'warn', server: { port: 5191, strictPort: false } });
  let browser;
  let blender;
  let mcp;
  try {
    await server.listen();
    const url = server.resolvedUrls.local[0];
    console.log(`dev server: ${url}`);

    browser = await puppeteer.launch({
      executablePath: BROWSER,
      headless: true,
      args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--ignore-gpu-blocklist', '--no-first-run', '--window-size=1280,720'],
    });
    const page = await browser.newPage();
    await page.setViewport({ width: 1280, height: 720 });
    const pageErrors = [];
    page.on('pageerror', (e) => pageErrors.push(e.message));
    page.on('console', (m) => m.type() === 'error' && pageErrors.push(m.text()));
    await page.goto(url, { waitUntil: 'load' });
    await page.waitForFunction(() => window.__gs?.ready === true, { timeout: 60000 });
    check('game page loaded the main scene', true, await page.evaluate(() => `${window.__gs.world.size} entities`));

    // --- Blender ------------------------------------------------------------------
    const blenderLog = [];
    blender = spawn(BLENDER, ['--background', '--factory-startup', '--python-exit-code', '1',
      '--python', path.join(REPO, 'tools', 'e2e', 'blender_e2e.py'), '--', sentinel], { cwd: REPO });
    const blenderExit = new Promise((resolve) => blender.on('exit', resolve));
    let waitingSeen = false;
    const onLine = (line) => {
      blenderLog.push(line);
      if (line.startsWith('E2E ')) console.log(`  [blender] ${line.slice(4)}`);
      if (line.startsWith('E2E_WAITING')) waitingSeen = true;
    };
    let carry = '';
    const feed = (chunk) => {
      carry += chunk;
      const lines = carry.split(/\r?\n/);
      carry = lines.pop();
      lines.forEach(onLine);
    };
    blender.stdout.on('data', feed);
    blender.stderr.on('data', feed);

    const deadline = Date.now() + 120000;
    while (!waitingSeen && blender.exitCode === null && Date.now() < deadline) await sleep(100);
    check('Blender reached the external-agent phase', waitingSeen);

    if (waitingSeen) {
      // A game-side log line must reach Blender's runtime console.
      await page.evaluate(() => console.warn('e2e-probe: hello from the game'));

      // Drive the MCP server like Claude Code would.
      mcp = startMcp();
      const init = await mcp.rpc('initialize', { protocolVersion: '2025-11-25', capabilities: {}, clientInfo: { name: 'e2e', version: '1' } });
      check('MCP initialize', init.result?.serverInfo?.name === 'gamestudio', init.result?.protocolVersion);
      const list = await mcp.rpc('tools/list');
      const names = (list.result?.tools ?? []).map((t) => t.name);
      check('MCP tools/list', names.includes('scene_summary') && names.includes('set_transform'), `${names.length} tools`);
      const summary = await mcp.rpc('tools/call', { name: 'scene_summary', arguments: { max_objects: 3 } });
      const text = summary.result?.content?.[0]?.text ?? '';
      check('MCP scene_summary answered from Blender', !summary.result?.isError && /"scene":\s*"main"/.test(text), text.slice(0, 80));
      const move = await mcp.rpc('tools/call', { name: 'set_transform', arguments: { name: 'Crate1', location: [4, 4, 6] } });
      check('MCP set_transform applied', !move.result?.isError, move.result?.content?.[0]?.text?.slice(0, 80));
      mcp.close();
      await writeFile(sentinel, 'done');
    }

    const code = await Promise.race([blenderExit, sleep(60000).then(() => 'timeout')]);
    check('Blender checks all passed', code === 0, `exit ${code}`);
    if (code !== 0) console.log(blenderLog.filter((l) => /Error|Traceback|FAIL/.test(l)).slice(-15).join('\n'));

    // --- Play the game ------------------------------------------------------------
    const playerPos = () =>
      page.evaluate(() => {
        const o = window.__gs.world.findByName('Player').object3d;
        o.updateWorldMatrix(true, false);
        return [o.matrixWorld.elements[12], o.matrixWorld.elements[13], o.matrixWorld.elements[14]];
      });
    await page.screenshot({ path: path.join(OUT, 'e2e-start.png') });
    const before = await playerPos();
    await page.keyboard.down('KeyW');
    await sleep(2500);
    await page.keyboard.up('KeyW');
    await sleep(300);
    const after = await playerPos();
    const moved = Math.hypot(after[0] - before[0], after[2] - before[2]);
    check('keyboard moves the player', moved > 1, `moved ${moved.toFixed(2)} m`);
    const hud = await page.evaluate(() => document.body.innerText);
    check('HUD shows coin progress', /Coins \d+ \/ 6/.test(hud), hud.split('\n').find((l) => l.includes('Coins')));
    await page.screenshot({ path: path.join(OUT, 'e2e-after-walk.png') });
    await mkdir(path.join(REPO, 'docs', 'images'), { recursive: true });
    await copyFile(path.join(OUT, 'e2e-start.png'), path.join(REPO, 'docs', 'images', 'demo-game.png'));
    check('no errors in the game page', pageErrors.length === 0, pageErrors.slice(0, 3).join(' | '));
    console.log(`screenshots: ${OUT}`);
  } finally {
    mcp?.close();
    if (blender && blender.exitCode === null) blender.kill();
    await browser?.close();
    await server.close();
    await rm(sentinel, { force: true });
  }
  const failed = results.filter((r) => !r.ok).length;
  console.log(`\ne2e: ${results.length - failed}/${results.length} checks passed`);
  process.exit(failed ? 1 : 0);
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
