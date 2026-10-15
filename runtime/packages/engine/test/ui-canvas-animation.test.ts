// Game UI Phase 2 at runtime (docs/GAME-UI.md §10.5, §10.6): canvas open, close and loop animations
// (track sampling, open → loop → close in the layer and the controller, opacity down the hierarchy,
// the UI clock under a test clock) and 9-slice panels (geometry and uvs at several sizes).
import { Mesh, MeshBasicMaterial, type BufferAttribute, type Object3D, type OrthographicCamera, type Scene } from 'three';
import { afterEach, beforeAll, beforeEach, describe, expect, it } from 'vitest';
import type { Game } from '../src/game/game';
import { RapierPhysics } from '../src/physics/rapier-backend';
import '../src/systems';
import type { ImageRegistry } from '../src/ui/canvas/assets';
import { CanvasAnimator, sampleTrack } from '../src/ui/canvas/animation';
import type { UiAnimation, UiAnimationTrack, UiCanvasData, UiCanvasSettings, UiElementData } from '../src/ui/canvas/format';
import { createCanvasLayer, type CanvasHost, type CanvasLayer } from '../src/ui/canvas/index';
import { CanvasModel, type ModelEnv } from '../src/ui/canvas/model';
import { CanvasView, createQuad, ORDER_SLOT, sliceStops } from '../src/ui/canvas/renderer';
import { fallbackMeasurer } from '../src/ui/canvas/text';
import { installDom, type FakeDom } from './dom';
import { heldGame, sceneFiles } from './game-fixture';
import { button, canvas as fixtureCanvas, text, uiFiles } from './ui-controller-fixtures';

// --- Builders (design size 1920 × 1080) ----------------------------------------------------

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

const at = (x: number, y: number, extra: Partial<UiElementData['transform']> = {}) => ({ x, y, rotation: 0, scaleX: 1, scaleY: 1, ...extra });

function panel(input: ElementInput & { bounds: [number, number, number, number] }, alpha = 1): UiElementData {
  const [x0, y0, x1, y1] = input.bounds;
  return element({
    type: 'mesh',
    mesh: {
      positions: [x0, y0, x1, y0, x1, y1, x0, y1],
      indices: [0, 1, 2, 0, 2, 3],
      uvs: null,
      colors: null,
      bounds: input.bounds,
      material: { color: [1, 1, 1, alpha], texture: '', blend: 'alpha' },
    },
    ...input,
  });
}

function picture(input: ElementInput & { imageOpacity: number }): UiElementData {
  return element({ type: 'image', image: { src: 'assets/ui/icon.png', rect: [-10, -10, 20, 20], opacity: input.imageOpacity, pixelated: false }, ...input });
}

function label(input: ElementInput & { body: string }): UiElementData {
  return element({
    type: 'text',
    text: {
      body: input.body,
      font: '',
      size: 40,
      color: [1, 1, 1, 1],
      align: 'center',
      valign: 'center',
      lineSpacing: 1,
      letterSpacing: 1,
      wordSpacing: 1,
      box: null,
      offset: [0, 0],
      shear: 0,
    },
    ...input,
  });
}

function design(name: string, elements: UiElementData[], settings: Partial<UiCanvasSettings> = {}): UiCanvasData {
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
      backdrop: [0, 0, 0, 0.6],
      layer: 0,
      focus: null,
      ...settings,
    },
    elements,
    assets: {},
  };
}

const anim = (duration: number, fps: number, tracks: Record<string, UiAnimationTrack>): UiAnimation => ({ duration, fps, tracks });

// --- A model and its view without a layer (images load at once) ------------------------------

const env: ModelEnv = { value: () => undefined, measurer: () => fallbackMeasurer, family: () => 'sans-serif', fontsVersion: () => 0 };

const loadedImages = {
  version: 1,
  get: () => ({ image: { width: 100, height: 100 } as unknown as HTMLImageElement, loaded: true, ready: Promise.resolve() }),
} as unknown as ImageRegistry;

