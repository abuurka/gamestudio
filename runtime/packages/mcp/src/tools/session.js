// @ts-check
// SPDX-License-Identifier: MIT
// Session tools: status, launching Blender, scenes, saving, checkpoints, undo.

import path from 'node:path';
import { defaultScene, findBlenderExecutable, projectScenes, readProjectConfig } from '../discovery.js';
import { ToolError } from '../util.js';
import { DESTRUCTIVE, READ, WRITE, bridgeTool, object } from './common.js';

/** @typedef {import('./common.js').Context} Context */

/** @param {Context} ctx @returns {import('./common.js').ToolDefinition[]} */
export function sessionTools(ctx) {
  return [
    {
      name: 'status',
      title: 'Status',
      description:
        'Where things stand: the project, the Blender session (file, unsaved changes, scene, object count, component and ' +
        'shader types), the dev server, connected games and their play state, the headless game, and buffered log ' +
        'counts. Call it first, and whenever something fails. It never launches anything.',
      inputSchema: object({}),
      annotations: READ,
      handler: () => status(ctx),
    },
    {
      name: 'launch_blender',
      title: 'Launch Blender',
      description:
        'Start Blender for this project when none is running, open a scene and wait until its bridge answers. The ' +
        'scene is scene, else the one open when Blender last crashed (as last saved), else scenes/<startScene>.blend. A ' +
        'window opens by default; background: true runs it headless (it closes when this MCP server exits). After a ' +
        'crash, the error lists the recovery choices: the last saved scene, a newer Blender autosave (pass its path as ' +
        'scene), or checkpoint: "<id>" (opens the scene, then restores that checkpoint). kill_unresponsive: true stops a ' +
        'Blender that has stopped answering (5 s ping) and starts a new one: its unsaved changes are lost. Uses ' +
        'GAMESTUDIO_BLENDER, else the path the GameStudio extension recorded.',
      inputSchema: object({
        background: { type: 'boolean', description: 'Headless session without a window. Default false.' },
        scene: { type: 'string', description: 'A .blend to open, relative to the project or absolute (an autosave works too).' },
        checkpoint: { type: 'string', minLength: 1, description: 'Start from this checkpoint id (list_checkpoints).' },
        kill_unresponsive: { type: 'boolean', description: 'Stop a hung Blender for this project first. Default false.' },
      }),
      annotations: { ...WRITE, openWorldHint: true },
      handler: async (args, call) =>
        ctx.blender.launch({
          background: args.background ?? false,
          scene: args.scene === undefined ? undefined : ctx.resolvePath(args.scene),
          checkpoint: args.checkpoint,
          killUnresponsive: args.kill_unresponsive ?? false,
          signal: call.signal,
        }),
    },
    {
      name: 'open_scene',
      title: 'Open scene',
      description:
        'Open a .blend in Blender (path relative to the project or absolute), or create a new empty one with create: true. ' +
        'Refuses when the open file has unsaved changes unless save_changes or discard_changes is true. If Blender is not ' +
        'running, launches it with this scene (background: true for headless).',
      inputSchema: object(
        {
          path: { type: 'string', minLength: 1, description: 'e.g. scenes/main.blend' },
          create: { type: 'boolean', description: 'Create a new, empty scene at path (must not exist).' },
          save_changes: { type: 'boolean', description: 'Save the current file before switching.' },
          discard_changes: { type: 'boolean', description: 'Drop unsaved changes of the current file.' },
          background: { type: 'boolean', description: 'Only when Blender has to be launched: run it headless.' },
        },
        ['path'],
      ),
      annotations: WRITE,
      handler: async (args, call) => {
        const file = ctx.resolvePath(args.path);
        if (!ctx.blender.connected) {
          try {
            await ctx.blender.connect();
          } catch (error) {
            if (!(error instanceof ToolError) || error.code !== 'blender_not_running' || args.create) throw error;
            const launched = await ctx.blender.launch({ background: args.background ?? false, scene: file, signal: call.signal });
            return { opened: file, launched };
          }
        }
        const { background: _background, ...rest } = args;
        return ctx.blender.call('open_scene', { ...rest, path: file }, { signal: call.signal });
      },
    },
    {
      name: 'save',
      title: 'Save scene',
      description:
        'Save the open .blend (Ctrl+S). With path, save it under a new name (relative to the project) and keep working on that file. ' +
        'Saving the UI file (ui/ui.blend) exports its screens for the game, and the result has ui_export: {canvases: ' +
        '[{canvas, file, rev, written, elements}], locales, warnings} (written: false when a screen did not change), ' +
        'or {error}.',
      inputSchema: object({ path: { type: 'string', minLength: 1, description: 'Save As target, e.g. scenes/level2.blend.' } }),
      annotations: WRITE,
      handler: (args, call) => ctx.blender.call('save', args.path ? { path: ctx.resolvePath(args.path) } : {}, { signal: call.signal }),
    },
    bridgeTool(ctx, {
      name: 'checkpoint',
      title: 'Checkpoint',
      description:
        'Save a copy of the open .blend, with unsaved changes, under .gamestudio/checkpoints/<id>/ with a manifest. The ' +
        'open file is not saved or changed. Take one before risky or large changes; restore_checkpoint brings it back.',
      inputSchema: object({ label: { type: 'string', minLength: 1, maxLength: 120, description: 'What state this is, e.g. "before terrain rework".' } }, ['label']),
      annotations: WRITE,
    }),
    bridgeTool(ctx, {
      name: 'list_checkpoints',
      title: 'List checkpoints',
      description: 'Checkpoints of this project, newest first: id, label, time, source scene, size.',
      inputSchema: object({ limit: { type: 'integer', minimum: 1, maximum: 500 } }),
      annotations: READ,
    }),
    bridgeTool(ctx, {
      name: 'restore_checkpoint',
      title: 'Restore checkpoint',
      description:
        'Replace the scene file with a checkpoint: opens the checkpoint and saves it over the scene it was taken from. The ' +
        'current state is checkpointed first ("before restore ..."), unless backup is false.',
      inputSchema: object(
        {
          id: { type: 'string', minLength: 1, description: 'Checkpoint id from list_checkpoints (or its label).' },
          backup: { type: 'boolean', description: 'Checkpoint the current state first. Default true.' },
        },
        ['id'],
      ),
      annotations: DESTRUCTIVE,
    }),
    bridgeTool(ctx, {
      name: 'undo',
      title: 'Undo',
      description:
        'Undo the last steps in Blender (Ctrl+Z). Each agent edit is one step; run_python is one step too. Undo ' +
        'changes Blender, not the last export: the game (and game_screenshot in mode "game") keeps what was exported ' +
        'until the next play, playtest or export_scene, which exports the undone state (a full export). With the ' +
        'live link on, a running game also gets the undone transforms and component values right away.',
      inputSchema: object({ steps: { type: 'integer', minimum: 1, maximum: 50 } }),
      annotations: WRITE,
    }),
    bridgeTool(ctx, {
      name: 'redo',
      title: 'Redo',
      description: 'Redo steps undone with undo (Ctrl+Shift+Z).',
      inputSchema: object({ steps: { type: 'integer', minimum: 1, maximum: 50 } }),
      annotations: WRITE,
    }),
  ];
}

