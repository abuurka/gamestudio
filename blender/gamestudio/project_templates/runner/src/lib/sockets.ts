// Technique: sockets, the Unity spawn-point pattern inside prefabs. Every chunk prefab has empties
// with a Socket component. TrackSystem calls fillSockets right after it spawns a chunk: it finds the
// chunk's own sockets (ctx.world.descendants), picks what goes in each from the seeded 'track' stream,
// weighted, and spawns it at the socket's world transform, attached to the chunk, so destroying the
// chunk takes it along.
// Technique: per-instance overrides. Spawned instances keep their prefab's component values until
// code changes them: here coins are worth more and sweepers roll faster further into the run.
import type { Entity, SystemContext } from '@gamestudio/engine';
import { Quaternion, Vector3 } from 'three';
import { Coin } from '../components/coin';
import { Runner } from '../components/runner';
import { Socket } from '../components/socket';
import { Sweeper } from '../components/sweeper';
import { Track } from '../components/track';
import { LANES } from './lanes';
import { difficulty } from './run';
import { spawnAt } from './spawn';
import { parseWeighted, pickWeighted } from './weighted';

const position = new Vector3();
const rotation = new Quaternion();
const warned = new Set<string>();

/** Spawns what goes in each of the chunk's sockets. `distance`: metres into the run where the chunk starts. */
export function fillSockets(ctx: SystemContext, chunk: Entity, distance: number): void {
  const track = ctx.world.query(Track)[0]?.get(Track);
  const runner = ctx.world.query(Runner)[0]?.get(Runner);
  const level = difficulty(distance, runner?.rampDistance ?? 2000);
  const random = ctx.random.stream('track'); // the same stream as the chunk picks: one seed, one track
  const lists = { obstacle: track?.obstacles ?? '', coins: track?.coins ?? '', pickup: track?.pickups ?? '' };

  for (const socket of ctx.world.descendants(chunk, Socket)) {
    const s = socket.get(Socket);
    if (!random.chance(s.chance)) continue;
    const name = pickWeighted(random, parseWeighted(s.options || lists[s.kind]));
    const lane = random.int(0, 2); // drawn for every socket, used or not, so the stream stays in step
    if (!name || (s.kind === 'obstacle' && distance < (track?.warmup ?? 0))) continue;

    socket.object3d.getWorldPosition(position);
    socket.object3d.getWorldQuaternion(rotation);
    if (s.anyLane) position.x = chunk.object3d.position.x + LANES[lane]; // chunks are laid along x = their root's
    let spawned: Entity;
    try {
      spawned = spawnAt(ctx, name, position, rotation, chunk);
    } catch (error) {
      if (!warned.has(name)) ctx.log.warn(`Socket ${socket.name}: cannot spawn "${name}": ${error instanceof Error ? error.message : error}`);
      warned.add(name);
      continue;
    }
    // Per-instance overrides: this copy only, the prefab keeps its values.
    for (const coin of [spawned, ...ctx.world.descendants(spawned, Coin)]) {
      if (coin.has(Coin)) coin.get(Coin).value = 1 + Math.floor(level * 2); // 1, 2 from halfway, 3 at the end
    }
    if (spawned.has(Sweeper)) spawned.get(Sweeper).speed *= 1 + level; // up to twice as fast
  }
}
