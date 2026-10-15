// Draws canvases with three.js (docs/GAME-UI.md §5.3): an orthographic scene in canvas CSS px (y down)
// drawn over the finished 3D frame. Each canvas has a view: one flat mesh per text, image and mesh
// element, its matrix and visibility copied from the model's layout, its renderOrder from the element's
// `order` stacked by canvas. Unlit, untonemapped, no depth. Works with WebGLRenderer and WebGPURenderer.
//
// Blending happens in linear light, as Blender composites (Eevee and the viewport): with WebGLRenderer the
// frame is copied into an sRGB render target (SRGB8_ALPHA8, which the GPU blends in linear light), the
// canvases are drawn into it, and it is copied back. Drawn straight into the 8-bit canvas, a white panel
// at 15% alpha over a dark scene came out about a third as bright as Blender shows it. WebGPURenderer
// gets the same from WebGpuComposite (§11.6): drawn straight, three's output pass tone mapped the UI.
//
// Phase 3 (§11.4): a mesh element whose material names a game shader made for UI meshes (`ui: true`)
// draws it, bound like a level's materials (ViewShading), with `ctx.time` the canvas's open time.
//
// Phase 2 (§10.5, §10.6): every material's opacity is multiplied by the element's opacity (animated, and
// inherited down the hierarchy); a menu's backdrop fades with its open and close animations; a 9-slice
// mesh is rebuilt as 9 quads whenever its rest size changes, so its corners keep `insets × canvas scale`.
import {
  AdditiveBlending,
  BufferAttribute,
  BufferGeometry,
  CanvasTexture,
  ColorManagement,
  DoubleSide,
  FramebufferTexture,
  Group,
  HalfFloatType,
  LinearFilter,
  Mesh,
  MeshBasicMaterial,
  NearestFilter,
  NearestMipmapNearestFilter,
  NoBlending,
  NoToneMapping,
  NormalBlending,
  OrthographicCamera,
  RenderTarget,
  Scene,
  ShaderMaterial,
  SRGBColorSpace,
  Texture,
  Vector2,
  WebGLRenderTarget,
  type Camera,
  type ColorSpace,
  type Material,
  type Object3D,
  type ToneMapping,
  type WebGLRenderer,
} from 'three';
import type { GameRenderer } from '../../game/renderer';
import { MaterialShaders } from '../../scene/materials';
import type { ManifestMaterial } from '../../scene/manifest';
import { getShader, loadShader, onShaderDefined } from '../../schema/shaders';
import type { ImageEntry, ImageRegistry } from './assets';
import type { UiCanvasData, UiColor, UiMeshData } from './format';
import { affine, multiply, scaleOf, type Affine, type Bounds } from './layout';
import type { CanvasModel, UiNode } from './model';
import type { ModelItem } from './models3d';
import { create2d, rasterizeText, textQuad, type TextPaint } from './text';

/** renderOrder room per stacked canvas (element orders stay below it). */
export const ORDER_SLOT = 1 << 20;

/** A unit quad (0..1, y down) whose uvs show a flipY texture upright. */
export function createQuad(): BufferGeometry {
  const geometry = new BufferGeometry();
  geometry.setAttribute('position', new BufferAttribute(new Float32Array([0, 0, 0, 1, 0, 0, 1, 1, 0, 0, 1, 0]), 3));
  geometry.setAttribute('uv', new BufferAttribute(new Float32Array([0, 1, 1, 1, 1, 0, 0, 0]), 2));
  geometry.setIndex([0, 1, 2, 0, 2, 3]);
  return geometry;
}

function material(color: Readonly<UiColor>, opacity: number): MeshBasicMaterial {
  const m = new MeshBasicMaterial({
    transparent: true,
    depthTest: false,
    depthWrite: false,
    toneMapped: false,
    side: DoubleSide,
    opacity,
  });
  m.color.setRGB(color[0] ?? 1, color[1] ?? 1, color[2] ?? 1);
  return m;
}

function flatMesh(geometry: BufferGeometry, mat: MeshBasicMaterial): Mesh {
  const mesh = new Mesh(geometry, mat);
  mesh.matrixAutoUpdate = false;
  mesh.frustumCulled = false;
  return mesh;
}

/** Sets an object's matrix from a 2D affine (z untouched). */
export function setMatrix(object: Object3D, m: Readonly<Affine>): void {
  object.matrix.set(m[0], m[2], 0, m[4], m[1], m[3], 0, m[5], 0, 0, 1, 0, 0, 0, 0, 1);
  object.matrixWorldNeedsUpdate = true;
}

/** A 9-slice panel (GAME-UI §10.6), checked: insets ≥ 0 in image px, the image size, the local rectangle. */
export interface SliceSpec {
  /** [left, right, top, bottom] in image px (= design px on screen at canvas scale 1). */
  insets: [number, number, number, number];
  image: [number, number];
  /** [minX, minY, maxX, maxY] in local px. */
  bounds: Bounds;
}

const finiteNumbers = (v: unknown, n: number): v is number[] => Array.isArray(v) && v.length >= n && v.slice(0, n).every((x) => typeof x === 'number' && Number.isFinite(x));

/** A mesh's 9-slice, or null when it has none (or an unusable one: then the mesh draws as exported). */
export function sliceOf(mesh: UiMeshData): SliceSpec | null {
  const slice = mesh.slice;
  if (!slice || !finiteNumbers(slice.insets, 4) || !finiteNumbers(slice.image, 2) || !(slice.image[0] > 0 && slice.image[1] > 0)) return null;
  let bounds: Bounds | null = finiteNumbers(mesh.bounds, 4) ? [mesh.bounds[0], mesh.bounds[1], mesh.bounds[2], mesh.bounds[3]] : null;
  if (!bounds && finiteNumbers(slice.size, 2)) bounds = [-slice.size[0] / 2, -slice.size[1] / 2, slice.size[0] / 2, slice.size[1] / 2];
  if (!bounds || !(bounds[2] >= bounds[0] && bounds[3] >= bounds[1])) return null;
  const [l, r, t, b] = slice.insets.map((v) => Math.max(0, v)) as [number, number, number, number];
  return { insets: [l, r, t, b], image: [slice.image[0], slice.image[1]], bounds };
}

/**
 * The four stops of a 9-slice along one axis, in local px from `lo` to `hi`: the corners `a` and `b`
 * (design px) are divided by `ratio`, the element's scale over the canvas scale, so they keep their size
 * on screen. Corners that would overlap shrink in proportion until they meet.
 */
