// GrassWind (library): the blade attribute from the geometry's bounds, the patch on three's real
// sources (plain, GPU instances), shadow materials that bend too, and live values.
import { BufferGeometry, Float32BufferAttribute } from 'three';
import { describe, expect, it } from 'vitest';
import { bendAttribute } from '../../src/shaders/library/grass-wind/impl';
import { compileMaterial, compilePatched, PREVIEW_MATERIAL } from './helpers';

describe('GrassWind', () => {
  it('measures each geometry from its base to its top', () => {
    const geometry = new BufferGeometry();
    geometry.setAttribute('position', new Float32BufferAttribute([0, 0, 0, 0.1, 0.25, 0, 0, 0.5, 0.05, 0.2, 0.3, 0], 3));
    bendAttribute(geometry);
    const bend = geometry.getAttribute('gsGrassBend');
    expect(Array.from({ length: 4 }, (_, i) => bend.getX(i))).toEqual([0, 0.5, 1, expect.closeTo(0.6, 6)]);
    expect(bend.getY(0)).toBeCloseTo(0.5, 6);
    // A mesh reaching below its origin bends from its lowest point.
    const sunk = new BufferGeometry();
    sunk.setAttribute('position', new Float32BufferAttribute([0, -0.2, 0, 0, 0.8, 0], 3));
    bendAttribute(sunk);
    expect(sunk.getAttribute('gsGrassBend').getX(0)).toBe(0);
    expect(sunk.getAttribute('gsGrassBend').getY(1)).toBeCloseTo(1, 6);
  });

  it('bends blades along a length-keeping curve, with sheen, translucency and root shade', async () => {
    const p = await compilePatched('GrassWind', { mesh: 'grass' });
    expect(p.mesh.geometry.getAttribute('gsGrassBend')).toBeDefined();
    expect(p.vertexShader).toContain('attribute vec2 gsGrassBend;');
    expect(p.vertexShader).toContain('transformed += inverse(grassBasis) * (grassSpine - vec3(0.0, grassT * grassH, 0.0));');
    expect(p.fragmentShader).toContain('grassWave');
    expect(p.fragmentShader).toContain('grassTransmit');
    expect(p.fragmentShader).toContain('grassAo');
    expect(p.uniforms.uGrassWind!.value).toBe(0.5);
    expect(p.uniforms.uGrassSheen!.value).toBe(0.6);
    expect(p.warnings).toEqual([]);
  });

  it('bends GPU instances from their own origins, shadows included', async () => {
    const p = await compilePatched('GrassWind', { mesh: 'grass', instanced: true });
    expect(p.vertexShader).toContain('grassModel = modelMatrix * instanceMatrix;');
    for (const shadow of [p.mesh.customDepthMaterial!, p.mesh.customDistanceMaterial!]) {
      const c = compileMaterial(shadow, p.mesh);
      expect(c.vertexShader).toContain('grassSpine');
      expect(c.uniforms.uGrassBend).toBe(p.uniforms.uGrassBend);
    }
  });

  it('takes live values, shared or per entity', async () => {
    const p = await compilePatched('GrassWind', { mesh: 'grass' });
    p.shaders.set(PREVIEW_MATERIAL, { wind: 1, windAngle: 1.5 });
    expect(p.uniforms.uGrassWind!.value).toBe(1);
    expect(p.uniforms.uGrassAngle!.value).toBe(1.5);
    p.shaders.set(p.entity, { bend: 0 });
    expect(p.compile().uniforms.uGrassBend!.value).toBe(0);
    expect(p.uniforms.uGrassBend!.value).toBe(1);
    expect(compileMaterial(p.mesh.customDepthMaterial!, p.mesh).uniforms.uGrassBend!.value).toBe(0);
  });
});
