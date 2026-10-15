// Technique: event-driven spawns. Picking up a power-up gives the runner a timer (Runner.magnet or
// Runner.shield, fields that playtests and game_state show) and spawns the power-up's aura prefab as a
// child of the runner, so it moves with it. The aura goes when the timer runs out. Auras follow the
// timers, so setting Runner.shield from a playtest or Blender shows the bubble too. The HUD's power-up
// rows (ui/ui.blend) show ctx.store.magnetLeft and shieldLeft, and only while they are above 0.
import { defineSystem, type Entity, type SystemContext } from '@gamestudio/engine';
import { Vector3 } from 'three';
import { PowerUp } from '../components/power-up';
import { Runner } from '../components/runner';
import { banner } from '../lib/run';
import { sfx } from '../lib/sfx';

type Kind = 'magnet' | 'shield';
const KINDS: Kind[] = ['magnet', 'shield'];
const REACH = 1.6; // metres from the runner's chest to a pickup's origin
const CHEST = 1.0; // metres above the runner's feet
const LOOK = { magnet: { label: 'Magnet', color: '#ff6b9a' }, shield: { label: 'Shield', color: '#4dd4ff' } };
const LEFT = { magnet: 'magnetLeft', shield: 'shieldLeft' } as const; // ctx.store keys: 0-1 of the time left, for the HUD bars

// The aura prefab of each power, from the last pickup of that kind (these until one is picked up).
const auraPrefab: Record<Kind, string> = { magnet: 'MagnetAura', shield: 'ShieldBubble' };
const auras = new Map<Kind, Entity>();
const full: Record<Kind, number> = { magnet: 8, shield: 8 }; // seconds of the last pickup: a full HUD bar
const chest = new Vector3();
const at = new Vector3();

/** Spawns or removes the runner's aura for `kind` to match its timer. */
function syncAura(ctx: SystemContext, runner: Entity, kind: Kind, left: number): void {
  const aura = auras.get(kind);
  if (left > 0 && !aura?.alive) {
    try {
      auras.set(kind, ctx.spawn(auraPrefab[kind], { parent: runner })); // at the runner's feet, moving with it
    } catch (error) {
      ctx.log.warn(`PowerUpSystem: no aura "${auraPrefab[kind]}": ${error instanceof Error ? error.message : error}`);
      auraPrefab[kind] = ''; // warn once; a pickup can name another
    }
  } else if (left <= 0 && aura) {
    ctx.destroy(aura);
    auras.delete(kind);
  }
}

/** Collects power-ups, counts their timers down and keeps the auras and the HUD's bars in step. */
export default defineSystem({
  name: 'PowerUpSystem',
  query: [PowerUp],
  phase: 'fixed', // with the runner's movement, like coins

  start(ctx) {
    auras.clear();
    for (const kind of KINDS) ctx.store[LEFT[kind]] = 0;
  },

  update(ctx, pickups) {
    const runner = ctx.world.query(Runner)[0];
    if (!runner) return;
    const r = runner.get(Runner);
    chest.copy(runner.object3d.position).setY(runner.object3d.position.y + CHEST);
    for (const pickup of pickups) {
      if (pickup.object3d.getWorldPosition(at).distanceTo(chest) > REACH) continue;
      const p = pickup.get(PowerUp);
      r[p.kind] = p.duration;
      full[p.kind] = Math.max(0.1, p.duration);
      if (p.aura) auraPrefab[p.kind] = p.aura;
      auras.get(p.kind)?.destroy(); // a fresh aura for a fresh pickup
      auras.delete(p.kind);
      ctx.vfx.burst(at, 'sparkle', { color: LOOK[p.kind].color, scale: 1.6, amount: 2 });
      ctx.audio.play(sfx.powerup, { volume: 0.6 });
      banner(ctx, `${LOOK[p.kind].label}!`, 1);
      ctx.log(`${LOOK[p.kind].label} for ${p.duration} s`);
      pickup.destroy();
    }
    for (const kind of KINDS) {
      r[kind] = Math.max(0, r[kind] - ctx.dt);
      syncAura(ctx, runner, kind, auraPrefab[kind] ? r[kind] : 0);
      ctx.store[LEFT[kind]] = r[kind] > 0 ? Math.min(1, r[kind] / full[kind]) : 0;
    }
  },
});
