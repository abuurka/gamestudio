// After FX Lab "arcane-gate" (layered parallax vortex, another place behind the throat, hot rim) and
// "dimensional-rift" (another world seen through the opening).
import {
  AddEquation,
  CustomBlending,
  DataTexture,
  DoubleSide,
  LinearFilter,
  LinearMipmapLinearFilter,
  OneFactor,
  OneMinusSrcAlphaFactor,
  RepeatWrapping,
  RGBAFormat,
  ShaderMaterial,
  UniformsLib,
  UniformsUtils,
  UnsignedByteType,
  Vector3,
  type Material,
  type MeshStandardMaterial,
} from 'three';
import type { ShaderApply, ShaderMesh } from '../../../schema/shaders';
import type { fields } from './meta';

type F = typeof fields;

// ---------------------------------------------------------------------------
// The noise texture: a CPU port of FX Lab's baked noise (R billowy cloud, GB its gradient, A ridged
// detail), tileable, 128² (about 20 ms once per page, the first time a portal is applied).

const SIZE = 128;
const fract = (x: number): number => x - Math.floor(x);
const wrap = (i: number, p: number): number => ((i % p) + p) % p;

/** David Hoskins' hash12 ("Hash without Sine", https://www.shadertoy.com/view/4djSRW, MIT; THIRD-PARTY-NOTICES.md). */
function hash12(x: number, y: number): number {
  let a = fract(x * 0.1031), b = fract(y * 0.1031), c = a;
  const d = a * (b + 33.33) + b * (c + 33.33) + c * (a + 33.33);
  a += d; b += d; c += d;
  return fract((a + b) * c);
}

const gradients = new Map<number, Float32Array>();
function gradientTable(period: number): Float32Array {
  let table = gradients.get(period);
  if (table) return table;
  table = new Float32Array(period * period * 2);
  for (let j = 0; j < period; j++) {
    for (let i = 0; i < period; i++) {
      const a = hash12(i + 0.37 * period, j) * Math.PI * 2;
      table[(j * period + i) * 2] = Math.cos(a);
      table[(j * period + i) * 2 + 1] = Math.sin(a);
    }
  }
  gradients.set(period, table);
  return table;
}

const features = new Map<number, Float32Array>();
function featureTable(period: number): Float32Array {
  let table = features.get(period);
  if (table) return table;
  table = new Float32Array(period * period * 2);
  for (let j = 0; j < period; j++) {
    for (let i = 0; i < period; i++) {
      table[(j * period + i) * 2] = hash12(i * 1.7 + 11.3, j * 1.3 + period) * 0.8 + 0.1;
      table[(j * period + i) * 2 + 1] = hash12(i * 2.3 + 5.1, j * 1.9 + 3.7 * period) * 0.8 + 0.1;
    }
  }
  features.set(period, table);
  return table;
}

/** Periodic gradient noise: wraps every `period` lattice cells. */
function pnoise(x: number, y: number, period: number): number {
  const g = gradientTable(period);
  const ix = Math.floor(x), iy = Math.floor(y), fx = x - ix, fy = y - iy;
  const ux = fx * fx * fx * (fx * (fx * 6 - 15) + 10), uy = fy * fy * fy * (fy * (fy * 6 - 15) + 10);
  const x0 = wrap(ix, period), x1 = wrap(ix + 1, period), y0 = wrap(iy, period) * period, y1 = wrap(iy + 1, period) * period;
  const a = (y0 + x0) * 2, b = (y0 + x1) * 2, c = (y1 + x0) * 2, d = (y1 + x1) * 2;
  const va = g[a]! * fx + g[a + 1]! * fy, vb = g[b]! * (fx - 1) + g[b + 1]! * fy;
  const vc = g[c]! * fx + g[c + 1]! * (fy - 1), vd = g[d]! * (fx - 1) + g[d + 1]! * (fy - 1);
  const top = va + (vb - va) * ux, bottom = vc + (vd - vc) * ux;
  return (top + (bottom - top) * uy) * 1.4;
}

