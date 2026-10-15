// Navigation: navmesh builds from colliders and prefab instances, paths, NavAgent movement.
import { BoxGeometry, Group, Mesh, Object3D, Scene, Vector3 } from 'three';
import type { GLTF } from 'three/addons/loaders/GLTFLoader.js';
import { beforeAll, describe, expect, it } from 'vitest';
import type { Entity } from '../src/ecs/entity';
import { World } from '../src/ecs/world';
import { NavAgentSystem } from '../src/nav/agent';
import { loadRecast, NavService, sceneUsesNav } from '../src/nav/nav';
import { RapierPhysics } from '../src/physics/rapier-backend';
import { Prefabs } from '../src/prefab/prefabs';
import { buildEntities } from '../src/scene/build';
import { processManifest } from '../src/scene/manifest';
import { Collider, NavAgent, NavMesh, RigidBody } from '../src/schema';
import { testContext } from './helpers';

beforeAll(async () => {
  await new RapierPhysics().init();
  await loadRecast();
});

/** A fixed box with its top face at `top`, centred on (x, z). */
function box(world: World, name: string, [x, z]: [number, number], [w, h, d]: [number, number, number], top = h): Entity {
  const mesh = new Mesh(new BoxGeometry(w, h, d));
  mesh.name = name;
  mesh.position.set(x, top - h / 2, z);
  return world.spawn({ object3d: mesh, components: [[Collider, {}]] });
}

/** A 20 × 20 m floor (top at y = 0) with a 10 m wall across the middle (x -5..5, z ±0.25). */
async function level(options: { pillar?: boolean; room?: boolean } = {}) {
  const scene = new Scene();
  const world = new World(scene);
  box(world, 'Floor', [0, 0], [20, 1, 20], 0);
  box(world, 'Wall', [0, 0], [10, 2, 0.5]);
  if (options.room) {
    // A closed 3 × 3 m room around (6, 6).
    box(world, 'RoomN', [6, 7.75], [4, 2, 0.5]);
    box(world, 'RoomS', [6, 4.25], [4, 2, 0.5]);
    box(world, 'RoomE', [7.75, 6], [0.5, 2, 4]);
    box(world, 'RoomW', [4.25, 6], [0.5, 2, 4]);
  }
  const physics = new RapierPhysics();
  physics.reset(world, [0, -9.81, 0]);
  const nav = new NavService(() => {});
  await nav.reset(world, physics, scene, true);
  return { scene, world, physics, nav };
}

/** Points along the path every 5 cm, to check that none is inside an obstacle. */
function samples(path: Vector3[]): Vector3[] {
  const out: Vector3[] = [];
  for (let i = 1; i < path.length; i++) {
    const steps = Math.ceil(path[i - 1]!.distanceTo(path[i]!) / 0.05);
    for (let s = 0; s <= steps; s++) out.push(path[i - 1]!.clone().lerp(path[i]!, s / steps));
  }
  return out;
}

/** Sideways distance from `p` to the box |x| <= hx, |z| <= hz (0 inside it). */
const distanceToBox = (p: Vector3, hx: number, hz: number) => Math.hypot(Math.max(0, Math.abs(p.x) - hx), Math.max(0, Math.abs(p.z) - hz));
const insideWall = (p: Vector3) => distanceToBox(p, 5, 0.25) === 0;
const length = (path: Vector3[]) => path.slice(1).reduce((sum, p, i) => sum + p.distanceTo(path[i]!), 0);

