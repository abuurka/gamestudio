# More than one level

A second level, the start level, and a goal that loads it

Click-by-click video tutorial, 1:49. GameStudio 0.1.0, Blender 5.2.2 or newer.

## 00:03 Step 1: Open the File menu

Gem Island has one level, main. A new level starts as a copy of it. Open the File menu,

## 00:09 Step 2: Choose Save As...

and choose Save As.

## 00:12 Step 3: Name it level2.blend

Blender opens the project's scenes folder, where levels live. Name the copy level2 dot blend,

## 00:19 Step 4: Click Save As

and click Save As.

## 00:22 Step 5: Click Ground in the Outliner

Level two is open now. To make it look different, click Ground in the Outliner,

## 00:27 Step 6: Open the Material tab

open the Material tab in the Properties,

## 00:30 Step 7: Open the material list

open the list of materials, left of GS Grass,

## 00:34 Step 8: Pick GS_Dirt

type dirt, and press Enter to pick GS Dirt. Level two is a dirt field now.

## 00:39 Step 9: Open the Scene tab

Now open the Scene tab,

## 00:42 Step 10: Scroll down to Levels

and scroll down to Game Project. Levels lists the files in the scenes folder: level2 is there, next to main.

## 00:49 Step 11: Keep main as the start level

Main is marked start: the game begins there. That's startScene in gamestudio.json. Leave it on main.

## 00:56 Step 12: Click Export All

Click Export All, so the game can load level two while it runs.

## 01:00 Step 13: Press Ctrl+S to save

Save level two with Control S.

## 01:03 Step 14: Click Open next to main

Then click Open next to main, to go back to level one.

## 01:07 Step 15: Load level2 after the last gem

Now the goal. On Gem Island you win by collecting every gem, in src, systems, collect dot ts. In main, make the last gem load level two, with ctx dot load scene.

## 01:19 Step 16: Press F5 to play

Press F5 to play level one.

## 01:21 Step 17: Click Play in the game

Click Play on the title screen.

## 01:25 Step 18: Collect every gem

Walk over the three gems. After the last one, the picture fades, and level two loads: the dirt field.

## 01:34 Done: Two levels, one game

That's a game with two levels: one file per level in scenes, the start level in gamestudio.json, and ctx dot load scene to move on. Your score comes along too.
