// Caustics: the patch with the defaults, instanced and skinned, the caustic light inside every direct
// light, the baked tile (shared, tileable, deterministic), and live values through ctx.shaders.
import { MeshBasicMaterial, RedFormat, RepeatWrapping, type DataTexture, type MeshStandardMaterial } from 'three';
import { describe, expect, it } from 'vitest';
import { getShader } from '../../src/schema';
import { compilePatched, PREVIEW_MATERIAL, resolveIncludes } from './helpers';

describe('Caustics', () => {
  it('is a built-in Water shader', () => {
    expect(getShader('Caustics')).toMatchObject({ builtin: true, category: 'Water' });
  });

  it('adds caustic light to every direct light of the standard material', async () => {
    const c = await compilePatched('Caustics', { mesh: 'plane' });
    expect(c.warnings).toEqual([]);
    expect(c.edits.every((e) => e.matched)).toBe(true);
    expect(c.material.customProgramCacheKey()).toMatch(/\|gs-caustics$/);
    const fragment = resolveIncludes(c.fragmentShader);
    // RE_Direct is redefined after the physical lighting functions and before the lights loop uses it.
    const redefined = fragment.indexOf('#define RE_Direct caDirect');
    expect(redefined).toBeGreaterThan(fragment.indexOf('#define RE_Direct\t\t\t\tRE_Direct_Physical'));
    expect(redefined).toBeLessThan(fragment.indexOf('RE_Direct( directLight, geometryPosition'));
    expect(c.uniforms.caTime).toBe(c.time);
    expect(c.uniforms.caColor.value.toArray()).toEqual([0.75, 0.95, 1, 1]);
    expect(c.uniforms.caScale.value).toBe(1);
  });

  it('bakes one tileable caustic texture, shared by every material', async () => {
    const a = await compilePatched('Caustics');
    const b = await compilePatched('Caustics', { mesh: 'sphere' });
    const tile = a.uniforms.caMap.value as DataTexture;
    expect(b.uniforms.caMap.value).toBe(tile);
    expect([tile.name, tile.format, tile.wrapS, tile.wrapT, tile.image.width]).toEqual(['gs_caustics', RedFormat, RepeatWrapping, RepeatWrapping, 256]);
    const data = tile.image.data as Uint8Array;
    // Bright lines on dark cells, and the opposite edges meet (it tiles).
    const mean = data.reduce((s, v) => s + v, 0) / data.length;
    expect(mean).toBeGreaterThan(20);
    expect(mean).toBeLessThan(140);
    expect(Math.max(...data)).toBeGreaterThan(200);
    let seam = 0;
    for (let i = 0; i < 256; i++) seam += Math.abs(data[i * 256]! - data[i * 256 + 255]!) + Math.abs(data[i]! - data[255 * 256 + i]!);
    expect(seam / 512).toBeLessThan(30);
  });

  it('works on instanced and skinned meshes', async () => {
    const instanced = await compilePatched('Caustics', { instanced: true });
    expect(instanced.mesh.isInstancedMesh).toBe(true);
    expect(instanced.vertexShader).toContain('caPos = instanceMatrix * caPos;');
    const skinned = await compilePatched('Caustics', { skinned: true, mesh: 'character' });
    expect(skinned.mesh.isSkinnedMesh).toBe(true);
    const vertex = resolveIncludes(skinned.vertexShader);
    expect(vertex.indexOf('transformed = ( bindMatrixInverse * skinned ).xyz;')).toBeLessThan(vertex.indexOf('vec4 caPos = vec4(transformed, 1.0);'));
  });

  it('fails (keeping the Blender look) on a material that is not a MeshStandardMaterial', async () => {
    await expect(compilePatched('Caustics', { material: new MeshBasicMaterial({ name: 'Flat' }) })).rejects.toThrow(
      'Game shader "Caustics" failed on material "Flat": Caustics needs a Principled BSDF material',
    );
  });

  it('takes live values', async () => {
    const c = await compilePatched('Caustics');
    c.shaders.set(PREVIEW_MATERIAL, { strength: 2, color: [1, 0.5, 0.2, 1] });
    expect(c.uniforms.caStrength.value).toBe(2);
    expect(c.uniforms.caColor.value.toArray()).toEqual([1, 0.5, 0.2, 1]);
    c.shaders.set(c.entity, { strength: 0 });
    const own = c.compile(c.mesh.material as MeshStandardMaterial);
    expect(own.uniforms.caStrength.value).toBe(0);
    expect(c.uniforms.caStrength.value).toBe(2);
    expect(own.fragmentShader).toBe(c.fragmentShader);
    expect(c.warnings).toEqual([]);
  });
});
