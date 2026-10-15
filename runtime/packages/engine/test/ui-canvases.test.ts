// Blender-authored menus and HUDs in a running game (docs/GAME-UI.md §5): loading from ui/index.json,
// open policies, pausing, keyboard and mouse, the pause menu, `previous`, ctx.ui's canvas API, live
// link messages and test state. The canvas layer is a stand-in (ui-controller-fixtures.ts).
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import type { SystemContext } from '../src/context';
import { defineSystem, removeSystem } from '../src/ecs/system';
import type { Game } from '../src/game/game';
import type { Input } from '../src/input/input';
import { handleEditorMessage } from '../src/link/handlers';
import { RapierPhysics } from '../src/physics/rapier-backend';
import '../src/systems';
import { Ui } from '../src/ui/ui';
import { installDom, type FakeDom } from './dom';
import { heldGame, sceneFiles } from './game-fixture';
import { bar, button, canvas, text, uiFiles, type FakeLayer } from './ui-controller-fixtures';

const loaded = vi.hoisted(() => ({ imports: 0, layers: [] as unknown[] }));
vi.mock('../src/ui/loader', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../src/ui/loader')>();
  const { FakeLayer } = await import('./ui-controller-fixtures');
  return {
    ...actual,
    importCanvasLayer: async () => {
      loaded.imports++;
      return {
        createCanvasLayer: (host: ConstructorParameters<typeof FakeLayer>[0]) => {
          const layer = new FakeLayer(host);
          loaded.layers.push(layer);
          return layer;
        },
      };
    },
  };
});

const Title = canvas('Title', { open: 'firstStart' }, [
  text('TitleText', 'Gem Island', [400, 80]),
  button('Play', 'Play', [400, 200], { action: 'close' }),
  button('Options', 'Options', [400, 270], { action: 'open', target: 'Options' }),
  button('Quit', 'Quit', [400, 340], { target: 'quit', sound: 'assets/sfx/click.ogg' }),
]);
const Options = canvas('Options', { back: 'previous' }, [button('Back', 'Back', [400, 200], { action: 'previous' }), button('Volume', 'Volume', [400, 300])]);
const Grid = canvas('Grid', {}, [button('A', 'A', [200, 150]), button('B', 'B', [600, 150]), button('C', 'C', [200, 300]), button('D', 'D', [600, 300])]);
const Ordered = canvas('Ordered', {}, [
  button('Third', 'Third', [400, 100], { order: 3 }),
  button('First', 'First', [400, 200], { order: 1 }),
  button('Second', 'Second', [400, 300], { order: 2 }),
]);
const Pause = canvas('Pause', { pauseMenu: true }, [button('Resume', 'Resume', [400, 180], { action: 'resume' }), button('Restart', 'Restart', [400, 260], { action: 'restart' })]);
const HUD = canvas('HUD', { kind: 'hud', open: 'start', backdrop: [0, 0, 0, 0] }, [
  text('Score', 'Score {score}', [100, 30]),
  bar('Health', [100, 70]),
  button('MenuButton', 'Menu', [700, 40], { action: 'open', target: 'Pause' }, [120, 40]),
  button('Shop', 'Shop', [700, 400], {}, [120, 40]),
]);
const CANVASES = [Title, Options, Grid, Ordered, Pause, HUD];

let dom: FakeDom;
beforeAll(async () => {
  await new RapierPhysics().init();
});
afterEach(() => {
  removeSystem('UiProbe');
  dom?.restore();
});

interface Probe {
  enter: number;
  presses: number;
  held: number;
  walking: number;
  ctx: SystemContext | null;
}

/** Counts what gameplay sees: Enter, left mouse presses (edges and held) and W. */
function probe(): Probe {
  const p: Probe = { enter: 0, presses: 0, held: 0, walking: 0, ctx: null };
  defineSystem({
    name: 'UiProbe',
    start(ctx) {
      p.ctx = ctx;
    },
    update(ctx) {
      if (ctx.input.key('Enter')) p.enter++;
      if (ctx.input.mouseButtonDown(0)) p.presses++;
      if (ctx.input.mouseButton(0)) p.held++;
      if (ctx.input.key('KeyW')) p.walking++;
    },
  });
  return p;
}

