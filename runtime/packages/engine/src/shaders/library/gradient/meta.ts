// Gradient: two colours blended along an angle across the mesh, in place of the material's colour.
// Works on Game UI meshes (panels, bars, buttons) and on 3D materials.

import { field } from '../../../schema/fields';
import { defineShader } from '../../../schema/shaders';

export const fields = {
  startColor: field.color([0.03, 0.08, 0.3, 1], {
    description:
      'Colour where the gradient starts (the bottom at the default angle), linear RGBA 0–1 (tools also take #RRGGBB); ' +
      'its alpha fades the material there',
  }),
  endColor: field.color([0.25, 0.75, 1, 1], {
    description: 'Colour where the gradient ends (the top at the default angle), linear RGBA 0–1 (tools also take #RRGGBB)',
  }),
  angle: field.float(Math.PI / 2, {
    unit: 'angle',
    description:
      'Direction from the start colour to the end colour, in radians; Blender shows degrees. As seen in Blender: ' +
      '0 (0°) = left to right, 1.5708 (90°, the default) = bottom to top, 3.1416 (180°) = right to left. ' +
      'A menu mesh is seen through its canvas camera, a 3D mesh from the front',
  }),
};

export const Gradient = defineShader('Gradient', {
  description:
    'Two colours blended along an angle across the mesh (corner to corner of its bounds), in place of the material’s ' +
    'colour: textures, vertex colours and alpha still multiply it. Made for menu and HUD panels, bars and buttons; ' +
    'works on 3D materials too. Cheap: no texture reads.',
  category: 'Style',
  builtin: true,
  ui: true,
  fields,
  preview: { mesh: 'box', camera: 'near', values: { startColor: [0.6, 0.05, 0.25, 1], endColor: [1, 0.75, 0.1, 1], angle: 1.2 } },
  load: () => import('./impl'),
});
