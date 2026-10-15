// Just enough DOM for a Game in Node: elements with style/dataset/children and bubbling events,
// keyboard/pointer/wheel event classes, a manual requestAnimationFrame and a fetch that serves files.
import { readFileSync } from 'node:fs';
import { FakeAudioContext } from './helpers';

type Listener = Parameters<EventTarget['addEventListener']>[1];

export class FakeElement extends EventTarget {
  readonly tagName: string;
  readonly style: Record<string, string> = { cssText: '' };
  readonly dataset: Record<string, string> = {};
  readonly children: FakeElement[] = [];
  parentElement: FakeElement | null = null;
  textContent = '';
  isContentEditable = false;
  clientWidth: number;
  clientHeight: number;

  constructor(tag: string, width = 0, height = 0) {
    super();
    this.tagName = tag.toUpperCase();
    this.clientWidth = width;
    this.clientHeight = height;
  }

  appendChild<T extends FakeElement>(child: T): T {
    child.remove();
    child.parentElement = this;
    this.children.push(child);
    return child;
  }
  append(...nodes: FakeElement[]): void {
    for (const node of nodes) this.appendChild(node);
  }
  remove(): void {
    const parent = this.parentElement;
    if (!parent) return;
    parent.children.splice(parent.children.indexOf(this), 1);
    this.parentElement = null;
  }
  get firstElementChild(): FakeElement | null {
    return this.children[0] ?? null;
  }
  get nextElementSibling(): FakeElement | null {
    const siblings = this.parentElement?.children ?? [];
    return siblings[siblings.indexOf(this) + 1] ?? null;
  }
  getBoundingClientRect(): { left: number; top: number; width: number; height: number } {
    return { left: 0, top: 0, width: this.clientWidth, height: this.clientHeight };
  }
  /** All descendants (depth first). */
  all(): FakeElement[] {
    return this.children.flatMap((c) => [c, ...c.all()]);
  }

  override dispatchEvent(event: Event): boolean {
    const result = super.dispatchEvent(event);
    if (!event.bubbles || event.cancelBubble) return result;
    if (this.parentElement) this.parentElement.dispatchEvent(event);
    else if (this === (globalThis as unknown as { document: FakeDocument }).document.body) globalThis.window.dispatchEvent(event);
    return result;
  }
}

class FakeDocument {
  readonly body = new FakeElement('body');
  createElement(tag: string): FakeElement {
    return new FakeElement(tag);
  }
}

type Init = Record<string, unknown> & EventInit;

class FakeKeyboardEvent extends Event {
  readonly code: string;
  readonly key: string;
  readonly repeat = false;
  constructor(type: string, init: Init = {}) {
    super(type, init);
    this.code = String(init.code ?? '');
    this.key = String(init.key ?? '');
  }
}

class FakeMouseEvent extends Event {
  readonly clientX: number;
  readonly clientY: number;
  readonly movementX: number;
  readonly movementY: number;
  readonly button: number;
  readonly buttons: number;
  constructor(type: string, init: Init = {}) {
    super(type, init);
    this.clientX = Number(init.clientX ?? 0);
    this.clientY = Number(init.clientY ?? 0);
    this.movementX = Number(init.movementX ?? 0);
    this.movementY = Number(init.movementY ?? 0);
    this.button = Number(init.button ?? 0);
    this.buttons = Number(init.buttons ?? 0);
  }
}

class FakePointerEvent extends FakeMouseEvent {
  readonly pointerId: number;
  readonly pointerType: string;
  constructor(type: string, init: Init = {}) {
    super(type, init);
    this.pointerId = Number(init.pointerId ?? 1);
    this.pointerType = String(init.pointerType ?? 'mouse');
  }
}

class FakeWheelEvent extends FakeMouseEvent {
  readonly deltaX: number;
  readonly deltaY: number;
  constructor(type: string, init: Init = {}) {
    super(type, init);
    this.deltaX = Number(init.deltaX ?? 0);
    this.deltaY = Number(init.deltaY ?? 0);
  }
}

export interface FakeDom {
  canvas: FakeElement;
  /** Runs one requestAnimationFrame callback batch at `now` ms. */
  frame(now: number): void;
  /** Every event type dispatched on the window, in order. */
  windowEvents: string[];
  restore(): void;
}

/** Installs the fake DOM globals; `files` maps URL paths ('/x.json') to files on disk. */
export function installDom(files: Record<string, string> = {}, size: [number, number] = [800, 450]): FakeDom {
  const g = globalThis as Record<string, unknown>;
  const saved = new Map(['window', 'document', 'HTMLElement', 'KeyboardEvent', 'MouseEvent', 'PointerEvent', 'WheelEvent', 'location', 'requestAnimationFrame', 'fetch'].map((k) => [k, g[k]]));
  const win = new EventTarget() as EventTarget & Record<string, unknown>;
  const windowEvents: string[] = [];
  const record: Listener = (event) => windowEvents.push(event.type);
  for (const type of ['keydown', 'keyup', 'pointermove', 'pointerdown', 'pointerup', 'mousedown', 'mouseup', 'click', 'wheel']) {
    win.addEventListener(type, record);
  }
  win.innerWidth = size[0];
  win.innerHeight = size[1];
  // Rapier's WASM glue looks for window.crypto (getrandom) the first time a world steps.
  Object.assign(win, { crypto: globalThis.crypto, performance: globalThis.performance, navigator: globalThis.navigator });
  win.AudioContext = FakeAudioContext; // The first scripted key press unlocks audio, as a real one would.
  const document = new FakeDocument();
  const canvas = document.body.appendChild(new FakeElement('canvas', size[0], size[1]));
  let callbacks: Array<(now: number) => void> = [];
  Object.assign(g, {
    window: win,
    document,
    HTMLElement: FakeElement,
    KeyboardEvent: FakeKeyboardEvent,
    MouseEvent: FakeMouseEvent,
    PointerEvent: FakePointerEvent,
    WheelEvent: FakeWheelEvent,
    location: { href: 'http://game.test/' },
    requestAnimationFrame: (fn: (now: number) => void) => callbacks.push(fn),
    fetch: async (input: string | URL) => {
      const url = new URL(String(input), 'http://game.test/');
      const file = files[url.pathname];
      if (!file) return new Response('missing', { status: 404 });
      const type = file.endsWith('.json') ? 'application/json' : 'model/gltf-binary';
      return new Response(readFileSync(file), { headers: { 'content-type': type } });
    },
  });
  return {
    canvas,
    windowEvents,
    frame(now) {
      const batch = callbacks;
      callbacks = [];
      for (const fn of batch) fn(now);
    },
    restore() {
      for (const [k, v] of saved) {
        if (v === undefined) delete g[k];
        else g[k] = v;
      }
    },
  };
}
