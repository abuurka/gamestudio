// Rapier implementation of PhysicsBackend.
//
// Ownership of transforms:
// - dynamic bodies: Rapier is authoritative; poses are written to the object3d after each step.
// - kinematic and fixed bodies: the object3d is authoritative; its world pose is pushed into
//   the body before each step (kinematic bodies via next-kinematic targets so they push others).
// Dynamic and kinematic objects are reparented to the world root at build time (keeping their
// world transform) so writing a pose back is a plain local assignment.

import RAPIER from '@dimforge/rapier3d-compat';
import { Matrix4, Quaternion, Vector3, type Object3D } from 'three';
import type { Entity } from '../ecs/entity';
import type { World } from '../ecs/world';
import { toQuaternion, toVector3, type RotationInput, type Vec3Input } from '../math';
import { Collider as ColliderC, RigidBody as RigidBodyC } from '../schema/builtins';
import type { ComponentValues } from '../schema/registry';
import type {
  CollisionEvent,
  PhysicsBackend,
  RaycastHit,
  RaycastOptions,
} from './backend';
import {
  collectLocalGeometry,
  colliderSkips,
  shapeFromGeometry,
  type PartSpec,
  type ShapeKind,
  type ShapeSpec,
} from './shapes';

type BodyKind = 'dynamic' | 'fixed' | 'kinematic';

interface BodyRecord {
  entity: Entity;
  kind: BodyKind;
  body: RAPIER.RigidBody;
  collider: RAPIER.Collider | null;
  /** World scale the collider was sized for; a change triggers a rebuild. */
  scale: Vector3;
  /**
   * The collider is the same at any turn about the body's up axis (a capsule or sphere on that
   * axis), so a kinematic or fixed body keeps only the swing of the object's rotation: turning in
   * place changes nothing in the simulation (see swingOnly).
   */
  turnFree: boolean;
  /**
   * RigidBody.lockX/Y/Z of a dynamic or kinematic body, with the world position those axes keep
   * (set at build and by teleport). `null` when nothing is locked.
   */
  locks: { axes: readonly [boolean, boolean, boolean]; anchor: Vector3 } | null;
}

let rapierReady: Promise<void> | null = null;

/** Initializes the Rapier WASM module once per page/process. */
export function initRapier(): Promise<void> {
  return (rapierReady ??= RAPIER.init());
}

const EPS = 1e-6;
const tmpPos = new Vector3();
const tmpQuat = new Quaternion();
const tmpScale = new Vector3();
const tmpMat = new Matrix4();

export class RapierPhysics implements PhysicsBackend {
  readonly rapier = RAPIER;
  world: RAPIER.World | null = null;
  collisions: CollisionEvent[] = [];

  private ecs: World | null = null;
  private unsubscribe: (() => void) | null = null;
  private eventQueue: RAPIER.EventQueue | null = null;
  private readonly records = new Map<Entity, BodyRecord>();
  private readonly byCollider = new Map<number, Entity>();
  private readonly dirty = new Set<Entity>();

  async init(): Promise<void> {
    await initRapier();
  }

  get bodyCount(): number {
    return this.records.size;
  }

  reset(ecs: World, gravity: Vec3Input): void {
    this.disposeWorld();
    this.ecs = ecs;
    const g = toVector3(gravity);
    this.world = new RAPIER.World({ x: g.x, y: g.y, z: g.z });
    this.eventQueue = new RAPIER.EventQueue(true);
    this.unsubscribe = ecs.onChange((event) => {
      if (event.type === 'destroy') {
        this.dirty.delete(event.entity);
        this.removeBody(event.entity);
      } else if ((event.type === 'add' || event.type === 'remove') && isPhysicsComponent(event.component)) {
        this.dirty.add(event.entity);
      }
    });
    for (const entity of ecs.all()) if (entity.has(RigidBodyC) || entity.has(ColliderC)) this.dirty.add(entity);
  }

