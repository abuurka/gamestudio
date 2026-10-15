// Game shaders: the registry and its JSON, patchShader (with the lightmap patch), binding manifest
// materials to a scene and its prefabs (replacements, warnings, reloads, hot redefinition), and the
// tone mapping and exposure that follow the scene's Color Management.
import { createHash } from 'node:crypto';
import { mkdtempSync, readFileSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import {
  ACESFilmicToneMapping,
  AgXToneMapping,
  BoxGeometry,
  Group,
  Mesh,
  MeshBasicMaterial,
  MeshStandardMaterial,
  NeutralToneMapping,
  NoToneMapping,
  ShaderLib,
  Texture,
  type Material,
  type Object3D,
} from 'three';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { Game } from '../src/game/game';
import { PostService } from '../src/game/post';
import { toneMappingOf } from '../src/game/renderer';
import { handleEditorMessage } from '../src/link/handlers';
import { RapierPhysics } from '../src/physics/rapier-backend';
import { cloneScene, ownMaterials } from '../src/scene/clone';
import { applyLightmaps } from '../src/scene/lightmaps';
import { processManifest } from '../src/scene/manifest';
import { MaterialShaders } from '../src/scene/materials';
import {
  defineComponent,
  defineShader,
  field,
  getComponent,
  getShader,
  listShaders,
  onShaderDefined,
  patchShader,
  shaderValues,
  toRegistryJSON,
  type ShaderContext,
} from '../src/schema';
import { installDom, type FakeDom } from './dom';
import { BASIC, sceneFiles } from './game-fixture';
import { FIXTURE_DIR } from './helpers';

type Compiled = { vertexShader: string; fragmentShader: string; uniforms: Record<string, { value: unknown }> };

/** What WebGL would compile for `material`: the physical shader after its onBeforeCompile. */
function compiled(material: Material): Compiled {
  const shader: Compiled = { vertexShader: ShaderLib.physical.vertexShader, fragmentShader: ShaderLib.physical.fragmentShader, uniforms: {} };
  material.onBeforeCompile(shader as never, null as never);
  return shader;
}

const count = (text: string, part: string): number => text.split(part).length - 1;

describe('defineShader', () => {
  it('registers shaders by name, sorted, in a namespace of their own; redefinition replaces', () => {
    const listener = vi.fn();
    const off = onShaderDefined(listener);
    const first = defineShader('RegWet', { description: 'Wet look', fields: { wet: field.float(0.5, { min: 0, max: 1 }) } });
    defineShader('RegAlpha');
    expect(listener).toHaveBeenCalledWith(first);
    expect(getShader('RegWet')).toBe(first);
    const second = defineShader('RegWet', { fields: { gloss: field.bool(true) } });
    expect(getShader(first)).toBe(second); // stale handles resolve to the current definition
    expect(listener).toHaveBeenCalledTimes(3);
    off();
    defineShader('RegAlpha');
    expect(listener).toHaveBeenCalledTimes(3);
    const names = listShaders().map((s) => s.name);
    expect(names).toEqual([...names].sort());
    expect(names.filter((n) => n === 'RegWet')).toHaveLength(1);
    // A component may have the same name: another namespace.
    defineComponent('RegWet', { fields: { n: field.int(1) } });
    expect(getShader('RegWet')).toBe(second);
    expect(Object.keys(getComponent('RegWet')!.fields)).toEqual(['n']);
    expect(shaderValues(second, { gloss: 0, extra: 1 })).toEqual({ gloss: false });
  });

  it('checks names and fields like defineComponent, and rejects entity fields', () => {
    expect(() => defineShader('lower')).toThrow('Invalid shader name "lower"');
    expect(() => defineShader('Bad', { fields: { Upper: field.int() } })).toThrow('Invalid field name "Bad.Upper"');
    expect(() => defineShader('Bad', { fields: { x: 3 as never } })).toThrow(/field\.\* builder/);
    expect(() => defineShader('Bad', { fields: { target: field.entity() } })).toThrow('Field "Bad.target": game shaders cannot have entity fields');
    expect(() => defineShader('Bad', { apply: 'no' as never })).toThrow('Game shader "Bad": apply must be a function');
    expect(getShader('Bad')).toBeUndefined();
  });

  it('are listed in the registry JSON after the components, in the component field format', () => {
    defineShader('JsonTriplanar', {
      description: 'World-space scans',
      fields: { diffuse: field.asset(['.webp', '.png']), tile: field.float(3.2, { min: 0.01 }), tint: field.color([1, 0.9, 0.8, 1]) },
    });
    defineShader('JsonBare');
    const json = toRegistryJSON({ generatedAt: '2026-10-11T00:00:00.000Z', shaderSources: { JsonTriplanar: 'src/shaders/triplanar.ts' } });
    expect(Object.keys(json)).toEqual(['format', 'formatVersion', 'generatedAt', 'components', 'shaders']);
    expect(json.formatVersion).toBe(1);
    const entry = json.shaders.find((s) => s.name === 'JsonTriplanar')!;
    expect(entry).toEqual({
      name: 'JsonTriplanar',
      description: 'World-space scans',
      builtin: false,
      source: 'src/shaders/triplanar.ts',
      fields: [
        { name: 'diffuse', type: 'asset', default: '', extensions: ['.webp', '.png'] },
        { name: 'tile', type: 'float', default: 3.2, min: 0.01 },
        { name: 'tint', type: 'color', default: [1, 0.9, 0.8, 1] },
      ],
    });
    expect(Object.keys(entry)).toEqual(['name', 'description', 'builtin', 'source', 'fields']);
    expect(json.shaders.find((s) => s.name === 'JsonBare')).toEqual({ name: 'JsonBare', builtin: false, fields: [] });
    expect(json.shaders.map((s) => s.name)).toEqual(listShaders().map((s) => s.name));
    expect(json.components.some((c) => c.name === 'JsonTriplanar')).toBe(false);
  });
});

describe('patchShader', () => {
  it('chains after the existing onBeforeCompile, keys the program, and ignores a key it already has', () => {
    const material = new MeshStandardMaterial();
    const order: string[] = [];
    material.onBeforeCompile = () => void order.push('own');
    const version = material.version;
    patchShader(material, 'a', (shader) => {
      order.push('a');
      shader.fragmentShader += '\n// a';
    });
    patchShader(material, 'b', (shader) => {
      order.push('b');
      shader.uniforms.uB = { value: 1 };
    });
    patchShader(material, 'a', () => void order.push('a again'));
    const shader = compiled(material);
    expect(order).toEqual(['own', 'a', 'b']);
    expect(shader.fragmentShader.endsWith('\n// a')).toBe(true);
    expect(shader.uniforms.uB).toEqual({ value: 1 });
    expect(material.customProgramCacheKey()).toMatch(/\|a\|b$/);
    expect(material.version).toBeGreaterThan(version); // recompiled if it was drawn before
    expect(() => patchShader(material, '', () => {})).toThrow(/needs a key/);
  });

  it('survives material.clone(), and a clone takes patches of its own', () => {
    const material = new MeshStandardMaterial({ name: 'Sand' });
    patchShader(material, 'tri', (shader) => void (shader.fragmentShader += '\n// tri'));
    const copy = material.clone();
    expect(copy).toBeInstanceOf(MeshStandardMaterial);
    expect(copy.name).toBe('Sand');
    expect(copy.customProgramCacheKey()).toBe(material.customProgramCacheKey());
    expect(count(compiled(copy).fragmentShader, '// tri')).toBe(1);
    patchShader(copy, 'tri', (shader) => void (shader.fragmentShader += '\n// tri'));
    patchShader(copy, 'wet', (shader) => void (shader.fragmentShader += '\n// wet'));
    expect(copy.customProgramCacheKey()).toMatch(/\|tri\|wet$/);
    expect(material.customProgramCacheKey()).toMatch(/\|tri$/); // the original is unchanged
    const fragment = compiled(copy.clone()).fragmentShader;
    expect([count(fragment, '// tri'), count(fragment, '// wet')]).toEqual([1, 1]);
    // The engine's own copies (ownMaterials, for scenes cloned from the parse cache) keep them too.
    const mesh = new Mesh(new BoxGeometry(), material);
    ownMaterials(new Group().add(mesh));
    expect(mesh.material).not.toBe(material);
    expect((mesh.material as Material).customProgramCacheKey()).toBe(material.customProgramCacheKey());
  });

  it('composes with the lightmap patch', () => {
    const geometry = new BoxGeometry();
    geometry.setAttribute('uv1', geometry.getAttribute('uv').clone());
    const wall = new Mesh(geometry, new MeshStandardMaterial({ name: 'Stone' }));
    wall.userData.gs_id = 'gs_wall';
    const root = new Group().add(wall);
    const lightmaps = { version: 1, mode: 'indirect', atlases: [{ file: 'a.png', size: 64, scale: 1 }], objects: { gs_wall: { atlas: 0, uv: 1 } }, bakedLights: [] };
    applyLightmaps(root, { lightmaps }, [new Texture()], () => {});
    const lit = wall.material as MeshStandardMaterial;
    patchShader(lit, 'triplanar', (shader) => {
      shader.fragmentShader = shader.fragmentShader.replace('#include <map_fragment>', '#include <map_fragment>\n// triplanar');
    });
    for (const material of [lit, lit.clone()]) {
      const fragment = compiled(material).fragmentShader;
      expect(fragment).toContain('// triplanar');
      expect(fragment).toContain('irradiance = lightMapIrradiance;'); // the lightmap chunk replaced the include
      expect(fragment).not.toContain('#include <lights_fragment_maps>');
      expect(material.customProgramCacheKey()).toMatch(/\|gs_lightmap\|triplanar$/);
    }
  });
});

/** A level: GS_Sand on two meshes, a multi-material mesh, and a prefab template with its own GS_Sand. */
function level() {
  const geometry = new BoxGeometry();
  const sand = new MeshStandardMaterial({ name: 'GS_Sand' });
  const templateSand = new MeshStandardMaterial({ name: 'GS_Sand' }); // the same name in the prefab GLB
  const plain = new MeshStandardMaterial({ name: 'Plain' });
  const glow = new MeshBasicMaterial({ name: 'Glow' });
  const a = new Mesh(geometry, sand);
  const b = new Mesh(geometry, sand);
  const c = new Mesh(geometry, [plain, glow]);
  const t = new Mesh(geometry, templateSand);
  const scene = new Group().add(a, b, c);
  const template = new Group().add(t);
  return { scene, template, sand, templateSand, plain, glow, a, b, c, t };
}

function binder() {
  const warnings: string[] = [];
  const host = { renderer: () => null as never, time: { value: 0 }, assetUrl: (p: string) => `/game/${p}`, warn: (m: string) => void warnings.push(m) };
  return { host, warnings, shaders: new MaterialShaders(host) };
}

describe('binding manifest materials', () => {
  it('applies once per material instance on clones, then runs mesh per mesh; the loaded materials stay as they were', () => {
    const calls: Array<{ material: Material; values: Record<string, unknown>; ctx: ShaderContext; patchedBefore: boolean }> = [];
    const meshes: Mesh[] = [];
    defineShader('BindWet', {
      fields: { wet: field.float(0.2), tex: field.asset(['.png']), mode: field.enum(['a', 'b']) },
      apply(material, values, ctx) {
        calls.push({ material, values, ctx, patchedBefore: material.customProgramCacheKey().includes('bindwet') });
        patchShader(material, 'bindwet', (shader) => void (shader.uniforms.uTime = ctx.time));
      },
      mesh(mesh) {
        meshes.push(mesh);
      },
    });
    const l = level();
    const { host, warnings, shaders } = binder();
    const summary = shaders.bind([l.scene, l.template], { GS_Sand: { shader: 'BindWet', values: { wet: 0.6, tex: 'assets/sand.png', mode: 'zzz', junk: 1 } } }, () => [l.scene, l.template]);
    expect(summary).toEqual({ applied: 2, warnings: [] });
    expect(warnings).toEqual([]);
    expect(calls).toHaveLength(2);
    expect(calls[0]!.values).toEqual({ wet: 0.6, tex: 'assets/sand.png', mode: 'a' }); // coerced, unknown keys dropped
    expect(calls.every((c) => !c.patchedBefore)).toBe(true);
    const ctx = calls[0]!.ctx;
    expect(ctx.material).toBe('GS_Sand');
    expect(ctx.time).toBe(host.time);
    expect(ctx.assetUrl('assets/sand.png')).toBe('/game/assets/sand.png');
    ctx.warn('missing UVs');
    expect(warnings).toEqual(['Game shader "BindWet" (material "GS_Sand"): missing UVs']);

    const drawn = l.a.material as Material;
    expect(drawn).toBe(calls[0]!.material);
    expect(drawn).not.toBe(l.sand);
    expect(l.b.material).toBe(drawn); // one apply for the material both meshes draw
    expect(drawn.name).toBe('GS_Sand');
    expect(drawn.customProgramCacheKey()).toMatch(/\|bindwet$/);
    expect(l.sand.customProgramCacheKey()).not.toContain('bindwet');
    expect(compiled(drawn).uniforms.uTime).toBe(host.time); // the one shared time uniform
    expect(l.t.material).toBe(calls[1]!.material);
    expect(meshes).toEqual([l.a, l.b, l.t]);
    // A prefab instance is a clone of the template: it draws the shaded material.
    const instance = cloneScene(l.template);
    expect((instance.children[0] as Mesh).material).toBe(l.t.material);
  });

  it('puts a returned replacement material on every mesh that drew the original', () => {
    defineShader('BindSwap', {
      apply(material) {
        return new MeshBasicMaterial({ name: `${material.name} (swapped)` });
      },
    });
    const l = level();
    const { shaders } = binder();
    expect(shaders.bind([l.scene], { Plain: { shader: 'BindSwap', values: {} } }, () => [l.scene]).applied).toBe(1);
    const [first, second] = l.c.material as Material[];
    expect(first).toBeInstanceOf(MeshBasicMaterial);
    expect(first!.name).toBe('Plain (swapped)');
    expect(second).toBe(l.glow);
  });

  it('warns once per load about unknown shaders, names no mesh draws and shaders that throw; those keep their Blender look', () => {
    defineShader('BindBoom', {
      apply() {
        throw new Error('no uv2');
      },
    });
    defineShader('BindMeshBoom', {
      apply(material) {
        patchShader(material, 'meshboom', () => {});
      },
      mesh() {
        throw new Error('bad mesh');
      },
    });
    const l = level();
    const { warnings, shaders } = binder();
    const summary = shaders.bind(
      [l.scene, l.template],
      {
        GS_Sand: { shader: 'BindBoom', values: {} }, // two instances, one warning
        Missing: { shader: 'BindBoom', values: {} },
        Plain: { shader: 'NoSuchShader', values: {} },
        Glow: { shader: 'BindMeshBoom', values: {} },
      },
      () => [l.scene, l.template],
    );
    expect(warnings).toEqual([
      'Game shader "BindBoom" failed on material "GS_Sand": no uv2',
      'Material "Missing" has game shader "BindBoom" but no mesh in the scene or its prefabs draws it',
      'Material "Plain" uses game shader "NoSuchShader", which no src/shaders file defines',
      'Game shader "BindMeshBoom" failed on material "Glow": bad mesh',
    ]);
    expect(summary).toEqual({ applied: 0, warnings });
    expect(l.a.material).toBe(l.sand);
    expect(l.t.material).toBe(l.templateSand);
    expect(l.c.material).toEqual([l.plain, l.glow]);
  });

  it('applies a redefined shader again from fresh clones, to spawned copies too, without double patches', () => {
    defineShader('HotTint', {
      fields: { k: field.float(1) },
      apply(material, values) {
        patchShader(material, 'hot', (shader) => void (shader.fragmentShader += `\n// hot v1 ${values.k}`));
      },
    });
    const l = level();
    const { warnings, shaders } = binder();
    const off = onShaderDefined((shader) => shaders.redefine(shader)); // as the Game does
    shaders.bind([l.scene, l.template], { GS_Sand: { shader: 'HotTint', values: { k: 2 } } }, () => [l.scene, l.template]);
    const before = l.a.material as Material;
    const spawned = cloneScene(l.template); // ctx.spawn: shares the template's material
    l.scene.add(spawned);
    const disposed = vi.fn();
    before.addEventListener('dispose', disposed);

    defineShader('HotTint', {
      fields: { k: field.float(1), extra: field.bool(true) },
      apply(material, values) {
        patchShader(material, 'hot', (shader) => void (shader.fragmentShader += `\n// hot v2 ${values.k} ${values.extra}`));
      },
    });
    const after = l.a.material as Material;
    expect(after).not.toBe(before);
    expect(l.b.material).toBe(after);
    const fragment = compiled(after).fragmentShader;
    expect(fragment).toContain('// hot v2 2 true');
    expect(fragment).not.toContain('// hot v1');
    expect(count(after.customProgramCacheKey(), '|hot')).toBe(1);
    expect((spawned.children[0] as Mesh).material).toBe(l.t.material);
    expect(compiled(l.t.material as Material).fragmentShader).toContain('// hot v2');
    expect(disposed).toHaveBeenCalledTimes(1);
    expect(l.sand.customProgramCacheKey()).not.toContain('hot');

    // A redefinition that throws: the Blender look, and a warning.
    defineShader('HotTint', {
      apply() {
        throw new Error('typo');
      },
    });
    expect(l.a.material).toBe(l.sand);
    expect(warnings).toEqual(['Game shader "HotTint" failed on material "GS_Sand": typo']);
    off();
  });

  it('applies a shader defined after the load (a new src/shaders file) to the materials that named it', () => {
    const l = level();
    const { warnings, shaders } = binder();
    shaders.bind([l.scene], { Plain: { shader: 'LateShader', values: { k: 4 } } }, () => [l.scene]);
    expect(warnings).toEqual(['Material "Plain" uses game shader "LateShader", which no src/shaders file defines']);
    expect((l.c.material as Material[])[0]).toBe(l.plain);
    const late = defineShader('LateShader', {
      fields: { k: field.int(1) },
      apply(material, values) {
        material.userData.k = values.k;
      },
    });
    shaders.redefine(late);
    const [first] = l.c.material as Material[];
    expect(first).not.toBe(l.plain);
    expect(first!.userData.k).toBe(4);
  });
});

describe('the API.md example', () => {
  it('patches the vertex shader with the shared clock and live per-material uniforms', () => {
    const Swell = defineShader('Swell', {
      description: 'Waves that move with game time',
      category: 'Water',
      fields: {
        height: field.float(0.15, { min: 0, unit: 'distance', description: 'Wave height (m)' }),
        length: field.float(4, { min: 0.1, unit: 'distance', description: 'Distance between crests (m)' }),
        speed: field.float(1, { min: 0, description: 'How fast the waves travel' }),
      },
      apply(material, values, ctx) {
        patchShader(material, 'swell', (shader) => {
          shader.uniforms.uTime = ctx.time;
          shader.uniforms.uHeight = ctx.uniforms.height;
          shader.uniforms.uLength = ctx.uniforms.length;
          shader.uniforms.uSpeed = ctx.uniforms.speed;
          shader.vertexShader = shader.vertexShader
            .replace('#include <common>', '#include <common>\nuniform float uTime;\nuniform float uHeight;\nuniform float uLength;\nuniform float uSpeed;')
            .replace(
              '#include <begin_vertex>',
              `#include <begin_vertex>
          vec4 gsWorld = modelMatrix * vec4(transformed, 1.0);
          float gsK = 6.2831853 / uLength;
          transformed.y += uHeight * sin(gsWorld.x * gsK + uTime * uSpeed) * cos(gsWorld.z * gsK);`,
            );
        });
      },
      mesh(mesh) {
        mesh.castShadow = false;
      },
    });
    const water = new Mesh(new BoxGeometry(), new MeshStandardMaterial({ name: 'Water' }));
    water.castShadow = true;
    const scene = new Group().add(water);
    const { host, warnings, shaders } = binder();
    shaders.bind([scene], { Water: { shader: Swell.name, values: { height: 0.3 } } }, () => [scene]);
    expect(warnings).toEqual([]);
    const shader = compiled(water.material as Material);
    expect(shader.vertexShader).toContain('uniform float uHeight;');
    expect(shader.vertexShader).toContain('transformed.y += uHeight * sin(');
    expect(shader.uniforms.uTime).toBe(host.time);
    expect([shader.uniforms.uHeight!.value, shader.uniforms.uLength!.value, shader.uniforms.uSpeed!.value]).toEqual([0.3, 4, 1]);
    shaders.set('Water', { height: 0.5 }); // ctx.shaders.set
    expect(shader.uniforms.uHeight!.value).toBe(0.5);
    expect(water.castShadow).toBe(false);
  });
});

describe('manifest materials and tone mapping', () => {
  const base = { format: 'gamestudio.scene', formatVersion: 2, scene: 'x', glb: 'x.glb', entities: [] };

  it('reads materials; entries without a shader are dropped with a warning', () => {
    const { manifest, warnings } = processManifest({
      ...base,
      materials: {
        GS_Sand: { shader: 'Triplanar', values: { tile: 3.2, diffuse: 'assets/sand.webp' } },
        GS_Water: { shader: 'Water' },
        GS_Odd: { shader: 'Water', values: [1] },
        NoShader: { values: {} },
        Empty: { shader: '' },
        Null: null,
      },
    });
    expect(manifest.materials).toEqual({
      GS_Sand: { shader: 'Triplanar', values: { tile: 3.2, diffuse: 'assets/sand.webp' } },
      GS_Water: { shader: 'Water', values: {} },
      GS_Odd: { shader: 'Water', values: {} },
    });
    expect(warnings).toEqual([
      'Material "GS_Odd": "values" is not an object; game shader "Water" uses its defaults',
      'Skipping 3 "materials" entries without a game shader name: "NoShader", "Empty", "Null"',
    ]);
    expect(processManifest(base).manifest.materials).toEqual({});
    expect(processManifest({ ...base, materials: [1] }).warnings).toEqual(['Ignoring "materials": not an object']);
  });

  it('reads toneMapping and exposure; without them ACES at exposure 0, as older exports looked', () => {
    const settings = (s: unknown) => processManifest({ ...base, settings: s });
    expect(settings({ toneMapping: 'agx', exposure: -0.5 })).toMatchObject({ manifest: { settings: { toneMapping: 'agx', exposure: -0.5 } }, warnings: [] });
    expect(settings({})).toMatchObject({ manifest: { settings: { toneMapping: 'aces', exposure: 0 } }, warnings: [] });
    const bad = settings({ toneMapping: 'filmic', exposure: 'bright' });
    expect(bad.manifest.settings).toMatchObject({ toneMapping: 'aces', exposure: 0 });
    expect(bad.warnings).toEqual(['Unknown settings.toneMapping "filmic"; using "aces"', 'Ignoring settings.exposure "bright": not a number']);
  });

  it('maps tone mapping names to three.js, and the exposure multiplies the PostEffects exposure', () => {
    expect([toneMappingOf('neutral'), toneMappingOf('agx'), toneMappingOf('aces'), toneMappingOf('none')]).toEqual([
      NeutralToneMapping,
      AgXToneMapping,
      ACESFilmicToneMapping,
      NoToneMapping,
    ]);
    const post = new PostService({ exposure: 1.3 });
    const raw = { toneMappingExposure: 0 };
    post.render({ kind: 'webgpu', raw } as never, null as never, null as never);
    expect(raw.toneMappingExposure).toBe(1.3); // scale 1: as before
    post.exposureScale = 2 ** -1;
    post.render({ kind: 'webgpu', raw } as never, null as never, null as never);
    expect(raw.toneMappingExposure).toBeCloseTo(0.65, 10);
  });
});

// ---------------------------------------------------------------------------
// In a game: the basic fixture with a prefab GLB (a copy of basic.glb) and game shaders.

const BASIC_HASH = createHash('sha1').update(readFileSync(`${FIXTURE_DIR}basic.glb`)).digest('hex').slice(0, 16);
const dir = mkdtempSync(path.join(tmpdir(), 'gs-shaders-'));
writeFileSync(path.join(dir, 'shaded.prefabs.glb'), readFileSync(`${FIXTURE_DIR}basic.glb`));
const SHADED = '/export/shaded.scene.json';

function shadedManifest(): Record<string, any> {
  const m = JSON.parse(readFileSync(`${FIXTURE_DIR}basic.scene.json`, 'utf8'));
  return {
    ...m,
    formatVersion: 2,
    scene: 'shaded',
    files: { 'basic.glb': BASIC_HASH, 'shaded.prefabs.glb': BASIC_HASH },
    settings: { ...m.settings, toneMapping: 'neutral', exposure: 1 },
    prefabs: [
      { name: 'Coin', glb: 'shaded.prefabs.glb', offset: [0, 0, 0], entities: [{ id: 'gs_000000a00006', name: 'Coin', type: 'MESH', parent: null, components: {} }], animations: [] },
    ],
    materials: {
      GroundMat: { shader: 'GameTint', values: { amount: 0.5 } },
      CoinMat: { shader: 'GameTint', values: {} },
      LostMat: { shader: 'GameTint', values: {} },
      CrateMat: { shader: 'NotDefinedAnywhere', values: {} },
    },
  };
}
writeFileSync(path.join(dir, 'shaded.scene.json'), JSON.stringify(shadedManifest()));

/** The first mesh under an object. */
function firstMesh(object: Object3D): Mesh {
  let mesh: Mesh | null = null;
  object.traverse((o) => {
    if (!mesh && (o as Mesh).isMesh) mesh = o as Mesh;
  });
  return mesh!;
}

/** The first mesh of an entity (by name). */
const meshOf = (game: Game, name: string): Mesh => firstMesh(game.world.findByName(name)!.object3d);

describe('game shaders in a game', () => {
  let dom: FakeDom;
  let applied: Array<{ material: Material; amount: number; patchedBefore: boolean }>;
  let warn: { mock: { calls: unknown[][] }; mockRestore(): void };
  let game: Game | null = null;
  beforeAll(async () => {
    await new RapierPhysics().init();
  });
  beforeEach(() => {
    dom = installDom({ ...sceneFiles(), [SHADED]: path.join(dir, 'shaded.scene.json'), '/export/shaded.prefabs.glb': path.join(dir, 'shaded.prefabs.glb') });
    warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
    applied = [];
    defineShader('GameTint', {
      fields: { amount: field.float(0.25) },
      apply(material, values, ctx) {
        applied.push({ material, amount: values.amount, patchedBefore: material.customProgramCacheKey().includes('gametint') });
        patchShader(material, 'gametint', (shader) => {
          shader.uniforms.uTime = ctx.time;
          shader.fragmentShader += '\n// tint';
        });
      },
      mesh(mesh) {
        mesh.userData.tinted = ((mesh.userData.tinted as number | undefined) ?? 0) + 1;
      },
    });
  });
  afterEach(() => {
    game?.dispose();
    game = null;
    warn.mockRestore();
    dom.restore();
  });

  async function start(url: string, renderer?: object): Promise<Game> {
    game = new Game(dom.canvas as unknown as HTMLCanvasElement, { environment: 0, fillLight: 0, startScene: 'basic', exportBase: '/export/' });
    if (renderer) game.three.renderer = renderer as never;
    await game.physics.init();
    game.test.hold();
    await game.loadUrl(url);
    return game;
  }
  const warnings = (): string[] => warn.mock.calls.map((c) => String(c[0]));

  it('binds the scene and the prefab templates, so spawned prefabs draw the shader, and warns once', async () => {
    const g = await start(SHADED);
    // GroundMat and CoinMat in the scene GLB and again in the prefab GLB: four materials.
    expect(applied.map((a) => `${a.material.name}:${a.amount}`).sort()).toEqual(['CoinMat:0.25', 'CoinMat:0.25', 'GroundMat:0.5', 'GroundMat:0.5']);
    const ground = meshOf(g, 'Ground');
    expect(ground.material).toBe(applied.find((a) => a.material.name === 'GroundMat')!.material);
    expect((ground.material as Material).customProgramCacheKey()).toMatch(/\|gametint$/);
    expect(ground.userData.tinted).toBe(1);
    expect((meshOf(g, 'Crate').material as Material).customProgramCacheKey()).not.toContain('gametint');
    const spawned = firstMesh(g.spawn('Coin').object3d);
    expect(spawned.material).not.toBe(meshOf(g, 'Coin').material); // the template's material, not the scene's
    expect(applied.map((a) => a.material)).toContain(spawned.material);
    expect(compiled(spawned.material as Material).fragmentShader).toContain('// tint');
    expect(warnings()).toEqual([
      '[gamestudio] Material "LostMat" has game shader "GameTint" but no mesh in the scene or its prefabs draws it',
      '[gamestudio] Material "CrateMat" uses game shader "NotDefinedAnywhere", which no src/shaders file defines',
    ]);
  });

  it('keeps the shared time uniform equal to ctx.time', async () => {
    const g = await start(SHADED);
    const uniforms = compiled(meshOf(g, 'Ground').material as Material).uniforms;
    await g.test.begin();
    await g.test.advance(30);
    expect(g.test.status().gameTime).toBe(0.5);
    expect(uniforms.uTime!.value).toBeCloseTo(0.5, 10);
    await g.test.begin({ reset: true }); // a reset starts the scene's time over
    expect(uniforms.uTime!.value).toBe(0);
  });

  it('patches every load once: reloads and resets get fresh materials from the parse cache', async () => {
    const g = await start(SHADED);
    const first = meshOf(g, 'Ground').material as Material;
    handleEditorMessage(g, { t: 'scene.load', url: SHADED }, () => {});
    await g.idle();
    g.stop();
    await g.idle();
    expect(applied).toHaveLength(12); // four per load
    expect(applied.every((a) => !a.patchedBefore)).toBe(true); // never a material patched by an earlier load
    const ground = meshOf(g, 'Ground');
    const material = ground.material as Material;
    expect(material).not.toBe(first);
    expect(count(material.customProgramCacheKey(), '|gametint')).toBe(1);
    expect(count(compiled(material).fragmentShader, '// tint')).toBe(1);
    expect(ground.userData.tinted).toBe(1);
  });

  it('applies a redefined shader to the running scene without reloading it', async () => {
    const g = await start(SHADED);
    const spawned = g.spawn('Coin');
    const loads = g.loads;
    defineShader('GameTint', {
      apply(material) {
        patchShader(material, 'gametint2', (shader) => void (shader.fragmentShader += '\n// tint 2'));
      },
    });
    expect(g.loads).toBe(loads);
    const ground = meshOf(g, 'Ground').material as Material;
    expect(ground.customProgramCacheKey()).toMatch(/\|gametint2$/);
    expect(ground.customProgramCacheKey()).not.toContain('|gametint|');
    expect(compiled(firstMesh(spawned.object3d).material as Material).fragmentShader).toContain('// tint 2');
    expect(compiled(firstMesh(g.spawn('Coin').object3d).material as Material).fragmentShader).toContain('// tint 2');
  });

  it("follows the scene's tone mapping and exposure; older manifests keep ACES at exposure 1", async () => {
    const raw = { toneMapping: -1, toneMappingExposure: 0 };
    const renderer = { kind: 'webgpu', raw, render() {}, setSize() {}, dispose() {} };
    const g = await start(SHADED, renderer);
    g.post.set({ exposure: 1.5 });
    g.test.render();
    expect(raw.toneMapping).toBe(NeutralToneMapping);
    expect(raw.toneMappingExposure).toBeCloseTo(3, 10); // 2 ** 1 × 1.5
    await g.loadUrl(BASIC);
    g.test.render();
    expect(raw.toneMapping).toBe(ACESFilmicToneMapping);
    expect(raw.toneMappingExposure).toBeCloseTo(1.5, 10);
  });
});
