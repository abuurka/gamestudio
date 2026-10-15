// The built-in shader library's conventions, checked for every shader in src/shaders/library: a
// folder per shader, registered (built in, with a library category) wherever the schema loads, its
// GLSL behind `load`, fields a level designer understands, and a preview the gallery can show.
import { describe, expect, it } from 'vitest';
import { libraryFolders } from '../../src/shaders/library';
import { getShader, libraryShaders, SHADER_CATEGORIES, toRegistryJSON } from '../../src/schema';

const kebab = (name: string): string => name.replace(/([a-z0-9])([A-Z])/g, '$1-$2').replace(/([A-Z])([A-Z][a-z])/g, '$1-$2').toLowerCase();
const MESHES = ['sphere', 'box', 'plane', 'cylinder', 'character', 'tree', 'grass', 'terrain', 'flag', 'building', 'gem'];

describe('the shader library', () => {
  const shaders = libraryShaders();

  it('has Triplanar, the reference shader', () => {
    expect(shaders.map((s) => s.name)).toContain('Triplanar');
  });

  for (const shader of shaders) {
    describe(shader.name, () => {
      it('is registered as a built-in shader of a library category, in a folder named after it', () => {
        expect(getShader(shader.name)).toBe(shader);
        expect(shader.builtin).toBe(true);
        expect(SHADER_CATEGORIES).toContain(shader.category);
        expect(libraryFolders()[shader.name]).toBe(kebab(shader.name));
        expect(shader.description?.length).toBeGreaterThan(20);
        const entry = toRegistryJSON().shaders.find((s) => s.name === shader.name)!;
        expect(entry).toMatchObject({ name: shader.name, category: shader.category, builtin: true });
        expect(entry.source).toBeUndefined();
      });

      it('keeps its GLSL in impl.ts behind load', async () => {
        expect(typeof shader.load).toBe('function');
        expect(shader.apply).toBeUndefined();
        expect(shader.mesh).toBeUndefined();
        const impl = await shader.load!();
        expect(typeof impl.apply === 'function' || typeof impl.mesh === 'function').toBe(true);
      });

      it('describes every field, and its preview uses its own fields', () => {
        for (const [name, def] of Object.entries(shader.fields)) {
          expect(def.description, `${shader.name}.${name} has no description`).toBeTruthy();
        }
        const preview = shader.preview ?? {};
        if (preview.mesh) expect(MESHES).toContain(preview.mesh);
        if (preview.camera) expect(['near', 'far', 'top', 'eye']).toContain(preview.camera);
        for (const key of Object.keys(preview.values ?? {})) expect(Object.keys(shader.fields)).toContain(key);
        if (preview.color) {
          expect(preview.color).toHaveLength(4); // linear RGBA
          expect(preview.color.every((c) => Number.isFinite(c) && c >= 0)).toBe(true);
        }
      });
    });
  }
});
