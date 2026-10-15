// After FX Lab "underwater" (the caustic pass: a baked web of curved Voronoi walls, two layers drifting
// apart, lit where they cross, projected along the sunlight, with a slight colour split) and
// "water-prison" (caustic light crawling over the trapped figure). Here the caustics are part of
// each direct light (the lighting's RE_Direct), so they are shadowed and coloured like the light,
// on the exported MeshStandardMaterial as it is.

import { DataTexture, LinearFilter, LinearMipmapLinearFilter, RedFormat, RepeatWrapping, UnsignedByteType, type MeshStandardMaterial } from 'three';
import { patchShader, type ShaderApply } from '../../../schema/shaders';
import type { fields } from './meta';

// ---------------------------------------------------------------------------
// The caustic tile, baked once on the CPU (about 50 ms): bright lines along the walls of Voronoi
// cells, the cells warped by a smooth noise so the walls curve. Tiles seamlessly (CELLS per tile).

const SIZE = 256;
const CELLS = 5;

/** Mixed with the constants of MurmurHash3's finaliser (Austin Appleby, public domain). */
function hash(x: number, y: number, seed: number): number {
  let h = Math.imul(x | 0, 0x27d4eb2d) ^ Math.imul(y | 0, 0x165667b1) ^ Math.imul(seed | 0, 0x9e3779b9);
  h = Math.imul(h ^ (h >>> 15), 0x85ebca6b);
  h = Math.imul(h ^ (h >>> 13), 0xc2b2ae35);
  return ((h ^ (h >>> 16)) >>> 0) / 4294967296;
}

/** Unit gradients of a noise that tiles every `period` cells. */
function gradients(period: number, seed: number): Float32Array {
  const g = new Float32Array(period * period * 2);
  for (let j = 0; j < period; j++) {
    for (let i = 0; i < period; i++) {
      const a = hash(i, j, seed) * Math.PI * 2;
      g[(j * period + i) * 2] = Math.cos(a);
      g[(j * period + i) * 2 + 1] = Math.sin(a);
    }
  }
  return g;
}

function gradientNoise(x: number, y: number, period: number, g: Float32Array): number {
  const ix = Math.floor(x);
  const iy = Math.floor(y);
  const fx = x - ix;
  const fy = y - iy;
  const x0 = ((ix % period) + period) % period;
  const y0 = ((iy % period) + period) % period;
  const x1 = (x0 + 1) % period;
  const y1 = (y0 + 1) % period;
  const ux = fx * fx * fx * (fx * (fx * 6 - 15) + 10);
  const uy = fy * fy * fy * (fy * (fy * 6 - 15) + 10);
  let k = (y0 * period + x0) * 2;
  const a = g[k]! * fx + g[k + 1]! * fy;
  k = (y0 * period + x1) * 2;
  const b = g[k]! * (fx - 1) + g[k + 1]! * fy;
  k = (y1 * period + x0) * 2;
  const c = g[k]! * fx + g[k + 1]! * (fy - 1);
  k = (y1 * period + x1) * 2;
  const d = g[k]! * (fx - 1) + g[k + 1]! * (fy - 1);
  return (a + (b - a) * ux + (c - a) * uy + (a - b - c + d) * ux * uy) * 1.4;
}

function bakeCaustics(): Uint8Array {
  const warp = [0, 1].map((w) => [1, 2, 4].map((f, k) => gradients(CELLS * f, 11 + w * 7 + k)));
  const fbm = (x: number, y: number, tables: Float32Array[]): number => {
    let sum = 0;
    let amp = 0.5;
    let f = 1;
    for (let k = 0; k < 3; k++) {
      sum += amp * gradientNoise(x * f, y * f, CELLS * f, tables[k]!);
      f *= 2;
      amp *= 0.5;
    }
    return sum;
  };
  const px = new Float32Array(CELLS * CELLS);
  const py = new Float32Array(CELLS * CELLS);
  for (let j = 0; j < CELLS; j++) {
    for (let i = 0; i < CELLS; i++) {
      px[j * CELLS + i] = 0.1 + 0.8 * hash(i, j, 3);
      py[j * CELLS + i] = 0.1 + 0.8 * hash(i, j, 7);
    }
  }
  const wrap = (i: number): number => ((i % CELLS) + CELLS) % CELLS;
  const out = new Uint8Array(SIZE * SIZE);
  for (let row = 0; row < SIZE; row++) {
    for (let col = 0; col < SIZE; col++) {
      const u = ((col + 0.5) / SIZE) * CELLS;
      const v = ((row + 0.5) / SIZE) * CELLS;
      const x = u + fbm(u, v, warp[0]!) * 0.42;
      const y = v + fbm(u + 5.2, v + 5.2, warp[1]!) * 0.42;
      const nx = Math.floor(x);
      const ny = Math.floor(y);
      const fx = x - nx;
      const fy = y - ny;
      // The nearest feature point, then the exact distance to its cell's border.
      let best = 8;
      let mrx = 0;
      let mry = 0;
      let mgx = 0;
      let mgy = 0;
      for (let j = -1; j <= 1; j++) {
        const base = wrap(ny + j) * CELLS;
        for (let i = -1; i <= 1; i++) {
          const k = base + wrap(nx + i);
          const rx = i + px[k]! - fx;
          const ry = j + py[k]! - fy;
          const d = rx * rx + ry * ry;
          if (d < best) {
            best = d;
            mrx = rx;
            mry = ry;
            mgx = i;
            mgy = j;
          }
        }
      }
      let border = 8;
      for (let j = -2; j <= 2; j++) {
        const gy = mgy + j;
        const base = wrap(ny + gy) * CELLS;
        for (let i = -2; i <= 2; i++) {
          const gx = mgx + i;
          const k = base + wrap(nx + gx);
          const rx = gx + px[k]! - fx;
          const ry = gy + py[k]! - fy;
          const ex = rx - mrx;
          const ey = ry - mry;
          const e2 = ex * ex + ey * ey;
          if (e2 > 1e-5) border = Math.min(border, (0.5 * (mrx + rx) * ex + 0.5 * (mry + ry) * ey) / Math.sqrt(e2));
        }
      }
      const c = Math.exp(-border * 14) * 0.75 + Math.exp(-border * 4) * 0.25;
      out[row * SIZE + col] = c >= 1 ? 255 : Math.round(c * 255);
    }
  }
  return out;
}

