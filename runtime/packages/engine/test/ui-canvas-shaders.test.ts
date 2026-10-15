// Game shaders on Game UI materials (docs/GAME-UI.md §11.4): a mesh element's `mesh.material.shader`
// (UI-capable shaders only, on the canvas's clock), the canvas's `materials` on its 3D models (any shader),
// and linear-light compositing on WebGPU (§11.6). No GPU in Node: renderers are fakes that record calls.
import {
  BoxGeometry,
  Color,
  Group,
  LinearSRGBColorSpace,
  Mesh,
  MeshBasicMaterial,
  MeshStandardMaterial,
  NoToneMapping,
  RenderTarget,
  Scene,
  SRGBColorSpace,
  Vector2,
  type Camera,
  type Material,
  type Object3D,
  type OrthographicCamera,
  type Texture,
} from 'three';
import type { GLTF } from 'three/addons/loaders/GLTFLoader.js';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { defineShader, getShader, loadShader, patchShader } from '../src/schema';
import { ImageRegistry } from '../src/ui/canvas/assets';
import type { UiCanvasData, UiElementData } from '../src/ui/canvas/format';
import { createCanvasLayer, type CanvasHost, type CanvasLayer } from '../src/ui/canvas/index';
import { CanvasModel } from '../src/ui/canvas/model';
import { UiModels } from '../src/ui/canvas/models3d';
import { CanvasRenderer, CanvasView, createQuad, loadWebGpuNodes, shaderOf, WebGpuComposite, type WebGpuNodes, type WebGpuRaw } from '../src/ui/canvas/renderer';
import { fallbackMeasurer } from '../src/ui/canvas/text';
import { installDom, type FakeDom } from './dom';
import { compileMaterial } from './library/helpers';

type ElementInput = Partial<UiElementData> & { id: string };

function element(input: ElementInput): UiElementData {
  return {
    name: input.id,
    parent: null,
    type: 'group',
    visible: true,
    order: 0,
    components: {},
    ...input,
    transform: { x: 0, y: 0, rotation: 0, scaleX: 1, scaleY: 1, ...input.transform },
  };
}

const at = (x: number, y: number) => ({ x, y, rotation: 0, scaleX: 1, scaleY: 1 });

function panel(input: ElementInput & { bounds: [number, number, number, number]; shader?: { name: string; values: Record<string, unknown> }; alpha?: number }): UiElementData {
  const { bounds, shader, alpha, ...rest } = input;
  const [x0, y0, x1, y1] = bounds;
  return element({
    type: 'mesh',
    anchor: 'center',
    mesh: {
      positions: [x0, y0, x1, y0, x1, y1, x0, y1],
      indices: [0, 1, 2, 0, 2, 3],
      uvs: null,
      colors: null,
      bounds,
      material: { color: [0.2, 0.3, 0.4, alpha ?? 1], texture: '', blend: 'alpha', ...(shader ? { shader } : {}) },
    },
    ...rest,
  });
}

function canvas(name: string, elements: UiElementData[], extra: Partial<UiCanvasData> = {}): UiCanvasData {
  return {
    format: 'gamestudio.ui',
    formatVersion: 1,
    canvas: name,
    source: 'ui/ui.blend',
    rev: 1,
    exportedAt: '',
    size: [1920, 1080],
    settings: {
      kind: 'menu',
      open: 'manual',
      pause: true,
      pauseMenu: false,
      back: 'none',
      scaleMode: 'fit',
      backdrop: [0, 0, 0, 0],
      layer: 0,
      focus: null,
    },
    elements,
    assets: {},
    ...extra,
  };
}

interface FakeRaw {
  autoClear: boolean;
  scene: Scene | null;
  clearDepth(): void;
  render(scene: Scene, camera: OrthographicCamera): void;
}

function fakeRaw(): FakeRaw {
  const raw: FakeRaw = {
    autoClear: true,
    scene: null,
    clearDepth() {},
    render(scene) {
      raw.scene = scene;
      scene.updateMatrixWorld();
    },
  };
  return raw;
}

function meshNamed(scene: Object3D, name: string): Mesh {
  let found: Mesh | null = null;
  scene.traverse((o) => {
    if (!found && o instanceof Mesh && o.name === name) found = o;
  });
  if (!found) throw new Error(`no mesh ${name}`);
  return found;
}

