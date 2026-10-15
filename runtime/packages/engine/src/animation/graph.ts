// Animation graphs (API.md §15): declarative state machines, like Unity's Animator Controller.
// Gameplay code sets parameters; the graph picks and blends the clips. `defineAnimationGraph`
// registers a graph by name (redefining a name replaces it, for hot reload), and `Animator.graph` or
// `ctx.animation.graph(entity, graph)` runs it on an entity. The runtime sets each action's weight
// and time itself every frame, on game time, so blends, exit times and events repeat exactly.

import { Vector3, type AnimationAction, type AnimationClip, type AnimationMixer } from 'three';
import type { Entity } from '../ecs/entity';
import type { AnimationEvent } from './animation';

// --- Definitions -------------------------------------------------------------------------------

export type GraphParamType = 'float' | 'int' | 'bool' | 'trigger';
/**
 * Where a parameter's value comes from, sampled every frame instead of set by code: `speed` is the
 * horizontal speed in m/s (smoothed, measured from the entity's movement), `verticalSpeed` m/s (+ is
 * up), `grounded` and `airTime` (seconds off the ground) come from the entity's CharacterController
 * (or its nearest ancestor's; without one it is always grounded).
 */
export type GraphMotionSource = 'speed' | 'verticalSpeed' | 'grounded' | 'airTime';

export interface GraphParamOptions {
  type: GraphParamType;
  default?: number | boolean;
  from?: GraphMotionSource;
  /** Triggers: seconds a trigger stays set waiting for a transition. Default 0: one graph update. */
  hold?: number;
}
export type GraphParamDef = GraphParamType | GraphParamOptions;
type ParamDefs = Record<string, GraphParamDef>;

type NumericDef = 'float' | 'int' | { readonly type: 'float' | 'int' };
type TriggerDef = 'trigger' | { readonly type: 'trigger' };
type ParamValue<D> = D extends NumericDef ? number : boolean;
/** Parameter values: numbers for float and int, booleans for bool and trigger. */
export type GraphParams<P> = { -readonly [K in keyof P]: ParamValue<P[K]> };
type Loose<P, T> = string extends keyof P ? string : T;
type NumericParam<P> = Loose<P, { [K in keyof P]: P[K] extends NumericDef ? K : never }[keyof P] & string>;
type TriggerParam<P> = Loose<P, { [K in keyof P]: P[K] extends TriggerDef ? K : never }[keyof P] & string>;
type ParamName<P> = Loose<P, keyof P & string>;

/**
 * A transition condition: a parameter name (a bool or trigger is set, a number is not 0), the
 * name with `!` in front (the opposite), or a function of the parameters.
 */
export type GraphCondition<P> = ParamName<P> | `!${ParamName<P>}` | ((params: Readonly<GraphParams<P>>) => boolean);

/** Entry of a 1D blend: `[threshold, clip]`, or `[threshold, clip, speed]` to play that clip faster or slower. */
export type GraphBlendClip = readonly [number, string] | readonly [number, string, number];

export interface GraphStateDef<P, S extends string> {
  /** The clip this state plays. A state has either `clip` or `blend`. */
  clip?: string;
  /** A 1D blend space: the clips' weights follow the numeric parameter `by` between their thresholds. */
  blend?: { by: NumericParam<P>; clips: readonly GraphBlendClip[] };
  /** Default true. A state played once holds its last frame and reports `finished`. */
  loop?: boolean;
  /** Playback speed: a number, or the name of a numeric parameter. Default 1. */
  speed?: number | NumericParam<P>;
  /** Go to this state when the clip has played once (exit time 1), with the graph's `blend`. */
  next?: S;
  /** Named events at normalized times of the clip (0–1), reported in `ctx.animation.events` (`type: 'event'`). */
  events?: Record<string, number | readonly number[]>;
  /** Called when the state starts and when it ends (after `go`, a transition or `next`). */
  onEnter?: (graph: AnimationGraphHandle<P, S>, entity: Entity) => void;
  onExit?: (graph: AnimationGraphHandle<P, S>, entity: Entity) => void;
}

