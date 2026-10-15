// After FX Lab "interact-glint" and "hover-rim": a band of specular light sweeps diagonally across
// the object every few seconds, small sparkles twinkle on it and a rim lights its edges from above,
// all brighter as `strength` rises (the player coming near). Builds on the Blender material
// (emissive only).

import { Vector2, Vector3, type BufferGeometry, type Material, type MeshStandardMaterial, type Object3D, type SkinnedMesh } from 'three';
import { patchShader, type ShaderApply } from '../../../schema/shaders';
import type { fields } from './meta';

/** The sweep's direction in the object's own space: up and across, like FX Lab's. */
const AXIS = new Vector3(1, 1.6, 0.4).normalize();

// igPhaseHash (here) and igHash13, igHash33 (FRAGMENT_PARS) are David Hoskins' hash13 and hash33 ("Hash
// without Sine", https://www.shadertoy.com/view/4djSRW, MIT; THIRD-PARTY-NOTICES.md).
const VERTEX_PARS = /* glsl */ `
varying vec3 vIgLocal;
varying float vIgPhase;
float igPhaseHash(vec3 p3) { p3 = fract(p3 * 0.1031); p3 += dot(p3, p3.zyx + 31.32); return fract((p3.x + p3.y) * p3.z); }`;

// Before project_vertex: `transformed` is in the object's own space (a GPU instance's own space).
// Each object (and GPU instance) gets its own phase from where it stands on the ground (bobbing up
// and down keeps it), so a row of pickups does not glint in step.
const VERTEX = /* glsl */ `
vIgLocal = transformed;
vec4 igOrigin = vec4(0.0, 0.0, 0.0, 1.0);
#ifdef USE_BATCHING
  igOrigin = batchingMatrix * igOrigin;
#endif
#ifdef USE_INSTANCING
  igOrigin = instanceMatrix * igOrigin;
#endif
igOrigin = modelMatrix * igOrigin;
vIgPhase = igPhaseHash(vec3(floor(igOrigin.xz * 4.0), 0.5));`;

const FRAGMENT_PARS = /* glsl */ `
uniform vec4 igColor;
uniform float igStrength;
uniform float igPeriod;
uniform float igSparkle;
uniform vec2 igRange;
uniform vec3 igAxis;
uniform float igTime;
varying vec3 vIgLocal;
varying float vIgPhase;
float igHash13(vec3 p3) { p3 = fract(p3 * 0.1031); p3 += dot(p3, p3.zyx + 31.32); return fract((p3.x + p3.y) * p3.z); }
vec3 igHash33(vec3 p3) { p3 = fract(p3 * vec3(0.1031, 0.1030, 0.0973)); p3 += dot(p3, p3.yxz + 33.33); return fract((p3.xxy + p3.yxx) * p3.zyx); }`;

// After emissivemap_fragment. Off (a uniform branch) at strength 0.
const EMISSIVE = /* glsl */ `
if (igStrength > 0.0) {
  float igS = clamp(igStrength, 0.0, 1.0);
  float igGain = min(igStrength * 4.0, 1.0) * max(igStrength, 1.0);
  vec3 igV = normalize(vViewPosition);
  float igFacing = clamp(dot(normal, igV), 0.0, 1.0);
  float igSpan = max(igRange.y - igRange.x, 1e-3);
  // The sweep crosses the object in 0.55 s, once per period.
  float igPeriodS = max(igPeriod, 0.6);
  float igSweep = mod(igTime + vIgPhase * igPeriodS, igPeriodS) / 0.55;
  float igPos = mix(igRange.x - 0.12 * igSpan, igRange.y + 0.12 * igSpan, igSweep);
  float igX = (dot(vIgLocal, igAxis) - igPos) / (0.075 * igSpan);
  float igBand = exp(-igX * igX) * step(igSweep, 1.2);
  float igCore = exp(-igX * igX * 9.0) * step(igSweep, 1.2);
  float igFres = pow(1.0 - igFacing, 3.0);
  // FX Lab's look at about half its brightness: a pickup can fill the screen in a game, and bloom
  // would turn the full-strength sweep into a haze.
  vec3 igCol = igColor.rgb * (igBand * (0.6 + 1.4 * igS) + igFres * (0.15 + 0.6 * igS));
  igCol += vec3(1.0) * igCore * (0.6 + 1.6 * igS) * (0.4 + 0.6 * igFacing);
  // The hover rim: brighter on edges that face up, as if lit from above.
  float igUp = clamp(inverseTransformDirection(normal, viewMatrix).y * 0.5 + 0.5, 0.0, 1.0);
  igCol += igColor.rgb * pow(1.0 - igFacing, 4.0) * igUp * 0.5 * igS;
  // Sparkles: a few cells of a 3D grid hold a star that twinkles now and then.
  vec3 igC = vIgLocal * (14.0 / igSpan);
  vec3 igCell = floor(igC);
  float igH = igHash13(igCell);
  vec3 igD = fract(igC) - 0.5 - (igHash33(igCell + 7.1) - 0.5) * 0.5;
  float igStar = exp(-dot(igD, igD) * 180.0);
  float igTw = pow(max(sin(igTime * (2.0 + 2.0 * igH) + (igH + vIgPhase) * 40.0), 0.0), 16.0) * step(0.72, igH);
  igCol += vec3(1.0) * igStar * igTw * (2.0 + 6.0 * igS) * igSparkle;
  totalEmissiveRadiance += igCol * igGain;
}`;

