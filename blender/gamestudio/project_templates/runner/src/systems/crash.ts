// Technique: event-driven spawns. Running into an Obstacle breaks it: the obstacle names its debris
// prefab (Obstacle.debris), which is spawned where the obstacle stood and attached to the same chunk,
// so it goes when the chunk does. With a shield the run goes on; without one it ends. Falling below the
// track ends it too, unless a shield bounces the runner back up.
import { defineSystem, type Entity, type SystemContext } from '@gamestudio/engine';
import { Box3, Quaternion, Vector3 } from 'three';
import { Obstacle } from '../components/obstacle';
import { Runner } from '../components/runner';
import { bodyBox, touches } from '../lib/contact';
import { runnerMotion } from '../lib/motion';
import { run } from '../lib/run';
import { sfx } from '../lib/sfx';
import { spawnAt } from '../lib/spawn';

const NEAR = 3; // metres along the track: only obstacles this close are tested
const FALL_LIMIT = -3; // runtime y: below this the runner has fallen off the track
const BOUNCE = 16; // m/s up when a shield saves a fall: back over the edge it fell past
const KNOCK = { back: -4, up: 6 }; // m/s: a crash throws the runner back and up

const body = new Box3();
const at = new Vector3();
const facing = new Quaternion();
const warned = new Set<string>();

/** Spawns the obstacle's debris prefab where it stands, under its chunk, and removes the obstacle. */
function breakApart(ctx: SystemContext, obstacle: Entity): void {
  const { debris } = obstacle.get(Obstacle);
  obstacle.object3d.getWorldPosition(at);
  obstacle.object3d.getWorldQuaternion(facing);
  if (debris) {
    try {
      spawnAt(ctx, debris, at, facing, ctx.world.get(obstacle.parentId)); // the chunk it was spawned into
    } catch (error) {
      if (!warned.has(debris)) ctx.log.warn(`${obstacle.name}: cannot spawn debris "${debris}": ${error instanceof Error ? error.message : error}`);
      warned.add(debris);
    }
  }
  ctx.destroy(obstacle);
}

function crash(ctx: SystemContext, runner: Entity, obstacle: Entity): void {
  const r = runner.get(Runner);
  run.over = true;
  run.cause = 'crash';
  r.speed = KNOCK.back;
  runnerMotion(runner).verticalVelocity = KNOCK.up;
  const p = runner.object3d.position;
  ctx.vfx.burst([p.x, p.y + 1, p.z - 0.4], 'explosion', { scale: 0.5 });
  ctx.audio.play(sfx.crash, { volume: 0.8 });
  ctx.log(`Crashed into ${obstacle.name} at ${Math.floor(r.distance)} m`);
}

/** Obstacles end the run or break on a shield; falls end it or bounce. RunSystem shows the screen. */
export default defineSystem({
  name: 'CrashSystem',
  query: [Runner],
  phase: 'fixed', // every physics step: at 20 m/s a slow frame is longer than an obstacle is deep

  update(ctx, runners) {
    if (run.over) return;
    for (const runner of runners) {
      const r = runner.get(Runner);
      const p = runner.object3d.position;
      bodyBox(runner, body);
      for (const obstacle of ctx.world.query(Obstacle)) {
        if (Math.abs(obstacle.object3d.getWorldPosition(at).z - p.z) > NEAR || !touches(body, obstacle)) continue;
        if (r.shield <= 0) {
          breakApart(ctx, obstacle);
          crash(ctx, runner, obstacle);
          return;
        }
        ctx.vfx.burst(at.setY(at.y + 0.8), 'sparkle', { color: '#4dd4ff', scale: 1.5, amount: 1.5 });
        ctx.audio.play(sfx.smash, { volume: 0.6 });
        breakApart(ctx, obstacle); // the shield smashes through
      }
      if (p.y >= FALL_LIMIT) continue;
      if (r.shield > 0) {
        runnerMotion(runner).verticalVelocity = BOUNCE; // the shield throws it back up onto the track
        ctx.vfx.burst([p.x, p.y + 1, p.z], 'sparkle', { color: '#4dd4ff', scale: 2 });
        ctx.audio.play(sfx.jump, { volume: 0.5, rate: 0.8 });
        continue;
      }
      run.over = true;
      run.cause = 'fall';
      ctx.audio.play(sfx.lose, { volume: 0.6 });
      ctx.log(`Fell off the track at ${Math.floor(r.distance)} m`);
    }
  },
});
