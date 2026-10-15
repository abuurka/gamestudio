// @ts-check
// SPDX-License-Identifier: MIT
// GameStudio runtime host (docs/fork/SURFACE.md). Runs one game page in Electron:
//   --mode docked  hidden offscreen window; frames go to the editor as GPU-shared textures,
//                  input comes back from the editor (Windows only in protocol v1);
//   --mode window  an ordinary window (detached view, or a shipped desktop game).
// With --surface the host connects back to the editor and exits when that link closes.

import { app, BrowserWindow, ipcMain, Menu } from 'electron';
import { chromiumLuidSwitch, parseArgs, UsageError } from './args.js';
import { FramePump } from './frame-pump.js';
import { describeActiveGpu } from './gpu.js';
import { sanitizeInput } from './input-messages.js';
import { SurfaceLink } from './surface-link.js';
import { configureDockedWindow, createDockedWindow, createGameWindow } from './views.js';

const PROTOCOL = 1;
const VERSION = '0.1.0';
const WELCOME_TIMEOUT_MS = 5000;
const EXIT_USAGE = 2;
const EXIT_LINK = 3;

const USAGE = `usage: electron <host dir> -- --url <game url> [--mode window|docked]
         [--surface 127.0.0.1:<port> --token <hex>] [--width 1280 --height 720 --dpr 1]
         [--fps 60] [--max-in-flight 3] [--adapter-luid <16 hex digits>]`;

/** @param {string} message */
const log = (message) => console.log(`[gamestudio-host] ${message}`);

/** @type {import('./args.js').HostOptions} */
let options;
try {
  options = parseArgs(process.argv.slice(1));
  if (options.mode === 'docked' && process.platform !== 'win32') {
    throw new UsageError('--mode docked is Windows-only in surface protocol v1 (see docs/fork/SURFACE.md §6)');
  }
} catch (error) {
  console.error(`[gamestudio-host] ${error instanceof Error ? error.message : String(error)}\n${USAGE}`);
  process.exit(EXIT_USAGE);
}

if (options.adapterLuid) app.commandLine.appendSwitch('use-adapter-luid', chromiumLuidSwitch(options.adapterLuid));
if (options.mode === 'docked') {
  // The offscreen window is never visible: keep timers and rAF at full rate anyway.
  app.commandLine.appendSwitch('disable-renderer-backgrounding');
  app.commandLine.appendSwitch('disable-background-timer-throttling');
  app.commandLine.appendSwitch('disable-backgrounding-occluded-windows');
}
/** @type {HostSession | null} */
let current = null;
app.on('window-all-closed', () => {
  // A deliberate end (the user closed the game window, or the page closed itself): say so, or
  // the editor would take the closed link for a crash.
  if (current?.started) current.link?.goodbye('the game window was closed');
  app.quit();
});

app
  .whenReady()
  .then(main)
  .catch((error) => {
    console.error(`[gamestudio-host] ${error instanceof Error ? error.message : String(error)}`);
    app.exit(EXIT_LINK);
  });

