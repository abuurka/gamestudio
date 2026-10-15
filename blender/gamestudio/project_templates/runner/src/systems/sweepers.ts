import { defineSystem, type Entity } from '@gamestudio/engine';
import { Sweeper } from '../components/sweeper';

const swings = new Map<Entity, { x: number; born: number }>(); // where each one was placed, and when

/**
 * The moving hazard: a drum that rolls from side to side across the lanes. It starts at one end, so a
 * runner sees it coming. lib/sockets.ts sets each one's speed from the difficulty when it spawns it.
 */
export default defineSystem({
  name: 'SweeperSystem',
  query: [Sweeper],
  phase: 'fixed', // in step with the crash checks

  onAdd(ctx, sweeper) {
    swings.set(sweeper, { x: sweeper.object3d.position.x, born: ctx.time }); // local to its chunk
  },
  onRemove(_ctx, sweeper) {
    swings.delete(sweeper);
  },

  update(ctx, sweepers) {
    for (const sweeper of sweepers) {
      const swing = swings.get(sweeper);
      if (!swing) continue;
      const { travel, speed, radius } = sweeper.get(Sweeper);
      const offset = travel * Math.cos((ctx.time - swing.born) * speed);
      sweeper.object3d.position.x = swing.x + offset;
      const drum = ctx.world.descendants(sweeper)[0]; // the part that turns
      if (drum) drum.object3d.rotation.z = -offset / radius; // rolling, not sliding: the run axis is runtime z
    }
  },
});
