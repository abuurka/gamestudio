import { defineSystem, type Entity, type SystemContext } from '@gamestudio/engine';
import { Vector3 } from 'three';
import { Car } from '../components/car';
import { Checkpoint } from '../components/checkpoint';
import { CheckpointMarker } from '../components/checkpoint-marker';
import { Race } from '../components/race';
import { carState, formatTime } from '../lib/car-state';
import { sfx } from '../lib/sfx';

const MARKER_LIFT = 0.2; // metres above the checkpoint's centre: inside the gate, over the car

let lap = 0; // 0 until the car first crosses the start line
let next = 0; // position (in index order) of the checkpoint to pass next
let lapStart = 0;
let raceStart = 0;
let finished = false;
let bannerUntil = -1; // game time at which the split banner hides (-1: hidden)
const inside = new Set<string>(); // checkpoints the car is in: a checkpoint counts when the car enters it
const local = new Vector3();
const gate = new Vector3();

/** The run's best lap in seconds (ctx.store: Restart clears it), or Infinity. */
const bestLap = (ctx: SystemContext): number => ctx.store.bestLap ?? Infinity;

/** `point` is inside the checkpoint's volume: its empty's 1 m cube, as scaled and turned in Blender. */
function contains(checkpoint: Entity, point: Vector3): boolean {
  checkpoint.object3d.updateWorldMatrix(true, false);
  checkpoint.object3d.worldToLocal(local.copy(point));
  return Math.abs(local.x) <= 0.5 && Math.abs(local.y) <= 0.5 && Math.abs(local.z) <= 0.5;
}

/** The HUD's split banner (ui/ui.blend, HUD/Split) with `text`, for `seconds` of game time. */
function banner(ctx: SystemContext, text: string, seconds: number): void {
  ctx.ui.text('SplitText', text);
  ctx.ui.show('Split');
  bannerUntil = ctx.time + seconds;
}

function pass(ctx: SystemContext, checkpoint: Entity, position: number, count: number, laps: number): void {
  next = (position + 1) % count;
  // Bursts go at the gate's centre point (an entity's scale would stretch them), low enough to stay in view.
  checkpoint.object3d.getWorldPosition(gate).y -= 2;
  if (position > 0) {
    const split = formatTime(ctx.time - lapStart);
    ctx.log(`Checkpoint ${position} / ${count - 1} at ${split}`);
    banner(ctx, `Checkpoint ${position}/${count - 1}   ${split}`, 1.2);
    ctx.audio.play(sfx.checkpoint, { volume: 0.6 });
    ctx.vfx.burst(gate, 'sparkle', { scale: 1.8, amount: 3, blending: 'alpha' }); // additive turns white on the sky
    return;
  }
  if (lap === 0) {
    lap = 1;
    lapStart = raceStart = ctx.time;
    ctx.log('Lap 1 started');
    banner(ctx, 'GO!', 1);
    ctx.audio.play(sfx.lap, { volume: 0.6 });
    return;
  }
  const time = ctx.time - lapStart;
  ctx.store.bestLap = Math.min(bestLap(ctx), time);
  ctx.log(`Lap ${lap} in ${formatTime(time)}`);
  if (lap >= laps) {
    finished = true;
    const total = ctx.time - raceStart;
    ctx.store.raceTime = total; // the clock stops: the HUD and Results show the total
    ctx.log(`Finished in ${formatTime(total)}, best lap ${formatTime(bestLap(ctx))}`);
    ctx.audio.play(sfx.finish, { volume: 0.7 });
    ctx.vfx.burst(gate, 'confetti', { amount: 3 });
    ctx.after(1.5, (ctx) => showResults(ctx, total)); // the confetti first: the screen pauses the game
    return;
  }
  lap += 1;
  lapStart = ctx.time;
  banner(ctx, `Lap ${lap}/${laps}   last ${formatTime(time)}`, 1.5);
  ctx.audio.play(sfx.lap, { volume: 0.6 });
  ctx.vfx.burst(gate, 'confetti', { amount: 1.5 });
}

/** The Results screen: its texts bind raceTime, bestLap and save.bestRace; "New record!" takes the record's place. */
function showResults(ctx: SystemContext, total: number): void {
  const record = ctx.save.get<number | null>('bestRace', null); // ctx.save survives page reloads
  const newRecord = record === null || total < record;
  if (newRecord) ctx.save.set('bestRace', total);
  ctx.ui.show('NewRecord', newRecord);
  ctx.ui.show('RecordText', !newRecord);
  ctx.ui.open('Results');
}

/**
 * The race: checkpoints in order, laps, the clock, the best lap, the speed, the marker over the next
 * checkpoint and the Results screen. R restarts. The screens are in ui/ui.blend: Title (opens on the
 * first start), Pause, Options, Results and the HUD, whose texts bind the ctx.store values set here.
 */
export default defineSystem({
  name: 'RaceSystem',
  query: [Car],

  start(ctx) {
    lap = 0;
    next = 0;
    finished = false;
    bannerUntil = -1;
    inside.clear();
    // What the HUD shows (ctx.store survives scene loads; Restart clears it). bestLap is set after a lap.
    Object.assign(ctx.store, {
      lap: 0,
      laps: ctx.world.query(Race)[0]?.get(Race).laps ?? 3,
      raceTime: 0, // seconds since the start line, stopped at the finish
      lapTime: 0, // seconds of the current lap
      speedKmh: 0,
      offRoad: false,
    });
  },

  update(ctx, cars) {
    if (ctx.input.keyDown('KeyR')) void ctx.restart();
    const car = cars[0];
    if (!car) return;
    const checkpoints = [...ctx.world.query(Checkpoint)].sort((a, b) => a.get(Checkpoint).index - b.get(Checkpoint).index);
    const laps = ctx.world.query(Race)[0]?.get(Race).laps ?? 3;
    checkpoints.forEach((checkpoint, position) => {
      const isInside = contains(checkpoint, car.object3d.position);
      const entered = isInside && !inside.has(checkpoint.id);
      if (isInside) inside.add(checkpoint.id);
      else inside.delete(checkpoint.id);
      if (entered && !finished && position === next) pass(ctx, checkpoint, position, checkpoints.length, laps);
    });

    const state = carState(car);
    ctx.store.lap = lap;
    ctx.store.laps = laps;
    if (lap > 0 && !finished) {
      ctx.store.raceTime = ctx.time - raceStart;
      ctx.store.lapTime = ctx.time - lapStart;
    }
    ctx.store.speedKmh = Math.abs(state.speed) * 3.6;
    ctx.store.offRoad = state.offRoad;
    if (bannerUntil >= 0 && ctx.time >= bannerUntil) {
      ctx.ui.hide('Split');
      bannerUntil = -1;
    }

    const marker = ctx.world.query(CheckpointMarker)[0];
    const target = checkpoints[next];
    if (marker && target) {
      marker.object3d.visible = !finished;
      target.object3d.getWorldPosition(marker.object3d.position);
      marker.object3d.position.y += MARKER_LIFT + Math.sin(ctx.time * 3) * 0.3;
      marker.object3d.rotation.y += 1.5 * ctx.dt;
    }
  },
});
