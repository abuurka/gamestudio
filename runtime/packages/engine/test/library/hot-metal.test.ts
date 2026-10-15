// HotMetal: the patch with the defaults, instanced and skinned, and heat driven from code: heat per entity
// and a quench triggered with coolAt.
import { MeshBasicMaterial, type MeshStandardMaterial } from 'three';
import { describe, expect, it } from 'vitest';
import { getShader } from '../../src/schema';
import { compilePatched, PREVIEW_MATERIAL, resolveIncludes } from './helpers';

describe('HotMetal', () => {
  it('is a built-in Magic & Sci-fi shader', () => {
    expect(getShader('HotMetal')).toMatchObject({ builtin: true, category: 'Magic & Sci-fi' });
  });

  it('patches the standard material: dark hot surface, rough crust, blackbody emission', async () => {
    const c = await compilePatched('HotMetal');
    expect(c.warnings).toEqual([]);
    expect(c.material.customProgramCacheKey()).toMatch(/\|gs-hot-metal$/);
    expect(c.edits.every((e) => e.matched)).toBe(true);
    expect(c.fragmentShader).toContain('vec3 gsHotBlackbody(float t)');
    expect(c.fragmentShader).toContain('totalEmissiveRadiance += gsHotLight;');
    expect(c.fragmentShader).toContain('roughnessFactor = mix(roughnessFactor, gsHotRough');
    expect(c.uniforms.gsHotHeat.value).toBe(0.7);
    expect(c.uniforms.gsHotCoolAt.value).toBe(-1);
    expect(c.uniforms.gsHotTime).toBe(c.time);
  });

  it('works on instanced and skinned meshes', async () => {
    const instanced = await compilePatched('HotMetal', { instanced: true });
    expect(instanced.vertexShader).toContain('vGsHotPos += instanceMatrix[3].xyz * 0.613;');
    const skinned = await compilePatched('HotMetal', { skinned: true, mesh: 'character' });
    const vertex = resolveIncludes(skinned.vertexShader);
    expect(vertex.indexOf('vGsHotPos = position;')).toBeLessThan(vertex.indexOf('transformed = ( bindMatrixInverse * skinned ).xyz;'));
  });

  it('takes heat per entity, and a quench from coolAt', async () => {
    const c = await compilePatched('HotMetal', { values: { heat: 0 } });
    c.shaders.set(PREVIEW_MATERIAL, { crust: 0.9 });
    expect(c.uniforms.gsHotCrust.value).toBe(0.9);
    c.shaders.set(c.entity, { heat: 1 });
    const own = c.mesh.material as MeshStandardMaterial;
    expect(own).not.toBe(c.material);
    const o = c.compile(own);
    expect(o.uniforms.gsHotHeat.value).toBe(1);
    expect(o.uniforms.gsHotCrust.value).toBe(0.9);
    expect(c.uniforms.gsHotHeat.value).toBe(0);
    c.time.value = 7;
    c.shaders.set(c.entity, { coolAt: c.time.value, coolTime: 3 });
    expect(o.uniforms.gsHotCoolAt.value).toBe(7);
    expect(o.uniforms.gsHotCoolTime.value).toBe(3);
    expect(o.fragmentShader).toBe(c.fragmentShader);
    expect(c.warnings).toEqual([]);
  });

  it('fails (keeping the Blender look) on a material that is not a MeshStandardMaterial', async () => {
    await expect(compilePatched('HotMetal', { material: new MeshBasicMaterial({ name: 'Flat' }) })).rejects.toThrow(
      'Game shader "HotMetal" failed on material "Flat": HotMetal needs a Principled BSDF material',
    );
  });
});