export interface GraphTransitionDef<P, S extends string> {
  /** A state, several, or '*' (any state except `to`). */
  from: S | readonly S[] | '*';
  to: S;
  /** Every condition must hold. Triggers named here are used up when the transition fires. */
  when?: GraphCondition<P> | readonly GraphCondition<P>[];
  /** Only once the current state has played this much (normalized: 1 = its clip once through). */
  exitTime?: number;
  /** Crossfade seconds. Default: the graph's `blend`. */
  blend?: number;
}

export interface AnimationGraphDef<P extends ParamDefs, S extends string> {
  parameters?: P;
  states: { [K in S]: GraphStateDef<P, NoInfer<S>> };
  /** Checked in order, any-state ('*') transitions first. At most one fires per frame. */
  transitions?: readonly GraphTransitionDef<P, NoInfer<S>>[];
  /** Default: the first state. */
  initial?: NoInfer<S>;
  /** Default crossfade seconds. Default 0.2. */
  blend?: number;
}

/** A defined graph (what `defineAnimationGraph` returns). Pass it to `ctx.animation.graph` for typed parameters. */
export interface AnimationGraph<P = ParamDefs, S extends string = string> {
  readonly name: string;
  readonly states: readonly S[];
  readonly parameters: Readonly<Record<string, { type: GraphParamType; default: number | boolean; from?: GraphMotionSource }>>;
  readonly initial: S;
  /** Default crossfade seconds. */
  readonly blend: number;
  /** Type-only: the parameter definitions. */
  readonly _params?: P;
}

/** A running graph on one entity. */
export interface AnimationGraphHandle<P = ParamDefs, S extends string = string> {
  /** The graph's name. */
  readonly graph: string;
  /** The current state (the one being blended into during a crossfade). */
  readonly state: S;
  /** Seconds since the current state started. */
  readonly time: number;
  /** How far the current state has played: 1 = its clip once through (it keeps counting while it loops). */
  readonly progress: number;
  readonly params: Readonly<GraphParams<P>>;
  get<K extends ParamName<P>>(name: K): K extends keyof P ? ParamValue<P[K]> : number | boolean;
  set<K extends ParamName<P>>(name: K, value: K extends keyof P ? ParamValue<P[K]> : number | boolean): this;
  /** Sets a trigger: the next transition that names it fires and uses it up; unused, it clears after `hold`. */
  trigger(name: TriggerParam<P>): this;
  /** Goes to a state now, crossfading over `blend` seconds (default: the graph's `blend`). */
  go(state: S, blend?: number): this;
}

// --- Registry ----------------------------------------------------------------------------------

interface Param {
  name: string;
  type: GraphParamType;
  default: number | boolean;
  from?: GraphMotionSource;
  hold: number;
}

interface BlendEntry {
  threshold: number;
  clip: string;
  speed: number;
}

interface State {
  name: string;
  clips: BlendEntry[];
  by: string | null;
  loop: boolean;
  speed: number | string;
  next: string | null;
  marks: { name: string; at: number }[];
  transitions: Transition[];
  def: GraphStateDef<any, string>;
}

interface Transition {
  to: string;
  checks: ((params: Record<string, number | boolean>) => boolean)[];
  triggers: string[];
  exitTime: number | null;
  blend: number | null;
}

interface Compiled {
  graph: AnimationGraph<any, any>;
  params: Param[];
  paramsByName: Map<string, Param>;
  states: Map<string, State>;
  any: Transition[];
  initial: string;
  blend: number;
}

const graphs = new Map<string, AnimationGraph<any, any>>();
const compiled = new WeakMap<AnimationGraph<any, any>, Compiled>();

const PARAM_TYPES: readonly GraphParamType[] = ['float', 'int', 'bool', 'trigger'];
const SOURCE_TYPES: Record<GraphMotionSource, 'number' | 'bool'> = { speed: 'number', verticalSpeed: 'number', grounded: 'bool', airTime: 'number' };
const DEFAULT_BLEND = 0.2;
/** Normalized times this close to an exit time count as there (sums of frame steps round). */
const EPS = 1e-6;

