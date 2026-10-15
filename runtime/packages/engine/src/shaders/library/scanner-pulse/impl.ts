// After FX Lab "scanner-pulse": a ring sweeps out from an origin and leaves a grid, contour lines and
// lit silhouettes in its wake (per material here, instead of a screen pass over the depth buffer).
import type { MeshStandardMaterial } from 'three';
import { patchShader, type ShaderApply } from '../../../schema/shaders';
import type { fields } from './meta';

type F = typeof fields;

const VERTEX_DECLARATIONS = /* glsl */ `
varying vec3 vGsScanWorld;
`;

const VERTEX = /* glsl */ `
vec4 gsScanWorld = vec4(transformed, 1.0);
#ifdef USE_BATCHING
gsScanWorld = batchingMatrix * gsScanWorld;
#endif
#ifdef USE_INSTANCING
gsScanWorld = instanceMatrix * gsScanWorld;
#endif
vGsScanWorld = (modelMatrix * gsScanWorld).xyz;
`;

// gsScanHash12 is David Hoskins' hash12 ("Hash without Sine", https://www.shadertoy.com/view/4djSRW, MIT;
// THIRD-PARTY-NOTICES.md).
const FRAGMENT_DECLARATIONS = /* glsl */ `
uniform float gsScanTime;
uniform vec3 gsScanOrigin;
uniform float gsScanStartAt;
uniform float gsScanInterval;
uniform float gsScanReach;
uniform float gsScanSweep;
uniform vec4 gsScanColor;
uniform float gsScanStrength;
uniform float gsScanGrid;
varying vec3 vGsScanWorld;
float gsScanHash12(vec2 p) { vec3 p3 = fract(vec3(p.xyx) * 0.1031); p3 += dot(p3, p3.yzx + 33.33); return fract((p3.x + p3.y) * p3.z); }
float gsScanNoise(vec2 p) {
  vec2 i = floor(p), f = fract(p);
  f = f * f * (3.0 - 2.0 * f);
  return mix(mix(gsScanHash12(i), gsScanHash12(i + vec2(1.0, 0.0)), f.x), mix(gsScanHash12(i + vec2(0.0, 1.0)), gsScanHash12(i + vec2(1.0, 1.0)), f.x), f.y);
}
/** Anti-aliased line every unit of x, 'width' pixels wide. */
float gsScanLine(float x, float width) {
  float f = abs(fract(x + 0.5) - 0.5) / max(fwidth(x), 1e-4);
  return 1.0 - smoothstep(width * 0.5, width * 0.5 + 1.0, f);
}
`;

