// After FX Lab "waterfall" (the falling sheets: streaks that ride with the water, ropes with thin
// see-through gaps, frayed edges, white aeration) and "lava-river" (flow along a channel). Built on
// the exported MeshStandardMaterial: its colour tints the clear water, its alpha mode decides
// whether the gaps are see-through; the sky is reflected like Water's.

import { patchShader, skyGlsl, skyUniforms, type ShaderApply } from '../../../schema/shaders';
import type { MeshStandardMaterial } from 'three';
import type { fields } from './meta';

const VERTEX_PARS = /* glsl */ `
varying vec2 vFlUv;`;

const VERTEX = /* glsl */ `
vFlUv = uv;`;

// flHash is David Hoskins' hash22 ("Hash without Sine", https://www.shadertoy.com/view/4djSRW, MIT;
// THIRD-PARTY-NOTICES.md).
const FRAGMENT_PARS = /* glsl */ `
uniform float flTime;
uniform float flSpeed;
uniform float flScale;
uniform float flStretch;
uniform float flFoam;
uniform float flBreakup;
varying vec2 vFlUv;
float flHeight;
float flWhite;
// The sky the clear water reflects: the scene's own (ctx.sky), as the game draws the background.
${skyGlsl('fl')}
vec2 flHash(vec2 p) { vec3 p3 = fract(vec3(p.xyx) * vec3(0.1031, 0.1030, 0.0973)); p3 += dot(p3, p3.yzx + 33.33); return fract((p3.xx + p3.yz) * p3.zy) * 2.0 - 1.0; }
// Gradient noise, 0..1: rounder shapes than value noise, no grid squares when thresholded.
float flNoise(vec2 p) {
  vec2 i = floor(p), f = fract(p);
  vec2 u = f * f * f * (f * (f * 6.0 - 15.0) + 10.0);
  float a = dot(flHash(i), f), b = dot(flHash(i + vec2(1.0, 0.0)), f - vec2(1.0, 0.0));
  float c = dot(flHash(i + vec2(0.0, 1.0)), f - vec2(0.0, 1.0)), d = dot(flHash(i + vec2(1.0, 1.0)), f - vec2(1.0, 1.0));
  return mix(mix(a, b, u.x), mix(c, d, u.x), u.y) * 0.85 + 0.5;
}
// Bump mapping from a height through screen derivatives (no tangents needed).
vec3 flBump(vec3 surfPos, vec3 surfNorm, float height) {
  vec3 sx = dFdx(surfPos), sy = dFdy(surfPos);
  vec3 r1 = cross(sy, surfNorm), r2 = cross(surfNorm, sx);
  float det = dot(sx, r1);
  vec2 dh = vec2(dFdx(height), dFdy(height));
  vec3 grad = sign(det) * (dh.x * r1 + dh.y * r2);
  return normalize(abs(det) * surfNorm - grad);
}`;

// After color_fragment.
const COLOR = /* glsl */ `
{
  // U runs across the stream, V along it; three.js's uv.y is Blender's V flipped, so the water runs
  // toward Blender's V = 0.
  vec2 q = vec2(vFlUv.x, (vFlUv.y - flTime * flSpeed) / max(flStretch, 0.05)) * flScale;
  // Features ride with the water: streaks stretched along the flow, at three sizes.
  float a = flNoise(q);
  float b = flNoise(q * 2.3 + vec2(3.1, 0.4));
  float c = flNoise(q * 5.3 + vec2(7.0, 0.2));
  float streak = a * 0.45 + b * 0.35 + c * 0.2;
  // The edges wander, so a sheet's outline frays instead of running dead straight.
  float side = vFlUv.x * 2.0 - 1.0;
  float wander = flNoise(vec2(q.y * 0.7, side > 0.0 ? 13.7 : 0.0));
  float edge = abs(side) + (wander - 0.4) * 0.25;
  // Body: ropes of dense water with thin see-through gaps, torn apart at the edges.
  float tear = flBreakup * 0.85 + smoothstep(0.7, 1.0, edge) * 0.7 * flBreakup;
  float body = smoothstep(tear * 0.6, tear * 0.6 + 0.24, streak + 0.08);
  float ropes = smoothstep(0.3, 0.62, flNoise(vec2(q.x * 0.6 + 1.7, q.y * 0.22 + 0.3)));
  float density = body * mix(1.0 - 0.7 * flBreakup, 1.0, ropes) * (1.0 - smoothstep(0.96, 1.06, edge) * flBreakup);
  // White water: aerated streaks and frothy edges.
  float churn = streak * 0.75 + c * 0.35 + smoothstep(0.75, 1.0, edge) * 0.35;
  flWhite = smoothstep(1.02 - flFoam * 0.75, 1.32 - flFoam * 0.75, churn) * step(0.001, flFoam);
  flHeight = (streak + flWhite * 0.4) * 0.6 / flScale;
  diffuseColor.rgb = mix(diffuseColor.rgb * vec3(0.3, 0.42, 0.45) * mix(0.6, 1.0, density), vec3(0.9, 0.94, 0.97), flWhite);
  diffuseColor.a *= clamp(density * mix(0.85, 1.0, flWhite), 0.0, 1.0);
  #ifndef OPAQUE
    if (diffuseColor.a < 0.003) discard;
  #endif
}`;

// After roughnessmap_fragment.
const ROUGHNESS = /* glsl */ `
roughnessFactor = mix(0.08, 0.7, flWhite);`;

// After normal_fragment_maps.
const NORMAL = /* glsl */ `
normal = flBump(-vViewPosition, normal, flHeight);`;

// Before lights_fragment_end: the clear water reflects the sky.
const REFLECTION = /* glsl */ `
#if defined( RE_IndirectSpecular )
{
  vec3 flR = inverseTransformDirection(reflect(-geometryViewDir, geometryNormal), viewMatrix);
  radiance = mix(radiance, flSky(vec3(flR.x, abs(flR.y), flR.z)), 1.0 - flWhite);
}
#endif`;

export const apply: ShaderApply<typeof fields> = (material, _values, ctx) => {
  if (!(material as MeshStandardMaterial).isMeshStandardMaterial) {
    throw new Error('Flow needs a Principled BSDF material (MeshStandardMaterial in the game)');
  }
  (material as MeshStandardMaterial).metalness = 0;
  const u = ctx.uniforms;
  const uniforms = {
    flTime: ctx.time,
    flSpeed: u.speed,
    flScale: u.scale,
    flStretch: u.stretch,
    flFoam: u.foam,
    flBreakup: u.breakup,
    ...skyUniforms(ctx.sky, 'fl'),
  };
  patchShader(material, 'gs-flow', (shader) => {
    Object.assign(shader.uniforms, uniforms);
    shader.vertexShader = shader.vertexShader
      .replace('#include <common>', `#include <common>\n${VERTEX_PARS}`)
      .replace('#include <uv_vertex>', `#include <uv_vertex>\n${VERTEX}`);
    shader.fragmentShader = shader.fragmentShader
      .replace('#include <common>', `#include <common>\n${FRAGMENT_PARS}`)
      .replace('#include <color_fragment>', `#include <color_fragment>\n${COLOR}`)
      .replace('#include <roughnessmap_fragment>', `#include <roughnessmap_fragment>\n${ROUGHNESS}`)
      .replace('#include <normal_fragment_maps>', `#include <normal_fragment_maps>\n${NORMAL}`)
      .replace('#include <lights_fragment_end>', `${REFLECTION}\n#include <lights_fragment_end>`);
  });
};
