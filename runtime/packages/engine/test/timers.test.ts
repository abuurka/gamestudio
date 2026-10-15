import { describe, expect, it } from 'vitest';
import { Timers } from '../src/game/timers';

describe('Timers (ctx.after / ctx.every)', () => {
  it('fires after a delay, in due order, once', () => {
    const timers = new Timers<string>();
    const calls: string[] = [];
    timers.after(0, 0.5, (ctx) => calls.push(`b:${ctx}`));
    const a = timers.after(0, 0.2, () => calls.push('a'));
    timers.after(0, 0.2, () => calls.push('a2'));
    timers.run(0.1, 'ctx', () => {});
    expect(calls).toEqual([]);
    expect(a.active).toBe(true);
    timers.run(0.6, 'ctx', () => {});
    expect(calls).toEqual(['a', 'a2', 'b:ctx']);
    expect(a.active).toBe(false);
    timers.run(5, 'ctx', () => {});
    expect(calls).toHaveLength(3);
    expect(timers.size).toBe(0);
  });

  it('repeats with every(), at most once per frame, until cancelled', () => {
    const timers = new Timers<null>();
    let n = 0;
    const t = timers.every(0, 0.25, () => n++);
    for (let time = 0.1; time <= 1.001; time += 0.1) timers.run(time, null, () => {});
    expect(n).toBe(4); // 0.25, 0.5, 0.75, 1.0
    timers.run(10, null, () => {});
    expect(n).toBe(5); // A long frame does not fire a burst.
    t.cancel();
    timers.run(20, null, () => {});
    expect(n).toBe(5);
    expect(t.active).toBe(false);
  });

  it('isolates errors, lets callbacks cancel others, and clears', () => {
    const timers = new Timers<null>();
    const errors: unknown[] = [];
    const calls: string[] = [];
    let second: { cancel(): void } | null = null;
    timers.after(0, 0.1, () => {
      second?.cancel();
      throw new Error('boom');
    });
    second = timers.after(0, 0.1, () => calls.push('second'));
    timers.after(0, 0.1, () => calls.push('third'));
    timers.run(1, null, (e) => errors.push(e));
    expect(calls).toEqual(['third']);
    expect(String(errors[0])).toBe('Error: boom');
    const later = timers.after(1, 1, () => calls.push('later'));
    timers.clear();
    timers.run(5, null, () => {});
    expect(later.active).toBe(false);
    expect(calls).toEqual(['third']);
    expect(() => timers.after(0, 1, null as never)).toThrow(/must be a function/);
  });
});
