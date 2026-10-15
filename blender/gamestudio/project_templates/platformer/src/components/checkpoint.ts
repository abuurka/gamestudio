import { defineComponent, field } from '@gamestudio/engine/schema';

export const Checkpoint = defineComponent('Checkpoint', {
  description: 'A flag pole. Passing it makes it the respawn point; its child flag plays its clip.',
  category: 'Platformer',
  fields: {
    radius: field.float(2.5, { min: 0.5, unit: 'distance', description: 'How close (m) the player must pass' }),
  },
});
