// @ts-check
// SPDX-License-Identifier: MIT
// Finding things on disk: the project, the Blender bridge discovery files, the dev-server
// session, the Blender executable and the engine docs.
//
// Discovery files written by the Blender extension (blender/gamestudio/bridge/discovery.py):
//   <project>/.gamestudio/blender.json            a session with a scene of this project open
//   <Blender user resources>/extensions/.user/<repo>/gamestudio/bridge.json
//                                                 the latest session of that Blender profile
//   .../gamestudio/last-blender.json              the Blender executable (kept after exit)

import { existsSync, readdirSync, readFileSync, statSync } from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { pidAlive } from './util.js';

export const PROJECT_FILE = 'gamestudio.json';

/**
 * @typedef {object} BridgeSession
 * @property {number} pid
 * @property {string} [host]
 * @property {number} port
 * @property {string} token
 * @property {string | null} blend
 * @property {string | null} project
 * @property {string | null} [scene]
 * @property {string} [blenderVersion]
 * @property {string} [binary]
 * @property {boolean} [background]
 * @property {string} [startedAt]
 * @property {string} [updatedAt]
 * @property {string} file  where it was read from
 */

/** @param {string} file @returns {any} */
export function readJson(file) {
  try {
    return JSON.parse(readFileSync(file, 'utf8'));
  } catch {
    return null;
  }
}

/**
 * The nearest directory at or above `start` that contains gamestudio.json.
 * @param {string} start
 * @returns {string | null}
 */
export function findProjectRoot(start) {
  let dir = path.resolve(start);
  for (;;) {
    if (existsSync(path.join(dir, PROJECT_FILE))) return dir;
    const parent = path.dirname(dir);
    if (parent === dir) return null;
    dir = parent;
  }
}

/** @param {string} root */
export function readProjectConfig(root) {
  const config = readJson(path.join(root, PROJECT_FILE)) ?? {};
  return {
    name: typeof config.name === 'string' && config.name ? config.name : path.basename(root),
    startScene: typeof config.startScene === 'string' ? config.startScene : 'main',
  };
}

/**
 * The project's levels: scenes/*.blend (relative, forward slashes, sorted; SPEC §4.7). Other .blend
 * files (asset libraries, inspection files under assets/) are not levels.
 * @param {string} root
 */
export function projectScenes(root) {
  let entries;
  try {
    entries = readdirSync(path.join(root, 'scenes'), { withFileTypes: true });
  } catch {
    return [];
  }
  return entries
    .filter((entry) => entry.isFile() && entry.name.toLowerCase().endsWith('.blend'))
    .map((entry) => `scenes/${entry.name}`)
    .sort()
    .slice(0, 50);
}

/**
 * The scene to open when launching Blender for the project: scenes/<startScene>.blend, else
 * the first level.
 * @param {string} root
 */
export function defaultScene(root) {
  const { startScene } = readProjectConfig(root);
  const preferred = path.join(root, 'scenes', `${startScene}.blend`);
  if (existsSync(preferred)) return preferred;
  const first = projectScenes(root)[0];
  return first ? path.join(root, first) : null;
}

/** @param {any} data @returns {data is BridgeSession} */
function isSession(data) {
  return data && typeof data === 'object' && typeof data.port === 'number' && typeof data.token === 'string' && typeof data.pid === 'number';
}

/**
 * The project's discovery file, if present and well-formed (the process may be gone).
 * @param {string} root
 * @returns {BridgeSession | null}
 */
export function readProjectSession(root) {
  const file = path.join(root, '.gamestudio', 'blender.json');
  const data = readJson(file);
  return isSession(data) ? { ...data, file } : null;
}

/**
 * Folders that may hold the extension's user files, most specific first.
 * @param {NodeJS.ProcessEnv} [env]
 * @param {NodeJS.Platform} [platform]
 * @returns {string[]}
 */
export function userBridgeDirs(env = process.env, platform = process.platform) {
  return [...new Set([...(env.GAMESTUDIO_BRIDGE_DIR ? [env.GAMESTUDIO_BRIDGE_DIR] : []), ...extensionUserDirs(env, platform)])];
}

/**
 * The GameStudio extension's user folders (bpy.utils.extension_path_user) of every Blender profile:
 * BLENDER_USER_RESOURCES, then the per-version config folders, newest version first. Same search as
 * @gamestudio/devserver's extensionUserDirs (src/browser.js).
 * @param {NodeJS.ProcessEnv} [env]
 * @param {NodeJS.Platform} [platform]
 * @returns {string[]}
 */
