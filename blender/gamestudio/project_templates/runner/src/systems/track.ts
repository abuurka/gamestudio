// Technique: chunk streaming. The track is made of chunk prefabs: collections marked gs_prefab in the
// "Chunks" scene of scenes/main.blend, each with a TrackChunk on its root. This system spawns them end
// to end ahead of the runner and destroys the ones behind it, so however long the run, only a handful
// of chunks (and their contents) exist at once.
// Technique: seeded, weighted selection. Each chunk is picked from ctx.random.stream('track') by its
// weight, among the chunks whose minDistance the run has reached, and its sockets are filled from the
// same stream (lib/sockets.ts). The same seed builds the same track, so playtests repeat.
import { defineSystem, type ComponentValues, type SystemContext } from '@gamestudio/engine';
import { Vector3 } from 'three';
import { Runner } from '../components/runner';
import { Track } from '../components/track';
import { TrackChunk } from '../components/track-chunk';
import { fillSockets } from '../lib/sockets';
import { pickWeighted } from '../lib/weighted';

interface ChunkInfo {
  name: string;
  length: number;
  weight: number;
  minDistance: number;
}

let catalog: ChunkInfo[] = [];
let next = 0; // metres from the Track empty to where the next chunk starts
let previous = ''; // the last chunk picked: never twice in a row when there is a choice
const origin = new Vector3();

/**
 * The chunk prefabs and their TrackChunk values. A prefab's components live on its instances, so each
 * prefab is spawned once, read and destroyed again, all in this frame: nothing draws or simulates it.
 */
function readCatalog(ctx: SystemContext): ChunkInfo[] {
  const found: ChunkInfo[] = [];
  for (const name of ctx.prefabs) {
    const probe = ctx.spawn(name, { position: [0, -1000, 0] });
    const chunk = probe.tryGet(TrackChunk);
    if (chunk) found.push({ name, ...chunk });
    probe.destroy();
  }
  if (found.length === 0) ctx.log.warn('TrackSystem: no prefab has a TrackChunk on its root');
  return found;
}

/** Spawns the next chunk where the track has got to, and fills its sockets. */
function spawnNext(ctx: SystemContext, track: ComponentValues<typeof Track>): void {
  const random = ctx.random.stream('track');
  const open = catalog.filter((c) => c.weight > 0 && c.minDistance <= next);
  const choices = open.length > 1 ? open.filter((c) => c.name !== previous) : open;
  const info = pickWeighted(random, choices.map((c) => ({ item: c, weight: c.weight })));
  if (!info) {
    next += 1000; // nothing to build: stop trying for a while
    return;
  }
  const chunk = ctx.spawn(info.name, { position: [origin.x, origin.y, origin.z - next] }); // the track runs toward -z
  fillSockets(ctx, chunk, next);
  previous = info.name;
  next += chunk.get(TrackChunk).length;
  track.chunks += 1;
}

/** Keeps `Track.ahead` metres of chunks in front of the runner and destroys the ones it has left behind. */
export default defineSystem({
  name: 'TrackSystem',
  query: [Track],

  start(ctx) {
    catalog = readCatalog(ctx);
    previous = '';
    for (const chunk of ctx.world.query(TrackChunk)) chunk.destroy(); // after a hot reload: build afresh
    const track = ctx.world.query(Track)[0];
    if (!track) return;
    track.object3d.getWorldPosition(origin);
    const runner = ctx.world.query(Runner)[0]; // RunSystem spawned it at PlayerStart, before the track
    next = Math.max(0, origin.z - (runner?.object3d.position.z ?? origin.z) - 10); // under the runner
    track.get(Track).chunks = 0;
    while (next < track.get(Track).ahead) spawnNext(ctx, track.get(Track));
  },

  update(ctx, tracks) {
    const track = tracks[0];
    const runner = ctx.world.query(Runner)[0];
    if (!track || !runner) return;
    const t = track.get(Track);
    const travelled = origin.z - runner.object3d.position.z; // metres past the Track empty
    while (next < travelled + t.ahead) spawnNext(ctx, t);
    for (const chunk of ctx.world.query(TrackChunk)) {
      const end = origin.z - chunk.object3d.position.z + chunk.get(TrackChunk).length;
      if (end < travelled - t.behind) chunk.destroy(); // its sockets' contents go with it
    }
  },
});
