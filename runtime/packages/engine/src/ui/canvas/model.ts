// One loaded canvas as state (docs/GAME-UI.md §2.3, §3, §5.2): the element tree with its components,
// code overrides (text, visibility, bars), button states, bound text and bars, slider and toggle values
// and UiShow rules (§10.3, §10.4), and the layout that follows from them: a screen matrix, visibility
// and rectangle per element. Pure: no WebGL; text is
// measured through the TextMeasurer it is given. The renderer and the mirror read the results.
import { CanvasAnimator, type AnimatedValues } from './animation';
import type { UiCanvasData, UiElementData, UiTransform } from './format';
import {
  affine,
  anchorFactors,
  canvasScale,
  copy,
  emptyBounds,
  fillMatrix,
  fromTransform,
  identity,
  isEmpty,
  multiply,
  rootMatrix,
  scaleAbout,
  stretchScale,
  toRect,
  transformBounds,
  unionBounds,
  type Affine,
  type Bounds,
} from './layout';
import { hasBindings, parseBody, renderBody, type BodyPart } from './bindings';
import { DEFAULT_STYLE, layoutText, type TextLayout, type TextMeasurer, type TextStyle } from './text';
import type { CanvasButton, CanvasRect, CanvasSlider, UiElementInfo } from './types';
import { modelBounds } from './uimodel';
import { compareValue, numberValue, truthy } from '../values';

export type StateShow = 'normal' | 'focused' | 'pressed' | 'disabled' | 'focusedOrPressed';
type ButtonState = 'normal' | 'focused' | 'pressed' | 'disabled';

export interface ButtonValues {
  action: string;
  target: string;
  disabled: boolean;
  order: number;
  focusScale: number;
  sound: string;
  focusSound: string;
}

export interface BarValues {
  value: string;
  max: number;
  fill: string | null;
  direction: string;
  smoothing: number;
}

/** UiShow (GAME-UI §10.3). */
export interface ShowValues {
  value: string;
  compare: string;
  than: number;
}

/** UiSlider (GAME-UI §10.4); `fill` and `handle` are element ids. */
export interface SliderValues {
  value: string;
  min: number;
  max: number;
  step: number;
  fill: string | null;
  handle: string | null;
  direction: string;
  disabled: boolean;
  order: number;
  sound: string;
}

/** UiToggle (GAME-UI §10.4); `on` and `off` are element ids. */
export interface ToggleValues {
  value: string;
  on: string | null;
  off: string | null;
  disabled: boolean;
  order: number;
  sound: string;
}

/** What a model needs from the layer. */
export interface ModelEnv {
  /** A value path (GAME-UI §10.1). */
  value(path: string): unknown;
  /** Authoring problems found while reading a canvas (the layer says each once). */
  warn?(message: string): void;
  measurer(): TextMeasurer;
  /** CSS font-family list for a text's font asset path ('' = fallback). */
  family(font: string): string;
  /** Bumps when fonts load (text re-measures). */
  fontsVersion(): number;
  /** A text body in the current language (GAME-UI §11.3), before its bindings are parsed; absent: as authored. */
  translate?(text: string): string;
  /** Bumps when the language or its table changes: the texts look their bodies up again. */
  localeVersion?(): number;
  /** The editor's preview (GAME-UI §11.2): bindings without a value show as written (`{score}`). */
  placeholders?(): boolean;
}

