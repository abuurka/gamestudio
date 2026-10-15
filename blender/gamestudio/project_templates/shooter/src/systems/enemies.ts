import { defineSystem, NavAgent, type Entity } from '@gamestudio/engine';
import { Enemy } from '../components/enemy';
import { Health } from '../components/health';
import { Shooter } from '../components/shooter';
import { arena } from '../lib/arena';
import { damage } from '../lib/health';
import { sfx } from '../lib/sfx';

const FACE = 2.5; // within this many reaches an enemy turns to face the player instead of where it walks
const nextHit = new Map<Entity, number>(); // ctx.time each enemy may hit again

/**
 * Enemies chase the nearest player and hit it when in reach. Their NavAgent does the walking: a path
 * on the level's navmesh around pillars, low walls and crates, re-planned twice a second as the
 * player moves, with Rapier's character mover sliding them along each other. The Enemy fields stay
 * the balance knobs: the agent takes its speed and where to stop from them every step.
 */
export default defineSystem({
  name: 'EnemySystem',
  query: [Enemy],
  phase: 'fixed', // in step with the physics, like the NavAgent and the CharacterController

  onAdd(_ctx, enemy) {
    // The prefabs carry a NavAgent; one made without it gets it here.
    if (!enemy.has(NavAgent)) enemy.add(NavAgent, { acceleration: 30, separation: 0.9 });
  },

  onRemove(_ctx, enemy) {
    nextHit.delete(enemy);
  },

  update(ctx, enemies) {
    const players = ctx.world.query(Shooter, Health);
    for (const enemy of enemies) {
      const e = enemy.get(Enemy);
      const agent = enemy.tryGet(NavAgent);
      const pos = enemy.object3d.position; // kinematic bodies sit at the scene root: local = world
      const player = players.length
        ? players.reduce((a, b) => (a.object3d.position.distanceToSquared(pos) <= b.object3d.position.distanceToSquared(pos) ? a : b))
        : undefined;
      if (agent) {
        agent.target = player && !arena.over ? player.id : null; // game over: they stop where they are
        agent.speed = e.speed;
        agent.stoppingDistance = e.reach * 0.8; // a step inside reach, so the hits land
      }
      if (!player) continue;
      const dx = player.object3d.position.x - pos.x;
      const dz = player.object3d.position.z - pos.z;
      const distance = Math.hypot(dx, dz);
      const close = distance <= e.reach * FACE;
      if (agent) agent.turnSpeed = close ? 0 : 12;
      if (close) enemy.object3d.rotation.y = Math.atan2(dx, dz); // face the player

      if (distance <= e.reach && !arena.over && ctx.time >= (nextHit.get(enemy) ?? 0)) {
        nextHit.set(enemy, ctx.time + e.attackInterval);
        damage(player, e.damage, ctx.time);
        ctx.audio.play(sfx.hurt, { volume: 0.5 });
      }
    }
  },
});
