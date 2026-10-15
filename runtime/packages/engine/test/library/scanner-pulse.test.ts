// ScannerPulse: the patch with the defaults, instanced and skinned (world position after skinning), and a
// pulse triggered from code: origin and startAt by material name or per entity.
import { MeshBasicMaterial, type MeshStandardMaterial } from 'three';
import { describe, expect, it } from 'vitest';
import { getShader } from '../../src/schema';
import { compilePatched, PREVIEW_MATERIAL, resolveIncludes } from './helpers';

describe('ScannerPulse', () => {
  it('is a built-in Magic & Sci-fi shader', () => {
    expect(getShader('ScannerPulse')).toMatchObject({ builtin: true, category: 'Magic & Sci-fi' });
  });

  it('patches the standard material: world position in the vertex shader, the pulse as emission', async () => {
    const c = await compilePatched('ScannerPulse', { mesh: 'plane' });
    expect(c.warnings).toEqual([]);
    expect(c.material.customProgramCacheKey()).toMatch(/\|gs-scanner-pulse$/);
    expect(c.edits.every((e) => e.matched)).toBe(true);
    expect(c.vertexShader).toContain('vGsScanWorld = (modelMatrix * gsScanWorld).xyz;');
    expect(c.fragmentShader).toContain('totalEmissiveRadiance += light * near;');
    expect(c.uniforms.gsScanOrigin.value.toArray()).toEqual([0, 0, 0]);
    expect(c.uniforms.gsScanInterval.value).toBe(4);
    expect(c.uniforms.gsScanReach.value).toBe(30);
    expect(c.uniforms.gsScanTime).toBe(c.time);
  });

  it('works on instanced and skinned meshes', async () => {
    const instanced = await compilePatched('ScannerPulse', { instanced: true });
    expect(instanced.vertexShader).toContain('gsScanWorld = instanceMatrix * gsScanWorld;');
    const skinned = await compilePatched('ScannerPulse', { skinned: true, mesh: 'character' });
    const vertex = resolveIncludes(skinned.vertexShader);
    expect(vertex.indexOf('transformed = ( bindMatrixInverse * skinned ).xyz;')).toBeLessThan(vertex.indexOf('vec4 gsScanWorld = vec4(transformed, 1.0);'));
  });

  it('is triggered from code: origin and startAt on the material everywhere, or on one entity', async () => {
    const c = await compilePatched('ScannerPulse', { values: { interval: 0 } });
    const origin = c.uniforms.gsScanOrigin;
    c.shaders.set(PREVIEW_MATERIAL, { origin: [4, 0, -2], startAt: 12.5 });
    expect(origin.value.toArray()).toEqual([4, 0, -2]); // the same Vector3, changed in place
    expect(c.uniforms.gsScanStartAt.value).toBe(12.5);
    c.shaders.set(c.entity, { startAt: 20, color: [1, 0.3, 0.1, 1] });
    const own = c.mesh.material as MeshStandardMaterial;
    expect(own).not.toBe(c.material);
    const o = c.compile(own);
    expect(o.uniforms.gsScanStartAt.value).toBe(20);
    expect(o.uniforms.gsScanOrigin.value.toArray()).toEqual([4, 0, -2]);
    expect(o.uniforms.gsScanColor.value.toArray()).toEqual([1, 0.3, 0.1, 1]);
    expect(c.uniforms.gsScanStartAt.value).toBe(12.5);
    expect(o.fragmentShader).toBe(c.fragmentShader);
    expect(c.warnings).toEqual([]);
  });

  it('fails (keeping the Blender look) on a material that is not a MeshStandardMaterial', async () => {
    await expect(compilePatched('ScannerPulse', { material: new MeshBasicMaterial({ name: 'Flat' }) })).rejects.toThrow(
      'Game shader "ScannerPulse" failed on material "Flat": ScannerPulse needs a Principled BSDF material',
    );
  });
});
