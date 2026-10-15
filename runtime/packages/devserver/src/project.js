// @ts-check
// Project files: gamestudio.json (input) and .gamestudio/* (generated).

import { mkdir, readFile, rename, rm, writeFile } from 'node:fs/promises';
import { readdirSync, readFileSync, rmSync, statSync } from 'node:fs';
import path from 'node:path';
import { compressionConfig, DEFAULT_COMPRESSION } from './compress.js';

/** @typedef {import('../types/index.js').ProjectConfig} ProjectConfig */

/** @type {ProjectConfig} */
const DEFAULTS = {
  name: 'game',
  formatVersion: 1,
  startScene: 'main',
  link: { editorPort: 7710, gamePort: 7711 },
  post: null,
  compression: { ...DEFAULT_COMPRESSION },
};

/**
 * Reads `<root>/gamestudio.json`, filling defaults. Missing file → defaults plus a warning.
 * @param {string} root
 * @param {{ warn(message: string): void }} [logger]
 * @returns {ProjectConfig}
 */
export function readProjectConfig(root, logger) {
  const file = path.join(root, 'gamestudio.json');
  /** @type {any} */
  let data = {};
  try {
    data = JSON.parse(readFileSync(file, 'utf8'));
  } catch (error) {
    const code = /** @type {NodeJS.ErrnoException} */ (error).code;
    logger?.warn(
      code === 'ENOENT'
        ? `[gamestudio] ${file} not found; using defaults`
        : `[gamestudio] could not read ${file}: ${String(error)}; using defaults`,
    );
  }
  const link = data && typeof data.link === 'object' && data.link ? data.link : {};
  return {
    name: typeof data.name === 'string' ? data.name : path.basename(root),
    formatVersion: typeof data.formatVersion === 'number' ? data.formatVersion : DEFAULTS.formatVersion,
    startScene: typeof data.startScene === 'string' && data.startScene ? data.startScene : DEFAULTS.startScene,
    link: {
      editorPort: Number.isInteger(link.editorPort) ? link.editorPort : DEFAULTS.link.editorPort,
      gamePort: Number.isInteger(link.gamePort) ? link.gamePort : DEFAULTS.link.gamePort,
    },
    post: data && typeof data.post === 'object' && data.post && !Array.isArray(data.post) ? data.post : null,
    compression: compressionConfig(data?.compression, logger),
  };
}

/**
 * Level files (`scenes/*.blend`) whose export is missing or older than the file. A level exports
 * under its file name (`scenes/level2.blend` -> `level2.scene.json`).
 * @param {string} root
 * @returns {import('../types/index.js').SceneExportProblem[]}
 */
export function checkSceneExports(root) {
  const scenes = path.join(root, 'scenes');
  /** @type {string[]} */
  let files = [];
  try {
    files = readdirSync(scenes).filter((f) => f.toLowerCase().endsWith('.blend')).sort();
  } catch {
    return [];
  }
  /** @type {import('../types/index.js').SceneExportProblem[]} */
  const problems = [];
  for (const file of files) {
    const scene = file.slice(0, -'.blend'.length).replace(/[^A-Za-z0-9_.-]+/g, '_').replace(/^[._]+|[._]+$/g, '') || 'scene';
    const manifest = path.join(gsDir(root), 'export', `${scene}.scene.json`);
    /** @type {'missing' | 'stale' | null} */
    let problem = null;
    try {
      problem = statSync(manifest).mtimeMs + 1000 < statSync(path.join(scenes, file)).mtimeMs ? 'stale' : null;
    } catch {
      problem = 'missing';
    }
    if (problem) problems.push({ file: `scenes/${file}`, scene, problem });
  }
  return problems;
}

/** @param {string} root */
export const gsDir = (root) => path.join(root, '.gamestudio');

/**
 * Writes a file atomically (temp file + rename), retrying briefly when another
 * process (e.g. Blender reading it) holds it open on Windows.
 * @param {string} file
 * @param {string} text
 */
export async function writeFileAtomic(file, text) {
  await mkdir(path.dirname(file), { recursive: true });
  const tmp = `${file}.${process.pid}.tmp`;
  await writeFile(tmp, text, 'utf8');
  for (let attempt = 0; ; attempt++) {
    try {
      await rename(tmp, file);
      return;
    } catch (error) {
      if (attempt >= 5) {
        await rm(tmp, { force: true });
        await writeFile(file, text, 'utf8'); // Last resort: non-atomic.
        return;
      }
      await new Promise((resolve) => setTimeout(resolve, 50 * (attempt + 1)));
    }
  }
}

/**
 * Writes `components.json` unless only `generatedAt` would change, so the file
 * (and anything watching it) stays stable across server restarts.
 * @param {string} file
 * @param {import('../types/index.js').RegistryJSON} registry
 * @returns {Promise<{ changed: boolean, registry: import('../types/index.js').RegistryJSON }>}
 */
export async function writeRegistryFile(file, registry) {
  try {
    const previous = JSON.parse(await readFile(file, 'utf8'));
    // A file from an older dev server has no `shaders`: it is rewritten with them.
    const content = (/** @type {any} */ r) => JSON.stringify([r.components, r.shaders ?? null]);
    if (content(previous) === content(registry)) {
      return { changed: false, registry: previous };
    }
  } catch {
    // Missing or unreadable: write a fresh one.
  }
  await writeFileAtomic(file, `${JSON.stringify(registry, null, 2)}\n`);
  return { changed: true, registry };
}

/**
 * @param {string} file
 * @param {object} session
 */
export async function writeSessionFile(file, session) {
  await writeFileAtomic(file, `${JSON.stringify(session, null, 2)}\n`);
}

/**
 * Removes session.json if it still belongs to this server (same token).
 * Synchronous so it can run during process exit.
 * @param {string} file
 * @param {string} token
 */
export function removeSessionFileSync(file, token) {
  try {
    const current = JSON.parse(readFileSync(file, 'utf8'));
    if (current.token === token) rmSync(file, { force: true });
  } catch {
    // Already gone or unreadable.
  }
}
