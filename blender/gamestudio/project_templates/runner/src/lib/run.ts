// The run, shared by the systems. RunSystem resets it when the scene starts. The coins are the run's,
// in ctx.store.coins (Restart clears them); the distance and speed are the Runner's own fields, which
// RunSystem copies into ctx.store for the HUD (ui/ui.blend binds {distance}, {speed}, {score}).
import type { SystemContext } from '@gamestudio/engine';

export const run = {
  over: false, // crashed or fell: the game over screen is on its way
  cause: 'crash' as 'crash' | 'fall',
};

/** Points per coin; every metre run is a point too. */
export const COIN_POINTS = 10;

export function score(distance: number, coins: number): number {
  return Math.floor(distance) + coins * COIN_POINTS;
}

/** 0 at the start of the run, 1 once `rampDistance` metres are run: speed, coin values and hazards follow it. */
export function difficulty(distance: number, rampDistance: number): number {
  return Math.min(1, Math.max(0, distance / rampDistance));
}

let banners = 0;

/**
 * Shows `text` in the middle of the screen for `seconds` of game time: the HUD's Banner text shows
 * {banner} (ctx.store.banner), and '' shows nothing. A newer banner replaces it and keeps its own time.
 */
export function banner(ctx: SystemContext, text: string, seconds: number): void {
  const shown = ++banners;
  ctx.store.banner = text;
  ctx.after(seconds, (ctx) => {
    if (shown === banners) ctx.store.banner = '';
  });
}