const programKey = (material: Material) => material.customProgramCacheKey();

describe('game shaders on canvas meshes', () => {
  let dom: FakeDom;
  let warnings: string[];
  let layer: CanvasLayer;

  beforeEach(async () => {
    dom = installDom({}, [960, 540]);
    warnings = [];
    const host: CanvasHost = {
      canvas: dom.canvas as unknown as HTMLCanvasElement,
      assetUrl: (_data, path) => `/export/files/${path}`,
      value: () => undefined,
      warn: (message) => warnings.push(message),
    };
    layer = createCanvasLayer(host);
  });
  afterEach(() => {
    layer.dispose();
    dom.restore();
  });

  const shine = { name: 'Shine', values: { color: [1, 0.9, 0.5, 1], interval: 2, width: 0.3 } };

  it('reads the shader a mesh material names', () => {
    expect(shaderOf(panel({ id: 'A', bounds: [0, 0, 1, 1], shader: shine }).mesh)).toEqual(shine);
    expect(shaderOf(panel({ id: 'B', bounds: [0, 0, 1, 1] }).mesh)).toBeNull();
    expect(shaderOf({ ...panel({ id: 'C', bounds: [0, 0, 1, 1] }).mesh!, material: { color: [1, 1, 1, 1], texture: '', blend: 'alpha', shader: { name: '', values: {} } } })).toBeNull();
  });

  it('applies a UI shader to the mesh on the canvas clock, keeping the element opacity', async () => {
    await layer.set(canvas('Title', [panel({ id: 'Card', transform: at(960, 540), order: 1, bounds: [-400, -100, 400, 100], shader: shine, alpha: 0.5 })]));
    layer.setOpen([], 'Title');
    const raw = fakeRaw();
    layer.render(raw);
    const card = meshNamed(raw.scene!, 'Card');
    const material = card.material as MeshBasicMaterial;
    expect(material.name).toBe('Title/Card');
    expect(programKey(material)).toMatch(/\|gs-shine$/);
    expect(material.opacity).toBeCloseTo(0.5, 9);
    expect([material.color.r, material.color.g, material.color.b]).toEqual([0.2, 0.3, 0.4]);
    const compiled = compileMaterial(material, card);
    expect(compiled.uniforms.gsShInterval.value).toBe(2);
    expect(compiled.uniforms.gsShColor.value.toArray()).toEqual([1, 0.9, 0.5, 1]);
    // Local px are y down on UI meshes: the shader flips y back to Blender's.
    expect(compiled.uniforms.gsShFlip.value).toBe(-1);
    // ctx.time is the canvas's clock: seconds since it opened, on the UI clock.
    const time = compiled.uniforms.gsShTime as { value: number };
    expect(time.value).toBe(0);
    layer.update(0.25);
    layer.update(0.5);
    expect(time.value).toBeCloseTo(0.75, 9);
    layer.update(Number.NaN);
    expect(time.value).toBeCloseTo(0.75, 9);
    layer.setOpen([], null);
    layer.update(1);
    expect(time.value).toBeCloseTo(0.75, 9); // closed: no clock
    layer.setOpen([], 'Title');
    expect(time.value).toBe(0); // opened again: starts again
    expect(warnings).toEqual([]);
  });

  it('opens with its shader applied: set() waits for the shader code', async () => {
    await layer.set(canvas('Title', [panel({ id: 'Bar', transform: at(960, 540), bounds: [-100, -10, 100, 10], shader: { name: 'Gradient', values: { angle: 0 } } })]));
    expect(getShader('Gradient')).toBeDefined();
    layer.setOpen([], 'Title');
    const raw = fakeRaw();
    layer.render(raw);
    expect(programKey(meshNamed(raw.scene!, 'Bar').material as Material)).toMatch(/\|gs-gradient$/);
  });

  it('warns once about a shader not made for UI meshes and draws the mesh without it', async () => {
    await layer.set(
      canvas('Title', [
        panel({ id: 'A', transform: at(900, 540), bounds: [-10, -10, 10, 10], shader: { name: 'HitFlash', values: {} } }),
        panel({ id: 'B', transform: at(1000, 540), bounds: [-10, -10, 10, 10], shader: { name: 'HitFlash', values: {} } }),
      ]),
    );
    layer.setOpen([], 'Title');
    const raw = fakeRaw();
    layer.render(raw);
    layer.render(raw);
    const a = meshNamed(raw.scene!, 'A').material as MeshBasicMaterial;
    expect(programKey(a)).not.toMatch(/gs-hit-flash/);
    expect(a.isMeshBasicMaterial).toBe(true);
    expect(warnings.filter((w) => w.includes('"HitFlash" is not made for UI meshes'))).toHaveLength(2); // one per element
    expect(warnings[0]).toContain('"Title/A" draws without it');
  });

  it('warns about an unknown shader, and applies it once a project file defines it', async () => {
    await layer.set(canvas('Title', [panel({ id: 'Glow', transform: at(960, 540), bounds: [-50, -50, 50, 50], shader: { name: 'UiTestPulse', values: { rate: 3 } } })]));
    layer.setOpen([], 'Title');
    const raw = fakeRaw();
    layer.render(raw);
    expect(warnings).toEqual(['Game UI: Material "Title/Glow" uses game shader "UiTestPulse", which no src/shaders file defines']);
    const seen: Array<{ ui: boolean; time: { value: number } }> = [];
    const { field } = await import('../src/schema');
    defineShader('UiTestPulse', {
      ui: true,
      fields: { rate: field.float(1) },
      apply(material, _values, ctx) {
        seen.push({ ui: ctx.ui, time: ctx.time });
        patchShader(material, 'ui-test-pulse', () => {});
      },
    });
    layer.render(raw);
    const glow = meshNamed(raw.scene!, 'Glow').material as Material;
    expect(programKey(glow)).toMatch(/\|ui-test-pulse$/);
    expect(seen).toHaveLength(1);
    expect(seen[0]!.ui).toBe(true);
    layer.update(0.5);
    expect(seen[0]!.time.value).toBeCloseTo(0.5, 9);
    // Redefined (hot reload): applied again from the material the canvas built.
    defineShader('UiTestPulse', { ui: true, fields: { rate: field.float(1) }, apply: (m) => patchShader(m, 'ui-test-pulse-2', () => {}) });
    layer.render(raw);
    const again = meshNamed(raw.scene!, 'Glow').material as Material;
    expect(programKey(again)).toMatch(/\|ui-test-pulse-2$/);
    expect(programKey(again)).not.toMatch(/ui-test-pulse\|/);
  });

  it('frees the materials and stops listening when the canvas goes', async () => {
    await layer.set(canvas('Title', [panel({ id: 'Card', transform: at(960, 540), bounds: [-10, -10, 10, 10], shader: shine })]));
    layer.setOpen([], 'Title');
    const raw = fakeRaw();
    layer.render(raw);
    const material = meshNamed(raw.scene!, 'Card').material as Material;
    let disposed = 0;
    material.addEventListener('dispose', () => disposed++);
    layer.remove('Title');
    expect(disposed).toBeGreaterThan(0);
  });
});

