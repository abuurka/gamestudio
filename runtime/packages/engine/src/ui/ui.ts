// Game screens (`ctx.ui`): menus and HUDs authored in Blender (canvases, docs/GAME-UI.md) and the
// code-only DOM screens (title, pause, game over and win menus with buttons), over the canvas like the
// HUD. One menu is open at a time, a canvas or a DOM screen; any number of HUD canvases are open under
// it. Menus pause the game through the normal pause path while open, so systems, physics, timers,
// animation, particles and sound all wait. Keyboard (arrows/WASD, Enter/Space, Escape), gamepad
// (d-pad/stick, A, B, Start) and mouse all work, and so do scripted playtest inputs: input is handled
// once per frame (paused or not), in the game's own clock.

import type { SystemContext } from '../context';
import type { Input } from '../input/input';
import type { UiBackAction, UiCanvasData, UiIndex, UiLocaleData } from './canvas/format';
import type { CanvasButton, CanvasElementRef, CanvasLayer, UiElementInfo } from './canvas/types';
import { CanvasScreens, type CanvasClick, type ControlChange, type NavStep } from './canvases';
import { canvasAssetUrl, fetchCanvas, fetchLocales, fetchUiIndex, importCanvasLayer, isCanvasData } from './loader';
import { browserLanguages, LOCALE_KEY, localeData, Locales, type UiLocale } from './locales';
import { isReadOnly, readValue, writeValue, type ValueSources } from './values';

export type { UiLocale } from './locales';

/** What a button does: a function, or 'close' (close the screen), 'resume' (same), 'restart' (`ctx.restart()`). */
export type UiAction = 'close' | 'resume' | 'restart' | ((ctx: SystemContext) => void);

/** A button of a DOM screen (`ctx.ui.screen`). */
export interface UiScreenButton {
  label: string;
  action?: UiAction;
  disabled?: boolean;
}

export interface UiScreenOptions {
  /** Big headline. */
  title?: string;
  /** A line under the title. */
  subtitle?: string;
  /** Body text (`\n` starts a new line). */
  text?: string;
  buttons?: UiScreenButton[];
  /** Pause the game while the screen is open. Default true. */
  pause?: boolean;
  /** What Escape and gamepad B do: an action, or nothing (the default; the pause menu resumes). */
  back?: UiAction | null;
  /** A small help line at the bottom; default "↑ ↓ choose · Enter select" with several buttons. `false` hides it. */
  hint?: string | false;
  /** Opacity of the darkened backdrop, 0..1. Default 0.6. */
  backdrop?: number;
  /** Colour of the selected button (any CSS colour). Default '#ffd400'. */
  color?: string;
  /** Button selected first. Default 0. */
  focus?: number;
}

export interface PauseMenuOptions extends Omit<UiScreenOptions, 'pause' | 'back'> {
  /** Keys that open and close it (KeyboardEvent.code). Default ['Escape', 'KeyP']; gamepad Start always works. */
  keys?: string[];
}

/** A button, or a Blender-authored slider or toggle (docs/GAME-UI.md §10.4). */
export type UiControlKind = 'button' | 'slider' | 'toggle';

export interface UiButtonState {
  label: string;
  /** Canvas buttons: the element (object) name and its canvas. */
  name?: string;
  canvas?: string;
  /** 'button' (DOM screen buttons too), or a canvas slider or toggle. */
  kind: UiControlKind;
  /** Sliders: the value shown; toggles: on. Absent for buttons. */
  value?: number | boolean;
  disabled: boolean;
  /** Canvas CSS pixels, of a canvas `UiTestState.size` (where a scripted mouse click lands on it). */
  x: number;
  y: number;
  width: number;
  height: number;
}

/** What `game.test.state().ui` reports. */
export interface UiTestState {
  /** The open menu (canvas or DOM screen), or null. */
  screen: string | null;
  focus: string | null;
  /** The open menu's buttons, or the open HUDs' buttons when no menu is open. */
  buttons: UiButtonState[];
  /**
   * [width, height]: the canvas size in CSS px that the buttons' x/y are in, as it is when state() runs (a resize
   * the game has not handled yet is laid out first). Scale x/y for a playtest at another size.
   */
  size: [number, number];
  /**
   * Open canvases bottom to top (HUDs, then the menu), plus a DOM screen's name. A closed canvas stays
   * listed while its close animation plays (until it is hidden).
   */
  open: string[];
  /**
   * Canvases of `open` playing their open or close animation (GAME-UI §10.5), not a loop: a screenshot
   * waits until a menu is not listed here to show it fully open. Empty when nothing animates.
   */
  animating: string[];
  /** 'Canvas/Name' → the rendered string of every visible text element of the open canvases. */
  texts: Record<string, string>;
  /**
   * 'Canvas/Name' of the elements of the open canvases that are hidden themselves, sorted: by
   * `ctx.ui.hide` / `show(false)`, or authored hidden (Disable in Renders) and not shown. An element
   * hidden only because its parent is hidden is not listed (its parent is), nor is UiState art, nor an
   * element hidden only by its UiShow or as a toggle's on/off part.
   */
  hidden: string[];
  /** The UI language shown (GAME-UI §11.3): `ctx.ui.locale`. */
  locale: string;
}

/** Which button of which canvas was pressed, or which slider or toggle changed (`ctx.ui.on`). */
export interface UiEventInfo {
  canvas: string;
  /** The button's (slider's, toggle's) element (object) name. */
  button: string;
  /** What fired: a button press, or a slider's or toggle's change. */
  kind?: UiControlKind;
  /** Sliders and toggles: the new value (already written to the value path). */
  value?: number | boolean;
}

export type UiEventHandler = (ctx: SystemContext, info: UiEventInfo) => void;

export interface UiAPI {
  /**
   * Opens a menu, replacing the open one: the Blender canvas `name` when the game has one (only
   * `pause` and `back` apply), else a DOM screen built from the options (see UiScreenOptions).
   */
  screen(name: string, options?: UiScreenOptions): void;
  /** Opens the Blender canvas `name`, a menu or a HUD. Unknown names warn once. */
  open(name: string): void;
  /**
   * Closes the open menu (canvas or DOM screen) and resumes the game if it paused it. With a name,
   * closes that menu or HUD canvas only.
   */
  close(name?: string): void;
  /** Name of the open menu (canvas or DOM screen), or null. */
  readonly current: string | null;
  /** A menu is open; with a name, that menu or HUD canvas is open. */
  isOpen(name?: string): boolean;
  /** Label of the selected button (a canvas button's rendered text, else its name), or null. */
  readonly focused: string | null;
  /**
   * Turns on the pause menu: Escape, P or gamepad Start opens it while playing and closes it again.
   * A canvas with `pauseMenu` is the pause menu; without one, a DOM screen with these options
   * (default buttons Resume and Restart). `false` turns either off.
   */
  pauseMenu(options?: PauseMenuOptions | false): void;
  /** Canvas names the game has (from ui/index.json). */
  readonly canvases: readonly string[];
  /**
   * Called when a button whose event is `event` is pressed: the UiButton `target` for `event`
   * buttons that have one, else the button's object name. One handler per event; registering again
   * replaces it (safe in `start`). Returns an unsubscribe.
   */
  on(event: string, handler: UiEventHandler): () => void;
  /**
   * Overrides a text element ('Name' or 'Canvas/Name'); null restores the authored body and its bindings.
   * Like show() and bar(), it works on a canvas that is not open too (it applies when it opens) and lasts
   * until null, `ctx.restart()`, Stop or a test reset.
   */
  text(path: string, value: string | null): void;
  /** Shows or hides an element (overriding its start visibility). */
  show(path: string, visible?: boolean): void;
  hide(path: string): void;
  /** Sets a UiBar (shown as value / its max), overriding its binding; null goes back to the binding. */
  bar(path: string, value: number | null): void;
  /**
   * Snapshot of an element (screen rectangle in canvas CSS px at the canvas's size now, text, visibility);
   * null when no open or loaded canvas has it.
   */
  element(path: string): UiElementInfo | null;
  /**
   * The UI language (docs/GAME-UI.md §11.3): a code of `locales`. Setting it switches every canvas text
   * to that language's table and saves the choice (`ctx.save` `gs.locale`); an unknown code warns once.
   * At start: the saved language, else the best match of the browser's languages, else the authored one.
   */
  locale: string;
  /** The game's languages, `{ code, name }` (the name in its own language), the authored one first. */
  readonly locales: readonly UiLocale[];
  /** `text` in the current language: its translation in the table, else `text` (for strings set from code). */
  translate(text: string): string;
}

