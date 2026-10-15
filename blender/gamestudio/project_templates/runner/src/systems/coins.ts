import { defineSystem } from '@gamestudio/engine';
import { Vector3 } from 'three';
import { Coin } from '../components/coin';
import { Runner } from '../components/runner';
import { sfx } from '../lib/sfx';

const CHEST = 1.0; // metres above the runner's feet
const PICKUP = 1.1; // metres from its chest to a coin's centre
const MAGNET_RADIUS = 8; // metres: coins this close fly to the runner while it has a magnet
const MAGNET_SPEED = 18; // m/s on top of the runner's own speed
const SPIN = 4; // rad/s
const STREAK = 0.5; // seconds: coins picked up closer together than this ring higher and higher

const chest = new Vector3();
const at = new Vector3();
let streak = 0;
let lastCoin = -1;

/**
 * Coins spin, fly to the runner while it has a magnet, and count when it touches them (ctx.store.coins).
 * Their value is set per instance by lib/sockets.ts: further into the run, coins are worth more.
 */
export default defineSystem({
  name: 'CoinSystem',
  query: [Coin],
  phase: 'fixed', // every physics step, so a fast runner never skips a coin between frames

  start(ctx) {
    ctx.store.coins ??= 0; // the run's coins: Restart clears ctx.store
    streak = 0;
    lastCoin = -1;
  },

  update(ctx, coins) {
    const runner = ctx.world.query(Runner)[0];
    if (!runner) return;
    const r = runner.get(Runner);
    chest.copy(runner.object3d.position).setY(runner.object3d.position.y + CHEST);
    for (const coin of coins) {
      const o = coin.object3d;
      o.rotation.y += SPIN * ctx.dt;
      o.getWorldPosition(at);
      const distance = at.distanceTo(chest);
      if (r.magnet > 0 && distance < MAGNET_RADIUS && distance > PICKUP) {
        at.lerp(chest, Math.min(1, ((MAGNET_SPEED + Math.max(0, r.speed)) * ctx.dt) / distance));
        o.position.copy(o.parent ? o.parent.worldToLocal(at.clone()) : at); // coins sit inside their line or arc
      }
      if (at.distanceTo(chest) > PICKUP) continue;
      const { value } = coin.get(Coin);
      ctx.store.coins += value;
      streak = ctx.time - lastCoin < STREAK ? streak + 1 : 0;
      lastCoin = ctx.time;
      ctx.audio.play(sfx.coin, { volume: 0.4, rate: 1 + Math.min(streak, 8) * 0.05 });
      ctx.vfx.burst(at, 'pickup', { scale: 0.6, amount: 0.6 });
      if (value > 1) ctx.hud.set(`coin-${coin.id}`, `+${value}`, { at: at.toArray(), color: '#ffd43b', duration: 0.5 });
      coin.destroy();
    }
  },
});
