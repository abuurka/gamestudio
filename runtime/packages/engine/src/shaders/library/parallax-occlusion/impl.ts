// After FX Lab "parallax-ruins".
// Parallax occlusion mapping on the Blender material: a linear search through height layers (fewer
// when seen head-on), a secant refinement, then a short march towards the sun for soft
// self-shadowing. Every texture the material has (colour, roughness, normal, ...) is read at the
// displaced UV, the normal follows the height's slope and low parts darken. The height comes from
// an asset texture, or procedural flagstones (baked once on the CPU) when the field is empty.
// Loops are bounded and use textureGrad, so the variable step count stays derivative-safe.

import { DataTexture, LinearMipmapLinearFilter, RepeatWrapping, RGBAFormat, ShaderChunk, UnsignedByteType } from 'three';
import type { MeshStandardMaterial } from 'three';
import { patchShader, type ShaderApply } from '../../../schema/shaders';
import type { fields } from './meta';

// ---------------------------------------------------------------------------
// Procedural flagstones: one tileable repeat, R height (1 = stone top), G a random per stone.

const BAKE = 512;
/** Stones per repeat: 5 rows of 4 (0.6 m by 0.75 m at the default 3 m repeat), running bond. */
const ROWS = 5;
const COLS = 4;

function fract(x: number): number {
  return x - Math.floor(x);
}

/**
 * David Hoskins' hash12, as in the FX shaders ("Hash without Sine", https://www.shadertoy.com/view/4djSRW,
 * MIT; THIRD-PARTY-NOTICES.md).
 */
function hash12(x: number, y: number): number {
  let a = fract(x * 0.1031);
  let b = fract(y * 0.1031);
  let c = fract(x * 0.1031);
  const d = a * (b + 33.33) + b * (c + 33.33) + c * (a + 33.33);
  a += d;
  b += d;
  c += d;
  return fract((a + b) * c);
}

const smoothstep = (e0: number, e1: number, x: number): number => {
  const t = Math.min(1, Math.max(0, (x - e0) / (e1 - e0)));
  return t * t * (3 - 2 * t);
};
const mod = (a: number, n: number): number => ((a % n) + n) % n;

/** Tiling value noise: random values on a `period` × `period` lattice over one repeat (u, v in 0..1). */
class TileNoise {
  readonly period: number;
  private readonly values: Float32Array;
  constructor(period: number, seed: number) {
    this.period = period;
    this.values = new Float32Array(period * period);
    for (let j = 0; j < period; j++) for (let i = 0; i < period; i++) this.values[j * period + i] = hash12(i + seed, j + seed * 1.7);
  }
  /** -1..1 */
  at(u: number, v: number): number {
    const n = this.period;
    const x = u * n;
    const y = v * n;
    const ix = Math.floor(x);
    const iy = Math.floor(y);
    const fx = x - ix;
    const fy = y - iy;
    const sx = fx * fx * (3 - 2 * fx);
    const sy = fy * fy * (3 - 2 * fy);
    const i0 = mod(ix, n);
    const i1 = (i0 + 1) % n;
    const j0 = mod(iy, n) * n;
    const j1 = ((mod(iy, n) + 1) % n) * n;
    const t = this.values;
    const top = t[j0 + i0]! + (t[j0 + i1]! - t[j0 + i0]!) * sx;
    const bottom = t[j1 + i0]! + (t[j1 + i1]! - t[j1 + i0]!) * sx;
    return (top + (bottom - top) * sy) * 2 - 1;
  }
}

/** Octaves of tiling value noise, each twice the frequency of the last. */
function fbm(octaves: TileNoise[], u: number, v: number): number {
  let sum = 0;
  let amp = 0.5;
  let norm = 0;
  for (const octave of octaves) {
    sum += amp * octave.at(u, v);
    norm += amp;
    amp *= 0.5;
  }
  return sum / norm;
}

const octaves = (period: number, seed: number, count: number): TileNoise[] =>
  Array.from({ length: count }, (_, o) => new TileNoise(period << o, seed + o * 13));

