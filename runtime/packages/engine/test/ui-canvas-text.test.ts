import { describe, expect, it } from 'vitest';
import type { UiTextData } from '../src/ui/canvas/format';
import {
  DEFAULT_STYLE,
  cssColor,
  fallbackMeasurer,
  layoutText,
  rasterizeText,
  textQuad,
  type TextMeasurer,
} from '../src/ui/canvas/text';

// Every character 0.5 em wide; ascent 0.8 em, descent 0.2 em.
const measurer: TextMeasurer = {
  width: (_family, text) => Array.from(text).length * 0.5,
  metrics: () => ({ ascent: 0.8, descent: 0.2 }),
  invalidate() {},
};

function text(overrides: Partial<UiTextData> = {}): UiTextData {
  return {
    body: '',
    font: '',
    size: 100,
    color: [1, 1, 1, 1],
    align: 'left',
    valign: 'top-baseline',
    lineSpacing: 1,
    letterSpacing: 1,
    wordSpacing: 1,
    box: null,
    offset: [0, 0],
    shear: 0,
    ...overrides,
  };
}

const lay = (body: string, data: Partial<UiTextData> = {}, fit = false) => layoutText(body, text(data), fit, 'x', measurer);

describe('Game UI text layout (Blender rules)', () => {
  it('aligns a line around the origin without a box', () => {
    expect(lay('abcd', { align: 'left' }).lines[0]).toMatchObject({ x: 0, baseline: 0, width: 200 });
    expect(lay('abcd', { align: 'center' }).lines[0]!.x).toBe(-100);
    expect(lay('abcd', { align: 'right' }).lines[0]!.x).toBe(-200);
    // Justify and flush need a box: left.
    expect(lay('abcd', { align: 'justify' }).lines[0]!.x).toBe(0);
    expect(lay('abcd', { align: 'center' }).bounds).toEqual([-100, -80, 100, 20]);
  });

  it('places lines vertically relative to the origin', () => {
    const baselines = (valign: UiTextData['valign']) => lay('ab\ncd', { valign }).lines.map((l) => l.baseline);
    expect(baselines('top-baseline')).toEqual([0, 100]);
    expect(baselines('top')).toEqual([80, 180]);
    expect(baselines('center')).toEqual([-20, 80]); // Block -100..100 centred on 0.
    expect(baselines('bottom-baseline')).toEqual([-100, 0]);
    expect(baselines('bottom')).toEqual([-120, -20]);
    expect(lay('ab\ncd', { valign: 'center' }).bounds).toEqual([0, -100, 100, 100]);
    expect(lay('ab\ncd', { lineSpacing: 1.5 }).lines.map((l) => l.baseline)).toEqual([0, 150]);
  });

  it('applies the offset to everything', () => {
    const layout = lay('ab', { offset: [10, 20] });
    expect(layout.lines[0]).toMatchObject({ x: 10, baseline: 20 });
    expect(layout.bounds).toEqual([10, -60, 110, 40]);
  });

  it('wraps at the last space before the box width and aligns within the box', () => {
    const box: UiTextData['box'] = [-125, 0, 250, 0];
    const layout = lay('aa bb cc', { box, align: 'center' });
    expect(layout.lines.map((l) => l.text)).toEqual(['aa bb', 'cc']);
    expect(layout.lines.map((l) => l.x)).toEqual([-125, -50]);
    expect(lay('aa bb cc', { box, align: 'right' }).lines.map((l) => l.x)).toEqual([-125, 25]);
    // Blender never breaks a word: one wider than the box runs past it.
    expect(lay('abcd', { box: [0, 0, 100, 0] }).lines.map((l) => l.text)).toEqual(['abcd']);
    expect(lay('aa bbbbbb', { box: [0, 0, 200, 0] }).lines.map((l) => l.text)).toEqual(['aa', 'bbbbbb']);
    // It also wraps after a hyphen, which stays at the end of the line.
    const hyphen = lay('ab-cd', { box: [0, 0, 150, 0] });
    expect(hyphen.lines.map((l) => l.text)).toEqual(['ab-', 'cd']);
    expect(hyphen.lines[0]!.glyphs.xs).toEqual([0, 50, 100]);
    // Explicit newlines always break.
    expect(lay('a\nb', { box: [0, 0, 1000, 0] }).lines.map((l) => l.text)).toEqual(['a', 'b']);
  });

  it('aligns vertically within a box frame that has a height', () => {
    // The frame is the box; Blender's own text box y (top-baseline) is one size below its top.
    const box: UiTextData['box'] = [0, 0, 1000, 400];
    expect(lay('ab', { box, valign: 'top-baseline' }).lines[0]!.baseline).toBe(100);
    expect(lay('ab', { box, valign: 'top' }).lines[0]!.baseline).toBe(80);
    expect(lay('ab', { box, valign: 'center' }).lines[0]!.baseline).toBe(230); // 200 − 50 + 80
    expect(lay('ab', { box, valign: 'bottom' }).lines[0]!.baseline).toBe(380);
    expect(lay('ab', { box, valign: 'bottom-baseline' }).lines[0]!.baseline).toBe(400);
    expect(lay('ab\ncd', { box, valign: 'bottom' }).lines.map((l) => l.baseline)).toEqual([280, 380]);
    // A box without a height only wraps: the rules without a box apply about its y.
    expect(lay('ab', { box: [0, 50, 1000, 0], valign: 'top-baseline' }).lines[0]!.baseline).toBe(50);
    expect(lay('ab', { box: [0, 50, 1000, 0], valign: 'top' }).lines[0]!.baseline).toBe(130);
  });

  it('justifies wrapped lines by their spaces and flushes every line by its characters', () => {
    const justified = lay('aa bb cc', { box: [0, 0, 300, 0], align: 'justify' });
    expect(justified.lines[0]!.text).toBe('aa bb');
    expect(justified.lines[0]!.glyphs.xs).toEqual([0, 50, 150, 200, 250]);
    expect(justified.lines[0]!.width).toBe(300);
    expect(justified.lines[1]).toMatchObject({ text: 'cc', x: 0, width: 100 }); // Last line: left.
    expect(justified.lines[1]!.glyphs.xs).toEqual([0, 50]);
    const flushed = lay('abc', { box: [0, 0, 300, 0], align: 'flush' });
    expect(flushed.lines[0]!.glyphs.xs).toEqual([0, 125, 250]);
    // Both need a box.
    expect(lay('a b', { align: 'justify' }).lines[0]!.glyphs.xs).toEqual([0, 50, 100]);
  });

  it('adds Blender letter and word spacing', () => {
    // space_character 2 adds (2 − 1) / 2 of the size after each character; the line's width leaves
    // out the last one.
    const letters = lay('ab', { letterSpacing: 2 }).lines[0]!;
    expect(letters.width).toBe(150);
    expect(letters.glyphs.xs).toEqual([0, 100]);
    expect(lay('ab', { letterSpacing: 2, align: 'center' }).lines[0]!.x).toBe(-75);
    // Below 1 it scales each advance instead.
    const tight = lay('abc', { letterSpacing: 0.5 }).lines[0]!;
    expect(tight.glyphs.xs).toEqual([0, 25, 50]);
    expect(tight.width).toBe(75);
    // space_word 2: spaces twice as wide.
    const words = lay('a b', { wordSpacing: 2 }).lines[0]!;
    expect(words.width).toBe(200);
    expect(words.glyphs.xs).toEqual([0, 50, 150]);
    // Every line is placed character by character (no kerning, no ligatures).
    expect(lay('ab').lines[0]!.glyphs).toEqual({ chars: ['a', 'b'], xs: [0, 50] });
  });

  it('draws glyphs at the exported font size and spaces by the size', () => {
    const data = { size: 100, fontSize: 50, ascent: 70, descent: 30 };
    const layout = lay('ab', data);
    expect(layout).toMatchObject({ size: 100, fontSize: 50, ascent: 70, descent: 30 });
    expect(layout.lines[0]!.glyphs.xs).toEqual([0, 25]);
    expect(layout.lines[0]!.width).toBe(50);
    expect(lay('ab', { ...data, letterSpacing: 2 }).lines[0]!.glyphs.xs).toEqual([0, 75]);
    expect(lay('ab', { ...data, valign: 'top' }).lines[0]!.baseline).toBe(70);
    expect(lay('ab\ncd', { ...data, valign: 'center' }).lines.map((l) => l.baseline)).toEqual([-30, 70]);
    // Without a readable font file: the size that gives the font the exported ascent.
    expect(lay('ab', { ...data, fontSize: null }).fontSize).toBeCloseTo(87.5, 6);
  });

  it('truncates the lines below the box as Blender does', () => {
    const box: UiTextData['box'] = [0, 0, 100, 250];
    expect(lay('a\nb\nc\nd', { box, overflow: 'truncate' }).lines.map((l) => l.text)).toEqual(['a', 'b']);
    expect(lay('a\nb\nc\nd', { box, overflow: 'none' }).lines).toHaveLength(4);
    // Without a height: only the first line.
    expect(lay('aa bb cc', { box: [0, 0, 120, 0], overflow: 'truncate' }).lines.map((l) => l.text)).toEqual(['aa']);
  });

  it('scales to fit the box with overflow scale, as Blender does', () => {
    // No height: the longest paragraph shrinks to the box width, unwrapped.
    const wide = lay('abcd', { box: [0, 0, 100, 0], overflow: 'scale' });
    expect(wide.size).toBeCloseTo(50, 6);
    expect(wide.lines).toHaveLength(1);
    expect(wide.lines[0]!.width).toBeCloseTo(100, 6);
    // With a height: the largest size whose lines fit.
    const tall = lay('a\nb\nc', { box: [0, 0, 1000, 150], overflow: 'scale' });
    expect(tall.size).toBeGreaterThanOrEqual(50);
    expect(tall.size).toBeLessThan(50.1);
    expect(tall.lines).toHaveLength(3);
    expect(lay('a', { box: [0, 0, 1000, 400], overflow: 'scale' }).size).toBe(100);
  });

  it('shrinks to fit the box when asked', () => {
    const one = lay('abcd', { box: [0, 0, 100, 0] }, true);
    expect(one.lines).toHaveLength(1);
    expect(one.size).toBeGreaterThan(49);
    expect(one.size).toBeLessThanOrEqual(50.26); // Fits within half a pixel.
    const fits = lay('ab', { box: [0, 0, 1000, 0] }, true);
    expect(fits.size).toBe(100);
    // With a height: wraps, then shrinks until the block fits.
    const tall = lay('aa bb cc dd', { box: [0, 0, 250, 150] }, true);
    const height = (tall.lines.length - 1) * tall.size + tall.ascent + tall.descent;
    expect(height).toBeLessThanOrEqual(150.5);
    for (const line of tall.lines) expect(line.width).toBeLessThanOrEqual(250.5);
  });

  it('has no bounds for empty text', () => {
    expect(lay('').bounds).toBeNull();
    expect(lay('').lines).toHaveLength(1);
  });

  it('estimates without Canvas2D', () => {
    const layout = layoutText('abcd', text({ size: 10 }), false, 'x', fallbackMeasurer);
    expect(layout.lines[0]!.width).toBeCloseTo(22, 6);
    expect(layout.ascent).toBeCloseTo(8, 6);
  });

  it('converts linear colours to CSS sRGB', () => {
    expect(cssColor([1, 0, 0.2, 0.5])).toBe('rgba(255,0,124,0.5)');
  });

  it('rasterises outline under fill, per glyph when spaced', () => {
    const calls: string[] = [];
    const ctx = new Proxy(
      {},
      {
        get: (_t, key) => (key === 'canvas' ? undefined : (...args: unknown[]) => calls.push(`${String(key)}:${String(args[0])}`)),
        set: (_t, key, value) => {
          calls.push(`${String(key)}=${String(value)}`);
          return true;
        },
      },
    ) as unknown as CanvasRenderingContext2D;
    const canvas = { width: 0, height: 0 } as HTMLCanvasElement;
    const layout = lay('ab', { letterSpacing: 2 });
    const paint = { family: 'x', color: [1, 1, 1, 1] as [number, number, number, number], style: { ...DEFAULT_STYLE, outline: 4 }, shear: 0 };
    const quad = textQuad(layout, paint)!;
    const drawn = rasterizeText({ canvas, ctx }, layout, paint, quad, 2)!;
    expect(drawn.width).toBe(Math.ceil((quad[2] - quad[0]) * 2));
    expect(canvas.width).toBe(drawn.width);
    expect(calls).toContain('font=100px x');
    expect(calls).toContain('fontKerning=none');
    expect(calls).toContain('lineWidth=8');
    const strokes = calls.filter((c) => c.startsWith('strokeText'));
    const fills = calls.filter((c) => c.startsWith('fillText'));
    expect(strokes).toEqual(['strokeText:a', 'strokeText:b']);
    expect(fills).toEqual(['fillText:a', 'fillText:b']);
    expect(calls.indexOf('strokeText:b')).toBeLessThan(calls.indexOf('fillText:a'));
  });
});
