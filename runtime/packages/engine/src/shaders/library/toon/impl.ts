// After FX Lab "smoke-dome" (toon bands, stepped rim, ink hull) and the Toon FX pack.
// Cel shading on the Blender material: every light's diffuse term is stepped into flat bands, its
// shadow edge made hard, a hard-edged highlight replaces the smooth one, and a stepped Fresnel rim
// is added. Base colour, textures, ambient light, fog and lightmaps are the material's own.
// Optional ink outline: an inverted hull, a back-facing copy of each mesh pushed out along its
// smoothed normals, drawn in the outline colour.

import { BackSide, BufferAttribute, DoubleSide, InstancedMesh, Mesh, MeshBasicMaterial, ShaderChunk, SkinnedMesh } from 'three';
import type { BufferGeometry, Material, MeshStandardMaterial } from 'three';
import { patchShader, type ShaderApply, type ShaderContext, type ShaderMesh } from '../../../schema/shaders';
import type { fields } from './meta';

const FRAGMENT_HEAD = /* glsl */ `
uniform float uToonBands;
uniform float uToonSpecular;
uniform float uToonRim;
uniform vec4 uToonRimColor;
uniform float uToonRimWidth;
`;

// Stepped light: bands over N.L from -0.5 (core shadow) to 1 (lit cap), antialiased edges.
const FRAGMENT_DIRECT = /* glsl */ `
float gsToonBand( float NdL ) {
  float bands = max( uToonBands, 2.0 );
  float x = saturate( ( NdL + 0.5 ) / 1.5 ) * bands;
  float aa = fwidth( x ) * 0.75 + 1e-4;
  float k = floor( x );
  float q = k + smoothstep( 1.0 - aa, 1.0, x - k );
  return clamp( q, 0.0, bands - 1.0 ) / ( bands - 1.0 );
}
// Shadow maps give soft (filtered) edges: cut them at half.
float gsToonShadow( float s ) { return smoothstep( 0.35, 0.65, s ); }
void RE_Direct_Toon( const in IncidentLight directLight, const in vec3 geometryPosition, const in vec3 geometryNormal, const in vec3 geometryViewDir, const in vec3 geometryClearcoatNormal, const in PhysicalMaterial material, inout ReflectedLight reflectedLight ) {
  float NdL = dot( geometryNormal, directLight.direction );
  vec3 irradiance = gsToonBand( NdL ) * directLight.color;
  reflectedLight.directDiffuse += irradiance * BRDF_Lambert( material.diffuseContribution );
  // Hard highlight: its size follows the material's roughness.
  vec3 H = normalize( directLight.direction + geometryViewDir );
  float gloss = 1.0 - material.roughness;
  float s = pow( saturate( dot( geometryNormal, H ) ), exp2( 10.0 * gloss + 1.0 ) );
  float aa = fwidth( s ) + 1e-3;
  float spec = smoothstep( 0.5 - aa, 0.5 + aa, s ) * gloss * gloss * uToonSpecular * step( 0.0, NdL );
  reflectedLight.directSpecular += directLight.color * spec;
}
#undef RE_Direct
#define RE_Direct RE_Direct_Toon
`;

// Stepped Fresnel rim (emissive, so it shows on the shadow side too, as in the FX look).
const FRAGMENT_RIM = /* glsl */ `
{
  vec3 gsV = isOrthographic ? vec3(0.0, 0.0, 1.0) : normalize(vViewPosition);
  float fres = 1.0 - saturate(dot(normal, gsV));
  float edge = 1.0 - saturate(uToonRimWidth);
  float aa = fwidth(fres) + 1e-3;
  totalEmissiveRadiance += uToonRimColor.rgb * smoothstep(edge - aa, edge + aa, fres) * uToonRim;
}
`;

// Smooth environment reflections fight the flat look: dielectrics keep a third of them, metals all.
const FRAGMENT_GLOSS = /* glsl */ `
reflectedLight.indirectSpecular *= mix(0.3, 1.0, metalnessFactor);
`;

const SHADOW_CALL = /(\?\s*)(get\w*Shadow\([^;]*\))(\s*:\s*1\.0\s*;)/g;

/** `lights_fragment_begin` with every shadow lookup cut to a hard edge. */
function hardShadowChunk(): string {
  return ShaderChunk.lights_fragment_begin.replace(SHADOW_CALL, '$1gsToonShadow( $2 )$3');
}

export const apply: ShaderApply<typeof fields> = (material, _values, ctx) => {
  if (!(material as MeshStandardMaterial).isMeshStandardMaterial) {
    throw new Error(`Toon needs a lit (Principled BSDF) material, got ${material.type}`);
  }
  const u = ctx.uniforms;
  patchShader(material, 'gs_toon', (shader) => {
    Object.assign(shader.uniforms, {
      uToonBands: u.bands,
      uToonSpecular: u.specular,
      uToonRim: u.rim,
      uToonRimColor: u.rimColor,
      uToonRimWidth: u.rimWidth,
    });
    shader.fragmentShader = shader.fragmentShader
      .replace('#include <common>', `#include <common>\n${FRAGMENT_HEAD}`)
      .replace('#include <lights_physical_pars_fragment>', `#include <lights_physical_pars_fragment>\n${FRAGMENT_DIRECT}`)
      .replace('#include <emissivemap_fragment>', `#include <emissivemap_fragment>\n${FRAGMENT_RIM}`)
      .replace('#include <lights_fragment_begin>', hardShadowChunk)
      .replace('#include <lights_fragment_end>', `#include <lights_fragment_end>\n${FRAGMENT_GLOSS}`);
  });
};

