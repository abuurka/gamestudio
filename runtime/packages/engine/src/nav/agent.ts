// NavAgent: walks an entity along navmesh paths with Rapier's kinematic character mover, set up like
// the CharacterController's (slopes, steps, snapping to the ground, pushing dynamic bodies).
// Runs in the fixed phase: it moves the object3d, and the physics step then moves the kinematic body.

import type RAPIER from '@dimforge/rapier3d-compat';
import { Quaternion, Vector3 } from 'three';
import type { SystemContext } from '../context';
import type { Entity } from '../ecs/entity';
import { defineSystem } from '../ecs/system';
import { NavAgent } from '../schema/builtins';
import type { ComponentValues } from '../schema/registry';
import { agentPaths } from './nav';

type AgentValues = ComponentValues<typeof NavAgent>;

interface AgentState {
  /** Waypoints still ahead (the first is the one it walks to now). */
  path: Vector3[];
  /** Horizontal velocity, m/s. */
  velocity: Vector3;
  verticalVelocity: number;
  /** Seconds until the next path. */
  repathIn: number;
  /** The goal the path was made for (an entity id, or a point). */
  goalKey: string;
  warnedNoNav: boolean;
}

const UP = new Vector3(0, 1, 0);
const OFFSET = 0.02;
/** Closer than this (m, sideways) to a waypoint counts as there. */
const REACHED = 0.15;
const states = new WeakMap<Entity, AgentState>();
const movers = new WeakMap<RAPIER.World, RAPIER.KinematicCharacterController>();

const goal = new Vector3();
const desired = new Vector3();
const push = new Vector3();
const offset = new Vector3();
const facing = new Quaternion();

function stateOf(e: Entity): AgentState {
  let state = states.get(e);
  if (!state) {
    state = { path: [], velocity: new Vector3(), verticalVelocity: 0, repathIn: 0, goalKey: '', warnedNoNav: false };
    states.set(e, state);
  }
  return state;
}

function moverFor(world: RAPIER.World): RAPIER.KinematicCharacterController {
  let mover = movers.get(world);
  if (!mover) {
    mover = world.createCharacterController(OFFSET);
    mover.setUp({ x: 0, y: 1, z: 0 });
    mover.setMaxSlopeClimbAngle((50 * Math.PI) / 180);
    mover.setMinSlopeSlideAngle((35 * Math.PI) / 180);
    mover.enableAutostep(0.35, 0.2, false);
    mover.enableSnapToGround(0.3);
    mover.setApplyImpulsesToDynamicBodies(true);
    movers.set(world, mover);
  }
  return mover;
}

/** Where the agent is going (into `out`) and a key that changes when the goal does; `null`: nowhere. */
function goalOf(ctx: SystemContext, a: AgentValues, out: Vector3): string | null {
  if (a.target) {
    const target = ctx.world.get(a.target);
    if (!target?.alive) return null;
    target.object3d.getWorldPosition(out);
    return `e:${target.id}`;
  }
  if (!a.useDestination) return null;
  out.fromArray(a.destination);
  return `p:${a.destination.join(',')}`;
}

/** Sideways length of the path from `from` through the waypoints. */
function pathLength(from: Vector3, path: readonly Vector3[]): number {
  let length = 0;
  let x = from.x;
  let z = from.z;
  for (const p of path) {
    length += Math.hypot(p.x - x, p.z - z);
    x = p.x;
    z = p.z;
  }
  return length;
}

/**
 * Moves entities with `NavAgent` to their `target` entity or `destination` along navmesh paths
 * (`ctx.nav`). Without a navmesh they walk straight at the goal and slide along what they hit.
 */
