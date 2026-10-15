import { spawnSync } from 'node:child_process';
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { PUPPETEER_REVISIONS } from 'puppeteer-core/internal/revisions.js';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import {
  BROWSER_MARKER, browserCandidates, browserDirs, browserLabel, extensionUserDirs, findBrowser, isHeadlessShell, locateBrowser,
  systemBrowserCandidates,
} from '../src/browser.js';
import {
  BUILD_ID, DOWNLOADS, browserPlatform, describeInstallError, installBrowser, installationDir, installedBrowser,
  relativeExecutable, runCli, type Downloader,
} from '../src/browser-install.js';

const BIN = fileURLToPath(new URL('../bin/gamestudio-browser.js', import.meta.url));
const require = createRequire(import.meta.url);
let tmp: string;

beforeEach(() => {
  tmp = mkdtempSync(path.join(process.env.GS_TEST_TMP ?? os.tmpdir(), 'gs_browser_'));
});
afterEach(() => {
  rmSync(tmp, { recursive: true, force: true, maxRetries: 5 });
});

/** A file that exists. */
function touch(file: string) {
  mkdirSync(path.dirname(file), { recursive: true });
  writeFileSync(file, '');
  return file;
}

/** A downloaded browser in `dir`: its executable and marker. */
function fakeDownload(dir: string, { platform = 'linux', buildId = BUILD_ID, marker = true } = {}) {
  const exe = touch(path.join(installationDir(dir, platform, buildId), relativeExecutable(platform)));
  if (marker) {
    writeFileSync(path.join(dir, BROWSER_MARKER), JSON.stringify({
      browser: 'chrome-headless-shell', buildId, platform, executable: path.relative(dir, exe).split(path.sep).join('/'), version: `Google Chrome for Testing ${buildId}`,
    }));
  }
  return exe;
}

