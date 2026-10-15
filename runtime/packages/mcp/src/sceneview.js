// @ts-check
// SPDX-License-Identifier: MIT
// game_screenshot mode "scene": the level drawn by the game's own renderer from a viewpoint, without
// playing (docs/MCP.md, "Scene shots"). The server's headless game is switched to scene mode for the
// shot (`view.mode`: systems, physics and animation do not run, UI screens and the HUD are hidden),
// looks through an editor camera (`view.camera`: world→view and projection matrices in the game's
// Y-up space, three.js column-major order, SPEC §5.4) and goes back to how it was afterwards.
//
// Viewpoints are given in Blender coordinates, like compare_views: {from, at, lens?} poses a camera
// at `from` looking at `at` (the up axis is Blender's Z), and {camera} looks through a Blender camera
// (its lens from Blender, its world pose from the export the game shows, else from Blender).

import { ToolError, round, sleep } from './util.js';

/** compare_views' lens when the scene has no camera (mm on a 36 mm sensor). */
export const DEFAULT_LENS = 35;
export const SENSOR_MM = 36;
/** Blender's defaults for a camera the game does not have: clip range and orthographic scale. */
const DEFAULT_CLIP = /** @type {[number, number]} */ ([0.1, 1000]);
const DEFAULT_ORTHO_SCALE = 6;
/**
 * How long a scene shot waits (ms): for the headless game to join the live link, to load the export,
 * to answer view.mode (game.status with `view`), for game shaders' modules and textures (the shot is
 * taken anyway after that, with a warning) and for the editor camera to arrive. Tests shorten them.
 */
export const SCENE_TIMEOUTS = { hub: 15_000, load: 60_000, view: 5_000, settle: 15_000, camera: 3_000 };
/** Frames drawn with the editor camera before the shot. */
const SHOT_FRAMES = 3;

/**
 * @typedef {{ position: number[], right: number[], up: number[], back: number[] }} Pose
 *   A camera in the game's space (Y-up): its position and its local axes in world space (it looks down −back).
 * @typedef {{ view: number[], projection: number[], ortho: boolean, near: number, far: number }} ViewCamera
 *   The `view.camera` message's body (SPEC §5.4).
 * @typedef {{ from: number[], at: number[], lens?: number } | { camera: string }} ViewSpec
 */

// --- Pose maths ----------------------------------------------------------------------------------

