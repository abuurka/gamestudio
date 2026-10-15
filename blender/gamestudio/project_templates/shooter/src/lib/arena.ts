// The match, shared by the systems. WaveSystem resets it when the scene starts. The score is the
// run's, in ctx.store.score: it would carry over to another arena, and Restart clears it.
export const arena = {
  kills: 0,
  wave: 0, // the current wave, from 1
  over: false, // the player has lost
};