describe('browser discovery', () => {
  const winEnv = (extra: Record<string, string> = {}) => ({
    'ProgramFiles(x86)': 'X:\\PF86', ProgramFiles: 'X:\\PF', LOCALAPPDATA: 'X:\\Local', PATH: '', APPDATA: path.join(tmp, 'nothing'), HOME: tmp, ...extra,
  });

  it('takes GAMESTUDIO_BROWSER, then GS_BROWSER, when the file exists', () => {
    const a = touch(path.join(tmp, 'a', 'browser.exe'));
    const b = touch(path.join(tmp, 'b', 'browser.exe'));
    const exists = (f: string) => existsSync(f) || f.endsWith('msedge.exe');
    expect(locateBrowser(winEnv({ GAMESTUDIO_BROWSER: a, GS_BROWSER: b }), { platform: 'win32', exists })).toEqual({ path: a, source: 'env' });
    expect(locateBrowser(winEnv({ GAMESTUDIO_BROWSER: path.join(tmp, 'missing'), GS_BROWSER: b }), { platform: 'win32', exists })).toEqual({ path: b, source: 'env' });
    expect(locateBrowser(winEnv({ GAMESTUDIO_BROWSER: path.join(tmp, 'missing') }), { platform: 'win32', exists })?.source).toBe('system');
  });

  it('prefers a system Edge, Chrome or Chromium to the downloaded browser, unless GAMESTUDIO_BROWSER_SYSTEM=0', () => {
    const dir = path.join(tmp, 'dl');
    const shell = fakeDownload(dir, { platform: 'win64' });
    const chrome = path.join('X:\\PF', 'Google', 'Chrome', 'Application', 'chrome.exe');
    const exists = (f: string) => f === chrome || existsSync(f);
    expect(locateBrowser(winEnv({ GAMESTUDIO_BROWSER_DIR: dir }), { platform: 'win32', exists })).toEqual({ path: chrome, source: 'system' });
    expect(locateBrowser(winEnv({ GAMESTUDIO_BROWSER_DIR: dir, GAMESTUDIO_BROWSER_SYSTEM: '0' }), { platform: 'win32', exists })).toEqual({ path: shell, source: 'downloaded' });
    expect(locateBrowser(winEnv({ GAMESTUDIO_BROWSER_DIR: dir }), { platform: 'win32', exists: existsSync })).toEqual({ path: shell, source: 'downloaded' });
    expect(findBrowser(winEnv({ GAMESTUDIO_BROWSER_DIR: path.join(tmp, 'empty') }), { platform: 'win32', exists: existsSync })).toBeNull();
  });

  it('lists the system browsers per platform in a fixed order: stable Edge and Chrome first, per-user installs too', () => {
    const win = systemBrowserCandidates(winEnv({ PATH: 'X:\\bin' }), 'win32');
    const edge = (base: string) => path.join(base, 'Microsoft', 'Edge', 'Application', 'msedge.exe');
    const chrome = (base: string) => path.join(base, 'Google', 'Chrome', 'Application', 'chrome.exe');
    expect(win.slice(0, 6)).toEqual([edge('X:\\PF86'), edge('X:\\PF'), edge('X:\\Local'), chrome('X:\\PF86'), chrome('X:\\PF'), chrome('X:\\Local')]);
    for (const rel of [['Chromium', 'Application', 'chrome.exe'], ['BraveSoftware', 'Brave-Browser', 'Application', 'brave.exe'], ['Vivaldi', 'Application', 'vivaldi.exe'],
      ['Google', 'Chrome SxS', 'Application', 'chrome.exe'], ['Microsoft', 'Edge Beta', 'Application', 'msedge.exe']]) {
      expect(win).toContain(path.join('X:\\Local', ...rel));
    }
    expect(win.indexOf(path.join('X:\\Local', 'Vivaldi', 'Application', 'vivaldi.exe'))).toBeLessThan(win.indexOf(path.join('X:\\PF86', 'Microsoft', 'Edge Beta', 'Application', 'msedge.exe')));
    expect(win).toContain(path.join('X:\\bin', 'chromium.exe'));
    const mac = systemBrowserCandidates({ HOME: '/Users/a b', PATH: '/usr/bin' }, 'darwin');
    expect(mac.slice(0, 2)).toEqual([
      path.join('/Applications', 'Google Chrome.app', 'Contents', 'MacOS', 'Google Chrome'),
      path.join('/Users/a b', 'Applications', 'Google Chrome.app', 'Contents', 'MacOS', 'Google Chrome'),
    ]);
    for (const name of ['Brave Browser', 'Vivaldi', 'Google Chrome Canary', 'Microsoft Edge Dev']) {
      expect(mac).toContain(path.join('/Applications', `${name}.app`, 'Contents', 'MacOS', name));
    }
    const linux = systemBrowserCandidates({ HOME: '/home/a', PATH: '/opt/bin' }, 'linux');
    expect(linux[0]).toBe(path.join('/opt/bin', 'microsoft-edge'));
    for (const file of ['google-chrome-stable', 'chromium-browser', 'microsoft-edge-stable', 'brave-browser', 'vivaldi']) expect(linux).toContain(path.join('/usr/bin', file));
    expect(linux.slice(-3)).toEqual(['/snap/bin/chromium', '/snap/bin/brave', '/var/lib/snapd/snap/bin/chromium']);
    expect(linux).toContain('/opt/google/chrome/chrome');
  });

  it('lists every browser found, in the order to try them', () => {
    const dir = path.join(tmp, 'dl');
    const shell = fakeDownload(dir, { platform: 'win64' });
    const own = touch(path.join(tmp, 'own.exe'));
    const system = [path.join('X:\\PF86', 'Microsoft', 'Edge', 'Application', 'msedge.exe'), path.join('X:\\Local', 'BraveSoftware', 'Brave-Browser', 'Application', 'brave.exe')];
    const exists = (f: string) => system.includes(f) || existsSync(f);
    expect(browserCandidates(winEnv({ GAMESTUDIO_BROWSER: own, GAMESTUDIO_BROWSER_DIR: dir }), { platform: 'win32', exists })).toEqual([
      { path: own, source: 'env' }, { path: system[0], source: 'system' }, { path: system[1], source: 'system' }, { path: shell, source: 'downloaded' },
    ]);
    expect(locateBrowser(winEnv({ GAMESTUDIO_BROWSER_DIR: dir }), { platform: 'win32', exists })).toEqual({ path: system[0], source: 'system' });
  });

  it('names browsers for the status line', () => {
    expect(browserLabel('C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe')).toBe('Edge');
    expect(browserLabel('/Applications/Brave Browser.app/Contents/MacOS/Brave Browser')).toBe('Brave');
    expect(browserLabel('/opt/vivaldi/vivaldi')).toBe('Vivaldi');
    expect(browserLabel('/snap/bin/chromium')).toBe('Chromium');
    expect(browserLabel('/opt/google/chrome/chrome')).toBe('Chrome');
    expect(browserLabel('/b/chrome-headless-shell-linux64/chrome-headless-shell')).toBe('Chrome Headless Shell');
  });

  it('finds the download in GAMESTUDIO_BROWSER_DIR first, then in the extension user folder of a Blender profile', () => {
    const resources = path.join(tmp, 'profile');
    const userFolder = path.join(resources, 'extensions', '.user', 'user_default', 'gamestudio');
    const appData = path.join(tmp, 'AppData');
    const v52 = path.join(appData, 'Blender Foundation', 'Blender', '5.2', 'extensions', '.user', 'blender_org', 'gamestudio');
    mkdirSync(userFolder, { recursive: true });
    mkdirSync(v52, { recursive: true });
    const env = { BLENDER_USER_RESOURCES: resources, APPDATA: appData, HOME: tmp, PATH: '', GAMESTUDIO_BROWSER_SYSTEM: '0' };
    expect(extensionUserDirs(env, 'win32')).toEqual([userFolder, v52]);
    expect(browserDirs({ ...env, GAMESTUDIO_BROWSER_DIR: path.join(tmp, 'own') }, 'win32')).toEqual([
      path.join(tmp, 'own'), path.join(userFolder, 'browser'), path.join(v52, 'browser'),
    ]);
    expect(findBrowser(env, { platform: 'win32' })).toBeNull();
    const inV52 = fakeDownload(path.join(v52, 'browser'));
    expect(locateBrowser(env, { platform: 'win32' })).toEqual({ path: inV52, source: 'downloaded' });
    const inProfile = fakeDownload(path.join(userFolder, 'browser'));
    expect(findBrowser(env, { platform: 'win32' })).toBe(inProfile);
    const own = fakeDownload(path.join(tmp, 'own'));
    expect(findBrowser({ ...env, GAMESTUDIO_BROWSER_DIR: path.join(tmp, 'own') }, { platform: 'win32' })).toBe(own);
  });

  it('ignores a download without its marker, or whose executable is gone', () => {
    const dir = path.join(tmp, 'dl');
    const exe = fakeDownload(dir, { marker: false });
    const env = { GAMESTUDIO_BROWSER_DIR: dir, GAMESTUDIO_BROWSER_SYSTEM: 'off', HOME: tmp, PATH: '' };
    expect(findBrowser(env, { platform: 'linux' })).toBeNull();
    fakeDownload(dir);
    expect(findBrowser(env, { platform: 'linux' })).toBe(exe);
    rmSync(exe);
    expect(findBrowser(env, { platform: 'linux' })).toBeNull();
    writeFileSync(path.join(dir, BROWSER_MARKER), '{not json');
    expect(findBrowser(env, { platform: 'linux' })).toBeNull();
  });

  it('knows Chrome Headless Shell by its executable', () => {
    expect(isHeadlessShell('C:\\b\\chrome-headless-shell-win64\\chrome-headless-shell.exe')).toBe(true);
    expect(isHeadlessShell('/b/chrome-headless-shell-mac-arm64/chrome-headless-shell')).toBe(true);
    expect(isHeadlessShell('C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe')).toBe(false);
    expect(isHeadlessShell(null)).toBe(false);
  });
});