async function game(canvases = CANVASES): Promise<Game> {
  dom = installDom({ ...sceneFiles(), ...uiFiles(canvases) });
  return heldGame(dom);
}

const layerOf = (): FakeLayer => loaded.layers.at(-1) as FakeLayer;
const ui = (g: Game) => g.test.state({ ids: [] }).ui;
const center = (g: Game, name: string): { x: number; y: number } => {
  const b = ui(g).buttons.find((x) => x.name === name)!;
  return { x: b.x, y: b.y };
};
const ctxOf = (g: Game): SystemContext => (g as unknown as { ctx: SystemContext }).ctx;

/** Presses a key now (down this step, up the next) and runs `steps` steps. */
async function tap(g: Game, key: string, steps = 3): Promise<void> {
  g.test.schedule([{ step: g.test.status().step, key }]);
  await g.test.advance(steps);
}

/** Clicks at a canvas point now (down this step, up the next) and runs `steps` steps. */
async function click(g: Game, at: { x: number; y: number }, steps = 3): Promise<void> {
  g.test.schedule([{ step: g.test.status().step, mouse: 'click', ...at }]);
  await g.test.advance(steps);
}

describe('Blender-authored menus', () => {
  it('opens the title on the first start, paused; Enter on Play closes it and play resumes without the key', async () => {
    const p = probe();
    const g = await game();
    expect(g.ui.canvases).toEqual(['Title', 'Options', 'Grid', 'Ordered', 'Pause', 'HUD']);
    await g.test.begin();
    await g.test.advance(2);
    expect(g.test.status()).toMatchObject({ state: 'paused', screen: 'Title' });
    const state = ui(g);
    expect(state).toMatchObject({ screen: 'Title', focus: 'Play', open: ['HUD', 'Title'] });
    expect(state.buttons.map((b) => [b.canvas, b.name, b.label, b.disabled])).toEqual([
      ['Title', 'Play', 'Play', false],
      ['Title', 'Options', 'Options', false],
      ['Title', 'Quit', 'Quit', false],
    ]);
    expect(state.buttons[0]).toMatchObject({ x: 400, y: 200, width: 200, height: 50 });
    expect(state.texts).toMatchObject({ 'Title/TitleText': 'Gem Island', 'HUD/Score': 'Score ' });
    expect(layerOf().open).toEqual({ huds: ['HUD'], menu: 'Title' });
    await g.test.advance(30);
    // One step ran (the systems started, as for a title shown from start()), then the title paused it.
    expect(g.test.status().gameTime).toBeCloseTo(1 / 60, 3);

    g.test.schedule([{ step: 40, key: 'Enter', hold: 0.2 }]);
    await g.test.advance(20);
    expect(g.test.status()).toMatchObject({ state: 'playing', screen: null });
    expect(ui(g).open).toEqual(['HUD']);
    expect(ui(g).buttons.map((b) => b.name)).toEqual(['MenuButton', 'Shop']); // no menu: the HUDs' buttons
    expect(p.enter).toBe(0);
    expect(g.test.status().gameTime).toBeGreaterThan(0);
  });

  it('moves by position with arrows and W/S; a W held through the menu walks on once it closes', async () => {
    const p = probe();
    const g = await game();
    await g.test.begin();
    g.test.schedule([
      { step: 5, key: 'ArrowDown' },
      { step: 7, key: 'KeyS' },
      { step: 9, key: 'ArrowDown' }, // wraps to the top
      { step: 11, key: 'ArrowUp' }, // and back to the bottom
      { step: 20, key: 'KeyW', hold: 2 }, // Quit → Options, then held
      { step: 30, key: 'ArrowUp' }, // Options → Play
      { step: 40, key: 'Enter' },
    ]);
    const focus: Array<string | null> = [];
    for (const until of [6, 8, 10, 12, 21, 31]) {
      await g.test.advanceTo(until / 60);
      focus.push(g.ui.focused);
    }
    expect(focus).toEqual(['Options', 'Quit', 'Play', 'Quit', 'Options', 'Play']);
    expect(p.walking).toBe(0);
    await g.test.advance(20);
    expect(g.ui.current).toBeNull();
    expect(p.walking).toBeGreaterThan(5); // W is held again for gameplay
    expect(p.enter).toBe(0);
  });

  it('navigates a 2 × 2 grid by position, Tab in reading order, and an ordered menu by order', async () => {
    const g = await game();
    await g.test.begin();
    await g.test.advance(1);
    g.ui.open('Grid');
    expect([g.ui.current, g.ui.focused]).toEqual(['Grid', 'A']);
    const keys = ['ArrowRight', 'ArrowDown', 'ArrowLeft', 'ArrowUp', 'ArrowUp', 'Tab', 'Tab'];
    const seen: Array<string | null> = [];
    for (const key of keys) {
      g.test.schedule([{ key }]);
      await g.test.advance(2);
      seen.push(g.ui.focused);
    }
    expect(seen).toEqual(['B', 'D', 'C', 'A', 'C', 'D', 'A']);
    g.test.schedule([{ key: 'ShiftLeft', hold: 0.1 }, { key: 'Tab' }]);
    await g.test.advance(2);
    expect(g.ui.focused).toBe('D');

    g.ui.open('Ordered');
    expect(g.ui.focused).toBe('First');
    const order: Array<string | null> = [];
    for (const key of ['ArrowDown', 'ArrowDown', 'ArrowDown', 'ArrowUp']) {
      g.test.schedule([{ key }]);
      await g.test.advance(2);
      order.push(g.ui.focused);
    }
    expect(order).toEqual(['Second', 'Third', 'First', 'Third']);
  });

  it('goes back with `previous`: Options from the title, Escape returns to it; the pause holds throughout', async () => {
    const g = await game();
    await g.test.begin();
    await g.test.advance(1);
    await tap(g, 'ArrowDown');
    await tap(g, 'Enter');
    expect([g.ui.current, g.ui.focused, g.state]).toEqual(['Options', 'Back', 'paused']);
    await tap(g, 'Escape');
    expect([g.ui.current, g.ui.focused, g.state]).toEqual(['Title', 'Play', 'paused']);
    // The Back button does the same.
    g.ui.open('Options');
    await click(g, center(g, 'Back'));
    expect(g.ui.current).toBe('Title');
    g.ui.close();
    await g.test.advance(1);
    expect([g.ui.current, g.state]).toEqual([null, 'playing']);
  });

  it('opens the pause menu canvas with Escape and P; Restart reopens the start HUD but not the title', async () => {
    const g = await game();
    await g.test.begin();
    await g.test.advance(1);
    g.ui.close('Title');
    await g.test.advance(2);
    expect(g.state).toBe('playing');
    await tap(g, 'Escape');
    expect(g.test.status()).toMatchObject({ state: 'paused', screen: 'Pause' });
    await tap(g, 'KeyP');
    expect(g.test.status()).toMatchObject({ state: 'playing', screen: null });
    await tap(g, 'Escape');
    await tap(g, 'ArrowDown');
    await tap(g, 'Enter', 60);
    expect(g.test.status()).toMatchObject({ state: 'playing', screen: null, sceneChanges: 1 });
    expect(ui(g).open).toEqual(['HUD']);
    // ctx.ui.pauseMenu(false) turns the canvas off too.
    g.ui.pauseMenu(false);
    await tap(g, 'Escape');
    expect(g.ui.current).toBeNull();
  });

  it('keeps HUDs open across a level load; a test reset brings the title back', async () => {
    const p = probe();
    const g = await game();
    await g.test.begin();
    await g.test.advance(1);
    g.ui.close();
    await g.test.advance(1);
    await p.ctx!.loadScene('level2', { fade: 0 });
    await g.test.advance(2);
    expect(g.scene.name).toBe('level2');
    expect(ui(g).open).toEqual(['HUD']);
    await g.test.begin({ reset: true });
    await g.test.advance(1);
    expect(ui(g)).toMatchObject({ screen: 'Title', open: ['HUD', 'Title'] });
    expect(g.state).toBe('paused');
  });

  it('activates a button clicked at its state() position; menus and HUD buttons keep clicks from gameplay', async () => {
    const p = probe();
    const g = await game();
    const quits: string[] = [];
    await g.test.begin();
    await g.test.advance(1);
    g.ui.on('quit', (ctx, info) => void quits.push(`${info.canvas}/${info.button}:${ctx === ctxOf(g)}`));
    const quit = center(g, 'Quit');
    g.test.schedule([{ step: g.test.status().step, mouse: 'move', ...quit }]);
    await g.test.advance(2);
    expect(g.ui.focused).toBe('Quit'); // hover focuses
    await click(g, quit);
    expect(quits).toEqual(['Title/Quit:true']);
    expect(g.ui.current).toBe('Title'); // an event button leaves it open
    // A press that leaves the button before the release does not click it.
    const play = center(g, 'Play');
    const now = g.test.status().step;
    g.test.schedule([{ step: now, mouse: 'down', ...play }, { step: now + 2, mouse: 'up', x: 20, y: 20 }]);
    await g.test.advance(4);
    expect(g.ui.current).toBe('Title');
    await click(g, play);
    expect(g.ui.current).toBeNull();

    // A menu that keeps the game running: every click is still the menu's.
    g.ui.screen('Grid', { pause: false });
    await g.test.advance(1);
    expect(g.state).toBe('playing');
    await click(g, { x: 20, y: 440 });
    await click(g, center(g, 'A'));
    g.test.schedule([{ step: g.test.status().step, mouse: 'down', x: 400, y: 20, hold: 0.1 }]);
    await g.test.advance(3);
    expect([p.presses, p.held]).toEqual([0, 0]);
    g.ui.close();
    await g.test.advance(10);
    // With no menu: a HUD button's click is the UI's, a click elsewhere reaches gameplay.
    const shops: string[] = [];
    g.ui.on('Shop', (_, info) => void shops.push(info.canvas));
    await click(g, center(g, 'Shop'));
    expect([shops, p.presses]).toEqual([['HUD'], 0]);
    await click(g, { x: 300, y: 250 });
    expect(p.presses).toBe(1);
    await click(g, center(g, 'MenuButton'));
    expect([g.ui.current, g.state]).toEqual(['Pause', 'paused']);
  });

  it('ctx.ui.on: one handler per event, registering again replaces it; unsubscribe removes only its own', async () => {
    const g = await game();
    await g.test.begin();
    await g.test.advance(1);
    const calls: string[] = [];
    const off1 = g.ui.on('quit', () => void calls.push('first'));
    const off2 = g.ui.on('quit', () => void calls.push('second'));
    off1();
    await click(g, center(g, 'Quit'));
    expect(calls).toEqual(['second']);
    off2();
    await click(g, center(g, 'Quit'));
    expect(calls).toEqual(['second']);
    // Buttons without a target call the handler of their object name.
    g.ui.on('Options', () => void calls.push('options'));
    await click(g, center(g, 'Options'));
    expect([calls, g.ui.current]).toEqual([['second', 'options'], 'Options']);
  });

  it('text(), show(), hide() and bar() override elements until null, and a restart drops the overrides', async () => {
    const p = probe();
    const g = await game();
    await g.test.begin();
    await g.test.advance(1);
    g.ui.close();
    await g.test.advance(1);
    p.ctx!.store.score = 5;
    expect(ui(g).texts['HUD/Score']).toBe('Score 5');
    g.ui.text('Score', 'Hi');
    expect(ui(g).texts['HUD/Score']).toBe('Hi');
    g.ui.text('HUD/Score', null);
    expect(ui(g).texts['HUD/Score']).toBe('Score 5');
    expect(ui(g).hidden).toEqual([]);
    g.ui.hide('Score');
    expect(ui(g).texts['HUD/Score']).toBeUndefined();
    expect(ui(g).hidden).toEqual(['HUD/Score']);
    expect(g.ui.element('Score')).toMatchObject({ canvas: 'HUD', name: 'Score', type: 'text', visible: false });
    g.ui.show('Score');
    expect(g.ui.element('HUD/Score')).toMatchObject({ visible: true, text: 'Score 5' });
    expect(g.ui.element('Nope')).toBeNull();
    g.ui.bar('Health', 0.25);
    expect(layerOf().bar('HUD', 'Health')).toBe(0.25);
    g.ui.hide('TitleText'); // a canvas that is not open
    g.ui.text('Score', 'Override');
    void p.ctx!.restart({ fade: 0 });
    await g.test.advance(3);
    expect(layerOf().bar('HUD', 'Health')).toBeNull();
    expect(ui(g).texts['HUD/Score']).toBe('Score '); // the store was cleared too
    expect(g.ui.element('TitleText')?.visible).toBe(true);
  });

  it('handles ui.canvas (keeping the open canvas and its focus), ui.open, ui.close and ui.remove', async () => {
    const g = await game();
    await g.test.begin();
    await g.test.advance(1);
    await tap(g, 'ArrowUp'); // Play → Quit
    expect(g.ui.focused).toBe('Quit');
    const edited = structuredClone(Title);
    edited.rev = 2;
    edited.elements.find((e) => e.name === 'QuitLabel')!.text!.body = 'Exit';
    edited.elements.find((e) => e.name === 'Play')!.transform.y = 120;
    const send = (msg: Record<string, unknown>): void => handleEditorMessage(g, msg as never, () => {});
    send({ t: 'ui.canvas', canvas: 'Title', data: edited });
    await g.ui.afterLoad(() => {});
    expect([g.ui.current, g.ui.focused, g.state]).toEqual(['Title', 'Exit', 'paused']);
    expect(center(g, 'Play')).toEqual({ x: 400, y: 120 });

    send({ t: 'ui.open', canvas: 'Options' });
    expect(g.ui.current).toBe('Options'); // at once: the canvases are loaded
    send({ t: 'ui.close', canvas: 'Options' });
    expect([g.ui.current, g.state]).toEqual([null, 'playing']);
    send({ t: 'ui.remove', canvas: 'HUD' });
    await g.ui.afterLoad(() => {});
    expect(g.ui.canvases).not.toContain('HUD');
    expect(ui(g).open).toEqual([]);
    // A new canvas whose fonts take a while: listed and opened once it is ready.
    const layer = layerOf();
    let ready = (): void => {};
    layer.hold = new Promise((resolve) => (ready = resolve));
    send({ t: 'ui.canvas', canvas: 'Late', data: { ...structuredClone(Options), canvas: 'Late' } });
    send({ t: 'ui.open', canvas: 'Late' });
    await g.ui.afterLoad(() => {});
    expect([g.ui.canvases.includes('Late'), ui(g).open, g.ui.current]).toEqual([false, [], null]);
    ready();
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect([g.ui.canvases.includes('Late'), ui(g).open, g.ui.current]).toEqual([true, ['Late'], 'Late']);
  });

  it('test state lists buttons in navigation order: UiButton.order, else reading order', async () => {
    const g = await game();
    await g.test.begin();
    await g.test.advance(1);
    g.ui.open('Ordered'); // elements Third, First, Second
    expect(ui(g).buttons.map((b) => b.name)).toEqual(['First', 'Second', 'Third']);
    g.ui.open('Grid'); // elements A B / C D, reading order
    expect(ui(g).buttons.map((b) => b.name)).toEqual(['A', 'B', 'C', 'D']);
    const swapped = canvas('Swapped', {}, [button('Low', 'Low', [400, 300]), button('High', 'High', [400, 100])]);
    handleEditorMessage(g, { t: 'ui.canvas', canvas: 'Swapped', data: swapped }, () => {});
    await g.ui.afterLoad(() => {});
    await new Promise((resolve) => setTimeout(resolve, 0)); // the layer has it once its assets are in
    g.ui.open('Swapped');
    expect(ui(g).buttons.map((b) => b.name)).toEqual(['High', 'Low']);
  });

  it("start canvases open after the systems' start(): a HUD under the title shows what start() set", async () => {
    defineSystem({
      name: 'UiProbe',
      start(ctx) {
        ctx.store.score = 7;
      },
    });
    const g = await game();
    await g.test.begin();
    await g.test.advance(1);
    expect(ui(g)).toMatchObject({ screen: 'Title', open: ['HUD', 'Title'] });
    expect(ui(g).texts['HUD/Score']).toBe('Score 7');
    expect(g.state).toBe('paused');
  });

  it('start HUDs open even when a system shows a code title from start() (which pauses that step)', async () => {
    defineSystem({
      name: 'UiProbe',
      start(ctx) {
        ctx.ui.screen('codeTitle', { title: 'Code title', buttons: [{ label: 'Go', action: 'close' }] });
      },
    });
    const g = await game([HUD]);
    await g.test.begin();
    await g.test.advance(2);
    expect([ui(g).screen, ui(g).open, g.state]).toEqual(['codeTitle', ['HUD', 'codeTitle'], 'paused']);
  });

  it('a key on the step that opens a start canvas still counts (a playtest Enter at 0 s closes the title)', async () => {
    const p = probe();
    const g = await game();
    await g.test.begin();
    g.test.schedule([{ step: 0, key: 'Enter' }]);
    await g.test.advance(3);
    expect([g.ui.current, g.state]).toEqual([null, 'playing']);
    // Systems on that first step ran before the title opened and could see the key once (as for DOM screens).
    expect(p.enter).toBeLessThanOrEqual(1);
  });

  it('a click on the step that opens a start canvas still counts (a playtest click at 0 s on Play closes the title)', async () => {
    const p = probe();
    const g = await game();
    await g.test.begin();
    await g.test.advance(1);
    const play = center(g, 'Play'); // from state().ui of an earlier run, as an agent would take it
    await g.test.begin({ reset: true });
    Object.assign(p, { presses: 0, held: 0 });
    g.test.schedule([{ at: 0, mouse: 'click', ...play }]);
    await g.test.advance(3);
    expect([g.ui.current, g.state]).toEqual([null, 'playing']);
    // Systems on that first step ran before the title opened and saw the press once; from then on it
    // was the menu's, as for a press while a menu is open (no held button, no release).
    expect([p.presses, p.held]).toEqual([1, 1]);

    // A down and up in the same step, at 0 s: the title opens and is pressed in that step.
    await g.test.begin({ reset: true });
    g.test.schedule([{ step: 0, mouse: 'down', ...play }, { step: 0, mouse: 'up', ...play }]);
    await g.test.advance(2);
    expect([g.ui.current, g.state]).toEqual([null, 'playing']);

    // A click elsewhere at 0 s leaves the title open.
    await g.test.begin({ reset: true });
    g.test.schedule([{ at: 0, mouse: 'click', x: 20, y: 20 }]);
    await g.test.advance(3);
    expect([g.ui.current, g.state]).toEqual(['Title', 'paused']);
  });

  it("a press from an earlier frame doesn't reach a menu that opens later", async () => {
    const opened: number[] = [];
    defineSystem({
      name: 'UiProbe',
      update(ctx) {
        if (ctx.frame === 5 && !opened.length) {
          opened.push(ctx.frame);
          ctx.ui.open('Title');
        }
      },
    });
    const g = await game([Title]);
    await g.test.begin();
    await g.test.advance(1);
    g.ui.close();
    const play = { x: 400, y: 200 };
    // The press lands on the step before a system opens the title; the release comes after it opened.
    g.test.schedule([{ step: 4, mouse: 'down', ...play }, { step: 6, mouse: 'up', ...play }]);
    await g.test.advance(10);
    expect(opened).toEqual([5]);
    expect([g.ui.current, g.state]).toEqual(['Title', 'paused']);
  });

  it('state().ui.size is the canvas size its button x/y are in', async () => {
    const g = await game();
    await g.test.begin();
    await g.test.advance(1);
    expect(ui(g).size).toEqual([800, 450]);
    Object.assign(dom.canvas, { clientWidth: 1280, clientHeight: 720 });
    g.ui.layout(1280, 720); // as the game does on resize
    expect(ui(g).size).toEqual([1280, 720]);
    // A resize the game has not seen yet (its observer runs before the next frame) counts at once.
    Object.assign(dom.canvas, { clientWidth: 960, clientHeight: 540 });
    expect(ui(g).size).toEqual([960, 540]);
  });

  it('refresh() picks up what was exported since the page loaded; Play and editor reloads call it', async () => {
    const files: Record<string, string> = { ...sceneFiles(), ...uiFiles([Title, HUD]) };
    dom = installDom(files);
    const g = await heldGame(dom);
    await g.test.begin();
    await g.test.advance(1);
    expect(ui(g).open).toEqual(['HUD', 'Title']);
    const layer = layerOf();
    const set = vi.spyOn(layer, 'set');
    // Nothing exported since: only the index is fetched.
    await g.ui.refresh();
    expect(set).not.toHaveBeenCalled();
    // A new Title (rev 2), HUD removed, a new canvas: as a re-export rewrites the files.
    const edited = structuredClone(Title);
    edited.rev = 2;
    edited.elements.find((e) => e.name === 'QuitLabel')!.text!.body = 'Exit';
    const extra = { ...structuredClone(Options), canvas: 'Extra' };
    const next = uiFiles([edited, extra]);
    for (const [url, file] of Object.entries(next)) files[url] = file;
    await g.ui.refresh();
    expect(set.mock.calls.map(([data]) => [data.canvas, data.rev])).toEqual([['Title', 2], ['Extra', 1]]);
    expect(g.ui.canvases).toEqual(['Title', 'Extra']);
    expect([g.ui.current, ui(g).open]).toEqual(['Title', ['Title']]); // still open, HUD gone
    expect(g.ui.element('QuitLabel')?.text).toBe('Exit');
    // The editor's Play and scene reloads refresh (a held test clock ignores Play).
    const refresh = vi.spyOn(g.ui, 'refresh');
    handleEditorMessage(g, { t: 'scene.load', scene: 'basic' }, () => {});
    expect(refresh).toHaveBeenCalledTimes(1);
    await g.idle();
    g.test.end();
    handleEditorMessage(g, { t: 'play.start' }, () => {});
    expect(refresh).toHaveBeenCalledTimes(2);
  });

  it('a menu opened and closed on a stopped game leaves it stopped', async () => {
    const g = await game();
    await g.test.begin();
    await g.test.advance(1);
    g.test.end();
    g.stop();
    await g.idle();
    expect([g.state, g.ui.current]).toEqual(['stopped', null]);
    handleEditorMessage(g, { t: 'ui.open', canvas: 'Options' }, () => {});
    expect([g.state, g.ui.current]).toEqual(['stopped', 'Options']);
    handleEditorMessage(g, { t: 'ui.close', canvas: 'Options' }, () => {});
    expect([g.state, g.ui.current, g.ui.holdsPause]).toEqual(['stopped', null, false]);
  });

  it('draws over the frame only through the raw renderer, and hides in the Scene view', async () => {
    const g = await game();
    let draws = 0;
    g.three.renderer = { kind: 'webgl', raw: {}, render: () => void draws++, setSize() {}, dispose() {} } as never;
    await g.test.begin();
    await g.test.advance(1);
    g.test.render();
    const layer = layerOf();
    expect([draws, layer.renders]).toEqual([1, 1]);
    expect(layer.updates).toBeGreaterThan(0);
    g.test.end();
    g.setView('scene');
    expect(layer.hidden).toBe(true);
    g.setView('game');
    expect(layer.hidden).toBe(false);
  });
});