/** A GLB with node `gs_hero`: a box drawing `HeroPaint`, and one drawing `Plain`. */
function heroGltf(): GLTF {
  const scene = new Group();
  const hero = new Group();
  hero.name = 'gs_hero';
  const body = new Mesh(new BoxGeometry(2, 2, 2), new MeshStandardMaterial({ name: 'HeroPaint' }));
  body.name = 'Body';
  const trim = new Mesh(new BoxGeometry(1, 1, 1), new MeshStandardMaterial({ name: 'Plain' }));
  trim.name = 'Trim';
  hero.add(body, trim);
  scene.add(hero);
  return { scene, scenes: [scene], animations: [], cameras: [], asset: {}, parser: null as never, userData: {} } as GLTF;
}

interface ModelRaw {
  isWebGPURenderer?: boolean;
  autoClear: boolean;
  toneMapping: number;
  toneMappingExposure: number;
  outputColorSpace: string;
  target: unknown;
  output: unknown;
  calls: Array<{ scene: string; target: unknown; output: unknown; space: string }>;
  getRenderTarget(): unknown;
  setRenderTarget(target: unknown): void;
  getOutputRenderTarget?(): unknown;
  setOutputRenderTarget?(target: unknown): void;
  getClearColor(out: Color): Color;
  getClearAlpha(): number;
  setClearColor(): void;
  clearDepth(): void;
  render(scene: Scene, camera: Camera): void;
}

