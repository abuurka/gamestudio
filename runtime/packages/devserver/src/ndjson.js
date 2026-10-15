// @ts-check
// NDJSON framing: one UTF-8 JSON object per line (docs/SPEC.md §5.1).

/**
 * Returns a function that accepts text chunks (in any split) and calls
 * `onLine` once per complete, non-empty line. A trailing '\r' is removed.
 *
 * @param {(line: string) => void} onLine
 * @param {{ maxLineLength?: number, onOverflow?: () => void }} [options]
 * @returns {(chunk: string) => void}
 */
export function createLineSplitter(onLine, options = {}) {
  const maxLineLength = options.maxLineLength ?? 16 * 1024 * 1024;
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
    if (buffer.length > maxLineLength) {
      buffer = '';
      options.onOverflow?.();
    }
  };
}

/**
 * Serializes one message as an NDJSON line.
 * @param {unknown} message
 */
export function encodeLine(message) {
  return `${JSON.stringify(message)}\n`;
}
