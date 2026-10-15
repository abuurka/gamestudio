// Test helpers: temporary projects and a fake Blender bridge that speaks the bridge protocol
// (NDJSON JSON-RPC 2.0 with a hello/token handshake).
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import net from 'node:net';
import os from 'node:os';
import path from 'node:path';

export const TOKEN = '0123456789abcdef0123456789abcdef';

/** A temporary GameStudio project. */
export function makeProject({ name = 'test-game', scenes = ['scenes/main.blend'] } = {}) {
  const root = mkdtempSync(path.join(os.tmpdir(), 'gs_mcp_test_'));
  writeFileSync(path.join(root, 'gamestudio.json'), JSON.stringify({ name, formatVersion: 1, startScene: 'main' }));
  mkdirSync(path.join(root, '.gamestudio'));
  for (const scene of scenes) {
    mkdirSync(path.dirname(path.join(root, scene)), { recursive: true });
    writeFileSync(path.join(root, scene), 'BLENDER');
  }
  return { root, cleanup: () => rmSync(root, { recursive: true, force: true }) };
}

/**
 * A fake bridge. `handlers[method](params)` returns a result or throws {code, message, data}.
 * Records every request in `calls`, and the params of every accepted hello in `hellos`.
 */
export async function startFakeBridge(handlers = {}, { token = TOKEN } = {}) {
  /** @type {{ method: string, params: any }[]} */
  const calls = [];
  /** @type {any[]} */
  const hellos = [];
  const sockets = new Set();
  const server = net.createServer((socket) => {
    sockets.add(socket);
    socket.on('close', () => sockets.delete(socket));
    socket.setEncoding('utf8');
    let authed = false;
    let buffer = '';
    socket.on('data', async (chunk) => {
      buffer += chunk;
      let nl;
      while ((nl = buffer.indexOf('\n')) >= 0) {
        const line = buffer.slice(0, nl);
        buffer = buffer.slice(nl + 1);
        const msg = JSON.parse(line);
        const reply = (body) => socket.write(`${JSON.stringify({ jsonrpc: '2.0', id: msg.id, ...body })}\n`);
        if (!authed) {
          if (msg.method === 'hello' && msg.params?.token === token) {
            authed = true;
            hellos.push(msg.params);
            reply({ result: { bridge: 1, blender: '5.2.2', pid: process.pid, background: true } });
          } else {
            reply({ error: { code: -32001, message: 'Unauthorized: wrong token', data: { hint: 'Read the token' } } });
            socket.end();
          }
          continue;
        }
        calls.push({ method: msg.method, params: msg.params });
        const handler = handlers[msg.method];
        if (!handler) {
          reply({ error: { code: -32601, message: `Unknown bridge method '${msg.method}'.` } });
          continue;
        }
        try {
          reply({ result: await handler(msg.params ?? {}) });
        } catch (error) {
          reply({ error });
        }
      }
    });
  });
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  const port = /** @type {net.AddressInfo} */ (server.address()).port;
  return {
    port,
    token,
    calls,
    hellos,
    handlers,
    /** Writes <root>/.gamestudio/blender.json pointing at this bridge. */
    writeDiscovery(root, extra = {}) {
      writeFileSync(
        path.join(root, '.gamestudio', 'blender.json'),
        JSON.stringify({ pid: process.pid, port, token, blend: path.join(root, 'scenes', 'main.blend'), project: root, background: true, ...extra }),
      );
    },
    dropConnections() {
      for (const s of sockets) s.destroy();
    },
    close: () =>
      new Promise((resolve) => {
        for (const s of sockets) s.destroy();
        server.close(() => resolve(undefined));
      }),
  };
}

/** Calls a tool through the MCP server and returns { data, images, isError }. */
export async function callTool(server, name, args = {}) {
  const response = await server.handle({ jsonrpc: '2.0', id: Math.floor(Math.random() * 1e9), method: 'tools/call', params: { name, arguments: args } });
  const content = response.result.content;
  const text = content.find((c) => c.type === 'text')?.text;
  let data = text;
  try {
    data = JSON.parse(text);
  } catch {
    // plain text (engine_docs)
  }
  return { data, images: content.filter((c) => c.type === 'image'), isError: Boolean(response.result.isError) };
}
