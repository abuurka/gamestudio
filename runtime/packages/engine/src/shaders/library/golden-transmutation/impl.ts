// After FX Lab "golden-transmutation": a molten seam climbs the object and leaves polished gold
// behind (metallic, glittering micro facets, still glowing hot just behind the seam); the material
// just ahead of the seam scorches. Builds on the Blender material: above the front it is untouched.

import { Box3, Vector2, Vector3, type BufferGeometry, type Material, type MeshStandardMaterial, type Object3D, type SkinnedMesh } from 'three';
import { patchShader, type ShaderApply } from '../../../schema/shaders';
import type { fields } from './meta';

// Hashes and 3D simplex noise, as in FX Lab's glsl.noise: gtHash13 and gtHash33 are David Hoskins' "Hash
// without Sine" (https://www.shadertoy.com/view/4djSRW, MIT), the noise is by Ian McEwan and Stefan
// Gustavson (Ashima Arts), https://github.com/stegu/webgl-noise, MIT. Both notices: THIRD-PARTY-NOTICES.md.
const NOISE = /* glsl */ `
float gtHash13(vec3 p3) { p3 = fract(p3 * 0.1031); p3 += dot(p3, p3.zyx + 31.32); return fract((p3.x + p3.y) * p3.z); }
vec3 gtHash33(vec3 p3) { p3 = fract(p3 * vec3(0.1031, 0.1030, 0.0973)); p3 += dot(p3, p3.yxz + 33.33); return fract((p3.xxy + p3.yxx) * p3.zyx); }
vec3 gtMod289(vec3 x) { return x - floor(x * (1.0 / 289.0)) * 289.0; }
vec4 gtMod289(vec4 x) { return x - floor(x * (1.0 / 289.0)) * 289.0; }
vec4 gtPermute(vec4 x) { return gtMod289(((x * 34.0) + 10.0) * x); }
float gtNoise(vec3 v) {
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
  i = gtMod289(i);
  vec4 p = gtPermute(gtPermute(gtPermute(i.z + vec4(0.0, i1.z, i2.z, 1.0)) + i.y + vec4(0.0, i1.y, i2.y, 1.0)) + i.x + vec4(0.0, i1.x, i2.x, 1.0));
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
varying vec3 vGtPos;
varying vec3 vGtLocal;`;

// Before project_vertex: `transformed` is morphed and skinned by then, in the object's own space
// (each GPU instance's own space: the instance matrix is applied after). The glitter cells always
// stick to the object; the front uses world heights in world space.
const VERTEX = (world: boolean) => /* glsl */ `
vGtLocal = transformed;
${
  world
    ? `vec4 gtW = vec4(transformed, 1.0);
#ifdef USE_BATCHING
  gtW = batchingMatrix * gtW;
#endif
#ifdef USE_INSTANCING
  gtW = instanceMatrix * gtW;
#endif
vGtPos = (modelMatrix * gtW).xyz;`
    : 'vGtPos = transformed;'
}`;

const FRAGMENT_PARS = /* glsl */ `
uniform float gtProgress;
uniform vec2 gtRange;
uniform vec4 gtGoldColor;
uniform float gtRoughness;
uniform float gtGlitter;
uniform vec4 gtMoltenColor;
uniform float gtHeat;
uniform float gtTime;
varying vec3 vGtPos;
varying vec3 vGtLocal;
${NOISE}
float gtFront() {
  if (gtProgress <= 0.0) return -1e9;
  if (gtProgress >= 1.0) return 1e9;
  return mix(gtRange.x - 0.12, gtRange.y + 0.12, gtProgress);
}
// A smooth warp of the front (FX Lab's frontWarp, flattened for a rising front) plus fine noise.
float gtEdge(vec3 p) {
  float warp = 0.16 * sin(1.7 * p.x + 2.1 * sin(1.3 * p.z)) * sin(1.9 * p.y + 1.7 * sin(1.1 * p.x))
    + 0.1 * sin(3.1 * p.z + 2.0 * sin(2.3 * p.y)) * sin(2.7 * p.x);
  return p.y + warp * 0.45 + gtNoise(p * 7.0) * 0.035;
}`;

// After color_fragment: gold behind the front (hot orange just behind the seam), scorched ahead of
// it. Nothing to do at progress 0 (a uniform branch).
const COLOR = /* glsl */ `
float gtAhead = -1e9;
float gtGold = 0.0;
float gtHot = 0.0;
if (gtProgress > 0.0) {
  gtAhead = gtProgress >= 1.0 ? 1e9 : gtFront() - gtEdge(vGtPos);
  gtGold = smoothstep(0.0, 0.025, gtAhead);
  gtHot = exp(-max(gtAhead, 0.0) * 9.0) * step(0.0, gtAhead);
  diffuseColor.rgb *= 1.0 - 0.45 * smoothstep(-0.18, 0.0, gtAhead) * step(gtAhead, 0.0);
  vec3 gtAlbedo = mix(gtGoldColor.rgb, gtGoldColor.rgb * vec3(1.0, 0.6, 0.35), gtHot * 0.6);
  diffuseColor.rgb = mix(diffuseColor.rgb, gtAlbedo, gtGold);
}`;

