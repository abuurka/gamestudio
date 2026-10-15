// After FX Lab "hologram" (scanlines, fresnel, flicker, glitch slices, materialise sweep) and
// "holo-war-table" (slices shifted in the vertex shader).
import {
  AddEquation,
  Box3,
  CustomBlending,
  FrontSide,
  OneFactor,
  ShaderMaterial,
  UniformsLib,
  UniformsUtils,
  Vector3,
  type BufferGeometry,
  type Material,
  type MeshStandardMaterial,
} from 'three';
import type { ShaderApply, ShaderMesh } from '../../../schema/shaders';
import type { fields } from './meta';

type F = typeof fields;

/**
 * Deterministic glitch timing shared by both stages: strength (x) and the slice seed of this step (y).
 * hash11, hash12 and hash13 are David Hoskins' "Hash without Sine" (https://www.shadertoy.com/view/4djSRW,
 * MIT; THIRD-PARTY-NOTICES.md).
 */
const GLITCH = /* glsl */ `
uniform float uTime;
uniform float uGlitch;
uniform float uGlitchAt;
uniform vec3 uMin;
uniform vec3 uMax;
float hash11(float p) { p = fract(p * 0.1031); p *= p + 33.33; p *= p + p; return fract(p); }
float hash12(vec2 p) { vec3 p3 = fract(vec3(p.xyx) * 0.1031); p3 += dot(p3, p3.yzx + 33.33); return fract((p3.x + p3.y) * p3.z); }
float hash13(vec3 p3) { p3 = fract(p3 * 0.1031); p3 += dot(p3, p3.zyx + 31.32); return fract((p3.x + p3.y) * p3.z); }
float wobble(float x, float seed) { float i = floor(x); float f = fract(x); f = f * f * (3.0 - 2.0 * f); return mix(hash11(i + seed * 17.0), hash11(i + 1.0 + seed * 17.0), f) * 2.0 - 1.0; }
/** x: glitch strength (0.02 calm, up to 1.5 in a triggered burst), y: seed of the current 65 ms step. */
vec2 holoGlitch() {
  float t = uTime;
  // A burst may start in each 3.2 s window ('glitch' is the chance), lasting 0.18-0.45 s.
  float seg = floor(t / 3.2);
  float start = seg * 3.2 + hash11(seg * 7.31 + 0.5) * 2.6;
  float len = 0.18 + 0.27 * hash11(seg * 3.17 + 1.3);
  float on = step(start, t) * step(t, start + len) * step(hash11(seg * 1.93 + 4.1), clamp(uGlitch, 0.0, 1.0));
  float peak = mix(0.5, 0.9, hash11(seg * 5.7 + 2.9));
  // A triggered burst (glitchAt) decays over about 0.7 s.
  float since = t - uGlitchAt;
  float burst = since >= 0.0 ? max(0.0, 1.0 - since * 1.4) : 0.0;
  float strength = max(max(on * peak, 0.02 * step(0.001, uGlitch)), burst * 1.5);
  return vec2(strength, floor(t / 0.065));
}
/** Height in the mesh's bounds, 0 at the bottom and 1 at the top. */
float holoHeight(vec3 p) { return (p.y - uMin.y) / max(uMax.y - uMin.y, 1e-4); }
/** 0 normal, 1 dropped, 2 shifted right, 3 shifted left. */
float sliceState(float y, vec2 glitch) {
  float bands = mix(14.0, 50.0, hash11(glitch.y * 3.1 + 0.2));
  float id = floor(y * bands + hash11(glitch.y + 0.7) * 3.0);
  float h = hash12(vec2(id * 1.37, glitch.y * 17.3 + 0.4));
  float p = clamp(glitch.x, 0.0, 1.0) * 0.5;
  if (h >= p) return 0.0;
  float k = h / max(p, 1e-4);
  return k < 0.3 ? 1.0 : (k < 0.65 ? 2.0 : 3.0);
}
`;

