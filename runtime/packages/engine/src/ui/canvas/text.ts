// Canvas text (docs/GAME-UI.md §5.3): measures and lays out text the way Blender's text objects do, and
// rasterises it with Canvas2D for a CanvasTexture. Layout is in the element's local px (y down) and does
// not depend on screen scale; rasterising does. Without Canvas2D (Node tests) measuring falls back to a
// fixed estimate and nothing is rasterised.
//
// The layout is a port of Blender's vfont_to_curve (blenkernel/intern/vfont_curve.cc, Blender 5.2) for
// one text box: the same wrapping, letter and word spacing, alignment, vertical alignment, overflow and
// scale-to-fit. Blender places each character by its own advance (no kerning, no ligatures), so every
// line is drawn character by character at the positions computed here.
import type { UiColor, UiTextData } from './format';
import type { Bounds } from './layout';

/** Baseline-to-baseline distance per unit of `lineSpacing`, in units of `size` (Blender: space_line × size). */
export const LINE_ADVANCE = 1;
/**
 * Blender's letter spacing (space_character `s`) for a character `advance` px wide in a text of `size` px
 * (XTRAX_WITH_CHAR_WIDTH): expanding (s ≥ 1) adds (s − 1) / 2 of the size after every character,
 * contracting (s < 1) scales the advance by s.
 */
export function letterExtra(letterSpacing: number, advance: number, size: number): number {
  return letterSpacing < 1 ? (letterSpacing - 1) * advance : (letterSpacing - 1) * 0.5 * size;
}
/** The engine's font when a text has none (or before its font loads). */
export const FALLBACK_FAMILY = 'system-ui, sans-serif';

/** Measures runs of text, in em, for a CSS font-family list. */
export interface TextMeasurer {
  /** Advance width of `text` in em (no kerning). Layout measures one character at a time. */
  width(family: string, text: string): number;
  /** Font-box ascent and descent in em (both positive). */
  metrics(family: string): { ascent: number; descent: number };
  /** Forgets cached metrics (a font finished loading). */
  invalidate(): void;
}

/** The estimate used without Canvas2D: 0.55 em per character, ascent 0.8 em, descent 0.2 em. */
export const fallbackMeasurer: TextMeasurer = {
  width: (_family, text) => charCount(text) * 0.55,
  metrics: () => ({ ascent: 0.8, descent: 0.2 }),
  invalidate() {},
};

function charCount(text: string): number {
  let n = 0;
  for (const _ of text) n++;
  return n;
}

type Context2D = CanvasRenderingContext2D;

/** A 2D context on a new canvas element, or null when the DOM has none (Node tests). */
export function create2d(): { canvas: HTMLCanvasElement; ctx: Context2D } | null {
  try {
    if (typeof document === 'undefined' || typeof document.createElement !== 'function') return null;
    const canvas = document.createElement('canvas');
    if (typeof (canvas as { getContext?: unknown }).getContext !== 'function') return null;
    const ctx = canvas.getContext('2d');
    return ctx ? { canvas, ctx } : null;
  } catch {
    return null;
  }
}

/** Blender applies no font kerning (and no ligatures: it places characters one by one). */
function noKerning(ctx: Context2D): void {
  try {
    (ctx as { fontKerning?: string }).fontKerning = 'none';
  } catch {
    // Older browsers: single characters are measured and drawn anyway.
  }
}

const REF = 100;

