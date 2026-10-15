// Triplanar: the library's reference shader (copy this shape). DOM-free and tiny: the fields, the
// gallery preview and a `load` of impl.ts, where all the GLSL is.

import { field } from '../../../schema/fields';
import { defineShader } from '../../../schema/shaders';

const IMAGES = ['.webp', '.png', '.jpg', '.jpeg'];

export const fields = {
  diffuse: field.asset(IMAGES, { colorSpace: 'srgb', description: 'Colour scan (albedo). Empty: the material keeps its own colour' }),
  normal: field.asset(IMAGES, { description: 'Normal map scan (OpenGL, green up). Empty: the material keeps its own normals' }),
  rough: field.asset(IMAGES, { description: 'Roughness scan (red channel). Empty: the material keeps its own roughness' }),
  tile: field.float(3.2, { min: 0.01, unit: 'distance', description: 'Metres per texture repeat' }),
  normalStrength: field.float(1, { min: 0, max: 4, description: 'How strong the bumps of the normal scan are' }),
  roughMin: field.float(0.6, { min: 0, max: 1, description: 'Roughness where the roughness scan is black' }),
  roughMax: field.float(1, { min: 0, max: 1, description: 'Roughness where the roughness scan is white' }),
  photo: field.float(0.35, { min: 0, max: 1, description: "0: the material's colour with the scan as detail; 1: the scan's own colour" }),
  variation: field.float(0.45, { min: 0, max: 1, description: 'Large light and dark patches that hide the tiling' }),
  wetness: field.float(0, { min: 0, max: 1, description: 'Darker, glossier surface near the water line' }),
  waterLevel: field.float(0, { unit: 'distance', description: 'World height of the water line: fully wet below it' }),
  wetBand: field.float(1.8, { min: 0.01, unit: 'distance', description: 'How far above the water line the wetness fades out' }),
};

export const Triplanar = defineShader('Triplanar', {
  description:
    'Photo scans projected in world space, so surfaces need no UVs and textures never stretch (terrain, cliffs, ' +
    'walls), with wetness near a water line. Keeps the material’s colour as a tint. About a dozen texture reads per pixel.',
  category: 'Surfaces',
  builtin: true,
  fields,
  preview: {
    mesh: 'sphere',
    camera: 'near',
    values: {
      diffuse: 'samples/stone_diff.webp',
      normal: 'samples/stone_nor.webp',
      rough: 'samples/stone_rough.webp',
      tile: 1.2,
      photo: 0.8,
      wetness: 0.8,
      waterLevel: 0.5,
      wetBand: 0.6,
    },
  },
  load: () => import('./impl'),
});
