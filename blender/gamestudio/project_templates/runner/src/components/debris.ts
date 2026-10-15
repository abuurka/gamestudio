import { defineComponent, field } from '@gamestudio/engine/schema';

export const Debris = defineComponent('Debris', {
  description: 'The root of a debris prefab: its children with a dynamic RigidBody fly apart when it appears.',
  category: 'Runner',
  fields: {
    force: field.float(6, { min: 0, description: 'Metres per second the pieces fly out at' }),
    lifetime: field.float(2.5, { min: 0.1, unit: 'time', description: 'Seconds before the pieces are removed' }),
  },
});
