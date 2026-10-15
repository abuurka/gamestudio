// Multi-level flow: ctx.loadScene with a fade, entities kept across scenes, ctx.store, ctx.restart,
// ctx.save, and the test clock and live link across a scene change.
import { afterEach, beforeAll, beforeEach, describe, expect, it } from 'vitest';
import type { SystemContext } from '../src/context';
import { defineSystem, removeSystem } from '../src/ecs/system';
import type { Game } from '../src/game/game';
import { RapierPhysics } from '../src/physics/rapier-backend';
import { handleEditorMessage } from '../src/link/handlers';
import '../src/systems';
import type { FakeDom } from './dom';
import { fakeRenderer, heldGame, installSceneDom } from './game-fixture';

let dom: FakeDom;
beforeAll(async () => {
  await new RapierPhysics().init();
});
beforeEach(() => {
  dom = installSceneDom();
});
afterEach(() => {
  removeSystem('LevelFlow');
  dom.restore();
});

const PLAYER_A = 'gs_000000a00005';
const fadeOf = (game: Game): number => (game as unknown as { fade: { value: number } }).fade.value;

/** N: +5 score and go to level2 keeping the Player. R: restart. */
function levelFlow(options: { keep?: string[]; fade?: number } = {}): { starts: string[]; ctx: SystemContext | null } {
  const seen = { starts: [] as string[], ctx: null as SystemContext | null };
  defineSystem({
    name: 'LevelFlow',
    start(ctx) {
      seen.ctx = ctx;
      ctx.store.score ??= 0;
      seen.starts.push(`${ctx.game.scene.name}:${ctx.store.score}`);
    },
    update(ctx) {
      if (ctx.input.keyDown('KeyN')) {
        ctx.store.score += 5;
        void ctx.loadScene('level2', { keep: options.keep ?? ['Player'], ...(options.fade !== undefined && { fade: options.fade }) });
      }
      if (ctx.input.keyDown('KeyR')) void ctx.restart();
    },
  });
  return seen;
}