function modelRaw(gpu: boolean): ModelRaw {
  const raw: ModelRaw = {
    ...(gpu ? { isWebGPURenderer: true } : {}),
    autoClear: true,
    toneMapping: 4,
    toneMappingExposure: 1,
    outputColorSpace: SRGBColorSpace,
    target: null,
    output: null,
    calls: [],
    getRenderTarget: () => raw.target,
    setRenderTarget: (target) => void (raw.target = target),
    ...(gpu ? { getOutputRenderTarget: () => raw.output, setOutputRenderTarget: (target: unknown) => void (raw.output = target) } : {}),
    getClearColor: (out) => out.setRGB(0, 0, 0),
    getClearAlpha: () => 1,
    setClearColor() {},
    clearDepth() {},
    render(scene) {
      scene.updateMatrixWorld();
      raw.calls.push({ scene: scene.name, target: raw.target, output: raw.output, space: raw.outputColorSpace });
    },
  };
  return raw;
}

describe('game shaders on 3D models in canvases', () => {
  async function drawn(materials: UiCanvasData['materials'], warnings: string[] = []) {
    await loadShader(getShader('Gradient')!);
    const data = canvas(
      'Title',
      [
        element({
          id: 'Hero',
          type: 'model',
          anchor: 'center',
          transform: at(960, 540),
          order: 2,
          model: { glb: 'Title.ui.glb', node: 'gs_hero', clips: [] },
          components: { UiModel: { size: [400, 300], view: 'front', zoom: 1, spin: 0, clip: '', light: 'flat', background: [0, 0, 0, 0] } },
        }),
      ],
      { materials },
    );
    const model = new CanvasModel(data, { value: () => undefined, measurer: () => fallbackMeasurer, family: () => 'sans-serif', fontsVersion: () => 0 });
    model.setViewport(1920, 1080);
    model.refresh();
    const gltf = heroGltf();
    const models = new UiModels((m) => warnings.push(m), async () => gltf);
    const quad = createQuad();
    const view = new CanvasView(model, {
      images: new ImageRegistry((m) => warnings.push(m)),
      url: (path) => `/export/files/${path}`,
      quad,
      pixelRatio: () => 1,
      model: (node) => models.item(data, node, (glb) => `/export/ui/${glb}`, quad),
    });
    await view.models[0]!.glb.ready;
    const renderer = new CanvasRenderer();
    renderer.warn = (m) => warnings.push(m);
    renderer.setOpen([], [view]);
    return { view, renderer, gltf, item: view.models[0]!, warnings };
  }

  it("binds the canvas's materials to each model's copy, any shader, leaving the GLB's materials alone", async () => {
    const { renderer, gltf, item, warnings } = await drawn({ HeroPaint: { shader: 'Gradient', values: { angle: 0 } }, Elsewhere: { shader: 'Toon', values: {} } });
    const raw = modelRaw(false);
    renderer.render(raw);
    const body = item.model!.getObjectByName('Body') as Mesh;
    const trim = item.model!.getObjectByName('Trim') as Mesh;
    const parsed = (gltf.scene.getObjectByName('Body') as Mesh).material as Material;
    expect(programKey(body.material as Material)).toMatch(/\|gs-gradient$/);
    expect(body.material).not.toBe(parsed);
    expect(programKey(parsed)).not.toMatch(/gs-gradient/);
    expect((trim.material as Material).name).toBe('Plain');
    // Lit 3D: y up, not the UI's flip; the model's clock is the canvas's.
    const compiled = compileMaterial(body.material as Material, body);
    expect(compiled.uniforms.gsGrFlip.value).toBe(1);
    expect(compiled.uniforms.gsGrAngle.value).toBe(0);
    expect(item.shaded).toBe(true);
    // Shaders may change with time: a still model is drawn every frame.
    renderer.render(raw);
    renderer.render(raw);
    expect(raw.calls.filter((c) => c.scene.startsWith('ui-model:'))).toHaveLength(3);
    expect(warnings).toEqual([]); // `Elsewhere` is another model's material: no warning here
  });

  it('a model without shaded materials stays drawn only on change, and the view frees the copies', async () => {
    const plain = await drawn({ Elsewhere: { shader: 'Gradient', values: {} } });
    const raw = modelRaw(false);
    plain.renderer.render(raw);
    plain.renderer.render(raw);
    expect(raw.calls.filter((c) => c.scene.startsWith('ui-model:'))).toHaveLength(1);
    expect(plain.item.shaded).toBe(false);

    const shaded = await drawn({ HeroPaint: { shader: 'Gradient', values: {} } });
    shaded.renderer.render(modelRaw(false));
    const body = shaded.item.model!.getObjectByName('Body') as Mesh;
    const made = body.material as Material;
    const parsed = (shaded.gltf.scene.getObjectByName('Body') as Mesh).material as Material;
    let madeDisposed = 0;
    let parsedDisposed = 0;
    made.addEventListener('dispose', () => madeDisposed++);
    parsed.addEventListener('dispose', () => parsedDisposed++);
    shaded.view.dispose();
    expect(madeDisposed).toBe(1);
    expect(parsedDisposed).toBe(0); // the GLB's parse is UiModels's
  });

  it('draws models on WebGPU into the output render target, linear, for the tone mapping', async () => {
    const { renderer, item } = await drawn(undefined);
    const raw = modelRaw(true);
    renderer.render(raw);
    const model = raw.calls.find((c) => c.scene.startsWith('ui-model:'))!;
    expect(model.target).toBeNull();
    expect(model.output).toBe(item.target);
    expect(item.target).toBeInstanceOf(RenderTarget);
    expect(model.space).toBe(LinearSRGBColorSpace);
    expect(raw.output).toBeNull();
    expect(raw.outputColorSpace).toBe(SRGBColorSpace);
  });
});

