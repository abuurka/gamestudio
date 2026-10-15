// SPDX-License-Identifier: GPL-3.0-or-later
// Browser half of tools/bench_play.py --browser: times a GameStudio scene load (manifest + GLBs ->
// game ready) in headless Edge/Chrome, the way runtime/scripts/smoke.mjs drives a game.
//
//   node tools/bench/browser_load.mjs --project DIR [--scene main] [--loads 3] [--out result.json]
//
// DIR is a throw-away project with an export (.gamestudio/export/<scene>.scene.json). The demo's
// index.html and src/ are copied in, node_modules is a junction to the repo's, and the project is
// served by its own Vite dev server on a free port with the gamestudio() plugin; gamestudio.json
// must give ephemeral hub ports (0), so a dev server the user runs is never touched. The first load
// is cold (Vite optimizes and transforms); the others run with the browser cache disabled. The
// junction is removed again at the end; the caller deletes the project.

import { cp, readFile, rm, symlink, writeFile } from 'node:fs/promises';
import { existsSync, unlinkSync } from 'node:fs';
import net from 'node:net';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import puppeteer from 'puppeteer-core';
import { createServer } from 'vite';
import { gamestudio } from '@gamestudio/devserver';
import { findBrowser } from '../../runtime/scripts/programs.mjs';

const REPO = fileURLToPath(new URL('../..', import.meta.url));
const DEMO = path.join(REPO, 'examples', 'demo-game');

function arg(name, fallback) {
  const i = process.argv.indexOf(`--${name}`);
  return i >= 0 && i + 1 < process.argv.length ? process.argv[i + 1] : fallback;
}

const PROJECT = path.resolve(arg('project', ''));
const SCENE = arg('scene', 'main');
const LOADS = Math.max(1, Number(arg('loads', '3')));
const OUT = arg('out', '');
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function freePort() {
  return new Promise((resolve, reject) => {
    const srv = net.createServer();
    srv.once('error', reject);
    srv.listen(0, '127.0.0.1', () => {
      const { port } = srv.address();
      srv.close(() => resolve(port));
    });
  });
}

async function prepare() {
  if (!existsSync(path.join(PROJECT, 'gamestudio.json'))) throw new Error(`not a project: ${PROJECT}`);
  const config = JSON.parse(await readFile(path.join(PROJECT, 'gamestudio.json'), 'utf8'));
  if (config.link?.editorPort !== 0 || config.link?.gamePort !== 0) {
    config.link = { editorPort: 0, gamePort: 0 }; // never the ports a user's dev server listens on
    await writeFile(path.join(PROJECT, 'gamestudio.json'), JSON.stringify(config, null, 2));
  }
  await cp(path.join(DEMO, 'index.html'), path.join(PROJECT, 'index.html'));
  await cp(path.join(DEMO, 'src'), path.join(PROJECT, 'src'), { recursive: true });
  const modules = path.join(PROJECT, 'node_modules');
  if (!existsSync(modules)) await symlink(path.join(REPO, 'node_modules'), modules, 'junction');
}

function cleanup() {
  const modules = path.join(PROJECT, 'node_modules');
  try {
    unlinkSync(modules); // the junction only, never the repo's node_modules
  } catch {
    // already gone
  }
}

/** Waits for window.__gs.ready across the reloads Vite may do while it optimizes dependencies. */
async function waitReady(page, timeout) {
  const end = Date.now() + timeout;
  for (;;) {
    try {
      await page.waitForFunction(() => window.__gs?.ready === true, { timeout: Math.max(1000, end - Date.now()), polling: 10 });
      return;
    } catch (error) {
      if (Date.now() > end) throw error;
      if (!/context|navigat|detached|Target closed/i.test(String(error))) throw error;
      await sleep(50);
    }
  }
}

