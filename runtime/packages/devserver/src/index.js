// @ts-check
// SPDX-License-Identifier: MIT
// The `gamestudio()` Vite plugin (docs/SPEC.md §5): starts the live-link hub,
// writes .gamestudio/session.json and components.json, serves scene exports (never the
// rest of .gamestudio/, which holds tokens),
// provides `virtual:gamestudio/session`, makes systems/components/shaders hot-swappable,
// warms the game's modules up while the server starts and forwards build errors to
// editors (build-errors.js). Builds use relative URLs, so they work from any folder, and get
// THIRD-PARTY-NOTICES.md (notices.js).

import { randomBytes } from 'node:crypto';
import { existsSync } from 'node:fs';
import { createRequire } from 'node:module';
import path from 'node:path';
import { normalizePath } from 'vite';
import { forwardBuildErrors } from './build-errors.js';
import { compressExports, formatSummary } from './compress.js';
import { copyExports, EXPORT_URL_PATH, exportMiddleware } from './export-files.js';
import { createHub } from './hub.js';
import { collectNotices, completeNotices, NOTICES_FILE, VITE_LICENSE_FILE } from './notices.js';
import { checkSceneExports, gsDir, readProjectConfig, removeSessionFileSync, writeRegistryFile, writeSessionFile } from './project.js';
import { generateRegistry, registryCounts } from './registry.js';
import { createSourceMapper } from './sourcemap.js';

export { createHub, PROTOCOL } from './hub.js';
export { NOTICES_FILE } from './notices.js';
export { checkSceneExports } from './project.js';

/** @typedef {import('../types/index.js').GamestudioPluginOptions} GamestudioPluginOptions */
/** @typedef {import('../types/index.js').GamestudioPluginAPI} GamestudioPluginAPI */
/** @typedef {import('../types/index.js').RegistryResult} RegistryResult */
/** @typedef {import('../types/index.js').Hub} Hub */

const VIRTUAL_SESSION = 'virtual:gamestudio/session';
const RESOLVED_SESSION = `\0${VIRTUAL_SESSION}`;
const HMR_ACCEPT = '\nif (import.meta.hot) import.meta.hot.accept();\n';
const REGEN_DEBOUNCE_MS = 100;
// Longest a dev server's first registry generation waits for the first page's modules.
const REGISTRY_WAIT_MS = 3000;
// The engine is TypeScript source (excluded from pre-bundling), so Vite would find its dependencies
// only while the page loads, then re-optimize and reload it. Listing them makes the first
// optimization complete.
export const ENGINE_DEPS = [
  'three',
  'three/webgpu',
  '@dimforge/rapier3d-compat',
  // Navigation (Recast WASM): imported only by scenes with a NavMesh or NavAgent, so a chunk of its own.
  'recast-navigation',
  'recast-navigation/generators',
  'three/addons/environments/RoomEnvironment.js',
  'three/addons/loaders/GLTFLoader.js',
  'three/addons/libs/meshopt_decoder.module.js',
  'three/addons/utils/SkeletonUtils.js',
  'three/addons/postprocessing/EffectComposer.js',
  'three/addons/postprocessing/OutputPass.js',
  'three/addons/postprocessing/RenderPass.js',
  'three/addons/postprocessing/ShaderPass.js',
  'three/addons/postprocessing/UnrealBloomPass.js',
];

const SOURCES = '**/*.{ts,tsx,mts,js,jsx,mjs}';

// Vite's own `server.fs.deny`. A configured list replaces it, so deniedFiles() repeats it.
const VITE_FS_DENY = ['.env', '.env.*', '*.{crt,pem,key,p12,pfx,cer,der}', '.npmrc', '.yarnrc.yml', '**/.git/**'];
// The project's agent folders: settings and skills, never game files.
const AGENT_DIRS = ['.claude', '.codex', '.agents'];

