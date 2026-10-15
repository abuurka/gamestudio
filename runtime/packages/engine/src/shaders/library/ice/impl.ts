// After FX Lab "freezing-rain" (ice glaze: darker, glassy, lumpy run-and-freeze ripples, frozen beads that glint)
// and "ice-sculpture" (frost, cracks seen inside the ice, a cold glow at thin edges).
import type { MeshStandardMaterial } from 'three';
import { patchShader, type ShaderApply } from '../../../schema/shaders';
import type { fields } from './meta';

const VERTEX_PARS = /* glsl */ `
varying vec3 vIceWorld;
`;

const VERTEX = /* glsl */ `
#include <project_vertex>
{
  vec4 icePosition = vec4(transformed, 1.0);
#ifdef USE_BATCHING
  icePosition = batchingMatrix * icePosition;
#endif
#ifdef USE_INSTANCING
  icePosition = instanceMatrix * icePosition;
#endif
  vIceWorld = (modelMatrix * icePosition).xyz;
}
`;

// iceHash is David Hoskins' hash13 ("Hash without Sine", https://www.shadertoy.com/view/4djSRW, MIT;
// THIRD-PARTY-NOTICES.md).
const FRAGMENT_PARS = /* glsl */ `
uniform float uTime;
uniform float uIceFreeze;
uniform vec4 uIceColor;
uniform float uIceFrost;
uniform float uIceCracks;
uniform float uIceGlints;
uniform float uIceScale;
varying vec3 vIceWorld;
float iceHash(vec3 p) {
  p = fract(p * 0.1031);
  p += dot(p, p.zyx + 31.32);
  return fract((p.x + p.y) * p.z);
}
float iceNoise(vec3 p) {
  vec3 i = floor(p), f = fract(p);
  vec3 u = f * f * (3.0 - 2.0 * f);
  return mix(
    mix(mix(iceHash(i), iceHash(i + vec3(1.0, 0.0, 0.0)), u.x), mix(iceHash(i + vec3(0.0, 1.0, 0.0)), iceHash(i + vec3(1.0, 1.0, 0.0)), u.x), u.y),
    mix(mix(iceHash(i + vec3(0.0, 0.0, 1.0)), iceHash(i + vec3(1.0, 0.0, 1.0)), u.x), mix(iceHash(i + vec3(0.0, 1.0, 1.0)), iceHash(i + vec3(1.0, 1.0, 1.0)), u.x), u.y),
    u.z);
}
// Crack sheets: one flat fracture per cell of a 3D grid (through a random point, at a random angle,
// fading out before the cell's edge, so no neighbours are read), a thin bright line where the
// surface crosses it. Some cells have none.
float iceSheet(vec3 p) {
  vec3 cell = floor(p);
  vec3 f = p - cell - 0.5;
  vec3 n = normalize(vec3(iceHash(cell + 3.1), iceHash(cell + 5.7), iceHash(cell + 8.3)) - 0.5 + 1e-4);
  float s = dot(f, n) - (iceHash(cell) - 0.5) * 0.3;
  float extent = 1.0 - smoothstep(0.18, 0.46, length(f - n * dot(f, n)));
  float width = 0.01 + fwidth(s);
  return (1.0 - smoothstep(width * 0.5, width, abs(s))) * extent * step(0.3, iceHash(cell + 1.7));
}
vec3 iceBump(vec3 n, vec3 position, float height) {
  vec3 dpx = dFdx(position), dpy = dFdy(position);
  vec3 r1 = cross(dpy, n), r2 = cross(n, dpx);
  float det = dot(dpx, r1);
  vec3 grad = sign(det) * (dFdx(height) * r1 + dFdy(height) * r2);
  return normalize(abs(det) * n - grad);
}
`;