// Lines need screen derivatives, so they are computed for every pixel, outside any branch.
const FRAGMENT = /* glsl */ `
{
  vec3 gsP = vGsScanWorld;
  float gsGridSize = max(gsScanGrid, 0.01);
  vec3 gsN = inverseTransformDirection(normal, viewMatrix);
  float gsUp = abs(gsN.y);
  float gsGridMinor = max(gsScanLine(gsP.x / gsGridSize, 1.2), gsScanLine(gsP.z / gsGridSize, 1.2));
  float gsGridMajor = max(gsScanLine(gsP.x / (gsGridSize * 5.0), 2.0), gsScanLine(gsP.z / (gsGridSize * 5.0), 2.0));
  float gsGrid = max(gsGridMinor * 0.55, gsGridMajor) * smoothstep(0.75, 0.95, gsUp);
  float gsContour = gsScanLine((gsP.y - gsScanOrigin.y) / (gsGridSize * 0.3), 1.5) * smoothstep(0.85, 0.5, gsUp);
  float gsTopo = gsScanLine((gsP.y - gsScanOrigin.y) / gsGridSize, 1.5) * step(0.3, gsP.y - gsScanOrigin.y) * smoothstep(0.5, 0.9, gsUp);
  // Silhouettes light up as the wake passes (the FX read depth edges); floors are left to the grid.
  float gsEdge = pow(1.0 - clamp(abs(dot(normal, normalize(vViewPosition))), 0.0, 1.0), 4.0) * (1.0 - smoothstep(0.6, 0.9, gsUp));
  float gsSince = gsScanTime - gsScanStartAt;
  float gsAge = gsScanInterval > 0.0 ? mod(gsSince, gsScanInterval) : gsSince;
  float gsSweep = max(gsScanSweep, 0.05);
  float gsReach = max(gsScanReach, 0.1);
  if (gsSince >= 0.0 && gsAge < gsSweep + 2.1 && gsScanStrength > 0.0) {
    float dist = length(gsP - gsScanOrigin);
    float x = clamp(gsAge / gsSweep, 0.0, 1.0);
    float radius = gsReach * (1.0 - (1.0 - x) * (1.0 - x));
    // Seconds since the front passed this point.
    float hit = gsSweep * (1.0 - sqrt(max(0.0, 1.0 - min(dist / gsReach, 1.0))));
    float wakeAge = gsAge - hit;
    // Front: a thin bright edge in a soft halo, a faint gradient trailing it, broken up by moving noise.
    // Narrower than the FX's night-time ring, so it stays a line on a sunlit surface.
    float width = 0.08 + 0.01 * radius;
    float dr = dist - radius;
    float fade = 1.0 - smoothstep(0.75, 1.0, radius / gsReach);
    float grain = 0.75 + 0.5 * gsScanNoise(gsP.xz * 1.7 + vec2(gsAge * 3.0, gsP.y * 2.0));
    float front = (exp(-dr * dr / (width * width)) + exp(-dr * dr / (width * width * 16.0)) * 0.2) * grain;
    float trail = dr < 0.0 ? exp(dr / (0.9 + 0.14 * radius)) : 0.0;
    // A faint echo ring a beat behind the front.
    float de = dist - radius * 0.86;
    float echo = exp(-de * de / (width * width * 0.5)) * 0.35;
    // Wake: the geometry read-out that fades over about 1.5 s.
    float wake = dr < 0.0 ? exp(-max(wakeAge, 0.0) / 0.8) * (1.0 - smoothstep(0.9, 1.6, wakeAge)) : 0.0;
    vec3 scanColor = gsScanColor.rgb;
    vec3 scanHot = mix(scanColor, vec3(1.0), 0.3);
    vec3 light = scanHot * (front * 4.2 + echo) * fade + scanColor * trail * 0.15 * fade;
    light += scanColor * (gsGrid * 2.2 + gsContour * 2.2 + gsTopo * 1.8) * wake;
    light += scanHot * gsEdge * (wake * 2.4 + front * 4.0);
    // Close to the origin the wake would cover everything; keep it about the world around it.
    float near = smoothstep(0.4, 1.6, dist) * gsScanStrength;
    totalEmissiveRadiance += light * near;
    // Dim the scanned world so the read-out pops, as in a scanner view.
    diffuseColor.rgb *= 1.0 - clamp((wake * 0.5 + trail * 0.15) * near, 0.0, 0.7);
  }
}
`;

export const apply: ShaderApply<F> = (material, _values, ctx) => {
  if (!(material as MeshStandardMaterial).isMeshStandardMaterial) {
    throw new Error('ScannerPulse needs a Principled BSDF material (MeshStandardMaterial in the game)');
  }
  const u = ctx.uniforms;
  const uniforms = {
    gsScanTime: ctx.time,
    gsScanOrigin: u.origin,
    gsScanStartAt: u.startAt,
    gsScanInterval: u.interval,
    gsScanReach: u.reach,
    gsScanSweep: u.sweep,
    gsScanColor: u.color,
    gsScanStrength: u.strength,
    gsScanGrid: u.gridSize,
  };
  patchShader(material, 'gs-scanner-pulse', (shader) => {
    Object.assign(shader.uniforms, uniforms);
    shader.vertexShader = shader.vertexShader
      .replace('#include <common>', `#include <common>\n${VERTEX_DECLARATIONS}`)
      .replace('#include <project_vertex>', `#include <project_vertex>\n${VERTEX}`);
    shader.fragmentShader = shader.fragmentShader
      .replace('#include <common>', `#include <common>\n${FRAGMENT_DECLARATIONS}`)
      .replace('#include <emissivemap_fragment>', `#include <emissivemap_fragment>\n${FRAGMENT}`);
  });
};