/** One element and everything derived from it. */
export interface UiNode {
  readonly el: UiElementData;
  readonly index: number;
  readonly parent: UiNode | null;
  readonly children: UiNode[];
  /** A focusable control: a UiButton's values, or a slider's or toggle's (action 'none'). */
  readonly button: ButtonValues | null;
  readonly slider: SliderValues | null;
  readonly toggle: ToggleValues | null;
  /** UiShow: shown only while its value compares as set. */
  readonly showIf: ShowValues | null;
  /** The slider whose fill or handle this is. */
  sliderFill: UiNode | null;
  sliderHandle: UiNode | null;
  /** The toggle whose `on` part (toggleOn) or `off` part this is. */
  toggleOf: UiNode | null;
  toggleOn: boolean;
  /** Its UiShow and toggle-part rules hold (true without either); evaluated every frame. */
  condition: boolean;
  /** Sliders: the value shown (clamped to the range) and its fraction of the range; toggles: on. */
  value: number | boolean;
  fraction: number;
  /** Sliders: the range ends (min, max) in the slider's own frame, then on screen; null without a range. */
  range: [number, number, number, number] | null;
  screenRange: [number, number, number, number] | null;
  /** UiState.show, with the nearest UiButton strictly above it (`owner`). */
  readonly show: StateShow | null;
  owner: UiNode | null;
  readonly bar: BarValues | null;
  /** The UiBar whose fill this is. */
  fillOf: UiNode | null;
  readonly style: TextStyle | null;
  /** The body in the current language, parsed (GAME-UI §11.3); `bound`: it has bindings. */
  parts: BodyPart[] | null;
  bound: boolean;
  // Code overrides.
  visibleOverride: boolean | null;
  textOverride: string | null;
  barOverride: number | null;
  // Bars: target and shown fraction (0..1).
  barTarget: number;
  barShown: number;
  barReady: boolean;
  // Text.
  string: string;
  family: string;
  layout: TextLayout | null;
  /** The string and fonts version `layout` was made for; `layoutVersion` bumps on every new layout. */
  layoutString: string | null;
  layoutFonts: number;
  layoutVersion: number;
  // Layout results.
  readonly local: Affine;
  /** Screen matrix without the focus scale (hit areas, rects). */
  readonly base: Affine;
  /** Screen matrix as drawn (focus scale applied). */
  readonly draw: Affine;
  /** Channels its canvas's open/close/loop animation sets now (GAME-UI §10.5); null: none. */
  anim: AnimatedValues | null;
  /** Screen matrix of the rest layout: `base` without animation (9-slice sizes, text rasters). */
  readonly rest: Affine;
  /** On-screen opacity 0..1: its own (animated, else `el.opacity`) times its parent's. */
  opacity: number;
  /** Own visibility: override or authored, and its UiState rule. */
  selfVisible: boolean;
  /** On screen: itself and every ancestor visible. */
  visible: boolean;
  /** Screen box of its own content (null: none). */
  own: Bounds | null;
  /** Its own box and those of self-visible descendants (empty bounds when nothing). */
  readonly area: Bounds;
  /** Highest draw order within `area` (topmost for hit tests). */
  top: number;
}

function values<T extends object>(components: UiElementData['components'], name: string, defaults: T): T | null {
  const raw = components?.[name];
  if (!raw || typeof raw !== 'object') return null;
  return { ...defaults, ...(raw as Partial<T>) };
}

const BUTTON: ButtonValues = { action: 'event', target: '', disabled: false, order: 0, focusScale: 1.06, sound: '', focusSound: '' };
const BAR: BarValues = { value: '', max: 1, fill: null, direction: 'right', smoothing: 0 };
const SHOW: ShowValues = { value: '', compare: 'atLeast', than: 1 };
const SLIDER: SliderValues = { value: '', min: 0, max: 1, step: 0.1, fill: null, handle: null, direction: 'right', disabled: false, order: 0, sound: '' };
const TOGGLE: ToggleValues = { value: '', on: null, off: null, disabled: false, order: 0, sound: '' };

/** A slider's or toggle's navigation part: a button with no action, its event the element name. */
function controlButton(c: SliderValues | ToggleValues): ButtonValues {
  return { ...BUTTON, action: 'none', disabled: !!c.disabled, order: c.order || 0, sound: c.sound || '' };
}

const finite = (v: unknown, fallback: number): number => (typeof v === 'number' && Number.isFinite(v) ? v : fallback);

/** out = m⁻¹ (identity when m is singular). `out` may alias `m`. */
function invert(out: Affine, m: Readonly<Affine>): Affine {
  const [a, b, c, d, e, f] = m;
  const det = a * d - b * c;
  if (!det) return identity(out);
  out[0] = d / det;
  out[1] = -b / det;
  out[2] = -c / det;
  out[3] = a / det;
  out[4] = (c * f - d * e) / det;
  out[5] = (b * e - a * f) / det;
  return out;
}

const scratchAffine = affine();
const scratchChain = affine();
const scratchInverse = affine();
const scratchBounds: Bounds = [0, 0, 0, 0];
const scratchTransform: UiTransform = { x: 0, y: 0, rotation: 0, scaleX: 1, scaleY: 1 };
const scratchAnimated = affine();
const scratchRest = affine();

