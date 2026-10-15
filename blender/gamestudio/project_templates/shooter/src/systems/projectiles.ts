import { defineSystem, type Entity } from '@gamestudio/engine';
import { Vector3 } from 'three';
import { Enemy } from '../components/enemy';
import { Projectile } from '../components/projectile';
import { damage } from '../lib/health';
import { sfx } from '../lib/sfx';

const FRONT = new Vector3(0, 0, 1); // an object's front (Blender -Y) in runtime space
const flight = new Map<Entity, { direction: Vector3; born: number }>();

/**
 * Flies projectiles straight ahead. Each frame a raycast covers the distance about to be flown, so
 * fast shots never pass through anything: an Enemy it meets takes damage, anything else stops it.
 */
export default defineSystem({
  name: 'ProjectileSystem',
  query: [Projectile],

  onAdd(ctx, shot) {
    flight.set(shot, { direction: FRONT.clone().applyQuaternion(shot.object3d.quaternion), born: ctx.time });
  },
  onRemove(_ctx, shot) {
    flight.delete(shot);
  },

  update(ctx, shots) {
    for (const shot of shots) {
      const f = flight.get(shot);
      if (!f) continue;
      const p = shot.get(Projectile);
      const step = p.speed * ctx.dt;
      const hit = ctx.physics.raycast(shot.object3d.position, f.direction, { maxDistance: step });
      if (hit || ctx.time - f.born > p.lifetime) {
        if (hit?.entity.has(Enemy)) {
          damage(hit.entity, p.damage, ctx.time);
          ctx.audio.play(sfx.hit, { volume: 0.4 });
          ctx.vfx.burst(hit.point, 'sparkle', { amount: 0.6 });
        } else if (hit) {
          ctx.vfx.burst(hit.point, 'dust', { scale: 0.5, amount: 0.6 }); // a wall, a crate
        }
        ctx.destroy(shot);
        continue;
      }
      shot.object3d.position.addScaledVector(f.direction, step);
    }
  },
});
