import { defineSystem, type SystemContext } from '@gamestudio/engine';
import { Vector3 } from 'three';
import { Enemy } from '../components/enemy';
import { Shooter } from '../components/shooter';
import { SpawnPoint } from '../components/spawn-point';
import { WaveSpawner } from '../components/wave-spawner';
import { arena } from '../lib/arena';
import { sfx } from '../lib/sfx';

const SAFE_DISTANCE = 6; // metres: spawn points closer to the player are skipped
const JITTER = 0.75; // metres around the spawn point
const HINT_SECONDS = 8; // the HUD's controls hint shows for the first seconds of play

type Stage = 'break' | 'spawning' | 'fighting';
let stage: Stage = 'break';
let nextAt = 0; // ctx.time of the next wave or spawn
let toSpawn = 0; // enemies of this wave still to come
const warned = new Set<string>();
let banner: { name: string; until: number } | null = null; // the HUD banner showing, and until when

/** The enemy prefabs a wave may use: "Grunt, Runner@3" is Grunt in waves 1-2, Grunt and Runner from wave 3. */
function roster(text: string, wave: number): string[] {
  return text
    .split(',')
    .map((part) => part.trim().split('@'))
    .filter(([name, from]) => name && wave >= Number(from ?? 1))
    .map(([name]) => name!);
}

function spawnEnemy(ctx: SystemContext, names: string[]): void {
  const points = ctx.world.query(SpawnPoint);
  if (points.length === 0 || names.length === 0) return;
  // A stream of its own: shots (their spread) also draw random numbers, and with
  // one shared stream every change to the player's aim would move where later enemies appear.
  const random = ctx.random.stream('spawns');
  const players = ctx.world.query(Shooter);
  const far = points.filter((p) => players.every((pl) => pl.object3d.position.distanceTo(p.object3d.position) > SAFE_DISTANCE));
  const at = random.pick(far.length > 0 ? far : points).object3d.getWorldPosition(new Vector3());
  at.y += 0.05; // a mover that starts touching the floor can sink into it
  at.x += random.range(-JITTER, JITTER);
  at.z += random.range(-JITTER, JITTER);
  const name = random.pick(names);
  try {
    ctx.spawn(name, { position: at, rotation: [0, Math.atan2(-at.x, -at.z), 0] }); // facing the arena's centre
    ctx.vfx.burst(at, 'sparkle', { color: '#ff3b30', scale: 1.6 });
  } catch (error) {
    if (!warned.has(name)) ctx.log.warn(`WaveSpawner: cannot spawn "${name}": ${error instanceof Error ? error.message : error}`);
    warned.add(name);
  }
}

/** Shows one of the HUD's banners (ui/ui.blend: WaveBanner, ClearedBanner) for `seconds` of game time. */
function showBanner(ctx: SystemContext, name: string, seconds: number): void {
  if (banner) ctx.ui.hide(`HUD/${banner.name}`);
  ctx.ui.show(`HUD/${name}`);
  banner = { name, until: ctx.time + seconds };
}

/**
 * The match: a breather, a wave spawned one enemy at a time, the next once it is cleared. It keeps the
 * HUD's values in ctx.store (wave, enemiesLeft; ui/ui.blend binds them) and shows its banners and hint.
 */
export default defineSystem({
  name: 'WaveSystem',
  query: [WaveSpawner],

  start(ctx) {
    Object.assign(arena, { kills: 0, wave: 0, over: false });
    Object.assign(ctx.store, { wave: 0, enemiesLeft: 0 }); // the HUD shows "Get ready" before wave 1
    ctx.store.score ??= 0;
    stage = 'break';
    toSpawn = 0;
    banner = null; // a start drops the HUD's code overrides: the banners are hidden again
    nextAt = ctx.time + (ctx.world.query(WaveSpawner)[0]?.get(WaveSpawner).breather ?? 3);
  },

  update(ctx, spawners) {
    // The controls hint, for the first seconds of play. From the second frame: the title (the Title
    // canvas, on the first start) opens after the first one and holds the game until Play.
    ctx.ui.show('HUD/ControlsHint', ctx.frame > 0 && ctx.time < HINT_SECONDS && !arena.over);
    if (banner && ctx.time >= banner.until) {
      ctx.ui.hide(`HUD/${banner.name}`);
      banner = null;
    }
    const spawner = spawners[0];
    if (!spawner || arena.over) return;
    const s = spawner.get(WaveSpawner);

    if (stage === 'break' && ctx.time >= nextAt) {
      arena.wave += 1;
      toSpawn = s.firstWave + s.perWave * (arena.wave - 1);
      stage = 'spawning';
      showBanner(ctx, 'WaveBanner', 1.5); // "Wave {wave}"
      ctx.audio.play(sfx.wave, { volume: 0.5 });
    }
    if (stage === 'spawning' && ctx.time >= nextAt) {
      spawnEnemy(ctx, roster(s.enemies, arena.wave));
      toSpawn -= 1;
      nextAt = ctx.time + s.spawnInterval;
      if (toSpawn <= 0) stage = 'fighting';
    }
    const alive = ctx.world.query(Enemy).length; // after this frame's spawn
    if (stage === 'fighting' && alive === 0) {
      stage = 'break';
      nextAt = ctx.time + s.breather;
      showBanner(ctx, 'ClearedBanner', 2); // "Wave {wave} cleared!"
      const player = ctx.world.query(Shooter)[0];
      if (player) ctx.vfx.burst(player, 'confetti');
    }
    // The HUD: "Wave {wave} · {enemiesLeft} left" (alive and still to come), "Get ready" before wave 1.
    ctx.store.wave = arena.wave;
    ctx.store.enemiesLeft = alive + toSpawn;
  },
});
