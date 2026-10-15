// @ts-check
// SPDX-License-Identifier: MIT
// Small shared helpers: errors for agents, NDJSON framing, timing.

/**
 * An error meant for the agent: `message` says what failed, `hint` how to fix it.
 * Tool handlers throw it; the server turns it into an `isError` tool result.
 */
export class ToolError extends Error {
  /**
   * @param {string} message
   * @param {{ hint?: string, code?: string, data?: Record<string, unknown> }} [options]
   */
  constructor(message, options = {}) {
    super(message);
    this.name = 'ToolError';
    this.hint = options.hint;
    this.code = options.code;
    this.data = options.data;
  }

  toJSON() {
    return {
      error: this.message,
      ...(this.code && { code: this.code }),
      ...(this.hint && { hint: this.hint }),
      ...(this.data ?? {}),
    };
  }
}

/**
 * Calls `onLine` once per complete, non-empty line of the text chunks it is fed.
 * @param {(line: string) => void} onLine
 * @param {number} [maxLineLength]
 * @returns {(chunk: string) => void}
 */
export function lineSplitter(onLine, maxLineLength = 64 * 1024 * 1024) {
  let buffer = '';
  return (chunk) => {
    buffer += chunk;
    let start = 0;
    let index;
    while ((index = buffer.indexOf('\n', start)) !== -1) {
      let line = buffer.slice(start, index);
      start = index + 1;
      if (line.endsWith('\r')) line = line.slice(0, -1);
      if (line.trim() !== '') onLine(line);
    }
    buffer = start === 0 ? buffer : buffer.slice(start);
    if (buffer.length > maxLineLength) buffer = '';
  };
}

/** @param {number} ms */
export const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

/**
 * Polls `fn` until it returns a truthy value or the timeout passes (then returns the last value).
 * @template T
 * @param {() => T | Promise<T>} fn
 * @param {{ timeoutMs: number, intervalMs?: number, signal?: AbortSignal }} options
 * @returns {Promise<T>}
 */
export async function waitFor(fn, { timeoutMs, intervalMs = 100, signal }) {
  const deadline = Date.now() + timeoutMs;
  for (;;) {
    const value = await fn();
    if (value || Date.now() >= deadline || signal?.aborted) return value;
    await sleep(intervalMs);
  }
}

/**
 * True if a process with this pid exists.
 * @param {unknown} pid
 */
export function pidAlive(pid) {
  if (typeof pid !== 'number' || !Number.isInteger(pid) || pid <= 0) return false;
  try {
    process.kill(pid, 0);
    return true;
  } catch (error) {
    return /** @type {NodeJS.ErrnoException} */ (error).code === 'EPERM';
  }
}

/** @param {number} n @param {number} [digits] */
export const round = (n, digits = 3) => Math.round(n * 10 ** digits) / 10 ** digits;
