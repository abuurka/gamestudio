// After FX Lab "object-outline": a selection outline of constant screen width that breathes softly,
// and where a wall hides the object, a dashed edge and a hatched fill through the wall. FX Lab draws
// it as a screen-space pass; here `mesh` adds two extra draws of each mesh (children that follow its
// skeleton, morphs and GPU instances): an inverted hull pushed out along the normals by the line
// width in pixels, and a pass drawn only where something stands in front (depth test "greater").
// The material itself keeps its look.
//
// The pass behind walls comes after every other opaque object and just before the mesh itself
// (render orders PASS_ORDER and HOST_ORDER): the depth it is tested against holds the walls but not
// the object, so its own arms and back never count as walls, and where it is in view it draws over
// the pass. The hull is transparent: it draws after all of them.

import {
  BackSide,
  BufferGeometry,
  CustomBlending,
  FrontSide,
  GreaterDepth,
  InstancedMesh,
  Mesh,
  MeshBasicMaterial,
  NormalBlending,
  OneFactor,
  ShaderMaterial,
  SkinnedMesh,
  UniformsLib,
  UniformsUtils,
  Vector2,
  Vector4,
  type Camera,
  type Group,
  type Intersection,
  type Material,
  type Object3D,
  type Raycaster,
  type Scene,
  type WebGLRenderer,
} from 'three';
import { HullGeometry } from '../../../scene/hull';
import type { ShaderApply, ShaderMesh, ShaderUniforms } from '../../../schema/shaders';
import type { fields } from './meta';

const SHADER = 'SelectionOutline';
/** Render orders: other opaque objects (0), then the passes behind walls, then the meshes that have them. */
const PASS_ORDER = 900;
const HOST_ORDER = 901;

const VERTEX_PARS = /* glsl */ `
#include <common>
#include <batching_pars_vertex>
#include <morphtarget_pars_vertex>
#include <skinning_pars_vertex>
#include <fog_pars_vertex>
#include <logdepthbuf_pars_vertex>
#include <clipping_planes_pars_vertex>
uniform float olStrength;`;

const FRAGMENT_PARS = /* glsl */ `
#include <common>
#include <fog_pars_fragment>
#include <logdepthbuf_pars_fragment>
#include <clipping_planes_pars_fragment>
uniform vec4 olColor;
uniform float olIntensity;
uniform float olStrength;
uniform float olTime;
float olFog() {
  #ifdef USE_FOG
    #ifdef FOG_EXP2
      return 1.0 - exp(-fogDensity * fogDensity * vFogDepth * vFogDepth);
    #else
      return smoothstep(fogNear, fogFar, vFogDepth);
    #endif
  #else
    return 0.0;
  #endif
}`;

// Both draws use the hull geometry: each vertex pushed out on screen along its smoothed normal by the
// line width, so the rim that shows around the mesh is the same number of pixels at any distance.
const PUSH_VERTEX = /* glsl */ `
${VERTEX_PARS}
attribute vec3 outlineNormal;
uniform vec2 olViewport;
uniform float olWidth;
varying vec3 vOlNormal;
varying vec3 vOlView;
vec4 olPush() {
  #include <morphinstance_vertex>
  #include <batching_vertex>
  vec3 objectNormal = outlineNormal;
  #include <skinbase_vertex>
  #include <skinnormal_vertex>
  #include <defaultnormal_vertex>
  #include <begin_vertex>
  #include <morphtarget_vertex>
  #include <skinning_vertex>
  #include <project_vertex>
  vec3 olN = transformedNormal;
  #ifdef FLIP_SIDED
    olN = -olN;
  #endif
  vec2 olDir = (projectionMatrix * vec4(olN, 0.0)).xy * olViewport;
  float olLen = length(olDir);
  if (olLen > 1e-6) gl_Position.xy += olDir / olLen * (olWidth * 2.0 / olViewport) * gl_Position.w;
  vOlNormal = olN;
  vOlView = -mvPosition.xyz;
  #include <logdepthbuf_vertex>
  #include <clipping_planes_vertex>
  #include <fog_vertex>
  return mvPosition;
}`;

