// Ice: a glassy glaze freezing over a Blender material. DOM-free and tiny: the fields, the gallery
// preview and a `load` of impl.ts, where all the GLSL is.

import { field } from '../../../schema/fields';
import { defineShader } from '../../../schema/shaders';

export const fields = {
  freeze: field.float(0.8, { min: 0, max: 1, description: '0 none, 1 fully glazed: the ice builds in patches, undersides first' }),
  color: field.color([0.45, 0.7, 1, 1], { description: 'Tint of the ice and the cold glow at its edges' }),
  frost: field.float(0.5, { min: 0, max: 1, description: 'White frost on the rims and in rough patches' }),
  cracks: field.float(0.4, { min: 0, max: 1, description: 'Crack sheets inside the ice (they shift with the view)' }),
  glints: field.float(0.6, { min: 0, max: 2, description: 'Frozen beads that catch the sun' }),
  scale: field.float(1, { min: 0.05, unit: 'distance', description: 'Size of the frost patches, cracks and ripples' }),
};

export const Ice = defineShader('Ice', {
  description:
    'Freezes the surface under a glassy glaze: darker and colder underneath, mirror-smooth with lumpy ripples, ' +
    'cracks seen inside, frosted rims, a cold glow at thin edges and beads that glint in the sun. Keeps the ' +
    'material underneath; animate freeze from code to freeze or thaw. Five noise lookups per pixel.',
  category: 'Nature',
  builtin: true,
  fields,
  preview: { mesh: 'character', camera: 'near' },
  load: () => import('./impl'),
});