/** @param {number[]} a @param {number[]} b */
const sub = (a, b) => [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
/** @param {number[]} a @param {number[]} b */
const dot = (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
/** @param {number[]} a @param {number[]} b */
const cross = (a, b) => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
/** @param {number[]} a */
const normalize = (a) => {
  const n = Math.hypot(a[0], a[1], a[2]);
  return n > 1e-12 ? [a[0] / n, a[1] / n, a[2] / n] : [0, 0, 0];
};

/** Blender world (Z-up) → game world (Y-up): (x, y, z) → (x, z, −y), the export's axis conversion. @param {number[]} v */
export const toGame = (v) => [v[0], v[2], -v[1]];

/**
 * A camera at `from` looking at `at`, both in Blender space, with Blender's Z as up (Y when it looks
 * straight up or down), as compare_views poses its temporary camera (bridge/compare.py `_axes`).
 * @param {number[]} from @param {number[]} at
 * @returns {Pose}
 */
export function lookAtPose(from, at) {
  const forward = normalize(sub(at, from));
  const hint = Math.abs(forward[2]) > 0.999 ? [0, 1, 0] : [0, 0, 1];
  const right = normalize(cross(forward, hint));
  const up = normalize(cross(right, forward));
  return { position: toGame(from), right: toGame(right), up: toGame(up), back: toGame(forward.map((v) => -v)) };
}

/**
 * A Blender object's pose from its location and XYZ Euler rotation in degrees (get_object's
 * `world_location` and `rotation_deg`: exact for an object without a parent or constraints).
 * @param {number[]} location @param {number[]} rotationDeg
 * @returns {Pose}
 */
export function eulerPose(location, rotationDeg) {
  const [a, b, c] = rotationDeg.map((d) => (d * Math.PI) / 180);
  const [ca, sa, cb, sb, cc, sc] = [Math.cos(a), Math.sin(a), Math.cos(b), Math.sin(b), Math.cos(c), Math.sin(c)];
  // Blender's XYZ order: X first, then Y, then Z (R = Rz · Ry · Rx); the columns are the local axes.
  const m = [
    [cc * cb, cc * sb * sa - sc * ca, cc * sb * ca + sc * sa],
    [sc * cb, sc * sb * sa + cc * ca, sc * sb * ca - cc * sa],
    [-sb, cb * sa, cb * ca],
  ];
  const column = (/** @type {number} */ i) => [m[0][i], m[1][i], m[2][i]];
  return { position: toGame(location), right: toGame(column(0)), up: toGame(column(1)), back: toGame(column(2)) };
}

/**
 * A camera's pose from its world matrix in the game (three.js `matrixWorld.elements`, column-major,
 * game space). Scale and shear are dropped.
 * @param {number[]} e
 * @returns {Pose}
 */
export function matrixPose(e) {
  const back = normalize([e[8], e[9], e[10]]);
  const right = normalize(cross([e[4], e[5], e[6]], back));
  return { position: [e[12], e[13], e[14]], right, up: cross(back, right), back };
}

/** World → view (three.js `matrixWorldInverse`), column-major. @param {Pose} pose */
export function viewMatrix({ position: p, right: r, up: u, back: b }) {
  return [r[0], u[0], b[0], 0, r[1], u[1], b[1], 0, r[2], u[2], b[2], 0, -dot(r, p), -dot(u, p), -dot(b, p), 1];
}

/**
 * The vertical field of view (radians) of Blender's camera view at this size: the sensor spans the
 * longer side (sensor fit Auto), as compare_views renders it.
 * @param {number} lens mm @param {[number, number]} size @param {number} [sensor] mm
 */
export function verticalFov(lens, [width, height], sensor = SENSOR_MM) {
  const wide = 2 * Math.atan(sensor / 2 / lens);
  return width >= height ? 2 * Math.atan((Math.tan(wide / 2) * height) / width) : wide;
}

/** OpenGL perspective projection (three.js `makePerspective`), column-major. @param {number} fovY radians @param {number} aspect @param {number} near @param {number} far */
export function perspective(fovY, aspect, near, far) {
  const f = 1 / Math.tan(fovY / 2);
  return [f / aspect, 0, 0, 0, 0, f, 0, 0, 0, 0, (far + near) / (near - far), -1, 0, 0, (2 * far * near) / (near - far), 0];
}

/**
 * OpenGL orthographic projection (three.js `makeOrthographic`), column-major. `scale` spans the
 * longer side, like Blender's Orthographic Scale.
 * @param {number} scale @param {[number, number]} size @param {number} near @param {number} far
 */
export function orthographic(scale, [width, height], near, far) {
  const half = scale / 2;
  const halfW = width >= height ? half : (half * width) / height;
  const halfH = width >= height ? (half * height) / width : half;
  return [1 / halfW, 0, 0, 0, 0, 1 / halfH, 0, 0, 0, 0, -2 / (far - near), 0, 0, 0, -(far + near) / (far - near), 1];
}

/** compare_views' clip range for a posed camera `distance` metres from what it looks at. @param {number} distance */
export const posedClip = (distance) => /** @type {[number, number]} */ ([Math.max(0.01, distance * 0.01), Math.max(1000, distance * 20)]);

/**
 * The view.camera message for a camera at {from, at} with `lens` (Blender space), drawn at `size`.
 * @param {number[]} from @param {number[]} at @param {number} lens @param {[number, number]} size
 * @returns {ViewCamera}
 */
export function posedCamera(from, at, lens, size) {
  const [near, far] = posedClip(Math.hypot(...sub(at, from)));
  return { view: viewMatrix(lookAtPose(from, at)), projection: perspective(verticalFov(lens, size), size[0] / size[1], near, far), ortho: false, near, far };
}

/** A world point through a view.camera's matrices: [view-space x, y, z] and NDC [x, y, z] (tests, checks). @param {ViewCamera} camera @param {number[]} point */
export function project(camera, point) {
  const m = camera.view;
  const v = [0, 1, 2].map((i) => m[i] * point[0] + m[4 + i] * point[1] + m[8 + i] * point[2] + m[12 + i]);
  const p = camera.projection;
  const clip = [0, 1, 2, 3].map((i) => p[i] * v[0] + p[4 + i] * v[1] + p[8 + i] * v[2] + p[12 + i]);
  return { view: v, ndc: [clip[0] / clip[3], clip[1] / clip[3], clip[2] / clip[3]] };
}

// --- Arguments -----------------------------------------------------------------------------------

const VIEW_HINT = 'Example: {"mode": "scene", "view": {"from": [8, -8, 5], "at": [0, 0, 1]}} or {"mode": "scene", "view": {"camera": "Camera"}} (Blender coordinates).';

/**
 * Checks a scene shot's view: {from, at, lens?} or {camera}.
 * @param {any} view
 * @returns {ViewSpec}
 */
export function checkView(view) {
  if (!view) throw new ToolError('mode "scene" needs a view: where to look from.', { code: 'invalid_arguments', hint: VIEW_HINT });
  if (view.camera !== undefined) {
    if (view.from !== undefined || view.at !== undefined || view.lens !== undefined) {
      throw new ToolError('view.camera goes alone: the camera has its own pose and lens.', { code: 'invalid_arguments', hint: VIEW_HINT });
    }
    return { camera: view.camera };
  }
  if (!view.from || !view.at) throw new ToolError('view needs from and at (or camera).', { code: 'invalid_arguments', hint: VIEW_HINT });
  if (Math.hypot(...sub(view.at, view.from)) < 1e-6) throw new ToolError('view.from and view.at are the same point.', { code: 'invalid_arguments', hint: VIEW_HINT });
  return { from: view.from, at: view.at, ...(view.lens !== undefined && { lens: view.lens }) };
}

// --- The shot ------------------------------------------------------------------------------------

/**
 * @typedef {import('./context.js').Context} Context
 * @typedef {import('./hub.js').GameInfo} GameInfo
 * @typedef {{ showsExport: (g: GameInfo, want: any, sentAt?: number) => boolean, exportSummary: (result: any) => Record<string, any> }} ExportHelpers
 */

/**
 * game_screenshot {mode: "scene"}: exports (unless export: false), makes sure the headless game shows
 * that export, switches it to scene mode, sets the editor camera, waits for game shaders' modules and
 * textures and a few frames, shoots, and puts the game back as it was (view mode, camera, play state,
 * page size).
 * @param {Context} ctx
 * @param {Record<string, any>} args the tool's arguments
 * @param {{ signal: AbortSignal }} call
 * @param {ExportHelpers} helpers
 */
export async function sceneScreenshot(ctx, args, call, { showsExport, exportSummary }) {
  const view = checkView(args.view);
  const dev = await ctx.ensureDevServer(call.signal);
  await ctx.hub.ensure();
  /** @type {string[]} */
  const warnings = [];
  /** @type {Record<string, any>} */
  const notes = {};
  /** @type {any} */
  let exported = null;
  if (args.export !== false) {
    try {
      exported = await ctx.blender.call('export_scene', { force: false }, { signal: call.signal });
      notes.export = exportSummary(exported);
    } catch (error) {
      if (!(error instanceof ToolError) || error.code !== 'blender_not_running') throw error;
      notes.export_skipped = 'Blender is not running; the game shows the last export.';
    }
  }
  const game = await ctx.browser.ensureGame(dev.url);
  const page = game.page;
  const marker = game.logs.at(-1);
  const connected = await ctx.hub.waitFor(() => ctx.hub.gamePeers().some((g) => g.client.includes(game.tag)), SCENE_TIMEOUTS.hub);
  const peer = ctx.hub.gamePeers().find((g) => g.client.includes(game.tag));
  if (!connected || !peer) {
    throw new ToolError('The headless game did not connect to the live link.', { code: 'game_not_connected', hint: 'Call status; restart the dev server if this persists.' });
  }
  const info = () => ctx.hub.gamePeers().find((g) => g.peer === peer.peer) ?? peer;
  if (exported && !showsExport(info(), exported)) {
    const sentAt = Date.now();
    ctx.hub.send({ ...exported.scene_load, to: peer.peer });
    const loaded = await ctx.hub.waitFor(() => showsExport(info(), exported, sentAt), SCENE_TIMEOUTS.load);
    if (!loaded) warnings.push(`The headless game did not report loading export rev ${exported.rev} yet; the shot may show an older one.`);
  }

  const size = /** @type {[number, number]} */ (args.size ?? game.size);
  const before = await page.evaluate(viewState);
  /** How the game was: its view mode, play state and (in scene mode) editor camera, put back afterwards. */
  const previous = { view: before.view ?? info().view ?? 'game', state: before.state ?? info().state, menuPaused: before.menuPaused, animate: before.animate, camera: before.camera };
  const own = page.viewport();
  const resized = Boolean(own && (own.width !== size[0] || own.height !== size[1]));
  let switched = previous.view === 'scene';
  /** @type {Awaited<ReturnType<typeof resolveCamera>> | null} */
  let camera = null;
  let image = '';
  try {
    if (resized) await page.setViewport({ ...own, width: size[0], height: size[1] });
    if (!switched) {
      ctx.hub.send({ t: 'view.mode', mode: 'scene', animate: false, to: peer.peer });
      switched = await waitForView(ctx, page, peer.peer, 'scene');
      if (!switched) {
        throw new ToolError("This project's engine did not switch to scene mode (view.mode), so it cannot draw a scene shot.", {
          code: 'scene_mode_unsupported',
          hint: 'Update @gamestudio/engine; meanwhile use game_screenshot without mode, or compare_views.',
        });
      }
    }
    // Scene mode shows the loaded state: the reset it starts must finish before a camera entity is read.
    const settled = await page.evaluate(settleShaders, SCENE_TIMEOUTS.settle);
    if (!settled.ok) warnings.push(`Game shaders were still loading after ${SCENE_TIMEOUTS.settle / 1000} s (${settled.pending}); the shot may show some materials without them.`);
    camera = await resolveCamera(ctx, page, view, size, exported?.scene ?? info().scene, call.signal);
    warnings.push(...camera.warnings);
    ctx.hub.send({ t: 'view.camera', ...camera.message, to: peer.peer });
    await page.evaluate(hideOverlays);
    const applied = await page.evaluate(waitForCamera, camera.message.view, SCENE_TIMEOUTS.camera);
    if (!applied) warnings.push('The game did not confirm the editor camera; the shot was taken anyway.');
    await page.evaluate(frames, SHOT_FRAMES);
    if (args.wait_ms) await sleep(args.wait_ms);
    image = Buffer.from(await page.screenshot({ type: 'png' })).toString('base64');
  } finally {
    await page.evaluate(showOverlays).catch(() => {});
    if (resized) await page.setViewport(own).catch(() => {});
    if (previous.view === 'scene') {
      if (previous.camera) ctx.hub.send({ t: 'view.camera', ...previous.camera, to: peer.peer });
    } else if (switched) {
      ctx.hub.send({ t: 'view.mode', mode: 'game', ...(typeof previous.animate === 'boolean' && { animate: previous.animate }), to: peer.peer });
      // Scene mode reset the game to its loaded state: a game that was running runs again, from the start.
      if (previous.state === 'playing' || previous.state === 'paused') ctx.hub.send({ t: 'play.start', to: peer.peer });
      if (previous.state === 'paused' && !previous.menuPaused) ctx.hub.send({ t: 'play.pause', paused: true, to: peer.peer });
    }
  }
  // Back as it was before the result goes out, so the next call sees the game's own view.
  const restarted = previous.view !== 'scene' && (previous.state === 'playing' || previous.state === 'paused');
  if (previous.view === 'scene') {
    if (previous.camera) await page.evaluate(waitForCamera, previous.camera.view, SCENE_TIMEOUTS.camera).catch(() => {});
  } else {
    await waitForView(ctx, page, peer.peer, 'game');
    if (restarted) await waitForState(page, previous.state === 'paused' && !previous.menuPaused ? ['paused'] : ['playing', 'paused']);
  }

  const now = await page.evaluate(() => {
    const gs = /** @type {any} */ (window).__gs;
    return { state: gs?.state ?? null, scene: gs?.game?.scene?.name ?? null, rev: gs?.game?.scene?.rev ?? null };
  });
  const since = marker ? game.logs.slice(game.logs.indexOf(marker) + 1) : game.logs;
  const errors = since.filter((l) => l.level === 'error').slice(-5).map((l) => l.msg);
  const shaderWarnings = since.filter((l) => l.level === 'warn' && /game shader/i.test(l.msg)).slice(-5).map((l) => l.msg);
  /** @type {Record<string, any>} */
  const data = {
    url: dev.url,
    mode: 'scene',
    view: /** @type {NonNullable<typeof camera>} */ (camera).info,
    size,
    opened_now: game.opened,
    ...notes,
    game: { scene: now.scene, rev: now.rev },
    restored: { view: previous.view, state: now.state, ...(restarted && { restarted: true }) },
    ...(warnings.length && { warnings }),
    ...(shaderWarnings.length && { shader_warnings: shaderWarnings }),
    ...(errors.length && { page_errors: errors }),
  };
  return { data, image };
}

/**
 * Waits until the game reports `mode` (game.status `view` on the hub, or the page's own view).
 * @param {Context} ctx @param {any} page @param {string} peer @param {'scene' | 'game'} mode
 */
async function waitForView(ctx, page, peer, mode) {
  const deadline = Date.now() + SCENE_TIMEOUTS.view;
  while (Date.now() < deadline) {
    const now = await page.evaluate(viewState).catch(() => null);
    if (now?.view === mode) return true;
    if (!now?.view && ctx.hub.gamePeers().find((g) => g.peer === peer)?.view === mode) return true;
    await ctx.hub.waitFor(() => ctx.hub.gamePeers().find((g) => g.peer === peer)?.view === mode, 100);
  }
  return false;
}

/**
 * Waits until the page's play state is one of `states` (or the view timeout).
 * @param {any} page @param {string[]} states
 */
async function waitForState(page, states) {
  const deadline = Date.now() + SCENE_TIMEOUTS.view;
  while (Date.now() < deadline) {
    const now = await page.evaluate(viewState).catch(() => null);
    if (!now || states.includes(now.state)) return;
    await sleep(50);
  }
}

/**
 * The view.camera message and what the result says about it.
 * @param {Context} ctx @param {any} page @param {ViewSpec} view @param {[number, number]} size
 * @param {string | null | undefined} scene @param {AbortSignal} signal
 * @returns {Promise<{ message: ViewCamera, info: Record<string, any>, warnings: string[] }>}
 */
async function resolveCamera(ctx, page, view, size, scene, signal) {
  if ('from' in view) {
    const scenes = view.lens === undefined ? await sceneCameraLens(ctx, scene, signal) : null;
    const lens = view.lens ?? scenes ?? DEFAULT_LENS;
    const from = view.lens !== undefined ? null : scenes !== null ? 'scene camera' : 'default';
    return { message: posedCamera(view.from, view.at, lens, size), info: { from: view.from, at: view.at, lens: round(lens, 2), ...(from && { lens_from: from }) }, warnings: [] };
  }
  /** @type {string[]} */
  const warnings = [];
  /** @type {any} */
  let object = null;
  try {
    object = await ctx.blender.call('get_object', { name: view.camera }, { signal });
  } catch (error) {
    if (!(error instanceof ToolError) || error.code !== 'blender_not_running') throw error;
  }
  if (object && object.type !== 'CAMERA') {
    throw new ToolError(`'${object.name}' is a ${object.type} object, not a camera.`, { code: 'invalid_arguments', hint: 'Name a camera object, or pose a view with {"from": [x, y, z], "at": [x, y, z]}.' });
  }
  /** @type {any} */
  const found = await page.evaluate(gameCamera, object?.id ?? null, object?.name ?? view.camera);
  if (!object && !found) {
    throw new ToolError(`No camera '${view.camera}' in the game, and Blender is not running to ask.`, { code: 'invalid_arguments', hint: 'Start Blender (launch_blender), or pose a view with {"from": [x, y, z], "at": [x, y, z]}.' });
  }
  const pose = found ? matrixPose(found.elements) : eulerPose(object.world_location ?? object.location, object.rotation_deg);
  if (!found && object.parent) warnings.push(`Camera '${object.name}' is parented to '${object.parent}' and not in the game's export; it was placed by its own transform. Export the scene to include it.`);
  const [near, far] = found ? [found.near, found.far] : DEFAULT_CLIP;
  const ortho = object ? object.camera?.type === 'ORTHO' : Boolean(found.ortho);
  /** @type {number[]} */
  let projection;
  /** @type {Record<string, any>} */
  const info = { camera: object?.name ?? found.name, ...((object?.id ?? found?.id) && { id: object?.id ?? found.id }), pose_from: found ? 'game export' : 'blender' };
  if (ortho) {
    const scale = found?.ortho ? Math.max(found.ortho.width, found.ortho.height) : DEFAULT_ORTHO_SCALE;
    if (!found?.ortho) warnings.push(`Camera '${info.camera}' is orthographic but not in the game's export; drawn with scale ${DEFAULT_ORTHO_SCALE}.`);
    projection = orthographic(scale, size, near, far);
    info.ortho_scale = round(scale, 3);
  } else if (object?.camera?.lens) {
    projection = perspective(verticalFov(object.camera.lens, size), size[0] / size[1], near, far);
    info.lens = round(object.camera.lens, 2);
  } else {
    projection = perspective(((found.fov ?? 50) * Math.PI) / 180, size[0] / size[1], near, far);
    info.fov_deg = round(found.fov ?? 50, 2);
  }
  return { message: { view: viewMatrix(pose), projection, ortho, near, far }, info, warnings };
}

/**
 * The scene camera's lens, like compare_views' default: the export's active camera, asked in Blender.
 * Null when there is none or Blender is not running.
 * @param {Context} ctx @param {string | null | undefined} scene @param {AbortSignal} signal
 */
async function sceneCameraLens(ctx, scene, signal) {
  if (!ctx.root || !scene) return null;
  try {
    const { readFileSync } = await import('node:fs');
    const path = await import('node:path');
    const manifest = JSON.parse(readFileSync(path.join(ctx.root, '.gamestudio', 'export', `${scene}.scene.json`), 'utf8'));
    const id = manifest?.settings?.activeCamera;
    if (typeof id !== 'string' || !id) return null;
    const camera = await ctx.blender.call('get_object', { name: id }, { signal });
    return camera?.type === 'CAMERA' && typeof camera.camera?.lens === 'number' ? camera.camera.lens : null;
  } catch {
    return null;
  }
}

// --- Functions evaluated in the page -------------------------------------------------------------

/**
 * The game's view mode (`game.view`), play state, shader animation setting and, in scene mode, the
 * editor camera it draws with as a view.camera payload (null before the editor sent one).
 */
function viewState() {
  const gs = /** @type {any} */ (window).__gs;
  const game = gs?.game;
  const view = gs?.view ?? game?.view ?? null;
  /** @type {any} */
  let camera = null;
  const drawn = view === 'scene' ? game?.viewCamera : null;
  if (drawn?.isCamera && drawn !== game?.three?.camera) {
    drawn.updateMatrixWorld?.();
    // `source`: the projection as the editor sent it (projectionMatrix may be adapted to the renderer's depth range).
    const projection = drawn.source?.elements ?? drawn.projectionMatrix.elements;
    camera = { view: Array.from(drawn.matrixWorldInverse.elements), projection: Array.from(projection), ortho: Boolean(drawn.isOrthographicCamera), near: drawn.near, far: drawn.far };
  }
  return {
    view: view === 'scene' || view === 'game' ? view : null,
    state: gs?.state ?? null,
    // Paused by an open menu (a title screen): Play brings the menu, and its pause, back by itself.
    menuPaused: Boolean(game?.ui?.holdsPause),
    animate: typeof game?.animate === 'boolean' ? game.animate : null,
    camera,
  };
}

/**
 * Waits for queued loads and resets, then for game shaders' modules and asset textures (or the
 * timeout). Engine internals are read defensively: an engine without them counts as settled.
 * @param {number} timeoutMs
 */
async function settleShaders(timeoutMs) {
  const game = /** @type {any} */ (window).__gs?.game;
  const deadline = performance.now() + timeoutMs;
  const left = () => Math.max(0, deadline - performance.now());
  const timeout = () => new Promise((resolve) => setTimeout(resolve, left()));
  try {
    if (typeof game?.idle === 'function') await Promise.race([game.idle(), timeout()]);
  } catch {
    // a failed load is reported by the game itself
  }
  // Engines with game.shadersReady() say when every bound shader's module and textures are in
  // (it never rejects; failed loads count as done).
  if (typeof game?.shadersReady === 'function') {
    const ready = await Promise.race([game.shadersReady().then(() => true, () => true), timeout().then(() => false)]);
    return ready ? { ok: true, pending: '' } : { ok: false, pending: 'shader modules or textures' };
  }
  // Older engines: MaterialShaders (scene/materials.ts) `loading` holds definitions whose module is
  // loading, `textures` the asset textures, each with the promise of its load (failures resolve to null).
  const shaders = game?.shaders;
  for (;;) {
    /** @type {any[]} */
    const textures = shaders?.textures instanceof Map ? [...shaders.textures.values()] : [];
    const promises = textures.map((t) => t?.promise).filter((p) => p && typeof p.then === 'function');
    const loaded = await Promise.race([Promise.all(promises).then(() => true, () => true), timeout().then(() => false)]);
    const loading = typeof shaders?.loading?.size === 'number' ? shaders.loading.size : 0;
    if (loaded && loading === 0) return { ok: true, pending: '' };
    if (left() <= 0) return { ok: false, pending: [loading && `${loading} shader module(s)`, !loaded && 'textures'].filter(Boolean).join(', ') };
    await new Promise((resolve) => setTimeout(resolve, 50));
  }
}

/**
 * A camera entity in the game, found by gs id or name: its world matrix (game space), lens-free
 * projection parameters and clip range. Null when the game has no such camera.
 * @param {string | null} id @param {string} name
 */
function gameCamera(id, name) {
  const gs = /** @type {any} */ (window).__gs;
  const entity = (id && gs?.world?.get?.(id)) || gs?.world?.findByName?.(name) || null;
  /** @type {any} */
  let camera = null;
  entity?.object3d?.traverse((/** @type {any} */ o) => {
    if (!camera && o.isCamera) camera = o;
  });
  if (!camera) return null;
  camera.updateWorldMatrix(true, false);
  const zoom = camera.zoom || 1;
  return {
    id: entity.id,
    name: entity.name,
    elements: Array.from(camera.matrixWorld.elements),
    fov: camera.isPerspectiveCamera ? camera.fov : null,
    near: camera.near,
    far: camera.far,
    ortho: camera.isOrthographicCamera ? { width: (camera.right - camera.left) / zoom, height: (camera.top - camera.bottom) / zoom } : null,
  };
}

/**
 * Resolves true once the camera the game draws with (`game.viewCamera`) has `view` as its world→view
 * matrix (the view.camera message arrived), false after `timeoutMs`.
 * @param {number[]} view @param {number} timeoutMs
 */
function waitForCamera(view, timeoutMs) {
  const game = /** @type {any} */ (window).__gs?.game;
  const close = () => {
    const camera = game?.viewCamera ?? game?.three?.camera;
    if (!camera?.matrixWorldInverse) return false;
    camera.updateMatrixWorld?.();
    const e = camera.matrixWorldInverse.elements;
    return view.every((v, i) => Math.abs(e[i] - v) < 1e-3 * Math.max(1, Math.abs(v)));
  };
  return new Promise((resolve) => {
    const started = performance.now();
    const tick = () => {
      if (close()) resolve(true);
      else if (performance.now() - started > timeoutMs) resolve(false);
      else requestAnimationFrame(tick);
    };
    tick();
  });
}

/** Resolves after `n` animation frames. @param {number} n */
function frames(n) {
  return new Promise((resolve) => {
    let i = 0;
    const tick = () => (++i >= n ? resolve(undefined) : requestAnimationFrame(tick));
    requestAnimationFrame(tick);
  });
}

/** Hides every page element over the game's canvas (the HUD, menus, overlays) until showOverlays. */
function hideOverlays() {
  const w = /** @type {any} */ (window);
  const canvas = w.__gs?.game?.canvas ?? document.querySelector('canvas');
  /** @type {[HTMLElement, string][]} */
  const hidden = [];
  for (const el of document.body.querySelectorAll('*')) {
    if (el === canvas || (canvas && el.contains(canvas)) || !(el instanceof HTMLElement)) continue;
    hidden.push([el, el.style.visibility]);
    el.style.visibility = 'hidden';
  }
  w.__gsMcpHidden = [...(w.__gsMcpHidden ?? []), ...hidden];
  return hidden.length;
}

/** Shows what hideOverlays hid, as it was. */
function showOverlays() {
  const w = /** @type {any} */ (window);
  for (const [el, visibility] of (w.__gsMcpHidden ?? []).reverse()) el.style.visibility = visibility;
  delete w.__gsMcpHidden;
}
