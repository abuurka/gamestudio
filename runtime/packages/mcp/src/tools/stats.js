// @ts-check
// SPDX-License-Identifier: MIT
// game_stats: what the running game costs: frame times, draw calls, triangles, GPU resources,
// memory, the heaviest meshes, and the project's export budgets. Read-only.

import { existsSync, statSync } from 'node:fs';
import path from 'node:path';
import { normalizeKey } from '../browser.js';
import { ToolError } from '../util.js';
import { readJson } from '../discovery.js';
import { READ, object } from './common.js';

/** @typedef {import('./common.js').Context} Context */

const DEFAULT_SIZE = /** @type {[number, number]} */ ([1280, 720]);
/** gamestudio.json "export" defaults (blender/gamestudio/core/export.py EXPORT_DEFAULTS). */
const EXPORT_DEFAULTS = { warnSizeMB: 32, maxTextureSize: 2048, bakeBudgetMB: 64 };
export const HEADLESS_NOTE =
  'Headless Chromium draws on the GPU when it can (GAMESTUDIO_BROWSER_GPU=auto, the default) and otherwise with ' +
  'SwiftShader (software GL); renderer.gpu says which. With SwiftShader fps and frame times are far below a real GPU; ' +
  'on a GPU they are capped by the headless frame rate. Neither is representative of a player\'s machine. Draw calls, ' +
  'triangles, textures, programs and memory are. Compare numbers before and after a change, at the same size, rather ' +
  'than against absolute targets.';

/**
 * min/avg/p95/max of frame times (ms) and the average fps.
 * @param {number[]} times
 */
export function frameStats(times) {
  if (!times.length) return null;
  const sorted = [...times].sort((a, b) => a - b);
  const avg = times.reduce((s, t) => s + t, 0) / times.length;
  const at = (/** @type {number} */ q) => sorted[Math.min(sorted.length - 1, Math.floor(q * sorted.length))] ?? 0;
  const r = (/** @type {number} */ v) => Math.round(v * 100) / 100;
  return { fps: r(1000 / avg), frames: times.length, min_ms: r(sorted[0] ?? 0), avg_ms: r(avg), p95_ms: r(at(0.95)), max_ms: r(sorted[sorted.length - 1] ?? 0) };
}

/**
 * The export of a scene on disk: <scene>.glb, <scene>.prefabs.glb and the asset files its manifest
 * names (export/files/ is shared by all scenes), like the export's own size report.
 * @param {string} root @param {string} scene
 */
export function exportSize(root, scene) {
  const dir = path.join(root, '.gamestudio', 'export');
  const size = (/** @type {string} */ f) => (existsSync(f) && statSync(f).isFile() ? statSync(f).size : 0);
  const glb = size(path.join(dir, `${scene}.glb`));
  const prefabs = size(path.join(dir, `${scene}.prefabs.glb`));
  const manifest = readJson(path.join(dir, `${scene}.scene.json`));
  let files = 0;
  for (const url of Object.values(manifest?.assets ?? {})) {
    if (typeof url === 'string') files += size(path.join(dir, url.split('?', 1)[0] ?? ''));
  }
  return { bytes: glb + prefabs + files, glb, prefabs, files };
}

/**
 * The project's export budgets (gamestudio.json "export", over the defaults) against what was measured.
 * @param {Record<string, any>} config gamestudio.json
 * @param {{ exportBytes: number, textures: { name: string, width: number, height: number }[] }} measured
 */
export function checkBudgets(config, measured) {
  const budgets = { ...EXPORT_DEFAULTS, ...(config?.export ?? {}) };
  /** @type {Record<string, any>} */
  const out = {};
  /** @type {string[]} */
  const over = [];
  if (budgets.warnSizeMB > 0) {
    const mb = Math.round((measured.exportBytes / 1024 / 1024) * 100) / 100;
    out.warnSizeMB = { limit: budgets.warnSizeMB, actual: mb, over: mb > budgets.warnSizeMB };
    if (mb > budgets.warnSizeMB) over.push(`export size ${mb} MB > warnSizeMB ${budgets.warnSizeMB}`);
  }
  if (budgets.maxTextureSize > 0) {
    const big = measured.textures.filter((t) => Math.max(t.width, t.height) > budgets.maxTextureSize);
    out.maxTextureSize = { limit: budgets.maxTextureSize, largest: Math.max(0, ...measured.textures.map((t) => Math.max(t.width, t.height))), over: big.map((t) => `${t.name} ${t.width}x${t.height}`).slice(0, 10) };
    if (big.length) over.push(`${big.length} texture(s) over maxTextureSize ${budgets.maxTextureSize}`);
  }
  const textureMB = Math.round((measured.textures.reduce((s, t) => s + t.width * t.height * 4 * 1.33, 0) / 1024 / 1024) * 10) / 10;
  out.textureMemory = { estimate_mb: textureMB, bakeBudgetMB: budgets.bakeBudgetMB };
  return { budgets: out, over_budget: over };
}

