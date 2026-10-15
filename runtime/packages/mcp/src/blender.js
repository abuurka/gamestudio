// @ts-check
// SPDX-License-Identifier: MIT
// Finds, connects to and launches the Blender session that serves this project.

import { spawn } from 'node:child_process';
import { closeSync, existsSync, mkdirSync, openSync, readFileSync, statSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { connectBridge } from './bridge-client.js';
import {
  defaultScene,
  findAutosaves,
  findBlenderExecutable,
  findCrashLog,
  listCheckpoints,
  projectScenes,
  readProjectSession,
  readUserSessions,
  samePath,
  sessionAlive,
} from './discovery.js';
import { ToolError, pidAlive, sleep } from './util.js';

/** Bridge methods that export: when one times out, Blender is still exporting. */
const EXPORTING = new Set(['export_scene', 'export_all', 'play_in_blender']);

const PACKAGE_DIR = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
export const BOOTSTRAP = path.join(PACKAGE_DIR, 'blender', 'bootstrap.py');

/**
 * Per-method answer timeouts (ms); the default is 60 s. Exports of big levels (texture baking, many
 * objects) take minutes, and a timed-out export keeps running in Blender: allow 15 minutes.
 */
export const TIMEOUTS = /** @type {Record<string, number>} */ ({
  open_scene: 180_000,
  restore_checkpoint: 180_000,
  checkpoint: 120_000,
  save: 120_000,
  export_scene: 900_000,
  export_all: 900_000, // a background Blender per level
  play_in_blender: 900_000, // exports first
  render_views: 180_000,
  import_asset: 300_000,
  validate_assets: 180_000,
  create_from_kit: 120_000,
  create_procedural: 180_000,
  set_procedural: 120_000,
  asset_add: 300_000, // a background Blender writes the library file
  asset_edit_components: 300_000,
  asset_library_restore: 120_000,
  asset_place: 120_000,
  asset_spawnable: 120_000,
  asset_replace: 120_000,
  asset_reload: 120_000,
  render_camera_view: 300_000,
  compare_images: 120_000,
  ui_open: 180_000, // opens ui/ui.blend; a missing one is made first (in a background Blender when another file is open)
});

/**
 * @typedef {import('./discovery.js').BridgeSession} BridgeSession
 * @typedef {import('./bridge-client.js').BridgeClient} BridgeClient
 */

export class BlenderManager {
  /**
   * @param {{ root: string | null, env?: NodeJS.ProcessEnv, log?: (line: string) => void,
   *   connect?: typeof connectBridge, client?: string }} options
   */
  constructor({ root, env = process.env, log = () => {}, connect = connectBridge, client = 'gamestudio-mcp' }) {
    this.root = root;
    this.env = env;
    this.log = log;
    this.connectFn = connect;
    this.clientName = client;
    /** The MCP client this server serves (clientInfo), passed to Blender in hello. Set by createGameStudioServer. */
    this.agentInfo = /** @type {() => { name?: unknown, version?: unknown } | null} */ (() => null);
    /** @type {BridgeClient | null} */
    this.client = null;
    /** @type {BridgeSession | null} */
    this.session = null;
    /** @type {{ child: import('node:child_process').ChildProcess, pid: number, background: boolean, scene: string | null, log: string | null, startedAt: number } | null} */
    this.launched = null;
    /** @type {Promise<BridgeClient> | null} */
    this.connecting = null;
    /** A session that ended without closing normally: {pid, blend, project, at, during, startedAt}. */
    this.crash = /** @type {{ pid: number, blend: string | null, project: string | null, at: number, during: string[], startedAt: number } | null} */ (null);
    /** The last request Blender did not answer in time. */
    this.lastTimeout = /** @type {{ method: string, at: number } | null} */ (null);
    /** Pids this server stopped on purpose (quit, kill_unresponsive): their exits are not crashes. */
    this.stopped = new Set();
  }

  get connected() {
    return this.client !== null && !this.client.closed;
  }

  /**
   * Discovery candidates, best first: the project's file, then user-level sessions that have
   * no project open or this project open.
   * @returns {{ candidates: BridgeSession[], elsewhere: BridgeSession[], stale: BridgeSession[] }}
   */
  discover() {
    /** @type {BridgeSession[]} */
    const candidates = [];
    /** @type {BridgeSession[]} */
    const elsewhere = [];
    /** @type {BridgeSession[]} */
    const stale = [];
    if (this.root) {
      const own = readProjectSession(this.root);
      if (own) (sessionAlive(own) ? candidates : stale).push(own);
    }
    for (const session of readUserSessions(this.env)) {
      if (!sessionAlive(session)) {
        stale.push(session);
        continue;
      }
      if (candidates.some((c) => c.pid === session.pid && c.port === session.port)) continue;
      if (!this.root || !session.project || samePath(session.project, this.root)) candidates.push(session);
      else elsewhere.push(session);
    }
    return { candidates, elsewhere, stale };
  }

  /**
   * Connects to the best discovered session (or to `preferPid` when it is among them).
   * @param {{ preferPid?: number }} [options]
   * @returns {Promise<BridgeClient>}
   */
  async connect({ preferPid } = {}) {
    if (this.connected && (!preferPid || this.session?.pid === preferPid)) return /** @type {BridgeClient} */ (this.client);
    this.connecting ??= this.#connect(preferPid).finally(() => {
      this.connecting = null;
    });
    return this.connecting;
  }

  /** True when the connected session has a scene of this project open. */
  get onProject() {
    return Boolean(this.connected && this.root && this.session?.project && samePath(this.session.project, this.root));
  }

  /** @param {number} [preferPid] */
  async #connect(preferPid) {
    const found = this.discover();
    const { elsewhere, stale } = found;
    const candidates = preferPid ? found.candidates.filter((s) => s.pid === preferPid) : found.candidates;
    /** @type {string[]} */
    const failures = [];
    for (const session of candidates) {
      try {
        const client = await this.connectFn({ host: session.host ?? '127.0.0.1', port: session.port, token: session.token, pid: session.pid, client: this.clientName, agent: this.agentInfo() });
        this.client?.close();
        this.client = client;
        this.session = session;
        this.crash = null;
        client.onClose(() => {
          if (this.client === client) this.client = null;
          if (this.stopped.has(session.pid)) return;
          // A socket that closes on its own: did Blender exit? (Checked a moment later: a crashing
          // process can still be shutting down when its sockets close.)
          const during = client.closedDuring;
          setTimeout(() => {
            if (!pidAlive(session.pid)) this.#recordCrash(session, during);
          }, 300).unref?.();
        });
        this.log(`connected to Blender pid ${session.pid} on port ${session.port} (${session.blend ?? 'unsaved file'})`);
        return client;
      } catch (error) {
        failures.push(`pid ${session.pid} port ${session.port}: ${error instanceof Error ? error.message : String(error)}`);
      }
    }
    throw this.notRunningError({ elsewhere, stale, failures });
  }

  /** @param {BridgeSession} session @param {string[]} during */
  #recordCrash(session, during) {
    this.crash = {
      pid: session.pid,
      blend: session.blend,
      project: session.project,
      at: Date.now(),
      during,
      startedAt: Date.parse(session.startedAt ?? '') || 0,
    };
    this.log(`Blender pid ${session.pid} exited unexpectedly${during.length ? ` during ${during.join(', ')}` : ''}`);
  }

  /** A crashed session: the one this server saw end, or the project's leftover discovery file. */
  deadSession() {
    if (this.crash) return this.crash;
    if (!this.root) return null;
    const own = readProjectSession(this.root);
    if (own && !sessionAlive(own)) {
      return { pid: own.pid, blend: own.blend, project: own.project, at: 0, during: [], startedAt: Date.parse(own.startedAt ?? '') || 0 };
    }
    return null;
  }

  /**
   * How to get back after a crash: the last saved scene, a newer Blender autosave, the newest
   * checkpoint, plus the crash log's first lines.
   * @param {{ pid: number, blend: string | null, startedAt: number }} dead
   */
  recoveryOptions(dead) {
    /** @type {string[]} */
    const options = [];
    /** @type {Record<string, unknown>} */
    const data = {};
    const rel = (/** @type {string} */ p) => (this.root ? path.relative(this.root, p).split(path.sep).join('/') : p);
    const clock = (/** @type {Date} */ d) => d.toTimeString().slice(0, 5);
    const blend = dead.blend && existsSync(dead.blend) ? dead.blend : null;
    if (blend) options.push(`launch_blender reopens ${rel(blend)} as last saved (${clock(statSync(blend).mtime)})`);
    const autosave = findAutosaves(dead.blend, dead.pid, this.env)[0];
    if (autosave) {
      options.push(
        `launch_blender {"scene": "${autosave.path.split(path.sep).join('/')}"} opens Blender's autosave from ${clock(autosave.mtime)}, ` +
          `which has unsaved work${blend ? `; then save {"path": "${rel(blend)}"}` : ''}`,
      );
    }
    const checkpoint = this.root ? listCheckpoints(this.root)[0] : undefined;
    if (checkpoint) {
      options.push(`launch_blender {"checkpoint": "${checkpoint.id}"} starts from the newest checkpoint ('${checkpoint.label ?? ''}'${checkpoint.created ? `, ${String(checkpoint.created).slice(11, 16)}` : ''})`);
    }
    const crashLog = findCrashLog(dead.blend, dead.startedAt || 0, this.env);
    if (crashLog) data.crash_log = crashLog;
    return { options, data };
  }

  /** @param {{ pid: number, blend: string | null, startedAt: number, during?: string[] }} dead @param {string} [method] */
  crashError(dead, method) {
    const during = method ?? dead.during?.[0];
    const { options, data } = this.recoveryOptions(dead);
    return new ToolError(`Blender (pid ${dead.pid}) exited unexpectedly${during ? ` while running ${during}` : ''}: it crashed or was killed.`, {
      code: 'blender_crashed',
      hint: options.length ? `Recover: ${options.join('; or ')}.` : 'Call launch_blender to start it again.',
      data: Object.keys(data).length ? data : undefined,
    });
  }

  /**
   * @param {{ elsewhere?: BridgeSession[], stale?: BridgeSession[], failures?: string[] }} [details]
   */
  notRunningError({ elsewhere = [], stale = [], failures = [] } = {}) {
    const dead = this.deadSession();
    if (dead && !(this.launched && pidAlive(this.launched.pid))) return this.crashError(dead);
    const scene = this.root ? defaultScene(this.root) : null;
    const where = this.root ? ` for this project (${this.root})` : '';
    const launch = scene
      ? `call launch_blender to open ${path.relative(this.root ?? '', scene).split(path.sep).join('/')} (add background: true for a headless session)`
      : 'call launch_blender (add background: true for a headless session), then open_scene';
    const parts = [`Blender is not running${where}: ${launch}, or open the project's scene in Blender with the GameStudio extension enabled (Agent bridge preference on).`];
    if (this.launched && pidAlive(this.launched.pid)) parts.push(`A Blender this server launched (pid ${this.launched.pid}) is still starting; call status again in a few seconds.`);
    if (elsewhere.length) parts.push(`Blender pid ${elsewhere[0]?.pid} is running with another project (${elsewhere[0]?.project}); it is left alone.`);
    if (failures.length) parts.push(`Discovery files pointed to sessions that did not answer: ${failures.join('; ')}.`);
    else if (stale.length) parts.push('Found discovery files from Blender sessions that have exited.');
    return new ToolError(parts[0] ?? 'Blender is not running.', {
      code: 'blender_not_running',
      hint: parts.slice(1).join(' ') || undefined,
    });
  }

  /**
   * Calls a bridge method, connecting first if needed.
   * @param {string} method
   * @param {Record<string, unknown>} [params]
   * @param {{ timeoutMs?: number, signal?: AbortSignal }} [options]
   */
  async call(method, params = {}, options = {}) {
    const client = await this.connect();
    const session = this.session;
    try {
      return await client.call(method, params, { timeoutMs: options.timeoutMs ?? TIMEOUTS[method] ?? 60_000, signal: options.signal });
    } catch (error) {
      if (error instanceof ToolError && error.code === 'blender_disconnected' && session) {
        await sleep(400);
        if (this.stopped.has(session.pid)) {
          throw new ToolError(`Blender (pid ${session.pid}) was stopped by launch_blender kill_unresponsive while running ${method}.`, {
            code: 'blender_stopped',
            hint: 'Its unsaved changes are lost. Call status; repeat the work in smaller steps.',
          });
        }
        if (!pidAlive(session.pid)) {
          this.#recordCrash(session, [method]);
          throw this.crashError(/** @type {any} */ (this.crash), method);
        }
      }
      if (error instanceof ToolError && error.code === 'blender_timeout' && EXPORTING.has(method)) {
        this.lastTimeout = { method, at: Date.now() };
        const seconds = Math.round((options.timeoutMs ?? TIMEOUTS[method] ?? 60_000) / 1000);
        throw new ToolError(
          `Blender did not finish ${method} within ${seconds} s. It is still exporting, and it will be busy (answering ` +
            'no other request) until that export finishes.',
          {
            code: 'blender_timeout',
            hint:
              'Wait, then call status: once Blender answers again, the export is written. Then call the tool again: ' +
              'an export of an unchanged scene is quick (tier "reuse"). Do not start another export meanwhile. If it ' +
              'never answers, launch_blender {"kill_unresponsive": true} stops it and starts a new one (unsaved ' +
              'changes are lost; autosaves and checkpoints remain).',
          },
        );
      }
      if (error instanceof ToolError && error.code === 'blender_timeout') {
        this.lastTimeout = { method, at: Date.now() };
        error.hint =
          `${error.hint ?? ''} If it never answers, launch_blender {"kill_unresponsive": true} stops it and starts a new one ` +
          '(unsaved changes are lost; autosaves and checkpoints remain).' +
          (method === 'run_python' ? ' For long scripts, split the work over several run_python calls.' : '');
      }
      throw error;
    }
  }

  /**
   * Does Blender answer within `timeoutMs`? False when it is busy or hung (requests run one at a
   * time on its main thread), null when no Blender is connected.
   * @param {number} [timeoutMs]
   */
  async responsive(timeoutMs = 5000) {
    let client;
    try {
      client = await this.connect();
    } catch {
      return null;
    }
    try {
      await client.call('ping', {}, { timeoutMs });
      return true;
    } catch {
      return false;
    }
  }

  /**
   * Starts Blender for the project and waits until its bridge answers.
   * @param {{ background?: boolean, scene?: string | null, timeoutMs?: number, signal?: AbortSignal,
   *   checkpoint?: string, killUnresponsive?: boolean }} [options]
   */
  async launch({ background = false, scene, timeoutMs, signal, checkpoint, killUnresponsive = false } = {}) {
    // A Blender with this project open counts as running; one with no project (found through the
    // user-level discovery file) is left alone and a new one is started.
    const own = this.discover().candidates.find((s) => s.project && this.root && samePath(s.project, this.root));
    /** @type {number | null} */
    let killed = null;
    if (own) {
      await this.connect({ preferPid: own.pid }).catch(() => null);
      if (killUnresponsive) {
        if (await this.responsive(5000)) {
          throw new ToolError(`Blender (pid ${own.pid}) answers, so it was not stopped.`, { code: 'already_running', hint: 'It is connected; use it, or open_scene to switch scenes.' });
        }
        killed = await this.#kill(own.pid);
        if (scene === undefined && !checkpoint && own.blend) scene = own.blend;
      } else if (this.onProject) {
        const hint = this.lastTimeout
          ? 'Use open_scene to switch scenes. If it has stopped answering, pass kill_unresponsive: true.'
          : 'Use open_scene to switch scenes.';
        throw new ToolError(`Blender is already running for this project (pid ${this.session?.pid}); connected to it.`, { code: 'already_running', hint });
      }
    }
    /** @type {any} */
    let restorePoint = null;
    if (checkpoint) {
      restorePoint = this.root ? listCheckpoints(this.root).find((c) => c.id === checkpoint || c.id.startsWith(checkpoint)) : undefined;
      if (!restorePoint) {
        throw new ToolError(`No checkpoint '${checkpoint}'.`, {
          code: 'not_found',
          hint: this.root ? `Checkpoints: ${listCheckpoints(this.root).slice(0, 5).map((c) => c.id).join(', ') || 'none'}` : undefined,
        });
      }
      if (scene === undefined && restorePoint.source && this.root) scene = path.join(this.root, restorePoint.source);
    }
    // After a crash, reopen the scene that was open (as last saved).
    const dead = this.deadSession();
    if (scene === undefined && dead?.blend && existsSync(dead.blend)) scene = dead.blend;
    const exe = findBlenderExecutable(this.env);
    if (!exe) {
      throw new ToolError('Could not find Blender to launch.', {
        code: 'blender_not_found',
        hint: 'Set GAMESTUDIO_BLENDER to blender(.exe) (Blender 5.2+) in the MCP server environment (.mcp.json "env"), or open Blender once with the GameStudio extension so it records its path.',
      });
    }
    let file = scene === undefined ? (this.root ? defaultScene(this.root) : null) : scene;
    if (file && this.root && !path.isAbsolute(file)) file = path.join(this.root, file);
    if (file && !existsSync(file)) {
      throw new ToolError(`Scene ${file} does not exist.`, { code: 'not_found', hint: this.root ? `Scenes: ${projectScenes(this.root).join(', ') || 'none'}` : undefined });
    }
    const args = [];
    if (background) args.push('--background');
    if (file) args.push(file);
    args.push('--python', BOOTSTRAP, '--', '--gamestudio-bridge');
    if (background) args.push('--serve', '--parent-pid', String(process.pid));
    /** @type {string | null} */
    let logPath = null;
    /** @type {import('node:child_process').ChildProcess} */
    let child;
    if (background) {
      logPath = this.root ? path.join(this.root, '.gamestudio', 'mcp', 'blender-background.log') : null;
      /** @type {number | 'ignore'} */
      let fd = 'ignore';
      if (logPath) {
        mkdirSync(path.dirname(logPath), { recursive: true });
        fd = openSync(logPath, 'w');
      }
      try {
        child = spawn(exe.path, args, { cwd: this.root ?? undefined, env: this.env, stdio: ['ignore', fd, fd], windowsHide: true });
      } catch (error) {
        throw launchError(exe.path, error);
      } finally {
        if (typeof fd === 'number') closeSync(fd);
      }
    } else {
      try {
        child = spawn(exe.path, args, { cwd: this.root ?? undefined, env: this.env, stdio: 'ignore', detached: true });
      } catch (error) {
        throw launchError(exe.path, error);
      }
      child.unref();
    }
    const pid = child.pid;
    if (!pid) {
      // A missing or non-executable file: spawn returns at once without a pid and says why in 'error' (ENOENT, EACCES).
      const error = await new Promise((resolve) => {
        child.once('error', resolve);
        setTimeout(() => resolve(new Error('the process did not start')), 2000).unref();
      });
      throw launchError(exe.path, error);
    }
    let exited = /** @type {number | null} */ (null);
    child.once('exit', (code) => {
      exited = code ?? -1;
    });
    child.once('error', () => {
      exited = -1;
    });
    this.launched = { child, pid, background, scene: file, log: logPath, startedAt: Date.now() };
    this.log(`launched Blender pid ${pid} (${background ? 'background' : 'GUI'}): ${exe.path} ${args.join(' ')}`);

    const deadline = Date.now() + (timeoutMs ?? (background ? 120_000 : 180_000));
    while (Date.now() < deadline && !signal?.aborted) {
      if (exited !== null) {
        const tail = logPath && existsSync(logPath) ? readFileSync(logPath, 'utf8').trim().split(/\r?\n/).slice(-15).join('\n') : '';
        throw new ToolError(`Blender exited (code ${exited}) before its bridge started.`, {
          code: 'launch_failed',
          hint: 'Check that the GameStudio extension is installed for this Blender profile.',
          data: tail ? { log_tail: tail } : undefined,
        });
      }
      const { candidates } = this.discover();
      const mine = candidates.find((s) => s.pid === pid);
      if (mine) {
        try {
          await this.connect({ preferPid: pid });
          if (this.session?.pid === pid) {
            const seconds = Math.round((Date.now() - this.launched.startedAt) / 100) / 10;
            /** @type {Record<string, unknown>} */
            const result = { launched: true, pid, background, scene: file, blender: exe.path, blender_source: exe.source, ...(logPath && { log: logPath }), seconds };
            if (killed) result.stopped_unresponsive_pid = killed;
            if (restorePoint) result.restored = await this.call('restore_checkpoint', { id: restorePoint.id });
            this.crash = null;
            return result;
          }
        } catch {
          // keep waiting
        }
      }
      await sleep(250);
    }
    throw new ToolError(`Blender (pid ${pid}) started but its bridge did not answer within ${Math.round((deadline - this.launched.startedAt) / 1000)} s.`, {
      code: 'launch_timeout',
      hint: 'Is the GameStudio extension enabled in that Blender profile? Call status to retry the connection.',
    });
  }

  /** Stop a Blender that does not answer. @param {number} pid */
  async #kill(pid) {
    this.stopped.add(pid);
    try {
      process.kill(pid);
    } catch (error) {
      throw new ToolError(`Could not stop Blender pid ${pid}: ${error instanceof Error ? error.message : String(error)}.`, { code: 'kill_failed' });
    }
    const deadline = Date.now() + 15_000;
    while (pidAlive(pid) && Date.now() < deadline) await sleep(200);
    this.client?.close();
    this.client = null;
    if (pidAlive(pid)) throw new ToolError(`Blender pid ${pid} did not exit after being stopped.`, { code: 'kill_failed' });
    this.log(`stopped unresponsive Blender pid ${pid}`);
    return pid;
  }

  /** Close the connection and stop a background Blender this server launched. */
  async shutdown() {
    if (this.session) this.stopped.add(this.session.pid);
    const launched = this.launched;
    if (launched?.background && pidAlive(launched.pid)) {
      try {
        await this.client?.call('quit', {}, { timeoutMs: 3000 });
      } catch {
        // fall through to kill
      }
      await sleep(300);
      if (pidAlive(launched.pid)) launched.child.kill();
    }
    this.client?.close();
    this.client = null;
  }
}

/** @param {string} exe @param {unknown} error */
function launchError(exe, error) {
  return new ToolError(`Could not start ${exe}: ${error instanceof Error ? error.message : String(error)}.`, {
    code: 'launch_failed',
    hint: 'Set GAMESTUDIO_BLENDER to the Blender 5.2+ executable (blender.exe on Windows).',
  });
}
