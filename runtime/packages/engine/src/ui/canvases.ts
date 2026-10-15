// Blender-authored canvases (docs/GAME-UI.md §5): which are open (one menu, any number of HUDs), the
// focused, hovered and pressed buttons, keyboard and gamepad navigation, what the mouse is over, and
// the values sliders take from keys and drags (§10.4; the controller writes them).
// The Ui (ui.ts) owns the modal menu, the pause and key consumption; the canvas layer (src/ui/canvas,
// a separate chunk) lays out, queries and draws. Navigation is pure functions of the laid-out buttons.

import type { UiCanvasData, UiCanvasSettings } from './canvas/format';
import type { CanvasButton, CanvasLayer, CanvasRect, CanvasSlider } from './canvas/types';

/** A navigation key: a direction, or Tab / Shift+Tab. */
export type NavStep = 'up' | 'down' | 'left' | 'right' | 'next' | 'prev';

/** A button of an open canvas, by element id. */
export interface CanvasClick {
  canvas: string;
  id: string;
}

/** A new value for a slider or toggle of an open canvas (the controller writes it). */
export interface ControlChange {
  canvas: string;
  button: CanvasButton;
  value: number | boolean;
}

// --- Navigation (GAME-UI §5.6) ------------------------------------------------------------

const centre = (b: CanvasButton): { x: number; y: number } => ({ x: b.rect.x + b.rect.width / 2, y: b.rect.y + b.rect.height / 2 });

/** True when the canvas navigates by `UiButton.order` (any button has an order above 0). */
export function usesOrder(buttons: readonly CanvasButton[]): boolean {
  return buttons.some((b) => b.order > 0);
}

/** By `order` (1, 2, 3, ...); buttons without one come after, in element order. */
function byOrder(buttons: readonly CanvasButton[]): CanvasButton[] {
  const rank = (b: CanvasButton): number => (b.order > 0 ? b.order : Infinity);
  return buttons.map((b, i) => ({ b, i })).sort((p, q) => rank(p.b) - rank(q.b) || p.i - q.i).map(({ b }) => b);
}

/** Reading order: top to bottom, then left to right (centres within half a button's height share a row). */
export function readingOrder(buttons: readonly CanvasButton[]): CanvasButton[] {
  return buttons
    .map((b, i) => ({ b, i, c: centre(b) }))
    .sort((p, q) => {
      const row = Math.min(p.b.rect.height, q.b.rect.height) / 2;
      return Math.abs(p.c.y - q.c.y) > row ? p.c.y - q.c.y : p.c.x - q.c.x || p.i - q.i;
    })
    .map(({ b }) => b);
}

/** Keyboard order: by `order` when the canvas uses it, else reading order. */
export function navigationOrder(buttons: readonly CanvasButton[]): CanvasButton[] {
  return usesOrder(buttons) ? byOrder(buttons) : readingOrder(buttons);
}

/** The button selected when a menu opens: the authored `focus` when it is enabled, else the first enabled in navigation order. */
export function defaultFocus(buttons: readonly CanvasButton[], authored: string | null): CanvasButton | null {
  const enabled = buttons.filter((b) => !b.disabled);
  const authoredButton = enabled.find((b) => b.id === authored);
  if (authoredButton) return authoredButton;
  return (usesOrder(buttons) ? byOrder(enabled) : readingOrder(enabled))[0] ?? null;
}

/** The next enabled button after `fromId` in `list`, going `step` (±1) and wrapping; null when there is none. */
function stepIn(list: readonly CanvasButton[], fromId: string, step: number): CanvasButton | null {
  const n = list.length;
  const at = list.findIndex((b) => b.id === fromId);
  for (let k = 1; k <= n; k++) {
    const b = list[(((at + k * step) % n) + n) % n]!;
    if (!b.disabled) return b;
  }
  return null;
}