// ---------------------------------------------------------------------------
// Ink outline (inverted hull)

const OUTLINE_TAG = 'gs_toon_outline';

/** Normals averaged over vertices that share a position, so the hull does not split at hard edges. */
const smoothNormals = new WeakMap<BufferGeometry, BufferAttribute>();

function smoothNormalAttribute(geometry: BufferGeometry): BufferAttribute {
  let attribute = smoothNormals.get(geometry);
  if (attribute) return attribute;
  const position = geometry.getAttribute('position');
  const normal = geometry.getAttribute('normal');
  const out = new Float32Array(position.count * 3);
  const sums = new Map<string, [number, number, number]>();
  const key = (i: number): string =>
    `${Math.round(position.getX(i) * 1e4)},${Math.round(position.getY(i) * 1e4)},${Math.round(position.getZ(i) * 1e4)}`;
  if (normal) {
    for (let i = 0; i < position.count; i++) {
      const k = key(i);
      const sum = sums.get(k) ?? [0, 0, 0];
      sum[0] += normal.getX(i);
      sum[1] += normal.getY(i);
      sum[2] += normal.getZ(i);
      sums.set(k, sum);
    }
  }
  for (let i = 0; i < position.count; i++) {
    const [x, y, z] = sums.get(key(i)) ?? [0, 1, 0];
    const length = Math.hypot(x, y, z) || 1;
    out.set([x / length, y / length, z / length], i * 3);
  }
  attribute = new BufferAttribute(out, 3);
  geometry.setAttribute('gsOutlineNormal', attribute);
  smoothNormals.set(geometry, attribute);
  return attribute;
}

const OUTLINE_VERTEX_HEAD = /* glsl */ `
attribute vec3 gsOutlineNormal;
uniform float uToonOutline;
`;

// Push out along the smoothed normal by a width in metres (object scale divided out).
const OUTLINE_VERTEX_PUSH = /* glsl */ `
{
  float gsScale = length(modelMatrix[0].xyz);
#ifdef USE_INSTANCING
  gsScale *= length(instanceMatrix[0].xyz);
#endif
  transformed += normalize(gsOutlineNormal) * uToonOutline / max(gsScale, 1e-6);
}
`;

const OUTLINE_FRAGMENT_HEAD = /* glsl */ `
uniform vec4 uToonOutlineColor;
`;

/** One hull material per Toon material: back faces only, the outline colour, fog. */
const hullMaterials = new WeakMap<Material, MeshBasicMaterial>();

function hullMaterial(toon: Material, ctx: ShaderContext<typeof fields>): MeshBasicMaterial {
  let hull = hullMaterials.get(toon);
  if (hull) return hull;
  hull = new MeshBasicMaterial({ side: BackSide, fog: true });
  hull.name = `${toon.name}_outline`;
  const u = ctx.uniforms;
  patchShader(hull, 'gs_toon_outline', (shader) => {
    Object.assign(shader.uniforms, { uToonOutline: u.outline, uToonOutlineColor: u.outlineColor });
    shader.vertexShader = shader.vertexShader
      .replace('#include <common>', `#include <common>\n${OUTLINE_VERTEX_HEAD}`)
      .replace('#include <begin_vertex>', `#include <begin_vertex>\n${OUTLINE_VERTEX_PUSH}`);
    shader.fragmentShader = shader.fragmentShader
      .replace('#include <common>', `#include <common>\n${OUTLINE_FRAGMENT_HEAD}`)
      .replace('#include <color_fragment>', '#include <color_fragment>\n\tdiffuseColor.rgb = uToonOutlineColor.rgb;');
  });
  hullMaterials.set(toon, hull);
  return hull;
}

const noRaycast = (): void => {};

export const mesh: ShaderMesh<typeof fields> = (target, values, ctx) => {
  // A redefinition (hot reload) applies again: replace the hull made before.
  for (const child of [...target.children]) if (child.userData[OUTLINE_TAG]) target.remove(child);
  if (!(Number(values.outline) > 0)) return;
  const toon = Array.isArray(target.material) ? undefined : target.material;
  // A hull needs a closed surface: double-sided cards (leaves, grass) get no outline.
  if (!toon || toon.side === DoubleSide || !target.geometry?.getAttribute('position')) return;
  smoothNormalAttribute(target.geometry);
  const material = hullMaterial(toon, ctx);
  let hull: Mesh;
  if ((target as SkinnedMesh).isSkinnedMesh) {
    const skinned = target as SkinnedMesh;
    const copy = new SkinnedMesh(skinned.geometry, material);
    copy.bindMode = skinned.bindMode;
    copy.bind(skinned.skeleton, skinned.bindMatrix);
    hull = copy;
  } else if ((target as InstancedMesh).isInstancedMesh) {
    const instanced = target as InstancedMesh;
    const copy = new InstancedMesh(instanced.geometry, material, instanced.count);
    copy.instanceMatrix = instanced.instanceMatrix;
    copy.onBeforeRender = () => {
      copy.count = instanced.count;
    };
    hull = copy;
  } else {
    hull = new Mesh(target.geometry, material);
  }
  hull.name = `${target.name}_outline`;
  hull.userData[OUTLINE_TAG] = true;
  hull.userData.gsShaderPass = { shader: 'Toon', kind: 'outline' }; // physics and navmeshes skip it
  hull.castShadow = false;
  hull.receiveShadow = false;
  hull.frustumCulled = target.frustumCulled;
  hull.renderOrder = target.renderOrder;
  hull.morphTargetInfluences = target.morphTargetInfluences;
  hull.morphTargetDictionary = target.morphTargetDictionary;
  hull.raycast = noRaycast;
  target.add(hull);
};
