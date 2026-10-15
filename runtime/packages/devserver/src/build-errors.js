// @ts-check
// Build errors for the live link. Vite logs every module it fails to transform
// (a TypeScript syntax error, an import that does not resolve) with the error
// object attached; this turns them into `error` messages with a source location
// and `kind: 'build'` (docs/SPEC.md §5.4), so an editor can open the file at the
// line even when no game runs, or the page failed to load because of the error.

import path from 'node:path';

/** @typedef {import('../types/index.js').HubMessage} HubMessage */

const ANSI = /\u001b\[[0-9;]*m/g;
// Oxc's code frame: box-drawing gutters and caret lines.
const FRAME_LINE = /^\s*(?:\d+\s*)?[│╭╰─┬├┤┼┆|^]/;
// Oxc puts the location in its message only: "╭─[ src/a.ts?t=1:6:15 ]" (1-based).
const MESSAGE_LOCATION = /([^\s[\]()"'?]+\.(?:[cm]?[jt]sx?|vue|json|css))(?:\?[^\s:]*)?:(\d+):(\d+)/;
// A module named in Vite's own log line: "Error when evaluating SSR module /src/a.ts: ...".
const LOGGED_MODULE = /(?:^|\s)(\/?(?:[\w@.$+-]+\/)+[\w@.$+-]+\.(?:[cm]?[jt]sx?|vue))(?=[:\s?]|$)/;
const REPEAT_MS = 1500;
const MAX_MSG = 2000;

/** @param {string} text */
export const stripAnsi = (text) => text.replace(ANSI, '');

/**
 * One readable line from a build error message: no colours, no code frame.
 * @param {string} message
 */
export function summarize(message) {
  const lines = stripAnsi(message)
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter((line) => line && !FRAME_LINE.test(line));
  const text = lines.slice(0, 2).join(' ');
  return text.length > MAX_MSG ? `${text.slice(0, MAX_MSG)}…` : text;
}

/**
 * A project-relative POSIX path for files inside root, else an absolute POSIX path.
 * @param {string} file
 * @param {string} root
 */
export function projectPath(file, root) {
  const absolute = path.isAbsolute(file) ? file : path.resolve(root, file);
  const relative = path.relative(root, absolute);
  return (relative.startsWith('..') || path.isAbsolute(relative) ? absolute : relative).split(path.sep).join('/');
}

/** @param {string} file */
const withoutQuery = (file) => file.split('?')[0] ?? file;

/**
 * The live-link message for a Vite/Rollup-style error, or null when it is not one.
 * `loc.column` is 0-based (Rollup); the message's own location (Oxc) is 1-based.
 * @param {unknown} error  `{ message, id?, loc?: { file?, line, column }, plugin?, frame? }`
 * @param {string} root
 * @param {string} [logged]  the line Vite logged with it, which may name the module
 * @returns {HubMessage | null}
 */
export function toBuildError(error, root, logged = '') {
  if (!error || typeof error !== 'object') return null;
  const err = /** @type {{ message?: unknown, id?: unknown, loc?: any, plugin?: unknown, frame?: unknown }} */ (error);
  const message = typeof err.message === 'string' ? err.message : '';
  if (!message) return null;
  /** @type {HubMessage} */
  const out = { t: 'error', kind: 'build', msg: summarize(message) };
  const loc = err.loc && typeof err.loc === 'object' ? err.loc : null;
  const fromMessage = MESSAGE_LOCATION.exec(stripAnsi(message));
  const fromLog = LOGGED_MODULE.exec(stripAnsi(logged));
  let file = '';
  if (loc && Number.isInteger(loc.line)) {
    file = withoutQuery(typeof loc.file === 'string' ? loc.file : String(err.id ?? ''));
    out.line = loc.line;
    if (Number.isInteger(loc.column)) out.col = loc.column + 1;
  } else if (fromMessage) {
    file = fromMessage[1] ?? '';
    out.line = Number(fromMessage[2]);
    out.col = Number(fromMessage[3]);
  } else if (typeof err.id === 'string') {
    file = withoutQuery(err.id);
  } else if (fromLog) {
    file = (fromLog[1] ?? '').replace(/^\/+/, '');
  }
  if (file) out.file = projectPath(file, root);
  if (typeof err.plugin === 'string') out.plugin = err.plugin;
  if (typeof err.frame === 'string' && err.frame) out.frame = stripAnsi(err.frame);
  return out;
}

/**
 * Wraps `logger.error` so every logged build error is also sent with `send`
 * (repeats of the same error within REPEAT_MS are sent once). Returns a
 * function that restores the logger.
 * @param {{ error(msg: string, options?: { error?: unknown }): void }} logger
 * @param {string} root
 * @param {(message: HubMessage) => void} send
 */
export function forwardBuildErrors(logger, root, send) {
  const original = logger.error;
  /** @type {Map<string, number>} */
  const recent = new Map();
  /**
   * @param {string} msg
   * @param {{ error?: unknown }} [options]
   */
  const wrapped = (msg, options) => {
    original.call(logger, msg, options);
    report(options?.error, msg);
  };
  /** @param {unknown} error @param {string} [logged] */
  function report(error, logged) {
    let message;
    try {
      message = toBuildError(error, root, typeof logged === 'string' ? logged : '');
    } catch {
      return; // Reporting is best-effort; never break Vite's own logging.
    }
    if (!message) return;
    const key = `${message.file}:${message.line}:${message.col}:${message.msg}`;
    const now = Date.now();
    if (now - (recent.get(key) ?? -Infinity) < REPEAT_MS) return;
    recent.set(key, now);
    if (recent.size > 100) recent.delete(/** @type {string} */ (recent.keys().next().value));
    send(message);
  }
  logger.error = wrapped;
  return {
    report,
    restore() {
      if (logger.error === wrapped) logger.error = original;
    },
  };
}
