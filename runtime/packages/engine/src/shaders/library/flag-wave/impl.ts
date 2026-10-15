// After FX Lab "cloth-banners" (cloth pinned along one edge, rippled by travelling gust bands, the sun
// glowing through it). The Verlet cloth there becomes travelling folds in the vertex shader here.

import { Float32BufferAttribute, type BufferGeometry, type Material, type MeshStandardMaterial } from 'three';
import { patchShader, type ShaderApply, type ShaderEdit, type ShaderMesh } from '../../../schema/shaders';
import { shadowTwins, windGlsl } from '../foliage-sway/wind';
import type { fields } from './meta';

type Uniforms = Record<string, { value: unknown }>;
type Pin = 'left' | 'right' | 'top' | 'bottom';

/**
 * Per vertex, written once per geometry from its UV map: w = 0 at the pinned edge .. 1 at the free
 * edge; xyz = how far the vertex moves in the object's space per unit of w (its length is the flag's
 * length, its direction runs away from the pin over the cloth).
 */
const FLAG = 'gsFlag';

const VERTEX_PARS = /* glsl */ `
uniform float uTime;
uniform float uFlagWind;
uniform float uFlagAngle;
uniform float uFlagSpeed;
uniform float uFlagAmplitude;
uniform float uFlagWaveLength;
attribute vec4 ${FLAG};
${windGlsl}
// Fold height (m) at distance d from the pin and a across the flag; w is d over the flag's length.
// Long folds, shorter diagonal ones, and a quick flutter that only the free end has.
float gsFlagHeight(float d, float a, float w, float k, float omega, float phase, float strength) {
  w = clamp(w, 0.0, 1.0);
  float ripple = sin(k * d - omega + phase)
    + 0.45 * sin(1.9 * k * d - 1.7 * omega + 1.3 + a * k * 0.35 + phase)
    + 0.25 * sin(3.3 * k * d - 2.9 * omega + 2.1 - a * k * 0.5)
    + 0.3 * w * w * sin(5.3 * k * d - 4.1 * omega + a * k * 0.9 + 0.7);
  return uFlagAmplitude * strength * w * (0.55 + 0.45 * w) * ripple;
}
vec3 gsFlagOffset;
vec3 gsFlagNormal;
// Folds travel along the cloth from the pin, swell in gusts and towards the free edge; the cloth draws
// in a little as it folds. Sets gsFlagOffset (object space) and gsFlagNormal (object space).
void gsFlagWave(vec3 p, vec3 n) {
  gsFlagOffset = vec3(0.0);
  gsFlagNormal = n;
  if (dot(${FLAG}.xyz, ${FLAG}.xyz) < 1e-10) return;
  mat4 model = modelMatrix;
#ifdef USE_INSTANCING
  model = modelMatrix * instanceMatrix;
#endif
  mat3 basis = mat3(model);
  vec3 origin = model[3].xyz;
  vec3 T = basis * ${FLAG}.xyz;
  float L = length(T);
  T /= L;
  vec3 N = normalize(basis * n);
  vec3 B = normalize(cross(N, T));
  float w = ${FLAG}.w;
  float d = w * L;
  float a = dot(basis * p, B);
  float s = clamp(uFlagWind, 0.0, 1.5);
  float t = uTime * uFlagSpeed;
  vec2 dir = gsWindDir(uFlagAngle);
  float gust = gsWindGust(origin.xz, dir, gsWindTravel(t, s));
  float phase = gsWindHash(floor(origin.xz * 5.0) + 0.5) * 6.2832;
  float k = 6.2832 / max(uFlagWaveLength, 0.05);
  float omega = k * t * (1.2 + 3.0 * s);
  float strength = 0.35 + 0.65 * s + 0.6 * gust * s;
  float h = gsFlagHeight(d, a, w, k, omega, phase, strength);
  float e = 0.02;
  float hd = (gsFlagHeight(d + e, a, w + e / L, k, omega, phase, strength) - h) / e;
  float ha = (gsFlagHeight(d, a + e, w, k, omega, phase, strength) - h) / e;
  // Folded cloth covers less ground: pull it towards the pin by about its mean squared slope.
  float slope = uFlagAmplitude * strength * clamp(w, 0.0, 1.0) * k;
  gsFlagOffset = inverse(basis) * (N * h - T * (0.35 * slope * slope * d));
  gsFlagNormal = normalize(transpose(basis) * (N - T * hd - B * ha));
}`;