/**
 * Defines (or, for hot reload, redefines) the animation graph `name`. Entities running it pick up
 * the new definition on their next frame, keeping their state and parameter values where they
 * still exist. Throws on a mistake (an unknown state, parameter or clip spec), naming it.
 */
export function defineAnimationGraph<const P extends ParamDefs = {}, S extends string = string>(
  name: string,
  def: AnimationGraphDef<P, S>,
): AnimationGraph<P, S> {
  const c = compile(name, def as unknown as AnimationGraphDef<ParamDefs, string>);
  graphs.set(name, c.graph);
  return c.graph as AnimationGraph<P, S>;
}

export function getAnimationGraph(name: string): AnimationGraph | undefined {
  return graphs.get(name);
}

export function listAnimationGraphs(): string[] {
  return [...graphs.keys()];
}

export function removeAnimationGraph(name: string): boolean {
  return graphs.delete(name);
}

function compile(name: string, def: AnimationGraphDef<ParamDefs, string>): Compiled {
  const fail = (message: string): never => {
    throw new Error(`[animation] Graph "${name}": ${message}`);
  };
  if (!name || typeof name !== 'string') throw new Error('[animation] defineAnimationGraph needs a name');
  if (!def || typeof def.states !== 'object') fail('needs `states`');

  const params: Param[] = [];
  for (const [pname, raw] of Object.entries(def.parameters ?? {})) {
    const o: GraphParamOptions = typeof raw === 'string' ? { type: raw } : raw;
    if (!PARAM_TYPES.includes(o.type)) fail(`parameter "${pname}" has type "${o.type}"; use ${PARAM_TYPES.join(', ')}`);
    const numeric = o.type === 'float' || o.type === 'int';
    if (o.from !== undefined) {
      const kind = SOURCE_TYPES[o.from];
      if (!kind) fail(`parameter "${pname}": unknown source "${o.from}"; use ${Object.keys(SOURCE_TYPES).join(', ')}`);
      if ((kind === 'number') !== numeric || o.type === 'trigger') fail(`parameter "${pname}": "${o.from}" needs a ${kind === 'number' ? 'float' : 'bool'} parameter`);
    }
    params.push({ name: pname, type: o.type, default: o.default ?? (numeric ? 0 : false), from: o.from, hold: Math.max(0, o.hold ?? 0) });
  }
  const paramsByName = new Map(params.map((p) => [p.name, p]));
  const paramNames = params.map((p) => p.name).join(', ') || 'none';
  const numericParam = (pname: unknown, where: string): string => {
    const p = paramsByName.get(pname as string);
    if (!p || (p.type !== 'float' && p.type !== 'int')) fail(`${where}: "${String(pname)}" is not a float or int parameter (parameters: ${paramNames})`);
    return pname as string;
  };

  const stateNames = Object.keys(def.states);
  if (stateNames.length === 0) fail('needs at least one state');
  const stateList = stateNames.join(', ');
  const knownState = (s: unknown, where: string): string => {
    if (typeof s !== 'string' || !(s in def.states)) fail(`${where}: no state "${String(s)}" (states: ${stateList})`);
    return s as string;
  };

  const states = new Map<string, State>();
  for (const sname of stateNames) {
    const s = def.states[sname] as GraphStateDef<any, string>;
    const where = `state "${sname}"`;
    if (!s || typeof s !== 'object') fail(`${where} must be an object`);
    if ((s.clip === undefined) === (s.blend === undefined)) fail(`${where} needs either \`clip\` or \`blend\``);
    let clips: BlendEntry[];
    let by: string | null = null;
    if (s.clip !== undefined) {
      if (typeof s.clip !== 'string' || !s.clip) fail(`${where}: \`clip\` must be a clip name`);
      clips = [{ threshold: 0, clip: s.clip, speed: 1 }];
    } else {
      by = numericParam(s.blend!.by, `${where} blend.by`);
      const entries = s.blend!.clips ?? [];
      if (entries.length === 0) fail(`${where}: blend.clips is empty`);
      clips = entries.map((e) => {
        if (!Array.isArray(e) || typeof e[0] !== 'number' || typeof e[1] !== 'string') fail(`${where}: blend.clips entries are [threshold, clip] or [threshold, clip, speed]`);
        return { threshold: e[0], clip: e[1], speed: typeof e[2] === 'number' && e[2] > 0 ? e[2] : 1 };
      });
      for (let i = 1; i < clips.length; i++) {
        if (!(clips[i]!.threshold > clips[i - 1]!.threshold)) fail(`${where}: blend thresholds must increase`);
      }
    }
    const speed = s.speed ?? 1;
    if (typeof speed !== 'number') numericParam(speed, `${where} speed`);
    const marks: State['marks'] = [];
    for (const [ename, at] of Object.entries(s.events ?? {})) {
      for (const t of typeof at === 'number' ? [at] : at) {
        if (typeof t !== 'number' || t < 0 || t > 1) fail(`${where}: event "${ename}" times are normalized (0–1)`);
        marks.push({ name: ename, at: t });
      }
    }
    states.set(sname, {
      name: sname,
      clips,
      by,
      loop: s.loop ?? true,
      speed,
      next: s.next === undefined ? null : knownState(s.next, `${where} next`),
      marks,
      transitions: [],
      def: s,
    });
  }

  const any: Transition[] = [];
  (def.transitions ?? []).forEach((t, i) => {
    const where = `transition ${i + 1} (${Array.isArray(t.from) ? t.from.join('|') : String(t.from)} → ${String(t.to)})`;
    const to = knownState(t.to, where);
    const conditions = t.when === undefined ? [] : Array.isArray(t.when) ? t.when : [t.when];
    const checks: Transition['checks'] = [];
    const triggers: string[] = [];
    for (const c of conditions as GraphCondition<ParamDefs>[]) {
      if (typeof c === 'function') checks.push(c as Transition['checks'][number]);
      else if (typeof c === 'string') {
        const not = c.startsWith('!');
        const pname = not ? c.slice(1) : c;
        const p = paramsByName.get(pname);
        if (!p) fail(`${where}: no parameter "${pname}" (parameters: ${paramNames})`);
        if (p!.type === 'trigger' && !not) triggers.push(pname);
        checks.push(not ? (v) => !truthy(v[pname]) : (v) => truthy(v[pname]));
      } else fail(`${where}: \`when\` takes parameter names and functions`);
    }
    const exitTime = t.exitTime ?? null;
    if (exitTime !== null && !(exitTime >= 0)) fail(`${where}: exitTime must be 0 or more`);
    if (checks.length === 0 && exitTime === null) fail(`${where} needs \`when\` or \`exitTime\``);
    const transition: Transition = { to, checks, triggers, exitTime, blend: t.blend === undefined ? null : Math.max(0, t.blend) };
    if (t.from === '*') any.push(transition);
    else for (const from of Array.isArray(t.from) ? t.from : [t.from]) states.get(knownState(from, where))!.transitions.push(transition);
  });

  const initial = def.initial === undefined ? stateNames[0]! : knownState(def.initial, 'initial');
  const graph: AnimationGraph<any, any> = Object.freeze({
    name,
    states: Object.freeze([...stateNames]),
    parameters: Object.freeze(Object.fromEntries(params.map((p) => [p.name, { type: p.type, default: p.default, ...(p.from && { from: p.from }) }]))),
    initial,
    blend: Math.max(0, def.blend ?? DEFAULT_BLEND),
  });
  const result: Compiled = { graph, params, paramsByName, states, any, initial, blend: graph.blend };
  compiled.set(graph, result);
  return result;
}

