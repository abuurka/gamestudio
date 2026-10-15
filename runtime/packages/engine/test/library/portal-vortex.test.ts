// PortalVortex: the replacement material (fog, premultiplied, writes depth, no shadow), its tileable noise
// texture, instanced and skinned, and live values per entity.
import { DataTexture, NormalBlending, RepeatWrapping, ShaderMaterial } from 'three';
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

describe('PortalVortex', () => {
  it('is a built-in Magic & Sci-fi shader', () => {
    expect(getShader('PortalVortex')).toMatchObject({ builtin: true, category: 'Magic & Sci-fi' });
  });

  it('replaces the material with a fogged, mostly opaque disc that writes depth and casts no shadow', async () => {
    const c = await compilePatched('PortalVortex', { mesh: 'plane' });
    expect(c.warnings).toEqual([]);
    const portal = c.material as ShaderMaterial;
    expect(portal).toBeInstanceOf(ShaderMaterial);
    expect(portal.name).toBe(PREVIEW_MATERIAL);
    expect(portal.fog).toBe(true);
    expect(portal.transparent).toBe(true);
    expect(portal.depthWrite).toBe(true);
    expect(portal.blending).not.toBe(NormalBlending);
    expect(unbound(portal)).toEqual([]);
    expect(portal.uniforms.uTime).toBe(c.time);
    expect(portal.uniforms.uOpen!.value).toBe(1);
    expect(resolveIncludes(portal.fragmentShader)).toContain('fogColor * fogAmount * gl_FragColor.a');
    expect(c.mesh.castShadow).toBe(false);
  });

  it('draws with one shared, tileable noise texture (billow, its gradient, ridged detail)', async () => {
    const a = await compilePatched('PortalVortex');
    const b = await compilePatched('PortalVortex');
    const noise = (a.material as ShaderMaterial).uniforms.uNoise!.value as DataTexture;
    expect(noise).toBeInstanceOf(DataTexture);
    expect((b.material as ShaderMaterial).uniforms.uNoise!.value).toBe(noise);
    expect([noise.image.width, noise.image.height]).toEqual([128, 128]);
    expect(noise.wrapS).toBe(RepeatWrapping);
    expect(noise.generateMipmaps).toBe(true);
    const data = noise.image.data as Uint8Array;
    const mean = (channel: number): number => {
      let sum = 0;
      for (let i = channel; i < data.length; i += 4) sum += data[i]!;
      return sum / (data.length / 4) / 255;
    };
    expect(mean(0)).toBeGreaterThan(0.2); // billowy clouds, not empty
    expect(mean(0)).toBeLessThan(0.5);
    expect(mean(1)).toBeCloseTo(0.5, 1); // gradients centre on 0.5
    expect(mean(3)).toBeGreaterThan(0.5); // ridged detail is mostly bright
  });

  it('works on instanced and skinned meshes', async () => {
    const instanced = await compilePatched('PortalVortex', { instanced: true });
    expect(resolveIncludes(instanced.vertexShader)).toContain('world = instanceMatrix * world;');
    const skinned = await compilePatched('PortalVortex', { skinned: true, mesh: 'character' });
    expect(resolveIncludes(skinned.vertexShader)).toContain('transformed = ( bindMatrixInverse * skinned ).xyz;');
  });

  it('takes live values per entity: one portal opens, the other stays as it was', async () => {
    const c = await compilePatched('PortalVortex', { values: { open: 0 } });
    c.shaders.set(PREVIEW_MATERIAL, { speed: 2 });
    expect((c.material as ShaderMaterial).uniforms.uSpeed!.value).toBe(2);
    c.shaders.set(c.entity, { open: 0.6, destination: [1, 0.5, 0.2, 1] });
    const own = c.mesh.material as ShaderMaterial;
    expect(own).not.toBe(c.material);
    expect(own.uniforms.uOpen!.value).toBe(0.6);
    expect(own.uniforms.uDestination!.value.toArray()).toEqual([1, 0.5, 0.2, 1]);
    expect(own.uniforms.uSpeed!.value).toBe(2);
    expect((c.material as ShaderMaterial).uniforms.uOpen!.value).toBe(0);
    expect(own.fragmentShader).toBe((c.material as ShaderMaterial).fragmentShader);
    expect(c.warnings).toEqual([]);
  });
});
