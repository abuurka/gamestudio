// After FX Lab "barrier" (geodesic hex lattice, fresnel, hit ripples) and "hardlight-bridge" (flat hex deck).
import {
  AddEquation,
  CustomBlending,
  DoubleSide,
  Matrix3,
  OneFactor,
  ShaderMaterial,
  UniformsLib,
  UniformsUtils,
  Vector3,
  Vector4,
  type Material,
  type MeshStandardMaterial,
} from 'three';
import type { ShaderApply, ShaderMesh } from '../../../schema/shaders';
import type { fields } from './meta';

type F = typeof fields;

/** Hits remembered at once: each `hitAt` change starts a ripple in the next slot. */
const HITS = 4;
/** Angle between neighbouring icosahedron corners: one geodesic edge. */
const EDGE_ANGLE = Math.acos(1 / Math.sqrt(5));

/**
 * Frames of an icosahedron with a vertex at the pole, for the hex lattice. Each spherical face gets
 * angular barycentric coordinates, so cells keep an even size across the face, and with the corners
 * on hex centres the lattice mirrors cleanly across every edge (a geodesic sphere, 12 pentagons).
 */
function icosahedronFrames() {
  const lat = Math.atan(0.5);
  const tau = Math.PI * 2;
  const vertices = [new Vector3(0, 1, 0)];
  for (let k = 0; k < 5; k++) { const a = (k / 5) * tau; vertices.push(new Vector3(Math.cos(a) * Math.cos(lat), Math.sin(lat), Math.sin(a) * Math.cos(lat))); }
  for (let k = 0; k < 5; k++) { const a = ((k + 0.5) / 5) * tau; vertices.push(new Vector3(Math.cos(a) * Math.cos(lat), -Math.sin(lat), Math.sin(a) * Math.cos(lat))); }
  vertices.push(new Vector3(0, -1, 0));
  const faces: [number, number, number][] = [];
  for (let k = 0; k < 5; k++) {
    const u0 = 1 + k, u1 = 1 + ((k + 1) % 5), l0 = 6 + k, l1 = 6 + ((k + 1) % 5);
    faces.push([0, u0, u1], [u0, l0, u1], [u1, l0, l1], [11, l0, l1]);
  }
  const normals: Vector3[] = [], edges: Matrix3[] = [], corners: Matrix3[] = [];
  const inward = (p: Vector3, q: Vector3, opposite: Vector3) => { const n = new Vector3().crossVectors(p, q).normalize(); return n.dot(opposite) < 0 ? n.negate() : n; };
  let height = 1;
  for (const [a, b, c] of faces) {
    const A = vertices[a]!, B = vertices[b]!, C = vertices[c]!;
    normals.push(new Vector3().add(A).add(B).add(C).normalize());
    const ea = inward(B, C, A), eb = inward(C, A, B), ec = inward(A, B, C);
    height = Math.asin(A.dot(ea));
    // Columns, so the shader gets all three edge distances with one `n * m`.
    edges.push(new Matrix3().set(ea.x, eb.x, ec.x, ea.y, eb.y, ec.y, ea.z, eb.z, ec.z));
    corners.push(new Matrix3().set(A.x, B.x, C.x, A.y, B.y, C.y, A.z, B.z, C.z));
  }
  return { normals, edges, corners, height };
}

let frames: ReturnType<typeof icosahedronFrames> | null = null;

const VERTEX = /* glsl */ `
#include <common>
#include <batching_pars_vertex>
#include <morphtarget_pars_vertex>
#include <skinning_pars_vertex>
#include <fog_pars_vertex>
#include <logdepthbuf_pars_vertex>
#include <clipping_planes_pars_vertex>
varying vec3 vObj;
varying vec3 vMesh;
varying vec3 vMeshNormal;
varying vec3 vWorld;
varying vec3 vNormalW;
varying float vViewZ;
varying float vScale;
varying float vRadius;
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
  vObj = position;
  vec4 local = vec4(transformed, 1.0);
  vec3 meshNormal = objectNormal;
  float instanceScale = 1.0;
  #ifdef USE_BATCHING
  local = batchingMatrix * local;
  meshNormal = mat3(batchingMatrix) * meshNormal;
  #endif
  #ifdef USE_INSTANCING
  local = instanceMatrix * local;
  meshNormal = mat3(instanceMatrix) * meshNormal;
  instanceScale = length(instanceMatrix[0].xyz);
  #endif
  vMesh = local.xyz;
  vMeshNormal = meshNormal;
  vWorld = (modelMatrix * local).xyz;
  vNormalW = inverseTransformDirection(transformedNormal, viewMatrix);
  vViewZ = -mvPosition.z;
  vScale = length(modelMatrix[0].xyz);
  // Constant over a sphere's vertices, so the lattice density does not change across triangles.
  vRadius = length(position) * vScale * instanceScale;
  #include <fog_vertex>
}
`;

