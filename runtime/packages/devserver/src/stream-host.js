// @ts-check
// SPDX-License-Identifier: MIT
// The game stream host: plays the game in a browser it controls (puppeteer-core) and sends its frames
// to Blender, which shows them in an area and sends input back (blender/gamestudio/core/stream.py;
// wire format in stream-protocol.js). Run by bin/gamestudio-stream.js, one host per stream.
//
// The browser is new headless Edge/Chrome, which renders WebGL on the GPU (ANGLE on D3D11 on
// Windows), keeps full speed without a window and plays sound through the default output device.
// Puppeteer's --mute-audio is left out for that. Only SwiftShader (which the MCP server's
// playtests pick on purpose) is slow: 8-9 fps at 960x540 against 75 on a GPU. `mode: 'offscreen'`
// is a fallback, a normal window placed off the desktop. It renders as fast but shows in the
// taskbar. Without Edge or Chrome on the machine it is the Chrome Headless Shell GameStudio
// downloads (src/browser.js finds it): the same GPU, screencast, input and minimize with these
// switches (SwiftShader without them), but no window, so always headless.
//
// Frames come from CDP Page.startScreencast as JPEG and are passed through untouched: Blender
// decodes them with imbuf, so no image work happens here. The host exits when Blender disconnects
// or sends stop, when the parent process (Blender) dies, or on SIGTERM.
//
// Blender starts the host before the dev server has an address when Play starts both: the browser
// starts without a page and goes to the game on `navigate`, so the two start at the same time. The
// page's user agent ends with GameStudioStream/<pid> (`agent` in the status): the engine sends its
// user agent when it joins the live link, which tells Blender which game peer is the streamed one.
//
// Blender keeps the host between Plays. While no Blender area shows the game it pauses the frames,
// and when nobody plays it either (after Stop) it asks for `suspend`: the browser window is
// minimized until the next resume, which makes the page hidden. A hidden page renders nothing (no
// requestAnimationFrame) but its scripts and its live link keep running, so a waiting game costs
// next to nothing (about 1% of a core, against 15-40% rendering) and still gets the scene Play
// sends. Page.setWebLifecycleState('frozen') is not used: the page stays hidden after it thaws.

import { randomBytes } from 'node:crypto';
import { mkdtemp, readdir, rm, stat } from 'node:fs/promises';
import net from 'node:net';
import os from 'node:os';
import path from 'node:path';
import { browserCandidates, browserLabel, isHeadlessShell } from './browser.js';
import {
  FORMAT, MessageReader, RateLimiter, TYPE, encodeJson, frameHeader, jpegSize, keyEventParams, mouseEventParams,
} from './stream-protocol.js';

export { findBrowser } from './browser.js';

export const DEFAULTS = Object.freeze({ width: 960, height: 540, scale: 1, quality: 80, maxFps: 60, mode: 'headless' });
const HELLO_TIMEOUT_MS = 5_000;
/** Without a client this long after start, the host gives up (Blender failed to connect). */
const CONNECT_TIMEOUT_MS = 30_000;
const LOAD_TIMEOUT_MS = 60_000;
/** Frames are dropped while more than this is queued for Blender (it is not keeping up). */
const MAX_BACKLOG = 4 * 1024 * 1024;
const STATUS_INTERVAL_MS = 1_000;
const PARENT_POLL_MS = 1_000;
/** A burst of size commands (an area being dragged) is applied once. */
const RESIZE_DELAY_MS = 40;
const MAX_SIZE = 7680;
const PROFILE_PREFIX = 'gamestudio-stream-';
const STALE_PROFILE_MS = 60 * 60 * 1000;

/**
 * @typedef {{ send(method: string, params?: object): Promise<any>, on(event: string, fn: (e: any) => void): void }} Cdp
 * @typedef {object} BrowserPage   what the host needs from the browser (puppeteerPage() makes one)
 * @property {Cdp} cdp
 * @property {(url: string, timeoutMs: number) => Promise<void>} goto
 * @property {(fn: () => any) => Promise<any>} evaluate
 * @property {(fn: (reason: string) => void) => void} onClose   called once if the browser or page goes away
 * @property {() => Promise<void>} close
 * @property {string} name   e.g. "Edge 155"
 * @property {string} [userAgent]   the browser's own user agent (the host appends its mark)
 * @property {string} [devtools]   the browser's DevTools WebSocket URL
 * @typedef {{ url: string, browserPath: string, mode: string, profile: string, mute: boolean,
 *   size: { width: number, height: number }, log: (line: string) => void }} OpenOptions
 */

