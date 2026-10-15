// @ts-check
// SPDX-License-Identifier: MIT
// Headless game pages in Edge or Chrome (puppeteer-core): the MCP server's own game instance
// (for play when no game is open, and game_screenshot) and scripted playtests. Without either on the
// machine, in the Chrome Headless Shell GameStudio downloads (discovery.js locateBrowser).
//
// Playtests run on game time: the page opens with the engine's test clock held (window.__gs.test),
// the script is handed to the engine, and the page steps the simulation itself, as fast as it can,
// rendering only for screenshots. Results are the same at any render size or speed.
//
// The browser draws on the GPU when it can (GAMESTUDIO_BROWSER_GPU=auto, the default): it starts with
// the GPU switches of the embedded stream host, a blank page checks that WebGL2 works on a real GPU,
// and if it does not the browser is started again with SwiftShader (software GL, slow but everywhere).

import { browserCandidates, isHeadlessShell } from './discovery.js';
import { ToolError, round, sleep } from './util.js';

const READY_TIMEOUT_MS = 60_000;
/** How long the WebGL check of a new browser may take before it counts as failed. */
const PROBE_TIMEOUT_MS = 10_000;
/** The persistent headless game (play, game_screenshot). Small: screenshots go to the agent, and SwiftShader draws 800×450 at 3–4 fps. */
export const GAME_SIZE = /** @type {[number, number]} */ ([480, 270]);
export const PLAYTEST_SIZE = /** @type {[number, number]} */ ([480, 270]);
/** How long a screenshot at another size waits for the game to draw at it. */
const RESIZE_FRAME_TIMEOUT_MS = 5_000;
const MAX_PAGE_LOGS = 500;
/** The engine's fixed step (FIXED_DT). */
const STEP_S = 1 / 60;
export const MAX_DURATION_S = 120;
/** Steps per page call, so the page can handle the live link and hot reloads between chunks. */
const CHUNK_STEPS = 600;
/** Position samples (sample_every) kept per entity and in the whole result; more are decimated. */
export const MAX_SAMPLES_PER_ENTITY = 200;
export const MAX_SAMPLE_POINTS = 1000;
/** Instances listed per track pattern ("Boulder*") unless track_max says otherwise. */
export const TRACK_MAX = 20;
/**
 * Blender UI canvases (docs/GAME-UI.md): how long one may take to reach the game (ui/index.json at startup, or
 * the live link), then to open (it waits for its fonts, up to 1 s), how long another menu's close animation may
 * take before the canvas opens, and its own open animation (at most 10 s, GAME-UI §10.5).
 */
export const UI_TIMEOUTS = { known: 3_000, open: 3_000, close: 3_000, animation: 11_000 };

/**
 * @typedef {{ at: number, key?: string, action?: 'down' | 'up' | 'press', hold?: number,
 *   mouse?: 'move' | 'down' | 'up' | 'click' | 'wheel' | 'aim', x?: number, y?: number,
 *   button?: 'left' | 'right' | 'middle', dx?: number, dy?: number, text?: string,
 *   target?: string, pick?: 'nearest' | 'first', until?: number, world?: number[], space?: 'runtime' | 'blender' }} InputEvent
 * @typedef {{ level: string, msg: string, at?: number, t?: number | null }} PageLog
 * @typedef {{ name?: string, position?: number[], yaw_deg?: number, rotation?: number[], space?: 'runtime' | 'blender' }} StartOverride
 * @typedef {{ inputs?: InputEvent[], duration?: number, screenshots?: number[] | number, track?: string[],
 *   start?: StartOverride | StartOverride[], size?: [number, number], seed?: number, realtime?: boolean,
 *   sample_every?: number, track_max?: number, set?: { store?: Record<string, unknown>, save?: Record<string, unknown> },
 *   state?: { names?: string[], component?: string, limit?: number } }} PlaytestOptions
 */

/** A track entry with * or ? is a name pattern: it follows every matching entity, spawned ones too. @param {string} name */
export const isPattern = (name) => /[*?]/.test(name);

/**
 * @typedef {'auto' | 'gpu' | 'swiftshader'} GpuMode
 * @typedef {{ webgl2: boolean, renderer: string | null, error?: string }} WebGLProbe
 * @typedef {{ mode: GpuMode, renderer: string | null, gpu: boolean | null, fallback?: string }} Rendering
 */

/**
 * GAMESTUDIO_BROWSER_GPU: 1 (or true/on/yes/gpu) draws on the GPU, 0 (or false/off/no/swiftshader)
 * with SwiftShader; unset, empty, "auto" or anything else: the GPU when it works, else SwiftShader.
 * @param {string | undefined} value
 * @returns {GpuMode}
 */
export function gpuMode(value) {
  const v = String(value ?? '').trim().toLowerCase();
  if (['1', 'true', 'on', 'yes', 'gpu'].includes(v)) return 'gpu';
  if (['0', 'false', 'off', 'no', 'swiftshader', 'software'].includes(v)) return 'swiftshader';
  return 'auto';
}

const COMMON_ARGS = ['--no-first-run', '--no-default-browser-check', '--mute-audio', '--autoplay-policy=no-user-gesture-required', '--disable-background-timer-throttling', '--disable-renderer-backgrounding', '--disable-backgrounding-occluded-windows'];

/**
 * Browser switches per mode. swiftshader: software GL. gpu (GAMESTUDIO_BROWSER_GPU=1): the browser's
 * own choice. auto: the GPU like the embedded stream host (devserver stream-host.js browserArgs), even
 * a blocklisted one, through D3D11 on Windows and the browser's own backend elsewhere (Metal on
 * macOS); SwiftShader only where there is no GPU at all. Chrome Headless Shell (`shell`) draws with
 * SwiftShader unless --enable-gpu asks for the GPU, so it gets that in auto and gpu.
 * @param {GpuMode} mode
 * @param {NodeJS.Platform} [platform]
 * @param {boolean} [shell] the browser is Chrome Headless Shell
 */
export function browserArgs(mode, platform = process.platform, shell = false) {
  const args = [...COMMON_ARGS];
  if (mode === 'swiftshader') args.push('--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--ignore-gpu-blocklist');
  else if (mode === 'auto') {
    args.push('--ignore-gpu-blocklist', '--enable-unsafe-swiftshader');
    if (platform === 'win32') args.push('--use-angle=d3d11');
  }
  if (shell && mode !== 'swiftshader') args.push('--enable-gpu');
  return args;
}

/** A software WebGL renderer: SwiftShader, llvmpipe, softpipe or the Microsoft Basic Render Driver (WARP). @param {string | null | undefined} renderer */
export const isSoftwareRenderer = (renderer) => /swiftshader|llvmpipe|softpipe|software|basic render/i.test(renderer ?? '');

/**
 * Why a probe rules out the GPU, or null when WebGL2 runs on a real GPU.
 * @param {WebGLProbe} probe
 */
export function gpuProblem(probe) {
  if (probe.error) return `the WebGL check failed: ${probe.error}`;
  if (!probe.webgl2) return 'no WebGL2';
  if (!probe.renderer) return 'the renderer is unknown';
  if (isSoftwareRenderer(probe.renderer)) return `software renderer ${probe.renderer}`;
  return null;
}

const PROGRAM_LOG = 'THREE.WebGLProgram: Program Info Log:';
/**
 * True for a three.js program log made only of Direct3D shader-compiler warnings (X4122 "cannot be
 * represented accurately in double precision"), printed when the GPU draws through ANGLE/D3D11. The
 * engine keeps them off the live link (isShaderCompilerNoise in link/capture.ts); playtest logs skip
 * them too. Real shader errors are never matched. Mirrored in holdAndRecordLogs, which runs in the page.
 * @param {string} level @param {string} msg
 */
export function isShaderCompilerNoise(level, msg) {
  if (level !== 'warn' || !msg.startsWith(PROGRAM_LOG)) return false;
  const lines = msg.slice(PROGRAM_LOG.length).split('\n').map((l) => l.trim()).filter(Boolean);
  return lines.length > 0 && lines.every((l) => /^\(\d+,\d+(?:-\d+)?\): warning X\d+:/.test(l));
}

/**
 * Samples without the rows where nothing moved: a row is kept when the position changed by 1 cm or more
 * since the last kept row; the last row is always kept (where it ended up, and until when).
 * @param {number[][]} samples [t, x, y, z] rows
 */
export function dropStill(samples) {
  /** @type {number[][]} */
  const kept = [];
  for (const s of samples) {
    const prev = kept.at(-1);
    if (!prev || Math.max(Math.abs(s[1] - prev[1]), Math.abs(s[2] - prev[2]), Math.abs(s[3] - prev[3])) >= 0.01) kept.push(s);
  }
  if (samples.length > 1 && kept.at(-1) !== samples.at(-1)) kept.push(/** @type {number[]} */ (samples.at(-1)));
  return kept;
}

/**
 * Keeps every k-th sample of a regular grid so no entity has more than `perEntity` samples and all
 * of them together no more than `total`. Samples are [t, x, y, z] taken every `every` seconds.
 * @param {number[][][]} lists
 * @param {number} every
 * @param {{ perEntity?: number, total?: number }} [limits]
 * @returns {{ lists: number[][][], factor: number }}
 */
