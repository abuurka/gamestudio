import { defineComponent } from '@gamestudio/engine/schema';

export const KillZone = defineComponent('KillZone', {
  description: 'Touching this volume (or falling below y = -20) respawns the player.',
  category: 'Gameplay',
});
