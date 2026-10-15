// SPDX-License-Identifier: MIT
// Unit tests for the Electron-free parts of the runtime host.
import net from 'node:net';
import { describe, expect, it } from 'vitest';
import { chromiumLuidSwitch, parseArgs, UsageError } from '../src/args.js';
import { FramePump } from '../src/frame-pump.js';
import { sanitizeInput } from '../src/input-messages.js';
import { SurfaceLink } from '../src/surface-link.js';

function fakeTexture(handle = 0x1234n) {
  const ntHandle = Buffer.alloc(8);
  ntHandle.writeBigUInt64LE(handle);
  const texture = {
    released: 0,
    textureInfo: { widgetType: 'frame', pixelFormat: 'bgra', codedSize: { width: 1280, height: 720 }, handle: { ntHandle } },
    release() {
      texture.released++;
    },
  };
  return texture;
}

describe('FramePump', () => {
  it('releases paints at once until enabled', () => {
    const sent = [];
    const pump = new FramePump({ maxInFlight: 3, send: (m) => sent.push(m) });
    const texture = fakeTexture();
    expect(pump.onPaint(texture)).toBe(0);
    expect(texture.released).toBe(1);
    expect(sent).toHaveLength(0);
  });

  it('sends a frame message with the handle as a decimal string', () => {
    const sent = [];
    const pump = new FramePump({ maxInFlight: 3, send: (m) => sent.push(m), now: () => 12.3456 });
    pump.enabled = true;
    const id = pump.onPaint(fakeTexture(0xabcdn));
    expect(sent).toEqual([{ t: 'surface.frame', frameId: id, handle: String(0xabcd), width: 1280, height: 720, format: 'bgra8', ts: 12.346 }]);
  });

  it('keeps textures until released and ignores unknown ids', () => {
    const pump = new FramePump({ maxInFlight: 3, send: () => {} });
    pump.enabled = true;
    const texture = fakeTexture();
    const id = pump.onPaint(texture);
    expect(texture.released).toBe(0);
    expect(pump.release(id)).toBe(true);
    expect(texture.released).toBe(1);
    expect(pump.release(id)).toBe(false);
    expect(pump.release('1')).toBe(false);
  });

  it('revokes the oldest frame when the editor holds too many', () => {
    const sent = [];
    const pump = new FramePump({ maxInFlight: 2, send: (m) => sent.push(m) });
    pump.enabled = true;
    const textures = [fakeTexture(), fakeTexture(), fakeTexture()];
    const ids = textures.map((t) => pump.onPaint(t));
    expect(textures.map((t) => t.released)).toEqual([1, 0, 0]);
    expect(pump.inFlight.size).toBe(2);
    // The revoke goes out before the frame that caused it.
    expect(sent.at(-2)).toEqual({ t: 'surface.revoke', frameIds: [ids[0]] });
    expect(sent.at(-1)).toMatchObject({ t: 'surface.frame', frameId: ids[2] });
    pump.revokeAll();
    expect(sent.at(-1)).toEqual({ t: 'surface.revoke', frameIds: [ids[1], ids[2]] });
    expect(textures.every((t) => t.released === 1)).toBe(true);
  });

  it('skips popup widgets and unknown formats', () => {
    const pump = new FramePump({ maxInFlight: 3, send: () => {} });
    pump.enabled = true;
    const popup = fakeTexture();
    popup.textureInfo.widgetType = 'popup';
    const nv12 = fakeTexture();
    nv12.textureInfo.pixelFormat = 'nv12';
    expect(pump.onPaint(popup)).toBe(0);
    expect(pump.onPaint(nv12)).toBe(0);
    expect(pump.stats.skipped).toBe(2);
  });
});

describe('parseArgs', () => {
  const docked = ['--mode', 'docked', '--surface', '127.0.0.1:5000', '--token', 'ab'.repeat(16), '--url', 'http://localhost:5173/'];

  it('parses docked options and defaults', () => {
    const options = parseArgs(['F:/host', ...docked, '--enable-logging']);
    expect(options).toMatchObject({ mode: 'docked', surface: { host: '127.0.0.1', port: 5000 }, width: 1280, height: 720, dpr: 1, fps: 60, maxInFlight: 3 });
  });

  it('reads options after the "--" separator that launchers put before them', () => {
    expect(parseArgs(['F:/host', '--', ...docked]).surface).toEqual({ host: '127.0.0.1', port: 5000 });
  });

  it('accepts --name=value', () => {
    expect(parseArgs(['--url=file:///C:/game/index.html', '--width=640']).width).toBe(640);
  });

  it('rejects docked mode without a surface, remote URLs and bad values', () => {
    expect(() => parseArgs(['--mode', 'docked', '--url', 'http://localhost/'])).toThrow(UsageError);
    expect(() => parseArgs(['--url', 'https://example.com/'])).toThrow(/loopback/);
    expect(() => parseArgs([...docked, '--surface', '10.0.0.1:5000'])).toThrow(/127.0.0.1/);
    expect(() => parseArgs([...docked, '--fps', '0'])).toThrow(/fps/);
  });

  it('formats the adapter LUID the way Chromium expects', () => {
    expect(chromiumLuidSwitch('00000000000123ab')).toBe('0,74667');
    expect(chromiumLuidSwitch('ffffffff00000001')).toBe('-1,1');
  });
});

describe('SurfaceLink', () => {
  it('says bye before it ends the link on purpose, then sends nothing more', async () => {
    let received = '';
    const ended = new Promise((resolve) => {
      const server = net.createServer((socket) => {
        socket.setEncoding('utf8');
        socket.on('data', (chunk) => (received += chunk));
        socket.on('end', () => {
          server.close();
          resolve(undefined);
        });
      });
      server.listen(0, '127.0.0.1', async () => {
        const address = /** @type {net.AddressInfo} */ (server.address());
        const link = await SurfaceLink.connect('127.0.0.1', address.port);
        link.send({ t: 'surface.state', state: 'running' });
        link.goodbye('the game window was closed');
        link.send({ t: 'surface.state', state: 'loading' });
      });
    });
    await ended;
    expect(received.trim().split('\n').map((line) => JSON.parse(line))).toEqual([
      { t: 'surface.state', state: 'running' },
      { t: 'bye', reason: 'the game window was closed' },
    ]);
  });
});

describe('sanitizeInput', () => {
  it('keeps valid key and pointer messages', () => {
    expect(sanitizeInput({ t: 'input.key', type: 'down', code: 'KeyW', key: 'w', modifiers: ['Shift', 'Bogus'] })).toEqual({
      t: 'input.key', type: 'down', code: 'KeyW', key: 'w', repeat: false, modifiers: ['Shift'],
    });
    expect(sanitizeInput({ t: 'input.pointer', type: 'wheel', x: 10, y: 20, deltaY: 100 })).toMatchObject({ x: 10, y: 20, deltaY: 100, button: 0 });
  });

  it('rejects malformed messages', () => {
    expect(sanitizeInput({ t: 'input.key', type: 'press', code: 'KeyW' })).toBeNull();
    expect(sanitizeInput({ t: 'input.key', type: 'down' })).toBeNull();
    expect(sanitizeInput({ t: 'input.pointer', type: 'drag' })).toBeNull();
    expect(sanitizeInput({ t: 'eval', code: 'alert(1)' })).toBeNull();
  });
});
