// After FX Lab "petrification": grey stone climbs from the feet behind a noisy, glowing seam; the
// stone is mottled, cracked (the cracks can glow), fully rough and not metallic. Builds on the
// Blender material: above the front it is untouched.

import { Box3, Vector2, Vector3, type BufferGeometry, type Material, type MeshStandardMaterial, type Object3D, type SkinnedMesh } from 'three';
import { patchShader, type ShaderApply } from '../../../schema/shaders';
import type { fields } from './meta';

// 3D simplex noise, as in FX Lab's glsl.noise: by Ian McEwan and Stefan Gustavson (Ashima Arts),
// https://github.com/stegu/webgl-noise, MIT (THIRD-PARTY-NOTICES.md).
const NOISE = /* glsl */ `
vec3 ptMod289(vec3 x) { return x - floor(x * (1.0 / 289.0)) * 289.0; }
vec4 ptMod289(vec4 x) { return x - floor(x * (1.0 / 289.0)) * 289.0; }
vec4 ptPermute(vec4 x) { return ptMod289(((x * 34.0) + 10.0) * x); }
float ptNoise(vec3 v) {
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
  i = ptMod289(i);
  vec4 p = ptPermute(ptPermute(ptPermute(i.z + vec4(0.0, i1.z, i2.z, 1.0)) + i.y + vec4(0.0, i1.y, i2.y, 1.0)) + i.x + vec4(0.0, i1.x, i2.x, 1.0));
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
}`;

const VERTEX_PARS = /* glsl */ `
varying vec3 vPtPos;`;

// Before project_vertex: `transformed` is morphed and skinned by then, in the object's own space
// (each GPU instance's own space: the instance matrix is applied after).
const VERTEX_OBJECT = /* glsl */ `
vPtPos = transformed;`;

const VERTEX_WORLD = /* glsl */ `
vec4 ptW = vec4(transformed, 1.0);
#ifdef USE_BATCHING
  ptW = batchingMatrix * ptW;
#endif
#ifdef USE_INSTANCING
  ptW = instanceMatrix * ptW;
#endif
vPtPos = (modelMatrix * ptW).xyz;`;

const FRAGMENT_PARS = /* glsl */ `
uniform float ptProgress;
uniform vec2 ptRange;
uniform vec4 ptStoneColor;
uniform vec4 ptGlowColor;
uniform float ptGlow;
uniform float ptCrackGlow;
varying vec3 vPtPos;
${NOISE}
// Height of the front: below everything at progress 0, above everything at 1.
float ptFront() {
  if (ptProgress <= 0.0) return -1e9;
  if (ptProgress >= 1.0) return 1e9;
  return mix(ptRange.x - 0.1, ptRange.y + 0.1, ptProgress);
}
// The point's height, broken up so the front is a ragged line, not a plane.
float ptEdge(vec3 p) { return p.y + ptNoise(p * 6.0) * 0.05 + ptNoise(p * 17.0) * 0.015; }`;

// After color_fragment. Everything below runs only while some stone shows (a uniform branch, so
// the derivatives in the normal step stay defined).
const COLOR = /* glsl */ `
float ptE = 0.0;
float ptF = ptFront();
float ptStone = 0.0;
float ptCrack = 0.0;
float ptHeight = 0.0;
if (ptProgress > 0.0) {
  ptE = ptEdge(vPtPos);
  ptStone = 1.0 - smoothstep(ptF - 0.025, ptF + 0.025, ptE);
  float ptMott = ptNoise(vPtPos * 3.0) * 0.5 + ptNoise(vPtPos * 11.0) * 0.25;
  ptCrack = 1.0 - smoothstep(0.0, 0.045, abs(ptNoise(vPtPos * 4.5 + 3.1)));
  ptCrack = max(ptCrack, (1.0 - smoothstep(0.0, 0.03, abs(ptNoise(vPtPos * 12.0 - 1.7)))) * 0.6);
  vec3 ptCol = ptStoneColor.rgb * (0.85 + 0.3 * ptMott);
  ptCol = mix(ptCol, ptStoneColor.rgb * 1.22, smoothstep(0.3, 0.7, ptNoise(vPtPos * 25.0)) * 0.5);
  ptCol *= 1.0 - ptCrack * 0.75;
  diffuseColor.rgb = mix(diffuseColor.rgb, ptCol, ptStone);
  ptHeight = ptMott * 0.004 - ptCrack * 0.006;
}`;

const ROUGHNESS = /* glsl */ `
roughnessFactor = mix(roughnessFactor, 0.96, ptStone);`;

const METALNESS = /* glsl */ `
metalnessFactor = mix(metalnessFactor, 0.0, ptStone);`;

