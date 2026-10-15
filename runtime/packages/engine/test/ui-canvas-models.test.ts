// 3D models in canvases (docs/GAME-UI.md §11.1): settings, framing, camera placement per view, lights,
// spin and clip timing on the UI clock, render target sizes, quad order and opacity, GLB caching and
// disposal. No WebGL in Node: the renderer is a fake that records what it is asked to draw.
import {
  AnimationClip,
  BoxGeometry,
  Color,
  Group,
  HalfFloatType,
  Mesh,
  MeshStandardMaterial,
  PerspectiveCamera,
  Scene,
  UnsignedByteType,
  Vector3,
  VectorKeyframeTrack,
  type Camera,
  type Object3D,
} from 'three';
import type { GLTF } from 'three/addons/loaders/GLTFLoader.js';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import type { UiCanvasData, UiCanvasSettings, UiElementData } from '../src/ui/canvas/format';
import { createCanvasLayer, type CanvasHost, type CanvasLayer } from '../src/ui/canvas/index';
import { CanvasModel, type ModelEnv } from '../src/ui/canvas/model';
import {
  aimLights,
  bindClip,
  createLights,
  findModelNode,
  frameDistance,
  framedSphere,
  glbSource,
  LIGHT_RIGS,
  MAX_TARGET_SIZE,
  MODEL_FOV,
  modelBounds,
  modelSettings,
  modelSphere,
  placeCamera,
  screenSize,
  targetSize,
  UiModels,
  viewDirection,
  viewUp,
  type GlbSource,
  type ModelItem,
  type UiModelView,
} from '../src/ui/canvas/models3d';
import { ORDER_SLOT } from '../src/ui/canvas/renderer';
import { fallbackMeasurer } from '../src/ui/canvas/text';
import { installDom, type FakeDom } from './dom';
import { FIXTURE_DIR } from './helpers';

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

const at = (x: number, y: number, scale = 1) => ({ x, y, rotation: 0, scaleX: scale, scaleY: scale });

function modelElement(input: ElementInput & { node?: string; ui?: Record<string, unknown> }): UiElementData {
  const { node, ui, ...rest } = input;
  return element({
    type: 'model',
    model: { glb: 'Title.ui.glb', node: node ?? input.id, clips: [] },
    components: { UiModel: { size: [400, 300], view: 'front', zoom: 1, spin: 0, clip: '', light: 'studio', background: [0, 0, 0, 0], ...ui } },
    ...rest,
  });
}

function panel(input: ElementInput & { bounds: [number, number, number, number] }): UiElementData {
  const [x0, y0, x1, y1] = input.bounds;
  return element({
    type: 'mesh',
    mesh: {
      positions: [x0, y0, x1, y0, x1, y1, x0, y1],
      indices: [0, 1, 2, 0, 2, 3],
      uvs: null,
      colors: null,
      bounds: input.bounds,
      material: { color: [1, 1, 1, 1], texture: '', blend: 'alpha' },
    },
    ...input,
  });
}

function canvas(name: string, elements: UiElementData[], extra: Partial<UiCanvasData> = {}, settings: Partial<UiCanvasSettings> = {}): UiCanvasData {
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
      ...settings,
    },
    elements,
    assets: {},
    ...extra,
  };
}

function layoutModel(data: UiCanvasData, width = 1920, height = 1080): CanvasModel {
  const env: ModelEnv = { value: () => undefined, measurer: () => fallbackMeasurer, family: () => 'sans-serif', fontsVersion: () => 0 };
  const model = new CanvasModel(data, env);
  model.setViewport(width, height);
  model.refresh();
  return model;
}

/** A parsed GLB: node `gs_hero` with a 2-unit box at (1, 1, 0), and a clip moving the box up by 1 in 1 s. */
function heroGltf(): GLTF {
  const scene = new Group();
  const hero = new Group();
  hero.name = 'gs_hero';
  const body = new Mesh(new BoxGeometry(2, 2, 2), new MeshStandardMaterial());
  body.name = 'Body';
  body.position.set(1, 1, 0);
  hero.add(body);
  scene.add(hero);
  const other = new Group();
  other.name = 'Other';
  scene.add(other);
  const bounce = new AnimationClip('Bounce', 1, [new VectorKeyframeTrack('Body.position', [0, 1], [1, 1, 0, 1, 2, 0])]);
  const elsewhere = new AnimationClip('Elsewhere', 1, [new VectorKeyframeTrack('Other.position', [0, 1], [0, 0, 0, 0, 1, 0])]);
  return { scene, scenes: [scene], animations: [bounce, elsewhere], cameras: [], asset: {}, parser: null as never, userData: {} } as GLTF;
}