/** A loaded canvas: elements, state and layout. */
export class CanvasModel {
  readonly name: string;
  readonly nodes: UiNode[] = [];
  readonly byId = new Map<string, UiNode>();
  readonly byName = new Map<string, UiNode>();
  readonly buttons: UiNode[] = [];
  readonly texts: UiNode[] = [];
  readonly bars: UiNode[] = [];
  /** Sliders and toggles (also in `buttons`). */
  readonly controls: UiNode[] = [];
  /** Elements with a UiShow, and toggles' on/off parts. */
  readonly conditional: UiNode[] = [];
  readonly designWidth: number;
  readonly designHeight: number;
  /** Bumps on every relayout (the renderer re-syncs). */
  version = 0;
  /** Bumps when what the mirror shows may have changed (strings, visibility, structure). */
  contentVersion = 0;
  private width = 0;
  private height = 0;
  private focused: UiNode | null = null;
  private pressed: UiNode | null = null;
  private dirty = true;
  private fonts = -1;
  /** Per node index: drawn with the focus scale (reused between layouts). */
  private readonly scaled: boolean[] = [];
  /** Open, close and loop animations (GAME-UI §10.5): where the canvas is in them. */
  readonly animator: CanvasAnimator;
  /** The canvas scale `s` (§5.2) of the last layout. */
  scale = 1;
  /** Nodes with animated channels now, and each node's channel record (by index, reused). */
  private readonly animatedNodes: UiNode[] = [];
  private readonly records: AnimatedValues[] = [];
  /** The language version the texts' parts are for (GAME-UI §11.3), and every body string parsed so far. */
  private localeVersion = 0;
  private readonly parsed = new Map<string, BodyPart[]>();

  constructor(
    readonly data: UiCanvasData,
    private readonly env: ModelEnv,
  ) {
    this.animator = new CanvasAnimator(data.animations);
    this.name = data.canvas;
    this.localeVersion = env.localeVersion?.() ?? 0;
    const size = Array.isArray(data.size) ? data.size : [0, 0];
    this.designWidth = size[0]! > 0 ? size[0]! : 1920;
    this.designHeight = size[1]! > 0 ? size[1]! : 1080;
    for (const el of data.elements ?? []) {
      const parent = el.parent ? (this.byId.get(el.parent) ?? null) : null;
      const components = el.components ?? {};
      const text = el.type === 'text' && el.text ? el.text : null;
      const parts = text ? this.partsOf(text.body ?? '') : null;
      const show = values(components, 'UiState', { show: 'focused' as StateShow });
      const style = text ? (values(components, 'UiText', DEFAULT_STYLE) ?? DEFAULT_STYLE) : null;
      // A slider or toggle is the element's control: a UiButton beside it is ignored (export warns too).
      const slider = values(components, 'UiSlider', SLIDER);
      const toggle = values(components, 'UiToggle', TOGGLE);
      const control = slider ?? toggle;
      if (control && (components.UiButton || (slider && toggle))) {
        const ignored = [components.UiButton ? 'UiButton' : '', slider && toggle ? 'UiToggle' : ''].filter(Boolean);
        env.warn?.(
          `Game UI: "${data.canvas}/${el.name}" is a ${slider ? 'slider' : 'toggle'}: its ${ignored.join(' and ')} ${ignored.length > 1 ? 'are' : 'is'} ignored`,
        );
      }
      const node: UiNode = {
        el,
        index: this.nodes.length,
        parent,
        children: [],
        button: control ? controlButton(control) : values(components, 'UiButton', BUTTON),
        slider,
        toggle: slider ? null : toggle,
        showIf: values(components, 'UiShow', SHOW),
        sliderFill: null,
        sliderHandle: null,
        toggleOf: null,
        toggleOn: false,
        condition: true,
        value: slider ? finite(slider.min, 0) : false,
        fraction: 0,
        range: null,
        screenRange: null,
        show: show ? show.show : null,
        owner: null,
        bar: values(components, 'UiBar', BAR),
        fillOf: null,
        style,
        parts,
        bound: !!parts && hasBindings(parts),
        visibleOverride: null,
        textOverride: null,
        barOverride: null,
        barTarget: 1,
        barShown: 1,
        barReady: false,
        string: parts && !hasBindings(parts) ? renderBody(parts, () => undefined) : '',
        family: text ? env.family(text.font ?? '') : '',
        layout: null,
        layoutString: null,
        layoutFonts: -1,
        layoutVersion: 0,
        local: affine(),
        base: affine(),
        draw: affine(),
        anim: null,
        rest: affine(),
        opacity: 1,
        selfVisible: el.visible !== false,
        visible: el.visible !== false,
        own: null,
        area: emptyBounds(),
        top: el.order ?? 0,
      };
      if (parent) parent.children.push(node);
      for (let up = parent; up; up = up.parent) {
        if (up.button) {
          node.owner = up;
          break;
        }
      }
      this.nodes.push(node);
      if (el.id) this.byId.set(el.id, node);
      if (el.name && !this.byName.has(el.name)) this.byName.set(el.name, node);
      if (node.button) this.buttons.push(node);
      if (node.slider || node.toggle) this.controls.push(node);
      if (text) this.texts.push(node);
      if (node.bar) this.bars.push(node);
    }
    for (const bar of this.bars) {
      const fill = bar.bar!.fill ? this.byId.get(bar.bar!.fill) : undefined;
      if (fill && fill !== bar) fill.fillOf = bar;
    }
    const part = (id: string | null, of: UiNode): UiNode | null => {
      const node = id ? this.byId.get(id) : undefined;
      return node && node !== of ? node : null;
    };
    for (const node of this.controls) {
      if (node.slider) {
        const fill = part(node.slider.fill, node);
        const handle = part(node.slider.handle, node);
        if (fill) fill.sliderFill = node;
        if (handle && handle !== fill) handle.sliderHandle = node;
      } else {
        const on = part(node.toggle!.on, node);
        const off = part(node.toggle!.off, node);
        if (on) {
          on.toggleOf = node;
          on.toggleOn = true;
        }
        if (off && off !== on) {
          off.toggleOf = node;
          off.toggleOn = false;
        }
      }
    }
    for (const node of this.nodes) if (node.showIf || node.toggleOf) this.conditional.push(node);
  }

