// @ts-check
// SPDX-License-Identifier: MIT
// Downloads Chrome Headless Shell for machines without Edge, Chrome or Chromium (a Mac with only
// Safari, a bare Linux): the browser the stream host and the MCP server's headless games play in
// (src/browser.js finds it). Run by bin/gamestudio-browser.js, which the GameStudio extension calls
// as one step of its first-run setup.
//
// The build is pinned: the Chrome that puppeteer-core 25.13.0 is made for (its PUPPETEER_REVISIONS),
// with each platform's archive size and SHA-256, so a damaged or swapped download is refused. It is
// fetched by @puppeteer/browsers into <dir>/chrome-headless-shell/<platform>-<build>/ (its cache
// layout), unpacked with the system's tar.exe or unzip (yauzl where there is no unzip; both keep
// the executable bit), and run once (--version). Only then <dir>/browser.json is written: the marker
// src/browser.js and the extension look for. A lock file keeps two installs into one folder apart.

import { spawn } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { mkdir, readdir, rename, rm, stat, writeFile } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { BROWSER_MARKER, readBrowserMarker } from './browser.js';

export const BROWSER = 'chrome-headless-shell';
/** puppeteer-core 25.13.0's PUPPETEER_REVISIONS['chrome-headless-shell'] (a test checks it). */
export const BUILD_ID = '155.0.8059.39';
export const DOWNLOAD_BASE = 'https://storage.googleapis.com/chrome-for-testing-public';

/**
 * The pinned archives, by @puppeteer/browsers platform. `folder` is Chrome for Testing's name.
 * Sizes and hashes of the zips at DOWNLOAD_BASE/BUILD_ID/<folder>/chrome-headless-shell-<folder>.zip.
 * @type {Readonly<Record<string, { folder: string, bytes: number, sha256: string }>>}
 */
export const DOWNLOADS = Object.freeze({
  win64: { folder: 'win64', bytes: 124_737_213, sha256: '20798f7c51a22def0b3d02e526a8f52e7ee2c7eddcde9d81a4c23f618e8b5436' },
  win32: { folder: 'win32', bytes: 118_304_713, sha256: '86191692553301daa53ae4d08ccb9de5cb7738eccc7a7fb058df0b0f327a40cf' },
  mac_arm: { folder: 'mac-arm64', bytes: 102_429_510, sha256: 'b3e093c06001c41e68decbc8dd4a62f9efe4bf4e4dd247a686ea531863448d75' },
  mac: { folder: 'mac-x64', bytes: 107_758_350, sha256: '6338a784c691f42dd1ed6aeeef650171c7d72cf74bdb8450d0079864e72a8074' },
  linux: { folder: 'linux64', bytes: 124_203_329, sha256: '39dcb8c46550632a3d911850ab3b8af840b4e3f6d8622faa2018eb8756278786' },
  linux_arm: { folder: 'linux-arm64', bytes: 124_553_121, sha256: '9fb86f7c0b2734c5febc0bbb4e85f37da43553f3f8a7970949828c5713e87e94' },
});

const LOCK = '.install.lock';
/** Another install of the same folder is waited for this long. */
const LOCK_WAIT_MS = 15 * 60_000;
/** A lock older than this is left over from a killed install. */
const STALE_LOCK_MS = 30 * 60_000;
/** The download fails when no byte arrives for this long. */
const STALL_MS = 60_000;
const VERIFY_TIMEOUT_MS = 30_000;
/** Progress lines at most this often (and on every whole percent). */
const PROGRESS_INTERVAL_MS = 250;

/**
 * @typedef {Record<string, unknown>} BrowserEvent   one JSON line of the CLI
 * @typedef {object} Downloader
 * @property {(options: { dir: string, platform: string, buildId: string, sha256: string,
 *   onProgress: (bytes: number, total: number) => void }) => Promise<string>} download
 *   downloads and unpacks the browser into `dir`; resolves with its executable
 * @property {(executable: string) => Promise<string>} verify   runs it; resolves with its version
 * @typedef {{ path: string, buildId: string, version: string, downloaded: boolean }} InstallResult
 */

export class BrowserInstallError extends Error {
  /** @param {string} message @param {{ offline?: boolean }} [details] */
  constructor(message, { offline = false } = {}) {
    super(message);
    this.offline = offline;
  }
}

/**
 * The download for this machine (a DOWNLOADS key), or null when Chrome for Testing has none.
 * Windows on Arm: Windows 11 runs the x64 build; Windows 10 only the x86 one.
 * @param {NodeJS.Platform} [platform]
 * @param {string} [arch]
 * @param {string} [release] os.release()
 * @returns {string | null}
 */
