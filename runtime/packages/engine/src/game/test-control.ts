// Deterministic playtests (`game.test`, `window.__gs.test`): a test takes the game's clock, runs
// whole fixed steps as fast as it can with scripted input, renders only when asked, and gives the
// clock back. Input goes through real DOM events, so systems see exactly what a keyboard gives them.

import { Quaternion, Vector3 } from 'three';
import type { GameState, SceneInfo } from '../context';
import type { Entity } from '../ecs/entity';
import type { World } from '../ecs/world';
import type { RotationInput, Vec3Input } from '../math';
import type { NavStats } from '../nav/nav';
import type { PhysicsAPI } from '../physics/backend';
import { snapCamera } from '../systems/camera-cut';
import type { UiTestState } from '../ui/ui';
import type { EntityState, StateFilter } from './describe';
import { mulberry32 } from './random';
import { FIXED_DT } from './stepper';

/** One scripted input. Times are game time since `begin()`, rounded to the nearest fixed step. */
export interface TestInput {
  /** Seconds since `begin()`. Default 0. */
  at?: number;
  /** Fixed steps since `begin()`, instead of `at`. */
  step?: number;
  /** A KeyboardEvent.code ('KeyW', 'Space', 'ArrowUp', 'ShiftLeft'); single letters and digits work too. */
  key?: string;
  /** For keys: 'press' (the default: down, then up one step later), 'down' or 'up'. */
  action?: 'down' | 'up' | 'press';
  /** Seconds to hold the key or mouse button: down at `at`, up `hold` later (at least one step). */
  hold?: number;
  /** 'move', 'down', 'up', 'click' (down, then up one step later) or 'wheel'. */
  mouse?: 'move' | 'down' | 'up' | 'click' | 'wheel';
  /** Pointer position in CSS pixels from the canvas's top-left corner. Default: where it was (first the centre). */
  x?: number;
  y?: number;
  /** 'move': movement in CSS pixels (moves the pointer too). 'wheel': scroll delta (+dy = down). */
  dx?: number;
  dy?: number;
  /** Default 'left'. */
  button?: 'left' | 'middle' | 'right' | 0 | 1 | 2;
}

/** A DOM event at a fixed step, from `expandTestInputs`. */
export interface TestEvent {
  step: number;
  type: 'keydown' | 'keyup' | 'pointermove' | 'pointerdown' | 'pointerup' | 'wheel';
  code?: string;
  x?: number;
  y?: number;
  dx?: number;
  dy?: number;
  button?: number;
}

export interface TestStatus {
  /** True while a test holds the clock: the real-time loop neither simulates nor renders. */
  holding: boolean;
  /** Fixed steps since `begin()`. */
  step: number;
  /** `step` in seconds. */
  time: number;
  /** The game's `ctx.time`, which restarts when a scene loads. */
  gameTime: number;
  state: GameState;
  scene: SceneInfo;
  /** Scene loads and resets since `begin()` that the game did not ask for (a re-export from Blender, a hot reload). */
  loads: number;
  /** Scene changes the game asked for since `begin()` (`ctx.loadScene`, `ctx.restart`); they repeat with the run. */
  sceneChanges: number;
  /** The open menu (`ctx.ui`: a Blender canvas or a DOM screen), or null. */
  screen: string | null;
  /** Steps during which the game was paused, stopped or loading, so nothing was simulated. */
  idleSteps: number;
  /** Scheduled input events not applied yet. */
  pending: number;
  /** CPU time spent stepping, and spent in `render()`. */
  simulateMs: number;
  renders: number;
  renderMs: number;
}

export interface TrackedEntity {
  name: string;
  id: string | null;
  found: boolean;
  /** World positions (Y-up, metres) when tracking began, at the end, and the extremes per axis. */
  start: [number, number, number] | null;
  final: [number, number, number] | null;
  min: [number, number, number] | null;
  max: [number, number, number] | null;
  /** Metres travelled, summed per step (a teleport counts its jump). */
  path: number;
  /** Seconds since `begin()` when it was destroyed, if it is gone. */
  destroyedAt: number | null;
  /** Final component values. */
  components: Record<string, unknown> | null;
  /** Component fields that changed, keyed 'Component.field'. */
  changes: Record<string, FieldChange>;
}

