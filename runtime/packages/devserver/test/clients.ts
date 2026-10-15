// Test clients for the hub: an NDJSON TCP client (editor/tool) and a WebSocket client (game).
import net from 'node:net';
import WebSocket from 'ws';
import { createLineSplitter } from '../src/ndjson.js';

export type Msg = { t: string; [key: string]: any };

export interface TestClient {
  messages: Msg[];
  send(message: object): void;
  /** Sends raw text (TCP only). */
  raw(text: string): void;
  /** Resolves with the first message (already received or future) matching `predicate`. */
  next(predicate: (m: Msg) => boolean, timeoutMs?: number): Promise<Msg>;
  closed: Promise<void>;
  close(): void;
}

/** `sendRaw` writes text as-is; `frame` turns one JSON message into what goes on the wire. */
function makeClient(sendRaw: (text: string) => void, frame: (json: string) => string, close: () => void, closed: Promise<void>) {
  const messages: Msg[] = [];
  const waiters: Array<{ predicate: (m: Msg) => boolean; resolve: (m: Msg) => void }> = [];
  const consumed = new Set<Msg>();
  const deliver = (m: Msg) => {
    messages.push(m);
    const index = waiters.findIndex((w) => w.predicate(m));
    if (index >= 0) {
      consumed.add(m);
      waiters.splice(index, 1)[0]!.resolve(m);
    }
  };
  const client: TestClient = {
    messages,
    send: (message) => sendRaw(frame(JSON.stringify(message))),
    raw: (text) => sendRaw(text),
    next(predicate, timeoutMs = 3000) {
      const existing = messages.find((m) => !consumed.has(m) && predicate(m));
      if (existing) {
        consumed.add(existing);
        return Promise.resolve(existing);
      }
      return new Promise((resolve, reject) => {
        const timer = setTimeout(() => reject(new Error(`Timed out waiting; got ${JSON.stringify(messages)}`)), timeoutMs);
        waiters.push({ predicate, resolve: (m) => (clearTimeout(timer), resolve(m)) });
      });
    },
    closed,
    close,
  };
  return { client, deliver };
}

export function connectTcp(port: number): Promise<TestClient> {
  return new Promise((resolve, reject) => {
    const socket = net.connect(port, '127.0.0.1');
    const closed = new Promise<void>((r) => socket.on('close', () => r()));
    const { client, deliver } = makeClient((text) => socket.write(text), (json) => `${json}\n`, () => socket.destroy(), closed);
    socket.setEncoding('utf8');
    socket.on('data', createLineSplitter((line) => deliver(JSON.parse(line))));
    socket.once('connect', () => resolve(client));
    socket.once('error', reject);
  });
}

export function connectWs(port: number, origin?: string): Promise<TestClient> {
  return new Promise((resolve, reject) => {
    const ws = new WebSocket(`ws://127.0.0.1:${port}`, origin ? { origin } : {});
    const closed = new Promise<void>((r) => ws.on('close', () => r()));
    const { client, deliver } = makeClient((text) => ws.send(text), (json) => json, () => ws.close(), closed);
    ws.on('message', (data) => deliver(JSON.parse(String(data))));
    ws.once('open', () => resolve(client));
    ws.once('error', reject);
    ws.once('unexpected-response', (_req, res) => reject(new Error(`HTTP ${res.statusCode}`)));
  });
}

export async function hello(client: TestClient, role: string, token: string, extra: object = {}): Promise<Msg> {
  client.send({ t: 'hello', role, token, protocol: 1, client: `test-${role}`, ...extra });
  return client.next((m) => m.t === 'welcome' || m.t === 'error');
}