function viewOf(data: UiCanvasData, size: [number, number] = [1920, 1080]) {
  const model = new CanvasModel(data, env);
  model.setViewport(size[0], size[1]);
  const view = new CanvasView(model, { images: loadedImages, url: (path) => path, quad: createQuad(), pixelRatio: () => 1 });
  const sync = () => {
    model.refresh();
    view.sync(0);
  };
  sync();
  const item = (name: string) => view.items.find((i) => i.node.el.name === name)!;
  const node = (name: string) => model.byName.get(name)!;
  return { model, view, item, node, sync };
}

describe('canvas animation sampling', () => {
  it('samples a track linearly between frames and holds its ends', () => {
    const track = [0, 10, 30]; // 10 fps: frames at 0, 0.1 and 0.2 s
    expect(sampleTrack(track, 10, 0)).toBe(0);
    expect(sampleTrack(track, 10, 0.05)).toBeCloseTo(5, 9);
    expect(sampleTrack(track, 10, 0.15)).toBeCloseTo(20, 9);
    expect(sampleTrack(track, 10, 0.2)).toBe(30);
    expect(sampleTrack(track, 10, 5)).toBe(30);
    expect(sampleTrack(track, 10, -1)).toBe(0);
    expect(sampleTrack([7], 10, 0.3)).toBe(7);
    expect(sampleTrack([], 10, 0)).toBeUndefined();
    expect(sampleTrack(undefined, 10, 0)).toBeUndefined();
  });

  it('plays open, then the loop over and over; close holds its end; presence follows open and close', () => {
    const one = { E: { x: [0, 1] } };
    const a = new CanvasAnimator({ open: anim(0.2, 5, one), loop: anim(0.5, 2, one), close: anim(0.1, 10, one) });
    a.play('open');
    expect([a.phase, a.time, a.presence]).toEqual(['open', 0, 0]);
    a.advance(0.1);
    expect([a.phase, a.presence]).toEqual(['open', 0.5]);
    a.advance(0.15); // 0.05 s past the open's end: into the loop
    expect(a.phase).toBe('loop');
    expect(a.time).toBeCloseTo(0.05, 9);
    a.advance(0.5);
    expect(a.time).toBeCloseTo(0.05, 9); // wrapped
    expect(a.presence).toBe(1);
    a.play('close');
    expect([a.phase, a.time, a.closed, a.presence]).toEqual(['close', 0, false, 1]);
    a.advance(0.05);
    expect(a.presence).toBeCloseTo(0.5, 9);
    a.advance(1);
    expect([a.time, a.closed, a.presence]).toEqual([0.1, true, 0]);
    a.play(null);
    expect(a.phase).toBeNull();

    // No open: straight to the loop. No loop: rest after the open. No close: closed at once.
    const loopOnly = new CanvasAnimator({ loop: anim(1, 2, one) });
    loopOnly.play('open');
    expect(loopOnly.phase).toBe('loop');
    const openOnly = new CanvasAnimator({ open: anim(0.2, 5, one) });
    openOnly.play('open');
    openOnly.advance(0.3);
    expect(openOnly.phase).toBeNull();
    const none = new CanvasAnimator(undefined);
    none.play('close');
    expect(none.closed).toBe(true);
    // Unusable animations count as none.
    const broken = new CanvasAnimator({ open: { duration: 0, fps: 30, tracks: one }, close: {} as UiAnimation });
    broken.play('open');
    expect(broken.phase).toBeNull();
  });

  it('writes every channel of each track, undefined where it has none', () => {
    const a = new CanvasAnimator({ open: anim(0.1, 10, { A: { y: [100, 0], opacity: [0, 1] }, Missing: { x: [1, 2] } }) });
    a.play('open');
    a.advance(0.05);
    const seen: Record<string, unknown> = {};
    a.sample((id) => (id === 'A' ? ((seen[id] = {}) as Record<string, number>) : null));
    expect(seen.A).toEqual({ x: undefined, y: 50, rotation: undefined, scaleX: undefined, scaleY: undefined, opacity: 0.5 });
  });
});