export function sliceStops(lo: number, hi: number, a: number, b: number, ratio: number): [number, number, number, number] {
  const span = Math.max(0, hi - lo);
  let first = ratio > 0 ? a / ratio : Infinity;
  let last = ratio > 0 ? b / ratio : Infinity;
  if (!(first + last <= span)) {
    const total = a + b;
    first = total > 0 ? (span * a) / total : 0;
    last = total > 0 ? (span * b) / total : 0;
  }
  return [lo, lo + first, hi - last, hi];
}

/** Writes the 4 × 4 grid of a 9-slice (row by row, top first; flat x, y, z) for the scale ratios (rx, ry). */
export function slicePositions(slice: SliceSpec, rx: number, ry: number, out: Float32Array): Float32Array {
  const [l, r, t, b] = slice.insets;
  const [x0, y0, x1, y1] = slice.bounds;
  const xs = sliceStops(x0, x1, l, r, rx);
  const ys = sliceStops(y0, y1, t, b, ry); // y down: the top inset is at minY
  for (let j = 0; j < 4; j++) {
    for (let i = 0; i < 4; i++) {
      const k = (j * 4 + i) * 3;
      out[k] = xs[i]!;
      out[k + 1] = ys[j]!;
      out[k + 2] = 0;
    }
  }
  return out;
}

/** The grid's uvs: the insets of the image (Blender convention, v up: the top row is v = 1). */
export function sliceUvs(slice: SliceSpec): Float32Array {
  const [l, r, t, b] = slice.insets;
  const [w, h] = slice.image;
  const clamp = (v: number) => Math.min(1, Math.max(0, v));
  const us = [0, clamp(l / w), clamp(1 - r / w), 1];
  const vs = [1, clamp(1 - t / h), clamp(b / h), 0];
  const out = new Float32Array(32);
  for (let j = 0; j < 4; j++) {
    for (let i = 0; i < 4; i++) {
      out[(j * 4 + i) * 2] = us[i]!;
      out[(j * 4 + i) * 2 + 1] = vs[j]!;
    }
  }
  return out;
}

/** The 9 quads of a 4 × 4 grid. */
function sliceIndices(): number[] {
  const out: number[] = [];
  for (let j = 0; j < 3; j++) {
    for (let i = 0; i < 3; i++) {
      const a = j * 4 + i;
      out.push(a, a + 1, a + 5, a, a + 5, a + 4);
    }
  }
  return out;
}

/** What a view needs from the layer. */
export interface ViewEnv {
  images: ImageRegistry;
  /** URL of one of this canvas's asset paths. */
  url(path: string): string;
  quad: BufferGeometry;
  /** Device pixels per CSS px for text rasters. */
  pixelRatio(): number;
  /** Phase 3 (§11.1): the item that draws a 3D model element (null: none; absent: models are not drawn). */
  model?(node: UiNode): ModelItem | null;
}

interface Item {
  node: UiNode;
  mesh: Mesh;
  material: MeshBasicMaterial;
  /** Textured items wait for their image. */
  image: ImageEntry | null;
  texture: Texture | null;
  /** Local px rect the unit quad covers (images and text). */
  rect: [number, number, number, number] | null;
  /** The material's own alpha (mesh colour alpha, image opacity, 1 for text), before the element's opacity. */
  alpha: number;
  /** 9-slice meshes: the slice, and the scale ratios its grid was built for. */
  slice?: { spec: SliceSpec; positions: BufferAttribute; rx: number; ry: number } | null;
  /** Text only. */
  raster?: { canvas: HTMLCanvasElement; ctx: CanvasRenderingContext2D } | null;
  /** The node layoutVersion and scale the raster was drawn for. */
  rasterVersion?: number;
  rasterScale?: number;
}

const scratch = affine();
const rectAffine = affine();

/** The three.js objects of one canvas. */
export class CanvasView {
  readonly root = new Group();
  readonly items: Item[] = [];
  /** 3D model elements (§11.1), each drawn into its own target and shown as a quad. */
  readonly models: ModelItem[] = [];
  private readonly textures = new Map<string, { texture: Texture; entry: ImageEntry; ready: boolean }>();
  private readonly owned: BufferGeometry[] = [];
  private synced = -1;
  private imagesSynced = -1;
  private orderBase = NaN;
  /** The game shaders' `ctx.time` on its meshes (§11.4): UI seconds since the canvas opened. */
  readonly time = { value: 0 };
  /** Its meshes' game shaders: undefined until the renderer draws it, null when it has none. */
  private shading: ViewShading | null | undefined;
  /** Its 3D models' game shaders (the canvas's `materials`), bound as each model's GLB arrives. */
  private readonly modelShading = new Map<ModelItem, ModelShading>();
  private shaderHost: UiShaderHost | null = null;

  constructor(
    readonly model: CanvasModel,
    private readonly env: ViewEnv,
  ) {
    this.root.name = `ui:${model.name}`;
    this.root.matrixAutoUpdate = false;
    for (const node of model.nodes) {
      if (node.el.type === 'model') {
        const m = this.env.model?.(node) ?? null;
        if (m) {
          this.models.push(m);
          this.root.add(m.mesh);
        }
        continue;
      }
      const item = this.build(node);
      if (!item) continue;
      item.mesh.name = node.el.name;
      item.mesh.visible = false;
      this.items.push(item);
      this.root.add(item.mesh);
    }
  }

  private texture(path: string, pixelated: boolean): { texture: Texture; entry: ImageEntry } | null {
    const url = this.env.url(path);
    const key = `${pixelated ? 'n' : 'l'}|${url}`;
    let found = this.textures.get(key);
    if (!found) {
      const entry = this.env.images.get(url);
      if (!entry.image) return null;
      const texture = new Texture(entry.image);
      texture.colorSpace = SRGBColorSpace;
      if (pixelated) {
        texture.magFilter = NearestFilter;
        texture.minFilter = NearestMipmapNearestFilter;
      }
      found = { texture, entry, ready: false };
      this.textures.set(key, found);
    }
    return found;
  }