  /** Takes over code overrides, bar values and button states from the canvas this one replaces. */
  adopt(old: CanvasModel): void {
    for (const prev of old.nodes) {
      const node = this.byId.get(prev.el.id);
      if (!node) continue;
      node.visibleOverride = prev.visibleOverride;
      node.textOverride = prev.textOverride;
      node.barOverride = prev.barOverride;
      if (prev.barReady && node.bar) {
        node.barShown = prev.barShown;
        node.barReady = true;
      }
    }
    this.focused = old.focused ? (this.byId.get(old.focused.el.id) ?? null) : null;
    this.pressed = old.pressed ? (this.byId.get(old.pressed.el.id) ?? null) : null;
    if (this.focused && !this.focused.button) this.focused = null;
    if (this.pressed && !this.pressed.button) this.pressed = null;
    this.animator.adopt(old.animator);
    this.applyAnimation();
    this.setViewport(old.width, old.height);
  }

  /** Starts the canvas's `open` animation (then its loop), its `close`, or goes back to rest (null). */
  play(kind: 'open' | 'close' | null): void {
    this.animator.play(kind);
    this.applyAnimation();
  }

  /** 0..1: how far it is in (a menu's backdrop fades with its open and close animations). */
  get presence(): number {
    return this.animator.presence;
  }

  /** Samples the animation into the nodes' channels; a layout follows when any were or are animated. */
  private applyAnimation(): void {
    const had = this.animatedNodes.length > 0;
    for (const node of this.animatedNodes) node.anim = null;
    this.animatedNodes.length = 0;
    this.animator.sample((id) => {
      const node = this.byId.get(id);
      if (!node || node.anim) return null;
      node.anim = this.records[node.index] ??= {};
      this.animatedNodes.push(node);
      return node.anim;
    });
    if (had || this.animatedNodes.length > 0) this.dirty = true;
  }

  /** A node's local transform: the authored one with its animated channels over it. */
  private transformOf(node: UiNode): UiTransform | undefined {
    const t = node.el.transform;
    const a = node.anim;
    if (!a) return t;
    const out = scratchTransform;
    out.x = a.x ?? t?.x ?? 0;
    out.y = a.y ?? t?.y ?? 0;
    out.rotation = a.rotation ?? t?.rotation ?? 0;
    out.scaleX = a.scaleX ?? t?.scaleX ?? 1;
    out.scaleY = a.scaleY ?? t?.scaleY ?? 1;
    return out;
  }

  /**
   * After its base matrix: a node's opacity (its own, animated or `el.opacity`, times its parent's) and
   * its rest matrix (`above`: the parent's rest, or the root matrix). Rest drops the animated transform
   * but keeps what layout added to the local (bar and slider fills): rest · animated⁻¹ · local.
   */
  private settle(node: UiNode, above: Readonly<Affine>): void {
    const own = node.anim?.opacity ?? node.el.opacity;
    const opacity = typeof own === 'number' && Number.isFinite(own) ? Math.min(1, Math.max(0, own)) : 1;
    node.opacity = opacity * (node.parent ? node.parent.opacity : 1);
    if (this.animatedNodes.length === 0) {
      copy(node.rest, node.base);
      return;
    }
    if (!node.anim) {
      multiply(node.rest, above, node.local);
      return;
    }
    const animated = fromTransform(scratchAnimated, this.transformOf(node));
    const rest = fromTransform(scratchRest, node.el.transform);
    if (Math.abs(animated[0] * animated[3] - animated[1] * animated[2]) > 1e-12) {
      multiply(rest, rest, multiply(scratchAnimated, invert(scratchAnimated, animated), node.local));
    }
    multiply(node.rest, above, rest);
  }

  setViewport(width: number, height: number): void {
    if (width === this.width && height === this.height) return;
    this.width = width;
    this.height = height;
    this.dirty = true;
  }