/** What the screens need from the game. */
export interface UiHost {
  input: Input;
  /** 'playing', 'paused', ... */
  state(): string;
  pause(paused: boolean): void;
  ctx(): SystemContext;
  restart(): void;
  /** `ctx.loadScene(name)`: canvas buttons with action `load`. */
  loadScene?(name: string): void;
  /** URL of the exported `ui/index.json`; without one the game has no canvases. */
  indexUrl?: string;
  /**
   * Where value paths (docs/GAME-UI.md §10.1) read and write: canvas text bindings, bars, UiShow, sliders
   * and toggles. 'score' → ctx.store.score, 'save.best' → ctx.save, 'audio.music' → ctx.audio's channel,
   * 'game.time' → ctx.time (read-only).
   */
  values?: ValueSources;
  /** Called once when the game turns out to have canvases: the player of their button sounds (URLs). */
  sounds?(): (url: string) => void;
  /** A `ctx.ui.on` handler threw. */
  error?(error: unknown, context: string): void;
  warn?(message: string): void;
}

interface Open {
  name: string;
  options: UiScreenOptions;
  buttons: UiScreenButton[];
  focus: number;
}

/** What Escape and gamepad B do in an open canvas menu: its `back`, or `ctx.ui.screen`'s. */
type CanvasBack = UiBackAction | UiAction | null;

/** The open canvas menu. */
interface CanvasMenu {
  name: string;
  pause: boolean;
  back: CanvasBack;
}

/** A menu a newer one replaced, which `previous` reopens: a canvas, or a DOM screen with its options. */
type Replaced = { canvas: string; pause: boolean; back: CanvasBack } | { screen: string; options: UiScreenOptions };

const PAUSE_SCREEN = 'pause';
const PREV = ['ArrowUp', 'KeyW', 'ArrowLeft', 'KeyA'];
const NEXT = ['ArrowDown', 'KeyS', 'ArrowRight', 'KeyD', 'Tab'];
const ACTIVATE = ['Enter', 'NumpadEnter', 'Space'];
const BACK = ['Escape'];
const PAD = { activate: 0, back: 1, start: 9, up: 12, down: 13, left: 14, right: 15 };
/** Canvas menus move by direction (spatial navigation, or `order`). */
const DIRECTIONS: ReadonlyArray<[NavStep, string[], number]> = [
  ['up', ['ArrowUp', 'KeyW'], PAD.up],
  ['down', ['ArrowDown', 'KeyS'], PAD.down],
  ['left', ['ArrowLeft', 'KeyA'], PAD.left],
  ['right', ['ArrowRight', 'KeyD'], PAD.right],
];
/** `ctx.ui.screen` options that only DOM screens have. */
const SCREEN_ONLY = ['title', 'subtitle', 'text', 'buttons', 'hint', 'backdrop', 'color', 'focus'] as const;
/** How many replaced menus `previous` can go back through. */
const HISTORY = 16;
/** How many of a frame's early mouse presses and releases a menu opening later in it gets (the latest). */
const EARLY = 16;
const ACCENT = '#ffd400';

const FONT = 'system-ui,-apple-system,"Segoe UI",Roboto,sans-serif';
const BUTTON_CSS =
  'display:block;box-sizing:border-box;border:2px solid rgba(255,255,255,.9);border-radius:999px;' +
  `background:rgba(0,0,0,.4);color:#fff;font-family:${FONT};font-weight:700;` +
  'font-size:clamp(13px,4.4cqmin,22px);line-height:1.2;padding:clamp(6px,1.7cqmin,12px) clamp(16px,5cqmin,36px);' +
  'text-align:center;cursor:pointer;box-shadow:0 2px 10px rgba(0,0,0,.35);white-space:nowrap';

const errorMessage = (error: unknown): string => (error instanceof Error ? error.message : String(error));

export class Ui implements UiAPI {
  /** The DOM screens' overlay. */
  readonly overlay: HTMLDivElement;
  private readonly panel: HTMLDivElement;
  private readonly backdrop: HTMLDivElement;
  /** The open DOM screen. */
  private dom: Open | null = null;
  /** The open canvas menu (never both). */
  private menu: CanvasMenu | null = null;
  private history: Replaced[] = [];
  private buttonEls: HTMLDivElement[] = [];
  private pausedGame = false;
  private pauseOptions: PauseMenuOptions | null = null;
  /** `pauseMenu(false)`: a `pauseMenu` canvas does not open on the pause keys either. */
  private pauseMenuOff = false;
  private padPrev: boolean[] = [];
  private stickPrev = 0;
  private stickPrevX = 0;
  private pressed = -1;
  private readonly queue: number[] = [];
  /** Canvas buttons clicked (or pressed by key), run in update(). */
  private readonly clicks: CanvasClick[] = [];
  /** Keys consumed only to move the focus (W, arrows, ...): given back as held when the screen closes. */
  private readonly navigated = new Set<string>();
  /**
   * This frame's mouse presses and releases that came while no menu was open (client px): a menu that
   * opens later in the frame gets them too (replayPointer). Forgotten when the frame ends.
   */
  private readonly early: Array<{ kind: 'down' | 'up'; x: number; y: number; button: number }> = [];
  private readonly cleanup: Array<() => void> = [];

  /** The game's canvases, once the canvas layer has loaded (never without ui/index.json or a live canvas). */
  private screens: CanvasScreens | null = null;
  private layerLoad: Promise<CanvasScreens | null> | null = null;
  private loading: Promise<void> | null = null;
  private loadState: 'idle' | 'loading' | 'done' = 'idle';
  /** Live link changes still being applied, in order. */
  private liveTail: Promise<void> | null = null;
  /** Canvases to open when the game next plays: a fresh run (`start` and `firstStart`) or a `ctx.restart()` (`start`). */
  private pendingOpen: 'run' | 'restart' | null = 'run';
  private readonly handlers = new Map<string, UiEventHandler>();
  /** Canvases asked to open before their fonts and images were ready: they open once they are. */
  private readonly waiting = new Map<string, { pause?: boolean; back?: CanvasBack }>();
  /** Undo of each `text`/`show`/`bar` override, cleared when the game starts over. */
  private readonly overrides = new Map<string, () => void>();
  private soundPlayer: ((url: string) => void) | null = null;
  /** The slider value a drag last played its sound at. */
  private dragSound: { key: string; value: number } | null = null;
  private hidden = false;
  /** The editor's preview of one canvas (preview()), or null. */
  private previewName: string | null = null;
  private size: [number, number] | null = null;
  private disposed = false;
  private readonly warned = new Set<string>();
  /** The game's languages, their tables and the one shown (docs/GAME-UI.md §11.3). */
  private readonly i18n = new Locales();
  /** The index's languages have loaded (or turned out to be none): a code can be checked against them. */
  private localesKnown = false;
  /** Where value paths read and write: the game's sources, plus `ui.locale` and `ui.localeName`. */
  private readonly values: ValueSources | null;

