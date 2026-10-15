import { spawn, type ChildProcess } from 'node:child_process';
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { createServer, type Server } from 'node:http';
import net from 'node:net';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { afterEach, describe, expect, it } from 'vitest';
import { StreamHost, browserArgs, findBrowser, softwareRendering, type BrowserPage } from '../src/stream-host.js';
import {
  FORMAT, MESSAGE_HEADER, MessageReader, RateLimiter, decodeFrame, encodeFrame, encodeJson, encodeMessage, jpegSize,
  keyEventParams, mouseEventParams, virtualKeyCode,
} from '../src/stream-protocol.js';

const HOST_BIN = fileURLToPath(new URL('../bin/gamestudio-stream.js', import.meta.url));
// The smallest JPEG header that matters to jpegSize: SOI, an APP0 segment, SOF0 with 640x360.
const JPEG_HEAD = Buffer.from([
  0xff, 0xd8, 0xff, 0xe0, 0x00, 0x04, 0x00, 0x00, 0xff, 0xc0, 0x00, 0x11, 0x08, 0x01, 0x68, 0x02, 0x80, 0x03, 0x01, 0x22, 0x00,
]);

describe('stream wire format', () => {
  it('encodes messages with a big-endian length and a type byte', () => {
    const msg = encodeMessage('S', Buffer.from('{}'));
    expect([...msg.subarray(0, MESSAGE_HEADER)]).toEqual([0, 0, 0, 2, 'S'.charCodeAt(0)]);
    expect(msg.subarray(MESSAGE_HEADER).toString()).toBe('{}');
  });

  it('splits a byte stream into messages, whatever the chunking', () => {
    const pixels = Buffer.alloc(300_000, 7);
    const stream = Buffer.concat([
      encodeJson('J', { type: 'hello', token: 't' }),
      encodeFrame({ width: 10, height: 20, frame: 3, format: FORMAT.JPEG, timestamp: 1234.5 }, pixels),
      encodeJson('S', { status: 'ready' }),
    ]);
    for (const size of [1, 7, 4096, stream.length]) {
      const reader = new MessageReader();
      const got = [];
      for (let i = 0; i < stream.length; i += size) got.push(...reader.push(stream.subarray(i, i + size)));
      expect(got.map((m) => m.type)).toEqual(['J', 'F', 'S']);
      expect(JSON.parse(got[0].payload.toString())).toEqual({ type: 'hello', token: 't' });
      const frame = decodeFrame(got[1].payload);
      expect(frame).toMatchObject({ width: 10, height: 20, frame: 3, format: FORMAT.JPEG, timestamp: 1234.5 });
      expect(frame.pixels.equals(pixels)).toBe(true);
      expect(JSON.parse(got[2].payload.toString())).toEqual({ status: 'ready' });
    }
  });

  it('rejects oversized messages', () => {
    const reader = new MessageReader(100);
    expect(() => reader.push(encodeMessage('J', Buffer.alloc(101)))).toThrow(/too large/);
  });

  it('reads JPEG sizes from the SOF marker', () => {
    expect(jpegSize(JPEG_HEAD)).toEqual([640, 360]);
    expect(jpegSize(Buffer.from('not a jpeg'))).toBeNull();
  });
});