function truthy(value: number | boolean | undefined): boolean {
  return typeof value === 'number' ? value !== 0 : value === true;
}

// --- Character motion for `from` parameters ----------------------------------------------------

/** Grounded state and vertical speed of the character that carries an entity, or null. */
export type GraphMotionProvider = (entity: Entity) => { grounded: boolean; verticalVelocity: number } | null;
let motionProvider: GraphMotionProvider = () => null;

/** Set by the built-in character systems, which know the CharacterController (src/systems/locomotion.ts). */
export function setGraphMotionProvider(provider: GraphMotionProvider): void {
  motionProvider = provider;
}

/** How fast measured speeds follow the movement (1/s); faster than TELEPORT_SPEED (m/s) is a respawn. */
const SMOOTHING = 15;
const TELEPORT_SPEED = 60;
const position = new Vector3();

// --- Runtime -----------------------------------------------------------------------------------

/** What a graph needs from the animation service: the entity's mixer, its clips, events and warnings. */
export interface GraphHost {
  readonly entity: Entity;
  readonly mixer: AnimationMixer;
  /** The named clip bound to the entity, or null (the host warns once). */
  clip(name: string): AnimationClip | null;
  /** The graph now sets this action's weight and time (stop fading it out). */
  claim(action: AnimationAction): void;
  emit(event: AnimationEvent): void;
  warn(key: string, message: string): void;
}