/**
 * Browser switches for a stream (puppeteer adds its defaults; --mute-audio is removed from those).
 * @param {string} mode 'headless' | 'offscreen'
 * @param {{ width: number, height: number }} size
 * @param {{ mute?: boolean, platform?: NodeJS.Platform, shell?: boolean, software?: boolean }} [options]
 *   shell: the browser is Chrome Headless Shell; software: software GL (GAMESTUDIO_BROWSER_GPU=0)
 */
export function browserArgs(mode, size, { mute = false, platform = process.platform, shell = false, software = false } = {}) {
  const args = [
    '--no-first-run', '--no-default-browser-check', '--autoplay-policy=no-user-gesture-required',
    // Full speed without focus or a visible window.
    '--disable-background-timer-throttling', '--disable-renderer-backgrounding', '--disable-backgrounding-occluded-windows',
    // The GPU, even a blocklisted one (still faster than software); SwiftShader only where there is
    // no GPU at all (VMs, CI), where Chrome would otherwise give the game no WebGL.
    '--ignore-gpu-blocklist', '--enable-unsafe-swiftshader',
  ];
  if (software) {
    // GAMESTUDIO_BROWSER_GPU=0: software GL (SwiftShader) everywhere, e.g. around a broken GPU driver.
    args.push('--use-angle=swiftshader');
  } else {
    // D3D11 on Windows; elsewhere the browser's own backend (Metal on macOS).
    if (platform === 'win32') args.push('--use-angle=d3d11');
    // Chrome Headless Shell draws with SwiftShader unless told to use the GPU (new headless Edge and
    // Chrome use it by default); it falls back to software where the GPU fails.
    if (shell) args.push('--enable-gpu');
  }
  if (mute) args.push('--mute-audio');
  if (mode === 'offscreen') {
    args.push('--window-position=-32000,-32000', `--window-size=${size.width},${size.height}`, '--disable-features=CalculateNativeWinOcclusion');
  }
  return args;
}

/**
 * GAMESTUDIO_BROWSER_GPU=0 (or false, off, no, swiftshader, software) asks for software GL, the way the
 * MCP server's headless browser reads it (mcp/src/browser.js gpuMode).
 * @param {string | undefined} value
 */
export function softwareRendering(value = process.env.GAMESTUDIO_BROWSER_GPU) {
  return ['0', 'false', 'off', 'no', 'swiftshader', 'software'].includes(String(value ?? '').trim().toLowerCase());
}

/**
 * Opens a page with puppeteer-core.
 * @param {OpenOptions} options
 * @returns {Promise<BrowserPage>}
 */