  setButtonState(focusedId: string | null, pressedId: string | null): void {
    const f = focusedId ? this.byId.get(focusedId) : undefined;
    const p = pressedId ? this.byId.get(pressedId) : undefined;
    const focused = f?.button ? f : null;
    const pressed = p?.button ? p : null;
    if (focused === this.focused && pressed === this.pressed) return;
    this.focused = focused;
    this.pressed = pressed;
    this.dirty = true;
  }

  get focusedId(): string | null {
    return this.focused?.el.id ?? null;
  }

  get pressedId(): string | null {
    return this.pressed?.el.id ?? null;
  }

  setText(node: UiNode, value: string | null): void {
    if (node.textOverride === value) return;
    node.textOverride = value;
    this.evaluateText(node);
  }

  setVisible(node: UiNode, visible: boolean | null): void {
    if (node.visibleOverride === visible) return;
    node.visibleOverride = visible;
    this.dirty = true;
  }

  setBar(node: UiNode, value: number | null): void {
    node.barOverride = value === null || !Number.isFinite(value) ? null : value;
  }

  /**
   * Re-evaluates bindings and bar targets, then lays out if anything changed. Bars without smoothing
   * (and new ones) jump to their targets; smoothed bars only move in step().
   */
  refresh(): void {
    this.evaluate();
    for (const node of this.bars) {
      if (!node.barReady || !(node.bar!.smoothing > 0)) this.showBar(node, node.barTarget);
    }
    this.ensure();
  }

  /** Per frame: animations and bar smoothing over `dt` seconds (UI time), bindings, layout. */
  step(dt: number): void {
    if (this.animator.phase && dt > 0) {
      this.animator.advance(dt);
      this.applyAnimation();
    }
    this.evaluate();
    for (const node of this.bars) {
      const target = node.barTarget;
      const smoothing = Math.max(0, node.bar!.smoothing || 0);
      if (!node.barReady || smoothing <= 0 || !(dt > 0)) {
        if (!node.barReady || smoothing <= 0) this.showBar(node, target);
        continue;
      }
      // Exponential approach: ~98% of the way after `smoothing` seconds.
      const k = 1 - Math.exp((-4 * dt) / smoothing);
      let next = node.barShown + (target - node.barShown) * k;
      if (Math.abs(target - next) < 1e-4) next = target;
      this.showBar(node, next);
    }
    this.ensure();
  }

  /** Lays out now if anything changed since the last layout. */
  ensure(): void {
    const fonts = this.env.fontsVersion();
    if (fonts !== this.fonts) {
      this.fonts = fonts;
      this.dirty = true;
    }
    if (this.dirty) this.relayout();
  }

  private showBar(node: UiNode, fraction: number): void {
    node.barReady = true;
    if (node.barShown === fraction) return;
    node.barShown = fraction;
    this.dirty = true;
  }

  private evaluate(): void {
    const locale = this.env.localeVersion?.() ?? 0;
    if (locale !== this.localeVersion) this.translateTexts(locale);
    for (const node of this.texts) if (node.bound || node.textOverride !== null) this.evaluateText(node);
    for (const node of this.bars) {
      const bar = node.bar!;
      let value: number | null = node.barOverride;
      if (value === null && bar.value) {
        const v = this.env.value(bar.value);
        const n = typeof v === 'number' ? v : typeof v === 'string' && v.trim() ? Number(v) : NaN;
        value = Number.isFinite(n) ? n : 0;
      }
      // No value and no binding: the fill keeps its authored size.
      const max = bar.max > 0 ? bar.max : 1;
      node.barTarget = value === null ? 1 : Math.min(1, Math.max(0, value / max));
    }
    for (const node of this.controls) this.evaluateControl(node);
    for (const node of this.conditional) {
      const condition = this.conditionOf(node);
      if (condition === node.condition) continue;
      node.condition = condition;
      this.dirty = true;
    }
  }

  /** A slider's value (clamped to its range) and fraction, or a toggle's state, from its value path. */
  private evaluateControl(node: UiNode): void {
    const slider = node.slider;
    if (!slider) {
      const on = truthy(this.env.value(node.toggle!.value));
      if (on !== node.value) this.contentVersion++; // the mirror says checked or not
      node.value = on;
      return;
    }
    const min = finite(slider.min, 0);
    const max = finite(slider.max, 1);
    const value = Math.min(Math.max(min, max), Math.max(Math.min(min, max), numberValue(this.env.value(slider.value))));
    const fraction = max === min ? 0 : (value - min) / (max - min);
    if (value !== node.value) this.contentVersion++; // the mirror's aria-valuenow
    node.value = value;
    if (fraction === node.fraction) return;
    node.fraction = fraction;
    this.dirty = true;
  }

