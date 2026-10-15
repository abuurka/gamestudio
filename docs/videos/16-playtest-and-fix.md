# Playtest and fix

Claude Code plays your level, finds what's wrong and fixes it

Click-by-click video tutorial, 1:32. GameStudio 0.1.0, Blender 5.2.2 or newer.

## 00:03 Step 1: A gem on a high ledge

Here's Gem Island with one change: a stone ledge, two metres high, with the third gem on top. Can the player get it? Let's ask Claude Code to find out.

## 00:12 Step 2: Open the GameStudio menu

Open the GameStudio menu,

## 00:15 Step 3: Choose Open in Claude Code

and choose Open in Claude Code.

## 00:18 Step 4: Claude Code starts: click Done

Claude Code starts in a terminal, in your project folder. Click Done.

## 00:23 Step 5: Ask for a playtest

In the terminal, ask in plain words: can the player collect every gem? Fix anything that's wrong.

## 00:30 Step 6: Claude reads the level

Claude Code connects to Blender and reads the level first: where the player starts, where the gems are, and the new ledge.

## 00:37 Step 7: It playtests the level

Then it playtests. GameStudio plays the game in a hidden browser with scripted keys, and sends back screenshots, the game's state and its logs. Two gems out of three.

## 00:48 Step 8: It finds the cause

The third gem was never picked up: it's two metres up, and the player's jump peaks under one metre. So Claude adds stairs.

## 00:56 Step 9: Stairs appear in Blender

The stairs appear in Blender, up against the ledge.

## 01:00 Step 10: It adds a Collider

It gives them a mesh Collider, shaped like the steps, so the player can walk up them.

## 01:05 Step 11: The same playtest passes

Then it runs the very same playtest again. This time the player climbs the stairs and takes all three gems, and the win screen opens.

## 01:13 Step 12: Claude saves and reports

Claude saves the level, and tells you what was wrong, what it changed, and the proof: the playtest that now passes.

## 01:21 Done: Playtest, fix, playtest again

That's the loop: ask Claude Code to playtest, let it fix what fails, and check that the same test passes.
