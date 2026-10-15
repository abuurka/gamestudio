// Colliders on GPU instances (EXT_mesh_gpu_instancing → InstancedMesh, e.g. a geometry-nodes
// scatter) count every copy at its instance matrix, and collision-only entities (`drawn: false`)
// shape their collider without being drawn.
import {
  BoxGeometry,
  BufferGeometry,
  Camera,
  Float32BufferAttribute,
  Group,
  InstancedMesh,
  Matrix4,
  Mesh,
  MeshBasicMaterial,
  Quaternion,
  Scene,
  Vector3,
  type Object3D,
} from 'three';
import { GLTFLoader, type GLTF } from 'three/addons/loaders/GLTFLoader.js';
import { beforeAll, describe, expect, it } from 'vitest';
import type { Entity } from '../src/ecs/entity';
import { World } from '../src/ecs/world';
import { RapierPhysics } from '../src/physics/rapier-backend';
import { collectLocalGeometry, shapeFromGeometry } from '../src/physics/shapes';
import { Prefabs } from '../src/prefab/prefabs';
import { buildEntities, COLLISION_LAYER } from '../src/scene/build';
import { processManifest } from '../src/scene/manifest';
import { Collider, RigidBody } from '../src/schema';

const DT = 1 / 60;
const Y = new Vector3(0, 1, 0);

/** Crates (unit cubes) in the scatter parent's space: [translation, rotation about Y (rad), scale, top y]. */
const CRATES: Array<{ t: [number, number, number]; yaw: number; s: [number, number, number]; top: number }> = [
  { t: [1.5, 0.5, -2.5], yaw: 0, s: [1, 1, 1], top: 1 },
  { t: [3, 0.5, 0], yaw: Math.PI / 4, s: [1, 1, 1], top: 1 },
  { t: [0, 1, 3], yaw: 0.3, s: [2, 2, 2], top: 2 },
  { t: [-3, 0.25, -2], yaw: 0, s: [1, 0.5, 1], top: 0.5 },
];
const PARENT_AT = new Vector3(10, 0, -5);

beforeAll(async () => {
  await new RapierPhysics().init();
});

function setup() {
  const world = new World(new Scene());
  const physics = new RapierPhysics();
  physics.reset(world, { x: 0, y: -9.81, z: 0 });
  const ground = new Mesh(new BoxGeometry(200, 1, 200));
  ground.position.set(0, -0.5, 0);
  world.spawn({ name: 'Ground', object3d: ground, components: [[Collider]] });
  return { world, physics };
}

function crateMatrix(c: (typeof CRATES)[number]): Matrix4 {
  return new Matrix4().compose(new Vector3(...c.t), new Quaternion().setFromAxisAngle(Y, c.yaw), new Vector3(...c.s));
}

/** A scatter parent (an empty) holding the crates as one InstancedMesh, as GLTFLoader builds it. */
function scatter(world: World, components: Record<string, Record<string, unknown>>, crates = CRATES): Entity {
  const parent = new Group();
  parent.name = 'Scatter';
  parent.position.copy(PARENT_AT);
  const copies = new InstancedMesh(new BoxGeometry(1, 1, 1), new MeshBasicMaterial(), crates.length);
  crates.forEach((c, i) => copies.setMatrixAt(i, crateMatrix(c)));
  parent.add(copies);
  return world.spawn({ name: 'Scatter', object3d: parent, components });
}

/** World (x, z) of a point in the parent's space. */
function at(x: number, z: number): [number, number] {
  return [PARENT_AT.x + x, PARENT_AT.z + z];
}

function down(physics: RapierPhysics, [x, z]: [number, number]) {
  const hit = physics.raycast([x, 50, z], [0, -1, 0], { maxDistance: 100 });
  return hit ? { entity: hit.entity.name, y: hit.point.y } : null;
}

// --- A minimal GLB with EXT_mesh_gpu_instancing (what the export writes for a scatter) ----------

