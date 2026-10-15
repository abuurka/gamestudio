// @ts-check
// SPDX-License-Identifier: MIT
// compare_views: does the game show what Blender shows? Blender renders a camera view, the game
// is opened headless at the same size, stopped (its loaded state), looked at through the same
// camera and screenshotted; core/compare.py scores the two and draws them side by side.

import { withImages } from '../protocol.js';
import { ToolError, sleep } from '../util.js';
import { READ, VEC3, object } from './common.js';

/** @typedef {import('./common.js').Context} Context */

const READY_TIMEOUT_MS = 60_000;

/** Resolves after `n` animation frames of the page. @param {any} page @param {number} n */
function frames(page, n) {
  return page.evaluate(
    (/** @type {number} */ count) =>
      new Promise((resolve) => {
        let i = 0;
        const tick = () => (++i >= count ? resolve(undefined) : requestAnimationFrame(tick));
        requestAnimationFrame(tick);
      }),
    n,
  );
}

/** @param {any} page */
async function closePage(page) {
  await page.goto('about:blank', { timeout: 5000 }).catch(() => {});
  await page.close().catch(() => {});
}

/**
 * The game's screenshot of the view Blender rendered (the recipe in core/compare.py).
 * @param {Context} ctx
 * @param {string} url the dev server's URL
 * @param {{ scene: string, camera_id: string | null, active_camera: boolean, size: [number, number], frames: number,
 *   pose?: { position: number[], quaternion: number[], fov_deg: number, near: number, far: number } }} want
 * @param {number} rev the export the game must show
 */
async function gameScreenshot(ctx, url, want, rev, keepPost = false) {
  const { page, logs } = await ctx.browser.openGame(`${url}?scene=${encodeURIComponent(want.scene)}`, { size: want.size });
  try {
    await page
      .waitForFunction((/** @type {number} */ r) => /** @type {any} */ (window).__gs?.ready === true && /** @type {any} */ (window).__gs.game.scene?.rev === r, { timeout: READY_TIMEOUT_MS, polling: 100 }, rev)
      .catch(() => {
        throw new ToolError(`The game did not load export rev ${rev} of '${want.scene}'.`, { code: 'game_not_ready', hint: 'Check game_logs; export_scene again.' });
      });
    // stop() reloads the loaded state and picks the camera again: wait for it, then a few frames.
    await page.evaluate(() => /** @type {any} */ (window).__gs.game.stop());
    await page.waitForFunction(() => /** @type {any} */ (window).__gs.state === 'stopped', { timeout: 10_000, polling: 50 }).catch(() => {});
    await sleep(300);
    await frames(page, 2);
    let cameraFound = true;
    if (want.pose) {
      // A camera Blender posed for this call: it is not in the export, so build the same one here.
      cameraFound = await page.evaluate((/** @type {any} */ pose) => {
        const gs = /** @type {any} */ (window).__gs;
        const base = gs.game.three.camera;
        if (!base?.isPerspectiveCamera) return false;
        const camera = base.clone(false);
        Object.assign(camera, { fov: pose.fov_deg, near: pose.near, far: pose.far, zoom: 1, filmOffset: 0 });
        camera.clearViewOffset?.();
        camera.position.fromArray(pose.position);
        camera.quaternion.fromArray(pose.quaternion);
        camera.updateProjectionMatrix();
        camera.updateMatrixWorld(true);
        gs.game.three.camera = camera;
        return true;
      }, want.pose);
    } else if (!want.active_camera) {
      cameraFound = await page.evaluate((/** @type {string} */ id) => {
        const gs = /** @type {any} */ (window).__gs;
        const entity = gs.world.get(id);
        /** @type {any} */
        let camera = null;
        entity?.object3d.traverse((/** @type {any} */ o) => {
          if (!camera && o.isCamera) camera = o;
        });
        if (camera) gs.game.three.camera = camera;
        return Boolean(camera);
      }, want.camera_id);
    }
    // Blender renders no post effects: switch the game's off for the shot (unless asked), and say which were on.
    /** @type {string[]} */
    const post = await page.evaluate((/** @type {boolean} */ keep) => {
      const p = /** @type {any} */ (window).__gs.game.post;
      const s = p?.settings;
      if (!s) return [];
      const on = ['bloom', 'vignette'].filter((k) => s[k]);
      if (s.contrast !== 1) on.push('contrast');
      if (s.saturation !== 1) on.push('saturation');
      if (Array.isArray(s.tint) && s.tint.slice(0, 3).some((/** @type {number} */ v) => v !== 1)) on.push('tint');
      if (!keep && on.length) p.set({ bloom: false, vignette: false, contrast: 1, saturation: 1, tint: [1, 1, 1, 1] });
      return on;
    }, keepPost);
    // Only the 3D view is compared: close the game UI canvases (menus and HUDs from ui/ui.blend, drawn into the
    // game's canvas: docs/GAME-UI.md), and hide the HUD and any other page elements over the canvas.
    await page.evaluate(() => {
      const gs = /** @type {any} */ (window).__gs;
      try {
        const open = gs.test?.state?.({ ids: [] })?.ui?.open;
        for (const name of Array.isArray(open) ? open : []) gs.message({ t: 'ui.close', canvas: name });
      } catch {
        // an engine without UI canvases
      }
      const canvas = document.querySelector('canvas');
      for (const el of document.body.querySelectorAll('*')) {
        if (el !== canvas && !(canvas && el.contains(canvas)) && el instanceof HTMLElement) el.style.visibility = 'hidden';
      }
    });
    await frames(page, want.frames);
    const png = await ctx.browser.screenshot(page);
    return { png, cameraFound, post, errors: logs.filter((l) => l.level === 'error').slice(-5).map((l) => l.msg) };
  } finally {
    await closePage(page);
  }
}

