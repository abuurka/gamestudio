// After FX Lab "oil-slick" and "soap-bubbles".
// Thin-film interference on the Blender material: a swirling film (oil, soap) colours the
// specular reflection with the Airy reflectance of an air / film / air stack, nine wavelengths
// folded into RGB. Base colour, roughness and maps stay; only the specular reflectance changes.

import type { Material, MeshStandardMaterial } from 'three';
import { patchShader, type ShaderApply } from '../../../schema/shaders';
import type { fields } from './meta';

const VERTEX_HEAD = /* glsl */ `
varying vec3 vGsFilmP;
varying vec3 vGsFilmN;
`;

const VERTEX_BODY = /* glsl */ `
vGsFilmP = position;
vGsFilmN = normal;
#ifdef USE_INSTANCING
  vGsFilmP += instanceMatrix[3].xyz * 0.37; // each instance its own swirl
#endif
`;

// gsFilmPermute and gsFilmNoise are the 3D simplex noise by Ian McEwan and Stefan Gustavson (Ashima Arts),
// https://github.com/stegu/webgl-noise, MIT (THIRD-PARTY-NOTICES.md).
const FRAGMENT_HEAD = /* glsl */ `
uniform float uGsTime;
uniform float uFilmStrength;
uniform vec2 uFilmThickness;
uniform float uFilmScale;
uniform float uFilmSpeed;
uniform float uFilmDrain;
uniform float uFilmCoverage;
uniform float uFilmGloss;
varying vec3 vGsFilmP;
varying vec3 vGsFilmN;
vec3 gsFilmR = vec3(0.0);
float gsFilmMask = 0.0;

vec4 gsFilmPermute(vec4 x) { return mod(((x * 34.0) + 10.0) * x, 289.0); }
// 3D simplex noise (Ashima Arts / Stefan Gustavson, MIT).
float gsFilmNoise(vec3 v) {
  const vec2 C = vec2(1.0 / 6.0, 1.0 / 3.0);
  vec3 i = floor(v + dot(v, C.yyy));
  vec3 x0 = v - i + dot(i, C.xxx);
  vec3 g = step(x0.yzx, x0.xyz);
  vec3 l = 1.0 - g;
  vec3 i1 = min(g.xyz, l.zxy);
  vec3 i2 = max(g.xyz, l.zxy);
  vec3 x1 = x0 - i1 + C.xxx;
  vec3 x2 = x0 - i2 + C.yyy;
  vec3 x3 = x0 - 0.5;
  i = mod(i, 289.0);
  vec4 p = gsFilmPermute(gsFilmPermute(gsFilmPermute(i.z + vec4(0.0, i1.z, i2.z, 1.0)) + i.y + vec4(0.0, i1.y, i2.y, 1.0)) + i.x + vec4(0.0, i1.x, i2.x, 1.0));
  vec4 j = p - 49.0 * floor(p / 49.0);
  vec4 x_ = floor(j / 7.0);
  vec4 y_ = floor(j - 7.0 * x_);
  vec4 x = (x_ * 2.0 + 0.5) / 7.0 - 1.0;
  vec4 y = (y_ * 2.0 + 0.5) / 7.0 - 1.0;
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
  vec4 norm = inversesqrt(vec4(dot(p0, p0), dot(p1, p1), dot(p2, p2), dot(p3, p3)));
  p0 *= norm.x; p1 *= norm.y; p2 *= norm.z; p3 *= norm.w;
  vec4 m = max(0.5 - vec4(dot(x0, x0), dot(x1, x1), dot(x2, x2), dot(x3, x3)), 0.0);
  m = m * m;
  return 105.0 * dot(m * m, vec4(dot(p0, x0), dot(p1, x1), dot(p2, x2), dot(p3, x3)));
}

// Airy reflectance of a thin film (air / film / air), s and p averaged, nine wavelengths.
vec3 gsThinFilm(float cos1, float dnm) {
  const float n2 = 1.4;
  float sin1 = sqrt(max(1.0 - cos1 * cos1, 0.0));
  float sin2 = sin1 / n2;
  float cos2 = sqrt(max(1.0 - sin2 * sin2, 0.0));
  float r12s = (cos1 - n2 * cos2) / (cos1 + n2 * cos2);
  float r12p = (n2 * cos1 - cos2) / (n2 * cos1 + cos2);
  float r23s = -r12s, r23p = -r12p;
  vec3 sum = vec3(0.0);
  for (int k = 0; k < 3; k++) {
    vec3 lambda = vec3(650.0, 540.0, 455.0) + (float(k) - 1.0) * vec3(30.0, 22.0, 18.0);
    vec3 cp = cos(12.566371 * n2 * dnm * cos2 / lambda);
    vec3 Rs = (r12s * r12s + r23s * r23s + 2.0 * r12s * r23s * cp) / (1.0 + r12s * r12s * r23s * r23s + 2.0 * r12s * r23s * cp);
    vec3 Rp = (r12p * r12p + r23p * r23p + 2.0 * r12p * r23p * cp) / (1.0 + r12p * r12p * r23p * r23p + 2.0 * r12p * r23p * cp);
    sum += 0.5 * (Rs + Rp);
  }
  return sum / 3.0;
}
`;

