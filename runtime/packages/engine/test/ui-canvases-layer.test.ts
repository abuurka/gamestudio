// The UI controller with the real canvas layer (src/ui/canvas): a game loads ui/index.json, opens its
// title on the first start, and a playtest clicks the buttons where test.state() says they are.
import { afterEach, beforeAll, describe, expect, it } from 'vitest';
import type { Game } from '../src/game/game';
import { handleEditorMessage } from '../src/link/handlers';
import { RapierPhysics } from '../src/physics/rapier-backend';
import '../src/systems';
import type { UiElementData } from '../src/ui/canvas/format';
import { installDom, type FakeDom } from './dom';
import { heldGame, sceneFiles } from './game-fixture';
import { bar, button, canvas, text, uiFiles } from './ui-controller-fixtures';

const Title = canvas('Title', { open: 'firstStart' }, [
  text('TitleText', 'Gem Island', [400, 80]),
  button('Play', 'Play', [400, 200], { action: 'close' }),
  button('Quit', 'Quit', [400, 300], { target: 'quit' }),
]);
const HUD = canvas('HUD', { kind: 'hud', open: 'start' }, [text('Score', 'Score {score:000}', [100, 30]), bar('Health', [100, 70], 'health')]);

/** A group element (no content of its own). */
function group(id: string, name: string, at: [number, number], visible = true): UiElementData {
  return {
    id,
    name,
    parent: null,
    type: 'group',
    visible,
    order: 0,
    transform: { x: at[0], y: at[1], rotation: 0, scaleX: 1, scaleY: 1 },
    anchor: 'center',
    stretch: 'none',
    components: {},
  };
}

/** A HUD with three hearts in a group, a hidden bonus text, a hidden boss group and a button with focus art. */
const Lives = (() => {
  const hearts = group('gs_lives000001', 'Hearts', [100, 30]);
  const boss = group('gs_lives000002', 'Boss', [400, 30], false);
  const shop = button('Shop', 'Shop', [700, 400]);
  const glow: UiElementData = { ...structuredClone(shop[0]!), id: 'gs_lives000003', name: 'ShopGlow', parent: shop[0]!.id, components: { UiState: { show: 'focused' } } };
  glow.transform = { x: 0, y: 0, rotation: 0, scaleX: 1, scaleY: 1 };
  delete glow.anchor;
  delete glow.stretch;
  return canvas('Lives', { kind: 'hud', open: 'start' }, [
    hearts,
    text('Heart1', '♥', [-40, 0], hearts.id),
    text('Heart2', '♥', [0, 0], hearts.id),
    text('Heart3', '♥', [40, 0], hearts.id),
    text('Bonus', 'Bonus!', [400, 400], null, false),
    boss,
    text('BossName', 'Boss', [0, 0], boss.id),
    shop,
    glow,
  ]);
})();
const Over = canvas('Over', {}, [
  text('Message', 'Game Over', [400, 80]),
  button('Retry', 'Retry', [400, 200], { action: 'restart' }),
  button('Quit', 'Quit', [400, 300]),
]);

let dom: FakeDom;
beforeAll(async () => {
  await new RapierPhysics().init();
});
afterEach(() => dom?.restore());