  getGravity(): Vector3 {
    const g = this.world?.gravity ?? { x: 0, y: -9.81, z: 0 };
    return new Vector3(g.x, g.y, g.z);
  }

  setGravity(gravity: Vec3Input): void {
    const g = toVector3(gravity);
    if (this.world) this.world.gravity = { x: g.x, y: g.y, z: g.z };
  }

  beginFrame(): void {
    this.collisions = [];
  }

  rebuild(entity: Entity): void {
    this.dirty.add(entity);
  }

  bodyOf(entity: Entity): RAPIER.RigidBody | null {
    this.flushDirty();
    return this.records.get(entity)?.body ?? null;
  }

  colliderOf(entity: Entity): RAPIER.Collider | null {
    this.flushDirty();
    return this.records.get(entity)?.collider ?? null;
  }

  step(dt: number): void {
    const world = this.world;
    if (!world) return;
    this.flushDirty();
    this.pushObjectPoses();
    world.timestep = dt;
    world.step(this.eventQueue!);
    this.holdDynamicLocks();
    this.writeDynamicPoses();
    this.eventQueue!.drainCollisionEvents((h1, h2, started) => {
      const a = this.byCollider.get(h1);
      const b = this.byCollider.get(h2);
      if (!a || !b) return; // One side was removed this step.
      const sensor = !!(world.getCollider(h1)?.isSensor() || world.getCollider(h2)?.isSensor());
      this.collisions.push({ a, b, started, sensor });
    });
  }

  raycast(origin: Vec3Input, direction: Vec3Input, options: RaycastOptions = {}): RaycastHit | null {
    const world = this.world;
    if (!world) return null;
    this.flushDirty();
    const from = toVector3(origin);
    const dir = toVector3(direction);
    if (dir.lengthSq() === 0) return null;
    dir.normalize();
    const ray = new RAPIER.Ray({ x: from.x, y: from.y, z: from.z }, { x: dir.x, y: dir.y, z: dir.z });
    const exclude = options.exclude ? this.records.get(options.exclude)?.body : undefined;
    const flags = options.includeSensors ? undefined : RAPIER.QueryFilterFlags.EXCLUDE_SENSORS;
    const hit = world.castRayAndGetNormal(ray, options.maxDistance ?? 1000, true, flags, undefined, undefined, exclude);
    if (!hit) return null;
    const entity = this.byCollider.get(hit.collider.handle);
    if (!entity) return null;
    const point = from.clone().addScaledVector(dir, hit.timeOfImpact);
    return { entity, point, normal: new Vector3(hit.normal.x, hit.normal.y, hit.normal.z), distance: hit.timeOfImpact };
  }

  teleport(entity: Entity, position?: Vec3Input | null, rotation?: RotationInput | null): void {
    const object = entity.object3d;
    if (position || rotation) {
      object.updateWorldMatrix(true, false);
      object.matrixWorld.decompose(tmpPos, tmpQuat, tmpScale);
      if (position) toVector3(position, tmpPos);
      if (rotation) toQuaternion(rotation, tmpQuat);
      setWorldPose(object, tmpPos, tmpQuat);
    }
    this.flushDirty();
    const record = this.records.get(entity);
    if (!record) return;
    object.updateWorldMatrix(true, false);
    object.matrixWorld.decompose(tmpPos, tmpQuat, tmpScale);
    const { body } = record;
    record.locks?.anchor.copy(tmpPos); // Teleporting is how a locked axis moves.
    if (record.turnFree) swingOnly(tmpQuat);
    body.setTranslation(tmpPos, true);
    body.setRotation(tmpQuat, true);
    if (record.kind === 'kinematic') {
      body.setNextKinematicTranslation(tmpPos);
      body.setNextKinematicRotation(tmpQuat);
    } else if (record.kind === 'dynamic') {
      body.setLinvel({ x: 0, y: 0, z: 0 }, true);
      body.setAngvel({ x: 0, y: 0, z: 0 }, true);
    }
    if (!approxEqual(tmpScale, record.scale)) this.dirty.add(entity);
  }

