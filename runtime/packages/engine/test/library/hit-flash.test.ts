// Hit Flash: the emissive patch with the defaults, instanced and skinned, and the trigger pattern:
// game code sets hitAt to the game time on one entity.
import type { InstancedMesh, MeshStandardMaterial, SkinnedMesh } from 'three';
import { describe, expect, it } from 'vitest';
import { getShader } from '../../src/schema';
import { compilePatched, PREVIEW_MATERIAL } from './helpers';

describe('HitFlash', () => {
  it('is a built-in Gameplay shader', () => {
    expect(getShader('HitFlash')).toMatchObject({ builtin: true, category: 'Gameplay' });
  });

  it('patches the emissive of the standard material; never hit by default', async () => {
    const c = await compilePatched('HitFlash');
    expect(c.warnings).toEqual([]);
    expect(c.material.customProgramCacheKey()).toMatch(/\|gs-hit-flash$/);
    expect(c.edits.every((e) => e.matched)).toBe(true);
    expect(c.fragmentShader).toContain('totalEmissiveRadiance += mix(hfColor.rgb, vec3(1.0), hfF) * hfF * 5.0 * hfFlash;');
    expect(c.fragmentShader).toContain('totalEmissiveRadiance += hfColor.rgb * hfR * 3.2 * hfRim * pow(hfFres, 2.2);');
    expect(c.vertexShader).not.toContain('hf'); // fragment only
    expect(c.uniforms.hfHitAt.value).toBe(-1000);
    expect(c.uniforms.hfTime).toBe(c.time); // the shared game-time uniform: the fade needs no code
    expect(c.uniforms.hfDuration.value).toBe(0.5);
  });

  it('works on instanced and skinned meshes', async () => {
    const instanced = await compilePatched('HitFlash', { instanced: true });
    expect((instanced.mesh as InstancedMesh).isInstancedMesh).toBe(true);
    expect(instanced.edits.every((e) => e.matched)).toBe(true);
    const skinned = await compilePatched('HitFlash', { skinned: true, mesh: 'character' });
    expect((skinned.mesh as SkinnedMesh).isSkinnedMesh).toBe(true);
    expect(skinned.fragmentShader).toContain('hfAge');
  });

  it('flashes one entity when game code sets hitAt to the game time', async () => {
    const c = await compilePatched('HitFlash', { time: 12.5 });
    c.shaders.set(c.entity, { hitAt: c.time.value });
    const own = c.mesh.material as MeshStandardMaterial;
    expect(own).not.toBe(c.material);
    const ownCompiled = c.compile(own);
    expect(ownCompiled.uniforms.hfHitAt.value).toBe(12.5);
    expect(ownCompiled.uniforms.hfTime).toBe(c.time);
    expect(c.uniforms.hfHitAt.value).toBe(-1000); // other users of the material are not hit
    expect(own.customProgramCacheKey()).toBe(c.material.customProgramCacheKey());
    // A second hit only moves the uniform: no new copy.
    c.shaders.set(c.entity, { hitAt: 13 });
    expect(c.mesh.material).toBe(own);
    expect(ownCompiled.uniforms.hfHitAt.value).toBe(13);
    c.shaders.set(PREVIEW_MATERIAL, { color: [1, 0, 0, 1] });
    expect(ownCompiled.uniforms.hfColor.value.toArray()).toEqual([1, 0, 0, 1]); // not overridden on the entity: inherited
    expect(c.warnings).toEqual([]);
  });
});
