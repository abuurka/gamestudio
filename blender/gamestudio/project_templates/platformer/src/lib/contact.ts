// Overlap helpers shared by the systems. The player's CharacterController keeps a 2 cm skin and
// raises no contact events, so gameplay checks use bounds and distances instead.
import type { Entity } from '@gamestudio/engine';
import { Box3, Vector3 } from 'three';

const boxA = new Box3();
const boxB = new Box3();
const pa = new Vector3();
const pb = new Vector3();

/** World-space bounds of `a` and `b` overlap, with `margin` metres of slack. */
export function boundsOverlap(a: Entity, b: Entity, margin = 0): boolean {
  boxA.setFromObject(a.object3d).expandByScalar(margin);
  boxB.setFromObject(b.object3d);
  return boxA.intersectsBox(boxB);
}

/** `player`'s feet rest on top of `surface` (within a few centimetres). */
export function isStandingOn(player: Entity, surface: Entity): boolean {
  boxA.setFromObject(player.object3d);
  boxB.setFromObject(surface.object3d);
  const feet = boxA.min.y;
  const overXZ = boxA.min.x <= boxB.max.x && boxA.max.x >= boxB.min.x && boxA.min.z <= boxB.max.z && boxA.max.z >= boxB.min.z;
  return overXZ && feet <= boxB.max.y + 0.15 && feet >= boxB.max.y - 0.3;
}

/** Horizontal (XZ) distance between two entities' origins, and the height difference. */
export function separation(a: Entity, b: Entity): { horizontal: number; vertical: number } {
  a.object3d.getWorldPosition(pa);
  b.object3d.getWorldPosition(pb);
  return { horizontal: Math.hypot(pa.x - pb.x, pa.z - pb.z), vertical: pa.y - pb.y };
}
