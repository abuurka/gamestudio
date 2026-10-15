// Camera cuts for FollowCamera and SideCamera: when their target teleports (a respawn, a checkpoint)
// they jump to their new place on that frame instead of smoothing across the level, through walls or
// the target itself. A jump is detected by distance; snapCamera() asks for a cut explicitly.

import { Vector3 } from 'three';
import type { Entity } from '../ecs/entity';

/** A target that moves farther than this in one frame (m)... */
const CUT_DISTANCE = 5;
/** ...and faster than this (m/s) teleported: no character or vehicle moves like that. */
const CUT_SPEED = 100;

/** Bumps on every snapCamera() call; a camera cuts when a request for it is newer than its last frame. */
let serial = 0;
/** The serial of the last snapCamera() without an entity: every camera. */
let everyAt = 0;
const requests = new WeakMap<Entity, number>();

interface CutState {
  /** The serial when the camera last moved. */
  seen: number;
  target: Entity;
  /** The target's world position then. */
  at: Vector3;
}

const states = new WeakMap<Entity, CutState>();
const position = new Vector3();

/**
 * The FollowCamera or SideCamera on `entity`, or every one that follows `entity`, jumps to its place
 * the next time it moves (this frame, when called before the `late` phase) instead of smoothing there.
 * Without an entity, every FollowCamera and SideCamera. Call it after teleporting a player.
 */
export function snapCamera(entity?: Entity | null): void {
  serial++;
  if (entity) requests.set(entity, serial);
  else everyAt = serial;
}

/**
 * Whether `camera` cuts to its place this frame: the first time it sees `target`, after snapCamera()
 * for it or its target, or when the target jumped farther than CUT_DISTANCE at more than CUT_SPEED
 * (`dt`: this frame's seconds). A new target is not a jump: the camera pans over to it. Records where
 * the target is for the next frame.
 */
export function cameraCut(camera: Entity, target: Entity, dt: number): boolean {
  target.object3d.getWorldPosition(position);
  const state = states.get(camera);
  if (!state) {
    states.set(camera, { seen: serial, target, at: position.clone() });
    return true;
  }
  const asked = Math.max(everyAt, requests.get(camera) ?? 0, requests.get(target) ?? 0) > state.seen;
  const jumped = state.target === target && state.at.distanceTo(position) > Math.max(CUT_DISTANCE, CUT_SPEED * dt);
  state.seen = serial;
  state.target = target;
  state.at.copy(position);
  return asked || jumped;
}
