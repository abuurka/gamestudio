// Lava: the patch with the defaults and instanced, the world and uv mappings as separate programs,
// its own glow replacing Blender's emission, and live values through ctx.shaders.
import { Color, MeshBasicMaterial, MeshStandardMaterial } from 'three';
import { describe, expect, it } from 'vitest';
import { getShader } from '../../src/schema';
import { compilePatched, PREVIEW_MATERIAL, resolveIncludes } from './helpers';

describe('Lava', () => {
  it('is a built-in Water shader', () => {
    expect(getShader('Lava')).toMatchObject({ builtin: true, category: 'Water' });
  });

  it('patches the standard material with the defaults (world mapping)', async () => {
    const c = await compilePatched('Lava', { mesh: 'plane' });
    expect(c.warnings).toEqual([]);
    expect(c.edits.every((e) => e.matched)).toBe(true);
    expect(c.material.customProgramCacheKey()).toMatch(/\|gs-lava$/);
    expect(c.fragmentShader).not.toContain('#define LV_UV');
    const fragment = resolveIncludes(c.fragmentShader);
    // The glow goes into the emissive light; the crust's colour and roughness come before the lighting reads them.
    expect(fragment.indexOf('totalEmissiveRadiance += lvGlow;')).toBeLessThan(fragment.indexOf('vec3 outgoingLight ='));
    expect(fragment.indexOf('roughnessFactor = mix(0.85, 0.4, lvOpen);')).toBeLessThan(fragment.indexOf('material.roughness = max( roughnessFactor, 0.0525 );'));
    expect(c.uniforms.lvColor.value.toArray()).toEqual([1, 0.3, 0.05, 1]);
    expect(c.uniforms.lvFlow.value.toArray()).toEqual([0.25, 0]);
    expect(c.uniforms.lvTime).toBe(c.time);
  });

  it('flows along the UVs as another program', async () => {
    const c = await compilePatched('Lava', { mesh: 'plane', values: { mapping: 'uv', flow: [0, 0.2] } });
    expect(c.material.customProgramCacheKey()).toMatch(/\|gs-lava-uv$/);
    expect(c.fragmentShader).toContain('#define LV_UV');
    expect(c.edits.every((e) => e.matched)).toBe(true);
  });

  it('replaces Blender’s emission with its own glow', async () => {
    const material = new MeshStandardMaterial({ name: 'GS_Lava', emissive: new Color(1, 0.3, 0) });
    const c = await compilePatched('Lava', { material });
    expect((c.material as MeshStandardMaterial).emissive.getHex()).toBe(0);
    expect(material.emissive.getHex()).not.toBe(0); // the loaded material is untouched
  });

  it('works on instanced meshes', async () => {
    const c = await compilePatched('Lava', { mesh: 'plane', instanced: true });
    expect(c.vertexShader).toContain('lvPos = instanceMatrix * lvPos;');
  });

  it('fails (keeping the Blender look) on a material that is not a MeshStandardMaterial', async () => {
    await expect(compilePatched('Lava', { material: new MeshBasicMaterial({ name: 'Flat' }) })).rejects.toThrow(
      'Game shader "Lava" failed on material "Flat": Lava needs a Principled BSDF material',
    );
  });

  it('takes live values; a mapping change is a new program', async () => {
    const c = await compilePatched('Lava', { mesh: 'plane' });
    c.shaders.set(PREVIEW_MATERIAL, { heat: 0.9, flow: [0, 0.5] });
    expect(c.uniforms.lvHeat.value).toBe(0.9);
    expect(c.uniforms.lvFlow.value.toArray()).toEqual([0, 0.5]);
    c.shaders.set(c.entity, { brightness: 3 });
    const own = c.compile(c.mesh.material as MeshStandardMaterial);
    expect(own.uniforms.lvBrightness.value).toBe(3);
    expect(c.uniforms.lvBrightness.value).toBe(1);
    expect(own.fragmentShader).toBe(c.fragmentShader);
    c.shaders.set(c.entity, { mapping: 'uv' });
    const uv = c.mesh.material as MeshStandardMaterial;
    expect(uv.customProgramCacheKey()).toMatch(/\|gs-lava-uv$/);
    expect(c.compile(uv).uniforms.lvBrightness.value).toBe(3);
    expect(c.warnings).toEqual([]);
  });
});