  constructor(
    private readonly canvas: HTMLCanvasElement,
    private readonly host: UiHost,
  ) {
    const ui = this;
    this.values = host.values
      ? {
          ...host.values,
          ui: {
            get locale() {
              return ui.locale;
            },
            set locale(code: string) {
              ui.locale = code;
            },
            get localeName() {
              return ui.i18n.nameOf(ui.locale);
            },
          },
        }
      : null;
    this.overlay = document.createElement('div');
    this.overlay.className = 'gs-ui';
    this.overlay.style.cssText =
      'position:fixed;z-index:20;display:none;align-items:center;justify-content:center;overflow:hidden;' +
      `container-type:size;font-family:${FONT};font-weight:600;color:#fff;` +
      'text-shadow:0 1px 3px rgba(0,0,0,.8);user-select:none;-webkit-user-select:none;pointer-events:auto';
    this.backdrop = document.createElement('div');
    this.backdrop.style.cssText = 'position:absolute;left:0;top:0;right:0;bottom:0';
    this.panel = document.createElement('div');
    this.panel.style.cssText =
      'position:relative;display:flex;flex-direction:column;align-items:center;gap:clamp(4px,1.6cqmin,12px);' +
      'padding:clamp(8px,3cqmin,28px);max-width:calc(100% - 32px);max-height:100%;box-sizing:border-box;text-align:center';
    this.overlay.append(this.backdrop, this.panel);
    document.body.appendChild(this.overlay);
    const on = (type: string, fn: (e: Event) => void): void => {
      window.addEventListener(type, fn);
      this.cleanup.push(() => window.removeEventListener(type, fn));
    };
    on('pointerdown', (e) => this.pointer(e as PointerEvent, 'down'));
    on('pointerup', (e) => this.pointer(e as PointerEvent, 'up'));
    on('pointermove', (e) => this.pointer(e as PointerEvent, 'move'));
    this.layout();
  }

  // --- UiAPI -------------------------------------------------------------------------

  get current(): string | null {
    return this.dom?.name ?? this.menu?.name ?? null;
  }

  get focused(): string | null {
    if (this.menu) return this.screens?.focusedButton()?.label ?? null;
    return this.dom?.buttons[this.dom.focus]?.label ?? null;
  }

  get canvases(): readonly string[] {
    return this.screens?.readyNames() ?? [];
  }

  isOpen(name?: string): boolean {
    if (name === undefined) return this.dom !== null || this.menu !== null;
    return this.dom?.name === name || this.menu?.name === name || !!this.screens?.isOpen(name);
  }

  screen(name: string, options: UiScreenOptions = {}): void {
    if (this.screens?.has(name)) {
      const ignored = SCREEN_ONLY.filter((key) => options[key] !== undefined);
      if (ignored.length > 0) {
        this.warnOnce(`screen:${name}`, `ctx.ui.screen("${name}") opens the Blender canvas "${name}": only pause and back apply (ignored: ${ignored.join(', ')})`);
      }
      this.openCanvas(name, {
        ...(options.pause !== undefined && { pause: options.pause !== false }),
        ...(options.back !== undefined && { back: options.back }),
      });
      return;
    }
    this.showScreen(name, options, false);
  }

  open(name: string): void {
    this.openCanvas(name);
  }

  close(name?: string): void {
    const current = this.current;
    // A canvas still waiting for its fonts does not open after all (with no name: a waiting menu).
    for (const waiting of [...this.waiting.keys()]) {
      if (name === undefined ? this.screens?.settings(waiting)?.kind !== 'hud' : waiting === name) this.waiting.delete(waiting);
    }
    if (name !== undefined && name !== current) {
      this.screens?.hideHud(name); // a HUD canvas (or nothing)
      return;
    }
    if (current === null) return;
    this.hideScreen();
    if (this.menu) {
      this.screens?.hideMenu();
      this.menu = null;
    }
    this.history = [];
    // Movement keys that only moved the focus count as held again (W held through the title walks on).
    for (const code of this.navigated) this.host.input.unconsume?.(code);
    this.navigated.clear();
    this.queue.length = 0;
    this.clicks.length = 0;
    if (this.pausedGame) this.resume();
  }

  pauseMenu(options: PauseMenuOptions | false = {}): void {
    this.pauseOptions = options === false ? null : options;
    this.pauseMenuOff = options === false;
    if (options !== false) return;
    this.close(PAUSE_SCREEN);
    if (this.menu && this.screens?.settings(this.menu.name)?.pauseMenu) this.close();
  }

  on(event: string, handler: UiEventHandler): () => void {
    this.handlers.set(event, handler);
    return () => {
      if (this.handlers.get(event) === handler) this.handlers.delete(event);
    };
  }

  text(path: string, value: string | null): void {
    const ref = this.find(path, 'text');
    if (!ref) return;
    if (ref.element.type !== 'text') {
      this.warnOnce(`text-type:${path}`, `ctx.ui.text("${path}"): "${ref.element.name}" is a ${ref.element.type}, not a text element`);
      return;
    }
    const text = value === null || value === undefined ? null : String(value);
    this.override(ref, 'text', text === null, (layer) => layer.setText(ref.canvas, ref.element.id, null));
    this.screens!.layer.setText(ref.canvas, ref.element.id, text);
  }

  show(path: string, visible = true): void {
    const ref = this.find(path, visible === false ? 'hide' : 'show');
    if (!ref) return;
    this.override(ref, 'visible', false, (layer) => layer.setVisible(ref.canvas, ref.element.id, null));
    this.screens!.layer.setVisible(ref.canvas, ref.element.id, visible !== false);
  }

  hide(path: string): void {
    this.show(path, false);
  }

  bar(path: string, value: number | null): void {
    const ref = this.find(path, 'bar');
    if (!ref) return;
    if (!ref.element.components.UiBar) {
      this.warnOnce(`bar-type:${path}`, `ctx.ui.bar("${path}"): "${ref.element.name}" has no UiBar component`);
      return;
    }
    const restore = value === null || value === undefined || !Number.isFinite(value);
    this.override(ref, 'bar', restore, (layer) => layer.setBar(ref.canvas, ref.element.id, null));
    this.screens!.layer.setBar(ref.canvas, ref.element.id, restore ? null : value);
  }

  element(path: string): UiElementInfo | null {
    this.fitCanvas();
    const screens = this.screens;
    const ref = screens?.layer.find(path, this.preferred()) ?? null;
    return ref ? screens!.layer.info(ref.canvas, ref.element.id) : null;
  }

  get locale(): string {
    return this.i18n.current ?? this.chooseLocale();
  }

  set locale(code: string) {
    if (!this.setLocale(code)) {
      this.warnOnce(`locale:${String(code)}`, `ctx.ui.locale = "${String(code)}": the game has no such language (languages: ${this.i18n.codes().join(', ')})`);
    }
  }

  get locales(): readonly UiLocale[] {
    return this.i18n.list();
  }

  translate(text: string): string {
    if (this.i18n.current === null) this.chooseLocale();
    return this.i18n.translate(text);
  }

  // --- Game hooks ---------------------------------------------------------------------

  /** True while an open menu holds the game paused. */
  get holdsPause(): boolean {
    return this.pausedGame;
  }

  /**
   * Loads the game's canvases once: `ui/index.json`, then every canvas it lists with its fonts and
   * images (each at most about a second). Without an index nothing else loads, the canvas code
   * included. Never rejects.
   */
  load(): Promise<void> {
    if (!this.loading) {
      this.loadState = 'loading';
      this.loading = this.loadCanvases()
        .catch((error) => this.warn(`Game UI: ${errorMessage(error)}`))
        .finally(() => {
          if (!this.localesKnown) this.applyLocales(null, null, {}); // no index: only the authored language
          this.loadState = 'done';
        });
    }
    return this.loading;
  }

  /**
   * Fetches `ui/index.json` again and applies what was exported since the page loaded (docs/GAME-UI.md
   * §5.1): canvases whose `rev` changed or that are new, and drops the ones no longer listed. A game page
   * kept warm between plays calls it on Play and on a scene reload. It holds nothing up while nothing
   * changed; once a change is found, the canvases a start opens wait for it. Never rejects.
   */
  refresh(): Promise<void> {
    if (this.disposed) return Promise.resolve();
    if (this.loadState !== 'done') return this.load(); // the first load gets everything anyway
    this.refreshing ??= this.refreshCanvases(this.pendingOpen)
      .catch((error) => this.warn(`Game UI: ${errorMessage(error)}`))
      .finally(() => {
        this.refreshing = null;
      });
    return this.refreshing;
  }

  private refreshing: Promise<void> | null = null;

