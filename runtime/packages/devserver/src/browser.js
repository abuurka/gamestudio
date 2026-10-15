// @ts-check
// SPDX-License-Identifier: MIT
// Which browser GameStudio plays games in: the stream host (Play in Blender), the runtime scripts
// (runtime/scripts/programs.mjs) and, through its own copy, the MCP server's headless games
// (@gamestudio/mcp src/discovery.js findBrowser; a test there checks that both agree).
//
// Order: GAMESTUDIO_BROWSER or GS_BROWSER (an executable, used when it exists); then Edge, Chrome,
// Chromium, Brave or Vivaldi installed on the system (skipped with GAMESTUDIO_BROWSER_SYSTEM=0); then the Chrome
// Headless Shell GameStudio downloaded (bin/gamestudio-browser.js, src/browser-install.js) into
// GAMESTUDIO_BROWSER_DIR or into the extension's user folder, <user folder>/browser, of any Blender
// profile. A download counts once its marker, <dir>/browser.json, names an executable that exists:
// the installer writes it only after the browser ran.
//
// No dependencies: it runs before `npm install` has finished, and it is only file stats.

import { existsSync, readdirSync, readFileSync } from 'node:fs';
import os from 'node:os';
import path from 'node:path';

/** The installer's marker in a browser folder (src/browser-install.js). */
export const BROWSER_MARKER = 'browser.json';
/** The extension user folder's subfolder for the downloaded browser. */
export const BROWSER_SUBDIR = 'browser';
const EXTENSION_ID = 'gamestudio';

/**
 * @typedef {'env' | 'system' | 'downloaded'} BrowserSource
 * @typedef {{ path: string, source: BrowserSource }} FoundBrowser
 * @typedef {{ platform?: NodeJS.Platform, exists?: (file: string) => boolean }} FindOptions
 * @typedef {{ browser?: string, buildId?: string, platform?: string, executable?: string, version?: string, installed?: string }} BrowserMarker
 */

/** Chrome Headless Shell (the downloaded browser): no window, so no 'offscreen' mode. @param {string | null | undefined} file */
export const isHeadlessShell = (file) => /^chrome-headless-shell(\.exe)?$/i.test(path.basename(file ?? ''));

/** A short name for status lines ("Playing in Edge 155"). @param {string} file */
export function browserLabel(file) {
  if (isHeadlessShell(file)) return 'Chrome Headless Shell';
  if (/edge/i.test(file)) return 'Edge';
  if (/brave/i.test(file)) return 'Brave';
  if (/vivaldi/i.test(file)) return 'Vivaldi';
  return /chromium/i.test(file) ? 'Chromium' : 'Chrome';
}

/** @param {string | undefined} value */
const off = (value) => ['0', 'false', 'off', 'no'].includes(String(value ?? '').trim().toLowerCase());

// Chromium browsers that run headless under puppeteer, stable channels first. Not Arc, Opera or
// Firefox. Keep in step with @gamestudio/mcp src/discovery.js and the extension's core/devserver.py.
/** Windows: under %ProgramFiles(x86)%, %ProgramFiles% and %LOCALAPPDATA% (per-user installs), in that order. */
export const WINDOWS_BROWSERS = Object.freeze([
  'Microsoft/Edge/Application/msedge.exe',
  'Google/Chrome/Application/chrome.exe',
  'Chromium/Application/chrome.exe',
  'BraveSoftware/Brave-Browser/Application/brave.exe',
  'Vivaldi/Application/vivaldi.exe',
  'Microsoft/Edge Beta/Application/msedge.exe',
  'Microsoft/Edge Dev/Application/msedge.exe',
  'Microsoft/Edge SxS/Application/msedge.exe',
  'Google/Chrome Beta/Application/chrome.exe',
  'Google/Chrome Dev/Application/chrome.exe',
  'Google/Chrome SxS/Application/chrome.exe',
]);
/** macOS: <name>.app/Contents/MacOS/<name> in /Applications, then ~/Applications. */
export const MAC_BROWSERS = Object.freeze([
  'Google Chrome', 'Microsoft Edge', 'Chromium', 'Brave Browser', 'Vivaldi',
  'Google Chrome Beta', 'Google Chrome Dev', 'Google Chrome Canary',
  'Microsoft Edge Beta', 'Microsoft Edge Dev', 'Microsoft Edge Canary',
]);
/** Commands on PATH (every platform; .exe on Windows), on Linux then in /usr/bin and /usr/local/bin. */
export const BROWSER_COMMANDS = Object.freeze([
  'microsoft-edge', 'microsoft-edge-stable', 'google-chrome', 'google-chrome-stable', 'chromium', 'chromium-browser',
  'brave-browser', 'brave-browser-stable', 'vivaldi', 'vivaldi-stable',
  'google-chrome-beta', 'google-chrome-unstable', 'microsoft-edge-beta', 'microsoft-edge-dev',
]);
/** Linux, last: the install folders (when the command is missing), then snap (its confinement is the least sure to work). */
export const LINUX_BROWSERS = Object.freeze([
  '/opt/microsoft/msedge/msedge', '/opt/google/chrome/chrome', '/opt/brave.com/brave/brave', '/opt/vivaldi/vivaldi',
  '/snap/bin/chromium', '/snap/bin/brave', '/var/lib/snapd/snap/bin/chromium',
]);

