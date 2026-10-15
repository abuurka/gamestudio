// Technique: a spawn point placed in Blender, found the Unity way. The level has an empty named
// "PlayerStart"; this system finds it by name (Unity's GameObject.Find), spawns the Runner prefab at its
// world transform and points the camera's FollowCamera at the new entity.
// It also keeps the HUD's values in ctx.store, opens the GameOver screen and keeps the best distance
// (ctx.save). The screens are made in Blender (ui/ui.blend, tools/build_ui.py): the Title opens on the
// first start, the Pause menu on Escape, and the HUD's texts and bars bind the ctx.store values below.
import { defineSystem, FollowCamera, type Entity, type SystemContext } from '@gamestudio/engine';
import { Quaternion, Vector3 } from 'three';
import { Runner } from '../components/runner';
import { banner, run, score } from '../lib/run';

const PLAYER_PREFAB = 'Runner';
const MILESTONE = 500; // metres: a banner and confetti every this many
const CONTROLS = { from: 0.1, to: 6 }; // seconds into a run while the HUD shows the controls (not under the title)

let milestone = 0;
let ending = false;

/** Spawns the player prefab at the PlayerStart empty (once: a hot reload keeps the running one). */
function spawnPlayer(ctx: SystemContext): Entity | undefined {
  let player = ctx.world.query(Runner)[0];
  if (!player) {
    const start = ctx.world.findByName('PlayerStart'); // or ctx.world.query(SomeMarker)[0]
    if (!start) {
      ctx.log.warn('RunSystem: the level has no "PlayerStart" empty, so there is no player');
      return undefined;
    }
    player = ctx.spawn(PLAYER_PREFAB, {
      position: start.object3d.getWorldPosition(new Vector3()),
      rotation: start.object3d.getWorldQuaternion(new Quaternion()),
      name: 'Player',
    });
  }
  for (const camera of ctx.world.query(FollowCamera)) camera.get(FollowCamera).target = player.id; // a per-instance override
  return player;
}

/** The GameOver screen: its texts bind {distance}, {coins}, {score} and {save.bestDistance}; it pauses the game. */
function gameOver(ctx: SystemContext): void {
  const distance: number = ctx.store.distance ?? 0;
  const coins: number = ctx.store.coins ?? 0;
  if (distance > ctx.save.get('bestDistance', 0)) {
    ctx.save.set('bestDistance', distance);
    ctx.ui.show('GameOver/NewBest'); // the ribbon; Try again (ctx.restart()) hides it again
  }
  ctx.post.set({ saturation: 0.25 }); // a grey world behind the screen; start() drops it
  ctx.log(`Game over: ${distance} m, ${coins} coins, score ${score(distance, coins)}`);
  ctx.ui.text('GameOver/OverTitle', run.cause === 'fall' ? 'You fell!' : 'Crashed!');
  ctx.ui.open('GameOver');
}

/**
 * The player at PlayerStart, the HUD's values (ctx.store: distance, speed and score; CoinSystem keeps
 * coins, PowerUpSystem magnetLeft and shieldLeft), milestones, and the GameOver screen. R restarts.
 */
export default defineSystem({
  name: 'RunSystem',

  start(ctx) {
    Object.assign(run, { over: false, cause: 'crash' });
    ending = false;
    milestone = 0;
    Object.assign(ctx.store, { distance: 0, speed: 0, score: 0, banner: '' });
    ctx.store.coins ??= 0;
    if (!ctx.save.has('bestDistance')) ctx.save.set('bestDistance', 0); // so "Best {save.bestDistance} m" never shows empty
    ctx.post.set(null); // colour back after a game over
    spawnPlayer(ctx);
  },

  update(ctx) {
    if (ctx.input.keyDown('KeyR')) void ctx.restart();
    const runner = ctx.world.query(Runner)[0];
    const r = runner?.get(Runner);
    const distance = Math.floor(r?.distance ?? 0);
    ctx.store.distance = distance; // whole metres run
    ctx.store.speed = r?.speed ?? 0; // m/s now
    ctx.store.score = score(distance, ctx.store.coins ?? 0);
    ctx.ui.show('HUD/Controls', !run.over && ctx.time > CONTROLS.from && ctx.time < CONTROLS.to);
    if (runner && distance >= milestone + MILESTONE && !run.over) {
      milestone += MILESTONE;
      banner(ctx, `${milestone} m!`, 1.5);
      ctx.vfx.burst(runner, 'confetti');
    }
    if (run.over && !ending) {
      ending = true;
      ctx.after(1.2, gameOver); // the crash or the fall first: the screen pauses the game
    }
  },
});
