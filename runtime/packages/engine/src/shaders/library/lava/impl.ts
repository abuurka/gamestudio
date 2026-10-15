// After FX Lab "lava-river" (flow-mapped crust plates: two copies advect for half a cycle each and
// cross-fade, so the fade reads as plates cracking and re-forming; fast hot centre, slow crusted
// banks), "volcano" (domain-warped rafts, hairline cracks, the HDR lava ramp, bump from the crust)
// and "lava-fissure" (glowing plate rims). Built on the exported MeshStandardMaterial so the crust is
// lit, shadowed and fogged like rock, but its colour, roughness and glow are the lava's own.

import { Color, type MeshStandardMaterial } from 'three';
import { patchShader, type ShaderApply } from '../../../schema/shaders';
import type { fields } from './meta';

const VERTEX_PARS = /* glsl */ `
varying vec3 vLvWorld;
varying vec2 vLvUv;`;

// After project_vertex: the world position (instanced and batched copies included) and the UVs.
const VERTEX = /* glsl */ `
{
  vec4 lvPos = vec4(transformed, 1.0);
  #ifdef USE_BATCHING
    lvPos = batchingMatrix * lvPos;
  #endif
  #ifdef USE_INSTANCING
    lvPos = instanceMatrix * lvPos;
  #endif
  vLvWorld = (modelMatrix * lvPos).xyz;
  vLvUv = uv;
}`;

// lvHash and lvHash2 are David Hoskins' hash12 and hash22 ("Hash without Sine",
// https://www.shadertoy.com/view/4djSRW, MIT; THIRD-PARTY-NOTICES.md).
const FRAGMENT_PARS = /* glsl */ `
uniform float lvTime;
uniform vec4 lvColor;
uniform float lvHeat;
uniform float lvScale;
uniform vec2 lvFlow;
uniform float lvBrightness;
varying vec3 vLvWorld;
varying vec2 vLvUv;
float lvOpen;
float lvHeight;
vec3 lvGlow;
float lvHash(vec2 p) { vec3 p3 = fract(vec3(p.xyx) * 0.1031); p3 += dot(p3, p3.yzx + 33.33); return fract((p3.x + p3.y) * p3.z); }
vec2 lvHash2(vec2 p) { vec3 p3 = fract(vec3(p.xyx) * vec3(0.1031, 0.1030, 0.0973)); p3 += dot(p3, p3.yzx + 33.33); return fract((p3.xx + p3.yz) * p3.zy); }
float lvNoise(vec2 p) {
  vec2 i = floor(p), f = fract(p);
  vec2 u = f * f * (3.0 - 2.0 * f);
  return mix(mix(lvHash(i), lvHash(i + vec2(1.0, 0.0)), u.x), mix(lvHash(i + vec2(0.0, 1.0)), lvHash(i + vec2(1.0, 1.0)), u.x), u.y);
}
// Crust plates: x = distance to the plate's border (F2 - F1, 0 on a crack), y = the plate's id.
vec2 lvCells(vec2 p) {
  vec2 n = floor(p), f = fract(p);
  float d1 = 8.0, d2 = 8.0, id = 0.0;
  for (int j = -1; j <= 1; j++) for (int i = -1; i <= 1; i++) {
    vec2 g = vec2(float(i), float(j));
    vec2 o = lvHash2(n + g);
    vec2 r = g + o * 0.8 + 0.1 - f;
    float d = dot(r, r);
    if (d < d1) { d2 = d1; d1 = d; id = o.y; } else if (d < d2) d2 = d;
  }
  return vec2(sqrt(d2) - sqrt(d1), id);
}
// Temperature 0..1 to HDR lava light: dull red through the colour to white-hot.
vec3 lvRamp(float t) {
  vec3 hot = mix(lvColor.rgb, vec3(1.0, 0.72, 0.28), 0.55);
  vec3 deep = mix(lvColor.rgb * lvColor.rgb, lvColor.rgb, 0.25);
  t = clamp(t, 0.0, 1.0);
  // Kept low enough that tone mapping leaves the reds saturated; only the hottest lava turns white.
  vec3 c = deep * 0.35 * smoothstep(0.0, 0.25, t);
  c = mix(c, deep * 1.3, smoothstep(0.18, 0.5, t));
  c = mix(c, hot * 2.2, smoothstep(0.45, 0.82, t));
  return mix(c, hot * 4.0 + vec3(1.5, 1.2, 0.6), smoothstep(0.85, 1.0, t));
}
// Bump mapping from a height through screen derivatives (no tangents needed).
vec3 lvBump(vec3 surfPos, vec3 surfNorm, float height) {
  vec3 sx = dFdx(surfPos), sy = dFdy(surfPos);
  vec3 r1 = cross(sy, surfNorm), r2 = cross(surfNorm, sx);
  float det = dot(sx, r1);
  vec2 dh = vec2(dFdx(height), dFdy(height));
  vec3 grad = sign(det) * (dh.x * r1 + dh.y * r2);
  return normalize(abs(det) * surfNorm - grad);
}`;

