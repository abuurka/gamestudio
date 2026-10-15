// The 'ui' view (view.mode { mode: 'ui', canvas, frame, region?, background? }, docs/GAME-UI.md §11.2):
// a game stops and shows one canvas alone, at rest, laid out at the frame's size where the frame is (the
// canvas element moves there, mapped from the editor's region onto the page), over the level or a
// checkerboard; bindings without a value show as written; no input; live edits update it; other views
// clear it.
import type { Camera, Scene } from 'three';
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import type { SystemContext } from '../src/context';
import { Game } from '../src/game/game';
import { CanvasPlacement, Checkerboard, parseUiPreview, uiPlacement } from '../src/game/ui-preview';
import { handleEditorMessage } from '../src/link/handlers';
import { RapierPhysics } from '../src/physics/rapier-backend';
import '../src/systems';
import { parseBody, renderBody } from '../src/ui/canvas/bindings';
import type { UiCanvasData } from '../src/ui/canvas/format';
import type { CanvasLayer } from '../src/ui/canvas/types';
import { installDom, type FakeDom } from './dom';
import { BASIC, sceneFiles } from './game-fixture';
import { bar, button, canvas, text, uiFiles } from './ui-controller-fixtures';

/** A title with an open animation (a fade) and a loop (a bob) on its heading. */
const Title = (() => {
  const heading = text('TitleText', 'Gem Island', [400, 80]);
  const data = canvas('Title', { open: 'firstStart', backdrop: [0, 0, 0, 0.5] }, [
    heading,
    button('Play', 'Play', [400, 200], { action: 'close' }),
    button('Quit', 'Quit', [400, 300]),
  ]);
  data.animations = {
    open: { duration: 0.3, fps: 10, tracks: { [heading.id]: { opacity: [0, 0.33, 0.66, 1] } } },
    loop: { duration: 1, fps: 2, tracks: { [heading.id]: { y: [80, 90, 80] } } },
  };
  return data;
})();
const HUD = canvas('HUD', { kind: 'hud', open: 'start' }, [text('Score', 'Score {score:000}', [100, 30]), text('Lives', 'Lives {lives}', [100, 60]), bar('Health', [100, 90], 'health')]);
/** A menu that only fades in: at rest once shown. */
const Over = (() => {
  const message = text('Message', 'Game Over', [400, 80]);
  const data = canvas('Over', {}, [message, button('Retry', 'Retry', [400, 200], { action: 'restart' })]);
  data.animations = { open: { duration: 0.5, fps: 10, tracks: { [message.id]: { opacity: [0, 0.25, 0.5, 0.75, 1, 1] } } } };
  return data;
})();

let dom: FakeDom;
let game: Game | null = null;
/** What the fake fetch serves (URL path → file); tests add to it. */
let served: Record<string, string> = {};
beforeAll(async () => {
  await new RapierPhysics().init();
});
afterEach(() => {
  game?.dispose();
  game = null;
  vi.restoreAllMocks();
  dom?.restore();
});

/** A game on the basic level with these canvases, played (not held): frames come from `frames()`. */
async function open(canvases: UiCanvasData[] = [Title, HUD, Over]): Promise<Game> {
  dom = installDom((served = { ...sceneFiles(), ...uiFiles(canvases) })); // a page of 800 × 450
  // As in a browser, the canvas's client size follows its CSS size (the page's 800 × 450 without one).
  const css = (key: 'width' | 'height', fallback: number) => () => parseFloat(dom.canvas.style[key] ?? '') || fallback;
  Object.defineProperty(dom.canvas, 'clientWidth', { get: css('width', 800), configurable: true });
  Object.defineProperty(dom.canvas, 'clientHeight', { get: css('height', 450), configurable: true });
  game = new Game(dom.canvas as unknown as HTMLCanvasElement, { environment: 0, fillLight: 0, startScene: 'basic', exportBase: '/export/' });
  await game.physics.init();
  await game.loadUrl(BASIC);
  return game;
}

/** Real-time frames every 16 ms from `from` to `to` (ms). */
function frames(g: Game, from: number, to: number): void {
  const frame = (g as unknown as { frame: (now: number) => void }).frame;
  for (let t = from; t <= to; t += 16) frame(t);
}

/** Stands in for the renderer: records what each draw drew. */
function recorder(g: Game): Array<{ scene: Scene; camera: Camera }> {
  const draws: Array<{ scene: Scene; camera: Camera }> = [];
  g.three.renderer = {
    kind: 'webgl',
    raw: undefined,
    render(scene: Scene, camera: Camera) {
      scene.updateMatrixWorld();
      draws.push({ scene, camera });
    },
    setSize() {},
    dispose() {},
  } as never;
  return draws;
}

