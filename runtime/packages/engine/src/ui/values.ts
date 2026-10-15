// Value paths (docs/GAME-UI.md §10.1): what text bindings, UiBar, UiShow, sliders and toggles read,
// and what sliders and toggles write. `score`, `a.b` and `store.score` are ctx.store; `save.best` is
// ctx.save; `audio.master`, `audio.music`, `audio.sfx` and `audio.ui` are the audio channels' volumes;
// `game.time` (ctx.time) and `game.level` (the level's name) are built in and read-only; `ui.locale`
// is the UI language (read and write) and `ui.localeName` its name (read-only) (§11.3).
// Pure functions of the game's stores, shared by the controller and the canvas layer (its own chunk).
import type { AudioChannelName } from '../audio/audio';

/** Where value paths read and write: the game's ctx.store, ctx.save and ctx.audio, and its clock. */
export interface ValueSources {
  store: Record<string, unknown>;
  save: { get(key: string): unknown; set(key: string, value: unknown): void };
  /** ctx.audio: `volume` is the master channel's. Without it, `audio.*` paths read nothing and can't be written. */
  audio?: { volume: number; channel(name: AudioChannelName): { volume: number } } | null;
  /**
   * The built-in values (read-only): `time` is ctx.time, `level` the loaded level's name. Without it,
   * `game.time` and `game.level` read nothing.
   */
  game?: { readonly time: number; readonly level: string } | null;
  /**
   * The UI language (ctx.ui): `locale` is its code (writing one switches to it), `localeName` its name.
   * Without it, `ui.locale` and `ui.localeName` read nothing and can't be written.
   */
  ui?: { locale: string; readonly localeName: string } | null;
}

const CHANNELS: readonly string[] = ['master', 'music', 'sfx', 'ui'];
/** The built-in values: `game.time` and `game.level`. Other `game.x` paths are ctx.store's. */
const BUILT_IN: readonly string[] = ['time', 'level'];
/** The UI's values: `ui.locale` and `ui.localeName`. Other `ui.x` paths are ctx.store's. */
const UI_VALUES: readonly string[] = ['locale', 'localeName'];
/** Keys a write never goes through (they would reach Object.prototype). */
const UNSAFE = new Set(['__proto__', 'prototype', 'constructor']);

/** Where a path points: store keys, a save key (and keys inside its value), an audio channel, a built-in value or a UI value. */
type Target =
  | { kind: 'store'; keys: string[] }
  | { kind: 'save'; key: string; keys: string[] }
  | { kind: 'audio'; channel: AudioChannelName }
  | { kind: 'game'; key: 'time' | 'level'; keys: string[] }
  | { kind: 'ui'; key: 'locale' | 'localeName' };

/** Null for what is not a path: empty, or with an empty part (`a..b`). */
function parse(path: string): Target | null {
  if (typeof path !== 'string') return null;
  const parts = path.split('.').map((p) => p.trim());
  if (parts.some((p) => p === '')) return null;
  const [head, ...rest] = parts as [string, ...string[]];
  if (head === 'save' && rest.length > 0) return { kind: 'save', key: rest[0]!, keys: rest.slice(1) };
  if (head === 'audio' && rest.length === 1 && CHANNELS.includes(rest[0]!)) return { kind: 'audio', channel: rest[0] as AudioChannelName };
  if (head === 'game' && rest.length > 0 && BUILT_IN.includes(rest[0]!)) return { kind: 'game', key: rest[0] as 'time' | 'level', keys: rest.slice(1) };
  if (head === 'ui' && rest.length === 1 && UI_VALUES.includes(rest[0]!)) return { kind: 'ui', key: rest[0] as 'locale' | 'localeName' };
  return { kind: 'store', keys: head === 'store' && rest.length > 0 ? rest : parts };
}

/** A built-in value (`game.time`, `game.level`) or `ui.localeName`: sliders and toggles can't write it. */
export function isReadOnly(path: string): boolean {
  const target = parse(path);
  return target?.kind === 'game' || (target?.kind === 'ui' && target.key === 'localeName');
}

function dig(value: unknown, keys: readonly string[]): unknown {
  for (const key of keys) {
    if (value === null || typeof value !== 'object') return undefined;
    value = (value as Record<string, unknown>)[key];
  }
  return value;
}

