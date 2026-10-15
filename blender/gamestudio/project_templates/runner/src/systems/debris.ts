// Technique: event-driven spawns, the receiving end. A debris prefab is an empty with a Debris
// component and pieces with dynamic bodies. CrashSystem spawns one where an obstacle breaks; when it
// appears, its pieces are thrown outward as soon as their bodies exist, and the whole prefab is
// removed after its lifetime (or with its chunk, whichever comes first).
import { defineSystem, RigidBody, type Entity } from '@gamestudio/engine';
import { Vector3 } from 'three';
import { Debris } from '../components/debris';
import { Runner } from '../components/runner';

const pending = new Set<Entity>(); // debris whose pieces have not been thrown yet
const centre = new Vector3();
const out = new Vector3();

/** Throws the pieces of new debris apart, with some of the runner's speed, and removes old debris. */
export default defineSystem({
  name: 'DebrisSystem',
  query: [Debris],

  onAdd(ctx, debris) {
    pending.add(debris);
    ctx.after(debris.get(Debris).lifetime, (ctx) => ctx.destroy(debris));
  },
  onRemove(_ctx, debris) {
    pending.delete(debris);
  },

  update(ctx) {
    if (pending.size === 0) return;
    const random = ctx.random.stream('debris'); // its own stream: breaking things never changes the track
    const runSpeed = ctx.world.query(Runner)[0]?.get(Runner).speed ?? 0;
    for (const debris of pending) {
      const pieces = ctx.world.descendants(debris, RigidBody);
      if (pieces.some((piece) => !ctx.physics.bodyOf(piece))) continue; // bodies come with the next physics step
      const { force } = debris.get(Debris);
      debris.object3d.getWorldPosition(centre);
      for (const piece of pieces) {
        piece.object3d.getWorldPosition(out).sub(centre).setY(0);
        if (out.lengthSq() < 1e-4) out.set(random.range(-1, 1), 0, random.range(-1, 1));
        out.normalize().multiplyScalar(force * random.range(0.5, 1));
        out.y = force * random.range(0.6, 1.1);
        out.z -= Math.max(0, runSpeed) * 0.6; // carried on the way the runner was going
        const body = ctx.physics.bodyOf(piece)!;
        body.setLinvel(out, true);
        body.setAngvel({ x: random.range(-9, 9), y: random.range(-9, 9), z: random.range(-9, 9) }, true);
      }
      pending.delete(debris);
    }
  },
});