async function main() {
  Menu.setApplicationMenu(null);
  // Lets tools tell this host's game apart from browser tabs on the live link, and the editor
  // find the game it docked (the pid is hello.pid).
  app.userAgentFallback = `${app.userAgentFallback} GameStudioHost/${VERSION} (pid ${process.pid})`;

  const link = options.surface ? await SurfaceLink.connect(options.surface.host, options.surface.port) : null;
  const session = new HostSession(link);
  current = session;
  if (!link) return;

  link.on('close', () => {
    const { sent, released, revoked } = session.pump.stats;
    log(`surface link closed; exiting (frames sent ${sent}, released ${released}, revoked ${revoked})`);
    session.shutdown();
    app.exit(0);
  });
  const gpu = await describeActiveGpu(app);
  link.send({
    t: 'hello',
    role: 'host',
    protocol: PROTOCOL,
    token: options.token,
    pid: process.pid,
    client: `gamestudio-host ${VERSION} (electron ${process.versions.electron})`,
    mode: options.mode,
    handles: process.platform === 'win32' ? ['d3d11-nt'] : [],
    formats: ['bgra8'],
    maxInFlight: options.maxInFlight,
    ...(options.adapterLuid && { adapterLuid: options.adapterLuid }),
    ...(gpu && { gpu }),
  });
  const reply = await link.next(['welcome', 'error'], WELCOME_TIMEOUT_MS);
  if (reply.t === 'error') throw new Error(`the editor refused the link (${String(reply.code)}): ${String(reply.msg)}`);
  if (reply.protocol !== PROTOCOL) throw new Error(`the editor speaks surface protocol ${String(reply.protocol)}, this host ${PROTOCOL}`);
  log(`connected to the editor at ${options.surface?.host}:${options.surface?.port} (${options.mode})`);
  link.on('message', (message) => session.handle(message));
  session.start();
}

/** One game page plus its link to the editor. */
class HostSession {
  /** @param {SurfaceLink | null} link */
  constructor(link) {
    this.link = link;
    this.mode = options.mode;
    this.paused = false;
    /** Nothing but the hello may reach the editor before the welcome (docs/fork/SURFACE.md §3.2). */
    this.started = false;
    /** @type {{ state: string, detail?: Record<string, unknown> }} */
    this.page = { state: 'loading' };
    this.pump = new FramePump({ maxInFlight: options.maxInFlight, send: (m) => link?.send(m) });
    /** @type {BrowserWindow | null} */
    this.docked = null;
    /** @type {BrowserWindow | null} */
    this.window = null;

    /** @type {import('./views.js').PageStateListener} */
    const onState = (state, detail) => {
      this.page = { state, detail };
      if (state === 'crashed' || state === 'error') console.error(`[gamestudio-host] page ${state}: ${JSON.stringify(detail)}`);
      this.sendState();
    };
    this.onState = onState;

    if (this.mode === 'docked') {
      this.docked = createDockedWindow(options, { onTexture: (texture) => this.pump.onPaint(texture), onState });
      this.watchDevTools(this.docked);
      ipcMain.on('gs:cursor-grab', (event, grab) => {
        if (this.started && this.docked && event.sender === this.docked.webContents) {
          this.link?.send({ t: 'cursor.grab', grab: grab === true });
        }
      });
      // A page that (re)loads while paused starts paused (preload.cjs asks).
      ipcMain.on('gs:paused', (event) => {
        event.returnValue = this.paused;
      });
    } else {
      this.window = createGameWindow(options, { onState });
      this.watchDevTools(this.window);
    }
  }

  /** The page the user sees: the docked page, or the window after detach. */
  get contents() {
    const window = this.docked ?? this.window;
    return window && !window.isDestroyed() ? window.webContents : null;
  }

  /** @param {BrowserWindow} window */
  watchDevTools(window) {
    const contents = window.webContents;
    contents.on('devtools-opened', () => this.sendState());
    contents.on('devtools-closed', () => this.sendState());
  }

  /** Chromium DevTools for the game page, in a window of its own (also for the offscreen page). */
  openDevTools() {
    const contents = this.contents;
    if (!contents) return;
    if (contents.isDevToolsOpened()) {
      contents.devToolsWebContents?.focus();
      return;
    }
    contents.openDevTools({ mode: 'detach', activate: true, title: 'GameStudio DevTools' });
  }

  /**
   * The link is gone. Stop producing first, so that no texture painted from now on is
   * held for an editor that will never release it, then give everything back.
   */
  shutdown() {
    this.started = false;
    this.pump.enabled = false;
    this.pump.releaseAll();
    for (const window of [this.docked, this.window]) {
      if (window && !window.isDestroyed()) window.destroy();
    }
    this.docked = null;
    this.window = null;
  }