export function extensionUserDirs(env = process.env, platform = process.platform) {
  /** @type {string[]} */
  const dirs = [];
  /** @type {string[]} */
  const resourceRoots = [];
  if (env.BLENDER_USER_RESOURCES) resourceRoots.push(env.BLENDER_USER_RESOURCES);
  const home = env.HOME || env.USERPROFILE || os.homedir();
  /** @type {string | null} */
  let versionsDir = null;
  if (platform === 'win32') {
    const appData = env.APPDATA || path.join(home, 'AppData', 'Roaming');
    versionsDir = path.join(appData, 'Blender Foundation', 'Blender');
  } else if (platform === 'darwin') {
    versionsDir = path.join(home, 'Library', 'Application Support', 'Blender');
  } else {
    versionsDir = path.join(env.XDG_CONFIG_HOME || path.join(home, '.config'), 'blender');
  }
  for (const version of listDirs(versionsDir).filter((v) => /^\d+\.\d+$/.test(v)).sort(compareVersions).reverse()) {
    resourceRoots.push(path.join(versionsDir, version));
  }
  for (const resources of resourceRoots) {
    const userExtensions = path.join(resources, 'extensions', '.user');
    for (const repo of listDirs(userExtensions)) {
      const dir = path.join(userExtensions, repo, 'gamestudio');
      if (existsSync(dir)) dirs.push(dir);
    }
  }
  return [...new Set(dirs)];
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
 * Live sessions from the user-level discovery files, newest first.
 * @param {NodeJS.ProcessEnv} [env]
 * @returns {BridgeSession[]}
 */
export function readUserSessions(env = process.env) {
  /** @type {BridgeSession[]} */
  const sessions = [];
  for (const dir of userBridgeDirs(env)) {
    const file = path.join(dir, 'bridge.json');
    const data = readJson(file);
    if (isSession(data)) sessions.push({ ...data, file });
  }
  const time = (/** @type {BridgeSession} */ s) => Date.parse(s.updatedAt ?? s.startedAt ?? '') || 0;
  return sessions.sort((a, b) => time(b) - time(a));
}

/**
 * Is this session alive? (The process exists; the port is checked by connecting.)
 * @param {BridgeSession} session
 */
export function sessionAlive(session) {
  return pidAlive(session.pid);
}

/** @param {string} a @param {string} b */
export function samePath(a, b) {
  const norm = (/** @type {string} */ p) => {
    const resolved = path.resolve(p);
    return process.platform === 'win32' ? resolved.toLowerCase() : resolved;
  };
  return norm(a) === norm(b);
}

/**
 * The Blender executable for launching: $GAMESTUDIO_BLENDER, the user-level discovery files,
 * $BLENDER, then the standard install folders (newest version first).
 * @param {NodeJS.ProcessEnv} [env]
 * @returns {{ path: string, source: string } | null}
 */
export function findBlenderExecutable(env = process.env) {
  if (env.GAMESTUDIO_BLENDER) return { path: env.GAMESTUDIO_BLENDER, source: 'GAMESTUDIO_BLENDER' };
  for (const dir of userBridgeDirs(env)) {
    for (const name of ['bridge.json', 'last-blender.json']) {
      const data = readJson(path.join(dir, name));
      if (data && typeof data.binary === 'string' && existsSync(data.binary)) {
        return { path: data.binary, source: path.join(dir, name) };
      }
    }
  }
  if (env.BLENDER && existsSync(env.BLENDER)) return { path: env.BLENDER, source: 'BLENDER' };
  /** @type {string[]} */
  const candidates = [];
  if (process.platform === 'win32') {
    for (const base of [env.ProgramFiles, 'C:\\Program Files', 'D:\\Program Files', 'E:\\Program Files', 'F:\\Program Files']) {
      if (!base) continue;
      const foundation = path.join(base, 'Blender Foundation');
      for (const dir of listDirs(foundation).filter((d) => /^Blender \d+\.\d+$/.test(d)).sort((a, b) => compareVersions(a.slice(8), b.slice(8))).reverse()) {
        candidates.push(path.join(foundation, dir, 'blender.exe'));
      }
    }
  } else if (process.platform === 'darwin') {
    candidates.push('/Applications/Blender.app/Contents/MacOS/Blender');
  } else {
    candidates.push('/usr/bin/blender', '/usr/local/bin/blender', '/snap/bin/blender');
  }
  const found = candidates.find((c) => existsSync(c));
  return found ? { path: found, source: 'install folder' } : null;
}

/**
 * The dev server's session (docs/SPEC.md §5.1) when its process is alive.
 * @param {string} root
 * @returns {{ pid: number, url: string, editorPort: number, gamePort: number, token: string } | null}
 */
export function readDevSession(root) {
  const data = readJson(path.join(root, '.gamestudio', 'session.json'));
  if (!data || typeof data.editorPort !== 'number' || typeof data.token !== 'string') return null;
  if (!pidAlive(data.pid)) return null;
  return data;
}

/**
 * The engine API reference: the project's installed @gamestudio/engine, else the checkout's.
 * @param {string | null} root
 * @param {string} here  a path inside this package
 */
export function engineDocsPath(root, here) {
  /** @type {string[]} */
  const candidates = [];
  if (root) candidates.push(path.join(root, 'node_modules', '@gamestudio', 'engine', 'API.md'));
  let dir = path.dirname(here);
  for (let i = 0; i < 6; i++) {
    candidates.push(path.join(dir, 'engine', 'API.md'), path.join(dir, 'packages', 'engine', 'API.md'));
    dir = path.dirname(dir);
  }
  return candidates.find((c) => existsSync(c) && statSync(c).isFile()) ?? null;
}

// --- The browser for headless play ------------------------------------------------------------
// A copy of @gamestudio/devserver's src/browser.js (this package does not depend on that one);
// test/browser-discovery.test.js checks that both find the same browser.

/** Chrome Headless Shell (the browser GameStudio downloads). @param {string | null | undefined} file */
export const isHeadlessShell = (file) => /^chrome-headless-shell(\.exe)?$/i.test(path.basename(file ?? ''));

/** @param {string | undefined} value */
const off = (value) => ['0', 'false', 'off', 'no'].includes(String(value ?? '').trim().toLowerCase());

// Chromium browsers that run headless under puppeteer, stable channels first (the same lists as the
// devserver's src/browser.js).
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
/** Linux, last: the install folders, then snap. */
export const LINUX_BROWSERS = Object.freeze([
  '/opt/microsoft/msedge/msedge', '/opt/google/chrome/chrome', '/opt/brave.com/brave/brave', '/opt/vivaldi/vivaldi',
  '/snap/bin/chromium', '/snap/bin/brave', '/var/lib/snapd/snap/bin/chromium',
]);

/**
 * Edge, Chrome, Chromium, Brave and Vivaldi where they are usually installed, then on PATH.
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
  if (platform === 'linux') pathDirs.push('/usr/bin', '/usr/local/bin');
  for (const name of BROWSER_COMMANDS) {
    for (const dir of pathDirs) candidates.push(path.join(dir, platform === 'win32' ? `${name}.exe` : name));
  }
  if (platform === 'linux') candidates.push(...LINUX_BROWSERS);
  return [...new Set(candidates)];
}

/**
 * Folders that may hold the browser GameStudio downloaded: GAMESTUDIO_BROWSER_DIR, then
 * <extension user folder>/browser of every Blender profile.
 * @param {NodeJS.ProcessEnv} [env]
 * @param {NodeJS.Platform} [platform]
 */
export function browserDirs(env = process.env, platform = process.platform) {
  const dirs = env.GAMESTUDIO_BROWSER_DIR ? [path.resolve(env.GAMESTUDIO_BROWSER_DIR)] : [];
  for (const dir of extensionUserDirs(env, platform)) dirs.push(path.join(dir, 'browser'));
  return [...new Set(dirs)];
}

/**
 * The downloaded browser in `dir`: its marker browser.json (written once the browser ran) names
 * the executable, relative to `dir`.
 * @param {string} dir
 * @param {(file: string) => boolean} [exists]
 * @returns {string | null}
 */
export function downloadedBrowser(dir, exists = existsSync) {
  const marker = readJson(path.join(dir, 'browser.json'));
  if (!marker || typeof marker.executable !== 'string' || !marker.executable) return null;
  const file = path.resolve(dir, marker.executable);
  return exists(file) ? file : null;
}

/**
 * @typedef {{ path: string, source: 'env' | 'system' | 'downloaded' }} FoundBrowser
 * @typedef {{ platform?: NodeJS.Platform, exists?: (file: string) => boolean }} FindOptions
 */

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
    const found = downloadedBrowser(dir, exists);
    if (found) yield { path: found, source: 'downloaded' };
  }
}

