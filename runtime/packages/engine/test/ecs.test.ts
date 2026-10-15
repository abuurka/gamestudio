import { Group, Mesh, Object3D } from 'three';
import { describe, expect, it, vi } from 'vitest';
import type { SystemContext } from '../src/context';
import { Scheduler } from '../src/ecs/scheduler';
import { defineSystem, listSystems, removeSystem } from '../src/ecs/system';
import { World } from '../src/ecs/world';
import { FixedStepper } from '../src/game/stepper';
import { defineComponent, field } from '../src/schema';

const Health = defineComponent('TestHealth', { fields: { hp: field.int(100) } });
const Speed = defineComponent('TestSpeed', { fields: { v: field.float(2), dir: field.vec3([0, 0, 1]) } });

function ctxFor(world: World): SystemContext {
  return { dt: 1 / 60, time: 0, frame: 0, phase: 'update', world } as unknown as SystemContext;
}

describe('World', () => {
  it('spawns entities with default-filled component data', () => {
    const world = new World();
    const e = world.spawn({ name: 'Hero', components: { TestHealth: { hp: 5 } } });
    e.add(Speed, { v: 7 });
    expect(e.get(Health)).toEqual({ hp: 5 });
    expect(e.get(Speed)).toEqual({ v: 7, dir: [0, 0, 1] });
    expect(e.has(Health)).toBe(true);
    expect(e.has('Nope')).toBe(false);
    expect(e.tryGet('Nope')).toBeUndefined();
    expect(() => e.get('Nope')).toThrow(/has no component Nope/);
    expect(e.components()).toEqual(['TestHealth', 'TestSpeed']);
    expect(world.get(e.id)).toBe(e);
    expect(world.findByName('Hero')).toBe(e);
    expect(e.object3d.parent).toBe(world.root);
    expect(e.id).toMatch(/^rt_\d+$/);
  });

  it('caches queries and invalidates them on add and remove', () => {
    const world = new World();
    const a = world.spawn({ components: [[Health], [Speed]] });
    const b = world.spawn({ components: [[Health]] });
    const q1 = world.query(Health, Speed);
    expect(q1).toEqual([a]);
    expect(world.query(Speed, Health)).toBe(q1); // Same cached array, order-insensitive key.
    expect(world.query(Health)).toEqual([a, b]);

    b.add(Speed);
    const q2 = world.query(Health, Speed);
    expect(q2).not.toBe(q1);
    expect(q2).toEqual([a, b]);
    expect(q1).toEqual([a]); // Old arrays are never mutated (safe to iterate while changing).

    a.remove(Health);
    expect(world.query(Health, Speed)).toEqual([b]);
    expect(world.query(Health)).toEqual([b]);
    expect(world.query()).toEqual([a, b]);
  });

  it('replaces data when adding an existing component, and reports changes', () => {
    const world = new World();
    const events: string[] = [];
    world.onChange((ev) => events.push(ev.type === 'add' ? `add:${ev.component}:${ev.replaced}` : ev.type));
    const e = world.spawn();
    e.add(Health, { hp: 1 });
    const q = world.query(Health);
    e.add(Health, { hp: 2 });
    expect(world.query(Health)).toBe(q); // Replacing doesn't change membership.
    expect(e.get(Health).hp).toBe(2);
    e.destroy();
    expect(events).toEqual(['spawn', 'add:TestHealth:false', 'add:TestHealth:true', 'remove', 'destroy']);
  });

  it('destroys child entities and detaches objects', () => {
    const world = new World();
    const parent = world.spawn({ name: 'Crate', components: [[Health]] });
    const child = world.spawn({ name: 'Lamp', parent });
    const childMesh = new Mesh();
    child.object3d.add(childMesh);
    expect(world.entityOf(childMesh)).toBe(child);
    parent.destroy();
    expect(parent.alive).toBe(false);
    expect(child.alive).toBe(false);
    expect(world.size).toBe(0);
    expect(world.query(Health)).toEqual([]);
    expect(parent.object3d.parent).toBeNull();
  });

  it('destroys authoring children that live elsewhere in the scene graph (physics bodies)', () => {
    const world = new World();
    const parent = world.spawn({ name: 'Cart' });
    const wheel = world.spawn({ name: 'Wheel', parent });
    const unrelated = world.spawn({ name: 'Rock' });
    world.root.attach(wheel.object3d); // Dynamic bodies are moved to the root.
    expect(wheel.parentId).toBe(parent.id);
    parent.destroy();
    expect(wheel.alive).toBe(false);
    expect(unrelated.alive).toBe(true);
    expect(world.all()).toEqual([unrelated]);
    // Ids handed out ahead of time (prefab instancing) are never reused.
    const reserved = world._nextId();
    expect(world.spawn().id).not.toBe(reserved);
  });

  it('lists descendants recursively, parents first, and filters them by components', () => {
    const world = new World();
    const chunk = world.spawn({ name: 'Chunk' });
    const floor = world.spawn({ name: 'Floor', parent: chunk });
    const socketA = world.spawn({ name: 'SocketA', parent: floor, components: [[Health]] });
    const socketB = world.spawn({ name: 'SocketB', parent: chunk, components: [[Health], [Speed]] });
    const moved = world.spawn({ name: 'Debris', parent: socketB, components: [[Speed]] });
    world.root.attach(moved.object3d); // Dynamic bodies are moved to the root.
    const loose = new Object3D(); // An entity whose object was attached under the chunk's object.
    floor.object3d.add(loose);
    const attached = world.spawn({ name: 'Attached', object3d: loose });
    world.spawn({ name: 'Elsewhere', components: [[Health]] });

    expect(world.descendants(chunk).map((e) => e.name)).toEqual(['Floor', 'SocketA', 'Attached', 'SocketB', 'Debris']);
    expect(world.descendants(chunk, Health)).toEqual([socketA, socketB]);
    expect(world.descendants(chunk, 'TestHealth', Speed)).toEqual([socketB]);
    expect(world.descendants(socketB, Speed)).toEqual([moved]);
    expect(world.descendants(attached)).toEqual([]);
  });

  it('adopts an existing object without moving it, and rejects duplicate ids', () => {
    const world = new World();
    const container = new Group();
    const object = new Object3D();
    container.add(object);
    const e = world.spawn({ id: 'gs_000000000001', object3d: object });
    expect(object.parent).toBe(container);
    expect(e.name).toBe('gs_000000000001');
    expect(() => world.spawn({ id: 'gs_000000000001' })).toThrow(/already exists/);
  });

  it('backfills new fields when a component is hot-reloaded', () => {
    const world = new World();
    defineComponent('TestHot', { fields: { a: field.int(1) } });
    const e = world.spawn({ components: { TestHot: { a: 5 } } });
    defineComponent('TestHot', { fields: { a: field.int(1), b: field.bool(true) } });
    expect(e.get('TestHot')).toEqual({ a: 5, b: true });
  });
});

