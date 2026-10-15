// Player movement with Rapier's KinematicCharacterController.
// Runs in the fixed phase: it moves the object3d, and the physics step then
// pushes the kinematic body to the new pose.

import type RAPIER from '@dimforge/rapier3d-compat';
import { Quaternion, Vector3 } from 'three';
import type { Entity } from '../ecs/entity';
import { defineSystem } from '../ecs/system';
import type { SystemContext } from '../context';
import { lockedAxes } from '../physics/rapier-backend';
import { CharacterController, FollowCamera, RigidBody } from '../schema/builtins';
import type { ComponentValues } from '../schema/registry';

/** Live movement state of a character (see `characterMotion`). */
export interface CharacterMotion {
  /** Vertical speed in m/s (+ is up). Set it to launch the character (e.g. a bounce pad). */
  verticalVelocity: number;
  /** Standing on something after the last fixed step. */
  readonly grounded: boolean;
}

interface MoverState extends CharacterMotion {
  grounded: boolean;
  /** Where we last put the object; any other change means it was teleported. */
  last: Vector3 | null;
}

const UP = new Vector3(0, 1, 0);
const states = new WeakMap<Entity, MoverState>();
/** The controller keeps this gap to everything it touches. */
const OFFSET = 0.02;
/** How far above itself a character looks for the ground it should stand on (see liftOutOfGround). */
const LIFT = 0.25;

function stateOf(e: Entity): MoverState {
  let state = states.get(e);
  if (!state) {
    state = { verticalVelocity: 0, grounded: false, last: null };
    states.set(e, state);
  }
  return state;
}

/** The live motion state of an entity driven by `CharacterController`. */
export function characterMotion(entity: Entity): CharacterMotion {
  return stateOf(entity);
}
const controllers = new WeakMap<RAPIER.World, RAPIER.KinematicCharacterController>();

const forward = new Vector3();
const right = new Vector3();
const topForward = new Vector3();
const topRight = new Vector3();
const sideAxis = new Vector3();
const move = new Vector3();
const facing = new Quaternion();
const restOffset = new Vector3();

/**
 * The horizontal direction "forward" input moves in (`out`, normalized). Behind a `FollowCamera` it is
 * the camera's rest direction (from `offset` toward `lookAtOffset`), which stays put while the
 * smoothed camera catches up; a lagging camera's yaw swings, and would bend a long sideways key hold.
 * Any other camera: its current view direction.
 */
export function moveForward(ctx: SystemContext, out: Vector3): Vector3 {
  const camera = ctx.three.camera;
  const follow = ctx.world.entityOf(camera)?.tryGet<ComponentValues<typeof FollowCamera>>(FollowCamera);
  if (follow) out.fromArray(follow.lookAtOffset).sub(restOffset.fromArray(follow.offset));
  if (!follow || out.x * out.x + out.z * out.z < 1e-6) camera.getWorldDirection(out);
  out.y = 0;
  if (out.lengthSq() < 1e-6) out.set(0, 0, -1);
  return out.normalize();
}

/**
 * "Up on the screen" on the ground, for `plane: 'top'`: a FollowCamera's rest direction as in
 * moveForward, else the camera's view direction plus its up vector, both flattened. That sum keeps a
 * direction also when the camera looks straight down, where the view direction alone has none.
 */
function screenUpOnGround(ctx: SystemContext, out: Vector3): Vector3 {
  const camera = ctx.three.camera;
  const follow = ctx.world.entityOf(camera)?.tryGet<ComponentValues<typeof FollowCamera>>(FollowCamera);
  if (follow) {
    out.fromArray(follow.lookAtOffset).sub(restOffset.fromArray(follow.offset)).setY(0);
    if (out.lengthSq() > 1e-6) return out.normalize();
  }
  camera.updateWorldMatrix(true, false);
  const m = camera.matrixWorld.elements; // columns: right, up, back (it looks along -Z)
  out.set(m[4]! - m[8]!, 0, m[6]! - m[10]!);
  if (out.lengthSq() < 1e-6) out.set(0, 0, -1);
  return out.normalize();
}

function controllerFor(world: RAPIER.World): RAPIER.KinematicCharacterController {
  let cc = controllers.get(world);
  if (!cc) {
    cc = world.createCharacterController(OFFSET);
    cc.setUp({ x: 0, y: 1, z: 0 });
    cc.setMaxSlopeClimbAngle((50 * Math.PI) / 180);
    cc.setMinSlopeSlideAngle((35 * Math.PI) / 180);
    cc.enableAutostep(0.35, 0.2, false);
    cc.enableSnapToGround(0.3);
    cc.setApplyImpulsesToDynamicBodies(true);
    controllers.set(world, cc);
  }
  return cc;
}

/**
 * How far to raise a character that is closer than OFFSET to the ground under it: placed exactly on
 * a floor, or sunk into it. Rapier's controller cannot leave that margin by itself; it lets the
 * character sink, slowly and depending on where it stands. A downward cast of the collider from
 * LIFT above finds where it should rest. 0 when it is clear (or something is right above it).
 */
