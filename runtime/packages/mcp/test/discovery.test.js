import { spawn } from 'node:child_process';
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { BlenderManager } from '../src/blender.js';
import {
  defaultScene,
  findBlenderExecutable,
  findProjectRoot,
  projectScenes,
  readDevSession,
  readUserSessions,
  userBridgeDirs,
} from '../src/discovery.js';
import { ToolError } from '../src/util.js';
import { TOKEN, makeProject, startFakeBridge } from './helpers.js';

const DEAD_PID = 2 ** 22 + 7;
let project;
let home;

beforeEach(() => {
  project = makeProject({ scenes: ['scenes/main.blend', 'scenes/level2.blend', 'node_modules/x/ignored.blend'] });
  home = mkdtempSync(path.join(os.tmpdir(), 'gs_mcp_home_'));
});
afterEach(() => {
  project.cleanup();
  rmSync(home, { recursive: true, force: true });
});

/** A fake Blender user profile with a gamestudio user folder; returns that folder. */
function userFolder(resources, repo = 'system') {
  const dir = path.join(resources, 'extensions', '.user', repo, 'gamestudio');
  mkdirSync(dir, { recursive: true });
  return dir;
}

describe('project discovery', () => {
  it('finds the project root from a nested folder and lists its scenes', () => {
    const nested = path.join(project.root, 'src', 'systems');
    mkdirSync(nested, { recursive: true });
    expect(findProjectRoot(nested)).toBe(project.root);
    expect(findProjectRoot(os.tmpdir())).toBeNull();
    expect(projectScenes(project.root)).toEqual(['scenes/level2.blend', 'scenes/main.blend']);
    expect(defaultScene(project.root)).toBe(path.join(project.root, 'scenes', 'main.blend'));
  });

  it('lists only scenes/*.blend as levels, not other .blend files of the project', () => {
    for (const rel of ['assets/models/crate/crate.blend', 'assets/props.blend', 'scenes/sub/nested.blend']) {
      mkdirSync(path.dirname(path.join(project.root, rel)), { recursive: true });
      writeFileSync(path.join(project.root, rel), 'BLENDER');
    }
    expect(projectScenes(project.root)).toEqual(['scenes/level2.blend', 'scenes/main.blend']);
    rmSync(path.join(project.root, 'scenes'), { recursive: true, force: true });
    expect(projectScenes(project.root)).toEqual([]);
    expect(defaultScene(project.root)).toBeNull();
  });

  it('ignores a dev-server session whose process is gone', () => {
    const file = path.join(project.root, '.gamestudio', 'session.json');
    writeFileSync(file, JSON.stringify({ pid: DEAD_PID, editorPort: 7710, gamePort: 7711, token: TOKEN, url: 'http://localhost:5173/' }));
    expect(readDevSession(project.root)).toBeNull();
    writeFileSync(file, JSON.stringify({ pid: process.pid, editorPort: 7710, gamePort: 7711, token: TOKEN, url: 'http://localhost:5173/' }));
    expect(readDevSession(project.root)?.url).toBe('http://localhost:5173/');
  });
});