/** A WebGPURenderer stand-in that records the composite's calls. */
function gpuRaw(options: { needsFrameBufferTarget?: boolean } = {}) {
  const log: string[] = [];
  const raw = {
    isWebGPURenderer: true as const,
    autoClear: true,
    toneMapping: 4 as WebGpuRaw['toneMapping'],
    outputColorSpace: SRGBColorSpace as WebGpuRaw['outputColorSpace'],
    needsFrameBufferTarget: options.needsFrameBufferTarget ?? true,
    samples: 4,
    target: null as unknown,
    clearDepth: () => log.push('clearDepth'),
    getDrawingBufferSize: (v: Vector2) => v.set(1920, 1080),
    getRenderTarget: () => raw.target,
    setRenderTarget(target: unknown) {
      raw.target = target;
    },
    copyFramebufferToTexture(texture: Texture) {
      const image = texture.image as { width: number; height: number };
      log.push(`copy ${image.width}x${image.height}`);
    },
    render(scene: Object3D) {
      const where = raw.target === null ? 'canvas' : raw.target instanceof RenderTarget ? 'target' : 'other';
      log.push(`render ${scene.name || 'quad'} -> ${where} tone=${raw.toneMapping} space=${raw.outputColorSpace} autoClear=${raw.autoClear}`);
    },
  };
  return { raw, log };
}

const fakeNodes = (): WebGpuNodes & { built: unknown[] } => {
  const built: unknown[] = [];
  // A plain material stands in for a node material: the composite only sets its fragmentNode.
  const NodeMaterial = MeshBasicMaterial as unknown as WebGpuNodes['NodeMaterial'];
  return {
    built,
    NodeMaterial,
    TSL: {
      texture: (value) => ({ texture: value }),
      renderOutput: (color, toneMapping, space) => {
        const node = { color, toneMapping, space };
        built.push(node);
        return node;
      },
    },
  };
};

