// Translations of the Game UI (docs/GAME-UI.md §11.3): language matching and tables (pure), text bodies
// looked up before their bindings in the canvas model, and in a running game: the start's language,
// ctx.ui.locale / locales / translate, saving, test isolation and seeding, `locale` buttons, the
// `ui.locale` and `ui.localeName` value paths, live `ui.locales` and a warm page's refresh.
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import type { SystemContext } from '../src/context';
import type { Game } from '../src/game/game';
import { handleEditorMessage } from '../src/link/handlers';
import { RapierPhysics } from '../src/physics/rapier-backend';
import '../src/systems';
import type { UiCanvasData, UiElementData, UiLocaleData } from '../src/ui/canvas/format';
import { createCanvasLayer } from '../src/ui/canvas/index';
import { languageName, localeData, Locales, matchLocale } from '../src/ui/locales';
import { isReadOnly, readValue, writeValue, type ValueSources } from '../src/ui/values';
import { installDom, type FakeDom } from './dom';
import { heldGame, sceneFiles } from './game-fixture';
import { button, canvas, text, uiFiles } from './ui-controller-fixtures';

const loaded = vi.hoisted(() => ({ imports: 0 }));
vi.mock('../src/ui/loader', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../src/ui/loader')>();
  const { FakeLayer } = await import('./ui-controller-fixtures');
  return {
    ...actual,
    importCanvasLayer: async () => {
      loaded.imports++;
      return { createCanvasLayer: (host: ConstructorParameters<typeof FakeLayer>[0]) => new FakeLayer(host) };
    },
  };
});

const FR: UiLocaleData = { name: 'Français', strings: { 'Gem Island': "L'île aux gemmes", Play: 'Jouer', 'Score {score}': 'Points {score}' } };
const DE: UiLocaleData = { name: 'Deutsch', strings: { Play: 'Spielen', 'Score {score}': 'Punkte: {score}' } };

describe('languages and tables', () => {
  it('match the browser languages: the same code, else the same language, in the order preferred', () => {
    const available = ['en', 'fr', 'pt-BR', 'zh-Hant'];
    expect(matchLocale(['fr'], available)).toBe('fr');
    expect(matchLocale(['fr-CA'], available)).toBe('fr'); // the language prefix
    expect(matchLocale(['pt-PT'], available)).toBe('pt-BR'); // another region of the language
    expect(matchLocale(['PT_br'], available)).toBe('pt-BR'); // any case, _ as -
    expect(matchLocale(['ja', 'de-AT', 'fr-BE', 'en'], available)).toBe('fr'); // the first that matches wins
    expect(matchLocale(['en-GB', 'fr'], available)).toBe('en');
    expect(matchLocale(['zh-TW'], available)).toBe('zh-Hant');
    expect(matchLocale(['ja', '', 5], available)).toBeNull();
    expect(matchLocale([], available)).toBeNull();
    // An exact region beats the bare language listed earlier among the available ones.
    expect(matchLocale(['pt-BR'], ['pt', 'pt-BR'])).toBe('pt-BR');
    expect(matchLocale(['pt-PT'], ['pt-BR', 'pt'])).toBe('pt');
  });

  it('name a language in its own language, else by its code', () => {
    expect(languageName('en')).toBe('English');
    expect(languageName('fr')).toBe('Français');
    expect(languageName('de')).toBe('Deutsch');
    expect(languageName('not a code!')).toBe('not a code!');
  });

  it('read tables leniently: string entries only, a name or none', () => {
    expect(localeData({ name: ' Français ', strings: { Play: 'Jouer', Bad: 3, Empty: '' } })).toEqual({ name: 'Français', strings: { Play: 'Jouer', Empty: '' } });
    expect(localeData({ strings: [] })).toEqual({ name: '', strings: {} });
    expect(localeData(null)).toBeNull();
    expect(localeData([1])).toBeNull();
  });

  it('list the source first, pick the saved language, else the browser match, else the source, and translate', () => {
    const l = new Locales();
    expect(l.list()).toEqual([{ code: 'en', name: 'English' }]);
    expect(l.apply('en', { fr: FR, de: { name: '', strings: {} } }, { de: 'Deutsch (DE)' })).toBe(true);
    expect(l.apply('en', { fr: FR, de: { name: '', strings: {} } }, { de: 'Deutsch (DE)' })).toBe(false); // the same again
    expect(l.list()).toEqual([
      { code: 'en', name: 'English' },
      { code: 'fr', name: 'Français' },
      { code: 'de', name: 'Deutsch (DE)' }, // the index's name over the table's
    ]);
    expect(l.pick('fr', ['de'])).toBe('fr');
    expect(l.pick('FR', ['de'])).toBe('fr');
    expect(l.pick('tlh', ['de-CH'])).toBe('de'); // a saved language the game doesn't have
    expect(l.pick(undefined, ['ja'])).toBe('en');
    expect(l.find('fr-CA')).toBe('fr');
    expect(l.find('ja')).toBeNull();
    expect(l.translate('Play')).toBe('Play'); // nothing shown yet
    const version = l.version;
    expect(l.use('fr')).toBe(true);
    expect(l.use('fr')).toBe(false);
    expect(l.version).toBe(version + 1);
    expect(l.translate('Play')).toBe('Jouer');
    expect(l.translate('Score {score}')).toBe('Points {score}'); // looked up as authored, bindings included
    expect(l.translate('Quit')).toBe('Quit');
    expect(l.translate('toString')).toBe('toString'); // own entries only
    l.apply('en', { fr: { name: 'Français', strings: { Play: '' } } });
    expect(l.translate('Play')).toBe('Play'); // an empty entry is untranslated
  });
});