export async function puppeteerPage({ browserPath, mode, profile, mute, size, log }) {
  /** @type {any} */
  let puppeteer;
  try {
    puppeteer = (await import('puppeteer-core')).default;
  } catch {
    throw new Error('puppeteer-core is missing from the game packages (GameStudio\'s setup installs it with @gamestudio/devserver)');
  }
  const shell = isHeadlessShell(browserPath);
  const browser = await puppeteer.launch({
    executablePath: browserPath, headless: shell ? 'shell' : mode !== 'offscreen', userDataDir: profile, defaultViewport: null,
    ignoreDefaultArgs: ['--mute-audio'], args: browserArgs(mode, size, { mute, shell, software: softwareRendering() }),
    handleSIGINT: false, handleSIGTERM: false, handleSIGHUP: false, protocolTimeout: 30_000,
  });
  const page = (await browser.pages())[0] ?? (await browser.newPage());
  const cdp = await page.createCDPSession();
  const version = String(await browser.version()).replace(/^(HeadlessChrome|Chrome|Edg|MicrosoftEdge|HeadlessEdg)\//, '');
  const name = `${browserLabel(browserPath)} ${version.split('.')[0]}`;
  page.on('pageerror', (/** @type {any} */ e) => log(`[page] ${String(e?.message ?? e).split('\n')[0]}`));
  page.on('console', (/** @type {any} */ m) => {
    if (m.type() === 'error') log(`[page] ${m.text().slice(0, 500)}`);
  });
  /** @type {((reason: string) => void)[]} */
  const closeListeners = [];
  let closed = false;
  /** @param {string} reason */
  const fireClose = (reason) => {
    if (closed) return;
    closed = true;
    for (const fn of closeListeners) fn(reason);
  };
  browser.on('disconnected', () => fireClose('The browser closed'));
  page.on('error', (/** @type {any} */ e) => fireClose(`The game page crashed: ${e?.message ?? e}`));
  page.on('close', () => fireClose('The game page closed'));
  return {
    cdp,
    name,
    userAgent: String(await browser.userAgent()),
    devtools: browser.wsEndpoint(),
    goto: async (url, timeoutMs) => {
      await page.goto(url, { waitUntil: 'load', timeout: timeoutMs });
    },
    evaluate: (fn) => page.evaluate(fn),
    onClose: (fn) => closeListeners.push(fn),
    close: async () => {
      closed = true;
      const proc = browser.process();
      await Promise.race([browser.close().catch(() => {}), new Promise((r) => setTimeout(r, 3000))]);
      if (proc && proc.exitCode === null) proc.kill('SIGKILL');
    },
  };
}

/** WebGL renderer of the page's browser (evaluated in the page). */
function rendererInfo() {
  try {
    const doc = /** @type {any} */ (globalThis).document;
    const gl = doc.createElement('canvas').getContext('webgl2') ?? doc.createElement('canvas').getContext('webgl');
    if (!gl) return 'no WebGL';
    const ext = gl.getExtension('WEBGL_debug_renderer_info');
    return String(gl.getParameter(ext ? ext.UNMASKED_RENDERER_WEBGL : gl.RENDERER));
  } catch (error) {
    return `unknown (${error})`;
  }
}

/** @param {number} pid */
export function pidAlive(pid) {
  try {
    process.kill(pid, 0);
    return true;
  } catch (error) {
    return /** @type {any} */ (error)?.code === 'EPERM';
  }
}

/** @param {unknown} v @param {number} lo @param {number} hi @param {number} fallback */
function clampInt(v, lo, hi, fallback) {
  const n = Math.round(Number(v));
  return Number.isFinite(n) ? Math.min(hi, Math.max(lo, n)) : fallback;
}

/** Removes profiles of hosts that were killed (older than an hour; the browser's lock keeps live ones). */
async function removeStaleProfiles() {
  const dir = os.tmpdir();
  for (const name of await readdir(dir).catch(() => /** @type {string[]} */ ([]))) {
    if (!name.startsWith(PROFILE_PREFIX)) continue;
    const full = path.join(dir, name);
    const info = await stat(full).catch(() => null);
    if (info && Date.now() - info.mtimeMs > STALE_PROFILE_MS) await rm(full, { recursive: true, force: true }).catch(() => {});
  }
}

export class StreamHost {
  /**
   * @param {{ url?: string, width?: number, height?: number, scale?: number, quality?: number, maxFps?: number,
   *   mode?: string, browserPath?: string | null, parentPid?: number, mute?: boolean, token?: string,
   *   exposeDevtools?: boolean, log?: (line: string) => void, openPage?: (options: OpenOptions) => Promise<BrowserPage>,
   *   connectTimeoutMs?: number, agent?: string, env?: NodeJS.ProcessEnv }} options
   *   env: where browsers are looked for (src/browser.js), process.env by default.
   *   Without a url the browser starts and waits for a navigate command.
   */
  constructor(options) {
    /** The game's address; '' until Blender sends one. */
    this.url = options.url ?? '';
    /** Appended to the page's user agent once the browser runs (then sent in the status). */
    this.agentMark = options.agent ?? `GameStudioStream/${process.pid}`;
    this.agent = '';
    this.size = {
      width: clampInt(options.width, 16, MAX_SIZE, DEFAULTS.width),
      height: clampInt(options.height, 16, MAX_SIZE, DEFAULTS.height),
      scale: Number(options.scale) > 0 ? Math.min(4, Number(options.scale)) : DEFAULTS.scale,
    };
    this.quality = clampInt(options.quality, 1, 100, DEFAULTS.quality);
    this.maxFps = clampInt(options.maxFps, 0, 240, DEFAULTS.maxFps);
    this.mode = options.mode === 'offscreen' ? 'offscreen' : 'headless';
    this.browserPath = options.browserPath ?? null;
    this.parentPid = options.parentPid ?? 0;
    this.mute = !!options.mute;
    this.token = options.token ?? randomBytes(16).toString('hex');
    this.exposeDevtools = !!options.exposeDevtools;
    this.log = options.log ?? (() => {});
    this.openPage = options.openPage ?? puppeteerPage;
    this.connectTimeoutMs = options.connectTimeoutMs ?? CONNECT_TIMEOUT_MS;
    this.env = options.env ?? process.env;

    /** @type {net.Server | null} */
    this.server = null;
    /** @type {net.Socket | null} */
    this.client = null;
    /** @type {BrowserPage | null} */
    this.page = null;
    this.profile = '';
    /** @type {Record<string, unknown>} */
    this.status = { status: 'starting', message: 'Starting the browser' };
    this.renderer = '';
    this.paused = false;
    /** Blender asked for the page to be hidden while paused (nobody sees the game, nobody plays it). */
    this.suspendWanted = false;
    /** The browser window is minimized: the page is hidden and renders nothing. */
    this.hidden = false;
    /** @type {number | null} the browser window of the page (Browser.getWindowForTarget) */
    this.windowId = null;
    /** A page load is under way (a page is never hidden while it loads). */
    this.loading = false;
    this.casting = false;
    this.frame = 0;
    this.dropped = 0;
    /** Size of the last frame sent, for mapping mouse positions. */
    this.frameSize = { width: this.size.width, height: this.size.height };
    this.limiter = new RateLimiter(this.maxFps);
    /** @type {number[]} send times of the last second's frames */
    this.sent = [];
    this.mouse = { buttons: 0, x: 0, y: 0 };
    /** @type {Map<string, any>} held keys by code */
    this.held = new Map();
    /** @type {NodeJS.Timeout[]} */
    this.timers = [];
    /** @type {NodeJS.Timeout | null} */
    this.resizeTimer = null;
    /** The page is set up: a navigate command loads at once (before, #launch loads this.url). */
    this.pageReady = false;
    /** Counts page loads: a load that a newer navigate replaced gives up quietly. */
    this.loads = 0;
    this.stopping = false;
    /** @type {(reason: string) => void} */
    this.resolveClosed = () => {};
    /** Resolves with the reason once the host has shut down. */
    this.closed = new Promise((resolve) => {
      this.resolveClosed = resolve;
    });
  }

  /**
   * Listens on 127.0.0.1 (port 0: any free port).
   * @returns {Promise<{ port: number, token: string }>}
   */
  listen(port = 0) {
    return new Promise((resolve, reject) => {
      const server = net.createServer((socket) => this.#accept(socket));
      server.once('error', reject);
      server.listen(port, '127.0.0.1', () => {
        this.server = server;
        resolve({ port: /** @type {net.AddressInfo} */ (server.address()).port, token: this.token });
      });
    });
  }

  /** Launches the browser and watches the parent process. */
  start() {
    this.timers.push(setInterval(() => this.#sendStatus(), STATUS_INTERVAL_MS));
    if (this.parentPid > 0) {
      this.timers.push(setInterval(() => {
        if (!pidAlive(this.parentPid)) void this.shutdown('The parent process exited');
      }, PARENT_POLL_MS));
    }
    const connectTimer = setTimeout(() => {
      if (!this.client) void this.shutdown('No client connected');
    }, this.connectTimeoutMs);
    this.timers.push(connectTimer);
    void this.#launch().catch((error) => this.fail(error instanceof Error ? error.message : String(error)));
  }

  async #launch() {
    // The browser Blender chose (--browser) first, then every other one found; the next is tried
    // when one does not start.
    /** @type {{ path: string, source: string }[]} */
    const candidates = this.browserPath ? [{ path: this.browserPath, source: 'given' }] : [];
    for (const c of browserCandidates(this.env)) if (!candidates.some((k) => k.path === c.path)) candidates.push(c);
    if (!candidates.length) throw new Error('No browser to play in: no Edge, Chrome or Chromium, and GameStudio\'s own browser is not set up yet');
    void removeStaleProfiles();
    /** @type {BrowserPage | null} */
    let page = null;
    let found = candidates[0];
    /** @type {unknown} */
    let firstError = null;
    for (const candidate of candidates) {
      // Chrome Headless Shell has no window for 'offscreen'.
      const mode = isHeadlessShell(candidate.path) ? 'headless' : this.mode;
      this.profile = await mkdtemp(path.join(os.tmpdir(), PROFILE_PREFIX));
      try {
        page = await this.openPage({
          url: this.url, browserPath: candidate.path, mode, profile: this.profile, mute: this.mute,
          size: { width: Math.round(this.size.width / this.size.scale), height: Math.round(this.size.height / this.size.scale) }, log: this.log,
        });
        found = candidate;
        if (mode !== this.mode) this.log(`${browserLabel(candidate.path)} has no window: playing ${mode}`);
        this.mode = mode;
        break;
      } catch (error) {
        firstError ??= error;
        this.log(`${candidate.path} did not start: ${String(error instanceof Error ? error.message : error).split('\n')[0]}`);
        await rm(this.profile, { recursive: true, force: true, maxRetries: 10, retryDelay: 200 }).catch(() => {});
        this.profile = '';
        if (this.stopping) return;
      }
    }
    if (!page) throw firstError;
    const browserPath = found.path;
    if (this.stopping) {
      await page.close();
      await rm(this.profile, { recursive: true, force: true, maxRetries: 10, retryDelay: 200 }).catch(() => {});
      return;
    }
    this.page = page;
    this.log(`browser: ${page.name} (${browserPath}, ${found.source}), ${this.mode}`);
    page.onClose((reason) => this.fail(reason));
    page.cdp.on('Page.screencastFrame', (e) => this.#onFrame(e));
    page.cdp.on('Page.frameNavigated', (e) => {
      // A reload (hot reload of the game) can end the screencast; start it again.
      if (!e?.frame?.parentId && this.casting) void this.#restartScreencast();
    });
    await page.cdp.send('Page.enable').catch(() => {});
    await page.cdp.send('Emulation.setFocusEmulationEnabled', { enabled: true }).catch(() => {});
    await this.#markUserAgent(page);
    await this.#applySize();
    if (this.stopping) return;
    this.pageReady = true;
    if (this.url) await this.#load();
    else this.#setStatus({ status: 'starting', message: 'The browser is ready; waiting for the game to start' });
  }

  /**
   * Marks the page's user agent with this host. The engine sends its user agent when it joins the
   * live link, so Blender can tell the streamed game from games in other browsers.
   * @param {BrowserPage} page
   */
  async #markUserAgent(page) {
    if (!page.userAgent || !this.agentMark) return;
    try {
      await page.cdp.send('Emulation.setUserAgentOverride', { userAgent: `${page.userAgent} ${this.agentMark}` });
      this.agent = this.agentMark;
    } catch (error) {
      this.log(`could not mark the user agent: ${error instanceof Error ? error.message : error}`);
    }
  }

  /** Loads this.url in the page (again after a navigate command); ready once it has loaded. */
  async #load() {
    const page = this.page;
    if (!page || this.stopping || !this.url) return;
    const load = ++this.loads;
    const url = this.url;
    this.loading = true;
    try {
      await this.#syncHidden(); // shown while it loads (a game may wait for its first frame)
      this.#setStatus({ status: 'starting', message: `Loading ${url}` });
      if (!this.paused && !this.casting) await this.#startScreencast();
      try {
        await page.goto(url, LOAD_TIMEOUT_MS);
      } catch (error) {
        if (load !== this.loads || this.stopping) return; // a newer address replaced this load
        throw error;
      }
      if (load !== this.loads || this.stopping) return;
      this.renderer = String(await page.evaluate(rendererInfo).catch(() => 'unknown'));
      const software = /swiftshader|llvmpipe|softpipe|software|basic render/i.test(this.renderer);
      this.#setStatus({
        status: 'ready',
        message: software ? `Playing in ${page.name} with software rendering (no GPU): expect a low frame rate` : `Playing in ${page.name}`,
      });
      if (!this.paused) await this.#restartScreencast();
    } finally {
      if (load === this.loads) {
        this.loading = false;
        void this.#syncHidden(); // a suspend that came while the page loaded
      }
    }
  }

  /**
   * Hides the page (its browser window minimized) while Blender wants it suspended and frames are
   * paused, or shows it again: a hidden page renders nothing, so a game nobody sees or plays costs
   * next to nothing, while its scripts and live link keep running. Never during a load.
   */
  async #syncHidden() {
    const page = this.page;
    const want = !!(page && this.suspendWanted && this.paused && !this.loading && this.status.status === 'ready');
    if (!page || want === this.hidden) return;
    this.hidden = want;
    try {
      if (this.windowId === null) this.windowId = (await page.cdp.send('Browser.getWindowForTarget'))?.windowId ?? null;
      if (this.windowId === null) throw new Error('the page has no browser window');
      await page.cdp.send('Browser.setWindowBounds', { windowId: this.windowId, bounds: { windowState: want ? 'minimized' : 'normal' } });
    } catch (error) {
      this.log(`could not ${want ? 'hide' : 'show'} the page: ${error instanceof Error ? error.message : error}`);
      if (want) this.hidden = false;
    }
    this.#sendStatus();
  }

  /** @param {net.Socket} socket */
  #accept(socket) {
    if (this.client || this.stopping) {
      socket.destroy();
      return;
    }
    socket.setNoDelay(true);
    const reader = new MessageReader(1024 * 1024);
    let authed = false;
    const helloTimer = setTimeout(() => socket.destroy(), HELLO_TIMEOUT_MS);
    socket.on('data', (chunk) => {
      let messages;
      try {
        messages = reader.push(/** @type {Buffer} */ (chunk));
      } catch (error) {
        this.log(`bad message: ${error instanceof Error ? error.message : error}`);
        socket.destroy();
        return;
      }
      for (const { type, payload } of messages) {
        /** @type {any} */
        let cmd;
        try {
          cmd = type === TYPE.JSON ? JSON.parse(payload.toString('utf8')) : null;
        } catch {
          cmd = null;
        }
        if (!authed) {
          if (!cmd || cmd.type !== 'hello' || cmd.token !== this.token || this.client) {
            socket.destroy();
            return;
          }
          authed = true;
          clearTimeout(helloTimer);
          this.client = socket;
          this.log('client connected');
          if (Array.isArray(cmd.formats) && !cmd.formats.includes('jpeg')) {
            this.fail('This client cannot show JPEG frames');
            return;
          }
          this.#sendStatus();
          continue;
        }
        if (cmd) this.handle(cmd);
      }
    });
    socket.on('error', () => {});
    socket.on('close', () => {
      clearTimeout(helloTimer);
      if (socket === this.client) {
        this.client = null;
        void this.shutdown('Blender disconnected');
      }
    });
  }

  /**
   * Runs one command from Blender.
   * @param {any} cmd
   */
  handle(cmd) {
    switch (cmd?.type) {
      case 'size': {
        const width = clampInt(cmd.width, 16, MAX_SIZE, this.size.width);
        const height = clampInt(cmd.height, 16, MAX_SIZE, this.size.height);
        const scale = Number(cmd.scale) > 0 ? Math.min(4, Number(cmd.scale)) : this.size.scale;
        if (width === this.size.width && height === this.size.height && scale === this.size.scale) return;
        this.size = { width, height, scale };
        if (this.resizeTimer) clearTimeout(this.resizeTimer);
        this.resizeTimer = setTimeout(() => {
          this.resizeTimer = null;
          void this.#applySize().then(() => (this.casting ? this.#restartScreencast() : undefined));
        }, RESIZE_DELAY_MS);
        return;
      }
      case 'quality': {
        const quality = cmd.quality === undefined ? this.quality : clampInt(cmd.quality, 1, 100, this.quality);
        if (cmd.max_fps !== undefined) {
          this.maxFps = clampInt(cmd.max_fps, 0, 240, this.maxFps);
          this.limiter.set(this.maxFps);
        }
        if (quality !== this.quality) {
          this.quality = quality;
          if (this.casting) void this.#restartScreencast();
        }
        return;
      }
      case 'mouse': {
        const params = mouseEventParams(cmd, this.mouse, this.#cssPerFramePixel('width'), this.#cssPerFramePixel('height'));
        if (!params) return;
        this.mouse.x = /** @type {number} */ (params.x);
        this.mouse.y = /** @type {number} */ (params.y);
        this.#send('Input.dispatchMouseEvent', params);
        return;
      }
      case 'key': {
        const params = keyEventParams(cmd);
        if (!params) return;
        if (cmd.kind === 'down') this.held.set(cmd.code, cmd);
        else this.held.delete(cmd.code);
        this.#send('Input.dispatchKeyEvent', params);
        return;
      }
      case 'text':
        if (typeof cmd.text === 'string' && cmd.text) this.#send('Input.insertText', { text: cmd.text });
        return;
      case 'release':
        this.#releaseInput();
        return;
      case 'pause':
        // suspend: also hide the page (Blender: nobody sees the game and it is not played).
        this.suspendWanted = !!cmd.suspend;
        if (!this.paused) {
          this.paused = true;
          this.#releaseInput();
          if (this.casting) void this.#stopScreencast();
        }
        void this.#syncHidden();
        return;
      case 'resume':
        this.suspendWanted = false;
        if (!this.paused) return;
        this.paused = false;
        // Shown before the frames start again.
        void this.#syncHidden().then(() => (this.page && !this.paused && !this.casting ? this.#startScreencast() : undefined));
        return;
      case 'navigate': {
        // The game's address once the dev server has one, or a new one (the server moved).
        const url = typeof cmd.url === 'string' ? cmd.url : '';
        if (!/^https?:\/\//i.test(url)) {
          this.log(`navigate: not an http(s) address: ${url.slice(0, 200)}`);
          return;
        }
        if (url === this.url) return;
        this.url = url;
        // Before the page is set up, #launch loads it.
        if (this.pageReady) void this.#load().catch((error) => this.fail(error instanceof Error ? error.message : String(error)));
        return;
      }
      case 'stop':
        void this.shutdown('Stopped by Blender');
        return;
      default:
        this.log(`unknown command: ${JSON.stringify(cmd).slice(0, 200)}`);
    }
  }

  /** CSS pixels per frame pixel (frames can be smaller than the page: device scale, maxWidth). @param {'width' | 'height'} axis */
  #cssPerFramePixel(axis) {
    const css = this.size[axis] / this.size.scale;
    return this.frameSize[axis] > 0 ? css / this.frameSize[axis] : 1 / this.size.scale;
  }

  /** Lifts every held key and mouse button (pause, focus lost). */
  #releaseInput() {
    for (const cmd of this.held.values()) {
      const params = keyEventParams({ ...cmd, kind: 'up' });
      if (params) this.#send('Input.dispatchKeyEvent', params);
    }
    this.held.clear();
    for (const button of [0, 1, 2]) {
      if (this.mouse.buttons & [1, 4, 2][button]) {
        const params = mouseEventParams({ kind: 'up', x: this.mouse.x, y: this.mouse.y, button }, this.mouse, 1, 1);
        if (params) this.#send('Input.dispatchMouseEvent', params);
      }
    }
  }

  /** @param {string} method @param {object} params */
  #send(method, params) {
    if (!this.page) return;
    this.page.cdp.send(method, params).catch((/** @type {any} */ error) => this.log(`${method} failed: ${error?.message ?? error}`));
  }

  async #applySize() {
    if (!this.page) return;
    const { width, height, scale } = this.size;
    await this.page.cdp.send('Emulation.setDeviceMetricsOverride', {
      width: Math.max(1, Math.round(width / scale)), height: Math.max(1, Math.round(height / scale)), deviceScaleFactor: scale, mobile: false,
    }).catch((/** @type {any} */ error) => this.log(`resize failed: ${error?.message ?? error}`));
  }

  async #startScreencast() {
    if (!this.page || this.stopping) return;
    this.casting = true;
    this.limiter.set(this.maxFps);
    await this.page.cdp.send('Page.startScreencast', {
      format: 'jpeg', quality: this.quality, maxWidth: this.size.width, maxHeight: this.size.height, everyNthFrame: 1,
    }).catch((/** @type {any} */ error) => this.log(`startScreencast failed: ${error?.message ?? error}`));
  }

  async #stopScreencast() {
    this.casting = false;
    await this.page?.cdp.send('Page.stopScreencast').catch(() => {});
  }

  async #restartScreencast() {
    await this.#stopScreencast();
    if (!this.paused) await this.#startScreencast();
  }

  /** @param {{ data: string, metadata?: { timestamp?: number }, sessionId: number }} e */
  #onFrame(e) {
    // Acknowledge first: the browser sends the next frame only after the ack.
    this.page?.cdp.send('Page.screencastFrameAck', { sessionId: e.sessionId }).catch(() => {});
    const client = this.client;
    const now = Date.now();
    if (!client || this.paused || !this.casting || !this.limiter.allow(now)) return;
    if (client.writableLength > MAX_BACKLOG) {
      this.dropped++;
      return;
    }
    const jpeg = Buffer.from(e.data, 'base64');
    const [width, height] = jpegSize(jpeg) ?? [this.size.width, this.size.height];
    this.frameSize = { width, height };
    const timestamp = e.metadata?.timestamp ? e.metadata.timestamp * 1000 : now;
    this.frame = (this.frame + 1) >>> 0;
    client.cork();
    client.write(frameHeader({ width, height, frame: this.frame, format: FORMAT.JPEG, timestamp }, jpeg.length));
    client.write(jpeg);
    process.nextTick(() => client.uncork());
    this.sent.push(now);
  }

  #fps() {
    const now = Date.now();
    while (this.sent.length && now - this.sent[0] > 1000) this.sent.shift();
    return this.sent.length;
  }

  /** @param {Record<string, unknown>} status */
  #setStatus(status) {
    this.status = status;
    this.log(`${status.status}: ${status.message}`);
    this.#sendStatus();
  }

  #sendStatus() {
    if (!this.client || this.client.destroyed) return;
    this.client.write(encodeJson(TYPE.STATUS, {
      ...this.status,
      fps: this.#fps(),
      mode: this.mode,
      audio: !this.mute,
      renderer: this.renderer,
      gpu: this.renderer ? !/swiftshader|llvmpipe|softpipe|software|basic render/i.test(this.renderer) : null,
      browser: this.page?.name ?? '',
      width: this.frameSize.width,
      height: this.frameSize.height,
      frame: this.frame,
      dropped: this.dropped,
      paused: this.paused,
      suspended: this.hidden,
      url: this.url,
      ...(this.agent ? { agent: this.agent } : {}),
      ...(this.exposeDevtools && this.page?.devtools ? { devtools: this.page.devtools } : {}),
    }));
  }

  /** Reports an error to Blender, then shuts down. @param {string} message */
  fail(message) {
    if (this.stopping) return;
    this.#setStatus({ status: 'error', message });
    void this.shutdown(message);
  }

  /**
   * Closes the browser and the connection. Safe to call more than once.
   * @param {string} reason
   */
  async shutdown(reason) {
    if (this.stopping) return this.closed;
    this.stopping = true;
    this.log(`stopping: ${reason}`);
    for (const timer of this.timers) clearInterval(timer);
    if (this.resizeTimer) clearTimeout(this.resizeTimer);
    const page = this.page;
    this.page = null;
    this.casting = false;
    if (page) await page.close().catch(() => {});
    if (this.profile) await rm(this.profile, { recursive: true, force: true, maxRetries: 10, retryDelay: 200 }).catch(() => {});
    const client = this.client;
    if (client && !client.destroyed) {
      if (this.status.status !== 'error') this.status = { status: 'stopped', message: reason };
      this.#sendStatus();
      await Promise.race([new Promise((resolve) => client.end(() => resolve(undefined))), new Promise((resolve) => setTimeout(resolve, 1000))]);
      client.destroy();
    }
    this.client = null;
    await new Promise((resolve) => (this.server ? this.server.close(() => resolve(undefined)) : resolve(undefined)));
    this.resolveClosed(reason);
    return this.closed;
  }
}
