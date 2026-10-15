// Playtests that replay the same inputs must replay the same fight: a character's facing must not move
// distant enemies, shot spread must not move spawns, and a lagging camera must not bend movement.
import { BoxGeometry, CapsuleGeometry, Group, Mesh, PerspectiveCamera, Quaternion, Scene, Vector3 } from 'three';
import { beforeAll, describe, expect, it } from 'vitest';
import { World } from '../src/ecs/world';
import { GameRandom } from '../src/game/random';
import { RapierPhysics, swingOnly } from '../src/physics/rapier-backend';
import { CharacterControllerSystem, FollowCameraSystem, moveForward } from '../src/systems';
import { fakeInput, testContext } from './helpers';

beforeAll(async () => {
  await new RapierPhysics().init();
});

/**
 * Eight enemies chase a player through a row of walls with Rapier's character mover, sidestepping
 * when blocked (as the shooter template's enemies do: chaotic, so any difference grows). The player
 * stands still and turns to `turnTo` at frame 120. Returns every enemy position at every frame.
 */
function chase(turnTo: number | null, playerShape: 'capsule' | 'box'): number[][] {
  const world = new World(new Scene());
  const physics = new RapierPhysics();
  physics.reset(world, { x: 0, y: -9.81, z: 0 });
  const ground = new Mesh(new BoxGeometry(80, 1, 80));
  ground.position.set(0, -0.5, 0);
  world.spawn({ name: 'Ground', object3d: ground, components: { Collider: {} } });
  for (let i = 0; i < 6; i++) {
    const wall = new Mesh(new BoxGeometry(4, 2, 0.5));
    wall.position.set(-12 + i * 5, 1, 2 + (i % 2) * 3);
    world.spawn({ name: `Wall${i}`, object3d: wall, components: { Collider: { shape: 'box' } } });
  }
  const body = new Mesh(new CapsuleGeometry(0.4, 1.0).translate(0, 0.9, 0));
  body.position.set(0, 0, 14);
  const player = world.spawn({
    name: 'Player',
    object3d: body,
    components: { RigidBody: { type: 'kinematic' }, Collider: { shape: playerShape } },
  });
  const enemies = Array.from({ length: 8 }, (_, i) => {
    const m = new Mesh(new CapsuleGeometry(0.35, 1.0).translate(0, 0.85, 0));
    m.position.set(-14 + i * 4, 0, -10 - (i % 3));
    return world.spawn({ name: `E${i}`, object3d: m, components: { RigidBody: { type: 'kinematic' }, Collider: { shape: 'capsule' } } });
  });
  physics.step(1 / 60);
  const mover = physics.world!.createCharacterController(0.02);
  mover.setUp({ x: 0, y: 1, z: 0 });
  mover.enableSnapToGround(0.3);
  mover.enableAutostep(0.35, 0.2, false);
  const detours = new Map<unknown, { until: number; side: number }>();
  const to = new Vector3();
  const step = new Vector3();
  const up = new Vector3(0, 1, 0);
  const trace: number[][] = [];
  for (let frame = 0; frame < 600; frame++) {
    const time = frame / 60;
    if (turnTo !== null && frame === 120) player.object3d.rotation.y = turnTo;
    for (const e of enemies) {
      const pos = e.object3d.position;
      to.subVectors(player.object3d.position, pos).setY(0);
      e.object3d.rotation.y = Math.atan2(to.x, to.z);
      if (to.length() < 1) continue;
      const detour = detours.get(e);
      const angle = detour && time < detour.until ? detour.side * 1.2 : 0;
      step.copy(to).normalize().applyAxisAngle(up, angle).multiplyScalar(3 / 60);
      step.y = -0.1;
      mover.computeColliderMovement(physics.colliderOf(e)!, step, physics.rapier.QueryFilterFlags.EXCLUDE_SENSORS);
      const moved = mover.computedMovement();
      pos.set(pos.x + moved.x, pos.y + moved.y, pos.z + moved.z);
      if (angle === 0 && Math.hypot(moved.x, moved.z) < (0.3 * 3) / 60) {
        detours.set(e, { until: time + 0.6, side: -(detour?.side ?? 1) });
      }
    }
    physics.step(1 / 60);
    trace.push(enemies.flatMap((e) => e.object3d.position.toArray()));
  }
  if (playerShape === 'capsule') {
    // The capsule is the same at any yaw: the body keeps only the swing, so turning changes nothing.
    const r = physics.bodyOf(player)!.rotation();
    expect([r.x, r.y, r.z, r.w].map((v) => Math.round(v * 1e6) / 1e6 + 0)).toEqual([0, 0, 0, 1]);
  }
  return trace;
}

function maxDiff(a: number[][], b: number[][]): number {
  return Math.max(...a.map((row, f) => Math.max(...row.map((v, i) => Math.abs(v - b[f]![i]!)))));
}

