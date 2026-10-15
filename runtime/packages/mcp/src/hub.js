// @ts-check
// SPDX-License-Identifier: MIT
// Connection to the dev server's live-link hub (docs/SPEC.md §5) as a "tool" peer. Games
// broadcast their status, logs and errors to tools, so this client keeps the latest game states
// and a log buffer. Messages for games are addressed with `to` (tool messages without `to`
// only reach editors).

import net from 'node:net';
import { readDevSession } from './discovery.js';
import { ToolError, lineSplitter } from './util.js';

const LOG_LIMIT = 2000;
const RECONNECT_MS = 2000;

/**
 * @typedef {{ peer: string, role: string, client?: string }} PeerInfo
 * @typedef {{ peer: string, client: string, state: string | null, scene: string | null, rev: number | null,
 *   readyAt: number | null, stats: Record<string, unknown> | null, statusAt: number | null,
 *   view?: 'scene' | 'game' | null }} GameInfo
 *   `view`: the game's view mode from its game.status (null until an engine with scene mode reports it).
 * @typedef {{ seq: number, time: string, level: string, msg: string, from?: string, source?: string,
 *   file?: string, line?: number, col?: number, stack?: string, kind?: string }} LogEntry
 */

export class HubClient {
  /** @param {{ root: string | null, log?: (line: string) => void, client?: string }} options */
  constructor({ root, log = () => {}, client = 'gamestudio-mcp' }) {
    this.root = root;
    this.log = log;
    this.clientName = client;
    /** @type {net.Socket | null} */
    this.socket = null;
    this.connected = false;
    /** @type {string | null} */
    this.peerId = null;
    /** @type {PeerInfo[]} */
    this.peers = [];
    /** @type {Map<string, GameInfo>} */
    this.games = new Map();
    /** @type {LogEntry[]} */
    this.logs = [];
    this.seq = 0;
    /** @type {{ token: string, editorPort: number, url: string, pid: number } | null} */
    this.session = null;
    this.nextId = 1;
    /** @type {Map<string, { resolve: (m: any) => void, timer: NodeJS.Timeout }>} */
    this.pending = new Map();
    /** @type {Set<(msg: any) => void>} */
    this.listeners = new Set();
    /** @type {Promise<void> | null} */
    this.connecting = null;
    /** @type {NodeJS.Timeout | null} */
    this.timer = null;
  }

  /** Keep trying to connect in the background, so game logs are captured from the start. */
  watch() {
    if (this.timer || !this.root) return;
    this.timer = setInterval(() => {
      if (!this.connected && this.root && readDevSession(this.root)) this.ensure().catch(() => {});
    }, RECONNECT_MS);
    this.timer.unref();
    if (readDevSession(this.root)) this.ensure().catch(() => {});
  }