/** Bounds of a drawn geometry along the sweep axis, in its object's space, measured once. */
const spans = new WeakMap<BufferGeometry, Vector2>();
function axisSpan(object: Object3D, geometry: BufferGeometry): Vector2 | null {
  let span = spans.get(geometry);
  if (span) return span;
  const position = geometry.getAttribute('position');
  if (!position) return null;
  const skinned = (object as SkinnedMesh).isSkinnedMesh ? (object as SkinnedMesh) : null;
  const v = new Vector3();
  let lo = Infinity;
  let hi = -Infinity;
  for (let i = 0; i < position.count; i++) {
    if (skinned) skinned.getVertexPosition(i, v);
    else v.fromBufferAttribute(position, i);
    const d = v.dot(AXIS);
    lo = Math.min(lo, d);
    hi = Math.max(hi, d);
  }
  if (!(hi >= lo)) return null;
  spans.set(geometry, (span = new Vector2(lo, hi)));
  return span;
}

/** The sweep crosses the meshes that draw the material from end to end: measured as they are first drawn. */
function measureSpan(material: Material, range: Vector2): void {
  const seen = new WeakSet<BufferGeometry>();
  let empty = true;
  const previous = material.onBeforeRender;
  material.onBeforeRender = function (renderer, scene, camera, geometry, object, group) {
    previous.call(this, renderer, scene, camera, geometry, object, group);
    if (seen.has(geometry)) return;
    seen.add(geometry);
    const span = axisSpan(object, geometry);
    if (!span) return;
    if (empty) range.copy(span);
    else range.set(Math.min(range.x, span.x), Math.max(range.y, span.y));
    empty = false;
  };
}

export const apply: ShaderApply<typeof fields> = (material, _values, ctx) => {
  if (!(material as MeshStandardMaterial).isMeshStandardMaterial) {
    throw new Error('InteractGlint needs a Principled BSDF material (MeshStandardMaterial in the game)');
  }
  const u = ctx.uniforms;
  const range = new Vector2(-0.5, 1);
  const uniforms = {
    igColor: u.color,
    igStrength: u.strength,
    igPeriod: u.period,
    igSparkle: u.sparkle,
    igRange: { value: range },
    igAxis: { value: AXIS },
    igTime: ctx.time,
  };
  measureSpan(material, range);
  patchShader(material, 'gs-interact-glint', (shader) => {
    Object.assign(shader.uniforms, uniforms);
    shader.vertexShader = shader.vertexShader
      .replace('#include <common>', `#include <common>\n${VERTEX_PARS}`)
      .replace('#include <project_vertex>', `${VERTEX}\n#include <project_vertex>`);
    shader.fragmentShader = shader.fragmentShader
      .replace('#include <common>', `#include <common>\n${FRAGMENT_PARS}`)
      .replace('#include <emissivemap_fragment>', `#include <emissivemap_fragment>\n${EMISSIVE}`);
  });
};