// The outline: the hull's back faces, showing only as a rim around the mesh.
const HULL_VERTEX = /* glsl */ `
${PUSH_VERTEX}
void main() {
  olPush();
  if (olStrength <= 0.0) gl_Position = vec4(0.0, 0.0, 2.0, 1.0);
}`;

const HULL_FRAGMENT = /* glsl */ `
${FRAGMENT_PARS}
uniform float olPulse;
void main() {
  #include <clipping_planes_fragment>
  #include <logdepthbuf_fragment>
  float olK = 1.0 - olPulse * (0.5 + 0.5 * sin(olTime * 3.0));
  gl_FragColor = vec4(olColor.rgb * olIntensity * olK, clamp(olStrength, 0.0, 1.0) * (1.0 - olFog()));
  #include <tonemapping_fragment>
  #include <colorspace_fragment>
}`;

// The hidden part: the hull's front faces where something at least 0.25 m in front hides them (not
// where the mesh sinks into the ground), drawn as marching dashes along the rim (where the smoothed
// normal turns away from the camera) and a hatch inside.
const HIDDEN_VERTEX = /* glsl */ `
${PUSH_VERTEX}
uniform float olHidden;
void main() {
  vec4 olView = olPush();
  vec4 olNear = projectionMatrix * vec4(olView.xy, min(olView.z + 0.25, -1e-4), 1.0);
  gl_Position.z = clamp(olNear.z / olNear.w, -1.0, 1.0) * gl_Position.w;
  if (olStrength <= 0.0 || olHidden <= 0.0) gl_Position = vec4(0.0, 0.0, 2.0, 1.0);
}`;

const HIDDEN_FRAGMENT = /* glsl */ `
${FRAGMENT_PARS}
uniform float olPixelRatio;
uniform float olHidden;
varying vec3 vOlNormal;
varying vec3 vOlView;
void main() {
  #include <clipping_planes_fragment>
  #include <logdepthbuf_fragment>
  float olEdge = smoothstep(0.65, 0.9, 1.0 - abs(dot(normalize(vOlNormal), normalize(vOlView))));
  float olDash = step(0.45, fract((gl_FragCoord.x + gl_FragCoord.y) / (9.0 * olPixelRatio) - olTime * 1.6));
  float olHatch = step(0.7, fract((gl_FragCoord.x - gl_FragCoord.y) / (6.0 * olPixelRatio)));
  vec3 olCol = olColor.rgb * olIntensity * (olEdge * 0.6 * olDash + (1.0 - olEdge) * 0.1 * olHatch);
  gl_FragColor = vec4(olCol * clamp(olStrength, 0.0, 1.0) * (1.0 - olFog()), 1.0);
  #include <tonemapping_fragment>
  #include <colorspace_fragment>
}`;

// ---------------------------------------------------------------------------
// The hull's geometry (scene/hull.ts): one per source geometry.

const hulls = new WeakMap<BufferGeometry, HullGeometry>();
function hullOf(geometry: BufferGeometry): HullGeometry {
  let hull = hulls.get(geometry);
  if (!hull) {
    const made = (hull = new HullGeometry(geometry));
    hulls.set(geometry, made);
    made.addEventListener('dispose', () => {
      if (hulls.get(geometry) === made) hulls.delete(geometry);
    });
  }
  return hull;
}

// ---------------------------------------------------------------------------
// The extra draws

type Uniforms = ShaderUniforms<typeof fields>;

/** What each bound material instance gives the extra draws of its meshes: its live values and the game time. */
const hosts = new WeakMap<Material, { readonly name: string; readonly uniforms: Uniforms; readonly time: { value: number } }>();

/** The extra draw's tag in `userData` (kept by clones): which shader, material and draw it is. */
interface PassTag {
  shader: string;
  material: string;
  kind: 'outline' | 'hidden';
}
const tagOf = (object: Object3D): PassTag | undefined => object.userData.gsShaderPass as PassTag | undefined;
const isPass = (object: Object3D, material: string): boolean => {
  const tag = tagOf(object);
  return tag?.shader === SHADER && tag.material === material;
};

/** For the material slots of a multi-material mesh that some other material draws. */
const NOTHING = new MeshBasicMaterial({ visible: false });