export interface FieldChange {
  start: unknown;
  final: unknown;
  /** Numbers only. */
  min?: number;
  max?: number;
  /** How many steps changed it, and when (seconds since `begin()`) first and last. */
  count: number;
  first: number;
  last: number;
}

export interface TestBeginOptions {
  /** Rebuild the scene first: menus close, `ctx.store` empties and the systems start again on the first step. */
  reset?: boolean;
  /** Seeds `ctx.random` and Math.random (default 1); `null` keeps Math.random and seeds `ctx.random` freshly. */
  seed?: number | null;
  /** Keys the test's own `ctx.save` starts with (JSON values), e.g. `{ bestTime: 42 }`. */
  save?: Record<string, unknown>;
  /** Keys set in `ctx.store` after the reset, before the first step: systems' start() reads them. */
  store?: Record<string, unknown>;
}

export interface SavedOptions {
  /**
   * Include the engine's own keys (`gs.`): `gs.locale`, the UI language (`ctx.ui.locale`), and `gs.audio`, the
   * channel volumes and mutes. Default false.
   */
  internal?: boolean;
}

export interface PlaceOptions {
  /** World position. */
  position?: Vec3Input;
  /** World rotation (quaternion or Euler radians). */
  rotation?: RotationInput;
  /** Rotation about the up axis in degrees (0 faces +Z), instead of `rotation`. */
  yaw?: number;
}

/** What the Game gives its test controller. */
export interface TestDriver {
  readonly canvas: HTMLCanvasElement;
  readonly world: World;
  readonly physics: PhysicsAPI;
  /** Play state, scene, `ctx.time`, how many scene loads and resets there were so far, and whether a menu holds the pause. */
  info(): { state: GameState; scene: SceneInfo; time: number; loads: number; sceneChanges: number; screen: string | null; uiPaused: boolean };
  setHeld(held: boolean): void;
  /** One 1/60 s frame with exactly one fixed step. False when the game is not playing. */
  step(): boolean;
  render(): void;
  /** A scene load or reset is queued or running. */
  busy(): boolean;
  /** Resolves once pending scene loads and resets are done. */
  idle(): Promise<void>;
  /** Rebuilds the scene from the loaded export and plays it. */
  restart(): Promise<void>;
  play(): void;
  describe(filter?: StateFilter): EntityState[];
  /** Restarts the game's own random stream (`ctx.random`) from a seed; `null` picks a fresh one. */
  seed(seed: number | null): void;
  /** `ctx.save` uses an empty in-memory store (true) or the browser's (false). */
  isolate(on: boolean): void;
  /** Sets keys of `ctx.save` (JSON values), then applies the saved audio settings (`gs.audio`) again. */
  seedSave(values: Record<string, unknown>): void;
  /** Sets keys of `ctx.store`. */
  seedStore(values: Record<string, unknown>): void;
  /** Every key of `ctx.save` now, with its value. */
  saveEntries(): Record<string, unknown>;
  ui(): UiTestState;
  vfx(): { emitters: number; particles: number };
  /** The navmesh: built or not, its size, the NavAgent count (optional). */
  nav?(): NavStats;
}

const MOUSE_BUTTONS: Record<string, number> = { left: 0, middle: 1, right: 2 };

/** Accepts KeyboardEvent.code names and plain letters, digits and a few aliases ('space', 'up', 'shift'). */
export function normalizeKeyCode(key: string): string {
  if (/^[a-z]$/i.test(key)) return `Key${key.toUpperCase()}`;
  if (/^[0-9]$/.test(key)) return `Digit${key}`;
  if (key === ' ') return 'Space';
  const aliases: Record<string, string> = {
    space: 'Space', up: 'ArrowUp', down: 'ArrowDown', left: 'ArrowLeft', right: 'ArrowRight', shift: 'ShiftLeft',
    ctrl: 'ControlLeft', control: 'ControlLeft', alt: 'AltLeft', enter: 'Enter', escape: 'Escape', esc: 'Escape', tab: 'Tab',
  };
  return aliases[key.toLowerCase()] ?? key;
}

