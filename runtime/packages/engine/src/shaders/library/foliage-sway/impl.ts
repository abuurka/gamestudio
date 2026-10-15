// After FX Lab "blossom-fall": the canopy's gust sway, leafy rustle and sun through the petals; the
// height-weighted bend of "kelp-forest". Bends from each object's (or GPU instance's) own origin.

import { DoubleSide, Float32BufferAttribute, type BufferGeometry, type Material, type MeshStandardMaterial } from 'three';
import { patchShader, type ShaderApply, type ShaderEdit, type ShaderMesh } from '../../../schema/shaders';
import type { fields } from './meta';
import { shadowTwins, windGlsl } from './wind';

type Uniforms = Record<string, { value: unknown }>;

/**
 * Per vertex, the same for a whole geometry: where it bends from, in its own units below the origin.
 * 0 when the geometry stands on its origin (a kit tree's leaves, a scattered bush); its lowest point
 * when it reaches below the origin (a crown object whose origin is its centre), so that part stays
 * put where it meets the trunk.
 */
const BASE = 'gsSwayBase';

const VERTEX_PARS = /* glsl */ `
uniform float uTime;
uniform float uSwayWind;
uniform float uSwayAngle;
uniform float uSwaySpeed;
uniform float uSwayAmount;
uniform float uSwayRustle;
uniform float uSwayHeight;
attribute float ${BASE};
${windGlsl}`;

// After begin_vertex. The offset is worked out in world space (wind, gusts, height in metres) and
// turned back into the object's own space, so every later chunk (and the shadow pass) sees it.
const SWAY = /* glsl */ `
{
  mat4 swayModel = modelMatrix;
#ifdef USE_INSTANCING
  swayModel = modelMatrix * instanceMatrix;
#endif
  mat3 swayBasis = mat3(swayModel);
  vec3 swayOrigin = swayModel[3].xyz;
  vec3 swayWorld = swayOrigin + swayBasis * transformed;
  float swayH = max(transformed.y - ${BASE}, 0.0) * length(swayBasis[1]);
  float swayFlex = pow(min(swayH / max(uSwayHeight, 0.05), 2.0), 1.5);
  float swayS = clamp(uSwayWind, 0.0, 1.5);
  float swayT = uTime * uSwaySpeed;
  vec2 swayDir = gsWindDir(uSwayAngle);
  vec2 swaySide = vec2(-swayDir.y, swayDir.x);
  float swaySeed = gsWindHash(floor(swayOrigin.xz * 5.0) + 0.5);
  float swayGust = gsWindGust(swayWorld.xz, swayDir, gsWindTravel(swayT, swayS));
  float swayPhase = swayT * (1.3 + 2.4 * swayS) * 0.8 + dot(swayWorld.xz, swayDir) * 0.12 + swaySeed * 6.2832;
  vec2 swayBend = swayDir * (swayS * (0.12 + 0.45 * swayGust) + sin(swayPhase) * (0.03 + 0.1 * swayS))
    + swaySide * sin(swayPhase * 1.37 + 1.3) * (0.02 + 0.05 * swayS);
  vec3 swayOffset = vec3(swayBend.x, -0.15 * dot(swayBend, swayBend), swayBend.y) * swayFlex * uSwayAmount;
  // Leafy rustle: small, quick, incoherent motion over the crown, none at the ground.
  vec3 swayP = swayWorld * 2.3;
  float swayR = swayT * (2.0 + 4.0 * swayS) + swaySeed * 20.0;
  vec3 swayJitter = vec3(
    sin(swayR * 1.1 + dot(swayP, vec3(1.0, 0.6, 0.8))),
    sin(swayR * 0.9 + dot(swayP, vec3(-0.7, 1.1, 0.5))),
    sin(swayR * 1.3 + dot(swayP, vec3(0.6, -0.5, 1.2)))) * 0.5;
  swayOffset += swayJitter * uSwayRustle * (0.03 + 0.12 * swayS + 0.12 * swayGust * swayS) * smoothstep(0.0, 1.0, swayH);
  transformed += inverse(swayBasis) * swayOffset;
}`;

