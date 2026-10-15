// SPDX-License-Identifier: MIT
// End-to-end test of the project templates: New Project in Blender, then each game in a browser.
//
//   node tools/e2e/templates_e2e.mjs [template ...] [--out DIR] [--keep] [--skip-scaffold] [--gpu] [--blender PATH]
//
// 1. tools/e2e/templates_e2e.py runs stock Blender (scratch profile, this checkout's extension) and
//    creates one project per template with New Project: scaffold, linked packages and `npm install`,
//    `npm run registry`, the template's level script, export.
// 2. Per project: its own `tsc`, then its own dev server on a free port, opened in headless Edge,
//    Chrome or Chromium.
// 3. Plays each game with real keyboard and mouse input and checks it: no console errors and no
//    reloads; the title screen opens on the first start and Enter presses Play (starter: its gem, a 3D
//    model from Title.ui.glb, is drawn and turns; first Down + Enter opens Options, whose Music slider
//    steps the music volume, whose Language button switches the menus to French and back, and Back
//    returns); starter: the player walks into a pickup and the Music object plays the theme on the music
//    channel; platformer:
//    running, coins and a jump; shooter: the player moves, a wave spawns and shooting lowers its count;
//    driving: the car drives, starts the lap, passes checkpoint 1 and steers, while the HUD canvas shows
//    the start hint, then the lap, race time, speed and split banner, and the theme plays; runner: it runs by itself,
//    changes lanes and jumps, then seeded 40 s runs on the test clock check the chunk streaming (chunks
//    come and go, the entity count stays bounded), coins, debris and that one seed builds one track,
//    while its HUD canvas shows the distance, score, coins, the power-up rows and the 500 m banner.
//    Then Escape opens and closes the pause menu (starter: its Options, and Escape back), the game's own
//    ctx.vfx bursts and ambient ParticleEmitters are counted, and each game is driven to its end screen
//    on the test clock (starter: every pickup; platformer: the star; shooter: game over; driving: the last
//    lap; runner: a crash), whose Restart starts a fresh run without the title. Every template's screens
//    are game UI canvases made in Blender (ui/ui.blend: Title, Pause, Options, its end screens and HUD;
//    the starter's built by projects.build_starter_ui, the genres' by their tools/build_ui.py).
//    Screenshots go to OUT/screenshots.
//
// --gpu renders with the GPU (ANGLE on D3D11) instead of SwiftShader: several times faster. The
// checks go by game state and game time (ctx.time), never the wall clock, so the slow SwiftShader
// default passes too: below 10 fps the game runs slower than real time, and the waits with it.
// Env: BLENDER (or --blender), GS_BROWSER (default: Edge, Chrome or Chromium; both found by
// runtime/scripts/programs.mjs), GS_PYTHON (the python that runs templates_e2e.py). The projects are
// deleted unless --keep; OUT defaults to <tmp>/gs-templates-e2e.

import { spawn, spawnSync } from 'node:child_process';
import { existsSync } from 'node:fs';
import { mkdir, readFile, readdir, rm, writeFile } from 'node:fs/promises';
import net from 'node:net';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import puppeteer from 'puppeteer-core';
import { findBlender, findBrowser } from '../../runtime/scripts/programs.mjs';

const REPO = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..');
const argv = process.argv.slice(2);
const flag = (name) => argv.includes(name);
const option = (name) => (argv.includes(name) ? argv[argv.indexOf(name) + 1] : undefined);
const OUT = path.resolve(option('--out') ?? path.join(os.tmpdir(), 'gs-templates-e2e'));
const PROJECTS = path.join(OUT, 'projects');
const SHOTS = path.join(OUT, 'screenshots');
const wanted = argv.filter((a, i) => !a.startsWith('--') && !['--out', '--blender'].includes(argv[i - 1]));
const BROWSER = findBrowser();
const BLENDER = option('--blender') ?? findBlender(); // templates_e2e.py looks itself when this is undefined
const PYTHON = process.env.GS_PYTHON ?? (process.platform === 'win32' ? 'python' : 'python3');