export function browserPlatform(platform = process.platform, arch = process.arch, release = os.release()) {
  if (platform === 'win32') {
    if (arch === 'x64') return 'win64';
    if (arch === 'arm64') return Number(release.split('.')[2] ?? 0) >= 22000 ? 'win64' : 'win32';
    return arch === 'ia32' ? 'win32' : null;
  }
  if (platform === 'darwin') return arch === 'arm64' ? 'mac_arm' : arch === 'x64' ? 'mac' : null;
  if (platform === 'linux') return arch === 'x64' ? 'linux' : arch === 'arm64' ? 'linux_arm' : null;
  return null;
}

/** Where @puppeteer/browsers unpacks a build. @param {string} dir @param {string} platform @param {string} [buildId] */
export const installationDir = (dir, platform, buildId = BUILD_ID) => path.join(dir, BROWSER, `${platform}-${buildId}`);

/** The executable inside installationDir. @param {string} platform */
export function relativeExecutable(platform) {
  const folder = `chrome-headless-shell-${DOWNLOADS[platform]?.folder ?? platform}`;
  return path.join(folder, platform.startsWith('win') ? 'chrome-headless-shell.exe' : 'chrome-headless-shell');
}

/**
 * The browser installed in `dir` (its marker), and whether it is the pinned build for this machine.
 * @param {string} dir
 * @param {string | null} [platform]
 */
export function installedBrowser(dir, platform = browserPlatform()) {
  const marker = readBrowserMarker(path.resolve(dir));
  if (!marker) return null;
  return { ...marker, current: marker.buildId === BUILD_ID && marker.platform === platform };
}

/** @param {number} pid */
function pidAlive(pid) {
  try {
    process.kill(pid, 0);
    return true;
  } catch (error) {
    return /** @type {any} */ (error)?.code === 'EPERM';
  }
}

const sleep = (/** @type {number} */ ms) => new Promise((resolve) => setTimeout(resolve, ms));

/**
 * Runs `fn` holding <dir>/.install.lock; waits while another live process holds it.
 * @template T
 * @param {string} dir
 * @param {(event: BrowserEvent) => void} onEvent
 * @param {() => Promise<T>} fn
 * @returns {Promise<T>}
 */
async function withLock(dir, onEvent, fn) {
  const lock = path.join(dir, LOCK);
  const deadline = Date.now() + LOCK_WAIT_MS;
  let waiting = false;
  for (;;) {
    try {
      await writeFile(lock, JSON.stringify({ pid: process.pid, started: new Date().toISOString() }), { flag: 'wx' });
      break;
    } catch (error) {
      if (/** @type {any} */ (error)?.code !== 'EEXIST') throw error;
      /** @type {any} */
      let holder = null;
      try {
        holder = JSON.parse(readFileSync(lock, 'utf8'));
      } catch {
        // being written, or damaged: judged by its age
      }
      const age = Date.now() - ((await stat(lock).catch(() => null))?.mtimeMs ?? 0);
      const live = holder && typeof holder.pid === 'number' ? pidAlive(holder.pid) : age < 5_000;
      if (!live || age > STALE_LOCK_MS) {
        await rm(lock, { force: true });
        continue;
      }
      if (Date.now() > deadline) throw new BrowserInstallError('Another GameStudio is installing the browser into this folder and has not finished');
      if (!waiting) onEvent({ stage: 'waiting', message: 'Another GameStudio is installing the browser' });
      waiting = true;
      await sleep(1000);
    }
  }
  try {
    return await fn();
  } finally {
    await rm(lock, { force: true });
  }
}

/**
 * Removes what a killed or failed install left in <dir>/chrome-headless-shell: archives, and the
 * folder of `keep`'s build when `partial` (it has no marker, so it never finished), or every other
 * build when not.
 * @param {string} dir @param {string} keep the build folder name (<platform>-<build>) @param {boolean} partial
 */
async function tidy(dir, keep, partial) {
  const root = path.join(dir, BROWSER);
  for (const entry of await readdir(root, { withFileTypes: true }).catch(() => [])) {
    const full = path.join(root, entry.name);
    if (entry.isFile() && entry.name.endsWith('.zip')) await rm(full, { force: true });
    else if (entry.isDirectory() && (entry.name === keep) === partial) await rm(full, { recursive: true, force: true, maxRetries: 5, retryDelay: 200 });
  }
}

/**
 * Installs the pinned Chrome Headless Shell into `dir` (nothing to do when it is there already).
 * Events: { stage: 'waiting' | 'download' | 'extract' | 'verify' }, { progress, bytes, total }.
 * @param {{ dir: string, platform?: string | null, downloader?: Downloader, onEvent?: (event: BrowserEvent) => void,
 *   stallMs?: number }} options
 * @returns {Promise<InstallResult>}
 */
