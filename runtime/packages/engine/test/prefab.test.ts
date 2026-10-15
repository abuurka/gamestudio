import {
  Bone, BufferGeometry, DirectionalLight, Euler, Group, Mesh, MeshDepthMaterial, MeshDistanceMaterial, MeshStandardMaterial, Object3D, Quaternion,
  Skeleton, SkinnedMesh, Vector3,
} from 'three';
import type { GLTF } from 'three/addons/loaders/GLTFLoader.js';
import { describe, expect, it } from 'vitest';
import { World } from '../src/ecs/world';
import { Prefabs } from '../src/prefab/prefabs';
import { buildEntities } from '../src/scene/build';
import { cloneScene } from '../src/scene/clone';
import { processManifest } from '../src/scene/manifest';
import '../src/schema'; // Registers the built-in components.

function node<T extends Object3D>(object: T, name: string, id: string | null, position: [number, number, number] = [0, 0, 0]): T {
  object.name = name;
  if (id) object.userData.gs_id = id;
  object.position.fromArray(position);
  return object;
}

/** A prefab GLB as GLTFLoader would return it, plus the manifest that describes it. */
function fixture() {
  const scene = new Group();
  const coin = node(new Mesh(), 'CoinBody', 'gs_c1', [0, 1, 0]);
  coin.add(node(new Object3D(), 'CoinGlint', 'gs_c2', [0, 0.3, 0]));
  const left = node(new Mesh(), 'Left', 'gs_p1', [-1, 0, 0]);
  const right = node(new Mesh(), 'Right', 'gs_p2', [1, 0, 0]);
  const tower = node(new Mesh(), 'TowerBase', 'gs_t1');
  tower.add(node(new Object3D(), 'TowerCoinSpot', 'gs_t2', [0, 3, 0]));
  const rig = node(new Group(), 'Rig', 'gs_r1');
  const bone = node(new Bone(), 'Root', null);
  const body = node(new SkinnedMesh(new BufferGeometry()), 'Body', 'gs_r2');
  rig.add(bone, body);
  body.bind(new Skeleton([bone]));
  scene.add(coin, left, right, tower, rig);
  const gltf = { scene, animations: [] } as unknown as GLTF;

  const raw = {
    format: 'gamestudio.scene',
    formatVersion: 2,
    scene: 'main',
    rev: 1,
    glb: 'main.glb',
    entities: [{ id: 'gs_inst', name: 'CoinInstance', type: 'EMPTY', parent: null, components: {}, prefab: 'Coin' }],
    prefabs: [
      {
        name: 'Coin',
        glb: 'main.prefabs.glb',
        offset: [0, 0.5, 0],
        entities: [
          // Children may come first (GLB node order).
          { id: 'gs_c2', name: 'CoinGlint', type: 'EMPTY', parent: 'gs_c1', components: {} },
          {
            id: 'gs_c1',
            name: 'CoinBody',
            type: 'MESH',
            parent: null,
            components: { Spin: { speed: 3 }, FollowCamera: { target: 'gs_c2' }, Animator: { clip: 'CoinSpin' } },
          },
        ],
        animations: [{ name: 'CoinSpin', duration: 1, entities: ['gs_c1'] }],
      },
      {
        name: 'Pair',
        glb: 'main.prefabs.glb',
        entities: [
          { id: 'gs_p1', name: 'Left', type: 'MESH', parent: null, components: { Spin: {} } },
          { id: 'gs_p2', name: 'Right', type: 'MESH', parent: null, components: { FollowCamera: { target: 'gs_outside' } } },
        ],
      },
      {
        name: 'Tower',
        glb: 'main.prefabs.glb',
        entities: [
          { id: 'gs_t1', name: 'TowerBase', type: 'MESH', parent: null, components: {} },
          { id: 'gs_t2', name: 'TowerCoinSpot', type: 'EMPTY', parent: 'gs_t1', components: {}, prefab: 'Coin' },
        ],
      },
      {
        name: 'Rigged',
        glb: 'main.prefabs.glb',
        entities: [
          { id: 'gs_r1', name: 'Rig', type: 'ARMATURE', parent: null, components: {} },
          { id: 'gs_r2', name: 'Body', type: 'MESH', parent: 'gs_r1', components: {} },
        ],
      },
    ],
  };
  const { manifest, warnings } = processManifest(raw);
  expect(warnings).toEqual([]);
  const prefabs = new Prefabs(() => {});
  prefabs.load(manifest.prefabs, new Map([['main.prefabs.glb', gltf]]));
  return { prefabs, manifest, gltf, templateCoin: coin };
}

