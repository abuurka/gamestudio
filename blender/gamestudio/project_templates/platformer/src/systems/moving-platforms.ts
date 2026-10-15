import { CharacterController, characterMotion, defineSystem, type Entity } from '@gamestudio/engine';
import { Vector3 } from 'three';
import { MovingPlatform } from '../components/moving-platform';
import { isStandingOn } from '../lib/contact';

const starts = new Map<Entity, Vector3>();
const next = new Vector3();
const delta = new Vector3();

/** Glides platforms to start + offset and back, eased at both ends, carrying players that stand on them. */
export default defineSystem({
  name: 'MovingPlatformSystem',
  query: [MovingPlatform],
  phase: 'fixed', // in step with the character controller and the physics

  onAdd(_ctx, platform) {
    starts.set(platform, platform.object3d.position.clone()); // kinematic bodies sit at the scene root: local = world
  },
  onRemove(_ctx, platform) {
    starts.delete(platform);
  },

  update(ctx, platforms) {
    const players = ctx.world.query(CharacterController);
    for (const platform of platforms) {
      const start = starts.get(platform);
      if (!start) continue;
      const { offset, period, phase } = platform.get(MovingPlatform);
      const t = (1 - Math.cos(2 * Math.PI * (ctx.time / period + phase))) / 2; // 0 -> 1 -> 0
      next.fromArray(offset).multiplyScalar(t).add(start);
      delta.subVectors(next, platform.object3d.position);
      for (const player of players) {
        // A player who just jumped is not carried (moving them would cancel the jump).
        if (characterMotion(player).verticalVelocity <= 0 && isStandingOn(player, platform)) {
          player.object3d.position.add(delta);
        }
      }
      platform.object3d.position.copy(next); // the kinematic body follows on the next physics step
    }
  },
});
