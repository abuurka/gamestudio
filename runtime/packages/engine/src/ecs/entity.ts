// Entity handle (docs/SPEC.md §6.4). Component data lives on the handle as
// plain objects keyed by component name; the World keeps the indexes.

import type { Object3D } from 'three';
import type { ComponentType } from '../schema/registry';
import type { World } from './world';

/** A component handle or its name. */
export type ComponentRef<V = any> = ComponentType<V> | string;

export function componentName(ref: ComponentRef): string {
  return typeof ref === 'string' ? ref : ref.name;
}

export class Entity {
  /** `gs_id` for authored entities, `rt_<n>` for runtime-spawned ones. */
  readonly id: string;
  /** The Blender object name (from the manifest), or the spawn name. */
  name: string;
  /** The Three.js object that represents this entity. */
  readonly object3d: Object3D;
  /** Authoring parent's id from the manifest (`null` for roots). */
  readonly parentId: string | null;
  /** `false` after `destroy()`. */
  alive = true;

  /** @internal Component data by name. Use get/add/remove. */
  readonly _data = new Map<string, Record<string, unknown>>();
  /** @internal */
  readonly _world: World;

  /** @internal Created by World.spawn / the scene loader. */
  constructor(world: World, id: string, name: string, object3d: Object3D, parentId: string | null) {
    this._world = world;
    this.id = id;
    this.name = name;
    this.object3d = object3d;
    this.parentId = parentId;
  }

  /** The component's live data. Throws if the entity doesn't have it (use `tryGet` to probe). */
  get<V>(type: ComponentRef<V>): V {
    const data = this._data.get(componentName(type));
    if (!data) throw new Error(`Entity "${this.name}" (${this.id}) has no component ${componentName(type)}`);
    return data as V;
  }

  /** The component's live data, or `undefined`. */
  tryGet<V>(type: ComponentRef<V>): V | undefined {
    return this._data.get(componentName(type)) as V | undefined;
  }

  has(type: ComponentRef): boolean {
    return this._data.has(componentName(type));
  }

  /**
   * Adds the component, filling unspecified fields with schema defaults. If the
   * entity already has it, the data is replaced. Returns the live data.
   */
  add<V>(type: ComponentRef<V>, values?: Partial<V>): V {
    return this._world._addComponent(this, componentName(type), (values ?? {}) as Record<string, unknown>) as V;
  }

  /** Removes the component. Returns whether it was present. */
  remove(type: ComponentRef): boolean {
    return this._world._removeComponent(this, componentName(type));
  }

  /** Names of the attached components, in insertion order. */
  components(): string[] {
    return [...this._data.keys()];
  }

  /** Removes the entity, its object3d and any child entities. */
  destroy(): void {
    this._world._destroy(this);
  }
}
