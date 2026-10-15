// Camera cuts: FollowCamera and SideCamera jump to their place when the target teleports (a fall
// respawn, a checkpoint) or after snapCamera(), instead of sweeping across the level and through the
// player. Ordinary movement is smoothed as before.
import { Object3D, OrthographicCamera, PerspectiveCamera, Scene, Vector3 } from 'three';
import { afterEach, beforeAll, describe, expect, it } from 'vitest';
import { defineSystem, removeSystem } from '../src/ecs/system';
import { World } from '../src/ecs/world';
import { RapierPhysics } from '../src/physics/rapier-backend';
import { CharacterController } from '../src/schema/builtins';
import { characterMotion, FollowCameraSystem, SideCameraSystem, snapCamera } from '../src/systems';
import type { FakeDom } from './dom';
import { heldGame, installSceneDom } from './game-fixture';
import { testContext } from './helpers';

type V3 = [number, number, number];

beforeAll(async () => {
  await new RapierPhysics().init();
});

const OFFSET = new Vector3(0, 4, 8);
/** Where a FollowCamera with the default offset rests behind a target at `p`. */
const rest = (p: V3): number[] => new Vector3(...p).add(OFFSET).toArray();
const rounded = (v: Vector3): number[] => v.toArray().map((n) => Math.round(n * 1e6) / 1e6 + 0);

/** A FollowCamera (default offset, smoothing 5) on a plain target, and a second entity far away. */
function followRig() {
  const world = new World(new Scene());
  const target = world.spawn({ name: 'Target', object3d: new Object3D() });
  const other = world.spawn({ name: 'Other', object3d: new Object3D(), position: [40, 0, 0] });
  const ctx = testContext(world, new RapierPhysics());
  ctx.phase = 'late';
  const cam = world.spawn({ name: 'Cam', object3d: new PerspectiveCamera(), components: { FollowCamera: { target: target.id } } });
  FollowCameraSystem.onAdd!(ctx, cam);
  /** Puts the target at `to`, runs one late update and returns where the camera is. */
  const move = (to: V3): number[] => {
    target.object3d.position.set(...to);
    FollowCameraSystem.update!(ctx, [cam]);
    return rounded(cam.object3d.getWorldPosition(new Vector3()));
  };
  return { target, other, cam, ctx, move };
}

describe('FollowCamera cuts', () => {
  it('smooths ordinary movement and cuts to its place when the target teleports', () => {
    const { move } = followRig();
    expect(move([0, 0, 0])).toEqual(rest([0, 0, 0])); // in place from the start
    const k = 1 - Math.exp(-5 / 60);
    expect(move([1, 0, 0])[0]).toBeCloseTo(k, 6); // smoothed: it lags
    // Falling: the camera lags above the target.
    for (let y = -0.5; y >= -12; y -= 0.5) move([1, y, 0]);
    const lagging = move([1, -12.5, 0]);
    expect(lagging[1] - (-12.5 + 4)).toBeGreaterThan(1);
    // The respawn: 13.5 m in one frame. The camera is behind the player at once, not sweeping up through it.
    expect(move([1, 1, 0])).toEqual(rest([1, 1, 0]));
    // A 4 m step is movement, smoothed.
    expect(move([5, 1, 0])[0]).toBeCloseTo(1 + 4 * k, 6);
  });

  it('a fast but steady target is no teleport: 6 m per frame at 10 fps (60 m/s) is smoothed', () => {
    const { ctx, move } = followRig();
    ctx.dt = 0.1;
    move([6, 0, 0]);
    expect(move([12, 0, 0])[0]).toBeLessThan(12 - 1);
  });

  it('snapCamera() cuts once: for the camera, its target, or every camera', () => {
    const { target, other, cam, move } = followRig();
    expect(move([2, 0, 0])).not.toEqual(rest([2, 0, 0]));
    snapCamera(target);
    expect(move([3, 0, 0])).toEqual(rest([3, 0, 0]));
    expect(move([4, 0, 0])).not.toEqual(rest([4, 0, 0])); // once
    snapCamera(cam);
    expect(move([5, 0, 0])).toEqual(rest([5, 0, 0]));
    snapCamera();
    expect(move([6, 0, 0])).toEqual(rest([6, 0, 0]));
    snapCamera(other); // not this camera's target
    expect(move([7, 0, 0])).not.toEqual(rest([7, 0, 0]));
  });

  it('a new target is not a teleport: the camera pans over to it', () => {
    const { other, cam, ctx } = followRig();
    cam.get<{ target: string }>('FollowCamera').target = other.id;
    FollowCameraSystem.update!(ctx, [cam]);
    const x = cam.object3d.getWorldPosition(new Vector3()).x;
    expect(x).toBeGreaterThan(1);
    expect(x).toBeLessThan(39);
  });
});

