// 2.5D: RigidBody axis locks, CharacterController plane modes, orthographic cameras and SideCamera.
import {
  BoxGeometry,
  CapsuleGeometry,
  Group,
  Mesh,
  Object3D,
  OrthographicCamera,
  PerspectiveCamera,
  Quaternion,
  Scene,
  Vector3,
} from 'three';
import { afterEach, beforeAll, describe, expect, it } from 'vitest';
import type { SystemContext } from '../src/context';
import type { Entity } from '../src/ecs/entity';
import { World } from '../src/ecs/world';
import { fitCamera } from '../src/game/camera';
import { RapierPhysics } from '../src/physics/rapier-backend';
import { CharacterControllerSystem, characterMotion, SideCameraSystem } from '../src/systems';
import type { InputAPI } from '../src/input/input';
import { installSceneDom, heldGame, fakeRenderer } from './game-fixture';
import type { FakeDom } from './dom';
import { fakeInput, testContext } from './helpers';

beforeAll(async () => {
  await new RapierPhysics().init();
});

const DT = 1 / 60;

function level() {
  const world = new World(new Scene());
  const physics = new RapierPhysics();
  physics.reset(world, { x: 0, y: -9.81, z: 0 });
  const ground = new Mesh(new BoxGeometry(200, 1, 200));
  ground.position.y = -0.5;
  world.spawn({ name: 'Ground', object3d: ground, components: { Collider: {} } });
  return { world, physics };
}

describe('RigidBody axis locks', () => {
  it('keep a dynamic body exactly on its locked axis under gravity, impulses, contacts and set velocities', () => {
    const { world, physics } = level();
    // A slope that pushes sideways in z: a free body slides off it in z.
    const ramp = new Mesh(new BoxGeometry(6, 0.5, 6));
    ramp.position.set(0, 1, 0.3);
    ramp.rotation.set(0.5, 0, 0.3);
    world.spawn({ name: 'Ramp', object3d: ramp, components: { Collider: {} } });
    const drop = (locks: Record<string, boolean>) => {
      const box = new Mesh(new BoxGeometry(0.6, 0.6, 0.6));
      box.position.set(0.2, 4, 0.37);
      box.rotation.set(0.2, 0.4, 0.1); // lands on an edge: tumbles
      return world.spawn({ object3d: box, components: { RigidBody: { type: 'dynamic', ...locks }, Collider: { shape: 'box' } } });
    };
    const locked = drop({ lockZ: true });
    const free = drop({});
    free.object3d.position.x = 20; // its own copy of the ramp is not needed: it falls on the ground
    physics.teleport(free);
    const z0 = locked.object3d.position.z;
    const r0 = new Quaternion().copy(locked.object3d.quaternion);
    let maxDrift = 0;
    for (let i = 0; i < 240; i++) {
      if (i === 30) physics.bodyOf(locked)!.applyImpulse({ x: 0.5, y: 1, z: 3 }, true);
      if (i === 90) physics.bodyOf(locked)!.setLinvel({ x: 1, y: 0, z: -4 }, true);
      if (i === 120) physics.bodyOf(free)!.applyImpulse({ x: 0, y: 0, z: 3 }, true);
      physics.step(DT);
      maxDrift = Math.max(maxDrift, Math.abs(locked.object3d.position.z - z0));
      expect(physics.bodyOf(locked)!.translation().z).toBe(Math.fround(z0)); // Rapier keeps 32-bit floats
    }
    expect(maxDrift).toBe(0); // exactly, every step
    expect(locked.object3d.position.y).toBeLessThan(3.5); // it did fall and move
    // One lock makes a 2D body: it turns only about the locked axis (z).
    const turn = new Quaternion().copy(locked.object3d.quaternion).multiply(r0.clone().invert());
    expect(Math.abs(turn.x) + Math.abs(turn.y)).toBeLessThan(1e-6);
    expect(Math.abs(turn.z)).toBeGreaterThan(0.05);
    // Without locks nothing changes: the same push moves a body in z.
    expect(Math.abs(free.object3d.position.z - 0.37)).toBeGreaterThan(0.5);
  });

  it('keep a kinematic body on its locked axes when code moves it, and move with teleport', () => {
    const { world, physics } = level();
    const mover = new Mesh(new BoxGeometry(1, 1, 1));
    mover.position.set(0, 2, 1.25);
    const e = world.spawn({ object3d: mover, components: { RigidBody: { type: 'kinematic', lockZ: true, lockY: true } } });
    physics.step(DT);
    for (let i = 0; i < 30; i++) {
      mover.position.x += 0.1;
      mover.position.y += 0.05; // locked
      mover.position.z -= 0.07; // locked
      physics.step(DT);
      expect([mover.position.y, mover.position.z]).toEqual([2, 1.25]);
    }
    expect(mover.position.x).toBeCloseTo(3, 6);
    expect(physics.bodyOf(e)!.translation()).toMatchObject({ y: 2, z: 1.25 });
    physics.teleport(e, [0, 5, -3]); // the way to move a locked axis
    mover.position.z += 1;
    physics.step(DT);
    expect(mover.position.toArray()).toEqual([0, 5, -3]);
  });
});

