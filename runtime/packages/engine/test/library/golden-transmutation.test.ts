// Golden Transmutation: the gold patch with the defaults, instanced and skinned, object and world
// space, the height range it measures as meshes draw, and live values through ctx.shaders.
import type { Material, MeshStandardMaterial } from 'three';
import { describe, expect, it } from 'vitest';
import { getShader } from '../../src/schema';
import { compilePatched, PREVIEW_MATERIAL, resolveIncludes } from './helpers';

/** What the renderer does before drawing `mesh` with its material. */
function draw(material: Material, mesh: { geometry: unknown }): void {
  material.onBeforeRender(null as never, null as never, null as never, mesh.geometry as never, mesh as never, null as never);
}

describe('GoldenTransmutation', () => {
  it('is a built-in Transformations shader', () => {
    expect(getShader('GoldenTransmutation')).toMatchObject({ builtin: true, category: 'Transformations' });
  });

  it('patches the standard material with the defaults: gold albedo, metal, glitter, seam', async () => {
    const c = await compilePatched('GoldenTransmutation');
    expect(c.warnings).toEqual([]);
    expect(c.material).not.toBe(c.original);
    expect(c.material.customProgramCacheKey()).toMatch(/\|gs-golden-transmutation$/);
    expect(c.edits.every((e) => e.matched)).toBe(true);
    expect(c.vertexShader).toContain('vGtLocal = transformed;\nvGtPos = transformed;\n#include <project_vertex>');
    for (const line of [
      'diffuseColor.rgb = mix(diffuseColor.rgb, gtAlbedo, gtGold);',
      'metalnessFactor = mix(metalnessFactor, 1.0, gtGold);',
      'roughnessFactor = mix(roughnessFactor, gtRoughness, gtGold);',
      'float gtFacet = pow(max(dot(reflect(-gtV, normalize(normal + gtJitter * 0.5)), directionalLights[0].direction), 0.0), 600.0);',
      'totalEmissiveRadiance += gtMoltenColor.rgb * gtSeam * gtFlick * 9.0 * gtHeat;',
    ]) {
      expect(c.fragmentShader).toContain(line);
    }
    // Without an environment map the gold reflects a gradient instead of going black.
    expect(c.fragmentShader).toContain('#ifndef USE_ENVMAP');
    expect(c.uniforms.gtProgress.value).toBe(1);
    expect(c.uniforms.gtGoldColor.value.toArray()).toEqual([1, 0.54, 0.042, 1]);
    expect(c.uniforms.gtRoughness.value).toBe(0.2);
    expect(c.uniforms.gtTime).toBe(c.time); // the shared game-time uniform
  });

  it('measures the height range of the meshes that draw it (object space)', async () => {
    const c = await compilePatched('GoldenTransmutation', { mesh: 'character' });
    draw(c.material, c.mesh);
    const [lo, hi] = c.uniforms.gtRange.value.toArray();
    expect(lo).toBeCloseTo(0);
    expect(hi).toBeCloseTo(1.8);
  });

  it('works on skinned meshes and GPU instances; world space uses world heights', async () => {
    const skinned = await compilePatched('GoldenTransmutation', { mesh: 'character', skinned: true });
    const vertex = resolveIncludes(skinned.vertexShader);
    expect(vertex.indexOf('transformed = ( bindMatrixInverse * skinned ).xyz;')).toBeLessThan(vertex.indexOf('vGtLocal = transformed;'));
    draw(skinned.material, skinned.mesh);
    expect(skinned.uniforms.gtRange.value.y).toBeCloseTo(1.8);

    const instanced = await compilePatched('GoldenTransmutation', { instanced: true });
    const iv = resolveIncludes(instanced.vertexShader);
    expect(iv.indexOf('vGtPos = transformed;')).toBeLessThan(iv.indexOf('mvPosition = instanceMatrix * mvPosition;'));

    const world = await compilePatched('GoldenTransmutation', { instanced: true, values: { space: 'world', bottom: -1, top: 3 } });
    expect(world.material.customProgramCacheKey()).toMatch(/\|gs-golden-transmutation-world$/);
    expect(world.vertexShader).toContain('gtW = instanceMatrix * gtW;');
    expect(world.vertexShader).toContain('vGtPos = (modelMatrix * gtW).xyz;');
    expect(world.vertexShader).toContain('vGtLocal = transformed;'); // the glitter still sticks to the object
    expect(world.uniforms.gtRange.value.toArray()).toEqual([-1, 3]);
  });

  it('takes live values: progress on one entity, with its own copy and the same program', async () => {
    const c = await compilePatched('GoldenTransmutation', { values: { progress: 0 } });
    c.shaders.set(PREVIEW_MATERIAL, { progress: 0.4, heat: 2 });
    expect(c.uniforms.gtProgress.value).toBe(0.4);
    expect(c.uniforms.gtHeat.value).toBe(2);
    c.shaders.set(c.entity, { progress: 0.9, goldColor: [0.9, 0.9, 0.95, 1] });
    const own = c.mesh.material as MeshStandardMaterial;
    expect(own).not.toBe(c.material);
    const ownCompiled = c.compile(own);
    expect(ownCompiled.uniforms.gtProgress.value).toBe(0.9);
    expect(ownCompiled.uniforms.gtGoldColor.value.toArray()).toEqual([0.9, 0.9, 0.95, 1]);
    expect(c.uniforms.gtProgress.value).toBe(0.4);
    expect(ownCompiled.fragmentShader).toBe(c.fragmentShader);
    expect(own.customProgramCacheKey()).toBe(c.material.customProgramCacheKey());
    expect(c.warnings).toEqual([]);
  });
});