  dispose(): void {
    this.disposeWorld();
  }

  // -------------------------------------------------------------------------

  private disposeWorld(): void {
    this.unsubscribe?.();
    this.unsubscribe = null;
    this.records.clear();
    this.byCollider.clear();
    this.dirty.clear();
    this.collisions = [];
    this.eventQueue?.free();
    this.eventQueue = null;
    this.world?.free();
    this.world = null;
  }

  private flushDirty(): void {
    if (this.dirty.size === 0) return;
    const pending = [...this.dirty];
    this.dirty.clear();
    for (const entity of pending) {
      this.removeBody(entity);
      if (entity.alive) this.buildBody(entity);
    }
  }

  private removeBody(entity: Entity): void {
    const record = this.records.get(entity);
    if (!record || !this.world) return;
    if (record.collider) this.byCollider.delete(record.collider.handle);
    this.world.removeRigidBody(record.body); // Also removes attached colliders.
    this.records.delete(entity);
  }

  private buildBody(entity: Entity): void {
    const world = this.world;
    const ecs = this.ecs;
    if (!world || !ecs) return;
    const rb = entity.tryGet<ComponentValues<typeof RigidBodyC>>(RigidBodyC);
    const col = entity.tryGet<ComponentValues<typeof ColliderC>>(ColliderC);
    if (!rb && !col) return;

    const kind: BodyKind = rb?.type ?? 'fixed';
    const object = entity.object3d;
    if (kind !== 'fixed' && object.parent !== ecs.root) ecs.root.attach(object);
    object.updateWorldMatrix(true, false);
    const pos = new Vector3();
    const quat = new Quaternion();
    const scale = new Vector3();
    object.matrixWorld.decompose(pos, quat, scale);

    const desc =
      kind === 'dynamic'
        ? RAPIER.RigidBodyDesc.dynamic()
        : kind === 'kinematic'
          ? RAPIER.RigidBodyDesc.kinematicPositionBased()
          : RAPIER.RigidBodyDesc.fixed();
    desc.setTranslation(pos.x, pos.y, pos.z).setRotation(quat);
    const axes = rb && kind !== 'fixed' ? lockedAxes(rb) : null;
    if (rb) {
      desc
        .setLinearDamping(rb.linearDamping)
        .setAngularDamping(rb.angularDamping)
        .setGravityScale(rb.gravityScale)
        .setCcdEnabled(rb.ccd);
      if (rb.lockRotation) desc.lockRotations();
      if (axes && kind === 'dynamic') {
        desc.enabledTranslations(!axes[0], !axes[1], !axes[2]);
        // One locked axis: a 2D body in the other two, so it only turns about the locked one.
        if (!rb.lockRotation && axes.filter(Boolean).length === 1) desc.enabledRotations(axes[0], axes[1], axes[2]);
      }
      if (!col && kind === 'dynamic') desc.setAdditionalMass(Math.max(rb.mass, 0.001));
    }
    const body = world.createRigidBody(desc);

    let collider: RAPIER.Collider | null = null;
    if (col) {
      // Trimeshes have no volume, so dynamic "mesh" colliders become convex hulls.
      const kindOfShape: ShapeKind = col.shape === 'mesh' && kind === 'dynamic' ? 'convex' : col.shape;
      const needsVertices = kindOfShape === 'convex' || kindOfShape === 'mesh';
      // Child entities (a lamp on a crate) don't count, unless they are the body (see colliderSkips).
      const skip = colliderSkips(object, (o) => {
        const child = ecs.entityOf(o);
        if (child?.object3d !== o) return 'none';
        return child.has(RigidBodyC) || child.has(ColliderC) ? 'physics' : 'plain';
      });
      const geometry = collectLocalGeometry(object, skip, needsVertices);
      const shape = shapeFromGeometry(kindOfShape, geometry, scale);
      const describe = (spec: ShapeSpec): RAPIER.ColliderDesc => {
        const colliderDesc = colliderDescFor(spec)
          .setFriction(col.friction)
          .setRestitution(col.restitution)
          .setSensor(col.sensor)
          .setActiveEvents(RAPIER.ActiveEvents.COLLISION_EVENTS);
        // Sensors must also report overlaps with kinematic and fixed bodies.
        if (col.sensor) colliderDesc.setActiveCollisionTypes(RAPIER.ActiveCollisionTypes.ALL);
        if (rb && kind === 'dynamic' && rb.mass > 0) colliderDesc.setMass(rb.mass);
        return colliderDesc;
      };
      try {
        collider = world.createCollider(describe(shape), body);
      } catch {
        // A hull of degenerate points (all on a line) has no shape: use its bounding box instead.
        collider = world.createCollider(describe(withoutHulls(shape)), body);
      }
      this.byCollider.set(collider.handle, entity);
    }
    const turnFree = kind !== 'dynamic' && !!collider && turnsFreely(collider);
    if (turnFree) {
      body.setRotation(swingOnly(quat), false);
      if (kind === 'kinematic') body.setNextKinematicRotation(quat);
    }
    const locks = axes ? { axes, anchor: pos.clone() } : null;
    this.records.set(entity, { entity, kind, body, collider, scale, turnFree, locks });
  }

