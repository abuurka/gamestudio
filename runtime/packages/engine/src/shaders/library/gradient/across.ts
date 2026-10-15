// Where a pixel lies along an angle across what a material draws, for Gradient and Shine: 0 where the
// line enters the drawn meshes' bounds, 1 where it leaves (corner to corner, as CSS gradients run).
// The angle is seen as in Blender: a 3D mesh from the front (object X right, Blender Z up = glTF +Y), a
// Game UI mesh through its canvas camera (local px with y down there, so `ctx.ui` flips y back).
//
// The bounds are the rest-pose bounding boxes of the geometries that draw the material, measured as
// they are first drawn. A geometry whose box is dropped (`geometry.boundingBox = null`: the Game UI
// does that when a 9-slice panel is laid out at another size) is measured again.

import { Box3, Vector3, type BufferGeometry, type Material } from 'three';

/** The bounds a material's pixels are placed in. */
export interface Across {
  readonly min: Vector3;
  readonly max: Vector3;
  /** 1 for 3D meshes, -1 for Game UI meshes (local y down). */
  readonly flip: { value: number };
  /** Each drawn geometry and the box it was measured with. */
  readonly boxes: Map<BufferGeometry, Box3 | null>;
}

export function createAcross(ui: boolean): Across {
  return { min: new Vector3(-1, -1, -1), max: new Vector3(1, 1, 1), flip: { value: ui ? -1 : 1 }, boxes: new Map() };
}

const union = new Box3();

/** Measures a geometry the first time it draws, and again when its bounding box was dropped. */
export function track(across: Across, geometry: BufferGeometry | undefined): void {
  if (!geometry) return;
  if (!geometry.boundingBox) geometry.computeBoundingBox();
  const box = geometry.boundingBox;
  if (across.boxes.has(geometry) && across.boxes.get(geometry) === box) return;
  across.boxes.set(geometry, box);
  union.makeEmpty();
  for (const b of across.boxes.values()) if (b && !b.isEmpty()) union.union(b);
  if (union.isEmpty()) return;
  across.min.copy(union.min);
  across.max.copy(union.max);
}

/** Measures every geometry that draws `material`, as it draws (copies made later, such as prefab spawns, too). */
export function trackDraws(material: Material, across: Across): void {
  const previous = material.onBeforeRender;
  material.onBeforeRender = function (renderer, scene, camera, geometry, object, group) {
    previous.call(this, renderer, scene, camera, geometry, object, group);
    track(across, geometry);
  };
}

/** The uniforms of `acrossGlsl(prefix)`. */
export function acrossUniforms(across: Across, prefix: string): Record<string, { value: unknown }> {
  return { [`${prefix}Min`]: { value: across.min }, [`${prefix}Max`]: { value: across.max }, [`${prefix}Flip`]: across.flip };
}

/** Vertex shader declarations: the rest-pose position in object space (each GPU instance its own). */
export const acrossVertexPars = (prefix: string): string => /* glsl */ `
varying vec2 v${prefix}Pos;`;

/** After `#include <begin_vertex>`. */
export const acrossVertex = (prefix: string): string => /* glsl */ `
v${prefix}Pos = position.xy;`;

/**
 * Fragment shader declarations: `float <p>Across(float angle)`, 0 where the line at `angle` (radians,
 * counter-clockwise from +X, as seen in Blender) enters the bounds and 1 where it leaves.
 */
export const acrossFragmentPars = (prefix: string): string => /* glsl */ `
uniform vec3 ${prefix}Min;
uniform vec3 ${prefix}Max;
uniform float ${prefix}Flip;
varying vec2 v${prefix}Pos;
float ${prefix}Across(float angle) {
  vec2 lo = ${prefix}Min.xy;
  vec2 hi = ${prefix}Max.xy;
  vec2 ext = max((hi - lo) * 0.5, vec2(1e-6));
  vec2 q = v${prefix}Pos - (lo + hi) * 0.5;
  q.y *= ${prefix}Flip;
  vec2 d = vec2(cos(angle), sin(angle));
  float reach = abs(ext.x * d.x) + abs(ext.y * d.y);
  return dot(q, d) / (2.0 * max(reach, 1e-6)) + 0.5;
}`;
