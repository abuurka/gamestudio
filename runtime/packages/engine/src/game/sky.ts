// The scene background and fill light from the exported sky (`settings.sky`, the `sky` kit's
// gradient): ground below the horizon, then horizon to zenith, as Blender's world draws it. Also
// the game shaders' `ctx.sky` uniforms, which follow the same settings.

import {
  Color,
  DataTexture,
  EquirectangularReflectionMapping,
  LinearFilter,
  LinearSRGBColorSpace,
  RGBAFormat,
  SRGBColorSpace,
  UnsignedByteType,
  type HemisphereLight,
} from 'three';
import type { SceneSettings, SkySettings } from '../scene/manifest';
import type { ShaderSky } from '../schema/shaders';

type RGB = [number, number, number];

/** The background (linear RGB) of a scene whose settings have neither a sky nor a background colour. */
export const DEFAULT_BACKGROUND_RGB: Readonly<RGB> = [0.05, 0.05, 0.08];

/** A new `ctx.sky` uniform set: the default background in every direction, no fog. */
export function createSkyUniforms(): ShaderSky {
  const [r, g, b] = DEFAULT_BACKGROUND_RGB;
  const color = (): { value: Color } => ({ value: new Color().setRGB(r, g, b, LinearSRGBColorSpace) });
  return { zenith: color(), horizon: color(), ground: color(), fog: color(), fogNear: { value: 0 }, fogFar: { value: 0 } };
}

/**
 * Sets the `ctx.sky` uniforms (in place: shaders hold these objects) from a scene's settings, as
 * the game draws them: the gradient sky, else the flat background colour in every direction (else
 * the default background); the fog, or the horizon's colour and distances of 0 without fog.
 */
export function updateSkyUniforms(uniforms: ShaderSky, settings: Pick<SceneSettings, 'sky' | 'background' | 'fog'>): void {
  const set = (target: { value: Color }, c: Readonly<RGB>): void => void target.value.setRGB(c[0], c[1], c[2], LinearSRGBColorSpace);
  const { sky, fog } = settings;
  const flat = settings.background ?? DEFAULT_BACKGROUND_RGB;
  set(uniforms.zenith, sky?.zenith ?? flat);
  set(uniforms.horizon, sky?.horizon ?? flat);
  set(uniforms.ground, sky?.ground ?? flat);
  set(uniforms.fog, fog?.color ?? sky?.horizon ?? flat);
  uniforms.fogNear.value = fog ? fog.near : 0;
  uniforms.fogFar.value = fog ? fog.far : 0;
}

const mix = (a: readonly number[], b: readonly number[], t: number): RGB => [
  a[0]! + (b[0]! - a[0]!) * t,
  a[1]! + (b[1]! - a[1]!) * t,
  a[2]! + (b[2]! - a[2]!) * t,
];

/**
 * Linear colour of the sky in a direction with this height (-1 straight down .. 1 straight up). The
 * stops match the sky kit's colour ramp: ground up to just below the horizon, then horizon, a mix
 * of horizon and zenith a little higher, and the zenith overhead.
 */
export function skyColor(sky: SkySettings, y: number): RGB {
  const t = Math.min(1, Math.max(0, y * 0.5 + 0.5));
  const mid = mix(sky.horizon, sky.zenith, 0.45);
  if (t <= 0.47) return [...sky.ground];
  if (t <= 0.5) return mix(sky.ground, sky.horizon, (t - 0.47) / 0.03);
  if (t <= 0.6) return mix(sky.horizon, mid, (t - 0.5) / 0.1);
  return mix(mid, sky.zenith, (t - 0.6) / 0.4);
}

const toSrgb = (c: number): number => {
  const v = Math.min(1, Math.max(0, c));
  return v <= 0.0031308 ? v * 12.92 : 1.055 * Math.pow(v, 1 / 2.4) - 0.055;
};

const HEIGHT = 512;

/** An equirectangular background with the gradient (sRGB, so it is drawn as authored, like a flat background colour). */
export function skyTexture(sky: SkySettings): DataTexture {
  const width = 4;
  const data = new Uint8Array(width * HEIGHT * 4);
  for (let row = 0; row < HEIGHT; row++) {
    const latitude = ((row + 0.5) / HEIGHT - 0.5) * Math.PI;
    const [r, g, b] = skyColor(sky, Math.sin(latitude)).map(toSrgb) as RGB;
    for (let x = 0; x < width; x++) {
      const i = (row * width + x) * 4;
      data[i] = Math.round(r * 255);
      data[i + 1] = Math.round(g * 255);
      data[i + 2] = Math.round(b * 255);
      data[i + 3] = 255;
    }
  }
  const texture = new DataTexture(data, width, HEIGHT, RGBAFormat, UnsignedByteType);
  texture.name = 'gs_sky';
  texture.mapping = EquirectangularReflectionMapping;
  texture.colorSpace = SRGBColorSpace;
  texture.magFilter = LinearFilter;
  texture.minFilter = LinearFilter;
  texture.needsUpdate = true;
  return texture;
}

const DEFAULT_SKY = new Color(0xdfe8ff);
const DEFAULT_GROUND = new Color(0x3a342c);

/** Tints the hemisphere fill light like the sky (or back to the neutral default). */
export function tintFillLight(light: HemisphereLight, sky: SkySettings | null): void {
  if (!sky) {
    light.color.copy(DEFAULT_SKY);
    light.groundColor.copy(DEFAULT_GROUND);
    return;
  }
  const [r, g, b] = mix(sky.horizon, sky.zenith, 0.5);
  light.color.setRGB(r, g, b, LinearSRGBColorSpace);
  light.groundColor.setRGB(sky.ground[0], sky.ground[1], sky.ground[2], LinearSRGBColorSpace);
}
