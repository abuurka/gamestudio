// Gradient: the material's colour becomes a blend of two colours along an angle across the drawn
// meshes' bounds (gradient/across.ts), before textures and vertex colours multiply it, so a textured
// panel keeps its art and a 9-slice frame its corners. Patches MeshBasicMaterial (Game UI) and the lit
// materials alike: every three.js material starts its fragment shader from `diffuseColor`.

import { patchShader, type ShaderApply, type ShaderMesh } from '../../../schema/shaders';
import { acrossFragmentPars, acrossUniforms, acrossVertex, acrossVertexPars, createAcross, track, trackDraws, type Across } from './across';
import type { fields } from './meta';

type F = typeof fields;

const P = 'gsGr';

const FRAGMENT_PARS = /* glsl */ `
uniform vec4 gsGrStart;
uniform vec4 gsGrEnd;
uniform float gsGrAngle;
${acrossFragmentPars(P)}`;

// Before map_fragment: `diffuseColor` is (material colour, opacity) here.
const COLOR = /* glsl */ `
{
  vec4 gsGrC = mix(gsGrStart, gsGrEnd, clamp(gsGrAcross(gsGrAngle), 0.0, 1.0));
  diffuseColor = vec4(gsGrC.rgb, diffuseColor.a * gsGrC.a);
}`;

const bounds = new WeakMap<object, Across>();

export const apply: ShaderApply<F> = (material, _values, ctx) => {
  const u = ctx.uniforms;
  const across = createAcross(ctx.ui);
  bounds.set(material, across);
  const uniforms = { gsGrStart: u.startColor, gsGrEnd: u.endColor, gsGrAngle: u.angle, ...acrossUniforms(across, P) };
  trackDraws(material, across);
  patchShader(material, 'gs-gradient', (shader) => {
    Object.assign(shader.uniforms, uniforms);
    shader.vertexShader = shader.vertexShader
      .replace('#include <common>', `#include <common>\n${acrossVertexPars(P)}`)
      .replace('#include <begin_vertex>', `#include <begin_vertex>\n${acrossVertex(P)}`);
    shader.fragmentShader = shader.fragmentShader
      .replace('#include <common>', `#include <common>\n${FRAGMENT_PARS}`)
      .replace('#include <map_fragment>', `${COLOR}\n#include <map_fragment>`);
  });
};

export const mesh: ShaderMesh<F> = (target) => {
  const materials = Array.isArray(target.material) ? target.material : [target.material];
  for (const material of materials) {
    const across = bounds.get(material);
    if (across) track(across, target.geometry);
  }
};