function fbm(x: number, y: number, period: number, octaves: number): number {
  let sum = 0, amplitude = 0.5;
  for (let i = 0; i < octaves; i++) {
    sum += amplitude * pnoise(x, y, period);
    x *= 2; y *= 2; period *= 2; amplitude *= 0.5;
  }
  return sum;
}

/** Distance to the nearest feature point of a periodic cellular pattern. */
function cellular(x: number, y: number, period: number): number {
  const t = featureTable(period);
  const nx = Math.floor(x), ny = Math.floor(y), fx = x - nx, fy = y - ny;
  let best = 8;
  for (let j = -1; j <= 1; j++) {
    const row = wrap(ny + j, period) * period;
    for (let i = -1; i <= 1; i++) {
      const k = (row + wrap(nx + i, period)) * 2;
      const rx = i + t[k]! - fx, ry = j + t[k + 1]! - fy;
      best = Math.min(best, rx * rx + ry * ry);
    }
  }
  return Math.sqrt(best);
}

function billow(u: number, v: number): number {
  const px = u * 5, py = v * 5;
  const wx = fbm(px, py, 5, 4) * 0.55, wy = fbm(px + 7.3, py + 7.3, 5, 4) * 0.55;
  const w1 = 1 - cellular(px + wx, py + wy, 5);
  const w2 = 1 - cellular(px * 2 + wx * 2, py * 2 + wy * 2, 10);
  const w3 = 1 - cellular(px * 4 + wx * 3, py * 4 + wy * 3, 20);
  const f = fbm(px * 2, py * 2, 10, 3);
  return Math.min(1, Math.max(0, w1 * 0.66 + w2 * 0.3 + w3 * 0.06 + f * 0.22 - 0.27));
}

let noise: DataTexture | null = null;

/** The shared noise texture (made on first use). */
function noiseTexture(): DataTexture {
  if (noise) return noise;
  const n = SIZE;
  const cloud = new Float32Array(n * n);
  for (let y = 0; y < n; y++) for (let x = 0; x < n; x++) cloud[y * n + x] = billow((x + 0.5) / n, (y + 0.5) / n);
  const data = new Uint8Array(n * n * 4);
  for (let y = 0; y < n; y++) {
    for (let x = 0; x < n; x++) {
      const i = y * n + x;
      const gx = ((cloud[y * n + ((x + 1) % n)]! - cloud[y * n + ((x + n - 1) % n)]!) * n) / 24;
      const gy = ((cloud[((y + 1) % n) * n + x]! - cloud[((y + n - 1) % n) * n + x]!) * n) / 24;
      const ridge = 1 - Math.abs(fbm(((x + 0.5) / n) * 16, ((y + 0.5) / n) * 16, 16, 5));
      data[i * 4] = Math.round(cloud[i]! * 255);
      data[i * 4 + 1] = Math.round(Math.min(1, Math.max(0, gx * 0.5 + 0.5)) * 255);
      data[i * 4 + 2] = Math.round(Math.min(1, Math.max(0, gy * 0.5 + 0.5)) * 255);
      data[i * 4 + 3] = Math.round(ridge * ridge * 255);
    }
  }
  noise = new DataTexture(data, n, n, RGBAFormat, UnsignedByteType);
  noise.wrapS = noise.wrapT = RepeatWrapping;
  noise.magFilter = LinearFilter;
  noise.minFilter = LinearMipmapLinearFilter;
  noise.generateMipmaps = true;
  noise.needsUpdate = true;
  return noise;
}

// ---------------------------------------------------------------------------

const VERTEX = /* glsl */ `
#include <common>
#include <batching_pars_vertex>
#include <morphtarget_pars_vertex>
#include <skinning_pars_vertex>
#include <fog_pars_vertex>
#include <logdepthbuf_pars_vertex>
#include <clipping_planes_pars_vertex>
varying vec2 vPortalUv;
varying vec3 vWorld;
void main() {
  #include <morphinstance_vertex>
  #include <batching_vertex>
  #include <skinbase_vertex>
  #include <begin_vertex>
  #include <morphtarget_vertex>
  #include <skinning_vertex>
  #include <project_vertex>
  #include <logdepthbuf_vertex>
  #include <clipping_planes_vertex>
  vPortalUv = uv;
  vec4 world = vec4(transformed, 1.0);
  #ifdef USE_BATCHING
  world = batchingMatrix * world;
  #endif
  #ifdef USE_INSTANCING
  world = instanceMatrix * world;
  #endif
  vWorld = (modelMatrix * world).xyz;
  #include <fog_vertex>
}
`;

