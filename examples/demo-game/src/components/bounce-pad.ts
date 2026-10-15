import { defineComponent, field } from '@gamestudio/engine/schema';

export const BouncePad = defineComponent('BouncePad', {
  description: 'Launches the player upward when they land on it.',
  category: 'Gameplay',
  fields: {
    strength: field.float(12, { min: 0, max: 100, description: 'Launch speed in m/s' }),
  },
});
