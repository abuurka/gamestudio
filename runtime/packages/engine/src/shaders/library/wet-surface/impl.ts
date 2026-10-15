// After FX Lab "rainstorm" (the world's wetness: darker albedo, smoother surface, flat puddles in the
// low patches; the wet sheen reflecting the sky; puddle ripple rings) and "neon-rain" (rain rings in
// offset grids rippling a wet street). Built on the exported MeshStandardMaterial: its colour, maps
// and lighting stay, wetness is layered on top.

import { patchShader, skyGlsl, skyUniforms, type ShaderApply } from '../../../schema/shaders';
import type { MeshStandardMaterial } from 'three';
import type { fields } from './meta';

const VERTEX_PARS = /* glsl */ `
varying vec3 vWsWorld;`;

// After project_vertex: the world position (skinned, instanced and batched copies included).
const VERTEX = /* glsl */ `
{
  vec4 wsPos = vec4(transformed, 1.0);
  #ifdef USE_BATCHING
    wsPos = batchingMatrix * wsPos;
  #endif
  #ifdef USE_INSTANCING
    wsPos = instanceMatrix * wsPos;
  #endif
  vWsWorld = (modelMatrix * wsPos).xyz;
}`;

// wsHash is David Hoskins' hash12 ("Hash without Sine", https://www.shadertoy.com/view/4djSRW, MIT;
// THIRD-PARTY-NOTICES.md).
const FRAGMENT_PARS = /* glsl */ `
uniform float wsTime;
uniform float wsWetness;
uniform float wsPuddles;
uniform float wsPuddleSize;
uniform float wsRain;
varying vec3 vWsWorld;
float wsUp;
float wsPuddle;
float wsDrip;
// The sky puddles and the wet sheen reflect: the scene's own (ctx.sky), as the game draws the background.
${skyGlsl('ws')}
float wsHash(vec2 p) { vec3 p3 = fract(vec3(p.xyx) * 0.1031); p3 += dot(p3, p3.yzx + 33.33); return fract((p3.x + p3.y) * p3.z); }
float wsNoise(vec2 p) {
  vec2 i = floor(p), f = fract(p);
  vec2 u = f * f * (3.0 - 2.0 * f);
  return mix(mix(wsHash(i), wsHash(i + vec2(1.0, 0.0)), u.x), mix(wsHash(i + vec2(0.0, 1.0)), wsHash(i + vec2(1.0, 1.0)), u.x), u.y);
}
// Rain rings: drops land at random spots of three offset grids and each sends out a ring; returns the
// slope of the water surface.
vec2 wsRipples(vec2 xz, float t) {
  vec2 slope = vec2(0.0);
  for (int i = 0; i < 3; i++) {
    float fi = float(i);
    vec2 p = xz * 2.6 + fi * vec2(0.37, 0.61);
    vec2 id = floor(p);
    vec2 f = fract(p) - 0.5;
    float h0 = wsHash(id + fi * 13.7), h1 = wsHash(id * 1.31 + 4.7 + fi), h2 = wsHash(id * 0.71 + 9.1 - fi);
    vec2 dv = f - (vec2(h0, h1) - 0.5) * 0.4;
    float phase = fract(t * mix(0.8, 1.5, h2) + h0 * 7.0);
    float r = length(dv);
    float x = (r - phase * 0.3) * 38.0;
    float ring = sin(x) * exp(-x * x * 0.06) * (1.0 - phase) * (1.0 - phase) * (1.0 - smoothstep(0.32, 0.48, r));
    slope += (r > 1e-4 ? dv / r : vec2(0.0)) * ring;
  }
  return slope;
}`;

// After normal_fragment_maps: puddles are flat water (they drown the normal map), stirred by rain;
// walls get drips running down.
const NORMAL = /* glsl */ `
{
  vec3 wsGeo = inverseTransformDirection(nonPerturbedNormal, viewMatrix);
  wsUp = smoothstep(0.55, 0.9, wsGeo.y);
  // Puddles: the low patches of a broad noise, only on ground that faces up.
  vec2 wsXZ = vWsWorld.xz / max(wsPuddleSize, 0.05);
  float wsBroad = wsNoise(wsXZ * 0.5 + vec2(0.31, 0.77)) * 0.65 + wsNoise(wsXZ * 1.7 + vec2(0.53, 0.11)) * 0.35;
  float wsEdge = mix(0.74, 0.26, wsPuddles);
  wsPuddle = smoothstep(wsEdge, wsEdge + 0.04, wsBroad) * wsUp * step(0.001, wsPuddles) * smoothstep(0.3, 0.8, wsWetness);
  normal = normalize(mix(normal, nonPerturbedNormal, wsPuddle * 0.9));
  vec2 wsSlope = wsRipples(vWsWorld.xz, wsTime) * wsRain * (0.3 + 0.7 * wsPuddle) * wsUp * wsWetness * 0.5;
  normal = normalize(normal + (viewMatrix * vec4(-wsSlope.x, 0.0, -wsSlope.y, 0.0)).xyz);
  // Drips: thin wet streaks sliding down steep faces.
  float wsSide = 1.0 - smoothstep(0.3, 0.7, abs(wsGeo.y));
  vec2 wsDp = vec2(dot(vWsWorld.xz, normalize(vec2(-wsGeo.z, wsGeo.x) + 1e-4)) * 16.0, vWsWorld.y * 0.4 + wsTime * 0.35);
  wsDrip = smoothstep(0.6, 0.8, wsNoise(wsDp) * 0.75 + wsNoise(wsDp * vec2(2.3, 0.5) + 3.7) * 0.25) * wsSide * min(wsRain, 1.0) * wsWetness;
}`;

// After emissivemap_fragment (the colour and roughness the lighting reads are still open here).
const SURFACE = /* glsl */ `
{
  // Water fills the pores: soaked surfaces darken and turn glossy; puddles are almost mirrors.
  float wsWet = clamp(wsWetness * mix(0.7, 1.0, wsUp) + wsDrip * 0.4, 0.0, 1.0);
  diffuseColor.rgb *= mix(1.0, 0.5, wsWet) * mix(1.0, 0.6, wsPuddle);
  roughnessFactor = mix(roughnessFactor, mix(min(roughnessFactor, 0.3), 0.03, wsPuddle), wsWet);
}`;

// Before lights_fragment_end: puddles mirror the sky, wet ground gets a sheen of it.
const REFLECTION = /* glsl */ `
#if defined( RE_IndirectSpecular )
{
  vec3 wsR = inverseTransformDirection(reflect(-geometryViewDir, geometryNormal), viewMatrix);
  radiance = mix(radiance, wsSky(vec3(wsR.x, abs(wsR.y), wsR.z)), max(wsPuddle, wsWetness * wsUp * 0.6));
}
#endif`;

export const apply: ShaderApply<typeof fields> = (material, _values, ctx) => {
  if (!(material as MeshStandardMaterial).isMeshStandardMaterial) {
    throw new Error('WetSurface needs a Principled BSDF material (MeshStandardMaterial in the game)');
  }
  const u = ctx.uniforms;
  const uniforms = {
    wsTime: ctx.time,
    wsWetness: u.wetness,
    wsPuddles: u.puddles,
    wsPuddleSize: u.puddleSize,
    wsRain: u.rain,
    ...skyUniforms(ctx.sky, 'ws'),
  };
  patchShader(material, 'gs-wet-surface', (shader) => {
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
};
