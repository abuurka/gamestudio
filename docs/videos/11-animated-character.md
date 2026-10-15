# A character that walks and jumps

A rigged character, its clips, and Locomotion

Click-by-click video tutorial, 1:57. GameStudio 0.1.0, Blender 5.2.2 or newer.

## 00:03 Step 1: Ask Claude for a rigged character

Here's Gem Island, with Claude Code running in the project, as in lesson four. GameStudio's rigged character comes from a kit that Claude builds, so ask Claude to swap the player for one.

## 00:14 Step 2: Watch Claude build the character

Claude deletes the old player, builds a rigged character from the kit, and gives it the player's components and the camera. It's an armature called Player, with a skinned mesh.

## 00:23 Step 3: Open the Editor Type menu

Its animations are Blender actions. To see them, turn the Console into the Nonlinear Animation editor: click its Editor Type button.

## 00:32 Step 4: Choose Nonlinear Animation

Choose Nonlinear Animation.

## 00:35 Step 5: Drag its top border up

Drag its top border up to make room for every track.

## 00:39 Step 6: See one track per clip

Here are the character's clips: Idle, Walk, Run, Jump, Fall and a few more. Each one is a Blender action on its own track, and the game plays them by name.

## 00:49 Step 7: Click Add Component

Now wire them up, with no code. The armature is still selected, and its Game panel in the Properties lists the components Claude gave it. Click Add Component.

## 00:58 Step 8: Scroll the menu up

The list is long, so scroll it up to Animation.

## 01:02 Step 9: Choose Locomotion

Choose Locomotion, under Animation.

## 01:05 Step 10: Scroll down to Locomotion

Scroll down to its settings.

## 01:08 Step 11: See a clip for each gait

Locomotion names a clip for each gait: Idle, Walk, Run, Jump and Fall, the kit's own names. It picks one from how the character moves.

## 01:18 Step 12: Press F5 to play

Press F5 to play.

## 01:20 Step 13: Click Play in the game

Click Play on the title screen. That also hands your keyboard to the game.

## 01:25 Step 14: Hold W to run

Hold W, and it runs: Locomotion plays the Run clip as soon as it's fast enough.

## 01:31 Step 15: Turn with D, jump with Space

Turn with D and press Space, and it jumps: Locomotion plays the Jump clip on the way up, and Fall on the way down.

## 01:38 Step 16: Press Shift+Esc

Shift Escape gives you the mouse back,

## 01:41 Step 17: Press F8 to stop

and F8 stops the game.

## 01:43 Done: A character that walks and jumps

That's an animated player, with no code. For moves of your own, like a landing or an attack, ask Claude for an animation graph.
