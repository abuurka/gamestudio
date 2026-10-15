import { defineComponent, field } from '@gamestudio/engine/schema';

export const ChaseCamera = defineComponent('ChaseCamera', {
  description: 'Put on the camera: it follows behind the target and turns with it (FollowCamera keeps a fixed direction).',
  category: 'Driving',
  fields: {
    target: field.entity({ description: 'What to chase (the car)' }),
    distance: field.float(8, { min: 1, unit: 'distance', description: 'Metres behind the target' }),
    height: field.float(3.2, { min: 0, unit: 'distance', description: 'Metres above the target' }),
    lookAhead: field.float(5, { min: 0, unit: 'distance', description: 'Looks at a point this far in front of the target' }),
    stiffness: field.float(5, { min: 0.5, description: 'How quickly it catches up and turns (higher: tighter)' }),
  },
});