let tile: DataTexture | null = null;
/** The caustic tile, shared by every Caustics material. */
function causticTexture(): DataTexture {
  if (!tile) {
    tile = new DataTexture(bakeCaustics(), SIZE, SIZE, RedFormat, UnsignedByteType);
    tile.name = 'gs_caustics';
    tile.wrapS = tile.wrapT = RepeatWrapping;
    tile.magFilter = LinearFilter;
    tile.minFilter = LinearMipmapLinearFilter;
    tile.generateMipmaps = true;
    tile.needsUpdate = true;
  }
  return tile;
}

// ---------------------------------------------------------------------------
// The patch

const VERTEX_PARS = /* glsl */ `
varying vec3 vCaWorld;`;

// After project_vertex: the world position (skinned, instanced and batched copies included).
const VERTEX = /* glsl */ `
{
  vec4 caPos = vec4(transformed, 1.0);
  #ifdef USE_BATCHING
    caPos = batchingMatrix * caPos;
  #endif
  #ifdef USE_INSTANCING
    caPos = instanceMatrix * caPos;
  #endif
  vCaWorld = (modelMatrix * caPos).xyz;
}`;

const FRAGMENT_PARS = /* glsl */ `
uniform float caTime;
uniform sampler2D caMap;
uniform vec4 caColor;
uniform float caStrength;
uniform float caScale;
uniform float caSpeed;
varying vec3 vCaWorld;
float caLayer(vec2 a, vec2 b) { return min(texture2D(caMap, a).r, texture2D(caMap, b).r); }
// Caustic light arriving from direction L (world, toward the light) at world position p: two webs
// drifting different ways, bright where their lines cross, red and blue focused slightly apart.
vec3 caCaustic(vec3 p, vec3 L) {
  // Projected along the light onto the horizontal, so the pattern slants with it; a small fixed slant
  // keeps it from smearing into stripes on walls under a high sun.
  vec2 q = p.xz - p.y * (L.xz / max(L.y, 0.25) + vec2(0.35, 0.22));
  q /= max(caScale, 0.01) * ${CELLS.toFixed(1)}; // tile units: the tile holds ${CELLS} cells
  float t = caTime * caSpeed;
  vec2 warp = vec2(sin(q.y * 6.1 + t * 0.7), cos(q.x * 5.3 - t * 0.6)) * 0.012;
  vec2 a = q + warp + t * vec2(0.021, 0.013);
  vec2 b = mat2(0.8, -0.6, 0.6, 0.8) * q * 0.83 - warp + t * vec2(-0.015, 0.019) + 0.37;
  vec2 split = vec2(0.0026, 0.0019);
  vec3 c = vec3(caLayer(a + split, b + split), caLayer(a, b), caLayer(a - split, b - split));
  return c * c * 3.0;
}`;

// After lights_physical_pars_fragment: every direct light (sun, lamps; their shadows included) also
// casts caustic light.
const DIRECT = /* glsl */ `
void caDirect(const in IncidentLight directLight, const in vec3 geometryPosition, const in vec3 geometryNormal, const in vec3 geometryViewDir, const in vec3 geometryClearcoatNormal, const in PhysicalMaterial material, inout ReflectedLight reflectedLight) {
  RE_Direct_Physical(directLight, geometryPosition, geometryNormal, geometryViewDir, geometryClearcoatNormal, material, reflectedLight);
  vec3 caL = inverseTransformDirection(directLight.direction, viewMatrix);
  // Light refracted by waves arrives at many angles: faces turned sideways to the light catch some.
  float caWrap = saturate(dot(geometryNormal, directLight.direction) * 0.5 + 0.5);
  vec3 caLight = caCaustic(vCaWorld, caL) * caColor.rgb * caStrength;
  reflectedLight.directDiffuse += directLight.color * caWrap * caLight * BRDF_Lambert(material.diffuseContribution);
}
#undef RE_Direct
#define RE_Direct caDirect`;

export const apply: ShaderApply<typeof fields> = (material, _values, ctx) => {
  if (!(material as MeshStandardMaterial).isMeshStandardMaterial) {
    throw new Error('Caustics needs a Principled BSDF material (MeshStandardMaterial in the game)');
  }
  const u = ctx.uniforms;
  const uniforms = { caTime: ctx.time, caMap: { value: causticTexture() }, caColor: u.color, caStrength: u.strength, caScale: u.scale, caSpeed: u.speed };
  patchShader(material, 'gs-caustics', (shader) => {
    Object.assign(shader.uniforms, uniforms);
    shader.vertexShader = shader.vertexShader
      .replace('#include <common>', `#include <common>\n${VERTEX_PARS}`)
      .replace('#include <project_vertex>', `#include <project_vertex>\n${VERTEX}`);
    shader.fragmentShader = shader.fragmentShader
      .replace('#include <common>', `#include <common>\n${FRAGMENT_PARS}`)
      .replace('#include <lights_physical_pars_fragment>', `#include <lights_physical_pars_fragment>\n${DIRECT}`);
  });
};
