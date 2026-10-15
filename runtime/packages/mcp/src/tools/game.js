// @ts-check
// SPDX-License-Identifier: MIT
// Game tools: export, play controls, live state and logs, scripted playtests (on game time), screenshots.

import { existsSync, readFileSync } from 'node:fs';
import path from 'node:path';
import { MAX_DURATION_S, UI_TIMEOUTS, planPlaytest, uiStateInPage } from '../browser.js';
import { withImages } from '../protocol.js';
import { sceneScreenshot } from '../sceneview.js';
import { ToolError, sleep } from '../util.js';
import { READ, VEC3, WRITE, object } from './common.js';

/** @typedef {import('./common.js').Context} Context */
/** @typedef {import('../hub.js').GameInfo} GameInfo */

const GAME_READY_TIMEOUT_MS = 60_000;
const START = object({
  name: { type: 'string', minLength: 1, description: 'Entity name or id. Default: the player (the first entity with CharacterController).' },
  position: { ...VEC3, description: 'World position: runtime space (Y-up), or Blender space with "space": "blender".' },
  yaw_deg: { type: 'number', description: 'Facing: degrees about the up axis (same in both spaces).' },
  rotation: { type: 'array', items: { type: 'number' }, minItems: 4, maxItems: 4, description: 'Quaternion [x, y, z, w], runtime space.' },
  space: { type: 'string', enum: ['runtime', 'blender'] },
});

/** @param {GameInfo} g @param {Context} ctx */
function describeGame(g, ctx) {
  return {
    peer: g.peer,
    state: g.state,
    scene: g.scene,
    rev: g.rev,
    fps: g.stats?.fps ?? null,
    headless: ctx.browser.game ? g.client.includes(ctx.browser.game.tag) : false,
  };
}

/** @param {Context} ctx */
async function requireGames(ctx) {
  await ctx.hub.ensure();
  const games = ctx.hub.gamePeers();
  if (games.length === 0) {
    throw new ToolError('No game is connected to the dev server.', { code: 'no_game', hint: 'Call play: it opens a headless game when none is running.' });
  }
  return games;
}

/**
 * The game a query goes to: the one asked for, else a playing one, else the newest ready one.
 * @param {GameInfo[]} games
 * @param {string | undefined} peer
 */
function pickGame(games, peer) {
  if (peer) {
    const found = games.find((g) => g.peer === peer);
    if (!found) throw new ToolError(`No game peer '${peer}'.`, { code: 'no_game', hint: `Connected games: ${games.map((g) => g.peer).join(', ')}` });
    return found;
  }
  return games.find((g) => g.state === 'playing') ?? [...games].sort((a, b) => (b.readyAt ?? 0) - (a.readyAt ?? 0))[0] ?? games[0];
}

/**
 * Sends a play-control message to every connected game and waits for the expected state.
 * @param {Context} ctx
 * @param {Record<string, unknown>} msg
 * @param {(g: GameInfo) => boolean} [settled]
 */
async function control(ctx, msg, settled) {
  const games = await requireGames(ctx);
  for (const g of games) ctx.hub.send({ ...msg, to: g.peer });
  if (settled) await ctx.hub.waitFor(() => ctx.hub.gamePeers().every(settled), 3000);
  else await sleep(150);
  return { sent: msg.t, games: ctx.hub.gamePeers().map((g) => describeGame(g, ctx)) };
}

const FORCE = {
  type: 'boolean',
  description: 'Export everything again, even what did not change since the last export (tier "full"). Default false.',
};

/** game_screenshot and playtest `set`: ctx.store and ctx.save values the game starts from. */
const SET_VALUES = object({
  store: { type: 'object', description: 'ctx.store values by key, e.g. {"coins": 7, "lives": 1}: what HUD bindings, bars and UiShow read.' },
  save: { type: 'object', description: 'ctx.save values by key, e.g. {"bestTime": 42.5, "subtitles": true}: saved settings and records ({save.bestTime}, "save.subtitles" toggles).' },
});

/**
 * The open animation of a UI canvas in seconds, from its export (.gamestudio/export/ui/<canvas>.ui.json,
 * docs/GAME-UI.md §3.2, §10.5); 0 when it has none or the file is missing.
 * @param {string | null | undefined} root @param {string} canvas
 */
export function exportedOpenSeconds(root, canvas) {
  if (!root) return 0;
  try {
    const data = JSON.parse(readFileSync(path.join(root, '.gamestudio', 'export', 'ui', `${canvas}.ui.json`), 'utf8'));
    const seconds = data?.animations?.open?.duration;
    return typeof seconds === 'number' && Number.isFinite(seconds) && seconds > 0 ? seconds : 0;
  } catch {
    return 0;
  }
}