/**
 * Every Chromium-based browser for headless play, in the order to try them: $GAMESTUDIO_BROWSER or
 * $GS_BROWSER; those on the system (not with GAMESTUDIO_BROWSER_SYSTEM=0); the Chrome Headless Shell
 * GameStudio downloaded.
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
 * The first of browserCandidates.
 * @param {NodeJS.ProcessEnv} [env]
 * @param {FindOptions} [options]
 * @returns {FoundBrowser | null}
 */
export function locateBrowser(env = process.env, options = {}) {
  for (const browser of eachBrowser(env, options)) return browser;
  return null;
}

/**
 * The browser's executable, or null (see locateBrowser).
 * @param {NodeJS.ProcessEnv} [env]
 * @param {{ platform?: NodeJS.Platform, exists?: (file: string) => boolean }} [options]
 */
export function findBrowser(env = process.env, options = {}) {
  return locateBrowser(env, options)?.path ?? null;
}

/**
 * The project's checkpoints (.gamestudio/checkpoints/<id>/checkpoint.json), newest first.
 * @param {string} root
 * @returns {{ id: string, label?: string, created?: string, source?: string, file?: string }[]}
 */
export function listCheckpoints(root) {
  const dir = path.join(root, '.gamestudio', 'checkpoints');
  /** @type {any[]} */
  const found = [];
  for (const id of listDirs(dir)) {
    const data = readJson(path.join(dir, id, 'checkpoint.json'));
    if (data) found.push({ ...data, id });
  }
  return found.sort((a, b) => (a.id < b.id ? 1 : -1));
}

