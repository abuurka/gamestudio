import { defineSystem, type Entity, type SystemContext } from '@gamestudio/engine';
import { Vector3 } from 'three';
import { ChaseCamera } from '../components/chase-camera';

const UP = new Vector3(0, 1, 0);
const heading = new Map<Entity, Vector3>(); // each camera's smoothed view of its target's front
const front = new Vector3();
const goal = new Vector3();
const look = new Vector3();

function chase(ctx: SystemContext, camera: Entity, blend: number): void {
  const c = camera.get(ChaseCamera);
  const target = ctx.world.get(c.target);
  if (!target) return;
  const at = target.object3d.position;
  front.set(0, 0, 1).applyQuaternion(target.object3d.quaternion).setY(0).normalize();
  let smoothed = heading.get(camera);
  if (!smoothed) heading.set(camera, (smoothed = front.clone()));
  smoothed.lerp(front, blend).normalize();
  goal.copy(at).addScaledVector(smoothed, -c.distance).addScaledVector(UP, c.height);
  camera.object3d.position.lerp(goal, Math.min(1, blend * 1.5)); // the camera has no parent: local = world
  camera.object3d.lookAt(look.copy(at).addScaledVector(smoothed, c.lookAhead).addScaledVector(UP, 1));
}

/** A camera that rides behind its target and swings round with it. Snaps into place when it starts. */
export default defineSystem({
  name: 'ChaseCameraSystem',
  query: [ChaseCamera],
  phase: 'late', // after everything has moved

  onAdd(ctx, camera) {
    heading.delete(camera);
    chase(ctx, camera, 1);
  },
  onRemove(_ctx, camera) {
    heading.delete(camera);
  },

  update(ctx, cameras) {
    for (const camera of cameras) {
      chase(ctx, camera, 1 - Math.exp(-camera.get(ChaseCamera).stiffness * ctx.dt));
    }
  },
});
