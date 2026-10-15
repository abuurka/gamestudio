// After FX Lab "wind-meadow" (blades bent along a length-keeping curve by gust fronts, flutter, the
// sheen wave, translucency, shade at the roots) and "wind-blade" (the same field). Bends each mesh or
// GPU instance from its own base.

import { Float32BufferAttribute, type BufferGeometry, type Material, type MeshStandardMaterial } from 'three';
import { patchShader, type ShaderApply, type ShaderEdit, type ShaderMesh } from '../../../schema/shaders';
import { shadowTwins, windGlsl } from '../foliage-sway/wind';
import type { fields } from './meta';

type Uniforms = Record<string, { value: unknown }>;

/**
 * Per vertex, written once per geometry from its bounds: x = how far up the blade (0 at the base,
 * 1 at the top), y = the geometry's height in its own units. So any blade or clump bends from its
 * base to its tip whatever its size, and instances share it.
 */
const BEND = 'gsGrassBend';

const VERTEX_PARS = /* glsl */ `
uniform float uTime;
uniform float uGrassWind;
uniform float uGrassAngle;
uniform float uGrassSpeed;
uniform float uGrassBend;
uniform float uGrassFlutter;
attribute vec2 ${BEND};
varying vec2 vGrass;
${windGlsl}`;

// After begin_vertex. A quadratic Bezier from the root: the control point sits straight up at half
// height, so the sideways push grows with height squared and the tip stays on a circle that keeps the
// blade's length (it dips as it bends). Worked out in world space, turned back into the object's.
const BLADE = /* glsl */ `
{
  mat4 grassModel = modelMatrix;
#ifdef USE_INSTANCING
  grassModel = modelMatrix * instanceMatrix;
#endif
  mat3 grassBasis = mat3(grassModel);
  vec3 grassOrigin = grassModel[3].xyz;
  vec3 grassWorld = grassOrigin + grassBasis * transformed;
  float grassT = clamp(${BEND}.x, 0.0, 1.0);
  float grassH = ${BEND}.y * length(grassBasis[1]);
  float grassS = clamp(uGrassWind, 0.0, 1.5);
  float grassTime = uTime * uGrassSpeed;
  vec2 grassDir = gsWindDir(uGrassAngle);
  vec2 grassSide = vec2(-grassDir.y, grassDir.x);
  float grassSeed = gsWindHash(floor(grassOrigin.xz * 5.0) + 0.5);
  float grassGust = gsWindGust(grassWorld.xz, grassDir, gsWindTravel(grassTime, grassS));
  // Base sway, a gust push (stronger for tall blades) and a quick sideways flutter.
  float grassSway = sin(grassTime * (1.3 + 2.4 * grassS) + dot(grassWorld.xz, grassDir) * 0.45 + grassSeed * 6.2832);
  float grassFlutter = sin(grassTime * (6.0 + 7.0 * grassSeed) + grassSeed * 37.0 + dot(grassWorld.xz, vec2(2.7, 1.9))) * (0.25 + grassGust);
  float grassFlex = mix(0.75, 1.25, grassSeed) * clamp(grassH * 1.6, 0.6, 1.5);
  float grassPush = grassS * (0.1 + 0.95 * grassGust) * grassFlex + grassSway * (0.035 + 0.1 * grassS);
  vec2 grassBendV = (grassDir * grassPush + grassSide * grassFlutter * (0.012 + 0.06 * grassS) * uGrassFlutter) * uGrassBend;
  float grassAngle = min(length(grassBendV), 1.3);
  vec2 grassTo = grassBendV / max(length(grassBendV), 1e-4);
  float grassArc = grassH * 3.0 / (2.5 + sqrt(1.25 - cos(grassAngle)));
  vec3 grassP1 = vec3(0.0, 0.5 * grassArc, 0.0);
  vec3 grassP2 = vec3(grassTo.x * sin(grassAngle), cos(grassAngle), grassTo.y * sin(grassAngle)) * grassArc;
  vec3 grassSpine = 2.0 * (1.0 - grassT) * grassT * grassP1 + grassT * grassT * grassP2;
  transformed += inverse(grassBasis) * (grassSpine - vec3(0.0, grassT * grassH, 0.0));
  vGrass = vec2(grassT, grassGust);
}`;

const FRAGMENT_PARS = /* glsl */ `
uniform float uGrassWind;
uniform float uGrassSheen;
uniform float uGrassTranslucency;
uniform float uGrassRootShade;
varying vec2 vGrass;`;

// After color_fragment: bent blades show their paler, silvery side and calm troughs stay darker, so
// gust fronts read as bright waves rolling over the field.
const SHEEN = /* glsl */ `
{
  float grassWave = clamp(uGrassWind, 0.0, 1.0) * smoothstep(0.15, 0.9, vGrass.x) * uGrassSheen;
  diffuseColor.rgb *= 1.0 - 0.3 * grassWave * (1.0 - vGrass.y);
  diffuseColor.rgb = mix(diffuseColor.rgb, vec3(dot(diffuseColor.rgb, vec3(0.3, 0.6, 0.1))) * vec3(1.2, 1.2, 0.8) * 1.9, vGrass.y * grassWave * 0.6);
}`;

