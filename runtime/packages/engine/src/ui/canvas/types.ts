// The canvas layer's API (docs/GAME-UI.md §5): what the UI controller (src/ui) calls to load, lay out,
// query and draw Blender-authored canvases. The implementation lives behind createCanvasLayer() in
// ./index, a separate chunk that is imported dynamically only when the game has canvases.
import type { UiCanvasData, UiElementData, UiElementType } from './format';

/** What the layer needs from the game. */
export interface CanvasHost {
  /** The game canvas: its client size is the layout size, and its client rect maps client px. */
  canvas: HTMLCanvasElement;
  /**
   * Resolves an asset of a canvas to a URL the page can load. `path` is the project-relative asset path
   * (a key of `data.assets`); `fileUrl` is the canvas file's URL, or undefined for live (`ui.canvas`) data.
   */
  assetUrl(data: UiCanvasData, path: string, fileUrl?: string): string;
  /**
   * A value path (GAME-UI §10.1), for bindings, bars, UiShow, sliders and toggles: 'score' -> ctx.store.score,
   * 'save.best' -> ctx.save.get('best'), 'audio.music' -> the music channel's volume, 'game.time' -> ctx.time.
   */
  value(path: string): unknown;
  warn(message: string): void;
  /**
   * Translations (GAME-UI §11.3): a text body as authored (bindings included) in the current language,
   * looked up before its bindings are parsed; the body itself without a translation. Absent: none.
   */
  translate?(text: string): string;
  /** Bumps when the language or its table changes: every text looks its body up again. */
  localeVersion?(): number;
  /** The editor's preview (GAME-UI §11.2): bindings without a value show as written (`{score}`), not as ''. */
  placeholders?(): boolean;
}

/** A rectangle in canvas CSS px (origin top-left, y down). */
export interface CanvasRect {
  x: number;
  y: number;
  width: number;
  height: number;
}

/** A button of a canvas, or a slider or toggle (they navigate as buttons do), as laid out now. */
export interface CanvasButton {
  canvas: string;
  /** Element id (gs_id). */
  id: string;
  /** Element (object) name. */
  name: string;
  /** The rendered text of its first visible text descendant, else the element name. */
  label: string;
  disabled: boolean;
  order: number;
  action: string;
  target: string;
  /** The ctx.ui.on event name: `target` for an `event` button with a target, else the element name. */
  event: string;
  focusScale: number;
  /** Asset paths ('' = none); resolve with CanvasHost.assetUrl. */
  sound: string;
  focusSound: string;
  /**
   * Screen rect in canvas CSS px: the union of the element and its visible descendants, laid out now,
   * without the focus scale (so focus does not move hit areas or navigation).
   */
  rect: CanvasRect;
  /**
   * A focusable control (GAME-UI §10.4): 'button' (also when absent) for a UiButton, else a UiSlider or
   * a UiToggle. Controls navigate like buttons; their action is 'none' and their event the element name.
   */
  kind?: CanvasControlKind;
  /** Sliders and toggles: the value path they show and write ('' = none). */
  path?: string;
  /** Sliders: the value shown, clamped to the range. Toggles: on (the path's value is truthy). */
  value?: number | boolean;
  /** Sliders: the range and where it lies on the screen. */
  slider?: CanvasSlider;
}

export type CanvasControlKind = 'button' | 'slider' | 'toggle';

/** A slider's range (UiSlider) as laid out now. */
export interface CanvasSlider {
  min: number;
  max: number;
  /** What a key press adds and drags snap to; 0: smooth. */
  step: number;
  direction: 'right' | 'left' | 'up' | 'down';
  /**
   * Canvas CSS px where the value is `min` and where it is `max`: the ends of the fill's own bounds, along
   * `direction`, without the focus scale (like `rect`).
   */
  from: { x: number; y: number };
  to: { x: number; y: number };
}

/** Snapshot of an element (GAME-UI §5.5; ctx.ui.element). */
export interface UiElementInfo {
  canvas: string;
  name: string;
  id: string;
  type: UiElementType;
  /** Visible on screen now: its own visibility and every ancestor's (authored, overrides, UiState). */
  visible: boolean;
  /** The rendered string for text elements, else null. */
  text: string | null;
  /** Screen rectangle in canvas CSS px, as laid out now (groups: union of visible descendants). */
  rect: CanvasRect;
}

/** An element found by path. */
export interface CanvasElementRef {
  canvas: string;
  element: UiElementData;
}

