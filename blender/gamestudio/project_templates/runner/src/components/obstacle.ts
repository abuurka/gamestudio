import { defineComponent, field } from '@gamestudio/engine/schema';

export const Obstacle = defineComponent('Obstacle', {
  description: 'Running into it ends the run (its bounds, children included). It breaks into its debris prefab.',
  category: 'Runner',
  fields: {
    debris: field.string('DebrisWood', { description: 'Prefab spawned where it breaks (empty: none)' }),
  },
});
