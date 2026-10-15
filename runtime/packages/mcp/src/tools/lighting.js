// @ts-check
// SPDX-License-Identifier: MIT
// bake_lighting: bakes the indirect light (sky, bounce, AO, baked lamps) of the level's static meshes into
// lightmaps in Blender (Cycles, core/lightmap.py), then exports the scene and reloads connected games.
// It writes generated files only (.gamestudio/lightmaps, the export), never the .blend.

import { NAMES, WRITE, object } from './common.js';

/** @typedef {import('./common.js').Context} Context */

/** How long Blender may take to answer, by quality (the bake runs on Blender's main thread). */
export const BAKE_TIMEOUTS_MS = /** @type {Record<string, number>} */ ({ preview: 600_000, final: 3_600_000 });

/**
 * Sends the export's scene.load to every connected game. The peers reloaded, or null without a dev server.
 * @param {Context} ctx
 * @param {Record<string, unknown>} load
 */
async function reloadGames(ctx, load) {
  if (!(await ctx.devSession())) return null;
  await ctx.hub.ensure().catch(() => {});
  const games = ctx.hub.connected ? ctx.hub.gamePeers() : [];
  for (const g of games) ctx.hub.send({ ...load, to: g.peer });
  return games.map((g) => g.peer);
}

/** @param {Context} ctx @returns {import('./common.js').ToolDefinition[]} */
export function lightingTools(ctx) {
  return [
    {
      name: 'bake_lighting',
      title: 'Bake lighting',
      description:
        'Bake lightmaps for the level\'s static meshes (Cycles, in Blender): the sky light with its ambient occlusion, ' +
        'the bounce light of every lamp and of the sky, and glowing (emissive) surfaces, so the game shows soft ' +
        'indirect light and contact shadows that realtime lights cannot. Use it once the level\'s geometry and lights ' +
        'are final, before compare_views and before shipping; bake again after moving static geometry or changing ' +
        'lights (the export report lists stale lightmaps). quality: "preview" (default) takes seconds, "final" takes ' +
        'longer (minutes on big levels) and is cleaner. Lamps stay realtime (direct light and shadows in the game) ' +
        'unless the light object has custom property gs_lightmap = "baked": then it is baked entirely and switched ' +
        'off in the game (area lights are always baked). Static means not moving: no RigidBody (or type fixed), no ' +
        'CharacterController, Spin or Animator, no animation; an object with custom property gs_lightmap = False is ' +
        'left out. objects: bake only these (the others keep their lightmaps). An unchanged scene is not baked again ' +
        '(up_to_date) unless force: true. Results go to .gamestudio/lightmaps/<scene>/ and, with export (default ' +
        'true), into the export, and connected games reload. Not an undo step: the scene and the .blend do not ' +
        'change. Returns objects baked, skipped (with the reason), baked_lights, atlases, texture_mb, seconds, ' +
        'warnings and the export report.',
      inputSchema: object({
        quality: { type: 'string', enum: ['preview', 'final'], description: '"preview" (default): seconds; "final": slower, cleaner.' },
        objects: { ...NAMES, description: 'Bake only these objects (names or gs_ids); the others keep their lightmaps. Default: every static mesh.' },
        force: { type: 'boolean', description: 'Bake even when nothing changed since the last bake. Default false.' },
        export: { type: 'boolean', description: 'Export the scene afterwards so games show the lightmaps (connected games reload). Default true.' },
      }),
      annotations: WRITE,
      handler: async (args, call) => {
        const quality = args.quality ?? 'preview';
        const result = await ctx.blender.call('bake_lighting', args, { signal: call.signal, timeoutMs: BAKE_TIMEOUTS_MS[quality] });
        /** @type {Record<string, any>} */
        const out = { ...result };
        if (result.export) {
          const { scene_load: load, ...report } = result.export;
          out.export = report;
          if (load) {
            const reloaded = await reloadGames(ctx, load);
            if (reloaded) out.export.reloaded_games = reloaded;
          }
        }
        return out;
      },
    },
  ];
}
