// Extra draws that game shaders add to a mesh (outline hulls, x-ray passes). DOM-free.

import type { Object3D } from 'three';

/**
 * True for an object a game shader's `mesh()` hook added only to draw (it carries
 * `userData.gsShaderPass`). Such objects are never entities, and physics shapes and navmeshes leave
 * them out: they repeat their mesh's geometry.
 */
export function isShaderPass(object: Object3D): boolean {
  return !!object.userData?.gsShaderPass;
}