  private async refreshCanvases(run: 'run' | 'restart' | null): Promise<void> {
    const url = this.indexUrl;
    if (!url) return;
    const warn = (message: string): void => this.warn(message);
    const index = await fetchUiIndex(url, warn);
    if (!index || this.disposed) return; // no index now: keep what the page has
    // Translation files carry no revision: they are fetched again, and apply when they changed.
    const locales = this.loadLocales(index, url);
    const before = this.screens;
    const known = new Set(before?.names() ?? []);
    const changed = Object.entries(index.canvases).filter(
      ([name, entry]) => typeof entry?.file === 'string' && (!known.has(name) || before?.layer.data(name)?.rev !== entry.rev),
    );
    const gone = [...known].filter((name) => !Object.hasOwn(index.canvases, name));
    if (changed.length === 0 && gone.length === 0) return locales;
    this.loadState = 'loading'; // a start that is due opens its canvases once these are in
    try {
      for (const name of gone) await this.removeCanvas(name);
      if (changed.length === 0) return;
      const screens = before ?? (await this.ensureLayer());
      if (!screens || this.disposed) return;
      const files = await Promise.all(
        changed.map(async ([name, entry]) => {
          const fileUrl = new URL(entry.file, url).href;
          const data = await fetchCanvas(fileUrl, name, warn);
          return data && { data, fileUrl };
        }),
      );
      await Promise.all(files.map((file) => file && this.applyCanvas(screens, file.data, file.fileUrl)));
      // New canvases due at the start that already opened the others (it ran before they arrived).
      if (run && this.pendingOpen === null && this.host.state() === 'playing') {
        for (const file of files) {
          if (!file || known.has(file.data.canvas)) continue;
          const settings = screens.settings(file.data.canvas);
          if (!settings || !(settings.open === 'start' || (run === 'run' && settings.open === 'firstStart'))) continue;
          if (settings.kind === 'hud' || this.current === null) this.openCanvas(file.data.canvas);
        }
      }
    } finally {
      await locales;
      this.loadState = 'done';
    }
  }

  /** Runs `fn` once the canvases have loaded and earlier live changes are in: at once when nothing is pending. */
  afterLoad(fn: () => void | Promise<unknown>): Promise<void> {
    if (!this.liveTail && this.loadState === 'done') {
      try {
        const result = fn();
        return result ? this.chain(result) : Promise.resolve();
      } catch (error) {
        this.warn(`Game UI: ${errorMessage(error)}`);
        return Promise.resolve();
      }
    }
    return this.chain((this.liveTail ?? this.load()).then(fn));
  }

  /** Adds or replaces a canvas (the live link's `ui.canvas`): an open one stays open, keeping its focused button. */
  setCanvas(data: unknown, fileUrl?: string): Promise<void> {
    if (!isCanvasData(data)) {
      this.warn('Game UI: ui.canvas without canvas data ("format": "gamestudio.ui")');
      return Promise.resolve();
    }
    return this.afterLoad(() => {
      if (this.screens) void this.applyCanvas(this.screens, data, fileUrl);
      else return this.ensureLayer().then((screens) => void (screens && this.applyCanvas(screens, data, fileUrl)));
    });
  }

  /**
   * Replaces the game's languages and their tables (the live link's `ui.locales`: the index's
   * `sourceLocale` and `locales` with the tables inline, `tables: {code: {name, strings}}`).
   */
  setLocales(message: { sourceLocale?: unknown; locales?: unknown; tables?: unknown }): Promise<void> {
    const raw = message?.tables;
    if (!raw || typeof raw !== 'object' || Array.isArray(raw)) {
      this.warn('Game UI: ui.locales without tables ({"tables": {"<code>": {"name": ..., "strings": {...}}}})');
      return Promise.resolve();
    }
    // The index's order, then any other table sent.
    const listed = message.locales && typeof message.locales === 'object' ? Object.keys(message.locales) : [];
    const tables: Record<string, UiLocaleData> = {};
    for (const code of [...listed, ...Object.keys(raw)]) {
      if (code === '__proto__' || Object.hasOwn(tables, code)) continue;
      const table = localeData((raw as Record<string, unknown>)[code]);
      if (table) tables[code] = table;
    }
    return this.afterLoad(() => this.applyLocales(message.sourceLocale, message.locales, tables));
  }

  /**
   * `ctx.save` changed under the game (a playtest's own store, or keys a test seeds): the language is
   * chosen again from it, as at start. The game calls it.
   */
  reloadLocale(): void {
    this.chooseLocale();
  }

  /** Closes a canvas and forgets it (the live link's `ui.remove`). */
  removeCanvas(name: string): Promise<void> {
    return this.afterLoad(() => {
      const screens = this.screens;
      if (!screens?.has(name)) return;
      if (this.menu?.name === name) this.close();
      this.waiting.delete(name);
      screens.removed(name);
      this.history = this.history.filter((entry) => !('canvas' in entry && entry.canvas === name));
      screens.layer.remove(name);
    });
  }

  /** Reads this frame's input: navigation, activation, back, and the pause key. Once per frame. */
  update(): void {
    const input = this.host.input;
    const pad = input.gamepad;
    const padDown = (i: number): boolean => !!pad?.buttons[i]?.pressed && !this.padPrev[i];
    const stick = pad ? (pad.axes[1] ?? 0) : 0;
    const stickX = pad ? (pad.axes[0] ?? 0) : 0;
    const stickStep = Math.abs(stick) > 0.5 && Math.abs(this.stickPrev) <= 0.5 ? Math.sign(stick) : 0;
    const stickStepX = Math.abs(stickX) > 0.5 && Math.abs(this.stickPrevX) <= 0.5 ? Math.sign(stickX) : 0;
    this.stickPrev = stick;
    this.stickPrevX = stickX;
    const key = (codes: readonly string[]): string | null => codes.find((c) => input.keyDown(c)) ?? null;
    const pauseKeys = this.pauseOptions?.keys ?? ['Escape', 'KeyP'];
    const consume = (code: string | null): void => {
      if (code) input.consume(code);
    };

    try {
      // A pausing menu opened while the game was loading or stopped pauses it once it plays.
      const pausing = this.dom ? this.dom.options.pause !== false : !!this.menu?.pause;
      if (pausing && !this.pausedGame && this.host.state() === 'playing') {
        this.host.pause(true);
        this.pausedGame = true;
      }
      if (!this.dom && !this.menu) {
        this.runDrag(); // HUD sliders
        this.runClicks(); // HUD buttons
        const pauseCanvas = this.pauseCanvas();
        if ((!this.pauseOptions && !pauseCanvas) || this.host.state() !== 'playing') return;
        const pressed = key(pauseKeys);
        if (pressed || padDown(PAD.start)) {
          consume(pressed);
          if (padDown(PAD.start)) input.consumeGamepad(PAD.start);
          if (pauseCanvas) this.openPauseCanvas(pauseCanvas);
          else this.openPauseMenu();
        }
        return;
      }

      if (this.menu) {
        const menu = this.menu;
        if (this.isPauseCanvas(menu.name)) {
          const pressed = key(pauseKeys);
          if (pressed || padDown(PAD.start)) {
            consume(pressed);
            if (!pressed) input.consumeGamepad(PAD.start);
            this.close();
            return;
          }
        }
        let step: NavStep | null = null;
        let code: string | null = null;
        for (const [direction, codes, button] of DIRECTIONS) {
          code = key(codes);
          if (code || padDown(button)) {
            step = direction;
            break;
          }
        }
        if (!step) {
          if (stickStep) step = stickStep < 0 ? 'up' : 'down';
          else if (stickStepX) step = stickStepX < 0 ? 'left' : 'right';
          else if (input.keyDown('Tab')) {
            code = 'Tab';
            step = input.key('ShiftLeft') || input.key('ShiftRight') ? 'prev' : 'next';
          }
        }
        if (step) {
          // The arrows along a focused slider's axis change its value; other keys move the focus.
          const change = this.screens?.adjust(step) ?? null;
          if (change) this.setControl(change);
          else this.screens?.move(step);
          if (code) this.navigate(code);
        }
        const activate = key(ACTIVATE);
        if (activate || padDown(PAD.activate)) {
          consume(activate);
          if (!activate) input.consumeGamepad(PAD.activate);
          const click = this.screens?.pressFocused();
          if (click) this.clicks.push(click);
        }
        const back = key(BACK);
        if ((back || padDown(PAD.back)) && menu.back && menu.back !== 'none') {
          consume(back);
          this.runBack(menu.back);
          return;
        }
        this.runDrag();
        this.runClicks();
        return;
      }

      const open = this.dom!;
      if (open.name === PAUSE_SCREEN && this.pauseOptions) {
        const pressed = key(pauseKeys);
        if (pressed || padDown(PAD.start)) {
          consume(pressed);
          this.close();
          return;
        }
      }
      const prev = key(PREV) ?? (padDown(PAD.up) || padDown(PAD.left) ? 'pad' : null);
      const next = key(NEXT) ?? (padDown(PAD.down) || padDown(PAD.right) ? 'pad' : null);
      if (prev || stickStep < 0) {
        this.move(-1);
        if (prev && prev !== 'pad') this.navigate(prev);
      } else if (next || stickStep > 0) {
        this.move(input.key('ShiftLeft') && next === 'Tab' ? -1 : 1);
        if (next && next !== 'pad') this.navigate(next);
      }
      const activate = key(ACTIVATE);
      if (activate || padDown(PAD.activate)) {
        consume(activate);
        if (!activate) input.consumeGamepad(PAD.activate);
        this.queue.push(open.focus);
      }
      const back = key(BACK);
      if ((back || padDown(PAD.back)) && open.options.back) {
        consume(back);
        this.run(open.options.back);
        return;
      }
      while (this.queue.length > 0 && this.dom === open) {
        const index = this.queue.shift()!;
        const button = open.buttons[index];
        if (button && !button.disabled) this.run(button.action ?? 'close');
      }
    } finally {
      this.padPrev = pad ? pad.buttons.map((b) => b.pressed) : [];
    }
  }