describe('value paths ui.locale and ui.localeName', () => {
  it('read the language and its name; write the language; localeName is read-only', () => {
    const ui = { locale: 'en', localeName: 'English' };
    const s: ValueSources = { store: { ui: { other: 1 } }, save: { get: () => undefined, set: () => {} }, ui };
    expect(readValue('ui.locale', s)).toBe('en');
    expect(readValue('ui.localeName', s)).toBe('English');
    expect(readValue('ui.other', s)).toBe(1); // other ui.x paths are ctx.store's: store.ui.other
    expect(readValue('ui', s)).toEqual({ other: 1 });
    expect(writeValue('ui.locale', 'fr', s)).toBe(true);
    expect(ui.locale).toBe('fr');
    expect(writeValue('ui.locale', true, s)).toBe(false); // a toggle can't
    expect(writeValue('ui.localeName', 'x', s)).toBe(false);
    expect(isReadOnly('ui.localeName')).toBe(true);
    expect(isReadOnly('ui.locale')).toBe(false);
    expect(readValue('ui.locale', { ...s, ui: null })).toBeUndefined();
    expect(writeValue('ui.locale', 'fr', { ...s, ui: null })).toBe(false);
  });
});

describe('canvas texts in the current language', () => {
  const label = (id: string, body: string, parent: string | null = null, y = 100): UiElementData => ({
    id,
    name: id,
    parent,
    type: 'text',
    visible: true,
    order: 1,
    transform: { x: parent ? 0 : 960, y: parent ? 0 : y, rotation: 0, scaleX: 1, scaleY: 1 },
    ...(parent === null && { anchor: 'center' as const, stretch: 'none' as const }),
    components: {},
    text: { body, font: '', size: 40, color: [1, 1, 1, 1], align: 'center', valign: 'center', lineSpacing: 1, letterSpacing: 1, wordSpacing: 1, box: null, offset: [0, 0], shear: 0 },
  });
  const menu = (name: string, elements: UiElementData[]): UiCanvasData => ({
    format: 'gamestudio.ui',
    formatVersion: 1,
    canvas: name,
    source: 'ui/ui.blend',
    rev: 1,
    exportedAt: '',
    size: [1920, 1080],
    settings: { kind: 'menu', open: 'manual', pause: true, pauseMenu: false, back: 'none', scaleMode: 'fit', backdrop: [0, 0, 0, 0], layer: 0, focus: null },
    elements,
    assets: {},
  });

  it('look the body up as authored before its bindings, fall back to it, and render again on a switch', async () => {
    const dom = installDom({}, [1920, 1080]);
    let table: Record<string, string> = {};
    let version = 0;
    const lookups: string[] = [];
    const store: Record<string, unknown> = { score: 7 };
    const layer = createCanvasLayer({
      canvas: dom.canvas as unknown as HTMLCanvasElement,
      assetUrl: (_data, p) => p,
      value: (p) => store[p],
      warn: () => {},
      translate: (body) => {
        lookups.push(body);
        return table[body] ?? body;
      },
      localeVersion: () => version,
    });
    try {
      await layer.set(
        menu('Menu', [
          label('Score', 'Score {score:000}'),
          { ...label('Play', 'unused'), type: 'group', text: undefined, components: { UiButton: { action: 'close', target: '' } } },
          label('PlayLabel', 'Play', 'Play'),
          label('Title', 'Gem Island', null, 300),
          label('Plain', 'Untranslated', null, 500),
          label('Note', 'Code text', null, 700),
        ]),
      );
      await layer.set(menu('Closed', [label('Gone', 'Play')]));
      layer.setOpen([], 'Menu');
      layer.update(0);
      const texts = () => layer.texts(['Menu']);
      expect(texts()).toEqual({ 'Menu/Score': 'Score 007', 'Menu/PlayLabel': 'Play', 'Menu/Title': 'Gem Island', 'Menu/Plain': 'Untranslated', 'Menu/Note': 'Code text' });
      table = { 'Score {score:000}': 'Punkte {score:000}', Play: 'Spielen', 'Gem Island': 'Edelstein-Insel {{1}}', 'Code text': 'Codetext' };
      layer.update(0);
      expect(texts()['Menu/Score']).toBe('Score 007'); // the same language: bodies are not looked up again
      lookups.length = 0;
      version++;
      layer.update(0);
      expect(texts()).toEqual({
        'Menu/Score': 'Punkte 007', // the translation's bindings
        'Menu/PlayLabel': 'Spielen',
        'Menu/Title': 'Edelstein-Insel {1}', // {{ }} are literal braces in translations too
        'Menu/Plain': 'Untranslated', // no entry: the authored text
        'Menu/Note': 'Codetext',
      });
      expect(lookups.sort()).toEqual(['Code text', 'Gem Island', 'Play', 'Score {score:000}', 'Untranslated']);
      expect(layer.buttons('Menu')[0]!.label).toBe('Spielen');
      store.score = 8;
      layer.update(0);
      expect(texts()['Menu/Score']).toBe('Punkte 008');
      // A code override stays over the translation; null brings back the body in the current language.
      const note = layer.find('Menu/Note', [])!;
      layer.setText('Menu', note.element.id, 'From code');
      version++;
      layer.update(0);
      expect(texts()['Menu/Note']).toBe('From code');
      layer.setText('Menu', note.element.id, null);
      expect(texts()['Menu/Note']).toBe('Codetext');
      // A canvas that is not open looks its texts up when it is queried.
      expect(layer.info('Closed', layer.find('Closed/Gone', [])!.element.id).text).toBe('Spielen');
      // Back to the authored language.
      table = {};
      version++;
      layer.update(0);
      expect(texts()['Menu/Score']).toBe('Score 008');
      expect(texts()['Menu/PlayLabel']).toBe('Play');
    } finally {
      layer.dispose();
      dom.restore();
    }
  });
});

