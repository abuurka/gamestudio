import { defineSystem, type Entity } from '@gamestudio/engine';
import { Backdrop } from '../components/backdrop';
import { Runner } from '../components/runner';

const placed = new Map<Entity, number>(); // each one's runtime z when it appeared

/**
 * Scenery that travels with the runner along the track: the cloud sea, the far islands and the wind
 * motes keep their distance, as things far away do. A finite backdrop would end; an endless track doesn't.
 */
export default defineSystem({
  name: 'BackdropSystem',
  query: [Backdrop],

  onAdd(_ctx, scenery) {
    placed.set(scenery, scenery.object3d.position.z);
  },
  onRemove(_ctx, scenery) {
    placed.delete(scenery);
  },

  update(ctx, sceneries) {
    const distance = ctx.world.query(Runner)[0]?.get(Runner).distance ?? 0;
    for (const scenery of sceneries) scenery.object3d.position.z = (placed.get(scenery) ?? 0) - distance; // the track runs toward -z
  },
});