  /** Its UiShow rule holds and, for a toggle's on or off part, the toggle is in that state. */
  private conditionOf(node: UiNode): boolean {
    if (node.toggleOf && node.toggleOn !== node.toggleOf.value) return false;
    const show = node.showIf;
    return !show || compareValue(this.env.value(show.value), show.compare, finite(show.than, 1));
  }

  private evaluateText(node: UiNode): void {
    const next = node.textOverride ?? (node.parts ? renderBody(node.parts, (p) => this.env.value(p), this.env.placeholders?.() === true) : '');
    if (next === node.string) return;
    node.string = next;
    this.dirty = true;
    this.contentVersion++;
  }

  /**
   * A text body in the current language (GAME-UI §11.3): its translation, looked up as authored with
   * the bindings unresolved, else the body itself; parsed once per string.
   */
  private partsOf(body: string): BodyPart[] {
    const text = this.env.translate ? this.env.translate(body) : body;
    let parts = this.parsed.get(text);
    if (!parts) this.parsed.set(text, (parts = parseBody(text)));
    return parts;
  }

  /** The language or its table changed: every text takes its body's translation, and renders again. */
  private translateTexts(locale: number): void {
    this.localeVersion = locale;
    for (const node of this.texts) {
      const parts = this.partsOf(node.el.text?.body ?? '');
      node.parts = parts;
      node.bound = hasBindings(parts);
      this.evaluateText(node);
    }
  }

  private stateOf(button: UiNode): ButtonState {
    if (button.button!.disabled) return 'disabled';
    if (button === this.pressed) return 'pressed';
    if (button === this.focused) return 'focused';
    return 'normal';
  }

  /** Its own visibility, before any UiState rule: the code override, else the authored one. */
  ownVisible(node: UiNode): boolean {
    return node.visibleOverride ?? node.el.visible !== false;
  }

  /** UiState art: a part of a button shown in one state only. */
  isStateArt(node: UiNode): boolean {
    return !!node.show && !!node.owner;
  }

  private stateVisible(node: UiNode): boolean {
    if (!node.show || !node.owner) return true;
    const state = this.stateOf(node.owner);
    if (node.show === 'focusedOrPressed') return state === 'focused' || state === 'pressed';
    return node.show === state;
  }

  /** Local bounds of a node's content and its descendants', in its own frame (before its transform). */
  private contentBounds(node: UiNode, out: Bounds): Bounds {
    emptyBounds(out);
    const own = this.ownBounds(node);
    if (own) unionBounds(out, own);
    const child: Bounds = [0, 0, 0, 0];
    const mapped: Bounds = [0, 0, 0, 0];
    for (const c of node.children) {
      this.contentBounds(c, child);
      if (!isEmpty(child)) unionBounds(out, transformBounds(mapped, fromTransform(affine(), c.el.transform), child));
    }
    return out;
  }

  private ownBounds(node: UiNode): Bounds | null {
    const el = node.el;
    if (el.type === 'mesh' && el.mesh?.bounds) return el.mesh.bounds;
    if (el.type === 'image' && el.image?.rect) {
      const [x, y, w, h] = el.image.rect;
      return [x, y, x + w, y + h];
    }
    if (el.type === 'text') return node.layout?.bounds ?? null;
    // A 3D model (§11.1): `size` centred on its origin. Not a button itself, it is hit only inside one.
    if (el.type === 'model') return modelBounds(el);
    return null;
  }