  private build(node: UiNode): Item | null {
    const el = node.el;
    if (el.type === 'mesh' && el.mesh) {
      const m = el.mesh;
      const geometry = new BufferGeometry();
      const spec = sliceOf(m);
      let colors: Float32Array | null = null;
      let slice: Item['slice'] = null;
      if (spec) {
        // A 4 × 4 grid, its positions rebuilt in sync() for the laid-out size (the exported mesh is the grid at scale 1).
        const positions = new BufferAttribute(slicePositions(spec, 1, 1, new Float32Array(48)), 3);
        geometry.setAttribute('position', positions);
        geometry.setAttribute('uv', new BufferAttribute(sliceUvs(spec), 2));
        geometry.setIndex(sliceIndices());
        slice = { spec, positions, rx: 1, ry: 1 };
      } else {
        const count = Math.floor((m.positions?.length ?? 0) / 2);
        const positions = new Float32Array(count * 3);
        for (let i = 0; i < count; i++) {
          positions[i * 3] = m.positions[i * 2]!;
          positions[i * 3 + 1] = m.positions[i * 2 + 1]!;
        }
        geometry.setAttribute('position', new BufferAttribute(positions, 3));
        // Blender uvs (v up) suit flipY textures as they are.
        if (m.uvs && m.uvs.length >= count * 2) geometry.setAttribute('uv', new BufferAttribute(new Float32Array(m.uvs), 2));
        colors = m.colors && m.colors.length >= count * 4 ? new Float32Array(m.colors) : null;
        if (colors) geometry.setAttribute('color', new BufferAttribute(colors, 4));
        if (m.indices?.length) geometry.setIndex(count > 65535 ? new BufferAttribute(new Uint32Array(m.indices), 1) : m.indices);
      }
      this.owned.push(geometry);
      const alpha = m.material?.color?.[3] ?? 1;
      const mat = material(m.material?.color ?? [1, 1, 1, 1], alpha);
      if (colors) mat.vertexColors = true;
      if (m.material?.blend === 'additive') mat.blending = AdditiveBlending;
      // 'opaque' still blends (alpha 1): every element stays in one transparent list ordered by renderOrder.
      else mat.blending = NormalBlending;
      const tex = m.material?.texture ? this.texture(m.material.texture, false) : null;
      if (tex) mat.map = tex.texture;
      return {
        node,
        mesh: flatMesh(geometry, mat),
        material: mat,
        image: m.material?.texture ? (tex?.entry ?? { image: null, loaded: false, ready: Promise.resolve() }) : null,
        texture: tex?.texture ?? null,
        rect: null,
        alpha,
        slice,
      };
    }
    if (el.type === 'image' && el.image) {
      const img = el.image;
      const alpha = Number.isFinite(img.opacity) ? img.opacity : 1;
      const mat = material([1, 1, 1, 1], alpha);
      const tex = img.src ? this.texture(img.src, !!img.pixelated) : null;
      if (tex) mat.map = tex.texture;
      return {
        node,
        mesh: flatMesh(this.env.quad, mat),
        material: mat,
        image: tex?.entry ?? { image: null, loaded: false, ready: Promise.resolve() },
        texture: tex?.texture ?? null,
        rect: [img.rect[0], img.rect[1], img.rect[2], img.rect[3]],
        alpha,
      };
    }
    if (el.type === 'text' && el.text) {
      const mat = material([1, 1, 1, 1], 1);
      return { node, mesh: flatMesh(this.env.quad, mat), material: mat, image: null, texture: null, rect: null, alpha: 1, rasterVersion: -1, rasterScale: 0 };
    }
    return null;
  }

  /** Copies the model's layout into the objects; `orderBase` stacks this canvas. Cheap when nothing changed. */
  sync(orderBase: number): void {
    const model = this.model;
    const images = this.env.images.version;
    if (this.shading?.swapped()) this.synced = -1; // a game shader's material went onto a mesh: its opacity
    if (model.version === this.synced && images === this.imagesSynced && orderBase === this.orderBase) return;
    this.synced = model.version;
    this.imagesSynced = images;
    this.orderBase = orderBase;
    for (const t of this.textures.values()) {
      if (!t.ready && t.entry.loaded) {
        t.ready = true;
        t.texture.needsUpdate = true;
      }
    }
    for (const item of this.items) {
      const node = item.node;
      item.mesh.renderOrder = orderBase + Math.max(0, Math.min(ORDER_SLOT - 1, node.el.order ?? 0));
      if (node.el.type === 'text') this.syncText(item);
      let ready = true;
      if (item.image) ready = item.image.loaded && !!item.texture;
      if (node.el.type === 'text') ready = !!item.texture && !!item.rect;
      item.mesh.visible = node.visible && ready;
      if (!item.mesh.visible) continue;
      // An image's own alpha is `image.opacity` unless the element carries its opacity (the same Object.color alpha).
      const own = node.el.type === 'image' && (Number.isFinite(node.el.opacity) || node.anim?.opacity !== undefined) ? 1 : item.alpha;
      const opacity = own * node.opacity;
      if (item.material.opacity !== opacity) item.material.opacity = opacity;
      // A resized grid drops its bounding box: game shaders that place pixels in the mesh's bounds measure it again.
      if (item.slice && this.syncSlice(item.slice, node)) item.mesh.geometry.boundingBox = null;
      if (item.rect) {
        const [x, y, w, h] = item.rect;
        rectAffine[0] = w;
        rectAffine[1] = 0;
        rectAffine[2] = 0;
        rectAffine[3] = h;
        rectAffine[4] = x;
        rectAffine[5] = y;
        setMatrix(item.mesh, multiply(scratch, node.draw, rectAffine));
      } else {
        setMatrix(item.mesh, node.draw);
      }
    }
  }

  /**
   * Rebuilds a 9-slice grid when the element's rest scale over the canvas scale changed (object scale,
   * stretch, parents, bar fills): corners keep `insets × canvas scale` on screen. Animation and the focus
   * scale zoom the result as a whole, as Blender shows a keyframed scale.
   */
  private syncSlice(slice: NonNullable<Item['slice']>, node: UiNode): boolean {
    const s = this.model.scale > 0 ? this.model.scale : 1;
    const m = node.rest;
    const rx = Math.round((Math.hypot(m[0], m[1]) / s) * 1e6) / 1e6;
    const ry = Math.round((Math.hypot(m[2], m[3]) / s) * 1e6) / 1e6;
    if (rx === slice.rx && ry === slice.ry) return false;
    slice.rx = rx;
    slice.ry = ry;
    slicePositions(slice.spec, rx, ry, slice.positions.array as Float32Array);
    slice.positions.needsUpdate = true;
    return true;
  }

  /**
   * Binds the game shaders its mesh elements name (§11.4), once: the renderer calls it as the canvas is
   * first drawn. Shaders without `ui: true` warn and are left out.
   */
  useShaders(host: UiShaderHost): void {
    this.shaderHost = host;
    if (this.shading !== undefined) return;
    const shaded = this.items.filter((item) => !!shaderOf(item.node.el.mesh));
    this.shading = shaded.length ? new ViewShading(this.model.name, this.root, this.time, shaded, host, (path) => this.env.url(path)) : null;
  }

