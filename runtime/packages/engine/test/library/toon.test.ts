// Toon: stepped direct light and hard shadow edges on the standard material, a rim, and an inverted
// hull (plain, instanced and skinned) for the ink outline.
import { BackSide, DoubleSide, MeshBasicMaterial, MeshStandardMaterial, type InstancedMesh, type Mesh, type SkinnedMesh } from 'three';
import { describe, expect, it } from 'vitest';
import { getShader } from '../../src/schema';
import { compileMaterial, compilePatched, PREVIEW_MATERIAL, resolveIncludes } from './helpers';

const hullOf = (mesh: Mesh): Mesh | undefined => mesh.children.find((c) => c.userData.gs_toon_outline) as Mesh | undefined;

describe('Toon', () => {
  it('is a built-in Style shader', () => {
    expect(getShader('Toon')).toMatchObject({ builtin: true, category: 'Style' });
  });

  it('bands every light, hardens shadow edges and adds a rim', async () => {
    const c = await compilePatched('Toon', { mesh: 'sphere' });
    expect(c.warnings).toEqual([]);
    expect(c.edits.every((e) => e.matched)).toBe(true);
    expect(c.material.customProgramCacheKey()).toMatch(/\|gs_toon$/);
    const fragment = resolveIncludes(c.fragmentShader);
    expect(fragment).toContain('#define RE_Direct RE_Direct_Toon');
    // Every shadow lookup (point, spot, sun, directional) is cut, none left soft.
    expect(fragment).toMatch(/gsToonShadow\( getShadow\( directionalShadowMap/);
    expect(fragment).toMatch(/gsToonShadow\( getPointShadow\(/);
    expect(fragment).not.toMatch(/\? get\w*Shadow\(/);
    expect(c.uniforms.uToonBands.value).toBe(3);
    expect(c.uniforms.uToonRimColor.value.toArray()).toEqual([1, 1, 1, 1]);
  });

  it('draws an ink outline as an inverted hull child that a reload replaces', async () => {
    const c = await compilePatched('Toon', { mesh: 'sphere' });
    const hull = hullOf(c.mesh)!;
    expect(hull).toBeDefined();
    expect(hull.geometry).toBe(c.mesh.geometry);
    expect(hull.castShadow).toBe(false);
    const ink = hull.material as MeshBasicMaterial;
    expect(ink.side).toBe(BackSide);
    expect(c.mesh.geometry.getAttribute('gsOutlineNormal').count).toBe(c.mesh.geometry.getAttribute('position').count);
    const compiled = compileMaterial(ink, hull);
    expect(compiled.edits.every((e) => e.matched)).toBe(true);
    expect(compiled.vertexShader).toContain('transformed += normalize(gsOutlineNormal) * uToonOutline / max(gsScale, 1e-6);');
    expect(compiled.uniforms.uToonOutline.value).toBe(0.02);
    // Live: the hull reads the same uniforms.
    c.shaders.set(PREVIEW_MATERIAL, { outline: 0.05, outlineColor: [1, 0, 0, 1] });
    expect(compiled.uniforms.uToonOutline.value).toBe(0.05);
    expect(compiled.uniforms.uToonOutlineColor.value.toArray()).toEqual([1, 0, 0, 1]);
    // The hull's smoothed normals close the seams of a split-normal box.
    const box = await compilePatched('Toon', { mesh: 'box' });
    const n = box.mesh.geometry.getAttribute('gsOutlineNormal');
    expect(Math.abs(n.getX(0)) + Math.abs(n.getY(0)) + Math.abs(n.getZ(0))).toBeGreaterThan(1.5); // a corner: three faces averaged
  });

  it('gives no hull without an outline, or on a double-sided card', async () => {
    const c = await compilePatched('Toon', { values: { outline: 0 } });
    expect(hullOf(c.mesh)).toBeUndefined();
    const card = await compilePatched('Toon', { mesh: 'grass', material: new MeshStandardMaterial({ name: 'Leaf', side: DoubleSide }) });
    expect(hullOf(card.mesh)).toBeUndefined();
    expect(card.warnings).toEqual([]);
  });

  it('works on instanced and skinned meshes, hull included', async () => {
    const instanced = await compilePatched('Toon', { instanced: true });
    const ih = hullOf(instanced.mesh) as InstancedMesh;
    expect(ih.isInstancedMesh).toBe(true);
    expect(ih.instanceMatrix).toBe((instanced.mesh as InstancedMesh).instanceMatrix);
    expect(compileMaterial(ih.material as MeshBasicMaterial, ih).vertexShader).toContain('gsScale *= length(instanceMatrix[0].xyz);');
    const skinned = await compilePatched('Toon', { skinned: true, mesh: 'character' });
    const sh = hullOf(skinned.mesh) as SkinnedMesh;
    expect(sh.isSkinnedMesh).toBe(true);
    expect(sh.skeleton).toBe((skinned.mesh as SkinnedMesh).skeleton);
    expect(skinned.edits.every((e) => e.matched)).toBe(true);
  });

  it('takes live values', async () => {
    const c = await compilePatched('Toon');
    c.shaders.set(PREVIEW_MATERIAL, { bands: 4, rim: 1 });
    expect(c.uniforms.uToonBands.value).toBe(4);
    expect(c.uniforms.uToonRim.value).toBe(1);
    c.shaders.set(c.entity, { specular: 0 });
    const own = c.compile(c.mesh.material as MeshStandardMaterial);
    expect(own.uniforms.uToonSpecular.value).toBe(0);
    expect(c.uniforms.uToonSpecular.value).toBe(0.6);
    expect(own.fragmentShader).toBe(c.fragmentShader);
    expect(c.warnings).toEqual([]);
  });

  it('fails (keeping the Blender look) on an unlit material', async () => {
    await expect(compilePatched('Toon', { material: new MeshBasicMaterial({ name: 'Flat' }) })).rejects.toThrow(
      'Toon needs a lit (Principled BSDF) material',
    );
  });
});
