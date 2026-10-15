// After FX Lab "spawn-despawn" (sweeping cut, glowing band), "phase-shift" (white-hot hairline, dissolving
// shadow) and "burning-paper" (scorch ahead of the edge).
import { Box3, MeshDepthMaterial, MeshDistanceMaterial, Vector3 } from 'three';
import type { BufferGeometry, Material, MeshStandardMaterial, Object3D, WebGLProgramParametersWithUniforms } from 'three';
import { patchShader, type ShaderApply, type ShaderMesh } from '../../../schema/shaders';
import type { fields } from './meta';

type F = typeof fields;
type Uniforms = Record<string, { value: unknown }>;

/**
 * 3D simplex noise by Ian McEwan and Stefan Gustavson (Ashima Arts), https://github.com/stegu/webgl-noise,
 * MIT (THIRD-PARTY-NOTICES.md); prefixed so it never meets another patch's copy.
 */
const NOISE = /* glsl */ `
vec3 gsDisMod289(vec3 x) { return x - floor(x * (1.0 / 289.0)) * 289.0; }
vec4 gsDisMod289(vec4 x) { return x - floor(x * (1.0 / 289.0)) * 289.0; }
vec4 gsDisPermute(vec4 x) { return gsDisMod289(((x * 34.0) + 10.0) * x); }
float gsDisSnoise(vec3 v) {
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
  i = gsDisMod289(i);
  vec4 p = gsDisPermute(gsDisPermute(gsDisPermute(i.z + vec4(0.0, i1.z, i2.z, 1.0)) + i.y + vec4(0.0, i1.y, i2.y, 1.0)) + i.x + vec4(0.0, i1.x, i2.x, 1.0));
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
}
`;

const VERTEX_DECLARATIONS = /* glsl */ `
varying vec3 vGsDisPos;
varying vec3 vGsDisSeed;
`;

// The rest pose (before morphs and skinning), so the pattern sticks to an animated body; GPU
// instances each get their own pattern.
const VERTEX = /* glsl */ `
vGsDisPos = position;
vGsDisSeed = vec3(0.0);
#ifdef USE_INSTANCING
vGsDisSeed = instanceMatrix[3].xyz * 0.731;
#endif
`;

/** The cut, shared by the visible material and the shadow materials so the shadow dissolves with the body. */
const CUT_DECLARATIONS = /* glsl */ `
uniform float gsDisTime;
uniform float gsDisProgress;
uniform float gsDisStartAt;
uniform float gsDisDuration;
uniform bool gsDisReverse;
uniform vec3 gsDisDirection;
uniform float gsDisNoise;
uniform float gsDisScale;
uniform float gsDisWidth;
uniform vec3 gsDisMin;
uniform vec3 gsDisMax;
varying vec3 vGsDisPos;
varying vec3 vGsDisSeed;
${NOISE}
/** How far the dissolve has gone: the larger of progress and the timed run. */
float gsDisAmount() {
  float timed = 0.0;
  if (gsDisStartAt >= 0.0) {
    timed = clamp((gsDisTime - gsDisStartAt) / max(gsDisDuration, 1e-3), 0.0, 1.0);
    if (gsDisReverse) timed = 1.0 - timed;
  }
  return max(clamp(gsDisProgress, 0.0, 1.0), timed);
}
/** Distance above the cut in field units: below 0 is gone, 0..width glows. Large while nothing dissolves. */
float gsDisMargin() {
  float amount = gsDisAmount();
  if (amount <= 0.0) return 1e3;
  if (amount >= 1.0) return -1.0;
  // Positions relative to the mesh's bounds, so the holes and the sweep fit any size and unit.
  vec3 size = max(gsDisMax - gsDisMin, vec3(1e-4));
  vec3 q = (vGsDisPos - gsDisMin) / length(size) * gsDisScale + vGsDisSeed;
  float n = gsDisSnoise(q) * 0.65 + gsDisSnoise(q * 3.1 + 7.3) * 0.35;
  float field = clamp((n * 0.5 + 0.5 - 0.2) / 0.6, 0.0, 1.0);
  float dl = length(gsDisDirection);
  if (dl > 1e-4) {
    vec3 d = gsDisDirection / dl;
    float a = dot(mix(gsDisMax, gsDisMin, step(0.0, d)), d);
    float b = dot(mix(gsDisMin, gsDisMax, step(0.0, d)), d);
    float sweep = clamp((dot(vGsDisPos, d) - a) / max(b - a, 1e-4), 0.0, 1.0);
    field = mix(sweep, field, clamp(gsDisNoise, 0.0, 1.0));
  }
  float w = max(gsDisWidth, 1e-3);
  // At 0 the whole scorch band sits below the field; at 1 the cut is above all of it.
  return field - mix(-w * 2.5 - 0.01, 1.0, amount);
}
`;

const CUT = /* glsl */ `
float gsDisE = gsDisMargin();
if (gsDisE < 0.0) discard;
`;

const LOOK_DECLARATIONS = /* glsl */ `
uniform vec4 gsDisEdgeColor;
uniform float gsDisGlow;
`;

