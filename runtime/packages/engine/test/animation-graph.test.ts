// Animation graphs: blend spaces, transitions, triggers, exit times, any-state, events, the
// Animator's `graph` field, motion parameters, hot reload and determinism.
import { AnimationClip, Object3D, VectorKeyframeTrack } from 'three';
import { afterEach, describe, expect, it } from 'vitest';
import { AnimationService, type AnimationEvent } from '../src/animation/animation';
import { defineAnimationGraph, removeAnimationGraph, setGraphMotionProvider } from '../src/animation/graph';
import { World } from '../src/ecs/world';
import { describeEntities } from '../src/game/describe';
import { Animator } from '../src/schema';

const DT = 1 / 60;

/** A clip that holds Box at `at` (x, y) for `seconds`, or moves it from `at` to `to`. */
function pose(name: string, seconds: number, at: [number, number], to: [number, number] = at): AnimationClip {
  return new AnimationClip(name, seconds, [new VectorKeyframeTrack('Box.position', [0, seconds], [at[0], at[1], 0, to[0], to[1], 0])]);
}

const CLIPS = () => [
  pose('Idle', 1, [0, 0]),
  pose('Walk', 1, [2, 0]),
  pose('Run', 0.5, [6, 0]),
  pose('Jump', 1, [0, 0], [0, 1]), // y rises 0 → 1 over 1 s
  pose('Land', 0.5, [0, 1], [0, 0]),
  pose('Fall', 1, [0, -1]),
];

/** Every graph this file defines is removed again (the registry is global). */
const defined: string[] = [];
afterEach(() => {
  for (const name of defined.splice(0)) removeAnimationGraph(name);
  setGraphMotionProvider(() => null);
});

function hero(name = 'Hero', extra: { blend?: number; landSpeed?: number } = {}) {
  defined.push(name);
  return defineAnimationGraph(name, {
    parameters: { speed: 'float', grounded: { type: 'bool', default: true }, jump: 'trigger', rate: { type: 'float', default: 1 } },
    initial: 'Move',
    blend: extra.blend ?? 0,
    states: {
      Move: { blend: { by: 'speed', clips: [[0, 'Idle'], [2, 'Walk'], [6, 'Run']] } },
      Jump: { clip: 'Jump', loop: false, speed: 'rate', events: { apex: 0.5 } },
      Fall: { clip: 'Fall' },
      Land: { clip: 'Land', loop: false, next: 'Move', speed: extra.landSpeed ?? 1 },
    },
    transitions: [
      { from: 'Move', to: 'Jump', when: 'jump' },
      { from: '*', to: 'Fall', when: ['!grounded', (p) => p.speed < 100] },
      { from: 'Fall', to: 'Land', when: 'grounded' },
      { from: 'Jump', to: 'Move', exitTime: 1 },
    ],
  });
}

function setup(animator?: Record<string, unknown>) {
  const world = new World();
  const box = new Object3D();
  box.name = 'Box';
  const e = world.spawn({ id: 'gs_box', name: 'Box', object3d: box, components: animator ? { Animator: animator } : {} });
  const warnings: string[] = [];
  const animation = new AnimationService((m) => warnings.push(m));
  animation.reset(world, CLIPS());
  const events: AnimationEvent[] = [];
  const step = (frames = 1) => {
    for (let i = 0; i < frames; i++) {
      animation.update(DT);
      events.push(...animation.events);
    }
  };
  return { world, e, box, animation, warnings, events, step };
}

