// X-Ray Silhouette: the extra draw `mesh` adds (a child that follows the mesh's skeleton, morphs and
// GPU instances, never casts shadows, never takes a ray, is not an entity), its GLSL, live values,
// and that applying again or cloning (prefab instances) leaves exactly one extra draw.
import { createHash } from 'node:crypto';
import { mkdtempSync, readFileSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import {
  CustomBlending,
  GreaterDepth,
  Group,
  Mesh,
  Raycaster,
  Vector3,
  type InstancedMesh,
  type Material,
  type Object3D,
  type ShaderMaterial,
  type SkinnedMesh,
} from 'three';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { Game } from '../../src/game/game';
import { RapierPhysics } from '../../src/physics/rapier-backend';
import { cloneScene } from '../../src/scene/clone';
import { getShader } from '../../src/schema';
import { installDom, type FakeDom } from '../dom';
import { sceneFiles } from '../game-fixture';
import { FIXTURE_DIR } from '../helpers';
import { compilePatched, PREVIEW_MATERIAL, resolveIncludes } from './helpers';

const passesOf = (mesh: Mesh): Mesh[] => mesh.children.filter((c) => c.userData.gsShaderPass?.shader === 'XRaySilhouette') as Mesh[];
const before = (pass: Mesh): void => pass.onBeforeRender({ getPixelRatio: () => 1, getCurrentViewport: (v: { set: (...n: number[]) => unknown }) => v.set(0, 0, 800, 600) } as never, null as never, null as never, pass.geometry, pass.material as Material, null as never);

/** Uniforms a pass shader declares that neither its material nor three.js provides. */
function missingUniforms(material: ShaderMaterial): string[] {
  const provided = new Set(['modelMatrix', 'modelViewMatrix', 'projectionMatrix', 'viewMatrix', 'normalMatrix', 'cameraPosition', 'isOrthographic', 'bindMatrix', 'bindMatrixInverse', 'boneTexture', 'morphTargetBaseInfluence', 'morphTargetInfluences', 'morphTargetsTexture', 'morphTargetsTextureSize', 'logDepthBufFC', 'toneMappingExposure', 'clippingPlanes', 'batchingTexture', 'batchingIdTexture', 'batchingColorTexture', 'morphTexture']);
  const out: string[] = [];
  for (const source of [material.vertexShader, material.fragmentShader]) {
    for (const match of source.matchAll(/^\s*uniform\s+\w+\s+(\w+)/gm)) {
      if (!provided.has(match[1]!) && !(match[1]! in material.uniforms)) out.push(match[1]!);
    }
  }
  return out;
}

describe('XRaySilhouette', () => {
  it('is a built-in Gameplay shader', () => {
    expect(getShader('XRaySilhouette')).toMatchObject({ builtin: true, category: 'Gameplay' });
  });

  it('adds one extra draw per mesh, drawn only behind other things; the material keeps its look', async () => {
    const c = await compilePatched('XRaySilhouette');
    expect(c.warnings).toEqual([]);
    expect(c.material.customProgramCacheKey()).toBe(c.original.customProgramCacheKey()); // not patched
    const [pass, ...more] = passesOf(c.mesh);
    expect(more).toEqual([]);
    expect(pass).toBeInstanceOf(Mesh);
    expect(pass!.geometry).toBe(c.mesh.geometry);
    expect(pass!.castShadow).toBe(false);
    expect(pass!.userData.gs_id).toBeUndefined(); // never an entity
    expect(c.world.entityOf(pass!)?.object3d).not.toBe(pass);
    const material = pass!.material as ShaderMaterial;
    expect(material.depthFunc).toBe(GreaterDepth);
    expect(material.depthWrite).toBe(false);
    // Drawn after every other opaque object and right before the mesh itself, so the mesh's own
    // parts never hide it: an opaque-list draw (blended all the same) in render order.
    expect(material.transparent).toBe(false);
    expect(material.blending).toBe(CustomBlending);
    expect(pass!.renderOrder).toBeGreaterThan(0);
    expect(c.mesh.renderOrder).toBeGreaterThan(pass!.renderOrder);
    // Every #include resolves and every uniform is provided.
    expect(() => resolveIncludes(material.vertexShader)).not.toThrow();
    expect(() => resolveIncludes(material.fragmentShader)).not.toThrow();
    expect(missingUniforms(material)).toEqual([]);
    // Rays pass through it (picking hits the mesh itself).
    c.mesh.updateMatrixWorld(true);
    const hits = new Raycaster(new Vector3(0, 0, 5), new Vector3(0, 0, -1)).intersectObject(c.mesh, true);
    expect(hits.length).toBeGreaterThan(0);
    expect(hits.every((h) => h.object === c.mesh)).toBe(true);
  });

  it('copies the live values before each draw; strength 0 hides it', async () => {
    const c = await compilePatched('XRaySilhouette', { time: 3 });
    const pass = passesOf(c.mesh)[0]!;
    const u = (pass.material as ShaderMaterial).uniforms;
    c.shaders.set(PREVIEW_MATERIAL, { color: [1, 0.1, 0.1, 1], pingAt: 2.5, minDepth: 0.5 });
    before(pass);
    expect(u.xrColor!.value.toArray()).toEqual([1, 0.1, 0.1, 1]);
    expect(u.xrPingAt!.value).toBe(2.5);
    expect(u.xrDepth!.value).toBe(0.5);
    expect(u.xrTime!.value).toBe(3);
    expect((pass.material as ShaderMaterial).uniformsNeedUpdate).toBe(true);
    c.shaders.set(PREVIEW_MATERIAL, { strength: 0 });
    c.material.onBeforeRender(null as never, null as never, null as never, c.mesh.geometry, c.mesh, null as never);
    expect(pass.visible).toBe(false);
    c.shaders.set(PREVIEW_MATERIAL, { strength: 1 });
    c.material.onBeforeRender(null as never, null as never, null as never, c.mesh.geometry, c.mesh, null as never);
    expect(pass.visible).toBe(true);
  });

  it('gives an entity with its own values its own extra draw (still one)', async () => {
    const c = await compilePatched('XRaySilhouette');
    c.shaders.set(c.entity, { color: [0, 1, 0, 1] });
    expect(c.mesh.material).not.toBe(c.material);
    const passes = passesOf(c.mesh);
    expect(passes).toHaveLength(1);
    before(passes[0]!);
    expect((passes[0]!.material as ShaderMaterial).uniforms.xrColor!.value.toArray()).toEqual([0, 1, 0, 1]);
  });

  it('follows a skinned mesh: a skinned pass on the same skeleton, rebound in copies', async () => {
    const c = await compilePatched('XRaySilhouette', { skinned: true, mesh: 'character' });
    const mesh = c.mesh as SkinnedMesh;
    const pass = passesOf(mesh)[0] as SkinnedMesh;
    expect(pass.isSkinnedMesh).toBe(true);
    expect(pass.skeleton).toBe(mesh.skeleton);
    expect(pass.bindMatrix.equals(mesh.bindMatrix)).toBe(true);
    expect(resolveIncludes((pass.material as ShaderMaterial).vertexShader)).toContain('#ifdef USE_SKINNING');
    // A prefab instance (SkeletonUtils clone) gets a pass bound to the copied bones.
    const copy = cloneScene(c.entity.object3d as Group);
    let copied: SkinnedMesh | null = null;
    copy.traverse((o) => {
      if ((o as SkinnedMesh).isSkinnedMesh && !o.userData.gsShaderPass) copied = o as SkinnedMesh;
    });
    const copyPasses = passesOf(copied!);
    expect(copyPasses).toHaveLength(1);
    const copyPass = copyPasses[0] as SkinnedMesh;
    expect(copyPass.skeleton.bones).toEqual(copied!.skeleton.bones);
    expect(copyPass.skeleton.bones[0]).not.toBe(mesh.skeleton.bones[0]);
    expect(copyPass.raycast).toBe(pass.raycast); // still a pass (its class survives the copy)
  });

  it('follows GPU instances: the same instance matrices and count', async () => {
    const c = await compilePatched('XRaySilhouette', { instanced: true });
    const mesh = c.mesh as InstancedMesh;
    const pass = passesOf(mesh)[0] as InstancedMesh;
    expect(pass.isInstancedMesh).toBe(true);
    expect(pass.instanceMatrix).toBe(mesh.instanceMatrix);
    expect(pass.frustumCulled).toBe(false);
    // A copy shares its own host's instances once drawn.
    const copy = cloneScene(c.entity.object3d as Group);
    const host = copy.children[0] as InstancedMesh;
    const copyPass = passesOf(host)[0] as InstancedMesh;
    host.count = 2;
    before(copyPass);
    expect(copyPass.instanceMatrix).toBe(host.instanceMatrix);
    expect(copyPass.count).toBe(2);
  });

  it('applying again replaces the extra draw instead of adding one', async () => {
    const c = await compilePatched('XRaySilhouette');
    const first = passesOf(c.mesh)[0]!;
    c.shaders.set(c.entity, { strength: 2 }); // a fresh apply on the entity's own copy
    c.shaders.set(c.entity, { strength: 1.5 }); // a uniform only
    const passes = passesOf(c.mesh);
    expect(passes).toHaveLength(1);
    expect(passes[0]).not.toBe(first);
  });
});

// ---------------------------------------------------------------------------
// In a game: the basic fixture, with the Coin also a prefab (a copy of basic.glb), seen through walls.

const BASIC_HASH = createHash('sha1').update(readFileSync(`${FIXTURE_DIR}basic.glb`)).digest('hex').slice(0, 16);
const dir = mkdtempSync(path.join(tmpdir(), 'gs-xray-'));
writeFileSync(path.join(dir, 'xray.prefabs.glb'), readFileSync(`${FIXTURE_DIR}basic.glb`));
const XRAY = '/export/xray.scene.json';
{
  const m = JSON.parse(readFileSync(`${FIXTURE_DIR}basic.scene.json`, 'utf8'));
  const manifest = {
    ...m,
    formatVersion: 2,
    scene: 'xray',
    files: { 'basic.glb': BASIC_HASH, 'xray.prefabs.glb': BASIC_HASH },
    prefabs: [
      { name: 'Coin', glb: 'xray.prefabs.glb', offset: [0, 0, 0], entities: [{ id: 'gs_000000a00006', name: 'Coin', type: 'MESH', parent: null, components: {} }], animations: [] },
    ],
    materials: { CoinMat: { shader: 'XRaySilhouette', values: {} } },
  };
  writeFileSync(path.join(dir, 'xray.scene.json'), JSON.stringify(manifest));
}

function firstMesh(object: Object3D): Mesh {
  let mesh: Mesh | null = null;
  object.traverse((o) => {
    if (!mesh && (o as Mesh).isMesh && !o.userData.gsShaderPass) mesh = o as Mesh;
  });
  return mesh!;
}

describe('XRaySilhouette in a game', () => {
  let dom: FakeDom;
  let game: Game | null = null;
  let warn: { mockRestore(): void };
  beforeAll(async () => {
    await new RapierPhysics().init();
  });
  beforeEach(() => {
    dom = installDom({ ...sceneFiles(), [XRAY]: path.join(dir, 'xray.scene.json'), '/export/xray.prefabs.glb': path.join(dir, 'xray.prefabs.glb') });
    warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
  });
  afterEach(() => {
    game?.dispose();
    game = null;
    warn.mockRestore();
    dom.restore();
  });

  it('gives scene meshes and spawned prefabs one extra draw, never an entity; a reload leaves no stale draw', async () => {
    game = new Game(dom.canvas as unknown as HTMLCanvasElement, { environment: 0, fillLight: 0, startScene: 'basic', exportBase: '/export/' });
    await game.physics.init();
    game.test.hold();
    await game.loadUrl(XRAY);
    const g = game;
    const coin = g.world.findByName('Coin')!;
    const mesh = firstMesh(coin.object3d);
    expect(passesOf(mesh)).toHaveLength(1);
    expect(g.world.entityOf(passesOf(mesh)[0]!)).toBe(coin);
    const entities = g.world.all().length;
    const spawned = g.spawn('Coin');
    expect(passesOf(firstMesh(spawned.object3d))).toHaveLength(1);
    expect(g.world.all().length).toBe(entities + 1);
    const old = passesOf(mesh)[0]!.material as Material;
    let disposed = false;
    old.addEventListener('dispose', () => void (disposed = true));
    await g.loadUrl(XRAY);
    expect(passesOf(firstMesh(g.world.findByName('Coin')!.object3d))).toHaveLength(1);
    expect(disposed).toBe(true);
  });
});