/** Distance to the nearest Voronoi edge (F2 - F1, in cells) on a tiling grid of `period` cells. */
function voronoiEdge(points: Float32Array, period: number, u: number, v: number): number {
  const x = u * period;
  const y = v * period;
  const ix = Math.floor(x);
  const iy = Math.floor(y);
  let f1 = 9;
  let f2 = 9;
  for (let j = -1; j <= 1; j++) {
    for (let i = -1; i <= 1; i++) {
      const k = (mod(iy + j, period) * period + mod(ix + i, period)) * 2;
      const dx = ix + i + points[k]! - x;
      const dy = iy + j + points[k + 1]! - y;
      const dist = Math.sqrt(dx * dx + dy * dy);
      if (dist < f1) {
        f2 = f1;
        f1 = dist;
      } else if (dist < f2) f2 = dist;
    }
  }
  return f2 - f1;
}

let flagstones: DataTexture | undefined;

/** The procedural flagstone height texture (made on first use, shared by every material). */
export function flagstoneTexture(): DataTexture {
  if (flagstones) return flagstones;
  const data = new Uint8Array(BAKE * BAKE * 4);
  const colW = 3 / COLS; // metres at the default 3 m repeat: bevels and chips are sized in metres
  const rowH = 3 / ROWS;
  const body = octaves(6, 1, 3);
  const chips = octaves(12, 4, 3);
  const warps = octaves(4, 9, 2);
  const CRACKS = 4;
  const points = new Float32Array(CRACKS * CRACKS * 2);
  for (let j = 0; j < CRACKS; j++) {
    for (let i = 0; i < CRACKS; i++) {
      points[(j * CRACKS + i) * 2] = hash12(i + 3.1, j + 7.7);
      points[(j * CRACKS + i) * 2 + 1] = hash12(i + 9.4, j + 1.3);
    }
  }
  for (let py = 0; py < BAKE; py++) {
    const v = (py + 0.5) / BAKE;
    const y = v * ROWS;
    const row = Math.floor(y);
    const fy = y - row;
    const shift = hash12(row, 3);
    for (let px = 0; px < BAKE; px++) {
      const u = (px + 0.5) / BAKE;
      const x = u * COLS + shift;
      const col = mod(Math.floor(x), COLS);
      const fx = fract(x);
      const id = hash12(col + 11, row + 11);
      const edge = Math.min(Math.min(fx, 1 - fx) * colW, Math.min(fy, 1 - fy) * rowH);
      const n1 = fbm(body, u, v);
      // Bevelled stone tops at slightly different heights, each a little tilted.
      const bevel = smoothstep(0.015, 0.07 + 0.03 * n1, edge);
      const top = 0.8 + 0.13 * id + n1 * 0.06 + (fx - 0.5) * 0.16 * (hash12(col + 5, row + 5) - 0.5);
      const low = 0.3 + 0.05 * n1;
      let h = low + (top - low) * bevel;
      // Chipped edges and a few cracks.
      if (edge < 0.22) {
        const chip = smoothstep(0.55, 0.75, fbm(chips, fract(u + id * 0.37), fract(v + id * 0.61)) * 0.5 + 0.5);
        h -= chip * (1 - smoothstep(0.05, 0.22, edge)) * 0.25;
      }
      if (bevel > 0 && hash12(col + 7, row + 7) >= 0.45) {
        const warp = fbm(warps, u, v) * 0.12;
        h -= (1 - smoothstep(0, 0.035, voronoiEdge(points, CRACKS, fract(u + warp), fract(v - warp)))) * bevel * 0.4;
      }
      const i = (py * BAKE + px) * 4;
      data[i] = Math.round(Math.min(1, Math.max(0, h)) * 255);
      data[i + 1] = Math.round(id * 255);
      data[i + 2] = 0;
      data[i + 3] = 255;
    }
  }
  const texture = new DataTexture(data, BAKE, BAKE, RGBAFormat, UnsignedByteType);
  texture.wrapS = texture.wrapT = RepeatWrapping;
  texture.minFilter = LinearMipmapLinearFilter;
  texture.generateMipmaps = true;
  texture.anisotropy = 4;
  texture.name = 'gs_flagstones';
  texture.needsUpdate = true;
  flagstones = texture;
  return texture;
}

