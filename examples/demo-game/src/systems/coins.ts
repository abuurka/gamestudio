import { CharacterController, defineSystem } from '@gamestudio/engine';
import { Vector3 } from 'three';
import { Coin } from '../components/coin';
import { startedTouching } from '../lib/contact';

const PICKUP_DISTANCE = 1.2; // metres between player and coin centres
const SPIN_SPEED = 2.5; // radians per second (coins with a Spin component use that instead)

let total = 0; // coins in the scene when it started
let score = 0;
const coinPos = new Vector3();
const playerPos = new Vector3();

/** Spins coins, collects them when a player touches or nears them, and shows progress. */
export default defineSystem({
  name: 'CoinSystem',
  query: [Coin],
  phase: 'update',

  start(ctx) {
    total = ctx.world.query(Coin).length;
    score = 0;
    ctx.hud.clear('coins');
    ctx.hud.clear('win');
  },

  update(ctx, coins) {
    total = Math.max(total, coins.length); // Also correct after this file is hot-reloaded.
    const players = ctx.world.query(CharacterController);

    for (const coin of coins) {
      if (!coin.has('Spin')) coin.object3d.rotateY(SPIN_SPEED * ctx.dt);
      coin.object3d.getWorldPosition(coinPos);
      const collected = players.some(
        (p) => p.object3d.getWorldPosition(playerPos).distanceTo(coinPos) < PICKUP_DISTANCE || startedTouching(ctx, p, coin),
      );
      if (collected) {
        score += coin.get(Coin).value;
        coin.destroy();
        ctx.log(`Coin collected, score ${score}`);
      }
    }

    if (total === 0) return;
    const remaining = ctx.world.query(Coin).length;
    ctx.hud.set('coins', `Coins ${total - remaining} / ${total}`);
    if (remaining === 0) ctx.hud.set('win', 'You collected every coin!', { position: 'center', size: 'large' });
  },
});
