import { defineComponent, field } from '@gamestudio/engine/schema';

export const Checkpoint = defineComponent('Checkpoint', {
  description: 'A gate the car must pass, in index order. Put it on an empty: its 1 m cube, scaled, is the volume. Index 0 is the start/finish line.',
  category: 'Driving',
  fields: {
    index: field.int(0, { min: 0, description: 'Order around the lap; 0 is the start/finish line' }),
  },
});
