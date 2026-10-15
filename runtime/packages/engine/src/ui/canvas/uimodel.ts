// A model element's UiModel values (docs/GAME-UI.md §11.1), checked and with defaults filled in, and the
// local bounds layout gives it. Pure, so the layout model needs nothing of the 3D code (./models3d).
import type { UiColor, UiElementData } from './format';
import type { Bounds } from './layout';

export type UiModelView = 'threeQuarter' | 'front' | 'side' | 'top';
export type UiModelLight = 'studio' | 'soft' | 'flat';

/** UiModel (GAME-UI §11.1), checked, defaults filled in. */
export interface UiModelSettings {
  /** The view in design px [w, h], centred on the element's origin: its local bounds. */
  size: [number, number];
  view: UiModelView;
  /** Multiplies the framing: 2 is twice as close. */
  zoom: number;
  /** Radians per second about the model's up axis (0: still). */
  spin: number;
  /** The clip to loop ('' = none). */
  clip: string;
  light: UiModelLight;
  /** Linear RGBA behind the model (alpha 0: the UI shows through). */
  background: UiColor;
}

/** The UiModel defaults (schema/builtins.ts). */
export const MODEL_DEFAULTS: Readonly<UiModelSettings> = {
  size: [320, 320],
  view: 'threeQuarter',
  zoom: 1,
  spin: 0.6,
  clip: '',
  light: 'studio',
  background: [0, 0, 0, 0],
};

const VIEWS: readonly UiModelView[] = ['threeQuarter', 'front', 'side', 'top'];
const LIGHTS: readonly UiModelLight[] = ['studio', 'soft', 'flat'];
const num = (v: unknown, fallback: number): number => (typeof v === 'number' && Number.isFinite(v) ? v : fallback);

/** Per element data, which never changes once loaded (a live edit brings new data). */
const cache = new WeakMap<UiElementData, { settings: UiModelSettings; bounds: Bounds }>();

function entry(el: UiElementData): { settings: UiModelSettings; bounds: Bounds } {
  let found = cache.get(el);
  if (found) return found;
  const raw = (el.components?.UiModel ?? {}) as Record<string, unknown>;
  const size = Array.isArray(raw.size) ? raw.size : MODEL_DEFAULTS.size;
  const bg = Array.isArray(raw.background) ? raw.background : MODEL_DEFAULTS.background;
  const zoom = num(raw.zoom, 1);
  const settings: UiModelSettings = {
    size: [Math.max(0, num(size[0], MODEL_DEFAULTS.size[0])), Math.max(0, num(size[1], MODEL_DEFAULTS.size[1]))],
    view: VIEWS.includes(raw.view as UiModelView) ? (raw.view as UiModelView) : MODEL_DEFAULTS.view,
    zoom: zoom > 0 ? zoom : 1,
    spin: num(raw.spin, MODEL_DEFAULTS.spin),
    clip: typeof raw.clip === 'string' ? raw.clip : '',
    light: LIGHTS.includes(raw.light as UiModelLight) ? (raw.light as UiModelLight) : MODEL_DEFAULTS.light,
    background: [num(bg[0], 0), num(bg[1], 0), num(bg[2], 0), Math.min(1, Math.max(0, num(bg[3], 0)))],
  };
  const [w, h] = settings.size;
  found = { settings, bounds: [-w / 2, -h / 2, w / 2, h / 2] };
  cache.set(el, found);
  return found;
}

/** An element's UiModel values. */
export function modelSettings(el: UiElementData): UiModelSettings {
  return entry(el).settings;
}

/** A model element's local bounds: `size` centred on its origin (layout, rects and button areas). */
export function modelBounds(el: UiElementData): Bounds {
  return entry(el).bounds;
}
