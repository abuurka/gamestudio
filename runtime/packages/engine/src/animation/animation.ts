// Animation playback (`ctx.animation`) on top of Three.js AnimationMixer, plus
// the built-in Animator component. Each animated entity gets its own mixer rooted
// at its object3d; a clip is bound to an entity with only the tracks whose
// targets (the object, its children or bones) live under that root. An entity
// can instead run an animation graph (graph.ts), which drives the same mixer.

import {
  AnimationClip,
  AnimationMixer,
  LoopOnce,
  LoopRepeat,
  PropertyBinding,
  type AnimationAction,
  type Bone,
  type Object3D,
  type SkinnedMesh,
} from 'three';
import { componentName } from '../ecs/entity';
import type { Entity } from '../ecs/entity';
import type { World } from '../ecs/world';
import { Animator } from '../schema/builtins';
import type { ComponentValues } from '../schema/registry';
import {
  getAnimationGraph,
  GraphRuntime,
  listAnimationGraphs,
  type AnimationGraph,
  type AnimationGraphHandle,
  type GraphSnapshot,
} from './graph';

type AnimatorValues = ComponentValues<typeof Animator>;

export interface PlayOptions {
  /** Crossfade seconds from the current clip. Default: `Animator.fade`, else 0.25. */
  fade?: number;
  /** Default: `Animator.loop`, else true. A clip played once holds its last frame. */
  loop?: boolean;
  /** Playback speed. Default: `Animator.speed`, else 1. */
  speed?: number;
  /** Start over even if this clip is already playing. Default false. */
  restart?: boolean;
}

export interface AnimationEvent {
  /**
   * 'finished': a clip played once reached its end. 'loop': a looping clip wrapped around.
   * Graphs also report 'enter' and 'exit' (a state started or ended) and 'event' (a state's named event).
   */
  type: 'finished' | 'loop' | 'enter' | 'exit' | 'event';
  entity: Entity;
  /** The clip (for a graph state: its strongest clip now, or '' if it has none). */
  clip: string;
  /** Graph events: the graph and its state. */
  graph?: string;
  state?: string;
  /** 'event': the event's name. */
  name?: string;
}

/** What `game_state` and `test.state()` show of an animated entity (`EntityState.animation`). */
export interface AnimationSnapshot extends Partial<GraphSnapshot> {
  clip: string | null;
  playing: boolean;
}

export interface AnimationAPI {
  /**
   * Plays `clip` (an exported Blender action) on the entity, crossfading from the
   * current one. `clip` defaults to `Animator.clip`, then to the entity's first clip.
   * Returns false (and logs a warning once) if no such clip animates the entity.
   */
  play(entity: Entity, clip?: string, options?: PlayOptions): boolean;
  /** Stops the entity's animation (fading out over `fade` seconds; default 0 = at once). */
  stop(entity: Entity, options?: { fade?: number }): void;
  /** True while a clip (or the named clip) is running on the entity. */
  isPlaying(entity: Entity, clip?: string): boolean;
  /** Name of the clip last started on the entity, or null. */
  current(entity: Entity): string | null;
  /** Clips that animate the entity, or every clip in the scene and its prefabs. */
  clips(entity?: Entity): string[];
  /** Clip ends and loops from this frame (read them in update or late systems). */
  readonly events: readonly AnimationEvent[];
  /** The entity's Three.js AnimationMixer (created on demand), for advanced blending. */
  mixer(entity: Entity): AnimationMixer;
  /**
   * A bone of the entity's own skeleton by its Blender name, or null. Loading renames repeated
   * bone names in later rigs (`upper_leg_L_1`); this finds the entity's bone either way.
   */
  bone(entity: Entity, name: string): Bone | null;
  /**
   * The animation graph running on the entity (`defineAnimationGraph`). Given a graph (or its name),
   * starts it unless it already runs there; without one, returns the running graph, or starts the
   * one its Animator names. Null when no graph runs (or the name is unknown: it warns once).
   * `play` and `stop` take the entity back from its graph.
   */
  graph<P, S extends string>(entity: Entity, graph: AnimationGraph<P, S>): AnimationGraphHandle<P, S> | null;
  graph(entity: Entity, graph?: string): AnimationGraphHandle | null;
}