/** A renderer that records what is drawn where (WebGL-like when `webgl`). */
interface FakeRaw {
  isWebGLRenderer?: boolean;
  autoClear: boolean;
  toneMapping: number;
  toneMappingExposure: number;
  extensions: { has(name: string): boolean };
  target: unknown;
  clear: Color;
  clearAlpha: number;
  calls: Array<{ scene: Scene; camera: Camera; target: unknown; autoClear: boolean; clear: number[] }>;
  getRenderTarget(): unknown;
  setRenderTarget(target: unknown): void;
  getClearColor(out: Color): Color;
  getClearAlpha(): number;
  setClearColor(color: Color, alpha?: number): void;
  clearDepth(): void;
  render(scene: Scene, camera: Camera): void;
}

function fakeRaw(options: { webgl?: boolean; halfFloat?: boolean } = {}): FakeRaw {
  const raw: FakeRaw = {
    ...(options.webgl ? { isWebGLRenderer: true } : {}),
    autoClear: false,
    toneMapping: 4,
    toneMappingExposure: 1,
    extensions: { has: (name) => options.halfFloat !== false && name === 'EXT_color_buffer_float' },
    target: null,
    clear: new Color(0.1, 0.2, 0.3),
    clearAlpha: 1,
    calls: [],
    getRenderTarget: () => raw.target,
    setRenderTarget: (target) => {
      raw.target = target;
    },
    getClearColor: (out) => out.copy(raw.clear),
    getClearAlpha: () => raw.clearAlpha,
    setClearColor: (color, alpha = 1) => {
      raw.clear.copy(color);
      raw.clearAlpha = alpha;
    },
    clearDepth() {},
    render(scene, camera) {
      scene.updateMatrixWorld();
      raw.calls.push({ scene, camera, target: raw.target, autoClear: raw.autoClear, clear: [raw.clear.r, raw.clear.g, raw.clear.b, raw.clearAlpha] });
    },
  };
  return raw;
}

const modelRenders = (raw: FakeRaw) => raw.calls.filter((c) => c.scene.name.startsWith('ui-model:'));

/** A model item built straight on a laid-out canvas, with a GLB loader the test controls. */
async function itemFor(
  ui: Record<string, unknown> = {},
  options: { transform?: ReturnType<typeof at>; gltf?: GLTF; warnings?: string[] } = {},
): Promise<{ item: ModelItem; models: UiModels; data: UiCanvasData; model: CanvasModel }> {
  const data = canvas('Title', [modelElement({ id: 'Hero', node: 'gs_hero', anchor: 'center', transform: options.transform ?? at(960, 540), order: 5, ui })]);
  const model = layoutModel(data);
  const gltf = options.gltf ?? heroGltf();
  const models = new UiModels((m) => options.warnings?.push(m), async () => gltf);
  const quad = new BoxGeometry();
  const item = models.item(data, model.byId.get('Hero')!, (glb) => `http://game.test/export/ui/${glb}`, quad)!;
  await item.glb.ready;
  return { item, models, data, model };
}