const layerOf = (g: Game): CanvasLayer & { menu: string | null; huds: string[]; hidden: boolean } =>
  (g.ui as unknown as { screens: { layer: CanvasLayer & { menu: string | null; huds: string[]; hidden: boolean } } }).screens.layer;
const drawn = (g: Game): { huds: string[]; menu: string | null } => ({ huds: [...layerOf(g).huds], menu: layerOf(g).menu });
const focusOf = (g: Game, name: string): string | null =>
  (layerOf(g) as unknown as { entries: Map<string, { model: { focusedId: string | null } }> }).entries.get(name)!.model.focusedId;
const store = (g: Game): Record<string, unknown> => (g as unknown as { ctx: SystemContext }).ctx.store;
const view = (g: Game, fields: Record<string, unknown>): void => handleEditorMessage(g, { t: 'view.mode', mode: 'ui', ...fields }, () => {});

describe('the ui view: what it is given', () => {
  it('takes a canvas and a frame, with an optional region and background', () => {
    expect(parseUiPreview({ canvas: 'Title', frame: [10, 20, 400, 225] })).toEqual({ canvas: 'Title', frame: [10, 20, 400, 225], region: null, background: 'level' });
    expect(parseUiPreview({ canvas: 'Title', frame: [10, 20, 400, 225, 9], region: [1000, 600], background: 'checker' })).toEqual({
      canvas: 'Title',
      frame: [10, 20, 400, 225],
      region: [1000, 600],
      background: 'checker',
    });
    expect(parseUiPreview({ canvas: 'Title', frame: [0, 0, 1, 1], region: [0, 600], background: 'sky' })).toMatchObject({ region: null, background: 'level' });
    for (const bad of [{ frame: [0, 0, 10, 10] }, { canvas: '', frame: [0, 0, 10, 10] }, { canvas: 'T', frame: [0, 0, 0, 10] }, { canvas: 'T', frame: [0, 0, 10] }, { canvas: 'T', frame: [0, NaN, 10, 10] }, { canvas: 'T', frame: 'x' }]) {
      expect(parseUiPreview(bad)).toBeNull();
    }
  });

  it('maps the frame from the region onto the page and lays out at the frame size', () => {
    const preview = parseUiPreview({ canvas: 'T', frame: [100, 50, 400, 225.4], region: [1000, 600] })!;
    expect(uiPlacement(preview, [500, 300])).toEqual({ left: 50, top: 25, width: 400, height: 225, scaleX: 0.5, scaleY: 0.5 });
    expect(uiPlacement(preview, null)).toEqual({ left: 100, top: 50, width: 400, height: 225, scaleX: 1, scaleY: 1 });
    expect(uiPlacement(parseUiPreview({ canvas: 'T', frame: [100, 50, 400, 225] })!, [500, 300])).toMatchObject({ left: 100, scaleX: 1 });
  });

  it('moves the canvas element there and puts its own style back', () => {
    const element = { style: { width: '100vw', transform: '' } as Record<string, string> };
    const placement = new CanvasPlacement(element as unknown as HTMLCanvasElement);
    expect(placement.place({ left: 50, top: 25.125, width: 400, height: 225, scaleX: 0.5, scaleY: 0.5 })).toBe(true);
    expect(element.style).toMatchObject({ position: 'fixed', left: '50px', top: '25.13px', width: '400px', height: '225px', transformOrigin: '0 0', transform: 'scale(0.5, 0.5)' });
    expect(placement.place({ left: 50, top: 25.125, width: 400, height: 225, scaleX: 0.5, scaleY: 0.5 })).toBe(false);
    expect(placement.place({ left: 50, top: 25, width: 400, height: 225, scaleX: 1, scaleY: 1 })).toBe(true);
    expect(element.style.transform).toBe('none');
    expect(placement.restore()).toBe(true);
    expect(element.style).toMatchObject({ position: '', width: '100vw', transform: '', left: '' });
    expect(placement.restore()).toBe(false);
  });

  it('renders bindings without a value as written, with their format', () => {
    const parts = parseBody('Score {score:000} of {best} {{x}}');
    expect(renderBody(parts, () => undefined)).toBe('Score  of  {x}');
    expect(renderBody(parts, () => undefined, true)).toBe('Score {score:000} of {best} {x}');
    expect(renderBody(parts, (p) => (p === 'score' ? 7 : null), true)).toBe('Score 007 of {best} {x}');
  });

  it('draws a checkerboard of 16 px squares', () => {
    const checker = new Checkerboard();
    const draws: Scene[] = [];
    checker.render({ render: (scene: Scene) => void draws.push(scene) } as never, 320, 64);
    expect(draws).toHaveLength(1);
    expect([checker.texture.repeat.x, checker.texture.repeat.y]).toEqual([10, 2]);
    checker.dispose();
  });
});

