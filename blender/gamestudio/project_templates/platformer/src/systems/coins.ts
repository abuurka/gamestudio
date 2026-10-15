import { CharacterController, defineSystem } from '@gamestudio/engine';
import { Vector3 } from 'three';
import { Coin } from '../components/coin';
import { sfx } from '../lib/sfx';

const PICKUP_DISTANCE = 1.1; // metres between the player's chest and the coin's centre
const CHEST = 0.9; // metres above the player's origin (its feet)
const SPIN_SPEED = 3; // rad/s
const BOB_HEIGHT = 0.12; // metres

const baseY = new Map<string, number>();
const coinPos = new Vector3();
const chest = new Vector3();

/**
 * Spins and bobs coins and collects them when a player touches them: ctx.store.coins and coinTotal (the
 * HUD's "{coins} / {coinTotal}") and the run's ctx.store.score, with a "+1" over the coin.
 */
export default defineSystem({
  name: 'CoinSystem',
  query: [Coin],

  start(ctx) {
    ctx.store.coins = 0;
    ctx.store.coinTotal = ctx.world.query(Coin).length;
    ctx.store.score ??= 0; // the run's score: it carries over to later levels
  },
  onAdd(_ctx, coin) {
    baseY.set(coin.id, coin.object3d.position.y);
  },
  onRemove(_ctx, coin) {
    baseY.delete(coin.id);
  },

  update(ctx, coins) {
    // Also counts coins added from Blender while the game runs.
    ctx.store.coinTotal = Math.max(ctx.store.coinTotal, ctx.store.coins + coins.length);
    const players = ctx.world.query(CharacterController);
    for (const coin of coins) {
      const o = coin.object3d;
      o.rotation.y += SPIN_SPEED * ctx.dt;
      o.position.y = (baseY.get(coin.id) ?? o.position.y) + Math.sin(ctx.time * 2.5 + o.position.x) * BOB_HEIGHT;
      o.getWorldPosition(coinPos);
      const touched = players.some((p) => p.object3d.getWorldPosition(chest).setY(chest.y + CHEST).distanceTo(coinPos) < PICKUP_DISTANCE);
      if (!touched) continue;
      const { value } = coin.get(Coin);
      ctx.store.coins += value;
      ctx.store.score += value;
      ctx.hud.set(`pop-${coin.id}`, `+${value}`, { at: coinPos.toArray(), color: '#ffd43b', duration: 0.6 });
      ctx.audio.play(sfx.coin, { volume: 0.5 });
      ctx.vfx.burst(coin, 'pickup'); // plays on where the coin was
      coin.destroy();
    }
  },
});
