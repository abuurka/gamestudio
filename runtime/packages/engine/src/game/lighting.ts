// Lighting defaults so Blender-exported scenes look right and are never black.

import {
  Box3,
  type Camera,
  DirectionalLight,
  HemisphereLight,
  Light,
  Mesh,
  OrthographicCamera,
  Sphere,
  Vector3,
  type Object3D,
} from 'three';

/**
 * Blender's glTF exporter ("Standard" lighting mode) multiplies light power by
 * 683 lm/W (a 3 W/m² sun becomes 2049 lux). Three.js has no camera exposure, so
 * we undo that factor: the scene then looks as bright as it does in Blender.
 */
export const BLENDER_LIGHT_SCALE = 1 / 683;

/** Low-intensity sky/ground fill so unlit areas are never pure black. */
export function createFillLight(intensity = 0.6): HemisphereLight {
  const light = new HemisphereLight(0xdfe8ff, 0x3a342c, intensity);
  light.name = 'gs_fill_light';
  return light;
}

/**
 * Enables shadows on meshes, rescales imported light intensities and returns
 * the shadow-casting directional lights. Adds a default sun if the scene has no
 * lights (unless `defaultSun` is false, as for prefab templates).
 */
export function prepareLighting(
  root: Object3D,
  lightScale: number,
  { defaultSun = true }: { defaultSun?: boolean } = {},
): DirectionalLight[] {
  const suns: DirectionalLight[] = [];
  let lights = 0;
  root.traverse((object) => {
    if (object instanceof Mesh) {
      object.castShadow = true;
      object.receiveShadow = true;
    } else if (object instanceof Light) {
      lights++;
      object.intensity *= lightScale;
      if (object instanceof DirectionalLight) suns.push(object);
    }
  });
  if (lights === 0 && defaultSun) {
    const sun = new DirectionalLight(0xffffff, 2.5);
    sun.name = 'gs_default_sun';
    sun.position.set(5, 10, 7);
    root.add(sun); // Its target stays at the origin.
    suns.push(sun);
  }
  for (const sun of suns) {
    sun.castShadow = true;
    sun.shadow.mapSize.set(2048, 2048);
    sun.shadow.bias = -0.0005;
    sun.shadow.normalBias = 0.02;
  }
  return suns;
}

/** Shadows reach this far (metres, radius) around the view in levels bigger than that. */
export const SHADOW_DISTANCE = 45;

/**
 * Fits each sun's orthographic shadow camera: around the whole scene when it is small, else around
 * the part the camera looks at (SHADOW_DISTANCE), snapped to shadow-map texels so edges do not
 * shimmer as the view moves. Biases follow the texel size, so big shadow cameras do not get acne
 * (stripes on long slopes).
 */
export class ShadowFitter {
  private readonly bounds = new Sphere();
  private readonly probe = new OrthographicCamera();
  private readonly center = new Vector3();
  private readonly scene = new Vector3();
  private readonly target = new Vector3();
  private readonly view = new Vector3();
  private readonly forward = new Vector3();

  constructor(
    private readonly suns: DirectionalLight[],
    root: Object3D,
    private readonly distance = SHADOW_DISTANCE,
  ) {
    const box = new Box3().setFromObject(root);
    if (box.isEmpty()) box.set(new Vector3(-10, -1, -10), new Vector3(10, 5, 10));
    box.getBoundingSphere(this.bounds);
    this.bounds.radius = Math.max(this.bounds.radius * 1.1, 1);
  }

  /** The radius the shadow cameras cover. */
  get radius(): number {
    return Math.min(this.bounds.radius, this.distance);
  }

  update(camera?: Camera): void {
    const sceneRadius = this.bounds.radius;
    const radius = this.radius;
    const follow = !!camera && sceneRadius > this.distance;
    if (follow) {
      camera.getWorldPosition(this.view);
      camera.getWorldDirection(this.forward);
      this.view.addScaledVector(this.forward, radius * 0.6);
    }
    for (const sun of this.suns) {
      if (!sun.parent || !sun.castShadow) continue;
      sun.updateWorldMatrix(true, true);
      // Mirror DirectionalLightShadow.updateMatrices to work in light space.
      this.probe.position.setFromMatrixPosition(sun.matrixWorld);
      this.probe.lookAt(this.target.setFromMatrixPosition(sun.target.matrixWorld));
      this.probe.updateMatrixWorld();
      this.scene.copy(this.bounds.center).applyMatrix4(this.probe.matrixWorldInverse);
      this.center.copy(follow ? this.view : this.bounds.center).applyMatrix4(this.probe.matrixWorldInverse);
      const texel = (2 * radius) / sun.shadow.mapSize.x;
      if (follow) {
        this.center.x = Math.round(this.center.x / texel) * texel;
        this.center.y = Math.round(this.center.y / texel) * texel;
      }
      // Depth covers the whole scene, so casters outside the view still cast.
      const depth = -this.scene.z;
      const cam = sun.shadow.camera;
      const near = depth - sceneRadius;
      const far = depth + sceneRadius;
      if (cam.left !== this.center.x - radius || cam.near !== near || cam.top !== this.center.y + radius) {
        cam.left = this.center.x - radius;
        cam.right = this.center.x + radius;
        cam.bottom = this.center.y - radius;
        cam.top = this.center.y + radius;
        cam.near = near;
        cam.far = far;
        cam.updateProjectionMatrix();
        sun.shadow.normalBias = Math.max(0.02, texel * 1.2);
        sun.shadow.bias = -Math.max(0.0001, (texel * 0.5) / (far - near));
      }
    }
  }
}
