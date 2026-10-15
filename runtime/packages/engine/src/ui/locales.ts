// Translations (docs/GAME-UI.md §11.3): the languages a game's UI has (the authored one, `sourceLocale`,
// and a table per translation, ui/locales/<code>.json) and the one shown. Text bodies, bindings
// included, are looked up in the current table as authored; a body without a translation shows as it
// is. Which language a player starts in is saved (`gs.locale`), else the best match of the browser's
// languages, else the authored one. Pure: the Ui (ui.ts) owns loading, saving and the live link.
import type { UiLocaleData } from './canvas/format';

/** Where `ctx.ui.locale` is saved in `ctx.save`. */
export const LOCALE_KEY = 'gs.locale';

/** A language of the game's UI (`ctx.ui.locales`). */
export interface UiLocale {
  /** The language code, e.g. 'en', 'fr', 'pt-BR'. */
  code: string;
  /** Its name in its own language, e.g. 'Français'. */
  name: string;
}

const lower = (code: string): string => code.trim().replace(/_/g, '-').toLowerCase();
const primary = (code: string): string => lower(code).split('-')[0]!;

/**
 * The best of `available` for a list of preferred languages (navigator.languages), or null. Each
 * preferred language in turn: the same code (any case, `_` as `-`), else the same language (`fr-CA`
 * finds `fr`, else `fr-FR`).
 */
export function matchLocale(preferred: readonly unknown[], available: readonly string[]): string | null {
  for (const want of preferred) {
    if (typeof want !== 'string' || !want.trim()) continue;
    const code = lower(want);
    const exact = available.find((c) => lower(c) === code);
    if (exact) return exact;
    const language = primary(want);
    const near = available.find((c) => lower(c) === language) ?? available.find((c) => primary(c) === language);
    if (near) return near;
  }
  return null;
}

/** A language's name in its own language ('fr' → 'Français'), from Intl.DisplayNames; the code without it. */
export function languageName(code: string): string {
  try {
    const name = new Intl.DisplayNames([code], { type: 'language' }).of(code);
    if (name && name !== code) return name.charAt(0).toLocaleUpperCase(code) + name.slice(1);
  } catch {
    // An unknown or malformed code, or no Intl.DisplayNames.
  }
  return code;
}

/** The browser's preferred languages, most preferred first ([] outside a browser). */
export function browserLanguages(): readonly string[] {
  const nav = (globalThis as { navigator?: { languages?: readonly string[]; language?: string } }).navigator;
  if (nav?.languages && nav.languages.length > 0) return nav.languages;
  return nav?.language ? [nav.language] : [];
}

/**
 * A translation table from a file or a live message: its name and its string → string entries (others
 * are dropped). Null when it is not an object.
 */
export function localeData(raw: unknown): UiLocaleData | null {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return null;
  const data = raw as { name?: unknown; strings?: unknown };
  const strings: Record<string, string> = {};
  if (data.strings && typeof data.strings === 'object' && !Array.isArray(data.strings)) {
    for (const [key, value] of Object.entries(data.strings as Record<string, unknown>)) {
      if (typeof value === 'string') strings[key] = value;
    }
  }
  return { name: typeof data.name === 'string' ? data.name.trim() : '', strings };
}

/** The game's languages, their tables and the one shown. */
export class Locales {
  /** The language the texts are authored in. */
  source = 'en';
  /** Code → table, in the index's order; may hold one for the source language too. */
  private tables = new Map<string, UiLocaleData>();
  /** Code → name, from the index (else the table's name). */
  private names = new Map<string, string>();
  /** What `apply` was last given, to tell a change from a repeat. */
  private signature = '';
  /** The language shown, or null before one is chosen. */
  current: string | null = null;
  /** Bumps when the language shown or its table changes: texts look their bodies up again. */
  version = 0;

  /** Every language: the source first, then the tables' in order. */
  list(): UiLocale[] {
    const out: UiLocale[] = [{ code: this.source, name: this.nameOf(this.source) }];
    for (const code of this.tables.keys()) if (code !== this.source) out.push({ code, name: this.nameOf(code) });
    return out;
  }

  codes(): string[] {
    return this.list().map((l) => l.code);
  }

  /** The game's spelling of a code or a close language (`FR`, `fr-CA` → `fr`), or null when it has none. */
  find(code: string): string | null {
    return matchLocale([code], this.codes());
  }

  nameOf(code: string): string {
    const name = this.names.get(code) || this.tables.get(code)?.name;
    return name || languageName(code);
  }

  /**
   * Replaces the languages and their tables (the index's, or a live `ui.locales`). False when nothing
   * changed. `names` gives each code's name (the index's `locales`); a code without a table is left out.
   */
  apply(source: string, tables: Record<string, UiLocaleData>, names: Record<string, string> = {}): boolean {
    const signature = JSON.stringify([source, tables, names]);
    if (signature === this.signature) return false;
    this.signature = signature;
    this.source = source;
    this.tables = new Map(Object.entries(tables));
    this.names = new Map(Object.entries(names).filter(([, name]) => typeof name === 'string' && name.trim() !== ''));
    this.version++;
    return true;
  }

  /** Shows a language (a code of `codes()`). False when it was already shown. */
  use(code: string): boolean {
    if (code === this.current) return false;
    this.current = code;
    this.version++;
    return true;
  }

  /**
   * The language to start in: `saved` when the game has it, else the best match of `preferred` (the
   * browser's languages), else the source language.
   */
  pick(saved: unknown, preferred: readonly unknown[]): string {
    const codes = this.codes();
    const exact = typeof saved === 'string' ? codes.find((c) => lower(c) === lower(saved)) : undefined;
    return exact ?? matchLocale(preferred, codes) ?? this.source;
  }

  /** `text` in the language shown: its table's entry for it, else `text` itself (an empty entry counts as none). */
  translate(text: string): string {
    const table = this.current === null ? undefined : this.tables.get(this.current);
    if (!table || typeof text !== 'string' || !Object.hasOwn(table.strings, text)) return text;
    const translated = table.strings[text];
    return typeof translated === 'string' && translated !== '' ? translated : text;
  }
}