/** Measures with Canvas2D at a fixed reference size; null without Canvas2D. */
export function canvasMeasurer(): TextMeasurer | null {
  const made = create2d();
  if (!made) return null;
  const { ctx } = made;
  let font = '';
  const metrics = new Map<string, { ascent: number; descent: number }>();
  // Single characters, per family: layout measures every character on its own.
  const chars = new Map<string, Map<string, number>>();
  const use = (family: string) => {
    const next = `${REF}px ${family}`;
    if (next !== font) {
      ctx.font = font = next;
      noKerning(ctx);
    }
  };
  return {
    width(family, text) {
      if (!text) return 0;
      let cache = chars.get(family);
      const single = text.length <= 2 && charCount(text) === 1;
      if (single) {
        const hit = cache?.get(text);
        if (hit !== undefined) return hit;
      }
      use(family);
      const w = ctx.measureText(text).width / REF;
      if (single) {
        if (!cache) chars.set(family, (cache = new Map()));
        cache.set(text, w);
      }
      return w;
    },
    metrics(family) {
      let m = metrics.get(family);
      if (!m) {
        use(family);
        const t = ctx.measureText('Hg');
        const ascent = Number.isFinite(t.fontBoundingBoxAscent) ? t.fontBoundingBoxAscent : t.actualBoundingBoxAscent;
        const descent = Number.isFinite(t.fontBoundingBoxDescent) ? t.fontBoundingBoxDescent : t.actualBoundingBoxDescent;
        m = Number.isFinite(ascent) && Number.isFinite(descent) && ascent + descent > 0
          ? { ascent: ascent / REF, descent: descent / REF }
          : { ascent: 0.8, descent: 0.2 };
        metrics.set(family, m);
      }
      return m;
    },
    invalidate() {
      metrics.clear();
      chars.clear();
      font = '';
    },
  };
}

/** UiText values (defaults filled in). */
export interface TextStyle {
  outline: number;
  outlineColor: UiColor;
  shadow: [number, number];
  shadowBlur: number;
  shadowColor: UiColor;
  fit: boolean;
}

export const DEFAULT_STYLE: TextStyle = {
  outline: 0,
  outlineColor: [0, 0, 0, 1],
  shadow: [0, 0],
  shadowBlur: 0,
  shadowColor: [0, 0, 0, 0.6],
  fit: false,
};

export interface TextLine {
  /** The characters drawn on this line. */
  text: string;
  /** Left edge of the line (its pen origin), local px. */
  x: number;
  baseline: number;
  width: number;
  /** Every character and its x relative to `x` (Blender places them one by one). */
  glyphs: { chars: string[]; xs: number[] };
}

export interface TextLayout {
  /** Blender's text size in px: the unit of line advance and spacing (smaller than authored when shrunk to fit). */
  size: number;
  /** The CSS font size the glyphs are drawn at (Blender scales a font by its bounding box, not its em). */
  fontSize: number;
  ascent: number;
  descent: number;
  lines: TextLine[];
  /** Local px [minX, minY, maxX, maxY] of the lines' font boxes; null when there is nothing to draw. */
  bounds: Bounds | null;
}

/** Sizes of one layout pass (all scale together when text shrinks to fit). */
interface Sizes {
  size: number;
  fontSize: number;
  ascent: number;
  descent: number;
}

/**
 * The text's sizes: Blender's `size`; the exported `fontSize` (glyphs as big as Blender draws them), else
 * the one that makes the font's ascent the exported `ascent`, else `size`; the exported ascent and
 * descent, else the font box's.
 */
function sizesOf(data: UiTextData, family: string, measurer: TextMeasurer): Sizes {
  const size = Math.max(0, data.size || 0);
  const m = measurer.metrics(family);
  const ascent = data.ascent;
  const descent = data.descent;
  const exported = Number.isFinite(ascent) && Number.isFinite(descent) && ascent! + descent! > 0;
  let fontSize = size;
  if (data.fontSize && data.fontSize > 0) fontSize = data.fontSize;
  else if (exported && m.ascent > 0) fontSize = ascent! / m.ascent;
  return exported
    ? { size, fontSize, ascent: ascent!, descent: descent! }
    : { size, fontSize, ascent: m.ascent * fontSize, descent: m.descent * fontSize };
}

function scaled(s: Sizes, f: number): Sizes {
  return { size: s.size * f, fontSize: s.fontSize * f, ascent: s.ascent * f, descent: s.descent * f };
}

