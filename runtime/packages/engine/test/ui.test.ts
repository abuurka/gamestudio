// Menu screens (ctx.ui): pausing through the normal pause path, keyboard, gamepad-free mouse clicks
// from scripted input, the pause menu, and key consumption.
import { afterEach, beforeAll, beforeEach, describe, expect, it } from 'vitest';
import type { SystemContext } from '../src/context';
import { defineSystem, removeSystem } from '../src/ecs/system';
import { RapierPhysics } from '../src/physics/rapier-backend';
import '../src/systems';
import type { FakeDom, FakeElement } from './dom';
import { heldGame, installSceneDom } from './game-fixture';
import { Ui } from '../src/ui/ui';
import type { Input } from '../src/input/input';

let dom: FakeDom;
beforeAll(async () => {
  await new RapierPhysics().init();
});
beforeEach(() => {
  dom = installSceneDom();
});
afterEach(() => {
  removeSystem('Menus');
  dom.restore();
});

interface Probe {
  timer: number;
  played: number;
  quit: number;
  jumps: number;
  ctx: SystemContext | null;
}

/** A title screen on start (Play / Options (disabled) / Quit), the pause menu, a timer and a Space counter. */
function menus(): Probe {
  const probe: Probe = { timer: 0, played: 0, quit: 0, jumps: 0, ctx: null };
  defineSystem({
    name: 'Menus',
    start(ctx) {
      probe.ctx = ctx;
      ctx.ui.pauseMenu();
      ctx.every(0.1, () => probe.timer++);
      if (!ctx.store.started) {
        ctx.ui.screen('title', {
          title: 'Gem Island',
          subtitle: 'Collect every gem',
          buttons: [
            { label: 'Play', action: () => { probe.played++; ctx.store.started = true; ctx.ui.close(); } },
            { label: 'Options', disabled: true },
            { label: 'Quit', action: () => void probe.quit++ },
          ],
        });
      }
    },
    update(ctx) {
      if (ctx.input.key('Space') || ctx.input.key('Enter')) probe.jumps++;
    },
  });
  return probe;
}

const texts = (root: unknown): string[] => (root as FakeElement).all().map((e) => e.textContent).filter(Boolean);