export function decimateSamples(lists, every, { perEntity = MAX_SAMPLES_PER_ENTITY, total = MAX_SAMPLE_POINTS } = {}) {
  const longest = Math.max(0, ...lists.map((l) => l.length));
  const all = lists.reduce((n, l) => n + l.length, 0);
  let factor = Math.max(1, Math.ceil(longest / perEntity), Math.ceil(all / total));
  const keep = (/** @type {number} */ k) => lists.map((l) => l.filter((s) => Math.round(s[0] / every) % k === 0));
  let kept = keep(factor);
  // Grid alignment can leave a few more than the ceilings allow: widen until it fits.
  while (kept.some((l) => l.length > perEntity) || kept.reduce((n, l) => n + l.length, 0) > total) kept = keep(++factor);
  return { lists: kept, factor };
}

/** Accepts KeyboardEvent.code names ("KeyW", "Space", "ArrowUp") and plain letters/digits. @param {string} key */
export function normalizeKey(key) {
  if (/^[a-z]$/i.test(key)) return `Key${key.toUpperCase()}`;
  if (/^[0-9]$/.test(key)) return `Digit${key}`;
  if (key === ' ') return 'Space';
  const aliases = /** @type {Record<string, string>} */ ({ space: 'Space', up: 'ArrowUp', down: 'ArrowDown', left: 'ArrowLeft', right: 'ArrowRight', shift: 'ShiftLeft', ctrl: 'ControlLeft', control: 'ControlLeft', alt: 'AltLeft', enter: 'Enter', escape: 'Escape', esc: 'Escape', tab: 'Tab' });
  return aliases[key.toLowerCase()] ?? key;
}

/**
 * Expands an input script into a time-sorted list of primitive actions (any time unit).
 * @param {InputEvent[]} inputs
 * @returns {{ at: number, kind: 'key' | 'mouse', action: string, key?: string, x?: number, y?: number, button?: string, dx?: number, dy?: number }[]}
 */
export function expandInputs(inputs) {
  /** @type {ReturnType<typeof expandInputs>} */
  const out = [];
  inputs.forEach((input, i) => {
    const at = Math.max(0, Number(input.at ?? 0));
    if (input.key !== undefined) {
      const key = normalizeKey(String(input.key));
      if (input.hold !== undefined) {
        out.push({ at, kind: 'key', action: 'down', key });
        out.push({ at: at + Math.max(0, Number(input.hold)), kind: 'key', action: 'up', key });
      } else {
        const action = input.action ?? 'press';
        if (!['down', 'up', 'press'].includes(action)) throw new ToolError(`inputs[${i}].action must be down, up or press.`, { code: 'invalid_arguments' });
        out.push({ at, kind: 'key', action, key });
      }
    } else if (input.mouse !== undefined) {
      if (!['move', 'down', 'up', 'click', 'wheel'].includes(input.mouse)) throw new ToolError(`inputs[${i}].mouse must be move, down, up, click or wheel.`, { code: 'invalid_arguments' });
      out.push({ at, kind: 'mouse', action: input.mouse, x: input.x, y: input.y, button: input.button ?? 'left', dx: input.dx ?? 0, dy: input.dy ?? 0 });
      if (input.hold !== undefined && (input.mouse === 'down' || input.mouse === 'click')) {
        out.push({ at: at + Math.max(0, Number(input.hold)), kind: 'mouse', action: 'up', button: input.button ?? 'left', dx: 0, dy: 0 });
      }
    } else {
      throw new ToolError(`inputs[${i}] needs "key" or "mouse".`, { code: 'invalid_arguments', hint: 'Example: {"at": 0, "key": "KeyW", "hold": 2}' });
    }
  });
  return out.sort((a, b) => a.at - b.at);
}

/**
 * Screenshot times: a list, or a count spread evenly from 0 to the duration (rounded to whole units).
 * @param {number[] | number | undefined} screenshots
 * @param {number} duration
 */
export function screenshotTimes(screenshots, duration) {
  if (screenshots === undefined) return [0, duration];
  if (typeof screenshots === 'number') {
    const n = Math.max(0, Math.min(12, Math.floor(screenshots)));
    if (n === 0) return [];
    if (n === 1) return [duration];
    return Array.from({ length: n }, (_, i) => Math.round((duration * i) / (n - 1)));
  }
  return [...screenshots].map((t) => Math.max(0, Math.min(duration, Number(t)))).sort((a, b) => a - b);
}

/** Seconds → whole fixed steps. @param {number} seconds */
export const toSteps = (seconds) => Math.round(seconds / STEP_S + 1e-9);

/**
 * Checks a playtest script and works out its timing, in game seconds. Throws ToolError on
 * anything that would fail later, in particular times that look like milliseconds.
 * @param {PlaytestOptions} options
 */
export function planPlaytest(options) {
  const inputs = options.inputs ?? [];
  // Aims follow a target through the game's camera every step, in the page; the rest are DOM events.
  const isAim = (/** @type {InputEvent} */ i) => i.mouse === 'aim' || (i.mouse === 'move' && i.world !== undefined);
  const plain = inputs.filter((i) => !isAim(i));
  const actions = expandInputs(plain);
  const msHint = 'playtest times are game seconds ({"at": 1.5, "hold": 2}), not milliseconds.';
  inputs.forEach((input, i) => {
    for (const field of /** @type {const} */ (['at', 'hold'])) {
      const value = input[field];
      if (typeof value === 'number' && value > MAX_DURATION_S) {
        throw new ToolError(`inputs[${i}].${field} is ${value} s, past the ${MAX_DURATION_S} s limit.`, { code: 'invalid_arguments', hint: `${msHint} Did you mean ${round(value / 1000)}?` });
      }
    }
  });
  // The default run lasts until 0.5 s after the last input, releases included; keys still held at the end are let go.
  const aimEnds = inputs.filter(isAim).map((i) => Number(i.until ?? i.at ?? 0));
  const last = Math.max(0, ...actions.map((a) => a.at), ...aimEnds);
  const duration = options.duration ?? Math.min(MAX_DURATION_S, last + 0.5);
  if (duration > MAX_DURATION_S) {
    throw new ToolError(`duration is ${duration} s; the limit is ${MAX_DURATION_S} s of game time.`, { code: 'invalid_arguments', hint: msHint });
  }
  const lastStart = Math.max(0, ...inputs.map((i) => Number(i.at ?? 0)));
  if (lastStart > duration) {
    throw new ToolError(`An input starts at ${round(lastStart)} s, after the end of the run (duration ${duration} s).`, { code: 'invalid_arguments', hint: 'Raise duration, or leave it out (default: the last input + 0.5 s).' });
  }
  if (Array.isArray(options.screenshots)) {
    const late = options.screenshots.find((t) => t > duration);
    if (late !== undefined) {
      throw new ToolError(`Screenshot time ${late} is after the end of the run (duration ${duration} s).`, { code: 'invalid_arguments', hint: late > MAX_DURATION_S ? msHint : 'Screenshot times are game seconds within the run.' });
    }
  }
  let shots;
  if (typeof options.screenshots === 'number') {
    const n = Math.max(0, Math.min(12, Math.floor(options.screenshots)));
    shots = n === 0 ? [] : n === 1 ? [duration] : Array.from({ length: n }, (_, i) => (duration * i) / (n - 1));
  } else {
    shots = [...(options.screenshots ?? [0, duration])].sort((a, b) => a - b);
  }
  const steps = toSteps(duration);
  // A shot at 0 is taken after the first step, once systems have started (the camera follows).
  const shotSteps = shots.map((t) => Math.min(steps, Math.max(steps > 0 ? 1 : 0, toSteps(t))));
  const start = options.start === undefined ? [] : Array.isArray(options.start) ? options.start : [options.start];
  start.forEach((s, i) => {
    if (!s.position && s.yaw_deg === undefined && !s.rotation) {
      throw new ToolError(`start[${i}] needs a position, yaw_deg or rotation.`, { code: 'invalid_arguments', hint: 'Example: {"start": {"position": [6.5, 1, -4]}} (runtime space; add "space": "blender" for Blender coordinates).' });
    }
  });
  let sampleSteps = 0;
  if (options.sample_every !== undefined) {
    const every = Number(options.sample_every);
    if (!(every > 0) || every > MAX_DURATION_S) {
      throw new ToolError(`sample_every is ${options.sample_every}; it is game seconds between position samples (e.g. 0.25).`, { code: 'invalid_arguments', hint: msHint });
    }
    sampleSteps = Math.max(1, toSteps(every));
  }
  const aims = inputs.map((input, i) => ({ input, i })).filter(({ input }) => isAim(input)).map(({ input, i }) => {
    const where = `inputs[${i}]`;
    const from = toSteps(Number(input.at ?? 0));
    if (input.mouse === 'aim' && (typeof input.target !== 'string' || !input.target) && !input.world) {
      throw new ToolError(`${where}: aim needs a target (an entity name or pattern such as "Grunt*") or world [x, y, z].`, { code: 'invalid_arguments', hint: 'Example: {"at": 10, "mouse": "aim", "target": "Grunt*", "pick": "nearest", "until": 25}' });
    }
    if (input.world !== undefined && (!Array.isArray(input.world) || input.world.length !== 3 || !input.world.every((v) => typeof v === 'number'))) {
      throw new ToolError(`${where}.world must be [x, y, z].`, { code: 'invalid_arguments' });
    }
    if (input.until !== undefined && Number(input.until) < Number(input.at ?? 0)) {
      throw new ToolError(`${where}.until is before its at.`, { code: 'invalid_arguments' });
    }
    // An aim follows until `until` (default: the end of the run); a move to a world point is one step unless `until`.
    const until = input.until !== undefined ? toSteps(Number(input.until)) : input.mouse === 'aim' ? steps : from + 1;
    const world = input.world ? (input.space === 'blender' ? [input.world[0], input.world[2], -input.world[1]] : input.world) : null;
    return { from, until: Math.max(from + 1, until), target: input.target ?? null, world, pick: input.pick ?? 'nearest' };
  });
  return { actions, duration, steps, shots, shotSteps, start, sampleSteps, inputs: plain, aims };
}

