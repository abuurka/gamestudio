import { defineComponent } from '@gamestudio/engine/schema';

export const KillZone = defineComponent('KillZone', {
  description: 'Touching it (lava, spikes) sends the player back to the last checkpoint.',
  category: 'Platformer',
});
