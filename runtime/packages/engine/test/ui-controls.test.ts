// Sliders, toggles and conditional visibility in a running game with the real canvas layer
// (docs/GAME-UI.md §10.1, §10.3, §10.4): keys and drags write value paths (ctx.store, ctx.save, audio
// channels), toggles flip, UiShow hearts follow ctx.store, and test state reports kinds and values.
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import type { SystemContext } from '../src/context';
import { defineSystem, removeSystem } from '../src/ecs/system';
import type { Game } from '../src/game/game';
import { RapierPhysics } from '../src/physics/rapier-backend';
import '../src/systems';
import type { UiElementData } from '../src/ui/canvas/format';
import type { UiEventInfo } from '../src/ui/ui';
import { installDom, type FakeDom } from './dom';
import { heldGame, sceneFiles } from './game-fixture';
import { button, canvas, text, uiFiles } from './ui-controller-fixtures';

let ids = 0;
const nextId = (): string => `gs_c${(++ids).toString(16).padStart(11, '0')}`;
const at = (x: number, y: number) => ({ x, y, rotation: 0, scaleX: 1, scaleY: 1 });

/** An element: top-level ones are centre-anchored (design px are canvas px in these tests). */
function element(name: string, parent: string | null, xy: [number, number], extra: Partial<UiElementData> = {}): UiElementData {
  return {
    id: nextId(),
    name,
    parent,
    type: 'group',
    visible: true,
    order: 0,
    transform: at(...xy),
    ...(parent === null && { anchor: 'center' as const, stretch: 'none' as const }),
    components: {},
    ...extra,
  };
}

function mesh(name: string, parent: string | null, xy: [number, number], bounds: [number, number, number, number], extra: Partial<UiElementData> = {}): UiElementData {
  const [x0, y0, x1, y1] = bounds;
  return element(name, parent, xy, {
    type: 'mesh',
    mesh: {
      positions: [x0, y0, x1, y0, x1, y1, x0, y1],
      indices: [0, 1, 2, 0, 2, 3],
      uvs: null,
      colors: null,
      bounds,
      material: { color: [1, 1, 1, 1], texture: '', blend: 'alpha' },
    },
    ...extra,
  });
}

/** A slider as the builder makes it: a group with UiSlider and Bg, Fill (its bounds are the range) and Handle (authored at the end). */
function slider(name: string, xy: [number, number], value: string, ui: Record<string, unknown> = {}, components: Record<string, unknown> = {}): UiElementData[] {
  const group = element(name, null, xy);
  const bg = mesh(`${name}Bg`, group.id, [0, 0], [-100, -10, 100, 10]);
  const fill = mesh(`${name}Fill`, group.id, [0, 0], [-100, -10, 100, 10]);
  const handle = mesh(`${name}Handle`, group.id, [100, 0], [-10, -15, 10, 15]);
  group.components = {
    UiSlider: { value, min: 0, max: 1, step: 0.1, fill: fill.id, handle: handle.id, direction: 'right', disabled: false, order: 0, sound: '', ...ui },
    ...components,
  };
  return [group, bg, fill, handle];
}

/** A toggle: a group with UiToggle, a Bg and its On and Off knobs. */
function toggle(name: string, xy: [number, number], value: string, ui: Record<string, unknown> = {}): UiElementData[] {
  const group = element(name, null, xy);
  const bg = mesh(`${name}Bg`, group.id, [0, 0], [-40, -20, 40, 20]);
  const on = mesh(`${name}On`, group.id, [20, 0], [-15, -15, 15, 15]);
  const off = mesh(`${name}Off`, group.id, [-20, 0], [-15, -15, 15, 15]);
  group.components = { UiToggle: { value, on: on.id, off: off.id, disabled: false, order: 0, sound: '', ...ui } };
  return [group, bg, on, off];
}

const show = (value: string, compare: string, than: number) => ({ UiShow: { value, compare, than } });

