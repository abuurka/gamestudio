import net from 'node:net';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { createHub, isAllowedOrigin } from '../src/hub.js';
import { createLineSplitter } from '../src/ndjson.js';
import type { Hub } from '../types/index.js';
import { connectTcp, connectWs, hello, type TestClient } from './clients';

const TOKEN = '0123456789abcdef0123456789abcdef';
let hub: Hub;
let clients: TestClient[];

beforeEach(async () => {
  hub = await createHub({ editorPort: 0, gamePort: 0, token: TOKEN });
  clients = [];
});
afterEach(async () => {
  for (const c of clients) c.close();
  await hub.close();
});

async function editor(role = 'editor') {
  const c = await connectTcp(hub.editorPort);
  clients.push(c);
  const welcome = await hello(c, role, TOKEN);
  expect(welcome.t).toBe('welcome');
  return { c, peer: welcome.peer as string };
}
async function game() {
  const c = await connectWs(hub.gamePort);
  clients.push(c);
  const welcome = await hello(c, 'game', TOKEN);
  expect(welcome.t).toBe('welcome');
  return { c, peer: welcome.peer as string };
}
const tick = (ms = 50) => new Promise((r) => setTimeout(r, ms));

describe('hub handshake', () => {
  it('welcomes valid peers with their id and the other peers', async () => {
    const g = await game();
    const c = await connectTcp(hub.editorPort);
    clients.push(c);
    const welcome = await hello(c, 'editor', TOKEN);
    expect(welcome).toMatchObject({ t: 'welcome', protocol: 1, peers: [{ peer: g.peer, role: 'game', client: 'test-game' }] });
    expect(welcome.peer).toMatch(/^p\d+$/);
    expect(hub.peers().map((p) => p.role).sort()).toEqual(['editor', 'game']);
  });

  it('rejects a bad token, a missing hello and a wrong protocol', async () => {
    const bad = await connectTcp(hub.editorPort);
    clients.push(bad);
    expect(await hello(bad, 'editor', 'wrong')).toMatchObject({ t: 'error', code: 'auth' });
    await bad.closed;

    const noHello = await connectWs(hub.gamePort);
    clients.push(noHello);
    noHello.send({ t: 'play.start' });
    expect(await noHello.next((m) => m.t === 'error')).toMatchObject({ code: 'auth' });
    await noHello.closed;

    const oldProtocol = await connectTcp(hub.editorPort);
    clients.push(oldProtocol);
    expect(await hello(oldProtocol, 'editor', TOKEN, { protocol: 99 })).toMatchObject({ t: 'error', code: 'protocol' });
    await oldProtocol.closed;
    expect(hub.peers()).toEqual([]);
  });

  it('closes a TCP connection whose first line is not hello, so a web page posting to the port gets nowhere', async () => {
    const g = await game();
    const page = await connectTcp(hub.editorPort);
    clients.push(page);
    // What fetch(port, { method: 'POST', mode: 'no-cors', body }) sends: HTTP headers, then a body with a valid hello.
    const body = `\n${JSON.stringify({ t: 'hello', role: 'editor', token: TOKEN, protocol: 1 })}\n${JSON.stringify({ t: 'play.start' })}\n`;
    page.raw(`POST / HTTP/1.1\r\nHost: 127.0.0.1:${hub.editorPort}\r\nOrigin: http://localhost:5173\r\n` +
      `Content-Type: text/plain;charset=UTF-8\r\nContent-Length: ${Buffer.byteLength(body)}\r\n\r\n${body}`);
    await page.closed;
    expect(page.messages).toEqual([{ t: 'error', code: 'auth', msg: 'The first message must be hello' }]);

    const helloLine = `${JSON.stringify({ t: 'hello', role: 'tool', token: TOKEN, protocol: 1 })}\n`;
    for (const first of ['{not json}', '[1, 2]', '"hello"']) {
      const c = await connectTcp(hub.editorPort);
      clients.push(c);
      c.raw(`${first}\n${helloLine}`);
      await c.closed;
      expect(c.messages.map((m) => m.code), first).toEqual(['auth']);
    }
    await tick();
    expect(hub.peers().map((p) => p.peer)).toEqual([g.peer]);
    expect(g.c.messages.some((m) => m.t === 'play.start')).toBe(false);
  });

  it('lets WebSocket peers be games only: editors and tools connect over TCP', async () => {
    for (const role of ['editor', 'tool']) {
      const page = await connectWs(hub.gamePort, 'http://localhost:5173');
      clients.push(page);
      expect(await hello(page, role, TOKEN)).toMatchObject({ t: 'error', code: 'auth' });
      await page.closed;
    }
    const garbled = await connectWs(hub.gamePort);
    clients.push(garbled);
    garbled.raw('{not json');
    expect(await garbled.next((m) => m.t === 'error')).toMatchObject({ code: 'auth' });
    await garbled.closed;
    expect(hub.peers()).toEqual([]);
    await game();
    await editor('tool');
    expect(hub.peers().map((p) => p.role).sort()).toEqual(['game', 'tool']);
  });

  it('only accepts WebSocket upgrades from localhost origins', async () => {
    await expect(connectWs(hub.gamePort, 'https://evil.example')).rejects.toThrow(/403/);
    const ok = await connectWs(hub.gamePort, 'http://localhost:5173');
    clients.push(ok);
    expect((await hello(ok, 'game', TOKEN)).t).toBe('welcome');
    expect(isAllowedOrigin('http://127.0.0.1:4000')).toBe(true);
    expect(isAllowedOrigin('http://localhost.evil.com')).toBe(false);
    expect(isAllowedOrigin(undefined)).toBe(true); // Non-browser clients still need the token.
  });

  it('broadcasts the peer list when peers join and leave', async () => {
    const e = await editor();
    const g = await game();
    const joined = await e.c.next((m) => m.t === 'peers' && m.peers.length === 1);
    expect(joined.peers).toEqual([{ peer: g.peer, role: 'game', client: 'test-game' }]);
    g.c.close();
    const left = await e.c.next((m) => m.t === 'peers' && m.peers.length === 0);
    expect(left.peers).toEqual([]);
  });

  it('answers ping with pong', async () => {
    const e = await editor();
    e.c.send({ t: 'ping', id: 'k1' });
    expect(await e.c.next((m) => m.t === 'pong')).toEqual({ t: 'pong', re: 'k1' });
  });
});

