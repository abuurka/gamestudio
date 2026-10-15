// MossOvergrowth (library): the patch on three's real sources (plain, GPU instances, skinned) and
// live values through ctx.shaders.set.
import { describe, expect, it } from 'vitest';
import { compilePatched, PREVIEW_MATERIAL } from './helpers';

describe('MossOvergrowth', () => {
  it('grows moss over the material from a front, with bumps and fuzz', async () => {
    const p = await compilePatched('MossOvergrowth', { mesh: 'cylinder' });
    expect(p.vertexShader).toContain('vMossHeight = vMossWorld.y - mossModel[3].y;');
    expect(p.fragmentShader).toContain('float mossFront = mix(-2.9, 3.2');
    expect(p.fragmentShader).toContain('normal = mossBump(normal, -vViewPosition, mossRelief);');
    expect(p.fragmentShader).toContain('totalEmissiveRadiance += uMossColor.rgb');
    expect(p.uniforms.uMossProgress!.value).toBe(0.6);
    expect(p.warnings).toEqual([]);
  });

  it('measures height from each GPU instance’s origin, and works skinned', async () => {
    const instanced = await compilePatched('MossOvergrowth', { instanced: true });
    expect(instanced.vertexShader).toContain('mossModel = modelMatrix * instanceMatrix;');
    await compilePatched('MossOvergrowth', { mesh: 'character', skinned: true });
  });

  it('takes live values, shared or per entity', async () => {
    const p = await compilePatched('MossOvergrowth');
    p.shaders.set(PREVIEW_MATERIAL, { progress: 0.1, height: 1 });
    expect(p.uniforms.uMossProgress!.value).toBe(0.1);
    expect(p.uniforms.uMossHeight!.value).toBe(1);
    p.shaders.set(p.entity, { progress: 1 });
    expect(p.compile().uniforms.uMossProgress!.value).toBe(1);
    expect(p.uniforms.uMossProgress!.value).toBe(0.1);
  });
});