const Options = canvas('Options', { back: 'previous' }, [
  slider('Music', [400, 100], 'audio.music', { sound: 'assets/sfx/click.ogg' }),
  slider('Sound', [400, 160], 'audio.sfx'),
  slider('Difficulty', [400, 220], 'save.difficulty', { min: 1, max: 3, step: 1 }),
  slider('Volume', [400, 280], 'settings.volume', { step: 0.25 }),
  toggle('Subtitles', [400, 340], 'save.subtitles', { sound: 'assets/sfx/click.ogg' }),
  button('Back', 'Back', [400, 410], { action: 'previous' }),
]);

const HUD = (() => {
  const hearts = element('Hearts', null, [100, 30]);
  return canvas('HUD', { kind: 'hud', open: 'start', backdrop: [0, 0, 0, 0] }, [
    hearts,
    { ...text('Heart1', '♥', [-40, 0], hearts.id), components: show('lives', 'atLeast', 1) },
    { ...text('Heart2', '♥', [0, 0], hearts.id), components: show('lives', 'atLeast', 2) },
    { ...text('Heart3', '♥', [40, 0], hearts.id), components: show('lives', 'atLeast', 3) },
    { ...text('LowHealth', 'Low!', [400, 30]), components: show('health', 'below', 30) },
    { ...text('Shield', 'Shield', [600, 30]), components: show('save.shield', 'equals', 1) },
    slider('Zoom', [400, 420], 'zoom'),
  ]);
})();

const Odd = canvas('Odd', {}, [
  slider('Both', [400, 60], 'both', {}, { UiButton: { action: 'close', target: '', disabled: false, order: 0, focusScale: 1.06, sound: '', focusSound: '' } }),
  slider('NoPath', [400, 140], ''),
  slider('Through', [400, 220], 'flag.on'),
  slider('Off', [400, 300], 'off', { disabled: true }),
  button('Done', 'Done', [400, 400], { action: 'close' }),
]);

let dom: FakeDom;
beforeAll(async () => {
  await new RapierPhysics().init();
});
afterEach(() => {
  removeSystem('UiProbe');
  vi.restoreAllMocks();
  dom?.restore();
});

interface Probe {
  presses: number;
  arrows: number;
}

/** Counts what gameplay sees: left mouse presses and arrow keys. */
function probe(): Probe {
  const p: Probe = { presses: 0, arrows: 0 };
  defineSystem({
    name: 'UiProbe',
    update(ctx) {
      if (ctx.input.mouseButtonDown(0)) p.presses++;
      if (ctx.input.key('ArrowLeft') || ctx.input.key('ArrowRight')) p.arrows++;
    },
  });
  return p;
}

async function game(): Promise<Game> {
  dom = installDom({ ...sceneFiles(), ...uiFiles([Options, HUD, Odd]) });
  const g = await heldGame(dom);
  await g.test.begin();
  await g.test.advance(1);
  return g;
}

const ui = (g: Game) => g.test.state({ ids: [] }).ui;
const ctxOf = (g: Game): SystemContext => (g as unknown as { ctx: SystemContext }).ctx;
const listed = (g: Game) => ui(g).buttons.map((b) => [b.name, b.kind, b.value]);

async function tap(g: Game, key: string, steps = 2): Promise<void> {
  g.test.schedule([{ step: g.test.status().step, key }]);
  await g.test.advance(steps);
}

async function mouse(g: Game, kind: 'down' | 'up' | 'move' | 'click', x: number, y: number, steps = 2): Promise<void> {
  g.test.schedule([{ step: g.test.status().step, mouse: kind, x, y }]);
  await g.test.advance(steps);
}