// Scorched just inside the edge (burning paper), then the glowing band and a white-hot hairline.
const COLOR = /* glsl */ `
float gsDisW = max(gsDisWidth, 1e-3);
diffuseColor.rgb *= mix(0.05, 1.0, smoothstep(gsDisW * 0.5, gsDisW * 2.5, gsDisE));
`;

const EMISSIVE = /* glsl */ `
float gsDisBand = 1.0 - smoothstep(0.0, gsDisW, gsDisE);
float gsDisCore = 1.0 - smoothstep(0.0, gsDisW * 0.3, gsDisE);
totalEmissiveRadiance += (gsDisEdgeColor.rgb * gsDisBand * gsDisBand * 3.0 + mix(gsDisEdgeColor.rgb, vec3(1.0), 0.65) * gsDisCore * 6.0) * gsDisGlow;
`;

function editCut(shader: WebGLProgramParametersWithUniforms, uniforms: Uniforms): void {
  Object.assign(shader.uniforms, uniforms);
  shader.vertexShader = shader.vertexShader
    .replace('#include <common>', `#include <common>\n${VERTEX_DECLARATIONS}`)
    .replace('#include <begin_vertex>', `#include <begin_vertex>\n${VERTEX}`);
  shader.fragmentShader = shader.fragmentShader
    .replace('#include <common>', `#include <common>\n${CUT_DECLARATIONS}`)
    .replace('#include <clipping_planes_fragment>', `#include <clipping_planes_fragment>\n${CUT}`);
}

/** Per material instance: its uniforms, the shadow materials that share them, and the bounds of what it draws. */
interface State {
  readonly min: Vector3;
  readonly max: Vector3;
  readonly seen: WeakSet<BufferGeometry>;
  readonly depth: MeshDepthMaterial;
  readonly distance: MeshDistanceMaterial;
}

const box = new Box3();

/** Grows the bounds by a geometry's (rest pose, local space) box. */
function include(state: State, geometry: BufferGeometry | undefined): void {
  if (!geometry || state.seen.has(geometry)) return;
  state.seen.add(geometry);
  if (!geometry.boundingBox) geometry.computeBoundingBox();
  if (!geometry.boundingBox || geometry.boundingBox.isEmpty()) return;
  box.copy(geometry.boundingBox);
  state.min.min(box.min);
  state.max.max(box.max);
}

/** Puts the dissolving shadow materials on a mesh. */
function attach(state: State, object: Object3D): void {
  if (object.customDepthMaterial !== state.depth) object.customDepthMaterial = state.depth;
  if (object.customDistanceMaterial !== state.distance) object.customDistanceMaterial = state.distance;
}

const states = new WeakMap<Material, State>();

export const apply: ShaderApply<F> = (material, _values, ctx) => {
  if (!(material as MeshStandardMaterial).isMeshStandardMaterial) {
    throw new Error('Dissolve needs a Principled BSDF material (MeshStandardMaterial in the game)');
  }
  const u = ctx.uniforms;
  const min = new Vector3(Infinity, Infinity, Infinity);
  const max = new Vector3(-Infinity, -Infinity, -Infinity);
  const cut: Uniforms = {
    gsDisTime: ctx.time,
    gsDisProgress: u.progress,
    gsDisStartAt: u.startAt,
    gsDisDuration: u.duration,
    gsDisReverse: u.reverse,
    gsDisDirection: u.direction,
    gsDisNoise: u.noise,
    gsDisScale: u.noiseScale,
    gsDisWidth: u.edgeWidth,
    gsDisMin: { value: min },
    gsDisMax: { value: max },
  };
  const look: Uniforms = { gsDisEdgeColor: u.edgeColor, gsDisGlow: u.glow };

  patchShader(material, 'gs-dissolve', (shader) => {
    editCut(shader, cut);
    Object.assign(shader.uniforms, look);
    shader.fragmentShader = shader.fragmentShader
      .replace('#include <common>', `#include <common>\n${LOOK_DECLARATIONS}`)
      .replace('#include <color_fragment>', `#include <color_fragment>\n${COLOR}`)
      .replace('#include <emissivemap_fragment>', `#include <emissivemap_fragment>\n${EMISSIVE}`);
  });

  // The shadow casters discard the same pixels (directional/spot and point-light shadows).
  const depth = new MeshDepthMaterial();
  patchShader(depth, 'gs-dissolve-depth', (shader) => editCut(shader, cut));
  const distance = new MeshDistanceMaterial();
  patchShader(distance, 'gs-dissolve-distance', (shader) => editCut(shader, cut));

  const state: State = { min, max, seen: new WeakSet(), depth, distance };
  states.set(material, state);
  // Meshes copied later (prefab spawns) share this material but not mesh(): they pick up the
  // shadow materials and their bounds when they first draw.
  material.onBeforeRender = (_renderer, _scene, _camera, geometry, object) => {
    include(state, geometry);
    attach(state, object);
  };
  material.addEventListener('dispose', () => {
    depth.dispose();
    distance.dispose();
  });
};

export const mesh: ShaderMesh<F> = (target) => {
  const materials = Array.isArray(target.material) ? target.material : [target.material];
  for (const material of materials) {
    const state = states.get(material);
    if (!state) continue;
    include(state, target.geometry);
    attach(state, target);
  }
};