describe('UiModel settings and bounds', () => {
  it('fills in defaults and rejects bad values', () => {
    const el = element({ id: 'M', type: 'model', components: { UiModel: { size: [200, -5], view: 'sideways', zoom: 0, spin: 'x', light: 'flat', background: [1, 0, 0, 3] } } });
    expect(modelSettings(el)).toEqual({ size: [200, 0], view: 'threeQuarter', zoom: 1, spin: 0.6, clip: '', light: 'flat', background: [1, 0, 0, 1] });
    expect(modelSettings(element({ id: 'N', type: 'model' }))).toMatchObject({ size: [320, 320], view: 'threeQuarter', light: 'studio', spin: 0.6 });
    expect(modelBounds(modelElement({ id: 'H' }))).toEqual([-200, -150, 200, 150]);
  });

  it('lays a model out as `size` centred on its origin, and hit-tests it only inside a button', () => {
    const data = canvas('Title', [
      modelElement({ id: 'Hero', anchor: 'center', transform: at(960, 540, 0.5) }),
      element({ id: 'Pick', anchor: 'center', transform: at(400, 540), components: { UiButton: { action: 'event' } } }),
      modelElement({ id: 'Gem', parent: 'Pick', ui: { size: [200, 200] } }),
    ]);
    const m = layoutModel(data, 960, 540); // half the design size
    expect(m.rectOf(m.byId.get('Hero')!)).toEqual({ x: 430, y: 232.5, width: 100, height: 75 });
    expect(m.info(m.byId.get('Hero')!)).toMatchObject({ type: 'model', visible: true });
    expect(m.hitTest(480, 270)).toBeNull(); // the bare model is no button
    expect(m.rectOf(m.byId.get('Pick')!)).toEqual({ x: 150, y: 220, width: 100, height: 100 });
    expect(m.hitTest(200, 270)!.el.id).toBe('Pick');
  });
});

describe('framing and cameras', () => {
  const close = (a: readonly number[], b: readonly number[]) => a.forEach((v, i) => expect(v).toBeCloseTo(b[i]!, 6));

  it('looks from Blender front, right, top and three-quarter (glTF Y-up)', () => {
    close(viewDirection('front'), [0, 0, 1]);
    close(viewDirection('side'), [1, 0, 0]);
    close(viewDirection('top'), [0, 1, 0]);
    const [x, y, z] = viewDirection('threeQuarter');
    expect(Math.hypot(x, y, z)).toBeCloseTo(1, 9);
    expect((Math.atan2(x, z) * 180) / Math.PI).toBeCloseTo(35, 6);
    expect((Math.asin(y) * 180) / Math.PI).toBeCloseTo(20, 6);
    expect(viewUp('top')).toEqual([0, 0, -1]);
    expect(viewUp('front')).toEqual([0, 1, 0]);
  });

  it('fits the sphere by the narrower field of view, and zoom divides the distance', () => {
    const fov = (MODEL_FOV * Math.PI) / 180;
    expect(frameDistance(1, fov, 1, 1)).toBeCloseTo(1 / Math.sin(fov / 2), 9);
    expect(frameDistance(1, fov, 2, 1)).toBeCloseTo(1 / Math.sin(fov / 2), 9); // wide: the height limits
    const narrow = Math.atan(Math.tan(fov / 2) * 0.5);
    expect(frameDistance(2, fov, 0.5, 1)).toBeCloseTo(2 / Math.sin(narrow), 9); // tall: the width limits
    expect(frameDistance(1, fov, 1, 2)).toBeCloseTo(frameDistance(1, fov, 1, 1) / 2, 9);
  });

  it('frames the sphere a spinning model sweeps about its up axis', () => {
    expect(framedSphere({ center: [3, 1, 4], radius: 2 }, false)).toEqual({ center: [3, 1, 4], radius: 2 });
    expect(framedSphere({ center: [3, 1, 4], radius: 2 }, true)).toEqual({ center: [0, 1, 0], radius: 7 });
    expect(framedSphere({ center: [0, 0, 0], radius: 0 }, false).radius).toBe(1);
  });

  const project = (camera: PerspectiveCamera, p: [number, number, number]) => new Vector3(...p).project(camera);

  it.each<[UiModelView, [number, number, number], 'right' | 'left' | 'up']>([
    // Blender +X (glTF +X) is right in the front view.
    ['front', [1, 0, 0], 'right'],
    // In Blender's right view, its front (−Y, glTF +Z) is on the left.
    ['side', [0, 0, 1], 'left'],
    // From above, Blender's back (+Y, glTF −Z) is up.
    ['top', [0, 0, -1], 'up'],
  ])('places the %s camera on the sphere, the sphere inside the frame', (view, offset, side) => {
    const camera = new PerspectiveCamera();
    const sphere = { center: [0.5, 1, -0.25] as [number, number, number], radius: 2 };
    const d = placeCamera(camera, view, sphere, 4 / 3, 1);
    const dir = viewDirection(view);
    close(camera.position.toArray(), [0.5 + dir[0] * d, 1 + dir[1] * d, -0.25 + dir[2] * d]);
    close(project(camera, sphere.center).toArray().slice(0, 2), [0, 0]);
    expect(camera.near).toBeLessThan(d - sphere.radius);
    expect(camera.far).toBeGreaterThan(d + sphere.radius);
    // The sphere's top touches the top of the frame (the height limits at 4:3); its side is inside.
    const forward = camera.getWorldDirection(new Vector3());
    const up = camera.up.clone().projectOnPlane(forward).normalize();
    const right = new Vector3().crossVectors(forward, up);
    const c = new Vector3(...sphere.center);
    const fov = (MODEL_FOV * Math.PI) / 180;
    // The tangent point of the frustum's top plane: the sphere's silhouette edge seen from the camera.
    const tangent = c.clone().addScaledVector(up, sphere.radius * Math.cos(fov / 2)).addScaledVector(forward, -sphere.radius * Math.sin(fov / 2));
    expect(tangent.project(camera).y).toBeCloseTo(1, 6);
    expect(Math.abs(c.clone().addScaledVector(right, sphere.radius).project(camera).x)).toBeLessThan(1);
    const p = project(camera, [sphere.center[0] + offset[0], sphere.center[1] + offset[1], sphere.center[2] + offset[2]]);
    if (side === 'right') expect(p.x).toBeGreaterThan(0.1);
    if (side === 'left') expect(p.x).toBeLessThan(-0.1);
    if (side === 'up') expect(p.y).toBeGreaterThan(0.1);
  });

  it('measures a model from its geometry, in its own frame', () => {
    const root = new Group();
    const box = new Mesh(new BoxGeometry(2, 4, 2));
    box.position.set(3, 2, 0);
    root.add(box);
    root.scale.setScalar(2);
    const s = modelSphere(root);
    close(s.center, [6, 4, 0]);
    expect(s.radius).toBeCloseTo(Math.hypot(2, 4, 2), 6);
    expect(modelSphere(new Group())).toEqual({ center: [0, 0, 0], radius: 1 });
  });
});

