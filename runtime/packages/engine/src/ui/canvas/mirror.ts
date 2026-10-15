// The accessibility mirror (docs/GAME-UI.md §5.4): a visually hidden DOM copy of the open canvases, so
// screen readers and `document.body.innerText` (MCP playtests) see their texts and buttons. Hidden by
// clipping to 1 px, not display/visibility, so innerText still includes it. Rebuilt per canvas only when
// its content version changes.
import type { CanvasModel } from './model';

const HIDDEN =
  'position:absolute;left:0;top:0;width:1px;height:1px;margin:-1px;padding:0;border:0;overflow:hidden;' +
  'clip:rect(0 0 0 0);clip-path:inset(50%);white-space:nowrap;pointer-events:none;';

/** Sets an attribute; the test DOM has no setAttribute, so it gets a property. */
function attr(el: HTMLElement, name: string, value: string): void {
  if (typeof el.setAttribute === 'function') el.setAttribute(name, value);
  else (el as unknown as Record<string, string>)[name] = value;
}

function clear(el: HTMLElement): void {
  while (el.firstElementChild) el.firstElementChild.remove();
}

export class Mirror {
  readonly root: HTMLElement;
  private readonly canvases = new Map<string, { div: HTMLElement; model: CanvasModel; version: number }>();
  private order: string[] = [];

  constructor() {
    this.root = document.createElement('div');
    this.root.className = 'gs-ui-mirror';
    this.root.style.cssText = HIDDEN;
    document.body.appendChild(this.root);
  }

  /** Shows these canvases (in this order), rebuilding the ones whose content changed. */
  sync(models: readonly CanvasModel[]): void {
    if (this.current(models)) return;
    const names = models.map((m) => m.name);
    let reorder = names.length !== this.order.length || names.some((n, i) => n !== this.order[i]);
    for (const [name, entry] of this.canvases) {
      if (models.includes(entry.model)) continue;
      entry.div.remove();
      this.canvases.delete(name);
    }
    for (const model of models) {
      let entry = this.canvases.get(model.name);
      if (!entry) {
        reorder = true;
        const div = document.createElement('div');
        div.className = 'gs-ui-canvas';
        div.dataset.canvas = model.name;
        div.style.cssText = 'pointer-events:none;';
        entry = { div, model, version: -1 };
        this.canvases.set(model.name, entry);
      }
      if (entry.version !== model.contentVersion) {
        entry.version = model.contentVersion;
        this.fill(entry.div, model);
      }
    }
    if (reorder) {
      for (const name of names) this.root.appendChild(this.canvases.get(name)!.div);
      this.order = names;
    }
  }

  /** Nothing to do: same canvases, same order, same content (no allocation on the per-frame path). */
  private current(models: readonly CanvasModel[]): boolean {
    if (models.length !== this.order.length) return false;
    for (let i = 0; i < models.length; i++) {
      const model = models[i]!;
      const entry = this.canvases.get(model.name);
      if (!entry || entry.model !== model || entry.version !== model.contentVersion || this.order[i] !== model.name) return false;
    }
    return true;
  }

  private fill(div: HTMLElement, model: CanvasModel): void {
    clear(div);
    // A focused button shows its normal and its focus label at once: list a button's text once.
    const said = new Map<unknown, Set<string>>();
    for (const node of model.nodes) {
      if (!node.visible) continue;
      if (node.button) {
        const button = document.createElement('div');
        const label = model.labelOf(node);
        button.dataset.label = label;
        attr(button, 'aria-label', label);
        if (node.slider) {
          attr(button, 'role', 'slider');
          attr(button, 'aria-valuemin', String(node.slider.min));
          attr(button, 'aria-valuemax', String(node.slider.max));
          attr(button, 'aria-valuenow', String(node.value));
        } else if (node.toggle) {
          attr(button, 'role', 'switch');
          attr(button, 'aria-checked', node.value ? 'true' : 'false');
        } else {
          attr(button, 'role', 'button');
        }
        if (node.button.disabled) attr(button, 'aria-disabled', 'true');
        div.appendChild(button);
      }
      if (node.el.type === 'text' && node.string) {
        if (node.owner) {
          const strings = said.get(node.owner) ?? new Set<string>();
          said.set(node.owner, strings);
          if (strings.has(node.string)) continue;
          strings.add(node.string);
        }
        const text = document.createElement('div');
        text.dataset.name = node.el.name;
        text.textContent = node.string;
        div.appendChild(text);
      }
    }
  }

  setHidden(hidden: boolean): void {
    this.root.style.display = hidden ? 'none' : '';
  }

  dispose(): void {
    this.root.remove();
    this.canvases.clear();
    this.order = [];
  }
}