/** A hero on the ground, run by the CharacterController with `input` for `frames` fixed steps. */
function hero(values: Record<string, unknown>, body: Record<string, unknown> = {}) {
  const { world, physics } = level();
  const mesh = new Mesh(new CapsuleGeometry(0.4, 1).translate(0, 0.9, 0));
  mesh.position.set(1, 0, -2.5);
  const e = world.spawn({
    name: 'Hero',
    object3d: mesh,
    components: { RigidBody: { type: 'kinematic', ...body }, Collider: { shape: 'capsule' }, CharacterController: values },
  });
  physics.step(DT);
  const run = (ctx: SystemContext, frames: number, each?: (frame: number) => void) => {
    const trace: Vector3[] = [];
    for (let frame = 0; frame < frames; frame++) {
      each?.(frame);
      CharacterControllerSystem.update!(ctx, [e]);
      physics.step(DT);
      trace.push(e.object3d.position.clone());
    }
    return trace;
  };
  return { world, physics, e, run };
}

function stick(h: number, v: number, jumpWhen: () => boolean = () => false): InputAPI {
  return {
    ...fakeInput(),
    axis: (name) => (name === 'horizontal' ? h : v),
    gamepadButton: (i) => i === 0 && jumpWhen(),
  };
}

describe("CharacterController plane: 'side'", () => {
  it('moves left/right along the screen and jumps, keeping the depth exactly', () => {
    const { world, physics, e, run } = hero({ plane: 'side', speed: 4 });
    let jumping = false;
    const ctx = testContext(world, physics, stick(1, 1, () => jumping)); // the stick also pushes up: ignored
    const z0 = e.object3d.position.z;
    const trace = run(ctx, 90, (frame) => (jumping = frame === 20)); // gamepad A jumps
    expect(trace.every((p) => p.z === z0)).toBe(true);
    const end = trace.at(-1)!;
    expect(end.x - 1).toBeGreaterThan(5.5); // 4 m/s for 1.5 s
    expect(Math.max(...trace.map((p) => p.y))).toBeGreaterThan(0.6); // jumped
    expect(end.y).toBeLessThan(0.05); // and landed
    expect(characterMotion(e).grounded).toBe(true);
    // It faces the way it runs (+x).
    expect(new Vector3(0, 0, 1).applyQuaternion(e.object3d.quaternion).x).toBeGreaterThan(0.99);
  });

  it('follows the screen, not the world: a camera looking along -x makes z the run axis', () => {
    const { world, physics, e, run } = hero({ plane: 'side' });
    const ctx = testContext(world, physics, stick(-1, 0));
    ctx.three.camera.position.set(12, 1, -2.5);
    ctx.three.camera.lookAt(0, 1, -2.5); // screen right is -z
    ctx.three.camera.updateMatrixWorld(true);
    const trace = run(ctx, 60);
    expect(trace.every((p) => p.x === 1)).toBe(true); // x is the depth now
    expect(trace.at(-1)!.z).toBeGreaterThan(-2.5 + 4); // left on the screen is +z
  });

  it("keeps the 3D default: plane 'none' walks into the screen with forward input", () => {
    const { world, physics, run } = hero({});
    const trace = run(testContext(world, physics, stick(0, 1)), 30);
    expect(trace.at(-1)!.z).toBeLessThan(-2.5 - 2);
    expect(trace.at(-1)!.x).toBeCloseTo(1, 2);
  });

  it('respects RigidBody locks also without a plane (and still jumps)', () => {
    const { world, physics, e, run } = hero({}, { lockZ: true });
    const held = new Set<string>();
    const ctx = testContext(world, physics, { ...stick(1, 1), key: (code) => held.has(code) });
    const trace = run(ctx, 60, (frame) => (frame === 10 ? held.add('Space') : held.delete('Space')));
    expect(trace.every((p) => p.z === -2.5)).toBe(true);
    expect(trace.at(-1)!.x).toBeGreaterThan(3);
    expect(Math.max(...trace.map((p) => p.y))).toBeGreaterThan(0.6);
    expect(e.object3d.position.z).toBe(-2.5);
  });
});

