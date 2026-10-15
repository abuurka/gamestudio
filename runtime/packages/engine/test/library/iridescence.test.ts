// Iridescence: a thin film over the standard material's specular reflectance, opaque and transparent.
import { MeshBasicMaterial, MeshStandardMaterial, type SkinnedMesh } from 'three';
import { describe, expect, it } from 'vitest';
import { getShader } from '../../src/schema';
import { compilePatched, PREVIEW_MATERIAL, resolveIncludes } from './helpers';

describe('Iridescence', () => {
  it('is a built-in Surfaces shader', () => {
    expect(getShader('Iridescence')).toMatchObject({ builtin: true, category: 'Surfaces' });
  });

  it('colours the specular reflectance with the film, keeping the material', async () => {
    const c = await compilePatched('Iridescence', { mesh: 'sphere' });
    expect(c.warnings).toEqual([]);
    expect(c.material).not.toBe(c.original);
    expect(c.material.type).toBe('MeshStandardMaterial');
    expect(c.material.customProgramCacheKey()).toMatch(/\|gs_iridescence$/);
    expect(c.edits.every((e) => e.matched)).toBe(true);
    const fragment = resolveIncludes(c.fragmentShader);
    // The film comes after three sets the reflectance and before the lights use it.
    const film = fragment.indexOf('material.specularColor = mix(material.specularColor, gsFilmR, gsFilmMask);');
    expect(film).toBeGreaterThan(fragment.indexOf('material.specularF90 = 1.0;'));
    expect(film).toBeLessThan(fragment.indexOf('IncidentLight directLight;'));
    expect(c.fragmentShader).not.toContain('outgoingLight += totalSpecular'); // opaque: no alpha handling
    expect(c.uniforms.uFilmStrength.value).toBe(2);
    expect(c.uniforms.uFilmThickness.value.toArray()).toEqual([150, 900]);
    expect(c.uniforms.uGsTime).toBe(c.time);
  });

  it('keeps a transparent film reflection at full strength (bubbles)', async () => {
    const bubble = new MeshStandardMaterial({ name: 'Bubble', transparent: true, opacity: 0.1, roughness: 0.05 });
    const c = await compilePatched('Iridescence', { mesh: 'sphere', material: bubble, values: { drain: 1 } });
    expect(c.material.customProgramCacheKey()).toMatch(/\|gs_iridescence_t$/);
    expect(c.fragmentShader).toContain('outgoingLight += totalSpecular * (1.0 / max(gsAlpha, 0.05) - 1.0);');
    expect(c.uniforms.uFilmDrain.value).toBe(1);
  });

  it('works on instanced and skinned meshes', async () => {
    const instanced = await compilePatched('Iridescence', { instanced: true });
    expect(instanced.vertexShader).toContain('vGsFilmP += instanceMatrix[3].xyz * 0.37;');
    const skinned = await compilePatched('Iridescence', { skinned: true, mesh: 'character' });
    expect((skinned.mesh as SkinnedMesh).isSkinnedMesh).toBe(true);
    expect(skinned.edits.every((e) => e.matched)).toBe(true);
  });

  it('takes live values', async () => {
    const c = await compilePatched('Iridescence');
    c.shaders.set(PREVIEW_MATERIAL, { strength: 3, thickness: [100, 400] });
    expect(c.uniforms.uFilmStrength.value).toBe(3);
    expect(c.uniforms.uFilmThickness.value.toArray()).toEqual([100, 400]);
    c.shaders.set(c.entity, { coverage: 0.4 });
    const own = c.compile(c.mesh.material as MeshStandardMaterial);
    expect(own.uniforms.uFilmCoverage.value).toBe(0.4);
    expect(c.uniforms.uFilmCoverage.value).toBe(1);
    expect(own.fragmentShader).toBe(c.fragmentShader);
    expect(c.warnings).toEqual([]);
  });

  it('fails (keeping the Blender look) on an unlit material', async () => {
    await expect(compilePatched('Iridescence', { material: new MeshBasicMaterial({ name: 'Flat' }) })).rejects.toThrow(
      'Iridescence needs a lit (Principled BSDF) material',
    );
  });
});
