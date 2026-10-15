// Ice (library): the patch on three's real sources (plain, GPU instances, skinned characters, which
// freeze too) and live values through ctx.shaders.set, the way game code freezes one enemy.
import { describe, expect, it } from 'vitest';
import { compilePatched, PREVIEW_MATERIAL } from './helpers';

describe('Ice', () => {
  it('glazes the material: colder, glassy, cracked, frosted, glinting', async () => {
    const p = await compilePatched('Ice');
    expect(p.vertexShader).toContain('vIceWorld = (modelMatrix * icePosition).xyz;');
    expect(p.fragmentShader).toContain('roughnessFactor = mix(roughnessFactor, mix(0.05, 0.6, iceFrost), iceAmt);');
    expect(p.fragmentShader).toContain('float iceCracks = (iceSheet((vIceWorld - iceView * 0.02 * iceScale)');
    expect(p.fragmentShader).toContain('reflectedLight.directSpecular += directLight.color * iceGlint');
    expect(p.uniforms.uIceFreeze!.value).toBe(0.8);
    expect(p.uniforms.uTime).toBe(p.time);
    expect(p.warnings).toEqual([]);
  });

  it('works on GPU instances and skinned characters', async () => {
    const instanced = await compilePatched('Ice', { instanced: true });
    expect(instanced.vertexShader).toContain('icePosition = instanceMatrix * icePosition;');
    await compilePatched('Ice', { mesh: 'character', skinned: true });
  });

  it('freezes one entity without the others', async () => {
    const p = await compilePatched('Ice', { values: { freeze: 0 } });
    p.shaders.set(p.entity, { freeze: 1, frost: 0.9 });
    const own = p.compile();
    expect(own.uniforms.uIceFreeze!.value).toBe(1);
    expect(own.uniforms.uIceFrost!.value).toBe(0.9);
    expect(p.uniforms.uIceFreeze!.value).toBe(0);
    p.shaders.set(PREVIEW_MATERIAL, { glints: 0 });
    expect(p.uniforms.uIceGlints!.value).toBe(0);
  });
});