/** @param {Context} ctx @param {AbortSignal} signal @param {boolean} [force] */
async function exportScene(ctx, signal, force = false) {
  return ctx.blender.call('export_scene', { force }, { signal });
}

/**
 * What an export result says about the export, for tool results: `tier` is how much was written
 * ("reuse": nothing changed, same rev; "layout": only the manifest, the GLBs were kept; "full", with
 * the `reason`).
 * @param {any} result
 */
function exportSummary(result) {
  return {
    scene: result.scene,
    rev: result.rev,
    entities: result.entities,
    ...(result.tier && { tier: result.tier }),
    ...(result.reason && { reason: result.reason }),
  };
}

/**
 * Does this game show the export (or a newer one of the same scene)? A `reuse` export keeps the rev
 * the game may already show; after a scene.load sent at `sentAt`, the load that follows counts too.
 * @param {GameInfo} g
 * @param {{ scene?: string, rev?: number } | null} want
 * @param {number | undefined} [sentAt]
 */
function showsExport(g, want, sentAt) {
  if (!want || want.rev === undefined) return true;
  if (want.scene !== undefined && g.scene !== want.scene) return false;
  return (g.rev !== null && g.rev >= want.rev) || (sentAt !== undefined && (g.readyAt ?? 0) >= sentAt);
}