/**
 * Edge, Chrome, Chromium, Brave and Vivaldi where they are usually installed, then on PATH, in
 * that order (see the lists above).
 * @param {NodeJS.ProcessEnv} [env]
 * @param {NodeJS.Platform} [platform]
 * @returns {string[]}
 */
export function systemBrowserCandidates(env = process.env, platform = process.platform) {
  /** @type {string[]} */
  const candidates = [];
  const home = env.HOME || env.USERPROFILE || os.homedir();
  if (platform === 'win32') {
    const bases = [env['ProgramFiles(x86)'], env.ProgramFiles, env.LOCALAPPDATA].filter((b) => !!b);
    for (const rel of WINDOWS_BROWSERS) {
      for (const base of bases) candidates.push(path.join(/** @type {string} */ (base), ...rel.split('/')));
    }
  } else if (platform === 'darwin') {
    for (const name of MAC_BROWSERS) {
      for (const apps of ['/Applications', path.join(home, 'Applications')]) candidates.push(path.join(apps, `${name}.app`, 'Contents', 'MacOS', name));
    }
  }
  const pathDirs = (env.PATH ?? env.Path ?? '').split(platform === 'win32' ? ';' : ':').filter(Boolean);
  // Blender started from a desktop launcher may have a short PATH.
  if (platform === 'linux') pathDirs.push('/usr/bin', '/usr/local/bin');
  for (const name of BROWSER_COMMANDS) {
    for (const dir of pathDirs) candidates.push(path.join(dir, platform === 'win32' ? `${name}.exe` : name));
  }
  if (platform === 'linux') candidates.push(...LINUX_BROWSERS);
  return [...new Set(candidates)];
}

/** @param {string} a @param {string} b */
function compareVersions(a, b) {
  const [a1, a2] = a.split('.').map(Number);
  const [b1, b2] = b.split('.').map(Number);
  return (a1 ?? 0) - (b1 ?? 0) || (a2 ?? 0) - (b2 ?? 0);
}

/** @param {string} dir */
function listDirs(dir) {
  try {
    return readdirSync(dir, { withFileTypes: true }).filter((e) => e.isDirectory()).map((e) => e.name);
  } catch {
    return [];
  }
}

/**
 * The GameStudio extension's user folders (bpy.utils.extension_path_user) of every Blender profile:
 * BLENDER_USER_RESOURCES, then the per-version config folders, newest version first.
 * Same search as @gamestudio/mcp's userBridgeDirs.
 * @param {NodeJS.ProcessEnv} [env]
 * @param {NodeJS.Platform} [platform]
 * @returns {string[]}
 */