const DEFAULT_FADE = 0.25;

interface EntityAnimation {
  entity: Entity;
  mixer: AnimationMixer;
  /** Clip name → clip filtered to this entity's tracks, or null if none apply. */
  bound: Map<string, AnimationClip | null>;
  action: AnimationAction | null;
  clip: string | null;
  /** Actions fading out; stopped once their weight reaches zero. */
  fading: Set<AnimationAction>;
  /** Animator values last applied, to react to edits (live link or code). */
  seen: AnimatorValues | null;
  /** The animation graph driving the mixer instead of `action`, if any. */
  graph: GraphRuntime | null;
  /** `Animator.graph` names a graph not defined yet: it starts once it is. */
  pendingGraph: string | null;
}

/** Every service's entities, for snapshots (an entity belongs to one game). */
const live = new WeakMap<Entity, EntityAnimation>();

/** The entity's animation now, for `game_state` and playtests: its graph state and parameters, or its clip. */
export function animationSnapshot(entity: Entity): AnimationSnapshot | undefined {
  const state = live.get(entity);
  if (!state) return undefined;
  if (state.graph) return { ...state.graph.snapshot(), playing: state.graph.running() };
  if (!state.clip) return undefined;
  return { clip: state.clip, playing: !!state.action?.isRunning() };
}

/** Blender names copies "Walk.001"; for one entity, "Walk" also finds its "Walk.001". */
function baseName(name: string): string {
  return name.replace(/\.\d{3,}$/, '');
}

/** Track targets resolve by node name (or uuid) under the root, like AnimationMixer does. */
function bindsUnder(root: Object3D, trackName: string): boolean {
  const { nodeName } = PropertyBinding.parseTrackName(trackName);
  return PropertyBinding.findNode(root, nodeName) !== null;
}

export class AnimationService implements AnimationAPI {
  events: AnimationEvent[] = [];

  private clipList: AnimationClip[] = [];
  private readonly states = new Map<Entity, EntityAnimation>();
  private readonly warned = new Set<string>();
  private world: World | null = null;
  private unsubscribe: (() => void) | null = null;

  constructor(private readonly warn: (message: string) => void = (m) => console.warn(m)) {}

  /**
   * Starts a new scene: forgets every mixer and uses `clips` (scene and prefab GLBs). Entities in
   * `keep` (carried into the scene) keep their mixers, and the old clips that animate them.
   */
  reset(world: World, clips: AnimationClip[], keep: ReadonlySet<Entity> = new Set()): void {
    const roots = [...keep].map((e) => e.object3d);
    const kept = roots.length
      ? this.clipList.filter((clip) => !clips.includes(clip) && clip.tracks.some((t) => roots.some((r) => bindsUnder(r, t.name))))
      : [];
    this.unsubscribe?.();
    this.unsubscribe = null;
    for (const entity of [...this.states.keys()]) if (!keep.has(entity)) this.forget(entity);
    this.events = [];
    this.warned.clear();
    this.world = world;
    this.clipList = [...clips, ...kept];
    this.unsubscribe = world.onChange((event) => {
      if (event.type === 'destroy') this.forget(event.entity);
      else if (event.type === 'remove' && event.component === componentName(Animator)) {
        const state = this.states.get(event.entity);
        if (state) {
          state.mixer.stopAllAction();
          state.fading.clear();
          state.action = null;
          state.clip = null;
          state.seen = null;
          state.graph = null;
          state.pendingGraph = null;
        }
      }
    });
  }

  dispose(): void {
    this.unsubscribe?.();
    this.unsubscribe = null;
    for (const entity of [...this.states.keys()]) this.forget(entity);
    this.events = [];
    this.warned.clear();
  }

  /** Applies Animator changes and advances every mixer by `dt` seconds. Once per simulated frame. */
  update(dt: number): void {
    this.events = [];
    if (this.world) {
      for (const entity of this.world.query(Animator)) this.syncAnimator(entity);
    }
    for (const state of this.states.values()) {
      if (state.graph) this.updateGraph(state, state.graph, dt);
      state.mixer.update(dt);
      for (const action of state.fading) {
        if (action.enabled) continue;
        action.stop();
        state.fading.delete(action);
      }
    }
  }