function hostOf(mesh: Mesh, name: string) {
  for (const material of Array.isArray(mesh.material) ? mesh.material : [mesh.material]) {
    const host = hosts.get(material);
    if (host?.name === name) return host;
  }
  return undefined;
}

const viewport = new Vector4();

/** Before each draw: share the parent mesh's GPU instances and morphs, and copy the live values. */
function follow(pass: Mesh, renderer: WebGLRenderer): void {
  const mesh = pass.parent as Mesh | null;
  const tag = tagOf(pass);
  const host = mesh?.isMesh && tag ? hostOf(mesh, tag.material) : undefined;
  // (WebGL; the WebGPU renderer cannot draw these GLSL materials at all.)
  const ratio = renderer?.getPixelRatio?.() ?? 1;
  renderer?.getCurrentViewport?.(viewport);
  for (const material of Array.isArray(pass.material) ? pass.material : [pass.material]) {
    const u = (material as ShaderMaterial).uniforms;
    if (!u?.olColor) continue;
    if (host) {
      const h = host.uniforms;
      u.olColor!.value.copy(h.color.value);
      u.olIntensity!.value = h.intensity.value;
      u.olStrength!.value = h.strength.value;
      u.olTime!.value = host.time.value;
      if (u.olWidth) u.olWidth.value = h.width.value * ratio;
      if (u.olViewport) u.olViewport.value.set(Math.max(viewport.z, 1), Math.max(viewport.w, 1));
      if (u.olPulse) u.olPulse.value = h.pulse.value;
      if (u.olPixelRatio) u.olPixelRatio.value = ratio;
      if (u.olHidden) u.olHidden.value = h.hidden.value ? 1 : 0;
    } else u.olStrength!.value = 0; // the mesh no longer draws the shaded material
    (material as ShaderMaterial).uniformsNeedUpdate = true;
  }
  if (!mesh?.isMesh) return;
  if (mesh.morphTargetInfluences) pass.morphTargetInfluences = mesh.morphTargetInfluences;
  if ((mesh as InstancedMesh).isInstancedMesh && (pass as InstancedMesh).isInstancedMesh) {
    const from = mesh as InstancedMesh;
    const to = pass as InstancedMesh;
    to.count = from.count;
    to.instanceMatrix = from.instanceMatrix;
    to.instanceColor = from.instanceColor;
    to.morphTexture = from.morphTexture;
  }
}

// One class per kind of mesh, so copies (prefab instances, SkeletonUtils clones) keep the behaviour.
// They never take a ray: picking and raycasts hit the mesh they belong to.
class PassMesh extends Mesh {
  override onBeforeRender(renderer: WebGLRenderer): void {
    follow(this, renderer);
  }
  override raycast(_raycaster: Raycaster, _intersects: Intersection[]): void {}
}
class PassSkinnedMesh extends SkinnedMesh {
  override onBeforeRender(renderer: WebGLRenderer): void {
    follow(this, renderer);
  }
  override raycast(_raycaster: Raycaster, _intersects: Intersection[]): void {}
}
class PassInstancedMesh extends InstancedMesh {
  override onBeforeRender(renderer: WebGLRenderer): void {
    follow(this, renderer);
  }
  override raycast(_raycaster: Raycaster, _intersects: Intersection[]): void {}
}

const common = () => ({
  olColor: { value: new Vector4(1, 1, 1, 1) },
  olIntensity: { value: 1 },
  olStrength: { value: 1 },
  olTime: { value: 0 },
});

function hullMaterial(): ShaderMaterial {
  return new ShaderMaterial({
    name: 'gs_selection_outline',
    uniforms: UniformsUtils.merge([UniformsLib.fog, common(), { olWidth: { value: 3 }, olViewport: { value: new Vector2(1, 1) }, olPulse: { value: 0 } }]),
    vertexShader: HULL_VERTEX,
    fragmentShader: HULL_FRAGMENT,
    fog: true,
    transparent: true,
    depthWrite: false,
    blending: NormalBlending,
    side: BackSide,
  });
}

