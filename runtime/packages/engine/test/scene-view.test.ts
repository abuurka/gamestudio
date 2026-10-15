// The Scene view (game.setView, view.mode / view.camera / material.values, SPEC §5.4): nothing
// simulates, the editor's camera matrices are used as given and kept, game shaders' clock runs with
// real time, menus and the HUD hide, the selection is outlined in Blender's colours, and Play/Stop
// round-trip as from a stopped game.
import { mkdtempSync, readFileSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import {
  Bone,
  BoxGeometry,
  Color,
  DataTexture,
  Float32BufferAttribute,
  Group,
  InstancedMesh,
  Matrix4,
  Mesh,
  MeshStandardMaterial,
  OrthographicCamera,
  PerspectiveCamera,
  Raycaster,
  Scene,
  Skeleton,
  SkinnedMesh,
  Uint16BufferAttribute,
  Vector3,
  WebGPUCoordinateSystem,
  type Camera,
  type Texture,
  type ShaderMaterial,
} from 'three';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import type { SystemContext } from '../src/context';
import { defineSystem, removeSystem } from '../src/ecs/system';
import { World } from '../src/ecs/world';
import { EditorCamera, parseViewCamera } from '../src/game/editor-camera';
import { Game, type GameOptions } from '../src/game/game';
import { ACTIVE_COLOR, OUTLINE_WIDTH, SELECTED_COLOR, Selection, isSelectionPass, outlineMaterial } from '../src/game/selection';
import { FIXED_DT } from '../src/game/stepper';
import { handleEditorMessage } from '../src/link/handlers';
import { RapierPhysics } from '../src/physics/rapier-backend';
import { MaterialShaders } from '../src/scene/materials';
import { isShaderPass } from '../src/scene/passes';
import { defineShader, field, patchShader } from '../src/schema';
import '../src/systems';
import { installDom, type FakeDom, type FakeElement } from './dom';
import { sceneFiles } from './game-fixture';
import { FIXTURE_DIR } from './helpers';
import { resolveIncludes } from './library/helpers';

// "sv": the basic fixture with a game shader on the coin's material.
const SV = '/export/sv.scene.json';
const dir = mkdtempSync(path.join(tmpdir(), 'gs-scene-view-'));
{
  const m = JSON.parse(readFileSync(`${FIXTURE_DIR}basic.scene.json`, 'utf8'));
  writeFileSync(path.join(dir, 'sv.scene.json'), JSON.stringify({ ...m, scene: 'sv', materials: { CoinMat: { shader: 'SvProbe', values: { glow: 0.5 } } } }));
  const sky = { horizon: [0.5, 0.7, 0.9], zenith: [0.05, 0.25, 0.7], ground: [0.3, 0.5, 0.1] };
  writeFileSync(path.join(dir, 'svsky.scene.json'), JSON.stringify({ ...m, scene: 'svsky', settings: { ...m.settings, sky } }));
}
const SKY = '/export/svsky.scene.json';

const COIN = 'gs_000000a00006';
const CRATE = 'gs_000000a00002';
const PLAYER = 'gs_000000a00005';

let dom: FakeDom;
/** What the probe shader's apply saw last: the shared clock and the instance's uniforms. */
let probe: { time: { value: number }; uniforms: Record<string, { value: unknown }> } | null;
let updates: number;
let game: Game | null = null;

beforeAll(async () => {
  await new RapierPhysics().init();
});
beforeEach(() => {
  dom = installDom({ ...sceneFiles(), [SV]: path.join(dir, 'sv.scene.json'), [SKY]: path.join(dir, 'svsky.scene.json') });
  probe = null;
  updates = 0;
  defineShader('SvProbe', {
    fields: { glow: field.float(0), mode: field.enum(['a', 'b'], 'a') },
    apply(material, _values, ctx) {
      probe = { time: ctx.time, uniforms: ctx.uniforms as never };
      patchShader(material, 'svprobe', (shader) => void (shader.uniforms.uGlow = ctx.uniforms.glow));
    },
  });
  defineSystem({ name: 'SvCounter', update: () => void updates++ });
});
afterEach(() => {
  game?.dispose();
  game = null;
  removeSystem('SvCounter');
  removeSystem('SvMenus');
  vi.restoreAllMocks();
  dom.restore();
});

/** A game on "sv". Not held: frames come from `frame(now)` (the real-time loop). */
async function open(options: GameOptions = {}): Promise<Game> {
  game = new Game(dom.canvas as unknown as HTMLCanvasElement, { environment: 0, fillLight: 0, startScene: 'sv', exportBase: '/export/', ...options });
  await game.physics.init();
  await game.loadUrl(SV);
  return game;
}

/** Stands in for the renderer: records the camera of every draw. */
function recorder(g: Game): Camera[] {
  const cameras: Camera[] = [];
  g.three.renderer = {
    kind: 'webgl',
    render(scene: Scene, camera: Camera) {
      scene.updateMatrixWorld();
      cameras.push(camera);
    },
    setSize() {},
    dispose() {},
  } as never;
  return cameras;
}

/** Runs real-time frames every 16 ms from `from` to `to` (ms). */
function frames(g: Game, from: number, to: number): void {
  const frame = (g as unknown as { frame: (now: number) => void }).frame;
  for (let t = from; t <= to; t += 16) frame(t);
}

const ctxOf = (g: Game): SystemContext => (g as unknown as { ctx: SystemContext }).ctx;
const time = (g: Game): number => ctxOf(g).time;
const pose = (g: Game, id: string) => {
  const o = g.world.get(id)!.object3d;
  o.updateWorldMatrix(true, false);
  return [...o.matrixWorld.elements];
};
const visibility = (g: Game) => [g.hud.element, g.ui.overlay, (g as unknown as { fade: { element: FakeElement } }).fade.element].map((e) => e.style.visibility);

/** An editor view: from `eye` looking at the origin. */
function viewOf(eye: [number, number, number]): number[] {
  const c = new PerspectiveCamera();
  c.position.set(...eye);
  c.lookAt(0, 0, 0);
  c.updateMatrixWorld(true);
  return c.matrixWorldInverse.toArray();
}
/** Blender's window matrix for a 40° view at 2:1, with a lens shift (so it is not what three.js would build). */
function perspective(): number[] {
  const p = new PerspectiveCamera(40, 2, 0.5, 300).projectionMatrix.toArray();
  p[8] = 0.1;
  return p;
}
function orthographic(): number[] {
  return new OrthographicCamera(-8, 8, 4, -4, 0.1, 100).projectionMatrix.toArray();
}

const expectMatrix = (actual: Matrix4, expected: number[], digits = 9): void => {
  actual.toArray().forEach((v, i) => expect(v).toBeCloseTo(expected[i]!, digits));
};

describe('the Scene view', () => {
  it('stops a running game, resets it, and simulates nothing while it shows', async () => {
    const g = await open();
    recorder(g);
    expect([g.state, g.view]).toEqual(['playing', 'game']);
    frames(g, 1000, 2000); // the coin spins, the crate falls, systems run
    expect(time(g)).toBeGreaterThan(0.9);
    expect(updates).toBeGreaterThan(50);
    const seen: string[] = [];
    g.onViewChange = (view) => void seen.push(view);
    g.setView('scene');
    await g.idle();
    expect([g.state, g.view, seen]).toEqual(['stopped', 'scene', ['scene']]);
    expect(time(g)).toBe(0);
    const coin = pose(g, COIN);
    const crate = pose(g, CRATE);
    const player = pose(g, PLAYER);
    updates = 0;
    frames(g, 3000, 5000);
    expect(time(g)).toBe(0);
    expect(updates).toBe(0);
    expect(pose(g, COIN)).toEqual(coin);
    expect(pose(g, CRATE)).toEqual(crate);
    expect(pose(g, PLAYER)).toEqual(player);
    // Live edits still apply.
    handleEditorMessage(g, { t: 'entity.transform', items: [{ id: COIN, p: [3, 2, 1] }] }, () => {});
    expect(g.test.positions([COIN])[COIN]).toEqual([3, 2, 1]);
  });

  it('starts there with view: "scene" (no autoplay, no title screen) and a load ends stopped', async () => {
    let titles = 0;
    defineSystem({ name: 'SvMenus', start: (ctx) => void (titles++, ctx.ui.screen('title', { title: 'Hello' })) });
    const g = await open({ view: 'scene' });
    recorder(g);
    expect([g.state, g.view]).toEqual(['stopped', 'scene']);
    expect(visibility(g)).toEqual(['hidden', 'hidden', 'hidden']);
    frames(g, 1000, 1500);
    await g.loadUrl(SV); // an edit in Blender: re-export and reload
    frames(g, 1600, 2000);
    expect([g.state, titles, g.ui.current, time(g)]).toEqual(['stopped', 0, null, 0]);
  });

  it('ends the first load stopped when it is asked for while the page loads', async () => {
    game = new Game(dom.canvas as unknown as HTMLCanvasElement, { environment: 0, fillLight: 0, startScene: 'sv', exportBase: '/export/' });
    await game.physics.init();
    const loading = game.loadUrl(SV);
    expect(game.state).toBe('loading');
    game.setView('scene');
    await loading;
    expect([game.state, game.view]).toEqual(['stopped', 'scene']);
  });

  it('runs game shaders’ clock with real time while animate is on; ctx.time stays 0', async () => {
    const g = await open({ view: 'scene' });
    recorder(g);
    expect(probe!.time.value).toBe(0);
    frames(g, 1000, 2008); // 64 frames: the first counts one fixed step, then 16 ms each
    expect(probe!.time.value).toBeCloseTo(FIXED_DT + 63 * 0.016, 6);
    expect(time(g)).toBe(0);
    // A long gap (a hidden page) counts at most 0.1 s.
    const before = probe!.time.value;
    frames(g, 9000, 9000);
    expect(probe!.time.value - before).toBeCloseTo(0.1, 6);
    // animate: false freezes it; on again, it runs on from there.
    handleEditorMessage(g, { t: 'view.mode', mode: 'scene', animate: false }, () => {});
    expect(g.animate).toBe(false);
    const frozen = probe!.time.value;
    frames(g, 9016, 10000);
    expect(probe!.time.value).toBe(frozen);
    // A reload does not restart it (the water would jump on every edit).
    g.setView('scene', { animate: true });
    frames(g, 10016, 10500);
    const running = probe!.time.value;
    await g.loadUrl(SV);
    expect(probe!.time.value).toBe(running);
    // The game: shader time is game time again.
    g.setView('game');
    expect(probe!.time.value).toBe(0);
  });

  it('advances the shader clock by the test steps under a held clock', async () => {
    const g = await open({ view: 'scene' });
    g.test.hold();
    await g.test.advance(30);
    expect(probe!.time.value).toBeCloseTo(0.5, 9);
    expect([time(g), g.state]).toEqual([0, 'stopped']);
  });

  it('hides menus, the HUD and the fade, and shows them again in the game', async () => {
    const g = await open();
    g.hud.set('score', 'Score 3');
    expect(visibility(g)).toEqual(['', '', '']);
    g.setView('scene');
    expect(visibility(g)).toEqual(['hidden', 'hidden', 'hidden']);
    g.setView('game');
    expect(visibility(g)).toEqual(['', '', '']);
  });
});

describe('the editor camera (view.camera)', () => {
  it('draws with the given matrices, kept across frames, resizes and scene loads', async () => {
    const g = await open({ view: 'scene' });
    const cameras = recorder(g);
    const view = viewOf([10, 5, 10]);
    const projection = perspective();
    frames(g, 1000, 1016);
    expect(cameras.at(-1)).toBe(g.three.camera); // the game's camera until the editor sends one
    handleEditorMessage(g, { t: 'view.camera', view, projection, ortho: false, near: 0.5, far: 300 }, () => {});
    const check = (): void => {
      const camera = cameras.at(-1)! as PerspectiveCamera;
      expect(camera).not.toBe(g.three.camera);
      expect(camera.isPerspectiveCamera).toBe(true);
      expect(camera.projectionMatrix.toArray()).toEqual(projection);
      expectMatrix(camera.projectionMatrixInverse, new Matrix4().fromArray(projection).invert().toArray());
      expectMatrix(camera.matrixWorldInverse, view);
      expect(camera.getWorldPosition(new Vector3()).toArray().map((v) => +v.toFixed(6))).toEqual([10, 5, 10]);
      expect([camera.near, camera.far]).toEqual([0.5, 300]);
      expect(camera.fov).toBeCloseTo(40, 6);
      expect(camera.aspect).toBeCloseTo(2, 6);
    };
    frames(g, 1032, 1100);
    check();
    // A resize fits the game's camera, never the editor's.
    dom.canvas.clientWidth = 1000;
    dom.canvas.clientHeight = 300;
    (g as unknown as { resize(): void }).resize();
    frames(g, 1116, 1200);
    check();
    expect((g.three.camera as PerspectiveCamera).aspect).toBeCloseTo(1000 / 300, 6);
    // Anything that rebuilds the projection gets the editor's matrix again.
    (cameras.at(-1) as PerspectiveCamera).updateProjectionMatrix();
    check();
    await g.loadUrl(SV);
    frames(g, 1216, 1300);
    check();
    // Shadows follow it.
    const shadows = (g as unknown as { shadows: { update(camera?: Camera): void } }).shadows;
    const fit = vi.spyOn(shadows, 'update');
    frames(g, 1316, 1316);
    expect(fit).toHaveBeenCalledWith(cameras.at(-1));
    // The game draws with its own camera; the Scene view with the editor's again.
    g.setView('game');
    frames(g, 1332, 1332);
    expect(cameras.at(-1)).toBe(g.three.camera);
    g.setView('scene');
    await g.idle();
    frames(g, 1348, 1348);
    check();
  });

  it('takes orthographic views; near and far come from the matrix when not given', async () => {
    const g = await open({ view: 'scene' });
    const cameras = recorder(g);
    const projection = orthographic();
    expect(g.setViewCamera({ view: viewOf([0, 20, 0.001]), projection, ortho: true })).toBe(true);
    frames(g, 1000, 1016);
    const camera = cameras.at(-1)! as OrthographicCamera;
    expect(camera.isOrthographicCamera).toBe(true);
    expect(camera.projectionMatrix.toArray()).toEqual(projection);
    expect([camera.left, camera.right, camera.top, camera.bottom].map((v) => +v.toFixed(9))).toEqual([-8, 8, 4, -4]);
    expect(camera.near).toBeCloseTo(0.1, 9);
    expect(camera.far).toBeCloseTo(100, 9);
    // Back to perspective: near and far from that matrix.
    g.setViewCamera({ view: viewOf([1, 2, 3]), projection: perspective(), ortho: false });
    frames(g, 1032, 1032);
    const p = cameras.at(-1)! as PerspectiveCamera;
    expect(p.isPerspectiveCamera).toBe(true);
    expect(p.near).toBeCloseTo(0.5, 6);
    expect(p.far).toBeCloseTo(300, 4);
  });

  it('draws the gradient sky as its one colour in an orthographic view’s direction', async () => {
    const g = await open({ view: 'scene' });
    await g.loadUrl(SKY);
    const backgrounds: unknown[] = [];
    g.three.renderer = {
      kind: 'webgl',
      render: (scene: Scene) => void backgrounds.push(scene.background && (scene.background as { isColor?: boolean }).isColor ? (scene.background as Color).clone() : scene.background),
      setSize() {},
      dispose() {},
    } as never;
    const texture = g.three.scene.background;
    expect((texture as { isTexture?: boolean }).isTexture).toBe(true);
    g.setViewCamera({ view: viewOf([0, 20, 0.001]), projection: orthographic(), ortho: true }); // straight down: the ground
    frames(g, 1000, 1000);
    expect((backgrounds[0] as Color).toArray().map((v) => +v.toFixed(4))).toEqual([0.3, 0.5, 0.1]);
    expect(g.three.scene.background).toBe(texture); // only while it draws
    g.setViewCamera({ view: viewOf([10, 5, 10]), projection: perspective(), ortho: false });
    frames(g, 1016, 1016);
    expect(backgrounds[1]).toBe(texture);
  });

  it('ignores matrices that are not 16 finite numbers', () => {
    expect(parseViewCamera({ view: [1, 2, 3], projection: perspective(), ortho: false })).toBeNull();
    expect(parseViewCamera({ view: viewOf([1, 1, 1]), projection: [...perspective().slice(0, 15), Number.NaN] })).toBeNull();
    expect(parseViewCamera(null)).toBeNull();
    expect(parseViewCamera({ view: viewOf([1, 1, 1]), projection: perspective(), ortho: 1, near: 'x' })).toMatchObject({ ortho: false, near: undefined });
  });

  it('maps depth to 0..1 for the WebGPU renderer', () => {
    const editor = new EditorCamera();
    const camera = editor.set({ view: viewOf([0, 0, 5]), projection: perspective(), ortho: false }) as PerspectiveCamera;
    camera.coordinateSystem = WebGPUCoordinateSystem;
    camera.updateProjectionMatrix(); // what the WebGPU renderer does when it takes the camera
    const near = new Vector3(0, 0, -0.5).applyMatrix4(camera.projectionMatrix);
    const far = new Vector3(0, 0, -300).applyMatrix4(camera.projectionMatrix);
    expect(near.z).toBeCloseTo(0, 6);
    expect(far.z).toBeCloseTo(1, 6);
  });
});

describe('material.values', () => {
  it('sets a game shader value in place, survives Stop, and a new export replaces it', async () => {
    const g = await open({ view: 'scene' });
    const send = (): void => {};
    expect(probe!.uniforms.glow!.value).toBe(0.5);
    handleEditorMessage(g, { t: 'material.values', material: 'CoinMat', values: { glow: 2 } }, send);
    expect(probe!.uniforms.glow!.value).toBe(2);
    // Another scene's edit, and a material the scene does not shade: ignored without a warning.
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
    handleEditorMessage(g, { t: 'material.values', material: 'CoinMat', values: { glow: 7 }, scene: 'level2' }, send);
    handleEditorMessage(g, { t: 'material.values', material: 'Nope', values: { glow: 7 } }, send);
    expect(probe!.uniforms.glow!.value).toBe(2);
    // Blender sends every field of its shader; fields the bound shader lacks (it switched shaders) are left out.
    handleEditorMessage(g, { t: 'material.values', material: 'CoinMat', values: { glow: 3, diffuse: '', tile: 2 }, scene: 'sv' }, send);
    expect(probe!.uniforms.glow!.value).toBe(3);
    handleEditorMessage(g, { t: 'material.values', material: 'CoinMat', values: { glow: 2 } }, send);
    expect(warn).not.toHaveBeenCalled();
    // An enum applies the shader again, with the values so far.
    handleEditorMessage(g, { t: 'material.values', material: 'CoinMat', values: { mode: 'b' }, scene: 'sv' }, send);
    expect(ctxOf(g).shaders.get('CoinMat')).toMatchObject({ glow: 2, mode: 'b' });
    // Play and Stop rebuild the scene from the export: the edit stays.
    g.play();
    g.stop();
    await g.idle();
    expect(probe!.uniforms.glow!.value).toBe(2);
    expect(ctxOf(g).shaders.get('CoinMat')).toMatchObject({ glow: 2, mode: 'b' });
    await g.loadUrl(SV);
    expect(probe!.uniforms.glow!.value).toBe(0.5);
  });
});

describe('shadersReady', () => {
  const tick = () => new Promise((resolve) => setTimeout(resolve, 0));

  it('waits for shader modules still loading and asset textures, failed ones too', async () => {
    let release!: () => void;
    const gate = new Promise<void>((resolve) => (release = resolve));
    const applied: string[] = [];
    defineShader('SvLazy', {
      fields: { tex: field.asset(['.png']), other: field.asset(['.png']) },
      load: async () => {
        await gate;
        return { apply: (_m, values) => void applied.push(String(values.tex)) };
      },
    });
    const loads = new Map<string, { resolve(t: Texture): void; reject(e: Error): void }>();
    const shaders = new MaterialShaders({
      renderer: () => null as never,
      time: { value: 0 },
      assetUrl: (p) => `/game/${p}`,
      warn: () => {},
      loadTexture: (url) => new Promise<Texture>((resolve, reject) => void loads.set(url, { resolve, reject })),
    });
    const mesh = new Mesh(new BoxGeometry(), new MeshStandardMaterial({ name: 'Lazy' }));
    const scene = new Group().add(mesh);
    shaders.bind([scene], { Lazy: { shader: 'SvLazy', values: { tex: 'a.png', other: 'b.png' } } }, () => [scene]); // no prepare: it loads now
    let ready = false;
    void shaders.ready().then(() => void (ready = true));
    await tick();
    expect(ready).toBe(false);
    release();
    await tick();
    expect(applied).toEqual(['a.png']); // applied; its textures are loading
    expect(ready).toBe(false);
    loads.get('/game/a.png')!.resolve(new DataTexture(new Uint8Array(4), 1, 1));
    await tick();
    expect(ready).toBe(false);
    loads.get('/game/b.png')!.reject(new Error('404'));
    await shaders.ready();
    expect(ready).toBe(true);
  });

  it('is on the game: queued loads, then a redefined shader that loads lazily', async () => {
    const g = await open({ view: 'scene' });
    await g.shadersReady(); // nothing pending: resolves
    let release!: () => void;
    const gate = new Promise<void>((resolve) => (release = resolve));
    let redefined = false;
    defineShader('SvProbe', {
      fields: { glow: field.float(0) },
      load: async () => {
        await gate;
        return { apply: () => void (redefined = true) };
      },
    });
    let ready = false;
    void g.shadersReady().then(() => void (ready = true));
    await tick();
    expect([ready, redefined]).toEqual([false, false]);
    release();
    await g.shadersReady();
    expect([ready, redefined]).toEqual([true, true]);
  });
});

describe('the selection', () => {
  const outlines = (g: Game) => {
    const found: Mesh[] = [];
    g.three.scene.traverse((o) => {
      if (isSelectionPass(o) && (o as Mesh).isMesh && !(o as { isLineSegments?: boolean }).isLineSegments) found.push(o as Mesh);
    });
    return found;
  };
  const boxes = (g: Game) => g.three.scene.children.filter((o) => o.type === 'BoxHelper');
  const color = (m: Mesh) => '#' + ((m.material as ShaderMaterial).uniforms.gsColor!.value as { getHexString(): string }).getHexString();

  it('outlines in Blender’s colours in the Scene view and boxes in the game, kept across reloads', async () => {
    const g = await open();
    handleEditorMessage(g, { t: 'select', ids: [COIN, CRATE, 'gs_missing'], active: CRATE }, () => {});
    expect(boxes(g)).toHaveLength(2);
    expect(outlines(g)).toHaveLength(0);
    g.setView('scene');
    await g.idle();
    expect(boxes(g)).toHaveLength(0);
    const passes = outlines(g);
    expect(passes).toHaveLength(2);
    const coinPass = passes.find((p) => g.world.entityOf(p)?.id === COIN)!;
    const cratePass = passes.find((p) => g.world.entityOf(p)?.id === CRATE)!;
    expect(color(coinPass)).toBe(`#${SELECTED_COLOR.toString(16).padStart(6, '0')}`);
    expect(color(cratePass)).toBe(`#${ACTIVE_COLOR.toString(16).padStart(6, '0')}`);
    expect(g.selected).toEqual({ ids: [COIN, CRATE, 'gs_missing'], active: CRATE });
    for (const pass of passes) {
      const mesh = pass.parent as Mesh;
      expect(mesh.isMesh).toBe(true);
      expect(pass.geometry.getAttribute('position')).toBe(mesh.geometry.getAttribute('position'));
      expect(pass.geometry.getAttribute('outlineNormal').count).toBe(mesh.geometry.getAttribute('position').count);
      expect([pass.castShadow, isShaderPass(pass)]).toEqual([false, true]); // physics and navmeshes leave it out
      const ray = new Raycaster(new Vector3(0, 50, 0), new Vector3(0, -1, 0));
      expect(ray.intersectObject(pass, false)).toEqual([]);
    }
    // A reload builds new meshes: the outlines come back on them.
    await g.loadUrl(SV);
    expect(outlines(g)).toHaveLength(2);
    expect(outlines(g)).not.toContain(coinPass);
    // Deselect.
    handleEditorMessage(g, { t: 'select', ids: [] }, () => {});
    expect(outlines(g)).toHaveLength(0);
    // Selecting in the game view again: boxes.
    g.select([COIN]);
    g.setView('game');
    expect([boxes(g).length, outlines(g).length]).toEqual([1, 0]);
  });

  it('follows skinned meshes and GPU instances, at a constant pixel width', () => {
    const scene = new Scene();
    const world = new World(scene);
    const root = world.spawn({ name: 'Root' });
    const bone = new Bone();
    const box = new BoxGeometry();
    const n = box.getAttribute('position').count;
    box.setAttribute('skinIndex', new Uint16BufferAttribute(new Uint16Array(n * 4), 4));
    box.setAttribute('skinWeight', new Float32BufferAttribute(new Float32Array(n * 4).map((_, i) => (i % 4 === 0 ? 1 : 0)), 4));
    const skinned = new SkinnedMesh(box, new MeshStandardMaterial());
    skinned.add(bone);
    skinned.bind(new Skeleton([bone]));
    const instanced = new InstancedMesh(new BoxGeometry(), [new MeshStandardMaterial(), new MeshStandardMaterial()], 5);
    const group = new Group().add(skinned, instanced);
    root.object3d.add(group);
    const selection = new Selection({ scene, world, ownMeshes: () => [skinned, instanced] });
    selection.show('outline');
    selection.set([root.id], root.id);
    const [a, b] = selection.passes as [SkinnedMesh, InstancedMesh];
    expect(a.isSkinnedMesh).toBe(true);
    expect(a.skeleton).toBe(skinned.skeleton);
    expect(a.geometry.getAttribute('skinWeight')).toBe(box.getAttribute('skinWeight'));
    expect(a.parent).toBe(skinned);
    expect(b.isInstancedMesh).toBe(true);
    expect(b.instanceMatrix).toBe(instanced.instanceMatrix);
    expect(Array.isArray(b.material) && b.material.length).toBe(2);
    instanced.count = 3;
    const viewport = (v: { set(...n: number[]): unknown }) => v.set(0, 0, 640, 360);
    b.onBeforeRender({ getPixelRatio: () => 2, getCurrentViewport: viewport } as never, null as never, null as never, null as never, null as never, null as never);
    expect(b.count).toBe(3);
    const u = (b.material as ShaderMaterial[])[0]!.uniforms;
    expect(u.gsWidth!.value).toBe(OUTLINE_WIDTH * 2);
    expect(u.gsViewport!.value.toArray()).toEqual([640, 360]);
    // Hulls are made once per geometry and freed with the scene, sparing the shared buffers.
    const position = skinned.geometry.getAttribute('position');
    selection.release();
    expect(skinned.children.filter(isSelectionPass)).toHaveLength(0);
    expect(skinned.geometry.getAttribute('position')).toBe(position);
    selection.dispose();
  });

  it('has shaders whose includes all resolve', () => {
    const material = outlineMaterial(SELECTED_COLOR);
    const vertex = resolveIncludes(material.vertexShader);
    expect(vertex).toContain('vec3 objectNormal = outlineNormal;');
    expect(resolveIncludes(material.fragmentShader)).toContain('gl_FragColor = vec4(gsColor, 1.0);');
    expect(material.toneMapped).toBe(false);
  });
});

describe('Play and Stop from the Scene view', () => {
  it('plays as Play does (game camera, title screen, systems), stops to stopped, and view.mode returns', async () => {
    let titles = 0;
    defineSystem({ name: 'SvMenus', start: (ctx) => void (titles++, ctx.ui.screen('title', { title: 'Hello', pause: false })) });
    const g = await open({ view: 'scene' });
    const cameras = recorder(g);
    const send = (): void => {};
    handleEditorMessage(g, { t: 'view.camera', view: viewOf([4, 4, 4]), projection: perspective(), ortho: false, near: 0.5, far: 300 }, send);
    const editor = (frames(g, 1000, 1000), cameras.at(-1));
    expect(editor).not.toBe(g.three.camera);
    // Blender's Play: a fresh export, then play.start while it loads.
    const statuses: string[] = [];
    g.onViewChange = (view) => void statuses.push(`view:${view}`);
    g.onStateChange = (state) => void statuses.push(state);
    handleEditorMessage(g, { t: 'scene.load', url: SV }, send);
    for (let i = 0; i < 100 && g.state !== 'loading'; i++) await Promise.resolve(); // play.start comes while it loads
    expect(g.state).toBe('loading');
    handleEditorMessage(g, { t: 'play.start' }, send);
    await g.idle();
    expect([g.state, g.view]).toEqual(['playing', 'game']);
    expect(statuses).toEqual(['loading', 'view:game', 'playing']);
    frames(g, 2000, 2500);
    expect(titles).toBe(1);
    expect(g.ui.current).toBe('title');
    expect(visibility(g)).toEqual(['', '', '']);
    expect(cameras.at(-1)).toBe(g.three.camera);
    expect(time(g)).toBeGreaterThan(0.4);
    expect(probe!.time.value).toBe(time(g));
    // Stop: stopped, still the game view; Blender then asks for the Scene view again.
    handleEditorMessage(g, { t: 'play.stop' }, send);
    await g.idle();
    expect([g.state, g.view, time(g), g.ui.current]).toEqual(['stopped', 'game', 0, null]);
    handleEditorMessage(g, { t: 'view.mode', mode: 'scene' }, send);
    expect([g.state, g.view]).toEqual(['stopped', 'scene']);
    expect(visibility(g)).toEqual(['hidden', 'hidden', 'hidden']);
    frames(g, 3000, 3100);
    expect(cameras.at(-1)).toBe(editor);
    expect(titles).toBe(1);
    // A warm Play without a reload: straight from the loaded state.
    handleEditorMessage(g, { t: 'play.start' }, send);
    expect([g.state, g.view]).toEqual(['playing', 'game']);
  });

  it('leaves the Scene view for a playtest; view.mode waits while a test holds the clock', async () => {
    const g = await open({ view: 'scene' });
    await g.test.begin();
    expect([g.state, g.view]).toEqual(['playing', 'game']);
    handleEditorMessage(g, { t: 'view.mode', mode: 'scene' }, () => {});
    expect(g.view).toBe('game');
    // The camera still applies: it is used once the Scene view shows.
    expect(g.setViewCamera({ view: viewOf([1, 1, 1]), projection: perspective(), ortho: false })).toBe(true);
    g.test.end();
    g.setView('scene');
    await g.idle();
    expect([g.state, g.view]).toEqual(['stopped', 'scene']);
    expect(g.viewCamera).not.toBe(g.three.camera);
  });

  it('leaves it to step', async () => {
    const g = await open({ view: 'scene' });
    g.step(2);
    expect([g.state, g.view]).toEqual(['paused', 'game']);
  });
});
