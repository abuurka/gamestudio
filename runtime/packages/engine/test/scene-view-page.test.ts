// createGame with `?gs_view=scene`: the page starts in the Scene view (stopped, no title screen) and
// window.__gs takes editor messages (`message`) and reports the view.
import { PerspectiveCamera, type Camera, type Scene } from 'three';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import type { Game } from '../src/game/game';
import { RapierPhysics } from '../src/physics/rapier-backend';
import { removeSystem, defineSystem } from '../src/ecs/system';
import { FakeElement, installDom, type FakeDom } from './dom';
import { FIXTURE_DIR } from './helpers';

const link = vi.hoisted(() => ({
  session: { gamePort: null as number | null, token: null as string | null, startScene: 'basic', name: 'sv-page', post: null },
  sent: [] as Array<Record<string, unknown>>,
}));
vi.mock('virtual:gamestudio/session', () => ({ default: link.session }));
// The live link, without a socket: connects at once and records what the game sends.
vi.mock('../src/link/client', () => ({
  LinkClient: class {
    connected = true;
    constructor(private readonly options: { onConnect(): void }) {}
    connect(): void {
      this.options.onConnect();
    }
    send(message: Record<string, unknown>): void {
      link.sent.push(message);
    }
    close(): void {}
  },
}));

const cameras: Camera[] = [];
vi.mock('../src/game/renderer', async (original) => ({
  ...(await original<typeof import('../src/game/renderer')>()),
  createRenderer: async () => ({
    kind: 'webgl',
    raw: undefined,
    domElement: null,
    setSize() {},
    render(scene: Scene, camera: Camera) {
      scene.updateMatrixWorld();
      cameras.push(camera);
    },
    createEnvironment: () => null,
    dispose() {},
  }),
}));

const { createGame, pageOptions } = await import('../src/game/create-game');

let dom: FakeDom;
let game: Game | null = null;
beforeAll(async () => {
  await new RapierPhysics().init();
});
beforeEach(() => {
  dom = installDom({
    '/__gs/export/basic.scene.json': `${FIXTURE_DIR}basic.scene.json`,
    '/__gs/export/basic.glb': `${FIXTURE_DIR}basic.glb`,
  });
  Object.assign(globalThis, {
    HTMLCanvasElement: FakeElement,
    ResizeObserver: class {
      observe() {}
      disconnect() {}
    },
  });
  cameras.length = 0;
});
afterEach(() => {
  game?.dispose();
  game = null;
  removeSystem('PageTitle');
  delete (globalThis as { ResizeObserver?: unknown }).ResizeObserver;
  delete (globalThis as { HTMLCanvasElement?: unknown }).HTMLCanvasElement;
  delete window.__gs;
  dom.restore();
});

function at(search: string): void {
  (globalThis as { location: unknown }).location = { href: `http://game.test/${search}`, search };
}

describe('the page URL', () => {
  it('reads ?scene and ?gs_view=scene', () => {
    expect(pageOptions('?gs_view=scene&scene=level2')).toEqual({ scene: 'level2', view: 'scene' });
    expect(pageOptions('?gs_view=game')).toEqual({ scene: null, view: null });
    expect(pageOptions('')).toEqual({ scene: null, view: null });
  });

  it('lets the view the tab was in win over ?gs_view, so a reload during Play plays again', () => {
    expect(pageOptions('?gs_view=scene', 'game')).toEqual({ scene: null, view: 'game' });
    expect(pageOptions('', 'scene')).toEqual({ scene: null, view: 'scene' });
    expect(pageOptions('?gs_view=scene', 'bogus')).toEqual({ scene: null, view: 'scene' });
    expect(pageOptions('?gs_view=scene', null)).toEqual({ scene: null, view: 'scene' });
  });

  it('starts in the Scene view with ?gs_view=scene: stopped, no title, and __gs takes editor messages', async () => {
    let titles = 0;
    defineSystem({ name: 'PageTitle', start: (ctx) => void (titles++, ctx.ui.screen('title', { title: 'Hi' })) });
    at('?gs_view=scene');
    game = createGame({ canvas: dom.canvas as unknown as HTMLCanvasElement, environment: 0, fillLight: 0 });
    await game.whenReady();
    await game.idle();
    for (let t = 1000; t <= 1500; t += 16) dom.frame(t);
    expect([game.state, game.view, window.__gs!.view, window.__gs!.state, titles]).toEqual(['stopped', 'scene', 'scene', 'stopped', 0]);
    const eye = new PerspectiveCamera(50, 16 / 9, 0.1, 500);
    eye.position.set(6, 3, 6);
    eye.lookAt(0, 0, 0);
    eye.updateMatrixWorld(true);
    const projection = eye.projectionMatrix.toArray();
    expect(window.__gs!.message({ t: 'view.camera', view: eye.matrixWorldInverse.toArray(), projection, ortho: false, near: 0.1, far: 500 })).toEqual([]);
    dom.frame(1600);
    expect(cameras.at(-1)!.projectionMatrix.toArray()).toEqual(projection);
    expect(cameras.at(-1)).not.toBe(game.three.camera);
    // Replies come back.
    const replies = window.__gs!.message({ t: 'state.request', id: 'r1', filter: { ids: ['gs_000000a00006'] } });
    expect(replies).toMatchObject([{ t: 'state.response', re: 'r1', entities: [{ name: 'Coin' }] }]);
    // Play leaves it.
    window.__gs!.message({ t: 'play.start' });
    expect([game.state, window.__gs!.view]).toEqual(['playing', 'game']);
  });

  it('reports the view in game.status', async () => {
    Object.assign(link.session, { gamePort: 1, token: 'test' });
    link.sent.length = 0;
    try {
      at('?gs_view=scene');
      game = createGame({ canvas: dom.canvas as unknown as HTMLCanvasElement, environment: 0, fillLight: 0 });
      await game.whenReady();
      await game.idle();
      game.play();
      game.stop();
      await game.idle();
      window.__gs!.message({ t: 'view.mode', mode: 'scene' });
      const statuses = link.sent.filter((m) => m.t === 'game.status').map((m) => `${m.state}/${m.view}`);
      expect(statuses).toEqual(['loading/scene', 'stopped/scene', 'stopped/game', 'playing/game', 'stopped/game', 'stopped/scene']);
    } finally {
      Object.assign(link.session, { gamePort: null, token: null });
    }
  });

  it('plays as before without it', async () => {
    at('');
    game = createGame({ canvas: dom.canvas as unknown as HTMLCanvasElement, environment: 0, fillLight: 0 });
    await game.whenReady();
    expect([game.state, game.view]).toEqual(['playing', 'game']);
  });
});
