import { defineSystem{{engine}} } from '@gamestudio/engine';
{{imports}}
/**
 * {{name}}: logic for every entity that has a {{component}} component.
 *
 * - Runs while the game plays. Saving this file hot-reloads it: the game keeps
 *   running and start() runs again.
 * - Module-level variables reset on a hot reload, so keep lasting state in
 *   components, or recompute it in start().
 * - ctx has dt, time, world, input, physics, hud, three and log (shown in the
 *   GameStudio Console).
 *
 * Reference: node_modules/@gamestudio/engine/API.md, sections 4 (Systems) and 5 (ctx).
 */
export default defineSystem({
  name: '{{name}}', // unique: a system with the same name replaces this one
  query: [{{component}}], // the entities that have ALL of these components
  phase: 'update', // 'fixed' (60 Hz, before physics), 'update' or 'late' (after update)

  start(ctx) {
    // Runs once when the scene starts playing, and after a hot reload.
    ctx.log('{{name}} started');
  },

  update(ctx, entities) {
    for (const e of entities) {
      const data = e.get({{component}}); // this entity's {{component}} values, typed
      // e.object3d is its Three.js object. Example: e.object3d.rotateY(ctx.dt);
    }
  },
});