/** Folders Blender may write autosaves and crash logs to. @param {NodeJS.ProcessEnv} env */
function tempDirs(env) {
  /** @type {string[]} */
  const dirs = [];
  for (const d of [env.TEMP, env.TMP, env.TMPDIR, os.tmpdir()]) if (typeof d === 'string' && d && !dirs.includes(d)) dirs.push(d);
  return dirs;
}

/**
 * Blender autosaves of a session newer than the saved file: "<name>_<pid>_autosave.blend" or
 * "<pid>_autosave.blend" in the temp folder.
 * @param {string | null} blend
 * @param {number} pid
 * @param {NodeJS.ProcessEnv} [env]
 * @returns {{ path: string, mtime: Date }[]}
 */
export function findAutosaves(blend, pid, env = process.env) {
  const base = blend ? path.basename(blend).replace(/\.blend$/i, '') : null;
  const savedAt = blend && existsSync(blend) ? statSync(blend).mtimeMs : 0;
  /** @type {{ path: string, mtime: Date }[]} */
  const found = [];
  for (const dir of tempDirs(env)) {
    let names;
    try {
      names = readdirSync(dir);
    } catch {
      continue;
    }
    for (const name of names) {
      if (!name.endsWith('_autosave.blend')) continue;
      const ours = name === `${pid}_autosave.blend` || (base !== null && name.startsWith(`${base}_`));
      if (!ours) continue;
      const file = path.join(dir, name);
      const mtime = statSync(file).mtime;
      if (mtime.getTime() > savedAt) found.push({ path: file, mtime });
    }
  }
  return found.sort((a, b) => b.mtime.getTime() - a.mtime.getTime());
}

/**
 * The first lines of Blender's crash log for a file ("<name>.crash.txt", or "blender.crash.txt"),
 * when it was written after `since` (ms).
 * @param {string | null} blend
 * @param {number} since
 * @param {NodeJS.ProcessEnv} [env]
 */
export function findCrashLog(blend, since, env = process.env) {
  const names = [blend ? `${path.basename(blend).replace(/\.blend$/i, '')}.crash.txt` : null, 'blender.crash.txt'].filter(Boolean);
  for (const dir of tempDirs(env)) {
    for (const name of names) {
      const file = path.join(dir, /** @type {string} */ (name));
      try {
        if (statSync(file).mtimeMs < since) continue;
        const lines = readFileSync(file, 'utf8').split(/\r?\n/).filter((l) => l.trim());
        return { path: file, excerpt: lines.slice(0, 14).join('\n') };
      } catch {
        // not there
      }
    }
  }
  return null;
}
