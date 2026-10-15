// @ts-check
// SPDX-License-Identifier: MIT
// The two ways the host shows a game (docs/fork/SURFACE.md §1):
//   docked: a hidden offscreen BrowserWindow whose frames go to the editor as shared textures;
//   window: an ordinary visible window (the detached view, and the desktop shipping shell).

import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { BrowserWindow } from 'electron';

const PRELOAD = path.join(path.dirname(fileURLToPath(import.meta.url)), 'preload.cjs');

/**
 * @typedef {'loading' | 'running' | 'crashed' | 'error'} PageState
 * @typedef {(state: PageState, detail?: Record<string, unknown>) => void} PageStateListener
 */

/**
 * Creates the hidden offscreen window. `paint` textures go to `onTexture`.
 * @param {{ url: string, width: number, height: number, dpr: number, fps: number }} options
 * @param {{ onTexture: (texture: Electron.OffscreenSharedTexture) => void, onState: PageStateListener }} handlers
 */
export function createDockedWindow(options, handlers) {
  const window = new BrowserWindow({
    width: options.width,
    height: options.height,
    useContentSize: true,
    show: false,
    frame: false,
    skipTaskbar: true,
    webPreferences: {
      offscreen: { useSharedTexture: true },
      backgroundThrottling: false,
      // Page zoom sets window.devicePixelRatio, so the texture stays width x height
      // physical pixels while CSS pixels shrink by dpr (docs/fork/SURFACE.md §4.1).
      zoomFactor: options.dpr,
      preload: PRELOAD,
      contextIsolation: true,
      sandbox: true,
      nodeIntegration: false,
    },
  });
  const contents = window.webContents;
  contents.setFrameRate(options.fps);
  contents.on('paint', (event) => {
    const texture = /** @type {{ texture?: Electron.OffscreenSharedTexture }} */ (event).texture;
    if (texture) handlers.onTexture(texture);
  });
  watchPage(contents, options.url, handlers.onState);
  void contents.loadURL(options.url).catch(() => {}); // did-fail-load reports the error.
  return window;
}

/**
 * Applies `surface.configure` to a docked window.
 * @param {BrowserWindow} window
 * @param {{ width: number, height: number, dpr: number, fps?: number }} size
 */
export function configureDockedWindow(window, size) {
  window.setContentSize(size.width, size.height);
  window.webContents.setZoomFactor(size.dpr);
  if (size.fps) window.webContents.setFrameRate(size.fps);
  window.webContents.invalidate();
  const [width, height] = window.getContentSize();
  if (width !== size.width || height !== size.height) {
    console.warn(`[gamestudio-host] asked for ${size.width}x${size.height}, Chromium gave ${width}x${height} (clamped to the screen?)`);
  }
}

/**
 * Creates a normal, visible game window.
 * @param {{ url: string, width: number, height: number, title?: string }} options
 * @param {{ onState: PageStateListener }} handlers
 */
export function createGameWindow(options, handlers) {
  const window = new BrowserWindow({
    width: options.width,
    height: options.height,
    useContentSize: true,
    title: options.title ?? 'GameStudio',
    backgroundColor: '#0d0e16',
    autoHideMenuBar: true,
    webPreferences: { contextIsolation: true, sandbox: true, nodeIntegration: false, backgroundThrottling: false },
  });
  window.setMenu(null);
  watchPage(window.webContents, options.url, handlers.onState);
  void window.webContents.loadURL(options.url).catch(() => {});
  return window;
}

/**
 * Keeps the page on its own origin and reports load and crash state.
 * @param {Electron.WebContents} contents
 * @param {string} url
 * @param {PageStateListener} onState
 */
function watchPage(contents, url, onState) {
  const home = new URL(url);
  /** @param {string} target */
  const allowed = (target) => {
    try {
      const next = new URL(target);
      return home.protocol === 'file:' ? next.protocol === 'file:' : next.origin === home.origin;
    } catch {
      return false;
    }
  };
  /** @param {Electron.Event & { url?: string }} event @param {string} [target] */
  const guard = (event, target) => {
    const next = event.url ?? target ?? '';
    if (allowed(next)) return;
    event.preventDefault();
    console.warn(`[gamestudio-host] blocked navigation to ${next} (only ${home.origin} is allowed)`);
  };
  contents.on('will-navigate', guard);
  contents.on('will-redirect', guard);
  contents.setWindowOpenHandler(({ url: target }) => {
    console.warn(`[gamestudio-host] blocked window.open(${target})`);
    return { action: 'deny' };
  });

  contents.on('did-start-loading', () => onState('loading'));
  contents.on('did-finish-load', () => onState('running'));
  contents.on('did-fail-load', (_event, code, description, failedUrl, isMainFrame) => {
    if (isMainFrame && code !== -3) onState('error', { msg: `${description} (${code}) loading ${failedUrl}` }); // -3: aborted by a newer load
  });
  contents.on('render-process-gone', (_event, details) => {
    onState('crashed', { reason: details.reason, exitCode: details.exitCode });
  });
}
