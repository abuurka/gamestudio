import { defineSystem, type Entity, type SystemContext } from '@gamestudio/engine';
import { Runner } from '../components/runner';
import { LANES } from '../lib/lanes';
import { runnerMotion, type RunnerMotion } from '../lib/motion';
import { difficulty, run } from '../lib/run';
import { sfx } from '../lib/sfx';

type PhysicsWorld = NonNullable<SystemContext['physics']['world']>;
type Mover = ReturnType<PhysicsWorld['createCharacterController']>;

const FORWARD = Math.PI; // the yaw that faces runtime -z, where the track runs
const LEAN = 0.3; // radians the runner turns toward the lane it is heading for
const EASE = 16; // 1/s: how hard the runner is pulled to its lane's centre (capped by Runner.laneSpeed)
const COYOTE = 0.1; // seconds after running off an edge when a jump still works
const BUFFER = 0.15; // seconds a jump pressed just before landing is kept
const HARD_LANDING = 0.35; // seconds in the air after which landing raises dust
const RUN_RATE = { min: 1.15, max: 1.7, per: 1 / 12 }; // the Run clip's playback speed: per m/s, within min and max
const movers = new WeakMap<PhysicsWorld, Mover>();
let padHeld = 0; // the gamepad's left/right last step, to turn a held stick into one lane change

/** Rapier's character mover: it keeps the runner on the floors, up ramps and over seams between chunks. */
function moverFor(world: PhysicsWorld): Mover {
  let mover = movers.get(world);
  if (!mover) {
    mover = world.createCharacterController(0.02);
    mover.setUp({ x: 0, y: 1, z: 0 });
    mover.setMaxSlopeClimbAngle((40 * Math.PI) / 180);
    mover.enableAutostep(0.3, 0.2, false); // seams where chunks meet
    mover.enableSnapToGround(0.3);
    movers.set(world, mover);
  }
  return mover;
}

/** -1 or 1 when a lane change was pressed this step (keys, d-pad or a flick of the left stick), else 0. */
function steer(ctx: SystemContext): number {
  const pad = ctx.input.gamepad;
  const stick = pad?.axes[0] ?? 0;
  const held = Math.abs(stick) > 0.6 ? Math.sign(stick) : (ctx.input.gamepadButton(15) ? 1 : 0) - (ctx.input.gamepadButton(14) ? 1 : 0);
  const flicked = held !== padHeld ? held : 0;
  padHeld = held;
  if (ctx.input.keyDown('KeyA') || ctx.input.keyDown('ArrowLeft')) return -1;
  if (ctx.input.keyDown('KeyD') || ctx.input.keyDown('ArrowRight')) return 1;
  return flicked;
}

function jumpPressed(ctx: SystemContext): boolean {
  return ctx.input.keyDown('Space') || ctx.input.keyDown('KeyW') || ctx.input.keyDown('ArrowUp') || ctx.input.gamepadButton(0);
}

/** Run on the floor, take-off then the air pose in a jump, flailing in a long fall or a crash. */
function animate(ctx: SystemContext, runner: Entity, m: RunnerMotion, speed: number): void {
  const taking = ctx.animation.current(runner) === 'JumpStart' && ctx.animation.isPlaying(runner, 'JumpStart');
  if (run.over) ctx.animation.play(runner, 'Fall', { fade: 0.15 });
  else if (m.grounded) ctx.animation.play(runner, 'Run', { fade: 0.1, speed: Math.min(RUN_RATE.max, Math.max(RUN_RATE.min, speed * RUN_RATE.per)) });
  else if (m.airTime > 0.5 && m.verticalVelocity < -8) ctx.animation.play(runner, 'Fall', { fade: 0.2 });
  else if (!taking) ctx.animation.play(runner, 'JumpAir', { fade: 0.15 });
}