describe('user-level discovery', () => {
  it('searches BLENDER_USER_RESOURCES and the per-version config folders, newest version first', () => {
    const scratch = path.join(home, 'scratch');
    const a = userFolder(scratch);
    const appData = path.join(home, 'AppData');
    const v51 = userFolder(path.join(appData, 'Blender Foundation', 'Blender', '5.1'), 'user_default');
    const v52 = userFolder(path.join(appData, 'Blender Foundation', 'Blender', '5.2'), 'blender_org');
    const env = { BLENDER_USER_RESOURCES: scratch, APPDATA: appData, HOME: home };
    expect(userBridgeDirs(env, 'win32')).toEqual([a, v52, v51]);
    expect(userBridgeDirs({ GAMESTUDIO_BRIDGE_DIR: '/x', HOME: home }, 'linux')[0]).toBe('/x');
  });

  it('reads live sessions newest first and records the Blender executable', () => {
    const scratch = path.join(home, 'scratch');
    const dir = userFolder(scratch);
    const exe = path.join(home, 'blender.exe');
    writeFileSync(exe, '');
    writeFileSync(path.join(dir, 'bridge.json'), JSON.stringify({ pid: process.pid, port: 1, token: TOKEN, project: null, blend: null, binary: exe, updatedAt: '2026-10-10T00:00:00Z' }));
    const env = { BLENDER_USER_RESOURCES: scratch, APPDATA: path.join(home, 'none'), HOME: home };
    const sessions = readUserSessions(env);
    expect(sessions).toHaveLength(1);
    expect(sessions[0].file).toBe(path.join(dir, 'bridge.json'));
    expect(findBlenderExecutable(env)).toEqual({ path: exe, source: path.join(dir, 'bridge.json') });
    expect(findBlenderExecutable({ ...env, GAMESTUDIO_BLENDER: 'C:/explicit/blender.exe' })).toEqual({ path: 'C:/explicit/blender.exe', source: 'GAMESTUDIO_BLENDER' });
    // After Blender exits only last-blender.json is left: it still names the executable.
    rmSync(path.join(dir, 'bridge.json'));
    writeFileSync(path.join(dir, 'last-blender.json'), JSON.stringify({ binary: exe, blenderVersion: '5.2.2' }));
    expect(findBlenderExecutable(env)?.path).toBe(exe);
  });
});