const DIRECTIONS = { up: [0, -1], down: [0, 1], left: [-1, 0], right: [1, 0] } as const;
/** cos 60°: a button counts as "that way" within 60° of the pressed direction. */
const CONE = 0.5;
/** Rectangles overlapping by more than this (px) across the pressed direction share a row or column. */
const BEAM = 0.5;

/** How far two rectangles overlap across a direction (vertically for left/right, horizontally for up/down); <= 0 when apart. */
function overlapAcross(a: CanvasRect, b: CanvasRect, horizontal: boolean): number {
  return horizontal
    ? Math.min(a.y + a.height, b.y + b.height) - Math.max(a.y, b.y)
    : Math.min(a.x + a.width, b.x + b.width) - Math.max(a.x, b.x);
}

interface Candidate {
  b: CanvasButton;
  along: number;
  off: number;
}

/**
 * `c` beats `best`: lower along the axis (ahead: nearer; behind, wrapping: farther), and within half a
 * pixel of that, nearer across it.
 */
const beats = (c: Candidate, best: Candidate | null): boolean =>
  !best || c.along < best.along - 0.5 || (Math.abs(c.along - best.along) <= 0.5 && c.off < best.off);

/**
 * Spatial navigation. Up and Down go by rows (rowFocus). Left and Right go as browsers do: from the
 * focused button, the buttons "in its beam" win: those whose rectangle overlaps its own vertically (the
 * same row), the nearest along the axis first. With none of those that way, the nearest enabled button
 * whose centre lies within 60° of `direction`, with the off-axis distance counted twice. With none, it
 * wraps to the farthest button the other way: in the beam when there is one, else along that axis (the
 * nearest on the other axis among equals); with none of those either, null.
 */
export function spatialFocus(buttons: readonly CanvasButton[], from: CanvasButton, direction: keyof typeof DIRECTIONS): CanvasButton | null {
  if (direction === 'up' || direction === 'down') return rowFocus(buttons, from, direction === 'down' ? 1 : -1);
  const [dx, dy] = DIRECTIONS[direction];
  const o = centre(from);
  let beam: Candidate | null = null;
  let best: CanvasButton | null = null;
  let bestScore = Infinity;
  let beamWrap: Candidate | null = null;
  let wrap: Candidate | null = null;
  for (const b of buttons) {
    if (b.disabled || b.id === from.id) continue;
    const c = centre(b);
    const vx = c.x - o.x;
    const vy = c.y - o.y;
    const candidate = { b, along: vx * dx + vy * dy, off: Math.abs(vx * dy - vy * dx) };
    const inBeam = overlapAcross(from.rect, b.rect, true) > BEAM; // the same row
    if (candidate.along > 0) {
      if (inBeam && beats(candidate, beam)) beam = candidate;
      if (candidate.along >= Math.hypot(vx, vy) * CONE - 1e-9) {
        const score = Math.hypot(candidate.along, 2 * candidate.off);
        if (score < bestScore) {
          best = b;
          bestScore = score;
        }
      }
    } else if (candidate.along < -0.5) {
      if (inBeam && beats(candidate, beamWrap)) beamWrap = candidate;
      if (beats(candidate, wrap)) wrap = candidate;
    }
  }
  return beam?.b ?? best ?? beamWrap?.b ?? wrap?.b ?? null;
}

/**
 * Up (`dy` −1) and Down (1): the nearest row that way, then the button in it that overlaps the focused
 * one most horizontally, else (none overlaps, or a tie within half a pixel) the nearest centre across.
 * A button is that way when its centre is farther than half the smaller of the two heights (closer, it
 * shares the focused one's row, as in readingOrder); the row is every such button whose centre is within
 * half the focused button's height of the nearest one's. With none that way, it wraps to the farthest row
 * the other way, chosen in the same way; with none there either, null. Ties go to the first in element order.
 */
