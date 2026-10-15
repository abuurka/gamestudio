import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { Input } from '../src/input/input';
import { installDom, type FakeDom } from './dom';

let dom: FakeDom;
let input: Input;

beforeEach(() => {
  dom = installDom();
  input = new Input(dom.canvas as unknown as HTMLElement);
});
afterEach(() => {
  input.dispose();
  dom.restore();
});

const key = (type: 'keydown' | 'keyup', code: string) =>
  dom.canvas.dispatchEvent(new KeyboardEvent(type, { code, bubbles: true }));
const pointer = (type: 'pointerdown' | 'pointerup', button: number) =>
  dom.canvas.dispatchEvent(new PointerEvent(type, { button, bubbles: true }));

describe('Input latches short presses', () => {
  it('a tap between two frames reads as held for one frame', () => {
    key('keydown', 'Space');
    key('keyup', 'Space');
    input.beginFrame(false);
    expect(input.key('Space')).toBe(true);
    expect(input.keyDown('Space')).toBe(true);
    expect(input.keyUp('Space')).toBe(true);
    input.endFrame();
    input.beginFrame(false);
    expect(input.key('Space')).toBe(false);
    expect(input.keyDown('Space')).toBe(false);
  });

  it('fixed steps see each press exactly once, even with several steps (or none) in a frame', () => {
    key('keydown', 'Space');
    key('keyup', 'Space');
    // A frame with no fixed step: the press waits for the next one.
    input.beginFrame(false);
    input.endFrame();
    input.beginFrame(false);
    const seen: boolean[] = [];
    for (let step = 0; step < 3; step++) {
      input.beginFixedStep();
      seen.push(input.key('Space') && input.keyDown('Space'));
      input.endFixedStep();
    }
    expect(seen).toEqual([true, false, false]);
    expect(input.key('Space')).toBe(false); // update/late: released before this frame
    expect(input.keyDown('Space')).toBe(false); // that frame already went by
  });

  it('held keys stay held; axes include taps', () => {
    key('keydown', 'KeyW');
    key('keydown', 'KeyD');
    key('keyup', 'KeyD');
    input.beginFixedStep();
    expect(input.axis('vertical')).toBe(1);
    expect(input.axis('horizontal')).toBe(1);
    input.endFixedStep();
    input.beginFixedStep();
    expect(input.axis('vertical')).toBe(1);
    expect(input.axis('horizontal')).toBe(0);
    input.endFixedStep();
  });

  it('latches mouse buttons the same way and drops edges nobody consumed', () => {
    pointer('pointerdown', 0);
    pointer('pointerup', 0);
    expect(input.mouseButton(0)).toBe(true);
    expect(input.mouseButtonDown(0)).toBe(true);
    input.endFrame();
    expect(input.mouseButton(0)).toBe(false);
    input.discardFixedEdges(); // the game was paused: no fixed step will see this click
    input.beginFixedStep();
    expect(input.mouseButtonDown(0)).toBe(false);
    input.endFixedStep();
  });
});
