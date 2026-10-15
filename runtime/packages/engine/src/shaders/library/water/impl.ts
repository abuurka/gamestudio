// After FX Lab "shoreline" (the ocean), "ripple-pond" and "storm-ocean": waves at deep-water speeds
// that fade out once shorter than a pixel, a Fresnel sky reflection with sun glints, a dark body
// tinted by the material's colour, and whitecaps of drifting lace on the crests. Built on the
// exported MeshStandardMaterial, so lights, shadows, fog and lightmaps keep working.

import { MeshStandardMaterial, type Material } from 'three';
import { patchShader, skyGlsl, skyUniforms, type ShaderApply, type ShaderMesh } from '../../../schema/shaders';
import type { fields } from './meta';

const VERTEX_PARS = /* glsl */ `
varying vec3 vWaWorld;`;

// After project_vertex: the world position, instanced and batched copies included.
const VERTEX = /* glsl */ `
{
  vec4 waPos = vec4(transformed, 1.0);
  #ifdef USE_BATCHING
    waPos = batchingMatrix * waPos;
  #endif
  #ifdef USE_INSTANCING
    waPos = instanceMatrix * waPos;
  #endif
  vWaWorld = (modelMatrix * waPos).xyz;
}`;

// waHash is David Hoskins' hash12 ("Hash without Sine", https://www.shadertoy.com/view/4djSRW, MIT;
// THIRD-PARTY-NOTICES.md).
const FRAGMENT_PARS = /* glsl */ `
uniform float waTime;
uniform float waLength;
uniform float waChop;
uniform float waSpeed;
uniform float waDirection;
uniform float waFoam;
uniform float waReflection;
varying vec3 vWaWorld;
vec3 waNormal;
float waHeight;
float waLost; // slope of the waves too small to draw here, which blurs the reflection instead
float waFoamMask;
// The sky a reflection sees: the scene's own (ctx.sky), as the game draws the background.
${skyGlsl('wa')}
float waHash(vec2 p) { vec3 p3 = fract(vec3(p.xyx) * 0.1031); p3 += dot(p3, p3.yzx + 33.33); return fract((p3.x + p3.y) * p3.z); }
float waNoise(vec2 p) {
  vec2 i = floor(p), f = fract(p);
  vec2 u = f * f * (3.0 - 2.0 * f);
  return mix(mix(waHash(i), waHash(i + vec2(1.0, 0.0)), u.x), mix(waHash(i + vec2(0.0, 1.0)), waHash(i + vec2(1.0, 1.0)), u.x), u.y);
}
// The slope of value noise (analytic): irregular ripples that break up the regular waves.
vec2 waNoiseSlope(vec2 p) {
  vec2 i = floor(p), f = fract(p);
  vec2 u = f * f * (3.0 - 2.0 * f), du = 6.0 * f * (1.0 - f);
  float a = waHash(i), b = waHash(i + vec2(1.0, 0.0)), c = waHash(i + vec2(0.0, 1.0)), d = waHash(i + vec2(1.0, 1.0));
  return du * vec2(b - a + (a - b - c + d) * u.y, c - a + (a - b - c + d) * u.x);
}
// Ten directional waves at deep-water speeds, longest first, spread wider around the wind as they get
// shorter. Returns the slope (xy) and the height (z, about -1 in a trough to 1 on a crest). A wave
// shorter than a few pixels fades out, so far water calms to a clean reflection instead of shimmering.
vec3 waWaves(vec2 p, float t, float footprint) {
  vec2 slope = vec2(0.0);
  float height = 0.0, total = 0.0;
  float len = max(waLength, 0.05);
  waLost = 0.0;
  for (int i = 0; i < 10; i++) {
    float fi = float(i);
    float a = waDirection + (fract(fi * 0.6180339) - 0.5) * mix(1.4, 3.2, fi / 9.0);
    vec2 d = vec2(cos(a), -sin(a)); // Blender's +Y is three.js's -Z
    float k = 6.2831853 / len;
    float fade = 1.0 - smoothstep(0.12 * len, 0.4 * len, footprint);
    float steep = 0.1 * (0.65 + 0.7 * fract(fi * 0.7548777));
    float ph = k * dot(d, p) - sqrt(9.81 * k) * t + fi * 2.39;
    slope += d * (steep * fade * cos(ph));
    height += steep / k * fade * sin(ph);
    total += steep / k;
    waLost += steep * steep * (1.0 - fade);
    len *= 0.7;
  }
  // Irregular ripples drifting with the wind, at two sizes, so the waves never look combed.
  vec2 wind = vec2(cos(waDirection), -sin(waDirection));
  for (int i = 0; i < 2; i++) {
    float r = max(waLength, 0.05) * (i == 0 ? 0.21 : 0.075);
    float fade = 1.0 - smoothstep(0.12 * r * 2.0, 0.4 * r * 2.0, footprint);
    vec2 q = p / r - wind * t * sqrt(1.56 * r) / r + float(i) * 17.3;
    slope += waNoiseSlope(mat2(0.8, -0.6, 0.6, 0.8) * q) * (0.075 * fade);
  }
  // Calmer and rougher patches drift over big water, so it never reads as one repeating tile.
  float patches = 0.55 + 0.9 * waNoise(p / (max(waLength, 0.05) * 9.0) + t * vec2(0.004, 0.002));
  return vec3(slope * waChop * patches, height / total);
}`;