/**
 * The vortex: four polar layers at increasing depth behind the surface, each offset by the view ray
 * (parallax) so the portal reads as a tunnel rather than a painted disc. Back to front: another place
 * (nebula and stars), perspective tunnel streaks, domain-warped swirl, thin near strands, rim.
 * hash33 is David Hoskins' ("Hash without Sine", https://www.shadertoy.com/view/4djSRW, MIT;
 * THIRD-PARTY-NOTICES.md).
 */
const FRAGMENT = /* glsl */ `
#include <common>
#include <fog_pars_fragment>
#include <logdepthbuf_pars_fragment>
#include <clipping_planes_pars_fragment>
uniform float uTime;
uniform sampler2D uNoise;
uniform vec4 uColor;
uniform vec4 uDestination;
uniform vec3 uTint;
uniform float uOpen;
uniform float uSpeed;
uniform float uIntensity;
varying vec2 vPortalUv;
varying vec3 vWorld;
#define TAU 6.2831853
vec3 hash33(vec3 p3) { p3 = fract(p3 * vec3(0.1031, 0.1030, 0.0973)); p3 += dot(p3, p3.yxz + 33.33); return fract((p3.xxy + p3.yxx) * p3.zyx); }
// Mip level for a polar lookup, computed without derivatives so the atan seam never shows.
float polarLod(float pix, float rate) { return clamp(log2(max(pix * rate * ${SIZE.toFixed(1)}, 1.0)), 0.0, ${Math.log2(SIZE).toFixed(1)}); }
void main() {
  #include <clipping_planes_fragment>
  #include <logdepthbuf_fragment>
  float open = max(uOpen, 1e-3);
  // The surface frame from screen derivatives (before any discard): metres per unit of UV, and the view ray in it.
  vec3 dpx = dFdx(vWorld), dpy = dFdy(vWorld);
  vec2 dux = dFdx(vPortalUv), duy = dFdy(vPortalUv);
  float det = dux.x * duy.y - dux.y * duy.x;
  vec3 Pu = (dpx * duy.y - dpy * dux.y) / (abs(det) > 1e-12 ? det : 1.0);
  vec3 Pv = (dpy * dux.x - dpx * duy.x) / (abs(det) > 1e-12 ? det : 1.0);
  vec3 Nf = normalize(cross(dpx, dpy));
  vec3 V = normalize(vWorld - cameraPosition);
  float into = max(abs(dot(V, Nf)), 0.35);
  // q runs -1..1 across the UV square: metres per unit of q along u and v.
  vec2 metres = vec2(length(Pu), length(Pv)) * 0.5 * open;
  vec2 par = abs(det) > 1e-12 ? vec2(dot(V, Pu) / max(length(Pu), 1e-6) / max(metres.x, 1e-4), dot(V, Pv) / max(length(Pv), 1e-6) / max(metres.y, 1e-4)) / into : vec2(0.0);
  // Seen from the side, the deep layers would slide off the disc: cap the shift so the throat stays in view.
  par *= min(1.0, 0.22 / max(length(par), 1e-4));
  vec2 q = (vPortalUv - 0.5) * 2.0 / open;
  float pix = max(fwidth(q.x) + fwidth(q.y), 1e-5) * 0.7;
  float r = length(q);
  if (r > 1.0) discard;
  float t = uTime * uSpeed;
  float angle = atan(q.y, q.x);

  vec3 accent = uColor.rgb * uTint;
  vec3 hot = mix(accent, vec3(1.0), 0.55);
  vec3 deep = accent * accent * 0.55;
  vec3 other = uDestination.rgb;

  // Another place, 7 m behind the throat: a cold sky with a nebula, stars and a ridge of far mountains.
  vec2 qo = (q + par * 7.0) * open;
  float neb = textureLod(uNoise, qo * 0.22 + vec2(t * 0.006, 0.31), 2.0).r;
  float neb2 = textureLod(uNoise, qo * 0.5 + vec2(0.57, -t * 0.008), 1.5).r;
  vec3 place = other * (0.12 + 0.5 * smoothstep(-0.9, 0.9, qo.y)) * (0.35 + 0.9 * neb * neb2);
  vec2 sc = qo * 4.0;
  vec3 sh = hash33(vec3(floor(sc), 3.7));
  float sd = length(fract(sc) - 0.5 - (sh.xy - 0.5) * 0.6);
  place += vec3(0.85, 0.92, 1.0) * step(0.8, sh.z) * exp(-sd * sd * 90.0) * (1.2 + 0.8 * sin(t * 2.3 + sh.z * 40.0)) * smoothstep(-0.3, 0.2, qo.y);
  float ridge = -0.28 + 0.2 * textureLod(uNoise, vec2(qo.x * 0.18 + 0.4, 0.12), 1.0).r + 0.05 * textureLod(uNoise, vec2(qo.x * 0.7, 0.62), 0.0).a;
  float land = smoothstep(ridge + 0.012, ridge - 0.012, qo.y);
  place = mix(place, other * 0.03 + accent * 0.02, land);
  place += other * 0.35 * exp(-abs(qo.y - ridge) * 14.0) * (1.0 - land * 0.6);
  float throat = 1.0 - smoothstep(0.12, 0.5, length(q + par * 2.5));

  // Tunnel walls 1.2 m deep: v = 1/r is how a tube looks in perspective, so streaks fall into a vanishing point.
  vec2 q1 = q + par * 1.2;
  float r1 = max(length(q1), 1e-3);
  float a1 = atan(q1.y, q1.x) / TAU;
  float vt = 0.3 / r1;
  vec2 uvT = vec2(a1 * 3.0 + vt * 0.25 + t * 0.04, vt * 0.35 - t * 0.3);
  float lodT = polarLod(pix, max(3.0 / (TAU * r1), 0.1 / (r1 * r1)));
  vec4 nT = textureLod(uNoise, uvT, lodT);
  float tunnel = smoothstep(0.35, 0.95, textureLod(uNoise, uvT + (nT.gb - 0.5) * 0.3 + vec2(0.37, 0.11), lodT).r);
  float depthFade = smoothstep(0.12, 0.85, r1);

  // Main swirl 0.45 m deep: log-polar, stretched along the flow so it reads as streaming arms, domain-warped.
  vec2 q2 = q + par * 0.45;
  float r2 = max(length(q2), 1e-3);
  float a2 = atan(q2.y, q2.x) / TAU;
  float l2 = log(r2);
  vec2 uvS = vec2(a2 * 2.0 - l2 * 0.55 + t * 0.06, l2 * 0.6 - t * 0.22);
  float lodS = polarLod(pix, (2.0 / TAU + 0.6) / r2);
  vec4 w1 = textureLod(uNoise, uvS * vec2(1.0, 0.7) + vec2(0.13, 0.4), lodS);
  vec4 w2 = textureLod(uNoise, uvS + (w1.gb - 0.5) * 0.45 + vec2(0.21, 0.63), lodS);
  // Two broad spiral arms give the swirl large-scale structure and dark gaps between the billows.
  float arms = smoothstep(-0.4, 0.9, sin(TAU * (a2 * 2.0 - l2 * 0.75) + t * 0.9 + (w1.r - 0.5) * 2.0));
  float swirl = smoothstep(0.25, 0.95, w2.r) * (0.35 + 0.65 * arms);
  float swirlMask = smoothstep(0.1, 0.62, r2);

  // Near strands 8 cm deep: a few thin bright filaments, fastest, brightest towards the rim.
  vec2 q3 = q + par * 0.08;
  float r3 = max(length(q3), 1e-3);
  float a3 = atan(q3.y, q3.x) / TAU;
  float l3 = log(r3);
  vec2 uvN = vec2(a3 * 3.0 - l3 * 0.9 + t * 0.1, l3 * 0.9 - t * 0.45);
  float lodN = polarLod(pix, (3.0 / TAU + 0.9) / r3);
  vec4 s1 = textureLod(uNoise, uvN * vec2(1.0, 0.8), lodN);
  float strand = textureLod(uNoise, uvN * vec2(0.5, 1.4) + (s1.gb - 0.5) * 0.25 + vec2(0.71, 0.29), lodN).a;
  strand = pow(strand, 5.0) * smoothstep(0.35, 0.75, s1.r);
  float strandMask = smoothstep(0.3, 0.9, r3);

  // Rim: a thin hot edge that flickers in width, with a glow bleeding a little way in.
  float rimN = textureLod(uNoise, vec2(angle / TAU * 5.0 - t * 0.11, t * 0.06), 0.0).r;
  float rimD = textureLod(uNoise, vec2(angle / TAU * 9.0 + t * 0.23, 0.5 + t * 0.09), 0.0).a;
  float rimW = 0.011 + 0.012 * rimN;
  float rim = exp(-pow((r - 0.978) / rimW, 2.0));
  float inner = pow(smoothstep(0.55, 0.99, r), 3.0);

  vec3 glow = mix(accent, hot, 0.35);
  vec3 col = deep * 0.06;
  col = mix(col, place, throat);
  col += mix(deep * 0.8, accent * 0.8, nT.r) * tunnel * depthFade * 0.45;
  col += (accent * 0.75 * swirl + glow * 1.1 * swirl * swirl * swirl) * swirlMask;
  col += glow * 4.0 * strand * strandMask;
  col += accent * inner * (0.35 + 1.1 * rimN);
  col += hot * rim * (4.5 + 4.0 * rimD);
  col *= uIntensity;

  float alpha = smoothstep(1.0, 0.985, r) * 0.97;
  gl_FragColor = vec4(col, alpha);
  #include <tonemapping_fragment>
  #include <colorspace_fragment>
  #ifdef USE_FOG
  #ifdef FOG_EXP2
  float fogAmount = 1.0 - exp(-fogDensity * fogDensity * vFogDepth * vFogDepth);
  #else
  float fogAmount = smoothstep(fogNear, fogFar, vFogDepth);
  #endif
  // Premultiplied: the glow fades out, the opaque part turns fog-coloured.
  gl_FragColor.rgb = gl_FragColor.rgb * (1.0 - fogAmount) + fogColor * fogAmount * gl_FragColor.a;
  #endif
}
`;

