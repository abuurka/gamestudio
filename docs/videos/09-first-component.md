# Your first component and system

Write game behaviour in TypeScript

Click-by-click video tutorial, 1:53. GameStudio 0.1.0, Blender 5.2.2 or newer.

## 00:03 Step 1: Open the GameStudio menu

Let's give a gem some new behaviour, written in TypeScript. Open the GameStudio menu.

## 00:08 Step 2: Choose New Component...

Choose New Component. A component is data you attach to objects.

## 00:13 Step 3: Name it Bob

Name it Bob.

## 00:17 Step 4: Click Create

Click Create. GameStudio writes src/components/bob.ts from a template, and opens it in your code editor.

## 00:26 Step 5: Give it two fields

Swap the example field for two of your own: how far it bobs, and how fast. Then save the file.

## 00:33 Step 6: Open the GameStudio menu again

Bob holds the data. The behaviour goes in a system. Open the GameStudio menu again,

## 00:38 Step 7: Choose New System...

and choose New System.

## 00:41 Step 8: Name it Bob

Name it Bob as well. GameStudio calls it Bob System.

## 00:46 Step 9: Open the Query list

Query picks the objects the system works on. Open the list,

## 00:50 Step 10: Choose Bob

and choose Bob: every object with a Bob component.

## 00:54 Step 11: Click Create

Click Create. The new system's update runs every frame, for each of those objects.

## 01:00 Step 12: Make it bob

Swap the example for a few lines. wave gives the height at any moment, and each frame the object moves by how much the wave changed. Save it.

## 01:09 Step 13: Click a gem

Back in Blender, click a gem to select it.

## 01:12 Step 14: Click Add Component

In the Properties, click Add Component.

## 01:15 Step 15: Choose Bob

Bob is in the list now, under Gameplay. Choose it.

## 01:19 Step 16: Scroll down to Bob

Scroll down. There's Bob, with the description from its file, and your two fields.

## 01:24 Step 17: Set Height to 0.5

Set its Height to half a metre.

## 01:27 Step 18: Press F5 to play

Press F5 to play.

## 01:30 Step 19: Click Play in the game

Click Play on the title screen.

## 01:33 Step 20: Watch the gem bob

And the gem bobs up and down. Change the system and save it while the game runs: it hot-reloads, and you see the change right away.

## 01:42 Done: Your first component and system

That's the pattern: a component holds the data, a system holds the behaviour, and you tune the fields in Blender.