describe('stream input', () => {
  it('maps keys to CDP key events with text for printable keys', () => {
    expect(keyEventParams({ kind: 'down', code: 'KeyW', key: 'w' })).toMatchObject({
      type: 'keyDown', code: 'KeyW', key: 'w', text: 'w', windowsVirtualKeyCode: 87, modifiers: 0,
    });
    expect(keyEventParams({ kind: 'down', code: 'KeyA', key: 'A', shift: true })).toMatchObject({ text: 'A', modifiers: 8 });
    expect(keyEventParams({ kind: 'down', code: 'KeyC', key: 'c', ctrl: true })).toMatchObject({ type: 'rawKeyDown', modifiers: 2 });
    expect(keyEventParams({ kind: 'down', code: 'Enter', key: 'Enter' })).toMatchObject({ type: 'keyDown', text: '\r', windowsVirtualKeyCode: 13 });
    expect(keyEventParams({ kind: 'down', code: 'ArrowLeft', key: 'ArrowLeft' })).toMatchObject({ type: 'rawKeyDown', windowsVirtualKeyCode: 37 });
    expect(keyEventParams({ kind: 'up', code: 'Space', key: ' ' })).toMatchObject({ type: 'keyUp', windowsVirtualKeyCode: 32 });
    expect(keyEventParams({ kind: 'down', code: 'ShiftRight', key: 'Shift' })).toMatchObject({ location: 2, windowsVirtualKeyCode: 16 });
    expect(keyEventParams({ kind: 'press', code: 'KeyW' })).toBeNull();
    expect([virtualKeyCode('Digit7'), virtualKeyCode('Numpad3'), virtualKeyCode('F12'), virtualKeyCode('Escape')]).toEqual([55, 99, 123, 27]);
  });

  it('maps mouse commands, tracking held buttons and scaling to CSS pixels', () => {
    const state = { buttons: 0 };
    expect(mouseEventParams({ kind: 'down', x: 100, y: 50, button: 2 }, state, 0.5, 0.5)).toMatchObject({
      type: 'mousePressed', x: 50, y: 25, button: 'right', buttons: 2, clickCount: 1,
    });
    expect(mouseEventParams({ kind: 'move', x: 110, y: 50 }, state)).toMatchObject({ type: 'mouseMoved', button: 'right', buttons: 2 });
    expect(mouseEventParams({ kind: 'up', x: 110, y: 50, button: 2 }, state)).toMatchObject({ type: 'mouseReleased', buttons: 0 });
    expect(mouseEventParams({ kind: 'move', x: 1, y: 1 }, state)).toMatchObject({ button: 'none', buttons: 0 });
    expect(mouseEventParams({ kind: 'wheel', x: 1, y: 1, dx: 0, dy: 120 }, state)).toMatchObject({ type: 'mouseWheel', deltaX: 0, deltaY: 120 });
    expect(mouseEventParams({ kind: 'down', x: 1, y: 1, button: 1 }, state)).toMatchObject({ button: 'middle', buttons: 4 });
    expect(mouseEventParams({ kind: 'drag', x: 1, y: 1 }, state)).toBeNull();
  });

  it('caps the frame rate on average', () => {
    const limiter = new RateLimiter(60);
    let sent = 0;
    for (let t = 0; t < 10_000; t += 1000 / 75) if (limiter.allow(t)) sent++;
    expect(sent).toBeGreaterThanOrEqual(595);
    expect(sent).toBeLessThanOrEqual(605);
    limiter.set(0);
    expect(limiter.allow(0) && limiter.allow(0)).toBe(true);
  });

  it('keeps puppeteer from muting and picks the GPU', () => {
    const args = browserArgs('headless', { width: 960, height: 540 }, { platform: 'win32' });
    expect(args).toContain('--use-angle=d3d11');
    expect(args).not.toContain('--mute-audio');
    expect(args).not.toContain('--use-angle=swiftshader');
    expect(browserArgs('offscreen', { width: 960, height: 540 }, { platform: 'linux' })).toContain('--window-position=-32000,-32000');
    // No Windows-only switch elsewhere (macOS: the browser's own Metal backend).
    expect(browserArgs('headless', { width: 960, height: 540 }, { platform: 'darwin' }).some((a) => a.startsWith('--use-angle'))).toBe(false);
  });

  it('asks Chrome Headless Shell for the GPU, which it leaves off by default', () => {
    for (const platform of ['win32', 'darwin', 'linux'] as const) {
      const plain = browserArgs('headless', { width: 960, height: 540 }, { platform });
      expect(browserArgs('headless', { width: 960, height: 540 }, { platform, shell: true })).toEqual([...plain, '--enable-gpu']);
      expect(plain).not.toContain('--enable-gpu');
    }
  });

  it('draws in software when GAMESTUDIO_BROWSER_GPU asks for it', () => {
    for (const value of ['0', 'off', 'False', ' swiftshader ', 'software']) expect(softwareRendering(value)).toBe(true);
    for (const value of [undefined, '', 'auto', '1', 'gpu']) expect(softwareRendering(value)).toBe(false);
    const args = browserArgs('headless', { width: 960, height: 540 }, { platform: 'win32', shell: true, software: true });
    expect(args).toContain('--use-angle=swiftshader');
    expect(args).not.toContain('--use-angle=d3d11');
    expect(args).not.toContain('--enable-gpu');
  });
});

