// @ts-check
// SPDX-License-Identifier: MIT
// What every tool handler can use: the project, Blender, the hub, the headless browser.

import { mkdirSync, writeFileSync } from 'node:fs';
import net from 'node:net';
import path from 'node:path';
import { BlenderManager } from './blender.js';
import { GameBrowser } from './browser.js';
import { readDevSession } from './discovery.js';
import { HubClient } from './hub.js';
import { ToolError, sleep } from './util.js';

const DEVSERVER_START_TIMEOUT_MS = 120_000;

/** @param {number} port */
function portOpen(port) {
  return new Promise((resolve) => {
    const socket = net.connect({ host: '127.0.0.1', port });
    const done = (/** @type {boolean} */ ok) => {
      socket.destroy();
      resolve(ok);
    };
    socket.setTimeout(500, () => done(false));
    socket.once('connect', () => done(true));
    socket.once('error', () => done(false));
  });
}

export class Context {
  /**
   * @param {{ root: string | null, env?: NodeJS.ProcessEnv, log?: (line: string) => void,
   *   blender?: BlenderManager, hub?: HubClient, browser?: GameBrowser, version?: string }} options
   */
  constructor({ root, env = process.env, log = () => {}, blender, hub, browser, version = '0.0.0' }) {
    this.root = root;
    this.env = env;
    this.log = log;
    this.version = version;
    const client = `gamestudio-mcp ${version}`;
    this.blender = blender ?? new BlenderManager({ root, env, log, client });
    this.hub = hub ?? new HubClient({ root, log, client });
    this.browser = browser ?? new GameBrowser({ env, log });
  }

  requireRoot() {
    if (!this.root) {
      throw new ToolError('No GameStudio project: the MCP server was not started inside a project folder (one with gamestudio.json).', {
        code: 'no_project',
        hint: 'Start Claude Code in the game project folder, or pass --project <dir> to gamestudio-mcp in .mcp.json.',
      });
    }
    return this.root;
  }

  /**
   * An absolute path for a path the agent gave (relative paths are relative to the project).
   * @param {string} p
   */
  resolvePath(p) {
    if (path.isAbsolute(p)) return path.normalize(p);
    return path.resolve(this.requireRoot(), p);
  }

  /** The running dev server's session, or null. */
  async devSession() {
    if (!this.root) return null;
    const session = readDevSession(this.root);
    if (!session) return null;
    return (await portOpen(session.editorPort)) ? session : null;
  }

  /**
   * The dev server's session, starting the server through Blender when it is not running.
   * @param {AbortSignal} [signal]
   */
  async ensureDevServer(signal) {
    const root = this.requireRoot();
    const running = await this.devSession();
    if (running) return running;
    try {
      await this.blender.call('devserver', { action: 'start' });
    } catch (error) {
      if (error instanceof ToolError && error.code === 'blender_not_running') {
        throw new ToolError('The dev server is not running, and Blender (which starts it) is not running either.', {
          code: 'devserver_not_running',
          hint: 'Call launch_blender first: Blender starts the dev server (and sets the project up the first time).',
        });
      }
      throw error;
    }
    const deadline = Date.now() + DEVSERVER_START_TIMEOUT_MS;
    while (Date.now() < deadline && !signal?.aborted) {
      const session = await this.devSession();
      if (session) {
        this.log(`dev server ready at ${session.url}`);
        return session;
      }
      const status = await this.blender.call('devserver', { action: 'status' }).catch(() => null);
      if (status && !status.starting && !status.running && status.last_error) {
        throw new ToolError(`The dev server failed to start: ${String(status.last_error).split('\n')[0]}`, {
          code: 'devserver_failed',
          hint: `See ${status.log}. A common cause is game packages that are not set up yet: GameStudio's setup installs them when the project is opened or played in Blender.`,
          data: status.log_tail ? { log_tail: status.log_tail } : undefined,
        });
      }
      await sleep(500);
    }
    throw new ToolError(`The dev server did not start within ${DEVSERVER_START_TIMEOUT_MS / 1000} s.`, {
      code: 'devserver_timeout',
      hint: `See ${path.join(root, '.gamestudio', 'devserver.log')}.`,
    });
  }

  /**
   * Writes a base64 PNG under <project>/.gamestudio/mcp/<folder>/ and returns its path.
   * @param {string} folder
   * @param {string} name
   * @param {string} base64
   */
  saveImage(folder, name, base64) {
    const dir = path.join(this.requireRoot(), '.gamestudio', 'mcp', folder);
    mkdirSync(dir, { recursive: true });
    const stamp = new Date().toISOString().replace(/[-:]/g, '').replace(/\..*/, '').replace('T', '-');
    const file = path.join(dir, `${stamp}-${name.replace(/[^\w.-]+/g, '_')}.png`);
    writeFileSync(file, Buffer.from(base64, 'base64'));
    return file;
  }

  async shutdown() {
    this.hub.close();
    await this.browser.close();
    await this.blender.shutdown();
  }
}
