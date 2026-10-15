// The built-in shader library (API.md §28): each `<name>/meta.ts` defines one game shader with its
// fields and a `load` of its `impl.ts` (all the GLSL), which a build splits into a chunk of its own
// that only games using the shader download. `../../schema.ts` imports this module, so the library
// is registered wherever the schema is (the game, the dev server's registry, tests). To add a
// shader, add a folder: there is no list to edit.

import type { ShaderType } from '../../schema/shaders';

const metas = import.meta.glob<Record<string, unknown>>('./*/meta.ts', { eager: true });

/** The library's shader definitions as their meta files made them, sorted by name. */
export function libraryShaders(): ShaderType[] {
  const out: ShaderType[] = [];
  for (const module of Object.values(metas)) {
    for (const value of Object.values(module)) {
      const shader = value as Partial<ShaderType> | null;
      if (shader && typeof shader === 'object' && typeof shader.name === 'string' && shader.fields && 'builtin' in shader) out.push(shader as ShaderType);
    }
  }
  return out.sort((a, b) => (a.name < b.name ? -1 : a.name > b.name ? 1 : 0));
}

/** The folder of each library shader (`water` for Water), for tools. */
export function libraryFolders(): Record<string, string> {
  const out: Record<string, string> = {};
  for (const [file, module] of Object.entries(metas)) {
    const folder = file.split('/')[1]!;
    for (const value of Object.values(module)) {
      const name = (value as { name?: unknown } | null)?.name;
      if (typeof name === 'string' && (value as { fields?: unknown }).fields) out[name] = folder;
    }
  }
  return out;
}