describe('model lights', () => {
  it('builds key, fill and rim for studio, an even rig for soft, ambient alone for flat', () => {
    const studio = createLights('studio');
    expect(studio.map((l) => [l.type, l.name])).toEqual([
      ['HemisphereLight', 'ui-light:sky'],
      ['DirectionalLight', 'ui-light:key'],
      ['DirectionalLight', 'ui-light:fill'],
      ['DirectionalLight', 'ui-light:rim'],
    ]);
    expect(createLights('soft').map((l) => l.type)).toEqual(['HemisphereLight', 'AmbientLight']);
    const flat = createLights('flat');
    expect(flat.map((l) => [l.type, l.intensity])).toEqual([['AmbientLight', Math.PI]]);
    expect(LIGHT_RIGS.flat.environment).toBe(0);
    expect(LIGHT_RIGS.studio.environment).toBeGreaterThan(0);
  });

  it.each<UiModelView>(['front', 'side', 'top', 'threeQuarter'])('keeps the key light upper left and the rim behind from the %s camera', (view) => {
    const camera = new PerspectiveCamera();
    const center: [number, number, number] = [0, 1, 0];
    const d = placeCamera(camera, view, { center, radius: 1 }, 1, 1);
    const lights = createLights('studio');
    aimLights(lights, camera, center, d);
    const inCamera = (name: string) => {
      const light = lights.find((l) => l.name === `ui-light:${name}`)!;
      return light.position.clone().applyMatrix4(camera.matrixWorldInverse);
    };
    const key = inCamera('key');
    expect(key.x).toBeLessThan(0);
    expect(key.y).toBeGreaterThan(0);
    const rim = inCamera('rim');
    expect(rim.z).toBeLessThan(-d); // beyond the model, seen from the camera
    const target = (lights[1] as unknown as { target: Object3D }).target.position.toArray();
    expect(target).toEqual(center);
  });
});

