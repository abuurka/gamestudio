// Copies of parsed scene graphs (prefab instances, scenes cloned from the parse cache). DOM-free.

import type { Light, Material, Mesh, Object3D } from 'three';
import { clone as cloneWithSkeletons } from 'three/addons/utils/SkeletonUtils.js';

/**
 * A copy of a parsed scene graph that shares its geometries, materials, textures and clips:
 * SkeletonUtils.clone (skinned meshes bound to the copied bones), plus light targets and shadow materials.
 * Light.copy gives a directional or spot light a detached copy of its target, which then never moves;
 * GLTFLoader puts the target under the light, so the copy points at its own copied child instead.
 * Object3D.copy drops `customDepthMaterial`/`customDistanceMaterial`, which game shaders that move vertices
 * or cut holes set (so the shadow matches): the copy shares the source's.
 */
export function cloneScene<T extends Object3D>(source: T): T {
  const copy = cloneWithSkeletons(source) as T;
  const sources: Object3D[] = [];
  source.traverse((o) => void sources.push(o));
  let i = 0;
  copy.traverse((o) => {
    const from = sources[i++] as (Light & { target?: Object3D }) | undefined;
    if (from?.customDepthMaterial) o.customDepthMaterial = from.customDepthMaterial;
    if (from?.customDistanceMaterial) o.customDistanceMaterial = from.customDistanceMaterial;
    const target = from?.isLight ? from.target : undefined;
    if (!from || !target || target.parent !== from) return;
    const child = o.children[from.children.indexOf(target)];
    if (child) (o as Light & { target: Object3D }).target = child;
  });
  return copy;
}

/**
 * Gives the objects under `root` their own copies of their materials (a material shared by several
 * objects stays shared among them). Game code that changes a material (a hit flash, a fade) then
 * changes neither the cached parse nor the next load. Textures stay shared. Shader patches made with
 * `patchShader` (game shaders, lightmaps) are kept: a patched material's `clone()` copies them.
 */
export function ownMaterials(root: Object3D): void {
  const copies = new Map<Material, Material>();
  const own = (material: Material): Material => {
    let copy = copies.get(material);
    if (!copy) copies.set(material, (copy = material.clone()));
    return copy;
  };
  root.traverse((o) => {
    const material = (o as Mesh).material;
    if (!material) return;
    (o as Mesh).material = Array.isArray(material) ? material.map(own) : own(material);
  });
}
