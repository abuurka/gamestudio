// Loading the exported Game UI (docs/GAME-UI.md §3, §5.1): `ui/index.json` next to the level
// manifests, the canvas files it lists, and the canvas layer's code, a separate chunk imported only
// when the game has canvases.

import { UI_CANVAS_FORMAT, UI_FORMAT_VERSION, UI_INDEX_FORMAT, type UiCanvasData, type UiIndex, type UiLocaleData } from './canvas/format';
import type { CreateCanvasLayer } from './canvas/types';
import { localeData } from './locales';

/** The canvas layer's chunk (src/ui/canvas). Games without Blender UI never load it. */
export function importCanvasLayer(): Promise<{ createCanvasLayer: CreateCanvasLayer }> {
  return import('./canvas/index');
}

/** Fetches JSON, never cached (exports change on every save). Null for a missing file (404, or a host's index.html). */
async function fetchJson(url: string): Promise<unknown> {
  const res = await fetch(url, { cache: 'no-store' });
  const html = (res.headers.get('content-type') ?? '').includes('text/html');
  if (res.status === 404 || (res.ok && html)) return null;
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

const message = (error: unknown): string => (error instanceof Error ? error.message : String(error));

/** The UI index, or null when the game has no Blender UI (no index, or an empty one). Never rejects. */
export async function fetchUiIndex(url: string, warn: (message: string) => void): Promise<UiIndex | null> {
  let raw: unknown;
  try {
    raw = await fetchJson(url);
  } catch (error) {
    warn(`Game UI: could not load ${url} (${message(error)}); the game shows no Blender UI`);
    return null;
  }
  if (raw === null) return null;
  const index = raw as Partial<UiIndex>;
  if (index.format !== UI_INDEX_FORMAT || !index.canvases || typeof index.canvases !== 'object') {
    warn(`Game UI: ${url} is not a UI index ("format": "${UI_INDEX_FORMAT}")`);
    return null;
  }
  if (typeof index.formatVersion === 'number' && index.formatVersion > UI_FORMAT_VERSION) {
    warn(`Game UI: ${url} has format version ${index.formatVersion}, newer than this engine's ${UI_FORMAT_VERSION}`);
  }
  return index as UiIndex;
}

/** Checks that a value is canvas data (a file, or a live `ui.canvas` payload). */
export function isCanvasData(data: unknown): data is UiCanvasData {
  const d = data as Partial<UiCanvasData> | null;
  return (
    !!d &&
    typeof d === 'object' &&
    d.format === UI_CANVAS_FORMAT &&
    typeof d.canvas === 'string' &&
    d.canvas !== '' &&
    Array.isArray(d.elements) &&
    !!d.settings &&
    typeof d.settings === 'object'
  );
}

/** A canvas file, or null (with a warning) when it is missing or not a canvas. Never rejects. */
export async function fetchCanvas(url: string, name: string, warn: (message: string) => void): Promise<UiCanvasData | null> {
  let raw: unknown;
  try {
    raw = await fetchJson(url);
  } catch (error) {
    warn(`Game UI: could not load canvas "${name}" from ${url} (${message(error)})`);
    return null;
  }
  if (raw === null) {
    warn(`Game UI: canvas "${name}" is listed in ui/index.json but ${url} is missing; export the UI file again`);
    return null;
  }
  if (!isCanvasData(raw)) {
    warn(`Game UI: ${url} is not a canvas file ("format": "${UI_CANVAS_FORMAT}")`);
    return null;
  }
  return raw;
}

/**
 * The translation tables the index lists (GAME-UI §11.3, `locales: {code: {file, name}}`, files relative
 * to the index), code → table in the index's order. A table that is missing or not a table is left out
 * with a warning. Never rejects.
 */
export async function fetchLocales(index: UiIndex, indexUrl: string, warn: (message: string) => void): Promise<Record<string, UiLocaleData>> {
  const listed = index.locales && typeof index.locales === 'object' ? Object.entries(index.locales) : [];
  const tables = await Promise.all(
    listed.map(async ([code, entry]): Promise<UiLocaleData | null> => {
      if (typeof entry?.file !== 'string' || !entry.file) {
        warn(`Game UI: ui/index.json lists the language "${code}" without a file`);
        return null;
      }
      const url = new URL(entry.file, indexUrl).href;
      let raw: unknown;
      try {
        raw = await fetchJson(url);
      } catch (error) {
        warn(`Game UI: could not load the "${code}" translations from ${url} (${message(error)})`);
        return null;
      }
      const table = raw === null ? null : localeData(raw);
      if (!table) warn(`Game UI: the "${code}" translations at ${url} are ${raw === null ? 'missing' : 'not a table ({"name", "strings"})'}; export the UI file again`);
      return table;
    }),
  );
  const out: Record<string, UiLocaleData> = {};
  listed.forEach(([code], i) => {
    const table = tables[i];
    if (table && code !== '__proto__') out[code] = table;
  });
  return out;
}

/**
 * URL of an asset a canvas uses: its copy in the export (`assets`, relative to the canvas file, or to
 * `base`: any URL in the export's `ui/` folder), else the file in the project. URLs with a scheme and
 * paths starting with `/` are used as they are (as for scene assets, SPEC §4.5).
 */
export function canvasAssetUrl(data: UiCanvasData, path: string, base: string): string {
  if (/^(?:[a-z][a-z0-9+.-]*:|\/)/i.test(path)) return path;
  const exported = data.assets?.[path];
  if (exported) return new URL(exported, base).href;
  return `${import.meta.env.BASE_URL}${path}`;
}