  // --- AnimationAPI --------------------------------------------------------

  play(entity: Entity, clip?: string, options: PlayOptions = {}): boolean {
    if (!entity.alive) return false;
    const animator = entity.tryGet<AnimatorValues>(Animator);
    const state = this.state(entity);
    const name = clip || animator?.clip || this.firstClip(state);
    if (!name) {
      this.warnOnce(`noclip:${entity.id}`, `[animation] No clip animates "${entity.name}"`);
      return false;
    }
    const bound = this.bind(state, name);
    if (!bound) {
      const available = this.clips(entity);
      this.warnOnce(
        `${entity.id}:${name}`,
        `[animation] Clip "${name}" does not animate "${entity.name}" or its children. ` +
          (available.length ? `Clips for it: ${available.join(', ')}` : 'No clip animates it.'),
      );
      return false;
    }
    const fade = Math.max(0, options.fade ?? animator?.fade ?? DEFAULT_FADE);
    const loop = options.loop ?? animator?.loop ?? true;
    const speed = options.speed ?? animator?.speed ?? 1;
    if (state.graph) this.detach(state, fade);

    const action = state.mixer.clipAction(bound);
    const previous = state.action;
    if (previous === action && !options.restart && action.isRunning()) {
      applyLoop(action, loop);
      action.timeScale = speed;
      return true;
    }
    state.fading.delete(action);
    action.reset();
    action.weight = 1; // A graph may have left it blended.
    applyLoop(action, loop);
    action.timeScale = speed;
    action.play();
    if (previous && previous !== action) {
      if (fade > 0) {
        previous.crossFadeTo(action, fade, false);
        state.fading.add(previous);
      } else {
        previous.stop();
      }
    }
    state.action = action;
    state.clip = bound.name;
    state.mixer.update(0); // Pose now, so this frame already renders the new clip.
    return true;
  }

  stop(entity: Entity, options: { fade?: number } = {}): void {
    const state = this.states.get(entity);
    if (state?.graph) this.detach(state, Math.max(0, options.fade ?? 0));
    if (!state?.action) return;
    const fade = Math.max(0, options.fade ?? 0);
    if (fade > 0) {
      state.action.fadeOut(fade);
      state.fading.add(state.action);
    } else {
      state.mixer.stopAllAction();
      state.fading.clear();
    }
    state.action = null;
    state.clip = null;
  }

  isPlaying(entity: Entity, clip?: string): boolean {
    const state = this.states.get(entity);
    if (state?.graph) return state.graph.running(clip);
    if (!state?.action || !state.action.isRunning()) return false;
    return clip === undefined || state.action.getClip() === this.bind(state, clip);
  }

  current(entity: Entity): string | null {
    const state = this.states.get(entity);
    if (state?.graph) return state.graph.currentClip();
    return state?.clip ?? null;
  }

  clips(entity?: Entity): string[] {
    const names = [...new Set(this.clipList.map((c) => c.name))];
    if (!entity) return names;
    const state = this.state(entity);
    return names.filter((name) => this.bind(state, name)?.name === name);
  }

  mixer(entity: Entity): AnimationMixer {
    return this.state(entity).mixer;
  }

  bone(entity: Entity, name: string): Bone | null {
    const bones = new Set<Bone>();
    entity.object3d.traverse((o) => {
      if ((o as Bone).isBone) bones.add(o as Bone);
      const skeleton = (o as SkinnedMesh).isSkinnedMesh ? (o as SkinnedMesh).skeleton : null;
      for (const b of skeleton?.bones ?? []) bones.add(b);
    });
    const list = [...bones];
    const clean = PropertyBinding.sanitizeNodeName(name);
    return (
      list.find((b) => b.name === name) ??
      list.find((b) => b.name === clean) ??
      list.find((b) => b.name.replace(/_\d+$/, '') === clean) ??
      null
    );
  }