  /**
   * Once per frame after the simulation, in the game view (also under a test's clock): canvases get
   * their open state and button states, then evaluate bindings, ease bars and refresh text and the
   * accessibility mirror.
   */
  frame(dt: number): void {
    this.screens?.frame(dt);
  }

  /** Draws the open canvases over the frame (`raw`: the three.js renderer; nothing is drawn without one). */
  render(raw: unknown): void {
    this.screens?.render(raw);
  }

  /** A canvas menu is drawn (open or closing): the DOM HUD's labels, which sit above the canvas, hide meanwhile. */
  get canvasMenuDrawn(): boolean {
    return !this.hidden && !!this.screens?.menuDrawn();
  }

  /** Hides the canvases (drawing and mirror), as in the Scene view. */
  setHidden(hidden: boolean): void {
    this.hidden = hidden;
    this.screens?.layer.setHidden(hidden);
  }

  /**
   * The editor's preview of a canvas (the game's 'ui' view, docs/GAME-UI.md §11.2): only `name` is drawn,
   * as it opens in the game but at rest (its open animation skipped, a loop running), and bindings without
   * a value show as written (`{score}`). Nothing opens, pauses or takes input for it. A canvas that has not
   * loaded shows once it does. Null ends it.
   */
  preview(name: string | null): void {
    this.previewName = name;
    this.screens?.preview(name);
  }

  /** Buttons with their canvas positions, open canvases, their texts and hidden elements, for tests and playtest reports. */
  state(): UiTestState {
    this.fitCanvas(); // size and button x/y in the canvas's size now, even before the game saw a resize
    const rect = this.canvas.getBoundingClientRect();
    const open = this.dom;
    const screens = this.screens;
    // Open canvases, and closed ones still playing their close animation (GAME-UI §10.5).
    const canvases = screens?.drawnNames() ?? [];
    const buttons: UiButtonState[] = open
      ? open.buttons.map((b, i) => {
          const r = this.buttonEls[i]?.getBoundingClientRect();
          return {
            label: b.label,
            kind: 'button' as const,
            disabled: !!b.disabled,
            x: r ? Math.round(r.left - rect.left + r.width / 2) : 0,
            y: r ? Math.round(r.top - rect.top + r.height / 2) : 0,
            width: r ? Math.round(r.width) : 0,
            height: r ? Math.round(r.height) : 0,
          };
        })
      : (screens?.listedButtons() ?? []).map((b) => ({
          label: b.label,
          name: b.name,
          canvas: b.canvas,
          kind: b.kind ?? 'button',
          ...(b.value !== undefined && { value: b.value }),
          disabled: b.disabled,
          x: Math.round(b.rect.x + b.rect.width / 2),
          y: Math.round(b.rect.y + b.rect.height / 2),
          width: Math.round(b.rect.width),
          height: Math.round(b.rect.height),
        }));
    const [width, height] = this.size ?? [rect.width, rect.height];
    return {
      screen: this.current,
      focus: this.focused,
      buttons,
      size: [Math.round(width), Math.round(height)],
      open: open ? [...canvases, open.name] : canvases,
      animating: screens && canvases.length > 0 ? screens.animatingNames() : [],
      texts: screens && canvases.length > 0 ? screens.layer.texts(canvases) : {},
      hidden: screens && canvases.length > 0 ? screens.layer.hiddenElements(canvases) : [],
      locale: this.locale,
    };
  }

  /**
   * The game starts over: forgets the pause it holds without resuming, closes every menu and canvas,
   * drops `text`/`show`/`bar` overrides, and opens the `start` canvases when it next plays (and the
   * `firstStart` ones too for a fresh `run`: Stop or a test reset, not `ctx.restart()`).
   */
  reset(reason: 'run' | 'restart' = 'run'): void {
    this.pausedGame = false;
    this.close();
    this.screens?.hideAll();
    this.clicks.length = 0;
    this.waiting.clear();
    for (const undo of this.overrides.values()) undo();
    this.overrides.clear();
    this.pendingOpen = reason;
  }

  /** Keeps the overlay over the canvas and lays the canvases out for its size (CSS px). Called on resize. */
  layout(width?: number, height?: number): void {
    const r = this.canvas.getBoundingClientRect();
    Object.assign(this.overlay.style, { left: `${r.left}px`, top: `${r.top}px`, width: `${r.width}px`, height: `${r.height}px` });
    if (width === undefined || height === undefined) return;
    this.size = [width, height];
    this.screens?.layer.layout(width, height);
  }

  /**
   * Lays out again when the canvas's client size differs from the last layout: a resize the game has not
   * seen yet (its observer runs before the next frame), such as a page set to another size and back for a
   * screenshot. What tools read (state(), element(), hit tests) is then in the canvas's size now.
   */
  private fitCanvas(): void {
    const width = Number(this.canvas.clientWidth) || 0;
    const height = Number(this.canvas.clientHeight) || 0;
    if (width <= 0 || height <= 0) return; // no layout box (hidden): the last layout stays
    if (this.size?.[0] === width && this.size[1] === height) return;
    this.layout(width, height);
  }

  dispose(): void {
    this.disposed = true;
    for (const fn of this.cleanup.splice(0)) fn();
    this.overlay.remove();
    this.screens?.layer.dispose();
    this.screens = null;
  }

  // --- Internals ------------------------------------------------------------------------

  private get indexUrl(): string | null {
    const url = this.host.indexUrl;
    if (!url) return null;
    try {
      return new URL(url, typeof location === 'undefined' ? undefined : location.href).href;
    } catch {
      return url;
    }
  }

  private async loadCanvases(): Promise<void> {
    const url = this.indexUrl;
    if (!url) return;
    const warn = (message: string): void => this.warn(message);
    const index = await fetchUiIndex(url, warn);
    // The translations load beside the canvases and are in before their texts are first laid out.
    const locales = index ? this.loadLocales(index, url) : null;
    const entries = index ? Object.entries(index.canvases) : [];
    if (entries.length === 0) {
      await locales; // no Blender UI: the canvas code is never downloaded
      return;
    }
    const [screens, files] = await Promise.all([
      this.ensureLayer(),
      Promise.all(
        entries.map(async ([name, entry]) => {
          if (typeof entry?.file !== 'string') {
            warn(`Game UI: ui/index.json lists "${name}" without a file`);
            return null;
          }
          const fileUrl = new URL(entry.file, url).href;
          const data = await fetchCanvas(fileUrl, name, warn);
          return data && { data, fileUrl };
        }),
      ),
      locales,
    ]);
    if (!screens) return;
    // Each resolves when its fonts and images are ready (or after about a second): a title opens in its own fonts.
    await Promise.all(files.map((file) => file && this.applyCanvas(screens, file.data, file.fileUrl)));
  }

