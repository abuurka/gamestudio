import { defineComponent, field } from '@gamestudio/engine/schema';

export const Race = defineComponent('Race', {
  description: 'Race settings. Put one on any object (the start line, for example).',
  category: 'Driving',
  fields: {
    laps: field.int(3, { min: 1, max: 99, description: 'Laps to finish the race' }),
  },
});