  graph<P, S extends string>(entity: Entity, graph: AnimationGraph<P, S>): AnimationGraphHandle<P, S> | null;
  graph(entity: Entity, graph?: string): AnimationGraphHandle | null;
  graph(entity: Entity, graph?: string | AnimationGraph<any, any>): AnimationGraphHandle<any, any> | null {
    if (!entity.alive) return null;
    const existing = this.states.get(entity)?.graph;
    const name = typeof graph === 'string' ? graph : graph?.name;
    if (existing && (name === undefined || existing.name === name)) return existing.handle;
    const wanted = name ?? entity.tryGet<AnimatorValues>(Animator)?.graph;
    if (!wanted) return null;
    return this.attach(this.state(entity), wanted)?.handle ?? null;
  }

  // -------------------------------------------------------------------------

  /**
   * Starts graph `name` on the entity, fading out what played before over `fade` seconds (default:
   * the graph's `blend`). Null (warned once) if no such graph is defined.
   */
  private attach(state: EntityAnimation, name: string, fadeSeconds?: number): GraphRuntime | null {
    const graph = getAnimationGraph(name);
    if (!graph) {
      const known = listAnimationGraphs();
      this.warnOnce(
        `graph:${name}`,
        `[animation] No animation graph "${name}" for "${state.entity.name}". ` +
          (known.length ? `Graphs: ${known.join(', ')}` : 'Define it with defineAnimationGraph in a module the game imports (src/systems/).'),
      );
      return null;
    }
    state.pendingGraph = null;
    const fade = fadeSeconds ?? graph.blend;
    if (state.graph) this.detach(state, fade);
    if (state.action) {
      if (fade > 0) {
        state.action.fadeOut(fade);
        state.fading.add(state.action);
      } else state.action.stop();
      state.action = null;
      state.clip = null;
    }
    const runtime = new GraphRuntime(graph, {
      entity: state.entity,
      mixer: state.mixer,
      clip: (clip) => this.graphClip(state, graph.name, clip),
      claim: (action) => state.fading.delete(action),
      emit: (event) => this.events.push(event),
      warn: (key, message) => this.warnOnce(key, message),
    });
    state.graph = runtime;
    runtime.apply();
    state.mixer.update(0); // Pose now, like play().
    this.writeState(state);
    return runtime;
  }

  /** Hands the entity back from its graph: the graph's clips fade out over `fade` seconds. */
  private detach(state: EntityAnimation, fade: number): void {
    const graph = state.graph;
    if (!graph) return;
    state.graph = null;
    for (const action of graph.actions) {
      if (fade > 0) {
        action.fadeOut(fade);
        state.fading.add(action);
      } else action.stop();
    }
    this.writeState(state);
  }

  private updateGraph(state: EntityAnimation, graph: GraphRuntime, dt: number): void {
    const latest = getAnimationGraph(graph.name);
    if (latest && latest !== graph.graph) graph.rebind(latest); // Redefined: hot reload.
    graph.update(dt, state.entity.tryGet<AnimatorValues>(Animator)?.speed ?? 1);
    this.writeState(state);
  }

  /** Mirrors the graph state into `Animator.state`, where playtests and `game_state` see it change. */
  private writeState(state: EntityAnimation): void {
    const animator = state.entity.alive ? state.entity.tryGet<AnimatorValues>(Animator) : undefined;
    const now = state.graph?.state ?? '';
    if (animator && animator.state !== now) animator.state = now;
  }

  private graphClip(state: EntityAnimation, graph: string, name: string): AnimationClip | null {
    const bound = this.bind(state, name);
    if (!bound) {
      const available = this.clips(state.entity);
      this.warnOnce(
        `${state.entity.id}:${name}`,
        `[animation] Graph "${graph}": clip "${name}" does not animate "${state.entity.name}" or its children. ` +
          (available.length ? `Clips for it: ${available.join(', ')}` : 'No clip animates it.'),
      );
    }
    return bound;
  }

