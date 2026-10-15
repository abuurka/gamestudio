// After the Sahan game's world-space scans (charted1 src/lib/triplanar.ts): three planar projections
// blended by the world normal, the scan as detail over the material's colour, a whiteout-blended
// normal scan, a remapped roughness scan, and wetness that fades out above a water line.

import { Vector3, type MeshStandardMaterial } from 'three';
import { patchShader, type ShaderApply } from '../../../schema/shaders';
import type { fields } from './meta';

const VERTEX_PARS = /* glsl */ `
varying vec3 vTpPos;
varying vec3 vTpNrm;`;

// After worldpos_vertex: `transformed` and `objectNormal` are skinned and morphed by then.
const VERTEX = /* glsl */ `
vec4 tpWp = vec4(transformed, 1.0);
vec3 tpNo = objectNormal;
#ifdef USE_BATCHING
  tpWp = batchingMatrix * tpWp;
  tpNo = mat3(batchingMatrix) * tpNo;
#endif
#ifdef USE_INSTANCING
  tpWp = instanceMatrix * tpWp;
  tpNo = mat3(instanceMatrix) * tpNo;
#endif
vTpPos = (modelMatrix * tpWp).xyz;
vTpNrm = normalize(mat3(modelMatrix) * tpNo);`;

const FRAGMENT_PARS = /* glsl */ `
uniform sampler2D tpDiff;
uniform sampler2D tpNor;
uniform sampler2D tpRgh;
uniform vec3 tpHas;
uniform float tpTile;
uniform float tpNormal;
uniform float tpRoughMin;
uniform float tpRoughMax;
uniform float tpPhoto;
uniform float tpVariation;
uniform float tpWet;
uniform float tpWaterLevel;
uniform float tpWetBand;
varying vec3 vTpPos;
varying vec3 vTpNrm;
vec3 tpWeights(vec3 n) { vec3 w = pow(abs(n), vec3(4.0)); return w / (w.x + w.y + w.z); }
vec4 tpSample(sampler2D t, vec3 p, vec3 w) {
  return texture2D(t, p.zy) * w.x + texture2D(t, p.xz) * w.y + texture2D(t, p.xy) * w.z;
}
float tpLum(vec3 c) { return dot(c, vec3(0.2126, 0.7152, 0.0722)); }
float tpWetness() { return tpWet * (1.0 - smoothstep(tpWaterLevel, tpWaterLevel + tpWetBand, vTpPos.y)); }`;

// After map_fragment: the scan over the material's colour (its brightness from the scan's average,
// read from the smallest mip), large patches against tiling, then darkening where wet.
const MAP = /* glsl */ `
if (tpHas.x > 0.5) {
  vec3 tpW = tpWeights(normalize(vTpNrm));
  vec3 tpP = vTpPos / tpTile;
  vec3 tpTex = tpSample(tpDiff, tpP, tpW).rgb;
  vec3 tpMacro = tpSample(tpDiff, tpP * 0.173 + 0.37, tpW).rgb;
  float tpAvg = max(tpLum(textureLod(tpDiff, vec2(0.5), 16.0).rgb), 1e-3);
  vec3 tpBase = diffuseColor.rgb;
  vec3 tpDetail = tpBase * clamp(tpLum(tpTex) / tpAvg, 0.25, 2.5);
  vec3 tpPhotoCol = tpTex * (tpLum(tpBase) / tpAvg);
  vec3 tpCol = mix(tpDetail, tpPhotoCol, tpPhoto);
  tpCol *= mix(1.0, clamp(tpLum(tpMacro) / tpAvg, 0.4, 1.8), tpVariation);
  diffuseColor.rgb = tpCol;
}
diffuseColor.rgb *= 1.0 - 0.45 * tpWetness();`;

const ROUGH = /* glsl */ `
if (tpHas.z > 0.5) {
  float tpR = tpSample(tpRgh, vTpPos / tpTile, tpWeights(normalize(vTpNrm))).r;
  roughnessFactor = mix(tpRoughMin, tpRoughMax, tpR);
}
roughnessFactor = mix(roughnessFactor, 0.25, tpWetness());`;

// After normal_fragment_maps: three tangent-space samples whiteout-blended into the world normal (Golus).
const NORMAL = /* glsl */ `
if (tpHas.y > 0.5) {
  vec3 tpN = normalize(vTpNrm);
  #ifdef DOUBLE_SIDED
    tpN *= faceDirection;
  #endif
  vec3 tpW = tpWeights(tpN);
  vec3 tpP = vTpPos / tpTile;
  vec3 tpX = texture2D(tpNor, tpP.zy).xyz * 2.0 - 1.0;
  vec3 tpY = texture2D(tpNor, tpP.xz).xyz * 2.0 - 1.0;
  vec3 tpZ = texture2D(tpNor, tpP.xy).xyz * 2.0 - 1.0;
  tpX.xy *= tpNormal;
  tpY.xy *= tpNormal;
  tpZ.xy *= tpNormal;
  tpX = vec3(tpX.xy + tpN.zy, abs(tpX.z) * tpN.x);
  tpY = vec3(tpY.xy + tpN.xz, abs(tpY.z) * tpN.y);
  tpZ = vec3(tpZ.xy + tpN.xy, abs(tpZ.z) * tpN.z);
  vec3 tpWorld = normalize(tpX.zyx * tpW.x + tpY.xzy * tpW.y + tpZ.xyz * tpW.z);
  normal = normalize((viewMatrix * vec4(tpWorld, 0.0)).xyz);
}`;

export const apply: ShaderApply<typeof fields> = (material, _values, ctx) => {
  if (!(material as MeshStandardMaterial).isMeshStandardMaterial) {
    throw new Error('Triplanar needs a Principled BSDF material (MeshStandardMaterial in the game)');
  }
  const u = ctx.uniforms;
  const has = new Vector3();
  // Getters: a scan that loads later (or is set from code) is used from the next frame on.
  const uniforms = {
    tpDiff: { get value() { return u.diffuse.value ?? ctx.white; } },
    tpNor: { get value() { return u.normal.value ?? ctx.white; } },
    tpRgh: { get value() { return u.rough.value ?? ctx.white; } },
    tpHas: { get value() { return has.set(u.diffuse.value ? 1 : 0, u.normal.value ? 1 : 0, u.rough.value ? 1 : 0); } },
    tpTile: u.tile,
    tpNormal: u.normalStrength,
    tpRoughMin: u.roughMin,
    tpRoughMax: u.roughMax,
    tpPhoto: u.photo,
    tpVariation: u.variation,
    tpWet: u.wetness,
    tpWaterLevel: u.waterLevel,
    tpWetBand: u.wetBand,
  };
  patchShader(material, 'gs-triplanar', (shader) => {
    Object.assign(shader.uniforms, uniforms);
    shader.vertexShader = shader.vertexShader
      .replace('#include <common>', `#include <common>\n${VERTEX_PARS}`)
      .replace('#include <worldpos_vertex>', `#include <worldpos_vertex>\n${VERTEX}`);
    shader.fragmentShader = shader.fragmentShader
      .replace('#include <common>', `#include <common>\n${FRAGMENT_PARS}`)
      .replace('#include <map_fragment>', `#include <map_fragment>\n${MAP}`)
      .replace('#include <roughnessmap_fragment>', `#include <roughnessmap_fragment>\n${ROUGH}`)
      .replace('#include <normal_fragment_maps>', `#include <normal_fragment_maps>\n${NORMAL}`);
  });
};
