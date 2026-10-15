// System registry (docs/SPEC.md §6.4). Systems are registered globally by name;
// redefining a name replaces the old definition in place (same run order),
// which is how hot module replacement swaps gameplay code.

import type { SystemContext } from '../context';
import type { ComponentRef, Entity } from './entity';

export type Phase = 'fixed' | 'update' | 'late';
export const PHASES: readonly Phase[] = ['fixed', 'update', 'late'];

export interface SystemDef {
  /** Unique name; redefining it replaces the system (HMR). */
  name: string;
  /** Entities passed to `update` have all of these components. Omit for a global system. */
  query?: readonly ComponentRef[];
  /** 'fixed' runs at 60 Hz before each physics step; 'update' and 'late' run once per frame. Default 'update'. */
  phase?: Phase;
  /** Called every tick of the system's phase while the game is playing. */
  update?(ctx: SystemContext, entities: readonly Entity[]): void;
  /** Called once before the first update after a scene loads or resets, and after a hot reload. */
  start?(ctx: SystemContext): void;
  /** Called when an entity starts matching `query` (also for existing entities after start/HMR). */
  onAdd?(ctx: SystemContext, entity: Entity): void;
  /** Called when an entity stops matching `query` or is destroyed. Its components may be gone. */
  onRemove?(ctx: SystemContext, entity: Entity): void;
}

const systems = new Map<string, SystemDef>();

/** Registers (or replaces) a system and returns its definition. */
export function defineSystem<S extends SystemDef>(def: S): S {
  if (!def || typeof def.name !== 'string' || def.name === '') throw new Error('defineSystem needs a name');
  const phase = def.phase ?? 'update';
  if (!PHASES.includes(phase)) throw new Error(`System "${def.name}": unknown phase "${phase}"`);
  systems.set(def.name, def);
  return def;
}

/** Unregisters a system by name. */
export function removeSystem(name: string): boolean {
  return systems.delete(name);
}

/** Registered systems in registration order. */
export function listSystems(): SystemDef[] {
  return [...systems.values()];
}
