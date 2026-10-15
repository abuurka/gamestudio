// @ts-check
// SPDX-License-Identifier: MIT
// @gamestudio/mcp: an MCP server that lets coding agents drive Blender (scene, assets,
// components) and the running game. See docs/MCP.md.

import { readFileSync } from 'node:fs';
import { Context } from './context.js';
import { createMcpServer } from './protocol.js';
import { assetTools } from './tools/assets.js';
import { compareTools } from './tools/compare.js';
import { componentTools } from './tools/components.js';
import { docsTools } from './tools/docs.js';
import { gameTools } from './tools/game.js';
import { libraryTools } from './tools/library.js';
import { lightingTools } from './tools/lighting.js';
import { sceneTools } from './tools/scene.js';
import { sessionTools } from './tools/session.js';
import { statsTools } from './tools/stats.js';
import { templateTools } from './tools/templates.js';
import { uiTools } from './tools/ui.js';

export { Context } from './context.js';
export { createMcpServer, serveStdio, PROTOCOL_VERSIONS } from './protocol.js';
export { ToolError } from './util.js';

export const VERSION = /** @type {string} */ (JSON.parse(readFileSync(new URL('../package.json', import.meta.url), 'utf8')).version);

export const INSTRUCTIONS = [
  'GameStudio: build and change a three.js game whose levels and assets are authored in Blender.',
  'Start with status. If Blender is not running, launch_blender (background: true for no window).',
  'Edits apply immediately; each tool call is one Blender undo step (undo reverts it). Take a checkpoint before large or risky changes.',
  'Blender space is Z-up, metres, rotations in degrees; game positions (game_state, playtest) are runtime space, Y-up.',
  'Loop: build (create_from_kit, run_python, import_asset, apply_material) -> check (validate_assets, render_views) ->',
  'wire up (add_component; write TS components/systems in src/ yourself) -> play / playtest -> game_state, game_logs -> fix.',
  'Menus and HUDs are built in ui/ui.blend, one Blender scene per screen: ui_open, then ui_canvas, ui_add, ui_set (canvas pixels,',
  'origin top-left, y down); check with game_screenshot {"ui": "<Canvas>"} and a playtest; game code reacts with ctx.ui.on.',
  'Never work around objects the developer protected from the agent; code paused_by_user means they paused you in Blender: ask or wait. Save when a change is done.',
].join(' ');

/** All tool definitions for a context. @param {Context} ctx */
export function createTools(ctx) {
  return [
    ...sessionTools(ctx),
    ...sceneTools(ctx),
    ...assetTools(ctx),
    ...libraryTools(ctx),
    ...componentTools(ctx),
    ...gameTools(ctx),
    ...lightingTools(ctx),
    ...compareTools(ctx),
    ...statsTools(ctx),
    ...templateTools(ctx),
    ...uiTools(ctx),
    ...docsTools(ctx),
  ];
}

/**
 * The GameStudio MCP server for a project.
 * @param {{ root: string | null, env?: NodeJS.ProcessEnv, log?: (line: string) => void, context?: Context,
 *   notify?: (message: object) => void }} options
 */
export function createGameStudioServer({ root, env = process.env, log = () => {}, context, notify }) {
  const ctx = context ?? new Context({ root, env, log, version: VERSION });
  const server = createMcpServer({
    name: 'gamestudio',
    title: 'GameStudio',
    version: VERSION,
    instructions: INSTRUCTIONS,
    tools: createTools(ctx),
    notify,
    log,
  });
  ctx.blender.agentInfo = () => server.clientInfo;
  return { server, ctx };
}
