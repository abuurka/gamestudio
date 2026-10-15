// Library mechanics: lazily loaded shaders (`load`), the project override of a library shader, the
// new defineShader options, and the compilePatched checks porting relies on.
import { BoxGeometry, Color, Group, Mesh, MeshStandardMaterial, ShaderMaterial, UniformsLib, UniformsUtils, type Material } from 'three';
import { describe, expect, it, vi } from 'vitest';
import { DEFAULT_BACKGROUND_RGB, updateSkyUniforms } from '../../src/game/sky';
import { MaterialShaders } from '../../src/scene/materials';
import { defineShader, field, getShader, loadShader, patchShader, skyGlsl, skyUniforms, toRegistryJSON, type ShaderSky } from '../../src/schema';
import { loadedShader } from '../../src/schema/shaders';
import { activeGlsl, compilePatched, unboundUniforms } from './helpers';

function binder() {
  const warnings: string[] = [];
  const shaders = new MaterialShaders({ renderer: () => null as never, time: { value: 0 }, assetUrl: (p) => p, warn: (m) => void warnings.push(m) });
  const mesh = new Mesh(new BoxGeometry(), new MeshStandardMaterial({ name: 'M' }));
  const scene = new Group().add(mesh);
  return { shaders, warnings, mesh, scene, original: mesh.material };
}

const patchedWith = (key: string) => (material: Material) => patchShader(material, key, () => {});

describe('lazily loaded shaders', () => {
  it('load only when a manifest names them, once per definition', async () => {
    const used = vi.fn(async () => ({ apply: patchedWith('lazy-used') }));
    const unused = vi.fn(async () => ({ apply: patchedWith('lazy-unused') }));
    defineShader('LazyUsed', { load: used });
    defineShader('LazyUnused', { load: unused });
    const { shaders, warnings, mesh, scene } = binder();
    const materials = { M: { shader: 'LazyUsed', values: {} } };
    await shaders.prepare(materials);
    await shaders.prepare(materials);
    shaders.bind([scene], materials, () => [scene]);
    expect(used).toHaveBeenCalledTimes(1);
    expect(unused).not.toHaveBeenCalled();
    expect((mesh.material as Material).customProgramCacheKey()).toMatch(/\|lazy-used$/);
    expect(warnings).toEqual([]);
    // A redefinition (hot module replacement) loads its own module.
    const again = vi.fn(async () => ({ apply: patchedWith('lazy-used-2') }));
    defineShader('LazyUsed', { load: again });
    await loadShader(getShader('LazyUsed')!);
    expect(again).toHaveBeenCalledTimes(1);
  });

  it('apply once loaded when bound before the module arrived (no prepare)', async () => {
    let release!: () => void;
    const gate = new Promise<void>((resolve) => (release = resolve));
    defineShader('LazySlow', {
      load: async () => {
        await gate;
        return { apply: patchedWith('lazy-slow') };
      },
    });
    const { shaders, warnings, mesh, scene, original } = binder();
    const summary = shaders.bind([scene], { M: { shader: 'LazySlow', values: {} } }, () => [scene]);
    expect(summary.applied).toBe(0);
    expect(mesh.material).toBe(original);
    release();
    await vi.waitFor(() => expect((mesh.material as Material).customProgramCacheKey()).toMatch(/\|lazy-slow$/));
    expect(warnings).toEqual([]);
  });

  it('report a module that fails or has no hooks as a failed shader; the material keeps its Blender look', async () => {
    defineShader('LazyBroken', { load: () => Promise.reject(new Error('Failed to fetch dynamically imported module')) });
    defineShader('LazyEmpty', { load: async () => ({}) as never });
    defineShader('LazyOdd', { load: async () => ({ apply: 3 }) as never });
    for (const [name, message] of [
      ['LazyBroken', 'Failed to fetch dynamically imported module'],
      ['LazyEmpty', 'the loaded module exports neither apply nor mesh'],
      ['LazyOdd', "the loaded module's apply is not a function"],
    ]) {
      const { shaders, warnings, mesh, scene, original } = binder();
      const materials = { M: { shader: name!, values: {} } };
      await shaders.prepare(materials); // never rejects
      shaders.bind([scene], materials, () => [scene]);
      expect(warnings).toEqual([`Game shader "${name}" failed on material "M": ${message}`]);
      expect(mesh.material).toBe(original);
    }
  });
});