export async function installBrowser({ dir, platform = browserPlatform(), downloader = puppeteerDownloader, onEvent = () => {}, stallMs = STALL_MS }) {
  const pinned = platform ? DOWNLOADS[platform] : undefined;
  if (!platform || !pinned) {
    throw new BrowserInstallError(`There is no Chrome Headless Shell for ${process.platform} on ${process.arch}`);
  }
  dir = path.resolve(dir);
  const done = () => {
    const found = installedBrowser(dir, platform);
    return found?.current ? { path: found.path, buildId: BUILD_ID, version: String(found.version ?? ''), downloaded: false } : null;
  };
  const before = done();
  if (before) return before;
  await mkdir(dir, { recursive: true });
  return withLock(dir, onEvent, async () => {
    const meanwhile = done();
    if (meanwhile) return meanwhile;
    const build = `${platform}-${BUILD_ID}`;
    await tidy(dir, build, true);
    onEvent({ stage: 'download', total: pinned.bytes, url: `${DOWNLOAD_BASE}/${BUILD_ID}/${pinned.folder}/chrome-headless-shell-${pinned.folder}.zip` });
    let last = { at: 0, percent: -1 };
    let received = 0;
    let finished = false;
    /** @type {NodeJS.Timeout | undefined} */
    let watchdog;
    const executable = await new Promise((resolve, reject) => {
      const arm = () => {
        clearTimeout(watchdog);
        watchdog = setTimeout(() => reject(new BrowserInstallError(
          received ? `The browser download stalled (no data for ${Math.round(stallMs / 1000)} s)` : 'Could not reach the browser download (no answer)',
          { offline: !received },
        )), stallMs);
      };
      arm();
      downloader.download({
        dir, platform, buildId: BUILD_ID, sha256: pinned.sha256,
        onProgress: (bytes, total) => {
          received = bytes;
          total ||= pinned.bytes;
          const percent = Math.floor((bytes / total) * 100);
          const now = Date.now();
          const complete = bytes >= total;
          if (complete || percent !== last.percent || now - last.at >= PROGRESS_INTERVAL_MS) {
            if (!finished) onEvent({ progress: Math.min(1, Math.round((bytes / total) * 1000) / 1000), bytes, total });
            last = { at: now, percent };
          }
          if (complete) {
            if (!finished) onEvent({ stage: 'extract' });
            finished = true;
            clearTimeout(watchdog);
          } else arm();
        },
      }).then(resolve, reject);
    }).finally(() => clearTimeout(watchdog));
    onEvent({ stage: 'verify' });
    let version;
    try {
      version = await downloader.verify(executable);
    } catch (error) {
      // Not trusted again: a retry downloads it afresh.
      await rm(installationDir(dir, platform), { recursive: true, force: true, maxRetries: 5, retryDelay: 200 }).catch(() => {});
      throw new BrowserInstallError(`The downloaded browser does not run: ${firstLine(error)}`);
    }
    const marker = {
      comment: 'Chrome Headless Shell downloaded by GameStudio (@gamestudio/devserver bin/gamestudio-browser.js). Delete this folder to remove it.',
      browser: BROWSER,
      buildId: BUILD_ID,
      platform,
      executable: path.relative(dir, executable).split(path.sep).join('/'),
      version,
      installed: new Date().toISOString(),
    };
    const temp = path.join(dir, `${BROWSER_MARKER}.${process.pid}.tmp`);
    await writeFile(temp, `${JSON.stringify(marker, null, 2)}\n`);
    await rename(temp, path.join(dir, BROWSER_MARKER));
    await tidy(dir, build, false);
    return { path: executable, buildId: BUILD_ID, version, downloaded: true };
  });
}

/** @param {unknown} error */
const firstLine = (error) => String(error instanceof Error ? error.message : error).split('\n').map((l) => l.trim()).filter(Boolean)[0] ?? 'unknown error';

/**
 * One plain sentence for a failed install, and whether the machine seems to be offline.
 * @param {unknown} error
 * @param {string} [dir]
 * @returns {{ error: string, offline?: true }}
 */
export function describeInstallError(error, dir = '') {
  if (error instanceof BrowserInstallError) return { error: error.message, ...(error.offline ? { offline: true } : {}) };
  const text = String(error instanceof Error ? `${error.message} ${/** @type {any} */ (error).cause?.message ?? ''} ${/** @type {any} */ (error).code ?? ''}` : error);
  // @puppeteer/browsers: "All providers failed for ...:\n  - DefaultProvider: <reason>"
  const reason = text.match(/DefaultProvider: ([^\n]+)/)?.[1]?.trim() ?? firstLine(error);
  const net = text.match(/\b(ENOTFOUND|EAI_AGAIN|ECONNREFUSED|ECONNRESET|ETIMEDOUT|ENETUNREACH|EHOSTUNREACH|ENETDOWN)\b/)?.[1];
  if (net) return { error: `Could not download the browser: no connection to the download server (${net}). Check the internet connection and try again.`, offline: true };
  if (/connection closed before|expected \d+ bytes, received/i.test(text)) return { error: 'The browser download was interrupted. Try again.', offline: true };
  if (/Integrity check failed/i.test(text)) return { error: 'The browser download was damaged (its checksum did not match). Try again.' };
  const status = text.match(/server returned code (\d+)/)?.[1];
  if (status) return { error: `The browser download failed: the server answered HTTP ${status}.` };
  if (/\bENOSPC\b/.test(text)) return { error: `Not enough disk space for the browser (about 350 MB)${dir ? ` in ${dir}` : ''}.` };
  if (/\b(EACCES|EPERM|EROFS)\b/.test(text)) return { error: `GameStudio cannot write the browser${dir ? ` to ${dir}` : ''}: ${reason}` };
  return { error: `Could not install the browser: ${reason}` };
}

