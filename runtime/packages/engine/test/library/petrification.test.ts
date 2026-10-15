// Petrification: the stone patch with the defaults, instanced and skinned, object and world space,
// the height range it measures as meshes draw, and live values through ctx.shaders.
import { Box3, type InstancedMesh, type Material, type MeshStandardMaterial, type SkinnedMesh } from 'three';
import { describe, expect, it } from 'vitest';
import { getShader } from '../../src/schema';
import { compilePatched, PREVIEW_MATERIAL, resolveIncludes } from './helpers';

/** What the renderer does before drawing `mesh` with its material. */
function draw(material: Material, mesh: { geometry: unknown }): void {
  material.onBeforeRender(null as never, null as never, null as never, mesh.geometry as never, mesh as never, null as never);
}

describe('Petrification', () => {
  it('is a built-in Transformations shader', () => {
    expect(getShader('Petrification')).toMatchObject({ builtin: true, category: 'Transformations' });
  });

  it('patches the standard material with the defaults: stone colour, roughness, metalness, bump and glow', async () => {
    const c = await compilePatched('Petrification');
    expect(c.warnings).toEqual([]);
    expect(c.material).not.toBe(c.original);
    expect(c.material.customProgramCacheKey()).toMatch(/\|gs-petrification$/);
    expect(c.edits.every((e) => e.matched)).toBe(true);
    expect(c.vertexShader).toContain('vPtPos = transformed;\n#include <project_vertex>');
    for (const line of [
      'diffuseColor.rgb = mix(diffuseColor.rgb, ptCol, ptStone);',
      'roughnessFactor = mix(roughnessFactor, 0.96, ptStone);',
      'metalnessFactor = mix(metalnessFactor, 0.0, ptStone);',
      'normal = normalize(mix(normal, ptBumped, ptStone));',
      'totalEmissiveRadiance *= 1.0 - ptStone;',
    ]) {
      expect(c.fragmentShader).toContain(line);
    }
    expect(c.uniforms.ptProgress.value).toBe(1);
    expect(c.uniforms.ptStoneColor.value.toArray()).toEqual([0.4, 0.39, 0.37, 1]);
    expect(c.uniforms.ptGlow.value).toBe(1);
    expect(c.uniforms.ptCrackGlow.value).toBe(0);
  });

  it('measures the height range of the meshes that draw it (object space)', async () => {
    const c = await compilePatched('Petrification', { mesh: 'character' });
    expect(c.uniforms.ptRange.value.toArray()).toEqual([0, 2]); // until drawn
    draw(c.material, c.mesh);
    const [lo, hi] = c.uniforms.ptRange.value.toArray();
    expect(lo).toBeCloseTo(0);
    expect(hi).toBeCloseTo(1.8);
  });

  it('measures a skinned mesh as posed', async () => {
    const c = await compilePatched('Petrification', { mesh: 'character', skinned: true });
    const skinned = c.mesh as SkinnedMesh;
    skinned.skeleton.bones[1]!.position.y = 1.5; // the upper half moves up 0.5
    skinned.updateMatrixWorld(true);
    draw(c.material, c.mesh);
    const [lo, hi] = c.uniforms.ptRange.value.toArray();
    expect(lo).toBeCloseTo(0);
    expect(hi).toBeGreaterThan(2.2);
    // The front follows the skinned position: skinning_vertex comes before the varying.
    const vertex = resolveIncludes(c.vertexShader);
    expect(vertex.indexOf('transformed = ( bindMatrixInverse * skinned ).xyz;')).toBeLessThan(vertex.indexOf('vPtPos = transformed;'));
  });

  it('works on GPU instances, each in its own space (object) or in the world', async () => {
    const instanced = await compilePatched('Petrification', { instanced: true });
    expect((instanced.mesh as InstancedMesh).isInstancedMesh).toBe(true);
    const vertex = resolveIncludes(instanced.vertexShader);
    // Object space: the varying is set before the instance matrix moves the vertex.
    expect(vertex.indexOf('vPtPos = transformed;')).toBeLessThan(vertex.indexOf('mvPosition = instanceMatrix * mvPosition;'));
    const world = await compilePatched('Petrification', { instanced: true, values: { space: 'world', bottom: 1, top: 4 } });
    expect(world.material.customProgramCacheKey()).toMatch(/\|gs-petrification-world$/);
    expect(world.vertexShader).toContain('ptW = instanceMatrix * ptW;');
    expect(world.vertexShader).toContain('vPtPos = (modelMatrix * ptW).xyz;');
    expect(world.uniforms.ptRange.value.toArray()).toEqual([1, 4]);
    world.shaders.set(PREVIEW_MATERIAL, { top: 6 });
    expect(world.uniforms.ptRange.value.toArray()).toEqual([1, 6]); // live: bottom and top
    draw(world.material, world.mesh);
    expect(world.uniforms.ptRange.value.toArray()).toEqual([1, 6]); // world space measures nothing
  });

  it('takes live values: progress on one entity, with its own copy and the same program', async () => {
    const c = await compilePatched('Petrification', { values: { progress: 0 } });
    expect(c.uniforms.ptProgress.value).toBe(0);
    c.shaders.set(PREVIEW_MATERIAL, { progress: 0.25, glowColor: [0, 0.5, 1, 1] });
    expect(c.uniforms.ptProgress.value).toBe(0.25);
    expect(c.uniforms.ptGlowColor.value.toArray()).toEqual([0, 0.5, 1, 1]);

    c.shaders.set(c.entity, { progress: 0.7 });
    const own = c.mesh.material as MeshStandardMaterial;
    expect(own).not.toBe(c.material);
    const ownCompiled = c.compile(own);
    expect(ownCompiled.uniforms.ptProgress.value).toBe(0.7);
    expect(c.uniforms.ptProgress.value).toBe(0.25);
    expect(ownCompiled.fragmentShader).toBe(c.fragmentShader);
    expect(own.customProgramCacheKey()).toBe(c.material.customProgramCacheKey());
    // The entity's copy measures its own range.
    draw(own, c.mesh);
    const box = new Box3().setFromBufferAttribute(c.mesh.geometry.getAttribute('position') as never);
    expect(ownCompiled.uniforms.ptRange.value.toArray()).toEqual([box.min.y, box.max.y]);
    expect(c.warnings).toEqual([]);
  });

  it('a space change applies the shader again with the other program', async () => {
    const c = await compilePatched('Petrification');
    c.shaders.set(PREVIEW_MATERIAL, { space: 'world' });
    const now = c.mesh.material as Material;
    expect(now).not.toBe(c.material);
    expect(now.customProgramCacheKey()).toMatch(/\|gs-petrification-world$/);
    expect(c.compile(now).vertexShader).toContain('vPtPos = (modelMatrix * ptW).xyz;');
  });
});
