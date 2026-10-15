// The editor's selection in the game (`select`, SPEC §5.4). In game mode each selected entity gets a
// bounding box (BoxHelper). In the Scene view it gets an outline in Blender's selection colours
// (selected orange, the active object lighter), a constant number of pixels wide: an inverted hull
// (scene/hull.ts) drawn as an extra child of each mesh the entity draws itself, so it follows
// skinning, morphs and GPU instances. The extra draws carry `userData.gsShaderPass`, so physics
// shapes and navmeshes leave them out, they never take a ray and never cast shadows.
//
// An inverted hull outlines closed meshes; an open flat mesh (a ground plane) seen from its front
// shows no outline.

import {
  BackSide,
  BoxHelper,
  Color,
  InstancedMesh,
  Mesh,
  ShaderMaterial,
  SkinnedMesh,
  Vector2,
  Vector4,
  type BufferGeometry,
  type Intersection,
  type Material,
  type Object3D,
  type Raycaster,
  type Scene,
  type WebGLRenderer,
} from 'three';
import type { Entity } from '../ecs/entity';
import type { World } from '../ecs/world';
import { HullGeometry } from '../scene/hull';
import { isShaderPass } from '../scene/passes';

/** Blender's theme colours (3D Viewport): Object Selected and Active Object. */
export const SELECTED_COLOR = 0xed5700;
export const ACTIVE_COLOR = 0xffaa40;
/** Outline width in CSS pixels. */
export const OUTLINE_WIDTH = 2;
/** The bounding boxes of game mode (as before the Scene view). */
export const BOX_COLOR = 0xffaa00;

/** How the selection shows: bounding boxes (game mode) or outlines (the Scene view). */
export type SelectionStyle = 'box' | 'outline';

/** The tag of an outline draw in `userData.gsShaderPass`. */
export const SELECTION_PASS = 'gs_selection';

const VERTEX = /* glsl */ `
#include <common>
#include <batching_pars_vertex>
#include <morphtarget_pars_vertex>
#include <skinning_pars_vertex>
#include <logdepthbuf_pars_vertex>
#include <clipping_planes_pars_vertex>
attribute vec3 outlineNormal;
uniform vec2 gsViewport;
uniform float gsWidth;
void main() {
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
  vec3 gsN = transformedNormal;
  #ifdef FLIP_SIDED
    gsN = -gsN;
  #endif
  // Out along the normal as seen on screen, by the width in pixels (the same at any distance).
  vec2 gsDir = (projectionMatrix * vec4(gsN, 0.0)).xy * gsViewport;
  float gsLen = length(gsDir);
  if (gsLen > 1e-6) gl_Position.xy += gsDir / gsLen * (gsWidth * 2.0 / gsViewport) * gl_Position.w;
  #include <logdepthbuf_vertex>
  #include <clipping_planes_vertex>
}`;

const FRAGMENT = /* glsl */ `
#include <common>
#include <logdepthbuf_pars_fragment>
#include <clipping_planes_pars_fragment>
uniform vec3 gsColor;
void main() {
  #include <clipping_planes_fragment>
  #include <logdepthbuf_fragment>
  gl_FragColor = vec4(gsColor, 1.0);
  #include <colorspace_fragment>
}`;

/** The outline material: the hull's back faces, in an exact colour (no tone mapping, no fog). */
export function outlineMaterial(color: number): ShaderMaterial {
  return new ShaderMaterial({
    name: 'gs_selection_outline',
    uniforms: {
      gsColor: { value: new Color(color) },
      gsWidth: { value: OUTLINE_WIDTH },
      gsViewport: { value: new Vector2(1, 1) },
    },
    vertexShader: VERTEX,
    fragmentShader: FRAGMENT,
    side: BackSide,
    toneMapped: false,
    fog: false,
  });
}

const viewport = new Vector4();

/** Before each draw: the drawing buffer's size and pixel ratio, and the parent's GPU instances and morphs. */
function follow(pass: Mesh, renderer: WebGLRenderer): void {
  const ratio = renderer?.getPixelRatio?.() ?? 1;
  renderer?.getCurrentViewport?.(viewport);
  for (const material of Array.isArray(pass.material) ? pass.material : [pass.material]) {
    const u = (material as ShaderMaterial).uniforms;
    if (!u?.gsViewport) continue;
    u.gsViewport.value.set(Math.max(viewport.z, 1), Math.max(viewport.w, 1));
    u.gsWidth!.value = OUTLINE_WIDTH * ratio;
    (material as ShaderMaterial).uniformsNeedUpdate = true;
  }
  const mesh = pass.parent as Mesh | null;
  if (!mesh?.isMesh) return;
  if (mesh.morphTargetInfluences) pass.morphTargetInfluences = mesh.morphTargetInfluences;
  if ((mesh as InstancedMesh).isInstancedMesh && (pass as InstancedMesh).isInstancedMesh) {
    const from = mesh as InstancedMesh;
    const to = pass as InstancedMesh;
    to.count = from.count;
    to.instanceMatrix = from.instanceMatrix;
  }
}

// One class per kind of mesh. They never take a ray: picking hits the mesh they belong to.
class OutlineMesh extends Mesh {
  override onBeforeRender(renderer: WebGLRenderer): void {
    follow(this, renderer);
  }
  override raycast(_raycaster: Raycaster, _intersects: Intersection[]): void {}
}
class OutlineSkinnedMesh extends SkinnedMesh {
  override onBeforeRender(renderer: WebGLRenderer): void {
    follow(this, renderer);
  }
  override raycast(_raycaster: Raycaster, _intersects: Intersection[]): void {}
}
class OutlineInstancedMesh extends InstancedMesh {
  override onBeforeRender(renderer: WebGLRenderer): void {
    follow(this, renderer);
  }
  override raycast(_raycaster: Raycaster, _intersects: Intersection[]): void {}
}