  private syncText(item: Item): void {
    const node = item.node;
    const layout = node.layout;
    if (!layout || !layout.bounds || !node.string) {
      item.rect = null;
      item.rasterVersion = -1;
      return;
    }
    if (!node.visible) return;
    const ratio = this.env.pixelRatio();
    // The exact device scale (rounded off float noise): one raster pixel per device pixel. (Rounding it
    // up resampled every raster a little smaller, softening small text.) A resize re-rasterises. The quad
    // keeps its sub-pixel position, as Blender renders it. The scale is the rest layout's: an open or
    // loop animation (a pop) zooms the raster instead of drawing it again every frame.
    const scale = Math.round(scaleOf(node.rest) * ratio * 1e4) / 1e4;
    if (item.rasterVersion === node.layoutVersion && item.rasterScale === scale) return;
    item.rasterVersion = node.layoutVersion;
    item.rasterScale = scale;
    if (item.raster === undefined) item.raster = create2d();
    if (!item.raster) return;
    const text = node.el.text!;
    const paint: TextPaint = { family: node.family, color: text.color ?? [1, 1, 1, 1], style: node.style!, shear: text.shear || 0 };
    const quad: Bounds | null = textQuad(layout, paint);
    const drawn = quad ? rasterizeText(item.raster, layout, paint, quad, scale) : null;
    if (!quad || !drawn) {
      item.rect = null;
      return;
    }
    item.rect = [quad[0], quad[1], drawn.width / drawn.scale, drawn.height / drawn.scale];
    const canvas = item.raster.canvas;
    const texture = item.texture as CanvasTexture | null;
    if (texture && texture.image === canvas && (texture.userData.width !== drawn.width || texture.userData.height !== drawn.height)) {
      // A new size needs a new GPU texture.
      texture.dispose();
      item.texture = null;
    }
    if (!item.texture) {
      const fresh = new CanvasTexture(canvas);
      fresh.colorSpace = SRGBColorSpace;
      fresh.generateMipmaps = false;
      fresh.minFilter = LinearFilter;
      fresh.magFilter = LinearFilter;
      item.texture = fresh;
      item.material.map = fresh;
      item.material.needsUpdate = true;
    }
    item.texture.userData.width = drawn.width;
    item.texture.userData.height = drawn.height;
    item.texture.needsUpdate = true;
  }

  /** After sync(): draws the visible 3D models into their targets and places their quads (§11.1). */
  renderModels(raw: unknown): void {
    if (this.models.length === 0 || !(this.orderBase >= 0)) return;
    this.shadeModels();
    const ratio = this.env.pixelRatio();
    for (const m of this.models) m.draw(raw, this.orderBase, ratio);
  }

  /** Binds the canvas's `materials` (game shaders, §11.4) to each 3D model once its GLB is in. */
  private shadeModels(): void {
    const materials = this.model.data.materials;
    if (!this.shaderHost || !materials || typeof materials !== 'object') return;
    for (const m of this.models) {
      if (!m.model || this.modelShading.has(m)) continue;
      this.modelShading.set(m, new ModelShading(m, materials, this.time, this.shaderHost, (path) => this.env.url(path)));
    }
  }

  /** The UI clock for the 3D models: spin and clips move on by `dt` seconds. */
  stepModels(dt: number): void {
    for (const m of this.models) m.step(dt);
  }

  /** Frees the 3D models' render targets while the canvas is not drawn (they are made again when it is). */
  releaseModels(): void {
    for (const m of this.models) m.release();
  }

  dispose(): void {
    this.root.removeFromParent();
    for (const s of this.modelShading.values()) s.dispose();
    this.modelShading.clear();
    for (const m of this.models) m.dispose();
    this.models.length = 0;
    this.shading?.dispose(); // the materials the shaders started from; the items' own go below
    this.shading = null;
    for (const item of this.items) {
      item.material.dispose();
      if (item.node.el.type === 'text') item.texture?.dispose();
    }
    for (const t of this.textures.values()) t.texture.dispose();
    for (const g of this.owned) g.dispose();
    this.items.length = 0;
    this.textures.clear();
    this.owned.length = 0;
  }
}

/** The layer's scene: open canvas views stacked, plus the menu backdrop. */
export class CanvasRenderer {
  readonly scene = new Scene();
  /** Canvas CSS px, y down: left 0, right W, top 0, bottom H. */
  readonly camera = new OrthographicCamera(0, 1, 0, 1, -1000, 1000);
  readonly quad = createQuad();
  /** The open menu's backdrop (the last menu's; the first of `backdrops`). */
  readonly backdrop: Mesh;
  /** One backdrop per menu drawn: closing menus each keep theirs while they fade out. */
  private readonly backdrops: Mesh[] = [];
  private huds: CanvasView[] = [];
  /** Menus bottom to top, each over its backdrop: those playing their close animation, then the open menu. */
  private menus: CanvasView[] = [];
  private width = 0;
  private height = 0;
  /** Game shader and WebGPU warnings (each once); the layer points it at the game's. */
  warn: (message: string) => void = (message) => console.warn(message);
  private readonly warned = new Set<string>();
  /** The renderer of the last render(), for the UI shaders' `ctx.renderer`. */
  private raw: unknown = null;
  /** The views of the last setOpen: a view new to it has opened, and its shader clock starts at 0. */
  private drawn = new Set<CanvasView>();
  /** What the views' game shaders get from the layer (§11.4). */
  private readonly shaderHost: UiShaderHost = {
    warn: (message) => this.warnOnce(message),
    renderer: () => uiGameRenderer(this.raw),
  };

  constructor() {
    this.scene.name = 'gs-ui';
    this.scene.matrixAutoUpdate = false;
    this.backdrop = this.addBackdrop();
    this.setSize(1, 1);
  }

  private addBackdrop(): Mesh {
    const mesh = flatMesh(this.quad, material([0, 0, 0, 1], 0));
    mesh.name = this.backdrops.length === 0 ? 'ui:backdrop' : `ui:backdrop:${this.backdrops.length}`;
    mesh.visible = false;
    setMatrix(mesh, [this.width, 0, 0, this.height, 0, 0]);
    this.backdrops.push(mesh);
    this.scene.add(mesh);
    return mesh;
  }

  setSize(width: number, height: number): void {
    if (width === this.width && height === this.height) return;
    this.width = width;
    this.height = height;
    const camera = this.camera;
    camera.left = 0;
    camera.right = width;
    camera.top = 0;
    camera.bottom = height;
    camera.updateProjectionMatrix();
    for (const mesh of this.backdrops) setMatrix(mesh, [width, 0, 0, height, 0, 0]);
  }

  /**
   * The views to draw, bottom to top: HUDs, then each menu over its backdrop. `menus` ends with the open
   * menu; menus before it are playing their close animation.
   */
  setOpen(huds: readonly CanvasView[], menus: readonly CanvasView[]): void {
    for (const view of this.huds) view.root.removeFromParent();
    for (const view of this.menus) view.root.removeFromParent();
    this.huds = [...huds];
    this.menus = [...menus];
    for (const view of this.huds) this.scene.add(view.root);
    for (const view of this.menus) this.scene.add(view.root);
    // The open menu's backdrop is the first mesh (the one `backdrop` names); closing menus' come after.
    while (this.backdrops.length < this.menus.length) this.addBackdrop();
    this.syncBackdrops();
    // Game shaders (§11.4) are bound as a canvas is first drawn; their clock starts as it opens.
    const drawn = new Set([...this.huds, ...this.menus]);
    for (const view of drawn) {
      if (!this.drawn.has(view)) view.time.value = 0;
      view.useShaders(this.shaderHost);
    }
    this.drawn = drawn;
  }

