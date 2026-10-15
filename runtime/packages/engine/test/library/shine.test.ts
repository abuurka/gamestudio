// Shine: the highlight sweep on Game UI meshes (MeshBasicMaterial) and lit materials, added to the final
// colour before the alpha is written, on the shared time uniform.
import { MeshBasicMaterial, MeshLambertMaterial, MeshPhysicalMaterial } from 'three';
import { describe, expect, it } from 'vitest';
import { getShader, libraryShaders } from '../../src/schema';
import { compilePatched, PREVIEW_MATERIAL } from './helpers';

describe('Shine', () => {
  it('is a built-in Style shader made for UI meshes', () => {
    expect(getShader('Shine')).toMatchObject({ builtin: true, category: 'Style', ui: true });
  });

  it('adds the band to the final colour of a Game UI mesh, on the shared clock', async () => {
    const c = await compilePatched('Shine', { material: new MeshBasicMaterial({ name: PREVIEW_MATERIAL }), mesh: 'plane', time: 0.3 });
    expect(c.warnings).toEqual([]);
    expect(c.material.customProgramCacheKey()).toMatch(/\|gs-shine$/);
    expect(c.edits.every((e) => e.matched)).toBe(true);
    const at = c.fragmentShader.indexOf('outgoingLight += gsShColor.rgb * gsShColor.a');
    expect(at).toBeGreaterThan(c.fragmentShader.indexOf('vec3 outgoingLight = reflectedLight.indirectDiffuse;'));
    expect(at).toBeLessThan(c.fragmentShader.indexOf('#include <opaque_fragment>'));
    expect(c.uniforms.gsShTime).toBe(c.time);
    expect(c.uniforms.gsShColor.value.toArray()).toEqual([1, 1, 1, 0.6]);
    expect(c.uniforms.gsShInterval.value).toBe(3);
    expect(c.uniforms.gsShAngle.value).toBeCloseTo(-Math.PI / 6, 9);
  });

  it('works on lit materials, instanced and skinned meshes', async () => {
    for (const material of [new MeshLambertMaterial(), new MeshPhysicalMaterial()]) {
      material.name = PREVIEW_MATERIAL;
      expect((await compilePatched('Shine', { material })).edits.every((e) => e.matched), material.type).toBe(true);
    }
    const standard = await compilePatched('Shine');
    expect(standard.fragmentShader.indexOf('gsShAcross(gsShAngle)')).toBeGreaterThan(standard.fragmentShader.indexOf('vec3 outgoingLight ='));
    expect((await compilePatched('Shine', { instanced: true })).edits.every((e) => e.matched)).toBe(true);
    expect((await compilePatched('Shine', { skinned: true, mesh: 'character' })).edits.every((e) => e.matched)).toBe(true);
  });
});

describe('the shader library on Game UI meshes', () => {
  it('marks only Shine and Gradient as made for UI meshes', () => {
    // The others need lit materials (HitFlash, Dissolve, InteractGlint and most more throw on
    // MeshBasicMaterial), normals (Hologram's fresnel: UI meshes have none) or world metres.
    expect(libraryShaders().filter((s) => s.ui).map((s) => s.name)).toEqual(['Gradient', 'Shine']);
  });

  it('UI-capable shaders patch a flat MeshBasicMaterial without normals', async () => {
    for (const shader of libraryShaders().filter((s) => s.ui)) {
      const c = await compilePatched(shader.name, { material: new MeshBasicMaterial({ name: PREVIEW_MATERIAL }), mesh: 'plane' });
      c.mesh.geometry.deleteAttribute('normal');
      expect(c.warnings, shader.name).toEqual([]);
      expect(c.edits.every((e) => e.matched), shader.name).toBe(true);
      expect(c.fragmentShader, shader.name).not.toMatch(/\bnormal\b|vViewPosition|totalEmissiveRadiance/);
    }
  });
});
