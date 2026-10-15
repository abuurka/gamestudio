// 3D models inside canvases (docs/GAME-UI.md §11.1). A `model` element shows one node of its canvas's
// GLB (`<Canvas>.ui.glb`) in a small three.js scene of its own: a copy of the node, lights for
// `UiModel.light` and a perspective camera that frames the node's bounding sphere from `view`. The scene
// is drawn into a render target the size of the element on screen (× devicePixelRatio, at most 2) and
// shown as a textured quad at the element's draw order, with its opacity, animations and visibility.
//
// - Loading: each GLB is fetched and parsed once per URL and content hash (`files` on the canvas, as in
//   scene manifests; else its `rev`), so a live `ui.canvas` replace parses it again only when it changed.
// - Tone mapping is the level's: models are lit 3D, unlike flat UI. With WebGLRenderer the target is
//   flagged as an output target (see ModelItem.target) so three.js applies the renderer's tone mapping
//   and exposure into it; with WebGPURenderer it is the renderer's output render target while the model
//   draws, with a linear output colour space, so its output pass tone maps into it. Either way the quad
//   shows tone-mapped linear colour like any UI texture.
// - Game shaders on the models' materials (the canvas's `materials`, §11.4) are bound by the view
//   (renderer.ts, ModelShading); a model that draws one is drawn again every frame (`shaded`).
// - The target holds premultiplied alpha (an MSAA edge over a clear background is half colour, half
//   alpha), so the quad blends with ONE, ONE_MINUS_SRC_ALPHA and its opacity scales colour and alpha.
// - `spin` turns the node about its up axis and `clip` loops on an AnimationMixer, both on the UI clock
//   (CanvasView.stepModels). A still model is drawn again only when something about it changed.
//
// The math (settings, framing, camera placement, lights, target sizes, clips) is exported for tests.
import {
  AmbientLight,
  AnimationClip,
  AnimationMixer,
  Box3,
  Color,
  CustomBlending,
  DirectionalLight,
  DoubleSide,
  Group,
  HalfFloatType,
  HemisphereLight,
  LinearFilter,
  LinearSRGBColorSpace,
  Mesh,
  MeshBasicMaterial,
  OneFactor,
  OneMinusSrcAlphaFactor,
  PerspectiveCamera,
  PMREMGenerator,
  PropertyBinding,
  RenderTarget,
  Scene,
  Sphere,
  UnsignedByteType,
  Vector3,
  WebGLRenderTarget,
  type BufferGeometry,
  type Camera,
  type InstancedMesh,
  type Light,
  type Object3D,
  type SkinnedMesh,
  type Texture,
  type WebGLRenderer,
} from 'three';
import { RoomEnvironment } from 'three/addons/environments/RoomEnvironment.js';
import type { GLTF } from 'three/addons/loaders/GLTFLoader.js';
import { cloneScene } from '../../scene/clone';
import { createGltfLoader } from '../../scene/loader';
import type { UiCanvasData } from './format';
import { affine, multiply, type Affine } from './layout';
import type { UiNode } from './model';
import { ORDER_SLOT, setMatrix } from './renderer';
import { modelSettings, type UiModelLight, type UiModelSettings, type UiModelView } from './uimodel';

export { MODEL_DEFAULTS, modelBounds, modelSettings, type UiModelLight, type UiModelSettings, type UiModelView } from './uimodel';

// --- Framing -------------------------------------------------------------------------------------

/** Vertical field of view of the model cameras, in degrees: a gentle telephoto, as for product shots. */
export const MODEL_FOV = 30;
const DEG = Math.PI / 180;
/** `threeQuarter`: round from the front toward Blender's right, and up. */
const THREE_QUARTER_YAW = 35 * DEG;
const THREE_QUARTER_PITCH = 20 * DEG;

export type Vec3 = [number, number, number];

/**
 * Unit vector from the model toward the camera, glTF Y-up (Blender's X, Z, −Y): `front` sees Blender's
 * front (camera at +Z looking −Z), `side` Blender's right (+X), `top` from above, `threeQuarter` 35°
 * round from the front toward the right and 20° up.
 */
export function viewDirection(view: UiModelView): Vec3 {
  switch (view) {
    case 'front':
      return [0, 0, 1];
    case 'side':
      return [1, 0, 0];
    case 'top':
      return [0, 1, 0];
    default:
      return [
        Math.sin(THREE_QUARTER_YAW) * Math.cos(THREE_QUARTER_PITCH),
        Math.sin(THREE_QUARTER_PITCH),
        Math.cos(THREE_QUARTER_YAW) * Math.cos(THREE_QUARTER_PITCH),
      ];
  }
}