/** `KeyboardEvent.key` for a code: 'KeyW' → 'w', 'Space' → ' ', 'ShiftLeft' → 'Shift'. */
function keyName(code: string): string {
  const letter = /^Key([A-Z])$/.exec(code);
  if (letter) return letter[1]!.toLowerCase();
  const digit = /^(?:Digit|Numpad)(\d)$/.exec(code);
  if (digit) return digit[1]!;
  if (code === 'Space') return ' ';
  const modifier = /^(Shift|Control|Alt|Meta)(?:Left|Right)$/.exec(code);
  return modifier ? modifier[1]! : code;
}

/** Turns a script into DOM events at fixed steps, in time order (script order within a step). */
export function expandTestInputs(inputs: readonly TestInput[], dt = FIXED_DT): TestEvent[] {
  const events: TestEvent[] = [];
  const toSteps = (seconds: number): number => Math.round(seconds / dt + 1e-9);
  inputs.forEach((input, i) => {
    const where = `inputs[${i}]`;
    if (!input || typeof input !== 'object') throw new TypeError(`${where} must be an object`);
    const at = input.at ?? 0;
    if (input.step !== undefined && (!Number.isInteger(input.step) || input.step < 0)) {
      throw new TypeError(`${where}.step must be a whole number >= 0`);
    }
    if (input.step === undefined && (typeof at !== 'number' || !Number.isFinite(at) || at < 0)) {
      throw new TypeError(`${where}.at must be a number of seconds >= 0`);
    }
    if (input.hold !== undefined && (typeof input.hold !== 'number' || !Number.isFinite(input.hold) || input.hold < 0)) {
      throw new TypeError(`${where}.hold must be a number of seconds >= 0`);
    }
    const step = input.step ?? toSteps(at);
    const release = step + Math.max(1, input.hold !== undefined ? toSteps(input.hold) : 1);
    if (input.key !== undefined) {
      if (typeof input.key !== 'string' || input.key === '') throw new TypeError(`${where}.key must be a key code such as "KeyW"`);
      const code = normalizeKeyCode(input.key);
      const action = input.hold !== undefined ? 'press' : (input.action ?? 'press');
      if (action === 'down' || action === 'press') events.push({ step, type: 'keydown', code });
      if (action === 'up') events.push({ step, type: 'keyup', code });
      else if (action === 'press') events.push({ step: release, type: 'keyup', code });
      else if (action !== 'down') throw new TypeError(`${where}.action must be "down", "up" or "press"`);
      return;
    }
    if (input.mouse !== undefined) {
      const button = typeof input.button === 'number' ? input.button : MOUSE_BUTTONS[input.button ?? 'left'];
      if (button === undefined || ![0, 1, 2].includes(button)) throw new TypeError(`${where}.button must be "left", "middle" or "right"`);
      const point = { ...(input.x !== undefined && { x: input.x }), ...(input.y !== undefined && { y: input.y }) };
      switch (input.mouse) {
        case 'move':
          events.push({ step, type: 'pointermove', ...point, ...(input.dx !== undefined && { dx: input.dx }), ...(input.dy !== undefined && { dy: input.dy }) });
          return;
        case 'wheel':
          events.push({ step, type: 'wheel', ...point, dx: input.dx ?? 0, dy: input.dy ?? 0 });
          return;
        case 'down':
        case 'click':
          events.push({ step, type: 'pointerdown', ...point, button });
          if (input.mouse === 'click' || input.hold !== undefined) events.push({ step: release, type: 'pointerup', button });
          return;
        case 'up':
          events.push({ step, type: 'pointerup', ...point, button });
          return;
        default:
          throw new TypeError(`${where}.mouse must be "move", "down", "up", "click" or "wheel"`);
      }
    }
    throw new TypeError(`${where} needs "key" or "mouse", e.g. {"at": 0, "key": "KeyW", "hold": 2}`);
  });
  return events.map((e, order) => ({ e, order })).sort((a, b) => a.e.step - b.e.step || a.order - b.order).map(({ e }) => e);
}