interface Active {
  state: State;
  /** Normalized time since the state started: 1 = its clip once through. */
  played: number;
  /** Seconds since the state started. */
  time: number;
  weight: number;
  /** Weight when the current crossfade began. */
  from: number;
  finished: boolean;
}

export interface GraphSnapshot {
  graph: string;
  state: string;
  clip: string | null;
  time: number;
  progress: number;
  params: Record<string, number | boolean>;
}

const round3 = (n: number): number => Math.round(n * 1000) / 1000;

export class GraphRuntime {
  readonly handle: AnimationGraphHandle;
  readonly params: Record<string, number | boolean> = {};
  graph: AnimationGraph;
  private c: Compiled;
  /** Set triggers → seconds they may still wait. */
  private readonly triggers = new Map<string, number>();
  private active: Active[] = [];
  private current!: Active;
  private fade = { elapsed: 0, duration: 0 };
  private used = new Set<AnimationAction>();
  private readonly motion = { last: null as Vector3 | null, speed: 0, vertical: 0, air: 0 };
  private depth = 0;

  constructor(
    graph: AnimationGraph,
    private readonly host: GraphHost,
  ) {
    this.graph = graph;
    this.c = compiled.get(graph)!;
    for (const p of this.c.params) this.params[p.name] = p.default;
    this.handle = this.makeHandle();
    this.sampleMotion(0);
    this.go(this.c.initial, 0, true);
  }

  get name(): string {
    return this.graph.name;
  }

  get state(): string {
    return this.current.state.name;
  }

  /** Actions this graph drives now. */
  get actions(): ReadonlySet<AnimationAction> {
    return this.used;
  }

  /** Swaps in a redefined graph, keeping the state and parameter values that still exist. */
  rebind(graph: AnimationGraph): void {
    const next = compiled.get(graph)!;
    const old = this.params;
    const values: Record<string, number | boolean> = {};
    for (const p of next.params) {
      const before = this.c.paramsByName.get(p.name);
      values[p.name] = before && before.type === p.type ? old[p.name]! : p.default;
    }
    for (const key of Object.keys(old)) delete old[key];
    Object.assign(old, values);
    for (const name of [...this.triggers.keys()]) if (next.paramsByName.get(name)?.type !== 'trigger') this.triggers.delete(name);
    this.graph = graph;
    this.c = next;
    this.active = this.active.flatMap((a) => {
      const state = next.states.get(a.state.name);
      return state ? [{ ...a, state }] : [];
    });
    const current = this.active.find((a) => a.state.name === this.current.state.name);
    if (current) this.current = current;
    else {
      this.active = [];
      this.go(next.initial, 0, true);
    }
  }

