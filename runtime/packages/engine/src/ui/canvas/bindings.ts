// Live values in text bodies (docs/GAME-UI.md §3.4): `{path}` and `{path:format}`, with `{{` and `}}`
// for literal braces. Bodies are parsed once; rendering looks each path up and formats it. Pure.

/** A literal run, or a binding: a path looked up through the host (`score`, `a.b`, `save.best`). */
export type BodyPart = string | { path: string; format: string };

/** Splits a text body into literal runs and bindings. A `{` without a closing `}` is literal. */
export function parseBody(body: string): BodyPart[] {
  const parts: BodyPart[] = [];
  let literal = '';
  let i = 0;
  while (i < body.length) {
    const ch = body[i]!;
    if (ch === '{' && body[i + 1] === '{') {
      literal += '{';
      i += 2;
    } else if (ch === '}' && body[i + 1] === '}') {
      literal += '}';
      i += 2;
    } else if (ch === '{') {
      const end = body.indexOf('}', i + 1);
      const inner = end < 0 ? '' : body.slice(i + 1, end);
      const colon = inner.indexOf(':');
      const path = (colon < 0 ? inner : inner.slice(0, colon)).trim();
      if (end < 0 || !path || inner.includes('{')) {
        literal += ch;
        i += 1;
        continue;
      }
      if (literal) parts.push(literal);
      literal = '';
      parts.push({ path, format: colon < 0 ? '' : inner.slice(colon + 1).trim() });
      i = end + 1;
    } else {
      literal += ch;
      i += 1;
    }
  }
  if (literal || parts.length === 0) parts.push(literal);
  return parts;
}

export function hasBindings(parts: readonly BodyPart[]): boolean {
  return parts.some((p) => typeof p !== 'string');
}

/**
 * Renders parsed parts, looking bindings up with `value`. With `placeholders` (the editor's preview,
 * GAME-UI §11.2), a binding without a value (undefined or null) shows as written: `{path}`, `{path:format}`.
 */
export function renderBody(parts: readonly BodyPart[], value: (path: string) => unknown, placeholders = false): string {
  if (parts.length === 1 && typeof parts[0] === 'string') return parts[0];
  let out = '';
  for (const part of parts) {
    if (typeof part === 'string') {
      out += part;
      continue;
    }
    const v = value(part.path);
    out += placeholders && (v === undefined || v === null) ? `{${part.path}${part.format ? `:${part.format}` : ''}}` : formatValue(v, part.format);
  }
  return out;
}

function plain(value: unknown): string {
  if (typeof value === 'string') return value;
  if (typeof value === 'number') {
    if (!Number.isFinite(value) || Number.isInteger(value)) return String(value);
    return String(Math.round(value * 1e6) / 1e6);
  }
  if (typeof value === 'object') {
    try {
      return JSON.stringify(value);
    } catch {
      return String(value);
    }
  }
  return String(value);
}

function pad(digits: string, width: number): string {
  return digits.length >= width ? digits : '0'.repeat(width - digits.length) + digits;
}

const NUMBER = /^(0+)(?:\.(0+))?$/;
const CLOCK = /^(?:(h+):)?(m+):ss(?:\.(0+))?$/;

/**
 * Formats a bound value. Missing (undefined/null) is ''. Formats: `000` (integer, zero-padded to the
 * number of zeros), `0.0` / `00.00` (decimals, integer part padded), `m:ss` / `mm:ss` / `h:mm:ss`
 * (seconds as a clock, rounded down) with optional `.0`/`.00`/`.000` fractions. Unknown formats and
 * non-numbers show the plain value.
 */
export function formatValue(value: unknown, format: string): string {
  if (value === undefined || value === null) return '';
  if (!format) return plain(value);
  const n = typeof value === 'number' ? value : typeof value === 'string' && value.trim() !== '' ? Number(value) : NaN;
  if (!Number.isFinite(n)) return plain(value);
  const number = NUMBER.exec(format);
  if (number) {
    const decimals = number[2]?.length ?? 0;
    const fixed = Math.abs(n).toFixed(decimals);
    const dot = fixed.indexOf('.');
    const whole = dot < 0 ? fixed : fixed.slice(0, dot);
    const text = pad(whole, number[1]!.length) + (dot < 0 ? '' : fixed.slice(dot));
    return n < 0 && Number(fixed) !== 0 ? `-${text}` : text;
  }
  const clock = CLOCK.exec(format);
  if (clock) {
    const decimals = clock[3]?.length ?? 0;
    const unit = 10 ** decimals;
    const ticks = Math.floor(Math.abs(n) * unit + 1e-6);
    const fraction = ticks % unit;
    let seconds = Math.floor(ticks / unit);
    let text = '';
    let minutes = Math.floor(seconds / 60);
    seconds %= 60;
    if (clock[1]) {
      const hours = Math.floor(minutes / 60);
      minutes %= 60;
      text = `${pad(String(hours), clock[1].length)}:${pad(String(minutes), Math.max(2, clock[2]!.length))}`;
    } else {
      text = pad(String(minutes), clock[2]!.length);
    }
    text += `:${pad(String(seconds), 2)}`;
    if (decimals) text += `.${pad(String(fraction), decimals)}`;
    return n < 0 && ticks > 0 ? `-${text}` : text;
  }
  return plain(value);
}