/**
 * Runs `<executable> --version`; resolves with what it prints.
 * @param {string} executable
 * @returns {Promise<string>}
 */
export function verifyBrowser(executable) {
  return new Promise((resolve, reject) => {
    const child = spawn(executable, ['--version'], { stdio: ['ignore', 'pipe', 'pipe'], windowsHide: true });
    let out = '';
    child.stdout.on('data', (d) => (out += d));
    child.stderr.on('data', (d) => (out += d));
    const timer = setTimeout(() => {
      child.kill('SIGKILL');
      reject(new Error(`no answer to --version within ${VERIFY_TIMEOUT_MS / 1000} s`));
    }, VERIFY_TIMEOUT_MS);
    child.once('error', (error) => {
      clearTimeout(timer);
      reject(error);
    });
    child.once('exit', (code, signal) => {
      clearTimeout(timer);
      const text = out.trim();
      if (code === 0 && /\d+\.\d+/.test(text)) resolve(text.split('\n')[0].trim());
      else reject(new Error(`--version ${signal ? `was killed (${signal})` : `exited with ${code}`}${text ? `: ${text.slice(0, 400)}` : ''}`));
    });
  });
}

/** @type {Downloader} */
export const puppeteerDownloader = {
  async download({ dir, platform, buildId, sha256, onProgress }) {
    const browsers = await import('@puppeteer/browsers');
    const installed = await browsers.install({
      browser: browsers.Browser.CHROMEHEADLESSSHELL,
      buildId,
      platform: /** @type {import('@puppeteer/browsers').BrowserPlatform} */ (platform),
      cacheDir: dir,
      expectedHash: sha256,
      downloadProgressCallback: onProgress,
    });
    return installed.executablePath;
  },
  verify: verifyBrowser,
};

/** @param {string[]} argv @param {string} name */
function option(argv, name) {
  const i = argv.indexOf(name);
  return i >= 0 && i + 1 < argv.length ? argv[i + 1] : undefined;
}

export const USAGE = 'Usage: gamestudio-browser install|path [--dir FOLDER]   (FOLDER defaults to $GAMESTUDIO_BROWSER_DIR)';

/**
 * The CLI (bin/gamestudio-browser.js): every output line is one JSON object.
 *   install --dir D   {"stage"...} and {"progress", "bytes", "total"} lines, then {"path", ...};
 *                     or {"error", "offline"?} and exit code 1
 *   path --dir D      {"path": executable | null, ...}
 * @param {string[]} argv
 * @param {{ env?: NodeJS.ProcessEnv, write?: (event: BrowserEvent) => void, downloader?: Downloader,
 *   platform?: string | null, stallMs?: number }} [options]
 * @returns {Promise<number>} the exit code
 */
export async function runCli(argv, { env = process.env, write = (event) => process.stdout.write(`${JSON.stringify(event)}\n`), downloader = puppeteerDownloader, platform = browserPlatform(), stallMs } = {}) {
  const command = argv[0];
  const dirArg = option(argv, '--dir') ?? env.GAMESTUDIO_BROWSER_DIR;
  if (!['install', 'path'].includes(command ?? '') || !dirArg || argv.includes('--help')) {
    write({ error: USAGE });
    return 2;
  }
  const dir = path.resolve(dirArg);
  const pinned = platform ? DOWNLOADS[platform] : undefined;
  const about = { buildId: BUILD_ID, platform: platform ?? null, downloadBytes: pinned?.bytes ?? null };
  if (command === 'path') {
    const found = installedBrowser(dir, platform);
    write(found
      ? { path: found.path, version: found.version ?? null, current: found.current, installedBuildId: found.buildId ?? null, ...about }
      : { path: null, ...about });
    return 0;
  }
  try {
    const result = await installBrowser({ dir, platform, downloader, onEvent: write, ...(stallMs ? { stallMs } : {}) });
    write({ path: result.path, version: result.version, buildId: result.buildId, downloaded: result.downloaded });
    return 0;
  } catch (error) {
    write(describeInstallError(error, dir));
    return 1;
  }
}
