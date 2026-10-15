// Particle simulation: a fixed-capacity pool in flat typed arrays, and the emission cycle of one
// emitter (rate, burst, loop). Deterministic: it advances only in fixed steps and draws its random
// numbers from its own stream, never from Math.random or ctx.random.

import { Quaternion, Vector3, type Matrix4 } from 'three';
import type { ParticleEmitter } from '../schema/builtins';
import type { Color } from '../schema/fields';
import type { ComponentValues } from '../schema/registry';

/** ParticleEmitter fields, plus `palette` (code only): each particle takes a random colour from it. */
export type ParticleSettings = ComponentValues<typeof ParticleEmitter> & { palette?: Color[] };

/** Per-particle size variation: sizes are multiplied by 1 ± this. */
export const SIZE_VARIATION = 0.2;

export class ParticlePool {
  count = 0;
  capacity: number;
  pos!: Float32Array;
  vel!: Float32Array;
  age!: Float32Array;
  life!: Float32Array;
  size!: Float32Array; // start, end
  rot!: Float32Array; // angle, spin
  color!: Float32Array; // start rgba, end rgba

  constructor(capacity: number) {
    this.capacity = 0;
    this.resize(Math.max(1, Math.floor(capacity)));
  }

  /** Changes the capacity, keeping the live particles that fit. */
  resize(capacity: number): void {
    capacity = Math.max(1, Math.floor(capacity));
    if (capacity === this.capacity) return;
    const keep = Math.min(this.count, capacity);
    const grow = (old: Float32Array | undefined, stride: number): Float32Array => {
      const next = new Float32Array(capacity * stride);
      if (old) next.set(old.subarray(0, keep * stride));
      return next;
    };
    this.pos = grow(this.pos, 3);
    this.vel = grow(this.vel, 3);
    this.age = grow(this.age, 1);
    this.life = grow(this.life, 1);
    this.size = grow(this.size, 2);
    this.rot = grow(this.rot, 2);
    this.color = grow(this.color, 8);
    this.capacity = capacity;
    this.count = keep;
  }

  /** Index of a new particle, or -1 when the pool is full. */
  spawn(): number {
    return this.count < this.capacity ? this.count++ : -1;
  }

  /** Ages and moves every particle by `dt`; `g` is the acceleration, `drag` the damping per second. */
  integrate(dt: number, gx: number, gy: number, gz: number, drag: number): void {
    if (dt <= 0) return;
    const damp = drag > 0 ? Math.exp(-drag * dt) : 1;
    const { pos, vel, age, life, rot } = this;
    for (let i = 0; i < this.count; i++) {
      age[i]! += dt;
      if (age[i]! >= life[i]!) {
        this.remove(i);
        i--;
        continue;
      }
      const j = i * 3;
      vel[j] = (vel[j]! + gx * dt) * damp;
      vel[j + 1] = (vel[j + 1]! + gy * dt) * damp;
      vel[j + 2] = (vel[j + 2]! + gz * dt) * damp;
      pos[j]! += vel[j]! * dt;
      pos[j + 1]! += vel[j + 1]! * dt;
      pos[j + 2]! += vel[j + 2]! * dt;
      rot[i * 2]! += rot[i * 2 + 1]! * dt;
    }
  }

  clear(): void {
    this.count = 0;
  }

  /** Moves the last particle into slot `i`. */
  private remove(i: number): void {
    const last = --this.count;
    if (i === last) return;
    this.pos.copyWithin(i * 3, last * 3, last * 3 + 3);
    this.vel.copyWithin(i * 3, last * 3, last * 3 + 3);
    this.age[i] = this.age[last]!;
    this.life[i] = this.life[last]!;
    this.size.copyWithin(i * 2, last * 2, last * 2 + 2);
    this.rot.copyWithin(i * 2, last * 2, last * 2 + 2);
    this.color.copyWithin(i * 8, last * 8, last * 8 + 8);
  }
}

const dir = new Vector3();
const start = new Vector3();
const axis = new Vector3();
const t1 = new Vector3();
const t2 = new Vector3();
const radial = new Vector3();
const UP = new Vector3(0, 1, 0);
const SIDE = new Vector3(1, 0, 0);

/** Where particles are born: a world matrix (world space) or nothing (the emitter's own space). */
export interface EmitFrame {
  matrix: Matrix4 | null;
  rotation: Quaternion | null;
}

/** One emitter's cycle: a burst when a cycle starts, `rate` per second during it, then loop or stop. */
export class EmitterCore {
  playing = false;
  /** Seconds into the current cycle. */
  cycle = 0;
  private carry = 0;
  private burstDue = false;

  constructor(
    public pool: ParticlePool,
    private readonly random: () => number,
  ) {}

  play(): void {
    this.playing = true;
    this.cycle = 0;
    this.carry = 0;
    this.burstDue = true;
  }

  stop(): void {
    this.playing = false;
    this.burstDue = false;
  }

  /** True once it stopped emitting and every particle died. */
  get finished(): boolean {
    return !this.playing && this.pool.count === 0;
  }

