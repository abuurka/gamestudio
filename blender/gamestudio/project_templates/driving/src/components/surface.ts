import { defineComponent, field } from '@gamestudio/engine/schema';

export const Surface = defineComponent('Surface', {
  description: 'How a car drives on this ground (it needs a Collider). Ground without one counts as road.',
  category: 'Driving',
  fields: {
    speedFactor: field.float(1, { min: 0, max: 2, description: 'Multiplies the top speed of a car on it (grass: about 0.5)' }),
    grip: field.float(1, { min: 0, max: 2, description: 'Multiplies the grip of a car on it' }),
  },
});
