// Canvas layout math (docs/GAME-UI.md §5.2): 2D affine matrices in canvas px (y down, rotation
// clockwise positive), the design-size scale, anchors and stretch for top-level elements, and screen
// rectangles as bounding boxes of transformed local bounds. Pure: no DOM, no WebGL.
import type { UiAnchorPoint, UiScaleMode, UiStretch, UiTransform } from './format';
import type { CanvasRect } from './types';

/**
 * A 2D affine matrix [a, b, c, d, e, f]: (x, y) -> (a·x + c·y + e, b·x + d·y + f). Functions write into
 * `out` (which may alias an input) so layout can reuse its arrays.
 */
export type Affine = [number, number, number, number, number, number];
/** [minX, minY, maxX, maxY]. */
export type Bounds = [number, number, number, number];

export function affine(): Affine {
  return [1, 0, 0, 1, 0, 0];
}

export function identity(out: Affine): Affine {
  out[0] = 1;
  out[1] = 0;
  out[2] = 0;
  out[3] = 1;
  out[4] = 0;
  out[5] = 0;
  return out;
}

export function copy(out: Affine, m: Readonly<Affine>): Affine {
  for (let i = 0; i < 6; i++) out[i] = m[i]!;
  return out;
}

/** out = m · n (n applies first). */
export function multiply(out: Affine, m: Readonly<Affine>, n: Readonly<Affine>): Affine {
  const a = m[0] * n[0] + m[2] * n[1];
  const b = m[1] * n[0] + m[3] * n[1];
  const c = m[0] * n[2] + m[2] * n[3];
  const d = m[1] * n[2] + m[3] * n[3];
  const e = m[0] * n[4] + m[2] * n[5] + m[4];
  const f = m[1] * n[4] + m[3] * n[5] + m[5];
  out[0] = a;
  out[1] = b;
  out[2] = c;
  out[3] = d;
  out[4] = e;
  out[5] = f;
  return out;
}

/** An element's local matrix: translate · rotate (clockwise positive, y down) · scale. */
export function fromTransform(out: Affine, t: UiTransform | undefined): Affine {
  if (!t) return identity(out);
  const cos = Math.cos(t.rotation || 0);
  const sin = Math.sin(t.rotation || 0);
  const sx = t.scaleX ?? 1;
  const sy = t.scaleY ?? 1;
  out[0] = cos * sx;
  out[1] = sin * sx;
  out[2] = -sin * sy;
  out[3] = cos * sy;
  out[4] = t.x || 0;
  out[5] = t.y || 0;
  return out;
}

/** Scales by (sx, sy) about the point (cx, cy). */
export function scaleAbout(out: Affine, cx: number, cy: number, sx: number, sy: number): Affine {
  out[0] = sx;
  out[1] = 0;
  out[2] = 0;
  out[3] = sy;
  out[4] = cx - sx * cx;
  out[5] = cy - sy * cy;
  return out;
}

/** The larger of the two axis scales (how big one local px is on screen). */
export function scaleOf(m: Readonly<Affine>): number {
  return Math.max(Math.hypot(m[0], m[1]), Math.hypot(m[2], m[3]));
}

/** Screen bounding box of local `bounds` under `m`. */
export function transformBounds(out: Bounds, m: Readonly<Affine>, bounds: Readonly<Bounds>): Bounds {
  let x0 = Infinity;
  let y0 = Infinity;
  let x1 = -Infinity;
  let y1 = -Infinity;
  for (let i = 0; i < 4; i++) {
    const x = i & 1 ? bounds[2] : bounds[0];
    const y = i & 2 ? bounds[3] : bounds[1];
    const px = m[0] * x + m[2] * y + m[4];
    const py = m[1] * x + m[3] * y + m[5];
    if (px < x0) x0 = px;
    if (px > x1) x1 = px;
    if (py < y0) y0 = py;
    if (py > y1) y1 = py;
  }
  out[0] = x0;
  out[1] = y0;
  out[2] = x1;
  out[3] = y1;
  return out;
}

