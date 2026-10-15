import { defineComponent, field } from '@gamestudio/engine/schema';

export const PowerUp = defineComponent('PowerUp', {
  description: 'A pickup that gives the runner a power for a while, shown by an aura prefab around it.',
  category: 'Runner',
  fields: {
    kind: field.enum(['magnet', 'shield'], 'magnet', { description: 'magnet: pulls coins in; shield: obstacles break and falls bounce' }),
    duration: field.float(8, { min: 0, unit: 'time', description: 'Seconds it lasts' }),
    aura: field.string('', { description: 'Prefab spawned on the runner while it lasts' }),
  },
});
