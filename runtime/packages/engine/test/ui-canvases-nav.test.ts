// Keyboard and gamepad navigation in Blender-authored canvases (docs/GAME-UI.md §5.6): by position
// (Left/Right by the row's beam, Up/Down by the nearest row), by UiButton.order, Tab in reading order.
// Value paths and sliders: ui-values.test.ts.
import { describe, expect, it } from 'vitest';
import type { CanvasButton } from '../src/ui/canvas/types';
import { defaultFocus, navigate, readingOrder, spatialFocus } from '../src/ui/canvases';

/** A button centred on (x, y), 100 × 40 (or `size`). */
function b(name: string, x: number, y: number, extra: Partial<CanvasButton> = {}, size: [number, number] = [100, 40]): CanvasButton {
  const [w, h] = size;
  return {
    canvas: 'C',
    id: name,
    name,
    label: name,
    disabled: false,
    order: 0,
    action: 'event',
    target: '',
    event: name,
    focusScale: 1.06,
    sound: '',
    focusSound: '',
    rect: { x: x - w / 2, y: y - h / 2, width: w, height: h },
    ...extra,
  };
}

const walk = (buttons: CanvasButton[], from: string, steps: Parameters<typeof navigate>[2][]): string[] => {
  const out: string[] = [];
  let at: string | null = from;
  for (const step of steps) {
    at = navigate(buttons, at, step)?.id ?? null;
    out.push(at ?? '-');
  }
  return out;
};