describe("CharacterController plane: 'top'", () => {
  it('moves up the screen under a camera looking straight down', () => {
    const { world, physics, run } = hero({ plane: 'top' });
    const ctx = testContext(world, physics, stick(0, 1));
    const camera = new OrthographicCamera(-8, 8, 4.5, -4.5, 0.1, 100);
    camera.position.set(0, 20, 0);
    camera.rotation.set(-Math.PI / 2, 0, 0); // looks down, screen up is -z
    camera.updateMatrixWorld(true);
    ctx.three.camera = camera;
    const end = run(ctx, 30).at(-1)!;
    expect(end.z).toBeLessThan(-2.5 - 2);
    expect(end.x).toBeCloseTo(1, 2);
    // The 3D default has no direction there and falls back to -z; screen right is +x for both.
    const right = hero({ plane: 'top' });
    const ctx2 = testContext(right.world, right.physics, stick(1, 0));
    ctx2.three.camera = camera;
    expect(right.run(ctx2, 30).at(-1)!.x).toBeGreaterThan(3);
  });
});

describe('orthographic cameras', () => {
  it('keep their height and adapt the width to the view, so nothing stretches', () => {
    const camera = new OrthographicCamera(-8, 8, 4.5, -4.5, 0.1, 100); // exported at 16:9
    const before = camera.projectionMatrix.elements.slice();
    fitCamera(camera, 16 / 9);
    expect(camera.projectionMatrix.elements).toEqual(before);
    fitCamera(camera, 3);
    expect([camera.left, camera.right, camera.top, camera.bottom]).toEqual([-13.5, 13.5, 4.5, -4.5]);
    const m = camera.projectionMatrix.elements;
    expect(m[5]).toBeCloseTo(2 / 9, 12); // the same vertical scale
    expect(m[0]! * 3).toBeCloseTo(m[5]!, 12); // a square stays square on screen
    fitCamera(camera, 0.5); // portrait: narrower
    expect(camera.right).toBeCloseTo(2.25, 12);
    expect(camera.top).toBe(4.5);
  });

  describe('in a game', () => {
    let dom: FakeDom;
    afterEach(() => dom?.restore());

    it('fit on resize and when ctx.three.camera is swapped to one', async () => {
      dom = installSceneDom();
      const game = await heldGame(dom);
      fakeRenderer(game);
      const resize = () => (game as unknown as { resize(): void }).resize();
      resize(); // 800 x 450
      await game.test.begin();
      const ortho = new OrthographicCamera(-5, 5, 5, -5, 0.1, 100);
      game.three.camera = ortho;
      await game.test.advance(1);
      expect(ortho.right).toBeCloseTo(5 * (800 / 450), 9);
      expect(ortho.top).toBe(5);
      dom.canvas.clientWidth = 400;
      dom.canvas.clientHeight = 400;
      resize();
      game.test.render();
      expect([ortho.left, ortho.right, ortho.top]).toEqual([-5, 5, 5]);
      game.test.end();
      game.dispose();
    });
  });
});

