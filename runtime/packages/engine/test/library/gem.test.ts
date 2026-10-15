// Gem: the stone's colour replaces the lit result of the (patched) standard material.
import { MeshBasicMaterial, MeshPhysicalMaterial, MeshStandardMaterial } from 'three';
import { describe, expect, it } from 'vitest';
import { getShader } from '../../src/schema';
import { compilePatched, PREVIEW_MATERIAL, resolveIncludes } from './helpers';

describe('Gem', () => {
  it('is a built-in Surfaces shader', () => {
    expect(getShader('Gem')).toMatchObject({ builtin: true, category: 'Surfaces' });
  });

  it('draws the stone over the lit result, keeping fog and emission', async () => {
    const c = await compilePatched('Gem', { mesh: 'sphere' });
    expect(c.warnings).toEqual([]);
    expect(c.edits.every((e) => e.matched)).toBe(true);
    expect(c.material.customProgramCacheKey()).toMatch(/\|gs_gem$/);
    const fragment = resolveIncludes(c.fragmentShader);
    const gem = fragment.indexOf('outgoingLight = gem + totalEmissiveRadiance;');
    expect(gem).toBeGreaterThan(fragment.indexOf('vec3 outgoingLight = totalDiffuse + totalSpecular + totalEmissiveRadiance;'));
    expect(gem).toBeLessThan(fragment.indexOf('gl_FragColor = vec4( outgoingLight, diffuseColor.a );'));
    expect(gem).toBeLessThan(fragment.lastIndexOf('#ifdef USE_FOG'));
    // The environment lookup uses the material's own envMap uniforms (declared before it).
    expect(fragment.indexOf('uniform sampler2D envMap;')).toBeLessThan(fragment.indexOf('vec3 gsGemEnv(vec3 d)'));
    expect(c.uniforms.uGemIor.value).toBe(2.42);
    expect(c.uniforms.uGemFacets.value).toBe(8);
    expect(c.uniforms.uGemCutSurface.value).toBe(false); // a bool field: a uniform, the same program either way
  });

  it('turns off screen-space transmission on a physical material', async () => {
    const glass = new MeshPhysicalMaterial({ name: 'Glass', transmission: 1 });
    const c = await compilePatched('Gem', { material: glass });
    expect((c.material as MeshPhysicalMaterial).transmission).toBe(0);
    expect(glass.transmission).toBe(1);
  });

  it('works on instanced meshes (the internal cut turns with each instance)', async () => {
    const instanced = await compilePatched('Gem', { instanced: true });
    expect(instanced.vertexShader).toContain('gsM = gsM * mat3(instanceMatrix);');
    expect(instanced.edits.every((e) => e.matched)).toBe(true);
  });

  it('takes live values', async () => {
    const c = await compilePatched('Gem');
    c.shaders.set(PREVIEW_MATERIAL, { ior: 1.77, dispersion: 0.2 });
    expect(c.uniforms.uGemIor.value).toBe(1.77);
    c.shaders.set(c.entity, { sparkle: 3 });
    const own = c.compile(c.mesh.material as MeshStandardMaterial);
    expect(own.uniforms.uGemSparkle.value).toBe(3);
    expect(c.uniforms.uGemSparkle.value).toBe(1);
    expect(own.fragmentShader).toBe(c.fragmentShader);
    expect(c.warnings).toEqual([]);
  });

  it('fails (keeping the Blender look) on an unlit material', async () => {
    await expect(compilePatched('Gem', { material: new MeshBasicMaterial({ name: 'Flat' }) })).rejects.toThrow(
      'Gem needs a lit (Principled BSDF) material',
    );
  });
});
