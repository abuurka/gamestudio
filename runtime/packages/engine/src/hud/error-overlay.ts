// Dev-only panel listing runtime errors. Identical messages are counted, not repeated.

export interface OverlayError {
  msg: string;
  file?: string;
  line?: number;
  col?: number;
}

export class ErrorOverlay {
  private element: HTMLDivElement | null = null;
  private list: HTMLOListElement | null = null;
  private readonly entries = new Map<string, { li: HTMLLIElement; count: number }>();

  add(error: OverlayError): void {
    const where = error.file ? ` (${error.file}${error.line ? `:${error.line}${error.col ? `:${error.col}` : ''}` : ''})` : '';
    const text = `${error.msg}${where}`;
    const existing = this.entries.get(text);
    if (existing) {
      existing.count++;
      existing.li.textContent = `${text}  (×${existing.count})`;
      return;
    }
    this.ensure();
    const li = document.createElement('li');
    li.textContent = text;
    li.style.cssText = 'margin:0 0 6px;white-space:pre-wrap;word-break:break-word';
    this.list!.appendChild(li);
    this.entries.set(text, { li, count: 1 });
    this.element!.style.display = 'block';
  }

  clear(): void {
    this.entries.clear();
    if (this.list) this.list.textContent = '';
    if (this.element) this.element.style.display = 'none';
  }

  dispose(): void {
    this.element?.remove();
    this.element = null;
    this.list = null;
    this.entries.clear();
  }

  private ensure(): void {
    if (this.element) return;
    const el = document.createElement('div');
    el.className = 'gs-error-overlay';
    el.style.cssText =
      'position:fixed;left:0;right:0;bottom:0;max-height:40%;overflow:auto;z-index:1000;display:none;' +
      'background:rgba(60,8,12,.92);color:#ffd7d7;font:13px/1.4 ui-monospace,Consolas,monospace;' +
      'padding:10px 14px;border-top:2px solid #ff5a5a;pointer-events:auto';
    const header = document.createElement('div');
    header.style.cssText = 'display:flex;justify-content:space-between;margin-bottom:6px;color:#fff;font-weight:700';
    header.textContent = 'Runtime errors';
    const close = document.createElement('button');
    close.textContent = 'Dismiss';
    close.style.cssText = 'font:inherit;cursor:pointer;background:#ff5a5a;color:#fff;border:0;border-radius:3px;padding:0 8px';
    close.onclick = () => this.clear();
    header.appendChild(close);
    const list = document.createElement('ol');
    list.style.cssText = 'margin:0;padding-left:20px';
    el.append(header, list);
    document.body.appendChild(el);
    this.element = el;
    this.list = list;
  }
}
