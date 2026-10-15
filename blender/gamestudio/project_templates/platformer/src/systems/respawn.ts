import { Animator, CharacterController, defineSystem, type Entity, type SystemContext } from '@gamestudio/engine';
import { Vector3 } from 'three';
import { Checkpoint } from '../components/checkpoint';
import { KillZone } from '../components/kill-zone';
import { boundsOverlap, separation } from '../lib/contact';
import { START_LIVES } from '../lib/level';
import { sfx } from '../lib/sfx';

const FALL_LIMIT = -3; // metres (runtime y): below this the player has fallen off the level
const LIFT = 0.1; // metres above the respawn point, so the player drops onto the floor
const FLAG_TOP = new Vector3(1, 3, 0); // the gold ball on top of a checkpoint's pole, in its local space

const respawnPoint = new Map<Entity, Vector3>(); // where each player comes back (at floor level)
const reached = new Set<string>(); // checkpoint ids already passed
const target = new Vector3();

function gameOver(ctx: SystemContext): void {
  ctx.log(`Game over with ${ctx.store.coins} / ${ctx.store.coinTotal} coins`);
  ctx.ui.open('GameOver'); // ui/ui.blend: the coins and Try again
}

/**
 * Checkpoints set the respawn point; falling off the level or touching a KillZone costs a life
 * (ctx.store.lives: the HUD's hearts) and returns the player there. No lives left: the GameOver screen.
 */
export default defineSystem({
  name: 'RespawnSystem',
  query: [CharacterController],

  start(ctx) {
    reached.clear();
    ctx.store.lives ??= START_LIVES; // the run's lives: they carry over to later levels
  },
  onAdd(_ctx, player) {
    respawnPoint.set(player, player.object3d.getWorldPosition(new Vector3())); // the start, until a checkpoint
  },
  onRemove(_ctx, player) {
    respawnPoint.delete(player);
  },

  update(ctx, players) {
    if (ctx.store.lives <= 0) return; // the game over screen is on its way
    const checkpoints = ctx.world.query(Checkpoint);
    const zones = ctx.world.query(KillZone);
    for (const player of players) {
      for (const checkpoint of checkpoints) {
        if (reached.has(checkpoint.id)) continue;
        const { horizontal, vertical } = separation(player, checkpoint);
        if (horizontal > checkpoint.get(Checkpoint).radius || Math.abs(vertical) > 3) continue;
        reached.add(checkpoint.id);
        respawnPoint.set(player, checkpoint.object3d.getWorldPosition(new Vector3()));
        // The flag is a child with an Animator: play its clip (an action authored in Blender) once.
        const flag = ctx.world.query(Animator).find((e) => e.parentId === checkpoint.id);
        if (flag) ctx.animation.play(flag, undefined, { loop: false });
        const top = checkpoint.object3d.localToWorld(target.copy(FLAG_TOP));
        ctx.vfx.burst(top, 'sparkle', { scale: 1.5, amount: 1.5 });
        ctx.audio.play(sfx.checkpoint, { volume: 0.6 });
        ctx.hud.set('checkpoint', 'Checkpoint!', { at: top.toArray(), offset: [0, 0.8, 0], color: '#69db7c', duration: 1.2 });
      }

      const fell = player.object3d.position.y < FALL_LIMIT;
      const burned = zones.some((zone) => boundsOverlap(player, zone));
      if (!fell && !burned) continue;
      const point = respawnPoint.get(player);
      if (!point) continue;
      if (burned) ctx.vfx.burst(player, 'smoke');
      ctx.physics.teleport(player, target.copy(point).setY(point.y + LIFT));
      ctx.vfx.burst(target.setY(point.y + 0.9), 'sparkle', { scale: 1.6 }); // pop back in, around the body
      ctx.audio.play(sfx.fall, { volume: 0.6 });
      ctx.store.lives -= 1;
      if (ctx.store.lives <= 0) {
        ctx.after(0.6, gameOver);
        return;
      }
      ctx.hud.set('oops', burned ? 'Ouch!' : 'Splash!', { at: player, offset: [0, 2.3, 0], color: '#ff8787', duration: 1 });
    }
  },
});
