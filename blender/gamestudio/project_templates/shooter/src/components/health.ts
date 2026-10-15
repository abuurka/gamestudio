import { defineComponent, field } from '@gamestudio/engine/schema';

export const Health = defineComponent('Health', {
  description: 'Hit points. At zero an enemy is defeated (score) and the player loses.',
  category: 'Shooter',
  fields: {
    max: field.int(3, { min: 1, description: 'Hit points at the start' }),
    current: field.int(0, {
      min: 0,
      description: 'Hit points now (runtime: HealthSystem fills it to max when the entity appears, hits lower it)',
    }),
  },
});
