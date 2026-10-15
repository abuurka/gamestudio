import { BoxGeometry, Mesh, Quaternion, Scene, Vector3 } from 'three';
import { beforeAll, describe, expect, it } from 'vitest';
import { World } from '../src/ecs/world';
import { toQuaternion, toVector3 } from '../src/math';
import { RapierPhysics } from '../src/physics/rapier-backend';
import { Collider, RigidBody } from '../src/schema';

beforeAll(async () => {
  await new RapierPhysics().init();
});

describe('positions and rotations take arrays or objects everywhere', () => {
  it('converts every accepted shape', () => {
    expect(toVector3([1, 2, 3])).toEqual(new Vector3(1, 2, 3));
    expect(toVector3({ x: 1, y: 2, z: 3 })).toEqual(new Vector3(1, 2, 3));
    const q = new Quaternion().setFromAxisAngle(new Vector3(0, 1, 0), 1);
    for (const shape of [q, [q.x, q.y, q.z, q.w] as const, { x: q.x, y: q.y, z: q.z, w: q.w }, [0, 1, 0] as const]) {
      expect(toQuaternion(shape).angleTo(q)).toBeLessThan(1e-6);
    }
  });

  it('physics.teleport, raycast, setGravity and world.spawn accept both', () => {
    const world = new World(new Scene());
    const physics = new RapierPhysics();
    physics.reset(world, [0, -9.81, 0]);
    const crate = world.spawn({ object3d: new Mesh(new BoxGeometry(1, 1, 1)), position: { x: 0, y: 5, z: 0 }, components: [[RigidBody], [Collider]] });
    expect(crate.object3d.position.y).toBe(5);
    physics.teleport(crate, [3, 1, 0]);
    expect(crate.object3d.position.toArray()).toEqual([3, 1, 0]);
    physics.teleport(crate, { x: 4, y: 1, z: 0 }, [0, 0, 0, 1]);
    expect(physics.bodyOf(crate)!.translation()).toMatchObject({ x: 4, y: 1, z: 0 });
    physics.step(1 / 60);
    expect(physics.raycast([4, 10, 0], { x: 0, y: -1, z: 0 })?.entity).toBe(crate);
    physics.setGravity({ x: 0, y: -1, z: 0 });
    expect(physics.getGravity().y).toBe(-1);
  });
});
