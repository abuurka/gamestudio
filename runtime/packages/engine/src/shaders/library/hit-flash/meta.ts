// Hit Flash: a white flash and a coloured rim pulse when the object is hit (trigger: hitAt).

import { field } from '../../../schema/fields';
import { defineShader } from '../../../schema/shaders';

export const fields = {
  hitAt: field.float(-1000, {
    unit: 'time',
    description: 'Game time of the last hit, seconds. Set it from code: ctx.shaders.set(entity, { hitAt: ctx.time })',
  }),
  color: field.color([1, 0.451, 0.063, 1], { description: 'Colour the flash cools to, and of the rim' }),
  flash: field.float(1, { min: 0, max: 4, description: 'Brightness of the flash over the whole object (0: none)' }),
  rim: field.float(1, { min: 0, max: 4, description: 'Brightness of the rim pulse around the edges (0: none)' }),
  duration: field.float(0.5, { min: 0.02, max: 5, unit: 'time', description: 'How long the rim takes to fade; the flash is four times quicker' }),
};

export const HitFlash = defineShader('HitFlash', {
  description:
    'Hit feedback: at the game time in Hit At the object flashes white, cooling to the hit colour, and a rim of that ' +
    'colour pulses around its edges and fades. Game code triggers it with ctx.shaders.set(entity, { hitAt: ctx.time }). ' +
    'Keeps the material’s look; costs nothing between hits.',
  category: 'Gameplay',
  builtin: true,
  fields,
  preview: { mesh: 'character', camera: 'near', color: [0.12, 0.13, 0.16, 1], values: { hitAt: 1 }, t: 1.1 },
  load: () => import('./impl'),
});