describe('render targets and clips', () => {
  it('sizes a target by the screen rect × pixel ratio, capped at 2 and at the largest side', () => {
    expect(targetSize(320, 240, 1)).toEqual([320, 240]);
    expect(targetSize(320, 240, 1.5)).toEqual([480, 360]);
    expect(targetSize(320, 240, 3)).toEqual([640, 480]);
    expect(targetSize(4000, 1000, 2)).toEqual([MAX_TARGET_SIZE, 512]);
    expect(targetSize(0.2, 0.2, 1)).toEqual([1, 1]);
    expect(targetSize(0, 100, 1)).toEqual([0, 0]);
    expect(screenSize([0, 2, -2, 0, 5, 5], [100, 50])).toEqual([200, 100]); // rotated 90° at scale 2
  });

  it('binds a clip by name or base name, to the tracks under the model', () => {
    const gltf = heroGltf();
    const hero = findModelNode(gltf.scene, 'gs_hero')!;
    expect(hero.name).toBe('gs_hero');
    expect(findModelNode(gltf.scene, 'nope')).toBeNull();
    expect(bindClip(gltf.animations, 'Bounce', hero)?.name).toBe('Bounce');
    expect(bindClip(gltf.animations, 'Bounce.001', hero)?.name).toBe('Bounce');
    expect(bindClip(gltf.animations, 'Elsewhere', hero)).toBeNull(); // animates another node
    expect(bindClip(gltf.animations, '', hero)).toBeNull();
    const mixed = new AnimationClip('Mixed', 2, [...gltf.animations[0]!.tracks, ...gltf.animations[1]!.tracks]);
    const bound = bindClip([mixed], 'Mixed', hero)!;
    expect(bound.tracks.map((t) => t.name)).toEqual(['Body.position']);
    expect(bound.duration).toBe(2);
  });

  it('requests a GLB by content hash when the canvas has one, else by revision', () => {
    const url = 'http://game.test/export/ui/Title.ui.glb';
    expect(glbSource({ rev: 4, files: { 'Title.ui.glb': 'abc123def0' } }, 'Title.ui.glb', url)).toEqual({ url: `${url}?h=abc123def0`, key: `${url}?h=abc123def0`, cache: true });
    expect(glbSource({ rev: 4 }, 'Title.ui.glb', url)).toEqual({ url: `${url}?rev=4`, key: `${url}?rev=4`, cache: false });
    expect(glbSource({ rev: 4 }, 'Title.ui.glb', `${url}?v=77`)).toEqual({ url: `${url}?v=77`, key: `${url}?v=77`, cache: true });
  });
});