/**
 * A start override in the engine's terms (runtime space).
 * @param {StartOverride} s
 */
export function placement(s) {
  /** @type {{ position?: number[], rotation?: number[], yaw?: number }} */
  const out = {};
  if (s.position) {
    const [x, y, z] = s.position;
    out.position = s.space === 'blender' ? [x, z, -y] : [x, y, z];
  }
  if (s.rotation) out.rotation = s.rotation;
  if (s.yaw_deg !== undefined) out.yaw = s.yaw_deg;
  return out;
}

/** @param {unknown} v */
const tidy = (v) => (typeof v === 'number' ? round(v, 4) : v);

/** @param {any} change */
const changeOut = (change) => ({
  start: tidy(change.start),
  final: tidy(change.final),
  ...(change.min !== undefined && { min: tidy(change.min), max: tidy(change.max) }),
  count: change.count,
  first_s: change.first,
  last_s: change.last,
});

export class GameBrowser {
  /** @param {{ env?: NodeJS.ProcessEnv, log?: (line: string) => void }} [options] */
  constructor({ env = process.env, log = () => {} } = {}) {
    this.env = env;
    this.log = log;
    /** @type {any} */
    this.browser = null;
    /** @type {Promise<any> | null} */
    this.launching = null;
    /** @type {{ page: any, tag: string, url: string, logs: PageLog[], size: [number, number] } | null} */
    this.game = null;
    this.tagCounter = 0;
    /** What draws the headless games (null until the browser starts): the mode, the WebGL renderer, and whether it is a GPU. @type {Rendering | null} */
    this.rendering = null;
  }

