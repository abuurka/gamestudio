// InteriorMapping: rooms behind a window grid (or a mask) on the standard material.
import { MeshBasicMaterial, MeshStandardMaterial, NoColorSpace, type Texture } from 'three';
import { describe, expect, it } from 'vitest';
import { getShader } from '../../src/schema';
import { compilePatched, PREVIEW_MATERIAL, resolveIncludes } from './helpers';

describe('InteriorMapping', () => {
  it('is a built-in Surfaces shader', () => {
    expect(getShader('InteriorMapping')).toMatchObject({ builtin: true, category: 'Surfaces' });
  });

  it('patches the standard material with a window grid by default', async () => {
    const c = await compilePatched('InteriorMapping');
    expect(c.warnings).toEqual([]);
    expect(c.edits.every((e) => e.matched)).toBe(true);
    expect(c.material.customProgramCacheKey()).toMatch(/\|gs_interior_mapping_windows$/);
    expect(c.fragmentShader).not.toContain('#define GS_IM_MASK');
    const fragment = resolveIncludes(c.fragmentShader);
    // Glass and frames change the surface before three builds its material from it.
    const surface = fragment.indexOf('roughnessFactor = mix(roughnessFactor, 0.03, gsImGlass);');
    expect(surface).toBeGreaterThan(fragment.indexOf('float metalnessFactor = metalness;'));
    expect(surface).toBeLessThan(fragment.indexOf('PhysicalMaterial material;'));
    expect(c.uniforms.uImSpacing.value.toArray()).toEqual([3, 3]);
    expect(c.uniforms.uImFloors.value).toBe(0);
    expect(c.uniforms.uGsTime).toBe(c.time);
    expect((c.uniforms.uImMask.value as Texture).name).toBe('gs_white');
    // The grid's v runs up the wall whichever way the UVs were stored (glTF flips v).
    expect(c.fragmentShader).toContain('vec2 uvG = vec2(vGsImUv.x, flipV ? 1.0 - vGsImUv.y : vGsImUv.y);');
  });

  it('compiles a program per ground floor style, and one for a mask (loaded as linear data)', async () => {
    const shop = await compilePatched('InteriorMapping', { values: { groundFloor: 'shopfront' } });
    expect(shop.fragmentShader).toContain('#define GS_IM_SHOPFRONT');
    expect(shop.material.customProgramCacheKey()).toMatch(/\|gs_interior_mapping_shopfront$/);
    const wall = await compilePatched('InteriorMapping', { values: { groundFloor: 'wall' } });
    expect(wall.fragmentShader).toContain('#define GS_IM_GROUND_WALL');
    const masked = await compilePatched('InteriorMapping', { values: { mask: 'textures/windows.png' } });
    expect(masked.fragmentShader).toContain('#define GS_IM_MASK');
    expect(masked.material.customProgramCacheKey()).toMatch(/\|gs_interior_mapping_mask_windows$/);
    const mask = masked.uniforms.uImMask.value as Texture;
    expect(mask.name).toBe('textures/windows.png');
    expect(mask.colorSpace).toBe(NoColorSpace);
  });

  it('works on instanced and skinned meshes', async () => {
    const instanced = await compilePatched('InteriorMapping', { instanced: true });
    expect(instanced.vertexShader).toContain('gsW = instanceMatrix * gsW;');
    const skinned = await compilePatched('InteriorMapping', { skinned: true, mesh: 'character' });
    const vertex = resolveIncludes(skinned.vertexShader);
    expect(vertex.indexOf('transformed = ( bindMatrixInverse * skinned ).xyz;')).toBeLessThan(vertex.indexOf('vec4 gsW = vec4(transformed, 1.0);'));
  });

  it('takes live values', async () => {
    const c = await compilePatched('InteriorMapping');
    c.shaders.set(PREVIEW_MATERIAL, { lit: 0.2, spacing: [2.5, 3.2] });
    expect(c.uniforms.uImLit.value).toBe(0.2);
    expect(c.uniforms.uImSpacing.value.toArray()).toEqual([2.5, 3.2]);
    c.shaders.set(c.entity, { brightness: 4 });
    const own = c.compile(c.mesh.material as MeshStandardMaterial);
    expect(own.uniforms.uImBrightness.value).toBe(4);
    expect(c.uniforms.uImBrightness.value).toBe(1);
    expect(own.fragmentShader).toBe(c.fragmentShader);
    expect(c.warnings).toEqual([]);
  });

  it('fails (keeping the Blender look) on an unlit material', async () => {
    await expect(compilePatched('InteriorMapping', { material: new MeshBasicMaterial({ name: 'Flat' }) })).rejects.toThrow(
      'InteriorMapping needs a lit (Principled BSDF) material',
    );
  });
});
