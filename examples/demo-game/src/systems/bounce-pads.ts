import { CharacterController, characterMotion, defineSystem } from '@gamestudio/engine';
import { BouncePad } from '../components/bounce-pad';
import { isStandingOn, startedTouching } from '../lib/contact';

/** Launches players that land on (or touch the sensor of) a bounce pad. */
export default defineSystem({
  name: 'BouncePadSystem',
  query: [BouncePad],
  phase: 'update',
  update(ctx, pads) {
    if (pads.length === 0) return;
    for (const player of ctx.world.query(CharacterController)) {
      const motion = characterMotion(player);
      if (motion.verticalVelocity > 0) continue; // Already going up.
      const pad = pads.find((p) => isStandingOn(player, p) || startedTouching(ctx, player, p));
      if (pad) motion.verticalVelocity = pad.get(BouncePad).strength;
    }
  },
});