function glb(json: Record<string, unknown>, bin: Uint8Array): ArrayBuffer {
  const text = new TextEncoder().encode(JSON.stringify(json));
  const pad = (n: number) => (4 - (n % 4)) % 4;
  const jsonChunk = new Uint8Array(text.length + pad(text.length)).fill(0x20);
  jsonChunk.set(text);
  const binChunk = new Uint8Array(bin.length + pad(bin.length));
  binChunk.set(bin);
  const total = 12 + 8 + jsonChunk.length + 8 + binChunk.length;
  const out = new Uint8Array(total);
  const view = new DataView(out.buffer);
  view.setUint32(0, 0x46546c67, true);
  view.setUint32(4, 2, true);
  view.setUint32(8, total, true);
  view.setUint32(12, jsonChunk.length, true);
  view.setUint32(16, 0x4e4f534a, true);
  out.set(jsonChunk, 20);
  view.setUint32(20 + jsonChunk.length, binChunk.length, true);
  view.setUint32(24 + jsonChunk.length, 0x004e4942, true);
  out.set(binChunk, 28 + jsonChunk.length);
  return out.buffer;
}

/** A scene node "Scatter" (gs_scatter) whose child draws the crates through EXT_mesh_gpu_instancing. */
function scatterGlb(): Promise<GLTF> {
  const box = new BoxGeometry(1, 1, 1);
  const positions = box.getAttribute('position').array as Float32Array;
  const indices = new Uint16Array(box.getIndex()!.array);
  const translations = new Float32Array(CRATES.flatMap((c) => c.t));
  const rotations = new Float32Array(CRATES.flatMap((c) => new Quaternion().setFromAxisAngle(Y, c.yaw).toArray()));
  const scales = new Float32Array(CRATES.flatMap((c) => c.s));
  const parts = [positions, indices, translations, rotations, scales];
  const views: Array<{ buffer: 0; byteOffset: number; byteLength: number }> = [];
  let size = 0;
  for (const part of parts) {
    views.push({ buffer: 0, byteOffset: size, byteLength: part.byteLength });
    size += part.byteLength + ((4 - (part.byteLength % 4)) % 4);
  }
  const bin = new Uint8Array(size);
  parts.forEach((part, i) => bin.set(new Uint8Array(part.buffer, part.byteOffset, part.byteLength), views[i]!.byteOffset));
  const json = {
    asset: { version: '2.0' },
    extensionsUsed: ['EXT_mesh_gpu_instancing'],
    scene: 0,
    scenes: [{ nodes: [0] }],
    nodes: [
      { name: 'Scatter', translation: PARENT_AT.toArray(), children: [1], extras: { gs_id: 'gs_scatter' } },
      {
        name: 'Scatter.Crate.instances',
        mesh: 0,
        extensions: { EXT_mesh_gpu_instancing: { attributes: { TRANSLATION: 2, ROTATION: 3, SCALE: 4 } } },
      },
    ],
    meshes: [{ name: 'Crate', primitives: [{ attributes: { POSITION: 0 }, indices: 1 }] }],
    accessors: [
      { bufferView: 0, componentType: 5126, count: positions.length / 3, type: 'VEC3', min: [-0.5, -0.5, -0.5], max: [0.5, 0.5, 0.5] },
      { bufferView: 1, componentType: 5123, count: indices.length, type: 'SCALAR' },
      { bufferView: 2, componentType: 5126, count: CRATES.length, type: 'VEC3' },
      { bufferView: 3, componentType: 5126, count: CRATES.length, type: 'VEC4' },
      { bufferView: 4, componentType: 5126, count: CRATES.length, type: 'VEC3' },
    ],
    bufferViews: views,
    buffers: [{ byteLength: size }],
  };
  return new GLTFLoader().parseAsync(glb(json, bin), '');
}

