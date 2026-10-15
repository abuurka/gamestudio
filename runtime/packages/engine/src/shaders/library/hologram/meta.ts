// Hologram: scanlines, fresnel glow, flicker and glitch slices, with a materialise sweep. DOM-free and
// tiny: the fields, the gallery preview and a `load` of impl.ts, where all the GLSL is.

import { field } from '../../../schema/fields';
import { defineShader } from '../../../schema/shaders';

export const fields = {
  color: field.color([0.08, 0.69, 1, 1], { description: 'Colour of the projection (a saturated Blender base colour tints it)' }),
  intensity: field.float(1, { min: 0, description: 'Brightness (above about 1 the edges bloom)' }),
  scanlines: field.float(60, { min: 1, description: 'Scanlines per metre of height' }),
  flicker: field.float(0.5, { min: 0, max: 1, description: 'How much the brightness flickers' }),
  glitch: field.float(0.6, { min: 0, max: 1, description: 'How often random glitches happen (slices drop out and jump sideways); 0: never' }),
  glitchAt: field.float(-10, {
    unit: 'time',
    description: 'Game time of a forced glitch burst (about 0.7 s). From code: ctx.shaders.set(holo, { glitchAt: ctx.time })',
  }),
  reveal: field.float(1, {
    min: 0,
    max: 1,
    description: 'Materialise sweep: 0 nothing, 1 all shown; in between a bright line rises up the object. From code: ctx.shaders.set(holo, { reveal: t })',
  }),
};

export const Hologram = defineShader('Hologram', {
  description:
    'A holographic projection: world-locked scanlines with a colour fringe, a glowing fresnel edge, rolling bands, ' +
    'sparkle, flicker and glitches where slices drop out and jump sideways, plus a materialise sweep. Replaces the ' +
    'material (additive light, no shadow). Cheap: no texture reads.',
  category: 'Magic & Sci-fi',
  builtin: true,
  fields,
  preview: { mesh: 'character', camera: 'near', t: 1 },
  load: () => import('./impl'),
});
