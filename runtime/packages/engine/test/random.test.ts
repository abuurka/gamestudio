// ctx.random: the game's own seeded stream, independent of Math.random, inputs and rendering.
import { BoxGeometry, Mesh, MeshBasicMaterial } from 'three';
import { afterEach, beforeAll, beforeEach, describe, expect, it } from 'vitest';
import { defineSystem, removeSystem } from '../src/ecs/system';
import { GameRandom, hashSeed } from '../src/game/random';
import { RapierPhysics } from '../src/physics/rapier-backend';
import '../src/systems';
import type { FakeDom } from './dom';
import { fakeRenderer, heldGame, installSceneDom } from './game-fixture';

describe('GameRandom', () => {
  it('repeats a sequence from a seed and offers range, int, pick and chance', () => {
    const a = new GameRandom(42).api;
    const b = new GameRandom(42).api;
    const seq = Array.from({ length: 5 }, () => a());
    expect(Array.from({ length: 5 }, () => b())).toEqual(seq);
    expect(seq.every((n) => n >= 0 && n < 1)).toBe(true);
    expect(a.seed).toBe(42);
    const r = new GameRandom(7).api;
    for (let i = 0; i < 200; i++) {
      const x = r.range(-2, 3);
      expect(x).toBeGreaterThanOrEqual(-2);
      expect(x).toBeLessThan(3);
      const n = r.int(1, 6);
      expect(Number.isInteger(n) && n >= 1 && n <= 6).toBe(true);
    }
    const seen = new Set(Array.from({ length: 300 }, () => r.int(1, 3)));
    expect([...seen].sort()).toEqual([1, 2, 3]);
    expect(['a', 'b', 'c']).toContain(r.pick(['a', 'b', 'c']));
    expect(() => r.pick([])).toThrow(/non-empty/);
    expect(r.chance(0)).toBe(false);
    expect(r.chance(1)).toBe(true);
    expect(hashSeed('main', 1)).not.toBe(hashSeed('main', 2));
  });
});

describe('ctx.random in a game', () => {
  let dom: FakeDom;
  beforeAll(async () => {
    await new RapierPhysics().init();
  });
  beforeEach(() => {
    dom = installSceneDom();
  });
  afterEach(() => {
    removeSystem('RandomSpawner');
    dom.restore();
  });

  /** A spawner: every 0.25 s a box at a random x from ctx.random; Math.random picks too, for contrast. A, when pressed, makes three.js objects (each draws from Math.random). */
  function spawner(out: { seeded: number[]; shared: number[] }): void {
    defineSystem({
      name: 'RandomSpawner',
      start(ctx) {
        ctx.every(0.25, () => {
          out.seeded.push(Math.round(ctx.random.range(-5, 5) * 1000) / 1000);
          out.shared.push(Math.round((Math.random() * 10 - 5) * 1000) / 1000);
        });
      },
      update(ctx) {
        if (ctx.input.keyDown('KeyA')) {
          for (let i = 0; i < 3; i++) ctx.world.spawn({ object3d: new Mesh(new BoxGeometry(), new MeshBasicMaterial()) });
        }
      },
    });
  }

  async function run(options: { inputs: boolean; render: boolean; seed?: number }) {
    const out = { seeded: [] as number[], shared: [] as number[] };
    spawner(out);
    const game = await heldGame(dom);
    fakeRenderer(game);
    await game.test.begin({ seed: options.seed ?? 3 });
    if (options.inputs) {
      game.test.schedule([
        { at: 0.1, key: 'KeyA' },
        { at: 0.6, key: 'KeyA' },
        { at: 0.6, key: 'KeyD', hold: 0.5 },
      ]);
    }
    for (let step = 0; step < 90; step++) {
      await game.test.advance(1);
      if (options.render && step % 7 === 0) game.test.render();
    }
    game.test.end();
    game.dispose();
    return out;
  }

  it('gives the same spawn sequence for a seed whatever the inputs and rendering', async () => {
    const idle = await run({ inputs: false, render: false });
    const busy = await run({ inputs: true, render: true });
    expect(idle.seeded).toHaveLength(5);
    expect(busy.seeded).toEqual(idle.seeded);
    // Math.random is shared with three.js: the same script with inputs reshuffles it.
    expect(busy.shared).not.toEqual(idle.shared);
    const other = await run({ inputs: false, render: false, seed: 4 });
    expect(other.seeded).not.toEqual(idle.seeded);
  });
});