  async #puppeteer() {
    try {
      return (await import('puppeteer-core')).default;
    } catch {
      throw new ToolError('The game packages are not set up yet (puppeteer-core is missing), so headless play is unavailable.', {
        code: 'puppeteer_missing',
        hint: 'Open the project in Blender once (launch_blender): GameStudio sets up the game packages there, puppeteer-core with them.',
      });
    }
  }

  async #browser() {
    if (this.browser?.connected) return this.browser;
    this.launching ??= (async () => {
      const candidates = browserCandidates(this.env);
      if (!candidates.length) {
        throw new ToolError('GameStudio has no game browser yet: this machine has no Edge, Chrome or Chromium, and GameStudio has not set up its own.', {
          code: 'browser_not_found',
          hint: 'Open the project in Blender once (launch_blender): GameStudio sets up its game browser there.',
        });
      }
      const puppeteer = await this.#puppeteer();
      // The next browser is tried when one does not start (a broken install, a confined snap).
      /** @type {unknown} */
      let firstError = null;
      for (const found of candidates) {
        try {
          return await this.#start(puppeteer, found);
        } catch (error) {
          firstError ??= error;
          this.log(`${found.path} did not start: ${firstLine(error)}`);
        }
      }
      throw firstError;
    })().finally(() => {
      this.launching = null;
    });
    return this.launching;
  }

  /**
   * Starts one browser: on the GPU when it can (GAMESTUDIO_BROWSER_GPU=auto), else with SwiftShader.
   * @param {any} puppeteer
   * @param {{ path: string, source: string }} found
   */
  async #start(puppeteer, found) {
    const executablePath = found.path;
    const mode = gpuMode(this.env.GAMESTUDIO_BROWSER_GPU);
    // Chrome Headless Shell: puppeteer's 'shell' mode (plain --headless) and --enable-gpu.
    const shell = isHeadlessShell(executablePath);
    const launch = (/** @type {GpuMode} */ m) => puppeteer.launch({ executablePath, headless: shell ? 'shell' : true, args: browserArgs(m, process.platform, shell), defaultViewport: null, handleSIGINT: false, handleSIGTERM: false, handleSIGHUP: false });
    /** @type {any} */
    let browser = null;
    /** @type {string | null} */
    let fallback = null;
    try {
      browser = await launch(mode);
    } catch (error) {
      if (mode !== 'auto') throw error;
      fallback = `the browser did not start with the GPU switches: ${firstLine(error)}`;
    }
    let probe = browser ? await probeWebGL(browser) : null;
    if (mode === 'auto') fallback ??= gpuProblem(/** @type {WebGLProbe} */ (probe));
    if (fallback) {
      // Started again rather than kept: SwiftShader through its own switches is the setup known to work.
      this.log(`no GPU for headless play (${fallback}): starting the browser again with SwiftShader`);
      await browser?.close().catch(() => {});
      browser = await launch('swiftshader');
      probe = await probeWebGL(browser);
    }
    const renderer = probe?.renderer ?? null;
    this.rendering = { mode, renderer, gpu: renderer ? !isSoftwareRenderer(renderer) : null, ...(fallback ? { fallback } : {}) };
    browser.on('disconnected', () => {
      if (this.browser === browser) {
        this.browser = null;
        this.game = null;
      }
    });
    this.browser = browser;
    this.log(`browser started: ${executablePath} (${found.source}; GAMESTUDIO_BROWSER_GPU=${mode}; WebGL: ${renderer ?? probe?.error ?? 'none'})`);
    return browser;
  }

  /**
   * Opens the game at `url` in a new headless page and waits until its scene is loaded.
   * `hold: true` starts it with the engine's test clock held, so nothing runs before the test.
   * @param {string} url
   * @param {{ size?: [number, number], timeoutMs?: number, hold?: boolean }} [options]
   */
  async openGame(url, { size = GAME_SIZE, timeoutMs = READY_TIMEOUT_MS, hold = false } = {}) {
    const browser = await this.#browser();
    const page = await browser.newPage();
    const tag = `GameStudioMCP/${process.pid}.${++this.tagCounter}`;
    /** @type {PageLog[]} */
    const logs = [];
    const started = Date.now();
    page.on('console', (/** @type {any} */ m) => {
      const level = m.type() === 'warning' ? 'warn' : m.type();
      const msg = m.text();
      if (isShaderCompilerNoise(level, msg)) return;
      logs.push({ level, msg: msg.slice(0, 2000), at: Date.now() - started });
      if (logs.length > MAX_PAGE_LOGS) logs.shift();
    });
    page.on('pageerror', (/** @type {any} */ e) => logs.push({ level: 'error', msg: String(e?.message ?? e).slice(0, 2000), at: Date.now() - started, t: null }));
    await page.setViewport({ width: size[0], height: size[1] });
    await page.setUserAgent(`${await browser.userAgent()} ${tag}`);
    if (hold) await page.evaluateOnNewDocument(holdAndRecordLogs);
    try {
      await page.goto(url, { waitUntil: 'load', timeout: timeoutMs });
      await page.waitForFunction(() => /** @type {any} */ (window).__gs?.ready === true, { timeout: timeoutMs, polling: 20 });
    } catch (error) {
      const hud = await page.evaluate(() => document.body?.innerText ?? '').catch(() => '');
      await closePage(page);
      const errors = logs.filter((l) => l.level === 'error').slice(-5).map((l) => l.msg);
      throw new ToolError(`The game at ${url} did not finish loading: ${error instanceof Error ? error.message.split('\n')[0] : String(error)}.`, {
        code: 'game_not_ready',
        hint: 'Export the scene first (export_scene); check game_logs for build errors and .gamestudio/devserver.log.',
        data: { ...(hud.trim() && { page_text: hud.trim().slice(0, 500) }), ...(errors.length && { errors }) },
      });
    }
    return { page, tag, logs, size, loadMs: Date.now() - started };
  }

  /**
   * The server's own persistent headless game, opened on first use at `size` (default GAME_SIZE).
   * A size for the open game resizes its page; without one it keeps its size.
   * @param {string} url
   * @param {{ size?: [number, number] }} [options]
   */
  async ensureGame(url, { size } = {}) {
    if (this.game && !this.game.page.isClosed() && this.game.url === url) {
      if (size && !sameSize(size, this.game.size)) {
        await this.game.page.setViewport({ width: size[0], height: size[1] });
        this.game.size = [size[0], size[1]];
      }
      return { ...this.game, opened: false };
    }
    if (this.game) await closePage(this.game.page);
    const opened = await this.openGame(url, { size: size ?? GAME_SIZE });
    this.game = { page: opened.page, tag: opened.tag, url, logs: opened.logs, size: opened.size };
    return { ...this.game, opened: true };
  }

  /**
   * A PNG of the page. With `size`, the page is drawn at that size for the shot, then set back.
   * @param {any} page
   * @param {[number, number]} [size]
   * @returns {Promise<string>} base64 PNG
   */
  async screenshot(page, size) {
    return (await this.shoot(page, size)).image;
  }

  /**
   * screenshot(), and `read` run in the page right after the shot, while the page still has the shot's size: the
   * page is resized, a frame is drawn at the new size, the picture is taken, `read` runs, and only then is the old
   * size set back. So what `read` reports (the UI state: ui.size and button x/y) describes the returned image.
   * @template T
   * @param {any} page
   * @param {[number, number]} [size]
   * @param {() => T} [read]
   * @returns {Promise<{ image: string, read: T | undefined }>}
   */
  async shoot(page, size, read) {
    const own = size ? page.viewport() : null;
    const take = async () => {
      const image = Buffer.from(await page.screenshot({ type: 'png' })).toString('base64');
      return { image, read: read ? await page.evaluate(read) : undefined };
    };
    if (!size || !own || sameSize(size, [own.width, own.height])) return take();
    await page.setViewport({ ...own, width: size[0], height: size[1] });
    try {
      await page.evaluate(nextFrames, RESIZE_FRAME_TIMEOUT_MS); // drawn at the new size, laid out for it
      return await take();
    } finally {
      await page.setViewport(own).catch(() => {});
    }
  }

  /**
   * Opens the Blender UI canvas `name` in a game page as `ctx.ui.open` would (the `ui.open` message,
   * docs/GAME-UI.md §4) and waits until it is open, its open animation has finished and it is drawn. Another
   * open menu is closed first, and its close animation waited for, so two menus never overlap in the shot.
   * Throws when the game does not know the canvas.
   * @param {any} page
   * @param {string} name
   * @param {{ openSeconds?: number }} [options] openSeconds: the canvas's open animation from its export, for an
   *   engine that does not report `ui.animating`
   * @returns {Promise<{ opened: boolean, closed: string | null, animating: boolean }>} opened: false when it did
   *   not report open in time; closed: the menu closed first; animating: its open animation had not ended in time
   */
  async openUiCanvas(page, name, { openSeconds = 0 } = {}) {
    /** @type {{ supported: boolean, known: boolean, canvases: string[] }} */
    const found = await page.evaluate(waitForUiCanvas, name, UI_TIMEOUTS.known);
    if (!found.supported) {
      throw new ToolError("This project's engine has no Blender UI canvases (ctx.ui.canvases), so it cannot open one.", {
        code: 'ui_unsupported',
        hint: 'Update @gamestudio/engine; meanwhile take the screenshot without ui.',
      });
    }
    if (!found.known) {
      throw new ToolError(`The game has no UI canvas '${name}'. ${found.canvases.length ? `Its canvases: ${found.canvases.join(', ')}.` : 'It has none.'}`, {
        code: 'unknown_canvas',
        hint: 'ui_list lists the canvases of ui/ui.blend (names are case-sensitive). A canvas reaches a running game live while ui/ui.blend is open in Blender, and from its export (saving ui/ui.blend) when the game loads.',
      });
    }
    return page.evaluate(openUiCanvasInPage, name, { ...UI_TIMEOUTS, openSeconds });
  }

  /**
   * Sets `ctx.store` values and `ctx.save` values in a game page (game_screenshot's store and save): store keys are
   * assigned as they are, save keys with `ctx.save.set` (the page's real save, in its browser profile).
   * @param {any} page
   * @param {{ store?: Record<string, unknown> | null, save?: Record<string, unknown> | null }} values
   * @returns {Promise<string[]>} warnings
   */
  async setGameValues(page, { store = null, save = null }) {
    return page.evaluate(setGameValuesInPage, store, save);
  }

  /**
   * Runs a scripted playtest in a fresh page: on game time (deterministic) unless `realtime`.
   * Times in `options` are game seconds.
   * @param {string} url
   * @param {PlaytestOptions} options
   * @param {AbortSignal} [signal]
   * @param {(done: number, total: number) => void} [onProgress]
   */
  async playtest(url, options, signal, onProgress) {
    const plan = planPlaytest(options);
    if (options.realtime) return this.#playtestRealtime(url, options, plan, signal);
    const size = options.size ?? PLAYTEST_SIZE;
    const { page, logs, loadMs } = await this.openGame(url, { size, hold: true });
    try {
      const supported = await page.evaluate(() => typeof (/** @type {any} */ (window).__gs?.test?.advance) === 'function');
      if (!supported) {
        await closePage(page);
        const result = await this.#playtestRealtime(url, options, plan, signal);
        result.warnings.unshift('This project\'s engine has no test clock (window.__gs.test), so the run used wall-clock time and is not reproducible. Update @gamestudio/engine.');
        return result;
      }
      await page.waitForFunction(() => /** @type {any} */ (window).__gs?.state === 'playing', { timeout: 10_000, polling: 50 }).catch(() => {});
      /** @type {string[]} */
      const warnings = [];
      const t0 = Date.now();
      warnings.push(...(await page.evaluate(beginTest, options.seed ?? 1, options.set?.store ?? null, options.set?.save ?? null)));
      /** @type {any[]} */
      const placed = [];
      for (const s of plan.start) {
        const result = await page.evaluate(placeEntity, s.name ?? null, placement(s));
        if (result.error) throw new ToolError(`start: ${result.error}.`, { code: 'invalid_arguments', hint: 'start.name is an entity (object) name; leave it out to move the player (the first entity with CharacterController).' });
        placed.push({ name: result.name, position: result.position, rotation: result.rotation });
      }
      /** @type {string[]} */
      const track = options.track ?? (await page.evaluate(trackDefaults));
      // Exact names go to the engine's tracker. A small tracker in the page (installTracker) counts
      // copies that share a tracked name, and follows patterns ("Boulder*") and samples positions,
      // which needs the page to step one fixed step at a time.
      const exact = track.filter((n) => !isPattern(n));
      const patterns = track.filter(isPattern);
      await page.evaluate((/** @type {string[]} */ names, /** @type {any[]} */ inputs) => {
        const test = /** @type {any} */ (window).__gs.test;
        test.track(names);
        test.schedule(inputs);
      }, exact, plan.inputs);
      const tracking = Boolean(await page.evaluate(installTracker, { exact, patterns, sampleSteps: plan.sampleSteps, max: options.track_max ?? TRACK_MAX, aims: plan.aims }));
      const perStep = tracking && (patterns.length > 0 || plan.sampleSteps > 0 || plan.aims.length > 0);
      if (!tracking && (patterns.length > 0 || plan.sampleSteps > 0 || plan.aims.length > 0)) {
        warnings.push("This project's engine cannot report spawned entities, so track patterns and sample_every were ignored. Update @gamestudio/engine.");
      }

      /** @type {{ time_s: number, positions: Record<string, number[] | null>, image: string }[]} */
      const screenshots = [];
      const targets = [...new Set([...plan.shotSteps, plan.steps])].sort((a, b) => a - b);
      let step = 0;
      /** @type {any} */
      let status = null;
      let renderMs = 0;
      for (const target of targets) {
        while (step < target) {
          if (signal?.aborted) throw new ToolError('playtest was cancelled.', { code: 'cancelled' });
          const n = Math.min(CHUNK_STEPS, target - step);
          status = tracking
            ? await page.evaluate(advanceTracked, n, perStep)
            : await page.evaluate((/** @type {number} */ k) => /** @type {any} */ (window).__gs.test.advance(k), n);
          step += n;
          onProgress?.(step, plan.steps);
        }
        for (const _ of plan.shotSteps.filter((s) => s === target)) {
          renderMs += await page.evaluate(() => /** @type {any} */ (window).__gs.test.render());
          const image = await this.screenshot(page);
          /** @type {Record<string, number[] | null>} */
          const all = await page.evaluate((/** @type {string[]} */ names) => /** @type {any} */ (window).__gs.test.positions(names), exact);
          const positions = Object.fromEntries(Object.entries(all).filter(([, p]) => p !== null)); // destroyed ones are left out
          if (perStep && patterns.length) Object.assign(positions, await page.evaluate(trackerPositions));
          screenshots.push({ time_s: round(target * STEP_S), positions, image });
        }
      }
      status ??= await page.evaluate(() => /** @type {any} */ (window).__gs.test.status());
      const wallS = (Date.now() - t0) / 1000;
      /** @type {any[]} */
      const report = await page.evaluate(() => /** @type {any} */ (window).__gs.test.report());
      /** @type {TrackerReport | null} */
      const extra = tracking ? await page.evaluate(trackerReport) : null;
      const final = await page.evaluate(finalState, options.state ?? null);
      // What the run left in its own ctx.save (persistence logic: a best time, unlocked levels).
      const saved = await page.evaluate(savedInPage);
      if (saved) final.save = saved;
      const hud = String(await page.evaluate(() => document.body?.innerText ?? '')).trim().slice(0, 1000);
      /** @type {PageLog[]} */
      const pageLogs = await page.evaluate(() => /** @type {any} */ (window).__gsTestLogs ?? []);
      const allLogs = [...pageLogs, ...logs.filter((l) => l.t === null)];

      if (status.loads > 0) warnings.push(`The scene reloaded ${status.loads} time(s) during the run (a re-export or hot reload), so this run is not reproducible.`);
      if (status.idleSteps > 0) warnings.push(`The game was paused, stopped or loading for ${status.idleSteps} of ${status.step} steps.`);
      const missing = report.filter((r) => !r.found).map((r) => r.name);
      if (missing.length) warnings.push(`No entity named ${missing.map((n) => `"${n}"`).join(', ')} to track.`);
      // After a reload or a level change, the same name in the new scene is not a copy.
      const shared = status.loads > 0 || status.sceneChanges > 0 ? [] : Object.entries(extra?.named ?? {}).filter(([, n]) => n > 1);
      for (const [name, n] of shared) {
        warnings.push(`${n} entities were named "${name}" during the run (spawned copies?); tracking follows one at a time. Track "${name}*" to follow each copy.`);
      }
      const tracked = [
        ...report.map((r) => ({
          name: r.name,
          ...(r.found
            ? {
                start: r.start,
                final: r.final,
                min: r.min,
                max: r.max,
                path_m: r.path,
                moved_m: round(Math.hypot(r.final[0] - r.start[0], r.final[1] - r.start[1], r.final[2] - r.start[2])),
                ...(r.destroyedAt !== null && { destroyed_at_s: r.destroyedAt }),
                ...(r.components && { components: r.components }),
                ...(Object.keys(r.changes).length && { changes: Object.fromEntries(Object.entries(r.changes).map(([k, v]) => [k, changeOut(v)])) }),
              }
            : { found: false }),
          ...(extra?.samples[r.name] && { samples: extra.samples[r.name] }),
        })),
        ...(extra?.patterns ?? []).map((p) => ({
          pattern: p.pattern,
          matched: p.count,
          alive_at_end: p.alive,
          ...(p.count > p.instances.length && { listed: p.instances.length }),
          instances: p.instances.map((i) => ({
            id: i.id,
            name: i.name,
            ...(i.spawned !== null && { spawned_at_s: i.spawned }),
            ...(i.destroyed !== null && { destroyed_at_s: i.destroyed }),
            start: i.start,
            final: i.final,
            min: i.min,
            max: i.max,
            path_m: i.path,
            moved_m: round(Math.hypot(i.final[0] - i.start[0], i.final[1] - i.start[1], i.final[2] - i.start[2])),
            ...(i.samples && { samples: i.samples }),
          })),
        })),
      ];
      for (const p of extra?.patterns ?? []) if (p.count === 0) warnings.push(`No entity matched the track pattern "${p.pattern}".`);
      for (const a of extra?.aims ?? []) if (!a.steps) warnings.push(`aim at ${JSON.stringify(a.target)} never found its target in view.`);
      /** @type {Record<string, any>} */
      const sampling = {};
      if (plan.sampleSteps > 0) {
        const every = plan.sampleSteps * STEP_S;
        /** @type {any[]} */
        const holders = tracked.flatMap((t) => ('instances' in t ? t.instances : [t])).filter((t) => t.samples);
        const { lists, factor } = decimateSamples(holders.map((h) => dropStill(h.samples)), every);
        holders.forEach((h, i) => (h.samples = lists[i]));
        sampling.sample_every_s = round(every * factor);
        if (factor > 1) warnings.push(`sample_every was raised from ${round(every)} to ${round(every * factor)} s to keep the samples under ${MAX_SAMPLES_PER_ENTITY} per entity and ${MAX_SAMPLE_POINTS} in all; shorten the run or track fewer entities for finer samples.`);
      }
      // The level at the end, the level changes the game made (ctx.loadScene: they repeat with the run) and the open menu.
      /** @type {{ scene?: string, rev?: number, scene_changes?: number, screen?: string | null }} */
      const level = {};
      if (status.scene) Object.assign(level, { scene: status.scene.name, rev: status.scene.rev });
      if (status.sceneChanges !== undefined) level.scene_changes = status.sceneChanges;
      if (status.screen !== undefined) level.screen = status.screen;
      return {
        mode: 'game time',
        game_time_s: round(status.step * STEP_S),
        steps: status.step,
        ...level,
        wall_s: round(wallS, 2),
        speed: round(status.step * STEP_S / Math.max(wallS, 1e-3), 2),
        fps: Math.round(status.step / Math.max(wallS, 1e-3)),
        simulate_ms: status.simulateMs,
        render_ms: round(renderMs, 1),
        load_ms: loadMs,
        ...this.renderInfo(),
        size,
        inputs: plan.actions.length,
        ...(placed.length && { start: placed }),
        ...sampling,
        ...(extra?.aims?.length && { aims: extra.aims }),
        tracked,
        final,
        hud,
        logs: allLogs.slice(-100),
        errors: allLogs.filter((l) => l.level === 'error').length,
        warnings,
        screenshots,
      };
    } finally {
      if (!page.isClosed()) await closePage(page);
    }
  }

  /**
   * The wall-clock playtest: inputs are sent while the game runs in real time.
   * @param {string} url
   * @param {PlaytestOptions} options
   * @param {ReturnType<typeof planPlaytest>} plan
   * @param {AbortSignal} [signal]
   */
  async #playtestRealtime(url, options, plan, signal) {
    const actions = plan.actions.map((a) => ({ ...a, at: a.at * 1000 }));
    const durationMs = plan.duration * 1000;
    const shots = plan.shots.map((t) => Math.round(t * 1000));
    const size = options.size ?? PLAYTEST_SIZE;
    const { page, logs, loadMs } = await this.openGame(url, { size });
    /** @type {Set<string>} */
    const held = new Set();
    try {
      await page.evaluate(() => {
        const canvas = document.querySelector('canvas');
        if (canvas) {
          canvas.tabIndex = canvas.tabIndex >= 0 ? canvas.tabIndex : 0;
          canvas.focus();
        }
      });
      await page.waitForFunction(() => /** @type {any} */ (window).__gs?.state === 'playing', { timeout: 10_000, polling: 50 }).catch(() => {});
      /** @type {any[]} */
      const placed = [];
      for (const s of plan.start) {
        const result = await page.evaluate(placeEntity, s.name ?? null, placement(s));
        if (result.error) throw new ToolError(`start: ${result.error}.`, { code: 'invalid_arguments' });
        placed.push({ name: result.name, position: result.position, rotation: result.rotation });
      }
      const track = (options.track ?? (await page.evaluate(trackDefaults))).filter((/** @type {string} */ n) => !isPattern(n));
      const before = await page.evaluate(positionsOf, track);
      const clock0 = await page.evaluate(gameClock);
      /** @type {{ at: number, kind: string, run: () => Promise<void> }[]} */
      const timeline = [];
      /** @type {{ time_s: number, actual_s: number, image: string }[]} */
      const screenshots = [];
      const t0 = Date.now();
      for (const a of actions) {
        timeline.push({
          at: a.at,
          kind: 'input',
          run: async () => {
            if (a.kind === 'key' && a.key) {
              if (a.action === 'down') {
                await page.keyboard.down(a.key);
                held.add(a.key);
              } else if (a.action === 'up') {
                await page.keyboard.up(a.key);
                held.delete(a.key);
              } else await page.keyboard.press(a.key);
            } else if (a.kind === 'mouse') {
              const x = a.x ?? size[0] / 2;
              const y = a.y ?? size[1] / 2;
              const button = /** @type {any} */ (a.button);
              if (a.action === 'move') await page.mouse.move(x, y, { steps: 4 });
              else if (a.action === 'down') {
                await page.mouse.move(x, y);
                await page.mouse.down({ button });
              } else if (a.action === 'up') await page.mouse.up({ button });
              else if (a.action === 'click') await page.mouse.click(x, y, { button });
              else if (a.action === 'wheel') await page.mouse.wheel({ deltaX: a.dx ?? 0, deltaY: a.dy ?? 0 });
            }
          },
        });
      }
      for (const at of shots) {
        timeline.push({
          at,
          kind: 'screenshot',
          run: async () => {
            const actual = (Date.now() - t0) / 1000;
            screenshots.push({ time_s: at / 1000, actual_s: round(actual, 2), image: await this.screenshot(page) });
          },
        });
      }
      // Inputs before screenshots at the same time, so "hold W at 0, screenshot at 0" shows the start.
      timeline.sort((a, b) => a.at - b.at || (a.kind === 'screenshot' ? 1 : -1));
      for (const item of timeline) {
        if (signal?.aborted) throw new ToolError('playtest was cancelled.', { code: 'cancelled' });
        const wait = t0 + item.at - Date.now();
        if (wait > 0) await sleep(wait);
        await item.run();
      }
      const remaining = t0 + durationMs - Date.now();
      if (remaining > 0) await sleep(remaining);
      for (const key of held) await page.keyboard.up(key).catch(() => {});
      const wallS = (Date.now() - t0) / 1000;
      const clock1 = await page.evaluate(gameClock);
      const after = await page.evaluate(positionsOf, track);
      const final = await page.evaluate(finalState, options.state ?? null);
      const tracked = await page.evaluate(trackedNow, track);
      const hud = String(await page.evaluate(() => document.body?.innerText ?? '')).trim().slice(0, 1000);
      const gameTime = clock1.time - clock0.time;
      const fps = (clock1.frames - clock0.frames) / Math.max(wallS, 1e-3);
      const speed = gameTime / Math.max(wallS, 1e-3);
      /** @type {string[]} */
      const warnings = [];
      if (speed < 0.8) warnings.push(`The game ran at ${round(speed, 2)}× real time (${round(fps, 1)} fps), so inputs covered less game time than asked. Drop realtime (game time is exact), or use a smaller size.`);
      if (options.sample_every !== undefined || (options.track ?? []).some(isPattern) || plan.aims.length) warnings.push('track patterns, sample_every and aim need game time: they were ignored in this realtime run.');
      if (options.set) warnings.push("set needs game time (a playtest's own ctx.save): it was ignored in this realtime run.");
      return {
        mode: 'realtime',
        game_time_s: round(gameTime),
        wall_s: round(wallS, 2),
        speed: round(speed, 2),
        fps: round(fps, 1),
        load_ms: loadMs,
        ...this.renderInfo(),
        size,
        inputs: actions.length,
        ...(placed.length && { start: placed }),
        tracked: track.map((/** @type {string} */ name) => {
          const a = before[name];
          const b = after[name];
          if (!a && !b) return { name, found: false };
          return {
            name,
            start: a ?? null,
            final: b ?? null,
            moved_m: a && b ? round(Math.hypot(b[0] - a[0], b[1] - a[1], b[2] - a[2])) : null,
            ...(tracked[name] && { components: tracked[name] }),
          };
        }),
        final,
        hud,
        logs: logs.slice(-100),
        errors: logs.filter((l) => l.level === 'error').length,
        warnings,
        screenshots,
      };
    } finally {
      await closePage(page);
    }
  }

  /** What draws the headless games, for tool results: the WebGL renderer and whether it is a GPU (empty before the browser starts). */
  renderInfo() {
    return this.rendering ? { renderer: this.rendering.renderer, gpu: this.rendering.gpu } : {};
  }

  async close() {
    const browser = this.browser;
    this.browser = null;
    this.game = null;
    await browser?.close().catch(() => {});
  }
}

