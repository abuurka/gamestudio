// Dissolve: the patch with the defaults, instanced and skinned, the dissolving shadow materials (also on
// meshes copied later, as prefab spawns are), and live values per entity through ctx.shaders.
import { Mesh, MeshBasicMaterial, MeshDepthMaterial, MeshDistanceMaterial, SkinnedMesh, type MeshStandardMaterial, type Vector4 } from 'three';
import { describe, expect, it } from 'vitest';
import { getShader } from '../../src/schema';
import { compileMaterial, compilePatched, PREVIEW_MATERIAL, resolveIncludes } from './helpers';

/** Calls the material's onBeforeRender as the renderer does just before it draws `mesh`. */
const draw = (mesh: Mesh): void => {
  const material = mesh.material as MeshStandardMaterial;
  material.onBeforeRender(null as never, null as never, null as never, mesh.geometry, mesh, null as never);
};

describe('Dissolve', () => {
  it('is a built-in Magic & Sci-fi shader', () => {
    expect(getShader('Dissolve')).toMatchObject({ builtin: true, category: 'Magic & Sci-fi' });
  });

  it('patches the standard material: a cut after clipping, scorch on the colour, the glowing edge as emission', async () => {
    const c = await compilePatched('Dissolve');
    expect(c.warnings).toEqual([]);
    expect(c.material).not.toBe(c.original);
    expect(c.material.customProgramCacheKey()).toMatch(/\|gs-dissolve$/);
    expect(c.edits.every((e) => e.matched)).toBe(true);
    expect(c.vertexShader).toContain('vGsDisPos = position;');
    expect(c.fragmentShader).toContain('#include <clipping_planes_fragment>\n\nfloat gsDisE = gsDisMargin();');
    expect(c.fragmentShader).toContain('totalEmissiveRadiance += (gsDisEdgeColor.rgb');
    expect(c.uniforms.gsDisProgress.value).toBe(0);
    expect(c.uniforms.gsDisStartAt.value).toBe(-1);
    expect(c.uniforms.gsDisReverse.value).toBe(false);
    expect(c.uniforms.gsDisDirection.value.toArray()).toEqual([0, -1, 0]);
    expect(c.uniforms.gsDisEdgeColor.value.toArray()).toEqual([1, 0.32, 0.05, 1]);
    expect(c.uniforms.gsDisTime).toBe(c.time); // the engine's shared time uniform itself
    // mesh() sized the pattern to the preview box (1 m cube).
    expect(c.uniforms.gsDisMin.value.toArray()).toEqual([-0.5, -0.5, -0.5]);
    expect(c.uniforms.gsDisMax.value.toArray()).toEqual([0.5, 0.5, 0.5]);
  });

  it('works on instanced and skinned meshes; the pattern follows the rest pose', async () => {
    const instanced = await compilePatched('Dissolve', { instanced: true });
    expect(instanced.vertexShader).toContain('vGsDisSeed = instanceMatrix[3].xyz * 0.731;');
    const skinned = await compilePatched('Dissolve', { skinned: true, mesh: 'character' });
    expect(skinned.mesh).toBeInstanceOf(SkinnedMesh);
    const vertex = resolveIncludes(skinned.vertexShader);
    // Taken right after begin_vertex: before skinning moves the vertex.
    expect(vertex.indexOf('vGsDisPos = position;')).toBeLessThan(vertex.indexOf('transformed = ( bindMatrixInverse * skinned ).xyz;'));
    expect(skinned.uniforms.gsDisMax.value.y).toBeCloseTo(1.8);
  });

  it('casts a dissolving shadow: depth and distance materials with the same cut and uniforms', async () => {
    const c = await compilePatched('Dissolve', { skinned: true, mesh: 'character' });
    const depth = c.mesh.customDepthMaterial as MeshDepthMaterial;
    const distance = c.mesh.customDistanceMaterial as MeshDistanceMaterial;
    expect(depth).toBeInstanceOf(MeshDepthMaterial);
    expect(distance).toBeInstanceOf(MeshDistanceMaterial);
    for (const shadow of [depth, distance]) {
      const s = compileMaterial(shadow, c.mesh);
      expect(s.edits.every((e) => e.matched)).toBe(true);
      expect(s.fragmentShader).toContain('if (gsDisE < 0.0) discard;');
      expect(s.uniforms.gsDisProgress).toBe(c.uniforms.gsDisProgress);
      expect(s.uniforms.gsDisMin).toBe(c.uniforms.gsDisMin);
    }
  });

  it('meshes copied later (prefab spawns) get the shadow materials when they first draw', async () => {
    const c = await compilePatched('Dissolve');
    const copy = c.mesh.clone();
    expect(copy.material).toBe(c.material); // shared, as cloneScene shares materials
    expect(copy.customDepthMaterial).toBeUndefined(); // Object3D.copy drops it
    draw(copy);
    expect(copy.customDepthMaterial).toBe(c.mesh.customDepthMaterial);
    expect(copy.customDistanceMaterial).toBe(c.mesh.customDistanceMaterial);
  });

  it('takes live values per entity: its own copy, its own shadow, the same program', async () => {
    const c = await compilePatched('Dissolve', { skinned: true, mesh: 'character' });
    c.shaders.set(PREVIEW_MATERIAL, { edgeWidth: 0.1 });
    expect(c.uniforms.gsDisWidth.value).toBe(0.1);

    c.shaders.set(c.entity, { progress: 0.5, startAt: 2, edgeColor: [0.2, 0.9, 1, 1] });
    const own = c.mesh.material as MeshStandardMaterial;
    expect(own).not.toBe(c.material);
    const o = c.compile(own);
    expect(o.uniforms.gsDisProgress.value).toBe(0.5);
    expect(o.uniforms.gsDisStartAt.value).toBe(2);
    expect((o.uniforms.gsDisEdgeColor.value as Vector4).toArray()).toEqual([0.2, 0.9, 1, 1]);
    expect(o.uniforms.gsDisWidth.value).toBe(0.1); // inherited from the material
    expect(c.uniforms.gsDisProgress.value).toBe(0); // the shared copy is untouched
    expect(o.fragmentShader).toBe(c.fragmentShader);
    expect(own.customProgramCacheKey()).toBe(c.material.customProgramCacheKey());
    // Its shadow follows its own values.
    const shadow = compileMaterial(c.mesh.customDepthMaterial!, c.mesh);
    expect(shadow.uniforms.gsDisProgress).toBe(o.uniforms.gsDisProgress);
    expect(o.uniforms.gsDisMax.value.y).toBeCloseTo(1.8);
    // Later changes write the same uniform objects (no new apply).
    c.shaders.set(c.entity, { progress: 0.8 });
    expect(c.mesh.material).toBe(own);
    expect(o.uniforms.gsDisProgress.value).toBe(0.8);
    expect(c.warnings).toEqual([]);
  });

  it('fails (keeping the Blender look) on a material that is not a MeshStandardMaterial', async () => {
    await expect(compilePatched('Dissolve', { material: new MeshBasicMaterial({ name: 'Flat' }) })).rejects.toThrow(
      'Game shader "Dissolve" failed on material "Flat": Dissolve needs a Principled BSDF material',
    );
  });
});
