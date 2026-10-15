// Physics interfaces. Systems see `PhysicsAPI` as `ctx.physics`; the game loop
// drives a `PhysicsBackend`. Rapier is the only backend today (rapier-backend.ts).

import type RAPIER from '@dimforge/rapier3d-compat';
import type { Vector3 } from 'three';
import type { Entity } from '../ecs/entity';
import type { World } from '../ecs/world';
import type { RotationInput, Vec3Input } from '../math';

export type Rapier = typeof RAPIER;

export interface Vec3Like {
  x: number;
  y: number;
  z: number;
}

/** A collision or sensor overlap that started or ended during this frame. */
export interface CollisionEvent {
  a: Entity;
  b: Entity;
  /** `true` when contact/overlap began, `false` when it ended. */
  started: boolean;
  /** `true` if either collider is a sensor. */
  sensor: boolean;
}

export interface RaycastOptions {
  /** Default 1000 m. */
  maxDistance?: number;
  /** Ignore this entity's body (e.g. the shooter). */
  exclude?: Entity;
  /** Hit sensor colliders too. Default false. */
  includeSensors?: boolean;
}

export interface RaycastHit {
  /** The entity owning the hit collider. */
  entity: Entity;
  point: Vector3;
  normal: Vector3;
  distance: number;
}

/** What systems use through `ctx.physics`. */
export interface PhysicsAPI {
  /** The Rapier module (`RigidBodyDesc`, `ColliderDesc`, `Ray`, ...). */
  readonly rapier: Rapier;
  /** The Rapier world for the current scene (`null` before the first scene loads). */
  readonly world: RAPIER.World | null;
  /** Collision start/end events from this frame's physics steps. */
  readonly collisions: readonly CollisionEvent[];
  /** Number of physics bodies. */
  readonly bodyCount: number;
  /** World gravity (m/s², Y-up). */
  getGravity(): Vector3;
  /** `[x, y, z]` or `{ x, y, z }`. */
  setGravity(gravity: Vec3Input): void;
  /**
   * Closest hit along a ray, or `null`. `direction` need not be normalized. Both take `[x, y, z]`,
   * `{ x, y, z }` or a `Vector3`. Like all Rapier scene queries it sees colliders as of the last physics step.
   */
  raycast(origin: Vec3Input, direction: Vec3Input, options?: RaycastOptions): RaycastHit | null;
  /** The entity's Rapier body, or `null` if it has no RigidBody/Collider. */
  bodyOf(entity: Entity): RAPIER.RigidBody | null;
  /** The entity's Rapier collider, or `null`. */
  colliderOf(entity: Entity): RAPIER.Collider | null;
  /**
   * Moves an entity instantly (world space) and zeroes its velocity. `position` is `[x, y, z]`,
   * `{ x, y, z }` or a `Vector3`; `rotation` a quaternion (`[x, y, z, w]`, `{ x, y, z, w }`,
   * `Quaternion`) or Euler radians. Without arguments it syncs the body to the object's transform.
   */
  teleport(entity: Entity, position?: Vec3Input | null, rotation?: RotationInput | null): void;
  /** Rebuild the entity's body after changing RigidBody/Collider data or its scale. */
  rebuild(entity: Entity): void;
}

/** What the game loop needs from a physics engine. */
export interface PhysicsBackend extends PhysicsAPI {
  init(): Promise<void>;
  /** Start a fresh simulation for `world` (scene load or reset). */
  reset(world: World, gravity: Vec3Input): void;
  /** Clear per-frame collision events. */
  beginFrame(): void;
  /** Push kinematic/fixed transforms, step by `dt`, write dynamic transforms back. */
  step(dt: number): void;
  dispose(): void;
}