function rowFocus(buttons: readonly CanvasButton[], from: CanvasButton, dy: 1 | -1): CanvasButton | null {
  const o = centre(from);
  const ahead: Candidate[] = [];
  const behind: Candidate[] = [];
  for (const b of buttons) {
    if (b.disabled || b.id === from.id) continue;
    const c = centre(b);
    const candidate = { b, along: (c.y - o.y) * dy, off: Math.abs(c.x - o.x) };
    const sameRow = Math.min(from.rect.height, b.rect.height) / 2;
    if (candidate.along > sameRow) ahead.push(candidate);
    else if (candidate.along < -sameRow) behind.push(candidate);
  }
  // Ahead, the lowest `along` is the nearest row; behind (wrapping), the farthest.
  const pool = ahead.length > 0 ? ahead : behind;
  if (pool.length === 0) return null;
  const edge = Math.min(...pool.map((c) => c.along));
  const row = pool.filter((c) => c.along <= edge + from.rect.height / 2);
  let best: Candidate | null = null;
  let bestOverlap = 0;
  for (const c of row) {
    const overlap = Math.max(0, overlapAcross(from.rect, c.b.rect, false));
    const level = Math.abs(overlap - bestOverlap) <= BEAM;
    if (!best || (!level && overlap > bestOverlap) || (level && c.off < best.off)) {
      best = c;
      bestOverlap = overlap;
    }
  }
  return best?.b ?? null;
}

/**
 * Where a navigation key moves the focus in a canvas's visible buttons: by `order` (Up/Left/Shift+Tab
 * back, Down/Right/Tab on, wrapping) when the canvas uses it, else by position, with Tab and Shift+Tab
 * in reading order. Null when nothing is enabled; the first in navigation order when nothing is focused.
 */
export function navigate(buttons: readonly CanvasButton[], fromId: string | null, step: NavStep): CanvasButton | null {
  const from = buttons.find((b) => b.id === fromId);
  if (!from) return defaultFocus(buttons, null);
  const back = step === 'up' || step === 'left' || step === 'prev';
  if (usesOrder(buttons)) return stepIn(byOrder(buttons), from.id, back ? -1 : 1);
  if (step === 'next' || step === 'prev') return stepIn(readingOrder(buttons), from.id, back ? -1 : 1);
  return spatialFocus(buttons, from, step);
}

// --- Sliders (GAME-UI §10.4) ---------------------------------------------------------------

/** A slider's value snapped to its step from `min` (step 0: as is) and clamped to its range. */
export function snapSlider(slider: CanvasSlider, value: number): number {
  const { min, max, step } = slider;
  let v = step > 0 ? min + Math.round((value - min) / step) * step : value;
  v = Math.min(Math.max(min, max), Math.max(Math.min(min, max), v));
  return Math.round(v * 1e9) / 1e9; // 0.1 · 3 is 0.3, not 0.30000000000000004
}

/**
 * The value a key gives a focused slider: the arrow keys along its axis (Left/Right for `right` and
 * `left`, Up/Down for `up` and `down`) move it by `step` (a tenth of the range when `step` is 0); the
 * key pointing along `direction` moves it toward `max`. Null for other keys: they move the focus.
 */
export function sliderKey(button: CanvasButton, nav: NavStep): number | null {
  const slider = button.slider;
  if (!slider || typeof button.value !== 'number') return null;
  const horizontal = slider.direction === 'right' || slider.direction === 'left';
  if (horizontal ? nav !== 'left' && nav !== 'right' : nav !== 'up' && nav !== 'down') return null;
  const sign = nav === slider.direction ? 1 : -1;
  const step = slider.step > 0 ? slider.step : Math.abs(slider.max - slider.min) / 10;
  return snapSlider(slider, button.value + sign * Math.sign(slider.max - slider.min || 1) * step);
}

/** The value of a slider at a pointer (canvas px): its projection on the range, snapped to `step`. */
export function sliderAt(button: CanvasButton, x: number, y: number): number | null {
  const slider = button.slider;
  if (!slider) return null;
  const dx = slider.to.x - slider.from.x;
  const dy = slider.to.y - slider.from.y;
  const length2 = dx * dx + dy * dy;
  if (!(length2 > 1e-9)) return typeof button.value === 'number' ? button.value : slider.min;
  const t = Math.min(1, Math.max(0, ((x - slider.from.x) * dx + (y - slider.from.y) * dy) / length2));
  return snapSlider(slider, slider.min + t * (slider.max - slider.min));
}