// --- In a running game ---------------------------------------------------------------------

const Title = canvas('Title', { open: 'firstStart' }, [
  text('TitleText', 'Gem Island', [400, 40]),
  button('Play', 'Play', [400, 110], { action: 'close' }),
  button('Language', '{ui.localeName}', [400, 180], { action: 'locale' }),
  button('French', 'Français', [400, 250], { action: 'locale', target: 'fr' }),
  button('Klingon', 'Klingon', [400, 320], { action: 'locale', target: 'tlh' }),
]);
const HUD = canvas('HUD', { kind: 'hud', open: 'start', backdrop: [0, 0, 0, 0] }, [text('Score', 'Score {score}', [100, 30]), text('Lang', 'Language: {ui.locale}', [100, 80])]);

/** The export with translation tables: `locales/<code>.json` beside the index, listed in it. */
function localeFiles(canvases: UiCanvasData[], tables: Record<string, UiLocaleData>, source = 'en'): Record<string, string> {
  const files = uiFiles(canvases);
  const indexFile = files['/export/ui/index.json']!;
  const dir = path.dirname(indexFile);
  const index = JSON.parse(readFileSync(indexFile, 'utf8'));
  index.sourceLocale = source;
  index.locales = {};
  mkdirSync(path.join(dir, 'locales'), { recursive: true });
  for (const [code, table] of Object.entries(tables)) {
    const file = `locales/${code}.json`;
    writeFileSync(path.join(dir, file), JSON.stringify(table));
    index.locales[code] = { file, name: table.name };
    files[`/export/ui/${file}`] = path.join(dir, file);
  }
  writeFileSync(indexFile, JSON.stringify(index));
  return files;
}