  /**
   * One fixed step: moves the live particles, then emits. `gravity` is the scene gravity in the space
   * particles live in. A step of 0 s only emits what is due now (a burst).
   */
  step(dt: number, s: ParticleSettings, frame: EmitFrame, gravity: Vector3): void {
    const g = s.gravity;
    this.pool.integrate(dt, gravity.x * g, gravity.y * g, gravity.z * g, s.drag);
    if (!this.playing) return;
    if (this.burstDue) {
      this.burstDue = false;
      this.emit(s.burst, s, frame);
    }
    this.carry += Math.max(0, s.rate) * dt;
    const n = Math.floor(this.carry);
    this.carry -= n;
    if (n > 0) this.emit(n, s, frame);
    this.cycle += dt;
    const duration = Math.max(0.01, s.duration);
    if (this.cycle >= duration) {
      if (s.loop) {
        this.cycle %= duration;
        this.burstDue = true;
      } else {
        this.stop();
      }
    }
  }

  /** Emits `count` particles now (fewer when the pool is full). */
  emit(count: number, s: ParticleSettings, frame: EmitFrame): void {
    const pool = this.pool;
    const rnd = this.random;
    const range = (pair: readonly number[]): number => pair[0]! + rnd() * (pair[1]! - pair[0]!);
    axis.fromArray(s.direction);
    if (axis.lengthSq() < 1e-12) axis.copy(UP);
    axis.normalize();
    t1.crossVectors(Math.abs(axis.y) < 0.99 ? UP : SIDE, axis).normalize();
    t2.crossVectors(axis, t1);
    const spread = Math.min(Math.PI, Math.max(0, s.spread));
    const cosSpread = Math.cos(spread);
    const palette = s.palette && s.palette.length > 0 ? s.palette : null;

    for (let k = 0; k < Math.floor(count); k++) {
      const i = pool.spawn();
      if (i < 0) return;
      start.set(0, 0, 0);
      switch (s.shape) {
        case 'sphere': {
          const z = rnd() * 2 - 1;
          const phi = rnd() * Math.PI * 2;
          const r = Math.sqrt(1 - z * z);
          dir.set(r * Math.cos(phi), z, r * Math.sin(phi));
          start.copy(dir).multiplyScalar(s.radius * Math.cbrt(rnd()));
          break;
        }
        case 'cone': {
          const f = Math.sqrt(rnd());
          const phi = rnd() * Math.PI * 2;
          radial.copy(t1).multiplyScalar(Math.cos(phi)).addScaledVector(t2, Math.sin(phi));
          start.copy(radial).multiplyScalar(s.radius * f);
          const angle = spread * f;
          dir.copy(axis).multiplyScalar(Math.cos(angle)).addScaledVector(radial, Math.sin(angle));
          break;
        }
        case 'box':
          start.set((rnd() - 0.5) * s.box[0], (rnd() - 0.5) * s.box[1], (rnd() - 0.5) * s.box[2]);
          coneDirection(rnd, cosSpread);
          break;
        default:
          coneDirection(rnd, cosSpread);
      }
      const speed = range(s.speed);
      if (frame.matrix) start.applyMatrix4(frame.matrix);
      if (frame.rotation) dir.applyQuaternion(frame.rotation);
      const j = i * 3;
      pool.pos[j] = start.x;
      pool.pos[j + 1] = start.y;
      pool.pos[j + 2] = start.z;
      pool.vel[j] = dir.x * speed;
      pool.vel[j + 1] = dir.y * speed;
      pool.vel[j + 2] = dir.z * speed;
      pool.age[i] = 0;
      pool.life[i] = Math.max(0.01, range(s.lifetime));
      const variation = 1 + (rnd() * 2 - 1) * SIZE_VARIATION;
      pool.size[i * 2] = s.size[0] * variation;
      pool.size[i * 2 + 1] = s.size[1] * variation;
      const angle = rnd() * Math.PI * 2;
      pool.rot[i * 2] = s.image && !s.spin ? 0 : angle; // an image stays upright unless it spins
      pool.rot[i * 2 + 1] = s.spin * (rnd() < 0.5 ? -1 : 1) * (0.5 + 0.5 * rnd());
      const c = i * 8;
      const from = palette ? palette[Math.floor(rnd() * palette.length)]! : s.colorStart;
      pool.color[c] = from[0];
      pool.color[c + 1] = from[1];
      pool.color[c + 2] = from[2];
      pool.color[c + 3] = s.colorStart[3];
      pool.color[c + 4] = palette ? from[0] : s.colorEnd[0];
      pool.color[c + 5] = palette ? from[1] : s.colorEnd[1];
      pool.color[c + 6] = palette ? from[2] : s.colorEnd[2];
      pool.color[c + 7] = s.colorEnd[3];
    }
  }
}

/** A direction inside the cone around `axis` (uniform over the cap). */
function coneDirection(rnd: () => number, cosSpread: number): void {
  const cos = 1 - rnd() * (1 - cosSpread);
  const sin = Math.sqrt(Math.max(0, 1 - cos * cos));
  const phi = rnd() * Math.PI * 2;
  dir.copy(axis).multiplyScalar(cos).addScaledVector(t1, Math.cos(phi) * sin).addScaledVector(t2, Math.sin(phi) * sin);
}

/** Opacity over life: holds longer than linear, and fades in over the first few percent. */
export function lifeAlpha(t: number, a0: number, a1: number): number {
  return (a0 + (a1 - a0) * t * t) * Math.min(1, t / 0.06);
}
