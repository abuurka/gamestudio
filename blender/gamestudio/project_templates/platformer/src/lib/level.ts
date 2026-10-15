// The game's values live in ctx.store, where the HUD and the screens of ui/ui.blend show them through
// bindings ("{coins} / {coinTotal}", "{time:m:ss}"), so no code draws them. Each system sets its level's
// values in start(); Restart (ctx.restart) clears ctx.store.
//
//   ctx.store.coins       coins collected in this level             CoinSystem    HUD/Coins, Win, GameOver
//   ctx.store.coinTotal   coins in this level                       CoinSystem    HUD/Coins, Win, GameOver
//   ctx.store.time        the level's clock in seconds; it stops    GoalSystem    HUD/Clock "{time:m:ss}", Win
//                         when the goal is reached
//   ctx.store.finished    the goal was reached                      GoalSystem
//   ctx.store.newBest     that finish beat the best time            GoalSystem    Win: "New best time!" (UiShow)
//   ctx.store.score       the run's coins: carries over to later    CoinSystem
//                         levels (ctx.loadScene)
//   ctx.store.lives       the run's lives: carry over too           RespawnSystem HUD/Lives hearts (UiShow)
//   ctx.save 'bestTime'   the best finish time, kept across         GoalSystem    Win "{save.bestTime:m:ss.00}"
//                         page loads

/**
 * Lives at the start of a run. Falling off or touching lava costs one; none left is game over. The HUD's
 * Lives row in ui/ui.blend has as many hearts: change its count with it (ui_set count).
 */
export const START_LIVES = 5;

/** 83.4 -> "1:23.4" */
export function formatTime(seconds: number): string {
  const minutes = Math.floor(seconds / 60);
  return `${minutes}:${(seconds - minutes * 60).toFixed(1).padStart(4, '0')}`;
}