const ROUGHNESS = /* glsl */ `
roughnessFactor = mix(roughnessFactor, gtRoughness, gtGold);`;

const METALNESS = /* glsl */ `
metalnessFactor = mix(metalnessFactor, 1.0, gtGold);`;

// After emissivemap_fragment: no glow from the gold itself, except heat, glitter and the seam.
const EMISSIVE = /* glsl */ `
if (gtProgress > 0.0) {
  totalEmissiveRadiance *= 1.0 - gtGold;
  vec3 gtV = normalize(vViewPosition);
  #ifndef USE_ENVMAP
    // No environment map to reflect: a sky-and-ground gradient, so the gold does not go black.
    vec3 gtR = inverseTransformDirection(reflect(-gtV, normal), viewMatrix);
    float gtFr = pow(1.0 - clamp(dot(normal, gtV), 0.0, 1.0), 5.0);
    vec3 gtEnv = mix(vec3(0.05, 0.045, 0.04), vec3(0.75, 0.72, 0.66), smoothstep(-0.25, 0.7, gtR.y));
    totalEmissiveRadiance += (gtGoldColor.rgb + (1.0 - gtGoldColor.rgb) * gtFr) * gtEnv * 0.45 * gtGold;
  #endif
  // Glitter: a speck in each cell of a fine grid on the object, a tiny facet that flashes when it
  // catches the sun; a few specks twinkle on their own.
  vec3 gtG = vGtLocal * 70.0;
  vec3 gtCell = floor(gtG);
  vec3 gtJitter = gtHash33(gtCell) - 0.5;
  vec3 gtD = fract(gtG) - 0.5 - gtJitter * 0.5;
  float gtSpeck = exp(-dot(gtD, gtD) * 40.0) * gtGlitter * gtGold;
  float gtTwinkle = step(0.94, gtHash13(gtCell + 3.1)) * (0.7 + 0.3 * sin(gtTime * 5.0 + gtHash13(gtCell) * 60.0));
  totalEmissiveRadiance += gtGoldColor.rgb * gtTwinkle * gtSpeck * 1.2;
  #if NUM_DIR_LIGHTS > 0
    float gtFacet = pow(max(dot(reflect(-gtV, normalize(normal + gtJitter * 0.5)), directionalLights[0].direction), 0.0), 600.0);
    totalEmissiveRadiance += directionalLights[0].color * gtGoldColor.rgb * gtFacet * 6.0 * gtSpeck;
  #endif
  // Still hot just behind the seam; the seam itself a bright flickering band.
  totalEmissiveRadiance += gtMoltenColor.rgb * gtHot * gtGold * 1.8 * gtHeat;
  float gtSeam = smoothstep(0.05, 0.0, abs(gtAhead - 0.015));
  if (gtSeam > 0.0) {
    float gtFlick = 0.75 + 0.25 * gtNoise(vec3(vGtLocal.xz * 3.0 + vGtLocal.y * 2.0, gtTime * 0.6));
    totalEmissiveRadiance += gtMoltenColor.rgb * gtSeam * gtFlick * 9.0 * gtHeat;
  }
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

/** Object space: the front climbs from the lowest to the highest point of the meshes that draw the material. */
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
    throw new Error('GoldenTransmutation needs a Principled BSDF material (MeshStandardMaterial in the game)');
  }
  const u = ctx.uniforms;
  const world = values.space === 'world';
  const range = new Vector2(0, 2);
  const uniforms = {
    gtProgress: u.progress,
    gtRange: world ? { get value() { return range.set(u.bottom.value, u.top.value); } } : { value: range },
    gtGoldColor: u.goldColor,
    gtRoughness: u.roughness,
    gtGlitter: u.glitter,
    gtMoltenColor: u.moltenColor,
    gtHeat: u.heat,
    gtTime: ctx.time,
  };
  if (!world) measureHeights(material, range);
  patchShader(material, world ? 'gs-golden-transmutation-world' : 'gs-golden-transmutation', (shader) => {
    Object.assign(shader.uniforms, uniforms);
    shader.vertexShader = shader.vertexShader
      .replace('#include <common>', `#include <common>\n${VERTEX_PARS}`)
      .replace('#include <project_vertex>', `${VERTEX(world)}\n#include <project_vertex>`);
    shader.fragmentShader = shader.fragmentShader
      .replace('#include <common>', `#include <common>\n${FRAGMENT_PARS}`)
      .replace('#include <color_fragment>', `#include <color_fragment>\n${COLOR}`)
      .replace('#include <roughnessmap_fragment>', `#include <roughnessmap_fragment>\n${ROUGHNESS}`)
      .replace('#include <metalnessmap_fragment>', `#include <metalnessmap_fragment>\n${METALNESS}`)
      .replace('#include <emissivemap_fragment>', `#include <emissivemap_fragment>\n${EMISSIVE}`);
  });
};