/** A fake browser page: records CDP calls, emits screencast frames on demand. */
function fakePage() {
  const calls: { method: string; params?: any }[] = [];
  const gotos: string[] = [];
  const listeners = new Map<string, ((e: any) => void)[]>();
  let onClose: ((reason: string) => void) | null = null;
  const page: BrowserPage & {
    calls: typeof calls; gotos: string[]; emit: (event: string, e: any) => void; crash: (r: string) => void; closed: boolean;
  } = {
    calls,
    gotos,
    closed: false,
    name: 'Fake 1',
    userAgent: 'Fake/1.0',
    cdp: {
      send: async (method: string, params?: object) => {
        calls.push({ method, params });
        return method === 'Browser.getWindowForTarget' ? { windowId: 7 } : {};
      },
      on: (event: string, fn: (e: any) => void) => listeners.set(event, [...(listeners.get(event) ?? []), fn]),
    },
    goto: async (url: string) => {
      gotos.push(url);
    },
    evaluate: async () => 'Fake GPU',
    onClose: (fn) => {
      onClose = fn;
    },
    close: async () => {
      page.closed = true;
    },
    emit: (event, e) => listeners.get(event)?.forEach((fn) => fn(e)),
    crash: (reason) => onClose?.(reason),
  };
  return page;
}

/** A client of a StreamHost: connects, says hello, collects messages. */
async function connect(port: number, token: string) {
  const socket = net.connect(port, '127.0.0.1');
  await new Promise((r) => socket.once('connect', r));
  const reader = new MessageReader();
  const messages: { type: string; payload: Buffer }[] = [];
  socket.on('data', (c) => messages.push(...reader.push(c as Buffer)));
  const closed = new Promise((r) => socket.once('close', r));
  socket.write(encodeJson('J', { type: 'hello', token, formats: ['jpeg'] }));
  const send = (cmd: object) => socket.write(encodeJson('J', cmd));
  const statuses = () => messages.filter((m) => m.type === 'S').map((m) => JSON.parse(m.payload.toString()));
  return { socket, messages, send, statuses, closed };
}

const until = async (fn: () => unknown, ms = 5000) => {
  const end = Date.now() + ms;
  while (!fn()) {
    if (Date.now() > end) throw new Error('timed out');
    await new Promise((r) => setTimeout(r, 10));
  }
};