  /** Object3D → body for kinematic and fixed bodies, when the object moved. */
  private pushObjectPoses(): void {
    for (const record of this.records.values()) {
      if (record.kind === 'dynamic') continue;
      const object = record.entity.object3d;
      object.updateWorldMatrix(true, false);
      object.matrixWorld.decompose(tmpPos, tmpQuat, tmpScale);
      if (!approxEqual(tmpScale, record.scale)) {
        this.dirty.add(record.entity);
        continue;
      }
      const { body } = record;
      if (record.locks && pinLocked(record.locks, tmpPos)) setWorldPosition(object, tmpPos);
      if (record.turnFree) swingOnly(tmpQuat);
      if (record.kind === 'kinematic') {
        body.setNextKinematicTranslation(tmpPos);
        body.setNextKinematicRotation(tmpQuat);
        continue;
      }
      const t = body.translation();
      const r = body.rotation();
      if (
        Math.abs(t.x - tmpPos.x) > EPS || Math.abs(t.y - tmpPos.y) > EPS || Math.abs(t.z - tmpPos.z) > EPS ||
        Math.abs(r.x - tmpQuat.x) > EPS || Math.abs(r.y - tmpQuat.y) > EPS ||
        Math.abs(r.z - tmpQuat.z) > EPS || Math.abs(r.w - tmpQuat.w) > EPS
      ) {
        body.setTranslation(tmpPos, true);
        body.setRotation(tmpQuat, true);
      }
    }
    this.flushDirty();
  }

  /**
   * Rapier keeps a locked axis still under forces and contacts, but not against a velocity set from
   * code (`setLinvel`): put such a body back on its locked axes, so they stay exactly constant.
   */
  private holdDynamicLocks(): void {
    for (const record of this.records.values()) {
      if (record.kind !== 'dynamic' || !record.locks) continue;
      const { body } = record;
      const t = body.translation();
      tmpPos.set(t.x, t.y, t.z);
      if (!pinLocked(record.locks, tmpPos, true)) continue;
      body.setTranslation(tmpPos, false);
      const v = body.linvel();
      const [x, y, z] = record.locks.axes;
      body.setLinvel({ x: x ? 0 : v.x, y: y ? 0 : v.y, z: z ? 0 : v.z }, false);
    }
  }

