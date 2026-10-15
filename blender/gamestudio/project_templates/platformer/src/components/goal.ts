import { defineComponent, field } from '@gamestudio/engine/schema';

export const Goal = defineComponent('Goal', {
  description: 'Reaching it finishes the level and shows the time and coins.',
  category: 'Platformer',
  fields: {
    radius: field.float(1.8, { min: 0.5, unit: 'distance', description: 'How close (m) the player must get' }),
  },
});
