// Overlap tests for obstacles. Rapier's character mover keeps a skin around the runner and raises no
// contact events, so gameplay checks use boxes, like the platformer's contact helpers.
import type { Entity } from '@gamestudio/engine';
import { Box3, Vector3 } from 'three';

const HALF_WIDTH = 0.25; // metres to each side of the runner's centre, and front to back
const HEIGHT = 1.6; // metres above its feet
const FEET = 0.12; // the box starts this far above the feet: clipping a hurdle's top is forgiven
const FORGIVE = 0.12; // metres taken off each side of an obstacle's box

const box = new Box3();
const shrink = new Vector3(FORGIVE, 0, FORGIVE);

/** The runner's body as a world box (its origin is at its feet). */
export function bodyBox(runner: Entity, out: Box3): Box3 {
  const p = runner.object3d.position; // kinematic bodies sit at the scene root: local = world
  out.min.set(p.x - HALF_WIDTH, p.y + FEET, p.z - HALF_WIDTH);
  out.max.set(p.x + HALF_WIDTH, p.y + HEIGHT, p.z + HALF_WIDTH);
  return out;
}

/** The body box overlaps the obstacle's meshes (children included), less a little forgiveness. */
export function touches(body: Box3, obstacle: Entity): boolean {
  box.setFromObject(obstacle.object3d);
  if (box.isEmpty()) return false;
  box.min.add(shrink);
  box.max.sub(shrink);
  box.max.y -= FORGIVE / 2;
  return box.intersectsBox(body);
}
