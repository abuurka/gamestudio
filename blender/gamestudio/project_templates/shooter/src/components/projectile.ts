import { defineComponent, field } from '@gamestudio/engine/schema';

export const Projectile = defineComponent('Projectile', {
  description: 'Flies straight along its front (Blender -Y) and damages the first thing with Health it hits.',
  category: 'Shooter',
  fields: {
    speed: field.float(32, { min: 1, description: 'Metres per second' }),
    damage: field.int(1, { min: 0, description: 'Hit points it takes' }),
    lifetime: field.float(1.2, { min: 0.1, unit: 'time', description: 'Seconds before it disappears' }),
  },
});
