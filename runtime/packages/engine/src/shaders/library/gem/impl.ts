// After FX Lab "gem-dispersion" (with the inner flaws of "crystal-growth").
// A cut stone drawn over the Blender material: the surface reflects the environment with a
// diamond's Fresnel, and light refracted in with a slightly different index per colour channel
// (dispersion, the stone's "fire") bounces inside a virtual brilliant-cut pavilion, partly leaving
// through each facet and partly reflecting on, before it is looked up in the environment. The mesh's
// own facets (flat shading in Blender) shape the entry; the base colour tints the light inside.
// Environment: the scene's environment map when there is one, the scene's lights as sharp lamps,
// and a few fixed "jeweller's lamps" that make the stone sparkle anywhere.

import type { MeshPhysicalMaterial, MeshStandardMaterial } from 'three';
import { patchShader, type ShaderApply } from '../../../schema/shaders';
import type { fields } from './meta';

const VERTEX_HEAD = /* glsl */ `
varying vec3 vGsGemAxisX;
varying vec3 vGsGemAxisY;
varying vec3 vGsGemAxisZ;
varying vec3 vGsGemP;
`;

// The object's axes in view space: the stone's internal facets turn with it.
const VERTEX_BODY = /* glsl */ `
{
  mat3 gsM = mat3(modelViewMatrix);
#ifdef USE_INSTANCING
  gsM = gsM * mat3(instanceMatrix);
#endif
#ifdef USE_BATCHING
  gsM = gsM * mat3(batchingMatrix);
#endif
  vGsGemAxisX = normalize(gsM[0]);
  vGsGemAxisY = normalize(gsM[1]);
  vGsGemAxisZ = normalize(gsM[2]);
  vGsGemP = position;
}
`;

// gsGemHash is David Hoskins' hash13 ("Hash without Sine", https://www.shadertoy.com/view/4djSRW, MIT;
// THIRD-PARTY-NOTICES.md).
const FRAGMENT_HEAD = /* glsl */ `
uniform float uGemIor;
uniform float uGemDispersion;
uniform float uGemSparkle;
uniform float uGemFacets;
uniform float uGemCloudiness;
uniform bool uGemCutSurface;
varying vec3 vGsGemAxisX;
varying vec3 vGsGemAxisY;
varying vec3 vGsGemAxisZ;
varying vec3 vGsGemP;

float gsGemHash(vec3 p3) {
  p3 = fract(p3 * 0.1031);
  p3 += dot(p3, p3.zyx + 31.32);
  return fract((p3.x + p3.y) * p3.z);
}
float gsGemNoise(vec3 p) {
  vec3 i = floor(p), f = fract(p);
  f = f * f * (3.0 - 2.0 * f);
  return mix(mix(mix(gsGemHash(i), gsGemHash(i + vec3(1, 0, 0)), f.x), mix(gsGemHash(i + vec3(0, 1, 0)), gsGemHash(i + vec3(1, 1, 0)), f.x), f.y),
             mix(mix(gsGemHash(i + vec3(0, 0, 1)), gsGemHash(i + vec3(1, 0, 1)), f.x), mix(gsGemHash(i + vec3(0, 1, 1)), gsGemHash(i + vec3(1, 1, 1)), f.x), f.y), f.z);
}
float gsGemFresnel(float cosi, float eta) {
  float r0 = (1.0 - eta) / (1.0 + eta);
  r0 *= r0;
  return r0 + (1.0 - r0) * pow(1.0 - saturate(cosi), 5.0);
}
vec3 gsGemToView(vec3 d) { return vGsGemAxisX * d.x + vGsGemAxisY * d.y + vGsGemAxisZ * d.z; }
vec3 gsGemToObject(vec3 d) { return vec3(dot(d, vGsGemAxisX), dot(d, vGsGemAxisY), dot(d, vGsGemAxisZ)); }

// Facet normal of a round brilliant at a point of its (unit sphere) outline, object space, y up:
// table, star and bezel facets (crown at about 34 degrees), girdle, lower girdle halves and pavilion
// mains (about 41 degrees: steep enough for total internal reflection), each ring with its own
// count of facets, alternate rings half a facet round.
vec3 gsGemFacet(vec3 p) {
  float n = max(uGemFacets, 3.0);
  float el = asin(clamp(p.y, -1.0, 1.0));
  if (el > 1.25) return vec3(0.0, 1.0, 0.0);
  vec3 ring = el > 0.75 ? vec3(1.12, 1.0, 0.0)
    : el > 0.07 ? vec3(0.86, 2.0, 0.5)
    : el > -0.07 ? vec3(0.0, 4.0, 0.0)
    : el > -0.7 ? vec3(-0.8, 2.0, 0.5)
    : vec3(-0.86, 1.0, 0.0); // elevation of the facet normal, facets per n, shift
  float facetStep = 6.2831853 / (n * ring.y);
  float az = (floor(atan(p.z, p.x) / facetStep + 0.5 - ring.z) + ring.z) * facetStep;
  return vec3(cos(az) * cos(ring.x), sin(ring.x), sin(az) * cos(ring.x));
}

// What the stone sees in a view-space direction.
vec3 gsGemEnv(vec3 d) {
  vec3 w = inverseTransformDirection(d, viewMatrix);
  vec3 c;
#if defined( USE_ENVMAP ) && defined( ENVMAP_TYPE_CUBE_UV )
  c = textureCubeUV(envMap, envMapRotation * w, 0.0).rgb * envMapIntensity;
#else
  c = mix(vec3(0.02), ambientLightColor + vec3(0.08, 0.09, 0.11), smoothstep(-0.2, 0.6, w.y));
#endif
#if NUM_DIR_LIGHTS > 0
  for (int i = 0; i < NUM_DIR_LIGHTS; i++) {
    float s = max(dot(d, directionalLights[i].direction), 0.0);
    c += directionalLights[i].color * (pow(s, 1200.0) * 40.0 + pow(s, 90.0) * 0.4);
  }
#endif
  // Jeweller's lamps, fixed in the world (after FX Lab's gem studio): small hot sources that throw
  // flashes and fire whatever the scene's environment is.
  vec3 studio = vec3(0.0);
  const vec3 l0 = vec3(0.37, 0.74, 0.42), l1 = vec3(-0.6, 0.6, 0.5), l2 = vec3(0.1, 0.5, -0.86);
  const vec3 l3 = vec3(0.85, 0.35, -0.3), l4 = vec3(-0.3, 0.9, -0.2), l5 = vec3(-0.75, 0.2, -0.6);
  vec4 s0 = vec4(dot(w, normalize(l0)), dot(w, normalize(l1)), dot(w, normalize(l2)), dot(w, normalize(l3)));
  vec2 s1 = vec2(dot(w, normalize(l4)), dot(w, normalize(l5)));
  s0 = max(s0, 0.0);
  s1 = max(s1, 0.0);
  vec4 hot0 = pow(s0, vec4(2400.0)) * 90.0 + pow(s0, vec4(200.0)) * 1.5;
  vec2 hot1 = pow(s1, vec2(2400.0)) * 90.0 + pow(s1, vec2(200.0)) * 1.5;
  studio += vec3(hot0.x + hot0.y + hot0.z + hot0.w + hot1.x + hot1.y);
  return c + studio * uGemSparkle;
}
`;

