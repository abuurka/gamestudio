// Headless play on the GPU: GAMESTUDIO_BROWSER_GPU, the browser switches per mode, the WebGL check of
// a new browser and the fallback to SwiftShader (against a stand-in puppeteer).
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import puppeteer from 'puppeteer-core';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { GameBrowser, browserArgs, gpuMode, gpuProblem, isShaderCompilerNoise, isSoftwareRenderer, probeWebGL } from '../src/browser.js';

vi.mock('puppeteer-core', () => ({ default: { launch: vi.fn() } }));

const NVIDIA = 'ANGLE (NVIDIA, NVIDIA GeForce RTX 4070 (0x00002786) Direct3D11 vs_5_0 ps_5_0, D3D11)';
const SWIFTSHADER = 'ANGLE (Google, Vulkan 1.3.0 (SwiftShader Device (Subzero) (0x0000C0DE)), SwiftShader driver)';
const COMMON = ['--no-first-run', '--no-default-browser-check', '--mute-audio', '--autoplay-policy=no-user-gesture-required', '--disable-background-timer-throttling', '--disable-renderer-backgrounding', '--disable-backgrounding-occluded-windows'];

describe('GAMESTUDIO_BROWSER_GPU', () => {
  it('is auto unless it says 1 or 0', () => {
    for (const v of [undefined, '', 'auto', 'AUTO', ' auto ', 'maybe', '2']) expect(gpuMode(v)).toBe('auto');
    for (const v of ['1', 'true', 'on', 'yes', 'gpu', 'GPU', ' 1 ']) expect(gpuMode(v)).toBe('gpu');
    for (const v of ['0', 'false', 'off', 'no', 'swiftshader', 'software']) expect(gpuMode(v)).toBe('swiftshader');
  });
});

describe('browser switches per mode', () => {
  it('keeps the switches of 0 (SwiftShader) and 1 (the browser\'s choice) as they were', () => {
    expect(browserArgs('swiftshader', 'win32')).toEqual([...COMMON, '--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--ignore-gpu-blocklist']);
    expect(browserArgs('swiftshader', 'linux')).toEqual(browserArgs('swiftshader', 'win32'));
    expect(browserArgs('gpu', 'win32')).toEqual(COMMON);
    expect(browserArgs('gpu', 'linux')).toEqual(COMMON);
  });

  it('asks for the GPU in auto, like the stream host: D3D11 on Windows, SwiftShader only without a GPU', () => {
    const win = browserArgs('auto', 'win32');
    expect(win).toEqual([...COMMON, '--ignore-gpu-blocklist', '--enable-unsafe-swiftshader', '--use-angle=d3d11']);
    expect(win).not.toContain('--use-angle=swiftshader');
    const linux = browserArgs('auto', 'linux');
    expect(linux).toEqual([...COMMON, '--ignore-gpu-blocklist', '--enable-unsafe-swiftshader']);
    expect(linux.some((a) => a.startsWith('--use-angle'))).toBe(false);
    expect(browserArgs('auto', 'darwin')).toEqual(linux);
  });

  it('asks Chrome Headless Shell for the GPU (it draws with SwiftShader otherwise), with no Windows switch elsewhere', () => {
    for (const platform of /** @type {NodeJS.Platform[]} */ (['win32', 'darwin', 'linux'])) {
      expect(browserArgs('auto', platform, true)).toEqual([...browserArgs('auto', platform), '--enable-gpu']);
      expect(browserArgs('gpu', platform, true)).toEqual([...COMMON, '--enable-gpu']);
      expect(browserArgs('swiftshader', platform, true)).toEqual(browserArgs('swiftshader', platform));
    }
    expect(browserArgs('auto', 'darwin', true).some((a) => a.startsWith('--use-angle'))).toBe(false);
  });
});

