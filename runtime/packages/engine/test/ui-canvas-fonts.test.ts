// Variable fonts in canvases (docs/GAME-UI.md §11.5): a text's exported `fontAxes` pin its font's
// FontFace to those axis values; without them a font draws its 'fvar' defaults, and Blender's Inter
// (a WOFF2 the browser cannot read) keeps its pinned defaults.
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { FontRegistry, fontAxesOf, variationDefaults, variationSettings } from '../src/ui/canvas/assets';
import type { UiCanvasData, UiElementData } from '../src/ui/canvas/format';

/** A TrueType font with just an 'fvar' table: `wght` 100..900 (default 400) and `opsz` 14..32 (default 14). */
function variableTtf(): ArrayBuffer {
  const axes: Array<[string, number, number, number]> = [
    ['wght', 100, 400, 900],
    ['opsz', 14, 14, 32],
  ];
  const fvarLength = 16 + axes.length * 20;
  const bytes = new ArrayBuffer(12 + 16 + fvarLength);
  const view = new DataView(bytes);
  const tag = (at: number, t: string) => [...t].forEach((c, i) => view.setUint8(at + i, c.charCodeAt(0)));
  view.setUint32(0, 0x00010000);
  view.setUint16(4, 1); // one table
  tag(12, 'fvar');
  view.setUint32(12 + 8, 28); // offset
  view.setUint32(12 + 12, fvarLength);
  const f = 28;
  view.setUint16(f + 4, 16); // axes offset
  view.setUint16(f + 8, axes.length);
  view.setUint16(f + 10, 20); // axis size
  axes.forEach(([t, min, def, max], i) => {
    const at = f + 16 + i * 20;
    tag(at, t);
    view.setInt32(at + 4, min * 65536);
    view.setInt32(at + 8, def * 65536);
    view.setInt32(at + 12, max * 65536);
  });
  return bytes;
}

interface MadeFace {
  family: string;
  source: unknown;
  descriptors: FontFaceDescriptors;
}

describe('variable font axes', () => {
  const g = globalThis as Record<string, unknown>;
  const saved = { FontFace: g.FontFace, document: g.document, fetch: g.fetch };
  let faces: MadeFace[];
  let files: Record<string, ArrayBuffer>;

  beforeEach(() => {
    faces = [];
    files = {};
    g.FontFace = class {
      constructor(family: string, source: unknown, descriptors: FontFaceDescriptors) {
        faces.push({ family, source, descriptors });
      }
      load() {
        return Promise.resolve(this);
      }
    };
    g.document = { fonts: { add() {} } };
    g.fetch = async (url: string) => {
      const body = files[url];
      return body ? { ok: true, arrayBuffer: async () => body } : { ok: false };
    };
  });
  afterEach(() => {
    g.FontFace = saved.FontFace;
    g.document = saved.document;
    g.fetch = saved.fetch;
  });

  it('writes axis values as FontFace variation settings, tags sorted, bad entries left out', () => {
    expect(variationSettings({ wght: 700, opsz: 32.12345 })).toBe("'opsz' 32.123, 'wght' 700");
    expect(variationSettings({ 'wg"t': 1, slnt: Number.NaN, wdth: 'wide' as unknown as number, ital: 1 })).toBe("'ital' 1");
    expect(variationSettings({})).toBeNull();
    expect(variationSettings(undefined)).toBeNull();
  });

  it("finds a font's exported axes in a canvas", () => {
    const text = (id: string, font: string, fontAxes?: Record<string, number>): UiElementData =>
      ({
        id,
        name: id,
        parent: null,
        type: 'text',
        visible: true,
        order: 0,
        components: {},
        transform: { x: 0, y: 0, rotation: 0, scaleX: 1, scaleY: 1 },
        text: { body: id, font, size: 40, color: [1, 1, 1, 1], align: 'left', valign: 'top', lineSpacing: 1, letterSpacing: 1, wordSpacing: 1, box: null, offset: [0, 0], shear: 0, fontAxes },
      }) as UiElementData;
    const data = { elements: [text('A', 'assets/fonts/Static.ttf'), text('B', 'assets/fonts/Roboto.ttf', { wght: 400, wdth: 100 }), text('C', 'assets/fonts/Roboto.ttf', { wght: 900 })] } as UiCanvasData;
    expect(fontAxesOf(data, 'assets/fonts/Roboto.ttf')).toEqual({ wght: 400, wdth: 100 });
    expect(fontAxesOf(data, 'assets/fonts/Static.ttf')).toBeUndefined();
  });

  it('pins a font to its exported axes, a face per set of values', async () => {
    files['/f/Roboto.ttf'] = variableTtf();
    const fonts = new FontRegistry(() => {});
    const bold = fonts.family('/f/Roboto.ttf', { wght: 700, opsz: 20 });
    const regular = fonts.family('/f/Roboto.ttf', { wght: 400 });
    expect(bold).not.toBe(regular);
    expect(fonts.family('/f/Roboto.ttf', { opsz: 20, wght: 700 })).toBe(bold); // the same values, any order
    await fonts.load('/f/Roboto.ttf', { wght: 700, opsz: 20 });
    await fonts.load('/f/Roboto.ttf', { wght: 400 });
    expect(faces.map((f) => (f.descriptors as { variationSettings?: string }).variationSettings)).toEqual(["'opsz' 20, 'wght' 700", "'wght' 400"]);
    expect(faces[0]!.source).toBeInstanceOf(ArrayBuffer);
    expect(fonts.version).toBe(2);
  });

  it("pins a font to Blender's instance (defaults, opsz 16) without exported axes, and Inter's", async () => {
    files['/f/Roboto.ttf'] = variableTtf();
    expect(await variationDefaults(variableTtf())).toBe("'wght' 400, 'opsz' 16");
    const fonts = new FontRegistry(() => {});
    await fonts.load('/f/Roboto.ttf');
    // Inter.woff2: a WOFF2 with 'fvar' (index 47 in WOFF2's known tables), which the browser cannot read.
    const woff2 = new ArrayBuffer(64);
    const view = new DataView(woff2);
    [...'wOF2'].forEach((c, i) => view.setUint8(i, c.charCodeAt(0)));
    view.setUint16(12, 1);
    view.setUint8(48, 47);
    files['/f/Inter.woff2'] = woff2;
    await fonts.load('/f/Inter.woff2');
    // A face pinned by the export drops neither (the fetch still serves the bytes).
    await fonts.load('/f/Inter.woff2', { opsz: 32, wght: 600 });
    expect(faces.map((f) => (f.descriptors as { variationSettings?: string }).variationSettings)).toEqual([
      "'wght' 400, 'opsz' 16",
      "'opsz' 16, 'wght' 400",
      "'opsz' 32, 'wght' 600",
    ]);
  });

  it('pins the exported axes when the font cannot be fetched (it loads from its URL)', async () => {
    const fonts = new FontRegistry(() => {});
    await fonts.load('/f/Missing.ttf', { wght: 300 });
    expect(faces[0]!.source).toBe('url("/f/Missing.ttf")');
    expect((faces[0]!.descriptors as { variationSettings?: string }).variationSettings).toBe("'wght' 300");
  });
});
