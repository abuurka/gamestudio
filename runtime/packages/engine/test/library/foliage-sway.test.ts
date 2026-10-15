// FoliageSway (library): the patch on three's real sources (plain, GPU instances, leaf cards), shadow
// materials that sway like the leaves, and live values through ctx.shaders.set.
import { CylinderGeometry, DoubleSide, SphereGeometry, type Material } from 'three';
import { describe, expect, it } from 'vitest';
import { baseAttribute } from '../../src/shaders/library/foliage-sway/impl';
import { compileMaterial, compilePatched, PREVIEW_MATERIAL } from './helpers';

describe('FoliageSway', () => {
  it('sways the leaves by height above the origin, with sun through them', async () => {
    const p = await compilePatched('FoliageSway', { mesh: 'tree' });
    expect(p.mesh.geometry.getAttribute('gsSwayBase')).toBeDefined();
    expect(p.vertexShader).toContain('transformed += inverse(swayBasis) * swayOffset;');
    expect(p.vertexShader).toContain('float gsWindGust(');
    expect(p.fragmentShader).toContain('uSwayTranslucency');
    expect(p.uniforms.uTime).toBe(p.time);
    expect(p.uniforms.uSwayWind!.value).toBe(0.5);
    expect(p.uniforms.uSwayHeight!.value).toBe(4);
    expect((p.material as Material).side).not.toBe(DoubleSide);
    expect(p.warnings).toEqual([]);
  });

  it('bends from the origin, or from the lowest point of a crown that reaches below it', () => {
    const standing = new CylinderGeometry(1, 1, 2).translate(0, 3, 0);
    baseAttribute(standing);
    expect(standing.getAttribute('gsSwayBase').getX(0)).toBe(0);
    const centred = new SphereGeometry(1.1, 12, 8);
    baseAttribute(centred);
    expect(centred.getAttribute('gsSwayBase').getX(5)).toBeCloseTo(-1.1, 5);
  });

  it('bends GPU instances from their own origins', async () => {
    const p = await compilePatched('FoliageSway', { mesh: 'tree', instanced: true });
    expect(p.vertexShader).toContain('swayModel = modelMatrix * instanceMatrix;');
    expect(p.vertexShader).toContain('floor(swayOrigin.xz * 5.0)'); // per-instance phase
  });

  it('gives the mesh shadow materials that move the same way and share the uniforms', async () => {
    const p = await compilePatched('FoliageSway', { mesh: 'tree', instanced: true });
    const depth = p.mesh.customDepthMaterial;
    const distance = p.mesh.customDistanceMaterial;
    expect(depth?.type).toBe('MeshDepthMaterial');
    expect(distance?.type).toBe('MeshDistanceMaterial');
    for (const shadow of [depth!, distance!]) {
      const c = compileMaterial(shadow, p.mesh);
      expect(c.vertexShader).toContain('transformed += inverse(swayBasis) * swayOffset;');
      expect(c.uniforms.uSwayWind).toBe(p.uniforms.uSwayWind);
      expect(c.uniforms.uTime).toBe(p.time);
    }
  });

  it('cuts out leaf cards and draws both sides', async () => {
    const p = await compilePatched('FoliageSway', { values: { leaves: 'cards' } });
    expect(p.material.alphaTest).toBe(0.5);
    expect(p.material.side).toBe(DoubleSide);
    expect(p.material.transparent).toBe(false);
  });

  it('takes live values, shared or per entity (shadows follow the entity)', async () => {
    const p = await compilePatched('FoliageSway', { mesh: 'tree' });
    p.shaders.set(PREVIEW_MATERIAL, { wind: 1.2, rustle: 0 });
    expect(p.uniforms.uSwayWind!.value).toBe(1.2);
    expect(p.uniforms.uSwayRustle!.value).toBe(0);
    p.shaders.set(p.entity, { sway: 2 });
    const own = p.compile();
    expect(own.uniforms.uSwayAmount!.value).toBe(2);
    expect(p.uniforms.uSwayAmount!.value).toBe(1);
    expect(compileMaterial(p.mesh.customDepthMaterial!, p.mesh).uniforms.uSwayAmount!.value).toBe(2);
    expect(p.shaders.get(p.entity)).toMatchObject({ wind: 1.2, sway: 2, rustle: 0 });
  });
});