  /** Body → Object3D for dynamic bodies. */
  private writeDynamicPoses(): void {
    for (const record of this.records.values()) {
      if (record.kind !== 'dynamic' || record.body.isSleeping()) continue;
      const t = record.body.translation();
      const r = record.body.rotation();
      tmpPos.set(t.x, t.y, t.z);
      tmpQuat.set(r.x, r.y, r.z, r.w);
      if (record.locks) pinLocked(record.locks, tmpPos); // The authored value, not Rapier's 32-bit copy.
      setWorldPose(record.entity.object3d, tmpPos, tmpQuat);
    }
  }
}

function isPhysicsComponent(name: string): boolean {
  return name === RigidBodyC.name || name === ColliderC.name;
}

/** RigidBody.lockX/Y/Z as `[x, y, z]`, or `null` when no axis is locked. */
export function lockedAxes(rb: Partial<ComponentValues<typeof RigidBodyC>>): [boolean, boolean, boolean] | null {
  const axes: [boolean, boolean, boolean] = [!!rb.lockX, !!rb.lockY, !!rb.lockZ];
  return axes.some(Boolean) ? axes : null;
}

/**
 * Sets the locked axes of `position` to the anchor's; `true` if that changed it. `f32`: compare and
 * set as Rapier stores positions (32-bit floats), for positions read from or written to a body.
 */
function pinLocked(locks: NonNullable<BodyRecord['locks']>, position: Vector3, f32 = false): boolean {
  let changed = false;
  for (let i = 0; i < 3; i++) {
    if (!locks.axes[i]) continue;
    const keep = f32 ? Math.fround(locks.anchor.getComponent(i)) : locks.anchor.getComponent(i);
    if (position.getComponent(i) !== keep) {
      position.setComponent(i, keep);
      changed = true;
    }
  }
  return changed;
}

/** Sets an object's world position, keeping its rotation and scale. */
function setWorldPosition(object: Object3D, position: Vector3): void {
  const parent = object.parent;
  if (!parent || isIdentity(parent)) object.position.copy(position);
  else {
    parent.updateWorldMatrix(true, false);
    object.position.copy(parent.worldToLocal(position.clone()));
  }
  object.updateMatrix();
}

function approxEqual(a: Vector3, b: Vector3): boolean {
  return Math.abs(a.x - b.x) < 1e-4 && Math.abs(a.y - b.y) < 1e-4 && Math.abs(a.z - b.z) < 1e-4;
}

/** Sets an object's world position/rotation, keeping its scale. */
export function setWorldPose(object: Object3D, position: Vector3, quaternion: Quaternion): void {
  const parent = object.parent;
  if (!parent || isIdentity(parent)) {
    object.position.copy(position);
    object.quaternion.copy(quaternion);
  } else {
    parent.updateWorldMatrix(true, false);
    tmpMat.compose(position, quaternion, tmpScale.set(1, 1, 1));
    tmpMat.premultiply(new Matrix4().copy(parent.matrixWorld).invert());
    const s = object.scale.clone();
    tmpMat.decompose(object.position, object.quaternion, tmpScale);
    object.scale.copy(s);
  }
  object.updateMatrix();
}

function isIdentity(object: Object3D): boolean {
  return (
    object.parent === null &&
    object.position.lengthSq() === 0 &&
    object.quaternion.w === 1 &&
    object.scale.x === 1 && object.scale.y === 1 && object.scale.z === 1
  );
}

/** A capsule (upright, along Y) or a ball whose centre is on the body's Y axis: unchanged by turning about it. */
function turnsFreely(collider: RAPIER.Collider): boolean {
  const type = collider.shape.type;
  if (type !== RAPIER.ShapeType.Capsule && type !== RAPIER.ShapeType.Ball) return false;
  const at = collider.translationWrtParent();
  const rot = collider.rotationWrtParent();
  const upright = !rot || (Math.abs(rot.x) < 1e-6 && Math.abs(rot.z) < 1e-6);
  return !!at && Math.abs(at.x) < 1e-6 && Math.abs(at.z) < 1e-6 && upright;
}