// ---------------------------------------------------------------------------
// The patch

const VERTEX_HEAD = /* glsl */ `
varying vec2 vGsPomUv;
`;

const FRAGMENT_HEAD = /* glsl */ `
uniform sampler2D uPomHeight;
uniform float uPomProcedural;
uniform float uPomSize;
uniform float uPomDepth;
uniform float uPomSteps;
uniform float uPomShadow;
uniform float uPomGaps;
varying vec2 vGsPomUv;
vec2 gsPomOffset = vec2(0.0);
vec4 gsPomSample = vec4(1.0);
vec3 gsPomBend = vec3(0.0);
float gsPomLight = 1.0;
float gsPomH(vec2 uv, vec2 gx, vec2 gy) { return textureGrad(uPomHeight, uv / uPomSize, gx, gy).r; }
`;

// Before map_fragment: march the view ray through the height field (view space, UV per metre).
const FRAGMENT_MARCH = /* glsl */ `
{
  vec3 gN;
#ifdef FLAT_SHADED
  gN = normalize(cross(dFdx(vViewPosition), dFdy(vViewPosition)));
#else
  gN = normalize(vNormal);
  #ifdef DOUBLE_SIDED
    gN *= gl_FrontFacing ? 1.0 : -1.0;
  #endif
#endif
  // UV per metre along the surface (the cotangent frame), from screen-space derivatives.
  vec3 q0 = dFdx(-vViewPosition), q1 = dFdy(-vViewPosition);
  vec2 st0 = dFdx(vGsPomUv), st1 = dFdy(vGsPomUv);
  vec3 q1perp = cross(q1, gN), q0perp = cross(gN, q0);
  float det = dot(q0, q1perp);
  float inv = abs(det) > 1e-12 ? 1.0 / det : 0.0;
  vec3 gu = (q1perp * st0.x + q0perp * st1.x) * inv;
  vec3 gv = (q1perp * st0.y + q0perp * st1.y) * inv;
  vec2 gx = st0 / uPomSize, gy = st1 / uPomSize;

  vec3 V = isOrthographic ? vec3(0.0, 0.0, 1.0) : normalize(vViewPosition);
  float cosv = max(dot(gN, V), 0.0);
  float steps = clamp(floor(mix(uPomSteps, uPomSteps * 0.35, cosv)), 4.0, 64.0);
  float layer = 1.0 / steps;
  vec2 duv = -vec2(dot(gu, V), dot(gv, V)) / max(cosv, 0.08) * uPomDepth * layer;
  vec2 p = vGsPomUv, prevP = p;
  float d = 0.0, prevD = 0.0;
  float sd = 1.0 - gsPomH(p, gx, gy), prevSd = sd;
  for (int i = 0; i < 64; i++) {
    if (float(i) >= steps || d >= sd) break;
    prevP = p; prevD = d; prevSd = sd;
    p += duv; d += layer;
    sd = 1.0 - gsPomH(p, gx, gy);
  }
  float after = d - sd, before = prevSd - prevD;
  float w = after / max(after + before, 1e-5);
  p = mix(p, prevP, w);
  d = mix(d, prevD, w);
  gsPomOffset = p - vGsPomUv;
  gsPomSample = textureGrad(uPomHeight, p / uPomSize, gx, gy);

  // Slope of the height field for the shading normal (central differences, one texel apart).
  vec2 e = vec2(uPomSize * 1.5 / float(textureSize(uPomHeight, 0).x), 0.0);
  vec2 dh = vec2(gsPomH(p + e.xy, gx, gy) - gsPomH(p - e.xy, gx, gy), gsPomH(p + e.yx, gx, gy) - gsPomH(p - e.yx, gx, gy)) / (2.0 * e.x);
  gsPomBend = (gu * dh.x + gv * dh.y) * uPomDepth;

  // Soft self-shadow: march up towards the sun and keep the closest miss.
#if NUM_DIR_LIGHTS > 0
  vec3 L = directionalLights[0].direction;
  float cosl = dot(gN, L);
  if (uPomShadow > 0.0 && cosl > 0.02) {
    vec2 luv = vec2(dot(gu, L), dot(gv, L)) / cosl * uPomDepth;
    float sStep = max(d, 0.02) / 12.0 + 0.004;
    float sdep = d;
    vec2 sp = p;
    float lit = 1.0;
    for (int i = 0; i < 16; i++) {
      sdep -= sStep;
      if (sdep <= 0.0) break;
      sp += luv * sStep;
      float occ = saturate((sdep - (1.0 - gsPomH(sp, gx, gy))) / (0.03 + float(i + 1) * sStep * 0.5));
      lit = min(lit, 1.0 - occ);
    }
    gsPomLight = mix(1.0, lit, saturate(uPomShadow));
  }
#endif
}
`;