  /** The handshake is complete: start streaming. */
  start() {
    this.started = true;
    this.pump.enabled = this.mode === 'docked' && !this.paused;
    this.sendState();
  }

  sendState() {
    if (!this.started) return;
    const devtools = this.contents?.isDevToolsOpened() ?? false;
    this.link?.send({ t: 'surface.state', state: this.page.state, mode: this.mode, paused: this.paused, devtools, ...this.page.detail });
  }

  /** @param {Record<string, unknown>} message an editor → host message */
  handle(message) {
    switch (message.t) {
      case 'surface.release':
        this.pump.release(message.frameId);
        break;
      case 'surface.configure':
        this.configure(message);
        break;
      case 'input.key':
      case 'input.pointer':
      case 'focus': {
        const clean = sanitizeInput(message);
        if (clean && this.docked) this.docked.webContents.send('gs:input', clean);
        break;
      }
      case 'pause':
        this.setPaused(message.paused !== false);
        break;
      case 'detach':
        this.detach();
        break;
      case 'devtools':
        if (message.open !== false) this.openDevTools();
        else this.contents?.closeDevTools();
        break;
      case 'ping':
        this.link?.send({ t: 'pong', re: message.id ?? null, ts: Math.round(performance.now() * 1000) / 1000 });
        break;
      case 'error':
        console.error(`[gamestudio-host] editor error (${String(message.code)}): ${String(message.msg)}`);
        break;
      default:
        break; // Unknown types are ignored (docs/fork/SURFACE.md §8).
    }
  }

  /** @param {Record<string, unknown>} message */
  configure(message) {
    const { width, height } = message;
    const dpr = typeof message.dpr === 'number' ? message.dpr : 1;
    const fps = typeof message.fps === 'number' ? message.fps : undefined;
    const valid =
      Number.isInteger(width) && Number.isInteger(height) &&
      /** @type {number} */ (width) >= 16 && /** @type {number} */ (height) >= 16 &&
      /** @type {number} */ (width) <= 16384 && /** @type {number} */ (height) <= 16384 &&
      dpr >= 0.25 && dpr <= 8 && (fps === undefined || (Number.isInteger(fps) && fps >= 1 && fps <= 240));
    if (!valid) {
      this.link?.send({ t: 'error', code: 'configure', msg: `invalid surface.configure: ${JSON.stringify(message)}` });
      return;
    }
    if (this.docked) {
      configureDockedWindow(this.docked, { width: /** @type {number} */ (width), height: /** @type {number} */ (height), dpr, fps });
    }
  }

  /**
   * Paused: no frames, and the page is told it is hidden and gets no animation frames, so a
   * game driven by requestAnimationFrame stops simulating too (preload.cjs). Timers and the
   * live link keep running, so the editor can still query and edit the paused game.
   * @param {boolean} paused
   */
  setPaused(paused) {
    if (paused === this.paused) return;
    this.paused = paused;
    if (this.docked) {
      if (paused) this.docked.webContents.stopPainting();
      else this.docked.webContents.startPainting();
      this.pump.enabled = !paused;
      this.docked.webContents.send('gs:pause', paused);
    }
    this.sendState();
  }

  /** Docked → window: the game moves to a normal window (it reloads; the live link restores the scene). */
  detach() {
    if (!this.docked) return;
    const docked = this.docked;
    const zoom = docked.webContents.getZoomFactor() || 1;
    const [width = options.width, height = options.height] = docked.getContentSize();
    const url = docked.webContents.getURL() || options.url;
    this.pump.enabled = false;
    this.pump.revokeAll();
    this.docked = null;
    this.mode = 'window';
    // Open the new window before destroying the old one, or window-all-closed would quit.
    this.window = createGameWindow(
      { url, width: Math.round(width / zoom), height: Math.round(height / zoom) },
      { onState: this.onState },
    );
    this.watchDevTools(this.window);
    this.paused = false; // A window is never paused by the editor.
    docked.destroy();
    this.sendState();
    log('detached into a window');
  }
}
