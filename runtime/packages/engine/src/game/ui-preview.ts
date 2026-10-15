// The editor's game-rendered preview of a Game UI canvas (docs/GAME-UI.md §11.2, SPEC §5.4 and §6.6):
// `view.mode { mode: 'ui', canvas, frame, region?, background? }`. The page shows one canvas as the game
// draws it, laid out at the size of the editor's camera frame and drawn where that frame is: the game's
// canvas element itself moves there, so the level behind it is drawn by the game's camera at the
// frame's aspect, as a game of that size would be. This file holds the frame's maths, the canvas
// element's placement and the checkerboard; Game (game.ts) owns the mode, the Ui (ui.ts) the canvas.
import {
  DataTexture,
  Mesh,
  MeshBasicMaterial,
  NearestFilter,
  OrthographicCamera,
  PlaneGeometry,
  RepeatWrapping,
  RGBAFormat,
  Scene,
  SRGBColorSpace,
  UnsignedByteType,
} from 'three';
import type { GameRenderer } from './renderer';

/** What is drawn under the canvas: the loaded level (dimmed by a menu's backdrop, as in the game), or a checkerboard. */
export type UiPreviewBackground = 'level' | 'checker';

/** A checked `view.mode` 'ui'. */
export interface UiPreview {
  /** The canvas shown. */
  canvas: string;
  /** [x, y, width, height] of the canvas camera's frame, y down, in px of `region` (of the page without one). */
  frame: [number, number, number, number];
  /** The size of the space `frame` is measured in (the editor's region; the stream's page may be smaller), or null. */
  region: [number, number] | null;
  background: UiPreviewBackground;
}

const finite = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v);

/** A preview from `view.mode` fields; null without a canvas name or a frame of 4 finite numbers with a positive size. */
export function parseUiPreview(input: { canvas?: unknown; frame?: unknown; region?: unknown; background?: unknown }): UiPreview | null {
  const { canvas, frame, region } = input;
  if (typeof canvas !== 'string' || !canvas) return null;
  if (!Array.isArray(frame) || frame.length < 4) return null;
  const [x, y, w, h] = frame as unknown[];
  if (!finite(x) || !finite(y) || !finite(w) || !finite(h) || !(w > 0 && h > 0)) return null;
  const size = Array.isArray(region) && finite(region[0]) && finite(region[1]) && region[0] > 0 && region[1] > 0 ? ([region[0], region[1]] as [number, number]) : null;
  return { canvas, frame: [x, y, w, h], region: size, background: input.background === 'checker' ? 'checker' : 'level' };
}

/**
 * Where the game's canvas element goes on the page, in CSS px: laid out at `width` × `height` (the frame's
 * size, in the editor's px, so the canvas's scale mode sees the size the editor shows) and scaled by
 * (`scaleX`, `scaleY`) about its top-left corner at (`left`, `top`), onto the frame mapped from the
 * region onto the page.
 */
export interface UiPlacement {
  left: number;
  top: number;
  width: number;
  height: number;
  scaleX: number;
  scaleY: number;
}

/** The canvas element's placement for a preview on a page of `page` CSS px (null: the page's size is unknown, no scaling). */
export function uiPlacement(preview: UiPreview, page: readonly [number, number] | null): UiPlacement {
  const [x, y, w, h] = preview.frame;
  const region = preview.region;
  const ratio = (a: number | undefined, b: number | undefined): number => (region && page && finite(a) && finite(b) && a > 0 && b > 0 ? a / b : 1);
  const scaleX = ratio(page?.[0], region?.[0]);
  const scaleY = ratio(page?.[1], region?.[1]);
  return { left: x * scaleX, top: y * scaleY, width: Math.max(1, Math.round(w)), height: Math.max(1, Math.round(h)), scaleX, scaleY };
}

