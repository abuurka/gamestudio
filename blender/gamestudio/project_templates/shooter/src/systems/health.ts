import { CharacterController, defineSystem, type Entity, type SystemContext } from '@gamestudio/engine';
import { Mesh, MeshStandardMaterial, type Material, type Object3D } from 'three';
import { Enemy } from '../components/enemy';
import { Health } from '../components/health';
import { arena } from '../lib/arena';
import { hitPoints, sinceHit } from '../lib/health';
import { sfx } from '../lib/sfx';

const FLASH_TIME = 0.08; // seconds a hit entity shows white
const flashMaterial = new MeshStandardMaterial({ color: 0xffffff, emissive: 0xffffff, emissiveIntensity: 0.6 });
const originals = new WeakMap<Mesh, Material | Material[]>();

/** Swaps the entity's meshes to the flash material and back (per mesh, so other instances keep theirs). */
function flash(object: Object3D, on: boolean): void {
  object.traverse((o) => {
    if (!(o instanceof Mesh)) return;
    if (on && !originals.has(o)) {
      originals.set(o, o.material);
      o.material = flashMaterial;
    } else if (!on && originals.has(o)) {
      o.material = originals.get(o)!;
      originals.delete(o);
    }
  });
}

function defeat(ctx: SystemContext, enemy: Entity): void {
  const points = enemy.get(Enemy).points;
  ctx.store.score += points;
  arena.kills += 1;
  const p = enemy.object3d.position;
  ctx.log(`${enemy.name} defeated: score ${ctx.store.score}`);
  ctx.hud.set(`pop-${enemy.id}`, `+${points}`, { at: [p.x, p.y + 1.9, p.z], color: '#ffd43b', duration: 0.7 });
  ctx.audio.play(sfx.enemyDown, { at: [p.x, p.y, p.z], volume: 0.6 });
  ctx.vfx.burst([p.x, p.y + 0.8, p.z], 'explosion', { scale: 0.7 });
  ctx.destroy(enemy);
}

function gameOver(ctx: SystemContext): void {
  const score: number = ctx.store.score;
  if (score > ctx.save.get('bestScore', 0)) {
    ctx.save.set('bestScore', score); // ctx.save survives page reloads
    ctx.ui.hide('GameOver/OverBest'); // "Best {save.bestScore}" makes way for "New best score!"
    ctx.ui.show('GameOver/NewBest');
  }
  ctx.post.set({ saturation: 0.2 }); // a grey arena behind the screen; HealthSystem.start drops it
  ctx.ui.open('GameOver'); // ui/ui.blend: the score, the wave reached ({wave}) and Try again (restart)
}

function lose(ctx: SystemContext, player: Entity): void {
  arena.over = true;
  const controller = player.tryGet(CharacterController);
  if (controller) controller.speed = 0; // Restart reloads the scene, which restores it
  ctx.log(`Game over: score ${ctx.store.score}, wave ${arena.wave}`);
  const p = player.object3d.position;
  ctx.vfx.burst([p.x, p.y + 0.9, p.z], 'explosion');
  player.object3d.visible = false;
  ctx.after(1, gameOver); // the explosion first: the screen pauses the game
}

/**
 * Health bars, hit flashes, defeated enemies (score) and the player's defeat (game over); R restarts.
 * The player's health is the HUD's (ui/ui.blend): ctx.store.health and healthLow; enemies' bars and
 * the +points over a kill are ctx.hud items pinned to them.
 */
export default defineSystem({
  name: 'HealthSystem',
  query: [Health],

  start(ctx) {
    ctx.store.score ??= 0; // the run's score: Restart clears ctx.store
    if (!ctx.save.has('bestScore')) ctx.save.set('bestScore', 0); // "Best {save.bestScore}" never shows empty
    ctx.post.set(null); // colour back after a game over
  },
  onAdd(_ctx, body) {
    const health = body.get(Health);
    if (health.current <= 0) health.current = health.max; // full health when it appears (spawned or loaded)
  },

  update(ctx, bodies) {
    for (const body of bodies) {
      if (!body.alive) continue;
      const hp = hitPoints(body);
      const { max } = body.get(Health);
      flash(body.object3d, sinceHit(body, ctx.time) < FLASH_TIME);
      if (body.has(Enemy)) {
        if (hp < max) ctx.hud.bar(`hp-${body.id}`, hp, { max, at: body, offset: [0, 2.1, 0], width: 46, color: '#ff6b6b' });
        if (hp <= 0) defeat(ctx, body);
      } else {
        // The HUD's HealthBar (max 100, the Player's Health.max) and its red twin at 30 % or less.
        ctx.store.health = hp;
        ctx.store.healthLow = hp <= max * 0.3;
        if (hp <= 0 && !arena.over) lose(ctx, body);
      }
    }
    if (ctx.input.keyDown('KeyR')) void ctx.restart();
  },
});