/** localStorage, so the player's save outlives a test's own store as in a browser. */
function fakeStorage(): Storage {
  const map = new Map<string, string>();
  return {
    getItem: (k: string) => map.get(k) ?? null,
    setItem: (k: string, v: string) => void map.set(k, String(v)),
    removeItem: (k: string) => void map.delete(k),
    clear: () => map.clear(),
    key: (i: number) => [...map.keys()][i] ?? null,
    get length() {
      return map.size;
    },
  };
}

let dom: FakeDom | null = null;
/** The last game's export: URL path → file on disk. */
let served: Record<string, string> = {};
beforeAll(async () => {
  await new RapierPhysics().init();
});
afterEach(() => {
  dom?.restore();
  dom = null;
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

async function game(languages: string[], tables: Record<string, UiLocaleData> = { fr: FR, de: DE }, canvases = [Title, HUD]): Promise<Game> {
  vi.stubGlobal('navigator', { languages, language: languages[0] });
  vi.stubGlobal('localStorage', fakeStorage());
  served = localeFiles(canvases, tables);
  dom = installDom({ ...sceneFiles(), ...served });
  return heldGame(dom);
}

const ui = (g: Game) => g.test.state({ ids: [] }).ui;
const ctxOf = (g: Game): SystemContext => (g as unknown as { ctx: SystemContext }).ctx;

async function tap(g: Game, key: string, steps = 2): Promise<void> {
  g.test.schedule([{ step: g.test.status().step, key }]);
  await g.test.advance(steps);
}

describe('translations in a running game', () => {
  it("start in the browser's best match, list the languages and translate canvas texts and code strings", async () => {
    const g = await game(['fr-CA', 'de']);
    expect(g.ui.locale).toBe('fr');
    expect(g.ui.locales).toEqual([
      { code: 'en', name: 'English' },
      { code: 'fr', name: 'Français' },
      { code: 'de', name: 'Deutsch' },
    ]);
    expect(g.ui.translate('Play')).toBe('Jouer');
    expect(g.ui.translate('Score {score}')).toBe('Points {score}');
    expect(g.ui.translate('Crashed!')).toBe('Crashed!');
    await g.test.begin({ store: { score: 3 } });
    await g.test.advance(1);
    const state = ui(g);
    expect(state.locale).toBe('fr');
    expect(state.texts).toEqual({
      'HUD/Score': 'Points 3',
      'HUD/Lang': 'Language: fr', // no entry: authored, with ui.locale
      'Title/TitleText': "L'île aux gemmes",
      'Title/PlayLabel': 'Jouer',
      'Title/LanguageLabel': 'Français', // {ui.localeName}
      'Title/FrenchLabel': 'Français',
      'Title/KlingonLabel': 'Klingon',
    });
    expect(state.focus).toBe('Jouer');
  });

  it('start in the source language when nothing matches, or without tables', async () => {
    let g = await game(['ja', 'ko']);
    expect(g.ui.locale).toBe('en');
    dom!.restore();
    g = await game(['fr'], {});
    expect(g.ui.locale).toBe('en');
    expect(g.ui.locales).toEqual([{ code: 'en', name: 'English' }]);
  });

  it('save ctx.ui.locale, which a start prefers over the browser; unknown languages warn once', async () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
    const g = await game(['en-US']);
    expect(g.ui.locale).toBe('en');
    g.ui.locale = 'de';
    expect(g.ui.locale).toBe('de');
    expect(g.save.get('gs.locale')).toBe('de');
    g.ui.locale = 'FR-ca'; // a close language
    expect(g.ui.locale).toBe('fr');
    expect(g.save.get('gs.locale')).toBe('fr');
    g.ui.locale = 'tlh';
    g.ui.locale = 'tlh';
    expect(g.ui.locale).toBe('fr');
    const warnings = warn.mock.calls.map(([m]) => String(m)).filter((m) => m.includes('ctx.ui.locale = "tlh"'));
    expect(warnings).toHaveLength(1);
    expect(warnings[0]).toContain('languages: en, fr, de');
    // The next start (a page load) takes the saved language over the browser's.
    g.ui.reloadLocale();
    expect(g.ui.locale).toBe('fr');
    g.save.set('gs.locale', 'de');
    g.ui.reloadLocale();
    expect(g.ui.locale).toBe('de');
  });

  it("isolate a test's save: it starts in the browser's language, begin({ save }) seeds one, end() gives the player's back", async () => {
    const g = await game(['en-GB']);
    g.ui.locale = 'de'; // the player's choice
    await g.test.begin();
    expect(g.ui.locale).toBe('en'); // the test's own, empty save
    await g.test.advance(1);
    expect(ui(g).texts['Title/PlayLabel']).toBe('Play');
    await g.test.begin({ save: { 'gs.locale': 'fr' } });
    expect(g.ui.locale).toBe('fr');
    await g.test.advance(1);
    expect(ui(g)).toMatchObject({ locale: 'fr', texts: { 'Title/PlayLabel': 'Jouer' } });
    g.ui.locale = 'en';
    expect(g.test.saved()).toEqual({}); // gs. keys are the engine's
    expect(g.test.saved({ internal: true })).toMatchObject({ 'gs.locale': 'en' });
    g.test.end();
    expect(g.ui.locale).toBe('de');
    expect(g.save.get('gs.locale')).toBe('de');
  });

  it('switch with locale buttons: no target cycles in ctx.ui.locales order, a target picks a language', async () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
    const g = await game(['en']);
    await g.test.begin();
    await g.test.advance(1);
    const seen: string[] = [];
    g.ui.on('Language', (ctx) => void seen.push(ctx.ui.locale)); // after the action: the new language
    const label = () => ui(g).texts['Title/LanguageLabel'];
    expect(label()).toBe('English');
    await tap(g, 'ArrowDown');
    expect(ui(g).focus).toBe('English');
    await tap(g, 'Enter');
    expect([ui(g).locale, label(), ui(g).texts['Title/PlayLabel']]).toEqual(['fr', 'Français', 'Jouer']);
    await tap(g, 'Enter');
    expect([ui(g).locale, label(), ui(g).texts['Title/PlayLabel']]).toEqual(['de', 'Deutsch', 'Spielen']);
    await tap(g, 'Enter');
    expect([ui(g).locale, label(), ui(g).texts['Title/PlayLabel']]).toEqual(['en', 'English', 'Play']); // wraps
    expect(seen).toEqual(['fr', 'de', 'en']);
    expect(g.test.saved({ internal: true })['gs.locale']).toBe('en');
    await tap(g, 'ArrowDown'); // French
    await tap(g, 'Enter');
    expect(ui(g).locale).toBe('fr');
    await tap(g, 'Enter');
    expect(ui(g).locale).toBe('fr');
    await tap(g, 'ArrowDown'); // Klingon: the game has no table
    await tap(g, 'Enter');
    await tap(g, 'Enter');
    expect(ui(g).locale).toBe('fr');
    const warnings = warn.mock.calls.map(([m]) => String(m)).filter((m) => m.includes('"Title/Klingon" switches to "tlh"'));
    expect(warnings).toHaveLength(1);
    expect(ui(g).screen).toBe('Title'); // a locale button keeps the menu open
  });

  it('take ui.locale writes from code value paths and show ui.localeName', async () => {
    const g = await game(['en']);
    await g.test.begin();
    await g.test.advance(1);
    const ctx = ctxOf(g);
    expect(ctx.ui.locale).toBe('en');
    ctx.ui.locale = 'de';
    await g.test.advance(1);
    expect(ui(g).texts).toMatchObject({ 'HUD/Lang': 'Language: de', 'Title/LanguageLabel': 'Deutsch' });
  });

  it('replace the tables live with ui.locales; a language that is gone falls back as a start would', async () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
    const g = await game(['en'], { fr: FR });
    await g.test.begin({ save: { 'gs.locale': 'fr' } });
    await g.test.advance(1);
    expect(ui(g).texts['Title/PlayLabel']).toBe('Jouer');
    const send = () => {};
    handleEditorMessage(
      g,
      {
        t: 'ui.locales',
        sourceLocale: 'en',
        locales: { fr: { file: 'locales/fr.json', name: 'Français' }, es: { file: 'locales/es.json', name: 'Español' } },
        tables: { fr: { name: 'Français', strings: { Play: 'Jouez' } }, es: { name: 'Español', strings: { Play: 'Jugar' } } },
      },
      send,
    );
    await g.ui.afterLoad(() => {});
    await g.test.advance(1);
    expect(g.ui.locales.map((l) => l.code)).toEqual(['en', 'fr', 'es']);
    expect(ui(g).texts).toMatchObject({ 'Title/PlayLabel': 'Jouez', 'Title/TitleText': 'Gem Island' });
    g.ui.locale = 'es';
    await g.test.advance(1);
    expect(ui(g).texts['Title/PlayLabel']).toBe('Jugar');
    // Spanish goes: the saved language (es) is gone, nothing matches the browser's en... the source.
    handleEditorMessage(g, { t: 'ui.locales', sourceLocale: 'en', locales: { fr: { file: 'locales/fr.json', name: 'Français' } }, tables: { fr: FR } }, send);
    await g.ui.afterLoad(() => {});
    await g.test.advance(1);
    expect(ui(g)).toMatchObject({ locale: 'en', texts: { 'Title/PlayLabel': 'Play' } });
    handleEditorMessage(g, { t: 'ui.locales', sourceLocale: 'en', locales: {} }, send);
    expect(warn.mock.calls.some(([m]) => String(m).includes('ui.locales without tables'))).toBe(true);
    expect(g.ui.locales.map((l) => l.code)).toEqual(['en', 'fr']);
  });

  it("fetch the tables again on a warm page's refresh", async () => {
    const g = await game(['fr']);
    await g.test.begin({ save: { 'gs.locale': 'fr' } });
    await g.test.advance(1);
    expect(ui(g).texts['Title/PlayLabel']).toBe('Jouer');
    // A tool rewrote the table and exported it while no game listened; the canvases did not change.
    writeFileSync(served['/export/ui/locales/fr.json']!, JSON.stringify({ name: 'Français', strings: { Play: 'Allons-y' } }));
    await g.ui.refresh();
    await g.test.advance(1);
    expect(ui(g).texts['Title/PlayLabel']).toBe('Allons-y');
  });

  it('translate code strings in a game without canvases, without loading the canvas code', async () => {
    const imports = loaded.imports;
    const g = await game(['de'], { de: DE }, []);
    expect(loaded.imports).toBe(imports);
    expect(g.ui.canvases).toEqual([]);
    expect(g.ui.locale).toBe('de');
    expect(g.ui.translate('Play')).toBe('Spielen');
    expect(ui(g).locale).toBe('de');
  });
});