describe('connecting to Blender', () => {
  let bridge;
  afterEach(async () => {
    await bridge?.close();
    bridge = null;
  });

  const env = () => ({ HOME: home, APPDATA: path.join(home, 'AppData'), BLENDER_USER_RESOURCES: path.join(home, 'scratch') });

  it('connects through the project discovery file and authenticates', async () => {
    bridge = await startFakeBridge({ status: () => ({ ok: 1 }) });
    bridge.writeDiscovery(project.root);
    const manager = new BlenderManager({ root: project.root, env: env() });
    expect(await manager.call('status')).toEqual({ ok: 1 });
    expect(manager.connected).toBe(true);
    expect(manager.onProject).toBe(true);
    expect(bridge.calls).toEqual([{ method: 'status', params: {} }]);
    await manager.shutdown();
  });

  it('says how to launch Blender when none is running', async () => {
    const manager = new BlenderManager({ root: project.root, env: env() });
    const error = await manager.connect().catch((e) => e);
    expect(error).toBeInstanceOf(ToolError);
    expect(error.code).toBe('blender_not_running');
    expect(error.message).toContain('launch_blender');
    expect(error.message).toContain('scenes/main.blend');
  });

  it('reports discovery files that point at sessions that do not answer', async () => {
    writeFileSync(path.join(project.root, '.gamestudio', 'blender.json'), JSON.stringify({ pid: process.pid, port: 9, token: TOKEN, project: project.root }));
    const error = await new BlenderManager({ root: project.root, env: env() }).connect().catch((e) => e);
    expect(error.code).toBe('blender_not_running');
    expect(error.hint).toContain('did not answer');
  });

  it('rejects a wrong token with the bridge\'s message', async () => {
    bridge = await startFakeBridge({}, { token: 'f'.repeat(32) });
    bridge.writeDiscovery(project.root, { token: TOKEN });
    const error = await new BlenderManager({ root: project.root, env: env() }).connect().catch((e) => e);
    expect(error.hint).toContain('Unauthorized');
  });

  it('uses a user-level session with no project, but leaves one on another project alone', async () => {
    bridge = await startFakeBridge({ status: () => 'free' });
    const dir = userFolder(path.join(home, 'scratch'));
    const write = (extra) => writeFileSync(path.join(dir, 'bridge.json'), JSON.stringify({ pid: process.pid, port: bridge.port, token: TOKEN, blend: null, ...extra }));
    write({ project: path.join(home, 'other-game') });
    const elsewhere = await new BlenderManager({ root: project.root, env: env() }).connect().catch((e) => e);
    expect(elsewhere.code).toBe('blender_not_running');
    expect(elsewhere.hint).toContain('another project');

    write({ project: null });
    const manager = new BlenderManager({ root: project.root, env: env() });
    expect(await manager.call('status')).toBe('free');
    expect(manager.onProject).toBe(false);
    await manager.shutdown();
  });

  it('maps bridge errors to agent errors and reconnects after a drop', async () => {
    bridge = await startFakeBridge({
      get_object: ({ name }) => {
        throw { code: -32000, message: `No object named '${name}'.`, data: { hint: 'Did you mean: Crate?' } };
      },
      ping: () => ({ pong: true }),
    });
    bridge.writeDiscovery(project.root);
    const manager = new BlenderManager({ root: project.root, env: env() });
    const error = await manager.call('get_object', { name: 'Crat' }).catch((e) => e);
    expect(error).toBeInstanceOf(ToolError);
    expect(error.toJSON()).toEqual({ error: "No object named 'Crat'.", code: 'blender_error', hint: 'Did you mean: Crate?' });
    bridge.dropConnections();
    await new Promise((r) => setTimeout(r, 50));
    expect(manager.connected).toBe(false);
    expect(await manager.call('ping')).toEqual({ pong: true });
    await manager.shutdown();
  });

  it('times out a request Blender never answers', async () => {
    bridge = await startFakeBridge({ render_views: () => new Promise(() => {}) });
    bridge.writeDiscovery(project.root);
    const manager = new BlenderManager({ root: project.root, env: env() });
    const error = await manager.call('render_views', {}, { timeoutMs: 100 }).catch((e) => e);
    expect(error.code).toBe('blender_timeout');
    expect(error.hint).toContain('busy');
    await manager.shutdown();
  });

  it('cannot launch without an executable', async () => {
    const manager = new BlenderManager({ root: project.root, env: { ...env(), ProgramFiles: path.join(home, 'none'), PATH: '' } });
    if (findBlenderExecutable(manager.env)) return; // a real install is found through the standard folders
    const error = await manager.launch({}).catch((e) => e);
    expect(error.code).toBe('blender_not_found');
    expect(error.hint).toContain('GAMESTUDIO_BLENDER');
  });

  it('says why an executable that is not there did not start', async () => {
    const missing = path.join(home, 'no-such-blender', process.platform === 'win32' ? 'blender.exe' : 'blender');
    const manager = new BlenderManager({ root: project.root, env: { ...env(), GAMESTUDIO_BLENDER: missing } });
    const error = await manager.launch({ background: true, timeoutMs: 3000 }).catch((e) => e);
    expect(error.code).toBe('launch_failed');
    expect(error.hint).toContain('GAMESTUDIO_BLENDER');
    // Linux and macOS give no pid and say why in 'error'; Windows starts and exits at once.
    if (process.platform !== 'win32') expect(error.message).toMatch(new RegExp(`${missing.replace(/[\\^$.*+?()[\]{}|/]/g, '\\$&')}.*ENOENT`));
  });
});

