import { defineComponent, field } from '@gamestudio/engine/schema';

export const MovingPlatform = defineComponent('MovingPlatform', {
  description: 'Glides to start + offset and back, carrying whoever stands on it. Needs a kinematic RigidBody.',
  category: 'Platformer',
  fields: {
    offset: field.vec3([0, 0, -8], { description: 'Far end relative to the start, metres in runtime space (Y up, Blender +Y is -Z)' }),
    period: field.float(6, { min: 0.5, unit: 'time', description: 'Seconds for a round trip' }),
    phase: field.float(0, { min: 0, max: 1, description: 'Where in the trip it starts (0-1)' }),
  },
});
