import { BoxGeometry, CapsuleGeometry, CylinderGeometry, Mesh, Scene, SphereGeometry, Vector3 } from 'three';
import { beforeAll, describe, expect, it } from 'vitest';
import { Scheduler } from '../src/ecs/scheduler';
import { World } from '../src/ecs/world';
import { RapierPhysics } from '../src/physics/rapier-backend';
import { collectLocalGeometry, shapeFromGeometry } from '../src/physics/shapes';
import { Collider, RigidBody } from '../src/schema';
import { characterMotion } from '../src/systems';
import { fakeInput, testContext, worldY } from './helpers';

const DT = 1 / 60;
const GRAVITY = { x: 0, y: -9.81, z: 0 };

function setup() {
  const world = new World(new Scene());
  const physics = new RapierPhysics();
  physics.reset(world, GRAVITY);
  const ground = new Mesh(new BoxGeometry(1, 1, 1));
  ground.scale.set(20, 0.5, 20);
  ground.position.set(0, -0.25, 0);
  world.spawn({ name: 'Ground', object3d: ground, components: [[Collider]] });
  return { world, physics };
}

function run(physics: RapierPhysics, steps: number, each?: () => void): void {
  for (let i = 0; i < steps; i++) {
    each?.();
    physics.step(DT);
  }
}

beforeAll(async () => {
  await new RapierPhysics().init();
});

