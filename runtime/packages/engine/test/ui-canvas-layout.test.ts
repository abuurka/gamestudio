import { describe, expect, it } from 'vitest';
import type { UiCanvasData, UiCanvasSettings, UiElementData } from '../src/ui/canvas/format';
import {
  affine,
  anchorFactors,
  canvasScale,
  fromTransform,
  multiply,
  rootMatrix,
  stretchScale,
  transformBounds,
  type Bounds,
} from '../src/ui/canvas/layout';
import { CanvasModel, type ModelEnv } from '../src/ui/canvas/model';
import { fallbackMeasurer } from '../src/ui/canvas/text';

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

function mesh(input: ElementInput & { bounds: Bounds }): UiElementData {
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

function canvas(elements: UiElementData[], settings: Partial<UiCanvasSettings> = {}, name = 'Test'): UiCanvasData {
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
  };
}

function model(data: UiCanvasData, store: Record<string, unknown> = {}): CanvasModel {
  const env: ModelEnv = {
    value: (path) => store[path],
    measurer: () => fallbackMeasurer,
    family: () => 'sans-serif',
    fontsVersion: () => 0,
  };
  return new CanvasModel(data, env);
}

function rect(m: CanvasModel, id: string) {
  m.refresh();
  return m.rectOf(m.byId.get(id)!);
}

describe('Game UI layout math', () => {
  it('computes the canvas scale for each mode', () => {
    expect(canvasScale('fit', 960, 1080, 1920, 1080)).toBe(0.5);
    expect(canvasScale('fit', 3840, 1080, 1920, 1080)).toBe(1);
    expect(canvasScale('width', 960, 1080, 1920, 1080)).toBe(0.5);
    expect(canvasScale('height', 960, 540, 1920, 1080)).toBe(0.5);
    expect(canvasScale('fixed', 960, 540, 1920, 1080)).toBe(1);
  });

  it('resolves anchors, including auto from thirds', () => {
    expect(anchorFactors('top-right', 0, 0, 1920, 1080)).toEqual([1, 0]);
    expect(anchorFactors('bottom', 0, 0, 1920, 1080)).toEqual([0.5, 1]);
    expect(anchorFactors(undefined, 100, 1000, 1920, 1080)).toEqual([0, 1]);
    expect(anchorFactors(undefined, 960, 540, 1920, 1080)).toEqual([0.5, 0.5]);
    expect(anchorFactors(undefined, 1800, 100, 1920, 1080)).toEqual([1, 0]);
  });

  it('picks per-axis scales for stretch', () => {
    expect(stretchScale('none', 0.5, 960, 1080, 1920, 1080)).toEqual([0.5, 0.5]);
    expect(stretchScale('width', 0.5, 960, 1080, 1920, 1080)).toEqual([0.5, 0.5]);
    expect(stretchScale('height', 0.5, 960, 1080, 1920, 1080)).toEqual([0.5, 1]);
    expect(stretchScale('both', 0.5, 960, 1080, 1920, 1080)).toEqual([0.5, 1]);
    expect(stretchScale('cover', 0.5, 960, 1080, 1920, 1080)).toEqual([1, 1]);
  });

  it('keeps an anchored element its distance from the anchor', () => {
    const m = rootMatrix(affine(), 1, 0, 0.5, 0.5, 960, 1080, 1920, 1080);
    const local = fromTransform(affine(), { x: 1820, y: 100, rotation: 0, scaleX: 1, scaleY: 1 });
    const screen = multiply(affine(), m, local);
    expect([screen[4], screen[5]]).toEqual([910, 50]);
    expect(screen[0]).toBe(0.5);
  });

  it('rotates clockwise with y down', () => {
    const m = fromTransform(affine(), { x: 0, y: 0, rotation: Math.PI / 2, scaleX: 1, scaleY: 1 });
    const b = transformBounds([0, 0, 0, 0], m, [10, -1, 20, 1]);
    // +x turns to +y (down the screen).
    expect(b[0]).toBeCloseTo(-1, 9);
    expect(b[1]).toBeCloseTo(10, 9);
    expect(b[2]).toBeCloseTo(1, 9);
    expect(b[3]).toBeCloseTo(20, 9);
  });
});