  /** The UI clock moves the drawn canvases' game shaders on by `dt` seconds (§11.4; real time, or test steps). */
  stepShaders(dt: number): void {
    if (!(dt > 0 && Number.isFinite(dt))) return;
    for (const view of this.drawn) view.time.value += dt;
  }

  private warnOnce(message: string): void {
    if (this.warned.has(message)) return;
    this.warned.add(message);
    this.warn(message);
  }

  /** Each menu's `backdrop` colour just under it, its alpha faded with the menu's open or close animation. */
  private syncBackdrops(): void {
    const menus = this.menus;
    const open = menus.length - 1;
    this.backdrops.forEach((mesh, k) => {
      // backdrops[0] goes with the last (open) menu, backdrops[1] with the one under it, and so on.
      const i = open - k;
      const view = i >= 0 ? menus[i] : undefined;
      const color = view?.model.data.settings?.backdrop;
      const alpha = view && color ? (color[3] ?? 0) * view.model.presence : 0;
      mesh.visible = alpha > 0;
      const m = mesh.material as MeshBasicMaterial;
      if (color) m.color.setRGB(color[0] ?? 0, color[1] ?? 0, color[2] ?? 0);
      m.opacity = alpha;
      mesh.renderOrder = (this.huds.length + 2 * Math.max(0, i)) * ORDER_SLOT;
    });
  }

  /** Syncs the open views and draws them over what `raw` already drew. */
  render(raw: unknown): void {
    if (!raw || (this.huds.length === 0 && this.menus.length === 0)) return;
    this.raw = raw;
    for (let i = 0; i < this.huds.length; i++) this.huds[i]!.sync(i * ORDER_SLOT);
    for (let i = 0; i < this.menus.length; i++) this.menus[i]!.sync((this.huds.length + 2 * i + 1) * ORDER_SLOT);
    this.syncBackdrops();
    // 3D models (§11.1) render into their own targets first, before the composite below binds its own.
    for (const view of this.huds) view.renderModels(raw);
    for (const view of this.menus) view.renderModels(raw);
    const isWebGPU = (raw as { isWebGPURenderer?: boolean }).isWebGPURenderer === true;
    if (isWebGPU && this.gpuLinear !== false) {
      try {
        this.gpuLinear ??= new WebGpuComposite(this.quad, loadWebGpuNodes, (message) => this.warnOnce(message));
        if (this.gpuLinear.render(raw as WebGpuRaw, this.scene, this.camera, this.width, this.height)) return;
      } catch (error) {
        // Never lose the UI over it: draw straight into the canvas from now on.
        this.warnOnce(`Game UI: linear compositing failed on WebGPU; drawing the UI straight into the canvas (${error instanceof Error ? error.message : String(error)})`);
        if (this.gpuLinear) this.gpuLinear.dispose();
        this.gpuLinear = false;
      }
    }
    const gl = raw as WebGLRenderer;
    const isWebGL = (raw as { isWebGLRenderer?: boolean }).isWebGLRenderer === true;
    if (isWebGL && this.linear !== false && typeof gl.copyFramebufferToTexture === 'function') {
      try {
        this.linear ??= new LinearComposite(this.quad);
        this.linear.render(gl, this.scene, this.camera, this.width, this.height);
        return;
      } catch (error) {
        // Never lose the UI over it: draw straight into the canvas from now on (sRGB blending).
        console.warn('Game UI: linear compositing failed; blending in sRGB', error);
        this.linear?.dispose();
        this.linear = false;
      }
    }
    const r = raw as { autoClear: boolean; clearDepth?: () => void; render(scene: Scene, camera: OrthographicCamera): unknown };
    const autoClear = r.autoClear;
    r.autoClear = false;
    try {
      r.clearDepth?.();
      r.render(this.scene, this.camera);
    } finally {
      r.autoClear = autoClear;
    }
  }

  /** WebGL linear-light compositing (null: not made yet; false: unavailable, draw straight). */
  private linear: LinearComposite | null | false = null;
  /** The same on WebGPU (§11.6). */
  private gpuLinear: WebGpuComposite | null | false = null;

  dispose(): void {
    for (const view of this.huds) view.root.removeFromParent();
    for (const view of this.menus) view.root.removeFromParent();
    this.huds = [];
    this.menus = [];
    this.drawn.clear();
    for (const mesh of this.backdrops) (mesh.material as MeshBasicMaterial).dispose();
    if (this.linear) this.linear.dispose();
    this.linear = null;
    if (this.gpuLinear) this.gpuLinear.dispose();
    this.gpuLinear = null;
    this.quad.dispose();
  }
}

/** Decodes the 8-bit sRGB frame copy to linear (the sRGB target encodes it again on write). */
const DECODE_VERTEX = /* glsl */ `
varying vec2 vUv;
void main() {
  vUv = uv;
  gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
}`;
const DECODE_FRAGMENT = /* glsl */ `
uniform sampler2D frame;
varying vec2 vUv;
void main() {
  vec4 c = texture2D(frame, vUv);
  vec3 lo = c.rgb / 12.92;
  vec3 hi = pow((c.rgb + 0.055) / 1.055, vec3(2.4));
  gl_FragColor = vec4(mix(lo, hi, step(vec3(0.04045), c.rgb)), c.a);
}`;

/**
 * Draws the UI scene over the frame with blending in linear light (WebGLRenderer): the frame is copied
 * into an SRGB8_ALPHA8 target (multisampled like the canvas), where the GPU decodes, blends and encodes
 * every write; then the target goes back onto the canvas. three.js writes linear values into render
 * targets and encodes to sRGB on the canvas, so colours round-trip.
 */
class LinearComposite {
  private target: WebGLRenderTarget | null = null;
  private frame: FramebufferTexture | null = null;
  private readonly buffer = new Vector2();
  private readonly decode: ShaderMaterial;
  private readonly encode: MeshBasicMaterial;
  private readonly copyIn = new Scene();
  private readonly copyOut = new Scene();
  private readonly inQuad: Mesh;
  private readonly outQuad: Mesh;