/** The Blender base colour's hue as a tint: white, grey and black leave the colour field as it is. */
function baseTint(material: Material): Vector3 {
  const color = (material as MeshStandardMaterial).color;
  const top = color ? Math.max(color.r, color.g, color.b) : 0;
  if (!color || top <= 1e-4) return new Vector3(1, 1, 1);
  const saturation = (top - Math.min(color.r, color.g, color.b)) / top;
  return new Vector3(1, 1, 1).lerp(new Vector3(color.r / top, color.g / top, color.b / top), saturation);
}

export const apply: ShaderApply<F> = (material, _values, ctx) => {
  const u = ctx.uniforms;
  const portal = new ShaderMaterial({
    name: material.name,
    uniforms: {
      ...UniformsUtils.clone(UniformsLib.fog),
      uTime: ctx.time,
      uNoise: { value: noiseTexture() },
      uColor: u.color,
      uDestination: u.destination,
      uTint: { value: baseTint(material) },
      uOpen: u.open,
      uSpeed: u.speed,
      uIntensity: u.intensity,
    },
    vertexShader: VERTEX,
    fragmentShader: FRAGMENT,
    side: DoubleSide,
    transparent: true,
    // It writes depth so things behind the throat vanish into it.
    depthWrite: true,
    fog: true,
    blending: CustomBlending,
    blendEquation: AddEquation,
    blendSrc: OneFactor,
    blendDst: OneMinusSrcAlphaFactor,
    blendSrcAlpha: OneFactor,
    blendDstAlpha: OneMinusSrcAlphaFactor,
  });
  portal.onBeforeRender = (_renderer, _scene, _camera, _geometry, object) => {
    object.castShadow = false;
  };
  return portal;
};

export const mesh: ShaderMesh<F> = (target) => {
  target.castShadow = false;
};