/** What the selection needs from the game. */
export interface SelectionHost {
  readonly scene: Scene;
  readonly world: World;
  /** The meshes an entity draws itself (not those of child entities). */
  ownMeshes(entity: Entity): Mesh[];
}

export class Selection {
  private ids: string[] = [];
  private activeId: string | null = null;
  private style: SelectionStyle = 'box';
  private boxes: BoxHelper[] = [];
  private outlines: Mesh[] = [];
  /** Hulls by source geometry, made on first use; freed with `release()` (the scene is going). */
  private readonly hulls = new Map<BufferGeometry, HullGeometry>();
  private materials: { selected: ShaderMaterial; active: ShaderMaterial } | null = null;

  constructor(private readonly host: SelectionHost) {}

  /** The selected entity ids. */
  get selected(): readonly string[] {
    return this.ids;
  }

  /** The active entity id (drawn lighter in the Scene view), or null. */
  get active(): string | null {
    return this.activeId;
  }

  /** The outline draws now in the scene (tests and debugging). */
  get passes(): readonly Mesh[] {
    return this.outlines;
  }

  /** The bounding boxes now in the scene. */
  get helpers(): readonly BoxHelper[] {
    return this.boxes;
  }

  /** Selects entities by id; `active` is one of them (or null). Shows them in the current style. */
  set(ids: string[], active: string | null = null): void {
    this.ids = [...new Set(ids)];
    this.activeId = active !== null && this.ids.includes(active) ? active : null;
    this.show();
  }

  /** Shows the selection as boxes or outlines (again, e.g. after a scene load). */
  show(style: SelectionStyle = this.style): void {
    this.hide();
    this.style = style;
    for (const id of this.ids) {
      const entity = this.host.world.get(id);
      if (!entity?.alive) continue;
      if (style === 'box') {
        const helper = new BoxHelper(entity.object3d, BOX_COLOR);
        helper.name = SELECTION_PASS;
        helper.userData.gsShaderPass = { shader: SELECTION_PASS, material: '', kind: 'box' };
        this.host.scene.add(helper);
        this.boxes.push(helper);
      } else this.outline(entity, id === this.activeId);
    }
  }

  /** Takes the boxes and outlines out of the scene (the selection is kept). */
  hide(): void {
    for (const helper of this.boxes) {
      helper.removeFromParent();
      helper.dispose();
    }
    this.boxes = [];
    for (const pass of this.outlines) pass.removeFromParent();
    this.outlines = [];
  }

  /** Every rendered frame: boxes follow their objects. */
  update(): void {
    for (const helper of this.boxes) helper.update();
  }

  /** Hides the selection and frees the hulls (the scene they were made for is going). */
  release(): void {
    this.hide();
    for (const hull of this.hulls.values()) hull.dispose();
    this.hulls.clear();
  }

  dispose(): void {
    this.release();
    this.materials?.selected.dispose();
    this.materials?.active.dispose();
    this.materials = null;
  }

  private outline(entity: Entity, active: boolean): void {
    this.materials ??= { selected: outlineMaterial(SELECTED_COLOR), active: outlineMaterial(ACTIVE_COLOR) };
    const material = active ? this.materials.active : this.materials.selected;
    for (const mesh of this.host.ownMeshes(entity)) {
      if (isShaderPass(mesh) || mesh.name.startsWith('gs_') || !mesh.geometry?.getAttribute('position')) continue;
      let hull = this.hulls.get(mesh.geometry);
      if (!hull) this.hulls.set(mesh.geometry, (hull = new HullGeometry(mesh.geometry)));
      this.outlines.push(addOutline(mesh, hull, material));
    }
  }
}

/** A child of `mesh` that draws the hull with `material` in every material slot. */
function addOutline(mesh: Mesh, hull: HullGeometry, material: Material): Mesh {
  let pass: Mesh;
  if ((mesh as SkinnedMesh).isSkinnedMesh) {
    const skinned = mesh as SkinnedMesh;
    const p = new OutlineSkinnedMesh(hull, material);
    p.bindMode = skinned.bindMode;
    p.bind(skinned.skeleton, skinned.bindMatrix);
    pass = p;
  } else if ((mesh as InstancedMesh).isInstancedMesh) {
    const instanced = mesh as InstancedMesh;
    const p = new OutlineInstancedMesh(hull, material, instanced.count);
    p.instanceMatrix = instanced.instanceMatrix;
    pass = p;
  } else pass = new OutlineMesh(hull, material);
  if (Array.isArray(mesh.material)) pass.material = mesh.material.map(() => material);
  pass.name = SELECTION_PASS;
  pass.userData.gsShaderPass = { shader: SELECTION_PASS, material: '', kind: 'outline' };
  pass.castShadow = pass.receiveShadow = false;
  pass.layers.mask = mesh.layers.mask;
  pass.renderOrder = mesh.renderOrder;
  // Bones and instances move without the pass knowing: never culled (the GPU clips them).
  pass.frustumCulled = mesh.frustumCulled && !(mesh as SkinnedMesh).isSkinnedMesh && !(mesh as InstancedMesh).isInstancedMesh;
  if (mesh.morphTargetInfluences) {
    pass.morphTargetInfluences = mesh.morphTargetInfluences;
    pass.morphTargetDictionary = mesh.morphTargetDictionary;
  }
  mesh.add(pass);
  return pass;
}

/** True for an object the selection added (a box or an outline). */
export function isSelectionPass(object: Object3D): boolean {
  return (object.userData?.gsShaderPass as { shader?: string } | undefined)?.shader === SELECTION_PASS;
}