/**
 * Lays text out the way Blender's text objects do (GAME-UI §5.3; Blender's vfont_to_curve):
 * - characters advance by their own width (no kerning), spaces × wordSpacing, plus Blender's letter
 *   spacing (letterExtra); a line's width leaves out its last character's extra;
 * - lines advance `lineSpacing × size × LINE_ADVANCE`;
 * - with a text box (width > 0) lines wrap at the last space (or hyphen) before the character that
 *   crosses the box width; a word without one is not broken. `align` places lines within the box
 *   (around the origin without one); justify stretches the spaces of wrapped lines and flush spreads
 *   the characters of every line (both only in a box);
 * - `valign` from the origin, or in a box with a height from its frame `box` (Blender's text box y is
 *   one `size` below the frame top): see the switch in `layoutPass`;
 * - `overflow` 'scale' shrinks the text until it fits the box and 'truncate' drops the lines below it,
 *   as Blender does; `fit` (UiText) shrinks until every line fits the box width and the block its height;
 * - `offset` moves everything; `shear` is applied when drawing.
 */
export function layoutText(text: string, data: UiTextData, fit: boolean, family: string, measurer: TextMeasurer): TextLayout {
  const box = data.box && data.box[2] > 0 ? data.box : null;
  const base = sizesOf(data, family, measurer);
  const chars = Array.from(text.replace(/\r\n?/g, '\n'));
  const width = (ch: string) => measurer.width(family, ch);
  const run = (f: number, wrap = true) => layoutPass(chars, data, base, f, box, wrap, width);
  if (box && data.overflow === 'scale') return blenderScaleToFit(run, box, data, base);
  if (!fit || !box) return run(1).layout;
  // UiText.fit: shrink until every line fits the width and (with a height) the block fits the height.
  const noWrap = !(box[3] > 0);
  const fits = (p: Pass) => {
    for (const line of p.layout.lines) if (line.width > box[2] + 0.5) return false;
    if (noWrap) return true;
    const l = p.layout;
    return (l.lines.length - 1) * advanceOf(data, l.size) + l.ascent + l.descent <= box[3] + 0.5;
  };
  const full = run(1, !noWrap);
  if (fits(full)) return full.layout;
  let lo = 0.02;
  let hi = 1;
  let best: Pass | null = null;
  for (let i = 0; i < 12; i++) {
    const mid = (lo + hi) / 2;
    const p = run(mid, !noWrap);
    if (fits(p)) {
      best = p;
      lo = mid;
    } else {
      hi = mid;
    }
  }
  return (best ?? run(lo, !noWrap)).layout;
}

function advanceOf(data: UiTextData, size: number): number {
  return (data.lineSpacing ?? 1) * size * LINE_ADVANCE;
}

/** Blender's overflow 'scale' for one text box (vfont_to_curve's scale-to-fit iterations). */
function blenderScaleToFit(
  run: (f: number, wrap?: boolean) => Pass,
  box: readonly [number, number, number, number],
  data: UiTextData,
  base: Sizes,
): TextLayout {
  const first = run(1);
  if (!(box[3] > 0)) {
    // No height: the longest paragraph (its wrapped pieces together) shrinks to the box width, unwrapped.
    return first.longest > box[2] && first.longest > 0 ? run(box[2] / first.longest, false).layout : first.layout;
  }
  const overflowing = (p: Pass) => p.lastLine !== -1 && p.lineCount > p.lastLine;
  if (!overflowing(first)) return first.layout;
  let max = 1;
  let min = box[3] / Math.max(1e-9, first.lineCount * advanceOf(data, base.size));
  let valid: Pass | null = null;
  for (let i = 0; i < 20 && max - min >= 1e-6; i++) {
    const f = (max + min) / 2;
    const p = run(f);
    if (overflowing(p)) {
      max = f;
      valid = null;
    } else {
      min = f;
      valid = p;
    }
  }
  return (valid ?? run(min)).layout;
}

/** One layout at scale `f`, with what Blender's overflow handling reads from it. */
interface Pass {
  layout: TextLayout;
  /** Lines, and the first line below the box (-1: none). */
  lineCount: number;
  lastLine: number;
  /** The longest paragraph, its wrapped lines added up (px). */
  longest: number;
}

