// Built-in particle sprites, drawn procedurally into small white-with-alpha textures (no canvas,
// no files), so they look the same everywhere and cost nothing to ship.

import { DataTexture, LinearFilter, LinearMipmapLinearFilter, RGBAFormat, UnsignedByteType } from 'three';

export const PARTICLE_TEXTURES = ['dot', 'spark', 'star', 'smoke', 'square'] as const;
export type ParticleTexture = (typeof PARTICLE_TEXTURES)[number];

const SIZE = 64;

const smoothstep = (a: number, b: number, x: number): number => {
  const t = Math.min(1, Math.max(0, (x - a) / (b - a)));
  return t * t * (3 - 2 * t);
};

/** Value noise on a lattice, tileable enough for a soft cloud. */
function noise(x: number, y: number): number {
  const hash = (i: number, j: number): number => {
    let h = Math.imul(i, 374761393) + Math.imul(j, 668265263);
    h = Math.imul(h ^ (h >>> 13), 1274126177);
    return ((h ^ (h >>> 16)) >>> 0) / 4294967296;
  };
  const xi = Math.floor(x);
  const yi = Math.floor(y);
  const fx = x - xi;
  const fy = y - yi;
  const sx = fx * fx * (3 - 2 * fx);
  const sy = fy * fy * (3 - 2 * fy);
  const a = hash(xi, yi) + (hash(xi + 1, yi) - hash(xi, yi)) * sx;
  const b = hash(xi, yi + 1) + (hash(xi + 1, yi + 1) - hash(xi, yi + 1)) * sx;
  return a + (b - a) * sy;
}

const fbm = (x: number, y: number): number => noise(x, y) * 0.55 + noise(x * 2.1, y * 2.1) * 0.3 + noise(x * 4.3, y * 4.3) * 0.15;

/** Opacity of each sprite at (u, v) in -1..1. */
const SHAPES: Record<ParticleTexture, (u: number, v: number) => number> = {
  dot: (u, v) => {
    const r2 = u * u + v * v;
    return Math.exp(-r2 * 4.5) * (1 - smoothstep(0.75, 1, Math.sqrt(r2)));
  },
  spark: (u, v) => {
    // A thin streak along v: stretched along the velocity it reads as a flying spark.
    const along = Math.max(0, 1 - v * v);
    return Math.min(1, Math.exp(-u * u * 7) * Math.pow(along, 1.5) + Math.exp(-(u * u + v * v) * 12) * 0.5);
  },
  star: (u, v) => {
    const r = Math.hypot(u, v);
    const rays = Math.exp(-Math.abs(u * v) * 70) * Math.max(0, 1 - r);
    return Math.min(1, Math.exp(-r * r * 18) + rays * 1.1) * (1 - smoothstep(0.9, 1, r));
  },
  smoke: (u, v) => {
    const r = Math.hypot(u, v);
    const cloud = 0.35 + 0.65 * fbm(u * 2.6 + 7.3, v * 2.6 + 1.9);
    return Math.pow(1 - smoothstep(0.15, 1, r), 1.4) * cloud;
  },
  square: (u, v) => {
    const edge = 1.5 / SIZE;
    return (1 - smoothstep(0.82 - edge, 0.82 + edge, Math.abs(u))) * (1 - smoothstep(0.5 - edge, 0.5 + edge, Math.abs(v)));
  },
};

const cache = new Map<ParticleTexture, DataTexture>();

/** The shared texture for a built-in sprite (made on first use). */
export function particleTexture(kind: ParticleTexture): DataTexture {
  let texture = cache.get(kind);
  if (texture) return texture;
  const shape = SHAPES[kind] ?? SHAPES.dot;
  const data = new Uint8Array(SIZE * SIZE * 4);
  for (let y = 0; y < SIZE; y++) {
    for (let x = 0; x < SIZE; x++) {
      const u = ((x + 0.5) / SIZE) * 2 - 1;
      const v = ((y + 0.5) / SIZE) * 2 - 1;
      const i = (y * SIZE + x) * 4;
      data[i] = data[i + 1] = data[i + 2] = 255;
      data[i + 3] = Math.round(Math.min(1, Math.max(0, shape(u, v))) * 255);
    }
  }
  texture = new DataTexture(data, SIZE, SIZE, RGBAFormat, UnsignedByteType);
  texture.name = `gs_particle_${kind}`;
  texture.magFilter = LinearFilter;
  texture.minFilter = LinearMipmapLinearFilter;
  texture.generateMipmaps = true;
  texture.needsUpdate = true;
  cache.set(kind, texture);
  return texture;
}

/** Opacity of a built-in sprite at (u, v) in -1..1 (for tests). */
export function spriteAlpha(kind: ParticleTexture, u: number, v: number): number {
  return SHAPES[kind](u, v);
}
