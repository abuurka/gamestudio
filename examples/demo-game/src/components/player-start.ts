import { defineComponent } from '@gamestudio/engine/schema';

export const PlayerStart = defineComponent('PlayerStart', {
  description: "Where the player respawns (place it at floor level). Optional: defaults to the player's start.",
  category: 'Gameplay',
});
