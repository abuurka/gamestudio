// The canvas layer (docs/GAME-UI.md §5): Blender-authored menus and HUDs, loaded, laid out, queried
// and drawn. This folder is its own chunk: the UI controller imports this file dynamically, only when
// the game's ui/index.json lists canvases, and talks to it through the CanvasLayer API in ./types.
import { FontRegistry, fontAxesOf, ImageRegistry } from './assets';
import type { UiCanvasData } from './format';
import { Mirror } from './mirror';
import { CanvasModel, type ModelEnv, type UiNode } from './model';
import { UiModels } from './models3d';
import { CanvasRenderer, CanvasView, loadUiShaders } from './renderer';
import { canvasMeasurer, fallbackMeasurer, type TextMeasurer } from './text';
import type { CanvasButton, CanvasElementRef, CanvasHost, CanvasLayer, UiElementInfo } from './types';

export type { CanvasButton, CanvasElementRef, CanvasHost, CanvasLayer, CanvasRect, UiElementInfo } from './types';

/** How long set() waits for fonts and images before resolving anyway (fallback fonts). */
const ASSET_WAIT_MS = 1000;

interface Entry {
  model: CanvasModel;
  fileUrl: string | undefined;
  view: CanvasView | null;
}

class Layer implements CanvasLayer {
  readonly mirror: HTMLElement;
  private readonly entries = new Map<string, Entry>();
  private readonly fonts: FontRegistry;
  private readonly images: ImageRegistry;
  private readonly renderer = new CanvasRenderer();
  private readonly mirrorView: Mirror;
  private measurer: TextMeasurer | null = null;
  private measuredFonts = 0;
  private huds: string[] = [];
  private menu: string | null = null;
  /** Canvases playing their close animation (GAME-UI §10.5): drawn, but closed. */
  private closing: string[] = [];
  /** What each drawn canvas was at the last setOpen: a change starts its open or close animation. */
  private status = new Map<string, 'open' | 'closing'>();
  /** The models drawn: open and closing canvases, bottom to top. */
  private open: CanvasModel[] = [];
  private width = 0;
  private height = 0;
  private clientWidth = -1;
  private clientHeight = -1;
  private hidden = false;
  private disposed = false;
  private readonly warned = new Set<string>();
  /** 3D model elements (§11.1): their GLBs, parsed once per URL and hash, and the studio environment. */
  private readonly models: UiModels;
  /** The views drawn now (open and closing canvases): their models step on the UI clock. */
  private drawn: CanvasView[] = [];

  constructor(private readonly host: CanvasHost) {
    const warn = (message: string) => host.warn(message);
    this.fonts = new FontRegistry(warn);
    this.renderer.warn = warn; // game shaders on UI meshes, WebGPU compositing
    this.images = new ImageRegistry(warn);
    this.mirrorView = new Mirror();
    this.mirror = this.mirrorView.root;
    this.models = new UiModels((message) => {
      if (this.warned.has(message)) return;
      this.warned.add(message);
      host.warn(message);
    });
  }

  /**
   * A GLB's URL: `model.glb` is relative to the canvas file, like the URLs in `assets` (an entry there
   * for the GLB, when the export writes one, wins).
   */
  private glbUrl(data: UiCanvasData, fileUrl: string | undefined): (glb: string) => string {
    return (glb) => this.url({ ...data, assets: { [glb]: glb, ...data.assets } }, glb, fileUrl);
  }

  /** Frees the GLBs no loaded canvas uses any more (after their views were disposed). */
  private retainGlbs(): void {
    const keep = new Set<string>();
    for (const entry of this.entries.values()) {
      for (const source of this.models.sources(entry.model.data, this.glbUrl(entry.model.data, entry.fileUrl))) keep.add(source.key);
    }
    this.models.retain(keep);
  }

  private url(data: UiCanvasData, path: string, fileUrl: string | undefined): string {
    try {
      return this.host.assetUrl(data, path, fileUrl);
    } catch {
      return path;
    }
  }