describe('defineAnimationGraph', () => {
  it('rejects mistakes, naming them', () => {
    const bad = (def: object) => () => defineAnimationGraph('Bad', def as never);
    expect(bad({ states: {} })).toThrow(/at least one state/);
    expect(bad({ states: { A: {} } })).toThrow(/state "A" needs either `clip` or `blend`/);
    expect(bad({ states: { A: { clip: 'Idle' } }, initial: 'B' })).toThrow(/initial: no state "B" \(states: A\)/);
    expect(bad({ states: { A: { clip: 'Idle', next: 'Nope' } } })).toThrow(/no state "Nope"/);
    expect(bad({ parameters: { on: 'bool' }, states: { A: { blend: { by: 'on', clips: [[0, 'Idle']] } } } })).toThrow(/"on" is not a float or int parameter/);
    expect(bad({ parameters: { v: 'float' }, states: { A: { blend: { by: 'v', clips: [[1, 'Idle'], [0, 'Walk']] } } } })).toThrow(/thresholds must increase/);
    expect(bad({ states: { A: { clip: 'Idle' }, B: { clip: 'Walk' } }, transitions: [{ from: 'A', to: 'B', when: 'go' }] })).toThrow(/no parameter "go"/);
    expect(bad({ states: { A: { clip: 'Idle' }, B: { clip: 'Walk' } }, transitions: [{ from: 'A', to: 'B' }] })).toThrow(/needs `when` or `exitTime`/);
    expect(bad({ parameters: { s: { type: 'bool', from: 'speed' } }, states: { A: { clip: 'Idle' } } })).toThrow(/"speed" needs a float parameter/);
  });

  it('types states and parameters (typos fail to compile)', () => {
    const typed = () =>
      defineAnimationGraph('Typed', {
        parameters: { speed: 'float', on: 'bool', go: 'trigger' },
        states: { A: { clip: 'Idle' }, B: { blend: { by: 'speed', clips: [[0, 'Walk']] } } },
        transitions: [
          { from: 'A', to: 'B', when: ['go', '!on', (p) => p.speed > 1] },
          // @ts-expect-error: no state "C"
          { from: 'A', to: 'C', when: 'on' },
          // @ts-expect-error: no parameter "speeed"
          { from: 'B', to: 'A', when: (p) => p.speeed > 1 },
        ],
      });
    expect(typed).toThrow(/no state "C"/);
    const graph = defineAnimationGraph('Typed', {
      parameters: { speed: 'float', go: 'trigger' },
      states: { A: { clip: 'Idle' } },
    });
    defined.push('Typed');
    const { e, animation } = setup();
    const g = animation.graph(e, graph)!;
    g.set('speed', 2).trigger('go');
    // @ts-expect-error: "speed" is not a trigger
    g.trigger('speed');
    // @ts-expect-error: speed is a number
    g.set('speed', true);
    const state: 'A' = g.state;
    expect(state).toBe('A');
  });
});

