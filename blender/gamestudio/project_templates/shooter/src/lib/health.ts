// Hit points are the Health component's `current` field, so game_state and playtest tracks show
// them. HealthSystem fills it to `max` when an entity appears; a scene reload makes new entities,
// which start at full health. Only the time of the last hit (for the white flash) lives here.
import type { Entity } from '@gamestudio/engine';
import { Health } from '../components/health';

const lastHit = new WeakMap<Entity, number>();

/** Current hit points. */
export function hitPoints(e: Entity): number {
  return e.get(Health).current;
}

/** Takes `amount` hit points at game time `time`; returns what is left. */
export function damage(e: Entity, amount: number, time: number): number {
  const health = e.get(Health);
  health.current = Math.max(0, health.current - amount);
  lastHit.set(e, time);
  return health.current;
}

/** Seconds since `e` was last hit (Infinity if never). */
export function sinceHit(e: Entity, time: number): number {
  const at = lastHit.get(e);
  return at === undefined ? Infinity : time - at;
}
