import { defineComponent, field } from '@gamestudio/engine/schema';

/** An example component. Attach it to objects in GameStudio's Game panel. */
export const Collectible = defineComponent('Collectible', {
  description: 'Picked up when the player walks into it.',
  category: 'Gameplay',
  fields: {
    points: field.int(1, { min: 0, description: 'Points awarded when collected' }),
  },
});
