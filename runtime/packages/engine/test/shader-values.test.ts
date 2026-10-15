// Live game-shader values: ctx.uniforms (one uniform per field, asset textures), ctx.shaders.set/get
// by material name and per entity (own copies with the same program), string/enum changes, the
// warnings, redefinition and destruction, lightmap variants, and all of it in a running game.
import { createHash } from 'node:crypto';
import { mkdtempSync, readFileSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import {
  BoxGeometry,
  DataTexture,
  Group,
  Mesh,
  MeshStandardMaterial,
  NoColorSpace,
  RepeatWrapping,
  Scene,
  ShaderLib,
  SRGBColorSpace,
  Texture,
  TextureLoader,
  Vector2,
  Vector3,
  Vector4,
  type Material,
  type Object3D,
} from 'three';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import type { SystemContext } from '../src/context';
import type { Entity } from '../src/ecs/entity';
import { defineSystem, removeSystem } from '../src/ecs/system';
import { World } from '../src/ecs/world';
import { Game } from '../src/game/game';
import { RapierPhysics } from '../src/physics/rapier-backend';
import { applyLightmaps } from '../src/scene/lightmaps';
import { MaterialShaders } from '../src/scene/materials';
import { defineShader, field, getShader, patchShader, type ShaderContext } from '../src/schema';
import { installDom, type FakeDom } from './dom';
import { sceneFiles } from './game-fixture';
import { FIXTURE_DIR } from './helpers';

type Compiled = { vertexShader: string; fragmentShader: string; uniforms: Record<string, { value: any }> };

function compiled(material: Material): Compiled {
  const shader: Compiled = { vertexShader: ShaderLib.standard.vertexShader, fragmentShader: ShaderLib.standard.fragmentShader, uniforms: {} };
  material.onBeforeCompile(shader as never, null as never);
  return shader;
}

/** A texture loader whose loads finish when the test says. */
function deferredLoader() {
  const pending = new Map<string, (texture: Texture) => void>();
  const urls: string[] = [];
  return {
    urls,
    load: (url: string) =>
      new Promise<Texture>((resolve, reject) => {
        urls.push(url);
        if (url.includes('missing')) reject(new Error('404'));
        else pending.set(url, resolve);
      }),
    finish(url: string) {
      const texture = new DataTexture(new Uint8Array(4), 1, 1);
      pending.get(url)!(texture);
      return texture;
    },
  };
}

function host(loadTexture?: (url: string) => Promise<Texture>) {
  const warnings: string[] = [];
  const time = { value: 0 };
  const shaders = new MaterialShaders({
    renderer: () => null as never,
    time,
    assetUrl: (p) => `/game/${p}`,
    warn: (m) => void warnings.push(m),
    loadTexture: loadTexture ?? (async (url) => Object.assign(new DataTexture(new Uint8Array(4), 1, 1), { name: url })),
  });
  return { shaders, warnings, time };
}

const tick = () => new Promise((resolve) => setTimeout(resolve, 0));

describe('ctx.uniforms', () => {
  it('has one uniform per field by type, none for strings and enums; asset fields get textures as they load', async () => {
    let ctx: ShaderContext | null = null;
    defineShader('ValAll', {
      fields: {
        f: field.float(0.5),
        i: field.int(3),
        b: field.bool(true),
        v2: field.vec2([1, 2]),
        v3: field.vec3([1, 2, 3]),
        c: field.color([0.1, 0.2, 0.3, 0.4]),
        s: field.string('x'),
        e: field.enum(['a', 'b']),
        tex: field.asset(['.png'], { colorSpace: 'srgb' }),
        data: field.asset(['.png']),
        empty: field.asset(['.png']),
      },
      apply(_material, _values, c) {
        ctx = c;
      },
    });
    const loader = deferredLoader();
    const { shaders, warnings } = host(loader.load);
    const mesh = new Mesh(new BoxGeometry(), new MeshStandardMaterial({ name: 'All' }));
    const scene = new Group().add(mesh);
    shaders.bind([scene], { All: { shader: 'ValAll', values: { tex: 'a.png', data: 'b.png' } } }, () => [scene]);
    const u = ctx!.uniforms as Record<string, { value: any }>;
    expect(Object.keys(u).sort()).toEqual(['b', 'c', 'data', 'empty', 'f', 'i', 'tex', 'v2', 'v3']);
    expect([u.f!.value, u.i!.value, u.b!.value]).toEqual([0.5, 3, true]);
    expect(u.v2!.value).toEqual(new Vector2(1, 2));
    expect(u.v3!.value).toEqual(new Vector3(1, 2, 3));
    expect(u.c!.value).toEqual(new Vector4(0.1, 0.2, 0.3, 0.4));
    expect([u.tex!.value, u.data!.value, u.empty!.value]).toEqual([null, null, null]); // loading, loading, empty
    expect(ctx!.white.image).toMatchObject({ width: 1, height: 1 });
    expect(loader.urls).toEqual(['/game/a.png', '/game/b.png']);
    const tex = loader.finish('/game/a.png');
    loader.finish('/game/b.png');
    await tick();
    expect(u.tex!.value).toBe(tex);
    expect([tex.colorSpace, (u.data!.value as Texture).colorSpace]).toEqual([SRGBColorSpace, NoColorSpace]);
    expect([tex.wrapS, tex.wrapT, tex.flipY, tex.name]).toEqual([RepeatWrapping, RepeatWrapping, false, 'a.png']);
    expect(warnings).toEqual([]);
  });

  it('has the textures at bind time after prepare, loads each file once, and warns about one that does not load', async () => {
    const seen: Array<Record<string, { value: any }>> = [];
    defineShader('ValTex', {
      fields: { tex: field.asset(['.png']) },
      apply(_m, _v, ctx) {
        seen.push(ctx.uniforms as never);
      },
    });
    const loads = vi.fn(async (url: string) => {
      if (url.includes('missing')) throw new Error('404');
      return new DataTexture(new Uint8Array(4), 1, 1);
    });
    const { shaders, warnings } = host(loads);
    const scene = new Group().add(
      new Mesh(new BoxGeometry(), new MeshStandardMaterial({ name: 'A' })),
      new Mesh(new BoxGeometry(), new MeshStandardMaterial({ name: 'B' })),
      new Mesh(new BoxGeometry(), new MeshStandardMaterial({ name: 'C' })),
    );
    const materials = {
      A: { shader: 'ValTex', values: { tex: 'shared.png' } },
      B: { shader: 'ValTex', values: { tex: 'shared.png' } },
      C: { shader: 'ValTex', values: { tex: 'missing.png' } },
    };
    await shaders.prepare(materials);
    shaders.bind([scene], materials, () => [scene]);
    expect(loads).toHaveBeenCalledTimes(2);
    expect(seen[0]!.tex!.value).toBeInstanceOf(Texture);
    expect(seen[1]!.tex!.value).toBe(seen[0]!.tex!.value);
    expect(seen[2]!.tex!.value).toBeNull();
    expect(warnings).toEqual(['Game shader "ValTex" (material "C"): texture missing.png did not load (404)']);
  });
});

/** A world: A and B draw one material, A's child entity C too; D draws its own lightmapped copy. */
function world() {
  let applies = 0;
  defineShader('ValWet', {
    fields: { wet: field.float(0.2), tile: field.float(1), mode: field.enum(['plain', 'gloss']), hitAt: field.float(-1000, { unit: 'time' }) },
    apply(material, values, ctx) {
      applies++;
      patchShader(material, `valwet-${values.mode}`, (shader) => {
        Object.assign(shader.uniforms, { uTime: ctx.time, uWet: ctx.uniforms.wet, uTile: ctx.uniforms.tile, uHitAt: ctx.uniforms.hitAt });
        shader.fragmentShader += `\n// wet ${values.mode}`;
      });
    },
  });
  const scene = new Scene();
  const w = new World(scene);
  const sand = new MeshStandardMaterial({ name: 'GS_Sand' });
  const geometry = new BoxGeometry();
  geometry.setAttribute('uv1', geometry.getAttribute('uv').clone());
  const meshA = new Mesh(geometry, sand);
  const meshB = new Mesh(geometry, sand);
  const meshC = new Mesh(geometry, sand);
  const meshD = new Mesh(geometry, sand);
  meshD.userData.gs_id = 'gs_d';
  const a = w.spawn({ name: 'A', object3d: new Group().add(meshA) });
  const b = w.spawn({ name: 'B', object3d: new Group().add(meshB) });
  const c = w.spawn({ name: 'C', object3d: new Group().add(meshC), parent: a });
  const d = w.spawn({ name: 'D', object3d: new Group().add(meshD) });
  const lightmaps = { version: 1, mode: 'indirect', atlases: [{ file: 'a.png', size: 64, scale: 1 }], objects: { gs_d: { atlas: 0, uv: 1 } }, bakedLights: [] };
  applyLightmaps(scene, { lightmaps }, [new Texture()], () => {});
  const { shaders, warnings, time } = host();
  const ownMeshes = (entity: Entity): Mesh[] => {
    const out: Mesh[] = [];
    entity.object3d.traverse((o) => {
      if ((o as Mesh).isMesh && w.entityOf(o) === entity) out.push(o as Mesh);
    });
    return out;
  };
  (shaders as unknown as { host: { ownMeshes: typeof ownMeshes } }).host.ownMeshes = ownMeshes;
  shaders.bind([scene], { GS_Sand: { shader: 'ValWet', values: { wet: 0.4 } } }, () => [scene]);
  return { w, shaders, warnings, time, a, b, c, d, meshA, meshB, meshC, meshD, sand, applies: () => applies };
}

const drawn = (mesh: Mesh): Material => mesh.material as Material;
const uniform = (mesh: Mesh, name: string): any => compiled(drawn(mesh)).uniforms[name]!.value;

describe('ctx.shaders', () => {
  it('sets a material everywhere in place: every instance (lightmap variants too), no new apply', () => {
    const l = world();
    expect(l.applies()).toBe(2); // the plain material and D's lightmapped copy
    const shared = drawn(l.meshA);
    const wet = compiled(shared).uniforms.uWet!;
    l.shaders.set('GS_Sand', { wet: 0.9, tile: 3 });
    expect(l.applies()).toBe(2);
    expect(drawn(l.meshA)).toBe(shared);
    expect(wet.value).toBe(0.9); // the same uniform object
    expect(uniform(l.meshD, 'uWet')).toBe(0.9);
    expect(uniform(l.meshD, 'uTile')).toBe(3);
    expect(drawn(l.meshD).customProgramCacheKey()).toMatch(/\|gs_lightmap\|valwet-plain$/);
    expect(l.shaders.get('GS_Sand')).toEqual({ wet: 0.9, tile: 3, mode: 'plain', hitAt: -1000 });
    expect(l.warnings).toEqual([]);
  });

  it("gives an entity its own copy on the first set (same program), leaving other entities and its child entities alone", () => {
    const l = world();
    const shared = drawn(l.meshA);
    l.shaders.set(l.a, { hitAt: 2.5 });
    const own = drawn(l.meshA);
    expect(own).not.toBe(shared);
    expect(own).not.toBe(l.sand);
    expect(l.applies()).toBe(3); // one fresh apply for A
    expect(drawn(l.meshB)).toBe(shared);
    expect(drawn(l.meshC)).toBe(shared); // a child entity is not part of A
    expect(own.customProgramCacheKey()).toBe(shared.customProgramCacheKey());
    expect(compiled(own).fragmentShader).toBe(compiled(shared).fragmentShader);
    expect(uniform(l.meshA, 'uHitAt')).toBe(2.5);
    expect(uniform(l.meshB, 'uHitAt')).toBe(-1000);
    expect(compiled(own).uniforms.uTime).toBe(l.time); // the shared clock

    // Later sets change A's uniforms in place.
    const hit = compiled(own).uniforms.uHitAt!;
    l.shaders.set(l.a, { hitAt: 4 });
    expect(l.applies()).toBe(3);
    expect(hit.value).toBe(4);
    expect(l.shaders.get(l.a)).toEqual({ wet: 0.4, tile: 1, mode: 'plain', hitAt: 4 });
    expect(l.shaders.get(l.b)).toEqual({ wet: 0.4, tile: 1, mode: 'plain', hitAt: -1000 });

    // A lightmapped entity's copy starts from its lightmapped material.
    l.shaders.set(l.d, { wet: 1 });
    expect(drawn(l.meshD).customProgramCacheKey()).toMatch(/\|gs_lightmap\|valwet-plain$/);
    expect(compiled(drawn(l.meshD)).fragmentShader).toContain('irradiance = lightMapIrradiance;');
  });

  it('reaches entity copies with a material set for the fields they did not set themselves', () => {
    const l = world();
    l.shaders.set(l.a, { wet: 1 });
    l.shaders.set('GS_Sand', { wet: 0.1, tile: 5 });
    expect([uniform(l.meshA, 'uWet'), uniform(l.meshA, 'uTile')]).toEqual([1, 5]);
    expect([uniform(l.meshB, 'uWet'), uniform(l.meshB, 'uTile')]).toEqual([0.1, 5]);
  });

  it('applies the shader again for a string or enum change (a new program), and takes three.js vectors', () => {
    const l = world();
    const before = drawn(l.meshB);
    l.shaders.set('GS_Sand', { mode: 'gloss' });
    expect(l.applies()).toBe(4); // both instances again
    expect(drawn(l.meshB)).not.toBe(before);
    expect(drawn(l.meshB).customProgramCacheKey()).toMatch(/\|valwet-gloss$/);
    expect(compiled(drawn(l.meshB)).fragmentShader).toContain('// wet gloss');
    l.shaders.set(l.a, { mode: 'plain' });
    expect(drawn(l.meshA).customProgramCacheKey()).toMatch(/\|valwet-plain$/);
    expect(drawn(l.meshB).customProgramCacheKey()).toMatch(/\|valwet-gloss$/);
  });

  it('warns once about unknown fields, values that do not fit, entities without shaded materials and unknown materials', () => {
    const l = world();
    const bare = l.w.spawn({ name: 'Bare', object3d: new Group().add(new Mesh(new BoxGeometry(), new MeshStandardMaterial({ name: 'Plain' }))) });
    for (let i = 0; i < 2; i++) {
      l.shaders.set('GS_Sand', { wett: 1, wet: 'high', tile: 2 });
      l.shaders.set(bare, { wet: 1 });
      l.shaders.set('GS_Nope', { wet: 1 });
    }
    expect(l.warnings).toEqual([
      'ctx.shaders.set(material "GS_Sand"): game shader "ValWet" has no field "wett"',
      'ctx.shaders.set(material "GS_Sand"): "high" does not fit float field "wet"',
      'ctx.shaders.set: entity "Bare" draws no material with a game shader',
      'ctx.shaders.set: no material "GS_Nope" has a game shader in this scene',
    ]);
    expect(uniform(l.meshB, 'uTile')).toBe(2); // the fitting value still applies
    expect(l.shaders.get(bare)).toBeNull();
    expect(l.shaders.get('GS_Nope')).toBeNull();
  });

  it("applies a redefinition to entity copies too, keeping their values, and drops a destroyed entity's copies", () => {
    const l = world();
    l.shaders.set(l.a, { wet: 0.7 });
    const old = drawn(l.meshA);
    defineShader('ValWet', {
      fields: { wet: field.float(0.2) },
      apply(material, _values, ctx) {
        patchShader(material, 'valwet2', (shader) => void (shader.uniforms.uWet = ctx.uniforms.wet));
      },
    });
    l.shaders.redefine(getShader('ValWet')!); // as the game does on onShaderDefined
    expect(drawn(l.meshA)).not.toBe(old);
    expect(drawn(l.meshA).customProgramCacheKey()).toMatch(/\|valwet2$/);
    expect(uniform(l.meshA, 'uWet')).toBe(0.7);
    expect(uniform(l.meshB, 'uWet')).toBe(0.4);
    const disposed = vi.fn();
    drawn(l.meshA).addEventListener('dispose', disposed);
    l.shaders.forget(l.a);
    expect(disposed).toHaveBeenCalledTimes(1);
  });
});

// ---------------------------------------------------------------------------
// In a game: a lazily loaded shader, asset textures, ctx.shaders from a system, spawned prefabs.

const BASIC_HASH = createHash('sha1').update(readFileSync(`${FIXTURE_DIR}basic.glb`)).digest('hex').slice(0, 16);
const dir = mkdtempSync(path.join(tmpdir(), 'gs-shader-values-'));
writeFileSync(path.join(dir, 'live.prefabs.glb'), readFileSync(`${FIXTURE_DIR}basic.glb`));
const LIVE = '/export/live.scene.json';
{
  const m = JSON.parse(readFileSync(`${FIXTURE_DIR}basic.scene.json`, 'utf8'));
  writeFileSync(
    path.join(dir, 'live.scene.json'),
    JSON.stringify({
      ...m,
      formatVersion: 2,
      scene: 'live',
      files: { 'basic.glb': BASIC_HASH, 'live.prefabs.glb': BASIC_HASH },
      prefabs: [
        { name: 'Coin', glb: 'live.prefabs.glb', offset: [0, 0, 0], entities: [{ id: 'gs_000000a00006', name: 'Coin', type: 'MESH', parent: null, components: {} }], animations: [] },
      ],
      assets: { 'textures/glow.png': 'assets/glow-1234.png' },
      materials: {
        CoinMat: { shader: 'LiveGlow', values: { glow: 0.5, tex: 'textures/glow.png' } },
        CrateMat: { shader: 'LiveLazy', values: { amount: 2 } },
        LampMat: { shader: 'LiveLazy', values: {} },
        RampMat: { shader: 'LiveBroken', values: {} },
      },
    }),
  );
}

describe('live values in a game', () => {
  let dom: FakeDom;
  let warn: { mock: { calls: unknown[][] }; mockRestore(): void };
  let game: Game | null = null;
  let loads: string[];
  beforeAll(async () => {
    await new RapierPhysics().init();
  });
  beforeEach(() => {
    dom = installDom({ ...sceneFiles(), [LIVE]: path.join(dir, 'live.scene.json'), '/export/live.prefabs.glb': path.join(dir, 'live.prefabs.glb') });
    warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
    loads = [];
    vi.spyOn(TextureLoader.prototype, 'loadAsync').mockImplementation((async (url: string) => {
      loads.push(url);
      return new DataTexture(new Uint8Array(4), 1, 1);
    }) as never);
    defineShader('LiveGlow', {
      fields: { glow: field.float(0), tex: field.asset(['.png'], { colorSpace: 'srgb' }), hitAt: field.float(-1000, { unit: 'time' }) },
      apply(material, _values, ctx) {
        patchShader(material, 'liveglow', (shader) => Object.assign(shader.uniforms, { uGlow: ctx.uniforms.glow, uTex: ctx.uniforms.tex, uHitAt: ctx.uniforms.hitAt }));
      },
    });
    let release!: () => void;
    const gate = new Promise<void>((resolve) => (release = resolve));
    setTimeout(() => release(), 20); // the module "downloads" after the GLBs parse
    defineShader('LiveLazy', {
      fields: { amount: field.float(1) },
      load: async () => {
        await gate;
        return {
          apply(material, _values, ctx) {
            patchShader(material, 'livelazy', (shader) => void (shader.uniforms.uAmount = ctx.uniforms.amount));
          },
        };
      },
    });
    defineShader('LiveBroken', { load: () => Promise.reject(new Error('chunk failed')) });
  });
  afterEach(() => {
    game?.dispose();
    game = null;
    removeSystem('LiveProbe');
    vi.restoreAllMocks();
    warn.mockRestore();
    dom.restore();
  });

  async function start(): Promise<{ g: Game; ctx: () => SystemContext }> {
    let captured: SystemContext | null = null;
    defineSystem({ name: 'LiveProbe', start: (ctx) => void (captured = ctx) });
    game = new Game(dom.canvas as unknown as HTMLCanvasElement, { environment: 0, fillLight: 0, startScene: 'basic', exportBase: '/export/' });
    await game.physics.init();
    game.test.hold();
    await game.loadUrl(LIVE);
    await game.test.begin();
    await game.test.advance(1); // systems start
    return { g: game, ctx: () => captured! };
  }
  const meshOf = (g: Game, name: string): Mesh => firstMesh(g.world.findByName(name)!.object3d);
  const warnings = (): string[] => warn.mock.calls.map((c) => String(c[0]));

  it('waits for lazy shaders and asset textures before the scene starts; a module that fails keeps the Blender look', async () => {
    const { g } = await start();
    expect(compiled(meshOf(g, 'Crate').material as Material).uniforms.uAmount!.value).toBe(2);
    expect(compiled(meshOf(g, 'CrateLamp').material as Material).uniforms.uAmount!.value).toBe(1);
    expect(loads).toEqual(['http://game.test/export/assets/glow-1234.png']); // the exported copy of the asset
    expect(compiled(meshOf(g, 'Coin').material as Material).uniforms.uTex!.value).toBeInstanceOf(Texture);
    expect((meshOf(g, 'Ramp').material as Material).customProgramCacheKey()).not.toContain('|');
    expect(warnings()).toContain('[gamestudio] Game shader "LiveBroken" failed on material "RampMat": chunk failed');
  });

  it('gives systems ctx.shaders: hit one spawned prefab, the others keep the template material; destroying it drops its copy', async () => {
    const { g, ctx } = await start();
    const first = g.spawn('Coin');
    const second = g.spawn('Coin');
    const template = firstMesh(second.object3d).material as Material;
    ctx().shaders.set(first, { hitAt: 1 }); // game code: { hitAt: ctx.time }
    const own = firstMesh(first.object3d).material as Material;
    expect(own).not.toBe(template);
    expect(compiled(own).uniforms.uHitAt!.value).toBe(1);
    expect(compiled(template).uniforms.uHitAt!.value).toBe(-1000);
    expect(firstMesh(second.object3d).material).toBe(template);
    expect(ctx().shaders.get(first)).toMatchObject({ hitAt: 1, glow: 0.5 });
    ctx().shaders.set('CoinMat', { glow: 2 });
    expect(compiled(own).uniforms.uGlow!.value).toBe(2);
    expect(compiled(template).uniforms.uGlow!.value).toBe(2);
    const disposed = vi.fn();
    own.addEventListener('dispose', disposed);
    ctx().destroy(first);
    expect(disposed).toHaveBeenCalledTimes(1);
  });
});

function firstMesh(object: Object3D): Mesh {
  let mesh: Mesh | null = null;
  object.traverse((o) => {
    if (!mesh && (o as Mesh).isMesh) mesh = o as Mesh;
  });
  return mesh!;
}