  constructor(quad: BufferGeometry) {
    this.decode = new ShaderMaterial({
      uniforms: { frame: { value: null } },
      vertexShader: DECODE_VERTEX,
      fragmentShader: DECODE_FRAGMENT,
      blending: NoBlending,
      depthTest: false,
      depthWrite: false,
      toneMapped: false,
      side: DoubleSide, // the y-down camera mirrors the quads
    });
    this.encode = new MeshBasicMaterial({ blending: NoBlending, depthTest: false, depthWrite: false, toneMapped: false, side: DoubleSide });
    this.inQuad = new Mesh(quad, this.decode);
    this.outQuad = flatMesh(quad, this.encode);
    this.inQuad.matrixAutoUpdate = false;
    this.inQuad.frustumCulled = false;
    this.copyIn.add(this.inQuad);
    this.copyOut.add(this.outQuad);
    this.copyIn.matrixAutoUpdate = false;
    this.copyOut.matrixAutoUpdate = false;
  }

  /** Draws `scene` over the canvas's frame; `width` × `height` is the canvas in CSS px (the camera's space). */
  render(gl: WebGLRenderer, scene: Scene, camera: OrthographicCamera, width: number, height: number): void {
    const size = gl.getDrawingBufferSize(this.buffer);
    const w = Math.max(1, Math.floor(size.x));
    const h = Math.max(1, Math.floor(size.y));
    if (!this.target) {
      this.target = new WebGLRenderTarget(w, h, {
        colorSpace: SRGBColorSpace,
        depthBuffer: false,
        generateMipmaps: false,
        minFilter: NearestFilter,
        magFilter: NearestFilter,
        samples: gl.getContextAttributes?.()?.antialias === false ? 0 : 4,
      });
      this.encode.map = this.target.texture;
      this.encode.needsUpdate = true;
    } else if (this.target.width !== w || this.target.height !== h) {
      this.target.setSize(w, h);
    }
    if (!this.frame || this.frame.image.width !== w || this.frame.image.height !== h) {
      this.frame?.dispose();
      this.frame = new FramebufferTexture(w, h);
      this.decode.uniforms.frame!.value = this.frame;
    }
    // Both quads cover the canvas.
    setMatrix(this.inQuad, [width, 0, 0, height, 0, 0]);
    setMatrix(this.outQuad, [width, 0, 0, height, 0, 0]);
    const previous = gl.getRenderTarget();
    const autoClear = gl.autoClear;
    gl.autoClear = false;
    try {
      gl.setRenderTarget(null);
      gl.copyFramebufferToTexture(this.frame);
      gl.setRenderTarget(this.target);
      gl.render(this.copyIn, camera);
      gl.render(scene, camera);
      gl.setRenderTarget(previous);
      gl.render(this.copyOut, camera);
    } finally {
      gl.setRenderTarget(previous);
      gl.autoClear = autoClear;
    }
  }

  dispose(): void {
    this.target?.dispose();
    this.frame?.dispose();
    this.decode.dispose();
    this.encode.dispose();
    this.target = null;
    this.frame = null;
  }
}

// ---------------------------------------------------------------------------
// Game shaders on UI meshes (GAME-UI §11.4)

/** What a canvas's game shaders get from the layer. */
export interface UiShaderHost {
  /** Warns once per message (Blender's console and the MCP logs). */
  warn(message: string): void;
  /** `ctx.renderer`: the renderer the canvases were last drawn with. */
  renderer(): GameRenderer;
}

/** The game shader a mesh element's material names (§3.5, §11.4), or null. */
export function shaderOf(mesh: UiMeshData | undefined): { name: string; values: Record<string, unknown> } | null {
  const shader = mesh?.material?.shader;
  if (!shader || typeof shader.name !== 'string' || shader.name === '') return null;
  const values = shader.values && typeof shader.values === 'object' && !Array.isArray(shader.values) ? shader.values : {};
  return { name: shader.name, values };
}

/**
 * Loads the code of the game shaders a canvas uses (their `load` modules): the UI-capable ones its meshes
 * name and those of its 3D models' `materials`, so the canvas opens with them applied. Never rejects: a
 * module that fails is reported when the shader is applied.
 */
export function loadUiShaders(data: UiCanvasData): Promise<void> {
  const jobs: Promise<unknown>[] = [];
  const seen = new Set<string>();
  const want = (name: string | undefined, uiOnly: boolean): void => {
    if (!name || seen.has(name)) return;
    const shader = getShader(name);
    if (!shader?.load || (uiOnly && !shader.ui)) return;
    seen.add(name);
    jobs.push(loadShader(shader).catch(() => undefined));
  };
  for (const el of data.elements ?? []) if (el.type === 'mesh') want(shaderOf(el.mesh)?.name, true);
  const materials = data.materials && typeof data.materials === 'object' ? data.materials : {};
  for (const entry of Object.values(materials)) want(typeof entry?.shader === 'string' ? entry.shader : undefined, false);
  return Promise.all(jobs).then(() => undefined);
}

/** `ctx.renderer` for UI shaders: the canvases' renderer as a GameRenderer (the layer only has the three.js one). */
function uiGameRenderer(raw: unknown): GameRenderer {
  const r = raw as (GameRenderer['raw'] & { isWebGPURenderer?: boolean }) | null;
  return {
    kind: r?.isWebGPURenderer ? 'webgpu' : 'webgl',
    domElement: r?.domElement as HTMLCanvasElement,
    raw: r as GameRenderer['raw'],
    setSize() {},
    render(scene, camera) {
      r?.render(scene, camera);
    },
    createEnvironment() {
      throw new Error('Game UI shaders cannot make an environment');
    },
    dispose() {},
  };
}

/**
 * The game shaders of one canvas's mesh elements, bound with the same machinery as a level's materials
 * (MaterialShaders: `load` modules, uniforms, asset textures, a fresh copy per apply, the Blender look
 * when a shader throws), with `ctx.ui` true and `ctx.time` the canvas's clock. Each mesh's own material
 * is named `<Canvas>/<Element>`, the name in warnings. A shader (re)defined later, such as a project
 * file saved, binds them all again from the materials the canvas built.
 */
class ViewShading {
  private readonly binder: MaterialShaders;
  private readonly shaded: Array<{ item: Item; base: MeshBasicMaterial; shader: string; values: Record<string, unknown> }> = [];
  private readonly unsubscribe: () => void;

  constructor(
    canvas: string,
    private readonly root: Group,
    time: { value: number },
    items: readonly Item[],
    private readonly host: UiShaderHost,
    url: (path: string) => string,
  ) {
    for (const item of items) {
      const shader = shaderOf(item.node.el.mesh)!;
      item.material.name = `${canvas}/${item.node.el.name}`;
      this.shaded.push({ item, base: item.material, shader: shader.name, values: shader.values });
    }
    this.binder = new MaterialShaders({
      renderer: () => host.renderer(),
      time,
      assetUrl: url,
      warn: (message) => host.warn(`Game UI: ${message}`),
      ui: true,
    });
    this.bind();
    const names = new Set(this.shaded.map((s) => s.shader));
    this.unsubscribe = onShaderDefined((shader) => {
      if (names.has(shader.name)) this.bind();
    });
  }

