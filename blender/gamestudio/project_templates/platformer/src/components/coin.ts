import { defineComponent, field } from '@gamestudio/engine/schema';

export const Coin = defineComponent('Coin', {
  description: 'A pickup: the player collects it by walking into it. It spins and bobs.',
  category: 'Platformer',
  fields: {
    value: field.int(1, { min: 0, description: 'Points it adds to the score' }),
  },
});
