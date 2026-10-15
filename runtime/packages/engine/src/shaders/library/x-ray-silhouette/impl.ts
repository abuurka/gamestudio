// After FX Lab "xray-silhouette": where a wall hides the object, a coloured fresnel silhouette with
// scan bands shows through it (and a bright band sweeps up it once at `pingAt`). The material itself
// keeps its look; `mesh` adds a second draw of each mesh, a child that follows its skeleton, morphs
// and GPU instances and draws only where something stands in front (depth test "greater").
//
// Draw order does the rest: the extra draw comes after every other opaque object and just before
// the mesh itself (render orders PASS_ORDER and HOST_ORDER). So the depth it is tested against
// holds the walls but not the object, whose own arms and back never count as walls; where the
// object is in view, it then draws over its silhouette.

import {
  CustomBlending,
  FrontSide,
  GreaterDepth,
  InstancedMesh,
  Mesh,
  MeshBasicMaterial,
  OneFactor,
  OneMinusSrcAlphaFactor,
  ShaderMaterial,
  SkinnedMesh,
  UniformsLib,
  UniformsUtils,
  Vector4,
  type BufferGeometry,
  type Camera,
  type Group,
  type Intersection,
  type Material,
  type Object3D,
  type Raycaster,
  type Scene,
  type WebGLRenderer,
} from 'three';
import type { ShaderApply, ShaderMesh, ShaderUniforms } from '../../../schema/shaders';
import type { fields } from './meta';

const SHADER = 'XRaySilhouette';
/** Render orders: other opaque objects (0), then the extra draws, then the meshes that have them. */
const PASS_ORDER = 900;
const HOST_ORDER = 901;

const VERTEX = /* glsl */ `
#include <common>
#include <batching_pars_vertex>
#include <morphtarget_pars_vertex>
#include <skinning_pars_vertex>
#include <fog_pars_vertex>
#include <logdepthbuf_pars_vertex>
#include <clipping_planes_pars_vertex>
uniform float xrStrength;
uniform float xrDepth;
varying vec3 vXrNormal;
varying vec3 vXrView;
varying float vXrHeight;
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
  #include <fog_vertex>
  vXrNormal = transformedNormal;
  vXrView = -mvPosition.xyz;
  // Height above the object's origin (each GPU instance's own), in metres.
  vec4 xrP = vec4(transformed, 1.0);
  vec4 xrO = vec4(0.0, 0.0, 0.0, 1.0);
  #ifdef USE_BATCHING
    xrP = batchingMatrix * xrP;
    xrO = batchingMatrix * xrO;
  #endif
  #ifdef USE_INSTANCING
    xrP = instanceMatrix * xrP;
    xrO = instanceMatrix * xrO;
  #endif
  vXrHeight = (modelMatrix * xrP).y - (modelMatrix * xrO).y;
  // Tested as if xrDepth metres nearer the camera: it shows only behind something at least that far
  // in front (not where its feet sink into the ground).
  vec4 xrNear = projectionMatrix * vec4(mvPosition.xy, min(mvPosition.z + xrDepth, -1e-4), 1.0);
  gl_Position.z = clamp(xrNear.z / xrNear.w, -1.0, 1.0) * gl_Position.w;
  if (xrStrength <= 0.0) gl_Position = vec4(0.0, 0.0, 2.0, 1.0);
}`;

const FRAGMENT = /* glsl */ `
#include <common>
#include <fog_pars_fragment>
#include <logdepthbuf_pars_fragment>
#include <clipping_planes_pars_fragment>
uniform vec4 xrColor;
uniform float xrStrength;
uniform float xrBands;
uniform float xrPingAt;
uniform float xrTime;
varying vec3 vXrNormal;
varying vec3 vXrView;
varying float vXrHeight;
void main() {
  #include <clipping_planes_fragment>
  #include <logdepthbuf_fragment>
  vec3 n = normalize(vXrNormal);
  vec3 v = normalize(vXrView);
  float fres = pow(1.0 - clamp(abs(dot(n, v)), 0.0, 1.0), 2.4);
  // Scan bands crawl up the body; the ping is a bright band sweeping up once.
  float bands = 0.5 + 0.5 * sin(vXrHeight * 26.0 - xrTime * 4.0);
  float ping = clamp(1.0 - (xrTime - xrPingAt) * 1.4, 0.0, 1.0) * step(xrPingAt, xrTime);
  float sweep = exp(-abs(vXrHeight - (1.0 - ping) * 2.2) * 6.0) * ping;
  vec3 col = xrColor.rgb * (0.16 + 0.1 * bands * xrBands + fres * 2.6 + sweep * 4.0);
  col += vec3(1.0) * pow(fres, 4.0) * 0.6;
  // Premultiplied: the wall darkens behind the silhouette, the glow adds on top.
  float alpha = 0.55 * clamp(xrStrength, 0.0, 1.0);
  col *= xrStrength;
  #ifdef USE_FOG
    #ifdef FOG_EXP2
      float keep = exp(-fogDensity * fogDensity * vFogDepth * vFogDepth);
    #else
      float keep = 1.0 - smoothstep(fogNear, fogFar, vFogDepth);
    #endif
    col *= keep;
    alpha *= keep;
  #endif
  gl_FragColor = vec4(col, alpha);
  #include <tonemapping_fragment>
  #include <colorspace_fragment>
}`;

