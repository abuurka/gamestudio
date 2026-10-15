// The test clock (game.test) on a real Game with the basic fixture scene, in a fake DOM.
import { afterEach, beforeAll, beforeEach, describe, expect, it } from 'vitest';
import { defineSystem, removeSystem } from '../src/ecs/system';
import { RapierPhysics } from '../src/physics/rapier-backend';
import { Game } from '../src/game/game';
import { FIXED_DT } from '../src/game/stepper';
import { expandTestInputs, type TestInput } from '../src/game/test-control';
import { handleEditorMessage } from '../src/link/handlers';
import '../src/systems';
import { installDom, type FakeDom, type FakeElement } from './dom';
import { FIXTURE_DIR } from './helpers';

const MANIFEST = '/export/basic.scene.json';
const FILES = { [MANIFEST]: `${FIXTURE_DIR}basic.scene.json`, '/export/basic.glb': `${FIXTURE_DIR}basic.glb` };
const WALK: TestInput[] = [
  { at: 0, key: 'KeyW', hold: 1 },
  { at: 0.5, key: 'KeyD', hold: 0.5 },
  { at: 1.2, key: 'Space' },
];

let dom: FakeDom;

beforeAll(async () => {
  await new RapierPhysics().init();
});
beforeEach(() => {
  dom = installDom(FILES);
});
afterEach(() => {
  dom.restore();
});

/** A Game on the fixture with the clock held from the start (as a playtest page opens it). */
async function heldGame(): Promise<Game> {
  const game = new Game(dom.canvas as unknown as HTMLCanvasElement, { environment: 0, fillLight: 0 });
  await game.physics.init();
  game.test.hold();
  await game.loadUrl(MANIFEST);
  return game;
}

/** Stands in for WebGLRenderer: drawing updates world matrices, like the real one. */
function fakeRenderer(game: Game): { draws: number } {
  const counter = { draws: 0 };
  game.three.renderer = {
    render(scene: { updateMatrixWorld(): void }) {
      scene.updateMatrixWorld();
      counter.draws++;
    },
    setSize() {},
  } as never;
  return counter;
}

const player = (game: Game) => game.test.positions(['Player']).Player!;
const hudTexts = (game: Game) => (game.hud.element as unknown as FakeElement).all().map((e) => e.textContent);

describe('expandTestInputs', () => {
  it('turns game seconds into fixed steps; press and hold release later', () => {
    expect(expandTestInputs([{ at: 1, key: 'w', hold: 0.5 }, { at: 0, key: 'Space' }, { at: 0.5, mouse: 'click', x: 10, y: 20 }])).toEqual([
      { step: 0, type: 'keydown', code: 'Space' },
      { step: 1, type: 'keyup', code: 'Space' },
      { step: 30, type: 'pointerdown', x: 10, y: 20, button: 0 },
      { step: 31, type: 'pointerup', button: 0 },
      { step: 60, type: 'keydown', code: 'KeyW' },
      { step: 90, type: 'keyup', code: 'KeyW' },
    ]);
    expect(expandTestInputs([{ step: 3, key: 'ArrowUp', action: 'down' }, { at: 0.004, key: 'up', hold: 0 }])).toEqual([
      { step: 0, type: 'keydown', code: 'ArrowUp' },
      { step: 1, type: 'keyup', code: 'ArrowUp' }, // a hold of 0 still lasts one step
      { step: 3, type: 'keydown', code: 'ArrowUp' },
    ]);
    expect(expandTestInputs([{ at: 2, mouse: 'move', dx: 5 }, { at: 2, mouse: 'wheel', dy: 120 }])).toEqual([
      { step: 120, type: 'pointermove', dx: 5 },
      { step: 120, type: 'wheel', dx: 0, dy: 120 },
    ]);
  });

  it('names the bad input', () => {
    expect(() => expandTestInputs([{ at: 0 }])).toThrow(/inputs\[0\] needs "key" or "mouse"/);
    expect(() => expandTestInputs([{ at: -1, key: 'KeyW' }])).toThrow(/inputs\[0\]\.at/);
    expect(() => expandTestInputs([{ key: 'KeyW' }, { key: 'KeyW', action: 'tap' as never }])).toThrow(/inputs\[1\]\.action/);
    expect(() => expandTestInputs([{ mouse: 'click', button: 'side' as never }])).toThrow(/button/);
  });
});