describe('navmesh paths', () => {
  it('go around a wall, keeping the agent radius from it', async () => {
    const { nav } = await level();
    expect(nav.ready).toBe(true);
    expect(nav.stats().triangles).toBeGreaterThan(4);
    const path = nav.path([0, 0, -4], [0, 0, 4])!;
    expect(path).not.toBeNull();
    expect(path.length).toBeGreaterThanOrEqual(3); // a corner at an end of the wall
    expect(path[0]!.distanceTo(new Vector3(0, 0, -4))).toBeLessThan(0.3);
    expect(path.at(-1)!.distanceTo(new Vector3(0, 0, 4))).toBeLessThan(0.3);
    expect(length(path)).toBeGreaterThan(10); // around, not the 8 m straight through
    // Paths keep the agent radius (0.4 m) from walls, give or take a cell (0.2 m).
    for (const p of samples(path)) expect(distanceToBox(p, 5, 0.25), `${p.toArray()}`).toBeGreaterThan(0.25);
    for (const p of path) expect(Math.abs(p.y)).toBeLessThan(0.3); // on the floor, not on the wall
  });

  it('return null for a goal that cannot be reached, or the way to the closest point with partial', async () => {
    const { nav } = await level({ room: true });
    expect(nav.closest([6, 0, 6])).not.toBeNull(); // the room's floor is navmesh, just cut off
    expect(nav.path([0, 0, -4], [6, 0, 6])).toBeNull();
    expect(nav.path([0, 0, -4], [60, 0, 0])).toBeNull(); // off the navmesh
    const partial = nav.path([0, 0, -4], [6, 0, 6], { partial: true })!;
    expect(partial).not.toBeNull();
    const end = partial.at(-1)!;
    expect(Math.max(Math.abs(end.x - 6), Math.abs(end.z - 6))).toBeGreaterThan(1.9); // outside the room's walls
  });

  it('are the same on every build (determinism) and reuse the cached mesh', async () => {
    const a = await level({ room: true });
    const b = await level({ room: true }); // another world and service: built again
    const queries: Array<[[number, number, number], [number, number, number]]> = [
      [[0, 0, -4], [0, 0, 4]],
      [[-8, 0, -8], [8, 0, 8]],
      [[3, 0, 3], [-7, 0, 2]],
    ];
    for (const [from, to] of queries) {
      const pa = a.nav.path(from, to)!.map((p) => p.toArray());
      const pb = b.nav.path(from, to)!.map((p) => p.toArray());
      expect(pa).toEqual(pb);
    }
    expect(a.nav.stats().triangles).toBe(b.nav.stats().triangles);
    expect(b.nav.stats().buildMs).toBeGreaterThan(0);
    // A reset of the same level (Stop, a restart) takes the navmesh from the cache.
    await b.nav.reset(b.world, b.physics, b.scene, true);
    expect(b.nav.stats().buildMs).toBe(0);
    expect(b.nav.path([0, 0, -4], [0, 0, 4])!.map((p) => p.toArray())).toEqual(a.nav.path([0, 0, -4], [0, 0, 4])!.map((p) => p.toArray()));
  });

  it('rebuild when static geometry is spawned, and draw a debug view', async () => {
    const { world, nav, scene } = await level();
    const before = length(nav.path([-8, 0, -4], [-8, 0, 4])!);
    box(world, 'Barrier', [-7.5, 0], [5, 2, 0.5]); // closes the gap at the wall's west end
    const after = nav.path([-8, 0, -4], [-8, 0, 4])!;
    expect(length(after)).toBeGreaterThan(before + 10); // now around the east end
    nav.debug = true;
    const debug = scene.getObjectByName('gs_nav_debug')!;
    expect(debug).toBeDefined();
    expect(debug.children.length).toBe(3); // navmesh, its edges, the agents' paths
    nav.debug = false;
    expect(scene.getObjectByName('gs_nav_debug')).toBeUndefined();
  });

  it('use the NavMesh settings: a wider agent keeps further from walls', async () => {
    const { world, nav } = await level();
    world.spawn({ name: 'Settings', components: [[NavMesh, { agentRadius: 1 }]] });
    await nav.rebuild();
    expect(nav.settings.agentRadius).toBe(1);
    const path = nav.path([0, 0, -4], [0, 0, 4])!;
    for (const p of samples(path)) expect(distanceToBox(p, 5, 0.25), `${p.toArray()}`).toBeGreaterThan(0.85);
  });
});