  private textMeasurer(): TextMeasurer {
    this.measurer ??= canvasMeasurer() ?? fallbackMeasurer;
    if (this.fonts.version !== this.measuredFonts) {
      this.measuredFonts = this.fonts.version;
      this.measurer.invalidate();
    }
    return this.measurer;
  }

  private env(data: UiCanvasData, fileUrl: string | undefined): ModelEnv {
    return {
      value: (path) => {
        try {
          return this.host.value(path);
        } catch {
          return undefined;
        }
      },
      translate: (text) => {
        try {
          return this.host.translate?.(text) ?? text;
        } catch {
          return text;
        }
      },
      localeVersion: () => this.host.localeVersion?.() ?? 0,
      placeholders: () => this.host.placeholders?.() === true,
      measurer: () => this.textMeasurer(),
      family: (font) => this.fonts.family(font ? this.url(data, font, fileUrl) : null, font ? fontAxesOf(data, font) : undefined),
      fontsVersion: () => this.fonts.version,
      warn: (message) => {
        if (this.warned.has(message)) return; // a canvas edited live is read again
        this.warned.add(message);
        this.host.warn(message);
      },
    };
  }

  set(data: UiCanvasData, fileUrl?: string): Promise<void> {
    if (this.disposed) return Promise.resolve();
    if (!data || typeof data.canvas !== 'string' || !data.canvas || !Array.isArray(data.elements)) {
      this.host.warn('Game UI: ignored canvas data without a canvas name or elements');
      return Promise.resolve();
    }
    const name = data.canvas;
    const old = this.entries.get(name);
    const model = new CanvasModel(data, this.env(data, fileUrl));
    if (old) {
      model.adopt(old.model);
      old.view?.dispose();
    } else {
      // Opened before it loaded: it animates in now.
      const status = this.status.get(name);
      if (status) model.play(status === 'open' ? 'open' : 'close');
    }
    model.setViewport(this.width, this.height);
    this.entries.set(name, { model, fileUrl, view: null });
    // Its GLB loads now (a replaced canvas keeps the parse while the GLB is the same); others are freed.
    const glbs = this.models.load(data, this.glbUrl(data, fileUrl));
    if (old) this.retainGlbs();
    if (this.status.has(name)) this.applyOpen();
    return this.preload(data, fileUrl, glbs);
  }

  private preload(data: UiCanvasData, fileUrl: string | undefined, glbs?: Promise<void>): Promise<void> {
    const fonts = new Set<string>();
    const images = new Set<string>();
    for (const el of data.elements) {
      if (el.type === 'text' && el.text?.font) fonts.add(el.text.font);
      if (el.type === 'image' && el.image?.src) images.add(this.url(data, el.image.src, fileUrl));
      if (el.type === 'mesh' && el.mesh?.material?.texture) images.add(this.url(data, el.mesh.material.texture, fileUrl));
    }
    const waits: Promise<void>[] = [];
    for (const font of fonts) waits.push(this.fonts.load(this.url(data, font, fileUrl), fontAxesOf(data, font)));
    for (const url of images) waits.push(this.images.get(url).ready);
    // The code of the game shaders its meshes and 3D models use (§11.4), so it opens with them applied.
    if (data.materials || data.elements.some((el) => el.type === 'mesh' && el.mesh?.material?.shader)) waits.push(loadUiShaders(data));
    if (glbs && data.elements.some((el) => el.type === 'model')) waits.push(glbs);
    if (waits.length === 0) return Promise.resolve();
    let timer: ReturnType<typeof setTimeout> | undefined;
    const timeout = new Promise<void>((resolve) => {
      timer = setTimeout(resolve, ASSET_WAIT_MS);
    });
    return Promise.race([Promise.all(waits).then(() => undefined), timeout]).finally(() => clearTimeout(timer));
  }

  remove(name: string): void {
    const entry = this.entries.get(name);
    if (!entry) return;
    entry.view?.dispose();
    this.entries.delete(name);
    this.retainGlbs();
    this.huds = this.huds.filter((n) => n !== name);
    if (this.menu === name) this.menu = null;
    this.closing = this.closing.filter((n) => n !== name);
    this.status.delete(name);
    this.applyOpen();
  }

