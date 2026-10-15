// EnergyShield: the replacement material (fog, additive, no shadow), both lattices, instanced and skinned,
// hit ripples from hitAt/hitPoint (a ring of slots, in the mesh's space) and live values per entity.
import { MeshStandardMaterial, NormalBlending, ShaderMaterial, Vector4, type Mesh } from 'three';
import { describe, expect, it } from 'vitest';
import { getShader } from '../../src/schema';
import { compilePatched, PREVIEW_MATERIAL, resolveIncludes } from './helpers';

/** Uniforms three.js sets itself. */
const PROVIDED = new Set([
  'modelMatrix', 'modelViewMatrix', 'projectionMatrix', 'viewMatrix', 'normalMatrix', 'cameraPosition', 'isOrthographic',
  'bindMatrix', 'bindMatrixInverse', 'boneTexture', 'morphTargetBaseInfluence', 'morphTargetInfluences', 'morphTargetsTexture',
  'morphTargetsTextureSize', 'logDepthBufFC', 'toneMappingExposure', 'clippingPlanes', 'batchingTexture', 'batchingIdTexture',
  'batchingColorTexture', '_gl_DrawID',
]);

/** Uniforms the material's GLSL declares (includes expanded) that nothing sets. */
function unbound(material: ShaderMaterial): string[] {
  const names = new Set<string>();
  for (const source of [material.vertexShader, material.fragmentShader]) {
    for (const m of resolveIncludes(source).matchAll(/^\s*uniform\s+(?:(?:lowp|mediump|highp)\s+)?\w+\s+(\w+)/gm)) names.add(m[1]!);
  }
  return [...names].filter((n) => !(n in material.uniforms) && !PROVIDED.has(n));
}

/** Calls the material's onBeforeRender as the renderer does just before it draws `mesh`. */
const draw = (mesh: Mesh): void => {
  mesh.updateMatrixWorld(true);
  (mesh.material as ShaderMaterial).onBeforeRender(null as never, null as never, null as never, mesh.geometry, mesh, null as never);
};