describe('Colliders on GPU instances', () => {
  for (const shape of ['mesh', 'box', 'auto', 'convex'] as const) {
    it(`a ${shape} collider on a scatter parent covers each instance and nothing else`, () => {
      const { world, physics } = setup();
      scatter(world, { Collider: { shape } });
      physics.step(DT);
      for (const crate of CRATES) {
        const hit = down(physics, at(crate.t[0], crate.t[2]));
        expect(hit?.entity, `crate at ${crate.t}`).toBe('Scatter');
        expect(hit!.y).toBeCloseTo(crate.top, 3);
      }
      // No phantom copy of the source at the parent's origin.
      expect(down(physics, at(0, 0))).toEqual({ entity: 'Ground', y: expect.closeTo(0, 3) });
      // The 45° crate is turned: inside its axis-aligned bounds but outside the crate is empty.
      expect(down(physics, at(3 + 0.45, 0.45))?.entity).toBe('Ground');
      expect(down(physics, at(3 + 0.68, 0))?.entity).toBe('Scatter');
      expect(physics.colliderOf(world.findByName('Scatter')!)).not.toBeNull();
    });
  }

  it('builds one compound collider of boxes, or one trimesh, from the instances', () => {
    const { world } = setup();
    const parent = scatter(world, {});
    const geometry = collectLocalGeometry(parent.object3d, () => false, false);
    expect(geometry.instanced[0]!.matrices).toHaveLength(CRATES.length);
    expect(geometry.box.max.y).toBeCloseTo(2, 5); // the bounds hold the copies
    expect(geometry.plainBox.isEmpty()).toBe(true);
    const scale = new Vector3(1, 1, 1);
    const boxes = shapeFromGeometry('box', geometry, scale);
    expect(boxes.kind).toBe('compound');
    if (boxes.kind === 'compound') {
      expect(boxes.parts.map((p) => p.kind)).toEqual(['box', 'box', 'box', 'box']);
      expect(boxes.parts[2]!.center.toArray()).toEqual([0, 1, 3]);
      expect(boxes.parts[3]!.kind === 'box' && boxes.parts[3]!.halfExtents.toArray()).toEqual([0.5, 0.25, 0.5]);
    }
    const withVertices = collectLocalGeometry(parent.object3d, () => false, true);
    const trimesh = shapeFromGeometry('mesh', withVertices, scale);
    expect(trimesh.kind === 'mesh' && trimesh.vertices.length).toBe(24 * 3 * CRATES.length);
    const hulls = shapeFromGeometry('convex', withVertices, scale);
    expect(hulls.kind === 'compound' && hulls.parts.every((p) => p.kind === 'convex')).toBe(true);
    // A sphere still encloses everything.
    expect(shapeFromGeometry('sphere', geometry, scale).kind).toBe('sphere');
  });

  it('applies the parent world scale to each instance', () => {
    const { world, physics } = setup();
    const parent = scatter(world, { Collider: { shape: 'box' } });
    parent.object3d.scale.set(2, 3, 2);
    physics.rebuild(parent);
    physics.step(DT);
    for (const crate of CRATES) {
      const hit = down(physics, [PARENT_AT.x + 2 * crate.t[0], PARENT_AT.z + 2 * crate.t[2]]);
      expect(hit?.entity).toBe('Scatter');
      expect(hit!.y).toBeCloseTo(3 * crate.top, 3);
    }
  });

  it('keeps the parent mesh and adds the instances (a fence: rails plus instanced posts)', () => {
    const { world, physics } = setup();
    const rails = new Mesh(new BoxGeometry(8, 0.2, 0.2).translate(0, 1, 0));
    rails.position.copy(PARENT_AT);
    const posts = new InstancedMesh(new BoxGeometry(0.2, 1.2, 0.2).translate(0, 0.6, 0), new MeshBasicMaterial(), 3);
    [-3, 0, 3].forEach((x, i) => posts.setMatrixAt(i, new Matrix4().makeTranslation(x, 0, 2)));
    rails.add(posts);
    world.spawn({ name: 'Fence', object3d: rails, components: { Collider: { shape: 'box' } } });
    physics.step(DT);
    expect(down(physics, at(-1.5, 0))).toMatchObject({ entity: 'Fence' }); // a rail
    expect(down(physics, at(-1.5, 0))!.y).toBeCloseTo(1.1, 3);
    for (const x of [-3, 0, 3]) expect(down(physics, at(x, 2))!.y).toBeCloseTo(1.2, 3); // the posts
    expect(down(physics, at(-1.5, 2))?.entity).toBe('Ground'); // between posts, beside the rails
  });

  it('drops a dynamic body made of instances as one rigid compound', () => {
    const { world, physics } = setup();
    // Two crates 1 m above the parent's origin: the body rests with the parent 0.5 m below ground level.
    const pair = [
      { t: [-1, 1, 0] as [number, number, number], yaw: 0, s: [1, 1, 1] as [number, number, number], top: 0 },
      { t: [1, 1, 0] as [number, number, number], yaw: 0, s: [1, 1, 1] as [number, number, number], top: 0 },
    ];
    for (const shape of ['mesh', 'box'] as const) { // a dynamic mesh collider becomes convex hulls
      const body = scatter(world, { RigidBody: {}, Collider: { shape } }, pair);
      body.object3d.position.set(shape === 'mesh' ? -20 : 20, 4, 0);
      physics.rebuild(body);
      for (let i = 0; i < 240; i++) physics.step(DT);
      expect(body.object3d.position.y, shape).toBeCloseTo(-0.5, 1);
      expect(body.object3d.quaternion.angleTo(new Quaternion()), shape).toBeLessThan(0.01);
    }
  });

  it('falls back to a box when a hull has no volume (points on a line)', () => {
    const { world, physics } = setup();
    const line = new BufferGeometry().setAttribute('position', new Float32BufferAttribute([0, 1, 0, 1, 1, 0, 2, 1, 0, 3, 1, 0], 3));
    const plain = new Mesh(line);
    plain.position.set(-30, 0, 0);
    world.spawn({ name: 'Line', object3d: plain, components: { Collider: { shape: 'convex' } } });
    const copies = new InstancedMesh(line, new MeshBasicMaterial(), 2);
    copies.setMatrixAt(1, new Matrix4().makeTranslation(0, 0, 4));
    const holder = new Group();
    holder.position.set(-40, 0, 0);
    holder.add(copies);
    world.spawn({ name: 'Lines', object3d: holder, components: { Collider: { shape: 'convex' } } });
    physics.step(DT);
    expect(down(physics, [-28.5, 0])).toEqual({ entity: 'Line', y: expect.closeTo(1.005, 3) });
    expect(down(physics, [-38.5, 4])).toEqual({ entity: 'Lines', y: expect.closeTo(1.005, 3) });
  });

  it('stays quick for hundreds of instances', () => {
    const { world, physics } = setup();
    const many = Array.from({ length: 600 }, (_, i) => ({
      t: [(i % 30) * 3 - 45, 0.5, Math.floor(i / 30) * 3 - 30] as [number, number, number],
      yaw: i * 0.1,
      s: [1, 1, 1] as [number, number, number],
      top: 1,
    }));
    for (const shape of ['box', 'mesh', 'convex'] as const) {
      const entity = scatter(world, { Collider: { shape } }, many);
      const started = performance.now();
      physics.step(DT);
      const ms = performance.now() - started;
      expect(ms, `${shape}: ${ms.toFixed(1)} ms`).toBeLessThan(1500);
      for (const crate of [many[0]!, many[317]!, many[599]!]) {
        expect(down(physics, at(crate.t[0], crate.t[2]))).toEqual({ entity: 'Scatter', y: expect.closeTo(1, 3) });
      }
      entity.destroy();
      physics.step(DT);
    }
  });

  it('builds the collider from a GLB scatter with EXT_mesh_gpu_instancing', async () => {
    const gltf = await scatterGlb();
    const holder = gltf.scene.children[0]!.children[0] as InstancedMesh;
    expect(holder.isInstancedMesh).toBe(true);
    const { world, physics } = setup();
    world.root.add(gltf.scene);
    const { manifest } = processManifest({
      format: 'gamestudio.scene',
      formatVersion: 2,
      glb: 'x.glb',
      entities: [{ id: 'gs_scatter', name: 'Scatter', type: 'EMPTY', parent: null, components: { Collider: { shape: 'mesh' } } }],
    });
    buildEntities(world, gltf.scene, manifest);
    physics.step(DT);
    for (const crate of CRATES) {
      expect(down(physics, at(crate.t[0], crate.t[2]))?.y).toBeCloseTo(crate.top, 3);
    }
    expect(down(physics, at(0, 0))?.entity).toBe('Ground');
  });
});