  has(name: string): boolean {
    return this.entries.has(name);
  }

  names(): string[] {
    return [...this.entries.keys()];
  }

  data(name: string): UiCanvasData | null {
    return this.entries.get(name)?.model.data ?? null;
  }

  setOpen(huds: readonly string[], menu: string | null, closing: readonly string[] = []): void {
    const list: string[] = [];
    for (const name of huds) if (name !== menu && !list.includes(name)) list.push(name);
    const ending: string[] = [];
    for (const name of closing) if (name !== menu && !ending.includes(name)) ending.push(name);
    // A canvas that opens plays `open` from its start (a closing one too); one that closes plays `close`;
    // one no longer drawn goes back to rest.
    const status = new Map<string, 'open' | 'closing'>();
    for (const name of list) status.set(name, 'open');
    if (menu) status.set(menu, 'open');
    for (const name of ending) status.set(name, 'closing');
    for (const [name, now] of status) {
      if (this.status.get(name) !== now) this.entries.get(name)?.model.play(now === 'open' ? 'open' : 'close');
    }
    for (const name of this.status.keys()) if (!status.has(name)) this.entries.get(name)?.model.play(null);
    this.status = status;
    this.huds = list;
    this.menu = menu;
    this.closing = ending;
    this.applyOpen();
  }

  /** Rebuilds what is drawn and mirrored from the open names (names not loaded yet wait for set()). */
  private applyOpen(): void {
    const view = (name: string): CanvasView | null => {
      const entry = this.entries.get(name);
      if (!entry) return null;
      entry.view ??= new CanvasView(entry.model, {
        images: this.images,
        url: (path) => this.url(entry.model.data, path, entry.fileUrl),
        quad: this.renderer.quad,
        pixelRatio: () => Math.min((globalThis as { devicePixelRatio?: number }).devicePixelRatio || 1, 2),
        model: (node) => this.models.item(entry.model.data, node, this.glbUrl(entry.model.data, entry.fileUrl), this.renderer.quad),
      });
      return entry.view;
    };
    const huds: CanvasView[] = [];
    for (const name of this.huds) {
      const v = view(name);
      if (v) huds.push(v);
    }
    // Menus bottom to top: closing ones (not HUDs, which keep their place) under the open menu.
    const menus: CanvasView[] = [];
    for (const name of [...this.closing.filter((n) => !this.huds.includes(n)), ...(this.menu ? [this.menu] : [])]) {
      const v = view(name);
      if (v) menus.push(v);
    }
    this.open = [...huds, ...menus].map((v) => v.model);
    this.drawn = [...huds, ...menus];
    // A canvas no longer drawn gives back its models' render targets (hidden and closed canvases render none).
    for (const entry of this.entries.values()) if (entry.view && !this.drawn.includes(entry.view)) entry.view.releaseModels();
    this.renderer.setOpen(huds, menus);
    this.ensureSize();
    for (const model of this.open) model.refresh();
    this.mirrorView.sync(this.open);
  }

  layout(width: number, height: number): void {
    this.width = width;
    this.height = height;
    for (const entry of this.entries.values()) entry.model.setViewport(width, height);
    this.renderer.setSize(Math.max(1, width), Math.max(1, height));
  }

  /** Lays out again when the game canvas's client size changed since the last look. */
  private ensureSize(): void {
    const canvas = this.host.canvas;
    const width = Number(canvas?.clientWidth) || 0;
    const height = Number(canvas?.clientHeight) || 0;
    if (width === this.clientWidth && height === this.clientHeight) return;
    this.clientWidth = width;
    this.clientHeight = height;
    if (width > 0 && height > 0) this.layout(width, height);
  }

  private model(name: string): CanvasModel | null {
    const model = this.entries.get(name)?.model ?? null;
    if (model) {
      this.ensureSize();
      model.refresh();
    }
    return model;
  }

  private node(canvas: string, id: string): { model: CanvasModel; node: UiNode } | null {
    const model = this.entries.get(canvas)?.model;
    const node = model?.byId.get(id);
    return model && node ? { model, node } : null;
  }