  /** One frame: sample motion, advance the states, take at most one transition, blend, then pose. */
  update(dt: number, speedScale: number): void {
    this.sampleMotion(dt);
    for (const a of this.active) this.advance(a, dt, speedScale);
    this.fade.elapsed += dt;
    const t = this.pick();
    if (t) this.take(t);
    this.blendWeights();
    for (const [name, left] of this.triggers) {
      if (left <= 0) {
        this.triggers.delete(name);
        this.params[name] = false;
      } else this.triggers.set(name, left - dt);
    }
    this.apply();
  }

  /** Sets every action's weight and time from the active states (the mixer then poses them). */
  apply(): void {
    const poses = new Map<AnimationClip, { weight: number; time: number; best: number }>();
    for (const a of this.active) {
      if (a.weight <= 0) continue;
      const phase = this.phase(a);
      for (const [clip, w] of this.clipWeights(a.state)) {
        const weight = a.weight * w;
        if (weight <= 0) continue;
        const pose = poses.get(clip);
        const time = phase * clip.duration;
        if (!pose) poses.set(clip, { weight, time, best: weight });
        else {
          pose.weight += weight;
          if (weight > pose.best) Object.assign(pose, { time, best: weight });
        }
      }
    }
    const now = new Set<AnimationAction>();
    for (const [clip, pose] of poses) {
      const action = this.host.mixer.clipAction(clip);
      this.host.claim(action);
      action.stopFading();
      action.enabled = true;
      action.timeScale = 0; // The graph advances time itself.
      if (!action.isScheduled()) action.play();
      action.weight = pose.weight;
      action.time = pose.time;
      now.add(action);
    }
    for (const action of this.used) if (!now.has(action)) action.stop();
    this.used = now;
  }

  snapshot(): GraphSnapshot {
    const params: Record<string, number | boolean> = {};
    for (const [k, v] of Object.entries(this.params)) params[k] = typeof v === 'number' ? round3(v) : v;
    return {
      graph: this.graph.name,
      state: this.current.state.name,
      clip: this.clipName(this.current.state),
      time: round3(this.current.time),
      progress: round3(this.current.played),
      params,
    };
  }

  /** The strongest clip of the current state now, by its bound name. */
  currentClip(): string | null {
    return this.clipName(this.current.state);
  }

  /** The current state runs (a state played once stops running at its end). */
  running(clip?: string): boolean {
    if (this.current.finished) return false;
    if (clip === undefined) return true;
    const bound = this.host.clip(clip);
    return [...this.clipWeights(this.current.state)].some(([c, w]) => c === bound && w > 0);
  }

  // -------------------------------------------------------------------------

  private makeHandle(): AnimationGraphHandle {
    const self = this;
    const handle: AnimationGraphHandle = {
      get graph() {
        return self.graph.name;
      },
      get state() {
        return self.current.state.name;
      },
      get time() {
        return self.current.time;
      },
      get progress() {
        return self.current.played;
      },
      get params() {
        return self.params;
      },
      get(name: string) {
        return self.params[name] as never;
      },
      set(name: string, value: number | boolean) {
        const p = self.param(name);
        if (!p) return handle;
        if (p.type === 'trigger') {
          if (value) self.triggers.set(name, p.hold);
          else self.triggers.delete(name);
          self.params[name] = !!value;
        } else if (p.type === 'bool') self.params[name] = !!value;
        else {
          const n = Number(value);
          self.params[name] = p.type === 'int' ? Math.round(n) : n;
        }
        return handle;
      },
      trigger(name: string) {
        const p = self.param(name);
        if (p && p.type !== 'trigger') self.host.warn(`${self.name}:notrigger:${name}`, `[animation] Graph "${self.name}": "${name}" is a ${p.type} parameter, not a trigger`);
        else if (p) handle.set(name, true);
        return handle;
      },
      go(state: string, blend?: number) {
        if (!self.c.states.has(state)) {
          self.host.warn(`${self.name}:nostate:${state}`, `[animation] Graph "${self.name}" has no state "${state}" (states: ${self.graph.states.join(', ')})`);
        } else self.go(state, blend ?? self.c.blend, false);
        return handle;
      },
    };
    return handle;
  }