describe('canvas navigation', () => {
  // A 2 × 2 grid: A B / C D.
  const grid = [b('A', 200, 150), b('B', 600, 150), b('C', 200, 300), b('D', 600, 300)];

  it('moves by position across a 2 × 2 grid and wraps to the far side of the same row or column', () => {
    expect(walk(grid, 'A', ['right', 'down', 'left', 'up'])).toEqual(['B', 'D', 'C', 'A']);
    expect(walk(grid, 'A', ['up', 'left'])).toEqual(['C', 'D']); // up from A wraps to C; left from C wraps to D
    expect(walk(grid, 'B', ['right'])).toEqual(['A']);
    expect(walk(grid, 'D', ['down'])).toEqual(['B']);
  });

  it('prefers the button straight ahead: off-axis distance counts double, only within 60°', () => {
    // From M: Near is closer but 50° off; Ahead is farther and straight right.
    const buttons = [b('M', 100, 200), b('Near', 230, 45), b('Ahead', 400, 200)];
    expect(spatialFocus(buttons, buttons[0]!, 'right')?.id).toBe('Ahead');
    // Far off to the side (more than 60°) is not "right" of M: it wraps to nothing the other way.
    const side = [b('M', 100, 200), b('Up', 150, 20)];
    expect(spatialFocus(side, side[0]!, 'right')).toBeNull();
    // A single row: down has nowhere to go.
    expect(navigate([b('L', 200, 200), b('R', 600, 200)], 'L', 'down')).toBeNull();
  });

  it('skips disabled buttons', () => {
    const buttons = [b('A', 200, 150), b('B', 600, 150, { disabled: true }), b('C', 200, 300), b('D', 600, 300)];
    // Right from A with B disabled: D, within 60°; Tab goes A, C, D.
    expect(walk(buttons, 'A', ['right', 'next', 'next', 'next'])).toEqual(['D', 'A', 'C', 'D']);
    expect(defaultFocus([b('X', 0, 0, { disabled: true }), b('Y', 0, 100)], null)?.id).toBe('Y');
  });

  it('Tab and Shift+Tab go in reading order: rows top to bottom, then left to right', () => {
    const shuffled = [grid[3]!, grid[0]!, grid[2]!, grid[1]!];
    expect(readingOrder(shuffled).map((x) => x.id)).toEqual(['A', 'B', 'C', 'D']);
    // A row tolerates a few pixels of misalignment.
    expect(readingOrder([b('R', 600, 152), b('L', 200, 148)]).map((x) => x.id)).toEqual(['L', 'R']);
    expect(walk(shuffled, 'A', ['next', 'next', 'next', 'next'])).toEqual(['B', 'C', 'D', 'A']);
    expect(walk(shuffled, 'A', ['prev', 'prev'])).toEqual(['D', 'C']);
  });

  it('follows UiButton.order when any button has one: Up/Left back, Down/Right/Tab on, wrapping', () => {
    const ordered = [b('Third', 400, 100, { order: 3 }), b('First', 400, 200, { order: 1 }), b('Second', 400, 300, { order: 2 }), b('Rest', 100, 0)];
    expect(defaultFocus(ordered, null)?.id).toBe('First');
    expect(walk(ordered, 'First', ['down', 'right', 'next', 'down'])).toEqual(['Second', 'Third', 'Rest', 'First']);
    expect(walk(ordered, 'First', ['up', 'left', 'prev'])).toEqual(['Rest', 'Third', 'Second']);
  });

  describe('Left/Right: the same row first (in the beam), as browsers do; Up/Down: the nearest row', () => {
    // An Options row "−  [bar]  +" (the bar is not a button) with Back centred below it.
    const row = (backX = 640, backWidth = 200): CanvasButton[] => [
      b('Minus', 480, 300, {}, [60, 60]),
      b('Plus', 800, 300, {}, [60, 60]),
      b('Back', backX, 420, {}, [backWidth, 64]),
    ];

    it('Right from − is + in its row, not Back below (which the cone score alone prefers)', () => {
      const buttons = row();
      expect(spatialFocus(buttons, buttons[0]!, 'right')?.id).toBe('Plus');
      expect(spatialFocus(buttons, buttons[1]!, 'left')?.id).toBe('Minus');
      // Down from either is Back; Up from Back is the nearer of − and + across.
      expect(walk(buttons, 'Minus', ['down'])).toEqual(['Back']);
      expect(walk(buttons, 'Plus', ['down'])).toEqual(['Back']);
      expect(walk(row(660), 'Back', ['up'])).toEqual(['Plus']);
      expect(walk(row(620), 'Back', ['up'])).toEqual(['Minus']);
      // A Back wide enough to share a column with both: still the nearer one across.
      expect(walk(row(660, 420), 'Back', ['up'])).toEqual(['Plus']);
      expect(walk(row(620, 420), 'Back', ['up'])).toEqual(['Minus']);
    });

    it('wraps within the row or column first', () => {
      // Right from + wraps to −, not to Back; Left from − wraps to +.
      expect(walk(row(), 'Plus', ['right', 'left'])).toEqual(['Minus', 'Plus']);
      // Two rows whose buttons don't line up: Right from the top + wraps to the top −, though the lower
      // − is farther left; Up from the top − wraps to the bottom of its column.
      const rows = [b('Minus1', 480, 200, {}, [60, 60]), b('Plus1', 800, 200, {}, [60, 60]), b('Minus2', 440, 300, {}, [60, 60]), b('Plus2', 840, 300, {}, [60, 60])];
      expect(walk(rows, 'Plus1', ['right'])).toEqual(['Minus1']);
      expect(walk(rows, 'Minus2', ['left'])).toEqual(['Plus2']);
      expect(walk(rows, 'Minus1', ['up'])).toEqual(['Minus2']);
      // With nothing in the row or column the other way, the farthest along the axis, as before.
      const lone = [b('Top', 400, 100), b('Left', 100, 300), b('Right', 700, 300)];
      expect(walk(lone, 'Top', ['up'])).toEqual(['Left']);
    });

    it('a 2 × 2 grid with columns of different widths moves within rows and columns', () => {
      // Wide A | narrow B over narrow C | wide D: Right from A, the cone score alone prefers D (diagonal) to B.
      const grid = [b('A', 200, 100, {}, [300, 40]), b('B', 520, 100, {}, [80, 40]), b('C', 120, 200, {}, [80, 40]), b('D', 440, 200, {}, [300, 40])];
      expect(walk(grid, 'A', ['right', 'down', 'left', 'up'])).toEqual(['B', 'D', 'C', 'A']);
      expect(walk(grid, 'B', ['down', 'up'])).toEqual(['D', 'B']);
      expect(walk(grid, 'C', ['right', 'up'])).toEqual(['D', 'B']);
    });

    it('rows of "− bar +" over a centred Back: Right stays in the row, Up and Down go row by row', () => {
      // Music and Sound rows (the bars are not buttons), Back centred under them, a little right of centre.
      const rows = [
        b('Minus1', 480, 200, {}, [60, 60]),
        b('Plus1', 800, 200, {}, [60, 60]),
        b('Minus2', 480, 300, {}, [60, 60]),
        b('Plus2', 800, 300, {}, [60, 60]),
        b('Back', 660, 420, {}, [200, 64]),
      ];
      expect(walk(rows, 'Minus1', ['right', 'right', 'left'])).toEqual(['Plus1', 'Minus1', 'Plus1']);
      expect(walk(rows, 'Minus1', ['down', 'right', 'down'])).toEqual(['Minus2', 'Plus2', 'Back']);
      // Up from Back: the Sound row, the nearer of its buttons across; then the Music row above it.
      expect(walk(rows, 'Back', ['up', 'up', 'up'])).toEqual(['Plus2', 'Plus1', 'Back']);
      expect(walk(rows, 'Minus2', ['down', 'down'])).toEqual(['Back', 'Plus1']); // wraps to the top row
    });

    it('Up and Down go to the nearest row, even when only a farther one lines up with the focused button', () => {
      // Options: Music and Sound sliders, a right-aligned toggle row (its label is not a button), Back.
      const options = [
        b('Music', 400, 150, { kind: 'slider' }, [400, 40]),
        b('Sound', 400, 220, { kind: 'slider' }, [400, 40]),
        b('Subtitles', 680, 290, { kind: 'toggle' }, [80, 40]),
        b('Back', 400, 370, {}, [200, 50]),
      ];
      // Back shares a column with both sliders, not with the toggle: Up still takes the toggle's row first.
      expect(walk(options, 'Back', ['up', 'up', 'up', 'up'])).toEqual(['Subtitles', 'Sound', 'Music', 'Back']);
      expect(walk(options, 'Music', ['down', 'down', 'down', 'down'])).toEqual(['Sound', 'Subtitles', 'Back', 'Music']);
      // A row is the buttons within half the focused height of the nearest: a toggle 10 px lower than
      // another still shares its row, and the one lined up with the focused button wins.
      const pair = [b('Top', 400, 100, {}, [200, 40]), b('Left', 380, 200, {}, [80, 40]), b('Right', 700, 190, {}, [80, 40])];
      expect(walk(pair, 'Top', ['down'])).toEqual(['Left']);
      expect(walk(pair, 'Left', ['up'])).toEqual(['Top']);
      // A neighbour a few pixels higher in the same row is not "up" (it shares the row): Up wraps down instead.
      const tilted = [b('A', 200, 300), b('B', 400, 296), b('Below', 300, 400)];
      expect(walk(tilted, 'A', ['up'])).toEqual(['Below']);
      expect(navigate([b('L', 200, 200), b('R', 600, 196)], 'L', 'up')).toBeNull();
    });

    it('leaves a single column as it was: up and down, wrapping; left and right go nowhere', () => {
      const column = [b('Play', 400, 200), b('Options', 400, 270), b('Quit', 400, 340)];
      expect(walk(column, 'Play', ['down', 'down', 'down', 'up'])).toEqual(['Options', 'Quit', 'Play', 'Quit']);
      expect(navigate(column, 'Options', 'left')).toBeNull();
      expect(navigate(column, 'Options', 'right')).toBeNull();
    });
  });

  it('starts at the authored focus when it is enabled, else the first in navigation order', () => {
    expect(defaultFocus(grid, 'C')?.id).toBe('C');
    expect(defaultFocus(grid, 'missing')?.id).toBe('A');
    expect(navigate(grid, null, 'down')?.id).toBe('A');
    expect(defaultFocus([], null)).toBeNull();
  });
});