  /** Imports the canvas layer's chunk and creates the layer, once. */
  private ensureLayer(): Promise<CanvasScreens | null> {
    this.layerLoad ??= importCanvasLayer().then(
      ({ createCanvasLayer }) => {
        if (this.disposed) return null;
        const base = this.indexUrl ?? (typeof location === 'undefined' ? '' : location.href);
        const layer = createCanvasLayer({
          canvas: this.canvas,
          assetUrl: (data, path, fileUrl) => canvasAssetUrl(data, path, fileUrl ?? base),
          value: (path) => readValue(path, this.values),
          warn: (message) => this.warn(message),
          translate: (text) => this.translate(text),
          localeVersion: () => this.i18n.version,
          placeholders: () => this.previewName !== null,
        });
        layer.setHidden(this.hidden);
        if (this.size) layer.layout(...this.size);
        this.soundPlayer = this.host.sounds?.() ?? null;
        this.screens = new CanvasScreens(layer, (canvas, path) => this.playSound(canvas, path));
        this.screens.preview(this.previewName);
        return this.screens;
      },
      (error) => {
        this.warn(`Game UI: could not load the canvas renderer (${errorMessage(error)})`);
        return null;
      },
    );
    return this.layerLoad;
  }

  /** Sets a canvas on the layer; an open one whose kind changed (menu ↔ HUD) stays open in its new role. */
  private applyCanvas(screens: CanvasScreens, data: UiCanvasData, fileUrl?: string): Promise<void> {
    const name = data.canvas;
    const before = screens.settings(name)?.kind ?? null;
    const ready = screens.layer
      .set(data, fileUrl)
      .catch((error) => this.warn(`Game UI: canvas "${name}": ${errorMessage(error)}`))
      .then(() => {
        if (this.disposed || !screens.has(name)) return;
        screens.markReady(name);
        const waiting = this.waiting.get(name);
        if (!waiting) return;
        this.waiting.delete(name);
        this.openCanvas(name, waiting);
      });
    screens.replaced(name);
    const kind = data.settings.kind === 'hud' ? 'hud' : 'menu';
    if (before && before !== kind && screens.isOpen(name)) {
      if (kind === 'hud') {
        this.close();
        screens.showHud(name);
      } else {
        screens.hideHud(name);
        this.openCanvas(name);
      }
    }
    return ready;
  }

  private chain(work: Promise<unknown>): Promise<void> {
    const tail: Promise<void> = work
      .then(() => {}, (error) => this.warn(`Game UI: ${errorMessage(error)}`))
      .finally(() => {
        if (this.liveTail === tail) this.liveTail = null;
      });
    this.liveTail = tail;
    return tail;
  }

  /**
   * After a simulation step: the canvases due at this start open now, once the systems' start() ran,
   * so their bindings show the values start() set (a HUD's score under the title), as a title opened
   * from start() does. The game calls update() again when this opened a menu, so a key pressed on
   * this step still reaches it.
   */
  afterSimulate(): void {
    // The game played this step; a title a system opened in start() may have paused it since. HUDs open
    // anyway, and a start menu only when no menu is open (openOnStart).
    if (this.pendingOpen) this.openOnStart();
  }

  /**
   * A menu opened during this frame's simulation (a start canvas, or a title shown from start()): the
   * mouse presses and releases that came earlier in the frame, while no menu was open, are hit-tested
   * again against it. A click on the step that opens the title (a playtest's click at 0 s) presses
   * its button, as a key on that step does. A press still held becomes the menu's, hidden from
   * gameplay until released. The game calls it before running update() again.
   */
  replayPointer(): void {
    const early = this.early.splice(0);
    if (this.current === null) return;
    early.forEach((e, i) => {
      const held = e.kind === 'down' && !early.slice(i + 1).some((later) => later.kind === 'up' && later.button === e.button);
      this.handlePointer(e.kind, e.x, e.y, e.button, held);
    });
  }

  /** The frame is over: its early mouse presses no longer reach a menu that opens. Once per frame, in either view. */
  endFrame(): void {
    this.early.length = 0;
  }

  /** Opens the canvases whose `open` policy applies to this start (the game is playing). */
  private openOnStart(): void {
    if (this.loadState === 'loading') return; // they open once loaded
    const run = this.pendingOpen === 'run';
    this.pendingOpen = null;
    const screens = this.screens;
    if (!screens) return;
    let menu: string | null = null;
    let menuLayer = -Infinity;
    for (const name of screens.names()) {
      const settings = screens.settings(name)!;
      if (settings.open !== 'start' && !(run && settings.open === 'firstStart')) continue;
      if (settings.kind === 'hud') this.openCanvas(name);
      else if (settings.layer > menuLayer) {
        menu = name;
        menuLayer = settings.layer;
      }
    }
    if (menu && this.current === null) this.openCanvas(menu);
  }

  /** Opens a canvas: a HUD joins the open ones; a menu replaces the open menu (remembered for `previous`). */
  private openCanvas(name: string, override: { pause?: boolean; back?: CanvasBack } = {}, fromHistory = false): void {
    const screens = this.screens;
    const settings = screens?.settings(name);
    if (!screens || !settings) {
      const known = screens ? ` (canvases: ${screens.names().join(', ') || 'none'})` : '; the game has no Blender UI (ui/index.json)';
      this.warnOnce(`open:${name}`, `ctx.ui.open("${name}"): no canvas named "${name}"${known}`);
      return;
    }
    if (!screens.isReady(name)) {
      this.waiting.set(name, override); // opens once its fonts and images are ready (about a second at most)
      return;
    }
    this.waiting.delete(name);
    if (settings.kind === 'hud') {
      screens.showHud(name);
      return;
    }
    if (!fromHistory) this.remember(name);
    this.hideScreen();
    this.menu = {
      name,
      pause: override.pause ?? settings.pause !== false,
      back: override.back !== undefined ? override.back : settings.back,
    };
    if (screens.menu !== name) screens.showMenu(name);
    this.queue.length = 0;
    this.clicks.length = 0;
    this.applyPause(this.menu.pause);
  }

  /** The pause menu canvas (Escape, P, Start): it pauses, and B resumes unless it says otherwise. */
  private openPauseCanvas(name: string): void {
    const back = this.screens?.settings(name)?.back ?? 'none';
    this.openCanvas(name, { pause: true, back: back === 'none' ? 'resume' : back });
  }

  private pauseCanvas(): string | null {
    return this.pauseMenuOff ? null : (this.screens?.pauseMenu() ?? null);
  }

  private isPauseCanvas(name: string): boolean {
    return !this.pauseMenuOff && !!this.screens?.settings(name)?.pauseMenu;
  }

  /** The open menu, before `next` replaces it. */
  private remember(next: string): void {
    if (this.dom && this.dom.name !== next) this.history.push({ screen: this.dom.name, options: this.dom.options });
    else if (this.menu && this.menu.name !== next) this.history.push({ canvas: this.menu.name, pause: this.menu.pause, back: this.menu.back });
    if (this.history.length > HISTORY) this.history.shift();
  }

  /** Reopens the menu the open one replaced; with none, closes it. */
  private previous(): void {
    for (let entry = this.history.pop(); entry; entry = this.history.pop()) {
      if ('screen' in entry) {
        this.showScreen(entry.screen, entry.options, true);
        return;
      }
      if (this.screens?.has(entry.canvas)) {
        this.openCanvas(entry.canvas, { pause: entry.pause, back: entry.back }, true);
        return;
      }
    }
    this.close();
  }

  private showScreen(name: string, options: UiScreenOptions, fromHistory: boolean): void {
    if (!fromHistory) this.remember(name);
    if (this.menu) {
      this.screens?.hideMenu();
      this.menu = null;
    }
    const buttons = (options.buttons ?? []).filter((b) => b && typeof b.label === 'string');
    const first = Math.min(Math.max(0, options.focus ?? 0), Math.max(0, buttons.length - 1));
    this.dom = { name, options, buttons, focus: this.enabledFrom(buttons, first, 1) };
    this.queue.length = 0;
    this.clicks.length = 0;
    this.pressed = -1;
    this.draw();
    this.applyPause(options.pause !== false);
  }