/** Sets `keys` inside `root`, creating missing objects on the way. False when a value on the way is not an object. */
function assign(root: Record<string, unknown>, keys: readonly string[], value: unknown): boolean {
  let at: Record<string, unknown> = root;
  for (let i = 0; i < keys.length - 1; i++) {
    const key = keys[i]!;
    const next = at[key];
    if (next === undefined || next === null) at = at[key] = {};
    else if (typeof next === 'object') at = next as Record<string, unknown>;
    else return false;
  }
  at[keys[keys.length - 1]!] = value;
  return true;
}

/** A path's value, or undefined when it is missing (or the path is not one). */
export function readValue(path: string, sources: ValueSources | null | undefined): unknown {
  const target = parse(path);
  if (!target || !sources) return undefined;
  if (target.kind === 'save') return dig(sources.save.get(target.key), target.keys);
  if (target.kind === 'game') return sources.game ? dig(sources.game[target.key], target.keys) : undefined;
  if (target.kind === 'ui') return sources.ui ? sources.ui[target.key] : undefined;
  if (target.kind === 'audio') {
    const audio = sources.audio;
    if (!audio) return undefined;
    return target.channel === 'master' ? audio.volume : audio.channel(target.channel).volume;
  }
  return dig(sources.store, target.keys);
}

/**
 * Writes a path (sliders and toggles). Store paths create missing objects on the way; `save.name` goes
 * through ctx.save.set (persists; `save.a.b` rewrites `a`); `audio.<channel>` sets that channel's volume,
 * booleans as 1/0; `ui.locale` switches the UI language to a code. False when the path can't be written:
 * not a path, a built-in value (`game.time`, `game.level`: read-only) or `ui.localeName`, a key such as
 * `__proto__`, a value on the way that is not an object, an audio volume that is not a number, a
 * language that is not a string, or no such store.
 */
export function writeValue(path: string, value: unknown, sources: ValueSources | null | undefined): boolean {
  const target = parse(path);
  if (!target || !sources || target.kind === 'game') return false;
  if (target.kind === 'ui') {
    if (target.key !== 'locale' || !sources.ui || typeof value !== 'string') return false;
    sources.ui.locale = value;
    return true;
  }
  if (target.kind === 'audio') {
    const audio = sources.audio;
    const volume = typeof value === 'boolean' ? Number(value) : typeof value === 'number' ? value : NaN;
    if (!audio || !Number.isFinite(volume)) return false;
    if (target.channel === 'master') audio.volume = Math.max(0, volume);
    else audio.channel(target.channel).volume = Math.max(0, volume);
    return true;
  }
  if (target.kind === 'store') return !target.keys.some((k) => UNSAFE.has(k)) && assign(sources.store, target.keys, value);
  if (UNSAFE.has(target.key) || target.keys.some((k) => UNSAFE.has(k))) return false;
  if (target.keys.length === 0) {
    sources.save.set(target.key, value);
    return true;
  }
  const saved = sources.save.get(target.key);
  const root = saved === undefined || saved === null ? {} : saved;
  if (typeof root !== 'object' || !assign(root as Record<string, unknown>, target.keys, value)) return false;
  sources.save.set(target.key, root);
  return true;
}

/** A value as a number: booleans are 1/0, numeric strings their number; missing and anything else is 0. */
export function numberValue(value: unknown): number {
  if (typeof value === 'number') return Number.isFinite(value) ? value : 0;
  if (typeof value === 'boolean') return value ? 1 : 0;
  if (typeof value === 'string' && value.trim() !== '') {
    const n = Number(value);
    return Number.isFinite(n) ? n : 0;
  }
  return 0;
}

/** A toggle's reading of a value: JavaScript truthiness (0, '', false, null and missing are off). */
export function truthy(value: unknown): boolean {
  return !!value;
}

export type Compare = 'atLeast' | 'below' | 'equals' | 'notEquals';

/**
 * UiShow's test (GAME-UI §10.3): `value compare than`, with the value read as a number. Equality allows
 * for float noise (0.1 + 0.2 equals 0.3). Unknown comparisons are `atLeast`.
 */
export function compareValue(value: unknown, compare: Compare | string, than: number): boolean {
  const v = numberValue(value);
  const t = Number.isFinite(than) ? than : 0;
  const near = Math.abs(v - t) <= 1e-9 * Math.max(1, Math.abs(t));
  switch (compare) {
    case 'below':
      return v < t && !near;
    case 'equals':
      return near;
    case 'notEquals':
      return !near;
    default:
      return v >= t || near;
  }
}
