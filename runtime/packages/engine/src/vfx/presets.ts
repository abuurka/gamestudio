// Named effects for `ctx.vfx.burst(at, 'pickup')`. A preset is one or more emitter layers that start
// together (an explosion is a flash, fire, sparks and smoke). Values are ParticleEmitter fields.

import type { ParticleSettings } from './particles';

export const VFX_PRESETS = ['sparkle', 'pickup', 'dust', 'smoke', 'explosion', 'confetti'] as const;
export type VfxPreset = (typeof VFX_PRESETS)[number];

type Layer = Partial<ParticleSettings>;
const ALL = Math.PI;

export const PRESET_LAYERS: Record<VfxPreset, Layer[]> = {
  sparkle: [
    {
      texture: 'star', burst: 16, lifetime: [0.5, 1], speed: [0.4, 1.6], spread: ALL, shape: 'sphere', radius: 0.35,
      gravity: -0.05, drag: 1.5, size: [0.32, 0], colorStart: [1, 0.72, 0.18, 1], colorEnd: [1, 0.42, 0.08, 0],
      spin: 2,
    },
  ],
  pickup: [
    {
      texture: 'dot', burst: 1, lifetime: [0.22, 0.22], speed: [0, 0], size: [0.7, 1.6],
      colorStart: [1, 0.7, 0.25, 0.6], colorEnd: [1, 0.5, 0.12, 0],
    },
    {
      texture: 'star', burst: 20, lifetime: [0.45, 0.8], speed: [2, 3.5], spread: 1.1, gravity: 0.35, drag: 2.5,
      size: [0.26, 0], colorStart: [1, 0.7, 0.14, 1], colorEnd: [1, 0.42, 0.06, 0], brightness: 1.1, spin: 3,
    },
  ],
  dust: [
    {
      texture: 'smoke', blending: 'alpha', burst: 10, lifetime: [0.5, 0.9], speed: [0.6, 1.6], spread: 1.45, shape: 'cone',
      radius: 0.25, gravity: 0.1, drag: 3.5, size: [0.25, 0.75], colorStart: [0.62, 0.54, 0.43, 0.6],
      colorEnd: [0.66, 0.6, 0.5, 0], spin: 1,
    },
  ],
  smoke: [
    {
      texture: 'smoke', blending: 'alpha', burst: 14, lifetime: [1.2, 2], speed: [0.4, 1.1], spread: 0.45, shape: 'sphere',
      radius: 0.25, gravity: -0.12, drag: 1.2, size: [0.5, 1.7], colorStart: [0.3, 0.3, 0.32, 0.65],
      colorEnd: [0.22, 0.22, 0.24, 0], spin: 0.6,
    },
  ],
  explosion: [
    {
      texture: 'smoke', blending: 'alpha', burst: 14, lifetime: [1.2, 2.2], speed: [1, 3], spread: ALL, shape: 'sphere',
      radius: 0.4, gravity: -0.15, drag: 2.2, size: [0.9, 2.6], colorStart: [0.16, 0.15, 0.14, 0.7],
      colorEnd: [0.12, 0.12, 0.12, 0], spin: 0.7,
    },
    {
      texture: 'smoke', blending: 'alpha', burst: 20, lifetime: [0.35, 0.6], speed: [2, 4.5], spread: ALL, shape: 'sphere',
      radius: 0.3, gravity: -0.2, drag: 4.5, size: [0.7, 1.4], colorStart: [1, 0.42, 0.06, 1], colorEnd: [0.25, 0.05, 0.02, 0],
      brightness: 1.2, spin: 1.5,
    },
    {
      texture: 'dot', burst: 10, lifetime: [0.15, 0.3], speed: [1, 3], spread: ALL, shape: 'sphere', radius: 0.2, drag: 5,
      size: [0.9, 0.3], colorStart: [1, 0.75, 0.3, 0.9], colorEnd: [1, 0.35, 0.05, 0], brightness: 1.2,
    },
    {
      texture: 'dot', burst: 1, lifetime: [0.12, 0.12], speed: [0, 0], size: [1.8, 3],
      colorStart: [1, 0.7, 0.35, 0.8], colorEnd: [1, 0.4, 0.1, 0],
    },
    {
      texture: 'spark', burst: 24, lifetime: [0.45, 0.9], speed: [6, 12], spread: ALL, gravity: 1, drag: 1.2, stretch: 0.05,
      size: [0.16, 0.07], colorStart: [1, 0.62, 0.18, 1], colorEnd: [1, 0.3, 0.05, 0], brightness: 1.3,
    },
  ],
  confetti: [
    {
      texture: 'square', blending: 'alpha', burst: 70, lifetime: [1.6, 2.6], speed: [5, 9], spread: 0.55, gravity: 0.55,
      drag: 1.6, size: [0.16, 0.14], colorStart: [1, 1, 1, 1], colorEnd: [1, 1, 1, 0.85], spin: 9,
      palette: [
        [0.9, 0.04, 0.08, 1],
        [1, 0.55, 0.02, 1],
        [0.02, 0.28, 0.9, 1],
        [0.08, 0.6, 0.1, 1],
        [0.5, 0.08, 0.85, 1],
      ],
    },
  ],
};