describe('the pinned browser', () => {
  it('is the Chrome Headless Shell puppeteer-core is made for, fetched by its @puppeteer/browsers', () => {
    expect(BUILD_ID).toBe(PUPPETEER_REVISIONS['chrome-headless-shell']);
    const core = JSON.parse(readFileSync(require.resolve('puppeteer-core/package.json'), 'utf8'));
    const own = JSON.parse(readFileSync(fileURLToPath(new URL('../package.json', import.meta.url)), 'utf8'));
    expect(own.dependencies['@puppeteer/browsers']).toBe(core.dependencies['@puppeteer/browsers']);
    expect(own.dependencies['puppeteer-core']).toBe(core.version);
    for (const download of Object.values(DOWNLOADS)) {
      expect(download.sha256).toMatch(/^[0-9a-f]{64}$/);
      expect(download.bytes).toBeGreaterThan(50e6);
    }
  });

  it('picks the build for the machine', () => {
    expect(browserPlatform('win32', 'x64')).toBe('win64');
    expect(browserPlatform('win32', 'arm64', '10.0.26100')).toBe('win64');
    expect(browserPlatform('win32', 'arm64', '10.0.19045')).toBe('win32');
    expect(browserPlatform('darwin', 'arm64')).toBe('mac_arm');
    expect(browserPlatform('darwin', 'x64')).toBe('mac');
    expect(browserPlatform('linux', 'x64')).toBe('linux');
    expect(browserPlatform('linux', 'arm64')).toBe('linux_arm');
    expect(browserPlatform('linux', 'arm')).toBeNull();
    expect(browserPlatform('freebsd', 'x64')).toBeNull();
    expect(relativeExecutable('win64')).toBe(path.join('chrome-headless-shell-win64', 'chrome-headless-shell.exe'));
    expect(relativeExecutable('mac_arm')).toBe(path.join('chrome-headless-shell-mac-arm64', 'chrome-headless-shell'));
  });
});