describe('opacity', () => {
  it('multiplies down the hierarchy into every material; an image element opacity stands in for image.opacity', () => {
    const data = design('Fade', [
      element({ id: 'Box', anchor: 'center', transform: at(960, 540), opacity: 0.5, order: 0 }),
      panel({ id: 'Card', parent: 'Box', bounds: [-100, -50, 100, 50], opacity: 0.5, order: 1 }, 0.8),
      label({ id: 'Label', parent: 'Box', body: 'Hi', order: 2 }),
      picture({ id: 'Icon', parent: 'Box', imageOpacity: 0.6, order: 3 }),
      picture({ id: 'Badge', parent: 'Box', imageOpacity: 0.6, opacity: 0.4, order: 4 }),
      element({ id: 'Inner', parent: 'Box', order: 5 }),
      panel({ id: 'Deep', parent: 'Inner', bounds: [-5, -5, 5, 5], order: 6 }),
    ]);
    data.animations = { open: anim(0.2, 10, { Box: { opacity: [0, 0.4, 1] } }) };
    const { model, item, node, sync } = viewOf(data);
    expect(['Box', 'Card', 'Label', 'Icon', 'Badge', 'Deep'].map((n) => node(n).opacity)).toEqual([0.5, 0.25, 0.5, 0.5, 0.2, 0.5]);
    const opacity = (name: string) => item(name).material.opacity;
    expect(opacity('Card')).toBeCloseTo(0.8 * 0.25, 9); // colour alpha × element × parent
    expect(opacity('Icon')).toBeCloseTo(0.6 * 0.5, 9); // image.opacity × parent
    expect(opacity('Badge')).toBeCloseTo(0.4 * 0.5, 9); // its element opacity, not image.opacity too
    expect(opacity('Deep')).toBeCloseTo(0.5, 9); // through a group

    // An animated opacity takes the element's place while it plays, and multiplies down the same way.
    model.play('open');
    sync();
    expect(['Card', 'Icon', 'Badge', 'Deep'].map(opacity)).toEqual([0, 0, 0, 0]);
    model.step(0.1);
    sync();
    expect(node('Box').opacity).toBeCloseTo(0.4, 9);
    expect(opacity('Card')).toBeCloseTo(0.8 * 0.5 * 0.4, 9);
    expect(opacity('Icon')).toBeCloseTo(0.6 * 0.4, 9);
    expect(node('Label').opacity).toBeCloseTo(0.4, 9);
    model.step(0.1); // the open is over: the rest opacity again
    sync();
    expect(node('Box').opacity).toBe(0.5);
    expect(opacity('Card')).toBeCloseTo(0.2, 9);
  });

  it('leaves canvases without opacity or animations as they were', () => {
    const { item, node } = viewOf(design('Plain', [panel({ id: 'Card', anchor: 'center', transform: at(960, 540), bounds: [-10, -10, 10, 10] }, 0.8), picture({ id: 'Icon', anchor: 'center', transform: at(100, 100), imageOpacity: 0.6 })]));
    expect(item('Card').material.opacity).toBeCloseTo(0.8, 9);
    expect(item('Icon').material.opacity).toBeCloseTo(0.6, 9);
    expect(node('Card').rest).toEqual(node('Card').base);
  });
});

// --- The layer: open → loop → close, drawn ----------------------------------------------------

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

function drawn(layer: CanvasLayer): Map<string, Mesh> {
  const raw = fakeRaw();
  layer.render(raw);
  const out = new Map<string, Mesh>();
  raw.scene?.traverse((o: Object3D) => {
    if (o instanceof Mesh && o.visible) out.set(o.name, o);
  });
  return out;
}

const opacityOf = (mesh: Mesh | undefined): number => (mesh ? (mesh.material as MeshBasicMaterial).opacity : NaN);