/**
 * Unloads the game before closing, so its live-link WebSocket closes right away (a closed
 * page can otherwise stay on the hub's peer list for a while).
 * @param {any} page
 */
async function closePage(page) {
  await page.goto('about:blank', { timeout: 5000 }).catch(() => {});
  await page.close().catch(() => {});
}

/** @param {[number, number]} a @param {[number, number]} b */
const sameSize = (a, b) => a[0] === b[0] && a[1] === b[1];

/** @param {unknown} error */
const firstLine = (error) => (error instanceof Error ? error.message : String(error)).split('\n')[0];

/**
 * Whether WebGL2 works in `browser` and what draws it, checked in a blank page that is closed
 * afterwards. Never throws: a failure or no answer within `timeoutMs` comes back as `error`.
 * @param {any} browser
 * @param {number} [timeoutMs]
 * @returns {Promise<WebGLProbe>}
 */
export async function probeWebGL(browser, timeoutMs = PROBE_TIMEOUT_MS) {
  const check = (async () => {
    const page = await browser.newPage();
    try {
      return /** @type {WebGLProbe} */ (await page.evaluate(webglInPage));
    } finally {
      await page.close().catch(() => {});
    }
  })();
  /** @type {NodeJS.Timeout | undefined} */
  let timer;
  /** @type {Promise<WebGLProbe>} */
  const late = new Promise((resolve) => {
    timer = setTimeout(() => resolve({ webgl2: false, renderer: null, error: `no answer within ${timeoutMs / 1000} s` }), timeoutMs);
  });
  try {
    return await Promise.race([check, late]);
  } catch (error) {
    return { webgl2: false, renderer: null, error: firstLine(error) };
  } finally {
    clearTimeout(timer);
    check.catch(() => {});
  }
}

