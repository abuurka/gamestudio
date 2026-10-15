// WetSurface: the patch with the defaults, instanced and skinned, layered over the material's own maps
// and lighting, and live values through ctx.shaders (rain starting and stopping).
import { MeshBasicMaterial, MeshStandardMaterial, Texture } from 'three';
import { describe, expect, it } from 'vitest';
import { getShader } from '../../src/schema';
import { compilePatched, PREVIEW_MATERIAL, resolveIncludes } from './helpers';

describe('WetSurface', () => {
  it('is a built-in Water shader', () => {
    expect(getShader('WetSurface')).toMatchObject({ builtin: true, category: 'Water' });
  });

  it('patches the standard material with the defaults', async () => {
    const c = await compilePatched('WetSurface', { mesh: 'plane' });
    expect(c.warnings).toEqual([]);
    expect(c.edits.every((e) => e.matched)).toBe(true);
    expect(c.material.customProgramCacheKey()).toMatch(/\|gs-wet-surface$/);
    expect(c.vertexShader).toContain('vWsWorld = (modelMatrix * wsPos).xyz;');
    const fragment = resolveIncludes(c.fragmentShader);
    // Darkening and gloss land before the lighting reads the colour and roughness; puddles mirror the sky.
    expect(fragment.indexOf('roughnessFactor = mix(roughnessFactor, mix(min(roughnessFactor, 0.3), 0.03, wsPuddle), wsWet);')).toBeLessThan(
      fragment.indexOf('material.roughness = max( roughnessFactor, 0.0525 );'),
    );
    expect(fragment.indexOf('radiance = mix(radiance, wsSky(')).toBeLessThan(fragment.indexOf('RE_IndirectSpecular( radiance,'));
    expect([c.uniforms.wsWetness.value, c.uniforms.wsPuddles.value, c.uniforms.wsPuddleSize.value, c.uniforms.wsRain.value]).toEqual([1, 0.4, 3, 1]);
    expect(c.uniforms.wsTime).toBe(c.time);
  });

  it('keeps the material’s own maps', async () => {
    const map = new Texture();
    const normalMap = new Texture();
    const ground = new MeshStandardMaterial({ name: 'GS_Ground', map, normalMap, roughness: 0.9 });
    const c = await compilePatched('WetSurface', { material: ground });
    const wet = c.material as MeshStandardMaterial;
    expect([wet.map, wet.normalMap, wet.roughness]).toEqual([map, normalMap, 0.9]);
    expect(c.edits.every((e) => e.matched)).toBe(true);
  });

  it('works on instanced and skinned meshes', async () => {
    const instanced = await compilePatched('WetSurface', { instanced: true });
    expect(instanced.vertexShader).toContain('wsPos = instanceMatrix * wsPos;');
    const skinned = await compilePatched('WetSurface', { skinned: true, mesh: 'character' });
    expect(skinned.mesh.isSkinnedMesh).toBe(true);
    const vertex = resolveIncludes(skinned.vertexShader);
    expect(vertex.indexOf('transformed = ( bindMatrixInverse * skinned ).xyz;')).toBeLessThan(vertex.indexOf('vec4 wsPos = vec4(transformed, 1.0);'));
  });

  it('fails (keeping the Blender look) on a material that is not a MeshStandardMaterial', async () => {
    await expect(compilePatched('WetSurface', { material: new MeshBasicMaterial({ name: 'Flat' }) })).rejects.toThrow(
      'Game shader "WetSurface" failed on material "Flat": WetSurface needs a Principled BSDF material',
    );
  });

  it('takes live values: the ground dries everywhere, one entity stays soaked', async () => {
    const c = await compilePatched('WetSurface', { mesh: 'plane' });
    c.shaders.set(PREVIEW_MATERIAL, { wetness: 0.2, rain: 0 });
    expect([c.uniforms.wsWetness.value, c.uniforms.wsRain.value]).toEqual([0.2, 0]);
    c.shaders.set(c.entity, { wetness: 1 });
    const own = c.compile(c.mesh.material as MeshStandardMaterial);
    expect(own.uniforms.wsWetness.value).toBe(1);
    expect(c.uniforms.wsWetness.value).toBe(0.2);
    expect(own.fragmentShader).toBe(c.fragmentShader);
    expect(c.warnings).toEqual([]);
  });
});
