// ParallaxOcclusion: a height march before the material reads its textures, at the displaced UV.
import { DataTexture, MeshBasicMaterial, MeshStandardMaterial, NoColorSpace, RepeatWrapping, type Texture } from 'three';
import { describe, expect, it } from 'vitest';
import { getShader } from '../../src/schema';
import { flagstoneTexture } from '../../src/shaders/library/parallax-occlusion/impl';
import { compilePatched, PREVIEW_MATERIAL, resolveIncludes } from './helpers';

describe('ParallaxOcclusion', () => {
  it('is a built-in Surfaces shader', () => {
    expect(getShader('ParallaxOcclusion')).toMatchObject({ builtin: true, category: 'Surfaces' });
  });

  it('marches before the textures are read, and reads them all at the displaced UV', async () => {
    const map = new DataTexture(new Uint8Array(4).fill(200), 1, 1);
    const roughnessMap = new DataTexture(new Uint8Array(4).fill(200), 1, 1);
    const stone = new MeshStandardMaterial({ name: 'Stone', map, roughnessMap });
    const c = await compilePatched('ParallaxOcclusion', { mesh: 'plane', material: stone });
    expect(c.warnings).toEqual([]);
    expect(c.edits.every((e) => e.matched)).toBe(true);
    expect(c.material.customProgramCacheKey()).toMatch(/\|gs_parallax_occlusion$/);
    const fragment = resolveIncludes(c.fragmentShader);
    const march = fragment.indexOf('gsPomOffset = p - vGsPomUv;');
    expect(march).toBeGreaterThan(0);
    expect(march).toBeLessThan(fragment.indexOf('texture2D( map, ( vMapUv + gsPomOffset ) )'));
    expect(fragment).toContain('texture2D( roughnessMap, ( vRoughnessMapUv + gsPomOffset ) )');
    expect(fragment).toContain('texture2D( normalMap, ( vNormalMapUv + gsPomOffset ) )');
    expect(fragment).not.toMatch(/texture2D\( map, vMapUv \)/);
    // The normal bends after the material's own normal map, the self-shadow dims direct light only.
    expect(fragment.indexOf('normal = normalize(normal - gsPomBend);')).toBeGreaterThan(fragment.indexOf('normal = normalize( tbn * mapN );'));
    expect(fragment).toContain('reflectedLight.directDiffuse *= gsPomLight;');
    expect(c.uniforms.uPomSteps.value).toBe(24);
    expect(c.uniforms.uPomDepth.value).toBe(0.05);
  });

  it('uses the procedural flagstones until a height map is set, which loads as linear data', async () => {
    const c = await compilePatched('ParallaxOcclusion');
    expect(c.uniforms.uPomHeight.value).toBe(flagstoneTexture());
    expect(c.uniforms.uPomProcedural.value).toBe(1);
    const h = await compilePatched('ParallaxOcclusion', { values: { height: 'textures/bricks_height.png' } });
    const height = h.uniforms.uPomHeight.value as Texture;
    expect(height.name).toBe('textures/bricks_height.png');
    expect(height.colorSpace).toBe(NoColorSpace);
    expect(h.uniforms.uPomProcedural.value).toBe(0);
  });

  it('bakes tileable flagstones once', () => {
    const t = flagstoneTexture();
    expect(flagstoneTexture()).toBe(t);
    expect([t.wrapS, t.wrapT]).toEqual([RepeatWrapping, RepeatWrapping]);
    const { data, width, height } = t.image as { data: Uint8Array; width: number; height: number };
    const at = (x: number, y: number): number => data[(y * width + x) * 4]!;
    let low = 255;
    let high = 0;
    let step = 0; // the largest jump between neighbours inside the texture
    let seam = 0; // ... and across its wrap, both ways
    for (let y = 0; y < height; y++) {
      for (let x = 0; x < width; x++) {
        low = Math.min(low, at(x, y));
        high = Math.max(high, at(x, y));
        if (x > 0) step = Math.max(step, Math.abs(at(x, y) - at(x - 1, y)));
      }
      seam = Math.max(seam, Math.abs(at(0, y) - at(width - 1, y)), Math.abs(at(y, 0) - at(y, height - 1)));
    }
    expect(low).toBeLessThan(100); // joints and cracks
    expect(high).toBeGreaterThan(200); // stone tops
    expect(seam).toBeLessThanOrEqual(step); // no cliff along the wrap
  });

  it('works on instanced and skinned meshes', async () => {
    const instanced = await compilePatched('ParallaxOcclusion', { instanced: true });
    expect(instanced.edits.every((e) => e.matched)).toBe(true);
    expect(instanced.vertexShader).toContain('vGsPomUv = uv;');
    const skinned = await compilePatched('ParallaxOcclusion', { skinned: true, mesh: 'character' });
    expect(skinned.edits.every((e) => e.matched)).toBe(true);
  });

  it('takes live values', async () => {
    const c = await compilePatched('ParallaxOcclusion');
    c.shaders.set(PREVIEW_MATERIAL, { depth: 0.1, steps: 40 });
    expect(c.uniforms.uPomDepth.value).toBe(0.1);
    expect(c.uniforms.uPomSteps.value).toBe(40);
    c.shaders.set(c.entity, { gaps: 0.9 });
    const own = c.compile(c.mesh.material as MeshStandardMaterial);
    expect(own.uniforms.uPomGaps.value).toBe(0.9);
    expect(c.uniforms.uPomGaps.value).toBe(0.5);
    expect(own.fragmentShader).toBe(c.fragmentShader);
    expect(c.warnings).toEqual([]);
  });

  it('fails (keeping the Blender look) on an unlit material', async () => {
    await expect(compilePatched('ParallaxOcclusion', { material: new MeshBasicMaterial({ name: 'Flat' }) })).rejects.toThrow(
      'ParallaxOcclusion needs a lit (Principled BSDF) material',
    );
  });
});
