import { defineComponent, field } from '@gamestudio/engine/schema';

export const Wheel = defineComponent('Wheel', {
  description: 'A wheel: a child of a Car, axle along X. It spins with the speed of the car; front wheels also steer.',
  category: 'Driving',
  fields: {
    radius: field.float(0.36, { min: 0.05, unit: 'distance', description: 'Metres' }),
    steers: field.bool(false, { description: 'Turns with the steering' }),
  },
});
