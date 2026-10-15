// Iridescence: a swirling thin film of oil or soap (after FX Lab "oil-slick" and "soap-bubbles").

import { field } from '../../../schema/fields';
import { defineShader } from '../../../schema/shaders';

export const fields = {
  strength: field.float(2, {
    min: 0,
    max: 8,
    description: 'How strongly the film colours reflections (1: physical; more is an artistic boost)',
  }),
  thickness: field.vec2([150, 900], {
    min: 0,
    max: 3000,
    description: 'Thinnest and thickest film, nanometres: thin films reflect gold and blue, thicker ones pink and green',
  }),
  scale: field.float(1.5, { min: 0.01, description: 'Swirls per metre (object space)' }),
  speed: field.float(0.15, { min: 0, description: 'How fast the swirls drift' }),
  drain: field.float(0, { min: 0, max: 1, description: 'Soap-bubble drainage: thin at the top, thick at the bottom' }),
  coverage: field.float(1, { min: 0, max: 1, description: 'How much of the surface has a film (below 1: oil-slick patches)' }),
  gloss: field.float(0.6, { min: 0, max: 1, description: "Smoothness of the film's reflection: 0 keeps the material's roughness, 1 is a mirror" }),
};

export const Iridescence = defineShader('Iridescence', {
  description:
    'Rainbow thin-film colours (oil slick, soap bubble, beetle shell, tempered metal) in the material’s reflections, ' +
    'swirling slowly over the material’s colour; the film reflects more sharply than the surface under it (gloss). ' +
    'Shows best with an environment to reflect. ' +
    'On a transparent (Alpha Blend) material the film’s reflection keeps its strength, as on a bubble.',
  category: 'Surfaces',
  builtin: true,
  fields,
  // A dark base, like oil on asphalt: the film's colours show best.
  preview: { mesh: 'sphere', camera: 'near', t: 1, color: [0.04, 0.04, 0.05, 1] },
  load: () => import('./impl'),
});
