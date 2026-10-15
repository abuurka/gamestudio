// Keyboard (by KeyboardEvent.code), mouse and gamepad input, sampled per frame.
// Presses are latched: a key that goes down and up between two samples still reads as held for
// one sample. `fixed` systems sample once per fixed step, `update`/`late` once per frame.

export interface MouseState {
  /** CSS pixels from the canvas's top-left corner. */
  x: number;
  y: number;
  /** Normalized device coordinates (-1..1, +y up), handy for Three.js raycasting. */
  nx: number;
  ny: number;
  /** Movement this frame in CSS pixels (works with pointer lock). */
  dx: number;
  dy: number;
  /** Wheel delta this frame (positive = scroll down). */
  wheel: number;
  /** Bitmask of held buttons: 1 = left, 2 = middle, 4 = right (bit = 1 << button). */
  buttons: number;
}

export interface GamepadState {
  id: string;
  /** Standard mapping: 0/1 left stick x/y, 2/3 right stick x/y (+y is down). */
  axes: readonly number[];
  /** Standard mapping: 0 = A/Cross, 1 = B/Circle, 2 = X/Square, 3 = Y/Triangle, 12-15 = d-pad. */
  buttons: readonly { pressed: boolean; value: number }[];
}

export type AxisName = 'horizontal' | 'vertical';

export interface InputAPI {
  /**
   * Held now, or pressed since the last sample (so a quick tap is never missed). `code` is a
   * KeyboardEvent.code such as 'KeyW', 'Space', 'ArrowLeft', 'ShiftLeft'.
   */
  key(code: string): boolean;
  /** Pressed since the last sample: this frame in `update`/`late`, this step in `fixed`. */
  keyDown(code: string): boolean;
  /** Released since the last sample. */
  keyUp(code: string): boolean;
  readonly mouse: Readonly<MouseState>;
  /** Mouse button held, or pressed since the last sample (0 = left, 1 = middle, 2 = right). */
  mouseButton(button: number): boolean;
  mouseButtonDown(button: number): boolean;
  mouseButtonUp(button: number): boolean;
  /** First connected gamepad, or `null`. */
  readonly gamepad: GamepadState | null;
  /** Gamepad button held (standard mapping index). */
  gamepadButton(index: number): boolean;
  /**
   * -1..1. 'horizontal': A/D, Left/Right, left stick X (+1 = right).
   * 'vertical': W/S, Up/Down, left stick Y (+1 = up/forward).
   */
  axis(name: AxisName): number;
}

const DEADZONE = 0.15;
const BLOCK_DEFAULT = new Set(['Space', 'ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight']);

/** Presses and releases since a sample point. */
class Edges {
  readonly down = new Set<string>();
  readonly up = new Set<string>();
  buttonsDown = 0;
  buttonsUp = 0;

  clear(): void {
    this.down.clear();
    this.up.clear();
    this.buttonsDown = 0;
    this.buttonsUp = 0;
  }
}

export class Input implements InputAPI {
  readonly mouse: MouseState = { x: 0, y: 0, nx: 0, ny: 0, dx: 0, dy: 0, wheel: 0, buttons: 0 };
  gamepad: GamepadState | null = null;

  private readonly held = new Set<string>();
  /** Keys a menu used: they read as up until released, so the press does not reach gameplay. */
  private readonly consumed = new Set<string>();
  private readonly consumedButtons = new Set<number>();
  /** Mouse buttons (bitmask) a menu or a HUD button took: hidden until released, the release too. */
  private consumedMouse = 0;
  /** Edges since the last frame (update/late) and since the last fixed step. */
  private readonly frameEdges = new Edges();
  private readonly fixedEdges = new Edges();
  private edges = this.frameEdges;
  private readonly cleanup: Array<() => void> = [];

  constructor(private readonly element: HTMLElement) {
    const on = <K extends keyof WindowEventMap>(
      target: Window | HTMLElement,
      type: K,
      fn: (e: WindowEventMap[K]) => void,
      options?: AddEventListenerOptions,
    ): void => {
      target.addEventListener(type, fn as EventListener, options);
      this.cleanup.push(() => target.removeEventListener(type, fn as EventListener, options));
    };

    on(window, 'keydown', (e) => {
      if (isTyping(e.target)) return;
      if (BLOCK_DEFAULT.has(e.code)) e.preventDefault();
      if (this.consumed.has(e.code)) return;
      if (!this.held.has(e.code)) {
        this.frameEdges.down.add(e.code);
        this.fixedEdges.down.add(e.code);
      }
      this.held.add(e.code);
    });
    on(window, 'keyup', (e) => this.release(e.code));
    on(window, 'blur', () => {
      for (const code of [...this.held]) this.release(code);
      this.consumed.clear();
      this.mouse.buttons = 0;
      this.consumedMouse = 0;
    });
    on(window, 'pointermove', (e) => {
      const rect = this.element.getBoundingClientRect();
      this.mouse.x = e.clientX - rect.left;
      this.mouse.y = e.clientY - rect.top;
      this.mouse.nx = rect.width > 0 ? (this.mouse.x / rect.width) * 2 - 1 : 0;
      this.mouse.ny = rect.height > 0 ? -((this.mouse.y / rect.height) * 2 - 1) : 0;
      this.mouse.dx += e.movementX;
      this.mouse.dy += e.movementY;
    });
    on(this.element, 'pointerdown', (e) => {
      const bit = 1 << e.button;
      this.consumedMouse &= ~bit; // a new press starts as gameplay's
      this.mouse.buttons |= bit;
      this.frameEdges.buttonsDown |= bit;
      this.fixedEdges.buttonsDown |= bit;
    });
    on(window, 'pointerup', (e) => {
      const bit = 1 << e.button;
      if (this.consumedMouse & bit) {
        this.consumedMouse &= ~bit;
        return;
      }
      this.mouse.buttons &= ~bit;
      this.frameEdges.buttonsUp |= bit;
      this.fixedEdges.buttonsUp |= bit;
    });
    on(this.element, 'wheel', (e) => {
      this.mouse.wheel += e.deltaY;
    }, { passive: true });
    on(this.element, 'contextmenu', (e) => e.preventDefault());
  }

