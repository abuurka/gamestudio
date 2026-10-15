---
name: add-level-section
description: Add a new section to this platformer's course. Sketches it within the player's jump metrics, builds islands, jumps, hazards, moving platforms, coins and a checkpoint from kits, moves the goal to the new end, then walks it in a playtest. Use when the user wants more level, a harder or easier stretch, a new area, a bonus route, or "make the level longer".
argument-hint: "[what the section should be like]"
---

# Add a level section

A section is a stretch of course with one idea: a run of jumps, a moving-platform crossing, a lava gauntlet, a climb. It starts where the course currently ends, finishes on a safe island with a checkpoint, and every jump in it is possible. Build it in Blender and keep it reproducible.

## 1. Read the level and the metrics

- Read `scenes/main.scene.json` (the scene twin), or call `scene_summary`. Find the course's end: the `Summit` island with `GoalStar` on `GoalPedestal`, and the last `Checkpoint`. The course runs toward Blender +Y.
- Read the `Player`'s `CharacterController` with `get_object`. With speed 6, jumpSpeed 7.5 and gravity 20, a jump is **1.4 m high** and about **4.5 m long**. Design gaps of **3 m or less** and rises of **1 m or less** per jump. A `BouncePad` reaches strength² / 40 m (14 gives 4.9 m). Stairs need `step_height` ≤ 0.35.

## 2. Sketch it in five lines

Write the pieces as a list with a centre, a size and a top height in metres. For example:

```
Bridge1  platform 3x3   at (0, 68)  top 3.6    gap 1.5 from the Summit edge (y 64)
Ferry2   platform 3x3   at (3, 73)  top 3.6    MovingPlatform offset [6, 0, 0] (runtime: +X), period 5
Tower    platform 8x8   at (9, 81)  top 5.0    rise 1.4 by a BouncePad on Ferry2's far island
...
```

Check every gap and rise against the metrics, and mark where the checkpoint and the coins go.

## 3. Build

1. `checkpoint` with the label "before section".
2. **Islands and steps**: `create_from_kit` with `items`, in the `Level` collection. Use kind `platform` with `{"width", "depth", "height": 3, "bevel": 0.25, "top_material": "grass", "side_material": "dirt"}` and `location` z = top − 3, because the kit's origin is at its bottom. Then `add_component` `Collider` on each. Stairs: kind `stairs` and `Collider {shape: "mesh"}`.
3. **Hazards**: a `platform` 0.12 thick, 6 cm proud of the floor, then `apply_material` with `{"preset": "emissive", "params": {"color": "#FF5A1F", "emission_strength": 3}}`, and `KillZone` with **no** Collider.
4. **Moving platform**: a `platform` (`top_material: wood`) with `RigidBody {type: kinematic}`, `Collider` and `MovingPlatform {offset, period}`. The offset is in **runtime** space: Blender +X is +X, Blender +Y is −Z, up is +Y. Leave about 0.5 m between the platform and the islands at both ends.
5. **Coins**: kind `collectible` with `{"style": "coin", "size": 0.6}` at top + 1.0, then `Coin {value: 1}`. For the same glowing gold as the others: `apply_material` with `{"preset": "gold", "name": "CoinGold", "params": {"metallic": 0.6, "emission": "#FFB01F", "emission_strength": 0.6}}`.
6. **Bounce pad and checkpoint**: the level script has helpers. Use `run_python`:

   ```python
   import pathlib, runpy
   lvl = runpy.run_path(str(pathlib.Path(bpy.data.filepath).parents[1] / "tools" / "build_level.py"))
   pole, flag = lvl["checkpoint"](gs, "Checkpoint3", (0, 81, 5.0), D.collections["Props"], C.scene)
   pad = lvl["bounce_pad"](gs, "BouncePad2", (3, 77, 3.6), D.collections["Props"])
   add = gs.core.components.add_component
   add(pole, "Checkpoint", {"radius": 2.5})
   add(flag, "Animator", {"clip": "FlagRaise", "loop": False, "playOnStart": False})
   add(pad, "BouncePad", {"strength": 14}); add(pad, "Collider")
   ```

   The checkpoint object's origin is the respawn point, so put it on the floor where landing is safe.
7. **Move the goal** to the new end: `set_transform` for `GoalStar` and `GoalPedestal`. The star's `spin_bob` clip keys its location, so re-key it there with `run_python`: `gs.kits.animation.add_clip(D.objects["GoalStar"], ["spin", "bob"], {"spin": {"speed": 0.3}, "bob": {"height": 0.2, "speed": 0.5}})`.
8. **Dress it**: a few `tree`, `bush` and `rock` kits at the edges, away from the jumps. Trees get `Collider` and, if you like, the `sway` clip with an `Animator` like the others.

## 4. Verify

1. `render_views` with `views: ["top", "iso"]` around the new section. Look at it. Can the path be read? Do the gaps look right next to the 1.8 m player? Is anything floating or overlapping by accident?
2. `validate_assets` reports no errors.
3. **Playtest the section itself.** `playtest` starts at the level start, so move the `Player` to the section's start with `set_transform` (on the floor) first. Then hold `KeyW` and press `Space` at each gap, take screenshots at the jumps, and `track: ["Player"]`. The player must arrive and the checkpoint must say "Checkpoint!". Put the `Player` back afterwards at (0, −6, 0).
4. `playtest` from the real start too, to check that nothing earlier broke and that the HUD counts the new coins (`HUD/Coins` in `final.ui.texts`: `n / total`).
5. `save`.

## 5. Keep it reproducible

`tools/build_level.py` rebuilds the whole level and would drop the new section. Either add the same pieces to `build()` (the helpers there are the ones you used), or tell the user the level is now edited by hand and the script is out of date.

## Common failures

| Symptom | Fix |
|---|---|
| A jump falls short | The gap is over 3 m or the rise over 1 m. Move the platform; don't raise `jumpSpeed`, or every earlier jump changes too. |
| The player walks through a piece | It has no `Collider`, or it is stairs or a building with an `auto` box. Use `shape: "mesh"`. |
| The moving platform leaves the player behind | It needs `RigidBody {type: kinematic}`. Check the offset: runtime −Z is Blender +Y. |
| The moving platform shoves the player off the island | Its end positions overlap the islands. Leave a 0.5 m gap. |
| The coin can't be collected | It is more than 1.1 m from the player's chest (0.9 m above the feet). Put it at top + 1.0. |
| Respawns land in lava or mid-air | The checkpoint's origin is the respawn point. Put it on a safe floor. |
| The star snaps back to its old spot | Its clip keys the old location. Re-key `spin_bob` after moving it. |
