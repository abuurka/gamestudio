// Live edits from the editor (docs/SPEC.md §5.4). Edits are applied to the
// running world and also recorded, so `play.stop` (which rebuilds the scene
// from the cached export) keeps what the user changed in Blender.

import { Matrix4, Quaternion, Vector3, type Object3D } from 'three';
import type { Entity } from '../ecs/entity';
import type { World } from '../ecs/world';
import type { PhysicsAPI } from '../physics/backend';
import { fillDefaults } from '../schema/registry';

export interface TransformItem {
  id: string;
  /** Position, Y-up, local to the authoring parent. */
  p?: [number, number, number];
  /** Rotation quaternion [x, y, z, w]. */
  r?: [number, number, number, number];
  s?: [number, number, number];
}

export type ComponentSet = Record<string, Record<string, unknown>>;

const m = new Matrix4();
const parentInverse = new Matrix4();
const pos = new Vector3();
const quat = new Quaternion();
const scl = new Vector3();

/** Applies a local-to-authoring-parent transform and teleports the physics body. */
export function applyTransform(world: World, physics: PhysicsAPI, item: TransformItem): boolean {
  const entity = world.get(item.id);
  if (!entity) return false;
  placeObject(entity.object3d, world.get(entity.parentId)?.object3d, item);
  physics.teleport(entity);
  return true;
}

/**
 * Puts `object` at a pose given local to its authoring parent (`entity.transform`: live edits, and the
 * manifest's poses of objects that moved after their GLB was written). Missing parts keep the current pose.
 */
export function placeObject(object: Object3D, authoringParent: Object3D | undefined, item: Omit<TransformItem, 'id'>): void {
  // Current pose relative to the authoring parent fills in any missing parts.
  object.updateWorldMatrix(true, false);
  m.copy(object.matrixWorld);
  if (authoringParent) m.premultiply(parentInverse.copy(authoringParent.matrixWorld).invert());
  m.decompose(pos, quat, scl);
  if (item.p) pos.fromArray(item.p);
  if (item.r) quat.fromArray(item.r).normalize();
  if (item.s) scl.fromArray(item.s);

  // Authoring-parent space → world → the object's actual parent space
  // (they differ when physics reparented the object to the scene root).
  m.compose(pos, quat, scl);
  if (authoringParent) m.premultiply(authoringParent.matrixWorld);
  if (object.parent) {
    object.parent.updateWorldMatrix(true, false);
    m.premultiply(parentInverse.copy(object.parent.matrixWorld).invert());
  }
  m.decompose(object.position, object.quaternion, object.scale);
  object.updateMatrixWorld(true);
}

/** Replaces an entity's full component set, touching only components whose values changed. */
export function replaceComponents(entity: Entity, components: ComponentSet): string[] {
  const changed: string[] = [];
  for (const name of entity.components()) {
    if (!(name in components)) {
      entity.remove(name);
      changed.push(name);
    }
  }
  for (const [name, values] of Object.entries(components)) {
    const next = fillDefaults(name, values ?? {}) as Record<string, unknown>;
    const current = entity.tryGet<Record<string, unknown>>(name);
    if (!current || JSON.stringify(current) !== JSON.stringify(next)) {
      entity.add(name, next);
      changed.push(name);
    }
  }
  return changed;
}

/** Records editor edits since the last scene load, to re-apply after a reset. */
export class EditLog {
  private readonly transforms = new Map<string, TransformItem>();
  private readonly components = new Map<string, ComponentSet>();
  private readonly visibility = new Map<string, boolean>();
  private readonly removed = new Set<string>();
  /** Game-shader values by material name (`material.values`), merged. */
  private readonly materials = new Map<string, Record<string, unknown>>();

  recordTransform(item: TransformItem): void {
    const prev = this.transforms.get(item.id);
    this.transforms.set(item.id, { ...prev, ...item });
  }
  recordComponents(id: string, components: ComponentSet): void {
    this.components.set(id, structuredClone(components));
  }
  recordVisibility(id: string, visible: boolean): void {
    this.visibility.set(id, visible);
  }
  recordRemove(id: string): void {
    this.removed.add(id);
  }
  recordMaterial(name: string, values: Record<string, unknown>): void {
    this.materials.set(name, { ...this.materials.get(name), ...structuredClone(values) });
  }

  /** The recorded game-shader values, by material name (applied after the shaders are bound). */
  materialValues(): Array<[string, Record<string, unknown>]> {
    return [...this.materials].map(([name, values]) => [name, structuredClone(values)]);
  }

  clear(): void {
    this.transforms.clear();
    this.components.clear();
    this.visibility.clear();
    this.removed.clear();
    this.materials.clear();
  }

  /** Re-applies every recorded edit to a freshly built world. */
  applyAll(world: World, physics: PhysicsAPI): void {
    for (const id of this.removed) world.get(id)?.destroy();
    for (const [id, set] of this.components) {
      const entity = world.get(id);
      if (entity) replaceComponents(entity, structuredClone(set));
    }
    for (const item of this.transforms.values()) applyTransform(world, physics, item);
    for (const [id, visible] of this.visibility) {
      const entity = world.get(id);
      if (entity) entity.object3d.visible = visible;
    }
  }
}
