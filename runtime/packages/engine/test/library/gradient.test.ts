// Gradient: the colour patch on Game UI meshes (MeshBasicMaterial) and on every lit material, the bounds
// it is placed in (measured as meshes draw, again when a resized 9-slice drops its box), and `ctx.ui`.
import {
  BufferGeometry,
  Float32BufferAttribute,
  MeshBasicMaterial,
  MeshLambertMaterial,
  MeshPhongMaterial,
  MeshPhysicalMaterial,
  MeshToonMaterial,
  type Material,
} from 'three';
import { describe, expect, it } from 'vitest';
import { getShader, toRegistryJSON } from '../../src/schema';
import { compilePatched, PREVIEW_MATERIAL } from './helpers';

const draw = (material: Material, geometry: BufferGeometry) =>
  material.onBeforeRender(null as never, null as never, null as never, geometry, null as never, null as never);

function rect(x0: number, y0: number, x1: number, y1: number): BufferGeometry {
  const g = new BufferGeometry();
  g.setAttribute('position', new Float32BufferAttribute([x0, y0, 0, x1, y0, 0, x1, y1, 0, x0, y1, 0], 3));
  g.setIndex([0, 1, 2, 0, 2, 3]);
  return g;
}

describe('Gradient', () => {
  it('is a built-in Style shader made for UI meshes', () => {
    expect(getShader('Gradient')).toMatchObject({ builtin: true, category: 'Style', ui: true });
    expect(toRegistryJSON().shaders.find((s) => s.name === 'Gradient')).toMatchObject({ ui: true });
  });

  it('replaces the colour of a Game UI mesh before its texture and vertex colours', async () => {
    const c = await compilePatched('Gradient', { material: new MeshBasicMaterial({ name: PREVIEW_MATERIAL }), mesh: 'plane' });
    expect(c.warnings).toEqual([]);
    expect(c.material.customProgramCacheKey()).toMatch(/\|gs-gradient$/);
    expect(c.edits.every((e) => e.matched)).toBe(true);
    const at = c.fragmentShader.indexOf('diffuseColor = vec4(gsGrC.rgb, diffuseColor.a * gsGrC.a);');
    expect(at).toBeGreaterThan(0);
    expect(at).toBeLessThan(c.fragmentShader.indexOf('#include <map_fragment>'));
    expect(c.vertexShader).toContain('vgsGrPos = position.xy;');
    expect(c.uniforms.gsGrStart.value.toArray()).toEqual([0.03, 0.08, 0.3, 1]);
    expect(c.uniforms.gsGrAngle.value).toBeCloseTo(Math.PI / 2, 9);
    // Level materials keep Blender's y up (compilePatched binds as a scene load does).
    expect(c.uniforms.gsGrFlip.value).toBe(1);
  });

  it('patches every lit material, instanced and skinned meshes too', async () => {
    for (const material of [new MeshLambertMaterial(), new MeshPhongMaterial(), new MeshPhysicalMaterial(), new MeshToonMaterial()]) {
      material.name = PREVIEW_MATERIAL;
      const c = await compilePatched('Gradient', { material });
      expect(c.edits.every((e) => e.matched), material.type).toBe(true);
    }
    const standard = await compilePatched('Gradient');
    expect(standard.fragmentShader).toContain('gsGrAcross(gsGrAngle)');
    expect((await compilePatched('Gradient', { instanced: true })).edits.every((e) => e.matched)).toBe(true);
    expect((await compilePatched('Gradient', { skinned: true, mesh: 'character' })).edits.every((e) => e.matched)).toBe(true);
  });

  it('places pixels in the bounds of what draws the material, measured again when a geometry drops its box', async () => {
    const c = await compilePatched('Gradient', { material: new MeshBasicMaterial({ name: PREVIEW_MATERIAL }) });
    const min = c.uniforms.gsGrMin.value;
    const max = c.uniforms.gsGrMax.value;
    // mesh() measured the preview box (1 m).
    expect([min.x, min.y, max.x, max.y]).toEqual([-0.5, -0.5, 0.5, 0.5]);
    const panel = rect(-200, -40, 200, 40);
    const material = c.material;
    draw(material, panel);
    expect([min.x, min.y, max.x, max.y]).toEqual([-200, -40, 200, 40]); // the union
    // A 9-slice laid out wider: the canvas drops the box, the next draw measures it again.
    panel.getAttribute('position').setX(1, 300);
    panel.getAttribute('position').setX(2, 300);
    panel.boundingBox = null;
    draw(material, panel);
    expect(max.x).toBe(300);
    draw(material, panel); // unchanged: not measured again
    expect(max.x).toBe(300);
  });

  it('takes live values like any shader', async () => {
    const c = await compilePatched('Gradient', { values: { angle: 0, endColor: [1, 0, 0, 0.5] } });
    expect(c.uniforms.gsGrAngle.value).toBe(0);
    c.shaders.set(PREVIEW_MATERIAL, { startColor: [0, 1, 0, 1] });
    expect(c.uniforms.gsGrStart.value.toArray()).toEqual([0, 1, 0, 1]);
    expect(c.uniforms.gsGrEnd.value.toArray()).toEqual([1, 0, 0, 0.5]);
  });
});
