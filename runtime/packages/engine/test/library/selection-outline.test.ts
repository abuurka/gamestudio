// Selection Outline: the two extra draws `mesh` adds (an inverted hull of constant screen width and a
// dashed pass behind walls; children that follow the mesh's skeleton and GPU instances, never cast
// shadows, never take a ray, are not entities), the hull's smoothed normals and its shared buffers,
// live values, and that applying again or cloning (prefab instances) keeps exactly two.
import { createHash } from 'node:crypto';
import { mkdtempSync, readFileSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import {
  BackSide,
  BoxGeometry,
  CustomBlending,
  GreaterDepth,
  Group,
  Mesh,
  Raycaster,
  Vector3,
  type BufferGeometry,
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

type Kind = 'outline' | 'hidden';
const passesOf = (mesh: Mesh): Mesh[] => mesh.children.filter((c) => c.userData.gsShaderPass?.shader === 'SelectionOutline') as Mesh[];
const passOf = (mesh: Mesh, kind: Kind): Mesh => passesOf(mesh).find((p) => p.userData.gsShaderPass.kind === kind)!;
const renderer = (ratio = 1) =>
  ({ getPixelRatio: () => ratio, getCurrentViewport: (v: { set: (...n: number[]) => unknown }) => v.set(0, 0, 800, 600) }) as never;
const before = (pass: Mesh, ratio = 1): void =>
  pass.onBeforeRender(renderer(ratio), null as never, null as never, pass.geometry, pass.material as Material, null as never);

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

describe('SelectionOutline', () => {
  it('is a built-in Gameplay shader', () => {
    expect(getShader('SelectionOutline')).toMatchObject({ builtin: true, category: 'Gameplay' });
  });

  it('adds an outline hull and a pass behind walls; the material keeps its look', async () => {
    const c = await compilePatched('SelectionOutline');
    expect(c.warnings).toEqual([]);
    expect(c.material.customProgramCacheKey()).toBe(c.original.customProgramCacheKey()); // not patched
    expect(passesOf(c.mesh)).toHaveLength(2);
    const hull = passOf(c.mesh, 'outline');
    const hidden = passOf(c.mesh, 'hidden');
    expect((hull.material as ShaderMaterial).side).toBe(BackSide);
    expect((hull.material as ShaderMaterial).transparent).toBe(true); // after every opaque object
    // The pass behind walls: after every other opaque object, right before the mesh itself, so the
    // mesh's own parts never count as walls.
    const hiddenMaterial = hidden.material as ShaderMaterial;
    expect(hiddenMaterial.depthFunc).toBe(GreaterDepth);
    expect(hiddenMaterial.transparent).toBe(false);
    expect(hiddenMaterial.blending).toBe(CustomBlending);
    expect(hidden.renderOrder).toBeGreaterThan(0);
    expect(c.mesh.renderOrder).toBeGreaterThan(hidden.renderOrder);
    expect(hidden.geometry).toBe(hull.geometry); // the same pushed-out hull, its front faces
    for (const pass of [hull, hidden]) {
      expect(pass).toBeInstanceOf(Mesh);
      expect(pass.castShadow).toBe(false);
      expect(pass.userData.gs_id).toBeUndefined(); // never an entity
      const material = pass.material as ShaderMaterial;
      expect(() => resolveIncludes(material.vertexShader)).not.toThrow();
      expect(() => resolveIncludes(material.fragmentShader)).not.toThrow();
      expect(missingUniforms(material)).toEqual([]);
    }
    c.mesh.updateMatrixWorld(true);
    const hits = new Raycaster(new Vector3(0, 0, 5), new Vector3(0, 0, -1)).intersectObject(c.mesh, true);
    expect(hits.length).toBeGreaterThan(0);
    expect(hits.every((h) => h.object === c.mesh)).toBe(true);
  });

  it('pushes the hull out along smoothed normals, sharing the mesh buffers', async () => {
    const c = await compilePatched('SelectionOutline', { mesh: 'box' });
    const hull = passOf(c.mesh, 'outline');
    const geometry = hull.geometry as BufferGeometry;
    const source = c.mesh.geometry;
    expect(geometry).not.toBe(source);
    expect(geometry.getAttribute('position')).toBe(source.getAttribute('position'));
    expect(geometry.getIndex()).toBe(source.getIndex());
    // A cube's corner: the three split vertices there share one diagonal outline normal.
    const position = source.getAttribute('position');
    const normal = geometry.getAttribute('outlineNormal');
    const corner = new Vector3(0.5, 0.5, 0.5);
    const at: Vector3[] = [];
    for (let i = 0; i < position.count; i++) {
      if (new Vector3().fromBufferAttribute(position, i).distanceTo(corner) < 1e-6) at.push(new Vector3().fromBufferAttribute(normal, i));
    }
    expect(at.length).toBe(3);
    for (const n of at) expect(n.distanceTo(corner.clone().normalize())).toBeLessThan(1e-5);
    expect(resolveIncludes((hull.material as ShaderMaterial).vertexShader)).toContain('vec3 objectNormal = outlineNormal;');
    // One hull per source geometry; disposing it frees only its own normals.
    const again = await compilePatched('SelectionOutline', { mesh: 'box' });
    expect(passOf(again.mesh, 'outline').geometry).not.toBe(geometry); // a different source geometry
    geometry.dispose();
    expect(source.getAttribute('position')).toBeTruthy();
    expect(geometry.getAttribute('position')).toBeUndefined();
  });

  it('keeps hard-edged meshes whole: no normals at all still gives an outline', async () => {
    const box = new BoxGeometry(1, 1, 1).toNonIndexed();
    box.deleteAttribute('normal');
    const c = await compilePatched('SelectionOutline');
    c.mesh.geometry = box;
    const { mesh } = (await getShader('SelectionOutline')!.load!()) as { mesh: (m: Mesh, v: never, ctx: never) => void };
    mesh(c.mesh, {} as never, { material: PREVIEW_MATERIAL } as never);
    const normal = (passOf(c.mesh, 'outline').geometry as BufferGeometry).getAttribute('outlineNormal');
    expect(normal.count).toBe(box.getAttribute('position').count);
    expect(Number.isFinite(normal.getX(0))).toBe(true);
    expect(passesOf(c.mesh)).toHaveLength(2);
  });

  it('copies the live values before each draw: width in device pixels, viewport, colour, time', async () => {
    const c = await compilePatched('SelectionOutline', { time: 7 });
    const hull = passOf(c.mesh, 'outline');
    const hidden = passOf(c.mesh, 'hidden');
    c.shaders.set(PREVIEW_MATERIAL, { color: [0, 1, 0, 1], width: 4, hidden: false });
    before(hull, 2);
    before(hidden, 2);
    const u = (hull.material as ShaderMaterial).uniforms;
    expect(u.olColor!.value.toArray()).toEqual([0, 1, 0, 1]);
    expect(u.olWidth!.value).toBe(8);
    expect(u.olViewport!.value.toArray()).toEqual([800, 600]);
    expect(u.olTime!.value).toBe(7);
    expect((hidden.material as ShaderMaterial).uniforms.olHidden!.value).toBe(0);
    // Strength 0 hides both draws, Hidden off the dashed one (from the next frame).
    c.material.onBeforeRender(null as never, null as never, null as never, c.mesh.geometry, c.mesh, null as never);
    expect([hull.visible, hidden.visible]).toEqual([true, false]);
    c.shaders.set(PREVIEW_MATERIAL, { strength: 0, hidden: true });
    c.material.onBeforeRender(null as never, null as never, null as never, c.mesh.geometry, c.mesh, null as never);
    expect([hull.visible, hidden.visible]).toEqual([false, false]);
  });

  it('switches on per entity (hover, selection) with its own draws', async () => {
    const c = await compilePatched('SelectionOutline', { values: { strength: 0 } });
    c.shaders.set(c.entity, { strength: 1 });
    expect(c.mesh.material).not.toBe(c.material);
    expect(passesOf(c.mesh)).toHaveLength(2);
    const hull = passOf(c.mesh, 'outline');
    before(hull);
    expect((hull.material as ShaderMaterial).uniforms.olStrength!.value).toBe(1);
  });

  it('follows skinned meshes and GPU instances, also in copies', async () => {
    const skinned = await compilePatched('SelectionOutline', { skinned: true, mesh: 'character' });
    const mesh = skinned.mesh as SkinnedMesh;
    for (const pass of passesOf(mesh) as SkinnedMesh[]) {
      expect(pass.isSkinnedMesh).toBe(true);
      expect(pass.skeleton).toBe(mesh.skeleton);
    }
    expect((passOf(mesh, 'outline').geometry as BufferGeometry).getAttribute('skinWeight')).toBe(mesh.geometry.getAttribute('skinWeight'));
    const copy = cloneScene(skinned.entity.object3d as Group);
    let copied: SkinnedMesh | null = null;
    copy.traverse((o) => {
      if ((o as SkinnedMesh).isSkinnedMesh && !o.userData.gsShaderPass) copied = o as SkinnedMesh;
    });
    const copyPasses = passesOf(copied!) as SkinnedMesh[];
    expect(copyPasses).toHaveLength(2);
    for (const pass of copyPasses) expect(pass.skeleton.bones).toEqual(copied!.skeleton.bones);

    const instanced = await compilePatched('SelectionOutline', { instanced: true });
    const host = instanced.mesh as InstancedMesh;
    for (const pass of passesOf(host) as InstancedMesh[]) {
      expect(pass.isInstancedMesh).toBe(true);
      expect(pass.instanceMatrix).toBe(host.instanceMatrix);
    }
  });

  it('applying again replaces the extra draws instead of adding more', async () => {
    const c = await compilePatched('SelectionOutline');
    c.shaders.set(c.entity, { color: [1, 1, 1, 1] });
    c.shaders.set(c.entity, { width: 5 });
    expect(passesOf(c.mesh)).toHaveLength(2);
  });
});

// ---------------------------------------------------------------------------
// In a game: the basic fixture, with the Coin also a prefab (a copy of basic.glb), outlined.

const BASIC_HASH = createHash('sha1').update(readFileSync(`${FIXTURE_DIR}basic.glb`)).digest('hex').slice(0, 16);
const dir = mkdtempSync(path.join(tmpdir(), 'gs-outline-'));
writeFileSync(path.join(dir, 'outlined.prefabs.glb'), readFileSync(`${FIXTURE_DIR}basic.glb`));
const OUTLINED = '/export/outlined.scene.json';
{
  const m = JSON.parse(readFileSync(`${FIXTURE_DIR}basic.scene.json`, 'utf8'));
  const manifest = {
    ...m,
    formatVersion: 2,
    scene: 'outlined',
    files: { 'basic.glb': BASIC_HASH, 'outlined.prefabs.glb': BASIC_HASH },
    prefabs: [
      { name: 'Coin', glb: 'outlined.prefabs.glb', offset: [0, 0, 0], entities: [{ id: 'gs_000000a00006', name: 'Coin', type: 'MESH', parent: null, components: {} }], animations: [] },
    ],
    materials: { CoinMat: { shader: 'SelectionOutline', values: { width: 2 } } },
  };
  writeFileSync(path.join(dir, 'outlined.scene.json'), JSON.stringify(manifest));
}

function firstMesh(object: Object3D): Mesh {
  let mesh: Mesh | null = null;
  object.traverse((o) => {
    if (!mesh && (o as Mesh).isMesh && !o.userData.gsShaderPass) mesh = o as Mesh;
  });
  return mesh!;
}

describe('SelectionOutline in a game', () => {
  let dom: FakeDom;
  let game: Game | null = null;
  let warn: { mockRestore(): void };
  beforeAll(async () => {
    await new RapierPhysics().init();
  });
  beforeEach(() => {
    dom = installDom({ ...sceneFiles(), [OUTLINED]: path.join(dir, 'outlined.scene.json'), '/export/outlined.prefabs.glb': path.join(dir, 'outlined.prefabs.glb') });
    warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
  });
  afterEach(() => {
    game?.dispose();
    game = null;
    warn.mockRestore();
    dom.restore();
  });

  async function start(): Promise<Game> {
    game = new Game(dom.canvas as unknown as HTMLCanvasElement, { environment: 0, fillLight: 0, startScene: 'basic', exportBase: '/export/' });
    await game.physics.init();
    game.test.hold();
    await game.loadUrl(OUTLINED);
    return game;
  }

  it('outlines scene meshes and spawned prefabs once, never as entities; a reload leaves no stale draws', async () => {
    const g = await start();
    const coin = g.world.findByName('Coin')!;
    const mesh = firstMesh(coin.object3d);
    expect(passesOf(mesh)).toHaveLength(2);
    for (const pass of passesOf(mesh)) expect(g.world.entityOf(pass)).toBe(coin); // part of the Coin, not an entity
    const entities = g.world.all().length;

    // A prefab instance copies the template's draws (and their behaviour).
    const spawned = g.spawn('Coin');
    const spawnedMesh = firstMesh(spawned.object3d);
    const spawnedPasses = passesOf(spawnedMesh);
    expect(spawnedPasses).toHaveLength(2);
    expect(spawnedPasses[0]!.raycast).toBe(passesOf(mesh)[0]!.raycast);
    expect(g.world.all().length).toBe(entities + 1); // the Coin, not its draws

    // Hover off on the spawned one only: its own material, its draws replaced, the scene's Coin unchanged.
    (g as unknown as { ctx: { shaders: { set(e: unknown, v: object): void } } }).ctx.shaders.set(spawned, { strength: 0 });
    expect(spawnedMesh.material).not.toBe(mesh.material);
    expect(passesOf(spawnedMesh)).toHaveLength(2);
    expect(passesOf(spawnedMesh)[0]).not.toBe(spawnedPasses[0]);

    // A reload: fresh draws on the fresh scene, the old ones' materials freed.
    const old = passesOf(mesh)[0]!.material as Material;
    let disposed = false;
    old.addEventListener('dispose', () => void (disposed = true));
    await g.loadUrl(OUTLINED);
    const again = firstMesh(g.world.findByName('Coin')!.object3d);
    expect(again).not.toBe(mesh);
    expect(passesOf(again)).toHaveLength(2);
    expect(disposed).toBe(true);
  });
});