  /** Connect if not connected. Throws a ToolError when no dev server runs. */
  async ensure() {
    if (this.connected) return;
    this.connecting ??= this.#connect().finally(() => {
      this.connecting = null;
    });
    return this.connecting;
  }

  async #connect() {
    if (!this.root) throw new ToolError('No GameStudio project, so no dev server to talk to.', { code: 'no_project' });
    const session = readDevSession(this.root);
    if (!session) {
      throw new ToolError('The dev server is not running.', {
        code: 'devserver_not_running',
        hint: 'play starts it through Blender.',
      });
    }
    await new Promise((resolve, reject) => {
      const socket = net.connect({ host: '127.0.0.1', port: session.editorPort });
      let welcomed = false;
      const fail = (/** @type {string} */ why) => {
        socket.destroy();
        reject(new ToolError(`Could not connect to the dev server's live-link hub on port ${session.editorPort}: ${why}.`, { code: 'hub_unreachable', hint: 'The dev server may be restarting; retry in a few seconds.' }));
      };
      const timer = setTimeout(() => fail('timed out'), 5000);
      socket.setEncoding('utf8');
      socket.setNoDelay(true);
      socket.on('error', (error) => {
        if (!welcomed) {
          clearTimeout(timer);
          fail(/** @type {NodeJS.ErrnoException} */ (error).code ?? error.message);
        }
      });
      socket.on('connect', () => {
        socket.write(`${JSON.stringify({ t: 'hello', role: 'tool', token: session.token, protocol: 1, client: this.clientName })}\n`);
      });
      socket.on('close', () => {
        if (this.socket === socket) this.#onClose();
      });
      const onChunk = lineSplitter((line) => {
        let msg;
        try {
          msg = JSON.parse(line);
        } catch {
          return;
        }
        if (!welcomed) {
          clearTimeout(timer);
          if (msg.t !== 'welcome') {
            fail(`${msg.code ?? 'refused'}: ${msg.msg ?? JSON.stringify(msg)}`);
            return;
          }
          welcomed = true;
          this.socket = socket;
          this.connected = true;
          this.session = session;
          this.peerId = msg.peer;
          this.#setPeers(msg.peers ?? []);
          this.log(`hub connected as ${msg.peer} (${this.gamePeers().length} game(s))`);
          resolve(undefined);
          return;
        }
        this.#onMessage(msg);
      });
      socket.on('data', (chunk) => onChunk(String(chunk)));
    });
  }

  #onClose() {
    this.connected = false;
    this.socket = null;
    this.peers = [];
    for (const [id, waiter] of this.pending) {
      clearTimeout(waiter.timer);
      waiter.resolve(null);
      this.pending.delete(id);
    }
    this.log('hub connection closed');
  }

  /** @param {PeerInfo[]} peers */
  #setPeers(peers) {
    this.peers = peers;
    const ids = new Set(peers.map((p) => p.peer));
    for (const peer of peers) {
      if (peer.role !== 'game') continue;
      if (!this.games.has(peer.peer)) {
        this.games.set(peer.peer, { peer: peer.peer, client: peer.client ?? '', state: null, scene: null, rev: null, readyAt: null, stats: null, statusAt: null });
      }
    }
    for (const id of this.games.keys()) if (!ids.has(id)) this.games.delete(id);
  }

  /** @param {any} msg */
  #onMessage(msg) {
    const from = typeof msg.from === 'string' ? msg.from : undefined;
    let game = from ? this.games.get(from) : undefined;
    if (!game && from && (msg.t === 'game.status' || msg.t === 'game.ready' || msg.t === 'stats')) {
      game = { peer: from, client: '', state: null, scene: null, rev: null, readyAt: null, stats: null, statusAt: null };
      this.games.set(from, game);
    }
    switch (msg.t) {
      case 'peers':
        this.#setPeers(msg.peers ?? []);
        break;
      case 'ping':
        this.send({ t: 'pong', ...(msg.id !== undefined && { re: msg.id }) });
        break;
      case 'game.status':
        if (game) {
          game.state = msg.state ?? null;
          if (msg.view === 'scene' || msg.view === 'game') game.view = msg.view;
          game.statusAt = Date.now();
        }
        break;
      case 'game.ready':
        if (game) {
          game.scene = msg.scene ?? null;
          game.rev = typeof msg.rev === 'number' ? msg.rev : null;
          game.readyAt = Date.now();
        }
        break;
      case 'stats':
        if (game) game.stats = { fps: msg.fps, frameMs: msg.frameMs, entities: msg.entities, bodies: msg.bodies };
        break;
      case 'log':
      case 'error':
        this.#addLog(msg, from);
        break;
      default:
        break;
    }
    if (msg.re !== undefined) {
      const waiter = this.pending.get(String(msg.re));
      if (waiter) {
        clearTimeout(waiter.timer);
        this.pending.delete(String(msg.re));
        waiter.resolve(msg);
      }
    }
    for (const fn of this.listeners) fn(msg);
  }

  /** @param {any} msg @param {string | undefined} from */
  #addLog(msg, from) {
    /** @type {LogEntry} */
    const entry = {
      seq: ++this.seq,
      time: new Date().toISOString(),
      level: msg.t === 'error' ? 'error' : typeof msg.level === 'string' ? msg.level : 'info',
      msg: String(msg.msg ?? ''),
    };
    if (from) entry.from = from;
    for (const key of /** @type {const} */ (['source', 'file', 'line', 'col', 'stack', 'kind'])) {
      if (msg[key] !== undefined && msg[key] !== null) /** @type {any} */ (entry)[key] = key === 'stack' ? String(msg[key]).slice(0, 2000) : msg[key];
    }
    this.logs.push(entry);
    if (this.logs.length > LOG_LIMIT) this.logs.splice(0, this.logs.length - LOG_LIMIT);
  }

  /** @param {object} msg */
  send(msg) {
    if (!this.socket || !this.connected) return false;
    this.socket.write(`${JSON.stringify(msg)}\n`);
    return true;
  }

  /**
   * Sends a request and resolves with the response (`re` = our id), or null on timeout.
   * @param {Record<string, unknown>} msg
   * @param {number} [timeoutMs]
   * @returns {Promise<any>}
   */
  request(msg, timeoutMs = 5000) {
    const id = `mcp-${process.pid}-${this.nextId++}`;
    return new Promise((resolve) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        resolve(null);
      }, timeoutMs);
      this.pending.set(id, { resolve, timer });
      if (!this.send({ ...msg, id })) {
        clearTimeout(timer);
        this.pending.delete(id);
        resolve(null);
      }
    });
  }

  /** Connected game peers with their latest status. @returns {GameInfo[]} */
  gamePeers() {
    return this.peers
      .filter((p) => p.role === 'game')
      .map((p) => this.games.get(p.peer) ?? { peer: p.peer, client: p.client ?? '', state: null, scene: null, rev: null, readyAt: null, stats: null, statusAt: null });
  }

  /**
   * Resolves when `predicate()` is true (checked on every message and every 100 ms).
   * @param {() => boolean} predicate
   * @param {number} timeoutMs
   */
  waitFor(predicate, timeoutMs) {
    return new Promise((resolve) => {
      if (predicate()) {
        resolve(true);
        return;
      }
      const check = () => {
        if (predicate()) done(true);
      };
      const done = (/** @type {boolean} */ value) => {
        clearInterval(interval);
        clearTimeout(timer);
        this.listeners.delete(check);
        resolve(value);
      };
      const interval = setInterval(check, 100);
      const timer = setTimeout(() => done(predicate()), timeoutMs);
      this.listeners.add(check);
    });
  }

  close() {
    if (this.timer) clearInterval(this.timer);
    this.timer = null;
    this.socket?.destroy();
    this.socket = null;
    this.connected = false;
  }
}