  /** Puts every mesh back on the material the canvas built and binds the shaders that work on UI meshes. */
  private bind(): void {
    const materials: Record<string, ManifestMaterial> = {};
    for (const s of this.shaded) {
      const current = s.item.mesh.material as MeshBasicMaterial;
      if (current !== s.base) {
        s.item.mesh.material = s.base;
        current.dispose();
      }
      s.item.material = s.base;
      const shader = getShader(s.shader);
      if (shader && !shader.ui) {
        this.host.warn(
          `Game UI: game shader "${shader.name}" is not made for UI meshes (its definition has no ui: true), so "${s.base.name}" draws without it. ` +
            'Shine and Gradient are; a project shader can say ui: true',
        );
        continue;
      }
      materials[s.base.name] = { shader: s.shader, values: s.values };
    }
    this.binder.bind([this.root], materials, () => [this.root]);
  }

  /** Points each shaded item at the material its mesh draws now (a shader replaced it); true when one changed. */
  swapped(): boolean {
    let changed = false;
    for (const s of this.shaded) {
      const current = s.item.mesh.material as MeshBasicMaterial;
      if (current === s.item.material) continue;
      s.item.material = current;
      changed = true;
    }
    return changed;
  }

  /** Disposes the materials the shaders started from (the view disposes what the meshes draw) and the shaders' textures. */
  dispose(): void {
    this.unsubscribe();
    for (const s of this.shaded) if (s.item.mesh.material !== s.base) s.base.dispose();
    this.binder.bind([], {}, () => []);
  }
}

const materialsOf = (object: Object3D): Material[] => {
  const m = (object as { material?: Material | Material[] }).material;
  return Array.isArray(m) ? m : m ? [m] : [];
};

/**
 * The game shaders of one 3D model's materials (§11.1, §11.4): the canvas's `materials` (GLB material
 * name -> shader and values, as in scene manifests), bound to the model's copy as a level's materials
 * are. Any shader works: these are lit 3D materials (`ctx.ui` false), on the canvas's clock. The copy
 * shares its materials with the GLB's parse, which stay as they are: the shaders draw copies of them. A
 * redefined shader applies again; a model drawing shaders is drawn every frame (ModelItem.shaded).
 */
class ModelShading {
  private readonly binder: MaterialShaders;
  /** The parse's materials the copy drew (never disposed here). */
  private readonly originals = new Set<Material>();
  private readonly root: Object3D;
  private readonly unsubscribe: () => void;

  constructor(
    item: ModelItem,
    materials: Readonly<Record<string, ManifestMaterial>>,
    time: { value: number },
    host: UiShaderHost,
    url: (path: string) => string,
  ) {
    const root = item.model!;
    this.root = root;
    const names = new Set<string>();
    root.traverse((o) => {
      for (const m of materialsOf(o)) {
        this.originals.add(m);
        names.add(m.name);
      }
    });
    // Only the materials this model draws: the map covers every model of the canvas.
    const own: Record<string, ManifestMaterial> = {};
    for (const [name, entry] of Object.entries(materials)) {
      if (names.has(name) && entry && typeof entry.shader === 'string' && entry.shader) own[name] = { shader: entry.shader, values: entry.values ?? {} };
    }
    this.binder = new MaterialShaders({
      renderer: () => host.renderer(),
      time,
      assetUrl: url,
      warn: (message) => host.warn(`Game UI: ${item.label}: ${message}`),
    });
    this.binder.bind([root], own, () => [root]);
    item.shaded = Object.keys(own).length > 0;
    const shaders = new Set(Object.values(own).map((e) => e.shader));
    this.unsubscribe = onShaderDefined((shader) => {
      if (shaders.has(shader.name)) this.binder.redefine(shader);
    });
  }

  /** Disposes the shaders' copies (what the model draws now that is not the parse's) and their textures. */
  dispose(): void {
    this.unsubscribe();
    const made = new Set<Material>();
    this.root.traverse((o) => {
      for (const m of materialsOf(o)) if (!this.originals.has(m)) made.add(m);
    });
    for (const m of made) m.dispose();
    this.binder.bind([], {}, () => []);
  }
}

// ---------------------------------------------------------------------------
// Linear-light compositing on WebGPU (GAME-UI §11.6)

/** The parts of `three/webgpu` the WebGPU composite uses (the game has loaded that module already). */
export interface WebGpuNodes {
  NodeMaterial: new () => Material & { fragmentNode: unknown };
  TSL: {
    texture(value: Texture): unknown;
    renderOutput(color: unknown, toneMapping?: ToneMapping | null, outputColorSpace?: string | null): unknown;
  };
}

/** The WebGPURenderer calls the composite makes. */
export interface WebGpuRaw {
  readonly isWebGPURenderer: true;
  autoClear: boolean;
  toneMapping: ToneMapping;
  outputColorSpace: ColorSpace;
  /** True when three draws the frame into its linear frame buffer target, then tone maps and encodes it onto the canvas. */
  readonly needsFrameBufferTarget?: boolean;
  readonly samples?: number;
  getDrawingBufferSize(target: Vector2): Vector2;
  getRenderTarget(): unknown;
  setRenderTarget(target: RenderTarget | null): void;
  copyFramebufferToTexture(texture: Texture): void;
  render(scene: Object3D, camera: Camera): unknown;
}

export const loadWebGpuNodes = (): Promise<WebGpuNodes> => import('three/webgpu') as unknown as Promise<WebGpuNodes>;

type NodeMaterialLike = Material & { fragmentNode: unknown };

/**
 * Draws the UI scene over the frame in linear light on WebGPURenderer, as LinearComposite does on WebGL.
 * WebGPURenderer draws a frame into a linear (half-float) frame buffer target and tone maps and encodes
 * it onto the canvas in an output pass; drawn straight, the UI went through that pass too, tone mapped
 * like the scene. Here:
 *
 * 1. the frame buffer target (the scene, linear, before tone mapping) is copied into a texture;
 * 2. a quad draws it, tone mapped as the output pass does it but kept linear, into a half-float target;
 * 3. the UI scene is drawn into that target, blending in linear light (and not tone mapped);
 * 4. a quad encodes the target to the output colour space onto the canvas, with the renderer's tone
 *    mapping and output colour space switched off meanwhile, as three's RenderPipeline does.
 *
 * The node materials come from `three/webgpu`, loaded on first use: until it has loaded, and where the
 * frame has no frame buffer target, `render` returns false and the caller draws straight.
 */
