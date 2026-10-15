// Canvas animations (docs/GAME-UI.md §10.5): `open` plays when a canvas opens, then `loop` over and over
// while it stays open; `close` plays before it hides. A track holds one value per frame of an element
// channel (local px, y down, radians clockwise, opacity 0..1); sampling is linear between frames and holds
// the end values. Pure: the model applies the samples to its layout, the controller times the close.
import type { UiAnimation, UiAnimationTrack, UiCanvasAnimations } from './format';

export type UiAnimationKind = 'open' | 'close' | 'loop';
export type UiChannel = keyof UiAnimationTrack;
export const UI_CHANNELS: readonly UiChannel[] = ['x', 'y', 'rotation', 'scaleX', 'scaleY', 'opacity'];

/** An element's channel values now; undefined: not animated (its rest value applies). */
export type AnimatedValues = { [K in UiChannel]?: number | undefined };

/** A close counts as ended this close to its end (float steps). */
const EPSILON = 1e-6;

/** An animation's duration in seconds; 0 when it is missing or has no positive, finite duration. */
export function animationDuration(animation: UiAnimation | null | undefined): number {
  const d = animation?.duration;
  return typeof d === 'number' && Number.isFinite(d) && d > 0 ? d : 0;
}

/**
 * A channel's value at `time` seconds: linear between its per-frame values at `fps`, holding the first
 * and the last. Undefined for a missing or empty track.
 */
export function sampleTrack(values: readonly number[] | null | undefined, fps: number, time: number): number | undefined {
  const n = Array.isArray(values) ? values.length : 0;
  if (n === 0) return undefined;
  const at = Number.isFinite(fps) && fps > 0 && Number.isFinite(time) ? Math.min(Math.max(time * fps, 0), n - 1) : 0;
  const i = Math.floor(at);
  const a = Number(values![i]);
  if (i >= n - 1) return a;
  const b = Number(values![i + 1]);
  return a + (b - a) * (at - i);
}

/** Samples per second of a track of `count` values: the animation's `fps`, else what count and duration imply. */
function rateOf(animation: UiAnimation, count: number): number {
  const fps = animation.fps;
  if (typeof fps === 'number' && Number.isFinite(fps) && fps > 0) return fps;
  const duration = animationDuration(animation);
  return duration > 0 && count > 1 ? (count - 1) / duration : 0;
}

/**
 * Where one canvas is in its animations. `play('open')` starts the opening (then the loop), `play('close')`
 * the closing, `play(null)` goes back to rest; `advance(dt)` moves on in UI time.
 */
export class CanvasAnimator {
  /** The animation playing: 'open', 'loop' or 'close'; null at rest. */
  phase: UiAnimationKind | null = null;
  /** Seconds into the phase. */
  time = 0;

  constructor(private readonly animations: UiCanvasAnimations | null | undefined) {}

  /** The canvas's animation of this kind, when it has a usable one. */
  animation(kind: UiAnimationKind): UiAnimation | null {
    const animation = this.animations?.[kind];
    return animation && animationDuration(animation) > 0 ? animation : null;
  }

  /** Starts `open` (or the loop, without one), `close`, or rest (null), from the start. */
  play(kind: 'open' | 'close' | null): void {
    this.time = 0;
    if (kind === 'open') {
      if (this.animation('open')) this.phase = 'open';
      else this.enterLoop(0);
    } else this.phase = kind;
  }

  /** Takes over another animator's place (a canvas replaced in place, live). */
  adopt(other: CanvasAnimator): void {
    this.phase = other.phase;
    this.time = other.time;
    // The new canvas may lack that animation: open goes on to the loop, a loop to rest.
    if (this.phase === 'open' && !this.animation('open')) this.enterLoop(0);
    else if (this.phase === 'loop' && !this.animation('loop')) this.phase = null;
    else if (this.phase === 'open' || this.phase === 'loop') this.advance(0);
  }

  /** Moves `dt` seconds on: open runs into the loop, the loop wraps, close holds its end. */
  advance(dt: number): void {
    const step = Number.isFinite(dt) && dt > 0 ? dt : 0;
    if (this.phase === 'open') {
      const duration = animationDuration(this.animation('open'));
      this.time += step;
      if (this.time >= duration - EPSILON) this.enterLoop(Math.max(0, this.time - duration));
    } else if (this.phase === 'loop') {
      const duration = animationDuration(this.animation('loop'));
      this.time = (this.time + step) % duration;
    } else if (this.phase === 'close') {
      this.time = Math.min(this.time + step, animationDuration(this.animation('close')));
    }
  }

  /** A close has played to its end (or there is none to play). */
  get closed(): boolean {
    if (this.phase !== 'close') return false;
    return this.time >= animationDuration(this.animation('close')) - EPSILON;
  }

  /**
   * How far the canvas is in, 0..1: the open's progress while opening, 1 − the close's progress while
   * closing, else 1. A menu's backdrop fades with it.
   */
  get presence(): number {
    if (this.phase === 'open') return Math.min(1, this.time / animationDuration(this.animation('open')));
    if (this.phase === 'close') {
      const duration = animationDuration(this.animation('close'));
      return duration > 0 ? Math.max(0, 1 - this.time / duration) : 0;
    }
    return 1;
  }

  /**
   * Writes the channels animated now into the record `target(id)` returns for each element id (null:
   * skip it). Every channel is written, undefined where the track has none.
   */
  sample(target: (id: string) => AnimatedValues | null): void {
    const animation = this.phase ? this.animation(this.phase) : null;
    const tracks = animation?.tracks;
    if (!animation || !tracks || typeof tracks !== 'object') return;
    for (const id of Object.keys(tracks)) {
      const track = tracks[id];
      if (!track || typeof track !== 'object') continue;
      const record = target(id);
      if (!record) continue;
      for (const channel of UI_CHANNELS) {
        const values = track[channel];
        record[channel] = sampleTrack(values, rateOf(animation, values?.length ?? 0), this.time);
      }
    }
  }

  private enterLoop(time: number): void {
    const duration = animationDuration(this.animation('loop'));
    if (duration > 0) {
      this.phase = 'loop';
      this.time = time % duration;
    } else {
      this.phase = null;
      this.time = 0;
    }
  }
}
