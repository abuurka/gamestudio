// Gem: a cut stone with brilliance and spectral fire (after FX Lab "gem-dispersion" and "crystal-growth").

import { field } from '../../../schema/fields';
import { defineShader } from '../../../schema/shaders';

export const fields = {
  ior: field.float(2.42, { min: 1.1, max: 3, description: 'Index of refraction: 2.42 diamond, 1.77 ruby and sapphire, 1.54 quartz' }),
  dispersion: field.float(0.5, { min: 0, max: 2, description: 'Fire: how far the colours split inside the stone' }),
  sparkle: field.float(1, {
    min: 0,
    max: 4,
    description: 'Brightness of the built-in jeweller’s lamps that make the stone sparkle in any scene',
  }),
  facets: field.int(8, { min: 3, max: 32, description: 'Main facets around the virtual cut (8 for a round brilliant): the pattern of the fire' }),
  cloudiness: field.float(0, { min: 0, max: 1, description: 'Milky inner flaws lit by the scene (0: flawless)' }),
  cutSurface: field.bool(false, {
    description: 'Facets the surface with the virtual cut too, so a round or smooth-shaded mesh looks like a cut brilliant',
  }),
};

export const Gem = defineShader('Gem', {
  description:
    'Cut gemstone: diamond-bright reflections and coloured fire from light bouncing inside a virtual brilliant cut, ' +
    'tinted by the hue of the material’s base colour (white: diamond). Shade the mesh flat in Blender for crisp facets, ' +
    'or turn on cutSurface for a round mesh. Draws ' +
    'over the material’s lighting; fog and emission stay. About ten environment reads per pixel: for small props.',
  category: 'Surfaces',
  builtin: true,
  fields,
  // A ruby on a flat-shaded brilliant (the gallery's gem mesh).
  preview: { mesh: 'gem', camera: 'near', color: [0.7, 0.02, 0.06, 1] },
  load: () => import('./impl'),
});
