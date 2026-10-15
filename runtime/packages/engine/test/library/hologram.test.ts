// Hologram: the replacement material (fog, additive, no shadow), instanced and skinned, the sweep and
// slices sized to the mesh (also for meshes copied later), and live values per entity.
import { NormalBlending, ShaderMaterial, type Mesh } from 'three';
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

const draw = (mesh: Mesh): void => {
  (mesh.material as ShaderMaterial).onBeforeRender(null as never, null as never, null as never, mesh.geometry, mesh, null as never);
};

describe('Hologram', () => {
  it('is a built-in Magic & Sci-fi shader', () => {
    expect(getShader('Hologram')).toMatchObject({ builtin: true, category: 'Magic & Sci-fi' });
  });

  it('replaces the material with additive, fogged light that casts no shadow', async () => {
    const c = await compilePatched('Hologram', { mesh: 'character' });
    expect(c.warnings).toEqual([]);
    const holo = c.material as ShaderMaterial;
    expect(holo).toBeInstanceOf(ShaderMaterial);
    expect(holo.name).toBe(PREVIEW_MATERIAL);
    expect(holo.fog).toBe(true);
    expect(holo.transparent).toBe(true);
    expect(holo.depthWrite).toBe(false);
    expect(holo.blending).not.toBe(NormalBlending);
    expect(unbound(holo)).toEqual([]);
    expect(holo.uniforms.uTime).toBe(c.time);
    expect(holo.uniforms.uScanlines!.value).toBe(60);
    expect(holo.uniforms.uReveal!.value).toBe(1);
    expect(c.mesh.castShadow).toBe(false);
    // The sweep and the slices run over the mesh's height (the preview character is 1.8 m).
    expect(holo.uniforms.uMin!.value.y).toBeCloseTo(0);
    expect(holo.uniforms.uMax!.value.y).toBeCloseTo(1.8);
  });

  it('works on instanced and skinned meshes', async () => {
    const instanced = await compilePatched('Hologram', { instanced: true });
    expect(resolveIncludes(instanced.vertexShader)).toContain('world = instanceMatrix * world;');
    const skinned = await compilePatched('Hologram', { skinned: true, mesh: 'character' });
    const vertex = resolveIncludes(skinned.vertexShader);
    // Glitch shifts move the skinned, projected vertex.
    expect(vertex.indexOf('transformed = ( bindMatrixInverse * skinned ).xyz;')).toBeLessThan(vertex.indexOf('mvPosition.x += shift;'));
  });

  it('meshes copied later (prefab spawns) size the sweep and drop their shadow when they first draw', async () => {
    const c = await compilePatched('Hologram', { mesh: 'box' });
    const copy = c.mesh.clone();
    copy.castShadow = true;
    draw(copy);
    expect(copy.castShadow).toBe(false);
    expect((c.material as ShaderMaterial).uniforms.uMax!.value.y).toBeCloseTo(0.5);
  });

  it('takes live values per entity: reveal and a glitch burst on one hologram only', async () => {
    const c = await compilePatched('Hologram', { mesh: 'character' });
    c.shaders.set(PREVIEW_MATERIAL, { color: [0.2, 1, 0.4, 1] });
    expect((c.material as ShaderMaterial).uniforms.uColor!.value.toArray()).toEqual([0.2, 1, 0.4, 1]);
    c.shaders.set(c.entity, { reveal: 0.3, glitchAt: 5 });
    const own = c.mesh.material as ShaderMaterial;
    expect(own).not.toBe(c.material);
    expect(own.uniforms.uReveal!.value).toBe(0.3);
    expect(own.uniforms.uGlitchAt!.value).toBe(5);
    expect(own.uniforms.uColor!.value.toArray()).toEqual([0.2, 1, 0.4, 1]);
    expect((c.material as ShaderMaterial).uniforms.uReveal!.value).toBe(1);
    expect(own.fragmentShader).toBe((c.material as ShaderMaterial).fragmentShader);
    expect(own.uniforms.uMax!.value.y).toBeCloseTo(1.8); // mesh() sized the entity's copy too
    expect(c.warnings).toEqual([]);
  });
});