function layoutPass(
  chars: readonly string[],
  data: UiTextData,
  base: Sizes,
  f: number,
  box: readonly [number, number, number, number] | null,
  wordWrap: boolean,
  measure: (ch: string) => number,
): Pass {
  const { size, fontSize, ascent, descent } = f === 1 ? base : scaled(base, f);
  const s = data.letterSpacing ?? 1;
  const wordSpacing = data.wordSpacing ?? 1;
  const ld = advanceOf(data, size);
  const boxW = box ? box[2] : 0;
  // A box with a height: its frame is `box`, Blender's text box y is one (authored) size below its top.
  let boxH = box && box[3] > 0 ? box[3] : 0;
  const n = chars.length;
  const at = (j: number) => (j < n ? chars[j]! : '\0');
  const advance = (ch: string) => measure(ch) * fontSize;
  const xs = new Float64Array(n + 1);
  const lineOf = new Int32Array(n + 1);
  const charNr = new Int32Array(n + 1);
  const isBreak = new Uint8Array(n + 1);
  const isOverflow = new Uint8Array(n + 1);
  const lineWidth: number[] = [];
  const lineChars: number[] = [];
  const lineSpaces: number[] = [];
  let pen = 0;
  let lnr = 0;
  let cnr = 0;
  let wsnr = 0;
  let lastLine = -1;
  let current = 0;
  let longest = 0;
  let i = 0;
  while (i <= n) {
    const ch = at(i);
    const end = ch === '\n' || ch === '\0';
    const tw = end ? 0 : advance(ch);
    if (boxW > 0 && wordWrap && !isBreak[i] && pen + tw > boxW) {
      // Wrap at the last space or hyphen of this line before the overflowing character (that character
      // ends the line), unless the line has none: then the word runs past the box.
      let breakAt = -1;
      for (let j = i; at(j) !== '\n' && !isBreak[j]; j--) {
        if (j === 0) {
          if (i === n) boxH = 0; // Blender: an unbreakable text in a box with a height aligns as if it had none
          break;
        }
        const c = at(j);
        if (c === ' ' || c === '-') {
          breakAt = j;
          break;
        }
      }
      if (breakAt >= 0) {
        cnr -= i - (breakAt - 1);
        wsnr += at(breakAt) === ' ' ? -1 : 1;
        i = breakAt - 1;
        pen = xs[i]!;
        isBreak[breakAt] = 1;
        if (boxH === 0) isOverflow[breakAt] = 1;
        continue;
      }
    }
    if (end || isBreak[i]) {
      xs[i] = pen;
      lineOf[i] = lnr;
      charNr[i] = cnr;
      lineWidth[lnr] = pen - letterExtra(s, tw, size);
      lineChars[lnr] = cnr;
      lineSpaces[lnr] = wsnr;
      if (boxH > 0 && (lnr + 1) * ld > boxH - ld && lastLine === -1) {
        lastLine = lnr + 1;
        isOverflow[i] = 1;
      }
      current += pen;
      if (isBreak[i]) {
        current += tw;
      } else {
        longest = Math.max(longest, current);
        current = 0;
      }
      pen = 0;
      lnr++;
      cnr = 0;
      wsnr = 0;
    } else if (ch === '\t') {
      xs[i] = pen;
      lineOf[i] = lnr;
      charNr[i] = cnr++;
      pen = size > 0 ? 2 * Math.ceil((pen / size + 0.01) / 2) * size : pen;
    } else {
      xs[i] = pen;
      lineOf[i] = lnr;
      charNr[i] = cnr++;
      let wsfac = 1;
      if (ch === ' ') {
        wsfac = wordSpacing;
        wsnr++;
      }
      pen += tw * wsfac + letterExtra(s, tw, size);
    }
    i++;
  }
  const lineCount = lnr;
  // Lines that end in a wrap (their end character is the break, not a newline or the end of the text).
  const wrapEnd = new Uint8Array(lineCount);
  for (let k = 0; k <= n; k++) if (isBreak[k] && at(k) !== '\n') wrapEnd[lineOf[k]!] = 1;

  // Horizontal alignment (Blender shifts every character of a line, its end character too).
  const align = data.align ?? 'left';
  const shift = new Float64Array(lineCount);
  const flush = new Float64Array(lineCount);
  for (let l = 0; l < lineCount; l++) {
    if (align === 'right') shift[l] = boxW - lineWidth[l]!;
    else if (align === 'center') shift[l] = (boxW - lineWidth[l]!) / 2;
    else if (align === 'flush' && boxW > 0) flush[l] = (boxW - lineWidth[l]!) / (lineChars[l]! > 1 ? lineChars[l]! - 1 : 1);
  }
  const extra = new Float64Array(n + 1); // justify and flush, per character
  if (boxW > 0 && align === 'flush') {
    for (let k = 0; k <= n; k++) extra[k] = charNr[k]! * flush[lineOf[k]!]!;
  } else if (boxW > 0 && align === 'justify') {
    // Lines that end in a wrap stretch their spaces (not the space they wrapped at).
    let curofs = 0;
    for (let k = 0; k <= n; k++) {
      const l = lineOf[k]!;
      if (wrapEnd[l] === 1) {
        if (at(k) === ' ' && !isBreak[k] && lineSpaces[l]! > 0) curofs += (boxW - lineWidth[l]!) / lineSpaces[l]!;
        extra[k] = curofs;
      }
      if (at(k) === '\n' || isBreak[k]) curofs = 0;
    }
  }

  // Truncate: nothing from the first overflowing character on.
  let stop = n;
  if (data.overflow === 'truncate') {
    for (let k = 0; k < n; k++) {
      if (isOverflow[k]) {
        stop = k;
        break;
      }
    }
  }
  const truncated = data.overflow === 'truncate' && boxH > 0 && lastLine !== -1;
  const lines = truncated ? Math.min(lineCount, lastLine) : lineCount;

  // Vertical: the first baseline, y down.
  const [ox, oy] = data.offset ?? [0, 0];
  const tbX = box ? box[0] : 0;
  // Blender's text box y: the frame top plus one authored size (a box with a height), else the box y.
  const tbY = box ? box[1] + (box[3] > 0 ? base.size : 0) : 0;
  const block = ascent + descent + (lines - 1) * ld;
  let first: number;
  if (boxH > 0) {
    const frame = tbY - size; // Blender: "the initial Y origin of the text-box is 1 × text size"
    switch (data.valign) {
      case 'top':
        first = frame + ascent;
        break;
      case 'center':
        first = frame + boxH / 2 - block / 2 + ascent;
        break;
      case 'bottom-baseline':
        first = frame + boxH - (lines - 1) * ld;
        break;
      case 'bottom':
        first = frame + boxH - (lines - 1) * ld - descent;
        break;
      default:
        first = tbY;
    }
  } else {
    const all = ascent + descent + (lineCount - 1) * ld;
    switch (data.valign) {
      case 'top':
        first = tbY + ascent;
        break;
      case 'center':
        first = tbY - all / 2 + ascent;
        break;
      case 'bottom-baseline':
        first = tbY - (lineCount - 1) * ld;
        break;
      case 'bottom':
        first = tbY - (lineCount - 1) * ld - descent;
        break;
      default:
        first = tbY;
    }
  }

  // Lines of drawn characters (a newline is not drawn; a wrap's space or hyphen is, at the line end).
  const out: TextLine[] = [];
  const shown = Math.min(lineCount, lines, stop < n ? lineOf[stop]! + 1 : lineCount);
  for (let l = 0; l < shown; l++) {
    const x = ox + tbX + shift[l]!;
    const width = (boxW > 0 && align === 'flush') || (boxW > 0 && align === 'justify' && wrapEnd[l] === 1 && lineSpaces[l]! > 0)
      ? Math.max(lineWidth[l]!, boxW)
      : lineWidth[l]!;
    out.push({ text: '', x, baseline: oy + first + l * ld, width, glyphs: { chars: [], xs: [] } });
  }
  for (let k = 0; k < Math.min(stop, n); k++) {
    const ch = chars[k]!;
    const l = lineOf[k]!;
    // Newlines draw nothing, nor does the space a line wrapped at (a hyphen stays at the line's end).
    if (ch === '\n' || l >= shown || (isBreak[k] && ch === ' ')) continue;
    const line = out[l]!;
    line.glyphs.chars.push(ch);
    line.glyphs.xs.push(xs[k]! + extra[k]!);
    line.text += ch;
  }
  if (out.length === 0) out.push({ text: '', x: ox + tbX, baseline: oy + first, width: 0, glyphs: { chars: [], xs: [] } });
  let minX = Infinity;
  let maxX = -Infinity;
  for (const line of out) {
    if (!line.text.trim()) continue;
    minX = Math.min(minX, line.x);
    maxX = Math.max(maxX, line.x + Math.max(line.width, lastGlyphEnd(line)));
  }
  const bounds: Bounds | null =
    minX <= maxX ? [minX, out[0]!.baseline - ascent, maxX, out[out.length - 1]!.baseline + descent] : null;
  return { layout: { size, fontSize, ascent, descent, lines: out, bounds }, lineCount, lastLine, longest };

  function lastGlyphEnd(line: TextLine): number {
    const { chars: cs, xs: gx } = line.glyphs;
    const last = cs.length - 1;
    return last >= 0 ? gx[last]! + (cs[last] === ' ' ? 0 : advance(cs[last]!)) : 0;
  }
}
/** Linear 0..1 channel to sRGB 0..255. */
function srgb(c: number): number {
  const v = Math.min(1, Math.max(0, c));
  const s = v <= 0.0031308 ? v * 12.92 : 1.055 * Math.pow(v, 1 / 2.4) - 0.055;
  return Math.round(s * 255);
}

