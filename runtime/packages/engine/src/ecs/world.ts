// A small ECS world: entity storage, name/object indexes and cached queries.
// Queries return cached arrays that are rebuilt (never mutated in place) when
// a relevant component is added or removed, so iterating while destroying is safe.

import { Group, Object3D } from 'three';
import { toVector3, type Vec3Input } from '../math';
import { fillDefaults, onComponentDefined } from '../schema/registry';
import { componentName, Entity, type ComponentRef } from './entity';

export type WorldEvent =
  | { type: 'spawn'; entity: Entity }
  | { type: 'add'; entity: Entity; component: string; replaced: boolean }
  | { type: 'remove'; entity: Entity; component: string }
  | { type: 'destroy'; entity: Entity };

export interface SpawnOptions {
  /** Defaults to a generated `rt_<n>` id. Must be unique. */
  id?: string;
  name?: string;
  /** Defaults to a new empty Group. */
  object3d?: Object3D;
  /** Parent entity or object; defaults to the world root (the Three.js scene). */
  parent?: Entity | Object3D | null;
  /** Initial components: `{ Spin: { speed: 2 } }` or `[[Spin, { speed: 2 }]]`. */
  components?: Record<string, Record<string, unknown>> | Array<[ComponentRef, Record<string, unknown>?]>;
  /** Local position: `[x, y, z]`, `{ x, y, z }` or a `Vector3`. */
  position?: Vec3Input;
  /** @internal Authoring parent id (scene loader). */
  parentId?: string | null;
}

interface CachedQuery {
  names: string[];
  result: readonly Entity[];
}

export class World {
  /** Spawned objects without a parent are added here (the Three.js scene in a game). */
  root: Object3D;

  private readonly entities = new Map<string, Entity>();
  private readonly byObject = new WeakMap<Object3D, Entity>();
  private readonly queries = new Map<string, CachedQuery>();
  private readonly listeners = new Set<(event: WorldEvent) => void>();
  /** Authoring parent id → children (they may live elsewhere in the scene graph, e.g. physics bodies). */
  private readonly childrenByParent = new Map<string, Set<Entity>>();
  private readonly unsubscribeSchema: () => void;
  private nextRuntimeId = 1;

  constructor(root: Object3D = new Group()) {
    this.root = root;
    // Hot-reloaded schemas may add fields: backfill them on live entities.
    this.unsubscribeSchema = onComponentDefined((type) => {
      for (const entity of this.query(type.name)) {
        const data = entity._data.get(type.name)!;
        const filled = fillDefaults(type, data) as Record<string, unknown>;
        for (const key of Object.keys(filled)) if (!(key in data)) data[key] = filled[key];
      }
    });
  }

  /** Number of live entities. */
  get size(): number {
    return this.entities.size;
  }

  /** All live entities, in creation order. */
  all(): readonly Entity[] {
    return this.query();
  }

  /** Entities that have every listed component (all entities if none are listed). */
  query(...types: ComponentRef[]): readonly Entity[] {
    const names = types.map(componentName).sort();
    const key = names.join('|');
    let cached = this.queries.get(key);
    if (!cached) {
      const result: Entity[] = [];
      for (const entity of this.entities.values()) {
        if (names.every((n) => entity._data.has(n))) result.push(entity);
      }
      cached = { names, result: Object.freeze(result) };
      this.queries.set(key, cached);
    }
    return cached.result;
  }

  get(id: string | null | undefined): Entity | undefined {
    return id ? this.entities.get(id) : undefined;
  }

  /** First entity with this name (Blender object name for authored entities). */
  findByName(name: string): Entity | undefined {
    for (const entity of this.entities.values()) if (entity.name === name) return entity;
    return undefined;
  }

  /** The entity whose object3d is `object` or its nearest ancestor. */
  entityOf(object: Object3D | null | undefined): Entity | undefined {
    for (let o = object; o; o = o.parent) {
      const entity = this.byObject.get(o);
      if (entity) return entity;
    }
    return undefined;
  }

  /** Creates an entity at runtime. */
  spawn(options: SpawnOptions = {}): Entity {
    let id = options.id;
    if (id === undefined) id = this._nextId();
    else if (this.entities.has(id)) throw new Error(`Entity id "${id}" already exists`);
    const object3d = options.object3d ?? new Group();
    const name = options.name ?? (object3d.name || id);
    if (!object3d.name) object3d.name = name;
    if (options.position) toVector3(options.position, object3d.position);

    const parent = options.parent;
    const parentObject = parent instanceof Entity ? parent.object3d : parent;
    if (parentObject) {
      if (object3d.parent !== parentObject) parentObject.add(object3d);
    } else if (!object3d.parent || parent === null) {
      this.root.add(object3d);
    }
    const parentId = options.parentId !== undefined ? options.parentId : parent instanceof Entity ? parent.id : null;

    const entity = new Entity(this, id, name, object3d, parentId);
    this.entities.set(id, entity);
    this.byObject.set(object3d, entity);
    if (parentId !== null) {
      let siblings = this.childrenByParent.get(parentId);
      if (!siblings) this.childrenByParent.set(parentId, (siblings = new Set()));
      siblings.add(entity);
    }
    this.invalidate(['']);
    this.emit({ type: 'spawn', entity });

    const comps = options.components;
    if (Array.isArray(comps)) for (const [type, values] of comps) entity.add(type, values);
    else if (comps) for (const [type, values] of Object.entries(comps)) entity.add(type, values);
    return entity;
  }

