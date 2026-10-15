import { CharacterController, characterMotion, defineSystem, type Entity } from '@gamestudio/engine';
import { sfx } from '../lib/sfx';

const HARD_LANDING = 7; // m/s downward: landing faster than this raises dust

const air = new Map<Entity, { grounded: boolean; fall: number }>(); // last frame's state, fastest fall since take-off

/** A puff of dust (and the jump sound) when a player takes off, and another when it lands hard. */
export default defineSystem({
  name: 'DustSystem',
  query: [CharacterController],

  onRemove(_ctx, player) {
    air.delete(player);
  },

  update(ctx, players) {
    for (const player of players) {
      const motion = characterMotion(player);
      const last = air.get(player);
      if (!last) {
        air.set(player, { grounded: motion.grounded, fall: 0 });
        continue;
      }
      if (last.grounded && !motion.grounded && motion.verticalVelocity > 0) {
        ctx.vfx.burst(player, 'dust', { scale: 0.7 }); // a jump or a bounce pad
        ctx.audio.play(sfx.jump, { volume: 0.3 });
      }
      if (!motion.grounded) last.fall = Math.max(last.fall, -motion.verticalVelocity);
      else if (!last.grounded) {
        if (last.fall > HARD_LANDING) ctx.vfx.burst(player, 'dust', { amount: 1.5 });
        last.fall = 0;
      }
      last.grounded = motion.grounded;
    }
  },
});