/** A CSS colour for a linear RGBA colour (Canvas2D draws sRGB). */
export function cssColor(c: Readonly<UiColor>): string {
  const a = Math.min(1, Math.max(0, c[3] ?? 1));
  return `rgba(${srgb(c[0])},${srgb(c[1])},${srgb(c[2])},${Math.round(a * 1000) / 1000})`;
}

/** What a text raster draws besides the layout. */
export interface TextPaint {
  family: string;
  color: UiColor;
  style: TextStyle;
  shear: number;
}

function hasShadow(style: TextStyle): boolean {
  return (style.shadow[0] !== 0 || style.shadow[1] !== 0 || style.shadowBlur > 0) && (style.shadowColor[3] ?? 1) > 0;
}

/** The local-px rectangle a text raster covers: the layout bounds plus room for overhangs, outline and shadow. */
export function textQuad(layout: TextLayout, paint: TextPaint): Bounds | null {
  const b = layout.bounds;
  if (!b) return null;
  const { style } = paint;
  const outline = Math.max(0, style.outline);
  const padX = 0.2 * layout.size + outline + Math.abs(paint.shear) * Math.max(layout.ascent, layout.descent) + 1;
  const padY = 0.15 * layout.size + outline + 1;
  const q: Bounds = [b[0] - padX, b[1] - padY, b[2] + padX, b[3] + padY];
  if (hasShadow(style)) {
    const blur = Math.max(0, style.shadowBlur) * 1.5;
    q[0] = Math.min(q[0], q[0] + style.shadow[0] - blur);
    q[1] = Math.min(q[1], q[1] + style.shadow[1] - blur);
    q[2] = Math.max(q[2], q[2] + style.shadow[0] + blur);
    q[3] = Math.max(q[3], q[3] + style.shadow[1] + blur);
  }
  return q;
}