/** The camera's up: +Y, except from above, where Blender's +Y (glTF −Z) is up on screen. */
export function viewUp(view: UiModelView): Vec3 {
  return view === 'top' ? [0, 0, -1] : [0, 1, 0];
}

export interface FrameSphere {
  center: Vec3;
  radius: number;
}

/**
 * The sphere to frame: the model's bounding sphere, or, when it spins, the sphere it sweeps about the
 * up axis through its origin, so the turning model stays in view.
 */
export function framedSphere(sphere: FrameSphere, spinning: boolean): FrameSphere {
  const radius = sphere.radius > 0 && Number.isFinite(sphere.radius) ? sphere.radius : 1;
  if (!spinning) return { center: [...sphere.center], radius };
  const [x, y, z] = sphere.center;
  return { center: [0, y, 0], radius: radius + Math.hypot(x, z) };
}

/**
 * Camera distance at which a sphere of `radius` just fits a view of vertical field `fovY` (radians) and
 * `aspect` (width / height), divided by `zoom`.
 */
export function frameDistance(radius: number, fovY: number, aspect: number, zoom: number): number {
  const half = fovY / 2;
  const halfX = Math.atan(Math.tan(half) * (aspect > 0 ? aspect : 1));
  return radius / Math.sin(Math.min(half, halfX)) / (zoom > 0 ? zoom : 1);
}

/** Places `camera` to frame `sphere` from `view` for a target of `aspect`; returns the distance. */
export function placeCamera(camera: PerspectiveCamera, view: UiModelView, sphere: FrameSphere, aspect: number, zoom: number): number {
  const r = sphere.radius > 0 ? sphere.radius : 1;
  const d = frameDistance(r, MODEL_FOV * DEG, aspect, zoom);
  const [cx, cy, cz] = sphere.center;
  const [dx, dy, dz] = viewDirection(view);
  camera.fov = MODEL_FOV;
  camera.aspect = aspect > 0 ? aspect : 1;
  // Room for clips that reach beyond the rest pose; closer than the sphere when zoomed in.
  camera.near = Math.max(d - r * 1.5, d * 0.01);
  camera.far = d + r * 1.5;
  camera.up.set(...viewUp(view));
  camera.position.set(cx + dx * d, cy + dy * d, cz + dz * d);
  camera.lookAt(cx, cy, cz);
  camera.updateProjectionMatrix();
  camera.updateMatrixWorld(true);
  return d;
}

// --- Lights ----------------------------------------------------------------------------------------

/** One light of a rig. `from` is a direction in camera space (right, up, toward the camera). */
export interface LightSpec {
  kind: 'directional' | 'hemisphere' | 'ambient';
  name: string;
  intensity: number;
  /** Linear RGB. */
  color: Vec3;
  /** Hemisphere lights: the ground colour. */
  ground?: Vec3;
  from?: Vec3;
}

/**
 * The `light` presets. Lights follow the camera (a turntable under fixed studio lights), so every view
 * is lit the same way. three.js lights are physical: a directional light of intensity π shows a white
 * surface facing it as white, before tone mapping. `environment` is the intensity of a neutral studio
 * environment (WebGL), so metals (coins, trophies) reflect something rather than render black.
 */
export const LIGHT_RIGS: Readonly<Record<UiModelLight, { lights: readonly LightSpec[]; environment: number }>> = {
  // Key from the upper left, fill from the right, rim from behind, a hemisphere for the shadows.
  studio: {
    environment: 0.4,
    lights: [
      { kind: 'hemisphere', name: 'sky', intensity: 0.9, color: [1, 1, 1], ground: [0.22, 0.22, 0.25] },
      { kind: 'directional', name: 'key', intensity: 2.6, color: [1, 0.97, 0.92], from: [-1, 1.2, 1] },
      { kind: 'directional', name: 'fill', intensity: 0.9, color: [0.9, 0.95, 1], from: [1.2, 0.2, 0.8] },
      { kind: 'directional', name: 'rim', intensity: 1.8, color: [1, 1, 1], from: [0.4, 1, -1.4] },
    ],
  },
  // Even light from everywhere, brighter from above.
  soft: {
    environment: 0.6,
    lights: [
      { kind: 'hemisphere', name: 'sky', intensity: 2.2, color: [1, 1, 1], ground: [0.35, 0.35, 0.35] },
      { kind: 'ambient', name: 'ambient', intensity: 0.8, color: [1, 1, 1] },
    ],
  },
  // Ambient only, at π: base colours show as authored (an unlit look).
  flat: { environment: 0, lights: [{ kind: 'ambient', name: 'ambient', intensity: Math.PI, color: [1, 1, 1] }] },
};

