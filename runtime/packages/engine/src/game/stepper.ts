// Fixed-timestep accumulator: converts variable frame times into whole
// physics steps of `step` seconds, capping the work done per frame.

export const FIXED_DT = 1 / 60;

export class FixedStepper {
  private accumulator = 0;

  constructor(
    readonly step = FIXED_DT,
    /** Steps allowed per frame before dropping time (avoids a spiral of death). */
    readonly maxSteps = 5,
  ) {}

  /** Adds `dt` seconds and returns how many fixed steps to run now. */
  advance(dt: number): number {
    this.accumulator += Math.max(0, dt);
    let steps = Math.floor(this.accumulator / this.step + 1e-9);
    if (steps > this.maxSteps) {
      steps = this.maxSteps;
      this.accumulator = 0;
    } else {
      this.accumulator -= steps * this.step;
    }
    return steps;
  }

  reset(): void {
    this.accumulator = 0;
  }
}