// After color_fragment: the lava's colour, glow and crust height.
const COLOR = /* glsl */ `
{
  float lvS = max(lvScale, 0.01);
  // Flow in Blender's axes: three.js's z is Blender's -Y, and its uv.y is Blender's V flipped.
  vec2 lvF = vec2(lvFlow.x, -lvFlow.y) / lvS;
  #ifdef LV_UV
    vec2 lvP = vLvUv / lvS;
    // A channel: U = 0 and 1 are the banks. Fast and hot down the middle, crusted and slow at the banks.
    float lvBank = abs(vLvUv.x * 2.0 - 1.0);
    lvF *= 0.25 + 0.75 * (1.0 - lvBank * lvBank);
  #else
    vec2 lvP = vLvWorld.xz / lvS;
    float lvBank = 0.0;
  #endif
  // Heat: hotter and cooler patches drifting slowly.
  float lvHn = lvNoise(lvP * 0.21 - lvF * lvTime * 0.3 + 3.1);
  float lvHeatHere = clamp(lvHeat * 1.25 - 0.1 + (lvHn - 0.5) * 0.7 - lvBank * 0.45, 0.0, 1.0);
  // Two phases of flow-mapped scrolling, cross-faded and offset by noise so the blend never pulses in
  // sync: plates travel, crack and re-form without being sheared into threads.
  float lvJitter = lvNoise(lvP * 0.37);
  float lvPh0 = fract(lvTime / 3.2 + lvJitter), lvPh1 = fract(lvTime / 3.2 + lvJitter + 0.5);
  float lvW = 1.0 - abs(lvPh0 * 2.0 - 1.0);
  vec2 lvQ0 = lvP - lvF * lvPh0 * 3.2, lvQ1 = lvP - lvF * lvPh1 * 3.2 + vec2(4.37, 2.61);
  // Domain warp: torn rafts, not a honeycomb.
  vec2 lvWarp0 = vec2(lvNoise(lvQ0 * 0.55), lvNoise(lvQ0 * 0.55 + 5.2)) - 0.5;
  vec2 lvWarp1 = vec2(lvNoise(lvQ1 * 0.55 + 2.7), lvNoise(lvQ1 * 0.55 + 8.1)) - 0.5;
  vec2 lvC0 = lvCells(lvQ0 * vec2(1.2, 1.0) + lvWarp0 * 1.4), lvC1 = lvCells(lvQ1 * vec2(1.2, 1.0) + lvWarp1 * 1.4);
  float lvStreak = mix(lvNoise(lvQ1 * vec2(0.9, 0.3)), lvNoise(lvQ0 * vec2(0.9, 0.3)), lvW);
  float lvFine = mix(lvNoise(lvQ1 * 3.1), lvNoise(lvQ0 * 3.1), lvW);
  // Crust thickens where it is cool and at the banks.
  float lvCrust = clamp((1.0 - lvHeatHere) * 1.2 + lvBank * lvBank * 0.5 + (lvNoise(lvP * 0.13 + 0.3) - 0.5) * 0.5 + 0.12, 0.0, 1.0);
  float lvPlate0 = lvCrust * (0.7 + 0.6 * lvC0.y), lvPlate1 = lvCrust * (0.7 + 0.6 * lvC1.y);
  float lvGap0 = mix(0.5, 0.025, lvPlate0), lvGap1 = mix(0.5, 0.025, lvPlate1);
  float lvOpenHere = mix(1.0 - smoothstep(lvGap1 * 0.35, lvGap1, lvC1.x), 1.0 - smoothstep(lvGap0 * 0.35, lvGap0, lvC0.x), lvW);
  float lvRim = mix(exp(-lvC1.x / max(lvGap1 * 2.2, 0.03)), exp(-lvC0.x / max(lvGap0 * 2.2, 0.03)), lvW);
  lvOpenHere = max(lvOpenHere, 1.0 - smoothstep(0.03, 0.2, lvCrust));
  float lvThick = mix(lvPlate1, lvPlate0, lvW);
  // Plates smaller than a few pixels blend to their average instead of sparkling.
  float lvAA = smoothstep(0.35, 1.0, length(fwidth(lvP)));
  lvOpenHere = mix(lvOpenHere, mix(0.45, 0.08, lvCrust), lvAA);
  lvRim *= 1.0 - lvAA;
  // Molten lava: hotter where the heat is, streaked along the flow.
  vec3 lvMolten = lvRamp(0.38 + 0.32 * lvHeatHere + (lvStreak - 0.5) * 0.45 - lvBank * 0.2);
  // Crust: dark, with dull red heat bleeding through thin plates, their rims and hairline cracks.
  float lvHair = smoothstep(0.8, 0.95, lvFine) * (1.0 - lvThick * 0.8) * (1.0 - lvAA);
  vec3 lvCrustGlow = lvRamp(0.4 - lvThick * 0.25) * (lvRim * (1.0 - lvThick) * 0.8 + lvHair * 0.5);
  lvGlow = mix(lvCrustGlow, lvMolten, lvOpenHere) * lvBrightness;
  lvOpen = lvOpenHere;
  lvHeight = (1.0 - lvOpenHere) * (0.55 + 0.45 * lvThick) * 0.06 * lvS;
  diffuseColor.rgb = mix(vec3(0.045, 0.038, 0.034) * (0.7 + 0.6 * mix(lvC1.y, lvC0.y, lvW)), vec3(0.02), lvOpenHere);
}`;