describe('hub routing', () => {
  it('routes editor → games and game → editors and tools, stamping from', async () => {
    const e = await editor();
    const tool = await editor('tool');
    const g1 = await game();
    const g2 = await game();

    e.c.send({ t: 'play.start' });
    expect(await g1.c.next((m) => m.t === 'play.start')).toEqual({ t: 'play.start', from: e.peer });
    expect(await g2.c.next((m) => m.t === 'play.start')).toEqual({ t: 'play.start', from: e.peer });

    g1.c.send({ t: 'log', level: 'info', msg: 'hi' });
    expect(await e.c.next((m) => m.t === 'log')).toMatchObject({ msg: 'hi', from: g1.peer });
    expect(await tool.c.next((m) => m.t === 'log')).toMatchObject({ msg: 'hi', from: g1.peer });

    tool.c.send({ t: 'tool.list', id: 'tl1' });
    expect(await e.c.next((m) => m.t === 'tool.list')).toMatchObject({ id: 'tl1', from: tool.peer });
    await tick();
    expect(g1.c.messages.some((m) => m.t === 'tool.list')).toBe(false); // Tools only talk to editors.
    expect(e.c.messages.some((m) => m.t === 'play.start')).toBe(false); // Never echoed to the sender.
  });

  it('refuses tool requests from games and editors', async () => {
    const e = await editor();
    const g = await game();

    g.c.send({ t: 'tool.call', id: 'g1', name: 'scene.list', input: {} });
    expect(await g.c.next((m) => m.t === 'error')).toMatchObject({ code: 'forbidden', re: 'g1' });
    g.c.send({ t: 'tool.list', id: 'g2', to: e.peer });
    expect(await g.c.next((m) => m.t === 'error' && m.re === 'g2')).toMatchObject({ code: 'forbidden' });
    e.c.send({ t: 'tool.call', id: 'e1', name: 'scene.list', input: {} });
    expect(await e.c.next((m) => m.t === 'error')).toMatchObject({ code: 'forbidden', re: 'e1' });
    await tick();
    expect(e.c.messages.some((m) => m.t === 'tool.call' || m.t === 'tool.list')).toBe(false);
  });

  it('routes a message with `to` only to that peer', async () => {
    const e = await editor();
    const g1 = await game();
    const g2 = await game();
    e.c.send({ t: 'select', ids: ['gs_1'], to: g2.peer });
    expect(await g2.c.next((m) => m.t === 'select')).toMatchObject({ to: g2.peer, from: e.peer });
    await tick();
    expect(g1.c.messages.some((m) => m.t === 'select')).toBe(false);
    e.c.send({ t: 'select', ids: [], to: 'p999', id: 'x' });
    expect(await e.c.next((m) => m.t === 'error')).toMatchObject({ code: 'no_peer', re: 'x' });
  });

  it('passes the Scene view messages through untouched: to one game, or to every game', async () => {
    const e = await editor();
    const g1 = await game();
    const g2 = await game();
    const camera = { t: 'view.camera', view: [...Array(16).keys()], projection: [...Array(16).keys()].map((i) => i / 2), ortho: false, near: 0.1, far: 100 };
    e.c.send({ t: 'view.mode', mode: 'scene', animate: true, to: g2.peer });
    e.c.send({ ...camera, to: g2.peer });
    e.c.send({ t: 'material.values', material: 'GS_Sand', values: { tile: 2, wet: 0.75 }, scene: 'main' });
    expect(await g2.c.next((m) => m.t === 'view.mode')).toEqual({ t: 'view.mode', mode: 'scene', animate: true, to: g2.peer, from: e.peer });
    expect(await g2.c.next((m) => m.t === 'view.camera')).toEqual({ ...camera, to: g2.peer, from: e.peer });
    for (const g of [g1, g2]) {
      expect(await g.c.next((m) => m.t === 'material.values')).toEqual({ t: 'material.values', material: 'GS_Sand', values: { tile: 2, wet: 0.75 }, scene: 'main', from: e.peer });
    }
    await tick();
    expect(g1.c.messages.some((m) => m.t === 'view.mode' || m.t === 'view.camera')).toBe(false);
  });

  it('routes responses (`re`) back to the requester only', async () => {
    const e1 = await editor();
    const e2 = await editor();
    const tool = await editor('tool');
    const g = await game();

    e1.c.send({ t: 'state.request', id: 'req-42', filter: { component: 'Spin' } });
    const request = await g.c.next((m) => m.t === 'state.request');
    expect(request).toMatchObject({ id: 'req-42', from: e1.peer });

    g.c.send({ t: 'state.response', re: 'req-42', entities: [{ id: 'gs_1' }] });
    expect(await e1.c.next((m) => m.t === 'state.response')).toMatchObject({ re: 'req-42', from: g.peer, entities: [{ id: 'gs_1' }] });
    await tick();
    expect(e2.c.messages.some((m) => m.t === 'state.response')).toBe(false);
    expect(tool.c.messages.some((m) => m.t === 'state.response')).toBe(false);

    // Editor answering a tool's call goes back to the tool, not to games.
    tool.c.send({ t: 'tool.call', id: 'call-1', name: 'scene.list', input: {} });
    await e1.c.next((m) => m.t === 'tool.call');
    e1.c.send({ t: 'tool.result', re: 'call-1', ok: true, content: [] });
    expect(await tool.c.next((m) => m.t === 'tool.result')).toMatchObject({ re: 'call-1', from: e1.peer });
    await tick();
    expect(g.c.messages.some((m) => m.t === 'tool.result')).toBe(false);
  });

  it('keeps request ids apart per peer: only a peer a request went to can answer it', async () => {
    const e1 = await editor();
    const e2 = await editor();
    const tool = await editor('tool');
    const g = await game();

    // The same id from two requesters: e1 asks the games, the tool asks the editors.
    e1.c.send({ t: 'state.request', id: 'same' });
    tool.c.send({ t: 'tool.call', id: 'same', name: 'scene.list', input: {} });
    await g.c.next((m) => m.t === 'state.request');
    await e1.c.next((m) => m.t === 'tool.call');
    g.c.send({ t: 'state.response', re: 'same', entities: [] });
    e1.c.send({ t: 'tool.result', re: 'same', ok: true, content: [] });
    expect(await e1.c.next((m) => m.t === 'state.response')).toMatchObject({ from: g.peer });
    expect(await tool.c.next((m) => m.t === 'tool.result')).toMatchObject({ from: e1.peer });

    // A reply from a peer the request never went to is routed like any other message from it.
    e1.c.send({ t: 'state.request', id: 'req-9' });
    await g.c.next((m) => m.t === 'state.request' && m.id === 'req-9');
    e2.c.send({ t: 'state.response', re: 'req-9', entities: [{ id: 'forged' }] });
    await g.c.next((m) => m.t === 'state.response' && m.from === e2.peer); // editor → games
    g.c.send({ t: 'state.response', re: 'req-9', entities: [{ id: 'gs_1' }] });
    expect(await e1.c.next((m) => m.t === 'state.response' && m.re === 'req-9')).toMatchObject({ from: g.peer, entities: [{ id: 'gs_1' }] });
    await tick();
    expect(e1.c.messages.filter((m) => m.t === 'state.response' && m.re === 'req-9')).toHaveLength(1);
    expect(tool.c.messages.some((m) => m.t === 'state.response')).toBe(false);
    expect(e2.c.messages.some((m) => m.t === 'state.response' || m.t === 'tool.result')).toBe(false);
  });

  it('sends the registry to editors on connect and on change', async () => {
    const e = await editor();
    const g = await game();
    hub.setRegistry({ format: 'gamestudio.registry', components: [{ name: 'A' }] });
    expect(await e.c.next((m) => m.t === 'registry')).toMatchObject({ registry: { components: [{ name: 'A' }] } });
    const later = await editor();
    expect(await later.c.next((m) => m.t === 'registry')).toMatchObject({ registry: { format: 'gamestudio.registry' } });
    await tick();
    expect(g.c.messages.some((m) => m.t === 'registry')).toBe(false);
  });
});