describe('model items', () => {
  it('turns the model on the UI clock and loops its clip', async () => {
    const { item } = await itemFor({ spin: 1, clip: 'Bounce' });
    expect(item.model!.name).toBe('gs_hero');
    expect(item.model).not.toBe(heroGltf().scene.children[0]); // a copy
    const body = item.model!.getObjectByName('Body')!;
    expect(body.position.y).toBeCloseTo(1, 6);
    item.step(0.25);
    expect(item.angle).toBeCloseTo(0.25, 9);
    expect(item.pivot.rotation.y).toBeCloseTo(0.25, 9);
    expect(body.position.y).toBeCloseTo(1.25, 6);
    item.step(1);
    expect(item.angle).toBeCloseTo(1.25, 9);
    expect(body.position.y).toBeCloseTo(1.25, 6); // looped
    item.step(0); // a frame without time: nothing moves
    item.step(Number.NaN);
    expect(item.angle).toBeCloseTo(1.25, 9);
    for (let i = 0; i < 7; i++) item.step(1);
    expect(item.angle).toBeLessThan(Math.PI * 2);
    expect(item.angle).toBeCloseTo((1.25 + 7) % (Math.PI * 2), 9);
  });

  it('frames the swept sphere of a spinning model, the model sphere of a still one', async () => {
    const still = await itemFor({ spin: 0 });
    // The box at (1, 1, 0), 2 units wide: centre (1, 1, 0), radius √3.
    expect(still.item.sphere!.center).toEqual([1, 1, 0]);
    expect(still.item.sphere!.radius).toBeCloseTo(Math.sqrt(3), 6);
    const spinning = await itemFor({ spin: 0.5 });
    expect(spinning.item.sphere!.center).toEqual([0, 1, 0]);
    expect(spinning.item.sphere!.radius).toBeCloseTo(Math.sqrt(3) + 1, 6);
  });

  it('warns about a missing node or clip', async () => {
    const warnings: string[] = [];
    await itemFor({ clip: 'Dance' }, { warnings });
    expect(warnings).toEqual(['Game UI: "Title/Hero": clip "Dance" does not animate its 3D model (it has no clips)']);
    warnings.length = 0;
    const scene = new Group();
    const { item } = await itemFor({}, { warnings, gltf: { scene, scenes: [scene], animations: [] } as unknown as GLTF });
    expect(item.model).toBeNull();
    expect(warnings).toEqual(['Game UI: "Title/Hero": the 3D model\'s node "gs_hero" is not in its GLB; export the UI file again']);
  });

  it('renders into a target sized to the element on screen, then shows it at its draw order', async () => {
    const { item, model } = await itemFor({ background: [0.2, 0.4, 0.6, 0.5] }, { transform: at(960, 540, 0.5) });
    const raw = fakeRaw({ webgl: true });
    item.draw(raw, 3 * ORDER_SLOT, 2);
    // 400 × 300 design px at 0.5 on a 1920 × 1080 canvas: 200 × 150 CSS px, × 2.
    expect([item.target!.width, item.target!.height]).toEqual([400, 300]);
    expect(item.target!.texture.type).toBe(HalfFloatType);
    expect(item.target!.samples).toBe(4);
    expect((item.target as unknown as { isXRRenderTarget?: boolean }).isXRRenderTarget).toBe(true);
    expect(item.material.map).toBe(item.target!.texture);
    const renders = modelRenders(raw);
    expect(renders).toHaveLength(1);
    expect(renders[0]).toMatchObject({ target: item.target, autoClear: true, clear: [0.2, 0.4, 0.6, 0.5] });
    expect(renders[0]!.camera).toBe(item.camera);
    expect(item.camera.aspect).toBeCloseTo(4 / 3, 9);
    // The renderer's state is back as it was.
    expect(raw.target).toBeNull();
    expect(raw.autoClear).toBe(false);
    expect(raw.clear.equals(new Color(0.1, 0.2, 0.3))).toBe(true);
    expect(raw.clearAlpha).toBe(1);
    // The quad: the element's draw order in its canvas slot, its rect on screen.
    expect(item.mesh.visible).toBe(true);
    expect(item.mesh.renderOrder).toBe(3 * ORDER_SLOT + 5);
    const e = item.mesh.matrix.elements;
    expect([e[0], e[5], e[12], e[13]]).toEqual([200, 150, 860, 465]);
    expect(model.byId.get('Hero')!.visible).toBe(true);
  });

  it('draws a still model again only when something changed; a turning one every step', async () => {
    const still = await itemFor({ spin: 0 });
    const raw = fakeRaw({ webgl: true });
    still.item.draw(raw, 0, 1);
    still.item.draw(raw, 0, 1);
    still.item.step(0.5);
    still.item.draw(raw, 0, 1);
    expect(still.item.renders).toBe(1);
    raw.toneMappingExposure = 2; // the level's exposure changed
    still.item.draw(raw, 0, 1);
    expect(still.item.renders).toBe(2);
    still.item.draw(raw, 0, 2); // a new size
    expect(still.item.renders).toBe(3);
    const turning = await itemFor({ spin: 1 });
    turning.item.draw(raw, 0, 1);
    turning.item.step(1 / 60);
    turning.item.draw(raw, 0, 1);
    turning.item.draw(raw, 0, 1);
    expect(turning.item.renders).toBe(2);
  });

  it('applies opacity to colour and alpha (premultiplied), and renders nothing hidden or transparent', async () => {
    const { item, model } = await itemFor();
    const node = model.byId.get('Hero')!;
    const raw = fakeRaw();
    node.opacity = 0.25;
    item.draw(raw, 0, 1);
    expect(item.material.opacity).toBe(0.25);
    expect(item.material.color.toArray()).toEqual([0.25, 0.25, 0.25]);
    expect((item.target as unknown as { isXRRenderTarget?: boolean }).isXRRenderTarget).toBeUndefined(); // not WebGL
    node.opacity = 0;
    item.draw(raw, 0, 1);
    expect(item.mesh.visible).toBe(false);
    node.opacity = 1;
    node.visible = false;
    item.step(1);
    item.draw(raw, 0, 1);
    expect(item.mesh.visible).toBe(false);
    expect(item.renders).toBe(1);
  });

  it('uses an 8-bit target on WebGL without float render targets', async () => {
    const { item } = await itemFor();
    item.draw(fakeRaw({ webgl: true, halfFloat: false }), 0, 1);
    expect(item.target!.texture.type).toBe(UnsignedByteType);
  });

  it('frees its target when released and everything it owns when disposed', async () => {
    const { item, models } = await itemFor({ clip: 'Bounce' });
    const raw = fakeRaw({ webgl: true });
    item.draw(raw, 0, 1);
    const target = item.target!;
    let freed = 0;
    target.addEventListener('dispose', () => freed++);
    item.release();
    expect(item.target).toBeNull();
    expect(item.mesh.visible).toBe(false);
    expect(freed).toBe(1);
    item.release(); // twice is fine
    item.draw(raw, 0, 1);
    expect(item.target).not.toBe(target); // made again when drawn
    let materialFreed = 0;
    item.material.addEventListener('dispose', () => materialFreed++);
    item.dispose();
    expect(item.target).toBeNull();
    expect(materialFreed).toBe(1);
    expect(item.mixer).toBeNull();
    expect(item.model).toBeNull();
    // The GLB stays cached until no canvas uses it.
    expect(models.keys()).toHaveLength(1);
    const gltf = item.glb.gltf!;
    let geometryFreed = 0;
    (gltf.scene.getObjectByName('Body') as Mesh).geometry.addEventListener('dispose', () => geometryFreed++);
    models.retain(new Set());
    expect(models.keys()).toEqual([]);
    expect(geometryFreed).toBe(1);
  });
});