// Before opaque_fragment: the stone's colour replaces the lit result (emission is kept).
const FRAGMENT_GEM = /* glsl */ `
{
  vec3 V = isOrthographic ? vec3(0.0, 0.0, 1.0) : normalize(vViewPosition);
  vec3 I = gsGemToObject(-V);
  vec3 nObj = normalize(gsGemToObject(normal));
  // A round or smooth mesh can take the virtual cut's facets on its surface too.
  if (uGemCutSurface) {
    nObj = gsGemFacet(nObj);
    if (dot(nObj, I) > 0.0) nObj = normalize(gsGemToObject(normal));
  }
  vec3 n = gsGemToView(nObj);
  float ndv = saturate(dot(n, V));
  float F = gsGemFresnel(ndv, uGemIor);
  vec3 gem = gsGemEnv(reflect(-V, n)) * F;

  vec3 fire = vec3(0.0);
  for (int ch = 0; ch < 3; ch++) {
    float ior = max(uGemIor + (float(ch) - 1.0) * 0.06 * uGemDispersion, 1.01);
    vec3 d = refract(I, nObj, 1.0 / ior);
    vec3 p = nObj; // entry on the virtual stone's unit outline
    float through = 1.0 - F;
    float acc = 0.0;
    for (int b = 0; b < 3; b++) {
      p = normalize(p + d * max(-2.0 * dot(p, d), 0.05));
      vec3 nf = gsGemFacet(p);
      vec3 outDir = refract(d, -nf, ior);
      if (dot(outDir, outDir) > 1e-4) {
        float R = gsGemFresnel(dot(d, nf), 1.0 / ior);
        acc += through * (1.0 - R) * gsGemEnv(gsGemToView(outDir))[ch];
        through *= R;
      }
      d = reflect(d, nf);
    }
    fire[ch] = acc + through * gsGemEnv(gsGemToView(d))[ch] * 0.5;
  }
  // Light inside the stone takes the base colour's hue; cloudy stones scatter some of the scene's light too.
  vec3 tint = diffuseColor.rgb / max(max(diffuseColor.r, diffuseColor.g), max(diffuseColor.b, 1e-3));
  float flaws = gsGemNoise((vGsGemP + I * 0.02) * 40.0) * 0.6 + gsGemNoise(vGsGemP * 110.0) * 0.4;
  float cloud = saturate(uGemCloudiness);
  gem += fire * tint * (1.0 - cloud * 0.7);
  gem += totalDiffuse * cloud * (0.5 + flaws);
  outgoingLight = gem + totalEmissiveRadiance;
}
`;

export const apply: ShaderApply<typeof fields> = (material, _values, ctx) => {
  if (!(material as MeshStandardMaterial).isMeshStandardMaterial) {
    throw new Error(`Gem needs a lit (Principled BSDF) material, got ${material.type}`);
  }
  // The stone draws its own transmission: no screen-space refraction pass.
  if ((material as MeshPhysicalMaterial).isMeshPhysicalMaterial) (material as MeshPhysicalMaterial).transmission = 0;
  const u = ctx.uniforms;
  patchShader(material, 'gs_gem', (shader) => {
    Object.assign(shader.uniforms, {
      uGemIor: u.ior,
      uGemDispersion: u.dispersion,
      uGemSparkle: u.sparkle,
      uGemFacets: u.facets,
      uGemCloudiness: u.cloudiness,
      uGemCutSurface: u.cutSurface,
    });
    shader.vertexShader = shader.vertexShader
      .replace('#include <common>', `#include <common>\n${VERTEX_HEAD}`)
      .replace('#include <project_vertex>', `#include <project_vertex>\n${VERTEX_BODY}`);
    shader.fragmentShader = shader.fragmentShader
      .replace('#include <lights_pars_begin>', `#include <lights_pars_begin>\n${FRAGMENT_HEAD}`)
      .replace('#include <opaque_fragment>', `${FRAGMENT_GEM}\n#include <opaque_fragment>`);
  });
};
