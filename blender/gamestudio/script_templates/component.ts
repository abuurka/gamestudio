import { defineComponent, field } from '@gamestudio/engine/schema';

/**
 * {{name}}: data you attach to objects in GameStudio (Inspector > Add Component).
 *
 * - Components hold data only. Behaviour goes in a system (src/systems/) that
 *   queries this component: `query: [{{name}}]`, then `e.get({{name}})`.
 * - The dev server also runs this file in Node to build the component registry,
 *   so keep it to defineComponent: no DOM, no Three.js, no side effects.
 * - Saving it updates the Inspector and the running game; values already set on
 *   objects are kept, and new fields start at their defaults.
 *
 * Reference: node_modules/@gamestudio/engine/API.md, section 2 (Components).
 */
export const {{name}} = defineComponent('{{name}}', {
  description: 'What {{name}} does. Shown in the Inspector.',
  category: 'Gameplay', // groups components in Add Component

  // Each field becomes a typed value (e.get({{name}}).speed is a number) and an
  // Inspector widget. Builders: float, int, bool, string, enum, vec2, vec3,
  // color, entity, asset. Field names are camelCase.
  fields: {
    speed: field.float(1, { min: 0, description: 'An example field: rename or remove it' }),
  },
});