// The glaze builds in patches as freeze rises (undersides first, where water runs and freezes).
// Under it the surface darkens and cools; cracks sit inside the ice at two depths, so they shift
// against the surface as the view moves; frost whitens the rims and the edge of a growing glaze.
const GLAZE = /* glsl */ `
#include <normal_fragment_maps>
vec3 iceUp = normalize((viewMatrix * vec4(0.0, 1.0, 0.0, 0.0)).xyz);
float iceScale = max(uIceScale, 0.01);
vec3 iceP = vIceWorld / iceScale;
vec3 iceView = normalize(cameraPosition - vIceWorld);
float icePatch = iceNoise(iceP * 2.3);
float iceCover = clamp(uIceFreeze, 0.0, 1.0) * 1.35 - icePatch * 0.55 + max(-dot(normal, iceUp), 0.0) * 0.25 - 0.05;
float iceAmt = smoothstep(0.0, 0.15, iceCover);
float iceRim = pow(1.0 - clamp(abs(dot(normal, normalize(vViewPosition))), 0.0, 1.0), 3.0);
// Cracks at two depths under the surface (they slide against it as the view moves).
float iceCracks = (iceSheet((vIceWorld - iceView * 0.02 * iceScale) / iceScale * 1.8)
  + 0.7 * iceSheet((vIceWorld - iceView * 0.07 * iceScale) / iceScale * 3.1 + 9.0)) * uIceCracks;
// Frost: feathery on the rims and along the edge of a glaze that is still growing.
float iceEdge = (1.0 - smoothstep(0.1, 0.35, iceCover)) * iceAmt;
float iceFrost = clamp(uIceFrost * (smoothstep(0.3, 0.85, iceRim * 1.3 + (iceNoise(iceP * 11.0) - 0.5) * 0.7) + iceEdge * 0.8), 0.0, 1.0);
// Clear ice over the surface: looking straight in, the surface under it, darker and colder; towards
// the rims more of the ice's own colour, scattered in its depth.
vec3 iceDeep = (diffuseColor.rgb * 0.4 + vec3(0.02, 0.028, 0.036)) * mix(vec3(1.0), uIceColor.rgb, 0.5) + uIceColor.rgb * 0.08;
vec3 iceGlaze = mix(iceDeep, uIceColor.rgb * 0.55, 0.3 + 0.6 * sqrt(iceRim));
iceGlaze = mix(iceGlaze, vec3(0.75, 0.85, 0.95), clamp(iceCracks, 0.0, 1.0) * 0.6);
diffuseColor.rgb = mix(diffuseColor.rgb, iceGlaze, iceAmt);
diffuseColor.rgb = mix(diffuseColor.rgb, vec3(0.8, 0.88, 0.95), iceFrost * iceAmt);
roughnessFactor = mix(roughnessFactor, mix(0.05, 0.6, iceFrost), iceAmt);
metalnessFactor = mix(metalnessFactor, 0.0, iceAmt);
// Run-and-freeze ripples: a lumpy glaze that throws the highlights about.
float iceRipple = (iceNoise(vec3(vIceWorld.x + vIceWorld.z, vIceWorld.y * 0.35, vIceWorld.z) * 9.0 / iceScale) - 0.5) * 0.006 * iceScale;
normal = iceBump(normal, -vViewPosition, iceRipple * iceAmt * (1.0 - iceFrost));
`;

// Cold light caught inside thin edges, and frozen beads that catch the key light.
const GLOW = /* glsl */ `
#include <emissivemap_fragment>
totalEmissiveRadiance += uIceColor.rgb * (iceRim * 0.35 + iceCracks * 0.05) * iceAmt * 0.5;
`;

const GLINTS = /* glsl */ `
#include <lights_fragment_begin>
#if NUM_DIR_LIGHTS > 0
{
  vec3 iceGrid = vIceWorld * 38.0 / max(uIceScale, 0.01);
  vec3 iceCell = floor(iceGrid);
  vec3 iceRand = vec3(iceHash(iceCell + 1.3), iceHash(iceCell + 7.1), iceHash(iceCell + 3.7));
  // A round bead somewhere in the cell, faded out where cells get smaller than a pixel.
  float iceBead = step(0.8, iceHash(iceCell)) * (1.0 - smoothstep(0.1, 0.25, length(fract(iceGrid) - 0.2 - iceRand * 0.6)));
  vec3 iceFacet = normalize(geometryNormal + (iceRand - 0.5) * 1.6);
  vec3 iceHalf = normalize(directLight.direction + geometryViewDir);
  float iceNear = 1.0 - smoothstep(0.35, 1.0, length(fwidth(iceGrid)));
  float iceGlint = iceBead * pow(max(dot(iceFacet, iceHalf), 0.0), 160.0) * iceNear * (0.6 + 0.4 * sin(uTime * 2.0 + iceHash(iceCell) * 40.0));
  reflectedLight.directSpecular += directLight.color * iceGlint * iceAmt * uIceGlints * 10.0;
}
#endif
`;

export const apply: ShaderApply<typeof fields> = (material, _values, ctx) => {
  if (!(material as MeshStandardMaterial).isMeshStandardMaterial) {
    throw new Error('Ice needs a Principled BSDF material (MeshStandardMaterial in the game)');
  }
  const u = ctx.uniforms;
  patchShader(material, 'gs-ice', (shader) => {
    shader.uniforms.uTime = ctx.time;
    shader.uniforms.uIceFreeze = u.freeze;
    shader.uniforms.uIceColor = u.color;
    shader.uniforms.uIceFrost = u.frost;
    shader.uniforms.uIceCracks = u.cracks;
    shader.uniforms.uIceGlints = u.glints;
    shader.uniforms.uIceScale = u.scale;
    shader.vertexShader = shader.vertexShader
      .replace('#include <common>', `#include <common>\n${VERTEX_PARS}`)
      .replace('#include <project_vertex>', VERTEX);
    shader.fragmentShader = shader.fragmentShader
      .replace('#include <common>', `#include <common>\n${FRAGMENT_PARS}`)
      .replace('#include <normal_fragment_maps>', GLAZE)
      .replace('#include <emissivemap_fragment>', GLOW)
      .replace('#include <lights_fragment_begin>', GLINTS);
  });
};