describe('the ui view in a game', () => {
  it('stops the game and draws one canvas, at rest, laid out at the frame where it is', async () => {
    const g = await open();
    const draws = recorder(g);
    frames(g, 1000, 1100);
    expect([g.state, g.ui.current, drawn(g)]).toEqual(['paused', 'Title', { huds: ['HUD'], menu: 'Title' }]);
    const views: string[] = [];
    g.onViewChange = (v) => void views.push(v);
    Object.assign(globalThis.window, { innerWidth: 500, innerHeight: 300 }); // the stream's page: half the region
    view(g, { canvas: 'Title', frame: [100, 50, 400, 225], region: [1000, 600], background: 'level' });
    await g.idle();
    expect([g.view, g.state, views, g.ui.current]).toEqual(['ui', 'stopped', ['ui'], null]);
    expect(g.uiPreview).toEqual({ canvas: 'Title', frame: [100, 50, 400, 225], region: [1000, 600], background: 'level' });
    expect(dom.canvas.style).toMatchObject({ position: 'fixed', left: '50px', top: '25px', width: '400px', height: '225px', transform: 'scale(0.5, 0.5)' });
    frames(g, 1200, 1300);
    // Only the previewed canvas, a menu over its backdrop; the HUD the game opened is not drawn.
    expect(drawn(g)).toEqual({ huds: [], menu: 'Title' });
    expect(layerOf(g).hidden).toBe(false);
    // Laid out at 400 × 225 (fit: half the design size): Play's centre at (200, 100) of the frame.
    const play = layerOf(g).buttons('Title').find((b) => b.name === 'Play')!;
    expect(play.rect.x + play.rect.width / 2).toBeCloseTo(200, 6);
    expect(play.rect.y + play.rect.height / 2).toBeCloseTo(100, 6);
    expect(play.rect.width).toBeCloseTo(100, 6);
    // The open animation is skipped; the loop runs. The menu shows its first focus, as it opens in the game.
    expect(layerOf(g).animationPhase('Title')).toBe('loop');
    expect(focusOf(g, 'Title')).toBe(play.id);
    // Nothing simulates; the level is drawn by the game's camera at the frame's aspect, then the canvas.
    expect(g.state).toBe('stopped');
    expect(draws.at(-1)!.scene).toBe(g.three.scene);
    expect(draws.at(-1)!.camera).toBe(g.three.camera);
    expect((g as unknown as { viewAspect: number }).viewAspect).toBeCloseTo(400 / 225, 6);
    // The DOM overlays stay hidden.
    expect([g.hud.element.style.visibility, g.ui.overlay.style.visibility]).toEqual(['hidden', 'hidden']);
  });

  it('shows bindings without a value as written, and values once they have one', async () => {
    const g = await open();
    recorder(g);
    view(g, { canvas: 'HUD', frame: [0, 0, 800, 450] });
    await g.idle();
    frames(g, 1000, 1100);
    expect(drawn(g)).toEqual({ huds: ['HUD'], menu: null });
    expect(layerOf(g).texts(['HUD'])).toEqual({ 'HUD/Score': 'Score {score:000}', 'HUD/Lives': 'Lives {lives}' });
    store(g).score = 7;
    frames(g, 1200, 1250);
    expect(layerOf(g).texts(['HUD'])).toEqual({ 'HUD/Score': 'Score 007', 'HUD/Lives': 'Lives {lives}' });
    // Back in the game, a missing value is empty again.
    g.setView('game');
    expect(layerOf(g).texts(['HUD'])['HUD/Lives']).toBe('Lives ');
  });

  it('takes no input and opens nothing; Play leaves it as from a stopped game', async () => {
    const g = await open();
    recorder(g);
    view(g, { canvas: 'Title', frame: [0, 0, 800, 450] });
    await g.idle();
    frames(g, 1000, 1100);
    window.dispatchEvent(new KeyboardEvent('keydown', { code: 'Enter', key: 'Enter' }));
    frames(g, 1116, 1200);
    expect([g.state, g.ui.current, g.ui.isOpen(), drawn(g)]).toEqual(['stopped', null, false, { huds: [], menu: 'Title' }]);
    expect(g.test.state({ ids: [] }).ui.open).toEqual([]);
    handleEditorMessage(g, { t: 'play.start' }, () => {});
    expect([g.view, g.uiPreview, dom.canvas.style.position]).toEqual(['game', null, '']);
    frames(g, 1300, 1400);
    expect([g.state, g.ui.current, drawn(g)]).toEqual(['paused', 'Title', { huds: ['HUD'], menu: 'Title' }]);
  });

  it('follows the editor: a new frame, another canvas, live edits; a menu without a loop rests', async () => {
    const g = await open();
    recorder(g);
    view(g, { canvas: 'Over', frame: [0, 0, 800, 450] });
    await g.idle();
    frames(g, 1000, 1050);
    expect([drawn(g), layerOf(g).animationPhase('Over')]).toEqual([{ huds: [], menu: 'Over' }, null]);
    view(g, { canvas: 'Over', frame: [20, 10, 400, 225] });
    expect(dom.canvas.style).toMatchObject({ left: '20px', top: '10px', width: '400px', transform: 'none' });
    view(g, { canvas: 'HUD', frame: [20, 10, 400, 225] });
    frames(g, 1100, 1150);
    expect(drawn(g)).toEqual({ huds: ['HUD'], menu: null });
    // A live edit (ui.canvas) of the previewed canvas shows at once.
    const edited = structuredClone(HUD);
    edited.elements[0]!.text!.body = 'Points {score}';
    handleEditorMessage(g, { t: 'ui.canvas', canvas: 'HUD', data: edited }, () => {});
    await g.ui.afterLoad(() => {});
    frames(g, 1200, 1250);
    expect(layerOf(g).texts(['HUD'])['HUD/Score']).toBe('Points {score}');
    // A screen exported since the page loaded: a new canvas fetches the index again, and shows once it is in.
    const fresh = canvas('Fresh', {}, [text('Hello', 'Hello {name}', [400, 200])]);
    await g.ui.refresh(); // the fetch the HUD's view started, done
    Object.assign(served, uiFiles([Title, HUD, Over, fresh]));
    view(g, { canvas: 'Fresh', frame: [20, 10, 400, 225] });
    expect(layerOf(g).has('Fresh')).toBe(false);
    await g.ui.refresh();
    frames(g, 1300, 1350);
    expect([drawn(g), layerOf(g).has('Fresh'), layerOf(g).texts(['Fresh'])]).toEqual([{ huds: [], menu: 'Fresh' }, true, { 'Fresh/Hello': 'Hello {name}' }]);
  });

  it('draws a checkerboard when asked, or with no level loaded', async () => {
    const g = await open();
    const draws = recorder(g);
    view(g, { canvas: 'Title', frame: [0, 0, 320, 180], background: 'checker' });
    await g.idle();
    frames(g, 1000, 1016);
    const checker = draws.at(-1)!;
    expect(checker.scene).not.toBe(g.three.scene);
    expect((g as unknown as { checker: Checkerboard }).checker.texture.repeat.x).toBe(320 / 32);
    view(g, { canvas: 'Title', frame: [0, 0, 320, 180], background: 'level' });
    frames(g, 1100, 1116);
    expect(draws.at(-1)!.scene).toBe(g.three.scene);
    // A page whose level never loaded (no export): the checkerboard.
    game!.dispose();
    dom.restore();
    dom = installDom(uiFiles([Title]));
    game = new Game(dom.canvas as unknown as HTMLCanvasElement, { environment: 0, fillLight: 0, startScene: 'none', exportBase: '/export/' });
    const empty = recorder(game);
    game.setView('ui', { canvas: 'Title', frame: [0, 0, 320, 180] });
    frames(game, 1000, 1016);
    expect(empty.at(-1)!.scene).not.toBe(game.three.scene);
  });

  it('hides the selection, ignores an unusable frame, and other views clear it', async () => {
    const g = await open();
    recorder(g);
    g.select(['gs_000000a00006']);
    const selection = (g as unknown as { selection: { helpers: unknown[] } }).selection;
    expect(selection.helpers).toHaveLength(1);
    view(g, { canvas: 'Title', frame: [0, 0, 400, 225] });
    await g.idle();
    expect(selection.helpers).toHaveLength(0);
    g.select(['gs_000000a00006']);
    expect(selection.helpers).toHaveLength(0);
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
    view(g, { canvas: 'Title', frame: [0, 0, 0, 225] });
    expect([g.view, g.uiPreview?.frame]).toEqual(['ui', [0, 0, 400, 225]]);
    expect(warn.mock.calls.some((call) => String(call[0]).includes('setView("ui") needs a canvas name and a frame'))).toBe(true);
    handleEditorMessage(g, { t: 'view.mode', mode: 'scene' }, () => {});
    expect([g.view, g.uiPreview, dom.canvas.style.position, dom.canvas.style.width, layerOf(g).hidden]).toEqual(['scene', null, '', '', true]);
    frames(g, 1000, 1050);
    expect(drawn(g)).toEqual({ huds: [], menu: null });
    expect(selection.helpers).toHaveLength(0); // outlines in the Scene view
    expect((selection as unknown as { outlines: unknown[] }).outlines.length).toBeGreaterThan(0);
  });

  it('is ignored while a test holds the clock, like the other view messages', async () => {
    const g = await open();
    recorder(g);
    g.test.hold();
    view(g, { canvas: 'Title', frame: [0, 0, 400, 225] });
    expect(g.view).toBe('game');
  });
});
