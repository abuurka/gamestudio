import { CharacterController, defineSystem } from '@gamestudio/engine';
import { Box3, Vector3 } from 'three';
import pickup from '../../assets/sfx/pickup.wav?url';
import win from '../../assets/sfx/win.wav?url';
import { Collectible } from '../components/collectible';

const PICKUP_DISTANCE = 1.2; // metres between the player's body centre and the item's centre
const itemPos = new Vector3();
const bounds = new Box3();

/**
 * Spins collectibles, removes them when a player gets close, and wins when none are left. Edit me: changes hot-reload.
 * The screens are in ui/ui.blend: the HUD shows the score ("Score {score}" is ctx.store.score), and Win is
 * opened from here.
 */
export default defineSystem({
  name: 'CollectSystem',
  query: [Collectible],

  start(ctx) {
    ctx.store.score ??= 0; // ctx.store survives level changes; Restart clears it
  },

  update(ctx, items) {
    // Measure from the body centre, wherever the origin is (the character kit's is at its feet).
    const centres = ctx.world.query(CharacterController).map((p) => bounds.setFromObject(p.object3d).getCenter(new Vector3()));
    let collected = false;
    for (const item of items) {
      item.object3d.rotateY(2 * ctx.dt);
      item.object3d.getWorldPosition(itemPos);
      const touched = centres.some((centre) => centre.distanceTo(itemPos) < PICKUP_DISTANCE);
      if (touched) {
        ctx.store.score += item.get(Collectible).points;
        ctx.vfx.burst(item, 'pickup'); // plays on where the item was
        ctx.audio.play(pickup, { volume: 0.6 });
        item.destroy();
        collected = true;
      }
    }
    if (collected && ctx.world.query(Collectible).length === 0) {
      const player = ctx.world.query(CharacterController)[0];
      if (player) ctx.vfx.burst(player, 'confetti');
      ctx.audio.play(win, { volume: 0.7 });
      // A moment for the confetti, then the Win screen (a menu: it pauses the game).
      ctx.after(1.2, (ctx) => ctx.ui.open('Win'));
    }
  },
});