export class WebGpuComposite {
  private nodes: WebGpuNodes | null = null;
  private loading: Promise<void> | null = null;
  private failed = false;
  private target: RenderTarget | null = null;
  private frame: FramebufferTexture | null = null;
  private base: NodeMaterialLike | null = null;
  private out: NodeMaterialLike | null = null;
  /** The tone mapping and frame texture `base` was built for; the output colour space `out` was. */
  private baseFor: { mapping: ToneMapping; frame: Texture } | null = null;
  private outFor: ColorSpace | null = null;
  private readonly buffer = new Vector2();
  private readonly copyIn = new Scene();
  private readonly copyOut = new Scene();
  private readonly inQuad: Mesh;
  private readonly outQuad: Mesh;

  constructor(
    quad: BufferGeometry,
    private readonly load: () => Promise<WebGpuNodes>,
    private readonly warn: (message: string) => void,
  ) {
    this.inQuad = flatMesh(quad, new MeshBasicMaterial());
    this.outQuad = flatMesh(quad, new MeshBasicMaterial());
    this.copyIn.add(this.inQuad);
    this.copyOut.add(this.outQuad);
    this.copyIn.matrixAutoUpdate = false;
    this.copyOut.matrixAutoUpdate = false;
  }

  /** True once `three/webgpu` has loaded. */
  get ready(): boolean {
    return this.nodes !== null;
  }

  /** Starts loading `three/webgpu`; resolves when it has loaded or failed (then it warns, and `render` stays false). */
  prepare(): Promise<void> {
    if (this.loading) return this.loading;
    if (this.nodes || this.failed) return Promise.resolve();
    this.loading = Promise.resolve()
      .then(() => this.load())
      .then((nodes) => {
        if (typeof nodes?.NodeMaterial !== 'function' || typeof nodes.TSL?.texture !== 'function' || typeof nodes.TSL.renderOutput !== 'function') {
          throw new Error('it has no NodeMaterial or TSL');
        }
        this.nodes = nodes;
      })
      .catch((error: unknown) => {
        this.failed = true;
        this.warn(`Game UI: could not load three/webgpu for linear compositing; drawing the UI straight into the canvas (${error instanceof Error ? error.message : String(error)})`);
      })
      .finally(() => {
        this.loading = null;
      });
    return this.loading;
  }

  /**
   * Draws `scene` over the canvas's frame (`width` × `height` CSS px, the camera's space). False when it
   * cannot (yet): the caller draws straight. Throws when a renderer call fails.
   */
  render(raw: WebGpuRaw, scene: Scene, camera: OrthographicCamera, width: number, height: number): boolean {
    if (!this.nodes) {
      void this.prepare();
      return false;
    }
    // The frame is in the frame buffer target only when the renderer converts its output (always with an
    // sRGB canvas), and the UI goes onto the canvas, not into a target someone set.
    if (raw.needsFrameBufferTarget !== true || raw.getRenderTarget() != null) return false;
    const size = raw.getDrawingBufferSize(this.buffer);
    const w = Math.max(1, Math.floor(size.x));
    const h = Math.max(1, Math.floor(size.y));
    const mapping = raw.toneMapping;
    const space = raw.outputColorSpace;
    const target = this.ensureTarget(w, h, raw.samples ?? 0);
    const frame = this.ensureFrame(w, h);
    this.ensureMaterials(mapping, space, frame, target);
    setMatrix(this.inQuad, [width, 0, 0, height, 0, 0]);
    setMatrix(this.outQuad, [width, 0, 0, height, 0, 0]);
    const autoClear = raw.autoClear;
    try {
      raw.copyFramebufferToTexture(frame);
      raw.autoClear = false;
      raw.setRenderTarget(target);
      raw.render(this.copyIn, camera);
      raw.render(scene, camera);
      raw.setRenderTarget(null);
      // Straight onto the canvas: `out` encodes it itself.
      raw.toneMapping = NoToneMapping;
      raw.outputColorSpace = ColorManagement.workingColorSpace as ColorSpace;
      raw.render(this.copyOut, camera);
    } finally {
      raw.toneMapping = mapping;
      raw.outputColorSpace = space;
      raw.setRenderTarget(null);
      raw.autoClear = autoClear;
    }
    return true;
  }

  private ensureTarget(w: number, h: number, samples: number): RenderTarget {
    if (!this.target || this.target.samples !== samples) {
      this.target?.dispose();
      this.target = new RenderTarget(w, h, {
        type: HalfFloatType,
        depthBuffer: false,
        generateMipmaps: false,
        minFilter: NearestFilter,
        magFilter: NearestFilter,
        samples,
      });
      this.outFor = null;
    } else if (this.target.width !== w || this.target.height !== h) {
      this.target.setSize(w, h);
    }
    return this.target;
  }

  private ensureFrame(w: number, h: number): FramebufferTexture {
    if (!this.frame || this.frame.image.width !== w || this.frame.image.height !== h) {
      this.frame?.dispose();
      this.frame = new FramebufferTexture(w, h);
    }
    return this.frame;
  }

  private ensureMaterials(mapping: ToneMapping, space: ColorSpace, frame: Texture, target: RenderTarget): void {
    const { NodeMaterial, TSL } = this.nodes!;
    if (!this.baseFor || this.baseFor.mapping !== mapping || this.baseFor.frame !== frame) {
      this.base?.dispose();
      // Tone mapped (with the renderer's exposure) as the output pass does, but left linear.
      this.base = flat(new NodeMaterial());
      this.base.fragmentNode = TSL.renderOutput(TSL.texture(frame), mapping, ColorManagement.workingColorSpace);
      this.inQuad.material = this.base;
      this.baseFor = { mapping, frame };
    }
    if (this.outFor !== space) {
      this.out?.dispose();
      this.out = flat(new NodeMaterial());
      this.out.fragmentNode = TSL.renderOutput(TSL.texture(target.texture), NoToneMapping, space);
      this.outQuad.material = this.out;
      this.outFor = space;
    }
  }

  dispose(): void {
    this.target?.dispose();
    this.frame?.dispose();
    this.base?.dispose();
    this.out?.dispose();
    (this.inQuad.material as Material).dispose();
    (this.outQuad.material as Material).dispose();
    this.target = null;
    this.frame = null;
    this.base = null;
    this.out = null;
    this.baseFor = null;
    this.outFor = null;
  }
}

/** A copy quad's material: replaces what is under it, both sides (the y-down camera mirrors quads), no depth. */
function flat<M extends Material>(m: M): M {
  m.blending = NoBlending;
  m.depthTest = false;
  m.depthWrite = false;
  m.side = DoubleSide;
  m.toneMapped = false;
  return m;
}