/** Grows `out` to include `b`. `out` starts empty as [Infinity, Infinity, -Infinity, -Infinity]. */
export function unionBounds(out: Bounds, b: Readonly<Bounds>): Bounds {
  if (b[0] < out[0]) out[0] = b[0];
  if (b[1] < out[1]) out[1] = b[1];
  if (b[2] > out[2]) out[2] = b[2];
  if (b[3] > out[3]) out[3] = b[3];
  return out;
}

export function emptyBounds(out: Bounds = [0, 0, 0, 0]): Bounds {
  out[0] = Infinity;
  out[1] = Infinity;
  out[2] = -Infinity;
  out[3] = -Infinity;
  return out;
}

export function isEmpty(b: Readonly<Bounds>): boolean {
  return !(b[2] >= b[0] && b[3] >= b[1]);
}

export function toRect(b: Readonly<Bounds>): CanvasRect {
  return { x: b[0], y: b[1], width: b[2] - b[0], height: b[3] - b[1] };
}

/** The design-to-screen scale `s` of GAME-UI §5.2. */
export function canvasScale(mode: UiScaleMode, width: number, height: number, designWidth: number, designHeight: number): number {
  switch (mode) {
    case 'width':
      return width / designWidth;
    case 'height':
      return height / designHeight;
    case 'fixed':
      return 1;
    default:
      return Math.min(width / designWidth, height / designHeight);
  }
}

const ANCHORS: Record<UiAnchorPoint, readonly [number, number]> = {
  'top-left': [0, 0],
  top: [0.5, 0],
  'top-right': [1, 0],
  left: [0, 0.5],
  center: [0.5, 0.5],
  right: [1, 0.5],
  'bottom-left': [0, 1],
  bottom: [0.5, 1],
  'bottom-right': [1, 1],
};

/**
 * The anchor factors (ax, ay) ∈ {0, ½, 1}². A missing or unknown anchor resolves as `auto` does in
 * export: thirds of the design size from the element's position.
 */
export function anchorFactors(
  anchor: string | undefined,
  x: number,
  y: number,
  designWidth: number,
  designHeight: number,
): readonly [number, number] {
  const known = anchor ? ANCHORS[anchor as UiAnchorPoint] : undefined;
  if (known) return known;
  const third = (v: number, size: number) => (v < size / 3 ? 0 : v > (2 * size) / 3 ? 1 : 0.5);
  return [third(x, designWidth), third(y, designHeight)];
}

/** (sx, sy) for a top-level element's stretch, given the canvas scale `s`. */
export function stretchScale(
  stretch: UiStretch | string | undefined,
  s: number,
  width: number,
  height: number,
  designWidth: number,
  designHeight: number,
): [number, number] {
  const wx = width / designWidth;
  const hy = height / designHeight;
  switch (stretch) {
    case 'width':
      return [wx, s];
    case 'height':
      return [s, hy];
    case 'both':
      return [wx, hy];
    case 'cover': {
      const c = Math.max(wx, hy);
      return [c, c];
    }
    default:
      return [s, s];
  }
}

/**
 * Maps design px to screen px for a top-level element at design position (x, y): the anchor point keeps
 * its place on the screen and offsets from it scale by (sx, sy). Compose with the element's local matrix.
 */
export function rootMatrix(
  out: Affine,
  ax: number,
  ay: number,
  sx: number,
  sy: number,
  width: number,
  height: number,
  designWidth: number,
  designHeight: number,
): Affine {
  out[0] = sx;
  out[1] = 0;
  out[2] = 0;
  out[3] = sy;
  out[4] = ax * width - ax * designWidth * sx;
  out[5] = ay * height - ay * designHeight * sy;
  return out;
}

export type FillDirection = 'right' | 'left' | 'up' | 'down';

/**
 * A bar fill's extra local scale: `fraction` along `direction`, from the starting edge of the fill's own
 * local `bounds` (y down, so `up` grows from the bottom edge).
 */
export function fillMatrix(out: Affine, bounds: Readonly<Bounds>, direction: FillDirection | string, fraction: number): Affine {
  switch (direction) {
    case 'left':
      return scaleAbout(out, bounds[2], 0, fraction, 1);
    case 'up':
      return scaleAbout(out, 0, bounds[3], 1, fraction);
    case 'down':
      return scaleAbout(out, 0, bounds[1], 1, fraction);
    default:
      return scaleAbout(out, bounds[0], 0, fraction, 1);
  }
}
