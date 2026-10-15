// The runner's live movement state, shared by the systems (like the engine's characterMotion).
import type { Entity } from '@gamestudio/engine';

export interface RunnerMotion {
  /** m/s, + is up. Set it to launch the runner (a shield saving a fall, a crash). */
  verticalVelocity: number;
  /** Standing on a floor after the last fixed step. */
  grounded: boolean;
  /** Seconds since it last stood on a floor. */
  airTime: number;
  /** It jumped since it last stood on a floor (no second jump in the coyote time). */
  jumped: boolean;
  /** ctx.time until which a jump press is kept (pressed just before landing). */
  jumpUntil: number;
  /** Runtime z where the run started (the track runs toward -z). */
  startZ: number;
}

const motions = new WeakMap<Entity, RunnerMotion>();

export function runnerMotion(runner: Entity): RunnerMotion {
  let motion = motions.get(runner);
  if (!motion) {
    const startZ = runner.object3d.position.z; // kinematic bodies sit at the scene root: local = world
    motion = { verticalVelocity: 0, grounded: false, airTime: 0, jumped: false, jumpUntil: -1, startZ };
    motions.set(runner, motion);
  }
  return motion;
}