describe('StreamHost commands', () => {
  const hosts: StreamHost[] = [];
  afterEach(async () => {
    for (const host of hosts.splice(0)) await host.shutdown('test over');
  });

  async function startHost(options: Partial<ConstructorParameters<typeof StreamHost>[0]> = {}) {
    const page = fakePage();
    const host = new StreamHost({ url: 'http://game.test/', browserPath: process.execPath, openPage: async () => page, ...options });
    hosts.push(host);
    const { port, token } = await host.listen();
    host.start();
    return { host, page, port, token };
  }

  it('drops clients without the token', async () => {
    const { port } = await startHost();
    const bad = await connect(port, 'wrong');
    await bad.closed;
    expect(bad.statuses()).toEqual([]);
  });

  it('streams frames, maps input to the frame size and follows size, pause and stop', async () => {
    const { host, page, port, token } = await startHost({ width: 960, height: 540, maxFps: 0 });
    const client = await connect(port, token);
    await until(() => client.statuses().some((s) => s.status === 'ready'));
    expect(client.statuses().at(-1)).toMatchObject({ status: 'ready', renderer: 'Fake GPU', gpu: true, mode: 'headless', audio: true });
    expect(page.calls.find((c) => c.method === 'Emulation.setDeviceMetricsOverride')?.params).toMatchObject({ width: 960, height: 540, deviceScaleFactor: 1 });
    expect(page.calls.find((c) => c.method === 'Page.startScreencast')?.params).toMatchObject({ format: 'jpeg', maxWidth: 960, maxHeight: 540 });

    // A 640x360 frame of a 960x540 page: mouse positions are frame pixels.
    page.emit('Page.screencastFrame', { data: JPEG_HEAD.toString('base64'), metadata: { timestamp: 1000 }, sessionId: 1 });
    await until(() => client.messages.some((m) => m.type === 'F'));
    const frame = decodeFrame(client.messages.find((m) => m.type === 'F')!.payload);
    expect(frame).toMatchObject({ width: 640, height: 360, frame: 1, format: FORMAT.JPEG, timestamp: 1_000_000 });
    expect(page.calls).toContainEqual({ method: 'Page.screencastFrameAck', params: { sessionId: 1 } });
    client.send({ type: 'mouse', kind: 'down', x: 320, y: 180, button: 0 });
    client.send({ type: 'key', kind: 'down', code: 'KeyW', key: 'w' });
    await until(() => page.calls.some((c) => c.method === 'Input.dispatchKeyEvent'));
    expect(page.calls.find((c) => c.method === 'Input.dispatchMouseEvent')?.params).toMatchObject({ type: 'mousePressed', x: 480, y: 270, button: 'left' });

    // Pause lifts held input and stops the screencast; frames are not sent while paused.
    const stops = page.calls.filter((c) => c.method === 'Page.stopScreencast').length;
    client.send({ type: 'pause' });
    await until(() => page.calls.filter((c) => c.method === 'Page.stopScreencast').length > stops);
    expect(page.calls.filter((c) => c.method === 'Input.dispatchKeyEvent').at(-1)?.params).toMatchObject({ type: 'keyUp', code: 'KeyW' });
    expect(page.calls.filter((c) => c.method === 'Input.dispatchMouseEvent').at(-1)?.params).toMatchObject({ type: 'mouseReleased' });
    page.emit('Page.screencastFrame', { data: JPEG_HEAD.toString('base64'), sessionId: 2 });
    client.send({ type: 'resume' });
    await until(() => page.calls.filter((c) => c.method === 'Page.startScreencast').length >= 3);
    expect(client.messages.filter((m) => m.type === 'F')).toHaveLength(1);

    client.send({ type: 'size', width: 1280, height: 720, scale: 2 });
    await until(() => page.calls.filter((c) => c.method === 'Emulation.setDeviceMetricsOverride').length === 2);
    expect(page.calls.filter((c) => c.method === 'Emulation.setDeviceMetricsOverride').at(-1)?.params).toMatchObject({ width: 640, height: 360, deviceScaleFactor: 2 });

    client.send({ type: 'stop' });
    expect(await host.closed).toBe('Stopped by Blender');
    expect(page.closed).toBe(true);
    await client.closed;
    expect(client.statuses().at(-1)).toMatchObject({ status: 'stopped' });
  });

  it('starts the browser without an address and loads the game on navigate', async () => {
    const { page, port, token } = await startHost({ url: undefined, agent: 'GameStudioStream/test' });
    const client = await connect(port, token);
    await until(() => client.statuses().some((s) => /waiting for the game/.test(s.message)));
    expect(page.gotos).toEqual([]);
    expect(page.calls.some((c) => c.method === 'Page.startScreencast')).toBe(false);
    // The engine sends its user agent when it joins the live link: the mark tells Blender which game is streamed.
    expect(page.calls.find((c) => c.method === 'Emulation.setUserAgentOverride')?.params).toEqual({ userAgent: 'Fake/1.0 GameStudioStream/test' });
    expect(client.statuses().at(-1)).toMatchObject({ status: 'starting', url: '', agent: 'GameStudioStream/test' });

    client.send({ type: 'navigate', url: 'javascript:alert(1)' });
    client.send({ type: 'navigate', url: 'http://game.test/' });
    await until(() => client.statuses().some((s) => s.status === 'ready'));
    expect(page.gotos).toEqual(['http://game.test/']);
    expect(client.statuses().at(-1)).toMatchObject({ status: 'ready', url: 'http://game.test/' });
    expect(page.calls.some((c) => c.method === 'Page.startScreencast')).toBe(true);

    // The same address again: nothing; a new one (the dev server moved): the game loads there.
    client.send({ type: 'navigate', url: 'http://game.test/' });
    client.send({ type: 'navigate', url: 'http://localhost:5174/' });
    await until(() => page.gotos.length === 2);
    expect(page.gotos).toEqual(['http://game.test/', 'http://localhost:5174/']);
    await until(() => client.statuses().at(-1)?.url === 'http://localhost:5174/' && client.statuses().at(-1)?.status === 'ready');
  });

  it('hides a suspended page once it has loaded, and shows it before frames or a load', async () => {
    let finishLoad: () => void = () => {};
    const page = fakePage();
    page.goto = async (url: string) => {
      page.gotos.push(url);
      await new Promise<void>((resolve) => (finishLoad = resolve));
    };
    const { port, token } = await startHost({ openPage: async () => page });
    const client = await connect(port, token);
    const lifecycle = () => page.calls.filter((c) => c.method === 'Browser.setWindowBounds').map((c) => c.params.bounds.windowState);
    const count = (method: string) => page.calls.filter((c) => c.method === method).length;
    await until(() => page.gotos.length === 1);

    // Nobody sees or plays the game: frames stop at once, hiding waits for the page to load.
    client.send({ type: 'pause', suspend: true });
    await until(() => count('Page.stopScreencast') > 0);
    expect(lifecycle()).toEqual([]);
    finishLoad();
    await until(() => client.statuses().at(-1)?.suspended === true);
    expect(lifecycle()).toEqual(['minimized']);
    expect(page.calls.find((c) => c.method === 'Browser.setWindowBounds')?.params.windowId).toBe(7);
    expect(client.statuses().at(-1)).toMatchObject({ status: 'ready', paused: true });

    // Play: shown, then the frames start again.
    const starts = count('Page.startScreencast');
    client.send({ type: 'resume' });
    await until(() => count('Page.startScreencast') > starts);
    expect(lifecycle()).toEqual(['minimized', 'normal']);
    const methods = page.calls.map((c) => c.method);
    expect(methods.lastIndexOf('Browser.setWindowBounds')).toBeLessThan(methods.lastIndexOf('Page.startScreencast'));

    // Unseen while it plays: no frames, the page renders on. Then stopped too: hidden.
    const stops = count('Page.stopScreencast');
    client.send({ type: 'pause' });
    await until(() => count('Page.stopScreencast') > stops);
    expect(lifecycle()).toEqual(['minimized', 'normal']);
    client.send({ type: 'pause', suspend: true });
    await until(() => lifecycle().length === 3);

    // A new address shows the page for the load; it is hidden again once loaded (still suspended).
    client.send({ type: 'navigate', url: 'http://localhost:5174/' });
    await until(() => page.gotos.length === 2);
    expect(lifecycle()).toEqual(['minimized', 'normal', 'minimized', 'normal']);
    finishLoad();
    await until(() => lifecycle().length === 5);
    expect(lifecycle().at(-1)).toBe('minimized');
    expect(page.calls.filter((c) => c.method === 'Browser.getWindowForTarget')).toHaveLength(1);
  });

  it('loads an address that came while the browser started', async () => {
    let open: (page: BrowserPage) => void = () => {};
    const page = fakePage();
    const host = new StreamHost({ browserPath: process.execPath, openPage: () => new Promise((resolve) => (open = resolve)) });
    hosts.push(host);
    const { port, token } = await host.listen();
    host.start();
    const client = await connect(port, token);
    await until(() => client.statuses().length > 0);
    client.send({ type: 'navigate', url: 'http://game.test/' });
    await until(() => host.url === 'http://game.test/');
    open(page);
    await until(() => client.statuses().some((s) => s.status === 'ready'));
    expect(page.gotos).toEqual(['http://game.test/']);
  });

  it('reports a crashed page as an error and exits', async () => {
    const { host, page, port, token } = await startHost();
    const client = await connect(port, token);
    await until(() => client.statuses().some((s) => s.status === 'ready'));
    page.crash('The game page crashed: boom');
    expect(await host.closed).toMatch(/crashed/);
    await client.closed;
    expect(client.statuses().at(-1)).toMatchObject({ status: 'error', message: 'The game page crashed: boom' });
  });

  it('plays headless in Chrome Headless Shell, which has no window for offscreen', async () => {
    let opened: { mode: string } | null = null;
    const { port, token } = await startHost({
      mode: 'offscreen', browserPath: '/gs/browser/chrome-headless-shell-linux64/chrome-headless-shell',
      openPage: async (options) => {
        opened = options;
        return fakePage();
      },
    });
    const client = await connect(port, token);
    await until(() => client.statuses().some((s) => s.status === 'ready'));
    expect(opened!.mode).toBe('headless');
    expect(client.statuses().at(-1)).toMatchObject({ mode: 'headless' });
  });

  it('tries the next browser when one does not start', async () => {
    const dir = mkdtempSync(path.join(process.env.GS_TEST_TMP ?? os.tmpdir(), 'gs_stream_'));
    try {
      const next = path.join(dir, 'chrome.exe');
      writeFileSync(next, '');
      const tried: string[] = [];
      const { port, token, host } = await startHost({
        browserPath: path.join(dir, 'broken', 'chromium'), env: { GAMESTUDIO_BROWSER: next, GAMESTUDIO_BROWSER_SYSTEM: '0', HOME: dir, PATH: '' },
        openPage: async (options) => {
          tried.push(options.browserPath);
          if (tried.length === 1) throw new Error('Failed to launch the browser process!\nsnap confinement');
          return fakePage();
        },
      });
      const client = await connect(port, token);
      await until(() => client.statuses().some((s) => s.status === 'ready'));
      expect(tried).toEqual([path.join(dir, 'broken', 'chromium'), next]);
      await host.shutdown('test over');
    } finally {
      rmSync(dir, { recursive: true, force: true, maxRetries: 5 });
    }
  });

  it('exits when Blender disconnects, and when the parent process dies', async () => {
    const first = await startHost();
    const client = await connect(first.port, first.token);
    await until(() => client.statuses().length > 0);
    client.socket.destroy();
    expect(await first.host.closed).toBe('Blender disconnected');

    const parent = spawn(process.execPath, ['-e', 'setTimeout(() => {}, 60000)']);
    const second = await startHost({ parentPid: parent.pid });
    await connect(second.port, second.token);
    parent.kill();
    expect(await second.host.closed).toBe('The parent process exited');
  });

  it('gives up without a client', async () => {
    const { host } = await startHost({ connectTimeoutMs: 100 });
    expect(await host.closed).toBe('No client connected');
  });
});