async function load(browser, url, cold) {
  const page = await browser.newPage();
  await page.setViewport({ width: 1280, height: 720 });
  if (!cold) await page.setCacheEnabled(false);
  const errors = [];
  page.on('pageerror', (e) => errors.push(e.message));
  page.on('console', (m) => m.type() === 'error' && errors.push(m.text()));
  // Stamp the moment the scene is ready on the page's own clock.
  await page.evaluateOnNewDocument(() => {
    const tick = () => {
      if (window.__gs?.ready) window.__benchReadyAt = performance.now();
      else setTimeout(tick, 2);
    };
    tick();
  });
  const start = Date.now();
  await page.goto(`${url}?scene=${encodeURIComponent(SCENE)}`, { waitUntil: 'load', timeout: 180000 });
  await waitReady(page, 180000);
  const wall = (Date.now() - start) / 1000;
  await sleep(100);
  const info = await page.evaluate(() => {
    const nav = performance.getEntriesByType('navigation')[0];
    const files = performance
      .getEntriesByType('resource')
      .filter((r) => /__gs\/export\//.test(r.name))
      .map((r) => ({
        file: decodeURIComponent(r.name.split('__gs/export/')[1].split('?')[0]),
        start: +(r.startTime / 1000).toFixed(3),
        seconds: +(r.duration / 1000).toFixed(3),
        bytes: r.encodedBodySize || r.transferSize || 0,
      }));
    const scripts = performance.getEntriesByType('resource').filter((r) => r.initiatorType === 'script' || /\.(ts|js)(\?|$)/.test(r.name));
    return {
      readyAt: window.__benchReadyAt ? +(window.__benchReadyAt / 1000).toFixed(3) : null,
      domContentLoaded: nav ? +(nav.domContentLoadedEventEnd / 1000).toFixed(3) : null,
      modules: scripts.length,
      files,
      entities: window.__gs?.world?.size ?? null,
      renderer: window.__gs?.game?.three?.renderer?.kind ?? null,
      state: window.__gs?.state ?? null,
    };
  });
  await page.close();
  const manifest = info.files.find((f) => f.file.endsWith('.scene.json'));
  // Manifest request -> ready: the scene load itself, without the page's module loading.
  const sceneLoad = manifest && info.readyAt != null ? +(info.readyAt - manifest.start).toFixed(3) : null;
  return { cold, wall: +wall.toFixed(3), sceneLoad, ...info, errors: errors.slice(0, 5) };
}

async function main() {
  await prepare();
  const executablePath = findBrowser();
  if (!executablePath) throw new Error('No Edge, Chrome or Chromium found; set GS_BROWSER');
  const port = await freePort();
  const server = await createServer({
    root: PROJECT,
    configFile: false,
    plugins: [gamestudio()],
    cacheDir: path.join(PROJECT, '.vite-cache'),
    logLevel: process.env.GS_VITE_LOG ?? 'warn',
    server: { port, strictPort: true, host: '127.0.0.1', fs: { allow: [PROJECT, REPO] } },
  });
  let browser;
  const loads = [];
  try {
    await server.listen();
    const url = server.resolvedUrls.local[0];
    console.log(`dev server ${url} (own port; hub on ephemeral ports)`);
    browser = await puppeteer.launch({
      executablePath,
      headless: true,
      args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--ignore-gpu-blocklist', '--no-first-run', '--window-size=1280,720'],
    });
    for (let i = 0; i < LOADS; i++) {
      const result = await load(browser, url, i === 0);
      loads.push(result);
      const glbs = result.files.filter((f) => f.file.endsWith('.glb'));
      console.log(
        `load ${i + 1}${i === 0 ? ' (cold)' : ''}: ready ${result.readyAt ?? '?'} s after navigation, ` +
          `${result.sceneLoad ?? '?'} s after the manifest request (${result.wall} s wall); ` +
          `${glbs.length} GLB(s) ${(glbs.reduce((n, f) => n + f.bytes, 0) / 1048576).toFixed(1)} MB; ${result.entities} entities; ${result.renderer}`,
      );
    }
  } finally {
    if (browser) await browser.close().catch(() => {});
    await server.close().catch(() => {});
    cleanup();
  }
  const warm = loads.filter((l) => !l.cold);
  const best = (key) => {
    const values = warm.map((l) => l[key]).filter((v) => v != null);
    return values.length ? Math.min(...values) : null;
  };
  const summary = {
    browser: path.basename(executablePath),
    cold_ready_s: loads[0]?.readyAt ?? null,
    cold_scene_load_s: loads[0]?.sceneLoad ?? null,
    warm_ready_s: best('readyAt'),
    warm_scene_load_s: best('sceneLoad'),
    loads,
  };
  if (OUT) await writeFile(OUT, JSON.stringify(summary, null, 2));
  return summary;
}

main().then(
  () => process.exit(0),
  async (error) => {
    console.error(error?.stack ?? String(error));
    cleanup();
    await rm(path.join(PROJECT, '.vite-cache'), { recursive: true, force: true }).catch(() => {});
    process.exit(1);
  },
);