  update(dt: number): void {
    if (this.disposed) return;
    this.ensureSize();
    for (const model of this.open) model.step(dt);
    for (const view of this.drawn) view.stepModels(dt);
    this.renderer.stepShaders(dt);
    this.mirrorView.sync(this.open);
  }

  animationPhase(name: string): 'open' | 'loop' | 'close' | null {
    return this.status.has(name) ? (this.entries.get(name)?.model.animator.phase ?? null) : null;
  }

  buttons(name: string): CanvasButton[] {
    return this.model(name)?.visibleButtons() ?? [];
  }

  setButtonState(name: string, focusedId: string | null, pressedId: string | null): void {
    this.entries.get(name)?.model.setButtonState(focusedId, pressedId);
  }

  find(path: string, prefer: readonly string[]): CanvasElementRef | null {
    const slash = path.indexOf('/');
    if (slash > 0) {
      const canvas = path.slice(0, slash);
      const node = this.entries.get(canvas)?.model.byName.get(path.slice(slash + 1));
      if (node) return { canvas, element: node.el };
    }
    for (const name of prefer) {
      const node = this.entries.get(name)?.model.byName.get(path);
      if (node) return { canvas: name, element: node.el };
    }
    for (const [name, entry] of this.entries) {
      const node = entry.model.byName.get(path);
      if (node) return { canvas: name, element: node.el };
    }
    return null;
  }

  info(canvas: string, id: string): UiElementInfo {
    const found = this.node(canvas, id);
    if (!found) throw new Error(`Game UI: no element ${id} in canvas ${canvas}`);
    this.model(canvas);
    return found.model.info(found.node);
  }

  setText(canvas: string, id: string, value: string | null): void {
    const found = this.node(canvas, id);
    if (found && found.node.el.type === 'text') found.model.setText(found.node, value === null ? null : String(value));
  }

  setVisible(canvas: string, id: string, visible: boolean | null): void {
    const found = this.node(canvas, id);
    if (found) found.model.setVisible(found.node, visible);
  }

  setBar(canvas: string, id: string, value: number | null): void {
    const found = this.node(canvas, id);
    if (found?.node.bar) found.model.setBar(found.node, value);
  }

  texts(names: readonly string[]): Record<string, string> {
    const out: Record<string, string> = {};
    for (const name of names) {
      const model = this.model(name);
      if (!model) continue;
      for (const node of model.texts) if (node.visible) out[`${name}/${node.el.name}`] = node.string;
    }
    return out;
  }

  hiddenElements(names: readonly string[]): string[] {
    const out: string[] = [];
    for (const name of names) {
      const model = this.entries.get(name)?.model;
      if (!model) continue;
      for (const node of model.nodes) if (!model.ownVisible(node) && !model.isStateArt(node)) out.push(`${name}/${node.el.name}`);
    }
    return out.sort();
  }

  hitTest(name: string, x: number, y: number): CanvasButton | null {
    const model = this.model(name);
    const node = model?.hitTest(x, y);
    return model && node ? model.buttonInfo(node) : null;
  }

  render(raw: unknown): void {
    if (!raw || this.hidden || this.disposed || this.open.length === 0) return;
    // update() already looked at the canvas size this frame; reading it again after the mirror's DOM
    // writes would force a synchronous layout.
    if (this.clientWidth < 0) this.ensureSize();
    for (const model of this.open) model.ensure();
    this.renderer.render(raw);
  }

  setHidden(hidden: boolean): void {
    this.hidden = hidden;
    this.mirrorView.setHidden(hidden);
  }

  dispose(): void {
    if (this.disposed) return;
    this.disposed = true;
    for (const entry of this.entries.values()) entry.view?.dispose();
    this.entries.clear();
    this.open = [];
    this.drawn = [];
    this.models.dispose();
    this.renderer.dispose();
    this.mirrorView.dispose();
  }
}

/** Creates the canvas layer for a game (the dynamic-import entry of this chunk). */
export function createCanvasLayer(host: CanvasHost): CanvasLayer {
  return new Layer(host);
}
