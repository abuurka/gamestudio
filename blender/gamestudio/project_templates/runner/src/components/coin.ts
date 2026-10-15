import { defineComponent, field } from '@gamestudio/engine/schema';

export const Coin = defineComponent('Coin', {
  description: 'Collected when the runner touches it. SocketSystem raises the value further into the run.',
  category: 'Runner',
  fields: {
    value: field.int(1, { min: 0, description: 'Coins it is worth' }),
  },
});
