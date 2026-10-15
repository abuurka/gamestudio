// @ts-check
// Component registry generation (docs/SPEC.md §3). Component and shader files are
// loaded through Vite's SSR runner, so they are TypeScript-aware and share the SAME
// instance of `@gamestudio/engine/schema` as the registry we read.

import { readdir } from 'node:fs/promises';
import path from 'node:path';

/** @typedef {import('../types/index.js').RegistryResult} RegistryResult */
/** @typedef {import('../types/index.js').RegistryError} RegistryError */

const SOURCE_EXT = /\.(m?[jt]s|tsx)$/;

/** @param {string} p */
const toPosix = (p) => p.split(path.sep).join('/');

/**
 * Source files of a definitions folder: `<dir>/*.{ts,js,...}` (not recursive,
 * matching `import.meta.glob('./components/*.ts')` and `'./shaders/*.ts'`),
 * sorted for determinism.
 * @param {string} dir
 * @returns {Promise<string[]>}
 */
export async function listSourceFiles(dir) {
  try {
    const entries = await readdir(dir, { withFileTypes: true });
    return entries
      .filter((e) => e.isFile() && SOURCE_EXT.test(e.name) && !e.name.endsWith('.d.ts'))
      .map((e) => path.join(dir, e.name))
      .sort();
  } catch (error) {
    if (/** @type {NodeJS.ErrnoException} */ (error).code === 'ENOENT') return [];
    throw error;
  }
}

/**
 * Evaluates the engine's schema module, every component file and then every
 * shader file, attributing each component and each shader to the file whose
 * evaluation (re)defined it.
 *
 * @param {import('vite').ViteDevServer} server
 * @param {string} root  Vite root (project root)
 * @param {string} componentsDir  absolute path
 * @param {string} [shadersDir]  absolute path
 * @returns {Promise<RegistryResult>}
 */
export async function generateRegistry(server, root, componentsDir, shadersDir) {
  // Fresh evaluation each time, so deleted or renamed components and shaders disappear.
  server.environments.ssr.moduleGraph.invalidateAll();
  const schema = /** @type {SchemaModule} */ (await server.ssrLoadModule('@gamestudio/engine/schema'));
  // An engine without game shaders (older than this dev server) has no listShaders.
  const listShaders = typeof schema.listShaders === 'function' ? schema.listShaders : () => [];
  /** @param {() => Array<{ name: string }>} list */
  const snapshot = (list) => new Map(list().map((d) => [d.name, d]));

  /** @type {Record<string, string>} */
  const sources = {};
  /** @type {Record<string, string>} */
  const shaderSources = {};
  /** @type {RegistryError[]} */
  const errors = [];
  const files = [...(await listSourceFiles(componentsDir)), ...(shadersDir ? await listSourceFiles(shadersDir) : [])];
  for (const file of files) {
    const relative = toPosix(path.relative(root, file));
    const components = snapshot(schema.listComponents);
    const shaders = snapshot(listShaders);
    try {
      await server.ssrLoadModule(`/${relative}`);
    } catch (error) {
      errors.push({ file: relative, message: error instanceof Error ? error.message : String(error) });
      continue;
    }
    for (const [name, component] of snapshot(schema.listComponents)) {
      if (components.get(name) !== component) sources[name] = relative;
    }
    for (const [name, shader] of snapshot(listShaders)) {
      if (shaders.get(name) !== shader) shaderSources[name] = relative;
    }
  }
  const registry = schema.toRegistryJSON({ sources, shaderSources });
  if (!Array.isArray(registry.shaders)) registry.shaders = []; // always written (SPEC §3)
  return { registry, errors };
}

/**
 * "3 components, 1 shader": what a registry holds, for log lines.
 * @param {{ components: unknown[], shaders?: unknown[] }} registry
 */
export function registryCounts(registry) {
  const count = (/** @type {number} */ n, /** @type {string} */ noun) => `${n} ${noun}${n === 1 ? '' : 's'}`;
  return `${count(registry.components.length, 'component')}, ${count(registry.shaders?.length ?? 0, 'shader')}`;
}

/**
 * The parts of `@gamestudio/engine/schema` the generator uses.
 * @typedef {object} SchemaModule
 * @property {() => Array<{ name: string }>} listComponents
 * @property {(() => Array<{ name: string }>) | undefined} listShaders
 * @property {(options: { sources: Record<string, string>, shaderSources: Record<string, string> }) => import('../types/index.js').RegistryJSON} toRegistryJSON
 */