  /** Takes the DOM screen away without resuming (another menu replaces it). */
  private hideScreen(): void {
    if (!this.dom) return;
    this.dom = null;
    this.buttonEls = [];
    this.overlay.style.display = 'none';
  }

  private applyPause(pause: boolean): void {
    if (pause && !this.pausedGame && this.host.state() === 'playing') {
      this.host.pause(true);
      this.pausedGame = true;
    } else if (!pause && this.pausedGame) this.resume();
  }

  private openPauseMenu(): void {
    const o = this.pauseOptions ?? {};
    this.screen(PAUSE_SCREEN, {
      title: 'Paused',
      ...o,
      buttons: o.buttons ?? [
        { label: 'Resume', action: 'resume' },
        { label: 'Restart', action: 'restart' },
      ],
      back: 'resume',
      pause: true,
    });
  }

  private resume(): void {
    this.pausedGame = false;
    if (this.host.state() === 'paused') this.host.pause(false);
  }

  private run(action: UiAction): void {
    if (action === 'close' || action === 'resume') this.close();
    else if (action === 'restart') {
      this.close();
      this.host.restart();
    } else if (typeof action === 'function') action(this.host.ctx());
  }

  private runBack(back: CanvasBack): void {
    if (back === 'previous') this.previous();
    else if (back && back !== 'none') this.run(back);
  }

  /** Runs the canvas buttons clicked or pressed since the last frame that are still there. */
  private runClicks(): void {
    const screens = this.screens;
    while (screens && this.clicks.length > 0) {
      const click = this.clicks.shift()!;
      const button = screens.button(click.canvas, click.id);
      if (button) this.press(click.canvas, button);
    }
  }

  /**
   * The value a held slider's pointer gives it this frame (GAME-UI §10.4). Its sound plays on each step,
   * and for a smooth slider (step 0) each tenth of the range, not on every frame of a drag.
   */
  private runDrag(): void {
    const change = this.screens?.takeDrag();
    if (!change || change.value === change.button.value) return;
    const slider = change.button.slider!;
    const key = `${change.canvas}/${change.button.id}`;
    const unit = slider.step > 0 ? slider.step : Math.abs(slider.max - slider.min) / 10;
    const last = this.dragSound?.key === key ? this.dragSound.value : null;
    const sound = last === null || Math.abs((change.value as number) - last) >= unit - 1e-9;
    if (sound) this.dragSound = { key, value: change.value as number };
    this.setControl(change, sound);
  }

  /**
   * A slider or toggle takes a new value: written to its value path (§10.1; a path that can't be written
   * warns once), then its sound and the `ctx.ui.on` handler of its element name. Nothing when unchanged.
   */
  private setControl(change: ControlChange, sound = true): void {
    const { canvas, button, value } = change;
    if (value === button.value) return;
    const path = button.path ?? '';
    if (!writeValue(path, value, this.values)) {
      const what = `${button.kind} "${canvas}/${button.name}"`;
      this.warnOnce(
        `write:${canvas}/${button.name}`,
        !path
          ? `Game UI: ${what} has no value path: set its value (e.g. audio.music, save.difficulty or a ctx.store name)`
          : isReadOnly(path)
            ? `Game UI: ${what} can't write "${path}": game.time, game.level and ui.localeName are read-only (bind a ctx.store name, save.name or audio.*)`
            : `Game UI: ${what} can't write "${path}": sliders and toggles write name or a.b (ctx.store), save.name or audio.master|music|sfx|ui`,
      );
    }
    this.screens?.changed();
    if (sound) this.playSound(canvas, button.sound);
    this.emit(canvas, button, value);
  }

  /** A canvas button was pressed: its sound, its built-in action, then the `ctx.ui.on` handler of its event. A toggle flips. */
  private press(canvas: string, button: CanvasButton): void {
    if (button.kind === 'toggle') {
      this.setControl({ canvas, button, value: !button.value });
      return;
    }
    if (button.kind === 'slider') return; // keys and drags change it
    this.playSound(canvas, button.sound);
    const isMenu = this.menu?.name === canvas;
    const target = button.target;
    switch (button.action) {
      case 'close':
      case 'resume':
        this.close(canvas);
        break;
      case 'open':
        if (target) this.openCanvas(target);
        else this.warnOnce(`target:${canvas}/${button.name}`, `Game UI: button "${canvas}/${button.name}" opens a canvas but has no target`);
        break;
      case 'previous':
        if (isMenu) this.previous();
        break;
      case 'restart':
        if (isMenu) this.close();
        this.host.restart();
        break;
      case 'load':
        if (!target) {
          this.warnOnce(`target:${canvas}/${button.name}`, `Game UI: button "${canvas}/${button.name}" loads a level but has no target`);
          break;
        }
        if (isMenu) this.close();
        this.host.loadScene?.(target);
        break;
      case 'locale':
        this.switchLocale(canvas, button);
        break;
      default:
        break; // event, none: only the handler
    }
    this.emit(canvas, button);
  }

  /** A `locale` button (GAME-UI §11.3): to the language in its target, or with none to the next one, wrapping. */
  private switchLocale(canvas: string, button: CanvasButton): void {
    const target = button.target.trim();
    if (!target) {
      const codes = this.i18n.codes();
      this.setLocale(codes[(codes.indexOf(this.locale) + 1) % codes.length]!);
    } else if (!this.setLocale(target)) {
      this.warnOnce(
        `locale:${canvas}/${button.name}`,
        `Game UI: button "${canvas}/${button.name}" switches to "${target}", a language the game has no table for (languages: ${this.i18n.codes().join(', ')})`,
      );
    }
  }

  /**
   * Switches the UI language to a code of the game's (or a close one: `fr-CA` is `fr`) and saves the
   * choice. Before the index's languages are known any code is taken: the start keeps it when the game
   * has it. False for a language the game doesn't have.
   */
  private setLocale(code: unknown): boolean {
    const wanted = typeof code === 'string' ? code.trim() : '';
    const found = !wanted ? null : this.localesKnown ? this.i18n.find(wanted) : wanted;
    if (!found) return false;
    this.host.values?.save.set(LOCALE_KEY, found);
    this.showLocale(found);
    return true;
  }

  /** The start's language: the saved one when the game has it, else the browser's best match, else the authored one. */
  private chooseLocale(): string {
    let saved: unknown;
    try {
      saved = this.host.values?.save.get(LOCALE_KEY);
    } catch {
      saved = undefined;
    }
    const code = this.i18n.pick(saved, browserLanguages());
    this.showLocale(code);
    return code;
  }

  private showLocale(code: string): void {
    if (this.i18n.use(code)) this.screens?.changed(); // the texts look their bodies up again
  }

  /** Fetches the translation tables the index lists and applies them. Never rejects. */
  private loadLocales(index: UiIndex, url: string): Promise<void> {
    return fetchLocales(index, url, (message) => this.warn(message)).then((tables) => {
      if (!this.disposed) this.applyLocales(index.sourceLocale, index.locales, tables);
    });
  }

  /**
   * The game's languages and their tables (the index's, or a live `ui.locales`): `listed` is the index's
   * `locales` (`{code: {file, name}}`, for the names). The language shown stays while the game has it;
   * on the first load, or when it is gone, the start's choice is made again.
   */
  private applyLocales(source: unknown, listed: unknown, tables: Record<string, UiLocaleData>): void {
    const names: Record<string, string> = {};
    if (listed && typeof listed === 'object') {
      for (const [code, entry] of Object.entries(listed as Record<string, unknown>)) {
        const name = entry && typeof entry === 'object' ? (entry as { name?: unknown }).name : undefined;
        if (typeof name === 'string' && code !== '__proto__') names[code] = name;
      }
    }
    const changed = this.i18n.apply(typeof source === 'string' && source.trim() ? source.trim() : 'en', tables, names);
    const first = !this.localesKnown;
    this.localesKnown = true;
    const current = this.i18n.current;
    if (first || current === null || !this.i18n.codes().includes(current)) this.chooseLocale();
    if (changed) this.screens?.changed();
  }

