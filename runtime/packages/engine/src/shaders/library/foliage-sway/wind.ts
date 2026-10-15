// Wind shared by the vertex-moving nature shaders (FoliageSway, GrassWind, FlagWave), after FX Lab
// "wind-meadow": gust fronts roll downwind over the ground, so trees, grass and flags in one level
// gust together when their wind fields agree. Also the shadow twins every vertex-moving shader needs.

import { MeshDepthMaterial, MeshDistanceMaterial, type Material, type Mesh } from 'three';
import { patchShader, type ShaderEdit } from '../../../schema/shaders';

/**
 * Hash, value noise and the gust field. Needs no uniforms. gsWindHash is David Hoskins' hash12 ("Hash
 * without Sine", https://www.shadertoy.com/view/4djSRW, MIT; THIRD-PARTY-NOTICES.md).
 */
export const windGlsl = /* glsl */ `
float gsWindHash(vec2 p) {
  vec3 p3 = fract(vec3(p.xyx) * 0.1031);
  p3 += dot(p3, p3.yzx + 33.33);
  return fract((p3.x + p3.y) * p3.z);
}
float gsWindNoise(vec2 p) {
  vec2 i = floor(p), f = fract(p);
  vec2 u = f * f * (3.0 - 2.0 * f);
  return mix(mix(gsWindHash(i), gsWindHash(i + vec2(1.0, 0.0)), u.x), mix(gsWindHash(i + vec2(0.0, 1.0)), gsWindHash(i + vec2(1.0, 1.0)), u.x), u.y);
}
// Direction the wind blows towards, world XZ, from the angle in Blender's top view (0 = +X, counter-clockwise).
vec2 gsWindDir(float angle) { return vec2(cos(angle), -sin(angle)); }
// How far the gust fronts have rolled: faster in stronger wind.
float gsWindTravel(float time, float strength) { return time * (1.6 + 7.4 * strength); }
// Gust 0..1 at a ground point: fronts about 11 m apart, bent and broken into gusts of varying strength.
float gsWindGust(vec2 p, vec2 dir, float travel) {
  float along = dot(p, dir) - travel;
  float across = dot(p, vec2(-dir.y, dir.x));
  float bend = gsWindNoise(vec2(along * 0.11, across * 0.13 + 0.37));
  float fronts = 0.5 + 0.5 * sin(along * 0.56 + bend * 7.0);
  float patches = gsWindNoise(vec2(along * 0.27 + 5.3, across * 0.19 + 1.1));
  return clamp(fronts * fronts * smoothstep(0.25, 0.85, patches * 0.8 + bend * 0.45) * 1.35, 0.0, 1.0);
}
`;

const twins = new WeakMap<Material, { depth: MeshDepthMaterial; distance: MeshDistanceMaterial }>();

/**
 * Gives `mesh` shadow materials (sun and spot lights: depth; point lights: distance) whose vertices
 * move like `material`'s, so shadows follow the motion. One pair per material instance, shared by its
 * meshes; `edit` patches the vertex shader only (the shadow shaders have no lighting chunks).
 */
export function shadowTwins(mesh: Mesh, material: Material, key: string, edit: ShaderEdit): void {
  let pair = twins.get(material);
  if (!pair) {
    const depth = new MeshDepthMaterial();
    const distance = new MeshDistanceMaterial();
    patchShader(depth, key, edit);
    patchShader(distance, key, edit);
    pair = { depth, distance };
    twins.set(material, pair);
    const owned = pair;
    material.addEventListener('dispose', () => {
      owned.depth.dispose();
      owned.distance.dispose();
    });
  }
  mesh.customDepthMaterial = pair.depth;
  mesh.customDistanceMaterial = pair.distance;
}
