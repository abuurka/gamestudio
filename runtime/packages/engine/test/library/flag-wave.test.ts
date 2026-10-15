// FlagWave (library): the cloth attribute from the UV map (pin weight and its direction over the
// cloth), the patch on three's real sources (plain, GPU instances), moving shadows, and live values.
import { BufferGeometry, Float32BufferAttribute, PlaneGeometry } from 'three';
import { describe, expect, it } from 'vitest';
import { flagAttribute } from '../../src/shaders/library/flag-wave/impl';
import { compileMaterial, compilePatched, PREVIEW_MATERIAL } from './helpers';

/** Index of the vertex nearest to (x, y) of an upright PlaneGeometry. */
const vertexAt = (plane: PlaneGeometry, x: number, y: number): number => {
  const p = plane.getAttribute('position');
  let best = 0;
  for (let i = 0; i < p.count; i++) if (Math.hypot(p.getX(i) - x, p.getY(i) - y) < Math.hypot(p.getX(best) - x, p.getY(best) - y)) best = i;
  return best;
};

describe('FlagWave', () => {
  it('reads the pinned edge from the UV map, in metres along the cloth', () => {
    const plane = new PlaneGeometry(3, 2, 6, 4); // upright in XY, U along +X
    expect(flagAttribute(plane, 'left')).toBe(true);
    const flag = plane.getAttribute('gsFlag');
    const pole = vertexAt(plane, -1.5, 0), free = vertexAt(plane, 1.5, 1);
    expect(flag.getW(pole)).toBeCloseTo(0, 6);
    expect(flag.getW(free)).toBeCloseTo(1, 6);
    // Moving one unit of w crosses the whole 3 m flag along +X.
    expect([flag.getX(free), flag.getY(free), flag.getZ(free)].map((n) => +n.toFixed(4) + 0)).toEqual([3, 0, 0]);
    // 'top' is Blender's V 1, which a GLB stores as v = 0, so the pin is where three's v is 0.
    flagAttribute(plane, 'top');
    expect(plane.getAttribute('gsFlag').getW(vertexAt(plane, 0, -1))).toBeCloseTo(0, 6); // PlaneGeometry: v = 0 at the bottom
    expect(plane.getAttribute('gsFlag').getY(free)).toBeCloseTo(2, 4);
  });

  it('leaves a mesh without a usable UV map still', () => {
    const bare = new BufferGeometry();
    bare.setAttribute('position', new Float32BufferAttribute([0, 0, 0, 1, 0, 0, 0, 1, 0], 3));
    expect(flagAttribute(bare, 'left')).toBe(false);
  });

  it('ripples the cloth and its normals, with sun through it', async () => {
    const p = await compilePatched('FlagWave', { mesh: 'plane' });
    expect(p.mesh.geometry.getAttribute('gsFlag')).toBeDefined();
    expect(p.vertexShader).toContain('gsFlagWave(position, objectNormal);');
    expect(p.vertexShader).toContain('transformed += gsFlagOffset;');
    expect(p.fragmentShader).toContain('flagThrough');
    expect(p.uniforms.uFlagAmplitude!.value).toBe(0.15);
    expect(p.warnings).toEqual([]);
  });

  it('moves GPU instances and their shadows', async () => {
    const p = await compilePatched('FlagWave', { mesh: 'plane', instanced: true });
    expect(p.vertexShader).toContain('model = modelMatrix * instanceMatrix;');
    for (const shadow of [p.mesh.customDepthMaterial!, p.mesh.customDistanceMaterial!]) {
      const c = compileMaterial(shadow, p.mesh);
      expect(c.vertexShader).toContain('gsFlagWave(position, normal);');
      expect(c.uniforms.uFlagWind).toBe(p.uniforms.uFlagWind);
    }
  });

  it('takes live values; a new pinned edge applies again and rewrites the attribute', async () => {
    const p = await compilePatched('FlagWave', { mesh: 'plane' });
    p.shaders.set(PREVIEW_MATERIAL, { wind: 1.1, waveLength: 0.8 });
    expect(p.uniforms.uFlagWind!.value).toBe(1.1);
    expect(p.uniforms.uFlagWaveLength!.value).toBe(0.8);
    const before = p.mesh.geometry.getAttribute('gsFlag');
    p.shaders.set(PREVIEW_MATERIAL, { pinnedEdge: 'right' });
    expect(p.mesh.geometry.getAttribute('gsFlag')).not.toBe(before);
    expect(p.compile().uniforms.uFlagWind!.value).toBe(1.1);
  });
});