/** @param {Context} ctx */
async function status(ctx) {
  /** @type {Record<string, any>} */
  const out = { mcp: { version: ctx.version, pid: process.pid } };
  const root = ctx.root;
  if (root) {
    const config = readProjectConfig(root);
    out.project = { root, name: config.name, startScene: config.startScene, scenes: projectScenes(root) };
  } else {
    out.project = null;
    out.hint = 'Not inside a GameStudio project: start Claude Code in the game folder (with gamestudio.json).';
  }

  /** @type {string[]} */
  const next = [];
  const responding = await ctx.blender.responsive(5000);
  if (responding === false) {
    const timeout = ctx.blender.lastTimeout;
    out.blender = {
      connected: true,
      responding: false,
      pid: ctx.blender.session?.pid,
      file: ctx.blender.session?.blend,
      ...(timeout && { last_unanswered: timeout.method, seconds_ago: Math.round((Date.now() - timeout.at) / 1000) }),
    };
    next.push('Blender is busy or hung (no answer within 5 s). Wait and call status again; if it stays stuck, launch_blender {"kill_unresponsive": true}.');
  }
  try {
    if (responding === false) throw null; // reported above
    const s = await ctx.blender.call('status', {}, { timeoutMs: 15_000 });
    out.blender = {
      connected: true,
      pid: s.blender?.pid,
      version: s.blender?.version,
      background: s.blender?.background,
      launched_by_mcp: ctx.blender.launched?.pid === s.blender?.pid,
      file: s.file?.path,
      unsaved_changes: s.file?.dirty,
      scene: s.scene?.name,
      objects: s.scene?.objects,
      mode: s.scene?.mode,
      component_types: s.registry?.components,
      // An extension older than game shaders reports none.
      shader_types: s.registry && (s.registry.shaders ?? 0),
      kits: s.kits,
      paused: s.agent?.paused,
      locked_objects: s.agent?.locked_objects,
    };
    if (s.agent?.paused) next.push('The user paused the agent in Blender: changes are refused (paused_by_user) until they resume. Ask them, or wait; reading the scene still works.');
    if (root && s.project?.root && path.resolve(s.project.root) !== path.resolve(root)) {
      out.blender.warning = `Blender has a scene of another project open (${s.project.root}).`;
    }
    if (!s.file?.path) next.push('The Blender file is unsaved: open_scene a project scene, or save({path}).');
    else if (!s.project && root) next.push(`Blender has no scene of this project open: open_scene ${path.relative(root, defaultScene(root) ?? path.join(root, 'scenes', 'main.blend')).split(path.sep).join('/')}.`);
  } catch (error) {
    if (error !== null) {
      out.blender = { connected: false, ...(error instanceof ToolError ? error.toJSON() : { error: String(error) }) };
      const exe = findBlenderExecutable(ctx.env);
      out.blender.executable = exe?.path ?? null;
      if (root) out.blender.default_scene = defaultScene(root);
      next.push(error instanceof ToolError && error.code === 'blender_crashed'
        ? 'Blender crashed: pick a recovery choice from blender.hint.'
        : 'launch_blender (or launch_blender {"background": true}) to start Blender.');
    }
  }

  const dev = await ctx.devSession();
  out.dev_server = dev ? { running: true, url: dev.url, pid: dev.pid } : { running: false };
  if (dev) {
    try {
      await ctx.hub.ensure();
    } catch (error) {
      out.dev_server.hub_error = error instanceof Error ? error.message : String(error);
    }
  } else if (root) {
    next.push('play starts the dev server (through Blender) and a game.');
  }
  const games = ctx.hub.connected ? ctx.hub.gamePeers() : [];
  out.games = games.map((g) => ({
    peer: g.peer,
    state: g.state,
    scene: g.scene,
    rev: g.rev,
    fps: g.stats?.fps ?? null,
    headless: ctx.browser.game ? g.client.includes(ctx.browser.game.tag) : false,
  }));
  out.headless_game = ctx.browser.game ? { url: ctx.browser.game.url, open: !ctx.browser.game.page.isClosed(), ...ctx.browser.renderInfo() } : null;
  const logs = ctx.hub.logs;
  out.logs = {
    buffered: logs.length,
    errors: logs.filter((l) => l.level === 'error').length,
    warnings: logs.filter((l) => l.level === 'warn').length,
    last_seq: ctx.hub.seq,
  };
  if (next.length) out.next = next;
  return out;
}
