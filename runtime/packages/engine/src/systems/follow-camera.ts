import { Vector3, type Object3D } from 'three';
import type { SystemContext } from '../context';
import type { Entity } from '../ecs/entity';
import { defineSystem } from '../ecs/system';
import { FollowCamera } from '../schema/builtins';
import { cameraCut } from './camera-cut';

const targetPos = new Vector3();
const desired = new Vector3();
const current = new Vector3();
const lookAt = new Vector3();

/** Moves the camera after its target: smoothed, or in place (`smooth` off, or a cut: a teleport or snapCamera). */
function follow(ctx: SystemContext, e: Entity, smooth: boolean): void {
  const f = e.get(FollowCamera);
  const target = ctx.world.get(f.target);
  if (!target) return;
  const cut = cameraCut(e, target, ctx.dt);
  const alpha = !smooth || cut || f.smoothing <= 0 ? 1 : 1 - Math.exp(-f.smoothing * ctx.dt);
  target.object3d.getWorldPosition(targetPos);
  desired.fromArray(f.offset).add(targetPos);
  e.object3d.getWorldPosition(current).lerp(desired, alpha);
  setWorldPosition(e.object3d, current);
  e.object3d.lookAt(lookAt.fromArray(f.lookAtOffset).add(targetPos));
}

function setWorldPosition(object: Object3D, world: Vector3): void {
  if (object.parent) {
    object.parent.updateWorldMatrix(true, false);
    object.position.copy(object.parent.worldToLocal(world.clone()));
  } else {
    object.position.copy(world);
  }
}

/**
 * Smoothly follows `FollowCamera.target`. It snaps into place when it starts, when the target teleports
 * (moves more than 5 m in a frame, faster than 100 m/s) and after `snapCamera()`.
 */
export const FollowCameraSystem = defineSystem({
  name: 'FollowCameraSystem',
  query: [FollowCamera],
  phase: 'late',
  onAdd(ctx, e) {
    follow(ctx, e, false);
  },
  update(ctx, entities) {
    for (const e of entities) follow(ctx, e, true);
  },
});
