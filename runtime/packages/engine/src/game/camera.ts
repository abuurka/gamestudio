// Active camera selection and aspect handling.

import { Camera, PerspectiveCamera, type Object3D, type OrthographicCamera } from 'three';
import type { World } from '../ecs/world';

/** First camera at or below `object`. */
export function findCamera(object: Object3D | undefined): Camera | undefined {
  let found: Camera | undefined;
  object?.traverse((o) => {
    if (!found && o instanceof Camera) found = o;
  });
  return found;
}

/** `settings.activeCamera`, else the first camera in the scene, else `undefined`. */
export function pickCamera(world: World, root: Object3D, activeId: string | null): Camera | undefined {
  return findCamera(world.get(activeId)?.object3d) ?? findCamera(root);
}

export function createDefaultCamera(): PerspectiveCamera {
  const camera = new PerspectiveCamera(50, 16 / 9, 0.1, 1000);
  camera.name = 'gs_default_camera';
  camera.position.set(0, 6, 12);
  camera.lookAt(0, 0, 0);
  return camera;
}

/**
 * Remembers a Blender camera's horizontal field of view (from the exported
 * yfov and aspect ratio) so resizing keeps Blender's framing ("Auto" sensor
 * fit: the larger dimension keeps the sensor's FOV).
 */
export function markBlenderCamera(camera: Camera): void {
  if (camera instanceof PerspectiveCamera && camera.userData.gsHFov === undefined) {
    const vfov = (camera.fov * Math.PI) / 180;
    camera.userData.gsHFov = 2 * Math.atan(Math.tan(vfov / 2) * (camera.aspect || 1));
  }
}

export function fitCamera(camera: Camera, aspect: number): void {
  if (!Number.isFinite(aspect) || aspect <= 0) return;
  if ((camera as OrthographicCamera).isOrthographicCamera) {
    fitOrthographic(camera as OrthographicCamera, aspect);
    return;
  }
  if (!(camera instanceof PerspectiveCamera)) return;
  const hfov = camera.userData.gsHFov as number | undefined;
  let fov = camera.fov;
  if (hfov !== undefined) {
    const v = aspect >= 1 ? 2 * Math.atan(Math.tan(hfov / 2) / aspect) : hfov;
    fov = (v * 180) / Math.PI;
  }
  if (camera.aspect !== aspect || camera.fov !== fov) {
    camera.aspect = aspect;
    camera.fov = fov;
    camera.updateProjectionMatrix();
  }
}

/**
 * An orthographic camera keeps its height (top - bottom: Blender's ortho scale across the render
 * height) and widens or narrows to the view, about its horizontal centre, so nothing stretches.
 * Zoom with `camera.zoom`, which this leaves alone.
 */
export function fitOrthographic(camera: OrthographicCamera, aspect: number): void {
  const halfWidth = ((camera.top - camera.bottom) / 2) * aspect;
  const centre = (camera.left + camera.right) / 2;
  const left = centre - halfWidth;
  const right = centre + halfWidth;
  if (camera.left === left && camera.right === right) return;
  camera.left = left;
  camera.right = right;
  camera.updateProjectionMatrix();
}