/** Largest raster side, px. */
export const MAX_RASTER = 4096;

let scratch: { canvas: HTMLCanvasElement; ctx: Context2D } | null | undefined;

function drawLines(ctx: Context2D, layout: TextLayout, shear: number, stroke: boolean): void {
  for (const line of layout.lines) {
    if (!line.text) continue;
    ctx.save();
    ctx.translate(line.x, line.baseline);
    // Shear slants each line about its own baseline (Blender shears every glyph about its origin).
    if (shear) ctx.transform(1, 0, -shear, 1, 0, 0);
    // One character at a time, where the layout put it: no kerning, no ligatures, as in Blender.
    const { chars, xs } = line.glyphs;
    for (let i = 0; i < chars.length; i++) {
      const ch = chars[i]!;
      if (ch === ' ' || ch === '	') continue;
      if (stroke) ctx.strokeText(ch, xs[i]!, 0);
      else ctx.fillText(ch, xs[i]!, 0);
    }
    ctx.restore();
  }
}

function paintText(ctx: Context2D, layout: TextLayout, paint: TextPaint, outlineColor: string | null, fillColor: string): void {
  // Glyphs at Blender's scale (fontSize); size is only the unit of spacing.
  ctx.font = `${layout.fontSize}px ${paint.family}`;
  noKerning(ctx);
  ctx.textAlign = 'left';
  ctx.textBaseline = 'alphabetic';
  if (outlineColor !== null && paint.style.outline > 0) {
    ctx.lineJoin = 'round';
    ctx.lineWidth = paint.style.outline * 2;
    ctx.strokeStyle = outlineColor;
    drawLines(ctx, layout, paint.shear, true);
  }
  ctx.fillStyle = fillColor;
  drawLines(ctx, layout, paint.shear, false);
}