// After lights_fragment_begin (directLight still holds the shadowed sun): wrapped light through the
// blade plus a forward lobe when looking into the sun; chlorophyll saturates what comes through.
const TRANSLUCENCY = /* glsl */ `
#if NUM_DIR_LIGHTS > 0
{
  float grassBack = clamp(-dot(geometryNormal, directLight.direction) * 0.6 + 0.4, 0.0, 1.0);
  float grassForward = pow(clamp(dot(-geometryViewDir, directLight.direction), 0.0, 1.0), 8.0);
  float grassThin = mix(0.2, 1.0, smoothstep(0.0, 0.8, vGrass.x));
  vec3 grassTransmit = mix(diffuseColor.rgb, diffuseColor.rgb * diffuseColor.rgb * 4.0, 0.7);
  reflectedLight.directDiffuse += directLight.color * grassTransmit * (grassBack * 0.5 + grassForward * 3.0) * grassThin * uGrassTranslucency;
}
#endif`;

// After lights_fragment_end: blades shade each other towards the base.
const ROOT_SHADE = /* glsl */ `
{
  float grassAo = mix(1.0, mix(0.16, 1.0, smoothstep(0.0, 0.75, vGrass.x)), uGrassRootShade);
  reflectedLight.indirectDiffuse *= grassAo;
  reflectedLight.directDiffuse *= mix(0.35, 1.0, grassAo);
  reflectedLight.directSpecular *= grassAo;
}`;

function wire(shader: { uniforms: Uniforms }, u: Uniforms, time: { value: number }): void {
  shader.uniforms.uTime = time;
  shader.uniforms.uGrassWind = u.wind!;
  shader.uniforms.uGrassAngle = u.windAngle!;
  shader.uniforms.uGrassSpeed = u.speed!;
  shader.uniforms.uGrassBend = u.bend!;
  shader.uniforms.uGrassFlutter = u.flutter!;
}

/** The vertex motion alone, for the shadow pass. */
function shadowEdit(u: Uniforms, time: { value: number }): ShaderEdit {
  return (shader) => {
    wire(shader, u, time);
    shader.vertexShader = shader.vertexShader
      .replace('#include <common>', `#include <common>\n${VERTEX_PARS}`)
      .replace('#include <begin_vertex>', `#include <begin_vertex>\n${BLADE}`);
  };
}

/** Writes the blade attribute once per geometry. Blades stand on the origin unless they reach below it. */
export function bendAttribute(geometry: BufferGeometry): void {
  if (geometry.getAttribute(BEND)) return;
  const position = geometry.getAttribute('position');
  if (!position) return;
  let low = 0, high = -Infinity;
  for (let i = 0; i < position.count; i++) {
    const y = position.getY(i);
    if (y < low) low = y;
    if (y > high) high = y;
  }
  const height = Math.max(high - low, 1e-4);
  const data = new Float32Array(position.count * 2);
  for (let i = 0; i < position.count; i++) {
    data[i * 2] = (position.getY(i) - low) / height;
    data[i * 2 + 1] = height;
  }
  geometry.setAttribute(BEND, new Float32BufferAttribute(data, 2));
}

export const apply: ShaderApply<typeof fields> = (material, _values, ctx) => {
  if (!(material as MeshStandardMaterial).isMeshStandardMaterial) {
    throw new Error('GrassWind needs a Principled BSDF material (MeshStandardMaterial in the game)');
  }
  const u = ctx.uniforms as unknown as Uniforms;
  patchShader(material, 'gs-grass-wind', (shader) => {
    wire(shader, u, ctx.time);
    shader.uniforms.uGrassSheen = u.sheen!;
    shader.uniforms.uGrassTranslucency = u.translucency!;
    shader.uniforms.uGrassRootShade = u.rootShade!;
    shader.vertexShader = shader.vertexShader
      .replace('#include <common>', `#include <common>\n${VERTEX_PARS}`)
      .replace('#include <begin_vertex>', `#include <begin_vertex>\n${BLADE}`);
    shader.fragmentShader = shader.fragmentShader
      .replace('#include <common>', `#include <common>\n${FRAGMENT_PARS}`)
      .replace('#include <color_fragment>', `#include <color_fragment>\n${SHEEN}`)
      .replace('#include <lights_fragment_begin>', `#include <lights_fragment_begin>\n${TRANSLUCENCY}`)
      .replace('#include <lights_fragment_end>', `#include <lights_fragment_end>\n${ROOT_SHADE}`);
  });
};

export const mesh: ShaderMesh<typeof fields> = (target, _values, ctx) => {
  bendAttribute(target.geometry);
  const drawn = target.material as Material | Material[];
  if (Array.isArray(drawn)) return;
  shadowTwins(target, drawn, 'gs-grass-wind', shadowEdit(ctx.uniforms as unknown as Uniforms, ctx.time));
};