/** The inline style properties a placement sets on the canvas (the rest of its style is left alone). */
const PLACED = ['position', 'left', 'top', 'width', 'height', 'margin', 'transformOrigin', 'transform'] as const;

const px = (v: number): string => `${Math.round(v * 100) / 100}px`;
const factor = (v: number): string => String(Math.round(v * 1e5) / 1e5);

/** Moves the game's canvas element to a preview's frame, and puts its own inline style back afterwards. */
export class CanvasPlacement {
  private saved: Record<string, string> | null = null;

  constructor(private readonly canvas: HTMLCanvasElement) {}

  /** The canvas is placed for a preview. */
  get placed(): boolean {
    return this.saved !== null;
  }

  /** Places the canvas; true when its style changed (the caller lays out again). */
  place(p: UiPlacement): boolean {
    const style = this.canvas.style as unknown as Record<string, string>;
    this.saved ??= Object.fromEntries(PLACED.map((key) => [key, style[key] ?? '']));
    const scaled = Math.abs(p.scaleX - 1) > 1e-6 || Math.abs(p.scaleY - 1) > 1e-6;
    const next: Record<(typeof PLACED)[number], string> = {
      position: 'fixed',
      left: px(p.left),
      top: px(p.top),
      width: px(p.width),
      height: px(p.height),
      margin: '0',
      transformOrigin: '0 0',
      transform: scaled ? `scale(${factor(p.scaleX)}, ${factor(p.scaleY)})` : 'none',
    };
    let changed = false;
    for (const key of PLACED) {
      if (style[key] === next[key]) continue;
      style[key] = next[key];
      changed = true;
    }
    return changed;
  }

  /** Puts the canvas's own inline style back; true when it was placed. */
  restore(): boolean {
    const saved = this.saved;
    if (!saved) return false;
    this.saved = null;
    const style = this.canvas.style as unknown as Record<string, string>;
    for (const key of PLACED) style[key] = saved[key] ?? '';
    return true;
  }
}

/** CSS px per square of the checkerboard. */
export const CHECKER_CELL = 16;
/** Its two greys (sRGB bytes): light and dark text both read over it. */
const CHECKER_LIGHT = 0x5a;
const CHECKER_DARK = 0x42;

/** A checkerboard over the whole canvas, under a previewed canvas that has no level to show. */
export class Checkerboard {
  private readonly scene = new Scene();
  private readonly camera = new OrthographicCamera(-1, 1, 1, -1, -1, 1);
  private readonly geometry = new PlaneGeometry(2, 2);
  readonly texture: DataTexture;
  private readonly material: MeshBasicMaterial;

  constructor() {
    const [a, b] = [CHECKER_LIGHT, CHECKER_DARK];
    const data = new Uint8Array([a, a, a, 255, b, b, b, 255, b, b, b, 255, a, a, a, 255]);
    const texture = new DataTexture(data, 2, 2, RGBAFormat, UnsignedByteType);
    texture.colorSpace = SRGBColorSpace;
    texture.magFilter = NearestFilter;
    texture.minFilter = NearestFilter;
    texture.wrapS = texture.wrapT = RepeatWrapping;
    texture.generateMipmaps = false;
    texture.needsUpdate = true;
    this.texture = texture;
    this.material = new MeshBasicMaterial({ map: texture, toneMapped: false, depthTest: false, depthWrite: false });
    const mesh = new Mesh(this.geometry, this.material);
    mesh.frustumCulled = false;
    this.scene.add(mesh);
  }

  /** Draws it over a canvas of `width` × `height` CSS px (squares of CHECKER_CELL px). */
  render(renderer: GameRenderer, width: number, height: number): void {
    this.texture.repeat.set(Math.max(1, width) / (2 * CHECKER_CELL), Math.max(1, height) / (2 * CHECKER_CELL));
    renderer.render(this.scene, this.camera);
  }

  dispose(): void {
    this.geometry.dispose();
    this.material.dispose();
    this.texture.dispose();
  }
}
