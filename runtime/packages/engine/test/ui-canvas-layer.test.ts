import { AdditiveBlending, Mesh, MeshBasicMaterial, type Object3D, type OrthographicCamera, type Scene } from 'three';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import type { UiCanvasData, UiCanvasSettings, UiElementData } from '../src/ui/canvas/format';
import { createCanvasLayer, type CanvasHost, type CanvasLayer } from '../src/ui/canvas/index';
import { ORDER_SLOT } from '../src/ui/canvas/renderer';
import { installDom, type FakeDom, type FakeElement } from './dom';

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

function panel(input: ElementInput & { bounds: [number, number, number, number] }, material: Partial<NonNullable<UiElementData['mesh']>['material']> = {}): UiElementData {
  const [x0, y0, x1, y1] = input.bounds;
  return element({
    type: 'mesh',
    mesh: {
      positions: [x0, y0, x1, y0, x1, y1, x0, y1],
      indices: [0, 1, 2, 0, 2, 3],
      uvs: null,
      colors: null,
      bounds: input.bounds,
      material: { color: [1, 0.5, 0, 0.8], texture: '', blend: 'alpha', ...material },
    },
    ...input,
  });
}

function label(input: ElementInput & { body: string }): UiElementData {
  return element({
    type: 'text',
    text: {
      body: input.body,
      font: 'assets/fonts/Inter.woff2',
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

function canvas(name: string, elements: UiElementData[], settings: Partial<UiCanvasSettings> = {}): UiCanvasData {
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

const button = (id: string, x: number, y: number, text: string, extra: Record<string, unknown> = {}, order = 1) => [
  element({ id, anchor: 'center', transform: at(x, y), order, components: { UiButton: { action: 'event', target: '', ...extra } } }),
  panel({ id: `${id}Bg`, parent: id, order: order + 1, bounds: [-200, -40, 200, 40] }),
  panel({ id: `${id}Hi`, parent: id, order: order + 2, bounds: [-200, -40, 200, 40], components: { UiState: { show: 'focused' } } }),
  label({ id: `${id}Label`, parent: id, order: order + 3, body: text }),
];

function title(): UiCanvasData {
  return canvas('Title', [
    label({ id: 'TitleText', anchor: 'top', transform: at(960, 200), order: 0, body: 'Gem Island' }),
    ...button('Play', 960, 600, 'Play', { action: 'close' }, 1),
    ...button('Quit', 960, 750, 'Quit', { action: 'event', target: 'quit', disabled: true }, 10),
    label({ id: 'Secret', anchor: 'center', transform: at(960, 900), visible: false, body: 'secret' }),
  ]);
}

function hud(): UiCanvasData {
  return canvas(
    'HUD',
    [
      label({ id: 'Score', anchor: 'top-left', transform: at(200, 60), body: 'Score {score:000}' }),
      element({ id: 'Health', anchor: 'top-left', transform: at(100, 120), components: { UiBar: { value: 'hp', max: 100, fill: 'HealthFill', direction: 'right', smoothing: 0 } } }),
      panel({ id: 'HealthFill', parent: 'Health', order: 2, bounds: [0, -10, 300, 10] }),
      panel({ id: 'Glow', anchor: 'top-left', transform: at(50, 50), order: 3, bounds: [0, 0, 10, 10] }, { blend: 'additive' }),
    ],
    { kind: 'hud', backdrop: [0, 0, 0, 0] },
  );
}

interface FakeRaw {
  autoClear: boolean;
  calls: string[];
  scene: Scene | null;
  camera: OrthographicCamera | null;
  clearDepth(): void;
  render(scene: Scene, camera: OrthographicCamera): void;
}

function fakeRaw(): FakeRaw {
  const raw: FakeRaw = {
    autoClear: true,
    calls: [],
    scene: null,
    camera: null,
    clearDepth() {
      raw.calls.push(`clearDepth autoClear=${raw.autoClear}`);
    },
    render(scene, camera) {
      raw.calls.push(`render autoClear=${raw.autoClear}`);
      raw.scene = scene;
      raw.camera = camera;
      scene.updateMatrixWorld();
    },
  };
  return raw;
}

function meshes(scene: Scene): Mesh[] {
  const out: Mesh[] = [];
  scene.traverse((o: Object3D) => {
    if (o instanceof Mesh) out.push(o);
  });
  return out;
}

describe('Game UI canvas layer', () => {
  let dom: FakeDom;
  let store: Record<string, unknown>;
  let warnings: string[];
  let layer: CanvasLayer;

  beforeEach(() => {
    dom = installDom({}, [960, 540]);
    store = { score: 7, hp: 50 };
    warnings = [];
    const host: CanvasHost = {
      canvas: dom.canvas as unknown as HTMLCanvasElement,
      assetUrl: (_data, path) => `/export/files/${path}`,
      value: (path) => store[path],
      warn: (message) => warnings.push(message),
    };
    layer = createCanvasLayer(host);
  });
  afterEach(() => {
    layer.dispose();
    dom.restore();
  });

  it('loads canvases without Canvas2D, FontFace or images', async () => {
    await layer.set(title());
    await layer.set(hud());
    expect(layer.names()).toEqual(['Title', 'HUD']);
    expect(layer.has('HUD')).toBe(true);
    expect(layer.data('Title')!.canvas).toBe('Title');
    expect(layer.data('Nope')).toBeNull();
    expect(warnings).toEqual([]);
  });

  it('lists visible buttons with labels, events and rects in canvas px', async () => {
    await layer.set(title());
    const buttons = layer.buttons('Title');
    expect(buttons.map((b) => [b.name, b.label, b.event, b.action, b.disabled])).toEqual([
      ['Play', 'Play', 'Play', 'close', false],
      ['Quit', 'Quit', 'quit', 'event', true],
    ]);
    // 960×540 is half the design size: the 400×80 panel at (960, 600) is 200×40 at (480, 300).
    expect(buttons[0]!.rect).toEqual({ x: 380, y: 280, width: 200, height: 40 });
    expect(buttons[0]!.focusScale).toBe(1.06);
    expect(layer.buttons('Nope')).toEqual([]);
  });

  it('follows the game canvas size lazily, and explicit layouts', async () => {
    await layer.set(title());
    expect(layer.buttons('Title')[0]!.rect.width).toBe(200);
    (dom.canvas as FakeElement).clientWidth = 1920;
    (dom.canvas as FakeElement).clientHeight = 1080;
    expect(layer.buttons('Title')[0]!.rect).toEqual({ x: 760, y: 560, width: 400, height: 80 });
    layer.layout(480, 270);
    expect(layer.buttons('Title')[0]!.rect.width).toBe(100);
  });

  it('hit-tests the topmost visible button', async () => {
    await layer.set(title());
    expect(layer.hitTest('Title', 480, 300)!.name).toBe('Play');
    expect(layer.hitTest('Title', 480, 375)!.name).toBe('Quit');
    expect(layer.hitTest('Title', 480, 375)!.disabled).toBe(true);
    expect(layer.hitTest('Title', 10, 10)).toBeNull();
    // Overlapping buttons: the one drawn on top wins.
    const data = title();
    data.elements.find((e) => e.id === 'Quit')!.transform.y = 610;
    await layer.set(data);
    expect(layer.hitTest('Title', 480, 300)!.name).toBe('Quit');
  });

  it('resolves bindings in texts and reports visible texts', async () => {
    await layer.set(title());
    await layer.set(hud());
    expect(layer.texts(['HUD', 'Title'])).toEqual({
      'HUD/Score': 'Score 007',
      'Title/TitleText': 'Gem Island',
      'Title/PlayLabel': 'Play',
      'Title/QuitLabel': 'Quit',
    });
    store.score = 12;
    expect(layer.texts(['HUD'])['HUD/Score']).toBe('Score 012');
    const id = layer.find('Score', [])!.element.id;
    layer.setText('HUD', id, 'Game over');
    store.score = 99;
    expect(layer.texts(['HUD'])['HUD/Score']).toBe('Game over');
    layer.setText('HUD', id, null);
    expect(layer.texts(['HUD'])['HUD/Score']).toBe('Score 099');
  });

  it('finds elements by name, preferring canvases, and by Canvas/Name', async () => {
    await layer.set(title());
    await layer.set(canvas('Pause', [label({ id: 'TitleText', body: 'Paused' })]));
    expect(layer.find('TitleText', [])!.canvas).toBe('Title');
    expect(layer.find('TitleText', ['Pause'])!.canvas).toBe('Pause');
    expect(layer.find('Pause/TitleText', ['Title'])).toMatchObject({ canvas: 'Pause', element: { name: 'TitleText' } });
    expect(layer.find('Title/PlayLabel', [])!.element.id).toBe('PlayLabel');
    expect(layer.find('Nope', [])).toBeNull();
    expect(layer.find('Nope/TitleText', [])).toBeNull();
  });

  it('reports element info and applies visibility overrides', async () => {
    await layer.set(title());
    expect(layer.info('Title', 'Secret')).toMatchObject({ canvas: 'Title', name: 'Secret', type: 'text', visible: false, text: 'secret' });
    layer.setVisible('Title', 'Secret', true);
    expect(layer.info('Title', 'Secret').visible).toBe(true);
    expect(layer.texts(['Title'])['Title/Secret']).toBe('secret');
    layer.setVisible('Title', 'Play', false);
    expect(layer.buttons('Title').map((b) => b.name)).toEqual(['Quit']);
    expect(layer.info('Title', 'PlayLabel').visible).toBe(false);
    expect(() => layer.info('Title', 'missing')).toThrow();
    const play = layer.info('Title', 'PlayBg');
    expect(play.rect).toEqual({ x: 380, y: 280, width: 200, height: 40 });
  });

  it('drives bars from bindings and code', async () => {
    await layer.set(hud());
    expect(layer.info('HUD', 'HealthFill').rect.width).toBe(75); // 50 of 100, at half scale.
    layer.setBar('HUD', 'Health', 100);
    expect(layer.info('HUD', 'HealthFill').rect.width).toBe(150);
    layer.setBar('HUD', 'Health', null);
    store.hp = 0;
    expect(layer.info('HUD', 'HealthFill').rect.width).toBe(0);
  });

  it('replaces a canvas in place, keeping overrides, focus and open state', async () => {
    await layer.set(title());
    layer.setOpen([], 'Title');
    layer.setButtonState('Title', 'Play', null);
    layer.setText('Title', 'TitleText', 'Custom');
    layer.setVisible('Title', 'Secret', true);
    const next = title();
    next.rev = 2;
    next.elements.find((e) => e.id === 'Play')!.transform.x = 1060;
    await layer.set(next);
    expect(layer.data('Title')!.rev).toBe(2);
    expect(layer.texts(['Title'])['Title/TitleText']).toBe('Custom');
    expect(layer.info('Title', 'Secret').visible).toBe(true);
    // Still focused: the focus art is visible.
    expect(layer.info('Title', 'PlayHi').visible).toBe(true);
    expect(layer.buttons('Title')[0]!.rect.x).toBe(430);
    const raw = fakeRaw();
    layer.render(raw);
    expect(raw.calls).toHaveLength(2);
  });

  it('forgets removed canvases', async () => {
    await layer.set(title());
    layer.setOpen([], 'Title');
    layer.remove('Title');
    expect(layer.has('Title')).toBe(false);
    expect(layer.find('PlayLabel', [])).toBeNull();
    const raw = fakeRaw();
    layer.render(raw);
    expect(raw.calls).toEqual([]);
  });

  it('draws over the frame without clearing it, and only with a renderer', async () => {
    await layer.set(title());
    layer.render(undefined);
    layer.render(null);
    const idle = fakeRaw();
    layer.render(idle); // Nothing open.
    expect(idle.calls).toEqual([]);
    layer.setOpen([], 'Title');
    const raw = fakeRaw();
    layer.render(raw);
    expect(raw.calls).toEqual(['clearDepth autoClear=false', 'render autoClear=false']);
    expect(raw.autoClear).toBe(true);
    layer.setHidden(true);
    const hidden = fakeRaw();
    layer.render(hidden);
    expect(hidden.calls).toEqual([]);
  });

  it('builds unlit flat meshes stacked HUDs, backdrop, menu', async () => {
    await layer.set(title());
    await layer.set(hud());
    layer.setOpen(['HUD'], 'Title');
    const raw = fakeRaw();
    layer.render(raw);
    const scene = raw.scene!;
    const camera = raw.camera!;
    expect([camera.left, camera.right, camera.top, camera.bottom]).toEqual([0, 960, 0, 540]);
    const all = meshes(scene);
    for (const mesh of all) {
      const m = mesh.material as MeshBasicMaterial;
      expect(m).toBeInstanceOf(MeshBasicMaterial);
      expect([m.toneMapped, m.depthTest, m.depthWrite, m.transparent]).toEqual([false, false, false, true]);
    }
    const byName = (name: string) => all.find((m) => m.name === name)!;
    // HUD elements draw first, then the backdrop, then the menu.
    expect(byName('HealthFill').renderOrder).toBe(2);
    const backdrop = byName('ui:backdrop');
    expect(backdrop.visible).toBe(true);
    expect(backdrop.renderOrder).toBe(ORDER_SLOT);
    expect((backdrop.material as MeshBasicMaterial).opacity).toBeCloseTo(0.6, 6);
    expect(byName('PlayBg').renderOrder).toBe(2 * ORDER_SLOT + 2);
    // Mesh geometry and colour (linear), placed by the layout matrix.
    const bg = byName('PlayBg');
    expect(bg.visible).toBe(true);
    const material = bg.material as MeshBasicMaterial;
    expect([material.color.r, material.color.g, material.color.b]).toEqual([1, 0.5, 0]);
    expect(material.opacity).toBeCloseTo(0.8, 6);
    expect(Array.from(bg.geometry.getAttribute('position').array).slice(0, 3)).toEqual([-200, -40, 0]);
    expect(bg.matrixWorld.elements[0]).toBeCloseTo(0.5, 9);
    expect(bg.matrixWorld.elements[12]).toBeCloseTo(480, 9);
    expect(bg.matrixWorld.elements[13]).toBeCloseTo(300, 9);
    expect((byName('Glow').material as MeshBasicMaterial).blending).toBe(AdditiveBlending);
    // UiState art hides until focused; texts wait for a raster (none without Canvas2D).
    expect(byName('PlayHi').visible).toBe(false);
    expect(byName('PlayLabel').visible).toBe(false);
    layer.setButtonState('Title', 'Play', null);
    layer.render(fakeRaw());
    expect(byName('PlayHi').visible).toBe(true);
    // The focused button scales about its centre when drawn.
    expect(byName('PlayBg').matrixWorld.elements[0]).toBeCloseTo(0.5 * 1.06, 9);
    expect(byName('PlayBg').matrixWorld.elements[12]).toBeCloseTo(480, 9);
    // Closing the menu takes its meshes and the backdrop out.
    layer.setOpen(['HUD'], null);
    const hudOnly = fakeRaw();
    layer.render(hudOnly);
    const names = meshes(hudOnly.scene!).filter((m) => m.visible).map((m) => m.name);
    expect(names).toEqual(['HealthFill', 'Glow']);
  });

  it('mirrors open canvases into a visually hidden DOM subtree', async () => {
    await layer.set(title());
    await layer.set(hud());
    layer.setOpen(['HUD'], 'Title');
    layer.update(0.016);
    const root = layer.mirror as unknown as FakeElement;
    expect(root.parentElement).toBe((globalThis as unknown as { document: { body: FakeElement } }).document.body);
    expect(root.style.cssText).toContain('clip');
    expect(root.style.cssText).not.toContain('display:none');
    expect(root.style.cssText).toContain('pointer-events:none');
    expect(root.children.map((c) => c.dataset.canvas)).toEqual(['HUD', 'Title']);
    const titleDiv = root.children[1]!;
    const texts = titleDiv.children.filter((c) => c.dataset.name).map((c) => [c.dataset.name, c.textContent]);
    expect(texts).toEqual([
      ['TitleText', 'Gem Island'],
      ['PlayLabel', 'Play'],
      ['QuitLabel', 'Quit'],
    ]);
    const buttons = titleDiv.children.filter((c) => (c as unknown as { role?: string }).role === 'button').map((c) => c.dataset.label);
    expect(buttons).toEqual(['Play', 'Quit']);
    store.score = 42;
    layer.update(0.016);
    expect(root.children[0]!.children.find((c) => c.dataset.name === 'Score')!.textContent).toBe('Score 042');
    layer.setHidden(true);
    expect(root.style.display).toBe('none');
    layer.setOpen([], null);
    layer.update(0.016);
    expect(root.children).toHaveLength(0);
  });

  it("lists a focused button's label once, though its focus label shows too", async () => {
    await layer.set(
      canvas('Menu', [
        ...button('Play', 960, 600, 'Play', { action: 'close' }, 1),
        label({ id: 'PlayFocusLabel', parent: 'Play', order: 5, body: 'Play', components: { UiState: { show: 'focusedOrPressed' } } }),
      ]),
    );
    layer.setOpen([], 'Menu');
    layer.setButtonState('Menu', 'Play', null);
    layer.update(0.016);
    const menu = (layer.mirror as unknown as FakeElement).children[0]!;
    expect(menu.children.filter((c) => c.dataset.name).map((c) => c.textContent)).toEqual(['Play']);
  });

  it('removes the mirror and GPU objects on dispose', async () => {
    await layer.set(title());
    layer.setOpen([], 'Title');
    layer.render(fakeRaw());
    const root = layer.mirror as unknown as FakeElement;
    layer.dispose();
    expect(root.parentElement).toBeNull();
    layer.render(fakeRaw());
    expect(layer.names()).toEqual([]);
  });
});