/** The lights of a preset, named `ui-light:<name>`; directional lights keep their spec in userData.from. */
export function createLights(light: UiModelLight): Light[] {
  return (LIGHT_RIGS[light] ?? LIGHT_RIGS.studio).lights.map((spec) => {
    let out: Light;
    if (spec.kind === 'directional') {
      out = new DirectionalLight(0xffffff, spec.intensity);
      out.userData.from = spec.from;
    } else if (spec.kind === 'hemisphere') {
      const h = new HemisphereLight(0xffffff, 0xffffff, spec.intensity);
      h.groundColor.setRGB(...(spec.ground ?? [0, 0, 0]), LinearSRGBColorSpace);
      out = h;
    } else {
      out = new AmbientLight(0xffffff, spec.intensity);
    }
    out.color.setRGB(...spec.color, LinearSRGBColorSpace);
    out.name = `ui-light:${spec.name}`;
    return out;
  });
}

const basisX = new Vector3();
const basisY = new Vector3();
const basisZ = new Vector3();
const lightDir = new Vector3();

/** Points the directional lights at `center` from their camera-space directions, `distance` away. */
export function aimLights(lights: readonly Light[], camera: Camera, center: Vec3, distance: number): void {
  camera.matrixWorld.extractBasis(basisX, basisY, basisZ);
  for (const light of lights) {
    const from = light.userData.from as Vec3 | undefined;
    if (!from || !(light as DirectionalLight).isDirectionalLight) continue;
    lightDir
      .set(0, 0, 0)
      .addScaledVector(basisX, from[0])
      .addScaledVector(basisY, from[1])
      .addScaledVector(basisZ, from[2])
      .normalize();
    const d = distance > 0 ? distance : 1;
    light.position.set(center[0] + lightDir.x * d, center[1] + lightDir.y * d, center[2] + lightDir.z * d);
    const target = (light as DirectionalLight).target;
    target.position.set(...center);
    light.updateMatrixWorld(true);
    target.updateMatrixWorld(true);
  }
}

// --- Bounds, sizes and clips -----------------------------------------------------------------------

const partBox = new Box3();

/**
 * The bounding sphere of a model in its parent's frame (its own transform applied): from the geometry
 * bounds (the bind pose for skinned meshes, whose bones are not posed before the first draw).
 */
export function modelSphere(root: Object3D): FrameSphere {
  root.updateMatrixWorld(true);
  const parent = root.parent;
  const box = new Box3();
  root.traverse((o) => {
    const mesh = o as Mesh;
    if (!mesh.geometry || !(mesh.isMesh || (o as { isPoints?: boolean }).isPoints || (o as { isLine?: boolean }).isLine)) return;
    const instanced = o as InstancedMesh;
    if (instanced.isInstancedMesh) {
      if (instanced.boundingBox === null) instanced.computeBoundingBox();
      partBox.copy(instanced.boundingBox!);
    } else {
      if (!mesh.geometry.boundingBox) mesh.geometry.computeBoundingBox();
      partBox.copy(mesh.geometry.boundingBox!);
    }
    if (partBox.isEmpty()) return;
    partBox.applyMatrix4(o.matrixWorld);
    box.union(partBox);
  });
  if (box.isEmpty()) return { center: [0, 0, 0], radius: 1 };
  // matrixWorld includes the parent's: take it back out (the sphere is in the parent's frame).
  if (parent) box.applyMatrix4(parent.matrixWorld.clone().invert());
  const sphere = box.getBoundingSphere(new Sphere());
  return { center: [sphere.center.x, sphere.center.y, sphere.center.z], radius: sphere.radius > 0 ? sphere.radius : 1 };
}

/** Largest render target side, in device px. */
export const MAX_TARGET_SIZE = 2048;

/** A matrix's x and y scales applied to a design-px size: its size on screen in CSS px. */
export function screenSize(m: Readonly<Affine>, size: readonly [number, number]): [number, number] {
  return [size[0] * Math.hypot(m[0], m[1]), size[1] * Math.hypot(m[2], m[3])];
}

/**
 * The render target for a view of `width` × `height` CSS px: × the pixel ratio (at most 2), rounded,
 * at least 1 and at most MAX_TARGET_SIZE a side (keeping the aspect). [0, 0] for an empty view.
 */