describe('defineShader options', () => {
  it('takes a category and a preview, and either load or apply/mesh', () => {
    const shader = defineShader('OptBoth', {
      category: 'Water',
      fields: { speed: field.float(1) },
      preview: { mesh: 'plane', camera: 'top', t: 2, values: { speed: 3 } },
      load: async () => ({ apply() {} }),
    });
    expect(shader).toMatchObject({ category: 'Water', preview: { mesh: 'plane', camera: 'top', t: 2, values: { speed: 3 } }, builtin: false });
    const entry = toRegistryJSON().shaders.find((s) => s.name === 'OptBoth')!;
    expect(Object.keys(entry)).toEqual(['name', 'category', 'builtin', 'fields']); // preview is not in the registry
    expect(() => defineShader('OptBad', { load: async () => ({}), apply() {} })).toThrow('give either load or apply/mesh, not both');
    expect(() => defineShader('OptBad', { load: 'x' as never })).toThrow('load must be a function');
    expect(() => defineShader('OptBad', { category: '' })).toThrow('category must be a non-empty string');
  });
});

describe('the sky (ctx.sky)', () => {
  const rgb = (u: { value: Color }) => u.value.toArray().map((x) => Math.round(x * 1000) / 1000);

  it('is one set of uniform objects every shader gets, set from the scene like the background and fog', async () => {
    const seen: ShaderSky[] = [];
    defineShader('SkySeen', { apply: (_m, _v, ctx) => void seen.push(ctx.sky) });
    const a = await compilePatched('SkySeen');
    expect(seen[0]).toBe(a.sky);
    const settings = { sky: { zenith: [0.1, 0.2, 0.7], horizon: [0.5, 0.7, 0.9], ground: [0.2, 0.3, 0.2] }, background: [1, 0, 0], fog: null } as const;
    const zenith = a.sky.zenith;
    updateSkyUniforms(a.sky, structuredClone(settings) as never);
    expect(a.sky.zenith).toBe(zenith); // in place: shaders hold the objects
    expect([rgb(a.sky.zenith), rgb(a.sky.horizon), rgb(a.sky.ground)]).toEqual([[0.1, 0.2, 0.7], [0.5, 0.7, 0.9], [0.2, 0.3, 0.2]]);
    expect([rgb(a.sky.fog), a.sky.fogNear.value, a.sky.fogFar.value]).toEqual([[0.5, 0.7, 0.9], 0, 0]); // no fog: the horizon
    // A flat world colour is the sky in every direction; fog has its own colour and distances.
    updateSkyUniforms(a.sky, { sky: null, background: [0.3, 0.3, 0.4], fog: { color: [0.6, 0.6, 0.6], near: 10, far: 80 } });
    expect([rgb(a.sky.zenith), rgb(a.sky.horizon), rgb(a.sky.ground)]).toEqual([[0.3, 0.3, 0.4], [0.3, 0.3, 0.4], [0.3, 0.3, 0.4]]);
    expect([rgb(a.sky.fog), a.sky.fogNear.value, a.sky.fogFar.value]).toEqual([[0.6, 0.6, 0.6], 10, 80]);
    // Neither: the engine's default background.
    updateSkyUniforms(a.sky, { sky: null, background: null, fog: null });
    expect(rgb(a.sky.horizon)).toEqual([...DEFAULT_BACKGROUND_RGB]);
  });

  it('skyGlsl samples it under a prefix, with skyUniforms binding the shared objects', async () => {
    defineShader('SkyGlsl', {
      apply(material, _values, ctx) {
        patchShader(material, 'sky-glsl', (shader) => {
          Object.assign(shader.uniforms, skyUniforms(ctx.sky, 'tst'));
          shader.fragmentShader = shader.fragmentShader
            .replace('#include <common>', `#include <common>\n${skyGlsl('tst')}`)
            .replace('#include <emissivemap_fragment>', '#include <emissivemap_fragment>\ntotalEmissiveRadiance += tstSky(vec3(0.0, 1.0, 0.0));');
        });
      },
    });
    const c = await compilePatched('SkyGlsl');
    expect(c.uniforms.tstSkyHorizon).toBe(c.sky.horizon);
    expect(c.fragmentShader).toContain('vec3 tstSky(vec3 direction)');
    expect(() => skyGlsl('2x')).toThrow('not a GLSL name prefix');
  });
});

