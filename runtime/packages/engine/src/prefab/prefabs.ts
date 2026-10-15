// Prefabs: Blender collections marked as prefabs, exported as templates
// (docs/SPEC.md §4.4). `ctx.spawn(name)` clones a template with its components;
// collection instances of a prefab placed in Blender are instantiated the same way
// under their instancer entity when the scene loads.

import { Group, Vector3, type Object3D } from 'three';
import type { GLTF } from 'three/addons/loaders/GLTFLoader.js';
import type { Entity } from '../ecs/entity';
import type { World } from '../ecs/world';
import type { ManifestEntity, ManifestPrefab } from '../scene/manifest';
import { toQuaternion, toVector3, type RotationInput, type Vec3Input } from '../math';
import { getComponent } from '../schema/registry';
import { hideOwnMeshes, indexByGsId } from '../scene/build';
import { cloneScene } from '../scene/clone';

export type { RotationInput, Vec3Input } from '../math';

export interface SpawnPrefabOptions {
  /** Position of the prefab's root, local to `parent` (world space without one). */
  position?: Vec3Input;
  rotation?: RotationInput;
  /** A number scales uniformly. */
  scale?: number | Vec3Input;
  /** Attach the instance to this entity. */
  parent?: Entity | null;
  /** Name of the returned root entity. Default: the template's root object name (or the prefab name). */
  name?: string;
}

interface Template {
  name: string;
  offset: Vector3;
  /** Template entities with parents before children. */
  entries: ManifestEntity[];
  /** Template objects without a parent inside the template. */
  roots: ManifestEntity[];
  /** Template id → node in the prefab GLB. */
  nodes: Map<string, Object3D>;
}

/** Nested prefab instances deeper than this are not expanded (guards against cycles). */
const MAX_DEPTH = 8;

export class Prefabs {
  private templates = new Map<string, Template>();

  constructor(private readonly warn: (message: string) => void = (m) => console.warn(m)) {}

  /** Replaces the templates with the scene's prefabs. `gltfs` maps `prefab.glb` to the parsed file. */
  load(prefabs: readonly ManifestPrefab[], gltfs: Map<string, GLTF>): void {
    this.templates = new Map();
    const indexes = new Map<string, Map<string, Object3D>>();
    for (const prefab of prefabs) {
      const gltf = gltfs.get(prefab.glb);
      if (!gltf) {
        this.warn(`[prefab] "${prefab.name}": ${prefab.glb} is missing`);
        continue;
      }
      let index = indexes.get(prefab.glb);
      if (!index) indexes.set(prefab.glb, (index = indexByGsId(gltf.scene)));
      const ids = new Set(prefab.entities.map((e) => e.id));
      const nodes = new Map<string, Object3D>();
      for (const entry of prefab.entities) {
        const node = index.get(entry.id);
        if (!node) continue;
        nodes.set(entry.id, node);
        if (entry.drawn === false) hideOwnMeshes(node); // clones copy the layers
      }
      this.templates.set(prefab.name, {
        name: prefab.name,
        offset: new Vector3().fromArray(prefab.offset),
        entries: parentsFirst(prefab.entities),
        roots: prefab.entities.filter((e) => e.parent === null || !ids.has(e.parent)),
        nodes,
      });
    }
  }

  names(): string[] {
    return [...this.templates.keys()];
  }

  has(name: string): boolean {
    return this.templates.has(name);
  }

  /**
   * Creates an instance of a prefab and returns its root entity: the template's
   * root object, or (for a collection with several top-level objects) a new
   * group entity named after the prefab, holding them.
   */
  spawn(world: World, name: string, options: SpawnPrefabOptions = {}): Entity {
    const template = this.templates.get(name);
    if (!template) {
      const known = this.names();
      throw new Error(
        `Unknown prefab "${name}". ` +
          (known.length ? `Prefabs in this scene: ${known.join(', ')}` : 'This scene has no prefabs (mark a collection as a prefab in Blender).'),
      );
    }
    const parent = options.parent ?? null;
    if (parent && !parent.alive) throw new Error(`Cannot spawn "${name}" under destroyed entity ${parent.id}`);
    const parentObject = parent?.object3d ?? world.root;
    const ids = new Map(template.entries.map((e) => [e.id, world._nextId()]));

    if (template.roots.length === 1) {
      const rootEntry = template.roots[0]!;
      const clone = this.cloneRoot(template, rootEntry);
      if (options.position) toVector3(options.position, clone.position);
      else clone.position.sub(template.offset);
      if (options.rotation) toQuaternion(options.rotation, clone.quaternion);
      if (options.scale !== undefined) setScale(clone.scale, options.scale);
      parentObject.add(clone);
      const entities = this.build(world, template, [clone], ids, parent?.id ?? null, 0);
      const root = entities.find((e) => e.id === ids.get(rootEntry.id))!;
      if (options.name) root.name = options.name;
      return root;
    }

    const group = new Group();
    if (options.position) toVector3(options.position, group.position);
    if (options.rotation) toQuaternion(options.rotation, group.quaternion);
    if (options.scale !== undefined) setScale(group.scale, options.scale);
    parentObject.add(group);
    const root = world.spawn({ name: options.name ?? template.name, object3d: group, parentId: parent?.id ?? null });
    const clones = template.roots.map((entry) => {
      const clone = this.cloneRoot(template, entry);
      clone.position.sub(template.offset);
      group.add(clone);
      return clone;
    });
    this.build(world, template, clones, ids, root.id, 0);
    return root;
  }

