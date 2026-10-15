import { defineComponent } from '@gamestudio/engine/schema';

export const SpawnPoint = defineComponent('SpawnPoint', {
  description: 'Enemies appear here (at floor level). Points near the player are skipped.',
  category: 'Shooter',
});