describe('a character turning in place', () => {
  it('does not move distant enemies (a capsule body ignores its yaw)', () => {
    const still = chase(null, 'capsule');
    expect(maxDiff(still, chase(null, 'capsule'))).toBe(0); // replays exactly
    // Before: the turn moved an enemy 12.8 m away by 1.6e-5 m at frame 407, and by 3.3 m at frame 600.
    expect(maxDiff(still, chase(1.3, 'capsule'))).toBe(0);
    expect(maxDiff(still, chase(-2.7, 'capsule'))).toBe(0);
  });

  it('still turns a body whose shape changes with its yaw (a box)', () => {
    const world = new World(new Scene());
    const physics = new RapierPhysics();
    physics.reset(world, { x: 0, y: -9.81, z: 0 });
    const crate = world.spawn({
      object3d: new Mesh(new BoxGeometry(2, 1, 1)),
      components: { RigidBody: { type: 'kinematic' }, Collider: {} },
    });
    crate.object3d.rotation.y = 0.5;
    physics.step(1 / 60);
    physics.step(1 / 60);
    const r = physics.bodyOf(crate)!.rotation();
    expect(new Quaternion(r.x, r.y, r.z, r.w).angleTo(crate.object3d.quaternion)).toBeLessThan(1e-5);
  });

  it('keeps the tilt of a turned and tilted body (swing-twist)', () => {
    const tilted = new Quaternion().setFromAxisAngle(new Vector3(1, 0, 0), 0.4);
    const turned = tilted.clone().multiply(new Quaternion().setFromAxisAngle(new Vector3(0, 1, 0), 1.1));
    expect(swingOnly(turned.clone()).angleTo(tilted)).toBeLessThan(1e-6);
    // The local up axis ends where it would have: the capsule stands the same way.
    const upA = new Vector3(0, 1, 0).applyQuaternion(turned);
    const upB = new Vector3(0, 1, 0).applyQuaternion(swingOnly(turned.clone()));
    expect(upA.distanceTo(upB)).toBeLessThan(1e-6);
  });
});

describe('ctx.random.stream', () => {
  it('gives independent named streams that restart with the game stream', () => {
    const draw = (api: GameRandom['api'], n: number) => Array.from({ length: n }, () => api());
    const a = new GameRandom(42);
    const b = new GameRandom(42);
    const spawnsA = a.api.stream('spawns');
    expect(a.api.stream('spawns')).toBe(spawnsA); // the same object every call
    draw(a.api, 17); // shot spread drawn from the main stream ...
    draw(a.api.stream('spread'), 5); // ... or from another stream
    expect(draw(spawnsA, 6)).toEqual(draw(b.api.stream('spawns'), 6)); // ... does not move the spawns
    expect(draw(a.api.stream('loot'), 3)).not.toEqual(draw(b.api.stream('spawns'), 3)); // names differ
    expect(spawnsA.int(1, 6)).toBeGreaterThanOrEqual(1);
    a.reseed(7);
    b.reseed(7);
    expect(draw(spawnsA, 4)).toEqual(draw(b.api.stream('spawns'), 4)); // a kept handle restarts with the scene
    expect(new GameRandom(8).api.stream('spawns')()).not.toBe(new GameRandom(7).api.stream('spawns')());
    expect(draw(new GameRandom(7).api, 3)).toEqual(draw(new GameRandom(7).api, 3)); // the main stream is as before
  });
});

describe('movement relative to a FollowCamera', () => {
  function setup() {
    const world = new World(new Scene());
    const physics = new RapierPhysics();
    physics.reset(world, { x: 0, y: -9.81, z: 0 });
    const ground = new Mesh(new BoxGeometry(200, 1, 200));
    ground.position.y = -0.5;
    world.spawn({ name: 'Ground', object3d: ground, components: { Collider: {} } });
    const hero = world.spawn({
      name: 'Hero',
      object3d: new Mesh(new CapsuleGeometry(0.4, 1).translate(0, 0.9, 0)),
      components: { RigidBody: { type: 'kinematic' }, Collider: { shape: 'capsule' }, CharacterController: { speed: 5 } },
    });
    const rig = new Group();
    const camera = new PerspectiveCamera();
    rig.add(camera);
    world.spawn({
      name: 'Camera',
      object3d: rig,
      components: { FollowCamera: { target: hero.id, offset: [0, 19, 11], smoothing: 6 } },
    });
    return { world, physics, hero, camera };
  }

  it('moves along the rest direction, not the lagging camera', () => {
    const { world, physics, camera } = setup();
    const ctx = testContext(world, physics);
    ctx.three.camera = camera;
    camera.parent!.position.set(9, 19, 2); // caught mid-swing, looking somewhere else
    camera.parent!.lookAt(0, 0, 0);
    camera.parent!.updateMatrixWorld(true);
    const forward = moveForward(ctx, new Vector3());
    expect(forward.toArray().map((v) => Math.round(v * 1e6) / 1e6 + 0)).toEqual([0, 0, -1]);
    // Without a FollowCamera: the camera's own view direction.
    const free = new PerspectiveCamera();
    free.lookAt(1, 0, 0);
    free.updateMatrixWorld(true);
    ctx.three.camera = free;
    expect(moveForward(ctx, new Vector3()).x).toBeCloseTo(1, 6);
  });

  it('keeps a long sideways key hold straight while the camera catches up', () => {
    const { world, physics, hero, camera } = setup();
    const ctx = testContext(world, physics, { ...fakeInput(), axis: (name) => (name === 'horizontal' ? 1 : 0) });
    ctx.three.camera = camera;
    physics.step(1 / 60);
    const characters = world.query(...CharacterControllerSystem.query!);
    const cameras = world.query(...FollowCameraSystem.query!);
    for (let frame = 0; frame < 162; frame++) { // 2.7 s, as in the dogfood report
      ctx.dt = 1 / 60;
      ctx.time = frame / 60;
      CharacterControllerSystem.update!(ctx, characters);
      physics.step(1 / 60);
      FollowCameraSystem.update!(ctx, cameras); // smoothing 6: the camera lags behind the hero
      camera.parent!.updateMatrixWorld(true);
    }
    const p = hero.object3d.position;
    expect(p.x).toBeGreaterThan(12); // 5 m/s for 2.7 s
    expect(Math.abs(p.z)).toBeLessThan(0.01); // before: drifted 1.6 m sideways behind a lagging camera
  });
});
