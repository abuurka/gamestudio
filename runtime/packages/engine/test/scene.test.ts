import { Camera, DirectionalLight, Group, Object3D, Scene } from 'three';
import { describe, expect, it } from 'vitest';
import { World } from '../src/ecs/world';
import { RapierPhysics } from '../src/physics/rapier-backend';
import { buildEntities } from '../src/scene/build';
import { processManifest } from '../src/scene/manifest';
import { loadFixtureGlb, readFixtureManifest, worldY } from './helpers';

const minimal = (entities: unknown[], extra: Record<string, unknown> = {}) => ({
  format: 'gamestudio.scene',
  formatVersion: 1,
  scene: 't',
  rev: 3,
  glb: 't.glb',
  entities,
  ...extra,
});

describe('processManifest', () => {
  it('accepts the Blender-exported fixture', () => {
    const { manifest, warnings } = processManifest(readFixtureManifest());
    expect(warnings).toEqual([]);
    expect(manifest.scene).toBe('basic');
    expect(manifest.entities).toHaveLength(8);
    expect(manifest.settings).toEqual({ gravity: [0, -9.81, 0], background: [0.05, 0.06, 0.09], activeCamera: 'gs_000000a00007', sky: null, fog: null, toneMapping: 'aces', exposure: 0 });
  });

  it('fills missing component fields from schema defaults', () => {
    const { manifest } = processManifest(
      minimal([{ id: 'gs_000000000001', name: 'Box', type: 'MESH', parent: null, components: { RigidBody: { mass: 3 }, Collider: {} } }]),
    );
    expect(manifest.entities[0]!.components).toEqual({
      RigidBody: { type: 'dynamic', mass: 3, linearDamping: 0, angularDamping: 0.05, gravityScale: 1, lockRotation: false, ccd: false, lockX: false, lockY: false, lockZ: false },
      Collider: { shape: 'auto', friction: 0.5, restitution: 0, sensor: false },
    });
  });

  it('defaults settings, keeps unknown components and warns about problems', () => {
    const raw = minimal([
      { id: 'gs_000000000001', components: { Mystery: { x: 1 } } },
      { id: 'gs_000000000001', name: 'dup' },
      { name: 'no id' },
    ]);
    const { manifest, warnings } = processManifest(raw);
    expect(manifest.settings).toEqual({ gravity: [0, -9.81, 0], background: null, activeCamera: null, sky: null, fog: null, toneMapping: 'aces', exposure: 0 });
    expect(manifest.entities).toEqual([
      { id: 'gs_000000000001', name: 'gs_000000000001', type: 'EMPTY', parent: null, components: { Mystery: { x: 1 } } },
    ]);
    expect(warnings.join('\n')).toMatch(/Duplicate entity id/);
    expect(warnings.join('\n')).toMatch(/without an id/);
    expect(warnings.join('\n')).toMatch(/"Mystery" is used in the scene but not defined/);
    // The input object is not modified.
    expect((raw.entities[0] as { name?: string }).name).toBeUndefined();
  });

  it('reads format 2: clips, prefab templates, prefab instances and exported assets', () => {
    const { manifest, warnings } = processManifest(
      minimal(
        [
          { id: 'gs_000000000001', name: 'CoinInstance', type: 'EMPTY', parent: null, components: {}, prefab: 'Coin' },
          { id: 'gs_000000000002', name: 'Rig', type: 'ARMATURE', parent: null, components: {}, animations: ['Wave', 4] },
        ],
        {
          formatVersion: 2,
          animations: [{ name: 'Wave', duration: 1.5, entities: ['gs_000000000002', 7] }, { duration: 2 }],
          prefabs: [
            {
              name: 'Coin',
              glb: 't.prefabs.glb',
              offset: [0, 0.5, 0],
              entities: [{ id: 'gs_00000000000c', name: 'CoinBody', type: 'MESH', parent: null, components: { Animator: { clip: 'Spin' } } }],
              animations: [{ name: 'Spin', duration: 1, entities: ['gs_00000000000c'] }],
            },
            { name: 'Coin', glb: 'x.glb' },
            { name: 'NoFile' },
          ],
          assets: { 'assets/a.ogg': 'files/assets/a.ogg?v=1', bad: 3 },
        },
      ),
    );
    expect(manifest.entities[0]!.prefab).toBe('Coin');
    expect(manifest.entities[0]!.animations).toBeUndefined();
    expect(manifest.entities[1]!.animations).toEqual(['Wave']);
    expect(manifest.animations).toEqual([{ name: 'Wave', duration: 1.5, entities: ['gs_000000000002'] }]);
    expect(manifest.prefabs).toEqual([
      {
        name: 'Coin',
        glb: 't.prefabs.glb',
        offset: [0, 0.5, 0],
        entities: [
          {
            id: 'gs_00000000000c',
            name: 'CoinBody',
            type: 'MESH',
            parent: null,
            components: { Animator: { clip: 'Spin', loop: true, speed: 1, playOnStart: true, fade: 0.25, graph: '', state: '' } },
          },
        ],
        animations: [{ name: 'Spin', duration: 1, entities: ['gs_00000000000c'] }],
      },
    ]);
    expect(manifest.assets).toEqual({ 'assets/a.ogg': 'files/assets/a.ogg?v=1' });
    expect(warnings).toEqual([
      'Duplicate prefab "Coin"; keeping the first',
      'Skipping a prefab without a name or glb ("NoFile")',
    ]);
  });

  it('reads format 1 files (no clips, prefabs or assets) and warns about newer formats', () => {
    const old = processManifest(minimal([]));
    expect(old.manifest).toMatchObject({ formatVersion: 1, animations: [], prefabs: [], assets: {} });
    expect(old.warnings).toEqual([]);
    expect(processManifest(minimal([], { formatVersion: 3 })).warnings).toEqual([
      'Scene formatVersion 3 is newer than this runtime supports (2)',
    ]);
  });

  it('reads GLB content hashes and the poses of scene entities that moved after their GLB was written', () => {
    const { manifest, warnings } = processManifest(
      minimal(
        [
          { id: 'gs_000000000001', name: 'A', transform: { p: [1, 2, 3], r: [0, 0, 0, 1], s: [2, 2, 2] } },
          { id: 'gs_000000000002', name: 'B', transform: { p: [1, 2], r: 'x', s: [1, 1, 1] } },
          { id: 'gs_000000000003', name: 'C', transform: { p: [1, Number.NaN, 3] } },
        ],
        {
          formatVersion: 2,
          files: { 't.glb': '3f2a9c01b7de4a10', 'bad.glb': 7, '': 'x' },
          prefabs: [{ name: 'P', glb: 't.prefabs.glb', entities: [{ id: 'gs_00000000000c', transform: { p: [1, 1, 1] } }] }],
        },
      ),
    );
    expect(warnings).toEqual([]);
    expect(manifest.files).toEqual({ 't.glb': '3f2a9c01b7de4a10' });
    expect(manifest.entities.map((e) => e.transform)).toEqual([{ p: [1, 2, 3], r: [0, 0, 0, 1], s: [2, 2, 2] }, { s: [1, 1, 1] }, undefined]);
    expect('transform' in manifest.entities[2]!).toBe(false);
    expect(manifest.prefabs[0]!.entities[0]!.transform).toBeUndefined(); // templates never have one
    // Older exports have no files.
    expect('files' in processManifest(minimal([])).manifest).toBe(false);
    expect(processManifest(minimal([], { files: ['t.glb'] })).warnings).toEqual(['Ignoring "files": not an object']);
  });

  it('rejects files that are not scene manifests', () => {
    expect(() => processManifest({ format: 'other' })).toThrow(/Not a GameStudio scene/);
    expect(() => processManifest(null)).toThrow();
    expect(() => processManifest({ format: 'gamestudio.scene' })).toThrow(/no "glb"/);
  });
});