// --- Open canvases and button states ------------------------------------------------------

/** A close counts as ended this close to its end (float steps). */
const CLOSE_EPSILON = 1e-6;

/** Seconds a canvas's open or close animation lasts (GAME-UI §10.5); 0 without one. */
function animationSeconds(data: UiCanvasData | null, kind: 'open' | 'close'): number {
  const d = data?.animations?.[kind]?.duration;
  return typeof d === 'number' && Number.isFinite(d) && d > 0 ? d : 0;
}

interface Pressed extends CanvasClick {
  /** A key press (shown for one frame) rather than a held mouse button. */
  key: boolean;
  /** The frame it shows in has begun. */
  shown: boolean;
}

/** A slider held with the mouse: where the pointer is now (canvas px). */
interface Drag extends CanvasClick {
  x: number;
  y: number;
  /** The pointer moved (or pressed, or released) since the value was last taken. */
  pending: boolean;
  /** Released: the value is taken once more, then the drag ends. */
  done: boolean;
}

/** The open canvases of the game and their buttons' focus, hover and pressed states. */
export class CanvasScreens {
  private menuName: string | null = null;
  private readonly huds = new Set<string>();
  private focusId: string | null = null;
  private hovered: CanvasClick | null = null;
  private pressed: Pressed | null = null;
  private drag: Drag | null = null;
  private pushedOpen: string | null = null;
  private readonly pushedButtons = new Map<string, string>();
  /** Canvases whose fonts and images are ready (or that waited long enough): they can open. */
  private readonly ready = new Set<string>();
  /** Something changed since the layer last updated. */
  private stale = true;
  /**
   * Closed canvases playing their close animation (GAME-UI §10.5), with the seconds left: still drawn and
   * listed in state().ui.open, but closed to input and to isOpen(). In the order they began closing.
   */
  private readonly closing = new Map<string, number>();
  /** What the layer drew at the last push (open and closing): only those animate closed. */
  private drawn = new Set<string>();
  /** The open canvases at the last push: one opened since plays its open animation from the next push. */
  private pushedOpenNames = new Set<string>();
  /** The editor's preview (GAME-UI §11.2): this canvas alone is drawn, whatever is open. */
  private previewName: string | null = null;

  constructor(
    readonly layer: CanvasLayer,
    /** Plays a canvas's asset sound ('' = none). */
    private readonly sound: (canvas: string, path: string) => void,
  ) {}

  /** The open menu canvas, or null. */
  get menu(): string | null {
    return this.menuName;
  }

  has(name: string): boolean {
    return this.layer.has(name);
  }

  names(): string[] {
    return this.layer.names();
  }

  /** Loaded canvases that can open (their fonts and images are ready), in load order. */
  readyNames(): string[] {
    return this.layer.names().filter((n) => this.ready.has(n));
  }

  isReady(name: string): boolean {
    return this.ready.has(name) && this.layer.has(name);
  }

  markReady(name: string): void {
    if (this.layer.has(name)) this.ready.add(name);
  }

  /**
   * The editor's preview (GAME-UI §11.2): only `name` is drawn, as it opens in the game (a menu over its
   * backdrop with its first button focused, a HUD as it is) and at rest: its open animation is skipped,
   * a loop runs. Nothing opens, closes or takes input for it. Null: what is open is drawn again.
   */
  preview(name: string | null): void {
    const old = this.previewName;
    if (name === old) return;
    this.previewName = name;
    if (old !== null) {
      this.layer.setButtonState(old, null, null);
      this.pushedButtons.delete(old);
    }
    this.pushedOpen = null;
    this.stale = true;
    this.push(); // at once: a view that draws no canvases (the Scene view) pushes nothing more
  }

