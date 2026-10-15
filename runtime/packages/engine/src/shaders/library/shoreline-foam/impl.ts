// After FX Lab "shoreline" (the foam), by way of the Sahan game's shoreline.ts: a white wash that
// clings to the rock, foam fronts that roll outward and break up into lace, thinning out to sea.
// Built on the exported MeshStandardMaterial (lit, shadowed, fogged), made see-through.

import { MeshStandardMaterial, type Material } from 'three';
import { patchShader, type ShaderApply, type ShaderMesh } from '../../../schema/shaders';
import type { fields } from './meta';

const VERTEX_PARS = /* glsl */ `
varying vec2 vSfUv;`;

const VERTEX = /* glsl */ `
vSfUv = uv;`;

// sfHash is David Hoskins' hash12 ("Hash without Sine", https://www.shadertoy.com/view/4djSRW, MIT;
// THIRD-PARTY-NOTICES.md).
const FRAGMENT_PARS = /* glsl */ `
uniform float sfTime;
uniform float sfSpeed;
uniform float sfWash;
uniform float sfWaves;
uniform float sfRepeat;
uniform float sfOpacity;
varying vec2 vSfUv;
float sfHash(vec2 p) { vec3 p3 = fract(vec3(p.xyx) * 0.1031); p3 += dot(p3, p3.yzx + 33.33); return fract((p3.x + p3.y) * p3.z); }
float sfNoise(vec2 p) {
  vec2 i = floor(p), f = fract(p);
  vec2 u = f * f * (3.0 - 2.0 * f);
  return mix(mix(sfHash(i), sfHash(i + vec2(1.0, 0.0)), u.x), mix(sfHash(i + vec2(0.0, 1.0)), sfHash(i + vec2(1.0, 1.0)), u.x), u.y);
}
float sfFbm(vec2 p) { float a = 0.5, s = 0.0; for (int i = 0; i < 4; i++) { s += a * sfNoise(p); p = p * 2.07 + 11.3; a *= 0.5; } return s; }`;

// After color_fragment: the foam's coverage becomes the alpha; the material's colour tints it.
const COLOR = /* glsl */ `
{
  // Blender's V runs from 0 at the shore to 1 out at sea; glTF stores it flipped.
  float u = vSfUv.x * sfRepeat, v = 1.0 - vSfUv.y, t = sfTime * sfSpeed;
  float n = sfFbm(vec2(u * 3.0, v * 2.0 - t * 0.12));
  // White water against the rock, ragged where it reaches out.
  float wash = 1.0 - smoothstep(0.04, sfWash + 0.18 * n, v);
  // Foam fronts rolling outward, broken into lace that thins out to sea.
  float front = fract(v * sfWaves - t * 0.16 + n * 0.45);
  float band = smoothstep(0.0, 0.06, front) * (1.0 - smoothstep(0.06, 0.42, front));
  float lace = smoothstep(0.42, 0.72, sfFbm(vec2(u * 10.0, v * 7.0 - t * 0.25)));
  float foam = max(wash * (0.7 + 0.5 * n), band * lace * (1.0 - v) * 1.7);
  foam *= 1.0 - smoothstep(0.55, 1.0, v);
  diffuseColor.a *= clamp(foam, 0.0, 1.0) * sfOpacity;
  if (diffuseColor.a < 0.01) discard;
  // Foam is bright: the material's colour gives its hue, not its brightness.
  diffuseColor.rgb *= 0.9 / max(max(max(diffuseColor.r, diffuseColor.g), diffuseColor.b), 1e-3);
  diffuseColor.rgb *= 0.88 + 0.3 * n;
}`;

/** The material as a MeshStandardMaterial: an unlit export becomes one with its colour and map. */
function standard(material: Material): MeshStandardMaterial {
  if ((material as MeshStandardMaterial).isMeshStandardMaterial) return material as MeshStandardMaterial;
  const from = material as Material & Partial<Pick<MeshStandardMaterial, 'color' | 'map'>>;
  const out = new MeshStandardMaterial({ side: material.side });
  out.name = material.name;
  if (from.color) out.color.copy(from.color);
  if (from.map) out.map = from.map;
  return out;
}

export const apply: ShaderApply<typeof fields> = (material, _values, ctx) => {
  const target = standard(material);
  // See-through foam over the water: no depth writes, nudged towards the camera against z-fighting.
  target.transparent = true;
  target.depthWrite = false;
  target.polygonOffset = true;
  target.polygonOffsetFactor = -2;
  target.polygonOffsetUnits = -4;
  target.roughness = 0.75;
  target.metalness = 0;
  const u = ctx.uniforms;
  const uniforms = { sfTime: ctx.time, sfSpeed: u.speed, sfWash: u.wash, sfWaves: u.waves, sfRepeat: u.repeat, sfOpacity: u.opacity };
  patchShader(target, 'gs-shoreline-foam', (shader) => {
    Object.assign(shader.uniforms, uniforms);
    shader.vertexShader = shader.vertexShader
      .replace('#include <common>', `#include <common>\n${VERTEX_PARS}`)
      .replace('#include <uv_vertex>', `#include <uv_vertex>\n${VERTEX}`);
    shader.fragmentShader = shader.fragmentShader
      .replace('#include <common>', `#include <common>\n${FRAGMENT_PARS}`)
      .replace('#include <color_fragment>', `#include <color_fragment>\n${COLOR}`);
  });
  return target === material ? undefined : target;
};

/** Drawn after the water it lies on (both are see-through); foam casts no shadow. */
export const mesh: ShaderMesh<typeof fields> = (mesh) => {
  mesh.renderOrder = Math.max(mesh.renderOrder, 1);
  mesh.castShadow = false;
};