/** @param {Context} ctx @returns {import('./common.js').ToolDefinition[]} */
export function gameTools(ctx) {
  return [
    {
      name: 'export_scene',
      title: 'Export scene',
      description:
        'Export the open scene to .gamestudio/export/<scene>.glb + .scene.json (what the game loads). play and playtest ' +
        'export by themselves; call this to check the export or to update games without restarting them (connected games ' +
        'reload the scene). The result reports size (bytes, the largest items), warnings, baked materials (bake.baked / ' +
        'bake.cached), downscaled or converted textures, GPU instancing and hidden meshes; verbose: true gives the full ' +
        'export report. all: true exports every level instead, for games that change level (ctx.loadScene): the open ' +
        'file\'s scenes here and every other scenes/*.blend in a background Blender (a few seconds each; those files are ' +
        'only read, never changed). It returns scenes: [{file, scene, rev, entities}] and errors per file, and each ' +
        'connected game reloads the level it shows. compress: true also reports what npm run build ships: size.compressed ' +
        'has each GLB before and after the meshopt geometry compression of the build (a dry run; the export stays uncompressed). ' +
        'tier says how much was written: "reuse" (nothing changed since the last export: same files, same rev), ' +
        '"layout" (only objects moved: the GLBs are kept and the manifest has their new poses, so games reload without ' +
        'downloading or parsing them again) or "full" (with the reason). force: true exports everything again ' +
        '(the open scene only, not with all).',
      inputSchema: object({
        all: { type: 'boolean', description: 'Export every level (scenes/*.blend), not just the open scene. Default false.' },
        compress: { type: 'boolean', description: 'Also measure the GLB sizes npm run build ships (meshopt geometry compression, a dry run). Default false.' },
        reload_games: { type: 'boolean', description: 'Send scene.load to connected games. Default true.' },
        verbose: { type: 'boolean', description: 'The full export report. Default false.' },
        force: FORCE,
      }),
      annotations: WRITE,
      handler: async (args, call) => {
        if (args.all) {
          if (args.verbose) throw new ToolError('verbose goes with a single export, not all.', { code: 'invalid_arguments', hint: 'Export the open scene with verbose: true for its full report.' });
          if (args.force) throw new ToolError('force goes with a single export, not all.', { code: 'invalid_arguments', hint: 'Open the level and export_scene {"force": true} it.' });
          const result = await ctx.blender.call('export_all', {}, { signal: call.signal });
          const { scene_loads: loads, ...out } = result;
          if (args.reload_games !== false && (await ctx.devSession())) {
            await ctx.hub.ensure().catch(() => {});
            const games = ctx.hub.connected ? ctx.hub.gamePeers() : [];
            /** @type {string[]} */
            const reloaded = [];
            for (const g of games) {
              const load = loads.find((/** @type {any} */ l) => l.scene === g.scene);
              if (!load) continue;
              ctx.hub.send({ ...load, to: g.peer });
              reloaded.push(g.peer);
            }
            out.reloaded_games = reloaded;
          }
          return out;
        }
        const result = await ctx.blender.call(
          'export_scene',
          { verbose: args.verbose === true, compress: args.compress === true, force: args.force === true },
          { signal: call.signal },
        );
        // The bridge already returns the compact report (or everything with verbose).
        const { scene_load: _sceneLoad, ...report } = result;
        /** @type {Record<string, any>} */
        const out = { ...(result.tier && { tier: result.tier }), ...report };
        if (args.reload_games !== false && (await ctx.devSession())) {
          await ctx.hub.ensure().catch(() => {});
          const games = ctx.hub.connected ? ctx.hub.gamePeers() : [];
          for (const g of games) ctx.hub.send({ ...result.scene_load, to: g.peer });
          out.reloaded_games = games.map((g) => g.peer);
        }
        return out;
      },
    },
    {
      name: 'play',
      title: 'Play',
      description:
        'Run the game: starts the dev server if needed (through Blender), exports the scene, then loads and starts it in ' +
        'every connected game. where: "auto" (default) uses the open games, or opens a headless one when none is open; ' +
        '"headless" always uses this server\'s headless game (what game_screenshot shows); "editor" uses Blender\'s Play ' +
        '(Game View or a game window the developer can see). export: false plays the last export. size: [width, height] ' +
        'of the headless game, default [480, 270] (larger sizes run slower in the software renderer); it is kept until ' +
        'changed. export.tier in the result says how much the export wrote ("reuse" when nothing changed); force: true ' +
        'exports everything again.',
      inputSchema: object({
        where: { type: 'string', enum: ['auto', 'headless', 'editor'] },
        export: { type: 'boolean' },
        force: FORCE,
        size: { type: 'array', items: { type: 'integer', minimum: 64, maximum: 1920 }, minItems: 2, maxItems: 2, description: '[width, height] of the headless game page, default [480, 270].' },
      }),
      annotations: WRITE,
      handler: async (args, call) => {
        const where = args.where ?? 'auto';
        const dev = await ctx.ensureDevServer(call.signal);
        await ctx.hub.ensure();
        /** @type {any} */
        let exported = null;
        if (where === 'editor') {
          const before = new Set(ctx.hub.gamePeers().map((g) => g.peer));
          const sentAt = Date.now(); // Blender sends scene.load and play.start itself
          const result = await ctx.blender.call('play_in_blender', { open_window: true, force: args.force === true }, { signal: call.signal });
          const want = result.export ?? null;
          const tier = want?.tier ?? result.tier;
          const settled = await ctx.hub.waitFor(() => ctx.hub.gamePeers().some((g) => g.state === 'playing' && showsExport(g, want, sentAt)), GAME_READY_TIMEOUT_MS);
          return {
            where,
            url: dev.url,
            export: { ...want, ...(tier && { tier }) },
            games: ctx.hub.gamePeers().map((g) => ({ ...describeGame(g, ctx), new: !before.has(g.peer) })),
            ...(!settled && { warning: 'No game reported playing the new export yet; check game_logs.' }),
          };
        }
        if (args.export !== false) exported = await exportScene(ctx, call.signal, args.force === true);
        /** @type {string | null} */
        let opened = null;
        /** @type {[number, number] | null} */
        let headlessSize = null;
        if (where === 'headless' || ctx.hub.gamePeers().length === 0 || (args.size && ctx.browser.game)) {
          const game = await ctx.browser.ensureGame(dev.url, { size: args.size });
          headlessSize = game.size;
          if (game.opened) opened = game.tag;
          const tag = game.tag;
          const ok = await ctx.hub.waitFor(() => ctx.hub.gamePeers().some((g) => g.client.includes(tag)), 15_000);
          if (!ok) throw new ToolError('The headless game loaded but did not connect to the live link.', { code: 'game_not_connected', hint: 'Call status; restart the dev server if this persists.' });
        }
        const games = ctx.hub.gamePeers();
        /** @type {Map<string, number>} when each game was sent the export's scene.load */
        const sent = new Map();
        for (const g of games) {
          // A game already showing this export (a "reuse" export keeps the rev) only needs play.start.
          if (exported && !showsExport(g, exported)) {
            sent.set(g.peer, Date.now());
            ctx.hub.send({ ...exported.scene_load, to: g.peer });
          }
          ctx.hub.send({ t: 'play.start', to: g.peer });
        }
        const settled = await ctx.hub.waitFor(
          () => ctx.hub.gamePeers().every((g) => g.state === 'playing' && showsExport(g, exported, sent.get(g.peer))),
          GAME_READY_TIMEOUT_MS,
        );
        const out = {
          where,
          url: dev.url,
          ...(exported && { export: exportSummary(exported) }),
          headless_opened: Boolean(opened),
          ...(headlessSize && { headless_size: headlessSize }),
          games: ctx.hub.gamePeers().map((g) => describeGame(g, ctx)),
        };
        if (!settled) return { ...out, warning: 'Not every game reported playing the new export yet; check game_logs.' };
        return out;
      },
    },
    {
      name: 'stop',
      title: 'Stop',
      description: 'Stop every connected game: reset to the loaded scene and halt systems and physics (play restarts it).',
      inputSchema: object({}),
      annotations: WRITE,
      handler: () => control(ctx, { t: 'play.stop' }, (g) => g.state === 'stopped'),
    },
    {
      name: 'pause',
      title: 'Pause',
      description: 'Freeze (paused: true, default) or resume (paused: false) simulation time in every connected game.',
      inputSchema: object({ paused: { type: 'boolean' } }),
      annotations: WRITE,
      handler: (args) => {
        const paused = args.paused ?? true;
        return control(ctx, { t: 'play.pause', paused }, (g) => g.state === (paused ? 'paused' : 'playing') || g.state === 'stopped');
      },
    },
    {
      name: 'step',
      title: 'Step',
      description: 'Advance paused games by N fixed steps (pauses them first). Follow with game_state to see the result.',
      inputSchema: object({ frames: { type: 'integer', minimum: 1, maximum: 600 } }),
      annotations: WRITE,
      handler: async (args) => {
        const result = await control(ctx, { t: 'play.step', frames: args.frames ?? 1 });
        await sleep(Math.min(2000, (args.frames ?? 1) * 20));
        return result;
      },
    },
    {
      name: 'game_state',
      title: 'Game state',
      description:
        'Live entity state from a running game: world position and rotation (runtime space: Y-up, metres; quaternion ' +
        '[x, y, z, w]), visibility and component values, and for animated entities `animation` (the clip, or the animation ' +
        "graph's state and parameters). Filter by names, gs ids or component; limit defaults to 50. Queries the playing game (or peer).",
      inputSchema: object({
        names: { type: 'array', items: { type: 'string' }, minItems: 1 },
        ids: { type: 'array', items: { type: 'string' }, minItems: 1 },
        component: { type: 'string' },
        limit: { type: 'integer', minimum: 1, maximum: 2000 },
        peer: { type: 'string' },
        verbose: { type: 'boolean', description: 'Also rotation and visibility. Default false.' },
      }),
      annotations: READ,
      handler: async (args) => {
        const game = pickGame(await requireGames(ctx), args.peer);
        const limit = args.limit ?? 50;
        /** @type {Record<string, unknown>} */
        const filter = { limit: args.names ? 2000 : limit };
        if (args.ids) filter.ids = args.ids;
        if (args.component) filter.component = args.component;
        const reply = await ctx.hub.request({ t: 'state.request', to: game.peer, filter }, 5000);
        if (!reply) throw new ToolError('The game did not answer the state request within 5 s.', { code: 'game_timeout', hint: 'It may be loading or frozen: check game_logs, or play again.' });
        /** @type {any[]} */
        let entities = reply.entities ?? [];
        if (!args.verbose) {
          entities = entities.map((e) => ({
            name: e.name,
            id: e.id,
            position: e.position,
            ...(e.visible === false && { visible: false }),
            ...(e.components && Object.keys(e.components).length && { components: e.components }),
            ...(e.animation && { animation: e.animation }),
          }));
        }
        if (args.names) {
          const wanted = new Set(args.names);
          entities = entities.filter((e) => wanted.has(e.name)).slice(0, limit);
          const missing = args.names.filter((/** @type {string} */ n) => !entities.some((e) => e.name === n));
          if (missing.length) return { game: describeGame(game, ctx), space: 'runtime (Y-up, metres)', entities, missing };
        }
        return { game: describeGame(game, ctx), space: 'runtime (Y-up, metres)', count: entities.length, entities };
      },
    },
    {
      name: 'game_logs',
      title: 'Game logs',
      description:
        'Console output, errors (with file and line) and build errors from running games, oldest first. Pass since = the ' +
        '"next" value of the previous call to get only new lines. level: all (default), info, warn or error (that level ' +
        'and above).',
      inputSchema: object({
        since: { type: 'integer', minimum: 0 },
        level: { type: 'string', enum: ['all', 'debug', 'info', 'warn', 'error'] },
        limit: { type: 'integer', minimum: 1, maximum: 2000 },
      }),
      annotations: READ,
      handler: async (args) => {
        if (!ctx.hub.connected && (await ctx.devSession())) await ctx.hub.ensure().catch(() => {});
        const order = ['debug', 'info', 'warn', 'error'];
        const min = args.level && args.level !== 'all' ? order.indexOf(args.level) : -1;
        const since = args.since ?? 0;
        const limit = args.limit ?? 200;
        const matching = ctx.hub.logs.filter((l) => l.seq > since && order.indexOf(l.level) >= min);
        return {
          next: ctx.hub.seq,
          count: matching.length,
          truncated: matching.length > limit,
          logs: matching.slice(-limit),
          ...(!ctx.hub.connected && { note: 'Not connected to the dev server: only logs received earlier are shown.' }),
        };
      },
    },
    {
      name: 'playtest',
      title: 'Playtest',
      description:
        'Play the game in a fresh headless page with a scripted input timeline and see what happened. It runs on game ' +
        'time: the engine steps its fixed 60 Hz simulation as fast as it can and draws only the screenshots, so the same ' +
        'script gives the same result on every run, at any size and machine speed. All times are game seconds. Each input ' +
        'has "at" and a key or mouse action: {"at": 0, "key": "KeyW", "hold": 2} (down at 0, up at 2), {"at": 1, "key": ' +
        '"Space"} (a press: down, then up one step later; action: "down" | "up" | "press"), {"at": 1.5, "mouse": "click", ' +
        '"x": 240, "y": 135} (mouse: "move" | "down" | "up" | "click" | "wheel"; x/y are canvas pixels, dx/dy relative ' +
        'movement or wheel delta). key is a KeyboardEvent.code (KeyW, Space, ArrowUp, ShiftLeft; single letters work too). ' +
        'Closed-loop aim for cursor-aimed games: {"at": 10, "mouse": "aim", "target": "Grunt*", "pick": "nearest", "until": ' +
        '25} puts the pointer on the nearest living match every step (projected through the game camera; clicks then hit ' +
        'it), and {"at": 2, "mouse": "move", "world": [x, y, z]} on a world point. ' +
        'duration: default the last input + 0.5, max 120. screenshots: times or a count (default [0, duration]); each lists ' +
        'the tracked positions at that time. track: entity names whose start/final/min/max position, path length, ' +
        'destruction time and component changes are reported (default: entities with CharacterController); a name with ' +
        '* or ? ("Boulder*") follows every matching entity, spawned copies included, each with its id, spawned_at_s and ' +
        'destroyed_at_s (track_max: how many to list, default 20). The result\'s tracked has one entry per track ' +
        'item: a name gives {name, start, final, min, max, path_m, moved_m, destroyed_at_s?, changes?, samples?} ' +
        '(or {name, found: false}); a pattern gives {pattern, matched, alive_at_end, listed?, instances: [{id, name, ' +
        'spawned_at_s?, destroyed_at_s?, start, final, min, max, path_m, moved_m, samples?}]}, with no name of its ' +
        'own: read instances[i].name. sample_every: 0.25 adds positions every 0.25 game ' +
        'seconds to each tracked entity as samples: [[t, x, y, z], ...] (no images needed; capped at 200 per entity and ' +
        '1000 in all, coarser when over). set: {"store": {...}, "save": {...}} starts the run with those ctx.store and ' +
        'ctx.save values (the run has a save of its own, empty otherwise; the player\'s is never touched), and ' +
        'final.save is what the run\'s ctx.save holds at the end, to check persistence: {"set": {"save": {"bestTime": ' +
        '60}}} then final.save.bestTime after a faster finish; it includes the engine\'s own keys, gs.locale (the ' +
        'UI language a Language button chose) and gs.audio (the volumes the Options sliders set). start: ' +
        '{"position": [x, y, z], "yaw_deg": 90} moves the player (or "name") for this run only, to test a late part of a ' +
        'level (runtime space; "space": "blender" for Blender coordinates). size: [width, height], default [480, 270]. ' +
        'scene: the level to play (default: the level open in Blender, which it exports first; a warning says when ' +
        'the played level is not the one just exported; force: true exports everything again). ' +
        'The result reports game_time_s, wall_s, speed, fps and warnings; final.ui is the open menu with its buttons, ' +
        'whose x/y (like game_screenshot\'s ui.buttons) are CSS px of the page\'s canvas at ui.size [w, h]: for a ' +
        'click in a playtest of another size, scale x by size[0] / ui.size[0] and y by size[1] / ui.size[1]. A ' +
        'click on a menu button works from t=0, like {"at": 0, "key": "Enter"} on the focused one. final.ui.hidden ' +
        'lists the elements of the open UI canvases that are hidden, by code or by Disable in Renders, as sorted ' +
        '"Canvas/Name": after one fall, final.ui.hidden is ["HUD/Heart3"]. ' +
        'realtime: true plays on the wall clock instead ' +
        '(not reproducible). Example: {"inputs": [{"at": 0, "key": "KeyW", "hold": 2}, {"at": 1, "key": "Space"}], ' +
        '"screenshots": [1, 2.5], "track": ["Player", "Gem_1"]}. Exports the scene first unless export is false.',
      inputSchema: object({
        inputs: {
          type: 'array',
          maxItems: 500,
          items: object({
            at: { type: 'number', minimum: 0, description: 'Game seconds from the start.' },
            key: { type: 'string', minLength: 1 },
            action: { type: 'string', enum: ['down', 'up', 'press'] },
            hold: { type: 'number', minimum: 0, description: 'Seconds between down and up (keys, mouse down/click).' },
            mouse: { type: 'string', enum: ['move', 'down', 'up', 'click', 'wheel', 'aim'] },
            x: { type: 'number' },
            y: { type: 'number' },
            target: { type: 'string', minLength: 1, description: 'aim: an entity name or pattern ("Grunt*") to keep the pointer on.' },
            pick: { type: 'string', enum: ['nearest', 'first'], description: 'aim: which match (default nearest to the player).' },
            until: { type: 'number', minimum: 0, description: 'aim / move with world: game seconds to keep following (aim default: the end).' },
            world: { ...VEC3, description: 'move or aim: a world point to put the pointer on (runtime space unless space: "blender").' },
            space: { type: 'string', enum: ['runtime', 'blender'] },
            button: { type: 'string', enum: ['left', 'right', 'middle'] },
            dx: { type: 'number' },
            dy: { type: 'number' },
          }),
        },
        duration: { type: 'number', minimum: 0, maximum: MAX_DURATION_S, description: 'Game seconds. Default: the last input + 0.5.' },
        screenshots: { type: ['array', 'integer'], items: { type: 'number', minimum: 0 }, maxItems: 12, minimum: 0, maximum: 12, description: 'Game seconds, or a count spread over the run. Default [0, duration].' },
        track: { type: 'array', items: { type: 'string', minLength: 1 }, maxItems: 20, description: 'Entity names or ids, or name patterns with * or ? ("Boulder*": every matching entity, spawned ones included). Default: entities with CharacterController.' },
        track_max: { type: 'integer', minimum: 1, maximum: 100, description: 'Entities listed per track pattern (default 20); matched counts them all.' },
        sample_every: { type: 'number', minimum: 0.05, maximum: MAX_DURATION_S, description: 'Game seconds between position samples of every tracked entity (e.g. 0.25), reported as samples: [[t, x, y, z], ...].' },
        start: {
          ...START,
          type: ['object', 'array'],
          maxItems: 10,
          items: START,
          description: 'Where an entity starts for this run only: {position, yaw_deg?, rotation?, name?, space?}, or a list of them.',
        },
        size: { type: 'array', items: { type: 'integer', minimum: 64, maximum: 1920 }, minItems: 2, maxItems: 2, description: '[width, height] of the page and screenshots, default [480, 270].' },
        seed: { type: 'integer', description: 'Seeds ctx.random and Math.random during the run (default 1), so random games repeat too.' },
        scene: { type: 'string', minLength: 1, description: 'The level to play (its last export). Default: the level open in Blender, just exported.' },
        realtime: { type: 'boolean', description: 'Play on the wall clock (the old behaviour): results vary with speed. Default false.' },
        state: object({ names: { type: 'array', items: { type: 'string' } }, component: { type: 'string' }, limit: { type: 'integer', minimum: 1, maximum: 500 } }),
        export: { type: 'boolean' },
        force: FORCE,
        save: { type: 'boolean', description: 'Also write the screenshots under .gamestudio/mcp/playtests/.' },
        set: { ...SET_VALUES, description: 'ctx.store and ctx.save values the run starts with (game.test.begin), e.g. {"save": {"bestTime": 60}, "store": {"lives": 1}}. Game time only.' },
        verbose: { type: 'boolean', description: 'All logs (up to 100) and full final entity states. Default false.' },
      }),
      annotations: { ...WRITE, openWorldHint: false },
      handler: async (args, call) => {
        planPlaytest(args); // Bad scripts fail before anything starts.
        const dev = await ctx.ensureDevServer(call.signal);
        /** @type {Record<string, any>} */
        const notes = {};
        if (args.export !== false) {
          try {
            const result = await exportScene(ctx, call.signal, args.force === true);
            notes.export = exportSummary(result);
          } catch (error) {
            if (!(error instanceof ToolError) || error.code !== 'blender_not_running') throw error;
            notes.export_skipped = 'Blender is not running; playing the last export.';
          }
        }
        const { save, export: _export, force: _force, verbose, scene: asked, ...options } = args;
        // The level to play: the one asked for, else the one just exported (the level open in Blender).
        const scene = asked ?? notes.export?.scene;
        /** @type {string[]} */
        const sceneWarnings = [];
        if (scene) {
          const manifest = ctx.root ? path.join(ctx.root, '.gamestudio', 'export', `${scene}.scene.json`) : null;
          if (manifest && !existsSync(manifest)) {
            throw new ToolError(`Level '${scene}' has never been exported, so it cannot be played.`, {
              code: 'invalid_arguments',
              hint: 'export_scene {"all": true} exports every level; or open it (open_scene) and export it.',
            });
          }
          if (notes.export && notes.export.scene !== scene) {
            sceneWarnings.push(`Blender's open level '${notes.export.scene}' was exported, but this run plays '${scene}' as last exported. ` +
              'Export every level first (export_scene {"all": true}) if both changed.');
          }
        }
        const url = scene ? `${dev.url.replace(/\/?$/, '/')}?scene=${encodeURIComponent(scene)}` : dev.url;
        const result = await ctx.browser.playtest(url, options, call.signal, (done, total) => call.progress(done, total, 'steps'));
        const { screenshots, warnings: runWarnings, ...rest } = result;
        const warnings = [...sceneWarnings, ...runWarnings];
        if (!verbose) {
          // Compact: warnings and errors first, then the last lines; entities without rotations.
          const when = (/** @type {any} */ l) => l.t ?? l.at ?? 0;
          const important = rest.logs.filter((l) => l.level === 'error' || l.level === 'warn');
          rest.logs = [...important.slice(-15), ...rest.logs.filter((l) => !important.includes(l)).slice(-10)].sort((a, b) => when(a) - when(b));
          if (rest.final.entities) rest.final.entities = rest.final.entities.map((/** @type {any} */ e) => ({ name: e.name, position: e.position, ...(e.components && Object.keys(e.components).length && { components: e.components }), ...(e.animation && { animation: e.animation }) }));
          // Tracked entities keep what changed; their full final components only come with verbose.
          if (rest.mode === 'game time') rest.tracked = rest.tracked.map((/** @type {any} */ { components: _components, ...t }) => t);
        }
        /** @type {Record<string, any>} */
        const data = {
          url: dev.url,
          ...notes,
          ...(warnings.length && { warnings }),
          ...rest,
          screenshots: screenshots.map((s, i) => ({ index: i, time_s: s.time_s, ...('actual_s' in s && { actual_s: s.actual_s }), ...('positions' in s && { positions: s.positions }) })),
        };
        if (save && ctx.root) data.saved = screenshots.map((s) => ctx.saveImage('playtests', `t${s.time_s}s`, s.image));
        return withImages(data, screenshots.map((s) => ({ data: s.image, mimeType: 'image/png' })));
      },
    },
    {
      name: 'game_screenshot',
      title: 'Game screenshot',
      description:
        'A screenshot of this server\'s headless game (opened on first use with the last export, then kept running; play, ' +
        'stop and live edits from Blender reach it). wait_ms waits before the shot. size: [width, height] of this shot ' +
        '(default: the game\'s size, [480, 270] unless play set another); the game then goes back to its size. save: true ' +
        'also writes the PNG under .gamestudio/mcp/screenshots/. ' +
        'mode: "scene" shows the level drawn by the game\'s renderer from a viewpoint, without playing: game shaders, ' +
        'lighting, lightmaps, tone mapping and post effects as in the game, no title screen, menus or HUD, nothing ' +
        'running. Use it to judge materials in the level. view (Blender coordinates, metres): {"from": [x, y, z], "at": ' +
        '[x, y, z], "lens": 35} (lens in mm, default the scene camera\'s, else 35) or {"camera": "<camera name>"}. It ' +
        'exports first (export: false shows the last export), and the headless game goes back to how it was afterwards ' +
        '(a running game plays again from the start). Example: {"mode": "scene", "view": {"from": [8, -8, 5], "at": [0, 0, 1]}}. ' +
        'ui: "<Canvas>" opens that game UI canvas (a menu or HUD built in ui/ui.blend) before the shot, as ' +
        'ctx.ui.open would; it stays open (a menu pauses the game; stop closes it). Another open menu is closed first ' +
        '(ui_closed names it), so two menus never overlap, and the shot waits until the canvas\'s open animation has ' +
        'ended; wait_ms waits longer. set: {"store": {...}, "save": {...}} sets ctx.store and ctx.save values in the ' +
        'game before that, to shoot screens that depend on them: {"set": {"save": {"subtitles": true}}, "ui": ' +
        '"Options"}, {"set": {"store": {"coins": 7, "lives": 1}}, "ui": "HUD"}. set.save writes the real save of the ' +
        'headless game\'s page (it lasts until changed, unlike a playtest\'s), and running systems may overwrite store ' +
        'values. In mode "game" the result has ui, read at the shot\'s size: ' +
        'the open menu (screen), focus, buttons with x/y in pixels of the returned image, whose size is ui.size ' +
        '[w, h] (size when given; where a playtest click lands in a page of that size; scale x/y for a playtest of ' +
        'another size), open (the open ' +
        'canvases), texts (what every visible text of them says) and hidden (their elements hidden by code or by ' +
        'Disable in Renders, sorted "Canvas/Name", e.g. ["HUD/Heart3"] after one fall). Example: {"ui": "Title", ' +
        '"size": [960, 540]}.',
      inputSchema: object({
        wait_ms: { type: 'integer', minimum: 0, maximum: 30_000 },
        size: { type: 'array', items: { type: 'integer', minimum: 64, maximum: 1920 }, minItems: 2, maxItems: 2, description: '[width, height] of this screenshot. Default: the headless game\'s size.' },
        save: { type: 'boolean' },
        mode: { type: 'string', enum: ['game', 'scene'], description: '"game" (default): the game as it runs. "scene": the level from view, without playing.' },
        view: object({
          from: { ...VEC3, description: 'Mode "scene": where the camera is (Blender space).' },
          at: { ...VEC3, description: 'Mode "scene": the point it looks at (Blender space); Blender\'s Z is up.' },
          lens: { type: 'number', minimum: 1, maximum: 500, description: 'Millimetres on a 36 mm sensor. Default: the scene camera\'s, else 35.' },
          camera: { type: 'string', minLength: 1, description: 'Or look through this Blender camera (its pose and lens).' },
        }),
        export: { type: 'boolean', description: 'Mode "scene": export the open scene first, so the shot shows the latest edits (default true; "reuse" when nothing changed).' },
        ui: { type: 'string', minLength: 1, description: 'Mode "game": open this UI canvas (a menu or HUD from ui/ui.blend, e.g. "Title") before the shot.' },
        set: { ...SET_VALUES, description: 'Mode "game": ctx.store and ctx.save values set in the game before the shot (before ui opens its canvas). save writes the headless game page\'s real save.' },
      }),
      annotations: { ...READ, idempotentHint: false },
      handler: async (args, call) => {
        if (args.mode === 'scene' && (args.ui !== undefined || args.set !== undefined)) {
          throw new ToolError(`${args.ui !== undefined ? 'ui' : 'set'} goes with mode "game": the scene shot draws no menus or HUD.`, {
            code: 'invalid_arguments',
            hint: 'Example: {"ui": "Title"}',
          });
        }
        if (args.mode !== 'scene' && (args.view !== undefined || args.export !== undefined)) {
          throw new ToolError(`${args.view !== undefined ? 'view' : 'export'} goes with mode "scene".`, {
            code: 'invalid_arguments',
            hint: 'Example: {"mode": "scene", "view": {"from": [8, -8, 5], "at": [0, 0, 1]}}',
          });
        }
        if (args.mode === 'scene') {
          const { data, image } = await sceneScreenshot(ctx, args, call, { showsExport, exportSummary });
          if (args.save && ctx.root) data.saved = ctx.saveImage('screenshots', 'scene', image);
          return withImages(data, [{ data: image, mimeType: 'image/png' }]);
        }
        const dev = await ctx.ensureDevServer(call.signal);
        await ctx.hub.ensure().catch(() => {});
        const game = await ctx.browser.ensureGame(dev.url);
        /** @type {string[]} */
        const warnings = [];
        if (args.set?.store !== undefined || args.set?.save !== undefined) {
          warnings.push(...(await ctx.browser.setGameValues(game.page, { store: args.set.store ?? null, save: args.set.save ?? null })));
        }
        /** @type {string | null} */
        let closed = null;
        if (args.ui !== undefined) {
          const opening = await ctx.browser.openUiCanvas(game.page, args.ui, { openSeconds: exportedOpenSeconds(ctx.root, args.ui) });
          closed = opening.closed;
          if (!opening.opened) warnings.push(`The UI canvas '${args.ui}' did not report open in time; the shot may not show it. Check game_logs.`);
          else if (opening.animating) warnings.push(`The UI canvas '${args.ui}' was still playing its open animation after ${UI_TIMEOUTS.animation / 1000} s; the shot may show it part-way.`);
        }
        if (args.wait_ms) await sleep(args.wait_ms);
        // The UI state is read at the shot's size, before the page goes back to its own: ui.size is the image's
        // size and button x/y are pixels of the returned image.
        const { image, read: ui } = await ctx.browser.shoot(game.page, args.size, uiStateInPage);
        const info = await game.page.evaluate(() => {
          const gs = /** @type {any} */ (window).__gs;
          return { state: gs?.state ?? null, scene: gs?.game?.scene?.name ?? null, rev: gs?.game?.scene?.rev ?? null };
        });
        /** @type {Record<string, any>} */
        const data = { url: dev.url, opened_now: game.opened, size: args.size ?? game.size, game: info, ...(closed && { ui_closed: closed }), ...(ui && { ui }), ...(warnings.length && { warnings }) };
        if (args.save && ctx.root) data.saved = ctx.saveImage('screenshots', args.ui ? `ui-${args.ui}` : 'game', image);
        return withImages(data, [{ data: image, mimeType: 'image/png' }]);
      },
    },
  ];
}