describe('game.test', () => {
  it('holds W for exactly one second of game time, however long that takes', async () => {
    const game = await heldGame();
    await game.test.begin();
    const start = player(game);
    game.test.schedule([{ at: 0, key: 'KeyW', hold: 1 }]);
    await game.test.advance(60);
    const released = player(game);
    const status = await game.test.advance(30);
    expect(status).toMatchObject({ holding: true, step: 90, time: 1.5, state: 'playing', loads: 0, idleSteps: 0, pending: 0 });
    expect(status.gameTime).toBeCloseTo(1.5, 3);
    const end = player(game);
    // At most 5 m/s for 60 steps, forward = away from the follow camera (-Z); then it stops.
    expect(start[2] - released[2]).toBeGreaterThan(4);
    expect(start[2] - released[2]).toBeLessThanOrEqual(5.001);
    expect(end[2]).toBe(released[2]);
  });

  it('gives identical results with or without rendering, run after run', async () => {
    const run = async (renderEvery: number) => {
      const game = await heldGame();
      const renderer = fakeRenderer(game);
      await game.test.begin();
      game.test.track(['Player', 'Crate']);
      game.test.schedule(WALK);
      for (let step = 0; step < 150; step++) {
        await game.test.advance(1);
        if (renderEvery && step % renderEvery === 0) game.test.render();
      }
      return { report: game.test.report(), state: game.describe({ ids: ['gs_000000a00005', 'gs_000000a00002'] }), draws: renderer.draws };
    };
    const never = await run(0);
    const always = await run(1);
    const sometimes = await run(37);
    expect(never.draws).toBe(0);
    expect(always.draws).toBe(150);
    expect(always.state).toEqual(never.state);
    expect(sometimes.state).toEqual(never.state);
    expect(always.report).toEqual(never.report);
    const [p, crate] = never.report;
    expect(p!.max![1]).toBeGreaterThan(p!.start![1] + 0.5); // it jumped
    expect(crate!.final![1]).toBeLessThan(crate!.start![1]); // the crate fell
    expect(p!.path).toBeGreaterThan(5);
  });

  it('latches a tap: Space down and up within one step still jumps', async () => {
    const game = await heldGame();
    await game.test.begin();
    await game.test.advance(30); // settle on the ground
    const ground = player(game)[1];
    game.test.track(['Player']);
    game.test.schedule([
      { at: 0.5, key: 'Space', action: 'down' },
      { at: 0.5, key: 'Space', action: 'up' },
    ]);
    await game.test.advance(40);
    const [report] = game.test.report();
    expect(report!.max![1]).toBeGreaterThan(ground + 0.5);
  });

  it('keeps the real-time loop from simulating or drawing while it holds the clock, then gives it back', async () => {
    const game = await heldGame();
    const renderer = fakeRenderer(game);
    const frame = (game as unknown as { frame: (now: number) => void }).frame;
    await game.test.begin();
    const before = player(game);
    for (let t = 1000; t <= 2000; t += 16) frame(t);
    expect(game.test.status().gameTime).toBe(0);
    expect(player(game)).toEqual(before);
    expect(renderer.draws).toBe(0);
    game.test.end();
    for (let t = 3000; t <= 3500; t += 16) frame(t);
    expect(game.test.holding).toBe(false);
    expect(renderer.draws).toBeGreaterThan(20);
    expect(game.test.status().gameTime).toBeGreaterThan(0.4);
  });

  it('keeps the live link working: edits apply, a re-export reloads, play controls wait', async () => {
    const game = await heldGame();
    await game.test.begin();
    await game.test.advance(10);
    const send = () => {};
    handleEditorMessage(game, { t: 'play.pause', paused: true }, send);
    handleEditorMessage(game, { t: 'play.step', frames: 5 }, send);
    expect(game.state).toBe('playing');
    handleEditorMessage(game, { t: 'entity.transform', items: [{ id: 'gs_000000a00006', p: [7, 1, 7] }] }, send);
    expect(game.test.positions(['Coin']).Coin).toEqual([7, 1, 7]);
    handleEditorMessage(game, { t: 'scene.load', url: MANIFEST }, send);
    const status = await game.test.advance(5); // waits for the reload, then steps
    expect(status).toMatchObject({ step: 15, loads: 1, state: 'playing' });
    expect(status.gameTime).toBeCloseTo(5 * FIXED_DT, 3);
  });

  it('runs a hot-reloaded system from the next step', async () => {
    const game = await heldGame();
    await game.test.begin();
    const seen: string[] = [];
    defineSystem({ name: 'HmrProbe', update: () => void seen.push('v1') });
    await game.test.advance(2);
    defineSystem({ name: 'HmrProbe', update: () => void seen.push('v2') }); // what Vite's HMR does
    await game.test.advance(1);
    removeSystem('HmrProbe');
    expect(seen).toEqual(['v1', 'v1', 'v2']);
  });

  it('places an entity for this run and counts HUD durations on game time', async () => {
    const game = await heldGame();
    await game.test.begin();
    const placed = game.test.place(undefined, { position: { x: 4, y: 3, z: -6 }, yaw: 90 });
    expect(placed.name).toBe('Player');
    expect(placed.position).toEqual([4, 3, -6]);
    expect(placed.rotation[1]).toBeCloseTo(Math.SQRT1_2, 5);
    await game.test.advance(1);
    expect(player(game)[0]).toBeCloseTo(4, 3);

    game.hud.set('note', 'hello', { duration: 0.5 });
    await game.test.advance(29);
    expect(hudTexts(game)).toContain('hello');
    await game.test.advance(2);
    expect(hudTexts(game)).not.toContain('hello');
  });

  it('seeds Math.random for the run and restores it afterwards', async () => {
    const game = await heldGame();
    const real = Math.random;
    await game.test.begin({ seed: 7 });
    const a = [Math.random(), Math.random()];
    await game.test.begin({ seed: 7, reset: true });
    expect([Math.random(), Math.random()]).toEqual(a);
    game.test.end();
    expect(Math.random).toBe(real);
  });

  it('tracks component changes and destroyed entities', async () => {
    const game = await heldGame();
    await game.test.begin();
    game.test.track(['Coin', 'Crate']);
    await game.test.advance(6);
    game.world.findByName('Crate')!.get<{ mass: number }>('RigidBody').mass = 3;
    await game.test.advance(6);
    game.world.findByName('Coin')!.destroy();
    await game.test.advance(1);
    const [coin, crate] = game.test.report();
    expect(coin).toMatchObject({ found: true, destroyedAt: 0.217, components: null });
    expect(crate!.changes['RigidBody.mass']).toEqual({ start: 1, final: 3, min: 1, max: 3, count: 1, first: 0.117, last: 0.117 });
  });
});
