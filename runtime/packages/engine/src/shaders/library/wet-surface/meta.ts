// WetSurface: rain-soaked ground and walls. The fields and the gallery preview; the GLSL is in impl.ts.

import { field } from '../../../schema/fields';
import { defineShader } from '../../../schema/shaders';

export const fields = {
  wetness: field.float(1, { min: 0, max: 1, description: 'How soaked the surface is: darker and glossier (0 = dry; animate it as rain starts and stops)' }),
  puddles: field.float(0.4, { min: 0, max: 1, description: 'How much of the upward-facing ground is covered by puddles' }),
  puddleSize: field.float(3, { min: 0.05, unit: 'distance', description: 'Typical size of a puddle in metres' }),
  rain: field.float(1, { min: 0, max: 2, description: 'Raindrop rings on the puddles and wet ground, and drips running down walls (0 = no rain)' }),
};

export const WetSurface = defineShader('WetSurface', {
  description:
    "Rain on the material: it darkens and turns glossy, puddles gather on upward-facing ground and mirror the sky, " +
    'raindrops ring the water and drips run down walls. Keeps the material’s own colour, textures and normal map. ' +
    'Cheap: value noise and three rings per pixel.',
  category: 'Water',
  builtin: true,
  fields,
  preview: { mesh: 'plane', camera: 'near', t: 1.3, values: { puddles: 0.35, puddleSize: 1.5 } },
  load: () => import('./impl'),
});
