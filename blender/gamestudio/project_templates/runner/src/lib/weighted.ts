// Seeded, weighted picks. A pick draws exactly one number from the stream it is given, so a stream
// such as ctx.random.stream('track') stays in step: the same seed always builds the same track.
import type { RandomAPI } from '@gamestudio/engine';

export interface Weighted<T> {
  item: T;
  weight: number;
}

/** "Hurdle:3, Crates, Boulder:0.5" -> Hurdle 3, Crates 1, Boulder 0.5. Names without a weight count 1. */
export function parseWeighted(text: string): Weighted<string>[] {
  return text
    .split(',')
    .map((part) => part.split(':'))
    .map(([name, weight]) => ({ item: (name ?? '').trim(), weight: weight === undefined ? 1 : Number(weight) }))
    .filter((entry) => entry.item && entry.weight > 0);
}

/** One item, with a chance proportional to its weight, or undefined for an empty list. */
export function pickWeighted<T>(random: RandomAPI, items: readonly Weighted<T>[]): T | undefined {
  const total = items.reduce((sum, entry) => sum + entry.weight, 0);
  if (!(total > 0)) return undefined;
  let roll = random() * total;
  for (const entry of items) {
    roll -= entry.weight;
    if (roll < 0) return entry.item;
  }
  return items[items.length - 1]!.item;
}