/** Runs in the page: samples for `seconds`, then describes the renderer and scene. */
/* c8 ignore start */
export function sampleInPage(/** @type {number} */ seconds) {
  const gs = /** @type {any} */ (window).__gs;
  const raw = gs?.game?.three?.renderer?.raw;
  /** @type {number[]} */
  const times = [];
  /** @type {{ calls: number, triangles: number, points: number, lines: number }[]} */
  const renders = [];
  // three.js resets renderer.info on every render() call, so with post effects (bloom) it would show only
  // the last pass. Count every pass of a frame instead: reset once per frame, here.
  const autoReset = raw?.info?.autoReset;
  if (raw?.info) raw.info.autoReset = false;
  return new Promise((resolve) => {
    let last = -1;
    const end = performance.now() + seconds * 1000;
    const tick = (/** @type {number} */ t) => {
      if (last >= 0) {
        times.push(t - last);
        const r = raw?.info?.render;
        if (r) renders.push({ calls: r.calls ?? r.drawCalls ?? 0, triangles: r.triangles ?? 0, points: r.points ?? 0, lines: r.lines ?? 0 });
      }
      raw?.info?.reset?.();
      last = t;
      if (t < end) requestAnimationFrame(tick);
      else resolve(undefined);
    };
    requestAnimationFrame(tick);
  }).then(() => {
    if (raw?.info && autoReset !== undefined) raw.info.autoReset = autoReset;
    /** @type {Map<string, any>} */
    const meshes = new Map();
    /** @type {Map<any, { name: string, width: number, height: number }>} */
    const textures = new Map();
    gs.game.three.scene.traverse((/** @type {any} */ o) => {
      if (!o.isMesh || !o.geometry) return;
      const g = o.geometry;
      const count = g.index ? g.index.count : g.attributes?.position?.count ?? 0;
      const instances = o.isInstancedMesh ? o.count : 1;
      let owner = o;
      let entity = gs.world.entityOf(o);
      while (!entity && owner.parent) {
        owner = owner.parent;
        entity = gs.world.entityOf(owner);
      }
      const key = `${entity?.name ?? '(no entity)'}|${o.name}`;
      const prev = meshes.get(key) ?? { entity: entity?.name ?? null, mesh: o.name || null, triangles: 0, instances: 0 };
      prev.triangles += Math.round((count / 3) * instances);
      prev.instances += instances;
      meshes.set(key, prev);
      for (const material of Array.isArray(o.material) ? o.material : [o.material]) {
        for (const value of Object.values(material ?? {})) {
          const image = /** @type {any} */ (value)?.isTexture ? /** @type {any} */ (value).image : null;
          if (image && image.width && !textures.has(value)) textures.set(value, { name: /** @type {any} */ (value).name || image.src?.slice(-40) || 'texture', width: image.width, height: image.height });
        }
      }
    });
    const avg = (/** @type {string} */ k) => (renders.length ? Math.round(renders.reduce((s, r) => s + /** @type {any} */ (r)[k], 0) / renders.length) : null);
    const max = (/** @type {string} */ k) => (renders.length ? Math.max(...renders.map((r) => /** @type {any} */ (r)[k])) : null);
    const memory = /** @type {any} */ (performance).memory;
    return {
      times,
      scene: gs.game.scene?.name ?? null,
      state: gs.state,
      screen: (() => {
        try {
          return gs.test?.state?.({ ids: [] })?.ui?.screen ?? null;
        } catch {
          return null;
        }
      })(),
      renderer: {
        kind: gs.game.three.renderer?.kind ?? null,
        draw_calls: { avg: avg('calls'), max: max('calls') },
        triangles: { avg: avg('triangles'), max: max('triangles') },
        points: max('points'),
        lines: max('lines'),
        geometries: raw?.info?.memory?.geometries ?? null,
        textures: raw?.info?.memory?.textures ?? null,
        programs: Array.isArray(raw?.info?.programs) ? raw.info.programs.length : null,
      },
      js_heap_mb: memory ? Math.round((memory.usedJSHeapSize / 1024 / 1024) * 10) / 10 : null,
      entities: gs.world.size ?? null,
      bodies: gs.game.physics?.bodyCount ?? null,
      meshes: [...meshes.values()].sort((a, b) => b.triangles - a.triangles).slice(0, 10),
      textureList: [...textures.values()],
    };
  });
}
/* c8 ignore stop */

