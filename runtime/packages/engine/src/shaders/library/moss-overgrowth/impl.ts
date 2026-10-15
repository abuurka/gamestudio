// After FX Lab "moss-overgrowth" (a growth front that prefers upward faces and low ground, creeping into cracks first).
import type { MeshStandardMaterial } from 'three';
import { patchShader, type ShaderApply } from '../../../schema/shaders';
import type { fields } from './meta';

const VERTEX_PARS = /* glsl */ `
varying vec3 vMossWorld;
varying float vMossHeight;
`;

// World position, and height above the object's (or instance's) origin: moss climbs from the base.
const VERTEX = /* glsl */ `
#include <project_vertex>
{
  mat4 mossModel = modelMatrix;
#ifdef USE_INSTANCING
  mossModel = modelMatrix * instanceMatrix;
#endif
  vec4 mossPosition = vec4(transformed, 1.0);
#ifdef USE_BATCHING
  mossPosition = batchingMatrix * mossPosition;
#endif
  vMossWorld = (mossModel * mossPosition).xyz;
  vMossHeight = vMossWorld.y - mossModel[3].y;
}
`;

// mossHash is David Hoskins' hash13 ("Hash without Sine", https://www.shadertoy.com/view/4djSRW, MIT;
// THIRD-PARTY-NOTICES.md).
const FRAGMENT_PARS = /* glsl */ `
uniform float uMossProgress;
uniform vec4 uMossColor;
uniform float uMossScale;
uniform float uMossHeight;
varying vec3 vMossWorld;
varying float vMossHeight;
float mossHash(vec3 p) {
  p = fract(p * 0.1031);
  p += dot(p, p.zyx + 31.32);
  return fract((p.x + p.y) * p.z);
}
float mossNoise(vec3 p) {
  vec3 i = floor(p), f = fract(p);
  vec3 u = f * f * (3.0 - 2.0 * f);
  return mix(
    mix(mix(mossHash(i), mossHash(i + vec3(1.0, 0.0, 0.0)), u.x), mix(mossHash(i + vec3(0.0, 1.0, 0.0)), mossHash(i + vec3(1.0, 1.0, 0.0)), u.x), u.y),
    mix(mix(mossHash(i + vec3(0.0, 0.0, 1.0)), mossHash(i + vec3(1.0, 0.0, 1.0)), u.x), mix(mossHash(i + vec3(0.0, 1.0, 1.0)), mossHash(i + vec3(1.0, 1.0, 1.0)), u.x), u.y),
    u.z);
}
// Bump from a height (metres) by screen derivatives, after Mikkelsen: world-consistent strength.
vec3 mossBump(vec3 n, vec3 position, float height) {
  vec3 dpx = dFdx(position), dpy = dFdy(position);
  vec3 r1 = cross(dpy, n), r2 = cross(n, dpx);
  float det = dot(dpx, r1);
  vec3 grad = sign(det) * (dFdx(height) * r1 + dFdy(height) * r2);
  return normalize(abs(det) * n - grad);
}
`;

// The front: progress, upward faces and low parts first, broken up by large patches and clumps.
// Ahead of it the surface darkens with damp; right at it young moss is a brighter green.
const GROWTH = /* glsl */ `
#include <normal_fragment_maps>
vec3 mossUp = normalize((viewMatrix * vec4(0.0, 1.0, 0.0, 0.0)).xyz);
vec3 mossP = vMossWorld / max(uMossScale, 0.01);
float mossPatch = mossNoise(mossP * 0.32) * 0.65 + mossNoise(mossP * 0.75 + 11.0) * 0.35;
float mossClump = mossNoise(mossP * 3.1 + 3.0);
// Fibres and grains, each averaged out before it gets smaller than a pixel (no shimmer far away).
float mossFibre = mix(0.5, mossNoise(mossP * 14.0 + 5.0), 1.0 - smoothstep(0.3, 0.8, length(fwidth(mossP * 14.0))));
float mossGrain = mix(0.5, mossNoise(mossP * 45.0 + 1.0), 1.0 - smoothstep(0.3, 0.8, length(fwidth(mossP * 45.0))));
float mossFront = mix(-2.9, 3.2, clamp(uMossProgress, 0.0, 1.0)) + dot(normal, mossUp) * 0.9
  - clamp(vMossHeight / max(uMossHeight, 0.01), 0.0, 2.0) * 0.8
  + (mossPatch - 0.5) * 2.4 + (mossClump - 0.5) * 0.9 + (mossFibre - 0.5) * 0.5;
float mossMask = smoothstep(0.0, 0.18, mossFront);
float mossYoung = smoothstep(-0.2, 0.0, mossFront) * (1.0 - mossMask);
float mossDamp = smoothstep(-1.2, 0.0, mossFront) * (1.0 - mossMask);
diffuseColor.rgb *= 1.0 - 0.3 * mossDamp;
// Dark hollows between the cushions, lit tops, yellowed patches, fibres.
vec3 mossColor = uMossColor.rgb * mix(0.4, 1.15, smoothstep(0.25, 0.75, mossClump));
mossColor = mix(mossColor, vec3(0.33, 0.3, 0.07), smoothstep(0.62, 0.85, mossPatch) * 0.4);
mossColor *= (0.7 + 0.6 * mossFibre) * (0.75 + 0.5 * mossGrain);
diffuseColor.rgb = mix(diffuseColor.rgb, mossColor, mossMask);
diffuseColor.rgb = mix(diffuseColor.rgb, uMossColor.rgb * 1.25 + vec3(0.12, 0.1, 0.0), mossYoung * 0.7);
roughnessFactor = mix(roughnessFactor, 1.0, mossMask);
metalnessFactor = mix(metalnessFactor, 0.0, max(mossMask, mossYoung));
// Cushions, fibres and grains raised over the surface.
float mossRelief = (mossClump * 0.05 + mossFibre * 0.012 + mossGrain * 0.004) * uMossScale * mossMask;
normal = mossBump(normal, -vViewPosition, mossRelief);
`;

// Fuzz: tiny fibres scatter light at grazing angles.
const FUZZ = /* glsl */ `
#include <emissivemap_fragment>
{
  float mossRim = pow(clamp(1.0 - abs(dot(normal, normalize(vViewPosition))), 0.0, 1.0), 3.0);
  totalEmissiveRadiance += uMossColor.rgb * (mossRim * mossMask * 0.12 + mossYoung * 0.03);
}
`;

export const apply: ShaderApply<typeof fields> = (material, _values, ctx) => {
  if (!(material as MeshStandardMaterial).isMeshStandardMaterial) {
    throw new Error('MossOvergrowth needs a Principled BSDF material (MeshStandardMaterial in the game)');
  }
  const u = ctx.uniforms;
  patchShader(material, 'gs-moss-overgrowth', (shader) => {
    shader.uniforms.uMossProgress = u.progress;
    shader.uniforms.uMossColor = u.color;
    shader.uniforms.uMossScale = u.scale;
    shader.uniforms.uMossHeight = u.height;
    shader.vertexShader = shader.vertexShader
      .replace('#include <common>', `#include <common>\n${VERTEX_PARS}`)
      .replace('#include <project_vertex>', VERTEX);
    shader.fragmentShader = shader.fragmentShader
      .replace('#include <common>', `#include <common>\n${FRAGMENT_PARS}`)
      .replace('#include <normal_fragment_maps>', GROWTH)
      .replace('#include <emissivemap_fragment>', FUZZ);
  });
};