// After lights_physical_fragment: the film replaces the specular reflectance (F0 and F90).
const FRAGMENT_FILM = /* glsl */ `
{
  vec3 gsV = isOrthographic ? vec3(0.0, 0.0, 1.0) : normalize(vViewPosition);
  float gsCos = saturate(dot(normal, gsV));
  vec3 q = vGsFilmP * uFilmScale;
  float t = uGsTime * uFilmSpeed;
  // Swirls: a warped noise field drifting over the surface.
  vec2 warp = vec2(gsFilmNoise(q * 0.7 + vec3(0.0, t, 0.0)), gsFilmNoise(q * 0.7 + vec3(5.2, -t, 1.3)));
  float swirl = gsFilmNoise(q + vec3(warp * 1.4, t * 1.3)) * 0.5 + 0.5;
  float thickness = mix(uFilmThickness.x, uFilmThickness.y, swirl);
  // Drainage (soap): thick at the bottom, thin at the top.
  float up = normalize(vGsFilmN).y + 0.25 * (swirl * 2.0 - 1.0);
  thickness *= mix(1.0, mix(1.25, 0.2, smoothstep(-1.0, 1.0, up)), uFilmDrain);
  // Patchy slicks below full coverage, thinning to nothing at their edges.
  float patches = gsFilmNoise(q * 0.3 + 11.3) * 0.5 + 0.5;
  float cover = saturate((patches + uFilmCoverage * 1.2 - 1.0) / 0.12);
  thickness *= cover;
  gsFilmMask = smoothstep(20.0, 140.0, thickness);
  gsFilmR = min(gsThinFilm(gsCos, thickness) * uFilmStrength, vec3(1.0));
  material.specularColor = mix(material.specularColor, gsFilmR, gsFilmMask);
  material.specularF90 = mix(material.specularF90, max(max(gsFilmR.r, gsFilmR.g), gsFilmR.b), gsFilmMask);
  material.specularColorBlended = mix(material.specularColor, diffuseColor.rgb, metalnessFactor);
  // The film is a smooth layer: its reflection is sharper than the surface under it.
  material.roughness = mix(material.roughness, min(max(0.0525, geometryRoughness), 1.0), saturate(uFilmGloss) * gsFilmMask);
}
`;

// Transparent materials (bubbles): the film's reflection is not faded by the alpha, and it adds opacity.
const FRAGMENT_ALPHA = /* glsl */ `
{
  float gsAlpha = max(diffuseColor.a, saturate(dot(gsFilmR, vec3(0.2126, 0.7152, 0.0722)) * gsFilmMask * 1.5));
  outgoingLight += totalSpecular * (1.0 / max(gsAlpha, 0.05) - 1.0);
  diffuseColor.a = gsAlpha;
}
`;

export const apply: ShaderApply<typeof fields> = (material, _values, ctx) => {
  if (!(material as MeshStandardMaterial).isMeshStandardMaterial) {
    throw new Error(`Iridescence needs a lit (Principled BSDF) material, got ${material.type}`);
  }
  const transparent = (material as Material).transparent;
  const u = ctx.uniforms;
  patchShader(material, transparent ? 'gs_iridescence_t' : 'gs_iridescence', (shader) => {
    Object.assign(shader.uniforms, {
      uGsTime: ctx.time,
      uFilmStrength: u.strength,
      uFilmThickness: u.thickness,
      uFilmScale: u.scale,
      uFilmSpeed: u.speed,
      uFilmDrain: u.drain,
      uFilmCoverage: u.coverage,
      uFilmGloss: u.gloss,
    });
    shader.vertexShader = shader.vertexShader
      .replace('#include <common>', `#include <common>\n${VERTEX_HEAD}`)
      .replace('#include <project_vertex>', `#include <project_vertex>\n${VERTEX_BODY}`);
    shader.fragmentShader = shader.fragmentShader
      .replace('#include <common>', `#include <common>\n${FRAGMENT_HEAD}`)
      .replace('#include <lights_physical_fragment>', `#include <lights_physical_fragment>\n${FRAGMENT_FILM}`);
    if (transparent) {
      shader.fragmentShader = shader.fragmentShader.replace('#include <opaque_fragment>', `${FRAGMENT_ALPHA}\n#include <opaque_fragment>`);
    }
  });
};