  /**
   * Destroys every entity, except those in `keep` (entities carried into the next scene). A kept
   * entity whose parent goes loses its parent link.
   */
  clear(keep?: ReadonlySet<Entity>): void {
    for (const entity of [...this.entities.values()]) if (!keep?.has(entity)) this._destroy(entity, keep);
    for (const entity of keep ?? []) {
      if (entity.parentId === null || this.entities.has(entity.parentId)) continue;
      this.childrenByParent.get(entity.parentId)?.delete(entity);
      (entity as { parentId: string | null }).parentId = null;
    }
  }

  /**
   * The entity's child entities, recursively, parents before their children: authoring children
   * (also those physics moved to the scene root) and entities whose objects are under its object.
   * With components, only those that have all of them: `descendants(chunk, Socket)` finds the
   * Socket empties inside a spawned prefab instance.
   */
  descendants(entity: Entity, ...types: ComponentRef[]): Entity[] {
    const names = types.map(componentName);
    const found = new Set<Entity>();
    const visit = (e: Entity): void => {
      const children = new Set<Entity>(this.childrenByParent.get(e.id));
      e.object3d.traverse((o) => {
        const child = o !== e.object3d ? this.byObject.get(o) : undefined;
        if (child) children.add(child);
      });
      for (const child of children) {
        if (found.has(child) || child === entity) continue;
        found.add(child);
        visit(child);
      }
    };
    visit(entity);
    const all = [...found];
    return names.length ? all.filter((e) => names.every((n) => e._data.has(n))) : all;
  }

  /** Subscribes to entity/component changes. Returns an unsubscribe function. */
  onChange(listener: (event: WorldEvent) => void): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  /** Detaches schema listeners; call when discarding the world. */
  dispose(): void {
    this.clear();
    this.unsubscribeSchema();
    this.listeners.clear();
  }

  // -------------------------------------------------------------------------
  // Internals used by Entity and the prefab instantiator

  /** @internal A fresh `rt_<n>` id that no live entity uses. */
  _nextId(): string {
    let id: string;
    do id = `rt_${this.nextRuntimeId++}`;
    while (this.entities.has(id));
    return id;
  }

  /** @internal */
  _addComponent(entity: Entity, name: string, values: Record<string, unknown>): Record<string, unknown> {
    if (!entity.alive) throw new Error(`Cannot add ${name} to destroyed entity ${entity.id}`);
    const replaced = entity._data.has(name);
    const data = fillDefaults(name, values) as Record<string, unknown>;
    entity._data.set(name, data);
    if (!replaced) this.invalidate([name]);
    this.emit({ type: 'add', entity, component: name, replaced });
    return data;
  }

  /** @internal */
  _removeComponent(entity: Entity, name: string): boolean {
    if (!entity._data.delete(name)) return false;
    this.invalidate([name]);
    this.emit({ type: 'remove', entity, component: name });
    return true;
  }

  /** @internal `spare`: entities to leave alive (scene loads that keep some). */
  _destroy(entity: Entity, spare?: ReadonlySet<Entity>): void {
    if (!entity.alive) return;
    // Child entities go first: those whose objects live under ours, and authoring
    // children that were moved elsewhere (dynamic and kinematic bodies sit at the root).
    const children = new Set<Entity>(this.childrenByParent.get(entity.id));
    entity.object3d.traverse((o) => {
      const child = o !== entity.object3d ? this.byObject.get(o) : undefined;
      if (child) children.add(child);
    });
    for (const child of children) if (!spare?.has(child)) this._destroy(child, spare);

    for (const name of [...entity._data.keys()]) this._removeComponent(entity, name);
    entity.alive = false;
    entity.object3d.removeFromParent();
    this.entities.delete(entity.id);
    this.byObject.delete(entity.object3d);
    this.childrenByParent.delete(entity.id);
    if (entity.parentId !== null) this.childrenByParent.get(entity.parentId)?.delete(entity);
    this.invalidate(['']);
    this.emit({ type: 'destroy', entity });
  }

  /** Drops cached queries that mention any of `names` ('' = the all-entities query). */
  private invalidate(names: string[]): void {
    this.queries.delete('');
    for (const [key, cached] of this.queries) {
      if (names.some((n) => cached.names.includes(n))) this.queries.delete(key);
    }
  }

  private emit(event: WorldEvent): void {
    for (const listener of this.listeners) listener(event);
  }
}
