import http from 'node:http';
import { existsSync, mkdtempSync, mkdirSync, readFileSync, rmSync, symlinkSync, utimesSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { build, createLogger, createServer, normalizePath, type ConfigEnv, type UserConfig } from 'vite';
import { afterAll, describe, expect, it, vi } from 'vitest';
import { copyExports, exportMiddleware } from '../src/export-files.js';
import { checkSceneExports, readProjectConfig } from '../src/project.js';
import { deniedFiles, ENGINE_DEPS, gamestudio, NOTICES_FILE, warmupFiles } from '../src/index.js';
import { readdirSync } from 'node:fs';
import { decodeMappings, originalPosition } from '../src/sourcemap.js';
import type { GamestudioPluginAPI } from '../types/index.js';

const DEMO = fileURLToPath(new URL('../../../../examples/demo-game/', import.meta.url));

describe('export middleware', () => {
  const dir = mkdtempSync(path.join(tmpdir(), 'gs-export-'));
  writeFileSync(path.join(dir, 'main.scene.json'), '{"format":"gamestudio.scene"}');
  writeFileSync(path.join(dir, 'main.glb'), Buffer.from('glTF'));
  writeFileSync(path.join(dir, 'main.prefabs.glb'), Buffer.from('glTF prefabs'));
  mkdirSync(path.join(dir, 'files', 'assets', 'sfx'), { recursive: true });
  writeFileSync(path.join(dir, 'files', 'assets', 'sfx', 'coin.ogg'), Buffer.from('OggS'));
  mkdirSync(path.join(dir, 'ui'));
  writeFileSync(path.join(dir, 'ui', 'index.json'), '{"format":"gamestudio.ui-index"}');
  const server = http.createServer((req, res) => {
    req.url = req.url!.replace(/^\/__gs\/export/, '');
    exportMiddleware(dir)(req as never, res, () => {
      res.statusCode = 599;
      res.end();
    });
  });
  const ready = new Promise<number>((r) => server.listen(0, '127.0.0.1', () => r((server.address() as { port: number }).port)));
  afterAll(() => {
    server.close();
    rmSync(dir, { recursive: true, force: true });
  });
  const get = async (p: string) => fetch(`http://127.0.0.1:${await ready}${p}`);

  it('serves exports with MIME types and no caching', async () => {
    const json = await get('/__gs/export/main.scene.json');
    expect(json.status).toBe(200);
    expect(json.headers.get('content-type')).toMatch(/^application\/json/);
    expect(json.headers.get('cache-control')).toBe('no-store');
    const glb = await get('/__gs/export/main.glb?rev=3');
    expect(glb.headers.get('content-type')).toBe('model/gltf-binary');
    expect(await glb.text()).toBe('glTF');
  });

  it('serves prefab GLBs and copied asset files (versioned URLs from the manifest)', async () => {
    const prefabs = await get('/__gs/export/main.prefabs.glb?rev=4');
    expect(prefabs.headers.get('content-type')).toBe('model/gltf-binary');
    const sound = await get('/__gs/export/files/assets/sfx/coin.ogg?v=3f2a9c01b7');
    expect(sound.status).toBe(200);
    expect(sound.headers.get('content-type')).toBe('audio/ogg');
    expect(await sound.text()).toBe('OggS');
  });

  it('copies prefab GLBs and asset files into builds', async () => {
    const out = mkdtempSync(path.join(tmpdir(), 'gs-build-'));
    try {
      expect(await copyExports(dir, out)).toBe(true);
      const exported = path.join(out, '__gs', 'export');
      expect(readFileSync(path.join(exported, 'main.prefabs.glb'), 'utf8')).toBe('glTF prefabs');
      expect(readFileSync(path.join(exported, 'files', 'assets', 'sfx', 'coin.ogg'), 'utf8')).toBe('OggS');
      expect(readFileSync(path.join(exported, 'ui', 'index.json'), 'utf8')).toContain('gamestudio.ui-index'); // Game UI
    } finally {
      rmSync(out, { recursive: true, force: true });
    }
  });

  it('writes the empty UI index into builds of games without Blender UI', async () => {
    const plain = mkdtempSync(path.join(tmpdir(), 'gs-export-noui-'));
    const out = mkdtempSync(path.join(tmpdir(), 'gs-build-'));
    try {
      writeFileSync(path.join(plain, 'main.scene.json'), '{"format":"gamestudio.scene"}');
      expect(await copyExports(plain, out)).toBe(true);
      const index = JSON.parse(readFileSync(path.join(out, '__gs', 'export', 'ui', 'index.json'), 'utf8'));
      expect(index).toEqual({ format: 'gamestudio.ui-index', formatVersion: 1, canvases: {} });
    } finally {
      rmSync(plain, { recursive: true, force: true });
      rmSync(out, { recursive: true, force: true });
    }
  });

  it('answers 404 for missing files and refuses path traversal', async () => {
    expect((await get('/__gs/export/nope.scene.json')).status).toBe(404);
    expect((await get('/__gs/export/..%2f..%2fsecret')).status).toBe(403);
  });
});

describe('source maps', () => {
  it('decodes VLQ mappings and finds original positions', () => {
    // Line 1: col 0 → src line 0 col 0; col 4 → src line 2 col 2. Line 2: col 0 → src line 5 col 0.
    const lines = decodeMappings('AAAA,IAEE;AAGF');
    expect(lines).toEqual([
      [[0, 0, 0, 0], [4, 0, 2, 2]],
      [[0, 0, 5, 0]],
    ]);
    expect(originalPosition(lines, 1, 7)).toEqual({ line: 3, col: 3 });
    expect(originalPosition(lines, 2, 1)).toEqual({ line: 6, col: 1 });
    expect(originalPosition(lines, 9, 1)).toBeNull();
  });
});

describe('gamestudio() plugin', () => {
  it('generates the registry from the demo through Vite SSR, with sources', async () => {
    const server = await createServer({
      root: DEMO,
      logLevel: 'silent',
      appType: 'custom',
      server: { middlewareMode: true, hmr: false, watch: null },
      optimizeDeps: { noDiscovery: true, include: [] },
    });
    try {
      const plugin = server.config.plugins.find((p) => p.name === 'gamestudio');
      const api = plugin!.api as GamestudioPluginAPI;
      expect(api.hub).toBeNull(); // Middleware mode never starts the hub.
      const { registry, errors } = await api.generateRegistry();
      expect(errors).toEqual([]);
      const byName = Object.fromEntries(registry.components.map((c) => [c.name, c]));
      expect(byName.Coin).toMatchObject({ builtin: false, source: 'src/components/coin.ts', fields: [{ name: 'value', type: 'int', default: 1 }] });
      expect(byName.BouncePad).toMatchObject({ source: 'src/components/bounce-pad.ts', fields: [{ name: 'strength', type: 'float', default: 12 }] });
      expect(byName.KillZone).toMatchObject({ fields: [] });
      expect(byName.RigidBody).toMatchObject({ builtin: true });
      expect(byName.RigidBody).not.toHaveProperty('source');
      expect(registry.components.map((c) => c.name)).toEqual(registry.components.map((c) => c.name).sort());

      // A second run re-evaluates files: a newly added component file shows up.
      const extra = path.join(DEMO, 'src', 'components', 'zz-test-extra.ts');
      mkdirSync(path.dirname(extra), { recursive: true });
      writeFileSync(extra, "import { defineComponent } from '@gamestudio/engine/schema';\nexport const ZzExtra = defineComponent('ZzExtra');\n");
      try {
        const second = await api.generateRegistry();
        expect(second.registry.components.find((c) => c.name === 'ZzExtra')).toMatchObject({ source: 'src/components/zz-test-extra.ts' });
      } finally {
        rmSync(extra, { force: true });
      }
      const third = await api.generateRegistry();
      expect(third.registry.components.some((c) => c.name === 'ZzExtra')).toBe(false); // Deleted → gone.
      expect(Array.isArray(third.registry.shaders)).toBe(true); // always written, [] without src/shaders

      // Systems and components self-accept hot updates; other modules don't.
      const accept = 'import.meta.hot.accept()';
      expect((await server.transformRequest('/src/systems/coins.ts'))!.code).toContain(accept);
      expect((await server.transformRequest('/src/components/coin.ts'))!.code).toContain(accept);
      expect((await server.transformRequest('/src/main.ts'))!.code).not.toContain(accept);
      expect((await server.transformRequest('/src/lib/contact.ts'))!.code).not.toContain(accept);

      // Without a listening server there is no hub: the session disables the link.
      const session = await server.environments.client.pluginContainer.load('\0virtual:gamestudio/session');
      const code = typeof session === 'string' ? session : String(session?.code);
      expect(code).toContain('export const gamePort = null;');
      expect(code).toContain('export const token = null;');
      expect(code).toContain('export const startScene = "main";');
      expect(code).toContain('export const name = "demo-game";');
      expect(code).toContain('export const post = null;');
    } finally {
      await server.close();
    }
  });
});

describe('served files', () => {
  it('never serves .gamestudio (the bridge and hub tokens) or agent folders, keeps Vite\'s own denials and still serves exports', async () => {
    // In a Claude Code worktree folder, with glob characters in its name: agent folders count at the root only.
    const base = mkdtempSync(path.join(tmpdir(), 'gs-deny-'));
    const root = path.join(base, '.claude', 'worktrees', 'try (1)', 'game');
    const secret = 'feedfacefeedfacefeedfacefeedface';
    const files: Record<string, string> = {
      'gamestudio.json': JSON.stringify({ name: 'deny', link: { editorPort: 0, gamePort: 0 } }),
      'index.html': '<script type="module" src="/src/main.ts"></script>\n',
      'src/main.ts': 'export const answer: number = 42;\n',
      'notes.json': '{"public":true}',
      '.gamestudio/blender.json': JSON.stringify({ pid: 1, port: 9, token: secret }),
      '.gamestudio/export/main.scene.json': '{"format":"gamestudio.scene"}',
      '.env': `TOKEN=${secret}\n`,
      '.claude/settings.local.json': JSON.stringify({ token: secret }),
      '.agents/skills/x/SKILL.md': secret,
    };
    for (const [file, text] of Object.entries(files)) {
      mkdirSync(path.dirname(path.join(root, file)), { recursive: true });
      writeFileSync(path.join(root, file), text);
    }
    expect(deniedFiles(root)).toEqual(expect.arrayContaining(['.env', '.env.*', '**/.git/**', '**/.gamestudio/**']));
    const server = await createServer({
      root,
      configFile: false,
      logLevel: 'silent',
      plugins: [gamestudio()],
      server: { port: 0, host: '127.0.0.1', watch: null },
    });
    try {
      await server.listen();
      const port = (server.httpServer!.address() as { port: number }).port;
      const get = (p: string) => fetch(`http://127.0.0.1:${port}${p}`);
      const session = path.join(root, '.gamestudio', 'session.json');
      await vi.waitFor(() => expect(readFileSync(session, 'utf8')).toContain('token'), { timeout: 15_000, interval: 50 });
      const hubToken = JSON.parse(readFileSync(session, 'utf8')).token as string;

      const abs = normalizePath(root);
      for (const p of ['/.gamestudio/blender.json', '/.gamestudio/session.json', '/.gamestudio/export/main.scene.json',
        '/.env', '/.claude/settings.local.json', '/.agents/skills/x/SKILL.md',
        // Other ways of asking for the same files.
        '/.gamestudio/blender.json?import', '/.gamestudio/blender.json?raw', '/.gamestudio/session.json?url',
        '/%2Egamestudio/blender.json', `/@fs/${abs}/.gamestudio/blender.json`]) {
        const res = await get(p);
        const text = await res.text();
        expect(res.status, p).toBe(403);
        expect(text, p).not.toContain(secret);
        expect(text, p).not.toContain(hubToken);
      }

      const scene = await get('/__gs/export/main.scene.json'); // where the game loads exports from
      expect(scene.status).toBe(200);
      expect(await scene.json()).toEqual({ format: 'gamestudio.scene' });
      expect(await (await get('/notes.json')).json()).toEqual({ public: true }); // the rest of the project as before
      expect(await (await get('/src/main.ts')).text()).toContain('42');
    } finally {
      await server.close();
      rmSync(base, { recursive: true, force: true });
    }
  });
});

describe('project scenes', () => {
  it('reads "post" from gamestudio.json and finds level files without an up-to-date export', () => {
    const root = mkdtempSync(path.join(tmpdir(), 'gs-scenes-'));
    try {
      writeFileSync(path.join(root, 'gamestudio.json'), JSON.stringify({ name: 'levels', startScene: 'main', post: { bloom: true } }));
      expect(readProjectConfig(root)).toMatchObject({ name: 'levels', startScene: 'main', post: { bloom: true } });
      expect(checkSceneExports(root)).toEqual([]); // no scenes folder
      mkdirSync(path.join(root, 'scenes'));
      mkdirSync(path.join(root, '.gamestudio', 'export'), { recursive: true });
      for (const name of ['main', 'level2', 'boss fight']) writeFileSync(path.join(root, 'scenes', `${name}.blend`), 'BLENDER');
      writeFileSync(path.join(root, '.gamestudio', 'export', 'main.scene.json'), '{}');
      writeFileSync(path.join(root, '.gamestudio', 'export', 'level2.scene.json'), '{}');
      const old = new Date(Date.now() - 60_000);
      utimesSync(path.join(root, '.gamestudio', 'export', 'level2.scene.json'), old, old);
      expect(checkSceneExports(root)).toEqual([
        { file: 'scenes/boss fight.blend', scene: 'boss_fight', problem: 'missing' },
        { file: 'scenes/level2.blend', scene: 'level2', problem: 'stale' },
      ]);
    } finally {
      rmSync(root, { recursive: true, force: true });
    }
  });
});

describe('warmup', () => {
  type ConfigHook = (config: UserConfig, env: ConfigEnv) => UserConfig;
  const configOf = (user: UserConfig, env: ConfigEnv) => (gamestudio().config as unknown as ConfigHook)(user, env);
  const serve: ConfigEnv = { command: 'serve', mode: 'development' };
  const SOURCES = '**/*.{ts,tsx,mts,js,jsx,mjs}';

  it('warms the page, the entry, systems, components, shaders and the engine entry up when the dev server listens', () => {
    const files = configOf({ root: DEMO }, serve).server?.warmup?.clientFiles ?? [];
    expect(files.slice(0, -1)).toEqual([
      'index.html',
      'src/main.{ts,tsx,js,jsx}',
      `src/components/${SOURCES}`,
      `src/shaders/${SOURCES}`,
      `src/systems/${SOURCES}`,
    ]);
    // The engine as node_modules links it: its real path, as Vite serves it.
    const engine = normalizePath(fileURLToPath(new URL('../../engine/src/index.ts', import.meta.url)));
    expect(files.at(-1)?.toLowerCase()).toBe(engine.toLowerCase());
    // Middleware mode (the gamestudio-registry bin) and builds warm nothing up.
    expect(configOf({ root: DEMO, server: { middlewareMode: true } }, serve).server?.warmup).toBeUndefined();
    expect(configOf({ root: DEMO }, { command: 'build', mode: 'production' }).server?.warmup).toBeUndefined();
  });

  it('follows the folder options and leaves out what the project lacks', () => {
    const root = mkdtempSync(path.join(tmpdir(), 'gs-warmup-'));
    try {
      // No index.html, no engine installed (unless the temp folder is inside this checkout, which links it).
      const options = { componentsDir: 'game/components', shadersDir: 'game/looks', systemsDir: path.join(root, 'game', 'systems') };
      const files = warmupFiles(root, options).filter((f) => !f.toLowerCase().endsWith('/engine/src/index.ts'));
      expect(files).toEqual([
        'src/main.{ts,tsx,js,jsx}',
        `game/components/${SOURCES}`,
        `game/looks/${SOURCES}`,
        `game/systems/${SOURCES}`,
      ]);
    } finally {
      rmSync(root, { recursive: true, force: true });
    }
  });

  it('transforms the game modules as soon as the server listens, before a page asks for them', async () => {
    const root = mkdtempSync(path.join(tmpdir(), 'gs-warmup-'));
    mkdirSync(path.join(root, 'src', 'systems'), { recursive: true });
    mkdirSync(path.join(root, 'src', 'lib'));
    writeFileSync(path.join(root, 'gamestudio.json'), JSON.stringify({ name: 'warm', link: { editorPort: 0, gamePort: 0 } }));
    writeFileSync(path.join(root, 'index.html'), '<script type="module" src="/src/main.ts"></script>\n');
    writeFileSync(path.join(root, 'src', 'main.ts'), "import { speed } from './lib/speed.ts';\nimport.meta.glob('./systems/*.ts', { eager: true });\nexport const s: number = speed;\n");
    writeFileSync(path.join(root, 'src', 'lib', 'speed.ts'), 'export const speed: number = 2;\n');
    writeFileSync(path.join(root, 'src', 'systems', 'turn.ts'), 'export const turn: number = 1;\n');
    const server = await createServer({
      root,
      configFile: false,
      logLevel: 'silent',
      plugins: [gamestudio()],
      server: { port: 0, host: '127.0.0.1', watch: null },
    });
    try {
      await server.listen();
      const graph = server.environments.client.moduleGraph;
      const code = async (url: string) => (await graph.getModuleByUrl(url))?.transformResult?.code;
      await vi.waitFor(
        async () => {
          for (const url of ['/src/main.ts', '/src/lib/speed.ts', '/src/systems/turn.ts']) expect(await code(url)).toBeTruthy();
        },
        { timeout: 15_000, interval: 50 },
      );
      expect(await code('/src/systems/turn.ts')).toContain('import.meta.hot.accept()'); // still hot-swappable
    } finally {
      await server.close();
      rmSync(root, { recursive: true, force: true });
    }
  });
});

describe('web builds', () => {
  type ConfigHook = (config: UserConfig, env: ConfigEnv) => UserConfig;
  const configOf = (user: UserConfig, env: ConfigEnv) => (gamestudio().config as unknown as ConfigHook)(user, env);
  const production: ConfigEnv = { command: 'build', mode: 'production' };

  it('builds with relative URLs unless the project sets base; the dev server keeps /', () => {
    expect(configOf({ root: DEMO }, production).base).toBe('./');
    expect(configOf({ root: DEMO, base: '/games/demo/' }, production)).not.toHaveProperty('base');
    expect(configOf({ root: DEMO, base: '' }, production)).not.toHaveProperty('base');
    expect(configOf({ root: DEMO }, { command: 'serve', mode: 'development' })).not.toHaveProperty('base');
    expect(configOf({ root: DEMO }, { command: 'serve', mode: 'production', isPreview: true })).not.toHaveProperty('base');
  });

  it('writes a game that loads from any folder (itch.io, GitHub Pages): nothing is asked for at the site root', async () => {
    const root = mkdtempSync(path.join(tmpdir(), 'gs-base-'));
    const files: Record<string, string | Buffer> = {
      'gamestudio.json': JSON.stringify({ name: 'base' }),
      'index.html': '<link rel="stylesheet" href="/src/style.css">\n<script type="module" src="/src/main.js"></script>\n',
      // The engine's way to its exports, a lazy chunk and an imported asset.
      'src/main.js':
        "import beep from './beep.wav?url';\n" +
        'fetch(`${import.meta.env.BASE_URL}__gs/export/main.scene.json`);\n' +
        "import('./later.js').then((m) => console.log(m.later, beep));\n",
      'src/later.js': "export const later = 'later';\n",
      'src/beep.wav': Buffer.alloc(8192, 1),
      'src/style.css': "@font-face { font-family: Game; src: url('./game.woff2'); }\nbody { font-family: Game; }\n",
      'src/game.woff2': Buffer.alloc(8192, 2), // above Vite's inline limit, so it is a file
      '.gamestudio/export/main.scene.json': '{"format":"gamestudio.scene"}',
    };
    for (const [file, data] of Object.entries(files)) {
      mkdirSync(path.dirname(path.join(root, file)), { recursive: true });
      writeFileSync(path.join(root, file), data);
    }
    const read = (dir: string, ext: string) =>
      readdirSync(path.join(root, dir, 'assets')).filter((f) => f.endsWith(ext)).map((f) => readFileSync(path.join(root, dir, 'assets', f), 'utf8')).join('\n');
    try {
      await build({ root, configFile: false, logLevel: 'silent', plugins: [gamestudio()] });
      const html = readFileSync(path.join(root, 'dist', 'index.html'), 'utf8');
      expect(html).toMatch(/<script type="module" crossorigin src="\.\/assets\/index-[\w-]+\.js"><\/script>/);
      expect(html).toMatch(/<link rel="stylesheet" crossorigin href="\.\/assets\/index-[\w-]+\.css">/);
      expect(read('dist', '.css')).toMatch(/url\(\.\/game-[\w-]+\.woff2\)/);
      const js = read('dist', '.js');
      expect(js).toContain('./__gs/export/');
      expect(js).toMatch(/new URL\([`'"]beep-[\w-]+\.wav[`'"],\s*import\.meta\.url\)/);
      expect(js).not.toMatch(/["'`]\/(assets|__gs)\//);
      // The exports sit next to index.html, where ./__gs/export/ finds them.
      expect(existsSync(path.join(root, 'dist', '__gs', 'export', 'main.scene.json'))).toBe(true);

      // A project's own base wins.
      await build({ root, configFile: false, logLevel: 'silent', plugins: [gamestudio()], base: '/games/base/', build: { outDir: 'dist-base' } });
      expect(readFileSync(path.join(root, 'dist-base', 'index.html'), 'utf8')).toContain('src="/games/base/assets/');
      expect(read('dist-base', '.js')).toContain('/games/base/__gs/export/');
    } finally {
      rmSync(root, { recursive: true, force: true });
    }
  }, 60_000);
});

describe('engine dependencies', () => {
  it('pre-bundles every third-party module the engine imports (no re-optimization and reload on first load)', () => {
    const src = fileURLToPath(new URL('../../engine/src/', import.meta.url));
    const files = readdirSync(src, { recursive: true, encoding: 'utf8' }).filter((f) => f.endsWith('.ts'));
    const found = new Set<string>();
    for (const file of files) {
      const code = readFileSync(path.join(src, file), 'utf8');
      for (const m of code.matchAll(/(?:from|import\()\s*'((?:three|@dimforge|recast-navigation)[^']*)'/g)) found.add(m[1]!);
    }
    expect([...found].filter((dep) => !ENGINE_DEPS.includes(dep))).toEqual([]);
  });
});

describe('third-party notices', () => {
  type ConfigHook = (config: UserConfig, env: ConfigEnv) => UserConfig;
  const configOf = (user: UserConfig) => (gamestudio().config as unknown as ConfigHook)(user, { command: 'build', mode: 'production' });
  const ENGINE = fileURLToPath(new URL('../../engine/', import.meta.url));
  const write = (file: string, text: string) => {
    mkdirSync(path.dirname(file), { recursive: true });
    writeFileSync(file, text);
  };

  /** A game that bundles a package from node_modules and one linked from outside it (as `file:` links the engine). */
  function makeGame(main: string) {
    const root = mkdtempSync(path.join(tmpdir(), 'gs-notices-'));
    const linked = mkdtempSync(path.join(tmpdir(), 'gs-linked-'));
    write(path.join(root, 'package.json'), JSON.stringify({ name: 'notices-game', version: '1.0.0', private: true, type: 'module' }));
    write(path.join(root, 'gamestudio.json'), JSON.stringify({ name: 'notices' }));
    write(path.join(root, 'index.html'), '<script type="module" src="/src/main.js"></script>\n');
    write(path.join(root, 'src', 'main.js'), main);
    const plain = path.join(root, 'node_modules', 'plain-lib');
    write(path.join(plain, 'package.json'), JSON.stringify({ name: 'plain-lib', version: '1.2.3', license: 'Apache-2.0', type: 'module', main: 'index.js' }));
    write(path.join(plain, 'index.js'), "export const plain = 'plain';\n");
    write(path.join(plain, 'LICENSE'), 'Apache License text of plain-lib');
    write(path.join(plain, 'NOTICE'), 'Plain Lib\nCopyright 2026 The Plain Authors');
    write(path.join(linked, 'package.json'), JSON.stringify({ name: 'linked-lib', version: '0.1.0', license: 'MIT', type: 'module', main: 'index.js' }));
    write(path.join(linked, 'index.js'), "export const linked = 'linked';\n");
    write(path.join(linked, 'LICENSE'), 'MIT licence text of linked-lib');
    write(path.join(linked, 'THIRD-PARTY-NOTICES.md'), '# Third-party notices\n\nCode by others.\n\n## Borrowed noise\n\nCopyright (c) 2014 A. Noise.\n');
    symlinkSync(linked, path.join(root, 'node_modules', 'linked-lib'), 'junction');
    // Scene exports with Game UI fonts: Inter with its licence next to it, another font without one.
    const fonts = path.join(root, '.gamestudio', 'export', 'files', 'assets', 'fonts');
    write(path.join(fonts, 'Inter.woff2'), 'wOF2');
    write(path.join(fonts, 'Inter-OFL.txt'), 'Copyright (c) 2016 The Inter Project Authors\n\nSIL OPEN FONT LICENSE Version 1.1 - 26 February 2007');
    write(path.join(fonts, 'Mystery.ttf'), 'font');
    return { root, cleanup: () => [root, linked].forEach((dir) => rmSync(dir, { recursive: true, force: true })) };
  }

  async function buildGame(root: string, user: UserConfig['build'] = {}) {
    const warnings: string[] = [];
    const logger = createLogger('silent');
    logger.warn = (msg) => void warnings.push(msg);
    await build({ root, configFile: false, logLevel: 'silent', customLogger: logger, plugins: [gamestudio()], build: user });
    return warnings;
  }

  it('asks Vite for THIRD-PARTY-NOTICES.md at the root of the output, unless the project sets build.license', () => {
    expect(NOTICES_FILE).toBe('THIRD-PARTY-NOTICES.md');
    expect(configOf({ root: DEMO }).build?.license).toEqual({ fileName: 'THIRD-PARTY-NOTICES.md' });
    expect(configOf({ root: DEMO, build: { license: false } }).build).not.toHaveProperty('license');
    expect(configOf({ root: DEMO, build: { license: { fileName: 'licenses.json' } } }).build).not.toHaveProperty('license');
  });

  it('lists node_modules and linked packages, their notices files and the exported fonts\' licences', async () => {
    const game = makeGame("import { plain } from 'plain-lib';\nimport { linked } from 'linked-lib';\nconsole.log(plain, linked);\n");
    try {
      const warnings = await buildGame(game.root);
      const dist = path.join(game.root, 'dist');
      const text = readFileSync(path.join(dist, 'THIRD-PARTY-NOTICES.md'), 'utf8');
      expect(existsSync(path.join(dist, '.vite', 'license.md'))).toBe(false);
      // Vite's own list: packages in node_modules with their licence files.
      expect(text).toMatch(/^# Licenses\n/);
      expect(text).toContain('## plain-lib - 1.2.3 (Apache-2.0)\n\nApache License text of plain-lib\n');
      // What the plugin adds: the linked package (Vite looks only inside node_modules)...
      expect(text).toContain('## linked-lib - 0.1.0 (MIT)\n\nMIT licence text of linked-lib\n');
      // ...the packages' notices files, a Markdown one with its headings one level down...
      expect(text).toContain('## Code from other authors in linked-lib (THIRD-PARTY-NOTICES.md)\n\nCode by others.\n\n### Borrowed noise\n');
      expect(text).not.toContain('# Third-party notices');
      expect(text).toContain('## Code from other authors in plain-lib (NOTICE)\n\nPlain Lib\nCopyright 2026 The Plain Authors\n');
      // ...and the licences of the fonts in the exports, which the build copies to __gs/export/.
      expect(text).toContain('## Font: __gs/export/files/assets/fonts/Inter.woff2 (OFL-1.1)\n\nCopyright (c) 2016 The Inter Project Authors');
      expect(existsSync(path.join(dist, '__gs', 'export', 'files', 'assets', 'fonts', 'Inter-OFL.txt'))).toBe(true);
      expect(warnings.join('\n')).toContain('__gs/export/files/assets/fonts/Mystery.ttf has no licence file');
      // The game's own code is not a third-party package.
      expect(text).not.toContain('notices-game');
    } finally {
      game.cleanup();
    }
  });

  it('follows a project\'s own build.license: none, or Vite\'s JSON with the linked packages added', async () => {
    const game = makeGame("import { linked } from 'linked-lib';\nconsole.log(linked);\n");
    try {
      await buildGame(game.root, { license: false });
      expect(readdirSync(path.join(game.root, 'dist')).filter((f) => /licen|notice/i.test(f))).toEqual([]);
      await buildGame(game.root, { license: { fileName: 'licenses.json' } });
      const entries = JSON.parse(readFileSync(path.join(game.root, 'dist', 'licenses.json'), 'utf8'));
      expect(entries).toContainEqual({ name: 'linked-lib', version: '0.1.0', identifier: 'MIT', text: 'MIT licence text of linked-lib' });
      expect(existsSync(path.join(game.root, 'dist', NOTICES_FILE))).toBe(false);
    } finally {
      game.cleanup();
    }
  });

  it('lists the engine when node_modules links it from outside, with its notices for borrowed shader code', async () => {
    const game = makeGame("import { libraryShaders } from '@gamestudio/engine/schema';\nconsole.log(libraryShaders.length);\n");
    try {
      mkdirSync(path.join(game.root, 'node_modules', '@gamestudio'));
      symlinkSync(ENGINE, path.join(game.root, 'node_modules', '@gamestudio', 'engine'), 'junction');
      await buildGame(game.root);
      const text = readFileSync(path.join(game.root, 'dist', NOTICES_FILE), 'utf8');
      expect(text).toContain('## @gamestudio/engine - 0.1.0 (MIT)\n\nMIT License\n');
      expect(text).toContain('## Code from other authors in @gamestudio/engine (THIRD-PARTY-NOTICES.md)');
      expect(text).toContain('### webgl-noise (3D simplex noise)');
      expect(text).toContain('Copyright (C) 2011 by Ashima Arts (Simplex noise)');
      expect(text).toContain('### Hash without Sine');
      expect(text).toContain('Copyright (c) 2014 David Hoskins.');
    } finally {
      game.cleanup();
    }
  }, 60_000);
});