describe('ctx.loadScene', () => {
  it('fades out, loads level2 with the store and the kept player, and fades back in', async () => {
    const seen = levelFlow();
    const game = await heldGame(dom);
    fakeRenderer(game);
    await game.test.begin();
    const player = game.world.get(PLAYER_A)!;
    const authored = game.test.positions(['Player']).Player!;
    game.test.place(null, { position: [3, 1.2, 2] });
    game.test.schedule([{ step: 5, key: 'KeyN' }]);
    await game.test.advance(6); // N is read in step 6
    expect(game.state).toBe('loading');
    const opacities: number[] = [];
    for (let i = 0; i < 40; i++) {
      await game.test.advance(1);
      game.test.render();
      opacities.push(Math.round(fadeOf(game) * 100) / 100);
    }
    const status = game.test.status();
    expect(status).toMatchObject({ state: 'playing', scene: { name: 'level2' }, sceneChanges: 1, loads: 0 });
    // Out over 0.25 s (15 steps), the load, then in over 0.25 s.
    expect(Math.max(...opacities)).toBe(1);
    expect(opacities[0]).toBeGreaterThan(0);
    expect(opacities[0]).toBeLessThan(0.2);
    expect(opacities.at(-1)).toBe(0);
    expect(seen.starts).toEqual(['basic:0', 'level2:5']);
    expect(game.store.score).toBe(5);
    // The same Player entity, now where level2's own Player stood; level2's Player and its id are gone.
    expect(game.world.get(PLAYER_A)).toBe(player);
    expect(player.alive).toBe(true);
    expect(game.world.get('gs_000000b00005')).toBeUndefined();
    expect(game.world.all().filter((e) => e.name === 'Player')).toHaveLength(1);
    const now = game.test.positions(['Player']).Player!;
    expect(Math.abs(now[0] - authored[0])).toBeLessThan(0.5);
    expect(Math.abs(now[2] - authored[2])).toBeLessThan(0.5);
    // References to level2's Player now point at the kept one; the rest is level2's.
    const camera = game.world.findByName('Camera')!;
    expect(camera.id).toBe('gs_000000b00007');
    expect(camera.get<{ target: string }>('FollowCamera').target).toBe(PLAYER_A);
    expect(game.world.findByName('Coin')!.get<{ speed: number }>('Spin').speed).toBe(9);
  });

  it('lands the change on the same step however the steps are split', async () => {
    const result = async (chunk: number) => {
      levelFlow({ fade: 0.2 });
      const game = await heldGame(dom);
      await game.test.begin();
      game.test.track(['Player']);
      game.test.schedule([{ at: 0, key: 'KeyW', hold: 1.2 }, { at: 0.3, key: 'KeyN' }]);
      for (let done = 0; done < 90; done += chunk) await game.test.advance(Math.min(chunk, 90 - done));
      const out = { status: game.test.status(), report: game.test.report() };
      game.test.end();
      removeSystem('LevelFlow');
      return out;
    };
    const one = await result(1);
    const many = await result(90);
    const odd = await result(7);
    expect(one.status.scene.name).toBe('level2');
    expect(one.status.idleSteps).toBeGreaterThan(5); // the fade-out and the load
    expect(many.report).toEqual(one.report);
    expect(odd.report).toEqual(one.report);
    expect(many.status.gameTime).toBe(one.status.gameTime);
  });

  it('cuts without a fade, and ctx.restart goes back to the start scene with an empty store', async () => {
    const seen = levelFlow({ fade: 0, keep: [] });
    const game = await heldGame(dom);
    await game.test.begin();
    game.test.schedule([{ step: 2, key: 'KeyN' }]);
    await game.test.advance(4);
    expect(game.test.status()).toMatchObject({ scene: { name: 'level2' }, state: 'playing', sceneChanges: 1 });
    expect(game.world.get(PLAYER_A)).toBeUndefined();
    expect(game.store.score).toBe(5);
    game.test.schedule([{ step: 6, key: 'KeyR' }]);
    await game.test.advance(60);
    expect(game.test.status()).toMatchObject({ scene: { name: 'basic' }, state: 'playing', sceneChanges: 2 });
    expect(game.store.score).toBe(0);
    expect(seen.starts).toEqual(['basic:0', 'level2:5', 'basic:0']);
  });

  it('reports a missing scene and keeps playing the current one', async () => {
    const game = await heldGame(dom);
    const errors: string[] = [];
    game.reportError = (error) => errors.push(String(error));
    await game.test.begin();
    void game.loadScene('nowhere', { fade: 0.2 });
    await game.test.advance(40);
    expect(errors.join()).toMatch(/nowhere/);
    expect(game.test.status()).toMatchObject({ scene: { name: 'basic' }, state: 'playing' });
    expect(fadeOf(game)).toBe(0);
  });

  it('keeps the live link working across a scene change; edits for another scene are ignored', async () => {
    levelFlow({ fade: 0 });
    const game = await heldGame(dom);
    await game.test.begin();
    game.test.schedule([{ step: 1, key: 'KeyN' }]);
    await game.test.advance(3);
    expect(game.scene.name).toBe('level2');
    const send = () => {};
    handleEditorMessage(game, { t: 'entity.transform', scene: 'basic', items: [{ id: 'gs_000000b00006', p: [9, 9, 9] }] }, send);
    expect(game.test.positions(['Coin']).Coin).not.toEqual([9, 9, 9]);
    handleEditorMessage(game, { t: 'entity.transform', scene: 'level2', items: [{ id: 'gs_000000b00006', p: [7, 1, 7] }] }, send);
    expect(game.test.positions(['Coin']).Coin).toEqual([7, 1, 7]);
    handleEditorMessage(game, { t: 'entity.transform', items: [{ id: 'gs_000000b00006', p: [6, 1, 6] }] }, send);
    expect(game.test.positions(['Coin']).Coin).toEqual([6, 1, 6]);
    const replies: unknown[] = [];
    handleEditorMessage(game, { t: 'state.request', id: 'q1', filter: { component: 'Spin' } }, (r) => replies.push(r));
    expect(replies).toMatchObject([{ t: 'state.response', re: 'q1', entities: [{ id: 'gs_000000b00006' }] }]);
    // A re-export from the editor reloads; it counts as a load, not a scene change.
    handleEditorMessage(game, { t: 'scene.load', url: '/export/basic.scene.json' }, send);
    await game.test.advance(2);
    expect(game.test.status()).toMatchObject({ scene: { name: 'basic' }, loads: 1, sceneChanges: 1 });
  });

  it('keeps ctx.store through scene changes; Stop clears it', async () => {
    levelFlow({ fade: 0 });
    const game = await heldGame(dom);
    await game.test.begin();
    game.test.schedule([{ step: 1, key: 'KeyN' }]);
    await game.test.advance(3);
    expect(game.store.score).toBe(5);
    game.test.end();
    game.stop();
    await game.idle();
    expect(game.store).toEqual({});
  });
});