describe('sliders and toggles', () => {
  it('a column of sliders: Up/Down move the focus, Left/Right change the focused value by its step and write its path', async () => {
    const p = probe();
    const g = await game();
    const ctx = ctxOf(g);
    const events: UiEventInfo[] = [];
    for (const name of ['Music', 'Sound', 'Difficulty', 'Volume', 'Subtitles', 'Back']) g.ui.on(name, (_, info) => void events.push(info));
    const play = vi.spyOn(g.audio, 'play');
    g.ui.open('Options');
    expect([g.ui.current, g.ui.focused]).toEqual(['Options', 'Music']);
    // Test state: every control in navigation order, with its kind and value (save.difficulty is missing: its min).
    expect(listed(g)).toEqual([
      ['Music', 'slider', 1],
      ['Sound', 'slider', 1],
      ['Difficulty', 'slider', 1],
      ['Volume', 'slider', 0],
      ['Subtitles', 'toggle', false],
      ['Back', 'button', undefined],
    ]);

    await tap(g, 'ArrowLeft');
    expect(g.audio.channel('music').volume).toBeCloseTo(0.9, 9);
    expect(g.ui.focused).toBe('Music');
    expect(events).toEqual([{ canvas: 'Options', button: 'Music', kind: 'slider', value: 0.9 }]);
    expect(play).toHaveBeenCalledWith(expect.stringContaining('assets/sfx/click.ogg'), { channel: 'ui' });
    await tap(g, 'ArrowDown');
    await tap(g, 'ArrowRight'); // Sound is at its max: it stays, and the key does not move the focus
    expect([g.ui.focused, g.audio.channel('sfx').volume, events.length]).toEqual(['Sound', 1, 1]);
    await tap(g, 'ArrowLeft');
    await tap(g, 'ArrowLeft');
    expect(g.audio.channel('sfx').volume).toBeCloseTo(0.8, 9);
    await tap(g, 'ArrowDown');
    for (const key of ['ArrowRight', 'ArrowRight', 'ArrowRight']) await tap(g, key);
    expect([g.ui.focused, ctx.save.get('difficulty')]).toEqual(['Difficulty', 3]);
    await tap(g, 'ArrowDown');
    await tap(g, 'ArrowRight');
    expect(ctx.store.settings).toEqual({ volume: 0.25 }); // created on the way
    expect(listed(g).map(([name, , value]) => [name, value])).toEqual([
      ['Music', 0.9],
      ['Sound', 0.8],
      ['Difficulty', 3],
      ['Volume', 0.25],
      ['Subtitles', false],
      ['Back', undefined],
    ]);
    expect(events.map((e) => `${e.button}=${String(e.value)}`)).toEqual(['Music=0.9', 'Sound=0.9', 'Sound=0.8', 'Difficulty=2', 'Difficulty=3', 'Volume=0.25']);

    // The toggle flips with Enter (and Space), showing its On or Off knob, never listed as hidden.
    await tap(g, 'ArrowDown');
    expect(g.ui.focused).toBe('Subtitles');
    expect([g.ui.element('SubtitlesOn')?.visible, g.ui.element('SubtitlesOff')?.visible]).toEqual([false, true]);
    await tap(g, 'Enter');
    expect([ctx.save.get('subtitles'), g.ui.element('SubtitlesOn')?.visible, g.ui.element('SubtitlesOff')?.visible]).toEqual([true, true, false]);
    expect(events.at(-1)).toEqual({ canvas: 'Options', button: 'Subtitles', kind: 'toggle', value: true });
    expect(ui(g).hidden).toEqual([]);
    await tap(g, 'Space');
    expect(ctx.save.get('subtitles')).toBe(false);
    // A value set by code shows at once.
    ctx.save.set('subtitles', true);
    expect(listed(g)[4]).toEqual(['Subtitles', 'toggle', true]);

    await tap(g, 'ArrowDown');
    await tap(g, 'Enter'); // Back: `previous` with no history closes
    expect([g.ui.current, g.state]).toEqual([null, 'playing']);
    expect(events.at(-1)).toEqual({ canvas: 'Options', button: 'Back', kind: 'button' });
    expect(p.arrows).toBe(0); // the menu kept its keys from gameplay
  });

  it('the accessibility mirror names sliders and toggles with their values', async () => {
    const g = await game();
    g.ui.open('Options');
    await g.test.advance(2);
    type Node = { children: Node[]; role?: string; dataset: Record<string, string>; [aria: string]: unknown };
    const all: Node[] = [];
    const walk = (n: Node): void => {
      all.push(n);
      n.children.forEach(walk);
    };
    walk((globalThis as unknown as { document: { body: Node } }).document.body);
    const byLabel = (label: string) => all.find((n) => n.dataset.label === label)!;
    expect(byLabel('Music').role).toBe('slider');
    expect(byLabel('Music')['aria-valuenow']).toBe('1');
    expect(byLabel('Subtitles').role).toBe('switch');
    expect(byLabel('Subtitles')['aria-checked']).toBe('false');
    expect(byLabel('Back').role).toBe('button');
  });

  it('a press on a slider takes the value under the pointer and a drag follows it, snapped, until the release', async () => {
    const p = probe();
    const g = await game();
    const values: number[] = [];
    g.ui.on('Music', (_, info) => void values.push(info.value as number));
    g.ui.open('Options');
    // Music's range is its fill: x 300..500 at y 100 (state rect: the bar and the handle at the end).
    expect(ui(g).buttons[0]).toMatchObject({ name: 'Music', x: 405, y: 100, width: 210, height: 30 });
    await mouse(g, 'down', 360, 100);
    expect([g.audio.channel('music').volume, g.ui.focused]).toEqual([0.3, 'Music']);
    await mouse(g, 'move', 444, 130); // off the bar: only the position along it counts
    expect(g.audio.channel('music').volume).toBe(0.7);
    await mouse(g, 'move', 700, 100);
    expect(g.audio.channel('music').volume).toBe(1);
    await mouse(g, 'move', 320, 100);
    expect(g.audio.channel('music').volume).toBe(0.1);
    // The fill is scaled from its starting edge and the handle sits at the value.
    expect(g.ui.element('MusicFill')?.rect).toEqual({ x: 300, y: 90, width: 20, height: 20 });
    expect(g.ui.element('MusicHandle')?.rect).toEqual({ x: 310, y: 85, width: 20, height: 30 });
    await mouse(g, 'up', 320, 100);
    await mouse(g, 'move', 480, 100); // released: hovering changes nothing
    expect(g.audio.channel('music').volume).toBe(0.1);
    expect(values).toEqual([0.3, 0.7, 1, 0.1]);
    // A click on a store slider writes the store (step 0.25).
    await mouse(g, 'click', 400, 280);
    expect([ctxOf(g).store.settings, g.ui.focused]).toEqual([{ volume: 0.5 }, 'Volume']);
    expect(p.presses).toBe(0);
  });

  it('a toggle flips on a click; HUD sliders drag and keep their presses from gameplay', async () => {
    const p = probe();
    const g = await game();
    const ctx = ctxOf(g);
    g.ui.open('Options');
    await mouse(g, 'click', 400, 340);
    expect(ctx.save.get('subtitles')).toBe(true);
    await mouse(g, 'click', 400, 340);
    expect(ctx.save.get('subtitles')).toBe(false);
    g.ui.close();
    await g.test.advance(2);
    expect(ui(g).buttons.map((b) => [b.canvas, b.name, b.kind, b.value])).toEqual([['HUD', 'Zoom', 'slider', 0]]);
    const presses = p.presses;
    await mouse(g, 'click', 460, 420);
    expect([ctx.store.zoom, p.presses]).toEqual([0.8, presses]);
    await mouse(g, 'click', 400, 250); // not on a control: gameplay's
    expect(p.presses).toBe(presses + 1);
  });

  it('a UiButton beside a slider is ignored; paths that cannot be written warn once; disabled sliders are skipped', async () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
    const g = await game();
    const ctx = ctxOf(g);
    ctx.store.flag = 5;
    const events: string[] = [];
    for (const name of ['Both', 'NoPath', 'Through']) g.ui.on(name, (_, info) => void events.push(`${info.button}=${String(info.value)}`));
    g.ui.open('Odd');
    const warnings = () => warn.mock.calls.map(([message]) => String(message));
    expect(warnings().filter((m) => m.includes('"Odd/Both" is a slider: its UiButton is ignored'))).toHaveLength(1);
    expect(listed(g)).toEqual([
      ['Both', 'slider', 0],
      ['NoPath', 'slider', 0],
      ['Through', 'slider', 0],
      ['Off', 'slider', 0],
      ['Done', 'button', undefined],
    ]);
    await tap(g, 'Enter'); // the ignored UiButton's `close` does nothing
    await tap(g, 'ArrowRight');
    expect([g.ui.current, ctx.store.both]).toEqual(['Odd', 0.1]);
    await tap(g, 'ArrowDown');
    await tap(g, 'ArrowRight');
    await tap(g, 'ArrowRight'); // it can't hold a value: the event says what it was set to, it shows 0
    await tap(g, 'ArrowDown');
    await tap(g, 'ArrowRight');
    expect(events).toEqual(['Both=0.1', 'NoPath=0.1', 'NoPath=0.1', 'Through=0.1']);
    expect(ctx.store.flag).toBe(5);
    expect(warnings().filter((m) => m.includes('"Odd/NoPath" has no value path'))).toHaveLength(1);
    expect(warnings().filter((m) => m.includes('"Odd/Through" can\'t write "flag.on"'))).toHaveLength(1);
    // The disabled slider is skipped (Down goes on to Done) and ignores the mouse.
    await tap(g, 'ArrowDown');
    expect(g.ui.focused).toBe('Done');
    await mouse(g, 'click', 450, 300);
    expect([ctx.store.off, g.ui.focused]).toEqual([undefined, 'Done']);
  });

  it('shows the read-only game.time and game.level; a slider or toggle on them warns once and changes nothing', async () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
    const Clock = canvas('Clock', {}, [slider('Elapsed', [400, 100], 'game.time', { max: 100, step: 1 }), toggle('Where', [400, 200], 'game.level')]);
    dom = installDom({ ...sceneFiles(), ...uiFiles([Clock]) });
    const g = await heldGame(dom);
    await g.test.begin();
    await g.test.advance(120); // 2 s
    const ctx = ctxOf(g);
    const time = ctx.time;
    const changes: string[] = [];
    for (const name of ['Elapsed', 'Where']) g.ui.on(name, (_, info) => void changes.push(`${info.button}=${String(info.value)}`));
    g.ui.open('Clock');
    const shown = () => ui(g).buttons.map((b) => [b.name, b.kind, typeof b.value === 'number' ? Math.round(b.value * 1e6) / 1e6 : b.value]);
    expect(shown()).toEqual([
      ['Elapsed', 'slider', 2],
      ['Where', 'toggle', true], // 'basic' is truthy
    ]);
    await tap(g, 'ArrowRight');
    await tap(g, 'ArrowRight');
    await tap(g, 'ArrowDown');
    await tap(g, 'Enter');
    await tap(g, 'Enter');
    expect([ctx.time, g.scene.name, ctx.store.game]).toEqual([time, 'basic', undefined]); // the menu pauses: time stands still
    expect(shown()).toEqual([
      ['Elapsed', 'slider', 2],
      ['Where', 'toggle', true],
    ]);
    expect(changes).toEqual(['Elapsed=3', 'Elapsed=3', 'Where=false', 'Where=false']);
    const warnings = warn.mock.calls.map(([message]) => String(message));
    expect(warnings.filter((m) => m.includes('"Clock/Elapsed" can\'t write "game.time": game.time, game.level and ui.localeName are read-only'))).toHaveLength(1);
    expect(warnings.filter((m) => m.includes('"Clock/Where" can\'t write "game.level"'))).toHaveLength(1);
  });
});