describe('linear compositing on WebGPU', () => {
  it('draws the frame tone mapped into a linear target, the UI over it, and encodes it onto the canvas', async () => {
    const nodes = fakeNodes();
    const warnings: string[] = [];
    const composite = new WebGpuComposite(createQuad(), async () => nodes, (m) => warnings.push(m));
    const { raw, log } = gpuRaw();
    const scene = new Scene();
    scene.name = 'gs-ui';
    const camera = new CanvasRenderer().camera;
    expect(composite.render(raw, scene, camera, 960, 540)).toBe(false); // loading three/webgpu: draw straight
    await composite.prepare();
    expect(composite.ready).toBe(true);
    expect(composite.render(raw, scene, camera, 960, 540)).toBe(true);
    expect(log).toEqual([
      'copy 1920x1080',
      'render quad -> target tone=4 space=srgb autoClear=false',
      'render gs-ui -> target tone=4 space=srgb autoClear=false',
      `render quad -> canvas tone=${NoToneMapping} space=srgb-linear autoClear=false`,
    ]);
    // Restored.
    expect([raw.toneMapping, raw.outputColorSpace, raw.autoClear, raw.target]).toEqual([4, SRGBColorSpace, true, null]);
    // The frame is tone mapped with the renderer's mapping but kept linear; the target is encoded only.
    const [base, out] = nodes.built as Array<{ color: { texture: Texture }; toneMapping: number; space: string }>;
    expect([base!.toneMapping, base!.space]).toEqual([4, LinearSRGBColorSpace]);
    expect([out!.toneMapping, out!.space]).toEqual([NoToneMapping, SRGBColorSpace]);
    expect(out!.color.texture.type).toBe(1016); // HalfFloatType
    // A level with other tone mapping rebuilds only the frame's material.
    raw.toneMapping = 2 as WebGpuRaw['toneMapping'];
    composite.render(raw, scene, camera, 960, 540);
    expect(nodes.built).toHaveLength(3);
    expect((nodes.built[2] as { toneMapping: number }).toneMapping).toBe(2);
    expect(warnings).toEqual([]);
    composite.dispose();
  });

  it('draws straight without a frame buffer target, into a target set, or when three/webgpu fails to load', async () => {
    const nodes = fakeNodes();
    const composite = new WebGpuComposite(createQuad(), async () => nodes, () => {});
    await composite.prepare();
    const scene = new Scene();
    const camera = new CanvasRenderer().camera;
    expect(composite.render(gpuRaw({ needsFrameBufferTarget: false }).raw, scene, camera, 10, 10)).toBe(false);
    const set = gpuRaw();
    set.raw.target = {};
    expect(composite.render(set.raw, scene, camera, 10, 10)).toBe(false);
    const warnings: string[] = [];
    const broken = new WebGpuComposite(createQuad(), async () => Promise.reject(new Error('offline')), (m) => warnings.push(m));
    await broken.prepare();
    expect(broken.ready).toBe(false);
    expect(broken.render(gpuRaw().raw, scene, camera, 10, 10)).toBe(false);
    expect(warnings).toEqual(['Game UI: could not load three/webgpu for linear compositing; drawing the UI straight into the canvas (offline)']);
  });

  it('builds its node materials with the real three/webgpu', async () => {
    const nodes = await loadWebGpuNodes();
    const composite = new WebGpuComposite(createQuad(), async () => nodes, () => {});
    await composite.prepare();
    const { raw, log } = gpuRaw();
    expect(composite.render(raw, new Scene(), new CanvasRenderer().camera, 960, 540)).toBe(true);
    expect(log).toHaveLength(4);
    composite.dispose();
  });

  it('falls back to drawing straight, with a warning, when the composite throws', async () => {
    const dom = installDom({}, [960, 540]);
    const warnings: string[] = [];
    const layer = createCanvasLayer({
      canvas: dom.canvas as unknown as HTMLCanvasElement,
      assetUrl: (_d, p) => p,
      value: () => undefined,
      warn: (m) => warnings.push(m),
    });
    try {
      await layer.set(canvas('Title', [panel({ id: 'Card', transform: at(960, 540), bounds: [-10, -10, 10, 10] })]));
      layer.setOpen([], 'Title');
      const { raw, log } = gpuRaw();
      raw.copyFramebufferToTexture = () => {
        throw new Error('lost device');
      };
      layer.render(raw); // loads three/webgpu, draws straight meanwhile
      expect(log.at(-1)).toMatch(/^render gs-ui -> canvas/);
      await new Promise((resolve) => setTimeout(resolve, 50));
      layer.render(raw);
      expect(warnings.some((w) => w.includes('linear compositing failed on WebGPU') && w.includes('lost device'))).toBe(true);
      expect(log.at(-1)).toMatch(/^render gs-ui -> canvas tone=4/);
      expect(raw.toneMapping).toBe(4);
    } finally {
      layer.dispose();
      dom.restore();
    }
  });
});