describe('a project shader with a library name', () => {
  it('overrides the library one: the registry lists it as the project’s, and the library module is never loaded', async () => {
    const library = getShader('Triplanar')!;
    expect(library.builtin).toBe(true);
    // A copied library file that kept `builtin: true` is still the project's.
    const own = defineShader('Triplanar', {
      description: 'My own',
      builtin: true,
      fields: { tile: field.float(1) },
      apply: patchedWith('my-triplanar'),
    });
    expect(getShader('Triplanar')).toBe(own);
    const entry = toRegistryJSON({ shaderSources: { Triplanar: 'src/shaders/triplanar.ts' } }).shaders.find((s) => s.name === 'Triplanar')!;
    expect(entry).toMatchObject({ builtin: false, source: 'src/shaders/triplanar.ts', description: 'My own' });
    const { shaders, mesh, scene } = binder();
    const materials = { M: { shader: 'Triplanar', values: {} } };
    await shaders.prepare(materials);
    shaders.bind([scene], materials, () => [scene]);
    expect((mesh.material as Material).customProgramCacheKey()).toMatch(/\|my-triplanar$/);
    expect(loadedShader(library)).toBeUndefined(); // never asked for
  });
});

describe('compilePatched', () => {
  it('fails on a replace() anchor that did not match, unless allowed', async () => {
    defineShader('CheckAnchor', {
      apply(material) {
        patchShader(material, 'check-anchor', (shader) => {
          shader.fragmentShader = shader.fragmentShader.replace('#include <map_fragment>', '#include <map_fragment>\n// ok').replace('#include <no_such_chunk>', '// lost');
        });
      },
    });
    await expect(compilePatched('CheckAnchor')).rejects.toThrow('replace() anchor did not match: #include <no_such_chunk>');
    const c = await compilePatched('CheckAnchor', { allowMiss: ['#include <no_such_chunk>'] });
    expect(c.edits).toEqual([
      { pattern: '#include <map_fragment>', matched: true },
      { pattern: '#include <no_such_chunk>', matched: false },
    ]);
  });

  it('fails on a uniform declared in GLSL but missing from shader.uniforms, and on duplicate declarations', async () => {
    defineShader('CheckUniform', {
      apply(material) {
        patchShader(material, 'check-uniform', (shader) => {
          shader.fragmentShader = shader.fragmentShader.replace('#include <common>', '#include <common>\nuniform float uForgot;');
        });
      },
    });
    await expect(compilePatched('CheckUniform')).rejects.toThrow('fragment shader declares uniform uForgot, which is not in shader.uniforms');
    defineShader('CheckTwice', {
      apply(material) {
        patchShader(material, 'check-twice', (shader) => {
          shader.uniforms.uK = { value: 1 };
          shader.vertexShader = shader.vertexShader.replace('#include <common>', '#include <common>\nuniform float uK;\nvarying vec3 vK;\nvarying vec3 vK;');
        });
      },
    });
    await expect(compilePatched('CheckTwice')).rejects.toThrow('vertex shader declares varying vK twice');
  });

  it('fails on a uniform used in a stage that does not declare it', async () => {
    defineShader('CheckUndeclared', {
      apply(material) {
        patchShader(material, 'check-undeclared', (shader) => {
          shader.uniforms.uLift = { value: 1 };
          shader.fragmentShader = shader.fragmentShader.replace('#include <common>', '#include <common>\nuniform float uLift;');
          shader.vertexShader = shader.vertexShader.replace('#include <begin_vertex>', '#include <begin_vertex>\ntransformed.y += uLift;');
        });
      },
    });
    await expect(compilePatched('CheckUndeclared')).rejects.toThrow('vertex shader uses uniform uLift without declaring it');
  });

  describe('a replacement material (ShaderMaterial)', () => {
    const VERTEX = '#include <common>\n#include <fog_pars_vertex>\nvoid main() {\n  vec4 mvPosition = modelViewMatrix * vec4(position, 1.0);\n  gl_Position = projectionMatrix * mvPosition;\n  #include <fog_vertex>\n}';
    const FRAGMENT = (pars: string, body = '') =>
      `#include <common>\n#include <fog_pars_fragment>\nuniform vec3 uColor;\n${pars}\nvoid main() {\n  gl_FragColor = vec4(uColor${body}, 1.0);\n  #include <fog_fragment>\n}`;
    const replacing = (name: string, make: () => ShaderMaterial) => defineShader(name, { apply: () => make() });

    it('passes when every uniform it declares is bound, fog included', async () => {
      replacing('CheckReplaceOk', () => new ShaderMaterial({
        fog: true,
        uniforms: { ...UniformsUtils.clone(UniformsLib.fog), uColor: { value: new Color() } },
        vertexShader: VERTEX,
        fragmentShader: FRAGMENT('#ifdef GS_GLOW\nuniform float uGlow;\n#endif'), // not compiled in: no uGlow needed
      }));
      const c = await compilePatched('CheckReplaceOk');
      expect(c.material).toBeInstanceOf(ShaderMaterial);
      expect(unboundUniforms(c.material as ShaderMaterial, c.mesh)).toEqual([]);
    });

    it('fails on a declared uniform nothing sets, also one only its defines or fog compile in', async () => {
      replacing('CheckReplaceUnbound', () => new ShaderMaterial({
        uniforms: { uColor: { value: new Color() } },
        vertexShader: '#include <common>\nvoid main() { gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }',
        fragmentShader: '#include <common>\nuniform vec3 uColor;\nuniform float uForgot;\nvoid main() { gl_FragColor = vec4(uColor * uForgot, 1.0); }',
      }));
      await expect(compilePatched('CheckReplaceUnbound')).rejects.toThrow("fragment shader declares uniform uForgot, which is not in the material's uniforms");
      replacing('CheckReplaceDefine', () => new ShaderMaterial({
        defines: { GS_GLOW: '' },
        fog: true,
        uniforms: { uColor: { value: new Color() } }, // no fog uniforms
        vertexShader: VERTEX,
        fragmentShader: FRAGMENT('#ifdef GS_GLOW\nuniform float uGlow;\n#endif', ' * uGlow'),
      }));
      const error = await compilePatched('CheckReplaceDefine').catch((e: Error) => e.message);
      expect(error).toContain('declares uniform uGlow');
      expect(error).toContain('declares uniform fogColor');
    });

    it('fails on a bound uniform it uses without declaring it', async () => {
      replacing('CheckReplaceUndeclared', () => new ShaderMaterial({
        uniforms: { uColor: { value: new Color() }, uTime: { value: 0 } },
        vertexShader: '#include <common>\nvoid main() { gl_Position = projectionMatrix * modelViewMatrix * vec4(position + uTime, 1.0); }',
        fragmentShader: '#include <common>\nuniform vec3 uColor;\nvoid main() { gl_FragColor = vec4(uColor, 1.0); }',
      }));
      await expect(compilePatched('CheckReplaceUndeclared')).rejects.toThrow('vertex shader uses uniform uTime without declaring it');
    });
  });

  it('resolves #if, #ifdef, #elif and #else like the GLSL preprocessor', () => {
    const source = [
      '#ifdef A',
      'a',
      '#elif defined( B ) && NUM_DIR_LIGHTS > 0',
      'b',
      '#else',
      'c',
      '#endif',
      '#if ! defined( C )',
      '#define D 2',
      '#endif',
      '#if D == 2',
      'd',
      '#endif',
    ].join('\n');
    const lines = (defines: Record<string, string>) => activeGlsl(source, defines).split('\n').filter((l) => /^[a-d]$/.test(l));
    expect(lines({ A: '' })).toEqual(['a', 'd']);
    expect(lines({ B: '', NUM_DIR_LIGHTS: '1' })).toEqual(['b', 'd']);
    expect(lines({ B: '', NUM_DIR_LIGHTS: '0', C: '' })).toEqual(['c']);
  });

  it('fails when apply throws', async () => {
    defineShader('CheckThrows', {
      apply() {
        throw new Error('boom');
      },
    });
    await expect(compilePatched('CheckThrows')).rejects.toThrow('Game shader "CheckThrows" failed on material "GS_Preview": boom');
  });
});