export function targetSize(width: number, height: number, pixelRatio: number): [number, number] {
  if (!(width > 0) || !(height > 0) || !Number.isFinite(width) || !Number.isFinite(height)) return [0, 0];
  const ratio = Math.min(2, pixelRatio > 0 && Number.isFinite(pixelRatio) ? pixelRatio : 1);
  let w = width * ratio;
  let h = height * ratio;
  const k = Math.min(1, MAX_TARGET_SIZE / Math.max(w, h));
  w *= k;
  h *= k;
  return [Math.max(1, Math.round(w)), Math.max(1, Math.round(h))];
}

const baseName = (name: string): string => name.replace(/\.\d{3,}$/, '');

/**
 * The clip called `name` reduced to the tracks that animate something under `root`, or null. An exact
 * name wins, then one that differs only by Blender's ".001" suffix (as `ctx.animation` finds clips).
 */
export function bindClip(clips: readonly AnimationClip[], name: string, root: Object3D): AnimationClip | null {
  if (!name) return null;
  const reduce = (clip: AnimationClip): AnimationClip | null => {
    const tracks = clip.tracks.filter((track) => PropertyBinding.findNode(root, PropertyBinding.parseTrackName(track.name).nodeName) !== null);
    if (tracks.length === 0) return null;
    return tracks.length === clip.tracks.length ? clip : new AnimationClip(clip.name, clip.duration, tracks);
  };
  for (const clip of clips) if (clip.name === name) return reduce(clip);
  const base = baseName(name);
  for (const clip of clips) if (baseName(clip.name) === base) return reduce(clip);
  return null;
}

/** The node called `name` (the element id) in a parsed GLB: by name, sanitized name or gs_id. */
export function findModelNode(root: Object3D, name: string): Object3D | null {
  if (!name) return null;
  const clean = PropertyBinding.sanitizeNodeName(name);
  let found: Object3D | null = null;
  root.traverse((o) => {
    if (found || o === root) return;
    if (o.name === name || o.name === clean || o.userData?.gs_id === name) found = o;
  });
  return found;
}

// --- Loading -----------------------------------------------------------------------------------------

/** Where a canvas's GLB comes from, and the key its parse is cached under. */
export interface GlbSource {
  url: string;
  key: string;
  /** May come from the HTTP cache: the URL changes when the content does. */
  cache: boolean;
}

function withQuery(url: string, key: string, value: string): string {
  const hash = url.indexOf('#');
  const head = hash >= 0 ? url.slice(0, hash) : url;
  return `${head}${head.includes('?') ? '&' : '?'}${key}=${encodeURIComponent(value)}${hash >= 0 ? url.slice(hash) : ''}`;
}

/**
 * A canvas's GLB request. With a content hash (`files[glb]`, a SHA-1 prefix as in scene manifests) it is
 * `<glb>?h=<hash>`, cacheable, and the same parse serves every canvas revision with that hash. A URL that
 * is versioned already (`?h=`, `?v=`) is used as it is. Otherwise `?rev=<canvas rev>`, never cached:
 * every new revision of the canvas loads it again. `url` is the GLB's URL, resolved against the canvas file.
 */