describe('ctx.save', () => {
  it('stores JSON values per game, and playtests get an empty store of their own', async () => {
    const saved = new Map<string, string>();
    (globalThis as Record<string, unknown>).localStorage = {
      getItem: (k: string) => saved.get(k) ?? null,
      setItem: (k: string, v: string) => void saved.set(k, v),
      removeItem: (k: string) => void saved.delete(k),
      key: (i: number) => [...saved.keys()][i] ?? null,
      get length() {
        return saved.size;
      },
    };
    try {
      const game = await heldGame(dom, { name: 'gem-island' });
      game.save.set('best', 120);
      expect(saved.get('gamestudio:gem-island:best')).toBe('120');
      expect(game.save.get('best', 0)).toBe(120);
      expect(game.save.get('missing', 'none')).toBe('none');
      await game.test.begin();
      expect(game.save.get('best', 0)).toBe(0);
      game.save.set('best', 999);
      game.test.end();
      expect(game.save.get('best')).toBe(120);
      expect(game.save.keys()).toEqual(['best']);
      game.save.clear();
      expect(saved.size).toBe(0);
    } finally {
      delete (globalThis as Record<string, unknown>).localStorage;
    }
  });

  it('a playtest seeds its own ctx.save and ctx.store for start(); saved() reads what the run saved', async () => {
    const saved = new Map<string, string>();
    (globalThis as Record<string, unknown>).localStorage = {
      getItem: (k: string) => saved.get(k) ?? null,
      setItem: (k: string, v: string) => void saved.set(k, v),
      removeItem: (k: string) => void saved.delete(k),
      key: (i: number) => [...saved.keys()][i] ?? null,
      get length() {
        return saved.size;
      },
    };
    try {
      const starts: unknown[] = [];
      defineSystem({
        name: 'LevelFlow',
        start(ctx) {
          starts.push([ctx.store.lives, ctx.store.mode, ctx.save.get('best'), ctx.audio.channel('music').volume]);
          ctx.store.lives ??= 5;
        },
        update(ctx) {
          if (ctx.input.keyDown('KeyB')) ctx.save.set('best', ctx.store.lives * 10);
        },
      });
      const game = await heldGame(dom, { name: 'gem-island' });
      game.save.set('best', 120); // the player's own
      expect(game.test.saved()).toEqual({}); // no test yet
      await game.test.advance(1);
      expect(starts).toEqual([[undefined, undefined, 120, 1]]);

      // A reset run: the reset empties ctx.store, then the seeds go in, and start() reads them on step 1.
      const music = { music: { volume: 0.25, muted: false } };
      await game.test.begin({ reset: true, save: { best: 5, 'gs.audio': music }, store: { lives: 3, mode: 'hard' } });
      expect(game.store).toEqual({ lives: 3, mode: 'hard' });
      expect(game.audio.channel('music').volume).toBe(0.25); // a seeded gs.audio sets the channels
      await game.test.advance(1);
      expect(starts.at(-1)).toEqual([3, 'hard', 5, 0.25]);
      expect(game.test.saved()).toEqual({ best: 5 });
      expect(game.test.saved({ internal: true })).toEqual({ best: 5, 'gs.audio': music });
      game.test.schedule([{ step: 1, key: 'KeyB' }]);
      await game.test.advance(2);
      expect(game.test.saved()).toEqual({ best: 30 });

      // Without a reset the store keeps its keys and the seeds go on top; the save starts empty again.
      await game.test.begin({ store: { mode: 'easy' } });
      expect([game.store.lives, game.store.mode, game.test.saved()]).toEqual([3, 'easy', {}]);
      game.save.set('best', 1);
      game.test.end();
      expect(game.save.get('best')).toBe(120); // the player's save was never touched
      expect(game.test.saved()).toEqual({ best: 1 }); // after end(): what the last test held
      expect(saved.size).toBe(1);
      await expect(game.test.begin({ save: [1] as never })).rejects.toThrow(/save/);
      await expect(game.test.begin({ store: 'lives' as never })).rejects.toThrow(/store/);
    } finally {
      delete (globalThis as Record<string, unknown>).localStorage;
    }
  });
});
