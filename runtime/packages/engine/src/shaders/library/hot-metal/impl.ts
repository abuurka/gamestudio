// After FX Lab "blacksmith-quench" (blackbody glow over forge-scaled steel) and "metal-pour" (a crust
// that skins over as the metal cools, glowing through its cracks).
import type { MeshStandardMaterial } from 'three';
import { patchShader, type ShaderApply } from '../../../schema/shaders';
import type { fields } from './meta';

type F = typeof fields;

const VERTEX_DECLARATIONS = /* glsl */ `
varying vec3 vGsHotPos;
`;

// The rest pose in object space, so the pattern sticks to the object; GPU instances each get their own.
const VERTEX = /* glsl */ `
vGsHotPos = position;
#ifdef USE_INSTANCING
vGsHotPos += instanceMatrix[3].xyz * 0.613;
#endif
`;

// gsHotMod289, gsHotPermute and gsHotSnoise are the 3D simplex noise by Ian McEwan and Stefan Gustavson
// (Ashima Arts), https://github.com/stegu/webgl-noise, MIT (THIRD-PARTY-NOTICES.md).
const FRAGMENT_DECLARATIONS = /* glsl */ `
uniform float gsHotTime;
uniform float gsHotHeat;
uniform float gsHotGlow;
uniform float gsHotCrust;
uniform float gsHotScale;
uniform float gsHotCoolAt;
uniform float gsHotCoolTime;
varying vec3 vGsHotPos;
vec3 gsHotMod289(vec3 x) { return x - floor(x * (1.0 / 289.0)) * 289.0; }
vec4 gsHotMod289(vec4 x) { return x - floor(x * (1.0 / 289.0)) * 289.0; }
vec4 gsHotPermute(vec4 x) { return gsHotMod289(((x * 34.0) + 10.0) * x); }
float gsHotSnoise(vec3 v) {
  const vec2 C = vec2(1.0 / 6.0, 1.0 / 3.0);
  const vec4 D = vec4(0.0, 0.5, 1.0, 2.0);
  vec3 i = floor(v + dot(v, C.yyy));
  vec3 x0 = v - i + dot(i, C.xxx);
  vec3 g = step(x0.yzx, x0.xyz);
  vec3 l = 1.0 - g;
  vec3 i1 = min(g.xyz, l.zxy);
  vec3 i2 = max(g.xyz, l.zxy);
  vec3 x1 = x0 - i1 + C.xxx;
  vec3 x2 = x0 - i2 + C.yyy;
  vec3 x3 = x0 - D.yyy;
  i = gsHotMod289(i);
  vec4 p = gsHotPermute(gsHotPermute(gsHotPermute(i.z + vec4(0.0, i1.z, i2.z, 1.0)) + i.y + vec4(0.0, i1.y, i2.y, 1.0)) + i.x + vec4(0.0, i1.x, i2.x, 1.0));
  vec3 ns = 0.142857142857 * D.wyz - D.xzx;
  vec4 j = p - 49.0 * floor(p * ns.z * ns.z);
  vec4 x_ = floor(j * ns.z);
  vec4 y_ = floor(j - 7.0 * x_);
  vec4 x = x_ * ns.x + ns.yyyy;
  vec4 y = y_ * ns.x + ns.yyyy;
  vec4 h = 1.0 - abs(x) - abs(y);
  vec4 b0 = vec4(x.xy, y.xy);
  vec4 b1 = vec4(x.zw, y.zw);
  vec4 s0 = floor(b0) * 2.0 + 1.0;
  vec4 s1 = floor(b1) * 2.0 + 1.0;
  vec4 sh = -step(h, vec4(0.0));
  vec4 a0 = b0.xzyw + s0.xzyw * sh.xxyy;
  vec4 a1 = b1.xzyw + s1.xzyw * sh.zzww;
  vec3 p0 = vec3(a0.xy, h.x);
  vec3 p1 = vec3(a0.zw, h.y);
  vec3 p2 = vec3(a1.xy, h.z);
  vec3 p3 = vec3(a1.zw, h.w);
  vec4 norm = 1.79284291400159 - 0.85373472095314 * vec4(dot(p0, p0), dot(p1, p1), dot(p2, p2), dot(p3, p3));
  p0 *= norm.x; p1 *= norm.y; p2 *= norm.z; p3 *= norm.w;
  vec4 m = max(0.5 - vec4(dot(x0, x0), dot(x1, x1), dot(x2, x2), dot(x3, x3)), 0.0);
  m = m * m;
  return 105.0 * dot(m * m, vec4(dot(p0, x0), dot(p1, x1), dot(p2, x2), dot(p3, x3)));
}
/**
 * Temperature 0..1 to HDR blackbody light (linear): dull red, cherry, orange, yellow, white-hot. The FX
 * ramp's hues, dimmer below white-hot: Neutral tone mapping turns anything much above 1 towards white,
 * so the reds and oranges stay near 1 (they still bloom) and only the top end is far brighter.
 */
vec3 gsHotBlackbody(float t) {
  t = clamp(t, 0.0, 1.0);
  vec3 c = mix(vec3(0.0), vec3(0.3, 0.012, 0.0), smoothstep(0.0, 0.2, t));
  c = mix(c, vec3(1.0, 0.1, 0.015) * 0.9, smoothstep(0.15, 0.42, t));
  c = mix(c, vec3(1.0, 0.33, 0.05) * 1.6, smoothstep(0.4, 0.68, t));
  c = mix(c, vec3(1.0, 0.62, 0.25) * 3.2, smoothstep(0.68, 0.9, t));
  c = mix(c, vec3(1.0, 0.94, 0.84) * 9.0, smoothstep(0.9, 1.0, t));
  return c;
}
/** Heat now: 'heat', cooling off over 'coolTime' seconds after 'coolAt'. */
float gsHotLevel() {
  float h = clamp(gsHotHeat, 0.0, 1.0);
  if (gsHotCoolAt >= 0.0 && gsHotTime >= gsHotCoolAt) h *= exp(-4.0 * (gsHotTime - gsHotCoolAt) / max(gsHotCoolTime, 1e-3));
  return h;
}
`;

