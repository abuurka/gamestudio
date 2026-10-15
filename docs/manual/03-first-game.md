# Your first game

[Manual contents](README.md) · Previous: [The editor](02-editor.md) · Next: [Projects and templates](04-projects.md)

This tutorial turns the Starter template into a small collection game named **Garden Quest**. You will move a pickup, change the player's speed, add a new collectible, edit a menu and make a web build. It uses existing template logic, so no new TypeScript is required.

## Create Garden Quest

1. Choose **GameStudio > New Project**.
2. Enter **Garden Quest**, choose a project location and select **Starter**.
3. Choose **Create** and finish runtime setup if prompted.
4. Wait for the start level to open in the Game workspace.
5. Press **F5**, click into the game and start from the title menu.

The Starter includes a player, follow camera, three pickups, sound effects, a HUD and menus. Walk through the level once before editing. Collecting the last item leads to the win screen. Restart to confirm that the pickups and score reset.

If Play opens a window rather than an embedded view, you can continue there or finish the game-browser setup. The content workflow is the same.

## Move a pickup

1. Release game input with **Shift+Esc** and stop with **F8**.
2. Select one of the visible pickup objects in the level.
3. Use Blender's **G** transform to move it to another reachable position on the lawn. Constrain the move to a horizontal axis if needed.
4. Keep the pickup near the player's body height. The Starter checks proximity to collect an item.
5. Save, play again and walk toward its new position.

**Expected result:** the pickup now appears where you placed it and disappears when collected. It still exists in Blender after stopping because the running game and authoring scene have separate state.

If the pickup cannot be reached, lower it and move it onto clear ground. A hovering pickup outside the collection distance is a placement problem, even if it looks attractive from the editor camera.

## Tune the player

1. Stop the game and select the player object that carries **CharacterController**.
2. Open **Properties > Object > Game**.
3. Find **CharacterController > Speed**, note its current value and increase it modestly, for example to `7`.
4. Press Play and compare how long it takes to cross the lawn.
5. Return the value or adjust it until movement suits the level.

Keep **RigidBody > Type** set to **Kinematic** and keep the player's Collider. The controller moves the character; a dynamic body is a different physics setup. Increasing speed can make narrow jumps and obstacles harder, so tune against the actual level rather than a number alone.

## Add a new collectible

The easiest way to retain the working setup is to duplicate a template pickup.

1. Stop and select one of the existing collectible objects.
2. Use **Shift+D** to duplicate it and place the copy on reachable ground.
3. Give it a clear object name such as `BonusPickup`.
4. In **Object > Game**, confirm that it has the project component **Collectible**.
5. Set its **points** field to `5`.
6. Save and restart the game from its title screen.

**Expected result:** the new pickup adds five points, the other pickups retain their own values, and victory waits until all collectibles are gone. The Starter's `CollectSystem` discovers objects with the `Collectible` component rather than relying on the original count of three.

The Starter already spins collectibles in its collection system. Avoid adding another Spin component to these objects unless you deliberately want a second rotation contribution.

To make a collectible from a new mesh instead, create a small mesh, assign a material, place it in reach and add **Collectible**. This works with the Starter's proximity-based system; other templates may use sensors or different components, so do not assume that any object named Coin is automatically collectible.

## Add a decorative rotating object

1. Add a mesh away from the walking route, such as a cube on a pedestal.
2. Name it `GardenMarker` and scale it to suit the scene.
3. In **Object > Game > Add Component**, add the built-in **Spin** component.
4. Set its speed to `1` and leave its runtime axis at `[0, 1, 0]` for a vertical spin.
5. Save and play.

**Expected result:** the marker rotates during play. It is decoration, so it needs no physics body. Component vector fields use runtime coordinates; Blender's vertical transform axis is Z, whereas the runtime's vertical axis is Y. [The gameplay chapter](05-gameplay.md#coordinates-and-units) explains the conversion.

## Change the title screen

1. Stop the game and choose **GameStudio > Edit Game UI**. Save the level when prompted.
2. Select the **Title** scene using Blender's scene selector.
3. Select the title text in the viewport or Outliner.
4. Edit its text in Blender to say **Garden Quest** if it does not already use the new project's name.
5. Save `ui/ui.blend`.
6. Choose **GameStudio > Back to Level**, then start the game again.

**Expected result:** the title screen displays your text and its original buttons still work. Edit the visible text body rather than renaming components or changing button actions while learning the layout.

You can also change HUD text from `Score {score}` to `Points {score}`. Keep `{score}` intact: it is the binding that displays the live score. If you use another language, also update its translation table as described in [Translations](07-interface.md#translations).

## Test the complete loop

Run from the title screen and check these behaviors in order:

1. Start begins play and the player responds to movement.
2. Every pickup is reachable and awards its intended points.
3. The five-point bonus changes the HUD by five.
4. The final pickup opens the win screen.
5. Restart restores the starting state.
6. Pause and options can be opened and closed without trapping input.

Inspect the Console after the test. If a change fails, reverse only that change and repeat the same steps so the cause stays clear.

## Create a build

Save the level and UI. In **Properties > Scene > Game Project**, use **Levels > Export All**, then expand **Build** and choose **Build Web**. Inspect the build log and output folder. The static game is written to the project's `dist/` directory.

Test that output through an HTTP server before uploading it; double-clicking `index.html` is not an equivalent test of a browser game that loads assets. [Testing and web builds](09-publishing.md) gives the full process.

You now have a repeatable editing loop: change source, inspect it in the game, save it, test from the beginning and build. Continue with templates for a larger starting point, or add your own data and logic in [Components and game logic](05-gameplay.md).
