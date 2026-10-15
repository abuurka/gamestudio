import { defineComponent, field } from '@gamestudio/engine/schema';

export const Coin = defineComponent('Coin', {
  description: 'A collectible. The player picks it up by touching it or walking close.',
  category: 'Gameplay',
  fields: {
    value: field.int(1, { min: 0, description: 'Points awarded when collected' }),
  },
});