  /** Calls the `ctx.ui.on` handler of a control's event, with the new value of a slider or toggle. */
  private emit(canvas: string, button: CanvasButton, value?: number | boolean): void {
    const handler = this.handlers.get(button.event);
    if (!handler) return;
    try {
      handler(this.host.ctx(), { canvas, button: button.name, kind: button.kind ?? 'button', ...(value !== undefined && { value }) });
    } catch (error) {
      if (this.host.error) this.host.error(error, `ctx.ui.on("${button.event}")`);
      else console.error(error);
    }
  }

  private playSound(canvas: string, path: string): void {
    const data = path && this.soundPlayer ? this.screens?.layer.data(canvas) : null;
    if (!data) return;
    this.soundPlayer!(canvasAssetUrl(data, path, this.indexUrl ?? (typeof location === 'undefined' ? '' : location.href)));
  }

  /** Open canvases to search first for a 'Name' path: the menu, then the HUDs from the top. */
  private preferred(): string[] {
    return this.screens?.openNames().reverse() ?? [];
  }

  private find(path: string, what: string): CanvasElementRef | null {
    const screens = this.screens;
    const ref = screens?.layer.find(path, this.preferred()) ?? null;
    if (!ref) {
      const why = screens ? '' : ' (the game has no Blender UI: ui/index.json)';
      this.warnOnce(`find:${path}`, `ctx.ui.${what}("${path}"): no canvas element "${path}"${why}`);
    }
    return ref;
  }

  /** Remembers how to undo an override (`restored`: it was undone). */
  private override(ref: CanvasElementRef, kind: string, restored: boolean, undo: (layer: CanvasLayer) => void): void {
    const key = `${ref.canvas}\u0000${ref.element.id}\u0000${kind}`;
    const layer = this.screens!.layer;
    if (restored) this.overrides.delete(key);
    else this.overrides.set(key, () => undo(layer));
  }

  private warn(message: string): void {
    if (this.host.warn) this.host.warn(message);
    else console.warn(`[gamestudio] ${message}`);
  }

  private warnOnce(key: string, message: string): void {
    if (this.warned.has(key)) return;
    this.warned.add(key);
    this.warn(message);
  }

  /** A key that moved the focus: hidden from gameplay while the screen is open, not after. */
  private navigate(code: string): void {
    this.host.input.consume(code);
    this.navigated.add(code);
  }

  private move(step: number): void {
    const open = this.dom;
    if (!open || open.buttons.length === 0) return;
    open.focus = this.enabledFrom(open.buttons, open.focus + step, step);
    this.styleButtons();
  }

  /** The first enabled button from `index` going in `step` direction (wrapping), or `index`. */
  private enabledFrom(buttons: UiScreenButton[], index: number, step: number): number {
    const n = buttons.length;
    if (n === 0) return 0;
    for (let k = 0; k < n; k++) {
      const i = (((index + k * step) % n) + n) % n;
      if (!buttons[i]!.disabled) return i;
    }
    return ((index % n) + n) % n;
  }

  private pointer(event: PointerEvent, kind: 'down' | 'up' | 'move'): void {
    // With no menu open, a menu that opens later this frame gets the press or release too (replayPointer).
    if (kind !== 'move' && this.current === null) {
      if (this.early.length >= EARLY) this.early.shift();
      this.early.push({ kind, x: event.clientX, y: event.clientY, button: event.button });
    }
    this.handlePointer(kind, event.clientX, event.clientY, event.button);
  }

  /** A mouse event at client px. `consume: false`: a press already released, so there is nothing to hide from gameplay. */
  private handlePointer(kind: 'down' | 'up' | 'move', clientX: number, clientY: number, button: number, consume = true): void {
    // While a menu is open every press is the menu's: gameplay never sees it.
    if (kind === 'down' && consume && this.current !== null) this.host.input.consumeMouse?.(button);
    if (this.screens || this.dom) this.fitCanvas(); // hit tests in the canvas's size now
    if (this.screens && !this.dom) {
      const r = this.canvas.getBoundingClientRect();
      const result = this.screens.pointer(kind, clientX - r.left, clientY - r.top, button);
      if (result.consume && consume && kind === 'down' && !this.menu) this.host.input.consumeMouse?.(button); // a HUD button's
      if (result.click) this.clicks.push(result.click);
      return;
    }
    if (!this.dom) return;
    const index = this.buttonEls.findIndex((el) => {
      const r = el.getBoundingClientRect();
      return clientX >= r.left && clientX <= r.left + r.width && clientY >= r.top && clientY <= r.top + r.height;
    });
    if (kind === 'move') {
      if (index >= 0 && !this.dom.buttons[index]!.disabled && index !== this.dom.focus) {
        this.dom.focus = index;
        this.styleButtons();
      }
      return;
    }
    if (button !== 0) return;
    if (kind === 'down') {
      this.pressed = index;
      if (index >= 0 && !this.dom.buttons[index]!.disabled) {
        this.dom.focus = index;
        this.styleButtons();
      }
    } else {
      if (index >= 0 && index === this.pressed) this.queue.push(index);
      this.pressed = -1;
    }
  }

  private draw(): void {
    const open = this.dom!;
    const o = open.options;
    const dim = Math.min(1, Math.max(0, o.backdrop ?? 0.6));
    this.backdrop.style.background =
      `radial-gradient(ellipse at center, rgba(8,10,22,${(dim * 0.75).toFixed(3)}) 0%, rgba(4,5,12,${Math.min(1, dim * 1.3).toFixed(3)}) 100%)`;
    for (const child of [...this.panel.children]) child.remove();
    const text = (content: string | undefined, css: string): void => {
      if (!content) return;
      const el = document.createElement('div');
      el.style.cssText = css;
      el.textContent = content;
      this.panel.appendChild(el);
    };
    text(
      o.title,
      'font-weight:800;font-size:clamp(22px,10cqmin,68px);line-height:1.05;letter-spacing:.01em;white-space:pre-line;' +
        'text-shadow:0 2px 0 rgba(0,0,0,.35),0 4px 18px rgba(0,0,0,.6)',
    );
    text(o.subtitle, 'font-size:clamp(13px,4.4cqmin,24px);font-weight:600;opacity:.92;white-space:pre-line');
    text(o.text, 'font-size:clamp(12px,3.4cqmin,18px);font-weight:500;opacity:.9;white-space:pre-line;max-width:min(560px,92cqw)');
    this.buttonEls = [];
    if (open.buttons.length > 0) {
      const list = document.createElement('div');
      list.style.cssText =
        'display:flex;flex-direction:column;align-items:stretch;gap:clamp(5px,1.8cqmin,12px);' +
        'margin-top:clamp(6px,2.4cqmin,18px);min-width:clamp(150px,40cqmin,300px)';
      open.buttons.forEach((button) => {
        const el = document.createElement('div');
        el.dataset.label = button.label;
        el.textContent = button.label;
        list.appendChild(el);
        this.buttonEls.push(el);
      });
      this.panel.appendChild(list);
    }
    const hint = o.hint === false ? '' : (o.hint ?? (open.buttons.length > 1 ? '↑ ↓ choose  ·  Enter select' : ''));
    text(hint, 'font-size:clamp(10px,2.8cqmin,14px);font-weight:600;opacity:.65;margin-top:clamp(2px,1.2cqmin,10px)');
    this.styleButtons();
    this.overlay.dataset.screen = open.name;
    this.overlay.style.display = 'flex';
  }

  private styleButtons(): void {
    const open = this.dom;
    if (!open) return;
    const accent = open.options.color ?? ACCENT;
    this.buttonEls.forEach((el, i) => {
      const button = open.buttons[i]!;
      const focused = i === open.focus && !button.disabled;
      el.style.cssText =
        BUTTON_CSS +
        (focused
          ? `;background:${accent};border-color:${accent};color:#17171c;text-shadow:none;transform:scale(1.05);` +
            'box-shadow:0 0 0 3px rgba(255,255,255,.28),0 6px 18px rgba(0,0,0,.45)'
          : '') +
        (button.disabled ? ';opacity:.4;cursor:default' : '');
      el.dataset.focused = String(focused);
    });
  }
}