// hash11 and hash13 are David Hoskins' "Hash without Sine" (https://www.shadertoy.com/view/4djSRW, MIT);
// mod289, permute and snoise are the 3D simplex noise by Ian McEwan and Stefan Gustavson (Ashima Arts),
// https://github.com/stegu/webgl-noise, MIT. Both notices: THIRD-PARTY-NOTICES.md.
const FRAGMENT = /* glsl */ `
#include <common>
#include <fog_pars_fragment>
#include <logdepthbuf_pars_fragment>
#include <clipping_planes_pars_fragment>
uniform float uTime;
uniform vec4 uColor;
uniform vec3 uTint;
uniform float uIntensity;
uniform float uCellSize;
uniform float uCharge;
uniform float uRippleSize;
uniform vec4 uHits[${HITS}];
#ifdef GS_SHIELD_SPHERE
uniform vec3 uFaceN[20];
uniform mat3 uFaceEdges[20];
uniform mat3 uFaceCorners[20];
uniform float uEdgeHeight;
#endif
varying vec3 vObj;
varying vec3 vMesh;
varying vec3 vMeshNormal;
varying vec3 vWorld;
varying vec3 vNormalW;
varying float vViewZ;
varying float vScale;
varying float vRadius;

float hash11(float p) { p = fract(p * 0.1031); p *= p + 33.33; p *= p + p; return fract(p); }
float hash13(vec3 p3) { p3 = fract(p3 * 0.1031); p3 += dot(p3, p3.zyx + 31.32); return fract((p3.x + p3.y) * p3.z); }
float wobble(float x, float seed) { float i = floor(x); float f = fract(x); f = f * f * (3.0 - 2.0 * f); return mix(hash11(i + seed * 17.0), hash11(i + 1.0 + seed * 17.0), f) * 2.0 - 1.0; }
vec3 mod289(vec3 x) { return x - floor(x * (1.0 / 289.0)) * 289.0; }
vec4 mod289(vec4 x) { return x - floor(x * (1.0 / 289.0)) * 289.0; }
vec4 permute(vec4 x) { return mod289(((x * 34.0) + 10.0) * x); }
float snoise(vec3 v) {
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
  i = mod289(i);
  vec4 p = permute(permute(permute(i.z + vec4(0.0, i1.z, i2.z, 1.0)) + i.y + vec4(0.0, i1.y, i2.y, 1.0)) + i.x + vec4(0.0, i1.x, i2.x, 1.0));
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
/** Hex distance (0 centre, 0.5 edge) and cell centre for a point p in cell units (hexes with unit spacing). */
float hexCell(vec2 p, out vec2 cell) {
  const vec2 s = vec2(1.0, 1.7320508);
  vec4 c = floor(vec4(p, p - vec2(0.5, 0.8660254)) / s.xyxy) + 0.5;
  vec4 h = vec4(p - c.xy * s, p - (c.zw + 0.5) * s);
  vec2 local;
  if (dot(h.xy, h.xy) < dot(h.zw, h.zw)) { local = h.xy; cell = c.xy * s; } else { local = h.zw; cell = (c.zw + 0.5) * s; }
  local = abs(local);
  return max(local.x, dot(local, vec2(0.5, 0.8660254)));
}
#ifdef GS_SHIELD_SPHERE
/** Geodesic lattice: hex distance and the cell centre as a unit direction, identical on both sides of a face seam. */
float hexLattice(vec3 n, float cells, out vec3 centre) {
  int f = 0;
  float best = -2.0;
  for (int i = 0; i < 20; i++) { float d = dot(n, uFaceN[i]); if (d > best) { best = d; f = i; } }
  vec3 bary = asin(clamp(n * uFaceEdges[f], -1.0, 1.0)) / uEdgeHeight;
  bary /= bary.x + bary.y + bary.z;
  // Corner A at the origin, B at (N, 0), C at (N/2, N·√3/2): all three sit on hex centres.
  vec2 p = cells * vec2(bary.y + bary.z * 0.5, bary.z * 0.8660254);
  vec2 cell;
  float d = hexCell(p, cell);
  float tc = cell.y / (cells * 0.8660254), tb = cell.x / cells - tc * 0.5;
  centre = normalize(uFaceCorners[f] * vec3(1.0 - tb - tc, tb, tc));
  return d;
}
#else
/** Flat lattice on the plane the surface faces most (box projection), in metres. */
float hexLattice(vec3 p, vec3 normal, float size, out vec3 centre) {
  vec3 a = abs(normal);
  vec2 cell;
  float d;
  if (a.y >= a.x && a.y >= a.z) { d = hexCell(p.xz / size, cell); centre = vec3(cell.x * size, p.y, cell.y * size); }
  else if (a.x >= a.z) { d = hexCell(p.zy / size, cell); centre = vec3(p.x, cell.y * size, cell.x * size); }
  else { d = hexCell(p.xy / size, cell); centre = vec3(cell.x * size, cell.y * size, p.z); }
  return d;
}
#endif
/** Ripple front, in the FX barrier's units (the full reach is 2.1), 'age' seconds after its hit: fast, then settling. */
float rippleFront(float age) { return 2.1 * (1.0 - exp(-age * 2.3)); }

void main() {
  #include <clipping_planes_fragment>
  #include <logdepthbuf_fragment>
  vec3 N = normalize(vNormalW);
  vec3 V = normalize(cameraPosition - vWorld);
  float ndv = clamp(abs(dot(N, V)), 0.0, 1.0);
  float fresnel = pow(1.0 - ndv, 2.5);
  float rim = pow(1.0 - ndv, 8.0);
  float front = gl_FrontFacing ? 1.0 : 0.0;
  float t = uTime;
  float size = max(uCellSize, 0.01);
  // Ripple distances are scaled so 'rippleSize' metres maps to the barrier's reach.
  float toRipple = 2.1 / max(uRippleSize, 0.01);

  vec3 centre, nn, cn;
  float hd, aa;
  #ifdef GS_SHIELD_SPHERE
  vec3 n = normalize(vObj);
  float radius = max(vRadius, 1e-3);
  float cells = max(1.0, floor(${EDGE_ANGLE.toFixed(7)} * radius / size + 0.5));
  hd = hexLattice(n, cells, centre);
  // Anti-alias from the continuous position: derivatives of the lattice coordinates jump at face seams.
  aa = length(fwidth(n)) * cells / ${EDGE_ANGLE.toFixed(7)};
  nn = n;
  cn = centre;
  #else
  vec3 pm = vMesh * vScale;
  hd = hexLattice(pm, normalize(vMeshNormal), size, centre);
  aa = length(fwidth(pm)) / size;
  // The barrier's noise was laid out on a 3.2 m dome.
  nn = pm / 3.2;
  cn = centre / 3.2;
  #endif
  float edge = 0.5 - hd;
  // Lines fade out where cells shrink below a few pixels (grazing angles) instead of shimmering.
  float line = (1.0 - smoothstep(0.018, 0.018 + aa * 1.3, edge)) * (1.0 - smoothstep(0.2, 0.55, aa));
  float halo = exp(-edge * 16.0);
  float bevel = smoothstep(0.12, 0.5, hd);

  vec3 cid = floor(cn * 64.0 + 0.5);
  float h1 = hash13(cid), h2 = hash13(cid + 17.31), h3 = hash13(cid + 41.97);
  float shimmer = step(0.86, h1) * pow(0.5 + 0.5 * sin(t * (1.1 + 2.2 * h2) + h3 * 6.2831), 6.0);
  float band = smoothstep(0.35, 0.8, snoise(cn * 2.3 + vec3(0.0, -t * 0.22, t * 0.07)));
  float energy = snoise(nn * 2.4 + vec3(0.0, -t * 0.3, t * 0.12)) * 0.65 + snoise(nn * 5.7 + vec3(t * 0.21, -t * 0.55, 0.0)) * 0.35;
  energy = energy * 0.5 + 0.5;

  float cellsLit = 0.0, ripple = 0.0, flash = 0.0, glare = 0.0, flare = 0.0;
  for (int i = 0; i < ${HITS}; i++) {
    vec4 hit = uHits[i];
    float age = t - hit.w;
    if (age < 0.0 || age > 2.5) continue;
    #ifdef GS_SHIELD_SPHERE
    vec3 dir = normalize(hit.xyz);
    float dp = acos(clamp(dot(n, dir), -1.0, 1.0)) * radius * toRipple;
    float dc = acos(clamp(dot(centre, dir), -1.0, 1.0)) * radius * toRipple;
    #else
    float dp = length(vMesh - hit.xyz) * vScale * toRipple;
    float dc = length(centre - hit.xyz * vScale) * toRipple;
    #endif
    float wave = rippleFront(age);
    // Energy spreads thinner as the ring grows, so far cells glow less than near ones.
    float fade = exp(-age * 2.6 - dc * 0.9);
    // Whole cells light as the front passes their centre; a thin ring runs along the lattice lines.
    float xc = (dc - wave) / 0.11;
    cellsLit += exp(-xc * xc) * fade * (0.35 + 0.9 * h2) + (1.0 - smoothstep(0.0, wave, dc)) * fade * 0.08;
    float xp = (dp - wave) / 0.04;
    ripple += exp(-xp * xp) * fade;
    float pulse = exp(-age * 7.0);
    flash += exp(-dp * dp * 450.0) * pulse;
    glare += exp(-dp * dp * 45.0) * pulse;
    flare += 0.45 * exp(-age * 5.0);
  }
  flare = min(flare, 1.0);

  // A drained shield dims and stutters.
  float charge = clamp(uCharge, 0.0, 1.0);
  float stutter = charge < 0.35 ? 0.75 + 0.25 * sign(wobble(t * 9.0, 3.0) + 0.4) : 1.0;

  vec3 color = uColor.rgb * uTint;
  vec3 hot = mix(color, vec3(1.0), 0.35);
  // Mostly clear glass: the lattice shows where the energy field flows and brightens towards the rim.
  float glass = 0.008 + 0.03 * energy * energy;
  float lattice = (line * (0.05 + 0.4 * energy * energy) + halo * 0.03) * (0.6 + 2.2 * fresnel);
  float lit = cellsLit * (bevel * bevel * 1.2 + 0.1);
  vec3 rgb = color * (glass + lattice + fresnel * 0.55 + bevel * bevel * (shimmer * 0.8 + band * 0.08) + flare * (0.05 + line * 0.5 + fresnel * 0.6) + lit * 0.8 + glare * 2.5)
    + hot * (rim * 2.4 + lit * 0.6 + ripple * (line * 6.0 + halo * 0.5))
    + vec3(1.0, 0.97, 0.92) * flash * 16.0;
  rgb *= mix(0.26, 1.0, front);
  rgb *= uIntensity * (0.35 + 0.65 * charge) * stutter * smoothstep(0.05, 0.6, vViewZ);
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

/** The Blender base colour's hue as a tint: white, grey and black leave the colour field as it is. */
function baseTint(material: Material): Vector3 {
  const color = (material as MeshStandardMaterial).color;
  const top = color ? Math.max(color.r, color.g, color.b) : 0;
  if (!color || top <= 1e-4) return new Vector3(1, 1, 1);
  const saturation = (top - Math.min(color.r, color.g, color.b)) / top;
  return new Vector3(1, 1, 1).lerp(new Vector3(color.r / top, color.g / top, color.b / top), saturation);
}

export const apply: ShaderApply<F> = (material, values, ctx) => {
  const u = ctx.uniforms;
  const sphere = values.lattice !== 'flat';
  const hits = Array.from({ length: HITS }, () => new Vector4(0, 0, 0, -1e6));
  const uniforms: Record<string, { value: unknown }> = {
    ...UniformsUtils.clone(UniformsLib.fog),
    uTime: ctx.time,
    uColor: u.color,
    uTint: { value: baseTint(material) },
    uIntensity: u.intensity,
    uCellSize: u.cellSize,
    uCharge: u.charge,
    uRippleSize: u.rippleSize,
    uHits: { value: hits },
  };
  if (sphere) {
    frames ??= icosahedronFrames();
    Object.assign(uniforms, {
      uFaceN: { value: frames.normals },
      uFaceEdges: { value: frames.edges },
      uFaceCorners: { value: frames.corners },
      uEdgeHeight: { value: frames.height },
    });
  }
  const shield = new ShaderMaterial({
    name: material.name,
    uniforms,
    vertexShader: VERTEX,
    fragmentShader: FRAGMENT,
    defines: sphere ? { GS_SHIELD_SPHERE: '' } : {},
    // Additive, so front and back faces share one draw; gl_FrontFacing dims the far wall.
    side: DoubleSide,
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
  shield.forceSinglePass = true;

  // Each new `hitAt` value starts a ripple in the next slot, at `hitPoint` converted into the
  // mesh's space when it is first drawn, so the ripple travels with a moving shield.
  let last = Number.NaN;
  let slot = 0;
  const point = new Vector3();
  shield.onBeforeRender = (_renderer, _scene, _camera, _geometry, object) => {
    object.castShadow = false;
    const at = u.hitAt.value;
    if (at === last) return;
    last = at;
    object.worldToLocal(point.copy(u.hitPoint.value));
    slot = (slot + 1) % HITS;
    hits[slot]!.set(point.x, point.y, point.z, at);
  };
  return shield;
};

export const mesh: ShaderMesh<F> = (target) => {
  // Light, not a solid: no shadow.
  target.castShadow = false;
};
