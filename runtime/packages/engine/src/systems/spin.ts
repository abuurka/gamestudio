import { Vector3 } from 'three';
import { defineSystem } from '../ecs/system';
import { Spin } from '../schema/builtins';

const axis = new Vector3();

/** Rotates entities with `Spin` around their local axis. */
export const SpinSystem = defineSystem({
  name: 'SpinSystem',
  query: [Spin],
  phase: 'update',
  update(ctx, entities) {
    for (const e of entities) {
      const spin = e.get(Spin);
      axis.fromArray(spin.axis);
      if (axis.lengthSq() === 0) continue;
      e.object3d.rotateOnAxis(axis.normalize(), spin.speed * ctx.dt);
    }
  },
});