describe('Game UI canvas model layout', () => {
  const corner = () =>
    canvas([
      mesh({ id: 'br', anchor: 'bottom-right', transform: { x: 1820, y: 1000, rotation: 0, scaleX: 1, scaleY: 1 }, bounds: [-50, -30, 50, 30] }),
      mesh({ id: 'mid', anchor: 'center', transform: { x: 960, y: 540, rotation: 0, scaleX: 1, scaleY: 1 }, bounds: [-10, -10, 10, 10] }),
      mesh({ id: 'tl', anchor: 'top-left', transform: { x: 100, y: 50, rotation: 0, scaleX: 1, scaleY: 1 }, bounds: [0, 0, 10, 10] }),
    ]);

  it('lays out anchors at several aspect ratios', () => {
    const m = model(corner());
    m.setViewport(1920, 1080);
    expect(rect(m, 'br')).toEqual({ x: 1770, y: 970, width: 100, height: 60 });
    m.setViewport(2560, 1080); // Ultrawide: s = 1.
    expect(rect(m, 'br')).toEqual({ x: 2410, y: 970, width: 100, height: 60 });
    expect(rect(m, 'mid')).toEqual({ x: 1270, y: 530, width: 20, height: 20 });
    expect(rect(m, 'tl')).toEqual({ x: 100, y: 50, width: 10, height: 10 });
    m.setViewport(1080, 1080); // Square: s = 0.5625.
    const br = rect(m, 'br');
    expect(br.x + br.width / 2).toBeCloseTo(1080 - 100 * 0.5625, 9);
    expect(br.y + br.height / 2).toBeCloseTo(1080 - 80 * 0.5625, 9);
    expect(br.width).toBeCloseTo(100 * 0.5625, 9);
    expect(rect(m, 'tl').x).toBeCloseTo(100 * 0.5625, 9);
  });

  it('stretches to cover the screen and across its width', () => {
    const m = model(
      canvas([
        element({
          id: 'bg',
          type: 'image',
          anchor: 'center',
          stretch: 'cover',
          transform: { x: 960, y: 540, rotation: 0, scaleX: 1, scaleY: 1 },
          image: { src: 'bg.png', rect: [-960, -540, 1920, 1080], opacity: 1, pixelated: false },
        }),
        mesh({ id: 'bar', anchor: 'top', stretch: 'width', transform: { x: 960, y: 20, rotation: 0, scaleX: 1, scaleY: 1 }, bounds: [-960, -20, 960, 20] }),
      ]),
    );
    m.setViewport(800, 800);
    const bg = rect(m, 'bg');
    expect(bg.height).toBeCloseTo(800, 6);
    expect(bg.width).toBeGreaterThanOrEqual(800);
    expect(bg.x + bg.width / 2).toBeCloseTo(400, 6);
    const bar = rect(m, 'bar');
    expect(bar.x).toBeCloseTo(0, 6);
    expect(bar.width).toBeCloseTo(800, 6);
    expect(bar.height).toBeCloseTo(40 * (800 / 1920), 6);
  });

  it('composes nested rotation and scale', () => {
    const m = model(
      canvas([
        element({ id: 'parent', anchor: 'center', transform: { x: 960, y: 540, rotation: Math.PI / 2, scaleX: 2, scaleY: 2 } }),
        mesh({ id: 'child', parent: 'parent', transform: { x: 100, y: 0, rotation: 0, scaleX: 1, scaleY: 1 }, bounds: [-10, -10, 10, 10] }),
      ]),
    );
    m.setViewport(1920, 1080);
    const child = rect(m, 'child');
    expect(child.x).toBeCloseTo(940, 9);
    expect(child.y).toBeCloseTo(720, 9);
    expect(child.width).toBeCloseTo(40, 9);
    expect(child.height).toBeCloseTo(40, 9);
    // A group's rect is the union of its visible descendants.
    expect(rect(m, 'parent')).toEqual(child);
  });

  it('sizes text by its measured layout', () => {
    const m = model(
      canvas([
        element({
          id: 'title',
          type: 'text',
          anchor: 'center',
          transform: { x: 960, y: 540, rotation: 0, scaleX: 1, scaleY: 1 },
          text: {
            body: 'Hello',
            font: '',
            size: 100,
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
        }),
      ]),
    );
    m.setViewport(1920, 1080);
    const r = rect(m, 'title');
    expect(r.width).toBeCloseTo(5 * 55, 6); // Fallback estimate: 0.55 em per character.
    expect(r.height).toBeCloseTo(100, 6);
    expect(r.x + r.width / 2).toBeCloseTo(960, 6);
    expect(r.y + r.height / 2).toBeCloseTo(540, 6);
  });
});

describe('Game UI visibility and button states', () => {
  const states = () =>
    canvas([
      element({ id: 'btn', transform: { x: 960, y: 540, rotation: 0, scaleX: 1, scaleY: 1 }, anchor: 'center', components: { UiButton: { action: 'event', focusScale: 2 } } }),
      mesh({ id: 'bg', parent: 'btn', bounds: [-100, -20, 100, 20] }),
      mesh({ id: 'focus', parent: 'btn', bounds: [-110, -25, 110, 25], components: { UiState: { show: 'focused' } } }),
      mesh({ id: 'press', parent: 'btn', bounds: [-100, -20, 100, 20], components: { UiState: { show: 'pressed' } } }),
      mesh({ id: 'either', parent: 'btn', bounds: [-100, -20, 100, 20], components: { UiState: { show: 'focusedOrPressed' } } }),
      mesh({ id: 'normal', parent: 'btn', bounds: [-100, -20, 100, 20], components: { UiState: { show: 'normal' } } }),
      mesh({ id: 'off', parent: 'btn', bounds: [-100, -20, 100, 20], components: { UiState: { show: 'disabled' } } }),
      element({ id: 'hidden', visible: false, transform: { x: 10, y: 10, rotation: 0, scaleX: 1, scaleY: 1 } }),
      mesh({ id: 'inner', parent: 'hidden', bounds: [0, 0, 5, 5] }),
    ]);
  const visible = (m: CanvasModel) => {
    m.refresh();
    return m.nodes.filter((n) => n.visible).map((n) => n.el.id);
  };

  it('shows UiState art per state of the nearest button', () => {
    const m = model(states());
    m.setViewport(1920, 1080);
    expect(visible(m)).toEqual(['btn', 'bg', 'normal']);
    m.setButtonState('btn', null);
    expect(visible(m)).toEqual(['btn', 'bg', 'focus', 'either']);
    m.setButtonState('btn', 'btn');
    expect(visible(m)).toEqual(['btn', 'bg', 'press', 'either']);
    m.setButtonState(null, 'btn');
    expect(visible(m)).toEqual(['btn', 'bg', 'press', 'either']);
    // Ids that are not buttons count as none.
    m.setButtonState('bg', null);
    expect(visible(m)).toEqual(['btn', 'bg', 'normal']);
  });

  it('shows disabled art for a disabled button whatever its state', () => {
    const data = states();
    data.elements[0]!.components.UiButton!.disabled = true;
    const m = model(data);
    m.setButtonState('btn', 'btn');
    expect(visible(m)).toEqual(['btn', 'bg', 'off']);
  });

  it('hides children of hidden parents, and code overrides authored visibility', () => {
    const m = model(states());
    expect(visible(m)).not.toContain('inner');
    m.setVisible(m.byId.get('hidden')!, true);
    expect(visible(m)).toContain('inner');
    m.setVisible(m.byId.get('bg')!, false);
    expect(visible(m)).not.toContain('bg');
    m.setVisible(m.byId.get('bg')!, null);
    expect(visible(m)).toContain('bg');
  });

  it('scales a focused button about its centre for drawing only', () => {
    const m = model(states());
    m.setViewport(1920, 1080);
    m.setButtonState('btn', null);
    m.refresh();
    const btn = m.byId.get('btn')!;
    const bg = m.byId.get('bg')!;
    // Its area includes the visible focus art.
    expect(m.rectOf(btn)).toEqual({ x: 850, y: 515, width: 220, height: 50 });
    expect([bg.draw[0], bg.draw[3]]).toEqual([2, 2]);
    const drawn = transformBounds([0, 0, 0, 0], bg.draw, [-100, -20, 100, 20]);
    expect(drawn).toEqual([760, 500, 1160, 580]);
    // Base matrices (hit areas) are unscaled.
    expect(bg.base[0]).toBe(1);
  });
});

describe('Game UI bars', () => {
  const bar = (direction: string, components: Record<string, unknown> = {}) =>
    canvas([
      element({
        id: 'bar',
        anchor: 'top-left',
        transform: { x: 100, y: 100, rotation: 0, scaleX: 1, scaleY: 1 },
        components: { UiBar: { value: 'hp', max: 10, fill: 'fill', direction, smoothing: 0, ...components } },
      }),
      mesh({ id: 'fill', parent: 'bar', bounds: [0, -10, 200, 10] }),
    ]);

  it('scales the fill from its starting edge', () => {
    const store: Record<string, unknown> = { hp: 5 };
    const right = model(bar('right'), store);
    expect(rect(right, 'fill')).toEqual({ x: 100, y: 90, width: 100, height: 20 });
    const left = model(bar('left'), store);
    expect(rect(left, 'fill')).toEqual({ x: 200, y: 90, width: 100, height: 20 });
    const up = model(bar('up'), store);
    expect(rect(up, 'fill')).toEqual({ x: 100, y: 100, width: 200, height: 10 });
    const down = model(bar('down'), store);
    expect(rect(down, 'fill')).toEqual({ x: 100, y: 90, width: 200, height: 10 });
    store.hp = 50; // Clamped to full.
    expect(rect(right, 'fill').width).toBe(200);
    store.hp = undefined; // Bound but missing: empty.
    expect(rect(right, 'fill').width).toBe(0);
  });

  it('takes code values over the binding until null', () => {
    const store: Record<string, unknown> = { hp: 5 };
    const m = model(bar('right'), store);
    const node = m.byId.get('bar')!;
    m.setBar(node, 2);
    expect(rect(m, 'fill').width).toBe(40);
    m.setBar(node, null);
    expect(rect(m, 'fill').width).toBe(100);
    // Without a binding and a value, the fill keeps its authored size.
    const unbound = model(bar('right', { value: '' }));
    expect(rect(unbound, 'fill').width).toBe(200);
  });

  it('eases toward new values with smoothing', () => {
    const store: Record<string, unknown> = { hp: 5 };
    const m = model(bar('right', { smoothing: 1 }), store);
    m.step(0.016);
    expect(m.byId.get('bar')!.barShown).toBe(0.5); // The first value shows at once.
    store.hp = 10;
    m.step(0.25);
    const shown = m.byId.get('bar')!.barShown;
    expect(shown).toBeCloseTo(0.5 + 0.5 * (1 - Math.exp(-1)), 9);
    for (let i = 0; i < 100; i++) m.step(0.05);
    expect(m.byId.get('bar')!.barShown).toBe(1);
  });
});
