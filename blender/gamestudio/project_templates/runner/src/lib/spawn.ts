// Spawning at a world transform under a parent: Unity's Instantiate(prefab, position, rotation, parent).
// ctx.spawn takes `position` and `rotation` local to `parent`, while sockets and broken obstacles give
// world transforms (getWorldPosition, getWorldQuaternion), so they are turned into the parent's space.
import type { Entity, SystemContext } from '@gamestudio/engine';
import { Quaternion, Vector3 } from 'three';

const local = new Vector3();
const parentRotation = new Quaternion();
const rotation = new Quaternion();

/** Spawns `prefab` with its root at a world position and rotation, attached to `parent` if given. */
export function spawnAt(ctx: SystemContext, prefab: string, position: Vector3, quaternion: Quaternion, parent?: Entity): Entity {
  if (!parent?.alive) return ctx.spawn(prefab, { position, rotation: quaternion });
  const p = parent.object3d;
  p.updateWorldMatrix(true, false);
  p.worldToLocal(local.copy(position));
  rotation.copy(p.getWorldQuaternion(parentRotation)).invert().multiply(quaternion);
  return ctx.spawn(prefab, { parent, position: local, rotation });
}
