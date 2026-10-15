// Game UI controller fixtures: canvas data builders (docs/GAME-UI.md §3.2), the exported files a fake
// fetch serves (ui/index.json + canvases), and a CanvasLayer stand-in with simple layout: the design
// size equals the fake canvas (800 × 450), so design px are canvas px, and a button's rect is its mesh
// bounds around its position.
import { mkdtempSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import type { UiCanvasData, UiCanvasSettings, UiElementData } from '../src/ui/canvas/format';
import type { CanvasButton, CanvasHost, CanvasLayer, CanvasRect, UiElementInfo } from '../src/ui/canvas/types';

export const SIZE: [number, number] = [800, 450];

const BUTTON = { action: 'event', target: '', disabled: false, order: 0, focusScale: 1.06, sound: '', focusSound: '' };

let ids = 0;
const nextId = (): string => `gs_${(++ids).toString(16).padStart(12, '0')}`;

/** A button: a mesh with a UiButton (rect: `size` centred on `at`) and its label as a text child. */
export function button(
  name: string,
  label: string,
  at: [number, number],
  ui: Partial<typeof BUTTON> = {},
  size: [number, number] = [200, 50],
): UiElementData[] {
  const id = nextId();
  const [w, h] = size;
  return [
    {
      id,
      name,
      parent: null,
      type: 'mesh',
      visible: true,
      order: 0,
      transform: { x: at[0], y: at[1], rotation: 0, scaleX: 1, scaleY: 1 },
      anchor: 'center',
      stretch: 'none',
      components: { UiButton: { ...BUTTON, ...ui } },
      mesh: {
        positions: [-w / 2, -h / 2, w / 2, -h / 2, w / 2, h / 2, -w / 2, h / 2],
        indices: [0, 1, 2, 0, 2, 3],
        uvs: null,
        colors: null,
        bounds: [-w / 2, -h / 2, w / 2, h / 2],
        material: { color: [1, 1, 1, 1], texture: '', blend: 'alpha' },
      },
    },
    text(`${name}Label`, label, [0, 0], id),
  ];
}

/** A text element (no box, centred on `at`). */
export function text(name: string, body: string, at: [number, number], parent: string | null = null, visible = true): UiElementData {
  return {
    id: nextId(),
    name,
    parent,
    type: 'text',
    visible,
    order: 0,
    transform: { x: at[0], y: at[1], rotation: 0, scaleX: 1, scaleY: 1 },
    ...(parent === null && { anchor: 'center' as const, stretch: 'none' as const }),
    components: {},
    text: {
      body,
      font: '',
      size: 32,
      color: [1, 1, 1, 1],
      align: 'center',
      valign: 'center',
      lineSpacing: 1,
      letterSpacing: 1,
      wordSpacing: 1,
      box: null,
      offset: [0, 0],
      shear: 0,
    },
  };
}

/** A bar group with a UiBar (value: a binding path or ''). */
export function bar(name: string, at: [number, number], value = ''): UiElementData {
  return {
    id: nextId(),
    name,
    parent: null,
    type: 'group',
    visible: true,
    order: 0,
    transform: { x: at[0], y: at[1], rotation: 0, scaleX: 1, scaleY: 1 },
    anchor: 'center',
    stretch: 'none',
    components: { UiBar: { value, max: 1, fill: null, direction: 'right', smoothing: 0 } },
  };
}

export function canvas(name: string, settings: Partial<UiCanvasSettings>, elements: Array<UiElementData | UiElementData[]>): UiCanvasData {
  const flat = elements.flat();
  flat.forEach((e, i) => (e.order = i));
  return {
    format: 'gamestudio.ui',
    formatVersion: 1,
    canvas: name,
    source: 'ui/ui.blend',
    rev: 1,
    exportedAt: '2026-10-11T12:00:00.000Z',
    size: SIZE,
    settings: {
      kind: 'menu',
      open: 'manual',
      pause: true,
      pauseMenu: false,
      back: 'none',
      scaleMode: 'fit',
      backdrop: [0, 0, 0, 0.6],
      layer: 0,
      focus: null,
      ...settings,
    },
    elements: flat,
    assets: { 'assets/sfx/click.ogg': '../files/assets/sfx/click.ogg?v=0123456789' },
  };
}

/** The game's canvases as export files: URL path ('/export/ui/...') → file on disk. */
export function uiFiles(canvases: UiCanvasData[], base = '/export/'): Record<string, string> {
  const dir = mkdtempSync(path.join(tmpdir(), 'gs-ui-'));
  const files: Record<string, string> = {};
  const index = { format: 'gamestudio.ui-index', formatVersion: 1, canvases: {} as Record<string, unknown> };
  for (const c of canvases) {
    const file = `${c.canvas}.ui.json`;
    index.canvases[c.canvas] = { file, rev: c.rev, source: c.source, kind: c.settings.kind, open: c.settings.open };
    writeFileSync(path.join(dir, file), JSON.stringify(c));
    files[`${base}ui/${file}`] = path.join(dir, file);
  }
  writeFileSync(path.join(dir, 'index.json'), JSON.stringify(index));
  files[`${base}ui/index.json`] = path.join(dir, 'index.json');
  return files;
}

interface Entry {
  data: UiCanvasData;
  fileUrl: string | undefined;
  texts: Map<string, string | null>;
  visible: Map<string, boolean | null>;
  bars: Map<string, number | null>;
}

/** A CanvasLayer stand-in: the controller's view of canvases, with flat layout and recorded calls. */
export class FakeLayer implements CanvasLayer {
  readonly mirror = { tagName: 'DIV' } as unknown as HTMLElement;
  readonly entries = new Map<string, Entry>();
  open: { huds: string[]; menu: string | null } = { huds: [], menu: null };
  readonly states = new Map<string, { focused: string | null; pressed: string | null }>();
  /** Every focus state pushed per canvas, in order. */
  readonly history: string[] = [];
  hidden = false;
  updates = 0;
  renders = 0;
  /** set() resolves at once unless a test holds it (`hold`). */
  hold: Promise<void> | null = null;

  constructor(readonly host: CanvasHost) {}

  set(data: UiCanvasData, fileUrl?: string): Promise<void> {
    const old = this.entries.get(data.canvas);
    this.entries.set(data.canvas, {
      data,
      fileUrl,
      texts: old?.texts ?? new Map(),
      visible: old?.visible ?? new Map(),
      bars: old?.bars ?? new Map(),
    });
    return this.hold ?? Promise.resolve();
  }
  remove(name: string): void {
    this.entries.delete(name);
  }
  has(name: string): boolean {
    return this.entries.has(name);
  }
  names(): string[] {
    return [...this.entries.keys()];
  }
  data(name: string): UiCanvasData | null {
    return this.entries.get(name)?.data ?? null;
  }
  setOpen(huds: readonly string[], menu: string | null): void {
    this.open = { huds: [...huds], menu };
  }
  layout(): void {}
  update(): void {
    this.updates++;
  }
  /** The stand-in doesn't animate: every canvas rests. */
  animationPhase(): null {
    return null;
  }
  buttons(name: string): CanvasButton[] {
    const entry = this.entries.get(name);
    if (!entry) return [];
    return entry.data.elements
      .filter((e) => e.components.UiButton && this.visibleOf(entry, e))
      .map((e) => {
        const b = e.components.UiButton as typeof BUTTON;
        const label = entry.data.elements.find((c) => c.parent === e.id && c.type === 'text');
        return {
          canvas: name,
          id: e.id,
          name: e.name,
          label: label ? this.textOf(entry, label) : e.name,
          disabled: b.disabled,
          order: b.order,
          action: b.action,
          target: b.target,
          event: b.action === 'event' && b.target ? b.target : e.name,
          focusScale: b.focusScale,
          sound: b.sound,
          focusSound: b.focusSound,
          rect: this.rectOf(e),
        };
      });
  }
  setButtonState(name: string, focusedId: string | null, pressedId: string | null): void {
    this.states.set(name, { focused: focusedId, pressed: pressedId });
    this.history.push(`${name}:${focusedId ?? '-'}:${pressedId ?? '-'}`);
  }
  find(p: string, prefer: readonly string[]): { canvas: string; element: UiElementData } | null {
    const slash = p.indexOf('/');
    const names = slash >= 0 ? [p.slice(0, slash)] : [...prefer, ...this.names()];
    const elementName = slash >= 0 ? p.slice(slash + 1) : p;
    for (const name of names) {
      const element = this.entries.get(name)?.data.elements.find((e) => e.name === elementName);
      if (element) return { canvas: name, element };
    }
    return null;
  }
  info(canvas: string, id: string): UiElementInfo {
    const entry = this.entries.get(canvas)!;
    const e = entry.data.elements.find((x) => x.id === id)!;
    return { canvas, name: e.name, id, type: e.type, visible: this.visibleOf(entry, e), text: e.type === 'text' ? this.textOf(entry, e) : null, rect: this.rectOf(e) };
  }
  setText(canvas: string, id: string, value: string | null): void {
    this.entries.get(canvas)?.texts.set(id, value);
  }
  setVisible(canvas: string, id: string, visible: boolean | null): void {
    this.entries.get(canvas)?.visible.set(id, visible);
  }
  setBar(canvas: string, id: string, value: number | null): void {
    this.entries.get(canvas)?.bars.set(id, value);
  }
  bar(canvas: string, name: string): number | null {
    const entry = this.entries.get(canvas)!;
    const e = entry.data.elements.find((x) => x.name === name)!;
    return entry.bars.get(e.id) ?? null;
  }
  texts(names: readonly string[]): Record<string, string> {
    const out: Record<string, string> = {};
    for (const name of names) {
      const entry = this.entries.get(name);
      for (const e of entry?.data.elements ?? []) if (e.type === 'text' && this.visibleOf(entry!, e)) out[`${name}/${e.name}`] = this.textOf(entry!, e);
    }
    return out;
  }
  hiddenElements(names: readonly string[]): string[] {
    const out: string[] = [];
    for (const name of names) {
      const entry = this.entries.get(name);
      for (const e of entry?.data.elements ?? []) if (!(entry!.visible.get(e.id) ?? e.visible)) out.push(`${name}/${e.name}`);
    }
    return out.sort();
  }
  hitTest(name: string, x: number, y: number): CanvasButton | null {
    const hits = this.buttons(name).filter((b) => x >= b.rect.x && x <= b.rect.x + b.rect.width && y >= b.rect.y && y <= b.rect.y + b.rect.height);
    return hits.at(-1) ?? null;
  }
  render(raw: unknown): void {
    if (raw && !this.hidden) this.renders++;
  }
  setHidden(hidden: boolean): void {
    this.hidden = hidden;
  }
  dispose(): void {}

  private visibleOf(entry: Entry, e: UiElementData): boolean {
    const own = entry.visible.get(e.id) ?? e.visible;
    const parent = e.parent ? entry.data.elements.find((p) => p.id === e.parent) : null;
    return own && (!parent || this.visibleOf(entry, parent));
  }
  private textOf(entry: Entry, e: UiElementData): string {
    const override = entry.texts.get(e.id);
    if (override !== undefined && override !== null) return override;
    // As the model: the body in the current language, then its bindings.
    const body = e.text?.body ?? '';
    return (this.host.translate?.(body) ?? body).replace(/\{([^}]+)\}/g, (_, p: string) => String(this.host.value(p.trim()) ?? ''));
  }
  private rectOf(e: UiElementData): CanvasRect {
    const [x0, y0, x1, y1] = e.mesh?.bounds ?? [0, 0, 0, 0];
    return { x: e.transform.x + x0, y: e.transform.y + y0, width: x1 - x0, height: y1 - y0 };
  }
}
