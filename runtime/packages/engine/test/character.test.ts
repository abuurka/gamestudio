// Rigged characters: collider sizing from the skinned body, bone lookup, Locomotion clips.
import {
  AnimationClip,
  Bone,
  BoxGeometry,
  Float32BufferAttribute,
  Group,
  Matrix4,
  Mesh,
  Scene,
  Skeleton,
  SkinnedMesh,
  Uint16BufferAttribute,
  VectorKeyframeTrack,
} from 'three';
import { beforeAll, describe, expect, it } from 'vitest';
import { AnimationService } from '../src/animation/animation';
import { World } from '../src/ecs/world';
import { RapierPhysics } from '../src/physics/rapier-backend';
import { collectLocalGeometry, colliderSkips, shapeFromGeometry } from '../src/physics/shapes';
import { CharacterController, Collider, Locomotion, RigidBody } from '../src/schema';
import { characterMotion, LocomotionSystem } from '../src/systems';
import { testContext } from './helpers';

/**
 * An armature root (no mesh) with a hips bone and a 1.8 m skinned body as a separate child entity.
 * Like an exported glTF skin, the raw vertex positions are in the bind-time world space (the rig
 * stood at z = 6) with an identity bind matrix; the rig has moved since.
 */
function rig(world: World, name: string, boneName = 'hips') {
  const root = new Group();
  root.name = name;
  root.position.set(0, 0, 6);
  const hips = new Bone();
  hips.name = boneName;
  hips.position.y = 0.9;
  root.add(hips);
  const geometry = new BoxGeometry(0.5, 1.8, 0.3).translate(0, 0.9, 6);
  const count = geometry.getAttribute('position').count;
  geometry.setAttribute('skinIndex', new Uint16BufferAttribute(new Array(count * 4).fill(0), 4));
  geometry.setAttribute('skinWeight', new Float32BufferAttribute(new Array(count).fill([1, 0, 0, 0]).flat(), 4));
  const skin = new SkinnedMesh(geometry);
  const body = new Group(); // glTF puts a multi-material skinned mesh's parts in a group
  body.name = `${name}_Mesh`;
  body.add(skin);
  root.add(body);
  root.updateMatrixWorld(true);
  skin.bind(new Skeleton([hips]), new Matrix4());
  root.position.set(3, 0, -2); // moved (a start position, a teleport) without a render since
  root.updateWorldMatrix(true, true);
  const entity = world.spawn({ object3d: root, components: [[RigidBody, { type: 'kinematic' }], [Collider, { shape: 'capsule' }]] });
  world.spawn({ object3d: body, parent: entity });
  return { entity, hips };
}

const kindOf = (world: World) => (o: import('three').Object3D) => {
  const e = world.entityOf(o);
  if (e?.object3d !== o) return 'none' as const;
  return e.has(RigidBody) || e.has(Collider) ? ('physics' as const) : ('plain' as const);
};

beforeAll(async () => {
  await new RapierPhysics().init();
});

describe('colliders of rigged characters', () => {
  it('size an armature root from its skinned body, not a 1 m fallback', () => {
    const world = new World(new Scene());
    const { entity } = rig(world, 'Hero');
    const geometry = collectLocalGeometry(entity.object3d, colliderSkips(entity.object3d, kindOf(world)), false);
    const shape = shapeFromGeometry('capsule', geometry, entity.object3d.scale);
    expect(shape).toMatchObject({ kind: 'capsule', radius: 0.25 });
    if (shape.kind !== 'capsule') throw new Error('capsule expected');
    expect(shape.halfHeight).toBeCloseTo(0.65, 5);
    expect(shape.center.toArray().map((n) => Math.round(n * 1e5) / 1e5)).toEqual([0, 0.9, 0]); // on the rig, not where it was bound

    const physics = new RapierPhysics();
    physics.reset(world, [0, -9.81, 0]);
    const collider = physics.colliderOf(entity)!;
    expect(collider.radius()).toBeCloseTo(0.25, 5);
    expect(collider.halfHeight()).toBeCloseTo(0.65, 5);
  });

  it('leaves out props a rigged character holds: the capsule stays on its body', () => {
    const world = new World(new Scene());
    const { entity, hips } = rig(world, 'Warden');
    const gun = new Mesh(new BoxGeometry(0.3, 0.3, 1.6).translate(0, 0, 0.8)); // a big blaster held forward
    gun.position.set(0.3, 0.1, 0.2);
    hips.add(gun);
    world.spawn({ name: 'Gun', object3d: gun, parent: entity });
    entity.object3d.updateWorldMatrix(true, true);
    const skip = colliderSkips(entity.object3d, kindOf(world));
    expect(skip(gun)).toBe(true);
    const shape = shapeFromGeometry('capsule', collectLocalGeometry(entity.object3d, skip, false), entity.object3d.scale);
    expect(shape).toMatchObject({ kind: 'capsule', radius: 0.25 }); // before: 1.1 m wide, 0.9 m in front of the body
    expect(shape.center.toArray().map((n) => Math.round(n * 1e5) / 1e5)).toEqual([0, 0.9, 0]);
  });

  it('still leaves out child entities that are not the body', () => {
    const world = new World(new Scene());
    const crate = world.spawn({ object3d: new Mesh(new BoxGeometry(1, 1, 1)), components: [[Collider]] });
    const lamp = new Mesh(new BoxGeometry(0.2, 3, 0.2));
    world.spawn({ object3d: lamp, parent: crate });
    const skip = colliderSkips(crate.object3d, kindOf(world));
    expect(skip(lamp)).toBe(true);
    expect(collectLocalGeometry(crate.object3d, skip, false).box.max.y).toBeCloseTo(0.5, 5);
    // An empty over plain child meshes is sized from them.
    const holder = world.spawn({ components: [[Collider]] });
    const part = new Mesh(new BoxGeometry(2, 2, 2));
    world.spawn({ object3d: part, parent: holder });
    expect(collectLocalGeometry(holder.object3d, colliderSkips(holder.object3d, kindOf(world)), false).box.max.x).toBeCloseTo(1, 5);
  });

  it('leaves out the extra draws a game shader adds (an outline hull pushed out on screen)', () => {
    const world = new World(new Scene());
    const crate = world.spawn({ object3d: new Mesh(new BoxGeometry(1, 1, 1)), components: [[Collider]] });
    const hull = new Mesh(new BoxGeometry(3, 3, 3));
    hull.userData.gsShaderPass = { shader: 'SelectionOutline', kind: 'hull' };
    crate.object3d.add(hull);
    const geometry = collectLocalGeometry(crate.object3d, colliderSkips(crate.object3d, kindOf(world)), true);
    expect(geometry.box.max.y).toBeCloseTo(0.5, 5);
    expect(geometry.positions.length).toBe(new BoxGeometry(1, 1, 1).getAttribute('position').count * 3);
  });
});