/** A downloader that writes the executable where @puppeteer/browsers would, reporting progress in steps. */
function mockDownloader({ fail, verifyError, steps = 4, delayMs = 0 }: { fail?: Error; verifyError?: Error; steps?: number; delayMs?: number } = {}) {
  const calls: { platform: string; buildId: string; sha256: string; leftovers: boolean }[] = [];
  const downloader: Downloader = {
    async download({ dir, platform, buildId, sha256, onProgress }) {
      calls.push({ platform, buildId, sha256, leftovers: existsSync(installationDir(dir, platform, buildId)) || existsSync(path.join(dir, 'chrome-headless-shell', `${buildId}-x.zip`)) });
      const total = 1000;
      for (let i = 0; steps > 0 && i <= steps; i++) {
        if (delayMs) await new Promise((r) => setTimeout(r, delayMs));
        onProgress(Math.round((total * i) / steps), total);
      }
      if (fail) throw fail;
      return touch(path.join(installationDir(dir, platform, buildId), relativeExecutable(platform)));
    },
    async verify() {
      if (verifyError) throw verifyError;
      return `Google Chrome for Testing ${BUILD_ID}`;
    },
  };
  return { downloader, calls };
}

/** Runs the CLI in-process; returns its exit code and JSON lines. */
async function cli(argv: string[], options: Parameters<typeof runCli>[1] = {}) {
  const lines: Record<string, any>[] = [];
  const code = await runCli(argv, { env: {}, platform: 'linux', write: (e) => lines.push(JSON.parse(JSON.stringify(e))), ...options });
  return { code, lines, last: lines.at(-1)! };
}

