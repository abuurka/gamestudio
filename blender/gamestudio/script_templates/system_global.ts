import { defineSystem } from '@gamestudio/engine';

/**
 * {{name}}: game-wide logic that is not tied to one kind of entity.
 *
 * - Runs while the game plays. Saving this file hot-reloads it: the game keeps
 *   running and start() runs again.
 * - Module-level variables reset on a hot reload, so keep lasting state in
 *   components, or recompute it in start().
 * - ctx has dt, time, world, input, physics, hud, three and log (shown in the
 *   GameStudio Console). To work on entities, add `query: [SomeComponent]`.
 *
 * Reference: node_modules/@gamestudio/engine/API.md, sections 4 (Systems) and 5 (ctx).
 */
export default defineSystem({
  name: '{{name}}', // unique: a system with the same name replaces this one
  phase: 'update', // 'fixed' (60 Hz, before physics), 'update' or 'late' (after update)

  start(ctx) {
    // Runs once when the scene starts playing, and after a hot reload.
    ctx.log('{{name}} started');
  },

  update(ctx) {
    // Runs every frame. Example: if (ctx.input.keyDown('KeyR')) void ctx.game.loadScene('main');
  },
});
