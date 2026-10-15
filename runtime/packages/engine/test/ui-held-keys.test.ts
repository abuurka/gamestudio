// A movement key held through a menu walks the player once the menu closes: W moved the title's
// focus (and was hidden from gameplay meanwhile), but it is still down when Play closes the title.
import { afterEach, beforeAll, beforeEach, describe, expect, it } from 'vitest';
import { defineSystem, removeSystem } from '../src/ecs/system';
import { RapierPhysics } from '../src/physics/rapier-backend';
import '../src/systems';
import type { FakeDom } from './dom';
import { heldGame, installSceneDom } from './game-fixture';

let dom: FakeDom;
beforeAll(async () => {
  await new RapierPhysics().init();
});
beforeEach(() => {
  dom = installSceneDom();
});
afterEach(() => {
  removeSystem('HeldKeys');
  dom.restore();
});

describe('keys held through a menu', () => {
  it('a movement key held while the title is open counts as held after it closes; Enter does not', async () => {
    const probe = { walking: 0, walkingWhileOpen: 0, entered: 0, played: 0 };
    defineSystem({
      name: 'HeldKeys',
      start(ctx) {
        if (ctx.store.started) return;
        ctx.ui.screen('title', {
          buttons: [
            { label: 'Play', action: () => { probe.played++; ctx.store.started = true; ctx.ui.close(); } },
            { label: 'Quit', action: () => {} },
          ],
        });
      },
      update(ctx) {
        if (ctx.input.key('KeyW') && ctx.input.axis('vertical') > 0) {
          if (ctx.ui.current) probe.walkingWhileOpen++;
          else probe.walking++;
        }
        if (ctx.input.key('Enter')) probe.entered++;
      },
    });
    const game = await heldGame(dom);
    await game.test.begin();
    // As in the playtest: W held from 0.5 to 3.5 s; it moves the focus to Quit, the arrow brings it back,
    // and Enter at 1.5 s plays.
    game.test.schedule([
      { step: 30, key: 'KeyW', hold: 3 },
      { step: 60, key: 'ArrowDown' },
      { step: 90, key: 'Enter', hold: 0.2 },
    ]);
    await game.test.advance(85);
    expect(game.ui.current).toBe('title');
    expect(game.ui.focused).toBe('Play');
    await game.test.advance(20); // Enter: the title closes, W is still down
    expect(probe.played).toBe(1);
    expect(game.ui.current).toBe(null);
    await game.test.advance(60);
    expect(probe.walking).toBeGreaterThan(50); // before: 0, W stayed "used by the menu" until released
    expect(probe.walkingWhileOpen).toBe(0); // while the menu had it, gameplay did not see it
    expect(probe.entered).toBe(0); // the activating key stays hidden until it is released
    await game.test.advance(120); // W released at 3.5 s
    const walked = probe.walking;
    await game.test.advance(30);
    expect(probe.walking).toBe(walked);
  });
});
