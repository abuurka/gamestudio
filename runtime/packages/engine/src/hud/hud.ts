// A tiny DOM text overlay positioned over the game canvas: corner text, text pinned
// to entities or world points, timed messages and bars.

import { Vector3, type Camera } from 'three';
import type { Entity } from '../ecs/entity';
import { isVec3Input, toVector3, type Vec3Input } from '../math';

export type HudPosition =
  | 'top-left'
  | 'top-center'
  | 'top-right'
  | 'center'
  | 'bottom-left'
  | 'bottom-center'
  | 'bottom-right';

export interface HudOptions {
  /** Default 'top-left'. Items in the same position stack vertically. Ignored with `at`. */
  position?: HudPosition;
  /** Default 'normal'. */
  size?: 'small' | 'normal' | 'large';
  /** Any CSS color, e.g. '#ffd400' or 'tomato'. Default white. */
  color?: string;
  /** Pin the item above an entity (it follows it, and goes away with it) or at a world point (`[x, y, z]` or `{ x, y, z }`). */
  at?: Entity | Vec3Input;
  /** World-space offset added to `at`, e.g. [0, 2, 0] above a character. */
  offset?: Vec3Input;
  /** Remove the item after this many seconds (on the game's clock: wall time, or game time under the test clock). */
  duration?: number;
}

export interface HudBarOptions extends HudOptions {
  /** The value of a full bar. Default 1. */
  max?: number;
  /** Text in front of the bar. */
  label?: string;
  /** Bar width in CSS pixels. Default 160. */
  width?: number;
}

export interface HudAPI {
  /** Shows `text` under `key`, replacing any previous item for that key. */
  set(key: string, text: string, options?: HudOptions): void;
  /** Shows a bar filled to `value / max` (health, progress...) under `key`. */
  bar(key: string, value: number, options?: HudBarOptions): void;
  /** Removes one entry, or every entry when `key` is omitted. */
  clear(key?: string): void;
}

// Centred regions sit at left:50%, which caps their shrink-to-fit width at half the canvas;
// max-content lets text use the whole width before it wraps.
const FIT = 'width:max-content;max-width:calc(100% - 32px)';
const POSITIONS: Record<HudPosition, string> = {
  'top-left': 'top:12px;left:16px;align-items:flex-start',
  'top-center': `top:12px;left:50%;transform:translateX(-50%);align-items:center;text-align:center;${FIT}`,
  'top-right': 'top:12px;right:16px;align-items:flex-end',
  center: `top:50%;left:50%;transform:translate(-50%,-50%);align-items:center;text-align:center;${FIT}`,
  'bottom-left': 'bottom:12px;left:16px;align-items:flex-start',
  'bottom-center': `bottom:12px;left:50%;transform:translateX(-50%);align-items:center;text-align:center;${FIT}`,
  'bottom-right': 'bottom:12px;right:16px;align-items:flex-end',
};
// 'large' shrinks on narrow pages so a headline still fits (40 px from 445 px wide).
const SIZES = { small: '13px', normal: '18px', large: 'min(40px, 9vw)' } as const;

interface Item {
  element: HTMLDivElement;
  at: Entity | Vector3 | null;
  offset: Vector3 | null;
  /** Deadline on the HUD clock (ms), or Infinity. */
  expires: number;
}

const tmp = new Vector3();

/**
 * Projects a world point to CSS pixels over a `width` x `height` canvas.
 * Returns null when the point is behind the camera or outside the depth range.
 */
export function projectToScreen(
  point: Vector3,
  camera: Camera,
  width: number,
  height: number,
): { x: number; y: number } | null {
  camera.updateMatrixWorld();
  const ndc = tmp.copy(point).project(camera);
  if (!Number.isFinite(ndc.x) || ndc.z < -1 || ndc.z > 1) return null;
  return { x: ((ndc.x + 1) / 2) * width, y: ((1 - ndc.y) / 2) * height };
}

export class Hud implements HudAPI {
  readonly element: HTMLDivElement;
  private readonly regions = new Map<HudPosition, HTMLDivElement>();
  private readonly items = new Map<string, Item>();
  private readonly point = new Vector3();

  /** `clock` returns milliseconds; `duration` counts on it. The Game passes its own clock. */
  constructor(
    private readonly canvas: HTMLCanvasElement,
    private readonly clock: () => number = () => performance.now(),
  ) {
    this.element = document.createElement('div');
    this.element.className = 'gs-hud';
    this.element.style.cssText =
      'position:fixed;pointer-events:none;z-index:10;color:#fff;overflow:hidden;' +
      'font:600 18px/1.35 system-ui,-apple-system,"Segoe UI",sans-serif;text-shadow:0 1px 3px rgba(0,0,0,.8)';
    document.body.appendChild(this.element);
    this.layout();
  }

  set(key: string, text: string, options: HudOptions = {}): void {
    const item = this.item(key, options, 'text');
    if (item.element.textContent !== text) item.element.textContent = text;
  }