  /** A canvas was removed. */
  removed(name: string): void {
    if (name === this.previewName) this.pushedOpen = null; // shown again if it comes back
    this.ready.delete(name);
    this.hideHud(name);
    if (this.menuName === name) this.hideMenu();
    this.closing.delete(name);
  }

  settings(name: string): UiCanvasSettings | null {
    return this.layer.data(name)?.settings ?? null;
  }

  /** The pause menu canvas: the first menu with `pauseMenu`, or null. */
  pauseMenu(): string | null {
    return this.names().find((n) => this.settings(n)?.kind === 'menu' && this.settings(n)?.pauseMenu) ?? null;
  }

  /** Open HUDs, bottom to top: by `layer`, then in load order. */
  openHuds(): string[] {
    return this.byLayer([...this.huds]);
  }

  /** Loaded canvases among `names`, bottom to top: by `layer`, then in load order. */
  private byLayer(names: string[]): string[] {
    const order = this.names();
    const layer = (n: string): number => this.settings(n)?.layer ?? 0;
    return names.filter((n) => this.layer.has(n)).sort((a, b) => layer(a) - layer(b) || order.indexOf(a) - order.indexOf(b));
  }

  /** Open canvases, bottom to top: the HUDs, then the menu. */
  openNames(): string[] {
    const huds = this.openHuds();
    return this.menuName ? [...huds, this.menuName] : huds;
  }

  /**
   * What is drawn, bottom to top (state().ui.open): open HUDs and HUDs playing their close animation, by
   * layer; then menus playing their close animation; then the open menu.
   */
  drawnNames(): string[] {
    if (this.closing.size === 0) return this.openNames();
    const closing = [...this.closing.keys()].filter((n) => this.layer.has(n));
    const isHud = (n: string): boolean => this.settings(n)?.kind === 'hud';
    const huds = this.byLayer([...this.huds, ...closing.filter(isHud)]);
    const menus = closing.filter((n) => !isHud(n));
    return [...huds, ...menus, ...(this.menuName ? [this.menuName] : [])];
  }

  /**
   * Drawn canvases playing their open or close animation, not a loop (state().ui.animating), in drawing
   * order. A canvas opened since the last push counts when it has an open animation: it plays from the
   * next frame. A closing one counts until its close ends, when it stops being drawn.
   */
  animatingNames(): string[] {
    return this.drawnNames().filter((name) => {
      if (this.closing.has(name)) return true;
      if (!this.pushedOpenNames.has(name)) return animationSeconds(this.layer.data(name), 'open') > 0;
      return this.layer.animationPhase(name) === 'open';
    });
  }

  isOpen(name: string): boolean {
    return this.menuName === name || this.huds.has(name);
  }

  /** A menu is drawn: open, or playing its close animation. */
  menuDrawn(): boolean {
    if (this.menuName) return true;
    for (const name of this.closing.keys()) if (this.settings(name)?.kind !== 'hud') return true;
    return false;
  }

  showMenu(name: string): void {
    if (this.menuName && this.menuName !== name) {
      this.startClose(this.menuName);
      this.forget(this.menuName);
    }
    this.closing.delete(name); // opened while closing: it opens again from its open animation's start
    this.menuName = name;
    this.focusId = null;
    this.pressed = null;
    this.validate();
    this.stale = true;
  }

  hideMenu(): void {
    if (this.menuName) {
      this.startClose(this.menuName);
      this.forget(this.menuName);
    }
    this.menuName = null;
    this.focusId = null;
    this.stale = true;
  }

  showHud(name: string): void {
    this.closing.delete(name);
    this.huds.add(name);
    this.stale = true;
  }

  hideHud(name: string): void {
    if (this.huds.delete(name)) {
      this.startClose(name);
      this.forget(name);
    }
    this.stale = true;
  }

  /** Closes everything at once, without close animations (restart, Stop, test reset). */
  hideAll(): void {
    this.hideMenu();
    for (const name of [...this.huds]) this.hideHud(name);
    for (const name of this.closing.keys()) this.layer.setButtonState(name, null, null);
    this.closing.clear();
    this.hovered = null;
    this.pressed = null;
    this.drag = null;
    this.push(); // the layer drops them now: what opens next plays its open animation from the start
  }