describe('animation graphs', () => {
  it('blends a 1D blend space by its parameter', () => {
    const graph = hero();
    const { e, box, animation, step } = setup();
    const g = animation.graph(e, graph)!;
    expect(g.state).toBe('Move');
    step();
    expect(box.position.x).toBeCloseTo(0, 5); // Idle
    g.set('speed', 1);
    step();
    expect(box.position.x).toBeCloseTo(1, 5); // Idle and Walk, half each
    g.set('speed', 4);
    step();
    expect(box.position.x).toBeCloseTo(4, 5); // Walk and Run, half each
    g.set('speed', 10);
    step();
    expect(box.position.x).toBeCloseTo(6, 5); // Run, past the last threshold
    expect(animation.current(e)).toBe('Run');
    expect(animation.isPlaying(e, 'Run')).toBe(true);
    expect(animation.isPlaying(e, 'Idle')).toBe(false);
  });

  it('fires a trigger once, uses it up, and drops a trigger nothing takes', () => {
    const { e, box, animation, events, step } = setup();
    const g = animation.graph(e, hero())!;
    g.trigger('jump');
    expect(g.get('jump')).toBe(true);
    step();
    expect(g.state).toBe('Jump');
    expect(g.get('jump')).toBe(false); // Used up.
    // (Move's 'enter' came with graph(), before this frame's events.)
    expect(events.filter((ev) => ev.type === 'exit' || ev.type === 'enter').map((ev) => `${ev.type}:${ev.state}`)).toEqual(['exit:Move', 'enter:Jump']);
    step(30);
    expect(box.position.y).toBeCloseTo(0.5, 2);
    expect(events.some((ev) => ev.type === 'event' && ev.name === 'apex' && ev.state === 'Jump')).toBe(true);
    // Jump has no transition on `jump`: the trigger waits one update, then clears.
    g.trigger('jump');
    step();
    expect(g.get('jump')).toBe(false);
    expect(g.state).toBe('Jump');
  });

  it('keeps a trigger for its `hold` time', () => {
    defined.push('Combo');
    const graph = defineAnimationGraph('Combo', {
      parameters: { attack: { type: 'trigger', hold: 0.2 } },
      states: { A: { clip: 'Jump', loop: false }, B: { clip: 'Land', loop: false } },
      transitions: [{ from: 'A', to: 'B', when: 'attack', exitTime: 0.5 }],
    });
    const { e, animation, step } = setup();
    const g = animation.graph(e, graph)!;
    step(21); // 0.35 s into A: too early for the exit time
    g.trigger('attack');
    step(6); // 0.45 s: still waiting
    expect(g.state).toBe('A');
    expect(g.get('attack')).toBe(true);
    step(4); // 0.5 s: the exit time passes while the trigger is held
    expect(g.state).toBe('B');

    g.go('A');
    step(6);
    g.trigger('attack');
    step(14); // Held 0.2 s, then dropped before the exit time
    expect(g.get('attack')).toBe(false);
    step(20);
    expect(g.state).toBe('A');
  });

  it('plays a state once, reports "finished", and goes on by exit time and `next`', () => {
    const { e, box, animation, events, step } = setup();
    const g = animation.graph(e, hero('Hero', { landSpeed: 2 }))!;
    g.trigger('jump');
    step(61); // Jump lasts 1 s; the transition with exitTime 1 then takes it back to Move.
    expect(events.some((ev) => ev.type === 'finished' && ev.state === 'Jump' && ev.clip === 'Jump')).toBe(true);
    expect(g.state).toBe('Move');

    g.go('Land');
    step();
    expect(g.state).toBe('Land');
    expect(animation.isPlaying(e, 'Land')).toBe(true);
    step(14); // Land: 0.5 s at speed 2
    expect(g.state).toBe('Move');
    expect(box.position.y).toBeCloseTo(0, 5);
  });

  it('takes any-state transitions from every state but its own', () => {
    const { e, animation, events, step } = setup();
    const g = animation.graph(e, hero())!;
    g.trigger('jump');
    step();
    expect(g.state).toBe('Jump');
    g.set('grounded', false);
    step();
    expect(g.state).toBe('Fall');
    step(10);
    expect(events.filter((ev) => ev.type === 'enter' && ev.state === 'Fall')).toHaveLength(1); // Not re-entered.
    g.set('grounded', true);
    step();
    expect(g.state).toBe('Land');
  });

  it('crossfades over the blend time', () => {
    const { e, box, animation, step } = setup();
    const g = animation.graph(e, hero('Hero', { blend: 0.5 }))!;
    g.set('grounded', false);
    step(); // Fall starts, at weight 0.
    expect(box.position.y).toBeCloseTo(0, 5);
    step(15); // 0.25 s: half Move (y 0), half Fall (y -1)
    expect(box.position.y).toBeCloseTo(-0.5, 5);
    step(15);
    expect(box.position.y).toBeCloseTo(-1, 5);
  });

  it('plays at a speed parameter and keeps blended clips in step', () => {
    const { e, box, animation, step } = setup();
    const g = animation.graph(e, hero())!;
    g.set('rate', 2).trigger('jump');
    step(16); // 15 frames of Jump at double speed: 0.5 of the clip
    expect(box.position.y).toBeCloseTo(0.5, 5);
    expect(g.progress).toBeCloseTo(0.5, 5);

    g.go('Move', 0).set('speed', 4); // Walk (1 s) and Run (0.5 s) half each: one cycle lasts 0.75 s.
    step(45);
    expect(g.progress).toBeCloseTo(1, 5);
  });

  it('runs from the Animator `graph` field and mirrors its state into Animator.state', () => {
    hero();
    const { e, box, animation, world, step } = setup({ graph: 'Hero' });
    step();
    const animator = e.get(Animator);
    expect(animator.state).toBe('Move');
    const g = animation.graph(e)!;
    expect(g.graph).toBe('Hero');
    g.set('grounded', false);
    step();
    expect(animator.state).toBe('Fall');
    expect(box.position.y).toBeCloseTo(-1, 5);

    const [snapshot] = describeEntities(world, { ids: ['gs_box'] });
    expect(snapshot!.animation).toMatchObject({ graph: 'Hero', state: 'Fall', clip: 'Fall', playing: true });
    expect(snapshot!.animation!.params).toEqual({ speed: 0, grounded: false, jump: false, rate: 1 });
    expect(snapshot!.components.Animator).toMatchObject({ graph: 'Hero', state: 'Fall' });

    // Clearing the field hands the entity back to `clip`.
    Object.assign(animator, { graph: '', clip: 'Walk', fade: 0 });
    step();
    expect(animation.graph(e)).toBeNull();
    expect(animator.state).toBe('');
    expect(animation.current(e)).toBe('Walk');
    expect(box.position.x).toBeCloseTo(2, 5);
  });

  it('waits for a graph the Animator names until it is defined, and warns once', () => {
    const { e, animation, warnings, step } = setup({ graph: 'Late' });
    step(3);
    expect(warnings.filter((w) => w.includes('No animation graph "Late"'))).toHaveLength(1);
    expect(animation.graph(e)).toBeNull();
    defined.push('Late');
    defineAnimationGraph('Late', { states: { Only: { clip: 'Walk' } } });
    step();
    expect(e.get(Animator).state).toBe('Only');
  });

  it('gives the entity back to play() and takes it again with graph()', () => {
    const graph = hero();
    const { e, box, animation, step } = setup();
    animation.graph(e, graph)!.set('speed', 2);
    step();
    expect(box.position.x).toBeCloseTo(2, 5);
    expect(animation.play(e, 'Fall', { fade: 0 })).toBe(true);
    expect(animation.graph(e)).toBeNull();
    step();
    expect(box.position.y).toBeCloseTo(-1, 5);
    expect(box.position.x).toBeCloseTo(0, 5);
    const again = animation.graph(e, 'Hero')!;
    expect(again.state).toBe('Move');
    step();
    expect(box.position.y).toBeCloseTo(0, 5);
  });

  it('picks up a redefined graph (hot reload), keeping its state and parameters', () => {
    const { e, box, animation, step } = setup({ graph: 'Hero' });
    hero();
    step();
    const g = animation.graph(e)!;
    g.set('speed', 2);
    step();
    expect(box.position.x).toBeCloseTo(2, 5);
    // The file is saved with Walk swapped for Run in the blend, and a new parameter.
    defineAnimationGraph('Hero', {
      parameters: { speed: 'float', crouch: 'bool' },
      states: { Move: { blend: { by: 'speed', clips: [[0, 'Idle'], [2, 'Run']] } }, Fall: { clip: 'Fall' } },
    });
    step();
    expect(g.state).toBe('Move');
    expect(g.params).toEqual({ speed: 2, crouch: false });
    expect(box.position.x).toBeCloseTo(6, 5);
  });

  it('fills `from` parameters from the movement and the character controller', () => {
    defined.push('Mover');
    const graph = defineAnimationGraph('Mover', {
      parameters: {
        speed: { type: 'float', from: 'speed' },
        vy: { type: 'float', from: 'verticalSpeed' },
        grounded: { type: 'bool', from: 'grounded' },
        air: { type: 'float', from: 'airTime' },
      },
      states: { Move: { blend: { by: 'speed', clips: [[0, 'Idle'], [2, 'Walk']] } }, Air: { clip: 'Fall' } },
      transitions: [{ from: 'Move', to: 'Air', when: (p) => p.air > 0.1 }],
    });
    const motion = { grounded: true, verticalVelocity: 0 };
    setGraphMotionProvider(() => motion);
    const { e, box, animation, step } = setup();
    const g = animation.graph(e, graph)!;
    for (let i = 0; i < 60; i++) {
      box.position.x += 2 * DT; // 2 m/s
      step();
    }
    expect(g.params.speed).toBeCloseTo(2, 3);
    expect(g.params.grounded).toBe(true);
    motion.grounded = false;
    motion.verticalVelocity = 3;
    step(7);
    expect(g.params).toMatchObject({ grounded: false, vy: 3 });
    expect(g.params.air).toBeCloseTo(7 * DT, 5);
    expect(g.state).toBe('Air');
  });

  it('repeats exactly on game time', () => {
    const run = () => {
      const { e, box, animation, events, step } = setup();
      const g = animation.graph(e, hero('Hero', { blend: 0.3 }))!;
      const trace: string[] = [];
      for (let frame = 0; frame < 240; frame++) {
        g.set('speed', (frame % 90) / 15);
        if (frame === 50) g.trigger('jump');
        g.set('grounded', frame < 130 || frame > 170);
        step();
        trace.push(`${g.state} ${box.position.x.toFixed(6)} ${box.position.y.toFixed(6)}`);
      }
      removeAnimationGraph('Hero');
      return { trace, events: events.map((ev) => `${ev.type}:${ev.state}:${ev.name ?? ''}`) };
    };
    const a = run();
    const b = run();
    expect(a.trace).toEqual(b.trace);
    expect(a.events).toEqual(b.events);
    expect(new Set(a.trace.map((t) => t.split(' ')[0]))).toEqual(new Set(['Move', 'Jump', 'Fall', 'Land']));
  });
});