describe('the WebGL check', () => {
  it('tells software renderers from GPUs', () => {
    for (const r of [SWIFTSHADER, 'llvmpipe (LLVM 15.0.7, 256 bits)', 'ANGLE (Microsoft, Microsoft Basic Render Driver Direct3D11 vs_5_0 ps_5_0, D3D11)', 'Mesa softpipe']) {
      expect(isSoftwareRenderer(r)).toBe(true);
    }
    for (const r of [NVIDIA, 'ANGLE (Apple, ANGLE Metal Renderer: Apple M2, Unspecified Version)', 'Mesa Intel(R) UHD Graphics 620 (KBL GT2)']) {
      expect(isSoftwareRenderer(r)).toBe(false);
    }
    expect(isSoftwareRenderer(null)).toBe(false);
  });

  it('rules out the GPU with a reason', () => {
    expect(gpuProblem({ webgl2: true, renderer: NVIDIA })).toBeNull();
    expect(gpuProblem({ webgl2: true, renderer: SWIFTSHADER })).toMatch(/^software renderer ANGLE \(Google/);
    expect(gpuProblem({ webgl2: false, renderer: null })).toBe('no WebGL2');
    expect(gpuProblem({ webgl2: true, renderer: null })).toMatch(/unknown/);
    expect(gpuProblem({ webgl2: false, renderer: null, error: 'Target closed' })).toBe('the WebGL check failed: Target closed');
  });

  it('checks in a blank page it closes, and never throws', async () => {
    const ok = fakeBrowser({ probe: { webgl2: true, renderer: NVIDIA } });
    expect(await probeWebGL(ok)).toEqual({ webgl2: true, renderer: NVIDIA });
    expect(ok.pages).toHaveLength(1);
    expect(ok.pages[0].closed).toBe(true);
    expect(ok.pages[0].evaluated).toEqual(['webglInPage']);

    const crashed = fakeBrowser({ probe: new Error('Protocol error: Target closed\nstack') });
    expect(await probeWebGL(crashed)).toEqual({ webgl2: false, renderer: null, error: 'Protocol error: Target closed' });
    expect(crashed.pages[0].closed).toBe(true);

    const hung = fakeBrowser({ probe: 'hang' });
    expect(await probeWebGL(hung, 30)).toMatchObject({ webgl2: false, error: 'no answer within 0.03 s' });
  });
});

describe('launching per mode', () => {
  /** @type {any} */
  const launch = puppeteer.launch;
  /** @type {any[]} */
  let queue;
  beforeEach(() => {
    queue = [];
    launch.mockReset();
    launch.mockImplementation(async (/** @type {any} */ options) => {
      const next = queue.shift();
      if (next instanceof Error) throw next;
      next.options = options;
      return next;
    });
  });

  /** @param {string | undefined} gpu */
  function gameBrowser(gpu) {
    const lines = /** @type {string[]} */ ([]);
    const env = { GAMESTUDIO_BROWSER: process.execPath, ...(gpu !== undefined && { GAMESTUDIO_BROWSER_GPU: gpu }) };
    return { browser: new GameBrowser({ env, log: (l) => lines.push(l) }), lines };
  }
  const switches = () => launch.mock.calls.map((/** @type {any[]} */ [o]) => o.args);

  it('auto: keeps a browser whose WebGL2 runs on a GPU', async () => {
    const gpu = fakeBrowser({ probe: { webgl2: true, renderer: NVIDIA } });
    queue.push(gpu);
    const { browser, lines } = gameBrowser(undefined);
    const game = await browser.openGame('http://x/');
    expect(switches()).toEqual([browserArgs('auto')]);
    expect(gpu.closed).toBe(false);
    expect(game.page).toBe(gpu.pages[1]); // the probe's page came first, and is closed
    expect(gpu.pages[0].closed).toBe(true);
    expect(browser.rendering).toEqual({ mode: 'auto', renderer: NVIDIA, gpu: true });
    expect(browser.renderInfo()).toEqual({ renderer: NVIDIA, gpu: true });
    expect(lines).toEqual([`browser started: ${process.execPath} (env; GAMESTUDIO_BROWSER_GPU=auto; WebGL: ${NVIDIA})`]);

    await browser.openGame('http://x/'); // once per browser
    expect(launch).toHaveBeenCalledTimes(1);
    expect(gpu.pages.filter((p) => p.evaluated.includes('webglInPage'))).toHaveLength(1);
  });

  it('launches the downloaded Chrome Headless Shell in the shell mode of puppeteer, with the same switches and fallback', async () => {
    const dir = mkdtempSync(path.join(process.env.GS_TEST_TMP ?? os.tmpdir(), 'gs_shell_'));
    try {
      const exe = path.join(dir, 'chrome-headless-shell-linux64', 'chrome-headless-shell');
      mkdirSync(path.dirname(exe), { recursive: true });
      writeFileSync(exe, '');
      writeFileSync(path.join(dir, 'browser.json'), JSON.stringify({ executable: 'chrome-headless-shell-linux64/chrome-headless-shell' }));
      queue.push(fakeBrowser({ probe: { webgl2: true, renderer: SWIFTSHADER } }), fakeBrowser({ probe: { webgl2: true, renderer: SWIFTSHADER } }));
      const lines = /** @type {string[]} */ ([]);
      const browser = new GameBrowser({ env: { GAMESTUDIO_BROWSER_DIR: dir, GAMESTUDIO_BROWSER_SYSTEM: '0', HOME: dir, PATH: '' }, log: (l) => lines.push(l) });
      await browser.openGame('http://x/');
      expect(launch.mock.calls.map((/** @type {any[]} */ [o]) => [o.executablePath, o.headless])).toEqual([[exe, 'shell'], [exe, 'shell']]);
      expect(switches()).toEqual([browserArgs('auto', process.platform, true), browserArgs('swiftshader', process.platform, true)]);
      expect(switches()[0]).toContain('--enable-gpu');
      expect(lines.at(-1)).toMatch(/\(downloaded; GAMESTUDIO_BROWSER_GPU=auto; WebGL: .*SwiftShader/);
    } finally {
      rmSync(dir, { recursive: true, force: true, maxRetries: 5 });
    }
  });

  it('tries the next browser when one does not start, and says when there is none', async () => {
    const dir = mkdtempSync(path.join(process.env.GS_TEST_TMP ?? os.tmpdir(), 'gs_next_'));
    try {
      const shell = path.join(dir, 'chrome-headless-shell-linux64', 'chrome-headless-shell');
      mkdirSync(path.dirname(shell), { recursive: true });
      writeFileSync(shell, '');
      writeFileSync(path.join(dir, 'browser.json'), JSON.stringify({ executable: 'chrome-headless-shell-linux64/chrome-headless-shell' }));
      queue.push(new Error('Failed to launch the browser process!\nconfined'), new Error('still not'), fakeBrowser({ probe: { webgl2: true, renderer: NVIDIA } }));
      const lines = /** @type {string[]} */ ([]);
      const env = { GAMESTUDIO_BROWSER: process.execPath, GAMESTUDIO_BROWSER_DIR: dir, GAMESTUDIO_BROWSER_SYSTEM: '0', HOME: dir, APPDATA: dir, PATH: '' };
      const browser = new GameBrowser({ env, log: (l) => lines.push(l) });
      await browser.openGame('http://x/');
      expect(launch.mock.calls.map((/** @type {any[]} */ [o]) => o.executablePath)).toEqual([process.execPath, process.execPath, shell]);
      expect(lines).toContain(`${process.execPath} did not start: still not`);
      expect(lines.at(-1)).toMatch(/\(downloaded; GAMESTUDIO_BROWSER_GPU=auto; WebGL: ANGLE \(NVIDIA/);

      const none = new GameBrowser({ env: { GAMESTUDIO_BROWSER_SYSTEM: '0', HOME: dir, APPDATA: dir, XDG_CONFIG_HOME: dir, PATH: '' } });
      await expect(none.openGame('http://x/')).rejects.toMatchObject({ code: 'browser_not_found', message: expect.stringMatching(/^GameStudio has no game browser yet/) });
    } finally {
      rmSync(dir, { recursive: true, force: true, maxRetries: 5 });
    }
  });

  it('auto: starts again with SwiftShader when the check finds software GL, and says why', async () => {
    const soft = fakeBrowser({ probe: { webgl2: true, renderer: SWIFTSHADER } });
    const fallback = fakeBrowser({ probe: { webgl2: true, renderer: SWIFTSHADER } });
    queue.push(soft, fallback);
    const { browser, lines } = gameBrowser('auto');
    const game = await browser.openGame('http://x/');
    expect(switches()).toEqual([browserArgs('auto'), browserArgs('swiftshader')]);
    expect(soft.closed).toBe(true);
    expect(game.page).toBe(fallback.pages[1]);
    expect(browser.browser).toBe(fallback);
    expect(browser.rendering).toEqual({ mode: 'auto', renderer: SWIFTSHADER, gpu: false, fallback: `software renderer ${SWIFTSHADER}` });
    expect(lines[0]).toBe(`no GPU for headless play (software renderer ${SWIFTSHADER}): starting the browser again with SwiftShader`);
    expect(lines[1]).toMatch(/^browser started: .*SwiftShader driver\)\)$/);
  });

  it('auto: falls back when the check fails, finds no WebGL2 or the GPU launch fails', async () => {
    for (const [first, reason] of /** @type {[any, RegExp][]} */ ([
      [fakeBrowser({ probe: new Error('Target closed') }), /^the WebGL check failed: Target closed$/],
      [fakeBrowser({ probe: { webgl2: false, renderer: null } }), /^no WebGL2$/],
      [new Error('Failed to launch the browser process!\nmore'), /^the browser did not start with the GPU switches: Failed to launch the browser process!$/],
    ])) {
      launch.mockClear();
      const fallback = fakeBrowser({ probe: { webgl2: true, renderer: SWIFTSHADER } });
      queue.push(first, fallback);
      const { browser, lines } = gameBrowser(undefined);
      await browser.openGame('http://x/');
      expect(switches()).toEqual([browserArgs('auto'), browserArgs('swiftshader')]);
      if (!(first instanceof Error)) expect(first.closed).toBe(true);
      expect(browser.browser).toBe(fallback);
      expect(browser.rendering?.fallback).toMatch(reason);
      expect(lines[0]).toMatch(/^no GPU for headless play \(.*\): starting the browser again with SwiftShader$/);
    }
  });

  it('1: the GPU switches as before, reported but never replaced', async () => {
    const soft = fakeBrowser({ probe: { webgl2: true, renderer: SWIFTSHADER } });
    queue.push(soft);
    const { browser } = gameBrowser('1');
    await browser.openGame('http://x/');
    expect(switches()).toEqual([COMMON]);
    expect(soft.closed).toBe(false);
    expect(browser.rendering).toEqual({ mode: 'gpu', renderer: SWIFTSHADER, gpu: false });
  });

  it('0: SwiftShader as before, with no GPU attempt', async () => {
    const soft = fakeBrowser({ probe: { webgl2: true, renderer: SWIFTSHADER } });
    queue.push(soft);
    const { browser } = gameBrowser('0');
    await browser.openGame('http://x/');
    expect(switches()).toEqual([browserArgs('swiftshader')]);
    expect(launch).toHaveBeenCalledTimes(1);
    expect(browser.rendering).toEqual({ mode: 'swiftshader', renderer: SWIFTSHADER, gpu: false });
  });

  it('0 and 1: a failed launch is an error, not a fallback', async () => {
    for (const gpu of ['0', '1']) {
      queue.push(new Error('Failed to launch'));
      await expect(gameBrowser(gpu).browser.openGame('http://x/')).rejects.toThrow('Failed to launch');
    }
    expect(launch).toHaveBeenCalledTimes(2);
  });

  it('a check that fails in 0 or 1 only leaves the renderer unknown', async () => {
    queue.push(fakeBrowser({ probe: new Error('Target closed') }));
    const { browser, lines } = gameBrowser('1');
    await browser.openGame('http://x/');
    expect(launch).toHaveBeenCalledTimes(1);
    expect(browser.rendering).toEqual({ mode: 'gpu', renderer: null, gpu: null });
    expect(lines).toEqual([`browser started: ${process.execPath} (env; GAMESTUDIO_BROWSER_GPU=gpu; WebGL: Target closed)`]);
  });

  it('keeps Direct3D shader-compiler warnings out of the page logs', async () => {
    queue.push(fakeBrowser({ probe: { webgl2: true, renderer: NVIDIA } }));
    const { browser } = gameBrowser(undefined);
    const game = await browser.openGame('http://x/');
    const emit = (/** @type {string} */ type, /** @type {string} */ text) => game.page.handlers.console({ type: () => type, text: () => text });
    emit('warning', 'THREE.WebGLProgram: Program Info Log: (210,81-129): warning X4122: sum of 0.996094 and -2.98545e-017 cannot be represented accurately in double precision\n(209,37-61): warning X4122: sum of 1 and -1.49272e-017 cannot be represented accurately in double precision');
    emit('warning', 'THREE.WebGLProgram: Program Info Log: (12,3): error X3004: undeclared identifier');
    emit('log', 'THREE.WebGLProgram: Program Info Log: (210,81-129): warning X4122: as a log line');
    expect(game.logs.map((l) => l.level)).toEqual(['warn', 'log']);
  });
});

describe('isShaderCompilerNoise', () => {
  it('matches only program logs made of HLSL warnings', () => {
    const noise = 'THREE.WebGLProgram: Program Info Log: (210,81-129): warning X4122: sum cannot be represented\n(209,37): warning X3557: loop only executes for 1 iteration(s)';
    expect(isShaderCompilerNoise('warn', noise)).toBe(true);
    expect(isShaderCompilerNoise('error', noise)).toBe(false);
    expect(isShaderCompilerNoise('warn', `${noise}\n(4,1): error X3000: syntax error`)).toBe(false);
    expect(isShaderCompilerNoise('warn', 'THREE.WebGLProgram: Program Info Log:')).toBe(false);
    expect(isShaderCompilerNoise('warn', 'Player fell (210,81): warning X4122')).toBe(false);
  });
});

/**
 * A stand-in puppeteer browser: newPage() pages answer the WebGL check with `probe` (an Error
 * throws, 'hang' never answers) and do nothing for the rest.
 * @param {{ probe: any }} options
 */
function fakeBrowser({ probe }) {
  /** @type {any} */
  const browser = {
    connected: true,
    closed: false,
    /** @type {any[]} */
    pages: [],
    on() {},
    async newPage() {
      const page = fakePage(probe);
      browser.pages.push(page);
      return page;
    },
    async userAgent() {
      return 'Mozilla/5.0 HeadlessEdg/150.0.0.0';
    },
    async close() {
      browser.closed = true;
      browser.connected = false;
    },
  };
  return browser;
}

/** @param {any} probe */
function fakePage(probe) {
  /** @type {any} */
  const page = {
    closed: false,
    /** @type {string[]} */
    evaluated: [],
    /** @type {Record<string, (...args: any[]) => void>} */
    handlers: {},
    on(/** @type {string} */ event, /** @type {any} */ fn) {
      page.handlers[event] = fn;
    },
    async evaluate(/** @type {Function} */ fn) {
      page.evaluated.push(fn.name);
      if (fn.name !== 'webglInPage') return '';
      if (probe === 'hang') return new Promise(() => {});
      if (probe instanceof Error) throw probe;
      return probe;
    },
    async setViewport() {},
    async setUserAgent() {},
    async evaluateOnNewDocument() {},
    async goto() {},
    async waitForFunction() {},
    async close() {
      page.closed = true;
    },
    isClosed: () => page.closed,
  };
  return page;
}
