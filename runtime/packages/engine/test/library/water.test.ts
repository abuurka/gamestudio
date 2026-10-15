// Water: the patch with the defaults and instanced, the sky reflection replacing the environment's,
// an unlit export turned into a lit material, no shadow cast, and live values through ctx.shaders.
import { Color, MeshBasicMaterial, type MeshStandardMaterial } from 'three';
import { describe, expect, it } from 'vitest';
import { getShader } from '../../src/schema';
import { compilePatched, PREVIEW_MATERIAL, resolveIncludes } from './helpers';

describe('Water', () => {
  it('is a built-in Water shader', () => {
    expect(getShader('Water')).toMatchObject({ builtin: true, category: 'Water' });
  });

  it('patches the standard material with the defaults', async () => {
    const c = await compilePatched('Water', { mesh: 'plane' });
    expect(c.warnings).toEqual([]);
    expect(c.edits.every((e) => e.matched)).toBe(true);
    expect(c.material).not.toBe(c.original);
    expect(c.material.customProgramCacheKey()).toMatch(/\|gs-water$/);
    expect(c.vertexShader).toContain('vWaWorld = (modelMatrix * waPos).xyz;');
    // The waves bend the normal after the normal maps; the sky replaces the environment's reflection.
    const fragment = resolveIncludes(c.fragmentShader);
    expect(fragment.indexOf('normal = normalize((viewMatrix * vec4(waNormal, 0.0)).xyz);')).toBeGreaterThan(fragment.indexOf('vec3 nonPerturbedNormal = normal;'));
    expect(fragment.indexOf('radiance = waSky(waR) * waReflection;')).toBeLessThan(fragment.indexOf('RE_IndirectSpecular( radiance,'));
    // The sky it reflects is the scene's own (ctx.sky), the same uniform objects every shader gets.
    expect(fragment).toContain('vec3 waSky(vec3 direction)');
    expect([c.uniforms.waSkyZenith, c.uniforms.waSkyHorizon, c.uniforms.waSkyGround]).toEqual([c.sky.zenith, c.sky.horizon, c.sky.ground]);
    expect(c.uniforms.waSkyZenith).toBe(c.sky.zenith);
    expect(c.uniforms.waLength.value).toBe(4);
    expect(c.uniforms.waChop.value).toBe(0.6);
    expect(c.uniforms.waFoam.value).toBe(0.2);
    expect(c.uniforms.waTime).toBe(c.time); // the shared game time, not a copy
    expect((c.material as MeshStandardMaterial).roughness).toBe(0.06);
    expect(c.mesh.castShadow).toBe(false);
  });

  it('works on instanced meshes', async () => {
    const c = await compilePatched('Water', { mesh: 'plane', instanced: true });
    expect(c.mesh.isInstancedMesh).toBe(true);
    expect(c.vertexShader).toContain('waPos = instanceMatrix * waPos;');
  });

  it('turns an unlit export into a lit water material with its colour and alpha', async () => {
    const flat = new MeshBasicMaterial({ name: 'GS_Water', color: new Color(0.1, 0.3, 0.6), transparent: true, opacity: 0.7 });
    const c = await compilePatched('Water', { material: flat });
    const water = c.material as MeshStandardMaterial;
    expect(water.isMeshStandardMaterial).toBe(true);
    expect(water.name).toBe('GS_Water');
    expect(water.color.toArray()).toEqual(flat.color.toArray());
    expect([water.transparent, water.opacity]).toEqual([true, 0.7]);
    expect(c.edits.every((e) => e.matched)).toBe(true);
  });

  it('takes live values', async () => {
    const c = await compilePatched('Water', { mesh: 'plane' });
    c.shaders.set(PREVIEW_MATERIAL, { waveLength: 12, foam: 0.6, direction: 1 });
    expect([c.uniforms.waLength.value, c.uniforms.waFoam.value, c.uniforms.waDirection.value]).toEqual([12, 0.6, 1]);
    c.shaders.set(c.entity, { choppiness: 1.5 });
    const own = c.compile(c.mesh.material as MeshStandardMaterial);
    expect(own.uniforms.waChop.value).toBe(1.5);
    expect(c.uniforms.waChop.value).toBe(0.6);
    expect(own.fragmentShader).toBe(c.fragmentShader);
    expect(c.warnings).toEqual([]);
  });
});