  /**
   * Instantiates a prefab under a collection-instance entity from the scene.
   * Ids are `<instancer id>/<template id>`, so they are the same on every load.
   */
  instantiateUnder(world: World, name: string, instancer: Entity, depth = 0): Entity[] {
    const template = this.templates.get(name);
    if (!template) {
      this.warn(`[prefab] "${instancer.name}" instances prefab "${name}", which was not exported`);
      return [];
    }
    if (depth > MAX_DEPTH) {
      this.warn(`[prefab] "${name}" is nested more than ${MAX_DEPTH} levels deep; stopping (does it contain itself?)`);
      return [];
    }
    const ids = new Map(template.entries.map((e) => [e.id, `${instancer.id}/${e.id}`]));
    // The instancer's transform already includes the collection offset, as in Blender.
    const clones = template.roots.map((entry) => {
      const clone = this.cloneRoot(template, entry);
      instancer.object3d.add(clone);
      return clone;
    });
    return this.build(world, template, clones, ids, instancer.id, depth);
  }

  private cloneRoot(template: Template, entry: ManifestEntity): Object3D {
    const node = template.nodes.get(entry.id);
    if (node) return cloneScene(node); // skinned meshes rebound to the copied bones
    const group = new Group(); // The node is missing from the GLB: keep the entity anyway.
    group.name = entry.name;
    group.userData.gs_id = entry.id;
    return group;
  }

  private build(
    world: World,
    template: Template,
    clones: Object3D[],
    ids: Map<string, string>,
    rootParentId: string | null,
    depth: number,
  ): Entity[] {
    const objects = new Map<string, Object3D>();
    for (const clone of clones) {
      clone.traverse((o) => {
        const id = o.userData?.gs_id;
        if (typeof id === 'string' && ids.has(id) && !objects.has(id)) objects.set(id, o);
      });
    }
    const entities: Entity[] = [];
    for (const entry of template.entries) {
      const parentKey = entry.parent !== null && ids.has(entry.parent) ? entry.parent : null;
      let object = objects.get(entry.id);
      if (!object) {
        object = new Group();
        object.name = entry.name;
        (parentKey ? objects.get(parentKey) : clones[0]?.parent)?.add(object);
        objects.set(entry.id, object);
      }
      const id = ids.get(entry.id)!;
      object.userData.gs_id = id;
      object.userData.gs_template = entry.id;
      const entity = world.spawn({
        id,
        name: entry.name,
        object3d: object,
        parentId: parentKey ? ids.get(parentKey)! : rootParentId,
        components: remapEntityRefs(entry.components, ids),
      });
      entities.push(entity);
      if (entry.prefab) entities.push(...this.instantiateUnder(world, entry.prefab, entity, depth + 1));
    }
    return entities;
  }
}

/** Orders entities so every parent comes before its children. */
function parentsFirst(entities: readonly ManifestEntity[]): ManifestEntity[] {
  const byId = new Map(entities.map((e) => [e.id, e]));
  const out: ManifestEntity[] = [];
  const placed = new Set<string>();
  const place = (e: ManifestEntity, depth: number): void => {
    if (placed.has(e.id)) return;
    const parent = e.parent !== null ? byId.get(e.parent) : undefined;
    if (parent && depth < 64) place(parent, depth + 1);
    placed.add(e.id);
    out.push(e);
  };
  for (const e of entities) place(e, 0);
  return out;
}

/** Entity fields that point inside the template are redirected to the new instance's entities. */
function remapEntityRefs(
  components: Record<string, Record<string, unknown>>,
  ids: Map<string, string>,
): Record<string, Record<string, unknown>> {
  const out: Record<string, Record<string, unknown>> = {};
  for (const [name, values] of Object.entries(components)) {
    const copy = structuredClone(values);
    const fields = getComponent(name)?.fields ?? {};
    for (const [field, def] of Object.entries(fields)) {
      const value = copy[field];
      if (def.type === 'entity' && typeof value === 'string' && ids.has(value)) copy[field] = ids.get(value)!;
    }
    out[name] = copy;
  }
  return out;
}

function setScale(target: Vector3, value: number | Vec3Input): void {
  if (typeof value === 'number') target.setScalar(value);
  else toVector3(value, target);
}