function liftOutOfGround(world: RAPIER.World, collider: RAPIER.Collider, filter: number): number {
  const t = collider.translation();
  const from = { x: t.x, y: t.y + LIFT, z: t.z };
  const hit = world.castShape(from, collider.rotation(), { x: 0, y: -1, z: 0 }, collider.shape, OFFSET, LIFT, true, filter, undefined, collider);
  if (!hit || hit.time_of_impact <= 0) return 0;
  const up = LIFT - hit.time_of_impact;
  return up > 1e-4 ? up : 0;
}

/**
 * Moves entities with `CharacterController` (they also need a `Collider`,
 * ideally a capsule, and a kinematic `RigidBody`). `plane: 'side'` runs along the screen only and
 * keeps the depth; `'top'` moves on the screen under a top-down camera. Locked RigidBody axes
 * (lockX/Y/Z) are never moved along.
 */
export const CharacterControllerSystem = defineSystem({
  name: 'CharacterControllerSystem',
  query: [CharacterController],
  phase: 'fixed',
  update(ctx, entities) {
    const world = ctx.physics.world;
    if (!world || entities.length === 0) return;
    const cc = controllerFor(world);

    // Movement is relative to the camera's yaw (a FollowCamera's rest direction: see moveForward).
    moveForward(ctx, forward);
    right.crossVectors(forward, UP);
    const h = ctx.input.axis('horizontal');
    const v = ctx.input.axis('vertical');
    const jump = ctx.input.key('Space') || ctx.input.gamepadButton(0);
    // plane 'side': left/right along the world axis nearest the screen's right; the other one is depth.
    const sideOnX = Math.abs(right.x) >= Math.abs(right.z);
    sideAxis.set(sideOnX ? Math.sign(right.x) : 0, 0, sideOnX ? 0 : Math.sign(right.z));
    let topReady = false;

    for (const e of entities) {
      const c = e.get(CharacterController);
      const collider = ctx.physics.colliderOf(e);
      if (!collider) continue;
      const object = e.object3d;
      const state = stateOf(e);
      // Moved by someone else (editor, respawn): forget the old fall speed.
      if (state.last && object.position.distanceToSquared(state.last) > 1e-8) state.verticalVelocity = 0;

      // Axes it must not move along: RigidBody locks, and the depth in a side-scroller.
      const rb = e.tryGet<ComponentValues<typeof RigidBody>>(RigidBody);
      const locks = rb ? lockedAxes(rb) : null;
      const side = c.plane === 'side';
      const keepX = !!locks?.[0] || (side && !sideOnX);
      const keepY = !!locks?.[1];
      const keepZ = !!locks?.[2] || (side && sideOnX);

      if (side) move.copy(sideAxis).multiplyScalar(Math.max(-1, Math.min(1, h)));
      else if (c.plane === 'top') {
        if (!topReady) {
          screenUpOnGround(ctx, topForward);
          topRight.crossVectors(topForward, UP);
          topReady = true;
        }
        move.copy(topRight).multiplyScalar(h).addScaledVector(topForward, v);
      } else move.copy(right).multiplyScalar(h).addScaledVector(forward, v);
      if (keepX) move.x = 0;
      if (keepZ) move.z = 0;
      if (move.lengthSq() > 1) move.normalize();
      move.multiplyScalar(c.speed);

      if (state.grounded && jump) state.verticalVelocity = c.jumpSpeed;
      state.verticalVelocity -= c.gravity * ctx.dt;
      if (keepY) state.verticalVelocity = 0;

      const filter = ctx.physics.rapier.QueryFilterFlags.EXCLUDE_SENSORS;
      const desired = { x: move.x * ctx.dt, y: state.verticalVelocity * ctx.dt, z: move.z * ctx.dt };
      const lift = state.verticalVelocity <= 0 && !keepY ? liftOutOfGround(world, collider, filter) : 0;
      if (lift > 0) desired.y = lift; // Back out of the margin first; it falls again from there.
      if (keepX) desired.x = 0;
      if (keepZ) desired.z = 0;
      cc.computeColliderMovement(collider, desired, filter);
      const moved = cc.computedMovement();
      state.grounded = cc.computedGrounded();
      if (state.grounded && state.verticalVelocity < 0) state.verticalVelocity = 0;
      if (state.verticalVelocity > 0 && moved.y < desired.y - 1e-4) state.verticalVelocity = 0; // Bumped a ceiling.

      // Kinematic bodies are parented to the scene root, so local == world. Kept axes stay exactly.
      if (!keepX) object.position.x += moved.x;
      if (!keepY) object.position.y += moved.y;
      if (!keepZ) object.position.z += moved.z;

      if (move.lengthSq() > 1e-4) {
        facing.setFromAxisAngle(UP, Math.atan2(move.x, move.z));
        object.quaternion.rotateTowards(facing, c.turnSpeed * ctx.dt);
      }
      state.last = (state.last ?? new Vector3()).copy(object.position);
    }
  },
});
