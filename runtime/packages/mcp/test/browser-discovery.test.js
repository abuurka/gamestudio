// The MCP server's browser discovery is a copy of @gamestudio/devserver's src/browser.js (this
// package does not depend on that one): both must find the same browser, in the same order.
import { existsSync, mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import * as devserver from '../../devserver/src/browser.js';
import * as mcp from '../src/discovery.js';

let tmp;
beforeEach(() => {
  tmp = mkdtempSync(path.join(process.env.GS_TEST_TMP ?? os.tmpdir(), 'gs_mcp_browser_'));
});
afterEach(() => {
  rmSync(tmp, { recursive: true, force: true, maxRetries: 5 });
});

/** @param {string} file */
function touch(file) {
  mkdirSync(path.dirname(file), { recursive: true });
  writeFileSync(file, '');
  return file;
}

/** A downloaded browser in `dir` (executable + the installer's marker). @param {string} dir */
function download(dir) {
  const exe = touch(path.join(dir, 'chrome-headless-shell', 'linux-1.0', 'chrome-headless-shell-linux64', 'chrome-headless-shell'));
  writeFileSync(path.join(dir, 'browser.json'), JSON.stringify({ browser: 'chrome-headless-shell', buildId: '1.0', executable: 'chrome-headless-shell/linux-1.0/chrome-headless-shell-linux64/chrome-headless-shell' }));
  return exe;
}

describe('MCP browser discovery agrees with the devserver', () => {
  it('lists the same system browsers and user folders on every platform', () => {
    const profile = path.join(tmp, 'profile');
    mkdirSync(path.join(profile, 'extensions', '.user', 'user_default', 'gamestudio'), { recursive: true });
    const env = {
      'ProgramFiles(x86)': 'X:\\PF86', ProgramFiles: 'X:\\PF', LOCALAPPDATA: 'X:\\Local', PATH: process.platform === 'win32' ? 'X:\\a;X:\\b' : '/a:/b',
      HOME: tmp, APPDATA: path.join(tmp, 'AppData'), XDG_CONFIG_HOME: path.join(tmp, 'xdg'), BLENDER_USER_RESOURCES: profile, GAMESTUDIO_BROWSER_DIR: path.join(tmp, 'own'),
    };
    for (const platform of /** @type {NodeJS.Platform[]} */ (['win32', 'darwin', 'linux'])) {
      expect(mcp.systemBrowserCandidates(env, platform)).toEqual(devserver.systemBrowserCandidates(env, platform));
      expect(mcp.extensionUserDirs(env, platform)).toEqual(devserver.extensionUserDirs(env, platform));
      expect(mcp.browserDirs(env, platform)).toEqual(devserver.browserDirs(env, platform));
    }
    expect(mcp.extensionUserDirs(env, 'win32')).toHaveLength(1);
    for (const list of ['WINDOWS_BROWSERS', 'MAC_BROWSERS', 'BROWSER_COMMANDS', 'LINUX_BROWSERS']) expect(mcp[list], list).toEqual(devserver[list]);
  });

  it('finds the same browser, from the same source, whatever is installed', () => {
    const own = path.join(tmp, 'own');
    const profile = path.join(tmp, 'profile');
    const userFolder = path.join(profile, 'extensions', '.user', 'user_default', 'gamestudio');
    mkdirSync(userFolder, { recursive: true });
    const envBrowser = touch(path.join(tmp, 'env', 'browser'));
    const base = { HOME: tmp, PATH: '', APPDATA: path.join(tmp, 'AppData'), XDG_CONFIG_HOME: path.join(tmp, 'xdg'), ProgramFiles: 'X:\\PF', BLENDER_USER_RESOURCES: profile };
    const systemFiles = new Set([
      path.join('X:\\PF', 'Google', 'Chrome', 'Application', 'chrome.exe'),
      path.join('/Applications', 'Microsoft Edge.app', 'Contents', 'MacOS', 'Microsoft Edge'),
      path.join('/usr/bin', 'chromium'),
    ]);
    const check = (/** @type {string} */ name, /** @type {Record<string, string>} */ env, /** @type {boolean} */ system, /** @type {'env' | 'system' | 'downloaded' | null} */ source) => {
      // Only the test's own files are real: a browser installed on the test machine must not count.
      const exists = (/** @type {string} */ f) => (system && systemFiles.has(f)) || (f.startsWith(tmp) && existsSync(f));
      for (const platform of /** @type {NodeJS.Platform[]} */ (['win32', 'darwin', 'linux'])) {
        const a = mcp.locateBrowser(env, { platform, exists });
        const b = devserver.locateBrowser(env, { platform, exists });
        expect(a, `${name} on ${platform}`).toEqual(b);
        expect(a?.source ?? null, `${name} on ${platform}`).toBe(source);
        expect(mcp.findBrowser(env, { platform, exists })).toBe(devserver.findBrowser(env, { platform, exists }));
        expect(mcp.browserCandidates(env, { platform, exists })).toEqual(devserver.browserCandidates(env, { platform, exists }));
      }
    };
    check('nothing', base, false, null);
    check('system only', base, true, 'system');
    check('env and system', { ...base, GS_BROWSER: envBrowser }, true, 'env');
    check('a missing env browser', { ...base, GAMESTUDIO_BROWSER: path.join(tmp, 'gone') }, true, 'system');
    download(own);
    check('system and a download', { ...base, GAMESTUDIO_BROWSER_DIR: own }, true, 'system');
    check('a download, system skipped', { ...base, GAMESTUDIO_BROWSER_DIR: own, GAMESTUDIO_BROWSER_SYSTEM: '0' }, true, 'downloaded');
    check('a download only', { ...base, GAMESTUDIO_BROWSER_DIR: own }, false, 'downloaded');
    const inProfile = download(path.join(userFolder, 'browser'));
    check('a download in the Blender profile', base, false, 'downloaded');
    expect(mcp.findBrowser({ ...base, GAMESTUDIO_BROWSER_SYSTEM: '0' }, { platform: 'linux' })).toBe(inProfile);
  });

  it('knows Chrome Headless Shell the same way', () => {
    for (const file of ['a/chrome-headless-shell', 'a\\chrome-headless-shell.exe', 'a/chrome', 'a/msedge.exe', '']) {
      expect(mcp.isHeadlessShell(file)).toBe(devserver.isHeadlessShell(file));
    }
  });
});
