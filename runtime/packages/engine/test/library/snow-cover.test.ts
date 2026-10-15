// SnowCover (library): the patch on three's real sources (plain, GPU instances, skinned characters)
// and live values through ctx.shaders.set.
import { describe, expect, it } from 'vitest';
import { compilePatched, PREVIEW_MATERIAL } from './helpers';

describe('SnowCover', () => {
  it('settles snow on world-up faces over the material, with sparkles in the sun', async () => {
    const p = await compilePatched('SnowCover');
    expect(p.vertexShader).toContain('vSnowWorld = (modelMatrix * snowPosition).xyz;');
    expect(p.fragmentShader).toContain('vec3 snowBaseNormal = normal;');
    expect(p.fragmentShader).toContain('dot(normal, snowUp)');
    expect(p.fragmentShader).toContain('reflectedLight.directSpecular += directLight.color * spark');
    expect(p.uniforms.uSnowAmount!.value).toBe(0.7);
    expect(p.uniforms.uSnowColor!.value.toArray()).toEqual([0.78, 0.82, 0.88, 1]);
    expect(p.warnings).toEqual([]);
  });

  it('works on GPU instances and skinned meshes', async () => {
    const instanced = await compilePatched('SnowCover', { instanced: true });
    expect(instanced.vertexShader).toContain('snowPosition = instanceMatrix * snowPosition;');
    const skinned = await compilePatched('SnowCover', { mesh: 'character', skinned: true });
    // After project_vertex: 'transformed' is skinned by then.
    expect(skinned.vertexShader.indexOf('#include <skinning_vertex>')).toBeLessThan(skinned.vertexShader.indexOf('vSnowWorld ='));
  });

  it('takes live values, shared or per entity', async () => {
    const p = await compilePatched('SnowCover');
    p.shaders.set(PREVIEW_MATERIAL, { amount: 0.2, color: [1, 1, 1, 1] });
    expect(p.uniforms.uSnowAmount!.value).toBe(0.2);
    expect(p.uniforms.uSnowColor!.value.toArray()).toEqual([1, 1, 1, 1]);
    p.shaders.set(p.entity, { amount: 1 });
    expect(p.compile().uniforms.uSnowAmount!.value).toBe(1);
    expect(p.uniforms.uSnowAmount!.value).toBe(0.2);
  });
});
