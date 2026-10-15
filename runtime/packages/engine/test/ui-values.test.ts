// Value paths (docs/GAME-UI.md §10.1): what bindings, bars, UiShow, sliders and toggles read and write;
// and the slider arithmetic of §10.4 (keys, pointer, step). Pure functions.
import { describe, expect, it } from 'vitest';
import type { CanvasButton, CanvasSlider } from '../src/ui/canvas/types';
import { sliderAt, sliderKey, snapSlider } from '../src/ui/canvases';
import { compareValue, isReadOnly, numberValue, readValue, truthy, writeValue, type ValueSources } from '../src/ui/values';

function sources(): ValueSources & { saved: Record<string, string>; channels: Record<string, { volume: number }> } {
  const saved: Record<string, string> = { best: '42', prefs: JSON.stringify({ volume: 0.5 }) };
  const channels: Record<string, { volume: number }> = { music: { volume: 1 }, sfx: { volume: 0.8 }, ui: { volume: 1 } };
  const audio = { volume: 0.9, channel: (name: string) => channels[name]! };
  return {
    store: { score: 7, player: { name: 'Ada' }, store: 'literal', lives: 3, flag: 5 },
    // As ctx.save: JSON in and out, so what is read back is a copy.
    save: { get: (key) => (key in saved ? JSON.parse(saved[key]!) : undefined), set: (key, value) => void (saved[key] = JSON.stringify(value)) },
    audio,
    saved,
    channels,
  };
}

describe('value paths', () => {
  it('read ctx.store (plain or store.), ctx.save (save.) and the audio channels (audio.)', () => {
    const s = sources();
    expect(readValue('score', s)).toBe(7);
    expect(readValue('player.name', s)).toBe('Ada');
    expect(readValue('store.score', s)).toBe(7);
    expect(readValue('store', s)).toBe('literal'); // `store` alone is the store key
    expect(readValue('save.best', s)).toBe(42);
    expect(readValue('save.prefs.volume', s)).toBe(0.5);
    expect(readValue('audio.master', s)).toBe(0.9);
    expect(readValue('audio.sfx', s)).toBe(0.8);
    expect(readValue('missing.deep', s)).toBeUndefined();
    expect(readValue('', s)).toBeUndefined();
    expect(readValue('a..b', s)).toBeUndefined();
    expect(readValue('audio.music', { ...s, audio: null })).toBeUndefined();
    expect(readValue('score', null)).toBeUndefined();
  });

  it('write the store (creating objects on the way), the save store and audio channels', () => {
    const s = sources();
    expect(writeValue('settings.volume', 0.4, s)).toBe(true);
    expect(s.store.settings).toEqual({ volume: 0.4 });
    expect(writeValue('store.score', 9, s)).toBe(true);
    expect(s.store.score).toBe(9);
    expect(writeValue('save.difficulty', 2, s)).toBe(true);
    expect(s.saved.difficulty).toBe('2');
    expect(writeValue('save.prefs.volume', 0.25, s)).toBe(true); // rewrites `prefs`
    expect(JSON.parse(s.saved.prefs!)).toEqual({ volume: 0.25 });
    expect(writeValue('save.options.subtitles', true, s)).toBe(true);
    expect(JSON.parse(s.saved.options!)).toEqual({ subtitles: true });
    expect(writeValue('audio.music', 0.3, s)).toBe(true);
    expect(s.channels.music!.volume).toBe(0.3);
    expect(writeValue('audio.master', 0.5, s)).toBe(true);
    expect(s.audio!.volume).toBe(0.5);
    expect(writeValue('audio.sfx', false, s)).toBe(true); // a toggle on a channel: off is 0
    expect(s.channels.sfx!.volume).toBe(0);
    expect(writeValue('audio.ui', -1, s)).toBe(true);
    expect(s.channels.ui!.volume).toBe(0);
  });

  it('refuse paths that cannot be written', () => {
    const s = sources();
    expect(writeValue('', 1, s)).toBe(false);
    expect(writeValue('a..b', 1, s)).toBe(false);
    expect(writeValue('flag.on', true, s)).toBe(false); // flag is a number, not an object
    expect(s.store.flag).toBe(5);
    expect(writeValue('__proto__.polluted', 1, s)).toBe(false);
    expect(writeValue('save.__proto__', 1, s)).toBe(false);
    expect(({} as Record<string, unknown>).polluted).toBeUndefined();
    expect(writeValue('save.best.deep', 1, s)).toBe(false); // best is a number
    expect(writeValue('audio.music', 'loud', s)).toBe(false);
    expect(writeValue('audio.music', 0.5, { ...s, audio: null })).toBe(false);
    expect(writeValue('score', 1, null)).toBe(false);
  });

  it('read the built-in game.time and game.level, which no write changes', () => {
    const clock = { time: 75.5, level: 'main' };
    const s = { ...sources(), game: { get time() { return clock.time; }, get level() { return clock.level; } } };
    s.store.game = { time: 'store', mode: 'arcade' };
    expect([readValue('game.time', s), readValue('game.level', s)]).toEqual([75.5, 'main']);
    clock.time = 76;
    expect(readValue('game.time', s)).toBe(76); // live, read on every evaluation
    expect(readValue('game.time.x', s)).toBeUndefined(); // nothing inside a number
    // Only time and level are built in: other game.x paths, and store.game.time, are ctx.store's.
    expect([readValue('game.mode', s), readValue('store.game.time', s), readValue('game', s)]).toEqual(['arcade', 'store', { time: 'store', mode: 'arcade' }]);
    expect(readValue('game.time', sources())).toBeUndefined(); // no clock
    expect([isReadOnly('game.time'), isReadOnly('game.level'), isReadOnly(' game . time '), isReadOnly('game.mode'), isReadOnly('store.game.time'), isReadOnly('time')]).toEqual([
      true, true, true, false, false, false,
    ]);
    expect(writeValue('game.time', 0, s)).toBe(false);
    expect(writeValue('game.level', 'other', s)).toBe(false);
    expect([clock.time, clock.level, s.store.game]).toEqual([76, 'main', { time: 'store', mode: 'arcade' }]);
    expect(writeValue('game.mode', 'story', s)).toBe(true);
    expect((s.store.game as Record<string, unknown>).mode).toBe('story');
  });

  it('read as numbers (booleans 1/0, missing 0) and as toggles (truthiness)', () => {
    expect([numberValue(3), numberValue(true), numberValue(false), numberValue('2.5'), numberValue(undefined), numberValue('x'), numberValue(NaN)]).toEqual([
      3, 1, 0, 2.5, 0, 0, 0,
    ]);
    expect([truthy(1), truthy(0), truthy('yes'), truthy(''), truthy(undefined), truthy(true)]).toEqual([true, false, true, false, false, true]);
  });

  it('compare for UiShow: atLeast, below, equals, notEquals, with float noise', () => {
    expect([compareValue(2, 'atLeast', 2), compareValue(1, 'atLeast', 2), compareValue(undefined, 'atLeast', 1)]).toEqual([true, false, false]);
    expect([compareValue(1, 'below', 2), compareValue(2, 'below', 2), compareValue(0.1 + 0.2, 'below', 0.3)]).toEqual([true, false, false]);
    expect([compareValue(0.1 + 0.2, 'equals', 0.3), compareValue(true, 'equals', 1), compareValue(2, 'notEquals', 2)]).toEqual([true, true, false]);
    expect(compareValue(5, 'unknown', 3)).toBe(true); // atLeast
  });
});