/** @param {Context} ctx @returns {import('./common.js').ToolDefinition[]} */
export function compareTools(ctx) {
  return [
    {
      name: 'compare_views',
      title: 'Compare Blender and game views',
      description:
        'Does the game look like the Blender scene? Exports the scene, renders a camera view in Blender (EEVEE by ' +
        'default), opens the game headless at the same size in its loaded state through the same camera, screenshots it ' +
        'and compares the two. Returns verdict (match / close / different), normalized_diff (0 = same content after ' +
        'matching brightness and contrast; match <= 0.12, close <= 0.25), detail_ratio (edge detail of the game over ' +
        'Blender: well below 1 means detail went missing, such as flat procedural materials or low-res textures; well ' +
        'above 1, noise), worst_cells (the grid cells that differ most) and mean_abs_diff, plus one image [Blender | game ' +
        '| difference x4]. Judge on normalized_diff and detail_ratio. mean_abs_diff is the raw difference, before ' +
        'brightness and contrast are matched: the game tone-maps with the scene\'s own view transform and exposure ' +
        '(Color Management), so it stays small for a good match unless the export warned that the view transform has ' +
        'no game equivalent. verdict_reason says which number decided the ' +
        'verdict and decided_by lists those metrics; when detail_ratio decided, detail_cells are the grid cells whose ' +
        'detail differs most. camera: a camera object (default: the scene camera), or {"from": [x, y, z], "at": [x, y, ' +
        'z], "lens": 35} for a temporary camera (Blender space, removed afterwards). view: "iso" | "front" | "back" | ' +
        '"left" | "right" | "top" instead frames a temporary camera on target (object names; default the scene). ' +
        'The game\'s HUD and other page overlays are hidden for the screenshot, and its post effects (bloom, vignette, ' +
        'grading) are off, since Blender renders none (post_effects: true keeps them; the result names them). Exports ' +
        '(bumping the revision) but does ' +
        'not change the scene. save: true writes the images under ' +
        '.gamestudio/mcp/compare/.',
      inputSchema: object({
        camera: {
          type: ['string', 'object'],
          minLength: 1,
          properties: {
            from: VEC3,
            at: VEC3,
            lens: { type: 'number', minimum: 1, maximum: 500, description: 'Millimetres on a 36 mm sensor. Default: the scene camera\'s, else 35.' },
          },
          required: ['from', 'at'],
          additionalProperties: false,
          description: 'A camera object name, or {from, at, lens?}: a temporary camera (Blender space). Default: the scene camera.',
        },
        view: { type: 'string', enum: ['iso', 'front', 'back', 'left', 'right', 'side', 'top', 'bottom'], description: 'Frame a temporary camera on target from this side.' },
        target: { type: ['string', 'array'], items: { type: 'string', minLength: 1 }, minItems: 1, description: 'With view: the objects to frame (children included). Default: the scene.' },
        size: { type: 'array', items: { type: 'integer', minimum: 64, maximum: 2048 }, minItems: 2, maxItems: 2, description: '[width, height], default [800, 450].' },
        engine: { type: 'string', enum: ['eevee', 'cycles', 'workbench'], description: 'Blender render engine. Default eevee.' },
        samples: { type: 'integer', minimum: 1, maximum: 1024, description: 'Render samples. Default 16.' },
        post_effects: { type: 'boolean', description: 'Keep the post effects of the game (bloom, vignette, grading) in its shot. Default false: Blender renders none.' },
        save: { type: 'boolean' },
      }),
      annotations: { ...READ, idempotentHint: false },
      handler: async (args, call) => {
        if (args.target !== undefined && args.view === undefined) {
          throw new ToolError('target goes with view.', { code: 'invalid_arguments', hint: 'Example: {"view": "iso", "target": ["Hill", "Player"]}' });
        }
        if (args.view !== undefined && args.camera !== undefined) {
          throw new ToolError('Pass camera or view, not both.', { code: 'invalid_arguments', hint: 'view frames a temporary camera; camera names one or poses one with {from, at}.' });
        }
        const dev = await ctx.ensureDevServer(call.signal);
        const exported = await ctx.blender.call('export_scene', {}, { signal: call.signal });
        const view = await ctx.blender.call(
          'render_camera_view',
          {
            ...(args.camera && { camera: args.camera }),
            ...(args.view && { view: args.view }),
            ...(args.target && { target: args.target }),
            size: args.size ?? [800, 450],
            engine: args.engine ?? 'eevee',
            samples: args.samples ?? 16,
          },
          { timeoutMs: 300_000, signal: call.signal },
        );
        const want = view.request;
        const game = await gameScreenshot(ctx, dev.url, want, exported.rev, args.post_effects === true);
        const result = await ctx.blender.call('compare_images', { blender_png: view.png, game_png: game.png }, { timeoutMs: 120_000, signal: call.signal });
        /** @type {Record<string, any>} */
        const data = {
          verdict: result.verdict,
          ...(result.verdict_reason && { verdict_reason: result.verdict_reason, decided_by: result.decided_by }),
          normalized_diff: result.normalized_diff,
          detail_ratio: result.detail_ratio,
          mean_abs_diff: result.mean_abs_diff,
          worst_cells: result.worst_cells,
          ...(result.detail_cells && { detail_cells: result.detail_cells }),
          rev: exported.rev,
          camera: view.camera,
          ...(view.pose && { pose: view.pose }),
          engine: view.engine,
          size: result.size,
          render_seconds: view.seconds,
          image: '[Blender | game | difference x4]',
          note:
            'Judge on normalized_diff and detail_ratio. mean_abs_diff is the raw difference before brightness and contrast are ' +
            'matched; the game uses the scene\'s view transform and exposure, so it is small for a good match.',
        };
        if (!game.cameraFound) {
          data.warning = want.pose
            ? 'The game camera is not a perspective camera, so the posed view could not be built; the game shows its own camera.'
            : `The game has no camera under entity ${want.camera_id}; it shows its default camera.`;
        }
        if (game.post.length) {
          data.post_effects = args.post_effects
            ? `kept in the game shot: ${game.post.join(', ')} (Blender renders none, so they count as differences)`
            : `off in the game shot: ${game.post.join(', ')} (Blender renders none; post_effects: true keeps them)`;
        }
        if (game.errors.length) data.page_errors = game.errors;
        if (args.save && ctx.root) {
          data.saved = {
            blender: ctx.saveImage('compare', 'blender', view.png),
            game: ctx.saveImage('compare', 'game', game.png),
            side_by_side: ctx.saveImage('compare', 'side_by_side', result.side_by_side_png),
            diff: ctx.saveImage('compare', 'diff', result.diff_png),
          };
        }
        return withImages(data, [{ data: result.side_by_side_png, mimeType: 'image/png' }]);
      },
    },
  ];
}
