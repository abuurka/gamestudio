// The full-screen fade used by scene transitions (`ctx.loadScene`). Its opacity is set from the
// game's clock, so a transition looks the same in a playtest screenshot as on screen.

export class Fade {
  readonly element: HTMLDivElement;
  private opacity = 0;

  constructor(private readonly canvas: HTMLCanvasElement) {
    this.element = document.createElement('div');
    this.element.className = 'gs-fade';
    this.element.style.cssText = 'position:fixed;z-index:30;pointer-events:none;display:none;background:#000;opacity:0';
    document.body.appendChild(this.element);
    this.layout();
  }

  /** 0 = clear (hidden), 1 = fully covered. */
  set(opacity: number, color = '#000'): void {
    const value = Math.min(1, Math.max(0, opacity));
    if (value === this.opacity && this.element.style.background === color) return;
    this.opacity = value;
    this.element.style.background = color;
    this.element.style.opacity = value.toFixed(3);
    this.element.style.display = value > 0 ? 'block' : 'none';
  }

  get value(): number {
    return this.opacity;
  }

  layout(): void {
    const r = this.canvas.getBoundingClientRect();
    Object.assign(this.element.style, { left: `${r.left}px`, top: `${r.top}px`, width: `${r.width}px`, height: `${r.height}px` });
  }

  dispose(): void {
    this.element.remove();
  }
}
