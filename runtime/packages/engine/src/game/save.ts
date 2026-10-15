// `ctx.save`: small values that survive page reloads (high scores, unlocked levels, settings), in
// localStorage under a per-project prefix. While a playtest holds the clock it is an empty
// in-memory store instead, so runs repeat and never touch the player's real save.

export interface SaveAPI {
  /** The stored value, or `fallback` when there is none. */
  get<T = unknown>(key: string, fallback?: T): T;
  /** Stores a JSON-serializable value. */
  set(key: string, value: unknown): void;
  has(key: string): boolean;
  delete(key: string): void;
  /** Deletes every value of this game. */
  clear(): void;
  keys(): string[];
}

interface Backend {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
  removeItem(key: string): void;
  keys(): string[];
}

function memory(): Backend {
  const map = new Map<string, string>();
  return {
    getItem: (k) => map.get(k) ?? null,
    setItem: (k, v) => void map.set(k, v),
    removeItem: (k) => void map.delete(k),
    keys: () => [...map.keys()],
  };
}

function browser(): Backend | null {
  try {
    const storage = (globalThis as { localStorage?: Storage }).localStorage;
    if (!storage) return null;
    return {
      getItem: (k) => storage.getItem(k),
      setItem: (k, v) => storage.setItem(k, v),
      removeItem: (k) => storage.removeItem(k),
      keys: () => Array.from({ length: storage.length }, (_, i) => storage.key(i)).filter((k): k is string => k !== null),
    };
  } catch {
    return null; // Storage blocked (privacy settings, sandboxed frames).
  }
}

export class SaveStore implements SaveAPI {
  private backend: Backend;
  private readonly prefix: string;

  constructor(game: string) {
    this.prefix = `gamestudio:${game || 'game'}:`;
    this.backend = browser() ?? memory();
  }

  /** `true`: a fresh in-memory store (playtests); `false`: back to the browser's storage. */
  isolate(on: boolean): void {
    this.backend = on ? memory() : (browser() ?? memory());
  }

  get<T = unknown>(key: string, fallback?: T): T {
    const raw = this.backend.getItem(this.prefix + key);
    if (raw === null) return fallback as T;
    try {
      return JSON.parse(raw) as T;
    } catch {
      return fallback as T;
    }
  }

  set(key: string, value: unknown): void {
    try {
      this.backend.setItem(this.prefix + key, JSON.stringify(value));
    } catch (error) {
      console.warn(`[gamestudio] ctx.save.set("${key}") failed: ${error instanceof Error ? error.message : String(error)}`);
    }
  }

  has(key: string): boolean {
    return this.backend.getItem(this.prefix + key) !== null;
  }

  delete(key: string): void {
    this.backend.removeItem(this.prefix + key);
  }

  clear(): void {
    for (const key of this.keys()) this.delete(key);
  }

  keys(): string[] {
    return this.backend
      .keys()
      .filter((k) => k.startsWith(this.prefix))
      .map((k) => k.slice(this.prefix.length));
  }
}
