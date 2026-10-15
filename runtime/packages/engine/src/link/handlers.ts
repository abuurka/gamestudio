// Editor → game messages (docs/SPEC.md §5.4). Unknown types are ignored.

import type { Game, ViewOptions } from '../game/game';
import type { ComponentSet, TransformItem } from '../game/edits';
import type { StateFilter } from '../game/describe';
import type { ViewCamera } from '../game/editor-camera';
import type { LinkMessage } from './client';

const asArray = <T>(value: unknown): T[] => (Array.isArray(value) ? (value as T[]) : []);

/** Edits that name a scene (`scene`) only apply while the game shows that scene. */
const SCENE_EDITS = new Set(['entity.transform', 'entity.components', 'entity.remove', 'entity.visibility', 'select', 'material.values']);

export function handleEditorMessage(game: Game, msg: LinkMessage, send: (reply: LinkMessage) => void): void {
  if (SCENE_EDITS.has(msg.t) && typeof msg.scene === 'string' && msg.scene !== game.scene.name) return;
  switch (msg.t) {
    case 'scene.load': {
      const url = typeof msg.url === 'string' && msg.url ? msg.url : game.sceneUrl(String(msg.scene ?? game.scene.name));
      // A page kept warm between plays also picks up menus and HUDs exported since it loaded (GAME-UI §5.1).
      if (game.ready) void game.ui.refresh();
      void game.loadUrl(url);
      break;
    }
    // While a test holds the clock (game.test), it alone drives time: play controls are ignored.
    case 'play.start':
      if (!game.test.holding) {
        if (game.ready) void game.ui.refresh(); // never waits while nothing changed
        game.play();
      }
      break;
    case 'play.stop':
      if (!game.test.holding) game.stop();
      break;
    case 'play.pause':
      if (!game.test.holding) game.pause(msg.paused !== false);
      break;
    case 'play.step':
      if (!game.test.holding) game.step(typeof msg.frames === 'number' ? msg.frames : 1);
      break;
    // The Scene view, and its 'ui' view of a UI file (GAME-UI §11.2). Switching it stops a running game:
    // like play controls, ignored under a test's clock.
    case 'view.mode':
      if (!game.test.holding && (msg.mode === 'scene' || msg.mode === 'game' || msg.mode === 'ui')) {
        game.setView(msg.mode, {
          ...(typeof msg.animate === 'boolean' && { animate: msg.animate }),
          ...(msg.mode === 'ui' && ({ canvas: msg.canvas, frame: msg.frame, region: msg.region, background: msg.background } as ViewOptions)),
        });
      }
      break;
    case 'view.camera':
      game.setViewCamera(msg as unknown as ViewCamera);
      break;
    case 'material.values':
      if (typeof msg.material === 'string' && msg.values && typeof msg.values === 'object') {
        game.setMaterialValues(msg.material, msg.values as Record<string, unknown>);
      }
      break;
    case 'entity.transform':
      game.applyTransforms(asArray<TransformItem>(msg.items));
      break;
    case 'entity.components':
      if (typeof msg.id === 'string' && msg.components && typeof msg.components === 'object') {
        game.replaceComponents(msg.id, msg.components as ComponentSet);
      }
      break;
    case 'entity.remove':
      game.removeEntities(asArray<string>(msg.ids).filter((id) => typeof id === 'string'));
      break;
    case 'entity.visibility':
      if (typeof msg.id === 'string') game.setVisibility(msg.id, msg.visible !== false);
      break;
    case 'select':
      game.select(
        asArray<string>(msg.ids).filter((id) => typeof id === 'string'),
        typeof msg.active === 'string' ? msg.active : null,
      );
      break;
    // Game UI (docs/GAME-UI.md §4): applied once the game's canvases have loaded, in order.
    case 'ui.canvas':
      void game.ui.setCanvas(msg.data);
      break;
    case 'ui.remove':
      if (typeof msg.canvas === 'string') void game.ui.removeCanvas(msg.canvas);
      break;
    case 'ui.open':
      if (typeof msg.canvas === 'string') {
        const name = msg.canvas;
        void game.ui.afterLoad(() => game.ui.open(name));
      }
      break;
    case 'ui.close': {
      const name = typeof msg.canvas === 'string' && msg.canvas ? msg.canvas : undefined;
      void game.ui.afterLoad(() => game.ui.close(name));
      break;
    }
    // Translations (GAME-UI §11.3): the index's sourceLocale and locales, with every table inline.
    case 'ui.locales':
      void game.ui.setLocales({ sourceLocale: msg.sourceLocale, locales: msg.locales, tables: msg.tables });
      break;
    case 'state.request': {
      const filter = (msg.filter && typeof msg.filter === 'object' ? msg.filter : {}) as StateFilter;
      send({
        t: 'state.response',
        re: msg.id,
        ...(typeof msg.from === 'string' && { to: msg.from }),
        entities: game.describe(filter),
      });
      break;
    }
    default:
      break; // Unknown message types are ignored (SPEC §5.4).
  }
}