/**
 * The files the dev server never serves (`server.fs.deny`): Vite's defaults, every .gamestudio
 * folder and the project's agent folders. .gamestudio holds the Blender bridge's and the hub's
 * tokens (blender.json, session.json), which would let a page run code in Blender; the game
 * loads its exports from /__gs/export/ (export-files.js). The agent folders are matched at the
 * root only, because Claude Code and Codex keep worktrees in such folders and a project
 * checked out in one must still be served.
 * @param {string} root Vite root (absolute).
 * @returns {string[]}
 */
export function deniedFiles(root) {
  // Glob characters in the root path are put in brackets, so they match themselves.
  const base = normalizePath(root).replace(/[*?[{}()!@+]/g, (c) => `[${c}]`);
  return [...VITE_FS_DENY, '**/.gamestudio/**', ...AGENT_DIRS.map((dir) => `${base}/${dir}/**`)];
}

/**
 * The files Vite transforms as soon as the dev server listens (`server.warmup.clientFiles`), so
 * the first page after a start finds them done (Blender's Play starts the server while it exports
 * the scene): the page, the game's entry, its systems, components and shaders, and the engine's
 * entry. Each module's static imports are warmed in turn, so this covers the whole engine. Missing
 * files are left out, so Vite logs no errors for them.
 * @param {string} root Vite root (absolute).
 * @param {GamestudioPluginOptions} [options]
 * @returns {string[]} Globs relative to the root, and the engine entry as an absolute path.
 */
export function warmupFiles(root, options = {}) {
  /** @param {string} dir */
  const sources = (dir) => `${normalizePath(path.relative(root, path.resolve(root, dir))) || '.'}/${SOURCES}`;
  const files = [
    'src/main.{ts,tsx,js,jsx}',
    sources(options.componentsDir ?? 'src/components'),
    sources(options.shadersDir ?? 'src/shaders'),
    sources(options.systemsDir ?? 'src/systems'),
  ];
  if (existsSync(path.join(root, 'index.html'))) files.unshift('index.html');
  const engine = engineEntry(root);
  if (engine) files.push(engine);
  return files;
}

/**
 * `@gamestudio/engine`'s entry file as the project resolves it (through a link: the real path), or
 * null. A file on another drive than the root is left out: Vite's warmup cannot address it, and
 * the game's entry reaches it anyway.
 * @param {string} root
 */
function engineEntry(root) {
  try {
    const file = createRequire(path.join(root, 'package.json')).resolve('@gamestudio/engine');
    return path.isAbsolute(path.relative(root, file)) ? null : normalizePath(file);
  } catch {
    return null; // not installed: the page reports that itself
  }
}

/**
 * Resolves once Vite has transformed the static imports of the client's first modules (the warmup
 * and the first page), or after `ms` at the latest.
 * @param {import('vite').ViteDevServer} server
 * @param {number} ms
 * @returns {Promise<void>}
 */
function clientIdle(server, ms) {
  return new Promise((resolve) => {
    const timer = setTimeout(resolve, ms);
    timer.unref(); // never keeps a closed server's process alive
    const done = () => {
      clearTimeout(timer);
      resolve();
    };
    server.environments.client.waitForRequestsIdle().then(done, done);
  });
}

/**
 * @param {GamestudioPluginOptions} [options]
 * @returns {import('vite').Plugin & { api: GamestudioPluginAPI }}
 */
export function gamestudio(options = {}) {
  const token = randomBytes(16).toString('hex');
  /** @type {import('vite').ResolvedConfig} */
  let config;
  let root = '';
  let componentsDir = '';
  let shadersDir = '';
  let systemsDir = '';
  /** @type {import('../types/index.js').ProjectConfig} */
  let project;
  /** @type {Hub | null} */
  let hub = null;
  /** @type {Promise<Hub | null>} */
  let hubReady = Promise.resolve(null);
  /** @type {import('vite').ViteDevServer | null} */
  let devServer = null;
  /** @type {Promise<RegistryResult | null>} */
  let registryJob = Promise.resolve(null);
  /** @type {ReturnType<typeof forwardBuildErrors> | null} */
  let buildErrors = null;

  /** Queues a registry regeneration (serialized; each run evaluates the files afresh). */
  function regenerate() {
    const server = devServer;
    if (!server) return Promise.reject(new Error('gamestudio: registry generation needs a Vite dev server'));
    const logger = config.logger;
    const job = registryJob.then(async () => {
      const result = await generateRegistry(server, root, componentsDir, shadersDir);
      for (const { file, message } of result.errors) {
        logger.error(`[gamestudio] ${file}: ${message}`);
        buildErrors?.report({ message, id: path.resolve(root, file) });
      }
      const file = path.join(gsDir(root), 'components.json');
      const { changed, registry } = await writeRegistryFile(file, result.registry);
      if (changed) {
        logger.info(`[gamestudio] components.json: ${registryCounts(registry)}`, { timestamp: true });
      }
      hub?.setRegistry(registry);
      return { registry, errors: result.errors };
    });
    registryJob = job.catch((error) => {
      logger.error(`[gamestudio] registry generation failed: ${error instanceof Error ? error.stack : String(error)}`);
      return null;
    });
    return job;
  }

  /** True when `id` (a module id or file path) is inside `dir`. @param {string} id @param {string} dir */
  const isUnder = (id, dir) => {
    const file = normalizePath(id.split('?')[0] ?? '');
    const prefix = `${normalizePath(dir)}/`;
    return process.platform === 'win32' ? file.toLowerCase().startsWith(prefix.toLowerCase()) : file.startsWith(prefix);
  };

  /** @type {GamestudioPluginAPI} */
  const api = {
    generateRegistry: () => regenerate(),
    get hub() {
      return hub;
    },
  };

  return {
    name: 'gamestudio',
    api,

    config(userConfig, env) {
      // Only a server that listens serves pages (not the registry bin's middleware mode).
      const listens = env.command === 'serve' && !env.isPreview && !userConfig.server?.middlewareMode;
      const viteRoot = userConfig.root ? path.resolve(userConfig.root) : process.cwd();
      return {
        // A build loads its files by relative URLs (./assets/, ./__gs/export/), so it works from any
        // folder: itch.io and GitHub Pages serve games from one. A project's own `base` wins. The dev
        // server keeps '/'.
        ...(env.command === 'build' && userConfig.base === undefined ? { base: './' } : {}),
        // Engine is TypeScript source: let Vite transform it (also in SSR, where the
        // registry generator must share one instance of its schema module). It is not
        // pre-bundled, so edits to the engine apply at once (a pre-bundled copy would only
        // change after a restart); the warmup below makes up for its many modules.
        ssr: { noExternal: ['@gamestudio/engine'] },
        optimizeDeps: { exclude: ['@gamestudio/engine'], include: ENGINE_DEPS },
        // A project that links the engine with `file:` sees the engine's own copies of its
        // dependencies too; the game and the engine must share one Three.js and one Rapier.
        resolve: { dedupe: ['three', '@dimforge/rapier3d-compat'] },
        server: {
          // Generated files never trigger reloads.
          watch: { ignored: ['**/.gamestudio/**'] },
          // Nor are they served: .gamestudio holds the bridge and hub tokens.
          fs: { deny: deniedFiles(viteRoot) },
          ...(listens ? { warmup: { clientFiles: warmupFiles(viteRoot, options) } } : {}),
        },
        build: {
          // Rapier's compat build inlines its WASM (~2 MB), so big chunks are expected.
          chunkSizeWarningLimit: 8000,
          // The licences of what the game bundles, in a plain file at the root of the output
          // (generateBundle below completes it). A project's own `build.license` wins.
          ...(userConfig.build?.license === undefined ? { license: { fileName: NOTICES_FILE } } : {}),
        },
      };
    },

    configResolved(resolved) {
      config = resolved;
      root = resolved.root;
      componentsDir = path.resolve(root, options.componentsDir ?? 'src/components');
      shadersDir = path.resolve(root, options.shadersDir ?? 'src/shaders');
      systemsDir = path.resolve(root, options.systemsDir ?? 'src/systems');
      project = readProjectConfig(root, resolved.logger);
    },

    resolveId(id) {
      return id === VIRTUAL_SESSION ? RESOLVED_SESSION : undefined;
    },

    async load(id) {
      if (id !== RESOLVED_SESSION) return undefined;
      const activeHub = config.command === 'serve' ? await hubReady : null;
      const session = {
        gamePort: activeHub ? activeHub.gamePort : null,
        token: activeHub ? token : null,
        startScene: project.startScene,
        name: project.name,
        post: project.post,
      };
      return [
        `export const gamePort = ${JSON.stringify(session.gamePort)};`,
        `export const token = ${JSON.stringify(session.token)};`,
        `export const startScene = ${JSON.stringify(session.startScene)};`,
        `export const name = ${JSON.stringify(session.name)};`,
        `export const post = ${JSON.stringify(session.post)};`,
        'export default { gamePort, token, startScene, name, post };',
      ].join('\n');
    },

    // Systems, components and shaders accept their own updates: editing one re-runs
    // only that module, whose defineSystem/defineComponent/defineShader call replaces
    // the old one while the world, physics and renderer keep running.
    transform(code, id, transformOptions) {
      if (config.command !== 'serve' || transformOptions?.ssr) return undefined;
      if (!/\.(m?[jt]sx?)$/.test(id.split('?')[0] ?? '')) return undefined;
      if (!isUnder(id, systemsDir) && !isUnder(id, componentsDir) && !isUnder(id, shadersDir)) return undefined;
      if (code.includes('import.meta.hot.accept(')) return undefined;
      return { code: code + HMR_ACCEPT, map: null };
    },

    configureServer(server) {
      devServer = server;
      const logger = config.logger;
      const exportDir = path.join(gsDir(root), 'export');

      // Scene exports, at <base>__gs/export/ (and /__gs/export/ when base isn't '/').
      const mounts = new Set([`/${EXPORT_URL_PATH}`, `${config.base}${EXPORT_URL_PATH}`]);
      for (const mount of mounts) server.middlewares.use(mount, exportMiddleware(exportDir));

      // Regenerate the registry when component or shader files change.
      /** @type {ReturnType<typeof setTimeout> | null} */
      let timer = null;
      server.watcher.on('all', (_event, file) => {
        if (!isUnder(file, componentsDir) && !isUnder(file, shadersDir)) return;
        if (timer) clearTimeout(timer);
        timer = setTimeout(() => {
          timer = null;
          regenerate().catch(() => {});
        }, REGEN_DEBOUNCE_MS);
      });

      // Middleware mode (e.g. the gamestudio-registry bin) never listens: no hub, no session.
      const httpServer = server.httpServer;
      if (!httpServer) return;

      // Modules Vite fails to transform reach editors as `error` messages with their location.
      buildErrors = forwardBuildErrors(logger, root, (message) => hub?.broadcast(message, ['editor', 'tool']));

      const sourceMapper = createSourceMapper(server, root);
      hubReady = createHub({
        editorPort: project.link.editorPort,
        gamePort: project.link.gamePort,
        token,
        logger: {
          info: (msg) => logger.info(msg, { timestamp: true }),
          warn: (msg) => logger.warn(msg, { timestamp: true }),
          error: (msg) => logger.error(msg, { timestamp: true }),
        },
        transform: (message, from) => (from.role === 'game' ? sourceMapper(message) : message),
      }).then(
        (created) => {
          hub = created;
          return created;
        },
        (error) => {
          logger.error(`[gamestudio] live-link hub failed to start: ${error instanceof Error ? error.message : String(error)}`);
          return null;
        },
      );

      const sessionFile = path.join(gsDir(root), 'session.json');
      httpServer.once('listening', async () => {
        const activeHub = await hubReady;
        const address = httpServer.address();
        const port = address && typeof address === 'object' ? address.port : config.server.port;
        const protocol = config.server.https ? 'https' : 'http';
        if (activeHub) {
          await writeSessionFile(sessionFile, {
            pid: process.pid,
            url: `${protocol}://localhost:${port}${config.base}`,
            editorPort: activeHub.editorPort,
            gamePort: activeHub.gamePort,
            token,
            startedAt: new Date().toISOString(),
          });
          logger.info(
            `[gamestudio] live link: editors tcp://127.0.0.1:${activeHub.editorPort}, games ws://127.0.0.1:${activeHub.gamePort}`,
            { timestamp: true },
          );
        }
        // The registry is evaluated in SSR on this same thread, so it waits until the first
        // page's modules are transformed (server.warmup); editors get it when it is ready.
        void clientIdle(server, REGISTRY_WAIT_MS).then(() => {
          if (httpServer.listening) regenerate().catch(() => {});
        });
        const missing = checkSceneExports(root).filter((p) => p.problem === 'missing');
        if (missing.length) {
          logger.info(`[gamestudio] not exported yet: ${missing.map((p) => p.file).join(', ')}`, { timestamp: true });
        }
      });

      const cleanup = () => removeSessionFileSync(sessionFile, token);
      process.once('exit', cleanup);
      httpServer.once('close', () => {
        buildErrors?.restore();
        cleanup();
        process.off('exit', cleanup);
        void hubReady.then((h) => h?.close());
      });
    },

    // After Vite's licence file is made (`build.license`): add what it leaves out (notices.js), such as
    // the engine linked from outside node_modules, its notices for borrowed shader code and font licences.
    generateBundle: {
      order: 'post',
      handler(_output, bundle) {
        const option = this.environment.config.build.license;
        if (config.command !== 'build' || !option) return;
        const fileName = typeof option === 'object' ? option.fileName : VITE_LICENSE_FILE;
        const asset = bundle[fileName];
        if (asset?.type !== 'asset') return;
        const moduleIds = Object.values(bundle).flatMap((file) => (file.type === 'chunk' ? file.moduleIds : []));
        const { notices, unlicensedFonts } = collectNotices(root, moduleIds, path.join(gsDir(root), 'export'));
        for (const font of unlicensedFonts) {
          config.logger.warn(
            `[gamestudio] ${font} has no licence file next to it (<name>-OFL.txt or OFL.txt), so ${fileName} cannot list it`,
          );
        }
        const source = typeof asset.source === 'string' ? asset.source : Buffer.from(asset.source).toString('utf8');
        asset.source = completeNotices(source, fileName, notices);
      },
    },

    async writeBundle() {
      if (config.command !== 'build') return;
      const outDir = path.resolve(root, config.build.outDir);
      const copied = await copyExports(path.join(gsDir(root), 'export'), outDir);
      if (!copied) config.logger.warn('[gamestudio] no .gamestudio/export folder; the build has no scenes');
      else {
        // Smaller builds: the copied scene GLBs get meshopt geometry compression (compress.js).
        const results = await compressExports(path.join(outDir, ...EXPORT_URL_PATH.split('/')), project.compression);
        for (const line of formatSummary(results, project.compression)) config.logger.info(line);
      }
      for (const { file, scene, problem } of checkSceneExports(root)) {
        config.logger.warn(
          problem === 'missing'
            ? `[gamestudio] ${file} has no export (${scene}.scene.json): export it, or the build cannot load '${scene}'`
            : `[gamestudio] ${file} changed after its last export: the build has the old '${scene}'`,
        );
      }
    },
  };
}

export default gamestudio;