describe('NDJSON framing', () => {
  it('handles messages split across chunks, joined in one chunk, CRLF and bad lines', async () => {
    const e = await editor();
    const g = await game();
    const line = JSON.stringify({ t: 'entity.transform', items: [{ id: 'gs_1', p: [1, 2, 3] }], note: 'héllo ✓' });
    // Split mid-way, including inside a multi-byte character.
    const bytes = Buffer.from(`${line}\n`);
    const cut = bytes.indexOf(Buffer.from('✓')) + 1;
    const socket = net.connect(hub.editorPort, '127.0.0.1');
    await new Promise((r) => socket.once('connect', r));
    const replies: string[] = [];
    socket.setEncoding('utf8');
    socket.on('data', createLineSplitter((l) => replies.push(l)));
    socket.write(`${JSON.stringify({ t: 'hello', role: 'editor', token: TOKEN, protocol: 1 })}\n`);
    await tick();
    socket.write(bytes.subarray(0, 10));
    await tick(20);
    socket.write(bytes.subarray(10, cut));
    await tick(20);
    socket.write(bytes.subarray(cut));
    const first = await g.c.next((m) => m.t === 'entity.transform');
    expect(first).toMatchObject({ items: [{ id: 'gs_1', p: [1, 2, 3] }], note: 'héllo ✓' });

    // Two messages in one write, the second with CRLF, plus blank lines.
    socket.write(`${JSON.stringify({ t: 'play.pause', paused: true })}\n\n${JSON.stringify({ t: 'play.step', frames: 3 })}\r\n`);
    expect(await g.c.next((m) => m.t === 'play.pause')).toMatchObject({ paused: true });
    expect(await g.c.next((m) => m.t === 'play.step')).toMatchObject({ frames: 3 });

    socket.write('{not json}\n');
    await tick();
    expect(replies.map((l) => JSON.parse(l)).some((m) => m.t === 'error' && m.code === 'parse')).toBe(true);
    socket.destroy();
    void e;
  });

  it('splits lines independently of chunk boundaries', () => {
    const lines: string[] = [];
    const feed = createLineSplitter((l) => lines.push(l));
    feed('{"a":1}\n{"b"');
    feed(':2}\r\n\n{"c":3');
    expect(lines).toEqual(['{"a":1}', '{"b":2}']);
    feed('}\n');
    expect(lines).toEqual(['{"a":1}', '{"b":2}', '{"c":3}']);
  });
});

describe('ports', () => {
  it('falls back to the next free port when one is busy', async () => {
    const blocker = net.createServer();
    await new Promise<void>((r) => blocker.listen(0, '127.0.0.1', () => r()));
    const busy = (blocker.address() as net.AddressInfo).port;
    const other = await createHub({ editorPort: busy, gamePort: 0, token: TOKEN });
    try {
      expect(other.editorPort).toBeGreaterThan(busy);
      expect(other.editorPort).toBeLessThanOrEqual(busy + 10);
    } finally {
      await other.close();
      blocker.close();
    }
  });
});