describe('SideCamera cuts', () => {
  it('cuts on a teleport and after snapCamera(), centring its dead zone on the target again', () => {
    const world = new World(new Scene());
    const target = world.spawn({ name: 'Target', object3d: new Object3D() });
    const camera = new OrthographicCamera(-4, 4, 3, -3, 0.1, 100);
    camera.position.set(0, 0, 20);
    const ctx = testContext(world, new RapierPhysics());
    ctx.phase = 'late';
    const cam = world.spawn({ name: 'Cam', object3d: camera, components: { SideCamera: { target: target.id, deadZone: [4, 2], lookAhead: 0, smoothing: 5, offset: [0, 0] } } });
    SideCameraSystem.onAdd!(ctx, cam);
    const move = (x: number, y = 0): number[] => {
      target.object3d.position.set(x, y, 0);
      SideCameraSystem.update!(ctx, [cam]);
      return rounded(camera.position);
    };
    expect(move(1)).toEqual([0, 0, 20]); // inside the dead zone
    expect(move(30, -2)).toEqual([30, -2, 20]); // a teleport: in place, centred
    expect(move(33, -2)[0]).toBeLessThan(31); // 3 m: pushed by the zone's edge, smoothed
    snapCamera(target);
    expect(move(36, -2)).toEqual([36, -2, 20]);
  });
});

describe('a fall respawn under the test clock', () => {
  let dom: FakeDom | null = null;
  afterEach(() => {
    removeSystem('FallRespawnProbe');
    dom?.restore();
    dom = null;
  });

  it('cuts the FollowCamera back to the start, never through the player; test.place() starts it in place', async () => {
    let start: V3 = [0, 0, 0];
    const respawns: number[] = [];
    defineSystem({
      name: 'FallRespawnProbe',
      query: [CharacterController],
      update(ctx, entities) {
        for (const e of entities) {
          if (e.object3d.position.y >= -12) continue;
          ctx.physics.teleport(e, { x: start[0], y: start[1], z: start[2] });
          characterMotion(e).verticalVelocity = 0;
          respawns.push(ctx.frame);
        }
      },
    });
    dom = installSceneDom();
    const g = await heldGame(dom);
    await g.test.begin();
    await g.test.advance(30);
    const player = g.world.findByName('Player')!;
    const camera = g.world.findByName('Camera')!;
    start = player.object3d.position.toArray() as V3;
    const at = (e: typeof player): Vector3 => e.object3d.getWorldPosition(new Vector3());
    const gap = (): number => at(camera).distanceTo(at(player));

    // Off the level: the camera is behind the player from the first step (no sweep from the start).
    g.test.place('Player', { position: [500, 1, 0] });
    await g.test.advance(1);
    expect(rounded(at(camera))).toEqual(rounded(at(player).add(OFFSET)));

    // It falls (the camera lags above it), drops below -12 and respawns at the start.
    let closest = Infinity;
    let afterRespawn: number[] | null = null;
    for (let step = 0; step < 150 && respawns.length === 0; step++) {
      await g.test.advance(1);
      closest = Math.min(closest, gap());
      if (respawns.length > 0) afterRespawn = rounded(at(camera));
    }
    expect(respawns).toHaveLength(1);
    expect(afterRespawn).toEqual(rounded(new Vector3(...start).add(OFFSET)));
    expect(closest).toBeGreaterThan(OFFSET.length() - 0.5);
    g.test.end();
    g.dispose();
  });
});
