import { defineComponent, field } from '@gamestudio/engine/schema';

export const Sweeper = defineComponent('Sweeper', {
  description: 'Rolls from side to side across the lanes. Its first child is the drum that turns.',
  category: 'Runner',
  fields: {
    travel: field.float(2.2, { min: 0, unit: 'distance', description: 'Metres to each side of where it was placed' }),
    speed: field.float(1.6, { min: 0, description: 'Radians per second of the swing (SocketSystem raises it with the difficulty)' }),
    radius: field.float(0.85, { min: 0.05, unit: 'distance', description: 'Drum radius, for rolling without sliding' }),
  },
});