// The normal before three.js transforms it, the offset once 'transformed' exists.
const NORMAL = /* glsl */ `
#include <beginnormal_vertex>
gsFlagWave(position, objectNormal);
objectNormal = gsFlagNormal;`;
const MOVE = /* glsl */ `
#include <begin_vertex>
transformed += gsFlagOffset;`;
const MOVE_SHADOW = /* glsl */ `
#include <begin_vertex>
gsFlagWave(position, normal);
transformed += gsFlagOffset;`;

// After lights_fragment_begin (directLight still holds the shadowed sun): thin fabric glows when the
// sun is behind it, most when looking into the sun.
const THROUGH = /* glsl */ `
#if NUM_DIR_LIGHTS > 0
{
  float flagThrough = pow(clamp(dot(-geometryViewDir, directLight.direction), 0.0, 1.0), 3.0);
  float flagBack = clamp(-dot(geometryNormal, directLight.direction), 0.0, 1.0);
  reflectedLight.directDiffuse += directLight.color * diffuseColor.rgb * (flagThrough * 0.8 + flagBack * 0.45) * uFlagTranslucency;
}
#endif`;

function wire(shader: { uniforms: Uniforms }, u: Uniforms, time: { value: number }): void {
  shader.uniforms.uTime = time;
  shader.uniforms.uFlagWind = u.wind!;
  shader.uniforms.uFlagAngle = u.windAngle!;
  shader.uniforms.uFlagSpeed = u.speed!;
  shader.uniforms.uFlagAmplitude = u.amplitude!;
  shader.uniforms.uFlagWaveLength = u.waveLength!;
}

/** The vertex motion alone, for the shadow pass. */
function shadowEdit(u: Uniforms, time: { value: number }): ShaderEdit {
  return (shader) => {
    wire(shader, u, time);
    shader.vertexShader = shader.vertexShader
      .replace('#include <common>', `#include <common>\n${VERTEX_PARS}`)
      .replace('#include <begin_vertex>', MOVE_SHADOW);
  };
}

const written = new WeakMap<BufferGeometry, Pin>();

/**
 * Writes the cloth attribute from the UV map: the pin weight (normalised over the geometry, so a flag
 * in a texture atlas works) and its gradient over each triangle, averaged per vertex. False when the
 * geometry has no UV map that varies across the flag.
 */
