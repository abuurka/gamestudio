import { defineComponent, field } from '@gamestudio/engine/schema';

export const Enemy = defineComponent('Enemy', {
  description: 'Chases the nearest player and hits it when close. Needs a kinematic RigidBody, a Collider and Health.',
  category: 'Shooter',
  fields: {
    speed: field.float(3.2, { min: 0, description: 'Metres per second' }),
    damage: field.int(10, { min: 0, description: 'Hit points taken from the player per hit' }),
    reach: field.float(1.3, { min: 0.5, unit: 'distance', description: 'Hits the player within this many metres' }),
    attackInterval: field.float(0.8, { min: 0.1, unit: 'time', description: 'Seconds between hits' }),
    points: field.int(10, { min: 0, description: 'Score for defeating it' }),
  },
});