/**
 * Removes the twist about the local Y axis from `q` (swing-twist decomposition), in place. The
 * physics of a body whose collider turnsFreely does not depend on that twist, so leaving it out
 * keeps a character's facing (aiming, turning in place) from touching the simulation at all.
 */
export function swingOnly(q: Quaternion): Quaternion {
  const length = Math.hypot(q.y, q.w);
  if (length < 1e-9) return q; // turned upside down: the twist is undefined
  const ty = q.y / length;
  const tw = q.w / length;
  // q * conjugate(twist), twist = (0, ty, 0, tw)
  const x = q.x * tw + q.z * ty;
  const y = q.y * tw - q.w * ty;
  const z = q.z * tw - q.x * ty;
  const w = q.w * tw + q.y * ty;
  return q.set(x, y, z, w).normalize();
}

function colliderDescFor(shape: ShapeSpec): RAPIER.ColliderDesc {
  let desc: RAPIER.ColliderDesc | null;
  switch (shape.kind) {
    case 'sphere':
      desc = RAPIER.ColliderDesc.ball(shape.radius);
      break;
    case 'capsule':
      desc = RAPIER.ColliderDesc.capsule(shape.halfHeight, shape.radius);
      break;
    case 'convex':
      desc = RAPIER.ColliderDesc.convexHull(shape.points);
      break;
    case 'mesh':
      desc = RAPIER.ColliderDesc.trimesh(shape.vertices, shape.indices);
      break;
    case 'compound':
      // One collider of many parts (GPU instances): positions and rotations are part-local.
      desc = RAPIER.ColliderDesc.compound(
        shape.parts.map((part) =>
          part.kind === 'box'
            ? new RAPIER.Cuboid(part.halfExtents.x, part.halfExtents.y, part.halfExtents.z)
            : new RAPIER.ConvexPolyhedron(part.points, null),
        ),
        shape.parts.map((part) => ({ x: part.center.x, y: part.center.y, z: part.center.z })),
        shape.parts.map((part) => ({ x: part.rotation.x, y: part.rotation.y, z: part.rotation.z, w: part.rotation.w })),
      );
      break;
    default:
      desc = RAPIER.ColliderDesc.cuboid(shape.halfExtents.x, shape.halfExtents.y, shape.halfExtents.z);
  }
  desc ??= RAPIER.ColliderDesc.cuboid(0.5, 0.5, 0.5);
  return desc.setTranslation(shape.center.x, shape.center.y, shape.center.z);
}

/** `shape` with every convex hull replaced by the box around its points (hulls of degenerate points fail). */
function withoutHulls(shape: ShapeSpec): ShapeSpec {
  if (shape.kind === 'convex') {
    const box = boundsOf(shape.points);
    return { kind: 'box', halfExtents: box.half, center: box.center.add(shape.center) };
  }
  if (shape.kind !== 'compound') return shape;
  const parts = shape.parts.map((part): PartSpec => {
    if (part.kind !== 'convex') return part;
    const box = boundsOf(part.points);
    return { kind: 'box', halfExtents: box.half, center: box.center.applyQuaternion(part.rotation).add(part.center), rotation: part.rotation };
  });
  return { ...shape, parts };
}

function boundsOf(points: Float32Array): { center: Vector3; half: Vector3 } {
  const min = new Vector3(Infinity, Infinity, Infinity);
  const max = new Vector3(-Infinity, -Infinity, -Infinity);
  const p = new Vector3();
  for (let i = 0; i + 2 < points.length; i += 3) {
    p.set(points[i]!, points[i + 1]!, points[i + 2]!);
    min.min(p);
    max.max(p);
  }
  if (min.x > max.x) return { center: new Vector3(), half: new Vector3(0.5, 0.5, 0.5) };
  const half = max.clone().sub(min).multiplyScalar(0.5);
  half.set(Math.max(half.x, 0.005), Math.max(half.y, 0.005), Math.max(half.z, 0.005));
  return { center: min.add(max).multiplyScalar(0.5), half };
}