const pos = (o: Object3D) => {
  o.updateWorldMatrix(true, false);
  return [o.matrixWorld.elements[12], o.matrixWorld.elements[13], o.matrixWorld.elements[14]].map((n) => Math.round(n! * 1e6) / 1e6);
};

describe('Prefabs.spawn', () => {
  it('clones a single-root prefab with its components, placing the root object', () => {
    const { prefabs, templateCoin } = fixture();
    const world = new World();
    expect(prefabs.names()).toEqual(['Coin', 'Pair', 'Tower', 'Rigged']);
    expect(prefabs.has('Coin')).toBe(true);

    const coin = prefabs.spawn(world, 'Coin', { position: [5, 0, 0], rotation: [0, Math.PI / 2, 0] });
    expect(coin.name).toBe('CoinBody');
    expect(coin.id).toMatch(/^rt_\d+$/);
    expect(coin.parentId).toBeNull();
    expect(coin.object3d.parent).toBe(world.root);
    expect(coin.object3d.position.toArray()).toEqual([5, 0, 0]);
    expect(coin.object3d.quaternion.angleTo(new Quaternion().setFromEuler(new Euler(0, Math.PI / 2, 0)))).toBeLessThan(1e-9);
    expect(coin.get('Spin')).toEqual({ speed: 3, axis: [0, 1, 0] });
    const glint = world.findByName('CoinGlint')!;
    expect(glint.parentId).toBe(coin.id);
    expect(glint.object3d.parent).toBe(coin.object3d);
    expect(coin.get<{ target: string }>('FollowCamera').target).toBe(glint.id); // Remapped into the instance.
    expect(coin.object3d.userData).toMatchObject({ gs_id: coin.id, gs_template: 'gs_c1' });
    expect(templateCoin.userData.gs_id).toBe('gs_c1'); // The template is untouched.

    const atOrigin = prefabs.spawn(world, 'Coin', { scale: 2, name: 'Bonus' });
    expect(atOrigin.name).toBe('Bonus');
    expect(atOrigin.object3d.position.toArray()).toEqual([0, 0.5, 0]); // As a Blender instance at the origin.
    expect(atOrigin.object3d.scale.toArray()).toEqual([2, 2, 2]);
    expect(world.query('Spin')).toHaveLength(2);
  });

  it('wraps multi-root prefabs in a group, and destroy() removes the whole instance', () => {
    const { prefabs } = fixture();
    const world = new World();
    const holder = world.spawn({ name: 'Holder' });
    const pair = prefabs.spawn(world, 'Pair', { position: [0, 2, 0], parent: holder });
    expect(pair.name).toBe('Pair');
    expect(pair.parentId).toBe(holder.id);
    expect(pair.object3d.parent).toBe(holder.object3d);
    const right = world.findByName('Right')!;
    expect(right.parentId).toBe(pair.id);
    expect(pos(right.object3d)).toEqual([1, 2, 0]);
    expect(right.get<{ target: string }>('FollowCamera').target).toBe('gs_outside'); // Outside refs stay.
    world.root.attach(right.object3d); // As physics does for dynamic bodies.
    pair.destroy();
    expect(right.alive).toBe(false);
    expect(world.all().map((e) => e.name)).toEqual(['Holder']);
  });

  it('rebinds skinned meshes to the cloned bones', () => {
    const { prefabs } = fixture();
    const world = new World();
    const rig = prefabs.spawn(world, 'Rigged');
    const body = world.findByName('Body')!.object3d as SkinnedMesh;
    const bone = body.skeleton.bones[0]!;
    expect(bone.parent).toBe(rig.object3d);
  });

  it('throws a helpful error for unknown prefabs', () => {
    const { prefabs } = fixture();
    expect(() => prefabs.spawn(new World(), 'Enemy')).toThrow('Unknown prefab "Enemy". Prefabs in this scene: Coin, Pair, Tower, Rigged');
    expect(() => new Prefabs().spawn(new World(), 'Enemy')).toThrow(/This scene has no prefabs/);
  });
});

