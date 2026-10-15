import { CharacterController, defineSystem, type Entity } from '@gamestudio/engine';
import { Box3, Vector3 } from 'three';
import { KillZone } from '../components/kill-zone';
import { PlayerStart } from '../components/player-start';
import { boundsOverlap, startedTouching } from '../lib/contact';

const FALL_LIMIT = -20; // metres

// Where each player was when the scene started (fresh handles after every reload/reset).
const spawns = new Map<Entity, Vector3>();
const position = new Vector3();
const box = new Box3();

/** Respawns players that fall below y = -20 or touch a KillZone. */
export default defineSystem({
  name: 'RespawnSystem',
  query: [CharacterController],
  phase: 'update',

  onAdd(_ctx, player) {
    spawns.set(player, player.object3d.getWorldPosition(new Vector3()));
  },
  onRemove(_ctx, player) {
    spawns.delete(player);
  },

  update(ctx, players) {
    const zones = ctx.world.query(KillZone);
    const start = ctx.world.query(PlayerStart)[0];
    for (const player of players) {
      player.object3d.getWorldPosition(position);
      const fell = position.y < FALL_LIMIT;
      const killed = zones.some((zone) => startedTouching(ctx, player, zone) || boundsOverlap(player, zone));
      if (!fell && !killed) continue;

      let target = spawns.get(player);
      if (start) {
        // PlayerStart marks the floor; lift the player's centre by half its height.
        const halfHeight = box.setFromObject(player.object3d).getSize(new Vector3()).y / 2;
        target = start.object3d.getWorldPosition(new Vector3()).add(new Vector3(0, halfHeight + 0.05, 0));
      }
      if (!target) continue;
      ctx.physics.teleport(player, target);
      ctx.log(`${player.name} respawned`);
    }
  },
});