describe('animation.bone', () => {
  it('finds bones in the entity\'s own skeleton, also when loading renamed them', () => {
    const world = new World(new Scene());
    const first = rig(world, 'A', 'upper_leg_L');
    const second = rig(world, 'B', 'upper_leg_L_1'); // GLTFLoader's name for the second rig's bone
    const animation = new AnimationService(() => {});
    expect(animation.bone(first.entity, 'upper_leg_L')).toBe(first.hips);
    expect(animation.bone(second.entity, 'upper_leg_L')).toBe(second.hips);
    expect(animation.bone(world.findByName('B_Mesh')!, 'upper_leg_L')).toBe(second.hips); // through the skin
    expect(animation.bone(first.entity, 'head')).toBeNull();
  });
});

describe('Locomotion', () => {
  const clip = (name: string) => new AnimationClip(name, 1, [new VectorKeyframeTrack('hips.position', [0, 1], [0, 0.9, 0, 0, 1, 0])]);

  function setup(values: Record<string, unknown> = {}) {
    const world = new World(new Scene());
    const { entity } = rig(world, 'Hero');
    entity.add(CharacterController);
    entity.add(Locomotion, values);
    const ctx = testContext(world, new RapierPhysics());
    const animation = new AnimationService(() => {});
    animation.reset(world, ['Idle', 'Walk', 'Run', 'Jump', 'Fall'].map(clip));
    ctx.animation = animation;
    ctx.phase = 'update';
    const motion = characterMotion(entity) as { grounded: boolean; verticalVelocity: number };
    motion.grounded = true;
    /** Runs `frames` frames moving at `speed` m/s along x; returns the clip playing. */
    const run = (frames: number, speed = 0) => {
      for (let i = 0; i < frames; i++) {
        entity.object3d.position.x += speed * ctx.dt;
        LocomotionSystem.update!(ctx, [entity]);
        animation.update(ctx.dt);
      }
      return { component: entity.get<{ clip: string }>(Locomotion).clip, playing: animation.current(entity) };
    };
    return { entity, motion, run };
  }

  it('plays idle, walk, run, jump and fall from the movement', () => {
    const { motion, run } = setup();
    expect(run(5)).toEqual({ component: 'Idle', playing: 'Idle' });
    expect(run(20, 2).playing).toBe('Walk');
    expect(run(20, 6).playing).toBe('Run');
    motion.grounded = false;
    motion.verticalVelocity = 6;
    expect(run(1, 6).playing).toBe('Jump');
    motion.verticalVelocity = -3; // past the top
    expect(run(1, 6).playing).toBe('Fall');
    motion.grounded = true;
    expect(run(1, 6).playing).toBe('Run');
    motion.grounded = false; // off a step: not a fall unless it lasts
    motion.verticalVelocity = -1;
    expect(run(3, 6).playing).toBe('Run');
    expect(run(10, 6).playing).toBe('Fall');
    motion.grounded = true;
    expect(run(40, 0).playing).toBe('Idle');
  });

  it('falls back to the clips it has', () => {
    const { motion, run } = setup({ run: 'Sprint', fall: '' });
    expect(run(30, 6).playing).toBe('Walk');
    motion.grounded = false;
    motion.verticalVelocity = -3;
    expect(run(20, 0).playing).toBe('Jump');
  });
});