export const NavAgentSystem = defineSystem({
  name: 'NavAgentSystem',
  query: [NavAgent],
  phase: 'fixed',

  onRemove(_ctx, e) {
    states.delete(e);
    agentPaths.delete(e);
  },

  update(ctx, agents) {
    const world = ctx.physics.world;
    if (!world || agents.length === 0) return;
    const mover = moverFor(world);
    const filter = ctx.physics.rapier.QueryFilterFlags.EXCLUDE_SENSORS;
    const gravity = -ctx.physics.getGravity().y;
    const dt = ctx.dt;

    for (const e of agents) {
      const a = e.get(NavAgent);
      const collider = ctx.physics.colliderOf(e);
      if (!collider) continue; // its body appears with the next physics step
      const state = stateOf(e);
      const pos = e.object3d.position; // kinematic bodies sit at the scene root: local = world

      // The path: a new one when the goal changes, and every autoRepath seconds while it has one.
      const key = goalOf(ctx, a, goal);
      state.repathIn -= dt;
      if (key === null) {
        state.path = [];
        state.goalKey = '';
      } else if (key !== state.goalKey || (a.autoRepath > 0 && state.repathIn <= 0) || (state.path.length === 0 && state.repathIn <= 0)) {
        state.goalKey = key;
        state.repathIn = a.autoRepath > 0 ? a.autoRepath : 0.5;
        if (ctx.nav.ready) {
          // From the agent's spot on the navmesh (dropped below once it is there) to the goal, or as
          // close to it as the agent can get.
          state.path = ctx.nav.path(pos, goal, { partial: true }) ?? [];
        } else {
          if (!state.warnedNoNav) {
            state.warnedNoNav = true;
            ctx.log.warn(`NavAgent "${e.name}": no navmesh yet, walking straight (add a NavMesh component to the level)`);
            void ctx.nav.rebuild();
          }
          state.path = [goal.clone()];
        }
      } else if (!ctx.nav.ready && state.path.length === 1) {
        state.path[0]!.copy(goal); // straight at a moving goal
      }
      while (state.path.length > 1 && Math.hypot(state.path[0]!.x - pos.x, state.path[0]!.z - pos.z) < Math.max(REACHED, a.speed * dt)) {
        state.path.shift();
      }
      agentPaths.set(e, state.path);

      // Desired velocity: along the path, slowing down to stop stoppingDistance short of its end.
      const remaining = state.path.length ? pathLength(pos, state.path) : -1;
      a.remaining = remaining < 0 ? -1 : Math.round(remaining * 100) / 100;
      desired.set(0, 0, 0);
      if (remaining > a.stoppingDistance) {
        const next = state.path[0]!;
        desired.set(next.x - pos.x, 0, next.z - pos.z);
        const length = desired.length();
        let speed = a.speed;
        if (a.acceleration > 0) speed = Math.min(speed, Math.sqrt(2 * a.acceleration * (remaining - a.stoppingDistance)));
        if (length > 1e-6) desired.multiplyScalar(speed / length);
      }
      if (a.separation > 0) {
        push.set(0, 0, 0);
        for (const other of agents) {
          if (other === e) continue;
          offset.subVectors(pos, other.object3d.position).setY(0);
          const d = offset.length();
          if (d >= a.separation || d < 1e-6) continue;
          push.addScaledVector(offset, (1 - d / a.separation) / d);
        }
        if (push.lengthSq() > 0) {
          desired.addScaledVector(push, a.speed);
          if (desired.length() > a.speed) desired.setLength(a.speed);
        }
      }

      // Accelerate toward it, fall with gravity, and let the mover slide along walls and others.
      const velocity = state.velocity;
      if (a.acceleration > 0) {
        offset.subVectors(desired, velocity);
        const change = offset.length();
        const max = a.acceleration * dt;
        if (change > max) offset.multiplyScalar(max / change);
        velocity.add(offset);
      } else {
        velocity.copy(desired);
      }
      state.verticalVelocity -= gravity * dt;
      mover.computeColliderMovement(collider, { x: velocity.x * dt, y: state.verticalVelocity * dt, z: velocity.z * dt }, filter);
      const moved = mover.computedMovement();
      if (mover.computedGrounded() && state.verticalVelocity < 0) state.verticalVelocity = 0;
      pos.set(pos.x + moved.x, pos.y + moved.y, pos.z + moved.z);

      if (a.turnSpeed > 0 && velocity.lengthSq() > 0.01) {
        facing.setFromAxisAngle(UP, Math.atan2(velocity.x, velocity.z));
        e.object3d.quaternion.rotateTowards(facing, a.turnSpeed * dt);
      }
    }
  },
});
