// Locomotion: picks idle, walk, run, jump and fall clips from how an entity moves.
// Speed comes from the entity's own movement, air state from its CharacterController.

import { Vector3 } from 'three';
import { setGraphMotionProvider } from '../animation/graph';
import type { Entity } from '../ecs/entity';
import { defineSystem } from '../ecs/system';
import { CharacterController, Locomotion } from '../schema/builtins';
import type { ComponentValues } from '../schema/registry';
import { characterMotion } from './character-controller';

// Animation graph parameters with `from: 'grounded'` and the like read the CharacterController of
// the animated entity or of its nearest ancestor (the armature may be a child of the player).
setGraphMotionProvider((entity) => {
  for (let e: Entity | undefined = entity; e; e = e.parentId ? e._world.get(e.parentId) : undefined) {
    if (e.has(CharacterController)) return characterMotion(e);
  }
  return null;
});

/** Off the ground this long (s) before falling plays: steps and bumps don't count. */
const AIR_DELAY = 0.12;
/** Faster than this (m/s) is a teleport (a respawn), not movement. */
const TELEPORT_SPEED = 60;

interface Gait {
  last: Vector3 | null;
  /** Smoothed horizontal speed, m/s. */
  speed: number;
  /** Seconds off the ground, and whether that counts as being in the air yet. */
  air: number;
  airborne: boolean;
  clips: Set<string> | null;
}

const gaits = new WeakMap<Entity, Gait>();
const position = new Vector3();

function gaitOf(e: Entity): Gait {
  let gait = gaits.get(e);
  if (!gait) gaits.set(e, (gait = { last: null, speed: 0, air: 0, airborne: false, clips: null }));
  return gait;
}

/** Plays idle, walk, run, jump and fall clips on entities with `Locomotion` (API.md §15). */
export const LocomotionSystem = defineSystem({
  name: 'LocomotionSystem',
  query: [Locomotion],
  phase: 'update',
  update(ctx, entities) {
    for (const e of entities) {
      const l = e.get<ComponentValues<typeof Locomotion>>(Locomotion);
      const gait = gaitOf(e);
      e.object3d.getWorldPosition(position);
      if (gait.last && ctx.dt > 0) {
        const measured = Math.hypot(position.x - gait.last.x, position.z - gait.last.z) / ctx.dt;
        if (measured < TELEPORT_SPEED) gait.speed += (measured - gait.speed) * Math.min(1, ctx.dt * 15);
      }
      (gait.last ??= new Vector3()).copy(position);

      const motion = e.has(CharacterController) ? characterMotion(e) : null;
      const grounded = !motion || motion.grounded;
      const rising = !grounded && motion!.verticalVelocity > 0.5;
      gait.air = grounded ? 0 : gait.air + ctx.dt;
      gait.airborne = !grounded && (gait.airborne || rising || gait.air > AIR_DELAY);
      let wanted: string[];
      if (gait.airborne) {
        wanted = rising ? [l.jump, l.fall, l.idle] : [l.fall, l.jump, l.idle];
      } else if (gait.speed >= l.runSpeed) wanted = [l.run, l.walk, l.idle];
      else if (gait.speed >= l.walkSpeed) wanted = [l.walk, l.run, l.idle];
      else wanted = [l.idle];

      gait.clips ??= new Set(ctx.animation.clips(e).flatMap((c) => [c, c.replace(/\.\d{3,}$/, '')]));
      const clip = wanted.find((name) => name && gait.clips!.has(name));
      if (!clip || clip === l.clip) continue;
      // A jump plays once (holding its last frame) unless the fall clip is missing too.
      const once = clip === l.jump && rising;
      if (ctx.animation.play(e, clip, { fade: l.fade, loop: !once, restart: once })) l.clip = clip;
    }
  },
});
