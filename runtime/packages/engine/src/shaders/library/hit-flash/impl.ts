// After FX Lab "hit-reactions" (the body overlay's flash and rim): at the game time `hitAt` the
// material flashes white, cooling to the hit colour as it snaps off, and a fresnel rim in that colour
// lingers a little longer. Builds on the Blender material (emissive only).

import type { MeshStandardMaterial } from 'three';
import { patchShader, type ShaderApply } from '../../../schema/shaders';
import type { fields } from './meta';

const FRAGMENT_PARS = /* glsl */ `
uniform float hfHitAt;
uniform float hfTime;
uniform vec4 hfColor;
uniform float hfFlash;
uniform float hfRim;
uniform float hfDuration;`;

// After emissivemap_fragment. `normal` and `vViewPosition` are in view space here.
const EMISSIVE = /* glsl */ `
{
  float hfAge = (hfTime - hfHitAt) / max(hfDuration, 1e-3);
  if (hfAge >= 0.0 && hfAge < 4.0) {
    float hfF = exp(-10.0 * hfAge);
    float hfR = exp(-2.5 * hfAge);
    float hfFres = clamp(1.0 - abs(dot(normal, normalize(vViewPosition))), 0.0, 1.0);
    totalEmissiveRadiance += mix(hfColor.rgb, vec3(1.0), hfF) * hfF * 5.0 * hfFlash;
    totalEmissiveRadiance += hfColor.rgb * hfR * 3.2 * hfRim * pow(hfFres, 2.2);
  }
}`;

export const apply: ShaderApply<typeof fields> = (material, _values, ctx) => {
  if (!(material as MeshStandardMaterial).isMeshStandardMaterial) {
    throw new Error('HitFlash needs a Principled BSDF material (MeshStandardMaterial in the game)');
  }
  const u = ctx.uniforms;
  const uniforms = {
    hfHitAt: u.hitAt,
    hfTime: ctx.time,
    hfColor: u.color,
    hfFlash: u.flash,
    hfRim: u.rim,
    hfDuration: u.duration,
  };
  patchShader(material, 'gs-hit-flash', (shader) => {
    Object.assign(shader.uniforms, uniforms);
    shader.fragmentShader = shader.fragmentShader
      .replace('#include <common>', `#include <common>\n${FRAGMENT_PARS}`)
      .replace('#include <emissivemap_fragment>', `#include <emissivemap_fragment>\n${EMISSIVE}`);
  });
};
