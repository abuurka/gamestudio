// MossOvergrowth: moss creeping over a Blender material. DOM-free and tiny: the fields, the gallery
// preview and a `load` of impl.ts, where all the GLSL is.

import { field } from '../../../schema/fields';
import { defineShader } from '../../../schema/shaders';

export const fields = {
  progress: field.float(0.6, { min: 0, max: 1, description: '0 bare, 1 overgrown: animate it from code to watch the moss creep in' }),
  color: field.color([0.13, 0.27, 0.035, 1], { description: 'Moss colour' }),
  scale: field.float(1, { min: 0.05, unit: 'distance', description: 'Size of the moss cushions and patches' }),
  height: field.float(3, {
    min: 0.1,
    unit: 'distance',
    description: "Moss climbs from the base: parts this far above the object's origin are covered last",
  }),
};

export const MossOvergrowth = defineShader('MossOvergrowth', {
  description:
    'Moss creeps over the surface as progress rises: upward faces and low parts first, into cracks before it ' +
    'covers the faces, with damp darkening ahead of the front, bright young growth at it, soft fuzz and ' +
    'cushion bumps. Keeps the material underneath. Five 3D noise lookups per pixel.',
  category: 'Nature',
  builtin: true,
  fields,
  preview: { mesh: 'cylinder', camera: 'near' },
  load: () => import('./impl'),
});