function hiddenMaterial(): ShaderMaterial {
  return new ShaderMaterial({
    name: 'gs_selection_outline_hidden',
    uniforms: UniformsUtils.merge([
      UniformsLib.fog,
      common(),
      { olWidth: { value: 3 }, olViewport: { value: new Vector2(1, 1) }, olPixelRatio: { value: 1 }, olHidden: { value: 1 } },
    ]),
    vertexShader: HIDDEN_VERTEX,
    fragmentShader: HIDDEN_FRAGMENT,
    fog: true,
    // In the opaque list (drawn in order, see PASS_ORDER), added all the same.
    transparent: false,
    depthWrite: false,
    depthFunc: GreaterDepth,
    blending: CustomBlending,
    blendSrc: OneFactor,
    blendDst: OneFactor,
    side: FrontSide,
  });
}

/** A child of `mesh` that draws `geometry` again with `material` (in the slots the shaded material fills). */
function addPass(mesh: Mesh, geometry: BufferGeometry, material: Material, name: string, kind: PassTag['kind']): Mesh {
  let pass: Mesh;
  if ((mesh as SkinnedMesh).isSkinnedMesh) {
    const skinned = mesh as SkinnedMesh;
    const p = new PassSkinnedMesh(geometry, material);
    p.bindMode = skinned.bindMode;
    p.bind(skinned.skeleton, skinned.bindMatrix);
    pass = p;
  } else if ((mesh as InstancedMesh).isInstancedMesh) {
    const instanced = mesh as InstancedMesh;
    const p = new PassInstancedMesh(geometry, material, instanced.count);
    p.instanceMatrix = instanced.instanceMatrix;
    p.instanceColor = instanced.instanceColor;
    pass = p;
  } else pass = new PassMesh(geometry, material);
  if (Array.isArray(mesh.material)) {
    pass.material = mesh.material.map((m) => (hosts.get(m)?.name === name ? material : NOTHING));
  }
  pass.name = `${mesh.name}_${kind === 'outline' ? 'outline' : 'outline_hidden'}`;
  pass.userData.gsShaderPass = { shader: SHADER, material: name, kind } satisfies PassTag;
  pass.castShadow = pass.receiveShadow = false;
  pass.layers.mask = mesh.layers.mask;
  // Instances move without the pass knowing: never culled (the GPU clips them).
  pass.frustumCulled = mesh.frustumCulled && !(mesh as InstancedMesh).isInstancedMesh;
  if (kind === 'hidden') {
    pass.renderOrder = PASS_ORDER;
    mesh.renderOrder = Math.max(mesh.renderOrder, HOST_ORDER);
  } else pass.renderOrder = mesh.renderOrder;
  if (mesh.morphTargetInfluences) {
    pass.morphTargetInfluences = mesh.morphTargetInfluences;
    pass.morphTargetDictionary = mesh.morphTargetDictionary;
  }
  mesh.add(pass);
  return pass;
}

/** Takes away the extra draws `mesh` has for the material `name` (from an earlier apply). */
function removePasses(mesh: Mesh, name: string): void {
  for (const child of [...mesh.children]) {
    if (!isPass(child, name)) continue;
    mesh.remove(child);
    const materials = (child as Mesh).material;
    for (const material of Array.isArray(materials) ? materials : [materials]) if (material !== NOTHING) material.dispose();
  }
}

export const apply: ShaderApply<typeof fields> = (material, _values, ctx) => {
  const name = ctx.material;
  const u = ctx.uniforms;
  hosts.set(material, { name, uniforms: u, time: ctx.time });
  // Strength 0 (or Hidden off) hides the extra draws from the next frame; until then they draw nothing.
  const previous = material.onBeforeRender;
  material.onBeforeRender = function (renderer: WebGLRenderer, scene: Scene, camera: Camera, geometry: BufferGeometry, object: Object3D, group: Group) {
    previous.call(this, renderer, scene, camera, geometry, object, group);
    for (const child of object.children) {
      if (!isPass(child, name)) continue;
      child.visible = u.strength.value > 0 && (tagOf(child)!.kind === 'outline' || u.hidden.value);
    }
  };
};

export const mesh: ShaderMesh<typeof fields> = (target, _values, ctx) => {
  removePasses(target, ctx.material);
  if (!target.geometry?.getAttribute('position')) return;
  addPass(target, hullOf(target.geometry), hullMaterial(), ctx.material, 'outline');
  addPass(target, hullOf(target.geometry), hiddenMaterial(), ctx.material, 'hidden');
};