// After normal_fragment_maps: the stone's mottling and cracks as a bump (surface gradient, Mikkelsen).
const NORMAL = /* glsl */ `
if (ptProgress > 0.0) {
  vec3 ptDx = dFdx(-vViewPosition);
  vec3 ptDy = dFdy(-vViewPosition);
  vec3 ptR1 = cross(ptDy, normal);
  vec3 ptR2 = cross(normal, ptDx);
  float ptDet = dot(ptDx, ptR1);
  vec3 ptGrad = sign(ptDet) * (dFdx(ptHeight) * ptR1 + dFdy(ptHeight) * ptR2);
  vec3 ptBumped = normalize(abs(ptDet) * normal - ptGrad);
  normal = normalize(mix(normal, ptBumped, ptStone));
}`;

// After emissivemap_fragment: stone does not glow; the seam and (optionally) the cracks do.
const EMISSIVE = /* glsl */ `
if (ptProgress > 0.0) {
  totalEmissiveRadiance *= 1.0 - ptStone;
  float ptBand = (ptE - ptF) / 0.035;
  totalEmissiveRadiance += ptGlowColor.rgb * ptGlow * 4.2 * exp(-ptBand * ptBand);
  totalEmissiveRadiance += ptGlowColor.rgb * ptGlow * 2.4 * ptCrack * ptStone * ptCrackGlow;
}`;

/** Bounds of a drawn geometry in its object's space, measured once (a skinned one as posed when first drawn). */
const bounds = new WeakMap<BufferGeometry, Box3>();
function localBounds(object: Object3D, geometry: BufferGeometry): Box3 {
  let box = bounds.get(geometry);
  if (box) return box;
  const skinned = (object as SkinnedMesh).isSkinnedMesh ? (object as SkinnedMesh) : null;
  const position = geometry.getAttribute('position');
  if (skinned && position) {
    box = new Box3();
    const v = new Vector3();
    for (let i = 0; i < position.count; i++) box.expandByPoint(skinned.getVertexPosition(i, v));
  } else {
    if (!geometry.boundingBox) geometry.computeBoundingBox();
    box = geometry.boundingBox!.clone();
  }
  bounds.set(geometry, box);
  return box;
}

/**
 * Object space: the front climbs from the lowest to the highest point of the meshes that draw the
 * material, measured as they are first drawn (a uniform per material, so meshes of different sizes
 * sharing one material share the range).
 */
function measureHeights(material: Material, range: Vector2): void {
  const seen = new WeakSet<BufferGeometry>();
  let empty = true;
  const previous = material.onBeforeRender;
  material.onBeforeRender = function (renderer, scene, camera, geometry, object, group) {
    previous.call(this, renderer, scene, camera, geometry, object, group);
    if (seen.has(geometry)) return;
    seen.add(geometry);
    const box = localBounds(object, geometry);
    if (box.isEmpty()) return;
    if (empty) range.set(box.min.y, box.max.y);
    else range.set(Math.min(range.x, box.min.y), Math.max(range.y, box.max.y));
    empty = false;
  };
}

export const apply: ShaderApply<typeof fields> = (material, values, ctx) => {
  if (!(material as MeshStandardMaterial).isMeshStandardMaterial) {
    throw new Error('Petrification needs a Principled BSDF material (MeshStandardMaterial in the game)');
  }
  const u = ctx.uniforms;
  const world = values.space === 'world';
  const range = new Vector2(0, 2);
  // World space: the range is the bottom and top fields, live.
  const uniforms = {
    ptProgress: u.progress,
    ptRange: world ? { get value() { return range.set(u.bottom.value, u.top.value); } } : { value: range },
    ptStoneColor: u.stoneColor,
    ptGlowColor: u.glowColor,
    ptGlow: u.glow,
    ptCrackGlow: u.crackGlow,
  };
  if (!world) measureHeights(material, range);
  patchShader(material, world ? 'gs-petrification-world' : 'gs-petrification', (shader) => {
    Object.assign(shader.uniforms, uniforms);
    shader.vertexShader = shader.vertexShader
      .replace('#include <common>', `#include <common>\n${VERTEX_PARS}`)
      .replace('#include <project_vertex>', `${world ? VERTEX_WORLD : VERTEX_OBJECT}\n#include <project_vertex>`);
    shader.fragmentShader = shader.fragmentShader
      .replace('#include <common>', `#include <common>\n${FRAGMENT_PARS}`)
      .replace('#include <color_fragment>', `#include <color_fragment>\n${COLOR}`)
      .replace('#include <roughnessmap_fragment>', `#include <roughnessmap_fragment>\n${ROUGHNESS}`)
      .replace('#include <metalnessmap_fragment>', `#include <metalnessmap_fragment>\n${METALNESS}`)
      .replace('#include <normal_fragment_maps>', `#include <normal_fragment_maps>\n${NORMAL}`)
      .replace('#include <emissivemap_fragment>', `#include <emissivemap_fragment>\n${EMISSIVE}`);
  });
};