describe('collection instances placed in Blender', () => {
  it('are instantiated under the instancer with stable ids, nested prefabs included', () => {
    const { prefabs, manifest } = fixture();
    const world = new World();
    const root = new Group();
    const instancer = node(new Object3D(), 'CoinInstance', 'gs_inst', [-3, -0.5, 0]); // Offset already baked in.
    root.add(instancer);
    world.root.add(root);
    buildEntities(world, root, manifest);
    const inst = world.get('gs_inst')!;
    prefabs.instantiateUnder(world, manifest.entities[0]!.prefab!, inst);
    const body = world.get('gs_inst/gs_c1')!;
    expect(body.parentId).toBe('gs_inst');
    expect(pos(body.object3d)).toEqual([-3, 0.5, 0]);
    expect(body.get<{ target: string }>('FollowCamera').target).toBe('gs_inst/gs_c2');
    expect(world.get('gs_inst/gs_c2')!.parentId).toBe('gs_inst/gs_c1');

    const tower = prefabs.spawn(world, 'Tower');
    const spot = world.findByName('TowerCoinSpot')!;
    const nested = world.get(`${spot.id}/gs_c1`)!;
    expect(nested.parentId).toBe(spot.id);
    expect(pos(nested.object3d)).toEqual([0, 4, 0]); // spot (0,3,0) + coin (0,1,0)
    // The parts of one instance, nested prefabs included; other instances are left out.
    expect(world.descendants(tower).map((e) => e.name)).toEqual(['TowerCoinSpot', 'CoinBody', 'CoinGlint']);
    expect(world.descendants(tower, 'Spin')).toEqual([nested]);
    tower.destroy();
    expect(nested.alive).toBe(false);
    inst.destroy();
    expect(world.size).toBe(0);
  });
});

describe('cloneScene', () => {
  it('copies a scene graph that shares its resources, with light targets still under their lights', () => {
    const root = new Group();
    const sun = new DirectionalLight();
    sun.position.set(0, 5, 0);
    sun.target.position.set(0, 0, -1);
    sun.add(sun.target); // as GLTFLoader does
    const mesh = new Mesh(new BufferGeometry(), new MeshStandardMaterial());
    root.add(sun, mesh);
    const copy = cloneScene(root);
    const [sunCopy, meshCopy] = copy.children as [DirectionalLight, Mesh];
    expect(sunCopy).not.toBe(sun);
    expect(sunCopy.target).toBe(sunCopy.children[0]);
    expect(meshCopy.geometry).toBe(mesh.geometry);
    expect(meshCopy.material).toBe(mesh.material);
    sunCopy.position.x = 3;
    copy.updateMatrixWorld(true);
    expect(sunCopy.target.getWorldPosition(new Vector3()).toArray()).toEqual([3, 5, -1]);
    expect(sun.target.parent).toBe(sun); // the source is untouched
  });

  it('keeps the shadow materials a game shader gave a mesh (ctx.spawn copies cast the same shadow)', () => {
    const root = new Group();
    const swaying = new Mesh(new BufferGeometry(), new MeshStandardMaterial());
    swaying.customDepthMaterial = new MeshDepthMaterial();
    swaying.customDistanceMaterial = new MeshDistanceMaterial();
    const plain = new Mesh(new BufferGeometry(), new MeshStandardMaterial());
    root.add(swaying, plain);
    const [swayingCopy, plainCopy] = cloneScene(root).children as [Mesh, Mesh];
    expect(swayingCopy.customDepthMaterial).toBe(swaying.customDepthMaterial);
    expect(swayingCopy.customDistanceMaterial).toBe(swaying.customDistanceMaterial);
    expect(plainCopy.customDepthMaterial).toBeUndefined();
    expect(plainCopy.customDistanceMaterial).toBeUndefined();
  });
});
