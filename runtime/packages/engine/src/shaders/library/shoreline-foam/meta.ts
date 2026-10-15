// ShorelineFoam: surf on the foam rings around rocks and along beaches. The fields and the gallery
// preview; the GLSL is in impl.ts.

import { field } from '../../../schema/fields';
import { defineShader } from '../../../schema/shaders';

export const fields = {
  speed: field.float(1, { min: 0, description: 'How fast the foam rolls out to sea' }),
  wash: field.float(0.34, { min: 0, max: 1, description: 'How far the white water against the shore reaches out, as a share of the ring (V)' }),
  waves: field.float(1.4, { min: 0, description: 'Foam fronts rolling out across the ring at once' }),
  repeat: field.float(1, { min: 0.01, description: 'Repeats the lace along the shore (U); raise it for long rings and beaches' }),
  opacity: field.float(0.92, { min: 0, max: 1, description: 'Opacity of the thickest foam' }),
};

export const ShorelineFoam = defineShader('ShorelineFoam', {
  description:
    'Animated surf for foam rings around rocks and along beaches: white water clinging to the shore and foam fronts ' +
    'rolling out to sea, breaking up into lace. Unwrap the ring so V runs from 0 at the shore to 1 out at sea. The ' +
    "material's colour gives the foam its hue; it is see-through and draws over Water. Cheap: value noise only.",
  category: 'Water',
  builtin: true,
  fields,
  preview: { mesh: 'plane', camera: 'near', t: 2, values: { repeat: 2.5, waves: 2 } },
  load: () => import('./impl'),
});
