// After FX Lab "flake-paint".
// Candy flake paint on the Blender material: its base colour flips hue towards the flanks and
// darkens at grazing angles, metallic flakes hashed per cell in object space glint against every
// light (resolving to a sheen once smaller than a pixel), under a clearcoat. The clearcoat is
// three.js's own, so the material becomes a MeshPhysicalMaterial copy of the Blender material.

import { MeshPhysicalMaterial, MeshStandardMaterial } from 'three';
import type { Material } from 'three';
import { patchShader, type ShaderApply } from '../../../schema/shaders';
import type { fields } from './meta';

const VERTEX_HEAD = /* glsl */ `
varying vec3 vGsPaintP;
`;

const VERTEX_BODY = /* glsl */ `
vGsPaintP = position;
#ifdef USE_INSTANCING
  vGsPaintP += instanceMatrix[3].xyz * 1.37;
#endif
`;

// gsPaintHash33 is David Hoskins' hash33 ("Hash without Sine", https://www.shadertoy.com/view/4djSRW,
// MIT; THIRD-PARTY-NOTICES.md).
const FRAGMENT_HEAD = /* glsl */ `
uniform float uPaintFlip;
uniform float uPaintFlakes;
uniform float uPaintFlakeSize;
uniform float uPaintCoat;
uniform float uPaintCoatRoughness;
varying vec3 vGsPaintP;
vec3 gsFlakeNA = vec3(0.0, 0.0, 1.0);
vec3 gsFlakeNB = vec3(0.0, 0.0, 1.0);
float gsFlakeCA = 0.0;
float gsFlakeCB = 0.0;
float gsFlakeSheen = 0.0;
vec3 gsFlakeTint = vec3(1.0);

vec3 gsPaintHash33(vec3 p3) {
  p3 = fract(p3 * vec3(0.1031, 0.1030, 0.0973));
  p3 += dot(p3, p3.yxz + 33.33);
  return fract((p3.xxy + p3.yxx) * p3.zyx);
}
// Rotates a linear RGB colour's hue by 'turns' (around the grey axis).
vec3 gsPaintHue(vec3 c, float turns) {
  float a = turns * 6.2831853;
  const vec3 k = vec3(0.57735027);
  float ca = cos(a);
  return max(c * ca + cross(k, c) * sin(a) + k * dot(k, c) * (1.0 - ca), vec3(0.0));
}
// One flake layer: coverage of this pixel's cell and the flake's normal (view space). The tilt is a
// world-space offset, so a flake keeps its orientation as the camera moves.
float gsFlakeLayer(vec3 cellP, vec3 nWorld, out vec3 flakeN) {
  vec3 id = floor(cellP);
  vec3 f = fract(cellP) - 0.5;
  vec3 rnd = gsPaintHash33(id);
  vec3 c = (rnd - 0.5) * 0.4;
  vec3 tilt = (gsPaintHash33(id + 17.31) - 0.5) * 0.85;
  flakeN = normalize((viewMatrix * vec4(normalize(nWorld + tilt), 0.0)).xyz);
  return smoothstep(0.38, 0.26, length(f - c)) * step(0.35, rnd.z);
}
`;

// After normal_fragment_maps: colour flip and flakes (normal and view direction are known here).
const FRAGMENT_PAINT = /* glsl */ `
{
  vec3 gsV = isOrthographic ? vec3(0.0, 0.0, 1.0) : normalize(vViewPosition);
  float ndv = saturate(dot(normal, gsV));
  vec3 base = diffuseColor.rgb;
  // Colour flip: face colour head-on, flip hue toward the flank, darker edge hue at grazing.
  vec3 c = mix(gsPaintHue(base, uPaintFlip), base, smoothstep(0.2, 0.9, ndv));
  c = mix(gsPaintHue(base, -1.5 * uPaintFlip) * 0.75, c, smoothstep(0.0, 0.35, ndv));
  // Candy depth: a longer path through the tinted coat saturates.
  diffuseColor.rgb = pow(max(c, vec3(0.0)), vec3(1.0 + (1.0 - ndv) * 1.2));
  gsFlakeTint = mix(base, vec3(1.0), 0.65) * uPaintFlakes * (1.0 - 0.6 * pow(1.0 - ndv, 3.0));

  // Flakes in two sizes, fading to an averaged sheen once a cell is smaller than a pixel.
  vec3 cellA = vGsPaintP / max(uPaintFlakeSize, 1e-4);
  vec3 cellB = cellA * 1.73 + 11.0;
  float fw = length(fwidth(cellA));
  float resolveA = smoothstep(1.4, 0.5, fw);
  float resolveB = smoothstep(1.4, 0.5, fw * 1.73);
  vec3 nWorld = inverseTransformDirection(normal, viewMatrix);
  gsFlakeCA = gsFlakeLayer(cellA, nWorld, gsFlakeNA) * resolveA;
  gsFlakeCB = gsFlakeLayer(cellB, nWorld, gsFlakeNB) * resolveB * 0.7;
  gsFlakeSheen = (2.0 - resolveA - resolveB) * 0.5;
}
`;

