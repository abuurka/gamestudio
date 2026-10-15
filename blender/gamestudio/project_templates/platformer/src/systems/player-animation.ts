import { defineAnimationGraph } from '@gamestudio/engine';

/**
 * The player's animation graph: the `Animator` on `Player` names it. Its parameters come from how
 * the player moves (`from`), so no system sets them. It idles, walks and runs by speed, takes off,
 * floats, flails on long drops and lands. `Animator.state` shows the state in game_state and playtests.
 */
export default defineAnimationGraph('Player', {
  parameters: {
    speed: { type: 'float', from: 'speed' }, // horizontal m/s
    vy: { type: 'float', from: 'verticalSpeed' }, // m/s, + is up
    grounded: { type: 'bool', from: 'grounded', default: true },
    air: { type: 'float', from: 'airTime' }, // seconds off the ground
  },
  blend: 0.15,
  states: {
    // The kit's Walk matches 1.5 m/s and Run 4 m/s; at the controller's 6 m/s Run plays 1.5 times as fast.
    Move: { blend: { by: 'speed', clips: [[0, 'Idle'], [1.5, 'Walk'], [4, 'Run'], [6, 'Run', 1.5]] } },
    Jump: { clip: 'JumpStart', loop: false, speed: 1.6, next: 'Air' },
    Air: { clip: 'JumpAir' },
    Fall: { clip: 'Fall' },
    Land: { clip: 'Land', loop: false, speed: 1.5, next: 'Move' },
  },
  transitions: [
    // Take-off: a jump or a bounce pad.
    { from: ['Move', 'Land'], to: 'Jump', when: (p) => !p.grounded && p.vy > 0.5, blend: 0.05 },
    // Off a ledge (steps and bumps shorter than 0.12 s don't count).
    { from: ['Move', 'Land'], to: 'Air', when: (p) => p.air > 0.12 },
    // Dropping fast, well below the take-off height.
    { from: ['Jump', 'Air'], to: 'Fall', when: (p) => p.vy < -9, blend: 0.3 },
    { from: ['Jump', 'Air', 'Fall'], to: 'Land', when: 'grounded', blend: 0.05 },
    // Landing on the run: back to running before the crouch ends.
    { from: 'Land', to: 'Move', when: (p) => p.speed > 2, exitTime: 0.35, blend: 0.2 },
  ],
});