// --- Functions evaluated in the page ------------------------------------------------------

/** Whether WebGL2 works, and the real name of what draws it (WEBGL_debug_renderer_info). @returns {WebGLProbe} */
function webglInPage() {
  try {
    const gl = document.createElement('canvas').getContext('webgl2');
    if (!gl) return { webgl2: false, renderer: null };
    const info = gl.getExtension('WEBGL_debug_renderer_info');
    const renderer = String(gl.getParameter(info ? info.UNMASKED_RENDERER_WEBGL : gl.RENDERER) ?? '') || null;
    gl.getExtension('WEBGL_lose_context')?.loseContext();
    return { webgl2: true, renderer };
  } catch (error) {
    return { webgl2: false, renderer: null, error: String(error) };
  }
}

/**
 * Resolves after two animation frames (the game has drawn at a new size), or after timeoutMs.
 * @param {number} timeoutMs
 */
function nextFrames(timeoutMs) {
  return new Promise((resolve) => {
    const timer = setTimeout(() => resolve(false), timeoutMs);
    requestAnimationFrame(() => requestAnimationFrame(() => {
      clearTimeout(timer);
      resolve(true);
    }));
  });
}

/**
 * Waits until the game knows the UI canvas `name` (ctx.ui.canvases: ui/index.json, or the live link), or
 * timeoutMs passes. Runs in the page.
 * @param {string} name @param {number} timeoutMs
 * @returns {Promise<{ supported: boolean, known: boolean, canvases: string[] }>}
 */
function waitForUiCanvas(name, timeoutMs) {
  const ui = /** @type {any} */ (window).__gs?.game?.ui;
  const canvases = () => (Array.isArray(ui?.canvases) ? /** @type {string[]} */ ([...ui.canvases]) : null);
  if (!canvases()) return Promise.resolve({ supported: false, known: false, canvases: [] });
  const started = Date.now();
  return new Promise((resolve) => {
    const tick = () => {
      const now = canvases() ?? [];
      if (now.includes(name) || Date.now() - started >= timeoutMs) resolve({ supported: true, known: now.includes(name), canvases: now });
      else setTimeout(tick, 50);
    };
    tick();
  });
}

/**
 * Opens the UI canvas `name` (the `ui.open` live-link message) and resolves once it is ready for a picture. Runs in
 * the page:
 * 1. another open menu (test.state().ui.screen) is closed (`ui.close`) and its close animation waited for, until
 *    it leaves ui.open (up to timeouts.close), so two menus never overlap;
 * 2. the canvas is opened, and reported open in ui.open (up to timeouts.open);
 * 3. its open animation is waited for: until ui.animating no longer lists it (up to timeouts.animation), or, with
 *    an engine that does not report ui.animating, the canvas's open duration + 100 ms (from the canvas data, else
 *    timeouts.openSeconds, from its export);
 * 4. two frames are drawn.
 * @param {string} name
 * @param {{ open: number, close: number, animation: number, openSeconds?: number }} timeouts milliseconds
 * @returns {Promise<{ opened: boolean, closed: string | null, animating: boolean }>}
 */
