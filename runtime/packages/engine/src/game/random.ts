// `ctx.random`: the game's own seeded random numbers. Math.random is shared with three.js (every new
// object, geometry and material draws from it) and the audio code, so seeded gameplay built on it moves
// whenever anything else changes. This stream only advances when gameplay code calls it.

export interface RandomAPI {
  /** A number in [0, 1). */
  (): number;
  /** A number in [min, max). */
  range(min: number, max: number): number;
  /** A whole number from `min` to `max`, both included. */
  int(min: number, max: number): number;
  /** A random element. Throws for an empty list. */
  pick<T>(items: readonly T[]): T;
  /** True with probability `p` (0..1). */
  chance(p: number): boolean;
  /** The seed the current stream started from (it changes with every scene load). */
  readonly seed: number;
  /**
   * An independent stream named `name` (the same object on every call). It is seeded from this
   * stream's seed and the name, and restarts with it, so draws from one stream (shot spread) never
   * move another (where enemies spawn): `const spawns = ctx.random.stream('spawns')`.
   */
  stream(name: string): RandomAPI;
}

/**
 * mulberry32 (Tommy Ettinger, 2017, public domain: https://gist.github.com/tommyettinger/46a874533244883189143505d203312c):
 * small, fast and good enough for games. Returns the next state and value.
 */
export function mulberry32(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/** A 32-bit seed from any mix of strings and numbers (FNV-1a). */
export function hashSeed(...parts: Array<string | number>): number {
  let h = 0x811c9dc5;
  for (const part of parts) {
    const text = `${part}\u0000`;
    for (let i = 0; i < text.length; i++) {
      h ^= text.charCodeAt(i);
      h = Math.imul(h, 0x01000193);
    }
  }
  return h >>> 0;
}

/** A seed that differs per page load. */
export function freshSeed(): number {
  const crypto = (globalThis as { crypto?: Crypto }).crypto;
  if (crypto?.getRandomValues) return crypto.getRandomValues(new Uint32Array(1))[0]!;
  return Math.floor(Math.random() * 2 ** 32) >>> 0;
}

export class GameRandom {
  readonly api: RandomAPI;
  private next: () => number;
  private current: number;
  private readonly streams = new Map<string, GameRandom>();

  constructor(seed = freshSeed()) {
    this.current = seed >>> 0;
    this.next = mulberry32(this.current);
    const next = (): number => this.next();
    const api = next as RandomAPI;
    api.range = (min, max) => min + this.next() * (max - min);
    api.int = (min, max) => {
      const lo = Math.ceil(Math.min(min, max));
      const hi = Math.floor(Math.max(min, max));
      return lo + Math.floor(this.next() * (hi - lo + 1));
    };
    api.pick = <T>(items: readonly T[]): T => {
      if (!items || items.length === 0) throw new Error('ctx.random.pick needs a non-empty list');
      return items[Math.floor(this.next() * items.length)]!;
    };
    api.chance = (p) => this.next() < p;
    api.stream = (name) => {
      let stream = this.streams.get(name);
      if (!stream) this.streams.set(name, (stream = new GameRandom(hashSeed(this.current, 'stream', name))));
      return stream.api;
    };
    Object.defineProperty(api, 'seed', { get: () => this.current, enumerable: true });
    this.api = api;
  }

  get seed(): number {
    return this.current;
  }

  /** Restarts the stream from `seed`. */
  reseed(seed: number): void {
    this.current = seed >>> 0;
    this.next = mulberry32(this.current);
    for (const [name, stream] of this.streams) stream.reseed(hashSeed(this.current, 'stream', name));
  }
}