describe('animated canvases in the layer', () => {
  let dom: FakeDom;
  let layer: CanvasLayer;

  beforeEach(() => {
    dom = installDom({}, [960, 540]); // half the design size: s = 0.5
    const host: CanvasHost = {
      canvas: dom.canvas as unknown as HTMLCanvasElement,
      assetUrl: (_data, path) => `/export/files/${path}`,
      value: () => undefined,
      warn: () => {},
    };
    layer = createCanvasLayer(host);
  });
  afterEach(() => {
    layer.dispose();
    dom.restore();
  });

  function menus(): [UiCanvasData, UiCanvasData] {
    const menu = design('Menu', [panel({ id: 'Card', anchor: 'center', transform: at(960, 540), bounds: [-100, -50, 100, 50] }, 0.8)]);
    menu.animations = {
      open: anim(0.2, 10, { Card: { y: [740, 640, 540], opacity: [0, 0.5, 1] } }),
      loop: anim(1, 2, { Card: { scaleX: [1, 1.2, 1] } }),
      close: anim(0.1, 10, { Card: { opacity: [1, 0] } }),
    };
    const other = design('Other', [panel({ id: 'Box', anchor: 'center', transform: at(960, 540), bounds: [-10, -10, 10, 10] })]);
    return [menu, other];
  }

  it('plays open from the start when a canvas opens, then its loop; channels override the layout', async () => {
    const [menu] = menus();
    await layer.set(menu);
    const rect = () => layer.info('Menu', 'Card').rect;
    // At rest (not open): the authored layout.
    expect(rect()).toEqual({ x: 430, y: 245, width: 100, height: 50 });
    layer.setOpen([], 'Menu');
    // The open's first frame: 200 design px lower (100 canvas px), transparent, and so is the backdrop.
    expect(rect()).toEqual({ x: 430, y: 345, width: 100, height: 50 });
    let meshes = drawn(layer);
    expect(opacityOf(meshes.get('Card'))).toBe(0);
    expect(meshes.has('ui:backdrop')).toBe(false);
    layer.update(0.05);
    expect(rect().y).toBeCloseTo(320, 9);
    meshes = drawn(layer);
    expect(opacityOf(meshes.get('Card'))).toBeCloseTo(0.25 * 0.8, 9);
    expect(opacityOf(meshes.get('ui:backdrop'))).toBeCloseTo(0.6 * 0.25, 9);
    layer.update(0.15); // the open is over: rest, then the loop from its start
    expect(rect()).toEqual({ x: 430, y: 245, width: 100, height: 50 });
    meshes = drawn(layer);
    expect(opacityOf(meshes.get('Card'))).toBeCloseTo(0.8, 9);
    expect(opacityOf(meshes.get('ui:backdrop'))).toBeCloseTo(0.6, 9);
    layer.update(0.25); // a quarter into the loop: scaleX 1.1 about the element's origin
    expect(rect().width).toBeCloseTo(110, 9);
    expect(rect().x).toBeCloseTo(425, 9);
    layer.update(1); // and round again
    expect(rect().width).toBeCloseTo(110, 9);
  });

  it('keeps a closing canvas drawn under the open menu, each over its own backdrop, until it is dropped', async () => {
    const [menu, other] = menus();
    await layer.set(menu);
    await layer.set(other);
    layer.setOpen([], 'Menu');
    layer.update(0.5);
    layer.setOpen([], null, ['Menu']);
    let meshes = drawn(layer);
    expect(opacityOf(meshes.get('Card'))).toBeCloseTo(0.8, 9); // the close's first frame; the loop's scale is gone
    expect(layer.info('Menu', 'Card').rect.width).toBe(100);
    layer.update(0.05);
    meshes = drawn(layer);
    expect(opacityOf(meshes.get('Card'))).toBeCloseTo(0.4, 9);
    expect(opacityOf(meshes.get('ui:backdrop'))).toBeCloseTo(0.3, 9);
    layer.update(1); // it holds the close's end until the controller drops it
    expect(opacityOf(drawn(layer).get('Card'))).toBe(0);
    expect(drawn(layer).has('ui:backdrop')).toBe(false);

    // Opened again while closing: from the open's start.
    layer.setOpen([], 'Menu');
    expect(layer.info('Menu', 'Card').rect.y).toBe(345);
    expect(opacityOf(drawn(layer).get('Card'))).toBe(0);

    // Replaced while open: it closes under the new menu.
    layer.update(0.3);
    layer.setOpen([], 'Other', ['Menu']);
    meshes = drawn(layer);
    expect(meshes.get('Card')!.renderOrder).toBe(ORDER_SLOT);
    expect(meshes.get('ui:backdrop:1')!.renderOrder).toBe(0);
    expect(opacityOf(meshes.get('ui:backdrop:1'))).toBeCloseTo(0.6, 9);
    expect(meshes.get('ui:backdrop')!.renderOrder).toBe(2 * ORDER_SLOT);
    expect(meshes.get('Box')!.renderOrder).toBe(3 * ORDER_SLOT);
    layer.update(0.016);
    const mirror = layer.mirror as unknown as { children: Array<{ dataset: Record<string, string> }> };
    expect(mirror.children.map((c) => c.dataset.canvas)).toEqual(['Menu', 'Other']);

    // Its close is over: no longer drawn, and back at rest.
    layer.setOpen([], 'Other');
    meshes = drawn(layer);
    expect(meshes.has('Card')).toBe(false);
    expect(meshes.has('ui:backdrop:1')).toBe(false);
    expect([...meshes.keys()].sort()).toEqual(['Box', 'ui:backdrop']);
    expect(layer.info('Menu', 'Card').rect).toEqual({ x: 430, y: 245, width: 100, height: 50 });
  });

  it('keeps the animation going when a canvas is replaced live', async () => {
    const [menu] = menus();
    await layer.set(menu);
    layer.setOpen([], 'Menu');
    layer.update(0.05);
    const next = structuredClone(menu);
    next.rev = 2;
    await layer.set(next);
    expect(layer.info('Menu', 'Card').rect.y).toBeCloseTo(320, 9);
    layer.update(0.05);
    expect(layer.info('Menu', 'Card').rect.y).toBeCloseTo(295, 9);
  });
});