/**
 * Loaded canvases, their layout and their drawing. Pure state + three.js; no input, pause or navigation
 * (the controller owns those). Methods taking a canvas name ignore unknown names (queries return empty
 * results); methods taking an element id ignore unknown ids.
 */
export interface CanvasLayer {
  /**
   * Adds a canvas, or replaces one with the same `data.canvas` name in place (open state, overrides and
   * button states are kept for ids that still exist). Resolves when its fonts and images are ready, or
   * after 1 s (fallback fonts). Layout and queries work immediately.
   */
  set(data: UiCanvasData, fileUrl?: string): Promise<void>;
  /** Forgets a canvas (closing it) and disposes its GPU resources. */
  remove(name: string): void;
  has(name: string): boolean;
  /** Loaded canvas names, in load order. */
  names(): string[];
  data(name: string): UiCanvasData | null;
  /**
   * What is drawn, bottom to top: the HUD canvases (in the given order; the caller sorts by layer) then the
   * menu with its backdrop. Unknown names are skipped. `closing`: canvases playing their close animation
   * (GAME-UI §10.5), drawn until the caller drops them: a closing HUD keeps its place in `huds`, closing
   * menus draw under the menu, each over its backdrop. A canvas that becomes open plays its `open`
   * animation from the start (then `loop`), one that starts closing plays `close`.
   */
  setOpen(huds: readonly string[], menu: string | null, closing?: readonly string[]): void;
  /** Lays out for a canvas of width × height CSS px. Queries also re-layout lazily when host.canvas's size changed. */
  layout(width: number, height: number): void;
  /** Per rendered frame: evaluates bindings, smooths bars, re-rasterises changed text, refreshes the mirror. */
  update(dt: number): void;
  /**
   * The animation a canvas is playing now (GAME-UI §10.5), as of the last setOpen and update: 'open',
   * 'loop' or 'close' (a close holds its end until the caller drops the canvas); null at rest, when it
   * is not drawn, or for an unknown name.
   */
  animationPhase(name: string): 'open' | 'loop' | 'close' | null;
  /** The visible buttons, sliders and toggles of a canvas, in document (element) order. */
  buttons(name: string): CanvasButton[];
  /** Visual state of a canvas's buttons (focus scale, UiState children). Ids not in the canvas count as none. */
  setButtonState(name: string, focusedId: string | null, pressedId: string | null): void;
  /** 'Name' or 'Canvas/Name'. 'Name' searches the `prefer` canvases first (in order), then all loaded ones. */
  find(path: string, prefer: readonly string[]): CanvasElementRef | null;
  /** Snapshot of an element; throws for an unknown canvas or id (use find() first). */
  info(canvas: string, id: string): UiElementInfo;
  /** Overrides a text element's body (no bindings); null restores the authored body and its bindings. */
  setText(canvas: string, id: string, value: string | null): void;
  /** Overrides an element's visibility; null restores the authored visibility. */
  setVisible(canvas: string, id: string, visible: boolean | null): void;
  /** Sets a UiBar's value in its own units (shown as value / max); null goes back to its binding. */
  setBar(canvas: string, id: string, value: number | null): void;
  /** 'Canvas/Name' -> rendered string, for every visible text element of the given canvases. */
  texts(names: readonly string[]): Record<string, string>;
  /**
   * 'Canvas/Name' of every element of the given canvases that is hidden itself, sorted: by a code
   * override (setVisible false), or authored hidden without one showing it. Not elements hidden only by
   * a hidden ancestor, not UiState art (a button's state parts), and not elements hidden only by their
   * UiShow or as a toggle's on/off part.
   */
  hiddenElements(names: readonly string[]): string[];
  /** The topmost visible button of a canvas under (x, y) canvas CSS px, or null. Disabled buttons are returned too. */
  hitTest(name: string, x: number, y: number): CanvasButton | null;
  /**
   * Draws the open canvases into the current framebuffer of `raw` (a three.js WebGLRenderer or
   * WebGPURenderer): autoClear off, clearDepth(), render, autoClear restored. No-op when `raw` is falsy,
   * when hidden, or when nothing is open.
   */
  render(raw: unknown): void;
  /** Hides the whole layer (drawing and mirror), e.g. in the Scene view. */
  setHidden(hidden: boolean): void;
  /** The accessibility mirror root (GAME-UI §5.4), appended to document.body. */
  readonly mirror: HTMLElement;
  /** Disposes every GPU resource and removes the mirror. */
  dispose(): void;
}

export type CreateCanvasLayer = (host: CanvasHost) => CanvasLayer;