describe('EnergyShield', () => {
  it('is a built-in Magic & Sci-fi shader', () => {
    expect(getShader('EnergyShield')).toMatchObject({ builtin: true, category: 'Magic & Sci-fi' });
  });

  it('replaces the material with additive, fogged light that casts no shadow', async () => {
    const c = await compilePatched('EnergyShield', { mesh: 'sphere' });
    expect(c.warnings).toEqual([]);
    const shield = c.material as ShaderMaterial;
    expect(shield).toBeInstanceOf(ShaderMaterial);
    expect(shield.name).toBe(PREVIEW_MATERIAL);
    expect(shield.fog).toBe(true);
    expect(shield.transparent).toBe(true);
    expect(shield.depthWrite).toBe(false);
    expect(shield.blending).not.toBe(NormalBlending);
    expect(shield.defines).toHaveProperty('GS_SHIELD_SPHERE');
    expect(unbound(shield)).toEqual([]);
    expect(shield.uniforms.uTime).toBe(c.time);
    expect(shield.uniforms.uFaceN!.value).toHaveLength(20);
    expect(shield.uniforms.uCellSize!.value).toBe(0.35);
    expect(resolveIncludes(shield.fragmentShader)).toContain('gl_FragColor.rgb *= 1.0 - fogAmount;');
    expect(c.mesh.castShadow).toBe(false);
  });

  it('flat lattice: a different program without the geodesic tables', async () => {
    const c = await compilePatched('EnergyShield', { mesh: 'box', values: { lattice: 'flat' } });
    const shield = c.material as ShaderMaterial;
    expect(shield.defines).not.toHaveProperty('GS_SHIELD_SPHERE');
    expect(shield.uniforms.uFaceN).toBeUndefined();
    // Declared only under #ifdef GS_SHIELD_SPHERE.
    expect(unbound(shield).sort()).toEqual(['uEdgeHeight', 'uFaceCorners', 'uFaceEdges', 'uFaceN']);
  });

  it('works on instanced and skinned meshes', async () => {
    const instanced = await compilePatched('EnergyShield', { instanced: true });
    expect(resolveIncludes(instanced.vertexShader)).toContain('local = instanceMatrix * local;');
    expect(instanced.mesh.castShadow).toBe(false);
    const skinned = await compilePatched('EnergyShield', { skinned: true, mesh: 'character' });
    const vertex = resolveIncludes(skinned.vertexShader);
    expect(vertex).toContain('transformed = ( bindMatrixInverse * skinned ).xyz;');
    expect(vertex.indexOf('transformed = ( bindMatrixInverse * skinned ).xyz;')).toBeLessThan(vertex.indexOf('vec4 local = vec4(transformed, 1.0);'));
  });

  it('respects a saturated Blender base colour as a tint, not a grey one', async () => {
    const grey = await compilePatched('EnergyShield');
    expect((grey.material as ShaderMaterial).uniforms.uTint!.value.toArray()).toEqual([1, 1, 1]);
    const red = await compilePatched('EnergyShield', { material: new MeshStandardMaterial({ name: 'Red', color: 0xff0000 }) });
    expect((red.material as ShaderMaterial).uniforms.uTint!.value.toArray()).toEqual([1, 0, 0]);
  });

  it('starts a ripple per new hitAt, at hitPoint in the mesh space, per entity', async () => {
    const c = await compilePatched('EnergyShield', { mesh: 'sphere' });
    c.mesh.position.set(10, 0, 0);
    c.shaders.set(c.entity, { hitPoint: [11, 0, 0], hitAt: 3 });
    const own = c.mesh.material as ShaderMaterial;
    expect(own).not.toBe(c.material);
    expect(own.uniforms.uHitAt).toBeUndefined(); // read through onBeforeRender, not as a uniform
    draw(c.mesh);
    const hits = own.uniforms.uHits!.value as Vector4[];
    expect(hits.map((h) => h.w).filter((w) => w === 3)).toHaveLength(1);
    expect(hits.find((h) => h.w === 3)!.toArray()).toEqual([1, 0, 0, 3]); // local to the mesh
    draw(c.mesh); // the same value again: no new ripple
    c.shaders.set(c.entity, { hitPoint: [10, 1, 0], hitAt: 3.2 });
    draw(c.mesh);
    expect(hits.map((h) => h.w).sort()).toEqual(expect.arrayContaining([3, 3.2]));
    expect(hits.find((h) => h.w === 3.2)!.toArray()).toEqual([0, 1, 0, 3.2]);
    // The shared material saw none of it.
    const shared = (c.material as ShaderMaterial).uniforms.uHits!.value as Vector4[];
    expect(shared.some((h) => h.w === 3 || h.w === 3.2)).toBe(false);
    expect(own.fragmentShader).toBe((c.material as ShaderMaterial).fragmentShader);
    expect(c.warnings).toEqual([]);
  });

  it('takes live values in place: charge, colour and size', async () => {
    const c = await compilePatched('EnergyShield');
    const shield = c.material as ShaderMaterial;
    const charge = shield.uniforms.uCharge!;
    c.shaders.set(PREVIEW_MATERIAL, { charge: 0.2, color: [1, 0.2, 0.1, 1], cellSize: 0.3 });
    expect(charge.value).toBe(0.2);
    expect(shield.uniforms.uColor!.value.toArray()).toEqual([1, 0.2, 0.1, 1]);
    expect(shield.uniforms.uCellSize!.value).toBe(0.3);
    expect(c.mesh.material).toBe(shield);
    // Switching the lattice is a new program (the slow path).
    c.shaders.set(PREVIEW_MATERIAL, { lattice: 'flat' });
    expect(c.mesh.material).not.toBe(shield);
    expect((c.mesh.material as ShaderMaterial).defines).not.toHaveProperty('GS_SHIELD_SPHERE');
    expect((c.mesh.material as ShaderMaterial).uniforms.uCharge!.value).toBe(0.2);
  });
});
