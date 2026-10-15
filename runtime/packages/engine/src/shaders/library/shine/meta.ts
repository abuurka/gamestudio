// Shine: a band of light sweeps across the mesh every few seconds, the highlight of a polished button,
// a card or a pickup. Works on Game UI meshes and on 3D materials.

import { field } from '../../../schema/fields';
import { defineShader } from '../../../schema/shaders';

export const fields = {
  color: field.color([1, 1, 1, 0.6], {
    description: 'Colour of the highlight, linear RGBA 0–1 (tools also take #RRGGBB); its alpha is how strong it is',
  }),
  width: field.float(0.2, { min: 0.01, max: 1, description: 'Width of the band, as a share of the distance it crosses' }),
  speed: field.float(1.5, { min: 0.05, description: 'Crossings per second while it sweeps: 1.5 crosses in two thirds of a second' }),
  interval: field.float(3, {
    min: 0,
    unit: 'time',
    description: 'Seconds from the start of one sweep to the next; 0 sweeps back to back',
  }),
  angle: field.float(-Math.PI / 6, {
    unit: 'angle',
    description:
      'Direction the band travels, in radians; Blender shows degrees. As seen in Blender: 0 (0°) = left to right, ' +
      '1.5708 (90°) = bottom to top; the band lies across it. The default, -0.5236 (-30°), is a / band moving right',
  }),
};

export const Shine = defineShader('Shine', {
  description:
    'A diagonal band of light sweeps across the mesh every few seconds and is gone between sweeps: a polished button, ' +
    'a card or a reward. Adds to the material’s look (unlit, like emission), only where the material is opaque. ' +
    'Made for menu and HUD panels and buttons; works on 3D materials too. Menus start a sweep as they open. ' +
    'Cheap: no texture reads.',
  category: 'Style',
  builtin: true,
  ui: true,
  fields,
  preview: { mesh: 'box', camera: 'near', color: [0.3, 0.12, 0.04, 1], values: { width: 0.3, color: [1, 0.9, 0.6, 1] }, t: 0.35 },
  load: () => import('./impl'),
});