/** `begin()`'s `save` or `store`: a plain object of key → value, or undefined. */
function seedValues(value: unknown, name: 'save' | 'store'): Record<string, unknown> | undefined {
  if (value === undefined || value === null) return undefined;
  if (typeof value !== 'object' || Array.isArray(value)) throw new TypeError(`begin({ ${name} }) must be an object of key → value, e.g. { "best": 42 }`);
  return value as Record<string, unknown>;
}

/** Keys of `ctx.save` the engine keeps there (`gs.locale`, `gs.audio`). */
const INTERNAL_SAVE = /^gs\./;

const round3 = (n: number): number => Math.round(n * 1000) / 1000;
const vec = (v: Vector3): [number, number, number] => [round3(v.x), round3(v.y), round3(v.z)];
const UP = new Vector3(0, 1, 0);

/** Per-step record of one tracked entity. */
class Track {
  entity: Entity | null = null;
  start: Vector3 | null = null;
  readonly last = new Vector3();
  readonly min = new Vector3(Infinity, Infinity, Infinity);
  readonly max = new Vector3(-Infinity, -Infinity, -Infinity);
  path = 0;
  destroyedAt: number | null = null;
  readonly values = new Map<string, unknown>();
  readonly changes = new Map<string, FieldChange>();
  private readonly position = new Vector3();

  constructor(readonly name: string) {}

  sample(world: World, time: number): void {
    let e = this.entity;
    if (!e || !e.alive) {
      e = world.get(this.name) ?? world.findByName(this.name) ?? null;
      if (!e) {
        if (this.entity && this.destroyedAt === null) this.destroyedAt = time;
        return;
      }
      this.entity = e;
      this.destroyedAt = null;
    }
    const p = e.object3d.getWorldPosition(this.position);
    if (this.start) this.path += p.distanceTo(this.last);
    else this.start = p.clone();
    this.last.copy(p);
    this.min.min(p);
    this.max.max(p);
    for (const component of e.components()) {
      const data = e.get<Record<string, unknown>>(component);
      for (const field in data) this.field(`${component}.${field}`, data[field], time);
    }
  }

  report(): TrackedEntity {
    const found = this.start !== null;
    const alive = this.entity?.alive ? this.entity : null;
    return {
      name: this.name,
      id: this.entity?.id ?? null,
      found,
      start: this.start ? vec(this.start) : null,
      final: found ? vec(this.last) : null,
      min: found ? vec(this.min) : null,
      max: found ? vec(this.max) : null,
      path: round3(this.path),
      destroyedAt: this.destroyedAt === null ? null : round3(this.destroyedAt),
      components: alive ? Object.fromEntries(alive.components().map((c) => [c, JSON.parse(JSON.stringify(alive.get(c)))])) : null,
      changes: Object.fromEntries(this.changes),
    };
  }

  private field(key: string, value: unknown, time: number): void {
    const copy = Array.isArray(value) ? [...value] : value;
    if (!this.values.has(key)) {
      this.values.set(key, copy);
      return;
    }
    const before = this.values.get(key);
    if (same(before, value)) return;
    this.values.set(key, copy);
    let change = this.changes.get(key);
    if (!change) {
      change = { start: before, final: copy, count: 0, first: round3(time), last: round3(time) };
      if (typeof before === 'number') change.min = change.max = before;
      this.changes.set(key, change);
    }
    change.final = copy;
    change.count++;
    change.last = round3(time);
    if (typeof value === 'number' && change.min !== undefined && change.max !== undefined) {
      change.min = Math.min(change.min, value);
      change.max = Math.max(change.max, value);
    }
  }
}

function same(a: unknown, b: unknown): boolean {
  if (a === b) return true;
  if (Array.isArray(a) && Array.isArray(b)) return a.length === b.length && a.every((v, i) => v === b[i]);
  if (a && b && typeof a === 'object' && typeof b === 'object') return JSON.stringify(a) === JSON.stringify(b);
  return false;
}