// After lights_fragment_begin, where directLight still holds the (shadowed) sun: light through the
// leaves when the sun is behind them, and a bright rim where the crown thins out at its edge.
const TRANSLUCENCY = /* glsl */ `
#if NUM_DIR_LIGHTS > 0
{
  float swayFacing = dot(geometryNormal, directLight.direction);
  float swayBack = clamp(-swayFacing * 0.5 + 0.5, 0.0, 1.0);
  float swayRim = pow(1.0 - clamp(dot(geometryNormal, geometryViewDir), 0.0, 1.0), 2.0);
  vec3 swayTransmit = diffuseColor.rgb * mix(diffuseColor.rgb, vec3(1.0), 0.45);
  // Faces turned from the sun lie in the crown's own shadow, where the shadow map only has acne:
  // they take the unshadowed sun (light through the leaves), faces towards it the shadowed one.
  vec3 swaySun = mix(directLight.color, directionalLight.color, smoothstep(0.0, -0.25, swayFacing));
  reflectedLight.directDiffuse += swaySun * swayTransmit * (swayBack * 0.3 + swayRim * (0.25 + 0.9 * swayBack)) * uSwayTranslucency;
}
#endif`;

function wire(shader: { uniforms: Uniforms }, u: Uniforms, time: { value: number }): void {
  shader.uniforms.uTime = time;
  shader.uniforms.uSwayWind = u.wind!;
  shader.uniforms.uSwayAngle = u.windAngle!;
  shader.uniforms.uSwaySpeed = u.speed!;
  shader.uniforms.uSwayAmount = u.sway!;
  shader.uniforms.uSwayRustle = u.rustle!;
  shader.uniforms.uSwayHeight = u.height!;
}

/** The vertex motion alone, for the shadow pass. */
function shadowEdit(u: Uniforms, time: { value: number }): ShaderEdit {
  return (shader) => {
    wire(shader, u, time);
    shader.vertexShader = shader.vertexShader
      .replace('#include <common>', `#include <common>\n${VERTEX_PARS}`)
      .replace('#include <begin_vertex>', `#include <begin_vertex>\n${SWAY}`);
  };
}

/** Writes the bend base once per geometry (meshes and instances share theirs). */
export function baseAttribute(geometry: BufferGeometry): void {
  if (geometry.getAttribute(BASE)) return;
  const position = geometry.getAttribute('position');
  if (!position) return;
  let low = 0;
  for (let i = 0; i < position.count; i++) low = Math.min(low, position.getY(i));
  geometry.setAttribute(BASE, new Float32BufferAttribute(new Float32Array(position.count).fill(low), 1));
}

export const apply: ShaderApply<typeof fields> = (material, values, ctx) => {
  if (!(material as MeshStandardMaterial).isMeshStandardMaterial) {
    throw new Error('FoliageSway needs a Principled BSDF material (MeshStandardMaterial in the game)');
  }
  if (values.leaves === 'cards') {
    // Cut-out leaf cards: alpha-tested (the shadow pass copies the test), drawn from both sides.
    material.transparent = false;
    material.alphaTest = 0.5;
    material.depthWrite = true;
    material.side = DoubleSide;
  }
  const u = ctx.uniforms as unknown as Uniforms;
  patchShader(material, 'gs-foliage-sway', (shader) => {
    wire(shader, u, ctx.time);
    shader.uniforms.uSwayTranslucency = u.translucency!;
    shader.vertexShader = shader.vertexShader
      .replace('#include <common>', `#include <common>\n${VERTEX_PARS}`)
      .replace('#include <begin_vertex>', `#include <begin_vertex>\n${SWAY}`);
    shader.fragmentShader = shader.fragmentShader
      .replace('#include <common>', '#include <common>\nuniform float uSwayTranslucency;')
      .replace('#include <lights_fragment_begin>', `#include <lights_fragment_begin>\n${TRANSLUCENCY}`);
  });
};

export const mesh: ShaderMesh<typeof fields> = (target, _values, ctx) => {
  baseAttribute(target.geometry);
  const drawn = target.material as Material | Material[];
  // glTF meshes draw one material each; a multi-material mesh keeps three's still shadow.
  if (Array.isArray(drawn)) return;
  shadowTwins(target, drawn, 'gs-foliage-sway', shadowEdit(ctx.uniforms as unknown as Uniforms, ctx.time));
};
