// Interact Glint: the emissive patch with the defaults, instanced and skinned, the sweep range it
// measures as meshes draw, and strength from game code.
import type { Material, MeshStandardMaterial } from 'three';
import { describe, expect, it } from 'vitest';
import { getShader } from '../../src/schema';
import { compilePatched, PREVIEW_MATERIAL, resolveIncludes } from './helpers';

/** What the renderer does before drawing `mesh` with its material. */
function draw(material: Material, mesh: { geometry: unknown }): void {
  material.onBeforeRender(null as never, null as never, null as never, mesh.geometry as never, mesh as never, null as never);
}

describe('InteractGlint', () => {
  it('is a built-in Gameplay shader', () => {
    expect(getShader('InteractGlint')).toMatchObject({ builtin: true, category: 'Gameplay' });
  });

  it('patches the emissive of the standard material with the defaults', async () => {
    const c = await compilePatched('InteractGlint');
    expect(c.warnings).toEqual([]);
    expect(c.material.customProgramCacheKey()).toMatch(/\|gs-interact-glint$/);
    expect(c.edits.every((e) => e.matched)).toBe(true);
    expect(c.vertexShader).toContain('vIgLocal = transformed;');
    expect(c.fragmentShader).toContain('totalEmissiveRadiance += igCol * igGain;');
    expect(c.uniforms.igStrength.value).toBe(1);
    expect(c.uniforms.igPeriod.value).toBe(2.4);
    expect(c.uniforms.igTime).toBe(c.time);
  });

  it('measures how far the sweep travels across the meshes that draw it', async () => {
    const c = await compilePatched('InteractGlint', { mesh: 'box' });
    draw(c.material, c.mesh);
    const axis = c.uniforms.igAxis.value;
    // The unit box's corners projected on the sweep axis.
    const reach = 0.5 * (Math.abs(axis.x) + Math.abs(axis.y) + Math.abs(axis.z));
    expect(c.uniforms.igRange.value.x).toBeCloseTo(-reach);
    expect(c.uniforms.igRange.value.y).toBeCloseTo(reach);
  });

  it('works on GPU instances (each with its own phase) and skinned meshes', async () => {
    const instanced = await compilePatched('InteractGlint', { instanced: true });
    expect(instanced.vertexShader).toContain('igOrigin = instanceMatrix * igOrigin;');
    const vertex = resolveIncludes(instanced.vertexShader);
    expect(vertex.indexOf('vIgLocal = transformed;')).toBeLessThan(vertex.indexOf('mvPosition = instanceMatrix * mvPosition;'));
    const skinned = await compilePatched('InteractGlint', { skinned: true, mesh: 'character' });
    const sv = resolveIncludes(skinned.vertexShader);
    expect(sv.indexOf('transformed = ( bindMatrixInverse * skinned ).xyz;')).toBeLessThan(sv.indexOf('vIgLocal = transformed;'));
  });

  it('wakes up on one entity as the player comes near', async () => {
    const c = await compilePatched('InteractGlint', { values: { strength: 0 } });
    expect(c.uniforms.igStrength.value).toBe(0);
    c.shaders.set(c.entity, { strength: 0.8 });
    const own = c.mesh.material as MeshStandardMaterial;
    const ownCompiled = c.compile(own);
    expect(ownCompiled.uniforms.igStrength.value).toBe(0.8);
    expect(c.uniforms.igStrength.value).toBe(0);
    expect(own.customProgramCacheKey()).toBe(c.material.customProgramCacheKey());
    c.shaders.set(PREVIEW_MATERIAL, { period: 4 });
    expect(c.uniforms.igPeriod.value).toBe(4);
    expect(c.warnings).toEqual([]);
  });
});
