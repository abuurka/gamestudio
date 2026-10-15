// InteriorMapping: fake rooms behind the windows of a facade (after FX Lab "interior-mapping").

import { field } from '../../../schema/fields';
import { defineShader } from '../../../schema/shaders';

const IMAGES = ['.webp', '.png', '.jpg', '.jpeg'];

export const fields = {
  spacing: field.vec2([3, 3], {
    min: 0.05,
    description: "Window grid in the material's UV (v up the wall, as in Blender): distance between windows and storey height. UV units are metres on GameStudio kits",
  }),
  windowSize: field.vec2([1.4, 1.6], { min: 0.01, description: 'Width and height of a window, UV units' }),
  sill: field.float(0.8, { min: 0, unit: 'distance', description: 'Height of the window bottoms above each storey, UV units' }),
  offset: field.vec2([0, 0], {
    description: 'Moves the grid, UV units: half a spacing centres an odd number of windows on a wall',
  }),
  floors: field.int(0, { min: 0, max: 500, description: 'Storeys with windows, from the bottom (0: all). Keeps gables and parapets plain' }),
  groundFloor: field.enum(['windows', 'shopfront', 'wall'] as const, 'windows', {
    description: 'Bottom storey: windows like the others, one wide lit shop window per cell, or plain wall',
  }),
  roomDepth: field.float(3, { min: 0.1, unit: 'distance', description: 'How deep the rooms behind the windows are, UV units' }),
  lit: field.float(0.6, { min: 0, max: 1, description: 'Share of rooms with their lights on (they switch now and then)' }),
  brightness: field.float(1, { min: 0, max: 20, description: 'Brightness of the rooms: raise it for night scenes' }),
  accent: field.color([0.45, 0.6, 1, 1], { description: 'Colour of some lamps and of the TV glow' }),
  mask: field.asset(IMAGES, {
    description: "Optional image, white where windows are, in the material's UV (rooms still follow the grid). Empty: windows from the grid",
  }),
};

export const InteriorMapping = defineShader('InteriorMapping', {
  description:
    'Windows with fake 3D rooms behind them on a building facade, from a window grid in the material’s UV (or a ' +
    'mask): lamps, curtains, TVs and furniture in parallax, lights switching over time; the wall keeps the material. ' +
    'Windows only on outside walls (vertical faces facing away from the object origin). On a building kit: set its ' +
    'windows to 0, the spacing’s height to its floor height and floors to its storeys (keeps the gable plain). ' +
    'One ray-box hit per pixel, no texture reads (one with a mask).',
  category: 'Surfaces',
  builtin: true,
  fields,
  // The building kit's shell with no windows: two 3 m storeys, metre UVs.
  preview: { mesh: 'building', camera: 'near', t: 2, values: { spacing: [3, 3], floors: 2 } },
  load: () => import('./impl'),
});