describe('a game without Blender UI', () => {
  it('loads no canvas code and reports empty canvas state', async () => {
    const imports = loaded.imports;
    dom = installDom(sceneFiles());
    const g = await heldGame(dom);
    await g.test.begin();
    await g.test.advance(2);
    expect(loaded.imports).toBe(imports);
    expect(g.ui.canvases).toEqual([]);
    expect(ui(g)).toEqual({ screen: null, focus: null, buttons: [], size: [800, 450], open: [], animating: [], texts: {}, hidden: [], locale: 'en' });
    g.ui.open('Title'); // warns once, nothing opens
    expect(g.ui.current).toBeNull();
    g.ui.screen('title', { title: 'Hi' }); // DOM screens as before
    expect(ui(g)).toMatchObject({ screen: 'title', open: ['title'] });
  });
});

describe('canvas menus with a gamepad, and button sounds', () => {
  it('moves with the d-pad and stick, presses with A, backs out with B, pauses with Start; sounds play', async () => {
    dom = installDom(uiFiles(CANVASES));
    const pad = { id: 'pad', axes: [0, 0, 0, 0], buttons: Array.from({ length: 17 }, () => ({ pressed: false, value: 0 })) };
    const consumed: number[] = [];
    const input = { gamepad: pad, keyDown: () => false, key: () => false, consume() {}, consumeGamepad: (i: number) => void consumed.push(i) } as unknown as Input;
    let state = 'playing';
    const sounds: string[] = [];
    const events: string[] = [];
    const ui = new Ui(dom.canvas as unknown as HTMLCanvasElement, {
      input,
      state: () => state,
      pause: (p) => void (state = p ? 'paused' : 'playing'),
      ctx: () => null as never,
      restart: () => void events.push('restart'),
      indexUrl: '/export/ui/index.json',
      sounds: () => (url) => void sounds.push(url),
      warn: () => {},
    });
    await ui.load();
    const press = (i: number): void => {
      pad.buttons[i]!.pressed = true;
      ui.update();
      pad.buttons[i]!.pressed = false;
      ui.update();
    };
    ui.afterSimulate(); // the first start: the title opens after the first step...
    ui.update(); // ...and pauses
    expect([ui.current, state, ui.focused]).toEqual(['Title', 'paused', 'Play']);
    press(13); // d-pad down
    pad.axes[1] = 0.9; // stick down
    ui.update();
    pad.axes[1] = 0;
    ui.update();
    expect(ui.focused).toBe('Quit');
    ui.on('quit', () => void events.push('quit'));
    press(0); // A
    expect(events).toEqual(['quit']);
    expect(sounds).toEqual(['http://game.test/export/files/assets/sfx/click.ogg?v=0123456789']);
    expect(consumed).toContain(0);
    press(12); // d-pad up
    press(0); // Options → opens it, replacing the title
    expect(ui.current).toBe('Options');
    press(1); // B: back = previous
    expect([ui.current, state]).toEqual(['Title', 'paused']);
    ui.close();
    expect(state).toBe('playing');
    press(9); // Start: the pause menu canvas
    expect([ui.current, state]).toEqual(['Pause', 'paused']);
    press(9);
    expect([ui.current, state]).toEqual([null, 'playing']);
    press(9);
    press(1); // B resumes the pause menu (its back is none)
    expect([ui.current, state]).toEqual([null, 'playing']);
    ui.dispose();
  });
});
