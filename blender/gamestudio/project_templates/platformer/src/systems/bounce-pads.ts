import { CharacterController, characterMotion, defineSystem } from '@gamestudio/engine';
import { BouncePad } from '../components/bounce-pad';
import { isStandingOn } from '../lib/contact';
import { sfx } from '../lib/sfx';

/** Launches players that land on a bounce pad. */
export default defineSystem({
  name: 'BouncePadSystem',
  query: [BouncePad],
  update(ctx, pads) {
    for (const player of ctx.world.query(CharacterController)) {
      const motion = characterMotion(player);
      if (motion.verticalVelocity > 0) continue; // already going up
      const pad = pads.find((p) => isStandingOn(player, p));
      if (!pad) continue;
      motion.verticalVelocity = pad.get(BouncePad).strength;
      ctx.audio.play(sfx.bounce, { at: pad, volume: 0.7 });
      ctx.vfx.burst(pad, 'sparkle', { color: '#f03e9b', scale: 1.8 });
    }
  },
});
