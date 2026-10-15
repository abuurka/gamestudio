import { defineSystem, type Entity, type SystemContext } from '@gamestudio/engine';
import { Vector3 } from 'three';
import { Car } from '../components/car';
import { Surface } from '../components/surface';
import { carState } from '../lib/car-state';
import { sfx } from '../lib/sfx';

type PhysicsWorld = NonNullable<SystemContext['physics']['world']>;
type Mover = ReturnType<PhysicsWorld['createCharacterController']>;

const GRAVITY = 20; // m/s²
const COAST = 3; // m/s² of slowdown with no throttle
const FULL_TURN_SPEED = 6; // m/s: below this the car turns less (a parked car does not spin)
const BUMP = 6; // m/s lost against a wall that makes a bump sound
const WALL = 0.7; // a contact whose normal is flatter than this (|normal.y|) is a wall, not the road
const UP = new Vector3(0, 1, 0);
const ROAD = { speedFactor: 1, grip: 1 };

const movers = new WeakMap<PhysicsWorld, Mover>();
const forward = new Vector3();
const right = new Vector3();
const above = new Vector3();
let lastBump = -1;

/** Rapier's character mover, set up for a car: it slides along walls and snaps to the road. */
function moverFor(world: PhysicsWorld): Mover {
  let mover = movers.get(world);
  if (!mover) {
    mover = world.createCharacterController(0.03);
    mover.setUp({ x: 0, y: 1, z: 0 });
    mover.setMaxSlopeClimbAngle((35 * Math.PI) / 180);
    mover.enableAutostep(0.12, 0.2, false); // kerbs and seams
    mover.enableSnapToGround(0.5);
    mover.setApplyImpulsesToDynamicBodies(true); // knock over cones
    movers.set(world, mover);
  }
  return mover;
}

/** The last move touched a wall (a contact with a steep normal), not only the road. */
function hitWall(mover: Mover): boolean {
  for (let i = 0; i < mover.numComputedCollisions(); i++) {
    const hit = mover.computedCollision(i);
    if (hit && Math.abs(hit.normal1.y) < WALL) return true;
  }
  return false;
}

/** The Surface under the car (a raycast down), or road. */
function surfaceUnder(ctx: SystemContext, car: Entity) {
  above.copy(car.object3d.position).setY(car.object3d.position.y + 1);
  const hit = ctx.physics.raycast(above, { x: 0, y: -1, z: 0 }, { maxDistance: 3, exclude: car });
  return hit?.entity.tryGet(Surface) ?? ROAD;
}

/**
 * Arcade car physics on a kinematic body: throttle and brakes change the speed along the car's front,
 * grip bleeds off sideways sliding (less with the handbrake: drifts), and the turn rate follows the
 * speed. The mover then resolves walls and the ground; speed lost against a wall is gone.
 */
export default defineSystem({
  name: 'CarControllerSystem',
  query: [Car],
  phase: 'fixed', // in step with the physics, like the CharacterController

  update(ctx, cars) {
    const world = ctx.physics.world;
    if (!world) return;
    const mover = moverFor(world);
    const dt = ctx.dt;
    const throttle = ctx.input.axis('vertical'); // W/S, arrows, left stick
    const steerInput = -ctx.input.axis('horizontal'); // + turns left: yaw grows counter-clockwise seen from above
    const handbrake = ctx.input.key('Space') || ctx.input.gamepadButton(1);

    for (const car of cars) {
      const collider = ctx.physics.colliderOf(car);
      if (!collider) continue;
      const c = car.get(Car);
      const s = carState(car);
      const surface = surfaceUnder(ctx, car);
      s.offRoad = surface.speedFactor < 1;

      forward.set(0, 0, 1).applyQuaternion(car.object3d.quaternion).setY(0).normalize(); // the car's front
      right.crossVectors(forward, UP);
      let along = s.velocity.dot(forward);
      let sideways = s.velocity.dot(right);

      if (throttle > 0) along += (along < 0 ? c.braking : c.acceleration) * throttle * dt;
      else if (throttle < 0) along -= (along > 0.5 ? c.braking : c.acceleration * 0.6) * -throttle * dt;
      else along -= Math.sign(along) * Math.min(Math.abs(along), COAST * dt);
      const top = c.maxSpeed * surface.speedFactor;
      if (along > top) along = Math.max(top, along - c.braking * 0.5 * dt); // off the road: slow down, not stop
      along = Math.max(along, -c.reverseSpeed);
      if (handbrake) along -= Math.sign(along) * Math.min(Math.abs(along), c.braking * 0.3 * dt);
      sideways *= Math.exp(-(handbrake ? c.handbrakeGrip : c.grip) * surface.grip * dt);

      s.steer += (steerInput - s.steer) * Math.min(1, dt * 8);
      const grip = Math.min(1, Math.abs(along) / FULL_TURN_SPEED) * (1 - 0.3 * Math.min(1, Math.abs(along) / c.maxSpeed));
      car.object3d.rotateOnWorldAxis(UP, s.steer * c.steering * grip * Math.sign(along) * dt);

      s.velocity.copy(forward).multiplyScalar(along).addScaledVector(right, sideways);
      s.verticalSpeed -= GRAVITY * dt;
      const desired = { x: s.velocity.x * dt, y: s.verticalSpeed * dt, z: s.velocity.z * dt };
      mover.computeColliderMovement(collider, desired, ctx.physics.rapier.QueryFilterFlags.EXCLUDE_SENSORS);
      const moved = mover.computedMovement();
      if (mover.computedGrounded()) s.verticalSpeed = 0;
      const p = car.object3d.position; // kinematic bodies sit at the scene root: local = world
      p.set(p.x + moved.x, p.y + moved.y, p.z + moved.z);

      // Against a wall the mover slides the car along it: keep only the velocity it really had. The
      // road alone never slows the car: on a flat floor the mover sometimes reports a slightly tilted
      // normal and cuts a step short, which must not cost the car its speed.
      const before = s.velocity.length();
      if (hitWall(mover)) s.velocity.set(moved.x / dt, 0, moved.z / dt);
      if (before - s.velocity.length() > BUMP && ctx.time - lastBump > 0.4) {
        lastBump = ctx.time;
        ctx.audio.play(sfx.bump, { at: car, volume: 0.6 });
        ctx.vfx.burst(car, 'dust', { scale: 1.3 });
      }
      s.speed = s.velocity.dot(forward);
    }
  },
});
