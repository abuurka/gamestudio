// Creates ECS entities from a loaded glTF scene graph and a processed manifest.
// Objects are matched to manifest entries ONLY through `userData.gs_id`
// (GLTFLoader rewrites names, docs/SPEC.md §2). DOM-free.

import { Group, type Mesh, type Object3D } from 'three';
import type { Entity } from '../ecs/entity';
import type { World } from '../ecs/world';
import { placeObject } from '../game/edits';
import type { ManifestEntity, SceneManifest } from './manifest';

export interface BuildResult {
  entities: Entity[];
  /** Manifest ids without a node in the GLB (given an empty Group). */
  missing: string[];
}

/**
 * The three.js layer of meshes that only shape a collider (`drawn: false` entities). Cameras see
 * layer 0 only, so these are not drawn and cast no shadows; `camera.layers.enable(COLLISION_LAYER)`
 * shows them (to look at colliders). Physics and raycasts (`ctx.physics.raycast`) still use them.
 */
export const COLLISION_LAYER = 31;

/**
 * Moves the meshes an entity draws itself (its object and descendants that are not other entities)
 * to `COLLISION_LAYER`. Layers apply per object, so child entities stay visible.
 */
export function hideOwnMeshes(root: Object3D): void {
  const visit = (object: Object3D): void => {
    if ((object as Mesh).isMesh) object.layers.set(COLLISION_LAYER);
    for (const child of object.children) {
      if (typeof child.userData?.gs_id !== 'string') visit(child);
    }
  };
  visit(root);
}

/** Indexes every object carrying a `gs_id` (first occurrence wins). */
export function indexByGsId(root: Object3D): Map<string, Object3D> {
  const map = new Map<string, Object3D>();
  root.traverse((object) => {
    const id = object.userData?.gs_id;
    if (typeof id === 'string' && !map.has(id)) map.set(id, object);
  });
  return map;
}

/**
 * Puts the nodes of entities with a manifest `transform` (moved after the GLB was written) where they
 * now are, with the math of a live `entity.transform` (local to the authoring parent). Runs on the
 * parsed scene before entities and bodies exist, so children, prefab instances placed under an
 * instancer later, and bodies built from the nodes all start at the new pose. Returns how many moved.
 */
export function applyManifestTransforms(sceneRoot: Object3D, entities: readonly ManifestEntity[]): number {
  let moved = 0;
  let nodes: Map<string, Object3D> | null = null;
  for (const entry of entities) {
    if (!entry.transform) continue;
    nodes ??= indexByGsId(sceneRoot);
    const node = nodes.get(entry.id);
    if (!node) continue; // Not in the GLB: buildEntities gives it an empty Group at its parent.
    placeObject(node, entry.parent ? nodes.get(entry.parent) : undefined, entry.transform);
    moved++;
  }
  return moved;
}

export function buildEntities(world: World, sceneRoot: Object3D, manifest: SceneManifest): BuildResult {
  const nodes = indexByGsId(sceneRoot);
  const missing: string[] = [];
  const entries = new Map(manifest.entities.map((e) => [e.id, e]));

  // Missing nodes get a Group under their parent's object (created on demand, parents first).
  const groups = new Map<string, Object3D>();
  const objectFor = (id: string, depth = 0): Object3D => {
    const existing = nodes.get(id) ?? groups.get(id);
    if (existing) return existing;
    const entry = entries.get(id)!;
    const group = new Group();
    group.name = entry.name;
    group.userData.gs_id = id;
    groups.set(id, group);
    const parentEntry = entry.parent ? entries.get(entry.parent) : undefined;
    // depth guards against parent cycles in malformed manifests.
    const parent = parentEntry && depth < 64 ? objectFor(parentEntry.id, depth + 1) : sceneRoot;
    parent.add(group);
    missing.push(id);
    return group;
  };

  const entities: Entity[] = [];
  for (const entry of manifest.entities) {
    const object3d = objectFor(entry.id);
    if (entry.drawn === false) hideOwnMeshes(object3d);
    entities.push(
      world.spawn({ id: entry.id, name: entry.name, object3d, parentId: entry.parent, components: entry.components }),
    );
  }

  // Nodes with a gs_id that the manifest doesn't list still become (component-less) entities.
  for (const [id, object3d] of nodes) {
    if (entries.has(id) || world.get(id)) continue;
    const parentEntity = world.entityOf(object3d.parent);
    entities.push(world.spawn({ id, name: object3d.name || id, object3d, parentId: parentEntity?.id ?? null }));
  }
  return { entities, missing };
}