// After normal_fragment_maps.
const NORMAL = /* glsl */ `
{
  // Metres per pixel here: shorter waves fade out.
  float waFoot = max(length(dFdx(vWaWorld)), length(dFdy(vWaWorld)));
  vec3 waW = waWaves(vWaWorld.xz, waTime * waSpeed, waFoot);
  vec3 waN0 = inverseTransformDirection(normal, viewMatrix);
  // Tilted as much as the face points up (or down, seen from below): the sides of a water volume stay flat.
  waNormal = normalize(waN0 + vec3(-waW.x, 0.0, -waW.y) * waN0.y);
  normal = normalize((viewMatrix * vec4(waNormal, 0.0)).xyz);
  waHeight = waW.z;
}`;

// After emissivemap_fragment: the colour, alpha and roughness the lighting uses.
const SURFACE = /* glsl */ `
{
  vec3 waV = isOrthographic ? inverseTransformDirection(vec3(0.0, 0.0, 1.0), viewMatrix) : normalize(cameraPosition - vWaWorld);
  float waF = 0.02 + 0.98 * pow(1.0 - clamp(dot(waNormal, waV), 0.0, 1.0), 5.0);
  // Whitecaps: the crests break into lace (ridges of drifting noise) that drifts with the wind.
  vec2 waP = vWaWorld.xz / max(waLength, 0.05);
  vec2 waDrift = vec2(cos(waDirection), -sin(waDirection)) * waTime * waSpeed * 0.08;
  float waL1 = waNoise(waP * 3.1 - waDrift);
  float waL2 = waNoise(waP * 8.3 - waDrift * 1.6 + 7.3);
  float waL3 = waNoise(waP * 21.0 - waDrift * 2.2 + 3.1);
  float waLace = (1.0 - abs(waL2 * 2.0 - 1.0)) * 0.55 + (1.0 - abs(waL3 * 2.0 - 1.0)) * 0.3 + waL1 * 0.35;
  float waCap = smoothstep(0.2, 0.9, waHeight) * waFoam;
  waFoamMask = smoothstep(1.02 - waCap * 0.7, 1.3 - waCap * 0.6, waLace + waCap * 0.25) * smoothstep(0.0, 0.05, waCap) * 0.9;
  // The body: the material's colour seen through the water, absorbed red first and dark as real water
  // is (most of what the eye gets from water is the sky it reflects). Light passing through the thin
  // crests brightens them. Grazing views and foam are opaque.
  vec3 waBody = diffuseColor.rgb * vec3(0.16, 0.3, 0.34) * (1.0 + 1.2 * smoothstep(0.2, 1.0, waHeight));
  diffuseColor.rgb = mix(waBody, vec3(0.9, 0.93, 0.95), waFoamMask);
  diffuseColor.a = mix(mix(diffuseColor.a, 1.0, waF), 1.0, waFoamMask);
  roughnessFactor = mix(0.06 + 0.5 * sqrt(waLost) * waChop, 0.75, waFoamMask);
  metalnessFactor = 0.0;
}`;

// Before lights_fragment_end: the environment reflection becomes the sky (with the sun's own glint
// from the direct light, shadowed like everything else).
const REFLECTION = /* glsl */ `
#if defined( RE_IndirectSpecular )
{
  vec3 waR = inverseTransformDirection(reflect(-geometryViewDir, geometryNormal), viewMatrix);
  waR.y = abs(waR.y);
  radiance = waSky(waR) * waReflection;
}
#endif`;

/** The material as a MeshStandardMaterial: an unlit export becomes one with its colour, map and alpha. */
function standard(material: Material): MeshStandardMaterial {
  if ((material as MeshStandardMaterial).isMeshStandardMaterial) return material as MeshStandardMaterial;
  const from = material as Material & Partial<Pick<MeshStandardMaterial, 'color' | 'map'>>;
  const out = new MeshStandardMaterial({ transparent: material.transparent, opacity: material.opacity, side: material.side, depthWrite: material.depthWrite });
  out.name = material.name;
  if (from.color) out.color.copy(from.color);
  if (from.map) out.map = from.map;
  return out;
}

export const apply: ShaderApply<typeof fields> = (material, _values, ctx) => {
  const target = standard(material);
  target.metalness = 0;
  target.roughness = 0.06;
  const u = ctx.uniforms;
  const uniforms = {
    waTime: ctx.time,
    waLength: u.waveLength,
    waChop: u.choppiness,
    waSpeed: u.speed,
    waDirection: u.direction,
    waFoam: u.foam,
    waReflection: u.reflection,
    ...skyUniforms(ctx.sky, 'wa'),
  };
  patchShader(target, 'gs-water', (shader) => {
    Object.assign(shader.uniforms, uniforms);
    shader.vertexShader = shader.vertexShader
      .replace('#include <common>', `#include <common>\n${VERTEX_PARS}`)
      .replace('#include <project_vertex>', `#include <project_vertex>\n${VERTEX}`);
    shader.fragmentShader = shader.fragmentShader
      .replace('#include <common>', `#include <common>\n${FRAGMENT_PARS}`)
      .replace('#include <normal_fragment_maps>', `#include <normal_fragment_maps>\n${NORMAL}`)
      .replace('#include <emissivemap_fragment>', `#include <emissivemap_fragment>\n${SURFACE}`)
      .replace('#include <lights_fragment_end>', `${REFLECTION}\n#include <lights_fragment_end>`);
  });
  return target === material ? undefined : target;
};

/** Water casts no shadow (it would darken the bed under it); it still receives them. */
export const mesh: ShaderMesh<typeof fields> = (mesh) => {
  mesh.castShadow = false;
};