  private param(name: string): Param | null {
    const p = this.c.paramsByName.get(name);
    if (!p) {
      const known = this.c.params.map((x) => x.name).join(', ') || 'none';
      this.host.warn(`${this.name}:noparam:${name}`, `[animation] Graph "${this.name}" has no parameter "${name}" (parameters: ${known})`);
      return null;
    }
    return p;
  }

  private sampleMotion(dt: number): void {
    const sourced = this.c.params.filter((p) => p.from);
    if (sourced.length === 0) return;
    const m = this.motion;
    const entity = this.host.entity;
    entity.object3d.getWorldPosition(position);
    const character = motionProvider(entity);
    if (m.last && dt > 0) {
      const k = Math.min(1, dt * SMOOTHING);
      const horizontal = Math.hypot(position.x - m.last.x, position.z - m.last.z) / dt;
      const vertical = (position.y - m.last.y) / dt;
      if (horizontal < TELEPORT_SPEED && Math.abs(vertical) < TELEPORT_SPEED) {
        m.speed += (horizontal - m.speed) * k;
        if (!character) m.vertical += (vertical - m.vertical) * k;
      }
    }
    (m.last ??= new Vector3()).copy(position);
    const grounded = character ? character.grounded : true;
    if (character) m.vertical = character.verticalVelocity;
    m.air = grounded ? 0 : m.air + dt;
    for (const p of sourced) {
      const value = p.from === 'speed' ? m.speed : p.from === 'verticalSpeed' ? m.vertical : p.from === 'airTime' ? m.air : grounded;
      this.params[p.name] = p.type === 'int' ? Math.round(value as number) : value;
    }
  }

  private rate(state: State, speedScale: number): number {
    const speed = typeof state.speed === 'number' ? state.speed : Number(this.params[state.speed] ?? 0);
    return speed * speedScale;
  }

  /** Bound clips of a state with their weights (a blend follows its parameter), merged by clip. */
  private clipWeights(state: State): Map<AnimationClip, number> {
    const out = new Map<AnimationClip, number>();
    for (const [entry, w] of this.entryWeights(state)) {
      const clip = this.host.clip(entry.clip)!;
      out.set(clip, (out.get(clip) ?? 0) + w);
    }
    return out;
  }

  /** Blend entries whose clip exists, with their weights (summing to 1). */
  private entryWeights(state: State): [BlendEntry, number][] {
    const entries = state.clips.filter((e) => this.host.clip(e.clip) !== null);
    if (entries.length <= 1) return entries.map((e) => [e, 1]);
    const v = Number(this.params[state.by!] ?? 0);
    if (v <= entries[0]!.threshold) return [[entries[0]!, 1]];
    const last = entries[entries.length - 1]!;
    if (v >= last.threshold) return [[last, 1]];
    const i = entries.findIndex((e) => e.threshold > v);
    const a = entries[i - 1]!;
    const b = entries[i]!;
    const t = (v - a.threshold) / (b.threshold - a.threshold);
    return [
      [a, 1 - t],
      [b, t],
    ];
  }

  /** Seconds one normalized unit of the state lasts at speed 1: its clips' lengths, weighted. */
  private cycle(state: State): number {
    let seconds = 0;
    for (const [entry, w] of this.entryWeights(state)) seconds += (w * this.host.clip(entry.clip)!.duration) / entry.speed;
    return seconds > 1e-4 ? seconds : 1;
  }

  private phase(a: Active): number {
    if (!a.state.loop) return Math.min(1, Math.max(0, a.played));
    return a.played - Math.floor(a.played);
  }