// After lights_physical_fragment: the clearcoat follows the live field values.
const FRAGMENT_COAT = /* glsl */ `
#ifdef USE_CLEARCOAT
  material.clearcoat = saturate(uPaintCoat);
  material.clearcoatRoughness = min(max(uPaintCoatRoughness, 0.0525) + geometryRoughness, 1.0);
#endif
`;

// Direct light: the physical model plus flake glints and the sub-pixel sheen.
const FRAGMENT_DIRECT = /* glsl */ `
void RE_Direct_CarPaint( const in IncidentLight directLight, const in vec3 geometryPosition, const in vec3 geometryNormal, const in vec3 geometryViewDir, const in vec3 geometryClearcoatNormal, const in PhysicalMaterial material, inout ReflectedLight reflectedLight ) {
  RE_Direct_Physical( directLight, geometryPosition, geometryNormal, geometryViewDir, geometryClearcoatNormal, material, reflectedLight );
  vec3 H = normalize( directLight.direction + geometryViewDir );
  float front = step( 0.0, dot( geometryNormal, directLight.direction ) );
  float glint = pow( saturate( dot( gsFlakeNA, H ) ), 900.0 ) * gsFlakeCA + pow( saturate( dot( gsFlakeNB, H ) ), 900.0 ) * gsFlakeCB;
  float sheen = pow( saturate( dot( geometryNormal, H ) ), 60.0 ) * 0.6 * gsFlakeSheen;
  reflectedLight.directSpecular += directLight.color * gsFlakeTint * ( glint * 30.0 + sheen ) * front;
}
#undef RE_Direct
#define RE_Direct RE_Direct_CarPaint
`;

// Environment glints: each flake mirrors the environment along its own normal.
const FRAGMENT_ENV = /* glsl */ `
#ifdef USE_ENVMAP
  reflectedLight.indirectSpecular += ( getIBLRadiance( geometryViewDir, gsFlakeNA, 0.08 ) * gsFlakeCA + getIBLRadiance( geometryViewDir, gsFlakeNB, 0.08 ) * gsFlakeCB ) * gsFlakeTint * 0.6;
#endif
`;

/** A MeshPhysicalMaterial with the Blender material's properties and patches (lightmap). */
function physicalCopy(source: MeshStandardMaterial): MeshPhysicalMaterial {
  if ((source as MeshPhysicalMaterial).isMeshPhysicalMaterial) return source as MeshPhysicalMaterial;
  const physical = new MeshPhysicalMaterial();
  MeshStandardMaterial.prototype.copy.call(physical, source);
  physical.defines = { ...source.defines, STANDARD: '', PHYSICAL: '' };
  physical.onBeforeCompile = source.onBeforeCompile;
  physical.customProgramCacheKey = source.customProgramCacheKey;
  return physical;
}

export const apply: ShaderApply<typeof fields> = (material, _values, ctx) => {
  if (!(material as MeshStandardMaterial).isMeshStandardMaterial) {
    throw new Error(`CarPaint needs a lit (Principled BSDF) material, got ${material.type}`);
  }
  const paint = physicalCopy(material as MeshStandardMaterial);
  paint.clearcoat = 1; // keeps USE_CLEARCOAT; the strength is the clearcoat field's uniform
  paint.transmission = 0;
  const u = ctx.uniforms;
  patchShader(paint, 'gs_car_paint', (shader) => {
    Object.assign(shader.uniforms, {
      uPaintFlip: u.flip,
      uPaintFlakes: u.flakes,
      uPaintFlakeSize: u.flakeSize,
      uPaintCoat: u.clearcoat,
      uPaintCoatRoughness: u.coatRoughness,
    });
    shader.vertexShader = shader.vertexShader
      .replace('#include <common>', `#include <common>\n${VERTEX_HEAD}`)
      .replace('#include <project_vertex>', `#include <project_vertex>\n${VERTEX_BODY}`);
    shader.fragmentShader = shader.fragmentShader
      .replace('#include <common>', `#include <common>\n${FRAGMENT_HEAD}`)
      .replace('#include <normal_fragment_maps>', `#include <normal_fragment_maps>\n${FRAGMENT_PAINT}`)
      .replace('#include <lights_physical_fragment>', `#include <lights_physical_fragment>\n${FRAGMENT_COAT}`)
      .replace('#include <lights_physical_pars_fragment>', `#include <lights_physical_pars_fragment>\n${FRAGMENT_DIRECT}`)
      .replace('#include <lights_fragment_end>', `#include <lights_fragment_end>\n${FRAGMENT_ENV}`);
  });
  return paint === material ? undefined : (paint as Material);
};