describe('gamestudio-browser install / path', () => {
  it('installs once: progress lines, then the path; again, just the path', async () => {
    const dir = path.join(tmp, 'browser');
    expect((await cli(['path', '--dir', dir])).last).toEqual({ path: null, buildId: BUILD_ID, platform: 'linux', downloadBytes: DOWNLOADS.linux.bytes });

    const { downloader, calls } = mockDownloader();
    const first = await cli(['install', '--dir', dir], { downloader });
    expect(first.code).toBe(0);
    const exe = path.join(installationDir(dir, 'linux'), relativeExecutable('linux'));
    expect(first.last).toEqual({ path: exe, version: `Google Chrome for Testing ${BUILD_ID}`, buildId: BUILD_ID, downloaded: true });
    expect(first.lines[0]).toMatchObject({ stage: 'download', total: DOWNLOADS.linux.bytes });
    expect(first.lines[0].url).toBe(`https://storage.googleapis.com/chrome-for-testing-public/${BUILD_ID}/linux64/chrome-headless-shell-linux64.zip`);
    const progress = first.lines.filter((l) => 'progress' in l);
    expect(progress.map((l) => l.progress)).toEqual([0, 0.25, 0.5, 0.75, 1]);
    expect(progress.at(-1)).toEqual({ progress: 1, bytes: 1000, total: 1000 });
    expect(first.lines.filter((l) => l.stage).map((l) => l.stage)).toEqual(['download', 'extract', 'verify']);
    expect(calls).toEqual([{ platform: 'linux', buildId: BUILD_ID, sha256: DOWNLOADS.linux.sha256, leftovers: false }]);

    const marker = JSON.parse(readFileSync(path.join(dir, BROWSER_MARKER), 'utf8'));
    expect(marker).toMatchObject({ browser: 'chrome-headless-shell', buildId: BUILD_ID, platform: 'linux', executable: `chrome-headless-shell/linux-${BUILD_ID}/chrome-headless-shell-linux64/chrome-headless-shell` });
    expect(existsSync(path.join(dir, '.install.lock'))).toBe(false);

    const again = await cli(['install', '--dir', dir], { downloader });
    expect(again.lines).toEqual([{ path: exe, version: `Google Chrome for Testing ${BUILD_ID}`, buildId: BUILD_ID, downloaded: false }]);
    expect(calls).toHaveLength(1);
    expect((await cli(['path', '--dir', dir])).last).toMatchObject({ path: exe, current: true, installedBuildId: BUILD_ID });
    expect(findBrowser({ GAMESTUDIO_BROWSER_DIR: dir, GAMESTUDIO_BROWSER_SYSTEM: '0', HOME: tmp, PATH: '' })).toBe(exe);
  });

  it('takes the folder from GAMESTUDIO_BROWSER_DIR, and refuses bad usage', async () => {
    const dir = path.join(tmp, 'env-dir');
    const { downloader } = mockDownloader();
    expect((await cli(['install'], { downloader, env: { GAMESTUDIO_BROWSER_DIR: dir } })).last.path).toContain(dir);
    for (const argv of [[], ['path'], ['frobnicate', '--dir', dir]]) {
      const run = await cli(argv);
      expect(run.code).toBe(2);
      expect(run.last.error).toMatch(/^Usage/);
    }
  });

  it('cleans up what a killed install left, and replaces an older build', async () => {
    const dir = path.join(tmp, 'browser');
    touch(path.join(installationDir(dir, 'linux'), 'half-unpacked'));
    touch(path.join(dir, 'chrome-headless-shell', `${BUILD_ID}-x.zip`));
    const old = fakeDownload(dir, { buildId: '150.0.7000.1' });
    expect((await cli(['path', '--dir', dir])).last).toMatchObject({ path: old, current: false, installedBuildId: '150.0.7000.1' });
    const { downloader, calls } = mockDownloader();
    const run = await cli(['install', '--dir', dir], { downloader });
    expect(run.code).toBe(0);
    expect(calls[0].leftovers).toBe(false);
    expect(existsSync(installationDir(dir, 'linux', '150.0.7000.1'))).toBe(false);
    expect(installedBrowser(dir, 'linux')).toMatchObject({ buildId: BUILD_ID, current: true });
  });

  it('reports a browser that does not run, and forgets it so a retry downloads it again', async () => {
    const dir = path.join(tmp, 'browser');
    const { downloader } = mockDownloader({ verifyError: new Error('--version exited with 127: error while loading shared libraries: libnss3.so') });
    const run = await cli(['install', '--dir', dir], { downloader });
    expect(run.code).toBe(1);
    expect(run.last).toEqual({ error: 'The downloaded browser does not run: --version exited with 127: error while loading shared libraries: libnss3.so' });
    expect(existsSync(path.join(dir, BROWSER_MARKER))).toBe(false);
    expect(existsSync(installationDir(dir, 'linux'))).toBe(false);
    expect((await cli(['path', '--dir', dir])).last.path).toBeNull();
  });

  it('says plainly when the machine is offline, and when there is no build for it', async () => {
    const offline = new Error('All providers failed for chrome-headless-shell 155.0.8059.39:\n  - DefaultProvider: getaddrinfo ENOTFOUND storage.googleapis.com');
    const run = await cli(['install', '--dir', path.join(tmp, 'b')], { downloader: mockDownloader({ fail: offline, steps: 0 }).downloader });
    expect(run.code).toBe(1);
    expect(run.last).toMatchObject({ offline: true });
    expect(run.last.error).toMatch(/^Could not download the browser: no connection .*ENOTFOUND/);

    const none = await cli(['install', '--dir', path.join(tmp, 'c')], { platform: null });
    expect(none.code).toBe(1);
    expect(none.last.error).toMatch(/^There is no Chrome Headless Shell for/);
    expect((await cli(['path', '--dir', path.join(tmp, 'c')], { platform: null })).last).toMatchObject({ path: null, platform: null, downloadBytes: null });

    expect(describeInstallError(new Error('Integrity check failed for downloaded browser archive.\n  URL: x'))).toEqual({ error: 'The browser download was damaged (its checksum did not match). Try again.' });
    expect(describeInstallError(new Error('All providers failed:\n  - DefaultProvider: Download failed: server returned code 404. URL: x'))).toEqual({ error: 'The browser download failed: the server answered HTTP 404.' });
    expect(describeInstallError(Object.assign(new Error('write failed'), { code: 'ENOSPC' }), '/b').error).toMatch(/^Not enough disk space .* in \/b\.$/);
  });

  it('gives up on a download that stalls', async () => {
    const stuck: Downloader = { download: () => new Promise(() => {}), verify: async () => 'x 1.0' };
    const run = await cli(['install', '--dir', path.join(tmp, 'b')], { downloader: stuck, stallMs: 50 });
    expect(run.code).toBe(1);
    expect(run.last).toEqual({ error: 'Could not reach the browser download (no answer)', offline: true });
  });

  it('downloads once when two installs of one folder run at the same time', async () => {
    const dir = path.join(tmp, 'browser');
    const { downloader, calls } = mockDownloader({ steps: 10, delayMs: 30 });
    const events: Record<string, unknown>[] = [];
    const [a, b] = await Promise.all([
      installBrowser({ dir, platform: 'linux', downloader }),
      new Promise((r) => setTimeout(r, 20)).then(() => installBrowser({ dir, platform: 'linux', downloader, onEvent: (e) => events.push(e) })),
    ]);
    expect(calls).toHaveLength(1);
    expect(a.downloaded).toBe(true);
    expect(b).toMatchObject({ path: a.path, downloaded: false });
    expect(events[0]).toMatchObject({ stage: 'waiting' });
  });

  it('runs as a program: path prints one JSON line', () => {
    const env = { ...process.env, GAMESTUDIO_BROWSER_DIR: '' };
    const empty = spawnSync(process.execPath, [BIN, 'path', '--dir', path.join(tmp, 'none')], { encoding: 'utf8', env });
    expect(empty.status).toBe(0);
    expect(JSON.parse(empty.stdout)).toMatchObject({ path: null, buildId: BUILD_ID });
    const exe = fakeDownload(path.join(tmp, 'dl'), { platform: browserPlatform() ?? 'linux' });
    const found = spawnSync(process.execPath, [BIN, 'path', '--dir', path.join(tmp, 'dl')], { encoding: 'utf8', env });
    expect(JSON.parse(found.stdout)).toMatchObject({ path: exe, current: browserPlatform() !== null });
    const usage = spawnSync(process.execPath, [BIN, 'install'], { encoding: 'utf8', env });
    expect(usage.status).toBe(2);
    expect(JSON.parse(usage.stdout).error).toMatch(/^Usage/);
  });
});