  private advance(a: Active, dt: number, speedScale: number): void {
    const before = a.played;
    a.time += dt;
    a.played += (dt * this.rate(a.state, speedScale)) / this.cycle(a.state);
    if (!a.state.loop) a.played = a.played > 1 - EPS ? 1 : Math.max(0, a.played);
    if (a !== this.current || a.played <= before) return;
    for (const mark of a.state.marks) {
      // Each crossing of the mark: once for a clip played once, every loop for a looping one.
      const first = a.state.loop ? Math.floor(before - mark.at + EPS) + 1 : 0;
      for (let n = first; n - first < 8; n++) {
        const at = n + mark.at - EPS;
        if (at > a.played) break;
        if (at > before) this.emit('event', a.state, mark.name);
        if (!a.state.loop) break;
      }
    }
    if (!a.state.loop) {
      if (!a.finished && a.played >= 1) {
        a.finished = true;
        this.emit('finished', a.state);
      }
    } else if (Math.floor(a.played) > Math.floor(before)) this.emit('loop', a.state);
  }

  private pick(): Transition | null {
    const current = this.current;
    const ok = (t: Transition): boolean =>
      (t.exitTime === null || current.played >= t.exitTime - EPS) && t.checks.every((check) => check(this.params));
    for (const t of this.c.any) if (t.to !== current.state.name && ok(t)) return t;
    for (const t of current.state.transitions) if (ok(t)) return t;
    if (current.state.next !== null && current.played >= 1 - EPS) {
      return { to: current.state.next, checks: [], triggers: [], exitTime: 1, blend: null };
    }
    return null;
  }

  private take(t: Transition): void {
    for (const name of t.triggers) {
      this.triggers.delete(name);
      this.params[name] = false;
    }
    this.go(t.to, t.blend ?? this.c.blend, false);
  }

  /** Enters `name`, crossfading from every active state over `blend` seconds. */
  private go(name: string, blend: number, initial: boolean): void {
    if (this.depth > 8) {
      this.host.warn(`${this.name}:loop`, `[animation] Graph "${this.name}": onEnter/onExit keep changing state; stopped at "${name}"`);
      return;
    }
    this.depth++;
    try {
      const state = this.c.states.get(name)!;
      if (!initial) {
        this.emit('exit', this.current.state);
        this.current.state.def.onExit?.(this.handle as never, this.host.entity);
      }
      for (const a of this.active) a.from = a.weight;
      let entry = this.active.find((a) => a.state === state);
      // A looping state still fading out carries on where it is; anything else starts over.
      if (entry && !(state.loop && entry.weight > 0)) {
        this.active.splice(this.active.indexOf(entry), 1);
        entry = undefined;
      }
      if (entry) {
        this.active.splice(this.active.indexOf(entry), 1);
        entry.played -= Math.floor(entry.played); // Same phase; exit times count from here.
      }
      entry ??= { state, played: 0, time: 0, weight: 0, from: 0, finished: false };
      entry.time = 0;
      this.active.push(entry);
      this.current = entry;
      this.fade = { elapsed: 0, duration: blend };
      if (initial || blend <= 0) {
        this.blendWeights();
      }
      this.emit('enter', state);
      state.def.onEnter?.(this.handle as never, this.host.entity);
    } finally {
      this.depth--;
    }
  }

  private blendWeights(): void {
    const d = this.fade.duration;
    const p = d > 0 ? Math.min(1, this.fade.elapsed / d) : 1;
    for (const a of this.active) a.weight = a === this.current ? a.from + (1 - a.from) * p : a.from * (1 - p);
    this.active = this.active.filter((a) => a === this.current || a.weight > 0);
  }

  private clipName(state: State): string | null {
    let best: AnimationClip | null = null;
    let most = -1;
    for (const [clip, w] of this.clipWeights(state)) {
      if (w > most) {
        best = clip;
        most = w;
      }
    }
    return best?.name ?? null;
  }

  private emit(type: AnimationEvent['type'], state: State, name?: string): void {
    this.host.emit({
      type,
      entity: this.host.entity,
      clip: this.clipName(state) ?? '',
      graph: this.graph.name,
      state: state.name,
      ...(name !== undefined && { name }),
    });
  }
}
