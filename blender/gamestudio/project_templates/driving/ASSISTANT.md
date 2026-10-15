# {{name}}: conventions for the AI assistant

{{base}}

This project started from the Time Trial template: an arcade driving game. Components: `Car`, `Wheel`, `Surface`, `Checkpoint`, `Race`, `ChaseCamera`, `CheckpointMarker`. Systems: `CarControllerSystem`, `WheelSystem`, `ChaseCameraSystem`, `RaceSystem`. The car is a kinematic body driven by code; `carState(car)` in `src/lib/car-state.ts` holds its speed and steering. Checkpoints are scaled empties passed in index order (0 is the start line). The menus and the HUD are Blender screens in `ui/ui.blend` (`Title`, `Pause`, `Options`, `Results`, `HUD`), which `tools/build_ui.py` built; `RaceSystem` keeps the HUD's values in `ctx.store` (`lap`, `laps`, `raceTime`, `lapTime`, `bestLap`, `speedKmh`, `offRoad`), shows the split banner and opens `Results`. The game opens on a title screen: playtests start with an Enter press. `tools/build_level.py` rebuilds the level, track included, from scratch and discards edits made in Blender.
