// Flow: the patch with the defaults and instanced, gaps only on see-through materials, a clear error
// on a material it cannot build on, and live values through ctx.shaders.
import { MeshBasicMaterial, MeshStandardMaterial } from 'three';
import { describe, expect, it } from 'vitest';
import { getShader } from '../../src/schema';
import { compilePatched, PREVIEW_MATERIAL, resolveIncludes } from './helpers';

describe('Flow', () => {
  it('is a built-in Water shader', () => {
    expect(getShader('Flow')).toMatchObject({ builtin: true, category: 'Water' });
  });

  it('patches the standard material with the defaults', async () => {
    const c = await compilePatched('Flow', { mesh: 'cylinder' });
    expect(c.warnings).toEqual([]);
    expect(c.edits.every((e) => e.matched)).toBe(true);
    expect(c.material.customProgramCacheKey()).toMatch(/\|gs-flow$/);
    expect(c.vertexShader).toContain('vFlUv = uv;');
    const fragment = resolveIncludes(c.fragmentShader);
    // Streaks scroll along V; the colour is set before the lighting reads it.
    expect(fragment).toContain('vec2 q = vec2(vFlUv.x, (vFlUv.y - flTime * flSpeed) / max(flStretch, 0.05)) * flScale;');
    expect(fragment.indexOf('flWhite = smoothstep(')).toBeLessThan(fragment.indexOf('material.diffuseColor = diffuseColor.rgb;'));
    expect(fragment.indexOf('normal = flBump(-vViewPosition, normal, flHeight);')).toBeGreaterThan(fragment.indexOf('vec3 nonPerturbedNormal = normal;'));
    expect([c.uniforms.flSpeed.value, c.uniforms.flScale.value, c.uniforms.flStretch.value]).toEqual([0.5, 10, 6]);
    expect(c.uniforms.flTime).toBe(c.time);
  });

  it('works on instanced meshes; see-through gaps only where the material blends', async () => {
    const instanced = await compilePatched('Flow', { mesh: 'plane', instanced: true });
    expect(instanced.mesh.isInstancedMesh).toBe(true);
    const sheet = await compilePatched('Flow', { material: new MeshStandardMaterial({ name: 'Falls', transparent: true }) });
    expect(sheet.material.transparent).toBe(true);
    expect(sheet.fragmentShader).toContain('#ifndef OPAQUE\n    if (diffuseColor.a < 0.003) discard;');
  });

  it('fails (keeping the Blender look) on a material that is not a MeshStandardMaterial', async () => {
    await expect(compilePatched('Flow', { material: new MeshBasicMaterial({ name: 'Flat' }) })).rejects.toThrow(
      'Game shader "Flow" failed on material "Flat": Flow needs a Principled BSDF material',
    );
  });

  it('takes live values', async () => {
    const c = await compilePatched('Flow', { mesh: 'plane' });
    c.shaders.set(PREVIEW_MATERIAL, { speed: -1, foam: 0.9 });
    expect([c.uniforms.flSpeed.value, c.uniforms.flFoam.value]).toEqual([-1, 0.9]);
    c.shaders.set(c.entity, { breakup: 0.8 });
    const own = c.compile(c.mesh.material as MeshStandardMaterial);
    expect(own.uniforms.flBreakup.value).toBe(0.8);
    expect(c.uniforms.flBreakup.value).toBe(0.25);
    expect(own.fragmentShader).toBe(c.fragmentShader);
    expect(c.warnings).toEqual([]);
  });
});