// --- 9-slice ---------------------------------------------------------------------------------

function slicedPanel(transform: Partial<UiElementData['transform']> = {}, stretch: UiElementData['stretch'] = 'none'): UiCanvasData {
  return design('Panel', [
    element({
      id: 'Frame',
      type: 'mesh',
      anchor: 'center',
      stretch,
      transform: at(960, 540, transform),
      mesh: {
        positions: [-200, -100, 200, -100, 200, 100, -200, 100],
        indices: [0, 1, 2, 0, 2, 3],
        uvs: [0, 1, 1, 1, 1, 0, 0, 0],
        colors: null,
        bounds: [-200, -100, 200, 100],
        material: { color: [1, 1, 1, 1], texture: 'assets/ui/panel.png', blend: 'alpha' },
        slice: { insets: [20, 30, 10, 40], image: [100, 100], size: [400, 200] },
      },
    }),
  ]);
}

/** The grid's column widths and row heights on screen, as drawn. */
function screenGrid(mesh: Mesh): { widths: number[]; heights: number[] } {
  const p = mesh.geometry.getAttribute('position').array;
  const e = mesh.matrix.elements;
  const xs = [0, 1, 2, 3].map((i) => e[0]! * p[i * 3]! + e[4]! * p[i * 3 + 1]! + e[12]!);
  const ys = [0, 1, 2, 3].map((j) => e[1]! * p[j * 12]! + e[5]! * p[j * 12 + 1]! + e[13]!);
  const round = (v: number) => Math.round(v * 1e6) / 1e6;
  return { widths: [1, 2, 3].map((i) => round(xs[i]! - xs[i - 1]!)), heights: [1, 2, 3].map((j) => round(ys[j]! - ys[j - 1]!)) };
}

