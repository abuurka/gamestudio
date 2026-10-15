// @ts-check
// SPDX-License-Identifier: MIT
// Command-line options of the runtime host (docs/fork/SURFACE.md §3).
//
//   electron <host dir> -- --url <game url> [--mode window|docked]
//            [--surface 127.0.0.1:<port> --token <hex>]
//            [--width 1280 --height 720 --dpr 1 --fps 60 --max-in-flight 3]
//            [--adapter-luid <16 hex digits>]
//
// The "--" matters: Electron refuses to start (exit code -1, no output) when a URL-like
// argument is followed by any other argument, unless "--" precedes them. `--url=<url>` as
// the last argument also works. Chromium's own switches go before "--"; anything unknown
// is ignored here.

/**
 * @typedef {object} HostOptions
 * @property {'docked' | 'window'} mode
 * @property {string} url
 * @property {{ host: string, port: number } | null} surface
 * @property {string | null} token
 * @property {number} width   texture width in pixels
 * @property {number} height  texture height in pixels
 * @property {number} dpr     device pixel ratio the page sees
 * @property {number} fps     offscreen frame rate
 * @property {number} maxInFlight  frames the editor may hold before the oldest is revoked
 * @property {string | null} adapterLuid  16 hex digits, or null
 */

const VALUE_OPTIONS = new Set([
  'url', 'mode', 'surface', 'token', 'width', 'height', 'dpr', 'fps', 'max-in-flight', 'adapter-luid',
]);
const LOOPBACK_HOSTS = new Set(['127.0.0.1', 'localhost', '[::1]', '::1']);

export class UsageError extends Error {}

/**
 * Parses host options from `argv` (without the executable and app path).
 * @param {readonly string[]} argv
 * @returns {HostOptions}
 */
export function parseArgs(argv) {
  /** @type {Map<string, string>} */
  const raw = new Map();
  for (let i = 0; i < argv.length; i++) {
    const arg = argv[i] ?? '';
    if (!arg.startsWith('--')) continue;
    const eq = arg.indexOf('=');
    const name = arg.slice(2, eq < 0 ? undefined : eq);
    if (!VALUE_OPTIONS.has(name)) continue;
    const value = eq < 0 ? argv[++i] : arg.slice(eq + 1);
    if (value === undefined || value.startsWith('--')) throw new UsageError(`--${name} needs a value`);
    raw.set(name, value);
  }

  const mode = raw.get('mode') ?? 'window';
  if (mode !== 'docked' && mode !== 'window') throw new UsageError(`--mode must be "docked" or "window", not "${mode}"`);

  const url = raw.get('url');
  if (!url) throw new UsageError('--url <game url> is required');
  checkGameUrl(url);

  const surface = raw.has('surface') ? parseSurface(/** @type {string} */ (raw.get('surface'))) : null;
  const token = raw.get('token') ?? null;
  if (token !== null && !/^[0-9a-fA-F]{32,128}$/.test(token)) throw new UsageError('--token must be 32-128 hex digits');
  if (mode === 'docked' && (!surface || !token)) {
    throw new UsageError('--mode docked needs --surface 127.0.0.1:<port> and --token <hex> (the editor passes both)');
  }
  if (surface && !token) throw new UsageError('--surface needs --token');

  const luid = raw.get('adapter-luid') ?? null;
  if (luid !== null && !/^[0-9a-fA-F]{16}$/.test(luid)) throw new UsageError('--adapter-luid must be 16 hex digits');

  return {
    mode,
    url,
    surface,
    token,
    width: intOption(raw, 'width', 1280, 16, 16384),
    height: intOption(raw, 'height', 720, 16, 16384),
    dpr: numberOption(raw, 'dpr', 1, 0.25, 8),
    fps: intOption(raw, 'fps', 60, 1, 240),
    maxInFlight: intOption(raw, 'max-in-flight', 3, 1, 8),
    adapterLuid: luid === null ? null : luid.toLowerCase(),
  };
}

/**
 * Only loopback http(s) and file: URLs may be loaded (docs/fork/SURFACE.md §7).
 * @param {string} url
 */
export function checkGameUrl(url) {
  let parsed;
  try {
    parsed = new URL(url);
  } catch {
    throw new UsageError(`--url is not a valid URL: ${url}`);
  }
  if (parsed.protocol === 'file:') return;
  if ((parsed.protocol === 'http:' || parsed.protocol === 'https:') && LOOPBACK_HOSTS.has(parsed.hostname)) return;
  throw new UsageError(`--url must be a loopback http(s) URL or a file: URL, not ${url}`);
}

/**
 * Chromium's `--use-adapter-luid` takes "<high>,<low>" as signed/unsigned decimals.
 * @param {string} luidHex 16 hex digits
 */
export function chromiumLuidSwitch(luidHex) {
  const value = BigInt(`0x${luidHex}`);
  const high = BigInt.asIntN(32, value >> 32n);
  const low = BigInt.asUintN(32, value);
  return `${high},${low}`;
}

/** @param {string} value */
function parseSurface(value) {
  const match = /^(\[::1\]|127\.0\.0\.1|localhost):(\d{1,5})$/.exec(value);
  const port = match ? Number(match[2]) : 0;
  if (!match || port < 1 || port > 65535) throw new UsageError(`--surface must be 127.0.0.1:<port>, not "${value}"`);
  return { host: match[1] === '[::1]' ? '::1' : /** @type {string} */ (match[1]), port };
}

/** @param {Map<string, string>} raw @param {string} name @param {number} fallback @param {number} min @param {number} max */
function numberOption(raw, name, fallback, min, max) {
  const text = raw.get(name);
  if (text === undefined) return fallback;
  const value = Number(text);
  if (!Number.isFinite(value) || value < min || value > max) throw new UsageError(`--${name} must be a number in ${min}..${max}`);
  return value;
}

/** @param {Map<string, string>} raw @param {string} name @param {number} fallback @param {number} min @param {number} max */
function intOption(raw, name, fallback, min, max) {
  const value = numberOption(raw, name, fallback, min, max);
  if (!Number.isInteger(value)) throw new UsageError(`--${name} must be an integer`);
  return value;
}
