// Entity state snapshots for `state.request` → `state.response` (docs/SPEC.md §5.4).

import { Quaternion, Vector3 } from 'three';
import { animationSnapshot, type AnimationSnapshot } from '../animation/animation';
import type { World } from '../ecs/world';

export interface StateFilter {
  ids?: string[];
  component?: string;
  limit?: number;
}

export interface EntityState {
  id: string;
  name: string;
  /** World position, Y-up. */
  position: [number, number, number];
  /** World rotation [x, y, z, w]. */
  rotation: [number, number, number, number];
  visible: boolean;
  components: Record<string, unknown>;
  /** Animated entities: the graph, its state and parameters, or the clip playing (API.md §15). */
  animation?: AnimationSnapshot;
}

const p = new Vector3();
const q = new Quaternion();
const s = new Vector3();

const round = (n: number): number => Math.round(n * 1e6) / 1e6;

export function describeEntities(world: World, filter: StateFilter = {}): EntityState[] {
  let entities = filter.ids
    ? filter.ids.map((id) => world.get(id)).filter((e) => e !== undefined)
    : filter.component
      ? [...world.query(filter.component)]
      : [...world.all()];
  if (filter.ids && filter.component) entities = entities.filter((e) => e.has(filter.component!));
  const limit = typeof filter.limit === 'number' && filter.limit >= 0 ? filter.limit : 1000;

  return entities.slice(0, limit).map((e) => {
    e.object3d.updateWorldMatrix(true, false);
    e.object3d.matrixWorld.decompose(p, q, s);
    const components: Record<string, unknown> = {};
    for (const name of e.components()) components[name] = JSON.parse(JSON.stringify(e.get(name)));
    const animation = animationSnapshot(e);
    return {
      id: e.id,
      name: e.name,
      position: [round(p.x), round(p.y), round(p.z)],
      rotation: [round(q.x), round(q.y), round(q.z), round(q.w)],
      visible: e.object3d.visible,
      components,
      ...(animation && { animation }),
    };
  });
}