const VERTEX = /* glsl */ `
#include <common>
#include <batching_pars_vertex>
#include <morphtarget_pars_vertex>
#include <skinning_pars_vertex>
#include <fog_pars_vertex>
#include <logdepthbuf_pars_vertex>
#include <clipping_planes_pars_vertex>
${GLITCH}
varying vec3 vWorld;
varying vec3 vNormalW;
varying float vHeight;
varying float vViewZ;
void main() {
  #include <morphinstance_vertex>
  #include <batching_vertex>
  #include <beginnormal_vertex>
  #include <morphnormal_vertex>
  #include <skinbase_vertex>
  #include <skinnormal_vertex>
  #include <defaultnormal_vertex>
  #include <begin_vertex>
  #include <morphtarget_vertex>
  #include <skinning_vertex>
  #include <project_vertex>
  #include <logdepthbuf_vertex>
  #include <clipping_planes_vertex>
  vHeight = holoHeight(position);
  // Glitched slices jump sideways on screen, a few centimetres each way.
  vec2 glitch = holoGlitch();
  float state = sliceState(vHeight, glitch);
  float shift = 0.0;
  if (state > 2.5) shift = -(0.006 + glitch.x * mix(0.02, 0.13, hash11(glitch.y * 1.7 + 0.3)));
  else if (state > 1.5) shift = 0.006 + glitch.x * mix(0.02, 0.13, hash11(glitch.y * 2.3 + 0.9));
  mvPosition.x += shift;
  gl_Position = projectionMatrix * mvPosition;
  vec4 world = vec4(transformed, 1.0);
  #ifdef USE_BATCHING
  world = batchingMatrix * world;
  #endif
  #ifdef USE_INSTANCING
  world = instanceMatrix * world;
  #endif
  vWorld = (modelMatrix * world).xyz;
  vNormalW = inverseTransformDirection(transformedNormal, viewMatrix);
  vViewZ = -mvPosition.z;
  #include <fog_vertex>
}
`;

const FRAGMENT = /* glsl */ `
#include <common>
#include <fog_pars_fragment>
#include <logdepthbuf_pars_fragment>
#include <clipping_planes_pars_fragment>
${GLITCH}
uniform vec4 uColor;
uniform vec3 uTint;
uniform float uIntensity;
uniform float uScanlines;
uniform float uFlicker;
uniform float uReveal;
varying vec3 vWorld;
varying vec3 vNormalW;
varying float vHeight;
varying float vViewZ;

float valueNoise(vec2 p) {
  vec2 i = floor(p), f = fract(p);
  f = f * f * (3.0 - 2.0 * f);
  return mix(mix(hash12(i), hash12(i + vec2(1.0, 0.0)), f.x), mix(hash12(i + vec2(0.0, 1.0)), hash12(i + vec2(1.0, 1.0)), f.x), f.y);
}
// Scanlines locked to world height, with every other line swapping brightness each frame (interlace).
float scan(float y, float fade) {
  float s = 0.5 + 0.5 * sin(y * uScanlines * 6.2831853);
  float row = floor(y * uScanlines + 0.25);
  float inter = mod(row + floor(uTime * 30.0), 2.0);
  // Lines closer together than a couple of pixels turn into their average instead of shimmering.
  return mix(0.3 + 0.7 * s * s * (0.62 + 0.38 * inter), 0.51, fade);
}

void main() {
  #include <clipping_planes_fragment>
  #include <logdepthbuf_fragment>
  vec2 glitch = holoGlitch();
  // Per pixel, so slice edges stay crisp on coarse meshes (the vertex shader moved the shifted ones).
  float state = sliceState(vHeight, glitch);
  if (abs(state - 1.0) < 0.5) discard;

  // Materialise: a jagged bright line sweeps up the mesh; above it nothing is shown yet.
  float reveal = clamp(uReveal, 0.0, 1.0);
  float jag = (valueNoise(vec2(vWorld.x * 9.0 + vWorld.z * 7.0, uTime * 2.0)) - 0.5) * 0.035;
  float d = vHeight - (reveal * 1.1 - 0.05 + jag);
  float shown = 1.0 - smoothstep(-0.003, 0.003, d);
  float sweeping = step(0.001, reveal) * step(reveal, 0.999);
  float line = exp(-abs(d) * 100.0) * sweeping;
  if (shown <= 0.0 && line < 0.01) discard;

  vec3 V = normalize(cameraPosition - vWorld);
  float ndv = abs(dot(normalize(vNormalW), V));
  float fres = pow(clamp(1.0 - ndv, 0.0, 1.0), 2.4);
  // Chromatic fringe: each channel reads the scanlines a little apart, more while glitching.
  float co = 0.0035 + clamp(glitch.x, 0.0, 1.5) * 0.012;
  float fade = smoothstep(0.35, 0.7, fwidth(vWorld.y * uScanlines));
  vec3 lines = vec3(scan(vWorld.y + co, fade), scan(vWorld.y, fade), scan(vWorld.y - co, fade));
  float roll = fract(vWorld.y * 0.55 - uTime * 0.42);
  float band = exp(-roll * 10.0) * 0.9 + exp(-fract(vWorld.y * 3.1 + uTime * 1.3) * 25.0) * 0.12;
  vec3 color = uColor.rgb * uTint * 1.6;
  vec3 holo = color * lines * (0.13 + 2.3 * fres) + color * band * (0.25 + fres);
  float sparkle = step(0.988 - clamp(glitch.x, 0.0, 1.0) * 0.03, hash13(floor(vWorld * 75.0) + vec3(floor(uTime * 20.0) * 17.31)));
  holo += mix(color, vec3(1.0), 0.5) * sparkle * 2.5;
  // Shifted slices carry a red or blue fringe.
  if (state > 2.5) holo *= vec3(0.55, 0.75, 1.6);
  else if (state > 1.5) holo *= vec3(1.6, 0.6, 0.75);
  float jitter = glitch.x > 0.3 ? mix(0.45, 1.25, hash11(glitch.y * 9.1 + 0.37)) : 1.0;
  float flicker = (1.0 + uFlicker * (0.06 * wobble(uTime * 11.0, 1.0) + 0.04 * wobble(uTime * 23.0, 2.0) - 0.1)) * jitter;
  vec3 rgb = holo * shown * flicker * uIntensity;
  rgb += mix(color, vec3(1.0), 0.55) * line * 7.0 * uIntensity;
  gl_FragColor = vec4(rgb, 0.0);
  #include <tonemapping_fragment>
  #include <colorspace_fragment>
  #ifdef USE_FOG
  #ifdef FOG_EXP2
  float fogAmount = 1.0 - exp(-fogDensity * fogDensity * vFogDepth * vFogDepth);
  #else
  float fogAmount = smoothstep(fogNear, fogFar, vFogDepth);
  #endif
  // Additive light fades into the fog instead of turning fog-coloured.
  gl_FragColor.rgb *= 1.0 - fogAmount;
  #endif
}
`;

