// Fonts and images for canvases (docs/GAME-UI.md §5.1): each URL loads once (FontFace +
// document.fonts.add, or an <img>), shared by every canvas. Each registry has a version that bumps when
// something finishes loading, so text re-measures and textured elements appear. Without FontFace or
// Image (Node tests) loads resolve at once and the fallback font is used.
//
// Variable fonts (§11.5) draw the instance Blender draws: a text's exported `fontAxes`, else the defaults
// of the font's 'fvar' axes, else (a WOFF2 the browser cannot read) Blender's Inter defaults. Each font
// file and set of axis values is a FontFace of its own with those values pinned (`variationSettings`).
import type { UiCanvasData } from './format';
import { FALLBACK_FAMILY } from './text';

interface FontEntry {
  family: string;
  ready: Promise<void>;
  loaded: boolean;
}

/** A variable font's axis values (GAME-UI §11.5): OpenType axis tag -> value, e.g. `{ wght: 700, opsz: 32 }`. */
export type FontAxes = Readonly<Record<string, number>>;

/** An OpenType axis tag (4 printable ASCII characters) that is safe inside a quoted CSS string. */
const AXIS_TAG = /^(?:(?!['"\\])[\x20-\x7e]){4}$/;

/**
 * A FontFace `variationSettings` value for axis values, tags sorted (`'opsz' 32, 'wght' 700`), or null
 * when there are none. Tags that are not 4 printable characters, and values that are not finite numbers,
 * are left out.
 */
export function variationSettings(axes: FontAxes | null | undefined): string | null {
  if (!axes || typeof axes !== 'object' || Array.isArray(axes)) return null;
  const parts: string[] = [];
  for (const tag of Object.keys(axes).sort()) {
    const value = axes[tag];
    if (!AXIS_TAG.test(tag) || typeof value !== 'number' || !Number.isFinite(value)) continue;
    parts.push(`'${tag}' ${Math.round(value * 1000) / 1000}`);
  }
  return parts.length ? parts.join(', ') : null;
}

const axesByCanvas = new WeakMap<UiCanvasData, Map<string, FontAxes>>();

/**
 * The `fontAxes` the canvas's texts give for a font asset path, or undefined (a static font, or an export
 * that could not read it). The export writes the font file's default instance (what Blender draws), the
 * same for every text using the file; the first text that has them counts.
 */
export function fontAxesOf(data: UiCanvasData, font: string): FontAxes | undefined {
  let byFont = axesByCanvas.get(data);
  if (!byFont) {
    byFont = new Map();
    for (const el of data.elements ?? []) {
      const text = el.type === 'text' ? el.text : undefined;
      if (!text?.font || byFont.has(text.font) || !variationSettings(text.fontAxes)) continue;
      byFont.set(text.font, text.fontAxes!);
    }
    axesByCanvas.set(data, byFont);
  }
  return byFont.get(font);
}

export class FontRegistry {
  /** Bumps whenever a font finishes loading. */
  version = 0;
  private readonly fonts = new Map<string, FontEntry>();
  private count = 0;

  constructor(
    private readonly warn: (message: string) => void,
    private readonly changed: () => void = () => {},
  ) {}

  /**
   * The CSS font-family list for a font URL (null or '' = the fallback) drawn at `axes` (a variable
   * font's `fontAxes`; absent: its default instance). Starts loading it.
   */
  family(url: string | null, axes?: FontAxes | null): string {
    if (!url) return FALLBACK_FAMILY;
    return this.entry(url, variationSettings(axes)).family;
  }

  /** Resolves when the font at `url` (at `axes`) loaded or failed. */
  load(url: string | null, axes?: FontAxes | null): Promise<void> {
    return url ? this.entry(url, variationSettings(axes)).ready : Promise.resolve();
  }

  private entry(url: string, pinned: string | null): FontEntry {
    const key = pinned ? `${url}\n${pinned}` : url;
    let entry = this.fonts.get(key);
    if (entry) return entry;
    const name = `gs-ui-font-${++this.count}`;
    const Face = (globalThis as { FontFace?: typeof FontFace }).FontFace;
    const set = typeof document !== 'undefined' ? (document as { fonts?: FontFaceSet }).fonts : undefined;
    if (typeof Face !== 'function' || !set || typeof set.add !== 'function') {
      entry = { family: FALLBACK_FAMILY, ready: Promise.resolve(), loaded: false };
      this.fonts.set(key, entry);
      return entry;
    }
    const created: FontEntry = { family: `"${name}", ${FALLBACK_FAMILY}`, ready: Promise.resolve(), loaded: false };
    created.ready = (async () => {
      try {
        // A variable font draws one instance, as Blender (FreeType) does: browsers set `opsz` to the font
        // size (and `wght`/`wdth` from CSS), so its axes are pinned: to the exported values, else to the
        // font's defaults.
        let source: string | ArrayBuffer = `url(${JSON.stringify(url)})`;
        const descriptors: FontFaceDescriptors = {};
        if (pinned) (descriptors as { variationSettings?: string }).variationSettings = pinned;
        try {
          const res = typeof fetch === 'function' ? await fetch(url) : null;
          if (res?.ok) {
            const bytes = await res.arrayBuffer();
            source = bytes;
            const settings = pinned ?? (await variationDefaults(bytes, url));
            if (settings) (descriptors as { variationSettings?: string }).variationSettings = settings;
          }
        } catch {
          // The face loads from its URL instead.
        }
        const face = new Face(name, source, descriptors);
        set.add(face);
        await face.load();
        created.loaded = true;
        this.version++;
        this.changed();
      } catch {
        this.warn(`Game UI: could not load the font ${url}; using ${FALLBACK_FAMILY}`);
      }
    })();
    this.fonts.set(key, created);
    return created;
  }
}

/** A font's variation axes with their defaults ('fvar'), or null for a static font. */
export interface FontAxis {
  tag: string;
  min: number;
  default: number;
  max: number;
}

/** The axes in an 'fvar' table. */
export function parseFvar(fvar: DataView): FontAxis[] {
  const axesOffset = fvar.getUint16(4);
  const count = fvar.getUint16(8);
  const size = fvar.getUint16(10);
  const axes: FontAxis[] = [];
  for (let i = 0; i < count; i++) {
    const at = axesOffset + i * size;
    if (at + 20 > fvar.byteLength) break;
    const tag = String.fromCharCode(fvar.getUint8(at), fvar.getUint8(at + 1), fvar.getUint8(at + 2), fvar.getUint8(at + 3));
    const fixed = (o: number) => fvar.getInt32(at + o) / 65536;
    axes.push({ tag, min: fixed(4), default: fixed(8), max: fixed(12) });
  }
  return axes;
}

const tagOf = (view: DataView, at: number): string =>
  String.fromCharCode(view.getUint8(at), view.getUint8(at + 1), view.getUint8(at + 2), view.getUint8(at + 3));

async function inflate(data: Uint8Array): Promise<Uint8Array | null> {
  const Stream = (globalThis as { DecompressionStream?: new (format: string) => TransformStream<Uint8Array, Uint8Array> }).DecompressionStream;
  if (typeof Stream !== 'function' || typeof Response !== 'function') return null;
  const stream = new Response(data as unknown as BodyInit).body!.pipeThrough(new Stream('deflate'));
  return new Uint8Array(await new Response(stream).arrayBuffer());
}

/**
 * The 'fvar' axes of a TrueType/OpenType font (the first of a collection) or a WOFF file; null when it
 * has none or cannot be read. WOFF2 data is Brotli-compressed, which browsers cannot decompress: only
 * whether it has 'fvar' is known (`[]`).
 */
export async function fontAxes(bytes: ArrayBuffer): Promise<FontAxis[] | null> {
  const view = new DataView(bytes);
  if (view.byteLength < 12) return null;
  const magic = tagOf(view, 0);
  try {
    if (magic === 'wOF2') {
      const count = view.getUint16(12);
      let at = 48;
      for (let i = 0; i < count && at < view.byteLength; i++) {
        const flags = view.getUint8(at);
        let tag: string;
        at += 1;
        if ((flags & 0x3f) === 0x3f) {
          tag = tagOf(view, at);
          at += 4;
        } else {
          tag = WOFF2_TAGS[flags & 0x3f] ?? '';
        }
        if (tag === 'fvar') return [];
        // origLength, then transformLength when the table is transformed (glyf/loca by default, hmtx when flagged).
        const version = (flags >> 6) & 3;
        const transformed = tag === 'glyf' || tag === 'loca' ? version === 0 : version !== 0;
        at = skipBase128(view, at);
        if (transformed) at = skipBase128(view, at);
      }
      return null;
    }
    if (magic === 'wOFF') {
      const count = view.getUint16(12);
      for (let i = 0; i < count; i++) {
        const at = 44 + 20 * i;
        if (tagOf(view, at) !== 'fvar') continue;
        const offset = view.getUint32(at + 4);
        const length = view.getUint32(at + 8);
        const original = view.getUint32(at + 12);
        const raw = new Uint8Array(bytes, offset, length);
        const data = length < original ? await inflate(raw) : raw;
        return data ? parseFvar(new DataView(data.buffer, data.byteOffset, data.byteLength)) : null;
      }
      return null;
    }
    const base = magic === 'ttcf' ? view.getUint32(12) : 0;
    const count = view.getUint16(base + 4);
    for (let i = 0; i < count; i++) {
      const at = base + 12 + 16 * i;
      if (tagOf(view, at) !== 'fvar') continue;
      return parseFvar(new DataView(bytes, view.getUint32(at + 8), view.getUint32(at + 12)));
    }
  } catch {
    return null;
  }
  return null;
}

function skipBase128(view: DataView, at: number): number {
  for (let i = 0; i < 5; i++) if ((view.getUint8(at++) & 0x80) === 0) break;
  return at;
}

/** WOFF2's known-table index (flags & 63). */
const WOFF2_TAGS = [
  'cmap', 'head', 'hhea', 'hmtx', 'maxp', 'name', 'OS/2', 'post', 'cvt ', 'fpgm', 'glyf', 'loca', 'prep', 'CFF ', 'VORG', 'EBDT',
  'EBLC', 'gasp', 'hdmx', 'kern', 'LTSH', 'PCLT', 'VDMX', 'vhea', 'vmtx', 'BASE', 'GDEF', 'GPOS', 'GSUB', 'EBSC', 'JSTF', 'MATH',
  'CBDT', 'CBLC', 'COLR', 'CPAL', 'SVG ', 'sbix', 'acnt', 'avar', 'bdat', 'bloc', 'bsln', 'cvar', 'fdsc', 'feat', 'fmtx', 'fvar',
  'gvar', 'hsty', 'just', 'lcar', 'mort', 'morx', 'opbd', 'prop', 'trak', 'Zapf', 'Silf', 'Glat', 'Gloc', 'Feat', 'Sill',
];

/** Blender's bundled Inter.woff2 (its default font in GameStudio): a WOFF2 whose axes cannot be read here. */
const INTER_DEFAULTS = "'opsz' 16, 'wght' 400";
/** Blender lays text out at optical size 16 whatever its size (measured in Blender 5.2), other axes at their defaults. */
const BLENDER_OPSZ = 16;

/**
 * A FontFace `variationSettings` value that pins a variable font to the instance Blender (FreeType) draws,
 * or null for a static font: for texts the export gave no `fontAxes`. Every axis at its default except
 * `opsz`, which is 16 (clamped to the axis's range). A WOFF2 variable font is pinned only when it is Inter.
 */
export async function variationDefaults(bytes: ArrayBuffer, url = ''): Promise<string | null> {
  const axes = await fontAxes(bytes);
  if (!axes) return null;
  if (axes.length === 0) return /(^|[/\\])Inter[^/\\]*\.woff2(\?|#|$)/i.test(url) ? INTER_DEFAULTS : null;
  const value = (a: FontAxis): number => (a.tag === 'opsz' ? Math.min(a.max, Math.max(a.min, BLENDER_OPSZ)) : a.default);
  return axes.map((a) => `'${a.tag}' ${Math.round(value(a) * 1000) / 1000}`).join(', ');
}

export interface ImageEntry {
  image: HTMLImageElement | null;
  loaded: boolean;
  ready: Promise<void>;
}

export class ImageRegistry {
  /** Bumps whenever an image finishes loading. */
  version = 0;
  private readonly images = new Map<string, ImageEntry>();

  constructor(
    private readonly warn: (message: string) => void,
    private readonly changed: () => void = () => {},
  ) {}

  /** The image at `url`, loading it on first use. `image` is null without DOM images (tests). */
  get(url: string): ImageEntry {
    let entry = this.images.get(url);
    if (entry) return entry;
    const Img = (globalThis as { Image?: typeof Image }).Image;
    if (typeof Img !== 'function') {
      entry = { image: null, loaded: false, ready: Promise.resolve() };
      this.images.set(url, entry);
      return entry;
    }
    const image = new Img();
    const created: ImageEntry = { image, loaded: false, ready: Promise.resolve() };
    created.ready = new Promise<void>((resolve) => {
      image.onload = () => {
        created.loaded = true;
        this.version++;
        this.changed();
        resolve();
      };
      image.onerror = () => {
        this.warn(`Game UI: could not load the image ${url}`);
        resolve();
      };
    });
    image.crossOrigin = 'anonymous';
    image.decoding = 'async';
    image.src = url;
    this.images.set(url, created);
    return created;
  }
}