describe('models in the canvas layer', () => {
  let dom: FakeDom;
  let layer: CanvasLayer;
  let warnings: string[];
  let fetched: string[];

  beforeEach(() => {
    dom = installDom({ '/export/files/Title.ui.glb': `${FIXTURE_DIR}basic.glb` }, [960, 540]);
    const serve = globalThis.fetch;
    fetched = [];
    globalThis.fetch = (input: RequestInfo | URL, init?: RequestInit) => {
      fetched.push(String(input));
      return serve(input, init);
    };
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

  const title = (hash = 'aaaa1111', extra: UiElementData[] = []) =>
    canvas(
      'Title',
      [
        panel({ id: 'Card', anchor: 'center', transform: at(960, 540), order: 1, bounds: [-500, -400, 500, 400] }),
        modelElement({ id: 'Crate', node: 'Crate', anchor: 'center', transform: at(960, 540), order: 2, ui: { spin: 1 } }),
        ...extra,
      ],
      { files: { 'Title.ui.glb': hash } },
    );

  function uiMeshes(raw: FakeRaw): Mesh[] {
    const ui = raw.calls.filter((c) => c.scene.name === 'gs-ui').at(-1)!;
    const out: Mesh[] = [];
    ui.scene.traverse((o) => {
      if (o instanceof Mesh && o.visible) out.push(o);
    });
    return out.sort((a, b) => a.renderOrder - b.renderOrder);
  }

  it('loads the canvas GLB once, draws the model before the UI and stacks its quad by order', async () => {
    await layer.set(title());
    expect(fetched).toEqual(['/export/files/Title.ui.glb?h=aaaa1111']);
    layer.setOpen([], 'Title');
    const raw = fakeRaw();
    layer.render(raw);
    const names = raw.calls.map((c) => c.scene.name);
    expect(names).toEqual(['ui-model:Title/Crate', 'gs-ui']);
    expect(raw.calls[0]!.target).not.toBeNull();
    expect(raw.calls[1]!.target).toBeNull();
    expect(uiMeshes(raw).map((m) => m.name)).toEqual(['Card', 'Crate']);
    // 400 × 300 at half the design size: a 200 × 150 target (devicePixelRatio 1).
    const target = raw.calls[0]!.target as { width: number; height: number };
    expect([target.width, target.height]).toEqual([200, 150]);
    expect(layer.info('Title', 'Crate')).toMatchObject({ type: 'model', rect: { x: 380, y: 195, width: 200, height: 150 } });
    expect(layer.hitTest('Title', 480, 270)).toBeNull();
    expect(warnings).toEqual([]);
  });

  it('spins drawn models on the UI clock and renders none while hidden or closed', async () => {
    await layer.set(title());
    layer.setOpen([], 'Title');
    const raw = fakeRaw();
    layer.render(raw);
    layer.update(0.5);
    layer.render(raw);
    expect(modelRenders(raw)).toHaveLength(2);
    const pivot = modelRenders(raw)[1]!.scene.children[0]!;
    expect(pivot.rotation.y).toBeCloseTo(0.5, 9);
    layer.setHidden(true);
    layer.update(0.5);
    layer.render(raw);
    expect(modelRenders(raw)).toHaveLength(2);
    layer.setHidden(false);
    layer.setOpen([], null);
    layer.update(0.5); // closed: it no longer turns
    expect(pivot.rotation.y).toBeCloseTo(1, 9);
    layer.update(0.5);
    expect(pivot.rotation.y).toBeCloseTo(1, 9);
    layer.render(raw);
    expect(modelRenders(raw)).toHaveLength(2);
  });

  it('keeps the parse across live edits with the same GLB hash, and loads a changed one', async () => {
    await layer.set(title('aaaa1111'));
    layer.setOpen([], 'Title');
    layer.render(fakeRaw());
    await layer.set({ ...title('aaaa1111'), rev: 2 });
    expect(fetched).toHaveLength(1);
    const raw = fakeRaw();
    layer.render(raw);
    expect(modelRenders(raw)).toHaveLength(1); // a new view, the same parse
    await layer.set({ ...title('bbbb2222'), rev: 3 });
    expect(fetched).toEqual(['/export/files/Title.ui.glb?h=aaaa1111', '/export/files/Title.ui.glb?h=bbbb2222']);
    layer.render(raw);
    expect(modelRenders(raw)).toHaveLength(2);
    layer.remove('Title');
    layer.render(raw);
    expect(modelRenders(raw)).toHaveLength(2);
  });

  it('reloads a GLB without a hash with every revision', async () => {
    await layer.set({ ...title(), files: undefined });
    await layer.set({ ...title(), files: undefined, rev: 2 });
    expect(fetched).toEqual(['/export/files/Title.ui.glb?rev=1', '/export/files/Title.ui.glb?rev=2']);
  });

  it('warns once about a missing GLB and draws the rest', async () => {
    await layer.set({ ...title(), elements: [...title().elements.slice(0, 1), { ...title().elements[1]!, model: { glb: 'Missing.ui.glb', node: 'Crate', clips: [] } }] });
    layer.setOpen([], 'Title');
    const raw = fakeRaw();
    layer.render(raw);
    expect(raw.calls.map((c) => c.scene.name)).toEqual(['gs-ui']);
    expect(uiMeshes(raw).map((m) => m.name)).toEqual(['Card']);
    expect(warnings).toEqual(['Game UI: could not load the 3D models in /export/files/Missing.ui.glb (HTTP 404)']);
  });

  it('a model inside a button widens what the button takes clicks on', async () => {
    await layer.set(
      canvas('Title', [
        element({ id: 'Pick', anchor: 'center', transform: at(960, 540), components: { UiButton: { action: 'event' } } }),
        modelElement({ id: 'Crate', node: 'Crate', parent: 'Pick', ui: { size: [400, 400] } }),
      ]),
    );
    expect(layer.hitTest('Title', 480, 270)!.name).toBe('Pick');
    expect(layer.buttons('Title')[0]!.rect).toEqual({ x: 380, y: 170, width: 200, height: 200 });
  });
});

describe('fake GLB loader contract', () => {
  it('passes the source to the loader and shares one load per key', async () => {
    const seen: GlbSource[] = [];
    const gltf = heroGltf();
    const models = new UiModels(
      () => {},
      async (source) => {
        seen.push(source);
        return gltf;
      },
    );
    const data = canvas('Title', [modelElement({ id: 'A', node: 'gs_hero' }), modelElement({ id: 'B', node: 'gs_hero' })], { rev: 7 });
    await models.load(data, (glb) => `http://x/${glb}`);
    await models.load(data, (glb) => `http://x/${glb}`);
    expect(seen.map((s) => s.url)).toEqual(['http://x/Title.ui.glb?rev=7']);
    models.dispose();
    expect(models.keys()).toEqual([]);
  });
});
