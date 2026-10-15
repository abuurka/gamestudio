// @ts-check
// SPDX-License-Identifier: MIT
// Client for the Blender bridge (blender/gamestudio/bridge): newline-delimited JSON-RPC 2.0 over
// TCP on 127.0.0.1, authenticated with the token from the discovery file.

import net from 'node:net';
import { ToolError, lineSplitter } from './util.js';

/** JSON-RPC error from the bridge. */
export class BridgeRpcError extends ToolError {
  /** @param {{ code: number, message: string, data?: any }} error @param {string} method */
  constructor(error, method) {
    const { hint, ...data } = error.data ?? {};
    super(error.message, { hint, code: rpcCodeName(error.code), data: Object.keys(data).length ? data : undefined });
    this.rpcCode = error.code;
    this.method = method;
  }
}

/** @param {number} code */
function rpcCodeName(code) {
  return (
    { [-32700]: 'parse_error', [-32600]: 'invalid_request', [-32601]: 'unknown_method', [-32602]: 'invalid_params', [-32603]: 'blender_internal_error', [-32000]: 'blender_error', [-32001]: 'unauthorized', [-32002]: 'paused_by_user' }[code] ??
    `rpc_${code}`
  );
}

export class BridgeClient {
  /**
   * @param {net.Socket} socket
   * @param {{ pid?: number, port: number }} info
   */
  constructor(socket, info) {
    this.socket = socket;
    this.info = info;
    this.closed = false;
    /** @type {Record<string, unknown> | null} */
    this.hello = null;
    this.nextId = 1;
    /** @type {string[]} */
    this.closedDuring = [];
    /** @type {Map<number, { resolve: (v: any) => void, reject: (e: Error) => void, timer: NodeJS.Timeout, method: string }>} */
    this.pending = new Map();
    /** @type {Set<() => void>} */
    this.closeListeners = new Set();
    socket.setEncoding('utf8');
    socket.setNoDelay(true);
    const onChunk = lineSplitter((line) => this.#onLine(line));
    socket.on('data', (chunk) => onChunk(String(chunk)));
    socket.on('close', () => this.#onClose());
    socket.on('error', () => {}); // 'close' follows
  }

  /** @param {string} line */
  #onLine(line) {
    let msg;
    try {
      msg = JSON.parse(line);
    } catch {
      return;
    }
    const waiter = this.pending.get(msg.id);
    if (!waiter) return;
    this.pending.delete(msg.id);
    clearTimeout(waiter.timer);
    if (msg.error) waiter.reject(new BridgeRpcError(msg.error, waiter.method));
    else waiter.resolve(msg.result);
  }

  #onClose() {
    if (this.closed) return;
    this.closed = true;
    /** The requests that were waiting when the connection closed (for crash reports). */
    this.closedDuring = [...this.pending.values()].map((w) => w.method);
    for (const [id, waiter] of this.pending) {
      clearTimeout(waiter.timer);
      waiter.reject(
        new ToolError(`Blender closed the bridge connection during ${waiter.method}.`, {
          code: 'blender_disconnected',
          hint: 'Blender may have quit or crashed. Call status; launch_blender starts it again.',
        }),
      );
      this.pending.delete(id);
    }
    for (const fn of this.closeListeners) fn();
  }

  /**
   * @param {string} method
   * @param {Record<string, unknown>} [params]
   * @param {{ timeoutMs?: number, signal?: AbortSignal }} [options]
   * @returns {Promise<any>}
   */
  call(method, params = {}, options = {}) {
    if (this.closed) {
      return Promise.reject(new ToolError('The Blender bridge connection is closed.', { code: 'blender_disconnected', hint: 'Call status to reconnect.' }));
    }
    const timeoutMs = options.timeoutMs ?? 60_000;
    const id = this.nextId++;
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        reject(
          new ToolError(`Blender did not answer ${method} within ${Math.round(timeoutMs / 1000)} s.`, {
            code: 'blender_timeout',
            hint: 'Blender is busy (a long render, a modal dialog, or code that never returns). Wait and call status; if it stays stuck, the session may need a restart.',
          }),
        );
      }, timeoutMs);
      this.pending.set(id, { resolve, reject, timer, method });
      options.signal?.addEventListener('abort', () => {
        if (!this.pending.has(id)) return;
        clearTimeout(timer);
        this.pending.delete(id);
        reject(new ToolError(`${method} was cancelled; Blender may still finish it.`, { code: 'cancelled' }));
      });
      this.socket.write(`${JSON.stringify({ jsonrpc: '2.0', id, method, params })}\n`);
    });
  }

  /** @param {() => void} fn */
  onClose(fn) {
    this.closeListeners.add(fn);
  }

  close() {
    this.socket.destroy();
  }
}

/**
 * Connects and authenticates. `agent` is the MCP client behind this server (its `clientInfo` from
 * initialize, e.g. claude-code), so Blender can show who is connected.
 * @param {{ host?: string, port: number, token: string, pid?: number, client?: string,
 *   agent?: { name?: unknown, version?: unknown } | null, timeoutMs?: number }} options
 * @returns {Promise<BridgeClient>}
 */
export function connectBridge({ host = '127.0.0.1', port, token, pid, client = 'gamestudio-mcp', agent = null, timeoutMs = 3000 }) {
  const who = typeof agent?.name === 'string' && agent.name ? { name: agent.name, version: String(agent.version ?? '') } : undefined;
  return new Promise((resolve, reject) => {
    const socket = net.connect({ host, port });
    const timer = setTimeout(() => {
      socket.destroy();
      reject(new ToolError(`Timed out connecting to the Blender bridge on ${host}:${port}.`, { code: 'blender_unreachable' }));
    }, timeoutMs);
    socket.once('error', (error) => {
      clearTimeout(timer);
      reject(new ToolError(`Could not connect to the Blender bridge on ${host}:${port} (${/** @type {NodeJS.ErrnoException} */ (error).code ?? error.message}).`, { code: 'blender_unreachable' }));
    });
    socket.once('connect', () => {
      clearTimeout(timer);
      const bridge = new BridgeClient(socket, { pid, port });
      // Blender answers between UI events, or after a long-running request: allow time.
      bridge
        .call('hello', { token, client, ...(who && { agent: who }) }, { timeoutMs: Math.max(timeoutMs, 15_000) })
        .then((hello) => {
          bridge.hello = hello;
          resolve(bridge);
        })
        .catch((error) => {
          bridge.close();
          reject(error);
        });
    });
  });
}
