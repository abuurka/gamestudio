// @ts-check
// Live-link hub (docs/SPEC.md §5): TCP NDJSON for editors/tools, WebSocket for
// games, both bound to 127.0.0.1. Authenticates peers, then routes messages.
// A connection must start with hello: anything else closes it, so a web page that posts
// to the TCP port gets no further than its HTTP request line. WebSocket peers can be web
// pages, so they may only be games.

import { timingSafeEqual } from 'node:crypto';
import http from 'node:http';
import net from 'node:net';
import { WebSocketServer } from 'ws';
import { createLineSplitter, encodeLine } from './ndjson.js';
import { listenWithFallback } from './ports.js';

/** @typedef {import('../types/index.js').HubOptions} HubOptions */
/** @typedef {import('../types/index.js').Hub} Hub */
/** @typedef {import('../types/index.js').HubMessage} HubMessage */
/** @typedef {import('../types/index.js').PeerInfo} PeerInfo */
/** @typedef {import('../types/index.js').PeerRole} PeerRole */

/**
 * @typedef {object} Peer
 * @property {string} id
 * @property {PeerRole | null} role  null until authenticated
 * @property {string} client
 * @property {'tcp' | 'ws'} transport
 * @property {(message: object) => void} send
 * @property {() => void} close
 * @property {boolean} rejected  set once turned away: nothing it sends after that is read
 * @property {ReturnType<typeof setTimeout> | null} helloTimer
 * @property {Map<string, string>} asked  ids of requests routed to this peer → requesting peer id (for `re` routing)
 */

export const PROTOCOL = 1;
const ROLES = new Set(['editor', 'game', 'tool']);
const HELLO_TIMEOUT_MS = 5000;
const MAX_PENDING_REQUESTS = 2000;
const MAX_MESSAGE_BYTES = 16 * 1024 * 1024;
const LOCAL_HOSTS = new Set(['localhost', '127.0.0.1', '[::1]', '::1']);

/** @type {import('../types/index.js').HubLogger} */
const silentLogger = { info() {}, warn() {}, error() {} };

/**
 * Browsers always send Origin on WebSocket upgrades; only local pages may connect.
 * Non-browser clients (no Origin) are allowed; they still need the token.
 * @param {string | undefined} origin
 */
export function isAllowedOrigin(origin) {
  if (origin === undefined) return true;
  try {
    const url = new URL(origin);
    return (url.protocol === 'http:' || url.protocol === 'https:') && LOCAL_HOSTS.has(url.hostname);
  } catch {
    return false;
  }
}

/**
 * @param {unknown} given
 * @param {string} expected
 */
function tokenMatches(given, expected) {
  if (typeof given !== 'string') return false;
  const a = Buffer.from(given);
  const b = Buffer.from(expected);
  return a.length === b.length && timingSafeEqual(a, b);
}

/**
 * Starts the hub. Resolves once both servers listen.
 * @param {HubOptions} options
 * @returns {Promise<Hub>}
 */
