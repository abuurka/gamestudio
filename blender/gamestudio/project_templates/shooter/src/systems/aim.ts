import { defineSystem } from '@gamestudio/engine';
import { Plane, Raycaster, Vector2, Vector3 } from 'three';
import { AimMarker } from '../components/aim-marker';
import { Shooter } from '../components/shooter';
import { arena } from '../lib/arena';

const STICK_DEADZONE = 0.3;
const raycaster = new Raycaster();
const ndc = new Vector2();
const plane = new Plane(new Vector3(0, 1, 0), 0);
const aim = new Vector3();

/** Turns each shooter toward the mouse's point at gun height (or the right stick) and moves the AimMarker there. */
export default defineSystem({
  name: 'AimSystem',
  query: [Shooter],

  start(ctx) {
    ctx.three.renderer.domElement.style.cursor = 'crosshair';
  },

  update(ctx, shooters) {
    const marker = ctx.world.query(AimMarker)[0];
    const pad = ctx.input.gamepad;
    const stickX = pad?.axes[2] ?? 0;
    const stickY = pad?.axes[3] ?? 0;
    for (const player of shooters) {
      if (arena.over) continue;
      const feet = player.object3d.position; // kinematic bodies sit at the scene root: local = world
      if (Math.hypot(stickX, stickY) > STICK_DEADZONE) {
        aim.set(feet.x + stickX * 6, feet.y, feet.z + stickY * 6); // the camera looks along -Z: stick down is +Z
      } else {
        plane.constant = -(feet.y + player.get(Shooter).muzzleHeight);
        raycaster.setFromCamera(ndc.set(ctx.input.mouse.nx, ctx.input.mouse.ny), ctx.three.camera);
        if (!raycaster.ray.intersectPlane(plane, aim)) continue;
      }
      // Yaw 0 faces +Z (Blender's front), as the CharacterController uses; its turnSpeed is 0 here.
      player.object3d.rotation.y = Math.atan2(aim.x - feet.x, aim.z - feet.z);
      marker?.object3d.position.set(aim.x, feet.y + 0.03, aim.z);
    }
  },
});
