// CarPaint: a MeshPhysicalMaterial copy of the Blender material (three's clear coat) with colour flip
// and flakes patched in.
import { Color, DataTexture, MeshBasicMaterial, MeshPhysicalMaterial, MeshStandardMaterial } from 'three';
import { describe, expect, it } from 'vitest';
import { getShader, patchShader } from '../../src/schema';
import { compilePatched, PREVIEW_MATERIAL, resolveIncludes } from './helpers';

describe('CarPaint', () => {
  it('is a built-in Surfaces shader', () => {
    expect(getShader('CarPaint')).toMatchObject({ builtin: true, category: 'Surfaces' });
  });

  it('becomes a physical copy of the material with a clear coat, keeping its colour, roughness and maps', async () => {
    const map = new DataTexture(new Uint8Array(4).fill(200), 1, 1);
    const paint = new MeshStandardMaterial({ name: 'Paint', color: new Color(0.8, 0.05, 0.02), roughness: 0.35, metalness: 0.4, map });
    const c = await compilePatched('CarPaint', { mesh: 'sphere', material: paint });
    expect(c.warnings).toEqual([]);
    const physical = c.material as MeshPhysicalMaterial;
    expect(physical.isMeshPhysicalMaterial).toBe(true);
    expect(physical.name).toBe('Paint');
    expect(physical.color.toArray()).toEqual(paint.color.toArray());
    expect([physical.roughness, physical.metalness, physical.map]).toEqual([0.35, 0.4, map]);
    expect(physical.clearcoat).toBeGreaterThan(0);
    expect(physical.defines).toMatchObject({ STANDARD: '', PHYSICAL: '' });
    expect(paint.type).toBe('MeshStandardMaterial'); // the loaded material is untouched
    expect(c.edits.every((e) => e.matched)).toBe(true);
    expect(c.material.customProgramCacheKey()).toMatch(/\|gs_car_paint$/);
    const fragment = resolveIncludes(c.fragmentShader);
    expect(fragment).toContain('#define RE_Direct RE_Direct_CarPaint');
    expect(fragment.indexOf('material.clearcoat = saturate(uPaintCoat);')).toBeGreaterThan(fragment.indexOf('material.clearcoat = clearcoat;'));
    expect(c.uniforms.uPaintFlip.value).toBe(0.08);
    expect(c.uniforms.uPaintCoat.value).toBe(1);
  });

  it('keeps patches made before it (the lightmap patch) on the physical copy', async () => {
    const paint = new MeshStandardMaterial({ name: 'Paint' });
    patchShader(paint, 'gs_lightmap', (shader) => {
      shader.fragmentShader = shader.fragmentShader.replace('#include <lights_fragment_maps>', '#include <lights_fragment_maps>\n// lightmapped');
    });
    const c = await compilePatched('CarPaint', { material: paint });
    expect(c.material.customProgramCacheKey()).toMatch(/\|gs_lightmap\|gs_car_paint$/);
    expect(c.fragmentShader).toContain('// lightmapped');
  });

  it('works on instanced and skinned meshes', async () => {
    const instanced = await compilePatched('CarPaint', { instanced: true });
    expect(instanced.vertexShader).toContain('vGsPaintP += instanceMatrix[3].xyz * 1.37;');
    const skinned = await compilePatched('CarPaint', { skinned: true, mesh: 'character' });
    expect(skinned.material.type).toBe('MeshPhysicalMaterial');
    expect(skinned.edits.every((e) => e.matched)).toBe(true);
  });

  it('takes live values, the clear coat included', async () => {
    const c = await compilePatched('CarPaint');
    c.shaders.set(PREVIEW_MATERIAL, { clearcoat: 0.3, flakes: 2 });
    expect(c.uniforms.uPaintCoat.value).toBe(0.3);
    expect(c.uniforms.uPaintFlakes.value).toBe(2);
    c.shaders.set(c.entity, { flip: -0.2 });
    const ownMaterial = c.mesh.material as MeshPhysicalMaterial;
    expect(ownMaterial).not.toBe(c.material);
    expect(ownMaterial.isMeshPhysicalMaterial).toBe(true);
    const own = c.compile(ownMaterial);
    expect(own.uniforms.uPaintFlip.value).toBe(-0.2);
    expect(c.uniforms.uPaintFlip.value).toBe(0.08);
    expect(own.fragmentShader).toBe(c.fragmentShader);
    expect(c.warnings).toEqual([]);
  });

  it('fails (keeping the Blender look) on an unlit material', async () => {
    await expect(compilePatched('CarPaint', { material: new MeshBasicMaterial({ name: 'Flat' }) })).rejects.toThrow(
      'CarPaint needs a lit (Principled BSDF) material',
    );
  });
});