describe('buildEntities', () => {
  it('maps objects by userData.gs_id only, never by name', () => {
    const root = new Group();
    const a = new Object3D();
    a.name = 'Cube001'; // GLTFLoader-style rewritten name.
    a.userData.gs_id = 'gs_00000000000a';
    const decoy = new Object3D();
    decoy.name = 'Cube.001'; // Same as the Blender name, but no gs_id.
    root.add(a, decoy);
    const { manifest } = processManifest(
      minimal([
        { id: 'gs_00000000000a', name: 'Cube.001', type: 'MESH', parent: null, components: { Spin: {} } },
        { id: 'gs_00000000000b', name: 'Missing', type: 'EMPTY', parent: 'gs_00000000000c', components: {} },
        { id: 'gs_00000000000c', name: 'AlsoMissing', type: 'EMPTY', parent: 'gs_00000000000a', components: {} },
      ]),
    );
    const world = new World(new Scene());
    const { entities, missing } = buildEntities(world, root, manifest);
    expect(entities.map((e) => e.name)).toEqual(['Cube.001', 'Missing', 'AlsoMissing']);
    expect(world.get('gs_00000000000a')!.object3d).toBe(a);
    expect(world.get('gs_00000000000a')!.get('Spin')).toEqual({ speed: 1, axis: [0, 1, 0] });
    // Missing nodes become Groups under their (possibly also missing) parent.
    const missingObj = world.get('gs_00000000000b')!.object3d;
    const alsoMissingObj = world.get('gs_00000000000c')!.object3d;
    expect(missingObj).toBeInstanceOf(Group);
    expect(missingObj.parent).toBe(alsoMissingObj);
    expect(alsoMissingObj.parent).toBe(a);
    expect(missing.sort()).toEqual(['gs_00000000000b', 'gs_00000000000c']);
    expect(world.entityOf(decoy)).toBeUndefined();
  });

  it('builds the real fixture GLB, and its crate falls onto the ground', async () => {
    const gltf = await loadFixtureGlb();
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
    const lamp = world.findByName('CrateLamp')!;
    expect(lamp.object3d.parent).toBe(crate.object3d);
    expect(lamp.parentId).toBe(crate.id);
    expect(world.findByName('Camera')!.object3d).toBeInstanceOf(Camera);
    expect(world.findByName('Sun')!.object3d).toBeInstanceOf(DirectionalLight);
    expect(worldY(crate.object3d)).toBeCloseTo(4, 3);

    for (let i = 0; i < 360; i++) physics.step(1 / 60);
    expect(crate.object3d.parent).toBe(scene); // Reparented for physics, keeping the lamp.
    expect(lamp.object3d.parent).toBe(crate.object3d);
    expect(worldY(crate.object3d)).toBeCloseTo(0.5, 1);
    expect(physics.bodyCount).toBe(5); // Ground, Crate, Ramp, Player, Coin.
  });
});
