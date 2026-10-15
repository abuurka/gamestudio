// Runs registered systems for a world: start hooks, onAdd/onRemove membership
// tracking and per-phase updates, isolating errors per system.

import type { SystemContext } from '../context';
import type { Entity } from './entity';
import { listSystems, type Phase, type SystemDef } from './system';

interface SystemState {
  started: boolean;
  members: Set<Entity>;
}

export type SystemErrorHandler = (system: SystemDef, error: unknown, hook: string) => void;

export class Scheduler {
  // Keyed by definition object: a hot-reloaded system gets fresh state.
  private states = new WeakMap<SystemDef, SystemState>();

  constructor(private readonly onError: SystemErrorHandler) {}

  /** Forget all per-system state so start/onAdd hooks run again (scene load or reset). */
  reset(): void {
    this.states = new WeakMap();
  }

  /** Runs pending start hooks and onAdd/onRemove membership changes. Call once per simulated frame. */
  prepare(ctx: SystemContext): void {
    for (const system of listSystems()) {
      const state = this.state(system);
      if (!state.started) {
        state.started = true;
        if (system.start) this.call(system, 'start', () => system.start!(ctx));
      }
      if (system.onAdd || system.onRemove) this.syncMembers(system, state, ctx);
    }
  }

  /** Runs every system of `phase` with `ctx.dt` already set by the caller. */
  run(phase: Phase, ctx: SystemContext): void {
    ctx.phase = phase;
    for (const system of listSystems()) {
      if ((system.phase ?? 'update') !== phase || !system.update) continue;
      const entities = system.query ? ctx.world.query(...system.query) : [];
      this.call(system, 'update', () => system.update!(ctx, entities));
    }
  }

  private syncMembers(system: SystemDef, state: SystemState, ctx: SystemContext): void {
    const current = system.query ? ctx.world.query(...system.query) : [];
    const currentSet = new Set(current);
    for (const entity of state.members) {
      if (!currentSet.has(entity)) {
        state.members.delete(entity);
        if (system.onRemove) this.call(system, 'onRemove', () => system.onRemove!(ctx, entity));
      }
    }
    for (const entity of current) {
      if (!state.members.has(entity)) {
        state.members.add(entity);
        if (system.onAdd) this.call(system, 'onAdd', () => system.onAdd!(ctx, entity));
      }
    }
  }

  private state(system: SystemDef): SystemState {
    let state = this.states.get(system);
    if (!state) {
      state = { started: false, members: new Set() };
      this.states.set(system, state);
    }
    return state;
  }

  private call(system: SystemDef, hook: string, fn: () => void): void {
    try {
      fn();
    } catch (error) {
      this.onError(system, error, hook);
    }
  }
}