describe('Rapier physics backend', () => {
  it('drops a dynamic box onto a fixed ground where it comes to rest', () => {
    const { world, physics } = setup();
    const box = new Mesh(new BoxGeometry(1, 1, 1));
    box.position.set(0, 4, 0);
    const crate = world.spawn({ name: 'Crate', object3d: box, components: [[RigidBody], [Collider]] });

    run(physics, 30);
    expect(box.position.y).toBeLessThan(4); // Falling, and the object3d follows the body.
    run(physics, 270);
    expect(box.position.y).toBeCloseTo(0.5, 1);
    const body = physics.bodyOf(crate)!;
    expect(body.translation().y).toBeCloseTo(0.5, 1);
    expect(Math.hypot(body.linvel().x, body.linvel().y, body.linvel().z)).toBeLessThan(0.05);
    expect(physics.bodyCount).toBe(2);
  });

  it('sizes colliders from the mesh bounds times world scale', () => {
    const { world, physics } = setup();
    // A 2 m tall cylinder scaled ×2 in Y: a 4 m tall, 0.5 m radius capsule.
    const pole = new Mesh(new CylinderGeometry(0.5, 0.5, 2));
    pole.scale.set(1, 2, 1);
    pole.position.set(3, 10, 0);
    world.spawn({ object3d: pole, components: { RigidBody: {}, Collider: { shape: 'capsule' } } });
    run(physics, 400);
    expect(pole.position.y).toBeCloseTo(2, 1); // Rests on its end (rotation unlocked but upright fall).

    const geometry = collectLocalGeometry(pole, () => false, false);
    const capsule = shapeFromGeometry('capsule', geometry, new Vector3(1, 2, 1));
    expect(capsule).toMatchObject({ kind: 'capsule', radius: 0.5, halfHeight: 1.5 });
    const sphere = shapeFromGeometry('sphere', geometry, new Vector3(1, 2, 1));
    expect(sphere).toMatchObject({ kind: 'sphere', radius: 2 });
  });

  it('reports sensor overlaps as collision events', () => {
    const { world, physics } = setup();
    const zone = new Mesh(new BoxGeometry(4, 1, 4));
    zone.position.set(0, 2, 0);
    const sensor = world.spawn({ name: 'Zone', object3d: zone, components: { Collider: { sensor: true } } });
    const ballMesh = new Mesh(new SphereGeometry(0.25));
    ballMesh.position.set(0, 5, 0);
    const ball = world.spawn({ name: 'Ball', object3d: ballMesh, components: [[RigidBody], [Collider, { shape: 'sphere' }]] });

    const events: string[] = [];
    run(physics, 180, () => {
      for (const c of physics.collisions) {
        const other = c.a === ball ? c.b : c.a;
        events.push(`${c.started ? 'start' : 'end'}:${other.name}:${c.sensor}`);
      }
      physics.beginFrame();
    });
    expect(events).toContain('start:Zone:true');
    expect(events).toContain('end:Zone:true');
    expect(events).toContain('start:Ground:false');
    expect(ballMesh.position.y).toBeCloseTo(0.25, 1); // Sensors don't block.
    expect(sensor.alive).toBe(true);
  });

  it('raycasts, teleports and rebuilds bodies', () => {
    const { world, physics } = setup();
    run(physics, 1); // Scene queries see the state as of the last step.
    const hit = physics.raycast({ x: 5, y: 10, z: 0 }, { x: 0, y: -1, z: 0 });
    expect(hit?.entity.name).toBe('Ground');
    expect(hit?.distance).toBeCloseTo(10, 3);
    expect(hit?.normal.y).toBeCloseTo(1, 3);

    const box = new Mesh(new BoxGeometry(1, 1, 1));
    box.position.set(0, 3, 0);
    const crate = world.spawn({ object3d: box, components: [[RigidBody], [Collider]] });
    run(physics, 20);
    physics.teleport(crate, { x: 4, y: 6, z: 1 });
    const body = physics.bodyOf(crate)!;
    expect(body.linvel().y).toBe(0);
    expect(box.position.toArray()).toEqual([4, 6, 1]);
    expect(body.translation()).toMatchObject({ x: 4, y: 6, z: 1 });

    crate.add(RigidBody, { type: 'fixed' }); // Changing the component rebuilds the body.
    run(physics, 60);
    expect(physics.bodyOf(crate)!.isFixed()).toBe(true);
    expect(box.position.y).toBe(6);

    crate.remove(RigidBody);
    crate.remove(Collider);
    expect(physics.bodyOf(crate)).toBeNull();
    crate.add(Collider);
    expect(physics.bodyOf(crate)!.isFixed()).toBe(true); // Collider alone → fixed body.
  });

  it('moves fixed and kinematic bodies with their objects', () => {
    const { world, physics } = setup();
    const door = new Mesh(new BoxGeometry(1, 2, 0.2));
    door.position.set(0, 1, 5);
    const entity = world.spawn({ object3d: door, components: [[Collider]] });
    run(physics, 1);
    door.position.x = 2;
    run(physics, 1);
    expect(physics.bodyOf(entity)!.translation().x).toBeCloseTo(2, 5);
  });

  it('drives a character with the built-in CharacterController system', () => {
    const { world, physics } = setup();
    const held = new Set<string>();
    const ctx = testContext(world, physics, fakeInput(held));
    const scheduler = new Scheduler((_s, error) => {
      throw error;
    });
    const mesh = new Mesh(new CylinderGeometry(0.4, 0.4, 1.8));
    mesh.position.set(-3, 3, 0);
    const player = world.spawn({
      name: 'Player',
      object3d: mesh,
      components: {
        RigidBody: { type: 'kinematic' },
        Collider: { shape: 'capsule' },
        CharacterController: {},
      },
    });
    const tick = () => {
      scheduler.run('fixed', ctx);
      physics.step(DT);
    };
    for (let i = 0; i < 120; i++) tick();
    expect(worldY(mesh)).toBeCloseTo(0.9, 1); // Standing on the ground.
    expect(characterMotion(player).grounded).toBe(true);

    held.add('Space');
    tick();
    held.delete('Space');
    let apex = 0;
    for (let i = 0; i < 30; i++) {
      tick();
      apex = Math.max(apex, worldY(mesh));
    }
    expect(apex).toBeGreaterThan(1.5); // jumpSpeed 6, gravity 20 → ~0.9 m jump.
    for (let i = 0; i < 60; i++) tick();
    expect(worldY(mesh)).toBeCloseTo(0.9, 1);

    characterMotion(player).verticalVelocity = 12; // What a bounce pad does.
    for (let i = 0; i < 20; i++) tick();
    expect(worldY(mesh)).toBeGreaterThan(3);
  });

  it('keeps a character placed exactly on a box floor from sinking into it', () => {
    // Rapier's controller lets a capsule that starts inside its 2 cm margin sink; it must not.
    const { world, physics } = setup(); // a 20 x 20 box floor, top at y = 0
    const ctx = testContext(world, physics);
    const scheduler = new Scheduler((_s, error) => {
      throw error;
    });
    const mesh = new Mesh(new CapsuleGeometry(0.3, 1.2).translate(0, 0.9, 0)); // feet at the origin
    const player = world.spawn({ object3d: mesh, position: [3, 0, 3], components: { RigidBody: { type: 'kinematic' }, Collider: { shape: 'capsule' }, CharacterController: {} } });
    const lowest = (seconds: number) => {
      let y = Infinity;
      for (let i = 0; i < seconds * 60; i++) {
        scheduler.run('fixed', ctx);
        physics.step(DT);
        y = Math.min(y, mesh.position.y);
      }
      return y;
    };
    // The first step cannot see the floor yet (Rapier's queries update when the world steps): one step of fall at most.
    expect(lowest(5)).toBeGreaterThan(-0.006);
    expect(mesh.position.y).toBeCloseTo(0.02, 3); // resting at the controller's margin
    for (const spot of [[5, 0, 5], [-3, 0, -3], [0, 0, 0], [5, -0.05, 5]] as const) {
      physics.teleport(player, spot); // onto the surface, or a little into it
      expect(lowest(3)).toBeGreaterThan(Math.min(spot[1], 0) - 0.001);
      expect(mesh.position.y).toBeCloseTo(0.02, 3);
    }
  });
});