describe('crashes and hangs', () => {
  let bridge;
  let child;
  afterEach(async () => {
    await bridge?.close();
    bridge = null;
    if (child && child.exitCode === null) child.kill();
  });

  /** A stand-in "Blender" process whose pid the discovery file names. */
  async function fakeBlenderProcess() {
    child = spawn(process.execPath, ['-e', 'setTimeout(() => {}, 60000)'], { stdio: 'ignore' });
    await new Promise((resolve) => child.once('spawn', resolve));
    return child.pid;
  }

  function env(extra = {}) {
    return { HOME: home, APPDATA: path.join(home, 'AppData'), BLENDER_USER_RESOURCES: path.join(home, 'scratch'), TEMP: home, TMP: home, ...extra };
  }

  it('reports a crash during a call with the recovery choices', async () => {
    const pid = await fakeBlenderProcess();
    bridge = await startFakeBridge({
      render_views: () => {
        child.kill(); // Blender dies mid-call
        bridge.dropConnections();
        return new Promise(() => {});
      },
    });
    const blend = path.join(project.root, 'scenes', 'main.blend');
    bridge.writeDiscovery(project.root, { pid, startedAt: new Date(Date.now() - 60_000).toISOString() });
    // A newer autosave, a checkpoint and a crash log, as Blender and the bridge leave them.
    writeFileSync(path.join(home, `main_${pid}_autosave.blend`), 'BLENDER');
    writeFileSync(path.join(home, 'main.crash.txt'), '# Blender 5.2.2\nEXCEPTION_ACCESS_VIOLATION\nblender::draw_view3d\n');
    const cp = path.join(project.root, '.gamestudio', 'checkpoints', '20261010-120000-before-roof');
    mkdirSync(cp, { recursive: true });
    writeFileSync(path.join(cp, 'checkpoint.json'), JSON.stringify({ label: 'before roof', source: 'scenes/main.blend', file: 'main.blend', created: '2026-10-10T12:00:00+02:00' }));

    const manager = new BlenderManager({ root: project.root, env: env() });
    const error = await manager.call('render_views', {}).catch((e) => e);
    expect(error.code).toBe('blender_crashed');
    expect(error.message).toBe(`Blender (pid ${pid}) exited unexpectedly while running render_views: it crashed or was killed.`);
    expect(error.hint).toContain('launch_blender reopens scenes/main.blend as last saved');
    expect(error.hint).toContain(`main_${pid}_autosave.blend`);
    expect(error.hint).toContain('launch_blender {"checkpoint": "20261010-120000-before-roof"}');
    expect(error.data.crash_log.excerpt).toContain('EXCEPTION_ACCESS_VIOLATION');
    expect(blend).toBeTruthy();

    // Later calls, and a fresh server that only finds the leftover discovery file, say the same.
    const again = await manager.call('status').catch((e) => e);
    expect(again.code).toBe('blender_crashed');
    const fresh = await new BlenderManager({ root: project.root, env: env() }).connect().catch((e) => e);
    expect(fresh.code).toBe('blender_crashed');
    expect(fresh.message).not.toContain('while running');
  });

  it('notices a hung Blender and can stop it', async () => {
    const pid = await fakeBlenderProcess();
    let hung = false;
    bridge = await startFakeBridge({
      ping: () => (hung ? new Promise(() => {}) : { pong: true }),
      run_python: () => {
        hung = true;
        return new Promise(() => {});
      },
    });
    bridge.writeDiscovery(project.root, { pid });
    const exe = path.join(home, 'not-blender.exe');
    writeFileSync(exe, '');
    const manager = new BlenderManager({ root: project.root, env: env({ GAMESTUDIO_BLENDER: exe }) });
    expect(await manager.responsive(1000)).toBe(true);
    const timeout = await manager.call('run_python', { code: 'while True: pass' }, { timeoutMs: 100 }).catch((e) => e);
    expect(timeout.code).toBe('blender_timeout');
    expect(timeout.hint).toContain('kill_unresponsive');
    expect(timeout.hint).toContain('split the work');
    expect(await manager.responsive(200)).toBe(false);

    const running = await manager.launch({}).catch((e) => e);
    expect(running.code).toBe('already_running');
    expect(running.hint).toContain('kill_unresponsive');

    // kill_unresponsive stops the hung process, then starts Blender (here a file that is not
    // Blender, so the launch itself fails after the kill).
    const relaunch = await manager.launch({ killUnresponsive: true, timeoutMs: 3000 }).catch((e) => e);
    await new Promise((resolve) => (child.exitCode !== null || child.signalCode !== null ? resolve(undefined) : child.once('exit', resolve)));
    expect(child.exitCode !== null || child.signalCode !== null).toBe(true);
    expect(relaunch.code).toBe('launch_failed');
    expect(relaunch.hint).toContain('GAMESTUDIO_BLENDER');
  });
});
