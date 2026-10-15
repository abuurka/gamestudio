// Baked lighting: the manifest's `lightmaps`, loading the atlases, putting them on static meshes
// (with the shader patch that keeps light from being counted twice) and turning baked lamps off.
import { readFileSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import {
  BoxGeometry,
  BufferAttribute,
  DirectionalLight,
  Group,
  LinearFilter,
  Mesh,
  MeshBasicMaterial,
  MeshLambertMaterial,
  MeshPhysicalMaterial,
  MeshStandardMaterial,
  Object3D,
  PointLight,
  ShaderChunk,
  ShaderLib,
  SRGBColorSpace,
  Texture,
  type Material,
} from 'three';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { RapierPhysics } from '../src/physics/rapier-backend';
import { applyLightmaps, lightmapShaderChunk, loadLightmapTextures } from '../src/scene/lightmaps';
import { processManifest, type ManifestLightmaps } from '../src/scene/manifest';
import type { FakeDom } from './dom';
import { heldGame, installSceneDom, sceneFiles } from './game-fixture';

const base = { format: 'gamestudio.scene', formatVersion: 2, scene: 'x', glb: 'x.glb', entities: [] };
const lightmapsOf = (lightmaps: unknown) => processManifest({ ...base, lightmaps });

describe('manifest lightmaps', () => {
  it('reads atlases, objects and baked lamps', () => {
    const { manifest, warnings } = lightmapsOf({
      version: 1,
      mode: 'indirect',
      atlases: [{ file: 'lightmaps/main_ab12_0.png?v=1f2e', size: 1024, scale: 1.37 }],
      objects: { gs_000000000001: { atlas: 0, uv: 1 }, gs_000000000002: { atlas: 0, uv: 0 } },
      bakedLights: ['gs_000000000009'],
    });
    expect(warnings).toEqual([]);
    expect(manifest.lightmaps).toEqual({
      version: 1,
      mode: 'indirect',
      atlases: [{ file: 'lightmaps/main_ab12_0.png?v=1f2e', size: 1024, scale: 1.37 }],
      objects: { gs_000000000001: { atlas: 0, uv: 1 }, gs_000000000002: { atlas: 0, uv: 0 } },
      bakedLights: ['gs_000000000009'],
    });
  });

  it('drops bad entries with a warning and keeps atlas indexes pointing at the right atlas', () => {
    const { manifest, warnings } = lightmapsOf({
      atlases: [
        { file: 'a0.png', size: 512, scale: 2 },
        { file: '', size: 512, scale: 1 },
        { file: 'a2.png', size: 256, scale: 0.5 },
        { file: 'a3.png', size: 256, scale: Infinity },
        null,
      ],
      objects: {
        gs_a: { atlas: 0, uv: 1 },
        gs_b: { atlas: 2, uv: 0 },
        gs_c: { atlas: 1, uv: 1 }, // a dropped atlas
        gs_d: { atlas: 0, uv: 4 },
        gs_e: { atlas: 9, uv: 1 },
        gs_f: null,
        gs_g: { atlas: 0, uv: 1.5 },
      },
      bakedLights: ['gs_lamp', 3, ''],
    });
    expect(manifest.lightmaps).toEqual({
      version: 1,
      mode: 'indirect',
      atlases: [
        { file: 'a0.png', size: 512, scale: 2 },
        { file: 'a2.png', size: 256, scale: 0.5 },
      ],
      objects: { gs_a: { atlas: 0, uv: 1 }, gs_b: { atlas: 1, uv: 0 } },
      bakedLights: ['gs_lamp'],
    });
    expect(warnings.filter((w) => w.startsWith('Skipping lightmap atlas'))).toHaveLength(3);
    expect(warnings).toContain('Skipping the lightmaps of 5 object(s) with a missing atlas or a bad uv (0..3): gs_c, gs_d, gs_e, gs_f, gs_g');
    expect(warnings.filter((w) => w.includes('bakedLights'))).toHaveLength(2);
  });

  it('is null when absent (older exports) or not an object', () => {
    expect(processManifest(base)).toEqual(expect.objectContaining({ warnings: [] }));
    expect(processManifest(base).manifest.lightmaps).toBeNull();
    expect(lightmapsOf(null).manifest.lightmaps).toBeNull();
    const wrong = lightmapsOf('yes');
    expect(wrong.manifest.lightmaps).toBeNull();
    expect(wrong.warnings).toEqual(['Ignoring "lightmaps": not an object']);
    expect(lightmapsOf({}).manifest.lightmaps).toEqual({ version: 1, mode: 'indirect', atlases: [], objects: {}, bakedLights: [] });
  });
});

describe('loadLightmapTextures', () => {
  const lightmaps = lightmapsOf({
    atlases: [
      { file: 'lightmaps/main_0.png?v=abc', size: 64, scale: 1.5 },
      { file: 'lightmaps/gone.png', size: 64, scale: 1 },
    ],
  }).manifest.lightmaps!;

  it('loads atlases next to the manifest with lightmap texture settings; a failed one is null with a warning', async () => {
    const urls: string[] = [];
    const warnings: string[] = [];
    const textures = await loadLightmapTextures(
      lightmaps,
      'http://game.test/__gs/export/main.scene.json',
      async (url) => {
        urls.push(url);
        if (url.includes('gone')) throw new Error('HTTP 404');
        return new Texture();
      },
      (m) => warnings.push(m),
    );
    expect(urls).toEqual(['http://game.test/__gs/export/lightmaps/main_0.png?v=abc', 'http://game.test/__gs/export/lightmaps/gone.png']);
    const texture = textures[0]!;
    expect(texture).toMatchObject({ name: 'gs_lightmap_0', colorSpace: SRGBColorSpace, flipY: false, minFilter: LinearFilter, magFilter: LinearFilter, generateMipmaps: false });
    expect(textures[1]).toBeNull();
    expect(warnings).toEqual(['Lightmap lightmaps/gone.png did not load (HTTP 404); its meshes are lit like moving objects']);
  });

  it('loads nothing without lightmaps', async () => {
    const load = vi.fn(async () => new Texture());
    expect(await loadLightmapTextures(null, 'http://game.test/x.scene.json', load)).toEqual([]);
    expect(await loadLightmapTextures(undefined, 'http://game.test/x.scene.json', load)).toEqual([]);
    expect(load).not.toHaveBeenCalled();
  });
});

/** A mesh that is an entity (`gs_id`), with lightmap UVs in `uvs` ('uv', 'uv1', ...). */
function entityMesh(id: string | null, material: Material | Material[], uvs: string[] = ['uv', 'uv1']): Mesh {
  const geometry = new BoxGeometry();
  geometry.deleteAttribute('uv');
  for (const name of uvs) geometry.setAttribute(name, new BufferAttribute(new Float32Array(geometry.attributes.position!.count * 2), 2));
  const mesh = new Mesh(geometry, material);
  if (id) mesh.userData.gs_id = id;
  mesh.name = id ?? 'part';
  return mesh;
}

/** Inlines `#include <chunk>` like WebGLProgram does. */
const resolveIncludes = (source: string): string =>
  source.replace(/^[ \t]*#include +<([\w./]+)>/gm, (_m, name: string) => resolveIncludes((ShaderChunk as unknown as Record<string, string>)[name]!));

/** The fragment shader WebGL would compile for `material` (after its onBeforeCompile). */
function fragmentOf(material: Material, lib: 'physical' | 'lambert' | 'phong' = 'physical'): string {
  const shader = { fragmentShader: ShaderLib[lib].fragmentShader, vertexShader: ShaderLib[lib].vertexShader, uniforms: {} };
  material.onBeforeCompile(shader as never, null as never);
  return resolveIncludes(shader.fragmentShader);
}

const mainOf = (source: string): string => source.slice(source.indexOf('void main()'));

describe('applyLightmaps', () => {
  const lightmaps: ManifestLightmaps = {
    version: 1,
    mode: 'indirect',
    atlases: [
      { file: 'a0.png', size: 64, scale: 1.5 },
      { file: 'a1.png', size: 64, scale: 2 },
      { file: 'gone.png', size: 64, scale: 1 },
    ],
    objects: {
      gs_wall: { atlas: 0, uv: 1 },
      gs_floor: { atlas: 0, uv: 1 },
      gs_roof: { atlas: 1, uv: 1 },
      gs_step: { atlas: 0, uv: 0 },
      gs_nouv: { atlas: 0, uv: 2 },
      gs_sign: { atlas: 0, uv: 1 },
      gs_lost: { atlas: 2, uv: 1 },
      gs_absent: { atlas: 0, uv: 1 },
    },
    bakedLights: ['gs_lamp', 'gs_area', 'gs_absent_lamp'],
  };

  function scene() {
    const shared = new MeshStandardMaterial({ name: 'Stone' });
    const trim = new MeshPhysicalMaterial({ name: 'Trim' });
    const lambert = new MeshLambertMaterial({ name: 'Plaster' });
    const unlit = new MeshBasicMaterial({ name: 'Glow' });
    const root = new Group();
    const wall = entityMesh('gs_wall', shared);
    const wallPart = entityMesh(null, trim); // a primitive of the wall: no gs_id of its own
    const wallChild = entityMesh('gs_wall_child', shared); // another entity: not lightmapped
    wall.add(wallPart, wallChild);
    const crate = entityMesh('gs_crate', shared); // dynamic: not in lightmaps.objects
    const floor = entityMesh('gs_floor', shared);
    const roof = entityMesh('gs_roof', shared);
    const step = entityMesh('gs_step', lambert);
    const noUv = entityMesh('gs_nouv', shared);
    const sign = entityMesh('gs_sign', [shared, unlit]);
    const sign2 = entityMesh(null, unlit);
    sign.add(sign2);
    const lost = entityMesh('gs_lost', shared);
    const lamp = new PointLight();
    lamp.userData.gs_id = 'gs_lamp';
    const area = new Object3D(); // a lamp node whose light is a child (glTF light orientation node)
    area.userData.gs_id = 'gs_area';
    const areaLight = new DirectionalLight();
    const otherLamp = new PointLight(); // a child entity of the baked lamp: realtime
    otherLamp.userData.gs_id = 'gs_other_lamp';
    area.add(areaLight, otherLamp);
    const sun = new DirectionalLight();
    sun.userData.gs_id = 'gs_sun';
    root.add(wall, crate, floor, roof, step, noUv, sign, lost, lamp, area, sun);
    const textures = [new Texture(), new Texture(), null];
    textures[0]!.name = 'gs_lightmap_0';
    return { root, shared, trim, lambert, unlit, wall, wallPart, wallChild, crate, floor, roof, step, noUv, sign, sign2, lost, lamp, areaLight, otherLamp, sun, textures };
  }

  it('clones materials per (material, atlas, channel) and leaves moving meshes alone', () => {
    const s = scene();
    const warnings: string[] = [];
    const summary = applyLightmaps(s.root, { lightmaps }, s.textures, (m) => warnings.push(m));

    const wall = s.wall.material as MeshStandardMaterial;
    expect(wall).not.toBe(s.shared);
    expect(wall).toBeInstanceOf(MeshStandardMaterial);
    expect(wall.name).toBe('Stone');
    expect(wall.lightMapIntensity).toBeCloseTo(Math.PI * 1.5, 10);
    expect(wall.lightMap!.channel).toBe(1);
    expect(wall.lightMap!.source).toBe(s.textures[0]!.source); // a clone sharing the image
    expect(wall.lightMap!.name).toBe('gs_lightmap_0');
    expect(wall.userData.gs_lightmap).toBe(true);
    // Same material, atlas and channel: one clone.
    expect(s.floor.material).toBe(wall);
    // Another atlas, another channel: other clones, other textures.
    const roof = s.roof.material as MeshStandardMaterial;
    expect(roof).not.toBe(wall);
    expect(roof.lightMapIntensity).toBeCloseTo(Math.PI * 2, 10);
    expect(roof.lightMap!.source).toBe(s.textures[1]!.source);
    const step = s.step.material as MeshLambertMaterial;
    expect(step).toBeInstanceOf(MeshLambertMaterial);
    expect(step.lightMap!.channel).toBe(0);
    expect(step.lightMap).not.toBe(wall.lightMap);
    // The entity's own primitives get it; child entities and moving meshes keep the shared material.
    const part = s.wallPart.material as MeshPhysicalMaterial;
    expect(part).toBeInstanceOf(MeshPhysicalMaterial);
    expect(part.lightMap).toBe(wall.lightMap);
    expect(s.wallChild.material).toBe(s.shared);
    expect(s.crate.material).toBe(s.shared);
    expect(s.shared.lightMap).toBeNull();
    expect(s.shared.userData.gs_lightmap).toBeUndefined();
    // An atlas that did not load: skipped.
    expect(s.lost.material).toBe(s.shared);
    // Multi-material: the lit one is lightmapped, the unlit one stays (one warning per material type).
    const [signLit, signUnlit] = s.sign.material as Material[];
    expect(signLit).toBe(wall);
    expect(signUnlit).toBe(s.unlit);
    expect(s.sign2.material).toBe(s.unlit);

    // wall, its part, floor, roof, step, sign; clones: Stone@0:1, Trim@0:1, Stone@1:1, Plaster@0:0.
    expect(summary).toMatchObject({ meshes: 6, materials: 4, lights: 2 });
    expect(summary.warnings).toEqual(warnings);
    expect(warnings.filter((w) => w.includes('MeshBasicMaterial'))).toHaveLength(1);
  });

  it('warns about meshes without the lightmap UVs and skips them', () => {
    const s = scene();
    const warnings: string[] = [];
    applyLightmaps(s.root, { lightmaps }, s.textures, (m) => warnings.push(m));
    expect(s.noUv.material).toBe(s.shared);
    expect(warnings).toContain('gs_nouv has no lightmap UVs ("uv2"), so it gets no lightmap; bake and export again');
  });

  it('turns off baked lamps, also a light under the lamp node, but not realtime ones or child entities', () => {
    const s = scene();
    applyLightmaps(s.root, { lightmaps }, s.textures, () => {});
    expect(s.lamp.visible).toBe(false);
    expect(s.areaLight.visible).toBe(false);
    expect(s.otherLamp.visible).toBe(true);
    expect(s.sun.visible).toBe(true);
  });

  it('does nothing without lightmaps', () => {
    const s = scene();
    expect(applyLightmaps(s.root, { lightmaps: null }, [], () => {})).toEqual({ meshes: 0, materials: 0, lights: 0, warnings: [] });
    expect(s.wall.material).toBe(s.shared);
    expect(s.lamp.visible).toBe(true);
  });

  it('patches lightmapped shaders so the lightmap replaces ambient, hemisphere, probe and environment diffuse light', () => {
    expect(lightmapShaderChunk()).not.toBeNull(); // three.js still has the chunk lines the patch edits
    const s = scene();
    applyLightmaps(s.root, { lightmaps }, s.textures, () => {});
    const plain = fragmentOf(s.shared);
    // Unchanged material: three.js's own shader (the lightmap adds to the rest).
    expect(plain).toBe(resolveIncludes(ShaderLib.physical.fragmentShader));
    expect(plain).toContain('irradiance += lightMapIrradiance;');
    expect(plain).toContain('iblIrradiance += getIBLIrradiance( geometryNormal );');

    for (const [material, lib] of [
      [s.wall.material as Material, 'physical'],
      [s.wallPart.material as Material, 'physical'],
      [s.step.material as Material, 'lambert'],
    ] as const) {
      const main = mainOf(fragmentOf(material, lib));
      const replaced = main.indexOf('irradiance = lightMapIrradiance;');
      expect(replaced).toBeGreaterThan(0);
      expect(main).not.toContain('irradiance += lightMapIrradiance');
      // Ambient, light probes and the hemisphere (fill) light are gathered first, then overwritten...
      for (const term of ['getAmbientLightIrradiance( ambientLightColor )', 'getLightProbeIrradiance( lightProbe', 'getHemisphereLightIrradiance( hemisphereLights']) {
        expect(main.indexOf(term)).toBeGreaterThan(0);
        expect(main.indexOf(term)).toBeLessThan(replaced);
      }
      // ...and nothing is added after: the environment's diffuse light (iblIrradiance) stays zero.
      expect(main).not.toMatch(/iblIrradiance \+=/);
      expect(main.slice(replaced).match(/(?<!\w)irradiance \+= [^;]*;/g)).toEqual(['irradiance += iblIrradiance;']);
      // Reflections of the environment stay.
      expect(main).toContain('radiance += iblRadiance;');
    }
    // Patched programs are told apart from the stock ones, and shared between lightmapped materials.
    const wall = s.wall.material as Material;
    expect(wall.customProgramCacheKey()).not.toBe(s.shared.customProgramCacheKey());
    expect(wall.customProgramCacheKey()).toMatch(/\|gs_lightmap$/);
    expect((s.roof.material as Material).customProgramCacheKey()).toBe(wall.customProgramCacheKey());
  });
});

/** Stands in for an `<img>`: loads if `exists(path)`, else fails, like a 404. */
class FakeImage extends EventTarget {
  width = 4;
  height = 4;
  crossOrigin: string | null = null;
  url = '';
  constructor(private readonly exists: (path: string) => boolean) {
    super();
  }
  set src(url: string) {
    this.url = url;
    const ok = this.exists(new URL(url).pathname);
    setTimeout(() => this.dispatchEvent(new Event(ok ? 'load' : 'error')), 0);
  }
}

describe('a game with lightmaps', () => {
  let dom: FakeDom;
  beforeAll(async () => {
    await new RapierPhysics().init();
  });
  beforeEach(() => {
    const files = sceneFiles();
    const manifest = JSON.parse(readFileSync(files['/export/basic.scene.json']!, 'utf8'));
    manifest.lightmaps = {
      version: 1,
      mode: 'indirect',
      atlases: [
        { file: 'lightmaps/lit_0.png?v=1', size: 64, scale: 2 },
        { file: 'lightmaps/missing.png?v=1', size: 64, scale: 1 },
      ],
      // Ground and Ramp have one UV set (TEXCOORD_0): the lightmap uses it here.
      objects: { gs_000000a00001: { atlas: 0, uv: 0 }, gs_000000a00004: { atlas: 1, uv: 0 } },
      bakedLights: ['gs_000000a00008'],
    };
    const file = path.join(path.dirname(files['/export/level2.scene.json']!), 'lit.scene.json');
    writeFileSync(file, JSON.stringify({ ...manifest, scene: 'lit' }));
    dom = installSceneDom();
    files['/export/lit.scene.json'] = file;
    const document = (globalThis as unknown as { document: Record<string, unknown> }).document;
    document.createElementNS = () => new FakeImage((p) => p === '/export/lightmaps/lit_0.png');
  });
  afterEach(() => {
    dom.restore();
    vi.restoreAllMocks();
  });

  it('lightmaps static meshes, turns baked lamps off, survives a missing atlas and disposes them with the scene', async () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
    const game = await heldGame(dom);
    await game.loadUrl('/export/lit.scene.json');
    expect(game.scene.name).toBe('lit');
    const ground = game.world.get('gs_000000a00001')!.object3d as Mesh;
    const material = ground.material as MeshStandardMaterial;
    expect(material.userData.gs_lightmap).toBe(true);
    expect(material.lightMap!.name).toBe('gs_lightmap_0');
    expect(material.lightMap!.channel).toBe(0);
    expect(material.lightMapIntensity).toBeCloseTo(Math.PI * 2, 10);
    expect((material.lightMap!.image as FakeImage).url).toBe('http://game.test/export/lightmaps/lit_0.png?v=1');
    expect(material.lightMap!.flipY).toBe(false);
    // The atlas that did not load: a warning, and the Ramp keeps its material.
    const ramp = game.world.get('gs_000000a00004')!.object3d as Mesh;
    expect((ramp.material as MeshStandardMaterial).lightMap).toBeNull();
    expect(warn.mock.calls.map((c) => String(c[0])).join('\n')).toMatch(/Lightmap lightmaps\/missing\.png\?v=1 did not load/);
    // The baked sun is off; a moving mesh is untouched.
    const sun = game.world.get('gs_000000a00008')!.object3d;
    const lights: boolean[] = [];
    sun.traverse((o) => {
      if ((o as DirectionalLight).isLight) lights.push(o.visible);
    });
    expect(lights).toEqual([false]);
    expect(((game.world.get('gs_000000a00005')!.object3d as Mesh).material as MeshStandardMaterial).lightMap).toBeNull();

    // Loading another scene disposes the lightmapped material and its lightmap.
    const disposed: string[] = [];
    material.addEventListener('dispose', () => disposed.push('material'));
    material.lightMap!.addEventListener('dispose', () => disposed.push('lightMap'));
    await game.loadUrl('/export/basic.scene.json');
    expect(disposed.sort()).toEqual(['lightMap', 'material']);
    expect(game.scene.name).toBe('basic');
    game.dispose();
  });
});