// After color_fragment: low parts darken (dirt and occlusion); flagstones vary a little in tone.
const FRAGMENT_TONE = /* glsl */ `
diffuseColor.rgb *= mix(1.0 - saturate(uPomGaps), 1.0, smoothstep(0.1, 0.85, gsPomSample.r));
diffuseColor.rgb *= mix(1.0, 0.82 + 0.36 * gsPomSample.g, uPomProcedural);
`;

const FRAGMENT_NORMAL = /* glsl */ `
normal = normalize(normal - gsPomBend);
`;

const FRAGMENT_SHADOW = /* glsl */ `
reflectedLight.directDiffuse *= gsPomLight;
reflectedLight.directSpecular *= gsPomLight;
`;

/** Chunks that read a texture: read at the displaced UV. */
const OFFSET_CHUNKS = [
  'map_fragment',
  'alphamap_fragment',
  'roughnessmap_fragment',
  'metalnessmap_fragment',
  'normal_fragment_maps',
  'emissivemap_fragment',
  'aomap_fragment',
] as const;
const MAP_UV = /\bv(?:Map|AlphaMap|RoughnessMap|MetalnessMap|NormalMap|BumpMap|EmissiveMap|AoMap)Uv\b/g;

function offsetChunk(name: (typeof OFFSET_CHUNKS)[number]): string {
  return ShaderChunk[name].replace(MAP_UV, (uv) => `( ${uv} + gsPomOffset )`);
}

export const apply: ShaderApply<typeof fields> = (material, values, ctx) => {
  if (!(material as MeshStandardMaterial).isMeshStandardMaterial) {
    throw new Error(`ParallaxOcclusion needs a lit (Principled BSDF) material, got ${material.type}`);
  }
  const u = ctx.uniforms;
  const height = u.height;
  if (!values.height) flagstoneTexture(); // bake now, not on the first frame
  patchShader(material, 'gs_parallax_occlusion', (shader) => {
    Object.assign(shader.uniforms, {
      // The asset when one is set and loaded, the flagstones otherwise.
      uPomHeight: { get value() { return height.value ?? flagstoneTexture(); } },
      uPomProcedural: { get value() { return height.value ? 0 : 1; } },
      uPomSize: u.size,
      uPomDepth: u.depth,
      uPomSteps: u.steps,
      uPomShadow: u.shadow,
      uPomGaps: u.gaps,
    });
    shader.vertexShader = shader.vertexShader
      .replace('#include <common>', `#include <common>\n${VERTEX_HEAD}`)
      .replace('#include <uv_vertex>', '#include <uv_vertex>\n\tvGsPomUv = uv;');
    let fragment = shader.fragmentShader
      .replace('#include <common>', `#include <common>\n${FRAGMENT_HEAD}`)
      .replace('#include <map_fragment>', () => `${FRAGMENT_MARCH}\n${offsetChunk('map_fragment')}`)
      .replace('#include <color_fragment>', `#include <color_fragment>\n${FRAGMENT_TONE}`)
      .replace('#include <normal_fragment_maps>', () => `${offsetChunk('normal_fragment_maps')}\n${FRAGMENT_NORMAL}`)
      .replace('#include <lights_fragment_end>', `#include <lights_fragment_end>\n${FRAGMENT_SHADOW}`);
    for (const name of OFFSET_CHUNKS) {
      if (name === 'map_fragment' || name === 'normal_fragment_maps') continue;
      fragment = fragment.replace(`#include <${name}>`, () => offsetChunk(name));
    }
    shader.fragmentShader = fragment;
  });
};