/** A camera with SideCamera following a plain target. */
function sideRig(values: Record<string, unknown>, camera: OrthographicCamera | PerspectiveCamera = new OrthographicCamera(-4, 4, 3, -3, 0.1, 100)) {
  const world = new World(new Scene());
  const target = world.spawn({ name: 'Target', object3d: new Object3D() });
  camera.position.set(0, 1, 20);
  const physics = new RapierPhysics();
  const ctx = testContext(world, physics);
  ctx.phase = 'late';
  ctx.three.camera = camera;
  const cam = world.spawn({ name: 'Cam', object3d: camera, components: { SideCamera: { target: target.id, ...values } } });
  SideCameraSystem.onAdd!(ctx, cam);
  const move = (x: number, y = 0, frames = 1) => {
    const from = target.object3d.position.clone();
    for (let i = 1; i <= frames; i++) {
      target.object3d.position.lerpVectors(from, new Vector3(x, y, from.z), i / frames);
      SideCameraSystem.update!(ctx, [cam]);
    }
    return camera.position;
  };
  return { world, target, camera, ctx, cam, move };
}

describe('SideCamera', () => {
  it('snaps onto the target, follows in the screen plane and keeps its depth exactly', () => {
    const { camera, move } = sideRig({ deadZone: [0, 0], lookAhead: 0, smoothing: 0, offset: [0, 1] });
    expect(camera.position.toArray()).toEqual([0, 1, 20]);
    move(7, 3);
    expect(camera.position.x).toBeCloseTo(7, 9);
    expect(camera.position.y).toBeCloseTo(4, 9);
    expect(camera.position.z).toBe(20);
    expect(camera.quaternion.toArray()).toEqual([0, 0, 0, 1]); // never turns
  });

  it('looks ahead where the target runs, and swings over when it turns', () => {
    const { camera, move, target } = sideRig({ deadZone: [0, 0], lookAhead: 2, smoothing: 0, offset: [0, 0] });
    move(10, 0, 180); // 3.33 m/s for 3 s
    expect(camera.position.x - target.object3d.position.x).toBeCloseTo(2 * (1 - Math.exp(-2 * 3)), 2);
    move(10, 0, 60); // standing still: the look-ahead stays
    expect(camera.position.x - 10).toBeGreaterThan(1.9);
    move(0, 0, 180); // back the other way
    expect(camera.position.x - target.object3d.position.x).toBeLessThan(-1.9);
    expect(camera.position.z).toBe(20);
    // A narrow (portrait) view looks ahead at most half its half width.
    const narrow = sideRig({ deadZone: [0, 0], lookAhead: 2, smoothing: 0, offset: [0, 0] }, new OrthographicCamera(-1, 1, 3, -3));
    narrow.move(10, 0, 300);
    expect(narrow.camera.position.x - 10).toBeCloseTo(0.5, 2);
  });

  it('lets the target move inside the dead zone without moving', () => {
    const { camera, move } = sideRig({ deadZone: [4, 2], lookAhead: 0, smoothing: 0, offset: [0, 0] });
    move(1.5, 0.8);
    expect(camera.position.x).toBe(0);
    expect(camera.position.y).toBe(0); // it snapped onto the target at the start
    move(3, 0);
    expect(camera.position.x).toBeCloseTo(1, 9); // pushed by the zone's edge
    move(3, -4);
    expect(camera.position.y).toBeCloseTo(-3, 9);
  });

  it('smooths towards the goal', () => {
    const { camera, move } = sideRig({ deadZone: [0, 0], lookAhead: 0, smoothing: 5, offset: [0, 0] });
    move(4, 0);
    expect(camera.position.x).toBeCloseTo(4 * (1 - Math.exp(-5 / 60)), 9);
  });

  it('keeps the view inside the level bounds (fields or a bounds object)', () => {
    const fields = sideRig({ deadZone: [0, 0], lookAhead: 0, smoothing: 0, offset: [0, 0], boundsMin: [-10, 0, 0], boundsMax: [10, 10, 0] });
    expect(fields.move(9, 5).x).toBe(6); // half width 4: the right edge stays at 10
    expect(fields.move(-50, -5).toArray()).toEqual([-6, 3, 20]); // half height 3: the bottom stays at 0
    const world = fields.world;
    const box = new Mesh(new BoxGeometry(20, 10, 2));
    box.position.set(0, 5, 0);
    const level = world.spawn({ name: 'Level', object3d: box });
    Object.assign(fields.cam.get<Record<string, unknown>>('SideCamera'), { bounds: level.id, boundsMin: [0, 0, 0] });
    expect(fields.move(30, 20).toArray()).toEqual([6, 7, 20]);
    // A Cube empty (no mesh): its position ± its scale.
    const empty = new Object3D();
    empty.position.set(100, 5, 0);
    empty.scale.set(10, 5, 1);
    const area = world.spawn({ name: 'Area', object3d: empty });
    Object.assign(fields.cam.get<Record<string, unknown>>('SideCamera'), { bounds: area.id });
    expect(fields.move(200, -20).toArray()).toEqual([106, 3, 20]);
    // A view wider than the level centres on it.
    const narrow = sideRig({ deadZone: [0, 0], smoothing: 0, boundsMin: [-2, -50, 0], boundsMax: [2, 50, 0] });
    expect(narrow.move(5, 0).x).toBe(0);
  });

  it('works with a perspective camera: bounds at the target depth', () => {
    const camera = new PerspectiveCamera(50, 1, 0.1, 100);
    const { move } = sideRig({ deadZone: [0, 0], lookAhead: 0, smoothing: 0, offset: [0, 0], boundsMin: [-20, -50, 0], boundsMax: [20, 50, 0] }, camera);
    const half = 20 * Math.tan((25 * Math.PI) / 180);
    expect(move(30, 0).x).toBeCloseTo(20 - half, 9);
    expect(camera.position.z).toBe(20);
  });

  it("follows one axis with follow: 'horizontal'", () => {
    const { camera, move } = sideRig({ deadZone: [0, 0], lookAhead: 0, smoothing: 0, follow: 'horizontal' });
    move(5, 8);
    expect(camera.position.toArray()).toEqual([5, 1, 20]);
  });

  it('snaps to whole pixels with pixelSnap', () => {
    const { camera, ctx, move } = sideRig({ deadZone: [0, 0], lookAhead: 0, smoothing: 0, offset: [0, 0], pixelSnap: true });
    ctx.three.renderer = { domElement: { height: 600 } } as never; // 6 m over 600 px: 1 cm per pixel
    move(1.23456, 0.5678);
    expect(camera.position.x).toBeCloseTo(1.23, 12);
    expect(camera.position.y).toBeCloseTo(0.57, 12);
  });

  it('moves a rig with the camera inside it', () => {
    const world = new World(new Scene());
    const target = world.spawn({ object3d: new Object3D() });
    const rig = new Group();
    const camera = new OrthographicCamera(-4, 4, 3, -3);
    camera.position.set(0, 0, 15);
    rig.add(camera);
    const ctx = testContext(world, new RapierPhysics());
    const cam: Entity = world.spawn({ object3d: rig, components: { SideCamera: { target: target.id, deadZone: [0, 0], smoothing: 0, offset: [0, 0], lookAhead: 0 } } });
    SideCameraSystem.onAdd!(ctx, cam);
    target.object3d.position.set(3, 2, 0);
    SideCameraSystem.update!(ctx, [cam]);
    expect(camera.getWorldPosition(new Vector3()).toArray()).toEqual([3, 2, 15]);
  });
});