  bar(key: string, value: number, options: HudBarOptions = {}): void {
    const item = this.item(key, options, 'bar');
    const max = options.max ?? 1;
    const fraction = max > 0 && Number.isFinite(value) ? Math.min(1, Math.max(0, value / max)) : 0;
    const element = item.element;
    let label = element.firstElementChild as HTMLSpanElement | null;
    if (!label) {
      label = document.createElement('span');
      const track = document.createElement('span');
      track.style.cssText =
        'display:inline-block;vertical-align:middle;height:10px;border:1px solid rgba(255,255,255,.85);' +
        'border-radius:3px;background:rgba(0,0,0,.35);box-shadow:0 1px 3px rgba(0,0,0,.6);overflow:hidden';
      const fill = document.createElement('span');
      fill.style.cssText = 'display:block;height:100%;background:currentColor';
      track.appendChild(fill);
      element.append(label, track);
    }
    const track = label.nextElementSibling as HTMLSpanElement;
    const fill = track.firstElementChild as HTMLSpanElement;
    const text = options.label ? `${options.label} ` : '';
    if (label.textContent !== text) label.textContent = text;
    track.style.width = `${options.width ?? 160}px`;
    fill.style.width = `${(fraction * 100).toFixed(2)}%`;
    element.dataset.value = String(fraction);
  }

  clear(key?: string): void {
    if (key === undefined) {
      for (const item of this.items.values()) item.element.remove();
      this.items.clear();
      return;
    }
    this.items.get(key)?.element.remove();
    this.items.delete(key);
  }

  /** Removes expired items and labels of destroyed entities. Called every simulated frame. */
  expire(now = this.clock()): void {
    for (const [key, item] of this.items) {
      const gone = item.at !== null && !(item.at instanceof Vector3) && !item.at.alive;
      if (now >= item.expires || gone) this.clear(key);
    }
  }

  /** Expires items and moves pinned ones to their entity or point. Called every rendered frame. */
  update(camera: Camera, now = this.clock()): void {
    this.expire(now);
    if (this.items.size === 0) return;
    const width = this.element.clientWidth || this.canvas.clientWidth;
    const height = this.element.clientHeight || this.canvas.clientHeight;
    for (const item of this.items.values()) {
      const at = item.at;
      if (!at) continue;
      if (at instanceof Vector3) this.point.copy(at);
      else at.object3d.getWorldPosition(this.point);
      if (item.offset) this.point.add(item.offset);
      const screen = projectToScreen(this.point, camera, width, height);
      item.element.style.display = screen ? '' : 'none';
      if (screen) item.element.style.transform = `translate(${screen.x.toFixed(1)}px,${screen.y.toFixed(1)}px) translate(-50%,-100%)`;
    }
  }

  /** Keeps the overlay aligned with the canvas. Called on resize. */
  layout(): void {
    const r = this.canvas.getBoundingClientRect();
    Object.assign(this.element.style, {
      left: `${r.left}px`,
      top: `${r.top}px`,
      width: `${r.width}px`,
      height: `${r.height}px`,
    });
  }

  dispose(): void {
    this.element.remove();
  }

  private item(key: string, options: HudOptions, kind: 'text' | 'bar'): Item {
    let item = this.items.get(key);
    if (item && item.element.dataset.kind !== kind) {
      this.clear(key);
      item = undefined;
    }
    if (!item) {
      const element = document.createElement('div');
      element.dataset.key = key;
      element.dataset.kind = kind;
      if (kind === 'text') element.style.whiteSpace = 'pre-line'; // '\n' starts a new line.
      item = { element, at: null, offset: null, expires: Infinity };
      this.items.set(key, item);
    }
    const element = item.element;
    const at = options.at;
    item.at = at === undefined || at === null ? null : isVec3Input(at) ? toVector3(at) : (at as Entity);
    item.offset = options.offset ? toVector3(options.offset) : null;
    item.expires = options.duration !== undefined ? this.clock() + Math.max(0, options.duration) * 1000 : Infinity;
    if (item.at) {
      if (element.parentElement !== this.element) this.element.appendChild(element);
      element.style.position = 'absolute';
      element.style.left = '0';
      element.style.top = '0';
      element.style.whiteSpace = kind === 'text' ? 'pre' : 'nowrap';
      element.style.textAlign = 'center';
    } else {
      const region = this.region(options.position ?? 'top-left');
      if (element.parentElement !== region) region.appendChild(element);
      element.style.position = '';
      element.style.transform = '';
      element.style.display = '';
      element.style.whiteSpace = kind === 'text' ? 'pre-line' : '';
      element.style.textAlign = '';
    }
    element.style.fontSize = SIZES[options.size ?? 'normal'];
    element.style.color = options.color ?? '';
    return item;
  }

  private region(position: HudPosition): HTMLDivElement {
    let region = this.regions.get(position);
    if (!region) {
      region = document.createElement('div');
      region.style.cssText = `position:absolute;display:flex;flex-direction:column;gap:2px;${POSITIONS[position]}`;
      this.element.appendChild(region);
      this.regions.set(position, region);
    }
    return region;
  }
}
