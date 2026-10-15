// @ts-check
// Browser stack traces point at the code Vite served (TypeScript already
// transformed), so line numbers drift from the source. This maps `error` and
// `log` locations sent by games back to original lines using the source maps
// in Vite's client module graph, before the hub forwards them to editors.

import path from 'node:path';

/** @typedef {import('../types/index.js').HubMessage} HubMessage */
/** @typedef {[number, number, number, number]} Segment generated col, source index, source line, source col (0-based) */

const B64 = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/';
const B64_LOOKUP = new Int8Array(128).fill(-1);
for (let i = 0; i < B64.length; i++) B64_LOOKUP[B64.charCodeAt(i)] = i;

/**
 * Decodes a source map `mappings` string into per-line segment lists.
 * @param {string} mappings
 * @returns {Segment[][]}
 */
export function decodeMappings(mappings) {
  /** @type {Segment[][]} */
  const lines = [];
  /** @type {Segment[]} */
  let segments = [];
  let genCol = 0;
  let source = 0;
  let sourceLine = 0;
  let sourceCol = 0;
  /** @type {number[]} */
  let fields = [];
  let value = 0;
  let shift = 0;
  const flush = () => {
    if (fields.length > 0) genCol += /** @type {number} */ (fields[0]);
    if (fields.length >= 4) {
      source += /** @type {number} */ (fields[1]);
      sourceLine += /** @type {number} */ (fields[2]);
      sourceCol += /** @type {number} */ (fields[3]);
      segments.push([genCol, source, sourceLine, sourceCol]);
    }
    fields = [];
  };
  for (let i = 0; i < mappings.length; i++) {
    const code = mappings.charCodeAt(i);
    if (code === 59 /* ; */) {
      flush();
      lines.push(segments);
      segments = [];
      genCol = 0;
    } else if (code === 44 /* , */) {
      flush();
    } else {
      const digit = B64_LOOKUP[code] ?? -1;
      if (digit < 0) continue;
      value += (digit & 31) * 2 ** shift;
      if (digit & 32) {
        shift += 5;
        continue;
      }
      fields.push(value % 2 === 1 ? -Math.floor(value / 2) : value / 2);
      value = 0;
      shift = 0;
    }
  }
  flush();
  lines.push(segments);
  return lines;
}

/**
 * Original 1-based line/column for a 1-based generated position, or null.
 * @param {Segment[][]} lines
 * @param {number} line
 * @param {number} col
 */
export function originalPosition(lines, line, col) {
  const segments = lines[line - 1];
  if (!segments || segments.length === 0) return null;
  let best = /** @type {Segment} */ (segments[0]);
  for (const segment of segments) {
    if (segment[0] <= col - 1) best = segment;
    else break;
  }
  return { line: best[2] + 1, col: best[3] + 1 };
}

const FRAME = /((?:https?:\/\/[^\s()/]+)?\/[^\s():]*?|src\/[^\s():]+|[A-Za-z]:\/[^\s():]+):(\d+):(\d+)/g;

/**
 * @param {import('vite').ViteDevServer} server
 * @param {string} root
 * @returns {(message: HubMessage) => HubMessage}
 */
export function createSourceMapper(server, root) {
  /** @type {WeakMap<object, Segment[][]>} */
  const decoded = new WeakMap();

  /**
   * @param {string} file  project-relative ('src/x.ts'), '/src/x.ts', a served URL or an absolute path
   * @param {number} line
   * @param {number} col
   */
  function map(file, line, col) {
    let pathname = file;
    try {
      if (/^https?:\/\//.test(file)) pathname = decodeURIComponent(new URL(file).pathname);
    } catch {
      return null;
    }
    pathname = pathname.split('?')[0] ?? pathname;
    if (pathname.startsWith('/@fs/')) pathname = pathname.slice(5);
    const absolute = /^[A-Za-z]:\//.test(pathname) ? pathname : path.resolve(root, pathname.replace(/^\/+/, ''));
    const graph = server.environments.client.moduleGraph;
    const modules = graph.getModulesByFile(path.normalize(absolute)) ?? graph.getModulesByFile(absolute.replace(/\\/g, '/'));
    for (const mod of modules ?? []) {
      const sourceMap = mod.transformResult?.map;
      if (!sourceMap || typeof sourceMap.mappings !== 'string') continue;
      let lines = decoded.get(sourceMap);
      if (!lines) {
        lines = decodeMappings(sourceMap.mappings);
        decoded.set(sourceMap, lines);
      }
      const position = originalPosition(lines, line, col);
      if (!position) return null;
      const relative = path.relative(root, absolute).split(path.sep).join('/');
      return { file: relative.startsWith('..') ? absolute.replace(/\\/g, '/') : relative, ...position };
    }
    return null;
  }

  return (message) => {
    try {
      if (message.t === 'error') {
        const out = { ...message };
        if (typeof out.file === 'string' && typeof out.line === 'number') {
          const mapped = map(out.file, out.line, typeof out.col === 'number' ? out.col : 1);
          if (mapped) Object.assign(out, mapped);
        }
        if (typeof out.stack === 'string') {
          out.stack = out.stack.replace(FRAME, (whole, file, line, col) => {
            const mapped = map(file, Number(line), Number(col));
            return mapped ? `${mapped.file}:${mapped.line}:${mapped.col}` : whole;
          });
        }
        return out;
      }
      if (message.t === 'log' && typeof message.source === 'string') {
        const match = /^(.*):(\d+):(\d+)$/.exec(message.source);
        const mapped = match && map(match[1] ?? '', Number(match[2]), Number(match[3]));
        if (mapped) return { ...message, source: `${mapped.file}:${mapped.line}:${mapped.col}` };
      }
    } catch {
      // Mapping is best-effort; never break message delivery.
    }
    return message;
  };
}