async function openUiCanvasInPage(name, timeouts) {
  const gs = /** @type {any} */ (window).__gs;
  const ui = () => {
    try {
      return gs.test?.state?.({ ids: [] })?.ui ?? null;
    } catch {
      return null;
    }
  };
  const isOpen = () => {
    const open = ui()?.open;
    try {
      return Array.isArray(open) ? open.includes(name) : Boolean(gs.game.ui.isOpen?.(name));
    } catch {
      return false;
    }
  };
  const wait = (/** @type {number} */ ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const until = async (/** @type {() => boolean} */ done, /** @type {number} */ ms) => {
    const started = Date.now();
    while (!done()) {
      if (Date.now() - started >= ms) return false;
      await wait(20);
    }
    return true;
  };
  const frame = typeof requestAnimationFrame === 'function' ? requestAnimationFrame : (/** @type {() => void} */ f) => setTimeout(f, 16);
  const screen = ui()?.screen;
  const closed = typeof screen === 'string' && screen !== name ? screen : null;
  if (closed) {
    gs.message({ t: 'ui.close' });
    await until(() => !(ui()?.open ?? []).includes(closed), timeouts.close);
  }
  gs.message({ t: 'ui.open', canvas: name });
  if (!(await until(isOpen, timeouts.open))) return { opened: false, closed, animating: false };
  let animating = false;
  if (Array.isArray(ui()?.animating)) {
    animating = !(await until(() => !(ui()?.animating ?? []).includes(name), timeouts.animation));
  } else {
    let seconds = timeouts.openSeconds ?? 0;
    try {
      // The canvas data the game plays (an engine without ui.animating keeps it on its canvas layer).
      const d = gs.game.ui.screens?.layer?.data?.(name)?.animations?.open?.duration;
      if (typeof d === 'number' && Number.isFinite(d)) seconds = d;
    } catch {
      // the fallback from the export
    }
    if (seconds > 0) await wait(Math.min(seconds * 1000 + 100, timeouts.animation));
  }
  await new Promise((resolve) => frame(() => frame(() => resolve(true))));
  return { opened: true, closed, animating };
}

/**
 * Sets ctx.store values (assigned key by key) and ctx.save values (ctx.save.set) in the game. Runs in the page.
 * @param {Record<string, unknown> | null} store @param {Record<string, unknown> | null} save
 * @returns {string[]} warnings
 */
function setGameValuesInPage(store, save) {
  const game = /** @type {any} */ (window).__gs?.game;
  /** @type {string[]} */
  const warnings = [];
  if (store) {
    if (game?.store && typeof game.store === 'object') Object.assign(game.store, store);
    else warnings.push("This project's engine does not expose ctx.store to tools, so set.store was ignored. Update @gamestudio/engine.");
  }
  if (save) {
    if (typeof game?.save?.set === 'function') for (const [key, value] of Object.entries(save)) game.save.set(key, value);
    else warnings.push("This project's engine does not expose ctx.save to tools, so set.save was ignored. Update @gamestudio/engine.");
    // The UI language is read from the save at start: switch the running page too, so the shot is in it.
    if (typeof save['gs.locale'] === 'string' && game?.ui && 'locale' in game.ui) game.ui.locale = save['gs.locale'];
  }
  return warnings;
}

/**
 * What a playtest left in its ctx.save, sorted by key, the engine's own gs. keys included (gs.locale: the UI
 * language; gs.audio: the channel volumes), so settings screens can be checked: game.test.saved({internal: true}),
 * or, with an older engine, the same read from the game's save store (the test's own while it holds the clock);
 * null without either. Runs in the page.
 * @returns {Record<string, unknown> | null}
 */
export function savedInPage() {
  const gs = /** @type {any} */ (window).__gs;
  try {
    if (typeof gs?.test?.saved === 'function') return gs.test.saved({ internal: true }) ?? null;
    const save = gs?.game?.save;
    if (typeof save?.keys !== 'function') return null;
    const keys = [...save.keys()].sort();
    return Object.fromEntries(keys.map((/** @type {string} */ key) => [key, save.get(key)]));
  } catch {
    return null;
  }
}

/**
 * Starts a playtest (game.test.begin) with its seed and the ctx.store and ctx.save values to start from (playtest
 * set). An engine whose begin does not take them (no test.saved either) gets them set after begin, so they go into
 * the test's own save store. Runs in the page.
 * @param {number} seed @param {Record<string, unknown> | null} store @param {Record<string, unknown> | null} save
 * @returns {Promise<string[]>} warnings
 */
async function beginTest(seed, store, save) {
  const gs = /** @type {any} */ (window).__gs;
  const test = gs.test;
  const seeds = typeof test.saved === 'function';
  await test.begin({ seed, ...(seeds && store && { store }), ...(seeds && save && { save }) });
  /** @type {string[]} */
  const warnings = [];
  if (seeds) return warnings;
  const game = gs.game;
  if (store) {
    if (game?.store && typeof game.store === 'object') Object.assign(game.store, store);
    else warnings.push("This project's engine cannot start a test with ctx.store values, so set.store was ignored. Update @gamestudio/engine.");
  }
  if (save) {
    if (typeof game?.save?.set === 'function') for (const [key, value] of Object.entries(save)) game.save.set(key, value);
    else warnings.push("This project's engine cannot start a test with ctx.save values, so set.save was ignored. Update @gamestudio/engine.");
  }
  return warnings;
}

/**
 * The open UI now (docs/GAME-UI.md §5.7: screen, focus, buttons, size, open, texts, hidden), or null. Runs in the
 * page. hidden: the sorted "Canvas/Name" elements of open canvases hidden by code or by Disable in Renders.
 * Button x/y are CSS px of the page's canvas, whose size is `size` [w, h]; a playtest page of another size
 * scales them.
 */
export function uiStateInPage() {
  try {
    return /** @type {any} */ (window).__gs?.test?.state?.({ ids: [] })?.ui ?? null;
  } catch {
    return null; // an engine without menus
  }
}

/** Runs before the page's scripts: hold the test clock, and keep console lines with their game time. */
function holdAndRecordLogs() {
  const w = /** @type {any} */ (window);
  w.__gsTestHold = true;
  /** @type {{ level: string, msg: string, t: number | null }[]} */
  const lines = (w.__gsTestLogs = []);
  for (const [method, level] of [['debug', 'debug'], ['log', 'log'], ['info', 'info'], ['warn', 'warn'], ['error', 'error']]) {
    const original = /** @type {any} */ (console)[method].bind(console);
    /** @type {any} */ (console)[method] = (/** @type {any[]} */ ...args) => {
      original(...args);
      let t = null;
      try {
        t = w.__gs?.test?.holding ? w.__gs.test.status().time : null;
      } catch {
        // not ready yet
      }
      const msg = args.map((a) => (typeof a === 'string' ? a : a instanceof Error ? (a.stack ?? a.message) : (() => {
        try {
          return JSON.stringify(a);
        } catch {
          return String(a);
        }
      })())).join(' ');
      // Direct3D shader-compiler warnings in a three.js program log (isShaderCompilerNoise).
      const program = 'THREE.WebGLProgram: Program Info Log:';
      if (level === 'warn' && msg.startsWith(program)) {
        const notes = msg.slice(program.length).split('\n').map((l) => l.trim()).filter(Boolean);
        if (notes.length && notes.every((l) => /^\(\d+,\d+(?:-\d+)?\): warning X\d+:/.test(l))) return;
      }
      lines.push({ level, msg: msg.slice(0, 2000), t });
      if (lines.length > 500) lines.shift();
    };
  }
}

/**
 * @typedef {{ id: string, name: string, spawned: number | null, destroyed: number | null, start: number[],
 *   final: number[], min: number[], max: number[], path: number, samples?: number[][] }} TrackedInstance
 * @typedef {{ samples: Record<string, number[][]>, named: Record<string, number>, aims?: any[],
 *   patterns: { pattern: string, count: number, alive: number, instances: TrackedInstance[] }[] }} TrackerReport
 */

/**
 * Installs window.__gsMcpTracker (runs in the page). It counts the entities that carry each exact
 * tracked name (spawned copies share their prefab's name), follows every entity whose name matches
 * a pattern from when it appears to when it is destroyed, and samples positions of those and of the
 * exact names every `sampleSteps` steps. advanceTracked() feeds it. False when the engine is too old.
 * @param {{ exact: string[], patterns: string[], sampleSteps: number, max: number, aims?: any[] }} config
 */
function installTracker(config) {
  const gs = /** @type {any} */ (window).__gs;
  const world = gs.world;
  if (typeof world?.onChange !== 'function' || typeof world.all !== 'function') return false;
  const STEP = 1 / 60;
  const r2 = (/** @type {number} */ n) => Math.round(n * 100) / 100;
  const r3 = (/** @type {number} */ n) => Math.round(n * 1000) / 1000;
  const glob = (/** @type {string} */ p) => new RegExp(`^${p.replace(/[.+^${}()|[\]\\]/g, '\\$&').replace(/\*/g, '.*').replace(/\?/g, '.')}$`);
  const position = (/** @type {any} */ e) => {
    e.object3d.updateWorldMatrix(true, false);
    const m = e.object3d.matrixWorld.elements;
    return [m[12], m[13], m[14]];
  };
  /** @type {{ pattern: string, regex: RegExp, count: number, records: any[], unlisted: any[] }[]} */
  const groups = config.patterns.map((pattern) => ({ pattern, regex: glob(pattern), count: 0, records: [], unlisted: [] }));
  /** @type {any[]} */
  const live = [];
  /** @type {any[]} */
  const pending = [];
  /** @type {Record<string, number>} */
  const named = Object.fromEntries(config.exact.map((n) => [n, 0]));
  /** @type {Record<string, number[][]>} */
  const samples = Object.fromEntries(config.exact.map((n) => [n, []]));
  const due = (/** @type {number} */ step) => config.sampleSteps > 0 && step % config.sampleSteps === 0;
  const sampleExact = (/** @type {number} */ step) => {
    for (const name of config.exact) {
      const e = world.get(name) ?? world.findByName(name);
      if (e) samples[name].push([r3(step * STEP), ...position(e).map(r2)]);
    }
  };
  const tracker = {
    step: 0,
    /** The step being simulated: destroys during it get its end time, like the engine's tracker. */
    current: 0,
    /** @param {any} e @param {number | null} spawned */
    adopt(e, spawned) {
      if (Object.hasOwn(named, e.name)) named[e.name]++;
      if (!e.alive) return;
      for (const g of groups) {
        if (!g.regex.test(e.name)) continue;
        g.count++;
        if (g.records.length >= config.max) {
          g.unlisted.push(e);
          continue;
        }
        const p = position(e);
        const record = { entity: e, id: e.id, name: e.name, spawned, destroyed: null, start: p, last: p, min: [...p], max: [...p], path: 0, samples: /** @type {number[][]} */ ([]) };
        g.records.push(record);
        live.push(record);
      }
    },
    /** Takes in the entities spawned since the last call (they have their final names by now). @param {number} step */
    flush(step) {
      tracker.step = step;
      for (const e of pending.splice(0)) tracker.adopt(e, r3(step * STEP));
    },
    /** After every step: new entities, then positions of the followed ones. @param {number} step */
    sample(step) {
      tracker.flush(step);
      const t = r3(step * STEP);
      const now = due(step);
      for (let i = live.length - 1; i >= 0; i--) {
        const r = live[i];
        if (!r.entity.alive) {
          if (r.destroyed === null) r.destroyed = t;
          live.splice(i, 1);
          continue;
        }
        const p = position(r.entity);
        r.path += Math.hypot(p[0] - r.last[0], p[1] - r.last[1], p[2] - r.last[2]);
        r.last = p;
        for (let k = 0; k < 3; k++) {
          r.min[k] = Math.min(r.min[k], p[k]);
          r.max[k] = Math.max(r.max[k], p[k]);
        }
        if (now) r.samples.push([t, ...p.map(r2)]);
      }
      if (now) sampleExact(step);
    },
    /** Before a step: point the mouse at each active aim's target, projected through the game's camera. @param {number} step */
    aim(step) {
      for (const a of aims) {
        if (step < a.from || step >= a.until) continue;
        let point = null;
        if (a.world) {
          point = gs.game.three.camera.position.clone().set(a.world[0], a.world[1], a.world[2]);
        } else {
          const regex = glob(a.target);
          const found = world.all().filter((/** @type {any} */ e) => e.alive && (regex.test(e.name) || e.id === a.target));
          if (!found.length) {
            a.misses++;
            continue;
          }
          let chosen = found[0];
          if (a.pick === 'nearest') {
            const player = world.query('CharacterController')[0];
            const from = player ? centreOf(player) : gs.game.three.camera.position;
            let best = Infinity;
            for (const e of found) {
              const d = centreOf(e).distanceTo(from);
              if (d < best) [best, chosen] = [d, e];
            }
          }
          point = centreOf(chosen);
          a.last = `${chosen.name}#${chosen.id}`;
        }
        const at = toCanvas(point);
        if (!at) {
          a.misses++;
          continue;
        }
        gs.test.schedule([{ step, mouse: 'move', x: Math.round(at.x * 10) / 10, y: Math.round(at.y * 10) / 10 }]);
        a.steps++;
      }
    },
    /** Positions of the pattern-tracked entities alive now, keyed "name#id". */
    positions() {
      return Object.fromEntries(live.filter((r) => r.entity.alive).map((r) => [`${r.name}#${r.id}`, position(r.entity).map(r3)]));
    },
    /** @returns {any} */
    report() {
      const v = (/** @type {number[]} */ p) => p.map(r3);
      return {
        samples: config.sampleSteps > 0 ? samples : {},
        named,
        aims: aims.map((a) => ({ target: a.target ?? a.world, steps: a.steps, ...(a.misses && { no_target_steps: a.misses }), ...(a.last && { last: a.last }) })),
        patterns: groups.map((g) => ({
          pattern: g.pattern,
          count: g.count,
          alive: g.records.filter((r) => r.entity.alive).length + g.unlisted.filter((e) => e.alive).length,
          instances: g.records.map((r) => ({
            id: r.id, name: r.name, spawned: r.spawned, destroyed: r.destroyed, start: v(r.start), final: v(r.last),
            min: v(r.min), max: v(r.max), path: r3(r.path), ...(config.sampleSteps > 0 && { samples: r.samples }),
          })),
        })),
      };
    },
  };
  /** @type {any[]} */
  const aims = (config.aims ?? []).map((/** @type {any} */ a) => ({ ...a, steps: 0, misses: 0, last: null }));
  // Where an entity is aimed at: the centre of its first mesh (the origin may be at the feet).
  const centreOf = (/** @type {any} */ e) => {
    /** @type {any} */
    let mesh = null;
    e.object3d.traverse((/** @type {any} */ o) => {
      if (!mesh && o.isMesh && o.geometry) mesh = o;
    });
    if (mesh) {
      if (!mesh.geometry.boundingSphere) mesh.geometry.computeBoundingSphere();
      mesh.updateWorldMatrix(true, false);
      return mesh.geometry.boundingSphere.center.clone().applyMatrix4(mesh.matrixWorld);
    }
    e.object3d.updateWorldMatrix(true, false);
    return e.object3d.getWorldPosition(e.object3d.position.clone());
  };
  // A world point in canvas CSS pixels through the game's active camera, or null behind it.
  const toCanvas = (/** @type {any} */ point) => {
    const camera = gs.game.three.camera;
    camera.updateMatrixWorld();
    const ndc = point.clone().project(camera);
    if (!(ndc.z > -1 && ndc.z < 1)) return null;
    const rect = document.querySelector('canvas')?.getBoundingClientRect() ?? { width: innerWidth, height: innerHeight };
    return { x: ((ndc.x + 1) / 2) * rect.width, y: ((1 - ndc.y) / 2) * rect.height };
  };
  // Destroys are noted when they happen; spawns are taken in after their step, once named.
  world.onChange((/** @type {any} */ ev) => {
    if (ev.type === 'spawn') pending.push(ev.entity);
    else if (ev.type === 'destroy') {
      for (const r of live) if (r.entity === ev.entity && r.destroyed === null) r.destroyed = r3(tracker.current * STEP);
    }
  });
  tracker.step = gs.test.status().step;
  for (const e of world.all()) tracker.adopt(e, null);
  if (due(tracker.step)) {
    for (const r of live) r.samples.push([r3(tracker.step * STEP), ...r.start.map(r2)]);
    sampleExact(tracker.step);
  }
  /** @type {any} */ (window).__gsMcpTracker = tracker;
  return true;
}

/**
 * Runs `n` steps (runs in the page). perStep: one at a time, feeding the tracker after each
 * (patterns and samples); otherwise in one go, then the tracker takes in what spawned.
 * @param {number} n
 * @param {boolean} perStep
 */
async function advanceTracked(n, perStep) {
  const test = /** @type {any} */ (window).__gs.test;
  const tracker = /** @type {any} */ (window).__gsMcpTracker;
  if (!perStep) {
    const status = await test.advance(n);
    tracker?.flush(status.step);
    return status;
  }
  let status = null;
  for (let i = 0; i < n; i++) {
    tracker.current = tracker.step + 1;
    tracker.aim(tracker.step);
    status = await test.advance(1);
    tracker.sample(status.step);
  }
  return status;
}

function trackerPositions() {
  return /** @type {any} */ (window).__gsMcpTracker?.positions() ?? {};
}

function trackerReport() {
  return /** @type {any} */ (window).__gsMcpTracker?.report() ?? null;
}

function trackDefaults() {
  const gs = /** @type {any} */ (window).__gs;
  try {
    return [...gs.world.query('CharacterController')].map((/** @type {any} */ e) => e.name).slice(0, 5);
  } catch {
    return [];
  }
}

/**
 * @param {string | null} name
 * @param {{ position?: number[], rotation?: number[], yaw?: number }} pose
 */
function placeEntity(name, pose) {
  const gs = /** @type {any} */ (window).__gs;
  try {
    const s = gs.test.place(name, pose);
    return { name: s.name, position: s.position, rotation: s.rotation };
  } catch (error) {
    return { error: error instanceof Error ? error.message : String(error) };
  }
}

/** @param {string[]} names */
function positionsOf(names) {
  const gs = /** @type {any} */ (window).__gs;
  /** @type {Record<string, number[]>} */
  const out = {};
  for (const name of names) {
    const e = gs?.world?.findByName(name);
    if (!e) continue;
    e.object3d.updateWorldMatrix(true, false);
    const m = e.object3d.matrixWorld.elements;
    out[name] = [m[12], m[13], m[14]].map((v) => Math.round(v * 1000) / 1000);
  }
  return out;
}

/** @param {string[]} names */
function trackedNow(names) {
  const gs = /** @type {any} */ (window).__gs;
  /** @type {Record<string, any>} */
  const out = {};
  for (const name of names) {
    const e = gs?.world?.findByName(name);
    if (e) out[name] = gs.game.describe({ ids: [e.id] })[0]?.components;
  }
  return out;
}

/** The game's clock and a frame counter (for fps in real-time runs). */
function gameClock() {
  const w = /** @type {any} */ (window);
  if (!w.__gsFrames) {
    w.__gsFrames = { n: 0 };
    const tick = () => {
      w.__gsFrames.n++;
      requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
  }
  const time = w.__gs?.test?.status ? w.__gs.test.status().gameTime : (w.__gs?.game?.ctx?.time ?? 0);
  return { time, frames: w.__gsFrames.n };
}

/** @param {{ names?: string[], component?: string, limit?: number } | null} filter */
function finalState(filter) {
  const gs = /** @type {any} */ (window).__gs;
  const all = gs.game.describe({ limit: 2000 });
  /** @type {Record<string, any>} */
  const out = { state: gs.state, scene: gs.game.scene?.name ?? null, entity_count: all.length, space: 'runtime (Y-up, metres)' };
  try {
    // The open menu (screen, focus, buttons with x/y to click in a page of ui.size), the live particles and the navmesh.
    const now = gs.test?.state?.({ ids: [] });
    if (now?.ui) out.ui = now.ui;
    if (now?.vfx) out.vfx = now.vfx;
    if (now?.nav) out.nav = now.nav; // the navmesh: ready, triangles, NavAgents
  } catch {
    // an engine without menus or particles
  }
  if (filter) {
    const entities = all.filter((/** @type {any} */ e) => (!filter.names || filter.names.includes(e.name)) && (!filter.component || e.components?.[filter.component] !== undefined));
    out.entities = entities.slice(0, filter.limit ?? 50);
  }
  return out;
}