  /** A canvas was replaced (live edit): its button states are pushed again. */
  replaced(name: string): void {
    this.pushedButtons.delete(name);
    this.pushedOpen = null;
    this.stale = true;
  }

  /** The focused button of the menu, or null. */
  focusedButton(): CanvasButton | null {
    this.validate();
    const menu = this.menuName;
    return menu && this.focusId ? (this.layer.buttons(menu).find((b) => b.id === this.focusId) ?? null) : null;
  }

  /** Keeps the menu's focus on an enabled, visible button: the one it has, else the default. */
  validate(): void {
    const menu = this.menuName;
    if (!menu) return;
    const buttons = this.layer.buttons(menu);
    if (buttons.some((b) => b.id === this.focusId && !b.disabled)) return;
    const next = defaultFocus(buttons, this.settings(menu)?.focus ?? null)?.id ?? null;
    if (next !== this.focusId) {
      this.focusId = next;
      this.stale = true;
    }
  }

  /** Moves the menu's focus. True when it moved. */
  move(step: NavStep): boolean {
    const menu = this.menuName;
    if (!menu) return false;
    this.validate();
    const target = navigate(this.layer.buttons(menu), this.focusId, step);
    if (!target || target.id === this.focusId) return false;
    this.focus(menu, target);
    return true;
  }

  /**
   * A navigation key on the focused slider: the arrows along its axis change its value (GAME-UI §10.4),
   * even when it stays the same at an end. Null when they don't apply: the key moves the focus.
   */
  adjust(step: NavStep): ControlChange | null {
    const button = this.focusedButton();
    const value = this.menuName && button && !button.disabled ? sliderKey(button, step) : null;
    return value === null ? null : { canvas: this.menuName!, button: button!, value };
  }

  /** The value a held slider has from the pointer since it was last taken, or null. Once per frame. */
  takeDrag(): ControlChange | null {
    const drag = this.drag;
    if (!drag) return null;
    if (drag.done) this.drag = null;
    if (!drag.pending) return null;
    drag.pending = false;
    const button = this.button(drag.canvas, drag.id);
    const value = button ? sliderAt(button, drag.x, drag.y) : null;
    if (value === null) {
      this.drag = null;
      return null;
    }
    return { canvas: drag.canvas, button: button!, value };
  }

  /** A value changed: the layer evaluates the canvases again before it next draws. */
  changed(): void {
    this.stale = true;
  }

  /** A key pressed the focused button or toggle: shown pressed for one frame. Null when nothing is focused, or for a slider. */
  pressFocused(): CanvasClick | null {
    const button = this.focusedButton();
    if (!this.menuName || !button || button.disabled || button.kind === 'slider') return null;
    this.pressed = { canvas: this.menuName, id: button.id, key: true, shown: false };
    this.stale = true;
    return { canvas: this.menuName, id: button.id };
  }

