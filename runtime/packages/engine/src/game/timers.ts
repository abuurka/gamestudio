// `ctx.after` / `ctx.every`: callbacks on simulated time. They pause with the
// game and are dropped when the scene loads or resets.

export interface Timer {
  /** Prevents any further calls. */
  cancel(): void;
  /** False once cancelled or (for `after`) fired. */
  readonly active: boolean;
}

interface Entry<C> {
  due: number;
  /** Seconds between calls for `every`; null for `after`. */
  interval: number | null;
  fn: (ctx: C) => void;
  active: boolean;
  seq: number;
}

export class Timers<C> {
  private entries: Entry<C>[] = [];
  private seq = 0;

  /** Calls `fn` once, `seconds` after `now`. */
  after(now: number, seconds: number, fn: (ctx: C) => void): Timer {
    return this.add(now + Math.max(0, seconds), null, fn);
  }

  /** Calls `fn` every `seconds` (at most once per frame), first after `seconds`. */
  every(now: number, seconds: number, fn: (ctx: C) => void): Timer {
    const interval = Math.max(0, seconds);
    return this.add(now + interval, interval, fn);
  }

  get size(): number {
    return this.entries.length;
  }

  /** Runs the timers that are due at `now`, earliest first. Errors go to `onError`; other timers still run. */
  run(now: number, ctx: C, onError: (error: unknown) => void): void {
    const due = this.entries.filter((e) => e.active && e.due <= now + 1e-9).sort((a, b) => a.due - b.due || a.seq - b.seq);
    for (const entry of due) {
      if (!entry.active) continue; // Cancelled by an earlier callback.
      if (entry.interval === null) entry.active = false;
      else entry.due = Math.max(entry.due + entry.interval, now + Math.min(entry.interval, 1e-6));
      try {
        entry.fn(ctx);
      } catch (error) {
        onError(error);
      }
    }
    if (due.length > 0) this.entries = this.entries.filter((e) => e.active);
  }

  clear(): void {
    for (const entry of this.entries) entry.active = false;
    this.entries = [];
  }

  private add(due: number, interval: number | null, fn: (ctx: C) => void): Timer {
    if (typeof fn !== 'function') throw new Error('Timer callback must be a function');
    const entry: Entry<C> = { due, interval, fn, active: true, seq: this.seq++ };
    this.entries.push(entry);
    return {
      cancel: () => {
        entry.active = false;
      },
      get active() {
        return entry.active;
      },
    };
  }
}