  private relayout(): void {
    this.dirty = false;
    const Wr = this.designWidth;
    const Hr = this.designHeight;
    const W = this.width > 0 ? this.width : Wr;
    const H = this.height > 0 ? this.height : Hr;
    const s = canvasScale(this.data.settings?.scaleMode ?? 'fit', W, H, Wr, Hr);
    this.scale = s;
    const measurer = this.env.measurer();
    for (const node of this.texts) {
      if (node.layout && node.layoutString === node.string && node.layoutFonts === this.fonts) continue;
      node.layoutString = node.string;
      node.layoutFonts = this.fonts;
      node.layoutVersion++;
      node.layout = layoutText(node.string, node.el.text!, !!node.style?.fit, node.family, measurer);
    }
    for (const node of this.nodes) fromTransform(node.local, this.transformOf(node));
    for (const node of this.nodes) {
      const bar = node.fillOf;
      if (!bar) continue;
      const bounds = this.contentBounds(node, [0, 0, 0, 0]);
      if (isEmpty(bounds)) continue;
      multiply(node.local, node.local, fillMatrix(scratchAffine, bounds, bar.bar!.direction, bar.barShown));
    }
    // Sliders: the range and the handle from the unscaled locals, then the fill scaled like a bar's.
    for (const node of this.controls) if (node.slider) this.placeSlider(node);
    for (const node of this.nodes) {
      const slider = node.sliderFill;
      if (!slider) continue;
      const bounds = this.contentBounds(node, [0, 0, 0, 0]);
      if (isEmpty(bounds)) continue;
      multiply(node.local, node.local, fillMatrix(scratchAffine, bounds, slider.slider!.direction, slider.fraction));
    }
    let changed = false;
    for (const node of this.nodes) {
      const parent = node.parent;
      if (parent) {
        multiply(node.base, parent.base, node.local);
      } else {
        const t = node.el.transform;
        const [ax, ay] = anchorFactors(node.el.anchor, t?.x ?? 0, t?.y ?? 0, Wr, Hr);
        const [sx, sy] = stretchScale(node.el.stretch, s, W, H, Wr, Hr);
        multiply(node.base, rootMatrix(scratchAffine, ax, ay, sx, sy, W, H, Wr, Hr), node.local);
      }
      this.settle(node, parent ? parent.rest : scratchAffine);
      const selfVisible = this.ownVisible(node) && this.stateVisible(node) && node.condition;
      const visible = selfVisible && (parent ? parent.visible : true);
      if (visible !== node.visible) changed = true;
      node.selfVisible = selfVisible;
      node.visible = visible;
      const own = this.ownBounds(node);
      node.own = own ? transformBounds(node.own ?? [0, 0, 0, 0], node.base, own) : null;
    }
    for (let i = this.nodes.length - 1; i >= 0; i--) {
      const node = this.nodes[i]!;
      emptyBounds(node.area);
      node.top = -Infinity;
      if (node.own) {
        unionBounds(node.area, node.own);
        node.top = node.el.order ?? 0;
      }
      for (const c of node.children) {
        if (!c.selfVisible || isEmpty(c.area)) continue;
        unionBounds(node.area, c.area);
        node.top = Math.max(node.top, c.top);
      }
    }
    // Focus: the focused button and its subtree scale about the centre of its area.
    const focused = this.focused?.visible ? this.focused : null;
    const scaled = this.scaled;
    scaled.length = this.nodes.length;
    for (const node of this.nodes) {
      const f = node.button?.focusScale ?? 1;
      if (node === focused && !isEmpty(node.area) && Number.isFinite(f) && f !== 1) {
        const a = node.area;
        multiply(node.draw, scaleAbout(scratchAffine, (a[0] + a[2]) / 2, (a[1] + a[3]) / 2, f, f), node.base);
        scaled[node.index] = true;
      } else if (node.parent && scaled[node.parent.index]) {
        multiply(node.draw, node.parent.draw, node.local);
        scaled[node.index] = true;
      } else {
        copy(node.draw, node.base);
        scaled[node.index] = false;
      }
    }
    for (const node of this.controls) {
      const r = node.range;
      if (!r) {
        node.screenRange = null;
        continue;
      }
      const m = node.base;
      const out = (node.screenRange ??= [0, 0, 0, 0]);
      out[0] = m[0] * r[0] + m[2] * r[1] + m[4];
      out[1] = m[1] * r[0] + m[3] * r[1] + m[5];
      out[2] = m[0] * r[2] + m[2] * r[3] + m[4];
      out[3] = m[1] * r[2] + m[3] * r[3] + m[5];
    }
    if (changed) this.contentVersion++;
    this.version++;
  }

  /**
   * out = the matrix from `node`'s own frame to `ancestor`'s: the locals from `node` up to `ancestor`'s
   * child (identity when `node` is `ancestor`); null when `ancestor` is not above it.
   */
  private toAncestor(out: Affine, node: UiNode, ancestor: UiNode): Affine | null {
    identity(out);
    for (let n: UiNode | null = node; n !== ancestor; n = n.parent) {
      if (!n) return null;
      multiply(out, n.local, out);
    }
    return out;
  }

