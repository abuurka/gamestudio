// PortalVortex: a swirling tunnel into another place, drawn as a disc. DOM-free and tiny: the fields, the
// gallery preview and a `load` of impl.ts, where all the GLSL is.

import { field } from '../../../schema/fields';
import { defineShader } from '../../../schema/shaders';

export const fields = {
  color: field.color([0.43, 0.14, 1, 1], { description: 'Colour of the swirl and rim (a saturated Blender base colour tints it)' }),
  destination: field.color([0.14, 0.63, 0.77, 1], { description: 'Colour of the sky seen through the middle of the portal' }),
  open: field.float(1, {
    min: 0,
    max: 1,
    description: 'How far it is open: 0 shut, 1 fully open. From code: ctx.shaders.set(gate, { open: t })',
  }),
  speed: field.float(1, { min: 0, description: 'Speed of the swirl' }),
  intensity: field.float(1, { min: 0, description: 'Brightness (above about 1 the rim blooms)' }),
};

export const PortalVortex = defineShader('PortalVortex', {
  description:
    'A magic portal: a swirling tunnel with parallax depth into another place, ringed by a hot flickering rim, ' +
    'drawn as a circle filling the mesh’s UV square (use a plane or circle). Replaces the material (mostly ' +
    'opaque, no shadow). About a dozen texture reads per pixel.',
  category: 'Magic & Sci-fi',
  builtin: true,
  fields,
  preview: { mesh: 'plane', camera: 'near', t: 2 },
  load: () => import('./impl'),
});