const COLOR = /* glsl */ `
float gsHotH = gsHotLevel();
vec3 gsHotLight = vec3(0.0);
float gsHotRough = 0.0;
if (gsHotH > 0.002) {
  vec3 gsP = vGsHotPos * gsHotScale;
  float gsN = gsHotSnoise(gsP) * 0.5 + 0.5;
  // Forge scale: dark flecks that run a little cooler.
  float gsFlake = smoothstep(0.62, 0.82, gsHotSnoise(gsP * 1.4 + 3.1) * 0.5 + 0.5);
  float gsT = gsHotH * (0.86 + 0.24 * gsN);
  gsT *= 1.0 - 0.15 * gsFlake * smoothstep(0.3, 0.7, gsT);
  // Crust: plates skin over as it cools, knitting together until only glowing hairlines remain.
  float gsCool = 1.0 - gsHotH;
  float gsCrust = clamp(gsHotCrust * (gsCool * 1.35 - 0.1 + (gsN - 0.5) * 0.45), 0.0, 1.0);
  float gsPlate = gsCrust * (0.75 + 0.5 * (gsHotSnoise(gsP * 0.7 + 11.0) * 0.5 + 0.5));
  float gsCrack = abs(gsHotSnoise(gsP * 1.6 + 5.7)) * 0.6 + abs(gsHotSnoise(gsP * 3.9 - 2.3)) * 0.25;
  float gsGap = mix(0.75, 0.035, gsPlate);
  float gsOpen = max(1.0 - smoothstep(gsGap * 0.35, gsGap, gsCrack), 1.0 - smoothstep(0.03, 0.2, gsCrust));
  float gsRim = exp(-gsCrack / max(gsGap * 2.2, 0.03));
  vec3 gsCrustGlow = gsHotBlackbody(gsT * (0.62 - gsPlate * 0.25)) * gsRim * (1.0 - gsPlate * 0.7);
  gsHotLight = mix(gsCrustGlow, gsHotBlackbody(gsT), gsOpen) * gsHotGlow;
  // Hot steel shows its glow, not its paint: the surface goes dark as it heats, the crust darker still.
  float gsHot = smoothstep(0.0, 0.12, gsHotH);
  vec3 gsDark = mix(vec3(0.05, 0.045, 0.04) * (0.6 + 0.8 * gsN), vec3(0.03, 0.02, 0.02), gsOpen);
  diffuseColor.rgb = mix(diffuseColor.rgb, mix(diffuseColor.rgb * 0.35, gsDark, smoothstep(0.15, 0.4, gsT)), gsHot);
  gsHotRough = gsHot * mix(0.9, 0.35, gsOpen);
}
`;

const ROUGHNESS = /* glsl */ `
roughnessFactor = mix(roughnessFactor, gsHotRough, step(0.001, gsHotRough));
`;

const EMISSIVE = /* glsl */ `
totalEmissiveRadiance += gsHotLight;
`;

export const apply: ShaderApply<F> = (material, _values, ctx) => {
  if (!(material as MeshStandardMaterial).isMeshStandardMaterial) {
    throw new Error('HotMetal needs a Principled BSDF material (MeshStandardMaterial in the game)');
  }
  const u = ctx.uniforms;
  const uniforms = {
    gsHotTime: ctx.time,
    gsHotHeat: u.heat,
    gsHotGlow: u.glow,
    gsHotCrust: u.crust,
    gsHotScale: u.noiseScale,
    gsHotCoolAt: u.coolAt,
    gsHotCoolTime: u.coolTime,
  };
  patchShader(material, 'gs-hot-metal', (shader) => {
    Object.assign(shader.uniforms, uniforms);
    shader.vertexShader = shader.vertexShader
      .replace('#include <common>', `#include <common>\n${VERTEX_DECLARATIONS}`)
      .replace('#include <begin_vertex>', `#include <begin_vertex>\n${VERTEX}`);
    shader.fragmentShader = shader.fragmentShader
      .replace('#include <common>', `#include <common>\n${FRAGMENT_DECLARATIONS}`)
      .replace('#include <color_fragment>', `#include <color_fragment>\n${COLOR}`)
      .replace('#include <roughnessmap_fragment>', `#include <roughnessmap_fragment>\n${ROUGHNESS}`)
      .replace('#include <emissivemap_fragment>', `#include <emissivemap_fragment>\n${EMISSIVE}`);
  });
};
