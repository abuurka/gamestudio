import { CharacterController, defineSystem } from '@gamestudio/engine';
import { Goal } from '../components/goal';
import { separation } from '../lib/contact';
import { formatTime } from '../lib/level';
import { sfx } from '../lib/sfx';

const CHEST = 0.9; // metres above the player's origin (its feet)

/**
 * The level's clock (ctx.store.time), confetti and the Win screen when a player reaches a Goal (the best
 * time in ctx.save), and R to restart. The title, pause and options screens are ui/ui.blend's own: no code.
 */
export default defineSystem({
  name: 'GoalSystem',
  query: [Goal],

  start(ctx) {
    ctx.store.finished = false;
    ctx.store.newBest = false;
    ctx.store.time = 0;
  },

  update(ctx, goals) {
    if (ctx.input.keyDown('KeyR')) void ctx.restart();
    if (ctx.store.finished) return;
    ctx.store.time = ctx.time; // the HUD's clock: it stops at the goal

    for (const player of ctx.world.query(CharacterController)) {
      for (const goal of goals) {
        const { horizontal, vertical } = separation(goal, player);
        if (horizontal > goal.get(Goal).radius || Math.abs(vertical - CHEST) > 2) continue;
        ctx.store.finished = true;
        const best = ctx.save.get<number | null>('bestTime', null); // ctx.save survives page reloads
        ctx.store.newBest = best === null || ctx.time < best;
        if (ctx.store.newBest) ctx.save.set('bestTime', ctx.time);
        ctx.audio.play(sfx.goal, { volume: 0.7 });
        ctx.vfx.burst(goal, 'confetti', { amount: 2 });
        ctx.log(`Level complete in ${formatTime(ctx.time)} with ${ctx.store.coins} / ${ctx.store.coinTotal} coins`);
        ctx.after(1.5, (ctx) => ctx.ui.open('Win')); // the confetti first: the screen pauses the game
        return;
      }
    }
  },
});