export class TestControl {
  private held = false;
  private steps = 0;
  private loadsAtBegin = 0;
  private changesAtBegin = 0;
  private idleSteps = 0;
  private simulateMs = 0;
  private renders = 0;
  private renderMs = 0;
  private events: TestEvent[] = [];
  private cursor = 0;
  private tracks: Track[] = [];
  private readonly keys = new Set<string>();
  private buttons = 0;
  private pointer: { x: number; y: number } | null = null;
  /** The real Math.random while a seeded one replaces it. */
  private realRandom: (() => number) | null = null;
  /** Between begin() and end(): `ctx.save` is the test's own store. */
  private isolated = false;
  /** The test save's contents when the last test ended (saved() after end()). */
  private lastSave: Record<string, unknown> = {};

  constructor(private readonly driver: TestDriver) {}

  /** True while a test holds the clock. */
  get holding(): boolean {
    return this.held;
  }

  /** Takes the clock without starting a test: the real-time loop stops simulating and rendering. */
  hold(): void {
    this.held = true;
    this.driver.setHeld(true);
  }

  /**
   * Starts a test at time 0: holds the clock, clears the script and tracking, seeds `ctx.random`
   * and Math.random (`seed`, default 1; `null` keeps Math.random and seeds `ctx.random` freshly),
   * gives `ctx.save` an empty store of its own (with the `save` keys) and plays. `reset: true` rebuilds
   * the scene first. `store` keys are set after the reset (which empties `ctx.store`) and before the
   * first step, so the systems' start() of a reset run reads them.
   */
  async begin(options: TestBeginOptions = {}): Promise<TestStatus> {
    const save = seedValues(options.save, 'save');
    const store = seedValues(options.store, 'store');
    this.hold();
    this.driver.isolate(true);
    this.isolated = true;
    if (save) this.driver.seedSave(save); // before the reset: whatever it runs reads the seeded save
    if (options.reset) await this.driver.restart();
    await this.driver.idle();
    if (store) this.driver.seedStore(store);
    const { state, uiPaused } = this.driver.info();
    if (state === 'stopped' || (state === 'paused' && !uiPaused)) this.driver.play();
    this.releaseAll();
    this.steps = 0;
    this.idleSteps = 0;
    this.simulateMs = 0;
    this.renders = 0;
    this.renderMs = 0;
    this.events = [];
    this.cursor = 0;
    this.tracks = [];
    this.pointer = null;
    this.loadsAtBegin = this.driver.info().loads;
    this.changesAtBegin = this.driver.info().sceneChanges;
    const seed = options.seed === undefined ? 1 : options.seed;
    this.useRandom(seed);
    this.driver.seed(seed);
    return this.status();
  }

  /** Adds inputs to the script (times relative to `begin()`; past ones apply on the next step). */
  schedule(inputs: readonly TestInput[]): { events: number; lastStep: number } {
    const added = expandTestInputs(inputs);
    const rest = this.events.slice(this.cursor);
    const merged = [...rest, ...added].map((e, order) => ({ e, order }));
    merged.sort((a, b) => a.e.step - b.e.step || a.order - b.order);
    this.events = merged.map(({ e }) => e);
    this.cursor = 0;
    return { events: added.length, lastStep: added.reduce((m, e) => Math.max(m, e.step), 0) };
  }

  /** Runs `steps` fixed steps (1/60 s each) now, applying due inputs before each. */
  async advance(steps = 1): Promise<TestStatus> {
    this.requireHeld();
    await this.driver.idle();
    const n = Math.max(0, Math.floor(steps));
    for (let i = 0; i < n; i++) {
      // A scene change the game asked for lands before the next step, wherever the steps are split.
      if (this.driver.busy()) await this.driver.idle();
      this.tick();
    }
    return this.status();
  }

  /** Runs until `seconds` since `begin()` (no-op if already there). */
  advanceTo(seconds: number): Promise<TestStatus> {
    return this.advance(Math.round(seconds / FIXED_DT + 1e-9) - this.steps);
  }