/** @param {Context} ctx @returns {import('./common.js').ToolDefinition[]} */
export function statsTools(ctx) {
  return [
    {
      name: 'game_stats',
      title: 'Game performance stats',
      description:
        'What the game costs, measured in a headless browser for `seconds` (default 3) at `size` (default [1280, 720]): ' +
        'frame times (min/avg/p95/max and fps), draw calls and triangles per frame, GPU geometries, textures and shader ' +
        'programs, JS heap, entity and physics body counts, the 10 heaviest meshes by triangles (with entity names), the ' +
        'export size on disk, and the project\'s export budgets from gamestudio.json (warnSizeMB, maxTextureSize, ' +
        'bakeBudgetMB) with over_budget listing what exceeds them. Read-only: it opens its own page with the last export ' +
        '(or samples this server\'s headless game with use_running: true) and changes nothing. Headless fps is NOT ' +
        'representative (software GL without a GPU, a capped frame rate with one; renderer.gpu says which): compare ' +
        'before/after a change at the same size; draw calls, triangles and memory are representative.',
      inputSchema: object({
        seconds: { type: 'number', minimum: 0.5, maximum: 30 },
        size: { type: 'array', items: { type: 'integer', minimum: 64, maximum: 3840 }, minItems: 2, maxItems: 2 },
        scene: { type: 'string', minLength: 1, description: 'Scene to load (default: the start scene).' },
        use_running: { type: 'boolean', description: "Sample this server's headless game (from play) instead of a fresh page." },
        keys: { type: 'array', items: { type: 'string', minLength: 1 }, maxItems: 10, description: 'Keys to press before measuring, e.g. ["Enter"] to leave a title screen.' },
      }),
      annotations: READ,
      handler: async (args, call) => {
        const root = ctx.requireRoot();
        const seconds = args.seconds ?? 3;
        const running = args.use_running && ctx.browser.game && !ctx.browser.game.page.isClosed() ? ctx.browser.game : null;
        if (!running) {
          const scene = args.scene ?? readJson(path.join(root, 'gamestudio.json'))?.startScene ?? 'main';
          if (!existsSync(path.join(root, '.gamestudio', 'export', `${scene}.scene.json`))) {
            throw new ToolError(`Level '${scene}' has not been exported yet, so there is nothing to measure.`, {
              code: 'invalid_arguments',
              hint: 'game_stats measures the last export: call export_scene (or export_scene {"all": true}) first.',
            });
          }
        }
        const dev = await ctx.ensureDevServer(call.signal);
        const size = running ? running.size : args.size ?? DEFAULT_SIZE;
        const opened = running ? null : await ctx.browser.openGame(args.scene ? `${dev.url}?scene=${encodeURIComponent(args.scene)}` : dev.url, { size });
        const page = running ? running.page : opened?.page;
        try {
          if (!running) await new Promise((r) => setTimeout(r, 1000)); // warm-up: first frames compile shaders
          if (args.keys?.length) {
            await page.evaluate(() => /** @type {HTMLElement | null} */ (document.querySelector('canvas'))?.focus());
            for (const key of args.keys) await page.keyboard.press(normalizeKey(key));
            await new Promise((r) => setTimeout(r, 1000));
          }
          const sample = /** @type {any} */ (await page.evaluate(sampleInPage, seconds));
          const config = readJson(path.join(root, 'gamestudio.json')) ?? {};
          const exported = sample.scene ? exportSize(root, sample.scene) : { bytes: 0 };
          const { budgets, over_budget } = checkBudgets(config, { exportBytes: exported.bytes, textures: sample.textureList });
          return {
            page: running ? 'the running headless game' : 'a fresh headless page',
            size,
            seconds,
            scene: sample.scene,
            state: sample.state,
            ...(sample.screen && { screen: sample.screen }),
            ...((sample.state !== 'playing' || sample.screen) && {
              warning: `Measured while the game was ${sample.state}${sample.screen ? ` with the '${sample.screen}' screen open` : ''}: ` +
                'the numbers are those of that screen, not of the level. Press keys first (keys: ["Enter"]) or measure a game you ' +
                'started and played (use_running: true).',
            }),
            frames: frameStats(sample.times),
            renderer: { ...sample.renderer, ...(ctx.browser.rendering && { webgl: ctx.browser.rendering.renderer, gpu: ctx.browser.rendering.gpu }) },
            js_heap_mb: sample.js_heap_mb,
            entities: sample.entities,
            bodies: sample.bodies,
            largest_meshes: sample.meshes,
            textures: sample.textureList.length,
            export: exported,
            budgets,
            over_budget,
            note: HEADLESS_NOTE,
          };
        } finally {
          if (opened) {
            await opened.page.goto('about:blank', { timeout: 5000 }).catch(() => {});
            await opened.page.close().catch(() => {});
          }
        }
      },
    },
  ];
}