/**
 * The player's movement: it runs toward runtime -z at a speed that ramps up with the distance, A/D
 * or the arrows move it a lane, and Space jumps. Rapier's character mover keeps it on the chunks'
 * floors; where a chunk has none, it falls. (The runtime prefab patterns are in track.ts, sockets.ts,
 * crash.ts, power-ups.ts and run.ts.)
 */
export default defineSystem({
  name: 'RunnerSystem',
  query: [Runner],
  phase: 'fixed', // moves the kinematic body in step with the physics, like the CharacterController

  onAdd(ctx, runner) {
    runnerMotion(runner); // remembers where the run starts
    runner.object3d.rotation.y = FORWARD;
    ctx.animation.play(runner, 'Idle', { fade: 0 }); // under the title screen
  },

  update(ctx, runners) {
    const world = ctx.physics.world;
    if (!world) return;
    const mover = moverFor(world);
    const dt = ctx.dt;
    const change = run.over ? 0 : steer(ctx);
    const jump = !run.over && jumpPressed(ctx);

    for (const runner of runners) {
      const collider = ctx.physics.colliderOf(runner);
      if (!collider) continue; // its body is created on the next physics step
      const r = runner.get(Runner);
      const m = runnerMotion(runner);
      const p = runner.object3d.position; // kinematic bodies sit at the scene root: local = world

      if (run.over) r.speed -= r.speed * Math.min(1, 4 * dt); // a crash knocks it back (CrashSystem), then it stops
      else r.speed = r.startSpeed + (r.maxSpeed - r.startSpeed) * difficulty(r.distance, r.rampDistance);
      if (change !== 0) {
        const lane = Math.min(2, Math.max(0, r.lane + change));
        if (lane !== r.lane) ctx.audio.play(sfx.whoosh, { volume: 0.25, rate: 1.4 });
        r.lane = lane;
      }
      if (jump) m.jumpUntil = ctx.time + BUFFER;
      if (m.jumpUntil >= ctx.time && (m.grounded || (m.airTime < COYOTE && !m.jumped))) {
        m.verticalVelocity = r.jumpSpeed;
        m.jumpUntil = -1;
        m.jumped = true;
        ctx.animation.play(runner, 'JumpStart', { loop: false, fade: 0.05, speed: 1.6 });
        ctx.audio.play(sfx.jump, { volume: 0.3 });
        ctx.vfx.burst(runner, 'dust', { scale: 0.6 });
      }
      m.verticalVelocity -= r.gravity * dt;

      const sideways = Math.max(-r.laneSpeed, Math.min(r.laneSpeed, (LANES[r.lane] - p.x) * EASE));
      const desired = { x: run.over ? 0 : sideways * dt, y: m.verticalVelocity * dt, z: -r.speed * dt };
      const flags = ctx.physics.rapier.QueryFilterFlags;
      mover.computeColliderMovement(collider, desired, flags.EXCLUDE_SENSORS | flags.EXCLUDE_DYNAMIC); // debris never blocks it
      const moved = mover.computedMovement();
      const landed = mover.computedGrounded() && !m.grounded;
      m.grounded = mover.computedGrounded();
      if (landed && m.airTime > HARD_LANDING) ctx.vfx.burst(runner, 'dust', { scale: 0.8 });
      if (m.grounded && m.verticalVelocity < 0) m.verticalVelocity = 0;
      if (m.verticalVelocity > 0 && moved.y < desired.y - 1e-4) m.verticalVelocity = 0; // bumped its head
      m.airTime = m.grounded ? 0 : m.airTime + dt;
      if (m.grounded) m.jumped = false;
      p.set(p.x + moved.x, p.y + moved.y, p.z + moved.z);

      r.distance = Math.max(r.distance, m.startZ - p.z);
      runner.object3d.rotation.y = FORWARD - (run.over ? 0 : (sideways / r.laneSpeed) * LEAN);
      animate(ctx, runner, m, r.speed);
    }
  },
});