type Uniforms = ShaderUniforms<typeof fields>;

/** What each bound material instance gives the extra draws of its meshes: its live values and the game time. */
const hosts = new WeakMap<Material, { readonly name: string; readonly uniforms: Uniforms; readonly time: { value: number } }>();

/** The extra draw's tag in `userData` (kept by clones): which shader and material it belongs to. */
interface PassTag {
  shader: string;
  material: string;
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

/** Before each draw: share the parent mesh's GPU instances and morphs, and copy the live values. */
function follow(pass: Mesh): void {
  const mesh = pass.parent as Mesh | null;
  const tag = tagOf(pass);
  const host = mesh?.isMesh && tag ? hostOf(mesh, tag.material) : undefined;
  for (const material of Array.isArray(pass.material) ? pass.material : [pass.material]) {
    const u = (material as ShaderMaterial).uniforms;
    if (!u?.xrColor) continue;
    if (host) {
      u.xrColor!.value.copy(host.uniforms.color.value);
      u.xrStrength!.value = host.uniforms.strength.value;
      u.xrBands!.value = host.uniforms.bands.value;
      u.xrPingAt!.value = host.uniforms.pingAt.value;
      u.xrDepth!.value = host.uniforms.minDepth.value;
      u.xrTime!.value = host.time.value;
    } else u.xrStrength!.value = 0; // the mesh no longer draws the shaded material
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
  override onBeforeRender(): void {
    follow(this);
  }
  override raycast(_raycaster: Raycaster, _intersects: Intersection[]): void {}
}
class PassSkinnedMesh extends SkinnedMesh {
  override onBeforeRender(): void {
    follow(this);
  }
  override raycast(_raycaster: Raycaster, _intersects: Intersection[]): void {}
}
class PassInstancedMesh extends InstancedMesh {
  override onBeforeRender(): void {
    follow(this);
  }
  override raycast(_raycaster: Raycaster, _intersects: Intersection[]): void {}
}

function passMaterial(): ShaderMaterial {
  return new ShaderMaterial({
    name: 'gs_xray_silhouette',
    uniforms: UniformsUtils.merge([
      UniformsLib.fog,
      {
        xrColor: { value: new Vector4(1, 1, 1, 1) },
        xrStrength: { value: 1 },
        xrBands: { value: 1 },
        xrPingAt: { value: -1000 },
        xrDepth: { value: 0.3 },
        xrTime: { value: 0 },
      },
    ]),
    vertexShader: VERTEX,
    fragmentShader: FRAGMENT,
    fog: true,
    // In the opaque list (drawn in order, see PASS_ORDER), blended all the same.
    transparent: false,
    depthWrite: false,
    depthFunc: GreaterDepth,
    blending: CustomBlending,
    blendSrc: OneFactor,
    blendDst: OneMinusSrcAlphaFactor,
    side: FrontSide,
  });
}

/** A child of `mesh` that draws `geometry` again with `material` (in the slots the shaded material fills). */
function addPass(mesh: Mesh, geometry: BufferGeometry, material: Material, name: string): Mesh {
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
  pass.name = `${mesh.name}_xray`;
  pass.userData.gsShaderPass = { shader: SHADER, material: name } satisfies PassTag;
  pass.castShadow = pass.receiveShadow = false;
  pass.layers.mask = mesh.layers.mask;
  // Instances move without the pass knowing: never culled (the GPU clips them).
  pass.frustumCulled = mesh.frustumCulled && !(mesh as InstancedMesh).isInstancedMesh;
  pass.renderOrder = PASS_ORDER;
  mesh.renderOrder = Math.max(mesh.renderOrder, HOST_ORDER);
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
  const strength = ctx.uniforms.strength;
  hosts.set(material, { name, uniforms: ctx.uniforms, time: ctx.time });
  // Strength 0 hides the extra draws (from the next frame; until then they draw nothing).
  const previous = material.onBeforeRender;
  material.onBeforeRender = function (renderer: WebGLRenderer, scene: Scene, camera: Camera, geometry: BufferGeometry, object: Object3D, group: Group) {
    previous.call(this, renderer, scene, camera, geometry, object, group);
    const on = strength.value > 0;
    for (const child of object.children) if (isPass(child, name)) child.visible = on;
  };
};

export const mesh: ShaderMesh<typeof fields> = (target, _values, ctx) => {
  removePasses(target, ctx.material);
  if (!target.geometry?.getAttribute('position')) return;
  addPass(target, target.geometry, passMaterial(), ctx.material);
};
