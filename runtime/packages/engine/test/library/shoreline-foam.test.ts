// ShorelineFoam: the see-through patch with the defaults and instanced, drawn after the water, and
// live values through ctx.shaders.
import { MeshBasicMaterial, type MeshStandardMaterial } from 'three';
import { describe, expect, it } from 'vitest';
import { getShader } from '../../src/schema';
import { compilePatched, PREVIEW_MATERIAL } from './helpers';

describe('ShorelineFoam', () => {
  it('is a built-in Water shader', () => {
    expect(getShader('ShorelineFoam')).toMatchObject({ builtin: true, category: 'Water' });
  });

  it('makes the material see-through foam, drawn after the water', async () => {
    const c = await compilePatched('ShorelineFoam', { mesh: 'plane' });
    expect(c.warnings).toEqual([]);
    expect(c.edits.every((e) => e.matched)).toBe(true);
    expect(c.material.customProgramCacheKey()).toMatch(/\|gs-shoreline-foam$/);
    const foam = c.material as MeshStandardMaterial;
    expect([foam.transparent, foam.depthWrite, foam.polygonOffset]).toEqual([true, false, true]);
    expect(c.original.transparent).toBe(false); // the loaded material is untouched
    expect(c.vertexShader).toContain('vSfUv = uv;');
    expect(c.fragmentShader).toContain('float u = vSfUv.x * sfRepeat, v = 1.0 - vSfUv.y, t = sfTime * sfSpeed;');
    expect(c.uniforms.sfWash.value).toBe(0.34);
    expect(c.uniforms.sfTime).toBe(c.time);
    expect(c.mesh.renderOrder).toBe(1);
    expect(c.mesh.castShadow).toBe(false);
  });

  it('works on instanced meshes and unlit exports', async () => {
    const instanced = await compilePatched('ShorelineFoam', { mesh: 'plane', instanced: true });
    expect(instanced.mesh.isInstancedMesh).toBe(true);
    const unlit = await compilePatched('ShorelineFoam', { material: new MeshBasicMaterial({ name: 'Sahan_Foam' }) });
    expect((unlit.material as MeshStandardMaterial).isMeshStandardMaterial).toBe(true);
    expect(unlit.material.name).toBe('Sahan_Foam');
    expect(unlit.edits.every((e) => e.matched)).toBe(true);
  });

  it('takes live values', async () => {
    const c = await compilePatched('ShorelineFoam', { mesh: 'plane' });
    c.shaders.set(PREVIEW_MATERIAL, { speed: 2, repeat: 4 });
    expect([c.uniforms.sfSpeed.value, c.uniforms.sfRepeat.value]).toEqual([2, 4]);
    c.shaders.set(c.entity, { opacity: 0.5 });
    const own = c.compile(c.mesh.material as MeshStandardMaterial);
    expect(own.uniforms.sfOpacity.value).toBe(0.5);
    expect(c.uniforms.sfOpacity.value).toBe(0.92);
    expect(c.warnings).toEqual([]);
  });
});
