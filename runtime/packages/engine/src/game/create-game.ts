// createGame(): builds a Game for a canvas, connects the dev live link (when the
// dev server provides a session) and exposes `window.__gs` for tests.

import type { GameState, GameView } from '../context';
import type { World } from '../ecs/world';
import { ErrorOverlay } from '../hud/error-overlay';
import { LinkClient, type LinkMessage } from '../link/client';
import { captureConsole, captureErrors, originalConsole, toErrorMessage, type ErrorMessage } from '../link/capture';
import { handleEditorMessage } from '../link/handlers';
import { linkSession } from '../link/session';
import { SceneNotFoundError } from '../scene/loader';
import { Game, type GameOptions } from './game';
import type { TestControl } from './test-control';

/** Debug handle for tests and the console. */
export interface DebugHandle {
  world: World;
  game: Game;
  /** Deterministic stepping (`game.test`). */
  test: TestControl;
  readonly ready: boolean;
  readonly state: GameState;
  /** 'game', 'scene' in the Scene view, or 'ui' in the Scene view of a UI file. */
  readonly view: GameView;
  /** Handles an editor message as if it came over the live link (SPEC §5.4); returns the replies. */
  message(message: LinkMessage): LinkMessage[];
}

declare global {
  interface Window {
    __gs?: DebugHandle;
    /** Set before the page loads (e.g. puppeteer's evaluateOnNewDocument) to start with the test clock held. */
    __gsTestHold?: boolean;
  }
}

const ENGINE_CLIENT = 'gamestudio-engine 0.1.0';
const LOGS_PER_SECOND = 100;
const ERROR_REPEAT_MS = 2000;

function resolveCanvas(canvas: string | HTMLCanvasElement): HTMLCanvasElement {
  if (canvas instanceof HTMLCanvasElement) return canvas;
  const found = document.querySelector(canvas);
  if (found instanceof HTMLCanvasElement) return found;
  throw new Error(`createGame: no <canvas> matches "${canvas}"`);
}

/**
 * What the page URL asks for: `?scene=<name>` (the first scene) and `?gs_view=scene` (start in the Scene
 * view). `remembered` is the view this tab was in when it last unloaded (`rememberedView()`): it wins over
 * the URL, so a page reloaded during Play (Vite reloads it when a non-hot file changes) plays again, while
 * one reloaded in the Scene view stays there.
 */
export function pageOptions(search: string, remembered: string | null = null): { scene: string | null; view: GameView | null } {
  const params = new URLSearchParams(search);
  const view = remembered === 'scene' || remembered === 'game' ? remembered : params.get('gs_view') === 'scene' ? 'scene' : null;
  return { scene: params.get('scene'), view };
}

const VIEW_KEY = 'gs_view';

/** The view this tab last showed (sessionStorage survives reloads, not new tabs); null if unknown. */
export function rememberedView(): string | null {
  try {
    return typeof sessionStorage === 'undefined' ? null : sessionStorage.getItem(VIEW_KEY);
  } catch {
    return null; // storage blocked: the URL decides
  }
}

function rememberView(view: GameView): void {
  try {
    if (typeof sessionStorage !== 'undefined') sessionStorage.setItem(VIEW_KEY, view);
  } catch {
    // storage blocked: a reload goes by the URL
  }
}

/** Creates and starts a game. Returns immediately; the first scene loads in the background. */
export function createGame(options: GameOptions = {}): Game {
  const canvas = resolveCanvas(options.canvas ?? '#game');
  const page = pageOptions(typeof location === 'undefined' ? '' : (location.search ?? ''), rememberedView());
  const startScene = page.scene ?? options.startScene ?? linkSession.startScene ?? 'main';
  const game = new Game(canvas, {
    ...options,
    startScene,
    view: page.view ?? options.view,
    name: options.name ?? linkSession.name,
    post: { ...(linkSession.post ?? {}), ...options.post },
  });
  // A playtest harness asked for the clock before anything ran: nothing steps until it says so.
  if (window.__gsTestHold) game.test.hold();

  const dev = import.meta.env.DEV;
  const overlay = dev ? new ErrorOverlay() : null;
  const link =
    dev && linkSession.token && linkSession.gamePort
      ? new LinkClient({
          port: linkSession.gamePort,
          token: linkSession.token,
          client: `${ENGINE_CLIENT} (${navigator.userAgent})`,
          onMessage: (msg) => handleEditorMessage(game, msg, (reply) => link!.send(reply)),
          onConnect: () => {
            link!.send({ t: 'game.status', state: game.state, view: game.view });
            if (game.ready) link!.send({ t: 'game.ready', scene: game.scene.name, rev: game.scene.rev });
          },
        })
      : null;

  // Errors go to the overlay (dev, every occurrence counted) and, at most once per
  // ERROR_REPEAT_MS per distinct error, to the link and the console: a system that
  // throws every frame must not flood the editor or the terminal.
  const lastReported = new Map<string, number>();
  const firstInWindow = (message: ErrorMessage): boolean => {
    const key = `${message.msg}|${message.file}|${message.line}`;
    const now = performance.now();
    if (now - (lastReported.get(key) ?? -Infinity) < ERROR_REPEAT_MS) return false;
    lastReported.set(key, now);
    return true;
  };
  game.reportError = (error, context) => {
    if (error instanceof SceneNotFoundError) {
      game.hud.set('gs_status', `Scene not found: ${error.url}\nExport it from Blender to see it here.`, {
        position: 'center',
      });
    }
    const message = toErrorMessage(error);
    overlay?.add(message);
    if (!firstInWindow(message)) return;
    originalConsole.error(`[gamestudio] ${context}:`, error);
    link?.send(message);
  };
  // Uncaught errors: the browser already printed them.
  captureErrors((message) => {
    overlay?.add(message);
    if (firstInWindow(message)) link?.send(message);
  });

  if (link) {
    let budget = LOGS_PER_SECOND;
    let dropped = 0;
    setInterval(() => {
      if (dropped > 0) link.send({ t: 'log', level: 'warn', msg: `[gamestudio] ${dropped} log messages dropped (rate limit)` });
      budget = LOGS_PER_SECOND;
      dropped = 0;
    }, 1000);
    captureConsole((message) => {
      if (budget-- > 0) link.send(message);
      else dropped++;
    });
    game.onStateChange = (state) => link.send({ t: 'game.status', state, view: game.view });
    game.onViewChange = (view) => link.send({ t: 'game.status', state: game.state, view });
    game.onSceneReady = (scene) => {
      game.hud.clear('gs_status');
      link.send({ t: 'game.ready', scene: scene.name, rev: scene.rev });
    };
    game.onStats = (stats) => {
      if (link.connected) link.send({ t: 'stats', ...stats });
    };
    link.connect();
  } else {
    game.onSceneReady = () => game.hud.clear('gs_status');
  }
  // The tab remembers its view, so a reload comes back in the same one (see pageOptions).
  rememberView(game.view);
  const notifyView = game.onViewChange;
  game.onViewChange = (view) => {
    rememberView(view);
    notifyView(view);
  };

  window.__gs = {
    world: game.world,
    game,
    test: game.test,
    get ready() {
      return game.ready;
    },
    get state() {
      return game.state;
    },
    get view() {
      return game.view;
    },
    message(message) {
      const replies: LinkMessage[] = [];
      handleEditorMessage(game, message, (reply) => void replies.push(reply));
      return replies;
    },
  };

  game.start().catch((error) => game.reportError(error, 'startup'));
  return game;
}
