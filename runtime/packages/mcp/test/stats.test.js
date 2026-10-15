import { mkdirSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import { afterEach, describe, expect, it } from 'vitest';
import { Context } from '../src/context.js';
import { createTools } from '../src/index.js';
import { checkBudgets, exportSize, frameStats, sampleInPage } from '../src/tools/stats.js';
import { makeProject } from './helpers.js';

let project;
afterEach(() => project?.cleanup());

describe('game_stats', () => {
  it('counts every render pass of a frame and says what it measured', async () => {
    const zero = () => ({ calls: 0, triangles: 0, points: 0, lines: 0 });
    const info = { autoReset: true, render: zero(), memory: { geometries: 4, textures: 1 }, programs: [], reset() { this.render = zero(); } };
    globalThis.requestAnimationFrame = (fn) => setTimeout(() => {
      // A frame with bloom: the scene, then two post passes, each a render() call.
      for (const [calls, triangles] of [[40, 5000], [1, 1], [1, 1]]) {
        if (info.autoReset) info.reset();
        info.render.calls += calls;
        info.render.triangles += triangles;
      }
      fn(performance.now());
    }, 1);
    globalThis.window = { __gs: {
      state: 'paused', world: { size: 9, entityOf: () => null },
      test: { state: () => ({ ui: { screen: 'title', focus: 'Play', buttons: [] } }) },
      game: { scene: { name: 'main' }, physics: { bodyCount: 3 }, three: { renderer: { kind: 'webgl', raw: { info } }, scene: { traverse() {} } } },
    } };
    try {
      const sample = await sampleInPage(0.08);
      expect(sample.renderer.draw_calls.avg).toBe(42);
      expect(sample.renderer.triangles.max).toBe(5002);
      expect((sample.state, sample.screen)).toBe('title');
      expect(info.autoReset).toBe(true); // given back
    } finally {
      delete globalThis.window;
      delete globalThis.requestAnimationFrame;
    }
  });

  it('is a read-only tool that says headless fps is not representative', () => {
    const tool = createTools(new Context({ root: null })).find((t) => t.name === 'game_stats');
    expect(tool.annotations.readOnlyHint).toBe(true);
    expect(tool.description).toContain('NOT representative');
  });

  it('says when there is no export to measure, before starting anything', async () => {
    project = makeProject();
    const ctx = new Context({ root: project.root, env: { HOME: project.root, APPDATA: path.join(project.root, 'none') } });
    const tool = createTools(ctx).find((t) => t.name === 'game_stats');
    const error = await tool.handler({ scene: 'level2' }, { signal: new AbortController().signal, progress() {} }).catch((e) => e);
    expect(error.code).toBe('invalid_arguments');
    expect(error.message).toMatch(/'level2' has not been exported/);
    await ctx.shutdown();
  });

  it('summarises frame times', () => {
    const times = [...Array(19).fill(16), 50];
    expect(frameStats(times)).toEqual({ fps: 56.5, frames: 20, min_ms: 16, avg_ms: 17.7, p95_ms: 50, max_ms: 50 });
    expect(frameStats([])).toBeNull();
  });

  it('sums the export files on disk', () => {
    project = makeProject();
    const dir = path.join(project.root, '.gamestudio', 'export');
    mkdirSync(path.join(dir, 'files', 'audio'), { recursive: true });
    writeFileSync(path.join(dir, 'main.glb'), Buffer.alloc(1000));
    writeFileSync(path.join(dir, 'main.prefabs.glb'), Buffer.alloc(200));
    writeFileSync(path.join(dir, 'files', 'audio', 'hit.ogg'), Buffer.alloc(30));
    writeFileSync(path.join(dir, 'files', 'audio', 'other-scene.ogg'), Buffer.alloc(500));
    writeFileSync(path.join(dir, 'main.scene.json'), JSON.stringify({ assets: { 'audio/hit.ogg': 'files/audio/hit.ogg?v=1' } }));
    expect(exportSize(project.root, 'main')).toEqual({ bytes: 1230, glb: 1000, prefabs: 200, files: 30 });
    expect(exportSize(project.root, 'other').bytes).toBe(0);
  });

  it('flags what is over the project budgets', () => {
    const textures = [{ name: 'grass', width: 4096, height: 4096 }, { name: 'rock', width: 1024, height: 512 }];
    const over = checkBudgets({ export: { warnSizeMB: 1 } }, { exportBytes: 3 * 1024 * 1024, textures });
    expect(over.over_budget).toEqual(['export size 3 MB > warnSizeMB 1', '1 texture(s) over maxTextureSize 2048']);
    expect(over.budgets.maxTextureSize).toEqual({ limit: 2048, largest: 4096, over: ['grass 4096x4096'] });
    expect(over.budgets.textureMemory.estimate_mb).toBeGreaterThan(80);
    const fine = checkBudgets({}, { exportBytes: 1024, textures: [] });
    expect(fine.over_budget).toEqual([]);
    expect(fine.budgets.warnSizeMB).toEqual({ limit: 32, actual: 0, over: false });
  });
});