describe('slider arithmetic', () => {
  const range = (extra: Partial<CanvasSlider> = {}): CanvasSlider => ({
    min: 0,
    max: 1,
    step: 0.1,
    direction: 'right',
    from: { x: 100, y: 50 },
    to: { x: 300, y: 50 },
    ...extra,
  });
  const slider = (value: number, extra: Partial<CanvasSlider> = {}): CanvasButton => ({
    canvas: 'C',
    id: 'S',
    name: 'S',
    label: 'S',
    disabled: false,
    order: 0,
    action: 'none',
    target: '',
    event: 'S',
    focusScale: 1.06,
    sound: '',
    focusSound: '',
    rect: { x: 100, y: 40, width: 200, height: 20 },
    kind: 'slider',
    path: 'v',
    value,
    slider: range(extra),
  });

  it('snaps to the step from min, clamps, and rounds float noise away', () => {
    expect(snapSlider(range(), 0.34)).toBe(0.3);
    expect(snapSlider(range(), 0.1 * 3)).toBe(0.3);
    expect(snapSlider(range(), 1.4)).toBe(1);
    expect(snapSlider(range({ min: 1, max: 3, step: 1 }), 2.6)).toBe(3);
    expect(snapSlider(range({ step: 0 }), 0.123)).toBe(0.123);
  });

  it('moves by step with the keys along its axis only; the key toward its direction goes up', () => {
    expect(sliderKey(slider(0.5), 'right')).toBe(0.6);
    expect(sliderKey(slider(0.5), 'left')).toBe(0.4);
    expect(sliderKey(slider(1), 'right')).toBe(1); // stays at the end (the key is still the slider's)
    expect(sliderKey(slider(0.5), 'up')).toBeNull(); // moves the focus
    expect(sliderKey(slider(0.5), 'next')).toBeNull();
    expect(sliderKey(slider(0.5, { direction: 'left' }), 'left')).toBe(0.6);
    expect(sliderKey(slider(0.5, { direction: 'up' }), 'up')).toBe(0.6);
    expect(sliderKey(slider(0.5, { direction: 'up' }), 'right')).toBeNull();
    expect(sliderKey(slider(0.5, { step: 0 }), 'right')).toBe(0.6); // smooth: a tenth of the range
    expect(sliderKey(slider(1, { min: 1, max: 0 }), 'right')).toBe(0.9); // a reversed range
  });

  it('takes the value under the pointer: projected on the range, clamped, snapped', () => {
    expect(sliderAt(slider(0), 160, 50)).toBe(0.3);
    expect(sliderAt(slider(0), 244, 90)).toBe(0.7); // off the bar: only the position along it counts
    expect(sliderAt(slider(0), 20, 50)).toBe(0);
    expect(sliderAt(slider(0), 900, 50)).toBe(1);
    expect(sliderAt(slider(0, { step: 0 }), 150, 50)).toBe(0.25);
    expect(sliderAt(slider(0, { from: { x: 0, y: 200 }, to: { x: 0, y: 0 }, direction: 'up' }), 0, 40)).toBe(0.8);
    expect(sliderAt(slider(0.4, { to: { x: 100, y: 50 } }), 160, 50)).toBe(0.4); // no range: keeps its value
  });
});