  /** Draws the current state (the HUD follows). Returns the milliseconds it took. */
  render(): number {
    const t0 = performance.now();
    this.driver.render();
    const ms = performance.now() - t0;
    this.renders++;
    this.renderMs += ms;
    return ms;
  }

  /**
   * Moves an entity (default: the first with a CharacterController) for this run only: teleports
   * its body and zeroes its velocity. A camera following it starts there (snapCamera). Returns its new state.
   */
  place(target: string | null | undefined, options: PlaceOptions): EntityState {
    const world = this.driver.world;
    const entity = target ? (world.get(target) ?? world.findByName(target)) : world.query('CharacterController')[0];
    if (!entity) throw new Error(target ? `No entity named "${target}"` : 'No entity with a CharacterController to place');
    let rotation: RotationInput | null = options.rotation ?? null;
    if (options.yaw !== undefined) rotation = new Quaternion().setFromAxisAngle(UP, (options.yaw * Math.PI) / 180);
    this.driver.physics.teleport(entity, options.position ?? null, rotation);
    entity.object3d.updateMatrixWorld(true);
    snapCamera(entity);
    return this.driver.describe({ ids: [entity.id] })[0]!;
  }

  /** Records the named entities (names or ids) every step from now on; see `report()`. */
  track(names: readonly string[]): void {
    this.tracks = names.map((name) => new Track(name));
    for (const t of this.tracks) t.sample(this.driver.world, this.steps * FIXED_DT);
  }

  /** What the tracked entities did since `track()`. */
  report(): TrackedEntity[] {
    return this.tracks.map((t) => t.report());
  }

  /** World positions of the named entities now (`null` if missing). */
  positions(names: readonly string[]): Record<string, [number, number, number] | null> {
    const world = this.driver.world;
    const p = new Vector3();
    return Object.fromEntries(
      names.map((name) => {
        const e = world.get(name) ?? world.findByName(name);
        return [name, e ? vec(e.object3d.getWorldPosition(p)) : null];
      }),
    );
  }

  /** Entity snapshots now, with the test time, the scene, the open menu and the particle count. */
  state(filter?: StateFilter): {
    step: number;
    time: number;
    scene: SceneInfo;
    ui: UiTestState;
    vfx: { emitters: number; particles: number };
    nav?: NavStats;
    entities: EntityState[];
  } {
    return {
      step: this.steps,
      time: round3(this.steps * FIXED_DT),
      scene: { ...this.driver.info().scene },
      ui: this.driver.ui(),
      vfx: this.driver.vfx(),
      ...(this.driver.nav && { nav: this.driver.nav() }),
      entities: this.driver.describe(filter),
    };
  }

  status(): TestStatus {
    const info = this.driver.info();
    return {
      holding: this.held,
      step: this.steps,
      time: round3(this.steps * FIXED_DT),
      gameTime: round3(info.time),
      state: info.state,
      scene: { ...info.scene },
      loads: info.loads - this.loadsAtBegin,
      sceneChanges: info.sceneChanges - this.changesAtBegin,
      screen: info.screen,
      idleSteps: this.idleSteps,
      pending: this.events.length - this.cursor,
      simulateMs: Math.round(this.simulateMs * 10) / 10,
      renders: this.renders,
      renderMs: Math.round(this.renderMs * 10) / 10,
    };
  }

  /**
   * The test's `ctx.save` now, key → value sorted by key: what the run saved on top of `begin({ save })`
   * (a best time, a setting). Keys the engine keeps there (`gs.`: `gs.locale`, the UI language, and
   * `gs.audio`, the channel volumes and mutes) only with `internal: true`, to check that a language or
   * volume choice persists. After `end()`, what it held when the test ended; `{}` before the first `begin()`.
   */
  saved(options: SavedOptions = {}): Record<string, unknown> {
    const entries = this.isolated ? this.driver.saveEntries() : this.lastSave;
    const keys = Object.keys(entries).filter((key) => options.internal || !INTERNAL_SAVE.test(key));
    return Object.fromEntries(keys.sort().map((key) => [key, entries[key]]));
  }

