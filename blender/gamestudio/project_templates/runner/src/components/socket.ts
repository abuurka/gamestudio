import { defineComponent, field } from '@gamestudio/engine/schema';

export const Socket = defineComponent('Socket', {
  description: 'An empty inside a chunk prefab: when the chunk appears, SocketSystem spawns an obstacle, coins or a pickup here.',
  category: 'Runner',
  fields: {
    kind: field.enum(['obstacle', 'coins', 'pickup'], 'obstacle', { description: "What goes here (the Track's list for that kind)" }),
    chance: field.float(1, { min: 0, max: 1, description: 'Probability that anything spawns here' }),
    anyLane: field.bool(false, { description: "A random lane instead of the socket's own" }),
    options: field.string('', { description: 'Prefabs to pick from, "Name:weight, ..." (empty: the Track\'s list for the kind)' }),
  },
});