const results = [];
function check(template, name, ok, detail = '') {
  results.push({ template, name, ok });
  console.log(`${ok ? 'PASS' : 'FAIL'}  [${template}] ${name}${detail ? `  (${detail})` : ''}`);
  return ok;
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
async function waitFor(fn, { timeout = 15000, interval = 100 } = {}) {
  const end = Date.now() + timeout;
  for (;;) {
    const value = await fn();
    if (value || Date.now() > end) return value;
    await sleep(interval);
  }
}

function freePort() {
  return new Promise((resolve, reject) => {
    const server = net.createServer();
    server.once('error', reject);
    server.listen(0, '127.0.0.1', () => {
      const { port } = server.address();
      server.close(() => resolve(port));
    });
  });
}

/** The project's own dev server (`npm run dev`, minus npm) on `port`. */
async function startDevServer(project, port) {
  const proc = spawn(process.execPath, [path.join(project, 'node_modules', 'vite', 'bin', 'vite.js'), '--port', String(port), '--strictPort', '--host', '127.0.0.1'], {
    cwd: project,
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  let output = '';
  proc.stdout.on('data', (d) => (output += d));
  proc.stderr.on('data', (d) => (output += d));
  const url = `http://127.0.0.1:${port}/`;
  const up = await waitFor(async () => {
    if (proc.exitCode !== null) return false;
    try {
      return (await fetch(url)).ok;
    } catch {
      return false;
    }
  }, { timeout: 60000, interval: 250 });
  if (!up) {
    proc.kill();
    throw new Error(`dev server did not start:\n${output}`);
  }
  return { url, output: () => output, stop: () => proc.kill() };
}

// --- Browser-side helpers (run with page.evaluate) ----------------------------------------------

/** World position of the first entity with `component`, or null. */
const positionOf = (page, component) =>
  page.evaluate((c) => {
    const e = window.__gs.world.query(c)[0];
    if (!e) return null;
    e.object3d.updateWorldMatrix(true, false);
    const m = e.object3d.matrixWorld.elements;
    return [m[12], m[13], m[14]];
  }, component);
const countOf = (page, component) => page.evaluate((c) => window.__gs.world.query(c).length, component);
/** A HUD value: a ctx.hud entry (`score`), or what a game UI canvas text shows (`HUD/Score`, state().ui.texts). */
const hudText = (page, key) =>
  page.evaluate(
    (k) =>
      k.includes('/')
        ? (window.__gs.game.ui.state().texts?.[k] ?? '')
        : (document.querySelector(`.gs-hud [data-key="${k}"]`)?.textContent ?? ''),
    key,
  );
/** The game's state and its open screen (ctx.ui): { state, screen, focus, buttons: [{ label, x, y, ... }] }. */
const uiState = (page) =>
  page.evaluate(() => {
    const game = window.__gs.game;
    return { state: game.state, ...game.ui.state() };
  });
const labels = (ui) => ui.buttons.map((b) => b.label).join(' / ');
/** The labels in alphabetical order: a canvas lists its buttons in element order, not the order on screen. */
const labelSet = (ui) => ui.buttons.map((b) => b.label).sort().join(' / ');
/** The menus' names: every template's are Blender canvases (code-only `ctx.ui.screen` menus would be `title` and `pause`). */
const SCREENS = { starter: { title: 'Title', pause: 'Pause' }, shooter: { title: 'Title', pause: 'Pause' }, platformer: { title: 'Title', pause: 'Pause' }, driving: { title: 'Title', pause: 'Pause' }, runner: { title: 'Title', pause: 'Pause' } };
const screenOf = (template, screen) => SCREENS[template]?.[screen] ?? screen;
/** The title's and the pause menu's buttons (labelSet); the fallback is code-only screens'. */
const MENU_BUTTONS = {
  driving: { title: 'Options / Play', pause: 'Options / Restart / Resume' },
  starter: { title: 'Options / Play', pause: 'Options / Restart / Resume' },
  shooter: { title: 'Options / Play', pause: 'Options / Restart / Resume' },
  platformer: { title: 'Options / Play', pause: 'Options / Restart / Resume' },
  runner: { title: 'Options / Play', pause: 'Options / Restart / Resume' },
};
const buttonsOf = (template, screen) => MENU_BUTTONS[template]?.[screen] ?? { title: 'Play', pause: 'Restart / Resume' }[screen];
/** Waits until `screen` (null: no screen) is open and the game is in `state`; returns the UI state or null. */
const waitForScreen = (page, screen, state, timeout = 30000) =>
  waitFor(async () => {
    const ui = await uiState(page);
    return ui.screen === screen && ui.state === state ? ui : null;
  }, { timeout, interval: 100 });
/** Records the presets of the game's ctx.vfx.burst calls in window.__bursts (ctx.vfx is game.vfx). */
const recordBursts = (page) =>
  page.evaluate(() => {
    const vfx = window.__gs.game.vfx;
    const bursts = (window.__bursts = []);
    const burst = vfx.burst.bind(vfx);
    vfx.burst = (at, effect, options) => {
      bursts.push(typeof effect === 'string' ? effect : (effect?.preset ?? 'custom'));
      return burst(at, effect, options);
    };
  });
const bursts = (page) => page.evaluate(() => [...window.__bursts]);
const dist = (a, b) => Math.hypot(a[0] - b[0], a[1] - b[1], a[2] - b[2]);
const fmt = (v) => `[${v.map((n) => n.toFixed(1)).join(', ')}]`;

/** Screen position (CSS px) of the nearest entity with `component` to the player, aimed `lift` m above its origin. */
const screenOfNearest = (page, component, lift) =>
  page.evaluate(
    ({ component, lift }) => {
      const { world, game } = window.__gs;
      const player = world.query('CharacterController')[0];
      const targets = world.query(component);
      if (!player || targets.length === 0) return null;
      const p = player.object3d.position;
      const nearest = targets.reduce((a, b) => (a.object3d.position.distanceTo(p) <= b.object3d.position.distanceTo(p) ? a : b));
      const v = nearest.object3d.position.clone();
      v.y += lift;
      v.project(game.three.camera);
      const rect = game.canvas.getBoundingClientRect();
      return { x: rect.left + ((v.x + 1) / 2) * rect.width, y: rect.top + ((1 - v.y) / 2) * rect.height, id: nearest.id };
    },
    { component, lift },
  );

// Headless rendering is slow (SwiftShader, a busy CI runner), and the game runs slower than real time
// below 10 fps (ctx.dt is capped at 0.1 s). So the scenarios wait on game time, not the wall clock.

/** Game seconds since the scene started playing (ctx.time). */
const gameTime = (page) => page.evaluate(() => window.__gs.test.status().gameTime);

/**
 * Polls `fn` until it returns something truthy or `seconds` of game time pass, whatever that takes in
 * real time (`wall` caps it, in case the game stops). Returns the last value.
 */
async function waitForGame(page, fn, seconds, { wall = 600000, interval = 100 } = {}) {
  const end = (await gameTime(page)) + seconds;
  const deadline = Date.now() + wall;
  for (;;) {
    const value = await fn();
    if (value || (await gameTime(page)) >= end || Date.now() > deadline) return value;
    await sleep(interval);
  }
}

/** The player's animation (`test.state()`): its graph, state, clip and parameters. */
const animationOf = (page) =>
  page.evaluate(() => window.__gs.test.state({ component: 'CharacterController', limit: 1 }).entities[0]?.animation ?? null);

/** Lets `seconds` of game time pass. */
const passGame = (page, seconds) => waitForGame(page, () => false, seconds, { interval: 50 });

/** Holds `key` for `seconds` of game time. */
async function hold(page, key, seconds) {
  await page.keyboard.down(key);
  await passGame(page, seconds);
  await page.keyboard.up(key);
}

// --- Scenarios -----------------------------------------------------------------------------------

async function platformer(page, shot) {
  const t = 'platformer';
  const start = await positionOf(page, 'CharacterController');
  // Go by position, and stop well before the meadow's edge (runtime z = -4): the release may land a
  // frame or two late.
  await page.keyboard.down('KeyW');
  await waitForGame(page, async () => (await positionOf(page, 'CharacterController'))[2] < 0.3, 6);
  await shot('running');
  const running = await animationOf(page);
  await page.keyboard.up('KeyW');
  check(t, 'the Player animation graph runs (Move state, Run clip)', running?.graph === 'Player' && running.state === 'Move' && running.clip === 'Run',
    JSON.stringify(running && { state: running.state, clip: running.clip, speed: running.params?.speed }));
  const moved = await positionOf(page, 'CharacterController');
  check(t, 'W moves the player forward (toward the course)', start[2] - moved[2] > 3.5, `${fmt(start)} -> ${fmt(moved)}`);
  // The HUD canvas (ui/ui.blend): "{coins} / {coinTotal}", the clock "{time:m:ss}" and a heart per life (UiShow).
  const coins = await hudText(page, 'HUD/Coins');
  check(t, 'walking through the first coins collects them (the HUD canvas counts them)', /^[2-9]\d* \/ 1[0-9]$/.test(coins), coins);
  const hud = await page.evaluate(hudOfPlatformer);
  check(t, 'the HUD shows the clock and five hearts', /^0:0\d$/.test(hud.clock) && hud.hearts === 5, JSON.stringify(hud));
  let ground = (await positionOf(page, 'CharacterController'))[1];
  await waitForGame(page, async () => {
    const y = (await positionOf(page, 'CharacterController'))[1];
    const settled = Math.abs(y - ground) < 0.01;
    ground = y;
    return settled;
  }, 3, { interval: 250 });
  await page.keyboard.down('Space');
  let peak = ground;
  const airborne = new Set();
  await waitForGame(page, async () => {
    peak = Math.max(peak, (await positionOf(page, 'CharacterController'))[1]);
    airborne.add((await animationOf(page))?.state);
    return peak - ground > 1.0;
  }, 2, { interval: 30 });
  await page.keyboard.up('Space');
  check(t, 'Space jumps', peak - ground > 0.8, `peak ${(peak - ground).toFixed(2)} m`);
  check(t, 'the jump animates (Jump or Air state)', airborne.has('Jump') || airborne.has('Air'), [...airborne].join(', '));
  const landed = await waitForGame(page, async () => (await animationOf(page))?.state === 'Move', 3);
  check(t, 'it lands back in the Move state', landed, (await animationOf(page))?.state);
  // Out to the stepping stones: the camera shows the course ahead.
  await hold(page, 'KeyW', 0.15); // about a metre: the meadow's edge is near
  await passGame(page, 0.5);
  await shot('course');
  // The level's Music object: AudioSource { assets/music/theme.ogg, loop, autoplay, channel music, volume 0.5 }.
  const music = await waitFor(async () => {
    const sounds = await page.evaluate(() =>
      window.__gs.game.audio.sounds.filter((s) => /theme/.test(s.clip)).map((s) => ({ channel: s.channel, state: s.state, volume: s.volume })));
    return sounds.some((s) => s.channel === 'music' && s.state === 'playing') ? sounds : null;
  }, { timeout: 15000 });
  check(t, 'the Music object plays the theme on the music channel', !!music, JSON.stringify(music));

  // On the test clock: the last life lost (a fall) opens GameOver with the coins, every heart gone and Try again.
  const over = await page.evaluate(async () => {
    const { test, game } = window.__gs;
    await test.begin();
    game.store.lives = 1;
    test.place(null, { position: [0, -10, -2] }); // below the fall limit (y = -3)
    for (let i = 0; i < 120 && test.status().screen !== 'GameOver'; i++) await test.advance(5);
    test.render();
    const ui = game.ui.state();
    return { screen: ui.screen, overCoins: ui.texts['GameOver/OverCoins'], buttons: ui.buttons.map((b) => b.label).join(' / ') };
  });
  Object.assign(over, await page.evaluate(hudOfPlatformer));
  await page.evaluate(() => window.__gs.test.end());
  await sleep(400); // its open animation
  await shot('gameover');
  check(t, 'losing the last life opens GameOver (coins, no hearts, Try again)',
    over.screen === 'GameOver' && /^Coins \d+ \/ 1\d$/.test(over.overCoins) && over.hearts === 0 && over.buttons === 'Try again', JSON.stringify(over));
  await page.keyboard.press('Enter');
  const fresh = await waitFor(async () => {
    const ui = await uiState(page);
    const now = await page.evaluate(hudOfPlatformer);
    return ui.screen === null && ui.state === 'playing' && /^0 \/ 1\d$/.test(now.coins) && now.hearts === 5 ? now : null;
  }, { timeout: 60000 });
  check(t, 'Try again starts a fresh run (no coins, five hearts), without the title', !!fresh,
    JSON.stringify(fresh ?? { ...(await uiState(page)), hud: await page.evaluate(hudOfPlatformer) }));
  await passGame(page, 1.5); // the fresh level's ambient emitters get going
}

/** The platformer's HUD canvas: its coins and clock texts and how many of the five hearts show (runs in the page). */
function hudOfPlatformer() {
  const ui = window.__gs.game.ui;
  const texts = ui.state().texts;
  return {
    coins: texts['HUD/Coins'],
    clock: texts['HUD/Clock'],
    hearts: [1, 2, 3, 4, 5].filter((i) => ui.element(`HUD/Lives${i}`)?.visible).length,
  };
}

async function shooter(page, shot, logs) {
  const t = 'shooter';
  // The HUD is a game UI canvas (ui/ui.blend) bound to ctx.store: health, wave, enemiesLeft, score.
  const texts = () => page.evaluate(() => window.__gs.game.ui.state().texts ?? {});
  const hud = await texts();
  check(t, 'the HUD canvas shows full health, "Get ready", the score and the controls hint',
    hud['HUD/Health'] === '100' && hud['HUD/GetReady'] === 'Get ready' && hud['HUD/Score'] === 'Score 0' && /WASD move/.test(hud['HUD/ControlsHint'] ?? ''),
    JSON.stringify(hud));
  const start = await positionOf(page, 'CharacterController');
  await page.keyboard.down('KeyA');
  await waitForGame(page, async () => dist(start, await positionOf(page, 'CharacterController')) > 2, 2);
  await page.keyboard.up('KeyA');
  const moved = await positionOf(page, 'CharacterController');
  check(t, 'A moves the player', dist(start, moved) > 1.5, `${fmt(start)} -> ${fmt(moved)}`);
  // The HUD reads "Wave 1 · N left" (alive + still to come). Wait until the whole wave is in: a 3 s
  // breather, then an enemy every 0.6 s. The "Wave 1" banner shows for 1.5 s as it begins.
  const left = async () => Number((await hudText(page, 'HUD/Wave')).match(/Wave 1 · (\d+) left/)?.[1] ?? NaN);
  let banner = '';
  const full = await waitForGame(page, async () => {
    banner ||= (await texts())['HUD/WaveBanner'] ?? '';
    const n = await countOf(page, 'Enemy');
    return n > 0 && n === (await left()) ? n : 0;
  }, 15, { interval: 100 });
  check(t, 'the first wave spawns its enemies', full > 0, `${full} enemies, HUD "${await hudText(page, 'HUD/Wave')}"`);
  check(t, 'the HUD banner announces the wave', banner === 'Wave 1', `banner "${banner}"`);
  const scoreBefore = await hudText(page, 'HUD/Score');
  let remaining = full;
  let shotTaken = false;
  await page.mouse.down();
  // Aim at the nearest enemy until one is down, for up to 20 s of game time.
  await waitForGame(page, async () => {
    const target = await screenOfNearest(page, 'Enemy', 1.0);
    if (target) await page.mouse.move(target.x, target.y, { steps: 2 });
    remaining = Math.min(remaining, await countOf(page, 'Enemy'));
    if (!shotTaken) {
      await shot('combat');
      shotTaken = true;
    }
    return remaining < full;
  }, 20, { interval: 60 });
  await page.mouse.up();
  check(t, 'shooting enemies lowers their count', remaining < full && (await left()) < full, `${full} -> ${remaining}, HUD "${await hudText(page, 'HUD/Wave')}"`);
  const score = await waitForGame(page, async () => {
    const text = await hudText(page, 'HUD/Score');
    return text !== scoreBefore && /^Score [1-9]/.test(text) ? text : '';
  }, 1);
  check(t, 'kills raise the score (the HUD canvas shows it)', !!score, `${scoreBefore} -> ${await hudText(page, 'HUD/Score')}`);
  console.log(`      game log: ${logs.filter((l) => /defeated|Wave|spawn/.test(l)).join(' | ')}`);
  await shot('after');
  // The level's Music object: AudioSource { assets/music/theme.ogg, loop, autoplay, channel music, volume 0.5 }.
  const music = await waitFor(async () => {
    const sounds = await page.evaluate(() =>
      window.__gs.game.audio.sounds.filter((s) => /theme/.test(s.clip)).map((s) => ({ channel: s.channel, state: s.state, volume: s.volume })));
    return sounds.some((s) => s.channel === 'music' && s.state === 'playing') ? sounds : null;
  }, { timeout: 15000 });
  check(t, 'the Music object plays the theme on the music channel', !!music, JSON.stringify(music));
}

async function driving(page, shot, logs) {
  const t = 'driving';
  const start = await positionOf(page, 'Car');
  // The HUD canvas (ui/ui.blend): before the start line only its hint shows (UiShow lap = 0), not the lap card.
  const hint = await hudText(page, 'HUD/StartText');
  const lapCard = await hudText(page, 'HUD/LapText');
  check(t, 'the HUD shows the start-line hint before the first lap', hint === 'Drive through the start line' && lapCard === '',
    `hint "${hint}", lap "${lapCard}"`);
  await page.keyboard.down('KeyW');
  // RaceSystem shows the split banner (HUD/Split) for a moment at the line and at each gate: collect what it says.
  const banners = new Set();
  const passedOne = () => logs.some((l) => /Checkpoint 1\b/.test(l));
  await waitForGame(page, async () => {
    const split = await hudText(page, 'HUD/SplitText');
    if (split) banners.add(split);
    return passedOne() && [...banners].some((b) => b.startsWith('Checkpoint 1/'));
  }, 15, { interval: 50 });
  const passed = passedOne();
  await shot('racing');
  const moved = await positionOf(page, 'Car');
  check(t, 'W drives the car forward', dist(start, moved) > 30, `${fmt(start)} -> ${fmt(moved)}`);
  check(t, 'crossing the start line starts the lap', logs.some((l) => /Lap 1 started/.test(l)), logs.filter((l) => /Lap/.test(l)).join(' | '));
  check(t, 'the car passes checkpoint 1', passed, logs.filter((l) => /Checkpoint/.test(l)).join(' | '));
  check(t, 'the split banner says GO! and the checkpoint split', banners.has('GO!') && [...banners].some((b) => /^Checkpoint 1\/3 +\d:\d\d\.\d\d$/.test(b)),
    [...banners].join(' | '));
  // The HUD binds what RaceSystem keeps in ctx.store: lap, laps, raceTime ({raceTime:m:ss.00}) and speedKmh.
  const hud = await page.evaluate(() => window.__gs.game.ui.state().texts);
  check(t, 'the HUD canvas shows the lap, the race time and the speed',
    hud['HUD/LapText'] === 'Lap 1/3' && /^0:\d\d\.\d\d$/.test(hud['HUD/RaceTime'] ?? '') && Number(hud['HUD/Speed']) > 20 &&
      hud['HUD/SpeedUnit'] === 'km/h' && !('HUD/StartText' in hud),
    JSON.stringify(Object.fromEntries(Object.entries(hud).filter(([k]) => k.startsWith('HUD/')))));
  // The level's Music object: AudioSource { assets/music/theme.ogg, loop, autoplay, channel music, volume 0.5 }.
  const music = await page.evaluate(() =>
    window.__gs.game.audio.sounds.filter((s) => /theme/.test(s.clip)).map((s) => ({ channel: s.channel, state: s.state, volume: s.volume })));
  check(t, 'the Music object plays the theme on the music channel', music.some((s) => s.channel === 'music' && s.state === 'playing'),
    JSON.stringify(music));
  const yaw = () =>
    page.evaluate(() => {
      const q = window.__gs.world.query('Car')[0].object3d.quaternion;
      return Math.atan2(2 * (q.w * q.y + q.x * q.z), 1 - 2 * (q.y * q.y + q.x * q.x)); // heading about Y
    });
  const before = await yaw();
  await page.keyboard.down('KeyD');
  const turned = await waitForGame(page, async () => Math.abs((await yaw()) - before) > 0.3, 3);
  await page.keyboard.up('KeyD');
  await page.keyboard.up('KeyW');
  check(t, 'D steers the car', turned, `heading ${before.toFixed(2)} -> ${(await yaw()).toFixed(2)}`);
  await passGame(page, 0.2);
  await shot('steer');
}

async function starter(page, shot) {
  const t = 'starter';
  const start = await positionOf(page, 'CharacterController');
  await page.keyboard.down('KeyW');
  await waitForGame(page, async () => start[2] - (await positionOf(page, 'CharacterController'))[2] > 7.5, 4);
  await page.keyboard.up('KeyW');
  const moved = await positionOf(page, 'CharacterController');
  check(t, 'W moves the player', start[2] - moved[2] > 7.5, `${fmt(start)} -> ${fmt(moved)}`);
  // The score is the HUD canvas's text "Score {score}" (ui/ui.blend), bound to ctx.store.score.
  const score = await waitForGame(page, async () => /^Score [1-9]/.test(await hudText(page, 'HUD/Score')), 1);
  check(t, 'walking into a pickup collects it (the HUD canvas shows it)', score, await hudText(page, 'HUD/Score'));
  await shot('walked');
  // The level's Music object: AudioSource { assets/music/theme.ogg, loop, autoplay, channel music, volume 0.5 }.
  const music = await waitFor(async () => {
    const sounds = await page.evaluate(() =>
      window.__gs.game.audio.sounds.filter((s) => /theme/.test(s.clip)).map((s) => ({ channel: s.channel, state: s.state, volume: s.volume })));
    return sounds.some((s) => s.channel === 'music' && s.state === 'playing') ? sounds : null;
  }, { timeout: 15000 });
  check(t, 'the Music object plays the theme on the music channel', !!music, JSON.stringify(music));
}

/**
 * Templates with an Options canvas: Down + Enter on the title (or the pause menu) opens it with Back focused;
 * Up reaches `slider`, whose Left and Right keys step its channel's volume by 0.1 (written through its value
 * path, audio.<channel>); Back (action previous) returns to the title, Escape (back: previous) to the pause menu.
 */
const OPTIONS = {
  driving: { screen: 'Options', back: 'Back', sliders: ['MusicSlider', 'SoundSlider'], slider: 'MusicSlider', channel: 'music' },
  starter: { screen: 'Options', back: 'Back', sliders: ['MusicSlider', 'SoundSlider'], slider: 'MusicSlider', channel: 'music', language: starterLanguage },
  shooter: { screen: 'Options', back: 'Back', sliders: ['MusicSlider', 'SoundSlider'], slider: 'MusicSlider', channel: 'music' },
  platformer: { screen: 'Options', back: 'Back', sliders: ['MusicSlider', 'SoundSlider'], slider: 'MusicSlider', channel: 'music' },
  runner: { screen: 'Options', back: 'Back', sliders: ['MusicSlider', 'SoundSlider'], slider: 'MusicSlider', channel: 'music' },
};
/** Presses `key` until the focused control's label is `label` (a slider without text: its name), at most `times`. */
async function focusOn(page, key, label, times = 4) {
  const focused = async () => (await uiState(page)).focus;
  for (let i = 0; i < times && (await focused()) !== label; i++) {
    const before = await focused();
    await page.keyboard.press(key);
    await waitFor(async () => (await focused()) !== before, { timeout: 3000 });
  }
  return (await focused()) === label;
}

async function optionsFromTitle(page, shot, template) {
  const o = OPTIONS[template];
  const onOptions = await focusOn(page, 'ArrowDown', 'Options', 1);
  await page.keyboard.press('Enter');
  const options = await waitForScreen(page, o.screen, 'paused', 10000);
  const sliders = (options?.buttons ?? []).filter((b) => b.kind === 'slider');
  check(template, `Down + Enter on the title opens ${o.screen} (${o.back} focused, sliders at full volume)`,
    onOptions && !!options && options.focus === o.back && sliders.map((b) => `${b.name} ${b.value}`).join(', ') === o.sliders.map((n) => `${n} 1`).join(', '),
    options ? `focus ${options.focus}, buttons ${options.buttons.map((b) => `${b.name}${b.kind === 'slider' ? ` ${b.value}` : ''}`).join(' / ')}` : JSON.stringify(await uiState(page)));
  const reached = await focusOn(page, 'ArrowUp', o.slider);
  const read = () =>
    page.evaluate(
      ({ slider, channel }) => ({
        value: window.__gs.game.ui.state().buttons.find((b) => b.name === slider)?.value,
        volume: window.__gs.game.audio.channel(channel).volume,
      }),
      { slider: o.slider, channel: o.channel },
    );
  const near = (a, b) => Math.abs(a - b) < 1e-6;
  await page.keyboard.press('ArrowLeft');
  const low = await waitFor(async () => {
    const r = await read();
    return near(r.value, 0.9) && near(r.volume, 0.9) ? r : null;
  }, { timeout: 5000 });
  await sleep(300); // the open animation has ended
  await shot('options');
  await page.keyboard.press('ArrowRight');
  const high = await waitFor(async () => {
    const r = await read();
    return near(r.value, 1) && near(r.volume, 1) ? r : null;
  }, { timeout: 5000 });
  check(template, `Up reaches ${o.slider}; Left and Right step the ${o.channel} volume (1 -> 0.9 -> 1)`, reached && !!low && !!high,
    `focus ${(await uiState(page)).focus}, after Left ${JSON.stringify(low)}, after Right ${JSON.stringify(high ?? (await read()))}`);
  if (o.language) await o.language(page, shot, template); // ends on this screen again, in the authored language
  const onBack = await focusOn(page, 'ArrowDown', o.back);
  await page.keyboard.press('Enter');
  const title = await waitForScreen(page, screenOf(template, 'title'), 'paused', 10000);
  check(template, `${o.back} returns to the title (Play focused)`, onBack && !!title && title.focus === 'Play',
    title ? `focus ${title.focus}, open ${title.open.join(', ')}` : JSON.stringify(await uiState(page)));
}

/** The UI language and every visible canvas text (`state().ui`). */
const languageState = (page) =>
  page.evaluate(() => {
    const ui = window.__gs.game.ui.state();
    return { locale: ui.locale, focus: ui.focus, texts: ui.texts };
  });

/**
 * The starter's Language button, the row under the sliders (from the Music slider: Down, Down): UiButton action
 * `locale` with no target, so a press goes to the next language, French from ui/locales/fr.json, and its label
 * `{ui.localeName}` names the language shown. Every menu text switches at once (Options here, then the title
 * after Retour), and a second press comes back to English. Ends on Options, in English, the button selected.
 */
async function starterLanguage(page, shot, template) {
  const reached = await focusOn(page, 'ArrowDown', 'English');
  await page.keyboard.press('Enter');
  const options = await waitFor(async () => {
    const s = await languageState(page);
    return s.locale === 'fr' && s.texts['Options/MusicLabel'] === 'Musique' ? s : null;
  }, { timeout: 5000 });
  const fr = options?.texts ?? {};
  check(template, 'Language switches the menus to French (Options: Musique, Son, Langue, Retour; the button says Français)',
    reached && !!options && options.focus === 'Français' && fr['Options/SoundLabel'] === 'Son' && fr['Options/LanguageLabel'] === 'Langue' &&
      fr['Options/BackButtonLabel'] === 'Retour' && fr['Options/LanguageButtonLabel'] === 'Français' && fr['Options/OptionsTitle'] === 'Options',
    JSON.stringify(options ?? (await languageState(page))));
  await shot('options-fr');
  const onBack = await focusOn(page, 'ArrowDown', 'Retour');
  await page.keyboard.press('Enter');
  const title = await waitForScreen(page, 'Title', 'paused', 10000);
  await sleep(400); // its open animation
  const titled = await languageState(page);
  check(template, 'the title follows in French (Jouer, Options, the subtitle and the controls)',
    onBack && !!title && labelSet(title) === 'Jouer / Options' && titled.focus === 'Jouer' && titled.texts['Title/PlayButtonLabel'] === 'Jouer' &&
      titled.texts['Title/Subtitle'] === 'Ramassez toutes les gemmes' && /^ZQSD\/WASD : se déplacer/.test(titled.texts['Title/Hint'] ?? ''),
    JSON.stringify(titled));
  await shot('title-fr');
  // Options again (Down: Options, the same word in French), then Language once more: back to English.
  const onOptions = await focusOn(page, 'ArrowDown', 'Options', 1);
  await page.keyboard.press('Enter');
  const again = await waitForScreen(page, 'Options', 'paused', 10000);
  const onLanguage = await focusOn(page, 'ArrowUp', 'Français');
  await page.keyboard.press('Enter');
  const english = await waitFor(async () => {
    const s = await languageState(page);
    return s.locale === 'en' && s.texts['Options/MusicLabel'] === 'Music' ? s : null;
  }, { timeout: 5000 });
  const saved = await page.evaluate(() => window.__gs.game.save.get('gs.locale'));
  check(template, 'Language again comes back to English (Music, Back; the button says English), and the game saves the choice',
    onOptions && !!again && onLanguage && !!english && english.focus === 'English' && english.texts['Options/BackButtonLabel'] === 'Back' &&
      english.texts['Options/LanguageButtonLabel'] === 'English' && saved === 'en',
    JSON.stringify({ english: english ?? (await languageState(page)), saved }));
}

/**
 * The starter's title: above the game's name the level's gem turns, a 3D model element (UiModel) that the game
 * draws from Title.ui.glb, lit, in its own view. Its screen rectangle must show the teal gem, and change as it
 * turns (the UI clock runs under the paused title).
 */
async function starterTitle(page, _shot, { warnings }) {
  const t = 'starter';
  const info = await page.evaluate(() => {
    const { game } = window.__gs;
    const gem = game.ui.element('Title/TitleGem');
    const box = game.canvas.getBoundingClientRect();
    const glb = performance.getEntriesByType('resource').filter((e) => /\/Title\.ui\.glb(\?|$)/.test(e.name))
      .map((e) => ({ status: e.responseStatus, bytes: e.encodedBodySize }));
    return { gem, offset: [box.left, box.top], glb };
  });
  /** Teal pixels (the gem material, #23B5D8, lit) in the gem's rectangle, and how much it changed since the last look. */
  const look = async () => {
    const r = info.gem.rect;
    const png = await page.screenshot({ encoding: 'base64', clip: { x: info.offset[0] + r.x, y: info.offset[1] + r.y, width: r.width, height: r.height } });
    return page.evaluate(async (b64) => {
      const img = new Image();
      img.src = `data:image/png;base64,${b64}`;
      await img.decode();
      const c = document.createElement('canvas');
      c.width = img.width;
      c.height = img.height;
      const g = c.getContext('2d');
      g.drawImage(img, 0, 0);
      const { data } = g.getImageData(0, 0, c.width, c.height);
      const last = window.__gemLook;
      let teal = 0;
      let changed = 0;
      for (let i = 0; i < data.length; i += 4) {
        if (data[i + 1] > 1.6 * data[i] + 20 && data[i + 2] > 1.6 * data[i] + 20) teal++;
        if (last && last.length === data.length && Math.abs(data[i] - last[i]) + Math.abs(data[i + 1] - last[i + 1]) + Math.abs(data[i + 2] - last[i + 2]) > 30) changed++;
      }
      window.__gemLook = data;
      return { teal, changed, pixels: data.length / 4 };
    }, png);
  };
  const ok = info.gem?.type === 'model' && info.gem.visible && info.gem.rect.width > 40;
  const first = ok ? await look() : null;
  await sleep(700);
  const second = ok ? await look() : null;
  check(t, 'the title shows its gem: a 3D model element loaded from Title.ui.glb, drawn teal',
    ok && info.glb.some((g) => g.status === 200 && g.bytes > 0) && first.teal > 400,
    JSON.stringify({ gem: info.gem, glb: info.glb, first }));
  check(t, 'the gem turns on the paused title (spin)', !!second && second.changed > 100, JSON.stringify(second));
  const ui = warnings.filter((w) => /Game UI|shader/i.test(w));
  check(t, 'no Game UI warnings (the model, the Shine on Play, the French table)', ui.length === 0, ui.join(' | ').slice(0, 800));
}
const TITLES = { starter: starterTitle };

async function optionsFromPause(page, template) {
  const o = OPTIONS[template];
  const onOptions = await focusOn(page, 'ArrowDown', 'Options', 1);
  await page.keyboard.press('Enter');
  const options = await waitForScreen(page, o.screen, 'paused', 10000);
  await page.keyboard.press('Escape');
  const back = await waitForScreen(page, screenOf(template, 'pause'), 'paused', 10000);
  check(template, `Options opens from the pause menu, and Escape goes back to it (still paused)`, onOptions && !!options && !!back,
    `${options ? `${o.screen} focus ${options.focus}` : 'no Options'}, then ${back ? `${back.screen} focus ${back.focus}` : JSON.stringify(await uiState(page))}`);
}

/** The runner's state: world position and its Runner fields (lane, distance, speed, magnet, shield). */
const runnerState = (page) =>
  page.evaluate(() => {
    const runner = window.__gs.world.query('Runner')[0];
    const p = runner.object3d.position; // a kinematic body: at the scene root
    return { x: p.x, y: p.y, z: p.z, ...runner.get('Runner') };
  });

/**
 * One seeded run on the test clock, from a fresh scene, with a shield and a magnet that never run out
 * (obstacles break, gaps bounce the runner back up, coins fly in). Samples the chunks, obstacles and
 * entity count every half second.
 */
const seededRun = (page, seed, seconds) =>
  page.evaluate(
    async ({ seed, seconds }) => {
      const { world, test, game } = window.__gs;
      await test.begin({ reset: true, seed });
      await test.advance(1); // RunSystem spawns the runner at PlayerStart
      game.ui.close(); // the Title canvas (firstStart: a test reset opens it again)
      const runner = world.query('Runner')[0].get('Runner');
      Object.assign(runner, { shield: 1e6, magnet: 1e6 });
      const chunks = new Map();
      const obstacles = new Map();
      const sizes = [];
      let alive = 0;
      let debris = 0;
      for (let step = 0; step < seconds * 60; step += 30) {
        await test.advance(30);
        for (const chunk of world.query('TrackChunk')) if (!chunks.has(chunk.id)) chunks.set(chunk.id, chunk.name);
        for (const o of world.query('Obstacle')) {
          const p = o.object3d.getWorldPosition(o.object3d.position.clone());
          if (!obstacles.has(o.id)) obstacles.set(o.id, `${o.name}@${p.x.toFixed(1)},${p.z.toFixed(1)}`);
        }
        debris = Math.max(debris, world.query('Debris').length);
        alive = Math.max(alive, world.query('TrackChunk').length);
        sizes.push(world.size);
      }
      test.render(); // the HUD canvas's bindings and UiShow follow the rendered frame
      return {
        chunks: [...chunks.values()],
        destroyed: [...chunks.keys()].filter((id) => !world.get(id)).length,
        obstacles: [...obstacles.values()],
        alive,
        sizes,
        debris,
        distance: runner.distance,
        coins: game.store.coins,
        hud: game.ui.state().texts,
      };
    },
    { seed, seconds },
  );

async function runner(page, shot) {
  const t = 'runner';
  const start = await runnerState(page);
  await passGame(page, 0.6);
  const ran = await runnerState(page);
  check(t, 'the runner runs forward by itself', start.z - ran.z > 4, `z ${start.z.toFixed(1)} -> ${ran.z.toFixed(1)}, ${ran.speed.toFixed(1)} m/s`);
  // The HUD canvas (ui/ui.blend) binds ctx.store: "{distance:0} m", "Score {score}", "{coins}"; the controls
  // show for the first seconds, and a power-up's row only while it runs (UiShow).
  // This run is not seeded: a pickup may already have started a power-up, so its row shows exactly while
  // its store value is above 0.
  const { hud, magnetLeft, shieldLeft } = await page.evaluate(() => ({
    hud: window.__gs.game.ui.state().texts,
    magnetLeft: window.__gs.game.store.magnetLeft ?? 0,
    shieldLeft: window.__gs.game.store.shieldLeft ?? 0,
  }));
  check(t, 'the HUD canvas shows the distance, score and coins, the controls, and power-up rows only while they run',
    /^[1-9]\d* m$/.test(hud['HUD/Distance']) && /^Score [1-9]\d*$/.test(hud['HUD/Score']) && /^\d+$/.test(hud['HUD/Coins']) &&
      /change lane/.test(hud['HUD/ControlsText']) && ('HUD/MagnetLabel' in hud) === magnetLeft > 0 &&
      ('HUD/ShieldLabel' in hud) === shieldLeft > 0,
    JSON.stringify({ hud, magnetLeft, shieldLeft }));
  // Lanes are 2.2 m apart: D goes right, A twice goes to the left lane.
  await page.keyboard.press('KeyD');
  const right = await waitForGame(page, async () => (await runnerState(page)).x > 2.0, 1);
  await shot('lane');
  await page.keyboard.press('KeyA');
  await waitForGame(page, async () => Math.abs((await runnerState(page)).x) < 0.2, 1);
  await page.keyboard.press('KeyA');
  const left = await waitForGame(page, async () => (await runnerState(page)).x < -2.0, 1);
  check(t, 'D and A switch lanes', !!right && !!left, `right ${!!right}, left ${!!left}, x ${(await runnerState(page)).x.toFixed(2)}`);
  const ground = (await runnerState(page)).y;
  await page.keyboard.press('Space');
  let peak = ground;
  await waitForGame(page, async () => {
    peak = Math.max(peak, (await runnerState(page)).y);
    return peak - ground > 1.0;
  }, 1, { interval: 30 });
  check(t, 'Space jumps', peak - ground > 0.8, `peak ${(peak - ground).toFixed(2)} m`);

  // 40 s of game time on the test clock: the track streams, and the same seed builds the same track.
  const a = await seededRun(page, 11, 40);
  await shot('streaming');
  const half = a.sizes.length / 2;
  const most = (sizes) => Math.max(...sizes);
  check(t, 'chunks spawn ahead and are destroyed behind', a.chunks.length >= 10 && a.destroyed >= a.chunks.length - a.alive && a.alive <= 9,
    `${a.chunks.length} chunks over ${a.distance.toFixed(0)} m, ${a.destroyed} destroyed, at most ${a.alive} at once: ${a.chunks.join(' ')}`);
  check(t, 'the entity count stays bounded', most(a.sizes) < 400 && most(a.sizes.slice(half)) <= 1.3 * most(a.sizes.slice(0, half)),
    `first 20 s at most ${most(a.sizes.slice(0, half))}, last 20 s at most ${most(a.sizes.slice(half))}`);
  check(t, 'coins are collected (with a magnet)', a.coins >= 20, `${a.coins} coins`);
  check(t, 'the shield breaks obstacles into debris prefabs', a.debris > 0, `${a.debris} debris at once, ${a.obstacles.length} obstacles`);
  check(t, 'the HUD follows the run: its coins, and the magnet and shield rows while they run',
    a.hud['HUD/Coins'] === String(a.coins) && a.hud['HUD/MagnetLabel'] === 'Magnet' && a.hud['HUD/ShieldLabel'] === 'Shield' &&
      a.hud['HUD/Distance'] === `${Math.floor(a.distance)} m` && !('HUD/ControlsText' in a.hud),
    JSON.stringify(a.hud));
  const b = await seededRun(page, 11, 20);
  const c = await seededRun(page, 12, 20);
  // Every 500 m RunSystem puts the milestone on the HUD's Banner ("{banner}", ctx.store.banner) for 1.5 s.
  const shown = await page.evaluate(async () => {
    const { world, test, game } = window.__gs;
    await test.begin({ reset: true, seed: 5 });
    await test.advance(1);
    game.ui.close(); // the Title canvas (firstStart: a test reset opens it again)
    await test.advance(20); // its close animation (0.25 s, in test steps) ends
    world.query('Runner')[0].get('Runner').distance = 500;
    await test.advance(2);
    test.render();
    return game.ui.state().texts['HUD/Banner'];
  });
  await shot('milestone');
  const gone = await page.evaluate(async () => {
    await window.__gs.test.advance(95);
    window.__gs.test.render();
    return window.__gs.game.ui.state().texts['HUD/Banner'];
  });
  check(t, 'every 500 m the HUD banner shows the milestone for 1.5 s', shown === '500 m!' && gone === '', `"${shown}", then "${gone}"`);
  await page.evaluate(() => window.__gs.test.end());
  const prefix = (run, n) => JSON.stringify([run.chunks.slice(0, n), run.obstacles.slice(0, n)]);
  const n = Math.min(b.chunks.length, b.obstacles.length);
  check(t, 'the same seed gives the same chunks and obstacles', n >= 6 && prefix(a, n) === prefix(b, n),
    `seed 11: ${b.chunks.slice(0, n).join(' ')} | ${b.obstacles.slice(0, 4).join(' ')}`);
  check(t, 'another seed gives another track', prefix(c, n) !== prefix(a, n), `seed 12: ${c.chunks.slice(0, n).join(' ')} | ${c.obstacles.slice(0, 4).join(' ')}`);
}

const SCENARIOS = { starter, platformer, shooter, driving, runner };

// --- Juice and endings ---------------------------------------------------------------------------------

/** Presets each game must have burst during its scenario (pickups, jumps, hits, kills, checkpoints). */
const JUICE = { starter: ['pickup'], platformer: ['pickup', 'dust'], shooter: ['sparkle', 'explosion'], driving: ['sparkle'], runner: ['pickup', 'dust', 'sparkle'] };
/** Ambient ParticleEmitters each level must have, at least (the starter has none). */
const AMBIENT = { platformer: 3, shooter: 4, driving: 1, runner: 1 };

/**
 * Each game's ending, reached on the test clock (window.__gs.test: whole fixed steps, as fast as the
 * machine allows, no drawing). `setup` runs in the page after test.begin(); the game then runs until
 * `burst` plays (a screenshot of the celebration) and until its `screen` opens. Restart (the screen's
 * first button) must start a fresh run: `reset` is the HUD entry and what it shows then.
 */
const ENDINGS = {
  starter: {
    screen: 'Win', burst: 'confetti', button: 'Play again', reset: ['HUD/Score', /^Score 0$/],
    setup: async () => {
      const { world, test } = window.__gs;
      for (const item of [...world.query('Collectible')]) {
        const p = item.object3d.getWorldPosition(item.object3d.position.clone());
        test.place(null, { position: [p.x, 0, p.z] }); // walk into each pickup
        await test.advance(4);
      }
    },
  },
  platformer: {
    screen: 'Win', burst: 'confetti', button: 'Play again', reset: ['HUD/Coins', /^0 \/ 1\d$/],
    // The Win canvas shows the run: its time and coins, and "New best time!" (a test's ctx.save starts empty)
    // instead of the best time.
    verify: () => {
      const texts = window.__gs.game.ui.state().texts;
      return /^Time \d:\d\d\.\d\d$/.test(texts['Win/WinTime']) && /^Coins \d+ \/ 1\d$/.test(texts['Win/WinCoins']) &&
        texts['Win/NewBest'] === 'New best time!' && !('Win/BestTime' in texts);
    },
    setup: async () => {
      const { world, test } = window.__gs;
      const star = world.query('Goal')[0];
      const p = star.object3d.getWorldPosition(star.object3d.position.clone());
      test.place(null, { position: [p.x, p.y - 1.4, p.z + 1] }); // on the summit, in front of the star
    },
  },
  shooter: {
    screen: 'GameOver', burst: 'explosion', button: 'Try again', reset: ['HUD/Score', /^Score 0$/],
    // Hit points are a component field (Health.current), so game_state and playtests can read them. The
    // GameOver canvas shows the run: its score and wave, and "New best score!" (a test's ctx.save starts empty).
    verify: () => {
      const texts = window.__gs.game.ui.state().texts;
      return window.__gs.world.query('Shooter')[0].get('Health').current === 0 && /^Score [1-9]\d*$/.test(texts['GameOver/OverScore']) &&
        /^Reached wave [1-9]/.test(texts['GameOver/OverWave']) && texts['GameOver/NewBest'] === 'New best score!' && !('GameOver/OverBest' in texts);
    },
    setup: async () => {
      const { world, test } = window.__gs;
      for (let i = 0; i < 60 && world.query('Enemy').length === 0; i++) await test.advance(10); // a wave is on its way
      for (const enemy of world.query('Enemy')) Object.assign(enemy.get('Enemy'), { damage: 1000, reach: 100 });
    },
  },

  driving: {
    screen: 'Results', burst: 'confetti', button: 'Race again', reset: ['HUD/StartText', /^Drive through the start line$/],
    // Results binds the race's ctx.store (total, best lap) and shows "New record!": a test clock's ctx.save starts empty.
    verify: () => {
      const texts = window.__gs.game.ui.state().texts;
      return texts['Results/NewRecord'] === 'New record!' && /^\d+:\d\d\.\d\d$/.test(texts['Results/TotalTime'] ?? '') &&
        /^Best lap \d+:\d\d\.\d\d$/.test(texts['Results/BestLapText'] ?? '');
    },
    setup: async () => {
      const { world, test } = window.__gs;
      world.query('Race')[0].get('Race').laps = 1;
      const gates = [...world.query('Checkpoint')].sort((a, b) => a.get('Checkpoint').index - b.get('Checkpoint').index);
      for (const gate of [...gates.slice(1), gates[0]]) {
        // The ones still to pass count, in order (a passed one is ignored), then the line ends the lap.
        const p = gate.object3d.getWorldPosition(gate.object3d.position.clone());
        test.place('Car', { position: [p.x, p.y - 2.5, p.z] });
        await test.advance(6);
      }
    },
  },
  runner: {
    screen: 'GameOver', burst: 'explosion', button: 'Try again', reset: ['HUD/Coins', /^0$/],
    // It crashed into the crates below, not somewhere further down the track. The GameOver canvas binds the
    // run's distance, coins and score, RunSystem set its title, and the NewBest ribbon shows (a test's ctx.save
    // starts empty).
    verify: () => {
      const texts = window.__gs.game.ui.state().texts;
      const distance = Math.floor(window.__gs.world.query('Runner')[0].get('Runner').distance);
      return distance < 20 && texts['GameOver/OverTitle'] === 'Crashed!' && texts['GameOver/OverDistance'] === `${distance} m` &&
        /^\d+ coins · Score \d+$/.test(texts['GameOver/OverStats']) && /^Best \d+ m$/.test(texts['GameOver/OverBest']) &&
        texts['GameOver/NewBestText'] === 'New best!';
    },
    setup: async () => {
      const { world, test, game } = window.__gs;
      await test.begin({ reset: true, seed: 3 }); // a fresh run: no shield left over from the scenario
      await test.advance(1);
      game.ui.close(); // the Title canvas (firstStart: a test reset opens it again)
      const p = world.query('Runner')[0].object3d.position;
      game.spawn('Crates', { position: [p.x, p.y, p.z - 12] }); // dead ahead, on the start deck
    },
  },
};

/** Runs the held test clock until a `burst` played since `from` (then `extra` steps), or until `screen` opened. */
const advanceUntil = (page, until, maxSteps = 1800) =>
  page.evaluate(
    async ({ until, maxSteps }) => {
      const test = window.__gs.test;
      const met = () => (until.screen ? test.status().screen === until.screen : window.__bursts.slice(until.from).includes(until.burst));
      let steps = 0;
      while (!met() && steps < maxSteps) {
        await test.advance(5);
        steps += 5;
      }
      if (until.extra) await test.advance(until.extra);
      test.render();
      return { met: met(), steps };
    },
    { until, maxSteps },
  );

async function ending(page, shot, template) {
  const end = ENDINGS[template];
  const from = (await bursts(page)).length;
  await page.evaluate(() => window.__gs.test.begin());
  await page.evaluate(end.setup);
  const party = await advanceUntil(page, { burst: end.burst, from, extra: 24 });
  await shot('celebrate');
  const shown = await advanceUntil(page, { screen: end.screen });
  await page.evaluate(() => window.__gs.test.end());
  const ui = await uiState(page);
  const verified = end.verify ? await page.evaluate(end.verify) : true;
  await sleep(400); // the screen's open animation, if it has one, plays in real time now
  await shot(end.screen);
  check(template, `the ending bursts '${end.burst}' and opens the '${end.screen}' screen (${end.button})`,
    party.met && shown.met && ui.state === 'paused' && ui.buttons[0]?.label === end.button && verified,
    `${party.steps + shown.steps} steps, screen ${ui.screen}, ${ui.state}, buttons ${labels(ui)}` +
      (end.verify ? `, ${verified ? 'state as expected' : 'unexpected state'}` : ''));
  const changes = () => page.evaluate(() => window.__gs.test.status().sceneChanges);
  const before = await changes();
  await page.keyboard.press('Enter');
  // ctx.restart(): a fade, then the start scene again with an empty ctx.store. Its systems' start()
  // has run once the HUD shows the fresh run, and the title would be open by then.
  const reloaded = await waitFor(async () => (await changes()) > before, { timeout: 60000 });
  const [key, pattern] = end.reset;
  const hud = await waitFor(async () => pattern.test(await hudText(page, key)), { timeout: 30000 });
  const fresh = await waitForScreen(page, null, 'playing', 10000);
  await sleep(500);
  await shot('restarted');
  check(template, `${end.button} starts a fresh run, without the title`, reloaded && hud && !!fresh,
    `${reloaded ? 'reloaded' : 'no scene change'}, ${fresh ? 'playing, no screen' : JSON.stringify(await uiState(page))}, ` +
      `HUD "${await hudText(page, key)}"`);
}

// --- Main ------------------------------------------------------------------------------------------

async function playTemplate(browser, template, project) {
  // Ephemeral live-link ports: another dev server (or the user's Blender) may hold the defaults.
  const configPath = path.join(project, 'gamestudio.json');
  const config = JSON.parse(await readFile(configPath, 'utf8'));
  await writeFile(configPath, JSON.stringify({ ...config, link: { editorPort: 0, gamePort: 0 } }, null, 2));

  const tsc = spawnSync(process.execPath, [path.join(project, 'node_modules', 'typescript', 'bin', 'tsc'), '-p', 'tsconfig.json'], {
    cwd: project,
    encoding: 'utf8',
  });
  check(template, 'npm run typecheck passes in the new project', tsc.status === 0, (tsc.stdout + tsc.stderr).trim().slice(0, 800));

  const server = await startDevServer(project, await freePort());
  const page = await browser.newPage();
  const errors = [];
  const logs = [];
  const warnings = [];
  page.on('console', (msg) => {
    if (msg.type() === 'error') errors.push(msg.text());
    if (msg.type() === 'warn') warnings.push(msg.text());
    logs.push(msg.text());
  });
  page.on('pageerror', (error) => errors.push(`pageerror: ${error.message}`));
  page.on('response', (res) => {
    if (res.status() >= 400) errors.push(`HTTP ${res.status()} ${res.url()}`);
  });
  let loads = 0;
  page.on('load', () => loads++);
  try {
    await page.setViewport({ width: 1280, height: 720 });
    await page.goto(server.url, { waitUntil: 'load' });
    await page.waitForFunction(() => window.__gs?.ready === true, { timeout: 90000 });
    let n = 0;
    const shot = async (name) => {
      const file = path.join(SHOTS, `${template}-${++n}-${name}.png`);
      await page.screenshot({ path: file });
      console.log(`      screenshot ${file}`);
    };
    // The title screen (ctx.ui) holds the game paused until Play: Enter presses it, as a player would.
    const title = await waitForScreen(page, screenOf(template, 'title'), 'paused');
    check(template, 'the title screen opens on the first start and holds the game', !!title && labelSet(title) === buttonsOf(template, 'title'),
      title ? `buttons ${labels(title)}` : JSON.stringify(await uiState(page)));
    await recordBursts(page);
    await page.click('#game', { offset: { x: 5, y: 5 } }); // focus, and the first input unlocks audio
    await sleep(500);
    await shot('title');
    if (TITLES[template]) await TITLES[template](page, shot, { warnings });
    if (OPTIONS[template]) await optionsFromTitle(page, shot, template);
    await page.keyboard.press('Enter');
    const playing = await waitForScreen(page, null, 'playing');
    const info = await page.evaluate(() => ({ entities: window.__gs.world.size, prefabs: window.__gs.game.prefabs }));
    check(template, 'the scene loads and plays', info.entities > 5 && !!playing, `${info.entities} entities, prefabs: ${info.prefabs.join(', ') || '-'}`);
    await passGame(page, 1); // physics settles, the camera catches up
    const fps = await page.evaluate(
      () => new Promise((resolve) => {
        let frames = 0;
        const began = performance.now();
        const tick = () => (++frames, performance.now() - began < 1000 ? requestAnimationFrame(tick) : resolve(frames));
        requestAnimationFrame(tick);
      }),
    );
    console.log(`      ${fps} fps in headless ${path.basename(BROWSER)} (game time runs slower below 12 fps)`);
    await shot('start');
    await SCENARIOS[template](page, shot, logs);

    const played = await bursts(page);
    check(template, `the game bursts particles (${JUICE[template].join(', ')})`, JUICE[template].every((b) => played.includes(b)),
      [...new Set(played)].join(', ') || 'no bursts');
    if (AMBIENT[template]) {
      const ambient = await page.evaluate(() => {
        const { world, game } = window.__gs;
        const emitters = world.query('ParticleEmitter');
        return { emitters: emitters.length, particles: emitters.reduce((sum, e) => sum + game.vfx.count(e), 0) };
      });
      check(template, 'ambient ParticleEmitters run', ambient.emitters >= AMBIENT[template] && ambient.particles > 0,
        `${ambient.emitters} emitters, ${ambient.particles} particles`);
    }

    await page.keyboard.press('Escape');
    const paused = await waitForScreen(page, screenOf(template, 'pause'), 'paused');
    await sleep(300); // its open animation, if it has one
    await shot('pause');
    if (OPTIONS[template]) await optionsFromPause(page, template);
    await page.keyboard.press('Escape');
    const resumed = await waitForScreen(page, null, 'playing');
    check(template, `Escape opens the pause menu (${buttonsOf(template, 'pause')}) and closes it`,
      !!paused && labelSet(paused) === buttonsOf(template, 'pause') && !!resumed,
      paused ? `buttons ${labels(paused)}, then ${resumed ? 'playing' : JSON.stringify(await uiState(page))}` : JSON.stringify(await uiState(page)));

    await ending(page, shot, template);
    check(template, 'no console errors', errors.length === 0, errors.join(' | ').slice(0, 1500));
    if (warnings.length) console.log(`      console warnings: ${warnings.join(' | ').slice(0, 1500)}`);
    check(template, 'the page never reloaded', loads === 1, `${loads} loads`);
  } catch (error) {
    check(template, 'scenario ran to the end', false, String(error?.stack ?? error));
  } finally {
    if (results.some((r) => r.template === template && !r.ok)) console.log(`      dev server output:\n${server.output()}`);
    await page.close();
    server.stop();
  }
  // A file outside the project that Vite reloaded the page for: the engine is linked from this
  // checkout, so someone editing it while the test runs reloads the game.
  const own = project.replaceAll('\\', '/').toLowerCase();
  const changed = [...server.output().replace(/\x1b\[[0-9;]*m/g, '').matchAll(/page reload (\S+)/g)].map((m) => m[1]);
  return changed.find((file) => !file.replaceAll('\\', '/').toLowerCase().startsWith(own)) ?? '';
}

async function main() {
  if (!BROWSER) throw new Error('No Edge, Chrome or Chromium found; set GS_BROWSER');
  await mkdir(SHOTS, { recursive: true });
  for (const old of await readdir(SHOTS)) await rm(path.join(SHOTS, old), { force: true });

  if (!flag('--skip-scaffold')) {
    await rm(PROJECTS, { recursive: true, force: true });
    console.log(`Creating the projects in ${PROJECTS} (Blender)...`);
    const began = Date.now();
    const blender = BLENDER ? ['--blender', BLENDER] : [];
    const scaffold = spawnSync(PYTHON, [path.join(REPO, 'tools', 'e2e', 'templates_e2e.py'), PROJECTS, ...wanted, ...blender], {
      encoding: 'utf8',
      maxBuffer: 256 * 1024 * 1024, // the glTF exporter logs a line per object
    });
    if (scaffold.error) throw scaffold.error;
    const lines = `${scaffold.stdout}\n${scaffold.stderr}`.split('\n');
    for (const line of lines.filter((l) => l.startsWith('TEMPLATE '))) {
      const [, id, status, ...rest] = line.trim().split(' ');
      check(id, 'New Project in Blender creates a playable project', status === 'OK', rest.join(' '));
    }
    if (scaffold.status !== 0) {
      console.log(lines.filter((l) => /error|Traceback|FAIL/i.test(l)).slice(-30).join('\n'));
    }
    console.log(`Blender: ${((Date.now() - began) / 1000).toFixed(1)} s`);
  }

  const browser = await puppeteer.launch({
    executablePath: BROWSER,
    headless: true,
    args: [
      ...(flag('--gpu') ? ['--use-angle=d3d11'] : ['--use-angle=swiftshader', '--enable-unsafe-swiftshader']),
      '--ignore-gpu-blocklist',
      '--no-first-run',
      '--lang=en-US', // the games start in English (a translated UI follows navigator.languages)
      '--autoplay-policy=no-user-gesture-required',
      '--window-size=1280,720',
    ],
  });
  try {
    for (const template of Object.keys(SCENARIOS)) {
      if (wanted.length && !wanted.includes(template)) continue;
      const project = path.join(PROJECTS, template);
      if (!existsSync(path.join(project, 'gamestudio.json'))) {
        check(template, 'project exists', false, project);
        continue;
      }
      console.log(`\n== ${template} ==`);
      const mark = results.length;
      const changed = await playTemplate(browser, template, project);
      if (changed && results.slice(mark).some((r) => !r.ok)) {
        console.log(`      ${changed} changed during the run and the page reloaded: playing ${template} again`);
        results.splice(mark);
        await playTemplate(browser, template, project);
      }
    }
  } finally {
    await browser.close();
    if (!flag('--keep')) await rm(PROJECTS, { recursive: true, force: true });
  }
  const passed = results.filter((r) => r.ok).length;
  console.log(`\ntemplates e2e: ${passed}/${results.length} checks passed. Screenshots: ${SHOTS}`);
  return passed === results.length;
}

main().then(
  (ok) => process.exit(ok ? 0 : 1),
  (error) => {
    console.error(`\ntemplates e2e FAILED: ${error instanceof Error ? error.stack : error}`);
    process.exit(1);
  },
);
