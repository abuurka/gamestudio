// Game shaders in the registry (docs/SPEC.md §3): src/shaders/*.ts is evaluated after the
// components, each shader is attributed to its file, the folder is watched and hot-swappable. The
// engine's shader library is listed too (built in); a project file may override a library shader.
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { createServer, type Alias } from 'vite';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { writeRegistryFile } from '../src/project.js';
import { gamestudio } from '../src/index.js';
import type { GamestudioPluginAPI, RegistryJSON } from '../types/index.js';

// The engine's source, so a temporary project needs no node_modules of its own.
const ENGINE = fileURLToPath(new URL('../../engine/src/', import.meta.url));
const alias: Alias[] = [
  { find: /^@gamestudio\/engine\/schema$/, replacement: path.join(ENGINE, 'schema.ts') },
  { find: /^@gamestudio\/engine$/, replacement: path.join(ENGINE, 'index.ts') },
];

const TRIPLANAR = `import { defineShader, field, patchShader } from '@gamestudio/engine';

export const Triplanar = defineShader('Triplanar', {
  description: 'World-space photo scans with wetness',
  fields: {
    tile: field.float(3.2, { min: 0.01 }),
    wet: field.float(0, { min: 0, max: 1 }),
  },
  apply(material) {
    patchShader(material, 'triplanar', () => {});
  },
});
`;
const shader = (name: string) =>
  `import { defineShader, field } from '@gamestudio/engine/schema';\nexport const ${name} = defineShader('${name}', { fields: { speed: field.float(1) } });\n`;

/** The project's own shaders (the registry also lists the built-in library). */
const own = (shaders: RegistryJSON['shaders']) => shaders.filter((s) => !s.builtin);

let root = '';
/** A temporary project with src/components and src/shaders. */
function makeProject(files: Record<string, string>) {
  root = mkdtempSync(path.join(tmpdir(), 'gs-shaders-'));
  writeFileSync(path.join(root, 'gamestudio.json'), JSON.stringify({ name: 'shaders', link: { editorPort: 0, gamePort: 0 } }));
  for (const [rel, text] of Object.entries(files)) {
    mkdirSync(path.dirname(path.join(root, rel)), { recursive: true });
    writeFileSync(path.join(root, rel), text);
  }
  return root;
}
afterEach(() => {
  if (root) rmSync(root, { recursive: true, force: true });
  root = '';
});

