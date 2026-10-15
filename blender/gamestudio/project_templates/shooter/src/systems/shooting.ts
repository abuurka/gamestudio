import { defineSystem, type Entity, type ParticleOptions } from '@gamestudio/engine';
import { Shooter } from '../components/shooter';
import { arena } from '../lib/arena';
import { sfx } from '../lib/sfx';

const MUZZLE_FORWARD = 0.8; // metres in front of the player's centre
const nextShot = new Map<Entity, number>(); // ctx.time each shooter may fire again
// A muzzle flash: one bright dot for a few frames (brightness above 1 glows with bloom).
const FLASH: ParticleOptions = {
  texture: 'dot',
  burst: 1,
  rate: 0,
  lifetime: [0.06, 0.06],
  speed: [0, 0],
  size: [0.35, 0.15],
  colorStart: '#ffd43b',
  colorEnd: '#ff922b',
  brightness: 1.6,
};

/** Spawns the shooter's projectile prefab while the left button, right trigger or right stick is held. */
export default defineSystem({
  name: 'ShootingSystem',
  query: [Shooter],

  update(ctx, shooters) {
    const pad = ctx.input.gamepad;
    const firing =
      ctx.input.mouseButton(0) || ctx.input.gamepadButton(7) || Math.hypot(pad?.axes[2] ?? 0, pad?.axes[3] ?? 0) > 0.5;
    if (!firing || arena.over) return;
    for (const player of shooters) {
      if (ctx.time < (nextShot.get(player) ?? 0)) continue;
      const s = player.get(Shooter);
      nextShot.set(player, ctx.time + 1 / s.fireRate);
      const yaw = player.object3d.rotation.y + ctx.random.range(-s.spread, s.spread);
      const p = player.object3d.position;
      const muzzle: [number, number, number] = [p.x + Math.sin(yaw) * MUZZLE_FORWARD, p.y + s.muzzleHeight, p.z + Math.cos(yaw) * MUZZLE_FORWARD];
      ctx.spawn(s.projectile, { position: muzzle, rotation: [0, yaw, 0] });
      ctx.vfx.burst(muzzle, FLASH);
      ctx.audio.play(sfx.shoot, { volume: 0.25 });
    }
  },
});