  /**
   * A mouse event at canvas px. Hover focuses a menu's button (or highlights a HUD's); a left press
   * shows it pressed; a release over the button the press began on clicks it. A press on a slider
   * holds it instead: its value follows the pointer until the release (takeDrag). `consume`: the press
   * landed on a button (a HUD's press is the UI's, not gameplay's).
   */
  pointer(kind: 'down' | 'up' | 'move', x: number, y: number, button: number): { consume: boolean; click: CanvasClick | null } {
    const drag = this.drag && !this.drag.done ? this.drag : null;
    if (drag && (kind === 'move' || (kind === 'up' && button === 0))) {
      // A held slider follows the pointer wherever it goes, until the release (takeDrag reads it).
      Object.assign(drag, { x, y, pending: true, done: kind === 'up' });
      if (kind === 'up') {
        this.pressed = null;
        this.stale = true;
      }
      return { consume: false, click: null };
    }
    const hit = this.hit(x, y);
    const usable = hit && !hit.button.disabled ? hit : null;
    if (kind === 'move') {
      if (this.menuName) {
        if (usable && usable.button.id !== this.focusId) this.focus(usable.canvas, usable.button);
      } else {
        const next = usable ? { canvas: usable.canvas, id: usable.button.id } : null;
        if (next?.canvas !== this.hovered?.canvas || next?.id !== this.hovered?.id) {
          this.hovered = next;
          this.stale = true;
        }
      }
      return { consume: false, click: null };
    }
    if (button !== 0) return { consume: false, click: null };
    if (kind === 'down') {
      this.pressed = usable ? { canvas: usable.canvas, id: usable.button.id, key: false, shown: false } : null;
      if (usable && this.menuName && usable.button.id !== this.focusId) this.focus(usable.canvas, usable.button);
      // A slider takes the value under the pointer at once, and follows it while held.
      this.drag = usable?.button.kind === 'slider' ? { canvas: usable.canvas, id: usable.button.id, x, y, pending: true, done: false } : null;
      this.stale = true;
      return { consume: hit !== null, click: null };
    }
    const pressed = this.pressed;
    if (!pressed || pressed.key) return { consume: false, click: null };
    this.pressed = null;
    this.stale = true;
    const same = usable && usable.canvas === pressed.canvas && usable.button.id === pressed.id;
    return { consume: false, click: same ? { canvas: pressed.canvas, id: pressed.id } : null };
  }

  /** An enabled, visible button of an open canvas, or null. */
  button(canvas: string, id: string): CanvasButton | null {
    if (!this.isOpen(canvas)) return null;
    const button = this.layer.buttons(canvas).find((b) => b.id === id);
    return button && !button.disabled ? button : null;
  }

  /**
   * What `state().ui.buttons` lists: the menu's buttons, or the open HUDs' with no menu, each canvas's
   * in navigation order (`UiButton.order`, else reading order), the order players see them in.
   */
  listedButtons(): CanvasButton[] {
    if (this.menuName) return navigationOrder(this.layer.buttons(this.menuName));
    return this.openHuds().flatMap((name) => navigationOrder(this.layer.buttons(name)));
  }

  /** Sends what is open and the buttons' states to the layer, when they changed. */
  push(): void {
    if (this.previewName !== null) {
      this.pushPreview(this.previewName);
      return;
    }
    // Closing canvases are drawn too: a closing HUD in its place among the HUDs, closing menus under the menu.
    const closing = this.closing.size > 0 ? [...this.closing.keys()].filter((n) => this.layer.has(n)) : [];
    const closingHuds = closing.filter((n) => this.settings(n)?.kind === 'hud');
    const huds = closingHuds.length > 0 ? this.byLayer([...this.huds, ...closingHuds]) : this.openHuds();
    const open = `${huds.join('\n')}\n|${this.menuName ?? ''}\n|${closing.join('\n')}`;
    if (open !== this.pushedOpen) {
      this.layer.setOpen(huds, this.menuName, closing);
      this.pushedOpen = open;
      this.drawn = new Set([...huds, ...closing, ...(this.menuName ? [this.menuName] : [])]);
      this.pushedOpenNames = new Set(this.openNames());
    }
    for (const name of this.openNames()) {
      const focused = name === this.menuName ? this.focusId : this.hovered?.canvas === name ? this.hovered.id : null;
      const pressed = this.pressed?.canvas === name ? this.pressed.id : null;
      const state = `${focused ?? ''}|${pressed ?? ''}`;
      if (this.pushedButtons.get(name) === state) continue;
      this.layer.setButtonState(name, focused, pressed);
      this.pushedButtons.set(name, state);
    }
  }