describe('game shaders in the registry', () => {
  it('loads src/shaders/* after the components and attributes each shader to its file', async () => {
    makeProject({
      'src/components/drift.ts': "import { defineComponent, field } from '@gamestudio/engine/schema';\nexport const Drift = defineComponent('Drift', { fields: { speed: field.float(1) } });\n",
      'src/shaders/triplanar.ts': TRIPLANAR, // overrides the library's Triplanar
      'src/shaders/ripples.ts': shader('Ripples'),
      'src/shaders/broken.ts': "throw new Error('shader file failed');\n",
      'src/shaders/types.d.ts': 'export type Ignored = 1;\n',
      'src/shaders/nested/deep.ts': shader('Deep'), // not recursive, like import.meta.glob('./shaders/*.ts')
    });
    const server = await createServer({
      root,
      configFile: false,
      logLevel: 'silent',
      appType: 'custom',
      plugins: [gamestudio()],
      resolve: { alias },
      server: { middlewareMode: true, hmr: false, watch: null },
      optimizeDeps: { noDiscovery: true, include: [] },
    });
    try {
      const api = server.config.plugins.find((p) => p.name === 'gamestudio')!.api as GamestudioPluginAPI;
      const { registry, errors } = await api.generateRegistry();
      expect(errors).toEqual([{ file: 'src/shaders/broken.ts', message: expect.stringContaining('shader file failed') }]);
      expect(registry.components.find((c) => c.name === 'Drift')).toMatchObject({ source: 'src/components/drift.ts' });
      expect(own(registry.shaders).map((s) => s.name)).toEqual(['Ripples', 'Triplanar']);
      expect(registry.shaders.filter((s) => s.name === 'Triplanar')).toHaveLength(1);
      expect(registry.shaders.find((s) => s.name === 'Triplanar')).toMatchObject({
        name: 'Triplanar',
        description: 'World-space photo scans with wetness',
        builtin: false,
        source: 'src/shaders/triplanar.ts',
        fields: [
          { name: 'tile', type: 'float', default: 3.2, min: 0.01 },
          { name: 'wet', type: 'float', default: 0, min: 0, max: 1 },
        ],
      });
      expect(registry.shaders.find((s) => s.name === 'Ripples')).toMatchObject({ builtin: false, source: 'src/shaders/ripples.ts' });
      for (const builtin of registry.shaders.filter((s) => s.builtin)) {
        expect(builtin.category).toBeTruthy();
        expect(builtin.source).toBeUndefined();
      }

      // The file on disk has them too, and the next run drops a deleted shader file's shader.
      const file = path.join(root, '.gamestudio', 'components.json');
      const written = () => own(JSON.parse(readFileSync(file, 'utf8')).shaders).map((s) => s.name);
      expect(written()).toEqual(['Ripples', 'Triplanar']);
      rmSync(path.join(root, 'src', 'shaders', 'ripples.ts'));
      const second = await api.generateRegistry();
      expect(own(second.registry.shaders).map((s) => s.name)).toEqual(['Triplanar']);
      expect(written()).toEqual(['Triplanar']);

      // Shader files accept their own hot updates, like components and systems.
      expect((await server.transformRequest('/src/shaders/triplanar.ts'))!.code).toContain('import.meta.hot.accept()');

      // Without the project's file the library's Triplanar is back.
      rmSync(path.join(root, 'src', 'shaders', 'triplanar.ts'));
      const third = await api.generateRegistry();
      expect(third.registry.shaders.find((s) => s.name === 'Triplanar')).toMatchObject({ builtin: true, category: 'Surfaces' });
      expect(third.registry.shaders.find((s) => s.name === 'Triplanar')!.source).toBeUndefined();
    } finally {
      await server.close();
    }
  });

  it('follows the shadersDir option, and writes "shaders": [] without a shaders folder', async () => {
    makeProject({ 'game/looks/glow.ts': shader('Glow'), 'src/shaders/ignored.ts': shader('Ignored') });
    const options = { appType: 'custom' as const, configFile: false as const, logLevel: 'silent' as const, resolve: { alias } };
    const server = await createServer({
      ...options,
      root,
      plugins: [gamestudio({ shadersDir: 'game/looks' })],
      server: { middlewareMode: true, hmr: false, watch: null },
      optimizeDeps: { noDiscovery: true, include: [] },
    });
    try {
      const api = server.config.plugins.find((p) => p.name === 'gamestudio')!.api as GamestudioPluginAPI;
      const { registry } = await api.generateRegistry();
      expect(own(registry.shaders)).toEqual([expect.objectContaining({ name: 'Glow', source: 'game/looks/glow.ts' })]);
      rmSync(path.join(root, 'game'), { recursive: true });
      expect(own((await api.generateRegistry()).registry.shaders)).toEqual([]);
      const written = JSON.parse(readFileSync(path.join(root, '.gamestudio', 'components.json'), 'utf8')) as RegistryJSON;
      expect(Array.isArray(written.shaders)).toBe(true);
      expect(own(written.shaders)).toEqual([]);
    } finally {
      await server.close();
    }
  });

  it('regenerates components.json when a shader file is added or changed', async () => {
    makeProject({ 'src/shaders/puddle.ts': shader('Puddle') });
    const server = await createServer({
      root,
      configFile: false,
      logLevel: 'silent',
      plugins: [gamestudio()],
      resolve: { alias },
      server: { port: 0, host: '127.0.0.1' },
    });
    const file = path.join(root, '.gamestudio', 'components.json');
    const shaders = () => {
      if (!existsSync(file)) return [];
      return own((JSON.parse(readFileSync(file, 'utf8')) as RegistryJSON).shaders).map((s) => `${s.name}:${s.fields.map((f) => f.name).join(',')}`);
    };
    try {
      await server.listen();
      await vi.waitFor(() => expect(shaders()).toEqual(['Puddle:speed']), { timeout: 20_000, interval: 100 });
      writeFileSync(path.join(root, 'src', 'shaders', 'embers.ts'), shader('Embers'));
      await vi.waitFor(() => expect(shaders()).toEqual(['Embers:speed', 'Puddle:speed']), { timeout: 20_000, interval: 100 });
      writeFileSync(path.join(root, 'src', 'shaders', 'puddle.ts'), shader('Puddle').replace('speed', 'depth'));
      await vi.waitFor(() => expect(shaders()).toEqual(['Embers:speed', 'Puddle:depth']), { timeout: 20_000, interval: 100 });
    } finally {
      await server.close();
    }
  }, 60_000);
});

describe('components.json', () => {
  const registry = (shaders: RegistryJSON['shaders']): RegistryJSON => ({
    format: 'gamestudio.registry',
    formatVersion: 1,
    generatedAt: new Date().toISOString(),
    components: [{ name: 'Spin', builtin: true, fields: [] }],
    shaders,
  });

  it('is rewritten when only the shaders change, and when an older file has no shaders key', async () => {
    const dir = mkdtempSync(path.join(tmpdir(), 'gs-registry-'));
    try {
      const file = path.join(dir, 'components.json');
      expect((await writeRegistryFile(file, registry([]))).changed).toBe(true);
      expect((await writeRegistryFile(file, registry([]))).changed).toBe(false); // only generatedAt differs
      const water = { name: 'Water', builtin: false, source: 'src/shaders/water.ts', fields: [] };
      expect((await writeRegistryFile(file, registry([water]))).changed).toBe(true);
      expect(JSON.parse(readFileSync(file, 'utf8')).shaders).toEqual([water]);
      const { shaders: _drop, ...old } = registry([]);
      writeFileSync(file, JSON.stringify(old));
      const rewritten = await writeRegistryFile(file, registry([]));
      expect(rewritten.changed).toBe(true);
      expect(JSON.parse(readFileSync(file, 'utf8')).shaders).toEqual([]);
    } finally {
      rmSync(dir, { recursive: true, force: true });
    }
  });
});