const BROWSER = findBrowser();
// A page that draws all the time and records the keys it gets.
const TEST_PAGE = `<!doctype html><title>stream test</title><style>html,body{margin:0;height:100%;overflow:hidden}</style>
<canvas id="c"></canvas><script>
window.__keys = []; window.__clicks = [];
addEventListener('keydown', (e) => __keys.push(e.code + ':' + e.key));
addEventListener('pointerdown', (e) => __clicks.push([e.clientX, e.clientY, e.button]));
const c = document.getElementById('c'); const g = c.getContext('2d'); let n = 0;
(function draw() { c.width = innerWidth; c.height = innerHeight; g.fillStyle = 'hsl(' + (n++ % 360) + ',80%,50%)'; g.fillRect(0, 0, c.width, c.height); requestAnimationFrame(draw); })();
</script>`;

describe.skipIf(!BROWSER)('gamestudio-stream with a real browser', () => {
  let server: Server | null = null;
  let host: ChildProcess | null = null;
  afterEach(async () => {
    if (host && host.exitCode === null) host.kill();
    await new Promise((r) => (server ? server.close(r) : r(undefined)));
  });

  it('streams a page and takes input until stopped', async () => {
    server = createServer((_req, res) => res.writeHead(200, { 'content-type': 'text/html' }).end(TEST_PAGE));
    await new Promise<void>((r) => server!.listen(0, '127.0.0.1', r));
    const url = `http://127.0.0.1:${(server.address() as net.AddressInfo).port}/`;
    host = spawn(process.execPath, [HOST_BIN, '--url', url, '--width', '320', '--height', '180', '--mute', '--devtools', '--parent-pid', String(process.pid)], { stdio: ['ignore', 'pipe', 'pipe'] });
    let log = '';
    host.stderr!.on('data', (d) => (log += d));
    const line: string = await new Promise((resolve) => host!.stdout!.once('data', (d) => resolve(String(d))));
    const { port, token } = JSON.parse(line);
    const client = await connect(port, token);
    await until(() => client.messages.filter((m) => m.type === 'F').length >= 5, 25_000).catch(() => {
      throw new Error(`No frames. Host log:\n${log}`);
    });
    const frame = decodeFrame(client.messages.filter((m) => m.type === 'F').at(-1)!.payload);
    expect(frame).toMatchObject({ width: 320, height: 180, format: FORMAT.JPEG });
    expect(jpegSize(frame.pixels)).toEqual([320, 180]);
    await until(() => client.statuses().some((s) => s.status === 'ready' && s.devtools), 25_000);
    expect(client.statuses().at(-1)).toMatchObject({ audio: false });

    client.send({ type: 'key', kind: 'down', code: 'KeyW', key: 'w' });
    client.send({ type: 'key', kind: 'up', code: 'KeyW', key: 'w' });
    client.send({ type: 'mouse', kind: 'down', x: 160, y: 90, button: 2 });
    client.send({ type: 'mouse', kind: 'up', x: 160, y: 90, button: 2 });
    const puppeteer = (await import('puppeteer-core')).default;
    const browser = await puppeteer.connect({ browserWSEndpoint: client.statuses().at(-1).devtools, defaultViewport: null });
    try {
      const page = (await browser.pages()).find((p) => p.url() === url)!;
      await page.waitForFunction(() => (globalThis as any).__clicks.length > 0, { timeout: 5000 });
      expect(await page.evaluate(() => (globalThis as any).__keys)).toEqual(['KeyW:w']);
      expect(await page.evaluate(() => (globalThis as any).__clicks)).toEqual([[160, 90, 2]]);
    } finally {
      await browser.disconnect();
    }

    client.send({ type: 'stop' });
    await new Promise((r) => (host!.exitCode !== null ? r(undefined) : host!.once('exit', r)));
    expect(host.exitCode).toBe(0);
  }, 60_000);
});