export function glbSource(data: Pick<UiCanvasData, 'rev' | 'files'>, glb: string, url: string): GlbSource {
  const file = glb.split(/[?#]/)[0]!;
  const files = data.files && typeof data.files === 'object' ? data.files : undefined;
  const hash = files?.[glb] ?? files?.[file];
  if (typeof hash === 'string' && hash !== '') {
    const hashed = withQuery(url, 'h', hash);
    return { url: hashed, key: hashed, cache: true };
  }
  if (/[?&](?:h|v)=/.test(url)) return { url, key: url, cache: true };
  const rev = withQuery(url, 'rev', String(typeof data.rev === 'number' ? data.rev : 0));
  return { url: rev, key: rev, cache: false };
}

/** Fetches and parses a GLB (meshopt-compressed builds too). */
export type GlbLoader = (source: GlbSource) => Promise<GLTF>;

export const fetchGlb: GlbLoader = async (source) => {
  const res = await fetch(source.url, source.cache ? {} : { cache: 'no-store' });
  if (!res.ok || (res.headers.get('content-type') ?? '').includes('text/html')) throw new Error(`HTTP ${res.ok ? 404 : res.status}`);
  const buffer = await res.arrayBuffer();
  let resourcePath = '';
  try {
    resourcePath = new URL('.', new URL(source.url, typeof location === 'undefined' ? undefined : location.href)).href;
  } catch {
    // A relative URL without a page: no external resources (the export embeds them).
  }
  return createGltfLoader().parseAsync(buffer, resourcePath);
};

/** One GLB: loading, loaded (gltf) or failed (null). */
export interface GlbEntry {
  readonly source: GlbSource;
  gltf: GLTF | null;
  done: boolean;
  /** Dropped from the cache: freed once loaded, and no new models use it. */
  dropped: boolean;
  readonly ready: Promise<GLTF | null>;
}

/** Frees the GPU resources of a parsed GLB (geometries, materials, their textures). */
export function disposeGltf(gltf: GLTF): void {
  const freed = new Set<unknown>();
  const free = (thing: { dispose(): void } | null | undefined): void => {
    if (!thing || freed.has(thing)) return;
    freed.add(thing);
    thing.dispose();
  };
  gltf.scene.traverse((o) => {
    const mesh = o as Mesh;
    if (mesh.geometry) free(mesh.geometry);
    const materials = Array.isArray(mesh.material) ? mesh.material : mesh.material ? [mesh.material] : [];
    for (const material of materials) {
      for (const value of Object.values(material)) {
        if (value && typeof value === 'object' && (value as Texture).isTexture) free(value as Texture);
      }
      free(material);
    }
    if ((o as SkinnedMesh).isSkinnedMesh) free((o as SkinnedMesh).skeleton);
  });
}

/** The renderer as the model pass uses it (WebGLRenderer or WebGPURenderer). */
interface RawRenderer {
  isWebGLRenderer?: boolean;
  isWebGPURenderer?: boolean;
  autoClear: boolean;
  toneMapping?: number;
  toneMappingExposure?: number;
  outputColorSpace?: string;
  /** WebGPURenderer: the target its output pass (tone mapping, colour space) writes to; null: the canvas. */
  getOutputRenderTarget?(): unknown;
  setOutputRenderTarget?(target: unknown): void;
  extensions?: { has?(name: string): boolean };
  getRenderTarget?(): unknown;
  setRenderTarget(target: unknown): void;
  getClearColor?(target: Color): Color;
  getClearAlpha?(): number;
  setClearColor?(color: Color | number, alpha?: number): void;
  render(scene: Scene, camera: Camera): unknown;
}

const isRaw = (raw: unknown): raw is RawRenderer =>
  !!raw && typeof (raw as RawRenderer).setRenderTarget === 'function' && typeof (raw as RawRenderer).render === 'function';

/**
 * The layer's 3D models: GLBs by key (each parsed once), the studio environment, and the items of model
 * elements (one per element of a drawn canvas view).
 */
export class UiModels {
  private readonly glbs = new Map<string, GlbEntry>();
  private env: Texture | null = null;
  private envRaw: unknown = null;
  private disposed = false;

  constructor(
    private readonly warn: (message: string) => void,
    private readonly loader: GlbLoader = fetchGlb,
  ) {}

  /** The distinct GLBs a canvas's model elements use; `resolve` gives a GLB's URL (relative to the canvas file). */
  sources(data: UiCanvasData, resolve: (glb: string) => string): GlbSource[] {
    const out = new Map<string, GlbSource>();
    for (const el of data.elements ?? []) {
      const glb = el.type === 'model' ? el.model?.glb : undefined;
      if (typeof glb !== 'string' || !glb) continue;
      const source = glbSource(data, glb, resolve(glb));
      out.set(source.key, source);
    }
    return [...out.values()];
  }

  /** The GLB of `source`, loading it on first use. */
  request(source: GlbSource): GlbEntry {
    let entry = this.glbs.get(source.key);
    if (entry) return entry;
    const created: GlbEntry = {
      source,
      gltf: null,
      done: false,
      dropped: false,
      ready: this.loader(source).then(
        (gltf) => {
          created.done = true;
          if (created.dropped || this.disposed) {
            disposeGltf(gltf);
            return null;
          }
          created.gltf = gltf;
          return gltf;
        },
        (error: unknown) => {
          created.done = true;
          if (!created.dropped && !this.disposed) {
            this.warn(`Game UI: could not load the 3D models in ${source.url.replace(/[?#].*$/, '')} (${error instanceof Error ? error.message : String(error)})`);
          }
          return null;
        },
      ),
    };
    entry = created;
    this.glbs.set(source.key, entry);
    return entry;
  }

  /** Starts loading a canvas's GLBs; resolves when they are in (or failed). */
  load(data: UiCanvasData, resolve: (glb: string) => string): Promise<void> {
    const sources = this.sources(data, resolve);
    if (sources.length === 0) return Promise.resolve();
    return Promise.all(sources.map((source) => this.request(source).ready)).then(() => undefined);
  }

  /** Cache keys held now (tests). */
  keys(): string[] {
    return [...this.glbs.keys()];
  }

  /** Keeps the GLBs whose keys are in `keep` and frees the others (their models must be disposed already). */
  retain(keep: ReadonlySet<string>): void {
    for (const [key, entry] of this.glbs) {
      if (keep.has(key)) continue;
      this.glbs.delete(key);
      entry.dropped = true;
      if (entry.gltf) disposeGltf(entry.gltf);
      entry.gltf = null;
    }
  }

  /** The item that draws a model element, or null when the element has no GLB node to show. */
  item(data: UiCanvasData, node: UiNode, resolve: (glb: string) => string, quad: BufferGeometry): ModelItem | null {
    const model = node.el.type === 'model' ? node.el.model : undefined;
    const label = `${data.canvas}/${node.el.name}`;
    if (!model || typeof model.glb !== 'string' || !model.glb || typeof model.node !== 'string' || !model.node) {
      this.warn(`Game UI: "${label}" is a 3D model without a GLB node; export the UI file again`);
      return null;
    }
    const entry = this.request(glbSource(data, model.glb, resolve(model.glb)));
    return new ModelItem(node, entry, quad, this, label);
  }

  /** A neutral studio environment for WebGL (one per renderer), or null. */
  environment(raw: unknown): Texture | null {
    if (raw === this.envRaw) return this.env;
    this.env?.dispose();
    this.env = null;
    this.envRaw = raw;
    if (!(raw as RawRenderer | null)?.isWebGLRenderer) return null;
    try {
      const pmrem = new PMREMGenerator(raw as WebGLRenderer);
      const room = new RoomEnvironment();
      this.env = pmrem.fromScene(room, 0.04).texture;
      room.dispose();
      pmrem.dispose();
    } catch {
      this.env = null;
    }
    return this.env;
  }

  /** Says a problem once (the layer de-duplicates). */
  report(message: string): void {
    this.warn(message);
  }

  dispose(): void {
    this.disposed = true;
    this.retain(new Set());
    this.env?.dispose();
    this.env = null;
    this.envRaw = null;
  }
}

const scratch = affine();
const rectAffine = affine();
const clearColor = new Color();
const savedColor = new Color();

/**
 * One model element of a canvas view: its quad in the UI scene, and the scene, camera, lights and render
 * target behind it. CanvasView owns it: `draw` per frame the canvas is drawn, `step` on the UI clock.
 */
export class ModelItem {
  /** The quad in the UI scene (unit quad × the element's rect, like an image). */
  readonly mesh: Mesh;
  readonly material: MeshBasicMaterial;
  readonly settings: UiModelSettings;
  readonly scene = new Scene();
  readonly camera = new PerspectiveCamera(MODEL_FOV, 1, 0.1, 100);
  /** Turns about Y (spin); the model's copy is its child. */
  readonly pivot = new Group();
  readonly lights: Light[];
  /** The copy of the GLB node, once loaded. */
  model: Object3D | null = null;
  mixer: AnimationMixer | null = null;
  /** The render target, while the canvas is drawn. */
  target: WebGLRenderTarget | RenderTarget | null = null;
  /** Spin angle in radians. */
  angle = 0;
  /** The sphere the camera frames (pivot space). */
  sphere: FrameSphere | null = null;
  /** Renders made into the target (tests). */
  renders = 0;
  /** Its materials draw game shaders (§11.4), which may change with time: drawn every frame. */
  shaded = false;
  private dirty = true;
  private aspect = 0;
  private tone = '';
  private rendered = false;
  private broken = false;
  private disposed = false;

  constructor(
    readonly node: UiNode,
    readonly glb: GlbEntry,
    quad: BufferGeometry,
    private readonly owner: UiModels,
    readonly label: string,
  ) {
    this.settings = modelSettings(node.el);
    this.material = new MeshBasicMaterial({
      transparent: true,
      depthTest: false,
      depthWrite: false,
      toneMapped: false,
      side: DoubleSide,
      // Premultiplied: colour and alpha are both scaled by the opacity (color and opacity below).
      blending: CustomBlending,
      blendSrc: OneFactor,
      blendDst: OneMinusSrcAlphaFactor,
      blendSrcAlpha: OneFactor,
      blendDstAlpha: OneMinusSrcAlphaFactor,
    });
    this.mesh = new Mesh(quad, this.material);
    this.mesh.name = node.el.name;
    this.mesh.matrixAutoUpdate = false;
    this.mesh.frustumCulled = false;
    this.mesh.visible = false;
    this.scene.name = `ui-model:${label}`;
    this.scene.add(this.pivot);
    this.lights = createLights(this.settings.light);
    for (const light of this.lights) {
      this.scene.add(light);
      if ((light as DirectionalLight).isDirectionalLight) this.scene.add((light as DirectionalLight).target);
    }
    if (glb.done) this.attach(glb.gltf);
    else void glb.ready.then((gltf) => this.attach(gltf));
  }

  /** Puts a copy of the element's node in the scene, with its clip. */
  private attach(gltf: GLTF | null): void {
    if (this.disposed || this.model || !gltf) return;
    const name = this.node.el.model?.node ?? '';
    const source = findModelNode(gltf.scene, name);
    if (!source) {
      this.owner.report(`Game UI: "${this.label}": the 3D model's node "${name}" is not in its GLB; export the UI file again`);
      return;
    }
    const model = cloneScene(source);
    this.sphere = framedSphere(modelSphere(model), this.settings.spin !== 0);
    this.model = model;
    this.pivot.add(model);
    const clip = this.settings.clip;
    if (clip) {
      const bound = bindClip(gltf.animations ?? [], clip, model);
      if (bound) {
        this.mixer = new AnimationMixer(model);
        this.mixer.clipAction(bound).play();
        this.mixer.update(0);
      } else {
        const listed = this.node.el.model?.clips ?? [];
        this.owner.report(
          `Game UI: "${this.label}": clip "${clip}" does not animate its 3D model` + (listed.length ? ` (its clips: ${listed.join(', ')})` : ' (it has no clips)'),
        );
      }
    }
    this.aspect = 0; // frame it at the next draw
    this.dirty = true;
  }

  /** The UI clock: spin and clip move on by `dt` seconds. */
  step(dt: number): void {
    if (!this.model || !(dt > 0) || !Number.isFinite(dt)) return;
    if (this.settings.spin !== 0) {
      this.angle = (this.angle + this.settings.spin * dt) % (Math.PI * 2);
      this.pivot.rotation.y = this.angle;
      this.dirty = true;
    }
    if (this.mixer) {
      this.mixer.update(dt);
      this.dirty = true;
    }
  }

  /**
   * Per frame its canvas is drawn, after the view's sync: places the quad (draw order, matrix, opacity)
   * and draws the model into its target when it changed. Hidden, transparent or empty: nothing renders.
   */
  draw(raw: unknown, orderBase: number, pixelRatio: number): void {
    const node = this.node;
    const mesh = this.mesh;
    mesh.renderOrder = orderBase + Math.max(0, Math.min(ORDER_SLOT - 1, node.el.order ?? 0));
    const opacity = node.opacity;
    const [width, height] = screenSize(node.rest, this.settings.size);
    const [tw, th] = targetSize(width, height, pixelRatio);
    if (!node.visible || !(opacity > 0) || tw === 0 || !this.model || this.broken) {
      mesh.visible = false;
      return;
    }
    if (isRaw(raw)) this.paint(raw, tw, th);
    mesh.visible = this.rendered;
    if (!mesh.visible) return;
    this.material.color.setRGB(opacity, opacity, opacity);
    this.material.opacity = opacity;
    const [w, h] = this.settings.size;
    rectAffine[0] = w;
    rectAffine[1] = 0;
    rectAffine[2] = 0;
    rectAffine[3] = h;
    rectAffine[4] = -w / 2;
    rectAffine[5] = -h / 2;
    setMatrix(mesh, multiply(scratch, node.draw, rectAffine));
  }

  /**
   * The render target for `w` × `h` device px. WebGL: half float when the GPU can render it (8-bit
   * linear bands in the darks), 4× MSAA, and `isXRRenderTarget` so three.js renders into it as it renders
   * to the screen: with the renderer's tone mapping and exposure (it skips both for plain targets), its
   * output colour space being the texture's (linear, as for any target). WebGPU: half float, made the
   * renderer's output render target while the model draws (paint), for the same tone mapping.
   */
  private ensureTarget(raw: RawRenderer, w: number, h: number): WebGLRenderTarget | RenderTarget {
    const target = this.target;
    if (target) {
      if (target.width !== w || target.height !== h) {
        target.setSize(w, h);
        this.dirty = true;
      }
      return target;
    }
    const webgl = raw.isWebGLRenderer === true;
    const half = !webgl || !!(raw.extensions?.has?.('EXT_color_buffer_float') || raw.extensions?.has?.('EXT_color_buffer_half_float'));
    const options = {
      type: half ? HalfFloatType : UnsignedByteType,
      colorSpace: LinearSRGBColorSpace,
      depthBuffer: true,
      generateMipmaps: false,
      minFilter: LinearFilter,
      magFilter: LinearFilter,
      samples: 4,
    };
    const created = webgl ? new WebGLRenderTarget(w, h, options) : new RenderTarget(w, h, options);
    if (webgl) (created as { isXRRenderTarget?: boolean }).isXRRenderTarget = true;
    created.texture.name = `ui-model:${this.label}`;
    this.target = created;
    this.material.map = created.texture;
    this.material.needsUpdate = true;
    this.dirty = true;
    return created;
  }

  private paint(raw: RawRenderer, w: number, h: number): void {
    try {
      const target = this.ensureTarget(raw, w, h);
      const tone = `${raw.toneMapping ?? ''}|${raw.toneMappingExposure ?? ''}`;
      if (tone !== this.tone) {
        this.tone = tone;
        this.dirty = true;
      }
      const rig = LIGHT_RIGS[this.settings.light] ?? LIGHT_RIGS.studio;
      const env = rig.environment > 0 ? this.owner.environment(raw) : null;
      if (env !== this.scene.environment) {
        this.scene.environment = env;
        this.scene.environmentIntensity = rig.environment;
        this.dirty = true;
      }
      const aspect = w / h;
      if (aspect !== this.aspect && this.sphere) {
        this.aspect = aspect;
        const distance = placeCamera(this.camera, this.settings.view, this.sphere, aspect, this.settings.zoom);
        aimLights(this.lights, this.camera, this.sphere.center, distance);
        this.dirty = true;
      }
      if (!this.dirty && !this.shaded) return;
      this.dirty = false;
      const previous = raw.getRenderTarget?.() ?? null;
      const autoClear = raw.autoClear;
      const saved = raw.getClearColor ? raw.getClearColor(savedColor) : null;
      const alpha = raw.getClearAlpha?.() ?? 0;
      const [r, g, b, a] = this.settings.background;
      // WebGPU: the target is the output render target, so three's output pass tone maps into it (with a
      // linear output colour space, as on WebGL), as RenderPipeline and XR use it.
      const gpu = raw.isWebGPURenderer === true && typeof raw.setOutputRenderTarget === 'function';
      const output = gpu ? (raw.getOutputRenderTarget?.() ?? null) : null;
      const space = raw.outputColorSpace;
      try {
        if (gpu) {
          raw.setOutputRenderTarget!(target);
          raw.outputColorSpace = LinearSRGBColorSpace;
          raw.setRenderTarget(null);
        } else {
          raw.setRenderTarget(target);
        }
        // Linear, like every UI colour; three.js premultiplies the clear colour by its alpha.
        raw.setClearColor?.(clearColor.setRGB(r, g, b, LinearSRGBColorSpace), a);
        raw.autoClear = true;
        raw.render(this.scene, this.camera);
        this.renders++;
        this.rendered = true;
      } finally {
        raw.autoClear = autoClear;
        if (saved) raw.setClearColor?.(saved, alpha);
        if (gpu) {
          raw.outputColorSpace = space;
          raw.setOutputRenderTarget!(output);
        }
        raw.setRenderTarget(previous);
      }
    } catch (error) {
      this.broken = true;
      this.rendered = false;
      this.owner.report(`Game UI: could not draw the 3D model "${this.label}" (${error instanceof Error ? error.message : String(error)})`);
    }
  }

  /** Frees the render target (the canvas is no longer drawn); the next draw makes it again. */
  release(): void {
    if (!this.target && !this.rendered) return;
    this.target?.dispose();
    this.target = null;
    this.material.map = null;
    this.material.needsUpdate = true;
    this.mesh.visible = false;
    this.rendered = false;
    this.dirty = true;
  }

  /** Frees everything it owns: the target, the quad's material, the copy's mixer and skeletons. */
  dispose(): void {
    if (this.disposed) return;
    this.disposed = true;
    this.release();
    this.mesh.removeFromParent();
    this.material.dispose();
    if (this.mixer && this.model) {
      this.mixer.stopAllAction();
      this.mixer.uncacheRoot(this.model);
    }
    this.mixer = null;
    // The copy shares geometries, materials and textures with the cached parse (UiModels frees those),
    // but has skeletons of its own.
    this.model?.traverse((o) => {
      if ((o as SkinnedMesh).isSkinnedMesh) (o as SkinnedMesh).skeleton.dispose();
    });
    this.scene.environment = null;
    this.scene.clear();
    this.model = null;
  }
}
