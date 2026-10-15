# Enemies that chase you

A NavMesh, a NavAgent and a few lines of code

Click-by-click video tutorial, 2:01. GameStudio 0.1.0, Blender 5.2.2 or newer.

## 00:03 Step 1: Ask Claude Code for an enemy

Gem Island is open, with Claude Code running in the project. First, an enemy. Ask Claude for a red one at the far end of the lawn, behind a low stone wall.

## 00:12 Step 2: Click the Enemy

Claude builds both from GameStudio's kits. The enemy already has a kinematic RigidBody and a capsule Collider, which it needs to walk. Click it to select it.

## 00:22 Step 3: Click Add Component

In the Properties, the Object tab's Game panel lists its components. Click Add Component.

## 00:28 Step 4: Choose NavAgent

Choose NavAgent, under Navigation. It walks the enemy to a target along the navigation mesh, around anything solid.

## 00:35 Step 5: Click the lawn, the Ground

Now the navigation mesh itself. Click the lawn to select the Ground.

## 00:40 Step 6: Click Add Component

Click Add Component again,

## 00:44 Step 7: Choose NavMesh

and choose NavMesh. When the level loads, the game builds the walkable floor from every solid collider, and keeps paths an Agent Radius away from walls. The defaults suit a person-sized enemy.

## 00:56 Step 8: Open the GameStudio menu

The enemy still needs somewhere to go. That's a job for a system. Open the GameStudio menu

## 01:02 Step 9: Choose New System...

and choose New System.

## 01:05 Step 10: Name it Chase

Name it Chase. The dialog shows the file it makes, in the systems folder.

## 01:10 Step 11: Click Create

Click Create. The new file opens in your code editor.

## 01:14 Step 12: Point the enemies at the player

Replace the example with this. Every frame, an enemy within fifteen metres takes the player as its target, and its NavAgent plans a path around anything in the way. Farther off, nav dot stop keeps it waiting. Save the file.

## 01:30 Step 13: Press F5 to play

Press F5 to play.

## 01:32 Step 14: Click Play in the game

Click Play on the title screen.

## 01:36 Step 15: The enemy goes around the wall

The enemy can't walk through the wall,

## 01:38 Step 16: It heads for the wall's end

so it heads for the end,

## 01:41 Step 17: It comes around the wall

comes around it, and walks straight to you. Its NavAgent plans a new path twice a second, so wherever you go, it follows.

## 01:48 Done: An enemy that chases you

That's an enemy that chases you: a NavMesh on the ground, a NavAgent on the enemy, and a few lines of code that point it at the player.