describe('UiShow', () => {
  it('shows hearts while lives is at least their number; hidden only by UiShow is not listed as hidden', async () => {
    const g = await game();
    const ctx = ctxOf(g);
    const shown = () => Object.keys(ui(g).texts).sort();
    expect(shown()).toEqual(['HUD/LowHealth']); // lives and health are missing: 0, so no hearts, and low
    ctx.store.health = 100;
    ctx.store.lives = 3;
    expect(shown()).toEqual(['HUD/Heart1', 'HUD/Heart2', 'HUD/Heart3']);
    ctx.store.lives = 1;
    ctx.store.health = 20;
    await g.test.advance(1);
    expect(shown()).toEqual(['HUD/Heart1', 'HUD/LowHealth']);
    expect(g.ui.element('Heart2')?.visible).toBe(false);
    expect(ui(g).hidden).toEqual([]);
    // Its own visibility counts too: hide() hides it whatever the value, and lists it.
    g.ui.hide('Heart1');
    expect([shown(), ui(g).hidden]).toEqual([['HUD/LowHealth'], ['HUD/Heart1']]);
    g.ui.show('Heart1');
    ctx.save.set('shield', true); // booleans read as 1
    expect(shown()).toEqual(['HUD/Heart1', 'HUD/LowHealth', 'HUD/Shield']);
  });
});