export function extensionUserDirs(env = process.env, platform = process.platform) {
  /** @type {string[]} */
  const resourceRoots = [];
  if (env.BLENDER_USER_RESOURCES) resourceRoots.push(env.BLENDER_USER_RESOURCES);
  const home = env.HOME || env.USERPROFILE || os.homedir();
  /** @type {string} */
  let versionsDir;
  if (platform === 'win32') {
    versionsDir = path.join(env.APPDATA || path.join(home, 'AppData', 'Roaming'), 'Blender Foundation', 'Blender');
  } else if (platform === 'darwin') {
    versionsDir = path.join(home, 'Library', 'Application Support', 'Blender');
  } else {
    versionsDir = path.join(env.XDG_CONFIG_HOME || path.join(home, '.config'), 'blender');
  }
  for (const version of listDirs(versionsDir).filter((v) => /^\d+\.\d+$/.test(v)).sort(compareVersions).reverse()) {
    resourceRoots.push(path.join(versionsDir, version));
  }
  /** @type {string[]} */
  const dirs = [];
  for (const resources of resourceRoots) {
    const userExtensions = path.join(resources, 'extensions', '.user');
    for (const repo of listDirs(userExtensions)) {
      const dir = path.join(userExtensions, repo, EXTENSION_ID);
      if (existsSync(dir)) dirs.push(dir);
    }
  }
  return [...new Set(dirs)];
}

/**
 * Folders that may hold a downloaded browser: GAMESTUDIO_BROWSER_DIR, then <user folder>/browser.
 * @param {NodeJS.ProcessEnv} [env]
 * @param {NodeJS.Platform} [platform]
 */
export function browserDirs(env = process.env, platform = process.platform) {
  const dirs = env.GAMESTUDIO_BROWSER_DIR ? [path.resolve(env.GAMESTUDIO_BROWSER_DIR)] : [];
  for (const dir of extensionUserDirs(env, platform)) dirs.push(path.join(dir, BROWSER_SUBDIR));
  return [...new Set(dirs)];
}

/**
 * The installer's marker in `dir`, when it names an executable that exists.
 * @param {string} dir
 * @param {(file: string) => boolean} [exists]
 * @returns {(BrowserMarker & { path: string }) | null}
 */
export function readBrowserMarker(dir, exists = existsSync) {
  /** @type {BrowserMarker} */
  let marker;
  try {
    marker = JSON.parse(readFileSync(path.join(dir, BROWSER_MARKER), 'utf8'));
  } catch {
    return null;
  }
  if (!marker || typeof marker.executable !== 'string' || !marker.executable) return null;
  const file = path.resolve(dir, marker.executable);
  return exists(file) ? { ...marker, path: file } : null;
}

/**
 * Every browser there is, in the order to try them: the environment's, the system's, the
 * downloaded ones. Launchers try the next when one does not start (a broken install, a snap that
 * its confinement stops).
 * @param {NodeJS.ProcessEnv} [env]
 * @param {FindOptions} [options]
 * @returns {FoundBrowser[]}
 */
export function browserCandidates(env = process.env, options = {}) {
  /** @type {FoundBrowser[]} */
  const found = [];
  for (const browser of eachBrowser(env, options)) if (!found.some((f) => f.path === browser.path)) found.push(browser);
  return found;
}

/**
 * @param {NodeJS.ProcessEnv} env
 * @param {FindOptions} options
 * @returns {Generator<FoundBrowser>}
 */
function* eachBrowser(env, { platform = process.platform, exists = existsSync }) {
  for (const file of [env.GAMESTUDIO_BROWSER, env.GS_BROWSER]) if (file && exists(file)) yield { path: file, source: 'env' };
  if (!off(env.GAMESTUDIO_BROWSER_SYSTEM)) {
    for (const file of systemBrowserCandidates(env, platform)) if (exists(file)) yield { path: file, source: 'system' };
  }
  for (const dir of browserDirs(env, platform)) {
    const marker = readBrowserMarker(dir, exists);
    if (marker) yield { path: marker.path, source: 'downloaded' };
  }
}

/**
 * The browser to play in, and where it came from (the first of browserCandidates).
 * @param {NodeJS.ProcessEnv} [env]
 * @param {FindOptions} [options]
 * @returns {FoundBrowser | null}
 */
export function locateBrowser(env = process.env, options = {}) {
  for (const browser of eachBrowser(env, options)) return browser;
  return null;
}

/**
 * The browser's executable, or null when there is none (see locateBrowser).
 * @param {NodeJS.ProcessEnv} [env]
 * @param {FindOptions} [options]
 */
export function findBrowser(env = process.env, options = {}) {
  return locateBrowser(env, options)?.path ?? null;
}