// After roughnessmap_fragment / metalnessmap_fragment.
const ROUGHNESS = /* glsl */ `
roughnessFactor = mix(0.85, 0.4, lvOpen);`;
const METALNESS = /* glsl */ `
metalnessFactor = 0.0;`;

// After normal_fragment_maps: the plates stand up from the molten rock.
const NORMAL = /* glsl */ `
normal = lvBump(-vViewPosition, normal, lvHeight);`;

// After emissivemap_fragment.
const EMISSIVE = /* glsl */ `
totalEmissiveRadiance += lvGlow;`;

export const apply: ShaderApply<typeof fields> = (material, values, ctx) => {
  if (!(material as MeshStandardMaterial).isMeshStandardMaterial) {
    throw new Error('Lava needs a Principled BSDF material (MeshStandardMaterial in the game)');
  }
  // The lava's own look: Blender's emission would add to its glow.
  const standard = material as MeshStandardMaterial;
  standard.emissive = new Color(0, 0, 0);
  standard.emissiveMap = null;
  const uv = values.mapping === 'uv';
  const u = ctx.uniforms;
  const uniforms = { lvTime: ctx.time, lvColor: u.color, lvHeat: u.heat, lvScale: u.scale, lvFlow: u.flow, lvBrightness: u.brightness };
  patchShader(material, uv ? 'gs-lava-uv' : 'gs-lava', (shader) => {
    Object.assign(shader.uniforms, uniforms);
    shader.vertexShader = shader.vertexShader
      .replace('#include <common>', `#include <common>\n${VERTEX_PARS}`)
      .replace('#include <project_vertex>', `#include <project_vertex>\n${VERTEX}`);
    shader.fragmentShader = shader.fragmentShader
      .replace('#include <common>', `#include <common>\n${uv ? '#define LV_UV\n' : ''}${FRAGMENT_PARS}`)
      .replace('#include <color_fragment>', `#include <color_fragment>\n${COLOR}`)
      .replace('#include <roughnessmap_fragment>', `#include <roughnessmap_fragment>\n${ROUGHNESS}`)
      .replace('#include <metalnessmap_fragment>', `#include <metalnessmap_fragment>\n${METALNESS}`)
      .replace('#include <normal_fragment_maps>', `#include <normal_fragment_maps>\n${NORMAL}`)
      .replace('#include <emissivemap_fragment>', `#include <emissivemap_fragment>\n${EMISSIVE}`);
  });
};
