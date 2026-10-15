# Sound and music

Sounds on objects, music, and the player's volume sliders

Click-by-click video tutorial, 1:51. GameStudio 0.1.0, Blender 5.2.2 or newer.

## 00:03 Step 1: Click a gem

Here's Gem Island, the starter game. Let's give a gem a sound of its own. Click it in the 3D View.

## 00:09 Step 2: Click Add Component

In the Properties, the gem's Game panel lists its components. Click Add Component.

## 00:15 Step 3: Choose AudioSource

Choose AudioSource, under Audio. It plays a sound from this object.

## 00:20 Step 4: Scroll down to AudioSource

Scroll down to its settings.

## 00:23 Step 5: Type a sound in Clip

Clip is the sound file, a path in your project. The starter's sound effects are in assets, sfx: type the path of whoosh dot wav.

## 00:33 Step 6: Turn on Loop

Turn on Loop, so it keeps repeating,

## 00:36 Step 7: Turn on Autoplay

and Autoplay, so it starts on its own. Spatial is already on: the sound comes from the gem, and gets louder as you walk up to it.

## 00:44 Step 8: Scroll down the Outliner

The starter has music too. Scroll down the Outliner,

## 00:48 Step 9: Click Music in the Outliner

and click the Music object.

## 00:51 Step 10: Point at its Channel

It has an AudioSource too: the theme, looping, and not spatial, so it sounds the same everywhere. Its Channel is Music; sound effects use Sfx.

## 01:02 Step 11: Press F5 to play

As the tooltip says, music keeps playing under menus, while sound effects pause with the game. Press F5 to play.

## 01:10 Step 12: Click Options

Click Options on the title screen. Browsers only play sound after a click or a key press, so the theme starts now. The gem's whoosh waits until you play.

## 01:19 Step 13: Drag the Music slider

Here are the player's own volume sliders. Drag Music to the left, and the theme gets quieter at once, even under this menu.

## 01:27 Step 14: Click along Sound

Sound sets the effects: the gem's whoosh and the pickups. The game saves both settings for the player, with no code.

## 01:35 Step 15: Press F8 to stop

Press F8 to stop the game.

## 01:37 Done: Sounds, channels and volumes

That's sound in GameStudio: an AudioSource plays a file from an object, its channel says music or effects, and the Options screen lets players set each volume.