describe('9-slice panels', () => {
  it('keeps corners at insets × canvas scale, stretching the edges and the middle', () => {
    const cases: Array<[string, UiCanvasData, [number, number], { widths: number[]; heights: number[] }]> = [
      ['design size', slicedPanel(), [1920, 1080], { widths: [20, 350, 30], heights: [10, 150, 40] }],
      ['half size (s = 0.5)', slicedPanel(), [960, 540], { widths: [10, 175, 15], heights: [5, 75, 20] }],
      ['scaled 2 × 0.5 at s = 0.5', slicedPanel({ scaleX: 2, scaleY: 0.5 }), [960, 540], { widths: [10, 375, 15], heights: [5, 25, 20] }],
      ['stretched to the width (s = 0.5, sx = 1)', slicedPanel({}, 'width'), [1920, 540], { widths: [10, 375, 15], heights: [5, 75, 20] }],
      ['smaller than its corners: they shrink to meet', slicedPanel({ scaleY: 0.1 }), [1920, 1080], { widths: [20, 350, 30], heights: [4, 0, 16] }],
    ];
    for (const [what, data, size, expected] of cases) {
      const { item } = viewOf(data, size);
      expect(screenGrid(item('Frame').mesh), what).toEqual(expected);
    }
  });

  it('maps the grid to the image insets (Blender uvs, v up)', () => {
    const { item } = viewOf(slicedPanel());
    const geometry = item('Frame').mesh.geometry;
    const uv = Array.from(geometry.getAttribute('uv').array);
    expect([0, 1, 2, 3].map((i) => uv[i * 2])).toEqual([0, 0.2, 0.7, 1].map((v) => Math.fround(v)));
    expect([0, 1, 2, 3].map((j) => uv[j * 8 + 1])).toEqual([1, 0.9, 0.4, 0].map((v) => Math.fround(v)));
    expect(geometry.getIndex()!.count).toBe(54); // 9 quads
    expect(geometry.getAttribute('position').count).toBe(16);
  });

  it('rebuilds only when the rest size changes; an animated scale zooms it whole', () => {
    const data = slicedPanel();
    data.animations = { loop: anim(1, 2, { Frame: { scaleX: [1.5, 1.5, 1.5] } }) };
    const { model, item, sync } = viewOf(data);
    const positions = item('Frame').mesh.geometry.getAttribute('position') as BufferAttribute;
    const version = positions.version;
    sync();
    expect(positions.version).toBe(version);
    model.play('open'); // no open: straight to the loop
    sync();
    expect(positions.version).toBe(version);
    expect(screenGrid(item('Frame').mesh)).toEqual({ widths: [30, 525, 45], heights: [10, 150, 40] });
    model.setViewport(960, 540); // a resize changes the canvas scale, not the ratio: still no rebuild
    sync();
    expect(positions.version).toBe(version);
    model.play(null);
    model.setViewport(1920, 540); // fit: s = 0.5, same ratio
    sync();
    expect(positions.version).toBe(version);
  });

  it('computes stops that fit', () => {
    expect(sliceStops(0, 100, 10, 20, 1)).toEqual([0, 10, 80, 100]);
    expect(sliceStops(0, 100, 10, 20, 2)).toEqual([0, 5, 90, 100]);
    expect(sliceStops(0, 15, 10, 20, 1)).toEqual([0, 5, 5, 15]);
    expect(sliceStops(0, 0, 10, 20, 0)).toEqual([0, 0, 0, 0]);
  });
});

// --- The controller: lifecycle in game steps ---------------------------------------------------

let gameDom: FakeDom | undefined;
beforeAll(async () => {
  await new RapierPhysics().init();
});
afterEach(() => {
  gameDom?.restore();
  gameDom = undefined;
});

function animatedGame() {
  const play = button('Play', 'Play', [400, 200], { action: 'close' });
  const Title = fixtureCanvas('Title', { open: 'firstStart' }, [text('TitleText', 'Gem Island', [400, 80]), play, button('Quit', 'Quit', [400, 300], { target: 'quit' })]);
  // 800 × 450 design = canvas: design px are canvas px. Play slides up 60 px as the title opens and down as it closes.
  Title.animations = {
    open: anim(0.1, 20, { [play[0]!.id]: { y: [260, 230, 200] } }),
    close: anim(0.25, 8, { [play[0]!.id]: { y: [200, 250, 300] } }),
  };
  const score = text('Score', 'Score', [100, 30]);
  const HUD = fixtureCanvas('HUD', { kind: 'hud', open: 'start' }, [score]);
  HUD.animations = { close: anim(0.1, 10, { [score.id]: { opacity: [1, 0] } }) };
  const Plain = fixtureCanvas('Plain', {}, [button('Ok', 'Ok', [400, 200], { action: 'close' })]);
  // A manual HUD badge: a 0.05 s open, then a 0.5 s loop.
  const badge = text('Badge', 'New!', [700, 30]);
  const Pulse = fixtureCanvas('Pulse', { kind: 'hud' }, [badge]);
  Pulse.animations = { open: anim(0.05, 20, { [badge.id]: { opacity: [0, 1] } }), loop: anim(0.5, 2, { [badge.id]: { scaleX: [1, 1.1] } }) };
  return [Title, HUD, Plain, Pulse];
}