describe('Collision-only entities (drawn: false)', () => {
  const camera = new Camera();
  const drawn = (o: Object3D): boolean => o.layers.test(camera.layers);

  function proxyScene() {
    const root = new Group();
    const wall = new Mesh(new BoxGeometry(4, 2, 0.5).translate(0, 1, 0));
    wall.name = 'Wall';
    wall.userData.gs_id = 'gs_wall';
    const lamp = new Mesh(new BoxGeometry(0.2, 0.2, 0.2));
    lamp.userData.gs_id = 'gs_lamp';
    lamp.position.y = 2.2;
    const trim = new Mesh(new BoxGeometry(4, 0.1, 0.6)); // a part of the wall's own mesh (no gs_id)
    wall.add(trim, lamp);
    root.add(wall);
    return { root, wall, lamp, trim };
  }

  it('keeps the collider but moves the meshes off the camera layer; child entities stay drawn', () => {
    const { world, physics } = setup();
    const { root, wall, lamp, trim } = proxyScene();
    world.root.add(root);
    const { manifest } = processManifest({
      format: 'gamestudio.scene',
      formatVersion: 2,
      glb: 'x.glb',
      entities: [
        { id: 'gs_wall', name: 'Wall', type: 'MESH', parent: null, components: { Collider: {} }, drawn: false },
        { id: 'gs_lamp', name: 'Lamp', type: 'MESH', parent: 'gs_wall', components: {} },
      ],
    });
    expect(manifest.entities.map((e) => e.drawn)).toEqual([false, undefined]);
    buildEntities(world, root, manifest);
    expect([drawn(wall), drawn(trim), drawn(lamp)]).toEqual([false, false, true]);
    expect(wall.layers.isEnabled(COLLISION_LAYER)).toBe(true);
    expect(wall.visible).toBe(true); // visibility (and with it the lamp) is untouched
    physics.step(DT);
    expect(down(physics, [0, 0])).toEqual({ entity: 'Wall', y: expect.closeTo(2, 3) });
  });

  it('applies to prefab templates and their clones', () => {
    const { root, wall, lamp } = proxyScene();
    const prefabs = new Prefabs(() => {});
    prefabs.load(
      [
        {
          name: 'Barrier',
          glb: 'p.glb',
          offset: [0, 0, 0],
          animations: [],
          entities: [
            { id: 'gs_wall', name: 'Wall', type: 'MESH', parent: null, components: { Collider: {} }, drawn: false },
            { id: 'gs_lamp', name: 'Lamp', type: 'MESH', parent: 'gs_wall', components: {} },
          ],
        },
      ],
      new Map([['p.glb', { scene: root } as unknown as GLTF]]),
    );
    expect([drawn(wall), drawn(lamp)]).toEqual([false, true]);
    const world = new World(new Scene());
    const instance = prefabs.spawn(world, 'Barrier', { position: [5, 0, 0] });
    expect(drawn(instance.object3d)).toBe(false);
    expect(drawn(world.findByName('Lamp')!.object3d)).toBe(true);
  });
});