/** Bounds of what a hologram material draws (rest pose, local space): the sweep and the slices follow them. */
interface Bounds {
  readonly min: Vector3;
  readonly max: Vector3;
  readonly seen: WeakSet<BufferGeometry>;
}

function include(bounds: Bounds, geometry: BufferGeometry | undefined): void {
  if (!geometry || bounds.seen.has(geometry)) return;
  bounds.seen.add(geometry);
  if (!geometry.boundingBox) geometry.computeBoundingBox();
  const box: Box3 | null = geometry.boundingBox;
  if (!box || box.isEmpty()) return;
  bounds.min.min(box.min);
  bounds.max.max(box.max);
}

const boundsOf = new WeakMap<object, Bounds>();

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
  const bounds: Bounds = { min: new Vector3(Infinity, Infinity, Infinity), max: new Vector3(-Infinity, -Infinity, -Infinity), seen: new WeakSet() };
  const hologram = new ShaderMaterial({
    name: material.name,
    uniforms: {
      ...UniformsUtils.clone(UniformsLib.fog),
      uTime: ctx.time,
      uColor: u.color,
      uTint: { value: baseTint(material) },
      uIntensity: u.intensity,
      uScanlines: u.scanlines,
      uFlicker: u.flicker,
      uGlitch: u.glitch,
      uGlitchAt: u.glitchAt,
      uReveal: u.reveal,
      uMin: { value: bounds.min },
      uMax: { value: bounds.max },
    },
    vertexShader: VERTEX,
    fragmentShader: FRAGMENT,
    side: FrontSide,
    transparent: true,
    depthWrite: false,
    fog: true,
    blending: CustomBlending,
    blendEquation: AddEquation,
    blendSrc: OneFactor,
    blendDst: OneFactor,
    blendSrcAlpha: OneFactor,
    blendDstAlpha: OneFactor,
  });
  boundsOf.set(hologram, bounds);
  // Meshes copied later (prefab spawns) share this material but not mesh().
  hologram.onBeforeRender = (_renderer, _scene, _camera, geometry, object) => {
    include(bounds, geometry);
    object.castShadow = false;
  };
  return hologram;
};

export const mesh: ShaderMesh<F> = (target) => {
  target.castShadow = false;
  const materials = Array.isArray(target.material) ? target.material : [target.material];
  for (const material of materials) {
    const bounds = boundsOf.get(material);
    if (bounds) include(bounds, target.geometry);
  }
};