  key(code: string): boolean {
    return this.held.has(code) || this.edges.down.has(code);
  }
  keyDown(code: string): boolean {
    return this.edges.down.has(code);
  }
  keyUp(code: string): boolean {
    return this.edges.up.has(code);
  }
  mouseButton(button: number): boolean {
    return ((this.mouse.buttons | this.edges.buttonsDown) & (1 << button)) !== 0;
  }
  mouseButtonDown(button: number): boolean {
    return (this.edges.buttonsDown & (1 << button)) !== 0;
  }
  mouseButtonUp(button: number): boolean {
    return (this.edges.buttonsUp & (1 << button)) !== 0;
  }
  gamepadButton(index: number): boolean {
    return !this.consumedButtons.has(index) && (this.gamepad?.buttons[index]?.pressed ?? false);
  }

  /** Hides a key press from gameplay until the key is released (a menu used it). */
  consume(code: string): void {
    const held = this.held.delete(code);
    this.frameEdges.down.delete(code);
    this.fixedEdges.down.delete(code);
    if (held) this.consumed.add(code);
  }

  /**
   * Gives gameplay back a key a menu consumed, as held (without a new press) if it is still down:
   * a movement key that moved a menu's focus moves the player once the menu closes.
   */
  unconsume(code: string): void {
    if (this.consumed.delete(code)) this.held.add(code);
  }

  /**
   * Hides a mouse button press from gameplay (held state and both edges) until it is released: a
   * menu or a HUD button took it. Call it while handling the press.
   */
  consumeMouse(button: number): void {
    const bit = 1 << button;
    this.mouse.buttons &= ~bit;
    this.frameEdges.buttonsDown &= ~bit;
    this.fixedEdges.buttonsDown &= ~bit;
    this.consumedMouse |= bit;
  }

  /** Hides a held gamepad button from gameplay until it is released. */
  consumeGamepad(index: number): void {
    this.consumedButtons.add(index);
  }

  axis(name: AxisName): number {
    const keys =
      name === 'horizontal'
        ? Number(this.key('KeyD') || this.key('ArrowRight')) - Number(this.key('KeyA') || this.key('ArrowLeft'))
        : Number(this.key('KeyW') || this.key('ArrowUp')) - Number(this.key('KeyS') || this.key('ArrowDown'));
    let stick = 0;
    if (this.gamepad) {
      const raw = name === 'horizontal' ? (this.gamepad.axes[0] ?? 0) : -(this.gamepad.axes[1] ?? 0);
      stick = Math.abs(raw) < DEADZONE ? 0 : raw;
    }
    return Math.max(-1, Math.min(1, keys + stick));
  }

  /** Polls gamepads (unless `pollGamepads` is false: scripted tests). Call at the start of each frame. */
  beginFrame(pollGamepads = true): void {
    const pads = pollGamepads && typeof navigator !== 'undefined' && navigator.getGamepads ? navigator.getGamepads() : [];
    const pad = [...pads].find((p): p is Gamepad => !!p && p.connected);
    this.gamepad = pad
      ? { id: pad.id, axes: [...pad.axes], buttons: pad.buttons.map((b) => ({ pressed: b.pressed, value: b.value })) }
      : null;
    for (const index of [...this.consumedButtons]) if (!this.gamepad?.buttons[index]?.pressed) this.consumedButtons.delete(index);
  }

  /** Clears per-frame state. Call at the end of each frame. */
  endFrame(): void {
    this.frameEdges.clear();
    this.mouse.dx = 0;
    this.mouse.dy = 0;
    this.mouse.wheel = 0;
  }

  /** Fixed systems read the edges since the previous fixed step. */
  beginFixedStep(): void {
    this.edges = this.fixedEdges;
  }

  /** Consumes the fixed-step edges and goes back to the frame's. */
  endFixedStep(): void {
    this.fixedEdges.clear();
    this.edges = this.frameEdges;
  }

  /** Drops edges no fixed step will consume (the game is not advancing). */
  discardFixedEdges(): void {
    this.fixedEdges.clear();
  }

  dispose(): void {
    for (const fn of this.cleanup) fn();
    this.cleanup.length = 0;
  }

  private release(code: string): void {
    if (this.consumed.delete(code)) return;
    this.held.delete(code);
    this.frameEdges.up.add(code);
    this.fixedEdges.up.add(code);
  }
}

function isTyping(target: EventTarget | null): boolean {
  return (
    typeof HTMLElement !== 'undefined' &&
    target instanceof HTMLElement &&
    (target.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(target.tagName))
  );
}
