import { defineComponent, field } from '@gamestudio/engine/schema';

export const BouncePad = defineComponent('BouncePad', {
  description: 'Launches the player upward when they land on it. Launch height is about strength² / 40 m.',
  category: 'Platformer',
  fields: {
    strength: field.float(14, { min: 0, max: 60, description: 'Launch speed in m/s (14 reaches about 4.9 m)' }),
  },
});