describe('NavAgent', () => {
  it('walks around the wall to its destination', async () => {
    const { world, physics, nav } = await level();
    const body = new Mesh(new BoxGeometry(0.6, 1.8, 0.6).translate(0, 0.9, 0));
    body.position.set(0, 0.05, -4);
    const agent = world.spawn({
      name: 'Walker',
      object3d: body,
      components: [
        [RigidBody, { type: 'kinematic' }],
        [Collider, { shape: 'capsule' }],
        [NavAgent, { speed: 4 }],
      ],
    });
    const ctx = testContext(world, physics);
    ctx.nav = nav;
    nav.moveTo(agent, [0, 0, 4]);
    let arrived = -1;
    for (let step = 0; step < 600 && arrived < 0; step++) {
      NavAgentSystem.update!(ctx, world.query(NavAgent));
      physics.step(1 / 60);
      ctx.time += 1 / 60;
      const p = agent.object3d.position;
      expect(insideWall(p), `step ${step}: ${p.toArray()}`).toBe(false);
      if (agent.get(NavAgent).remaining >= 0 && agent.get(NavAgent).remaining <= 0.3) arrived = step;
    }
    expect(arrived).toBeGreaterThan(0);
    expect(arrived).toBeLessThan(360); // ~13 m at 4 m/s, with acceleration: well under 6 s
    expect(agent.object3d.position.distanceTo(new Vector3(0, 0, 4))).toBeLessThan(0.5);
    expect(nav.agentPath(agent).length).toBeGreaterThan(0);

    nav.stop(agent); // stops: no goal, no path
    NavAgentSystem.update!(ctx, world.query(NavAgent));
    expect(agent.get(NavAgent).remaining).toBe(-1);
  });
});

describe('a level built from prefab instances', () => {
  it('has the instances in its navmesh', async () => {
    // Prefab "WallSegment": one 4 m wall. The level places two of them side by side as Blender
    // collection instances (an empty each), making an 8 m wall across the floor.
    const template = new Mesh(new BoxGeometry(4, 2, 0.5).translate(0, 1, 0));
    template.name = 'Segment';
    template.userData.gs_id = 'gs_seg';
    const prefabScene = new Group();
    prefabScene.add(template);
    const raw = {
      format: 'gamestudio.scene',
      formatVersion: 2,
      scene: 'main',
      rev: 1,
      glb: 'main.glb',
      entities: [
        { id: 'gs_floor', name: 'Floor', type: 'MESH', parent: null, components: { Collider: {} } },
        { id: 'gs_w1', name: 'WallA', type: 'EMPTY', parent: null, components: {}, prefab: 'WallSegment' },
        { id: 'gs_w2', name: 'WallB', type: 'EMPTY', parent: null, components: {}, prefab: 'WallSegment' },
      ],
      prefabs: [
        {
          name: 'WallSegment',
          glb: 'main.prefabs.glb',
          entities: [{ id: 'gs_seg', name: 'Segment', type: 'MESH', parent: null, components: { Collider: {} } }],
        },
        {
          name: 'Guard',
          glb: 'main.prefabs.glb',
          entities: [{ id: 'gs_guard', name: 'Guard', type: 'MESH', parent: null, components: { NavAgent: {} } }],
        },
      ],
    };
    const { manifest } = processManifest(raw);
    expect(sceneUsesNav(manifest)).toBe(true); // a prefab walks
    expect(sceneUsesNav({ ...manifest, prefabs: [] })).toBe(false);

    const scene = new Scene();
    const world = new World(scene);
    const root = new Group();
    const floor = new Mesh(new BoxGeometry(20, 1, 20).translate(0, -0.5, 0));
    floor.userData.gs_id = 'gs_floor';
    const a = new Object3D();
    a.userData.gs_id = 'gs_w1';
    a.position.set(-2, 0, 0);
    const b = new Object3D();
    b.userData.gs_id = 'gs_w2';
    b.position.set(2, 0, 0);
    root.add(floor, a, b);
    scene.add(root);
    buildEntities(world, root, manifest);
    const prefabs = new Prefabs(() => {});
    prefabs.load(manifest.prefabs, new Map([['main.prefabs.glb', { scene: prefabScene, animations: [] } as unknown as GLTF]]));
    for (const entry of manifest.entities) if (entry.prefab) prefabs.instantiateUnder(world, entry.prefab, world.get(entry.id)!);
    expect(world.get('gs_w1/gs_seg')).toBeDefined();

    const physics = new RapierPhysics();
    physics.reset(world, [0, -9.81, 0]);
    const nav = new NavService(() => {});
    await nav.reset(world, physics, scene, sceneUsesNav(manifest));
    const path = nav.path([0, 0, -3], [0, 0, 3])!;
    expect(path).not.toBeNull();
    expect(length(path)).toBeGreaterThan(8); // around the 8 m wall the two instances make
    for (const p of samples(path)) expect(distanceToBox(p, 4, 0.25), `${p.toArray()}`).toBeGreaterThan(0.25);
  });
});