describe('Blender UI with the canvas layer', () => {
  it('lays out, reports and clicks canvas buttons; bindings show store values', async () => {
    dom = installDom({ ...sceneFiles(), ...uiFiles([Title, HUD]) });
    const g: Game = await heldGame(dom);
    expect(g.ui.canvases).toEqual(['Title', 'HUD']);
    await g.test.begin();
    await g.test.advance(1);
    const state = g.test.state({ ids: [] }).ui;
    expect(state).toMatchObject({ screen: 'Title', focus: 'Play', open: ['HUD', 'Title'] });
    expect(state.buttons.map((b) => [b.canvas, b.name, b.label])).toEqual([
      ['Title', 'Play', 'Play'],
      ['Title', 'Quit', 'Quit'],
    ]);
    // Design size = canvas size (800 × 450): design px are canvas px.
    expect(state.buttons[1]).toMatchObject({ x: 400, y: 300, width: 200, height: 50 });
    expect(state.texts).toMatchObject({ 'Title/TitleText': 'Gem Island' });
    expect(g.state).toBe('paused');

    const quits: string[] = [];
    g.ui.on('quit', (_, info) => void quits.push(info.button));
    const quit = state.buttons[1]!;
    g.test.schedule([{ step: 1, mouse: 'click', x: quit.x, y: quit.y }]);
    await g.test.advance(3);
    expect([quits, g.ui.focused]).toEqual([['Quit'], 'Quit']);
    expect(g.ui.element('Quit')).toMatchObject({ canvas: 'Title', type: 'mesh', visible: true });

    const play = state.buttons[0]!;
    g.test.schedule([{ step: 4, mouse: 'click', x: play.x, y: play.y }]);
    await g.test.advance(3);
    expect([g.ui.current, g.state]).toEqual([null, 'playing']);
    (g as unknown as { store: Record<string, unknown> }).store.score = 7;
    await g.test.advance(1);
    expect(g.test.state({ ids: [] }).ui.texts).toMatchObject({ 'HUD/Score': 'Score 007' });
    g.ui.text('Score', 'Override');
    expect(g.test.state({ ids: [] }).ui.texts['HUD/Score']).toBe('Override');

    // The editor opens a canvas (Show in Game), as MCP does through window.__gs.message.
    handleEditorMessage(g, { t: 'ui.open', canvas: 'Title' }, () => {});
    expect(g.test.state({ ids: [] }).ui.open).toEqual(['HUD', 'Title']);
    handleEditorMessage(g, { t: 'ui.close', canvas: 'Title' }, () => {});
    expect([g.ui.current, g.state]).toEqual([null, 'playing']);
  });

  it('state(), element() and clicks use the canvas size now, before the game sees a resize', async () => {
    dom = installDom({ ...sceneFiles(), ...uiFiles([Title]) });
    const g: Game = await heldGame(dom);
    await g.test.begin();
    await g.test.advance(1);
    const ui = () => g.test.state({ ids: [] }).ui;
    const quit = () => ui().buttons.find((b) => b.name === 'Quit')!;
    const resize = (width: number, height: number): void => void Object.assign(dom.canvas, { clientWidth: width, clientHeight: height });
    // A screenshot at 960 × 540: the game lays out at that size (1.2 × the design size) ...
    resize(960, 540);
    g.ui.layout(960, 540); // as the game does on resize
    expect(ui().size).toEqual([960, 540]);
    expect(quit()).toMatchObject({ x: 480, y: 360, width: 240, height: 60 });
    // ... then the page goes back to 800 × 450, and the state is read before any frame or resize.
    resize(800, 450);
    expect(ui().size).toEqual([800, 450]);
    expect(quit()).toMatchObject({ x: 400, y: 300, width: 200, height: 50 });
    // Twice the size, still without a frame: coordinates scale, element() rects too.
    resize(1600, 900);
    expect(ui().size).toEqual([1600, 900]);
    expect(quit()).toMatchObject({ x: 800, y: 600, width: 400, height: 100 });
    expect(g.ui.element('Quit')!.rect).toEqual({ x: 600, y: 550, width: 400, height: 100 });
    // A click where the state says lands on the button.
    const quits: string[] = [];
    g.ui.on('quit', (_, info) => void quits.push(info.button));
    g.test.schedule([{ step: g.test.status().step, mouse: 'click', x: 800, y: 600 }]);
    await g.test.advance(3);
    expect(quits).toEqual(['Quit']);
  });

  it('binds the built-in game.time (ctx.time) and game.level with no code', async () => {
    const Clock = canvas('Clock', { kind: 'hud', open: 'start' }, [
      text('Timer', 'Time {game.time:m:ss.00}', [100, 30]),
      text('Seconds', '{game.time:0.0}', [100, 60]),
      text('Level', 'Level {game.level}', [100, 90]),
    ]);
    dom = installDom({ ...sceneFiles(), ...uiFiles([Title, Clock]) });
    const g: Game = await heldGame(dom);
    await g.test.begin();
    await g.test.advance(1);
    const texts = () => g.test.state({ ids: [] }).ui.texts;
    // The title holds the game paused from the step it opened on: the clock stands still at 1/60 s.
    expect(texts()).toMatchObject({ 'Clock/Timer': 'Time 0:00.01', 'Clock/Seconds': '0.0', 'Clock/Level': 'Level basic' });
    await g.test.advance(30);
    expect(texts()['Clock/Timer']).toBe('Time 0:00.01');
    g.test.schedule([{ step: g.test.status().step, key: 'Enter' }]); // Play
    await g.test.advance(90);
    expect(texts()).toMatchObject({ 'Clock/Timer': 'Time 0:01.51', 'Clock/Seconds': '1.5' }); // 91 steps of play
    g.pause(true);
    await g.test.advance(60);
    expect(texts()['Clock/Timer']).toBe('Time 0:01.51');
    g.pause(false);
    // A level load starts the clock again at 0, with the new level's name.
    void g.loadScene('level2');
    await g.test.advance(1);
    expect(texts()).toMatchObject({ 'Clock/Timer': 'Time 0:00.01', 'Clock/Level': 'Level level2' });
  });

  it('state().ui.hidden lists what code or Blender hides: not under a hidden parent, not UiState art', async () => {
    dom = installDom({ ...sceneFiles(), ...uiFiles([Lives, Over]) });
    const g: Game = await heldGame(dom);
    await g.test.begin();
    await g.test.advance(1);
    const ui = () => g.test.state({ ids: [] }).ui;
    // Bonus and Boss start hidden (Disable in Renders); BossName is hidden only by Boss; the Shop's
    // focus art is UiState art; Over is not open.
    expect(ui()).toMatchObject({ open: ['Lives'], hidden: ['Lives/Bonus', 'Lives/Boss'] });
    g.ui.hide('Heart3'); // a life lost
    g.ui.show('Bonus');
    expect(ui().hidden).toEqual(['Lives/Boss', 'Lives/Heart3']);
    expect(Object.keys(ui().texts).sort()).toEqual(['Lives/Bonus', 'Lives/Heart1', 'Lives/Heart2', 'Lives/ShopLabel']);
    g.ui.hide('Hearts'); // the group: its hearts are hidden with it, only the group is listed (and Heart3, hidden itself)
    expect(ui().hidden).toEqual(['Lives/Boss', 'Lives/Heart3', 'Lives/Hearts']);
    g.ui.show('Hearts');
    g.ui.show('Heart3', true);
    expect(ui().hidden).toEqual(['Lives/Boss']);
  });

  it('text(), hide() and bar() on a canvas that is not open apply when it opens, until a reset', async () => {
    dom = installDom({ ...sceneFiles(), ...uiFiles([Lives, Over]) });
    const g: Game = await heldGame(dom);
    await g.test.begin();
    await g.test.advance(1);
    const ui = () => g.test.state({ ids: [] }).ui;
    g.ui.text('Over/Message', 'Out of lives');
    g.ui.hide('Retry');
    expect(g.ui.element('Retry')).toMatchObject({ canvas: 'Over', visible: false });
    await g.test.advance(5);
    g.ui.open('Over');
    expect(ui()).toMatchObject({ screen: 'Over', open: ['Lives', 'Over'], hidden: ['Lives/Bonus', 'Lives/Boss', 'Over/Retry'] });
    expect(ui().texts['Over/Message']).toBe('Out of lives');
    expect(ui().buttons.map((b) => b.name)).toEqual(['Quit']);
    // A test reset (as Stop and ctx.restart()) drops the overrides.
    await g.test.begin({ reset: true });
    await g.test.advance(1);
    g.ui.open('Over');
    expect(ui().texts['Over/Message']).toBe('Game Over');
    expect(ui().hidden).not.toContain('Over/Retry');
    expect(ui().buttons.map((b) => b.name)).toEqual(['Retry', 'Quit']);
  });
});

