// @ts-check
// SPDX-License-Identifier: MIT
// The host's end of the surface channel (docs/fork/SURFACE.md §2): one TCP connection to the
// editor on loopback, NDJSON framing (one JSON object per line).

import { EventEmitter } from 'node:events';
import net from 'node:net';

const MAX_LINE_BYTES = 1 << 20;
const CONNECT_TIMEOUT_MS = 5000;

/**
 * Events: `message` (object), `close` (). Messages that are not JSON objects are dropped.
 */
export class SurfaceLink extends EventEmitter {
  /** @param {net.Socket} socket */
  constructor(socket) {
    super();
    this.socket = socket;
    this.closed = false;
    let buffer = '';
    socket.setNoDelay(true);
    socket.setEncoding('utf8');
    socket.on('data', (chunk) => {
      buffer += chunk;
      let newline;
      while ((newline = buffer.indexOf('\n')) >= 0) {
        const line = buffer.slice(0, newline).trim();
        buffer = buffer.slice(newline + 1);
        if (line) this.#dispatch(line);
      }
      if (buffer.length > MAX_LINE_BYTES) {
        console.error('[gamestudio-host] surface: line too long, closing the link');
        this.close();
      }
    });
    socket.on('error', (error) => console.error(`[gamestudio-host] surface: ${error.message}`));
    socket.on('close', () => {
      this.closed = true;
      this.emit('close');
    });
  }

  /**
   * Connects to the editor's surface listener.
   * @param {string} host
   * @param {number} port
   * @returns {Promise<SurfaceLink>}
   */
  static connect(host, port) {
    return new Promise((resolve, reject) => {
      const socket = net.connect({ host, port });
      const timer = setTimeout(() => {
        socket.destroy();
        reject(new Error(`no answer from the editor at ${host}:${port} within ${CONNECT_TIMEOUT_MS} ms`));
      }, CONNECT_TIMEOUT_MS);
      socket.once('connect', () => {
        clearTimeout(timer);
        socket.removeAllListeners('error');
        resolve(new SurfaceLink(socket));
      });
      socket.once('error', (error) => {
        clearTimeout(timer);
        reject(new Error(`cannot connect to the editor at ${host}:${port}: ${error.message}`));
      });
    });
  }

  /** @param {Record<string, unknown>} message */
  send(message) {
    if (!this.closed) this.socket.write(`${JSON.stringify(message)}\n`);
  }

  /**
   * Resolves with the next message whose `t` is in `types`.
   * @param {string[]} types
   * @param {number} timeoutMs
   * @returns {Promise<Record<string, unknown>>}
   */
  next(types, timeoutMs) {
    return new Promise((resolve, reject) => {
      /** @param {Record<string, unknown>} message */
      const onMessage = (message) => {
        if (typeof message.t !== 'string' || !types.includes(message.t)) return;
        cleanup();
        resolve(message);
      };
      const onClose = () => {
        cleanup();
        reject(new Error('the editor closed the surface link'));
      };
      const timer = setTimeout(() => {
        cleanup();
        reject(new Error(`no ${types.join('/')} from the editor within ${timeoutMs} ms`));
      }, timeoutMs);
      const cleanup = () => {
        clearTimeout(timer);
        this.off('message', onMessage);
        this.off('close', onClose);
      };
      this.on('message', onMessage);
      this.on('close', onClose);
    });
  }

  close() {
    this.closed = true;
    this.socket.destroy();
  }

  /**
   * Ends the link on purpose, saying why first, so the editor does not take the host's exit for
   * a crash (docs/fork/SURFACE.md §3.3).
   * @param {string} reason
   */
  goodbye(reason) {
    if (this.closed) return;
    this.send({ t: 'bye', reason });
    this.closed = true;
    this.socket.end();
  }

  /** @param {string} line */
  #dispatch(line) {
    let message;
    try {
      message = JSON.parse(line);
    } catch {
      console.error('[gamestudio-host] surface: dropped a line that is not JSON');
      return;
    }
    if (message && typeof message === 'object' && !Array.isArray(message)) this.emit('message', message);
  }
}