describe('systems', () => {
  it('runs systems by phase with their query results', () => {
    const world = new World();
    const e = world.spawn({ components: [[Health]] });
    const seen: string[] = [];
    defineSystem({ name: 'TestFixed', phase: 'fixed', query: [Health], update: (_c, es) => seen.push(`fixed:${es.length}`) });
    defineSystem({ name: 'TestLate', phase: 'late', update: (_c, es) => seen.push(`late:${es.length}`) });
    defineSystem({ name: 'TestUpdate', query: [Health], update: (_c, es) => seen.push(`update:${es[0] === e}`) });
    const scheduler = new Scheduler(() => {});
    const ctx = ctxFor(world);
    scheduler.run('fixed', ctx);
    scheduler.run('update', ctx);
    scheduler.run('late', ctx);
    expect(seen).toEqual(['fixed:1', 'update:true', 'late:0']);
    for (const name of ['TestFixed', 'TestLate', 'TestUpdate']) removeSystem(name);
  });

  it('replaces a system with the same name in place (HMR)', () => {
    const calls: string[] = [];
    defineSystem({ name: 'TestA', update: () => calls.push('A1') });
    defineSystem({ name: 'TestB', update: () => calls.push('B') });
    defineSystem({ name: 'TestA', update: () => calls.push('A2') });
    const names = listSystems().map((s) => s.name);
    expect(names.filter((n) => n === 'TestA')).toHaveLength(1);
    expect(names.indexOf('TestA')).toBeLessThan(names.indexOf('TestB')); // Keeps its run order.
    new Scheduler(() => {}).run('update', ctxFor(new World()));
    expect(calls).toEqual(['A2', 'B']);
    removeSystem('TestA');
    removeSystem('TestB');
  });

  it('calls start once per definition and tracks onAdd/onRemove membership', () => {
    const world = new World();
    const log: string[] = [];
    const def = {
      name: 'TestHooks',
      query: [Health],
      start: () => log.push('start'),
      onAdd: (_c: SystemContext, e: { name: string }) => log.push(`add:${e.name}`),
      onRemove: (_c: SystemContext, e: { name: string }) => log.push(`remove:${e.name}`),
    };
    defineSystem(def);
    const scheduler = new Scheduler(() => {});
    const ctx = ctxFor(world);
    const a = world.spawn({ name: 'a', components: [[Health]] });
    scheduler.prepare(ctx);
    scheduler.prepare(ctx);
    world.spawn({ name: 'b', components: [[Health]] });
    a.remove(Health);
    scheduler.prepare(ctx);
    expect(log).toEqual(['start', 'add:a', 'remove:a', 'add:b']);

    defineSystem({ ...def }); // Hot reload: fresh state, hooks run again.
    scheduler.prepare(ctx);
    expect(log.slice(4)).toEqual(['start', 'add:b']);
    scheduler.reset(); // Scene reset.
    scheduler.prepare(ctx);
    expect(log.slice(6)).toEqual(['start', 'add:b']);
    removeSystem('TestHooks');
  });

  it('isolates errors per system', () => {
    const onError = vi.fn();
    const ran: string[] = [];
    defineSystem({ name: 'TestBroken', update: () => { throw new Error('boom'); } });
    defineSystem({ name: 'TestFine', update: () => ran.push('fine') });
    new Scheduler(onError).run('update', ctxFor(new World()));
    expect(ran).toEqual(['fine']);
    expect(onError).toHaveBeenCalledTimes(1);
    expect(onError.mock.calls[0]![0].name).toBe('TestBroken');
    expect(onError.mock.calls[0]![2]).toBe('update');
    removeSystem('TestBroken');
    removeSystem('TestFine');
  });

  it('rejects unknown phases', () => {
    expect(() => defineSystem({ name: 'TestPhase', phase: 'never' as 'fixed' })).toThrow(/unknown phase/);
  });
});

describe('FixedStepper', () => {
  it('turns frame times into whole 60 Hz steps', () => {
    const stepper = new FixedStepper(1 / 60, 5);
    expect(stepper.advance(1 / 60)).toBe(1);
    expect(stepper.advance(1 / 120)).toBe(0);
    expect(stepper.advance(1 / 120)).toBe(1);
    expect(stepper.advance(1 / 30)).toBe(2);
    expect(stepper.advance(1)).toBe(5); // Capped; the backlog is dropped.
    expect(stepper.advance(0)).toBe(0);
  });
});