describe('ctx.ui', () => {
  it('shows the title paused; Enter on Play closes it and play resumes, without the key reaching gameplay', async () => {
    const probe = menus();
    const game = await heldGame(dom);
    await game.test.begin();
    await game.test.advance(2);
    expect(game.test.status()).toMatchObject({ state: 'paused', screen: 'title' });
    expect(texts(game.ui.overlay)).toEqual(expect.arrayContaining(['Gem Island', 'Collect every gem', 'Play', 'Options', 'Quit']));
    const state = game.test.state();
    expect(state.ui).toMatchObject({ screen: 'title', focus: 'Play' });
    expect(state.ui.buttons.map((b) => [b.label, b.disabled])).toEqual([['Play', false], ['Options', true], ['Quit', false]]);
    const time = game.test.status().gameTime;
    await game.test.advance(30);
    expect(game.test.status().gameTime).toBe(time); // timers, systems and physics wait
    expect(probe.timer).toBe(0);

    // Down skips the disabled button; Up wraps back to Play. Enter activates; Space too.
    game.test.schedule([
      { step: 40, key: 'ArrowDown' },
      { step: 42, key: 'ArrowUp' },
      { step: 44, key: 'ArrowUp' },
      { step: 46, key: 'Space' },
    ]);
    await game.test.advance(9);
    expect(game.ui.focused).toBe('Quit');
    await game.test.advance(2);
    expect(game.ui.focused).toBe('Play');
    await game.test.advance(2);
    expect(game.ui.focused).toBe('Quit');
    await game.test.advance(3);
    expect(probe.quit).toBe(1);
    expect(game.ui.current).toBe('title'); // a function action leaves it open
    game.test.schedule([{ step: 50, key: 'ArrowDown' }, { step: 52, key: 'Enter', hold: 0.2 }]);
    await game.test.advance(6);
    expect(probe.played).toBe(1);
    expect(game.test.status()).toMatchObject({ state: 'playing', screen: null });
    await game.test.advance(20);
    expect(probe.timer).toBeGreaterThan(0);
    expect(probe.jumps).toBe(0); // the Enter that closed the menu never reached gameplay
  });

  it('a key pressed on the step that opens a screen reaches it (a playtest pressing Enter at 0 s)', async () => {
    const probe = menus();
    const game = await heldGame(dom);
    await game.test.begin();
    game.test.schedule([{ step: 0, key: 'Enter' }]);
    await game.test.advance(3);
    expect(probe.played).toBe(1);
    expect(game.test.status()).toMatchObject({ state: 'playing', screen: null });
    // Systems on that first step ran before the title opened and could see the key once (as before).
    expect(probe.jumps).toBeLessThanOrEqual(1);
  });

  it('opens and closes the pause menu with Escape or P; Restart starts over', async () => {
    const probe = menus();
    const game = await heldGame(dom);
    await game.test.begin();
    await game.test.advance(1);
    game.ui.close('title');
    probe.ctx!.store.started = true;
    await game.test.advance(10);
    game.test.schedule([{ step: 12, key: 'Escape' }]);
    await game.test.advance(3);
    expect(game.test.status()).toMatchObject({ state: 'paused', screen: 'pause' });
    expect(texts(game.ui.overlay)).toEqual(expect.arrayContaining(['Paused', 'Resume', 'Restart']));
    const timer = probe.timer;
    await game.test.advance(30);
    expect(probe.timer).toBe(timer);
    game.test.schedule([{ step: 45, key: 'KeyP' }]);
    await game.test.advance(2);
    expect(game.test.status()).toMatchObject({ state: 'playing', screen: null });
    game.test.schedule([{ step: 48, key: 'Escape' }, { step: 50, key: 'ArrowDown' }, { step: 52, key: 'Enter' }]);
    await game.test.advance(40);
    // Restart: the store is cleared, so the title comes back.
    expect(game.test.status()).toMatchObject({ screen: 'title', sceneChanges: 1 });
  });

  it('activates a button clicked by a scripted mouse', async () => {
    const probe = menus();
    const game = await heldGame(dom);
    await game.test.begin();
    await game.test.advance(1);
    const quit = (game.ui.overlay as unknown as FakeElement).all().find((e) => e.dataset.label === 'Quit')!;
    quit.getBoundingClientRect = () => ({ left: 300, top: 260, width: 200, height: 40 });
    expect(game.test.state().ui.buttons[2]).toMatchObject({ label: 'Quit', x: 400, y: 280 });
    game.test.schedule([{ step: 3, mouse: 'click', x: 400, y: 280 }]);
    await game.test.advance(4);
    expect(probe.quit).toBe(1);
    game.test.schedule([{ step: 6, mouse: 'click', x: 20, y: 20 }]); // not on a button
    await game.test.advance(3);
    expect(probe.quit).toBe(1);
  });

  it('can keep the game running under a screen, and closes menus on Stop', async () => {
    const game = await heldGame(dom);
    await game.test.begin();
    game.ui.screen('banner', { title: 'Ready?', pause: false, hint: false });
    await game.test.advance(3);
    expect(game.test.status()).toMatchObject({ state: 'playing', screen: 'banner' });
    game.ui.screen('over', { title: 'Game over', buttons: [{ label: 'Restart', action: 'restart' }] });
    await game.test.advance(1);
    expect(game.state).toBe('paused');
    game.test.end();
    game.stop();
    await game.idle();
    expect(game.ui.current).toBeNull();
    expect(game.state).toBe('stopped');
  });
});

describe('ctx.ui with a gamepad', () => {
  it('moves with the d-pad and stick, presses with A, backs out with B and pauses with Start', () => {
    const pad = { id: 'pad', axes: [0, 0, 0, 0], buttons: Array.from({ length: 17 }, () => ({ pressed: false, value: 0 })) };
    const consumed: number[] = [];
    const input = {
      gamepad: pad,
      keyDown: () => false,
      key: () => false,
      consume() {},
      consumeGamepad: (i: number) => void consumed.push(i),
    } as unknown as Input;
    let state = 'playing';
    const pressed: string[] = [];
    const ui = new Ui(dom.canvas as unknown as HTMLCanvasElement, {
      input,
      state: () => state,
      pause: (p) => void (state = p ? 'paused' : 'playing'),
      ctx: () => null as never,
      restart: () => void pressed.push('restart'),
    });
    const press = (i: number) => {
      pad.buttons[i]!.pressed = true;
      ui.update();
      pad.buttons[i]!.pressed = false;
      ui.update();
    };
    ui.pauseMenu();
    press(9); // Start
    expect([ui.current, state]).toEqual(['pause', 'paused']);
    press(13); // d-pad down
    expect(ui.focused).toBe('Restart');
    pad.axes[1] = -0.9; // stick up
    ui.update();
    pad.axes[1] = 0;
    ui.update();
    expect(ui.focused).toBe('Resume');
    press(1); // B: back = resume
    expect([ui.current, state]).toEqual([null, 'playing']);
    ui.screen('over', { title: 'Game over', buttons: [{ label: 'Again', action: () => void pressed.push('again') }] });
    press(0); // A
    expect(pressed).toEqual(['again']);
    expect(consumed).toContain(0); // the A that pressed the button does not also jump
    ui.dispose();
  });
});