export async function createHub(options) {
  if (!options || typeof options.token !== 'string' || options.token === '') throw new Error('createHub: token is required');
  const { token, host = '127.0.0.1', logger = silentLogger, transform } = options;

  let nextPeerNumber = 1;
  /** @type {Map<string, Peer>} authenticated peers */
  const peers = new Map();
  /** @type {Set<Peer>} every open connection */
  const connections = new Set();
  /** @type {unknown} */
  let registry = null;
  const listeners = {
    /** @type {Set<(message: HubMessage, from: PeerInfo) => void>} */ message: new Set(),
    /** @type {Set<(peer: PeerInfo) => void>} */ connect: new Set(),
    /** @type {Set<(peer: PeerInfo) => void>} */ disconnect: new Set(),
  };

  /** @param {Peer} peer @returns {PeerInfo} */
  const info = (peer) => ({ peer: peer.id, role: /** @type {PeerRole} */ (peer.role), client: peer.client });
  /** @param {Peer | null} except */
  const peerList = (except) => [...peers.values()].filter((p) => p !== except).map(info);

  /**
   * @param {'tcp' | 'ws'} transport
   * @param {(message: object) => void} send
   * @param {() => void} close
   * @returns {Peer}
   */
  function addConnection(transport, send, close) {
    /** @type {Peer} */
    const peer = { id: `p${nextPeerNumber++}`, role: null, client: '', transport, send, close, rejected: false, helloTimer: null, asked: new Map() };
    peer.helloTimer = setTimeout(() => reject(peer, 'auth', 'No hello received in time'), HELLO_TIMEOUT_MS);
    peer.helloTimer.unref?.();
    connections.add(peer);
    return peer;
  }

  /** @param {Peer} peer @param {'auth' | 'protocol'} code @param {string} msg */
  function reject(peer, code, msg) {
    if (peer.rejected) return;
    peer.rejected = true; // the rest of its chunk, or frames still arriving, are ignored
    logger.warn(`[gamestudio] link: rejected ${peer.id}: ${msg}`);
    peer.send({ t: 'error', code, msg });
    peer.close();
  }

  /**
   * Answers input the hub cannot read. Before hello it closes the connection instead.
   * @param {Peer} peer @param {string} code @param {string} msg
   */
  function badInput(peer, code, msg) {
    if (peer.role === null) reject(peer, 'auth', 'The first message must be hello');
    else peer.send({ t: 'error', code, msg });
  }

  /** @param {Peer} peer */
  function dropConnection(peer) {
    if (peer.helloTimer) clearTimeout(peer.helloTimer);
    connections.delete(peer);
    if (!peers.delete(peer.id)) return;
    logger.info(`[gamestudio] link: ${peer.role} ${peer.id} disconnected`);
    broadcastPeers();
    for (const fn of listeners.disconnect) fn(info(peer));
  }

  function broadcastPeers() {
    for (const peer of peers.values()) peer.send({ t: 'peers', peers: peerList(peer) });
  }

  /** @param {Peer} peer @param {unknown} raw */
  function handleMessage(peer, raw) {
    if (!raw || typeof raw !== 'object' || Array.isArray(raw) || typeof (/** @type {any} */ (raw).t) !== 'string') {
      badInput(peer, 'bad_message', 'Every message must be a JSON object with a string "t"');
      return;
    }
    const message = /** @type {HubMessage} */ (raw);
    if (peer.role === null) {
      handleHello(peer, message);
      return;
    }
    switch (message.t) {
      case 'ping':
        peer.send(message.id !== undefined ? { t: 'pong', re: message.id } : { t: 'pong' });
        return;
      case 'pong':
      case 'hello':
        return;
      case 'tool.list':
      case 'tool.call':
        // Blender's assistant tools answer these. A game holds the hub token (its page gets it), so
        // only tool peers, which connect over TCP, may ask.
        if (peer.role !== 'tool') {
          peer.send({ t: 'error', code: 'forbidden', msg: `Only tools send ${message.t}`, ...(message.id !== undefined && { re: message.id }) });
          return;
        }
        route(peer, message);
        return;
      default:
        route(peer, message);
    }
  }

  /** @param {Peer} peer @param {HubMessage} message */
  function handleHello(peer, message) {
    if (message.t !== 'hello') return reject(peer, 'auth', 'The first message must be hello');
    if (!tokenMatches(message.token, token)) return reject(peer, 'auth', 'Invalid token');
    if (message.protocol !== PROTOCOL) return reject(peer, 'protocol', `Unsupported protocol ${String(message.protocol)}; hub speaks ${PROTOCOL}`);
    if (typeof message.role !== 'string' || !ROLES.has(message.role)) {
      return reject(peer, 'protocol', `Unknown role ${String(message.role)}`);
    }
    // Any web page on this machine can open a WebSocket here, so editors and tools must use TCP.
    if (peer.transport === 'ws' && message.role !== 'game') {
      return reject(peer, 'auth', `Only games connect over WebSocket; a ${message.role} connects over TCP`);
    }
    if (peer.helloTimer) clearTimeout(peer.helloTimer);
    peer.helloTimer = null;
    peer.role = /** @type {PeerRole} */ (message.role);
    peer.client = typeof message.client === 'string' ? message.client : '';
    peers.set(peer.id, peer);
    peer.send({ t: 'welcome', protocol: PROTOCOL, peer: peer.id, peers: peerList(peer) });
    if (registry !== null && peer.role === 'editor') peer.send({ t: 'registry', registry });
    logger.info(`[gamestudio] link: ${peer.role} ${peer.id} connected${peer.client ? ` (${peer.client})` : ''}`);
    broadcastPeers();
    for (const fn of listeners.connect) fn(info(peer));
  }

  /**
   * Routing rules (SPEC §5.3): `to` wins; responses (`re`) go back to the requester;
   * otherwise editor→games, game→editors+tools, tool→editors. Only a peer that a request
   * went to can answer it, so one peer cannot take another's reply.
   * @param {Peer} from @param {HubMessage} message
   */
  function route(from, message) {
    /** @type {HubMessage | null | undefined} */
    let out = { ...message, from: from.id };
    const fromInfo = info(from);
    if (transform) out = transform(out, fromInfo) ?? null;
    if (!out) return;
    for (const fn of listeners.message) fn(out, fromInfo);

    if (typeof message.to === 'string') {
      const target = peers.get(message.to);
      if (target) target.send(out);
      else from.send({ t: 'error', code: 'no_peer', msg: `No peer ${message.to}`, ...(message.id !== undefined && { re: message.id }) });
      return;
    }
    if (message.re !== undefined) {
      const key = String(message.re);
      const requester = from.asked.get(key);
      from.asked.delete(key);
      const target = requester ? peers.get(requester) : undefined;
      if (target) {
        target.send(out);
        return;
      }
    }
    const request = message.id !== undefined && message.re === undefined ? String(message.id) : null;
    /** @type {Set<string>} */
    const roles = from.role === 'editor' ? new Set(['game']) : from.role === 'game' ? new Set(['editor', 'tool']) : new Set(['editor']);
    for (const peer of peers.values()) {
      if (peer === from || !roles.has(/** @type {string} */ (peer.role))) continue;
      if (request !== null) {
        peer.asked.set(request, from.id);
        if (peer.asked.size > MAX_PENDING_REQUESTS) peer.asked.delete(/** @type {string} */ (peer.asked.keys().next().value));
      }
      peer.send(out);
    }
  }

  // --- Editors and tools: TCP, NDJSON ------------------------------------------
  /** @type {Set<net.Socket>} */
  const tcpSockets = new Set();
  const tcpServer = net.createServer((socket) => {
    tcpSockets.add(socket);
    socket.setNoDelay(true);
    socket.setEncoding('utf8'); // Decodes multi-byte characters split across chunks.
    const peer = addConnection(
      'tcp',
      (message) => {
        if (!socket.destroyed && socket.writable) socket.write(encodeLine(message));
      },
      () => socket.end(),
    );
    const onChunk = createLineSplitter(
      (line) => {
        if (peer.rejected) return;
        let parsed;
        try {
          parsed = JSON.parse(line);
        } catch {
          badInput(peer, 'parse', 'Invalid JSON line'); // e.g. `POST / HTTP/1.1` from a web page
          return;
        }
        handleMessage(peer, parsed);
      },
      {
        maxLineLength: MAX_MESSAGE_BYTES,
        onOverflow: () => {
          peer.send({ t: 'error', code: 'too_large', msg: 'Message too large' });
          socket.destroy();
        },
      },
    );
    socket.on('data', (chunk) => onChunk(String(chunk)));
    socket.on('close', () => {
      tcpSockets.delete(socket);
      dropConnection(peer);
    });
    socket.on('error', () => {}); // 'close' follows.
  });

  // --- Games: WebSocket ----------------------------------------------------------
  const wss = new WebSocketServer({ noServer: true, maxPayload: MAX_MESSAGE_BYTES });
  const httpServer = http.createServer((_req, res) => {
    res.writeHead(426, { 'Content-Type': 'text/plain' });
    res.end('GameStudio live-link hub: WebSocket connections only.\n');
  });
  httpServer.on('upgrade', (req, socket, head) => {
    if (!isAllowedOrigin(req.headers.origin)) {
      logger.warn(`[gamestudio] link: rejected WebSocket from origin ${req.headers.origin}`);
      socket.end('HTTP/1.1 403 Forbidden\r\nConnection: close\r\nContent-Length: 0\r\n\r\n');
      return;
    }
    wss.handleUpgrade(req, socket, head, (ws) => {
      const peer = addConnection(
        'ws',
        (message) => {
          if (ws.readyState === ws.OPEN) ws.send(JSON.stringify(message));
        },
        () => ws.close(1008),
      );
      ws.on('message', (data, isBinary) => {
        if (peer.rejected) return;
        if (isBinary) {
          badInput(peer, 'bad_message', 'Binary frames are not supported');
          return;
        }
        let parsed;
        try {
          parsed = JSON.parse(data.toString());
        } catch {
          badInput(peer, 'parse', 'Invalid JSON');
          return;
        }
        handleMessage(peer, parsed);
      });
      ws.on('close', () => dropConnection(peer));
      ws.on('error', () => {});
    });
  });

  let editorPort;
  let gamePort;
  try {
    editorPort = await listenWithFallback(tcpServer, options.editorPort ?? 7710, host);
    gamePort = await listenWithFallback(httpServer, options.gamePort ?? 7711, host);
  } catch (error) {
    tcpServer.close();
    httpServer.close();
    throw error;
  }

  /** @type {Hub} */
  const hub = {
    editorPort,
    gamePort,
    token,
    peers: () => peerList(null),
    setRegistry(value) {
      registry = value;
      for (const peer of peers.values()) if (peer.role === 'editor') peer.send({ t: 'registry', registry });
    },
    send(peerId, message) {
      const peer = peers.get(peerId);
      if (!peer) return false;
      peer.send(message);
      return true;
    },
    broadcast(message, roles) {
      for (const peer of peers.values()) if (!roles || roles.includes(/** @type {PeerRole} */ (peer.role))) peer.send(message);
    },
    on(event, listener) {
      const set = /** @type {Set<Function>} */ (listeners[event]);
      set.add(listener);
      return () => set.delete(listener);
    },
    async close() {
      for (const peer of connections) {
        if (peer.helloTimer) clearTimeout(peer.helloTimer);
      }
      for (const socket of tcpSockets) socket.destroy();
      for (const client of wss.clients) client.terminate();
      await new Promise((resolve) => wss.close(() => resolve(undefined)));
      httpServer.closeAllConnections();
      await Promise.all([closeServer(tcpServer), closeServer(httpServer)]);
      peers.clear();
      connections.clear();
    },
  };
  return hub;
}

/** @param {net.Server | http.Server} server */
function closeServer(server) {
  return new Promise((resolve) => server.close(() => resolve(undefined)));
}
