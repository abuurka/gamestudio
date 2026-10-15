// After FX Lab "blizzard" (settling snow on upward faces, broken up by noise) and "hailstorm" (white cover that thickens).
import type { MeshStandardMaterial } from 'three';
import { patchShader, type ShaderApply } from '../../../schema/shaders';
import type { fields } from './meta';

const VERTEX_PARS = /* glsl */ `
varying vec3 vSnowWorld;
`;

const VERTEX = /* glsl */ `
#include <project_vertex>
{
  vec4 snowPosition = vec4(transformed, 1.0);
#ifdef USE_BATCHING
  snowPosition = batchingMatrix * snowPosition;
#endif
#ifdef USE_INSTANCING
  snowPosition = instanceMatrix * snowPosition;
#endif
  vSnowWorld = (modelMatrix * snowPosition).xyz;
}
`;

// snowHash and snowHash3 are David Hoskins' hash12 and hash13 ("Hash without Sine",
// https://www.shadertoy.com/view/4djSRW, MIT; THIRD-PARTY-NOTICES.md).
const FRAGMENT_PARS = /* glsl */ `
uniform float uSnowAmount;
uniform vec4 uSnowColor;
uniform float uSnowScale;
uniform float uSnowSparkle;
varying vec3 vSnowWorld;
float snowHash(vec2 p) {
  vec3 p3 = fract(vec3(p.xyx) * 0.1031);
  p3 += dot(p3, p3.yzx + 33.33);
  return fract((p3.x + p3.y) * p3.z);
}
float snowHash3(vec3 p) {
  p = fract(p * 0.1031);
  p += dot(p, p.zyx + 31.32);
  return fract((p.x + p.y) * p.z);
}
float snowNoise3(vec3 p) {
  vec3 i = floor(p), f = fract(p);
  vec3 u = f * f * (3.0 - 2.0 * f);
  return mix(
    mix(mix(snowHash3(i), snowHash3(i + vec3(1.0, 0.0, 0.0)), u.x), mix(snowHash3(i + vec3(0.0, 1.0, 0.0)), snowHash3(i + vec3(1.0, 1.0, 0.0)), u.x), u.y),
    mix(mix(snowHash3(i + vec3(0.0, 0.0, 1.0)), snowHash3(i + vec3(1.0, 0.0, 1.0)), u.x), mix(snowHash3(i + vec3(0.0, 1.0, 1.0)), snowHash3(i + vec3(1.0, 1.0, 1.0)), u.x), u.y),
    u.z);
}
float snowNoise(vec2 p) {
  vec2 i = floor(p), f = fract(p);
  vec2 u = f * f * (3.0 - 2.0 * f);
  return mix(mix(snowHash(i), snowHash(i + vec2(1.0, 0.0)), u.x), mix(snowHash(i + vec2(0.0, 1.0)), snowHash(i + vec2(1.0, 1.0)), u.x), u.y);
}
`;

// Snow settles on faces that look up (after normal maps, so it fills their upward crevices first),
// in patches that grow and join as the amount rises; a damp rim darkens the surface where it thins.
const COVER = /* glsl */ `
#include <normal_fragment_maps>
vec3 snowUp = normalize((viewMatrix * vec4(0.0, 1.0, 0.0, 0.0)).xyz);
// Patches in 3D (steep faces get patches too, not streaks); fine breakup on the ground plane,
// sheared by height for the same reason.
vec3 snowP = vSnowWorld / max(uSnowScale, 0.01);
float snowMacro = snowNoise3(snowP) * 0.65 + snowNoise3(snowP * 2.3 + 7.1) * 0.35;
float snowFine = snowNoise((snowP.xz + snowP.y * vec2(0.71, -0.53)) * 17.0 + 3.7);
float snowAmount = clamp(uSnowAmount, 0.0, 1.0);
float snowCover = dot(normal, snowUp) + (snowMacro - 0.5) * 0.8 + (snowFine - 0.5) * 0.25;
float snowLine = mix(1.15, 0.1, snowAmount);
float snowFade = smoothstep(0.0, 0.1, snowAmount);
float snowMask = smoothstep(snowLine - 0.1, snowLine + 0.1, snowCover) * snowFade;
float snowDamp = smoothstep(snowLine - 0.3, snowLine - 0.1, snowCover) * (1.0 - snowMask) * snowFade;
diffuseColor.rgb *= 1.0 - 0.15 * snowDamp;
diffuseColor.rgb = mix(diffuseColor.rgb, uSnowColor.rgb * (0.92 + 0.08 * snowFine), snowMask);
roughnessFactor = mix(roughnessFactor, 0.75, snowMask);
metalnessFactor = mix(metalnessFactor, 0.0, snowMask);
// Snow blankets the surface's own detail.
normal = normalize(mix(normal, snowBaseNormal, snowMask * 0.75));
`;

// Ice crystals that catch the sun: sparse cells with a random facet and a sharp highlight of the key
// light (shadowed, so none in the shade).
const SPARKLE = /* glsl */ `
#include <lights_fragment_begin>
#if NUM_DIR_LIGHTS > 0
{
  vec3 sparkGrid = vSnowWorld * 60.0;
  vec3 sparkCell = floor(sparkGrid);
  float sparkId = snowHash3(sparkCell);
  vec3 sparkRand = vec3(snowHash3(sparkCell + 1.3), snowHash3(sparkCell + 7.1), snowHash3(sparkCell + 3.7));
  vec3 sparkFacet = normalize(geometryNormal + (sparkRand - 0.5) * 1.4);
  vec3 sparkHalf = normalize(directLight.direction + geometryViewDir);
  // A small round crystal somewhere in the cell, faded out where cells get smaller than a pixel.
  float sparkDot = 1.0 - smoothstep(0.12, 0.3, length(fract(sparkGrid) - 0.2 - sparkRand * 0.6));
  float sparkNear = 1.0 - smoothstep(0.35, 1.0, length(fwidth(sparkGrid)));
  float spark = step(0.7, sparkId) * sparkDot * pow(max(dot(sparkFacet, sparkHalf), 0.0), 120.0) * sparkNear;
  reflectedLight.directSpecular += directLight.color * spark * snowMask * uSnowSparkle * 6.0;
}
#endif
`;

export const apply: ShaderApply<typeof fields> = (material, _values, ctx) => {
  if (!(material as MeshStandardMaterial).isMeshStandardMaterial) {
    throw new Error('SnowCover needs a Principled BSDF material (MeshStandardMaterial in the game)');
  }
  const u = ctx.uniforms;
  patchShader(material, 'gs-snow-cover', (shader) => {
    shader.uniforms.uSnowAmount = u.amount;
    shader.uniforms.uSnowColor = u.color;
    shader.uniforms.uSnowScale = u.scale;
    shader.uniforms.uSnowSparkle = u.sparkle;
    shader.vertexShader = shader.vertexShader
      .replace('#include <common>', `#include <common>\n${VERTEX_PARS}`)
      .replace('#include <project_vertex>', VERTEX);
    shader.fragmentShader = shader.fragmentShader
      .replace('#include <common>', `#include <common>\n${FRAGMENT_PARS}`)
      .replace('#include <normal_fragment_begin>', '#include <normal_fragment_begin>\nvec3 snowBaseNormal = normal;')
      .replace('#include <normal_fragment_maps>', COVER)
      .replace('#include <lights_fragment_begin>', SPARKLE);
  });
};
