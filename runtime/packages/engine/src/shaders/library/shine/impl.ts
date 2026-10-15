// Shine: a soft band of light crosses the drawn meshes' bounds along `angle` (../gradient/across.ts),
// once every `interval` seconds of `ctx.time` (the game's; a canvas's since it opened on Game UI
// meshes), starting at 0. It adds to the final colour before the alpha is written, so it is unlit like
// emission and shows only where the material is opaque. Patches MeshBasicMaterial (Game UI) and the lit
// materials alike.

import { patchShader, type ShaderApply, type ShaderMesh } from '../../../schema/shaders';
import { acrossFragmentPars, acrossUniforms, acrossVertex, acrossVertexPars, createAcross, track, trackDraws, type Across } from '../gradient/across';
import type { fields } from './meta';

type F = typeof fields;

const P = 'gsSh';

const FRAGMENT_PARS = /* glsl */ `
uniform vec4 gsShColor;
uniform float gsShWidth;
uniform float gsShSpeed;
uniform float gsShInterval;
uniform float gsShAngle;
uniform float gsShTime;
${acrossFragmentPars(P)}`;

// Before opaque_fragment: `outgoingLight` is the final (lit) colour, `diffuseColor.a` its alpha.
const SHINE = /* glsl */ `
{
  float gsShSweep = 1.0 / max(gsShSpeed, 1e-3);
  float gsShP = mod(max(gsShTime, 0.0), max(gsShInterval, gsShSweep)) / gsShSweep;
  if (gsShP <= 1.0) {
    float gsShW = max(gsShWidth, 1e-3) * 0.5;
    float gsShX = abs(gsShAcross(gsShAngle) - mix(-gsShW, 1.0 + gsShW, gsShP)) / gsShW;
    outgoingLight += gsShColor.rgb * gsShColor.a * (1.0 - smoothstep(0.0, 1.0, gsShX));
  }
}`;

const bounds = new WeakMap<object, Across>();

export const apply: ShaderApply<F> = (material, _values, ctx) => {
  const u = ctx.uniforms;
  const across = createAcross(ctx.ui);
  bounds.set(material, across);
  const uniforms = {
    gsShColor: u.color,
    gsShWidth: u.width,
    gsShSpeed: u.speed,
    gsShInterval: u.interval,
    gsShAngle: u.angle,
    gsShTime: ctx.time,
    ...acrossUniforms(across, P),
  };
  trackDraws(material, across);
  patchShader(material, 'gs-shine', (shader) => {
    Object.assign(shader.uniforms, uniforms);
    shader.vertexShader = shader.vertexShader
      .replace('#include <common>', `#include <common>\n${acrossVertexPars(P)}`)
      .replace('#include <begin_vertex>', `#include <begin_vertex>\n${acrossVertex(P)}`);
    shader.fragmentShader = shader.fragmentShader
      .replace('#include <common>', `#include <common>\n${FRAGMENT_PARS}`)
      .replace('#include <opaque_fragment>', `${SHINE}\n#include <opaque_fragment>`);
  });
};

export const mesh: ShaderMesh<F> = (target) => {
  const materials = Array.isArray(target.material) ? target.material : [target.material];
  for (const material of materials) {
    const across = bounds.get(material);
    if (across) track(across, target.geometry);
  }
};