  private syncAnimator(entity: Entity): void {
    const data = entity.get<AnimatorValues>(Animator);
    const state = this.state(entity);
    const seen = state.seen;
    state.seen = { ...data };
    if (!seen) {
      if (!data.playOnStart) return;
      if (!data.graph) this.play(entity, data.clip || undefined, { fade: 0 });
      else if (!this.attach(state, data.graph, 0)) state.pendingGraph = data.graph;
      return;
    }
    if (data.graph !== seen.graph) {
      state.pendingGraph = null;
      if (data.graph) {
        if (!this.attach(state, data.graph)) state.pendingGraph = data.graph;
      } else {
        this.detach(state, data.fade);
        if (data.clip) this.play(entity, data.clip, { fade: data.fade });
      }
      return;
    }
    if (state.pendingGraph && !state.graph && getAnimationGraph(state.pendingGraph)) {
      this.attach(state, state.pendingGraph, 0); // Defined since: a module loaded later, or a hot reload.
      return;
    }
    if (state.graph) return; // The graph picks the clips; `speed` scales it (updateGraph).
    if (data.clip !== seen.clip) {
      if (data.clip) this.play(entity, data.clip, { fade: data.fade });
      else this.stop(entity, { fade: data.fade });
      return;
    }
    const action = state.action;
    // Code played another clip: leave it alone.
    if (!action || (data.clip && this.bind(state, data.clip) !== action.getClip())) return;
    if (data.loop !== seen.loop) applyLoop(action, data.loop);
    if (data.speed !== seen.speed) action.timeScale = data.speed;
  }

  private state(entity: Entity): EntityAnimation {
    let state = this.states.get(entity);
    if (!state) {
      const mixer = new AnimationMixer(entity.object3d);
      const created: EntityAnimation = {
        entity,
        mixer,
        bound: new Map(),
        action: null,
        clip: null,
        fading: new Set(),
        seen: null,
        graph: null,
        pendingGraph: null,
      };
      const report = (type: AnimationEvent['type']) => (event: { action: AnimationAction }) => {
        this.events.push({ type, entity, clip: event.action.getClip().name });
      };
      mixer.addEventListener('finished', report('finished'));
      mixer.addEventListener('loop', report('loop'));
      this.states.set(entity, created);
      live.set(entity, created);
      state = created;
    }
    return state;
  }

  /**
   * The named clip reduced to the tracks that bind under the entity, or null. An exact
   * name wins; otherwise a clip whose name differs only by Blender's ".001" suffix.
   */
  private bind(state: EntityAnimation, name: string): AnimationClip | null {
    if (state.bound.has(name)) return state.bound.get(name)!;
    const root = state.entity.object3d;
    const reduce = (clip: AnimationClip): AnimationClip | null => {
      const tracks = clip.tracks.filter((track) => bindsUnder(root, track.name));
      if (tracks.length === 0) return null;
      // Keep the authored duration so clips shared by several objects stay in step.
      return tracks.length === clip.tracks.length ? clip : new AnimationClip(clip.name, clip.duration, tracks);
    };
    let result: AnimationClip | null = null;
    for (const clip of this.clipList) if (!result && clip.name === name) result = reduce(clip);
    const base = baseName(name);
    for (const clip of this.clipList) {
      if (!result && clip.name !== name && baseName(clip.name) === base) result = reduce(clip);
    }
    state.bound.set(name, result);
    return result;
  }

  private firstClip(state: EntityAnimation): string | null {
    for (const clip of this.clipList) if (this.bind(state, clip.name)) return clip.name;
    return null;
  }

  private forget(entity: Entity): void {
    const state = this.states.get(entity);
    if (!state) return;
    state.mixer.stopAllAction();
    state.mixer.uncacheRoot(entity.object3d);
    state.graph = null;
    this.states.delete(entity);
    if (live.get(entity) === state) live.delete(entity);
  }

  private warnOnce(key: string, message: string): void {
    if (this.warned.has(key)) return;
    this.warned.add(key);
    this.warn(message);
  }
}

function applyLoop(action: AnimationAction, loop: boolean): void {
  action.setLoop(loop ? LoopRepeat : LoopOnce, Infinity);
  action.clampWhenFinished = !loop;
}