  /** The preview canvas alone to the layer (a HUD, or a menu over its backdrop), its menu focus as on opening. */
  private pushPreview(name: string): void {
    const settings = this.settings(name);
    const hud = settings?.kind === 'hud';
    const open = `\u0000preview\n${name}\n${hud}`; // again once it loads and turns out to be a HUD
    if (open !== this.pushedOpen) {
      this.layer.setOpen(hud ? [name] : [], hud ? null : name);
      this.pushedOpen = open;
      this.drawn = new Set([name]);
      this.pushedOpenNames = new Set([name]);
    }
    // At rest: an open animation the preview started (it was just shown, or its canvas just came) is
    // skipped to its end before it is drawn; a loop goes on in UI time.
    if (this.layer.animationPhase(name) === 'open') this.layer.update(animationSeconds(this.layer.data(name), 'open'));
    const focused = settings && !hud ? (defaultFocus(this.layer.buttons(name), settings.focus)?.id ?? null) : null;
    const state = `${focused ?? ''}|`;
    if (this.pushedButtons.get(name) === state) return;
    this.layer.setButtonState(name, focused, null);
    this.pushedButtons.set(name, state);
  }

  /** Once per frame, after the simulation: states to the layer, then its update (bindings, bars, text, mirror). */
  frame(dt: number): void {
    if (this.previewName !== null) {
      this.push();
      this.layer.update(dt);
      this.stale = false;
      return;
    }
    // A key press shows pressed for one frame.
    if (this.pressed?.key && this.pressed.shown) this.pressed = null;
    this.validate();
    this.push();
    this.layer.update(dt);
    if (this.advanceCloses(dt)) this.push(); // a close ended: the layer stops drawing it now
    this.stale = false;
    if (this.pressed?.key) this.pressed.shown = true;
  }

  /**
   * A canvas closes: one the layer drew, with a close animation, goes on being drawn (and listed) until
   * the animation ends; the layer plays it from the next push.
   */
  private startClose(name: string): void {
    const duration = animationSeconds(this.layer.data(name), 'close');
    if (duration > 0 && this.drawn.has(name)) this.closing.set(name, duration);
  }

  /** UI time passes for the close animations (the same `dt` the layer steps them by). True when one ended. */
  private advanceCloses(dt: number): boolean {
    if (!(dt > 0)) return false;
    let ended = false;
    for (const [name, left] of this.closing) {
      if (left - dt > CLOSE_EPSILON) {
        this.closing.set(name, left - dt);
        continue;
      }
      this.closing.delete(name);
      // It kept its focus shown while it faded out.
      if (!this.isOpen(name)) this.layer.setButtonState(name, null, null);
      ended = true;
    }
    return ended;
  }

  /** Draws the open canvases into the current frame (`raw`: the three.js renderer). */
  render(raw: unknown): void {
    this.push();
    if (this.stale) {
      this.layer.update(0); // a change since the last frame (a test renders between steps)
      this.stale = false;
    }
    this.layer.render(raw);
  }

  // --- Internals ----------------------------------------------------------------------

  private focus(canvas: string, button: CanvasButton): void {
    this.focusId = button.id;
    this.stale = true;
    this.sound(canvas, button.focusSound);
  }

  /** The button under (x, y): the menu's, or with no menu the topmost HUD's. */
  private hit(x: number, y: number): { canvas: string; button: CanvasButton } | null {
    if (this.menuName) {
      const button = this.layer.hitTest(this.menuName, x, y);
      return button ? { canvas: this.menuName, button } : null;
    }
    for (const name of this.openHuds().reverse()) {
      const button = this.layer.hitTest(name, x, y);
      if (button) return { canvas: name, button };
    }
    return null;
  }

  /**
   * A canvas closed: its buttons show no state (a closing one keeps its focus shown until its close
   * animation ends), and the next push sends its state again.
   */
  private forget(name: string): void {
    const pushed = this.pushedButtons.get(name);
    if (pushed !== undefined) {
      const focused = this.closing.has(name) ? pushed.slice(0, pushed.indexOf('|')) || null : null;
      this.layer.setButtonState(name, focused, null);
    }
    this.pushedButtons.delete(name);
    if (this.hovered?.canvas === name) this.hovered = null;
    if (this.pressed?.canvas === name) this.pressed = null;
    if (this.drag?.canvas === name) this.drag = null;
  }
}