  /**
   * A slider's range in its own frame: the ends of its fill's own bounds along `direction` (the slider's
   * own content without a fill under it). Its handle moves along the range to the value, keeping its
   * authored offset across it. Runs on the locals before the fills are scaled.
   */
  private placeSlider(node: UiNode): void {
    const slider = node.slider!;
    const fill = slider.fill ? this.byId.get(slider.fill) : undefined;
    const m = scratchChain;
    const bounds = scratchBounds;
    if (fill && fill.sliderFill === node && this.toAncestor(m, fill, node)) {
      this.contentBounds(fill, bounds);
    } else {
      this.contentBounds(node, bounds);
      identity(m);
    }
    if (isEmpty(bounds)) {
      node.range = null;
      return;
    }
    const [x0, y0, x1, y1] = bounds;
    const xc = (x0 + x1) / 2;
    const yc = (y0 + y1) / 2;
    const ends: Bounds =
      slider.direction === 'left' ? [x1, yc, x0, yc] : slider.direction === 'up' ? [xc, y1, xc, y0] : slider.direction === 'down' ? [xc, y0, xc, y1] : [x0, yc, x1, yc];
    const r = (node.range ??= [0, 0, 0, 0]);
    r[0] = m[0] * ends[0] + m[2] * ends[1] + m[4];
    r[1] = m[1] * ends[0] + m[3] * ends[1] + m[5];
    r[2] = m[0] * ends[2] + m[2] * ends[3] + m[4];
    r[3] = m[1] * ends[2] + m[3] * ends[3] + m[5];
    const handle = slider.handle ? this.byId.get(slider.handle) : undefined;
    if (!handle || handle.sliderHandle !== node || !handle.parent) return;
    // The range in the handle's parent frame; the handle's origin goes to the value's point on it.
    if (!this.toAncestor(m, handle.parent, node)) return;
    const inv = invert(scratchInverse, m);
    const ax = inv[0] * r[0] + inv[2] * r[1] + inv[4];
    const ay = inv[1] * r[0] + inv[3] * r[1] + inv[5];
    const dx = inv[0] * r[2] + inv[2] * r[3] + inv[4] - ax;
    const dy = inv[1] * r[2] + inv[3] * r[3] + inv[5] - ay;
    const length2 = dx * dx + dy * dy;
    if (!(length2 > 1e-12)) return;
    const local = handle.local;
    const along = ((local[4] - ax) * dx + (local[5] - ay) * dy) / length2;
    local[4] += (node.fraction - along) * dx;
    local[5] += (node.fraction - along) * dy;
  }

  // Queries (call refresh() first).

  /** The rendered string of a button's first visible, non-empty text, else its name. */
  labelOf(button: UiNode): string {
    const found = firstText(button);
    return found ?? button.el.name;
  }

  buttonInfo(node: UiNode): CanvasButton {
    const b = node.button!;
    return {
      canvas: this.name,
      id: node.el.id,
      name: node.el.name,
      label: this.labelOf(node),
      disabled: !!b.disabled,
      order: b.order || 0,
      action: b.action || 'event',
      target: b.target || '',
      event: (b.action || 'event') === 'event' && b.target ? b.target : node.el.name,
      focusScale: Number.isFinite(b.focusScale) ? b.focusScale : 1,
      sound: b.sound || '',
      focusSound: b.focusSound || '',
      rect: this.rectOf(node),
      kind: node.slider ? 'slider' : node.toggle ? 'toggle' : 'button',
      ...(node.slider && { path: node.slider.value || '', value: node.value, slider: this.sliderInfo(node) }),
      ...(node.toggle && { path: node.toggle.value || '', value: node.value }),
    };
  }

  private sliderInfo(node: UiNode): CanvasSlider {
    const s = node.slider!;
    const r = node.screenRange ?? [node.base[4], node.base[5], node.base[4], node.base[5]];
    const direction = s.direction === 'left' || s.direction === 'up' || s.direction === 'down' ? s.direction : 'right';
    return {
      min: finite(s.min, 0),
      max: finite(s.max, 1),
      step: Math.max(0, finite(s.step, 0)),
      direction,
      from: { x: r[0], y: r[1] },
      to: { x: r[2], y: r[3] },
    };
  }

  visibleButtons(): CanvasButton[] {
    const out: CanvasButton[] = [];
    for (const node of this.buttons) if (node.visible) out.push(this.buttonInfo(node));
    return out;
  }

  /** Topmost visible button whose area contains (x, y). */
  hitTest(x: number, y: number): UiNode | null {
    let best: UiNode | null = null;
    for (const node of this.buttons) {
      if (!node.visible) continue;
      const a = node.area;
      if (isEmpty(a) || x < a[0] || x > a[2] || y < a[1] || y > a[3]) continue;
      if (!best || node.top >= best.top) best = node;
    }
    return best;
  }

  /** Screen rect of a node's area; an element with no content is a zero rect at its origin. */
  rectOf(node: UiNode): CanvasRect {
    if (!isEmpty(node.area)) return toRect(node.area);
    return { x: node.base[4], y: node.base[5], width: 0, height: 0 };
  }

  info(node: UiNode): UiElementInfo {
    return {
      canvas: this.name,
      name: node.el.name,
      id: node.el.id,
      type: node.el.type,
      visible: node.visible,
      text: node.el.type === 'text' ? node.string : null,
      rect: this.rectOf(node),
    };
  }
}

function firstText(node: UiNode): string | null {
  for (const c of node.children) {
    if (!c.visible) continue;
    if (c.el.type === 'text' && c.string) return c.string;
    const found = firstText(c);
    if (found !== null) return found;
  }
  return null;
}