  /** Releases held keys and buttons, restores Math.random and `ctx.save` and gives the clock back to real time. */
  end(): TestStatus {
    this.releaseAll();
    this.events = [];
    this.cursor = 0;
    this.useRandom(null);
    if (this.isolated) this.lastSave = this.driver.saveEntries();
    this.isolated = false;
    this.driver.isolate(false);
    const status = { ...this.status(), holding: false };
    this.held = false;
    this.driver.setHeld(false);
    return status;
  }

  private tick(): void {
    while (this.cursor < this.events.length && this.events[this.cursor]!.step <= this.steps) {
      this.dispatch(this.events[this.cursor++]!);
    }
    // Counted first, so code running in this step (and its logs) sees the time the step ends at.
    this.steps++;
    const t0 = performance.now();
    if (!this.driver.step()) this.idleSteps++;
    this.simulateMs += performance.now() - t0;
    const time = this.steps * FIXED_DT;
    for (const t of this.tracks) t.sample(this.driver.world, time);
  }

  private requireHeld(): void {
    if (!this.held) throw new Error('game.test: call begin() first');
  }

  private useRandom(seed: number | null): void {
    if (this.realRandom) Math.random = this.realRandom;
    this.realRandom = null;
    if (seed === null) return;
    this.realRandom = Math.random;
    Math.random = mulberry32(seed);
  }

  private releaseAll(): void {
    for (const code of [...this.keys]) this.dispatch({ step: this.steps, type: 'keyup', code });
    for (const button of [0, 1, 2]) {
      if (this.buttons & (1 << button)) this.dispatch({ step: this.steps, type: 'pointerup', button });
    }
  }

  private dispatch(e: TestEvent): void {
    const canvas = this.driver.canvas;
    if (e.type === 'keydown' || e.type === 'keyup') {
      const code = e.code!;
      if (e.type === 'keydown') this.keys.add(code);
      else this.keys.delete(code);
      canvas.dispatchEvent(new KeyboardEvent(e.type, { code, key: keyName(code), bubbles: true, cancelable: true, composed: true }));
      return;
    }
    const rect = canvas.getBoundingClientRect();
    const pointer = (this.pointer ??= { x: rect.width / 2, y: rect.height / 2 });
    let movementX = 0;
    let movementY = 0;
    if (e.type === 'pointermove') {
      const x = e.x ?? pointer.x + (e.dx ?? 0);
      const y = e.y ?? pointer.y + (e.dy ?? 0);
      movementX = e.dx ?? x - pointer.x;
      movementY = e.dy ?? y - pointer.y;
      pointer.x = x;
      pointer.y = y;
    } else {
      if (e.x !== undefined) pointer.x = e.x;
      if (e.y !== undefined) pointer.y = e.y;
    }
    const position = { clientX: rect.left + pointer.x, clientY: rect.top + pointer.y, bubbles: true, cancelable: true, composed: true };
    if (e.type === 'wheel') {
      canvas.dispatchEvent(new WheelEvent('wheel', { ...position, deltaX: e.dx ?? 0, deltaY: e.dy ?? 0, deltaMode: 0 }));
      return;
    }
    const button = e.button ?? 0;
    if (e.type === 'pointerdown') this.buttons |= 1 << button;
    if (e.type === 'pointerup') this.buttons &= ~(1 << button);
    const init = {
      ...position,
      movementX,
      movementY,
      button: e.type === 'pointermove' ? -1 : button,
      buttons: this.buttons,
      pointerId: 1,
      pointerType: 'mouse',
      isPrimary: true,
    };
    canvas.dispatchEvent(new PointerEvent(e.type, init));
    const mouseType = e.type === 'pointermove' ? 'mousemove' : e.type === 'pointerdown' ? 'mousedown' : 'mouseup';
    canvas.dispatchEvent(new MouseEvent(mouseType, { ...init, button: Math.max(0, init.button) }));
    if (e.type === 'pointerup' && button === 0) canvas.dispatchEvent(new MouseEvent('click', { ...init, button: 0 }));
  }
}