/**
 * Rasterises a laid-out text into `ctx`'s canvas at `scale` canvas px per local px, covering `quad`
 * (from textQuad). Resizes the canvas; returns its pixel size, or null when it cannot draw.
 */
export function rasterizeText(
  target: { canvas: HTMLCanvasElement; ctx: Context2D },
  layout: TextLayout,
  paint: TextPaint,
  quad: Readonly<Bounds>,
  scale: number,
): { width: number; height: number; scale: number } | null {
  const qw = quad[2] - quad[0];
  const qh = quad[3] - quad[1];
  if (!(qw > 0 && qh > 0 && scale > 0)) return null;
  const s = Math.min(scale, MAX_RASTER / qw, MAX_RASTER / qh);
  const width = Math.max(1, Math.ceil(qw * s));
  const height = Math.max(1, Math.ceil(qh * s));
  const { canvas, ctx } = target;
  if (canvas.width !== width || canvas.height !== height) {
    canvas.width = width;
    canvas.height = height;
  } else {
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.clearRect(0, 0, width, height);
  }
  const { style } = paint;
  if (hasShadow(style)) {
    // One shadow for the whole silhouette (outline + fill), so overlapping glyphs don't darken it twice:
    // draw the silhouette off to the side and let only its canvas shadow land.
    if (scratch === undefined) scratch = create2d();
    if (scratch) {
      const sc = scratch;
      if (sc.canvas.width < width || sc.canvas.height < height) {
        sc.canvas.width = Math.max(sc.canvas.width, width);
        sc.canvas.height = Math.max(sc.canvas.height, height);
      }
      sc.ctx.setTransform(1, 0, 0, 1, 0, 0);
      sc.ctx.clearRect(0, 0, width, height);
      sc.ctx.setTransform(s, 0, 0, s, -quad[0] * s, -quad[1] * s);
      paintText(sc.ctx, layout, paint, '#000', '#000');
      const away = width + 16;
      ctx.save();
      ctx.setTransform(1, 0, 0, 1, 0, 0);
      ctx.shadowColor = cssColor(style.shadowColor);
      ctx.shadowOffsetX = style.shadow[0] * s + away;
      ctx.shadowOffsetY = style.shadow[1] * s;
      ctx.shadowBlur = Math.max(0, style.shadowBlur) * s;
      ctx.drawImage(sc.canvas, 0, 0, width, height, -away, 0, width, height);
      ctx.restore();
    }
  }
  ctx.setTransform(s, 0, 0, s, -quad[0] * s, -quad[1] * s);
  paintText(ctx, layout, paint, cssColor(style.outlineColor), cssColor(paint.color));
  return { width, height, scale: s };
}
