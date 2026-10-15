// Triplanar (the library's reference shader; copy this test's shape for a library shader): the patch
// with the defaults, instanced and skinned, its asset textures, and live values through ctx.shaders.
import { MeshBasicMaterial, MeshStandardMaterial, NoColorSpace, SRGBColorSpace, type Texture } from 'three';
import { describe, expect, it } from 'vitest';
import { getShader } from '../../src/schema';
import { compilePatched, PREVIEW_MATERIAL, resolveIncludes } from './helpers';

describe('Triplanar', () => {
  it('is a built-in Surfaces shader', () => {
    expect(getShader('Triplanar')).toMatchObject({ builtin: true, category: 'Surfaces' });
  });

  it('patches the standard material with the defaults; without scans only wetness changes it', async () => {
    const c = await compilePatched('Triplanar');
    expect(c.warnings).toEqual([]);
    expect(c.material).not.toBe(c.original); // a patched clone; the loaded material stays as it was
    expect(c.original.customProgramCacheKey()).not.toContain('gs-triplanar');
    expect(c.material.customProgramCacheKey()).toMatch(/\|gs-triplanar$/);
    expect(c.vertexShader).toContain('vTpPos = (modelMatrix * tpWp).xyz;');
    expect(c.fragmentShader).toContain('#include <map_fragment>\n');
    expect(c.fragmentShader).toContain('roughnessFactor = mix(roughnessFactor, 0.25, tpWetness());');
    expect(c.edits.every((e) => e.matched)).toBe(true);
    expect(c.uniforms.tpTile.value).toBe(3.2);
    expect(c.uniforms.tpWet.value).toBe(0);
    expect(c.uniforms.tpHas.value.toArray()).toEqual([0, 0, 0]);
    expect((c.uniforms.tpDiff.value as Texture).name).toBe('gs_white'); // samplers are never unbound
  });

  it('works on instanced and skinned meshes', async () => {
    const instanced = await compilePatched('Triplanar', { instanced: true });
    expect(instanced.mesh.isInstancedMesh).toBe(true);
    expect(instanced.vertexShader).toContain('tpWp = instanceMatrix * tpWp;');
    const skinned = await compilePatched('Triplanar', { skinned: true, mesh: 'character' });
    expect(skinned.mesh.isSkinnedMesh).toBe(true);
    // After worldpos_vertex: the skinned position (skinning_vertex comes before it).
    const vertex = resolveIncludes(skinned.vertexShader);
    expect(vertex.indexOf('transformed = ( bindMatrixInverse * skinned ).xyz;')).toBeLessThan(vertex.indexOf('vec4 tpWp = vec4(transformed, 1.0);'));
  });

  it('loads its scans as textures: the colour scan in sRGB, the data scans linear', async () => {
    const c = await compilePatched('Triplanar', { values: { diffuse: 'scans/d.webp', normal: 'scans/n.webp', rough: 'scans/r.webp', wetness: 0.4 } });
    const [diffuse, normal, rough] = ['tpDiff', 'tpNor', 'tpRgh'].map((k) => c.uniforms[k]!.value as Texture);
    expect([diffuse!.name, normal!.name, rough!.name]).toEqual(['scans/d.webp', 'scans/n.webp', 'scans/r.webp']);
    expect([diffuse!.colorSpace, normal!.colorSpace, rough!.colorSpace]).toEqual([SRGBColorSpace, NoColorSpace, NoColorSpace]);
    expect(diffuse!.flipY).toBe(false);
    expect(c.uniforms.tpHas.value.toArray()).toEqual([1, 1, 1]);
    expect(c.uniforms.tpWet.value).toBe(0.4);
  });

  it('takes live values: a material everywhere, or one entity with its own copy and the same program', async () => {
    const c = await compilePatched('Triplanar');
    const tile = c.uniforms.tpTile;
    c.shaders.set(PREVIEW_MATERIAL, { tile: 2, wetness: 0.5 });
    expect(tile.value).toBe(2); // the same uniform object, changed in place
    expect(c.mesh.material).toBe(c.material);

    c.shaders.set(c.entity, { wetness: 1 });
    const own = c.mesh.material as MeshStandardMaterial;
    expect(own).not.toBe(c.material);
    const ownCompiled = c.compile(own);
    expect(ownCompiled.uniforms.tpWet.value).toBe(1);
    expect(c.uniforms.tpWet.value).toBe(0.5); // the shared copy keeps its value
    expect(ownCompiled.fragmentShader).toBe(c.fragmentShader); // same GLSL and key: three reuses the program
    expect(own.customProgramCacheKey()).toBe(c.material.customProgramCacheKey());
    expect(c.shaders.get(c.entity)).toMatchObject({ tile: 2, wetness: 1 });
    expect(c.warnings).toEqual([]);
  });

  it('fails (keeping the Blender look) on a material that is not a MeshStandardMaterial', async () => {
    await expect(compilePatched('Triplanar', { material: new MeshBasicMaterial({ name: 'Flat' }) })).rejects.toThrow(
      'Game shader "Triplanar" failed on material "Flat": Triplanar needs a Principled BSDF material',
    );
  });
});