async function startGame(): Promise<Game> {
  gameDom = installDom({ ...sceneFiles(), ...uiFiles(animatedGame()) });
  const g = await heldGame(gameDom);
  await g.test.begin();
  await g.test.advance(1);
  return g;
}

describe('canvas animations in a running game (UI clock = test steps)', () => {
  it('opens with its open animation, takes input during it, and closes over its close animation', async () => {
    const g = await startGame();
    const ui = () => g.test.state({ ids: [] }).ui;
    const playY = () => g.ui.element('Play')!.rect.y;
    expect(ui()).toMatchObject({ screen: 'Title', focus: 'Play', open: ['HUD', 'Title'] });
    expect(g.state).toBe('paused'); // the UI clock runs while the title holds the game paused
    // 1/60 s into the open (the step that opened it): a third of the way from 260 to 230.
    expect(playY()).toBeCloseTo(250 - 25, 6);
    expect(ui().buttons.map((b) => b.name)).toEqual(['Play', 'Quit']);
    await g.test.advance(2); // 0.05 s: frame 1
    expect(playY()).toBeCloseTo(230 - 25, 6);
    await g.test.advance(3); // 0.1 s: the open is over, at rest
    expect(playY()).toBeCloseTo(175, 6);

    g.ui.close();
    // Closed at once for the game (it plays on), drawn and listed until its close animation ends.
    expect([g.ui.current, g.ui.isOpen('Title'), g.state]).toEqual([null, false, 'playing']);
    expect(ui().open).toEqual(['HUD', 'Title']);
    expect(ui().buttons).toEqual([]); // a closing menu takes no input
    await g.test.advance(7); // 7/60 s at 8 fps: frame 0.9333
    expect(playY()).toBeCloseTo(200 + 50 * (56 / 60) - 25, 6);
    expect(ui().texts['Title/TitleText']).toBe('Gem Island');
    await g.test.advance(7); // 14 steps: not yet 0.25 s
    expect(ui().open).toEqual(['HUD', 'Title']);
    await g.test.advance(1); // 15 steps = 0.25 s: hidden, back at rest
    expect(ui().open).toEqual(['HUD']);
    expect(ui().texts['Title/TitleText']).toBeUndefined();
    expect(playY()).toBeCloseTo(175, 6);
  });

  it('presses a button during the open animation', async () => {
    const g = await startGame();
    g.test.schedule([{ step: g.test.status().step, key: 'Enter' }]);
    await g.test.advance(1);
    expect([g.ui.current, g.state]).toEqual([null, 'playing']);
    expect(g.test.state({ ids: [] }).ui.open).toEqual(['HUD', 'Title']); // closing
  });

  it('opens again from the open start when opened during its close', async () => {
    const g = await startGame();
    const playY = () => g.ui.element('Play')!.rect.y;
    await g.test.advance(10);
    g.ui.close();
    await g.test.advance(5);
    expect(g.test.state({ ids: [] }).ui.open).toEqual(['HUD', 'Title']);
    g.ui.open('Title');
    expect(g.test.state({ ids: [] }).ui).toMatchObject({ screen: 'Title', open: ['HUD', 'Title'] });
    await g.test.advance(1);
    expect(playY()).toBeCloseTo(250 - 25, 6); // 1/60 s into a fresh open
    await g.test.advance(20); // its old close timer is gone with it
    expect(g.test.state({ ids: [] }).ui).toMatchObject({ screen: 'Title', open: ['HUD', 'Title'] });
    expect(playY()).toBeCloseTo(175, 6);
  });

  it('draws a replaced menu closing under the new one; canvases without a close animation close at once', async () => {
    const g = await startGame();
    const ui = () => g.test.state({ ids: [] }).ui;
    await g.test.advance(10);
    g.ui.open('Plain');
    expect(ui()).toMatchObject({ screen: 'Plain', open: ['HUD', 'Title', 'Plain'] });
    expect(ui().buttons.map((b) => b.name)).toEqual(['Ok']);
    await g.test.advance(15);
    expect(ui().open).toEqual(['HUD', 'Plain']);
    g.ui.close();
    expect(ui().open).toEqual(['HUD']); // Plain has no close animation
  });

  it('closes a HUD over its close animation; isOpen is false at once; a reopen keeps it', async () => {
    const g = await startGame();
    const ui = () => g.test.state({ ids: [] }).ui;
    g.ui.close();
    await g.test.advance(15);
    g.ui.close('HUD');
    expect([g.ui.isOpen('HUD'), ui().open]).toEqual([false, ['HUD']]);
    await g.test.advance(5);
    expect(ui().open).toEqual(['HUD']);
    await g.test.advance(1); // 6 steps = 0.1 s
    expect(ui().open).toEqual([]);
    g.ui.open('HUD');
    await g.test.advance(2);
    g.ui.close('HUD');
    await g.test.advance(3);
    g.ui.open('HUD');
    await g.test.advance(10);
    expect([g.ui.isOpen('HUD'), ui().open]).toEqual([true, ['HUD']]);
  });

  it('lists what plays its open or close animation in state().ui.animating, not a loop', async () => {
    const g = await startGame();
    const ui = () => g.test.state({ ids: [] }).ui;
    // 1/60 s into the title's 0.1 s open; the HUD has no open animation.
    expect(ui()).toMatchObject({ open: ['HUD', 'Title'], animating: ['Title'] });
    await g.test.advance(4); // 5/60 s
    expect(ui().animating).toEqual(['Title']);
    await g.test.advance(1); // 0.1 s: fully open
    expect(ui().animating).toEqual([]);
    g.ui.close();
    expect(ui()).toMatchObject({ open: ['HUD', 'Title'], animating: ['Title'] }); // its 0.25 s close
    await g.test.advance(14);
    expect(ui().animating).toEqual(['Title']);
    await g.test.advance(1);
    expect(ui()).toMatchObject({ open: ['HUD'], animating: [] });
    // Opened between steps: listed at once, its open plays from the next frame.
    g.ui.open('Title');
    expect(ui().animating).toEqual(['Title']);
    await g.test.advance(6);
    expect(ui().animating).toEqual([]);
    g.ui.close();
    // A canvas without animations is never listed; a HUD closing is.
    g.ui.open('Plain');
    expect(ui()).toMatchObject({ open: ['HUD', 'Title', 'Plain'], animating: ['Title'] });
    g.ui.close();
    await g.test.advance(15);
    g.ui.close('HUD');
    expect(ui()).toMatchObject({ open: ['HUD'], animating: ['HUD'] });
    await g.test.advance(6);
    expect(ui()).toMatchObject({ open: [], animating: [] });
    // The open counts, the loop after it does not.
    g.ui.open('Pulse');
    expect(ui().animating).toEqual(['Pulse']);
    await g.test.advance(2);
    expect(ui().animating).toEqual(['Pulse']);
    await g.test.advance(1); // 0.05 s: looping
    expect(ui()).toMatchObject({ open: ['Pulse'], animating: [] });
    await g.test.advance(40);
    expect(ui().animating).toEqual([]);
  });

  it('a reset closes everything at once, without close animations', async () => {
    const g = await startGame();
    g.ui.close();
    expect(g.test.state({ ids: [] }).ui.open).toEqual(['HUD', 'Title']);
    await g.test.begin({ reset: true });
    expect(g.test.state({ ids: [] }).ui.open).toEqual([]);
    await g.test.advance(1);
    expect(g.test.state({ ids: [] }).ui.open).toEqual(['HUD', 'Title']);
    expect(g.ui.element('Play')!.rect.y).toBeCloseTo(250 - 25, 6); // opening again from the start
  });
});
