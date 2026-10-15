// Small overlap helpers shared by the demo's systems.
import type { Entity, SystemContext } from '@gamestudio/engine';
import { Box3 } from 'three';

/** True if a collision or sensor overlap between `a` and `b` started this frame. */
export function startedTouching(ctx: SystemContext, a: Entity, b: Entity): boolean {
  return ctx.physics.collisions.some(
    (c) => c.started && ((c.a === a && c.b === b) || (c.a === b && c.b === a)),
  );
}

const boxA = new Box3();
const boxB = new Box3();

/** World-space bounds of `a` and `b` overlap, with `margin` metres of slack. */
export function boundsOverlap(a: Entity, b: Entity, margin = 0): boolean {
  boxA.setFromObject(a.object3d).expandByScalar(margin);
  boxB.setFromObject(b.object3d);
  return boxA.intersectsBox(boxB);
}

/** `player`'s feet are on top of `surface` (within a few centimetres). */
export function isStandingOn(player: Entity, surface: Entity): boolean {
  boxA.setFromObject(player.object3d);
  boxB.setFromObject(surface.object3d);
  const feet = boxA.min.y;
  const overlapsXZ =
    boxA.min.x <= boxB.max.x && boxA.max.x >= boxB.min.x && boxA.min.z <= boxB.max.z && boxA.max.z >= boxB.min.z;
  return overlapsXZ && feet <= boxB.max.y + 0.15 && feet >= boxB.max.y - 0.3;
}