export function flagAttribute(geometry: BufferGeometry, pin: Pin): boolean {
  if (written.get(geometry) === pin) return true;
  const position = geometry.getAttribute('position');
  const uv = geometry.getAttribute('uv');
  if (!position || !uv) return false;
  const n = position.count;
  // three.js UVs from a GLB have v = 0 at Blender's top (glTF flips V).
  const raw = (i: number): number =>
    pin === 'left' ? uv.getX(i) : pin === 'right' ? -uv.getX(i) : pin === 'top' ? uv.getY(i) : -uv.getY(i);
  let lo = Infinity, hi = -Infinity;
  for (let i = 0; i < n; i++) {
    lo = Math.min(lo, raw(i));
    hi = Math.max(hi, raw(i));
  }
  if (!(hi - lo > 1e-6)) return false;
  const w = new Float32Array(n);
  for (let i = 0; i < n; i++) w[i] = (raw(i) - lo) / (hi - lo);
  const grad = new Float32Array(n * 3);
  const count = new Uint16Array(n);
  const index = geometry.getIndex();
  const corners = index ? index.count : n;
  const at = (c: number): number => (index ? index.getX(c) : c);
  for (let c = 0; c + 2 < corners; c += 3) {
    const ia = at(c), ib = at(c + 1), ic = at(c + 2);
    const e1x = position.getX(ib) - position.getX(ia), e1y = position.getY(ib) - position.getY(ia), e1z = position.getZ(ib) - position.getZ(ia);
    const e2x = position.getX(ic) - position.getX(ia), e2y = position.getY(ic) - position.getY(ia), e2z = position.getZ(ic) - position.getZ(ia);
    const nx = e1y * e2z - e1z * e2y, ny = e1z * e2x - e1x * e2z, nz = e1x * e2y - e1y * e2x;
    const nn = nx * nx + ny * ny + nz * nz;
    if (nn < 1e-14) continue;
    const d1 = w[ib]! - w[ia]!, d2 = w[ic]! - w[ia]!;
    // The in-plane gradient of w: (d1 (e2 x n) + d2 (n x e1)) / |n|^2.
    const gx = (d1 * (e2y * nz - e2z * ny) + d2 * (ny * e1z - nz * e1y)) / nn;
    const gy = (d1 * (e2z * nx - e2x * nz) + d2 * (nz * e1x - nx * e1z)) / nn;
    const gz = (d1 * (e2x * ny - e2y * nx) + d2 * (nx * e1y - ny * e1x)) / nn;
    for (const v of [ia, ib, ic]) {
      grad[v * 3] = grad[v * 3]! + gx;
      grad[v * 3 + 1] = grad[v * 3 + 1]! + gy;
      grad[v * 3 + 2] = grad[v * 3 + 2]! + gz;
      count[v] = Math.min(count[v]! + 1, 65535);
    }
  }
  const data = new Float32Array(n * 4);
  for (let i = 0; i < n; i++) {
    const k = Math.max(count[i]!, 1);
    const gx = grad[i * 3]! / k, gy = grad[i * 3 + 1]! / k, gz = grad[i * 3 + 2]! / k;
    const g2 = gx * gx + gy * gy + gz * gz;
    // Distance per unit of w along the (averaged) gradient: grad / |grad|^2.
    if (g2 > 1e-12) {
      data[i * 4] = gx / g2;
      data[i * 4 + 1] = gy / g2;
      data[i * 4 + 2] = gz / g2;
    }
    data[i * 4 + 3] = w[i]!;
  }
  geometry.setAttribute(FLAG, new Float32BufferAttribute(data, 4));
  written.set(geometry, pin);
  return true;
}

export const apply: ShaderApply<typeof fields> = (material, _values, ctx) => {
  if (!(material as MeshStandardMaterial).isMeshStandardMaterial) {
    throw new Error('FlagWave needs a Principled BSDF material (MeshStandardMaterial in the game)');
  }
  const u = ctx.uniforms as unknown as Uniforms;
  patchShader(material, 'gs-flag-wave', (shader) => {
    wire(shader, u, ctx.time);
    shader.uniforms.uFlagTranslucency = u.translucency!;
    shader.vertexShader = shader.vertexShader
      .replace('#include <common>', `#include <common>\n${VERTEX_PARS}`)
      .replace('#include <beginnormal_vertex>', NORMAL)
      .replace('#include <begin_vertex>', MOVE);
    shader.fragmentShader = shader.fragmentShader
      .replace('#include <common>', '#include <common>\nuniform float uFlagTranslucency;')
      .replace('#include <lights_fragment_begin>', `#include <lights_fragment_begin>\n${THROUGH}`);
  });
};

export const mesh: ShaderMesh<typeof fields> = (target, values, ctx) => {
  if (!flagAttribute(target.geometry, values.pinnedEdge)) {
    ctx.warn(`mesh "${target.name}" has no UV map across the flag, so its pinned edge is unknown; it stays still`);
  }
  const drawn = target.material as Material | Material[];
  if (Array.isArray(drawn)) return;
  shadowTwins(target, drawn, 'gs-flag-wave', shadowEdit(ctx.uniforms as unknown as Uniforms, ctx.time));
};
