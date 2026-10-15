// FlagWave: flags and banners rippling in the wind, pinned at one edge. DOM-free and tiny: the fields,
// the gallery preview and a `load` of impl.ts, where all the GLSL is.

import { field } from '../../../schema/fields';
import { defineShader } from '../../../schema/shaders';

export const fields = {
  pinnedEdge: field.enum(['left', 'right', 'top', 'bottom'], 'left', {
    description: 'The edge held by the pole or bar, as in the UV editor (left = U 0, top = V 1). It stays still; the far edge moves most',
  }),
  wind: field.float(0.6, { min: 0, max: 1.5, description: 'Wind strength: 0 a slow ripple, 1 a gale' }),
  windAngle: field.float(0.35, {
    unit: 'angle',
    description: 'Direction the wind blows towards, in radians (Blender shows degrees), seen from above: 0 = +X, counter-clockwise. Gusts reach flags, trees and grass together when they match',
  }),
  speed: field.float(1, { min: 0, description: 'Speed of the ripples' }),
  amplitude: field.float(0.15, { min: 0, unit: 'distance', description: 'Ripple height at the free edge' }),
  waveLength: field.float(1.2, { min: 0.05, unit: 'distance', description: 'Distance between ripples' }),
  translucency: field.float(0.5, { min: 0, max: 2, description: 'Sun glowing through the cloth from behind' }),
};

export const FlagWave = defineShader('FlagWave', {
  description:
    'Flags, banners and cloth ripple in the wind with travelling folds that grow towards the free edge and ' +
    'swell in gusts; lit folds and moving shadows. The UV map says which edge is pinned (pinnedEdge). ' +
    'Needs a subdivided plane (about 20 cuts along the flag).',
  category: 'Nature',
  builtin: true,
  fields,
  preview: { mesh: 'flag', t: 1.5, camera: 'near', values: { wind: 0.8 } },
  load: () => import('./impl'),
});
