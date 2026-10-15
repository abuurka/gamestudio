// `npm run build` compresses scene GLBs with EXT_meshopt_compression (devserver/src/compress.js). The
// fixture basic.meshopt.glb is basic.glb run through it:
//   node runtime/packages/devserver/bin/gamestudio-compress.js runtime/fixtures/basic/basic.meshopt.glb
import { readFileSync } from 'node:fs';
import { Mesh, Scene, type BufferAttribute, type Object3D } from 'three';
import { describe, expect, it } from 'vitest';
import { World } from '../src/ecs/world';
import { RapierPhysics } from '../src/physics/rapier-backend';
import { buildEntities } from '../src/scene/build';
import { parseGlb, type SceneFiles } from '../src/scene/loader';
import { processManifest } from '../src/scene/manifest';
import { FIXTURE_DIR, readFixtureManifest, worldY } from './helpers';

function files(name: string): SceneFiles {
  const buf = readFileSync(`${FIXTURE_DIR}${name}`);
  const glb = buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength) as ArrayBuffer;
  return { manifestUrl: '', raw: {}, glb, resourcePath: '', prefabGlbs: new Map() };
}

function snapshot(root: Object3D) {
  const out: unknown[] = [];
  root.traverse((o) => {
    const attributes =
      o instanceof Mesh
        ? Object.fromEntries(Object.entries(o.geometry.attributes).map(([k, a]) => [k, Array.from((a as BufferAttribute).array)]))
        : null;
    out.push({ name: o.name, gs_id: o.userData.gs_id, position: o.position.toArray(), attributes });
  });
  return out;
}

describe('compressed scene GLBs (EXT_meshopt_compression)', () => {
  it('the fixture really is compressed', () => {
    const buf = readFileSync(`${FIXTURE_DIR}basic.meshopt.glb`);
    const json = JSON.parse(buf.subarray(20, 20 + buf.readUInt32LE(12)).toString('utf8'));
    expect(json.extensionsRequired).toContain('EXT_meshopt_compression');
  });

  it('loads like the uncompressed export: nodes, ids and vertex data', async () => {
    const plain = await parseGlb(files('basic.glb'));
    const compressed = await parseGlb(files('basic.meshopt.glb'));
    expect(snapshot(compressed.scene)).toEqual(snapshot(plain.scene));
  });

  it('builds the entities, and its crate falls onto the ground', async () => {
    const gltf = await parseGlb(files('basic.meshopt.glb'));
    const { manifest } = processManifest(readFixtureManifest());
    const scene = new Scene();
    scene.add(gltf.scene);
    const world = new World(scene);
    const physics = new RapierPhysics();
    await physics.init();
    physics.reset(world, { x: 0, y: -9.81, z: 0 });
    const { missing } = buildEntities(world, gltf.scene, manifest);
    expect(missing).toEqual([]);
    expect(world.size).toBe(8);
    const crate = world.findByName('Crate')!;
    for (let i = 0; i < 360; i++) physics.step(1 / 60);
    expect(worldY(crate.object3d)).toBeCloseTo(0.5, 1);
    expect(physics.bodyCount).toBe(5);
  });
});
